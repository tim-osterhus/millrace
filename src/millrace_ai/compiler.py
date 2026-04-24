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
    FrozenGraphPlanePlan,
    FrozenGraphRunPlan,
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
from millrace_ai.assets import (
    discover_stage_kind_definitions,
    load_builtin_graph_loop_definition,
    load_builtin_mode_bundle,
    load_builtin_stage_kind_definitions,
    load_graph_loop_definition,
    resolve_builtin_mode_id,
)
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import (
    CompileDiagnostics,
    ExecutionStageName,
    FrozenRunPlan,
    FrozenStagePlan,
    LoopConfigDefinition,
    ModeDefinition,
    Plane,
    PlanningStageName,
    StageName,
)
from millrace_ai.errors import AssetValidationError, ConfigurationError
from millrace_ai.paths import WorkspacePaths, workspace_paths

_DEFAULT_MODE_ID = "default_codex"
_DEFAULT_STAGE_TIMEOUT_SECONDS = 3600
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
}
_STAGE_NAME_BY_VALUE: dict[str, StageName] = {
    **{stage.value: stage for stage in ExecutionStageName},
    **{stage.value: stage for stage in PlanningStageName},
}

class CompilerValidationError(ConfigurationError):
    """Raised when a mode bundle fails reduced-compiler validation rules."""


