# CUSTOMIZATIONS.md — windro's fork of Hermes Agent

This file is the **rulebook and registry** for this fork. It has two readers:

1. **Humans** (windro, and any agent doing work here) — the rules that govern how
   a customization may be built, so updates stay cheap and safe.
2. **`claude-opus-5`, automatically** — `hermes-sync` sends this whole file to the
   model as context when a git rebase conflict has to be resolved during an
   upstream update. The model uses it to understand *what each patch was trying to
   do* so it can re-express that intent against new upstream code instead of
   guessing from the diff.

Because a model reads this, entries must state **intent and mount point**, not just
"changed file X". A registry entry that only lists files is useless during a
conflict.

---

## The three layers

| Layer | Where | Who edits it |
|---|---|---|
| **Upstream** | `upstream` remote = `NousResearch/hermes-agent` | Nobody here. Never edited. |
| **This fork** | `origin` remote = `windro-xdd/hermes-agent`, branch `main` | Customizations, as small documented commits. |
| **User data** | `%LOCALAPPDATA%\hermes` (config, plugins, prompts, sessions) | Freely. Updates never touch it. |

An update is: fetch upstream → replay this fork's commits on top → verify → push.
Nothing else. If a change can live in **user data** instead of the fork, it belongs
in user data — that is the zero-conflict option.

---

## Project rules

### R1 — Logic in new files; mount with the smallest possible hunk
A customization is a **new file** containing the real logic, plus the minimum edit
to an upstream file needed to reach it — ideally one line (an import, a call, a
registration). Rationale: git conflicts happen line-by-line inside changed files.
A 200-line change inside `cli.py` conflicts on every upstream refactor. A new
module plus a one-line call almost never does.

### R2 — One customization, one commit, intent-rich message
Never bundle two unrelated changes. The commit message is what the resolver model
reads, so write **why**, not what:

```
custom(<id>): <one-line what>        # or fix(<id>): / feat(<id>):

Why: <the actual problem this solves>
Mount: <file:symbol where it hooks into upstream>
Depends on: <the upstream contracts this needs to keep working>
Re-apply: <what to do if upstream moves or deletes that seam>
```

### R3 — Every customization gets an entry in this file and the changelog
Both added in the same commit as the code. No entry, no merge. The registry entry
(this file) describes the patch as it exists **now**; the changelog entry records
**when and why** it changed.

### R4 — Additive over destructive
Prefer adding a branch, a wrapper, or a new function over rewriting or deleting
upstream logic. Deleting upstream code guarantees a conflict when upstream touches
that code, and makes the resolver's job ambiguous.

### R5 — Match upstream's existing style and APIs
Use the libraries, patterns, error handling, and naming already in the file being
touched. Do not introduce a new dependency for a customization.

### R6 — Build it to outlive the thing it hooks into
A customization must keep working when *related* code changes around it. It is not
enough that it works today. Concretely:

- **Depend on the stable thing, not the incidental thing.** Hook a named function,
  a documented config key, or a public interface — not a line number, a string
  literal in a log message, a private `_helper`, a CSS class name, or the current
  order of a list.
- **Degrade, don't explode.** If the seam is missing, the customization logs once
  and returns control to upstream. A customization must never be able to take the
  app down — the worst case is "my feature is inactive", never "Hermes won't
  start". Wrap the mount in a guard that tolerates absence.
- **Assume the data will grow.** Handle a config key that isn't set, a model that
  isn't in the map, a list with zero or many entries, a value of a new type. No
  hardcoded counts, no assumption of exactly one of anything.
- **Feature-detect instead of version-checking.** Ask "does this function exist /
  does this key exist" rather than "is this version ≥ X".
- **No copy-paste of upstream logic.** Duplicated logic silently goes stale when
  upstream fixes a bug in the original. Call upstream's function; wrap it if you
  must change behavior.
- **Isolate the blast radius.** One customization must not be required for another
  to work. If two need shared logic, that shared logic is its own new module both
  import — not one reaching into the other.

