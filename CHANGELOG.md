# Changelog

All notable changes to coding-rails are documented here. Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Semver.

## [0.5.0] — 2026-05-12

Active Scope Sentinel — repo-native detection + poison-pill semantics for mid-session scope drift. Driven by a reviewer push to make drift visible in conversation, not just at PR time.

### Added

- **New rule 010 — Active Scope Sentinel** (`bundle/rules/010-scope-lock.md`). Once a task starts, `allowed_paths` are frozen; any change outside them is **drift**; the first drift detected **poisons the task** and no downstream layer (commit, push, finish, CI gate) can proceed until the operator resolves it. Documented honestly: detection + poison-pill, not OS-level prevention.
- **`bundle/scripts/agent_scope_check.py`** — one-shot scope status check. Reports clean/drift, optionally writes a drift record. (Renamed from `agent_scope_status.py`.)
- **`bundle/scripts/agent_scope_watch.py`** — polling watcher (default 1s) that writes drift records on detection AND emits a heartbeat to `.agent/state/<task_id>.heartbeat` every cycle.
- **`bundle/scripts/agent_checkpoint.py`** — single-line CLEAN/DRIFT/NO-TASK/NO-WATCHER status the agent prints after every file-changing turn. Cheap (<100ms). Designed to be cited in the agent's response so operators see scope state mid-conversation.
- **`bundle/scripts/rules/010_scope_lock.py`** — pre-commit-side check: refuses commit if unresolved drift exists for the current task OR if the ledger's `allowed_paths` hash no longer matches the frozen scope lock.
- **`.agent/scope/<task_id>.lock`** — written at task start by `agent_start_task.sh`, contains the frozen allowed_paths + sha256 scope hash + lock timestamp. Tracked.
- **`.agent/drift/<task_id>.json`** — written by the watcher / scope-check on detection. Status: `unresolved` | `resolved`. Per-machine; gitignored by default; operator may commit explicitly when documenting resolution.
- **`.agent/state/<task_id>.heartbeat`** — written by the watcher on every poll. Used by `agent_finish_task.sh` to refuse if the watcher wasn't running.

### Changed

- **`agent_start_task.sh`** now writes the scope lock at task start.
- **`agent_finish_task.sh`** now refuses if (a) an unresolved drift record exists OR (b) the watcher heartbeat is missing or older than `CODING_RAILS_MAX_HEARTBEAT_AGE` seconds (default 60s). Operator override via `CODING_RAILS_SKIP_HEARTBEAT=1`.
- **`bundle/hooks/pre-push`** now refuses pushes from an agent branch whose task has an unresolved drift record. Closes the window where a commit made *before* drift was detected could still push *after* drift was recorded.
- **`bundle/scripts/agent_completion_gate.py`** gains a rule-010 drift-record check. CI refuses PRs that contain an unresolved drift record. The gate's `bookkeeping` set now also covers `.agent/scope/<task_id>.lock` and `.agent/drift/<task_id>.json` so operators can commit those without out-of-scope errors.
- **`install.sh`** provisions `.agent/scope/` (tracked) and `.agent/drift/` (gitignored by default) on install/upgrade.
- **README + rule index** updated for rule 010.
- **`coding-rails.config.example.yml`** gains a `scope_enforcement` section: watch interval, fail-on-drift, require-clean-scope-before-finish, additional bookkeeping path globs.

### Honest framing (in the rule itself)

Rule 010 explicitly says what it does NOT promise:
- It does NOT prevent the file write — only OS / container / sandbox can.
- It detects drift within ~1s and makes it structurally fatal to the task.
- An agent operating in bad faith (kill watcher + delete drift record + silence heartbeat) is out of scope for this rule. The answer there is OS-level isolation (separate UID, ACLs, scoped container), which the bundle does not ship.

For private personal repos with no GitHub branch protection, credential separation is the final gate: the agent does not push; the operator runs `git push` after the bundle reports clean.

### Self-test counts

- v0.4.0: 158 + 1 skipped
- **v0.5.0: 180 + 1 skipped** (+22 net for v0.5.0; tests for rule 010 / scope_check / checkpoint / heartbeat / drift-blocks-completion-gate)

### Naming notes

