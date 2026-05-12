#!/usr/bin/env python3
"""coding-rails rule 010 — active scope lock.

Invoked by .githooks/pre-commit. Refuses commits while an unresolved
drift record exists for the current branch's task. Also refuses if the
ledger's allowed_paths has been edited since the scope was locked.

Drift detection itself happens in `agent_scope_watch.py` (live polling)
and `agent_scope_status.py` (one-shot). This rule is the choke point
that turns a drift record into a hard refusal at commit time. The same
drift-record check fires from `agent_finish_task.sh` (before push) and
`agent_completion_gate.py` (at PR time).

Exits 0 on pass, non-zero on fail.

See `.agent/rules/010-scope-lock.md` for the resolution paths an
operator must take when this rule fires.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def find_repo_root() -> Path:
    return Path(run("git", "rev-parse", "--show-toplevel"))


def current_branch() -> str:
    return run("git", "rev-parse", "--abbrev-ref", "HEAD")


def find_ledger_for_branch(repo_root: Path, branch: str) -> Path | None:
    tasks_dir = repo_root / ".agent" / "tasks"
    if not tasks_dir.is_dir():
        return None
    for path in sorted(tasks_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("branch") == branch:
            return path
    return None


def compute_scope_hash(allowed_paths: list[str]) -> str:
    """Deterministic hash of the sorted allowed_paths list."""
    canonical = "\n".join(sorted(allowed_paths))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fail(reason: str) -> None:
    sys.stderr.write(f"rule 010 (scope lock): {reason}\n")


def file_existed_in_head(repo_root: Path, rel_path: str) -> bool:
    """True if the path exists in HEAD's tree (i.e. was committed before)."""
    try:
        subprocess.check_call(
            ["git", "cat-file", "-e", f"HEAD:{rel_path}"],
            cwd=str(repo_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def staged_modifies(repo_root: Path, rel_path: str) -> bool:
    """True if the path is staged for modification (not addition)."""
    try:
        raw = subprocess.check_output(
            ["git", "diff", "--cached", "--name-status", "--", rel_path],
            cwd=str(repo_root),
            text=True,
        ).strip()
    except subprocess.CalledProcessError:
        return False
    if not raw:
        return False
    # Format: <status>\t<path>. Status M = modify, A = add, D = delete.
    status = raw.split("\t", 1)[0]
    return status.startswith("M") or status.startswith("D")


def main() -> int:
    repo_root = find_repo_root()
    branch = current_branch()

    # Only fires on agent/* branches — operator branches are out of
    # scope for this rule, same as rule 001.
    if not branch.startswith("agent/"):
        return 0

    ledger = find_ledger_for_branch(repo_root, branch)
    if ledger is None:
        # Rule 001 will already complain about this; we don't double-fail.
        return 0

    try:
        ledger_data = json.loads(ledger.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0

    task_id = ledger_data.get("task_id")
    if not task_id:
        return 0

    # ---- check 0: immutability of scope lock + drift record ----
    # The scope lock is created by agent_start_task.sh and is then immutable
    # from the agent's perspective. The drift record is written by the
    # watcher and only resolved by the operator. If either was staged
    # for MODIFICATION (not initial addition) without the operator escape
    # hatch, refuse the commit. This closes the "agent edits ledger AND
    # scope lock together" loophole that defeats the hash check below.
    operator_override = os.environ.get("CODING_RAILS_OPERATOR_SCOPE_UPDATE") == "1"

    scope_lock_rel = f".agent/scope/{task_id}.lock"
    drift_rel = f".agent/drift/{task_id}.json"

    if not operator_override:
        if file_existed_in_head(repo_root, scope_lock_rel) and staged_modifies(repo_root, scope_lock_rel):
            fail(
                f"refusing to commit modification of {scope_lock_rel}"
            )
            sys.stderr.write(
                "  The scope lock is immutable after task start. Modifying it\n"
                "  defeats the scope-hash check that catches ledger expansion.\n"
                "  Operator override (after manual review): set\n"
                "    CODING_RAILS_OPERATOR_SCOPE_UPDATE=1 git commit ...\n"
                "  Audit: that env var leaves a visible trail in operator logs.\n"
            )
            return 1

        if file_existed_in_head(repo_root, drift_rel) and staged_modifies(repo_root, drift_rel):
            # Drift record state transition (e.g. unresolved → resolved)
            # may only be performed by the operator. The watcher writes
            # the record fresh; resolution flips status; the agent must
            # not stage either.
            fail(
                f"refusing to commit modification of {drift_rel}"
            )
            sys.stderr.write(
                "  Drift records are written by agent_scope_watch.py / agent_scope_check.py\n"
                "  and resolved by operator action. Agents may not flip status\n"
                "  or otherwise edit existing drift records.\n"
                "  Operator override: CODING_RAILS_OPERATOR_SCOPE_UPDATE=1 git commit ...\n"
            )
            return 1

    # ---- check 1: drift record ----
    drift_path = repo_root / ".agent" / "drift" / f"{task_id}.json"
    if drift_path.is_file():
        try:
            drift = json.loads(drift_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            drift = {"status": "unresolved", "_unreadable": True}
        status = drift.get("status", "unresolved")
        if status == "unresolved":
            fail(
                f"unresolved drift record at {drift_path.relative_to(repo_root)}"
            )
            unauthorized = drift.get("unauthorized_paths") or []
            if unauthorized:
                sys.stderr.write("  unauthorized paths detected during session:\n")
                for p in unauthorized:
                    sys.stderr.write(f"    {p}\n")
            sys.stderr.write(
                "  Operator must resolve before commit/push:\n"
                "    1. Revert the unauthorized changes (git restore <path>)\n"
                "       and delete .agent/drift/<task_id>.json, OR\n"
                "    2. Expand the ledger's allowed_paths to cover the new\n"
                "       paths, update .agent/scope/<task_id>.lock to match,\n"
                "       and mark the drift record status: resolved, OR\n"
                "    3. Abort the task (mark ledger status: superseded).\n"
                "  See .agent/rules/010-scope-lock.md for details.\n"
            )
            return 1

    # ---- check 2: scope lock consistency ----
    scope_lock_path = repo_root / ".agent" / "scope" / f"{task_id}.lock"
    if scope_lock_path.is_file():
        try:
            lock = json.loads(scope_lock_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            fail(f"scope lock {scope_lock_path.relative_to(repo_root)} is unreadable")
            return 1

        locked_hash = lock.get("scope_hash")
        current_allowed = ledger_data.get("allowed_paths") or []
        current_hash = compute_scope_hash(current_allowed)

        if locked_hash and locked_hash != current_hash:
            fail(
                "ledger allowed_paths has changed since scope was locked at "
                f"task start ({lock.get('locked_at', 'unknown time')})."
            )
            sys.stderr.write(
                f"    locked scope hash:  {locked_hash}\n"
                f"    current ledger hash: {current_hash}\n"
                "  Scope expansion mid-task requires operator review.\n"
                "  Either revert the allowed_paths change, or have the\n"
                "  operator update .agent/scope/<task_id>.lock to match\n"
                "  (which records the scope expansion as an audit event).\n"
            )
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
