"""Integration tests for agent_start_task.sh."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
START_TASK = PROJECT_ROOT / "bundle" / "scripts" / "agent_start_task.sh"


def _start(repo: Path, bash: str, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [bash, str(START_TASK), *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        env=env,
    )


def test_refuses_missing_task_id(bash_repo_with_origin, bash_path):
    repo, _ = bash_repo_with_origin
    result = _start(repo, bash_path)
    assert result.returncode != 0
    assert "task_id is required" in (result.stdout + result.stderr)


def test_refuses_missing_paths(bash_repo_with_origin, bash_path):
    repo, _ = bash_repo_with_origin
    result = _start(repo, bash_path, "20260512-test-task")
    assert result.returncode != 0
    assert "--paths is required" in (result.stdout + result.stderr)


def test_refuses_invalid_task_id_shape(bash_repo_with_origin, bash_path):
    """task_id must match YYYYMMDD-slug."""
    repo, _ = bash_repo_with_origin
    result = _start(repo, bash_path, "bad-id", "--paths", "foo.py")
    assert result.returncode != 0
    assert "must match" in (result.stdout + result.stderr)


def test_refuses_dirty_tree(bash_repo_with_origin, bash_path):
    repo, _ = bash_repo_with_origin
    (repo / "scratch.txt").write_text("dirty")
    subprocess.run(["git", "add", "scratch.txt"], cwd=str(repo), check=True)
    result = _start(repo, bash_path, "20260512-test-task", "--paths", "foo.py")
    assert result.returncode != 0
    assert "dirty" in (result.stdout + result.stderr).lower()


def test_creates_branch_and_ledger(bash_repo_with_origin, bash_path):
    repo, _ = bash_repo_with_origin
    env = {**__import__("os").environ, "CODING_RAILS_AGENT": "claude"}
    result = _start(
        repo, bash_path,
        "20260512-test-task",
        "--paths", "src/foo.py,tests/test_foo.py",
        "--summary", "test task",
        env=env,
    )
    assert result.returncode == 0, f"start_task failed:\n{result.stdout}\n{result.stderr}"

    # Branch was created
    current = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    assert current.stdout.strip() == "agent/claude/20260512-test-task"

    # Ledger was written
    ledger = repo / ".agent" / "tasks" / "20260512-test-task.json"
    assert ledger.is_file()
    data = json.loads(ledger.read_text(encoding="utf-8"))
    assert data["task_id"] == "20260512-test-task"
    assert data["agent"] == "claude"
    assert data["branch"] == "agent/claude/20260512-test-task"
    assert data["base_ref"] == "origin/main"
    assert data["allowed_paths"] == ["src/foo.py", "tests/test_foo.py"]
    assert data["status"] == "in_progress"
    assert data["summary"] == "test task"
    # base_sha is resolved (not the placeholder)
    assert data["base_sha"] != "auto-resolved-at-start"
    assert len(data["base_sha"]) == 40  # full sha


def test_refuses_collision_with_existing_ledger(bash_repo_with_origin, bash_path):
    """Trying to start a task with an existing ledger should error."""
    repo, _ = bash_repo_with_origin
    env = {**__import__("os").environ, "CODING_RAILS_AGENT": "claude"}
    r1 = _start(repo, bash_path, "20260512-collision", "--paths", "foo.py", env=env)
    assert r1.returncode == 0

    # Switch back to main so the dirty-tree check passes
    subprocess.run(["git", "checkout", "main"], cwd=str(repo), check=True, capture_output=True)
    r2 = _start(repo, bash_path, "20260512-collision", "--paths", "foo.py", env=env)
    assert r2.returncode != 0
    assert "already exists" in (r2.stdout + r2.stderr).lower()


def test_summary_falls_back_when_omitted(bash_repo_with_origin, bash_path):
    """No --summary should not crash; a placeholder is written."""
    repo, _ = bash_repo_with_origin
    env = {**__import__("os").environ, "CODING_RAILS_AGENT": "claude"}
    result = _start(repo, bash_path, "20260512-no-summary", "--paths", "foo.py", env=env)
    assert result.returncode == 0, result.stderr

    ledger = repo / ".agent" / "tasks" / "20260512-no-summary.json"
    data = json.loads(ledger.read_text(encoding="utf-8"))
    assert "summary" in data
    # The fallback string mentions "none provided"
    assert "none provided" in data["summary"]
