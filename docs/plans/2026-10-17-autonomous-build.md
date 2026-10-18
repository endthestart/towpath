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
- **Open questions** are collected below and sent as they come up, each with context, options and a
  recommendation, to the owner's Project Manager session, which asks the owner one at a time and sends the answers
  back (changed October 17: no longer held until six are open). Building continues meanwhile.
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

## Next items (October 17, after the plan was built)

The owner chose to keep building until UAT. From the [roadmap](../roadmap.md)'s unbuilt parts, one at a time:

1. **Previews on the Year pages and in Your story**: a few previews for each of the year's busiest photo days.
2. **Export package**: download everything Towpath has learned and decided (life story, memories, recollections,
   people, rules, sender categories, purchases, decisions) as one archive of plain JSON and Markdown, readable
   without Towpath (roadmap milestone 8, "portable export").
3. **Contradictions in the life story**: events that disagree (the same thing on different dates, or duplicates
   from different sources) shown side by side for the owner to settle (roadmap milestone 6).
4. **Messages as a source**: built (the owner chose imessage-exporter's text format): one item per conversation
   per day in the owner's local time, the same protections as mail, tested only on made-up conversations. The owner
   runs the export at UAT (see the guide).

Then, from the roadmap's life-story and sources milestones:

5. **Trips from photos**: runs of days with many photos (in folders allowed for the life story) gathered into one
   proposed trip memory, instead of one event per day. Built (`96cc6f5`).
6. **Bookings as evidence**: purchases that are flights, hotels or tickets (read from receipts) proposed as dated
   life events, citing the receipt, once the account is allowed for the life story. Built (`379d6da`).
7. **Messages in Search all and People**: the Apple Messages export searchable from Search all, and a person's
   conversations shown on their People page. Built (`a53f6c8`).

Then, from the roadmap's later milestone and the owner's search ideas:

8. **Restore from an export package**: load the owner's own decisions (reviews, recollections, memories kept or
   renamed, rules, sender categories, settings) from an export archive into a fresh Towpath, shown first as a dry
   run of what would be added, never overwriting a newer decision (roadmap milestone 8, "portable export and
   restore"). Built: Your story → *Restore your decisions* (add-only; permissions, model choices and mail
   switches never come back from a file; rules return turned off).
