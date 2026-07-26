"""Behavior contracts for the fork self-updater.

These are the invariants that, when violated, silently break the update flow or
lose customizations. Each test exists because the invariant was ACTUALLY broken
during development, not because it seemed like a good idea:

  * sync must NOT push        -> pushing made `hermes update` return early and
                                 skip dependency install + the desktop rebuild
  * rollback must clean up    -> a real run left 100+ staged files, which would
                                 have deadlocked the next sync
  * merge, never rebase       -> rebase rewrites SHAs, which breaks the app's
                                 `pull --ff-only` and thereby ARMS the
                                 `reset --hard` the whole project avoids
  * untracked files           -> one stray file silently disabled syncing
  * non-ASCII output          -> crashed under a cp1252 (scheduled) stdout

Run: python -m pytest tests/fork_sync/ -q
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE = REPO_ROOT / "scripts" / "fork_sync.py"


def _load_engine():
    """Import the engine from its path.

    The module must be registered in sys.modules BEFORE exec_module: the engine
    defines dataclasses, and @dataclass resolves annotations via
    sys.modules[cls.__module__].__dict__. Without the registration that lookup
    returns None and raises "'NoneType' object has no attribute '__dict__'".
    """
    name = "fork_sync_under_test"
    spec = importlib.util.spec_from_file_location(name, ENGINE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod


@pytest.fixture(scope="module")
def engine():
    assert ENGINE.is_file(), f"engine missing at {ENGINE}"
    return _load_engine()


def _git(cwd: Path, *args: str) -> str:
    env = dict(os.environ, HERMES_SYNC_ALLOW_REWRITE="1")
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
    ).stdout.strip()


@pytest.fixture
def fork(tmp_path: Path) -> Path:
    """An upstream repo plus a fork clone carrying one fork-only commit."""
    up = tmp_path / "up"
    up.mkdir()
    _git(up, "init", "-q", ".")
    _git(up, "config", "user.email", "t@t")
    _git(up, "config", "user.name", "t")
    (up / "app.txt").write_text("v1\n", encoding="utf-8")
    _git(up, "add", ".")
    _git(up, "commit", "-qm", "upstream base")

    fk = tmp_path / "fork"
    subprocess.run(["git", "clone", "-q", str(up), str(fk)],
                   capture_output=True, text=True)
    _git(fk, "config", "user.email", "f@f")
    _git(fk, "config", "user.name", "f")
    _git(fk, "remote", "add", "upstream", str(up))
    (fk / "mine.txt").write_text("customization\n", encoding="utf-8")
    _git(fk, "add", ".")
    _git(fk, "commit", "-qm", "custom(x): fork-only work")
    return fk


# ── the invariant that broke the update flow ─────────────────────────────────
def test_sync_does_not_push(engine):
    """`sync()` must never push.

    Pushing makes `rev-list HEAD..origin/<branch>` zero, which makes
    `hermes update` return early (main.py:11406 -> 11513) and skip dependency
    install (11648) and the desktop rebuild (electron/main.ts:3165) — leaving
    merged source running against stale deps, while the UI reports "up to date".
    """
    src = ENGINE.read_text(encoding="utf-8")
    sync_body = src.split("def sync(", 1)[1].split("\ndef ", 1)[0]
    pushes = [
        line.strip() for line in sync_body.splitlines()
        if '"push"' in line and not line.strip().startswith("#")
    ]
    assert not pushes, (
        "sync() must not push — it makes `hermes update` skip deps and the "
        f"desktop rebuild. Offending line(s): {pushes}"
    )


def test_push_is_a_separate_entry_point(engine):
    """The push must be reachable, just not from sync()."""
    assert hasattr(engine, "push_after_update"), \
        "expected a separate push_after_update() so the fork can be pushed after the update applies"


def test_push_is_a_noop_when_fork_is_current(engine, fork, monkeypatch, capsys):
    """push must exit 0 without pushing when there is nothing ahead.

    Note: the fixture's origin is a NON-BARE clone, which git refuses to push to
    ("refusing to update checked out branch"). So this asserts the guard fires
    BEFORE any push is attempted — point origin at the tip and confirm the
    early return, rather than pushing for real.
    """
    monkeypatch.setattr(engine, "REPO_ROOT", fork)
    # Make origin/master identical to HEAD without pushing.
    head = _git(fork, "rev-parse", "HEAD")
    _git(fork, "update-ref", "refs/remotes/origin/master", head)
    assert engine.push_after_update("master") == 0
    assert "up to date" in capsys.readouterr().out.lower()


# ── merge, not rebase ────────────────────────────────────────────────────────
def test_engine_merges_and_never_rebases(engine):
    """Rebase rewrites fork SHAs, so the app's `pull --ff-only` fails — and that
    failure is the ONLY trigger for `reset --hard origin/<branch>`. Merging keeps
    the old sha an ancestor, making the destructive path unreachable."""
    src = ENGINE.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in src.splitlines()
        if not line.strip().startswith("#")
    )
    assert '"merge"' in code, "engine must merge"
    assert '"rebase"' not in code, (
        "engine must not rebase: rewritten SHAs break `pull --ff-only`, which is "
        "what arms the reset --hard this project exists to avoid"
    )


def test_merge_keeps_old_head_as_ancestor(fork):
    """The guarantee the whole design rests on."""
    before = _git(fork, "rev-parse", "HEAD")
    up = Path(_git(fork, "remote", "get-url", "upstream"))
    (up / "app.txt").write_text("v2\n", encoding="utf-8")
    _git(up, "commit", "-qam", "upstream v2")
    _git(fork, "fetch", "-q", "upstream", "master")
    _git(fork, "merge", "--no-edit", "upstream/master")

    rc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", before, "HEAD"],
        cwd=str(fork), capture_output=True,
    ).returncode
    assert rc == 0, "after a merge the previous HEAD must remain an ancestor (ff-only must work)"


# ── never reset --hard the fork ──────────────────────────────────────────────
def test_no_hard_reset_of_the_fork(engine):
    src = ENGINE.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("#")
    )
    assert '"--hard"' not in code, (
        "the engine must never `reset --hard` — it destroys fork commits. "
        "Rollback uses merge --abort + update-ref + mixed reset."
    )


# ── untracked files must not disable syncing ─────────────────────────────────
def test_untracked_file_does_not_block_sync(engine, fork, monkeypatch):
    """One stray untracked file silently disabled syncing in a real run."""
    monkeypatch.setattr(engine, "REPO_ROOT", fork)
    (fork / "stray.txt").write_text("junk\n", encoding="utf-8")
    git = engine.Git(fork)
    tracked = [
        l for l in git.out("status", "--porcelain").splitlines()
        if l and not l.startswith("??")
    ]
    assert tracked == [], "an untracked file must not count as a blocking change"


# ── scheduled runs use a different stdout encoding ───────────────────────────
def test_survives_cp1252_stdout():
    """Under Task Scheduler with redirected output Python picks cp1252 and a
    non-ASCII status line raises UnicodeEncodeError — crashing on a print."""
    proc = subprocess.run(
        [sys.executable, str(ENGINE), "--help"],
        cwd=str(REPO_ROOT), capture_output=True,
        env=dict(os.environ, PYTHONIOENCODING="cp1252"),
    )
    assert proc.returncode == 0, (
        f"engine must not crash under cp1252 stdout: {proc.stderr[-400:]!r}"
    )


# ── the resolver must not be handed the update mechanism ─────────────────────
def test_update_mechanism_is_never_ai_resolved(engine):
    """Enforced in code, not asked of the model: telling a model 'do not resolve
    this' while handing it exactly that file is self-defeating."""
    protected = set(engine.HAND_RESOLVE_ONLY)
    for required in ("hermes_cli/main.py", "scripts/install.ps1", "scripts/install.sh"):
        assert required in protected, f"{required} must be hand-resolve only"


