"""Mailbox command contracts."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePath
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from .base import ContractModel
from .enums import MailboxCommand
from .stage_metadata import validate_safe_identifier
from .work_documents import SpecDocument, TaskDocument


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


__all__ = [
    "MailboxAddIdeaPayload",
    "MailboxAddSpecPayload",
    "MailboxAddTaskPayload",
    "MailboxCommandEnvelope",
]
