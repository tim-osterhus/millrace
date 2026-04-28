"""Canonical typed contracts for the Millrace runtime."""

from __future__ import annotations

from .base import ContractModel as ContractModel
from .compile_diagnostics import CompileDiagnostics
from .enums import (  # noqa: F401
    ExecutionStageName,
    ExecutionTerminalResult,
    IncidentDecision,
    IncidentSeverity,
    IncidentStatusHint,
    LearningRequestAction,
    LearningStageName,
    LearningTerminalResult,
    LoopEdgeKind,
    MailboxCommand,
    Plane,
    PlanningStageName,
    PlanningTerminalResult,
    ReloadOutcome,
    ResultClass,
    RuntimeErrorCode,
    RuntimeMode,
    StageName,
    TaskStatusHint,
    TerminalResult,
    WatcherMode,
    WorkItemKind,
)
from .loop_config import CompletionBehaviorDefinition, LoopConfigDefinition, LoopEdgeDefinition
from .mailbox import (
    MailboxAddIdeaPayload,
    MailboxAddSpecPayload,
    MailboxAddTaskPayload,
    MailboxCommandEnvelope,
)
from .modes import (  # noqa: F401
    LearningTriggerRuleDefinition,
    ModeDefinition,
    PlaneConcurrencyPolicyDefinition,
)
from .recovery import RecoveryCounterEntry, RecoveryCounters
from .runtime_errors import RuntimeErrorContext
from .runtime_snapshot import ActiveRunRequestKind, ActiveRunState, RuntimeSnapshot
from .stage_results import StageResultEnvelope
from .token_usage import TokenUsage
from .work_documents import (  # noqa: F401
    ClosureTargetState,
    IncidentDocument,
    LearningRequestDocument,
    SpecDocument,
    TaskDocument,
)

__all__ = [
    "ClosureTargetState",
    "CompileDiagnostics",
    "CompletionBehaviorDefinition",
    "ContractModel",
    "ActiveRunRequestKind",
    "ActiveRunState",
    "ExecutionStageName",
    "ExecutionTerminalResult",
    "IncidentDocument",
    "IncidentDecision",
    "IncidentSeverity",
    "IncidentStatusHint",
    "LearningRequestAction",
    "LearningRequestDocument",
    "LearningStageName",
    "LearningTerminalResult",
    "LearningTriggerRuleDefinition",
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
    "PlaneConcurrencyPolicyDefinition",
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
