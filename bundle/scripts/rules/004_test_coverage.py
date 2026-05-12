#!/usr/bin/env python3
"""coding-rails rule 004 — test coverage check.

Invoked by .githooks/pre-commit. For every staged source file matching the
configured "agent surface" glob, requires a paired test file matching the
configured "test path template" to also be staged.

Defaults:
    agent_surface_glob: "agents/*.py"
    test_path_template: "tests/test_{name}.py"

Override via .agent/coding-rails.config.yml (read with PyYAML if installed;
otherwise falls back to defaults).

Exits 0 on pass, non-zero on fail.
"""

from __future__ import annotations

import re
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


def load_config(repo_root: Path) -> dict:
    """Best-effort config load. If PyYAML is missing or no config file
    exists, returns defaults — the rule still fires with sensible
    defaults."""
    defaults = {
        "agent_surface_glob": "agents/*.py",
        "test_path_template": "tests/test_{name}.py",
        "exclude_patterns": ["agents/__init__.py", "agents/_*.py"],
    }
    cfg_path = repo_root / ".agent" / "coding-rails.config.yml"
    if not cfg_path.is_file():
        return defaults

    try:
        import yaml  # type: ignore
    except ImportError:
        sys.stderr.write(
            "rule 004 (test coverage): PyYAML not installed; using defaults.\n"
            "  Install with: pip install pyyaml\n"
        )
        return defaults

    try:
        loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        sys.stderr.write(
            f"rule 004 (test coverage): config parse error ({exc}); using defaults.\n"
        )
        return defaults

    out = dict(defaults)
    out.update({k: v for k, v in loaded.items() if k in defaults})
    return out


def fail(reason: str) -> None:
    sys.stderr.write(f"rule 004 (test coverage): {reason}\n")


def name_of(source_path: str, glob: str) -> str | None:
    """Extract the 'name' from a source path that matches the glob.
    For 'agents/*.py' applied to 'agents/horsemaster.py', returns 'horsemaster'.
    Returns None if path doesn't match the glob."""
    if not fnmatchcase(source_path, glob):
        return None
    stem = Path(source_path).stem
    return stem


def main() -> int:
    repo_root = find_repo_root()
    staged = staged_files(repo_root)
    if not staged:
        return 0

    cfg = load_config(repo_root)
    glob = cfg["agent_surface_glob"]
    template = cfg["test_path_template"]
    excludes: list[str] = cfg.get("exclude_patterns", [])

    # Find agent-surface files in the staged set
    missing: list[tuple[str, str]] = []  # (source_path, expected_test_path)
    staged_set = set(staged)

    for path in staged:
        if any(fnmatchcase(path, exc) for exc in excludes):
            continue
        name = name_of(path, glob)
        if name is None:
            continue
        expected_test = template.format(name=name)
        # Accept exact match OR any staged test that starts with the same prefix
        prefix = expected_test.rsplit(".", 1)[0]  # tests/test_horsemaster
        if expected_test in staged_set:
            continue
        if any(s.startswith(prefix) for s in staged_set if s != path):
            continue
        missing.append((path, expected_test))

    if missing:
        fail("the following staged source files have no paired test:")
        for src, expected in missing:
            sys.stderr.write(f"    {src}  →  expected staged {expected}\n")
        sys.stderr.write(
            "  Either stage the test file in the same commit, or document "
            "an exception in .agent/test-coverage-exceptions.md.\n"
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
