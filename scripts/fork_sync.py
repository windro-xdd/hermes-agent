#!/usr/bin/env python3
"""Keep windro's fork current with upstream, without ever losing a customization.

WHY THIS EXISTS
    The stock update path destroys fork commits. On divergence it runs
    ``git reset --hard origin/<branch>`` — verified at three separate sites:
    hermes_cli/main.py:11540, scripts/install.ps1:1524, scripts/install.sh:1226.
    install.ps1's own comment says it is there to discard "local-only commits".

THE KEY DESIGN DECISION: MERGE, NOT REBASE
    The project plan specified rebase. Testing showed rebase is the WRONG choice
    here, and the reason is subtle enough to write down.

    Rebase rewrites the fork's commit SHAs every sync. The desktop app's local
    checkout still sits at an OLD sha, which is then no longer an ancestor of the
    fork tip — so ``git pull --ff-only`` FAILS, and failing ff-only is the exact
    and only trigger for ``reset --hard`` at main.py:11540. Rebasing would
    manufacture the very condition that destroys the fork.

    Merge keeps every existing commit reachable, so the old sha stays an ancestor
    and the app's pull is always a clean fast-forward. Verified empirically:

        rebase: old fork sha ancestor of new tip -> NO  (ff-only fails)
        merge:  old fork sha ancestor of new tip -> YES (ff-only succeeds)

    Consequences of choosing merge:
      * The destructive reset becomes UNREACHABLE in normal operation, so the
        highest-churn file in the tree (hermes_cli/main.py — 455 commits/90d)
        does NOT need patching. Zero upstream files modified.
      * No force-push. Nothing is ever rewritten, so nothing can be lost to a
        rewrite. Recovery is always "check out the tag".
      * History shows merge commits instead of a linear replay. Cosmetic.

HOW IT RUNS
    Unattended, on a schedule. Fetch upstream, tag a rollback point, merge, and
    if the merge is clean and verification passes, push the fork. The desktop
    app's normal update check then sees new commits on origin and shows its
    native popup; clicking Update is a plain fast-forward of already-verified
    code.

CONFLICT POLICY
    A conflict is resolved by claude-opus-5 via the local kiro proxy, then
    verified by build/tests before being accepted. Low confidence, a failed
    verification, or a conflict in the update mechanism itself -> abort, restore
    the rollback point, and notify. A failed sync DEFERS; it never ships a
    broken tree and never leaves the app unrunnable.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent

# Force UTF-8 on stdout/stderr. When this runs unattended under Windows Task
# Scheduler with output redirected to a file, Python picks cp1252 and every
# non-ASCII character in a status line raises UnicodeEncodeError — crashing the
# sync on a *print statement*. Interactive runs never hit this because the
# console is UTF-8, so it only appears once the job is actually automated.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - very old Python
        pass

PROXY_URL = "http://127.0.0.1:8081/v1/messages"
RESOLVER_MODEL = "claude-opus-5"
RESOLVER_MAX_TOKENS = 32000
PROXY_TIMEOUT_S = 600

# Conflicts in these files are NEVER sent to the model. They ARE the update
# mechanism; a wrong resolution here can destroy the ability to update at all.
# Enforced in code rather than asked of the model — instructing a model "do not
# resolve this" while handing it exactly that file is self-defeating.
HAND_RESOLVE_ONLY = (
    "hermes_cli/main.py",
    "scripts/install.ps1",
    "scripts/install.sh",
    "scripts/fork_sync.py",
    "scripts/fork-guard.sh",
)

CONFLICT_MARKER_RE = re.compile(r"^(<{7}|={7}|>{7})", re.MULTILINE)


def _state_dir() -> Path:
    """Where sync history, reports, and the test baseline live.

    Outside the repo, so this state is never committed and never becomes conflict
    surface.

    Deliberately NOT derived from HERMES_HOME. That variable is set in an
    interactive shell (pointing at a per-profile dir like
    .../hermes/profiles/dev-xd) but NOT under Windows Task Scheduler, so the two
    contexts wrote to different directories. The scheduled run then could not see
    the baseline the interactive run had seeded, and reported 12 known-failing
    tests as "newly failing" — blocking every sync. History and reports were
    fragmenting the same way.

    Anchored to the install root instead: one location, same answer in every
    context, and correct regardless of which profile is active (this state
    describes the CHECKOUT, not a profile).
    """
    return REPO_ROOT.parent / "fork-sync"


HISTORY_PATH = _state_dir() / "history.jsonl"
REPORT_DIR = _state_dir() / "reports"


# ── git plumbing ─────────────────────────────────────────────────────────────
@dataclass
class Git:
    """Thin git runner. Never raises on non-zero exit; callers decide."""

    cwd: Path

    def __call__(self, *args: str, allow_rewrite: bool = False) -> subprocess.CompletedProcess:
        # windows.appendAtomically=false mirrors what the stock updater uses to
        # work around a Windows git ref-write bug.
        cmd = ["git", "-c", "windows.appendAtomically=false", *args]
        env = dict(os.environ)
        if allow_rewrite:
            # Tell the fork-guard hook this is our own deliberate operation.
            env["HERMES_SYNC_ALLOW_REWRITE"] = "1"
        return subprocess.run(
            cmd,
            cwd=str(self.cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )

    def out(self, *args: str) -> str:
        return self(*args).stdout.strip()

    def ok(self, *args: str) -> bool:
        return self(*args).returncode == 0


# ── result recording ─────────────────────────────────────────────────────────
@dataclass
class Resolution:
    """One AI-resolved conflict, recorded so it can be reviewed later."""

    path: str
    confidence: str
    reasoning: str
    accepted: bool
    merged_preview: str = ""


@dataclass
class SyncRecord:
    """One sync attempt. Appended to history.jsonl for the UI and for auditing."""

    started_at: str
    status: str = "running"          # ok | up-to-date | failed | deferred
    upstream_commits: int = 0
    fork_patches: int = 0
    rollback_tag: str = ""
    pre_sha: str = ""
    post_sha: str = ""
    message: str = ""
    conflicts: list[str] = field(default_factory=list)
    resolutions: list[dict] = field(default_factory=list)
    verification: str = ""
    pushed: bool = False
    duration_s: float = 0.0
    report_path: str = ""

    def save(self) -> None:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with HISTORY_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(self), ensure_ascii=False) + "\n")


def _write_report(name: str, body: str) -> str:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = REPORT_DIR / f"{name}-{stamp}.txt"
    path.write_text(body, encoding="utf-8")
    return str(path)


# ── the resolver ─────────────────────────────────────────────────────────────
RESOLVER_SYSTEM_PROMPT = """\
You resolve a git MERGE conflict while bringing windro's personal customizations
onto a NEW upstream release of Hermes (Electron + Python AI agent).
Inputs: CUSTOMIZATIONS.md (what each patch does + why) and one conflicted file.
In this merge, "ours" (<<<<<<<) = OUR fork's code; "theirs" (>>>>>>>) = NEW UPSTREAM.
Goal: produce a merged file that keeps BOTH upstream's new behavior AND our
customization's intent.
Rules:
- Never weaken/delete an upstream change to make ours apply — adapt ours into the
  new upstream structure.
