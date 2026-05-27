"""Request-context profile contracts for workflow primitives."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator

from millrace_ai.contracts import Plane

from ..common import normalize_canonical_id, normalize_nonempty_text
from ..stage_kinds import ArchitectureContractModel
from ._validation import (
    _canonical,
    _normalize_runtime_relative_path,
    _normalize_unique_id_tuple,
)
from .identifiers import (
    RequestContextProfileId,
    RequestContextProviderId,
    RequestContextRenderPlanId,
)


class RequestContextProviderDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["request_context_provider"] = "request_context_provider"
    provider_id: RequestContextProviderId
    python_registry_id: str
    supported_request_kinds: tuple[str, ...] = Field(min_length=1)
    supported_planes: tuple[Plane, ...] = Field(min_length=1)
    capabilities: tuple[str, ...] = Field(min_length=1)
    required_workspace_data_surfaces: tuple[str, ...] = ()

    @field_validator("provider_id", "python_registry_id")
    @classmethod
    def validate_ids(cls, value: str, info: ValidationInfo) -> str:
        return _canonical(value, info)

    @field_validator(
        "supported_request_kinds",
        "capabilities",
        "required_workspace_data_surfaces",
        mode="before",
    )
    @classmethod
    def normalize_id_tuples(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(
            value,
            field_label=info.field_name or "request context provider id tuple",
            allow_empty=info.field_name == "required_workspace_data_surfaces",
        )

    @field_validator("supported_planes")
    @classmethod
    def validate_supported_planes(cls, value: tuple[Plane, ...]) -> tuple[Plane, ...]:
        if len(set(value)) != len(value):
            raise ValueError("supported_planes may not contain duplicate planes")
        return value


class RequestContextProfileDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["request_context_profile"] = "request_context_profile"
    profile_id: RequestContextProfileId
    request_kind: str
    provider_id: RequestContextProviderId
    primary_render_plan_id: RequestContextRenderPlanId
    allow_render_plan_override: bool = False
    required_providers: tuple[str, ...] = ()
    optional_providers: tuple[str, ...] = ()
    output_path_preferences: dict[str, str] = Field(default_factory=dict)
    visibility_policy: Literal["active_item_only", "lineage_summary", "lineage_full", "closure_target"]

    @field_validator("profile_id", "request_kind", "provider_id", "primary_render_plan_id")
    @classmethod
    def validate_ids(cls, value: str, info: ValidationInfo) -> str:
        return _canonical(value, info)

    @field_validator("required_providers", "optional_providers", mode="before")
    @classmethod
    def normalize_providers(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(
            value,
            field_label=info.field_name or "provider ids",
            allow_empty=info.field_name != "required_providers",
        )

    @field_validator("output_path_preferences", mode="before")
    @classmethod
    def normalize_output_path_preferences(cls, value: object) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("output_path_preferences must be a mapping")
        normalized: dict[str, str] = {}
        for raw_key, raw_path in value.items():
            key = normalize_canonical_id(str(raw_key), field_label="output_path_preferences key")
            if key in normalized:
                raise ValueError("output_path_preferences may not contain duplicate normalized keys")
            normalized[key] = _normalize_runtime_relative_path(
                str(raw_path),
                field_label="output_path_preferences",
            )
        return normalized


class RequestContextRenderPlan(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["request_context_render_plan"] = "request_context_render_plan"
    render_plan_id: RequestContextRenderPlanId
    profile_id: RequestContextProfileId | None = None
    bundle_schema_version: str = "1.0"
    included_sections: tuple[str, ...] = Field(min_length=1)
    required_provider_capabilities: tuple[str, ...] = ()
    artifact_ref_policy: Literal["path_only", "inline_if_small", "summary_only"]
    prompt_rendering_behavior: Literal["default_markdown", "blueprint_markdown", "closure_markdown"]
    redaction_policy_id: str
    max_inline_bytes_by_role: dict[str, int] = Field(default_factory=dict)
    missing_optional_provider_policy: Literal["omit", "mention_absent"]

    @model_validator(mode="before")
    @classmethod
    def normalize_section_alias(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        if "included_sections" not in payload and "section_order" in payload:
            payload["included_sections"] = payload["section_order"]
        return payload

    @field_validator("render_plan_id", "profile_id", "redaction_policy_id")
    @classmethod
    def validate_ids(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _canonical(value, info)

    @field_validator("bundle_schema_version")
    @classmethod
    def validate_bundle_schema_version(cls, value: str) -> str:
        return normalize_nonempty_text(value, field_label="bundle_schema_version")

    @field_validator("included_sections", "required_provider_capabilities", mode="before")
    @classmethod
    def normalize_id_tuples(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(
            value,
            field_label=info.field_name or "request context render plan id tuple",
            allow_empty=info.field_name == "required_provider_capabilities",
        )

    @field_validator("max_inline_bytes_by_role", mode="before")
    @classmethod
    def normalize_max_inline_bytes_by_role(cls, value: object) -> dict[str, int]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("max_inline_bytes_by_role must be a mapping")
        normalized: dict[str, int] = {}
        for raw_role, raw_limit in value.items():
            role = normalize_canonical_id(str(raw_role), field_label="max_inline_bytes_by_role key")
            limit = int(raw_limit)
            if limit < 0:
                raise ValueError("max_inline_bytes_by_role values must be non-negative")
            normalized[role] = limit
        return normalized

    @property
    def section_order(self) -> tuple[str, ...]:
        return self.included_sections