### R7 — Wire it properly, end to end
A half-wired customization is worse than none, because it looks present and
behaves inconsistently. Before a customization is considered done, every layer it
touches must be connected and reachable:

- The setting exists in config **and** has a sane default when absent.
- The backend honors it **and** the UI reflects the real value (not a hardcoded
  placeholder).
- It applies on **every** path that should see it, not just the one path tested.
  Find the sibling call sites.
- It survives a restart, a profile switch, and a fresh install with no prior state.
- Turning it off returns exactly upstream behavior, with nothing left behind.
- It is reachable the way a user would actually reach it, not only via internals.

### R8 — Document forward, not just backward
Docs record what a future change will need to know, not only what was done. Every
registry entry answers: what would break this, what to do when it breaks, what
related work is expected next, and what was deliberately *not* done and why. A
rejected alternative is worth writing down — without it, someone re-litigates the
same decision in six months, or "fixes" something that was a conscious tradeoff.
When a customization touches an area with known upstream churn, say so.

### R9 — Every change gets a changelog entry, in the same commit
**Where:** `fork/changelog/entries/YYYY-MM-DD-NN-short-slug.md` — **one file per
change**, never appended to a shared file. Add a row to the index table in
`fork/changelog/README.md` in the same commit. A flat shared log collides on every
concurrent edit and grows until nobody reads it; separate files cannot conflict
with each other.

**When:** anything that changes behavior or the shape of the fork —

| Type | For |
|---|---|
| `Added` | a new feature or capability |
| `Fixed` | a bug fix, ours or on top of upstream's |
| `Customized` | a modification to built-in Hermes behavior (the common case) |
| `Changed` | a rework of an existing fork patch |
| `Retired` | a patch removed, usually because upstream absorbed it |
| `Infrastructure` | repo/tooling/topology changes affecting how updates work |
| `Upstream sync` | a sync that needed conflict resolution, and what the AI resolved |

Not logged: comment typos, formatting, anything with no behavioral or structural
effect. A clean sync with no conflict needs no entry.

**Required fields** — all of them, every entry:

```markdown
# <Type>: <one-line summary>

**Date:** YYYY-MM-DD
**Type:** <one of the table above>

- **Patch id:** matches the registry below (`—` if not a registered patch)
- **Why:** the problem or request that caused this — not a restatement of the what
- **Changed:** files, and what each one does now
- **Impact:** what a user notices (`none (internal)` is valid)
- **Risk / watch for:** how this could break later, and the symptom it would show
- **Verified:** the actual command run and its real output
- **Follow-ups:** known gaps, deferred work, related changes expected next
```

**Rules for entries:**
- **Same commit as the code.** An entry written later is written from memory, and
  memory is where the "why" is lost.
- **Append-only.** Correct a past entry by adding a new one that supersedes it;
  never edit history. A changelog you rewrite is a changelog nobody can trust.
- **"Why" must stand alone.** Assume the reader has no memory of the conversation
  that produced the change — in six months, nobody will.
- **"Verified" means it was actually run.** Paste the real result. An unverified
  entry is worse than none: it manufactures false confidence.
- **"Risk / watch for" is the forward-looking field (R8).** Name the upstream seam,
  assumption, or platform quirk this depends on. It is what turns a future failure
  into a five-minute diagnosis.

**No entry, no merge.** Enforced by review, plus
`tests/fork_sync/test_fork_sync_contract.py::test_changelog_entries_exist`, which
fails if an entry file is not linked from the index.

### R10 — Every change goes through a branch, never straight to `main`
No commit lands directly on `main`. One branch per change, merged back only when
it is complete and verified.

**Branch naming** — type, then a short slug:

| Prefix | For |
|---|---|
| `fix/` | a bug fix |
| `feat/` | a new capability |
| `custom/` | a modification to built-in Hermes behavior |
| `docs/` | documentation only |
| `chore/` | tooling, deps, repo mechanics |

e.g. `fix/rebuild-path`, `custom/model-prompts-in-settings`.

**The workflow:**

