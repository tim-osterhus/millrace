"""Runtime outcome contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from millrace_ai.contracts import RuntimeSnapshot, StageName, StageResultEnvelope
from millrace_ai.contracts.router import RouterDecision


@dataclass(frozen=True, slots=True)
class RuntimeTickOutcome:
    """Outcome from one runtime tick."""

    stage: StageName | None
    stage_result: StageResultEnvelope | None
    stage_result_path: Path
    router_decision: RouterDecision
    snapshot: RuntimeSnapshot


__all__ = ["RuntimeTickOutcome"]
