"""Compiler materialization for individual graph nodes."""

from __future__ import annotations

from millrace_ai.architecture import GraphLoopNodeDefinition, MaterializedGraphNodePlan, RegisteredStageKindDefinition
from millrace_ai.architecture.common import dedupe_preserve_order
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import (
    ExecutionStageName,
    LearningStageName,
    ModeDefinition,
    Plane,
    PlanningStageName,
    StageName,
)
from millrace_ai.contracts.stage_metadata import STAGE_NAME_BY_VALUE, stage_name_for_value

from .entrypoint_overrides import validate_entrypoint_override

DEFAULT_STAGE_TIMEOUT_SECONDS = 3600

REQUIRED_SKILLS_BY_STAGE: dict[StageName, tuple[str, ...]] = {
    ExecutionStageName.BUILDER: ("skills/stage/execution/builder-core/SKILL.md",),
    ExecutionStageName.CHECKER: ("skills/stage/execution/checker-core/SKILL.md",),
    ExecutionStageName.FIXER: ("skills/stage/execution/fixer-core/SKILL.md",),
    ExecutionStageName.DOUBLECHECKER: ("skills/stage/execution/doublechecker-core/SKILL.md",),
    ExecutionStageName.UPDATER: ("skills/stage/execution/updater-core/SKILL.md",),
    ExecutionStageName.TROUBLESHOOTER: ("skills/stage/execution/troubleshooter-core/SKILL.md",),
    ExecutionStageName.CONSULTANT: ("skills/stage/execution/consultant-core/SKILL.md",),
    PlanningStageName.PLANNER: ("skills/stage/planning/planner-core/SKILL.md",),
    PlanningStageName.MANAGER: ("skills/stage/planning/manager-core/SKILL.md",),
    PlanningStageName.MECHANIC: ("skills/stage/planning/mechanic-core/SKILL.md",),
    PlanningStageName.AUDITOR: ("skills/stage/planning/auditor-core/SKILL.md",),
    PlanningStageName.ARBITER: ("skills/stage/planning/arbiter-core/SKILL.md",),
    LearningStageName.ANALYST: ("skills/stage/learning/analyst-core/SKILL.md",),
    LearningStageName.PROFESSOR: ("skills/stage/learning/professor-core/SKILL.md",),
    LearningStageName.CURATOR: ("skills/stage/learning/curator-core/SKILL.md",),
}

def required_skills_for_stage(stage: StageName) -> tuple[str, ...]:
    return REQUIRED_SKILLS_BY_STAGE.get(stage, ())


def materialize_graph_node_plan(
    *,
    node: GraphLoopNodeDefinition,
    plane: Plane,
    mode: ModeDefinition,
    config: RuntimeConfig,
    stage_kinds: dict[str, RegisteredStageKindDefinition],
) -> MaterializedGraphNodePlan:
    stage_kind = stage_kinds[node.stage_kind_id]
    stage_name = stage_name_for_identifier(node.stage_kind_id)
    stage_config = config.stages.get(node.stage_kind_id)

    entrypoint_path = stage_kind.default_entrypoint_path
    if node.entrypoint_path is not None:
        entrypoint_path = node.entrypoint_path
    if stage_name is not None:
        entrypoint_override = mode.stage_entrypoint_overrides.get(stage_name)
        if entrypoint_override is not None:
            entrypoint_path = validate_entrypoint_override(node.node_id, entrypoint_override)

    attached_skill_additions = tuple(node.attached_skill_additions)
    if stage_name is not None:
        attached_skill_additions = dedupe_preserve_order(
            [*attached_skill_additions, *mode.stage_skill_additions.get(stage_name, ())]
        )

    runner_name = node.runner_name
    if stage_config is not None and stage_config.runner is not None:
        runner_name = stage_config.runner
    if stage_name is not None:
        mode_runner = mode.stage_runner_bindings.get(stage_name)
        if mode_runner is not None:
            runner_name = mode_runner

    model_name = node.model_name
    if stage_config is not None and stage_config.model is not None:
        model_name = stage_config.model
    if stage_name is not None:
        mode_model = mode.stage_model_bindings.get(stage_name)
        if mode_model is not None:
            model_name = mode_model

    model_reasoning_effort = config.runners.codex.model_reasoning_effort
    if stage_config is not None and stage_config.model_reasoning_effort is not None:
        model_reasoning_effort = stage_config.model_reasoning_effort

    timeout_seconds = (
        node.timeout_seconds
        if node.timeout_seconds is not None
        else DEFAULT_STAGE_TIMEOUT_SECONDS
    )
    if stage_config is not None and stage_config.timeout_seconds is not None:
        timeout_seconds = stage_config.timeout_seconds

    return MaterializedGraphNodePlan(
        node_id=node.node_id,
        stage_kind_id=node.stage_kind_id,
        plane=plane,
        entrypoint_path=entrypoint_path,
        entrypoint_contract_id=f"{node.node_id}.contract.v1",
        running_status_marker=stage_kind.running_status_marker,
        allowed_result_classes_by_outcome=stage_kind.allowed_result_classes_by_outcome,
        declared_output_artifacts=stage_kind.declared_output_artifacts,
        required_skill_paths=stage_kind.required_skill_paths,
        attached_skill_additions=attached_skill_additions,
        runner_name=runner_name,
        model_name=model_name,
        model_reasoning_effort=(
            model_reasoning_effort.value if model_reasoning_effort is not None else None
        ),
        timeout_seconds=timeout_seconds,
    )


def stage_name_for_identifier(identifier: str) -> StageName | None:
    try:
        return stage_name_for_value(identifier)
    except ValueError:
        return None


__all__ = [
    "DEFAULT_STAGE_TIMEOUT_SECONDS",
    "REQUIRED_SKILLS_BY_STAGE",
    "STAGE_NAME_BY_VALUE",
    "materialize_graph_node_plan",
    "required_skills_for_stage",
    "stage_name_for_identifier",
]
