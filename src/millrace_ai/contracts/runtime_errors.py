"""Runtime error context contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import model_validator

from .base import ContractModel
from .enums import Plane, RuntimeErrorCode, StageName, TerminalResult, WorkItemKind
from .stage_metadata import stage_plane


class RuntimeErrorContext(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["runtime_error_context"] = "runtime_error_context"

    error_code: RuntimeErrorCode
    plane: Plane
    failed_stage: StageName
    repair_stage: StageName
    work_item_kind: WorkItemKind
    work_item_id: str
    run_id: str

    router_action: str | None = None
    terminal_result: TerminalResult | None = None
    stage_result_path: str | None = None
    report_path: str

    exception_type: str
    exception_message: str
    captured_at: datetime

    @model_validator(mode="after")
    def validate_stage_alignment(self) -> "RuntimeErrorContext":
        if stage_plane(self.failed_stage) != self.plane:
            raise ValueError("failed_stage must belong to plane")
        if stage_plane(self.repair_stage) != self.plane:
            raise ValueError("repair_stage must belong to plane")
        return self


__all__ = ["RuntimeErrorContext"]
