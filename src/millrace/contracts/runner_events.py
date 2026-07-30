"""Bounded, non-authoritative runner-session event contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import ClassVar

from millrace.contracts.compiled_plan import AuthorityValue, freeze_authority_value

RUNNER_SESSION_EVENT_SCHEMA_VERSION = 1
RUNNER_SESSION_EVENT_MAX_PAYLOAD_BYTES = 16 * 1024
RUNNER_SESSION_EVENT_KINDS = frozenset(
    {
        "session_started",
        "runner_progress",
        "tool_activity",
        "usage_update",
        "diagnostic",
        "cancellation_progress",
        "session_terminal",
    }
)


@dataclass(frozen=True, slots=True)
class RunnerSessionEvent:
    """A public live projection that carries no workflow authority."""

    schema_version: ClassVar[int] = RUNNER_SESSION_EVENT_SCHEMA_VERSION

    event_id: str
    session_id: str
    run_id: str
    dispatch_generation: int
    sequence: int
    kind: str
    observed_at: int
    bounded_payload: Mapping[str, AuthorityValue] = field(default_factory=dict)
    redaction_policy_id: str = ""
    truncation_metadata: Mapping[str, AuthorityValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_event_text(self)
        for name in ("dispatch_generation", "sequence", "observed_at"):
            value = getattr(self, name)
            if type(value) is not int:
                raise TypeError(f"{name} must be an integer")
        if self.dispatch_generation < 1:
            raise ValueError("dispatch_generation must be positive")
        if self.sequence < 1:
            raise ValueError("sequence must be positive")
        if self.observed_at < 0:
            raise ValueError("observed_at must be nonnegative")
        if self.kind not in RUNNER_SESSION_EVENT_KINDS:
            raise ValueError("kind is not a supported runner-session event kind")
        payload = _freeze_mapping(self.bounded_payload, "bounded_payload")
        truncation = _freeze_mapping(
            self.truncation_metadata,
            "truncation_metadata",
        )
        if len(_canonical_bytes(payload)) > RUNNER_SESSION_EVENT_MAX_PAYLOAD_BYTES:
            raise ValueError("bounded_payload exceeds the event payload ceiling")
        object.__setattr__(self, "bounded_payload", payload)
        object.__setattr__(self, "truncation_metadata", truncation)

    def payload(self) -> Mapping[str, AuthorityValue]:
        return MappingProxyType(
            {
                "schema_version": self.schema_version,
                "event_id": self.event_id,
                "session_id": self.session_id,
                "run_id": self.run_id,
                "dispatch_generation": self.dispatch_generation,
                "sequence": self.sequence,
                "kind": self.kind,
                "observed_at": self.observed_at,
                "bounded_payload": self.bounded_payload,
                "redaction_policy_id": self.redaction_policy_id,
                "truncation_metadata": self.truncation_metadata,
            }
        )


def _validate_event_text(event: RunnerSessionEvent) -> None:
    for name in ("event_id", "session_id", "run_id", "redaction_policy_id"):
        value = getattr(event, name)
        if not isinstance(value, str):
            raise TypeError(f"{name} must be a string")
        if not value.strip():
            raise ValueError(f"{name} must be nonblank")


def _freeze_mapping(
    value: object,
    name: str,
) -> Mapping[str, AuthorityValue]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    frozen = freeze_authority_value(value)
    if not isinstance(frozen, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return frozen


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    ).encode("utf-8")


def _json_default(value: object) -> object:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


__all__ = (
    "RUNNER_SESSION_EVENT_KINDS",
    "RUNNER_SESSION_EVENT_MAX_PAYLOAD_BYTES",
    "RUNNER_SESSION_EVENT_SCHEMA_VERSION",
    "RunnerSessionEvent",
)
