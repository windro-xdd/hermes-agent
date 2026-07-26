# Infrastructure: move fork docs into a `fork/` namespace, one file per change

**Date:** 2026-07-26
**Type:** Infrastructure

- **Patch id:** `customizations-registry`
- **Why:** the fork's docs were two flat files at the repo root
  (`CUSTOMIZATIONS.md`, `CHANGELOG-FORK.md`). Three problems with that:
  1. **Root is upstream's namespace.** Upstream has added 32 distinct root-level
     `.md` files over its history, so a name collision was a real (if unlikely)
     add/add conflict waiting to happen.
  2. **A single shared changelog collides on every concurrent edit.** All entries
     land at the top of one file, so two changes touching it — from two machines,
     two branches, or an AI-resolved merge — conflict on the same lines. Ironically
     it was the most conflict-prone file in a fork built to avoid conflicts.
  3. **It grows until nobody reads it.** A flat log passes 500 lines and becomes
     an archive rather than a reference.

- **Changed:**
  - `CUSTOMIZATIONS.md` → `fork/CUSTOMIZATIONS.md`
  - `CHANGELOG-FORK.md` → `fork/changelog/README.md` (now index + conventions),
    with its four historical entries split into `fork/changelog/entries/*.md`,
    one file per change, named `YYYY-MM-DD-NN-slug.md`.
  - `scripts/fork_sync.py` now resolves the resolver-context file through a
    candidate list (`fork/CUSTOMIZATIONS.md`, then the historical root path)
    instead of one hardcoded path.
  - Registry references updated; two contract tests added (18 → 16 → 16 passing,
    net +2 from the previous 14).

- **Impact:** none at runtime. Documentation layout only. `git mv` was used, so
  file history is preserved and `git log --follow` still works.

- **Risk / watch for:**
  - **A stale path here fails silently, not loudly.** `CUSTOMIZATIONS.md` is the
    resolver's only context about *why* each patch exists. If the engine cannot
    find it, the sync still succeeds — it just resolves conflicts blind, which is
    considerably worse than an error. `test_customizations_file_is_locatable`
    guards this, and also asserts the file is not truncated.
  - Entries are **append-only**. One historical entry still references the old
    `CHANGELOG-FORK.md` filename; that is correct and was deliberately left, since
    rewriting history is what makes a changelog untrustworthy.
  - `test_changelog_entries_exist` fails if an entry file is not linked from the
    index, so the index cannot drift out of sync with the directory.

- **Verified:** `python -m pytest tests/fork_sync/ -q` → **16 passed**. Structure
  confirmed on disk: 4 migrated entries + this one, index README, and
  `fork/CUSTOMIZATIONS.md`. `grep` for the old filename returns only the one
  historical mention noted above. Working tree clean after the move.

- **Follow-ups:**
  - If the fork ever gains more tooling, `fork/` is the place for it — the
    namespace exists specifically so fork-owned files never contend with upstream.
