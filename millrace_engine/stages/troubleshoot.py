"""Troubleshoot stage."""

from __future__ import annotations

from ..contracts import ExecutionStatus, StageType
from .base import ExecutionStage


class TroubleshootStage(ExecutionStage):
    stage_type = StageType.TROUBLESHOOT
    running_status = ExecutionStatus.TROUBLESHOOT_RUNNING
    success_status = ExecutionStatus.TROUBLESHOOT_COMPLETE
    allowed_terminal_markers = frozenset(
        {ExecutionStatus.TROUBLESHOOT_COMPLETE, ExecutionStatus.BLOCKED}
    )


Stage = TroubleshootStage
