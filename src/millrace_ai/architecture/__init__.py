"""Additive loop-architecture contracts and helpers."""

from millrace_ai.architecture.loop_graphs import (
    GraphLoopCompletionBehaviorDefinition,
    GraphLoopDefinition,
    GraphLoopEdgeDefinition,
    GraphLoopEdgeKind,
    GraphLoopEntryDefinition,
    GraphLoopEntryKey,
    GraphLoopNodeDefinition,
    GraphLoopTerminalClass,
    GraphLoopTerminalStateDefinition,
)
from millrace_ai.architecture.materialization import (
    FrozenGraphPlanePlan,
    FrozenGraphRunPlan,
    MaterializedGraphNodePlan,
)
from millrace_ai.architecture.stage_kinds import (
    ArchitectureContractModel,
    RecoveryRole,
    RegisteredStageKindDefinition,
    StageIdempotencePolicy,
)

__all__ = [
    "ArchitectureContractModel",
    "FrozenGraphPlanePlan",
    "FrozenGraphRunPlan",
    "GraphLoopCompletionBehaviorDefinition",
    "GraphLoopDefinition",
    "GraphLoopEdgeDefinition",
    "GraphLoopEdgeKind",
    "GraphLoopEntryDefinition",
    "GraphLoopEntryKey",
    "GraphLoopNodeDefinition",
    "GraphLoopTerminalClass",
    "GraphLoopTerminalStateDefinition",
    "MaterializedGraphNodePlan",
    "RecoveryRole",
    "RegisteredStageKindDefinition",
    "StageIdempotencePolicy",
]
