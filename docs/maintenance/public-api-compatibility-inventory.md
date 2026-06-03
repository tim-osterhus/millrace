# Public API Compatibility Inventory

This page freezes the import and symbol surfaces that the maintainability
follow-up refactor must preserve while files become packages and implementation
ownership moves. Removing one of these surfaces requires a separate ADR,
release-note path, and compatibility plan.

Baseline: `18fd133` on `main` (`origin/main`) before follow-up Batch 0 source
movement.

FU-9 status: revalidated against `tests/test_public_import_surfaces.py` and
live module `__all__` exports after the follow-up refactor wave.

## Import Surfaces

The following module imports are compatibility contracts for this refactor wave:

- `millrace_ai.compiler`
- `millrace_ai.compilation.validation`
- `millrace_ai.architecture.workflow_primitives`
- `millrace_ai.architecture`
- `millrace_ai.runtime.request_context`

`tests/test_public_import_surfaces.py` protects these imports.

## Compiler Validation

`millrace_ai.compilation.validation` currently exposes three deliberate public
validator functions:

- `validate_lane_conflict_coverage`
- `validate_mode_stage_maps`
- `validate_workflow_primitives`

Expected post-package surface: `millrace_ai.compilation.validation` may become
a package, but importing this module and these symbols must keep working.
Additional validator-family modules should remain internal unless a later
packet deliberately adds a public export.

## Workflow Primitive Contracts

`millrace_ai.architecture.workflow_primitives` is the public contract-family
facade for workflow primitive schemas. Expected post-package `__all__`:

- `ArtifactContractDefinition`
- `ArtifactContractId`
- `ArtifactFilenameAdapterDefinition`
- `ArtifactFormat`
- `DocumentAdapterId`
- `LaneConflictPolicyDefinition`
- `LifecycleMutationPlanDefinition`
- `LifecycleMutationPlanId`
- `OperatorControlCapabilityDefinition`
- `OutcomeArtifactDefinition`
- `PlaneQueueClaimPolicyDefinition`
- `QueueClaimPolicyId`
- `RequestContextProfileDefinition`
- `RequestContextProfileId`
- `RequestContextProviderDefinition`
- `RequestContextProviderId`
- `RequestContextRenderPlan`
- `RequestContextRenderPlanId`
- `RuntimeEffectHandlerDefinition`
- `RuntimeEffectHandlerId`
- `RuntimeEffectMutationPhaseValue`
- `RuntimeEffectOperationRunnerDefinition`
- `RuntimeEffectOperationRunnerId`
- `RuntimeEffectRuleDefinition`
- `RuntimeEffectRuleId`
- `RuntimeFailurePolicyDefinition`
- `RuntimeFailurePolicyRepairClosureMappingDefinition`
- `TerminalActionDefinition`
- `TerminalActionId`
- `WorkItemDocumentAdapterDefinition`
- `WorkItemFamilyDefinition`
- `WorkItemFamilyId`
- `WorkItemPartitionSelectorDefinition`
- `WorkItemQueueDirs`
- `WorkflowCompletionBehaviorDefinition`
- `WorkflowLaneDefinition`
- `WorkflowPlaneSchedulerPolicyDefinition`
- `WorkflowPrimitiveId`
- `WorkflowRecoveryPolicyDefinition`
- `WorkspaceSchemaEpochDefinition`

The architecture request-context `RequestContextRenderPlan` is intentionally
distinct from `millrace_ai.runtime.request_context.RequestContextRenderPlan`.
Both public names must remain importable, but they are different models.

## Architecture Package Facade

`millrace_ai.architecture` is the broader public architecture facade. It
re-exports graph, materialization, stage-kind, runtime-effect operation, and
workflow primitive contracts. Expected post-package `__all__`:

