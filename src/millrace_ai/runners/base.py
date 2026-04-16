"""Runner adapter interface contracts."""

from __future__ import annotations

from typing import Protocol

from millrace_ai.runner import RunnerRawResult, StageRunRequest


class StageRunnerAdapter(Protocol):
    """Interface implemented by concrete stage runner adapters."""

    @property
    def name(self) -> str:
        """Stable adapter identifier used in mode/config resolution."""

    def run(self, request: StageRunRequest) -> RunnerRawResult:
        """Execute one stage request and return raw runner output."""


__all__ = ["StageRunnerAdapter"]
