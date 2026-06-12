"""Built-in Blueprint context provider adapter."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.extensions.interfaces import BUILTIN_INTERFACE_IDS

if TYPE_CHECKING:
    from millrace_ai.architecture import CompiledRunPlan
    from millrace_ai.runners import StageRunRequest


class BuiltInBlueprintContextProvider:
    """Built-in Blueprint context provider backed by the Blueprint extension."""

    interface_id: str = BUILTIN_INTERFACE_IDS["blueprint_context_provider"]
    domain: str = "blueprint"

    def build_context_plan(
        self,
        workspace_root: Path,
        request: "StageRunRequest",
        authority: object,
        compiled_plan: "CompiledRunPlan | None",
    ) -> object:
        from millrace_ai.extensions.builtin.blueprint.context import (
            built_in_blueprint_provider_registrations,
        )
        from millrace_ai.runtime.context.providers import (
            RequestContextProviderRegistration,
            RequestContextProviderRegistry,
        )

        registry = RequestContextProviderRegistry.from_registrations(
            RequestContextProviderRegistration(
                implementation_id=impl_id,
                provider=provider,
            )
            for impl_id, provider in built_in_blueprint_provider_registrations()
        )

        provider_impl_id = getattr(authority, "provider_python_registry_id", "")
        provider_fn = registry.provider_for(provider_impl_id)
        if provider_fn is None:
            raise ValueError(
                f"Blueprint context provider not found for {provider_impl_id!r}"
            )
        return provider_fn(workspace_root, request, authority, compiled_plan)


__all__ = ["BuiltInBlueprintContextProvider"]
