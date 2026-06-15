from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from millrace_ai.contracts import (
    ExecutionStageName,
    ExecutionTerminalResult,
    Plane,
    ResultClass,
    StageResultEnvelope,
    WorkItemKind,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.workspace.history_log import append_history_entry_for_stage_result

NOW = datetime(2026, 6, 14, 12, 30, 0, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _stage_result(
    *,
    stage: ExecutionStageName = ExecutionStageName.BUILDER,
    terminal_result: ExecutionTerminalResult = ExecutionTerminalResult.BUILDER_COMPLETE,
    started_at: datetime = NOW,
    completed_at: datetime = NOW,
) -> StageResultEnvelope:
    return StageResultEnvelope(
        run_id="run-history",
        plane=Plane.EXECUTION,
        stage=stage,
        node_id=stage.value,
        stage_kind_id=stage.value,
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-history",
        terminal_result=terminal_result,
        result_class=ResultClass.SUCCESS,
        summary_status_marker=f"### {terminal_result.value}",
        success=True,
        started_at=started_at,
        completed_at=completed_at,
    )


def _write_stage_result_path(paths, stage_result: StageResultEnvelope) -> Path:
    request_id = "request-history"
    if stage_result.stage is ExecutionStageName.CHECKER:
        request_id = "request-checker"
    stage_result_path = paths.runs_dir / stage_result.run_id / "stage_results" / f"{request_id}.json"
    stage_result_path.parent.mkdir(parents=True, exist_ok=True)
    stage_result_path.write_text(stage_result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return stage_result_path


def _write_history_entry(paths, stage_result: StageResultEnvelope, payload: dict[str, object]) -> Path:
    history_path = paths.runs_dir / stage_result.run_id / "history_entry.json"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    timestamp = stage_result.completed_at.timestamp()
    os.utime(history_path, (timestamp, timestamp))
    return history_path


def _claimed_history_entry_path(stage_result_path: Path) -> Path:
    return stage_result_path.with_name(f"{stage_result_path.stem}.history_entry.json")


def _history_payload(*, summary: str = "Implemented runtime-owned history log.") -> dict[str, object]:
    return {
        "schema_version": 1,
        "summary": summary,
        "changed_paths": ["src/millrace_ai/runtime/result_application.py"],
        "evidence_paths": ["millrace-agents/runs/run-history/stage_results/request-history.json"],
        "warnings": [],
    }


def test_history_entry_appends_jsonl_and_renders_markdown(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    stage_result = _stage_result()
    stage_result_path = _write_stage_result_path(paths, stage_result)
    history_path = _write_history_entry(paths, stage_result, _history_payload())

    result = append_history_entry_for_stage_result(paths, stage_result=stage_result, stage_result_path=stage_result_path)

    assert result.status == "appended"
    claimed_path = _claimed_history_entry_path(stage_result_path)
    assert not history_path.exists()
    assert claimed_path.exists()
    entry_path = paths.history_log_entries_dir / "2026-06-14.jsonl"
    entry = json.loads(entry_path.read_text(encoding="utf-8"))
    assert entry["history_entry_id"] == "history:run-history:request-history:execution:builder:BUILDER_COMPLETE"
    assert entry["request_id"] == "request-history"
    assert entry["summary"] == "Implemented runtime-owned history log."
    assert "Implemented runtime-owned history log." in paths.history_log_latest_file.read_text(encoding="utf-8")
    assert "- [2026-06-14](daily/2026-06-14.md) - 1 entry" in paths.history_log_index_file.read_text(encoding="utf-8")


def test_history_entry_duplicate_replay_skips_byte_equivalent_payload(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    stage_result = _stage_result()
    stage_result_path = _write_stage_result_path(paths, stage_result)
    _write_history_entry(paths, stage_result, _history_payload())
    first = append_history_entry_for_stage_result(paths, stage_result=stage_result, stage_result_path=stage_result_path)
    before = first.entry_path.read_text(encoding="utf-8") if first.entry_path is not None else ""

    second = append_history_entry_for_stage_result(paths, stage_result=stage_result, stage_result_path=stage_result_path)

    assert second.status == "duplicate_skipped"
    assert first.entry_path is not None
    assert first.entry_path.read_text(encoding="utf-8") == before


def test_history_entry_conflict_skips_changed_payload_for_same_identity(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    stage_result = _stage_result()
    stage_result_path = _write_stage_result_path(paths, stage_result)
    _write_history_entry(paths, stage_result, _history_payload())
    first = append_history_entry_for_stage_result(paths, stage_result=stage_result, stage_result_path=stage_result_path)
    before = first.entry_path.read_text(encoding="utf-8") if first.entry_path is not None else ""
    _write_history_entry(paths, stage_result, _history_payload(summary="Changed summary."))

    second = append_history_entry_for_stage_result(paths, stage_result=stage_result, stage_result_path=stage_result_path)

    assert second.status == "conflict_skipped"
    assert first.entry_path is not None
    assert first.entry_path.read_text(encoding="utf-8") == before


def test_history_entry_rejects_conflicting_runtime_authored_identity(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    stage_result = _stage_result()
    stage_result_path = _write_stage_result_path(paths, stage_result)
    payload = _history_payload()
    payload["run_id"] = "stage-authored-run"
    _write_history_entry(paths, stage_result, payload)

    result = append_history_entry_for_stage_result(paths, stage_result=stage_result, stage_result_path=stage_result_path)

    assert result.status == "invalid_skipped"
    assert result.diagnostic is not None
    assert "run_id" in result.diagnostic
    assert not (paths.history_log_entries_dir / "2026-06-14.jsonl").exists()


def test_history_entry_rejects_paths_that_escape_workspace(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    stage_result = _stage_result()
    stage_result_path = _write_stage_result_path(paths, stage_result)
    payload = _history_payload()
    payload["evidence_paths"] = ["../outside.json"]
    _write_history_entry(paths, stage_result, payload)

    result = append_history_entry_for_stage_result(paths, stage_result=stage_result, stage_result_path=stage_result_path)

    assert result.status == "invalid_skipped"
    assert result.diagnostic is not None
    assert "evidence_paths" in result.diagnostic


def test_history_entry_rejects_stale_run_root_history_entry_for_later_stage(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    builder_result = _stage_result()
    builder_stage_result_path = _write_stage_result_path(paths, builder_result)
    builder_history_path = _write_history_entry(paths, builder_result, _history_payload())
    first = append_history_entry_for_stage_result(
        paths,
        stage_result=builder_result,
        stage_result_path=builder_stage_result_path,
    )
    assert first.status == "appended"
    assert not builder_history_path.exists()
    assert _claimed_history_entry_path(builder_stage_result_path).exists()

    checker_result = _stage_result(
        stage=ExecutionStageName.CHECKER,
        terminal_result=ExecutionTerminalResult.CHECKER_PASS,
        started_at=NOW + timedelta(seconds=1),
        completed_at=NOW + timedelta(seconds=1),
    )
    checker_stage_result_path = _write_stage_result_path(paths, checker_result)

    result = append_history_entry_for_stage_result(
        paths,
        stage_result=checker_result,
        stage_result_path=checker_stage_result_path,
    )

    assert result.status == "missing"
    entry_path = paths.history_log_entries_dir / "2026-06-14.jsonl"
    lines = entry_path.read_text(encoding="utf-8").splitlines()
    assert len([line for line in lines if line.strip()]) == 1


def test_history_entry_ignores_absolute_external_artifact_path(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    stage_result = _stage_result()
    stage_result_path = _write_stage_result_path(paths, stage_result)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    external_history = outside_dir / "history_entry.json"
    external_history.write_text(json.dumps(_history_payload(), sort_keys=True) + "\n", encoding="utf-8")
    timestamp = stage_result.completed_at.timestamp()
    os.utime(external_history, (timestamp, timestamp))
    stage_result = stage_result.model_copy(update={"artifact_paths": (str(external_history),)})

    result = append_history_entry_for_stage_result(paths, stage_result=stage_result, stage_result_path=stage_result_path)

    assert result.status == "missing"
    assert not (paths.history_log_entries_dir / "2026-06-14.jsonl").exists()


def test_history_entry_ignores_relative_escape_artifact_path(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    stage_result = _stage_result()
    stage_result_path = _write_stage_result_path(paths, stage_result)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    external_history = outside_dir / "history_entry.json"
    external_history.write_text(json.dumps(_history_payload(), sort_keys=True) + "\n", encoding="utf-8")
    timestamp = stage_result.completed_at.timestamp()
    os.utime(external_history, (timestamp, timestamp))
    stage_result = stage_result.model_copy(update={"artifact_paths": ("../outside/history_entry.json",)})

    result = append_history_entry_for_stage_result(paths, stage_result=stage_result, stage_result_path=stage_result_path)

    assert result.status == "missing"
    assert not (paths.history_log_entries_dir / "2026-06-14.jsonl").exists()
