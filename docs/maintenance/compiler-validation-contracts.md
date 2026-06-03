# Compiler Validation Contracts

This page characterizes compiler validation behavior exposed through the
package facade in `src/millrace_ai/compilation/validation/__init__.py`.
Earlier batches recorded the behaviors, diagnostics, and focused test gaps that
the validator-family extraction had to preserve.

Diagnostic stability here means preserving useful substrings. The compiler does
not expose structured validation error codes for these checks.

## Batch 6 Packet 02 Status

Packet 02 extracts the low-coupling validation families into focused modules
under `src/millrace_ai/compilation/validation/`:

- `graphs.py` for graph topology smoke validation and entry-walk checks
- `stages.py` for stage artifact references, entry-family ownership, and runtime
  failure recovery node constraints
- `modes.py`, `model_assignments.py`, and `capabilities.py` for mode map scope
  validation across entrypoint, skill, model/thinking, and runner bindings
- `artifacts.py` for document adapter and artifact contract checks
- `work_families.py` for queue claim policy/lifecycle adapter validation
- `request_context_profiles.py` for profile/provider/render-plan authority
- `lifecycle.py` for terminal action and lifecycle plan authority checks
- `lane_conflicts.py` for concurrent-plane lane conflict coverage
- `__init__.py` as the orchestration facade keeping public imports stable

Runtime-effect handler/rule validation, recovery policies, runtime failure
policies, and repair-closure validation were intentionally deferred to Packet
03, which extracted them into their own modules.

Representative direct invalid-asset coverage for extracted families now includes:

- graph duplicate route bindings and unknown entry-walk targets
- stage output/producer mismatch and runtime failure recovery repair-role checks
- mode model-binding out-of-scope stage maps
- lifecycle unknown runtime effect rules and lifecycle source family/node checks
- lane concurrency tuple arity checks

## Batch 6 Packet 03 Status

Packet 03 extracts the remaining runtime-effect and failure-policy validator
families from the package facade into focused modules under
`src/millrace_ai/compilation/validation/`:

- `operation_runners.py` for runtime-effect runner ownership and legacy alias
  registry validation/resolution helpers
- `runtime_effects.py` for runtime-effect handler/rule validation and operation
  catalog checks
- `failure_policies.py` for workflow recovery policy and runtime failure policy
  scope/plane/node checks
- `repair_closures.py` for generic route-to-node repair-closure validation
  (operation/failure resolution, mapping drift checks, evidence requirements,
  resume guard checks)
- `__init__.py` now calls these modules directly
- `src/millrace_ai/compilation/effect_operations.py` is a compatibility facade
  over the extracted runtime-effect operation validator

The previous monolithic `compilation/validation.py` module has been removed.
Normal imports continue to resolve through the package facade.

## Validation Groups

