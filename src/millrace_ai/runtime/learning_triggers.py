"""Runtime evaluation for compiler-frozen learning trigger rules."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import JsonValue

from millrace_ai.contracts import LearningRequestDocument, StageResultEnvelope
from millrace_ai.events import write_runtime_event
from millrace_ai.queue_store import QueueStore

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine


def enqueue_learning_requests_for_stage_result(
    engine: RuntimeEngine,
    *,
    stage_result: StageResultEnvelope,
    stage_result_path: Path,
) -> tuple[Path, ...]:
    """Evaluate frozen learning rules and enqueue matching learning requests."""

    assert engine.compiled_plan is not None
    queued_paths: list[Path] = []
    if engine.compiled_plan.learning_graph is None:
        return ()

    queue = QueueStore(engine.paths)
    for rule in engine.compiled_plan.learning_trigger_rules:
        if rule.source_plane is not stage_result.plane:
            continue
        if rule.source_stage != stage_result.stage:
            continue
        if stage_result.terminal_result.value not in rule.on_terminal_results:
            continue

        document = LearningRequestDocument(
            learning_request_id=f"learn-{uuid4().hex[:12]}",
            title=f"Learn from {stage_result.node_id} {stage_result.terminal_result.value}",
            summary=(
                "Runtime-generated learning request from a compiler-frozen "
                f"trigger rule: {rule.rule_id}"
            ),
            requested_action=rule.requested_action,
            target_skill_id=rule.target_skill_id,
            target_stage=rule.target_stage,
            source_refs=(
                f"run:{stage_result.run_id}",
                f"request:{_string_metadata(stage_result, 'request_id') or 'unknown'}",
                f"node:{stage_result.node_id}",
                f"stage_kind:{stage_result.stage_kind_id}",
                f"stage:{stage_result.stage.value}",
                f"terminal:{stage_result.terminal_result.value}",
            ),
            trigger_metadata=_trigger_metadata(rule_id=rule.rule_id, stage_result=stage_result),
            originating_run_ids=(stage_result.run_id,),
            artifact_paths=_learning_artifact_paths(stage_result_path, stage_result),
            preferred_output_paths=rule.preferred_output_paths,
            created_at=engine._now(),
            created_by="millrace runtime",
        )
        queued_path = queue.enqueue_learning_request(document)
        queued_paths.append(queued_path)
        write_runtime_event(
            engine.paths,
            event_type="learning_request_enqueued",
            data={
                "learning_request_id": document.learning_request_id,
                "rule_id": rule.rule_id,
                "source_plane": stage_result.plane.value,
                "source_stage": stage_result.stage.value,
                "source_node_id": stage_result.node_id,
                "source_stage_kind_id": stage_result.stage_kind_id,
                "terminal_result": stage_result.terminal_result.value,
                "target_stage": rule.target_stage.value,
                "target_skill_id": rule.target_skill_id,
                "preferred_output_paths": list(rule.preferred_output_paths),
                "path": str(queued_path),
            },
        )
    return tuple(queued_paths)


def _trigger_metadata(
    *,
    rule_id: str,
    stage_result: StageResultEnvelope,
) -> dict[str, JsonValue]:
    return {
        "rule_id": rule_id,
        "source_plane": stage_result.plane.value,
        "source_stage": stage_result.stage.value,
        "source_node_id": stage_result.node_id,
        "source_stage_kind_id": stage_result.stage_kind_id,
        "terminal_result": stage_result.terminal_result.value,
        "run_id": stage_result.run_id,
        "work_item_family_id": stage_result.work_item_family_id,
        "work_item_kind": (
            stage_result.work_item_kind.value if stage_result.work_item_kind is not None else None
        ),
        "work_item_id": stage_result.work_item_id,
        "source_work_item_family_id": stage_result.work_item_family_id,
        "source_work_item_kind": (
            stage_result.work_item_kind.value if stage_result.work_item_kind is not None else None
        ),
        "source_work_item_id": stage_result.work_item_id,
        "source_active_work_item_path": _string_metadata(stage_result, "active_work_item_path"),
    }


def _learning_artifact_paths(
    stage_result_path: Path,
    stage_result: StageResultEnvelope,
) -> tuple[str, ...]:
    paths: list[str] = [str(stage_result_path)]
    for artifact_path in stage_result.artifact_paths:
        if artifact_path not in paths:
            paths.append(artifact_path)
    return tuple(paths)


def _string_metadata(stage_result: StageResultEnvelope, key: str) -> str | None:
    value = stage_result.metadata.get(key)
    return value if isinstance(value, str) else None


__all__ = ["enqueue_learning_requests_for_stage_result"]
