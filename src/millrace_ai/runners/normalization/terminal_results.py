"""Terminal result and result-class mapping helpers."""

from __future__ import annotations

from millrace_ai.contracts import ResultClass, TerminalOutcome
from millrace_ai.runners.requests import StageRunRequest


def terminal_result_for_request(
    request: StageRunRequest,
    token: str,
) -> TerminalOutcome | None:
    if f"### {token}" not in request.legal_terminal_markers:
        return None
    return TerminalOutcome(token)


def resolve_result_class(
    request: StageRunRequest,
    terminal_token: str,
    raw_result_class: str | None,
) -> ResultClass | None:
    allowed_result_classes = request.allowed_result_classes_by_outcome.get(terminal_token)
    if not allowed_result_classes:
        return None
    if raw_result_class is None:
        if len(allowed_result_classes) == 1:
            return allowed_result_classes[0]
        if ResultClass.BLOCKED in allowed_result_classes:
            return ResultClass.BLOCKED
        return None

    try:
        result_class = ResultClass(raw_result_class)
    except ValueError:
        return None

    if result_class not in allowed_result_classes:
        return None
    return result_class


__all__ = ["resolve_result_class", "terminal_result_for_request"]
