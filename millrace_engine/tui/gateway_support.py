"""Shared execution and normalization helpers for the TUI runtime gateway."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

from pydantic import ValidationError

from ..control_common import (
    ControlError,
    expected_error_message,
    single_line_message,
    validation_error_message,
)
from .formatting import runtime_event_view
from .models import EventLogView, FailureCategory, GatewayFailure, GatewayResult

T = TypeVar("T")
ControlT = TypeVar("ControlT")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def resolve_config_path(config_path: Path | str) -> Path:
    resolved = Path(config_path).expanduser()
    return resolved.resolve() if not resolved.is_absolute() else resolved


def input_failure(operation: str, message: str) -> GatewayResult[Any]:
    return GatewayResult(
        failure=GatewayFailure(
            operation=operation,
            category=FailureCategory.INPUT,
            message=message,
            exception_type="ValueError",
            retryable=False,
        )
    )


def failure_from_exception(operation: str, exc: Exception) -> GatewayFailure:
    if isinstance(exc, ControlError):
        message = single_line_message(exc) or "control operation failed"
        category = FailureCategory.CONTROL
    elif isinstance(exc, ValidationError):
        message = validation_error_message(exc)
        category = FailureCategory.CONTROL
    elif isinstance(exc, OSError):
        message = expected_error_message(exc) or "file system operation failed"
        category = FailureCategory.IO
    else:
        message = expected_error_message(exc) or exc.__class__.__name__
        category = FailureCategory.UNEXPECTED
    return GatewayFailure(
        operation=operation,
        category=category,
        message=message,
        exception_type=exc.__class__.__name__,
        retryable=category is not FailureCategory.INPUT,
    )


def execute_gateway_operation(
    new_control: Callable[[], ControlT],
    operation: str,
    callback: Callable[[ControlT], T],
) -> GatewayResult[T]:
    try:
        return GatewayResult(value=callback(new_control()))
    except Exception as exc:  # noqa: BLE001
        return GatewayResult(failure=failure_from_exception(operation, exc))


def normalized_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.strip().split())
    return normalized or None


def event_log_view(events: list[object], *, refreshed_at: datetime) -> EventLogView:
    return EventLogView(
        events=tuple(runtime_event_view(record) for record in events),
        last_loaded_at=refreshed_at,
    )


__all__ = [
    "event_log_view",
    "execute_gateway_operation",
    "failure_from_exception",
    "input_failure",
    "normalized_optional_text",
    "resolve_config_path",
    "utcnow",
]
