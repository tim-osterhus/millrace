"""Compatibility facade for runner contracts and normalization."""

from millrace_ai.runners import (
    RunnerExitKind,
    RunnerRawResult,
    StageRunRequest,
    normalize_stage_result,
    render_stage_request_context_lines,
)

__all__ = [
    "RunnerExitKind",
    "RunnerRawResult",
    "StageRunRequest",
    "normalize_stage_result",
    "render_stage_request_context_lines",
]
