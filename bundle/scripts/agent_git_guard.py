#!/usr/bin/env python3
"""coding-rails — agent_git_guard.py

Decision engine for the best-effort command guard. Reads a single git or
gh invocation (argv list on stdin or via --argv flag) and decides whether
it is allowed or refused for a coding-agent context.

This is invoked by agent_bash_guard.sh (BASH_ENV / shell wrapper) before
the actual command runs. Refused commands exit non-zero with a structured
reason; allowed commands exit 0.

This guard is best-effort. It is bypassed by:
  - any agent that does not source agent_bash_guard.sh
  - any harness that uses git library bindings instead of the CLI
  - direct invocation of git via absolute path

It is therefore NOT load-bearing. The load-bearing enforcement is at the
commit/push hooks and PR completion gate. This guard's value is fast
feedback for the agents that DO use the shell — most do.

Allowed verbs (case-sensitive, after the executable name):
  git fetch · git switch (no -f/--detach to existing) · git add (no . / -A / *)
  git commit · git status · git log · git diff · git show · git rev-parse
  git rev-list · git ls-files · git remote · git config (get only)
  gh pr create · gh pr view · gh pr checks · gh pr list · gh repo view
  gh api  (read-only methods: GET, HEAD)

Refused verbs:
  git reset · git restore · git checkout (--ours/--theirs)
  git rebase · git merge · git clean · git stash
  git push --force · git push --force-with-lease
  git push <shared-branch>
  gh pr merge · gh pr close · gh pr ready
  Any command containing OPERATOR_REVIEWED_BYPASS=, OPERATOR_PUSH_TO_MAIN=
"""

from __future__ import annotations

import argparse
import json
import re
import sys


SHARED_BRANCH_PREFIXES = (
    "main", "master", "develop", "trunk",
    "release/", "hotfix/", "feature/",
    "claude-", "codex-", "codex/", "cline-",
    "stage-", "path-",
)

BYPASS_ENV_RE = re.compile(
    r"^(OPERATOR_REVIEWED_BYPASS|OPERATOR_PUSH_TO_MAIN|OPERATOR_BYPASS"
    r"|CODING_RAILS_OPERATOR_SCOPE_UPDATE|CODING_RAILS_SKIP_HEARTBEAT)="
)


def deny(reason: str) -> int:
    """Print a structured deny reason and exit 2."""
    sys.stderr.write("\ncoding-rails command guard: REFUSED\n")
    sys.stderr.write(f"  {reason}\n")
    return 2


def allow() -> int:
    return 0


def is_shared_branch(target: str) -> bool:
    target = target.strip()
    if target in {"main", "master", "develop", "trunk"}:
        return True
    for prefix in SHARED_BRANCH_PREFIXES:
        if target.startswith(prefix):
            return True
    return False


