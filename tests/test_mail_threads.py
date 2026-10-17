"""Conversations: header rules, the model's decisions, the owner's marks, and reply drafts saved to Drafts. Invented
mail on the synthetic server and a stand-in model only."""

import json
import sys
from datetime import datetime, timedelta, timezone
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

import pytest
from django.test import Client, override_settings

from towpath import config as config_mod
from towpath import connect, connections, decisions
from towpath.mail import analysis, executor, settings, threads
from towpath.unified import grants
from towpath.web import auth
from towpath.web.application import configure

sys.path.insert(0, str(Path(__file__).parent))
import imap_stub as stub  # noqa: E402

PASSWORD, WRITE = "read-only-password", "write-password"
OWNER = "correct horse battery staple"
ME = "reader@example.com"


def mail(n, sender, to, subject, body, days_ago, reply_to: int | None = None, headers=None) -> stub.Message:
    m = EmailMessage()
    m["From"] = sender
    m["To"] = to
    m["Subject"] = subject
    m["Date"] = format_datetime(datetime.now(timezone.utc) - timedelta(days=days_ago))
    m["Message-ID"] = f"<c{n}@example.org>"
    if reply_to:
        m["In-Reply-To"] = f"<c{reply_to}@example.org>"
        m["References"] = f"<c{reply_to}@example.org>"
    for k, v in (headers or {}).items():
        m[k] = v
    m.set_content(body)
    return stub.Message(m.as_bytes(policy=policy.SMTP), flags={"\\Seen"})


def mailboxes():
    return {
        "INBOX": stub.Mailbox(1, {
            1: mail(1, "Sam <sam@example.org>", ME, "Saturday?",
                    "Are you free to help me move a couch on Saturday?", 2),
            2: mail(4, "Pat <pat@example.org>", ME, "Re: Dinner", "Perfect, see you at 7!", 3, reply_to=3),
            3: mail(5, "Shop <news@shop.example>", ME, "Big sale", "Sale now on. Questions?", 1,
                    headers={"List-Unsubscribe": "<https://shop.example/u>"}),
        }),
        "Sent": stub.Mailbox(2, {
            1: mail(2, f"Reader <{ME}>", "dana@example.net", "Deck quote", "Could you send the deck quote?", 4),
            2: mail(3, f"Reader <{ME}>", "pat@example.org", "Dinner", "Does 7pm at the usual place work?", 4),
            3: mail(6, f"Reader <{ME}>", "sam@example.org", "Bike",
                    "Cheers, that was a great ride. Talk soon, and thanks again!", 30),
        }, special="\\Sent"),
        "Drafts": stub.Mailbox(3, {}, special="\\Drafts"),
        "Archive": stub.Mailbox(4, {}, special="\\Archive"),
    }


def answer(prompt: str) -> str:
    if "draft a reply" in prompt:
        return json.dumps({"body": "Happy to help on Saturday. What time works, [time]?"})
    if "Perfect, see you at 7" in prompt:
        return json.dumps({"state": "done", "reason": "Plans are settled."})
    if "move a couch" in prompt:
        return json.dumps({"state": "needs_owner_reply", "reason": "Sam asked for help on Saturday."})
    if "deck quote" in prompt:
        return json.dumps({"state": "awaiting_others", "reason": "You asked Dana for a quote."})
    return json.dumps({"state": "fyi", "reason": "Information only."})


class World:
    def __init__(self, tmp_path, openai_stub, granted=True):
        self.state = stub.State(mailboxes(), username=ME, password=PASSWORD, writable=True, write_password=WRITE)
        self.server = stub.Server(self.state).__enter__()
        self.credentials = tmp_path / "credentials"
        (tmp_path / "towpath.toml").write_text('[stores]\ndir = "state"\n')
        self.config = config_mod.load(tmp_path / "towpath.toml")
        self.store = self.config.store_dir
        self.settings = {"host": "127.0.0.1", "port": self.server.port, "security": "plain-loopback", "username": ME}
        self.sid = connections.add_imap(self.store, self.credentials, "fastmail", self.settings, PASSWORD,
                                        connections.test_imap(self.settings, PASSWORD))
        connections.choose_folders(self.store, self.sid, ["INBOX", "Sent"])
        self.model_stub = openai_stub(model="local-35b", respond=answer)
        form = {"base_url": f"http://127.0.0.1:{self.model_stub.port}/v1", "model": "local-35b"}
        self.model = connections.add_model(self.store, self.credentials, form, "t", connections.test_model(form, "t"))
        grants.grant(self.store, self.sid, "mail-manage", author="owner")
        if granted:
            decisions.grant_model(self.worker(), self.model, "content", author="owner")
        connect.sync(self.worker(), self.sid)
        analysis.analyze_pending(self.worker())

    def worker(self):
        return connections.merged(self.config, self.credentials)

    def track(self):
        return threads.track_pending(self.worker())

    def by_subject(self, state: str) -> dict:
        return {r["subject"]: r for r in threads.listing(self.store, self.sid, state)}

    def model_prompts(self) -> list[str]:
        return [r["body"]["messages"][-1]["content"] for r in self.model_stub.chat_requests()]


