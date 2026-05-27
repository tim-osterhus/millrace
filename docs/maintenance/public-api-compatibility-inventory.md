# Public API Compatibility Inventory

This page freezes the import and symbol surfaces that the maintainability
follow-up refactor must preserve while files become packages and implementation
ownership moves. Removing one of these surfaces requires a separate ADR,
release-note path, and compatibility plan.

Baseline: `18fd133` on `main` (`origin/main`) before follow-up Batch 0 source
movement.

## Import Surfaces

The following module imports are compatibility contracts for this refactor wave:

- `millrace_ai.compiler`
- `millrace_ai.compilation.validation`
- `millrace_ai.architecture.workflow_primitives`
- `millrace_ai.architecture`
- `millrace_ai.runtime.request_context`
- `millrace_ai.runtime.effects.operations`
- `millrace_ai.runtime.blueprint_effects`

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
- `RequestContextRenderPlan`
- `RuntimeEffectHandlerDefinition`
- `RuntimeEffectHandlerId`
- `RuntimeEffectMutationPhaseValue`
- `RuntimeEffectOperationRunnerDefinition`
- `RuntimeEffectOperationRunnerId`
- `RuntimeEffectRuleDefinition`
- `RuntimeEffectRuleId`
- `RuntimeFailurePolicyDefinition`
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
- `RequestContextRenderPlan`
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
- `RuntimeEffectRuleDefinition`
- `RuntimeEffectRuleId`
- `RuntimeEffectStepId`
- `RuntimeEffectStoreDefinition`
- `RuntimeEffectStoreId`
- `RuntimeEffectValidatorDefinition`
- `RuntimeEffectValidatorId`
- `RuntimeFailurePolicyDefinition`
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

`millrace_ai.runtime.request_context` remains the runtime rendering facade for
runner request context:

- `RenderedRequestContext`
- `RequestContextRenderPlan`
- `attach_default_request_context`
- `render_request_context`

Future provider/registry extraction must preserve these imports while moving
generic and Blueprint-specific context providers behind narrower modules.

## Runtime Effect Operations

`millrace_ai.runtime.effects.operations` is currently the public operation-runner
facade. Batch 2 of the follow-up wave may split implementation modules, but
this import path and these names stay compatible:

- `CONTRACTOR_BLUEPRINT_OPERATION_ID`
- `EVALUATOR_BLUEPRINT_APPROVAL_OPERATION_ID`
- `EVALUATOR_BLUEPRINT_REJECTION_OPERATION_ID`
- `MANAGER_BLUEPRINT_OPERATION_ID`
- `MECHANIC_BLUEPRINT_REPAIR_OPERATION_ID`
- `contractor_blueprint_candidate_persist`
- `evaluator_blueprint_approved_to_task`
- `evaluator_blueprint_rejected_to_draft_revision`
- `manager_blueprint_manifest_to_blueprint_drafts`
- `mechanic_blueprint_repair_apply`

The constants are operation ids, not new public handler authority.

## Blueprint Effect Compatibility Facade

`millrace_ai.runtime.blueprint_effects` is a temporary compatibility facade for
old imports and legacy handler-id names. It must remain importable through the
next public release unless a later ADR changes that plan:

- `CONTRACTOR_BLUEPRINT_HANDLER_ID`
- `EVALUATOR_BLUEPRINT_APPROVAL_HANDLER_ID`
- `EVALUATOR_BLUEPRINT_REJECTION_HANDLER_ID`
- `MANAGER_BLUEPRINT_HANDLER_ID`
- `MECHANIC_BLUEPRINT_REPAIR_HANDLER_ID`
- `contractor_blueprint_candidate_persist`
- `evaluator_blueprint_approved_to_task`
- `evaluator_blueprint_rejected_to_draft_revision`
- `manager_blueprint_manifest_to_blueprint_drafts`
- `mechanic_blueprint_repair_apply`

The facade delegates to operation runners and must not regain durable mutation
logic.
