# Fixed: audit corrections to the fork-aware update patch

**Date:** 2026-07-26
**Type:** Fixed

- **Patch id:** `fork-aware-update`, `fork-sync`
- **Why:** an adversarial audit of the two commits merged earlier today
  (`ab2377e85`, `8c9d8e0b1`) against rules R1–R15 found five real defects. Three
  were in the code, two in the documentation — and the documentation ones matter as
  much, because `fork/CUSTOMIZATIONS.md` is what the AI resolver reads when it has
  to re-apply a mount after a conflict. A registry that overstates what a patch does
  is a resolver working from a wrong description.

  This entry supersedes the corresponding claims in
  [`2026-07-26-09-fork-aware-update.md`](./2026-07-26-09-fork-aware-update.md).
  That entry is left as written (R9: append-only), so read this one alongside it.

- **Changed:**

  **1. The off switch only disabled half the feature (R7 violation).**
  `HERMES_FORK_MERGE=0` stopped the Python merge but not the desktop probe. Result:
  the popup still advertised upstream commits while the Update button did nothing —
  the exact half-wired state R7 calls worse than no customization. The switch is now
  honored by both halves: `isForkMergeEnabled()` in
  `apps/desktop/electron/fork-upstream.ts` mirrors `is_enabled()` in
  `hermes_cli/fork_merge.py`, value for value, and when off the probe does not even
  invoke git. R15 condition 2 now states this requirement explicitly.

  **2. The crash path told the user their tree was safe when nobody knew.**
  `merge_upstream_into_fork()` printed *"Your customizations are intact and the
  previous build is still running"* for every failure — including the path where the
  engine *raised*. `record_crash()` deliberately does not roll back (the tree state
  is unknown at crash time), so that reassurance was false in precisely the one case
  where it mattered. Added a distinct `CRASHED` outcome: it now says the repository
  was **not** rolled back and points at `python scripts/fork_sync.py status`.

  **3. The mount in `main.py` swallowed exceptions silently.** `except Exception:
  pass` made a permanently broken fork module indistinguishable from one that simply
  declined. R6 requires a customization to log once and hand control back; it now
  prints the reason once and falls through.

  **4. `checkUpdates()` no longer needs editing at all.** The first version rewrote
  two lines inside that function's return object, in a region upstream touches
  ~5×/90d. It has exactly one call site, so the fork logic moved there:
  `withForkUpstreamStatus()` decorates the payload at the
  `ipcMain.handle('hermes:updates:check', …)` handler. `checkUpdates()` is now
  byte-identical to upstream, and `apps/desktop/electron/main.ts` is +9/−1 where the
  single removal is the wrapped call itself.

  **5. The churn figure justifying the R15 rewrite was measured inconsistently.**
  R15 and the registry claimed the mount region saw "one upstream change in 365
  days". That was `git log -L` over the 9 lines of the mount, while the paired
  electron figure in the same sentence was measured over a whole function. Measured
  consistently: the enclosing `_sync_with_upstream_if_needed` saw **6** changes in
  365 days, of which one is the function's own creation and two are a Windows fix
  plus its revert — **3 substantive changes in a year**. Both numbers and the method
  are now stated in R15. The conclusion (low, bounded cost) survives; the number
  quoted to support it did not.

  **6. The `fork-sync` registry entry's "Degrades to" field was false (R3).**
  It still promised "any failure → rollback tag restored". `record_crash()`, added in
  `ab2377e85`, explicitly does not roll back. That commit should have updated the
  registry in the same commit; it did not. Corrected now, with the crash caveat
  spelled out.

  **7. Stale comment block removed from `scripts/fork_sync.py`.** A 20-line
  "DO NOT PUSH HERE" header described an abandoned design in which the sync
  deliberately left origin behind so the native popup would fire. This change is
  what made that obsolete, and the resolver reads that file. Replaced with what the
  code actually does and a note on what was abandoned and why.

  **8. Smaller doc corrections.** The fallback block was described as "six print
  lines" and elsewhere "three messages"; it is five `print()` calls (the first bare)
  plus a `return`. The claim that `hermes update` reaches
  `_sync_with_upstream_if_needed` "only when the origin count is zero" was wrong —
  there are two call sites; the second is unreachable while the sync pushes last,
  and would cost duplicated work rather than a wrong result. Both corrected in
  `fork_merge.py`'s docstring and in the registry.

- **Impact:** none visible while the feature is on and working. Switching it off now
  genuinely returns upstream behavior, and a crash during an update no longer lies
  about the state of the repository.

- **Risk / watch for:**
  - The two off switches are separate implementations of the same rule in two
    languages. If one gains a value the other lacks they will disagree. Both are
    tested against the same list.
  - `withForkUpstreamStatus()` runs on every update check where origin found nothing
    — app start, window focus (5-minute throttle), and every 30 minutes — and each
    run adds one `git fetch --quiet upstream <branch>`. `runGit` has no timeout, so
    an unreachable upstream host relies on git's own DNS/connect timeouts.
  - `apps/desktop/electron/main.ts` is **deliberately not** on
    `HAND_RESOLVE_ONLY`, unlike `hermes_cli/main.py`. A conflict there will be
    AI-resolved, and a bad resolution silently stops the popup rather than breaking
    anything — visible and recoverable. Listing it would defer real syncs several
    times a year. The asymmetry and its reasoning are now written into R15.

- **Verified:**
  - `venv/Scripts/python.exe -m pytest tests/fork_sync/ -q` → **44 passed**
    (was 42; +2 for the crash-message and unimportable-module contracts).
  - from `apps/desktop`:
    `npx vitest run electron/fork-upstream.test.ts electron/update-count.test.ts`
    → **26 passed** (was 19; +7 for the shared off switch and the call-site wrapper).
  - `npx tsc -p tsconfig.electron.json --noEmit` → clean.
  - `git diff upstream/main -- apps/desktop/electron/main.ts` → **+9/−1**, and the
    diff confirms `checkUpdates()` itself is untouched.
  - The off-switch fix is asserted by `test_off_switch_values` (Python) and
    `'the off switch disables the probe entirely'` (desktop), which also asserts the
    probe makes **zero** git calls when disabled.

- **Follow-ups:**
  - **Acknowledged R10 violation, not fixed:** the crash fix (`fix/`, patch id
    `fork-sync`) and the feature (`feat/`, patch id `fork-aware-update`) rode the
    same branch, so the merge commit names only the feature. Splitting them now would
    mean rewriting merged history, which R11's spirit and R10's own "never
    force-push a merged branch" both argue against. Recorded here instead.
  - `tsconfig.electron.json` has `strict: false` and `noImplicitAny: false`, and
    `fork-upstream.ts` carries no type annotations — matching its neighbour
    `update-count.ts` (R5). "Typecheck clean" therefore proves less than it sounds;
    the behavior is covered by the unit tests instead.
  - The desktop must be rebuilt for the mount move to take effect. The previous
    rebuild built the superseded version.
