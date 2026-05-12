"""End-to-end tests for agent_completion_gate.py main().

Uses the new `--pr-json` mode to bypass the gh CLI round-trip. Tests
construct a synthetic git history + a JSON file describing the PR,
then invoke main() and assert on outcomes.

These complement the helper-level tests in test_agent_completion_gate.py.
The helpers were unit-tested in v0.3.0; this file covers main() flow.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GATE = PROJECT_ROOT / "bundle" / "scripts" / "agent_completion_gate.py"


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), check=check, capture_output=True, text=True
    )


def _init_test_repo(tmp_path: Path) -> Path:
    """Make a tmp git repo with one initial commit on main."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("# test\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    return repo


def _write_ledger(
    repo: Path, task_id: str, branch: str, allowed_paths: list[str], base_sha: str
) -> Path:
    tasks_dir = repo / ".agent" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    ledger = tasks_dir / f"{task_id}.json"
    ledger.write_text(
        json.dumps(
            {
                "task_id": task_id,
                "branch": branch,
                "base_ref": "origin/main",
                "base_sha": base_sha,
                "allowed_paths": allowed_paths,
                "status": "in_progress",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return ledger


VALID_PR_BODY = """## Summary
Did the thing.

## Task metadata
- task_id: foo

## Tests
Ran them: pytest tests/foo.py PASSED

## Negative-smoke
n/a

## Changed files
foo.py

## Known risks
None.

## Not done / follow-up
None.
"""


def _write_pr_json(
    tmp_path: Path,
    *,
    branch: str,
    base_sha: str,
    head_sha: str,
    files: list[str],
    body: str = VALID_PR_BODY,
    base_ref: str = "main",
) -> Path:
    path = tmp_path / "pr.json"
    path.write_text(
        json.dumps(
            {
                "headRefName": branch,
                "baseRefName": base_ref,
                "baseRefOid": base_sha,
                "body": body,
                "number": 42,
                "state": "open",
                "files": [{"path": p} for p in files],
            }
        )
    )
    return path


def _run_gate(repo: Path, pr_json: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), "--pr-json", str(pr_json)],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )


# ---- happy path ----

def test_gate_passes_normal_pr(tmp_path):
    """Valid agent branch + valid ledger + in-scope file changes +
    well-formed PR body + no merge commits = PASS."""
    repo = _init_test_repo(tmp_path)
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    branch = "agent/claude/20260512-test-task"
    _git(repo, "checkout", "-b", branch)

    ledger = _write_ledger(
        repo,
        "20260512-test-task",
        branch,
        allowed_paths=["src/foo.py"],
        base_sha=base_sha,
    )
    (repo / "src").mkdir()
    (repo / "src" / "foo.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "src/foo.py", str(ledger.relative_to(repo)))
    _git(repo, "commit", "-m", "Add foo module")

    head_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    pr = _write_pr_json(
        tmp_path,
        branch=branch,
        base_sha=base_sha,
        head_sha=head_sha,
        files=["src/foo.py", ".agent/tasks/20260512-test-task.json"],
    )

    result = _run_gate(repo, pr)
    assert result.returncode == 0, f"gate failed:\n{result.stdout}\n{result.stderr}"
    assert "PASS" in result.stdout


def test_gate_passes_ledger_only_via_bookkeeping(tmp_path):
    """The task ledger itself is NOT in allowed_paths but should be
    auto-allowed as bookkeeping. (REGRESSION for the v0.3.0 fix.)"""
    repo = _init_test_repo(tmp_path)
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    branch = "agent/claude/20260512-test"
    _git(repo, "checkout", "-b", branch)
    ledger = _write_ledger(
        repo,
        "20260512-test",
        branch,
        # Note: ledger path NOT in allowed_paths
        allowed_paths=["src/foo.py"],
        base_sha=base_sha,
    )
    (repo / "src").mkdir()
    (repo / "src" / "foo.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "src/foo.py", str(ledger.relative_to(repo)))
    _git(repo, "commit", "-m", "First commit")

    head_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    pr = _write_pr_json(
        tmp_path,
        branch=branch,
        base_sha=base_sha,
        head_sha=head_sha,
        files=["src/foo.py", ".agent/tasks/20260512-test.json"],
    )

    result = _run_gate(repo, pr)
    assert result.returncode == 0, (
        f"gate should auto-allow the ledger; got:\n{result.stdout}\n{result.stderr}"
    )


# ---- failure paths ----

def test_gate_fails_bad_branch_shape(tmp_path):
    repo = _init_test_repo(tmp_path)
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    branch = "feature/not-an-agent-branch"
    _git(repo, "checkout", "-b", branch)
    ledger = _write_ledger(repo, "20260512-x", branch, ["foo.py"], base_sha)
    (repo / "foo.py").write_text("x", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "x")
    head_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    pr = _write_pr_json(
        tmp_path, branch=branch, base_sha=base_sha, head_sha=head_sha, files=["foo.py"]
    )
    result = _run_gate(repo, pr)
    assert result.returncode != 0
    assert "branch" in (result.stdout + result.stderr).lower()


def test_gate_fails_files_outside_allowed_paths(tmp_path):
    repo = _init_test_repo(tmp_path)
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    branch = "agent/claude/20260512-x"
    _git(repo, "checkout", "-b", branch)
    ledger = _write_ledger(repo, "20260512-x", branch, ["src/foo.py"], base_sha)
    (repo / "src").mkdir()
    (repo / "src" / "foo.py").write_text("x", encoding="utf-8")
    (repo / "evil.py").write_text("y", encoding="utf-8")  # out of scope
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "x")
    head_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    pr = _write_pr_json(
        tmp_path,
        branch=branch,
        base_sha=base_sha,
        head_sha=head_sha,
        files=["src/foo.py", "evil.py", ".agent/tasks/20260512-x.json"],
    )
    result = _run_gate(repo, pr)
    assert result.returncode != 0
    assert "evil.py" in (result.stdout + result.stderr)


def test_gate_fails_pr_body_missing_section(tmp_path):
    repo = _init_test_repo(tmp_path)
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    branch = "agent/claude/20260512-x"
    _git(repo, "checkout", "-b", branch)
    ledger = _write_ledger(repo, "20260512-x", branch, ["foo.py"], base_sha)
    (repo / "foo.py").write_text("x", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "x")
    head_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    pr = _write_pr_json(
        tmp_path,
        branch=branch,
        base_sha=base_sha,
        head_sha=head_sha,
        files=["foo.py"],
        body="## Summary\nyes\n",  # missing everything else
    )
    result = _run_gate(repo, pr)
    assert result.returncode != 0


def test_gate_fails_commit_msg_completion_without_evidence(tmp_path):
    """REGRESSION: completion-claim commit in PR range fails the gate's
    rule 008 scan, using the shared evidence-lib."""
    repo = _init_test_repo(tmp_path)
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    branch = "agent/claude/20260512-x"
    _git(repo, "checkout", "-b", branch)
    ledger = _write_ledger(repo, "20260512-x", branch, ["foo.py"], base_sha)
    (repo / "foo.py").write_text("x", encoding="utf-8")
    _git(repo, "add", "-A")
    # Commit message contains "verified" without evidence
    _git(repo, "commit", "-m", "Fix bug\n\nverified locally")
    head_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    pr = _write_pr_json(
        tmp_path,
        branch=branch,
        base_sha=base_sha,
        head_sha=head_sha,
        files=["foo.py", ".agent/tasks/20260512-x.json"],
    )
    result = _run_gate(repo, pr)
    assert result.returncode != 0
    assert "evidence" in (result.stdout + result.stderr).lower()


def test_gate_passes_commit_msg_completion_with_evidence(tmp_path):
    repo = _init_test_repo(tmp_path)
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    branch = "agent/claude/20260512-x"
    _git(repo, "checkout", "-b", branch)
    ledger = _write_ledger(repo, "20260512-x", branch, ["foo.py"], base_sha)
    (repo / "foo.py").write_text("x", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(
        repo,
        "commit",
        "-m",
        "Fix bug\n\nverified at https://example.com/run/42",
    )
    head_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    pr = _write_pr_json(
        tmp_path,
        branch=branch,
        base_sha=base_sha,
        head_sha=head_sha,
        files=["foo.py", ".agent/tasks/20260512-x.json"],
    )
    result = _run_gate(repo, pr)
    assert result.returncode == 0, f"unexpected fail:\n{result.stdout}\n{result.stderr}"


# ---- scope-growth-in-PR (closes review-point-2) ----

def test_gate_fails_when_pr_creates_then_expands_ledger(tmp_path):
    """REGRESSION: in v0.3.0, if a PR CREATED the ledger (no base
    version existed), scope-growth check skipped silently. Now it
    compares to the first-in-PR version and catches expansion."""
    repo = _init_test_repo(tmp_path)
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    branch = "agent/claude/20260512-test"
    _git(repo, "checkout", "-b", branch)

    # Commit 1: create ledger with NARROW scope
    ledger = _write_ledger(
        repo,
        "20260512-test",
        branch,
        allowed_paths=["src/foo.py"],
        base_sha=base_sha,
    )
    (repo / "src").mkdir()
    (repo / "src" / "foo.py").write_text("x", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "Commit 1: narrow scope")

    # Commit 2: EXPAND ledger's allowed_paths AND add files that fit
    # the expanded scope. Without the fix, gate would pass; with the
    # fix, gate compares to first-in-PR ledger and catches the growth.
    ledger.write_text(
        json.dumps(
            {
                "task_id": "20260512-test",
                "branch": branch,
                "base_ref": "origin/main",
                "base_sha": base_sha,
                "allowed_paths": ["src/foo.py", "src/bar.py"],  # NEW: bar.py
                "status": "in_progress",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (repo / "src" / "bar.py").write_text("y", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "Commit 2: expand scope, add bar")

    head_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    pr = _write_pr_json(
        tmp_path,
        branch=branch,
        base_sha=base_sha,
        head_sha=head_sha,
        files=[
            "src/foo.py",
            "src/bar.py",
            ".agent/tasks/20260512-test.json",
        ],
    )
    result = _run_gate(repo, pr)
    # Gate should reject because scope grew within the PR
    assert result.returncode != 0, (
        f"gate should catch mid-PR scope growth; got:\n{result.stdout}\n{result.stderr}"
    )
    assert "expand" in (result.stdout + result.stderr).lower() or "scope" in (result.stdout + result.stderr).lower()


def test_gate_passes_when_pr_creates_ledger_and_no_growth(tmp_path):
    """Baseline for the test above: creating a ledger without expanding
    it should pass."""
    repo = _init_test_repo(tmp_path)
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    branch = "agent/claude/20260512-test"
    _git(repo, "checkout", "-b", branch)
    ledger = _write_ledger(
        repo, "20260512-test", branch, ["src/foo.py"], base_sha
    )
    (repo / "src").mkdir()
    (repo / "src" / "foo.py").write_text("x", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "Commit 1")
    # Commit 2: only modify foo.py, no scope change
    (repo / "src" / "foo.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "Commit 2")

    head_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    pr = _write_pr_json(
        tmp_path,
        branch=branch,
        base_sha=base_sha,
        head_sha=head_sha,
        files=["src/foo.py", ".agent/tasks/20260512-test.json"],
    )
    result = _run_gate(repo, pr)
    assert result.returncode == 0, f"unexpected fail:\n{result.stdout}\n{result.stderr}"
