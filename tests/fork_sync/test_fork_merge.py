"""Behavior contracts for the fork-aware update mount.

Two things are under test:

1. ``hermes_cli/fork_merge.py`` — the adapter that lets `hermes update` reach the
   sync engine. Its whole job is to never break the update path, so most of these
   tests are about degradation: engine missing, engine raising, switch off.

2. The mount itself — that ``_sync_with_upstream_if_needed()`` in
   ``hermes_cli/main.py`` actually calls the fork merge when the fork carries its
   own commits and upstream is ahead, AND that upstream's original "Skipping
   upstream sync" message still prints when the fork machinery declines. The
   fallback is the part most likely to be lost in a future conflict resolution,
   so it is asserted explicitly rather than assumed.

Run: python -m pytest tests/fork_sync/test_fork_merge.py -q
"""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

from hermes_cli import fork_merge


@pytest.fixture(autouse=True)
def _clear_engine_cache():
    """load_engine() caches in sys.modules; drop it between tests."""
    sys.modules.pop(fork_merge._ENGINE_MODULE_NAME, None)
    yield
    sys.modules.pop(fork_merge._ENGINE_MODULE_NAME, None)


def _fake_engine(sync_result, *, raises: bool = False) -> types.ModuleType:
    mod = types.ModuleType("fake_engine")
    calls: list[tuple] = []

    def sync(branch="main", **kw):
        calls.append((branch, kw))
        if raises:
            raise RuntimeError("induced engine failure")
        return sync_result

    mod.sync = sync
    mod.calls = calls
    mod.crashes = []
    mod.record_crash = lambda tb: mod.crashes.append(tb) or 9
    return mod


# ── the off switch ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("value,expected", [
    ("0", False), ("false", False), ("no", False), ("off", False), ("", False),
    ("1", True), ("true", True), ("yes", True),
])
def test_off_switch_values(monkeypatch, value, expected):
    monkeypatch.setenv("HERMES_FORK_MERGE", value)
    assert fork_merge.is_enabled() is expected


def test_enabled_by_default(monkeypatch):
    """Absent config must mean ON — a fork that silently stops updating is the
    failure this whole patch exists to fix."""
    monkeypatch.delenv("HERMES_FORK_MERGE", raising=False)
    assert fork_merge.is_enabled() is True


def test_switch_off_is_exactly_upstream_behavior(monkeypatch, tmp_path):
    """Off must return False so the caller runs its original code path."""
    monkeypatch.setenv("HERMES_FORK_MERGE", "0")
    assert fork_merge.merge_outcome(tmp_path) == fork_merge.UNAVAILABLE
    assert fork_merge.merge_upstream_into_fork(tmp_path) is False


