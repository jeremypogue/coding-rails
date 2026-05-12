"""Tests for rule 004 — test coverage (paired test file)."""

from __future__ import annotations


def test_passes_when_no_agent_surface_files_staged(tmp_repo, stage_file, run_rule):
    stage_file("docs/readme.md", "# docs\n")
    result = run_rule("004_test_coverage.py")
    assert result.returncode == 0, result.stderr


def test_blocks_agent_file_without_test(tmp_repo, stage_file, run_rule):
    stage_file("agents/horsemaster.py", "x = 1\n")
    result = run_rule("004_test_coverage.py")
    assert result.returncode != 0
    assert "horsemaster" in result.stderr or "test_horsemaster" in result.stderr


def test_passes_when_paired_test_staged(tmp_repo, stage_file, run_rule):
    stage_file("agents/horsemaster.py", "x = 1\n")
    stage_file("tests/test_horsemaster.py", "def test_x(): pass\n")
    result = run_rule("004_test_coverage.py")
    assert result.returncode == 0, result.stderr


def test_passes_when_prefix_test_staged(tmp_repo, stage_file, run_rule):
    """A test file matching the prefix counts (e.g. test_horsemaster_log.py)."""
    stage_file("agents/horsemaster.py", "x = 1\n")
    stage_file("tests/test_horsemaster_extras.py", "def test_x(): pass\n")
    result = run_rule("004_test_coverage.py")
    assert result.returncode == 0, result.stderr


def test_excluded_init_passes(tmp_repo, stage_file, run_rule):
    """agents/__init__.py is in the default exclude list."""
    stage_file("agents/__init__.py", "")
    result = run_rule("004_test_coverage.py")
    assert result.returncode == 0, result.stderr


def test_operator_exception_passes(tmp_repo, stage_file, run_rule):
    """A path matching .agent/test-coverage-exceptions.md is exempt."""
    exceptions = tmp_repo / ".agent" / "test-coverage-exceptions.md"
    exceptions.parent.mkdir(parents=True, exist_ok=True)
    exceptions.write_text(
        "# Comment-only refactor approved in PR #42\n"
        "agents/horsemaster.py\n",
        encoding="utf-8",
    )
    stage_file("agents/horsemaster.py", "x = 1\n")  # no paired test
    result = run_rule("004_test_coverage.py")
    assert result.returncode == 0, result.stderr


def test_operator_exception_glob_passes(tmp_repo, stage_file, run_rule):
    exceptions = tmp_repo / ".agent" / "test-coverage-exceptions.md"
    exceptions.parent.mkdir(parents=True, exist_ok=True)
    exceptions.write_text(
        "# Generated files\nagents/_generated_*.py\n",
        encoding="utf-8",
    )
    stage_file("agents/_generated_foo.py", "x = 1\n")
    result = run_rule("004_test_coverage.py")
    assert result.returncode == 0, result.stderr


def test_operator_exception_comments_ignored(tmp_repo, stage_file, run_rule):
    """Comment lines in the exceptions file should not be parsed as paths."""
    exceptions = tmp_repo / ".agent" / "test-coverage-exceptions.md"
    exceptions.parent.mkdir(parents=True, exist_ok=True)
    exceptions.write_text(
        "# This is a comment, not a path\n\n# Another\n",
        encoding="utf-8",
    )
    stage_file("agents/horsemaster.py", "x = 1\n")  # no paired test, no exception
    result = run_rule("004_test_coverage.py")
    assert result.returncode != 0  # still blocked
