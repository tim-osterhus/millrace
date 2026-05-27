"""Work-item family contracts for workflow primitives."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator

from millrace_ai.contracts import Plane

from ..common import normalize_nonempty_text
from ..stage_kinds import ArchitectureContractModel
from ._validation import (
    _canonical,
    _normalize_file_extension,
    _normalize_runtime_relative_path,
    _normalize_unique_id_tuple,
)
from .identifiers import (
    DocumentAdapterId,
    QueueLifecycleAdapterId,
    WorkItemFamilyId,
    builtin_queue_lifecycle_adapter_id_for_family,
)


class WorkItemQueueDirs(ArchitectureContractModel):
    queue: str
    active: str
    done: str
    blocked: str
    canceled: str | None = None
    superseded: str | None = None

    @field_validator("queue", "active", "done", "blocked", "canceled", "superseded")
    @classmethod
    def validate_state_dir(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _normalize_runtime_relative_path(
            value,
            field_label=info.field_name or "queue directory",
        )


class WorkItemFamilyDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["work_item_family"] = "work_item_family"
    family_id: WorkItemFamilyId
    plane: Plane
    entry_key: str
    display_name: str
    document_kind: str
    runtime_relative_dir: str
    file_extension: str = ".json"
    schema_id: str
    document_adapter_id: DocumentAdapterId
    queue_lifecycle_adapter_id: QueueLifecycleAdapterId | None = None
    queue_dirs: WorkItemQueueDirs
    lifecycle_states: tuple[str, ...] = Field(min_length=1)
    claimable_state: str = "queued"
    active_state: str = "active"
    done_state: str = "done"
    blocked_state: str = "blocked"
    canceled_state: str | None = None
    closure_blocking_states: tuple[str, ...] = ()
    default_entry_key: str | None = None
    id_field: str | None = None
    created_at_field: str = "created_at"
    lineage_fields: tuple[str, ...] = ()
    dependency_field: str | None = None
    one_active_policy: Literal[
        "plane",
        "lane",
        "family",
        "lineage",
        "work_item",
        "custom_partition",
    ] = "plane"
    duplicate_policy: Literal["fail", "supersede", "idempotent"] = "fail"
    invalid_artifact_policy: Literal["reject", "block_source", "quarantine"] = "block_source"
    sort_policy: Literal["created_at_asc", "created_at_desc", "lexical_path"] = "created_at_asc"
    operator_capabilities: tuple[str, ...] = ()

    @field_validator("family_id", "entry_key", "document_kind", "schema_id", "document_adapter_id")
    @classmethod
    def validate_canonical_ids(cls, value: str, info: ValidationInfo) -> str:
        return _canonical(value, info)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        return normalize_nonempty_text(value, field_label="display_name")

    @field_validator("runtime_relative_dir")
    @classmethod
    def validate_runtime_relative_dir(cls, value: str) -> str:
        return _normalize_runtime_relative_path(value, field_label="runtime_relative_dir")

    @field_validator("file_extension")
    @classmethod
    def validate_file_extension(cls, value: str) -> str:
        return _normalize_file_extension(value, field_label="file_extension")

    @field_validator(
        "lifecycle_states",
        "closure_blocking_states",
        "lineage_fields",
        "operator_capabilities",
        mode="before",
    )
    @classmethod
    def normalize_id_tuples(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(
            value,
            field_label=info.field_name or "id tuple",
            allow_empty=info.field_name != "lifecycle_states",
        )

    @field_validator(
        "claimable_state",
        "active_state",
        "done_state",
        "blocked_state",
        "canceled_state",
        "default_entry_key",
        "id_field",
        "created_at_field",
        "dependency_field",
        "queue_lifecycle_adapter_id",
    )
    @classmethod
    def validate_optional_ids(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _canonical(value, info)

    @model_validator(mode="after")
    def validate_lifecycle_membership(self) -> "WorkItemFamilyDefinition":
        states = set(self.lifecycle_states)
        semantic_states = {
            "claimable_state": self.claimable_state,
            "active_state": self.active_state,
            "done_state": self.done_state,
            "blocked_state": self.blocked_state,
            "canceled_state": self.canceled_state,
        }
        for field_name, state in semantic_states.items():
            if state is not None and state not in states:
                raise ValueError(f"{field_name} must be declared in lifecycle_states")
        unknown_blocking = set(self.closure_blocking_states) - states
        if unknown_blocking:
            raise ValueError("closure_blocking_states must be declared in lifecycle_states")
        if self.default_entry_key is not None and self.default_entry_key != self.entry_key:
            raise ValueError("default_entry_key must match entry_key for this family")
        if self.queue_lifecycle_adapter_id is None:
            self.queue_lifecycle_adapter_id = builtin_queue_lifecycle_adapter_id_for_family(
                self.family_id
            )
        return self
