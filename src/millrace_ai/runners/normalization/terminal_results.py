"""Terminal result and result-class mapping helpers."""

from __future__ import annotations

from millrace_ai.contracts import ResultClass, TerminalResult
from millrace_ai.contracts.stage_metadata import terminal_result_for_plane
from millrace_ai.runners.requests import StageRunRequest


def terminal_result_for_request(
    request: StageRunRequest,
    token: str,
) -> TerminalResult | None:
    if f"### {token}" not in request.legal_terminal_markers:
        return None
    return terminal_result_for_plane(request.plane, token)


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
        if terminal_token == "BLOCKED" and ResultClass.BLOCKED in allowed_result_classes:
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
