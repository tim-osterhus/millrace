"""Runtime-facing dispatcher that resolves and invokes stage runner adapters."""

from __future__ import annotations

from millrace_ai.config import RuntimeConfig
from millrace_ai.runners.errors import UnknownRunnerError
from millrace_ai.runners.registry import RunnerRegistry
from millrace_ai.runners.requests import RunnerRawResult, StageRunRequest


class StageRunnerDispatcher:
    """Callable stage runner that delegates to a resolved adapter."""

    def __init__(self, *, registry: RunnerRegistry, config: RuntimeConfig) -> None:
        self.registry = registry
        self.config = config

    def resolve_runner_name(self, request: StageRunRequest) -> str:
        if request.runner_name is not None and request.runner_name.strip():
            return request.runner_name.strip()

        if self.config.runners.default_runner.strip():
            return self.config.runners.default_runner.strip()

        return "codex_cli"

    def __call__(self, request: StageRunRequest) -> RunnerRawResult:
        runner_name = self.resolve_runner_name(request)
        adapter = self.registry.get(runner_name)
        if adapter is None:
            raise UnknownRunnerError(
                f"Unknown stage runner: {runner_name}. Available: {', '.join(self.registry.names()) or 'none'}"
            )
        return adapter.run(request)


__all__ = ["StageRunnerDispatcher"]
