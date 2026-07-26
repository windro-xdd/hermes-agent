# Fork changelog

Every change made **in this fork** gets an entry here. Upstream's own changes are
not logged — this is the fork's history only, so it stays readable.

**One file per change**, in [`entries/`](./entries), newest last. A single shared
file was the previous design and it was wrong for two reasons: concurrent edits
collide on the same lines, and a flat file grows until nobody reads it. Separate
files never conflict with each other and each one is a self-contained record.

Companion to [`../CUSTOMIZATIONS.md`](../CUSTOMIZATIONS.md). The division matters:

| File | Answers |
|---|---|
| `../CUSTOMIZATIONS.md` | What exists **right now**, and how it is wired. Rewritten in place as patches change. |
| `entries/*.md` | **When and why** it changed. Append-only history. Never rewritten. |

Required by rule **R9**: the entry goes in the **same commit** as the change. An
entry written later is written from memory, and memory is where the "why" is lost.

---

## Index

Newest first.

| Date | Type | Change |
|---|---|---|
| 2026-07-26 | Fixed | [Second-pass audit — one real bug plus documentation drift](entries/2026-07-26-11-second-pass-audit-fixes.md) |
| 2026-07-26 | Fixed | [Audit corrections to the fork-aware update patch](entries/2026-07-26-10-audit-corrections-to-fork-aware-update.md) |
| 2026-07-26 | Added | [The native Update button now actually updates the fork](entries/2026-07-26-09-fork-aware-update.md) |
| 2026-07-26 | Fixed | [The sync crashed after every real merge (undefined variable)](entries/2026-07-26-08-sync-crashed-after-every-real-merge.md) |
| 2026-07-26 | Fixed | [Projects appearing that were never created (git repo auto-discovery)](entries/2026-07-26-07-disable-git-repo-auto-discovery.md) |
| 2026-07-26 | Infrastructure | [Require a branch workflow, and make the changelog rule self-contained](entries/2026-07-26-06-require-branch-workflow-and-complete-the-changelog-rule.md) |
| 2026-07-26 | Infrastructure | [Move fork docs into a `fork/` namespace, one file per change](entries/2026-07-26-05-move-fork-docs-into-a-fork-namespace.md) |
| 2026-07-26 | Fixed | [Six defects in the sync engine, all found by running it](entries/2026-07-26-04-six-defects-in-the-sync-engine-all-found-by-running-it.md) |
| 2026-07-26 | Added | [Automatic upstream sync with AI conflict resolution](entries/2026-07-26-03-automatic-upstream-sync-with-ai-conflict-resolution.md) |
| 2026-07-25 | Infrastructure | [Fork topology, full history, and rerere](entries/2026-07-25-02-fork-topology-full-history-and-rerere.md) |
| 2026-07-25 | Added | [Fork rulebook, patch registry, and this changelog](entries/2026-07-25-01-fork-rulebook-patch-registry-and-this-changelog.md) |

---

## What needs an entry

Anything that changes behavior or the shape of the fork:

- **Added** — a new feature or capability
- **Fixed** — a bug fix, ours or a patch on top of upstream's
- **Customized** — a modification to built-in Hermes behavior (the common case)
- **Changed** — a rework of an existing fork patch
- **Retired** — a patch removed, usually because upstream absorbed it
- **Infrastructure** — repo/tooling/topology changes affecting how updates work
- **Upstream sync** — a sync that required conflict resolution, and what the AI
  resolved. Clean syncs need no entry.

Not logged: comment typos, formatting, anything with no behavioral or structural
effect.

---

## Adding an entry

Create `entries/YYYY-MM-DD-NN-short-slug.md`, where `NN` is the next sequence
number, and add a row to the index above. Use this shape:

```markdown
# <Type>: <one-line summary>

**Date:** YYYY-MM-DD
**Type:** Added | Fixed | Customized | Changed | Retired | Infrastructure | Upstream sync

- **Patch id:** `<id>` (matches CUSTOMIZATIONS.md; `—` if not a registered patch)
- **Why:** the problem or request that caused this. Not a restatement of the what.
- **Changed:** files, and what each one does now.
- **Impact:** what a user notices. `none (internal)` is valid.
- **Risk / watch for:** how this could break later, and the symptom it would show.
- **Verified:** the actual command run and its real result. Not "should work".
- **Follow-ups:** known gaps, deferred work, related changes expected next.
```

Rules for entries:

- **Append-only.** Correct a past entry by adding a new one that supersedes it;
  never edit history. The point of a changelog is that it can be trusted.
- **"Why" must survive without context.** Assume the reader has no memory of the
  conversation that produced the change — in six months, nobody will.
- **"Verified" means it was actually run.** Paste the real outcome. An unverified
  entry is worse than none: it manufactures false confidence.
- **"Risk / watch for" is the forward-looking field (R8).** If a change depends on
  an upstream seam, an assumption, or a platform quirk, name it. This is what makes
  a future failure diagnosable in minutes instead of hours.
