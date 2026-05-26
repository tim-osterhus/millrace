# Request Context Contracts

This note records the behavior contracts for MR-MAINT-007 before moving code out
of `src/millrace_ai/runtime/request_context.py`. It separates generic runner
request-context contracts from Blueprint-specific coupling so Batch 5 can
decompose the generic half without accidentally redesigning Blueprint.

The current module has two reasons to change:

- generic deterministic context rendering for stage runner requests;
- Blueprint-specific context providers for Manager, Contractor, Evaluator, and
  Mechanic repair flows.

Production code should not move until the direct tests named below are either
kept green or replaced by equivalent focused coverage.

## Generic Contracts

### Work Item Loading

Default request context does not parse the active work item document. It carries
the active item identity as a visible artifact reference when
`active_work_item_family_id` and `active_work_item_id` are present, for example
`task:task-001`.

Closure-target requests are the generic non-work-item case. When no active work
item is present and `closure_target_root_spec_id` is set, the visible reference
is `closure_target:<root-spec-id>`.

Direct coverage:

- `tests/runtime/test_request_context.py::test_stage_run_request_writes_default_context_artifacts`
- `tests/runtime/test_request_context.py::test_default_request_context_uses_closure_target_ref_without_active_work_item`
- `tests/runners/test_runner.py::test_stage_run_request_accepts_closure_target_without_active_work_item`

Missing before Batch 5:

- Focused default-context coverage for Learning, Recon, and Integrator stages
  once their expected generic references are explicitly named.
- Focused default-context coverage for Planning stages other than Arbiter and
  Blueprint.

### Compiled Plan And Stage Context

The generic request context records the stage kind in both the render plan and
profile id. For non-Blueprint stages the render plan id is stable:
`stage_request.default.v1`; the profile id is `<stage_kind_id>.default`.

Stage requests are built from the compiled runtime stage plan. A custom stage
kind selected by the active mode must survive the stage-plan lookup before
request-context attachment.

Direct coverage:

- `tests/runtime/test_request_context.py::test_stage_run_request_writes_default_context_artifacts`
- `tests/runtime/test_request_context.py::test_stage_plan_lookup_resolves_custom_stage_kind_by_runtime_stage`
- `tests/runners/test_runner.py::test_render_stage_request_context_lines_includes_live_envelope_fields`
- `tests/runners/test_runner.py::test_render_stage_request_context_lines_covers_all_stage_run_request_fields`

Missing before Batch 5:

- Direct generic request-context tests for Planning, Execution, Learning,
  Recon, and Integrator compiled stage plans. Current direct coverage is
  Builder plus Arbiter closure-target behavior, with Blueprint covered
  separately.

### Capability Grants

Capability grants are request-envelope data, not authority granted by the
context renderer. The renderer must preserve request fields so prompt assembly,
runner invocation artifacts, capability evidence checks, and result
normalization can reason about them. Context rendering must not turn a prompt
visible grant into runtime mutation authority.

Direct coverage:

- `tests/runners/test_runner.py::test_render_stage_request_context_lines_covers_all_stage_run_request_fields`
- `tests/runners/test_runner.py::test_normalize_rejects_completed_result_with_missing_capability_evidence`
- `tests/runners/test_capability_support.py::test_request_context_renders_execution_capability_grants`

Missing before Batch 5:

- A focused runtime request-context test proving default context attachment
  preserves `execution_capability_grants` and `capability_support_decisions`
  unchanged.

### Artifact References

The render plan separates visible artifact refs from operator-only refs. Visible
refs are rendered into prompt context and written to context manifests.
Operator-only refs are redacted from prompt text and appear as
`redacted_artifact_refs` in the manifest.

The default context includes runtime snapshot and recovery counters as
operator-only refs. It includes `active_work_item` as an inline section only
when `active_work_item_path` is present.

Direct coverage:

- `tests/runtime/test_request_context.py::test_request_context_render_excludes_operator_only_refs`
- `tests/runtime/test_request_context.py::test_stage_run_request_writes_default_context_artifacts`
- `tests/runtime/test_request_context.py::test_default_request_context_uses_closure_target_ref_without_active_work_item`
- `tests/runners/test_runner.py::test_normalize_persists_request_context_and_failure_origin_metadata`

Missing before Batch 5:

- A focused manifest assertion for default operator-only runtime snapshot and
  recovery-counter refs. Current tests mainly assert prompt redaction through a
  hand-built render plan.

### Prompt Input Assembly

Request-context rendering writes three artifacts under the run context
directory: `context.json`, `prompt_context.md`, and `render_manifest.json`.
Rendering is deterministic for the same render plan.

Runner prompt assembly includes rendered request context when
`rendered_prompt_context_path` is present. The stage request field renderer must
also expose all `StageRunRequest` fields so runner adapters receive complete
request provenance.

