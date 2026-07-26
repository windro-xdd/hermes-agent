# Added: fork rulebook, patch registry, and this changelog

**Date:** 2026-07-25  
**Type:** Added

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
