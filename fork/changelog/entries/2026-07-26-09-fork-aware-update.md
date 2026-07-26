# Added: the native Update button now actually updates the fork

**Date:** 2026-07-26
**Type:** Added

- **Patch id:** `fork-aware-update`
- **Why:** windro reported that the update UI showed nothing he expected. It was
  not a display bug — on a fork, **both** halves of the update flow were inert,
  for two independent reasons found by reading the code rather than assuming.

  1. **`hermes update` deliberately declines to merge.** Upstream already has fork
     support: `_is_fork()` plus `_sync_with_upstream_if_needed()`, called from two
     places in `_cmd_update_impl`. That function fetches upstream and compares
     `origin/main` with `upstream/main`. When the fork has commits of its own it
     prints *"Skipping upstream sync to preserve your changes"* and returns. This
     fork exists to carry its own commits, so that branch was taken every time.
     Clicking Update did nothing but print that notice.
  2. **The update indicator asks the wrong remote.** `checkUpdates()` in
     `apps/desktop/electron/main.ts` measures `HEAD..origin/<branch>` — the
     distance from the remote this checkout **pushes to**. The nightly sync merges
     upstream in and then pushes there, so local and origin end up identical and
     the count is permanently 0. Settings always read "Already up to date", the
     popup never fired, and "Update now" had nothing to do.

  A prior note in `CUSTOMIZATIONS.md` claimed no patch to `hermes_cli/main.py` was
  needed because merging made the destructive reset unreachable. That was true for
  *protecting* the fork and false for *delivering* updates. Both statements are now
  corrected in the registry.

  Two further claims from earlier notes were checked and found wrong, which is why
  the change is smaller than first planned:
  - *"A fork with its own commits always fails a fast-forward."* It fails against
    **upstream**, not against **origin** — and `hermes update` pulls from origin.
    With the sync keeping local and origin level, the `reset --hard` at the heart
    of the original threat model is not reached in normal operation.
  - *"The project dialog and settings are the same 206-commits/90d area."* Measured
    per file: the update-check region of `electron/main.ts` sees 5 changes in 90
    days and the mount region of `main.py` **one in 365 days**. The frightening
    numbers belong to code this patch does not touch.

- **Changed:**
  - `hermes_cli/fork_merge.py` (new) — adapter. Loads `scripts/fork_sync.py` by
    path (that directory has no `__init__.py` upstream and adding one would be an
    upstream edit for no gain) and calls its `sync()`. Decides what happened by
    comparing HEAD before and after, because the engine returns exit code 0 for
    three different situations: merged, already current, and declined because the
    tree was dirty. Only a moved HEAD means the caller should stop. Refuses to run
    when the caller's path and the engine's own `REPO_ROOT` disagree, so it can
    never merge some other repository and report on it. Off switch:
    `HERMES_FORK_MERGE=0` — an environment variable, not a config key, because this
    runs inside the update path and an update is exactly the event that can leave
    config loading broken.
  - `hermes_cli/main.py` — **one additive block (20 lines)** in
    `_sync_with_upstream_if_needed`, inside `if origin_ahead > 0:`, gated on
    `upstream_ahead > 0`. Upstream's six print lines and `return` are untouched
    directly below it as the fallback — not deleted, not re-indented, so an
    upstream edit to them cannot conflict with this patch.
  - `apps/desktop/electron/fork-upstream.ts` (new) + `fork-upstream.test.ts` (new)
    — resolves how far behind the `upstream` remote the checkout is. Dependency-
    injected git runner, so it is unit-tested without a repository. Follows the
    existing one-module-per-concern convention in `electron/` (see
    `update-count.ts`).
  - `apps/desktop/electron/main.ts` — one import line, plus a probe in
    `checkUpdates()` gated on the origin count being 0 so the non-fork path is
    byte-identical. Adds a `forkUpstream` field to the payload naming the ref the
    count came from.
  - `fork/CUSTOMIZATIONS.md` — **R15 rewritten** (see below) and a new registry
    entry.
  - `tests/fork_sync/test_fork_merge.py` (new) — 24 contracts.

- **Impact:** the popup fires when Nous ships. Clicking Update saves nothing,
  destroys nothing, and instead merges upstream into the fork, sends any conflict
  to the AI resolver, verifies with the test suite, installs dependencies and
  rebuilds the desktop app if the merge touched them, then pushes. Same popup,
  same progress window, same buttons.

