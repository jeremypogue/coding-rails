#!/usr/bin/env bash
# coding-rails install.sh — portable coding-agent enforcement bundle
#
# Usage:
#   ./install.sh                       Install into the current directory
#   ./install.sh --setup-github        Also configure GitHub branch protection
#   ./install.sh --force               Overwrite existing files (default: skip)
#   ./install.sh --dry-run             Show what would happen without doing it
#   ./install.sh --target=<path>       Install into a different directory
#
# Exit codes:
#   0  success
#   1  target not a git repo
#   2  target tree dirty (and --force not set)
#   3  gh CLI missing (and --setup-github requested)
#   4  copy conflict (and --force not set)
#   5  branch protection configuration failed

set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_DIR="${SOURCE_DIR}/bundle"
VERSION="$(cat "${SOURCE_DIR}/VERSION")"

TARGET=""
SETUP_GITHUB=0
FORCE=0
DRY_RUN=0

# ---------- arg parsing ----------
for arg in "$@"; do
  case "$arg" in
    --setup-github) SETUP_GITHUB=1 ;;
    --force)        FORCE=1 ;;
    --dry-run)      DRY_RUN=1 ;;
    --target=*)     TARGET="${arg#--target=}" ;;
    -h|--help)
      sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# //;s/^#//'
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

TARGET="${TARGET:-$PWD}"
TARGET="$(cd "$TARGET" 2>/dev/null && pwd)" || { echo "Target not found: $TARGET" >&2; exit 1; }

# ---------- verification ----------
say() { printf '  %s\n' "$*"; }
hdr() { printf '\n== %s ==\n' "$*"; }

hdr "coding-rails install"
say "Source : ${SOURCE_DIR}"
say "Target : ${TARGET}"
say "Version: ${VERSION}"
[ "$DRY_RUN" = "1" ] && say "Mode   : DRY RUN (no changes will be made)"

# 1. Target must be a git repo
if [ ! -d "${TARGET}/.git" ] && ! git -C "${TARGET}" rev-parse --git-dir >/dev/null 2>&1; then
  echo "ERROR: ${TARGET} is not a git repository." >&2
  exit 1
fi

# 2. Target tree should be clean
if [ "$FORCE" != "1" ] && [ "$DRY_RUN" != "1" ]; then
  if ! git -C "${TARGET}" diff --quiet || ! git -C "${TARGET}" diff --cached --quiet; then
    echo "ERROR: target working tree is dirty. Commit or stash, or re-run with --force." >&2
    git -C "${TARGET}" status --short
    exit 2
  fi
fi

# 3. gh check if --setup-github requested
if [ "$SETUP_GITHUB" = "1" ]; then
  if ! command -v gh >/dev/null 2>&1; then
    echo "ERROR: --setup-github requested but 'gh' is not installed." >&2
    exit 3
  fi
  if ! gh auth status >/dev/null 2>&1; then
    echo "ERROR: --setup-github requested but 'gh auth status' fails. Run 'gh auth login'." >&2
    exit 3
  fi
fi

# ---------- copy mapping ----------
# bundle/rules/         -> .agent/rules/
# bundle/hooks/         -> .githooks/
# bundle/workflows/     -> .github/workflows/
# bundle/scripts/       -> scripts/coding-rails/
# bundle/entry-templates/AGENTS.md.template  -> AGENTS.md       (skip if present)
# bundle/entry-templates/CLAUDE.md.template  -> CLAUDE.md       (skip if present)
# bundle/entry-templates/.clinerules.template/ -> .clinerules/  (skip if present)
# bundle/tests/         -> tests/coding_rails/

