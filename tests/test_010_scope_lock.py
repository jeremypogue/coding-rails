"""Tests for rule 010 — active scope lock.

Covers:
  - 010_scope_lock.py check script (drift record blocks; scope-lock hash check)
  - agent_scope_status.py one-shot detection
  - Bookkeeping paths auto-allowed without explicit declaration
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RULE = PROJECT_ROOT / "bundle" / "scripts" / "rules" / "010_scope_lock.py"
SCOPE_CHECK = PROJECT_ROOT / "bundle" / "scripts" / "agent_scope_check.py"
CHECKPOINT = PROJECT_ROOT / "bundle" / "scripts" / "agent_checkpoint.py"


def _scope_hash(paths: list[str]) -> str:
    canonical = "\n".join(sorted(paths))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _write_scope_lock(repo: Path, task_id: str, branch: str, allowed: list[str]) -> Path:
    scope_dir = repo / ".agent" / "scope"
    scope_dir.mkdir(parents=True, exist_ok=True)
    lock = scope_dir / f"{task_id}.lock"
    lock.write_text(
        json.dumps(
            {
                "task_id": task_id,
                "branch": branch,
                "allowed_paths": allowed,
                "scope_hash": _scope_hash(allowed),
                "locked_at": "2026-05-12T00:00:00Z",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return lock


def _write_drift(repo: Path, task_id: str, branch: str, paths: list[str], status: str = "unresolved") -> Path:
    drift_dir = repo / ".agent" / "drift"
    drift_dir.mkdir(parents=True, exist_ok=True)
    drift = drift_dir / f"{task_id}.json"
    drift.write_text(
        json.dumps(
            {
                "task_id": task_id,
                "branch": branch,
                "detected_at": "2026-05-12T12:00:00Z",
                "unauthorized_paths": paths,
                "status": status,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return drift


# ---- rule 010 check script ----

def test_010_passes_on_main(tmp_repo, run_rule):
    """Operator branches are out of scope for rule 010."""
    result = run_rule("010_scope_lock.py")
    assert result.returncode == 0, result.stderr


def test_010_passes_when_no_drift_and_no_lock(tmp_repo, run_rule, make_branch, ledger_factory):
    branch = "agent/test/20260512-foo"
    make_branch(branch)
    ledger_factory(branch=branch, allowed_paths=["src/foo.py"])
    result = run_rule("010_scope_lock.py")
    assert result.returncode == 0, result.stderr


def test_010_blocks_on_unresolved_drift(tmp_repo, run_rule, make_branch, ledger_factory):
    branch = "agent/test/20260512-foo"
    make_branch(branch)
    ledger_factory(branch=branch, allowed_paths=["src/foo.py"], task_id="20260512-foo")
    _write_drift(tmp_repo, "20260512-foo", branch, ["evil.py"], status="unresolved")
    result = run_rule("010_scope_lock.py")
    assert result.returncode != 0
    assert "drift" in result.stderr.lower()
    assert "evil.py" in result.stderr


def test_010_passes_on_resolved_drift(tmp_repo, run_rule, make_branch, ledger_factory):
    branch = "agent/test/20260512-foo"
    make_branch(branch)
    ledger_factory(branch=branch, allowed_paths=["src/foo.py"], task_id="20260512-foo")
    _write_drift(tmp_repo, "20260512-foo", branch, ["evil.py"], status="resolved")
    result = run_rule("010_scope_lock.py")
    assert result.returncode == 0, result.stderr


def test_010_blocks_on_scope_hash_mismatch(tmp_repo, run_rule, make_branch, ledger_factory):
    """REGRESSION: ledger allowed_paths changed since the scope was locked."""
    branch = "agent/test/20260512-foo"
    make_branch(branch)
    # Ledger says foo.py + bar.py (expanded)
    ledger_factory(
        branch=branch,
        allowed_paths=["src/foo.py", "src/bar.py"],
        task_id="20260512-foo",
    )
    # Scope lock was written when only foo.py was allowed
    _write_scope_lock(tmp_repo, "20260512-foo", branch, ["src/foo.py"])
    result = run_rule("010_scope_lock.py")
    assert result.returncode != 0
    assert "scope" in result.stderr.lower()
    assert "hash" in result.stderr.lower()


def test_010_passes_when_scope_lock_matches_ledger(tmp_repo, run_rule, make_branch, ledger_factory):
    branch = "agent/test/20260512-foo"
    make_branch(branch)
    paths = ["src/foo.py", "src/bar.py"]
    ledger_factory(branch=branch, allowed_paths=paths, task_id="20260512-foo")
    _write_scope_lock(tmp_repo, "20260512-foo", branch, paths)
    result = run_rule("010_scope_lock.py")
    assert result.returncode == 0, result.stderr


# ---- agent_scope_status.py ----

def _run_status(repo: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCOPE_CHECK), *extra],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )


def _run_checkpoint(repo: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECKPOINT), *extra],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )


def _write_heartbeat(repo: Path, task_id: str, age_seconds: float = 0.0) -> Path:
    """Write a heartbeat with a timestamp `age_seconds` in the past."""
    import datetime as dt
    state = repo / ".agent" / "state"
    state.mkdir(parents=True, exist_ok=True)
    hb = state / f"{task_id}.heartbeat"
    when = dt.datetime.utcnow() - dt.timedelta(seconds=age_seconds)
    hb.write_text(
        json.dumps({
            "task_id": task_id,
            "updated_at": when.isoformat() + "Z",
            "pid": 0,
        }),
        encoding="utf-8",
    )
    return hb


def test_status_no_task_returns_2(tmp_repo, make_branch):
    """Operator branch with no ledger → exit 2 (informational)."""
    result = _run_status(tmp_repo)
    assert result.returncode == 2


def test_status_clean_returns_0(tmp_repo, make_branch, ledger_factory):
    branch = "agent/test/20260512-foo"
    make_branch(branch)
    ledger_factory(branch=branch, allowed_paths=["src/foo.py"], task_id="20260512-foo")
    result = _run_status(tmp_repo)
    assert result.returncode == 0, result.stderr


def test_status_detects_out_of_scope_change(tmp_repo, make_branch, ledger_factory):
    branch = "agent/test/20260512-foo"
    make_branch(branch)
    ledger_factory(branch=branch, allowed_paths=["src/foo.py"], task_id="20260512-foo")
    # Create an out-of-scope file
    (tmp_repo / "evil.py").write_text("import os\n", encoding="utf-8")
    result = _run_status(tmp_repo)
    assert result.returncode == 1
    assert "evil.py" in result.stderr


def test_status_writes_drift_record_with_flag(tmp_repo, make_branch, ledger_factory):
    branch = "agent/test/20260512-foo"
    make_branch(branch)
    ledger_factory(branch=branch, allowed_paths=["src/foo.py"], task_id="20260512-foo")
    (tmp_repo / "evil.py").write_text("oops", encoding="utf-8")
    result = _run_status(tmp_repo, "--write-drift")
    assert result.returncode == 1
    drift = tmp_repo / ".agent" / "drift" / "20260512-foo.json"
    assert drift.is_file()
    data = json.loads(drift.read_text(encoding="utf-8"))
    assert data["status"] == "unresolved"
    assert "evil.py" in data["unauthorized_paths"]


def test_status_clean_with_in_scope_change(tmp_repo, make_branch, ledger_factory):
    branch = "agent/test/20260512-foo"
    make_branch(branch)
    ledger_factory(branch=branch, allowed_paths=["src/foo.py"], task_id="20260512-foo")
    (tmp_repo / "src").mkdir()
    (tmp_repo / "src" / "foo.py").write_text("x = 1\n", encoding="utf-8")
    result = _run_status(tmp_repo)
    assert result.returncode == 0, result.stderr


def test_status_bookkeeping_paths_auto_allowed(tmp_repo, make_branch, ledger_factory):
    """The ledger file itself and .agent/state/ are bookkeeping → allowed."""
    branch = "agent/test/20260512-foo"
    make_branch(branch)
    ledger_factory(branch=branch, allowed_paths=["src/foo.py"], task_id="20260512-foo")
    # "Modify" the ledger (just touch the file)
    state_dir = tmp_repo / ".agent" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "runtime.json").write_text("{}", encoding="utf-8")
    result = _run_status(tmp_repo)
    assert result.returncode == 0, (
        f"bookkeeping paths should be auto-allowed:\n{result.stderr}"
    )


def test_status_stale_unresolved_drift_detected(tmp_repo, make_branch, ledger_factory):
    """If the working tree is clean but an unresolved drift record persists,
    status reports drift (operator hasn't formally resolved)."""
    branch = "agent/test/20260512-foo"
    make_branch(branch)
    ledger_factory(branch=branch, allowed_paths=["src/foo.py"], task_id="20260512-foo")
    _write_drift(tmp_repo, "20260512-foo", branch, ["was-evil.py"], status="unresolved")
    result = _run_status(tmp_repo)
    assert result.returncode == 1
    assert "drift" in result.stderr.lower()


def test_status_resolved_drift_does_not_block(tmp_repo, make_branch, ledger_factory):
    branch = "agent/test/20260512-foo"
    make_branch(branch)
    ledger_factory(branch=branch, allowed_paths=["src/foo.py"], task_id="20260512-foo")
    _write_drift(tmp_repo, "20260512-foo", branch, ["was-evil.py"], status="resolved")
    result = _run_status(tmp_repo)
    assert result.returncode == 0, result.stderr


# ---- agent_checkpoint.py ----

def test_checkpoint_no_task_returns_2(tmp_repo):
    """On main with no ledger, checkpoint reports NO-TASK."""
    result = _run_checkpoint(tmp_repo)
    assert result.returncode == 2
    assert "NO-TASK" in result.stdout


def test_checkpoint_no_watcher_when_heartbeat_missing(tmp_repo, make_branch, ledger_factory):
    """Clean scope but no heartbeat → NO-WATCHER (informational, exit 0)."""
    branch = "agent/test/20260512-foo"
    make_branch(branch)
    ledger_factory(branch=branch, allowed_paths=["src/foo.py"], task_id="20260512-foo")
    result = _run_checkpoint(tmp_repo)
    # No heartbeat file → NO-WATCHER reported, but exit 0 (informational)
    assert result.returncode == 0, result.stderr
    assert "NO-WATCHER" in result.stdout


def test_checkpoint_clean_with_fresh_heartbeat(tmp_repo, make_branch, ledger_factory):
    branch = "agent/test/20260512-foo"
    make_branch(branch)
    ledger_factory(branch=branch, allowed_paths=["src/foo.py"], task_id="20260512-foo")
    _write_heartbeat(tmp_repo, "20260512-foo", age_seconds=2)
    result = _run_checkpoint(tmp_repo)
    assert result.returncode == 0, result.stderr
    assert "CLEAN" in result.stdout
    assert "20260512-foo" in result.stdout


def test_checkpoint_no_watcher_when_heartbeat_stale(tmp_repo, make_branch, ledger_factory):
    branch = "agent/test/20260512-foo"
    make_branch(branch)
    ledger_factory(branch=branch, allowed_paths=["src/foo.py"], task_id="20260512-foo")
    # 30s old heartbeat with default 10s threshold → stale
    _write_heartbeat(tmp_repo, "20260512-foo", age_seconds=30)
    result = _run_checkpoint(tmp_repo)
    assert result.returncode == 0
    assert "NO-WATCHER" in result.stdout
    assert "watcher may be dead" in result.stdout


def test_checkpoint_drift_when_out_of_scope_change(tmp_repo, make_branch, ledger_factory):
    branch = "agent/test/20260512-foo"
    make_branch(branch)
    ledger_factory(branch=branch, allowed_paths=["src/foo.py"], task_id="20260512-foo")
    # Create out-of-scope file
    (tmp_repo / "evil.py").write_text("oops", encoding="utf-8")
    result = _run_checkpoint(tmp_repo)
    assert result.returncode == 1
    assert "DRIFT" in result.stdout


def test_checkpoint_drift_with_drift_record(tmp_repo, make_branch, ledger_factory):
    """If a drift record already exists, checkpoint shows the unauthorized list."""
    branch = "agent/test/20260512-foo"
    make_branch(branch)
    ledger_factory(branch=branch, allowed_paths=["src/foo.py"], task_id="20260512-foo")
    _write_drift(tmp_repo, "20260512-foo", branch, ["evil.py", "more-evil.py"])
    result = _run_checkpoint(tmp_repo)
    assert result.returncode == 1
    assert "DRIFT" in result.stdout
    # Either the drift record's contents or working-tree detection
    assert "evil" in result.stdout.lower()
