"""Strict, bounded candidate control requests; no native capability is implied."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

CONTROL_CONTRACT_ID = "millrace.core.controls"
CONTROL_CONTRACT_REVISION = 1
CONTROL_MAX_BYTES = 16 * 1024
INT64_MAX = (1 << 63) - 1


class InvalidControlRequest(ValueError):
    """Input was refused before operation admission; never echo raw input."""


def text_field(value: object, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidControlRequest("invalid_control_text")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError as exc:
        raise InvalidControlRequest("invalid_control_unicode") from exc
    if size > maximum or any(ord(char) < 32 for char in value):
        raise InvalidControlRequest("invalid_control_text")
    return value


def uuid_field(value: object) -> str:
    value = text_field(value, 36)
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except ValueError as exc:
        raise InvalidControlRequest("invalid_control_uuid") from exc
    return value


def revision_field(value: object) -> int:
    if type(value) is not int or not 0 <= value <= INT64_MAX:
        raise InvalidControlRequest("invalid_control_revision")
    return value


def digest_field(value: object, *, prefixed: bool = False) -> str:
    value = text_field(value)
    pattern = r"sha256:[0-9a-f]{64}" if prefixed else r"[0-9a-f]{64}"
    if re.fullmatch(pattern, value) is None:
        raise InvalidControlRequest("invalid_control_digest")
    return value


def daemon_runtime_identity_field(value: object) -> None:
    """Validate runtime syntax without requiring the currently installed version."""
    runtime = cast(dict[str, Any], value)
    _shape(
        runtime,
        {
            "public_contract_id",
            "public_contract_revision",
            "runtime_distribution",
            "runtime_version",
            "build_identity",
            "store_schema_version",
            "public_schema_digest",
        },
    )
    for key in ("public_contract_id", "runtime_distribution", "runtime_version"):
        text_field(runtime[key])
    for key in ("public_contract_revision", "store_schema_version"):
        revision_field(runtime[key])
    digest_field(runtime["public_schema_digest"])
    if runtime["build_identity"] is not None:
        digest_field(runtime["build_identity"])


def canonical_json(value: object) -> str:
    """Preserve exact Unicode; reject non-JSON types, floats and deep input."""

    def check(item: object, depth: int = 0) -> None:
        if depth > 16:
            raise InvalidControlRequest("control_input_too_deep")
        if item is None or type(item) in (str, bool, int):
            if type(item) is int and not -INT64_MAX <= item <= INT64_MAX:
                raise InvalidControlRequest("invalid_control_integer")
            return
        if type(item) is list:
            for nested in item:
                check(nested, depth + 1)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for nested in item.values():
                check(nested, depth + 1)
            return
        raise InvalidControlRequest("invalid_control_json")

    check(value)
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        if len(encoded.encode("utf-8")) > CONTROL_MAX_BYTES:
            raise InvalidControlRequest("control_input_too_large")
    except (ValueError, UnicodeError) as exc:
        raise InvalidControlRequest("invalid_control_json") from exc
    return encoded


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidControlRequest("duplicate_control_key")
        result[key] = value
    return result


def _shape(value: Any, required: set[str], optional: set[str] | None = None) -> None:
    if (
        type(value) is not dict
        or not required <= value.keys()
        or (value.keys() - required - (optional or set()))
    ):
        raise InvalidControlRequest("invalid_control_fields")


@dataclass(frozen=True, slots=True)
class ControlRequest:
    """Immutable validated semantic JSON, including explicit optional nulls."""

    canonical: str

    def __post_init__(self) -> None:
        # Construct through parse; direct construction is equally validated.
        value = _parse(self.canonical)
        if canonical_json(value) != self.canonical:
            raise InvalidControlRequest("noncanonical_control_request")

    @classmethod
    def parse(cls, raw: str | bytes) -> ControlRequest:
        return cls(canonical_json(_parse(raw)))

    @property
    def payload(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.canonical))

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical.encode("utf-8")).hexdigest()

    @property
    def key(self) -> tuple[str, str, str, str, str]:
        value = self.payload
        target = value["target"]
        return (
            target["workspace_id"],
            target["instance_id"],
            target["store_epoch"],
            value["caller_id"],
            value["operation_id"],
        )


def _parse(raw: str | bytes) -> dict[str, Any]:
    try:
        if (
            len(raw.encode("utf-8") if isinstance(raw, str) else raw)
            > CONTROL_MAX_BYTES
        ):
            raise InvalidControlRequest("control_input_too_large")
        value = json.loads(raw, object_pairs_hook=_object)
        canonical_json(value)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise InvalidControlRequest("invalid_control_request") from exc
    _shape(
        value,
        {
            "contract_id",
            "contract_revision",
            "action",
            "operation_id",
            "actor_id",
            "caller_id",
            "reason",
            "correlation_id",
            "target",
        },
        {"causation_id", "pause_id", "profile", "request_digest"},
    )
    if value["contract_id"] != CONTROL_CONTRACT_ID or (
        type(value["contract_revision"]) is not int
        or value["contract_revision"] != CONTROL_CONTRACT_REVISION
    ):
        raise InvalidControlRequest("unsupported_control_contract")
    _validate_control_context(value)
    text_field(value["reason"], 512)
    value.setdefault("pause_id", None)
    if value["action"] in {"runs.resume", "runs.recover"}:
        uuid_field(value["pause_id"])
    elif value["pause_id"] is not None:
        raise InvalidControlRequest("unexpected_pause_id")
    if "request_digest" in value and value["request_digest"] is None:
        raise InvalidControlRequest("invalid_control_digest")
    supplied = value.pop("request_digest", None)
    if supplied is not None:
        digest_field(supplied)
        if (
            supplied
            != hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
        ):
            raise InvalidControlRequest("control_digest_mismatch")
    return cast(dict[str, Any], value)


def _validate_control_context(value: dict[str, Any]) -> None:
    """Validate fields shared by requests and retained receipts.

    The private reason and submitted pause ID are request-only facts. Receipt
    pause IDs describe decisions and are validated separately by the store.
    """
    if value["action"] not in (
        "runs.pause",
        "runs.resume",
        "runs.recover",
        "daemon.stop",
    ):
        raise InvalidControlRequest("unsupported_control_action")
    for key in ("operation_id", "actor_id", "caller_id", "correlation_id"):
        text_field(value[key])
    value.setdefault("causation_id", None)
    if value["causation_id"] is not None:
        text_field(value["causation_id"])
    target = value["target"]
    scope = {"workspace_id", "instance_id", "store_epoch"}
    if value["action"] == "daemon.stop":
        _shape(
            target,
            scope
            | {
                "daemon_id",
                "daemon_generation",
                "process_nonce",
                "expected_source_revision",
                "expected_runtime",
                "plan_fingerprint",
            },
        )
        daemon_runtime_identity_field(target["expected_runtime"])
        uuid_field(target["daemon_id"])
        uuid_field(target["process_nonce"])
        revision_field(target["daemon_generation"])
        revision_field(target["expected_source_revision"])
        if target["plan_fingerprint"] is not None:
            digest_field(target["plan_fingerprint"], prefixed=True)
    else:
        _shape(
            target,
            scope
            | {
                "run_id",
                "plan_fingerprint",
                "run_generation",
                "run_fencing_token",
                "expected_control_revision",
                "expected_session",
                "last_dispatch_generation",
            }
            | (
                {"mode", "expected_source_revision", "owner_id", "witness_digest"}
                if value["action"] == "runs.recover"
                else set()
            ),
        )
        if value["action"] == "runs.recover":
            if target["mode"] != "retire_native_continuation":
                raise InvalidControlRequest("unsupported_native_recovery")
            revision_field(target["expected_source_revision"])
            uuid_field(target["owner_id"])
            digest_field(target["witness_digest"])
        # Runtime-owned references retain their exact identity within the request
        # bound; the 128-byte caller-identifier limit does not apply to them.
        text_field(target["run_id"], CONTROL_MAX_BYTES)
        text_field(target["run_fencing_token"], CONTROL_MAX_BYTES)
        digest_field(target["plan_fingerprint"], prefixed=True)
        for key in (
            "run_generation",
            "expected_control_revision",
            "last_dispatch_generation",
        ):
            revision_field(target[key])
        session = target["expected_session"]
        if session is None:
            if target["last_dispatch_generation"] != 0:
                raise InvalidControlRequest("invalid_null_session_generation")
        else:
            _shape(
                session,
                {"session_id", "dispatch_generation", "session_fencing_token", "state"},
            )
            text_field(session["session_id"], CONTROL_MAX_BYTES)
            text_field(session["session_fencing_token"], CONTROL_MAX_BYTES)
            generation = revision_field(session["dispatch_generation"])
            if generation == 0 or generation != target["last_dispatch_generation"]:
                raise InvalidControlRequest("invalid_session_generation")
            if session["state"] not in (
                "created",
                "starting",
                "running",
                "cancellation_requested",
                "terminating",
                "completed",
                "interrupted",
                "failed",
                "lost",
            ):
                raise InvalidControlRequest("invalid_session_state")
    for key in scope:
        uuid_field(target[key])
    value.setdefault("profile", None)
    profile = value["profile"]
    if value["action"] == "runs.recover" and profile is None:
        raise InvalidControlRequest("native_recovery_profile_required")
    if profile is not None:
        _shape(
            profile,
            {
                "profile_id",
                "profile_digest",
                "selected_adapter",
                "native_state",
                "effect_boundary",
            },
        )
        for key in (
            "profile_id",
            "selected_adapter",
            "native_state",
            "effect_boundary",
        ):
            text_field(profile[key])
        digest_field(profile["profile_digest"])