@dataclass(frozen=True, slots=True)
class CompileOutcome:
    """Result of one compile attempt including fallback state."""

    active_plan: FrozenRunPlan | None
    diagnostics: CompileDiagnostics
    used_last_known_good: bool
    active_graph_plan: FrozenGraphRunPlan | None = None


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
    compiled_graph_plan_path = paths.state_dir / "compiled_graph_plan.json"
    diagnostics_path = paths.state_dir / "compile_diagnostics.json"

    last_known_good = _load_existing_plan(compiled_plan_path)
    last_known_good_graph = _load_existing_graph_plan(compiled_graph_plan_path)

    try:
        plan = _compile_frozen_run_plan(
            config=config,
            mode_id=mode_id,
            assets_root=assets_root,
            compile_time=compile_time,
        )
        graph_plan = _compile_frozen_graph_run_plan(
            config=config,
            mode_id=mode_id,
            assets_root=assets_root,
            compile_time=compile_time,
            compiled_plan_id=plan.compiled_plan_id,
        )
        diagnostics = CompileDiagnostics(
            ok=True,
            mode_id=mode_id,
            warnings=(),
            emitted_at=compile_time,
        )
        _atomic_write_json(compiled_plan_path, plan.model_dump(mode="json"))
        _atomic_write_json(compiled_graph_plan_path, graph_plan.model_dump(mode="json"))
        _atomic_write_json(diagnostics_path, diagnostics.model_dump(mode="json"))
        return CompileOutcome(
            active_plan=plan,
            diagnostics=diagnostics,
            used_last_known_good=False,
            active_graph_plan=graph_plan,
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
        return CompileOutcome(
            active_plan=last_known_good,
            diagnostics=diagnostics,
            used_last_known_good=last_known_good is not None,
            active_graph_plan=last_known_good_graph,
        )


def _load_existing_graph_plan(path: Path) -> FrozenGraphRunPlan | None:
    if not path.exists():
        return None
    return FrozenGraphRunPlan.model_validate_json(path.read_text(encoding="utf-8"))


def _compile_frozen_run_plan(
    *,
    config: RuntimeConfig,
    mode_id: str,
    assets_root: Path | None,
    compile_time: datetime,
) -> FrozenRunPlan:
    # Phase 1 + 2: resolve mode and loop definitions.
    bundle = load_builtin_mode_bundle(mode_id, assets_root=assets_root)

    # Phase 3 + 4 + 5 + 6: validate map scope, resolve bundles, enforce boundaries.
    stage_plans = _freeze_stage_plans(
        config=config,
        mode=bundle.mode,
        execution_loop=bundle.execution_loop,
        planning_loop=bundle.planning_loop,
    )

    # Phase 7: freeze run plan.
    return FrozenRunPlan(
        compiled_plan_id=_build_compiled_plan_id(
            mode=bundle.mode,
            execution_loop_id=bundle.execution_loop.loop_id,
            planning_loop_id=bundle.planning_loop.loop_id,
            stage_plans=stage_plans,
            completion_behavior=bundle.planning_loop.completion_behavior,
        ),
        mode_id=bundle.mode.mode_id,
        execution_loop_id=bundle.execution_loop.loop_id,
        planning_loop_id=bundle.planning_loop.loop_id,
        stage_plans=stage_plans,
        completion_behavior=bundle.planning_loop.completion_behavior,
        compiled_at=compile_time,
        source_refs=_build_source_refs(
            bundle.mode,
            bundle.execution_loop.loop_id,
            bundle.planning_loop.loop_id,
            bundle.planning_loop.completion_behavior,
        ),
    )


def _freeze_stage_plans(
    *,
    config: RuntimeConfig,
    mode: ModeDefinition,
    execution_loop: LoopConfigDefinition,
    planning_loop: LoopConfigDefinition,
) -> tuple[FrozenStagePlan, ...]:
    selected_stages = {stage for stage in execution_loop.stages} | {
        stage for stage in planning_loop.stages
    }
    _validate_mode_stage_maps(mode, selected_stages)

    stage_plans: list[FrozenStagePlan] = []
    for loop in (execution_loop, planning_loop):
        for stage in loop.stages:
            stage_name = stage.value
            entrypoint_override = mode.stage_entrypoint_overrides.get(stage)
            if entrypoint_override is not None:
                entrypoint_path = _validate_entrypoint_override(stage_name, entrypoint_override)
            else:
                entrypoint_path = f"entrypoints/{loop.plane.value}/{stage_name}.md"

            stage_config = config.stages.get(stage_name)
            runner_name = mode.stage_runner_bindings.get(stage)
            if runner_name is None and stage_config is not None:
                runner_name = stage_config.runner

            model_name = mode.stage_model_bindings.get(stage)
            if model_name is None and stage_config is not None:
                model_name = stage_config.model

            timeout_seconds = (
                stage_config.timeout_seconds
                if stage_config is not None
                else _DEFAULT_STAGE_TIMEOUT_SECONDS
            )

            stage_plans.append(
                FrozenStagePlan(
                    stage=stage,
                    plane=loop.plane,
                    entrypoint_path=entrypoint_path,
                    entrypoint_contract_id=f"{stage_name}.contract.v1",
                    required_skills=_required_skills_for_stage(stage),
                    attached_skill_additions=tuple(mode.stage_skill_additions.get(stage, ())),
                    runner_name=runner_name,
                    model_name=model_name,
                    timeout_seconds=timeout_seconds,
                )
            )

    return tuple(stage_plans)


def _required_skills_for_stage(stage: StageName) -> tuple[str, ...]:
    return _REQUIRED_SKILLS_BY_STAGE.get(stage, ())


def _compile_frozen_graph_run_plan(
    *,
    config: RuntimeConfig,
    mode_id: str,
    assets_root: Path | None,
    compile_time: datetime,
    compiled_plan_id: str,
) -> FrozenGraphRunPlan:
    bundle = load_builtin_mode_bundle(mode_id, assets_root=assets_root)
    execution_graph_loop = load_builtin_graph_loop_definition(
        bundle.execution_loop.loop_id,
        assets_root=assets_root,
    )
    planning_graph_loop = load_builtin_graph_loop_definition(
        bundle.planning_loop.loop_id,
        assets_root=assets_root,
    )
    stage_kinds = {
        stage_kind.stage_kind_id: stage_kind
        for stage_kind in load_builtin_stage_kind_definitions(assets_root=assets_root)
    }
    execution_graph = _materialize_graph_plane_plan(
        graph_loop=execution_graph_loop,
        mode=bundle.mode,
        config=config,
        stage_kinds=stage_kinds,
    )
    planning_graph = _materialize_graph_plane_plan(
        graph_loop=planning_graph_loop,
        mode=bundle.mode,
        config=config,
        stage_kinds=stage_kinds,
    )
    legacy_equivalence_issues = _legacy_equivalence_issues_for_shipped_defaults(
        execution_graph=execution_graph,
        planning_graph=planning_graph,
        config=config,
    )

    return FrozenGraphRunPlan(
        compiled_plan_id=compiled_plan_id,
        mode_id=bundle.mode.mode_id,
        authoritative_for_runtime_execution=True,
        legacy_equivalence_ready_for_cutover=not legacy_equivalence_issues,
        legacy_equivalence_issues=legacy_equivalence_issues,
        execution_graph=execution_graph,
        planning_graph=planning_graph,
        compiled_at=compile_time,
        source_refs=_build_graph_source_refs(
            bundle.mode.mode_id,
            execution_graph_loop.loop_id,
            planning_graph_loop.loop_id,
            has_planning_completion_behavior=planning_graph_loop.completion_behavior is not None,
        ),
    )


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
        required_skill_paths=stage_kind.required_skill_paths,
        attached_skill_additions=attached_skill_additions,
        runner_name=runner_name,
        model_name=model_name,
        timeout_seconds=timeout_seconds,
    )


def _stage_name_for_identifier(identifier: str) -> StageName | None:
    return _STAGE_NAME_BY_VALUE.get(identifier)


