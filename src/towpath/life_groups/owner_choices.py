"""The owner's profile and decisions on memories, and reading memories for the pages."""

import json
from contextlib import closing
from datetime import date

from towpath.life_groups.common import GROUP_KEY, KINDS, PROFILE_KEY, PROFILE_TARGET, now
from towpath.stores import open_store, store_path


def profile(store_dir, role: str = "web") -> dict:
    if not store_path(store_dir, "decisions").exists():
        return {}
    with closing(open_store(store_dir, "decisions", role)) as db:
        row = db.execute("SELECT value FROM settings WHERE target_id = ? AND key = ?",
                         (PROFILE_TARGET, PROFILE_KEY)).fetchone()
    return json.loads(row[0]) if row else {}


def set_profile(store_dir, name: str, birthday: str | None, author: str = "owner") -> dict:
    if birthday:
        date.fromisoformat(birthday)
    value = {"name": name.strip()[:80] or None, "birthday": birthday or None}
    with closing(open_store(store_dir, "decisions", "web")) as db:
        db.execute("INSERT OR REPLACE INTO settings (target_id, key, value, author, at) VALUES (?,?,?,?,?)",
                   (PROFILE_TARGET, PROFILE_KEY, json.dumps(value, sort_keys=True), author, now()))
        db.commit()
    return value


def decisions(store_dir, role: str = "web") -> dict[str, dict]:
    """The owner's decisions on groups: kept ones (with their snapshot) and dismissed ones."""
    if not store_path(store_dir, "decisions").exists():
        return {}
    with closing(open_store(store_dir, "decisions", role)) as db:
        return {t.split(":", 1)[1]: json.loads(v) for t, v in db.execute(
            "SELECT target_id, value FROM settings WHERE key = ? AND target_id LIKE 'group:%'", (GROUP_KEY,))}


def decide(store_dir, group: dict, state: str, author: str = "owner", **changes) -> dict:
    """Keep, dismiss or edit a group. Keeping snapshots its kind, title, period and members."""
    if state not in ("kept", "dismissed"):
        raise ValueError("a group is kept or dismissed")
    value = {"state": state, "kind": changes.get("kind") or group["kind"],
             "title": (changes.get("title") or group["title"]).strip()[:80],
             "members": sorted(set(changes["members"] if "members" in changes else group["members"])),
             "proposed_members": sorted(group.get("proposed_members", group["members"])),
             "by": author, "at": now()}
    with closing(open_store(store_dir, "decisions", "web")) as db:
        db.execute("INSERT OR REPLACE INTO settings (target_id, key, value, author, at) VALUES (?,?,?,?,?)",
                   (f"group:{group['group_id']}", GROUP_KEY, json.dumps(value, sort_keys=True), author, value["at"]))
        db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES ('group-review', ?, ?, ?, ?)",
                   (f"group:{group['group_id']}", json.dumps({"state": state, "title": value["title"]}), author,
                    value["at"]))
        db.commit()
    return value


def undecide(store_dir, group_id: str) -> None:
    with closing(open_store(store_dir, "decisions", "web")) as db:
        db.execute("DELETE FROM settings WHERE target_id = ? AND key = ?", (f"group:{group_id}", GROUP_KEY))
        db.commit()


# -- reading groups for pages -------------------------------------------------------------------------------------

def _period(claim_rows: dict, members: list[str]) -> tuple[str | None, str | None]:
    dates = [claim_rows[m] for m in members if m in claim_rows and claim_rows[m][0]]
    if not dates:
        return None, None
    return min(d[0] for d in dates), max(d[1] or d[0] for d in dates)


def groups(store_dir, role: str = "web") -> list[dict]:
    """Every group as the owner sees it: kept groups as kept, proposals not already covered by a decision, each with
    its members and period, newest first. Dismissed ones are marked, not dropped."""
    found = decisions(store_dir, role)
    proposed = []
    spans = {}
    if store_path(store_dir, "life").exists():
        with closing(open_store(store_dir, "life", role)) as db:
            proposed = [dict(r) for r in db.execute("SELECT * FROM groups")]
            spans = {r[0]: (r[1], r[2]) for r in db.execute("SELECT claim_id, earliest, latest FROM claims")}
    out, covered = [], []
    for gid, d in found.items():
        g = {"group_id": gid, "kind": d["kind"], "title": d["title"], "members": d["members"], "state": d["state"],
             "summary": None}
        covered.append(set(d.get("proposed_members") or d["members"]))
        out.append(g)
    by_id = {g["group_id"]: g for g in out}
    for p in proposed:
        members = json.loads(p["members"])
        if p["group_id"] in by_id:
            kept = by_id[p["group_id"]]
            kept["summary"] = p["summary"]
            kept["new_members"] = sorted(set(members) - set(kept["members"]) -
                                         set(found[p["group_id"]].get("proposed_members", [])))
            continue
        if any(len(set(members) & c) >= max(1, len(members) // 2) for c in covered):
            continue  # mostly a group the owner already decided on
        out.append({"group_id": p["group_id"], "kind": p["kind"], "title": p["title"], "summary": p["summary"],
                    "members": members, "state": "proposed"})
    for g in out:
        g["earliest"], g["latest"] = _period(spans, g["members"])
        g["label"] = KINDS.get(g["kind"], g["kind"])
    return sorted(out, key=lambda g: (g["earliest"] or ""), reverse=True)


def get(store_dir, group_id: str, role: str = "web") -> dict | None:
    return next((g for g in groups(store_dir, role) if g["group_id"] == group_id), None)
