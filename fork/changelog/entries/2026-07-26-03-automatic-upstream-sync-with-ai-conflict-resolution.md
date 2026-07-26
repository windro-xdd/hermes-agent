# Added: automatic upstream sync with AI conflict resolution

**Date:** 2026-07-26  
**Type:** Added

- **Patch id:** `fork-sync`, `fork-guard`
- **Why:** the stock updater destroys fork commits. It runs
  `git reset --hard origin/<branch>` whenever a fast-forward fails, and a fork
  with its own commits always fails a fast-forward. Three separate sites do this
  — `hermes_cli/main.py:11540`, `scripts/install.ps1:1524`,
  `scripts/install.sh:1226` — and install.ps1's own comment states it exists to
  discard "local-only commits". The Windows update path reaches that script via
  `hermes-setup.exe` without ever entering `main.py`.
- **Changed:**
  - `scripts/fork_sync.py` (new) — fetch upstream, tag a rollback point, **merge**,
    resolve conflicts with `claude-opus-5`, verify, push. `status` subcommand for
    a read-only health check.
  - `scripts/fork-guard.sh` (new) — `reference-transaction` hook refusing any ref
    move that would abandon unpushed fork commits. Reinstalled on every sync
    because `.git/hooks` is unversioned.
  - `~/.hermes/scripts/fork_sync_tick.py` (new, user data) — cron launcher.
  - Cron job `3d3f6afa4074`, daily 04:00. Silent on success; reports failures in
    plain language.
  - `scripts/hermes-sync.ps1` — written, then **removed**. Wrong language.
- **Impact:** the fork tracks upstream automatically. Hermes's native update
  popup works unchanged, and clicking Update is a plain fast-forward of code that
  was already merged and verified hours earlier.

- **The decision that matters — merge, not rebase.** The plan specified rebase.
  Testing proved rebase manufactures the exact hazard the project exists to
  prevent:

  | strategy | old local sha still an ancestor of fork tip? | app's `pull --ff-only` |
  |---|---|---|
  | rebase | NO — SHAs rewritten | **fails → triggers `reset --hard`** |
  | merge | YES | succeeds; reset unreachable |

  Because failing `pull --ff-only` is the *only* path to the reset, merging makes
  the destructive branch unreachable — which is why **no upstream file needed
  patching at all**. Measured churn made that decisive: `hermes_cli/main.py` sees
  455 commits/90d and `apps/desktop/src/app/settings/` 206, so patching either
  would have been a permanent conflict tax. This commit series modifies **zero**
  upstream files.

- **Risk / watch for:**
  - **pytest is not installed in this venv**, so the Python suite cannot run. The
    gate falls back to importing every changed module and says so in each history
    entry. Installing pytest would strengthen verification — worth doing.
  - **The desktop popup reads `origin`, not `upstream`** (`electron/main.ts:2415`).
    The popup only works because the sync keeps the fork current. If the cron job
    stops, the popup goes quiet rather than erroring — a silent failure. Check
    `python scripts/fork_sync.py status` if updates seem to have stopped.
  - **The guard also blocks `reset --soft`** onto a remote tip while unpushed work
    exists. Git gives the hook no way to distinguish it from `reset --hard`.
    Override with `HERMES_SYNC_ALLOW_REWRITE=1`.
  - **Untracked files that upstream later adds** are moved to
    `~/.hermes/fork-sync/parked/<timestamp>/` rather than deleted. Nothing prunes
    that directory.
  - AI resolutions are gated on build/import, which cannot catch a *semantically*
    degraded customization that still compiles. Review `resolutions` entries in
    the history file after any sync that reports a conflict.

- **Verified — four real unattended runs on the live repo, each fixing a defect
  the previous run exposed:**
  1. merged 9 upstream commits, 5 customizations intact, dry run — passed
  2. failed: `run_tests.sh` cannot find pytest → distinguished "harness
     unavailable" from "tests failed"; rollback worked
  3. failed: rollback left 100+ staged files → fixed ordering (`reset` mixed +
     `checkout`), which would otherwise have deadlocked the next sync
  4. failed: an untracked file blocked the merge outright → park-and-retry
  Final run: **"✓ synced 1 upstream commit(s); 9 customization(s) intact;
  pushed"**. `status` reports 0 behind upstream, 10 fork patches, 0 unpushed,
  clean tree, guard installed. Every `pre-sync-*` tag confirmed still an ancestor
  of HEAD — the fast-forward guarantee holds. Guard suite: 15/15 in both
  directions. AI resolution proven separately on an induced conflict: kept
  upstream's refactor *and* the customization, added error handling, self-reported
  medium confidence with an honest reason.

- **Follow-ups:**
  - **Cron delivery is `local` — failure messages are saved but NOT pushed into a
    chat.** Read them with `cronjob(action='list')` or the history file. Wiring a
    gateway (e.g. Telegram) would make alerts actually arrive; until then a failed
    sync is silent. **This is the biggest open gap.**
  - Install pytest to enable the real test gate.
  - A Settings page for sync history/AI resolutions is deliberately deferred: it
    would mean permanently editing the second-highest-churn directory for a
    read-only log viewer. History is already JSONL, so the UI can come later.
  - `fork_sync.py` has no unit tests of its own.
