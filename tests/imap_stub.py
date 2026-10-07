"""A small synthetic IMAP4rev1 server for tests: enough protocol for IMAPClient, nothing more.

It records every command (passwords masked), refuses anything outside the read-only allow-list,
and models the one side effect read-only access must avoid: a non-PEEK body fetch sets ``\\Seen``.
Mailbox contents are invented messages built with the standard library.
"""

import re
import socketserver
import threading
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from email.message import EmailMessage

from imapclient.imap_utf7 import decode as utf7_decode
from imapclient.imap_utf7 import encode as utf7_encode

READ_ONLY_COMMANDS = {"CAPABILITY", "LOGIN", "LIST", "EXAMINE", "SEARCH", "FETCH", "LOGOUT", "NOOP"}


@dataclass
class Message:
    raw: bytes
    internaldate: str = "02-Apr-2009 10:15:00 +0000"
    flags: set = field(default_factory=set)


@dataclass
class Mailbox:
    uidvalidity: int
    messages: dict = field(default_factory=dict)  # uid -> Message
    noselect: bool = False
    special: str | None = None  # an RFC 6154 special-use attribute such as \\Trash, sent in LIST

    @property
    def uidnext(self) -> int:
        return max(self.messages, default=0) + 1


class State:
    def __init__(self, mailboxes: dict, username: str = "reader@example.com", password: str = "synthetic-only"):
        self.mailboxes = mailboxes
        self.username, self.password = username, password
        self.transcript: list[str] = []
        self.violations: list[str] = []
        self.literal_plus = False
        self.grant_read_write = False
        self.drop_after_fetches: int | None = None
        self.max_fetch_ids: int | None = None
        self.fail_examine: set = set()
        self.fetch_commands = 0
        self.lock = threading.Lock()

    def flags(self) -> dict:
        return {(name, uid): frozenset(m.flags) for name, box in self.mailboxes.items()
                for uid, m in box.messages.items()}


def message(n: int, subject: str, body: str, sender: str = "sender@example.org",
            date: str = "Thu, 02 Apr 2009 10:15:00 +0000", attachment: tuple | None = None,
            charset_body: bool = False) -> bytes:
    m = EmailMessage()
    m["From"] = sender
    m["To"] = "reader@example.com"
    m["Subject"] = subject
    m["Date"] = date
    m["Message-ID"] = f"<stub-{n}@example.org>"
    if charset_body:
        m.set_content(body, charset="utf-8", cte="quoted-printable")
    else:
        m.set_content(body)
    if attachment:
        name, data, mtype = attachment
        maintype, subtype = mtype.split("/", 1)
        m.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return m.as_bytes(policy=policy.SMTP)


# -- protocol helpers -----------------------------------------------------------------------------------------


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _tokens(data: bytes) -> list:
    """Atoms, quoted strings, literals and parenthesised lists."""
    out: list = []
    stack = [out]
    i = 0
    while i < len(data):
        c = data[i:i + 1]
        if c in b" \r\n":
            i += 1
        elif c == b"(":
            stack.append([])
            stack[-2].append(stack[-1])
            i += 1
        elif c == b")":
            stack.pop()
            i += 1
        elif c == b'"':
            j, buf = i + 1, bytearray()
            while data[j:j + 1] != b'"':
                if data[j:j + 1] == b"\\":
                    j += 1
                buf += data[j:j + 1]
                j += 1
            stack[-1].append(bytes(buf))
            i = j + 1
        elif c == b"{":
            j = data.index(b"}", i)
            n = int(data[i + 1:j].rstrip(b"+"))
            start = data.index(b"\n", j) + 1
            stack[-1].append(data[start:start + n])
            i = start + n
        else:
            j = i
            depth = 0
            while j < len(data) and (data[j:j + 1] not in b" ()\r\n" or depth):
                if data[j:j + 1] == b"[":
                    depth += 1
                elif data[j:j + 1] == b"]":
                    depth -= 1
                j += 1
            stack[-1].append(data[i:j])
            i = j
    return out


