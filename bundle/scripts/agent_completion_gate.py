#!/usr/bin/env python3
"""coding-rails — agent_completion_gate.py

Run from GitHub Actions on every PR. Validates that the PR follows the
required agent workflow:

  1. PR was opened from an agent/<tool>/<YYYYMMDD>-<slug> branch.
  2. Task ledger (.agent/tasks/<task_id>.json) exists for that branch.
  3. PR body has all required sections AND each section has non-empty
     non-comment content.
  4. Every changed file is within the ledger's allowed_paths.
  5. No conflict markers in any committed file.
  6. No merge commits in the PR's commit range.
  7. Ledger base_sha is reachable from the PR's base or its commit
     range (i.e. ledger has not been rebased onto a stale base).
  8. Every commit message in the PR range that contains a completion
     phrase ('verified', 'shipped', 'confirmed', 'tested', 'smoked')
     also contains at least one evidence reference.
  9. If allowed_paths was modified by this PR, the new list is a
     subset of the base's list (no agent-driven scope expansion).

NOTE: The "negative-smoke" section is checked for presence and
non-empty content only. Semantic verification that the transcript
actually shows commands being refused is operator-judgement at PR
review time — this gate does not parse the transcript content.

Exits 0 on pass, non-zero on fail. CI treats failure as
required-check-failed (when wired with branch protection) or
visible-red-check (otherwise).

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

# Slug allows dots so version numbers / CVE identifiers work:
#   agent/claude/20260512-upgrade-coding-rails-v0.2.0
AGENT_BRANCH_RE = re.compile(r"^agent/[a-z0-9_-]+/[0-9]{8}-[a-z0-9._-]+$")
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


def check_allowed_paths(
    changed: list[str],
    allowed: list[str],
    bookkeeping: set[str] | None = None,
) -> bool:
    """Verify every changed file is within allowed_paths OR is a bookkeeping
    path. Bookkeeping paths (the task ledger itself, the test-coverage
    exceptions file) are auto-allowed without explicit declaration —
    mirrors the pre-commit rule 001 behavior so the workflow can ship a
    task without manually listing its own ledger."""
    bookkeeping = bookkeeping or set()
    out_of_scope = [
        p for p in changed
        if p not in bookkeeping and not path_in_allowed(p, allowed)
    ]
    if out_of_scope:
        fail("the following changed files are outside allowed_paths:")
        for p in out_of_scope:
            sys.stderr.write(f"    {p}\n")
        sys.stderr.write("  allowed_paths in ledger:\n")
        for entry in allowed:
            sys.stderr.write(f"    {entry}\n")
        if bookkeeping:
            sys.stderr.write("  bookkeeping paths (auto-allowed):\n")
            for entry in sorted(bookkeeping):
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


# ---- check 7: ledger base_sha is reachable ----

def check_base_sha_reachable(ledger_base_sha: str, base_sha: str, head_sha: str) -> bool:
    """The ledger records the base SHA at task-start time. That SHA must
    still be an ancestor of the PR's base OR appear inside the PR's
    commit range. Otherwise the branch has been rebased onto a stale
    or alien base and the ledger no longer describes reality."""
    if not ledger_base_sha or ledger_base_sha == "auto-resolved-at-start":
        # Older ledgers / manual ledgers may have a placeholder. Skip
        # with a notice rather than fail.
        sys.stderr.write(
            "  NOTE: ledger base_sha is placeholder; skipping reachability check.\n"
        )
        return True
    # Is ledger_base_sha an ancestor of base_sha OR head_sha?
    try:
        subprocess.check_call(
            ["git", "merge-base", "--is-ancestor", ledger_base_sha, base_sha],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except subprocess.CalledProcessError:
        pass
    try:
        subprocess.check_call(
            ["git", "merge-base", "--is-ancestor", ledger_base_sha, head_sha],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except subprocess.CalledProcessError:
        pass
    fail(
        f"ledger base_sha {ledger_base_sha[:10]} is not reachable from the PR base "
        f"({base_sha[:10]}) or head ({head_sha[:10]}).\n"
        "  The branch was likely rebased onto a different base than the ledger declares.\n"
        "  Either restart the task from origin/main, or have the operator update the ledger."
    )
    return False


# ---- check 8: commit-msg evidence scan ----

COMPLETION_RE = re.compile(
    r"(?i)\b(verified|shipped|confirmed|tested|smoked?)\b"
)

EVIDENCE_REGEXES = [
    re.compile(r"(?i)https?://"),
    re.compile(r"(?i)evidence:\s*\S{10,}"),
    re.compile(r"(?i)pytest\b.*\bpassed\b"),
    re.compile(r"(?i)screenshot:\s*\S+"),
    re.compile(r"(?i)logbook:\s*\S+"),
    re.compile(r"(?i)telegram:\s*\S+"),
    re.compile(r"(?i)sms:\s*\S+"),
    re.compile(r"(?i)physical-check:\s*\S+"),
    re.compile(r"(?i)event_log:\s*\S+"),
    re.compile(r"(?i)\.agent/tasks/\S+\.json"),
]


def _strip_comments(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def check_commit_msg_evidence(base_sha: str, head_sha: str) -> bool:
    """For each commit in the PR range, if its message contains a
    completion claim phrase, require at least one evidence-pattern
    match in the same message. Mirrors rule 008 enforcement at PR time
    in case the local commit-msg hook was bypassed."""
    raw = run(
        "git", "log", "--format=%H%x00%B%x00END%x00",
        f"{base_sha}..{head_sha}",
    )
    if not raw:
        return True

    failures: list[tuple[str, str]] = []
    # Records are SHA \x00 message \x00 END \x00
    for record in raw.split("END\x00"):
        record = record.strip()
        if not record:
            continue
        parts = record.split("\x00", 1)
        if len(parts) != 2:
            continue
        sha, msg = parts[0].strip(), parts[1].strip()
        msg_clean = _strip_comments(msg)
        if not COMPLETION_RE.search(msg_clean):
            continue
        if any(p.search(msg_clean) for p in EVIDENCE_REGEXES):
            continue
        first_line = msg_clean.splitlines()[0][:60] if msg_clean else ""
        failures.append((sha[:10], first_line))

    if failures:
        fail(
            "the following commit(s) contain a completion phrase "
            "(verified/shipped/confirmed/tested/smoked) without an evidence "
            "reference:"
        )
        for sha, line in failures:
            sys.stderr.write(f"    {sha}  {line}\n")
        sys.stderr.write(
            "  Each such commit needs an evidence ref in its message "
            "(URL, pytest output, evidence:, screenshot:, logbook:, etc.) "
            "or rephrase to not claim completion.\n"
        )
        return False
    return True


# ---- check 9: allowed_paths-growth guard ----

def check_allowed_paths_not_expanded(
    repo_root: Path, ledger_rel_path: str, base_sha: str
) -> bool:
    """If allowed_paths was modified by this PR, verify the new list is
    a SUBSET of the base's list. Catches agent-driven scope expansion."""
    try:
        base_blob = subprocess.check_output(
            ["git", "show", f"{base_sha}:{ledger_rel_path}"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        # Ledger did not exist at base SHA — this is the first commit
        # creating the ledger. No comparison possible / needed.
        return True

    try:
        base_data = json.loads(base_blob)
    except json.JSONDecodeError:
        # Base ledger unreadable; skip with notice.
        sys.stderr.write(
            "  NOTE: base-SHA ledger unparseable; skipping scope-expansion check.\n"
        )
        return True

    base_allowed = set(base_data.get("allowed_paths") or [])
    head_data = json.loads((repo_root / ledger_rel_path).read_text(encoding="utf-8"))
    head_allowed = set(head_data.get("allowed_paths") or [])

    new_entries = head_allowed - base_allowed
    if new_entries:
        fail(
            "allowed_paths was expanded by this PR — agent-driven scope "
            "growth requires operator approval:"
        )
        for entry in sorted(new_entries):
            sys.stderr.write(f"    + {entry}\n")
        sys.stderr.write(
            "  Either remove the new entries (and the files that depend on them) "
            "or have the operator update the ledger in a separate commit.\n"
        )
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

    # 4. allowed_paths (with bookkeeping auto-allow — mirrors rule 001)
    allowed = ledger_data.get("allowed_paths") or []
    bookkeeping: set[str] = {".agent/test-coverage-exceptions.md"}
    if ledger_path is not None:
        bookkeeping.add(
            str(ledger_path.relative_to(repo_root)).replace("\\", "/")
        )
    if allowed:
        if not check_allowed_paths(changed, allowed, bookkeeping):
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

    # 7. base_sha reachability
    if ledger_data:
        ledger_base = ledger_data.get("base_sha", "")
        if not check_base_sha_reachable(ledger_base, base_sha, head_sha):
            all_ok = False

    # 8. commit-msg evidence scan
    if not check_commit_msg_evidence(base_sha, head_sha):
        all_ok = False

    # 9. allowed_paths-growth guard
    if ledger_path is not None:
        rel = str(ledger_path.relative_to(repo_root)).replace("\\", "/")
        if not check_allowed_paths_not_expanded(repo_root, rel, base_sha):
            all_ok = False

    if all_ok:
        print("\ncompletion gate: PASS")
        return 0
    print("\ncompletion gate: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
