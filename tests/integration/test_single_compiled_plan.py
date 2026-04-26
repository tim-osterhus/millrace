from __future__ import annotations

from pathlib import Path

from millrace_ai.architecture import CompiledRunPlan
from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.paths import bootstrap_workspace, workspace_paths


def test_compile_persists_single_canonical_compiled_plan_artifact(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )

    paths = workspace_paths(workspace_root)
    compiled_plan_path = paths.state_dir / "compiled_plan.json"
    compiled_graph_plan_path = paths.state_dir / "compiled_graph_plan.json"

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    assert compiled_plan_path.is_file()
    assert not compiled_graph_plan_path.exists()

    persisted_plan = CompiledRunPlan.model_validate_json(
        compiled_plan_path.read_text(encoding="utf-8")
    )

    assert persisted_plan.mode_id == "default_codex"
    assert persisted_plan.compile_input_fingerprint.mode_id == "default_codex"
    assert persisted_plan.compile_input_fingerprint.config_fingerprint.startswith("cfg-")
    assert persisted_plan.compile_input_fingerprint.assets_fingerprint.startswith("assets-")
    assert persisted_plan.execution_loop_id == "execution.standard"
    assert persisted_plan.planning_loop_id == "planning.standard"
    assert {entry.entry_key.value: entry.node_id for entry in persisted_plan.execution_graph.compiled_entries} == {
        "task": "builder"
    }
    assert {entry.entry_key.value: entry.node_id for entry in persisted_plan.planning_graph.compiled_entries} == {
        "incident": "auditor",
        "spec": "planner",
    }
    assert persisted_plan.planning_graph.compiled_completion_entry is not None
    assert persisted_plan.planning_graph.compiled_completion_entry.node_id == "arbiter"
