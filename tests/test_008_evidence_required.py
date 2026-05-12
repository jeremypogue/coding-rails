"""Tests for rule 008 — evidence required for completion claims."""

from __future__ import annotations

from pathlib import Path


def _write_msg(tmp_repo: Path, content: str) -> Path:
    """Write a commit message to a temp file in the repo."""
    msg_path = tmp_repo / ".git" / "COMMIT_EDITMSG"
    msg_path.write_text(content, encoding="utf-8")
    return msg_path


def test_passes_no_completion_phrase(tmp_repo, run_rule):
    msg = _write_msg(tmp_repo, "Refactor the horse identity logic")
    result = run_rule("008_evidence_required.py", str(msg))
    assert result.returncode == 0, result.stderr


def test_blocks_verified_without_evidence(tmp_repo, run_rule):
    msg = _write_msg(tmp_repo, "Fixed pool pump bug\n\nverified locally")
    result = run_rule("008_evidence_required.py", str(msg))
    assert result.returncode != 0
    assert "evidence" in result.stderr.lower()


def test_blocks_shipped_without_evidence(tmp_repo, run_rule):
    msg = _write_msg(tmp_repo, "Add new feature\n\nshipped to staging")
    result = run_rule("008_evidence_required.py", str(msg))
    assert result.returncode != 0


def test_passes_verified_with_url(tmp_repo, run_rule):
    msg = _write_msg(tmp_repo, "Fixed pool pump bug\n\nverified at https://grafana/dash/12")
    result = run_rule("008_evidence_required.py", str(msg))
    assert result.returncode == 0, result.stderr


def test_passes_verified_with_evidence_keyword(tmp_repo, run_rule):
    msg = _write_msg(tmp_repo, "Fixed pool pump bug\n\nverified\n\nevidence: pytest tests/test_pool.py PASSED")
    result = run_rule("008_evidence_required.py", str(msg))
    assert result.returncode == 0, result.stderr


def test_passes_tested_with_pytest_output(tmp_repo, run_rule):
    msg = _write_msg(tmp_repo, "Add new feature\n\ntested: pytest tests/test_x.py :: 5 passed")
    result = run_rule("008_evidence_required.py", str(msg))
    assert result.returncode == 0, result.stderr


def test_passes_when_no_msg_arg_no_file(tmp_repo, run_rule):
    """If no commit-msg path is given and COMMIT_EDITMSG doesn't exist,
    the script exits 0 silently (pre-commit invocation pattern)."""
    # COMMIT_EDITMSG does not exist
    msg_path = tmp_repo / ".git" / "COMMIT_EDITMSG"
    if msg_path.exists():
        msg_path.unlink()
    result = run_rule("008_evidence_required.py")
    assert result.returncode == 0


def test_pre_commit_ignores_stale_editmsg(tmp_repo, run_rule):
    """REGRESSION: rule 008 invoked without a msg-path arg must NOT read
    .git/COMMIT_EDITMSG, even if EDITMSG holds stale completion-claim
    content from a previously-failed commit.

    This is the v0.3.0 fix for coding-rails issue #7. Earlier versions
    fell back to reading EDITMSG when no arg was passed, which caused
    false positives when a prior `git commit -m` had left a stale
    message there.
    """
    # Plant a stale message that WOULD trigger 008 if read
    msg_path = tmp_repo / ".git" / "COMMIT_EDITMSG"
    msg_path.write_text(
        "Fix pool pump bug\n\nverified locally with no evidence at all",
        encoding="utf-8",
    )
    # Call without an arg (pre-commit invocation pattern)
    result = run_rule("008_evidence_required.py")
    # Must exit 0; pre-commit invocation should not read EDITMSG
    assert result.returncode == 0, (
        f"008 read stale EDITMSG when invoked without arg: {result.stderr}"
    )


def test_strips_git_comment_lines(tmp_repo, run_rule):
    """Git comment lines (starting with #) don't count as message content."""
    msg = _write_msg(tmp_repo, "# verified — this is a comment, not the message\n\nReal subject line")
    result = run_rule("008_evidence_required.py", str(msg))
    assert result.returncode == 0, result.stderr
