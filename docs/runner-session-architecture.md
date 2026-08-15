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

## Selected Context Checkouts

A selected plan may also bind a stage to a generic context checkout. The
binding is selected authority: it names one UTF-8 `template` router asset, a
workspace-relative checkout root, bounded required and discoverable sources,
and optional `direct_write` or `protected_proposal` rules. The compiler checks
the complete binding closure, including stage, runner, asset, source, path,
and writeback linkage. It does not recognize a workflow name, stage nickname,
or implicit workspace directory.

The supported source declarations are `dispatch_material:current`,
`accepted_lineage_artifacts:current_lineage`,
`lineage_attempt_history:current_lineage`, and selected
`workspace_relative_root` paths. Required sources fail closed when unavailable
or over bounds. Discoverable sources are either captured completely or appear
as a deterministic omission in the manifest. Selected roots cannot overlap
`.millrace`, the configured SQLite database, the CAS root, or one another.

For a bound session, Millrace first captures stable UTF-8 regular-file bytes,
stores the canonical schema-1 `millrace.context_checkout_manifest` and each
selected file in the existing byte CAS, then publishes a read-only checkout
under:

```text
<checkout_root>/<session-id>/<dispatch-generation>/
├── CONTEXT.md
├── checkout.manifest.json
├── required/
└── discoverable/
```

`CONTEXT.md` contains the selected router body and a generated index of the
authority boundary, required reads, discoverable sources, live project root,
write rules, and legal output channel. It contains navigation and policy, not
an absolute operator path or a copy of canonical runtime state.

The session lifecycle is explicit:

```text
created without context
  -> stable capture and CAS publication
  -> created with one attached manifest digest
  -> starting
  -> running or a legal terminal aftermath
```

`RunnerSessionRecord.context_manifest_digest` is optional only for an
unbound session or a bound session still in `created`. `AttachRunnerSessionContext`
is replay-safe for the exact identity and digest; a different digest or a
late attach is a reconciliation contradiction. Once attached, every session
transition preserves the digest. If CAS prewrites occur before the attach
transaction commits, they remain unreferenced and a later created session may
capture afresh. After a committed attach, restart may only verify or
rematerialize the attached manifest and its exact CAS bytes.

Before dispatch and before result acceptance, the local checkout is checked
against the attached manifest: path set, regular-file kinds, bytes, manifest
digest, session, generation, plan fingerprint, binding, router, and CAS
references must all agree. A missing, added, substituted, or drifted checkout
refuses the result. Read-only modes are defense in depth; this is integrity
validation, not a filesystem sandbox.

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

For a bound run, the schema-7 dispatch envelope carries one compact
`context_checkout` descriptor containing the manifest digest, binding ID,
router asset ID, checkout-relative path, and `CONTEXT.md` path. The descriptor
is part of the dispatch echo, so changing it invalidates runner evidence. The
Codex adapter exposes that descriptor only through wrapper protocol 4; the
wrapper receives navigation instructions and reads the materialized checkout
itself, not a checkout file body in the prompt bundle. A bound Codex session
must resolve its configured `cwd` to the initialized Millrace workspace root.
Unbound sessions retain protocol-3 behavior, including the absence of a
`context_checkout` prompt field.

Protocol 4 also exposes the reviewed Codex token-usage mapping. A successful
or post-provider error envelope carries exact non-negative input, output, and
total counts, with total equal to input plus output. Only protocol 4 receives
that reviewed capability; protocol 3 remains byte-compatible.

For a write-enabled binding, result validation compares the pre-dispatch
snapshot, selected live roots, and the linked selected artifact before the
runner result becomes a kernel observation. Direct changes must be reported
with truthful create/modify/delete digests under direct-write roots. Protected
proposals carry content and a digest but do not mutate the protected path. A
no-op requires a nonblank reason. Unreported, forbidden, protected, symlinked,
or digest-inconsistent changes refuse the result; the runtime does not roll
back a detected filesystem mutation.

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

When the current session ends with an adapter error, `runs show RUN_ID` adds a
bounded `rejected_result` projection. When a completed runner result has a
refused application receipt, it adds the same projection with
`rejection_kind: observation_refusal` and the durable kernel refusal reason.
The projection reports the session identity, application status, relevant
digests, safe marker and candidate-presence booleans when the retained
evidence passes CAS and canonical-codec checks. Missing, corrupt, and digest
mismatch evidence is reported as a stable status rather than exposed.

Candidate bodies are omitted by default. Operators may request only the
current rejected session's already-redacted canonical evidence and bounded
completion diagnostic with:

```text
millrace runs show RUN_ID --include-rejected-evidence
```

This is a read-only projection. It never creates artifacts, advances queues,
retries sessions, or changes refusal state. Accepted, pending, cancelled,
lost, and orphan-risk sessions retain their existing projections.

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

Runner sessions use store schema 9 and CAS-backed bounded evidence. Selected
plans use schema 17, runner dispatch envelopes use schema 7, runner-session
records use schema 2, and context manifests use schema 1. There is
no automatic migration from schema 6 or schema 7. An exact schema-version-6 or
schema-version-7 workspace is refused as `workspace_upgrade_required` with
the database, CAS, and runner-event sidecar byte-for-byte unchanged; see
[v0.22 compatibility](v0.22-compatibility.md). Schema 8 and unknown store
versions retain unsupported-schema refusal without mutation. No context
manifest is inferred for a pre-session run.

See [Daemon lifecycle](daemon-lifecycle.md) for startup, restart, signal, and
shutdown behavior and [Errors and refusals](errors.md) for stable public
codes.
