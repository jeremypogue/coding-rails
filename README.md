# coding-rails

Portable coding-agent enforcement bundle. Drops into any git repository to enforce the rules that prevent coding-agent chaos — task ledgers, test pairing, allowed-paths scope, PR-only workflow, and no-bypass guardrails — **harness-agnostic** (Claude Code, Codex, Cline, Cursor, Windsurf, OpenCode, browser-based agents, and humans alike).

**This bundle governs coding agents only.** Runtime/domain agents (the LLMs that operate the production system) are outside its scope — their rules live in the host project.

## What it solves

Coding agents (Claude Code, Codex, Cline, etc.) running in parallel sessions, with no isolation or scope, will:

- Step on each other's work — one agent reverts another's fix while "fixing" a different bug.
- Use destructive commands (`git reset --hard`, `git stash`, `git push --force`) to "clean up" mid-task.
- Push to shared branches and bypass hooks via `--no-verify` if the hooks tell them how.
- Skip reading instructions if nothing forces them to.
- Leave you to open PRs, resolve conflicts, and merge — instead of finishing the task themselves.

`coding-rails` collapses every guardrail into the repository itself, so the same protections apply regardless of which harness the agent uses, which host it runs on, or whether a hook system exists.

## What it installs

```
<target-repo>/
├── .agent/
│   ├── rules/                  ← the rule text (numbered .md files)
│   ├── tasks/                  ← task metadata per coding-agent session
│   └── coding-rails-version.txt
├── .githooks/
│   ├── pre-commit              ← scope/test-pair/secrets/bypass-strip
│   ├── pre-push                ← block shared branches, force, merge commits
│   └── post-commit             ← bypass detection log
├── .github/workflows/
│   ├── agent-task-gates.yml    ← PR body, scope, branch shape, negative-smoke
│   └── agent-rules-check.yml   ← per-rule check matrix
├── scripts/coding-rails/
│   ├── agent_start_task.sh     ← creates task metadata + branch
│   ├── agent_finish_task.sh    ← runs checks + pushes + opens PR
│   ├── agent_completion_gate.py
│   ├── agent_bash_guard.sh     ← best-effort BASH_ENV destructive-git guard
│   ├── agent_git_guard.py
│   ├── precommit_self_audit.sh
│   └── rules/                  ← one check per rule
│       ├── 001_task_ledger.py
│       ├── 004_test_coverage.py
│       └── 008_evidence_required.py
├── tests/coding_rails/         ← test suite for the rule checks themselves
├── AGENTS.md                   ← entry pointer for Codex (copied only if absent)
├── CLAUDE.md                   ← entry pointer for Claude Code (copied only if absent)
└── .clinerules/                ← entry pointer for Cline (copied only if absent)
```

Entry-point files are **content-free pointers** at `.agent/rules/`. They tell the harness "read the rules here." No content duplication.

## Install

```bash
# From within the target repo:
git clone https://github.com/jeremypogue/coding-rails ../coding-rails
../coding-rails/install.sh --setup-github
```

Flags:

- `--setup-github` — also configure GitHub branch protection on `main`/`master` via the `gh` CLI (requires `gh auth status` to show `repo` scope). Configures: require PR, required status checks (`agent-task-gates`, `agent-rules-check`), no force push, no deletion, no direct push, require CODEOWNERS review.
- `--force` — overwrite existing files in the target. Default is skip-if-present.
- `--dry-run` — show what would be copied without doing it.
- `--target=<path>` — install into a directory other than the current one.

After install:

```bash
# In any new coding-agent session:
./scripts/coding-rails/agent_start_task.sh 20260512-fix-pool-bug --paths "agents/pool.py,tests/test_pool.py"
# ... agent works in the new branch, scoped to those paths ...
./scripts/coding-rails/agent_finish_task.sh
# Runs checks, opens PR. Operator reviews + merges.
```

## What's enforced (current bundle)

| Rule | Source | Check | Where it fires |
|---|---|---|---|
| 001 Task ledger | every coding change has a fresh ledger entry | `scripts/coding-rails/rules/001_task_ledger.py` | pre-commit |
| 004 Test coverage | code change paired with test | `scripts/coding-rails/rules/004_test_coverage.py` | pre-commit |
| 008 Evidence required | "verified"/"shipped" commit msg requires evidence reference | `scripts/coding-rails/rules/008_evidence_required.py` | pre-commit |
| Branch scope | only the personal `agent/<tool>/<date>-<slug>` branch is push-allowed | `bundle/hooks/pre-push` | pre-push |
| No force push | reject `--force` / `--force-with-lease` | `bundle/hooks/pre-push` | pre-push |
| No merge commits | reject merge commits in pushed range | `bundle/hooks/pre-push` | pre-push |
| No bypass leakage | hook output never instructs agents how to bypass | `bundle/hooks/*` | every hook |
| Allowed paths | staged files must be within task's `allowed_paths` | `bundle/hooks/pre-commit` | pre-commit |
| PR completion | task ends as PR; PR body has required sections | `scripts/coding-rails/agent_completion_gate.py` | CI |

Coming next (planned, not in initial skeleton): rules 002, 003, 005, 006 task-aware checks; full negative-smoke harness for agent_git_guard; portable rules-config for non-mesh projects.

## Why "coding-rails" and not "agent-rails"

This bundle is about **coding agents only**. Domain/runtime agents (the LLMs that operate a production system) have their own rules — those live in the host project, not here. The name signals the boundary.

## Versioning

Semver. The installed version is recorded in `<target>/.agent/coding-rails-version.txt`. Upgrade via `../coding-rails/upgrade.sh`.

## License

MIT. Use it, fork it, ship your own variant.
