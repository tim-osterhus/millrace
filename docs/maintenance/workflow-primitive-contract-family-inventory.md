# Workflow Primitive Contract Family Inventory

Status: characterization for Batch 2 Packet 03
Source candidate: `MR-MAINT-004`
Current source: `src/millrace_ai/architecture/workflow_primitives.py`

This inventory records the current public contract families before any Batch 5
module split. No production source has moved in this packet.

## Current Public Surface

All public names below are currently defined in
`millrace_ai.architecture.workflow_primitives` and re-exported from
`millrace_ai.architecture`. During extraction, keep both import paths working:

- `from millrace_ai.architecture.workflow_primitives import ...`
- `from millrace_ai.architecture import ...`

The target package should expose the same names from
`millrace_ai.architecture.workflow_primitives.__init__` while
`millrace_ai.architecture.__init__` remains the stable package facade.

## Contract Families

| Family | Public model names | Current import/export location | Current consumers | Direct tests | Proposed destination | Compatibility re-export needs |
| --- | --- | --- | --- | --- | --- | --- |
| Identifiers and graph references | `WorkflowPrimitiveId`, `WorkItemFamilyId`, `DocumentAdapterId`, `QueueClaimPolicyId`, `TerminalActionId`, `LifecycleMutationPlanId`, `RuntimeEffectHandlerId`, `RuntimeEffectRuleId`, `RequestContextProfileId`, `ArtifactContractId`, `RuntimeEffectMutationPhaseValue`; graph-reference fields on `WorkItemPartitionSelectorDefinition`, `WorkflowLaneDefinition`, `WorkflowPlaneSchedulerPolicyDefinition` | Defined in `architecture/workflow_primitives.py`; re-exported by `architecture/__init__.py` | Pydantic model field types across the workflow primitive module; frozen plan fields in `architecture/materialization.py`; compiler validation and workspace compilation | Indirectly covered by `tests/architecture/test_workflow_primitives.py`; lane aliases covered by `tests/architecture/test_lane_contracts.py`; shipped asset round trips in `tests/assets/test_workflow_assets.py` | `architecture/workflow_primitives/identifiers.py` for aliases; graph-reference-bearing models stay with their semantic family | Re-export all aliases from package `workflow_primitives.__init__` and `architecture.__init__`; no separate compatibility path for private field-level references |
| Work-item families | `WorkItemQueueDirs`, `WorkItemFamilyDefinition` | Same as above | `assets/workflows.py`; `architecture/materialization.py`; `compilation/validation.py`; `compilation/workspace_plan.py`; `workspace/queue_store.py`; `workspace/queue_selection.py`; `workspace/queue_lifecycle.py`; `workspace/operator_interventions.py`; `workspace/work_inventory.py`; `runtime/engine.py`; `runtime/blocked_recovery.py`; `runtime/control_mutations.py`; CLI and workspace tests create custom families | `tests/architecture/test_workflow_primitives.py` validates lifecycle membership, path safety, duplicate states, and construction; `tests/assets/test_workflow_assets.py` validates shipped and custom family loading; secondary coverage in workspace/runtime/CLI tests | `architecture/workflow_primitives/work_item_families.py` | Re-export `WorkItemQueueDirs`, `WorkItemFamilyDefinition`, and `WorkItemFamilyId` from both current facades |
| Document adapters | `WorkItemDocumentAdapterDefinition`, `DocumentAdapterId` | Same as above | `assets/workflows.py`; `doctor.py`; `runtime/blocked_recovery.py`; `architecture/materialization.py`; compiler validation through loaded workflow primitive bundle | `tests/architecture/test_workflow_primitives.py` covers duplicate family ids and capability requirement; `tests/assets/test_workflow_assets.py` covers shipped/custom adapter discovery; `tests/workspace/test_doctor.py` provides secondary behavior coverage | `architecture/workflow_primitives/document_adapters.py` | Re-export model and id alias from both current facades |
| Artifact contracts | `ArtifactFormat`, `ArtifactFilenameAdapterDefinition`, `ArtifactContractDefinition`, `ArtifactContractId` | Same as above | `assets/workflows.py`; `architecture/materialization.py`; `runtime/artifact_contracts.py`; `runtime/request_context.py`; `runtime/result_application.py`; `compilation/validation.py`; `compilation/workspace_plan.py` | `tests/architecture/test_workflow_primitives.py` covers filename safety, duplicate filenames, adapter coverage, format matching, required identity fields, and exported round trip; `tests/assets/test_workflow_assets.py` covers shipped inventory and filename ownership; `tests/runtime/test_artifact_contracts.py` and `tests/runtime/test_result_application.py` provide runtime secondary coverage; `tests/compilation/test_workflow_validation.py` covers cross-contract validation | `architecture/workflow_primitives/artifact_contracts.py` | Re-export enum, models, and id alias from both current facades |
| Request-context profiles | `RequestContextProfileDefinition`, `RequestContextRenderPlan`, `RequestContextProfileId` | Same as above | `assets/workflows.py`; `architecture/materialization.py` stores profiles indirectly through compiled assets; `compilation/validation.py`; `runtime/request_context.py` has a runtime-local `RequestContextRenderPlan` with the same class name but a separate implementation | `tests/architecture/test_workflow_primitives.py` covers unsafe output path preferences and exported render-plan round trip; `tests/assets/test_workflow_assets.py` covers shipped profile discovery; `tests/runtime/test_request_context.py` covers runtime render behavior for the runtime-local plan | `architecture/workflow_primitives/request_context_profiles.py` | Re-export architecture `RequestContextProfileDefinition`, architecture `RequestContextRenderPlan`, and id alias from both current facades; document the name collision with `millrace_ai.runtime.request_context.RequestContextRenderPlan` during Batch 5 |
| Lifecycle declarations | `TerminalActionDefinition`, `TerminalActionId`, `LifecycleMutationPlanDefinition`, `LifecycleMutationPlanId` | Same as above | `assets/workflows.py`; `architecture/materialization.py`; `compilation/validation.py`; `compilation/workspace_plan.py`; runtime lifecycle interpretation via compiled plan dictionaries | `tests/architecture/test_workflow_primitives.py` covers required lifecycle plan and mutation coherence; `tests/assets/test_workflow_assets.py` covers shipped action/plan loading; `tests/compilation/test_workflow_validation.py` covers unknown lifecycle plan diagnostics | `architecture/workflow_primitives/lifecycle.py` | Re-export action/plan models and id aliases from both current facades |
| Concurrency declarations | `WorkItemPartitionSelectorDefinition`, `PlaneQueueClaimPolicyDefinition`, `QueueClaimPolicyId`, `WorkflowLaneDefinition`, `LaneConflictPolicyDefinition`, `WorkflowPlaneSchedulerPolicyDefinition` | Same as above | `compilation/workspace_plan.py`; `compilation/validation.py`; `runtime/lane_conflicts.py`; `workspace/queue_store.py`; `workspace/queue_selection.py`; `runtime/activation.py`; `architecture/materialization.py`; `assets/workflows.py` for claim policies | `tests/architecture/test_lane_contracts.py` covers accepted-family alias, production single-lane guard, experimental multi-lane requirements, and lane-pair derivation; `tests/architecture/test_workflow_primitives.py` covers duplicate claim policy families, custom partition selector requirement, lane conflict lock order, and scheduler closure; `tests/runtime/test_lane_conflicts.py` provides secondary runtime coverage | `architecture/workflow_primitives/concurrency.py` | Re-export all scheduler, lane, conflict, selector, and claim-policy models from both current facades |
| Completion behavior | `WorkflowCompletionBehaviorDefinition` | Same as above | Currently loaded as workflow primitive assets when present; compiler validation and frozen graph completion use `GraphLoopCompletionBehaviorDefinition` from `loop_graphs.py`, so this model is mostly declaration authority for the workflow-primitive side of completion | Exported round trip in `tests/architecture/test_workflow_primitives.py`; shipped workflow bundle currently does not assert a concrete `WorkflowCompletionBehaviorDefinition` asset | `architecture/workflow_primitives/completion.py` | Re-export model from both current facades; Batch 5 should add direct asset coverage before moving if shipped completion assets are introduced |
| Closure root-source contracts and policies | Closure-related fields on `WorkItemFamilyDefinition`, `PlaneQueueClaimPolicyDefinition`, `RuntimeEffectRuleDefinition`, `WorkflowCompletionBehaviorDefinition`; no standalone public model today | Same containing models as above | `compilation/validation.py` enforces cross-primitive closure and lineage rules; `compilation/workspace_plan.py` freezes validated primitive data; runtime completion and Blueprint closure behavior consume compiled graph authority | `tests/architecture/test_workflow_primitives.py` covers closure-blocking lifecycle membership and completion declaration round trip; `tests/assets/test_workflow_assets.py` covers planning claim policy `empty_behavior=check_completion`; `tests/compilation/test_workflow_validation.py` has secondary cross-contract coverage | Keep field declarations with owning semantic models: `work_item_families.py`, `concurrency.py`, `completion.py`, and `artifact_contracts.py`/`recovery_policies.py` as applicable. Do not create a root-source grab-bag module unless a later packet introduces a real standalone abstraction | No standalone compatibility re-export is needed because there is no standalone public model; preserve containing model exports |
| Recovery and failure policies | `WorkflowRecoveryPolicyDefinition`, `RuntimeFailurePolicyDefinition`, `RuntimeEffectMutationPhaseValue` | Same as above | `assets/workflows.py`; `architecture/materialization.py`; `compilation/validation.py`; `compilation/workspace_plan.py`; runtime error/effect recovery through compiled plan authority | `tests/architecture/test_workflow_primitives.py` covers exhausted target shape, recovery-node requirement, and runtime-effect route inputs; `tests/assets/test_workflow_assets.py` covers shipped recovery/failure policy loading; `tests/compilation/test_workflow_validation.py` covers runtime-effect recovery route closure, undeclared classes, wrong planes, terminal targets, and partial-mutation restrictions | `architecture/workflow_primitives/recovery_policies.py` | Re-export policy models and mutation-phase alias from both current facades |
| Runtime effect declarations | `RuntimeEffectHandlerDefinition`, `RuntimeEffectHandlerId`, `RuntimeEffectRuleDefinition`, `RuntimeEffectRuleId`, `OutcomeArtifactDefinition` | Same as above | `assets/workflows.py`; `architecture/materialization.py`; `compilation/validation.py`; `compilation/workspace_plan.py`; `runtime/effect_execution.py` uses `RuntimeEffectRuleDefinition` for type-checking; runtime effect handlers are selected through compiled plan data | `tests/architecture/test_workflow_primitives.py` covers handler lifecycle metadata, rule destination requirement, and outcome artifact round trip; `tests/assets/test_workflow_assets.py` covers shipped effect handler/rule inventory; `tests/compilation/test_workflow_validation.py` covers unknown handlers/artifacts/capabilities and Blueprint repair effect route closure | `architecture/workflow_primitives/runtime_effects.py`. This adds a small destination beyond the source spec's shorter module list because handlers/rules are large and change for a different reason than generic recovery policies | Re-export handler/rule/outcome models and id aliases from both current facades |
| Operator controls | `OperatorControlCapabilityDefinition` | Same as above | Currently public and covered by architecture tests; no production loader in `assets/workflows.py` yet, so future registry/discovery work would be a separate asset-loading change | Exported round trip and target-scope validation in `tests/architecture/test_workflow_primitives.py` | `architecture/workflow_primitives/operator_controls.py` | Re-export model from both current facades |
| Schema epochs | `WorkspaceSchemaEpochDefinition` | Same as above | `assets/workflows.py`; `architecture/materialization.py`; `compilation/workspace_plan.py`; workspace baseline/upgrade and doctor flows through compiled plan/schema epoch authority | `tests/architecture/test_workflow_primitives.py` covers exported round trip; `tests/assets/test_workflow_assets.py` covers shipped epoch discovery/load; workspace upgrade tests provide secondary behavior coverage | `architecture/workflow_primitives/schema_epochs.py` | Re-export model from both current facades |
| Reusable validation helpers | `_canonical`, `_ensure_sequence`, `_normalize_unique_id_tuple`, `_normalize_unique_status_tuple`, `_reject_duplicates`, `_normalize_runtime_relative_path`, `_normalize_artifact_filename`, `_normalize_file_extension` | Private functions in `architecture/workflow_primitives.py`; not re-exported | Only validators in the same module today | Indirectly covered by all `tests/architecture/test_workflow_primitives.py` validation cases and shipped asset loading tests | `architecture/workflow_primitives/_validation.py` | No public compatibility re-export. Keep private underscore names private; each extracted model module may import from `_validation.py` |

