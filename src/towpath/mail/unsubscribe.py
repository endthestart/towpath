"""Unsubscribing: what a sender's List-Unsubscribe headers offer, and carrying an unsubscribe out.

RFC 8058 one-click: an HTTPS POST of ``List-Unsubscribe=One-Click`` to the header's https address, when the sender
also sends ``List-Unsubscribe-Post``. Otherwise a ``mailto:`` address gets a short email, sent over the account's SMTP
server with its write password. A header offering only a web page can't be done for the owner; the page shows the
link instead. Requests go only to public addresses over HTTPS, without following redirects.
"""

import http.client
import ipaddress
import re
import smtplib
import socket
import ssl
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from datetime import datetime, timezone

URLS = re.compile(r"<([^>]+)>")
SMTP = {"imap.fastmail.com": ("smtp.fastmail.com", 465), "imap.gmail.com": ("smtp.gmail.com", 465),
        "imap.mail.yahoo.com": ("smtp.mail.yahoo.com", 465)}
NO_SMTP = {"imap.mail.me.com"}  # iCloud sends only over STARTTLS, which Towpath doesn't use yet
TIMEOUT = 20


class UnsubscribeError(RuntimeError):
    pass


def options(list_unsubscribe: str | None, list_unsubscribe_post: str | None) -> dict:
    """What the headers offer: a one-click URL, a mailto address, and a web page to open by hand."""
    found = {"post": None, "mail": None, "page": None}
    for url in URLS.findall(list_unsubscribe or ""):
        url = url.strip()
        if url.lower().startswith("mailto:") and not found["mail"]:
            found["mail"] = url
        elif url.lower().startswith("https://"):
            if list_unsubscribe_post and "one-click" in list_unsubscribe_post.lower() and not found["post"]:
                found["post"] = url
            elif not found["page"]:
                found["page"] = url
        elif url.lower().startswith("http://") and not found["page"]:
            found["page"] = url
    return found


def best(found: dict, sender: str | None = None, list_id: str | None = None,
         host: str | None = None) -> tuple[str, str] | None:
    """The method Towpath can carry out, one-click first. An email goes only to an address in the domain of the
    sender (or of the List-Id), and never from an account Towpath can't send from, so it is offered only then."""
    if found.get("post"):
        return "post", found["post"]
    mailto = found.get("mail")
    if host in NO_SMTP:
        mailto = None
    if mailto and (sender is None or _domain(urllib.parse.unquote(urllib.parse.urlsplit(mailto).path))
                   in {_domain(sender), _domain((list_id or "").strip("<>"))}):
        return "mail", mailto
    return None


def _plain(url: str) -> str:
    """The address as given, refused if it holds spaces or control characters (header or request smuggling)."""
    if not url or any(ord(ch) < 33 or ord(ch) == 127 for ch in url):
        raise UnsubscribeError("the unsubscribe address has spaces or control characters")
    return url


def _public(host: str) -> str:
    """One public address for the host; every address it resolves to must be public."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise UnsubscribeError(f"couldn't find {host}") from None
    addresses = [info[4][0].split("%")[0] for info in infos]
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            raise UnsubscribeError(f"{host} isn't a public address")
    if not addresses:
        raise UnsubscribeError(f"couldn't find {host}")
    return addresses[0]


class _PinnedHTTPS(http.client.HTTPSConnection):
    """HTTPS to the address that was checked (so a second lookup can't send it elsewhere), with the certificate
    checked for the host name. No proxy, and http.client never follows a redirect."""

    def __init__(self, host: str, address: str, timeout: float):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self):
        sock = socket.create_connection((self._address, 443), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def one_click(url: str, opener=None) -> str:
    """RFC 8058 one-click: POST to the https address on port 443. Only a 2xx answer counts as unsubscribed."""
    parsed = urllib.parse.urlsplit(_plain(url))
    if parsed.scheme != "https" or not parsed.hostname:
        raise UnsubscribeError("one-click unsubscribing needs an https address")
    if parsed.port not in (None, 443) or parsed.username or parsed.password:
        raise UnsubscribeError("one-click unsubscribing goes only to an ordinary https address")
    body = b"List-Unsubscribe=One-Click"
    headers = {"Content-Type": "application/x-www-form-urlencoded",
               "User-Agent": "Towpath (RFC 8058 one-click unsubscribe)"}
    if opener is not None:  # tests: a stand-in for the network
        request = urllib.request.Request(url, data=body, method="POST", headers=headers)
        with opener.open(request, timeout=TIMEOUT) as response:
            status = response.status
    else:
        connection = _PinnedHTTPS(parsed.hostname, _public(parsed.hostname), TIMEOUT)
        try:
            path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
            connection.request("POST", path, body=body, headers=headers)
            status = connection.getresponse().status
        except (OSError, http.client.HTTPException):
            raise UnsubscribeError(f"couldn't reach {parsed.hostname}") from None
        finally:
            connection.close()
    if not 200 <= status < 300:
        raise UnsubscribeError(f"{parsed.hostname} answered {status}")
    return f"{parsed.hostname} answered {status}"


def _domain(address_or_host: str | None) -> str:
    """The last two labels of a host or an address's domain (an approximation of the registered domain)."""
    host = (address_or_host or "").rpartition("@")[2].strip("<> ").lower()
    return ".".join(host.split(".")[-2:])


def mail_message(mailto: str, sender: str, list_from: str | None = None, list_id: str | None = None) -> EmailMessage:
    """The unsubscribe email. It goes only to an address in the domain of the mail's sender (or of its List-Id),
    so a header can't make the owner's account write to anyone else; subject and body are short plain text."""
    parsed = urllib.parse.urlsplit(_plain(mailto))
    query = urllib.parse.parse_qs(parsed.query)
    to = urllib.parse.unquote(parsed.path)
    if to.count("@") != 1 or any(ord(ch) < 33 for ch in to):
        raise UnsubscribeError("the mailto address isn't an email address")
    if list_from is not None and _domain(to) not in {_domain(list_from), _domain((list_id or "").strip("<>"))}:
        raise UnsubscribeError(f"{to} isn't in the sender's own domain; unsubscribe on their page instead")

    def short(values, default):
        text = (values or [default])[0]
        return text if len(text) <= 200 and not any(ord(ch) < 32 for ch in text) else default

    m = EmailMessage()
    m["From"] = sender
    m["To"] = to
    m["Subject"] = short(query.get("subject"), "unsubscribe")
    m["Date"] = format_datetime(datetime.now(timezone.utc))
    m["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    m.set_content(short(query.get("body"), "unsubscribe"))
    return m


def send_mail(source, password: str, mailto: str, smtp_factory=None, list_from: str | None = None,
              list_id: str | None = None) -> str:
    """Send the unsubscribe email from the account's own address over its SMTP server."""
    host, port = SMTP.get(source.host, (None, None))
    if smtp_factory is None and host is None:
        raise UnsubscribeError("Towpath doesn't know this provider's SMTP server yet")
    message = mail_message(mailto, source.username, list_from=list_from or "", list_id=list_id)
    factory = smtp_factory or (lambda: smtplib.SMTP_SSL(host, port, timeout=TIMEOUT,
                                                         context=ssl.create_default_context()))
    try:
        with factory() as smtp:
            smtp.login(source.username, password)
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError:
        raise UnsubscribeError("the SMTP server refused the write password") from None
    except (smtplib.SMTPException, OSError):
        raise UnsubscribeError("couldn't send the unsubscribe email") from None
    return f"emailed {message['To']}"
