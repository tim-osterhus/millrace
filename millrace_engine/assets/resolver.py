"""Workspace/package asset resolution helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any

from ..baseline_assets import (
    iter_packaged_baseline_family_files,
    packaged_baseline_asset,
    packaged_baseline_bundle_version,
    packaged_baseline_file_entry,
)


class AssetResolutionError(RuntimeError):
    """Raised when an asset cannot be resolved deterministically."""


class AssetSourceKind(str, Enum):
    """Where a resolved asset came from."""

    WORKSPACE = "workspace"
    PACKAGE = "package"


@dataclass(frozen=True, slots=True)
class ResolvedAsset:
    """One resolved asset with provenance metadata."""

    requested_path: Path
    workspace_path: Path
    source_kind: AssetSourceKind
    relative_path: PurePosixPath | None = None
    family: str | None = None
    category: str | None = None
    bundle_version: str | None = None

    @property
    def resolved_ref(self) -> str:
        if self.source_kind is AssetSourceKind.PACKAGE and self.relative_path is not None:
            return f"package:{self.relative_path.as_posix()}"
        if self.relative_path is not None:
            return f"workspace:{self.relative_path.as_posix()}"
        return f"workspace:{self.workspace_path.as_posix()}"

    @property
    def prompt_path(self) -> Path | None:
        if self.source_kind is AssetSourceKind.WORKSPACE:
            return self.workspace_path
        return None

    def read_text(self, *, encoding: str = "utf-8") -> str:
        if self.source_kind is AssetSourceKind.WORKSPACE:
            return self.workspace_path.read_text(encoding=encoding)
        if self.relative_path is None:
            raise AssetResolutionError("package-backed asset is missing a relative path")
        return packaged_baseline_asset(self.relative_path.as_posix()).read_text(encoding=encoding)

    def to_payload(self) -> dict[str, Any]:
        return {
            "requested_path": self.requested_path.as_posix(),
            "workspace_path": self.workspace_path.as_posix(),
            "relative_path": self.relative_path.as_posix() if self.relative_path is not None else None,
            "source_kind": self.source_kind.value,
            "resolved_ref": self.resolved_ref,
            "family": self.family,
            "category": self.category,
            "bundle_version": self.bundle_version,
        }


@dataclass(frozen=True, slots=True)
class AssetFamilyEntry:
    """One enumerated asset from an open overlay family."""

    family: str
    relative_path: PurePosixPath
    source_kind: AssetSourceKind
    workspace_path: Path
    category: str | None = None
    bundle_version: str | None = None

    @property
    def resolved_ref(self) -> str:
        prefix = "package" if self.source_kind is AssetSourceKind.PACKAGE else "workspace"
        return f"{prefix}:{self.relative_path.as_posix()}"

    def to_payload(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "relative_path": self.relative_path.as_posix(),
            "source_kind": self.source_kind.value,
            "workspace_path": self.workspace_path.as_posix(),
            "resolved_ref": self.resolved_ref,
            "category": self.category,
            "bundle_version": self.bundle_version,
        }


_OPEN_FAMILY_ROOTS: dict[str, PurePosixPath] = {
    "roles": PurePosixPath("agents/roles"),
    "skills": PurePosixPath("agents/skills"),
}


def _pure_relative_path(raw_path: str) -> PurePosixPath:
    raw_parts = raw_path.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise AssetResolutionError(f"invalid relative asset path: {raw_path!r}")
    path = PurePosixPath(raw_path)
    if path.is_absolute() or not path.parts:
        raise AssetResolutionError(f"invalid relative asset path: {raw_path!r}")
    return path


def _metadata_for(relative_path: PurePosixPath | None) -> tuple[str | None, str | None]:
    if relative_path is None:
        return None, None
    entry = packaged_baseline_file_entry(relative_path.as_posix())
    if entry is None:
        return None, None
    family = entry.get("family")
    category = entry.get("category")
    return (
        family if isinstance(family, str) and family else None,
        category if isinstance(category, str) and category else None,
    )


def _normalize_requested_path(workspace_root: Path, requested_path: Path | str) -> tuple[Path, Path, PurePosixPath | None]:
    raw = requested_path if isinstance(requested_path, Path) else Path(str(requested_path).strip())
    if not str(raw):
        raise AssetResolutionError("asset path may not be empty")

    if raw.is_absolute():
        candidate = raw.expanduser().resolve(strict=False)
        try:
            relative = PurePosixPath(candidate.relative_to(workspace_root).as_posix())
        except ValueError:
            relative = None
        return candidate, candidate, relative

    relative = _pure_relative_path(raw.as_posix())
    candidate = (workspace_root / raw).expanduser().resolve(strict=False)
    return raw, candidate, relative


class AssetResolver:
    """Resolve workspace assets with packaged baseline fallback."""

    def __init__(self, workspace_root: Path | str) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve(strict=False)
        self.bundle_version = packaged_baseline_bundle_version()

    def resolve_file(self, requested_path: Path | str) -> ResolvedAsset:
        requested, workspace_path, relative = _normalize_requested_path(self.workspace_root, requested_path)

        if workspace_path.exists():
            if not workspace_path.is_file():
                raise AssetResolutionError(f"asset path is not a file: {workspace_path}")
            family, category = _metadata_for(relative)
            return ResolvedAsset(
                requested_path=requested,
                workspace_path=workspace_path,
                relative_path=relative,
                source_kind=AssetSourceKind.WORKSPACE,
                family=family,
                category=category,
                bundle_version=self.bundle_version,
            )

        if relative is not None:
            packaged_entry = packaged_baseline_file_entry(relative.as_posix())
            if packaged_entry is not None:
                asset = packaged_baseline_asset(relative.as_posix())
                if not asset.is_file():
                    raise AssetResolutionError(f"packaged asset is not a file: {relative.as_posix()}")
                family, category = _metadata_for(relative)
                return ResolvedAsset(
                    requested_path=requested,
                    workspace_path=workspace_path,
                    relative_path=relative,
                    source_kind=AssetSourceKind.PACKAGE,
                    family=family,
                    category=category,
                    bundle_version=self.bundle_version,
                )

        if relative is not None:
            raise AssetResolutionError(
                f"asset is missing from workspace and package: {relative.as_posix()}"
            )
        raise AssetResolutionError(f"workspace-only asset is missing: {workspace_path}")

    def resolve_ref(self, requested_ref: Path | str) -> ResolvedAsset:
        """Resolve a relative asset ref or an explicit workspace:/package: ref."""

        if isinstance(requested_ref, Path):
            return self.resolve_file(requested_ref)

        text = str(requested_ref).strip()
        if not text:
            raise AssetResolutionError("asset ref may not be empty")

        if text.startswith("workspace:"):
            relative = _pure_relative_path(text.removeprefix("workspace:"))
            workspace_path = self.workspace_root.joinpath(*relative.parts).resolve(strict=False)
            if not workspace_path.exists():
                raise AssetResolutionError(f"workspace-backed asset is missing: {relative.as_posix()}")
            if not workspace_path.is_file():
                raise AssetResolutionError(f"workspace asset is not a file: {workspace_path}")
            family, category = _metadata_for(relative)
            return ResolvedAsset(
                requested_path=Path(text),
                workspace_path=workspace_path,
                relative_path=relative,
                source_kind=AssetSourceKind.WORKSPACE,
                family=family,
                category=category,
                bundle_version=self.bundle_version,
            )

        if text.startswith("package:"):
            relative = _pure_relative_path(text.removeprefix("package:"))
            packaged_entry = packaged_baseline_file_entry(relative.as_posix())
            if packaged_entry is None:
                raise AssetResolutionError(f"packaged asset is missing: {relative.as_posix()}")
            asset = packaged_baseline_asset(relative.as_posix())
            if not asset.is_file():
                raise AssetResolutionError(f"packaged asset is not a file: {relative.as_posix()}")
            family, category = _metadata_for(relative)
            return ResolvedAsset(
                requested_path=Path(text),
                workspace_path=self.workspace_root.joinpath(*relative.parts),
                relative_path=relative,
                source_kind=AssetSourceKind.PACKAGE,
                family=family,
                category=category,
                bundle_version=self.bundle_version,
            )

        return self.resolve_file(text)

    def iter_open_family(self, family: str) -> tuple[AssetFamilyEntry, ...]:
        normalized = family.strip()
        root = _OPEN_FAMILY_ROOTS.get(normalized)
        if root is None:
            raise AssetResolutionError(f"unsupported overlay family: {family}")

        merged: dict[str, AssetFamilyEntry] = {}
        for entry in iter_packaged_baseline_family_files(normalized):
            raw_path = entry.get("path")
            if not isinstance(raw_path, str) or not raw_path:
                raise AssetResolutionError(f"packaged family entry is missing a path: {family}")
            relative = _pure_relative_path(raw_path)
            category = entry.get("category")
            merged[relative.as_posix()] = AssetFamilyEntry(
                family=normalized,
                relative_path=relative,
                source_kind=AssetSourceKind.PACKAGE,
                workspace_path=self.workspace_root.joinpath(*relative.parts),
                category=category if isinstance(category, str) and category else None,
                bundle_version=self.bundle_version,
            )

        workspace_root = self.workspace_root.joinpath(*root.parts)
        if workspace_root.exists():
            if not workspace_root.is_dir():
                raise AssetResolutionError(f"overlay family root is not a directory: {workspace_root}")
            for path in sorted(item for item in workspace_root.rglob("*") if item.is_file()):
                relative = root / PurePosixPath(path.relative_to(workspace_root).as_posix())
                _, category = _metadata_for(relative)
                merged[relative.as_posix()] = AssetFamilyEntry(
                    family=normalized,
                    relative_path=relative,
                    source_kind=AssetSourceKind.WORKSPACE,
                    workspace_path=path.resolve(strict=False),
                    category=category,
                    bundle_version=self.bundle_version,
                )

        return tuple(merged[key] for key in sorted(merged))
