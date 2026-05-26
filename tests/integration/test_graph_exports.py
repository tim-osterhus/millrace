from __future__ import annotations

from pathlib import Path

from millrace_ai.compilation.graph_exports import export_compiled_stage_graphs
from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import Plane
from millrace_ai.contracts.graph_exports import CompiledStageGraphExport
from millrace_ai.paths import bootstrap_workspace


def test_export_compiled_stage_graphs_projects_default_mode_graphs(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="default_codex",
    )
    assert outcome.active_plan is not None

    exports = export_compiled_stage_graphs(outcome.active_plan)

    assert [export.plane for export in exports] == [Plane.EXECUTION, Plane.PLANNING]
    execution = next(export for export in exports if export.plane is Plane.EXECUTION)
    assert execution.kind == "compiled_stage_graph"
    assert execution.compiled_plan_id == outcome.active_plan.compiled_plan_id
    assert execution.mode_id == "default_codex"
    assert execution.loop_id == "execution.standard"
    assert execution.runtime_failure_recovery is not None
    assert execution.runtime_failure_recovery.default_repair_node_id == "troubleshooter"
    assert execution.runtime_failure_recovery.counter_name == "troubleshoot_attempt_count"
    assert {entry.entry_key: entry.node_id for entry in execution.entries} == {
        "task": "builder",
    }
    assert "builder" in {node.node_id for node in execution.nodes}
    assert "update_complete" in {
        state.terminal_state_id for state in execution.terminal_states
    }
    assert any(
        edge.source_node_id == "builder"
        and edge.outcome == "BUILDER_COMPLETE"
        and edge.target_node_id == "checker"
        for edge in execution.edges
    )

    round_tripped = CompiledStageGraphExport.model_validate_json(
        execution.model_dump_json()
    )
    assert round_tripped == execution


def test_export_compiled_stage_graphs_includes_learning_graph_for_learning_mode(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="learning_codex",
    )
    assert outcome.active_plan is not None

    exports = export_compiled_stage_graphs(outcome.active_plan)

    assert [export.plane for export in exports] == [
        Plane.EXECUTION,
        Plane.LEARNING,
        Plane.PLANNING,
    ]
    learning = next(export for export in exports if export.plane is Plane.LEARNING)
    assert learning.loop_id == "learning.standard"
    assert learning.runtime_failure_recovery is None
    assert {entry.entry_key: entry.node_id for entry in learning.entries} == {
        "learning_request": "analyst",
    }
    assert any(
        edge.source_node_id == "analyst"
        and edge.outcome == "ANALYST_COMPLETE"
        and edge.target_node_id == "professor"
        for edge in learning.edges
    )
