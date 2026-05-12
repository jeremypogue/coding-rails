# coding-rails — rule index

**These rules govern coding-agent behavior only.** Runtime/domain agents (the LLMs operating production) have their own rules elsewhere in the host project.

Every coding agent — Claude Code, Codex, Cline, Cursor, Windsurf, OpenCode, Kanban, browser-based, or human-driven via terminal — MUST read every numbered file in this directory at session start, before touching any file in the workspace.

The harness entry files (`AGENTS.md`, `CLAUDE.md`, `.clinerules/`, `.cursorrules`, `.windsurfrules`, `.opencode/AGENTS.md`) point at this directory. They do not duplicate content; they are pointers.

## Rules in load order

1. [`001-task-ledger.md`](./001-task-ledger.md) — every change has a restart-survivable task ledger entry.
2. [`004-test-coverage.md`](./004-test-coverage.md) — code change + test change in the same commit.
3. [`008-evidence-required.md`](./008-evidence-required.md) — "verified" / "shipped" claims require evidence references.
4. [`010-scope-lock.md`](./010-scope-lock.md) — task scope is frozen at start; out-of-scope edits trigger drift records that block commit/push/finish/CI until the operator resolves.

> The bundle ships four rules. Numbers 002, 003, 005, 006, 007, 009 are intentionally unallocated — projects that consume this bundle may add their own numbered rule files under `.agent/rules/` alongside the bundle's, and add matching check scripts under `scripts/coding-rails/rules/`. The pre-commit and PR-completion-gate aggregators discover all `*.py` rule scripts at runtime, so project-defined rules fire automatically once dropped in.

## How these rules are enforced

- **Read at session start (any harness)** — every harness's entry file (`AGENTS.md`, `CLAUDE.md`, `.clinerules`, `.cursorrules`, `.windsurfrules`) points here. Do not begin any file edit, code change, config change, commit, push, or deploy without having read the rules in the current session.
- **At commit time (provider-neutral)** — `.githooks/pre-commit` runs concrete pass/fail checks tied to each rule. Bypassing the hook (`--no-verify`) is logged to `.agent/precommit.log` and detected at push time.
- **At push time (provider-neutral)** — `.githooks/pre-push` blocks pushes to shared branches, refuses force pushes, refuses merge commits in the pushed range, and runs the regression suite.
- **At PR time (GitHub Actions)** — `.github/workflows/agent-task-gates.yml` validates PR body sections, scope (files-within-`allowed_paths`), branch shape, and negative-smoke transcript.
- **At GitHub server level** — branch protection on `main` requires PR + required status checks + CODEOWNERS review; force pushes and deletions are disabled. This is the floor: even a harness that bypasses every local control cannot land bad code on `main`.

## Adding a project-specific rule

Add a new numbered file in this directory. Update this index. Add a check script under `scripts/coding-rails/rules/`. The pre-commit and CI workflows pick it up automatically (rule scripts are discovered by filename pattern).

## Bypass policy

Agent-driven bypass (`--no-verify`, `OPERATOR_BYPASS=1`, etc.) is forbidden. Operator-driven bypass requires explicit operator review before push and leaves an audit trail in `.agent/precommit.log`.

## When a rule has not been followed

Write a one-line note in `.agent/rule-violations.md` with the date, the rule number, and what happened. Supervisors read this file.
