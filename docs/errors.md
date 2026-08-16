# Errors And Refusals

Millrace reports expected failures as bounded errors or governed refusals.
Use `--json` for automation and branch on `code`, `reason`, or `error_kind`
instead of human-readable messages.

## CLI Envelope

Successful JSON results contain exactly `ok`, `command`, `code`, `message`,
and `data`. Error results contain exactly `ok`, `command`, `code`, `message`,
and `details`.

| Exit | Meaning | State changed? | Retry? | Operator action |
| --- | --- | --- | --- | --- |
| `0` | Command completed | Depends on the command | Not needed | Inspect returned data. A doctor command can complete while reporting unhealthy findings. |
| `1` | Unexpected CLI failure | Unknown | Once | Retry once, then report the bounded error without parsing traceback text. |
| `2` | Invalid command or input shape | No | After correction | Fix arguments, JSON, actor identity, or local options. |
| `3` | Governed domain refusal | Normally no new requested mutation | After state/input changes | Inspect the exact code and current status. |
| `4` | Missing, unsupported, or corrupt persistence | No requested mutation | Not blindly | Preserve files; run workspace/package/status/doctor checks and restore known-good state. |
| `5` | Runner or evidence failure | A claim may already be durable; no terminal result was accepted | Only when safe | Repair adapter/provider/output configuration, then inspect the claimed run. |

Example:

```json
{"ok":false,"command":"workspace.check","code":"store_not_initialized","message":"SQLite runtime store is not initialized.","details":{}}
```

## Operator Reference

