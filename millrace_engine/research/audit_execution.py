"""Execution and record-movement helpers for the research audit loop."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
import json

from ..baseline_assets import packaged_baseline_asset
from ..contracts import AuditContract, AuditExecutionFinding, AuditExecutionReport, ResearchStatus
from ..markdown import write_text_atomic
from ..paths import RuntimePaths
from .audit_gate_helpers import _evaluate_completion_gate
from .audit_models import (
    AuditExecutionError,
    AuditGatekeeperExecutionResult,
    AuditGatekeeperRecord,
    AuditIntakeExecutionResult,
    AuditIntakeRecord,
    AuditLifecycleStatus,
    AuditQueueRecord,
    AuditValidateExecutionResult,
    AuditValidateRecord,
)
from .audit_parsing import _COMMAND_SECTION_NAMES, _SUMMARY_SECTION_NAMES, _extract_section_lines
from .audit_queue_helpers import load_audit_queue_record
from .audit_remediation_helpers import _persist_audit_remediation
from .audit_storage_helpers import (
    _audit_record_path,
    _audit_remediation_record_path,
    _audited_source_path,
    _execution_report_path,
    _load_json_model,
    _relative_path,
    _resolve_path_token,
    _validate_record_path,
    _write_audit_history,
    _write_audit_summary,
    _write_json_model,
)
from .parser_helpers import _parse_frontmatter_block

if TYPE_CHECKING:
    from .state import ResearchCheckpoint


_PACKAGED_STRICT_CONTRACT_REF = "packaged:agents/audit/strict_contract.json"
_PACKAGED_COMPLETION_MANIFEST_REF = "packaged:agents/audit/completion_manifest.json"
_PACKAGED_OBJECTIVE_CONTRACT_REF = "packaged:agents/objective/contract.yaml"
_AUDIT_HISTORY_RETENTION_KEEP = 100


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _frontmatter_value(frontmatter: dict[str, str], key: str, *, default: str) -> str:
    raw_value = frontmatter.get(key)
    if raw_value is None:
        return default
    normalized = raw_value.strip()
    return normalized or default


def _render_frontmatter(frontmatter: dict[str, str]) -> str:
    lines = ["---"]
    for key, value in frontmatter.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def _resolve_checkpoint_audit_record(
    paths: RuntimePaths,
    checkpoint: "ResearchCheckpoint",
) -> AuditQueueRecord:
    active_request = checkpoint.active_request
    candidates: list[tuple[Path, AuditLifecycleStatus | None]] = []
    if active_request is not None and active_request.audit_record is not None:
        source_path = _resolve_path_token(active_request.audit_record.source_path, relative_to=paths.root)
        expected_status = None
        parent_name = source_path.parent.name.strip().lower()
        if parent_name in {status.value for status in AuditLifecycleStatus}:
            expected_status = AuditLifecycleStatus(parent_name)
        candidates.append((source_path, expected_status))
    for ownership in checkpoint.owned_queues:
        if ownership.item_path is None:
            continue
        item_path = _resolve_path_token(ownership.item_path, relative_to=paths.root)
        expected_status = None
        parent_name = item_path.parent.name.strip().lower()
        if parent_name in {status.value for status in AuditLifecycleStatus}:
            expected_status = AuditLifecycleStatus(parent_name)
        candidates.append((item_path, expected_status))
    seen: set[Path] = set()
    for candidate_path, expected_status in candidates:
        if candidate_path in seen or not candidate_path.exists():
            continue
        seen.add(candidate_path)
        return load_audit_queue_record(candidate_path, expected_status=expected_status)
    raise AuditExecutionError("audit checkpoint does not reference an available audit queue item")


def _load_audit_contract(paths: RuntimePaths) -> tuple[AuditContract, str]:
    contract_path = paths.audit_strict_contract_file
    if contract_path.exists():
        payload = json.loads(contract_path.read_text(encoding="utf-8"))
        contract_ref = _relative_path(contract_path, relative_to=paths.root)
    else:
        payload = json.loads(packaged_baseline_asset("agents/audit/strict_contract.json").read_text(encoding="utf-8"))
        contract_ref = _PACKAGED_STRICT_CONTRACT_REF
    return AuditContract.model_validate(payload), contract_ref


def _build_execution_report(
    *,
    paths: RuntimePaths,
    run_id: str,
    emitted_at: datetime,
    record: AuditQueueRecord,
    contract: AuditContract,
    strict_contract_ref: str,
) -> AuditExecutionReport:
    source_text = record.source_path.read_text(encoding="utf-8")
    observed_commands = _extract_section_lines(source_text, section_names=_COMMAND_SECTION_NAMES)
    observed_summaries = _extract_section_lines(source_text, section_names=_SUMMARY_SECTION_NAMES)
    findings: list[AuditExecutionFinding] = []

    if contract.enabled:
        for required_substring in contract.required_command_substrings:
            matching_commands = tuple(
                command for command in observed_commands if required_substring.casefold() in command.casefold()
            )
            if matching_commands:
                continue
            findings.append(
                AuditExecutionFinding(
                    kind="missing_required_command_substring",
                    expected=required_substring,
                    observed=observed_commands,
                    message=f"Missing required command substring `{required_substring}`.",
                )
            )

        for forbidden_marker in contract.forbidden_command_markers:
            offending_commands = tuple(
                command for command in observed_commands if forbidden_marker.casefold() in command.casefold()
            )
            if not offending_commands:
                continue
            findings.append(
                AuditExecutionFinding(
                    kind="forbidden_command_marker",
                    expected=forbidden_marker,
                    observed=offending_commands,
                    message=f"Forbidden command marker `{forbidden_marker}` found in observed commands.",
                )
            )

        for required_summary in contract.required_summaries:
            matching_summaries = tuple(
                summary for summary in observed_summaries if required_summary.casefold() in summary.casefold()
            )
            if matching_summaries:
                continue
            findings.append(
                AuditExecutionFinding(
                    kind="missing_required_summary",
                    expected=required_summary,
                    observed=observed_summaries,
                    message=f"Missing required summary `{required_summary}`.",
                )
            )

    return AuditExecutionReport(
        run_id=run_id,
        emitted_at=emitted_at,
        audit_id=record.audit_id,
        working_path=_relative_path(record.source_path, relative_to=paths.root),
        contract_id=contract.contract_id,
        strict_contract_path=strict_contract_ref,
        strict_contract_enabled=contract.enabled,
        observed_commands=observed_commands,
        observed_summaries=observed_summaries,
        command_count=len(observed_commands),
        summary_count=len(observed_summaries),
        findings=findings,
        finding_count=len(findings),
        passed=not findings,
    )


def _move_audit_record(
    record: AuditQueueRecord,
    *,
    target_status: AuditLifecycleStatus,
    owner: str,
    updated_at: datetime,
) -> AuditQueueRecord:
    source_path = record.source_path
    target_dir = source_path.parent.parent / target_status.value
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / source_path.name
    updated_iso = updated_at.isoformat().replace("+00:00", "Z")

    if source_path.exists():
        frontmatter, body = _parse_frontmatter_block(source_path.read_text(encoding="utf-8"))
        frontmatter["audit_id"] = _frontmatter_value(frontmatter, "audit_id", default=record.audit_id)
        frontmatter["scope"] = _frontmatter_value(frontmatter, "scope", default=record.scope)
        frontmatter["trigger"] = _frontmatter_value(frontmatter, "trigger", default=record.trigger.value)
        frontmatter["status"] = target_status.value
        frontmatter["owner"] = owner
        frontmatter["created_at"] = _frontmatter_value(frontmatter, "created_at", default=updated_iso)
        frontmatter["updated_at"] = updated_iso
        write_text_atomic(target_path, _render_frontmatter(frontmatter) + "\n\n" + body.lstrip("\n"))
        if source_path != target_path:
            source_path.unlink()
    elif not target_path.exists():
        raise AuditExecutionError(f"audit queue item is missing: {source_path.as_posix()}")

    return load_audit_queue_record(target_path, expected_status=target_status)


def execute_audit_intake(
    paths: RuntimePaths,
    checkpoint: "ResearchCheckpoint",
    *,
    run_id: str,
    emitted_at: datetime | None = None,
) -> AuditIntakeExecutionResult:
    """Move one audit item into the working queue and persist intake evidence."""

    emitted_at = emitted_at or _utcnow()
    record = _resolve_checkpoint_audit_record(paths, checkpoint)
    working_record = record
    if record.lifecycle_status is AuditLifecycleStatus.INCOMING:
        working_record = _move_audit_record(
            record,
            target_status=AuditLifecycleStatus.WORKING,
            owner=run_id,
            updated_at=emitted_at,
        )
    elif record.lifecycle_status is not AuditLifecycleStatus.WORKING:
        raise AuditExecutionError(
            f"audit intake requires incoming or working status, got {record.lifecycle_status.value}"
        )

    intake_record = AuditIntakeRecord(
        run_id=run_id,
        emitted_at=emitted_at,
        audit_id=working_record.audit_id,
        title=working_record.title,
        trigger=working_record.trigger,
        scope=working_record.scope,
        source_path=_relative_path(record.source_path, relative_to=paths.root),
        working_path=_relative_path(working_record.source_path, relative_to=paths.root),
    )
    record_path = _audit_record_path(paths, stage="intake", run_id=run_id)
    _write_json_model(record_path, intake_record)
    return AuditIntakeExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        working_path=_relative_path(working_record.source_path, relative_to=paths.root),
        audit_record=working_record,
    )


def execute_audit_validate(
    paths: RuntimePaths,
    checkpoint: "ResearchCheckpoint",
    *,
    run_id: str,
    emitted_at: datetime | None = None,
) -> AuditValidateExecutionResult:
    """Evaluate the strict command contract and persist deterministic audit evidence."""

    emitted_at = emitted_at or _utcnow()
    record = _resolve_checkpoint_audit_record(paths, checkpoint)
    if record.lifecycle_status is not AuditLifecycleStatus.WORKING:
        raise AuditExecutionError(
            f"audit validate requires a working audit item, got {record.lifecycle_status.value}"
        )

    contract, strict_contract_ref = _load_audit_contract(paths)
    execution_report = _build_execution_report(
        paths=paths,
        run_id=run_id,
        emitted_at=emitted_at,
        record=record,
        contract=contract,
        strict_contract_ref=strict_contract_ref,
    )
    execution_report_path = _execution_report_path(paths, run_id=run_id)
    _write_json_model(execution_report_path, execution_report)
    findings = tuple(finding.message for finding in execution_report.findings)
    if execution_report.passed:
        summary = (
            "Command-contract guard passed; observed audit evidence satisfied the strict contract."
            if contract.enabled
            else "Strict command-contract guard is disabled; no blocking findings were produced."
        )
        recommended_decision = "pass"
    else:
        summary = (
            "Command-contract guard failed; blocking findings were recorded in the durable execution report."
        )
        recommended_decision = "fail"
    validate_record = AuditValidateRecord(
        run_id=run_id,
        emitted_at=emitted_at,
        audit_id=record.audit_id,
        title=record.title,
        trigger=record.trigger,
        scope=record.scope,
        working_path=_relative_path(record.source_path, relative_to=paths.root),
        execution_report_path=_relative_path(execution_report_path, relative_to=paths.root),
        finding_count=len(findings),
        findings=findings,
        summary=summary,
        recommended_decision=recommended_decision,
    )
    record_path = _validate_record_path(paths, run_id=run_id)
    _write_json_model(record_path, validate_record)
    return AuditValidateExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        working_path=_relative_path(record.source_path, relative_to=paths.root),
        audit_record=record,
        validate_record=validate_record,
    )


def execute_audit_gatekeeper(
    paths: RuntimePaths,
    checkpoint: "ResearchCheckpoint",
    *,
    run_id: str,
    emitted_at: datetime | None = None,
) -> AuditGatekeeperExecutionResult:
    """Persist the terminal audit decision and move the queue item to its final bucket."""

    emitted_at = emitted_at or _utcnow()
    record = _resolve_checkpoint_audit_record(paths, checkpoint)
    if record.lifecycle_status is not AuditLifecycleStatus.WORKING:
        raise AuditExecutionError(
            f"audit gatekeeper requires a working audit item, got {record.lifecycle_status.value}"
        )

    validate_record_path = _validate_record_path(paths, run_id=run_id)
    if not validate_record_path.exists():
        raise AuditExecutionError(
            f"audit gatekeeper requires a validate report at {validate_record_path.as_posix()}"
        )
    validate_record = AuditValidateRecord.model_validate(
        _load_json_model(validate_record_path, AuditValidateRecord)
    )

    gate_decision, completion_decision, final_status = _evaluate_completion_gate(
        paths=paths,
        run_id=run_id,
        emitted_at=emitted_at,
        record=record,
        validate_record=validate_record,
        validate_record_path=validate_record_path,
        packaged_objective_contract_ref=_PACKAGED_OBJECTIVE_CONTRACT_REF,
        packaged_completion_manifest_ref=_PACKAGED_COMPLETION_MANIFEST_REF,
    )
    execution_report_path = _resolve_path_token(validate_record.execution_report_path, relative_to=paths.root)
    audited_source_path = _audited_source_path(paths, run_id=run_id, record=record)
    if final_status is ResearchStatus.AUDIT_FAIL:
        target_status = AuditLifecycleStatus.FAILED
        decision = "audit_fail"
    else:
        target_status = AuditLifecycleStatus.PASSED
        decision = "audit_pass"

    terminal_record = _move_audit_record(
        record,
        target_status=target_status,
        owner=run_id,
        updated_at=emitted_at,
    )
    remediation_record = None
    if final_status is ResearchStatus.AUDIT_FAIL:
        remediation_record = _persist_audit_remediation(
            paths,
            checkpoint,
            run_id=run_id,
            emitted_at=emitted_at,
            audited_source_path=audited_source_path,
            record=record,
            terminal_record=terminal_record,
            gate_decision=gate_decision,
            completion_decision=completion_decision,
            validate_record_path=validate_record_path,
            execution_report_path=execution_report_path,
        )
    _write_audit_summary(
        paths,
        emitted_at=emitted_at,
        audited_source_path=audited_source_path,
        record=record,
        terminal_record=terminal_record,
        gate_decision=gate_decision,
        completion_decision=completion_decision,
        final_status=final_status,
        remediation_record=remediation_record,
    )
    _write_audit_history(
        paths,
        emitted_at=emitted_at,
        audited_source_path=audited_source_path,
        record=record,
        terminal_record=terminal_record,
        gate_decision=gate_decision,
        completion_decision=completion_decision,
        final_status=final_status,
        remediation_record=remediation_record,
        retention_keep=_AUDIT_HISTORY_RETENTION_KEEP,
    )
    gate_record = AuditGatekeeperRecord(
        run_id=run_id,
        emitted_at=emitted_at,
        audit_id=terminal_record.audit_id,
        title=terminal_record.title,
        source_path=audited_source_path,
        terminal_path=_relative_path(terminal_record.source_path, relative_to=paths.root),
        validate_record_path=_relative_path(validate_record_path, relative_to=paths.root),
        decision=decision,
        final_status=final_status,
        rationale=(
            f"Gate decision {gate_decision.decision} moved the queue item to `{target_status.value}`."
        ),
        gate_decision_path=gate_decision.gate_decision_path,
        completion_decision_path=completion_decision.completion_decision_path,
        remediation_record_path=(
            None
            if remediation_record is None
            else _relative_path(_audit_remediation_record_path(paths, run_id=run_id), relative_to=paths.root)
        ),
        remediation_spec_id=(None if remediation_record is None else remediation_record.remediation_spec_id),
        remediation_task_id=(None if remediation_record is None else remediation_record.remediation_task_id),
    )
    record_path = _audit_record_path(paths, stage="gatekeeper", run_id=run_id)
    _write_json_model(record_path, gate_record)
    return AuditGatekeeperExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        terminal_path=_relative_path(terminal_record.source_path, relative_to=paths.root),
        gate_decision_path=gate_decision.gate_decision_path,
        completion_decision_path=completion_decision.completion_decision_path,
        audit_record=terminal_record,
        final_status=final_status,
        remediation_record_path=(
            None
            if remediation_record is None
            else _relative_path(_audit_remediation_record_path(paths, run_id=run_id), relative_to=paths.root)
        ),
    )


__all__ = [
    "AuditExecutionError",
    "execute_audit_gatekeeper",
    "execute_audit_intake",
    "execute_audit_validate",
]
