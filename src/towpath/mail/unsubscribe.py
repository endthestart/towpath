"""Unsubscribing: what a sender's List-Unsubscribe headers offer, and carrying an unsubscribe out.

RFC 8058 one-click: an HTTPS POST of ``List-Unsubscribe=One-Click`` to the header's https address, when the sender
also sends ``List-Unsubscribe-Post``. Otherwise a ``mailto:`` address gets a short email, sent over the account's SMTP
server with its write password. A header offering only a web page can't be done for the owner; the page shows the
link instead. Requests go only to public addresses over HTTPS, without following redirects.
"""

import ipaddress
import re
import smtplib
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from datetime import datetime, timezone

URLS = re.compile(r"<([^>]+)>")
SMTP = {"imap.fastmail.com": ("smtp.fastmail.com", 465)}
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


def best(found: dict) -> tuple[str, str] | None:
    """The method Towpath can carry out, one-click first."""
    if found.get("post"):
        return "post", found["post"]
    if found.get("mail"):
        return "mail", found["mail"]
    return None


def _public(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise UnsubscribeError(f"couldn't find {host}") from None
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%")[0])
        if not address.is_global:
            raise UnsubscribeError(f"{host} isn't a public address")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def one_click(url: str, opener=None) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise UnsubscribeError("one-click unsubscribing needs an https address")
    if opener is None:
        _public(parsed.hostname)
        opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, data=b"List-Unsubscribe=One-Click", method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded",
                                              "User-Agent": "Towpath (RFC 8058 one-click unsubscribe)"})
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            status = exc.code
        else:
            raise UnsubscribeError(f"{parsed.hostname} answered {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise UnsubscribeError(f"couldn't reach {parsed.hostname}") from None
    return f"{parsed.hostname} answered {status}"


def mail_message(mailto: str, sender: str) -> EmailMessage:
    parsed = urllib.parse.urlsplit(mailto)
    query = urllib.parse.parse_qs(parsed.query)
    to = urllib.parse.unquote(parsed.path)
    if to.count("@") != 1:
        raise UnsubscribeError("the mailto address isn't an email address")
    m = EmailMessage()
    m["From"] = sender
    m["To"] = to
    m["Subject"] = (query.get("subject") or ["unsubscribe"])[0]
    m["Date"] = format_datetime(datetime.now(timezone.utc))
    m["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    m.set_content((query.get("body") or ["unsubscribe"])[0])
    return m


def send_mail(source, password: str, mailto: str, smtp_factory=None) -> str:
    """Send the unsubscribe email from the account's own address over its SMTP server."""
    host, port = SMTP.get(source.host, (None, None))
    if smtp_factory is None and host is None:
        raise UnsubscribeError("Towpath doesn't know this provider's SMTP server yet")
    message = mail_message(mailto, source.username)
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
