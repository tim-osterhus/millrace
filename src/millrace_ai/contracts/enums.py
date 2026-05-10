"""Enum contracts shared across runtime artifacts."""

from __future__ import annotations

from enum import Enum


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
    RECON = "recon"
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
    RECON_TO_EXECUTION = "RECON_TO_EXECUTION"
    RECON_TO_PLANNING = "RECON_TO_PLANNING"
    RECON_BLOCKED = "RECON_BLOCKED"
    RECON_NOOP = "RECON_NOOP"
    PLANNER_COMPLETE = "PLANNER_COMPLETE"
    MANAGER_COMPLETE = "MANAGER_COMPLETE"
    MECHANIC_COMPLETE = "MECHANIC_COMPLETE"
    AUDITOR_COMPLETE = "AUDITOR_COMPLETE"
    ARBITER_COMPLETE = "ARBITER_COMPLETE"
    REMEDIATION_NEEDED = "REMEDIATION_NEEDED"
    BLOCKED = "BLOCKED"


class LearningTerminalResult(str, Enum):
    ANALYST_COMPLETE = "ANALYST_COMPLETE"
    ANALYST_NOOP = "ANALYST_NOOP"
    PROFESSOR_COMPLETE = "PROFESSOR_COMPLETE"
    PROFESSOR_NOOP = "PROFESSOR_NOOP"
    CURATOR_COMPLETE = "CURATOR_COMPLETE"
    CURATOR_NOOP = "CURATOR_NOOP"
    BLOCKED = "BLOCKED"


TerminalResult = ExecutionTerminalResult | PlanningTerminalResult | LearningTerminalResult


class ResultClass(str, Enum):
    SUCCESS = "success"
    NO_OP = "no_op"
    FOLLOWUP_NEEDED = "followup_needed"
    RECOVERABLE_FAILURE = "recoverable_failure"
    ESCALATE_PLANNING = "escalate_planning"
    BLOCKED = "blocked"


class WorkItemKind(str, Enum):
    TASK = "task"
    PROBE = "probe"
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


class ProbeStatusHint(str, Enum):
    QUEUED = "queued"
    ACTIVE = "active"
    BLOCKED = "blocked"
    DONE = "done"


class RootIntakeKind(str, Enum):
    IDEA = "idea"
    PROBE = "probe"
    MANUAL = "manual"
    INCIDENT = "incident"
    DERIVED_SPEC = "derived_spec"


class IncidentStatusHint(str, Enum):
    INCOMING = "incoming"
    ACTIVE = "active"
    BLOCKED = "blocked"
    RESOLVED = "resolved"


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
    RECON_HANDOFF_INVALID = "recon_handoff_invalid"


class MailboxCommand(str, Enum):
    STOP = "stop"
    PAUSE = "pause"
    RESUME = "resume"
    RELOAD_CONFIG = "reload_config"
    ADD_TASK = "add_task"
    ADD_PROBE = "add_probe"
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


__all__ = [
    "ExecutionStageName",
    "ExecutionTerminalResult",
    "IncidentDecision",
    "IncidentSeverity",
    "IncidentStatusHint",
    "LearningRequestAction",
    "LearningStageName",
    "LearningTerminalResult",
    "LoopEdgeKind",
    "MailboxCommand",
    "Plane",
    "PlanningStageName",
    "PlanningTerminalResult",
    "ProbeStatusHint",
    "ReloadOutcome",
    "ResultClass",
    "RootIntakeKind",
    "RuntimeErrorCode",
    "RuntimeMode",
    "StageName",
    "TaskStatusHint",
    "TerminalResult",
    "WatcherMode",
    "WorkItemKind",
]
