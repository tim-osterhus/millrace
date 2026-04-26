from __future__ import annotations

import json
from pathlib import Path

import pytest

from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.errors import RuntimeLifecycleError
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _unused_stage_runner(request: StageRunRequest) -> RunnerRawResult:
    raise AssertionError(
        f"stage runner should not be called in single-compiled-plan tests: {request.stage.value}"
    )


def test_runtime_startup_uses_single_compiled_plan_object(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)

    engine.startup()

    assert engine.compiled_plan is not None
    assert engine.compiled_plan.execution_graph.loop_id == "execution.standard"
    assert engine.compiled_plan.planning_graph.loop_id == "planning.standard"
    assert not hasattr(engine, "compiled_graph_plan")


def test_runtime_startup_reuses_current_compiled_plan_without_recompiling(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    outcome = compile_and_persist_workspace_plan(
        paths,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=paths.runtime_root,
    )
    assert outcome.active_plan is not None

    compiled_plan_path = paths.state_dir / "compiled_plan.json"
    diagnostics_path = paths.state_dir / "compile_diagnostics.json"
    compiled_before = compiled_plan_path.read_bytes()
    diagnostics_before = diagnostics_path.read_bytes()

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    assert compiled_plan_path.read_bytes() == compiled_before
    assert diagnostics_path.read_bytes() == diagnostics_before
    assert engine.compiled_plan is not None
    assert engine.compiled_plan.compiled_plan_id == outcome.active_plan.compiled_plan_id


def test_runtime_startup_refuses_stale_last_known_good_plan_when_inputs_drift(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    outcome = compile_and_persist_workspace_plan(
        paths,
        config=RuntimeConfig(),
        requested_mode_id="default_codex",
        assets_root=paths.runtime_root,
    )
    assert outcome.active_plan is not None

    mode_path = paths.runtime_root / "modes" / "default_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["loop_ids_by_plane"]["planning"] = "planning.unknown"
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    with pytest.raises(RuntimeLifecycleError, match="Unknown graph loop id: planning.unknown"):
        engine.startup()
