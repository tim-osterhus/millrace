"""Reduced compiler for mode selection, frozen plans, and compile diagnostics."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from millrace_ai.architecture import (
    CompiledGraphCompletionEntryPlan,
    CompiledGraphEntryPlan,
    CompiledGraphResumePolicyPlan,
    CompiledGraphThresholdPolicyPlan,
    CompiledGraphTransitionPlan,
    CompiledRunPlan,
    CompileInputFingerprint,
    FrozenGraphPlanePlan,
    GraphLoopCounterName,
    GraphLoopDefinition,
    GraphLoopEdgeDefinition,
    GraphLoopNodeDefinition,
    GraphLoopResumePolicyDefinition,
    GraphLoopThresholdPolicyDefinition,
    MaterializedGraphNodePlan,
    RegisteredStageKindDefinition,
)
from millrace_ai.architecture.common import dedupe_preserve_order
from millrace_ai.architecture.materialization import ResolvedAssetRef
from millrace_ai.assets import (
    BUILTIN_GRAPH_LOOP_PATHS,
    BUILTIN_MODE_PATHS,
    BUILTIN_STAGE_KIND_PATHS,
    discover_stage_kind_definitions,
    load_builtin_graph_loop_definition,
    load_builtin_mode_definition,
    load_builtin_stage_kind_definitions,
    load_graph_loop_definition,
    resolve_builtin_mode_id,
)
from millrace_ai.config import RuntimeConfig, fingerprint_runtime_config
from millrace_ai.contracts import (
    CompileDiagnostics,
    ExecutionStageName,
    LearningStageName,
    LearningTriggerRuleDefinition,
    ModeDefinition,
    Plane,
    PlanningStageName,
    StageName,
)
from millrace_ai.errors import AssetValidationError, ConfigurationError
from millrace_ai.paths import WorkspacePaths, workspace_paths

_DEFAULT_MODE_ID = "default_codex"
_DEFAULT_STAGE_TIMEOUT_SECONDS = 3600
_MISSING_ASSET_TOKEN = "missing"
_REQUIRED_SKILLS_BY_STAGE: dict[StageName, tuple[str, ...]] = {
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
_STAGE_NAME_BY_VALUE: dict[str, StageName] = {
    **{stage.value: stage for stage in ExecutionStageName},
    **{stage.value: stage for stage in PlanningStageName},
    **{stage.value: stage for stage in LearningStageName},
}

class CompilerValidationError(ConfigurationError):
    """Raised when a mode bundle fails reduced-compiler validation rules."""


@dataclass(frozen=True, slots=True)
class CompileOutcome:
    """Result of one compile attempt including fallback state."""

    active_plan: CompiledRunPlan | None
    diagnostics: CompileDiagnostics
    used_last_known_good: bool
    compile_input_fingerprint: CompileInputFingerprint | None = None


@dataclass(frozen=True, slots=True)
class CompiledPlanCurrentness:
    """Read-only comparison between persisted compiled plan and current compile inputs."""

    state: str
    expected_fingerprint: CompileInputFingerprint
    persisted_plan_id: str | None
    persisted_fingerprint: CompileInputFingerprint | None


def preview_graph_loop_plan(
    loop_id: str,
    *,
    config: RuntimeConfig,
    assets_root: Path | None = None,
) -> FrozenGraphPlanePlan:
    """Materialize one discovered graph loop into a non-authoritative plane plan."""

    graph_loop = load_graph_loop_definition(loop_id, assets_root=assets_root)
    stage_kinds = {
        stage_kind.stage_kind_id: stage_kind
        for stage_kind in discover_stage_kind_definitions(assets_root=assets_root)
    }
    return _materialize_graph_plane_plan(
        graph_loop=graph_loop,
        mode=_graph_preview_mode_definition(graph_loop),
        config=config,
        stage_kinds=stage_kinds,
    )


def compile_and_persist_workspace_plan(
    target: WorkspacePaths | Path | str,
    *,
    config: RuntimeConfig,
    requested_mode_id: str | None = None,
    assets_root: Path | None = None,
    now: datetime | None = None,
    compile_if_needed: bool = False,
    refuse_stale_last_known_good: bool = False,
) -> CompileOutcome:
    """Compile one mode into a frozen plan and persist canonical artifacts.

    Failure policy:
    - Always writes fresh diagnostics.
    - Keeps the existing compiled plan untouched on compile failure.
    - Returns the last known-good plan when one exists.
    """

    paths = _resolve_paths(target)
    compile_time = _utc_now(now)
    mode_id = _resolve_mode_id(requested_mode_id, config)
    compiled_plan_path = paths.state_dir / "compiled_plan.json"
    diagnostics_path = paths.state_dir / "compile_diagnostics.json"
    compile_assets_root = _resolve_compile_assets_root(paths, assets_root)

    last_known_good = _load_existing_plan(compiled_plan_path)
    compile_input_fingerprint = None
    if last_known_good is not None:
        try:
            compile_input_fingerprint = _build_existing_plan_input_fingerprint(
                config=config,
                mode_id=mode_id,
                plan=last_known_good,
                paths=paths,
                assets_root=compile_assets_root,
            )
        except CompilerValidationError:
            compile_input_fingerprint = None

    if (
        compile_if_needed
        and last_known_good is not None
        and compile_input_fingerprint is not None
        and last_known_good.compile_input_fingerprint == compile_input_fingerprint
    ):
        diagnostics = CompileDiagnostics(
            ok=True,
            mode_id=mode_id,
            warnings=(),
            emitted_at=compile_time,
        )
        return CompileOutcome(
            active_plan=last_known_good,
            diagnostics=diagnostics,
            used_last_known_good=False,
            compile_input_fingerprint=compile_input_fingerprint,
        )

    try:
        plan = _compile_compiled_run_plan(
            paths=paths,
            config=config,
            mode_id=mode_id,
            assets_root=compile_assets_root,
            compile_time=compile_time,
        )
        diagnostics = CompileDiagnostics(
            ok=True,
            mode_id=mode_id,
            warnings=(),
            emitted_at=compile_time,
        )
        _atomic_write_json(compiled_plan_path, plan.model_dump(mode="json"))
        _atomic_write_json(diagnostics_path, diagnostics.model_dump(mode="json"))
        return CompileOutcome(
            active_plan=plan,
            diagnostics=diagnostics,
            used_last_known_good=False,
            compile_input_fingerprint=plan.compile_input_fingerprint,
        )

    except (AssetValidationError, CompilerValidationError, ValidationError, ValueError) as exc:
        diagnostics = CompileDiagnostics(
            ok=False,
            mode_id=mode_id,
            errors=(str(exc),),
            warnings=(),
            emitted_at=compile_time,
        )
        _atomic_write_json(diagnostics_path, diagnostics.model_dump(mode="json"))
        active_plan = last_known_good
        used_last_known_good = last_known_good is not None
        if (
            refuse_stale_last_known_good
            and last_known_good is not None
            and last_known_good.compile_input_fingerprint != compile_input_fingerprint
        ):
            active_plan = None
            used_last_known_good = False
        return CompileOutcome(
            active_plan=active_plan,
            diagnostics=diagnostics,
            used_last_known_good=used_last_known_good,
            compile_input_fingerprint=compile_input_fingerprint,
        )


def inspect_workspace_plan_currentness(
    target: WorkspacePaths | Path | str,
    *,
    config: RuntimeConfig,
    requested_mode_id: str | None = None,
    assets_root: Path | None = None,
) -> CompiledPlanCurrentness:
    """Compare current compile inputs against the persisted compiled plan without recompiling."""

    paths = _resolve_paths(target)
    mode_id = _resolve_mode_id(requested_mode_id, config)
    persisted_plan = _load_existing_plan(paths.state_dir / "compiled_plan.json")
    if persisted_plan is None:
        expected_fingerprint = CompileInputFingerprint(
            mode_id=mode_id,
            config_fingerprint=fingerprint_runtime_config(config),
            assets_fingerprint="assets-missing",
        )
        return CompiledPlanCurrentness(
            state="missing",
            expected_fingerprint=expected_fingerprint,
            persisted_plan_id=None,
            persisted_fingerprint=None,
        )
    expected_fingerprint = _build_existing_plan_input_fingerprint(
        config=config,
        mode_id=mode_id,
        plan=persisted_plan,
        paths=paths,
        assets_root=_resolve_compile_assets_root(paths, assets_root),
    )
    state = (
        "current"
        if persisted_plan.compile_input_fingerprint == expected_fingerprint
        else "stale"
    )
    return CompiledPlanCurrentness(
        state=state,
        expected_fingerprint=expected_fingerprint,
        persisted_plan_id=persisted_plan.compiled_plan_id,
        persisted_fingerprint=persisted_plan.compile_input_fingerprint,
    )

def _compile_compiled_run_plan(
    *,
    paths: WorkspacePaths,
    config: RuntimeConfig,
    mode_id: str,
    assets_root: Path | None,
    compile_time: datetime,
) -> CompiledRunPlan:
    mode = load_builtin_mode_definition(mode_id, assets_root=assets_root)
    graph_loops = {
        plane: load_builtin_graph_loop_definition(loop_id, assets_root=assets_root)
        for plane, loop_id in mode.loop_ids_by_plane.items()
    }
    _validate_mode_stage_maps(
        mode,
        _selected_stages_for_graph_loops(*graph_loops.values()),
    )

    stage_kinds = {
        stage_kind.stage_kind_id: stage_kind
        for stage_kind in load_builtin_stage_kind_definitions(assets_root=assets_root)
    }
    graphs_by_plane = {
        plane: _materialize_graph_plane_plan(
            graph_loop=graph_loop,
            mode=mode,
            config=config,
            stage_kinds=stage_kinds,
        )
        for plane, graph_loop in graph_loops.items()
    }
    selected_stages = _selected_stages_for_graph_loops(*graph_loops.values())
    _validate_learning_trigger_rules(mode, selected_stages)

    execution_graph = graphs_by_plane[Plane.EXECUTION]
    planning_graph = graphs_by_plane[Plane.PLANNING]
    learning_graph = graphs_by_plane.get(Plane.LEARNING)

    resolved_assets = _build_resolved_asset_refs(
        paths=paths,
        mode=mode,
        graph_loops=graph_loops,
        node_plans=tuple(node for graph in graphs_by_plane.values() for node in graph.nodes),
        assets_root=_resolve_compile_assets_root(paths, assets_root),
    )
    compile_input_fingerprint = _build_compile_input_fingerprint(
        config=config,
        mode_id=mode.mode_id,
        resolved_assets=resolved_assets,
        paths=paths,
        assets_root=_resolve_compile_assets_root(paths, assets_root),
    )

    return CompiledRunPlan(
        compiled_plan_id=_build_compiled_plan_id(
            mode_id=mode.mode_id,
            loop_ids_by_plane=mode.loop_ids_by_plane,
            graphs_by_plane=graphs_by_plane,
            concurrency_policy=mode.concurrency_policy,
            learning_trigger_rules=mode.learning_trigger_rules,
        ),
        compile_input_fingerprint=compile_input_fingerprint,
        mode_id=mode.mode_id,
        loop_ids_by_plane=mode.loop_ids_by_plane,
        execution_loop_id=execution_graph.loop_id,
        planning_loop_id=planning_graph.loop_id,
        learning_loop_id=learning_graph.loop_id if learning_graph is not None else None,
        graphs_by_plane=graphs_by_plane,
        execution_graph=execution_graph,
        planning_graph=planning_graph,
        learning_graph=learning_graph,
        concurrency_policy=mode.concurrency_policy,
        learning_trigger_rules=mode.learning_trigger_rules,
        compiled_at=compile_time,
        resolved_assets=resolved_assets,
        source_refs=_build_graph_source_refs(
            mode.mode_id,
            graphs_by_plane,
            has_planning_completion_behavior=planning_graph.completion_behavior is not None,
        ),
    )


def _required_skills_for_stage(stage: StageName) -> tuple[str, ...]:
    return _REQUIRED_SKILLS_BY_STAGE.get(stage, ())


def _materialize_graph_plane_plan(
    *,
    graph_loop: GraphLoopDefinition,
    mode: ModeDefinition,
    config: RuntimeConfig,
    stage_kinds: dict[str, RegisteredStageKindDefinition],
) -> FrozenGraphPlanePlan:
    node_plans = tuple(
        _materialize_graph_node_plan(
            node=node,
            plane=graph_loop.plane,
            mode=mode,
            config=config,
            stage_kinds=stage_kinds,
        )
        for node in graph_loop.nodes
    )
    node_plan_by_id = {node.node_id: node for node in node_plans}
    return FrozenGraphPlanePlan(
        loop_id=graph_loop.loop_id,
        plane=graph_loop.plane,
        nodes=node_plans,
        entry_nodes=graph_loop.entry_nodes,
        transitions=graph_loop.edges,
        compiled_entries=tuple(
            CompiledGraphEntryPlan(
                entry_key=entry.entry_key,
                node_id=entry.node_id,
                stage_kind_id=node_plan_by_id[entry.node_id].stage_kind_id,
                plane=graph_loop.plane,
            )
            for entry in graph_loop.entry_nodes
        ),
        compiled_completion_entry=_compile_graph_completion_entry(
            graph_loop=graph_loop,
            node_plan_by_id=node_plan_by_id,
        ),
        compiled_transitions=_compile_graph_transitions(graph_loop.edges),
        compiled_resume_policies=_compile_graph_resume_policies(
            graph_loop.dynamic_policies.resume_policies
            if graph_loop.dynamic_policies is not None
            else ()
        ),
        compiled_threshold_policies=_compile_graph_threshold_policies(
            graph_loop.dynamic_policies.threshold_policies
            if graph_loop.dynamic_policies is not None
            else (),
            config=config,
        ),
        terminal_states=graph_loop.terminal_states,
        completion_behavior=graph_loop.completion_behavior,
    )


def _materialize_graph_node_plan(
    *,
    node: GraphLoopNodeDefinition,
    plane: Plane,
    mode: ModeDefinition,
    config: RuntimeConfig,
    stage_kinds: dict[str, RegisteredStageKindDefinition],
) -> MaterializedGraphNodePlan:
    stage_kind = stage_kinds[node.stage_kind_id]
    stage_name = _stage_name_for_identifier(node.stage_kind_id)
    stage_config = config.stages.get(node.stage_kind_id)

    entrypoint_path = stage_kind.default_entrypoint_path
    if node.entrypoint_path is not None:
        entrypoint_path = node.entrypoint_path
    if stage_name is not None:
        entrypoint_override = mode.stage_entrypoint_overrides.get(stage_name)
        if entrypoint_override is not None:
            entrypoint_path = _validate_entrypoint_override(node.node_id, entrypoint_override)

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

    timeout_seconds = (
        node.timeout_seconds
        if node.timeout_seconds is not None
        else _DEFAULT_STAGE_TIMEOUT_SECONDS
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
        timeout_seconds=timeout_seconds,
    )


def _selected_stages_for_graph_loops(*graph_loops: GraphLoopDefinition) -> set[StageName]:
    selected_stages: set[StageName] = set()
    for graph_loop in graph_loops:
        for node in graph_loop.nodes:
            stage_name = _stage_name_for_identifier(node.stage_kind_id)
            if stage_name is not None:
                selected_stages.add(stage_name)
    return selected_stages


def _stage_name_for_identifier(identifier: str) -> StageName | None:
    return _STAGE_NAME_BY_VALUE.get(identifier)


def _graph_preview_mode_definition(graph_loop: GraphLoopDefinition) -> ModeDefinition:
    loop_ids_by_plane = {
        Plane.EXECUTION: graph_loop.loop_id
        if graph_loop.plane is Plane.EXECUTION
        else "execution.preview",
        Plane.PLANNING: graph_loop.loop_id
        if graph_loop.plane is Plane.PLANNING
        else "planning.preview",
    }
    if graph_loop.plane is Plane.LEARNING:
        loop_ids_by_plane[Plane.LEARNING] = graph_loop.loop_id
    return ModeDefinition(
        mode_id=f"graph_preview.{graph_loop.loop_id}",
        loop_ids_by_plane=loop_ids_by_plane,
    )


def _compile_graph_transitions(
    edges: tuple[GraphLoopEdgeDefinition, ...],
) -> tuple[CompiledGraphTransitionPlan, ...]:
    compiled: list[CompiledGraphTransitionPlan] = []
    for edge in edges:
        for outcome in edge.on_outcomes:
            compiled.append(
                CompiledGraphTransitionPlan(
                    edge_id=edge.edge_id,
                    source_node_id=edge.from_node_id,
                    outcome=outcome,
                    target_node_id=edge.to_node_id,
                    terminal_state_id=edge.terminal_state_id,
                    kind=edge.kind,
                    priority=edge.priority,
                    max_attempts=edge.max_attempts,
                )
            )
    return tuple(compiled)


def _compile_graph_completion_entry(
    *,
    graph_loop: GraphLoopDefinition,
    node_plan_by_id: dict[str, MaterializedGraphNodePlan],
) -> CompiledGraphCompletionEntryPlan | None:
    completion_behavior = graph_loop.completion_behavior
    if completion_behavior is None:
        return None

    node_plan = node_plan_by_id[completion_behavior.target_node_id]
    return CompiledGraphCompletionEntryPlan(
        node_id=completion_behavior.target_node_id,
        stage_kind_id=node_plan.stage_kind_id,
        plane=graph_loop.plane,
        trigger=completion_behavior.trigger,
        readiness_rule=completion_behavior.readiness_rule,
        request_kind=completion_behavior.request_kind,
        target_selector=completion_behavior.target_selector,
        rubric_policy=completion_behavior.rubric_policy,
        blocked_work_policy=completion_behavior.blocked_work_policy,
        skip_if_already_closed=completion_behavior.skip_if_already_closed,
        on_pass_terminal_state_id=completion_behavior.on_pass_terminal_state_id,
        on_gap_terminal_state_id=completion_behavior.on_gap_terminal_state_id,
        create_incident_on_gap=completion_behavior.create_incident_on_gap,
    )


def _compile_graph_resume_policies(
    policies: tuple[GraphLoopResumePolicyDefinition, ...],
) -> tuple[CompiledGraphResumePolicyPlan, ...]:
    return tuple(
        CompiledGraphResumePolicyPlan(
            policy_id=policy.policy_id,
            source_node_id=policy.source_node_id,
            on_outcome=policy.on_outcome,
            default_target_node_id=policy.default_target_node_id,
            metadata_stage_keys=policy.metadata_stage_keys,
            disallowed_target_node_ids=policy.disallowed_target_node_ids,
        )
        for policy in policies
    )


def _compile_graph_threshold_policies(
    policies: tuple[GraphLoopThresholdPolicyDefinition, ...],
    *,
    config: RuntimeConfig,
) -> tuple[CompiledGraphThresholdPolicyPlan, ...]:
    return tuple(
        CompiledGraphThresholdPolicyPlan(
            policy_id=policy.policy_id,
            source_node_ids=policy.source_node_ids,
            on_outcome=policy.on_outcome,
            counter_name=policy.counter_name,
            threshold=_resolved_threshold_for_policy(policy, config=config),
            exhausted_target_node_id=policy.exhausted_target_node_id,
            exhausted_terminal_state_id=policy.exhausted_terminal_state_id,
        )
        for policy in policies
    )


def _resolved_threshold_for_policy(
    policy: GraphLoopThresholdPolicyDefinition,
    *,
    config: RuntimeConfig,
) -> int:
    if policy.counter_name is GraphLoopCounterName.FIX_CYCLE_COUNT:
        return config.recovery.max_fix_cycles
    if policy.counter_name is GraphLoopCounterName.TROUBLESHOOT_ATTEMPT_COUNT:
        return config.recovery.max_troubleshoot_attempts_before_consult
    if policy.counter_name is GraphLoopCounterName.MECHANIC_ATTEMPT_COUNT:
        return config.recovery.max_mechanic_attempts
    return policy.threshold


def _validate_mode_stage_maps(mode: ModeDefinition, selected_stages: set[StageName]) -> None:
    for map_name, mapping in (
        ("stage_entrypoint_overrides", mode.stage_entrypoint_overrides),
        ("stage_skill_additions", mode.stage_skill_additions),
        ("stage_model_bindings", mode.stage_model_bindings),
        ("stage_runner_bindings", mode.stage_runner_bindings),
    ):
        for stage in sorted(mapping, key=lambda stage_name: stage_name.value):
            if stage not in selected_stages:
                raise CompilerValidationError(
                    f"Mode map `{map_name}` references stage outside selected loops: {stage.value}"
                )


def _validate_learning_trigger_rules(
    mode: ModeDefinition,
    selected_stages: set[StageName],
) -> None:
    for rule in mode.learning_trigger_rules:
        if rule.source_stage not in selected_stages:
            raise CompilerValidationError(
                "Learning trigger rule references source stage outside selected loops: "
                f"{rule.rule_id}:{rule.source_stage.value}"
            )
        if rule.target_stage not in selected_stages:
            raise CompilerValidationError(
                "Learning trigger rule references target learning stage outside selected loops: "
                f"{rule.rule_id}:{rule.target_stage.value}"
            )


def _validate_entrypoint_override(stage_name: str, raw_path: str) -> str:
    normalized = _normalize_relative_asset_path(raw_path)
    if (
        normalized is None
        or not normalized.startswith("entrypoints/")
        or not normalized.endswith(".md")
    ):
        raise CompilerValidationError(
            f"Invalid entrypoint override for stage `{stage_name}`: {raw_path}"
        )
    return normalized


def _normalize_relative_asset_path(raw_path: str) -> str | None:
    text = raw_path.strip()
    if not text:
        return None

    path = Path(text)
    if path.is_absolute():
        return None

    normalized = path.as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized:
        return None
    if normalized == ".." or normalized.startswith("../") or "/../" in normalized:
        return None

    return normalized


def _build_compiled_plan_id(
    *,
    mode_id: str,
    loop_ids_by_plane: dict[Plane, str],
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    concurrency_policy: object,
    learning_trigger_rules: tuple[LearningTriggerRuleDefinition, ...],
) -> str:
    payload = {
        "mode_id": mode_id,
        "loop_ids_by_plane": {
            plane.value: loop_id
            for plane, loop_id in sorted(loop_ids_by_plane.items(), key=lambda item: item[0].value)
        },
        "graphs_by_plane": {
            plane.value: graph.model_dump(mode="json")
            for plane, graph in sorted(graphs_by_plane.items(), key=lambda item: item[0].value)
        },
        "concurrency_policy": (
            concurrency_policy.model_dump(mode="json")
            if hasattr(concurrency_policy, "model_dump")
            else None
        ),
        "learning_trigger_rules": [
            rule.model_dump(mode="json")
            for rule in learning_trigger_rules
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:12]
    return f"plan-{mode_id}-{digest}"


def _build_resolved_asset_refs(
    *,
    paths: WorkspacePaths,
    mode: ModeDefinition,
    graph_loops: dict[Plane, GraphLoopDefinition],
    node_plans: tuple[MaterializedGraphNodePlan, ...],
    assets_root: Path,
) -> tuple[ResolvedAssetRef, ...]:
    refs: list[ResolvedAssetRef] = [
        _resolved_packaged_asset_ref(
            asset_family="mode",
            logical_id=f"mode:{mode.mode_id}",
            relative_path=BUILTIN_MODE_PATHS[mode.mode_id],
            assets_root=assets_root,
        ),
        *[
            _resolved_packaged_asset_ref(
                asset_family="graph_loop",
                logical_id=f"graph_loop:{graph_loop.loop_id}",
                relative_path=BUILTIN_GRAPH_LOOP_PATHS[graph_loop.loop_id],
                assets_root=assets_root,
            )
            for _plane, graph_loop in sorted(graph_loops.items(), key=lambda item: item[0].value)
        ],
    ]

    used_stage_kind_ids = dedupe_preserve_order([node.stage_kind_id for node in node_plans])
    refs.extend(
        _resolved_packaged_asset_ref(
            asset_family="stage_kind",
            logical_id=f"stage_kind:{stage_kind_id}",
            relative_path=BUILTIN_STAGE_KIND_PATHS[stage_kind_id],
            assets_root=assets_root,
        )
        for stage_kind_id in used_stage_kind_ids
    )

    entrypoint_paths = dedupe_preserve_order([node.entrypoint_path for node in node_plans])
    refs.extend(
        _resolved_workspace_asset_ref(
            asset_family="entrypoint",
            logical_id=f"entrypoint:{entrypoint_path}",
            relative_path=entrypoint_path,
            paths=paths,
        )
        for entrypoint_path in entrypoint_paths
    )

    required_skill_paths = dedupe_preserve_order(
        [
            skill_path
            for node in node_plans
            for skill_path in node.required_skill_paths
        ]
    )
    attached_skill_paths = dedupe_preserve_order(
        [
            skill_path
            for node in node_plans
            for skill_path in node.attached_skill_additions
        ]
    )
    refs.extend(
        _resolved_workspace_asset_ref(
            asset_family="skill",
            logical_id=f"skill:{skill_path}",
            relative_path=skill_path,
            paths=paths,
        )
        for skill_path in required_skill_paths
    )
    refs.extend(
        _maybe_resolved_workspace_asset_ref(
            asset_family="skill",
            logical_id=f"skill:{skill_path}",
            relative_path=skill_path,
            paths=paths,
        )
        for skill_path in attached_skill_paths
    )

    return tuple(refs)


def _resolved_packaged_asset_ref(
    *,
    asset_family: str,
    logical_id: str,
    relative_path: Path,
    assets_root: Path,
) -> ResolvedAssetRef:
    compile_path = assets_root / relative_path
    return ResolvedAssetRef(
        asset_family=asset_family,
        logical_id=logical_id,
        compile_time_path=relative_path.as_posix(),
        content_sha256=_sha256_file(compile_path),
    )


def _resolved_workspace_asset_ref(
    *,
    asset_family: str,
    logical_id: str,
    relative_path: str,
    paths: WorkspacePaths,
) -> ResolvedAssetRef:
    compile_path = paths.runtime_root / relative_path
    return ResolvedAssetRef(
        asset_family=asset_family,
        logical_id=logical_id,
        compile_time_path=compile_path.relative_to(paths.root).as_posix(),
        content_sha256=_sha256_file(compile_path),
    )


def _maybe_resolved_workspace_asset_ref(
    *,
    asset_family: str,
    logical_id: str,
    relative_path: str,
    paths: WorkspacePaths,
) -> ResolvedAssetRef:
    compile_path = paths.runtime_root / relative_path
    return ResolvedAssetRef(
        asset_family=asset_family,
        logical_id=logical_id,
        compile_time_path=compile_path.relative_to(paths.root).as_posix(),
        content_sha256=_sha256_file(compile_path) if compile_path.is_file() else _MISSING_ASSET_TOKEN,
    )


def _sha256_file(path: Path) -> str:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise CompilerValidationError(f"Cannot read compile asset: {path}") from exc
    return hashlib.sha256(payload).hexdigest()


def _build_graph_source_refs(
    mode_id: str,
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    *,
    has_planning_completion_behavior: bool,
) -> tuple[str, ...]:
    refs = [
        f"mode:{mode_id}",
        *[
            f"graph_loop:{graph.loop_id}"
            for _plane, graph in sorted(graphs_by_plane.items(), key=lambda item: item[0].value)
        ],
    ]
    if has_planning_completion_behavior:
        refs.append(f"graph_completion_behavior:{graphs_by_plane[Plane.PLANNING].loop_id}")
    return tuple(refs)


def _resolve_mode_id(requested_mode_id: str | None, config: RuntimeConfig) -> str:
    if requested_mode_id and requested_mode_id.strip():
        return resolve_builtin_mode_id(requested_mode_id.strip())

    default_mode = config.runtime.default_mode.strip()
    if default_mode:
        return resolve_builtin_mode_id(default_mode)

    return resolve_builtin_mode_id(_DEFAULT_MODE_ID)


def _resolve_paths(target: WorkspacePaths | Path | str) -> WorkspacePaths:
    return target if isinstance(target, WorkspacePaths) else workspace_paths(target)


def _resolve_compile_assets_root(paths: WorkspacePaths, assets_root: Path | None) -> Path:
    if assets_root is not None:
        return assets_root
    from millrace_ai.modes import ASSETS_ROOT

    return ASSETS_ROOT


def _build_compile_input_fingerprint(
    *,
    config: RuntimeConfig,
    mode_id: str,
    resolved_assets: tuple[ResolvedAssetRef, ...],
    paths: WorkspacePaths,
    assets_root: Path,
) -> CompileInputFingerprint:
    return CompileInputFingerprint(
        mode_id=mode_id,
        config_fingerprint=fingerprint_runtime_config(config),
        assets_fingerprint=_fingerprint_resolved_assets(
            resolved_assets=resolved_assets,
            paths=paths,
            assets_root=assets_root,
        ),
    )


def _build_existing_plan_input_fingerprint(
    *,
    config: RuntimeConfig,
    mode_id: str,
    plan: CompiledRunPlan,
    paths: WorkspacePaths,
    assets_root: Path,
) -> CompileInputFingerprint:
    return _build_compile_input_fingerprint(
        config=config,
        mode_id=mode_id,
        resolved_assets=plan.resolved_assets,
        paths=paths,
        assets_root=assets_root,
    )


def _fingerprint_resolved_assets(
    *,
    resolved_assets: tuple[ResolvedAssetRef, ...],
    paths: WorkspacePaths,
    assets_root: Path,
) -> str:
    digest = hashlib.sha256()
    for asset_ref in sorted(
        resolved_assets,
        key=lambda ref: (ref.asset_family, ref.logical_id, ref.compile_time_path),
    ):
        file_path = _path_for_resolved_asset_ref(asset_ref, paths=paths, assets_root=assets_root)
        digest.update(asset_ref.asset_family.encode("utf-8"))
        digest.update(b"\0")
        digest.update(asset_ref.logical_id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(asset_ref.compile_time_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_current_asset_content_token(file_path).encode("utf-8"))
        digest.update(b"\0")
    return f"assets-{digest.hexdigest()[:12]}"


def _path_for_resolved_asset_ref(
    asset_ref: ResolvedAssetRef,
    *,
    paths: WorkspacePaths,
    assets_root: Path,
) -> Path:
    compile_path = Path(asset_ref.compile_time_path)
    if compile_path.parts[:1] == ("millrace-agents",):
        return paths.root / compile_path
    return assets_root / compile_path


def _current_asset_content_token(path: Path) -> str:
    if not path.is_file():
        return _MISSING_ASSET_TOKEN
    return _sha256_file(path)


def _utc_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _load_existing_plan(path: Path) -> CompiledRunPlan | None:
    if not path.is_file():
        return None

    try:
        payload = path.read_text(encoding="utf-8")
    except OSError:
        return None

    try:
        return CompiledRunPlan.model_validate_json(payload)
    except ValidationError:
        return None


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    serialized = json.dumps(payload, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


__all__ = [
    "CompiledPlanCurrentness",
    "CompileOutcome",
    "CompilerValidationError",
    "compile_and_persist_workspace_plan",
    "inspect_workspace_plan_currentness",
    "preview_graph_loop_plan",
]
