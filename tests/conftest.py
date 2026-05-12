"""Shared pytest fixtures for coding-rails tests.

Each fixture creates an isolated tmp git repo with the bundle's rule
check scripts copied into the canonical install path. Tests run the
scripts via subprocess to exercise them exactly as the real hooks do.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = PROJECT_ROOT / "bundle"
SCRIPTS = BUNDLE_ROOT / "scripts"
HOOKS = BUNDLE_ROOT / "hooks"


def _git(repo: Path, *args: str, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=check,
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """Create a fresh git repo with the bundle's scripts copied into place."""
    repo = tmp_path / "repo"
    repo.mkdir()

    # Initialize
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "commit.gpgsign", "false")

    # Copy bundle scripts/rules into the target's expected location
    target_scripts = repo / "scripts" / "coding-rails"
    target_rules = target_scripts / "rules"
    target_rules.mkdir(parents=True)
    for rule_script in (SCRIPTS / "rules").glob("*.py"):
        shutil.copy(rule_script, target_rules / rule_script.name)
    for top_script in SCRIPTS.glob("*.py"):
        shutil.copy(top_script, target_scripts / top_script.name)

    # Establish initial main commit so branches have a base
    (repo / "README.md").write_text("# test\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")

    return repo


@pytest.fixture
def ledger_factory(tmp_repo: Path) -> Callable[..., Path]:
    """Factory that writes a task ledger for the given branch/scope."""

    def make(
        *,
        branch: str,
        allowed_paths: list[str],
        task_id: str = "20260512-test-task",
        base_sha: str = "auto-resolved-at-start",
        status: str = "in_progress",
    ) -> Path:
        tasks_dir = tmp_repo / ".agent" / "tasks"
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
                    "status": status,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return ledger

    return make


@pytest.fixture
def stage_file(tmp_repo: Path) -> Callable[[str, str], Path]:
    """Write a file under the repo and `git add` it."""

    def make(rel: str, content: str = "x") -> Path:
        full = tmp_repo / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        _git(tmp_repo, "add", rel)
        return full

    return make


@pytest.fixture
def run_rule(tmp_repo: Path) -> Callable[[str], subprocess.CompletedProcess]:
    """Invoke a rule script in the tmp repo. Returns CompletedProcess."""

    def run(rule_filename: str, *extra_args: str) -> subprocess.CompletedProcess:
        rule = tmp_repo / "scripts" / "coding-rails" / "rules" / rule_filename
        return subprocess.run(
            [sys.executable, str(rule), *extra_args],
            cwd=str(tmp_repo),
            capture_output=True,
            text=True,
        )

    return run


@pytest.fixture
def run_script(tmp_repo: Path) -> Callable[..., subprocess.CompletedProcess]:
    """Invoke a top-level coding-rails script (agent_*.py / agent_git_guard.py)."""

    def run(script_filename: str, *extra_args: str) -> subprocess.CompletedProcess:
        script = tmp_repo / "scripts" / "coding-rails" / script_filename
        return subprocess.run(
            [sys.executable, str(script), *extra_args],
            cwd=str(tmp_repo),
            capture_output=True,
            text=True,
        )

    return run


@pytest.fixture
def make_branch(tmp_repo: Path) -> Callable[[str], None]:
    """Switch to a new branch off main."""

    def make(name: str) -> None:
        _git(tmp_repo, "checkout", "-b", name)

    return make


@pytest.fixture
def git_helper(tmp_repo: Path):
    """Direct git access for tests that need it."""

    def call(*args: str, check: bool = True) -> subprocess.CompletedProcess:
        return _git(tmp_repo, *args, check=check)

    return call
