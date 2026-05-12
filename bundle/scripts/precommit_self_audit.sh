#!/usr/bin/env bash
# coding-rails — precommit_self_audit.sh
#
# Optional re-grounding script. Run before `git push` to produce a
# permanent audit record proving the agent re-read the rules and
# justified each changed file. The audit's SHA-256 becomes a token that
# (when wired) gates the pre-push hook.
#
# This is a softer enforcement than the gate-based controls; it is
# valuable for solo-developer or trust-by-default workflows where a
# permanent justification log is more useful than a hard block.
#
# Usage:
#   ./scripts/coding-rails/precommit_self_audit.sh
#   # interactive: prompts for justification

set -uo pipefail

REPO="$(git rev-parse --show-toplevel)"
AUDIT_DIR="${REPO}/.agent/self-audits"
RULES_DIR="${REPO}/.agent/rules"

mkdir -p "${AUDIT_DIR}"

branch="$(git rev-parse --abbrev-ref HEAD)"
upstream="$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null || echo origin/main)"
ts="$(date -u +%Y-%m-%dT%H-%M-%SZ)"

rules_hash="$(find "${RULES_DIR}" -type f -name '*.md' 2>/dev/null | sort | xargs cat 2>/dev/null | sha256sum | cut -c1-12)"
diff_hash="$(git diff "${upstream}..HEAD" 2>/dev/null | sha256sum | cut -c1-12)"

commits="$(git log --format='%h %s' "${upstream}..HEAD" 2>/dev/null || echo)"
files="$(git diff --name-only "${upstream}..HEAD" 2>/dev/null || echo)"

audit_file="${AUDIT_DIR}/${ts}-${branch//\//_}.md"

cat <<EOF
== coding-rails self-audit ==

Branch       : ${branch}
Upstream     : ${upstream}
Rules hash   : ${rules_hash}
Diff hash    : ${diff_hash}

Commits in this push:
${commits:-  (none)}

Files changed:
${files:-  (none)}

----
Answer the four questions below. Your text becomes the audit record at:
  ${audit_file#${REPO}/}

Type your answers, finish with a single line containing only: END
----
EOF

# Collect free-form justification
read_until_end() {
  local line
  while IFS= read -r line; do
    if [ "${line}" = "END" ]; then break; fi
    printf '%s\n' "${line}"
  done
}

cat <<EOF >"${audit_file}"
---
ts: ${ts}
branch: ${branch}
upstream: ${upstream}
rules_hash: ${rules_hash}
diff_hash: ${diff_hash}
---

# Self-audit record

## Commits in this push

\`\`\`
${commits}
\`\`\`

## Files changed

\`\`\`
${files}
\`\`\`

## Agent justification

EOF

read_until_end >>"${audit_file}"

# Validate justification length and DRIFT_FOUND attestation
justification="$(sed -n '/## Agent justification/,$p' "${audit_file}" | tail -n +3)"
nws_len="$(printf '%s' "${justification}" | tr -d '[:space:]' | wc -c)"
distinct_chars="$(printf '%s' "${justification}" | fold -w1 | sort -u | wc -l)"
drift_lines="$(printf '%s\n' "${justification}" | grep -c '^DRIFT_FOUND=' || true)"

if [ "${nws_len}" -lt 80 ]; then
  echo "ERROR: justification too short (${nws_len} non-whitespace chars, need >=80)" >&2
  rm -f "${audit_file}"
  exit 2
fi
if [ "${distinct_chars}" -lt 15 ]; then
  echo "ERROR: justification looks like filler (${distinct_chars} distinct chars, need >=15)" >&2
  rm -f "${audit_file}"
  exit 2
fi
if [ "${drift_lines}" -ne 1 ]; then
  echo "ERROR: justification must contain exactly one DRIFT_FOUND= line" >&2
  echo "  Use: 'DRIFT_FOUND=no' or 'DRIFT_FOUND=yes: <description ≥20 chars>'" >&2
  rm -f "${audit_file}"
  exit 2
fi

token="$(sha256sum "${audit_file}" | cut -d' ' -f1)"

echo ""
echo "+ audit record written: ${audit_file#${REPO}/}"
echo "  token: ${token}"
echo ""
echo "If your pre-push hook requires the token, export it before pushing:"
echo "  PRECOMMIT_SELF_AUDIT_OK=${token} git push -u origin ${branch}"