```bash
git switch -c fix/<slug> main       # branch from a clean, current main
# ... make the change, plus its registry entry (R3) and changelog entry (R9)
git switch main
git merge --no-ff fix/<slug>        # --no-ff keeps the branch visible in history
git branch -d fix/<slug>            # delete once merged
```

`--no-ff` is deliberate: a fast-forward merge erases the fact that the work was a
unit, which defeats the point of branching. The merge commit is the record of what
shipped together.

**Before merging back**, all of these must hold:
- the change is complete — no "will finish on main"
- `python -m pytest tests/fork_sync/ -q` passes (and any other relevant suite)
- registry entry updated (R3) and changelog entry written (R9), in the branch
- `main` has not moved underneath you; if it has, merge `main` into the branch
  first and re-verify

**Never** force-push a branch that has been merged, and never rebase `main`
itself — see R11 and the merge-not-rebase reasoning under `fork-sync`.

**One interaction to know about (R10 ↔ `fork-sync`):** the automatic sync merges
`upstream/main` into `main` and requires a clean tree with no tracked
modifications. An in-progress feature branch is fine — the sync only touches
`main` — but **uncommitted work on any branch defers the sync** with
"N uncommitted change(s) in the repo". So commit or stash before leaving work
overnight, or the 04:00 sync silently skips. It reports this and retries the next
day; nothing is lost, but updates stall until the tree is clean.

**Exception, narrow:** a single-commit hotfix to an actively broken install may go
straight to `main`, and only when a branch would prolong the outage. It still needs
its registry and changelog entries in the same commit.

### R11 — Never `reset --hard` this fork
It silently destroys fork commits. Not in scripts, not in the patched updater, not
manually. Recovery point before any risky git operation is a **tag**, and the
recovery move is `git rebase --abort` or checking out the tag — never a hard reset.

### R12 — No AI-resolved conflict is trusted until it builds
A conflict resolved by the model is a *proposal*. It is accepted only after the
desktop build and typecheck pass. Failure, or the model reporting low confidence,
means abort, roll back, and notify. A failed update **defers**; it never ships a
broken tree.

### R13 — Secrets stay out of the repo
API keys, tokens, and personal paths live in user data or the environment. This
fork is on GitHub.

### R14 — `git rerere` stays enabled
`rerere.enabled` and `rerere.autoupdate` are on. Git remembers how a conflict was
resolved and replays it automatically next time, so a recurring conflict costs one
model call ever, not one per update.

### R15 — The update path may be patched, but only on these terms
**Superseded 2026-07-26.** The original rule said the update path must never be
patched at all. That was written when the sync was expected to be invisible; it
made the native Update button permanently inert, because `hermes update`
deliberately refuses to merge upstream into a fork that carries its own commits
(`_sync_with_upstream_if_needed`, the `origin_ahead > 0` branch). The fork's whole
purpose is carrying its own commits, so "never patch the update path" meant
"updates never arrive through the UI".

Patching it is now allowed, under four conditions. They exist because the risk the
old rule was protecting against is real: a wrong edit here can break the ability
to update at all.

1. **The upstream edit is a call, never logic.** The fork's code lives in a new
   file (`hermes_cli/fork_merge.py`, `apps/desktop/electron/fork-upstream.ts`).
   The edit inside an upstream file is the smallest possible mount that reaches
   it, and it must be *additive wherever additive is possible* — upstream's
   original lines stay in place as the fallback, not deleted and not re-indented.
   Where a single existing line genuinely must change (wrapping a call), that is
   allowed, but exactly one line and never a block. Measured against
   Shape, not counts: `hermes_cli/main.py` is **one added block with zero
   removals**; `apps/desktop/electron/main.ts` is **one added import plus one
   wrapped line**, and that wrapped line is its only removal. Raw line counts are
   deliberately not quoted here — they went stale three times in one session, each
   time in the commit that was fixing the previous stale number. Get the current
   figures with:

   ```bash
   git diff $(git merge-base upstream/main HEAD)..HEAD --numstat -- \
     hermes_cli/main.py apps/desktop/electron/main.ts
   ```

   Use the **merge base**, never a two-dot diff against `upstream/main`: that
   attributes upstream's own newer commits to the fork (111 files instead of 21
   when measured while 66 commits behind).
