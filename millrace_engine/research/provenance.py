"""Task provenance registry contracts for GoalSpec task generation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Sequence
import json

from pydantic import Field, field_validator, model_validator

from ..contracts import ContractModel, _normalize_datetime
from ..markdown import parse_task_store, write_text_atomic


SCHEMA_VERSION = "1.0"
DEFAULT_TASK_PROVENANCE_SOURCE_FILENAMES = ("tasks.md", "tasksbacklog.md", "tasksarchive.md")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_optional_datetime(value: datetime | str | None) -> datetime | None:
    if value in (None, ""):
        return None
    return _normalize_datetime(value)


def _normalize_required_text(value: str, *, field_name: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError(f"{field_name} may not be empty")
    return normalized


def _normalize_optional_text(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.strip().split())


def _normalize_path_token(value: str | Path | None) -> str:
    if value is None:
        return ""
    if isinstance(value, Path):
        return value.as_posix()
    stripped = value.strip()
    if not stripped:
        return ""
    return Path(stripped).as_posix()


def _path_token(path: Path, *, relative_to: Path | None = None) -> str:
    candidate = path
    if relative_to is not None:
        try:
            candidate = path.relative_to(relative_to)
        except ValueError:
            pass
    return candidate.as_posix()


def _write_json_model(path: Path, model: ContractModel) -> None:
    payload = json.loads(model.model_dump_json(exclude_none=False))
    write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


class TaskProvenanceSource(ContractModel):
    """Per-source task-store summary used by the provenance registry."""

    source_file: str
    present: bool
    card_count: int = Field(default=0, ge=0)

    @field_validator("source_file", mode="before")
    @classmethod
    def normalize_source_file(cls, value: str | Path) -> str:
        normalized = _normalize_path_token(value)
        if not normalized:
            raise ValueError("source_file may not be empty")
        return normalized


class TaskCardProvenance(ContractModel):
    """One card summary emitted into the task provenance registry."""

    source_file: str
    title: str
    spec_id: str = ""

    @field_validator("source_file", mode="before")
    @classmethod
    def normalize_source_file(cls, value: str | Path) -> str:
        normalized = _normalize_path_token(value)
        if not normalized:
            raise ValueError("source_file may not be empty")
        return normalized

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="title")

    @field_validator("spec_id", mode="before")
    @classmethod
    def normalize_spec_id(cls, value: str | None) -> str:
        return _normalize_optional_text(value)


class TaskauditProvenance(ContractModel):
    """Deterministic metadata for the latest successful Taskaudit merge."""

    record_path: str
    run_id: str
    merged_at: datetime
    pending_path: str
    pending_shards: tuple[str, ...] = ()
    pending_card_count: int = Field(default=0, ge=0)
    merged_backlog_card_count: int = Field(default=0, ge=0)
    merged_spec_ids: tuple[str, ...] = ()
    ordered_backlog_titles: tuple[str, ...] = ()

    @field_validator("record_path", "pending_path", mode="before")
    @classmethod
    def normalize_required_path_fields(cls, value: str | Path) -> str:
        normalized = _normalize_path_token(value)
        if not normalized:
            raise ValueError("taskaudit provenance path fields may not be empty")
        return normalized

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="run_id")

    @field_validator("merged_at", mode="before")
    @classmethod
    def normalize_merged_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("pending_shards", "merged_spec_ids", "ordered_backlog_titles", mode="before")
    @classmethod
    def normalize_sequence_fields(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        return tuple(
            token
            for token in (_normalize_optional_text(str(item)) for item in value)
            if token
        )


class TaskProvenanceRegistry(ContractModel):
    """On-disk registry that records which task stores contributed visible cards."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    updated_at: datetime | None = None
    sources: tuple[TaskProvenanceSource, ...] = ()
    task_cards: tuple[TaskCardProvenance, ...] = ()
    taskaudit: TaskauditProvenance | None = None

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_optional_datetime(value)

    @model_validator(mode="after")
    def validate_unique_sources(self) -> "TaskProvenanceRegistry":
        source_files = [entry.source_file for entry in self.sources]
        if len(set(source_files)) != len(source_files):
            raise ValueError("sources may not contain duplicate source_file entries")
        return self


def default_task_provenance_registry() -> TaskProvenanceRegistry:
    """Return the empty bootstrap task provenance registry."""

    return TaskProvenanceRegistry()


def task_provenance_source_paths(agents_dir: Path) -> tuple[Path, ...]:
    """Return the canonical task stores that feed task provenance."""

    return tuple(agents_dir / name for name in DEFAULT_TASK_PROVENANCE_SOURCE_FILENAMES)


def load_task_provenance_registry(path: Path) -> TaskProvenanceRegistry:
    """Load task provenance or return the bootstrap registry when absent."""

    if not path.exists():
        return default_task_provenance_registry()
    return TaskProvenanceRegistry.model_validate(json.loads(path.read_text(encoding="utf-8")))


def write_task_provenance_registry(path: Path, registry: TaskProvenanceRegistry) -> TaskProvenanceRegistry:
    """Persist task provenance deterministically."""

    _write_json_model(path, registry)
    return registry


def refresh_task_provenance_registry(
    output_path: Path,
    *,
    source_paths: Sequence[Path],
    relative_to: Path | None = None,
    updated_at: datetime | str | None = None,
    taskaudit: TaskauditProvenance | dict[str, object] | None = None,
) -> TaskProvenanceRegistry:
    """Rebuild task provenance from the visible task stores."""

    source_meta: list[TaskProvenanceSource] = []
    task_cards: list[TaskCardProvenance] = []

    for source_path in source_paths:
        source_token = _path_token(source_path, relative_to=relative_to)
        if not source_path.exists():
            source_meta.append(TaskProvenanceSource(source_file=source_token, present=False, card_count=0))
            continue

        document = parse_task_store(source_path.read_text(encoding="utf-8"), source_file=source_path)
        source_meta.append(
            TaskProvenanceSource(
                source_file=source_token,
                present=True,
                card_count=len(document.cards),
            )
        )
        for card in document.cards:
            task_cards.append(
                TaskCardProvenance(
                    source_file=source_token,
                    title=card.title,
                    spec_id=card.spec_id or "",
                )
            )

    registry = TaskProvenanceRegistry(
        updated_at=_normalize_optional_datetime(updated_at) or _utcnow(),
        sources=tuple(source_meta),
        task_cards=tuple(task_cards),
        taskaudit=(
            taskaudit
            if isinstance(taskaudit, TaskauditProvenance) or taskaudit is None
            else TaskauditProvenance.model_validate(taskaudit)
        ),
    )
    write_task_provenance_registry(output_path, registry)
    return registry


__all__ = [
    "DEFAULT_TASK_PROVENANCE_SOURCE_FILENAMES",
    "SCHEMA_VERSION",
    "TaskCardProvenance",
    "TaskauditProvenance",
    "TaskProvenanceRegistry",
    "TaskProvenanceSource",
    "default_task_provenance_registry",
    "load_task_provenance_registry",
    "refresh_task_provenance_registry",
    "task_provenance_source_paths",
    "write_task_provenance_registry",
]
