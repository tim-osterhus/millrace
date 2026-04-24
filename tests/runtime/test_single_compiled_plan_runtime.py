from __future__ import annotations

from pathlib import Path

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
