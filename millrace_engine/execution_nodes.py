"""Shared execution-node mappings for frozen-plan routing and reporting."""

from __future__ import annotations

from .contracts import ExecutionStatus, StageType


EXECUTION_NODE_STAGE_TYPES: dict[str, StageType] = {
    "builder": StageType.BUILDER,
    "integration": StageType.INTEGRATION,
    "qa": StageType.QA,
    "hotfix": StageType.HOTFIX,
    "doublecheck": StageType.DOUBLECHECK,
    "troubleshoot": StageType.TROUBLESHOOT,
    "consult": StageType.CONSULT,
    "update": StageType.UPDATE,
    "large_plan": StageType.LARGE_PLAN,
    "large_execute": StageType.LARGE_EXECUTE,
    "reassess": StageType.REASSESS,
    "refactor": StageType.REFACTOR,
}

LARGE_ROUTE_STATUSES = frozenset(
    {
        ExecutionStatus.LARGE_PLAN_COMPLETE,
        ExecutionStatus.LARGE_EXECUTE_COMPLETE,
        ExecutionStatus.LARGE_REASSESS_COMPLETE,
        ExecutionStatus.LARGE_REFACTOR_COMPLETE,
    }
)


def execution_stage_type_for_node(node_id: str) -> StageType | None:
    """Return the public execution stage type for one frozen-plan node id."""

    return EXECUTION_NODE_STAGE_TYPES.get(node_id.strip())


def status_requires_large_route(status: ExecutionStatus | None) -> bool:
    """Return whether the current execution marker requires the LARGE route."""

    return status in LARGE_ROUTE_STATUSES


__all__ = [
    "EXECUTION_NODE_STAGE_TYPES",
    "LARGE_ROUTE_STATUSES",
    "execution_stage_type_for_node",
    "status_requires_large_route",
]