def _graph_preview_mode_definition(graph_loop: GraphLoopDefinition) -> ModeDefinition:
    execution_loop_id = graph_loop.loop_id if graph_loop.plane is Plane.EXECUTION else "preview.execution"
    planning_loop_id = graph_loop.loop_id if graph_loop.plane is Plane.PLANNING else "preview.planning"
    return ModeDefinition(
        mode_id=f"graph_preview.{graph_loop.loop_id}",
        execution_loop_id=execution_loop_id,
        planning_loop_id=planning_loop_id,
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


def _legacy_equivalence_issues_for_shipped_defaults(
    *,
    execution_graph: FrozenGraphPlanePlan,
    planning_graph: FrozenGraphPlanePlan,
    config: RuntimeConfig,
) -> tuple[str, ...]:
    issues: list[str] = []

    execution_entries = {entry.entry_key.value: entry.node_id for entry in execution_graph.compiled_entries}
    planning_entries = {entry.entry_key.value: entry.node_id for entry in planning_graph.compiled_entries}
    if execution_entries != {"task": "builder"}:
        issues.append("execution.standard: task intake entry no longer matches legacy builder activation")
    if planning_entries != {"spec": "planner", "incident": "auditor"}:
        issues.append("planning.standard: spec/incident intake entries no longer match legacy activation")

    execution_transitions = {
        (transition.source_node_id, transition.outcome): transition
        for transition in execution_graph.compiled_transitions
    }
    planning_transitions = {
        (transition.source_node_id, transition.outcome): transition
        for transition in planning_graph.compiled_transitions
    }
    execution_resume_policies = {
        policy.policy_id: policy for policy in execution_graph.compiled_resume_policies
    }
    execution_threshold_policies = {
        policy.policy_id: policy for policy in execution_graph.compiled_threshold_policies
    }
    planning_resume_policies = {
        policy.policy_id: policy for policy in planning_graph.compiled_resume_policies
    }
    planning_threshold_policies = {
        policy.policy_id: policy for policy in planning_graph.compiled_threshold_policies
    }

    if execution_transitions.get(("troubleshooter", "TROUBLESHOOT_COMPLETE"), None) is None:
        issues.append("execution.standard: troubleshooter completion transition is missing")
    else:
        troubleshooter_complete = execution_transitions[("troubleshooter", "TROUBLESHOOT_COMPLETE")]
        if troubleshooter_complete.target_node_id != "builder":
            issues.append(
                "execution.standard: troubleshooter completion default target does not match legacy builder resume"
            )
    expected_execution_resume = execution_resume_policies.get("execution.troubleshooter.resume")
    if (
        expected_execution_resume is None
        or expected_execution_resume.metadata_stage_keys != ("resume_stage",)
    ):
        issues.append("execution.standard: troubleshooter metadata resume routing is not yet encoded")
    consultant_resume = execution_resume_policies.get("execution.consultant.resume")
    if (
        consultant_resume is None
        or consultant_resume.metadata_stage_keys != ("target_stage", "resume_stage")
        or consultant_resume.default_target_node_id != "troubleshooter"
    ):
        issues.append("execution.standard: consultant metadata-target routing is not yet encoded")

    if not {
        ("checker", "FIX_NEEDED"),
        ("doublechecker", "FIX_NEEDED"),
    }.issubset(execution_transitions):
        issues.append("execution.standard: fix-needed routing coverage is incomplete")
    else:
        fix_needed_targets = {
            execution_transitions[("checker", "FIX_NEEDED")].target_node_id,
            execution_transitions[("doublechecker", "FIX_NEEDED")].target_node_id,
        }
        if fix_needed_targets != {"fixer"}:
            issues.append("execution.standard: fix-needed direct transition no longer targets fixer")
    fix_needed_exhaustion = execution_threshold_policies.get("execution.fix-needed.exhaustion")
    if (
        fix_needed_exhaustion is None
        or set(fix_needed_exhaustion.source_node_ids) != {"checker", "doublechecker"}
        or fix_needed_exhaustion.counter_name is not GraphLoopCounterName.FIX_CYCLE_COUNT
        or fix_needed_exhaustion.threshold != config.recovery.max_fix_cycles
        or fix_needed_exhaustion.exhausted_target_node_id != "troubleshooter"
    ):
        issues.append("execution.standard: fix-needed exhaustion routing is not yet encoded")

    execution_blocked_recovery = execution_threshold_policies.get("execution.blocked.recovery")
    if (
        execution_blocked_recovery is None
        or set(execution_blocked_recovery.source_node_ids)
        != {"builder", "checker", "fixer", "doublechecker", "updater", "troubleshooter"}
        or execution_blocked_recovery.counter_name
        is not GraphLoopCounterName.TROUBLESHOOT_ATTEMPT_COUNT
        or execution_blocked_recovery.threshold
        != config.recovery.max_troubleshoot_attempts_before_consult
        or execution_blocked_recovery.exhausted_target_node_id != "consultant"
    ):
        issues.append("execution.standard: blocked recovery attempt thresholds are not yet encoded")

    planner_complete = planning_transitions.get(("planner", "PLANNER_COMPLETE"))
    if planner_complete is None or planner_complete.target_node_id != "manager":
        issues.append("planning.standard: planner completion no longer targets manager")
    auditor_complete = planning_transitions.get(("auditor", "AUDITOR_COMPLETE"))
    if auditor_complete is None or auditor_complete.target_node_id != "planner":
        issues.append("planning.standard: auditor completion no longer targets planner")
    mechanic_resume = planning_resume_policies.get("planning.mechanic.resume")
    if (
        mechanic_resume is None
        or mechanic_resume.metadata_stage_keys != ("resume_stage",)
        or mechanic_resume.default_target_node_id != "planner"
    ):
        issues.append("planning.standard: mechanic metadata resume routing is not yet encoded")
    planning_blocked_recovery = planning_threshold_policies.get("planning.blocked.recovery")
    if (
        planning_blocked_recovery is None
        or set(planning_blocked_recovery.source_node_ids)
        != {"planner", "manager", "auditor", "mechanic"}
        or planning_blocked_recovery.counter_name
        is not GraphLoopCounterName.MECHANIC_ATTEMPT_COUNT
        or planning_blocked_recovery.threshold != config.recovery.max_mechanic_attempts
        or planning_blocked_recovery.exhausted_terminal_state_id != "blocked"
    ):
        issues.append("planning.standard: blocked recovery attempt thresholds are not yet encoded")

    if planning_graph.completion_behavior is None or planning_graph.completion_behavior.target_node_id != "arbiter":
        issues.append("planning.standard: completion behavior no longer targets arbiter")
    completion_entry = planning_graph.compiled_completion_entry
    if completion_entry is None:
        issues.append("planning.standard: closure-target activation entry is missing")
    else:
        if completion_entry.node_id != "arbiter" or completion_entry.stage_kind_id != "arbiter":
            issues.append("planning.standard: closure-target activation entry no longer targets arbiter")
        if completion_entry.request_kind != "closure_target":
            issues.append("planning.standard: closure-target activation request kind drifted from legacy behavior")
        if completion_entry.target_selector != "active_closure_target":
            issues.append(
                "planning.standard: closure-target selector no longer matches legacy activation behavior"
            )

    return tuple(dict.fromkeys(issues))


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
    mode: ModeDefinition,
    execution_loop_id: str,
    planning_loop_id: str,
    stage_plans: tuple[FrozenStagePlan, ...],
    completion_behavior: object | None,
) -> str:
    serialized_completion_behavior = (
        completion_behavior.model_dump(mode="json")
        if hasattr(completion_behavior, "model_dump")
        else completion_behavior
    )
    payload = {
        "mode_id": mode.mode_id,
        "execution_loop_id": execution_loop_id,
        "planning_loop_id": planning_loop_id,
        "stage_plans": [stage_plan.model_dump(mode="json") for stage_plan in stage_plans],
        "completion_behavior": serialized_completion_behavior,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:12]
    return f"plan-{mode.mode_id}-{digest}"


