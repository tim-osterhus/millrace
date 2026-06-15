# Monitoring And Intervention

## Contents

- Status and run inspection
- Monitor behavior
- Intervention commands
- Optional web observation
- Bad-intake cleanup

## Status And Run Inspection

Use this rhythm:

1. `millrace status --workspace <workspace>` for current snapshot state.
2. `millrace queue ls --workspace <workspace>` for queue shape.
3. `millrace queue show <work_item_id> --workspace <workspace>` for one queued
   item.
4. `millrace runs ls --workspace <workspace>` for recent runs.
5. `millrace runs show <run_id> --workspace <workspace>` for evidence.
6. `millrace runs trace <run_id> --workspace <workspace>` for concrete stage
   path and router decisions.
7. `millrace runs tail <run_id> --workspace <workspace>` for primary artifacts.

Interpret status markers literally:

- active stages show running markers such as `### CHECKER_RUNNING`
- inactive planes fall back to the latest terminal marker or `### IDLE`
- Learning-enabled workspaces expose learning queue depth and
  `learning_status_marker`
- `lane: ...` lines show durable scheduler-lane state
- `active_run_count` and `active_run: ...` show canonical active lanes and
  launch-plan identity
- older `active_plane` and `active_stage` fields are foreground projections
- `latest_runtime_failure_origin` is runtime-owned diagnostic evidence
- `pause_sources: operator` means an operator pause is still active
- `pause_sources: usage_governance` means usage governance is blocking dispatch

Use `millrace status show --format json --workspace <workspace>` when
machine-readable fields such as `blocked_idle`, `current_failure_class`, or
`latest_runtime_error_report_path` matter.

## Monitor Behavior

`millrace status watch` is monitor-only and does not acquire runtime ownership
locks.

`millrace run daemon --monitor basic` is live daemon output. It uses compact
stage labels, short run handles, and omits unknown token usage. Full run ids,
artifacts, capability metadata, and durable details remain available through
`millrace runs ...`.

The basic monitor prints the first `idle reason=no_work` line immediately, then
treats repeated no-work idles as a 6-hour heartbeat until runtime activity or a
different idle reason appears. Durable idle events are separately throttled as
transition and heartbeat records. Idle suppression state is process memory, not
a `RuntimeSnapshot` field.

Use `millrace run daemon --monitor none --monitor-log <path>` for quiet
foreground operation with a persisted monitor stream.

## Intervention Commands

Use intervention only when runtime state justifies it:

- `control pause` stops further ticks cleanly.
- `control resume` clears only operator pause; it does not override active
  usage-governance blockers.
- `control stop` requests daemon shutdown.
- unscoped `control retry-active` is appropriate only when exactly one active
  work item exists.
- `planning retry-active` is for planning-plane retry intent.
- `clear-stale-state` recovers stale active files and should preserve open
  closure targets.
- `queue retry-blocked <WORK_ITEM_ID> --family <FAMILY_ID> --reason
  "<reason>"` requeues one blocked task, probe, spec, incident,
  learning-request, or parseable graph-family artifact through audited recovery.
- `queue cancel <WORK_ITEM_ID> --kind task|probe|spec|incident --reason
  "<reason>"` archives bad queued or blocked intake as canceled.
- `queue supersede <OLD_TASK_ID> --replacement <NEW_TASK_ID> --reason
  "<reason>"` carries corrected task work forward.
- `queue retarget-dependency <TASK_ID> --from <OLD> --to <NEW> --reason
  "<reason>"` rewrites one queued dependent.
- `incident resolve <INCIDENT_ID> --reason "<reason>"` confirms no more
  planning work is needed.
- `incident cancel <INCIDENT_ID> --reason "<reason>"` invalidates stale or bad
  incident intake.
- `incident archive-invalid <FILENAME> --reason "<reason>"` archives one
  unparsable incoming incident artifact.
- `queue repair-lineage --root-spec-id <ROOT_SPEC_ID>` previews
  stopped-daemon closure lineage repair; add `--apply` only with no live owner
  or active stage.

Operator intervention commands archive rather than delete files, append
`interventions.jsonl`, emit runtime events, and refresh queue-depth snapshots.
When a daemon owns the workspace, they mailbox-route and apply at a safe
no-active-run boundary.

Use `approvals approve <APPROVAL_ID> --reason "<reason>"` or
`approvals deny <APPROVAL_ID> --reason "<reason>"` only after inspecting the
pending execution capability approval.

## Bad-Intake Cleanup

For bad intake:

1. Pause if needed.
2. Add or confirm the corrected replacement task.
3. Supersede the bad blocked or queued task.
4. Retarget or cancel stale queued dependents.
5. Cancel stale planning incidents.
6. Inspect `queue ls`, `status`, and `doctor`.
7. Resume only when remaining claimable work is correct.

If `doctor` reports `duplicate_task_lifecycle_state`, inspect the named task
across `tasks/queue/`, `tasks/active/`, `tasks/done/`, and `tasks/blocked/`.
Same-root blocked predecessors are automatically retired only after a same-ID
continuation reaches `done`.

## Optional Web Observation

`millrace-web` is a separate optional package, not part of the base
`millrace-ai` wheel.

```bash
millrace-web serve --workspace <workspace> --view detail
millrace-web serve --workspace <workspace> --view flow
```

The dashboard is read-only. It should not acquire daemon ownership locks,
clear queue files, approve capabilities, pause/resume/stop daemons, or write
runtime state. Use supported `millrace ...` CLI commands for mutation.
