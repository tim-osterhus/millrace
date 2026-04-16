"""Runner dispatch and adapter plumbing."""

from millrace_ai.runners.base import StageRunnerAdapter
from millrace_ai.runners.dispatcher import StageRunnerDispatcher
from millrace_ai.runners.registry import RunnerRegistry

__all__ = [
    "RunnerRegistry",
    "StageRunnerAdapter",
    "StageRunnerDispatcher",
]
