# Rule 004 — Test Coverage

## The rule

When you change behavior in any code path the user can hit, the change is not done until a test exercises **the user's full request shape**, not just the part that's easy to mock.

Specifically: every change to a code file under a configured "agent surface" path must have a paired test file changed in the same commit.

## Why this rule exists

Coding agents will routinely:

- Change an `agents/<x>.py` file and not touch `tests/test_<x>.py`.
- Add a feature, run `py_compile`, declare it tested.
- Mock `requests.post` and never run the actual reasoning loop.
- Test the happy path that was already working; ignore the failure mode the change was supposed to fix.

Every one of these passes an internal check. None of them tests the user's actual surface.

## Required behavior

### For every code change

- If the change touches a file matching the project's "agent surface" glob (default: `agents/*.py`), a corresponding test file matching the project's test template (default: `tests/test_<name>.py`) must be modified or created in the same commit.
- Tests must exercise the **full pipeline at least once** — not only individual functions in isolation. For LLM-driven agents, use a fake/stub LLM and assert the agent terminates, calls the right tools, and returns the expected shape.

### What does NOT count as testing

- A unit test that mocks the network and never runs the real path.
- A "happy path" smoke that only exercises the cached / instant route.
- A `py_compile` syntax check.
- "I tested it manually" with no committed assertion.
- A test that passes today because the broken thing is skipped.

## Configuration

The check script reads `<repo>/.agent/coding-rails.config.yml` if present. If absent, it uses defaults:

```yaml
agent_surface_glob: "agents/*.py"
test_path_template: "tests/test_{name}.py"
```

Projects with different layouts override these in their config file. The check honors per-project configuration without bundle modification.

## Enforcement

- **Pre-commit** — detects when an agent-surface file is staged without its paired test file, blocks the commit, prints which test is missing.
- **PR completion gate (CI)** — re-runs the check against the full diff, in case the local hook was bypassed.

## Operator-maintained exceptions

If a code change genuinely does not need a paired test (e.g. comment-only change, generated file, third-party vendoring), the operator adds the path glob to `.agent/test-coverage-exceptions.md`:

```markdown
# Generated files have no source-of-truth to test
agents/_generated_*.py

# Vendored library
agents/_vendor.py

# Comment-only refactor approved in PR #42
agents/docs.py
```

The format is one path glob per line; lines starting with `#` are comments; inline ` # ...` is stripped.

The check script reads this file at every run; no commit-time interaction needed. Operator owns the file; agents do not modify it.

## Bypass policy

There is no agent-side bypass through the script. The only path to skip the paired-test requirement is an operator-authored entry in `.agent/test-coverage-exceptions.md`, which is committed and visible in the PR.
