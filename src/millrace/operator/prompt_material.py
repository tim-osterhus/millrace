"""Selected prompt and skill material projection for runner invocation."""

from __future__ import annotations

from collections.abc import Mapping

from millrace.contracts.compiled_plan import (
    AssetDeclaration,
    AuthorityValue,
    SelectedCompiledPlan,
    SelectedWorkflowPackageAssetPin,
    freeze_authority_mapping,
)
from millrace.contracts.runner import RunnerDispatchEnvelope
from millrace.contracts.workflow_package import asset_digest_for_bytes

_INLINE_ENTRYPOINT_ASSET_KINDS = frozenset(("prompt",))
_INLINE_SKILL_ASSET_KINDS = frozenset(("skill",))
_PACKAGE_ENTRYPOINT_ASSET_KINDS = frozenset(("entrypoint_prompt",))
_PACKAGE_SKILL_ASSET_KINDS = frozenset(("stage_skill",))


class SelectedAssetMaterializationError(ValueError):
    """Raised when selected prompt/skill material cannot be projected safely."""


def build_selected_asset_material(
    *,
    selected_plan: SelectedCompiledPlan,
    dispatch_envelope: RunnerDispatchEnvelope,
) -> Mapping[str, AuthorityValue]:
    """Build frozen selected asset material for a runner invocation request."""

    if not isinstance(selected_plan, SelectedCompiledPlan):
        raise TypeError("selected_plan must be SelectedCompiledPlan")
    if not isinstance(dispatch_envelope, RunnerDispatchEnvelope):
        raise TypeError("dispatch_envelope must be RunnerDispatchEnvelope")

    referenced_roles = _dispatch_referenced_asset_roles(dispatch_envelope)
    referenced_ids = frozenset(referenced_roles)
    assets_by_id = _referenced_assets_by_id(
        selected_plan.assets,
        referenced_ids=referenced_ids,
    )
    pins_by_id = _referenced_package_pins_by_id(
        selected_plan.workflow_package_pin.selected_asset_pins
        if selected_plan.workflow_package_pin is not None
        else (),
        referenced_ids=referenced_ids,
        require_pins=selected_plan.workflow_package_pin is not None,
    )
    package_backed = selected_plan.workflow_package_pin is not None

    material: dict[str, object] = {}
    for asset_id, role in referenced_roles.items():
        asset = assets_by_id[asset_id]
        body = _selected_text_body(asset, asset_id)
        _validate_asset_kind(
            asset,
            asset_id=asset_id,
            role=role,
            package_backed=package_backed,
        )

        content_digest: str | None = None
        source = "selected_plan_inline"
        if selected_plan.workflow_package_pin is not None:
            pin = pins_by_id[asset_id]
            content_digest = pin.content_digest
            actual_digest = asset_digest_for_bytes(body.encode("utf-8"))
            if actual_digest != content_digest:
                raise SelectedAssetMaterializationError(
                    "selected package asset digest mismatch: "
                    f"{asset_id} expected {content_digest} got {actual_digest}"
                )
            source = "selected_package_pin"

        material[asset_id] = {
            "asset_id": asset_id,
            "asset_kind": asset.asset_kind,
            "body": body,
            "content_digest": content_digest,
            "source": source,
        }

    if set(material) != referenced_ids:
        raise SelectedAssetMaterializationError(
            "selected asset material output did not match dispatch references"
        )
    return freeze_authority_mapping(material)


def _dispatch_referenced_asset_roles(
    dispatch_envelope: RunnerDispatchEnvelope,
) -> dict[str, str]:
    roles: dict[str, str] = {}
    if dispatch_envelope.entrypoint_asset_id is not None:
        roles[dispatch_envelope.entrypoint_asset_id] = "entrypoint"
    for asset_id in dispatch_envelope.skill_asset_ids:
        existing_role = roles.get(asset_id)
        if existing_role is not None and existing_role != "skill":
            raise SelectedAssetMaterializationError(
                f"asset referenced for multiple dispatch roles: {asset_id}"
            )
        roles[asset_id] = "skill"
    return roles


def _referenced_assets_by_id(
    assets: tuple[AssetDeclaration, ...],
    *,
    referenced_ids: frozenset[str],
) -> dict[str, AssetDeclaration]:
    matches: dict[str, list[AssetDeclaration]] = {
        asset_id: [] for asset_id in referenced_ids
    }
    for asset in assets:
        asset_id = str(asset.id)
        if asset_id in referenced_ids:
            matches[asset_id].append(asset)

    assets_by_id: dict[str, AssetDeclaration] = {}
    for asset_id, found in matches.items():
        if not found:
            raise SelectedAssetMaterializationError(
                f"missing selected asset declaration: {asset_id}"
            )
        if len(found) > 1:
            raise SelectedAssetMaterializationError(
                f"duplicate selected asset declaration: {asset_id}"
            )
        assets_by_id[asset_id] = found[0]
    return assets_by_id


def _referenced_package_pins_by_id(
    pins: tuple[SelectedWorkflowPackageAssetPin, ...],
    *,
    referenced_ids: frozenset[str],
    require_pins: bool,
) -> dict[str, SelectedWorkflowPackageAssetPin]:
    if not require_pins:
        return {}

    matches: dict[str, list[SelectedWorkflowPackageAssetPin]] = {
        asset_id: [] for asset_id in referenced_ids
    }
    for pin in pins:
        if pin.asset_id in referenced_ids:
            matches[pin.asset_id].append(pin)

    pins_by_id: dict[str, SelectedWorkflowPackageAssetPin] = {}
    for asset_id, found in matches.items():
        if not found:
            raise SelectedAssetMaterializationError(
                f"missing selected package asset pin: {asset_id}"
            )
        if len(found) > 1:
            raise SelectedAssetMaterializationError(
                f"duplicate selected package asset pin: {asset_id}"
            )
        pins_by_id[asset_id] = found[0]
    return pins_by_id


def _selected_text_body(asset: AssetDeclaration, asset_id: str) -> str:
    body = asset.body
    if not isinstance(body, str):
        raise SelectedAssetMaterializationError(
            f"selected asset body is not text: {asset_id}"
        )
    if not body.strip():
        raise SelectedAssetMaterializationError(
            f"selected asset body is blank: {asset_id}"
        )
    return body


def _validate_asset_kind(
    asset: AssetDeclaration,
    *,
    asset_id: str,
    role: str,
    package_backed: bool,
) -> None:
    if package_backed:
        allowed_kinds = (
            _PACKAGE_ENTRYPOINT_ASSET_KINDS
            if role == "entrypoint"
            else _PACKAGE_SKILL_ASSET_KINDS
        )
    else:
        allowed_kinds = (
            _INLINE_ENTRYPOINT_ASSET_KINDS
            if role == "entrypoint"
            else _INLINE_SKILL_ASSET_KINDS
        )
    if asset.asset_kind not in allowed_kinds:
        raise SelectedAssetMaterializationError(
            f"asset kind {asset.asset_kind!r} is not valid for {role} material: "
            f"{asset_id}"
        )


__all__ = (
    "SelectedAssetMaterializationError",
    "build_selected_asset_material",
)
