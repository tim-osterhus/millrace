"""Typed contracts for additive stage-kind registry objects."""

from __future__ import annotations

from enum import Enum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from millrace_ai.contracts import Plane, ResultClass

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
    allowed_result_classes_by_outcome: dict[str, tuple[ResultClass, ...]] = Field(min_length=1)
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

    @field_validator("allowed_result_classes_by_outcome", mode="before")
    @classmethod
    def normalize_allowed_result_classes_by_outcome(
        cls,
        value: dict[str, tuple[ResultClass, ...] | list[ResultClass | str]] | None,
    ) -> dict[str, tuple[ResultClass, ...]]:
        if not value:
            raise ValueError("allowed_result_classes_by_outcome must not be empty")
        if not isinstance(value, dict):
            raise ValueError("allowed_result_classes_by_outcome must be a mapping")

        normalized: dict[str, tuple[ResultClass, ...]] = {}
        for raw_outcome, raw_result_classes in value.items():
            outcome = normalize_status(str(raw_outcome), field_label="stage outcome")
            if outcome in normalized:
                raise ValueError(
                    "allowed_result_classes_by_outcome may not declare duplicate normalized outcomes"
                )
            if not raw_result_classes:
                raise ValueError(
                    "allowed_result_classes_by_outcome entries must declare at least one result class"
                )
            result_classes: list[ResultClass] = []
            for item in raw_result_classes:
                try:
                    result_class = item if isinstance(item, ResultClass) else ResultClass(str(item).strip())
                except ValueError as exc:
                    raise ValueError(
                        f"invalid result class for outcome {outcome}: {item}"
                    ) from exc
                if result_class not in result_classes:
                    result_classes.append(result_class)
            normalized[outcome] = tuple(result_classes)
        return normalized

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
        if set(self.allowed_result_classes_by_outcome) != legal:
            raise ValueError(
                "allowed_result_classes_by_outcome must define one entry for every legal outcome"
            )

        blocked_result_classes = {
            ResultClass.BLOCKED,
            ResultClass.RECOVERABLE_FAILURE,
        }
        for outcome, result_classes in self.allowed_result_classes_by_outcome.items():
            if outcome == "BLOCKED":
                if set(result_classes) - blocked_result_classes:
                    raise ValueError(
                        "BLOCKED outcome may only allow blocked or recoverable_failure result classes"
                    )
                continue
            if len(result_classes) != 1:
                raise ValueError(
                    "non-BLOCKED outcomes must map to exactly one allowed result class"
                )

        for outcome in self.success_outcomes:
            if ResultClass.SUCCESS not in self.allowed_result_classes_by_outcome[outcome]:
                raise ValueError(
                    "success outcomes must allow result class success"
                )

        for outcome in self.failure_outcomes:
            if ResultClass.SUCCESS in self.allowed_result_classes_by_outcome[outcome]:
                raise ValueError("failure outcomes may not allow result class success")
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
