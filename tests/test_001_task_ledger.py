"""Tests for rule 001 — task ledger enforcement."""

from __future__ import annotations


def test_passes_on_main_branch_without_ledger(
    tmp_repo, stage_file, run_rule
):
    """On main/master/develop without an agent/ prefix, rule is not enforced."""
    stage_file("foo.py", "x = 1\n")
    result = run_rule("001_task_ledger.py")
    assert result.returncode == 0, result.stderr


def test_passes_on_non_agent_feature_branch_without_ledger(
    tmp_repo, stage_file, run_rule, make_branch
):
    """Operator's own feature branches are informational, not blocking."""
    make_branch("feature/operator-branch")
    stage_file("foo.py", "x = 1\n")
    result = run_rule("001_task_ledger.py")
    assert result.returncode == 0, result.stderr


def test_blocks_agent_branch_without_ledger(
    tmp_repo, stage_file, run_rule, make_branch
):
    """An agent/* branch must have a ledger."""
    make_branch("agent/claude/20260512-foo")
    stage_file("foo.py", "x = 1\n")
    result = run_rule("001_task_ledger.py")
    assert result.returncode != 0
    assert "no .agent/tasks" in result.stderr.lower() or "no .agent/tasks" in result.stderr


def test_passes_when_staged_files_in_allowed_paths(
    tmp_repo, stage_file, run_rule, make_branch, ledger_factory
):
    branch = "agent/claude/20260512-foo"
    make_branch(branch)
    ledger_factory(branch=branch, allowed_paths=["src/foo.py"])
    stage_file("src/foo.py", "x = 1\n")
    result = run_rule("001_task_ledger.py")
    assert result.returncode == 0, result.stderr


def test_blocks_staged_file_outside_allowed_paths(
    tmp_repo, stage_file, run_rule, make_branch, ledger_factory
):
    branch = "agent/claude/20260512-foo"
    make_branch(branch)
    ledger_factory(branch=branch, allowed_paths=["src/foo.py"])
    stage_file("src/foo.py", "x = 1\n")
    stage_file("src/bar.py", "y = 2\n")  # NOT in allowed_paths
    result = run_rule("001_task_ledger.py")
    assert result.returncode != 0
    assert "src/bar.py" in result.stderr


def test_glob_in_allowed_paths(
    tmp_repo, stage_file, run_rule, make_branch, ledger_factory
):
    branch = "agent/claude/20260512-foo"
    make_branch(branch)
    ledger_factory(branch=branch, allowed_paths=["src/*.py"])
    stage_file("src/foo.py", "x = 1\n")
    stage_file("src/bar.py", "y = 2\n")
    result = run_rule("001_task_ledger.py")
    assert result.returncode == 0, result.stderr


def test_directory_entry_in_allowed_paths(
    tmp_repo, stage_file, run_rule, make_branch, ledger_factory
):
    """An allowed_paths entry ending in / matches everything under it."""
    branch = "agent/claude/20260512-foo"
    make_branch(branch)
    ledger_factory(branch=branch, allowed_paths=["src/"])
    stage_file("src/foo.py", "x = 1\n")
    stage_file("src/sub/bar.py", "y = 2\n")
    result = run_rule("001_task_ledger.py")
    assert result.returncode == 0, result.stderr


def test_ledger_is_bookkeeping_auto_allowed(
    tmp_repo, stage_file, run_rule, make_branch, ledger_factory
):
    """The task ledger itself must be commitable without explicit listing."""
    branch = "agent/claude/20260512-foo"
    make_branch(branch)
    ledger = ledger_factory(branch=branch, allowed_paths=["src/foo.py"])
    stage_file("src/foo.py", "x = 1\n")
    # Stage the ledger too — it's NOT in allowed_paths, but should be
    # auto-allowed as bookkeeping
    rel = str(ledger.relative_to(tmp_repo)).replace("\\", "/")
    import subprocess
    subprocess.run(["git", "add", rel], cwd=str(tmp_repo), check=True)

    result = run_rule("001_task_ledger.py")
    assert result.returncode == 0, result.stderr


def test_done_status_blocks_new_commits(
    tmp_repo, stage_file, run_rule, make_branch, ledger_factory
):
    branch = "agent/claude/20260512-foo"
    make_branch(branch)
    ledger_factory(
        branch=branch, allowed_paths=["src/foo.py"], status="done"
    )
    stage_file("src/foo.py", "x = 1\n")
    result = run_rule("001_task_ledger.py")
    assert result.returncode != 0
    assert "done" in result.stderr or "closed" in result.stderr


def test_superseded_status_blocks_new_commits(
    tmp_repo, stage_file, run_rule, make_branch, ledger_factory
):
    branch = "agent/claude/20260512-foo"
    make_branch(branch)
    ledger_factory(
        branch=branch, allowed_paths=["src/foo.py"], status="superseded"
    )
    stage_file("src/foo.py", "x = 1\n")
    result = run_rule("001_task_ledger.py")
    assert result.returncode != 0


def test_empty_allowed_paths_rejected(
    tmp_repo, stage_file, run_rule, make_branch, ledger_factory
):
    branch = "agent/claude/20260512-foo"
    make_branch(branch)
    ledger_factory(branch=branch, allowed_paths=[])
    stage_file("src/foo.py", "x = 1\n")
    result = run_rule("001_task_ledger.py")
    assert result.returncode != 0
    assert "allowed_paths" in result.stderr
