"""Workspace compiled-plan API."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from millrace_ai.architecture import CompiledRunPlan
from millrace_ai.assets import (
    load_builtin_graph_loop_definition,
    load_builtin_mode_definition,
    load_builtin_stage_kind_definitions,
)
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import CompileDiagnostics, Plane
from millrace_ai.errors import AssetValidationError
from millrace_ai.paths import WorkspacePaths

from .assets import build_resolved_asset_refs
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
from .validation import validate_mode_stage_maps


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
        plan = compile_compiled_run_plan(
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
        for stage_kind in load_builtin_stage_kind_definitions(assets_root=assets_root)
    }
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
        source_refs=build_graph_source_refs(
            mode.mode_id,
            graphs_by_plane,
            has_planning_completion_behavior=planning_graph.completion_behavior is not None,
        ),
    )


__all__ = ["compile_and_persist_workspace_plan", "compile_compiled_run_plan"]
