"""Canonical typed contracts for the Millrace runtime."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from pathlib import PurePath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


class Plane(str, Enum):
    EXECUTION = "execution"
    PLANNING = "planning"
    LEARNING = "learning"


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
    ARBITER = "arbiter"


class LearningStageName(str, Enum):
    ANALYST = "analyst"
    PROFESSOR = "professor"
    CURATOR = "curator"


StageName = ExecutionStageName | PlanningStageName | LearningStageName


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
    ARBITER_COMPLETE = "ARBITER_COMPLETE"
    REMEDIATION_NEEDED = "REMEDIATION_NEEDED"
    BLOCKED = "BLOCKED"


class LearningTerminalResult(str, Enum):
    ANALYST_COMPLETE = "ANALYST_COMPLETE"
    PROFESSOR_COMPLETE = "PROFESSOR_COMPLETE"
    CURATOR_COMPLETE = "CURATOR_COMPLETE"
    BLOCKED = "BLOCKED"


TerminalResult = ExecutionTerminalResult | PlanningTerminalResult | LearningTerminalResult


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
    LEARNING_REQUEST = "learning_request"


class LearningRequestAction(str, Enum):
    CREATE = "create"
    IMPROVE = "improve"
    PROMOTE = "promote"
    EXPORT = "export"
    INSTALL = "install"


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
    PlanningStageName.ARBITER.value: Plane.PLANNING,
    LearningStageName.ANALYST.value: Plane.LEARNING,
    LearningStageName.PROFESSOR.value: Plane.LEARNING,
    LearningStageName.CURATOR.value: Plane.LEARNING,
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
    PlanningStageName.ARBITER.value: {
        PlanningTerminalResult.ARBITER_COMPLETE.value,
        PlanningTerminalResult.REMEDIATION_NEEDED.value,
        PlanningTerminalResult.BLOCKED.value,
    },
    LearningStageName.ANALYST.value: {
        LearningTerminalResult.ANALYST_COMPLETE.value,
        LearningTerminalResult.BLOCKED.value,
    },
    LearningStageName.PROFESSOR.value: {
        LearningTerminalResult.PROFESSOR_COMPLETE.value,
        LearningTerminalResult.BLOCKED.value,
    },
    LearningStageName.CURATOR.value: {
        LearningTerminalResult.CURATOR_COMPLETE.value,
        LearningTerminalResult.BLOCKED.value,
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

    root_idea_id: str | None = None
    root_spec_id: str | None = None
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
        if self.root_idea_id is not None:
            _validate_safe_identifier(self.root_idea_id, field_name="root_idea_id")
        if self.root_spec_id is not None:
            _validate_safe_identifier(self.root_spec_id, field_name="root_spec_id")
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
    root_idea_id: str | None = None
    root_spec_id: str | None = None

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
        if self.root_idea_id is not None:
            _validate_safe_identifier(self.root_idea_id, field_name="root_idea_id")
        if self.root_spec_id is not None:
            _validate_safe_identifier(self.root_spec_id, field_name="root_spec_id")
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

    root_idea_id: str | None = None
    root_spec_id: str | None = None
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
        if self.root_idea_id is not None:
            _validate_safe_identifier(self.root_idea_id, field_name="root_idea_id")
        if self.root_spec_id is not None:
            _validate_safe_identifier(self.root_spec_id, field_name="root_spec_id")
        if self.source_task_id is not None:
            _validate_safe_identifier(self.source_task_id, field_name="source_task_id")
        if self.source_spec_id is not None:
            _validate_safe_identifier(self.source_spec_id, field_name="source_spec_id")
        if _STAGE_TO_PLANE[self.source_stage.value] != self.source_plane:
            raise ValueError("source_stage must belong to source_plane")
        return self


class LearningRequestDocument(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["learning_request"] = "learning_request"

    learning_request_id: str
    title: str
    summary: str = ""

    requested_action: LearningRequestAction
    target_skill_id: str | None = None
    target_stage: LearningStageName | None = None
    source_refs: tuple[str, ...] = ()
    preferred_output_paths: tuple[str, ...] = ()
    trigger_metadata: dict[str, JsonValue] = Field(default_factory=dict)
    originating_run_ids: tuple[str, ...] = ()
    artifact_paths: tuple[str, ...] = ()
    references: tuple[str, ...] = ()

    created_at: datetime
    created_by: str
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def validate_identifier_shape(self) -> "LearningRequestDocument":
        _validate_safe_identifier(self.learning_request_id, field_name="learning_request_id")
        if self.target_skill_id is not None:
            _validate_safe_identifier(self.target_skill_id, field_name="target_skill_id")
        for run_id in self.originating_run_ids:
            _validate_safe_identifier(run_id, field_name="originating_run_ids")
        return self


class CompletionBehaviorDefinition(ContractModel):
    trigger: Literal["backlog_drained"]
    readiness_rule: Literal["no_open_lineage_work"]
    stage: StageName
    request_kind: Literal["closure_target"]
    target_selector: Literal["active_closure_target"]
    rubric_policy: Literal["reuse_or_create"]
    blocked_work_policy: Literal["suppress"]
    skip_if_already_closed: bool = True
    on_pass_terminal_result: TerminalResult
    on_gap_terminal_result: TerminalResult
    create_incident_on_gap: bool = False

    @model_validator(mode="after")
    def validate_completion_behavior(self) -> "CompletionBehaviorDefinition":
        if self.on_pass_terminal_result == self.on_gap_terminal_result:
            raise ValueError("completion behavior pass/gap results must differ")
        legal_results = _STAGE_LEGAL_TERMINAL_RESULTS[self.stage.value]
        if self.on_pass_terminal_result.value not in legal_results:
            raise ValueError("on_pass_terminal_result is not legal for completion stage")
        if self.on_gap_terminal_result.value not in legal_results:
            raise ValueError("on_gap_terminal_result is not legal for completion stage")
        return self


class ClosureTargetState(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["closure_target_state"] = "closure_target_state"

    root_spec_id: str
    root_idea_id: str
    root_spec_path: str
    root_idea_path: str
    rubric_path: str
    latest_verdict_path: str | None = None
    latest_report_path: str | None = None
    closure_open: bool = True
    closure_blocked_by_lineage_work: bool = False
    blocking_work_ids: tuple[str, ...] = ()
    opened_at: datetime
    closed_at: datetime | None = None
    last_arbiter_run_id: str | None = None

    @model_validator(mode="after")
    def validate_target_state(self) -> "ClosureTargetState":
        _validate_safe_identifier(self.root_spec_id, field_name="root_spec_id")
        _validate_safe_identifier(self.root_idea_id, field_name="root_idea_id")
        if self.last_arbiter_run_id is not None:
            _validate_safe_identifier(self.last_arbiter_run_id, field_name="last_arbiter_run_id")
        for work_item_id in self.blocking_work_ids:
            _validate_safe_identifier(work_item_id, field_name="blocking_work_ids")
        if self.closed_at is not None and self.closed_at < self.opened_at:
            raise ValueError("closed_at cannot precede opened_at")
        if self.closed_at is not None and self.closure_open:
            raise ValueError("closed closure target cannot remain open")
        if self.blocking_work_ids and not self.closure_blocked_by_lineage_work:
            raise ValueError("blocking_work_ids require closure_blocked_by_lineage_work=true")
        return self


class StageResultEnvelope(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["stage_result"] = "stage_result"

    run_id: str
    plane: Plane
    stage: StageName
    node_id: str = ""
    stage_kind_id: str = ""
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
    completion_behavior: CompletionBehaviorDefinition | None = None

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

        if self.completion_behavior is not None:
            if self.completion_behavior.stage.value not in stage_set:
                raise ValueError("completion_behavior stage must be in stages")
            if _STAGE_TO_PLANE[self.completion_behavior.stage.value] != self.plane:
                raise ValueError("completion_behavior stage must belong to loop plane")
            if self.completion_behavior.on_pass_terminal_result.value not in terminal_values:
                raise ValueError("completion_behavior on_pass_terminal_result must be in terminal_results")
            if self.completion_behavior.on_gap_terminal_result.value not in terminal_values:
                raise ValueError("completion_behavior on_gap_terminal_result must be in terminal_results")

        if not has_terminal_path:
            raise ValueError("loop must include at least one terminal edge")

        return self


class LearningTriggerRuleDefinition(ContractModel):
    rule_id: str
    source_plane: Plane
    source_stage: StageName
    on_terminal_results: tuple[str, ...] = Field(min_length=1)
    target_stage: LearningStageName
    requested_action: LearningRequestAction = LearningRequestAction.IMPROVE

    @field_validator("on_terminal_results", mode="before")
    @classmethod
    def normalize_terminal_results(cls, value: tuple[str, ...] | list[str] | str) -> tuple[str, ...]:
        if isinstance(value, str):
            raw_values = [value]
        else:
            raw_values = list(value)
        normalized = tuple(str(item).strip() for item in raw_values if str(item).strip())
        if not normalized:
            raise ValueError("on_terminal_results must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_rule_shape(self) -> "LearningTriggerRuleDefinition":
        _validate_safe_identifier(self.rule_id, field_name="rule_id")
        if _STAGE_TO_PLANE[self.source_stage.value] is not self.source_plane:
            raise ValueError("source_stage must belong to source_plane")
        if self.source_plane is Plane.LEARNING:
            raise ValueError("learning triggers must originate outside the learning plane")
        legal = _STAGE_LEGAL_TERMINAL_RESULTS[self.source_stage.value]
        unknown = tuple(result for result in self.on_terminal_results if result not in legal)
        if unknown:
            raise ValueError(
                "on_terminal_results contains values illegal for source_stage: "
                + ", ".join(unknown)
            )
        return self


class PlaneConcurrencyPolicyDefinition(ContractModel):
    mutually_exclusive_planes: tuple[tuple[Plane, ...], ...] = ()
    may_run_concurrently: tuple[tuple[Plane, ...], ...] = ()


class ModeDefinition(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["mode"] = "mode"

    mode_id: str
    loop_ids_by_plane: dict[Plane, str]

    stage_entrypoint_overrides: dict[StageName, str] = Field(default_factory=dict)
    stage_skill_additions: dict[StageName, tuple[str, ...]] = Field(default_factory=dict)
    stage_model_bindings: dict[StageName, str] = Field(default_factory=dict)
    stage_runner_bindings: dict[StageName, str] = Field(default_factory=dict)
    concurrency_policy: PlaneConcurrencyPolicyDefinition | None = None
    learning_trigger_rules: tuple[LearningTriggerRuleDefinition, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_loop_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value

        payload = dict(value)
        loop_ids = dict(payload.get("loop_ids_by_plane") or {})
        legacy_execution = payload.pop("execution_loop_id", None)
        legacy_planning = payload.pop("planning_loop_id", None)

        if legacy_execution is not None:
            loop_ids[Plane.EXECUTION.value] = legacy_execution
        if legacy_planning is not None:
            loop_ids[Plane.PLANNING.value] = legacy_planning
        if loop_ids:
            payload["loop_ids_by_plane"] = loop_ids
        return payload

    @model_validator(mode="after")
    def validate_loop_bindings(self) -> "ModeDefinition":
        if Plane.EXECUTION not in self.loop_ids_by_plane:
            raise ValueError("loop_ids_by_plane must include execution")
        if Plane.PLANNING not in self.loop_ids_by_plane:
            raise ValueError("loop_ids_by_plane must include planning")
        for plane, loop_id in self.loop_ids_by_plane.items():
            expected_prefix = f"{plane.value}."
            if not loop_id.startswith(expected_prefix):
                raise ValueError(
                    f"loop id for plane {plane.value} must start with {expected_prefix!r}"
                )
        if self.learning_trigger_rules and not self.learning_enabled:
            raise ValueError("learning_trigger_rules require a learning loop binding")
        return self

    @property
    def execution_loop_id(self) -> str:
        return self.loop_ids_by_plane[Plane.EXECUTION]

    @property
    def planning_loop_id(self) -> str:
        return self.loop_ids_by_plane[Plane.PLANNING]

    @property
    def learning_loop_id(self) -> str | None:
        return self.loop_ids_by_plane.get(Plane.LEARNING)

    @property
    def learning_enabled(self) -> bool:
        return Plane.LEARNING in self.loop_ids_by_plane


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
    learning_loop_id: str | None = None
    loop_ids_by_plane: dict[Plane, str] = Field(default_factory=dict)
    compiled_plan_id: str
    compiled_plan_path: str

    active_plane: Plane | None = None
    active_stage: StageName | None = None
    active_run_id: str | None = None
    active_work_item_kind: WorkItemKind | None = None
    active_work_item_id: str | None = None

    execution_status_marker: str
    planning_status_marker: str
    learning_status_marker: str = "### IDLE"
    status_markers_by_plane: dict[Plane, str] = Field(default_factory=dict)

    queue_depth_execution: int = 0
    queue_depth_planning: int = 0
    queue_depth_learning: int = 0
    queue_depths_by_plane: dict[Plane, int] = Field(default_factory=dict)

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

    @model_validator(mode="before")
    @classmethod
    def normalize_plane_indexed_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value

        payload = dict(value)
        loop_ids = dict(payload.get("loop_ids_by_plane") or {})
        if "execution_loop_id" in payload:
            loop_ids.setdefault(Plane.EXECUTION.value, payload["execution_loop_id"])
        if "planning_loop_id" in payload:
            loop_ids.setdefault(Plane.PLANNING.value, payload["planning_loop_id"])
        if payload.get("learning_loop_id") is not None:
            loop_ids.setdefault(Plane.LEARNING.value, payload["learning_loop_id"])
        if loop_ids:
            payload["loop_ids_by_plane"] = loop_ids

        status_markers = dict(payload.get("status_markers_by_plane") or {})
        if "execution_status_marker" in payload:
            status_markers.setdefault(Plane.EXECUTION.value, payload["execution_status_marker"])
        if "planning_status_marker" in payload:
            status_markers.setdefault(Plane.PLANNING.value, payload["planning_status_marker"])
        if "learning_status_marker" in payload:
            status_markers.setdefault(Plane.LEARNING.value, payload["learning_status_marker"])
        if status_markers:
            payload["status_markers_by_plane"] = status_markers

        queue_depths = dict(payload.get("queue_depths_by_plane") or {})
        if "queue_depth_execution" in payload:
            queue_depths.setdefault(Plane.EXECUTION.value, payload["queue_depth_execution"])
        if "queue_depth_planning" in payload:
            queue_depths.setdefault(Plane.PLANNING.value, payload["queue_depth_planning"])
        if "queue_depth_learning" in payload:
            queue_depths.setdefault(Plane.LEARNING.value, payload["queue_depth_learning"])
        if queue_depths:
            payload["queue_depths_by_plane"] = queue_depths
        return payload

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

        if self.queue_depth_execution < 0 or self.queue_depth_planning < 0 or self.queue_depth_learning < 0:
            raise ValueError("queue depth values must be >= 0")
        if any(depth < 0 for depth in self.queue_depths_by_plane.values()):
            raise ValueError("plane-indexed queue depth values must be >= 0")

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
    "ClosureTargetState",
    "CompileDiagnostics",
    "CompletionBehaviorDefinition",
    "ExecutionStageName",
    "ExecutionTerminalResult",
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
