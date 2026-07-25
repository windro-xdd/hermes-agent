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
custom(<id>): <one-line what>

Why: <the actual problem this solves>
Mount: <file:symbol where it hooks into upstream>
Re-apply: <what to do if upstream moves or deletes that seam>
```

### R3 — Every customization gets an entry in this file
Added in the same commit as the code. No entry, no merge.

### R4 — Additive over destructive
Prefer adding a branch, a wrapper, or a new function over rewriting or deleting
upstream logic. Deleting upstream code guarantees a conflict when upstream touches
that code, and makes the resolver's job ambiguous.

### R5 — Match upstream's existing style and APIs
Use the libraries, patterns, error handling, and naming already in the file being
touched. Do not introduce a new dependency for a customization.

### R6 — Never `reset --hard` this fork
It silently destroys fork commits. Not in scripts, not in the patched updater, not
manually. Recovery point before any risky git operation is a **tag**, and the
recovery move is `git rebase --abort` or checking out the tag — never a hard reset.

### R7 — No AI-resolved conflict is trusted until it builds
A conflict resolved by the model is a *proposal*. It is accepted only after the
desktop build and typecheck pass. Failure, or the model reporting low confidence,
means abort, roll back, and notify. A failed update **defers**; it never ships a
broken tree.

### R8 — Secrets stay out of the repo
API keys, tokens, and personal paths live in user data or the environment. This
fork is on GitHub.

### R9 — `git rerere` stays enabled
`rerere.enabled` and `rerere.autoupdate` are on. Git remembers how a conflict was
resolved and replays it automatically next time, so a recurring conflict costs one
model call ever, not one per update.

### R10 — The updater patch is the fragile one
The patch that redirects the native Update button into `hermes-sync` modifies the
very code that performs updates. If it ever conflicts, **a human resolves that one
by hand** — the model must not auto-resolve a conflict in the update path, because
a wrong resolution there can break the ability to update at all.

---

## Registry

### Core patches on this fork
_None yet. Fork is currently identical to upstream `main`._

| id | files | status |
|---|---|---|
| `updater-hermes-sync` | `hermes_cli/main.py` (mount only) + `scripts/hermes-sync.ps1` (new) | planned — Phase 1 Task 4 |

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