def _uidset(spec: str, uids: list[int]) -> list[int]:
    wanted = set()
    top = max(uids, default=0)
    for piece in spec.split(","):
        if ":" in piece:
            a, b = piece.split(":")
            a = top if a == "*" else int(a)
            b = top if b == "*" else int(b)
            wanted |= {u for u in uids if min(a, b) <= u <= max(a, b)}
        else:
            wanted |= {u for u in uids if u == (top if piece == "*" else int(piece))}
    return sorted(wanted)


def _leaves(msg, prefix=""):
    """IMAP part numbering over a parsed message: {section: part}."""
    if not msg.is_multipart():
        return {prefix.rstrip(".") or "1": msg}
    out = {}
    for i, part in enumerate(msg.get_payload(), start=1):
        out.update(_leaves(part, f"{prefix}{i}.") if part.is_multipart() else {f"{prefix}{i}": part})
    return out


def _body_bytes(part) -> bytes:
    payload = part.get_payload(decode=False)
    return payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)


def _bodystructure(msg) -> str:
    if msg.is_multipart():
        inner = "".join(_bodystructure(p) for p in msg.get_payload())
        subtype = _quote(msg.get_content_subtype().upper())
        return f'({inner} {subtype} ("BOUNDARY" {_quote(msg.get_boundary())}) NIL NIL)'
    maintype, subtype = msg.get_content_maintype().upper(), msg.get_content_subtype().upper()
    params = []
    if msg.get_param("charset"):
        params += ['"CHARSET"', _quote(str(msg.get_param("charset")))]
    if msg.get_param("name"):
        params += ['"NAME"', _quote(str(msg.get_param("name")))]
    param_text = f"({' '.join(params)})" if params else "NIL"
    encoding = _quote((msg.get("Content-Transfer-Encoding") or "7BIT").upper())
    body = _body_bytes(msg)
    disposition = msg.get_content_disposition()
    dsp = "NIL"
    if disposition:
        filename = msg.get_filename()
        dsp = f'({_quote(disposition.upper())} ' + (f'("FILENAME" {_quote(filename)})' if filename else "NIL") + ")"
    base = f"{_quote(maintype)} {_quote(subtype)} {param_text} NIL NIL {encoding} {len(body)}"
    if maintype == "TEXT":
        lines = body.count(b"\n")
        return f"({base} {lines} NIL {dsp} NIL)"
    return f"({base} NIL {dsp} NIL)"


