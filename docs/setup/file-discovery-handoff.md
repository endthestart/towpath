# Handoff: validate file discovery locally

Paste the prompt below into a coding agent running on the owner's machine. It continues the [file discovery](../file-discovery.md) foundation on branch `claude/file-discovery-foundation`. That foundation was built and tested in a cloud session on synthetic files only.

---

You are validating Towpath's optional file discovery on the owner's machine. The work is on branch `claude/file-discovery-foundation`. Before doing anything, read:

- `docs/file-discovery.md`, including **Known gaps**;
- `docs/decisions.md` (D17);
- `docs/evaluations/file-discovery-providers.md` and `docs/evaluations/file-discovery-native.md`.

**What exists:**

- `towpath files ...`, with a fixture provider and a Recoll adapter (through Recoll's Python binding), and a sist2 capability slot.
- `files.db`, per-root grants, recovery, and context packets.
- A native evaluation harness (`towpath files evaluate`).

All of it was tested on synthetic files. None of it has met the owner's archives.

**Rules:**

1. **Leave the running Gmail index alone.** Do not pull, install, or switch branches in the checkout, virtualenv, or state folder that the ongoing Gmail index uses. Use a **separate clone** and a separate virtualenv. Do not start until the owner confirms this is safe.
2. **Read only.** Point roots at a read-only mount or a read-only copy. Never move, delete, rename, deduplicate, or modify a source file. `recover_dir` must be outside every root.
3. **Private material stays out of Git.** Keep these in `~/towpath-private/files/`, outside the repository:
   - configs with real paths;
   - Recoll configuration and index;
   - `files.db` and recovered copies;
   - command output that names real files.

   Before every commit, run `git status`, `git diff --cached`, and `towpath fixtures check-domains src tests docs examples README.md CONTRIBUTING.md`.
4. **No models.** Nothing in file discovery calls a model; keep it that way. Context packets stay local.
5. **No adoption by default.** Record evidence. The choice between Recoll and sist2 is the owner's.
6. **Checks before any commit:** `set -o pipefail`, `python -m pytest -q`, and `ruff check src tests`. Never commit with a failing test. Commit only on `claude/file-discovery-foundation`, and do not merge it into the email branch.

**Steps.** Stop after each one and report:

1. **Clone and test.**
   - In a fresh clone, run `git checkout claude/file-discovery-foundation`. Create a new virtualenv and run `pip install -e '.[dev]'`.
   - Run `python -m pytest -q -rs`. Record the total and the skipped tests: native tests skip when Recoll or sist2 is missing.
2. **Install Recoll** with the system package manager (for example `recollcmd` and `python3-recoll` on Debian or Ubuntu).
   - Find an interpreter that can `import recoll`; the binding is built for one Python version.
   - Rerun the tests. The two native Recoll tests should now run.
3. **Synthetic native evaluation.**
   - Run `towpath files evaluate ~/towpath-private/files/eval-synthetic --recoll-python <interpreter>`.
   - Compare its `report.json` with `docs/evaluations/file-discovery-native-report.json`, and note any difference in behaviour or version.
4. **sist2 (optional).**
   - Run `docker pull sist2app/sist2:4.2.3`, then check that its digest is `sha256:6481bcdf7e806ece5e444a273acf967460e0a5385de5ab547285a3573e5edb19` and that `--version` prints `4.2.3`. Do not use `latest`, which pointed at 4.2.1.
   - Run `towpath files evaluate ... --tool sist2`. Record the result.
5. **A small real root.**
   - With the owner, pick one small folder of old backups that holds at least one ZIP of mail (ideally the "college paper" case), plus an excluded subfolder.
   - Write a private Recoll config whose `topdirs` is that folder, and run `recollindex -c <confdir>`. Record time, index size, peak memory (`/usr/bin/time -v`), and Recoll's `missing` file.
   - Write a private `towpath.toml` from `examples/files.example.toml`.
6. **Probe, grant, import.**
   - Run `towpath files probe`, then `towpath files grant <root> search`, then `towpath files import`.
   - Record `by_status`, `refused_references`, `complete`, and the run time.
   - If the import fails with `output-limit`, record that (a known gap: enumeration is not chunked yet). Do not raise limits past their ceilings.
   - Run `towpath files import --max-items 1` and confirm `complete: false`, `absence_established: false`, and `marked_missing: 0`. Recoll lists folders as rows, so the cap is reached even though they are filtered.
7. **The defining example.**
   - `towpath files search "<words from the paper>"`: confirm the result names the ZIP, the mailbox, the message, and the attachment, with the message date.
   - Grant `excerpt`, run `files excerpt`, then `files cite`.
   - Grant `recover`, run `files recover`, then open the recovered copy and check its SHA-256 against the provenance file.
   - Confirm nothing under the root changed: compare a listing with sizes and mtimes taken before and after.
8. **Negative checks on real data:**
   - The excluded subfolder never appears, in search, import, or context.
   - An ungranted second root never appears.
   - A symlink leaving the root is refused.
   - Record what happens with an encrypted ZIP, a corrupt ZIP, a PST with and without `pffexport`, and a TAR (off by default in Recoll).
9. **Context packet.**
   - Run `towpath files context --purpose agent-context --query ...` without an excerpt grant, then with one. Check that `omitted`, the uncertainty notes, and the limits are sensible.
   - Confirm a `life-evidence` packet is refused until that grant is given.
10. **Staleness and rebuild, on a scratch copy only.**
    - Copy one ZIP into a scratch root and index it. Cite a passage from it, then modify the copy **without** re-running `recollindex`. Then check:
      - `describe` shows `provider_version: same` and `source: changed`;
      - `cite` says `stale`;
      - `excerpt` and `recover` refuse with `stale`.
    - Run `recollindex` again, search again: the result is `current`, and the old citation stays `stale`.
    - On the real Recoll version, confirm the stamp fields still behave as observed:
      - `sig` is the outer size followed by the whole-second ctime;
      - `pcbytes` is the outer size;
      - `fmtime` is the outer mtime.

      If they do not, `describe` shows which fields were compared (`source_fields_checked`). Record that.
    - Delete `files.db` and re-import: occurrence IDs and grants must survive.
11. **Measurements.** For the real root, record:
    - index time, size, and memory;
    - query latency for a few searches;
    - coverage by status.

    These are measurements, not promises.
12. **Write up.**
    - **Private:** full outputs in `~/towpath-private/files/notes.md`.
    - **Public:** a generic summary appended to `docs/evaluations/file-discovery-native.md` under "Local results". Include tool versions, behaviours, and order-of-magnitude numbers, but no file names, paths, or contents.
    - **Decisions:** if the owner chooses a provider, update D17 and add a decision-log row.

**Stop and ask the owner when:**

- a step would touch the Gmail index's checkout or state;
- a source would have to be writable;
- a check fails and the cause is unclear;
- a fix would widen a grant, raise a ceiling, or add a model call;
- it is time to choose between Recoll and sist2.

---
