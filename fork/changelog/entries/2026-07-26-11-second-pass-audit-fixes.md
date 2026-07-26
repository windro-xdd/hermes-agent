# Fixed: second-pass audit — one real bug plus documentation drift

**Date:** 2026-07-26
**Type:** Fixed

- **Patch id:** `fork-aware-update`, `fork-sync`
- **Why:** an independent second audit re-verified the fixes from
  [entry 10](./2026-07-26-10-audit-corrections-to-fork-aware-update.md). All three
  code fixes and all three verification numbers held up exactly. It found one real
  bug that would have defeated the feature, and — pointedly — that the commit whose
  argument was "documentation matters as much as code" had itself shipped a stale
  number and left its own file's contract false.

- **Changed:**

  **1. Real bug: a failed SHA lookup produced a count with no target.**
  `resolveForkUpstreamStatus()` ignored the exit code of
  `git rev-parse upstream/<branch>`. If the count succeeded but the SHA lookup
  failed, the payload carried `behind: 43` with `targetSha: ''` — and the renderer
  refuses to raise the update notification without a `targetSha`. Net effect: the UI
  claims 43 commits behind and the popup stays silent, which is exactly the failure
  this whole patch exists to remove. It also overwrote origin's perfectly good
  `targetSha` with an empty string. Now a missing or empty SHA means "report
  nothing" and origin's answer stands. Three tests added, including one asserting
  origin's SHA survives.

  **2. `fork_merge.py`'s own degradation contract was false.** The module docstring
  said *"Every failure path returns False, which makes the caller fall through to
  upstream's original behavior."* Two failure outcomes return **True** on purpose —
  `FAILED` and `CRASHED` — because the engine has already reported and adding
  upstream's "Skipping upstream sync" on top would mislead. The docstring now states
  the real contract for both values. This is the paragraph a resolver reads to decide
  what the mount is allowed to do.

  **3. The handled-failure message over-promised.** It said *"Your customizations are
  intact"*. Every nonzero return from the engine does call `rollback()` first, and
  rollback never uses `reset --hard`, so no fork **commit** can be lost — that part
  is guaranteed. But `rollback()` checks none of its own git return codes and handles
  partial failure by printing a warning about leftover modified files. So the engine
  could warn that the tree is still modified while the mount said everything was
  fine. Now it promises only what is guaranteed (no commits lost, previous build
  still running) and points at the engine's recovery line for the rest.

  **4. Stale line count, in the commit that was fixing stale claims.** The registry
  said `hermes_cli/main.py +20/−0`; entry 10's own changes made it +23/−0. Corrected,
  and the registry now specifies the **merge-base** three-dot method — a two-dot diff
  against `upstream/main` attributes upstream's 66 newer commits to the fork and
  reports 111 changed files instead of the real 21.

  **5. `push_after_update()`'s docstring still stated the abandoned design.**
  Entry 10 deleted that reasoning from one comment block and left an identical copy,
  presented as current intent, in the function 15 lines below that the comment points
  the reader to. Rewritten, with the abandoned rationale explicitly labelled as
  history.

  **6. A test was passing for the wrong reason.**
  `test_mount_survives_an_unimportable_fork_module` asserted only that upstream's
  fallback message printed — a line that printed even before the mount existed. It
  had no fail-before value for the contract it was presented as locking. It now
  asserts the failure is reported once, matching its sibling.

  **7. Off-switch test coverage did not match its claim.** Entry 10 said both halves
  are "tested against the same list"; the desktop list included case and whitespace
  variants and the Python one did not, leaving `.strip().lower()` untested. The
  Python parametrize now covers the same values plus unrecognized ones.

  **8. Dead parameter removed.** `shouldProbeUpstream({ originBehind, supported })`
  — the `supported` flag was dead in production, since `withForkUpstreamStatus`
  rejects unhealthy payloads before calling it. Dropped, with the reason recorded.

  **9. New known limitation documented.** The Electron main process snapshots its
  environment at launch, so exporting `HERMES_FORK_MERGE=0` in a shell disables the
  Python merge on the next update but leaves an already-running app's probe active
  until restart — the half-wired state reachable by a different route. Recorded in
  the registry; the remedy is to restart the app after flipping the switch.

- **Impact:** the update popup can no longer go silent while reporting commits
  behind. Failure messages no longer claim more than the engine guarantees.

- **Risk / watch for:**
  - The two off-switch implementations are still separate code in two languages.
    They now agree on every tested value, with one theoretical exception: JS
    `trim()` strips a BOM (`\uFEFF`), Python `strip()` does not, so
    `HERMES_FORK_MERGE=$'\ufeff0'` would disable one half only. Not worth code.
  - `resolveForkUpstreamStatus` now requires a usable SHA. If a future upstream
    rename makes `rev-parse <remote>/<branch>` fail routinely, the feature degrades
    to silent rather than loud. The tests pin the behavior, not the symptom.
  - `apps/desktop/electron/fork-upstream.ts` exports six symbols and `main.ts`
    imports one; the other five are test-only. That is deliberate (the module is
    unit-tested through its pure parts) but it is surface that no production caller
    protects.

- **Verified:**
  - `venv/Scripts/python.exe -m pytest tests/fork_sync/ -q` → **52 passed**
    (44 before this round).
  - from `apps/desktop`:
    `npx vitest run electron/fork-upstream.test.ts electron/update-count.test.ts`
    → **30 passed** (26 before).
  - `npx tsc -p tsconfig.electron.json --noEmit` → clean.
  - `git diff $(git merge-base upstream/main HEAD)..HEAD --numstat` → the fork
    changes 21 files, of which exactly **two** exist at the merge base:
    `hermes_cli/main.py` (+23/−0) and `apps/desktop/electron/main.ts` (+9/−1).
  - The second audit independently re-traced the crash path and confirmed no route
    exists where the engine raises and the user is told the tree is intact. It also
    confirmed `sync()` contains no `except` or `raise`, so it cannot swallow an
    exception and return nonzero (which would have routed a crash to the gentler
    `FAILED` message).

- **Follow-ups:**
  - A `BaseException` — Ctrl-C during an update — still escapes all three
    `except Exception` layers into `hermes update`. Pre-existing, unchanged.
  - `forkUpstream` in the update payload is diagnostic only; nothing in the desktop
    app reads it.
  - Sync failure alerts are still delivered nowhere. Unchanged, and still the
    biggest gap in the whole system.
