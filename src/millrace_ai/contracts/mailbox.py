"""Mailbox command contracts."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePath
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from .base import ContractModel
from .enums import MailboxCommand, WorkItemKind
from .stage_metadata import validate_safe_identifier
from .work_documents import ProbeDocument, SpecDocument, TaskDocument


class MailboxCommandEnvelope(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["mailbox_command"] = "mailbox_command"

    command_id: str
    command: MailboxCommand
    issued_at: datetime
    issuer: str
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class MailboxAddTaskPayload(ContractModel):
    document: TaskDocument


class MailboxAddProbePayload(ContractModel):
    document: ProbeDocument


class MailboxAddSpecPayload(ContractModel):
    document: SpecDocument


class MailboxAddIdeaPayload(ContractModel):
    source_name: str
    markdown: str

    @model_validator(mode="after")
    def validate_shape(self) -> "MailboxAddIdeaPayload":
        source_name = self.source_name.strip()
        if source_name != self.source_name:
            raise ValueError("source_name must not include surrounding whitespace")
        if not source_name:
            raise ValueError("source_name is required")
        if not source_name.endswith(".md"):
            raise ValueError("source_name must end with .md")
        path = PurePath(source_name)
        if path.is_absolute() or len(path.parts) != 1:
            raise ValueError("source_name must be a single relative filename")
        stem = source_name[:-3]
        validate_safe_identifier(stem, field_name="source_name")
        if not self.markdown.strip():
            raise ValueError("markdown is required")
        return self


class _ReasonedPayload(ContractModel):
    reason: str

    @model_validator(mode="after")
    def validate_reason(self) -> "_ReasonedPayload":
        if not self.reason.strip():
            raise ValueError("reason is required")
        return self


class MailboxCancelWorkItemPayload(_ReasonedPayload):
    work_item_id: str
    work_item_kind: WorkItemKind | None = None
    force: bool = False

    @model_validator(mode="after")
    def validate_shape(self) -> "MailboxCancelWorkItemPayload":
        validate_safe_identifier(self.work_item_id, field_name="work_item_id")
        return self


class MailboxArchiveBlockedTaskPayload(_ReasonedPayload):
    task_id: str

    @model_validator(mode="after")
    def validate_shape(self) -> "MailboxArchiveBlockedTaskPayload":
        validate_safe_identifier(self.task_id, field_name="task_id")
        return self


class MailboxSupersedeTaskPayload(_ReasonedPayload):
    old_task_id: str
    replacement_task_id: str
    cascade: Literal["none", "retarget", "cancel"] = "none"

    @model_validator(mode="after")
    def validate_shape(self) -> "MailboxSupersedeTaskPayload":
        validate_safe_identifier(self.old_task_id, field_name="old_task_id")
        validate_safe_identifier(self.replacement_task_id, field_name="replacement_task_id")
        return self


class MailboxRetargetTaskDependencyPayload(_ReasonedPayload):
    task_id: str
    old_dependency_id: str
    new_dependency_id: str

    @model_validator(mode="after")
    def validate_shape(self) -> "MailboxRetargetTaskDependencyPayload":
        validate_safe_identifier(self.task_id, field_name="task_id")
        validate_safe_identifier(self.old_dependency_id, field_name="old_dependency_id")
        validate_safe_identifier(self.new_dependency_id, field_name="new_dependency_id")
        return self


class MailboxIncidentInterventionPayload(_ReasonedPayload):
    incident_id: str

    @model_validator(mode="after")
    def validate_shape(self) -> "MailboxIncidentInterventionPayload":
        validate_safe_identifier(self.incident_id, field_name="incident_id")
        return self


class MailboxArchiveInvalidIncidentPayload(_ReasonedPayload):
    filename: str

    @model_validator(mode="after")
    def validate_shape(self) -> "MailboxArchiveInvalidIncidentPayload":
        filename = self.filename.strip()
        if filename != self.filename or not filename:
            raise ValueError("filename must be a single relative filename")
        path = PurePath(filename)
        if path.is_absolute() or len(path.parts) != 1:
            raise ValueError("filename must be a single relative filename")
        return self


class MailboxExecutionCapabilityApprovalPayload(_ReasonedPayload):
    approval_id: str

    @model_validator(mode="after")
    def validate_shape(self) -> "MailboxExecutionCapabilityApprovalPayload":
        validate_safe_identifier(self.approval_id, field_name="approval_id")
        return self


__all__ = [
    "MailboxAddIdeaPayload",
    "MailboxAddProbePayload",
    "MailboxAddSpecPayload",
    "MailboxAddTaskPayload",
    "MailboxArchiveBlockedTaskPayload",
    "MailboxArchiveInvalidIncidentPayload",
    "MailboxCancelWorkItemPayload",
    "MailboxCommandEnvelope",
    "MailboxExecutionCapabilityApprovalPayload",
    "MailboxIncidentInterventionPayload",
    "MailboxRetargetTaskDependencyPayload",
    "MailboxSupersedeTaskPayload",
]
