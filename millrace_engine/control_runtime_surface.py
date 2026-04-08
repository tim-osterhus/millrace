"""Runtime/report-facing control surface helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator, Literal
import time

from pydantic import ValidationError

from .contracts import AuditGateDecision, CompletionDecision, ExecutionStatus, ResearchStatus
from .control_common import ControlError, event_log_control_error, expected_error_message, single_line_message, validation_error_message
from .control_models import (
    ResearchReport,
    RunProvenanceReport,
    RuntimeState,
    SelectionExplanationView,
    StatusReport,
    SupervisorAction,
    SupervisorAttentionReason,
    SupervisorReport,
)
from .control_reports import (
    asset_inventory_for,
    build_live_runtime_state,
    completion_state_view,
    count_deferred,
    decision_report_paths,
    live_research_runtime_state,
    read_control_research_state,
    read_control_runtime_state,
    read_event_log,
    read_run_provenance,
    read_runtime_state,
    research_queue_family_view,
    selection_explanation,
    selection_preview_for,
    size_status_view,
    snapshot_selection_explanation,
)
from .events import EventRecord, EventType, is_research_event_type
from .health import WorkspaceHealthReport
from .paths import RuntimePaths
from .policies import ExecutionIntegrationSnapshot, resolve_execution_integration_context
from .queue import QueueError, TaskQueue
from .research.audit import load_audit_remediation_record, load_audit_summary
from .research.governance import build_research_governance_report
from .research.interview import InterviewError, list_interview_questions
from .research.queues import discover_research_queues
from .research.state import ResearchQueueFamily, ResearchQueueOwnership
from .standard_runtime import RuntimeSelectionView, runtime_selection_view_from_snapshot
from .status import ControlPlane, StatusError, StatusStore


def _supervisor_allowed_actions(
    runtime: RuntimeState,
    *,
    backlog_depth: int,
    active_task_present: bool,
) -> tuple[SupervisorAction, ...]:
    actions: list[SupervisorAction] = [SupervisorAction.ADD_TASK]
    if backlog_depth > 1:
        actions.append(SupervisorAction.QUEUE_REORDER)
    if active_task_present or backlog_depth > 0:
        actions.extend(
            (
                SupervisorAction.QUEUE_CLEANUP_REMOVE,
                SupervisorAction.QUEUE_CLEANUP_QUARANTINE,
            )
        )
    if runtime.process_running:
        actions.append(SupervisorAction.STOP)
        actions.append(SupervisorAction.RESUME if runtime.paused else SupervisorAction.PAUSE)
    return tuple(actions)


def _derive_supervisor_run_context(
    events: tuple[EventRecord, ...],
    *,
    runtime: RuntimeState,
    research_status: ResearchStatus,
    generated_at: datetime,
) -> tuple[str | None, str | None, float | None]:
    current_run_id: str | None = None
    current_stage: str | None = None
    time_in_current_status_seconds: float | None = None

    for event in reversed(events):
        payload = event.payload
        if current_run_id is None:
            candidate = str(payload.get("run_id") or "").strip()
            if candidate:
                current_run_id = candidate
        if current_stage is None:
            for key in ("stage", "stage_type", "stage_id", "node_id"):
                candidate = str(payload.get(key) or "").strip()
                if candidate:
                    current_stage = candidate
                    break
        if current_run_id is not None and current_stage is not None:
            break

    for event in reversed(events):
        payload = event.payload
        if event.type is EventType.STATUS_CHANGED and payload.get("status") == runtime.execution_status.value:
            time_in_current_status_seconds = max((generated_at - event.timestamp).total_seconds(), 0.0)
            break
        if research_status is ResearchStatus.BLOCKED and event.type is EventType.RESEARCH_BLOCKED:
            time_in_current_status_seconds = max((generated_at - event.timestamp).total_seconds(), 0.0)
            break
        if research_status is ResearchStatus.IDLE and event.type is EventType.RESEARCH_IDLE:
            time_in_current_status_seconds = max((generated_at - event.timestamp).total_seconds(), 0.0)
            break

    return current_run_id, current_stage, time_in_current_status_seconds


def _attention_reason_for(
    *,
    health: WorkspaceHealthReport,
    status: StatusReport,
    pending_blocking_interview: bool,
) -> tuple[SupervisorAttentionReason, str]:
    runtime = status.runtime
    research = status.research
    assert research is not None

    if not health.bootstrap_ready:
        return SupervisorAttentionReason.NOT_BOOTSTRAPPED, "Workspace bootstrap checks are failing."
    if health.status.value == "fail" and not health.execution_ready:
        return SupervisorAttentionReason.RUNNER_NOT_READY, (
            "Workspace bootstrap passed, but execution prerequisites are not ready."
        )
    if health.status.value == "fail":
        return SupervisorAttentionReason.HEALTH_FAILED, "Workspace health checks are failing."
    if pending_blocking_interview:
        return (
            SupervisorAttentionReason.AWAITING_OPERATOR_INPUT,
            "A pending blocking interview question requires operator input.",
        )
    if research.status is ResearchStatus.AUDIT_FAIL:
        return SupervisorAttentionReason.AUDIT_FAILED, "Research reported AUDIT_FAIL."
    if runtime.execution_status is ExecutionStatus.BLOCKED:
        return SupervisorAttentionReason.BLOCKED_EXECUTION, "Execution status is BLOCKED."
    if research.status is ResearchStatus.BLOCKED:
        return SupervisorAttentionReason.BLOCKED_RESEARCH, "Research status is BLOCKED."
    if runtime.execution_status in {ExecutionStatus.QUICKFIX_NEEDED, ExecutionStatus.NET_WAIT} or research.status in {
        ResearchStatus.NET_WAIT,
    }:
        return SupervisorAttentionReason.DEGRADED_STATE, "Workspace is degraded and may require supervisor action."
    if runtime.execution_status is ExecutionStatus.IDLE:
        research_ready = any(family.ready for family in research.queue_families)
        if runtime.backlog_depth > 0 or research_ready:
            return (
                SupervisorAttentionReason.IDLE_WITH_PENDING_WORK,
                "Execution is idle while pending work remains available.",
            )
        return SupervisorAttentionReason.IDLE_WITH_NO_WORK, "Workspace is idle with no pending work."
    return SupervisorAttentionReason.NONE, "Workspace is active and does not currently require supervisor attention."


def status(control, *, detail: bool = False) -> StatusReport:
    active_task = TaskQueue(control.paths).active_task()
    size = size_status_view(control.loaded, task=active_task)
    snapshot = read_control_runtime_state(control.state_path)
    if snapshot is None:
        try:
            runtime = build_live_runtime_state(
                control.loaded,
                process_running=False,
                paused=False,
                pause_reason=None,
                pause_run_id=None,
                started_at=None,
                mode="once",
            )
        except (QueueError, StatusError, ValidationError, ValueError) as exc:
            raise ControlError(f"runtime state could not be read: {expected_error_message(exc)}") from exc
        source_kind = "live"
    else:
        runtime = snapshot
        source_kind = "snapshot"
    selection = selection_preview_for(
        control.loaded,
        size=size,
        current_status=runtime.execution_status,
    )
    queue = control.queue_inspect() if detail else control.queue()
    return StatusReport.model_validate(
        {
            "runtime": runtime,
            "source_kind": source_kind,
            "config_path": control.config_path,
            "config_source_kind": control.loaded.source.kind,
            "config_source": control.loaded.source,
            "selection": selection,
            "selection_explanation": selection_explanation(
                size=size,
                current_status=runtime.execution_status,
                selection=selection,
            ),
            "size": size,
            "integration_policy": resolve_execution_integration_context(
                ExecutionIntegrationSnapshot.from_config(control.loaded.config),
                task=active_task,
                policy_toggle_integration_mode=(
                    selection.policy_toggles.integration_mode
                    if selection.policy_toggles is not None
                    else None
                ),
                execution_node_ids=tuple(binding.node_id for binding in selection.stage_bindings),
            ),
            "assets": asset_inventory_for(control.loaded) if detail else None,
            "research": control.research_report() if detail else None,
            "active_task": queue.active_task if detail else None,
            "next_task": queue.next_task if detail else None,
        }
    )


def supervisor_report(control, *, recent_event_limit: int = 10) -> SupervisorReport:
    if recent_event_limit < 0:
        raise ControlError("supervisor_report requires a non-negative recent event limit")

    generated_at = datetime.now(timezone.utc)
    health = control.health()
    status_report = control.status(detail=True)
    research = status_report.research
    if research is None:
        raise ControlError("supervisor report requires detailed research visibility")

    recent_events = tuple(control.logs(n=recent_event_limit))
    try:
        pending_blocking_interview = any(
            question.status == "pending" and question.blocking
            for question in list_interview_questions(control.paths)
        )
    except (InterviewError, ValidationError, ValueError) as exc:
        raise ControlError(f"supervisor report failed: {expected_error_message(exc)}") from exc

    attention_reason, attention_summary = _attention_reason_for(
        health=health,
        status=status_report,
        pending_blocking_interview=pending_blocking_interview,
    )
    current_run_id, current_stage, time_in_current_status_seconds = _derive_supervisor_run_context(
        recent_events,
        runtime=status_report.runtime,
        research_status=research.status,
        generated_at=generated_at,
    )

    return SupervisorReport(
        workspace_root=control.paths.root,
        config_path=control.config_path,
        generated_at=generated_at,
        health_status=health.status,
        health_summary=health.summary,
        bootstrap_ready=health.bootstrap_ready,
        execution_ready=health.execution_ready,
        process_running=status_report.runtime.process_running,
        paused=status_report.runtime.paused,
        execution_status=status_report.runtime.execution_status,
        research_status=research.status,
        status_source_kind=status_report.source_kind,
        research_source_kind=research.source_kind,
        active_task=status_report.active_task,
        next_task=status_report.next_task,
        backlog_depth=status_report.runtime.backlog_depth,
        deferred_queue_size=status_report.runtime.deferred_queue_size,
        current_run_id=current_run_id,
        current_stage=current_stage,
        time_in_current_status_seconds=time_in_current_status_seconds,
        attention_reason=attention_reason,
        attention_summary=attention_summary,
        allowed_actions=_supervisor_allowed_actions(
            status_report.runtime,
            backlog_depth=status_report.runtime.backlog_depth,
            active_task_present=status_report.active_task is not None,
        ),
        recent_events=recent_events,
    )


def run_provenance(
    control,
    run_id: str,
    *,
    selection_view_builder=runtime_selection_view_from_snapshot,
) -> RunProvenanceReport:
    normalized_run_id = run_id.strip()
    if not normalized_run_id:
        raise ControlError("run_provenance requires a run_id")
    try:
        report = read_run_provenance(control.paths.runs_dir / normalized_run_id)
    except ValidationError as exc:
        raise ControlError(f"run provenance is invalid: {validation_error_message(exc)}") from exc
    except ValueError as exc:
        raise ControlError(f"run provenance is invalid: {single_line_message(exc)}") from exc
    if report is None:
        raise ControlError(f"run provenance not found: {normalized_run_id}")
    try:
        selection = (
            selection_view_builder(report.compile_snapshot, workspace_root=control.paths.root)
            if report.compile_snapshot is not None
            else None
        )
    except RuntimeError as exc:
        raise ControlError(f"run provenance selection failed: {single_line_message(exc)}") from exc
    except ValidationError as exc:
        raise ControlError(f"run provenance selection failed: {validation_error_message(exc)}") from exc
    current_preview: RuntimeSelectionView | None = None
    current_preview_explanation: SelectionExplanationView | None = None
    current_preview_error: str | None = None
    try:
        active_task = TaskQueue(control.paths).active_task()
        current_status = StatusStore(control.paths.status_file, ControlPlane.EXECUTION).read()
        size = size_status_view(control.loaded, task=active_task)
        current_preview = selection_preview_for(
            control.loaded,
            size=size,
            current_status=current_status,
        )
        current_preview_explanation = selection_explanation(
            size=size,
            current_status=current_status,
            selection=current_preview,
        )
    except ControlError as exc:
        current_preview_error = str(exc)
    routing_modes = report.expected_routing_modes()
    try:
        return report.with_selection_details(
            selection=selection,
            selection_explanation=(
                snapshot_selection_explanation(selection) if selection is not None else None
            ),
            current_preview=current_preview,
            current_preview_explanation=current_preview_explanation,
            current_preview_error=current_preview_error,
            routing_modes=routing_modes,
        )
    except ValidationError as exc:
        raise ControlError(f"run provenance is invalid: {validation_error_message(exc)}") from exc


def research_report(control) -> ResearchReport:
    observed_at = datetime.now(timezone.utc)
    state = read_control_research_state(control.paths)
    source_kind: Literal["snapshot", "live"] = "snapshot"
    if state is None:
        state = live_research_runtime_state(control.loaded, observed_at=observed_at)
        source_kind = "live"

    try:
        status_value = StatusStore(control.paths.research_status_file, ControlPlane.RESEARCH).read()
    except (FileNotFoundError, StatusError, ValidationError, ValueError) as exc:
        raise ControlError(f"research status could not be read: {expected_error_message(exc)}") from exc

    try:
        discovery = discover_research_queues(control.paths)
    except (ValidationError, ValueError) as exc:
        raise ControlError(f"research queue state could not be read: {expected_error_message(exc)}") from exc
    if source_kind == "live":
        state = state.model_copy(update={"queue_snapshot": discovery.to_snapshot(last_scanned_at=observed_at)})

    ownership_map: dict[ResearchQueueFamily, tuple[ResearchQueueOwnership, ...]] = {
        family: tuple(item for item in state.queue_snapshot.ownerships if item.family is family)
        for family in ResearchQueueFamily
    }
    gate_decision_path, completion_decision_path = decision_report_paths(control.paths)
    latest_gate_decision = None
    latest_completion_decision = None
    latest_audit_remediation = None
    audit_summary = load_audit_summary(control.paths)
    try:
        if gate_decision_path.exists():
            latest_gate_decision = AuditGateDecision.model_validate_json(
                gate_decision_path.read_text(encoding="utf-8")
            )
        if completion_decision_path.exists():
            latest_completion_decision = CompletionDecision.model_validate_json(
                completion_decision_path.read_text(encoding="utf-8")
            )
        if latest_gate_decision is not None:
            latest_audit_remediation = load_audit_remediation_record(
                control.paths,
                run_id=latest_gate_decision.run_id,
            )
    except ValidationError as exc:
        raise ControlError(f"research decision state is invalid: {validation_error_message(exc)}") from exc
    except ValueError as exc:
        raise ControlError(f"research decision state is invalid: {single_line_message(exc)}") from exc
    try:
        governance = build_research_governance_report(control.paths)
    except ValidationError as exc:
        raise ControlError(f"governance report state is invalid: {validation_error_message(exc)}") from exc
    except ValueError as exc:
        raise ControlError(f"governance report state is invalid: {single_line_message(exc)}") from exc

    return ResearchReport(
        config_path=control.config_path,
        source_kind=source_kind,
        configured_mode=control.loaded.config.research.mode,
        configured_idle_mode=control.loaded.config.research.idle_mode,
        status=status_value,
        runtime=state,
        queue_families=tuple(
            research_queue_family_view(scan, ownerships=ownership_map[scan.family])
            for scan in discovery.families
        ),
        deferred_breadcrumb_count=count_deferred(control.paths),
        audit_history_path=control.paths.agents_dir / "audit_history.md",
        audit_summary_path=control.paths.agents_dir / "audit_summary.json",
        audit_summary=audit_summary,
        latest_gate_decision=latest_gate_decision,
        latest_completion_decision=latest_completion_decision,
        latest_audit_remediation=latest_audit_remediation,
        governance=governance,
        completion_state=completion_state_view(
            control.paths,
            latest_completion_decision=latest_completion_decision,
        ),
    )


def research_history(control, limit: int = 20) -> list[EventRecord]:
    if limit < 0:
        raise ControlError("research_history requires a non-negative limit")
    if limit == 0:
        return []
    try:
        return [event for event in read_event_log(control.paths.engine_events_log) if is_research_event_type(event.type)][
            -limit:
        ]
    except (ValidationError, ValueError) as exc:
        raise event_log_control_error(exc) from exc


def logs(control, n: int = 50) -> list[EventRecord]:
    if n < 0:
        raise ControlError("logs requires a non-negative tail size")
    if n == 0:
        return []
    try:
        return read_event_log(control.paths.engine_events_log)[-n:]
    except (ValidationError, ValueError) as exc:
        raise event_log_control_error(exc) from exc


def events_subscribe(
    control,
    *,
    start_at_end: bool = True,
    poll_interval_seconds: float = 0.2,
    idle_timeout_seconds: float | None = None,
) -> Iterator[EventRecord]:
    if poll_interval_seconds <= 0:
        raise ControlError("poll_interval_seconds must be greater than zero")
    if idle_timeout_seconds is not None and idle_timeout_seconds <= 0:
        raise ControlError("idle_timeout_seconds must be greater than zero")

    log_path = control.paths.engine_events_log
    offset = log_path.stat().st_size if start_at_end and log_path.exists() else 0
    last_activity = time.monotonic()

    while True:
        if log_path.exists():
            current_size = log_path.stat().st_size
            if current_size < offset:
                offset = 0
            with log_path.open("rb") as handle:
                handle.seek(offset)
                while True:
                    line = handle.readline()
                    if not line:
                        offset = handle.tell()
                        break
                    offset = handle.tell()
                    if not line.strip():
                        continue
                    last_activity = time.monotonic()
                    try:
                        yield EventRecord.model_validate_json(line.decode("utf-8"))
                    except (ValidationError, ValueError) as exc:
                        raise event_log_control_error(exc) from exc

        if idle_timeout_seconds is not None and (time.monotonic() - last_activity) >= idle_timeout_seconds:
            return
        time.sleep(poll_interval_seconds)
