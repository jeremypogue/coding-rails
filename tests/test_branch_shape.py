"""Tests for the agent-branch-shape regex (closes #4)."""

from __future__ import annotations

import re

import pytest


# Both the pre-push hook and agent_completion_gate enforce the same
# branch shape. Mirrored here so a drift between them would be caught
# by reading the source.
AGENT_BRANCH_RE = re.compile(r"^agent/[a-z0-9_-]+/[0-9]{8}-[a-z0-9._-]+$")


@pytest.mark.parametrize(
    "branch,expected",
    [
        # Happy path
        ("agent/claude/20260512-foo", True),
        ("agent/codex/20260101-test", True),
        ("agent/cline/20260601-bar-baz", True),
        # Dot in slug — version numbers / CVE identifiers (the bug from #4)
        ("agent/claude/20260512-upgrade-coding-rails-v0.2.0", True),
        ("agent/codex/20260601-cve-2024.123-patch", True),
        ("agent/claude/20260720-bump-deps-1.4.x", True),
        # Underscore in slug
        ("agent/claude/20260512-fix_bug_123", True),
        ("agent/claude/20260512-foo_bar.baz", True),
        # Hyphen in slug
        ("agent/claude/20260512-very-long-hyphenated-slug-name", True),
        # Bad shape — should reject
        ("agent/claude/20260512-foo bar", False),  # space
        ("agent/claude/20260512", False),  # no slug
        ("agent//20260512-foo", False),  # empty tool
        ("agent/claude/2026-05-12-foo", False),  # bad date format
        ("agent/Claude/20260512-foo", False),  # uppercase tool
        ("agent/claude/20260512-FOO", False),  # uppercase slug
        ("not-agent/claude/20260512-foo", False),  # wrong prefix
        ("agent/claude/20260512-", False),  # empty slug
        ("agent/claude/-20260512-foo", False),  # date missing
        ("agent/claude/20260532-foo", True),  # date format only checks YYYYMMDD shape; ranges are not validated
        # Refs/heads prefix is for pre-push only — the gate regex doesn't include it
        ("refs/heads/agent/claude/20260512-foo", False),
    ],
)
def test_agent_branch_regex(branch: str, expected: bool) -> None:
    assert bool(AGENT_BRANCH_RE.match(branch)) == expected, (
        f"branch={branch!r} expected match={expected}"
    )


def test_regex_matches_bundle_hook() -> None:
    """The pre-push hook embeds the regex with refs/heads/ prefix. Verify
    the bundle source matches what this test file claims."""
    from pathlib import Path
    pre_push = Path(__file__).resolve().parents[1] / "bundle" / "hooks" / "pre-push"
    src = pre_push.read_text(encoding="utf-8")
    # The embedded regex literal in the hook
    assert "[a-z0-9._-]+$" in src, (
        "pre-push hook should allow dots in slug; "
        "grep for '[a-z0-9._-]+$' in bundle/hooks/pre-push"
    )


def test_regex_matches_bundle_gate() -> None:
    """The completion gate has its own copy. Verify it also allows dots."""
    from pathlib import Path
    gate = Path(__file__).resolve().parents[1] / "bundle" / "scripts" / "agent_completion_gate.py"
    src = gate.read_text(encoding="utf-8")
    assert "[a-z0-9._-]+$" in src, (
        "agent_completion_gate should allow dots in slug; "
        "grep for '[a-z0-9._-]+$' in bundle/scripts/agent_completion_gate.py"
    )


def test_regex_matches_bundle_start_task() -> None:
    """agent_start_task validates task_id (which becomes the slug). Verify
    it also allows dots."""
    from pathlib import Path
    start = Path(__file__).resolve().parents[1] / "bundle" / "scripts" / "agent_start_task.sh"
    src = start.read_text(encoding="utf-8")
    assert "[a-z0-9._-]+" in src, (
        "agent_start_task.sh should allow dots in task_id; "
        "grep for '[a-z0-9._-]+' in bundle/scripts/agent_start_task.sh"
    )
