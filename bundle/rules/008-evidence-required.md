# Rule 008 — Evidence Required for "Done" Claims

## The rule

A commit message that contains "verified", "shipped", "confirmed", "tested", or any equivalent claim of completion MUST reference concrete evidence — a URL that was scraped, a test command that ran, a screenshot path, a logbook entry, or a user-surface artifact.

Internal smoke tests, `py_compile`, and "tests pass" are necessary but not sufficient. The evidence must show the change worked through the *user's actual entrypoint with the user's actual request shape*.

## Why this rule exists

Coding agents declare "shipped" / "verified" / "tested" routinely while the user's actual surface is still broken. Internal tests pass because the test mocks the part that's failing. The agent is not lying; it has no way to know its tests didn't cover the real path. The fix is to require **externally observable evidence** in the commit message.

## Required behavior

### When claiming completion

Pick one of these evidence patterns (the check is regex-based and lenient):

- **URL scrape:** `verified at http://...` or `evidence: https://...`
- **Test command + result:** `verified: pytest tests/test_x.py -q :: 5 passed`
- **Screenshot:** `screenshot: <path>` or `evidence-image: <path>`
- **Log entry:** `logbook: <iso-timestamp>` or `event_log: <agent>/<offset>`
- **External confirmation:** `telegram: <msg-id>` or `sms: <thread>` or `physical-check: <observation>`
- **Or:** the literal string `evidence: <description>` — anything after `evidence:` counts as long as it's >10 chars.

If a commit message says "verified" / "shipped" / etc. **without** one of these references, the commit is rejected.

### When the change is genuinely un-verifiable from a hook

Some changes (physical actions, Sonos, hardware reboots) cannot be smoked from a hook. In that case the task ledger entry must reference an out-of-band confirmation (Telegram timestamp, HA logbook entry, photo) and the commit message references the ledger entry:

```
Restart driveway camera

evidence: see .agent/tasks/20260512-claude-cam-restart.json (telegram msg 4821)
```

## Configuration

The check script reads `<repo>/.agent/coding-rails.config.yml` if present:

```yaml
completion_phrases:
  - verified
  - shipped
  - confirmed
  - tested
  - smoke[ d]?
evidence_patterns:
  - "(?i)https?://"
  - "(?i)evidence:\\s*\\S{10,}"
  - "(?i)pytest .* :: .* passed"
  - "(?i)screenshot:\\s*\\S+"
  - "(?i)logbook:\\s*\\S+"
  - "(?i)telegram:\\s*\\S+"
```

## Enforcement

- **Pre-commit** — scans the commit message; if a completion phrase appears without a matching evidence pattern, the commit is rejected with a message naming the missing reference.
- **PR completion gate (CI)** — re-runs the check against every commit in the PR range.

## Bypass policy

No agent-side bypass. If you genuinely need to use "verified" in a non-claim context (e.g. *"this verifies that..."* in a docstring sentence), rephrase the commit message.

## The principle

If the user discovers your change is broken before you do, **the test you wrote was not testing the right thing.** That is the only definition of "shipped" that matters. The evidence reference forces you to capture *what you actually checked* — and if you didn't check the right thing, the missing evidence shows up in code review.
