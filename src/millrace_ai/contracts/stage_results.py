"""Stage-result artifact contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from .base import ContractModel
from .enums import Plane, ResultClass, StageName, TerminalResult, WorkItemKind
from .stage_metadata import stage_plane
from .token_usage import TokenUsage
from .work_refs import coerce_family_and_kind


class StageResultEnvelope(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["stage_result"] = "stage_result"

    run_id: str
    plane: Plane
    stage: StageName
    node_id: str = ""
    stage_kind_id: str = ""
    work_item_family_id: str | None = None
    work_item_kind: WorkItemKind | None = None
    work_item_id: str

    terminal_result: TerminalResult
    result_class: ResultClass
    summary_status_marker: str

    success: bool
    retryable: bool = False
    exit_code: int = 0
    duration_seconds: float = 0

    prompt_artifact: str | None = None
    report_artifact: str | None = None
    artifact_paths: tuple[str, ...] = ()

    detected_marker: str | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
    runner_name: str | None = None
    model_name: str | None = None
    thinking_level: str | None = None
    model_reasoning_effort: str | None = None
    model_assignment_alias_id: str | None = None
    model_assignment_source: str | None = None
    token_usage: TokenUsage | None = None

    notes: tuple[str, ...] = ()
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    started_at: datetime
    completed_at: datetime

    @model_validator(mode="after")
    def validate_contract(self) -> "StageResultEnvelope":
        family_id, work_item_kind = coerce_family_and_kind(
            family_id=self.work_item_family_id,
            work_item_kind=self.work_item_kind,
        )
        if family_id is None:
            raise ValueError("stage result requires work_item_family_id or work_item_kind")
        self.work_item_family_id = family_id
        self.work_item_kind = work_item_kind

        if stage_plane(self.stage) != self.plane:
            raise ValueError("stage must belong to plane")
        if not self.node_id:
            self.node_id = self.stage.value
        if not self.stage_kind_id:
            self.stage_kind_id = self.stage.value

        marker = f"### {self.terminal_result.value}"
        if self.summary_status_marker != marker:
            raise ValueError("summary_status_marker must match terminal_result")

        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")

        if self.duration_seconds < 0:
            raise ValueError("duration_seconds must be >= 0")
        if self.result_class == ResultClass.SUCCESS and not self.success:
            raise ValueError("success result_class requires success=true")
        if self.result_class != ResultClass.SUCCESS and self.success:
            raise ValueError("non-success result_class requires success=false")

        return self


__all__ = ["StageResultEnvelope"]
