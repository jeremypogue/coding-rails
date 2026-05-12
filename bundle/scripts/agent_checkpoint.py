#!/usr/bin/env python3
"""coding-rails — agent_checkpoint.py

Single-line scope status reporter. Designed to be invoked by the agent
after every file-changing turn and the output included in the agent's
response. Gives the operator visibility into scope state mid-conversation
without reading multiple files.

Output formats:

  coding-rails scope: CLEAN  task=20260512-foo  changed=2  allowed=2  hash=9b1c2a..

  coding-rails scope: DRIFT  task=20260512-foo  unauthorized=evil.py,config.yml

  coding-rails scope: NO-TASK  (not on an agent branch)

  coding-rails scope: NO-WATCHER  task=20260512-foo  (heartbeat missing — drift detection is OFF)

Exit codes mirror status:
  0 — CLEAN
  1 — DRIFT
  2 — NO-TASK / NO-WATCHER / config error

Usage:
  agent_checkpoint.py [--task-id <id>] [--max-heartbeat-age <sec>]

Designed to be cheap (<100ms typical) so an agent can run it frequently
without overhead.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCOPE_CHECK = os.path.join(SCRIPT_DIR, "agent_scope_check.py")


def _repo_root() -> Path:
    return Path(
        subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], text=True
        ).strip()
    )


def _resolve_task_for_branch(repo_root: Path, branch: str) -> dict | None:
    tasks_dir = repo_root / ".agent" / "tasks"
    if not tasks_dir.is_dir():
        return None
    for path in sorted(tasks_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("branch") == branch:
            return data
    return None


def _heartbeat_age_seconds(repo_root: Path, task_id: str) -> float | None:
    hb = repo_root / ".agent" / "state" / f"{task_id}.heartbeat"
    if not hb.is_file():
        return None
    try:
        data = json.loads(hb.read_text(encoding="utf-8"))
        ts = data.get("updated_at")
        if not ts:
            return None
        # ISO-8601 with trailing Z
        when = dt.datetime.fromisoformat(ts.rstrip("Z"))
        now = dt.datetime.utcnow()
        return (now - when).total_seconds()
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def _short_hash(allowed: list[str]) -> str:
    canonical = "\n".join(sorted(allowed))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]


def _changed_count(repo_root: Path) -> int:
    raw = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=str(repo_root),
        text=True,
    )
    return sum(1 for line in raw.splitlines() if line.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", default=None)
    parser.add_argument(
        "--max-heartbeat-age",
        type=float,
        default=10.0,
        help="Seconds. If the watcher's last heartbeat is older than this, "
        "report NO-WATCHER (suggests drift detection is offline).",
    )
    args = parser.parse_args()

    repo_root = _repo_root()

    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True
        ).strip()
    except subprocess.CalledProcessError:
        print("coding-rails scope: NO-TASK  (not in a git repo)")
        return 2

    if not branch.startswith("agent/") and not args.task_id:
        print(f"coding-rails scope: NO-TASK  (branch={branch})")
        return 2

    if args.task_id:
        ledger_path = repo_root / ".agent" / "tasks" / f"{args.task_id}.json"
        if not ledger_path.is_file():
            print(f"coding-rails scope: NO-TASK  (no ledger for {args.task_id})")
            return 2
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    else:
        ledger = _resolve_task_for_branch(repo_root, branch)
        if ledger is None:
            print(f"coding-rails scope: NO-TASK  (branch={branch}, no matching ledger)")
            return 2

    task_id = ledger["task_id"]
    allowed = ledger.get("allowed_paths") or []

    # Check the scope state via agent_scope_check (the canonical logic).
    # We don't request a drift record write — checkpoint is read-only.
    rc = subprocess.run(
        [sys.executable, SCOPE_CHECK, "--task-id", task_id, "--quiet"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    ).returncode

    hb_age = _heartbeat_age_seconds(repo_root, task_id)
    hash_short = _short_hash(allowed)

    if rc == 1:
        # Drift. Pull the unauthorized list from the drift record if it exists.
        drift = repo_root / ".agent" / "drift" / f"{task_id}.json"
        unauthorized: list[str] = []
        if drift.is_file():
            try:
                d = json.loads(drift.read_text(encoding="utf-8"))
                unauthorized = d.get("unauthorized_paths") or []
            except (json.JSONDecodeError, OSError):
                pass
        u_str = ",".join(unauthorized[:3]) if unauthorized else "?"
        if len(unauthorized) > 3:
            u_str += f",+{len(unauthorized) - 3}"
        print(f"coding-rails scope: DRIFT  task={task_id}  unauthorized={u_str}")
        return 1

    changed = _changed_count(repo_root)

    if hb_age is None:
        print(
            f"coding-rails scope: NO-WATCHER  task={task_id}  changed={changed}  "
            f"allowed={len(allowed)}  hash={hash_short}  "
            "(drift detection is OFF; run agent_scope_watch.py)"
        )
        # Not a hard fail — operator may have decided to run without watcher.
        # finish_task is the layer that enforces watcher presence.
        return 0

    if hb_age > args.max_heartbeat_age:
        print(
            f"coding-rails scope: NO-WATCHER  task={task_id}  changed={changed}  "
            f"allowed={len(allowed)}  hash={hash_short}  "
            f"(last heartbeat {hb_age:.0f}s ago — watcher may be dead)"
        )
        return 0

    print(
        f"coding-rails scope: CLEAN  task={task_id}  changed={changed}  "
        f"allowed={len(allowed)}  hash={hash_short}  hb={hb_age:.0f}s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
