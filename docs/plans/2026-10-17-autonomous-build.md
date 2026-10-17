# Plan: autonomous build, October 17–19, 2026

Status: agreed with the owner on 2026-10-17 (six answers). The owner is away from a computer; full UAT follows when
they are back. This plan continues the [mail management plan](2026-10-16-native-mail-management.md) and widens
the scope to the rest of Towpath.

## Working agreement

- **Build autonomously** across Towpath, not only Inbox Zero parity. One task at a time, no subagents.
- **Release without asking** whenever a piece is finished and its tests pass. Deploy only while indexing is idle,
  add new stores rather than changing existing ones where possible (so a rollback needs no data change), check the
  pages and the worker after each deployment, roll back if they fail, and record each deployment in
  [Hub](../setup/hub.md). The earlier "one release a day" rule is withdrawn.
- **Mail stays in dry runs.** Fastmail keeps *Dry runs only* on; nothing changes mail or reaches outside it until
  the owner turns it off at UAT.
- **Models:** mail management may send all of Fastmail's text to the models, as fast as they allow. Test the Hub
  4B and Mac Pro 35B (through Poundlock, with the token Towpath already stores) with made-up prompts first; the
  owner allowed recording content grants for both on their behalf. Carbonio is busy with another project.
- **Archives** (archive-then-delete) are written under `archive/mail/` in Towpath's data folder.
- **Open questions** are collected below. When six are open, stop and ask them one at a time, each with a
  recommendation, then continue.
- Keep disk use in check on the Mac: one virtual environment, scratch files deleted when done.

## Order

Balanced across three areas, mail first:

1. Model viability and the models mail management uses (Hub 4B, Mac Pro 35B).
2. Mail milestone 2: reply tracking and drafts.
3. Mail milestone 3: categories, rules, learned senders, cold-email check.
4. Mail milestone 4: cleanup, archive to mbox then Trash, unsubscribe, Clean.
5. Mail milestone 5: alerts, digest, statistics, attachment filing.
6. Life story and *Ask my life*: timeline across mail and files, people from correspondence, periods, cited
   answers.
7. Photo metadata (EXIF) search and natural-language search through a local model.
8. More sources: Fastmail contacts and calendars (CardDAV/CalDAV); import how-tos for Apple and Facebook exports.
9. Mail milestone 6: Gmail over IMAP (built against the synthetic server; connecting it waits for the owner).

## Open questions

None yet.

## Decisions made for review

1. **The web service brings its own stores up to date at startup** (add-only, as the connector already does for
   its stores), instead of refusing to start until `towpath stores upgrade` runs. Stores other services write are
   never changed by it. This lets new tables ship without a manual step on Hub.
2. **Mail work uses the Mac Pro 35B.** On made-up conversations it judged 3 of 4 correctly (about 3.6 s each once
   loaded); the Hub 4B judged 1 of 4 and isn't used or granted for mail. Carbonio isn't used.
3. **Poundlock offers Towpath's token cloud models too** (Claude, frontier). Mail features are bound only to local
   models; a further model added to a connection is labelled as running where the connection does unless chosen
   otherwise, so a cloud model name must be labelled as cloud when added.
4. **Drafts stay in Towpath** until the owner saves one to Drafts (Inbox Zero saves them automatically). Saving is
   a `draft` change, so it follows dry runs and the per-type switch.
5. **Reply tracking rules:** mailing-list and automated mail is FYI without the model; a conversation the owner
   ended over 60 days ago is *done* by rule; conversations older than two years are *done* without asking the
   model; everything else with a person goes to the model, newest first.
6. **Twelve categories** for senders: people, newsletters, marketing, notifications, receipts and orders, banking
   and bills, travel, accounts and security, social networks, calendar, cold email, other. A sender has one
   category; account-security mail and calendar invitations are also recognised per message.
7. **Rules propose; automatic is opt-in per rule** and limited to reversible changes. Switching a rule to automatic
   also carries out the proposals of that version still waiting. Changing a rule's conditions or actions makes a new
   version, which looks at mail again; switching it on, off or to automatic doesn't.
8. **Rule patterns:** text without `*` matches anywhere ignoring case; with `*` it's a wildcard over the whole
   value. A rule with a condition in words waits (rather than skipping mail) while no model may read mail text.
9. **A message Towpath can't read** (malformed headers) is kept with its identity and the problem named, so one
   message can't stop reading again (this stopped reading Fastmail at 55,650 messages on 2026-10-17).

## Progress

- 2026-10-17: milestone 1 deployed (`6147799`); Fastmail management on; reading the whole history.
- 2026-10-17: model check (decision 2); further models per connection and a model per task; the account-wide dry
  runs switch; milestone 2 built (conversations, reply drafts).
- 2026-10-17: fixed reading stopping on an 8-bit header (`75f606a`, deploying); milestone 3 built (senders,
  categories, rules with templates, batch approval, automatic rules); the connector now updates the worker's stores
  at start too.