Direct coverage:

- `tests/runtime/test_request_context.py::test_request_context_render_excludes_operator_only_refs`
- `tests/runtime/test_request_context.py::test_stage_run_request_writes_default_context_artifacts`
- `tests/runners/test_runner.py::test_stage_prompt_includes_rendered_request_context`
- `tests/runners/test_runner.py::test_render_stage_request_context_lines_covers_all_stage_run_request_fields`

Missing before Batch 5:

- A direct generic test that reads `context.json` and `render_manifest.json`
  after default attachment and asserts the schema/kind/render ids. Existing
  coverage checks file existence and selected normalized metadata.

### Runner Provenance

Request-context provenance must survive runner normalization on success and
failure: profile id, bundle path, render plan id, rendered prompt path, and
artifact refs are copied into stage-result metadata.

Runner identity, raw result identity, token usage, event logs, observed timeout
reconciliation, and failure origin remain runner-normalization contracts. The
request-context module must not erase or rewrite them.

Direct coverage:

- `tests/runners/test_runner.py::test_normalize_persists_request_context_and_failure_origin_metadata`
- `tests/runners/test_runner.py::test_normalize_rejects_raw_result_identity_mismatch`
- `tests/runners/test_runner.py::test_normalize_preserves_token_usage_and_event_log_artifacts`
- `tests/runners/test_runner.py::test_normalize_preserves_reconciled_timeout_evidence_on_success`

Missing before Batch 5:

- Provenance preservation tests for request-context metadata on successful
  structured terminal-result normalization. Current focused provenance test is
  on a runner-error path.

### Model Assignment Alias Provenance

Model assignment alias provenance is carried on `StageRunRequest` through
`model_assignment_alias_id`, `model_assignment_source`, `model_name`,
`thinking_level`, and `model_reasoning_effort`. Prompt field rendering must show
those fields, and result normalization must preserve model provenance across
success and failure paths.

Direct coverage:

- `tests/runners/test_runner.py::test_render_stage_request_context_lines_covers_all_stage_run_request_fields`
- `tests/runners/test_runner.py::test_render_stage_request_context_lines_handles_optional_fields_absent`

Missing before Batch 5:

- Direct normalization coverage that asserts model assignment alias/source
  metadata is preserved on both success and failure envelopes. This should live
  with runner normalization unless Batch 5 moves provenance projection into a
  request-context helper.

## Blueprint-Specific Contracts

Blueprint request context is documented here as existing behavior, not as Batch
2 refactor scope. These pieces should wait for Batch 4's declarative
runtime-effects and Blueprint decoupling work before any source movement.

### Manager Context

Manager Blueprint context is selected by `stage_kind_id == "manager_blueprint"`.
It includes the active source work item, root lineage, preferred Blueprint
output paths, and a preferred `manager_blueprint_report.md` run artifact.

It intentionally omits execution queue mutation authority and direct Blueprint
store write authority from the prompt-visible provider set.

Direct coverage:

- Secondary coverage through `tests/runtime/test_blueprint_request_context.py`
  Mechanic tests that consume failed Manager outputs.

Missing before Batch 5:

- A focused Manager context test that asserts included providers, omitted
  providers, preferred output refs, and artifact-contract source.

Batch 4 wait:

- Decide whether Manager output paths and store refs become declarative
  operation/store assets or a registered Blueprint context provider.

### Contractor Context

Contractor Blueprint context loads the active Blueprint draft from the Blueprint
draft store. It includes draft excerpt and output paths. When available, it adds
latest critique and latest rejected Blueprint refs. It omits full manifest,
all drafts, prior approved Blueprints, and queue mutation authority.

Direct coverage:

- `tests/runtime/test_blueprint_request_context.py::test_contractor_blueprint_context_excludes_full_manifest`

Missing before Batch 5:

- Direct coverage for Contractor preferred output refs when a compiled plan
  supplies artifact contracts.

Batch 4 wait:

- Draft loading, latest critique, latest rejected packet, and preferred output
  path resolution should follow the Blueprint store/effect-store decision.

### Evaluator Context

Evaluator Blueprint context loads the active draft, resolves the draft's
manifest by `manifest_id`, includes the candidate packet, same-root approved
packets, same-root draft history, same-root critique/evaluation history, the
original spec, and preferred output refs.

It omits queue mutation authority and redacts runtime control state. It filters
same-root history and prefers canonical artifact-contract filenames over stale
legacy names.

Direct coverage:

