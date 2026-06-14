"""Config-swap tests proving multi-config runtime.

These tests compile representative flows across all five config-swap configs
without mutating kernel source code. All behavior differences come from
mode asset data and RuntimeConfig.
"""

from __future__ import annotations

from pathlib import Path

from millrace_ai.architecture import CompiledRunPlan
from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import Plane
from millrace_ai.paths import bootstrap_workspace

# ── helpers ────────────────────────────────────────────────────────────────


def _compile_for_config(
    tmp_path: Path,
    mode_id: str,
    *,
    recovery: dict | None = None,
) -> "CompiledRunPlan":
    """Compile a mode and return the plan, failing with the config name."""
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    config_kwargs = {}
    if recovery is not None:
        config_kwargs["recovery"] = recovery

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(**config_kwargs),
        requested_mode_id=mode_id,
    )

    assert outcome.diagnostics.ok is True, (
        f"Config-swap compilation failed for `{mode_id}`: "
        f"{outcome.diagnostics.errors}"
    )
    assert outcome.active_plan is not None
    return outcome.active_plan


def _stage_kind_ids(plan: "CompiledRunPlan") -> set[str]:
    return {
        node.stage_kind_id
        for graph in plan.graphs_by_plane.values()
        for node in graph.nodes
    }


# ── config-swap compilation tests ──────────────────────────────────────────


def test_config_swap_minimal_three_plane_compiles_three_planes(tmp_path: Path) -> None:
    """minimal_three_plane compiles three planes with only basic stage kinds."""
    plan = _compile_for_config(tmp_path, "minimal_three_plane")

    assert plan.mode_id == "minimal_three_plane"
    assert plan.learning_graph is not None

    assert set(plan.graphs_by_plane) == {
        Plane.EXECUTION, Plane.PLANNING, Plane.LEARNING,
    }

    stage_ids = _stage_kind_ids(plan)
    assert stage_ids == {"basic_worker", "basic_planner", "basic_learner"}

    # No threshold policies (fixture mode)
    for graph in plan.graphs_by_plane.values():
        assert graph.compiled_threshold_policies == ()


def test_config_swap_standard_millrace_compiles_full_standard_planes(tmp_path: Path) -> None:
    """standard_millrace compiles with all standard execution and planning
    stages, pi_rpc runner."""
    plan = _compile_for_config(tmp_path, "standard_millrace")

    assert plan.mode_id == "lad_pi"
    assert plan.learning_graph is None

    stage_ids = _stage_kind_ids(plan)
    assert "lad_builder" in stage_ids
    assert "lad_checker" in stage_ids
    assert "lad_troubleshooter" in stage_ids
    assert "recon" in stage_ids
    assert "lad_planner" in stage_ids
    assert "lad_arbiter" in stage_ids

    # Standard thresholds present
    all_thresholds = {
        p.policy_id
        for graph in plan.graphs_by_plane.values()
        for p in graph.compiled_threshold_policies
    }
    assert "execution.fix-needed.exhaustion" in all_thresholds
    assert "execution.blocked.recovery" in all_thresholds


def test_config_swap_learning_enabled_millrace_has_learning_plane(tmp_path: Path) -> None:
    """learning_enabled_millrace compiles three planes with triggers."""
    plan = _compile_for_config(tmp_path, "learning_enabled_millrace")

    assert plan.mode_id == "learning_lad_pi"
    assert plan.learning_graph is not None

    assert set(plan.graphs_by_plane) == {
        Plane.EXECUTION, Plane.PLANNING, Plane.LEARNING,
    }

    # Learning stages
    learning_stages = {node.stage_kind_id for node in plan.learning_graph.nodes}
    assert learning_stages == {"analyst", "professor", "curator", "librarian"}

    # Learning triggers bridge execution/planning into learning
    trigger_pairs = {
        (rule.source_stage.value, rule.target_stage.value)
        for rule in plan.learning_trigger_rules
    }
    assert ("doublechecker", "analyst") in trigger_pairs
    assert ("troubleshooter", "analyst") in trigger_pairs
    assert ("consultant", "analyst") in trigger_pairs
    assert ("planner", "librarian") in trigger_pairs


def test_config_swap_recovery_heavy_has_elevated_thresholds(tmp_path: Path) -> None:
    """recovery_heavy_millrace compiles with different recovery thresholds
    than standard, driven by the mode's declared recovery policy selection."""
    recovery_heavy = _compile_for_config(
        tmp_path,
        "recovery_heavy_millrace",
    )

    assert recovery_heavy.mode_id == "recovery_heavy_millrace"

    heavy_thresholds = {
        (p.policy_id, p.threshold)
        for graph in recovery_heavy.graphs_by_plane.values()
        for p in graph.compiled_threshold_policies
    }

    standard = _compile_for_config(
        tmp_path / "standard",
        "standard_millrace",
    )

    standard_thresholds = {
        (p.policy_id, p.threshold)
        for graph in standard.graphs_by_plane.values()
        for p in graph.compiled_threshold_policies
    }

    assert heavy_thresholds != standard_thresholds, (
        f"recovery_heavy thresholds ({heavy_thresholds}) "
        f"should differ from standard ({standard_thresholds})"
    )

    # Recovery-heavy mode selects recovery_heavy_policies.json which
    # lowers blocked-recovery thresholds to 1.
    assert ("execution.blocked.recovery", 1) in heavy_thresholds
    assert ("planning.blocked.recovery", 1) in heavy_thresholds


def test_config_swap_generic_two_plane_fixture_minimal_stages(tmp_path: Path) -> None:
    """generic_two_plane_fixture compiles with only basic_worker and
    basic_planner."""
    plan = _compile_for_config(tmp_path, "generic_two_plane_fixture")

    assert plan.mode_id == "generic_two_plane_fixture"
    assert plan.learning_graph is None

    stage_ids = _stage_kind_ids(plan)
    assert stage_ids == {"basic_worker", "basic_planner"}

    # No threshold policies
    for graph in plan.graphs_by_plane.values():
        assert graph.compiled_threshold_policies == ()


def test_config_swap_all_five_produce_unique_compiled_plan_ids(tmp_path: Path) -> None:
    """All five configs produce distinct compiled_plan_ids from the same
    kernel without source-code mutations."""
    configs = [
        ("minimal_three_plane", None),
        ("standard_millrace", None),
        ("learning_enabled_millrace", None),
        (
            "recovery_heavy_millrace",
            None,
        ),
        ("generic_two_plane_fixture", None),
    ]

    plans: dict[str, "CompiledRunPlan"] = {}

    for mode_id, recovery in configs:
        plan = _compile_for_config(
            tmp_path / mode_id, mode_id, recovery=recovery,
        )
        plans[mode_id] = plan

    # Every config produces a unique compiled plan identity
    plan_ids = {plan.compiled_plan_id for plan in plans.values()}
    assert len(plan_ids) == len(plans), (
        f"Config-swap produced {len(plan_ids)} unique plan IDs across "
        f"{len(plans)} configs"
    )

    # Stage kinds differ materially
    stages_by_config = {
        mode_id: _stage_kind_ids(plan)
        for mode_id, plan in plans.items()
    }

    # minimal_three_plane has learning; generic_two_plane does not
    assert stages_by_config["minimal_three_plane"] != (
        stages_by_config["generic_two_plane_fixture"]
    )

    # standard_millrace has full stages unlike minimal/generic
    assert stages_by_config["standard_millrace"] != (
        stages_by_config["minimal_three_plane"]
    )

    # learning_enabled differs from standard by having learning stages
    assert stages_by_config["learning_enabled_millrace"] != (
        stages_by_config["standard_millrace"]
    )
