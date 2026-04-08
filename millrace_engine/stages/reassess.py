"""LARGE reassess stage."""

from __future__ import annotations

from ..contracts import ExecutionStatus, StageType
from .base import ExecutionStage


class ReassessStage(ExecutionStage):
    stage_type = StageType.REASSESS
    running_status = ExecutionStatus.BUILDER_RUNNING
    success_status = ExecutionStatus.LARGE_REASSESS_COMPLETE
    allowed_terminal_markers = frozenset(
        {ExecutionStatus.LARGE_REASSESS_COMPLETE, ExecutionStatus.BLOCKED}
    )


Stage = ReassessStage
