"""Canonical typed contracts for the Millrace runtime."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from pathlib import PurePath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class Plane(str, Enum):
    EXECUTION = "execution"
    PLANNING = "planning"


class ExecutionStageName(str, Enum):
    BUILDER = "builder"
    CHECKER = "checker"
    FIXER = "fixer"
    DOUBLECHECKER = "doublechecker"
    UPDATER = "updater"
    TROUBLESHOOTER = "troubleshooter"
    CONSULTANT = "consultant"


class PlanningStageName(str, Enum):
    PLANNER = "planner"
    MANAGER = "manager"
    MECHANIC = "mechanic"
    AUDITOR = "auditor"


StageName = ExecutionStageName | PlanningStageName


class ExecutionTerminalResult(str, Enum):
    BUILDER_COMPLETE = "BUILDER_COMPLETE"
    CHECKER_PASS = "CHECKER_PASS"
    FIX_NEEDED = "FIX_NEEDED"
    FIXER_COMPLETE = "FIXER_COMPLETE"
    DOUBLECHECK_PASS = "DOUBLECHECK_PASS"
    UPDATE_COMPLETE = "UPDATE_COMPLETE"
    TROUBLESHOOT_COMPLETE = "TROUBLESHOOT_COMPLETE"
    CONSULT_COMPLETE = "CONSULT_COMPLETE"
    NEEDS_PLANNING = "NEEDS_PLANNING"
    BLOCKED = "BLOCKED"


class PlanningTerminalResult(str, Enum):
    PLANNER_COMPLETE = "PLANNER_COMPLETE"
    MANAGER_COMPLETE = "MANAGER_COMPLETE"
    MECHANIC_COMPLETE = "MECHANIC_COMPLETE"
    AUDITOR_COMPLETE = "AUDITOR_COMPLETE"
    BLOCKED = "BLOCKED"


TerminalResult = ExecutionTerminalResult | PlanningTerminalResult


class ResultClass(str, Enum):
    SUCCESS = "success"
    FOLLOWUP_NEEDED = "followup_needed"
    RECOVERABLE_FAILURE = "recoverable_failure"
    ESCALATE_PLANNING = "escalate_planning"
    BLOCKED = "blocked"


class WorkItemKind(str, Enum):
    TASK = "task"
    SPEC = "spec"
    INCIDENT = "incident"


class TaskStatusHint(str, Enum):
    QUEUED = "queued"
    ACTIVE = "active"
    BLOCKED = "blocked"
    DONE = "done"


class IncidentSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentDecision(str, Enum):
    NEEDS_PLANNING = "needs_planning"
    BLOCKED = "blocked"


class RuntimeMode(str, Enum):
    ONCE = "once"
    DAEMON = "daemon"


class WatcherMode(str, Enum):
    WATCH = "watch"
    POLL = "poll"
    OFF = "off"


class ReloadOutcome(str, Enum):
    APPLIED = "applied"
    FAILED_RETAINED_PREVIOUS_PLAN = "failed_retained_previous_plan"


class RuntimeErrorCode(str, Enum):
    PLANNING_WORK_ITEM_COMPLETION_CONFLICT = "planning_work_item_completion_conflict"
    EXECUTION_WORK_ITEM_COMPLETION_CONFLICT = "execution_work_item_completion_conflict"
    PLANNING_POST_STAGE_APPLY_FAILED = "planning_post_stage_apply_failed"
    EXECUTION_POST_STAGE_APPLY_FAILED = "execution_post_stage_apply_failed"


class MailboxCommand(str, Enum):
    STOP = "stop"
    PAUSE = "pause"
    RESUME = "resume"
    RELOAD_CONFIG = "reload_config"
    ADD_TASK = "add_task"
    ADD_SPEC = "add_spec"
    ADD_IDEA = "add_idea"
    RETRY_ACTIVE = "retry_active"
    CLEAR_STALE_STATE = "clear_stale_state"


class LoopEdgeKind(str, Enum):
    NORMAL = "normal"
    RETRY = "retry"
    ESCALATION = "escalation"
    HANDOFF = "handoff"
    TERMINAL = "terminal"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TokenUsage(ContractModel):
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    total_tokens: int = 0

    @model_validator(mode="after")
    def validate_non_negative_values(self) -> "TokenUsage":
        for field_name in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "thinking_tokens",
            "total_tokens",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be >= 0")
        return self


_STAGE_TO_PLANE: dict[str, Plane] = {
    ExecutionStageName.BUILDER.value: Plane.EXECUTION,
    ExecutionStageName.CHECKER.value: Plane.EXECUTION,
    ExecutionStageName.FIXER.value: Plane.EXECUTION,
    ExecutionStageName.DOUBLECHECKER.value: Plane.EXECUTION,
    ExecutionStageName.UPDATER.value: Plane.EXECUTION,
    ExecutionStageName.TROUBLESHOOTER.value: Plane.EXECUTION,
    ExecutionStageName.CONSULTANT.value: Plane.EXECUTION,
    PlanningStageName.PLANNER.value: Plane.PLANNING,
    PlanningStageName.MANAGER.value: Plane.PLANNING,
    PlanningStageName.MECHANIC.value: Plane.PLANNING,
    PlanningStageName.AUDITOR.value: Plane.PLANNING,
}


_STAGE_LEGAL_TERMINAL_RESULTS: dict[str, set[str]] = {
    ExecutionStageName.BUILDER.value: {
        ExecutionTerminalResult.BUILDER_COMPLETE.value,
        ExecutionTerminalResult.BLOCKED.value,
    },
    ExecutionStageName.CHECKER.value: {
        ExecutionTerminalResult.CHECKER_PASS.value,
        ExecutionTerminalResult.FIX_NEEDED.value,
        ExecutionTerminalResult.BLOCKED.value,
    },
    ExecutionStageName.FIXER.value: {
        ExecutionTerminalResult.FIXER_COMPLETE.value,
        ExecutionTerminalResult.BLOCKED.value,
    },
    ExecutionStageName.DOUBLECHECKER.value: {
        ExecutionTerminalResult.DOUBLECHECK_PASS.value,
        ExecutionTerminalResult.FIX_NEEDED.value,
        ExecutionTerminalResult.BLOCKED.value,
    },
    ExecutionStageName.UPDATER.value: {
        ExecutionTerminalResult.UPDATE_COMPLETE.value,
        ExecutionTerminalResult.BLOCKED.value,
    },
    ExecutionStageName.TROUBLESHOOTER.value: {
        ExecutionTerminalResult.TROUBLESHOOT_COMPLETE.value,
        ExecutionTerminalResult.BLOCKED.value,
    },
    ExecutionStageName.CONSULTANT.value: {
        ExecutionTerminalResult.CONSULT_COMPLETE.value,
        ExecutionTerminalResult.NEEDS_PLANNING.value,
        ExecutionTerminalResult.BLOCKED.value,
    },
    PlanningStageName.PLANNER.value: {
        PlanningTerminalResult.PLANNER_COMPLETE.value,
        PlanningTerminalResult.BLOCKED.value,
    },
    PlanningStageName.MANAGER.value: {
        PlanningTerminalResult.MANAGER_COMPLETE.value,
        PlanningTerminalResult.BLOCKED.value,
    },
    PlanningStageName.MECHANIC.value: {
        PlanningTerminalResult.MECHANIC_COMPLETE.value,
        PlanningTerminalResult.BLOCKED.value,
    },
    PlanningStageName.AUDITOR.value: {
        PlanningTerminalResult.AUDITOR_COMPLETE.value,
        PlanningTerminalResult.BLOCKED.value,
    },
}

_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _validate_safe_identifier(value: str, *, field_name: str) -> str:
    cleaned = value.strip()
    if cleaned != value:
        raise ValueError(f"{field_name} must not include surrounding whitespace")
    if not cleaned:
        raise ValueError(f"{field_name} is required")
    if not _SAFE_ID_PATTERN.fullmatch(cleaned):
        raise ValueError(f"{field_name} must match {_SAFE_ID_PATTERN.pattern}")
    return cleaned


class TaskDocument(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["task"] = "task"

    task_id: str
    title: str
    summary: str = ""

    spec_id: str | None = None
    parent_task_id: str | None = None
    incident_id: str | None = None

    target_paths: tuple[str, ...] = Field(min_length=1)
    acceptance: tuple[str, ...] = Field(min_length=1)
    required_checks: tuple[str, ...] = Field(min_length=1)
    references: tuple[str, ...] = Field(min_length=1)
    risk: tuple[str, ...] = Field(min_length=1)

    depends_on: tuple[str, ...] = ()
    blocks: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    status_hint: TaskStatusHint | None = None
    created_at: datetime
    created_by: str
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def validate_identifier_shape(self) -> "TaskDocument":
        _validate_safe_identifier(self.task_id, field_name="task_id")
        if self.spec_id is not None:
            _validate_safe_identifier(self.spec_id, field_name="spec_id")
        if self.parent_task_id is not None:
            _validate_safe_identifier(self.parent_task_id, field_name="parent_task_id")
        if self.incident_id is not None:
            _validate_safe_identifier(self.incident_id, field_name="incident_id")
        return self


class SpecDocument(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["spec"] = "spec"

    spec_id: str
    title: str
    summary: str

    source_type: Literal["idea", "incident", "manual", "derived_spec"]
    source_id: str | None = None
    parent_spec_id: str | None = None

    goals: tuple[str, ...] = Field(min_length=1)
    non_goals: tuple[str, ...] = ()
    scope: tuple[str, ...] = ()
    constraints: tuple[str, ...] = Field(min_length=1)
    assumptions: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()

    target_paths: tuple[str, ...] = ()
    entrypoints: tuple[str, ...] = ()
    required_skills: tuple[str, ...] = ()

    decomposition_hints: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = Field(min_length=1)
    references: tuple[str, ...] = Field(min_length=1)

    created_at: datetime
    created_by: str
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def validate_identifier_shape(self) -> "SpecDocument":
        _validate_safe_identifier(self.spec_id, field_name="spec_id")
        if self.source_id is not None:
            _validate_safe_identifier(self.source_id, field_name="source_id")
        if self.parent_spec_id is not None:
            _validate_safe_identifier(self.parent_spec_id, field_name="parent_spec_id")
        return self


class IncidentDocument(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["incident"] = "incident"

    incident_id: str
    title: str
    summary: str

    source_task_id: str | None = None
    source_spec_id: str | None = None
    source_stage: StageName
    source_plane: Plane

    failure_class: str
    severity: IncidentSeverity = IncidentSeverity.MEDIUM
    needs_planning: bool = True

    trigger_reason: str
    observed_symptoms: tuple[str, ...] = ()
    failed_attempts: tuple[str, ...] = ()
    consultant_decision: IncidentDecision

    evidence_paths: tuple[str, ...] = ()
    related_run_ids: tuple[str, ...] = ()
    related_stage_results: tuple[str, ...] = ()
    references: tuple[str, ...] = ()

    opened_at: datetime
    opened_by: str
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def validate_stage_plane_alignment(self) -> "IncidentDocument":
        _validate_safe_identifier(self.incident_id, field_name="incident_id")
        if self.source_task_id is not None:
            _validate_safe_identifier(self.source_task_id, field_name="source_task_id")
        if self.source_spec_id is not None:
            _validate_safe_identifier(self.source_spec_id, field_name="source_spec_id")
        if _STAGE_TO_PLANE[self.source_stage.value] != self.source_plane:
            raise ValueError("source_stage must belong to source_plane")
        return self


class StageResultEnvelope(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["stage_result"] = "stage_result"

    run_id: str
    plane: Plane
    stage: StageName
    work_item_kind: WorkItemKind
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
    token_usage: TokenUsage | None = None

    notes: tuple[str, ...] = ()
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    started_at: datetime
    completed_at: datetime

    @model_validator(mode="after")
    def validate_contract(self) -> "StageResultEnvelope":
        if _STAGE_TO_PLANE[self.stage.value] != self.plane:
            raise ValueError("stage must belong to plane")

        if self.terminal_result.value not in _STAGE_LEGAL_TERMINAL_RESULTS[self.stage.value]:
            raise ValueError("terminal_result is not legal for stage")

        marker = f"### {self.terminal_result.value}"
        if self.summary_status_marker != marker:
            raise ValueError("summary_status_marker must match terminal_result")

        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")

        if self.duration_seconds < 0:
            raise ValueError("duration_seconds must be >= 0")

        if self.terminal_result.value == "BLOCKED":
            if self.result_class not in {
                ResultClass.BLOCKED,
                ResultClass.RECOVERABLE_FAILURE,
            }:
                raise ValueError("BLOCKED requires blocked or recoverable_failure result_class")
            if self.success:
                raise ValueError("BLOCKED terminal_result cannot be success=true")
            return self

        expected_result_class: dict[TerminalResult, ResultClass] = {
            ExecutionTerminalResult.BUILDER_COMPLETE: ResultClass.SUCCESS,
            ExecutionTerminalResult.CHECKER_PASS: ResultClass.SUCCESS,
            ExecutionTerminalResult.FIX_NEEDED: ResultClass.FOLLOWUP_NEEDED,
            ExecutionTerminalResult.FIXER_COMPLETE: ResultClass.SUCCESS,
            ExecutionTerminalResult.DOUBLECHECK_PASS: ResultClass.SUCCESS,
            ExecutionTerminalResult.UPDATE_COMPLETE: ResultClass.SUCCESS,
            ExecutionTerminalResult.TROUBLESHOOT_COMPLETE: ResultClass.SUCCESS,
            ExecutionTerminalResult.CONSULT_COMPLETE: ResultClass.SUCCESS,
            ExecutionTerminalResult.NEEDS_PLANNING: ResultClass.ESCALATE_PLANNING,
            PlanningTerminalResult.PLANNER_COMPLETE: ResultClass.SUCCESS,
            PlanningTerminalResult.MANAGER_COMPLETE: ResultClass.SUCCESS,
            PlanningTerminalResult.MECHANIC_COMPLETE: ResultClass.SUCCESS,
            PlanningTerminalResult.AUDITOR_COMPLETE: ResultClass.SUCCESS,
        }
        if self.result_class != expected_result_class[self.terminal_result]:
            raise ValueError("result_class does not match terminal_result semantics")

        if self.result_class == ResultClass.SUCCESS and not self.success:
            raise ValueError("success result_class requires success=true")
        if self.result_class != ResultClass.SUCCESS and self.success:
            raise ValueError("non-success result_class requires success=false")

        return self


class LoopEdgeDefinition(ContractModel):
    source_stage: StageName
    on_terminal_result: TerminalResult
    target_stage: StageName | None = None
    terminal_result: TerminalResult | None = None
    edge_kind: LoopEdgeKind = LoopEdgeKind.NORMAL
    max_attempts: int | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "LoopEdgeDefinition":
        if self.max_attempts is not None and self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")

        has_target = self.target_stage is not None
        has_terminal = self.terminal_result is not None

        if has_target == has_terminal:
            raise ValueError("exactly one of target_stage or terminal_result must be set")

        if self.edge_kind == LoopEdgeKind.TERMINAL and not has_terminal:
            raise ValueError("terminal edges require terminal_result")

        if self.edge_kind != LoopEdgeKind.TERMINAL and not has_target:
            raise ValueError("non-terminal edges require target_stage")

        return self


class LoopConfigDefinition(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["loop_config"] = "loop_config"

    loop_id: str
    plane: Plane
    stages: tuple[StageName, ...]
    entry_stage: StageName
    edges: tuple[LoopEdgeDefinition, ...]
    terminal_results: tuple[TerminalResult, ...]

    @model_validator(mode="after")
    def validate_loop_integrity(self) -> "LoopConfigDefinition":
        stage_values = [stage.value for stage in self.stages]
        stage_set = set(stage_values)

        if self.entry_stage.value not in stage_set:
            raise ValueError("entry_stage must be in stages")

        if len(stage_set) != len(self.stages):
            raise ValueError("stages must be unique")

        for stage in self.stages:
            if _STAGE_TO_PLANE[stage.value] != self.plane:
                raise ValueError("stages must belong to the loop plane")

        terminal_values = {result.value for result in self.terminal_results}

        has_terminal_path = False
        for edge in self.edges:
            if edge.source_stage.value not in stage_set:
                raise ValueError("edge source_stage must be in stages")

            legal_results = _STAGE_LEGAL_TERMINAL_RESULTS[edge.source_stage.value]
            if edge.on_terminal_result.value not in legal_results:
                raise ValueError("edge on_terminal_result is not legal for source_stage")

            if edge.target_stage is not None and edge.target_stage.value not in stage_set:
                raise ValueError("edge target_stage must be in stages")

            if edge.terminal_result is not None:
                if edge.terminal_result.value not in terminal_values:
                    raise ValueError("edge terminal_result must be in terminal_results")
                has_terminal_path = True

        if not has_terminal_path:
            raise ValueError("loop must include at least one terminal edge")

        return self


class ModeDefinition(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["mode"] = "mode"

    mode_id: str
    execution_loop_id: str
    planning_loop_id: str

    stage_entrypoint_overrides: dict[StageName, str] = Field(default_factory=dict)
    stage_skill_additions: dict[StageName, tuple[str, ...]] = Field(default_factory=dict)
    stage_model_bindings: dict[StageName, str] = Field(default_factory=dict)
    stage_runner_bindings: dict[StageName, str] = Field(default_factory=dict)


class FrozenStagePlan(ContractModel):
    stage: StageName
    plane: Plane
    entrypoint_path: str
    entrypoint_contract_id: str | None = None
    required_skills: tuple[str, ...] = ()
    attached_skill_additions: tuple[str, ...] = ()
    runner_name: str | None = None
    model_name: str | None = None
    timeout_seconds: int = 0

    @model_validator(mode="after")
    def validate_plane(self) -> "FrozenStagePlan":
        if _STAGE_TO_PLANE[self.stage.value] != self.plane:
            raise ValueError("stage must belong to plane")
        if self.timeout_seconds < 0:
            raise ValueError("timeout_seconds must be >= 0")
        return self


class FrozenRunPlan(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["frozen_run_plan"] = "frozen_run_plan"

    compiled_plan_id: str
    mode_id: str
    execution_loop_id: str
    planning_loop_id: str
    stage_plans: tuple[FrozenStagePlan, ...]
    compiled_at: datetime
    source_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_unique_stage_plans(self) -> "FrozenRunPlan":
        keys = [(plan.plane.value, plan.stage.value) for plan in self.stage_plans]
        if len(keys) != len(set(keys)):
            raise ValueError("stage_plans must be unique by plane/stage")
        return self


class CompileDiagnostics(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["compile_diagnostics"] = "compile_diagnostics"

    ok: bool
    mode_id: str
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    emitted_at: datetime

    @model_validator(mode="after")
    def validate_error_shape(self) -> "CompileDiagnostics":
        if not self.ok and not self.errors:
            raise ValueError("errors are required when ok is false")
        return self


class RuntimeSnapshot(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["runtime_snapshot"] = "runtime_snapshot"

    runtime_mode: RuntimeMode
    process_running: bool
    paused: bool
    stop_requested: bool = False
    active_mode_id: str
    execution_loop_id: str
    planning_loop_id: str
    compiled_plan_id: str
    compiled_plan_path: str

    active_plane: Plane | None = None
    active_stage: StageName | None = None
    active_run_id: str | None = None
    active_work_item_kind: WorkItemKind | None = None
    active_work_item_id: str | None = None

    execution_status_marker: str
    planning_status_marker: str

    queue_depth_execution: int = 0
    queue_depth_planning: int = 0

    last_terminal_result: TerminalResult | None = None
    last_stage_result_path: str | None = None

    current_failure_class: str | None = None
    troubleshoot_attempt_count: int = 0
    mechanic_attempt_count: int = 0
    fix_cycle_count: int = 0
    consultant_invocations: int = 0

    config_version: str
    watcher_mode: WatcherMode
    last_reload_outcome: ReloadOutcome | None = None
    last_reload_error: str | None = None

    started_at: datetime | None = None
    active_since: datetime | None = None
    updated_at: datetime

    @model_validator(mode="after")
    def validate_active_state(self) -> "RuntimeSnapshot":
        if self.active_stage is None and self.active_plane is not None:
            raise ValueError("active_plane cannot be set when active_stage is missing")

        if self.active_stage is not None:
            if self.active_plane is None:
                raise ValueError("active_plane is required when active_stage is set")
            if _STAGE_TO_PLANE[self.active_stage.value] != self.active_plane:
                raise ValueError("active_stage must belong to active_plane")

        has_kind = self.active_work_item_kind is not None
        has_id = self.active_work_item_id is not None
        if has_kind != has_id:
            raise ValueError(
                "active_work_item_kind and active_work_item_id must be set together"
            )
        if has_kind and self.active_stage is None:
            raise ValueError("active work item requires active_stage")
        if has_kind and self.active_plane is None:
            raise ValueError("active work item requires active_plane")
        if has_kind and self.active_run_id is None:
            raise ValueError("active work item requires active_run_id")

        if self.active_since is not None and self.active_stage is None:
            raise ValueError("active_since requires active_stage")

        if self.queue_depth_execution < 0 or self.queue_depth_planning < 0:
            raise ValueError("queue depth values must be >= 0")

        return self


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
        if _STAGE_TO_PLANE[self.failed_stage.value] != self.plane:
            raise ValueError("failed_stage must belong to plane")
        if _STAGE_TO_PLANE[self.repair_stage.value] != self.plane:
            raise ValueError("repair_stage must belong to plane")
        return self


class MailboxCommandEnvelope(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["mailbox_command"] = "mailbox_command"

    command_id: str
    command: MailboxCommand
    issued_at: datetime
    issuer: str
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class MailboxAddTaskPayload(ContractModel):
    document: TaskDocument


class MailboxAddSpecPayload(ContractModel):
    document: SpecDocument


class MailboxAddIdeaPayload(ContractModel):
    source_name: str
    markdown: str

    @model_validator(mode="after")
    def validate_shape(self) -> "MailboxAddIdeaPayload":
        source_name = self.source_name.strip()
        if source_name != self.source_name:
            raise ValueError("source_name must not include surrounding whitespace")
        if not source_name:
            raise ValueError("source_name is required")
        if not source_name.endswith(".md"):
            raise ValueError("source_name must end with .md")
        path = PurePath(source_name)
        if path.is_absolute() or len(path.parts) != 1:
            raise ValueError("source_name must be a single relative filename")
        stem = source_name[:-3]
        _validate_safe_identifier(stem, field_name="source_name")
        if not self.markdown.strip():
            raise ValueError("markdown is required")
        return self


class RecoveryCounterEntry(ContractModel):
    failure_class: str
    work_item_id: str
    work_item_kind: WorkItemKind
    troubleshoot_attempt_count: int = 0
    mechanic_attempt_count: int = 0
    fix_cycle_count: int = 0
    consultant_invocations: int = 0
    last_updated_at: datetime

    @model_validator(mode="after")
    def validate_non_negative_counts(self) -> "RecoveryCounterEntry":
        if self.troubleshoot_attempt_count < 0:
            raise ValueError("troubleshoot_attempt_count must be >= 0")
        if self.mechanic_attempt_count < 0:
            raise ValueError("mechanic_attempt_count must be >= 0")
        if self.fix_cycle_count < 0:
            raise ValueError("fix_cycle_count must be >= 0")
        if self.consultant_invocations < 0:
            raise ValueError("consultant_invocations must be >= 0")
        return self


class RecoveryCounters(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["recovery_counters"] = "recovery_counters"
    entries: tuple[RecoveryCounterEntry, ...] = ()


__all__ = [
    "CompileDiagnostics",
    "ExecutionStageName",
    "ExecutionTerminalResult",
    "FrozenRunPlan",
    "FrozenStagePlan",
    "IncidentDocument",
    "IncidentDecision",
    "IncidentSeverity",
    "LoopConfigDefinition",
    "LoopEdgeDefinition",
    "LoopEdgeKind",
    "MailboxCommand",
    "MailboxAddIdeaPayload",
    "MailboxAddSpecPayload",
    "MailboxAddTaskPayload",
    "MailboxCommandEnvelope",
    "ModeDefinition",
    "Plane",
    "PlanningStageName",
    "PlanningTerminalResult",
    "RecoveryCounterEntry",
    "RecoveryCounters",
    "ResultClass",
    "ReloadOutcome",
    "RuntimeMode",
    "RuntimeErrorCode",
    "RuntimeErrorContext",
    "RuntimeSnapshot",
    "SpecDocument",
    "StageName",
    "StageResultEnvelope",
    "TaskDocument",
    "TaskStatusHint",
    "TerminalResult",
    "TokenUsage",
    "WatcherMode",
    "WorkItemKind",
]
