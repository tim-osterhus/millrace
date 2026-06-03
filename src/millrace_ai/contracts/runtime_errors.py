"""Runtime error context contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import model_validator

from .base import ContractModel
from .enums import Plane, RuntimeErrorCode, StageName, WorkItemKind
from .stage_metadata import stage_plane
from .terminal_outcomes import TerminalOutcome
from .work_refs import coerce_family_and_kind


class RuntimeFailureOrigin(str, Enum):
    MODEL_PROVIDER_UNAVAILABLE = "model_provider_unavailable"
    NETWORK_UNAVAILABLE = "network_unavailable"
    REQUEST_CONTEXT_PROVIDER_FAILURE = "request_context_provider_failure"
    PROMPT_RENDER_FAILURE = "prompt_render_failure"
    RUNTIME_PRIMITIVE_EXCEPTION = "runtime_primitive_exception"
    DOCUMENT_ADAPTER_PARSE_FAILURE = "document_adapter_parse_failure"
    DOCUMENT_ADAPTER_VALIDATION_FAILURE = "document_adapter_validation_failure"
    FILESYSTEM_IO_FAILURE = "filesystem_io_failure"
    WORKSPACE_INTEGRITY_FAILURE = "workspace_integrity_failure"


class RuntimeErrorContext(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["runtime_error_context"] = "runtime_error_context"

    error_code: RuntimeErrorCode
    plane: Plane
    failed_stage: StageName
    repair_stage: StageName
    work_item_family_id: str | None = None
    work_item_kind: WorkItemKind | None = None
    work_item_id: str
    run_id: str

    router_action: str | None = None
    terminal_result: TerminalOutcome | None = None
    stage_result_path: str | None = None
    report_path: str

    exception_type: str
    exception_message: str
    failure_origin: RuntimeFailureOrigin | None = None
    captured_at: datetime

    @model_validator(mode="after")
    def validate_stage_alignment(self) -> "RuntimeErrorContext":
        family_id, work_item_kind = coerce_family_and_kind(
            family_id=self.work_item_family_id,
            work_item_kind=self.work_item_kind,
        )
        if family_id is None:
            raise ValueError("runtime error context requires work_item_family_id or work_item_kind")
        self.work_item_family_id = family_id
        self.work_item_kind = work_item_kind
        if stage_plane(self.failed_stage) != self.plane:
            raise ValueError("failed_stage must belong to plane")
        if stage_plane(self.repair_stage) != self.plane:
            raise ValueError("repair_stage must belong to plane")
        return self


__all__ = ["RuntimeErrorContext", "RuntimeFailureOrigin"]
