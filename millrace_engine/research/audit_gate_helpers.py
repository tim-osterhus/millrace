"""Completion-gate helpers for research audit execution."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ..baseline_assets import packaged_baseline_asset
from ..contracts import (
    AuditContract,
    AuditExecutionReport,
    AuditGateDecision,
    AuditGateDecisionCounts,
    CompletionDecision,
    CompletionManifest,
    ObjectiveContract,
    ResearchStatus,
    load_objective_contract,
)
from ..markdown import parse_task_store
from ..paths import RuntimePaths
from .audit_models import AuditQueueRecord, AuditValidateRecord
from .audit_storage_helpers import (
    _load_json_model,
    _relative_path,
    _resolve_path_token,
    _write_json_model,
)


def _normalize_required_text(value: str, *, field_name: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError(f"{field_name} may not be empty")
    return normalized


def _normalize_optional_text(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.strip().split())
    if not normalized:
        return None
    return normalized


def _default_gate_decision_path(paths: RuntimePaths) -> Path:
    return paths.reports_dir / "audit_gate_decision.json"


def _default_completion_decision_path(paths: RuntimePaths) -> Path:
    return paths.reports_dir / "completion_decision.json"


def _load_objective_contract(
    paths: RuntimePaths,
    *,
    packaged_objective_contract_ref: str,
) -> tuple[ObjectiveContract | None, str, tuple[str, ...], Path, Path]:
    contract_path = paths.objective_contract_file
    gate_decision_path = _default_gate_decision_path(paths)
    completion_decision_path = _default_completion_decision_path(paths)
    contract_ref = _relative_path(contract_path, relative_to=paths.root)
    issues: list[str] = []

    if contract_path.exists():
        raw_text = contract_path.read_text(encoding="utf-8")
    else:
        asset_path = packaged_baseline_asset("agents/objective/contract.yaml")
        raw_text = asset_path.read_text(encoding="utf-8")
        contract_ref = packaged_objective_contract_ref

    try:
        contract = load_objective_contract(raw_text)
    except (ValidationError, ValueError) as exc:
        issues.append(
            f"Objective contract is invalid: {_normalize_required_text(str(exc), field_name='objective contract')}"
        )
        return None, contract_ref, tuple(issues), gate_decision_path, completion_decision_path

    gate_decision_path = _resolve_path_token(contract.completion.fallback_decision_file, relative_to=paths.root)
    completion_decision_path = _resolve_path_token(
        contract.completion.authoritative_decision_file,
        relative_to=paths.root,
    )
    return contract, contract_ref, tuple(issues), gate_decision_path, completion_decision_path


def _load_completion_manifest(
    paths: RuntimePaths,
    *,
    packaged_completion_manifest_ref: str,
) -> tuple[CompletionManifest | None, str, tuple[str, ...]]:
    manifest_path = paths.audit_completion_manifest_file
    manifest_ref = _relative_path(manifest_path, relative_to=paths.root)
    issues: list[str] = []

    if manifest_path.exists():
        raw_text = manifest_path.read_text(encoding="utf-8")
    else:
        asset_path = packaged_baseline_asset("agents/audit/completion_manifest.json")
        raw_text = asset_path.read_text(encoding="utf-8")
        manifest_ref = packaged_completion_manifest_ref

    try:
        manifest = CompletionManifest.model_validate(json.loads(raw_text))
    except (json.JSONDecodeError, ValidationError) as exc:
        issues.append(
            f"Completion manifest is invalid: {_normalize_required_text(str(exc), field_name='completion manifest')}"
        )
        return None, manifest_ref, tuple(issues)
    return manifest, manifest_ref, tuple(issues)


def _dedupe_messages(messages: list[str]) -> tuple[str, ...]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in messages:
        normalized = _normalize_optional_text(item, field_name="message")
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return tuple(deduped)


def _task_store_card_count(path: Path) -> tuple[int, str | None]:
    if not path.exists():
        return 0, None
    try:
        document = parse_task_store(path.read_text(encoding="utf-8"), source_file=path)
    except ValueError as exc:
        return 0, f"Task store `{path.name}` is invalid: {_normalize_required_text(str(exc), field_name='task store')}"
    return len(document.cards), None


def _count_open_gaps(gaps_path: Path) -> tuple[int, str | None]:
    if not gaps_path.exists():
        return 0, None

    try:
        text = gaps_path.read_text(encoding="utf-8")
    except OSError as exc:
        return 0, f"Gaps file `{gaps_path.name}` could not be read: {_normalize_required_text(str(exc), field_name='gaps')}"

    actionable_open_gaps = 0
    in_open = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Open Gaps"):
            in_open = True
            continue
        if in_open and stripped.startswith("## "):
            break
        if not in_open or not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.split("|")[1:-1]]
        if len(cells) < 7:
            continue
        gap_id = cells[0]
        status = cells[5].lower()
        if not gap_id or gap_id in {"Gap ID", "---", "GAP-000"}:
            continue
        if "<" in stripped or ">" in stripped:
            continue
        if status == "open":
            actionable_open_gaps += 1
    return actionable_open_gaps, None


def _evaluate_completion_gate(
    *,
    paths: RuntimePaths,
    run_id: str,
    emitted_at: datetime,
    record: "AuditQueueRecord",
    validate_record: "AuditValidateRecord",
    validate_record_path: Path,
    packaged_objective_contract_ref: str,
    packaged_completion_manifest_ref: str,
) -> tuple[AuditGateDecision, CompletionDecision, ResearchStatus]:
    execution_report_path = _resolve_path_token(validate_record.execution_report_path, relative_to=paths.root)
    gate_checks: list[bool] = []
    reasons: list[str] = []

    objective_contract, objective_contract_ref, objective_issues, gate_decision_path, completion_decision_path = (
        _load_objective_contract(
            paths,
            packaged_objective_contract_ref=packaged_objective_contract_ref,
        )
    )
    gate_checks.append(not objective_issues)
    reasons.extend(objective_issues)

    execution_report: AuditExecutionReport | None = None
    try:
        execution_report = AuditExecutionReport.model_validate(
            _load_json_model(execution_report_path, AuditExecutionReport)
        )
    except (FileNotFoundError, ValidationError, json.JSONDecodeError, ValueError) as exc:
        reasons.append(
            f"Audit execution report is unavailable: {_normalize_required_text(str(exc), field_name='execution report')}"
        )
        gate_checks.append(False)
    else:
        artifacts_aligned = (
            execution_report.run_id == run_id
            and execution_report.audit_id == record.audit_id
            and validate_record.execution_report_path == _relative_path(execution_report_path, relative_to=paths.root)
        )
        gate_checks.append(artifacts_aligned)
        if not artifacts_aligned:
            reasons.append("Audit execution and validate artifacts are not aligned for this audit run.")

    validate_passed = validate_record.recommended_decision == "pass"
    gate_checks.append(validate_passed)
    if not validate_passed:
        reasons.extend(list(validate_record.findings) or [validate_record.summary])

    completion_manifest, completion_manifest_ref, manifest_issues = _load_completion_manifest(
        paths,
        packaged_completion_manifest_ref=packaged_completion_manifest_ref,
    )
    gate_checks.append(not manifest_issues)
    reasons.extend(manifest_issues)

    required_commands: tuple[str, ...] = ()
    if completion_manifest is not None:
        gate_checks.append(completion_manifest.configured)
        if not completion_manifest.configured:
            reasons.append("Completion manifest is not configured (`configured=false`).")

        required_commands = tuple(command.command for command in completion_manifest.required_commands())
        has_required_commands = bool(required_commands)
        gate_checks.append(has_required_commands)
        if completion_manifest.configured and not has_required_commands:
            reasons.append("Completion manifest is configured but declares no required completion commands.")
    else:
        gate_checks.extend((False, False))

    observed_commands = execution_report.observed_commands if execution_report is not None else ()
    missing_required_commands = tuple(
        command for command in required_commands if command not in observed_commands
    )
    completion_command_coverage = not missing_required_commands
    gate_checks.append(completion_command_coverage)
    if missing_required_commands:
        reasons.append(
            "Required completion command evidence is missing: " + "; ".join(missing_required_commands[:5])
        )

    active_task_cards, active_issue = _task_store_card_count(paths.tasks_file)
    backlog_cards, backlog_issue = _task_store_card_count(paths.backlog_file)
    pending_task_cards, pending_issue = _task_store_card_count(paths.taskspending_file)
    for issue in (active_issue, backlog_issue, pending_issue):
        if issue is not None:
            reasons.append(issue)

    task_store_cards = active_task_cards + backlog_cards + pending_task_cards
    require_task_store_cards_zero = (
        objective_contract.completion.require_task_store_cards_zero
        if objective_contract is not None
        else True
    )
    if require_task_store_cards_zero:
        gate_checks.append(task_store_cards == 0)
        if active_task_cards:
            reasons.append(f"Active task store still has {active_task_cards} task card(s).")
        if backlog_cards:
            reasons.append(f"Backlog still has {backlog_cards} task card(s).")
        if pending_task_cards:
            reasons.append(f"Pending task store still has {pending_task_cards} task card(s).")

    open_gaps, gaps_issue = _count_open_gaps(paths.agents_dir / "gaps.md")
    if gaps_issue is not None:
        reasons.append(gaps_issue)
    require_open_gaps_zero = (
        objective_contract.completion.require_open_gaps_zero
        if objective_contract is not None
        else True
    )
    if require_open_gaps_zero:
        gate_checks.append(open_gaps == 0)
        if open_gaps:
            reasons.append(f"{open_gaps} actionable open gap row(s) remain in `agents/gaps.md`.")

    deduped_reasons = _dedupe_messages(reasons)
    required_total = len(gate_checks)
    required_pass = sum(1 for item in gate_checks if item)
    counts = AuditGateDecisionCounts(
        required_total=required_total,
        required_pass=required_pass,
        required_fail=required_total - required_pass,
        required_blocked=0,
        completion_required=len(required_commands),
        completion_pass=len(required_commands) - len(missing_required_commands),
        open_gaps=open_gaps,
        task_store_cards=task_store_cards,
        active_task_cards=active_task_cards,
        backlog_cards=backlog_cards,
        pending_task_cards=pending_task_cards,
    )
    decision = "PASS" if not deduped_reasons and required_pass == required_total else "FAIL"
    final_status = ResearchStatus.AUDIT_PASS if decision == "PASS" else ResearchStatus.AUDIT_FAIL

    gate_decision = AuditGateDecision(
        run_id=run_id,
        audit_id=record.audit_id,
        generated_at=emitted_at,
        decision=decision,
        reasons=deduped_reasons,
        counts=counts,
        gate_decision_path=_relative_path(gate_decision_path, relative_to=paths.root),
        objective_contract_path=objective_contract_ref,
        completion_manifest_path=completion_manifest_ref,
        execution_report_path=_relative_path(execution_report_path, relative_to=paths.root),
        validate_record_path=_relative_path(validate_record_path, relative_to=paths.root),
    )
    completion_decision = CompletionDecision(
        run_id=run_id,
        audit_id=record.audit_id,
        generated_at=emitted_at,
        decision=decision,
        reasons=deduped_reasons,
        counts=counts,
        completion_decision_path=_relative_path(completion_decision_path, relative_to=paths.root),
        gate_decision_path=_relative_path(gate_decision_path, relative_to=paths.root),
        objective_contract_path=objective_contract_ref,
    )
    _write_json_model(gate_decision_path, gate_decision)
    _write_json_model(completion_decision_path, completion_decision)
    return gate_decision, completion_decision, final_status
