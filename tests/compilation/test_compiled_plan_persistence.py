from __future__ import annotations

import json
from pathlib import Path

from millrace_ai.compilation.persistence import load_existing_plan
from millrace_ai.compiler import compile_and_persist_workspace_plan, inspect_workspace_plan_currentness
from millrace_ai.config import RuntimeConfig
from millrace_ai.paths import bootstrap_workspace, workspace_paths


def test_load_existing_plan_rejects_plan_missing_runtime_stage(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="blueprint_" "codex",
    )
    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    compiled_plan_path = workspace_paths(workspace_root).state_dir / "compiled_plan.json"
    payload = json.loads(compiled_plan_path.read_text(encoding="utf-8"))
    for graph_key in ("planning_graph",):
        for node in payload[graph_key]["nodes"]:
            if node["stage_kind_id"] == "manager_blueprint":
                node.pop("runtime_stage", None)
    for node in payload["graphs_by_plane"]["planning"]["nodes"]:
        if node["stage_kind_id"] == "manager_blueprint":
            node.pop("runtime_stage", None)
    compiled_plan_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    assert load_existing_plan(compiled_plan_path) is None

    currentness = inspect_workspace_plan_currentness(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="blueprint_" "codex",
    )
    assert currentness.state == "missing"
    assert currentness.persisted_plan_id is None


def test_load_existing_plan_rejects_plan_missing_terminal_action_id(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    compiled_plan_path = workspace_paths(workspace_root).state_dir / "compiled_plan.json"
    payload = json.loads(compiled_plan_path.read_text(encoding="utf-8"))
    payload["execution_graph"]["terminal_states"][0].pop("terminal_action_id", None)
    payload["graphs_by_plane"]["execution"]["terminal_states"][0].pop("terminal_action_id", None)
    compiled_plan_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    assert load_existing_plan(compiled_plan_path) is None

    currentness = inspect_workspace_plan_currentness(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert currentness.state == "missing"
    assert currentness.persisted_plan_id is None
