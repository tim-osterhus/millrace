# Millrace Runtime Error Codes

This catalog defines the stable runtime-owned error codes used when a stage has already returned a legal terminal result, but the runtime itself then hits an exception while persisting status, applying routing, or finalizing work-item state.

These codes are for `Mechanic` and `Troubleshooter` consumption. If a recovery-stage request does not include `runtime_error_code`, this document is not relevant to that run.

## Codes

| Code | Emitted when | Typical first inspection |
| --- | --- | --- |
| `planning_work_item_completion_conflict` | A planning-stage run finished successfully, but the runtime could not finalize the active planning work item because it had already been moved out of `specs/active/` or `incidents/active/`. | Read `runtime_error_report_path`, then inspect the active/done/blocked planning queues implicated by the report. |
| `execution_work_item_completion_conflict` | An execution-stage run finished successfully, but the runtime could not finalize the active execution work item because it had already been moved out of `tasks/active/`. | Read `runtime_error_report_path`, then inspect the active/done/blocked task queues implicated by the report. |
| `planning_post_stage_apply_failed` | A planning-stage run returned a legal terminal result, but another runtime-owned post-stage step failed after normalization. | Read `runtime_error_report_path`, then inspect the named exception, router action, and referenced stage-result artifact. |
| `execution_post_stage_apply_failed` | An execution-stage run returned a legal terminal result, but another runtime-owned post-stage step failed after normalization. | Read `runtime_error_report_path`, then inspect the named exception, router action, and referenced stage-result artifact. |

## Interpretation Notes

- These codes describe runtime-owned failures, not stage-owned failures.
- The stage itself may still have exited `0` and emitted a valid terminal marker.
- The recovery-stage prompt should treat `runtime_error_report_path` as the primary evidence source.
- The code narrows the diagnosis; the report provides the concrete run-specific details.
