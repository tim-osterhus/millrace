# Recovery, Status, Doctor, And Runner Contracts

This inventory records behavior that must remain stable before the Batch 3
Doctor, CLI status, and runner-normalization refactors, plus the later runtime
recovery split. It names direct tests where they already exist and calls out
missing characterization that should be added before source movement.

## Environmental Blocker Classification

Contract: runner/provider/environment failures stay distinguishable from
semantic stage failures. Transient classes such as provider/network/rate-limit
and reconciled timeout evidence may be retried automatically or manually when
policy allows; missing binaries, auth failures, malformed output, and semantic
stage-declared blocked results remain durable blockers unless explicitly
forced by an operator path.

Direct tests:

- `tests/runners/test_runner.py::test_normalize_preserves_reconciled_timeout_evidence_on_success`
- `tests/runners/test_runner.py::test_normalize_classifies_unreconciled_timeout_even_with_terminal_like_stdout`
- `tests/runners/test_runner.py::test_normalize_classifies_network_transport_failure_as_auto_requeue_candidate`
- `tests/runners/test_runner.py::test_normalize_classifies_missing_runner_binary_as_durable_setup_failure`
- `tests/runners/test_runner.py::test_normalize_classifies_provider_and_runner_errors`
- `tests/runtime/test_supervisor.py::test_supervisor_auto_requeues_transient_blocked_dependency_on_idle_cycle`
- `tests/runtime/test_supervisor.py::test_supervisor_does_not_auto_requeue_non_retryable_blocked_dependency`

Missing characterization before movement:

- A focused table-style test for the full failure-class retryability mapping in
  `runtime/blocked_recovery.py`.
- A direct test that unknown transport failures remain non-retryable unless
  classification is explicitly widened.

## Blocked Work Retry Eligibility

Contract: `queue retry-blocked` and daemon auto-recovery must parse the blocked
artifact through the correct family adapter, enforce destination collision
guards, preserve lineage/root-spec safety, write the family audit trail, refresh
snapshot queue depths, and refuse live daemon-owned mutation for direct CLI
commands.

Direct tests:

- `tests/runtime/test_blocked_recovery.py::test_blocked_metadata_accepts_custom_family_without_work_item_kind`
- `tests/runtime/test_blocked_recovery.py::test_blocked_metadata_blueprint_draft_includes_root_lineage`
- `tests/runtime/test_blocked_recovery.py::test_retry_blocked_task_refreshes_inventory_with_compiled_plan`
- `tests/runtime/test_blocked_recovery.py::test_retry_blocked_task_refreshes_inventory_without_compiled_plan`
- `tests/runtime/test_blocked_recovery.py::test_retry_blocked_work_item_requeues_blocked_spec_and_writes_family_audit`
- `tests/runtime/test_blocked_recovery.py::test_retry_blocked_work_item_refuses_malformed_blocked_document`
- `tests/runtime/test_blocked_recovery.py::test_retry_blocked_work_item_refuses_ambiguous_family_without_selector`
- `tests/runtime/test_blocked_recovery.py::test_retry_blocked_work_item_enforces_root_spec_guard`
- `tests/runtime/test_blocked_recovery.py::test_retry_blocked_work_item_requeues_blueprint_draft_when_family_parser_validates`
- `tests/runtime/test_blocked_recovery.py::test_retry_blocked_work_item_refuses_unsupported_family`
- `tests/cli/test_cli.py::test_queue_retry_blocked_requeues_retryable_blocked_task`
- `tests/cli/test_cli.py::test_queue_retry_blocked_requeues_family_selected_blocked_spec`
- `tests/cli/test_cli.py::test_queue_retry_blocked_refuses_non_retryable_task_without_force`
- `tests/cli/test_cli.py::test_queue_retry_blocked_refuses_live_daemon_ownership_lock`

Missing characterization before movement:

- Direct event-payload assertions for retry-blocked audit/runtime events after
  queue movement.
- Coverage for malformed custom-family JSON where the compiled adapter is
  present but the document id field is absent.

## Operator Recovery Commands

