"""Final-family Taskaudit assembly, backlog merge, and provenance refresh."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from ..contracts import ContractModel, TaskCard, _normalize_datetime
from ..markdown import TaskStoreDocument, parse_task_store, render_task_store, write_text_atomic
from ..paths import RuntimePaths
from ..queue import QueueMergeConflictError, TaskQueue
from .goalspec_helpers import GoalSpecExecutionError
from .normalization_helpers import (
    _normalize_optional_text,
    _normalize_required_text,
    _normalize_token_sequence,
)
from .path_helpers import _normalize_path_token, _relative_path, _resolve_path_token
from .persistence_helpers import _load_json_model, _sha256_text, _write_json_model
from .provenance import (
    TaskauditProvenance,
    refresh_task_provenance_registry,
    task_provenance_source_paths,
)
from .specs import GoalSpecFamilyState, load_goal_spec_family_state

TASKAUDIT_ARTIFACT_SCHEMA_VERSION = "1.0"
_DEFAULT_PENDING_PREAMBLE = "# Tasks Pending"
_DEFAULT_BACKLOG_PREAMBLE = "# Task Backlog"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _read_store_text(path: Path, *, default_preamble: str) -> str:
    if not path.exists():
        return default_preamble + "\n"
    return path.read_text(encoding="utf-8")


def _pending_document(paths: RuntimePaths) -> TaskStoreDocument:
    return parse_task_store(
        _read_store_text(paths.taskspending_file, default_preamble=_DEFAULT_PENDING_PREAMBLE),
        source_file=paths.taskspending_file,
    )


def _backlog_document(paths: RuntimePaths) -> TaskStoreDocument:
    return parse_task_store(
        _read_store_text(paths.backlog_file, default_preamble=_DEFAULT_BACKLOG_PREAMBLE),
        source_file=paths.backlog_file,
    )


def _card_keys(card: TaskCard) -> set[str]:
    keys = {
        card.heading.casefold(),
        card.task_id.casefold(),
        card.title.casefold(),
    }
    if card.spec_id:
        keys.add(card.spec_id.casefold())
    return keys


def _normalized_dependency(depends_on: str) -> str:
    normalized = depends_on.strip().strip("`").casefold()
    if normalized in {"", "none"}:
        return ""
    return normalized


def _validate_dependency_order(
    ordered_backlog_cards: tuple[TaskCard, ...],
    *,
    active_cards: tuple[TaskCard, ...],
    archive_cards: tuple[TaskCard, ...],
) -> None:
    known_dependencies: set[str] = set()
    for card in (*ordered_backlog_cards, *active_cards, *archive_cards):
        known_dependencies.update(_card_keys(card))

    satisfied: set[str] = set()
    for card in (*archive_cards, *active_cards):
        satisfied.update(_card_keys(card))

    for card in ordered_backlog_cards:
        for depends_on in card.depends_on:
            dependency = _normalized_dependency(depends_on)
            if not dependency or dependency not in known_dependencies:
                continue
            if dependency not in satisfied:
                raise TaskauditExecutionError(
                    f"Taskaudit merge would violate dependency ordering for {card.title!r}: {depends_on!r}"
                )
        satisfied.update(_card_keys(card))


def _ordered_shard_paths(
    family_state: GoalSpecFamilyState,
    *,
    paths: RuntimePaths,
) -> tuple[Path, ...]:
    shard_paths: list[Path] = []
    for spec_id in family_state.spec_order:
        spec_state = family_state.specs.get(spec_id)
        if spec_state is None or spec_state.status != "decomposed":
            raise TaskauditExecutionError(f"Taskaudit requires decomposed spec state for {spec_id}")
        if not spec_state.pending_shard_path:
            raise TaskauditExecutionError(f"Taskaudit requires a pending shard path for {spec_id}")
        shard_path = _resolve_path_token(spec_state.pending_shard_path, relative_to=paths.root)
        if not shard_path.exists():
            raise TaskauditExecutionError(f"Taskaudit shard is missing: {shard_path.as_posix()}")
        shard_paths.append(shard_path)
    return tuple(shard_paths)


def _assemble_pending_family(
    paths: RuntimePaths,
    family_state: GoalSpecFamilyState,
) -> tuple[TaskStoreDocument, tuple[Path, ...], tuple[str, ...], tuple[str, ...]]:
    pending_document = _pending_document(paths)
    shard_paths = _ordered_shard_paths(family_state, paths=paths)
    cards: list[TaskCard] = []
    titles: list[str] = []
    merged_spec_ids: list[str] = []

    for spec_id, shard_path in zip(family_state.spec_order, shard_paths):
        shard_document = parse_task_store(shard_path.read_text(encoding="utf-8"), source_file=shard_path)
        if not shard_document.cards:
            raise TaskauditExecutionError(f"Taskaudit shard has no task cards: {shard_path.as_posix()}")
        for card in shard_document.cards:
            if card.spec_id != spec_id:
                raise TaskauditExecutionError(
                    f"Taskaudit shard {shard_path.as_posix()} contains card {card.title!r} for unexpected spec_id {card.spec_id!r}"
                )
            cards.append(card)
            titles.append(card.title)
        merged_spec_ids.append(spec_id)

    assembled_document = TaskStoreDocument(
        preamble=pending_document.preamble or _DEFAULT_PENDING_PREAMBLE,
        cards=cards,
    )
    return assembled_document, shard_paths, tuple(merged_spec_ids), tuple(titles)


def _build_merged_backlog(
    backlog_document: TaskStoreDocument,
    pending_document: TaskStoreDocument,
) -> tuple[TaskCard, ...]:
    ordered_cards = list(backlog_document.cards)
    seen_ids = {card.task_id for card in ordered_cards}
    for card in pending_document.cards:
        if card.task_id in seen_ids:
            raise TaskauditExecutionError(
                f"Taskaudit merge would duplicate backlog task id {card.task_id!r} for {card.title!r}"
            )
        ordered_cards.append(card)
        seen_ids.add(card.task_id)
    return tuple(ordered_cards)


class TaskauditRecord(ContractModel):
    """Durable record for one final-family Taskaudit merge."""

    schema_version: Literal["1.0"] = TASKAUDIT_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["taskaudit_final_family_merge"] = "taskaudit_final_family_merge"
    status: Literal["prepared", "merged", "promotion_blocked"] = "prepared"
    run_id: str
    emitted_at: datetime
    family_state_path: str
    pending_path: str
    backlog_path: str
    provenance_path: str = ""
    blocked_at: datetime | None = None
    blocked_reason: str = ""
    blocked_failure_kind: str = ""
    shard_paths: tuple[str, ...] = ()
    merged_spec_ids: tuple[str, ...] = ()
    ordered_backlog_titles: tuple[str, ...] = ()
    pending_card_count: int = Field(ge=0)
    backlog_card_count_before: int = Field(ge=0)
    backlog_card_count_after: int = Field(ge=0)
    pending_sha256: str
    backlog_sha256_before: str
    backlog_sha256_after: str

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("blocked_at", mode="before")
    @classmethod
    def normalize_blocked_at(cls, value: datetime | str | None) -> datetime | None:
        if value in (None, ""):
            return None
        return _normalize_datetime(value)

    @field_validator("family_state_path", "pending_path", "backlog_path", "provenance_path", mode="before")
    @classmethod
    def normalize_path_fields(cls, value: str | Path | None, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        normalized = _normalize_path_token(value)
        if field_name == "provenance_path":
            return normalized
        if not normalized:
            raise ValueError(f"{field_name} may not be empty")
        return normalized

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="run_id")

    @field_validator("blocked_reason", "blocked_failure_kind", mode="before")
    @classmethod
    def normalize_optional_text_fields(cls, value: str | None) -> str:
        return _normalize_optional_text(value)

    @field_validator("shard_paths", "merged_spec_ids", "ordered_backlog_titles", mode="before")
    @classmethod
    def normalize_sequence_fields(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        return _normalize_token_sequence([_normalize_optional_text(str(item)) for item in value])

    @field_validator("pending_sha256", "backlog_sha256_before", "backlog_sha256_after")
    @classmethod
    def validate_hash_fields(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
            raise ValueError("hash fields must be 64-character lowercase hex digests")
        return normalized


class TaskauditExecutionResult(ContractModel):
    """Minimal Taskaudit result returned to the research plane."""

    status: Literal["prepared", "merged"] = "merged"
    record_path: str
    provenance_path: str = ""
    pending_card_count: int = Field(ge=0)
    backlog_card_count: int = Field(ge=0)

    @field_validator("record_path")
    @classmethod
    def validate_required_paths(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("provenance_path", mode="before")
    @classmethod
    def normalize_optional_provenance_path(cls, value: str | Path | None) -> str:
        return _normalize_path_token(value)


class TaskauditExecutionError(GoalSpecExecutionError):
    """Raised when final-family Taskaudit cannot complete safely."""


def execute_taskaudit(
    paths: RuntimePaths,
    *,
    run_id: str,
    emitted_at: datetime | None = None,
    defer_merge: bool = False,
) -> TaskauditExecutionResult:
    """Assemble one final pending family, merge it into backlog, and refresh provenance."""

    emitted_at = emitted_at or _utcnow()
    record_path = paths.goalspec_runtime_dir / "taskaudit" / f"{run_id}.json"
    provenance_path = paths.agents_dir / "task_provenance.json"

    def _prepared_result(record: TaskauditRecord) -> TaskauditExecutionResult:
        return TaskauditExecutionResult(
            status="prepared",
            record_path=_relative_path(record_path, relative_to=paths.root),
            provenance_path="",
            pending_card_count=record.pending_card_count,
            backlog_card_count=record.backlog_card_count_before,
        )

    def _merged_result(record: TaskauditRecord) -> TaskauditExecutionResult:
        return TaskauditExecutionResult(
            status="merged",
            record_path=_relative_path(record_path, relative_to=paths.root),
            provenance_path=_relative_path(provenance_path, relative_to=paths.root),
            pending_card_count=record.pending_card_count,
            backlog_card_count=record.backlog_card_count_after,
        )

    def _blocked_record(
        record: TaskauditRecord,
        *,
        reason: str,
        failure_kind: str,
        blocked_at: datetime,
    ) -> TaskauditRecord:
        blocked_record = record.model_copy(
            update={
                "status": "promotion_blocked",
                "blocked_at": blocked_at,
                "blocked_reason": reason,
                "blocked_failure_kind": failure_kind,
            }
        )
        _write_json_model(record_path, blocked_record)
        return blocked_record

    def _finalize_merged_record(record: TaskauditRecord) -> TaskauditExecutionResult:
        taskaudit_metadata = TaskauditProvenance(
            record_path=_relative_path(record_path, relative_to=paths.root),
            run_id=run_id,
            merged_at=emitted_at,
            pending_path=_relative_path(paths.taskspending_file, relative_to=paths.root),
            pending_shards=record.shard_paths,
            pending_card_count=record.pending_card_count,
            merged_backlog_card_count=record.backlog_card_count_after,
            merged_spec_ids=record.merged_spec_ids,
            ordered_backlog_titles=record.ordered_backlog_titles,
        )
        refresh_task_provenance_registry(
            provenance_path,
            source_paths=task_provenance_source_paths(paths.agents_dir),
            relative_to=paths.root,
            updated_at=emitted_at,
            taskaudit=taskaudit_metadata,
        )
        merged_record = record.model_copy(
            update={
                "status": "merged",
                "provenance_path": _relative_path(provenance_path, relative_to=paths.root),
            }
        )
        _write_json_model(record_path, merged_record)
        return _merged_result(merged_record)

    def _merge_prepared_record(record: TaskauditRecord) -> TaskauditExecutionResult:
        try:
            backlog_text = _read_store_text(paths.backlog_file, default_preamble=_DEFAULT_BACKLOG_PREAMBLE)
            pending_text = _read_store_text(paths.taskspending_file, default_preamble=_DEFAULT_PENDING_PREAMBLE)
            if _sha256_text(backlog_text) != record.backlog_sha256_before:
                raise TaskauditExecutionError("Taskaudit prepared backlog changed before final merge")
            if _sha256_text(pending_text) != record.pending_sha256:
                raise TaskauditExecutionError("Taskaudit prepared pending family changed before final merge")

            backlog_document = parse_task_store(backlog_text, source_file=paths.backlog_file)
            pending_document = parse_task_store(pending_text, source_file=paths.taskspending_file)
            active_document = parse_task_store(
                _read_store_text(paths.tasks_file, default_preamble="# Active Task"),
                source_file=paths.tasks_file,
            )
            archive_document = parse_task_store(
                _read_store_text(paths.archive_file, default_preamble="# Task Archive"),
                source_file=paths.archive_file,
            )
            merged_backlog_cards = _build_merged_backlog(backlog_document, pending_document)
            _validate_dependency_order(
                merged_backlog_cards,
                active_cards=tuple(active_document.cards),
                archive_cards=tuple(archive_document.cards),
            )
            merged_backlog_text = render_task_store(
                TaskStoreDocument(
                    preamble=backlog_document.preamble or _DEFAULT_BACKLOG_PREAMBLE,
                    cards=list(merged_backlog_cards),
                )
            )
            if _sha256_text(merged_backlog_text) != record.backlog_sha256_after:
                raise TaskauditExecutionError(
                    "Taskaudit prepared merge snapshot no longer matches the recorded backlog"
                )

            shard_paths = tuple(_resolve_path_token(path, relative_to=paths.root) for path in record.shard_paths)
            queue = TaskQueue(paths)
            try:
                queue.merge_pending_family(
                    expected_backlog_sha256=record.backlog_sha256_before,
                    expected_pending_sha256=record.pending_sha256,
                    ordered_backlog_cards=merged_backlog_cards,
                    pending_preamble=pending_document.preamble,
                    clear_shard_paths=shard_paths,
                )
            except QueueMergeConflictError as exc:
                raise TaskauditExecutionError("Taskaudit merge snapshot changed during final merge") from exc

            final_backlog_text = _read_store_text(paths.backlog_file, default_preamble=_DEFAULT_BACKLOG_PREAMBLE)
            final_pending_document = _pending_document(paths)
            if final_pending_document.cards:
                raise TaskauditExecutionError("Taskaudit merge must leave agents/taskspending.md empty")
            if any(path.exists() for path in shard_paths):
                raise TaskauditExecutionError("Taskaudit merge must clear pending shard files")
            if _sha256_text(final_backlog_text) != record.backlog_sha256_after:
                raise TaskauditExecutionError("Taskaudit merge produced an unexpected backlog snapshot")

            final_backlog_document = parse_task_store(final_backlog_text, source_file=paths.backlog_file)
            merged_record = record.model_copy(
                update={
                    "status": "merged",
                    "provenance_path": _relative_path(provenance_path, relative_to=paths.root),
                    "blocked_at": None,
                    "blocked_reason": "",
                    "blocked_failure_kind": "",
                    "ordered_backlog_titles": tuple(card.title for card in final_backlog_document.cards),
                    "backlog_card_count_after": len(final_backlog_document.cards),
                }
            )
            _write_json_model(record_path, merged_record)
            taskaudit_metadata = TaskauditProvenance(
                record_path=_relative_path(record_path, relative_to=paths.root),
                run_id=run_id,
                merged_at=emitted_at,
                pending_path=_relative_path(paths.taskspending_file, relative_to=paths.root),
                pending_shards=record.shard_paths,
                pending_card_count=merged_record.pending_card_count,
                merged_backlog_card_count=merged_record.backlog_card_count_after,
                merged_spec_ids=merged_record.merged_spec_ids,
                ordered_backlog_titles=merged_record.ordered_backlog_titles,
            )
            refresh_task_provenance_registry(
                provenance_path,
                source_paths=task_provenance_source_paths(paths.agents_dir),
                relative_to=paths.root,
                updated_at=emitted_at,
                taskaudit=taskaudit_metadata,
            )
            return _merged_result(merged_record)
        except TaskauditExecutionError as exc:
            _blocked_record(
                record,
                reason=str(exc),
                failure_kind=type(exc).__name__,
                blocked_at=emitted_at,
            )
            raise

    if record_path.exists():
        existing_record = _load_json_model(record_path, TaskauditRecord)
        final_backlog_text = _read_store_text(paths.backlog_file, default_preamble=_DEFAULT_BACKLOG_PREAMBLE)
        final_pending_document = _pending_document(paths)
        if (
            existing_record.status == "merged"
            and not final_pending_document.cards
            and _sha256_text(final_backlog_text) == existing_record.backlog_sha256_after
            and provenance_path.exists()
        ):
            return _merged_result(existing_record)
        if (
            existing_record.status == "prepared"
            and not final_pending_document.cards
            and _sha256_text(final_backlog_text) == existing_record.backlog_sha256_after
        ):
            return _finalize_merged_record(existing_record)
        if existing_record.status == "prepared":
            return _merge_prepared_record(existing_record)
        if existing_record.status == "promotion_blocked":
            raise TaskauditExecutionError(
                existing_record.blocked_reason
                or f"Taskaudit promotion integrity is blocked; inspect {record_path.as_posix()}"
            )

    family_state = load_goal_spec_family_state(paths.goal_spec_family_state_file)
    if not family_state.family_complete or not family_state.fulfills_initial_family_plan():
        raise TaskauditExecutionError("Taskaudit requires a complete GoalSpec family before final merge")

    assembled_pending_document, shard_paths, merged_spec_ids, _ = _assemble_pending_family(paths, family_state)
    assembled_pending_text = render_task_store(assembled_pending_document)
    if _read_store_text(paths.taskspending_file, default_preamble=_DEFAULT_PENDING_PREAMBLE) != assembled_pending_text:
        write_text_atomic(paths.taskspending_file, assembled_pending_text)

    backlog_text = _read_store_text(paths.backlog_file, default_preamble=_DEFAULT_BACKLOG_PREAMBLE)
    pending_text = _read_store_text(paths.taskspending_file, default_preamble=_DEFAULT_PENDING_PREAMBLE)
    backlog_document = parse_task_store(backlog_text, source_file=paths.backlog_file)
    pending_document = parse_task_store(pending_text, source_file=paths.taskspending_file)
    active_document = parse_task_store(
        _read_store_text(paths.tasks_file, default_preamble="# Active Task"),
        source_file=paths.tasks_file,
    )
    archive_document = parse_task_store(
        _read_store_text(paths.archive_file, default_preamble="# Task Archive"),
        source_file=paths.archive_file,
    )
    merged_backlog_cards = _build_merged_backlog(backlog_document, pending_document)
    _validate_dependency_order(
        merged_backlog_cards,
        active_cards=tuple(active_document.cards),
        archive_cards=tuple(archive_document.cards),
    )
    merged_backlog_text = render_task_store(
        TaskStoreDocument(
            preamble=backlog_document.preamble or _DEFAULT_BACKLOG_PREAMBLE,
            cards=list(merged_backlog_cards),
        )
    )
    prepared_record = TaskauditRecord(
        status="prepared",
        run_id=run_id,
        emitted_at=emitted_at,
        family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
        pending_path=_relative_path(paths.taskspending_file, relative_to=paths.root),
        backlog_path=_relative_path(paths.backlog_file, relative_to=paths.root),
        provenance_path="",
        shard_paths=[_relative_path(path, relative_to=paths.root) for path in shard_paths],
        merged_spec_ids=merged_spec_ids,
        ordered_backlog_titles=tuple(card.title for card in merged_backlog_cards),
        pending_card_count=len(pending_document.cards),
        backlog_card_count_before=len(backlog_document.cards),
        backlog_card_count_after=len(merged_backlog_cards),
        pending_sha256=_sha256_text(pending_text),
        backlog_sha256_before=_sha256_text(backlog_text),
        backlog_sha256_after=_sha256_text(merged_backlog_text),
    )
    _write_json_model(record_path, prepared_record)
    if defer_merge:
        return _prepared_result(prepared_record)
    return _merge_prepared_record(prepared_record)


__all__ = [
    "TASKAUDIT_ARTIFACT_SCHEMA_VERSION",
    "TaskauditExecutionError",
    "TaskauditExecutionResult",
    "TaskauditRecord",
    "execute_taskaudit",
]
