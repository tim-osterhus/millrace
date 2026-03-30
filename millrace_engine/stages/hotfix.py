"""Hotfix stage."""

from __future__ import annotations

from .base import ExecutionStage
from ..contracts import ExecutionStatus, StageType


class HotfixStage(ExecutionStage):
    stage_type = StageType.HOTFIX
    running_status = ExecutionStatus.HOTFIX_RUNNING
    success_status = ExecutionStatus.BUILDER_COMPLETE
    allowed_terminal_markers = frozenset(
        {ExecutionStatus.BUILDER_COMPLETE, ExecutionStatus.BLOCKED}
    )


Stage = HotfixStage
