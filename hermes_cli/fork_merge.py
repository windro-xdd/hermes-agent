"""Fork-aware upstream merge for the `hermes update` path.

WHAT THIS IS FOR
    `hermes update` already detects a fork and calls
    `_sync_with_upstream_if_needed()`. That function fast-forwards a fork that
    is strictly behind upstream, but when the fork carries commits of its own it
    deliberately stops and prints "Skipping upstream sync to preserve your
    changes". For a fork whose whole purpose is carrying customizations, that
    means clicking Update in the desktop app does nothing forever.

    This module is the missing step: merge `upstream/<branch>` into the fork,
    resolve any conflict with the AI resolver, verify the result, install
    dependencies and rebuild the desktop app when the merge touched them, then
    push. On any failure the sync engine restores its own rollback point, so the
    app keeps running the previous, working code.

WHY IT DELEGATES INSTEAD OF IMPLEMENTING
    `scripts/fork_sync.py` already does all of the above and is exercised nightly
    by a scheduled task. Re-implementing merge/resolve/verify here would create a
    second copy that silently goes stale — the failure mode the fork rulebook
    calls out (R6: no copy-paste of logic). So this module is a thin adapter: it
    loads that script by path and calls its `sync()`. One implementation, two
    entry points (the scheduled job and the Update button).

    It is loaded by path, not imported as a package, because `scripts/` has no
    `__init__.py` upstream and adding one would be an upstream edit for no gain.

WHY THE MERGE MUST DO ITS OWN DEPS + REBUILD
    `hermes update` calls `_sync_with_upstream_if_needed()` from TWO places. The
    one that matters here is inside `if commit_count == 0:` — the "nothing new
    from my own fork" branch, which is the normal state of a fork that pushes its
    own merges. That branch returns before the dependency install and the desktop
    rebuild, so those steps cannot be inherited from the caller; `sync()` performs
    them itself. Do not "simplify" by deleting them there.

    The second call site sits after a successful origin pull, where `hermes update`
    does run its own deps install and rebuild afterwards. Reaching it means origin
    was ahead of local, which the sync never causes (it pushes last), so in
    practice it is unreachable on this install. If it is ever reached the result is
    duplicated work — slow, not wrong.

DEGRADATION (R6)
    Every failure path returns False, which makes the caller fall through to
    upstream's original behavior. A missing script, an unreadable module, a
    broken git, an exception anywhere: the worst case is "the fork merge did not
    run", never "hermes update crashed". Updating must never be breakable by
    this file.

OFF SWITCH
    `HERMES_FORK_MERGE=0` (also: false/no/off) disables it, and upstream's exact
    original message prints instead. An environment variable rather than a
    config.yaml key on purpose: this code runs inside the update path, and an
    update is precisely the event that can leave config loading broken. The
    switch has to work when nothing else does.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

ENGINE_RELPATH = ("scripts", "fork_sync.py")
_ENGINE_MODULE_NAME = "_hermes_fork_sync_engine"

# Outcomes. Strings rather than an enum so a log line reads plainly and a test
# asserts on the contract instead of an import.
MERGED = "merged"          # upstream came in; the caller should not print its own message
UNCHANGED = "unchanged"    # engine ran but HEAD did not move (nothing to do / it declined)
FAILED = "failed"          # engine ran, failed, and restored its rollback point
CRASHED = "crashed"        # engine raised: it reported, but the tree state is UNKNOWN
UNAVAILABLE = "unavailable"  # disabled, or the engine could not be loaded at all

_FALSEY = {"0", "false", "no", "off", ""}


def is_enabled() -> bool:
    """False when the operator has switched the fork merge off."""
    raw = os.environ.get("HERMES_FORK_MERGE")
    if raw is None:
        return True
    return raw.strip().lower() not in _FALSEY


def repo_root() -> Path:
    """The checkout this module lives in (<root>/hermes_cli/fork_merge.py)."""
    return Path(__file__).resolve().parent.parent


def engine_path(root: Optional[Path] = None) -> Path:
    return (root or repo_root()).joinpath(*ENGINE_RELPATH)


def load_engine(root: Optional[Path] = None) -> Optional[Any]:
    """Load `scripts/fork_sync.py` as a module. None when unavailable.

    Cached in `sys.modules` so a second call in the same process is free. Note
    the engine reconfigures stdout/stderr to UTF-8 on import — deliberate on
    Windows, where the update path can be writing to a redirected pipe under
    cp1252 and a single non-ASCII status line would otherwise raise.
    """
    cached = sys.modules.get(_ENGINE_MODULE_NAME)
    if cached is not None:
        return cached
    path = engine_path(root)
    if not path.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location(_ENGINE_MODULE_NAME, path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        # Registered before exec so a self-import inside the engine resolves.
        sys.modules[_ENGINE_MODULE_NAME] = module
        spec.loader.exec_module(module)
        return module
    except Exception:
        sys.modules.pop(_ENGINE_MODULE_NAME, None)
        return None


def _head_sha(cwd: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd),
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def merge_outcome(cwd: Path, branch: str = "main") -> str:
    """Run the fork-aware merge. Returns one of the module's outcome constants.

    HEAD is compared before and after rather than trusting the exit code,
    because the engine returns 0 for three different situations: it merged, it
    was already current, and it declined because the tree was dirty. Only the
    first should stop the caller from printing its own "skipping" message, and
    "did HEAD move" is the one observation that distinguishes them without
    parsing text.
    """
    if not is_enabled():
        return UNAVAILABLE
    engine = load_engine(Path(cwd))
    if engine is None or not hasattr(engine, "sync"):
        return UNAVAILABLE

    # The engine operates on its own REPO_ROOT (derived from where the script
    # lives), not on an argument. For `hermes update` those are the same
    # checkout, which is why this works — but if a caller ever passes a
    # different path, silently merging some OTHER repository and then reporting
    # on it would be the worst possible outcome. Decline instead; the caller
    # falls back to upstream behavior.
    engine_root = getattr(engine, "REPO_ROOT", None)
    if engine_root is not None:
        try:
            if Path(engine_root).resolve() != Path(cwd).resolve():
                return UNAVAILABLE
        except Exception:
            return UNAVAILABLE

    before = _head_sha(Path(cwd))
    try:
        code = engine.sync(branch)
    except Exception:
        # The engine records its own crashes when invoked through its CLI; this
        # path is a direct call, so record it the same way rather than letting an
        # exception escape into `hermes update`.
        #
        # Reported as CRASHED, not FAILED, and the distinction is not cosmetic: on
        # a handled failure the engine has restored its rollback point, so "your
        # work is intact" is true. On a crash it has not — record_crash explicitly
        # does not roll back, because the tree state is unknown. Telling the user
        # everything is fine in exactly the case where nobody knows would be the
        # worst thing this module could say.
        recorder = getattr(engine, "record_crash", None)
        if callable(recorder):
            try:
                import traceback

                recorder(traceback.format_exc())
            except Exception:
                pass
        return CRASHED
    after = _head_sha(Path(cwd))

    if code != 0:
        return FAILED
    return MERGED if (before and after and before != after) else UNCHANGED


def merge_upstream_into_fork(cwd: Path, branch: str = "main") -> bool:
    """True when this handled the fork/upstream situation and the caller should stop.

    Returned True covers success AND a reported failure: on failure the engine
    has already printed what happened, written a report, and restored its
    rollback point, so the caller printing "Skipping upstream sync to preserve
    your changes" on top of that would be actively misleading.

    Returned False means nothing was done — the caller should run its original
    code path unchanged.
    """
    try:
        outcome = merge_outcome(Path(cwd), branch)
    except Exception:
        return False
    if outcome == MERGED:
        return True
    if outcome == FAILED:
        # The engine restored its rollback point on this path, so this is a fact,
        # not a hope.
        print("  ℹ Your customizations are intact and the previous build is "
              "still running.", flush=True)
        return True
    if outcome == CRASHED:
        print("  ⚠ The fork merge crashed. The repository state was NOT rolled "
              "back automatically — check it before updating again:", flush=True)
        print("      python scripts/fork_sync.py status", flush=True)
        print("    The crash report (with the rollback tag to recover from) is in "
              "the fork-sync reports directory.", flush=True)
        return True
    return False
