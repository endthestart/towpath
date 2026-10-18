"""Contacts and calendars over CardDAV and CalDAV: read-only requests, incremental sync, names and known people for
mail, and past calendar events as life-story evidence. A synthetic server on loopback only."""

import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from towpath import config as config_mod, connections, dav_sync, life, life_calendar
from towpath.adapters import dav
from towpath.stores import open_store
from towpath.unified import grants

sys.path.insert(0, str(Path(__file__).parent))

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
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{self.http.server_address[1]}"
        self.urls = {"contacts": f"{base}/dav/addressbooks/user/{USER}/",
                     "calendars": f"{base}/dav/calendars/user/{USER}/"}


@pytest.fixture
def world(tmp_path):
    server = Server()
    (tmp_path / "towpath.toml").write_text('[stores]\ndir = "state"\n')
    config = config_mod.load(tmp_path / "towpath.toml")
    creds = tmp_path / "credentials"
    yield config, creds, server
    server.http.shutdown()


def test_parsing():
    card = dav.parse_vcard(CARD)
    assert card == {"uid": "c1", "name": "Sam Rivera", "emails": ["sam@example.org", "sam.work@example.net"],
                    "phones": [], "birthday": "1985-04-12", "org": "Example Lock", "dates": []}
    trip, = dav.parse_events(EVENTS["trip.ics"])
    assert trip["summary"] == "Flight to Example Junction" and trip["start"] == "2009-05-14T08:15:00Z"
    assert trip["tz"] == "UTC" and not trip["recurring"]
    gym, = dav.parse_events(EVENTS["gym.ics"])
    assert gym["recurring"] and gym["tz"] == "America/Chicago" and gym["start"] == "2009-01-01T07:00:00"
    with pytest.raises(dav.DavViolation):
        dav.DavClient("a", "b").request("PUT", "https://example.org/", b"")
    with pytest.raises(dav.DavViolation):
        dav.DavClient("a", "b").request("PROPFIND", "http://example.org/", b"")


def test_add_sync_and_read_only(world):
    config, creds, server = world
    with pytest.raises(connections.ConnectionProblem, match="rejected"):
        dav_sync.test(USER, "wrong", server.urls)
    found = dav_sync.test(USER, PASSWORD, server.urls)
    assert {f["kind"] for f in found} == {"contacts", "calendars"}
    sid = dav_sync.add(config.store_dir, creds, USER, PASSWORD, found, server.urls)
    connections.start_indexing(config.store_dir, creds, sid)
    result = dav_sync.sync_pending(config, creds)[0]
    assert result["contacts"] == 1 and result["events"] == 3
    assert dav_sync.names(config.store_dir) == {"sam@example.org": "Sam Rivera", "sam.work@example.net": "Sam Rivera"}
    connections.start_indexing(config.store_dir, creds, sid)
    server.multigets.clear()
    dav_sync.sync_pending(config, creds)
    assert server.multigets == [[], []] or all(m == [] for m in server.multigets)  # nothing changed: nothing read
    server.items.pop(next(h for h in server.items if h.endswith("gym.ics")))
    connections.start_indexing(config.store_dir, creds, sid)
    assert dav_sync.sync_pending(config, creds)[0]["removed"] == 1
    assert set(server.methods) == {"PROPFIND", "REPORT"}
    assert PASSWORD not in "".join(p.read_bytes().decode("latin-1") for p in config.store_dir.glob("*.db"))


def test_past_calendar_events_become_life_evidence_once_allowed(world):
    config, creds, server = world
    sid = dav_sync.add(config.store_dir, creds, USER, PASSWORD, dav_sync.test(USER, PASSWORD, server.urls),
                       server.urls)
    connections.start_indexing(config.store_dir, creds, sid)
    dav_sync.sync_pending(config, creds)
    assert life_calendar.propose(config) == []  # not allowed for the life story yet
    grants.grant(config.store_dir, sid, "life-evidence", author="owner")
    assert life_calendar.propose(config)[0]["proposed"] == 1  # the flight; not the weekly gym, not the future
    claim, = life.claims(config.store_dir)
    assert claim.statement == "Flight to Example Junction at Example Airport" and claim.modality == "plan"
    assert claim.when.earliest.startswith("2009-05-14") and claim.significance == "notable"
    assert life_calendar.propose(config) == []  # never twice
    assert json.loads(json.dumps(claim.to_dict()))["citations"][0]["ref"].startswith(sid)