# ── degradation: this must never break `hermes update` ───────────────────────
def test_missing_engine_degrades_to_noop(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_FORK_MERGE", raising=False)
    monkeypatch.setattr(fork_merge, "ENGINE_RELPATH", ("scripts", "not_here.py"))
    assert fork_merge.load_engine() is None
    assert fork_merge.merge_outcome(tmp_path) == fork_merge.UNAVAILABLE
    assert fork_merge.merge_upstream_into_fork(tmp_path) is False


def test_engine_without_sync_degrades_to_noop(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_FORK_MERGE", raising=False)
    monkeypatch.setattr(fork_merge, "load_engine", lambda root=None: types.ModuleType("empty"))
    assert fork_merge.merge_outcome(tmp_path) == fork_merge.UNAVAILABLE


def test_engine_exception_is_contained_and_recorded(monkeypatch, tmp_path):
    """An exception inside the engine must not escape into the update path, and
    must still leave a trace via the engine's crash recorder."""
    monkeypatch.delenv("HERMES_FORK_MERGE", raising=False)
    engine = _fake_engine(0, raises=True)
    monkeypatch.setattr(fork_merge, "load_engine", lambda root=None: engine)
    monkeypatch.setattr(fork_merge, "_head_sha", lambda cwd: "aaa")

    assert fork_merge.merge_outcome(tmp_path) == fork_merge.FAILED
    assert engine.crashes, "a direct-call crash must still be recorded"
    # And the caller-facing wrapper must not raise either.
    assert fork_merge.merge_upstream_into_fork(tmp_path) is True


# ── outcome classification ───────────────────────────────────────────────────
def test_merged_when_head_moves(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_FORK_MERGE", raising=False)
    monkeypatch.setattr(fork_merge, "load_engine", lambda root=None: _fake_engine(0))
    shas = iter(["before", "after"])
    monkeypatch.setattr(fork_merge, "_head_sha", lambda cwd: next(shas))
    assert fork_merge.merge_outcome(tmp_path) == fork_merge.MERGED


def test_unchanged_when_head_does_not_move(monkeypatch, tmp_path):
    """Exit code 0 covers merged, already-current, AND declined-because-dirty.
    Only a moved HEAD means the caller should stop; otherwise upstream's own
    message is the correct thing for the user to see."""
    monkeypatch.delenv("HERMES_FORK_MERGE", raising=False)
    monkeypatch.setattr(fork_merge, "load_engine", lambda root=None: _fake_engine(0))
    monkeypatch.setattr(fork_merge, "_head_sha", lambda cwd: "same")
    assert fork_merge.merge_outcome(tmp_path) == fork_merge.UNCHANGED
    assert fork_merge.merge_upstream_into_fork(tmp_path) is False


def test_failed_is_handled_not_silent(monkeypatch, tmp_path, capsys):
    """On failure the engine has already reported and rolled back, so the caller
    must NOT additionally print 'skipping to preserve your changes'."""
    monkeypatch.delenv("HERMES_FORK_MERGE", raising=False)
    monkeypatch.setattr(fork_merge, "load_engine", lambda root=None: _fake_engine(8))
    monkeypatch.setattr(fork_merge, "_head_sha", lambda cwd: "same")
    assert fork_merge.merge_outcome(tmp_path) == fork_merge.FAILED
    assert fork_merge.merge_upstream_into_fork(tmp_path) is True
    out = capsys.readouterr().out.lower()
    assert "intact" in out, "a failure must reassure the user their work survived"


def test_branch_is_passed_through(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_FORK_MERGE", raising=False)
    engine = _fake_engine(0)
    monkeypatch.setattr(fork_merge, "load_engine", lambda root=None: engine)
    monkeypatch.setattr(fork_merge, "_head_sha", lambda cwd: "same")
    fork_merge.merge_outcome(tmp_path, "release")
    assert engine.calls and engine.calls[0][0] == "release"


def test_real_engine_loads():
    """The adapter must be able to load the actual engine in this checkout —
    a broken path here means the mount is a permanent no-op."""
    engine = fork_merge.load_engine()
    assert engine is not None, f"engine not loadable at {fork_merge.engine_path()}"
    assert callable(getattr(engine, "sync", None))


def test_declines_when_asked_about_a_different_checkout(monkeypatch, tmp_path):
    """The engine merges its OWN checkout, ignoring the path argument. If a
    caller ever passes a different repo, merging something else and reporting on
    it would be the worst outcome — decline instead."""
    monkeypatch.delenv("HERMES_FORK_MERGE", raising=False)
    engine = _fake_engine(0)
    engine.REPO_ROOT = tmp_path / "some" / "other" / "checkout"
    monkeypatch.setattr(fork_merge, "load_engine", lambda root=None: engine)
    assert fork_merge.merge_outcome(tmp_path) == fork_merge.UNAVAILABLE
    assert engine.calls == [], "it must not run the engine against the wrong repo"


# ── the mount in `hermes update` ─────────────────────────────────────────────
@pytest.fixture
def upstream_sync_env(monkeypatch):
    """Drive main._sync_with_upstream_if_needed() with stubbed git counts."""
    from hermes_cli import main as hermes_main

    monkeypatch.setattr(hermes_main, "_has_upstream_remote", lambda git_cmd, cwd: True)

    def fake_run(cmd, *a, **kw):
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)
    return hermes_main


def _set_counts(monkeypatch, hermes_main, *, origin_ahead: int, upstream_ahead: int):
    def counts(git_cmd, cwd, base, head):
        return origin_ahead if head == "origin/main" else upstream_ahead

    monkeypatch.setattr(hermes_main, "_count_commits_between", counts)


def test_mount_fires_when_fork_has_own_commits(upstream_sync_env, monkeypatch, tmp_path, capsys):
    """The case that matters: the fork carries commits and upstream is ahead.
    Upstream's code would stop here; the mount must merge instead."""
    hermes_main = upstream_sync_env
    _set_counts(monkeypatch, hermes_main, origin_ahead=26, upstream_ahead=43)

    seen: list[tuple] = []
    monkeypatch.setattr(
        fork_merge, "merge_upstream_into_fork",
        lambda cwd, branch="main": seen.append((Path(cwd), branch)) or True,
    )

    hermes_main._sync_with_upstream_if_needed(["git"], tmp_path)

    assert seen == [(tmp_path, "main")], "the fork merge must be invoked with the repo path"
    assert "skipping upstream sync" not in capsys.readouterr().out.lower(), \
        "when the merge handled it, the 'skipping' message must not print"


def test_mount_falls_back_to_upstream_message(upstream_sync_env, monkeypatch, tmp_path, capsys):
    """When the fork machinery declines, upstream's original behavior must be
    intact — byte-for-byte the same user-visible outcome as unpatched Hermes."""
    hermes_main = upstream_sync_env
    _set_counts(monkeypatch, hermes_main, origin_ahead=26, upstream_ahead=43)
    monkeypatch.setattr(fork_merge, "merge_upstream_into_fork", lambda cwd, branch="main": False)

    hermes_main._sync_with_upstream_if_needed(["git"], tmp_path)

    out = capsys.readouterr().out
    assert "26 commit(s) not on upstream" in out
    assert "Skipping upstream sync to preserve your changes." in out
    assert "git pull upstream main" in out


def test_mount_does_not_fire_when_upstream_has_nothing(upstream_sync_env, monkeypatch, tmp_path):
    """No upstream commits means no merge to attempt — don't spin up the engine
    (and its test suite) just to be told there is nothing to do."""
    hermes_main = upstream_sync_env
    _set_counts(monkeypatch, hermes_main, origin_ahead=26, upstream_ahead=0)

    called: list[int] = []
    monkeypatch.setattr(
        fork_merge, "merge_upstream_into_fork",
        lambda cwd, branch="main": called.append(1) or True,
    )

    hermes_main._sync_with_upstream_if_needed(["git"], tmp_path)
    assert called == [], "the merge must not run when upstream is not ahead"


def _git(cwd: Path, *args: str) -> str:
    env = dict(__import__("os").environ, HERMES_SYNC_ALLOW_REWRITE="1")
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
    ).stdout.strip()


@pytest.fixture
def diverged_fork(tmp_path: Path) -> Path:
    """Three real repos, mirroring the live topology.

    upstream (NousResearch)  ->  origin.git (the fork on GitHub, bare)  ->  clone
    The clone carries a fork-only commit that is already pushed to origin, and
    upstream has since moved. That is exactly the state in which upstream Hermes
    prints "Skipping upstream sync to preserve your changes" and gives up.
    """
    up = tmp_path / "upstream"
    up.mkdir()
    _git(up, "init", "-q", "-b", "main", ".")
    _git(up, "config", "user.email", "u@u")
    _git(up, "config", "user.name", "u")
    (up / "app.txt").write_text("v1\n", encoding="utf-8")
    _git(up, "add", ".")
    _git(up, "commit", "-qm", "upstream base")

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(up), str(origin)],
                   capture_output=True, text=True)

    fk = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(fk)],
                   capture_output=True, text=True)
    _git(fk, "config", "user.email", "f@f")
    _git(fk, "config", "user.name", "f")
    _git(fk, "remote", "add", "upstream", str(up))
    (fk / "mine.txt").write_text("my customization\n", encoding="utf-8")
    _git(fk, "add", ".")
    _git(fk, "commit", "-qm", "custom(x): fork-only work")
    _git(fk, "push", "-q", "origin", "main")

    # Upstream moves on, touching a desktop file so the rebuild branch is live.
    desktop = up / "apps" / "desktop" / "src"
    desktop.mkdir(parents=True)
    (desktop / "new.ts").write_text("export const shipped = true\n", encoding="utf-8")
    _git(up, "add", ".")
    _git(up, "commit", "-qm", "upstream: new desktop work")
    return fk


