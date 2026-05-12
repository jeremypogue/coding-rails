#!/usr/bin/env python3
"""coding-rails — agent_scope_watch.py

Live polling watcher. Runs alongside the agent's working session and
checks the working tree against the task's allowed_paths at a regular
interval. On detection of an out-of-scope change, writes a drift record
to `.agent/drift/<task_id>.json` and (optionally) exits non-zero.

The watcher is the LIVE (mid-session) layer of rule 010. The drift
record it produces is what makes pre-commit, pre-push, finish_task,
and the CI completion gate refuse to proceed until the operator
resolves the drift.

Limits (be honest):
  - The watcher DETECTS drift; it does NOT prevent the file write.
    A determined agent can still write the out-of-scope file. The
    drift record then blocks every downstream layer.
  - A determined agent can kill this process. The drift record it
    already wrote persists and still blocks downstream — but new
    drift won't be detected until a new watcher (or a one-shot
    `agent_scope_status.py`) runs.
  - True prevention requires OS-level isolation (separate UID, ACLs,
    scoped container). This watcher works alongside those but does
    not replace them.

Usage:
  agent_scope_watch.py [--task-id <id>] [--interval <sec>]
                       [--fail-on-drift] [--once] [--quiet]

  --task-id <id>      target a specific task; otherwise resolved by current branch
  --interval <sec>    polling interval, default 1.0
  --fail-on-drift     exit non-zero on first drift detected
  --once              run a single check and exit (same as agent_scope_status.py --write-drift)
  --quiet             suppress informational stdout
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCOPE_CHECK = os.path.join(SCRIPT_DIR, "agent_scope_check.py")


def _repo_root() -> Path:
    return Path(
        subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], text=True
        ).strip()
    )


def _resolve_task_id(explicit: str | None) -> str | None:
    """If task_id wasn't passed, infer from the current branch's ledger."""
    if explicit:
        return explicit
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True
        ).strip()
    except subprocess.CalledProcessError:
        return None
    if not branch.startswith("agent/"):
        return None
    tasks_dir = _repo_root() / ".agent" / "tasks"
    if not tasks_dir.is_dir():
        return None
    for path in sorted(tasks_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("branch") == branch:
            return data.get("task_id")
    return None


def _write_heartbeat(task_id: str | None) -> None:
    """Write .agent/state/<task_id>.heartbeat with a fresh timestamp.

    finish_task and pre-push check the staleness of this file. A dead
    or never-started watcher leaves no recent heartbeat — making it a
    visible signal that scope was not actively monitored.
    """
    if not task_id:
        return
    try:
        state_dir = _repo_root() / ".agent" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        hb = state_dir / f"{task_id}.heartbeat"
        hb.write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "updated_at": dt.datetime.utcnow().isoformat() + "Z",
                    "pid": os.getpid(),
                }
            ),
            encoding="utf-8",
        )
    except OSError:
        pass  # best-effort; don't crash the watcher on FS errors


def _invoke_status(task_id: str | None, write_drift: bool, quiet: bool) -> int:
    cmd = [sys.executable, SCOPE_CHECK]
    if task_id:
        cmd.extend(["--task-id", task_id])
    if write_drift:
        cmd.append("--write-drift")
    if quiet:
        cmd.append("--quiet")
    result = subprocess.run(cmd, capture_output=True, text=True)
    # Pass through stderr / stdout
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--fail-on-drift", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    task_id = _resolve_task_id(args.task_id)

    if args.once:
        _write_heartbeat(task_id)
        return _invoke_status(args.task_id, write_drift=True, quiet=args.quiet)

    if not args.quiet:
        print(
            f"scope-watch: polling every {args.interval}s. "
            f"task={task_id or '(auto-resolve per cycle)'}. "
            "Press Ctrl-C to stop."
        )

    last_state = 0
    try:
        while True:
            _write_heartbeat(task_id or _resolve_task_id(None))
            state = _invoke_status(args.task_id, write_drift=True, quiet=True)
            # state codes (from agent_scope_status.py):
            #   0 clean / 1 drift / 2 no-task (informational)
            if state == 1 and last_state != 1:
                # Just transitioned to drift — make it loud once
                sys.stderr.write(
                    "\nscope-watch: DRIFT DETECTED. Drift record written to "
                    ".agent/drift/<task_id>.json. Downstream layers (commit, "
                    "push, finish_task, CI gate) will refuse until the "
                    "operator resolves it.\n\n"
                )
                if args.fail_on_drift:
                    return 1
            if state == 0 and last_state == 1 and not args.quiet:
                # Transitioned back to clean (operator resolved)
                print("scope-watch: working tree is clean again.")
            last_state = state
            time.sleep(args.interval)
    except KeyboardInterrupt:
        if not args.quiet:
            print("\nscope-watch: stopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