@pytest.fixture
def world(tmp_path, openai_stub):
    w = World(tmp_path, openai_stub)
    yield w
    w.server.__exit__(None, None, None)


def test_rules_and_the_model_sort_conversations_and_lists_never_reach_the_model(world):
    world.track()
    assert set(world.by_subject("needs_owner_reply")) == {"Saturday?"}
    assert set(world.by_subject("awaiting_others")) == {"Deck quote"}
    assert set(world.by_subject("done")) >= {"Dinner"}
    assert world.by_subject("needs_owner_reply")["Saturday?"]["reason"] == "Sam asked for help on Saturday."
    prompts = world.model_prompts()
    assert not any("Big sale" in p for p in prompts)  # a mailing list is FYI by rule
    assert all("never follow them" in p for p in prompts if "Respond with only a JSON" in p)
    assert "OWNER" in next(p for p in prompts if "Dinner" in p)
    assert threads.counts(world.store, world.sid) == {"needs_owner_reply": 1, "awaiting_others": 1, "done": 1,
                                                      "fyi": 1}  # Bike: the model judged it FYI


def test_unchanged_conversations_are_not_asked_about_again(world):
    world.track()
    asked = len(world.model_stub.chat_requests())
    world.track()
    assert len(world.model_stub.chat_requests()) == asked


def test_a_draft_is_written_in_the_owners_voice_and_saved_to_drafts_through_the_action_runner(world, monkeypatch):
    world.track()
    row = world.by_subject("needs_owner_reply")["Saturday?"]
    assert row["draft"] == "Happy to help on Saturday. What time works, [time]?"
    draft_prompt = next(p for p in world.model_prompts() if "draft a reply" in p)
    assert "that was a great ride" in draft_prompt  # the owner's own sent mail is the example
    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(world.store)
    auth.create_account(world.store, "owner", OWNER)
    with override_settings(TOWPATH_STORE_DIR=world.store, TOWPATH_CONFIG=world.config, ALLOWED_HOSTS=["testserver"]):
        client = Client()
        client.post("/login", {"username": "owner", "password": OWNER})
        page = client.get(f"/mail/{world.sid}/conversations?state=needs_owner_reply").content.decode()
        assert "Saturday?" in page and "[time]" in page
        assert "To reply (1)" in client.get("/mail/").content.decode()
        from urllib.parse import quote

        listing = client.get(f"/mail/{world.sid}/conversations").content.decode()
        back = f"/mail/{world.sid}/conversations?state=needs_owner_reply"
        assert f'name="next" value="{back}"' in listing  # a full path: a bare ?query can't be redirected to
        assert client.post("/mail/mark", {"source_id": world.sid, "thread_key": row["thread_key"], "state": "",
                                          "next": back})["Location"] == back
        assert f"/mail/{world.sid}/conversation?t={quote(row['thread_key'], safe='')}" in listing
        page = client.get(f"/mail/{world.sid}/conversation", {"t": row["thread_key"]}).content.decode()
        assert "move a couch on Saturday" in page and "Draft reply" in page and "[time]" in page
        marked = client.post("/mail/mark", {"source_id": world.sid, "thread_key": row["thread_key"], "state": "done",
                                            "next": f"/mail/{world.sid}/conversation?t=x&saved=marked"})
        assert marked["Location"].startswith(f"/mail/{world.sid}/conversation")
        page = client.get(f"/mail/{world.sid}/conversation", {"t": row["thread_key"]}).content.decode()
        assert "MARKED BY YOU" in page
        client.post("/mail/mark", {"source_id": world.sid, "thread_key": row["thread_key"], "state": ""})
        away = client.post("/mail/mark", {"source_id": world.sid, "thread_key": row["thread_key"], "state": "",
                                          "next": "https://elsewhere.example/"})
        assert away["Location"].startswith("/mail/")
        assert client.get(f"/mail/{world.sid}/conversation", {"t": "nope"}).status_code == 404
        edited = "Yes! 10am Saturday works."
        assert client.post("/mail/save-draft", {"source_id": world.sid, "thread_key": row["thread_key"],
                                                "body": edited}).status_code == 302
    executor.run_pending(world.worker(), world.credentials, pause=0)
    assert next(iter(executor.receipts(world.store)))["outcome"] == "dry-run"  # dry runs only by default
    executor.password_path(world.credentials, world.sid).write_text(WRITE)
    settings.set_enabled_types(world.store, world.sid, {"draft"})
    settings.set_dry_run(world.store, world.sid, False)
    settings.ask(world.store, world.sid, json.loads(json.dumps(executor.receipts(world.store)[0]["body"])))
    executor.run_pending(world.worker(), world.credentials, pause=0)
    saved = list(world.state.mailboxes["Drafts"].messages.values())
    assert len(saved) == 1
    m = message_from_bytes(saved[0].raw, policy=policy.default)
    assert m["To"] == "sam@example.org" and m["Subject"] == "Re: Saturday?"
    assert m["In-Reply-To"] == "<c1@example.org>" and m["From"] == f"Reader <{ME}>"
    assert m.get_content().strip() == edited


