# Rule 001 — Persistent Task Ledger

## The rule

Before making any file, code, config, or git change, record the work in a restart-survivable task ledger under `<repo>/.agent/tasks/`. Chat-only todos and in-memory plans do not count.

## Why this rule exists

Sessions die. Context windows compact. Agents restart. When that happens, the *only* thing that survives is what's on disk. Without a ledger, every restart is a fresh "what was I doing?" — and work gets duplicated, abandoned, or silently broken.

Multiple coding agents running in parallel (Claude Code + Codex + Cline at the same time) without separate ledgers will step on each other's work. The ledger is the coordination surface.

## Required behavior

- **One ledger file per tool / session / task.** Parallel sessions must not overwrite each other.
- **Path:** `<repo>/.agent/tasks/<task_id>.json`. The task_id follows the format `<YYYYMMDD>-<tool>-<short-slug>` — e.g. `20260512-claude-pool-pump-fix.json`.
- **Required fields:**
  ```json
  {
    "task_id": "20260512-claude-pool-pump-fix",
    "agent": "claude | codex | cline | cursor | windsurf | opencode | human",
    "session": "<session-id-or-thread-id-or-recovery-text>",
    "started_at": "<ISO-8601 timestamp>",
    "branch": "agent/<tool>/<YYYYMMDD>-<slug>",
    "base_ref": "origin/main",
    "base_sha": "<resolved at task start>",
    "allowed_paths": ["explicit/file/path", "tests/test_foo.py", "..."],
    "status": "pending | in_progress | blocked | done | superseded",
    "summary": "<one-line statement of intent>"
  }
  ```
- **Update `status` as work moves.** A task abandoned without status update is unfinished work that the next session can't recover.
- **`allowed_paths` is enforced.** The pre-commit hook refuses to stage files outside this list. Be explicit; do not list directories unless you mean every file in them.

## Helper

The bundled script creates the ledger from a template:

```bash
./scripts/coding-rails/agent_start_task.sh <task_id> --paths "path1,path2,..."
```

This script also creates the agent's working branch and writes the resolved base SHA into the ledger.

## Recovery procedure (when a session restarts)

1. Read `.agent/tasks/` first. List files; identify yours by `agent` + `session` + `branch`.
2. Resume from the persisted `status` and `allowed_paths` *before* inspecting dirty git state or memory.
3. If you cannot find the prior ledger, create a new one and note the discontinuity in `summary`.

## Enforcement

- **Pre-commit** — refuses commit if no `.agent/tasks/<task_id>.json` matches the current branch, or if `allowed_paths` does not include every staged file.
- **PR completion gate (CI)** — refuses PR merge if the task ledger references a `base_sha` that has been rewritten or no longer exists.

## Bypass policy

There is no agent-side bypass. If you need to make a change outside a task scope, an operator must edit the ledger directly.
