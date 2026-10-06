# IMAP client library choice

Status: chosen for the read-only IMAP adapter on 2026-10-06, from a cloud session with GitHub and PyPI access only. Live-server behavior is unverified. That check belongs to the local operator ([acceptance](../setup/unified-discovery-acceptance.md#increment-2-provider-adapters)).

## Decision

Use **IMAPClient 4.1.0**, installed as an optional dependency (`pip install 'towpath[imap]'`, pinned `>=4.1,<5`). Towpath wraps it in a narrow read-only facade, `ReadOnlySession` in `src/towpath/adapters/imap.py`, and never hands the client object to other code.

## Evidence

| Question | Finding | How it was checked |
| --- | --- | --- |
| License | BSD-3-Clause ("Copyright (c) 2014, Menno Smits"), shipped as `COPYING` in the sdist and wheel | Read the `COPYING` file in the 4.1.0 sdist downloaded from PyPI |
| Runtime dependencies | None. Requires Python >= 3.8 | `pyproject.toml` in the 4.1.0 sdist |
| Maintenance | Releases 3.1.0 (2026-01-17), 4.0.0 and 4.0.1 (2026-09-11) and 4.1.0 (2026-09-18) on PyPI. Classifier "Production/Stable". A maintainers group is listed | PyPI JSON metadata |
| Read-only selection | `select_folder(folder, readonly=True)` calls `imaplib.select(readonly=True)`, which sends `EXAMINE`. The parsed response exposes `READ-WRITE` only if the server grants it | Read `imapclient.py` 4.1.0 and the Python 3.11 `imaplib` source. Exercised against the synthetic server: the transcript shows `EXAMINE` |
| UID identity | `use_uid=True` is the default. `search` and `fetch` send `UID SEARCH` and `UID FETCH`, and the select response gives `UIDVALIDITY` and `UIDNEXT` | Source and transcript |
| PEEK reads | `fetch` passes item names through unchanged. Towpath's allow-list permits body sections only as `BODY.PEEK[...]` and refuses `BODY[...]`, `RFC822` and `RFC822.TEXT` | Source; unit and transcript tests |
| Structure parsing | `BODYSTRUCTURE` is parsed into nested tuples (`BodyData`), so Towpath needs no MIME parser to list parts | Source; tests against the synthetic server |
| Non-ASCII search | 8-bit search values are sent as literals with `CHARSET UTF-8` | Transcript (`UID SEARCH CHARSET UTF-8 TEXT {7}`) |
| Mutating API | The library also offers `add_flags`, `set_flags`, `delete_messages`, `copy`, `move`, `expunge`, `append` and folder create, rename and delete. Towpath cannot reach any of them through `ReadOnlySession`, and a test asserts the facade's public surface | `tests/test_imap.py` |

GitHub release notes for 4.x could not be read from this environment, because the GitHub API and web pages for the project were not reachable. The API surface Towpath uses was checked in the 4.1.0 source instead. The local operator should skim the 4.0 and 4.1 release notes before production use.

## Alternatives considered

- **Standard-library `imaplib`.** No dependency, but it returns raw response lines, so Towpath would have to write its own FETCH and BODYSTRUCTURE parser. IMAPClient builds on `imaplib` and adds exactly that parsing.
- **Mailbox collectors (mbsync/isync, OfflineIMAP).** These copy whole mailboxes to local Maildir. Durable mailbox copies are outside this implementation (see the [specification](../specs/unified-discovery-foundation.md#3-gmail-and-imap)). They could later act as a separate source, read as an archive.
- **Async clients.** Not needed: runs are sequential and paced, like the Gmail connector.

## What the tests prove, and what they do not

The synthetic server, `tests/imap_stub.py`, speaks enough IMAP4rev1 for IMAPClient. It records every command, refuses every command outside the read-only set, and sets `\Seen` when a client fetches a body without PEEK, as a real server does. The tests in `tests/test_imap.py` show that a full sync, an incremental sync, provider search and selected-content reads together issue only CAPABILITY, LOGIN, LIST, EXAMINE, UID SEARCH, UID FETCH (PEEK only) and LOGOUT, and leave every message's flags unchanged.

The tests also cover:

- identity under UIDVALIDITY change;
- expunged messages;
- deleted and renamed mailboxes;
- a mailbox that fails to open, which is never reconciled as empty;
- dropped connections, with clean stop and resume;
- capped runs;
- FETCH batch limits;
- a server that grants write access on EXAMINE, which is refused;
- a wrong password, which stops cleanly with an auth stop.

They do not prove how a particular server behaves. Still to check against a real server:

- TLS and STARTTLS negotiation;
- `SEARCH TEXT` semantics and speed (substring or word matching, attachments included or not, indexed or scanned);
- server-specific limits;
- folder naming.

Those checks are listed in the local acceptance guide.
