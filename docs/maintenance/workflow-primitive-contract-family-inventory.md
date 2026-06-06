# Workflow Primitive Contract Family Inventory

Status: Batch 7 Packet 02 complete  
Source candidate: `MR-MAINT-004`  
Facade source: `src/millrace_ai/architecture/workflow_primitives/__init__.py`

This inventory records the post-extraction contract-family layout for workflow
primitive architecture models. Public imports remain stable from both:

- `from millrace_ai.architecture.workflow_primitives import ...`
- `from millrace_ai.architecture import ...`

## Contract Family Modules

| Family | Module | Public models and aliases |
| --- | --- | --- |
| Identifier aliases | `workflow_primitives/identifiers.py` | `WorkflowPrimitiveId`, `WorkItemFamilyId`, `DocumentAdapterId`, `QueueClaimPolicyId`, `TerminalActionId`, `LifecycleMutationPlanId`, `RuntimeEffectHandlerId`, `RuntimeEffectRuleId`, `RequestContextProfileId`, `RequestContextProviderId`, `RequestContextRenderPlanId`, `ArtifactContractId`, `RuntimeEffectOperationRunnerId`, `RuntimeEffectMutationPhaseValue` |
| Work-item families | `workflow_primitives/work_families.py` | `WorkItemQueueDirs`, `WorkItemFamilyDefinition` |
| Document adapters | `workflow_primitives/document_adapters.py` | `WorkItemDocumentAdapterDefinition` |
| Artifact contracts | `workflow_primitives/artifact_contracts.py` | `ArtifactFormat`, `ArtifactFilenameAdapterDefinition`, `ArtifactContractDefinition` |
| Request-context profiles | `workflow_primitives/request_context_profiles.py` | `RequestContextProviderDefinition`, `RequestContextProfileDefinition`, `RequestContextRenderPlan` (architecture contract model) |
| Lifecycle declarations | `workflow_primitives/lifecycle.py` | `TerminalActionDefinition`, `LifecycleMutationPlanDefinition` |
| Concurrency declarations | `workflow_primitives/concurrency.py` | `WorkItemPartitionSelectorDefinition`, `PlaneQueueClaimPolicyDefinition`, `WorkflowLaneDefinition`, `LaneConflictPolicyDefinition`, `WorkflowPlaneSchedulerPolicyDefinition` |
| Completion behavior | `workflow_primitives/completion.py` | `WorkflowCompletionBehaviorDefinition` |
| Runtime-effect declarations | `workflow_primitives/runtime_effects.py` | `RuntimeEffectHandlerDefinition`, `RuntimeEffectOperationRunnerDefinition`, `RuntimeEffectRuleDefinition`, `OutcomeArtifactDefinition` |
| Recovery and failure policies | `workflow_primitives/recovery_policies.py` | `WorkflowRecoveryPolicyDefinition`, `RuntimeFailurePolicyRepairClosureMappingDefinition`, `RuntimeFailurePolicyDefinition` |
| Operator controls | `workflow_primitives/operator_controls.py` | `OperatorControlCapabilityDefinition` |
| Schema epochs | `workflow_primitives/schema_epochs.py` | `WorkspaceSchemaEpochDefinition` |
| Shared private validators | `workflow_primitives/_validation.py` | `_canonical`, `_ensure_sequence`, `_normalize_unique_id_tuple`, `_normalize_unique_status_tuple`, `_normalize_runtime_relative_path`, `_normalize_artifact_filename`, `_normalize_file_extension`, `_reject_duplicates` |

## Guardrails Preserved

- Family modules remain declarative and avoid runtime/compiler imports.
- `RuntimeEffectHandlerDefinition`, `RuntimeEffectRuleDefinition`, and
  `OutcomeArtifactDefinition` remain co-located in
  `workflow_primitives/runtime_effects.py`.
- `workflow_primitives/__init__.py` remains the compatibility facade with the
  same public `__all__` contract.
- Runtime-effect operation, store, validator, and primitive models live in
  `src/millrace_ai/architecture/effect_operations.py`, outside this
  workflow-primitive family facade.

## Coverage Notes

- Family placement is asserted in
  `tests/architecture/test_workflow_primitives.py`.
- Representative shipped asset payload round-trips are asserted in
  `tests/assets/test_workflow_assets.py` across work-family, adapter, artifact,
  request-context, lifecycle, concurrency-policy, runtime-effect, recovery, and
  schema-epoch families.
- Architecture request-context contract models are explicitly distinguished from
  runtime rendering models in
  `tests/architecture/test_workflow_primitives.py`.