2. **It degrades to exactly upstream behavior.** If the fork module is missing,
   switched off, throws, or declines, the original code path runs unchanged. A
   test asserts the fallback, not just the happy path. **Every mount honors the
   same off switch.** A switch that disables one half and leaves the other running
   produces the half-wired state R7 forbids — for this patch that would be a popup
   advertising commits over a button that does nothing.
3. **`hermes_cli/main.py` stays on `HAND_RESOLVE_ONLY`.** A conflict there is
   never AI-resolved: it is the file that contains `reset --hard`, and a subtly
   wrong resolution is the one failure this project exists to prevent. The sync
   defers, writes a HAND-RESOLVE-REQUIRED report with the hunks, and restores the
   rollback point. Nothing is lost; updates pause until someone re-applies the
   mount — and the registry entry below tells a future agent exactly how.

   **Churn at that mount, measured with `git log -L` against `upstream/main`
   (corrected 2026-07-26 after an audit found the first figure was measured on a
   narrower range than its paired number):** the 9 lines the mount sits in saw
   **1** change in 365 days; the enclosing `_sync_with_upstream_if_needed` saw
   **6**, of which one is the function's own creation and two are a Windows fix
   and its revert — so **3 substantive changes in a year**. Low, bounded, not
   "one".

   **`apps/desktop/electron/main.ts` is deliberately NOT on the list.** That is a
   considered asymmetry, not an oversight: its mount region changes ~5×/90d, so
   listing it would defer real syncs several times a year for a file whose worst
   failure mode is *the popup goes quiet* — visible, non-destructive, and
   recoverable. `main.py` holds the destructive reset; that is the difference.
4. **`fork-guard` stays installed.** It is the only protection that does not
   depend on the patch being correct: if a resolution ever silently drops the
   mount and an update falls back to `reset --hard`, the hook refuses the ref
   move and the update fails loudly instead of eating fork commits.

The installers (`scripts/install.ps1`, `scripts/install.sh`) were deliberately
**not** patched. Their destructive reset only fires when the local checkout has
diverged from *origin* — not from upstream — which the sync never causes, and
`fork-guard` covers the remaining edge. Patching them would add a second
hand-resolve-only surface for no gain.

---

## Registry

### Entry template
Every core patch gets a section in this shape. The last four fields are what make
this file useful to a resolver model and to future work — do not skip them.

```markdown
#### `<id>` — <one-line what>
- **Status:** active | planned | retired (retired: why, and when)
- **Files:** new files (the logic) / upstream files (the mount, with the symbol)
- **What:** the behavior change, from a user's point of view.
- **Why:** the problem. Enough that someone can judge whether it still applies.
- **Mount:** exactly where and how it hooks in — `file:symbol`, and what kind of
  seam it is (config key, middleware hook, function call, UI registration).
- **Depends on:** the upstream contracts it needs. This is the fragility list.
- **If the seam moves:** what to do when upstream refactors it away. The single
  most valuable field during a conflict.
- **Degrades to:** what happens when the seam is missing — must be a safe
  no-op, never a crash (R6).
- **Next / related:** planned follow-ups, and other patches that touch this area.
- **Deliberately not done:** rejected alternatives and why (R8).
```

### Core patches on this fork

#### `customizations-registry` — fork rulebook, patch registry, changelog
- **Status:** active
- **Files:** `fork/CUSTOMIZATIONS.md` + `fork/changelog/` (index + one file per
  change). No upstream file touched.
- **What:** documents the fork's rules and every customization.
- **Why:** a diff does not record intent; without intent, a rebase conflict gets
  resolved by guesswork and a customization silently dies.