class Handler(socketserver.StreamRequestHandler):
    state: State

    def send(self, text: str | bytes) -> None:
        self.wfile.write(text if isinstance(text, bytes) else text.encode("utf-8"))

    def read_command(self) -> bytes | None:
        data = b""
        while True:
            line = self.rfile.readline()
            if not line:
                return None
            data += line
            match = re.search(rb"\{(\d+)(\+?)\}\r\n$", line)
            if not match:
                return data
            if not match.group(2):
                self.send("+ go ahead\r\n")
            data += self.rfile.read(int(match.group(1)))

    def handle(self) -> None:
        caps = "IMAP4rev1 AUTH=PLAIN" + (" LITERAL+" if self.state.literal_plus else "")
        self.send(f"* OK [CAPABILITY {caps}] synthetic stub ready\r\n")
        self.selected: str | None = None
        while True:
            data = self.read_command()
            if data is None:
                return
            tokens = _tokens(data)
            tag, words = tokens[0].decode(), tokens[1:]
            command = words[0].decode().upper()
            uid = command == "UID"
            if uid:
                command, words = words[1].decode().upper(), words[1:]
            args = words[1:]
            logged = f"{tag} LOGIN <masked>" if command == "LOGIN" else data.decode("utf-8", "replace").strip()
            with self.state.lock:
                self.state.transcript.append(logged)
            if command not in READ_ONLY_COMMANDS or (command in {"SEARCH", "FETCH"} and not uid):
                with self.state.lock:
                    self.state.violations.append(command)
                self.send(f"{tag} NO [CANNOT] this synthetic server is read-only\r\n")
                continue
            if getattr(self, f"cmd_{command.lower()}")(tag, args) is False:
                return

    def cmd_capability(self, tag, args):
        caps = "IMAP4rev1 AUTH=PLAIN" + (" LITERAL+" if self.state.literal_plus else "")
        self.send(f"* CAPABILITY {caps}\r\n{tag} OK done\r\n")

    def cmd_noop(self, tag, args):
        self.send(f"{tag} OK done\r\n")

    def cmd_login(self, tag, args):
        user, password = (a.decode() for a in args[:2])
        if (user, password) != (self.state.username, self.state.password):
            self.send(f"{tag} NO [AUTHENTICATIONFAILED] invalid credentials\r\n")
        else:
            self.send(f"{tag} OK logged in\r\n")

    def cmd_logout(self, tag, args):
        self.send(f"* BYE synthetic stub closing\r\n{tag} OK done\r\n")
        return False

    def cmd_list(self, tag, args):
        for name, box in sorted(self.state.mailboxes.items()):
            flags = "\\Noselect" if box.noselect else "\\HasNoChildren"
            if box.special:
                flags += " " + box.special
            self.send(f'* LIST ({flags}) "/" {_quote(utf7_encode(name).decode())}\r\n')
        self.send(f"{tag} OK done\r\n")

    def cmd_examine(self, tag, args):
        name = utf7_decode(args[0])
        box = self.state.mailboxes.get(name)
        if name in self.state.fail_examine:
            self.selected = None
            self.send(f"{tag} NO [UNAVAILABLE] mailbox temporarily unavailable\r\n")
            return
        if box is None or box.noselect:
            self.selected = None
            self.send(f"{tag} NO [NONEXISTENT] no such mailbox\r\n")
            return
        self.selected = name
        mode = "READ-WRITE" if self.state.grant_read_write else "READ-ONLY"
        self.send(f"* {len(box.messages)} EXISTS\r\n* 0 RECENT\r\n* FLAGS (\\Seen \\Answered \\Flagged)\r\n"
                  f"* OK [UIDVALIDITY {box.uidvalidity}] UIDs valid\r\n* OK [UIDNEXT {box.uidnext}] next\r\n"
                  f"{tag} OK [{mode}] EXAMINE completed\r\n")

    def _box(self, tag) -> Mailbox | None:
        box = self.state.mailboxes.get(self.selected) if self.selected else None
        if box is None:
            self.send(f"{tag} BAD no mailbox selected\r\n")
        return box

    def cmd_search(self, tag, args):
        box = self._box(tag)
        if box is None:
            return
        args = list(args)
        charset = "us-ascii"
        if args and args[0].upper() == b"CHARSET":
            charset = args[1].decode()
            args = args[2:]
        uids = sorted(box.messages)
        i = 0
        while i < len(args):
            key = args[i].decode().upper()
            if key == "ALL":
                i += 1
            elif key in {"TEXT", "BODY", "SUBJECT", "FROM"}:
                needle = args[i + 1].decode(charset).lower()
                uids = [u for u in uids if needle in self._searchable(box.messages[u], key)]
                i += 2
            elif key == "UID":
                uids = [u for u in uids if u in _uidset(args[i + 1].decode(), uids)]
                i += 2
            else:
                self.send(f"{tag} BAD unsupported search key\r\n")
                return
        found = "".join(f" {u}" for u in uids)
        self.send(f"* SEARCH{found}\r\n{tag} OK SEARCH completed\r\n")

    @staticmethod
    def _searchable(msg: Message, key: str) -> str:
        parsed = message_from_bytes(msg.raw, policy=policy.default)
        if key == "SUBJECT":
            return str(parsed["Subject"] or "").lower()
        if key == "FROM":
            return str(parsed["From"] or "").lower()
        texts = [str(parsed["Subject"] or "")] if key == "TEXT" else []
        for part in parsed.walk():
            if part.get_content_maintype() == "text":
                texts.append(part.get_content())
        return "\n".join(texts).lower()

    def cmd_fetch(self, tag, args):
        box = self._box(tag)
        if box is None:
            return
        self.state.fetch_commands += 1
        if self.state.drop_after_fetches is not None and self.state.fetch_commands > self.state.drop_after_fetches:
            return False  # connection lost mid-run
        uids = _uidset(args[0].decode(), sorted(box.messages))
        if self.state.max_fetch_ids is not None and len(uids) > self.state.max_fetch_ids:
            self.send(f"{tag} BAD [LIMIT] too many messages in one FETCH\r\n")
            return
        items = args[1] if isinstance(args[1], list) else [args[1]]
        names = []
        for item in items:
            if isinstance(item, list):  # a HEADER.FIELDS list attached to the previous atom
                names[-1] = names[-1] + b" (" + b" ".join(item) + b")"
            else:
                names.append(item)
        names = [n.decode().upper() for n in names]
        seqs = {u: i + 1 for i, u in enumerate(sorted(box.messages))}
        for u in uids:
            msg = box.messages[u]
            parsed = message_from_bytes(msg.raw, policy=policy.compat32)
            pieces = [f"UID {u}"]
            literals = []
            for name in names:
                if name == "UID":
                    continue
                if name == "FLAGS":
                    pieces.append(f"FLAGS ({' '.join(sorted(msg.flags))})")
                elif name == "RFC822.SIZE":
                    pieces.append(f"RFC822.SIZE {len(msg.raw)}")
                elif name == "INTERNALDATE":
                    pieces.append(f"INTERNALDATE {_quote(msg.internaldate)}")
                elif name == "BODYSTRUCTURE":
                    pieces.append(f"BODYSTRUCTURE {_bodystructure(parsed)}")
                elif name.startswith(("BODY[", "BODY.PEEK[", "RFC822")):
                    if not name.startswith("BODY.PEEK["):
                        msg.flags.add("\\Seen")  # what a careless client would cause
                        with self.state.lock:
                            self.state.violations.append(f"non-peek {name}")
                    section = name[name.index("[") + 1:name.rindex("]")] if "[" in name else ""
                    data = self._section(parsed, msg.raw, section)
                    label = f"BODY[{section}]" if "[" in name else name
                    literals.append((label, data))
            text = " ".join(pieces)
            self.send(f"* {seqs[u]} FETCH ({text}")
            for label, data in literals:
                self.send(f" {label} {{{len(data)}}}\r\n".encode() + data)
            self.send(")\r\n")
        self.send(f"{tag} OK FETCH completed\r\n")

    @staticmethod
    def _section(parsed, raw: bytes, section: str) -> bytes:
        if section.startswith("HEADER.FIELDS"):
            fields = re.findall(r"[A-Z0-9-]+", section[len("HEADER.FIELDS"):])
            lines = [f"{k}: {v}" for k, v in parsed.items() if k.upper() in fields]
            return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")
        if section == "":
            return raw
        return _body_bytes(_leaves(parsed)[section])