def _build_source_refs(
    mode: ModeDefinition,
    execution_loop_id: str,
    planning_loop_id: str,
    completion_behavior: object | None,
) -> tuple[str, ...]:
    refs = [
        f"mode:{mode.mode_id}",
        f"loop:{execution_loop_id}",
        f"loop:{planning_loop_id}",
    ]
    if completion_behavior is not None:
        if hasattr(completion_behavior, "stage"):
            stage_value = completion_behavior.stage.value
        else:
            stage_value = "unknown"
        refs.append(f"completion_behavior:{planning_loop_id}:{stage_value}")
    return tuple(refs)


def _build_graph_source_refs(
    mode_id: str,
    execution_loop_id: str,
    planning_loop_id: str,
    *,
    has_planning_completion_behavior: bool,
) -> tuple[str, ...]:
    refs = [
        f"mode:{mode_id}",
        f"graph_loop:{execution_loop_id}",
        f"graph_loop:{planning_loop_id}",
    ]
    if has_planning_completion_behavior:
        refs.append(f"graph_completion_behavior:{planning_loop_id}")
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


def _utc_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _load_existing_plan(path: Path) -> FrozenRunPlan | None:
    if not path.is_file():
        return None

    try:
        payload = path.read_text(encoding="utf-8")
    except OSError:
        return None

    try:
        return FrozenRunPlan.model_validate_json(payload)
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
    "CompileOutcome",
    "CompilerValidationError",
    "compile_and_persist_workspace_plan",
    "preview_graph_loop_plan",
]
