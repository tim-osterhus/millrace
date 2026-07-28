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

`runs list`, `runs show`, `trace show RUN_ID`, and `status` expose a compact
`runner_session` projection when a current session exists. The projection can
include:

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

Runner sessions use store schema 7 and CAS-backed bounded evidence. There is
no automatic schema-6-to-7 migration. An exact schema-version-6 workspace is
refused unchanged as `workspace_upgrade_required`; see
[v0.22 compatibility](v0.22-compatibility.md).

See [Daemon lifecycle](daemon-lifecycle.md) for startup, restart, signal, and
shutdown behavior and [Errors and refusals](errors.md) for stable public
codes.
