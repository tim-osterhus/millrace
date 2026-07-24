"""Workflow package manifest records, canonical bytes, and digest helpers."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Any, ClassVar, TypeAlias, cast

ManifestValue: TypeAlias = (
    str
    | int
    | bool
    | None
    | tuple["ManifestValue", ...]
    | Mapping[str, "ManifestValue"]
)

CanonicalManifestValue: TypeAlias = (
    str
    | int
    | bool
    | None
    | list["CanonicalManifestValue"]
    | dict[str, "CanonicalManifestValue"]
)

WORKFLOW_PACKAGE_MANIFEST_RECORD_KIND = "millrace.workflow_package_manifest"
WORKFLOW_PACKAGE_MANIFEST_FORMAT_VERSION = "1"
WORKFLOW_PACKAGE_CANONICALIZATION_ALGORITHM = "millrace-json-v1"
WORKFLOW_PACKAGE_HASH_ALGORITHM = "sha256"
WORKFLOW_PACKAGE_MANIFEST_DIGEST_DOMAIN = "millrace.wpkg.manifest.v1"
WORKFLOW_PACKAGE_ASSET_DIGEST_DOMAIN = "millrace.wpkg.asset.v1"

_MANIFEST_DIGEST_DOMAIN_BYTES = WORKFLOW_PACKAGE_MANIFEST_DIGEST_DOMAIN.encode(
    "utf-8"
) + b"\0"
_ASSET_DIGEST_DOMAIN_BYTES = WORKFLOW_PACKAGE_ASSET_DIGEST_DOMAIN.encode(
    "utf-8"
) + b"\0"

_ROOT_AUTHORITY_FIELDS = (
    "record_kind",
    "manifest_format_version",
    "package",
    "workflows",
    "assets",
    "dependencies",
    "compatibility",
    "canonicalization",
)

_PACKAGE_PROVENANCE_FIELDS = frozenset(
    (
        "source_kind",
        "publication_scope",
        "license",
        "repository_url",
        "source_ref",
        "display",
    )
)


class WorkflowPackageManifestCanonicalizationError(ValueError):
    """Raised when a workflow package manifest cannot be canonicalized."""


class UnsupportedWorkflowPackageManifestValue(ValueError):
    """Raised when a manifest record contains a non-canonical value type."""


def freeze_manifest_value(value: object) -> ManifestValue:
    if value is None or isinstance(value, (str, bool)):
        return value
    if type(value) is int:
        return value
    if isinstance(value, Mapping):
        for key in value:
            if not isinstance(key, str):
                raise UnsupportedWorkflowPackageManifestValue(
                    "manifest map keys must be strings"
                )
        frozen = {
            key: freeze_manifest_value(nested_value)
            for key, nested_value in value.items()
        }
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(freeze_manifest_value(item) for item in value)
    raise UnsupportedWorkflowPackageManifestValue(
        f"unsupported manifest value type: {type(value).__name__}"
    )


def freeze_manifest_mapping(value: Mapping[str, object]) -> Mapping[str, ManifestValue]:
    return cast(Mapping[str, ManifestValue], freeze_manifest_value(value))


def _freeze_sequence(value: Iterable[Any]) -> tuple[Any, ...]:
    return tuple(value)


@dataclass(frozen=True, slots=True)
class WorkflowPackageIdentity:
    record_kind: ClassVar[str] = "workflow_package_identity"
    schema_version: ClassVar[int] = 1

    package_id: str
    package_version: str
    package_format_version: str
    package_role: str
    publisher: str
    base_millrace_compatibility: str
    source_kind: str | None = None
    publication_scope: str | None = None
    license: str | None = None
    repository_url: str | None = None
    source_ref: str | None = None
    display: Mapping[str, ManifestValue] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "display",
            freeze_manifest_mapping(cast(Mapping[str, object], self.display or {})),
        )


@dataclass(frozen=True, slots=True)
class WorkflowPackageCompatibility:
    record_kind: ClassVar[str] = "workflow_package_compatibility"
    schema_version: ClassVar[int] = 1

    requirements: Mapping[str, ManifestValue]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "requirements",
            freeze_manifest_mapping(cast(Mapping[str, object], self.requirements)),
        )


@dataclass(frozen=True, slots=True)
class WorkflowPackageCanonicalization:
    record_kind: ClassVar[str] = "workflow_package_canonicalization"
    schema_version: ClassVar[int] = 1

    algorithm: str
    hash_algorithm: str


@dataclass(frozen=True, slots=True)
class WorkflowPackageDependency:
    record_kind: ClassVar[str] = "workflow_package_dependency"
    schema_version: ClassVar[int] = 1

    package_id: str
    version_constraint: str
    manifest_digest: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowPackageAssetRef:
    record_kind: ClassVar[str] = "workflow_package_asset_ref"
    schema_version: ClassVar[int] = 1

    asset_id: str
    content_digest: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowPackageWorkflow:
    record_kind: ClassVar[str] = "workflow_package_workflow"
    schema_version: ClassVar[int] = 1

    workflow_id: str
    workflow_version: str
    visibility: str
    entrypoints: tuple[str, ...]
    selected_authority: Mapping[str, ManifestValue]
    required_assets: tuple[WorkflowPackageAssetRef, ...]
    display: Mapping[str, ManifestValue] | None = None
    source_refs: tuple[str, ...] = ()
    required_dependencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "entrypoints", _freeze_sequence(self.entrypoints))
        object.__setattr__(
            self,
            "selected_authority",
            freeze_manifest_mapping(
                cast(Mapping[str, object], self.selected_authority)
            ),
        )
        object.__setattr__(
            self,
            "required_assets",
            _freeze_sequence(self.required_assets),
        )
        object.__setattr__(
            self,
            "display",
            freeze_manifest_mapping(cast(Mapping[str, object], self.display or {})),
        )
        object.__setattr__(self, "source_refs", _freeze_sequence(self.source_refs))
        object.__setattr__(
            self,
            "required_dependencies",
            _freeze_sequence(self.required_dependencies),
        )


@dataclass(frozen=True, slots=True)
class WorkflowPackageAsset:
    record_kind: ClassVar[str] = "workflow_package_asset"
    schema_version: ClassVar[int] = 1

    asset_id: str
    asset_kind: str
    media_type: str
    encoding: str
    content_digest: str
    byte_length: int
    package_path: str
    selection: str
    selected_authority_participation: str


@dataclass(frozen=True, slots=True)
class WorkflowPackageManifest:
    record_kind: ClassVar[str] = WORKFLOW_PACKAGE_MANIFEST_RECORD_KIND
    schema_version: ClassVar[int] = 1

    package: WorkflowPackageIdentity
    workflows: tuple[WorkflowPackageWorkflow, ...]
    assets: tuple[WorkflowPackageAsset, ...]
    dependencies: tuple[WorkflowPackageDependency, ...]
    compatibility: WorkflowPackageCompatibility
    canonicalization: WorkflowPackageCanonicalization
    manifest_format_version: str = WORKFLOW_PACKAGE_MANIFEST_FORMAT_VERSION
    manifest_digest: str | None = None
    non_authoritative_metadata: Mapping[str, ManifestValue] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workflows", _freeze_sequence(self.workflows))
        object.__setattr__(self, "assets", _freeze_sequence(self.assets))
        object.__setattr__(
            self,
            "dependencies",
            _freeze_sequence(self.dependencies),
        )
        object.__setattr__(
            self,
            "non_authoritative_metadata",
            freeze_manifest_mapping(
                cast(Mapping[str, object], self.non_authoritative_metadata or {})
            ),
        )


def canonical_manifest_bytes(
    manifest: WorkflowPackageManifest | Mapping[str, object],
) -> bytes:
    authority = _manifest_authority(manifest)
    try:
        serialized = json.dumps(
            _canonical_value(authority),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise WorkflowPackageManifestCanonicalizationError(
            "workflow package manifest is not canonical"
        ) from exc
    return serialized.encode("utf-8")


def manifest_digest_for_manifest(
    manifest: WorkflowPackageManifest | Mapping[str, object],
) -> str:
    digest = sha256(
        _MANIFEST_DIGEST_DOMAIN_BYTES + canonical_manifest_bytes(manifest)
    ).hexdigest()
    return f"sha256:{digest}"


def asset_digest_for_bytes(asset_bytes: bytes) -> str:
    if not isinstance(asset_bytes, bytes):
        raise TypeError("asset digests require exact bytes")
    digest = sha256(_ASSET_DIGEST_DOMAIN_BYTES + asset_bytes).hexdigest()
    return f"sha256:{digest}"


def _manifest_authority(
    manifest: WorkflowPackageManifest | Mapping[str, object],
) -> Mapping[str, object]:
    if isinstance(manifest, WorkflowPackageManifest):
        return _manifest_record_authority(manifest)
    return _manifest_mapping_authority(manifest)


def _manifest_record_authority(
    manifest: WorkflowPackageManifest,
) -> Mapping[str, object]:
    return {
        "record_kind": WORKFLOW_PACKAGE_MANIFEST_RECORD_KIND,
        "manifest_format_version": manifest.manifest_format_version,
        "package": _package_record_authority(manifest.package),
        "workflows": sorted(
            (_workflow_record_authority(workflow) for workflow in manifest.workflows),
            key=lambda workflow: cast(str, workflow["workflow_id"]),
        ),
        "assets": sorted(
            (_asset_record_authority(asset) for asset in manifest.assets),
            key=lambda asset: cast(str, asset["asset_id"]),
        ),
        "dependencies": sorted(
            (
                _dependency_record_authority(dependency)
                for dependency in manifest.dependencies
            ),
            key=lambda dependency: (
                cast(str, dependency["package_id"]),
                cast(str, dependency["version_constraint"]),
            ),
        ),
        "compatibility": manifest.compatibility.requirements,
        "canonicalization": {
            "algorithm": manifest.canonicalization.algorithm,
            "hash": manifest.canonicalization.hash_algorithm,
        },
    }


def _package_record_authority(package: WorkflowPackageIdentity) -> Mapping[str, object]:
    record: dict[str, object] = {
        "package_id": package.package_id,
        "package_version": package.package_version,
        "package_format_version": package.package_format_version,
        "package_role": package.package_role,
        "publisher": package.publisher,
        "base_millrace_compatibility": package.base_millrace_compatibility,
    }
    return record


def _workflow_record_authority(
    workflow: WorkflowPackageWorkflow,
) -> Mapping[str, object]:
    record: dict[str, object] = {
        "workflow_id": workflow.workflow_id,
        "workflow_version": workflow.workflow_version,
        "visibility": workflow.visibility,
        "entrypoints": workflow.entrypoints,
        "selected_authority": workflow.selected_authority,
        "required_assets": sorted(
            (
                _asset_ref_record_authority(asset_ref)
                for asset_ref in workflow.required_assets
            ),
            key=lambda asset_ref: cast(str, asset_ref["asset_id"]),
        ),
    }
    if workflow.required_dependencies:
        record["required_dependencies"] = workflow.required_dependencies
    return record


def _asset_ref_record_authority(
    asset_ref: WorkflowPackageAssetRef,
) -> Mapping[str, object]:
    record: dict[str, object] = {"asset_id": asset_ref.asset_id}
    if asset_ref.content_digest is not None:
        record["content_digest"] = asset_ref.content_digest
    return record


def _asset_record_authority(asset: WorkflowPackageAsset) -> Mapping[str, object]:
    return {
        "asset_id": asset.asset_id,
        "asset_kind": asset.asset_kind,
        "media_type": asset.media_type,
        "encoding": asset.encoding,
        "content_digest": asset.content_digest,
        "byte_length": asset.byte_length,
        "package_path": asset.package_path,
        "selection": asset.selection,
        "selected_authority_participation": (
            asset.selected_authority_participation
        ),
    }


def _dependency_record_authority(
    dependency: WorkflowPackageDependency,
) -> Mapping[str, object]:
    record: dict[str, object] = {
        "package_id": dependency.package_id,
        "version_constraint": dependency.version_constraint,
    }
    if dependency.manifest_digest is not None:
        record["manifest_digest"] = dependency.manifest_digest
    return record


def _manifest_mapping_authority(manifest: Mapping[str, object]) -> Mapping[str, object]:
    authority: dict[str, object] = {}
    for field in _ROOT_AUTHORITY_FIELDS:
        if field not in manifest:
            continue
        value = manifest[field]
        if field == "package":
            authority[field] = _package_mapping_authority(value)
        elif field == "workflows":
            authority[field] = _sorted_mapping_records(
                value,
                "workflow_id",
                normalizer=_workflow_mapping_authority,
            )
        elif field == "assets":
            authority[field] = _sorted_mapping_records(value, "asset_id")
        elif field == "dependencies":
            authority[field] = _sorted_mapping_records(
                value,
                "package_id",
                "version_constraint",
                normalizer=_dependency_mapping_authority,
            )
        else:
            authority[field] = value
    return authority


def _package_mapping_authority(value: object) -> object:
    if not isinstance(value, Mapping):
        return value
    return {
        key: nested_value
        for key, nested_value in value.items()
        if isinstance(key, str) and key not in _PACKAGE_PROVENANCE_FIELDS
    }


def _sorted_mapping_records(
    value: object,
    *key_fields: str,
    normalizer: Any = None,
) -> object:
    if not isinstance(value, (list, tuple)):
        return value

    authority_records = [
        normalizer(record) if normalizer is not None else record
        for record in value
    ]

    def sort_key(record: object) -> tuple[str, ...]:
        if not isinstance(record, Mapping):
            return ("",)
        return tuple(str(record.get(field, "")) for field in key_fields)

    return sorted(authority_records, key=sort_key)


def _workflow_mapping_authority(value: object) -> object:
    if not isinstance(value, Mapping):
        return value
    record = {
        key: nested_value
        for key, nested_value in value.items()
        if key not in {"display", "source_refs"}
    }
    if "required_assets" in record:
        record["required_assets"] = _sorted_mapping_records(
            record["required_assets"],
            "asset_id",
            normalizer=_optional_none_mapping_authority,
        )
    if not record.get("required_dependencies"):
        record.pop("required_dependencies", None)
    return record


def _dependency_mapping_authority(value: object) -> object:
    if not isinstance(value, Mapping):
        return value
    return _optional_none_mapping_authority(value)


def _optional_none_mapping_authority(value: Mapping[Any, Any]) -> Mapping[str, object]:
    return {
        key: nested_value
        for key, nested_value in value.items()
        if isinstance(key, str) and nested_value is not None
    }


def _canonical_value(value: object) -> CanonicalManifestValue:
    if value is None or isinstance(value, (str, bool)):
        return value
    if type(value) is int:
        return value
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, Mapping):
        return _canonical_mapping(value)
    raise WorkflowPackageManifestCanonicalizationError(
        f"unsupported workflow package manifest value type: {type(value).__name__}"
    )


def _canonical_mapping(value: Mapping[Any, Any]) -> dict[str, CanonicalManifestValue]:
    canonical: dict[str, CanonicalManifestValue] = {}
    for key, nested_value in value.items():
        if not isinstance(key, str):
            raise WorkflowPackageManifestCanonicalizationError(
                "workflow package manifest map keys must be strings"
            )
        canonical[key] = _canonical_value(nested_value)
    return canonical


__all__ = (
    "WORKFLOW_PACKAGE_ASSET_DIGEST_DOMAIN",
    "WORKFLOW_PACKAGE_CANONICALIZATION_ALGORITHM",
    "WORKFLOW_PACKAGE_HASH_ALGORITHM",
    "WORKFLOW_PACKAGE_MANIFEST_DIGEST_DOMAIN",
    "WORKFLOW_PACKAGE_MANIFEST_FORMAT_VERSION",
    "WORKFLOW_PACKAGE_MANIFEST_RECORD_KIND",
    "ManifestValue",
    "WorkflowPackageAsset",
    "WorkflowPackageAssetRef",
    "WorkflowPackageCanonicalization",
    "WorkflowPackageCompatibility",
    "WorkflowPackageDependency",
    "WorkflowPackageIdentity",
    "WorkflowPackageManifest",
    "WorkflowPackageManifestCanonicalizationError",
    "WorkflowPackageWorkflow",
    "asset_digest_for_bytes",
    "canonical_manifest_bytes",
    "manifest_digest_for_manifest",
)
