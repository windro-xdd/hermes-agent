# Fixed: six defects in the sync engine, all found by running it

**Date:** 2026-07-26  
**Type:** Fixed

- **Patch id:** `fork-sync`
- **Why:** the engine shipped in the entry below was verified interactively and
  then failed repeatedly once it ran unattended. Every defect here was found by
  executing the real automated path or by an independent reviewer reading the
  update flow — none by re-reading my own code. They are logged individually
  because the *pattern* matters more than any single fix.

- **Changed — the six defects, in the order found:**

  **6. The push hid the update (the worst one).** `sync()` pushed to origin as its
  last step, which made `rev-list HEAD..origin/<branch>` zero, which made
  `hermes update` return early at `main.py:11406 → 11513` printing "Already up to
  date!". That skipped dependency install (`main.py:11648`) and the desktop
  rebuild (`electron/main.ts:3165`). Result: merged source running against stale
  deps and a stale app bundle while the UI reported everything current. The native
  popup never fired either — it reads the same count (`electron/main.ts:2418`).
  The mechanism disarmed its own trigger, silently. Found by a review subagent,
  *after* I had declared the work finished.

  **7. The test gate could never pass.** `run_tests.sh` reported "no virtualenv
  with pytest found" even after installing pytest, because it probes
  `<venv>/bin/activate` (POSIX) and Windows venvs use `Scripts/`. My probe checked
  `import pytest` in the current interpreter — the wrong question. Now: use
  `run_tests.sh` when it can select a venv, else run pytest directly, else fall
  back to an import check.

  **8. The gate demanded a green suite that has never been green.** Once pytest
  ran, 12 tests failed — all pre-existing, confirmed by running the same files
  against the pristine upstream clone at `C:/Users/wnxdd/hermes-pr-verify` (POSIX
  path assumptions in `test_managed_uv.py` / `test_config.py`). Blocking on those
  meant no update could ever be accepted. Now gates on failures the merge
  *introduced*, against a stored baseline.

  **9. State split across two directories.** `_state_dir()` derived from
  `HERMES_HOME`, which is set interactively and **not** under Task Scheduler. The
  scheduled run could not see the baseline the interactive run wrote, so 12 known
  failures read as "newly failing". Now anchored to the install root — one
  location, every context.

  **10. The baseline was too narrow.** Two skills tests entered scope on a later
  sync, were absent from the baseline, and were reported as merge damage though
  both fail pre-merge. Now a suspect failure is re-run against the rollback point
  in a detached git worktree, so "the merge broke this" is *proven*. A bug in my
  own fix was caught by its test: `_write_baseline(failed_now)` discarded the
  just-absorbed entry.

  **11. The rebuild command did not exist.** The rebuild called
  `./venv/Scripts/hermes`, which is not present on this install; `hermes` is not on
  PATH under Task Scheduler either. The step meant to guarantee a current app could
  never run — it would have failed quietly on the first sync touching
  `apps/desktop/`. Now `python -m hermes_cli.main desktop --build-only
  --force-build`, verified against `--help`, with a helper that checks both venv
  layouts. Note `python cli.py desktop` is a *different program* — cli.py treats
  "desktop" as a chat prompt.

- **Impact:** the sync now works unattended end to end. Because this checkout IS
  the running install, the sync performs the catch-up steps itself — dependency
  install only if manifests changed, desktop rebuild only if `apps/desktop/`
  changed — and pushes **last**, once nothing can be hidden.

- **Risk / watch for:**
  - **A failed rebuild does NOT roll back.** The merge is verified and the source
    is good; only the bundle is stale. Reverting verified code over a build failure
    would be worse. It reports loudly instead — check `REBUILD-FAILED` reports.
  - The test baseline is now self-maintaining, but a *legitimately* new failure
    that also fails pre-merge for an unrelated reason would be absorbed. Review
    the `resolutions` and baseline diffs after any noisy sync.
  - `tests/fork_sync/` runs on every sync. If it ever starts failing, the sync
    blocks — by design, but it means a broken contract test halts updates.

- **Verified:** 14 contract tests in `tests/fork_sync/test_fork_sync_contract.py`,
  covering each defect above plus both directions of the gate (pre-existing
  failures don't block; a new failure does). The push-ordering test was proven to
  bite by reintroducing the bug and watching it fail. Final scheduled run:
  **"✓ merged 81 upstream commit(s); 17 customization(s) intact"**, verification
  *"no new test failures (12 pre-existing, 2 newly fixed upstream); typecheck
  passed"*, then a clean push of 83 commits. App bundle rebuilt and confirmed
  current — 0 source files newer than `app.asar`. `status`: 0 behind upstream,
  19 fork patches, 0 unpushed, clean tree, guard installed.

- **The pattern, named so it can be prevented:** of these six defects, **four
  (7, 9, 11 and the earlier cp1252 crash) passed interactively and failed only on
  the automated path** — encoding, venv layout, environment variables, PATH. Two
  more (7, 8) were checks that *could not pass*, which is an outage disguised as
  safety and trains the user to ignore alerts. Twice I encoded my first guess as a
  test invariant rather than the actual requirement (an exact-wording assertion,
  and "never push" when the real rule was "don't push *yet*"), and both had to be
  rewritten. Rules adopted: test the automated path, not the interactive one; a
  check that cannot pass is a bug in the check; prove "new" before blaming a
  change.

- **Follow-ups:**
  - Cron delivery is still `local` — failure alerts are saved, not delivered.
    Wiring a gateway (e.g. Telegram) remains the biggest open gap.
  - `pytest`, `pytest-asyncio`, `pytest-timeout` were installed into the venv to
    make the gate real. A fresh install would need them again.
  - 12 upstream tests fail on Windows for POSIX path reasons; not ours to fix, now
    baselined.