- **Mount:** none — root-level new file, conflict-free by construction.
- **Depends on:** nothing.
- **If the seam moves:** everything lives under `fork/`, a directory upstream does
  not use, so a name collision is now very unlikely. `fork_sync.py` resolves the
  file through a candidate list (current path, then the historical root path), and
  a contract test asserts it is findable — a stale path would otherwise make the
  resolver work *blind* rather than fail.
- **Degrades to:** n/a — documentation.
- **Next / related:** `fork/changelog/` (companion history); this file is read by
  `fork-sync` and sent to the resolver as its only context about patch intent.
- **Deliberately not done:** not placed in `docs/` — upstream owns and churns that
  directory. `fork/` is a namespace upstream does not use, so fork docs cannot
  collide with upstream's. Also not left as flat root files: a single shared
  changelog collides on every concurrent edit and grows until unread.

#### `fork-sync` — keep the fork current with upstream, losing nothing
- **Status:** active
- **Files:** `scripts/fork_sync.py` (the engine), `scripts/fork-guard.sh` (the
  backstop hook), `tests/fork_sync/test_fork_sync_contract.py` (14 behavior
  contracts), plus `%LOCALAPPDATA%\hermes\fork-sync-run.cmd` (Task Scheduler
  wrapper, outside the repo). **Zero upstream files modified.**
- **Scheduling:** Windows Task Scheduler task `Hermes Fork Sync`, daily 04:00.
  NOT Hermes cron — `hermes cron list` warns that jobs never fire without the
  gateway installed, and it is not installed here.
- **Order of operations (this order IS the invariant):** merge → verify → deps
  (only if manifests changed) → desktop rebuild (only if `apps/desktop/` changed)
  → **then** push. This checkout *is* the running install, so the sync performs
  the catch-up steps itself. Pushing earlier drives
  `rev-list HEAD..origin/<branch>` to 0, which makes `hermes update` return early
  and skip exactly those steps — see CHANGELOG defect 6.
- **What:** a scheduled job merges new upstream commits into the fork, resolves
  any conflict with `claude-opus-5`, verifies, and pushes. Hermes's own update
  popup then fires normally and clicking Update is a plain fast-forward of
  already-verified code.
- **Why:** the stock path runs `git reset --hard origin/<branch>` on divergence,
  destroying every fork commit. Three sites do this, not one:
  `hermes_cli/main.py:11540`, `scripts/install.ps1:1524`, `scripts/install.sh:1226`.
  install.ps1's own comment says it exists to discard "local-only commits".

- **MERGE, NOT REBASE — the load-bearing decision.** The original plan said
  rebase. Sandbox testing showed rebase *creates* the hazard:

  | strategy | old local sha still an ancestor of the fork tip? | app's `pull --ff-only` |
  |---|---|---|
  | rebase | **NO** (SHAs rewritten) | **fails → triggers `reset --hard`** |
  | merge  | **YES** | succeeds; reset unreachable |

  Failing `pull --ff-only` is the *only* route to the reset at `main.py:11540`.
  Rebasing every sync would have manufactured that condition on purpose.
  Verified on the live repo: every `pre-sync-*` tag remains an ancestor of HEAD.

- **Mount:** none. This is why merge matters — it makes patching the update path
  unnecessary. Measured churn: `hermes_cli/main.py` 455 commits/90d,
  `apps/desktop/src/app/settings/` 206. Patching either would be a permanent
  conflict tax on the highest-churn files in the tree.
- **Depends on:** an `upstream` remote; a non-shallow clone (the engine unshallows
  if needed — the installer clones `--depth 1` and merge needs a merge-base); the
  kiro proxy for conflict resolution *only* (a clean merge never calls a model).
- **If the seam moves:** there is no seam to move. If upstream ever changes the
  update flow so a merged fork still cannot fast-forward, re-check the table
  above before reaching for a patch.