- Rule numbered **010**, not 002. The bundle's existing ships are 001/004/008; project-specific extensions (like agent-mesh-v2's 002 dashboard rule) sit in the 1–9 range. Putting a portable bundle rule at 002 would clobber projects that already use that number.
- Markdown still lives at `bundle/rules/010-scope-lock.md` and check script at `bundle/scripts/rules/010_scope_lock.py`. The filename retains "scope-lock" for stable cross-version references; the rule's *content* and the bundle's framing call it "Active Scope Sentinel" to be honest about what it actually does.

### Still deferred

- **Level-3 filesystem permissions** (ACL / scoped container). Operator-controlled OS config; bundle doesn't ship. Rule 010's drift detection works alongside Level-3 setups.
- **Real `gh` mock** (cleaner than `--pr-json` in v0.4.0).
- **Operator-authored scope** (still from v0.2.0 review).

## [0.4.0] — 2026-05-12

Driven by a third external review of v0.3.0. Four remaining consistency / coverage gaps closed.

### Fixed

- **CI completion gate's rule-008 scan now uses the same patterns + per-project config as the local commit-msg hook.** Previously, `agent_completion_gate.py::check_commit_msg_evidence` hardcoded `COMPLETION_RE` and `EVIDENCE_REGEXES`. A project could customize `.agent/coding-rails.config.yml` and the local commit-msg hook would respect it, but CI would enforce a different policy. New `bundle/scripts/_evidence_lib.py` holds the shared logic; both sites import it. (Review point 1 — biggest remaining consistency issue.)
- **`check_allowed_paths_not_expanded` now catches scope growth *within* a PR.** Previously, if the ledger didn't exist at `base_sha` (i.e. the PR creates the ledger), the check returned True unconditionally. An agent could create the ledger with narrow scope in commit 1, expand it in commit 2, and CI wouldn't notice. Now: if no base-SHA version exists, find the earliest commit in (base..head] that touched the ledger and use that as the comparison baseline. (Review point 2.)

### Added

- **`--pr-json <file>` mode** for `agent_completion_gate.py`. Bypasses the `gh pr view` round-trip by reading PR metadata from a local JSON file. Enables end-to-end testing of `main()` without network/auth/CI. (Review point 3.)
- **`tests/test_evidence_lib.py`** — 17 tests covering the new shared module: defaults, YAML config loading (valid + invalid), comment stripping, message validation across the matrix (empty / no-claim / claim-without-evidence / claim-with-each-evidence-form), and per-project config affecting behavior.
- **`tests/test_completion_gate_e2e.py`** — 8 end-to-end tests against `agent_completion_gate.py::main()` via `--pr-json`. Covers: normal happy path; ledger auto-allowed as bookkeeping; bad branch shape rejected; out-of-scope files rejected; missing PR body section rejected; commit-msg evidence enforcement across PR range; scope-growth-within-PR detection (the actual review-point-2 regression).
- **`tests/test_hooks_integration.py`** gains two real-git pre-push tests: `test_pre_push_blocks_force_push` (push after amend), `test_pre_push_blocks_merge_commit_in_range` (push of a no-ff merge).

### Changed

- **`agent_completion_gate.py` is now a thin coordinator** over `_evidence_lib` for the commit-msg evidence scan. The hardcoded `COMPLETION_RE` and `EVIDENCE_REGEXES` are gone; the shared library is the single source of truth.
- **README "Coming next" wording updated** to reflect what's actually deferred (real-git force-push tests, fuller workplace-rule set) vs. what now exists (helper-level unit tests, `--pr-json` E2E mode).

### Self-test counts

- v0.3.0: 132 tests
- **v0.4.0: 158 passing + 1 conditional skip** (+26 net: 17 evidence_lib + 8 completion gate E2E + 2 pre-push integration; small adjustments to existing tests)

### Still deferred

