"""LARGE execute stage."""

from __future__ import annotations

from ..contracts import ExecutionStatus, StageType
from .base import ExecutionStage


class LargeExecuteStage(ExecutionStage):
    stage_type = StageType.LARGE_EXECUTE
    running_status = ExecutionStatus.BUILDER_RUNNING
    success_status = ExecutionStatus.LARGE_EXECUTE_COMPLETE
    allowed_terminal_markers = frozenset(
        {ExecutionStatus.LARGE_EXECUTE_COMPLETE, ExecutionStatus.BLOCKED}
    )


Stage = LargeExecuteStage
