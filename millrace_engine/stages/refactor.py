"""LARGE refactor stage."""

from __future__ import annotations

from ..contracts import ExecutionStatus, StageType
from .base import ExecutionStage


class RefactorStage(ExecutionStage):
    stage_type = StageType.REFACTOR
    running_status = ExecutionStatus.BUILDER_RUNNING
    success_status = ExecutionStatus.LARGE_REFACTOR_COMPLETE
    allowed_terminal_markers = frozenset(
        {ExecutionStatus.LARGE_REFACTOR_COMPLETE, ExecutionStatus.BLOCKED}
    )


Stage = RefactorStage