def check_git(argv: list[str]) -> int:
    if len(argv) < 2:
        return allow()

    # Strip leading `-c KEY=VALUE` config overrides and inspect them.
    # `git -c core.hooksPath=/dev/null commit` etc. is a way to disable
    # hooks per-command without modifying the repo config.
    cursor = 1
    while cursor < len(argv) - 1 and argv[cursor] == "-c":
        kv = argv[cursor + 1]
        key = kv.split("=", 1)[0].strip().lower()
        if key in {"core.hookspath", "core.hookpath"}:
            return deny(
                "`git -c core.hooksPath=...` is not allowed. It bypasses the "
                "hook chain per-command. Hooks are not optional in agent context."
            )
        cursor += 2

    if cursor >= len(argv):
        return allow()
    sub = argv[cursor]
    rest = argv[cursor + 1:]

    # ---- absolute refusals ----
    if sub in {"reset", "restore", "clean", "stash", "rebase", "merge"}:
        return deny(f"`git {sub}` is not allowed in agent context. "
                    "Resolve via fresh branch from origin/main, or ask the operator.")

    if sub == "checkout":
        if "--ours" in rest or "--theirs" in rest:
            return deny("`git checkout --ours/--theirs` (blanket conflict resolution) "
                        "is not allowed. Resolve conflicts file-by-file or restart from "
                        "a clean rebase.")
        # other checkouts are usually fine (switching files), but `checkout <branch>`
        # is replaced by `git switch` in modern git.
        return allow()

    if sub == "add":
        if rest and rest[0] in {".", "-A", "--all", "-u", "--update", "*"}:
            return deny("`git add . / -A / --all / -u / *` is not allowed. "
                        "Stage explicit file paths only — this prevents accidentally "
                        "staging files outside your task's allowed_paths.")
        return allow()

    if sub == "push":
        # Detect force pushes and bypass flags
        for token in rest:
            if token in {"--force", "-f", "--force-with-lease", "--force-with-lease="}:
                return deny("`git push --force` / `--force-with-lease` is not allowed.")
            if token.startswith("--force-with-lease="):
                return deny("`git push --force-with-lease=...` is not allowed.")
            if token in {"--no-verify", "-n"}:
                return deny(
                    "`git push --no-verify` is not allowed. It bypasses the pre-push "
                    "hook entirely. Operator-driven bypass is via explicit env var, "
                    "not a flag."
                )
        # Detect push target. Forms accepted:
        #   git push origin <branch>
        #   git push -u origin <branch>
        #   git push --set-upstream origin <branch>
        tokens = rest
        flags = {"-u", "--set-upstream"}
        positional: list[str] = []
        i = 0
        while i < len(tokens):
            t = tokens[i]
            if t in flags or t.startswith("--"):
                i += 1
                continue
            positional.append(t)
            i += 1
        # positional[0] = remote (often "origin"), positional[1] = branch or refspec
        if len(positional) >= 2:
            target = positional[1]
            # refspecs like local:remote
            if ":" in target:
                remote_part = target.split(":", 1)[1]
            else:
                remote_part = target
            if is_shared_branch(remote_part):
                return deny(f"`git push origin {remote_part}` to a shared branch is not "
                            f"allowed. Push to your agent branch instead.")
        return allow()

    return allow()


def check_gh(argv: list[str]) -> int:
    if len(argv) < 2:
        return allow()
    sub = argv[1]

    if sub == "pr":
        if len(argv) >= 3:
            sub2 = argv[2]
            if sub2 in {"merge", "close", "ready"}:
                return deny(f"`gh pr {sub2}` is not allowed in agent context. "
                            "The operator decides whether to merge / close / ready.")
        return allow()

    if sub == "api":
        # Allow only safe methods. Detect --method or -X
        method = "GET"
        for i, t in enumerate(argv[2:], start=2):
            if t in {"--method", "-X"} and i + 1 < len(argv):
                method = argv[i + 1].upper()
            elif t.startswith("--method="):
                method = t.split("=", 1)[1].upper()
            elif t.startswith("-X="):
                method = t.split("=", 1)[1].upper()
        if method not in {"GET", "HEAD"}:
            return deny(f"`gh api --method {method}` (write API) is not allowed in agent "
                        "context. Only GET/HEAD are permitted.")
        return allow()

    return allow()


def check_env(argv: list[str]) -> int:
    """Detect inline env-var bypass attempts like:
        OPERATOR_REVIEWED_BYPASS=1 git push ...
    Bash splits these into argv as VAR=value tokens at the start."""
    for t in argv:
        if "=" not in t:
            break  # stop at first non-VAR=value token
        if BYPASS_ENV_RE.match(t):
            return deny(f"inline bypass env-var detected ({t}). "
                        "Bypass requires explicit operator action, not agent action.")
    return allow()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--argv", help="JSON-encoded argv list (alternative to stdin)")
    args = parser.parse_args()

    if args.argv:
        try:
            argv = json.loads(args.argv)
        except json.JSONDecodeError as exc:
            sys.stderr.write(f"agent_git_guard: bad --argv ({exc})\n")
            return 1
    else:
        raw = sys.stdin.read()
        try:
            argv = json.loads(raw)
        except json.JSONDecodeError:
            argv = raw.split()

    if not isinstance(argv, list) or not argv:
        return allow()

    # Env-var bypass check applies to all commands
    rc = check_env(argv)
    if rc != 0:
        return rc

    exe = argv[0].rsplit("/", 1)[-1]
    if exe == "git":
        return check_git(argv)
    if exe == "gh":
        return check_gh(argv)

    return allow()


if __name__ == "__main__":
    sys.exit(main())
