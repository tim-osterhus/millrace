# Daemon Lifecycle And Signals

`millrace run daemon` is the local single-operator execution loop. On startup
it opens and integrity-checks runtime state, then classifies every current
runner session before dispatching new work. It replays an unapplied successful
completion, resumes a session still in `created`, and asks the selected adapter
to reconcile potentially started states.

Restart never guesses success. A verified live session retains ownership.
Unsupported reattachment for potentially live work becomes `lost` with
`orphan_risk`. Contradictory locators, authority, completion, or cleanup state
stop the daemon without repair or replacement.

## Durable Budget Epochs

The daemon accepts optional durable ceilings:

```bash
millrace run daemon \
  --budget-id bounded-run-1 \
  --max-wall-seconds 3600 \
  --max-invocations 8
```

`--max-total-tokens` is available when the resolved selected adapter declares
the reviewed token-usage mapping required for governance. Every limit must be a
positive integer and any limit requires a nonblank caller-selected
`--budget-id`. `--max-ticks` remains a loop/test bound; it is not an invocation
budget.

The first use pins the budget ID to the workspace, selected default plan,
limits, start time, and wall deadline in the schema-8 store. Reusing the same
ID with identical authority resumes its totals and deadline. Changed limits,
workspace, or plan are refused. Accepted runner start intents are counted once
per fenced runner session, and reviewed adapter usage is accumulated
monotonically against that same session identity. Missing or contradictory
usage under a token-governed epoch refuses the epoch rather than treating
usage as zero.

These normalized input, output, and total token counters enforce the selected
daemon ceiling. They are adapter-reported execution evidence, not billing,
invoice, provider spend, price, or provider rate-limit truth.

Wall expiry uses the existing `daemon_shutdown` runner-session cancellation
path. Invocation and completed-token exhaustion prevent another daemon unit.
The daemon summary, `status`, run/trace projections, and `doctor` expose
bounded budget identity, limits, counters, status, terminal reason, and
overshoot.

## Lock Ownership

The daemon holds `.millrace/daemon.lock` while it owns the loop. The lock is
diagnostic-only: it records bounded local ownership information but is not a
cross-process control channel. Millrace does not provide a `daemon stop`
command. If no process owns an ambiguous stale lock, inspect it before manual
removal.

## SIGINT And SIGTERM

On POSIX systems, `SIGINT` and `SIGTERM` request an orderly daemon shutdown.
When a runner session is active, the coordinator first persists the primary
`daemon_shutdown` cancellation request, then performs the same truthful
cooperative/terminate/kill/transport-cleanup sequence used by other
cancellation reasons.

The daemon does not report a clean signal stop until owned worker and reader
work has reached `complete` or `not_required` cleanup. If cleanup cannot be
proved, the stop summary reports `runner_session_orphan_risk`. A second
terminal signal cannot replace the first accepted completion.

For direct process evidence, wait for daemon progress or active-session
evidence, record the PID from `.millrace/daemon.lock`, send that PID one
`SIGINT` or `SIGTERM`, and wait with an explicit bound for the recorded process
to exit. The diagnostic lock is not a readiness signal. Check both OS liveness
and exit status, then compare the JSON daemon-stop summary with the durable
runner-session projection. Shell, terminal, and `tmux` state are not lifecycle
authority.

## Stop Summaries

JSON stop output reports bounded counters, `stopped_reason`, `last_result`,
diagnostics, and the affected `runner_session` when available. That session
projection includes selected adapter kind, cancellation reason/phase, cleanup,
completion/application status, orphan risk, mechanical grace constants, and
the last persisted cancellation operation/result when available. It does not
claim static operation support that a live handle has not proved. Human output
remains compact.

Stop summaries are read-only projections. They do not repair state, replace a
session, or create workflow meaning.

Use `millrace status`, `millrace runs show RUN_ID`,
`millrace trace show RUN_ID`, and `millrace doctor` after shutdown. For event
history, use the finite
`millrace runs follow RUN_ID --after-sequence N` projection.
