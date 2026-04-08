"""GoalSpec delivery-integrity helpers for stalled or recycling post-spec families."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ..markdown import write_text_atomic
from ..paths import RuntimePaths
from .parser_helpers import _parse_simple_frontmatter
from .path_helpers import _relative_path, _resolve_path_token
from .queues import ResearchQueueDiscovery, discover_research_queues
from .specs import GoalSpecFamilySpecState, load_goal_spec_family_state
from .state import ResearchQueueFamily
from .taskaudit import TaskauditRecord

if TYPE_CHECKING:
    from .governance import GoalSpecDeliveryIntegrityReport


_EARLIER_STAGE_ENTRY_NODE_IDS = frozenset({"goal_intake", "objective_profile_sync"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _delivery_integrity_state_path(paths: RuntimePaths) -> Path:
    return paths.goalspec_runtime_dir / "delivery_integrity_state.json"


def _delivery_integrity_report_path(paths: RuntimePaths) -> Path:
    return paths.tmp_dir / "goalspec_delivery_integrity_report.json"


def _normalize_optional_text(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.strip().split())


def _normalized_token(value: str | None) -> str:
    return _normalize_optional_text(value).casefold()


def _spec_has_emitted_artifacts(spec_state: GoalSpecFamilySpecState) -> bool:
    return spec_state.status in {"emitted", "reviewed", "decomposed"} or any(
        (
            spec_state.queue_path,
            spec_state.reviewed_path,
            spec_state.archived_path,
            spec_state.stable_spec_paths,
            spec_state.pending_shard_path,
        )
    )


def _queue_item_goal_id(item_path: Path | None) -> str:
    if item_path is None or not item_path.exists():
        return ""
    frontmatter = _parse_simple_frontmatter(item_path.read_text(encoding="utf-8", errors="replace"))
    return _normalize_optional_text(
        frontmatter.get("idea_id")
        or frontmatter.get("goal_id")
        or frontmatter.get("id")
        or item_path.stem.split("__", 1)[0]
    )


def _goalspec_first_item(queue_discovery: ResearchQueueDiscovery):
    try:
        return queue_discovery.family_scan(ResearchQueueFamily.GOALSPEC).first_item
    except KeyError:
        return None


def _matching_merged_taskaudit_record_path(paths: RuntimePaths) -> Path | None:
    taskaudit_dir = paths.goalspec_runtime_dir / "taskaudit"
    if not taskaudit_dir.exists():
        return None
    family_state_path = _relative_path(paths.goal_spec_family_state_file, relative_to=paths.root)
    matches: list[Path] = []
    for record_path in taskaudit_dir.glob("*.json"):
        try:
            record = TaskauditRecord.model_validate_json(record_path.read_text(encoding="utf-8"))
        except (ValidationError, ValueError):
            continue
        if record.status == "merged" and record.family_state_path == family_state_path:
            matches.append(record_path)
    if not matches:
        return None
    matches.sort(key=lambda path: path.stat().st_mtime_ns)
    return matches[-1]


def evaluate_goalspec_delivery_integrity(
    *,
    paths: RuntimePaths,
    queue_discovery: ResearchQueueDiscovery | None = None,
    entry_node_id: str | None = None,
    observed_at: datetime | None = None,
):
    """Evaluate one GoalSpec delivery-integrity snapshot without mutating runtime state."""

    from .governance import GoalSpecDeliveryIntegrityReport

    observed_at = observed_at or _utcnow()
    report = GoalSpecDeliveryIntegrityReport(
        updated_at=observed_at,
        report_path=_relative_path(_delivery_integrity_report_path(paths), relative_to=paths.root),
        state_path=_relative_path(_delivery_integrity_state_path(paths), relative_to=paths.root),
        spec_family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
        reason="no-goalspec-family-state",
    )
    if not paths.goal_spec_family_state_file.exists():
        return report

    family_state = load_goal_spec_family_state(paths.goal_spec_family_state_file)
    emitted_spec_ids = tuple(
        spec_id
        for spec_id in family_state.spec_order
        if (spec_state := family_state.specs.get(spec_id)) is not None and _spec_has_emitted_artifacts(spec_state)
    )
    report = report.model_copy(
        update={
            "goal_id": family_state.goal_id,
            "active_spec_id": family_state.active_spec_id,
            "emitted_spec_ids": emitted_spec_ids,
        }
    )
    if not emitted_spec_ids:
        return report.model_copy(update={"reason": "goalspec-family-has-not-emitted-specs"})

    queue_discovery = queue_discovery or discover_research_queues(paths)
    first_item = _goalspec_first_item(queue_discovery)
    pending_shard_count = 0
    for spec_id in family_state.spec_order:
        spec_state = family_state.specs.get(spec_id)
        if spec_state is None or not spec_state.pending_shard_path:
            continue
        shard_path = _resolve_path_token(spec_state.pending_shard_path, relative_to=paths.root)
        if shard_path.exists():
            pending_shard_count += 1
    taskaudit_record_path = _matching_merged_taskaudit_record_path(paths)
    queue_goal_id = _queue_item_goal_id(None if first_item is None else first_item.item_path)
    queue_item_path = ""
    queue_path = ""
    if first_item is not None:
        if first_item.item_path is not None:
            queue_item_path = _relative_path(first_item.item_path, relative_to=paths.root)
        queue_path = _relative_path(first_item.queue_path, relative_to=paths.root)

    report = report.model_copy(
        update={
            "pending_shard_count": pending_shard_count,
            "merged_backlog_handoff": taskaudit_record_path is not None,
            "queue_item_path": queue_item_path,
            "queue_path": queue_path,
            "queue_goal_id": queue_goal_id,
            "entry_node_id": _normalize_optional_text(entry_node_id),
        }
    )

    same_goal_item = (
        first_item is not None
        and _normalized_token(queue_goal_id) != ""
        and _normalized_token(queue_goal_id) == _normalized_token(family_state.goal_id)
    )
    if same_goal_item and report.entry_node_id in _EARLIER_STAGE_ENTRY_NODE_IDS:
        return report.model_copy(
            update={
                "status": "failed",
                "reason": "same-family-earlier-stage-recycling-after-spec-emission",
                "violation_codes": ("same-family-earlier-stage-recycling",),
            }
        )
    if pending_shard_count > 0:
        return report.model_copy(
            update={
                "status": "healthy",
                "reason": "goalspec-family-pending-shard-handoff-present",
            }
        )
    if taskaudit_record_path is not None:
        return report.model_copy(
            update={
                "status": "healthy",
                "reason": "goalspec-family-merged-backlog-handoff-present",
            }
        )
    if first_item is not None:
        return report.model_copy(
            update={
                "status": "healthy",
                "reason": (
                    "same-family-downstream-goalspec-queue-ready"
                    if same_goal_item
                    else "another-goalspec-queue-item-ready"
                ),
            }
        )
    return report.model_copy(
        update={
            "status": "failed",
            "reason": "emitted-specs-without-queue-or-handoff",
            "violation_codes": ("emitted-specs-without-queue-or-handoff",),
        }
    )


def sync_goalspec_delivery_integrity(
    *,
    paths: RuntimePaths,
    queue_discovery: ResearchQueueDiscovery | None = None,
    entry_node_id: str | None = None,
    observed_at: datetime | None = None,
):
    """Persist one GoalSpec delivery-integrity report and state snapshot."""

    from .governance import GoalSpecDeliveryIntegrityState

    report = evaluate_goalspec_delivery_integrity(
        paths=paths,
        queue_discovery=queue_discovery,
        entry_node_id=entry_node_id,
        observed_at=observed_at,
    )
    state = GoalSpecDeliveryIntegrityState(
        updated_at=report.updated_at,
        status=report.status,
        reason=report.reason,
        goal_id=report.goal_id,
        active_spec_id=report.active_spec_id,
        emitted_spec_ids=report.emitted_spec_ids,
        pending_shard_count=report.pending_shard_count,
        merged_backlog_handoff=report.merged_backlog_handoff,
        violation_codes=report.violation_codes,
    )
    state_path = _delivery_integrity_state_path(paths)
    report_path = _delivery_integrity_report_path(paths)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(state_path, state.model_dump_json(indent=2) + "\n")
    write_text_atomic(report_path, report.model_dump_json(indent=2) + "\n")
    return report


def delivery_integrity_error_message(report: "GoalSpecDeliveryIntegrityReport") -> str:
    """Render one stable hard-failure message for a failed delivery-integrity report."""

    goal_label = report.goal_id or "active GoalSpec family"
    if report.reason == "same-family-earlier-stage-recycling-after-spec-emission":
        stage_label = report.entry_node_id or "an earlier stage"
        queue_label = report.queue_item_path or report.queue_path or "an earlier-stage GoalSpec queue item"
        return (
            f"GoalSpec delivery integrity failed for {goal_label}: emitted specs recycled into {stage_label} "
            f"from {queue_label} without a valid pending shard or merged backlog handoff; "
            f"diagnostic: {report.report_path}"
        )
    if report.reason == "emitted-specs-without-queue-or-handoff":
        return (
            f"GoalSpec delivery integrity failed for {goal_label}: emitted specs have no active GoalSpec queue item, "
            f"pending shard, or merged Taskaudit backlog handoff; diagnostic: {report.report_path}"
        )
    return f"GoalSpec delivery integrity failed for {goal_label}: {report.reason}; diagnostic: {report.report_path}"


__all__ = [
    "delivery_integrity_error_message",
    "evaluate_goalspec_delivery_integrity",
    "sync_goalspec_delivery_integrity",
]
