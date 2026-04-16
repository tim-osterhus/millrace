"""Adapter registry for stage-runner dispatch."""

from __future__ import annotations

from collections.abc import Iterable

from millrace_ai.runners.base import StageRunnerAdapter


class RunnerRegistry:
    """Maps stable runner names to adapter implementations."""

    def __init__(self, adapters: Iterable[StageRunnerAdapter] | None = None) -> None:
        self._adapters: dict[str, StageRunnerAdapter] = {}
        if adapters is not None:
            for adapter in adapters:
                self.register(adapter)

    def register(self, adapter: StageRunnerAdapter) -> None:
        self._adapters[adapter.name] = adapter

    def get(self, runner_name: str) -> StageRunnerAdapter | None:
        return self._adapters.get(runner_name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))


__all__ = ["RunnerRegistry"]
