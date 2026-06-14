"""Config-swap runtime tests proving multi-config RuntimeEngine behavior.

These tests prove the same runtime kernel starts up and produces different
compiled plans when config-swapped, without any source-code changes.
"""

from __future__ import annotations

from pathlib import Path

from millrace_ai.architecture import CompiledRunPlan
from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import Plane
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine


def _unused_stage_runner(request: StageRunRequest) -> RunnerRawResult:
    raise AssertionError(
        f"stage runner should not be called in config-swap startup tests: "
        f"{request.stage.value}"
    )


def _bootstrap_and_startup(
    tmp_path: Path,
    mode_id: str,
    *,
    recovery: dict | None = None,
) -> tuple["RuntimeEngine", "CompiledRunPlan"]:
    """Compile, persist, and start up RuntimeEngine for a config."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))

    config_kwargs = {}
    if recovery is not None:
        config_kwargs["recovery"] = recovery

    outcome = compile_and_persist_workspace_plan(
        paths,
        config=RuntimeConfig(**config_kwargs),
        requested_mode_id=mode_id,
        assets_root=paths.runtime_root,
    )
    assert outcome.diagnostics.ok is True, (
        f"Config-swap compilation failed for `{mode_id}`: "
        f"{outcome.diagnostics.errors}"
    )
    assert outcome.active_plan is not None

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id=mode_id)
    engine.startup()

    return engine, outcome.active_plan


def test_config_swap_runtime_startup_standard_millrace(tmp_path: Path) -> None:
    """RuntimeEngine starts up with standard_millrace config."""
    engine, plan = _bootstrap_and_startup(tmp_path, "standard_millrace")

    assert engine.compiled_plan is not None
    assert engine.compiled_plan.mode_id == "lad_pi"
    assert engine.compiled_plan.compiled_plan_id == plan.compiled_plan_id
    assert engine.compiled_plan.learning_graph is None
    assert set(engine.compiled_plan.graphs_by_plane) == {
        Plane.EXECUTION, Plane.PLANNING,
    }


def test_config_swap_runtime_startup_learning_enabled_millrace(tmp_path: Path) -> None:
    """RuntimeEngine starts up with learning_enabled_millrace config."""
    engine, plan = _bootstrap_and_startup(tmp_path, "learning_enabled_millrace")

    assert engine.compiled_plan is not None
    assert engine.compiled_plan.mode_id == "learning_lad_pi"
    assert engine.compiled_plan.compiled_plan_id == plan.compiled_plan_id
    assert engine.compiled_plan.learning_graph is not None
    assert len(engine.compiled_plan.learning_trigger_rules) == 4


def test_config_swap_runtime_startup_recovery_heavy_millrace(tmp_path: Path) -> None:
    """RuntimeEngine starts up with recovery_heavy_millrace config."""
    engine, plan = _bootstrap_and_startup(
        tmp_path,
        "recovery_heavy_millrace",
    )

    assert engine.compiled_plan is not None
    assert engine.compiled_plan.mode_id == "recovery_heavy_millrace"

    heavy_thresholds = {
        (p.policy_id, p.threshold)
        for graph in engine.compiled_plan.graphs_by_plane.values()
        for p in graph.compiled_threshold_policies
    }
    # Recovery-heavy mode selects recovery_heavy_policies.json which
    # lowers blocked-recovery thresholds to 1.
    assert ("execution.blocked.recovery", 1) in heavy_thresholds
    assert ("planning.blocked.recovery", 1) in heavy_thresholds


def test_config_swap_runtime_startup_minimal_three_plane(tmp_path: Path) -> None:
    """RuntimeEngine starts up with minimal_three_plane config."""
    engine, plan = _bootstrap_and_startup(tmp_path, "minimal_three_plane")

    assert engine.compiled_plan is not None
    assert engine.compiled_plan.mode_id == "minimal_three_plane"
    assert engine.compiled_plan.learning_graph is not None
    assert set(engine.compiled_plan.graphs_by_plane) == {
        Plane.EXECUTION, Plane.PLANNING, Plane.LEARNING,
    }


def test_config_swap_runtime_startup_generic_two_plane_fixture(tmp_path: Path) -> None:
    """RuntimeEngine starts up with generic_two_plane_fixture config."""
    engine, plan = _bootstrap_and_startup(
        tmp_path, "generic_two_plane_fixture",
    )

    assert engine.compiled_plan is not None
    assert engine.compiled_plan.mode_id == "generic_two_plane_fixture"
    assert engine.compiled_plan.learning_graph is None

    stage_ids = {
        node.stage_kind_id
        for graph in engine.compiled_plan.graphs_by_plane.values()
        for node in graph.nodes
    }
    assert stage_ids == {"basic_worker", "basic_planner"}


def test_config_swap_runtime_different_configs_produce_different_plans(
    tmp_path: Path,
) -> None:
    """The same RuntimeEngine kernel produces materially different compiled
    plans when config-swapped."""
    configs = [
        ("minimal_three_plane", None),
        ("standard_millrace", None),
        ("learning_enabled_millrace", None),
        ("recovery_heavy_millrace", False),
        ("generic_two_plane_fixture", None),
    ]

    plans: dict[str, "CompiledRunPlan"] = {}

    for mode_id, _is_recovery_heavy in configs:
        work_dir = tmp_path / mode_id
        paths = bootstrap_workspace(workspace_paths(work_dir / "workspace"))

        engine = RuntimeEngine(
            paths,
            stage_runner=_unused_stage_runner,
            mode_id=mode_id,
        )

        engine.startup()
        assert engine.compiled_plan is not None
        plans[mode_id] = engine.compiled_plan

    # Every config produces a unique compiled plan
    plan_ids = {plan.compiled_plan_id for plan in plans.values()}
    assert len(plan_ids) == len(plans), (
        f"Config-swap runtime produced duplicate plan IDs: {plan_ids}"
    )

    # Learning differs across configs
    assert plans["learning_enabled_millrace"].learning_graph is not None
    assert plans["standard_millrace"].learning_graph is None

    # Thresholds differ
    heavy_thresholds = {
        (p.policy_id, p.threshold)
        for graph in plans["recovery_heavy_millrace"].graphs_by_plane.values()
        for p in graph.compiled_threshold_policies
    }
    standard_thresholds = {
        (p.policy_id, p.threshold)
        for graph in plans["standard_millrace"].graphs_by_plane.values()
        for p in graph.compiled_threshold_policies
    }
    assert heavy_thresholds != standard_thresholds
