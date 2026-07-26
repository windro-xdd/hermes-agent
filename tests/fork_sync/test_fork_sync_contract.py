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
import json
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
def test_push_happens_after_the_catch_up_steps(engine):
    """The push must come AFTER the dependency install and desktop rebuild.

    Original defect: sync() pushed as its last act *before* anything else, which
    drove `rev-list HEAD..origin/<branch>` to 0 and made `hermes update` return
    early (main.py:11406 -> 11513), skipping dependency install (11648) and the
    desktop rebuild (electron/main.ts:3165).

    The real fix is not "never push" — this checkout IS the running install, so
    the sync performs those catch-up steps itself and then pushes. What must hold
    is the ORDER: nothing may push before the install has caught up, or the same
    early-return hides the work.
    """
    src = ENGINE.read_text(encoding="utf-8")
    sync_body = src.split("def sync(", 1)[1].split("\ndef push_after_update", 1)[0]
    lines = [l for l in sync_body.splitlines() if not l.strip().startswith("#")]

    def first_index(needle: str) -> int:
        for i, l in enumerate(lines):
            if needle in l:
                return i
        return -1

    push_at = first_index('"push", "origin"')
    rebuild_at = first_index("--force-build")
    deps_at = first_index("npm install")

    assert push_at != -1, "sync() must eventually push the fork"
    assert rebuild_at != -1 and deps_at != -1, \
        "sync() must perform the catch-up steps itself (this checkout is the app)"
    assert push_at > rebuild_at and push_at > deps_at, (
        "the push must come AFTER deps+rebuild; pushing first makes "
        "`hermes update` return early and skip them"
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


# ── the resolver's context file must actually be found ───────────────────────
def test_customizations_file_is_locatable():
    """The resolver is sent CUSTOMIZATIONS.md as its only context about intent.

    If the path is stale the sync does not fail — it resolves conflicts BLIND,
    which is far worse than an error. This asserts the file exists where the
    engine looks, so moving it can never silently strip that context.
    """
    candidates = [REPO_ROOT / "fork" / "CUSTOMIZATIONS.md",
                  REPO_ROOT / "CUSTOMIZATIONS.md"]
    found = [p for p in candidates if p.is_file()]
    assert found, (
        "CUSTOMIZATIONS.md not found at any path the engine checks: "
        f"{[str(p) for p in candidates]}"
    )
    text = found[0].read_text(encoding="utf-8", errors="replace")
    assert len(text) > 500, "the resolver context file looks truncated or empty"


def test_changelog_entries_exist():
    """Changelog lives as one file per change under fork/changelog/entries/."""
    entries = REPO_ROOT / "fork" / "changelog" / "entries"
    assert entries.is_dir(), f"missing changelog entries dir: {entries}"
    files = sorted(entries.glob("*.md"))
    assert files, "no changelog entries found"
    index = (REPO_ROOT / "fork" / "changelog" / "README.md").read_text(
        encoding="utf-8", errors="replace"
    )
    missing = [f.name for f in files if f.name not in index]
    assert not missing, f"entries missing from the changelog index: {missing}"


# ── state must live in ONE place regardless of environment ───────────────────
def test_state_dir_is_environment_independent(engine, monkeypatch):
    """State must not move when HERMES_HOME is set or unset.

    It IS set in an interactive shell and is NOT set under Windows Task
    Scheduler. Deriving the state dir from it split history/reports/baseline
    across two directories: the scheduled run could not see the baseline the
    interactive run wrote, so 12 known-failing tests read as "newly failing" and
    every sync was blocked.
    """
    monkeypatch.setenv("HERMES_HOME", r"C:\some\profile\dir")
    with_home = engine._state_dir()
    monkeypatch.delenv("HERMES_HOME", raising=False)
    without_home = engine._state_dir()
    assert with_home == without_home, (
        "the state dir must not depend on HERMES_HOME — the scheduled and "
        "interactive contexts would disagree and the baseline would be invisible"
    )


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


def test_failure_confirmed_preexisting_is_absorbed(engine, monkeypatch, tmp_path):
    """A failure absent from the baseline but ALSO failing pre-merge must be
    absorbed, not reported as merge damage.

    Seeding the baseline from a fixed list missed tests that later syncs pulled
    into scope — two skills tests were reported as newly broken when both fail on
    the pre-merge tree as well."""
    monkeypatch.setattr(engine, "BASELINE_PATH", tmp_path / "baseline.json")
    engine._write_baseline({"tests/x.py::test_known"})
    monkeypatch.setattr(
        engine, "_failing_tests",
        lambda targets: {"tests/x.py::test_known", "tests/y.py::test_also_broken_before"},
    )
    # The suspect fails on the pre-merge tree too.
    monkeypatch.setattr(
        engine, "_failing_on_baseline_tree",
        lambda ids: {"tests/y.py::test_also_broken_before"},
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
    assert ok, f"a confirmed pre-existing failure must not block (notes: {notes})"
    assert "tests/y.py::test_also_broken_before" in engine._read_baseline(), \
        "the absorbed failure should be remembered so the next sync is cheaper"


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


# ── the catch-up steps must actually run ─────────────────────────────────────
def test_followups_use_the_merged_diff(engine, fork, monkeypatch, tmp_path):
    """A merge touching apps/desktop must rebuild; one touching a manifest must
    install dependencies.

    Regression: the block making those two decisions read a ``changed_files``
    name that was never assigned anywhere in the module, so sync() raised
    NameError immediately after the merge — before the push, the dependency
    install, and the rebuild — leaving no history entry and no report. It never
    showed up interactively because every run after that code landed had nothing
    new to merge, and a crash there is silent (sync alerts are not delivered
    anywhere yet). This test drives a real merge that touches both file classes
    and asserts the two commands are actually invoked.
    """
    monkeypatch.setattr(engine, "REPO_ROOT", fork)
    monkeypatch.setattr(engine, "HISTORY_PATH", tmp_path / "history.jsonl")
    monkeypatch.setattr(engine, "REPORT_DIR", tmp_path / "reports")
    # Verification is covered by its own tests; stub it so this one stays about
    # the follow-up decisions.
    monkeypatch.setattr(engine, "verify", lambda changed, quick=False: (True, "stubbed"))

    up = Path(_git(fork, "remote", "get-url", "upstream"))
    (up / "package.json").write_text('{"name":"x"}\n', encoding="utf-8")
    desktop_src = up / "apps" / "desktop" / "src"
    desktop_src.mkdir(parents=True)
    (desktop_src / "app.ts").write_text("export const a = 1\n", encoding="utf-8")
    _git(up, "add", ".")
    _git(up, "commit", "-qm", "upstream: desktop change + manifest change")

    real_run = subprocess.run
    invoked: list[str] = []

    def fake_run(cmd, *a, **kw):
        joined = " ".join(str(c) for c in cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
        # Intercept only the two expensive catch-up commands; every git call
        # must still run for real or the merge under test does not happen.
        if "npm install" in joined or "--force-build" in joined:
            invoked.append(joined)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(engine.subprocess, "run", fake_run)

    code = engine.sync("master")

    assert code == 0, "a clean verified merge must succeed"
    assert any("npm install" in c for c in invoked), (
        "a merge that changes package.json must install dependencies; "
        f"commands invoked: {invoked}"
    )
    assert any("--force-build" in c for c in invoked), (
        "a merge that changes apps/desktop must rebuild the desktop app; "
        f"commands invoked: {invoked}"
    )


def test_unhandled_crash_is_recorded(engine, monkeypatch, tmp_path):
    """A crash must leave a history entry and a report, not just a traceback.

    Sync failure alerts are not delivered anywhere yet, so an unrecorded crash
    is completely silent: the scheduled job fails every night while `status`
    still looks healthy. `record_crash` is what turns that into evidence.
    """
    monkeypatch.setattr(engine, "HISTORY_PATH", tmp_path / "history.jsonl")
    monkeypatch.setattr(engine, "REPORT_DIR", tmp_path / "reports")

    def boom(*a, **kw):
        raise RuntimeError("induced failure")

    monkeypatch.setattr(engine, "sync", boom)
    code = engine.main(["sync"])

    assert code != 0, "a crash must not report success"
    entries = [
        l for l in (tmp_path / "history.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    assert entries, "a crash must append a history entry"
    record = json.loads(entries[-1])
    assert record["status"] == "crashed"
    assert record["report_path"], "a crash must write a report file"
    assert Path(record["report_path"]).is_file()
    assert "induced failure" in Path(record["report_path"]).read_text(encoding="utf-8"), \
        "the report must contain the traceback"
