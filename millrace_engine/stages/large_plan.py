"""LARGE plan stage."""

from __future__ import annotations

from ..contracts import ExecutionStatus, StageType
from .base import ExecutionStage


class LargePlanStage(ExecutionStage):
    stage_type = StageType.LARGE_PLAN
    running_status = ExecutionStatus.BUILDER_RUNNING
    success_status = ExecutionStatus.LARGE_PLAN_COMPLETE
    allowed_terminal_markers = frozenset(
        {ExecutionStatus.LARGE_PLAN_COMPLETE, ExecutionStatus.BLOCKED}
    )


Stage = LargePlanStage
