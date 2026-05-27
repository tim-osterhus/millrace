from __future__ import annotations

import importlib

import pytest

PUBLIC_IMPORT_SURFACES = (
    "millrace_ai.compiler",
    "millrace_ai.compilation.validation",
    "millrace_ai.architecture.workflow_primitives",
    "millrace_ai.architecture",
    "millrace_ai.runtime.request_context",
    "millrace_ai.runtime.effects.operations",
    "millrace_ai.runtime.blueprint_effects",
)

EXPECTED_PUBLIC_SYMBOLS: dict[str, tuple[str, ...]] = {
    "millrace_ai.compilation.validation": (
        "validate_lane_conflict_coverage",
        "validate_mode_stage_maps",
        "validate_workflow_primitives",
    ),
    "millrace_ai.architecture.workflow_primitives": (
        "ArtifactContractDefinition",
        "ArtifactContractId",
        "ArtifactFilenameAdapterDefinition",
        "ArtifactFormat",
        "DocumentAdapterId",
        "LaneConflictPolicyDefinition",
        "LifecycleMutationPlanDefinition",
        "LifecycleMutationPlanId",
        "OperatorControlCapabilityDefinition",
        "OutcomeArtifactDefinition",
        "PlaneQueueClaimPolicyDefinition",
        "QueueClaimPolicyId",
        "RequestContextProfileDefinition",
        "RequestContextProfileId",
        "RequestContextProviderDefinition",
        "RequestContextProviderId",
        "RequestContextRenderPlan",
        "RequestContextRenderPlanId",
        "RuntimeEffectHandlerDefinition",
        "RuntimeEffectHandlerId",
        "RuntimeEffectMutationPhaseValue",
        "RuntimeEffectOperationRunnerDefinition",
        "RuntimeEffectOperationRunnerId",
        "RuntimeEffectRuleDefinition",
        "RuntimeEffectRuleId",
        "RuntimeFailurePolicyDefinition",
        "RuntimeFailurePolicyRepairClosureMappingDefinition",
        "TerminalActionDefinition",
        "TerminalActionId",
        "WorkItemDocumentAdapterDefinition",
        "WorkItemFamilyDefinition",
        "WorkItemFamilyId",
        "WorkItemPartitionSelectorDefinition",
        "WorkItemQueueDirs",
        "WorkflowCompletionBehaviorDefinition",
        "WorkflowLaneDefinition",
        "WorkflowPlaneSchedulerPolicyDefinition",
        "WorkflowPrimitiveId",
        "WorkflowRecoveryPolicyDefinition",
        "WorkspaceSchemaEpochDefinition",
    ),
    "millrace_ai.architecture": (
        "ArchitectureContractModel",
        "ArtifactContractDefinition",
        "ArtifactContractId",
        "ArtifactFilenameAdapterDefinition",
        "ArtifactFormat",
        "CompileInputFingerprint",
        "CompiledRunPlan",
        "CompiledGraphCompletionEntryPlan",
        "CompiledGraphEntryPlan",
        "CompiledGraphResumePolicyPlan",
        "CompiledGraphThresholdPolicyPlan",
        "CompiledGraphTransitionPlan",
        "DocumentAdapterId",
        "FrozenGraphPlanePlan",
        "GraphLoopCounterName",
        "GraphLoopCompletionBehaviorDefinition",
        "GraphLoopDynamicPoliciesDefinition",
        "GraphLoopDefinition",
        "GraphLoopEdgeDefinition",
        "GraphLoopEdgeKind",
        "GraphLoopEntryDefinition",
        "GraphLoopEntryKey",
        "GraphLoopEntryKeyValue",
        "GraphLoopNodeDefinition",
        "GraphLoopResumePolicyDefinition",
        "GraphLoopRootSourcePolicyDefinition",
        "GraphLoopRuntimeFailureRecoveryDefinition",
        "GraphLoopTerminalClass",
        "GraphLoopTerminalStateDefinition",
        "GraphLoopThresholdPolicyDefinition",
        "LaneConflictPolicyDefinition",
        "LifecycleMutationPlanDefinition",
        "LifecycleMutationPlanId",
        "MaterializedGraphNodePlan",
        "OperatorControlCapabilityDefinition",
        "OutcomeArtifactDefinition",
        "PlaneQueueClaimPolicyDefinition",
        "QueueClaimPolicyId",
        "RecoveryRole",
        "RegisteredStageKindDefinition",
        "RequestContextProfileDefinition",
        "RequestContextProfileId",
        "RequestContextProviderDefinition",
        "RequestContextProviderId",
        "RequestContextRenderPlan",
        "RequestContextRenderPlanId",
        "ResolvedAssetRef",
        "RuntimeEffectFailureMappingDefinition",
        "RuntimeEffectHandlerDefinition",
        "RuntimeEffectHandlerId",
        "RuntimeEffectIdempotencyDefinition",
        "RuntimeEffectMutationJournalDefinition",
        "RuntimeEffectMutationPhaseValue",
        "RuntimeEffectOperationDefinition",
        "RuntimeEffectOperationId",
        "RuntimeEffectOperationRunnerDefinition",
        "RuntimeEffectOperationRunnerId",
        "RuntimeEffectOperationStepDefinition",
        "RuntimeEffectPrimitiveId",
        "RuntimeEffectRepairClosureContractDefinition",
        "RuntimeEffectRuleDefinition",
        "RuntimeEffectRuleId",
        "RuntimeEffectStepId",
        "RuntimeEffectStoreDefinition",
        "RuntimeEffectStoreId",
        "RuntimeEffectValidatorDefinition",
        "RuntimeEffectValidatorId",
        "RuntimeFailurePolicyDefinition",
        "RuntimeFailurePolicyRepairClosureMappingDefinition",
        "StageIdempotencePolicy",
        "TerminalActionDefinition",
        "TerminalActionId",
        "WorkItemDocumentAdapterDefinition",
        "WorkItemFamilyDefinition",
        "WorkItemFamilyId",
        "WorkItemPartitionSelectorDefinition",
        "WorkItemQueueDirs",
        "WorkflowCompletionBehaviorDefinition",
        "WorkflowLaneDefinition",
        "WorkflowPlaneSchedulerPolicyDefinition",
        "WorkflowPrimitiveId",
        "WorkflowRecoveryPolicyDefinition",
        "WorkspaceSchemaEpochDefinition",
    ),
    "millrace_ai.runtime.request_context": (
        "RenderedRequestContext",
        "RequestContextRenderPlan",
        "attach_default_request_context",
        "render_request_context",
    ),
    "millrace_ai.runtime.effects.operations": (
        "CONTRACTOR_BLUEPRINT_OPERATION_ID",
        "EVALUATOR_BLUEPRINT_APPROVAL_OPERATION_ID",
        "EVALUATOR_BLUEPRINT_REJECTION_OPERATION_ID",
        "MANAGER_BLUEPRINT_OPERATION_ID",
        "MECHANIC_BLUEPRINT_REPAIR_OPERATION_ID",
        "contractor_blueprint_candidate_persist",
        "evaluator_blueprint_approved_to_task",
        "evaluator_blueprint_rejected_to_draft_revision",
        "manager_blueprint_manifest_to_blueprint_drafts",
        "mechanic_blueprint_repair_apply",
    ),
    "millrace_ai.runtime.blueprint_effects": (
        "CONTRACTOR_BLUEPRINT_HANDLER_ID",
        "EVALUATOR_BLUEPRINT_APPROVAL_HANDLER_ID",
        "EVALUATOR_BLUEPRINT_REJECTION_HANDLER_ID",
        "MANAGER_BLUEPRINT_HANDLER_ID",
        "MECHANIC_BLUEPRINT_REPAIR_HANDLER_ID",
        "contractor_blueprint_candidate_persist",
        "evaluator_blueprint_approved_to_task",
        "evaluator_blueprint_rejected_to_draft_revision",
        "manager_blueprint_manifest_to_blueprint_drafts",
        "mechanic_blueprint_repair_apply",
    ),
}


@pytest.mark.parametrize("module_name", PUBLIC_IMPORT_SURFACES)
def test_public_import_surface_imports(module_name: str) -> None:
    importlib.import_module(module_name)


@pytest.mark.parametrize("module_name, expected_symbols", EXPECTED_PUBLIC_SYMBOLS.items())
def test_public_import_surface_exports_expected_symbols(
    module_name: str,
    expected_symbols: tuple[str, ...],
) -> None:
    module = importlib.import_module(module_name)

    exported_symbols = tuple(module.__all__)

    assert len(exported_symbols) == len(set(exported_symbols))
    assert set(exported_symbols) == set(expected_symbols)
    for symbol in expected_symbols:
        assert hasattr(module, symbol), f"{module_name} missing public symbol {symbol}"


def test_validation_public_symbols_support_direct_import() -> None:
    from millrace_ai.compilation.validation import (
        validate_lane_conflict_coverage,
        validate_mode_stage_maps,
        validate_workflow_primitives,
    )

    assert callable(validate_lane_conflict_coverage)
    assert callable(validate_mode_stage_maps)
    assert callable(validate_workflow_primitives)
