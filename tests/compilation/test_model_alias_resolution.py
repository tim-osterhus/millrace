from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.architecture import CompiledRunPlan, MaterializedGraphNodePlan
from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import CompileDiagnostics, Plane
from millrace_ai.paths import bootstrap_workspace, workspace_paths

NOW = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)


def test_stage_alias_overrides_stage_config(tmp_path: Path) -> None:
    plan = _compile_with(
        tmp_path,
        config={
            "stages": {"builder": {"model": "old-model", "thinking_level": "low"}},
            "model_assignment": {"by_stage": {"builder": "deep"}},
        },
    )

    builder = _node(plan, Plane.EXECUTION, "lad_builder")

    assert builder.model_name == "gpt-5.5"
    assert builder.thinking_level == "xhigh"
    assert builder.model_reasoning_effort == "xhigh"
    assert builder.model_assignment_alias_id == "deep"
    assert builder.model_assignment_source == "stage:builder"


def test_loop_alias_applies_to_every_node_in_loop(tmp_path: Path) -> None:
    plan = _compile_with(
        tmp_path,
        config={"model_assignment": {"by_loop": {"planning.blueprint": "deep"}}},
        mode_id="blueprint_" "codex",
    )

    for node in plan.planning_graph.nodes:
        assert node.model_assignment_alias_id == "deep"
        assert node.model_assignment_source == "loop:planning.blueprint"


def test_unknown_stage_alias_warns_and_falls_back_to_loop_alias(tmp_path: Path) -> None:
    plan, diagnostics = _compile_with_diagnostics(
        tmp_path,
        config={
            "model_assignment": {
                "by_stage": {"contractor_blueprint": "missing"},
                "by_loop": {"planning.blueprint": "fast"},
            }
        },
        mode_id="blueprint_" "codex",
    )

    contractor = _node(plan, Plane.PLANNING, "contractor_blueprint")

    assert contractor.model_assignment_alias_id == "fast"
    assert contractor.model_assignment_source == "loop:planning.blueprint"
    assert any(
        "model assignment stage:contractor_blueprint references unknown alias 'missing'; "
        "falling back" in warning
        for warning in diagnostics.warnings
    )


def test_invalid_global_alias_warns_and_falls_back_to_builtin_standard(tmp_path: Path) -> None:
    plan, diagnostics = _compile_with_diagnostics(
        tmp_path,
        config={
            "model_aliases": {"broken": {"model": "", "thinking_level": ""}},
            "model_assignment": {"default_alias": "broken"},
        }
    )

    builder = _node(plan, Plane.EXECUTION, "lad_builder")

    assert builder.model_assignment_alias_id == "standard"
    assert builder.model_name == "gpt-5.5"
    assert builder.thinking_level == "medium"
    assert any(
        "model assignment default selected invalid alias 'broken': model is empty or "
        "contains unsupported characters; falling back" in warning
        for warning in diagnostics.warnings
    )


def test_alias_values_are_trimmed_before_materialization(tmp_path: Path) -> None:
    plan = _compile_with(
        tmp_path,
        config={
            "model_aliases": {
                "trimmed": {"model": " gpt-5.5 ", "thinking_level": " high "},
            },
            "model_assignment": {"default_alias": "trimmed"},
        },
    )

    builder = _node(plan, Plane.EXECUTION, "lad_builder")

    assert builder.model_name == "gpt-5.5"
    assert builder.thinking_level == "high"
    assert builder.model_reasoning_effort == "high"


def _compile_with(
    tmp_path: Path,
    *,
    config: dict,
    mode_id: str = "default_codex",
) -> CompiledRunPlan:
    plan, _diagnostics = _compile_with_diagnostics(tmp_path, config=config, mode_id=mode_id)
    return plan


def _compile_with_diagnostics(
    tmp_path: Path,
    *,
    config: dict,
    mode_id: str = "default_codex",
) -> tuple[CompiledRunPlan, CompileDiagnostics]:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    outcome = compile_and_persist_workspace_plan(
        paths,
        config=RuntimeConfig.model_validate(config),
        requested_mode_id=mode_id,
        now=NOW,
    )
    assert outcome.active_plan is not None
    assert outcome.diagnostics.ok is True
    return outcome.active_plan, outcome.diagnostics


def _node(
    plan: CompiledRunPlan,
    plane: Plane,
    stage_kind_id: str,
) -> MaterializedGraphNodePlan:
    graph = plan.graphs_by_plane[plane]
    for node in graph.nodes:
        if node.stage_kind_id == stage_kind_id:
            return node
    raise AssertionError(f"missing {plane.value} node with stage_kind_id={stage_kind_id}")
