"""coding-rails — shared evidence/completion regex + config loader.

Used by:
  - bundle/scripts/rules/008_evidence_required.py (commit-msg hook stage)
  - bundle/scripts/agent_completion_gate.py (CI completion-gate PR-range scan)

Keeping the patterns and config-load logic in one place prevents the local
rule (per-commit) and the CI gate (across PR range) from drifting apart.
Both sites must honor the same `.agent/coding-rails.config.yml` overrides
the project ships.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Pattern


DEFAULT_COMPLETION_PATTERNS = [
    r"(?i)\bverified\b",
    r"(?i)\bshipped\b",
    r"(?i)\bconfirmed\b",
    r"(?i)\btested\b",
    r"(?i)\bsmoked?\b",
]

DEFAULT_EVIDENCE_PATTERNS = [
    r"(?i)https?://",
    r"(?i)evidence:\s*\S{10,}",
    r"(?i)pytest\b.*\bpassed\b",
    r"(?i)screenshot:\s*\S+",
    r"(?i)logbook:\s*\S+",
    r"(?i)telegram:\s*\S+",
    r"(?i)sms:\s*\S+",
    r"(?i)physical-check:\s*\S+",
    r"(?i)event_log:\s*\S+",
    r"(?i)\.agent/tasks/\S+\.json",
]


def load_config(repo_root: Path) -> dict:
    """Load completion/evidence patterns from `.agent/coding-rails.config.yml`.

    Returns a dict with `completion_patterns` and `evidence_patterns`. If
    the config file is missing or PyYAML isn't installed, returns defaults
    silently (the rule still fires with sensible defaults; we don't want
    a missing dev dependency to disable enforcement).
    """
    defaults = {
        "completion_patterns": DEFAULT_COMPLETION_PATTERNS,
        "evidence_patterns": DEFAULT_EVIDENCE_PATTERNS,
    }
    cfg_path = repo_root / ".agent" / "coding-rails.config.yml"
    if not cfg_path.is_file():
        return defaults

    try:
        import yaml  # type: ignore
    except ImportError:
        return defaults

    try:
        loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return defaults

    out = dict(defaults)
    if "completion_patterns" in loaded and loaded["completion_patterns"]:
        out["completion_patterns"] = loaded["completion_patterns"]
    if "evidence_patterns" in loaded and loaded["evidence_patterns"]:
        out["evidence_patterns"] = loaded["evidence_patterns"]
    return out


def compile_patterns(patterns: list[str]) -> list[Pattern]:
    return [re.compile(p) for p in patterns]


def strip_git_comments(text: str) -> str:
    """Drop git-comment lines (those starting with `#`) from a commit
    message. Git itself strips them before the commit object is created."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def find_completion_phrases(msg: str, patterns: list[Pattern]) -> list[str]:
    """Return list of pattern strings that matched the message."""
    return [pat.pattern for pat in patterns if pat.search(msg)]


def has_evidence(msg: str, patterns: list[Pattern]) -> bool:
    return any(pat.search(msg) for pat in patterns)


def check_message(repo_root: Path, raw_msg: str) -> tuple[bool, list[str]]:
    """Validate a commit message against the configured rule 008 patterns.

    Returns `(passes, completion_hits)`:
      - `passes=True, completion_hits=[]` — no completion claim; no check needed
      - `passes=True, completion_hits=[...]` — claim present, evidence found
      - `passes=False, completion_hits=[...]` — claim present, no evidence

    Empty or whitespace-only messages pass (caller decides whether to block
    on empty messages separately — that's not rule 008's job).
    """
    clean = strip_git_comments(raw_msg).strip()
    if not clean:
        return True, []

    cfg = load_config(repo_root)
    completion_compiled = compile_patterns(cfg["completion_patterns"])
    evidence_compiled = compile_patterns(cfg["evidence_patterns"])

    hits = find_completion_phrases(clean, completion_compiled)
    if not hits:
        return True, []

    if has_evidence(clean, evidence_compiled):
        return True, hits

    return False, hits