- `tests/runtime/test_blueprint_request_context.py::test_evaluator_blueprint_context_includes_manifest_and_prior_approvals`
- `tests/runtime/test_blueprint_request_context.py::test_evaluator_blueprint_context_excludes_unrelated_root_history`
- `tests/runtime/test_blueprint_request_context.py::test_evaluator_blueprint_context_resolves_same_root_manifests_by_manifest_id`
- `tests/runtime/test_blueprint_request_context.py::test_evaluator_blueprint_context_uses_compiled_artifact_contract_filenames`
- `tests/runtime/test_blueprint_request_context.py::test_request_context_rejects_mismatched_compiled_plan_authority`

Missing before Batch 5:

- Operation-id-aware repair/evaluation context expectations after legacy
  handler ids start migrating.

Batch 4 wait:

- Same-root store queries, manifest lookup compatibility, candidate packet
  lookup, and output path preferences should move only after effect stores and
  operation assets define the final contract.

### Mechanic Repair Context

Mechanic Blueprint context includes active work item, runtime failure context,
repair output paths, and selected runtime-effect failure evidence from the
recovery run directory. Manager failures expose failed manifest/draft artifacts
and normalized failure class/message. Evaluator approval failures expose
handler id, failure class/message, mutation phase, failure policy id, recovery
action, failed evaluation/generated-task artifacts, required repair action, and
the fact that runtime owns Blueprint state.

Manager failure selection prefers an exact recovery request match and otherwise
falls back to the latest completed eligible Manager failure.

Direct coverage:

- `tests/runtime/test_blueprint_request_context.py::test_mechanic_blueprint_context_includes_manager_runtime_effect_failure_evidence`
- `tests/runtime/test_blueprint_request_context.py::test_mechanic_blueprint_context_includes_evaluator_runtime_effect_failure_evidence`
- `tests/runtime/test_blueprint_request_context.py::test_mechanic_blueprint_context_prefers_manager_failure_matching_request`
- `tests/runtime/test_blueprint_request_context.py::test_mechanic_blueprint_context_falls_back_to_latest_completed_manager_failure`
- `tests/runtime/test_blueprint_request_context.py::test_mechanic_blueprint_context_normalizes_multiline_failure_message_refs`

Missing before Batch 5:

- Tests for operation-id plus legacy-handler-id dual-key repair context once
  Batch 4 introduces operation ids.
- Tests for malformed or unreadable failure stage-result files beyond the
  current silent-skip behavior.

Batch 4 wait:

- Runtime-effect failure evidence selection should align with the declarative
  effect operation result model, mutation journal, and handler-id compatibility
  policy before extraction.

### Blueprint Store Refs And Preferred Output Paths

Blueprint context currently knows concrete Blueprint store locations for
manifests, drafts, packets, critiques, evaluations, original specs, and
preferred run-output filenames. Compiled artifact contracts can override output
filenames, but the request compiled-plan id must match the supplied compiled
plan id.

Direct coverage:

- `tests/runtime/test_blueprint_request_context.py::test_evaluator_blueprint_context_uses_compiled_artifact_contract_filenames`
- `tests/runtime/test_blueprint_request_context.py::test_request_context_rejects_mismatched_compiled_plan_authority`

Missing before Batch 5:

- Focused Manager and Contractor compiled artifact-contract path tests.
- Store ref behavior for malformed packet, critique, and evaluation documents.

Batch 4 wait:

- Store paths and preferred output paths should be reconciled with declarative
  effect stores and operation assets before generic request-context
  decomposition chooses a module boundary.

### Legacy Handler-Id Metadata

Mechanic repair context currently keys eligible Manager and Evaluator failures
on legacy runtime effect handler ids:
`manager_blueprint_manifest_to_blueprint_drafts` and
`evaluator_blueprint_approved_to_task`.

Evaluator repair context also exposes `runtime_effect_handler_id` in refs. This
is required compatibility behavior until operation ids and legacy handler aliases
are both available in runtime traces and repair artifacts.

Direct coverage:

- `tests/runtime/test_blueprint_request_context.py::test_mechanic_blueprint_context_includes_evaluator_runtime_effect_failure_evidence`
- `tests/runtime/test_blueprint_request_context.py::test_mechanic_blueprint_context_prefers_manager_failure_matching_request`

Missing before Batch 5:

- Dual-key tests for new operation ids with legacy handler aliases.

Batch 4 wait:

- Do not extract or rename handler-id repair selectors until Batch 4 defines
  operation-id/handler-id compatibility metadata.

## Batch 5 Readiness Checklist

- Keep generic renderer and default context movement separate from Blueprint
  provider movement.
- Add generic coverage for Learning, Recon, Integrator, and additional Planning
  stages before extracting compiled-plan/stage context helpers.
- Add explicit success-path provenance tests for request-context and model
  assignment metadata before moving runner provenance projection.
- Wait for Batch 4 before moving Manager, Contractor, Evaluator, Mechanic
  repair evidence, Blueprint store refs, preferred output paths, or legacy
  handler-id selectors.
