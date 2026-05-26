"""Runtime failure-origin classification helpers."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from pydantic import ValidationError

from millrace_ai.contracts import RuntimeFailureOrigin

from .compiled_plans import CompiledPlanAuthorityError


class RuntimeFailureBoundary(str, Enum):
    RUNNER_INVOCATION = "runner_invocation"
    REQUEST_CONTEXT = "request_context"
    PROMPT_RENDER = "prompt_render"
    RUNTIME_PRIMITIVE = "runtime_primitive"
    DOCUMENT_ADAPTER = "document_adapter"
    FILESYSTEM_IO = "filesystem_io"
    RESULT_APPLICATION = "result_application"
    RELOAD = "reload"


@dataclass(frozen=True, slots=True)
class RuntimeEffectFailurePolicyInput:
    failure_class: str | None
    mutation_phase: str
    handler_id: str | None
    source_node_id: str | None
    source_terminal_state_id: str | None
    source_plane: str | None
    source_family_id: str | None
    created_paths: tuple[str, ...]
    message: str | None
    operation_id: str | None = None
    runner_id: str | None = None
    legacy_handler_id: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeFailurePolicyInterpretation:
    policy_id: str
    action: str
    failure_class: str
    target_node_id: str | None = None
    target_terminal_state_id: str | None = None
    max_attempts: int | None = None
    incident_severity: str | None = None


def classify_failure_origin(
    error: Exception,
    *,
    boundary: RuntimeFailureBoundary,
) -> RuntimeFailureOrigin:
    """Map an exception and boundary to a stable runtime failure origin."""

    if isinstance(error, CompiledPlanAuthorityError):
        return RuntimeFailureOrigin.WORKSPACE_INTEGRITY_FAILURE
    if boundary is RuntimeFailureBoundary.REQUEST_CONTEXT:
        return RuntimeFailureOrigin.REQUEST_CONTEXT_PROVIDER_FAILURE
    if boundary is RuntimeFailureBoundary.PROMPT_RENDER:
        return RuntimeFailureOrigin.PROMPT_RENDER_FAILURE
    if boundary is RuntimeFailureBoundary.RUNTIME_PRIMITIVE:
        return RuntimeFailureOrigin.RUNTIME_PRIMITIVE_EXCEPTION
    if boundary is RuntimeFailureBoundary.FILESYSTEM_IO:
        return RuntimeFailureOrigin.FILESYSTEM_IO_FAILURE
    if boundary is RuntimeFailureBoundary.DOCUMENT_ADAPTER:
        if isinstance(error, json.JSONDecodeError):
            return RuntimeFailureOrigin.DOCUMENT_ADAPTER_PARSE_FAILURE
        if isinstance(error, ValidationError):
            return RuntimeFailureOrigin.DOCUMENT_ADAPTER_VALIDATION_FAILURE
    message = str(error).lower()
    if "network" in message:
        return RuntimeFailureOrigin.NETWORK_UNAVAILABLE
    if "provider" in message or "model" in message:
        return RuntimeFailureOrigin.MODEL_PROVIDER_UNAVAILABLE
    if isinstance(error, OSError):
        return RuntimeFailureOrigin.FILESYSTEM_IO_FAILURE
    return RuntimeFailureOrigin.RUNTIME_PRIMITIVE_EXCEPTION


def interpret_runtime_effect_failure_policy(
    policies: Iterable[object],
    failure: RuntimeEffectFailurePolicyInput,
) -> RuntimeFailurePolicyInterpretation | None:
    """Resolve a runtime effect failure through declared runtime failure policies."""

    if (
        not failure.failure_class
        or not (failure.handler_id or failure.operation_id or failure.legacy_handler_id)
        or not failure.source_node_id
        or not failure.source_plane
        or not failure.source_family_id
    ):
        return None
    for policy in policies:
        if not _policy_matches_effect_failure(policy, failure):
            continue
        action = str(getattr(policy, "action"))
        if failure.mutation_phase == "partial_mutation" and action == "route_to_node":
            continue
        return RuntimeFailurePolicyInterpretation(
            policy_id=str(getattr(policy, "policy_id")),
            action=action,
            failure_class=failure.failure_class,
            target_node_id=getattr(policy, "target_node_id", None),
            target_terminal_state_id=getattr(policy, "target_terminal_state_id", None),
            max_attempts=getattr(policy, "max_attempts", None),
            incident_severity=getattr(policy, "incident_severity", None),
        )
    return None


def _policy_matches_effect_failure(
    policy: object,
    failure: RuntimeEffectFailurePolicyInput,
) -> bool:
    if "runtime_effect" not in _tuple_attr(policy, "applies_to_origins"):
        return False
    if failure.source_plane not in _tuple_attr(policy, "applies_to_planes"):
        return False
    if not _matches_optional_tuple(
        _tuple_attr(policy, "applies_to_families"),
        failure.source_family_id,
    ):
        return False
    if not _matches_optional_tuple(
        _tuple_attr(policy, "applies_to_failure_classes"),
        failure.failure_class,
    ):
        return False
    if not _matches_optional_tuple(
        _tuple_attr(policy, "applies_to_mutation_phases"),
        failure.mutation_phase,
    ):
        return False
    if not _matches_optional_tuple(
        _tuple_attr(policy, "applies_to_operation_ids"),
        failure.operation_id,
    ):
        return False
    if not _matches_optional_tuple_any(
        _tuple_attr(policy, "applies_to_handler_ids"),
        (failure.handler_id, failure.legacy_handler_id),
    ):
        return False
    if not _matches_optional_tuple(
        _tuple_attr(policy, "applies_to_source_node_ids"),
        failure.source_node_id,
    ):
        return False
    return _matches_optional_tuple(
        _tuple_attr(policy, "applies_to_source_terminal_state_ids"),
        failure.source_terminal_state_id,
    )


def _matches_optional_tuple(values: tuple[str, ...], candidate: str | None) -> bool:
    return not values or (candidate is not None and candidate in values)


def _matches_optional_tuple_any(values: tuple[str, ...], candidates: tuple[str | None, ...]) -> bool:
    return not values or any(candidate is not None and candidate in values for candidate in candidates)


def _tuple_attr(policy: object, name: str) -> tuple[str, ...]:
    value = getattr(policy, name, ())
    return tuple(str(item.value if hasattr(item, "value") else item) for item in value)


__all__ = [
    "RuntimeEffectFailurePolicyInput",
    "RuntimeFailureBoundary",
    "RuntimeFailurePolicyInterpretation",
    "classify_failure_origin",
    "interpret_runtime_effect_failure_policy",
]
