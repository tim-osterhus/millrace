"""Integration stage."""

from __future__ import annotations

from ..contracts import ExecutionStatus, StageType
from .base import ExecutionStage


class IntegrationStage(ExecutionStage):
    stage_type = StageType.INTEGRATION
    running_status = ExecutionStatus.INTEGRATION_RUNNING
    success_status = ExecutionStatus.INTEGRATION_COMPLETE
    allowed_terminal_markers = frozenset(
        {ExecutionStatus.INTEGRATION_COMPLETE, ExecutionStatus.BLOCKED}
    )


Stage = IntegrationStage
