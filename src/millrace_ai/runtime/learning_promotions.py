"""Learning Curator promotion safe-boundary handling."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.contracts import LearningStageName, LearningTerminalResult, Plane, StageResultEnvelope
from millrace_ai.events import write_runtime_event

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine


def handle_learning_curator_promotion_boundary(
    engine: RuntimeEngine,
    *,
    stage_result: StageResultEnvelope,
) -> None:
    """Stage or apply Curator promotion artifacts at a foreground-safe boundary."""

    artifacts = _promotion_artifacts(stage_result)
    if not artifacts:
        return
    if _foreground_active_planes(engine):
        _write_promotion_record(
            engine,
            stage_result=stage_result,
            artifacts=artifacts,
            state="deferred",
        )
        return
    _write_promotion_record(
        engine,
        stage_result=stage_result,
        artifacts=artifacts,
        state="applied",
    )


def apply_deferred_learning_promotions_if_safe(engine: RuntimeEngine) -> int:
    """Mark deferred Curator promotion records applied when foreground lanes drain."""

    if _foreground_active_planes(engine):
        return 0
    deferred_dir = _promotion_dir(engine, "deferred")
    applied_dir = _promotion_dir(engine, "applied")
    applied_count = 0
    for path in sorted(deferred_dir.glob("*.json")):
        target = applied_dir / path.name
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload["state"] = "applied"
            payload["applied_at"] = engine._now().isoformat()
            target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        path.unlink()
        applied_count += 1
        write_runtime_event(
            engine.paths,
            event_type="learning_curator_promotion_applied",
            data={
                "promotion_record_path": str(target.relative_to(engine.paths.root)),
                "source": "deferred_safe_boundary",
            },
        )
        engine._emit_monitor_event(
            "learning_curator_promotion_applied",
            promotion_record_path=str(target.relative_to(engine.paths.root)),
            source="deferred_safe_boundary",
        )
    return applied_count


def _promotion_artifacts(stage_result: StageResultEnvelope) -> tuple[str, ...]:
    if stage_result.plane is not Plane.LEARNING:
        return ()
    if stage_result.stage is not LearningStageName.CURATOR:
        return ()
    if stage_result.terminal_result is not LearningTerminalResult.CURATOR_COMPLETE:
        return ()
    return tuple(
        artifact
        for artifact in stage_result.artifact_paths
        if _is_skill_update_artifact(artifact)
    )


def _is_skill_update_artifact(raw_path: str) -> bool:
    name = Path(raw_path).name.lower()
    return "skill_update" in name or "skill-update" in name


def _foreground_active_planes(engine: RuntimeEngine) -> tuple[Plane, ...]:
    assert engine.snapshot is not None
    return tuple(
        plane
        for plane in (Plane.PLANNING, Plane.EXECUTION)
        if plane in engine.snapshot.active_runs_by_plane
    )


def _write_promotion_record(
    engine: RuntimeEngine,
    *,
    stage_result: StageResultEnvelope,
    artifacts: tuple[str, ...],
    state: str,
) -> Path:
    directory = _promotion_dir(engine, state)
    record_path = directory / f"{stage_result.run_id}-{stage_result.work_item_id}.json"
    payload = {
        "schema_version": "1.0",
        "kind": "learning_curator_promotion",
        "state": state,
        "run_id": stage_result.run_id,
        "work_item_id": stage_result.work_item_id,
        "artifact_paths": list(artifacts),
        "foreground_active_planes": [plane.value for plane in _foreground_active_planes(engine)],
        "recorded_at": engine._now().isoformat(),
    }
    record_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    event_type = (
        "learning_curator_promotion_deferred"
        if state == "deferred"
        else "learning_curator_promotion_applied"
    )
    write_runtime_event(
        engine.paths,
        event_type=event_type,
        data={
            "promotion_record_path": str(record_path.relative_to(engine.paths.root)),
            "work_item_id": stage_result.work_item_id,
            "artifact_paths": list(artifacts),
            "foreground_active_planes": [plane.value for plane in _foreground_active_planes(engine)],
        },
    )
    engine._emit_monitor_event(
        event_type,
        promotion_record_path=str(record_path.relative_to(engine.paths.root)),
        work_item_id=stage_result.work_item_id,
        artifact_paths=list(artifacts),
        foreground_active_planes=[plane.value for plane in _foreground_active_planes(engine)],
    )
    return record_path


def _promotion_dir(engine: RuntimeEngine, state: str) -> Path:
    directory = engine.paths.learning_update_candidates_dir / state
    directory.mkdir(parents=True, exist_ok=True)
    return directory


__all__ = [
    "apply_deferred_learning_promotions_if_safe",
    "handle_learning_curator_promotion_boundary",
]
