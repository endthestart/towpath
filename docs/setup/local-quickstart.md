# Local quickstart: test Towpath against your own accounts

Status: **instructions for code that is built but not yet run against real services.** Everything here reads only. Towpath has no code that can change a mailbox, a document system, or a photo library.

Start with a **dedicated test Google account** that has a little mail and a few PDF and image attachments. Move to a primary account only after the checks in parts C and D pass.

Keep all private material in a folder **outside the repository**. Nothing from that folder goes into Git, issues, or chats about the public project.

Google's console labels change from time to time. If a label below does not match, look for the nearest equivalent in "Google Auth Platform" (formerly the "OAuth consent screen").

## Part A: install and run the tests

Requires Python 3.11 or newer, Git, and a browser.

```sh
git clone https://github.com/endthestart/towpath.git
cd towpath
git checkout claude/happy-gauss-ugprmu
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q          # expect: 94 passed
ruff check src tests         # expect: All checks passed!
```

Create the private folder and copy the example configuration:

```sh
mkdir -p ~/towpath-private/secrets
chmod 700 ~/towpath-private ~/towpath-private/secrets
cp examples/towpath.example.toml ~/towpath-private/towpath.toml
cd ~/towpath-private
```

Every command below runs from `~/towpath-private` with the virtual environment active, and reads `towpath.toml` from there.

## Part B: a Google Cloud project for Towpath (read-only)