copy_dir() {
  local src="$1" dst="$2" mode="${3:-merge}"
  [ -d "$src" ] || return 0
  if [ "$DRY_RUN" = "1" ]; then
    say "[dry-run] would copy: $src/ -> $dst/  (mode=$mode)"
    return 0
  fi
  mkdir -p "$dst"
  # mode=merge   : overwrite always (used for bundle-owned files)
  # mode=skip    : copy file only if missing (used for entry templates)
  if [ "$mode" = "skip" ]; then
    (cd "$src" && find . -type f) | while read -r f; do
      f="${f#./}"
      if [ ! -e "${dst}/${f}" ]; then
        mkdir -p "$(dirname "${dst}/${f}")"
        cp "${src}/${f}" "${dst}/${f}"
        say "+ ${dst#${TARGET}/}/${f}"
      else
        say ". ${dst#${TARGET}/}/${f}  (kept existing)"
      fi
    done
  else
    cp -R "${src}/." "${dst}/"
    say "+ ${dst#${TARGET}/}/  (recursive)"
  fi
}

copy_template_file() {
  local src="$1" dst="$2"
  [ -f "$src" ] || return 0
  if [ -f "$dst" ] && [ "$FORCE" != "1" ]; then
    say ". $(basename "$dst")  (kept existing)"
    return 0
  fi
  if [ "$DRY_RUN" = "1" ]; then
    say "[dry-run] would copy: $src -> $dst"
    return 0
  fi
  cp "$src" "$dst"
  say "+ $(basename "$dst")"
}

hdr "copying bundle"
copy_dir "${BUNDLE_DIR}/rules"      "${TARGET}/.agent/rules"            merge
copy_dir "${BUNDLE_DIR}/hooks"      "${TARGET}/.githooks"               merge
copy_dir "${BUNDLE_DIR}/workflows"  "${TARGET}/.github/workflows"       merge
copy_dir "${BUNDLE_DIR}/scripts"    "${TARGET}/scripts/coding-rails"    merge
copy_dir "${BUNDLE_DIR}/tests"      "${TARGET}/tests/coding_rails"      merge

hdr "copying entry templates (skip if already present)"
copy_template_file "${BUNDLE_DIR}/entry-templates/AGENTS.md.template"  "${TARGET}/AGENTS.md"
copy_template_file "${BUNDLE_DIR}/entry-templates/CLAUDE.md.template"  "${TARGET}/CLAUDE.md"
copy_dir           "${BUNDLE_DIR}/entry-templates/.clinerules.template" "${TARGET}/.clinerules" skip

# ---------- post-copy wiring ----------
hdr "wiring git config"
if [ "$DRY_RUN" != "1" ]; then
  git -C "${TARGET}" config core.hooksPath .githooks
  say "+ core.hooksPath = .githooks"

  mkdir -p "${TARGET}/.agent"
  echo "${VERSION}" > "${TARGET}/.agent/coding-rails-version.txt"
  say "+ .agent/coding-rails-version.txt = ${VERSION}"

  # Runtime state dir (self-ignoring). agent_finish_task.sh writes
  # awaiting_review status here; the committed ledger stays immutable.
  mkdir -p "${TARGET}/.agent/state"
  cat > "${TARGET}/.agent/state/.gitignore" <<'STATE_IGNORE_EOF'
# coding-rails runtime task state — per-machine, not tracked.
*
!.gitignore
STATE_IGNORE_EOF
  say "+ .agent/state/.gitignore (runtime state)"

  mkdir -p "${TARGET}/.agent/precommit-markers"
  cat > "${TARGET}/.agent/precommit-markers/.gitignore" <<'MARKER_IGNORE_EOF'
*
!.gitignore
MARKER_IGNORE_EOF
  cat > "${TARGET}/.agent/.gitignore" <<'AGENT_IGNORE_EOF'
# Runtime artifacts written by coding-rails hooks. Per-machine, not tracked.
precommit.log
self-audits/
AGENT_IGNORE_EOF
  say "+ .agent/.gitignore (runtime artifacts)"

  # ensure hook files are executable
  find "${TARGET}/.githooks" -type f -exec chmod +x {} \; 2>/dev/null || true
  find "${TARGET}/scripts/coding-rails" -type f \( -name '*.sh' -o -name '*.py' \) -exec chmod +x {} \; 2>/dev/null || true
else
  say "[dry-run] would set core.hooksPath = .githooks"
fi