9. **Save a photo search as a collection**: the Photos page's filters, or a plain-English search, saved as a
   collection that keeps the search and lists what it finds (owner idea: natural-language search "can be saved as
   a collection"); hidden and private photos stay out as everywhere else. Built: Photos → *Save as a collection*.
10. **Documents as life evidence**: built. Dated documents in folders allowed for the life story (a lease, a
    diploma, a deed, an offer letter) proposed as events citing the file, found and dated by their names and year
    folders alone: no model and nothing inside a file is read (stricter than first planned), never identity papers
    or the private export.

## Open questions

1. ~~**Attachment filing**~~ — answered October 17: a folder in Towpath's data folder, by year and sender,
   documents only, dry runs first; which file types count and how attachments connect to other things come later.
   Built: Mail → Attachments → *Filing documents*.
2. ~~**Fastmail for the life story and Ask**~~ — answered October 17: allowed (the Mac Pro model only; keep-from-cloud
   marks stay protected). Granted on Hub, recorded as the owner's answer relayed by the Project Manager session.
3. ~~**Finding life events by itself**~~ — answered October 17: start now, Fastmail first (stored text, no Gmail
   API use), then Gmail, the current year first. Turned on on Hub; accounts with stored text now always come first.
4. ~~**Photo previews**~~ — answered October 17: yes, optional and on by default, as long as no cloud model reads
   any photo; never for the private export or photos hidden in Apple Photos (by the library's own hidden flag).
   Built with a test for each rule.

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
10. **Archive files** are `archive/mail/<account>/<request>.mbox` (mboxrd) in the data folder, as agreed. The web
    service sees only `state/`, so the pages show where a file is but can't offer it as a download yet.
11. **Unsubscribing** uses one-click where offered, else email over Fastmail's SMTP with the write password; a
    sender offering only a web page gets a link to open yourself. Unsubscribing never runs from a rule.
12. **Clean-up defaults:** newsletters, marketing, notifications and social mail older than 30 days in the inbox.
13. **Mail search is its own index** (full text over Towpath's copy of each message) on the Mail pages; joining it
    to *Search all* comes with the life-story work.
14. **Alerts and digest:** alerts mark only mail that arrives after they're turned on; the digest is written at 7am
    Chicago time (the owner's time zone is assumed), daily or on Mondays, and put in the inbox from "Towpath digest"
    at the owner's own address.
15. **Finding events by itself** goes a year at a time, newest first, per account, and rechecks the current year
    every ten minutes; it waits behind a period the owner asked for explicitly.
16. **Memories:** a person needs three or more events to get a memory; chapters are decades of age; the model
    groups each month's events with ten days either side; the life-story, grouping and Ask tasks run on the Mac
    Pro 35B on Hub (Carbonio is busy), logged as the owner's chat permission.
17. **Photo search reads headers only** (ExifTool `-fast2`), stores camera, lens, when and where, and never reads
    anything under `Exports/Hidden` (the owner's private export), not even metadata. No thumbnails: the web service
    can't see the library. A plain-English request is turned into filters by a local model that sees the request
    and the camera and lens names, never a photo.
18. **Duplicates are confirmed by content** only for the Space page's top groups (largest waste first), reading
    each candidate once; groups touching the private export are shown as not compared.
19. **Mail from companies and services is FYI without the model.** A conversation whose latest message is from a
    sender categorized as notifications, receipts, banking, travel, accounts, social, calendar, newsletters or
    marketing is FYI by rule; calling the sender a person hands it back to the model.
20. **Senders are sorted eight to a model call**; any the model leaves out are asked about alone.
21. **Purchases** are read from receipt senders' mail, or mail with an order-like subject and an amount in the
    text, never shipping notices; 30 seconds of model time per poll, after conversations and senders.
22. **Mailbox labels** are `To-reply`, `Awaiting-reply` and `Follow-up` (IMAP keywords can't hold spaces).
    Follow-ups and reply labels apply to conversations from the month before you turn them on, and everything
    after.
23. **iCloud contacts and calendars** are found the standard way (principal, then home sets); a home on another
    service than the one asked is refused.
24. **Duplicates over 2 GB are compared by samples** (the first, middle and last 64 MB, with the size), and the
    Space page says so: hashing a 77 GB file in full held up the connector for minutes each poll.
25. **Unsubscribing by email goes only to the sender's own domain** (or its List-Id's); otherwise only their page
    is offered. The address is shown before you tick.
26. **A move into a Trash folder counts as Move to Trash**: it needs that switch on and your own approval, never a
    rule's automatic one.
27. **The private photo export is left out of the catalog and Recoll entirely**, not just skipped by the photo
    and life features: it no longer appears in Search all or Space, and Space's totals don't count it.
28. **Taking a source's life-story permission away takes its events out** of every page, answer and prompt; On
    this day, the Year pages and the timeline's yearly figures use allowed sources only, and a digest holds only
    its own account's mail.
29. **Photo previews reverse decision 17** (as the owner chose): each photo's embedded camera thumbnail is kept in
    a store of its own. Apple Photos libraries are judged by their own database: only originals the library shows
    get a preview; hidden or deleted photos, the library's own copies, and every file of a library whose database
    can't be read get none, and hidden photos are also left out of photo lists and counts.

## Progress

- 2026-10-17: milestone 1 deployed (`6147799`); Fastmail management on; reading the whole history.
- 2026-10-17: model check (decision 2); further models per connection and a model per task; the account-wide dry
  runs switch; milestone 2 built (conversations, reply drafts).
- 2026-10-17: fixed reading stopping on an 8-bit header (`75f606a`, deploying); milestone 3 built (senders,
  categories, rules with templates, batch approval, automatic rules); the connector now updates the worker's stores
  at start too.
- 2026-10-17: milestone 4 built (mail search, space, clean-up, archives verified before removal, unsubscribe).
- 2026-10-17: `75f606a` deployed (reading resumed past the 8-bit header); the Mac Pro 35B added on Hub, chosen for
  the three mail tasks and allowed to read mail text, logged as the owner's chat permission. Milestone 5 built
  (alerts, digest, statistics); attachment filing waits on open question 1.
- 2026-10-17: life story layers built: finding events by itself, memories, profile, timeline, Ask.
- 2026-10-17: `a6b182e` deployed (mail milestones 2–5). Photo search built (EXIF fields, Photos page,
  plain-English requests).
- 2026-10-17: mail milestone 6 built: Gmail over IMAP (preset, All Mail and Inbox, Gmail threads and labels, safe
  archive and Undo). Running it next to Inbox Zero waits for the owner to add a Gmail app password.
- 2026-10-17: contacts and calendars built (Fastmail CardDAV/CalDAV, read-only); connecting them waits for an app
  password. The photo-search release failed CI (ExifTool missing on the CI runner); fixed so photo reading skips
  where ExifTool isn't installed.
- 2026-10-17: days with many photos as proposed life events (folders allowed on the Life page); photo links fixed
  to open the file's evidence page.
- 2026-10-17: Your story page: only what the owner kept, by decade of life, with sources; Markdown download.
- 2026-10-17: recollections (the owner's own memories, in their words, on the timeline and in Ask).
- 2026-10-17: duplicate candidates on the Space page confirmed by content (SHA-256 of the largest groups, never the
  private export); `3d63e3f` deployed.
- 2026-10-17: People pages (correspondents and contacts, mail by year, conversations, life events); Overview
  Today cards.
- 2026-10-17: Search all filters by folder, size and month modified; plain-English search (the model writes the
  typed query, which is checked before it runs; the owner sees and can edit it).
- 2026-10-17: keep-from-cloud marks for senders and messages (ADR 0002 point 7); On this day; Search all matches
  message text; per-sender clean-up links.
- 2026-10-17: Facebook export source built (posts, day-by-day conversations); how-tos for Facebook, Apple and
  Google data in [other sources](../setup/other-sources.md).
- 2026-10-17: a category the owner gives one sender is learned for its domain (not for shared domains like
  gmail.com); the owner's conversation marks are examples for the model; Ask cites file names in folders allowed for
  the life story. Rules can be described in plain English and drafted by the model for the owner to check.
- 2026-10-17: `131fabe` deployed. Follow-up reminders (a label and a drafted nudge after 3, 7 or 14 days
  unanswered), notes for drafts, the Attachments page, mail search operators (`from:`, `in:`, `after:`,
  `has:attachment`, `is:unread`, `larger:`…), and photo dates outside the years photography existed dropped.
- 2026-10-17: `8c42865` and `4ff5d30` deployed. Reply labels in the mailbox (To-reply, Awaiting-reply); an Inbox Zero
  [parity checklist](2026-10-17-inbox-zero-parity.md); Year pages; Ask cites attachments; conversations from
  companies and services settled as FYI without the model (on Hub *To reply* by rule fell from 989 to 249); senders
  sorted eight to a model call (about twice as many an hour). Then: purchases from receipts, plain-English mail
  search, a whole-conversation page, per-account mail Settings, iCloud contacts and calendars.
- 2026-10-17: `1a9f7c5` deployed (purchases are being read on Hub). Since: inbox by category on the Mail and
  Overview pages, the Accounts page, bulk sender categories, template coverage on Rules, 90-day statistics,
  purchases in the digest, Ask answers about a year, people in Search all, flagged mail never cleaned up, owner-asked
  work between model slices, safe form redirects, and huge duplicate files compared by samples (they had stalled
  each connector poll for minutes). A visual pass on synthetic data fixed a Mail page failure when serving a bare
  stores folder, and the newer pages' forms.
- 2026-10-17: `b310977` deployed. A code review of the day's riskier changes found eleven problems, all fixed in
  `d05ca6f`: the contacts-and-calendars password could follow a server's pointer to another host; Ask didn't apply
  keep-from-cloud marks; labels put back after removal were dropped; follow-up labels stayed after reminders were
  turned off; some forms failed after acting; purchases could be read twice; one refusal stalled drafts or purchases;
  sampled duplicates counted as exact.
- 2026-10-17: `d05ca6f` deployed. A second review, of the code that changes or removes mail, found eight problems,
  fixed in `84e4283`: a move into Trash bypassed the Trash switch; unsubscribing could reach a private address after a
  second DNS lookup, count a redirect as done, email any address a header named, and jam the queue on a bad
  address; archiving the same mail twice crashed the connector; a Trash approval didn't re-check its copy or target;
  a dropped connection could repeat a change; Gmail labels were missed for Undo.
- 2026-10-17: `84e4283` deployed. A privacy review found the private export could still be listed by name (Search
  all, Space) and indexed by Recoll, life-story model calls didn't name their senders, and revoked or ungranted
  accounts still fed the life pages and the digest; all fixed in `1d06040` (decisions 27 and 28).
- 2026-10-17: `1d06040` deployed. The owner answered all four open questions through the Project Manager session:
  attachment filing built (`44d310e`, dry run first); Fastmail allowed for the life story and finding events turned
  on, on Hub; photo previews built (decision 29).
- 2026-10-17: `ffae7c4` deployed. On Hub the Apple Photos check read the main library (91,083 files: about 18,500
  visible originals, 9,567 photos hidden or deleted) and left out every file of the libraries it couldn't read
  (mostly libraries' own copies in backups); 137 previews were made in the first pass. Finding life events by
  itself is reading Fastmail's 2026 mail.
- 2026-10-17: `a53f6c8` deployed (items 5–7: trips from photos, bookings from receipts, Messages in Search all and
  on People pages); all 34 pages answered on Hub. Items 8–10 built and under review before release.
