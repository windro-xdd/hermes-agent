# Infrastructure: require a branch workflow, and make the changelog rule self-contained

**Date:** 2026-07-26
**Type:** Infrastructure

- **Patch id:** `customizations-registry`
- **Why:** two gaps windro identified.

  1. **No branching rule existed, and its absence showed.** Every commit in this
     project so far went straight to `main` — 20+ of them, including the six
     bug-fix commits for defects that were found *after* I had declared the work
     done. Each of those was an incomplete change landing on the branch the
     automatic sync merges into. Nothing broke, but "it happened to be fine" is
     not a process.
  2. **R9 deferred its own content.** It said "see that README for the format",
     so the single most-referenced rule required opening a second file to follow.
     A rule that cannot be read where it is stated will be followed inconsistently.

- **Changed:**
  - `fork/CUSTOMIZATIONS.md` — new **R10**: every change goes through a branch.
    Naming (`fix/`, `feat/`, `custom/`, `docs/`, `chore/`), the exact command
    sequence, `--no-ff` merges so the unit of work stays visible in history, and a
    pre-merge checklist (complete, tests pass, registry + changelog entries
    present, `main` hasn't moved). Old R10–R14 renumbered to R11–R15; all
    cross-references verified.
  - `fork/CUSTOMIZATIONS.md` — **R9** rewritten to be self-contained: where entries
    live and why one-file-per-change, the seven entry types in a table, every
    required field, and the four rules governing entries (same commit, append-only,
    "why" stands alone, "verified" means actually run).

- **Impact:** process only, no runtime change. This commit is the first to follow
  R10 — authored on `docs/git-workflow-rule` and merged with `--no-ff`.

- **Risk / watch for:**
  - **R10 interacts with the automatic sync, and this is the one to remember:** the
    04:00 sync needs a clean tree and defers with "N uncommitted change(s) in the
    repo" if anything tracked is modified. An in-progress *branch* is fine — the
    sync only touches `main` — but **uncommitted work defers the sync**. Leave work
    committed or stashed overnight, or updates silently stall. It reports and
    retries the next day; nothing is lost, but the fork drifts behind upstream
    until the tree is clean.
  - R10 is enforced by review only. Nothing prevents a direct commit to `main`; a
    pre-commit hook could, but hooks live in unversioned `.git/hooks` and would be
    lost on reinstall (the same reason `fork-guard.sh` is reinstalled every sync).
  - The narrow exception — a single-commit hotfix to an actively broken install —
    is the obvious loophole. It still requires both entries in the same commit.

- **Verified:** `grep -oE '^### R[0-9]+'` returns R1–R15 with no duplicates and no
  gaps; all `(Rn)` cross-references still resolve to the intended rules after
  renumbering. `python -m pytest tests/fork_sync/ -q` → 16 passed. Branch created,
  committed, and merged `--no-ff` per the rule it adds.

- **Follow-ups:**
  - Consider a `fork/` pre-commit check that refuses a commit on `main` and refuses
    a commit that touches `scripts/` or `fork/CUSTOMIZATIONS.md` without a matching
    changelog entry — the enforcement R9 and R10 currently lack.
