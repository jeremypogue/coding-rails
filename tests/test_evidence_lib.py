"""Tests for _evidence_lib (the shared rule 008 logic)."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LIB_PATH = PROJECT_ROOT / "bundle" / "scripts" / "_evidence_lib.py"


@pytest.fixture(scope="module")
def lib():
    """Import _evidence_lib as a module."""
    spec = importlib.util.spec_from_file_location("_evidence_lib", LIB_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- defaults ----

def test_defaults_exist(lib):
    assert lib.DEFAULT_COMPLETION_PATTERNS
    assert lib.DEFAULT_EVIDENCE_PATTERNS
    assert all(isinstance(p, str) for p in lib.DEFAULT_COMPLETION_PATTERNS)


def test_load_config_no_file_returns_defaults(lib, tmp_path):
    cfg = lib.load_config(tmp_path)
    assert cfg["completion_patterns"] == lib.DEFAULT_COMPLETION_PATTERNS
    assert cfg["evidence_patterns"] == lib.DEFAULT_EVIDENCE_PATTERNS


def test_load_config_with_yaml(lib, tmp_path):
    """A project's .agent/coding-rails.config.yml overrides the defaults."""
    pytest.importorskip("yaml")
    cfg_dir = tmp_path / ".agent"
    cfg_dir.mkdir()
    (cfg_dir / "coding-rails.config.yml").write_text(
        "completion_patterns:\n"
        '  - "(?i)\\\\bgolive\\\\b"\n'
        "evidence_patterns:\n"
        '  - "ticket:\\\\s*\\\\S+"\n',
        encoding="utf-8",
    )
    cfg = lib.load_config(tmp_path)
    assert cfg["completion_patterns"] == [r"(?i)\bgolive\b"]
    assert cfg["evidence_patterns"] == [r"ticket:\s*\S+"]


def test_load_config_with_invalid_yaml_falls_back(lib, tmp_path):
    pytest.importorskip("yaml")
    cfg_dir = tmp_path / ".agent"
    cfg_dir.mkdir()
    (cfg_dir / "coding-rails.config.yml").write_text(
        "this is not :: valid: yaml: at all\n  invalid\nlist:\n  - [\n",
        encoding="utf-8",
    )
    cfg = lib.load_config(tmp_path)
    # Bad YAML → fall back to defaults silently
    assert cfg["completion_patterns"] == lib.DEFAULT_COMPLETION_PATTERNS


# ---- strip_git_comments ----

def test_strip_git_comments(lib):
    text = "Real subject\n\n# a comment line\nReal body\n# another comment"
    out = lib.strip_git_comments(text)
    assert "comment" not in out
    assert "Real subject" in out
    assert "Real body" in out


def test_strip_git_comments_preserves_inline_hash(lib):
    """Only LINES starting with `#` are comments, not inline `#`."""
    text = "Fix issue #42 in module #core"
    out = lib.strip_git_comments(text)
    assert "Fix issue #42" in out


# ---- check_message (the main API) ----

def test_check_message_empty_passes(lib, tmp_path):
    passes, hits = lib.check_message(tmp_path, "")
    assert passes is True
    assert hits == []


def test_check_message_no_completion_phrase_passes(lib, tmp_path):
    passes, hits = lib.check_message(tmp_path, "Refactor the horse module")
    assert passes is True
    assert hits == []


def test_check_message_completion_without_evidence_fails(lib, tmp_path):
    passes, hits = lib.check_message(
        tmp_path, "Fix bug\n\nverified locally"
    )
    assert passes is False
    assert any("verified" in h for h in hits)


def test_check_message_completion_with_url_passes(lib, tmp_path):
    passes, _ = lib.check_message(
        tmp_path, "Fix bug\n\nverified at https://example.com/run/1"
    )
    assert passes is True


def test_check_message_completion_with_evidence_keyword_passes(lib, tmp_path):
    passes, _ = lib.check_message(
        tmp_path, "Fix bug\n\nshipped: pytest tests/foo.py PASSED"
    )
    # "pytest...passed" pattern matches (case-insensitive)
    assert passes is True


def test_check_message_completion_with_evidence_long_string_passes(lib, tmp_path):
    """The `evidence:` pattern requires `\\S{10,}` — a CONSECUTIVE 10+ char
    non-whitespace token, not a multi-word phrase."""
    passes, _ = lib.check_message(
        tmp_path, "Fix bug\n\ntested\n\nevidence: a-long-enough-evidence-token-here"
    )
    assert passes is True


def test_check_message_completion_short_evidence_fails(lib, tmp_path):
    """evidence: pattern requires >=10 chars after the colon."""
    passes, hits = lib.check_message(
        tmp_path, "Fix bug\n\ntested\n\nevidence: short"
    )
    # "short" is only 5 chars, fails the \S{10,} pattern
    assert passes is False


def test_check_message_strips_comments(lib, tmp_path):
    """# verified ... in a comment line should NOT count as a claim."""
    passes, _ = lib.check_message(
        tmp_path,
        "# verified by reviewer - this is a comment\n\nRefactor the module",
    )
    assert passes is True


# ---- per-project config affects behavior ----

def test_per_project_completion_pattern_applies(lib, tmp_path):
    """A project-defined completion pattern adds a new completion verb."""
    pytest.importorskip("yaml")
    (tmp_path / ".agent").mkdir()
    (tmp_path / ".agent" / "coding-rails.config.yml").write_text(
        "completion_patterns:\n"
        '  - "(?i)\\\\bgolive\\\\b"\n',
        encoding="utf-8",
    )
    # 'golive' is not in defaults; with this config, it triggers
    passes, hits = lib.check_message(tmp_path, "Deploy ready to golive now")
    assert passes is False
    assert any("golive" in h for h in hits)


def test_per_project_evidence_pattern_applies(lib, tmp_path):
    """A project-defined evidence pattern accepts new evidence forms."""
    pytest.importorskip("yaml")
    (tmp_path / ".agent").mkdir()
    (tmp_path / ".agent" / "coding-rails.config.yml").write_text(
        "evidence_patterns:\n"
        '  - "ticket:\\\\s*\\\\S+"\n',
        encoding="utf-8",
    )
    # Only "ticket:" counts now (default URL/evidence: patterns are gone)
    passes_with = lib.check_message(
        tmp_path, "Fix bug\n\nverified — ticket: ABC-123"
    )[0]
    passes_without = lib.check_message(
        tmp_path, "Fix bug\n\nverified at https://example.com/foo"
    )[0]
    assert passes_with is True
    # https:// no longer matches because patterns were replaced
    assert passes_without is False