Contract: operator recovery commands use the runtime-control boundary. Daemon
owned workspaces receive mailbox commands; offline workspaces may mutate state
directly only through audited helpers. Active work mutation is deferred or
refused when it would race a running stage.

Direct tests:

- `tests/cli/test_cli.py::test_control_commands_delegate_to_runtime_control`
- `tests/cli/test_cli.py::test_namespaced_control_commands_delegate_to_runtime_control`
- `tests/cli/test_cli.py::test_planning_retry_active_command_delegates_to_runtime_control`
- `tests/cli/test_cli.py::test_top_level_clear_stale_state_alias_delegates_to_runtime_control`
- `tests/cli/test_cli.py::test_reload_config_routes_to_mailbox_when_workspace_has_daemon_owner`
- `tests/runtime/test_runtime.py::test_runtime_mailbox_retry_active_requeues_active_item_and_resets_counters`
- `tests/runtime/test_runtime.py::test_runtime_mailbox_clear_stale_state_requeues_multiple_active_artifacts`
- `tests/runtime/test_runtime.py::test_runtime_mailbox_applies_operator_intervention_before_new_work_claim`
- `tests/runtime/test_runtime.py::test_runtime_mailbox_defers_operator_intervention_until_active_run_drains`
- `tests/runtime/test_runtime.py::test_clear_stale_state_direct_clears_stale_runtime_ownership_lock`
- `tests/runtime/test_runtime.py::test_clear_stale_state_prefers_direct_path_for_stale_lock_even_if_snapshot_claims_running`

Missing characterization before movement:

- A focused mailbox-envelope failure test for unsupported recovery command
  payloads outside the large runtime test module.
- A direct test that operator recovery commands preserve open closure-target
  contracts when clearing stale active state.

## Monitor And Event Compatibility

Contract: daemon monitor output remains concise and human-oriented, while
runtime events and persisted run artifacts carry detailed machine-readable
state. Unexpected tick failures, monitor logs, lane snapshots, status markers,
and recovery events must remain stable enough for operators and tests.

Direct tests:

- `tests/cli/test_cli.py::test_run_daemon_with_monitor_basic_installs_monitor_and_prints_output`
- `tests/cli/test_cli.py::test_run_daemon_without_monitor_stays_quiet`
- `tests/cli/test_cli.py::test_run_daemon_can_write_basic_monitor_log_without_stdout_monitor`
- `tests/cli/test_cli.py::test_run_daemon_monitor_records_unexpected_tick_exit`
- `tests/cli/test_cli.py::test_status_watch_outputs_multiple_updates_with_bound`
- `tests/cli/test_cli.py::test_status_watch_can_observe_multiple_workspaces_in_one_session`
- `tests/runtime/test_runtime.py::test_runtime_writes_snapshot_status_events_and_stage_result_artifacts`
- `tests/runtime/test_runtime.py::test_runtime_single_tick_emits_stage_events_in_order`

Missing characterization before movement:

- Focused monitor rendering tests outside `tests/cli/test_cli.py` before
  extracting any CLI monitor/status renderer helpers.
- Direct assertions for auto-recovery event payload fields, not only filesystem
  side effects.

## Status Data And Rendering

Contract: `millrace status` and JSON status output must preserve current fields
for active mode, compiled plan identity/currentness, queue depths, active runs,
lanes, usage governance, failure/retry counters, runtime-effect diagnostics,
closure targets, Blueprint operator state, custom families, and blocked idle
context. Data collection may be split from rendering, but user-visible text and
JSON field names stay compatible unless a release note explicitly changes them.

Direct tests:

