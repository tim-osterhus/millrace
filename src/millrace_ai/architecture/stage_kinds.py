"""Typed contracts for additive stage-kind registry objects."""

from __future__ import annotations

from enum import Enum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from millrace_ai.contracts import Plane

from .common import (
    dedupe_preserve_order,
    normalize_canonical_id,
    normalize_nonempty_text,
    normalize_override_name,
    normalize_status,
)

_ALLOWED_ENTRYPOINT_PREFIX = "entrypoints/"
_ALLOWED_SKILL_PREFIX = "skills/"


class ArchitectureContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StageIdempotencePolicy(str, Enum):
    IDEMPOTENT = "idempotent"
    RETRY_SAFE_WITH_KEY = "retry_safe_with_key"
    SINGLE_ATTEMPT_ONLY = "single_attempt_only"


class RecoveryRole(str, Enum):
    LOCAL_REPAIR = "local_repair"
    ESCALATION = "escalation"


class RegisteredStageKindDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["registered_stage_kind"] = "registered_stage_kind"

    stage_kind_id: str
    plane: Plane
    display_name: str
    default_entrypoint_path: str
    required_skill_paths: tuple[str, ...] = Field(min_length=1)
    suggested_skill_paths: tuple[str, ...] = ()
    running_status_marker: str
    legal_outcomes: tuple[str, ...] = Field(min_length=1)
    success_outcomes: tuple[str, ...] = Field(min_length=1)
    failure_outcomes: tuple[str, ...] = ()
    allowed_input_artifacts: tuple[str, ...] = ()
    declared_output_artifacts: tuple[str, ...] = ()
    idempotence_policy: StageIdempotencePolicy = StageIdempotencePolicy.RETRY_SAFE_WITH_KEY
    allowed_overrides: tuple[str, ...] = ()
    can_start_tasks: bool = False
    can_start_specs: bool = False
    can_start_incidents: bool = False
    can_start_learning_requests: bool = False
    recovery_role: RecoveryRole | None = None
    closure_role: bool = False

    @field_validator("stage_kind_id")
    @classmethod
    def validate_stage_kind_id(cls, value: str) -> str:
        return normalize_canonical_id(value, field_label="stage_kind_id")

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        return normalize_nonempty_text(value, field_label="display_name")

    @field_validator("default_entrypoint_path")
    @classmethod
    def validate_default_entrypoint_path(cls, value: str) -> str:
        return _normalize_markdown_asset_path(
            value,
            field_label="default_entrypoint_path",
            required_prefix=_ALLOWED_ENTRYPOINT_PREFIX,
        )

    @field_validator("required_skill_paths", "suggested_skill_paths", mode="before")
    @classmethod
    def normalize_skill_paths(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        normalized = [
            _normalize_markdown_asset_path(
                str(item),
                field_label="skill path",
                required_prefix=_ALLOWED_SKILL_PREFIX,
            )
            for item in value
        ]
        return dedupe_preserve_order(normalized)

    @field_validator(
        "legal_outcomes",
        "success_outcomes",
        "failure_outcomes",
        mode="before",
    )
    @classmethod
    def normalize_outcomes(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        normalized = [normalize_status(str(item), field_label="stage outcome") for item in value]
        return dedupe_preserve_order(normalized)

    @field_validator("allowed_input_artifacts", "declared_output_artifacts", mode="before")
    @classmethod
    def normalize_artifact_names(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        normalized = [normalize_canonical_id(str(item), field_label="artifact name") for item in value]
        return dedupe_preserve_order(normalized)

    @field_validator("allowed_overrides", mode="before")
    @classmethod
    def normalize_allowed_overrides(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        normalized = [normalize_override_name(str(item), field_label="allowed override") for item in value]
        return dedupe_preserve_order(normalized)

    @field_validator("running_status_marker")
    @classmethod
    def validate_running_status_marker(cls, value: str) -> str:
        return normalize_status(value, field_label="running_status_marker")

    @model_validator(mode="after")
    def validate_contract(self) -> "RegisteredStageKindDefinition":
        legal = set(self.legal_outcomes)
        if set(self.success_outcomes) - legal:
            raise ValueError("success_outcomes must be a subset of legal_outcomes")
        if set(self.failure_outcomes) - legal:
            raise ValueError("failure_outcomes must be a subset of legal_outcomes")
        return self


def _normalize_markdown_asset_path(
    value: str,
    *,
    field_label: str,
    required_prefix: str,
) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_label} may not be empty")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field_label} must stay inside packaged runtime assets")
    if not normalized.startswith(required_prefix):
        raise ValueError(f"{field_label} must start with {required_prefix!r}")
    if path.suffix.lower() != ".md":
        raise ValueError(f"{field_label} must point at a markdown asset")
    return normalized


__all__ = [
    "ArchitectureContractModel",
    "RecoveryRole",
    "RegisteredStageKindDefinition",
    "StageIdempotencePolicy",
]
