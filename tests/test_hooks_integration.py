"""Integration tests that exercise the git hooks end-to-end via real
`git commit` and `git push` in a tmp repo with the bundle installed."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _install_and_commit_seed(repo: Path, installer) -> str:
    """Install bundle, commit the install, return the seed SHA."""
    r = installer(repo)
    assert r.returncode == 0, f"install failed:\n{r.stdout}\n{r.stderr}"

    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
    # Use --no-verify here intentionally — we're seeding the test repo,
    # not exercising the hook chain. Subsequent tests use normal flow.
    seed = subprocess.run(
        ["git", "commit", "--no-verify", "-m", "install coding-rails"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        text=True,
    ).stdout.strip()
    return sha


def test_post_commit_writes_log_entry(bash_repo_with_origin, installer):
    """post-commit should record the commit in .agent/precommit.log."""
    repo, _ = bash_repo_with_origin
    _install_and_commit_seed(repo, installer)

    # Switch to an agent branch, write a ledger that allows our test file,
    # then commit a file (which should fire pre-commit + post-commit)
    subprocess.run(["git", "checkout", "-b", "agent/test/20260512-foo"], cwd=str(repo), check=True, capture_output=True)
    tasks = repo / ".agent" / "tasks"
    tasks.mkdir(exist_ok=True)
    (tasks / "20260512-foo.json").write_text(
        '{"task_id": "20260512-foo", "branch": "agent/test/20260512-foo", '
        '"allowed_paths": ["src/foo.py"], "status": "in_progress", "base_ref": "origin/main"}',
        encoding="utf-8",
    )
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "foo.py").write_text("x = 1\n", encoding="utf-8")

    subprocess.run(["git", "add", "src/foo.py", ".agent/tasks/20260512-foo.json"], cwd=str(repo), check=True)
    commit = subprocess.run(
        ["git", "commit", "-m", "add foo"],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    assert commit.returncode == 0, f"commit failed:\n{commit.stdout}\n{commit.stderr}"

    log = repo / ".agent" / "precommit.log"
    assert log.is_file()
    content = log.read_text(encoding="utf-8")
    assert "bypass=no" in content


def test_precommit_blocks_out_of_scope_file(bash_repo_with_origin, installer):
    """pre-commit should block when staged file is outside allowed_paths."""
    repo, _ = bash_repo_with_origin
    _install_and_commit_seed(repo, installer)

    subprocess.run(["git", "checkout", "-b", "agent/test/20260512-foo"], cwd=str(repo), check=True, capture_output=True)
    tasks = repo / ".agent" / "tasks"
    tasks.mkdir(exist_ok=True)
    (tasks / "20260512-foo.json").write_text(
        '{"task_id": "20260512-foo", "branch": "agent/test/20260512-foo", '
        '"allowed_paths": ["src/foo.py"], "status": "in_progress", "base_ref": "origin/main"}',
        encoding="utf-8",
    )
    # Stage a file NOT in allowed_paths
    (repo / "out_of_scope.txt").write_text("nope", encoding="utf-8")
    subprocess.run(
        ["git", "add", "out_of_scope.txt", ".agent/tasks/20260512-foo.json"],
        cwd=str(repo),
        check=True,
    )

    commit = subprocess.run(
        ["git", "commit", "-m", "out of scope"],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    assert commit.returncode != 0
    assert "out_of_scope.txt" in (commit.stdout + commit.stderr)


def test_commit_msg_blocks_completion_without_evidence(bash_repo_with_origin, installer):
    """commit-msg should reject 'verified' without an evidence reference."""
    repo, _ = bash_repo_with_origin
    _install_and_commit_seed(repo, installer)

    subprocess.run(["git", "checkout", "-b", "agent/test/20260512-foo"], cwd=str(repo), check=True, capture_output=True)
    tasks = repo / ".agent" / "tasks"
    tasks.mkdir(exist_ok=True)
    (tasks / "20260512-foo.json").write_text(
        '{"task_id": "20260512-foo", "branch": "agent/test/20260512-foo", '
        '"allowed_paths": ["src/foo.py"], "status": "in_progress", "base_ref": "origin/main"}',
        encoding="utf-8",
    )
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "foo.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/foo.py", ".agent/tasks/20260512-foo.json"], cwd=str(repo), check=True)

    commit = subprocess.run(
        ["git", "commit", "-m", "Fix pool pump bug\n\nverified locally"],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    assert commit.returncode != 0
    assert "evidence" in (commit.stdout + commit.stderr).lower()


def test_commit_msg_passes_with_evidence(bash_repo_with_origin, installer):
    repo, _ = bash_repo_with_origin
    _install_and_commit_seed(repo, installer)

    subprocess.run(["git", "checkout", "-b", "agent/test/20260512-foo"], cwd=str(repo), check=True, capture_output=True)
    tasks = repo / ".agent" / "tasks"
    tasks.mkdir(exist_ok=True)
    (tasks / "20260512-foo.json").write_text(
        '{"task_id": "20260512-foo", "branch": "agent/test/20260512-foo", '
        '"allowed_paths": ["src/foo.py"], "status": "in_progress", "base_ref": "origin/main"}',
        encoding="utf-8",
    )
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "foo.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/foo.py", ".agent/tasks/20260512-foo.json"], cwd=str(repo), check=True)

    commit = subprocess.run(
        ["git", "commit", "-m", "Fix pool pump bug\n\nverified: pytest tests/test_pool.py PASSED"],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    assert commit.returncode == 0, f"commit unexpectedly rejected:\n{commit.stdout}\n{commit.stderr}"


def test_pre_push_blocks_main(bash_repo_with_origin, installer):
    """pre-push should refuse pushes to main even with valid commits."""
    repo, _ = bash_repo_with_origin
    _install_and_commit_seed(repo, installer)

    # Try to push main back to origin/main; pre-push should block.
    push = subprocess.run(
        ["git", "push", "origin", "main"],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    assert push.returncode != 0
    assert "shared branch" in (push.stdout + push.stderr).lower() or "refusing" in (push.stdout + push.stderr).lower()
