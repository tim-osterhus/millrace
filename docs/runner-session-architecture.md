# Runner-Session Architecture

A Millrace workflow run and an external runner attempt are different durable
identities. `RunRef` remains the workflow-run identity. Every external attempt
has a distinct `session_id`, increasing `dispatch_generation`, and
`session_fencing_token`. A same-run retry advances only the dispatch
generation; it does not replace the run, claim, plan, or workflow generation.

The selected plan owns adapter authority. Each run points to a selected runner
binding, and that binding supplies `adapter_kind`. Local configuration may
resolve that kind to an implementation and narrow limits, but it cannot remap
the selected binding or add workflow meaning.

## Coordinator Ownership

`adapters/cli/session_coordinator.py` is the stable orchestration facade. It
owns session creation, external start, live-handle driving, and state routing.
Four private modules own the remaining mechanics:

- `session_reconciliation.py` owns restart reconciliation and locator checks.
- `session_cancellation.py` owns cancellation, escalation, and cleanup.
- `session_completion.py` owns completion persistence, replay, and diagnostics.
- `session_persistence.py` owns CAS evidence loading and governed usage
  persistence.

The dependency graph is acyclic. The coordinator may use all four private
modules. Reconciliation may use cancellation and completion. Cancellation may
use completion. Completion may use persistence. Persistence does not use
another session module.

Reconciliation returns verified live state through one immutable record with
four fields: `session`, `request`, `handle`, and `deadline`. The private modules
do not expose a second orchestration API. Existing callers continue to use the
coordinator facade.

## Durable Ordering

Before external work starts, Millrace persists the run, creates the session,
and commits `state=starting` with its start intent. A crash in `created` proves
that start was not attempted. A crash after start intent is potentially live
and must be reconciled; Millrace cannot assume clean failure or launch a
replacement.

Completion follows one path:

1. validate and bound adapter output;
2. authenticate the complete run/session/plan/claim/generation/fence echo;
3. persist one terminal session completion;
4. for success only, replay the persisted evidence through the existing
   runner-result transition;
5. treat that transition's deterministic input receipt as application proof.

Adapter errors, cancellation, loss, and live events cannot create a workflow
result, route, artifact, recovery attempt, terminal action, or closure.

## Cancellation

The operator command is:

```text
millrace runs cancel RUN_ID --input-id ID
```

`ID` is the replay-safe cancellation request ID. The command always requests
`operator_cancel_work` with source `operator`; callers cannot choose internal
reasons. The request is durable and audited. The CLI never signals a PID.
The current session coordinator observes the request and escalates through
cooperative cancellation, terminate, kill, and transport cleanup as supported.

The mechanical grace constants are
`cooperative_cancel_grace_seconds=5.0` and
`terminate_grace_seconds=5.0`. They are finite local cleanup mechanics, not
compiled workflow policy. Millrace reports cleanup as `complete`,
`not_required`, or truthful `orphan_risk`; it never infers cleanup success.

JSON automation can branch on `runner_session_cancel_requested` or
`runner_session_cancel_refused`. Replaying the same accepted input ID is
idempotent. Missing, terminal, stale, lost, or contradictory sessions are
refused.

## Read-Only Projections

`runs list` is active-run scoped and adds a compact `runner_session` projection
to each listed active run. `runs show RUN_ID` and `trace show RUN_ID` expose
the current session for that run. `status` exposes current sessions across its
runtime scope. The projection can include:

- session ID, dispatch generation, state, and selected adapter kind;
- primary cancellation reason and current phase;
- cleanup disposition and orphan-risk state;
- completion persistence and deterministic application-receipt status;
- the effective mechanical grace constants.

Daemon stop summaries carry the final affected session, including the last
persisted cancellation operation/result when one exists. Unsupported or
unknown operations are not guessed. `doctor` reports the grace constants and
bounded diagnostics:
`runner_session_lost`, `runner_session_orphan_risk`,
`runner_session_reconciliation_unsupported`, and
`runner_session_cleanup_pending`.

These surfaces are bounded, redacted, read-only, and non-authoritative. They
do not reconcile, repair, retry, cancel, or otherwise mutate runtime state.
Application status is `not_completed`, `not_applicable`, `pending`, `applied`,
or `refused`; a persisted refused receipt is never reported as applied.

## Finite Event Follow

The event projection is:

```text
millrace runs follow RUN_ID --after-sequence N
```

It is a finite bounded read, not a live blocking tail. The result contains a
bounded page, the next sequence, an explicit gap marker when retained history
was compacted or unavailable, and final status reconciled from durable session
and completion state. Runner-session events are optional, bounded, redacted,
and non-authoritative; they never enter `RuntimeState.runner_observations`.

## Persistence Compatibility

Runner sessions use store schema 8 and CAS-backed bounded evidence. There is
no automatic migration from schema 6 or schema 7. An exact schema-version-6 or
schema-version-7 workspace is refused as `workspace_upgrade_required` with
the database, CAS, and runner-event sidecar byte-for-byte unchanged; see
[v0.22 compatibility](v0.22-compatibility.md).

See [Daemon lifecycle](daemon-lifecycle.md) for startup, restart, signal, and
shutdown behavior and [Errors and refusals](errors.md) for stable public
codes.
