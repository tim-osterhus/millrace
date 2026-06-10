"""Shipped registry compatibility tests.

Prove that shipped Millrace modes resolve stage and plane identity
through the shipped registry data (stage-kind JSON assets and graph-loop
assets) rather than through hard-coded kernel enums.

ADRs: ADR-0013 (generic-stage-and-plane-registry), ADR-0012 (kernel-boundary).
"""

from __future__ import annotations

from pathlib import Path

from millrace_ai.contracts import Plane
from millrace_ai.contracts.stage_metadata import (
    STAGE_METADATA_BY_VALUE,
    allowed_result_classes_by_outcome,
    known_stage_values,
    legal_terminal_markers,
    running_status_marker,
    stage_metadata,
)

# ---------------------------------------------------------------------------
# Shipped stage-kind assets must produce correct registry facades
# ---------------------------------------------------------------------------


def test_all_shipped_stages_have_known_plane_membership() -> None:
    """Every shipped stage in the registry must resolve to a known plane."""
    for stage_value in known_stage_values():
        metadata = stage_metadata(stage_value)
        assert metadata.plane in (Plane.EXECUTION, Plane.PLANNING, Plane.LEARNING), (
            f"Stage {stage_value} resolves to unknown plane {metadata.plane!r}"
        )


def test_plane_membership_is_loaded_from_stage_kind_assets() -> None:
    """Plane membership must come from the JSON stage-kind registry assets."""
    from millrace_ai.assets.architecture import discover_stage_kind_definitions

    kinds = discover_stage_kind_definitions()
    kind_by_id = {k.stage_kind_id: k for k in kinds}

    for stage_value in known_stage_values():
        kind = kind_by_id.get(stage_value)
        assert kind is not None, (
            f"Shipped stage {stage_value} has no stage-kind asset"
        )
        metadata = stage_metadata(stage_value)
        assert kind.plane == metadata.plane, (
            f"Stage {stage_value} plane mismatch: asset={kind.plane}, "
            f"metadata={metadata.plane}"
        )


def test_running_markers_are_loaded_from_stage_kind_assets() -> None:
    """Running/blocked markers must come from stage-kind assets."""
    from millrace_ai.assets.architecture import discover_stage_kind_definitions

    kinds = discover_stage_kind_definitions()
    kind_by_id = {k.stage_kind_id: k for k in kinds}

    for stage_value in known_stage_values():
        kind = kind_by_id.get(stage_value)
        if kind is None:
            continue
        runtime_marker = running_status_marker(stage_value)
        assert runtime_marker == kind.running_status_marker, (
            f"Stage {stage_value} running marker mismatch: "
            f"asset={kind.running_status_marker!r}, "
            f"metadata={runtime_marker!r}"
        )


def test_legal_terminal_markers_are_loaded_from_stage_kind_assets() -> None:
    """Legal terminal markers must come from stage-kind assets."""
    from millrace_ai.assets.architecture import discover_stage_kind_definitions

    kinds = discover_stage_kind_definitions()
    kind_by_id = {k.stage_kind_id: k for k in kinds}

    for stage_value in known_stage_values():
        kind = kind_by_id.get(stage_value)
        if kind is None:
            continue
        markers = frozenset(legal_terminal_markers(stage_value))
        asset_markers = frozenset(
            f"### {outcome}" for outcome in kind.legal_outcomes
        )
        assert markers == asset_markers, (
            f"Stage {stage_value} legal terminal markers mismatch: "
            f"asset={sorted(asset_markers)}, metadata={sorted(markers)}"
        )


def test_result_class_mappings_are_loaded_from_stage_kind_assets() -> None:
    """Result-class policy must come from stage-kind assets."""
    from millrace_ai.assets.architecture import discover_stage_kind_definitions

    kinds = discover_stage_kind_definitions()
    kind_by_id = {k.stage_kind_id: k for k in kinds}

    for stage_value in known_stage_values():
        kind = kind_by_id.get(stage_value)
        if kind is None:
            continue
        metadata_classes = allowed_result_classes_by_outcome(stage_value)
        asset_classes = kind.allowed_result_classes_by_outcome
        assert metadata_classes == asset_classes, (
            f"Stage {stage_value} result class mismatch: "
            f"asset={asset_classes}, metadata={metadata_classes}"
        )


# ---------------------------------------------------------------------------
# Custom stage-kind fixtures resolve correctly
# ---------------------------------------------------------------------------


def test_minimal_three_plane_stage_kinds_resolve_through_registry() -> None:
    """Custom stage kinds from minimal_three_plane resolve through registry assets."""
    from millrace_ai.assets.architecture import discover_stage_kind_definitions

    kinds = discover_stage_kind_definitions()
    kind_by_id = {k.stage_kind_id: k for k in kinds}

    fixture_kinds = ("basic_worker", "basic_planner", "basic_learner")
    for k_id in fixture_kinds:
        assert k_id in kind_by_id, (
            f"Minimal fixture stage kind {k_id!r} not found in registry"
        )

    assert kind_by_id["basic_worker"].runtime_stage.value == "builder"
    assert kind_by_id["basic_planner"].runtime_stage.value == "planner"
    assert kind_by_id["basic_learner"].runtime_stage.value == "analyst"

    assert kind_by_id["basic_worker"].plane == Plane.EXECUTION
    assert kind_by_id["basic_planner"].plane == Plane.PLANNING
    assert kind_by_id["basic_learner"].plane == Plane.LEARNING

    assert kind_by_id["basic_worker"].closure_role is False
    assert kind_by_id["basic_planner"].closure_role is False
    assert kind_by_id["basic_learner"].closure_role is False
    assert kind_by_id["basic_worker"].recovery_role is None
    assert kind_by_id["basic_planner"].recovery_role is None
    assert kind_by_id["basic_learner"].recovery_role is None