- Operator-authored scope (issue #10 from v0.2.0 review).
- A genuine `gh` mock would replace `--pr-json` mode with a stronger contract. `--pr-json` is the pragmatic stopgap.
- `commit-msg` real-git integration test for the strict-no-arg behavior (currently unit-tested only).

## [0.3.0] — 2026-05-12

Driven by a second external review of v0.2.0 plus the first real-world use of the bundle on agent-mesh-v2. Three real defects surfaced during use and are fixed here.

### Fixed

- **`agent_completion_gate.py` now auto-allows bookkeeping paths.** Previously, a PR that introduced its own `.agent/tasks/<id>.json` ledger would fail the CI completion gate's `allowed_paths` check (unless the agent manually listed its own ledger). Now the gate exempts the task ledger and `.agent/test-coverage-exceptions.md` — mirroring the pre-commit rule 001 behavior. (Review point 1)
- **`agent_finish_task.sh` now validates the committed range**, not the empty staged set. Previously it required a clean working tree, then ran rule scripts that look at `git diff --cached` — which on a clean tree showed nothing, so the rules passed trivially. Now finish_task seeds a temp `GIT_INDEX_FILE` with the base-SHA tree, stages every file changed in `base_ref..HEAD`, runs the rule scripts against that view, and discards the temp index. The real index is untouched. (Review point 2)
- **Rule 008 is strictly commit-msg-only.** Previously, the script fell back to reading `.git/COMMIT_EDITMSG` when invoked without an arg (the pre-commit invocation pattern). That was unsafe because COMMIT_EDITMSG can hold stale content from a previously-failed `git commit -m` attempt. Now the script returns "" immediately when no message-path arg is passed — only the commit-msg hook invocation, which receives the actual path, can validate. (Closes issue #7; surfaced during v2 PR #28.)

### Changed

- **README updated to match implementation:**
  - File-layout list now includes `commit-msg` hook
  - "What's enforced" table corrected: rule 008 fires at **commit-msg**, not pre-commit; allowed_paths gain bookkeeping auto-allow note; added rows for the v0.2.0-shipped checks (conflict markers, base_sha reachability, scope growth) that weren't documented
- **`install.sh` no longer attempts to copy `bundle/tests/`** — that directory was never shipped, the install line was a no-op, and the README falsely claimed installed tests would live under `tests/coding_rails/`. Removed the line and the claim. The bundle's own tests at the coding-rails root validate the bundle; consumers don't inherit them.

### Added

- **`tests/test_agent_completion_gate.py`** — 13 new unit tests covering `check_allowed_paths` (with and without bookkeeping), `check_branch_shape` (with v0.3.0 dot-allowing slug), `check_pr_body` (missing sections, comment-only-empty sections), and `path_in_allowed` (exact / glob / directory-prefix). Imports the completion gate as a module so the helpers are unit-testable without the `gh pr view` round-trip.
- **`tests/test_008_evidence_required.py::test_pre_commit_ignores_stale_editmsg`** — regression test that plants a completion-claim message in `.git/COMMIT_EDITMSG` and verifies the script invoked without an arg ignores it.

### Self-test counts

- v0.2.0: 67 tests
- v0.2.x (PR #5 regex fix): 90 tests (+23 branch-shape)
- v0.2.x (PR #6 bash harness): 91 tests (+24 install/start/hooks integration; some overlap)
- **v0.3.0: 132 tests** (+41 from this release: completion gate, 008 strict mode, bash harness from PR #6)

### Still deferred (not in v0.3.0)

- **CI end-to-end test for completion gate.** The gate calls `gh pr view`, which is hard to mock cleanly. Unit tests cover the helpers; integration coverage relies on real PR runs against the v2 install.
- **Full operator-authored scope** (issue #10 from the v0.2.0 review). Growth-check shipped in v0.2.0 is partial mitigation.
- **Pre-push real-git tests for force/non-FF refusal.** Doable but requires more git plumbing in the test harness.

## [0.2.0] — 2026-05-12

Driven by external review of v0.1.0. Addresses 10 substantive issues plus adds the first test suite.

### Added

- **commit-msg hook** (`bundle/hooks/commit-msg`): runs `008_evidence_required.py` against the prepared commit message. Pre-commit cannot see the message yet; this closes the enforcement gap that v0.1.0 documented but did not actually enforce.
- **Bookkeeping auto-allow in rule 001** (`bundle/scripts/rules/001_task_ledger.py`): the task ledger file itself and `.agent/test-coverage-exceptions.md` are always commit-allowed without explicit listing. Removes the bootstrap deadlock where the workflow couldn't commit its own ledger.
- **`.agent/state/` runtime directory** (created by `install.sh`): runtime task state (PR URL, `awaiting_review` status) lives here, gitignored. Committed ledger stays immutable. Includes a self-ignoring `.gitignore`.
- **`.agent/.gitignore`** (created by `install.sh`): ignores `precommit.log` and `self-audits/` for any project.
- **Operator-maintained test-coverage exceptions** (`bundle/scripts/rules/004_test_coverage.py`): reads `.agent/test-coverage-exceptions.md` for operator-authored path globs to exempt from the paired-test requirement.
- **base_sha reachability check** in `agent_completion_gate.py`: refuses PRs whose ledger references a base SHA that is no longer reachable (rebase onto stale base).
- **Commit-msg evidence scan across PR range** in `agent_completion_gate.py`: re-validates every commit message in the PR against rule 008. Catches commits made with `--no-verify` or via a non-commit-msg-aware workflow.
- **allowed_paths-growth check** in `agent_completion_gate.py`: refuses PRs where the ledger's `allowed_paths` was *expanded* by the PR itself (catches agent-driven scope creep).
- **Test suite** (`tests/`): 67 pytest tests covering rule 001 (task ledger), rule 004 (test coverage + exceptions), rule 008 (evidence required + commit-msg arg + comment stripping), and the full `agent_git_guard.py` decision matrix (destructive refusals, force/no-verify/-c overrides, shared-branch detection, gh CLI handling).
- **`.github/workflows/ci.yml`**: runs the test suite on every push and PR.

### Changed

- **`agent_finish_task.sh`**: no longer mutates the committed ledger after push. Records `status: awaiting_review` and `pr_url` in `.agent/state/<task_id>.json` (gitignored) instead. Resolves the "post-PR ledger dirties tree" issue.
- **`agent-rules-check.yml`**: job `rules-check` now has `name: agent-rules-check`. The required-status-check name reported to GitHub now matches what `install.sh --setup-github` registers.
- **`agent_git_guard.py`**: now refuses `git push --no-verify`, `git push -n`, and `git -c core.hooksPath=<anything>`. The first bypasses the pre-push hook; the second bypasses the entire hook chain per-command.
- **`pre-push` hook**: if `coding-rails-version.txt` exists but `.agent/precommit.log` is missing, the push is blocked (was previously silently skipped). Missing log is now treated as suspicious, not benign.
- **`README.md`**: documents the actual overwrite semantics (bundle-owned paths always overwrite; entry pointers skip-if-present; `--force` covers entry pointers too).
- **Rule 001, 004, 008 docs**: updated to match the actual implementation (was previously aspirational in places).
- **`agent_completion_gate.py` docstring**: clarifies that negative-smoke section verification is presence/non-emptiness only, not semantic — operator judges the transcript at review time.

### Fixed

- **`v2_secrets_scan.py`** (Windows path): subprocess output decoded as UTF-8 with replacement, was previously cp1252 on Windows and crashed on real diffs. (Note: this fix is in the v2 install of coding-rails, since `v2_*` scripts are project-specific. The bundle's own scripts also use raw bytes with explicit UTF-8 decode where they invoke `git diff`.)

### Strategic notes (still open)

- `allowed_paths` remains agent-authored. The growth check (above) is partial mitigation. Full operator-authored scope (issue templates / PR labels / signed manifests) is deliberately deferred — discussed in #11 of the v0.1.0 review.
- `agent_bash_guard.sh` is explicitly best-effort, not load-bearing. CI checks remain the actual floor on private repos with GitHub Pro; on Free private repos, the floor is operator merge-button discipline (no server-side enforcement available without Pro).

## [0.1.0] — 2026-05-11

Initial bundle skeleton + complete bundle release.

- README, install.sh (with `--setup-github`), VERSION, LICENSE
- Bundle: rules 001/004/008 (text + check scripts), pre-commit/pre-push/post-commit hooks, agent_start_task.sh, agent_finish_task.sh, agent_completion_gate.py, agent_bash_guard.sh, agent_git_guard.py, precommit_self_audit.sh
- CI workflows: agent-task-gates.yml, agent-rules-check.yml
- Entry templates: AGENTS.md, CLAUDE.md, .clinerules/
- Per-project config: coding-rails.config.example.yml
- Graceful Free-private 403 handling in install.sh
