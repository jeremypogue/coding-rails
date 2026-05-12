"""Tests for agent_git_guard.py — destructive git/gh command refusal."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GUARD = PROJECT_ROOT / "bundle" / "scripts" / "agent_git_guard.py"


def guard(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GUARD), "--argv", json.dumps(argv)],
        capture_output=True,
        text=True,
    )


# ---- absolute refusals ----

def test_refuses_reset_hard():
    r = guard(["git", "reset", "--hard"])
    assert r.returncode != 0
    assert "REFUSED" in r.stderr or "not allowed" in r.stderr


def test_refuses_stash():
    r = guard(["git", "stash", "push"])
    assert r.returncode != 0


def test_refuses_clean():
    r = guard(["git", "clean", "-fd"])
    assert r.returncode != 0


def test_refuses_merge():
    r = guard(["git", "merge", "origin/main"])
    assert r.returncode != 0


def test_refuses_rebase():
    r = guard(["git", "rebase", "origin/main"])
    assert r.returncode != 0


def test_refuses_checkout_ours():
    r = guard(["git", "checkout", "--ours", "agents/pool.py"])
    assert r.returncode != 0


def test_refuses_checkout_theirs():
    r = guard(["git", "checkout", "--theirs", "agents/pool.py"])
    assert r.returncode != 0


def test_allows_normal_checkout_path():
    r = guard(["git", "checkout", "main", "agents/pool.py"])
    assert r.returncode == 0


# ---- add patterns ----

def test_refuses_add_dot():
    r = guard(["git", "add", "."])
    assert r.returncode != 0


def test_refuses_add_dash_A():
    r = guard(["git", "add", "-A"])
    assert r.returncode != 0


def test_refuses_add_all_flag():
    r = guard(["git", "add", "--all"])
    assert r.returncode != 0


def test_allows_add_explicit_path():
    r = guard(["git", "add", "agents/pool.py", "tests/test_pool.py"])
    assert r.returncode == 0


# ---- push refusals ----

def test_refuses_push_force():
    r = guard(["git", "push", "--force", "origin", "main"])
    assert r.returncode != 0


def test_refuses_push_force_short():
    r = guard(["git", "push", "-f", "origin", "feature-branch"])
    assert r.returncode != 0


def test_refuses_push_force_with_lease():
    r = guard(["git", "push", "--force-with-lease", "origin", "main"])
    assert r.returncode != 0


def test_refuses_push_no_verify():
    r = guard(["git", "push", "--no-verify", "origin", "agent/x/20260512-foo"])
    assert r.returncode != 0
    assert "no-verify" in r.stderr or "bypass" in r.stderr.lower()


def test_refuses_push_to_main():
    r = guard(["git", "push", "origin", "main"])
    assert r.returncode != 0


def test_refuses_push_to_master():
    r = guard(["git", "push", "origin", "master"])
    assert r.returncode != 0


def test_refuses_push_to_develop():
    r = guard(["git", "push", "origin", "develop"])
    assert r.returncode != 0


def test_refuses_push_to_shared_feature():
    r = guard(["git", "push", "origin", "feature/some-shared"])
    assert r.returncode != 0


def test_refuses_push_to_codex_branch():
    r = guard(["git", "push", "origin", "codex/foo"])
    assert r.returncode != 0


def test_refuses_push_to_path_a_branch():
    r = guard(["git", "push", "origin", "path-a-recovery"])
    assert r.returncode != 0


def test_allows_push_agent_branch():
    r = guard(["git", "push", "-u", "origin", "agent/claude/20260512-test"])
    assert r.returncode == 0


# ---- inline -c overrides ----

def test_refuses_c_core_hookspath():
    r = guard(["git", "-c", "core.hooksPath=/dev/null", "commit"])
    assert r.returncode != 0
    assert "hookspath" in r.stderr.lower() or "core.hooksPath" in r.stderr


def test_allows_other_c_overrides():
    r = guard(["git", "-c", "user.email=foo@bar", "commit"])
    assert r.returncode == 0


# ---- inline bypass env vars ----

def test_refuses_bypass_env_in_argv():
    r = guard(["OPERATOR_REVIEWED_BYPASS=1", "git", "push"])
    assert r.returncode != 0
    assert "OPERATOR_REVIEWED_BYPASS" in r.stderr


def test_refuses_bypass_push_to_main_env():
    r = guard(["OPERATOR_PUSH_TO_MAIN=1", "git", "push", "origin", "main"])
    assert r.returncode != 0


# ---- gh CLI ----

def test_refuses_gh_pr_merge():
    r = guard(["gh", "pr", "merge", "42"])
    assert r.returncode != 0


def test_refuses_gh_pr_close():
    r = guard(["gh", "pr", "close", "42"])
    assert r.returncode != 0


def test_refuses_gh_pr_ready():
    r = guard(["gh", "pr", "ready", "42"])
    assert r.returncode != 0


def test_allows_gh_pr_create():
    r = guard(["gh", "pr", "create", "--title", "x"])
    assert r.returncode == 0


def test_allows_gh_pr_view():
    r = guard(["gh", "pr", "view", "42"])
    assert r.returncode == 0


def test_refuses_gh_api_put():
    r = guard(["gh", "api", "repos/x/y", "--method", "PUT"])
    assert r.returncode != 0


def test_refuses_gh_api_delete():
    r = guard(["gh", "api", "repos/x/y", "-X", "DELETE"])
    assert r.returncode != 0


def test_allows_gh_api_get():
    r = guard(["gh", "api", "repos/x/y"])
    assert r.returncode == 0


# ---- allowed commands sanity ----

def test_allows_git_status():
    r = guard(["git", "status"])
    assert r.returncode == 0


def test_allows_git_log():
    r = guard(["git", "log", "--oneline"])
    assert r.returncode == 0


def test_allows_git_fetch():
    r = guard(["git", "fetch", "origin", "--prune"])
    assert r.returncode == 0


def test_allows_git_commit():
    r = guard(["git", "commit", "-m", "x"])
    assert r.returncode == 0


def test_allows_git_switch():
    r = guard(["git", "switch", "-c", "agent/test/20260512-foo"])
    assert r.returncode == 0
