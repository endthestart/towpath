# Local email UI

The optional Django interface reads an existing email metadata index. It gives you an overview, a paginated email list, literal search of subjects, senders and attachment names, Inbox/Sent/attachment filters, and individual metadata details. Labels come from each message's latest recorded observation. Search does not cover bodies or attachment contents.

## Try it with synthetic mail

From your Towpath checkout, using Python 3.11 or newer:

```sh
uv sync --locked --extra web
source .venv/bin/activate
towpath fixtures generate /tmp/towpath-ui-demo
cd /tmp/towpath-ui-demo
towpath connect sync
towpath web serve --store-dir ./state
```

Open `http://127.0.0.1:8790/`. The first visit asks you to create the instance's one account: enter the setup code the server printed when it started, a username and a password of at least 12 characters. Try searching for `invoice`, then open a result to see its attachment references. Stop the server with Ctrl+C. The generated data is invented; this demo needs no credentials or network connection after installation.

## Use your existing Gmail index

Complete the Gmail section of the [local quickstart](local-quickstart.md), then launch the UI from your installed environment:

```sh
towpath web serve --store-dir /path/to/private/state
```

Replace the path with the folder containing `source.db`, outside your checkout. The UI refuses to start if the file is missing. No OAuth client, token, account address, or private configuration is needed by the UI. Keep private stores, browser screenshots and live test output outside Git.

The completed index is usable immediately. The UI displays what is saved; it does not start or schedule a sync. To refresh mail, run the existing connector separately using your private configuration and existing pacing. Refresh the page after it finishes. Each request opens the store again, so restarting the UI is unnecessary.

`--port` selects another port if 8790 is occupied. By default the server binds to `127.0.0.1` on the current computer.

**Login.** Every page requires the instance's single account, which can do everything the UI can; there are no other users or roles. Until it exists, every page leads to `/setup`, which asks for a one-time setup code that the server prints to its log (container logs, when deployed), so only someone who can read the host's logs can claim the instance. The account is a salted PBKDF2 hash in `web-login.json` beside the stores, and sessions are signed cookies (key in `web-secret.key`, both mode 0600) that last 30 days. Failed sign-ins are slowed, and a burst of them pauses sign-in for a few minutes. To recover a lost password, run `towpath web reset-login --store-dir <folder>`: everyone is signed out and the next visit starts setup with a new code in the log.

**Behind a reverse proxy.** To reach the UI from other machines, keep it off the network itself and put it behind a TLS reverse proxy: `--host 0.0.0.0` inside a container that publishes no port, on the proxy's network, with `--public-url https://towpath.example.org` so that host is accepted, its forms pass the CSRF origin check, and the proxy's `X-Forwarded-Proto` marks cookies Secure. See [Hub setup](hub.md) and `deploy/swag-towpath.subdomain.conf.example`. The server is a simple single-process WSGI server meant for one owner.

## Unified search and collections (development branch)

Adding `--config` lets the UI see file sources and their declared scope as well as mail. Loading the configuration resolves no credential.

```sh
towpath web serve --store-dir /path/to/private/state --config /path/to/private/towpath.toml
```

- **Search all** (`/search/`) runs one query over every source's local catalog.
  - Each source shows its own status, depth, notes and errors. The page states every reason the answer is not complete.
  - Keyword text there matches only names and metadata, and the page says so.
  - **Search inside messages** (offered right under the search box) records the query for `towpath search run-requests` or the request worker (towpath-connect). While it waits, the page refreshes itself every few seconds. The stored responses appear under *Inside messages*, above the instant results, with the time they ran, after being re-checked against current grants, exclusions, configured sources and catalogs. Results that no longer qualify are withheld, with a count and reason per source. The UI never contacts Gmail, IMAP or a files provider itself.
- **References** (`/ref/?r=<ref>`) show one result's record, version, typed dates, location and parts.
  - **Request this part** queues one mail part for `towpath connect fetch-requests`.
  - Once fetched, a plain-text part is shown as escaped, untrusted text, cut at 20 KB. Other types are described, never rendered.
- **Collections** (`/collections/`) are saved queries or explicit reference sets, stored as owner decisions in the decisions store.
  - Opening one re-evaluates it against the local catalogs and reports unchanged, changed, added, removed, unverified and unavailable references, and whether the evaluation was partial.
  - For a saved query, **Accept current results** records the baseline for later comparisons.

Without `--config`, Search all covers the mail sources found in `source.db`.

Stores made by an earlier version need one explicit upgrade before the unified pages work: `towpath stores upgrade --store-dir <folder>`. Stop the UI and copy the folder first. The UI refuses to start until the upgrade has run, and it never changes a store's layout itself. See [upgrading stores](unified-discovery-acceptance.md#before-anything-else-upgrade-existing-stores).

## Data and permissions

- The source and files stores are opened under the `web` role using SQLite's read-only mode. Browsing does not change index records, label mail, delete mail or send anything.
- The only writes are owner decisions (collections, written to the decisions store and logged) and requests appended to the queue store (a content part to fetch, a provider search to run). towpath-connect carries out queued requests separately, under its own role.
- There are no model-call, delivery, approval or mailbox-action controls. Selected content appears only after the owner requests that part and towpath-connect has fetched it.
- Pages are GET-only. The forms that record decisions or queue requests are POSTs, protected by Django's CSRF check with a strict same-site cookie. Responses disable browser caching and embedding, and all source data is rendered with HTML escaping. There is no browser JavaScript and no external asset request.
- Request logging is disabled so searches and message identifiers are not written to access logs. Searches still appear in the browser's address bar and may remain in its history.
- Results use the provider's internal date where available, falling back to the parsed message date. Dates are shown in UTC. Overview counts describe the saved index, not a live provider query.

## Verification

`tests/test_web.py` and `tests/test_unified_web.py` use invented messages to check search, literal wildcard handling, current labels, paging, absent messages, escaped metadata, attachment references, missing records, read-only database enforcement, rejected writes/hosts, and unchanged database contents across requests. Browser checks use the existing synthetic corpus. Live checks should inspect aggregate counts only, leaving personal message browsing to the owner.

## Connections

Accounts are added on the **Connections** page, which is served by the connector (`towpath connect serve-setup`),
not by the web service, so the UI never handles a password. In a deployment the reverse proxy sends
`/connections/` to it; see [Hub setup](hub.md). For local development, run it beside `web serve`:

```sh
towpath connect serve-setup --config towpath.toml --credentials-dir /path/to/private/credentials --port 8791
towpath connect run-worker --config towpath.toml --credentials-dir /path/to/private/credentials
```

*Add an account* tests the sign-in, lists folders with their message counts and opens each read-only;
trash, spam and drafts start unticked. Indexing starts only when you press *Start indexing*, runs in the
worker in short resumable slices, shows progress on the account's page, and can be paused. *Replace
password* tests a new password before storing it; *Disconnect* deletes the stored password and keeps
what was indexed. New mail is read when you choose *Check for new mail*. See the
[specification](../specs/connections-in-the-ui.md).

**Gmail.** A Gmail source in the configuration file appears on the page automatically, with its index, token,
quota history and pacing unchanged. *Add an account → Gmail* first guides you through creating your own Google
OAuth *Web application* client (Google requires one per self-hoster) and shows the exact redirect URI to paste;
after that, *Connect Gmail* goes to Google's consent screen for `gmail.readonly` only, and a broader grant is
refused. *Reconnect Gmail* must sign in as the same account. The account page also sets the speed limit from
the verified per-user quota in Google Cloud Console (30% of it, at most 1,800 units a minute).
