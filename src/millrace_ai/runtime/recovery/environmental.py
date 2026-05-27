"""Pure blocked-failure classification helpers."""

from __future__ import annotations

from collections.abc import Mapping, Set
from typing import Literal, cast

BlockedOrigin = Literal[
    "stage_terminal",
    "runner_failure",
    "runtime_exception",
    "operator",
    "unknown",
]
FailureScope = Literal[
    "environment",
    "provider",
    "local_configuration",
    "contract",
    "semantic",
    "unknown",
]

_BLOCKED_ORIGINS: Set[str] = {
    "stage_terminal",
    "runner_failure",
    "runtime_exception",
    "operator",
    "unknown",
}
_FAILURE_SCOPES: Set[str] = {
    "environment",
    "provider",
    "local_configuration",
    "contract",
    "semantic",
    "unknown",
}
_AUTO_RETRYABLE_SCOPE: dict[FailureScope, bool] = {
    "environment": True,
    "provider": True,
    "local_configuration": True,
    "contract": False,
    "semantic": False,
    "unknown": True,
}


def blocked_origin_from_metadata(metadata: Mapping[str, object]) -> BlockedOrigin:
    raw_origin = metadata.get("blocked_origin")
    if isinstance(raw_origin, str) and raw_origin in _BLOCKED_ORIGINS:
        return cast(BlockedOrigin, raw_origin)
    if metadata.get("normalization_source") == "failure":
        return "runner_failure"
    return "stage_terminal"


def failure_scope_from_metadata(
    metadata: Mapping[str, object],
    *,
    blocked_origin: BlockedOrigin,
) -> FailureScope:
    raw_scope = metadata.get("failure_scope")
    if isinstance(raw_scope, str) and raw_scope in _FAILURE_SCOPES:
        return cast(FailureScope, raw_scope)
    if blocked_origin == "stage_terminal":
        return "semantic"
    return "unknown"


def auto_retryable_scope(scope: FailureScope) -> bool:
    return _AUTO_RETRYABLE_SCOPE[scope]


__all__ = [
    "BlockedOrigin",
    "FailureScope",
    "auto_retryable_scope",
    "blocked_origin_from_metadata",
    "failure_scope_from_metadata",
]
