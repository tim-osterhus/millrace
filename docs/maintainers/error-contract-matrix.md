# Maintainer Error Contract Matrix

This maintainer record maps public error and refusal families to their source
owners and executable characterization tests. The operator-facing contract is
[Errors and refusals](../errors.md).

Use `--json` for automation. Text output is intended for people. Exit status is
a coarse category; the JSON `code` field is the primary discriminator for a
CLI result or error. A domain refusal is an expected governed outcome, not
necessarily a crash.

## Authority Model

The error surface follows the same ownership boundary as execution:

1. The compiler diagnoses authored workflow, package, and selected-authority
   problems before admission.
2. The kernel decides and applies runtime transitions. A refusal reason records
   why an input was not accepted.
3. A runner emits candidate evidence only. Adapter success or failure cannot
   decide terminal legality, route work, or close work.
4. Operator commands build explicit inputs and submit them through audited
   paths. They do not mutate runtime authority directly.
5. The substrate persists accepted authority or refuses inconsistent durable
   state. It does not guess or silently repair corruption.

Compiler parity is **N/A** for runtime-only failures such as dispatch
projection, daemon lifecycle, adapter invocation, evidence conversion, and
persistence corruption. Those conditions cannot be expressed or eliminated by
validating authored workflow source.

## Stability Rules

- **Public stable:** automation may branch on the documented field and code in
  v0.22. Message prose and unspecified detail fields remain explanatory.
- **Public category only:** the documented outer category is stable; internal
  exception types, messages, and corruption details are not.
- **Internal:** useful for debugging, but not a compatibility contract.
- **Release-note-only:** describes an intentionally absent v0.21 surface, not a
  callable v0.22 API.

Expected usage, domain, persistence, and runner failures are rendered without
a traceback. Do not parse human-readable `message`, free-form `details.error`,
adapter diagnostic text, or `SelectedAssetMaterializationError` text.

## CLI Envelope

`CliSuccess` JSON objects have exactly `ok`, `command`, `code`, `message`, and
`data`. `CliError` JSON objects have exactly `ok`, `command`, `code`, `message`,
and `details`. The numeric process exit status is not repeated in the JSON
object.

| Exit status | `ExitCode` | Meaning |
| --- | --- | --- |
| `0` | `SUCCESS` | The command completed. A successful doctor command may still report unhealthy findings in its data. |
| `1` | `INTERNAL_ERROR` | An unexpected CLI implementation failure occurred. Retry once, then report it without relying on traceback output. |
| `2` | `CLI_USAGE` | Arguments, JSON input, actor identity, or local command options are invalid. Correct the request before retrying. |
| `3` | `DOMAIN_REFUSAL` | The request was understood but governed authority refused it. Inspect the JSON `code` and current status. |
| `4` | `PERSISTENCE_FAILURE` | Local state is absent, unsupported, or corrupt. Use workspace, package, status, and doctor commands before low-level file surgery. |
| `5` | `RUNNER_FAILURE` | The selected adapter attempt failed or its output could not become candidate evidence. No terminal outcome was accepted. |

Example automation should branch on `code`, not `message`:

```json
{"ok":false,"command":"workspace.check","code":"store_not_initialized","message":"SQLite runtime store is not initialized.","details":{}}
```

## Contract Matrix

Paths in **Source owner** are relative to `src/millrace`. Test names are exact
characterization or behavior proof. Detailed inventories follow the matrix.

