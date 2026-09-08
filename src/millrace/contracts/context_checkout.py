"""Canonical contracts for CLI-owned context checkout manifests."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import ClassVar, NoReturn, cast
from unicodedata import normalize


class ContextCheckoutContractError(ValueError):
    """Raised when a context checkout contract is malformed."""


_SHA256_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_MANIFEST_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "session_id",
        "dispatch_generation",
        "plan_fingerprint",
        "binding_id",
        "router_asset_id",
        "files",
        "catalog",
        "omissions",
        "root_states",
    }
)
_LEGACY_MANIFEST_KEYS = _MANIFEST_KEYS - {"root_states"}
_FILE_KEYS = frozenset(
    {
        "checkout_path",
        "source_kind",
        "source_ref",
        "content_digest",
        "byte_length",
        "required",
    }
)
_CATALOG_KEYS = frozenset(
    {
        "logical_path",
        "source_kind",
        "source_ref",
        "content_digest",
        "byte_length",
        "provenance_ids",
    }
)
_OMISSION_KEYS = frozenset({"source_kind", "source_ref", "reason"})
_OMISSION_REASONS = frozenset(
    {"source_missing", "file_limit_exceeded", "byte_limit_exceeded"}
)
_ROOT_STATE_KEYS = frozenset(
    {"source_kind", "source_ref", "root_kind", "files", "directories"}
)
_ROOT_KINDS = frozenset({"missing", "file", "directory"})


def _refuse(message: str, cause: BaseException | None = None) -> NoReturn:
    if cause is None:
        raise ContextCheckoutContractError(message)
    raise ContextCheckoutContractError(message) from cause


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _refuse(f"{field_name} must be a non-blank string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        _refuse(f"{field_name} must be valid UTF-8", exc)
    if b"\x00" in encoded:
        _refuse(f"{field_name} must not contain NUL")
    if normalize("NFC", value) != value:
        _refuse(f"{field_name} must be NFC")
    return value


def _digest(value: object, field_name: str) -> str:
    value = _text(value, field_name)
    if _SHA256_DIGEST.fullmatch(value) is None:
        _refuse(f"{field_name} must be lowercase sha256:<64 hex>")
    return value


def _int(value: object, field_name: str, *, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        _refuse(f"{field_name} must be an integer >= {minimum}")
    return value


def _bool(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        _refuse(f"{field_name} must be a boolean")
    return value


def _checkout_path(value: object) -> str:
    path = _text(value, "checkout_path")
    if (
        path.startswith("/")
        or "\\" in path
        or ":" in path.split("/", 1)[0]
        or any(part in {"", ".", "..", ".millrace"} for part in path.split("/"))
    ):
        _refuse("checkout_path must be a safe relative POSIX path")
    return path


def _root_relative_path(value: object, field_name: str) -> str:
    if type(value) is str and value == "":
        return value
    path = _text(value, field_name)
    if (
        path.startswith("/")
        or "\\" in path
        or ":" in path.split("/", 1)[0]
        or any(part in {"", ".", "..", ".millrace"} for part in path.split("/"))
    ):
        _refuse(f"{field_name} must be a safe relative POSIX path")
    return path


def _canonical_root_paths(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray, Mapping)) or not isinstance(
        value, Sequence
    ):
        _refuse(f"{field_name} must be a sequence")
    try:
        paths = tuple(cast(Sequence[object], value))
    except Exception as exc:
        _refuse(f"{field_name} must be a finite sequence", exc)
    result = tuple(
        _root_relative_path(item, "root path") for item in paths
    )
    if len(result) != len(set(result)):
        _refuse(f"{field_name} must not contain duplicates")
    return tuple(sorted(result, key=lambda item: item.encode("utf-8")))


def _canonical_files(value: object) -> tuple[ContextCheckoutFile, ...]:
    if isinstance(value, (str, bytes, bytearray, Mapping)) or not isinstance(
        value, Sequence
    ):
        _refuse("files must be a sequence")
    try:
        files = tuple(cast(Sequence[object], value))
    except Exception as exc:
        _refuse("files must be a finite sequence", exc)
    if any(not isinstance(item, ContextCheckoutFile) for item in files):
        _refuse("files must contain ContextCheckoutFile records")
    typed_files = cast(tuple[ContextCheckoutFile, ...], files)
    try:
        paths = [item.checkout_path for item in typed_files]
        if len(paths) != len(set(paths)):
            _refuse("files must not contain duplicate checkout_path values")
        return tuple(
            sorted(typed_files, key=lambda item: item.checkout_path.encode("utf-8"))
        )
    except ContextCheckoutContractError:
        raise
    except Exception as exc:
        _refuse("files cannot be canonically ordered", exc)


def _canonical_catalog(
    value: object,
) -> tuple[ContextCheckoutCatalogEntry, ...]:
    if isinstance(value, (str, bytes, bytearray, Mapping)) or not isinstance(
        value, Sequence
    ):
        _refuse("catalog must be a sequence")
    try:
        catalog = tuple(cast(Sequence[object], value))
    except Exception as exc:
        _refuse("catalog must be a finite sequence", exc)
    if any(not isinstance(item, ContextCheckoutCatalogEntry) for item in catalog):
        _refuse("catalog must contain ContextCheckoutCatalogEntry records")
    typed_catalog = cast(tuple[ContextCheckoutCatalogEntry, ...], catalog)
    try:
        paths = [item.logical_path for item in typed_catalog]
        if len(paths) != len(set(paths)):
            _refuse("catalog must not contain duplicate logical_path values")
        return tuple(
            sorted(typed_catalog, key=lambda item: item.logical_path.encode("utf-8"))
        )
    except ContextCheckoutContractError:
        raise
    except Exception as exc:
        _refuse("catalog cannot be canonically ordered", exc)


def _canonical_omissions(value: object) -> tuple[ContextCheckoutOmission, ...]:
    if isinstance(value, (str, bytes, bytearray, Mapping)) or not isinstance(
        value, Sequence
    ):
        _refuse("omissions must be a sequence")
    try:
        omissions = tuple(cast(Sequence[object], value))
    except Exception as exc:
        _refuse("omissions must be a finite sequence", exc)
    if any(not isinstance(item, ContextCheckoutOmission) for item in omissions):
        _refuse("omissions must contain ContextCheckoutOmission records")
    typed_omissions = cast(tuple[ContextCheckoutOmission, ...], omissions)
    try:
        return tuple(
            sorted(
                typed_omissions,
                key=lambda item: (
                    item.source_kind.encode("utf-8"),
                    item.source_ref.encode("utf-8"),
                    item.reason.encode("utf-8"),
                ),
            )
        )
    except (UnicodeError, AttributeError, TypeError) as exc:
        _refuse("omissions cannot be canonically ordered", exc)


def _canonical_root_states(
    value: object,
) -> tuple[ContextCheckoutRootState, ...]:
    if isinstance(value, (str, bytes, bytearray, Mapping)) or not isinstance(
        value, Sequence
    ):
        _refuse("root_states must be a sequence")
    try:
        root_states = tuple(cast(Sequence[object], value))
    except Exception as exc:
        _refuse("root_states must be a finite sequence", exc)
    if any(not isinstance(item, ContextCheckoutRootState) for item in root_states):
        _refuse("root_states must contain ContextCheckoutRootState records")
    typed_states = cast(tuple[ContextCheckoutRootState, ...], root_states)
    source_keys = [(item.source_kind, item.source_ref) for item in typed_states]
    if len(source_keys) != len(set(source_keys)):
        _refuse("root_states must not contain duplicate sources")
    return tuple(
        sorted(
            typed_states,
            key=lambda item: (
                item.source_kind.encode("utf-8"),
                item.source_ref.encode("utf-8"),
            ),
        )
    )


@dataclass(frozen=True, slots=True)
class ContextCheckoutFile:
    checkout_path: str
    source_kind: str
    source_ref: str
    content_digest: str
    byte_length: int
    required: bool

    def __post_init__(self) -> None:
        _checkout_path(self.checkout_path)
        _text(self.source_kind, "source_kind")
        _text(self.source_ref, "source_ref")
        _digest(self.content_digest, "content_digest")
        _int(self.byte_length, "byte_length", minimum=0)
        _bool(self.required, "required")


@dataclass(frozen=True, slots=True)
class ContextCheckoutOmission:
    source_kind: str
    source_ref: str
    reason: str

    def __post_init__(self) -> None:
        _text(self.source_kind, "source_kind")
        _text(self.source_ref, "source_ref")
        _text(self.reason, "reason")
        if self.reason not in _OMISSION_REASONS:
            _refuse("unsupported context checkout omission reason")


def _logical_path(value: object) -> str:
    path = _text(value, "logical_path")
    if (
        path.startswith("/")
        or "\\" in path
        or ":" in path.split("/", 1)[0]
        or any(part in {"", ".", "..", ".millrace"} for part in path.split("/"))
    ):
        _refuse("logical_path must be a safe relative POSIX path")
    return path


def _canonical_provenance_ids(value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray, Mapping)) or not isinstance(
        value, Sequence
    ):
        _refuse("provenance_ids must be a sequence")
    try:
        provenance_ids = tuple(cast(Sequence[object], value))
    except Exception as exc:
        _refuse("provenance_ids must be a finite sequence", exc)
    if not provenance_ids:
        _refuse("provenance_ids must not be empty")
    identifiers = tuple(
        _text(item, "provenance_id") for item in provenance_ids
    )
    if len(identifiers) != len(set(identifiers)):
        _refuse("provenance_ids must not contain duplicates")
    return tuple(sorted(identifiers, key=lambda item: item.encode("utf-8")))


@dataclass(frozen=True, slots=True)
class ContextCheckoutCatalogEntry:
    logical_path: str
    source_kind: str
    source_ref: str
    content_digest: str
    byte_length: int
    provenance_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _logical_path(self.logical_path)
        _text(self.source_kind, "source_kind")
        _text(self.source_ref, "source_ref")
        _digest(self.content_digest, "content_digest")
        _int(self.byte_length, "byte_length", minimum=0)
        object.__setattr__(
            self, "provenance_ids", _canonical_provenance_ids(self.provenance_ids)
        )


@dataclass(frozen=True, slots=True)
class ContextCheckoutRootState:
    """Authenticated, bounded state for one selected workspace root."""

    source_kind: str
    source_ref: str
    root_kind: str
    files: tuple[str, ...]
    directories: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.source_kind, "source_kind")
        _text(self.source_ref, "source_ref")
        _text(self.root_kind, "root_kind")
        if self.root_kind not in _ROOT_KINDS:
            _refuse("unsupported context checkout root kind")
        files = _canonical_root_paths(self.files, "files")
        directories = _canonical_root_paths(self.directories, "directories")
        if set(files) & set(directories):
            _refuse("root state files and directories must not overlap")
        if self.root_kind == "missing" and (files or directories):
            _refuse("missing root state must not contain entries")
        if self.root_kind == "file" and (files != ("",) or directories):
            _refuse("file root state must contain only its root file")
        if self.root_kind == "directory":
            if "" not in directories:
                _refuse("directory root state must contain its root directory")
            for path in (*files, *directories):
                if any(
                    parent.as_posix() not in directories
                    for parent in Path(path).parents
                    if parent.as_posix() != "."
                ):
                    _refuse("root state directory structure is incomplete")
        object.__setattr__(self, "files", files)
        object.__setattr__(self, "directories", directories)


def _validate_manifest_fields(
    *,
    session_id: str,
    dispatch_generation: int,
    plan_fingerprint: str,
    binding_id: str,
    router_asset_id: str,
    files: object,
    catalog: object,
    omissions: object,
) -> tuple[
    tuple[ContextCheckoutFile, ...],
    tuple[ContextCheckoutCatalogEntry, ...],
    tuple[ContextCheckoutOmission, ...],
]:
    _text(session_id, "session_id")
    _int(dispatch_generation, "dispatch_generation", minimum=1)
    _digest(plan_fingerprint, "plan_fingerprint")
    _text(binding_id, "binding_id")
    _text(router_asset_id, "router_asset_id")
    typed_files = _canonical_files(files)
    typed_catalog = _canonical_catalog(catalog)
    declared_sizes: dict[str, int] = {}
    declared_items: tuple[
        ContextCheckoutFile | ContextCheckoutCatalogEntry, ...
    ] = (*typed_files, *typed_catalog)
    for item in declared_items:
        previous = declared_sizes.setdefault(item.content_digest, item.byte_length)
        if previous != item.byte_length:
            _refuse("content digest has conflicting declared sizes")
    return typed_files, typed_catalog, _canonical_omissions(omissions)


@dataclass(frozen=True, slots=True)
class ContextCheckoutManifest:
    record_kind: ClassVar[str] = "millrace.context_checkout_manifest"
    schema_version: ClassVar[int] = 3

    session_id: str
    dispatch_generation: int
    plan_fingerprint: str
    binding_id: str
    router_asset_id: str
    files: tuple[ContextCheckoutFile, ...]
    catalog: tuple[ContextCheckoutCatalogEntry, ...] = ()
    omissions: tuple[ContextCheckoutOmission, ...] = ()
    root_states: tuple[ContextCheckoutRootState, ...] = ()

    def __post_init__(self) -> None:
        files, catalog, omissions = _validate_manifest_fields(
            session_id=self.session_id,
            dispatch_generation=self.dispatch_generation,
            plan_fingerprint=self.plan_fingerprint,
            binding_id=self.binding_id,
            router_asset_id=self.router_asset_id,
            files=self.files,
            catalog=self.catalog,
            omissions=self.omissions,
        )
        object.__setattr__(self, "files", files)
        object.__setattr__(self, "catalog", catalog)
        object.__setattr__(self, "omissions", omissions)
        object.__setattr__(
            self, "root_states", _canonical_root_states(self.root_states)
        )


@dataclass(frozen=True, slots=True)
class ContextCheckoutLegacyManifest:
    """Inspect-only representation of an authenticated schema-v2 manifest."""

    record_kind: ClassVar[str] = "millrace.context_checkout_manifest"
    schema_version: ClassVar[int] = 2

    session_id: str
    dispatch_generation: int
    plan_fingerprint: str
    binding_id: str
    router_asset_id: str
    files: tuple[ContextCheckoutFile, ...]
    catalog: tuple[ContextCheckoutCatalogEntry, ...] = ()
    omissions: tuple[ContextCheckoutOmission, ...] = ()

    def __post_init__(self) -> None:
        files, catalog, omissions = _validate_manifest_fields(
            session_id=self.session_id,
            dispatch_generation=self.dispatch_generation,
            plan_fingerprint=self.plan_fingerprint,
            binding_id=self.binding_id,
            router_asset_id=self.router_asset_id,
            files=self.files,
            catalog=self.catalog,
            omissions=self.omissions,
        )
        object.__setattr__(self, "files", files)
        object.__setattr__(self, "catalog", catalog)
        object.__setattr__(self, "omissions", omissions)




def _manifest_record(
    manifest: ContextCheckoutManifest | ContextCheckoutLegacyManifest,
    *,
    schema_version: int,
) -> dict[str, object]:
    record: dict[str, object] = {
        "record_kind": ContextCheckoutManifest.record_kind,
        "schema_version": schema_version,
        "session_id": manifest.session_id,
        "dispatch_generation": manifest.dispatch_generation,
        "plan_fingerprint": manifest.plan_fingerprint,
        "binding_id": manifest.binding_id,
        "router_asset_id": manifest.router_asset_id,
        "files": [
            {
                "checkout_path": item.checkout_path,
                "source_kind": item.source_kind,
                "source_ref": item.source_ref,
                "content_digest": item.content_digest,
                "byte_length": item.byte_length,
                "required": item.required,
            }
            for item in manifest.files
        ],
        "catalog": [
            {
                "logical_path": item.logical_path,
                "source_kind": item.source_kind,
                "source_ref": item.source_ref,
                "content_digest": item.content_digest,
                "byte_length": item.byte_length,
                "provenance_ids": list(item.provenance_ids),
            }
            for item in manifest.catalog
        ],
        "omissions": [
            {
                "source_kind": item.source_kind,
                "source_ref": item.source_ref,
                "reason": item.reason,
            }
            for item in manifest.omissions
        ],
    }
    if schema_version == ContextCheckoutManifest.schema_version:
        record["root_states"] = [
            {
                "source_kind": item.source_kind,
                "source_ref": item.source_ref,
                "root_kind": item.root_kind,
                "files": list(item.files),
                "directories": list(item.directories),
            }
            for item in getattr(manifest, "root_states", ())
        ]
    return record


def _encode_manifest_record(record: Mapping[str, object]) -> bytes:
    return json.dumps(
        record,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def encode_context_checkout_manifest(
    manifest: ContextCheckoutManifest
    | ContextCheckoutLegacyManifest
    | Mapping[str, object],
) -> bytes:
    """Return canonical bytes without upgrading an inspect-only legacy manifest."""
    try:
        manifest = _coerce_manifest(manifest)
        schema_version = (
            ContextCheckoutLegacyManifest.schema_version
            if isinstance(manifest, ContextCheckoutLegacyManifest)
            else ContextCheckoutManifest.schema_version
        )
        return _encode_manifest_record(
            _manifest_record(manifest, schema_version=schema_version)
        )
    except ContextCheckoutContractError:
        raise
    except Exception as exc:
        _refuse("manifest cannot be canonically encoded", exc)

def decode_context_checkout_manifest(
    raw: bytes | Mapping[str, object],
) -> ContextCheckoutManifest | ContextCheckoutLegacyManifest:
    """Decode canonical schema-v3 or inspect-only schema-v2 manifest bytes."""
    try:
        if isinstance(raw, Mapping):
            return _manifest_from_mapping(cast(Mapping[object, object], raw))
        if type(raw) is not bytes:
            _refuse("manifest bytes must be exact bytes")
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_pairs,
            parse_constant=_reject_constant,
        )
        if not isinstance(parsed, Mapping):
            _refuse("manifest root must be an object")
        manifest = _manifest_from_mapping(parsed)
        if encode_context_checkout_manifest(manifest) != raw:
            _refuse("manifest bytes are not canonical")
        return manifest
    except ContextCheckoutContractError:
        raise
    except Exception as exc:
        _refuse("manifest bytes are malformed", exc)



def context_checkout_manifest_digest(
    manifest: ContextCheckoutManifest
    | ContextCheckoutLegacyManifest
    | Mapping[str, object]
    | bytes,
) -> str:
    """Return the raw CAS digest of canonical manifest bytes."""
    try:
        raw = (
            encode_context_checkout_manifest(manifest)
            if isinstance(
                manifest,
                (ContextCheckoutManifest, ContextCheckoutLegacyManifest, Mapping),
            )
            else _require_bytes(manifest)
        )
        return f"sha256:{sha256(raw).hexdigest()}"
    except ContextCheckoutContractError:
        raise
    except Exception as exc:
        _refuse("manifest digest cannot be obtained", exc)


def verify_context_checkout_manifest_digest(
    manifest: ContextCheckoutManifest
    | ContextCheckoutLegacyManifest
    | Mapping[str, object]
    | bytes,
    expected_digest: str,
) -> bool:
    """Verify a raw `sha256:` digest and return ``True`` or refuse."""
    actual_digest = context_checkout_manifest_digest(manifest)
    _digest(expected_digest, "expected_digest")
    if actual_digest != expected_digest:
        _refuse("context checkout manifest digest mismatch")
    return True


def _require_bytes(value: object) -> bytes:
    if type(value) is not bytes:
        _refuse("manifest bytes must be exact bytes")
    return value


def _coerce_manifest(
    value: (
        ContextCheckoutManifest
        | ContextCheckoutLegacyManifest
        | Mapping[str, object]
    ),
) -> ContextCheckoutManifest | ContextCheckoutLegacyManifest:
    if isinstance(value, ContextCheckoutManifest):
        return ContextCheckoutManifest(
            session_id=value.session_id,
            dispatch_generation=value.dispatch_generation,
            plan_fingerprint=value.plan_fingerprint,
            binding_id=value.binding_id,
            router_asset_id=value.router_asset_id,
            files=tuple(
                ContextCheckoutFile(
                    checkout_path=item.checkout_path,
                    source_kind=item.source_kind,
                    source_ref=item.source_ref,
                    content_digest=item.content_digest,
                    byte_length=item.byte_length,
                    required=item.required,
                )
                for item in value.files
            ),
            catalog=tuple(
                ContextCheckoutCatalogEntry(
                    logical_path=item.logical_path,
                    source_kind=item.source_kind,
                    source_ref=item.source_ref,
                    content_digest=item.content_digest,
                    byte_length=item.byte_length,
                    provenance_ids=tuple(item.provenance_ids),
                )
                for item in value.catalog
            ),
            omissions=tuple(
                ContextCheckoutOmission(
                    source_kind=item.source_kind,
                    source_ref=item.source_ref,
                    reason=item.reason,
                )
                for item in value.omissions
            ),
            root_states=tuple(
                ContextCheckoutRootState(
                    source_kind=item.source_kind,
                    source_ref=item.source_ref,
                    root_kind=item.root_kind,
                    files=tuple(item.files),
                    directories=tuple(item.directories),
                )
                for item in value.root_states
            ),
        )
    if isinstance(value, ContextCheckoutLegacyManifest):
        return ContextCheckoutLegacyManifest(
            session_id=value.session_id,
            dispatch_generation=value.dispatch_generation,
            plan_fingerprint=value.plan_fingerprint,
            binding_id=value.binding_id,
            router_asset_id=value.router_asset_id,
            files=tuple(
                ContextCheckoutFile(
                    checkout_path=item.checkout_path,
                    source_kind=item.source_kind,
                    source_ref=item.source_ref,
                    content_digest=item.content_digest,
                    byte_length=item.byte_length,
                    required=item.required,
                )
                for item in value.files
            ),
            catalog=tuple(
                ContextCheckoutCatalogEntry(
                    logical_path=item.logical_path,
                    source_kind=item.source_kind,
                    source_ref=item.source_ref,
                    content_digest=item.content_digest,
                    byte_length=item.byte_length,
                    provenance_ids=tuple(item.provenance_ids),
                )
                for item in value.catalog
            ),
            omissions=tuple(
                ContextCheckoutOmission(
                    source_kind=item.source_kind,
                    source_ref=item.source_ref,
                    reason=item.reason,
                )
                for item in value.omissions
            ),
        )
    if isinstance(value, Mapping):
        return _manifest_from_mapping(cast(Mapping[object, object], value))
    _refuse("manifest must be a ContextCheckoutManifest or mapping")


def _manifest_from_mapping(
    value: Mapping[object, object],
) -> ContextCheckoutManifest | ContextCheckoutLegacyManifest:
    schema_version = value.get("schema_version")
    if type(schema_version) is not int or schema_version not in {2, 3}:
        _refuse("manifest schema_version is unsupported")
    expected_keys = (
        _LEGACY_MANIFEST_KEYS if schema_version == 2 else _MANIFEST_KEYS
    )
    _exact_keys(value, expected_keys, "manifest")
    if value["record_kind"] != ContextCheckoutManifest.record_kind:
        _refuse("manifest record_kind is unsupported")
    files = value["files"]
    catalog = value["catalog"]
    omissions = value["omissions"]
    if (
        isinstance(files, (str, bytes, bytearray, Mapping))
        or not isinstance(files, Sequence)
        or isinstance(catalog, (str, bytes, bytearray, Mapping))
        or not isinstance(catalog, Sequence)
        or isinstance(omissions, (str, bytes, bytearray, Mapping))
        or not isinstance(omissions, Sequence)
    ):
        _refuse("manifest files, catalog, and omissions must be arrays")
    decoded_files = tuple(
        _decode_file(item, index)
        for index, item in enumerate(cast(Sequence[object], files))
    )
    decoded_catalog = tuple(
        _decode_catalog_entry(item, index)
        for index, item in enumerate(cast(Sequence[object], catalog))
    )
    decoded_omissions = tuple(
        _decode_omission(item, index)
        for index, item in enumerate(cast(Sequence[object], omissions))
    )
    common = {
        "session_id": cast(str, value["session_id"]),
        "dispatch_generation": cast(int, value["dispatch_generation"]),
        "plan_fingerprint": cast(str, value["plan_fingerprint"]),
        "binding_id": cast(str, value["binding_id"]),
        "router_asset_id": cast(str, value["router_asset_id"]),
        "files": decoded_files,
        "catalog": decoded_catalog,
        "omissions": decoded_omissions,
    }
    if schema_version == 2:
        return ContextCheckoutLegacyManifest(
            session_id=cast(str, common["session_id"]),
            dispatch_generation=cast(int, common["dispatch_generation"]),
            plan_fingerprint=cast(str, common["plan_fingerprint"]),
            binding_id=cast(str, common["binding_id"]),
            router_asset_id=cast(str, common["router_asset_id"]),
            files=cast(tuple[ContextCheckoutFile, ...], common["files"]),
            catalog=cast(
                tuple[ContextCheckoutCatalogEntry, ...], common["catalog"]
            ),
            omissions=cast(
                tuple[ContextCheckoutOmission, ...], common["omissions"]
            ),
        )
    root_states = value["root_states"]
    if (
        isinstance(root_states, (str, bytes, bytearray, Mapping))
        or not isinstance(root_states, Sequence)
    ):
        _refuse("manifest root_states must be an array")
    decoded_root_states = tuple(
        _decode_root_state(item, index)
        for index, item in enumerate(cast(Sequence[object], root_states))
    )
    return ContextCheckoutManifest(
        session_id=cast(str, common["session_id"]),
        dispatch_generation=cast(int, common["dispatch_generation"]),
        plan_fingerprint=cast(str, common["plan_fingerprint"]),
        binding_id=cast(str, common["binding_id"]),
        router_asset_id=cast(str, common["router_asset_id"]),
        files=cast(tuple[ContextCheckoutFile, ...], common["files"]),
        catalog=cast(
            tuple[ContextCheckoutCatalogEntry, ...], common["catalog"]
        ),
        omissions=cast(tuple[ContextCheckoutOmission, ...], common["omissions"]),
        root_states=decoded_root_states,
    )



def _decode_root_state(value: object, index: int) -> ContextCheckoutRootState:
    if not isinstance(value, Mapping):
        _refuse(f"root_states[{index}] must be an object")
    record = cast(Mapping[object, object], value)
    _exact_keys(record, _ROOT_STATE_KEYS, f"root_states[{index}]")
    files = record["files"]
    directories = record["directories"]
    if (
        isinstance(files, (str, bytes, bytearray, Mapping))
        or not isinstance(files, Sequence)
        or isinstance(directories, (str, bytes, bytearray, Mapping))
        or not isinstance(directories, Sequence)
    ):
        _refuse(f"root_states[{index}] files and directories must be arrays")
    return ContextCheckoutRootState(
        source_kind=cast(str, record["source_kind"]),
        source_ref=cast(str, record["source_ref"]),
        root_kind=cast(str, record["root_kind"]),
        files=cast(tuple[str, ...], tuple(cast(Sequence[object], files))),
        directories=cast(
            tuple[str, ...], tuple(cast(Sequence[object], directories))
        ),
    )


def _strict_pairs(pairs: list[tuple[object, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if not isinstance(key, str):
            _refuse("JSON object keys must be strings")
        if key in result:
            _refuse("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    _refuse(f"unsupported JSON constant: {value}")


def _exact_keys(
    record: Mapping[object, object], expected: frozenset[str], label: str
) -> None:
    try:
        keys = set(record)
    except Exception as exc:
        _refuse(f"{label} keys are malformed", exc)
    if any(not isinstance(key, str) for key in keys):
        _refuse(f"{label} keys must be strings")
    if keys != expected:
        _refuse(f"{label} keys are not exact")


def _decode_file(value: object, index: int) -> ContextCheckoutFile:
    if not isinstance(value, Mapping):
        _refuse(f"files[{index}] must be an object")
    record = cast(Mapping[object, object], value)
    _exact_keys(record, _FILE_KEYS, f"files[{index}]")
    return ContextCheckoutFile(
        checkout_path=cast(str, record["checkout_path"]),
        source_kind=cast(str, record["source_kind"]),
        source_ref=cast(str, record["source_ref"]),
        content_digest=cast(str, record["content_digest"]),
        byte_length=cast(int, record["byte_length"]),
        required=cast(bool, record["required"]),
    )


def _decode_catalog_entry(
    value: object, index: int
) -> ContextCheckoutCatalogEntry:
    if not isinstance(value, Mapping):
        _refuse(f"catalog[{index}] must be an object")
    record = cast(Mapping[object, object], value)
    _exact_keys(record, _CATALOG_KEYS, f"catalog[{index}]")
    provenance_ids = record["provenance_ids"]
    if (
        isinstance(provenance_ids, (str, bytes, bytearray, Mapping))
        or not isinstance(provenance_ids, Sequence)
    ):
        _refuse(f"catalog[{index}].provenance_ids must be an array")
    return ContextCheckoutCatalogEntry(
        logical_path=cast(str, record["logical_path"]),
        source_kind=cast(str, record["source_kind"]),
        source_ref=cast(str, record["source_ref"]),
        content_digest=cast(str, record["content_digest"]),
        byte_length=cast(int, record["byte_length"]),
        provenance_ids=cast(
            tuple[str, ...], tuple(cast(Sequence[object], provenance_ids))
        ),
    )


def _decode_omission(value: object, index: int) -> ContextCheckoutOmission:
    if not isinstance(value, Mapping):
        _refuse(f"omissions[{index}] must be an object")
    record = cast(Mapping[object, object], value)
    _exact_keys(record, _OMISSION_KEYS, f"omissions[{index}]")
    return ContextCheckoutOmission(
        source_kind=cast(str, record["source_kind"]),
        source_ref=cast(str, record["source_ref"]),
        reason=cast(str, record["reason"]),
    )


__all__ = (
    "ContextCheckoutCatalogEntry",
    "ContextCheckoutContractError",
    "ContextCheckoutFile",
    "ContextCheckoutLegacyManifest",
    "ContextCheckoutManifest",
    "ContextCheckoutOmission",
    "ContextCheckoutRootState",
    "context_checkout_manifest_digest",
    "decode_context_checkout_manifest",
    "encode_context_checkout_manifest",
    "verify_context_checkout_manifest_digest",
)
