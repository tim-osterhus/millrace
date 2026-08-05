"""Bounded, typed codecs for runner-session operator diagnostics."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from hashlib import sha256
from typing import Protocol

from millrace.contracts.compiled_plan import AuthorityValue
from millrace.contracts.runner import (
    RUNNER_SESSION_COMPLETION_DIAGNOSTIC_MAX_BYTES,
    RunnerDispatchEnvelope,
    RunnerSessionCompletionDiagnostic,
    runner_session_completion_diagnostic_bytes,
)


class _RedactionPolicy(Protocol):
    def redact_authority_value(self, value: object) -> AuthorityValue: ...


class _DiagnosticRequest(Protocol):
    @property
    def dispatch_envelope(self) -> RunnerDispatchEnvelope: ...

    @property
    def redaction_policy(self) -> _RedactionPolicy: ...


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        _plain_json_value(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _bounded_session_diagnostic_bytes(
    value: object,
    *,
    redaction_policy: _RedactionPolicy,
) -> bytes:
    try:
        redacted = redaction_policy.redact_authority_value(value)
        payload = _canonical_json_bytes(redacted)
    except Exception:
        return _canonical_json_bytes({"redaction_failed": True})
    if len(payload) <= RUNNER_SESSION_COMPLETION_DIAGNOSTIC_MAX_BYTES:
        return payload
    return _canonical_json_bytes(
        {
            "full_diagnostic_digest": f"sha256:{sha256(payload).hexdigest()}",
            "observed_bytes": len(payload),
            "truncated": True,
        }
    )


def _completion_diagnostic_bytes(
    request: _DiagnosticRequest,
    value: object,
) -> bytes:
    return _completion_diagnostic_bytes_for_dispatch(
        request.dispatch_envelope,
        value,
        redaction_policy=request.redaction_policy,
    )


def _completion_diagnostic_bytes_for_dispatch(
    dispatch: RunnerDispatchEnvelope,
    value: object,
    *,
    redaction_policy: _RedactionPolicy,
) -> bytes:
    if isinstance(value, bytes):
        if len(value) > RUNNER_SESSION_COMPLETION_DIAGNOSTIC_MAX_BYTES:
            decoded = {"redaction_failed": True}
        else:
            try:
                decoded = json.loads(value.decode("utf-8"))
            except (RecursionError, TypeError, UnicodeDecodeError, ValueError):
                decoded = {"redaction_failed": True}
    else:
        bounded = _bounded_session_diagnostic_bytes(
            value,
            redaction_policy=redaction_policy,
        )
        try:
            decoded = json.loads(bounded.decode("utf-8"))
        except (RecursionError, TypeError, UnicodeDecodeError, ValueError):
            decoded = {"redaction_failed": True}
    if not isinstance(decoded, Mapping):
        decoded = {"redaction_failed": True}

    def build(diagnostic: Mapping[str, AuthorityValue]) -> bytes:
        return runner_session_completion_diagnostic_bytes(
            RunnerSessionCompletionDiagnostic(
                run_id=dispatch.run_id,
                session_id=dispatch.session_id,
                dispatch_generation=dispatch.dispatch_generation,
                session_fencing_token=dispatch.session_fencing_token,
                plan_fingerprint=dispatch.plan_fingerprint,
                claim_id=dispatch.claim_id,
                generation=dispatch.generation,
                fencing_token=dispatch.fencing_token,
                stage_kind_id=dispatch.stage_kind_id,
                graph_node_id=dispatch.graph_node_id,
                runner_binding_id=dispatch.runner_binding_id,
                diagnostic=decoded,
            )
        )

    try:
        return build(decoded)
    except (RecursionError, TypeError, ValueError):
        return build({"redaction_failed": True})


def _signal_digest(value: object) -> str:
    payload = _canonical_json_bytes(_stable_signal_value(value))
    return f"sha256:{sha256(payload).hexdigest()}"


def _stable_signal_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool)):
        return value
    if type(value) is int:
        return value
    if isinstance(value, float):
        return {"float_hex": value.hex()}
    if isinstance(value, Mapping):
        return {str(key): _stable_signal_value(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_stable_signal_value(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "record_type": f"{type(value).__module__}.{type(value).__qualname__}",
            **{
                item.name: _stable_signal_value(getattr(value, item.name))
                for item in fields(value)
            },
        }
    return {
        "value_type": f"{type(value).__module__}.{type(value).__qualname__}",
    }


def _plain_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json_value(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_plain_json_value(item) for item in value]
    return value
