"""Integration tests for install.sh.

Exercises the actual bash script against a fresh git repo, asserts the
expected file layout and git config state. Skipped if bash isn't
available (e.g. Windows without Git Bash).
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def test_install_refuses_non_git_target(installer, tmp_path):
    """install.sh should refuse a target that isn't a git repo."""
    non_git = tmp_path / "not_a_repo"
    non_git.mkdir()
    result = installer(non_git)
    assert result.returncode != 0
    assert "not a git repository" in result.stdout + result.stderr


def test_install_creates_expected_layout(installer, bash_repo):
    """Successful install creates the bundle's canonical paths."""
    result = installer(bash_repo)
    assert result.returncode == 0, f"install failed:\n{result.stdout}\n{result.stderr}"

    # Rules
    assert (bash_repo / ".agent" / "rules" / "index.md").is_file()
    assert (bash_repo / ".agent" / "rules" / "001-task-ledger.md").is_file()
    assert (bash_repo / ".agent" / "rules" / "004-test-coverage.md").is_file()
    assert (bash_repo / ".agent" / "rules" / "008-evidence-required.md").is_file()

    # Hooks
    assert (bash_repo / ".githooks" / "pre-commit").is_file()
    assert (bash_repo / ".githooks" / "pre-push").is_file()
    assert (bash_repo / ".githooks" / "post-commit").is_file()
    assert (bash_repo / ".githooks" / "commit-msg").is_file()

    # Workflows
    assert (bash_repo / ".github" / "workflows" / "agent-task-gates.yml").is_file()
    assert (bash_repo / ".github" / "workflows" / "agent-rules-check.yml").is_file()

    # Scripts
    scripts = bash_repo / "scripts" / "coding-rails"
    assert (scripts / "agent_start_task.sh").is_file()
    assert (scripts / "agent_finish_task.sh").is_file()
    assert (scripts / "agent_completion_gate.py").is_file()
    assert (scripts / "agent_bash_guard.sh").is_file()
    assert (scripts / "agent_git_guard.py").is_file()
    assert (scripts / "precommit_self_audit.sh").is_file()

    # Rule check scripts
    rules = scripts / "rules"
    assert (rules / "001_task_ledger.py").is_file()
    assert (rules / "004_test_coverage.py").is_file()
    assert (rules / "008_evidence_required.py").is_file()


def test_install_sets_core_hookspath(installer, bash_repo):
    result = installer(bash_repo)
    assert result.returncode == 0

    config = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"],
        cwd=str(bash_repo),
        capture_output=True,
        text=True,
    )
    assert config.stdout.strip() == ".githooks"


def test_install_writes_version_file(installer, bash_repo):
    result = installer(bash_repo)
    assert result.returncode == 0
    version_file = bash_repo / ".agent" / "coding-rails-version.txt"
    assert version_file.is_file()
    content = version_file.read_text(encoding="utf-8").strip()
    assert content  # has a version
    # Version should match a semver-ish shape
    parts = content.split(".")
    assert len(parts) >= 2
    assert parts[0].isdigit()


def test_install_creates_self_ignoring_state_dir(installer, bash_repo):
    """v0.2.0+ creates .agent/state/.gitignore that ignores everything inside."""
    result = installer(bash_repo)
    assert result.returncode == 0

    state_ignore = bash_repo / ".agent" / "state" / ".gitignore"
    assert state_ignore.is_file()
    content = state_ignore.read_text(encoding="utf-8")
    assert "*" in content
    assert "!.gitignore" in content


def test_install_creates_agent_gitignore(installer, bash_repo):
    """v0.2.0+ creates .agent/.gitignore for runtime artifacts."""
    result = installer(bash_repo)
    assert result.returncode == 0

    agent_ignore = bash_repo / ".agent" / ".gitignore"
    assert agent_ignore.is_file()
    content = agent_ignore.read_text(encoding="utf-8")
    assert "precommit.log" in content
    assert "self-audits" in content


def test_install_seeds_entry_pointers_if_absent(installer, bash_repo):
    """install seeds AGENTS.md and CLAUDE.md only if they don't exist."""
    result = installer(bash_repo)
    assert result.returncode == 0
    assert (bash_repo / "AGENTS.md").is_file()
    assert (bash_repo / "CLAUDE.md").is_file()


def test_install_preserves_existing_agents_md(installer, bash_repo):
    """If target already has AGENTS.md, install does NOT overwrite it."""
    existing = bash_repo / "AGENTS.md"
    existing.write_text("# my custom rules\n", encoding="utf-8")
    result = installer(bash_repo)
    assert result.returncode == 0
    # Content should be unchanged
    assert existing.read_text(encoding="utf-8") == "# my custom rules\n"


def test_install_dry_run_changes_nothing(installer, bash_repo):
    """--dry-run should show plans without creating files."""
    result = installer(bash_repo, "--dry-run")
    assert result.returncode == 0
    # No .agent/ should exist
    assert not (bash_repo / ".agent" / "rules").exists()
    assert not (bash_repo / ".githooks").exists()
    # Output should mention dry-run
    assert "dry-run" in (result.stdout + result.stderr).lower()


def test_install_refuses_dirty_tree(installer, bash_repo):
    """A dirty target tree should be refused (without --force)."""
    # Make the tree dirty
    (bash_repo / "scratch.txt").write_text("uncommitted", encoding="utf-8")
    subprocess.run(["git", "add", "scratch.txt"], cwd=str(bash_repo), check=True)

    result = installer(bash_repo)
    assert result.returncode != 0
    assert "dirty" in (result.stdout + result.stderr).lower()


def test_install_force_overrides_dirty(installer, bash_repo):
    """--force should proceed even with a dirty tree."""
    (bash_repo / "scratch.txt").write_text("uncommitted", encoding="utf-8")
    subprocess.run(["git", "add", "scratch.txt"], cwd=str(bash_repo), check=True)

    result = installer(bash_repo, "--force")
    assert result.returncode == 0


def test_install_is_idempotent(installer, bash_repo):
    """Running install twice should succeed both times (upgrade pattern).

    Uses --force on the second run since the tree is dirty (uncommitted
    install changes). Real upgrade flow commits between, but committing
    between exercises the hooks which is out of scope for this test.
    """
    r1 = installer(bash_repo)
    assert r1.returncode == 0, f"first install failed:\n{r1.stdout}\n{r1.stderr}"

    # Second install with --force to bypass the dirty-tree check
    r2 = installer(bash_repo, "--force")
    assert r2.returncode == 0, f"second install failed:\n{r2.stdout}\n{r2.stderr}"

    # Layout should still be intact
    assert (bash_repo / ".agent" / "rules" / "index.md").is_file()
    assert (bash_repo / ".githooks" / "pre-commit").is_file()