def test_icloud_style_discovery_finds_the_homes_and_stays_on_the_service(world):
    config, creds, server = world
    base = f"http://127.0.0.1:{server.http.server_address[1]}/"
    homes = dav_sync.discover(USER, PASSWORD, {"contacts": base, "calendars": base})
    assert homes == server.urls
    sid = dav_sync.add(config.store_dir, creds, USER, PASSWORD, dav_sync.test(USER, PASSWORD, homes), homes,
                       provider="icloud_dav")
    assert connections.get(config.store_dir, sid)["display_name"] == "iCloud contacts and calendars"
    server.calendar_home = "http://elsewhere.example/calendars/"  # a server pointing away is refused
    with pytest.raises(connections.ConnectionProblem, match="pointed somewhere else"):
        dav_sync.discover(USER, PASSWORD, {"calendars": base})
    with pytest.raises(connections.ConnectionProblem, match="rejected"):
        dav_sync.discover(USER, "wrong", {"contacts": base})
    server.calendar_home = None
    server.principal = "https://elsewhere.example/principals/42/"  # never sent the password
    calls = len(server.methods)
    with pytest.raises(connections.ConnectionProblem, match="pointed somewhere else"):
        dav_sync.discover(USER, PASSWORD, {"contacts": base})
    assert len(server.methods) == calls + 1
    client = dav.DavClient(USER, PASSWORD)
    client.request("PROPFIND", base, dav.PRINCIPAL_BODY, depth="0")
    with pytest.raises(dav.DavViolation):
        client.request("PROPFIND", "https://elsewhere.example/", dav.PRINCIPAL_BODY, depth="0")
    assert set(server.methods) <= {"PROPFIND", "REPORT"}  # reads only


def test_the_add_pages_for_fastmail_and_icloud(world, monkeypatch):
    from django.test import Client, override_settings

    from towpath.web import auth
    from towpath.web.application import configure

    config, creds, _ = world
    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(config.store_dir)
    auth.create_account(config.store_dir, "owner", "correct horse battery staple")
    with override_settings(TOWPATH_STORE_DIR=config.store_dir, TOWPATH_CONFIG=config,
                           TOWPATH_CREDENTIALS_DIR=creds, ALLOWED_HOSTS=["testserver"],
                           ROOT_URLCONF="towpath.web.connection_urls"):
        client = Client()
        client.post("/login", {"username": "owner", "password": "correct horse battery staple"})
        assert "Fastmail address" in client.get("/connections/add/fastmail_dav").content.decode()
        page = client.get("/connections/add/icloud_dav").content.decode()
        assert "Apple Account email" in page and "app-specific password" in page
        missing = client.post("/connections/add/icloud_dav", {"username": "reader", "password": ""})
        assert missing.status_code == 400 and "Apple Account email" in missing.content.decode()


def test_birthdays_in_the_next_two_weeks_with_the_age_they_turn(tmp_path):
    import json
    from datetime import date

    from towpath import dav_sync
    from towpath.stores import open_store

    store = tmp_path / "state"
    cards = [("Sam Rivera", "1990-10-20", ["sam@example.org"]), ("Pat Example", "---10-17", []),
             ("Leap Example", "2000-02-29", []), ("Old Card", "1604-10-25", []), ("Far Away", "1980-12-25", []),
             ("New Year", "1985-01-02", [])]
    with open_store(store, "dav", "connect") as db:
        for i, (name, birthday, emails) in enumerate(cards):
            db.execute("INSERT INTO contacts (source_id, href, etag, uid, name, emails, phones, birthday, org, "
                       "collection, updated_at) VALUES ('dav', ?, 'e', NULL, ?, ?, '[]', ?, NULL, 'c', 't')",
                       (f"/c/{i}", name, json.dumps(emails), birthday))
        db.commit()
    got = dav_sync.birthdays_ahead(store, today=date(2026, 10, 17))
    assert [(b["name"], b["in_days"], b["turns"]) for b in got] == [
        ("Pat Example", 0, None), ("Sam Rivera", 3, 36), ("Old Card", 8, None)]
    assert got[1]["address"] == "sam@example.org"
    assert [b["name"] for b in dav_sync.birthdays_ahead(store, today=date(2027, 2, 20))] == ["Leap Example"]
    assert [b["name"] for b in dav_sync.birthdays_ahead(store, today=date(2026, 12, 28))] == ["New Year"]


