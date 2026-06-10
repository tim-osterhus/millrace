"""Extension manifest asset loading.

Discovers and loads extension package manifests from the asset registry.
Extensions follow the same JSON-asset discovery pattern as stage kinds,
workflow primitives, and other shipped assets.

ADRs: ADR-0015.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from millrace_ai.errors import AssetValidationError
from millrace_ai.extensions import ExtensionPackageManifest

ASSETS_ROOT = Path(__file__).resolve().parent
EXTENSIONS_REGISTRY_ROOT = Path("registry/extensions")


class ExtensionAssetError(AssetValidationError):
    """Raised when extension manifest assets cannot be resolved or validated."""


def discover_extension_package_manifests(
    *,
    assets_root: Path | None = None,
) -> tuple[ExtensionPackageManifest, ...]:
    """Discover all extension package manifests in the asset registry."""
    root = _resolve_assets_root(assets_root)
    registry_root = root / EXTENSIONS_REGISTRY_ROOT
    if not registry_root.is_dir():
        return ()

    discovered: list[ExtensionPackageManifest] = []
    seen_ids: set[str] = set()

    for asset_path in sorted(
        candidate for candidate in registry_root.rglob("*.json") if candidate.is_file()
    ):
        manifest = _load_extension_manifest_at_path(asset_path)
        if manifest.package_id in seen_ids:
            raise ExtensionAssetError(
                f"Duplicate extension package id: {manifest.package_id} "
                f"(found in {asset_path})"
            )
        seen_ids.add(manifest.package_id)
        discovered.append(manifest)

    return tuple(
        sorted(discovered, key=lambda manifest: manifest.package_id)
    )


def load_extension_package_manifest(
    package_id: str,
    *,
    assets_root: Path | None = None,
) -> ExtensionPackageManifest:
    """Load a specific extension package manifest by id."""
    manifests = discover_extension_package_manifests(assets_root=assets_root)
    for manifest in manifests:
        if manifest.package_id == package_id:
            return manifest
    raise ExtensionAssetError(f"Unknown extension package id: {package_id}")


def _load_extension_manifest_at_path(path: Path) -> ExtensionPackageManifest:
    payload = _load_json_asset(path)
    _validate_top_level_manifest(payload, path)

    try:
        return ExtensionPackageManifest.from_dict(payload)
    except (ValueError, TypeError, KeyError) as exc:
        raise ExtensionAssetError(
            f"Invalid extension package manifest in {path}: {exc}"
        ) from exc


def _validate_top_level_manifest(payload: dict[str, Any], path: Path) -> None:
    kind = payload.get("kind")
    if kind != "extension_package_manifest":
        raise ExtensionAssetError(
            f"Extension manifest in {path} must declare kind='extension_package_manifest', "
            f"got {kind!r}"
        )
    schema_version = payload.get("schema_version")
    if schema_version != "1.0":
        raise ExtensionAssetError(
            f"Extension manifest in {path} must declare schema_version='1.0', "
            f"got {schema_version!r}"
        )
    if "package_id" not in payload:
        raise ExtensionAssetError(
            f"Extension manifest in {path} missing required field: package_id"
        )


def _load_json_asset(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExtensionAssetError(f"Cannot read extension asset: {path}") from exc

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExtensionAssetError(f"Invalid JSON in extension asset: {path}") from exc

    if not isinstance(payload, dict):
        raise ExtensionAssetError(f"Extension asset must be a JSON object: {path}")

    return payload


def _resolve_assets_root(assets_root: Path | None) -> Path:
    if assets_root is None:
        return ASSETS_ROOT
    return Path(assets_root)


__all__ = [
    "ASSETS_ROOT",
    "EXTENSIONS_REGISTRY_ROOT",
    "ExtensionAssetError",
    "discover_extension_package_manifests",
    "load_extension_package_manifest",
]
