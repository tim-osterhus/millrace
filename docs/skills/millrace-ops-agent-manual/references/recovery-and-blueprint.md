# Recovery And Blueprint

## Contents

- Runtime error reports
- Retryable blocked work
- Closure and Arbiter
- Blueprint diagnostics
- Recovery pitfalls

## Runtime Error Reports

If the runtime surfaces a recovery-stage request with `runtime_error_code`,
treat it as runtime-owned evidence. Read in order:

1. `runtime_error_report_path`
2. `runtime_error_catalog_path`

Do not invent semantics for runtime error codes from memory.

Recon handoff failures are blocked-probe cases. If status shows
`current_failure_class: recon_handoff_invalid`, run:

```bash
millrace status show --format json --workspace <workspace>
```

Then inspect `latest_runtime_error_report_path`, the run's `recon_packet.md`,
and any generated task/spec artifact. Recon emitted an invalid typed handoff;
do not manually route that probe into Planner, Manager, or Mechanic.

## Retryable Blocked Work

Blocked dependency auto-recovery is enabled by default but conservative. It
acts only when queued same-lineage execution work is stranded behind a blocked
predecessor whose latest blocked metadata classifies the failure as:

- `network_unavailable`
- `provider_unavailable`
- `provider_rate_limited`
- `runner_timeout`

Cooldown and retry-budget gates still apply.

Missing runner binaries, auth failures, malformed terminal output,
stage-authored blocked states, and unknown transport failures require operator
review or explicit `queue retry-blocked --force`. Use `--force` only after
inspecting the artifact and accepting the override.

## Closure And Arbiter

Arbiter closure is rooted in a generic root source plus a root spec. Use
`Root-Idea-ID` only for idea-rooted work. Probe-rooted work should preserve
`Root-Intake-Kind: probe` and `Root-Intake-ID: <probe-id>`.

For Arbiter closure audits, inspect `closure_evidence_window_path` before
trusting older verdicts. Criterion evidence provenance means:

- `fresh`: collected for the current audit
- `revalidated`: explicitly checked against current source
- `historical_only`: pre-watermark context
- `missing`: cannot support a current pass/fail decision

After newer same-lineage remediation exists, historical-only evidence is not
enough to close a target.

Treat Arbiter remediation as runtime-owned. Arbiter reports gaps and guidance;
the runtime creates closure remediation incidents with
`created_by=millrace-runtime` and
`trigger_metadata.runtime_created=true`. A
`closure_repeated_remediation_blocked` status means the guard detected a
planning-only loop or stale remediation-loop evidence. Inspect the latest
Arbiter report, freshness window, and same-lineage execution completions.

If `doctor` reports `daemon_stopped_with_open_graph_work`, the daemon is
stopped while an open closure target still has compiled-family backlog or
blockers. Confirm `process_running`, inspect `queue ls`, then restart unless a
separate provider/network outage explains the stop.

Consultant `NEEDS_PLANNING` handoffs should produce same-lineage planning
incidents under an open closure target. The runtime should adopt the valid
incident declared by Consultant and should create a generic fallback only when
that artifact is absent or invalid. If both remain visible, inspect
`runtime_handoff_incident_registered` and authored-rejection events before
changing queue state. If an older workspace idles with a blocked source task
and lineage-less incoming incident, repair incident lineage rather than
bypassing the root.

## Blueprint Diagnostics

Use `millrace status --workspace <workspace>` to inspect
`blueprint_draft_*`, `blueprint_packet_*`, `blueprint_critique_*`,
`blueprint_evaluation_count`, and `blueprint_promotion_count` before opening
raw files.

Use `millrace runs show <run_id>` on Blueprint stage runs to inspect
`runtime_effect_handler_id`, `runtime_effect_operation_id`,
`runtime_effect_runner_id`, `runtime_effect_legacy_handler_id`,
`runtime_effect_decision`, `runtime_effect_failure_class`,
`runtime_effect_failure_message`, `runtime_effect_mutation_phase`,
`runtime_effect_failure_policy_id`, `runtime_effect_recovery_action`,
`runtime_effect_source_lifecycle_*`, and `runtime_effect_created_path`.

Treat `runtime_effect_operation_id` plus `runtime_effect_runner_id` as dispatch
authority. Handler ids are compatibility metadata and may be absent for
operation-only effects.

Compare `artifact_status` and `runtime_outcome`. `artifact_status: valid`
means the stage-result artifact parsed; `runtime_outcome: blocked` means
routing or runtime effect still failed.

Canonical JSON outputs win over legacy fallback filenames. Malformed canonical
files are intentional blockers, not a reason to hand-edit fallback markdown.

Blueprint lineage ids are metadata, not storage keys. New manifests are stored
by `manifest_id`; legacy root-keyed manifests are resolved by embedded
`manifest_id`. Same-root remediation manifests are expected when Arbiter gaps
trigger another Manager Blueprint pass under the original `root_spec_id`.

`blueprint_manifest_duplicate` should be diagnosed by comparing `manifest_id`
and normalized manifest content. Do not block or edit solely because two
manifests share `root_spec_id`.

## Blueprint Recovery Rules

- Manager Blueprint runtime-effect failures route by class.
- Manager missing, malformed, schema-invalid, manifest/draft-mismatched,
  duplicate id, invalid source lifecycle, and partial-mutation failures block
  conservatively.
- Evaluator approval pre-mutation `generated_task_missing` and
  `generated_task_invalid` route to `mechanic_blueprint`.
- Other approval replay conflicts and partial mutations remain conservative
  blockers unless a declared reconciliation handler proves equivalent durable
  state.
- Mechanic Blueprint must emit `MECHANIC_BLUEPRINT_COMPLETE` only with
  `blueprint_repair_decision.json`; `mechanic_report.md` alone is evidence,
  not operational state.
- `repaired_generated_task.json` is valid only with
  `repair_action=apply_repaired_generated_task`; unsafe recovery must emit
  `BLOCKED`.
- Manager replay is idempotent when durable manifest and draft outputs already
  exist with equivalent content.
- Contractor replay is idempotent only when existing candidate packet and
  markdown outputs are equivalent.
- Evaluator approval replay is idempotent only when existing evaluation,
  approved packet, approved markdown, generated task, and promotion outputs are
  equivalent.

Planner disposition controls whether the active source continues to Manager:

- `active_source_ready_for_manager`: continue the same source
- `emitted_child_specs`: resolve or complete the source after validating child
  specs and do not send it to Manager
- `blocked`: preserve normal blocked recovery

Missing disposition is a source blocker.

A rejected Blueprint is not a failed daemon run by itself. Evaluator rejection
should leave an open critique and route the same active draft back to
Contractor. An approved Blueprint should create an approved packet, evaluation,
promotion record, and generated task. Arbiter remains suppressed until that
generated task completes or blocks.

Blueprint implementation APIs are extension-owned. Use
`millrace_ai.extensions.builtin.blueprint.*` modules for Blueprint contracts,
status, context, state, Doctor, and operation-runner helpers.

## Recovery Pitfalls

- Treating Contractor Blueprint as an implementation role. Contractor emits a
  packet; Builder edits source only after Evaluator approval promotes a task.
- Treating a planning gap report as permission to hand-create incoming
  incidents.
- Manually moving blocked files instead of using `queue retry-blocked`,
  cancellation, lineage repair, or runtime recovery.
- Trusting historical Arbiter evidence after newer remediation landed.
- Replaying bad intake rather than canceling it and adding corrected intake.
