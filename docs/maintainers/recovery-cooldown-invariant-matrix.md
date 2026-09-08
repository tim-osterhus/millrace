# Recovery Cooldown Invariant Matrix

This matrix covers the generic relationship between a lineage recovery attempt
and its durable cooldown wait. A reset-trigger result belongs to the exact
selected plan, policy, and lineage observed by the terminal action. It does not
consume or delete a cooldown wait owned by another recovery transition.

A sibling failure during an exact pending cooldown is saved as the existing
runner completion but its application is deferred with `cooldown_wait_pending`.
It receives no application receipt until a real due timer consumes the wait.
The daemon revisits completed results before new dispatch, with idle backoff
when nothing else is ready; unrelated ready work remains eligible. This adds
no schema, retry counter, timer policy, or second completion store.

If that saved failure advances the lineage below its quarantine threshold,
the prior timer-created recovery activation may be superseded. The kernel
recognizes it as a noncandidate only from its exact consumed wait, accepted
input, selected target, source/route provenance and later same-policy attempt.
Missing or mismatched provenance remains corrupt authority. A later real due
timer can create the new valid recovery activation normally.

Compiler/admission changes: not applicable. Selected recovery declarations,
failure counts and wait durations are unchanged; this correction governs
application ordering and projections of existing durable records.

Additional proof:

- `tests/kernel/test_recovery_cooldown_source_collision.py`: pending failure
  refusal, persisted saved completion/exact replay, below-threshold reload
  and next due activation, and rejected forged supersession provenance.
- `tests/cli/test_cli_daemon_lifecycle.py`: completion-before-dispatch,
  unrelated ready work, waiting backoff, stop and max-tick bounds.

| Authority field/record | Legal state/sequence | Illegal/corrupt state | Admission/runtime rule | Durable rule | Exact test proof |
| --- | --- | --- | --- | --- | --- |
| Pending attempt and wait ownership | A `pending_cooldown` attempt has an unconsumed wait whose attempt record ID, plan, policy, lineage, attempt count, and pending source fields all match. | A wait points to a missing, resolved, active, wrong-plan, wrong-policy, wrong-lineage, or wrong-count attempt. | A reset-trigger success defers only when that exact pending pair exists. It does not infer ownership from an active attempt or from another sibling wait. | The wait remains unconsumed and the attempt remains `pending_cooldown`; SQLite relation validation remains unchanged. | `tests/kernel/test_recovery_cooldown_reset.py::test_persisted_sibling_pass_keeps_pending_cooldown_wait`; `tests/kernel/test_recovery_cooldown_reset.py::test_reset_guard_does_not_defer_unrelated_active_attempt`; `tests/kernel/test_recovery_cooldown_reset.py::test_reset_guard_requires_exact_wait_not_pending_phase_alone` |
| Accepted sibling success and replay | A persisted terminal success is accepted once while the pending pair remains intact. Replaying the same input is a no-op. | A success resolves the pending attempt without consuming its wait, or replay starts another run/session/result. | The terminal result and its ordinary close/artifact mutations remain accepted; only the unrelated reset mutation is deferred. | Wait, attempt, receipts, observations, runs, sessions, completions, and transition counts survive reload and exact replay. | `tests/kernel/test_recovery_cooldown_reset.py::test_persisted_sibling_pass_keeps_pending_cooldown_wait` |
| Due wait lifecycle | Before `due_at`, `TimerDue` refuses. At or after `due_at`, one timer transition creates the recovery activation, marks the wait consumed, and advances the attempt to `active_recovery`. | Early consumption, duplicate consumption, missing target authority, or phase mismatch. | Due dispatch uses the compiled wait destination and consumes only the exact unconsumed wait. | Consumed input, timestamp, resulting activation, and attempt phase persist across reload; the consumed row is historical evidence. | `tests/kernel/test_recovery_cooldown_reset.py::test_due_wait_dispatches_then_later_reset_preserves_consumed_history`; `tests/cli/test_cli_daemon_lifecycle.py::test_daemon_applies_due_cooldown_before_runner_dispatch`; `tests/cli/test_cli_daemon_lifecycle.py::test_lifecycle_does_not_consume_cooldown_before_due_time`; `tests/substrate/test_persistence_integrity_refusals.py::test_generic_restart_preserves_consumed_cooldown_after_attempt_advances` |
| Later normal reset | After the wait is consumed, a later legitimate reset-trigger success resolves the active attempt without rewriting the consumed wait. | A later reset rewrites consumed provenance or creates a second attempt transition on replay. | Normal reset behavior is preserved once no unconsumed exact wait remains. | The attempt resolves and the consumed wait remains linked with its original consumption fields. | `tests/kernel/test_recovery_cooldown_reset.py::test_due_wait_dispatches_then_later_reset_preserves_consumed_history` |
| Corrupt persisted phase | A valid pending pair loads and dispatches. | An unconsumed wait paired with `active_recovery` or `resolved` attempt phase is refused on load. | The correction does not weaken phase validation or repair state in memory. | `StorageIntegrityError` remains the fail-closed outcome before further runtime work. | `tests/kernel/test_recovery_cooldown_reset.py::test_pending_wait_phase_integrity_refusal_remains[active_recovery]`; `tests/kernel/test_recovery_cooldown_reset.py::test_pending_wait_phase_integrity_refusal_remains[resolved]` |
