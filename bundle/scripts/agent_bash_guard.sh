#!/usr/bin/env bash
# coding-rails — agent_bash_guard.sh
#
# Best-effort shell-level guard for coding-agent contexts. Sourced via
# BASH_ENV or directly (`source agent_bash_guard.sh`). Wraps `git` and `gh`
# so destructive invocations are intercepted by agent_git_guard.py before
# execution.
#
# This is harness-agnostic in the sense that it works for any agent that
# shells out via bash. It is bypassed by:
#   - agents that use git library bindings instead of the CLI
#   - agents that invoke git via absolute path (/usr/bin/git)
#   - agents using a different shell
#
# Therefore it is NOT load-bearing. The load-bearing enforcement is the
# commit/push hooks and the PR completion gate. This guard provides fast,
# in-line feedback for the most common case (agents using bash).
#
# Install:
#   1. Source this file from your shell:
#         source /path/to/coding-rails/scripts/agent_bash_guard.sh
#      (or set BASH_ENV=<path-to-this-file> in the agent harness env)
#   2. Agent harness should source it before exposing the shell to the
#      coding agent.

__CODING_RAILS_GUARD_PY=""

__coding_rails_find_guard() {
  local candidates=(
    "${CODING_RAILS_GUARD_PY:-}"
    "$(dirname "${BASH_SOURCE[0]:-}")/agent_git_guard.py"
    "${HOME}/coding-rails/bundle/scripts/agent_git_guard.py"
  )
  for c in "${candidates[@]}"; do
    [ -n "${c}" ] && [ -f "${c}" ] && { echo "${c}"; return 0; }
  done
  return 1
}

__CODING_RAILS_GUARD_PY="$(__coding_rails_find_guard || true)"

if [ -z "${__CODING_RAILS_GUARD_PY}" ]; then
  echo "coding-rails: agent_bash_guard sourced but agent_git_guard.py not found." >&2
  echo "  Set CODING_RAILS_GUARD_PY=/path/to/agent_git_guard.py" >&2
  return 0 2>/dev/null || exit 0
fi

__coding_rails_check() {
  local argv_json
  argv_json="$(python3 -c '
import json, sys
print(json.dumps(sys.argv[1:]))
' "$@")"
  python3 "${__CODING_RAILS_GUARD_PY}" --argv "${argv_json}"
}

git() {
  if ! __coding_rails_check git "$@"; then
    return 2
  fi
  command git "$@"
}

gh() {
  if ! __coding_rails_check gh "$@"; then
    return 2
  fi
  command gh "$@"
}

echo "coding-rails: agent_bash_guard active (git/gh wrapped)"
