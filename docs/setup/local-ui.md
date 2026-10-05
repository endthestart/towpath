# Local email UI

The optional Django interface reads an existing email metadata index. It gives you an overview, a paginated email list, literal search of subjects, senders and attachment names, Inbox/Sent/attachment filters, and individual metadata details. Labels come from each message's latest recorded observation. Search does not cover bodies or attachment contents.

## Try it with synthetic mail

From your Towpath checkout, using Python 3.11 or newer:

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e ".[web]"
towpath fixtures generate /tmp/towpath-ui-demo
cd /tmp/towpath-ui-demo
towpath connect sync
towpath web serve --store-dir ./state
```

Open `http://127.0.0.1:8790/`. Try searching for `invoice`, then open a result to see its attachment references. Stop the server with Ctrl+C. The generated data is invented; this demo needs no credentials or network connection after installation.

## Use your existing Gmail index

Complete the Gmail section of the [local quickstart](local-quickstart.md), then launch the UI from your installed environment:

```sh
towpath web serve --store-dir /path/to/private/state
```

Replace the path with the folder containing `source.db`, outside your checkout. The UI refuses to start if the file is missing. No OAuth client, token, account address, or private configuration is needed by the UI. Keep private stores, browser screenshots and live test output outside Git.

The completed index is usable immediately. The UI displays what is saved; it does not start or schedule a sync. To refresh mail, run the existing connector separately using your private configuration and existing pacing. Refresh the page after it finishes. Each request opens the store again, so restarting the UI is unnecessary.

`--port` selects another local port if 8790 is occupied. The server always binds to `127.0.0.1` on the current computer. It has no login and uses a simple local WSGI server; remote access, reverse proxies, production serving and authenticated multi-user access require a separate design. The existing container images package the CLI, not this optional UI.

## Data and permissions

- The source store is opened under the `web` role using SQLite's read-only mode. Browsing does not change index records, label mail, delete mail or send anything.
- There are no content-fetch, model-call, delivery, approval or mailbox-action controls in this preview. Message details show metadata and named attachment references, even if other processes have cached content.
- Routes accept GET only. Responses disable browser caching and embedding; mail metadata is rendered with HTML escaping. There is no browser JavaScript or external asset request.
- Request logging is disabled so searches and message identifiers are not written to access logs. Searches still appear in the browser's address bar and may remain in its history.
- Results use the provider's internal date where available, falling back to the parsed message date. Dates are shown in UTC. Overview counts describe the saved index, not a live provider query.

## Verification

`tests/test_web.py` uses invented messages to check search, literal wildcard handling, current labels, paging, absent messages, escaped metadata, attachment references, missing records, read-only database enforcement, rejected writes/hosts, and unchanged database contents across requests. Browser checks use the existing synthetic corpus. Live checks should inspect aggregate counts only, leaving personal message browsing to the owner.
