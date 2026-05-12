#!/usr/bin/env python3
"""coding-rails rule 001 — task ledger check.

Invoked by .githooks/pre-commit. Reads the staged file list, looks up the
current branch's task ledger under .agent/tasks/, and verifies:

  1. A task ledger exists for the current branch.
  2. Every staged file path is inside the ledger's `allowed_paths`.
  3. The ledger's `status` is not `done` or `superseded` (a closed task
     should not be receiving new commits).

Exits 0 on pass, non-zero on fail. Prints structured reasons to stderr.

Bypass: none for agents. Operator can edit the ledger directly.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from fnmatch import fnmatchcase
from pathlib import Path


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def find_repo_root() -> Path:
    return Path(run("git", "rev-parse", "--show-toplevel"))


def staged_files(repo_root: Path) -> list[str]:
    raw = run("git", "diff", "--cached", "--name-only", "--diff-filter=ACMR")
    return [line for line in raw.splitlines() if line]


def current_branch(repo_root: Path) -> str:
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


def path_matches_allowed(path: str, allowed: list[str]) -> bool:
    """A staged path matches if any entry in allowed_paths is an exact
    match OR a glob match. Globs use fnmatch semantics (forward-slash
    case-sensitive)."""
    for entry in allowed:
        if entry == path:
            return True
        if fnmatchcase(path, entry):
            return True
        # Directory entries (ending in /) match anything under them
        if entry.endswith("/") and path.startswith(entry):
            return True
    return False


def fail(reason: str) -> None:
    sys.stderr.write(f"rule 001 (task ledger): {reason}\n")


def main() -> int:
    repo_root = find_repo_root()
    staged = staged_files(repo_root)
    if not staged:
        # nothing staged — pass quietly; pre-commit will refuse the
        # commit on its own.
        return 0

    branch = current_branch(repo_root)

    # On the project's main/master/development branches the rule is
    # not enforced for direct operator commits. Agent branches always
    # match `agent/<tool>/<date>-<slug>` so the rule fires for them.
    operator_branches = {"main", "master", "develop", "trunk"}
    is_agent_branch = branch.startswith("agent/")
    if branch in operator_branches and not is_agent_branch:
        return 0

    if not is_agent_branch:
        # On any other branch (a non-agent feature branch the operator
        # is working on) the rule is informational, not blocking.
        return 0

    ledger = find_ledger_for_branch(repo_root, branch)
    if ledger is None:
        fail(
            f"no .agent/tasks/<task_id>.json references branch '{branch}'.\n"
            "  Run scripts/coding-rails/agent_start_task.sh to create one."
        )
        return 1

    try:
        data = json.loads(ledger.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        fail(f"ledger {ledger.relative_to(repo_root)} unreadable: {exc}")
        return 1

    status = data.get("status", "")
    if status in {"done", "superseded"}:
        fail(
            f"ledger {ledger.relative_to(repo_root)} has status={status!r}; "
            "closed tasks should not receive new commits."
        )
        return 1

    allowed = data.get("allowed_paths") or []
    if not isinstance(allowed, list) or not allowed:
        fail(
            f"ledger {ledger.relative_to(repo_root)} has no allowed_paths. "
            "Every task must explicitly enumerate the files it may modify."
        )
        return 1

    # Bookkeeping paths are always allowed without explicit declaration.
    # The task ledger itself must be commitable so CI can see it; the
    # exceptions file is operator-maintained; runtime state under
    # .agent/state/ is gitignored anyway but listing it here is harmless.
    bookkeeping = {
        str(ledger.relative_to(repo_root)).replace("\\", "/"),
        ".agent/test-coverage-exceptions.md",
    }

    out_of_scope = [
        p for p in staged
        if p not in bookkeeping and not path_matches_allowed(p, allowed)
    ]
    if out_of_scope:
        fail("the following staged paths are outside allowed_paths:")
        for p in out_of_scope:
            sys.stderr.write(f"    {p}\n")
        sys.stderr.write(
            "  allowed_paths in ledger:\n"
        )
        for entry in allowed:
            sys.stderr.write(f"    {entry}\n")
        sys.stderr.write(
            "  Edit the ledger to add the path (if it belongs to this task) "
            "or unstage the file.\n"
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
