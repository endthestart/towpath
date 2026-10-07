# Connections in the UI

Status: **accepted 2026-10-06**. Phase 1 (Fastmail/IMAP from the UI) accepted 2026-10-07: an account added
entirely in the browser indexed every message in its chosen folders (counts matched the server's per folder),
in resumable slices; a later check read only new mail; full-text search ran through the worker; unread
counts were unchanged, and the provider-side app password was IMAP-only and read-only. Phase 2 (Gmail)
accepted 2026-10-07: the configured connection was imported unchanged and an incremental check ran from the
page within its pacing. Phase 3 (shell-free install) implemented, awaiting owner acceptance.

Implements [R3](../decisions.md#recommendations-in-this-design) and [components](../components.md#services) as
designed, replacing the interim file-and-shell setup used for the first Hub deployment.

## Goal

A person sets Towpath up the way they set up any self-hosted app: create one folder for its data, start the
containers, open the site, and do everything else in the browser. Adding Fastmail, connecting Gmail, choosing
folders, starting and watching indexing, replacing a password and disconnecting an account are all pages,
not files, scripts or SSH sessions. The same flow works for every instance and every user.

## What stays true

- **The web service never sees a credential.** Passwords and OAuth tokens are typed into, and returned to, pages
  served by `towpath-connect`, which stores them in its own secret folder. The web service shows status only.
- **Read-only.** Mail access stays EXAMINE/BODY.PEEK for IMAP and `gmail.readonly` for Gmail.
- **Nothing runs on its own.** Indexing starts when the owner clicks *Start indexing* and can be paused. The
  connector still never schedules syncs, crawls or model calls by itself.
- **One login.** Connection pages require the same single account as the rest of the UI.

## How it fits together

```
browser ── https://towpath.example.org ── reverse proxy ─┬─ /connections/…  → towpath-connect (setup pages)
                                                          └─ everything else → towpath-web
```

- `towpath-connect` gains a small setup web server beside its request worker. It serves only `/connections/…`
  and checks the same signed session cookie as the web service, so there is one sign-in.
- Non-secret connection settings (provider, address, host, chosen folders, status) live in a connections
  store that both services read. Secrets live only in the connector's secret folder, one file per connection,
  mode 0600. A secret is never displayed again; it can only be replaced or deleted.
- The connector reads configured connections at each poll, so a new connection needs no restart.

## The flows

**Add Fastmail (or another IMAP account).** *Connections → Add account → Fastmail.*
1. The page explains, with a direct link, how to make an app password in Fastmail and which access to choose.
2. The person enters their address and pastes the password. Server, port and TLS are filled in for Fastmail;
   *Other IMAP* shows those fields.
3. *Test connection*: the connector logs in, lists folders and confirms that EXAMINE opens them read-only. The
   page shows the folders with message counts, or a plain error (“Fastmail rejected the password”).
4. The person ticks folders to include. Trash, Spam/Junk and Drafts start unticked.
5. *Save*, then *Start indexing*. A progress panel shows messages indexed, folders done, rate and any stop
   reason; *Pause* and *Resume* work across restarts (the sync engine already checkpoints).

**Connect Gmail.** *Connections → Add account → Gmail.*
1. Google requires each self-hoster to have their own OAuth client ([D14](../decisions.md#d14-gmail-access-for-other-self-hosters)). A
   one-time guide walks through creating a *Web application* client in Google Cloud and shows the exact
   redirect URI to paste (`https://<site>/connections/google/callback`). The person pastes the client ID and
   secret into the connector's page.
2. *Connect Gmail* goes to Google's consent screen for `gmail.readonly` only, and back to Towpath. The
   connector exchanges the code and stores the refresh token. A broader scope is refused.
3. Pacing: the existing safe default applies. The verified-quota opt-in (30% of the Cloud Console figure,
   1,800-unit ceiling) becomes a field on the connection's settings page, with the same validation.
4. Folders and indexing as for Fastmail.

**Existing Gmail connection.** On first start of the new version, the connector imports the current
configuration file's sources, client file and token into the connections store and its secret folder,
keeping the source ID, pacing, quota history and index. No re-consent and no re-indexing.

**Manage.** Each connection's page shows status, last indexed, counts, folders, pacing; *Replace password*,
*Reconnect*, *Change folders*, *Pause/Resume*, and *Disconnect* (removes the secret; the index stays unless
the person also chooses *Delete index*). It links to the provider's page for revoking access.

## Installing without a shell

- One data folder per instance (for example a TrueNAS dataset created with the Apps preset). The containers
  run as `TOWPATH_USER`, by default TrueNAS's `apps` user (568), the owner's choice on 2026-10-07 over a
  dedicated user: simpler setup, at the cost that other apps running as 568 can read Towpath's stored
  credentials. They create `state/`, `credentials/`, `index/` and `scratch/` inside it on first start, with
  correct modes. No hand-made subfolders, configuration files or ownership fixes ([install guide](../setup/install.md)).
- Compose and an example env file in the repository; the only values a person sets are the data folder, the
  public URL and the proxy network. Everything else has a default or a page.
- First run: the site shows account setup (with the code from the container log), then an empty Connections
  page.

## Phases

Each phase ships through CI, is deployed by digest, and is accepted before the next starts.

1. **Fastmail from the UI.** Connections store, connector setup server, proxy route, IMAP add/test/folders,
   start/pause/progress for indexing. Acceptance: the owner adds Fastmail entirely in the browser; flags and
   unread counts unchanged; a re-run is incremental; full-text search works.
2. **Gmail from the UI.** OAuth web flow, import of the existing connection, pacing settings, manage page.
   Acceptance: the existing index keeps working with no re-consent; a fresh connection works end to end on
   synthetic or test credentials.
3. **Self-initialising install.** Data-folder layout created by the containers; configuration files become
   optional; install guide with no shell steps.

## Folders on the server (2026-10-07)

*Connections → Folders on this server* lists folders under the read-only library mount two levels deep
(unreadable ones are shown and explained). The chosen folders become search-granted roots; the connector
writes the Recoll configuration (media by name only, common junk skipped), runs `recollindex` at low CPU and
idle I/O priority with progress from Recoll's status file (counts only, never file names), stops it on Pause,
and when it finishes lists the index into the files store page by page (5,000 rows per page, one run per
folder, absence recorded only from a complete listing). File contents are searched through Recoll by the
request worker, like Gmail and IMAP full-text search.

## Later connection types (owner request, not scheduled)

- **Calendars (CalDAV):** past events as dated evidence for the life stream (who, when, where), read-only.
- **Contacts (CardDAV):** names for addresses in mail, messages and other sources, as identity hints, never merged
  automatically ([R5](../decisions.md#recommendations-in-this-design)).

Both fit the same page and storage: Fastmail offers per-protocol app passwords (CalDAV, CardDAV) with a read-only
option, like the IMAP password used in phase 1.

## Decisions (owner, 2026-10-06)

1. Setup pages are served by `towpath-connect` behind the same hostname; the web service never receives a secret (R3).
2. Phases in the order above: Fastmail first.
3. *Delete index* is deferred; phase 1 *Disconnect* removes the secret and keeps the index, labelled disconnected.
