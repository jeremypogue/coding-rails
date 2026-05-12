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


# ---- v0.6.0: scope-lock + drift-record immutability ----

def _commit_initial_lock_and_drift(tmp_repo, make_branch, ledger_factory, task_id):
    """Set up: branch + ledger + scope lock + drift record, all committed.
    Returns (branch, ledger_path)."""
    branch = f"agent/test/{task_id}"
    make_branch(branch)
    ledger = ledger_factory(
        branch=branch, allowed_paths=["src/foo.py"], task_id=task_id
    )
    lock_path = _write_scope_lock(tmp_repo, task_id, branch, ["src/foo.py"])
    drift_path = _write_drift(tmp_repo, task_id, branch, ["evil.py"], status="unresolved")
    # Commit the initial state so file_existed_in_head returns True
    subprocess.run(
        ["git", "add", str(ledger), str(lock_path), str(drift_path)],
        cwd=str(tmp_repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "initial task setup", "--no-verify"],
        cwd=str(tmp_repo),
        check=True,
        capture_output=True,
    )
    return branch, ledger, lock_path, drift_path


def test_010_blocks_staged_modification_of_existing_scope_lock(
    tmp_repo, make_branch, ledger_factory, run_rule
):
    """REGRESSION (v0.6.0): even if scope-hash matches, the rule refuses
    a staged modification of an already-committed scope lock."""
    branch, ledger, lock_path, drift_path = _commit_initial_lock_and_drift(
        tmp_repo, make_branch, ledger_factory, "20260512-immut"
    )
    # Resolve the drift first so the existing-drift check doesn't fire
    drift_path.write_text(
        json.dumps({
            "task_id": "20260512-immut",
            "branch": branch,
            "status": "resolved",
            "unauthorized_paths": ["evil.py"],
        }),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", str(drift_path)], cwd=str(tmp_repo), check=True)
    subprocess.run(
        ["git", "commit", "-m", "resolve drift", "--no-verify"],
        cwd=str(tmp_repo), check=True, capture_output=True,
    )
    # Now modify the scope lock — even keeping the same paths, this is
    # a modification of an existing file. Should be refused.
    lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
    lock_data["locked_at"] = "2026-05-12T20:00:00Z"  # benign field change
    lock_path.write_text(json.dumps(lock_data, indent=2), encoding="utf-8")
    subprocess.run(["git", "add", str(lock_path)], cwd=str(tmp_repo), check=True)

    result = run_rule("010_scope_lock.py")
    assert result.returncode != 0
    err = result.stderr.lower()
    assert "scope lock" in err or "modification" in err
    assert "scope/20260512-immut.lock" in result.stderr


def test_010_blocks_staged_modification_of_existing_drift_record(
    tmp_repo, make_branch, ledger_factory, run_rule
):
    """REGRESSION (v0.6.0): the rule refuses agent-side modification of
    an existing drift record (e.g. flipping status to resolved)."""
    branch, ledger, lock_path, drift_path = _commit_initial_lock_and_drift(
        tmp_repo, make_branch, ledger_factory, "20260512-drift-mut"
    )
    # Agent flips status to resolved (without operator override)
    drift_data = json.loads(drift_path.read_text(encoding="utf-8"))
    drift_data["status"] = "resolved"
    drift_path.write_text(json.dumps(drift_data, indent=2), encoding="utf-8")
    subprocess.run(["git", "add", str(drift_path)], cwd=str(tmp_repo), check=True)

    result = run_rule("010_scope_lock.py")
    assert result.returncode != 0
    err = result.stderr.lower()
    assert "drift" in err and ("modification" in err or "edit" in err)
    assert "drift/20260512-drift-mut.json" in result.stderr


def test_010_allows_initial_creation_of_scope_lock(
    tmp_repo, make_branch, ledger_factory, run_rule
):
    """Baseline: on the first commit of a task, the scope lock is being
    ADDED (not modified). The rule should allow this case."""
    branch = "agent/test/20260512-fresh"
    make_branch(branch)
    ledger_factory(
        branch=branch, allowed_paths=["src/foo.py"], task_id="20260512-fresh"
    )
    _write_scope_lock(tmp_repo, "20260512-fresh", branch, ["src/foo.py"])
    # Stage the new files (addition, not modification)
    subprocess.run(
        ["git", "add", ".agent/tasks/20260512-fresh.json", ".agent/scope/20260512-fresh.lock"],
        cwd=str(tmp_repo),
        check=True,
    )
    result = run_rule("010_scope_lock.py")
    assert result.returncode == 0, (
        f"initial scope-lock creation should be allowed: {result.stderr}"
    )


def test_010_operator_override_allows_scope_lock_modification(
    tmp_repo, make_branch, ledger_factory, run_rule
):
    """The CODING_RAILS_OPERATOR_SCOPE_UPDATE=1 env var permits the
    modification (operator manually approving scope expansion)."""
    import os as _os
    branch, ledger, lock_path, drift_path = _commit_initial_lock_and_drift(
        tmp_repo, make_branch, ledger_factory, "20260512-override"
    )
    # Resolve drift to isolate the scope-lock check
    drift_path.write_text(
        json.dumps({
            "task_id": "20260512-override",
            "branch": branch,
            "status": "resolved",
            "unauthorized_paths": [],
        }),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", str(drift_path)], cwd=str(tmp_repo), check=True)
    subprocess.run(
        ["git", "commit", "-m", "resolve", "--no-verify"],
        cwd=str(tmp_repo), check=True, capture_output=True,
    )

    # Operator expands scope: ledger AND scope lock together
    ledger_data = json.loads(ledger.read_text(encoding="utf-8"))
    ledger_data["allowed_paths"] = ["src/foo.py", "src/bar.py"]
    ledger.write_text(json.dumps(ledger_data, indent=2), encoding="utf-8")

    lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
    lock_data["allowed_paths"] = ["src/foo.py", "src/bar.py"]
    lock_data["scope_hash"] = _scope_hash(["src/foo.py", "src/bar.py"])
    lock_path.write_text(json.dumps(lock_data, indent=2), encoding="utf-8")

    subprocess.run(
        ["git", "add", str(ledger), str(lock_path)],
        cwd=str(tmp_repo), check=True,
    )

    # Without override: should fail
    result_blocked = subprocess.run(
        [sys.executable, str(RULE)],
        cwd=str(tmp_repo),
        capture_output=True,
        text=True,
    )
    assert result_blocked.returncode != 0

    # With override: should pass
    env = dict(_os.environ)
    env["CODING_RAILS_OPERATOR_SCOPE_UPDATE"] = "1"
    result_allowed = subprocess.run(
        [sys.executable, str(RULE)],
        cwd=str(tmp_repo),
        capture_output=True,
        text=True,
        env=env,
    )
    assert result_allowed.returncode == 0, (
        f"override should permit operator-driven update: {result_allowed.stderr}"
    )


# ---- v0.6.0: scope_enforcement config ----

def test_scope_check_disabled_via_config(tmp_repo, make_branch, ledger_factory):
    """scope_enforcement.enabled=false skips the rule entirely."""
    import pytest as _pytest
    _pytest.importorskip("yaml")
    cfg_dir = tmp_repo / ".agent"
    cfg_dir.mkdir(exist_ok=True)
    (cfg_dir / "coding-rails.config.yml").write_text(
        "scope_enforcement:\n  enabled: false\n", encoding="utf-8"
    )
    branch = "agent/test/20260512-disabled"
    make_branch(branch)
    ledger_factory(branch=branch, allowed_paths=["src/foo.py"], task_id="20260512-disabled")
    # Out-of-scope edit
    (tmp_repo / "evil.py").write_text("oops", encoding="utf-8")
    result = _run_status(tmp_repo)
    # Disabled → exit 0 regardless of drift
    assert result.returncode == 0, result.stderr


def test_scope_check_per_project_bookkeeping_paths(tmp_repo, make_branch, ledger_factory):
    """scope_enforcement.bookkeeping_paths extends auto-allowed globs."""
    import pytest as _pytest
    _pytest.importorskip("yaml")
    cfg_dir = tmp_repo / ".agent"
    cfg_dir.mkdir(exist_ok=True)
    (cfg_dir / "coding-rails.config.yml").write_text(
        "scope_enforcement:\n"
        "  bookkeeping_paths:\n"
        '    - "docs/*.md"\n',
        encoding="utf-8",
    )
    branch = "agent/test/20260512-bk"
    make_branch(branch)
    ledger_factory(branch=branch, allowed_paths=["src/foo.py"], task_id="20260512-bk")
    # docs/*.md not in allowed_paths, but added to bookkeeping_paths
    (tmp_repo / "docs").mkdir()
    (tmp_repo / "docs" / "notes.md").write_text("notes", encoding="utf-8")
    result = _run_status(tmp_repo)
    assert result.returncode == 0, (
        f"per-project bookkeeping path should be auto-allowed: {result.stderr}"
    )