- `tests/cli/test_cli.py::test_status_surfaces_active_mode_and_compiled_plan_id`
- `tests/cli/test_cli.py::test_status_surfaces_baseline_manifest_identity_and_compile_currentness`
- `tests/cli/test_cli.py::test_status_surfaces_learning_plane_depth_and_status`
- `tests/cli/test_cli.py::test_status_surfaces_latest_operator_intervention`
- `tests/cli/test_cli.py::test_status_surfaces_multiple_active_runs_by_plane`
- `tests/cli/test_cli.py::test_status_surfaces_usage_governance_pause_context`
- `tests/cli/test_cli.py::test_status_surfaces_failure_class_and_retry_counters`
- `tests/cli/test_cli.py::test_status_surfaces_latest_runtime_effect_failure_metadata`
- `tests/cli/test_cli.py::test_status_surfaces_blueprint_repair_runtime_effect_diagnostics`
- `tests/cli/test_cli.py::test_status_keeps_blueprint_repair_diagnostics_after_mechanic_apply_runtime_effect`
- `tests/cli/test_cli.py::test_status_uses_latest_prior_runtime_effect_metadata_when_last_stage_is_recovery`
- `tests/cli/test_cli.py::test_status_json_surfaces_blocked_idle_context_and_runtime_error_report`
- `tests/cli/test_cli.py::test_status_surfaces_closure_target_state`
- `tests/cli/test_cli.py::test_status_prefers_actionable_closure_target_when_blocked_targets_remain`
- `tests/cli/test_cli.py::test_status_surfaces_blueprint_operator_state`
- `tests/cli/test_cli.py::test_status_and_queue_ls_count_queued_blueprint_drafts`
- `tests/cli/test_cli.py::test_status_uses_inventory_for_custom_family_visibility`

Missing characterization before movement:

- A view-model-level test that exercises status collection without relying on
  the final terminal line order.
- A focused JSON status schema test for high-value fields before splitting
  serialization from rendering.

## Doctor Check Behavior

Contract: `millrace doctor` remains read-only, deterministic, and stable in
issue codes/order for workspace layout, queue parseability, Blueprint state,
closure lineage/root-source contracts, snapshot reconciliation, mode assets,
runner posture, daemon locks, and baseline manifests.

Direct tests:

- `tests/workspace/test_doctor.py::test_doctor_passes_for_bootstrapped_workspace`
- `tests/workspace/test_doctor.py::test_doctor_flags_invalid_status_and_unparseable_queue_artifact`
- `tests/workspace/test_doctor.py::test_doctor_flags_queue_filename_and_document_id_mismatch`
- `tests/workspace/test_doctor.py::test_doctor_validates_blueprint_draft_queue_parseability`
- `tests/workspace/test_doctor.py::test_doctor_flags_legacy_root_keyed_blueprint_manifest`
- `tests/workspace/test_doctor.py::test_doctor_flags_unresolved_blueprint_draft_manifest_reference`
- `tests/workspace/test_doctor.py::test_doctor_flags_duplicate_task_lifecycle_state`
- `tests/workspace/test_doctor.py::test_doctor_flags_closure_lineage_drift`
- `tests/workspace/test_doctor.py::test_doctor_reports_missing_closure_root_source`
- `tests/workspace/test_doctor.py::test_doctor_reports_unsupported_closure_root_source_kind`
- `tests/workspace/test_doctor.py::test_doctor_reports_closure_root_source_legacy_mismatch`
- `tests/workspace/test_doctor.py::test_doctor_warns_stopped_daemon_with_open_closure_and_graph_backlog`
- `tests/workspace/test_doctor.py::test_doctor_flags_snapshot_reconciliation_problems`
- `tests/workspace/test_doctor.py::test_doctor_flags_invalid_mode_assets_deterministically`
- `tests/workspace/test_doctor.py::test_doctor_warns_when_resolved_runner_binary_is_unavailable`
- `tests/workspace/test_doctor.py::test_doctor_reports_active_runtime_ownership_lock_health`
- `tests/workspace/test_doctor.py::test_doctor_flags_stale_runtime_ownership_lock`
- `tests/workspace/test_doctor.py::test_doctor_flags_missing_baseline_manifest`
- `tests/cli/test_cli.py::test_doctor_command_surfaces_workspace_diagnostics`
- `tests/cli/test_cli.py::test_doctor_warns_on_latest_blueprint_repair_runtime_effect_context`
- `tests/cli/test_cli.py::test_doctor_keeps_blueprint_repair_context_after_mechanic_apply_runtime_effect`

