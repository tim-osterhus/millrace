"""Remediation helpers for research audit execution."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ..contracts import AuditGateDecision, CompletionDecision, ResearchRecoveryDecision
from ..markdown import TaskStoreDocument, parse_task_store, render_task_store, write_text_atomic
from ..paths import RuntimePaths
from ..queue import load_research_recovery_latch, write_research_recovery_latch
from .audit_storage_helpers import (
    _audit_remediation_record_path,
    _relative_path,
    _write_json_model,
)

if TYPE_CHECKING:
    from ..contracts import TaskCard
    from .audit import AuditQueueRecord, AuditRemediationRecord
    from .state import ResearchCheckpoint


def _audit_remediation_spec_id(audit_id: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "-", audit_id.strip().upper()).strip("-")
    if not token:
        token = "AUDIT"
    return f"SPEC-{token}-REMEDIATION"


def _audit_remediation_title(record: "AuditQueueRecord") -> str:
    return f"Remediate failed audit {record.audit_id}"


def _render_audit_remediation_body(
    *,
    record: "AuditQueueRecord",
    gate_decision: AuditGateDecision,
    remediation_record_path: str,
    execution_report_path: str,
    validate_record_path: str,
) -> str:
    reasons = list(gate_decision.reasons[:3])
    if not reasons:
        reasons = ["Audit gate failed closed; inspect the gate and execution artifacts."]
    reason_text = " ".join(reasons)
    return "\n".join(
        [
            f"- **Goal:** Repair the work audited by `{record.audit_id}` and rerun the completion gate.",
            f"- **Why:** {reason_text}",
            "- **Acceptance:** The failed audit reasons are addressed, the audit can be rerun, and the completion gate can pass cleanly.",
            f"- **Audit-ID:** {record.audit_id}",
            f"- **Audit-Scope:** {record.scope}",
            f"- **Audit-Trigger:** {record.trigger.value}",
            f"- **Audit-Gate-Decision:** `{gate_decision.gate_decision_path}`",
            f"- **Audit-Validate-Record:** `{validate_record_path}`",
            f"- **Audit-Execution-Report:** `{execution_report_path}`",
            f"- **Audit-Remediation-Record:** `{remediation_record_path}`",
        ]
    )


def _existing_remediation_task(paths: RuntimePaths, *, remediation_spec_id: str) -> "TaskCard | None":
    for store_path in (paths.tasks_file, paths.backlog_file, paths.taskspending_file):
        if not store_path.exists():
            continue
        document = parse_task_store(store_path.read_text(encoding="utf-8"), source_file=store_path)
        for card in document.cards:
            if card.spec_id == remediation_spec_id:
                return card
    return None


def _append_backlog_task(
    paths: RuntimePaths,
    *,
    title: str,
    body: str,
    spec_id: str,
) -> "TaskCard":
    from ..contracts import TaskCard

    task_date = datetime.now(timezone.utc).date().isoformat()
    card = TaskCard.model_validate(
        {
            "heading": f"## {task_date} - {title}",
            "body": f"- **Spec-ID:** {spec_id}\n\n{body.strip()}",
        }
    )
    existing = parse_task_store(paths.backlog_file.read_text(encoding="utf-8"), source_file=paths.backlog_file)
    updated = TaskStoreDocument(preamble=existing.preamble, cards=[*existing.cards, card])
    write_text_atomic(paths.backlog_file, render_task_store(updated))
    return card


def _persist_audit_recovery_decision(
    paths: RuntimePaths,
    checkpoint: "ResearchCheckpoint",
    *,
    emitted_at: datetime,
    remediation_record: "AuditRemediationRecord",
) -> bool:
    handoff = checkpoint.parent_handoff or (
        None if checkpoint.active_request is None else checkpoint.active_request.handoff
    )
    if handoff is None or handoff.recovery_batch_id is None:
        return False

    latch = load_research_recovery_latch(paths.research_recovery_latch_file)
    if latch is None or latch.batch_id != handoff.recovery_batch_id:
        return False
    if latch.handoff is not None and latch.handoff.handoff_id != handoff.handoff_id:
        return False

    decision = ResearchRecoveryDecision(
        decision_type="durable_remediation_decision",
        decided_at=emitted_at,
        remediation_spec_id=remediation_record.remediation_spec_id,
        remediation_record_path=Path(
            _relative_path(
                _audit_remediation_record_path(paths, run_id=remediation_record.run_id),
                relative_to=paths.root,
            )
        ),
        taskaudit_record_path=None,
        task_provenance_path=None,
        lineage_path=None,
        pending_card_count=0,
        backlog_card_count=remediation_record.backlog_depth_after_enqueue,
    )
    write_research_recovery_latch(
        paths.research_recovery_latch_file,
        latch.model_copy(
            update={
                "handoff": latch.handoff or handoff,
                "remediation_decision": decision,
            }
        ),
    )
    return True


def _persist_audit_remediation(
    paths: RuntimePaths,
    checkpoint: "ResearchCheckpoint",
    *,
    run_id: str,
    emitted_at: datetime,
    audited_source_path: str,
    record: "AuditQueueRecord",
    terminal_record: "AuditQueueRecord",
    gate_decision: AuditGateDecision,
    completion_decision: CompletionDecision,
    validate_record_path: Path,
    execution_report_path: Path,
) -> "AuditRemediationRecord":
    from .audit import AuditRemediationRecord

    remediation_spec_id = _audit_remediation_spec_id(record.audit_id)
    remediation_path = _audit_remediation_record_path(paths, run_id=run_id)
    existing_task = _existing_remediation_task(paths, remediation_spec_id=remediation_spec_id)
    if existing_task is None:
        task_card = _append_backlog_task(
            paths,
            title=_audit_remediation_title(record),
            body=_render_audit_remediation_body(
                record=record,
                gate_decision=gate_decision,
                remediation_record_path=_relative_path(remediation_path, relative_to=paths.root),
                execution_report_path=_relative_path(execution_report_path, relative_to=paths.root),
                validate_record_path=_relative_path(validate_record_path, relative_to=paths.root),
            ),
            spec_id=remediation_spec_id,
        )
        selected_action: Literal["enqueue_backlog_task", "reuse_existing_task"] = "enqueue_backlog_task"
    else:
        task_card = existing_task
        selected_action = "reuse_existing_task"

    backlog_depth = len(parse_task_store(paths.backlog_file.read_text(encoding="utf-8")).cards)
    remediation_record = AuditRemediationRecord(
        run_id=run_id,
        emitted_at=emitted_at,
        audit_id=record.audit_id,
        title=record.title,
        scope=record.scope,
        trigger=record.trigger,
        source_path=audited_source_path,
        terminal_path=_relative_path(terminal_record.source_path, relative_to=paths.root),
        gate_decision_path=gate_decision.gate_decision_path,
        completion_decision_path=completion_decision.completion_decision_path,
        validate_record_path=_relative_path(validate_record_path, relative_to=paths.root),
        execution_report_path=_relative_path(execution_report_path, relative_to=paths.root),
        selected_action=selected_action,
        remediation_spec_id=remediation_spec_id,
        remediation_task_id=task_card.task_id,
        remediation_task_title=task_card.title,
        backlog_depth_after_enqueue=backlog_depth,
        reasons=gate_decision.reasons,
        recovery_latch_updated=False,
    )
    recovery_latch_updated = _persist_audit_recovery_decision(
        paths,
        checkpoint,
        emitted_at=emitted_at,
        remediation_record=remediation_record,
    )
    remediation_record = remediation_record.model_copy(
        update={"recovery_latch_updated": recovery_latch_updated}
    )
    _write_json_model(remediation_path, remediation_record)
    return remediation_record
