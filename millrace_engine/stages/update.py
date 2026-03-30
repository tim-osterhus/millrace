"""Update stage."""

from __future__ import annotations

from .base import ExecutionStage
from ..contracts import ExecutionStatus, StageType


class UpdateStage(ExecutionStage):
    stage_type = StageType.UPDATE
    running_status = ExecutionStatus.UPDATE_RUNNING
    success_status = ExecutionStatus.UPDATE_COMPLETE
    allowed_terminal_markers = frozenset(
        {ExecutionStatus.UPDATE_COMPLETE, ExecutionStatus.BLOCKED}
    )
    synthesized_success_status = ExecutionStatus.UPDATE_COMPLETE


Stage = UpdateStage
