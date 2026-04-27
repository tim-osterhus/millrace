"""Public compiler outcome contracts."""

from __future__ import annotations

from dataclasses import dataclass

from millrace_ai.architecture import CompiledRunPlan, CompileInputFingerprint
from millrace_ai.contracts import CompileDiagnostics
from millrace_ai.errors import ConfigurationError


class CompilerValidationError(ConfigurationError):
    """Raised when a mode bundle fails reduced-compiler validation rules."""


@dataclass(frozen=True, slots=True)
class CompileOutcome:
    """Result of one compile attempt including fallback state."""

    active_plan: CompiledRunPlan | None
    diagnostics: CompileDiagnostics
    used_last_known_good: bool
    compile_input_fingerprint: CompileInputFingerprint | None = None


@dataclass(frozen=True, slots=True)
class CompiledPlanCurrentness:
    """Read-only comparison between persisted compiled plan and current compile inputs."""

    state: str
    expected_fingerprint: CompileInputFingerprint
    persisted_plan_id: str | None
    persisted_fingerprint: CompileInputFingerprint | None


__all__ = [
    "CompiledPlanCurrentness",
    "CompileOutcome",
    "CompilerValidationError",
]
