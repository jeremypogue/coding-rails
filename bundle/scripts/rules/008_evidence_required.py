#!/usr/bin/env python3
"""coding-rails rule 008 — evidence required for completion claims.

Invoked by .githooks/commit-msg. The commit-msg hook receives the path
to COMMIT_EDITMSG as its first argument and re-runs this script to
validate the prepared commit message. Pre-commit fires too early —
COMMIT_EDITMSG does not exist yet when pre-commit runs.

The pre-commit hook may still invoke this script (the aggregator runs
all *.py under scripts/coding-rails/rules/) but in that case there is
no commit message to read and we exit 0 silently. The real enforcement
happens in the commit-msg hook.

Scans the commit message for completion phrases ('verified', 'shipped',
'confirmed', 'tested', 'smoked'). If any are present, requires at least
one matching evidence pattern.

Exits 0 on pass, non-zero on fail.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Stage hint: this rule depends on the commit-msg hook stage, not
# pre-commit. The aggregator may use this hint in future versions to
# skip pre-commit invocation entirely.
_STAGE = "commit-msg"


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


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def find_repo_root() -> Path:
    return Path(run("git", "rev-parse", "--show-toplevel"))


def load_config(repo_root: Path) -> dict:
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
    if "completion_patterns" in loaded:
        out["completion_patterns"] = loaded["completion_patterns"]
    if "evidence_patterns" in loaded:
        out["evidence_patterns"] = loaded["evidence_patterns"]
    return out


def read_commit_msg(repo_root: Path, msg_path_arg: str | None) -> str:
    """Read the commit message. Only the commit-msg hook invocation
    passes the path as $1. Pre-commit invocation has no arg, in which
    case this returns empty string — main() will exit 0 quietly.

    Earlier versions of this script fell back to reading
    .git/COMMIT_EDITMSG when no arg was passed. That was unsafe because
    COMMIT_EDITMSG can hold stale content from a previously-failed
    commit (git does not write a fresh `-m` message there until later
    in the lifecycle). The strict no-arg → return "" behavior closes
    that hole: only the commit-msg invocation, which receives the
    actual message path, can validate. See coding-rails issue #7.
    """
    if not msg_path_arg:
        return ""
    return Path(msg_path_arg).read_text(encoding="utf-8")


def strip_comments(text: str) -> str:
    """Git comment lines (starting with #) don't count toward the
    message — they're stripped before the commit object is created."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def fail(reason: str) -> None:
    sys.stderr.write(f"rule 008 (evidence required): {reason}\n")


def main() -> int:
    repo_root = find_repo_root()
    msg_arg = sys.argv[1] if len(sys.argv) > 1 else None
    raw = read_commit_msg(repo_root, msg_arg)
    msg = strip_comments(raw).strip()

    if not msg:
        # No commit message yet — pre-commit fires before the editor
        # opens. Skip; the commit-msg hook (if wired) will re-check.
        return 0

    cfg = load_config(repo_root)
    completion = [re.compile(p) for p in cfg["completion_patterns"]]
    evidence = [re.compile(p) for p in cfg["evidence_patterns"]]

    completion_hits = [pat.pattern for pat in completion if pat.search(msg)]
    if not completion_hits:
        return 0

    evidence_hits = [pat.pattern for pat in evidence if pat.search(msg)]
    if evidence_hits:
        return 0

    fail(
        "commit message contains a completion claim "
        f"(matched: {', '.join(completion_hits)}) but no evidence reference."
    )
    sys.stderr.write(
        "  Add one of:\n"
        "    - a URL the change was verified at (https://...)\n"
        "    - a test command + result ('pytest tests/x.py :: passed')\n"
        "    - a screenshot/logbook/telegram/sms/event_log reference\n"
        "    - a task ledger reference (.agent/tasks/<id>.json)\n"
        "    - the literal string 'evidence: <≥10 char description>'\n"
        "  Or rephrase the message to not claim completion.\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