class Server:
    """Context manager: a threaded stub on 127.0.0.1 with a random port."""

    def __init__(self, state: State):
        self.state = state
        handler = type("BoundHandler", (Handler,), {"state": state})
        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def build(spec) -> dict:
    """Mailboxes from ``towpath.unified.environment.imap_mailboxes()``-shaped specs."""
    boxes = {}
    n = 0
    for name, uidvalidity, messages in spec:
        box = Mailbox(uidvalidity)
        for uid, (subject, body, sender, date, attachment) in enumerate(messages, start=1):
            n += 1
            box.messages[uid] = Message(message(n, subject, body, sender, date, attachment))
        boxes[name] = box
    return boxes


if __name__ == "__main__":  # a synthetic server for local acceptance: python tests/imap_stub.py [port]
    import os
    import sys
    import time

    from towpath.unified.environment import IMAP_PASSWORD_ENV, imap_mailboxes

    state = State(build(imap_mailboxes()), password=os.environ.get(IMAP_PASSWORD_ENV, "synthetic-only"))
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    server = Server(state)
    if port:
        server.server.server_close()
        handler = type("BoundHandler", (Handler,), {"state": state})
        server.server = socketserver.ThreadingTCPServer(("127.0.0.1", port), handler)
        server.port = port
    with server:
        print(f"synthetic IMAP server on 127.0.0.1:{server.port} (read-only; Ctrl-C to stop)", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print(f"violations: {state.violations}")
