"""Operator control capability contracts for workflow primitives."""

from __future__ import annotations

from typing import Literal

from pydantic import ValidationInfo, field_validator, model_validator

from millrace_ai.contracts import Plane

from ..common import normalize_canonical_id
from ..stage_kinds import ArchitectureContractModel
from ._validation import _normalize_unique_id_tuple
from .identifiers import WorkItemFamilyId


class OperatorControlCapabilityDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["operator_control_capability"] = "operator_control_capability"
    capability_id: str
    action: Literal[
        "pause",
        "resume",
        "stop",
        "cancel",
        "requeue",
        "retry",
        "block",
        "unblock",
        "supersede",
        "archive",
        "inspect",
        "approve",
        "deny",
        "reload_config",
        "recompile",
        "reset_workspace_schema",
        "repair_state",
    ]
    target_type: Literal[
        "workspace",
        "plane",
        "lane",
        "family",
        "lineage",
        "work_item",
        "run",
        "node",
        "completion_target",
    ]
    plane: Plane | None = None
    family_ids: tuple[WorkItemFamilyId, ...] = ()
    lane_ids: tuple[str, ...] = ()
    allowed_lifecycle_states: tuple[str, ...] = ()

    @field_validator("capability_id")
    @classmethod
    def validate_capability_id(cls, value: str) -> str:
        return normalize_canonical_id(value, field_label="capability_id")

    @field_validator("family_ids", "lane_ids", "allowed_lifecycle_states", mode="before")
    @classmethod
    def normalize_id_tuples(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(value, field_label=info.field_name or "id tuple", allow_empty=True)

    @model_validator(mode="after")
    def validate_target_scope(self) -> "OperatorControlCapabilityDefinition":
        if self.target_type in {"family", "work_item"} and not self.family_ids:
            raise ValueError("family_ids are required for family and work_item control targets")
        if self.target_type == "lane" and not self.lane_ids:
            raise ValueError("lane_ids are required for lane control targets")
        return self
