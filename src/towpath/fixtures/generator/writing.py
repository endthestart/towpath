"""Writing the fixtures and config, and changing them for later runs."""

import json
import random
from datetime import timedelta
from pathlib import Path

from towpath.fixtures.generator.accounts import build_account_a, build_account_b, _write_folder
from towpath.fixtures.generator.pieces import (
    BASE_TIME,
    HOLDER_A,
    Account,
    _headers,
    synthetic_pdf,
    synthetic_png,
    _text,
    _with_attachment,
)

CONFIG = """# Synthetic first-slice configuration. Paths are relative to this file.
[stores]
dir = "state"

[[sources]]
id = "src_a"
kind = "mail-provider"
adapter = "fixture-gmail"
path = "account_a.json"

[[sources]]
id = "src_b"
kind = "mail-provider"
adapter = "fixture-gmail"
path = "account_b.json"

[[sources]]
id = "dst_documents"
kind = "document-system"
adapter = "folder"
path = "documents"

[[sources]]
id = "dst_photos"
kind = "photo-library"
adapter = "folder"
path = "photos"

[[selectors]]
id = "pdf-to-documents"
media_types = ["application/pdf"]
disposition = "attachment"
min_bytes = 1
destination = "dst_documents"
exclude_labels = ["SPAM", "TRASH"]
classifier = "doc.is_statement"

[[selectors]]
id = "photos-to-library"
media_types = ["image/png", "image/jpeg"]
disposition = "attachment"
min_bytes = 1
destination = "dst_photos"
exclude_labels = ["SPAM", "TRASH"]
"""


def generate(out: Path, seed: int = 7) -> dict:
    """Write both accounts, both destination folders, and a config into ``out``."""
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    acct_a, known = build_account_a(rng)
    acct_b = build_account_b(rng, known["shared_message_id"])
    (out / "account_a.json").write_text(json.dumps(acct_a.to_json()))
    (out / "account_b.json").write_text(json.dumps(acct_b.to_json()))

    stmt_name = "statement-2026-03.pdf"
    docs = {"scan-0001.pdf": synthetic_pdf("unrelated scan", rng), stmt_name: known["statements"][stmt_name][1],
            "tax-2025.pdf": synthetic_pdf("tax form", rng)}
    photo_name = "photo-2.png"
    photos = {"beach.png": synthetic_png(rng), photo_name: known["photos"][photo_name][1]}
    _write_folder(out / "documents", "sha256", docs, rng)
    _write_folder(out / "photos", "sha1-base64", photos, rng)
    (out / "towpath.toml").write_text(CONFIG)

    summary = {
        "seed": seed,
        "account_a": acct_a.address,
        "messages_a": len(acct_a.messages),
        "messages_b": len(acct_b.messages),
        "present_in_documents": known["statements"][stmt_name][0],
        "present_in_photos": known["photos"][photo_name][0],
        "inline_pdf": known["inline_pdf"][0],
        "deep_pdf": known["deep_pdf"][0],
        "injection": known["injection"],
        "no_message_id": known["no_message_id"],
        "bad_date": known["bad_date"],
        "latin1": known["latin1"],
        "shared_message_id": known["shared_message_id"],
        "statement_jan": known["statements"]["statement-2026-01.pdf"][0],
        "newsletters": [mid for mid, m in acct_a.messages.items() if "CATEGORY_PROMOTIONS" in m["labelIds"]
                        and any(h["name"] == "List-Id" for h in m["payload"]["headers"])],
    }
    (out / "fixture-summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def advance(out: Path, seed: int = 11) -> dict:
    """Second state: another tool relabels three newsletters, one statement is
    deleted, and two messages arrive. Each change is recorded in history."""
    path = out / "account_a.json"
    data = json.loads(path.read_text())
    summary = json.loads((out / "fixture-summary.json").read_text())
    rng = random.Random(seed)
    history_id = int(data["historyId"])

    for msg_id in summary["newsletters"][:3]:
        msg = data["messages"][msg_id]
        msg["labelIds"] = sorted((set(msg["labelIds"]) - {"INBOX", "UNREAD"}) | {"Label_Newsletters"})
        history_id += 1
        data["history"].append({"id": str(history_id), "labelsRemoved": [
            {"message": {"id": msg_id, "threadId": msg["threadId"]}, "labelIds": ["INBOX", "UNREAD"]}]})
        history_id += 1
        data["history"].append({"id": str(history_id), "labelsAdded": [
            {"message": {"id": msg_id, "threadId": msg["threadId"]}, "labelIds": ["Label_Newsletters"]}]})

    deleted = summary["statement_jan"]
    del data["messages"][deleted]
    history_id += 1
    data["history"].append({"id": str(history_id), "messagesDeleted": [{"message": {"id": deleted}}]})

    acct = Account(data["emailAddress"], int(max(data["messages"]), 16), rng)
    acct.history_id = history_id
    acct.clock = BASE_TIME + timedelta(days=200)
    new_pdf = synthetic_pdf("statement 2026-07", rng)
    added = [
        acct.add(_headers(_with_attachment("Your statement for month 7 is attached.", new_pdf,
                                           "statement-2026-07.pdf", "pdf"),
                          "Example Bank <statements@bank.example.com>", HOLDER_A, "Statement for 2026-07",
                          acct.clock, "<stmt-07@bank.example.com>"), ["INBOX", "CATEGORY_UPDATES"],
                 record_history=True),
        acct.add(_headers(_text("Lunch tomorrow?"), "Morgan Test <morgan@friends.test>", HOLDER_A, "Lunch",
                          acct.clock, "<lunch@friends.test>"), ["INBOX", "CATEGORY_PERSONAL"],
                 record_history=True),
    ]
    data["messages"].update(acct.messages)
    data["attachments"].update(acct.attachments)
    data["history"].extend(acct.history)
    data["historyId"] = str(acct.history_id)
    path.write_text(json.dumps(data))
    summary.update({"deleted": deleted, "added": added, "relabeled": summary["newsletters"][:3]})
    (out / "fixture-summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def expire_cursor(out: Path, account: str = "account_a.json") -> None:
    """Make every stored history cursor too old, as Gmail does after about a week."""
    path = out / account
    data = json.loads(path.read_text())
    data["historyFloor"] = str(int(data["historyId"]) + 1)
    data["historyId"] = str(int(data["historyId"]) + 1)
    path.write_text(json.dumps(data))
