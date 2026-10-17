# UAT guide: what was built October 16–17, and how to try it

Everything below is deployed on Hub (towpath.andermic.net) unless marked otherwise. Nothing has changed your mail:
Fastmail is in **Dry runs only**, there is no write password, and every change so far is a recorded dry run.
Decisions I made for you to confirm or reverse are listed in the
[autonomous build plan](2026-10-17-autonomous-build.md#decisions-made-for-review); open questions are there too.

## 1. Start here (10 minutes)

1. **Overview** (`/`): the Today cards: conversations waiting for your reply, life events and memories to review,
   On this day, photos read.
2. **Mail** (`/mail/`): Fastmail is managed; its whole history (114,924 messages) is read. Check the model section
   (Mac Pro 35B for all three mail tasks, allowed to read mail text). The **What Towpath may change** switches are
   all off and **Dry runs only** is on.
3. **To reply** and **Awaiting reply**: are the conversations right? Mark any that aren't; your mark always wins.
   Open one with a draft: is the voice yours? Edit and **Save to Drafts** (a dry run: check the receipt on Mail).

## 2. Mail management, one feature at a time

| Try | Where | What to look for |
| --- | --- | --- |
| Reader and changes | open any message; Archive, Mark read | a dry-run receipt saying what would happen |
| Senders | Mail → Senders | categories by people, newsletters, marketing…; change one and it sticks, and other senders from that company's domain follow (not gmail.com and the like) |
| Rules | Mail → Rules → a template | Preview on recent mail; save; proposals appear under *Waiting for your answer*; approve per rule |
| Rules in your words | Mail → Rules → *Describe a rule* | e.g. "label receipts and archive them"; the model fills in the rule form; check, preview, save |
| Notes for drafts | Mail → *Drafting replies* | when you're free, phone number, sign-off; new drafts use them |
| Follow-up reminders | Mail → *Remind you to follow up* | after 3/7/14 days unanswered: a *Follow-up* label (dry run) and a drafted nudge under *Awaiting reply* |
| Attachments | Mail → Attachments | every file by kind, year and sender; search names; each message once |
| Search | Mail → Search | full text, including archived mail |
| Space | Mail → Space | by category, year, folder, sender, largest |
| Clean up | Mail → Clean up | old marketing/newsletters: counts and examples; *Move to Archive* (dry run) |
| Archive to a file | Clean up → *Archive to a file…* | Mail → Archives: copies checked one by one; removal stays a dry run |
| Unsubscribe | Mail → Unsubscribe | one-click / email / page; *Asked* after you tick; dry-run receipts |
| Alerts and digest | Mail → *Telling you about mail* | turn on the digest; tomorrow 7am a dry-run receipt for the note |
| Statistics | Mail → Statistics | weekly volume, reply time, top correspondents |
| Keep from cloud | Senders, or a message | the mark shows; local models still read it |

**Going live** (only when you're happy): Connections → Fastmail → *Add a write password* (a second Fastmail app
password with full IMAP and SMTP access), then on Mail tick the kinds of change you want and press **Turn dry runs
off**. Start with *Mark as read*, *Flag* and *Archive*; leave *Move to Trash* and *Unsubscribe* for later.

## 3. Life story

1. **Life** (`/life/`): fill in **About you** (name, birthday). Decide open questions 2 and 3 (Fastmail and finding
   events by itself) — the Life page's account switches and *Find events by itself* do it. Allow a photo folder too:
   days with many photos become proposed events.
2. **Memories**: keep, rename or dismiss what Towpath gathered (people, decades, trips and occasions).
3. **Write down a memory**: a few of your own; they appear on the timeline as remembered.
4. **Timeline**, **On this day**, **Your story** (print or download as Markdown), **Ask** (try "When did I first go
   to …?").
5. **People** (`/people/`): a person's mail by year, conversations and events.

## 4. Photos and files

- **Photos** (`/photos/`): cameras and years from 40,000+ photos read; try "photos from my Fuji in 2019" in the
  plain-English box. Few of your photos record a location, so place search has little to go on.
- **Search all**: `in:"root/folder"`, `size:>100MB`, `modified:2019`; or ask in plain English.
- **Space**: duplicate groups now say whether copies are identical by content.

## 5. Waiting on you (not yet connected)

| What | How |
| --- | --- |
| Gmail over IMAP (milestone 6), to compare with Inbox Zero | Connections → Gmail (IMAP), with a Google app password |
| Fastmail contacts and calendars | Connections → Fastmail contacts and calendars, a read-only CardDAV/CalDAV app password |
| Facebook export | unzip a JSON export under the library; Connections → Facebook export |
| Attachment filing | open question 1 |

## 6. Rolling back

Every release added stores and tables only. To go back, put an earlier pair of digests from
[Hub](../setup/hub.md) into the Arcane project's environment and redeploy; no data change is needed.
