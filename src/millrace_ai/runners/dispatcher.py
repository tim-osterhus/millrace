"""Runtime-facing dispatcher that resolves and invokes stage runner adapters."""

from __future__ import annotations

from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import CapabilitySupportDecision, ExecutionCapabilityGrant
from millrace_ai.runners.base import default_capability_support_decision
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

    def evaluate_capability_grant(
        self,
        grant: ExecutionCapabilityGrant,
        request: StageRunRequest,
    ) -> CapabilitySupportDecision:
        runner_name = self.resolve_runner_name(request)
        adapter = self.registry.get(runner_name)
        if adapter is None:
            return default_capability_support_decision(
                grant,
                {"request": request, "stage": request.stage.value},
            ).model_copy(update={"runner_id": runner_name, "reason": "unknown runner"})
        evaluator = getattr(adapter, "evaluate_capability_grant", None)
        if evaluator is None:
            return default_capability_support_decision(
                grant,
                {"request": request, "stage": request.stage.value},
            )
        return evaluator(grant, {"request": request, "stage": request.stage.value})


__all__ = ["StageRunnerDispatcher"]
