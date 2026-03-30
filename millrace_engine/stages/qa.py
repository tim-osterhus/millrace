"""QA stage."""

from __future__ import annotations

from .base import ExecutionStage
from ..contracts import ExecutionStatus, StageType


class QAStage(ExecutionStage):
    stage_type = StageType.QA
    running_status = ExecutionStatus.QA_RUNNING
    success_status = ExecutionStatus.QA_COMPLETE
    allowed_terminal_markers = frozenset(
        {
            ExecutionStatus.QA_COMPLETE,
            ExecutionStatus.QUICKFIX_NEEDED,
            ExecutionStatus.BLOCKED,
        }
    )


Stage = QAStage