| Group | Contract | Direct tests | Diagnostic substrings to preserve | Missing focused tests before extraction |
| --- | --- | --- | --- | --- |
| Graph topology | Selected graph nodes must route every legal outcome once, terminal states must have terminal actions, terminal artifact references must exist, and entry walks must not reach unknown nodes. | `tests/compilation/test_workflow_validation.py::test_compile_rejects_graph_with_unrouted_legal_outcome`; `test_compile_rejects_graph_route_with_illegal_source_outcome`; `test_compile_rejects_terminal_state_without_terminal_action`; `test_compile_rejects_terminal_state_with_unknown_artifact`. | `has no route for legal outcome`; `declares illegal outcome`; `uses terminal class`; `emits unknown artifact`; `has multiple routes for outcome`; `entry walk reached unknown node`. | Add direct negative tests for duplicate outcome routes and entry-walk unknown target before extracting `graph_topology.py`; today those paths are only guarded by implementation proximity. |
| Stages and stage-kind validation | Stage-kind input/output artifact declarations must match known artifact contracts; graph entries must route families to stages that can start them; runtime repair nodes must map to legal plane stage names or explicit custom stage kinds. | `test_compile_rejects_stage_kind_with_unknown_output_artifact`; `test_compile_rejects_stage_kind_with_unknown_input_artifact`; `test_compile_rejects_entry_stage_without_family_ownership`; `test_compile_rejects_unmapped_runtime_failure_recovery_stage_kind`. | `declares unknown output artifact`; `allows unknown input artifact`; `declares output artifact`; `does not list that stage kind as a producer`; `which cannot start family`; `uses unmapped stage kind`; `must declare recovery_role=local_repair`; `belongs to plane`. | Add direct tests for artifact-producer mismatch and runtime failure recovery wrong role/plane/unknown repair node before extracting `stages.py`. |
| Modes and entrypoints | Mode stage maps may reference only selected graph stages. Entrypoint override paths must stay under `entrypoints/`, reject traversal, and remain deterministic. | `tests/compilation/test_workflow_validation.py::test_compile_rejects_mode_stage_map_outside_selected_loops`; `tests/integration/test_compiler.py::test_compile_rejects_stage_thinking_binding_outside_selected_loops`; `test_compile_rejects_custom_stage_entrypoint_override_outside_selected_loops`; `test_compile_rejects_invalid_entrypoint_override_deterministically`; `test_compile_rejects_entrypoint_override_path_traversal`. | `Mode map \`stage_runner_bindings\` references stage outside selected loops`; `Mode map \`stage_thinking_bindings\` references stage outside selected loops`; `Invalid entrypoint override for stage`. | The high-risk mode-map path now has direct compilation coverage. Before extracting `modes.py`, add one focused test for each remaining map family only if the extraction changes iteration or shared helper behavior. |
| Artifact contracts | Artifact contracts must reference known destination families, parser/renderer adapters, producer stage kinds, and consumer handlers; built-in adapter formats and document-adapter parse/render capabilities must agree with file formats. | `test_compile_rejects_artifact_contract_with_unknown_destination_family`; `test_compile_rejects_artifact_contract_with_unknown_parser_id`; `test_compile_rejects_artifact_contract_with_unknown_renderer_id`; `test_compile_rejects_artifact_contract_with_parser_format_mismatch`; `test_compile_rejects_artifact_contract_with_renderer_format_mismatch`; `test_compile_rejects_artifact_contract_parser_adapter_without_parse_capability`; `test_compile_rejects_artifact_contract_renderer_adapter_without_render_capability`; `test_compile_rejects_artifact_contract_with_unknown_producer_stage`; `test_compile_rejects_artifact_contract_consumer_handler_mismatch`; `tests/assets/test_workflow_assets.py::test_shipped_artifact_inventory_has_packaged_contracts`; `test_shipped_artifact_contract_filenames_have_one_owner`. | `references unknown destination family`; `references unknown parser`; `references unknown renderer`; `declares format`; `without parse capability`; `without render capability`; `extension`; `references unknown producer stage kind`; `declares consumer handler`; `does not consume`. | Add focused tests for document-adapter family mismatch, unknown adapter family, unsupported filename extension, producer-stage mismatch, and unknown consumer handler before extracting `artifact_contracts.py`. |
| Request context profiles | Profiles may prefer output paths only for known artifact contracts, and each preferred filename must be allowed by the contract. | `test_compile_rejects_request_context_profile_with_unknown_output_artifact`; `test_compile_rejects_request_context_profile_with_invalid_output_filename`. | `request context profile`; `references unknown output artifact`; `artifact contract mismatch`; `allows filenames`. | Direct coverage is enough for extraction unless the profile schema moves; then add a success-path test that materializes preferred filenames in the compiled plan. |
| Capabilities and runners | Capability grants are compiled into node plans, config denial has precedence over mode allowance, and strict required-advisory policy can fail compilation. Runner binding behavior is compiled outside `validation.py`, but belongs to the same future capability seam. | `tests/compilation/test_capability_grants.py::test_compile_seals_default_execution_grants_in_node_plan`; `test_config_policy_can_make_capability_approval_required`; `test_strict_required_advisory_grant_fails_compile`; `test_mode_policy_cannot_override_runtime_config_denial`; `tests/integration/test_compiler.py::test_default_pi_compiles_with_pi_runner_bound_for_every_node`; `test_compile_resolves_runner_neutral_thinking_precedence_for_pi`. | `requires enforcement`; `advisory`; runner tests preserve `pi_rpc` materialization rather than a validation diagnostic. | If Batch 5 creates `capabilities.py`, keep capability grant compilation with its current owner unless the extraction explicitly includes non-validation compiler logic. Add focused unknown-runner diagnostic tests only if runner binding validation is added to this seam. |
| Runtime effect handlers and rules | Handlers must reference known source/destination families and artifacts. Rules must reference declared handlers, known operation ids, legal source nodes/outcomes, known required artifacts, declared handler artifacts/capabilities, lifecycle plans, destination families, and unique source-outcome bindings. | `test_compile_rejects_effect_rule_with_unknown_handler`; `test_compile_rejects_effect_rule_with_unknown_operation`; `test_compile_rejects_runtime_effect_handler_with_unknown_artifact`; `test_compile_rejects_runtime_effect_rule_with_unknown_required_artifact`; `test_compile_rejects_runtime_effect_rule_with_missing_handler_capability`; `test_compile_rejects_effect_rule_with_unknown_effect_operation`; `test_compile_rejects_duplicate_effect_rule_binding`; `tests/compilation/test_workflow_validation.py::test_compile_blueprint_accepts_closed_runtime_effect_recovery_route`; `tests/assets/test_workflow_assets.py::test_shipped_artifact_inventory_has_packaged_contracts`. | `runtime effect handler`; `unknown source family`; `unknown destination family`; `requires unknown artifact`; `does not list that handler`; `runtime effect rule`; `references unknown handler`; `references unknown operation`; `references unknown effect operation`; `requires handler capability`; `missing from runtime effect rule`; `references illegal outcome`; `both bind`. | Add focused tests for unknown handler source/destination family, handler consumes artifact not listing handler, rule required artifact not declared by handler, missing handler-required artifact, unknown destination family, unknown lifecycle plan, unknown source node, and illegal outcome before extracting `runtime_effects.py`. |
| Runtime failure policies | Runtime failure policies may reference only known families, runtime-effect handlers or operation ids, failure classes, source/recovery/target nodes, terminal states, and active planes. If a policy scopes by both handler and operation ids, the handler ids must be legacy aliases for the selected operations. Partial mutation failures must not route to nodes; target nodes must accept the affected family. | `test_compile_rejects_runtime_effect_failure_policy_with_unknown_target_node`; `test_compile_rejects_runtime_effect_failure_policy_with_illegal_source_family_target`; `test_compile_rejects_runtime_effect_failure_policy_with_wrong_source_plane`; `test_compile_rejects_runtime_effect_failure_policy_with_wrong_target_plane`; `test_compile_rejects_runtime_effect_failure_policy_source_alias_when_policy_binds_graph`; `test_compile_rejects_runtime_effect_failure_policy_target_alias_when_policy_binds_graph`; `test_compile_ignores_optional_runtime_effect_failure_policy_terminal_for_unselected_graph`; `test_compile_rejects_runtime_effect_failure_policy_with_unknown_terminal_state`; `test_compile_rejects_runtime_effect_failure_policy_with_undeclared_failure_class`; `test_compile_rejects_runtime_effect_failure_policy_with_unknown_operation`; `test_compile_rejects_runtime_failure_policy_operation_handler_drift`; `test_compile_rejects_runtime_effect_failure_policy_partial_mutation_route_to_node`. | `runtime failure policy`; `references unknown family`; `references unknown runtime effect handler`; `references unknown runtime effect operation`; `is not a legacy alias for operation ids`; `cannot route partial mutation runtime effect failures to node`; `references undeclared runtime effect failure class`; `references unknown target node`; `is not in plane`; `cannot start family`; `references unknown terminal state`; `target terminal state`. | Add direct tests for unknown family, unknown handler, recovery-node validation, target terminal in wrong plane, and source terminal in wrong plane before extracting `failure_policies.py`. |
| Lane conflicts and plane concurrency | If a mode declares concurrent planes, every lane pair across those planes must have a conflict policy, and concurrency tuples must contain exactly two planes. | `tests/compilation/test_lane_validation.py::test_builtin_default_mode_compiles_one_main_lane_per_plane`; `test_builtin_learning_mode_declares_conflict_policy_for_overlap`; `test_compile_rejects_concurrency_overlap_without_lane_conflict_policy`. | `may_run_concurrently entries must name exactly two planes`; `lane conflict policy missing for concurrent lane pair`; lane IDs such as `execution.main` and `learning.main`. | Add a direct malformed-arity test if the concurrency schema allows such payloads to reach `validate_lane_conflict_coverage`; otherwise current focused coverage is enough for `lane_conflicts.py`. |
| Repair-route closure (generic) | `route_to_node` runtime-effect failures must resolve to one contract-compatible repair closure per operation+failure pair. Each closure must point to an existing repair operation, target node, and target terminal outcome; declare evidence artifacts emitted by the target node and required by the target-node effect rule; match policy family scope; honor partial-mutation and resume-guard flags; and remain unambiguous when operation scope is inferred from handlers. | `test_compile_accepts_non_blueprint_repair_route_from_operation_contract`; `test_compile_rejects_non_blueprint_repair_route_with_unknown_repair_operation`; `test_compile_rejects_non_blueprint_repair_route_missing_rule_artifact`; `test_compile_rejects_non_blueprint_repair_route_with_wrong_family_scope`; `test_compile_rejects_non_blueprint_repair_route_with_wrong_target_plane`; `test_compile_rejects_non_blueprint_repair_route_with_wrong_target_terminal_outcome`; `test_compile_rejects_non_blueprint_repair_route_without_explicit_operation_scope_when_ambiguous`; `test_compile_rejects_non_blueprint_repair_route_without_resume_guard`; `test_compile_rejects_non_blueprint_repair_route_partial_mutation_without_support`; `test_compile_blueprint_accepts_closed_runtime_effect_recovery_route`; `test_compile_rejects_blueprint_recovery_route_without_mechanic_repair_effect`; `test_compile_rejects_blueprint_recovery_route_without_mechanic_resume_guard`; `test_compile_rejects_blueprint_recovery_route_without_declared_repair_artifact_emission`. | `repair closure`; `references unknown repair operation`; `does not invoke repair operation`; `must exactly match repair closure affected source families`; `is not in plane`; `missing required repair evidence artifact`; `must declare repair_closure_mappings`; `lacks resume guard`; `does not support partial mutation`. | Add focused tests for explicit mapping drift (`field ... does not match source operation repair closure contract`) and out-of-scope mapping pairs before extracting this validator into a dedicated `repair_closure.py` helper. |
| Lifecycle and completion behavior | Terminal actions must map terminal classes to lifecycle plans/effect rules; lifecycle plans must reference known source families and source nodes; graph completion behavior materializes closure-target entry data and participates in plan identity. | `test_compile_rejects_terminal_action_with_unknown_lifecycle_plan`; `test_compile_rejects_terminal_state_without_terminal_action`; `tests/integration/test_compiler.py::test_compile_materializes_compiled_plan_graph_surface`; `test_compile_plan_identity_changes_when_graph_completion_behavior_changes`; `tests/assets/test_loop_graphs.py::test_builtin_graph_loop_definitions_load`. | `terminal action`; `references unknown lifecycle mutation plan`; `references unknown runtime effect rule`; `lifecycle mutation plan`; `references unknown source family`; `references unknown source node`; `closure_target`; `active_closure_target`. | Add direct tests for terminal action unknown effect rule, lifecycle plan unknown source family, and lifecycle plan unknown source node before extracting `lifecycle.py`. Completion behavior itself is mostly graph materialization, so avoid folding unrelated materializer code into validation. |
| Closure root-source policy validation | Root-source accepted kinds are normalized and non-empty in graph primitive construction, not in `validation.py`. Compiler validation currently trusts materialized graph definitions. | `tests/assets/test_loop_graphs.py::test_builtin_graph_loop_definitions_load`; architecture model coverage in `tests/architecture/test_workflow_primitives.py` for completion behavior construction. | `root_source_policy.accepted_kinds must not be empty`; `closure_target`; root source accepted kind values `idea`, `probe`, `manual`, `spec`, `incident`. | No `validation.py` extraction is needed unless Batch 5 intentionally moves graph primitive validation into compiler validation. If it does, add a direct compile-negative test for empty accepted kinds. |
| Self-contained intake artifact validation | No self-contained intake artifact validator is present in `validation.py` in this snapshot. Intake artifact parsing and root-source artifact handling live in runtime/workspace/adapter code. | No direct `tests/compilation` owner. Related runtime and asset coverage lives outside this packet. | None from `validation.py`. | Do not create an extraction module for this group in Batch 5 unless a validator is first introduced or moved from another source owner. |
| Model assignment aliases | Alias precedence, fallback, trimming, loop/stage source recording, mode-local alias precedence, and invalid-alias warnings must survive compiler changes. This behavior is adjacent to validation but does not currently raise `CompilerValidationError`. | `tests/compilation/test_model_alias_resolution.py::test_stage_alias_overrides_stage_config`; `test_loop_alias_applies_to_every_node_in_loop`; `test_unknown_stage_alias_warns_and_falls_back_to_loop_alias`; `test_invalid_global_alias_warns_and_falls_back_to_builtin_standard`; `test_alias_values_are_trimmed_before_materialization`; `tests/assets/test_modes.py::test_efficient_learning_mixed_compiles_with_mode_stage_aliases`. | Warning substrings currently include alias IDs such as `missing` and `broken`; preserve `model_assignment_alias_id`, `model_assignment_source`, and fallback alias `standard`. | Before extracting `model_assignments.py`, add exact warning-substring tests if the warning formatter moves. Keep warning behavior separate from hard validation errors. |
| Diagnostics formatting | Validation failures surface as `CompilerValidationError` strings collected into `CompileDiagnostics.errors`; compile failures should set `diagnostics.ok` false and not produce an active plan. Existing tests assert substrings rather than structured codes. | Most `tests/compilation` negative tests assert `diagnostics.ok is False` and `active_plan is None`; `tests/integration/test_compiler.py::test_compiler_validation_errors_use_project_error_hierarchy`; selected integration tests assert exact one-error tuples for deterministic paths. | Preserve the leading noun phrases in every row above, especially `Mode map`, `graph`, `stage kind`, `artifact contract`, `request context profile`, `runtime effect rule`, `runtime failure policy`, `terminal action`, and `lifecycle mutation plan`. | Batch 6 Packet 01 introduced `validation/diagnostics.py` as a tiny formatting/helper layer. Keep it narrow and do not invent error codes during extraction; that would be a behavior/API change requiring a separate spec. |

