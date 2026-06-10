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

## Package Root Compatibility Facades

These package-root facades remain importable for older integrations while
active runtime authority lives in focused packages and compiled graph/workflow
metadata:

- `millrace_ai.router`
- `millrace_ai.compiler`
- `millrace_ai.queue_store`
- `millrace_ai.runner`
- `millrace_ai.paths`
- `millrace_ai.state_store`
- `millrace_ai.stage_kinds`
- `millrace_ai.loop_graphs`

The run-14759-24 legacy graph-authority cleanup revalidated all eight imports.
The cleanup did not move or retire these facades; it only removed remaining
legacy active-authority paths from graph-authority formatting, Recon result
detection, and per-plane graph-authority compatibility wrappers.

## Runtime Package Facade

`millrace_ai.runtime` is the stable public runtime package facade. It exports:

- `RuntimeEngine`
- `RuntimeDaemonSupervisor`
- `NullRuntimeMonitorSink`
- `RuntimeMonitorEvent`
- `RuntimeMonitorSink`
- `RuntimeTickOutcome`
- `StageWorkerOutcome`

Run-14759-75 revalidated that `import millrace_ai.runtime` and
`from millrace_ai.runtime import RuntimeEngine` keep these symbols importable
without eagerly loading the seven forbidden domain/startup prefixes:
`millrace_ai.recon_packets`, `millrace_ai.runtime.completion_behavior`,
`millrace_ai.runtime.graph_authority.validation`,
`millrace_ai.runtime.learning_promotions`,
`millrace_ai.runtime.learning_triggers`,
`millrace_ai.workspace.arbiter_state`, and
`millrace_ai.workspace.blueprint_state`.

Run-14759-76 preserved that guarantee while routing closure lifecycle callers
through `millrace_ai.runtime.closure_boundary`; `completion_behavior` remains
the forbidden startup prefix and the boundary's lazy internal implementation,
not a public runtime import dependency.

Run-14759-77 preserved the same forbidden-prefix guarantee while routing
Learning trigger/promotion callers through extension-boundary handlers in both
`runtime/tick_cycle.py` and `runtime/supervisor.py`, and while removing the
direct Recon-domain recovery import from `runtime/error_recovery.py`.

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
re-exports graph, materialization, stage-kind, runtime-effect operation and
primitive, and workflow primitive contracts. Expected post-package `__all__`:

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
- `RuntimeEffectPrimitiveDefinition`
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

## Extension Boundary Compatibility Surface

`millrace_ai.extensions` is the public extension boundary package facade
(`extensions/__init__.py`). It exports:

- `ExtensionDomain`, `ExtensionItemKind`, `ExtensionItemManifest`,
  `ExtensionPackageManifest` — manifest data models for extension packages
- `BuiltInExtensionBoundaryRegistry`, `builtin_extension_boundary_registry` —
  lazy singleton registry that resolves built-in domain implementations by
  interface ID without importing domain modules directly
- `ArtifactAdapter`, `ContextProvider`, `ReconTransitionHandler`,
  `ClosureTransitionHandler`, `BlueprintValidator`,
  `BlueprintContextProvider`, `LearningTriggerHandler`,
  `LearningPromotionHandler` — built-in domain boundary Protocols defined in
  `extensions/interfaces.py`
- `BUILTIN_INTERFACE_IDS` — canonical interface ID constants

The registry resolves implementations lazily. Calling
`builtin_extension_boundary_registry().get_recon_transition_handler()` only
imports Recon domain code when requested, preserving the kernel boundary for
minimal fixture configs.

Built-in adapters under `extensions/builtin/` bridge the existing legacy domain
modules to the Protocol interfaces. These adapters are the *only* place
kernel-to-domain imports should occur outside the documented compatibility
facades recorded in `docs/adr/0016-extension-boundary-compatibility-facades.md`.

Import guardrail tests in `tests/maintenance/test_kernel_import_guardrails.py`
protect this boundary.