- Never drop our customization — re-express it against the new code.
- Prefer minimal, additive edits; match upstream's current style and APIs.
- If upstream removed the seam our patch used, adapt to the new seam; if truly
  impossible, leave a clearly-marked TODO(custom-hermes) and lower confidence.
Output ONLY the full resolved file contents (no conflict markers, no commentary).
Then a final line: CONFIDENCE: high|medium|low — <one sentence why>.
"""


def resolve_with_ai(rel_path: str, body: str, customizations: str) -> dict[str, Any]:
    """Ask the resolver for a merged file.

    Returns {ok, content, confidence, reasoning} or {ok: False, reason}.
    """
    user = (
        "=== CUSTOMIZATIONS.md ===\n"
        f"{customizations}\n\n"
        f"=== CONFLICTED FILE: {rel_path} ===\n"
        f"{body}"
    )
    payload = json.dumps(
        {
            "model": RESOLVER_MODEL,
            "max_tokens": RESOLVER_MAX_TOKENS,
            "system": RESOLVER_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user}],
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        PROXY_URL,
        data=payload,
        headers={
            "content-type": "application/json",
            "x-api-key": "no-key-required",
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=PROXY_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        return {"ok": False, "reason": f"proxy unreachable: {exc}"}
    except json.JSONDecodeError as exc:
        return {"ok": False, "reason": f"proxy returned invalid JSON: {exc}"}

    # The proxy emits a `thinking` block BEFORE `text` on reasoning-heavy
    # prompts, and only `text` on trivial ones — verified on this machine. Take
    # the LAST text block: content[0] hands back the model's reasoning as file
    # contents, and content[1] would IndexError on single-block replies.
    blocks = data.get("content") or []
    texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
    if not texts:
        return {"ok": False, "reason": "no text block in resolver response"}
    raw = texts[-1]
    thinking = next(
        (b.get("thinking", "") for b in blocks if b.get("type") == "thinking"), ""
    )

    lines = raw.splitlines()
    conf_idx = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip().upper().startswith("CONFIDENCE"):
            conf_idx = i
            break
    if conf_idx is None:
        return {"ok": False, "reason": "resolver omitted the CONFIDENCE line"}

    conf_line = lines[conf_idx]
    low = conf_line.lower()
    confidence = "high" if "high" in low else "medium" if "medium" in low else "low"

    content = "\n".join(lines[:conf_idx]).strip()
    # Strip a markdown fence if the model wrapped the file.
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else ""
        if content.rstrip().endswith("```"):
            content = content.rstrip()[:-3]
    content = content.strip()

    if not content:
        return {"ok": False, "reason": "resolver returned an empty file"}
    if CONFLICT_MARKER_RE.search(content):
        return {"ok": False, "reason": "resolver left conflict markers in the output"}

    return {
        "ok": True,
        "content": content + "\n",
        "confidence": confidence,
        "reasoning": conf_line.strip(),
        "thinking": thinking,
    }


# ── verification ─────────────────────────────────────────────────────────────
def _import_check(rel_paths: list[str]) -> list[str]:
    """Import each changed module in a subprocess; return the ones that fail.

    Weaker than the test suite but far stronger than a parse check: it executes
    module-level code, so a bad merge that references a removed upstream symbol
    is caught. Modules that are not importable in isolation (scripts, __main__
    guards, optional deps) are skipped rather than reported as failures — a
    false alarm here would roll back a good sync.
    """
    failures: list[str] = []
    for rel in rel_paths:
        if not rel.endswith(".py"):
            continue
        # Only check importable package modules; standalone scripts and test
        # files are not meant to be imported.
        if rel.startswith(("tests/", "scripts/")) or "/" not in rel.replace("\\", "/"):
            continue
        module = rel[:-3].replace("/", ".").replace("\\", ".")
        proc = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
        if proc.returncode != 0:
            err = (proc.stderr or "").strip().splitlines()
            last = err[-1] if err else "unknown error"
            # A missing optional third-party dep is an environment gap, not a
            # merge defect. Do not roll back a good sync over it.
            if "ModuleNotFoundError" in last and module.split(".")[0] not in last:
                continue
            failures.append(f"{rel}: {last}")
    return failures


BASELINE_PATH = _state_dir() / "test-baseline.json"


def _repo_python() -> str:
    """The repo venv's interpreter, falling back to the current one.

    Checks both layouts: Windows venvs use Scripts/, POSIX uses bin/. Assuming
    one layout is what made the test-harness probe fail silently on Windows.
    """
    for rel in ("venv/Scripts/python.exe", "venv/bin/python", ".venv/Scripts/python.exe",
                ".venv/bin/python"):
        cand = REPO_ROOT / rel
        if cand.is_file():
            return str(cand)
    return sys.executable


def _failing_tests(targets: list[str]) -> set[str]:
    """Run pytest on `targets` and return the set of failing test node ids.

    Returns ids rather than a pass/fail verdict so the caller can diff against a
    baseline: on this platform upstream ships tests that already fail (POSIX path
    assumptions), and a boolean would make every sync look broken.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *dict.fromkeys(targets),
         "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=2400,
    )
    out = proc.stdout + proc.stderr
    failing = {
        line.split(" ", 1)[1].split(" - ")[0].strip()
        for line in out.splitlines()
        if line.startswith(("FAILED ", "ERROR "))
    }
    # A collection error or crash yields no FAILED lines but a non-zero exit;
    # surface that rather than silently reporting "no failures".
    if proc.returncode != 0 and not failing:
        failing.add(f"<pytest exited {proc.returncode} with no failure list>")
    return failing


