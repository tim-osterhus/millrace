"""Transport-readiness policy helpers."""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from shutil import which
from typing import Any, Protocol

from pydantic import Field, field_validator

from ..contracts import ContractModel, RunnerKind


class TransportReadiness(str, Enum):
    """Normalized transport-readiness outcomes."""

    READY = "ready"
    ENV_BLOCKED = "env_blocked"
    NET_WAIT = "net_wait"


class TransportProbeContext(ContractModel):
    """Runner transport inputs evaluated during preflight."""

    runner: RunnerKind
    model: str | None = None
    command: tuple[str, ...] = ()

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("command", mode="before")
    @classmethod
    def normalize_command(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        return tuple(str(item).strip() for item in value if str(item).strip())


class TransportProbeResult(ContractModel):
    """Typed readiness result for one stage transport check."""

    readiness: TransportReadiness
    summary: str
    command: tuple[str, ...] = ()
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("summary may not be empty")
        return normalized

    @field_validator("command", mode="before")
    @classmethod
    def normalize_command(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        return tuple(str(item).strip() for item in value if str(item).strip())

    @field_validator("details", mode="before")
    @classmethod
    def normalize_details(cls, value: dict[str, Any] | None) -> dict[str, Any]:
        return {str(key): item for key, item in (value or {}).items()}


class TransportProbe(Protocol):
    """Interface for runner transport-readiness probes."""

    def check(self, context: TransportProbeContext) -> TransportProbeResult:
        """Return the normalized readiness result for one stage."""


class DefaultTransportProbe:
    """Deterministic local transport probe for execution preflight."""

    OVERRIDE_ENV = "MILLRACE_TRANSPORT_STATUS"

    def check(self, context: TransportProbeContext) -> TransportProbeResult:
        override = os.environ.get(self.OVERRIDE_ENV, "").strip().lower()
        if override:
            readiness = TransportReadiness(override)
            return TransportProbeResult(
                readiness=readiness,
                summary=f"Transport readiness forced via {self.OVERRIDE_ENV}.",
                details={"override_env": self.OVERRIDE_ENV, "override_value": readiness.value},
            )

        if context.runner is RunnerKind.SUBPROCESS:
            return self._check_subprocess(context)
        executable = "codex" if context.runner is RunnerKind.CODEX else "claude"
        resolved = which(executable)
        if resolved is None:
            return TransportProbeResult(
                readiness=TransportReadiness.ENV_BLOCKED,
                summary=f"{executable} is not available on PATH.",
                details={"runner": context.runner.value, "executable": executable},
            )
        return TransportProbeResult(
            readiness=TransportReadiness.READY,
            summary=f"{executable} transport is available.",
            details={"runner": context.runner.value, "executable": executable, "resolved_path": resolved},
        )

    def _check_subprocess(self, context: TransportProbeContext) -> TransportProbeResult:
        if not context.command:
            return TransportProbeResult(
                readiness=TransportReadiness.ENV_BLOCKED,
                summary="Subprocess runner requires an explicit command.",
                details={"runner": context.runner.value},
            )
        token = context.command[0]
        resolved = self._resolve_command(token)
        if resolved is None:
            return TransportProbeResult(
                readiness=TransportReadiness.ENV_BLOCKED,
                summary=f"Subprocess command {token!r} is not available.",
                details={"runner": context.runner.value, "command": list(context.command)},
            )
        return TransportProbeResult(
            readiness=TransportReadiness.READY,
            summary="Subprocess transport is available.",
            details={"runner": context.runner.value, "command": list(context.command), "resolved_path": resolved},
        )

    def _resolve_command(self, token: str) -> str | None:
        candidate = Path(token).expanduser()
        if candidate.is_absolute() or candidate.parent != Path("."):
            return str(candidate.resolve()) if candidate.exists() else None
        return which(token)


class StaticTransportProbe:
    """Deterministic test probe that always returns the configured result."""

    def __init__(self, result: TransportProbeResult) -> None:
        self._result = result

    def check(self, context: TransportProbeContext) -> TransportProbeResult:
        details = dict(self._result.details)
        details.setdefault("runner", context.runner.value)
        if context.command:
            details.setdefault("command", list(context.command))
        return self._result.model_copy(update={"command": context.command, "details": details})


__all__ = [
    "DefaultTransportProbe",
    "StaticTransportProbe",
    "TransportProbe",
    "TransportProbeContext",
    "TransportProbeResult",
    "TransportReadiness",
]