# ---------- GitHub branch protection ----------
if [ "$SETUP_GITHUB" = "1" ]; then
  hdr "configuring GitHub branch protection"
  remote_url="$(git -C "${TARGET}" remote get-url origin 2>/dev/null || true)"
  if [ -z "$remote_url" ]; then
    echo "ERROR: no 'origin' remote configured in target." >&2
    exit 5
  fi

  # Parse owner/repo from ssh or https URL
  owner_repo="$(echo "$remote_url" \
    | sed -E 's|^git@github.com:||; s|^https://github.com/||; s|\.git$||')"

  if [ -z "$owner_repo" ]; then
    echo "ERROR: could not parse owner/repo from remote URL: $remote_url" >&2
    exit 5
  fi

  # Detect default branch
  default_branch="$(gh api "repos/${owner_repo}" --jq '.default_branch' 2>/dev/null || echo main)"
  say "Target repo : ${owner_repo}"
  say "Branch      : ${default_branch}"

  if [ "$DRY_RUN" = "1" ]; then
    say "[dry-run] would PUT repos/${owner_repo}/branches/${default_branch}/protection"
  else
    # Required status checks include the two CI workflows the bundle ships.
    # If a project does not yet have those workflows wired into CI, the
    # protection setup tolerates missing checks (they will start enforcing
    # once the workflows run for the first time).
    response="$(mktemp)"
    http_code="$(gh api "repos/${owner_repo}/branches/${default_branch}/protection" \
      --method PUT \
      --header "Accept: application/vnd.github+json" \
      --raw-field "required_status_checks[strict]=true" \
      --raw-field "required_status_checks[contexts][]=agent-task-gates" \
      --raw-field "required_status_checks[contexts][]=agent-rules-check" \
      --raw-field "enforce_admins=false" \
      --raw-field "required_pull_request_reviews[required_approving_review_count]=1" \
      --raw-field "required_pull_request_reviews[require_code_owner_reviews]=true" \
      --raw-field "required_pull_request_reviews[dismiss_stale_reviews]=true" \
      --raw-field "restrictions=" \
      --raw-field "allow_force_pushes=false" \
      --raw-field "allow_deletions=false" \
      --raw-field "required_linear_history=true" \
      --raw-field "block_creations=false" \
      >"${response}" 2>&1 && echo 200 || echo "$?")"

    if [ "${http_code}" = "200" ]; then
      say "+ branch protection applied to ${default_branch}"
    elif grep -q "Upgrade to GitHub Pro" "${response}" 2>/dev/null \
      || grep -q "make this repository public" "${response}" 2>/dev/null; then
      cat <<EOF >&2

  NOTE: GitHub branch protection is not available on this repository.
  Free-tier private repos cannot use branch protection without GitHub Pro.

  The bundle is still installed and fully functional:
    - Local pre-commit / pre-push hooks enforce rules at the developer machine.
    - CI workflows still run on every push and PR; status is visible.
    - You (as sole approver) are the floor: do not click merge on red CI,
      and do not push directly to the protected branch.

  To get the server-side floor, either:
    - upgrade the repo to GitHub Pro (\$4/mo), OR
    - make the repository public, OR
    - self-host on Gitea/Forgejo where protection is free.

EOF
      say "+ branch protection skipped (Free private repo); hooks + CI still active"
    else
      echo "ERROR: branch protection update failed. Response:" >&2
      cat "${response}" >&2
      rm -f "${response}"
      exit 5
    fi
    rm -f "${response}"
  fi
fi

# ---------- summary ----------
hdr "done"
say "coding-rails ${VERSION} installed at ${TARGET}"
say ""
say "Next steps:"
say "  1. Review the staged changes (\`git -C ${TARGET} status\`)"
say "  2. Commit and push as the bootstrap PR for this project"
say "  3. New coding-agent sessions should run:"
say "       ./scripts/coding-rails/agent_start_task.sh <task-id> --paths <list>"
say ""
if [ "$SETUP_GITHUB" != "1" ]; then
  say "(GitHub branch protection NOT configured — re-run with --setup-github when ready)"
fi
