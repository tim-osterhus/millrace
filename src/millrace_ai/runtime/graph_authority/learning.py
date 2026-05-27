"""Learning-plane compiled graph routing."""

from __future__ import annotations

from millrace_ai.architecture import FrozenGraphPlanePlan
from millrace_ai.contracts import LearningStageName, LearningTerminalResult, Plane, RuntimeSnapshot, StageResultEnvelope
from millrace_ai.router import RouterAction, RouterDecision

from .counters import normalize_failure_class
from .policies import transition_for_source
from .stage_mapping import node_plan_by_id, stage_for_node
from .validation import validate_stage_result_matches_snapshot


def route_learning_stage_result_from_graph(
    graph: FrozenGraphPlanePlan,
    snapshot: RuntimeSnapshot,
    stage_result: StageResultEnvelope,
) -> RouterDecision:
    validate_stage_result_matches_snapshot(snapshot, stage_result, expected_plane=Plane.LEARNING)
    source_stage = LearningStageName(stage_result.stage)
    outcome = LearningTerminalResult(stage_result.terminal_result)
    transition = transition_for_source(graph, source_node_id=stage_result.node_id, outcome=outcome.value)

    if transition.target_node_id is not None:
        return RouterDecision(
            action=RouterAction.RUN_STAGE,
            next_plane=Plane.LEARNING,
            next_stage=stage_for_node(graph, transition.target_node_id),
            next_node_id=transition.target_node_id,
            next_stage_kind_id=node_plan_by_id(graph, transition.target_node_id).stage_kind_id,
            reason=f"{source_stage.value}:{outcome.value}",
        )

    if outcome is LearningTerminalResult.BLOCKED:
        metadata_failure_class = stage_result.metadata.get("failure_class")
        failure_class = (
            normalize_failure_class(metadata_failure_class)
            if isinstance(metadata_failure_class, str) and metadata_failure_class.strip()
            else normalize_failure_class(f"{source_stage.value}_blocked")
        )
        return RouterDecision(
            action=RouterAction.BLOCKED,
            next_plane=None,
            next_stage=None,
            reason=f"{source_stage.value}_blocked",
            failure_class=failure_class,
        )
    return RouterDecision(
        action=RouterAction.IDLE,
        next_plane=None,
        next_stage=None,
        reason=f"{source_stage.value}:{outcome.value}",
    )


__all__ = ["route_learning_stage_result_from_graph"]