- **Rule change:** **R15 was rewritten, not deleted.** It previously banned
  patching the update path outright, which is what made the Update button
  permanently inert. Patching is now allowed under four conditions: the upstream
  edit is a *call* and never logic; it degrades to exactly upstream behavior with a
  test asserting the fallback; `hermes_cli/main.py` **stays** on
  `HAND_RESOLVE_ONLY` so no model ever auto-resolves a conflict in the file that
  contains `reset --hard`; and `fork-guard` stays installed as the one protection
  that does not depend on this patch being correct.

- **Risk / watch for:**
  - **A conflict in the `main.py` mount pauses updates.** By design: the sync
    writes a HAND-RESOLVE-REQUIRED report, restores its rollback point, and defers.
    Nothing is lost, but nothing updates until the mount is re-applied by hand.
    That region changed once in 365 days. The registry entry's "If the seam moves"
    field is written for whoever has to do it.
  - **The nightly sync and the button now overlap.** If the 04:00 job keeps
    merging automatically, the popup will rarely have anything to show, because the
    machine is already current by morning. Turning the nightly into check-and-
    notify is the obvious next step and is **not** done here — it is a behavior
    decision, not a defect.
  - **`hermes update` refuses to run while anything uses the venv**
    (`main.py:11342`, exit 2) — a running gateway is enough. The desktop button
    sidesteps it by quitting the app first; a CLI update will not. Unrelated to this
    patch, but it is the first thing that will look like this patch failing.
  - **The desktop change needs a rebuild to be visible.** Until then the backend
    behaves correctly and the UI still shows the old count.
  - The electron probe only runs when the origin count is 0. If upstream ever
    changes `checkUpdates()` to return early before that point, the probe silently
    stops running and the popup goes quiet again — a silent regression, not a crash.

- **Verified:**
  - `venv/Scripts/python.exe -m pytest tests/fork_sync/ -q` → **42 passed**.
  - `npx vitest run electron/fork-upstream.test.ts electron/update-count.test.ts`
    → **19 passed**.
  - `npx tsc -p tsconfig.electron.json --noEmit` → clean. It first caught a real
    defect in the new test file (a call omitting the injected git runner), fixed by
    giving the parameters explicit defaults so a forgetful caller gets `null`
    instead of a `TypeError` inside the update check.
  - **End-to-end on real repositories** (`test_end_to_end_update_merges_upstream_
    and_keeps_fork_work`): three git repos mirroring the live topology — upstream,
    a bare "fork on GitHub", and a clone carrying a pushed fork-only commit — with
    upstream then moving ahead. Calling the real
    `_sync_with_upstream_if_needed()` produced: HEAD moved, upstream's new file
    present, the fork's own file still present, the previous HEAD still an ancestor
    (so the app's later `pull --ff-only` still works), the bare fork updated to the
    new HEAD, and no "Skipping upstream sync" in the output.
  - The mount itself is asserted behaviorally, not by reading source:
    `test_mount_fires_when_fork_has_own_commits` proves `main.py` calls the merge;
    `test_mount_falls_back_to_upstream_message` proves upstream's original three
    messages still print verbatim when it declines;
    `test_mount_survives_a_broken_fork_module` proves an exception cannot break an
    update; `test_mount_does_not_fire_when_upstream_has_nothing` proves the gate.
  - **Not caused by this patch:** `tests/hermes_cli/test_cmd_update.py` and
    `test_update_venv_health.py` report 16 failed / 52 passed. Removing this
    patch's hunk and running the identical command in the same directory gives the
    identical 16 failed / 52 passed. Cause: the venv-holders guard exiting on live
    gateway processes. An earlier comparison in a `git worktree` was a **bad
    control** — a different `PROJECT_ROOT` changes that guard's behavior, and it
    wrongly suggested 12 new failures.

- **Follow-ups:**
  - Decide whether the nightly job becomes check-and-notify so the button drives
    updates. Optionally have it run the engine's existing dry-run (merge, test,
    roll back) so the popup can say whether an update is safe before it is applied.
  - Sync failure alerts are still delivered nowhere.
  - The desktop rebuild has never actually executed on this install: the rebuild
    step landed five minutes after the only merge that would have triggered it, so
    the bundle predates the source it runs. First real sync after this change will
    perform it.
  - Running `tests/hermes_cli/test_cmd_update.py` on Windows **spawns real
    `hermes gateway run` processes** — the test does not stub the "resume gateways
    after update" step. Four runs left 56 strays holding 590 MB. Do not run that
    file on this machine without cleaning up after it.
