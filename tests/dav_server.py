"""A synthetic CardDAV and CalDAV server, for the contacts and calendars tests."""

import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.stubs import serve
from towpath.adapters import dav

USER, PASSWORD = "reader@example.com", "dav-read-only"
CARD = ("BEGIN:VCARD\r\nVERSION:3.0\r\nUID:c1\r\nFN:Sam Rivera\r\nEMAIL;TYPE=home:Sam@Example.org\r\n"
        "item1.EMAIL:sam.work@example.net\r\nBDAY:1985-04-12\r\nORG:Example Lock;Ops\r\nEND:VCARD\r\n")
EVENTS = {
    "trip.ics": ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:e1\r\nSUMMARY:Flight to Example Junction\r\n"
                 "DTSTART:20090514T081500Z\r\nDTEND:20090514T101500Z\r\nLOCATION:Example Airport\r\n"
                 "BEGIN:VALARM\r\nACTION:DISPLAY\r\nSUMMARY:not this\r\nEND:VALARM\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"),
    "gym.ics": ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:e2\r\nSUMMARY:Gym\r\nDTSTART;TZID=America/Chicago:"
                "20090101T070000\r\nRRULE:FREQ=WEEKLY\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"),
    "future.ics": ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:e3\r\nSUMMARY:Far future\r\nDTSTART;VALUE=DATE:"
                   "29990101\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"),
}


class Server:
    def __init__(self):
        self.items = {"/dav/addressbooks/user/reader@example.com/Default/sam.vcf": ("e-1", CARD)}
        for name, text in EVENTS.items():
            self.items[f"/dav/calendars/user/reader@example.com/Personal/{name}"] = ("e-1", text)
        self.methods, self.multigets = [], []
        self.calendar_home = None
        self.principal = "/principals/42/"
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, xml: str):
                data = xml.encode()
                self.send_response(207)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_PROPFIND(self):  # noqa: N802
                outer.methods.append("PROPFIND")
                if self.headers.get("Authorization") != dav.DavClient(USER, PASSWORD)._auth:
                    self.send_response(401)
                    self.end_headers()
                    return
                path = self.path
                resp = []
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
                base = f"http://127.0.0.1:{outer.http.server_address[1]}"
                if path == "/" and "current-user-principal" in body:  # discovery, as iCloud does it
                    resp.append("<d:response><d:href>/</d:href><d:propstat><d:prop><d:current-user-principal>"
                                f"<d:href>{outer.principal}</d:href></d:current-user-principal></d:prop></d:propstat>"
                                "</d:response>")
                elif path == "/principals/42/":
                    home = (f"<card:addressbook-home-set><d:href>{base}/dav/addressbooks/user/{USER}/</d:href>"
                            "</card:addressbook-home-set>") if "addressbook-home-set" in body else \
                        ("<cal:calendar-home-set><d:href>"
                         f"{outer.calendar_home or base + '/dav/calendars/user/' + USER + '/'}"
                         "</d:href></cal:calendar-home-set>")
                    resp.append(f"<d:response><d:href>{path}</d:href><d:propstat><d:prop>{home}</d:prop></d:propstat>"
                                "</d:response>")
                elif path.endswith("/user/reader@example.com/"):
                    kind = "card:addressbook" if "addressbooks" in path else "cal:calendar"
                    child = "Default/" if "addressbooks" in path else "Personal/"
                    resp.append(f"<d:response><d:href>{path}</d:href><d:propstat><d:prop><d:resourcetype>"
                                f"<d:collection/></d:resourcetype></d:prop></d:propstat></d:response>")
                    resp.append(f"<d:response><d:href>{path}{child}</d:href><d:propstat><d:prop><d:resourcetype>"
                                f"<d:collection/><{kind}/></d:resourcetype><d:displayname>{child[:-1]}</d:displayname>"
                                f"</d:prop></d:propstat></d:response>")
                else:
                    for href, (etag, _) in outer.items.items():
                        if href.startswith(path):
                            resp.append(f"<d:response><d:href>{href}</d:href><d:propstat><d:prop><d:getetag>{etag}"
                                        f"</d:getetag></d:prop></d:propstat></d:response>")
                self._reply('<d:multistatus xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav" '
                            f'xmlns:cal="urn:ietf:params:xml:ns:caldav">{"".join(resp)}</d:multistatus>')

            def do_REPORT(self):  # noqa: N802
                outer.methods.append("REPORT")
                body = self.rfile.read(int(self.headers["Content-Length"])).decode()
                hrefs = re.findall(r"<d:href>([^<]+)</d:href>", body)
                outer.multigets.append(hrefs)
                tag = "card:address-data" if "addressbook" in body else "cal:calendar-data"
                resp = "".join(f"<d:response><d:href>{h}</d:href><d:propstat><d:prop><d:getetag>{outer.items[h][0]}"
                               f"</d:getetag><{tag}>{outer.items[h][1]}</{tag}></d:prop></d:propstat></d:response>"
                               for h in hrefs if h in outer.items)
                self._reply('<d:multistatus xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav" '
                            f'xmlns:cal="urn:ietf:params:xml:ns:caldav">{resp}</d:multistatus>')

            def do_PUT(self):  # noqa: N802
                outer.methods.append("PUT")
                self.send_response(500)
                self.end_headers()

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        serve(self.http)
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        self.urls = {"contacts": f"{base}/dav/addressbooks/user/{USER}/",
                     "calendars": f"{base}/dav/calendars/user/{USER}/"}
