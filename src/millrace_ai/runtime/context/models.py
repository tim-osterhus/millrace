"""Runtime request-context render models and provider authority bindings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from millrace_ai.architecture import (
    RequestContextProfileDefinition,
    RequestContextProviderDefinition,
)
from millrace_ai.architecture import (
    RequestContextRenderPlan as RequestContextRenderPlanDefinition,
)


class RequestContextRenderPlan(BaseModel):
    """Runtime render input for one stage request context bundle."""

    model_config = ConfigDict(extra="forbid")

    render_plan_id: str
    context_bundle_path: str
    rendered_prompt_context_path: str | None = None
    profile_id: str = "stage.default"
    visible_artifact_refs: tuple[str, ...] = ()
    operator_only_artifact_refs: tuple[str, ...] = ()
    included_provider_ids: tuple[str, ...] = ()
    redacted_provider_ids: tuple[str, ...] = ()
    inline_sections: tuple[str, ...] = ()
    omitted_provider_ids: tuple[str, ...] = ()
    artifact_contract_source: str | None = None
    output_artifact_contract_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_plan(self) -> "RequestContextRenderPlan":
        if not self.render_plan_id.strip():
            raise ValueError("render_plan_id is required")
        if not self.context_bundle_path.strip():
            raise ValueError("context_bundle_path is required")
        if not self.profile_id.strip():
            raise ValueError("profile_id is required")
        return self


class RenderedRequestContext(BaseModel):
    """Paths and text emitted by a deterministic context render."""

    model_config = ConfigDict(extra="forbid")

    context_bundle_path: str
    rendered_prompt_context_path: str
    render_manifest_path: str
    text: str
    manifest: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RequestContextAuthority:
    """Compiled profile/provider/render-plan authority for one request."""

    profile_id: str
    render_plan_id: str
    provider_id: str
    provider_python_registry_id: str
    profile: RequestContextProfileDefinition
    provider: RequestContextProviderDefinition
    render_plan: RequestContextRenderPlanDefinition


__all__ = [
    "RenderedRequestContext",
    "RequestContextAuthority",
    "RequestContextRenderPlan",
]