def _failing_on_baseline_tree(node_ids: list[str]) -> set[str]:
    """Of `node_ids`, which already fail on the PRE-MERGE tree?

    Called only when a test looks newly broken. Runs the suspects against a
    pristine worktree checked out at the rollback point, so "the merge broke
    this" is proven rather than assumed. A `git stash`-style approach would risk
    the live tree; a detached worktree touches nothing.
    """
    if not node_ids:
        return set()
    git = Git(REPO_ROOT)
    pre = git.out("rev-parse", "HEAD~1") if git.ok("rev-parse", "HEAD~1") else ""
    # Prefer the sync's own rollback tag when present (most recent wins).
    tags = [t for t in git.out("tag", "--list", "pre-sync-*").splitlines() if t]
    if tags:
        pre = git.out("rev-parse", sorted(tags)[-1])
    if not pre:
        return set()

    wt = _state_dir() / "baseline-worktree"
    git("worktree", "remove", "--force", str(wt), allow_rewrite=True)
    if not git.ok("worktree", "add", "--detach", "--quiet", str(wt), pre):
        return set()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *node_ids,
             "-q", "--no-header", "-p", "no:cacheprovider"],
            cwd=str(wt), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=1200,
        )
        out = proc.stdout + proc.stderr
        return {
            line.split(" ", 1)[1].split(" - ")[0].strip()
            for line in out.splitlines()
            if line.startswith(("FAILED ", "ERROR "))
        }
    except (subprocess.SubprocessError, OSError):
        return set()
    finally:
        git("worktree", "remove", "--force", str(wt), allow_rewrite=True)


