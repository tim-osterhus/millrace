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
    operation_id: str | None = None


@dataclass(slots=True)
class RuntimeEffectHandlerRegistry:
    handlers_by_id: dict[str, RuntimeEffectHandler] = field(default_factory=dict)
    handlers_by_operation_id: dict[str, RuntimeEffectHandler] = field(default_factory=dict)
    runner_ids_by_handler_id: dict[str, str] = field(default_factory=dict)
    runner_ids_by_operation_id: dict[str, str] = field(default_factory=dict)
    legacy_handler_ids_by_operation_id: dict[str, str] = field(default_factory=dict)

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
        operation_id = registration.operation_id or registration.handler_id
        previous = self.handlers_by_id.get(registration.handler_id)
        if previous is not None and previous is not registration.handler:
            raise ValueError(f"duplicate runtime effect handler id: {registration.handler_id}")
        previous_operation = self.handlers_by_operation_id.get(operation_id)
        if previous_operation is not None and previous_operation is not registration.handler:
            raise ValueError(f"duplicate runtime effect operation id: {operation_id}")
        self.handlers_by_id[registration.handler_id] = registration.handler
        self.handlers_by_operation_id[operation_id] = registration.handler
        self.runner_ids_by_handler_id[registration.handler_id] = registration.runner_id
        self.runner_ids_by_operation_id[operation_id] = registration.runner_id
        self.legacy_handler_ids_by_operation_id[operation_id] = registration.handler_id

    def handler_for(self, handler_id: str) -> RuntimeEffectHandler | None:
        return self.handlers_by_id.get(handler_id)

    def handler_for_operation(self, operation_id: str) -> RuntimeEffectHandler | None:
        return self.handlers_by_operation_id.get(operation_id) or self.handlers_by_id.get(operation_id)

    def runner_id_for(self, handler_id: str) -> str | None:
        return self.runner_ids_by_handler_id.get(handler_id)

    def runner_id_for_operation(self, operation_id: str) -> str | None:
        return self.runner_ids_by_operation_id.get(operation_id) or self.runner_ids_by_handler_id.get(operation_id)

    def legacy_handler_id_for_operation(self, operation_id: str) -> str | None:
        return self.legacy_handler_ids_by_operation_id.get(operation_id)


__all__ = [
    "RuntimeEffectHandler",
    "RuntimeEffectHandlerRegistration",
    "RuntimeEffectHandlerRegistry",
]