| Family | Representative codes | Meaning | State changed? | Retry? | Operator action |
| --- | --- | --- | --- | --- | --- |
| CLI usage | `argument_parse_error`, `invalid_actor_id`, `invalid_payload_json`, `invalid_max_events` | The command could not form a valid request. | No | After correction | Use command help and correct the request. |
| Workspace and CAS | `store_not_initialized`, `cas_root_not_initialized`, `substrate_error` | Required local state is absent or inconsistent. | No requested mutation | Not blindly | Initialize a new workspace or inspect known-good state. |
| Plans and packages | `plan_not_admitted`, `compiled_plan_export_invalid`, `plan_fingerprint_drift`, `package_archive_unreadable`, package-selection diagnostic codes | Package or selected-plan authority is unavailable or invalid. | Failed operations do not partially select a plan | After correction | Verify package identity, digests, selection, and fingerprint. |
| Queue and operator input | `invalid_payload_source`, `unknown_queue_family`, `queue_family_not_external`, `invalid_payload_schema`, `queued_work_not_found`, `queued_lineage_not_found`, `queued_work_not_cancelable`, `queued_work_plan_mismatch`, `queued_lineage_plan_mismatch`, `queued_work_claimed`, `queued_runner_session_live`, `queued_runner_session_lost`, `queued_runner_session_missing`, `queued_runner_cleanup_unresolved` | The selected plan does not accept this intake, or the requested queue closure is absent, stale, wrong-plan, or unsafe while runner aftermath may remain. | Accepted queue closure records audit evidence and closes the complete target set atomically; refusals create no closure record | Re-query first | Use the selected plan fingerprint. Control live work with `runs cancel`; retry queue closure only after clean terminal runner aftermath. |
| Dispatch and readiness | `unknown_run`, `already_claimed`, `stale_generation`, `workspace_paused`, `dispatch_suspended`, `dispatch_already_suspended`, `dispatch_not_suspended`, `dispatch_suspension_identity_mismatch`, `stale_dispatch_generation`, `selected_plan_mismatch`, `lineage_quarantined`, `operator_wait_active`, `dependency_not_ready` | Work is absent, stale, gated, or corrupt, or an operator dispatch control does not match current durable authority. | Accepted suspend/resume and claim commands mutate through the common transition path; read-only queries do not | Re-query first | Inspect status and use the exact active suspension ID, selected-plan fingerprint, and a new input ID when correcting a refused command. |
| Waits and interventions | `unknown_operator_wait`, `operator_wait_resolution_forbidden`, `invalid_operator_wait_target`, `unknown_intervention_option` | The requested operator action is not selected or is stale. | No | After refresh | List current waits/interventions and choose a declared option. |
| Compiler diagnostics | `missing_id`, `duplicate_id`, `missing_reference`, `invalid_artifact_schema`, `unsupported_terminal_action_kind`, `unsupported_compatibility_profile` | Authored workflow/package authority is invalid. | No runtime mutation | After source correction | Fix all error diagnostics before admission. |
| Daemon budgets and usage | `budget_id_required`, `budget_limit_required`, `runner_usage_mapping_unsupported`, `budget_plan_pin_refused`, `daemon_budget_epoch_refused`, `daemon_budget_limit_out_of_range`, `daemon_budget_immutable_limits_changed`, `daemon_budget_clock_discontinuity`, `runner_usage_evidence_refused`, `budget_not_found`, `budget_workspace_mismatch`, `budget_terminal_conflict`, `budget_session_not_terminal`, `budget_session_cleanup_incomplete`, `budget_session_completion_missing`, `budget_session_usage_missing`, `budget_session_usage_not_final`, `budget_session_lost`, `budget_session_orphan_risk`, `budget_projection_corrupt` | Budget intake, immutable epoch authority, clock continuity, governed adapter usage, or the audited operator close preconditions are absent or refused. | Intake and close refusals create no requested mutation. Immutable-limit and clock refusals preserve the existing active epoch. Governed usage refusal and a successful operator close terminalize and suspend new dispatch atomically. | After correction or a new budget ID | Correct the limits, selected-plan pin, clock, runner cleanup/completion/usage evidence, or workspace selection. Preserve and inspect the existing epoch rather than rewriting it. |
| Daemon lifecycle | `no_ready_work`, `ready_state_refused`, `ready_state_corrupt`, `lifecycle_transition_refused`, `observation_refused` | The daemon found no work or could not legally advance it. | Depends on the last accepted transition | After inspection | Read status, runs, trace, waits, and doctor output before retrying. |
| Adapter and evidence | `timeout`, `cancelled`, `missing_opt_in_config`, `invocation_failed`, `result_parse_failed`, `input_too_large`, `output_too_large`, `redaction_refused`, `selected_authority_refused` | The runner attempt failed or its output could not become candidate evidence. | A claim may exist; no terminal route was accepted | Only when category permits | Repair local configuration, selected authority, wrapper output, or bounds. |
| Runner sessions | `runner_session_cancel_requested`, `runner_session_cancel_refused`, `runner_session_retry_refused`, `runner_session_orphan_risk` | A durable session command was accepted or safely refused. | Accepted cancellation records a request; refusals do not alter session authority | Re-query first | Inspect `runs show`, `trace show`, `status`, and `doctor`; never replace lost/orphan-risk work blindly. |
| Workspace upgrade | `workspace_upgrade_required` | An exact schema-version-6 or schema-version-7 workspace reached the schema-9 runtime. | No; the database, CAS, and runner-event sidecar remain byte-for-byte unchanged | No | Finish or retire active work with its matching runtime, then initialize a schema-9 workspace. |
| Package doctor | `workflow_package_registry_load_refused`, `active_pin_selected_plan_corrupt`, and finding categories such as `manifest_digest_mismatch` | Package health is unhealthy or unknown. | No; doctor is read-only | After repair | Repair the package source/registry or preserve an active pin as reported. |

## Stable Fields

- Compiler errors expose diagnostic `code`.
- Kernel transition refusals expose `reason`.
- CLI results expose `code`.
- Package command audit exposes `error_code`.
- Runner adapters expose `error_kind`.
- Doctor output exposes `overall_status`, finding `category`, and diagnostic
  `code`.

Messages, local paths, provider responses, internal exception names, and
unspecified diagnostic detail are not stable automation fields.

## Important Boundaries

Runner success is candidate evidence, not completion authority. A failed
adapter attempt creates no terminal action, route, artifact, or closure,
although the preceding claim may already be recorded.

