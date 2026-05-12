# Rule 010 — Active Scope Sentinel

## The rule

Once a task starts, its `allowed_paths` are **frozen**. During the task:

- Any file changed outside the allowed paths is **drift**.
- The first drift detected **poisons the task**. A poisoned task cannot commit, push, finish, or pass CI until the operator resolves it.
- Drift cannot be cleared by the agent — only by operator action (revert + delete record, or expand ledger + mark resolved, or abort task).

This rule is the runtime complement to rule 001 (task ledger). Rule 001 records what the task is *allowed* to do. Rule 010 records what the task *actually* did, makes drift visible within ~1s of the edit, and prevents a drifted task from completing cleanly.

## Honest framing

This is a **repo-native detection + poison-pill** model, not a filesystem permission lock. The truth:

> A repo-only bundle cannot physically stop an arbitrary process from editing files. Only the OS, a container runtime, or the agent's harness sandbox can do that. Git hooks, Python scripts, and watchers can only inspect state when they run.

Rule 010 gives you something different — and adequate for most threat models:

1. **Drift becomes visible** within ~1s (polling watcher writes a drift record).
2. **Drift becomes structurally fatal** to the task (every downstream gate checks for the drift record).
3. **Scope cannot expand silently** (scope-hash mismatch between ledger and `.agent/scope/<task_id>.lock` is a hard fail).
4. **Watcher absence is detectable** (heartbeat file; finish_task refuses if stale).

What this does NOT promise:
- It does NOT prevent the file write. An agent that ignores rule 010 can still write the file. The drift record just makes that fact unmissable.
- It does NOT survive an agent that has `rm -rf .agent/drift/`. The drift record can be deleted; the watcher's next poll re-writes it; finish_task / pre-commit / pre-push / CI all check at run time, so a deleted-and-re-watched record still blocks. But an agent that kills the watcher AND deletes the record AND silences the heartbeat is operating in bad faith — at which point the answer is OS-level isolation, not better hooks.

## Why this rule exists

`allowed_paths` declared in the ledger is operator-blessed scope at task start. Without active enforcement, an agent in conversation can:

- Edit a file outside its declared scope mid-session
- Expand `allowed_paths` itself to "cover" the new file
- Continue working as if the scope had always been that wide
- Push a PR that the gate approves because the post-hoc ledger and the diff line up

Active scope lock closes those three escapes:

1. **Drift detection** — a polling watcher compares the working tree to the ledger every second. Any unauthorized file change writes a drift record on disk within ~1s of the edit.
2. **Drift-record blocking** — pre-commit, pre-push, `agent_finish_task.sh`, and the CI completion gate ALL refuse to proceed while an unresolved drift record exists for the current task.
3. **Scope freeze** — at task start, `agent_start_task.sh` writes `.agent/scope/<task_id>.lock` with a SHA-256 of the frozen `allowed_paths` set. The check refuses if the ledger's current allowed_paths hash doesn't match the lock.

## Required behavior

### At task start

- `agent_start_task.sh` writes `.agent/scope/<task_id>.lock` containing:
  ```json
  {
    "task_id": "<id>",
    "branch": "<agent-branch>",
    "allowed_paths": [...],
    "scope_hash": "sha256:<hash of sorted allowed_paths>",
    "locked_at": "<ISO-8601>"
  }
  ```
- The scope lock is tracked under `.agent/scope/` (committed via the same bookkeeping mechanism as the ledger).

### During the task

- `agent_scope_watch.py` should run alongside the agent's working session, polling `git status` at the configured interval (default 1s). On detection of an out-of-scope change (modified, untracked, deleted), it writes `.agent/drift/<task_id>.json`:
  ```json
  {
    "task_id": "<id>",
    "branch": "<agent-branch>",
    "detected_at": "<ISO-8601>",
    "unauthorized_paths": ["..."],
    "status": "unresolved"
  }
  ```
- Once a drift record exists with `status: unresolved`, no commit / push / finish / CI gate succeeds until the operator resolves it.

### Resolution paths

An operator (NOT the agent) resolves drift one of three ways:

