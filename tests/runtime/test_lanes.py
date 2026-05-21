from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.contracts import LaneRuntimeState, Plane
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.lanes import compiled_plan_fingerprint_for_runtime
from millrace_ai.state_store import load_snapshot

NOW = datetime(2026, 4, 15, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _unused_stage_runner(request: StageRunRequest) -> RunnerRawResult:
    raise AssertionError(f"stage runner should not be called by lane tests: {request.stage.value}")


def test_lane_runtime_state_records_active_run_and_plan_identity() -> None:
    state = LaneRuntimeState(
        lane_id="execution.main",
        plane=Plane.EXECUTION,
        status="active",
        compiled_plan_id="plan-001",
        compiled_plan_fingerprint="fingerprint-001",
        active_run_ids=("run-001",),
        active_work_refs=("task:task-001",),
        pause_requested=False,
        stop_requested=False,
        drain_requested=False,
        mutation_lock_refs=(),
        completion_target_refs=(),
        failure_counter_refs=(),
        last_claim_attempt_at=None,
        last_terminal_outcome=None,
    )

    round_tripped = LaneRuntimeState.model_validate(state.model_dump(mode="python"))

    assert round_tripped.active_run_ids == ("run-001",)
    assert round_tripped.compiled_plan_id == "plan-001"
    assert round_tripped.compiled_plan_fingerprint == "fingerprint-001"


def test_runtime_startup_initializes_lane_state_from_compiled_plan(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)

    snapshot = engine.startup()
    try:
        assert engine.compiled_plan is not None
        expected_fingerprint = compiled_plan_fingerprint_for_runtime(engine.compiled_plan)

        assert set(snapshot.lanes_by_id) == {"execution.main", "planning.main"}
        assert snapshot.compiled_plan_id == engine.compiled_plan.compiled_plan_id
        assert snapshot.compiled_plan_fingerprint == expected_fingerprint
        assert snapshot.lanes_by_id["execution.main"].plane is Plane.EXECUTION
        assert snapshot.lanes_by_id["execution.main"].compiled_plan_id == snapshot.compiled_plan_id
        assert snapshot.lanes_by_id["execution.main"].compiled_plan_fingerprint == expected_fingerprint

        persisted = load_snapshot(paths)
        assert persisted.lanes_by_id == snapshot.lanes_by_id
    finally:
        engine.close()
