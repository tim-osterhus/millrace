"""Request-context provider registry and compiled-authority resolution."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import import_module
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
from millrace_ai.assets.extensions import discover_extension_package_manifests
from millrace_ai.extensions import ExtensionItemKind
from millrace_ai.runners import StageRunRequest

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
        ]
        _DEFAULT_REQUEST_CONTEXT_PROVIDER_REGISTRY = RequestContextProviderRegistry.from_registrations(
            registrations
        )
    return _DEFAULT_REQUEST_CONTEXT_PROVIDER_REGISTRY


def _ensure_extension_context_providers(
    registry: RequestContextProviderRegistry,
    *,
    authority: RequestContextAuthority,
) -> None:
    owned_provider, owned_render_plan = _request_context_ownership(authority)
    if owned_provider is None and owned_render_plan is None:
        return
    if owned_provider is None or owned_render_plan is None:
        raise ValueError(_request_context_ownership_error(authority))
    if owned_provider.package_id != owned_render_plan.package_id:
        raise ValueError(
            "request context provider asset "
            f"{authority.provider_id} and render plan {authority.render_plan_id} "
            "are owned by different extension packages: "
            f"{owned_provider.package_id!r} and {owned_render_plan.package_id!r}"
        )

    module = import_module(owned_provider.implementation_path)
    registrations = _request_context_provider_registrations(module)
    if registrations is None:
        raise ValueError(
            "request context provider asset "
            f"{authority.provider_id} is owned by extension package "
            f"{owned_provider.package_id!r}, but implementation module "
            f"{owned_provider.implementation_path!r} does not expose provider registrations"
        )

    for implementation_id, provider in registrations():
        if not registry.has(implementation_id):
            registry.register(
                RequestContextProviderRegistration(
                    implementation_id=implementation_id,
                    provider=cast(RequestContextPlanProvider, provider),
                )
            )


def _provider_for_authority(
    registry: RequestContextProviderRegistry,
    authority: RequestContextAuthority,
) -> RequestContextPlanProvider | None:
    provider = registry.provider_for(authority.provider_python_registry_id)
    if provider is not None:
        return provider
    owned_provider, owned_render_plan = _request_context_ownership(authority)
    if owned_provider is None and owned_render_plan is None:
        return None
    if owned_provider is None or owned_render_plan is None:
        raise ValueError(_request_context_ownership_error(authority))
    if owned_provider.package_id != owned_render_plan.package_id:
        raise ValueError(
            "request context provider asset "
            f"{authority.provider_id} and render plan {authority.render_plan_id} "
            "are owned by different extension packages: "
            f"{owned_provider.package_id!r} and {owned_render_plan.package_id!r}"
        )
    if owned_provider is not None and owned_render_plan is not None:
        _ensure_extension_context_providers(registry, authority=authority)
        return registry.provider_for(authority.provider_python_registry_id)
    return None


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
    if compiled_plan is None and _has_extension_owned_context(authority):
        raise ValueError(
            "request context provider asset "
            f"{authority.provider_id} requires compiled plan authority before loading "
            f"extension-owned implementation {authority.provider_python_registry_id!r}"
        )
    registry = default_request_context_provider_registry()
    provider = _provider_for_authority(registry, authority)
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
    render_plans_by_id = _as_mapping(
        getattr(compiled_plan, "request_context_render_plans_by_id", {})
    )
    if not profiles_by_id or not providers_by_id or not render_plans_by_id:
        return

    profile_id = stage_plan.request_context_profile_id
    if profile_id is None:
        raise ValueError(
            f"compiled graph node {stage_plan.node_id} is missing request context profile authority"
        )
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
    render_plan_id = stage_plan.context_render_plan_id
    if render_plan_id is None:
        raise ValueError(
            f"compiled graph node {stage_plan.node_id} is missing context render plan authority"
        )
    raw_render_plan = render_plans_by_id.get(render_plan_id)
    if raw_render_plan is None:
        raise ValueError(
            f"compiled request context render plan {render_plan_id!r} is unavailable for node {stage_plan.node_id}"
        )
    implementation_id = str(getattr(raw_provider, "python_registry_id", "")).strip()
    if not implementation_id:
        raise ValueError(
            "request context provider asset "
            f"{provider_id} declares an empty python registry id"
        )
    registry = default_request_context_provider_registry()
    provider = registry.provider_for(implementation_id)
    authority = RequestContextAuthority(
        profile_id=profile_id,
        render_plan_id=render_plan_id,
        provider_id=provider_id,
        provider_python_registry_id=implementation_id,
        profile=cast(RequestContextProfileDefinition, raw_profile),
        provider=cast(RequestContextProviderDefinition, raw_provider),
        render_plan=cast(RequestContextRenderPlanDefinition, raw_render_plan),
    )
    if provider is None and _has_extension_owned_context(authority):
        _ensure_extension_context_providers(registry, authority=authority)
        provider = registry.provider_for(implementation_id)
    if provider is None:
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
    node_profile_id = (
        str(getattr(node, "request_context_profile_id"))
        if node is not None and getattr(node, "request_context_profile_id", None) is not None
        else None
    )
    profile_id = node_profile_id or request.request_context_profile_id
    if profile_id is None:
        authority_source = (
            f"compiled graph node {request.node_id}"
            if node is not None
            else f"stage request {request.request_id}"
        )
        raise ValueError(
            f"{authority_source} is missing request context profile authority; "
            "recompile the workspace plan from graph or stage-kind assets that declare "
            "request_context_profile_id"
        )
    raw_profile = profiles_by_id.get(profile_id)
    if raw_profile is None:
        raise ValueError(f"request context profile {profile_id!r} is unavailable")
    profile = cast(RequestContextProfileDefinition, raw_profile)
    node_render_plan_id = (
        str(getattr(node, "context_render_plan_id"))
        if node is not None and getattr(node, "context_render_plan_id", None) is not None
        else None
    )
    render_plan_id = node_render_plan_id or request.context_render_plan_id
    if render_plan_id is None:
        authority_source = (
            f"compiled graph node {request.node_id}"
            if node is not None
            else f"stage request {request.request_id}"
        )
        raise ValueError(
            f"{authority_source} is missing context render plan authority; "
            "recompile the workspace plan from graph or stage-kind assets that declare "
            "context_render_plan_id"
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


@dataclass(frozen=True, slots=True)
class _OwnedRequestContextItem:
    package_id: str
    implementation_path: str


def _request_context_ownership(
    authority: RequestContextAuthority,
) -> tuple[_OwnedRequestContextItem | None, _OwnedRequestContextItem | None]:
    return (
        _extension_owned_request_context_item(
            item_kind=ExtensionItemKind.CONTEXT_PROVIDER,
            item_id=authority.provider_id,
        ),
        _extension_owned_request_context_item(
            item_kind=ExtensionItemKind.REQUEST_CONTEXT_RENDER_PLAN,
            item_id=authority.render_plan_id,
        ),
    )


def _has_extension_owned_context(authority: RequestContextAuthority) -> bool:
    return (
        all(item is not None for item in _request_context_ownership(authority))
    )


def _request_context_ownership_error(authority: RequestContextAuthority) -> str:
    owned_provider, owned_render_plan = _request_context_ownership(authority)
    missing: list[str] = []
    if owned_provider is None:
        missing.append(f"provider {authority.provider_id!r}")
    if owned_render_plan is None:
        missing.append(f"render plan {authority.render_plan_id!r}")
    return (
        "request context metadata partially selects extension ownership; "
        "missing extension manifest ownership for "
        + " and ".join(missing)
    )


def _extension_owned_request_context_item(
    *,
    item_kind: ExtensionItemKind,
    item_id: str,
) -> _OwnedRequestContextItem | None:
    for manifest in discover_extension_package_manifests():
        for item in manifest.items:
            if item.item_kind is item_kind and item.item_id == item_id:
                return _OwnedRequestContextItem(
                    package_id=manifest.package_id,
                    implementation_path=item.implementation_path,
                )
    return None


def _request_context_provider_registrations(module: object) -> object | None:
    for attr_name in (
        "built_in_request_context_provider_registrations",
        "request_context_provider_registrations",
        "built_in_provider_registrations",
    ):
        registrations = getattr(module, attr_name, None)
        if registrations is not None:
            return registrations
    return None


__all__ = [
    "RequestContextProviderRegistration",
    "RequestContextProviderRegistry",
    "build_request_context_plan",
    "default_request_context_provider_registry",
    "validate_stage_request_context_provider_implementation",
]
