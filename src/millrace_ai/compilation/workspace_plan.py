"""Workspace compiled-plan API."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from millrace_ai.architecture import (
    CompiledRunPlan,
    LaneConflictPolicyDefinition,
    PlaneQueueClaimPolicyDefinition,
    WorkflowLaneDefinition,
    WorkflowPlaneSchedulerPolicyDefinition,
)
from millrace_ai.assets import (
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

    stage_kinds = {
        stage_kind.stage_kind_id: stage_kind
        for stage_kind in discover_stage_kind_definitions(assets_root=assets_root)
    }
    workflow_primitives = load_builtin_workflow_primitives(assets_root=assets_root)
    graphs_by_plane = {
        plane: materialize_graph_plane_plan(
            graph_loop=graph_loop,
            mode=mode,
            config=config,
            stage_kinds=stage_kinds,
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
    )
    queue_claim_policies_by_plane = _map_queue_claim_policies_by_plane(
        workflow_primitives.queue_claim_policies
    )
    scheduler_policy = _build_scheduler_policy(
        mode=mode,
        queue_claim_policies_by_plane=queue_claim_policies_by_plane,
    )
    validate_lane_conflict_coverage(
        scheduler_policy=scheduler_policy,
        mode=mode,
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
                "effect_stores_by_id": _map_by_attr(
                    workflow_primitives.effect_stores,
                    "store_id",
                ),
                "effect_validators_by_id": _map_by_attr(
                    workflow_primitives.effect_validators,
                    "validator_id",
                ),
                "workflow_recovery_policies_by_id": _map_by_attr(
                    workflow_primitives.recovery_policies,
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
        effect_stores_by_id=_map_by_attr(
            workflow_primitives.effect_stores,
            "store_id",
        ),
        effect_validators_by_id=_map_by_attr(
            workflow_primitives.effect_validators,
            "validator_id",
        ),
        runtime_effect_rules=workflow_primitives.runtime_effect_rules,
        workflow_recovery_policies_by_id=_map_by_attr(
            workflow_primitives.recovery_policies,
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
        if policy.plane in policies_by_plane:
            raise CompilerValidationError(f"Duplicate queue claim policy for plane: {policy.plane.value}")
        policies_by_plane[policy.plane] = policy
    return policies_by_plane


def _build_scheduler_policy(
    *,
    mode: ModeDefinition,
    queue_claim_policies_by_plane: dict[Plane, PlaneQueueClaimPolicyDefinition],
) -> WorkflowPlaneSchedulerPolicyDefinition:
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
    return WorkflowPlaneSchedulerPolicyDefinition(
        policy_id=f"{mode.mode_id}.scheduler",
        plane_order=tuple(mode.loop_ids_by_plane),
        concurrency_policy_id=(
            f"{mode.mode_id}.concurrency" if mode.concurrency_policy is not None else None
        ),
        lanes=lanes,
        claim_policies_by_plane=queue_claim_policies_by_plane,
        completion_check_order=tuple(mode.loop_ids_by_plane),
        experimental_multi_lane=False,
        lane_conflict_policies=lane_conflict_policies,
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


__all__ = ["compile_and_persist_workspace_plan", "compile_compiled_run_plan"]
