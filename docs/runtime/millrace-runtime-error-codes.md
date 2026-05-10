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
| `recon_handoff_invalid` | Recon returned a handoff terminal result, but the required `recon_packet.md` or generated task/spec artifact was missing, malformed, or inconsistent with the terminal result. The runtime blocks the active probe instead of routing that probe into Planner, Manager, or Mechanic. | Run `millrace status show --format json`, read `latest_runtime_error_report_path`, inspect the run's `recon_packet.md` and generated artifact, then fix the probe/request or rerun Recon. |

## Interpretation Notes

- These codes describe runtime-owned failures, not stage-owned failures.
- The stage itself may still have exited `0` and emitted a valid terminal marker.
- The recovery-stage prompt should treat `runtime_error_report_path` as the primary evidence source.
- The code narrows the diagnosis; the report provides the concrete run-specific details.
- `recon_handoff_invalid` is intentionally not auto-routed into Mechanic. Recon
  handoff artifacts are ownership boundaries; invalid handoff output should
  block the probe with evidence rather than mutate it into unrelated planning
  work.
