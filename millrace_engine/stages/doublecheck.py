"""Doublecheck stage."""

from __future__ import annotations

from .base import ExecutionStage
from ..contracts import ExecutionStatus, StageType


class DoublecheckStage(ExecutionStage):
    stage_type = StageType.DOUBLECHECK
    running_status = ExecutionStatus.DOUBLECHECK_RUNNING
    success_status = ExecutionStatus.QA_COMPLETE
    allowed_terminal_markers = frozenset(
        {
            ExecutionStatus.QA_COMPLETE,
            ExecutionStatus.QUICKFIX_NEEDED,
            ExecutionStatus.BLOCKED,
        }
    )


Stage = DoublecheckStage