Missing characterization before movement:

- Unit-level tests for individual future check registry entries so a Doctor
  split does not depend only on full-command CLI output.
- A registry ordering test if checks become registered diagnostics.

## Runner Normalization Success And Failure Paths

Contract: runner normalization is deterministic and converts raw adapter output
into `StageResultEnvelope` without losing request identity, request kind,
root-source/closure metadata, capability evidence, token usage, timeout
evidence, terminal result legality, artifact safety, or failure origin.

Direct tests:

- `tests/runners/test_runner.py::test_normalize_prefers_structured_terminal_result_file`
- `tests/runners/test_runner.py::test_normalize_falls_back_to_final_stdout_terminal_token`
- `tests/runners/test_runner.py::test_normalize_uses_request_policy_not_global_stage_table`
- `tests/runners/test_runner.py::test_normalize_uses_root_spec_identity_for_closure_target_requests`
- `tests/runners/test_runner.py::test_normalize_classifies_illegal_terminal_result_for_stage`
- `tests/runners/test_runner.py::test_runner_artifacts_surface_request_kind_and_closure_target_identity`
- `tests/runners/test_runner.py::test_normalize_classifies_conflicting_terminal_results`
- `tests/runners/test_runner.py::test_normalize_classifies_missing_terminal_result`
- `tests/runners/test_runner.py::test_normalize_classifies_illegal_result_class_in_structured_output`
- `tests/runners/test_runner.py::test_normalize_rejects_summary_artifact_traversal_outside_run_dir`
- `tests/runners/test_runner.py::test_normalize_rejects_raw_result_identity_mismatch`
- `tests/runners/test_runner.py::test_normalize_persists_request_context_and_failure_origin_metadata`
- `tests/runners/test_runner.py::test_normalize_output_is_deterministic`
- `tests/runners/test_runner.py::test_normalize_surfaces_preferred_troubleshoot_report_artifact_when_present`
- `tests/runners/test_runner.py::test_normalize_preserves_token_usage_and_event_log_artifacts`
- `tests/runners/test_runner.py::test_normalize_rejects_completed_result_with_missing_capability_evidence`

Missing characterization before movement:

- Separate parser-table tests for structured terminal-result file variants
  before extracting parser modules.
- Direct tests that distinguish runner process failure from output parsing
  failure after error-classification extraction.

## Model Alias Provenance On Runner Failures

Contract: model alias resolution/provenance must remain visible on stage
requests and normalized failure envelopes. Failure paths must not drop the
selected model, alias source, loop/stage override source, or request context
identity.

Direct tests:

- `tests/runtime/test_stage_requests.py::test_blueprint_stage_request_carries_model_alias_provenance`
- `tests/runners/test_runner.py::test_normalize_persists_request_context_and_failure_origin_metadata`
- `tests/runners/test_runner.py::test_render_stage_request_context_lines_covers_all_stage_run_request_fields`
- `tests/compilation/test_model_alias_resolution.py::test_stage_alias_overrides_stage_config`
- `tests/compilation/test_model_alias_resolution.py::test_loop_alias_applies_to_every_node_in_loop`
- `tests/compilation/test_model_alias_resolution.py::test_unknown_stage_alias_warns_and_falls_back_to_loop_alias`
- `tests/compilation/test_model_alias_resolution.py::test_invalid_global_alias_warns_and_falls_back_to_builtin_standard`
- `tests/compilation/test_model_alias_resolution.py::test_alias_values_are_trimmed_before_materialization`

Missing characterization before movement:

- A focused runner-normalization failure test that asserts every model alias
  provenance field is preserved when terminal output is malformed.
- A status/run-inspection assertion that provenance stays visible after a
  normalized runner failure, not only on success.

## Recommended Batch 3 Order

The safest Batch 3 packet is the CLI status data/render split if it begins by
extracting a view model with no output change. Doctor registry should follow,
because its checks are read-only but issue ordering matters. Runner
normalization should come after direct parser/provenance tests are added or
identified, because it sits on the external adapter/runtime boundary.