def _read_baseline() -> set[str]:
    try:
        return set(json.loads(BASELINE_PATH.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return set()


def _write_baseline(failing: set[str]) -> None:
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(
        json.dumps(sorted(failing), indent=1), encoding="utf-8"
    )


def verify(changed: Iterable[str], *, quick: bool = False) -> tuple[bool, str]:
    """Nothing is accepted unverified.

    The suite is chosen by WHAT changed. An earlier draft gated everything on the
    TypeScript typecheck, which would have accepted an AI resolution of a Python
    file without ever executing it.
    """
    changed = list(changed)
    py = [f for f in changed if f.endswith(".py")]
    ts = [f for f in changed if f.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"))]
    notes: list[str] = []

    # Always: every changed Python file must at least parse. Cheap, and catches
    # the syntax-error-bricks-the-CLI class the stock updater also guards.
    import ast

    for rel in py:
        target = REPO_ROOT / rel
        if not target.exists():
            continue
        try:
            ast.parse(target.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            return False, f"{rel} does not parse: {exc}"
    if py:
        notes.append(f"parsed {len(py)} python file(s)")

    if quick:
        return True, "; ".join(notes) or "parse-only (quick mode)"

    if py:
        # The suite needs a venv that scripts/run_tests.sh can actually SELECT.
        # "The harness cannot run" is NOT the same failure as "the tests failed":
        # conflating them fails every sync and trains the user to ignore the
        # alert. So probe what run_tests.sh itself probes, not just whether
        # pytest imports here.
        #
        # run_tests.sh (lines ~53-56) looks for `<venv>/bin/activate` and runs
        # `<venv>/bin/python -c 'import pytest'` — a POSIX layout. On Windows a
        # venv has Scripts/, not bin/, so the probe NEVER matches and the script
        # exits with "no virtualenv with pytest found" even when pytest is
        # installed. Checking `import pytest` in our own interpreter was the
        # wrong test: it passed while the harness still could not start.
        runner_can_start = any(
            (REPO_ROOT / v / "bin" / "activate").is_file()
            and subprocess.run(
                [str(REPO_ROOT / v / "bin" / "python"), "-c", "import pytest"],
                capture_output=True,
            ).returncode == 0
            for v in (".venv", "venv")
        )

        if not runner_can_start:
            # run_tests.sh can't start here, but pytest itself may still work.
            # Prefer the REAL suite over an import check whenever possible —
            # skipping straight to imports threw away a genuine gate.
            have_pytest = subprocess.run(
                [sys.executable, "-c", "import pytest"], capture_output=True
            ).returncode == 0

            if have_pytest:
                # Scope to tests that plausibly cover the changed modules, plus
                # this project's own contracts. A full 2,300-file run per sync is
                # not viable on a laptop.
                targets = ["tests/fork_sync"]
                for rel in py:
                    guess = Path("tests") / Path(rel).with_suffix("")
                    cand = REPO_ROOT / guess.parent / f"test_{guess.name}.py"
                    if cand.is_file():
                        targets.append(cand.relative_to(REPO_ROOT).as_posix())
                failed_now = _failing_tests(targets)

                # Self-healing baseline: a failure is only "new" if the SAME test
                # passes on the pre-merge tree. Seeding the baseline from a fixed
                # list was fragile — it missed tests that a later sync happened to
                # pull into scope (two skills tests surfaced this way and were
                # wrongly reported as merge damage, when both fail pre-merge too).
                #
                # Re-running the suspects against the rollback point costs one
                # extra pytest invocation and only happens when something looks
                # broken, so the common path is unaffected.
                candidates = failed_now - _read_baseline()
                if candidates:
                    confirmed_preexisting = _failing_on_baseline_tree(sorted(candidates))
                    if confirmed_preexisting:
                        _write_baseline(_read_baseline() | confirmed_preexisting)
                        failed_now -= confirmed_preexisting

                # Compare against the PRE-MERGE baseline instead of demanding a
                # fully green suite. Upstream ships tests that already fail on
                # this platform (POSIX path assumptions: 11 confirmed failures in
                # test_managed_uv.py / test_config.py, reproduced on a pristine
                # upstream clone). Requiring zero failures blocks EVERY sync on
                # breakage we did not cause and cannot fix — which is the same
                # false-alarm trap as conflating a missing harness with failure.
                #
                # What actually matters is whether the merge made things WORSE.
                baseline = _read_baseline()
                new_failures = sorted(failed_now - baseline)
                if new_failures:
                    listed = "\n  ".join(new_failures[:15])
                    return False, (
                        f"{len(new_failures)} test(s) newly failing after the merge:"
                        f"\n  {listed}"
                    )

                # Re-read: absorbing a confirmed pre-existing failure above may
                # have widened the baseline, and writing `failed_now` blindly
                # would drop that record (the absorbed test is subtracted from
                # failed_now, so it would silently vanish and be re-investigated
                # on every future sync).
                baseline = _read_baseline()
                fixed = sorted(baseline - failed_now)
                _write_baseline(baseline | failed_now)
                note = f"no new test failures ({len(failed_now)} pre-existing"
                if fixed:
                    note += f", {len(fixed)} newly fixed upstream"
                notes.append(note + ")")
            else:
                failed_imports = _import_check(py)
                if failed_imports:
                    return False, "changed module failed to import: " + "; ".join(failed_imports)
                notes.append(f"imported {len(py)} changed module(s) "
                             "(no pytest available, so the suite was skipped)")
        else:
            proc = subprocess.run(
                ["bash", "-lc", "cd '%s' && ./scripts/run_tests.sh" % REPO_ROOT.as_posix()],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if proc.returncode != 0:
                tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-25:])
                return False, f"python tests failed:\n{tail}"
            notes.append("python tests passed")

    if ts:
        proc = subprocess.run(
            ["bash", "-lc", "cd '%s/apps/desktop' && npm run typecheck" % REPO_ROOT.as_posix()],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-25:])
            return False, f"typecheck failed:\n{tail}"
        notes.append("typecheck passed")

    return True, "; ".join(notes) or "no code files changed"


# ── the guard backstop ───────────────────────────────────────────────────────
def install_guard(git: Git) -> str:
    """(Re)install the reference-transaction hook.

    It lives in .git/hooks, which is NOT versioned — a fresh clone or a repaired
    install silently loses it. Reinstalling on every sync makes the protection
    self-healing. It covers the two installer scripts, which run entirely outside
    this module.
    """
    src = REPO_ROOT / "scripts" / "fork-guard.sh"
    if not src.exists():
        return "fork-guard.sh missing; backstop NOT installed"
    hooks = Path(git.out("rev-parse", "--absolute-git-dir") or str(REPO_ROOT / ".git")) / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    dst = hooks / "reference-transaction"
    dst.write_bytes(src.read_bytes())
    try:
        os.chmod(dst, 0o755)
    except OSError:
        pass
    return "guard installed"


# ── main flow ────────────────────────────────────────────────────────────────
def sync(branch: str = "main", *, dry_run: bool = False, no_ai: bool = False,
         quick_verify: bool = False) -> int:
    started = time.time()
    rec = SyncRecord(started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    git = Git(REPO_ROOT)

    def finish(status: str, msg: str, code: int) -> int:
        rec.status = status
        rec.message = msg
        rec.duration_s = round(time.time() - started, 1)
        rec.save()
        prefix = {"ok": "✓", "up-to-date": "✓", "deferred": "⚠"}.get(status, "✗")
        print(f"{prefix} {msg}")
        return code

    if not (REPO_ROOT / ".git").exists():
        return finish("failed", f"not a git repository: {REPO_ROOT}", 1)

    # rerere: a repeated conflict replays its previous resolution, so a recurring
    # conflict costs one model call ever rather than one per sync.
    git("config", "rerere.enabled", "true")
    git("config", "rerere.autoUpdate", "true")
    print(f"  {install_guard(git)}")

    if not git.ok("remote", "get-url", "upstream"):
        return finish("failed", "no 'upstream' remote configured", 1)

    # A shallow clone has no merge-base, so merge/rebase cannot work at all. The
    # installer clones with --depth 1, so this is a real state to handle.
    if git.out("rev-parse", "--is-shallow-repository") == "true":
        print("  → shallow clone; fetching full history")
        if not git.ok("fetch", "--unshallow", "upstream"):
            return finish("failed", "could not unshallow the repository", 1)

    # Only TRACKED modifications block a sync. Untracked files do not prevent a
    # merge, and refusing to run because of one is a self-inflicted deadlock: a
    # single stray file (e.g. a new test file left behind by a previous upstream
    # merge) would silently disable syncing forever. Verified: this exact case
    # blocked a real run.
    dirty = [l for l in git.out("status", "--porcelain").splitlines()
             if l and not l.startswith("??")]
    if dirty:
        return finish("deferred",
                      f"{len(dirty)} uncommitted change(s) in the repo; skipping sync "
                      "(nothing was changed)", 0)

    print(f"→ fetching upstream/{branch}")
    if not git.ok("fetch", "upstream", branch):
        return finish("failed", "git fetch upstream failed", 1)

    behind = git.out("rev-list", "--count", f"HEAD..upstream/{branch}")
    rec.upstream_commits = int(behind or 0)
    if rec.upstream_commits == 0:
        return finish("up-to-date", "already current with upstream", 0)

    rec.fork_patches = int(git.out("rev-list", "--count", f"upstream/{branch}..HEAD") or 0)
    rec.pre_sha = git.out("rev-parse", "HEAD")

    # Rollback point BEFORE any history operation. A tag is a permanent named
    # recovery point — recovery is "git checkout <tag>", not sha archaeology.
    rec.rollback_tag = "pre-sync-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    git("tag", rec.rollback_tag, rec.pre_sha)
    print(f"  rollback point: {rec.rollback_tag} -> {rec.pre_sha[:9]}")
    print(f"  {rec.upstream_commits} upstream commit(s); {rec.fork_patches} fork patch(es)")

    customizations = ""
    # Resolver context. Checked in order: the current location first, then the
    # historical root path, so moving the file cannot silently strip the model's
    # context (it would still "work" — just resolve conflicts blind).
    cust = next(
        (p for p in (REPO_ROOT / "fork" / "CUSTOMIZATIONS.md",
                     REPO_ROOT / "CUSTOMIZATIONS.md")
         if p.is_file()),
        REPO_ROOT / "fork" / "CUSTOMIZATIONS.md",
    )
    if cust.exists():
        customizations = cust.read_text(encoding="utf-8", errors="replace")

    def rollback() -> None:
        """Restore the pre-sync state without `reset --hard` (hard invariant).

        Order matters, and an earlier version got this wrong: moving the branch
        ref with `update-ref` leaves the index and working tree holding the
        MERGED content, so the tree ends up full of staged modifications against
        the restored commit. A real run left 100+ files staged. The index must be
        reset (mixed, never hard) and the tree checked out to match.
        """
        gitdir = Path(git.out("rev-parse", "--absolute-git-dir"))
        if (gitdir / "MERGE_HEAD").exists():
            git("merge", "--abort", allow_rewrite=True)

        if git.out("rev-parse", "HEAD") != rec.pre_sha:
            git("update-ref", f"refs/heads/{branch}", rec.pre_sha, allow_rewrite=True)

        # `reset` with no mode is --mixed: it rewrites the index but NEVER
        # discards working-tree content, so it does not violate the no-hard-reset
        # invariant. Then restore tracked files to the commit's state.
        git("reset", "-q", allow_rewrite=True)
        git("checkout", "--", ".")

        leftover = git.out("status", "--porcelain")
        if leftover:
            tracked = [l for l in leftover.splitlines() if not l.startswith("??")]
            if tracked:
                print(f"  ⚠ rollback left {len(tracked)} modified file(s); "
                      f"recover with: git checkout {rec.rollback_tag}")
        print(f"  restored to {rec.rollback_tag}")

    # MERGE, not rebase — see the module docstring. This is what keeps the app's
    # subsequent `pull --ff-only` clean and the destructive reset unreachable.
    print(f"→ merging upstream/{branch}")
    merged = git("merge", "--no-edit", f"upstream/{branch}", allow_rewrite=True)

    # Upstream sometimes ADDS a file that already exists locally as untracked
    # (e.g. a test file a previous merge left behind). Git refuses the whole merge
    # rather than conflicting on it. Untracked files are not our work and not in
    # any history, but deleting them silently would be data loss — so move them
    # aside into HERMES_HOME, report the location, and retry once.
    if merged.returncode != 0 and "untracked working tree files would be overwritten" in (
        merged.stdout + merged.stderr
    ):
        blocked = re.findall(r"^\t(.+)$", merged.stdout + merged.stderr, re.MULTILINE)
        parked = _state_dir() / "parked" / datetime.now().strftime("%Y%m%d-%H%M%S")
        moved: list[str] = []
        for rel in blocked:
            src = REPO_ROOT / rel.strip()
            if not src.is_file():
                continue
            dst = parked / rel.strip()
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.replace(dst)
            moved.append(rel.strip())
        if moved:
            print(f"  moved {len(moved)} untracked file(s) aside -> {parked}")
            merged = git("merge", "--no-edit", f"upstream/{branch}", allow_rewrite=True)

    if merged.returncode != 0:
        conflicted = [f for f in git.out("diff", "--name-only", "--diff-filter=U").splitlines() if f]
        rec.conflicts = conflicted
        if not conflicted:
            body = merged.stdout + merged.stderr
            rec.report_path = _write_report("MERGE-FAILED", body)
            rollback()
            return finish("failed", f"merge failed with no conflicts; report: {rec.report_path}", 1)

        print(f"  ⚠ conflict in {len(conflicted)} file(s): {', '.join(conflicted)}")

        protected = [f for f in conflicted if f in HAND_RESOLVE_ONLY]
        if protected:
            body = (
                "A conflict landed in the update mechanism itself. These files are\n"
                "never auto-resolved: a wrong resolution can destroy the ability to\n"
                "update at all.\n\n"
                f"Files: {', '.join(protected)}\n"
                f"Rollback point: {rec.rollback_tag} ({rec.pre_sha})\n\n"
                "Resolve by hand, then: git commit\n\n"
                "=== hunks ===\n" + git.out("diff")
            )
            rec.report_path = _write_report("HAND-RESOLVE-REQUIRED", body)
            rollback()
            return finish("deferred",
                          f"update-path conflict needs a human: {', '.join(protected)}; "
                          f"report: {rec.report_path}", 3)

        if no_ai:
            rec.report_path = _write_report("CONFLICT-NOAI", git.out("diff"))
            rollback()
            return finish("deferred", f"conflict and --no-ai set; report: {rec.report_path}", 4)

        for rel in conflicted:
            print(f"  → resolving {rel} with {RESOLVER_MODEL}")
            target = REPO_ROOT / rel
            body = target.read_text(encoding="utf-8", errors="replace")
            res = resolve_with_ai(rel, body, customizations)

            if not res.get("ok"):
                rec.resolutions.append(asdict(Resolution(
                    path=rel, confidence="none", reasoning=res.get("reason", ""),
                    accepted=False)))
                rec.report_path = _write_report(
                    "RESOLUTION-FAILED",
                    f"{res.get('reason')}\n\nRollback: {rec.rollback_tag}\n\n"
                    "=== hunks ===\n" + git.out("diff"))
                rollback()
                return finish("failed",
                              f"resolver failed on {rel}: {res.get('reason')}; "
                              f"report: {rec.report_path}", 5)

            accepted = res["confidence"] != "low"
            rec.resolutions.append(asdict(Resolution(
                path=rel,
                confidence=res["confidence"],
                reasoning=res["reasoning"],
                accepted=accepted,
                merged_preview=res["content"][:4000],
            )))

            if not accepted:
                rec.report_path = _write_report(
                    "LOW-CONFIDENCE",
                    f"{res['reasoning']}\n\nRollback: {rec.rollback_tag}\n\n"
                    "=== model reasoning ===\n" + (res.get("thinking") or "(none)") +
                    "\n\n=== hunks ===\n" + git.out("diff"))
                rollback()
                return finish("deferred",
                              f"low confidence on {rel}; not merged. "
                              f"report: {rec.report_path}", 6)

            target.write_text(res["content"], encoding="utf-8", newline="\n")
            git("add", "--", rel)
            print(f"    ✓ resolved (confidence: {res['confidence']})")

        committed = git("commit", "--no-edit", allow_rewrite=True)
        if committed.returncode != 0:
            rec.report_path = _write_report("COMMIT-FAILED",
                                            committed.stdout + committed.stderr)
            rollback()
            return finish("failed", f"could not commit the merge; report: {rec.report_path}", 7)

    rec.post_sha = git.out("rev-parse", "HEAD")
    print("  ✓ merge complete")

    changed = [f for f in git.out("diff", "--name-only", f"{rec.pre_sha}..HEAD").splitlines() if f]
    ok, notes = verify(changed, quick=quick_verify)
    rec.verification = notes
    if not ok:
        rec.report_path = _write_report(
            "VERIFICATION-FAILED",
            f"{notes}\n\nRestored to {rec.rollback_tag} ({rec.pre_sha}).\n"
            "The app still runs the previous, working build.")
        rollback()
        return finish("failed", f"verification failed: {notes.splitlines()[0]}; "
                                f"report: {rec.report_path}", 8)
    print(f"  ✓ verification: {notes}")

    if dry_run:
        return finish("ok", f"dry run: {rec.upstream_commits} commit(s) merged and verified, "
                            f"not pushed (rollback: {rec.rollback_tag})", 0)

    # DO NOT PUSH HERE. This is deliberate and load-bearing — an earlier version
    # pushed at this point and it broke the update flow.
    #
    # `hermes update` computes `rev-list HEAD..origin/<branch> --count` and, when
    # that is 0, RETURNS EARLY (main.py:11406 -> 11513) printing "Already up to
    # date!". Pushing here makes origin == local, so the count is 0 and the update
    # exits before the steps that follow the git pull:
    #   * "Updating Python dependencies..." (main.py:11648)
    #   * the desktop rebuild (electron/main.ts:3165)
    # Result: merged source code running against stale deps and a stale app
    # bundle, while the UI cheerfully reports everything is current. The native
    # popup also never fires, because it reads the same count.
    #
    # Leaving origin BEHIND is what keeps the stock update flow intact: the popup
    # fires, the user clicks Update, and `hermes update` runs its FULL body —
    # pull (a clean fast-forward, since we merged rather than rebased), syntax
    # validation, dependency install, and rebuild.
    #
    # The fork is pushed separately, after the app has caught up: see
    # `push_after_update()` below, which the post-update path calls.
    # The merge lands in the RUNNING install (this checkout IS the app), so the
    # post-pull steps `hermes update` would normally perform must happen here.
    # Only run what the diff actually requires — a rebuild costs minutes.
    followups: list[str] = []
    # `changed` is the merged diff computed above (pre_sha..HEAD). An earlier
    # revision read a `changed_files` name that was never assigned anywhere, so
    # this whole block raised NameError on the first sync that actually merged
    # something — after the merge, before the push, with no history entry and no
    # report. Never reproduced interactively because the runs that followed the
    # fix had nothing new to merge. Covered by
    # tests/fork_sync/test_fork_sync_contract.py::test_followups_use_the_merged_diff.
    dep_files = [f for f in changed
                 if Path(f).name in ("pyproject.toml", "requirements.txt",
                                     "package.json", "package-lock.json")]
    desktop_changed = [f for f in changed if f.startswith("apps/desktop/")]

    if dep_files:
        print("→ dependency manifests changed; installing")
        dep = subprocess.run(
            ["bash", "-lc", "cd '%s' && npm install --no-fund --no-audit "
                            "--progress=false --workspaces=false" % REPO_ROOT.as_posix()],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        followups.append("deps installed" if dep.returncode == 0 else "DEP INSTALL FAILED")

    if desktop_changed:
        print(f"→ {len(desktop_changed)} desktop file(s) changed; rebuilding")
        # Invoke the CLI as a MODULE, not via a `hermes` shim. There is no
        # venv/Scripts/hermes on this install (an earlier version hardcoded that
        # path and would have failed on the first sync that touched the desktop),
        # `hermes` is not on PATH under Task Scheduler, and `python cli.py
        # desktop` is a different program entirely — cli.py treats "desktop" as a
        # chat prompt and starts an agent. pyproject.toml maps the console script
        # to hermes_cli.main:main, so -m hermes_cli.main is the stable form.
        build = subprocess.run(
            [_repo_python(), "-m", "hermes_cli.main", "desktop",
             "--build-only", "--force-build"],
            cwd=str(REPO_ROOT),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=3600,
        )
        if build.returncode == 0:
            followups.append("desktop rebuilt")
        else:
            # The merge is verified and the source is good; only the bundle is
            # stale. Report loudly but do NOT roll back — reverting verified code
            # because a build step failed would be the worse outcome.
            tail = "\n".join((build.stdout + build.stderr).splitlines()[-15:])
            rec.report_path = _write_report("REBUILD-FAILED", tail)
            followups.append(f"DESKTOP REBUILD FAILED (see {rec.report_path})")

    # Push last: the local install is now fully caught up, so making origin equal
    # to HEAD no longer hides anything. Pushing BEFORE this point was defect #6 —
    # it drove `rev-list HEAD..origin/<branch>` to 0, which made `hermes update`
    # return early and skip exactly the two steps performed above.
    print("→ pushing fork")
    pushed = git("push", "origin", branch)
    rec.pushed = pushed.returncode == 0
    if not rec.pushed:
        followups.append("push failed (local tree is fine; retry with: fork_sync push)")

    suffix = ("; " + ", ".join(followups)) if followups else ""
    return finish("ok",
                  f"synced {rec.upstream_commits} upstream commit(s); "
                  f"{rec.fork_patches} customization(s) intact{suffix}", 0)


def push_after_update(branch: str = "main") -> int:
    """Push the fork once the running install has caught up to local HEAD.

    Split out from `sync()` on purpose: pushing during the sync makes
    `rev-list HEAD..origin/<branch>` zero, which makes `hermes update` return
    early and skip dependency install + the desktop rebuild. So the push has to
    happen AFTER the update has applied, not before it is offered.

    Safe to run any time — it only pushes when the local tree is clean and
    genuinely ahead of the fork.
    """
    git = Git(REPO_ROOT)
    if [l for l in git.out("status", "--porcelain").splitlines()
            if l and not l.startswith("??")]:
        print("⚠ uncommitted changes; not pushing")
        return 0
    ahead = git.out("rev-list", "--count", f"origin/{branch}..HEAD")
    if ahead in ("", "0"):
        print("✓ fork already up to date")
        return 0
    pushed = git("push", "origin", branch)
    if pushed.returncode != 0:
        tail = (pushed.stderr or pushed.stdout).strip().splitlines()[-1:] or ["unknown"]
        print(f"⚠ push failed: {tail[0]}")
        return 1
    print(f"✓ pushed {ahead} commit(s) to the fork")
    return 0


def status(branch: str = "main") -> int:
    """Print current fork state. Read-only; safe to run any time."""
    git = Git(REPO_ROOT)
    git("fetch", "--quiet", "upstream", branch)
    print(f"repo:      {REPO_ROOT}")
    print(f"HEAD:      {git.out('rev-parse', '--short', 'HEAD')} ({git.out('rev-parse', '--abbrev-ref', 'HEAD')})")
    print(f"origin:    {git.out('remote', 'get-url', 'origin')}")
    print(f"upstream:  {git.out('remote', 'get-url', 'upstream')}")
    print(f"behind upstream: {git.out('rev-list', '--count', f'HEAD..upstream/{branch}')}")
    print(f"fork patches:    {git.out('rev-list', '--count', f'upstream/{branch}..HEAD')}")
    print(f"unpushed:        {git.out('rev-list', '--count', f'origin/{branch}..HEAD')}")
    print(f"tree clean:      {'yes' if not git.out('status', '--porcelain') else 'NO'}")
    guard = Path(git.out("rev-parse", "--absolute-git-dir")) / "hooks" / "reference-transaction"
    print(f"guard installed: {'yes' if guard.exists() else 'NO'}")
    if HISTORY_PATH.exists():
        lines = [l for l in HISTORY_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
        print(f"sync history:    {len(lines)} entr(ies) at {HISTORY_PATH}")
        if lines:
            last = json.loads(lines[-1])
            print(f"  last: {last.get('started_at')} -> {last.get('status')}: {last.get('message','')}")
    else:
        print("sync history:    (none yet)")
    return 0


def record_crash(tb: str) -> int:
    """Turn an unhandled exception into a report + a history entry.

    Without this, a bug anywhere in `sync()` exits with a bare traceback: no
    history entry, no report, and — because sync alerts are not delivered
    anywhere yet — no signal at all. The scheduled job would keep failing every
    night while `status` still looked healthy. A crash must leave the same
    evidence trail as a handled failure.

    Deliberately does NOT attempt a rollback. At crash time the tree state is
    unknown, and `sync()` already restores its rollback point on every failure
    path it controls; blind recovery here could destroy a good merge. The report
    names the tag so recovery is one command.
    """
    rec = SyncRecord(started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    rec.status = "crashed"
    try:
        tags = Git(REPO_ROOT).out("tag", "--list", "pre-sync-*", "--sort=-creatordate")
        latest_tag = tags.splitlines()[0] if tags else ""
    except Exception:
        latest_tag = ""
    body = (
        "fork_sync crashed with an unhandled exception.\n\n"
        f"Most recent rollback point: {latest_tag or '(none found)'}\n"
        "The merge may or may not have completed. Check with:\n"
        "  python scripts/fork_sync.py status\n"
        f"Recover the previous state with:\n  git checkout {latest_tag or '<pre-sync tag>'}\n\n"
        "=== traceback ===\n" + tb
    )
    rec.report_path = _write_report("CRASHED", body)
    rec.rollback_tag = latest_tag
    rec.message = f"crashed: {tb.strip().splitlines()[-1] if tb.strip() else 'unknown'}"
    rec.save()
    print(f"✗ {rec.message}")
    print(f"  report: {rec.report_path}")
    return 9


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="fork_sync",
        description="Keep this fork current with upstream without losing customizations.",
    )
    ap.add_argument("command", nargs="?", default="sync",
                    choices=["sync", "status", "push"])
    ap.add_argument("--branch", default="main")
    ap.add_argument("--dry-run", action="store_true", help="merge and verify, but do not push")
    ap.add_argument("--no-ai", action="store_true", help="defer on conflict instead of calling the resolver")
    ap.add_argument("--quick-verify", action="store_true", help="parse check only; skip test suites")
    args = ap.parse_args(argv)

    if args.command == "status":
        return status(args.branch)
    if args.command == "push":
        return push_after_update(args.branch)
    try:
        return sync(args.branch, dry_run=args.dry_run, no_ai=args.no_ai,
                    quick_verify=args.quick_verify)
    except KeyboardInterrupt:
        raise
    except Exception:
        return record_crash(traceback.format_exc())


if __name__ == "__main__":
    sys.exit(main())
