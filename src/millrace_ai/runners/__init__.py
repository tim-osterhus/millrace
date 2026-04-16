"""Runner package surface."""

from millrace_ai.runners.base import StageRunnerAdapter
from millrace_ai.runners.dispatcher import StageRunnerDispatcher
from millrace_ai.runners.normalization import normalize_stage_result
from millrace_ai.runners.registry import RunnerRegistry
from millrace_ai.runners.requests import (
    RunnerExitKind,
    RunnerRawResult,
    StageRunRequest,
    render_stage_request_context_lines,
)

__all__ = [
    "RunnerExitKind",
    "RunnerRawResult",
    "RunnerRegistry",
    "StageRunnerAdapter",
    "StageRunnerDispatcher",
    "StageRunRequest",
    "normalize_stage_result",
    "render_stage_request_context_lines",
]
