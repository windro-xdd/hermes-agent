# Infrastructure: fork topology, full history, and rerere

**Date:** 2026-07-25  
**Type:** Infrastructure

- **Patch id:** `—` (repo setup, no code patch)
- **Why:** the install was a plain auto-updating clone of upstream. To keep
  customizations across updates it needs a fork it can push to, and a real commit
  history to rebase against.
- **Changed:**
  - Remote `origin` (was NousResearch) renamed to `upstream`; `origin` now points
    at `windro-xdd/hermes-agent`. Update detection reads `upstream/<branch>`, so
    the native popup keeps working — this rename is what preserves it.
  - Unshallowed the repository. The installer had cloned with `--depth 1`, leaving
    30 grafted boundaries, no parent on `HEAD`, and no merge-base with upstream.
  - Fast-forwarded the checkout 233 commits to upstream `main` (`9f38478` →
    `07e97d2`), then rebuilt the desktop app.
  - Enabled `rerere.enabled` and `rerere.autoupdate`.
  - Configured repo-local git identity (none was set; commits would have failed).
  - Discarded two CRLF-only diffs (`website/tsconfig.json`,
    `website/static/img/favicon.svg`) and a CRLF-only `package-lock.json` rewrite
    left by `npm install`. No content change in any of them; committed, they would
    have been permanent conflict surface.
- **Impact:** none visible. The app runs current upstream code; `~/.hermes` config,
  plugins, and prompts untouched.
- **Risk / watch for:**
  - The shallow clone was the real blocker — `git rebase upstream/main` cannot work
    without a merge-base. **If a future reinstall re-clones shallow, unshallow
    before syncing** or the whole update system silently fails.
  - `main` now tracks `origin/main`, not upstream. Harmless: detection hardcodes a
    fetch from `upstream` for `branch == "main"` rather than reading the tracking
    ref (verified by reading the code). If upstream ever switches detection to use
    the tracking ref, this becomes a real bug — symptom would be the popup
    reporting "up to date" forever.
  - Line numbers in any plan or doc are already stale by ~350 lines after the
    catch-up. Always re-locate seams by searching for symbols.
- **Verified:** `git rev-parse --is-shallow-repository` → `false`;
  `git merge-base HEAD upstream/main` resolves; 17,806 commits reachable;
  `upstream/main..HEAD` = 0 before the first fork commit; working tree clean;
  `git bundle verify` on the pre-change backup → "records a complete history";
  desktop rebuild produced a fresh `Hermes.exe` + `app.asar` with no source file
  newer than the build.
- **Follow-ups:** `hermes-sync` engine (Phase 1 Task 3), then the updater mount
  (Task 4), then an induced-conflict dry run (Task 5).
