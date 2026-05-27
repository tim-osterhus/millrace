"""Completion behavior contracts for workflow primitives."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, ValidationInfo, field_validator

from millrace_ai.contracts import Plane

from ..common import normalize_canonical_id, normalize_status
from ..stage_kinds import ArchitectureContractModel
from ._validation import _canonical, _normalize_unique_id_tuple
from .identifiers import RequestContextProfileId, TerminalActionId


class WorkflowCompletionBehaviorDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["workflow_completion_behavior"] = "workflow_completion_behavior"
    behavior_id: str
    plane: Plane
    trigger: Literal["backlog_drained"] = "backlog_drained"
    target_scope: Literal["workspace", "plane", "lane", "lineage", "family", "custom"]
    readiness_handler_ids: tuple[str, ...] = Field(min_length=1)
    target_entry_key: str
    target_node_id: str
    request_context_profile_id: RequestContextProfileId
    target_selector: str
    backpressure_policy: Literal["block_same_scope", "block_same_lineage", "block_all", "allow_unrelated"]
    skip_if_already_closed: bool = True
    terminal_action_by_outcome: dict[str, TerminalActionId] = Field(min_length=1)

    @field_validator(
        "behavior_id",
        "target_entry_key",
        "target_node_id",
        "request_context_profile_id",
        "target_selector",
    )
    @classmethod
    def validate_ids(cls, value: str, info: ValidationInfo) -> str:
        return _canonical(value, info)

    @field_validator("readiness_handler_ids", mode="before")
    @classmethod
    def normalize_readiness_handlers(cls, value: object) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(value, field_label="readiness_handler_ids", allow_empty=False)

    @field_validator("terminal_action_by_outcome", mode="before")
    @classmethod
    def normalize_terminal_action_by_outcome(cls, value: object) -> dict[str, str]:
        if not isinstance(value, dict) or not value:
            raise ValueError("terminal_action_by_outcome must not be empty")
        normalized: dict[str, str] = {}
        for raw_outcome, raw_action in value.items():
            outcome = normalize_status(str(raw_outcome), field_label="terminal_action_by_outcome outcome")
            if outcome in normalized:
                raise ValueError("terminal_action_by_outcome may not contain duplicate normalized outcomes")
            normalized[outcome] = normalize_canonical_id(
                str(raw_action),
                field_label="terminal_action_by_outcome action",
            )
        return normalized