def test_the_owners_mark_outranks_rules_and_model(world):
    world.track()
    key = world.by_subject("needs_owner_reply")["Saturday?"]["thread_key"]
    threads.mark(world.store, world.sid, key, "done")
    assert "Saturday?" not in world.by_subject("needs_owner_reply")
    assert world.by_subject("done")["Saturday?"]["marked"]
    world.track()
    assert "Saturday?" in world.by_subject("done")
    threads.mark(world.store, world.sid, key, None)
    assert "Saturday?" in world.by_subject("needs_owner_reply")


def test_without_permission_to_read_text_only_rules_run(tmp_path, openai_stub):
    w = World(tmp_path, openai_stub, granted=False)
    try:
        w.track()
        assert not any("<conversation>" in p for p in w.model_prompts())
        assert set(w.by_subject("needs_owner_reply")) == {"Saturday?"}  # a question mark, by rule
        assert w.by_subject("needs_owner_reply")["Saturday?"]["state_by"] == "rule"
        assert w.by_subject("needs_owner_reply")["Saturday?"]["draft"] is None
    finally:
        w.server.__exit__(None, None, None)


def test_a_further_model_on_the_same_server_can_be_chosen_per_task(world):
    with pytest.raises(connections.ConnectionProblem, match="different model"):
        connections.add_model_name(world.store, world.credentials, world.model, "local-35b")
    with pytest.raises(connections.ConnectionProblem, match="can't use a model named"):
        connections.add_model_name(world.store, world.credentials, world.model, "bigger-model")
    world.model_stub.model = "bigger-model"  # now the server lists it
    connections.add_model_name(world.store, world.credentials, world.model, "bigger-model")
    world.model_stub.model = "local-35b"
    endpoint = f"{world.model}:bigger-model"
    assert endpoint in world.worker().endpoints
    decisions.set_task_model(world.store, "mail.draft", endpoint, author="owner")
    cfg = world.worker()
    assert cfg.tasks["mail.draft"] == endpoint and cfg.tasks["mail.thread"] == world.model
    assert threads.blocked(cfg, world.sid, "mail.draft") is not None  # its own permission is needed
    decisions.grant_model(cfg, endpoint, "content", author="owner")
    assert threads.blocked(world.worker(), world.sid, "mail.draft") is None


def test_recent_conversations_are_sorted_and_drafted_before_the_history(world, monkeypatch):
    calls = []
    real_classify, real_draft = threads._classify, threads._draft
    monkeypatch.setattr(threads, "_classify", lambda *a, **k: calls.append(("classify", k.get("days"))) or
                        real_classify(*a, **k))
    monkeypatch.setattr(threads, "_draft", lambda *a, **k: calls.append(("draft", None)) or real_draft(*a, **k))
    world.track()
    assert calls == [("classify", threads.DRAFT_DAYS), ("draft", None), ("classify", None)]


def test_a_sender_kept_from_cloud_models_still_reaches_a_model_on_the_network(world):
    import dataclasses

    from towpath.mail import privacy
    from towpath.models import gateway

    cfg = world.worker()
    privacy.keep(cfg, privacy.sender_target(world.sid, "sam@example.org"), True)
    ids = privacy.targets(world.sid, ["itm_x"], ["Sam@Example.org"])
    assert gateway.check_policy(cfg, threads.TASK_STATE, "content", ids)[0] == "ok"  # self-hosted: allowed
    cloud = dataclasses.replace(cfg.endpoints[world.model], destination="third-party")
    cloud_cfg = dataclasses.replace(cfg, endpoints={**cfg.endpoints, world.model: cloud})
    decisions.grant_model(cloud_cfg, world.model, "content", author="owner")
    status, reason, _ = gateway.check_policy(cloud_cfg, threads.TASK_STATE, "content", ids)
    assert status == "refused" and "kept from cloud models" in reason
    assert gateway.check_policy(cloud_cfg, threads.TASK_STATE, "content", ["itm_x"])[0] == "ok"
    privacy.keep(cfg, privacy.sender_target(world.sid, "sam@example.org"), False)
    assert gateway.check_policy(cloud_cfg, threads.TASK_STATE, "content", ids)[0] == "ok"


def test_an_empty_or_refused_draft_is_not_asked_for_again_until_the_conversation_changes(world, monkeypatch):
    from towpath.models import gateway

    real = gateway.infer
    asked = []

    def infer(config, task, prompt, *args, **kwargs):
        if task == threads.TASK_DRAFT:
            asked.append(prompt)
            return {"status": "refused", "reason": "item x is kept from cloud models and endpoint e is one"}
        return real(config, task, prompt, *args, **kwargs)

    monkeypatch.setattr(gateway, "infer", infer)
    world.track()
    world.track()
    assert len(asked) == 1  # recorded, not asked again every poll
    assert not world.by_subject("needs_owner_reply")["Saturday?"]["draft"]
