"""Compatibility facade for the research audit surface."""

from __future__ import annotations

from ..contracts import AuditExecutionReport
from .audit_execution import execute_audit_gatekeeper, execute_audit_intake, execute_audit_validate
from .audit_models import (
    AuditExecutionError,
    AuditGoalGapReviewRecord,
    AuditGatekeeperExecutionResult,
    AuditGatekeeperRecord,
    AuditIntakeExecutionResult,
    AuditIntakeRecord,
    AuditLifecycleStatus,
    AuditQueueRecord,
    AuditRemediationRecord,
    AuditSummary,
    AuditSummaryLastOutcome,
    AuditTrigger,
    AuditValidateExecutionResult,
    AuditValidateRecord,
)
from .audit_parsing import _extract_section_lines
from .audit_queue_helpers import (
    ensure_backlog_empty_audit_ticket,
    load_audit_queue_record,
    parse_audit_queue_record,
)
from .audit_storage_helpers import load_audit_remediation_record, load_audit_summary

__all__ = [
    "AuditExecutionError",
    "AuditGoalGapReviewRecord",
    "AuditExecutionReport",
    "AuditGatekeeperExecutionResult",
    "AuditGatekeeperRecord",
    "AuditIntakeExecutionResult",
    "AuditIntakeRecord",
    "AuditLifecycleStatus",
    "AuditQueueRecord",
    "AuditRemediationRecord",
    "AuditSummary",
    "AuditSummaryLastOutcome",
    "AuditTrigger",
    "AuditValidateExecutionResult",
    "AuditValidateRecord",
    "ensure_backlog_empty_audit_ticket",
    "execute_audit_gatekeeper",
    "execute_audit_intake",
    "execute_audit_validate",
    "load_audit_remediation_record",
    "load_audit_summary",
    "load_audit_queue_record",
    "parse_audit_queue_record",
    "_extract_section_lines",
]