## High-Risk Coverage Gaps

The highest-risk missing direct tests before source movement are:

- Runtime effect handler/rule edge cases that currently lack focused negative
  tests: unknown source/destination families, missing handler-required
  artifacts, rule artifact not declared by handler, unknown lifecycle plan, and
  illegal source outcome.
- Generic repair-route mapping drift and out-of-scope mapping-pair coverage for
  explicit `repair_closure_mappings`.
- Lifecycle and terminal action edge cases for unknown terminal effect rules and
  lifecycle source family/node references.
- Graph topology edge cases for duplicate route bindings and entry walks that
  reach an unknown node.
- Artifact/document-adapter edge cases for family mismatch and unsupported
  filename extension.

Batch 2 added direct compilation coverage for the mode-stage-map high-risk
path: `test_compile_rejects_mode_stage_map_outside_selected_loops`.

## Proposed Batch 5 Extraction Order

1. Extract `diagnostics.py` only for shared string helpers, keeping messages
   byte-for-byte compatible.
2. Extract low-coupling validators: `artifact_contracts.py`,
   `request_context_profiles.py`, and `lane_conflicts.py`.
3. Extract graph/stage validators together: `graph_topology.py`, `stages.py`,
   and mode-map checks in `modes.py`, after adding the graph/stage gap tests.
4. Extract `lifecycle.py` once terminal-action and lifecycle-plan negative
   tests are direct.
5. Extract `runtime_effects.py`, then `failure_policies.py`; these should land
   as separate commits because diagnostic order and cross-reference maps are
   easy to disturb.
6. Keep route-to-node repair closure checks grouped in a dedicated helper such
   as `repair_closure.py` when extracting `failure_policies.py`; the checks are
   generic and should not be re-coupled to Blueprint-specific modules.
7. Treat model assignment alias handling as adjacent compiler materialization,
   not part of the hard-error validator split, unless Batch 5 explicitly owns
   warning formatting and alias provenance.