- **Degrades to:** any *handled* failure → rollback tag restored, nothing pushed,
  app keeps running the previous build. A failed sync **defers**; it never bricks.
  **One exception, added 2026-07-26:** an *unhandled* exception is caught by
  `record_crash()`, which writes a `CRASHED-*` report and a history entry but
  **deliberately does not roll back** — at crash time the tree state is unknown and
  blind recovery could destroy a good merge. So after a crash the working tree may
  hold a completed merge, an aborted one, or a partial one. The report names the
  rollback tag; `python scripts/fork_sync.py status` is the first thing to run.
  Callers must not tell the user their tree is safe on that path (see
  `hermes_cli/fork_merge.py`, which distinguishes `FAILED` from `CRASHED` for
  exactly this reason).
- **Next / related:** a Settings page showing sync history and AI resolutions is
  deliberately deferred (see below). Sync history is already recorded as JSONL at
  `~/.hermes/fork-sync/history.jsonl`, so a UI can be added later without
  changing the engine.
- **Deliberately not done:**
  - **~~No patch to `hermes_cli/main.py`.~~ Superseded 2026-07-26** by
    `fork-aware-update`. The claim was that merging made a patch unnecessary. That
    was true for *protecting* the fork and false for *delivering* updates: with no
    patch, `hermes update` reaches its fork branch and deliberately declines to
    merge, so the native popup and Update button did nothing at all. The mount is
    now one additive block in the lowest-churn region of that file. See the
    `fork-aware-update` entry below.
  - **No Settings page (yet).** It would mean editing the second-highest-churn
    directory (206 commits/90d) permanently, for a read-only log viewer. A chat
    notification plus the JSONL history delivers the same information at zero
    merge risk. Revisit only if the notification proves insufficient.
  - **No PowerShell.** `scripts/hermes-sync.ps1` was written and removed:
    upstream ships 20 `.py` scripts to 2 `.ps1`, PS 5.1 mis-parses BOM-less
    non-ASCII (three failed fix attempts chasing phantom line numbers), and the
    caller is Python.
  - **No force-push.** A merge never rewrites history, so a plain push always
    works and nothing can be lost to a rewrite.

#### `fork-guard` — refuse a ref update that would abandon fork commits
- **Status:** active (backstop only)
- **Files:** `scripts/fork-guard.sh`, installed to `.git/hooks/reference-transaction`
- **What:** blocks any branch move onto a *remote* tip that would drop unpushed
  fork commits — which is exactly what all three `reset --hard` sites do.
- **Why:** `scripts/install.ps1` and `scripts/install.sh` run entirely outside the
  Python update path, so nothing in `fork_sync.py` can protect against them. This
  covers whichever site fires, including any not yet found.
