"""Consult stage."""

from __future__ import annotations

from ..contracts import ExecutionStatus, StageType
from .base import ExecutionStage


class ConsultStage(ExecutionStage):
    stage_type = StageType.CONSULT
    running_status = ExecutionStatus.CONSULT_RUNNING
    success_status = ExecutionStatus.CONSULT_COMPLETE
    allowed_terminal_markers = frozenset(
        {
            ExecutionStatus.CONSULT_COMPLETE,
            ExecutionStatus.NEEDS_RESEARCH,
            ExecutionStatus.BLOCKED,
        }
    )


Stage = ConsultStage
