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
