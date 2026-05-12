#!/usr/bin/env bash
# coding-rails — agent_finish_task.sh
#
# Finalize a coding-agent task:
#   1. Refuse if task ledger missing or branch mismatch
#   2. Run all rule checks against staged + committed changes
#   3. Verify no conflict markers, no merge commits
#   4. Push the agent branch
#   5. Open a PR with the required body sections
#
# Usage:
#   ./scripts/coding-rails/agent_finish_task.sh [--draft] [--title "<title>"]
#
# Notes:
#   - This script does NOT commit. Commit your work yourself, then run this.
#   - It will refuse to push if the diff between origin/main and HEAD violates
#     any rule.
#   - The PR body is pre-populated; agent must edit-in-place to fill the
#     Summary / Tests / Known risks sections before the PR is submitted.

set -uo pipefail

REPO="$(git rev-parse --show-toplevel)"
TASKS_DIR="${REPO}/.agent/tasks"
RULES_DIR="${REPO}/scripts/coding-rails/rules"

DRAFT=0
TITLE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --draft) DRAFT=1; shift ;;
    --title) TITLE="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# //;s/^#//'
      exit 0 ;;
    *)
      echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

fail() { printf '\nagent_finish_task: %s\n' "$*" >&2; }

# ---- locate ledger ----
current_branch="$(git rev-parse --abbrev-ref HEAD)"
if ! [[ "${current_branch}" =~ ^agent/ ]]; then
  fail "current branch '${current_branch}' is not an agent branch."
  fail "  Start a task with agent_start_task.sh first."
  exit 1
fi

