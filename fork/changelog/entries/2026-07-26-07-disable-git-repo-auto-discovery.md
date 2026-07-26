# Fixed: projects appearing that were never created (git repo auto-discovery)

**Date:** 2026-07-26
**Type:** Fixed

- **Patch id:** `—` (configuration change, no fork patch)
- **Why:** windro reported three symptoms in the projects feature:
  1. projects he never created appeared in the projects tab;
  2. projects appeared to be visible across profiles;
  3. projects felt like "just folders and sessions", with no rules of their own.

  Investigation showed **1 and 2 are the same cause**, and it is a default setting
  rather than a bug. `desktop.repo_scan_enabled` defaults to `True`
  (`hermes_cli/config.py:3655`). Hermes walks the filesystem, finds every directory
  containing `.git`, and **auto-promotes each one to a project**
  (`tui_gateway/project_tree.py:636,662`, flagged `is_auto=True`). Both profile
  databases held **12** such rows each — `hermes-pr-verify`, `kalolsavam`,
  `opencode`, `T3MP3ST`, `asthra-recovered` and others.

  The apparent cross-profile leak was a misdiagnosis worth recording: projects ARE
  correctly isolated per profile (separate `projects.db`; no code path reads across
  them — the real user-created projects differ, `Hermus` in default vs `Hermes` in
  dev-xd). What looked like leakage was the *same scan* running independently in
  each profile and finding the same directories on disk. Fixing (1) resolved (2).

  Item 3 is a genuine missing feature and is not addressed here.

- **Changed:**
  - `desktop.repo_scan_enabled` → `false` in **both** profiles' `config.yaml`, via
    `hermes config set` (the supported path — no code edited).
  - Cleared the 24 cached rows with `projects_db.clear_discovered_repos()`, again
    using the existing API rather than raw SQL.
  - **No fork patch.** Zero upstream files touched; this is user configuration.

- **Impact:** the projects tab now shows only projects the user created. Verified 1
  per profile, both `isAuto: False`. Auto-discovery can be re-enabled any time in
  Settings → Workspace.

- **Risk / watch for:**
  - **Sessions are placed by working directory, not by a stored project link** —
    there is no project column on the `sessions` table. With auto-discovery off, a
    session whose cwd falls under no user-created project folder lands in the
    synthetic "No project" bucket instead of getting a project of its own. That is
    the intended trade for not being spammed, but it is a behavior change: add the
    folder to a project (`hermes project add-folder`) to place such sessions.
  - `reconcile_discovered_repos_policy()` clears cached rows when the policy key
    changes, so a future config edit re-scans cleanly. But **re-enabling the
    setting will repopulate all 12 entries per profile** — this is a preference,
    not a one-way migration.
  - The setting is per profile. A newly created profile gets the default (`True`)
    and will scan again unless `--clone`d from a profile that has it off.

- **Verified:** `hermes config get desktop.repo_scan_enabled` → `false` in both
  profiles. Auto-repo row counts 12 → 0 in each database, user projects intact
  (`['hermus']`, `['hermes']`). Ran the app's own `tui_gateway.project_tree.
  build_tree()` against the real data: default profile shows 1 project (`Hermus`,
  auto=False), dev-xd shows 1 (`Hermes`, auto=False, 4 sessions). Previously the
  same call returned 13 projects, 12 of them auto.

- **Follow-ups:**
  - **Per-project rules (item 3) is unbuilt.** A project is currently a name plus
    folder paths; there is no per-project instruction file, and `AGENTS.md` is a
    repo artifact rather than a project property. Designing this needs care: the
    project dialog and settings live in `apps/desktop/src/app/settings/` and
    neighbours — **206 upstream commits in 90 days**, the second-highest-churn area
    in the tree. This fork currently modifies zero upstream files, and a UI patch
    there would be a permanent conflict tax. Research the cheapest wiring (plugin
    surface, or a file-based convention the backend reads) before touching UI.