Use a project that **only Towpath uses**. Inbox Zero gets its own separate project later, because Google can combine grants across clients in one project ([D14](../decisions.md#d14-gmail-access-for-other-self-hosters)).

1. Open [Google Cloud console](https://console.cloud.google.com/), signed in as the test account (or your own account; the project owner does not need to be the mailbox).
2. Create a project, for example "towpath-readonly".
3. **APIs & Services → Library**, search "Gmail API", **Enable**.
4. **Google Auth Platform → Branding** (or "Get started"):
   - App name: "Towpath (personal)".
   - User support email and developer contact: your address.
5. **Audience**:
   - Choose **External** for a personal Gmail account. Workspace accounts can choose **Internal**, which skips the unverified-app warning and the token expiry.
   - Under test users, add the test Gmail address.
6. **Data Access → Add or remove scopes**: add only `https://www.googleapis.com/auth/gmail.readonly`, then save. Add nothing else.
7. **Clients → Create client**:
   - Application type: **Desktop app**. Name: "Towpath CLI".
   - Create, then **Download JSON**.
   - Save it as `~/towpath-private/secrets/google-oauth-client.json`, then run `chmod 600 ~/towpath-private/secrets/google-oauth-client.json`.
8. **Audience → Publish app → In production**, then confirm.
   - Do not submit for verification. Personal use by you does not need it.
   - Left in Testing status, Google expires the refresh token after 7 days and you would re-run `connect auth` weekly.
   - Google will show "Google hasn't verified this app" during consent. That is expected for a personal client.

## Part C: authorize and prove the read-only boundary

```sh
towpath config check
```

Expect `client secrets present, token missing` for `gmail_test`. Paperless and Immich lines will say MISSING until part E.

```sh
towpath connect auth gmail_test
```

1. A browser opens. Choose the test account.
2. On the warning, choose **Advanced → Go to Towpath (personal) (unsafe)**.
3. Allow "View your email messages and settings".
4. The terminal prints the granted scopes. It must show only `https://www.googleapis.com/auth/gmail.readonly`.
   - If it says **refused**, the grant was broader. Towpath deleted nothing and saved no token.
   - Fix it this way: remove the app at [Google account permissions](https://myaccount.google.com/permissions), create a fresh project as in part B, and repeat.
5. On a machine without a browser, use `towpath connect auth gmail_test --no-browser` and open the printed URL elsewhere.

```sh
towpath connect probe gmail_test
```

This shows the account address and its current history ID.

```sh
towpath connect verify-structure gmail_test --sample 50
```

This is the D16 check ([decision](../decisions.md#d16-gmail-structure-without-content)). It reads 50 recent messages with Towpath's structural field mask and stores nothing.

- **Pass:** `passed: True`, `messages_with_body_data: 0`, and `truncated: 0`.
- An empty mailbox cannot establish this: the check fails when no messages were read. Add harmless test messages with PDF and image attachments first, and record how many messages and which MIME cases were actually checked.
- **Also note:** `attachments_inline`, which counts attachments Gmail sends inline instead of by attachment ID, and `max_depth`.
- **If it fails:** stop and record the output privately. Do not continue to the sync until the result is understood.

A pass applies to the messages sampled. Before using a primary account, also verify the granted scopes, capped and incremental syncs, interruption recovery, and handling of messages deleted during a sync on the test account.

## Part D: index slowly, measure, and resume

Every Gmail request Towpath makes is paced by a persistent budget in `state/quota.db`:

- **Minimum interval:** at least 1 second between request attempts.
- **Per minute:** at most 1,200 quota units, counted per account.
- **Per day:** at most 1,800,000 units for the whole Google project. The budget day follows Google's Pacific-time quota day.
- **What counts:** every attempt, including retries, listing, history, probes, `verify-structure`, and attachment fetches. Each is charged at its published unit cost: a message read is 20 units, so about one message per second at most.
- **One command at a time:** a lock admits one Gmail command per budget. A second one exits with code 8 instead of doubling the rate.

These are Towpath's own conservative budgets, not Google's limits. Confirm your project's actual quotas in Cloud console (**APIs & Services → Gmail API → Quotas**). You may lower the budgets, never raise them, in `towpath.toml`:

```toml
[gmail_pacing]
budget_id = "default"          # one budget per Google Cloud project
min_interval_seconds = 1.0     # 1.0 or more
units_per_minute = 1200        # 20 to 1200
daily_units = 1800000          # 100 to 1800000
checkpoint_every = 25          # messages per committed checkpoint
```

Start with a capped run and look at its status:

```sh
time towpath connect sync gmail_test --max-items 200
towpath connect status gmail_test
```

`status` shows the following, but no message data, IDs, cursors, or page tokens:

- the last runs, and how each ended;
- whether a full sync is in progress, and its phase;
- how much of the per-minute and daily budget is used;
- whether another command holds the lock.

Progress lines on stderr show the phase, the count processed, any waits, and a *measured* rate with its spread. That rate is not a completion estimate.

Then let it run in the background until a run reports `complete`. For example:

```sh
nohup towpath connect sync gmail_test > ~/towpath-private/sync.log 2>&1 &
```

Every way a run can end is recorded, and the next run continues from where it stopped:

| Exit | Meaning | What to do |
| --- | --- | --- |
| 0 | complete, or capped by `--max-items` | Run again to continue a capped run |
| 3 | quota stop, or the local daily budget is used | Run again later; after a daily stop, run again the next budget day |
| 4 | authorization expired or revoked (an app in Testing expires after 7 days) | `towpath connect auth gmail_test`, then run again |
| 5 | permission refused | Check that only `gmail.readonly` is granted and the Gmail API is enabled |
| 6 | Gmail rejected a request | Record the reason privately and report it |
| 7 | server or network errors persisted through retries | Run again later |
| 8 | another Towpath command holds the budget | Wait for it to finish |
| 130 | Ctrl-C | Run again |

How resuming works:

1. A full sync first **scans** the mailbox, then makes a cheap **reconcile** listing pass that catches anything the mailbox shifted underneath it.
2. Last, it **confirms** each previously indexed message the listing did not show. A message is marked absent only when Gmail says it is gone.
3. A resumed run starts at the page where the last one stopped. If Gmail rejects that saved page token, the run lists from the start again, skipping messages already read.
4. Incremental runs save their position after each history record. They never skip events, and a stop never claims completion.

Later runs are incremental and take seconds. Labels changed by you or by another tool are recorded as dated observations; deleted messages are marked absent, not erased.

Check the index directly, if you like:

```sh
sqlite3 state/source.db "select count(*), sum(absent_since_run is not null) from items;"
sqlite3 state/source.db "select count(*) from cache;"   # 0 until a scan fetches attachments
sqlite3 state/quota.db "select method, count(*), sum(units) from attempts group by method;"
```

## Part E: Paperless and Immich (read-only lookups)

Both are optional. Each answers one question: does it already hold a file with this checksum?

### Paperless-ngx

1. In Paperless, create a user named `towpath-reader` with permission to **view documents** only, and no change or delete permissions.
   - Documents with no owner may be visible to every user; that is Paperless behavior.
2. Get that user's API token:
   - Either log in as `towpath-reader` and use **My Profile → API Auth Token**,
   - or request one: `curl -s -X POST -d "username=towpath-reader&password=..." http://localhost:8000/api/token/`.
3. Save the token, then lock the file down:

   ```sh
   printf '%s' 'THE_TOKEN' > secrets/paperless-token
   chmod 600 secrets/paperless-token
   ```
4. In `towpath.toml`, set the Paperless `base_url` to your instance.
5. Check the connection:

   ```sh
   towpath connect probe paperless
   ```

   Expect `algorithm: sha256` on Paperless 3.x, or `md5` on 2.x. If the version is missing, record the output.

### Immich

1. In Immich, go to **Account Settings → API Keys → New API Key**.
   - Grant only asset read permissions: `asset.read`, plus `asset.view` if offered.
   - If `connect probe immich` later returns HTTP 401 or 403 on `/api/server/version` or `/api/search/metadata`, add the smallest permission that fixes it, and note which.
2. Save the key, then lock the file down:

   ```sh
   printf '%s' 'THE_KEY' > secrets/immich-api-key
   chmod 600 secrets/immich-api-key
   ```
3. Set the Immich `base_url`, then check the connection:

   ```sh
   towpath connect probe immich
   ```

### Run the scans

```sh
towpath connect sync paperless immich      # registers each destination's checksum type
towpath scan run                           # finds attachments; requests their bytes
towpath connect fetch-requests             # fetches only those parts
towpath scan run                           # requests presence checks
towpath connect fetch-requests             # asks Paperless and Immich
towpath scan run                           # marks present files; proposes the rest
towpath proposals list
```

- **Spot-check:** a PDF you know is in Paperless should not appear as a proposal.
- **Proposals are inert:** Towpath has no code that uploads anything. Dismiss proposals you don't want with `towpath scan dismiss PROPOSAL_ID`.

## Part F: a local model (optional)

1. Install a local OpenAI-compatible server. For example, [Ollama](https://ollama.com/) listens on `http://127.0.0.1:11434/v1`; llama.cpp's server listens on port 8080.
2. Pull a small instruction-following model. Put its exact name in `[endpoints.local] model`.
3. Probe it:

   ```sh
   towpath model probe local
   ```

   This uses synthetic prompts only. Note which of `json_schema` and `json_object` it supports.
4. Process the queue:

   ```sh
   towpath model run-queue
   towpath scan model-queue
   ```

   Only metadata (file name, type, size, subject, sender domain) is sent, because `allow_data` lists only `synthetic` and `metadata`.

For a server elsewhere on your network, such as Poundlock, set `destination = "self-hosted"` and grant what it may receive, for example `towpath model grant <endpoint> metadata`. Towpath refuses to send anything else until you do. Changing the endpoint's model or URL voids the grant.

To keep an item away from every model, run `towpath item set ITEM_ID --model-use excluded`.

## Part G: Inbox Zero (separate evaluation)

Follow the [evaluation plan](../evaluations/inbox-zero-plan.md) with its own Google Cloud project. Afterwards, enable the `[[providers]]` block in `towpath.toml`, using a key with only `STATS_READ` and `RULES_READ`, and run:

```sh
towpath provider probe
towpath provider overview --period week
towpath provider rules
towpath provider links
```

## What to record, and where

| Record | Keep private | May go in the public repository |
| --- | --- | --- |
| `verify-structure` output | Full output | Pass or fail, `max_depth`, whether inline attachments occurred |
| Sync timing | Message counts and times | Messages per minute on a new project |
| Paperless and Immich results | URLs, IDs, file names | Versions, checksum types, any API differences from the code |
| Errors | Full traces | The error type, with no account data |

## Undo everything

- **Remove Gmail access:** at [Google account permissions](https://myaccount.google.com/permissions). Optionally delete the Cloud project.
- **Delete Towpath's local data:** remove `~/towpath-private/state/`.
- **Revoke Paperless and Immich access:** delete the `towpath-reader` user and the Immich API key.
