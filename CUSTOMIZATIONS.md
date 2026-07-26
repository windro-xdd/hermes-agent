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

### R9 — Every change gets a CHANGELOG entry
New feature, bug fix, or a modification to built-in Hermes behavior — it goes in
`CHANGELOG-FORK.md`, in the same commit, using the format defined there. No entry,
no merge. See that file for the required fields.

### R10 — Never `reset --hard` this fork
It silently destroys fork commits. Not in scripts, not in the patched updater, not
manually. Recovery point before any risky git operation is a **tag**, and the
recovery move is `git rebase --abort` or checking out the tag — never a hard reset.

### R11 — No AI-resolved conflict is trusted until it builds
A conflict resolved by the model is a *proposal*. It is accepted only after the
desktop build and typecheck pass. Failure, or the model reporting low confidence,
means abort, roll back, and notify. A failed update **defers**; it never ships a
broken tree.

### R12 — Secrets stay out of the repo
API keys, tokens, and personal paths live in user data or the environment. This
fork is on GitHub.

### R13 — `git rerere` stays enabled
`rerere.enabled` and `rerere.autoupdate` are on. Git remembers how a conflict was
resolved and replays it automatically next time, so a recurring conflict costs one
model call ever, not one per update.

### R14 — The updater patch is the fragile one
The patch that redirects the native Update button into `hermes-sync` modifies the
very code that performs updates. If it ever conflicts, **a human resolves that one
by hand** — the model must not auto-resolve a conflict in the update path, because
a wrong resolution there can break the ability to update at all.

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
- **Files:** `CUSTOMIZATIONS.md` + `CHANGELOG-FORK.md` (both new). No upstream file
  touched.
- **What:** documents the fork's rules and every customization.
- **Why:** a diff does not record intent; without intent, a rebase conflict gets
  resolved by guesswork and a customization silently dies.
- **Mount:** none — root-level new file, conflict-free by construction.
- **Depends on:** nothing.
- **If the seam moves:** if upstream ever adds its own `CUSTOMIZATIONS.md`, rename
  ours to `FORK-CUSTOMIZATIONS.md` and update the path in `hermes-sync`.
- **Degrades to:** n/a — documentation.
- **Next / related:** `CHANGELOG-FORK.md` (companion); read by
  `updater-hermes-sync`.
- **Deliberately not done:** not placed in `docs/` — upstream churns that
  directory, and the resolver needs a predictable path.

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
- **Degrades to:** any failure → rollback tag restored, nothing pushed, app keeps
  running the previous build. A failed sync **defers**; it never bricks.
- **Next / related:** a Settings page showing sync history and AI resolutions is
  deliberately deferred (see below). Sync history is already recorded as JSONL at
  `~/.hermes/fork-sync/history.jsonl`, so a UI can be added later without
  changing the engine.
- **Deliberately not done:**
  - **No patch to `hermes_cli/main.py`.** Unnecessary under merge, and it is the
    single highest-churn file in the repo.
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
