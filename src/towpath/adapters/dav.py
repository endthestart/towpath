"""Read-only contacts (CardDAV) and calendars (CalDAV): Fastmail's known addresses, or found the standard way
(the account's principal, then its address-book and calendar homes) for iCloud and other servers.

Towpath sends only PROPFIND and REPORT, the two read methods these protocols need: PROPFIND lists the address books
and calendars and each item's ETag; REPORT (``addressbook-multiget``, ``calendar-multiget``) reads the items that
changed. Anything else is refused before it is sent. HTTPS only, except to a loopback address for tests.

vCards and iCalendar events are parsed into the few fields Towpath uses: names, addresses, numbers, birthdays and
organisations; event summaries, times, places, organisers, attendees and whether an event repeats.
"""

import base64
import ipaddress
import re
import urllib.error
import urllib.parse
import urllib.request
from xml.etree import ElementTree as ET

from towpath.adapters.errors import AuthStop, ServerStop

NS = {"d": "DAV:", "card": "urn:ietf:params:xml:ns:carddav", "cal": "urn:ietf:params:xml:ns:caldav",
      "cs": "http://calendarserver.org/ns/"}
METHODS = {"PROPFIND", "REPORT"}
FASTMAIL = {"contacts": "https://carddav.fastmail.com/dav/addressbooks/user/{user}/",
            "calendars": "https://caldav.fastmail.com/dav/calendars/user/{user}/"}
ICLOUD = {"contacts": "https://contacts.icloud.com/", "calendars": "https://caldav.icloud.com/"}
TIMEOUT = 60
MULTIGET = 100

LIST_BODY = b"""<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:cs="http://calendarserver.org/ns/"><d:prop><d:resourcetype/><d:displayname/>
<cs:getctag/><d:getetag/></d:prop></d:propfind>"""


PRINCIPAL_BODY = b"""<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>"""
HOME_BODY = {"contacts": b"""<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav"><d:prop><card:addressbook-home-set/></d:prop>
</d:propfind>""", "calendars": b"""<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav"><d:prop><cal:calendar-home-set/></d:prop>
</d:propfind>"""}


def same_service(base: str, other: str) -> bool:
    """Whether a home the server pointed to is on the same service as the address Towpath asked (iCloud's homes are
    on numbered hosts under icloud.com): same scheme, and the same last two labels of the host name."""
    def tail(host):
        return ".".join((host or "").split(".")[-2:])

    a, b = urllib.parse.urlsplit(base), urllib.parse.urlsplit(other)
    return a.scheme == b.scheme and (a.hostname == b.hostname or tail(a.hostname) == tail(b.hostname))


class DavViolation(RuntimeError):
    """Towpath was about to send something other than a read."""