Persistence corruption is not automatically repaired. Preserve the workspace
and inspect it through supported read-only commands before restoring from a
known-good source.

An absent v0.21 command normally produces `argument_parse_error` or
`command_not_implemented`; v0.22 does not provide dedicated migration codes.
Authored compatibility profiles produce `unsupported_compatibility_profile`.
Unsupported selected runner authority can produce `adapter_kind_refused`.

## Daemon Budget And Usage Refusals

`budget_id_required` and `budget_limit_required` reject incomplete CLI intake.
`runner_usage_mapping_unsupported` rejects a token ceiling when the resolved
adapter lacks a reviewed usage mapping. `budget_plan_pin_refused`,
`daemon_budget_epoch_refused`, and `daemon_budget_limit_out_of_range` reject
invalid epoch authority before daemon work starts.

`daemon_budget_immutable_limits_changed` and
`daemon_budget_clock_discontinuity` surface as their exact CLI domain-refusal
codes. They preserve the existing active epoch. Only an unrecognized durable
epoch failure maps to `daemon_budget_epoch_refused`.
`runner_usage_evidence_refused` rejects missing, decreasing, contradictory, or
wrong-session governed usage. This refusal terminalizes the epoch and suspends
new claims atomically. It does not undo an accepted claim or invent a workflow
result.

`run.budget-stop` returns `budget_stopped` with `data.budget`,
`data.dispatch_suspension`, and `data.replayed`. It creates only the
`active -> stopped` transition with terminal reason `operator_completed`.
Missing or nonfinal session usage, live or lost sessions, pending cleanup or
reservations, identity contradictions, a wrong workspace, a missing budget,
corrupt projections, and terminal conflicts refuse without mutation. An exact
replay remains successful without creating a new suspension after a legitimate
dispatch resume.

Input, output, and total token values are normalized adapter evidence for a
daemon ceiling. They are not billing, invoice, provider spend, price, or
provider rate-limit truth. Maintainers should use the
[daemon-budget](maintainers/daemon-budget-invariant-matrix.md),
[dispatch-suspension](maintainers/dispatch-suspension-invariant-matrix.md),
and [queue-closure](maintainers/queue-closure-invariant-matrix.md) matrices for
exact invariant proof.

## Runner-Session Refusals And Diagnostics

Kernel refusal `reason` values are:

- `invalid_runner_session_transition`;
- `stale_runner_session`;
- `duplicate_runner_session_completion`;
- `runner_session_authority_mismatch`;
- `runner_session_retry_forbidden`;
- `runner_session_cleanup_incomplete`;
- `runner_session_reconciliation_contradiction`.

The kernel owns those runtime meanings. They are stable refusal reasons,
persisted through the existing refusal/event/trace path when the attempted
operation is auditable. A refusal does not create a workflow result, replace a
session, release a claim, or repair state.

Stable CLI codes are `runner_session_cancel_requested`,
`runner_session_cancel_refused`, `runner_session_retry_refused`,
`runner_session_orphan_risk`, and `workspace_upgrade_required`. Cancellation
acceptance records the replay-safe request. Cancellation/retry refusals do not
perform the requested session mutation. Orphan risk is a durable safety stop,
not a cleanup success. Workspace upgrade refusal leaves the schema-6 or
schema-7 database, CAS, and runner-event sidecar byte-for-byte unchanged.

Doctor uses `runner_session_lost`, `runner_session_orphan_risk`,
`runner_session_reconciliation_unsupported`, and
`runner_session_cleanup_pending`. Doctor and all run/status/trace projections
are read-only.

Adapter errors retain adapter ownership and existing meanings:
`timeout`, `cancelled`, `invocation_failed`, `result_parse_failed`,
`input_too_large`, `output_too_large`, `redaction_refused`, and
`selected_authority_refused`. These are evidence outcomes, not workflow
terminal outcomes. Truncation remains metadata unless it prevents result
normalization.

See [Runner-session architecture](runner-session-architecture.md) for command
and projection semantics.
