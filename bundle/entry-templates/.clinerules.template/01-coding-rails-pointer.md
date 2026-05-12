# 01 — coding-rails entry pointer

**You are a coding agent operating in this repository.** Before you make any file edit, code change, commit, or push, read every numbered file under `.agent/rules/`. They are short. The failure modes they prevent have already happened in this workspace.

## Mandatory at session start

1. Read `.agent/rules/index.md` first — the rule index.
2. Read every numbered rule file. All of them. Do not skim.
3. If you find yourself thinking "I'll come back to read those later" — stop. Read them now.

## Mandatory before any code change

- Create a task ledger via `scripts/coding-rails/agent_start_task.sh <task-id> --paths <list>`.
- The pre-commit hook will reject staged files outside `allowed_paths`.

## Mandatory at task completion

- Run `scripts/coding-rails/agent_finish_task.sh` — runs checks, pushes, opens PR.
- Do not push directly to `main`, do not force-push, do not push to shared branches.
- Do not use `--no-verify`. Do not set `OPERATOR_BYPASS` env vars.

## Why this file exists

Cline reads every file under `.clinerules/` as always-active rules at session start. This file is a content-free pointer at the actual rule set in `.agent/rules/`. The same pointer pattern is used by `AGENTS.md` (Codex) and `CLAUDE.md` (Claude Code).

---

*The actual rules live in `.agent/rules/`. Do not duplicate content here.*