- **Mount:** none — a git hook, unversioned. Upstream cannot conflict with it.
- **Depends on:** git ≥ 2.9 (`reference-transaction`). Confirmed on 2.54.0.
- **If the seam moves:** git removing this hook would break it; there is no
  `pre-reset` hook to fall back on (verified: *"cannot find a hook named
  pre-reset"*). The patched-site approach would then be the only option.
- **Degrades to:** `.git/hooks` is not versioned, so a reinstall silently drops
  the guard. `fork_sync.py` reinstalls it on **every** run — the protection is
  self-healing. `HERMES_SYNC_ALLOW_REWRITE=1` overrides it for deliberate surgery.
- **Known limitation (verified, do not "fix" without reading this):** git's hook
  interface presents `reset --hard` and `reset --soft` as an *identical* ref move,
  so the guard also refuses a `--soft` that lands on a remote tip while unpushed
  fork work exists. Two earlier designs were rejected by testing: v1 blocked
  `git rebase` and stranded the repo mid-sequencer; v2 fixed that but blocked
  every `reset --soft`. 15/15 sandbox tests now pass in both directions.
- **Deliberately not done:** not attempting to detect the reset *mode* — the
  information does not reach the hook. An attempt to infer it from working-tree
  state wrongly un-blocked the real attack.

#### `fork-aware-update` — make the native Update button actually update the fork
- **Status:** active
- **Files:**
  - `hermes_cli/fork_merge.py` (new) — the adapter. Loads `scripts/fork_sync.py`
    by path and calls its `sync()`. Classifies the outcome by whether HEAD moved.
  - `apps/desktop/electron/fork-upstream.ts` (new) + `.test.ts` — resolves how far
    behind the `upstream` remote the checkout is, for the update indicator.
  - `tests/fork_sync/test_fork_merge.py` (new) — 24 contracts, including a real
    three-repo end-to-end merge.
  - **Upstream mounts (2 files):**
    `hermes_cli/main.py::_sync_with_upstream_if_needed` — one additive block
    inside the `origin_ahead > 0` branch, no removals;
    `apps/desktop/electron/main.ts` — one import plus **one wrapped line** at the
    `ipcMain.handle('hermes:updates:check', ...)` handler. `checkUpdates()`
    itself is byte-identical to upstream: the decoration happens to its *result*,
    at its single call site, not inside it.
- **What:** clicking Update in the desktop app (or running `hermes update`) now
  merges new upstream commits into the fork, resolves conflicts with the AI
  resolver, verifies, installs dependencies and rebuilds the desktop when the
  merge touched them, then pushes. The popup fires when **Nous** ships, not when
  the user's own fork moves.
- **Why:** two separate defects made the update UI inert on a fork.
  1. `_sync_with_upstream_if_needed` fetches upstream, compares, and when the fork
     has commits of its own prints *"Skipping upstream sync to preserve your
     changes"* and returns. That is the normal state of this fork, so the merge
     never happened.
  2. `checkUpdates()` measures `HEAD..origin/<branch>` — distance from the remote
     the fork **pushes to**. The sync pushes there, so the count is permanently 0:
     "Already up to date", popup silent, Update button inert.
- **Mount:**
  - `hermes_cli/main.py::_sync_with_upstream_if_needed`, inside `if origin_ahead >
    0:`, gated on `upstream_ahead > 0`. Function-level seam, not a line number.
    Upstream's original block — five `print()` calls (the first one bare) and its
    `return` — is kept immediately below as the fallback, unmodified and at the
    same indentation. Preserve exactly those lines when re-applying after a
    conflict.
  - `apps/desktop/electron/main.ts`, the `hermes:updates:check` IPC handler — its
    single call to `checkUpdates()` is wrapped in `withForkUpstreamStatus()`, which
    decorates the returned payload and passes a rejection straight through to the
    handler's existing `.catch`. Gated on the origin count being 0 so the non-fork
    path is untouched, and on the same `HERMES_FORK_MERGE` switch as the Python
    half. Mounted at the call site rather than inside `checkUpdates()` so that
    function needs no edit at all.
- **Depends on:** the *names* `_sync_with_upstream_if_needed`, `origin_ahead`,
    `upstream_ahead` (main.py); the `hermes:updates:check` IPC channel name plus
  `checkUpdates`, `runGit` and `resolveUpdateRoot` (electron/main.ts);
  `scripts/fork_sync.py` exposing `sync()`, `REPO_ROOT`, and `record_crash()`. An
  `upstream` remote must exist — which is also what upstream's own fork detection
  requires.
- **If the seam moves:** re-apply by finding where `hermes update` decides what to
  do about a fork. Two questions to answer in the new code: *(a)* where does it
  give up because the fork has its own commits — insert
  `merge_upstream_into_fork(cwd, "main")` there and return on True; *(b)* where
  does it count how far behind it is — make that count consider `upstream/<branch>`
  when the origin count is 0. If upstream ever merges its own fork-merge support,
  **retire this patch** rather than stacking on top of it. If the whole function
  disappears, the fallback is a `hermes update` that no longer offers upstream
  commits — annoying, not dangerous, and the nightly scheduled sync still merges.
- **Degrades to:** exactly upstream behavior. Missing module, `HERMES_FORK_MERGE=0`,
  an exception, a dirty tree, or a path mismatch between the caller and the
  engine's own checkout all return "not handled", and upstream's original lines
  run. Asserted by `test_mount_falls_back_to_upstream_message`,
  `test_mount_survives_a_broken_fork_module`, and
  `test_mount_survives_an_unimportable_fork_module`.
  **Two outcomes deliberately do NOT fall through:** a reported engine failure and
  a crash both count as handled, because the engine has already told the user what
  happened and adding "Skipping upstream sync to preserve your changes" on top
  would be misleading. See the return contract in `hermes_cli/fork_merge.py`.
- **Known limitation — the off switch and a running app:** `HERMES_FORK_MERGE` is
  read from the environment. The Electron main process snapshots its environment at
  launch, so exporting the variable in a shell disables the Python merge on the next
  `hermes update` but leaves the *already running* app's probe active until it is
  restarted. During that window the popup can still offer commits the merge will
  decline. Restart the desktop app after flipping the switch.
- **Next / related:** the nightly scheduled sync now overlaps this — if it keeps
  merging at 04:00, the popup rarely has anything to show. Deciding whether the
  nightly becomes check-and-notify (so the user drives updates from the button) is
  an open question, not a defect. Sync failure alerts are still delivered nowhere;
  that remains the biggest gap.
- **Deliberately not done:**
  - **No commit list for the fork case.** `readCommitLog()` is hardcoded to the
    origin range and widening it would mean editing an upstream function plus its
    call site for a cosmetic list. The official-SSH branch of `checkUpdates()`
    already returns `commits: []`, so an empty list is an established shape. The
    count, which is what drives the popup, is exact.
  - **No patch to the installers.** See R15.
  - **No second implementation of merge/resolve/verify.** The adapter delegates to
    the engine the scheduled job already exercises nightly. A copy would go stale.
  - **The engine's deps-install and desktop-rebuild steps were NOT removed.** They
    look like duplication of what `hermes update` does. On the call site that
    matters — inside `if commit_count == 0:`, the normal state of a fork that
    pushes its own merges — `hermes update` returns before its own deps and rebuild
    steps, so the engine must perform them; removing them ships merged source
    against a stale venv and a stale app bundle. There is a **second** call site
    after a successful origin pull where `hermes update` does run those steps
    itself. Reaching it requires origin to be ahead of local, which the sync never
    causes because it pushes last; if it is ever reached the cost is duplicated
    work, not a wrong result.

### Existing customizations that live in user data
Registered for **resolver context only** — these are not fork commits and cannot
conflict. They tell the model what windro's setup cares about, so a resolution
does not accidentally break a seam these rely on.

**`model-system-prompt`** — Python middleware plugin at
`%LOCALAPPDATA%\hermes\plugins\`. Injects a per-model system-prompt overlay read
from `%LOCALAPPDATA%\hermes\model-prompts\<model>.md`. Relies on the plugin
middleware hook and the resolved model name being available at prompt-assembly
time. Preserve those seams.

**`model-prompts-ui`** — desktop UI plugin plus `plugin_api.py` backend, same
plugins directory. Adds a sidebar page for editing the per-model prompts above.
Relies on the desktop plugin page registration and the plugin HTTP API.

**Config** — `%LOCALAPPDATA%\hermes\config.yaml` defines a custom `kiro` provider
(`api_mode: anthropic_messages`, local proxy, `discover_models: true`) with
per-model context lengths, and enables the two plugins above. The same proxy is
what `hermes-sync` calls to resolve conflicts.

---

## How the resolver is invoked

`hermes-sync` posts to the local kiro proxy at
`http://127.0.0.1:8081/v1/messages` (Anthropic Messages API) with model
`claude-opus-5`, sending: the resolver system prompt, **this file**, the commit
message of the fork patch being replayed, and the conflicted file.

Two facts about that endpoint, verified on this machine:

- Auth is ignored — any key value works.
- The response contains a `thinking` block **before** the `text` block. Parse the
  **last** `type == "text"` block. Reading `content[0]` returns the model's
  reasoning, not the resolved file.

In a rebase the conflict sides are inverted from intuition: `<<<<<<<` **ours** is
the **new upstream** code, `>>>>>>>` **theirs** is **our** customization.
