"""Request-context provider registry and compiled-authority resolution."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import cast

from millrace_ai.architecture import (
    CompiledRunPlan,
    MaterializedGraphNodePlan,
    RequestContextProfileDefinition,
    RequestContextProviderDefinition,
)
from millrace_ai.architecture import (
    RequestContextRenderPlan as RequestContextRenderPlanDefinition,
)
from millrace_ai.assets import (
    discover_request_context_profile_definitions,
    discover_request_context_provider_definitions,
    discover_request_context_render_plan_definitions,
)
from millrace_ai.runners import StageRunRequest

from .blueprint import built_in_blueprint_provider_registrations
from .generic import built_in_generic_provider_registrations
from .models import RequestContextAuthority, RequestContextRenderPlan

RequestContextPlanProvider = Callable[
    [Path, StageRunRequest, RequestContextAuthority, CompiledRunPlan | None],
    RequestContextRenderPlan,
]


@dataclass(frozen=True, slots=True)
class RequestContextProviderRegistration:
    implementation_id: str
    provider: RequestContextPlanProvider


@dataclass(slots=True)
class RequestContextProviderRegistry:
    providers_by_implementation_id: dict[str, RequestContextPlanProvider] = field(default_factory=dict)

    @classmethod
    def from_registrations(
        cls,
        registrations: Iterable[RequestContextProviderRegistration],
    ) -> "RequestContextProviderRegistry":
        registry = cls()
        for registration in registrations:
            registry.register(registration)
        return registry

    def register(self, registration: RequestContextProviderRegistration) -> None:
        implementation_id = registration.implementation_id.strip()
        if not implementation_id:
            raise ValueError("request context provider implementation id is required")
        previous = self.providers_by_implementation_id.get(implementation_id)
        if previous is not None and previous is not registration.provider:
            raise ValueError(
                f"duplicate request context provider implementation id: {implementation_id}"
            )
        self.providers_by_implementation_id[implementation_id] = registration.provider

    def provider_for(self, implementation_id: str) -> RequestContextPlanProvider | None:
        return self.providers_by_implementation_id.get(implementation_id)

    def has(self, implementation_id: str) -> bool:
        return implementation_id in self.providers_by_implementation_id


_DEFAULT_REQUEST_CONTEXT_PROVIDER_REGISTRY: RequestContextProviderRegistry | None = None


def default_request_context_provider_registry() -> RequestContextProviderRegistry:
    global _DEFAULT_REQUEST_CONTEXT_PROVIDER_REGISTRY
    if _DEFAULT_REQUEST_CONTEXT_PROVIDER_REGISTRY is None:
        registrations = [
            *(
                RequestContextProviderRegistration(
                    implementation_id=implementation_id,
                    provider=cast(RequestContextPlanProvider, provider),
                )
                for implementation_id, provider in built_in_generic_provider_registrations()
            ),
            *(
                RequestContextProviderRegistration(
                    implementation_id=implementation_id,
                    provider=cast(RequestContextPlanProvider, provider),
                )
                for implementation_id, provider in built_in_blueprint_provider_registrations()
            ),
        ]
        _DEFAULT_REQUEST_CONTEXT_PROVIDER_REGISTRY = RequestContextProviderRegistry.from_registrations(
            registrations
        )
    return _DEFAULT_REQUEST_CONTEXT_PROVIDER_REGISTRY


def build_request_context_plan(
    *,
    workspace_root: Path,
    request: StageRunRequest,
    compiled_plan: CompiledRunPlan | None,
) -> RequestContextRenderPlan:
    authority = resolve_request_context_authority(
        request=request,
        compiled_plan=compiled_plan,
    )
    provider = default_request_context_provider_registry().provider_for(
        authority.provider_python_registry_id
    )
    if provider is None:
        raise ValueError(
            "request context provider asset "
            f"{authority.provider_id} declares python registry id "
            f"{authority.provider_python_registry_id!r}, but no registered runtime implementation exists"
        )
    return provider(workspace_root, request, authority, compiled_plan)


def validate_stage_request_context_provider_implementation(
    *,
    stage_plan: MaterializedGraphNodePlan,
    compiled_plan: CompiledRunPlan | None,
) -> None:
    if compiled_plan is None:
        return

    profiles_by_id = _as_mapping(getattr(compiled_plan, "request_context_profiles_by_id", {}))
    providers_by_id = _as_mapping(getattr(compiled_plan, "request_context_providers_by_id", {}))
    if not profiles_by_id or not providers_by_id:
        return

    profile_id = stage_plan.request_context_profile_id or f"{stage_plan.stage_kind_id}.default"
    raw_profile = profiles_by_id.get(profile_id)
    if raw_profile is None:
        raise ValueError(
            f"compiled request context profile {profile_id!r} is unavailable for node {stage_plan.node_id}"
        )
    provider_id = str(getattr(raw_profile, "provider_id"))
    raw_provider = providers_by_id.get(provider_id)
    if raw_provider is None:
        raise ValueError(
            f"compiled request context provider {provider_id!r} is unavailable for node {stage_plan.node_id}"
        )
    implementation_id = str(getattr(raw_provider, "python_registry_id", "")).strip()
    if not implementation_id:
        raise ValueError(
            "request context provider asset "
            f"{provider_id} declares an empty python registry id"
        )
    if default_request_context_provider_registry().provider_for(implementation_id) is None:
        raise ValueError(
            "request context provider asset "
            f"{provider_id} declares python registry id "
            f"{implementation_id!r}, but no registered runtime implementation exists"
        )


def resolve_request_context_authority(
    *,
    request: StageRunRequest,
    compiled_plan: CompiledRunPlan | None,
) -> RequestContextAuthority:
    (
        profiles_by_id,
        providers_by_id,
        render_plans_by_id,
    ) = _request_context_assets_for_request(
        compiled_plan,
        request_compiled_plan_id=request.compiled_plan_id,
    )
    node = _compiled_node_for_request(compiled_plan, request=request)
    profile_id = (
        str(getattr(node, "request_context_profile_id"))
        if node is not None and getattr(node, "request_context_profile_id", None) is not None
        else request.request_context_profile_id
        or f"{request.stage_kind_id}.default"
    )
    raw_profile = profiles_by_id.get(profile_id)
    if raw_profile is None:
        raise ValueError(f"request context profile {profile_id!r} is unavailable")
    profile = cast(RequestContextProfileDefinition, raw_profile)
    render_plan_id = (
        str(getattr(node, "context_render_plan_id"))
        if node is not None and getattr(node, "context_render_plan_id", None) is not None
        else request.context_render_plan_id
        or profile.primary_render_plan_id
    )
    raw_render_plan = render_plans_by_id.get(render_plan_id)
    if raw_render_plan is None:
        raise ValueError(f"request context render plan {render_plan_id!r} is unavailable")
    render_plan = cast(RequestContextRenderPlanDefinition, raw_render_plan)
    raw_provider = providers_by_id.get(profile.provider_id)
    if raw_provider is None:
        raise ValueError(
            "request context provider "
            f"{profile.provider_id!r} is unavailable for profile {profile.profile_id}"
        )
    provider = cast(RequestContextProviderDefinition, raw_provider)
    provider_python_registry_id = provider.python_registry_id.strip()
    if not provider_python_registry_id:
        raise ValueError(
            "request context provider "
            f"{provider.provider_id} declares an empty python registry id"
        )
    return RequestContextAuthority(
        profile_id=profile.profile_id,
        render_plan_id=render_plan.render_plan_id,
        provider_id=provider.provider_id,
        provider_python_registry_id=provider_python_registry_id,
        profile=profile,
        provider=provider,
        render_plan=render_plan,
    )


def _request_context_assets_for_request(
    compiled_plan: CompiledRunPlan | None,
    *,
    request_compiled_plan_id: str,
) -> tuple[
    Mapping[str, object],
    Mapping[str, object],
    Mapping[str, object],
]:
    if compiled_plan is not None:
        profiles = _as_mapping(getattr(compiled_plan, "request_context_profiles_by_id", {}))
        providers = _as_mapping(getattr(compiled_plan, "request_context_providers_by_id", {}))
        render_plans = _as_mapping(getattr(compiled_plan, "request_context_render_plans_by_id", {}))
        if profiles and providers and render_plans:
            if compiled_plan.compiled_plan_id != request_compiled_plan_id:
                raise ValueError(
                    "request context compiled plan mismatch: "
                    f"request references {request_compiled_plan_id}, "
                    f"but request context authority came from {compiled_plan.compiled_plan_id}"
                )
            return profiles, providers, render_plans
    return _packaged_request_context_assets()


def _compiled_node_for_request(
    compiled_plan: CompiledRunPlan | None,
    *,
    request: StageRunRequest,
) -> object | None:
    if compiled_plan is None:
        return None
    graphs_by_plane = _as_mapping(getattr(compiled_plan, "graphs_by_plane", {}))
    graph = graphs_by_plane.get(request.plane)
    if graph is not None:
        node = _graph_node_for_request(graph, request=request)
        if node is not None:
            return node
    for graph_attr in ("execution_graph", "planning_graph", "learning_graph"):
        graph = getattr(compiled_plan, graph_attr, None)
        if graph is None:
            continue
        node = _graph_node_for_request(graph, request=request)
        if node is not None:
            return node
    return None


def _graph_node_for_request(
    graph: object,
    *,
    request: StageRunRequest,
) -> object | None:
    for node in tuple(getattr(graph, "nodes", ()) or ()):
        if getattr(node, "plane", None) != request.plane:
            continue
        if getattr(node, "node_id", None) == request.node_id:
            return cast(object, node)
    for node in tuple(getattr(graph, "nodes", ()) or ()):
        if getattr(node, "plane", None) != request.plane:
            continue
        if getattr(node, "stage_kind_id", None) == request.stage_kind_id:
            return cast(object, node)
    return None


@lru_cache(maxsize=1)
def _packaged_request_context_assets() -> tuple[
    Mapping[str, RequestContextProfileDefinition],
    Mapping[str, RequestContextProviderDefinition],
    Mapping[str, RequestContextRenderPlanDefinition],
]:
    profiles_by_id = {
        profile.profile_id: profile
        for profile in discover_request_context_profile_definitions()
    }
    providers_by_id = {
        provider.provider_id: provider
        for provider in discover_request_context_provider_definitions()
    }
    render_plans_by_id = {
        render_plan.render_plan_id: render_plan
        for render_plan in discover_request_context_render_plan_definitions()
    }
    return profiles_by_id, providers_by_id, render_plans_by_id


def _as_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    return {}


__all__ = [
    "RequestContextProviderRegistration",
    "RequestContextProviderRegistry",
    "build_request_context_plan",
    "default_request_context_provider_registry",
    "validate_stage_request_context_provider_implementation",
]
