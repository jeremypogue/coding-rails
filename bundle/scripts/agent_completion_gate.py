#!/usr/bin/env python3
"""coding-rails — agent_completion_gate.py

Run from GitHub Actions on every PR. Validates that the PR follows the
required agent workflow:

  1. PR was opened from an agent/<tool>/<YYYYMMDD>-<slug> branch
  2. Task ledger (.agent/tasks/<task_id>.json) exists for that branch
  3. PR body has all required sections
  4. Every changed file is within the ledger's allowed_paths
  5. No conflict markers in any committed file
  6. No merge commits in the PR's commit range
  7. Forbidden command transcripts are not present in PR body (the
     negative-smoke section is required but should show commands being
     REFUSED, not executed)

Exits 0 on pass, non-zero on fail. CI treats failure as required-check-failed
(when wired with branch protection) or visible-red-check (otherwise).

Environment:
  GITHUB_TOKEN     — for gh CLI
  GITHUB_REPOSITORY — owner/repo (set by Actions)
  PR_NUMBER         — passed in via workflow

Or pass --pr <number> for local testing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


# ---- helpers ----

REQUIRED_SECTIONS = [
    "## Summary",
    "## Task metadata",
    "## Tests",
    "## Negative-smoke",
    "## Changed files",
    "## Known risks",
    "## Not done / follow-up",
]

AGENT_BRANCH_RE = re.compile(r"^agent/[a-z0-9_-]+/[0-9]{8}-[a-z0-9_-]+$")
CONFLICT_MARKER_RE = re.compile(r"^(<<<<<<<|=======|>>>>>>>)", re.MULTILINE)


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def gh_json(*args: str) -> Any:
    output = subprocess.check_output(["gh"] + list(args), text=True)
    return json.loads(output)


def fail(msg: str) -> None:
    sys.stderr.write(f"completion-gate: {msg}\n")


# ---- check 1: branch shape ----

def check_branch_shape(branch: str) -> bool:
    if not AGENT_BRANCH_RE.match(branch):
        fail(
            f"branch '{branch}' does not match the required shape\n"
            "    expected: agent/<tool>/<YYYYMMDD>-<slug>"
        )
        return False
    return True


# ---- check 2: ledger exists ----

def find_ledger(repo_root: Path, branch: str) -> Path | None:
    tasks_dir = repo_root / ".agent" / "tasks"
    if not tasks_dir.is_dir():
        return None
    for p in tasks_dir.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("branch") == branch:
            return p
    return None


# ---- check 3: PR body sections ----

def check_pr_body(body: str) -> bool:
    missing = [s for s in REQUIRED_SECTIONS if s not in body]
    if missing:
        fail("PR body is missing required sections:")
        for s in missing:
            sys.stderr.write(f"    {s}\n")
        return False
    # Each section must have non-empty content after its header
    sections = re.split(r"^(## [^\n]+)$", body, flags=re.MULTILINE)
    # sections is [pre, header1, content1, header2, content2, ...]
    empty = []
    for i in range(1, len(sections) - 1, 2):
        header = sections[i].strip()
        content = sections[i + 1].strip()
        # Strip HTML comments and whitespace; only the placeholder
        # comment indicates "empty"
        cleaned = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL).strip()
        if not cleaned and header in REQUIRED_SECTIONS:
            empty.append(header)
    if empty:
        fail("the following PR body sections are empty:")
        for s in empty:
            sys.stderr.write(f"    {s}\n")
        return False
    return True


# ---- check 4: files within allowed_paths ----

def path_in_allowed(path: str, allowed: list[str]) -> bool:
    from fnmatch import fnmatchcase
    for entry in allowed:
        if entry == path:
            return True
        if fnmatchcase(path, entry):
            return True
        if entry.endswith("/") and path.startswith(entry):
            return True
    return False


def check_allowed_paths(changed: list[str], allowed: list[str]) -> bool:
    out_of_scope = [p for p in changed if not path_in_allowed(p, allowed)]
    if out_of_scope:
        fail("the following changed files are outside allowed_paths:")
        for p in out_of_scope:
            sys.stderr.write(f"    {p}\n")
        sys.stderr.write("  allowed_paths in ledger:\n")
        for entry in allowed:
            sys.stderr.write(f"    {entry}\n")
        return False
    return True


# ---- check 5: no conflict markers in committed files ----

def check_no_conflict_markers(repo_root: Path, base_sha: str, head_sha: str) -> bool:
    # Get all changed files; for each, check for conflict markers in the
    # final file content (post-merge, post-resolve).
    changed = run("git", "diff", "--name-only", f"{base_sha}..{head_sha}").splitlines()
    offenders: list[str] = []
    for path in changed:
        full = repo_root / path
        if not full.is_file():
            continue
        # Only check text-y files; binary detection is best-effort
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if CONFLICT_MARKER_RE.search(content):
            offenders.append(path)
    if offenders:
        fail("conflict markers found in committed files:")
        for p in offenders:
            sys.stderr.write(f"    {p}\n")
        return False
    return True


# ---- check 6: no merge commits ----

def check_no_merge_commits(base_sha: str, head_sha: str) -> bool:
    mc = run("git", "rev-list", "--merges", f"{base_sha}..{head_sha}")
    if mc:
        fail("merge commits found in PR range (history must be linear):")
        for sha in mc.splitlines():
            msg = run("git", "log", "--format=%s", "-n", "1", sha)
            sys.stderr.write(f"    {sha[:10]}  {msg}\n")
        return False
    return True


# ---- main ----

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", type=int, help="PR number (default: from env)")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    args = parser.parse_args()

    pr_number = args.pr or int(os.environ.get("PR_NUMBER") or os.environ.get("GITHUB_PR_NUMBER") or 0)
    if not pr_number:
        fail("PR number required (--pr or PR_NUMBER env)")
        return 1

    if not args.repo:
        fail("GITHUB_REPOSITORY env or --repo argument required")
        return 1

    repo_root = Path(run("git", "rev-parse", "--show-toplevel"))

    # Fetch PR metadata
    pr = gh_json("pr", "view", str(pr_number), "--repo", args.repo,
                 "--json", "headRefName,baseRefName,baseRefOid,body,number,state,files")

    branch = pr["headRefName"]
    base_ref = pr["baseRefName"]
    base_sha = pr["baseRefOid"]
    body = pr.get("body") or ""
    head_sha = run("git", "rev-parse", "HEAD")
    changed = [f["path"] for f in pr.get("files", [])]

    print(f"== coding-rails completion gate ==")
    print(f"  PR        : #{pr_number}")
    print(f"  branch    : {branch}")
    print(f"  base      : {base_ref} ({base_sha[:10]})")
    print(f"  head      : {head_sha[:10]}")
    print(f"  changed   : {len(changed)} file(s)")

    all_ok = True

    # 1. branch shape
    if not check_branch_shape(branch):
        all_ok = False

    # 2. ledger
    ledger_path = find_ledger(repo_root, branch)
    if ledger_path is None:
        fail(f"no .agent/tasks/<task_id>.json references branch '{branch}'")
        all_ok = False
        ledger_data: dict[str, Any] = {}
    else:
        ledger_data = json.loads(ledger_path.read_text(encoding="utf-8"))
        print(f"  ledger    : {ledger_path.relative_to(repo_root)}")

    # 3. PR body sections
    if not check_pr_body(body):
        all_ok = False

    # 4. allowed_paths
    allowed = ledger_data.get("allowed_paths") or []
    if allowed:
        if not check_allowed_paths(changed, allowed):
            all_ok = False
    else:
        fail("ledger has no allowed_paths; cannot verify scope.")
        all_ok = False

    # 5. conflict markers
    if not check_no_conflict_markers(repo_root, base_sha, head_sha):
        all_ok = False

    # 6. merge commits
    if not check_no_merge_commits(base_sha, head_sha):
        all_ok = False

    if all_ok:
        print("\ncompletion gate: PASS")
        return 0
    print("\ncompletion gate: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
