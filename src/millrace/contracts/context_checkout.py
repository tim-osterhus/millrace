"""Canonical contracts for CLI-owned context checkout manifests."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
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
        "omissions",
    }
)
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
_OMISSION_KEYS = frozenset({"source_kind", "source_ref", "reason"})
_OMISSION_REASONS = frozenset(
    {"source_missing", "file_limit_exceeded", "byte_limit_exceeded"}
)


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


@dataclass(frozen=True, slots=True)
class ContextCheckoutManifest:
    record_kind: ClassVar[str] = "millrace.context_checkout_manifest"
    schema_version: ClassVar[int] = 1

    session_id: str
    dispatch_generation: int
    plan_fingerprint: str
    binding_id: str
    router_asset_id: str
    files: tuple[ContextCheckoutFile, ...]
    omissions: tuple[ContextCheckoutOmission, ...]

    def __post_init__(self) -> None:
        _text(self.session_id, "session_id")
        _int(self.dispatch_generation, "dispatch_generation", minimum=1)
        _digest(self.plan_fingerprint, "plan_fingerprint")
        _text(self.binding_id, "binding_id")
        _text(self.router_asset_id, "router_asset_id")
        object.__setattr__(self, "files", _canonical_files(self.files))
        object.__setattr__(self, "omissions", _canonical_omissions(self.omissions))


def encode_context_checkout_manifest(
    manifest: ContextCheckoutManifest | Mapping[str, object],
) -> bytes:
    """Return compact, sorted-key UTF-8 JSON bytes for a manifest."""
    try:
        manifest = _coerce_manifest(manifest)
        record = {
            "record_kind": ContextCheckoutManifest.record_kind,
            "schema_version": ContextCheckoutManifest.schema_version,
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
            "omissions": [
                {
                    "source_kind": item.source_kind,
                    "source_ref": item.source_ref,
                    "reason": item.reason,
                }
                for item in manifest.omissions
            ],
        }
        return json.dumps(
            record,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except ContextCheckoutContractError:
        raise
    except Exception as exc:
        _refuse("manifest cannot be canonically encoded", exc)


def decode_context_checkout_manifest(
    raw: bytes | Mapping[str, object],
) -> ContextCheckoutManifest:
    """Decode strict JSON bytes and return a canonically ordered manifest."""
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
    manifest: ContextCheckoutManifest | Mapping[str, object] | bytes,
) -> str:
    """Return the raw CAS digest of canonical manifest bytes."""
    try:
        raw = (
            encode_context_checkout_manifest(manifest)
            if isinstance(manifest, (ContextCheckoutManifest, Mapping))
            else _require_bytes(manifest)
        )
        return f"sha256:{sha256(raw).hexdigest()}"
    except ContextCheckoutContractError:
        raise
    except Exception as exc:
        _refuse("manifest digest cannot be obtained", exc)


def verify_context_checkout_manifest_digest(
    manifest: ContextCheckoutManifest | Mapping[str, object] | bytes,
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
    value: ContextCheckoutManifest | Mapping[str, object],
) -> ContextCheckoutManifest:
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


def _manifest_from_mapping(value: Mapping[object, object]) -> ContextCheckoutManifest:
    _exact_keys(value, _MANIFEST_KEYS, "manifest")
    if value["record_kind"] != ContextCheckoutManifest.record_kind:
        _refuse("manifest record_kind is unsupported")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        _refuse("manifest schema_version is unsupported")
    files = value["files"]
    omissions = value["omissions"]
    if (
        isinstance(files, (str, bytes, bytearray, Mapping))
        or not isinstance(files, Sequence)
        or isinstance(omissions, (str, bytes, bytearray, Mapping))
        or not isinstance(omissions, Sequence)
    ):
        _refuse("manifest files and omissions must be arrays")
    return ContextCheckoutManifest(
        session_id=cast(str, value["session_id"]),
        dispatch_generation=cast(int, value["dispatch_generation"]),
        plan_fingerprint=cast(str, value["plan_fingerprint"]),
        binding_id=cast(str, value["binding_id"]),
        router_asset_id=cast(str, value["router_asset_id"]),
        files=tuple(
            _decode_file(item, index)
            for index, item in enumerate(cast(Sequence[object], files))
        ),
        omissions=tuple(
            _decode_omission(item, index)
            for index, item in enumerate(cast(Sequence[object], omissions))
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
    "ContextCheckoutContractError",
    "ContextCheckoutFile",
    "ContextCheckoutManifest",
    "ContextCheckoutOmission",
    "context_checkout_manifest_digest",
    "decode_context_checkout_manifest",
    "encode_context_checkout_manifest",
    "verify_context_checkout_manifest_digest",
)
