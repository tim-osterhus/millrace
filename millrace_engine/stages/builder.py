"""Builder stage."""

from __future__ import annotations

from .base import ExecutionStage
from ..contracts import ExecutionStatus, StageType


class BuilderStage(ExecutionStage):
    stage_type = StageType.BUILDER
    running_status = ExecutionStatus.BUILDER_RUNNING
    success_status = ExecutionStatus.BUILDER_COMPLETE
    allowed_terminal_markers = frozenset(
        {ExecutionStatus.BUILDER_COMPLETE, ExecutionStatus.BLOCKED}
    )


Stage = BuilderStage