class DavClient:
    """Reads one service: the first address it is asked about pins the service, and the password is never sent
    anywhere else, whatever the server's answers point to."""

    def __init__(self, username: str, password: str, opener=None):
        self._auth = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        self._opener = opener or urllib.request.build_opener()
        self._service: str | None = None

    def request(self, method: str, url: str, body: bytes, depth: str = "1") -> ET.Element:
        if method not in METHODS:
            raise DavViolation(f"{method} is not a read")
        if self._service is None:
            self._service = url
        elif not same_service(self._service, url):
            raise DavViolation("the server pointed somewhere else; Towpath only reads from the service you chose")
        parsed = urllib.parse.urlsplit(url)
        loopback = parsed.hostname in ("127.0.0.1", "localhost", "::1")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
            raise DavViolation("contacts and calendars are read over HTTPS only")
        req = urllib.request.Request(url, data=body, method=method, headers={
            "Authorization": self._auth, "Depth": depth, "Content-Type": "application/xml; charset=utf-8",
            "User-Agent": "Towpath (read-only)"})
        try:
            with self._opener.open(req, timeout=TIMEOUT) as response:
                return ET.fromstring(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise AuthStop("the server refused the contacts and calendars password") from None
            raise ServerStop(f"the server answered {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ServerStop(f"couldn't reach {parsed.hostname}") from None
        except ET.ParseError:
            raise ServerStop("the server's answer wasn't valid XML") from None

    def discover(self, base: str, kind: str) -> str:
        """The address-book or calendar home for this account: its principal, then the home set it names."""
        root = self.request("PROPFIND", base, PRINCIPAL_BODY, depth="0")
        principal = root.findtext(".//d:current-user-principal/d:href", namespaces=NS)
        if not principal:
            raise ServerStop("the server didn't say where this account's contacts and calendars are")
        principal = urllib.parse.urljoin(base, principal)
        if not same_service(base, principal):  # checked before the password is sent there
            raise DavViolation("the server pointed somewhere else; Towpath only reads from the service you chose")
        tag = "card:addressbook-home-set" if kind == "contacts" else "cal:calendar-home-set"
        home = self.request("PROPFIND", principal, HOME_BODY[kind], depth="0").findtext(f".//{tag}/d:href",
                                                                                          namespaces=NS)
        if not home:
            raise ServerStop(f"the server has no {kind} for this account")
        home = urllib.parse.urljoin(principal, home)
        if not (same_service(base, principal) and same_service(base, home)):
            raise DavViolation("the server pointed somewhere else; Towpath only reads from the service you chose")
        return home

    def collections(self, home: str, kind: str) -> list[dict]:
        """Address books (``contacts``) or calendars (``calendars``) under a home URL."""
        tag = "card:addressbook" if kind == "contacts" else "cal:calendar"
        out = []
        for resp in self.request("PROPFIND", home, LIST_BODY).findall("d:response", NS):
            if resp.find(f"d:propstat/d:prop/d:resourcetype/{tag}", NS) is None:
                continue
            href = resp.findtext("d:href", namespaces=NS)
            if not same_service(home, urllib.parse.urljoin(home, href)):
                continue  # a collection elsewhere is never read
            out.append({"href": urllib.parse.urljoin(home, href),
                        "name": resp.findtext("d:propstat/d:prop/d:displayname", namespaces=NS) or href,
                        "ctag": resp.findtext("d:propstat/d:prop/cs:getctag", namespaces=NS)})
        return out

    def etags(self, collection: str) -> dict[str, str]:
        """Each item's href and ETag in a collection."""
        out = {}
        for resp in self.request("PROPFIND", collection, LIST_BODY).findall("d:response", NS):
            href = urllib.parse.urljoin(collection, resp.findtext("d:href", namespaces=NS) or "")
            etag = resp.findtext("d:propstat/d:prop/d:getetag", namespaces=NS)
            if href.rstrip("/") != collection.rstrip("/") and etag:
                out[href] = etag
        return out

    def multiget(self, collection: str, hrefs: list[str], kind: str) -> dict[str, str]:
        """The vCard or iCalendar text of each href."""
        if kind == "contacts":
            head, data = "card:addressbook-multiget", "card:address-data"
        else:
            head, data = "cal:calendar-multiget", "cal:calendar-data"
        out = {}
        for start in range(0, len(hrefs), MULTIGET):
            paths = "".join(f"<d:href>{_xml(urllib.parse.urlsplit(h).path)}</d:href>"
                            for h in hrefs[start:start + MULTIGET])
            body = (f'<?xml version="1.0" encoding="utf-8"?><{head} xmlns:d="DAV:" '
                    f'xmlns:card="{NS["card"]}" xmlns:cal="{NS["cal"]}"><d:prop><d:getetag/><{data}/></d:prop>'
                    f"{paths}</{head}>").encode()
            for resp in self.request("REPORT", collection, body).findall("d:response", NS):
                href = urllib.parse.urljoin(collection, resp.findtext("d:href", namespaces=NS) or "")
                text = resp.findtext(f"d:propstat/d:prop/{data}", namespaces=NS)
                if text:
                    out[href] = text
        return out


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def is_loopback(host: str | None) -> bool:
    try:
        return host in ("localhost",) or ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


# -- vCard and iCalendar --------------------------------------------------------------------------------------------

def _lines(text: str) -> list[tuple[str, dict, str]]:
    """Unfolded content lines as (name, parameters, value)."""
    unfolded = re.sub(r"\r?\n[ \t]", "", text)
    out = []
    for line in unfolded.splitlines():
        if ":" not in line:
            continue
        head, _, value = line.partition(":")
        name, *params = head.split(";")
        name = name.split(".")[-1].upper()  # item1.EMAIL -> EMAIL
        found = {}
        for p in params:
            k, _, v = p.partition("=")
            found[k.upper()] = v
        out.append((name, found, value))
    return out


def _unescape(value: str) -> str:
    return value.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


APPLE_LABEL = re.compile(r"^_\$!<(.+)>!\$_$")  # Apple's built-in labels, e.g. _$!<Anniversary>!$_


def _date(value: str) -> str | None:
    m = re.match(r"(\d{4}|--)-?(\d{2})-?(\d{2})", value.strip())
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def card_dates(text: str) -> list[tuple[str, str]]:
    """(label, date) for each date a card records besides the birthday: ``ANNIVERSARY``, and Apple's grouped
    ``itemN.X-ABDATE`` with its ``itemN.X-ABLabel``."""
    unfolded = re.sub(r"\r?\n[ \t]", "", text)
    labels, dated, out = {}, [], []
    for line in unfolded.splitlines():
        head, sep, value = line.partition(":")
        if not sep:
            continue
        group, _, name = head.split(";")[0].rpartition(".")
        name = name.upper()
        if name == "X-ABLABEL" and group:
            raw = _unescape(value).strip()
            m = APPLE_LABEL.match(raw)
            labels[group] = (m.group(1) if m else raw)[:40] or "Date"
        elif name == "X-ABDATE":
            dated.append((group, _date(value)))
        elif name == "ANNIVERSARY":
            day = _date(value)
            if day:
                out.append(("Anniversary", day))
    out += [(labels.get(group, "Date"), day) for group, day in dated if day]
    return sorted(set(out))


def parse_vcard(text: str) -> dict:
    card = {"uid": None, "name": None, "emails": [], "phones": [], "birthday": None, "org": None,
            "dates": card_dates(text)}
    for name, _, value in _lines(text):
        if name == "UID":
            card["uid"] = value.strip()
        elif name == "FN":
            card["name"] = _unescape(value).strip() or card["name"]
        elif name == "N" and not card["name"]:
            parts = [_unescape(p).strip() for p in value.split(";")]
            card["name"] = " ".join(p for p in (parts[1:2] + parts[0:1]) if p) or None
        elif name == "EMAIL" and value.strip():
            card["emails"].append(value.strip().lower().removeprefix("mailto:"))
        elif name == "TEL" and value.strip():
            card["phones"].append(value.strip().removeprefix("tel:"))
        elif name == "BDAY" and value.strip():
            m = re.match(r"(\d{4}|--)-?(\d{2})-?(\d{2})", value.strip())
            card["birthday"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
        elif name == "ORG":
            card["org"] = _unescape(value.split(";")[0]).strip() or None
    return card


def _when(value: str, params: dict) -> tuple[str | None, bool, str | None]:
    """(ISO date or date-time, all-day, time zone) from a DTSTART or DTEND value."""
    m = re.match(r"(\d{4})(\d{2})(\d{2})(T(\d{2})(\d{2})(\d{2})(Z)?)?", value.strip())
    if not m:
        return None, False, None
    day = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    if not m.group(4) or params.get("VALUE") == "DATE":
        return day, True, None
    return f"{day}T{m.group(5)}:{m.group(6)}:{m.group(7)}" + ("Z" if m.group(8) else ""), False, (
        "UTC" if m.group(8) else params.get("TZID"))


def parse_events(text: str) -> list[dict]:
    """Each VEVENT in an iCalendar object (a repeating event once, marked as repeating)."""
    events, current, depth = [], None, 0
    for name, params, value in _lines(text):
        if name == "BEGIN" and value.upper() == "VEVENT":
            current, depth = {"uid": None, "summary": None, "start": None, "end": None, "all_day": False,
                              "tz": None, "location": None, "organizer": None, "attendees": [], "recurring": False,
                              "description": None, "recurrence_id": None}, 0
        elif name == "BEGIN" and current is not None:
            depth += 1  # a VALARM inside the event
        elif name == "END" and current is not None and depth:
            depth -= 1
        elif name == "END" and value.upper() == "VEVENT" and current is not None:
            events.append(current)
            current = None
        elif current is not None and not depth:
            if name == "UID":
                current["uid"] = value.strip()
            elif name == "SUMMARY":
                current["summary"] = _unescape(value).strip()
            elif name == "DTSTART":
                current["start"], current["all_day"], current["tz"] = _when(value, params)
            elif name == "DTEND":
                current["end"] = _when(value, params)[0]
            elif name == "LOCATION":
                current["location"] = _unescape(value).strip() or None
            elif name == "ORGANIZER":
                current["organizer"] = value.lower().removeprefix("mailto:").strip() or None
            elif name == "ATTENDEE":
                current["attendees"].append(value.lower().removeprefix("mailto:").strip())
            elif name in ("RRULE", "RDATE"):
                current["recurring"] = True
            elif name == "RECURRENCE-ID":
                current["recurrence_id"] = value.strip()
            elif name == "DESCRIPTION":
                current["description"] = _unescape(value).strip()[:2000] or None
    return events
