"""Workspace compiled-plan API."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from millrace_ai.architecture import (
    CompiledRunPlan,
    GraphLoopDefinition,
    GraphLoopThresholdPolicyDefinition,
    LaneConflictPolicyDefinition,
    PlaneQueueClaimPolicyDefinition,
    WorkflowLaneDefinition,
    WorkflowPlaneSchedulerPolicyDefinition,
)
from millrace_ai.assets import (
    discover_extension_package_manifests,
    discover_stage_kind_definitions,
    load_builtin_graph_loop_definition,
    load_builtin_mode_definition,
    load_builtin_workflow_primitives,
)
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import CompileDiagnostics, ModeDefinition, Plane
from millrace_ai.errors import AssetValidationError
from millrace_ai.paths import WorkspacePaths

from .assets import build_resolved_asset_refs
from .capabilities import merge_execution_capability_summaries
from .fingerprints import (
    build_compile_input_fingerprint,
    build_compiled_plan_id,
    build_existing_plan_input_fingerprint,
)
from .graph_materialization import (
    build_graph_source_refs,
    materialize_graph_plane_plan,
    selected_stages_for_graph_loops,
)
from .learning_triggers import validate_learning_trigger_rules
from .mode_resolution import resolve_compile_assets_root, resolve_mode_id, resolve_paths
from .outcomes import CompileOutcome, CompilerValidationError
from .persistence import atomic_write_json, load_existing_plan, utc_now
from .plan_authority import has_required_workflow_authority
from .validation import (
    validate_lane_conflict_coverage,
    validate_mode_stage_maps,
    validate_required_extensions,
    validate_scheduler_policy,
    validate_workflow_primitives,
)

_ModelT = TypeVar("_ModelT")


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

    paths = resolve_paths(target)
    compile_time = utc_now(now)
    mode_id = resolve_mode_id(requested_mode_id, config)
    compiled_plan_path = paths.state_dir / "compiled_plan.json"
    diagnostics_path = paths.state_dir / "compile_diagnostics.json"
    compile_assets_root = resolve_compile_assets_root(paths, assets_root)

    last_known_good = load_existing_plan(compiled_plan_path)
    last_known_good_has_workflow_authority = (
        has_required_workflow_authority(last_known_good)
        if last_known_good is not None
        else False
    )
    compile_input_fingerprint = None
    if last_known_good is not None:
        try:
            compile_input_fingerprint = build_existing_plan_input_fingerprint(
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
        and last_known_good_has_workflow_authority
        and compile_input_fingerprint is not None
        and last_known_good.compile_input_fingerprint == compile_input_fingerprint
    ):
        warnings = _compiled_plan_warnings(last_known_good)
        diagnostics = CompileDiagnostics(
            ok=True,
            mode_id=mode_id,
            warnings=warnings,
            emitted_at=compile_time,
        )
        return CompileOutcome(
            active_plan=last_known_good,
            diagnostics=diagnostics,
            used_last_known_good=False,
            compile_input_fingerprint=compile_input_fingerprint,
        )

    try:
        plan = compile_compiled_run_plan(
            paths=paths,
            config=config,
            mode_id=mode_id,
            assets_root=compile_assets_root,
            compile_time=compile_time,
        )
        warnings = _compiled_plan_warnings(plan)
        diagnostics = CompileDiagnostics(
            ok=True,
            mode_id=mode_id,
            warnings=warnings,
            emitted_at=compile_time,
        )
        atomic_write_json(compiled_plan_path, plan.model_dump(mode="json"))
        atomic_write_json(diagnostics_path, diagnostics.model_dump(mode="json"))
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
        atomic_write_json(diagnostics_path, diagnostics.model_dump(mode="json"))
        active_plan = last_known_good if last_known_good_has_workflow_authority else None
        used_last_known_good = active_plan is not None
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


def compile_compiled_run_plan(
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
    validate_mode_stage_maps(
        mode,
        selected_stages_for_graph_loops(*graph_loops.values()),
    )

    extension_manifests = discover_extension_package_manifests(assets_root=assets_root)
    stage_kinds = {
        stage_kind.stage_kind_id: stage_kind
        for stage_kind in discover_stage_kind_definitions(assets_root=assets_root)
    }
    workflow_primitives = load_builtin_workflow_primitives(assets_root=assets_root)
    terminal_actions_by_id = {
        ta.terminal_action_id: ta
        for ta in workflow_primitives.terminal_actions
    }
    queue_claim_policies_by_plane = _map_queue_claim_policies_by_plane(
        workflow_primitives.queue_claim_policies
    )
    scheduler_policy = _build_scheduler_policy(
        mode=mode,
        queue_claim_policies_by_plane=queue_claim_policies_by_plane,
        workflow_primitives=workflow_primitives,
    )
    queue_claim_policies_by_plane = dict(scheduler_policy.claim_policies_by_plane)
    selected_recovery_policies = _mode_selected_recovery_policies(
        workflow_primitives.recovery_policies,
        mode.recovery_policy_ids,
    )
    request_context_profiles_by_id = _map_by_attr(
        workflow_primitives.request_context_profiles,
        "profile_id",
    )
    graph_loops = _apply_mode_recovery_policy_overrides(
        graph_loops=graph_loops,
        mode=mode,
        workflow_primitives=workflow_primitives,
    )
    graphs_by_plane = {
        plane: materialize_graph_plane_plan(
            graph_loop=graph_loop,
            mode=mode,
            config=config,
            stage_kinds=stage_kinds,
            request_context_profiles_by_id=request_context_profiles_by_id,
        )
        for plane, graph_loop in graph_loops.items()
    }
    selected_stages = selected_stages_for_graph_loops(*graph_loops.values())
    validate_learning_trigger_rules(mode, selected_stages)
    validate_workflow_primitives(
        mode=mode,
        graphs_by_plane=graphs_by_plane,
        stage_kinds=stage_kinds,
        workflow_primitives=workflow_primitives,
        scheduler_policy=scheduler_policy,
    )
    validate_scheduler_policy(
        mode=mode,
        workflow_primitives=workflow_primitives,
    )
    validate_lane_conflict_coverage(
        scheduler_policy=scheduler_policy,
        mode=mode,
    )
    validate_required_extensions(
        mode=mode,
        discovered_manifests=extension_manifests,
        graph_loops=graph_loops,
        stage_kinds=stage_kinds,
        terminal_actions_by_id=terminal_actions_by_id,
        workflow_primitives=workflow_primitives,
        scheduler_policy=scheduler_policy,
        recovery_policies=selected_recovery_policies,
    )

    execution_graph = graphs_by_plane[Plane.EXECUTION]
    planning_graph = graphs_by_plane[Plane.PLANNING]
    learning_graph = graphs_by_plane.get(Plane.LEARNING)

    resolved_assets = build_resolved_asset_refs(
        paths=paths,
        mode=mode,
        graph_loops=graph_loops,
        node_plans=tuple(node for graph in graphs_by_plane.values() for node in graph.nodes),
        assets_root=resolve_compile_assets_root(paths, assets_root),
    )
    compile_input_fingerprint = build_compile_input_fingerprint(
        config=config,
        mode_id=mode.mode_id,
        resolved_assets=resolved_assets,
        paths=paths,
        assets_root=resolve_compile_assets_root(paths, assets_root),
    )

    return CompiledRunPlan(
        compiled_plan_id=build_compiled_plan_id(
            mode_id=mode.mode_id,
            loop_ids_by_plane=mode.loop_ids_by_plane,
            graphs_by_plane=graphs_by_plane,
            concurrency_policy=mode.concurrency_policy,
            learning_trigger_rules=mode.learning_trigger_rules,
            workflow_authority={
                "work_item_families_by_id": _map_by_attr(
                    workflow_primitives.work_item_families,
                    "family_id",
                ),
                "artifact_contracts_by_id": _map_by_attr(
                    workflow_primitives.artifact_contracts,
                    "artifact_id",
                ),
                "artifact_contracts": workflow_primitives.artifact_contracts,
                "document_adapters_by_id": _map_by_attr(
                    workflow_primitives.document_adapters,
                    "adapter_id",
                ),
                "queue_claim_policies_by_plane": {
                    plane.value: policy.model_dump(mode="json")
                    for plane, policy in queue_claim_policies_by_plane.items()
                },
                "terminal_actions_by_id": _map_by_attr(
                    workflow_primitives.terminal_actions,
                    "terminal_action_id",
                ),
                "lifecycle_mutation_plans_by_id": _map_by_attr(
                    workflow_primitives.lifecycle_mutation_plans,
                    "plan_id",
                ),
                "request_context_profiles_by_id": request_context_profiles_by_id,
                "request_context_providers_by_id": _map_by_attr(
                    workflow_primitives.request_context_providers,
                    "provider_id",
                ),
                "request_context_render_plans_by_id": _map_by_attr(
                    workflow_primitives.request_context_render_plans,
                    "render_plan_id",
                ),
                "runtime_effect_handlers_by_id": _map_by_attr(
                    workflow_primitives.runtime_effect_handlers,
                    "handler_id",
                ),
                "runtime_effect_runners_by_id": _map_by_attr(
                    workflow_primitives.runtime_effect_runners,
                    "runner_id",
                ),
                "runtime_effect_rules": workflow_primitives.runtime_effect_rules,
                "runtime_effect_operations_by_id": _map_by_attr(
                    workflow_primitives.runtime_effect_operations,
                    "operation_id",
                ),
                "runtime_operations_by_id": _map_by_attr(
                    workflow_primitives.runtime_operations,
                    "operation_id",
                ),
                "effect_stores_by_id": _map_by_attr(
                    workflow_primitives.effect_stores,
                    "store_id",
                ),
                "effect_validators_by_id": _map_by_attr(
                    workflow_primitives.effect_validators,
                    "validator_id",
                ),
                "workflow_recovery_policies_by_id": _map_by_attr(
                    _mode_selected_recovery_policies(
                        workflow_primitives.recovery_policies,
                        mode.recovery_policy_ids,
                    ),
                    "policy_id",
                ),
                "runtime_failure_policies_by_id": _map_by_attr(
                    workflow_primitives.runtime_failure_policies,
                    "policy_id",
                ),
                "workspace_schema_epoch": (
                    workflow_primitives.workspace_schema_epoch.model_dump(mode="json")
                    if workflow_primitives.workspace_schema_epoch is not None
                    else None
                ),
                "scheduler_policy": scheduler_policy,
            },
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
        artifact_contracts_by_id=_map_by_attr(
            workflow_primitives.artifact_contracts,
            "artifact_id",
        ),
        artifact_contracts=workflow_primitives.artifact_contracts,
        work_item_families_by_id=_map_by_attr(
            workflow_primitives.work_item_families,
            "family_id",
        ),
        document_adapters_by_id=_map_by_attr(
            workflow_primitives.document_adapters,
            "adapter_id",
        ),
        queue_claim_policies_by_plane=queue_claim_policies_by_plane,
        terminal_actions_by_id=_map_by_attr(
            workflow_primitives.terminal_actions,
            "terminal_action_id",
        ),
        lifecycle_mutation_plans_by_id=_map_by_attr(
            workflow_primitives.lifecycle_mutation_plans,
            "plan_id",
        ),
        request_context_profiles_by_id=request_context_profiles_by_id,
        request_context_providers_by_id=_map_by_attr(
            workflow_primitives.request_context_providers,
            "provider_id",
        ),
        request_context_render_plans_by_id=_map_by_attr(
            workflow_primitives.request_context_render_plans,
            "render_plan_id",
        ),
        runtime_effect_handlers_by_id=_map_by_attr(
            workflow_primitives.runtime_effect_handlers,
            "handler_id",
        ),
        runtime_effect_runners_by_id=_map_by_attr(
            workflow_primitives.runtime_effect_runners,
            "runner_id",
        ),
        runtime_effect_operations_by_id=_map_by_attr(
            workflow_primitives.runtime_effect_operations,
            "operation_id",
        ),
        runtime_operations_by_id=_map_by_attr(
            workflow_primitives.runtime_operations,
            "operation_id",
        ),
        effect_stores_by_id=_map_by_attr(
            workflow_primitives.effect_stores,
            "store_id",
        ),
        effect_validators_by_id=_map_by_attr(
            workflow_primitives.effect_validators,
            "validator_id",
        ),
        runtime_effect_primitives_by_id=_map_by_attr(
            workflow_primitives.runtime_effect_primitives,
            "primitive_id",
        ),
        runtime_effect_rules=workflow_primitives.runtime_effect_rules,
        workflow_recovery_policies_by_id=_map_by_attr(
            _mode_selected_recovery_policies(
                workflow_primitives.recovery_policies,
                mode.recovery_policy_ids,
            ),
            "policy_id",
        ),
        runtime_failure_policies_by_id=_map_by_attr(
            workflow_primitives.runtime_failure_policies,
            "policy_id",
        ),
        scheduler_policy=scheduler_policy,
        lane_conflict_policies_by_id={
            policy.policy_id: policy
            for policy in scheduler_policy.lane_conflict_policies
        },
        workspace_schema_epoch=workflow_primitives.workspace_schema_epoch,
        compiled_at=compile_time,
        resolved_assets=resolved_assets,
        source_refs=build_graph_source_refs(
            mode.mode_id,
            graphs_by_plane,
            has_planning_completion_behavior=planning_graph.completion_behavior is not None,
        ),
        execution_capability_summary=merge_execution_capability_summaries(
            tuple(graph.execution_capability_summary for graph in graphs_by_plane.values())
        ),
    )


def _map_by_attr(items: tuple[_ModelT, ...], attr: str) -> dict[str, _ModelT]:
    return {str(getattr(item, attr)): item for item in items}


def _compiled_plan_warnings(plan: CompiledRunPlan) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            warning
            for graph in plan.graphs_by_plane.values()
            for node in graph.nodes
            for warning in node.model_assignment_warnings
        )
    )


def _map_queue_claim_policies_by_plane(
    policies: tuple[PlaneQueueClaimPolicyDefinition, ...],
) -> dict[Plane, PlaneQueueClaimPolicyDefinition]:
    policies_by_plane: dict[Plane, PlaneQueueClaimPolicyDefinition] = {}
    for policy in policies:
        if policy.policy_id != f"{policy.plane.value}.default":
            continue
        if policy.plane in policies_by_plane:
            raise CompilerValidationError(
                f"Duplicate default queue claim policy for plane: {policy.plane.value}"
            )
        policies_by_plane[policy.plane] = policy
    return policies_by_plane


def _build_scheduler_policy(
    *,
    mode: ModeDefinition,
    queue_claim_policies_by_plane: dict[Plane, PlaneQueueClaimPolicyDefinition],
    workflow_primitives: object,
) -> WorkflowPlaneSchedulerPolicyDefinition:
    if mode.scheduler_policy_id is not None:
        policy_id = mode.scheduler_policy_id
        scheduler_policies_by_id = {
            policy.policy_id: policy
            for policy in getattr(workflow_primitives, "scheduler_policies", ())
        }
        if policy_id in scheduler_policies_by_id:
            resolved = scheduler_policies_by_id[policy_id]
            if mode.lane_conflict_policies is not None:
                resolved = resolved.model_copy(
                    update={
                        "lane_conflict_policies": tuple(
                            LaneConflictPolicyDefinition.model_validate(policy)
                            for policy in mode.lane_conflict_policies
                        )
                    }
                )
            return resolved
        raise CompilerValidationError(
            f"mode {mode.mode_id} references unknown scheduler policy: {policy_id}"
        )

    planes = tuple(mode.loop_ids_by_plane)
    has_learning = Plane.LEARNING in mode.loop_ids_by_plane
    auto_policy_id = f"{mode.mode_id}.scheduler"

    scheduler_policies_by_id = {
        policy.policy_id: policy
        for policy in getattr(workflow_primitives, "scheduler_policies", ())
    }

    default_policy_id = "default.three_plane" if has_learning else "default.two_plane"
    base_policy = scheduler_policies_by_id.get(default_policy_id)

    if base_policy is not None and set(base_policy.plane_order) == set(planes):
        policy_plane_order = base_policy.plane_order
        lanes = tuple(
            WorkflowLaneDefinition(
                lane_id=f"{plane.value}.main",
                plane=plane,
                allowed_family_ids=queue_claim_policies_by_plane[plane].family_order,
                claim_policy_id=queue_claim_policies_by_plane[plane].policy_id,
                max_active_runs=1,
            )
            for plane in policy_plane_order
        )
        if mode.lane_conflict_policies is not None:
            lane_conflict_policies = tuple(
                LaneConflictPolicyDefinition.model_validate(policy)
                for policy in mode.lane_conflict_policies
            )
        elif mode.concurrency_policy is not None:
            lane_conflict_policies = _default_lane_conflict_policies(mode)
        else:
            lane_conflict_policies = ()
        return base_policy.model_copy(
            update={
                "policy_id": auto_policy_id,
                "concurrency_policy_id": (
                    f"{mode.mode_id}.concurrency" if mode.concurrency_policy is not None else None
                ),
                "lane_conflict_policies": lane_conflict_policies,
                "claim_policies_by_plane": queue_claim_policies_by_plane,
            }
        )

    lanes = tuple(
        WorkflowLaneDefinition(
            lane_id=f"{plane.value}.main",
            plane=plane,
            allowed_family_ids=queue_claim_policies_by_plane[plane].family_order,
            claim_policy_id=queue_claim_policies_by_plane[plane].policy_id,
            max_active_runs=1,
        )
        for plane in mode.loop_ids_by_plane
    )
    if mode.lane_conflict_policies is None:
        lane_conflict_policies = _default_lane_conflict_policies(mode)
    else:
        lane_conflict_policies = tuple(
            LaneConflictPolicyDefinition.model_validate(policy)
            for policy in mode.lane_conflict_policies
        )
    foreground_order = tuple(
        p for p in (Plane.PLANNING, Plane.EXECUTION, Plane.LEARNING)
        if p in mode.loop_ids_by_plane
    )
    return WorkflowPlaneSchedulerPolicyDefinition(
        policy_id=auto_policy_id,
        plane_order=tuple(mode.loop_ids_by_plane),
        concurrency_policy_id=(
            f"{mode.mode_id}.concurrency" if mode.concurrency_policy is not None else None
        ),
        lanes=lanes,
        claim_policies_by_plane=queue_claim_policies_by_plane,
        completion_check_order=tuple(mode.loop_ids_by_plane),
        experimental_multi_lane=False,
        lane_conflict_policies=lane_conflict_policies,
        foreground_order=foreground_order,
    )


def _default_lane_conflict_policies(mode: ModeDefinition) -> tuple[LaneConflictPolicyDefinition, ...]:
    if mode.concurrency_policy is None:
        return ()
    policies: list[LaneConflictPolicyDefinition] = []
    seen_pairs: set[tuple[str, str]] = set()
    for plane_pair in mode.concurrency_policy.may_run_concurrently:
        if len(plane_pair) != 2:
            continue
        first_lane_id = f"{plane_pair[0].value}.main"
        second_lane_id = f"{plane_pair[1].value}.main"
        first_ordered_lane_id, second_ordered_lane_id = sorted((first_lane_id, second_lane_id))
        lane_pair = (first_ordered_lane_id, second_ordered_lane_id)
        if lane_pair in seen_pairs:
            continue
        seen_pairs.add(lane_pair)
        policies.append(
            LaneConflictPolicyDefinition(
                policy_id=f"{lane_pair[0]}-with-{lane_pair[1]}",
                lane_ids=(lane_pair[0],),
                concurrent_with_lane_ids=(lane_pair[1],),
                conflict_scopes=("workspace",),
                lock_acquisition_order=lane_pair,
                release_policy="on_result_applied",
                missing_lock_policy="block_dispatch",
            )
        )
    return tuple(policies)


def _mode_selected_recovery_policies(
    all_policies: tuple[object, ...],
    selected_policy_ids: tuple[str, ...],
) -> tuple[object, ...]:
    """Filter recovery policies to only those explicitly declared by the mode."""
    if not selected_policy_ids:
        return ()
    selected_set = set(selected_policy_ids)
    return tuple(
        policy for policy in all_policies
        if getattr(policy, "policy_id", None) in selected_set
    )


def _apply_mode_recovery_policy_overrides(
    *,
    graph_loops: dict[Plane, GraphLoopDefinition],
    mode: ModeDefinition,
    workflow_primitives: object,
) -> dict[Plane, GraphLoopDefinition]:
    """Override graph-loop threshold policies with mode-declared recovery policies."""
    if not mode.recovery_policy_ids:
        return graph_loops

    recovery_policies_by_id: dict[str, object] = {
        policy.policy_id: policy
        for policy in getattr(workflow_primitives, "recovery_policies", ())
    }

    # Build overrides keyed by (plane, counter_name) -> threshold
    overrides: dict[tuple[Plane, str], int] = {}
    for policy_id in mode.recovery_policy_ids:
        policy = recovery_policies_by_id.get(policy_id)
        if policy is None:
            raise CompilerValidationError(
                f"mode {mode.mode_id} references unknown recovery policy: {policy_id}"
            )
        target_plane = _plane_for_recovery_policy(policy, graph_loops)
        counter_name = str(getattr(policy, "counter_name", ""))
        threshold = getattr(policy, "threshold", None)
        if not counter_name or not isinstance(threshold, int) or threshold < 1:
            raise CompilerValidationError(
                f"recovery policy {policy_id} has invalid counter_name or threshold"
            )
        overrides[(target_plane, counter_name)] = threshold

    # Apply overrides to each graph loop's threshold policies
    overridden_loops: dict[Plane, GraphLoopDefinition] = {}
    for plane, graph_loop in graph_loops.items():
        if (
            graph_loop.dynamic_policies is None
            or not graph_loop.dynamic_policies.threshold_policies
        ):
            overridden_loops[plane] = graph_loop
            continue

        overridden_thresholds: list[GraphLoopThresholdPolicyDefinition] = []
        for tp in graph_loop.dynamic_policies.threshold_policies:
            counter_str = (
                tp.counter_name.value
                if hasattr(tp.counter_name, "value")
                else str(tp.counter_name)
            )
            override_threshold = overrides.get((plane, counter_str))
            if override_threshold is not None:
                overridden_thresholds.append(
                    tp.model_copy(update={"threshold": override_threshold})
                )
            else:
                overridden_thresholds.append(tp)

        new_dynamic = graph_loop.dynamic_policies.model_copy(
            update={"threshold_policies": tuple(overridden_thresholds)}
        )
        overridden_loops[plane] = graph_loop.model_copy(
            update={"dynamic_policies": new_dynamic}
        )

    return overridden_loops


def _plane_for_recovery_policy(
    policy: object,
    graph_loops: dict[Plane, GraphLoopDefinition],
) -> Plane:
    """Determine which plane a recovery policy belongs to by matching source node IDs."""
    policy_source_ids: set[str] = set(getattr(policy, "source_node_ids", ()))
    for plane, graph_loop in graph_loops.items():
        graph_node_ids = {node.node_id for node in graph_loop.nodes}
        if policy_source_ids & graph_node_ids:
            return plane
    raise CompilerValidationError(
        f"recovery policy {getattr(policy, 'policy_id', 'unknown')} "
        "source node IDs do not match any graph loop nodes"
    )


__all__ = ["compile_and_persist_workspace_plan", "compile_compiled_run_plan"]
