#!/usr/bin/env python3
"""coding-rails — agent_scope_check.py

One-shot scope status check (the canonical rule-010 detection logic).
Reports whether the current working tree is within the task's
allowed_paths, and surfaces any existing drift record. Does not
modify state unless `--write-drift` is passed.

Used by:
  - Operators checking session state at any time
  - `agent_scope_watch.py` (polls this logic continuously)
  - `agent_checkpoint.py` (one-liner status for per-turn output)

Exit codes:
  0 — clean (no drift, no out-of-scope changes), OR rule disabled via config
  1 — drift detected (out-of-scope changes OR unresolved drift record)
  2 — no task / no ledger for current branch (informational)

Usage:
  agent_scope_check.py [--task-id <id>] [--write-drift] [--quiet]

  --task-id <id>    explicitly target a task; otherwise resolved by current branch
  --write-drift     if drift detected, write/update .agent/drift/<task_id>.json
                    (used by the watcher; not normal CLI invocation)
  --quiet           suppress informational stdout (errors still print)

Configuration (via `.agent/coding-rails.config.yml`):
  scope_enforcement:
    enabled: true                  # rule 010 active by default
    bookkeeping_paths: []          # extra globs always allowed beyond defaults
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from fnmatch import fnmatchcase
from pathlib import Path


# Bookkeeping paths always allowed (no explicit declaration needed).
# These include the rule 010 plumbing files themselves — the operator
# may commit/modify drift records as part of resolving them, and the
# scope lock is written by agent_start_task.sh.
GLOBAL_BOOKKEEPING_GLOBS = [
    ".agent/state/*",
    ".agent/state/**/*",
    ".agent/state/.gitignore",
    ".agent/precommit-markers/*",
    ".agent/precommit-markers/.gitignore",
    ".agent/test-coverage-exceptions.md",
    ".agent/.gitignore",
    ".agent/drift/*",        # drift records — runtime, operator-resolvable
    ".agent/drift/.gitignore",
    ".agent/scope/*",        # scope locks — written by start_task
    ".agent/scope/.gitkeep",
    ".agent/coding-rails.config.yml",   # per-project config; operator-edited
]


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def find_repo_root() -> Path:
    return Path(run("git", "rev-parse", "--show-toplevel"))


def load_scope_config(repo_root: Path) -> dict:
    """Load `scope_enforcement` from `.agent/coding-rails.config.yml`.
    Returns defaults if the file or PyYAML is unavailable.

    Defaults:
      enabled: true
      bookkeeping_paths: []
      watch_interval_seconds: 1.0
      require_clean_scope_before_finish: true
      fail_on_drift: true
    """
    defaults = {
        "enabled": True,
        "bookkeeping_paths": [],
        "watch_interval_seconds": 1.0,
        "require_clean_scope_before_finish": True,
        "fail_on_drift": True,
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
    section = loaded.get("scope_enforcement") or {}
    out = dict(defaults)
    for key in defaults:
        if key in section:
            out[key] = section[key]
    return out


def current_branch() -> str:
    return run("git", "rev-parse", "--abbrev-ref", "HEAD")


def find_ledger(repo_root: Path, branch: str, task_id: str | None) -> Path | None:
    tasks_dir = repo_root / ".agent" / "tasks"
    if not tasks_dir.is_dir():
        return None
    if task_id:
        candidate = tasks_dir / f"{task_id}.json"
        return candidate if candidate.is_file() else None
    for path in sorted(tasks_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("branch") == branch:
            return path
    return None


def working_tree_changes(repo_root: Path) -> list[tuple[str, str]]:
    """Return list of (status_code, path) for every changed/untracked file."""
    raw = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=str(repo_root),
        text=True,
    )
    out: list[tuple[str, str]] = []
    for line in raw.splitlines():
        if not line:
            continue
        # porcelain format: XY <path>  (XY is 2 chars, then space, then path)
        if len(line) < 4:
            continue
        status = line[:2]
        path = line[3:].strip()
        # Handle renames (e.g. "R  old -> new")
        if " -> " in path:
            old, new = path.split(" -> ", 1)
            out.append((status, old.strip().strip('"')))
            out.append((status, new.strip().strip('"')))
        else:
            out.append((status, path.strip().strip('"')))
    return out


def path_matches(path: str, patterns: list[str]) -> bool:
    """fnmatch glob match, with directory-prefix shortcut."""
    for pattern in patterns:
        if pattern == path:
            return True
        if fnmatchcase(path, pattern):
            return True
        if pattern.endswith("/") and path.startswith(pattern):
            return True
    return False


def is_in_scope(
    path: str, allowed_paths: list[str], bookkeeping_paths: list[str]
) -> bool:
    if path_matches(path, GLOBAL_BOOKKEEPING_GLOBS):
        return True
    if path_matches(path, bookkeeping_paths):
        return True
    if path_matches(path, allowed_paths):
        return True
    return False


def emit_drift_record(
    repo_root: Path,
    task_id: str,
    branch: str,
    unauthorized: list[str],
    write: bool,
) -> Path:
    drift_dir = repo_root / ".agent" / "drift"
    drift_dir.mkdir(parents=True, exist_ok=True)
    drift_path = drift_dir / f"{task_id}.json"
    record = {
        "task_id": task_id,
        "branch": branch,
        "detected_at": dt.datetime.utcnow().isoformat() + "Z",
        "unauthorized_paths": sorted(set(unauthorized)),
        "status": "unresolved",
    }
    if write:
        drift_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return drift_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", default=None)
    parser.add_argument(
        "--write-drift",
        action="store_true",
        help="Write/update .agent/drift/<task_id>.json on detection.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress informational output (errors still print).",
    )
    args = parser.parse_args()

    repo_root = find_repo_root()
    config = load_scope_config(repo_root)

    if not config.get("enabled", True):
        if not args.quiet:
            print("scope-check: rule 010 disabled via scope_enforcement.enabled=false.")
        return 0

    branch = current_branch()

    if not branch.startswith("agent/") and not args.task_id:
        if not args.quiet:
            print("scope-check: not on an agent branch; rule 010 does not apply.")
        return 2

    ledger_path = find_ledger(repo_root, branch, args.task_id)
    if ledger_path is None:
        if not args.quiet:
            sys.stderr.write(
                "scope-check: no ledger for branch / task-id; nothing to check.\n"
            )
        return 2

    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        sys.stderr.write(f"scope-check: ledger unreadable: {exc}\n")
        return 2

    task_id = ledger["task_id"]
    allowed_paths: list[str] = ledger.get("allowed_paths") or []
    bookkeeping: list[str] = list(ledger.get("bookkeeping_paths") or [])
    # The ledger itself is always bookkeeping
    bookkeeping.append(
        str(ledger_path.relative_to(repo_root)).replace("\\", "/")
    )
    # The scope lock for this task is always bookkeeping
    bookkeeping.append(f".agent/scope/{task_id}.lock")
    # Per-project additions from scope_enforcement.bookkeeping_paths
    bookkeeping.extend(config.get("bookkeeping_paths") or [])

    changes = working_tree_changes(repo_root)
    unauthorized = [p for _, p in changes if not is_in_scope(p, allowed_paths, bookkeeping)]

    # Also check for an existing drift record
    drift_path = repo_root / ".agent" / "drift" / f"{task_id}.json"
    existing_drift = None
    if drift_path.is_file():
        try:
            existing_drift = json.loads(drift_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing_drift = {"status": "unresolved", "_unreadable": True}

    if unauthorized:
        sys.stderr.write(
            f"scope-check: DRIFT — {len(unauthorized)} unauthorized path(s) "
            f"in working tree for task {task_id}\n"
        )
        for p in sorted(set(unauthorized)):
            sys.stderr.write(f"    {p}\n")
        sys.stderr.write(
            f"  allowed_paths ({len(allowed_paths)}):\n"
        )
        for p in allowed_paths:
            sys.stderr.write(f"    {p}\n")

        if args.write_drift:
            emit_drift_record(repo_root, task_id, branch, unauthorized, write=True)
            sys.stderr.write(
                f"  drift record written: "
                f"{(drift_path).relative_to(repo_root)}\n"
            )
        return 1

    if existing_drift and existing_drift.get("status") == "unresolved":
        sys.stderr.write(
            f"scope-check: stale unresolved drift record for task {task_id}.\n"
            f"  Working tree is currently clean, but the drift record at\n"
            f"  {drift_path.relative_to(repo_root)} remains unresolved.\n"
            f"  Operator must either set status: resolved or delete the file.\n"
        )
        return 1

    if not args.quiet:
        print(
            f"scope-check: clean. task={task_id}  branch={branch}  "
            f"allowed_paths={len(allowed_paths)}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
