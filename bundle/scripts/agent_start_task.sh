#!/usr/bin/env bash
# coding-rails — agent_start_task.sh
#
# Initialize a new coding-agent task:
#   1. Refuse if working tree is dirty
#   2. Fetch origin
#   3. Switch to a fresh agent branch off origin/main (or specified base)
#   4. Write the task ledger to .agent/tasks/<task_id>.json
#
# Usage:
#   ./scripts/coding-rails/agent_start_task.sh <task_id> --paths "<comma-list>"
#                                              [--base <ref>] [--agent <name>]
#                                              [--summary "<one line>"]
#
# Example:
#   ./scripts/coding-rails/agent_start_task.sh 20260512-pool-pump-fix \
#       --paths "agents/pool.py,tests/test_pool.py" \
#       --summary "investigate pool pump cycle interruption at 03:14"
#
# task_id format: <YYYYMMDD>-<short-slug>  (no agent name; that goes in --agent)
# Branch created: agent/<agent>/<task_id>

set -euo pipefail

REPO="$(git rev-parse --show-toplevel)"
TASKS_DIR="${REPO}/.agent/tasks"

# ---- arg parsing ----
TASK_ID=""
PATHS=""
BASE="origin/main"
AGENT_NAME=""
SUMMARY=""

while [ $# -gt 0 ]; do
  case "$1" in
    --paths)   PATHS="$2"; shift 2 ;;
    --base)    BASE="$2"; shift 2 ;;
    --agent)   AGENT_NAME="$2"; shift 2 ;;
    --summary) SUMMARY="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# //;s/^#//'
      exit 0
      ;;
    --*)
      echo "Unknown flag: $1" >&2; exit 1 ;;
    *)
      if [ -z "${TASK_ID}" ]; then
        TASK_ID="$1"
      else
        echo "Unexpected positional arg: $1" >&2; exit 1
      fi
      shift ;;
  esac
done

if [ -z "${TASK_ID}" ]; then
  echo "ERROR: task_id is required. Usage: agent_start_task.sh <task_id> --paths <list>" >&2
  exit 1
fi
if [ -z "${PATHS}" ]; then
  echo "ERROR: --paths is required. Example: --paths 'agents/pool.py,tests/test_pool.py'" >&2
  exit 1
fi
if [ -z "${AGENT_NAME}" ]; then
  AGENT_NAME="${CODING_RAILS_AGENT:-$(whoami 2>/dev/null || echo unknown)}"
fi

# task_id format check (YYYYMMDD-slug)
if ! [[ "${TASK_ID}" =~ ^[0-9]{8}-[a-z0-9_-]+$ ]]; then
  echo "ERROR: task_id must match YYYYMMDD-slug, got '${TASK_ID}'" >&2
  echo "  example: 20260512-pool-pump-fix" >&2
  exit 1
fi

BRANCH="agent/${AGENT_NAME}/${TASK_ID}"
LEDGER="${TASKS_DIR}/${TASK_ID}.json"

# ---- preflight ----
echo "== coding-rails agent_start_task =="
echo "  task_id : ${TASK_ID}"
echo "  agent   : ${AGENT_NAME}"
echo "  branch  : ${BRANCH}"
echo "  base    : ${BASE}"
echo "  ledger  : ${LEDGER#${REPO}/}"

# Clean tree
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "" >&2
  echo "ERROR: working tree is dirty. Commit, stash, or revert before starting a new task." >&2
  git status --short >&2
  exit 2
fi

# Untracked-files warning (don't block; agents may legitimately have local config)
untracked="$(git ls-files --others --exclude-standard)"
if [ -n "${untracked}" ]; then
  echo "  NOTE: untracked files present (allowed but visible):"
  printf '    %s\n' ${untracked}
fi

# Existing ledger collision
if [ -f "${LEDGER}" ]; then
  echo "" >&2
  echo "ERROR: ledger already exists at ${LEDGER#${REPO}/}" >&2
  echo "  Choose a different task_id, or finish/supersede the existing task first." >&2
  exit 3
fi

# Existing branch collision
if git show-ref --verify --quiet "refs/heads/${BRANCH}"; then
  echo "" >&2
  echo "ERROR: branch ${BRANCH} already exists locally." >&2
  exit 4
fi

# ---- fetch + branch ----
echo ""
echo "Fetching origin..."
git fetch origin --prune

base_sha="$(git rev-parse "${BASE}")"
if [ -z "${base_sha}" ]; then
  echo "ERROR: could not resolve base ref '${BASE}'" >&2
  exit 5
fi

echo "Creating branch ${BRANCH} at ${base_sha:0:10}..."
git switch -c "${BRANCH}" "${base_sha}" >/dev/null

# ---- write ledger ----
mkdir -p "${TASKS_DIR}"
started_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

# Convert comma-separated paths to JSON array
paths_json="$(printf '%s' "${PATHS}" \
  | tr ',' '\n' \
  | sed 's/^ *//;s/ *$//' \
  | grep -v '^$' \
  | python3 -c 'import json,sys; print(json.dumps([line.strip() for line in sys.stdin]))')"

cat >"${LEDGER}" <<EOF
{
  "task_id": "${TASK_ID}",
  "agent": "${AGENT_NAME}",
  "session": "$(date -u +%s)-${TASK_ID}",
  "started_at": "${started_at}",
  "branch": "${BRANCH}",
  "base_ref": "${BASE}",
  "base_sha": "${base_sha}",
  "allowed_paths": ${paths_json},
  "status": "in_progress",
  "summary": $(printf '%s' "${SUMMARY:-(none provided)}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')
}
EOF

echo ""
echo "+ ${LEDGER#${REPO}/}"
echo ""
echo "Ready. Make your changes, then:"
echo "  ./scripts/coding-rails/agent_finish_task.sh"
