"""Storage and persistence helpers for research audit execution."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
import json
import re

from pydantic import ValidationError

from ..contracts import CompletionDecision, ContractModel, ResearchStatus
from ..markdown import write_text_atomic
from ..paths import RuntimePaths
from .path_helpers import _relative_path, _resolve_path_token
from .persistence_helpers import _load_json_model, _write_json_model as _shared_write_json_model

if TYPE_CHECKING:
    from .audit import AuditGateDecision, AuditQueueRecord, AuditRemediationRecord, AuditSummary


def _audit_runtime_dir(paths: RuntimePaths) -> Path:
    return paths.research_runtime_dir / "audit"


def _write_json_model(path: Path, model: ContractModel) -> None:
    _shared_write_json_model(path, model, create_parent=True, by_alias=True)


def _audit_record_path(paths: RuntimePaths, *, stage: str, run_id: str) -> Path:
    return _audit_runtime_dir(paths) / stage / f"{run_id}.json"


def _audit_history_path(paths: RuntimePaths) -> Path:
    return paths.agents_dir / "audit_history.md"


def _audit_summary_path(paths: RuntimePaths) -> Path:
    return paths.agents_dir / "audit_summary.json"


def _audit_remediation_record_path(paths: RuntimePaths, *, run_id: str) -> Path:
    return _audit_record_path(paths, stage="remediation", run_id=run_id)


def _validate_record_path(paths: RuntimePaths, *, run_id: str) -> Path:
    return _audit_record_path(paths, stage="validate", run_id=run_id)


def _execution_report_path(paths: RuntimePaths, *, run_id: str) -> Path:
    return _audit_record_path(paths, stage="execution", run_id=run_id)


def _audited_source_path(paths: RuntimePaths, *, run_id: str, record: "AuditQueueRecord") -> str:
    from .audit import AuditIntakeRecord

    intake_record_path = _audit_record_path(paths, stage="intake", run_id=run_id)
    if intake_record_path.exists():
        intake_record = _load_json_model(intake_record_path, AuditIntakeRecord)
        return intake_record.source_path
    return _relative_path(record.source_path, relative_to=paths.root)


def _default_audit_summary() -> "AuditSummary":
    from .audit import AuditSummary, AuditSummaryLastOutcome

    return AuditSummary(
        updated_at=None,
        last_outcome=AuditSummaryLastOutcome(status="none", details="none", at=None),
        counts={"total": 0, "pass": 0, "fail": 0},
    )


def _load_audit_summary(paths: RuntimePaths) -> "AuditSummary":
    from .audit import AuditSummary

    summary_path = _audit_summary_path(paths)
    if not summary_path.exists():
        return _default_audit_summary()
    try:
        return AuditSummary.model_validate_json(summary_path.read_text(encoding="utf-8"))
    except (ValidationError, ValueError, json.JSONDecodeError):
        return _default_audit_summary()


def load_audit_summary(paths: RuntimePaths) -> "AuditSummary":
    """Load the workspace audit summary with fail-soft defaults."""

    return _load_audit_summary(paths)


def load_audit_remediation_record(
    paths: RuntimePaths,
    *,
    run_id: str,
) -> "AuditRemediationRecord | None":
    """Load one audit remediation record if it exists."""

    from .audit import AuditRemediationRecord

    record_path = _audit_remediation_record_path(paths, run_id=run_id)
    if not record_path.exists():
        return None
    return AuditRemediationRecord.model_validate_json(record_path.read_text(encoding="utf-8"))


def _write_audit_summary(
    paths: RuntimePaths,
    *,
    emitted_at: datetime,
    audited_source_path: str,
    record: "AuditQueueRecord",
    terminal_record: "AuditQueueRecord",
    gate_decision: "AuditGateDecision",
    completion_decision: CompletionDecision,
    final_status: ResearchStatus,
    remediation_record: "AuditRemediationRecord | None",
) -> "AuditSummary":
    from .audit import AuditSummary, AuditSummaryLastOutcome

    summary = _load_audit_summary(paths)
    counts = dict(summary.counts)
    if final_status is ResearchStatus.AUDIT_PASS:
        counts["pass"] = counts.get("pass", 0) + 1
        counts["total"] = counts.get("total", 0) + 1
    elif final_status is ResearchStatus.AUDIT_FAIL:
        counts["fail"] = counts.get("fail", 0) + 1
        counts["total"] = counts.get("total", 0) + 1

    details = "; ".join(gate_decision.reasons[:5]) if gate_decision.reasons else "none"
    payload = AuditSummary(
        updated_at=emitted_at,
        last_outcome=AuditSummaryLastOutcome(
            status=final_status.value,
            details=details,
            at=emitted_at,
            audit_id=record.audit_id,
            title=record.title,
            scope=record.scope,
            trigger=record.trigger,
            decision=gate_decision.decision,
            reason_count=len(gate_decision.reasons),
            source_path=audited_source_path,
            terminal_path=_relative_path(terminal_record.source_path, relative_to=paths.root),
            gate_decision_path=gate_decision.gate_decision_path,
            completion_decision_path=completion_decision.completion_decision_path,
            remediation_spec_id=(
                None if remediation_record is None else remediation_record.remediation_spec_id
            ),
            remediation_task_id=(
                None if remediation_record is None else remediation_record.remediation_task_id
            ),
            remediation_record_path=(
                None
                if remediation_record is None
                else _relative_path(
                    _audit_remediation_record_path(paths, run_id=remediation_record.run_id),
                    relative_to=paths.root,
                )
            ),
        ),
        counts=counts,
    )
    _write_json_model(_audit_summary_path(paths), payload)
    return payload


def _write_audit_history(
    paths: RuntimePaths,
    *,
    emitted_at: datetime,
    audited_source_path: str,
    record: "AuditQueueRecord",
    terminal_record: "AuditQueueRecord",
    gate_decision: "AuditGateDecision",
    completion_decision: CompletionDecision,
    final_status: ResearchStatus,
    remediation_record: "AuditRemediationRecord | None",
    retention_keep: int,
) -> None:
    history_path = _audit_history_path(paths)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    existing_entries: list[str] = []
    if history_path.exists():
        text = history_path.read_text(encoding="utf-8", errors="replace")
        existing_entries = [
            match.group(0).rstrip() for match in re.finditer(r"(?ms)^## .*?(?=^## |\Z)", text)
        ]

    lines = [
        f"## {emitted_at.isoformat().replace('+00:00', 'Z')} - {final_status.value}",
        "",
        f"- Audit: `{record.audit_id}` :: {record.title}",
        f"- Scope: `{record.scope}`",
        f"- Trigger: `{record.trigger.value}`",
        f"- Decision: `{gate_decision.decision}` ({len(gate_decision.reasons)} reason(s))",
        f"- Source path: `{audited_source_path}`",
        f"- Terminal path: `{_relative_path(terminal_record.source_path, relative_to=paths.root)}`",
        f"- Gate decision: `{gate_decision.gate_decision_path}`",
        f"- Completion decision: `{completion_decision.completion_decision_path}`",
    ]
    if gate_decision.reasons:
        lines.append(f"- Details: {'; '.join(gate_decision.reasons[:5])}")
    else:
        lines.append("- Details: none")
    if remediation_record is not None:
        lines.append(
            f"- Remediation: `{remediation_record.remediation_spec_id}` -> `{remediation_record.remediation_task_id}`"
        )
        lines.append(
            f"- Remediation record: `{_relative_path(_audit_remediation_record_path(paths, run_id=remediation_record.run_id), relative_to=paths.root)}`"
        )
    else:
        lines.append("- Remediation: none")
    entry = "\n".join(lines).rstrip()
    entries = [entry, *existing_entries][:retention_keep]
    header = [
        "# Audit History",
        "",
        "Local audit outcomes recorded by `millrace_engine.research.audit` (newest first).",
        "",
    ]
    rendered = "\n".join(header) + "\n\n".join(entries) + ("\n" if entries else "")
    write_text_atomic(history_path, rendered)
