"""Compiler materialization for individual graph nodes."""

from __future__ import annotations

from millrace_ai.architecture import (
    GraphLoopNodeDefinition,
    MaterializedGraphNodePlan,
    RegisteredStageKindDefinition,
    RequestContextProfileDefinition,
)
from millrace_ai.architecture.common import dedupe_preserve_order
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import (
    ModeDefinition,
    Plane,
    StageName,
)
from millrace_ai.contracts.stage_metadata import stage_name_for_value

from .capabilities import compile_execution_capability_grants
from .entrypoint_overrides import validate_entrypoint_override
from .model_aliases import resolve_model_alias_assignment

DEFAULT_STAGE_TIMEOUT_SECONDS = 3600

def required_skills_for_stage(stage: StageName) -> tuple[str, ...]:
    from millrace_ai.assets import load_stage_kind_definition

    return load_stage_kind_definition(stage.value).required_skill_paths


def materialize_graph_node_plan(
    *,
    node: GraphLoopNodeDefinition,
    plane: Plane,
    mode: ModeDefinition,
    config: RuntimeConfig,
    stage_kinds: dict[str, RegisteredStageKindDefinition],
    request_context_profiles_by_id: dict[str, RequestContextProfileDefinition],
    loop_id: str,
) -> MaterializedGraphNodePlan:
    stage_kind = stage_kinds[node.stage_kind_id]
    runtime_stage = stage_kind.runtime_stage
    stage_name = stage_name_for_identifier(node.stage_kind_id) or runtime_stage
    if (
        node.stage_kind_id in mode.stage_entrypoint_overrides
        or node.stage_kind_id in mode.stage_skill_additions
        or node.stage_kind_id in mode.stage_model_bindings
        or node.stage_kind_id in mode.stage_runner_bindings
        or node.stage_kind_id in mode.stage_thinking_bindings
        or node.stage_kind_id in mode.model_assignment.by_stage
        or node.stage_kind_id in config.model_assignment.by_stage
    ):
        stage_key = node.stage_kind_id
    else:
        stage_key = stage_name or node.stage_kind_id
    stage_config = config.stages.get(node.stage_kind_id) or config.stages.get(runtime_stage.value)

    entrypoint_path = stage_kind.default_entrypoint_path
    if node.entrypoint_path is not None:
        entrypoint_path = node.entrypoint_path
    entrypoint_override = mode.stage_entrypoint_overrides.get(stage_key)
    if entrypoint_override is not None:
        entrypoint_path = validate_entrypoint_override(node.node_id, entrypoint_override)

    attached_skill_additions = tuple(node.attached_skill_additions)
    attached_skill_additions = dedupe_preserve_order(
        [*attached_skill_additions, *mode.stage_skill_additions.get(stage_key, ())]
    )

    runner_name = node.runner_name
    if stage_config is not None and stage_config.runner is not None:
        runner_name = stage_config.runner
    mode_runner = mode.stage_runner_bindings.get(stage_key)
    if mode_runner is not None:
        runner_name = mode_runner

    model_name = node.model_name
    if stage_config is not None and stage_config.model is not None:
        model_name = stage_config.model
    mode_model = mode.stage_model_bindings.get(stage_key)
    if mode_model is not None:
        model_name = mode_model

    thinking_level = node.thinking_level
    if stage_config is not None and stage_config.thinking_level is not None:
        thinking_level = stage_config.thinking_level
    if stage_key in mode.stage_thinking_bindings:
        thinking_level = mode.stage_thinking_bindings[stage_key]

    model_reasoning_effort = (
        thinking_level
        if runner_name == "codex_cli" and thinking_level is not None
        else None
    )
    alias_decision = resolve_model_alias_assignment(
        config=config,
        mode=mode,
        stage_key=stage_key,
        loop_id=loop_id,
        existing_model_name=model_name,
        existing_thinking_level=thinking_level,
    )
    model_name = alias_decision.model_name
    thinking_level = alias_decision.thinking_level
    model_reasoning_effort = (
        thinking_level
        if runner_name == "codex_cli" and thinking_level is not None
        else None
    )
    capability_grants, capability_warnings = compile_execution_capability_grants(
        node=node,
        stage_kind=stage_kind,
        mode=mode,
        config=config,
    )

    timeout_seconds = (
        node.timeout_seconds
        if node.timeout_seconds is not None
        else DEFAULT_STAGE_TIMEOUT_SECONDS
    )
    if stage_config is not None and stage_config.timeout_seconds is not None:
        timeout_seconds = stage_config.timeout_seconds
    request_context_profile_id = (
        node.request_context_profile_id
        if node.request_context_profile_id is not None
        else stage_kind.request_context_profile_id
    )
    context_render_plan_id = (
        node.context_render_plan_id
        if node.context_render_plan_id is not None
        else stage_kind.context_render_plan_id
    )

    return MaterializedGraphNodePlan(
        node_id=node.node_id,
        stage_kind_id=node.stage_kind_id,
        plane=plane,
        runtime_stage=runtime_stage,
        entrypoint_path=entrypoint_path,
        entrypoint_contract_id=f"{node.node_id}.contract.v1",
        running_status_marker=stage_kind.running_status_marker,
        allowed_result_classes_by_outcome=stage_kind.allowed_result_classes_by_outcome,
        declared_output_artifacts=stage_kind.declared_output_artifacts,
        allowed_work_item_families=stage_kind.allowed_work_item_families,
        required_skill_paths=stage_kind.required_skill_paths,
        attached_skill_additions=attached_skill_additions,
        runner_name=runner_name,
        model_name=model_name,
        thinking_level=thinking_level,
        model_reasoning_effort=model_reasoning_effort,
        model_assignment_alias_id=alias_decision.alias_id,
        model_assignment_source=alias_decision.source,
        model_assignment_warnings=alias_decision.warnings,
        timeout_seconds=timeout_seconds,
        execution_capability_grants=capability_grants,
        execution_capability_warnings=capability_warnings,
        request_context_profile_id=request_context_profile_id,
        context_render_plan_id=context_render_plan_id,
    )


def stage_name_for_identifier(identifier: str) -> StageName | None:
    try:
        return stage_name_for_value(identifier)
    except ValueError:
        return None


__all__ = [
    "DEFAULT_STAGE_TIMEOUT_SECONDS",
    "materialize_graph_node_plan",
    "required_skills_for_stage",
    "stage_name_for_identifier",
]
