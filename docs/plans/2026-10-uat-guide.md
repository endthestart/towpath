# UAT guide: what was built October 16–17, and how to try it

Everything below is deployed on Hub (towpath.andermic.net) unless marked otherwise. Nothing has changed your mail:
Fastmail is in **Dry runs only**, there is no write password, and every change so far is a recorded dry run.
Decisions I made for you to confirm or reverse are listed in the
[autonomous build plan](2026-10-17-autonomous-build.md#decisions-made-for-review); open questions are there too.

## 1. Start here (10 minutes)

1. **Overview** (`/`): the Today cards: conversations waiting for your reply, life events and memories to review,
   On this day, photos read.
2. **Mail** (`/mail/`): Fastmail is managed; its whole history (114,924 messages) is read. Open *AI models for
   mail* (Mac Pro 35B for all three mail tasks, allowed to read mail text). The account's **Settings** page holds
   everything you set: what Towpath may change (all off, **Dry runs only** on), alerts, follow-ups, reply labels,
   the digest, notes for drafts, pausing.
3. **To reply** and **Awaiting reply**: are the conversations right? Mark any that aren't; your mark always wins.
   Open one: the whole conversation is on one page with its draft. Is the voice yours? Edit and **Save to Drafts**
   (a dry run: check the receipt on Mail). The Mail page's **Inbox** line shows what's in the inbox by category;
   each links to cleaning it up.

## 2. Mail management, one feature at a time

| Try | Where | What to look for |
| --- | --- | --- |
| Reader and changes | open any message; Archive, Mark read | a dry-run receipt saying what would happen |
| Senders | Mail → Senders | categories by people, newsletters, marketing…; change one (or tick several) and it sticks, and other senders from that company's domain follow (not gmail.com and the like) |
| Rules | Mail → Rules → a template | each template says how much inbox mail it would look at; preview, save; proposals appear under *Waiting for your answer*; approve per rule |
| Rules in your words | Mail → Rules → *Describe a rule* | e.g. "label receipts and archive them"; the model fills in the rule form; check, preview, save |
| Notes for drafts | Mail → Settings → *Drafting replies* | when you're free, phone number, sign-off; new drafts use them |
| Follow-up reminders | Mail → Settings → *Remind you to follow up* | after 3/7/14 days unanswered: a *Follow-up* label (dry run) and a drafted nudge under *Awaiting reply* |
| Attachments | Mail → Attachments | every file by kind, year and sender; search names; each message once |
| Filing documents | Mail → Attachments → *Filing documents* | a dry run lists what would be copied to `files/attachments/` on the NAS, and where; switch to *File them* when the list looks right |
| Purchases | Mail → Purchases | what you bought, by year and merchant, read from receipts (fills in slowly); try Ask "when did I buy …?" |
| Search operators | Mail → Search | `from:`, `in:Archive`, `after:2019`, `has:attachment`, `is:unread`, `larger:5MB` |
| Year pages | Life → Timeline → a year | month by month: photos, mail, events; busiest photo days; people first written to |
| Search | Mail → Search | full text, including archived mail |
| Space | Mail → Space | by category, year, folder, sender, largest |
| Clean up | Mail → Clean up | old marketing/newsletters: counts and examples; *Move to Archive* (dry run) |
| Archive to a file | Clean up → *Archive to a file…* | Mail → Archives: copies checked one by one; removal stays a dry run |
| Unsubscribe | Mail → Unsubscribe | one-click / email / page; *Asked* after you tick; dry-run receipts |
| Alerts and digest | Mail → Settings → *Telling you about mail* | turn on the digest; tomorrow 7am a dry-run receipt for the note |
| Statistics | Mail → Statistics | weekly volume, reply time, top correspondents |
| Keep from cloud | Senders, or a message | the mark shows; local models still read it |

**Going live** (only when you're happy): Connections → Fastmail → *Add a write password* (a second Fastmail app
password with full IMAP and SMTP access), then on Mail → Settings tick the kinds of change you want and press
**Turn dry runs off**. Start with *Mark as read*, *Flag* and *Archive*; leave *Move to Trash* and *Unsubscribe* for later.

## 3. Life story

1. **Life** (`/life/`): fill in **About you** (name, birthday). Fastmail is now allowed for the life story and
   *Find events by itself* is on (your answers of October 17): check what it proposes, newest year first. Allow a photo folder too:
   days with many photos become proposed events.
2. **2025 events to review** (Life → events to review): the re-read of 2025 finished on October 15 and left 16 of
   its 101 events for you to keep or reject.
3. **Pairs to settle** (Life → *pairs to settle*, when there are any): events worded alike on about the same day,
   or a few months apart; keep the right one or say both are right.
4. **Memories**: keep, rename or dismiss what Towpath gathered (people, decades, trips and occasions). Runs of
   photo days now come as one trip; flights, stays and tickets bought by mail come as events citing the receipt.
   Dated documents in allowed folders (a lease, a diploma, a deed) come as events citing the file.
5. **Export**: Your story → *Download everything Towpath knows*: a zip of plain JSON and Markdown (events,
   memories, people, rules, sender categories, purchases, decisions; no mail text or passwords).
   *Restore your decisions* reads one back: upload it to see what it would add, then *Restore*. It only adds, and
   never brings back permissions, model choices or mail switches; rules return turned off.
   **Family edition**: on Your story choose *Share* beside memories and events you'd show family, then open
   the *family edition* (print or Markdown): only those, with no sources, excerpts, photos or ages.
6. **Write down a memory**: a few of your own; they appear on the timeline as remembered.
7. **Timeline**, **On this day**, **Your story** (print or download as Markdown), **Ask** (try "When did I first go
   to …?").
8. **People** (`/people/`): a person's mail by year, conversations and events, and the days you messaged them
   (once the Messages export is added).

## 4. Photos and files

- **Photos** (`/photos/`): cameras and years from 40,000+ photos read; try "photos from my Fuji in 2019" in the
  plain-English box. Small previews appear as they're made (each photo's own camera thumbnail; none for the private
  export or photos hidden in Apple Photos; turn them off on the page). Few of your photos record a location.
  *Save as a collection* keeps a search (yours or a plain-English one); its collection lists what it finds each
  time, with previews, and never a hidden or private photo.
- **Search all**: `in:"root/folder"`, `size:>100MB`, `modified:2019`; or ask in plain English. Words in your
  Messages and Facebook conversations are found too, once those exports are added.
- **Space**: duplicate groups now say whether copies are identical by content.

## 5. Waiting on you (not yet connected)

| What | How |
| --- | --- |
| Gmail over IMAP (milestone 6), to compare with Inbox Zero | Connections → Gmail (IMAP), with a Google app password |
| Fastmail contacts and calendars | Connections → Fastmail contacts and calendars, a read-only CardDAV/CalDAV app password |
| iCloud contacts and calendars (if you use them) | Connections → iCloud contacts and calendars, an app-specific password |
| Facebook export | unzip a JSON export under the library; Connections → Facebook export |
| Apple Messages | on a Mac: `brew install imessage-exporter`; give Terminal *Full Disk Access* (System Settings → Privacy & Security); then `imessage-exporter -f txt -o /Volumes/storage/exports/messages` (the `storage` share, which Towpath sees as `storage/exports/messages` in its library). Then Connections → *Apple Messages export* → that folder → *Check and add*, and *Index now*. Run the export again now and then. Allow it on the Life page only if you want messages in your life story. |


## 6. Rolling back

Every release added stores and tables only. To go back, put an earlier pair of digests from
[Hub](../setup/hub.md) into the Arcane project's environment and redeploy; no data change is needed.