1. **Revert the unauthorized changes.** Operator runs `git restore <path>` for each unauthorized file. After the watcher re-scans and the working tree matches the ledger, the operator deletes `.agent/drift/<task_id>.json` or sets `status: resolved`.
2. **Expand the ledger explicitly.** Operator edits `.agent/tasks/<task_id>.json` to add the new paths to `allowed_paths`, recomputes the scope hash, updates `.agent/scope/<task_id>.lock`, and sets the drift record to `status: resolved`.
3. **Abort the task.** Operator marks the ledger `status: superseded`, deletes the drift record, and starts a fresh task.

### What the agent CAN do

- Modify any file under `allowed_paths` exactly as before.
- Modify bookkeeping paths: `.agent/tasks/<own_task_id>.json`, `.agent/test-coverage-exceptions.md`, `.agent/state/`, `.agent/precommit-markers/`.
- Read any file in the repo.

### What the agent CANNOT do

- Modify the scope lock file at `.agent/scope/<task_id>.lock`.
- Modify the drift record at `.agent/drift/<task_id>.json`.
- Expand `allowed_paths` in the ledger and continue (CI completion gate's allowed_paths-growth check from v0.2.0 catches this at PR time; rule 010 catches it during the session).
- Delete the drift record (the next watcher poll re-creates it).

## Enforcement

| Layer | Mechanism |
|---|---|
| **Live (mid-session)** | `scripts/coding-rails/agent_scope_watch.py` polls `git status` and writes drift records within ~1s |
| **One-shot (any time)** | `scripts/coding-rails/agent_scope_check.py` reports current scope state without writing |
| **Per-turn agent checkpoint** | `scripts/coding-rails/agent_checkpoint.py` — one-line CLEAN/DRIFT/NO-WATCHER status the agent prints after each file-changing turn |
| **Pre-commit** | `scripts/coding-rails/rules/010_scope_lock.py` refuses commit if unresolved drift exists OR scope-hash mismatch detected |
| **Pre-push** | refuses push if unresolved drift record exists for the current branch's task |
| **finish_task** | refuses to push / open PR if drift unresolved OR watcher heartbeat is missing/stale |
| **CI completion gate** | refuses PR if `.agent/drift/<task_id>.json` is committed AND `status` is unresolved |

## Recommended operating model

The rule is most useful when the agent's harness invokes the checkpoint after every file-changing turn:

```
1. agent_start_task.sh <task_id> --paths <list>    # freezes scope
2. agent_scope_watch.py &                           # background polling watcher
3. # Agent works.
4. # After every file-changing response, agent runs:
5. agent_checkpoint.py                              # prints one-line CLEAN / DRIFT
6. agent_finish_task.sh                             # refuses if poisoned or watcher stale
```

In personal repos with no GitHub branch protection (the v0.4.0 review's note), credential separation is the final gate: the agent does not have push credentials. The operator runs `git push` after `finish_task` reports clean. The repo state machine — frozen scope, drift records, heartbeat — is the local truth source.

## Configuration

`.agent/coding-rails.config.yml`:

```yaml
scope_enforcement:
  enabled: true              # default true — rule fires regardless of config (config is opt-out)
  watch_interval_seconds: 1  # polling rate for agent_scope_watch.py
  fail_on_drift: true        # exit non-zero from watcher on first drift detected (alongside writing record)
  require_clean_scope_before_finish: true   # agent_finish_task.sh refuses if drift record exists
```

## Bookkeeping paths (auto-allowed)

Beyond `allowed_paths`, these are always writable without explicit declaration:

- `.agent/tasks/<own_task_id>.json` — the task's own ledger
- `.agent/test-coverage-exceptions.md` — operator-maintained
- `.agent/state/` — runtime state (gitignored)
- `.agent/precommit-markers/` — hook markers (gitignored)
- `.agent/scope/<own_task_id>.lock` — written by `agent_start_task.sh`, read-only thereafter

Other `.agent/*` paths are NOT bookkeeping — `.agent/decisions.json`, `.agent/rules/*`, other agents' task ledgers, other agents' state directories all require explicit `allowed_paths`.
