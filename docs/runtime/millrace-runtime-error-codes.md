# Millrace Runtime Error Codes

This catalog defines the stable runtime-owned error codes used when a stage has already returned a legal terminal result, but the runtime itself then hits an exception while persisting status, applying routing, or finalizing work-item state.

These codes are for runtime diagnostics and, when routed into a repair stage,
for `Mechanic` and `Troubleshooter` consumption. If a recovery-stage request or
status payload does not include `runtime_error_code` or
`current_failure_class`, this document is not relevant to that run.

## Codes

| Code | Emitted when | Typical first inspection |
| --- | --- | --- |
| `planning_work_item_completion_conflict` | A planning-stage run finished successfully, but the runtime could not finalize the active planning work item because it had already been moved out of `specs/active/` or `incidents/active/`. | Read `runtime_error_report_path`, then inspect the active/done/blocked planning queues implicated by the report. |
| `execution_work_item_completion_conflict` | An execution-stage run finished successfully, but the runtime could not finalize the active execution work item because it had already been moved out of `tasks/active/`. | Read `runtime_error_report_path`, then inspect the active/done/blocked task queues implicated by the report. |
| `planning_post_stage_apply_failed` | A planning-stage run returned a legal terminal result, but another runtime-owned post-stage step failed after normalization. | Read `runtime_error_report_path`, then inspect the named exception, router action, and referenced stage-result artifact. |
| `execution_post_stage_apply_failed` | An execution-stage run returned a legal terminal result, but another runtime-owned post-stage step failed after normalization. | Read `runtime_error_report_path`, then inspect the named exception, router action, and referenced stage-result artifact. |
| `recon_handoff_invalid` | Recon returned a handoff terminal result, but the required `recon_packet.md` or generated task/spec artifact was missing, malformed, or inconsistent with the terminal result. When the active Planning graph declares a default runtime repair node and attempts remain, the runtime routes the active probe to that repair node; otherwise it blocks the probe with evidence. | Run `millrace status show --format json`, read `latest_runtime_error_report_path`, inspect the run's `recon_packet.md` and generated artifact, then inspect the active repair-stage request or blocked probe. |
| `compiled_plan_stale` | Result application found an active-run launch plan that is no longer available or no longer matches the launch fingerprint. | Inspect the active run's `compiled_plan_id` and `compiled_plan_fingerprint`, then inspect archived compiled plans under runtime state. |
| `workspace_integrity_failure` | Runtime-owned workspace authority, launch-plan identity, or persisted state invariants are inconsistent enough that routing cannot continue safely. | Read `latest_runtime_error_report_path`, inspect the cited active run, lane state, snapshot, and archived compiled plan. |

## Interpretation Notes

- These codes describe runtime-owned failures, not stage-owned failures.
- The stage itself may still have exited `0` and emitted a valid terminal marker.
- The recovery-stage prompt should treat `runtime_error_report_path` as the primary evidence source.
- The code narrows the diagnosis; the report provides the concrete run-specific details.
- `millrace status` and `millrace status show --format json` also surface the
  latest `failure_origin` when one is known.
- Default runtime repair routing is graph-authoritative. If the compiled active
  graph declares `runtime_failure_recovery.default_repair_node_id`, unclassified
  runtime-owned Planning and Execution failures route to that repair node after
  any more specific runtime failure policy has had a chance to apply.
- `recon_handoff_invalid` is a runtime-owned Planning failure. In
  `planning.standard` it routes to `mechanic`; in `planning.blueprint` it routes
  to `mechanic_blueprint`. Attempt counters still cap repeated repair loops, and
  the probe blocks with the runtime error report when the threshold is exhausted.
- `learning.standard` currently declares no default runtime repair node.

## Failure Origins

`failure_origin` is a lower-level classifier for where the runtime believes an
edge failure came from. It is not a replacement for `RuntimeErrorCode`; it is a
diagnostic dimension used in runtime events, runtime error context, normalized
stage results, and operator status output.

Current origins:

- `model_provider_unavailable`
- `network_unavailable`
- `request_context_provider_failure`
- `prompt_render_failure`
- `runtime_primitive_exception`
- `document_adapter_parse_failure`
- `document_adapter_validation_failure`
- `filesystem_io_failure`
- `workspace_integrity_failure`

## Runtime Effect Failure Classes

Workflow primitive runtime effects can also return failure classes that are not
members of the stable `RuntimeErrorCode` enum. The current foundation failure
class is `runtime_effect_destination_missing`: it is emitted as a runtime event
when an effect handler claims it created a destination artifact but that path is
missing before source lifecycle mutation. The runtime then requests recovery
instead of moving the source work item to done or blocked.

Treat these failure classes as runtime-owned diagnostics. They describe a
failed effect application boundary, not a legal stage terminal result and not a
new queue state.
