"""Request-context asset ownership tests."""

from __future__ import annotations

from millrace_ai.assets import (
    discover_request_context_profile_definitions,
    discover_request_context_provider_definitions,
    discover_request_context_render_plan_definitions,
)
from millrace_ai.assets.extensions import discover_extension_package_manifests
from millrace_ai.extensions import ExtensionDomain, ExtensionItemKind


def _owned_ids(item_kind: ExtensionItemKind, domain: ExtensionDomain) -> set[str]:
    ids: set[str] = set()
    for manifest in discover_extension_package_manifests():
        if manifest.domain is not domain:
            continue
        ids.update(
            item.item_id
            for item in manifest.items
            if item.item_kind is item_kind
        )
    return ids


def test_blueprint_context_assets_are_manifest_owned_as_a_set() -> None:
    blueprint_provider_ids = _owned_ids(
        ExtensionItemKind.CONTEXT_PROVIDER,
        ExtensionDomain.BLUEPRINT,
    )
    blueprint_profile_ids = _owned_ids(
        ExtensionItemKind.REQUEST_CONTEXT_PROFILE,
        ExtensionDomain.BLUEPRINT,
    )
    blueprint_render_plan_ids = _owned_ids(
        ExtensionItemKind.REQUEST_CONTEXT_RENDER_PLAN,
        ExtensionDomain.BLUEPRINT,
    )

    providers_by_id = {
        provider.provider_id: provider
        for provider in discover_request_context_provider_definitions()
    }
    profiles_by_id = {
        profile.profile_id: profile
        for profile in discover_request_context_profile_definitions()
    }
    render_plans_by_id = {
        render_plan.render_plan_id: render_plan
        for render_plan in discover_request_context_render_plan_definitions()
    }

    assert blueprint_provider_ids <= providers_by_id.keys()
    assert blueprint_profile_ids <= profiles_by_id.keys()
    assert blueprint_render_plan_ids <= render_plans_by_id.keys()
    for profile_id in blueprint_profile_ids:
        assert profiles_by_id[profile_id].provider_id in blueprint_provider_ids


def test_generic_context_assets_are_not_owned_by_blueprint_manifest() -> None:
    blueprint_provider_ids = _owned_ids(
        ExtensionItemKind.CONTEXT_PROVIDER,
        ExtensionDomain.BLUEPRINT,
    )
    blueprint_render_plan_ids = _owned_ids(
        ExtensionItemKind.REQUEST_CONTEXT_RENDER_PLAN,
        ExtensionDomain.BLUEPRINT,
    )

    assert "generic.active_work_item" not in blueprint_provider_ids
    assert "generic.closure_target" not in blueprint_provider_ids
    assert "stage_request.default.v1" not in blueprint_render_plan_ids
    assert "closure_target.default.v1" not in blueprint_render_plan_ids