def test_end_to_end_update_merges_upstream_and_keeps_fork_work(
    diverged_fork, monkeypatch, tmp_path, capsys
):
    """The whole chain on real repositories: `hermes update`'s fork branch ->
    adapter -> engine -> merge -> push.

    Asserts the four things that actually matter:
      1. upstream's new code arrives
      2. the fork's own commit survives
      3. the previous HEAD stays an ancestor (so the app's later `pull --ff-only`
         still works — this is what keeps the destructive reset unreachable)
      4. the fork on GitHub is updated
    """
    from hermes_cli import main as hermes_main

    engine = fork_merge.load_engine()
    assert engine is not None
    monkeypatch.setattr(engine, "REPO_ROOT", diverged_fork)
    monkeypatch.setattr(engine, "HISTORY_PATH", tmp_path / "history.jsonl")
    monkeypatch.setattr(engine, "REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr(engine, "verify", lambda changed, quick=False: (True, "stubbed"))
    monkeypatch.setattr(fork_merge, "load_engine", lambda root=None: engine)

    real_run = subprocess.run

    def fake_run(cmd, *a, **kw):
        joined = " ".join(str(c) for c in cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
        if "npm install" in joined or "--force-build" in joined:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(engine.subprocess, "run", fake_run)

    before = _git(diverged_fork, "rev-parse", "HEAD")

    hermes_main._sync_with_upstream_if_needed(["git"], diverged_fork)

    after = _git(diverged_fork, "rev-parse", "HEAD")
    out = capsys.readouterr().out

    assert after and after != before, f"HEAD must move; output was:\n{out}"
    assert (diverged_fork / "apps" / "desktop" / "src" / "new.ts").is_file(), \
        "upstream's new file must be present after the update"
    assert (diverged_fork / "mine.txt").is_file(), \
        "the fork's own work must survive the update"
    assert subprocess.run(
        ["git", "merge-base", "--is-ancestor", before, after],
        cwd=str(diverged_fork), capture_output=True,
    ).returncode == 0, "the previous HEAD must remain an ancestor (ff-only must still work)"

    origin_path = _git(diverged_fork, "remote", "get-url", "origin")
    assert _git(Path(origin_path), "rev-parse", "main") == after, \
        "the fork on GitHub must be updated after the local install catches up"
    assert "Skipping upstream sync" not in out, \
        "the give-up path must not run when the merge succeeded"


def test_mount_survives_a_broken_fork_module(upstream_sync_env, monkeypatch, tmp_path, capsys):
    """If the fork module raises on import or call, `hermes update` must still
    complete with upstream's behavior. Updating can never be breakable by us."""
    hermes_main = upstream_sync_env
    _set_counts(monkeypatch, hermes_main, origin_ahead=26, upstream_ahead=43)

    def explode(cwd, branch="main"):
        raise RuntimeError("fork module is broken")

    monkeypatch.setattr(fork_merge, "merge_upstream_into_fork", explode)

    hermes_main._sync_with_upstream_if_needed(["git"], tmp_path)
    assert "Skipping upstream sync to preserve your changes." in capsys.readouterr().out
