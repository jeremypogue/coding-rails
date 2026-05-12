#!/usr/bin/env python3
"""coding-rails rule 008 — evidence required for completion claims.

Invoked by .githooks/commit-msg. The commit-msg hook receives the path
to COMMIT_EDITMSG as its first argument and re-runs this script to
validate the prepared commit message. Pre-commit fires too early —
COMMIT_EDITMSG does not exist yet when pre-commit runs.

The pre-commit hook aggregator may still invoke this script (it iterates
all *.py under scripts/coding-rails/rules/). In that case there is no
message-path argument and we exit 0 silently. The real enforcement
happens only in the commit-msg hook.

Patterns and config are shared with the PR completion gate via
`_evidence_lib`. Per-project overrides in `.agent/coding-rails.config.yml`
affect BOTH the local commit-msg check AND the CI completion-gate scan.

Exits 0 on pass, non-zero on fail.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


# Stage hint: this rule depends on the commit-msg hook stage, not
# pre-commit. The aggregator may use this hint in future versions to
# skip pre-commit invocation entirely.
_STAGE = "commit-msg"


# Import the shared evidence lib (sibling of bundle/scripts/, which is
# the parent dir of this script's location). This works in the bundle
# source tree AND in installed targets (scripts/coding-rails/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _evidence_lib  # noqa: E402


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def find_repo_root() -> Path:
    return Path(run("git", "rev-parse", "--show-toplevel"))


def read_commit_msg(msg_path_arg: str | None) -> str:
    """Read the commit message. Only the commit-msg hook invocation
    passes the path as $1. Pre-commit invocation has no arg, in which
    case this returns empty string — main() will exit 0 quietly.

    Earlier versions of this script fell back to reading
    .git/COMMIT_EDITMSG when no arg was passed. That was unsafe because
    COMMIT_EDITMSG can hold stale content from a previously-failed
    commit. The strict no-arg → return "" behavior closes that hole.
    See coding-rails issue #7.
    """
    if not msg_path_arg:
        return ""
    return Path(msg_path_arg).read_text(encoding="utf-8")


def fail(reason: str) -> None:
    sys.stderr.write(f"rule 008 (evidence required): {reason}\n")


def main() -> int:
    msg_arg = sys.argv[1] if len(sys.argv) > 1 else None
    msg = read_commit_msg(msg_arg)
    if not msg:
        # No commit-msg path → pre-commit invocation (or no message yet).
        # Real enforcement happens only via the commit-msg hook stage.
        return 0

    repo_root = find_repo_root()
    passes, completion_hits = _evidence_lib.check_message(repo_root, msg)
    if passes:
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