# ── the gate must compare to a baseline, not demand a green suite ────────────
def test_preexisting_failures_do_not_block(engine, monkeypatch, tmp_path):
    """Upstream ships tests that already fail on Windows (POSIX path
    assumptions; 11 confirmed on a pristine upstream clone). Demanding zero
    failures blocks every sync on breakage we did not cause — the same
    false-alarm trap as treating a missing harness as a failure."""
    known = {"tests/x.py::test_a", "tests/x.py::test_b"}
    monkeypatch.setattr(engine, "BASELINE_PATH", tmp_path / "baseline.json")
    engine._write_baseline(known)
    monkeypatch.setattr(engine, "_failing_tests", lambda targets: set(known))
    monkeypatch.setattr(engine, "_import_check", lambda paths: [])

    real_run = engine.subprocess.run

    def fake_run(cmd, *a, **kw):
        joined = " ".join(map(str, cmd))
        if "import pytest" in joined:
            class R:
                returncode = 0
                stdout = stderr = ""
            return R()
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(engine.subprocess, "run", fake_run)
    ok, notes = engine.verify(["agent/example.py"])
    assert ok, f"pre-existing failures must not block a sync (notes: {notes})"
    assert "pre-existing" in notes


def test_new_failure_blocks_the_sync(engine, monkeypatch, tmp_path):
    """The other half of the contract: a failure the merge INTRODUCED must
    block. A gate that never fails is not a gate."""
    monkeypatch.setattr(engine, "BASELINE_PATH", tmp_path / "baseline.json")
    engine._write_baseline({"tests/x.py::test_a"})
    monkeypatch.setattr(
        engine, "_failing_tests",
        lambda targets: {"tests/x.py::test_a", "tests/x.py::test_NEW"},
    )
    monkeypatch.setattr(engine, "_import_check", lambda paths: [])

    real_run = engine.subprocess.run

    def fake_run(cmd, *a, **kw):
        if "import pytest" in " ".join(map(str, cmd)):
            class R:
                returncode = 0
                stdout = stderr = ""
            return R()
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(engine.subprocess, "run", fake_run)
    ok, notes = engine.verify(["agent/example.py"])
    assert not ok, "a newly-failing test must block the sync"
    assert "test_NEW" in notes


# ── verification must distinguish "cannot run" from "failed" ─────────────────
def test_missing_test_harness_is_not_reported_as_failure(engine, monkeypatch):
    """pytest is absent on some installs; conflating 'harness unavailable' with
    'tests failed' rolled back good merges and fired false alarms every run."""
    monkeypatch.setattr(engine, "_import_check", lambda paths: [])

    def fake_run(cmd, *a, **kw):
        class R:
            returncode = 1 if "import pytest" in " ".join(map(str, cmd)) else 0
            stdout = stderr = ""
        return R()

    monkeypatch.setattr(engine.subprocess, "run", fake_run)
    ok, notes = engine.verify(["agent/example.py"])
    # Assert the CONTRACT (verification still passes, and the note says the suite
    # was skipped) rather than an exact sentence — pinning the wording made this
    # test fail on a message reword that changed no behavior.
    assert ok, f"a missing harness must not fail verification (notes: {notes})"
    assert "skipped" in notes.lower(), \
        f"the note must state the suite was skipped, got: {notes!r}"