| Surface | Source owner | Public field | Exact stable code/family and examples | Public stability | Operator meaning/action | Authority boundary | Test proof | Docs location |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CLI process/result envelope | `adapters/cli/output.py` | process `exit_code`; JSON `code` | Exit `0`-`5`; success/error shapes; `ok`, `help`, `internal_error` | Public stable | Use the exit status for coarse routing and JSON `code` for the specific result. | Rendering only; no workflow authority. | `tests/cli/test_cli_output_contract.py::test_json_success_and_error_contract_are_single_objects` | [CLI Envelope](#cli-envelope) |
| CLI parser, help, version, actor | `adapters/cli/main.py` | JSON `code` | `help`, `ok`, `argument_parse_error`, `invalid_actor_id`, `command_not_implemented` | Public stable | Correct usage or select an implemented command. | Parsing precedes state access. | `tests/cli/test_cli_output_contract.py::test_json_argument_parse_errors_are_single_error_objects`; `::test_json_help_renders_single_success_objects`; `::test_actor_id_validation_rejects_blank_without_state_access` | [Command Codes](#command-codes) |
| Workspace open and CAS | `adapters/cli/context.py`, `adapters/cli/main.py` | CLI JSON `code` | `store_not_initialized`, `cas_root_not_initialized`, `substrate_error` | Public stable JSON codes; detail text internal | Initialize the workspace or inspect durable state; do not create guessed CAS content. | Compiler parity: N/A. The substrate opens or refuses durable truth. | `tests/cli/test_cli_workspace_commands.py::test_workspace_check_is_read_only_and_refuses_missing_store`; `tests/cli/test_cli_output_contract.py::test_daemon_uninitialized_store_returns_stable_error`; `::test_substrate_failures_are_bounded_json_errors` | [Persistence](#persistence-and-corruption) |
| Queue | `adapters/cli/queue.py`, `operator/intake.py` | JSON `code` | `invalid_payload_source`, `invalid_payload_json`, `payload_file_unreadable`, plus operator-input reasons | Public stable | Fix the payload source/schema or selected queue/plan guard, then resubmit with audited identity. | Operator builds input; kernel decides/applies. | `tests/cli/test_cli_queue_commands.py::test_queue_enqueue_requires_exactly_one_payload_source`; `tests/operator/test_kp_0005_intake.py::test_build_enqueue_work_rejects_operator_shape_errors` | [Command Codes](#command-codes) |
| Status, runs, trace | `adapters/cli/status.py`, `operator/status.py` | JSON `code` | `run_not_found`, `invalid_max_events`, `invalid_plan_fingerprint` | Public stable | Correct the query or inspect current status. | Read-only projection; compiler parity: N/A. | `tests/cli/test_cli_status_commands.py::test_negative_max_events_refuses_before_store_load`; `::test_trace_show_supports_recent_and_run_specific_projection` | [Command Codes](#command-codes) |
| Waits and interventions | `adapters/cli/interventions.py`, `operator/intake.py` | JSON `code` / refusal reason | `unknown_operator_wait`, `operator_wait_resolution_forbidden`, `invalid_operator_wait_target`, `invalid_intervention_target`, `unknown_intervention_option`, `intervention_option_kind_mismatch`, `intervention_policy_mismatch` | Public stable | Refresh the wait, choose a declared option, and submit the required payload and plan guard. | Operator submits audited input; kernel owns acceptance. | `tests/operator/test_vendor_selection_operator_wait.py::test_operator_wait_refuses_duplicate_stale_wrong_plan_or_status_decisions`; `tests/cli/test_cli_intervention_commands.py::test_intervention_cli_matches_builder_payload_and_reason_contract` | [Operator Input Refusals](#operator-input-refusals) |
| Dispatch claim | `adapters/cli/dispatch.py`, `kernel/decision.py` | JSON `code` / transition reason | Claim refusal families including `workspace_paused`, `lineage_quarantined`, `operator_wait_active`, `dependency_not_ready`, `stale_activation`, and capability refusals | Public stable | Resolve the reported policy, wait, dependency, generation, or capability condition. | Kernel decides claim legality. Compiler parity: N/A for current-state conditions. | `tests/cli/test_cli_dispatch_commands.py::test_ready_candidate_claim_reload_aftermath_is_stable`; `tests/operator/test_dispatch_projection.py::test_ready_dispatch_claim_refusal_diagnostic_mapping_is_dispatch_owned` | [Transition Refusals](#transition-refusals) |
| `dispatch.show` | `operator/dispatch.py`, `adapters/cli/dispatch.py` | JSON `code` | `unknown_run`, `run_observed`, `work_item_closed`, `missing_activation`, `missing_work_item`, `missing_admitted_plan`, `plan_ref_mismatch`, `run_activation_drift`, `missing_stage_kind`, `graph_stage_runner_drift`, `missing_selected_asset`, `missing_selected_schema`, `graph_node_missing` | Public stable | Re-query status. Corrupt-authority codes require restoring a known-good workspace, not manual repair. | Read-only runtime projection; compiler parity: N/A. | `tests/operator/test_dispatch_projection.py::test_dispatch_projection_refuses_corrupt_or_inactive_runs`; `tests/cli/test_cli_dispatch_commands.py::test_dispatch_show_is_read_only_and_refuses_unknown_run` | [Dispatch And Readiness](#dispatch-and-readiness) |
| Ready dispatch projection | `operator/dispatch.py` | `reason_code` | `missing_work_item`, `plan_ref_mismatch`, `already_claimed`, `stale_generation`, `missing_admitted_plan`, `work_item_closed`, `missing_selected_schema`, plus mapped policy/corruption reasons | Public stable | Treat `non_candidate` as current state, `policy_refusal` as an actionable gate, and `corrupt_authority` as persistence/authority failure. | Read-only runtime projection; compiler parity: N/A. | `tests/operator/test_dispatch_projection.py::test_ready_dispatch_candidate_projection_reports_reason_coded_diagnostics`; `::test_ready_dispatch_claim_refusal_diagnostic_mapping_is_dispatch_owned` | [Dispatch And Readiness](#dispatch-and-readiness) |
| Workspace doctor | `adapters/cli/doctor.py` | JSON `code` | `doctor_ok`; common open failures retain persistence codes | Public stable | Inspect returned checks even when the command succeeds. | Read-only projection; no repair authority. | `tests/cli/test_cli_doctor_commands.py::test_doctor_is_minimal_generic_read_only_projection` | [Command Codes](#command-codes) |
| Plan commands | `adapters/cli/plans.py` | JSON `code` | `plan_not_admitted`, `compiled_plan_export_unreadable`, `compiled_plan_export_invalid`, `plan_fingerprint_drift`, plus transition/package-selection reasons | Public stable | Recompile or verify the exact export and fingerprint before admission/selection. | Compiler creates selected authority; kernel admits/selects it. | `tests/cli/test_cli_plan_commands.py::test_plan_admit_refuses_fingerprint_drift_without_partial_persist`; `::test_select_default_unknown_fingerprint_is_domain_refusal` | [Command Codes](#command-codes) |
| Package CLI | `adapters/cli/packages.py` | JSON `code` | `package_archive_unreadable`, `package_command_succeeded`, `command_not_implemented`; command-specific failure uses audit `error_code` | Public stable | Inspect command audit and diagnostics; fix package input or selection state. | Package operations do not admit or select runtime plans. | `tests/cli/test_cli_package_commands.py::test_package_json_outputs_have_stable_keys_and_deterministic_ordering`; `::test_package_verify_and_doctor_do_not_admit_or_select_plan` | [Package Commands And Audit](#package-commands-and-audit) |
| Package command audit | `operator/packages.py`, `substrate/_workflow_package_command_audit.py` | `error_code` | Validation/path codes, compiler/package diagnostics, `workflow_package_import_error`, `workflow_package_operation_error`, `workflow_package_registry_load_refused`, `registry_commit_failed` | Public stable | Use the recorded failed audit row to identify the repair boundary; retry with a new command identity after correction. | Audits operator attempts; registry/runtime authority changes only through owned commits. | `tests/operator/test_workflow_package_operator_command_audit.py::test_package_command_audit_records_failure_without_registry_generation_change`; `::test_package_command_audit_records_selection_failure_without_registry_change` | [Package Commands And Audit](#package-commands-and-audit) |
| Package doctor | `operator/package_doctor.py` | `overall_status`, finding `category`, diagnostic `code` | `healthy`, `unhealthy`, `unknown`; categories such as `registry_load_refused`, `manifest_unreadable`, `asset_digest_mismatch`, `dependency_problem`, `selection_refused`, `active_pin_retained` | Public stable | A successful command can report unhealthy/unknown findings. Repair the package source/registry or retain the active pin as reported. | Read-only diagnosis; no registry, CAS, selected-plan, or runtime repair. | `tests/operator/test_workflow_package_doctor.py::test_operator_package_doctor_reports_public_registry_load_refusal_without_private_sqlite_inspection`; `::test_operator_package_doctor_does_not_repair_registry_cas_selected_plan_or_runtime` | [Package Doctor](#package-doctor) |
| Compiler authored source | `compiler/build.py`, `compiler/identity.py`, `compiler/references.py`, `compiler/schemas.py`, `compiler/terminal_actions.py` | diagnostic `code` | Exact codes such as `missing_id`, `duplicate_id`, `missing_reference`, `invalid_artifact_schema`, `unsupported_terminal_action_kind`, `unsupported_compatibility_profile` | Public stable | Fix authored workflow source before admission. | Compiler diagnoses; it does not mutate runtime state. | `tests/compiler/test_kernel_ping_diagnostics.py::test_missing_workflow_id_returns_structured_diagnostic`; `::test_terminal_action_missing_reference_diagnostics_are_structured`; `::test_non_null_compatibility_profile_is_refused` | [Compiler Diagnostics](#compiler-diagnostics) |
| Compiler runner/default policy | `compiler/runner_bindings.py` | diagnostic `code` | `runner_adapter_kind_defaulted`, `runner_adapter_kind_unsupported`, `missing_runner_adapter_kind`, `runner_component_authority_cannot_default_adapter`, and selected component/mapping diagnostics | Public stable | Correct malformed/missing authority; review warnings on eligible new compilation. | Only eligible new compilation defaults to Millforge. Active selected plans are never remapped. | `tests/compiler/test_kernel_ping_diagnostics.py::test_invalid_selected_runner_adapter_kind_defaults_with_warning`; `::test_blank_runner_adapter_kind_remains_error_not_default`; `tests/cli/test_cli_bounded_execution_unit.py::test_active_codex_plan_is_not_rebound_to_millforge_default` | [Compiler Diagnostics](#compiler-diagnostics) |
| Package compiler/selection | `compiler/workflow_package_manifest.py`, `compiler/workflow_package_sources.py`, `compiler/package_selection.py` | diagnostic `code` | Manifest/source/selection codes, including `hidden_default_authority`, `package_selection_package_not_found`, `package_selection_asset_digest_mismatch`, `package_selection_dependency_conflict`, `package_selection_dependency_cycle` | Public stable | Fix manifest, declared assets, digests, status, or exact dependency closure before selection/admission. | Compiler diagnoses selected package authority; no registry mutation. | `tests/compiler/test_workflow_package_manifest_validation.py::test_workflow_package_manifest_refuses_hidden_default_authority`; `tests/compiler/test_workflow_package_selection.py::test_compile_refuses_package_hidden_defaults_and_undeclared_authority`; `tests/compiler/test_workflow_package_dependency_closure.py::test_package_selection_refuses_dependency_conflict` | [Compiler Diagnostics](#compiler-diagnostics) |
| Kernel transition | `kernel/decision.py`, `kernel/operator_waits.py`, `kernel/terminal_actions.py`, `kernel/joins.py` | refusal `reason` | Idempotency, plan, enqueue/claim, fanout/join, wait, effect, observation, terminal, closure, and remediation reasons | Public stable when surfaced or persisted | Inspect the exact reason and current state; correct the input or satisfy the governed prerequisite. | Kernel alone decides and applies transitions. | `tests/cli/test_cli_workspace_commands.py::test_workspace_init_conflicting_input_id_returns_domain_refusal`; `tests/operator/test_vendor_selection_operator_wait.py::test_operator_wait_refuses_duplicate_stale_wrong_plan_or_status_decisions` | [Transition Refusals](#transition-refusals) |
| Operator input builders | `operator/intake.py`, `operator/status.py`, `operator/packages.py` | exception `reason` mapped to JSON/audit code | Exact shape, plan, queue, wait, intervention, status, and package-control reasons | Public stable when mapped; exception message internal | Correct the command shape and resubmit through the public command. | Operator validates/submits audited inputs; it does not apply state directly. | `tests/operator/test_kp_0005_intake.py::test_build_enqueue_work_rejects_operator_shape_errors`; `tests/operator/test_workflow_package_mutation_commands.py::test_operator_mutation_commands_reject_unknown_operation_or_package` | [Operator Input Refusals](#operator-input-refusals) |
| Daemon lifecycle | `adapters/cli/lifecycle.py` | bounded-unit `code` | `lifecycle_state_corrupt`, `lifecycle_transition_refused`, `lifecycle_transition_applied`, `no_ready_work` | Public stable | Stop on corruption/refusal; inspect state and the projected transition before retry. | Kernel transition projection/application at runtime; compiler parity: N/A. | `tests/cli/test_cli_daemon_lifecycle.py::test_daemon_lifecycle_corruption_stops_before_runner_dispatch`; `::test_daemon_surfaces_projected_lifecycle_refusal` | [Daemon And Bounded Units](#daemon-and-bounded-units) |
| Daemon/bounded execution | `adapters/cli/daemon.py`, `adapters/cli/run.py` | bounded-unit `code`; daemon summary `stopped_reason`; CLI JSON `code` | The listed values are bounded-unit codes: `no_ready_work`, `ready_state_refused`, `ready_state_corrupt`, `adapter_kind_refused`, `adapter_failure`, `adapter_conversion_refused`, `asset_material_refused`, `observation_refused`, `observation_accepted`. A failure code that stops the daemon is also its stop reason and CLI error code. | Public stable in the named fields | Distinguish a no-op, corrupt authority, runner attempt failure, evidence refusal, or accepted observation. | Runtime orchestration only; compiler parity: N/A. | `tests/cli/test_cli_bounded_execution_unit.py::test_bounded_unit_no_ready_work_is_successful_noop`; `::test_bounded_unit_adapter_conversion_refusal_creates_no_evidence`; `::test_bounded_unit_kernel_observation_refusal_after_claim_preserves_only_claim`; `tests/cli/test_cli_daemon_loop.py::test_daemon_ready_state_refused_and_corrupt_are_not_idle` | [Daemon And Bounded Units](#daemon-and-bounded-units) |
| Selected prompt/skill material | `operator/prompt_material.py`, `adapters/cli/run.py` | bounded-unit `code` | `asset_material_refused` | Public stable category; exception text internal | Fix missing/duplicate selected assets, kinds, text bodies, pins, or digests in selected authority. | Refuses before adapter invocation; compiler parity: N/A for loaded-state corruption. | `tests/operator/test_prompt_materialization.py::test_materialization_refuses_missing_pin_duplicate_pin_and_digest_mismatch`; `tests/cli/test_cli_bounded_execution_unit.py::test_selected_asset_material_refusal_after_claim_preserves_only_claim` | [Selected Material](#selected-material) |
| Adapter contract | `adapters/runner_contract.py` | `error_kind` | `timeout`, `cancelled`, `missing_opt_in_config`, `invocation_failed`, `result_parse_failed`, `unsupported_adapter_kind`, `input_too_large`, `output_too_large`, `redaction_refused`, `selected_authority_refused` | Public stable | Repair local setup/selected authority or retry only when the category is transient. | Candidate-evidence failure; compiler parity: N/A. | `tests/adapters/test_runner_contract.py::test_each_adapter_error_kind_cannot_convert_to_evidence` | [Adapters And Evidence](#adapters-and-evidence) |
| Subprocess transport | `adapters/subprocess_transport.py` | `error_kind` | `cancelled`, `input_too_large`, `invalid_cwd`, `redaction_refused`, `invocation_failed`, `timeout`, `output_too_large`, `nonzero_exit` | Public stable at transport boundary | Correct bounded invocation inputs or inspect redacted diagnostics. | Transport has no workflow authority; compiler parity: N/A. | `tests/adapters/test_subprocess_transport.py::test_subprocess_transport_error_and_output_ceilings_are_fail_closed`; `::test_subprocess_transport_pre_cancel_and_timeout_do_not_expose_success` | [Adapters And Evidence](#adapters-and-evidence) |
| Codex adapter | `adapters/codex.py` | adapter `error_kind` | Transport `invalid_cwd` and `nonzero_exit` map to `invocation_failed`; malformed output maps to `result_parse_failed` | Public stable | Repair explicit Codex local config/invocation or wrapper output. | Adapter produces candidate evidence only; compiler parity: N/A. | `tests/adapters/test_codex_adapter.py::test_codex_adapter_maps_wrapper_failures_to_adapter_errors`; `::test_codex_adapter_malformed_success_fields_are_parse_errors` | [Adapters And Evidence](#adapters-and-evidence) |
| Millforge adapter | `adapters/millforge.py` | adapter `error_kind` | Exactly `redaction_refused`, `missing_opt_in_config`, `input_too_large`, `timeout`, `cancelled`, `invocation_failed`, `selected_authority_refused`, `result_parse_failed` | Public stable; diagnostic reason text internal | Repair explicit local config/optional package, selected component authority, or selected output as indicated without exposing values. | Adapter remains non-authoritative; compiler parity: N/A for invocation/result failures. | `tests/adapters/test_millforge_adapter.py::test_invalid_live_config_or_factory_failure_has_no_observation`; `::test_millforge_adapter_refuses_component_capability_context_and_evidence_drift_before_execute`; `::test_millforge_adapter_refuses_unknown_identity_or_selected_output_mismatch` | [Millforge](#millforge) |
| Evidence conversion | `adapters/runner_contract.py`, `adapters/cli/run.py` | bounded-unit `code` | `adapter_conversion_refused`; adapter errors cannot convert to `RunnerResultEvidence` | Public stable | Treat the attempt as failed; no terminal action was accepted. | Conversion binds candidate evidence to dispatch; kernel still decides meaning. Compiler parity: N/A. | `tests/cli/test_cli_bounded_execution_unit.py::test_bounded_unit_adapter_conversion_refusal_creates_no_evidence`; `tests/adapters/test_runner_contract.py::test_error_outcomes_and_half_success_records_cannot_convert_to_evidence` | [Adapters And Evidence](#adapters-and-evidence) |
| Persistence/open-state refusal | `adapters/cli/daemon.py`, `adapters/cli/run.py`, `operator/packages.py`, `operator/package_doctor.py` | CLI JSON `code`; daemon summary `stopped_reason`; bounded-unit `code`; audit `error_code`; doctor diagnostic `code` | `state_open_failed` is a daemon stop reason; `daemon_state_open_failed` is its CLI JSON mapping; `ready_state_corrupt` is a bounded-unit code and daemon stop reason; `workflow_package_registry_load_refused` is an audit error or doctor diagnostic | Public stable only in the named fields | Preserve files, inspect with public commands, and restore/migrate from known-good state. No silent repair is promised. | Substrate persists/refuses durable truth; compiler parity: N/A. Internal exception types remain internal. | `tests/cli/test_cli_output_contract.py::test_daemon_uninitialized_store_returns_stable_error`; `tests/cli/test_cli_daemon_loop.py::test_daemon_ready_state_refused_and_corrupt_are_not_idle`; `tests/cli/test_cli_bounded_execution_unit.py::test_bounded_unit_corrupt_ready_diagnostics_are_not_no_ready_work`; `tests/operator/test_workflow_package_selection_commands.py::test_operator_select_workflow_reports_registry_load_refusal_without_repair`; `tests/operator/test_workflow_package_doctor.py::test_operator_package_doctor_reports_public_registry_load_refusal_without_private_sqlite_inspection` | [Persistence And Corruption](#persistence-and-corruption) |
| v0.21 cutover | CLI/compiler/package distribution guardrails | absence, JSON/diagnostic category | Old workspaces/configs/snapshots/imports/aliases/intake/skills/models/web/Pi/unsupported runners are absent or refused; no dedicated code is implied | Release-note-only absence categories | Recreate v0.22 state/config and use only current commands and `codex`/`millforge`. | No compatibility authority; compiler parity: N/A for absent runtime surfaces. | `tests/cli/test_cli_bounded_execution_unit.py::test_no_public_run_once_tick_observe_or_dispatch_invoke_commands`; `tests/cli/test_cli_daemon_loop.py::test_daemon_does_not_start_watcher_mailbox_or_source_intake`; `tests/packaging/test_public_package_surface.py::test_built_base_artifacts_ship_only_kernel_ping_workflow_modules` | [v0.21 Absence](#v021-absence) |
| Live E2E guardrails | E2E harness tests | refusal/skip category | Explicit opt-in; finite timeout, input/output byte limits, redaction canary, tick/workflow/total/retry budgets | Public operational contract | Fix guardrail configuration before live execution; never weaken redaction to obtain a run. | Harness bounds attempts and cannot grant workflow authority. Compiler parity: N/A. | `tests/e2e/test_actual_model_workflow_smoke.py::test_missing_opt_in_skips_before_config_read_or_artifact_creation`; `::test_unbounded_config_missing_caps_and_redaction_canary_is_refused` | [Live Execution Guardrails](#live-execution-guardrails) |

## Command Codes

Stable successful command codes include `workspace_initialized`,
`workspace_ok`, `work_enqueued`, `queue_families_listed`, `status_projected`,
`runs_listed`, `run_shown`, `trace_projected`, `waits_listed`,
`interventions_listed`, `operator_intervention_recorded`, `work_claimed`,
`dispatch_shown`, `doctor_ok`, `plan_admitted`, `default_plan_selected`,
`plans_shown`, `package_command_succeeded`, and `daemon_stopped`.

Stable usage/refusal codes include:

- General: `argument_parse_error`, `invalid_actor_id`,
  `command_not_implemented`, `invalid_payload_json`, `internal_error`.
- Queue: `invalid_payload_source`, `payload_file_unreadable`, plus the
  operator-input codes below.
- Status/runs/trace: `run_not_found`, `invalid_max_events`,
  `invalid_plan_fingerprint`.
- Dispatch claim: `claimed_run_missing` reports inconsistent post-claim state;
  `dispatch.show` projection codes are listed below.
- Plan: `plan_not_admitted`, `compiled_plan_export_unreadable`,
  `compiled_plan_export_invalid`, `plan_fingerprint_drift`, plus compiler,
  package-selection, or transition codes passed through by the command.
- Package: `package_archive_unreadable`; failed operations expose their
  command-audit `error_code`.
- Daemon options: `invalid_activation_id`, `invalid_monitor`,
  `invalid_max_ticks`, `invalid_idle_sleep`, `invalid_adapter_config`, and
  `invalid_run_option`.

## Dispatch And Readiness

`dispatch.show` exposes this exact projection-refusal set:

`unknown_run`, `run_observed`, `work_item_closed`, `missing_activation`,
`missing_work_item`, `missing_admitted_plan`, `plan_ref_mismatch`,
`run_activation_drift`, `missing_stage_kind`, `graph_stage_runner_drift`,
`missing_selected_asset`, `missing_selected_schema`, and `graph_node_missing`.

Ready projection emits direct reason codes `missing_work_item`,
`plan_ref_mismatch`, `already_claimed`, `stale_generation`,
`missing_admitted_plan`, `work_item_closed`, and `missing_selected_schema`.
It also maps kernel/current-authority conditions to `workspace_paused`,
`lineage_quarantined`, `operator_wait_active`, `dependency_not_ready`,
`concurrency_policy_blocked`, `capability_denied`,
`capability_approval_pending`, `capability_unsupported`,
`unsupported_selected_authority`, `graph_node_missing`, `queue_family_drift`,
and `graph_stage_runner_drift`.

These are current runtime projection results. Compiler parity is N/A.

## Transition Refusals

The stable refusal field is `reason`. Detail text is explanatory. Exact
source-backed reason families include:

- Admission and replay: `unsupported_input`, `replayed_refusal`,
  `idempotency_conflict`, `plan_authority_conflict`,
  `unsupported_selected_authority`, `plan_fingerprint_mismatch`,
  `unknown_plan_ref`.
- Enqueue and claim: `missing_default_plan`, `queue_family_not_external`,
  `unknown_enqueue_payload_schema`, `invalid_enqueue_payload_schema`,
  `workspace_paused`, `missing_activation`, `missing_work_item`,
  `lineage_quarantined`, `operator_wait_active`, `work_item_closed`,
  `dependency_not_ready`, `stale_activation`, `concurrency_policy_blocked`,
  `capability_denied`, `capability_approval_pending`, and
  `capability_unsupported`.
- Fanout and join: `unsupported_fanout`, `missing_source_artifact`,
  `wrong_source_artifact`, `fanout_partial_state`, `invalid_fanout_payload`,
  `fanout_already_applied`, `fanout_refused`, `unsupported_join`,
  `join_partial_state`, `join_already_applied`, `join_evidence_missing`,
  `join_evidence_mismatch`, `join_evidence_duplicate`,
  `source_work_item_not_closed`, `invalid_join_target_payload`.
- Waits and effects: `unknown_wait`, `wait_already_consumed`, `wait_not_due`,
  `invalid_cooldown_wait`, `invalid_operator_wait`,
  `invalid_operator_wait_payload_schema`, `effect_proposal_not_found`,
  `effect_proposal_not_pending`, `unselected_effect_provider`,
  `unsupported_effect_reconciliation_status`,
  `effect_result_requests_runtime_mutation`, `effect_reconciliation_conflict`.
- Runner observation and terminal handling: `invalid_runner_evidence`,
  `unknown_run`, `invalid_observation_authority`, `missing_run_state`,
  `duplicate_runner_observation`, `undeclared_terminal_outcome`,
  `missing_terminal_action`, `unsupported_runtime_terminal_action`,
  `unsupported_terminal_route`, `unsupported_terminal_recovery_route`,
  `invalid_artifact_payload`, `unsupported_artifact_schema`,
  `invalid_route_projection`, `invalid_dynamic_route_target`,
  `missing_observed_at`, `observed_at_out_of_range`.
- Runtime policy: `operator_wait_exists`, `unsupported_operator_wait`,
  `lineage_required`, `lineage_quarantine_exists`,
  `effect_artifact_required`, `effect_declaration_mismatch`,
  `effect_real_side_effects_unsupported`, `ambiguous_effect_declaration`,
  `counter_threshold_requires_escalation`, `counter_threshold_not_reached`,
  `invalid_intervention_option`,
  `invalid_operator_intervention_payload_schema`.
- Closure/remediation: `unknown_closure_target`, `closure_target_closed`,
  `closure_target_not_ready`, `closure_evaluation_already_active`,
  `invalid_closure_target`, `closure_action_mismatch`,
  `no_live_lineage_work`, `invalid_remediation_policy`,
  `missing_remediation_source_artifact`, `duplicate_remediation_work`,
  `invalid_remediation_payload`.

## Operator Input Refusals

Stable operator input reasons, when mapped to JSON `code` or command-audit
`error_code`, include:

`missing_default_plan`, `plan_fingerprint_mismatch`, `unknown_queue_family`,
`queue_family_not_external`, `missing_external_enqueue_route`,
`unknown_payload_schema`, `invalid_payload_schema`,
`invalid_selected_plan_ref`, `invalid_actor_kind`,
`selected_plan_ref_mismatch`, `unknown_plan_ref`,
`unknown_intervention_option`, `intervention_option_kind_mismatch`,
`intervention_policy_mismatch`, `unknown_operator_wait`,
`operator_wait_resolution_forbidden`, `invalid_operator_wait_target`,
`invalid_intervention_target`, `unknown_lineage_quarantine`,
`invalid_payload`, `payload_forbidden`, `missing_payload_schema`,
`invalid_max_events`, and `invalid_plan_fingerprint`.

Blank required reason text is rejected as `invalid_reason`. Exception messages
remain internal.

## Compiler Diagnostics

Compiler diagnostic `code` is stable; diagnostic message/detail prose is not.
Fix compiler errors before plan admission. Representative exact families are:

- Identity and top-level source: `missing_id`, `duplicate_id`, `non_nfc_id`,
  `canonically_equivalent_id`, `non_nfc_authority_map_key`,
  `unknown_source_section`, `unsupported_authority_value`,
  `missing_reference`, `unsupported_compatibility_profile`,
  `unsupported_required_extensions`.
- Graph, route, and terminal authority: `outcome_without_action`,
  `outcome_stage_mismatch`, `unreferenced_partition`,
  `external_enqueue_route_internal_queue`,
  `ambiguous_external_enqueue_route`, `invalid_artifact_schema`,
  `ambiguous_terminal_marker`, `ambiguous_terminal_action`,
  `unsupported_terminal_action_kind`.
- Runner authority: `invalid_runner_invocation_timeout_seconds`,
  `runner_adapter_kind_defaulted`, `runner_adapter_kind_unsupported`,
  `runner_component_authority_cannot_default_adapter`,
  `runner_default_component_authority_incompatible`,
  `runner_default_component_capability_unusable`,
  `runner_default_component_mapping_incomplete`, `missing_runner_adapter_kind`,
  `missing_runner_component_authority`,
  `missing_runner_terminal_mapping_authority`,
  `runner_binding_missing_runner_invoke`.
- Manifest/source: `invalid_manifest_shape`, `missing_manifest_field`,
  `invalid_manifest_value`, `invalid_manifest_string_whitespace`,
  `non_nfc_manifest_string`, `invalid_package_id`, `duplicate_workflow_id`,
  `duplicate_asset_id`, `duplicate_asset_package_path`,
  `invalid_asset_package_path`, `invalid_asset_byte_length`, `invalid_digest`,
  `dangling_asset_reference`, `empty_workflow_package`,
  `hidden_default_authority`, `plugin_execution_claim`,
  `marketplace_install_claim`, `missing_manifest`, `invalid_manifest_json`,
  `unreadable_package_file`, `asset_byte_length_mismatch`,
  `asset_digest_mismatch`, `missing_manifest_digest`,
  `manifest_digest_mismatch`.
- Package selection: `package_selection_package_not_found`,
  `package_selection_zero_current_package`,
  `package_selection_duplicate_current_package`,
  `package_selection_package_status_refused`,
  `package_selection_unknown_package_status`,
  `package_selection_expected_manifest_digest_mismatch`,
  `package_selection_expected_package_digest_mismatch`,
  `package_selection_manifest_cas_unreadable`,
  `package_selection_manifest_digest_mismatch`,
  `package_selection_manifest_package_mismatch`,
  `package_selection_workflow_not_found`,
  `package_selection_entrypoint_not_found`,
  `package_selection_asset_not_found`,
  `package_selection_asset_cas_unreadable`,
  `package_selection_asset_digest_mismatch`,
  `package_selection_binary_asset_unsupported`,
  `package_selection_selected_authority_assets_refused`,
  `package_selection_workflow_source_mismatch`,
  `package_selection_ambiguous_dependency`,
  `package_selection_dependency_conflict`,
  `package_selection_dependency_cycle`,
  `package_selection_dependency_malformed`,
  `package_selection_dependency_manifest_digest_mismatch`,
  `package_selection_dependency_not_declared`,
  `package_selection_dependency_not_found`,
  `package_selection_dependency_status_refused`,
  `package_selection_duplicate_dependency_pin`,
  `package_selection_non_exact_dependency_constraint`.

`runner_adapter_kind_defaulted` is a warning for an eligible new compilation.
Missing, blank, whitespace, or otherwise malformed adapter authority remains an
error. The v0.22 supported adapter kinds are `codex` and `millforge`. Both
require explicit local configuration. Only eligible new compilation defaults
to `millforge`; a selected plan already admitted or used by an active run is
never remapped to the current default.

Exact characterization proof added for this catalog:

| Diagnostic code | Test proof |
| --- | --- |
| `asset_byte_length_mismatch` | `tests/compiler/test_workflow_package_sources.py::test_path_source_reader_reports_asset_byte_length_mismatch` |
| `invalid_manifest_json` | `tests/compiler/test_workflow_package_sources.py::test_path_source_reader_reports_invalid_manifest_json` |
| `package_selection_manifest_package_mismatch` | `tests/compiler/test_workflow_package_selection.py::test_compile_refuses_manifest_package_identity_mismatch` |
| `package_selection_workflow_source_mismatch` | `tests/compiler/test_workflow_package_selection.py::test_compile_refuses_selected_workflow_source_identity_mismatch` |
| `package_selection_dependency_malformed` | `tests/compiler/test_workflow_package_dependency_closure.py::test_package_selection_refuses_malformed_dependency_declaration` |
| `package_selection_dependency_not_declared` | `tests/compiler/test_workflow_package_dependency_closure.py::test_package_selection_refuses_undeclared_required_dependency` |
| `package_selection_dependency_status_refused` | `tests/compiler/test_workflow_package_dependency_closure.py::test_package_selection_refuses_disabled_dependency` |
| `package_selection_duplicate_dependency_pin` | `tests/compiler/test_workflow_package_dependency_closure.py::test_package_selection_refuses_duplicate_dependency_pin_from_diamond` |

## Package Commands And Audit

Package command audit `error_code` values are stable when recorded. Exact
operator validation/path codes include:

`missing_command_id`, `missing_operation_id`, `missing_actor_id`,
`missing_actor_kind`, `duplicate_command_id`, `missing_success_command_audit`,
`missing_package_id`, `missing_package_version`, `missing_workflow_id`,
`missing_workflow_version`, `missing_workflow_selection`, `missing_entrypoint`,
`missing_expected_manifest_digest`, `missing_expected_package_digest`,
`package_removed`, `missing_package_root`, `missing_archive_bytes`,
`missing_installed_distribution_name`,
`mixed_installed_source_fields`, `missing_update_source`,
`unsupported_package_operation`, `invalid_installed_resource_root`,
`missing_export_root`, `missing_output_path`, `package_not_found`,
`export_path_suffix`, `export_path_parent_traversal`, `export_path_escape`,
`export_root_invalid`, `export_path_symlink`,
`export_destination_parent_missing`, `export_destination_parent_invalid`,
`export_directory_destination`, and `export_destination_not_regular_file`.

Source/compiler diagnostics may become the audit `error_code`. Bounded fallback
categories are `workflow_package_import_error`,
`workflow_package_operation_error`, `workflow_package_registry_load_refused`,
and `registry_commit_failed`. Free-form exception text is not stable.

## Package Doctor

Package doctor can return a successful `package_command_succeeded` CLI envelope
while `overall_status` is `unhealthy` or `unknown`. Finding categories include
`registry_load_refused`, `manifest_unreadable`, `manifest_digest_mismatch`,
`package_digest_mismatch`, `asset_unreadable`, `asset_digest_mismatch`,
`dependency_problem`, `package_disabled`, `package_removed`,
`selection_refused`, `active_pin_retained`, and
`active_pin_selected_plan_corrupt`.

Diagnostic codes include underlying package-selection codes plus
`package_not_found`, `workflow_package_registry_load_refused`, and
`active_pin_selected_plan_corrupt`. Doctor is read-only and does not repair
registry, CAS, selected-plan, or runtime state.

The `active_pin_aftermath_category` values are `active_pin_none`,
`active_pin_selected_plan_corrupt`,
`active_pin_retained_after_package_disable`,
`active_pin_retained_after_package_remove`, and
`active_pin_retained_after_package_update`.

## Daemon And Bounded Units

Bounded execution result `code` values are `no_ready_work`,
`ready_state_refused`, `ready_state_corrupt`, `adapter_kind_refused`, `adapter_failure`,
`adapter_conversion_refused`, `asset_material_refused`,
`observation_refused`, and `observation_accepted`. Lifecycle adds
`lifecycle_state_corrupt`, `lifecycle_transition_refused`, and
`lifecycle_transition_applied`.

Daemon summary `stopped_reason` values include `daemon_already_running`,
`state_open_failed`, `signal`, `max_ticks`, and the bounded-unit code that
stopped progression. Thus `ready_state_corrupt` is both a bounded-unit `code`
and, when it stops the daemon, a daemon `stopped_reason`. The CLI maps daemon
`state_open_failed` to JSON `code` `daemon_state_open_failed`; it passes other
daemon failure reasons, including `ready_state_corrupt`, through as the JSON
`code`. State diagnostics can include `cas_root_not_initialized`.
`lifecycle_state_corrupt` and `lifecycle_transition_refused` are runtime
categories. Compiler parity is N/A.

## Selected Material

`asset_material_refused` is the stable public category. It covers missing or
duplicate selected asset declarations, wrong entrypoint/skill asset kinds,
blank or non-text selected bodies, missing or duplicate package pins, body
digest mismatch, and inconsistent dispatch roles or selected outputs.

Materialization refuses before adapter invocation. The free-form
`SelectedAssetMaterializationError` message is explanatory and may change.

## Adapters And Evidence

The complete adapter contract `error_kind` set is `timeout`, `cancelled`,
`missing_opt_in_config`, `invocation_failed`, `result_parse_failed`,
`unsupported_adapter_kind`, `input_too_large`, `output_too_large`,
`redaction_refused`, and `selected_authority_refused`.

The subprocess transport set is `cancelled`, `input_too_large`, `invalid_cwd`,
`redaction_refused`, `invocation_failed`, `timeout`, `output_too_large`, and
`nonzero_exit`. Codex maps transport `invalid_cwd` and `nonzero_exit` to adapter
`invocation_failed`; malformed bounded wrapper output maps to
`result_parse_failed`.

An `AdapterErrorResult` cannot convert to `RunnerResultEvidence`. A success
result must still match the selected dispatch and pass conversion; otherwise
bounded execution reports `adapter_conversion_refused`. Adapter failures and
transport failures create no observation, terminal action, route, artifact, or
work closure. A claim may already have been durably recorded before a
post-claim attempt fails.

Redaction runs before public result exposure. `redaction_refused` means the
attempt failed closed; diagnostics must not contain the rejected secret or
canary. Never include local config values, credentials, raw selected prompt
material, or unbounded provider output in reports.

## Millforge

Millforge exposes exactly these public adapter `error_kind` values:

- `missing_opt_in_config`: explicit local setup or the optional package is not
  available. Repair local installation/configuration without publishing its
  values.
- `invocation_failed`: local profile/factory/invocation setup failed at the
  bounded adapter boundary.
- `selected_authority_refused`: the selected component pin, descriptor,
  capability, evidence, or schema authority is incompatible or incomplete.
- `result_parse_failed`: selected result identity, required output presence,
  output digest, or output schema did not match the selected contract.
- `redaction_refused`, `input_too_large`, `timeout`, and `cancelled`: the
  corresponding bounded safety or execution condition stopped the attempt.

More specific Millforge diagnostic reason strings are internal and are not
independently stable codes. Millforge remains a runner adapter: it cannot
choose legal terminal meaning or mutate runtime state directly.

## Persistence And Corruption

Externally visible persistence and open-state failures are classified by their
actual field:

- CLI JSON `code`: `store_not_initialized`, `cas_root_not_initialized`,
  `substrate_error`, and `daemon_state_open_failed`.
- Daemon summary `stopped_reason`: `state_open_failed`. This is mapped to
  `daemon_state_open_failed` only when rendered as a CLI JSON error.
- Bounded-unit `code`: `ready_state_corrupt`. If it stops daemon progression,
  it is also the daemon summary `stopped_reason` and is passed through as the
  CLI JSON `code`.
- Workflow package command-audit `error_code`:
  `workflow_package_registry_load_refused` for failed public package
  operations that cannot load the registry.
- Package doctor diagnostic `code`:
  `workflow_package_registry_load_refused` for the successful doctor report
  whose `overall_status` is `unknown`; that report does not reclassify the
  diagnostic as a generic CLI error.

Internal exception classes such as `InvalidCasDigest`, `CasObjectNotFound`,
`CasDigestMismatch`, `InvalidCasObject`, `UnsupportedRecordKind`,
`UnsupportedSchemaVersion`, `UnsupportedStoreSchemaVersion`,
`UnsupportedCodec`, `CasObjectKindMismatch`, and `StorageIntegrityError`
identify implementation-level causes. They are not public categories, and
their names/messages are not public automation fields.

Preserve the workspace when corruption is reported. Use `workspace check`,
`status`, package verify/doctor, and known-good backup or migration procedures
before any low-level intervention. v0.22 does not promise automatic repair or
support for opening v0.21 state.

## v0.21 Absence

v0.22 does not silently retain old compatibility surfaces. The following are
absence/refusal categories, not dedicated error codes:

- v0.21 workspaces, store schemas, configs, snapshots, imports, root aliases,
  bundled workflow layouts, and compatibility profiles;
- old command aliases including `add-task`, `add-spec`, `add-probe`,
  `add-idea`, `pause`, `resume`, `stop`, `retry-active`,
  `clear-stale-state`, and `reload-config`;
- watcher, ideas/mailbox, webhook, provider-callback, source-intake, skill
  lifecycle/index/install/promote/search/refresh, workspace-map/history-log,
  old mode/model, rich-monitor, and live-tail surfaces;
- Pi RPC, Claude Code, OpenCode, OpenHands, `millrace-web`, marketplace, and
  plugin execution surfaces;
- runner kinds other than the supported `codex` and `millforge` adapters.

An absent CLI shape normally produces `argument_parse_error` or
`command_not_implemented`; no more specific compatibility code is implied.
Authored compatibility profiles produce `unsupported_compatibility_profile`.
Unsupported current selected adapter authority can produce
`adapter_kind_refused`. Recreate current configuration and state instead of
depending on an old alias or hidden reader.

## Live Execution Guardrails

Live E2E execution requires explicit opt-in and finite timeout, input byte,
stdout/stderr byte, maximum-tick, per-workflow, total, retry, and redaction
canary limits. Missing or unbounded guardrails refuse or skip before provider
execution and artifact creation. Redaction canaries are checked across public
output and artifacts. These guardrails bound attempts; they do not grant
workflow authority.