- `ArchitectureContractModel`
- `ArtifactContractDefinition`
- `ArtifactContractId`
- `ArtifactFilenameAdapterDefinition`
- `ArtifactFormat`
- `CompileInputFingerprint`
- `CompiledRunPlan`
- `CompiledGraphCompletionEntryPlan`
- `CompiledGraphEntryPlan`
- `CompiledGraphResumePolicyPlan`
- `CompiledGraphThresholdPolicyPlan`
- `CompiledGraphTransitionPlan`
- `DocumentAdapterId`
- `FrozenGraphPlanePlan`
- `GraphLoopCounterName`
- `GraphLoopCompletionBehaviorDefinition`
- `GraphLoopDynamicPoliciesDefinition`
- `GraphLoopDefinition`
- `GraphLoopEdgeDefinition`
- `GraphLoopEdgeKind`
- `GraphLoopEntryDefinition`
- `GraphLoopEntryKey`
- `GraphLoopEntryKeyValue`
- `GraphLoopNodeDefinition`
- `GraphLoopResumePolicyDefinition`
- `GraphLoopRootSourcePolicyDefinition`
- `GraphLoopRuntimeFailureRecoveryDefinition`
- `GraphLoopTerminalClass`
- `GraphLoopTerminalStateDefinition`
- `GraphLoopThresholdPolicyDefinition`
- `LaneConflictPolicyDefinition`
- `LifecycleMutationPlanDefinition`
- `LifecycleMutationPlanId`
- `MaterializedGraphNodePlan`
- `OperatorControlCapabilityDefinition`
- `OutcomeArtifactDefinition`
- `PlaneQueueClaimPolicyDefinition`
- `QueueClaimPolicyId`
- `RecoveryRole`
- `RegisteredStageKindDefinition`
- `RequestContextProfileDefinition`
- `RequestContextProfileId`
- `RequestContextProviderDefinition`
- `RequestContextProviderId`
- `RequestContextRenderPlan`
- `RequestContextRenderPlanId`
- `ResolvedAssetRef`
- `RuntimeEffectFailureMappingDefinition`
- `RuntimeEffectHandlerDefinition`
- `RuntimeEffectHandlerId`
- `RuntimeEffectIdempotencyDefinition`
- `RuntimeEffectMutationJournalDefinition`
- `RuntimeEffectMutationPhaseValue`
- `RuntimeEffectOperationDefinition`
- `RuntimeEffectOperationId`
- `RuntimeEffectOperationRunnerDefinition`
- `RuntimeEffectOperationRunnerId`
- `RuntimeEffectOperationStepDefinition`
- `RuntimeEffectPrimitiveId`
- `RuntimeEffectRepairClosureContractDefinition`
- `RuntimeEffectRuleDefinition`
- `RuntimeEffectRuleId`
- `RuntimeEffectStepId`
- `RuntimeEffectStoreDefinition`
- `RuntimeEffectStoreId`
- `RuntimeEffectValidatorDefinition`
- `RuntimeEffectValidatorId`
- `RuntimeFailurePolicyDefinition`
- `RuntimeFailurePolicyRepairClosureMappingDefinition`
- `StageIdempotencePolicy`
- `TerminalActionDefinition`
- `TerminalActionId`
- `WorkItemDocumentAdapterDefinition`
- `WorkItemFamilyDefinition`
- `WorkItemFamilyId`
- `WorkItemPartitionSelectorDefinition`
- `WorkItemQueueDirs`
- `WorkflowCompletionBehaviorDefinition`
- `WorkflowLaneDefinition`
- `WorkflowPlaneSchedulerPolicyDefinition`
- `WorkflowPrimitiveId`
- `WorkflowRecoveryPolicyDefinition`
- `WorkspaceSchemaEpochDefinition`

## Runtime Request Context

`millrace_ai.runtime.request_context` remains the public runtime rendering facade
for runner request context:

- `RenderedRequestContext`
- `RequestContextRenderPlan`
- `attach_default_request_context`
- `render_request_context`

FU-9 status: these imports are preserved while implementation ownership now lives
under `millrace_ai.runtime.context`.

## Runtime Effect Operations

The `millrace_ai.runtime.effects.operations` compatibility facade has been
retired. Runtime-effect operation behavior is selected from compiled asset
metadata and implemented, where Python mutation code is still required, under
`millrace_ai.runtime.effects.operation_runners`.

## Final Facade Status (FU-9)

- `millrace_ai.compilation.validation`: package facade (`compilation/validation/__init__.py`) with stable public validator exports.
- `millrace_ai.architecture.workflow_primitives`: package facade (`architecture/workflow_primitives/__init__.py`) with stable contract-family exports.
- `millrace_ai.runtime.request_context`: compatibility facade over
  `runtime/context/`.
- Runtime-effect operation compatibility facades have been retired; use
  compiled runtime-effect operation/rule/runner metadata and focused operation
  runner modules for implementation tests.

## Blueprint Effect Compatibility Facade

The Blueprint effect compatibility facade has been retired. Operation behavior
is selected by compiled runtime-effect metadata and implemented in focused
operation-runner modules.
