"""Unit tests for agent_completion_gate.py helpers.

The completion gate's `main()` calls `gh pr view` (needs network + auth),
which is hard to mock cleanly. These tests focus on the pure helper
functions — `check_allowed_paths` (with bookkeeping), `check_pr_body`,
`check_branch_shape`, and `path_in_allowed` — by importing the script
as a module.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GATE_PATH = PROJECT_ROOT / "bundle" / "scripts" / "agent_completion_gate.py"


@pytest.fixture(scope="module")
def gate():
    """Import agent_completion_gate as a module."""
    spec = importlib.util.spec_from_file_location(
        "agent_completion_gate", GATE_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- check_allowed_paths bookkeeping (closes the review-point-1 gap) ----

def test_allowed_paths_pass_in_scope(gate, capsys):
    assert gate.check_allowed_paths(
        ["src/foo.py"],
        ["src/foo.py", "tests/test_foo.py"],
    ) is True


def test_allowed_paths_fail_out_of_scope(gate, capsys):
    assert gate.check_allowed_paths(
        ["src/foo.py", "evil.py"],
        ["src/foo.py"],
    ) is False
    err = capsys.readouterr().err
    assert "evil.py" in err


def test_bookkeeping_ledger_auto_allowed(gate):
    """REGRESSION (review point 1): the task ledger path must be
    auto-allowed even when not in allowed_paths. Without this, a PR
    that introduces its own ledger fails the completion gate."""
    changed = [
        "src/foo.py",
        ".agent/tasks/20260512-test-task.json",  # NOT in allowed_paths
    ]
    allowed = ["src/foo.py"]
    bookkeeping = {".agent/tasks/20260512-test-task.json"}
    assert gate.check_allowed_paths(changed, allowed, bookkeeping) is True


def test_bookkeeping_test_coverage_exceptions_auto_allowed(gate):
    """The exceptions file is bookkeeping; operator may add it without
    explicit allowed_paths entry."""
    changed = [
        "src/foo.py",
        ".agent/test-coverage-exceptions.md",
    ]
    allowed = ["src/foo.py"]
    bookkeeping = {".agent/test-coverage-exceptions.md"}
    assert gate.check_allowed_paths(changed, allowed, bookkeeping) is True


def test_bookkeeping_doesnt_pass_arbitrary_files(gate):
    """Bookkeeping is an explicit allowlist of specific paths, not a
    blanket exemption."""
    changed = ["src/foo.py", "random.py"]
    allowed = ["src/foo.py"]
    bookkeeping = {".agent/tasks/20260512-test-task.json"}
    # random.py is not in allowed_paths and not in bookkeeping
    assert gate.check_allowed_paths(changed, allowed, bookkeeping) is False


def test_bookkeeping_default_empty(gate):
    """bookkeeping defaults to empty set; behavior matches the legacy
    no-arg form."""
    changed = ["src/foo.py"]
    allowed = ["src/foo.py"]
    # No bookkeeping arg - should still pass since file is in allowed_paths
    assert gate.check_allowed_paths(changed, allowed) is True


# ---- branch shape ----

def test_branch_shape_pass(gate):
    assert gate.check_branch_shape("agent/claude/20260512-foo") is True


def test_branch_shape_pass_with_dot(gate):
    """Issue #4: dots in slug must be allowed."""
    assert gate.check_branch_shape("agent/claude/20260512-fix-v0.2.0") is True


def test_branch_shape_fail_no_date(gate):
    assert gate.check_branch_shape("agent/claude/foo-bar") is False


def test_branch_shape_fail_wrong_prefix(gate):
    assert gate.check_branch_shape("feature/foo-bar") is False


# ---- PR body sections ----

def test_pr_body_pass_all_sections_filled(gate):
    body = """
## Summary
Did the thing.

## Task metadata
- task_id: foo

## Tests
Ran them.

## Negative-smoke
n/a

## Changed files
- foo.py

## Known risks
None.

## Not done / follow-up
None.
"""
    assert gate.check_pr_body(body) is True


def test_pr_body_fail_missing_section(gate, capsys):
    body = """
## Summary
yes

## Task metadata
- yes

## Tests
yes

## Changed files
- foo

## Known risks
none

## Not done / follow-up
none
"""
    # Missing ## Negative-smoke
    assert gate.check_pr_body(body) is False
    err = capsys.readouterr().err
    assert "Negative-smoke" in err


def test_pr_body_fail_empty_section_with_only_comments(gate, capsys):
    body = """
## Summary
yes

## Task metadata
- yes

## Tests
<!-- TODO fill in -->

## Negative-smoke
n/a

## Changed files
- foo

## Known risks
none

## Not done / follow-up
none
"""
    # ## Tests has only an HTML comment; should be detected as empty
    assert gate.check_pr_body(body) is False


# ---- path_in_allowed semantics ----

def test_path_in_allowed_exact(gate):
    assert gate.path_in_allowed("src/foo.py", ["src/foo.py"]) is True


def test_path_in_allowed_glob(gate):
    assert gate.path_in_allowed("src/foo.py", ["src/*.py"]) is True
    assert gate.path_in_allowed("src/foo.py", ["src/**"]) is True


def test_path_in_allowed_directory_prefix(gate):
    assert gate.path_in_allowed("src/sub/foo.py", ["src/"]) is True


def test_path_in_allowed_no_match(gate):
    assert gate.path_in_allowed("evil.py", ["src/foo.py", "src/*.py"]) is False
