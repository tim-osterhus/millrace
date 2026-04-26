"""Persisted stage-result outputs and plane status markers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.contracts import StageResultEnvelope
from millrace_ai.runners import StageRunRequest

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine


def write_stage_result(
    engine: RuntimeEngine,
    request: StageRunRequest,
    stage_result: StageResultEnvelope,
) -> Path:
    del engine
    run_dir = Path(request.run_dir)
    stage_result_dir = run_dir / "stage_results"
    stage_result_dir.mkdir(parents=True, exist_ok=True)
    stage_result_path = stage_result_dir / f"{request.request_id}.json"
    stage_result_path.write_text(stage_result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return stage_result_path


def write_plane_status(engine: RuntimeEngine, stage_result: StageResultEnvelope) -> None:
    assert engine.snapshot is not None
    engine._set_plane_status_marker(
        plane=stage_result.plane,
        marker=stage_result.summary_status_marker,
        run_id=stage_result.run_id,
        source="stage_completed",
    )


__all__ = ["write_plane_status", "write_stage_result"]
