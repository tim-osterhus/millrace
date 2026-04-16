"""Runner-layer exceptions for adapter resolution and process execution."""

from __future__ import annotations


class RunnerError(RuntimeError):
    """Base class for runner subsystem exceptions."""


class UnknownRunnerError(RunnerError):
    """Raised when dispatcher cannot resolve a runner adapter by name."""


class RunnerBinaryNotFoundError(RunnerError):
    """Raised when configured runner executable is not available on PATH."""


__all__ = [
    "RunnerBinaryNotFoundError",
    "RunnerError",
    "UnknownRunnerError",
]