def test_custom_stage_kinds_are_not_in_shipped_metadata() -> None:
    """Custom stage kinds must not appear in the shipped stage metadata facade."""
    fixture_kinds = ("basic_worker", "basic_planner", "basic_learner")
    for k_id in fixture_kinds:
        assert k_id not in STAGE_METADATA_BY_VALUE, (
            f"Fixture stage kind {k_id!r} incorrectly appears in "
            f"shipped STAGE_METADATA_BY_VALUE"
        )


# ---------------------------------------------------------------------------
# Graph-loop assets resolve correct plane identity
# ---------------------------------------------------------------------------


def test_shipped_graph_loops_declare_correct_plane() -> None:
    """Every shipped graph loop asset must declare the correct plane."""
    from millrace_ai.assets.loop_graphs import discover_graph_loop_definitions

    loops = discover_graph_loop_definitions()
    loops_by_id = {loop_def.loop_id: loop_def for loop_def in loops}

    for loop_id, expected_plane in (
        ("execution.standard", Plane.EXECUTION),
        ("execution.with_integrator", Plane.EXECUTION),
        ("planning.standard", Plane.PLANNING),
        ("planning.blueprint", Plane.PLANNING),
        ("learning.standard", Plane.LEARNING),
    ):
        loop = loops_by_id.get(loop_id)
        assert loop is not None, f"Missing shipped loop: {loop_id}"
        assert loop.plane == expected_plane, (
            f"Loop {loop_id} has wrong plane: {loop.plane} (expected {expected_plane})"
        )


def test_minimal_graph_loops_declare_correct_plane() -> None:
    """Minimal fixture graph loops must declare correct planes."""
    from millrace_ai.assets.loop_graphs import discover_graph_loop_definitions

    loops = discover_graph_loop_definitions()
    loops_by_id = {loop_def.loop_id: loop_def for loop_def in loops}

    for loop_id, expected_plane in (
        ("execution.minimal_three_plane", Plane.EXECUTION),
        ("planning.minimal_three_plane", Plane.PLANNING),
        ("learning.minimal_three_plane", Plane.LEARNING),
    ):
        loop = loops_by_id.get(loop_id)
        if loop is None:
            continue
        assert loop.plane == expected_plane, (
            f"Loop {loop_id} has wrong plane: {loop.plane} (expected {expected_plane})"
        )


# ---------------------------------------------------------------------------
# Mode-selected loops resolve correct stage identity
# ---------------------------------------------------------------------------


def test_shipped_modes_select_loops_with_correct_plane_affiliation() -> None:
    """Shipped modes must select loops whose planes match the mode's plane set."""
    from millrace_ai.assets.loop_graphs import discover_graph_loop_definitions
    from millrace_ai.assets.modes import SHIPPED_MODE_IDS, load_builtin_mode_definition

    loops = discover_graph_loop_definitions()
    loops_by_id = {loop_def.loop_id: loop_def for loop_def in loops}

    for mode_id in SHIPPED_MODE_IDS:
        mode = load_builtin_mode_definition(mode_id)
        loops_by_plane = mode.loop_ids_by_plane or {}

        for plane_key, loop_id in loops_by_plane.items():
            loop = loops_by_id.get(loop_id)
            assert loop is not None, (
                f"Mode {mode_id} selects unknown loop {loop_id!r} "
                f"for plane {plane_key!r}"
            )
            assert loop.plane.value == plane_key, (
                f"Mode {mode_id} selects loop {loop_id!r} "
                f"({loop.plane.value}) for plane {plane_key!r} — mismatch"
            )


def test_compiled_plan_nodes_carry_correct_plane_from_mode_and_loop() -> None:
    """Integration proof: compile a shipped mode and verify node planes."""
    import tempfile

    from millrace_ai.compiler import compile_and_persist_workspace_plan
    from millrace_ai.config.loading import load_runtime_config
    from millrace_ai.paths import bootstrap_workspace, workspace_paths

    assets_root = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "workspace"
        ws.mkdir(parents=True)
        (ws / "millrace.toml").write_text(
            "[runtime]\ndefault_mode = \"default_codex\"\n", encoding="utf-8"
        )
        paths = bootstrap_workspace(workspace_paths(ws), assets_root=assets_root)

        outcome = compile_and_persist_workspace_plan(
            paths,
            config=load_runtime_config(ws / "millrace.toml"),
            requested_mode_id="default_codex",
            assets_root=assets_root,
            compile_if_needed=True,
            refuse_stale_last_known_good=False,
        )

        assert outcome.active_plan is not None, (
            f"default_codex should compile. Errors: {outcome.diagnostics.errors}"
        )

        plan = outcome.active_plan

        # Execution nodes must be on execution plane
        for node in plan.execution_graph.nodes:
            assert node.plane == Plane.EXECUTION, (
                f"Execution graph node {node.node_id} has plane {node.plane!r}"
            )

        # Planning nodes must be on planning plane
        for node in plan.planning_graph.nodes:
            assert node.plane == Plane.PLANNING, (
                f"Planning graph node {node.node_id} has plane {node.plane!r}"
            )

        # No learning nodes in default_codex mode
        assert plan.learning_graph is None, (
            "default_codex should have no learning graph"
        )