## Coverage Notes

No new tests were added in this packet. Every public family that is likely to
move in Batch 5 has at least one direct architecture test, and most shipped
asset-backed families also have asset discovery coverage. The thinnest direct
coverage is `WorkflowCompletionBehaviorDefinition`, because it currently has an
architecture round-trip test but no shipped asset assertion. Add a focused
asset/discovery test before moving it if Batch 5 also introduces or moves
completion registry loading.

`RequestContextRenderPlan` needs special care during migration because
`millrace_ai.runtime.request_context` defines a runtime render-plan model with
the same public class name. The architecture model should move with
request-context primitive declarations; the runtime model should remain a
runtime rendering view model unless a later design intentionally unifies them.

## Families To Keep Together

- Keep `TerminalActionDefinition` and `LifecycleMutationPlanDefinition`
  together. Terminal actions are not understandable without their mutation-plan
  contract, and separating them would force readers to hop between modules for
  one lifecycle decision.
- Keep `WorkflowLaneDefinition`, `LaneConflictPolicyDefinition`,
  `PlaneQueueClaimPolicyDefinition`, `WorkItemPartitionSelectorDefinition`, and
  `WorkflowPlaneSchedulerPolicyDefinition` together. Their validators form one
  scheduler/concurrency closure.
- Keep `RuntimeEffectHandlerDefinition`, `RuntimeEffectRuleDefinition`, and
  `OutcomeArtifactDefinition` together. Handler/rule/artifact outcome metadata
  changes as one runtime-effect declaration family.
- Keep closure and root-source policy fields on their owning models instead of
  extracting a standalone closure module. Today those policies are field-level
  constraints across work-item families, claim policies, effect rules, and
  completion behavior; a separate module would increase cognitive load without
  creating a cleaner import direction.
- Keep private validation helpers in one `_validation.py` module after the
  split. Duplicating sequence/id/path normalization across extracted files
  would make public validation behavior harder to keep stable.

## Batch 5 Migration Guardrails

- Move lower-level aliases and private validation helpers first.
- Extract family modules one at a time and re-export from
  `workflow_primitives.__init__`.
- Keep `architecture.__init__` exports stable in the same patch that moves a
  family.
- Add import-compatibility tests that assert the moved public names remain
  available from both current import paths.
- Preserve Pydantic validation message substrings already asserted by
  architecture and compilation tests.
