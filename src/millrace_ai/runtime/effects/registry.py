"""Runtime effect handler registry seam."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from millrace_ai.contracts import StageResultEnvelope
from millrace_ai.workspace.paths import WorkspacePaths

from .models import RuntimeEffectResult

RuntimeEffectHandler = Callable[
    [WorkspacePaths, StageResultEnvelope, Path, Any],
    RuntimeEffectResult,
]


@dataclass(frozen=True, slots=True)
class RuntimeEffectHandlerRegistration:
    handler_id: str
    runner_id: str
    handler: RuntimeEffectHandler


@dataclass(slots=True)
class RuntimeEffectHandlerRegistry:
    handlers_by_id: dict[str, RuntimeEffectHandler] = field(default_factory=dict)
    runner_ids_by_handler_id: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_registrations(
        cls,
        registrations: Iterable[RuntimeEffectHandlerRegistration],
    ) -> "RuntimeEffectHandlerRegistry":
        registry = cls()
        for registration in registrations:
            registry.register(registration)
        return registry

    def register(self, registration: RuntimeEffectHandlerRegistration) -> None:
        previous = self.handlers_by_id.get(registration.handler_id)
        if previous is not None and previous is not registration.handler:
            raise ValueError(f"duplicate runtime effect handler id: {registration.handler_id}")
        self.handlers_by_id[registration.handler_id] = registration.handler
        self.runner_ids_by_handler_id[registration.handler_id] = registration.runner_id

    def handler_for(self, handler_id: str) -> RuntimeEffectHandler | None:
        return self.handlers_by_id.get(handler_id)

    def runner_id_for(self, handler_id: str) -> str | None:
        return self.runner_ids_by_handler_id.get(handler_id)


__all__ = [
    "RuntimeEffectHandler",
    "RuntimeEffectHandlerRegistration",
    "RuntimeEffectHandlerRegistry",
]