ledger=""
for f in "${TASKS_DIR}"/*.json; do
  [ -f "${f}" ] || continue
  b="$(python3 -c "import json; print(json.load(open(r'${f}', encoding='utf-8')).get('branch', ''))" 2>/dev/null || echo)"
  if [ "${b}" = "${current_branch}" ]; then
    ledger="${f}"
    break
  fi
done

if [ -z "${ledger}" ]; then
  fail "no task ledger references branch '${current_branch}'."
  fail "  Re-run agent_start_task.sh to create one, or check .agent/tasks/."
  exit 1
fi

task_id="$(python3 -c "import json; print(json.load(open(r'${ledger}', encoding='utf-8'))['task_id'])")"
base_ref="$(python3 -c "import json; print(json.load(open(r'${ledger}', encoding='utf-8')).get('base_ref', 'origin/main'))")"

echo "== coding-rails agent_finish_task =="
echo "  task_id : ${task_id}"
echo "  branch  : ${current_branch}"
echo "  ledger  : ${ledger#${REPO}/}"
echo "  base    : ${base_ref}"

# ---- preflight: must have at least one commit beyond base ----
git fetch origin --prune >/dev/null 2>&1 || true
ahead="$(git rev-list --count "${base_ref}..HEAD" 2>/dev/null || echo 0)"
if [ "${ahead}" -lt 1 ]; then
  fail "no commits between ${base_ref} and HEAD. Make at least one commit first."
  exit 2
fi
echo "  commits : ${ahead} ahead of ${base_ref}"

# Working tree must be clean (don't accidentally push dirty state)
if ! git diff --quiet || ! git diff --cached --quiet; then
  fail "working tree is dirty. Commit (or revert) before finishing the task."
  git status --short >&2
  exit 2
fi

# ---- conflict-marker scan in committed files ----
echo ""
echo "Scanning for conflict markers in committed range..."
if git diff "${base_ref}..HEAD" -G '^(<<<<<<<|=======|>>>>>>>)' --name-only \
    | grep -q .; then
  fail "conflict markers found in committed files:"
  git diff "${base_ref}..HEAD" -G '^(<<<<<<<|=======|>>>>>>>)' --name-only >&2
  exit 3
fi

# ---- merge-commit scan ----
echo "Scanning for merge commits in pushed range..."
mc="$(git rev-list --merges "${base_ref}..HEAD" 2>/dev/null || true)"
if [ -n "${mc}" ]; then
  fail "merge commits found in pushed range:"
  while IFS= read -r sha; do
    [ -z "${sha}" ] && continue
    msg="$(git log --format=%s -n 1 "${sha}")"
    fail "    ${sha:0:10}  ${msg}"
  done <<<"${mc}"
  fail "  Rebase to remove merge commits before finishing."
  exit 4
fi

# ---- run rule checks against the committed range ----
#
# The rule scripts inspect `git diff --cached --name-only`. On a clean
# working tree, nothing is staged, so they'd see no files and pass
# trivially — masking real violations that only appear in the committed
# range. Use a temp index to populate "staged" with the files in
# ${base_ref}..HEAD, run the rule scripts against that view, then drop
# the temp index. The real index is untouched.
if [ -d "${RULES_DIR}" ]; then
  echo "Running rule checks against ${base_ref}..HEAD..."
  tmp_index="$(mktemp -t coding-rails-index.XXXXXX 2>/dev/null || mktemp)"
  cleanup() {
    rm -f "${tmp_index}" 2>/dev/null || true
    unset GIT_INDEX_FILE
  }
  trap cleanup EXIT

  # Seed the temp index with the base SHA's tree, then stage the diff.
  GIT_INDEX_FILE="${tmp_index}" git read-tree "${base_ref}" 2>/dev/null || true

  changed="$(git diff --name-only --diff-filter=ACMR "${base_ref}..HEAD" 2>/dev/null || true)"
  while IFS= read -r f; do
    [ -z "${f}" ] && continue
    if [ -f "${f}" ]; then
      GIT_INDEX_FILE="${tmp_index}" git add -- "${f}" 2>/dev/null || true
    else
      # File was deleted in this range; record the deletion in the temp index
      GIT_INDEX_FILE="${tmp_index}" git rm --cached -- "${f}" 2>/dev/null || true
    fi
  done <<<"${changed}"

  rules_failed=0
  for check in "${RULES_DIR}"/*.py; do
    [ -f "${check}" ] || continue
    if ! GIT_INDEX_FILE="${tmp_index}" python3 "${check}"; then
      rules_failed=1
    fi
  done

  cleanup
  trap - EXIT

  if [ "${rules_failed}" -ne 0 ]; then
    fail "rule checks failed against ${base_ref}..HEAD; not pushing. Fix the issues above and re-run."
    exit 5
  fi
fi

# ---- push ----
echo ""
echo "Pushing ${current_branch} to origin..."
if ! git push -u origin "${current_branch}"; then
  fail "git push failed (see output above)."
  exit 6
fi

# ---- open PR ----
if ! command -v gh >/dev/null 2>&1; then
  fail "gh CLI not installed; branch pushed but PR not created."
  fail "  Install gh and run: gh pr create --base main --head ${current_branch}"
  exit 7
fi

pr_title="${TITLE}"
if [ -z "${pr_title}" ]; then
  pr_title="${task_id}"
fi

# Pre-fill PR body with required sections; the agent must edit it before
# submission. The CI completion-gate validates the section structure.
body_file="$(mktemp)"
allowed_paths_json="$(python3 -c "import json; print(json.dumps(json.load(open(r'${ledger}', encoding='utf-8')).get('allowed_paths', []), indent=2))")"

cat >"${body_file}" <<PRBODY
## Summary

<!-- one-paragraph summary of what this PR does -->

## Task metadata

- task_id: \`${task_id}\`
- branch: \`${current_branch}\`
- base: \`${base_ref}\`
- allowed_paths:
\`\`\`json
${allowed_paths_json}
\`\`\`

## Tests

<!-- exact commands run, with results -->

## Negative-smoke (command guard)

<!-- if the command guard is wired, paste the transcript of attempted
     destructive commands being refused; otherwise note "n/a (no guard
     wired for this harness)" -->

## Changed files

<!-- run \`git diff --name-only ${base_ref}..HEAD\` and paste -->

## Known risks

<!-- what could break, what to check post-merge -->

## Not done / follow-up

<!-- explicit list of deferred work -->

---

🤖 Generated by coding-rails agent_finish_task.sh
PRBODY

gh_args=(pr create --base "$(echo "${base_ref}" | sed 's|^origin/||')" --head "${current_branch}" --title "${pr_title}" --body-file "${body_file}")
if [ "${DRAFT}" = "1" ]; then
  gh_args+=(--draft)
fi

echo "Creating PR..."
pr_url="$(gh "${gh_args[@]}" 2>&1 | tail -1)"
rm -f "${body_file}"

if echo "${pr_url}" | grep -q "https://github.com"; then
  echo ""
  echo "+ PR opened: ${pr_url}"
  echo ""
  echo "Next step: edit the PR body to fill in Summary / Tests / etc."
  echo "  gh pr edit --body-file -  (paste edited body)"
else
  fail "gh pr create may have failed:"
  fail "  ${pr_url}"
  exit 8
fi

# Record runtime state (PR URL, awaiting-review status) in a SEPARATE
# state file. The committed task ledger is the immutable task
# declaration; runtime mutations go here and are gitignored.
python3 - <<EOF
import json, pathlib
state_dir = pathlib.Path(r"${REPO}/.agent/state")
state_dir.mkdir(parents=True, exist_ok=True)
state_path = state_dir / "${task_id}.json"
state = {
    "task_id": "${task_id}",
    "status": "awaiting_review",
    "pr_url": "${pr_url}",
    "branch": "${current_branch}",
    "updated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
}
state_path.write_text(json.dumps(state, indent=2), encoding='utf-8')
EOF

echo "  runtime state recorded: .agent/state/${task_id}.json"
echo ""
echo "Done. Operator reviews + merges."
