# CHANGELOG-FORK.md — windro's fork of Hermes Agent

Every change made **in this fork** gets an entry here. Upstream's own changes are
not logged — this file is the fork's history only, so it stays readable and short.

Companion to [`CUSTOMIZATIONS.md`](./CUSTOMIZATIONS.md), and the division matters:

| File | Answers |
|---|---|
| `CUSTOMIZATIONS.md` | What exists **right now**, and how it's wired. Rewritten in place as patches change. |
| `CHANGELOG-FORK.md` | **When and why** it changed. Append-only history. Never rewritten. |

Required by rule **R9**. Entry goes in the **same commit** as the code — an entry
written later gets written from memory, and memory is where the "why" gets lost.

---

## What needs an entry

Anything that changes behavior or the shape of the fork:

- **Added** — a new feature or capability
- **Fixed** — a bug fix, ours or a patch on top of upstream's
- **Customized** — a modification to built-in Hermes behavior (the common case here)
- **Changed** — a rework of an existing fork patch
- **Retired** — a patch removed, usually because upstream absorbed it
- **Infrastructure** — repo/tooling/topology changes that affect how updates work
- **Upstream sync** — a `hermes-sync` run that required conflict resolution, with
  what the AI resolved. Clean fast-forward syncs need no entry.

Not logged: typo fixes in comments, formatting, or anything with no behavioral or
structural effect.

---

## Entry format

```markdown
### YYYY-MM-DD — <type>: <one-line summary>
- **Patch id:** `<id>` (matches CUSTOMIZATIONS.md; `—` if not a registered patch)
- **Why:** the problem or request that caused this. Not a restatement of the what.
- **Changed:** files, and what each one does now.
- **Impact:** what a user notices. `none (internal)` is a valid answer.
- **Risk / watch for:** how this could break later, and the symptom it would show.
- **Verified:** the actual command run and its result. Not "should work".
- **Follow-ups:** known gaps, deferred work, related changes expected next.
```

Rules for entries:

- **Newest at the top**, directly under this section.
- **Append-only.** Correct a past entry with a new one that supersedes it; do not
  edit history. The point of a changelog is that it's trustworthy.
- **"Why" must survive without context.** Assume the reader has no memory of the
  conversation that produced the change — because in six months, nobody will.
- **"Verified" means it was actually run.** Paste the real outcome. An unverified
  entry is worse than none, because it manufactures false confidence.
- **"Risk / watch for" is the forward-looking field (R8).** If a change depends on
  an upstream seam, an assumption, or a version quirk, name it. This is what makes
  a future failure diagnosable in minutes instead of hours.

---

## Log

### 2026-07-26 — Fixed: six defects in the sync engine, all found by running it
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

### 2026-07-26 — Added: automatic upstream sync with AI conflict resolution
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

### 2026-07-25 — Infrastructure: fork topology, full history, and rerere
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

### 2026-07-25 — Added: fork rulebook, patch registry, and this changelog
- **Patch id:** `customizations-registry`
- **Why:** an update that rebases custom patches onto new upstream code will hit
  conflicts. A diff shows *what* a patch changed but never *why*, and without the
  why, a resolver — human or model — resolves by guesswork and quietly drops the
  customization. Rules also needed writing down so customizations are built to
  survive surrounding change instead of being rewritten every update.
- **Changed:**
  - `CUSTOMIZATIONS.md` (new) — three-layer model, 14 project rules, the registry
    with a mandatory entry template, and the two existing user-data customizations
    recorded as resolver context. Sent to `claude-opus-5` on every conflict.
  - `CHANGELOG-FORK.md` (new) — this file.
- **Impact:** none at runtime. Documentation and process only.
- **Risk / watch for:** these files are only as good as the discipline behind them.
  A patch landed without a registry entry is invisible to the resolver, and the
  first conflict involving it is the one that loses work. If upstream ever adds a
  file at either of these paths, rename ours and update the path inside
  `hermes-sync` — the resolver reads `CUSTOMIZATIONS.md` by a hardcoded path.
- **Verified:** committed as `d816a855` and pushed; `git ls-remote origin` confirms
  the fork tip matches. Registry entry for `updater-hermes-sync` written ahead of
  the code so its seam and hand-resolve requirement are recorded before the
  sensitive edit is made.
- **Follow-ups:** rules R6/R7 (resilience, end-to-end wiring) are currently
  unenforced by tooling — they hold by review only. If the fork grows past a
  handful of patches, consider a pre-commit check that a touched patch id appears
  in both docs.
