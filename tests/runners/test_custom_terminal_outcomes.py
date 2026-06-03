from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from millrace_ai.contracts import ResultClass
from millrace_ai.runner import RunnerRawResult, StageRunRequest, normalize_stage_result

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def test_normalization_accepts_custom_graph_terminal_outcome(tmp_path: Path) -> None:
    request = _request(tmp_path)
    stdout_path = tmp_path / "stdout.txt"
    stdout_path.write_text("work log\n### SYNTHETIC_2_COMPLETE\n", encoding="utf-8")

    envelope = normalize_stage_result(request, _raw(request, stdout_path))

    assert envelope.terminal_result.value == "SYNTHETIC_2_COMPLETE"
    assert envelope.summary_status_marker == "### SYNTHETIC_2_COMPLETE"
    assert envelope.result_class is ResultClass.SUCCESS
    assert envelope.success is True
    assert envelope.metadata["valid_terminal_result"] is True


def _request(tmp_path: Path) -> StageRunRequest:
    return StageRunRequest(
        request_id="req-001",
        run_id="run-001",
        plane="execution",
        stage="builder",
        mode_id="learning_codex",
        compiled_plan_id="plan-001",
        node_id="synthetic_2",
        stage_kind_id="synthetic_builder",
        legal_terminal_markers=("### SYNTHETIC_2_COMPLETE",),
        allowed_result_classes_by_outcome={
            "SYNTHETIC_2_COMPLETE": (ResultClass.SUCCESS,),
        },
        entrypoint_path="millrace-agents/entrypoints/execution/builder.md",
        active_work_item_kind="task",
        active_work_item_id="task-001",
        active_work_item_path="millrace-agents/tasks/active/task-001.md",
        run_dir=str(tmp_path),
        summary_status_path=str(tmp_path / "execution_status.md"),
        runtime_snapshot_path=str(tmp_path / "runtime_snapshot.json"),
        recovery_counters_path=str(tmp_path / "recovery_counters.json"),
        runner_name="unit-runner",
    )


def _raw(request: StageRunRequest, stdout_path: Path) -> RunnerRawResult:
    return RunnerRawResult(
        request_id=request.request_id,
        run_id=request.run_id,
        stage=request.stage,
        runner_name="unit-runner",
        exit_kind="completed",
        exit_code=0,
        stdout_path=str(stdout_path),
        started_at=NOW,
        ended_at=NOW + timedelta(seconds=1),
    )