def test_this_week_is_in_the_owners_time_zone_without_cancelled_or_finished_events(tmp_path):
    from datetime import date, datetime
    from zoneinfo import ZoneInfo

    from towpath import dav_sync

    store = tmp_path / "state"
    rows = [  # href, summary, start, end, all_day, tz, recurring, override, status, location
        ("a", "Dentist", "2026-10-19T09:30:00", None, 0, "America/Chicago", 0, 0, None, None),
        ("b", "Dentist", "2026-10-19T09:30:00", None, 0, "America/Chicago", 0, 0, None, "Main St"),
        ("c", "Late call", "2026-10-18T02:00:00Z", None, 0, "UTC", 0, 0, None, None),  # 21:00 on the 17th here
        ("d", "Standup", "2026-10-18T09:00:00", None, 0, None, 1, 0, None, None),  # repeats: not listed yet
        ("e", "Standup (moved)", "2026-10-20T14:00:00", None, 0, None, 1, 1, None, None),  # a changed occurrence
        ("f", "Cancelled lunch", "2026-10-19T12:00:00", None, 0, None, 0, 0, "CANCELLED", None),
        ("g", "Trip", "2026-10-15", "2026-10-21", 1, None, 0, 0, None, None),  # under way: 15th to 20th
        ("h", "Breakfast", "2026-10-17T08:00:00", "2026-10-17T09:00:00", 0, None, 0, 0, None, None),  # over
        ("i", "Next week", "2026-10-24T10:00:00", None, 0, None, 0, 0, None, None),
    ]
    with open_store(store, "dav", "connect") as db:
        for href, summary, start, end, all_day, tz, recurring, override, status, location in rows:
            db.execute("INSERT INTO events (source_id, href, n, etag, uid, summary, start, end, all_day, tz, location, "
                       "organizer, attendees, recurring, description, collection, updated_at, override, status) "
                       "VALUES ('dav', ?, 0, 'e', NULL, ?, ?, ?, ?, ?, ?, NULL, '[]', ?, NULL, 'c', 't', ?, ?)",
                       (href, summary, start, end, all_day, tz, location, recurring, override, status))
        db.commit()
    now = datetime(2026, 10, 17, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    got = dav_sync.week_ahead(store, today=date(2026, 10, 17), now=now)
    assert [(e["summary"], str(e["day"]), e["time"]) for e in got] == [
        ("Trip", "2026-10-15", None), ("Late call", "2026-10-17", "21:00"), ("Dentist", "2026-10-19", "09:30"),
        ("Standup (moved)", "2026-10-20", "14:00")]
    assert got[0]["until"] == date(2026, 10, 20) and got[2]["location"] == "Main St"
    card = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x\r\nSUMMARY:Off\r\nSTATUS:CANCELLED\r\n"
            "DTSTART:20261019T120000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    assert dav.parse_events(card)[0]["status"] == "CANCELLED"


def test_anniversaries_and_apples_labelled_dates_are_read_from_cards(world):
    from datetime import date

    card = ("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Alex Example\r\nANNIVERSARY:20101030\r\n"
            "item2.X-ABDATE;type=pref:2014-11-02\r\nitem2.X-ABLabel:_$!<Anniversary>!$_\r\n"
            "item3.X-ABDATE:1999-10-25\r\nitem3.X-ABLabel:First day at work\r\n"
            "item4.X-ABDATE:not a date\r\nEND:VCARD\r\n")
    assert dav.card_dates(card) == [("Anniversary", "2010-10-30"), ("Anniversary", "2014-11-02"),
                                    ("First day at work", "1999-10-25")]
    config, creds, server = world
    found = dav_sync.test(USER, PASSWORD, server.urls)
    sid = dav_sync.add(config.store_dir, creds, USER, PASSWORD, found, server.urls)
    connections.start_indexing(config.store_dir, creds, sid)
    dav_sync.sync_pending(config, creds)
    with open_store(config.store_dir, "dav", "connect") as db:  # as if read before dates were kept
        db.execute("DELETE FROM contact_dates")
        db.commit()
    server.items["/dav/addressbooks/user/reader@example.com/Default/sam.vcf"] = ("e-1", CARD.replace(
        "END:VCARD", "ANNIVERSARY:2012-10-20\r\nEND:VCARD"))
    connections.start_indexing(config.store_dir, creds, sid)
    server.multigets.clear()
    dav_sync.sync_pending(config, creds)
    assert any(server.multigets)  # the unchanged card is read once more for its dates
    got = dav_sync.birthdays_ahead(config.store_dir, today=date(2026, 10, 17))
    assert [(b["name"], b["label"], b["turns"]) for b in got] == [("Sam Rivera", "Anniversary", 14)]


def test_the_same_person_in_two_address_books_shows_once_with_the_year_and_address(tmp_path):
    import json
    from datetime import date

    from towpath import dav_sync

    store = tmp_path / "state"
    with open_store(store, "dav", "connect") as db:
        for sid, name, emails, birthday in (("a", "sam rivera", ["old@example.org"], "1604-10-20"),
                                            ("b", "Sam Rivera", ["sam@example.org"], "1990-10-20"),
                                            ("c", "Placeholder", [], "1900-10-21")):
            db.execute("INSERT INTO contacts (source_id, href, etag, uid, name, emails, phones, birthday, org, "
                       "collection, updated_at) VALUES (?, '/c', 'e', NULL, ?, ?, '[]', ?, NULL, 'c', 't')",
                       (sid, name, json.dumps(emails), birthday))
        db.commit()
    got = dav_sync.birthdays_ahead(store, today=date(2026, 10, 17))
    assert [(b["name"], b["turns"], b["address"]) for b in got] == [("Sam Rivera", 36, "sam@example.org"),
                                                                   ("Placeholder", None, None)]
    assert dav_sync.birthdays_ahead(store, today=date(2026, 10, 6)) == []  # 14 days from the 6th end on the 19th
