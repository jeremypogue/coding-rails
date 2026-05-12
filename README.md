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
│   ├── pre-commit              ← invokes rule scripts (rules 001/004 + project rules)
│   ├── commit-msg              ← invokes rule 008 (evidence required) on the message
│   ├── pre-push                ← block shared branches, force, merge commits, bypass scan
│   └── post-commit             ← bypass detection log
├── .github/workflows/
│   ├── agent-task-gates.yml    ← PR body, scope, branch shape, base_sha, scope-growth, commit-msg evidence
│   └── agent-rules-check.yml   ← per-rule check matrix
├── scripts/coding-rails/
│   ├── agent_start_task.sh     ← creates task metadata + branch
│   ├── agent_finish_task.sh    ← runs rules against base_ref..HEAD + pushes + opens PR
│   ├── agent_completion_gate.py
│   ├── agent_bash_guard.sh     ← best-effort BASH_ENV destructive-git guard
│   ├── agent_git_guard.py
│   ├── precommit_self_audit.sh
│   └── rules/                  ← one check per rule
│       ├── 001_task_ledger.py
│       ├── 004_test_coverage.py
│       └── 008_evidence_required.py
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

- `--setup-github` — also configure GitHub branch protection on `main`/`master` via the `gh` CLI (requires `gh auth status` to show `repo` scope). Configures: require PR, required status checks (`agent-task-gates`, `agent-rules-check`), no force push, no deletion, no direct push, require CODEOWNERS review. On GitHub Free private repos this step is automatically skipped with a clear note (server-side floor unavailable; local hooks + CI + operator merge-button discipline is the floor).
- `--force` — overwrite **everything** including entry pointers (`AGENTS.md`, `CLAUDE.md`, `.clinerules/`). Default behavior is described below.
- `--dry-run` — show what would be copied without doing it.
- `--target=<path>` — install into a directory other than the current one.

### Overwrite semantics

- **Bundle-owned paths** (`.agent/rules/`, `.githooks/`, `.github/workflows/`, `scripts/coding-rails/`) are **always overwritten**. This is intentional: it's how upgrades work — the bundle is authoritative for these paths.
- **Entry pointer files** (`AGENTS.md`, `CLAUDE.md`, `.clinerules/01-coding-rails-pointer.md`) are **kept** if they already exist in the target. The bundle does not stomp on a project's existing instructions. Use `--force` to overwrite them.
- **Runtime artifact directories** (`.agent/state/`, `.agent/precommit-markers/`) are created fresh with a self-ignoring `.gitignore`. Existing contents are preserved.
- **Per-project config** (`.agent/coding-rails.config.yml`) is NOT touched. Projects edit this file to override defaults; the bundle ships an example at `bundle/coding-rails.config.example.yml` for reference.

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
| 001 Task ledger | every coding change has a fresh ledger entry + staged files in `allowed_paths` (ledger itself auto-allowed as bookkeeping) | `scripts/coding-rails/rules/001_task_ledger.py` | pre-commit + CI completion gate |
| 004 Test coverage | code change paired with test (operator exceptions in `.agent/test-coverage-exceptions.md`) | `scripts/coding-rails/rules/004_test_coverage.py` | pre-commit + CI |
| 008 Evidence required | "verified"/"shipped" commit msg requires evidence reference | `scripts/coding-rails/rules/008_evidence_required.py` | **commit-msg** (per-commit) + CI completion gate (across PR range) |
| Branch scope | only the personal `agent/<tool>/<date>-<slug>` branch is push-allowed | `bundle/hooks/pre-push` | pre-push |
| No force push | reject `--force` / `--force-with-lease` | `bundle/hooks/pre-push` | pre-push |
| No merge commits | reject merge commits in pushed range | `bundle/hooks/pre-push` + completion gate | pre-push + CI |
| No bypass leakage | hook output never instructs agents how to bypass | `bundle/hooks/*` | every hook |
| Allowed paths | every changed file in task's `allowed_paths` (ledger + test-coverage-exceptions auto-allowed) | `bundle/hooks/pre-commit` + completion gate | pre-commit + CI |
| Conflict markers | no `<<<<<<<` / `=======` / `>>>>>>>` in committed files | `agent_completion_gate.py` | CI |
| Base SHA reachable | ledger's `base_sha` must be reachable from PR's base or head | `agent_completion_gate.py` | CI |
| Scope growth | PR cannot expand `allowed_paths` in its own ledger | `agent_completion_gate.py` | CI |
| PR completion | task ends as PR; PR body has required sections | `agent_completion_gate.py` | CI |
| Self-tests | the bundle's bash + Python scripts have integration tests | `tests/` + `.github/workflows/ci.yml` | CI on every push/PR to coding-rails main |

Coming next: deeper CI for hook chain edge cases (force-push refusal via real git push); fuller workplace-rule set as opt-in extensions. Helper-level unit tests for `agent_completion_gate.py` exist (v0.3.0 added 13); end-to-end `main()` coverage via `--pr-json` testing mode exists (v0.4.0); real-CI integration coverage comes from PR runs against installed targets.

## Why "coding-rails" and not "agent-rails"

This bundle is about **coding agents only**. Domain/runtime agents (the LLMs that operate a production system) have their own rules — those live in the host project, not here. The name signals the boundary.

## Versioning

Semver. The installed version is recorded in `<target>/.agent/coding-rails-version.txt`. Upgrade via `../coding-rails/upgrade.sh`.

## License

MIT. Use it, fork it, ship your own variant.
