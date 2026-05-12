# Changelog

All notable changes to coding-rails are documented here. Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Semver.

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
