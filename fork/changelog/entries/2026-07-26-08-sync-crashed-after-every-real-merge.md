# Fixed: the sync crashed after every real merge (undefined variable)

**Date:** 2026-07-26
**Type:** Fixed

- **Patch id:** `fork-sync`
- **Why:** the block in `scripts/fork_sync.py` that decides whether a merge needs
  a dependency install or a desktop rebuild read a variable named
  `changed_files`. That name is **never assigned anywhere in the module** — the
  merged file list is computed a few lines above as `changed` and passed to
  `verify()`. A rename missed two references.

  The consequence was not cosmetic. The block runs only after a sync has actually
  merged something, so every run since the code landed skipped it: the two runs
  that followed found nothing new upstream, and the run that merged 81 commits
  happened five minutes *before* this code existed. With 43 upstream commits
  waiting, the next scheduled run at 04:00 would have raised `NameError`
  immediately after the merge — before the push, before the dependency install,
  before the desktop rebuild.

  And it would have been silent. The exception was unhandled, so `finish()` never
  ran: no history entry, no report file. Sync alerts are not delivered anywhere
  yet, so the only trace would have been a traceback at the end of
  `fork-sync/last-run.log`, which nothing reads. Every following night would
  repeat it — the fork merging upstream locally, never publishing, never
  rebuilding, `status` still reporting a healthy tree.

- **Changed:**
  - `scripts/fork_sync.py` — the two reads now use `changed`, the merged diff that
    was already computed. Comment records why, so the rename is not re-broken.
  - `scripts/fork_sync.py` — new `record_crash()`, plus a `try/except` around the
    sync call in `main()`. An unhandled exception now writes a `CRASHED-*` report
    containing the traceback and the most recent rollback tag, appends a history
    entry with `status: "crashed"`, and exits non-zero. It deliberately does **not**
    attempt a rollback: at crash time the tree state is unknown, and blind
    recovery could destroy a good merge. `KeyboardInterrupt` is re-raised.
  - `tests/fork_sync/test_fork_sync_contract.py` — two tests
    (`test_followups_use_the_merged_diff`, `test_unhandled_crash_is_recorded`).

- **Impact:** the nightly sync can complete again. Without this, updates would
  have stalled indefinitely and silently starting tonight.

- **Risk / watch for:**
  - The follow-up test drives a **real** merge in a temporary repo and intercepts
    only `npm install` and the desktop `--force-build` command; every git call runs
    for real. If either command is ever renamed, the test silently stops asserting
    what it claims to. It matches on the argument text, not the function.
  - `record_crash` writes to the state directory. If that directory is
    unwritable the crash report itself fails — the traceback still reaches stdout.
  - The deps/rebuild steps in the engine look like duplication of what
    `hermes update` does. They are not: on the fork path `hermes update` returns
    before its own deps and rebuild steps. Do not "simplify" them away.

- **Verified:** the fix was proven by reintroducing the bug. With
  `changed_files` restored, `test_followups_use_the_merged_diff` fails with
  `NameError: name 'changed_files' is not defined` at `scripts/fork_sync.py:882`,
  printed immediately after `✓ merge complete` and `✓ verification: stubbed` —
  the exact point and state the 04:00 run would have died in. With the fix in
  place: `venv/Scripts/python.exe -m pytest tests/fork_sync/ -q` → `42 passed in
  15.58s`. The crash test asserts a real history entry with `status: "crashed"`
  and a report file containing the traceback.

- **Follow-ups:**
  - **Sync alerts still go nowhere.** This fix makes a crash *recorded*; it does
    not make it *delivered*. A crash is still invisible unless someone reads
    `fork-sync/history.jsonl`. Wiring a gateway remains the biggest open gap.
  - The test baseline at `fork-sync/test-baseline.json` only lists the failures
    `verify()` happened to run, so it cannot be used to judge unrelated suites.
    Comparing against a pre-change tree is still the only reliable method — and
    it must be done in the **same directory**, since guards in `hermes update`
    read `PROJECT_ROOT` and behave differently in a worktree.
