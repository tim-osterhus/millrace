"""Pure view-shaping helpers used by the TUI runtime gateway."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from pydantic import ValidationError

from ..control import EngineControl
from ..control_common import expected_error_message, single_line_message, validation_error_message
from ..control_reports import read_run_provenance
from ..events import EventRecord
from ..publishing.staging import PublishCommitReport, PublishPreflightReport, StagingSyncReport
from .formatting import detail_items, runtime_event_view, stringify_value
from .models import (
    ActionResultView,
    CompoundingGovernanceOverviewView,
    ConfigFieldInputKind,
    ConfigFieldView,
    ConfigOverviewView,
    InterviewQuestionSummaryView,
    KeyValueView,
    PublishOverviewView,
    QueueOverviewView,
    QueueTaskView,
    ResearchAuditSummaryView,
    ResearchGovernanceOverviewView,
    ResearchOverviewView,
    ResearchQueueFamilyView,
    ResearchQueueItemView,
    RunCompoundingView,
    RunContextFactSelectionSummaryView,
    RunCreatedProcedureSummaryView,
    RunDetailView,
    RunIntegrationSummaryView,
    RunPolicyEvidenceView,
    RunProcedureSelectionSummaryView,
    RunsOverviewView,
    RunSummaryView,
    RunTransitionView,
    RuntimeOverviewView,
    SelectionDecisionView,
    SelectionSummaryView,
)

RECENT_RUN_LIMIT = 8


@dataclass(frozen=True, slots=True)
class ConfigFieldSpec:
    key: str
    label: str
    description: str
    input_kind: ConfigFieldInputKind | None = None
    options: tuple[str, ...] = ()
    minimum: int | None = None


CONFIG_FIELD_SPECS: tuple[ConfigFieldSpec, ...] = (
    ConfigFieldSpec(
        key="engine.poll_interval_seconds",
        label="Poll interval",
        description="Seconds between idle polls when filesystem watch mode is not active.",
        input_kind=ConfigFieldInputKind.INTEGER,
        minimum=1,
    ),
    ConfigFieldSpec(
        key="engine.inter_task_delay_seconds",
        label="Inter-task delay",
        description="Seconds the runtime waits between queued tasks in one cycle.",
        input_kind=ConfigFieldInputKind.INTEGER,
        minimum=0,
    ),
    ConfigFieldSpec(
        key="engine.idle_mode",
        label="Idle mode",
        description="Choose whether the daemon idles by filesystem watch or by polling.",
        input_kind=ConfigFieldInputKind.CHOICE,
        options=("watch", "poll"),
    ),
    ConfigFieldSpec(
        key="execution.integration_mode",
        label="Integration mode",
        description="Control when integration runs after builder success.",
        input_kind=ConfigFieldInputKind.CHOICE,
        options=("always", "large_only", "never"),
    ),
    ConfigFieldSpec(
        key="execution.run_update_on_empty",
        label="Run update on empty",
        description="Allow the maintenance update path when the execution backlog is empty.",
        input_kind=ConfigFieldInputKind.CHOICE,
        options=("true", "false"),
    ),
)

STARTUP_ONLY_FIELD_SPECS: tuple[ConfigFieldSpec, ...] = (
    ConfigFieldSpec(
        key="paths.workspace",
        label="Workspace root",
        description="Configured workspace root used to resolve all runtime-relative paths.",
    ),
    ConfigFieldSpec(
        key="paths.agents_dir",
        label="Agents directory",
        description="Runtime workspace subdirectory that contains queues, state, and artifacts.",
    ),
)


def registry_ref(value: object | None) -> str | None:
    if value is None:
        return None
    kind = getattr(value, "kind", None)
    identifier = getattr(value, "id", None)
    version = getattr(value, "version", None)
    if kind is not None and identifier is not None and version is not None:
        kind_value = getattr(kind, "value", kind)
        return f"{kind_value}:{identifier}@{version}"
    return stringify_value(value) or None


def selection_ref(view: object | None) -> str | None:
    if view is None:
        return None
    return registry_ref(getattr(view, "ref", None))


def task_view(task: object | None) -> QueueTaskView | None:
    if task is None:
        return None
    return QueueTaskView(
        task_id=str(getattr(task, "task_id")),
        title=str(getattr(task, "title")),
        spec_id=getattr(task, "spec_id"),
    )


def selection_summary_view(selection: object | None) -> SelectionSummaryView | None:
    if selection is None:
        return None
    return SelectionSummaryView(
        scope=str(getattr(selection, "scope")),
        selection_ref=registry_ref(getattr(getattr(selection, "selection"), "ref")) or "unknown",
        mode_ref=selection_ref(getattr(selection, "mode")),
        execution_loop_ref=selection_ref(getattr(selection, "execution_loop")),
        frozen_plan_id=str(getattr(selection, "frozen_plan_id")),
        frozen_plan_hash=str(getattr(selection, "frozen_plan_hash")),
        run_id=getattr(selection, "run_id"),
        research_participation=str(getattr(selection, "research_participation")),
        stage_labels=tuple(
            f"{binding.node_id}:{binding.kind_id}" if binding.kind_id is not None else str(binding.node_id)
            for binding in getattr(selection, "stage_bindings")
        ),
    )


def selection_decision_view(explanation: object | None) -> SelectionDecisionView | None:
    if explanation is None:
        return None
    return SelectionDecisionView(
        selected_size=str(getattr(explanation, "selected_size")),
        route_decision=str(getattr(explanation, "route_decision")),
        route_reason=str(getattr(explanation, "route_reason")),
        large_profile_decision=str(getattr(explanation, "large_profile_decision")),
        large_profile_reason=getattr(explanation, "large_profile_reason"),
    )


def stage_count(snapshot: object | None) -> int | None:
    if snapshot is None:
        return None
    execution_plan = getattr(getattr(snapshot, "content", None), "execution_plan", None)
    stages = getattr(execution_plan, "stages", None)
    if stages is None:
        return None
    return len(tuple(stages))


def research_item_view(item: object | None) -> ResearchQueueItemView | None:
    if item is None:
        return None
    source_status = getattr(item, "source_status")
    item_kind = getattr(item, "item_kind")
    return ResearchQueueItemView(
        family=str(getattr(getattr(item, "family"), "value", getattr(item, "family"))),
        item_key=str(getattr(item, "item_key")),
        title=str(getattr(item, "title")),
        item_kind=str(getattr(item_kind, "value", item_kind)),
        queue_path=Path(getattr(item, "queue_path")).as_posix(),
        item_path=(Path(item.item_path).as_posix() if getattr(item, "item_path") is not None else None),
        occurred_at=getattr(item, "occurred_at"),
        source_status=(str(getattr(source_status, "value", source_status)) if source_status is not None else None),
        stage_blocked=getattr(item, "stage_blocked"),
    )


def config_value(config: object, key: str) -> object:
    current = config
    for token in key.split("."):
        if isinstance(current, dict):
            current = current[token]
        else:
            current = getattr(current, token)
    return current


def runtime_overview_view(status: object) -> RuntimeOverviewView:
    runtime = getattr(status, "runtime")
    return RuntimeOverviewView(
        workspace_path=Path(getattr(status, "config_path")).parent.as_posix(),
        config_path=Path(getattr(status, "config_path")).as_posix(),
        config_source_kind=str(getattr(status, "config_source_kind")),
        source_kind=str(getattr(status, "source_kind")),
        process_running=bool(getattr(runtime, "process_running")),
        paused=bool(getattr(runtime, "paused")),
        pause_reason=getattr(runtime, "pause_reason"),
        pause_run_id=getattr(runtime, "pause_run_id"),
        mode=str(getattr(runtime, "mode")),
        execution_status=str(getattr(getattr(runtime, "execution_status"), "value", getattr(runtime, "execution_status"))),
        research_status=str(getattr(getattr(runtime, "research_status"), "value", getattr(runtime, "research_status"))),
        active_task_id=getattr(runtime, "active_task_id"),
        backlog_depth=int(getattr(runtime, "backlog_depth")),
        deferred_queue_size=int(getattr(runtime, "deferred_queue_size")),
        uptime_seconds=getattr(runtime, "uptime_seconds"),
        asset_bundle_version=getattr(runtime, "asset_bundle_version"),
        pending_config_hash=getattr(runtime, "pending_config_hash"),
        previous_config_hash=getattr(runtime, "previous_config_hash"),
        pending_config_boundary=(
            str(getattr(getattr(runtime, "pending_config_boundary"), "value", getattr(runtime, "pending_config_boundary")))
            if getattr(runtime, "pending_config_boundary") is not None
            else None
        ),
        pending_config_fields=tuple(getattr(runtime, "pending_config_fields")),
        rollback_armed=bool(getattr(runtime, "rollback_armed")),
        started_at=getattr(runtime, "started_at"),
        updated_at=getattr(runtime, "updated_at"),
        selection=selection_summary_view(getattr(status, "selection")),
        selection_decision=selection_decision_view(getattr(status, "selection_explanation")),
    )


def config_overview_view(report: object) -> ConfigOverviewView:
    config = getattr(report, "config")
    boundaries = getattr(config, "boundaries")
    source = getattr(report, "source")
    editing_enabled = str(getattr(source, "kind")) == "native_toml"

    def build_field(spec: ConfigFieldSpec, *, editable: bool) -> ConfigFieldView:
        boundary = getattr(boundaries, "classify_field")(spec.key)
        return ConfigFieldView(
            key=spec.key,
            label=spec.label,
            value=stringify_value(config_value(config, spec.key)) or "none",
            boundary=str(getattr(boundary, "value", boundary)),
            description=spec.description,
            editable=editable,
            input_kind=spec.input_kind if editable else None,
            options=spec.options if editable else (),
            minimum=spec.minimum if editable else None,
        )

    return ConfigOverviewView(
        config_path=Path(getattr(source, "primary_path")).as_posix(),
        source_kind=str(getattr(source, "kind")),
        source_ref=Path(getattr(source, "primary_path")).as_posix(),
        config_hash=str(getattr(report, "config_hash")),
        bundle_version=getattr(getattr(report, "assets"), "bundle_version", None),
        editing_enabled=editing_enabled,
        editing_disabled_reason=(
            None if editing_enabled else "guided edits are available only for native TOML configs"
        ),
        fields=tuple(build_field(spec, editable=editing_enabled) for spec in CONFIG_FIELD_SPECS),
        startup_only_fields=tuple(build_field(spec, editable=False) for spec in STARTUP_ONLY_FIELD_SPECS),
    )


def queue_overview_view(queue: object) -> QueueOverviewView:
    return QueueOverviewView(
        active_task=task_view(getattr(queue, "active_task")),
        next_task=task_view(getattr(queue, "next_task")),
        backlog_depth=int(getattr(queue, "backlog_depth")),
        backlog=tuple(task_view(task) for task in getattr(queue, "backlog") if task_view(task) is not None),
    )


def research_overview_view(
    report: object,
    *,
    recent_activity: Iterable[EventRecord] = (),
    interview_questions: Iterable[object] = (),
) -> ResearchOverviewView:
    runtime = getattr(report, "runtime")
    queue_snapshot = getattr(runtime, "queue_snapshot")
    latest_gate_decision = getattr(report, "latest_gate_decision")
    latest_completion_decision = getattr(report, "latest_completion_decision")
    completion_state = getattr(report, "completion_state")
    queue_families = tuple(
        ResearchQueueFamilyView(
            family=str(getattr(getattr(family, "family"), "value", getattr(family, "family"))),
            ready=bool(getattr(family, "ready")),
            item_count=int(getattr(family, "item_count")),
            queue_owner=(
                str(getattr(getattr(family, "queue_owner"), "value", getattr(family, "queue_owner")))
                if getattr(family, "queue_owner") is not None
                else None
            ),
            queue_paths=tuple(Path(path).as_posix() for path in getattr(family, "queue_paths")),
            contract_paths=tuple(Path(path).as_posix() for path in getattr(family, "contract_paths")),
            first_item=research_item_view(getattr(family, "first_item")),
        )
        for family in getattr(report, "queue_families")
    )
    return ResearchOverviewView(
        status=str(getattr(getattr(report, "status"), "value", getattr(report, "status"))),
        source_kind=str(getattr(report, "source_kind")),
        configured_mode=str(getattr(getattr(report, "configured_mode"), "value", getattr(report, "configured_mode"))),
        configured_idle_mode=str(getattr(report, "configured_idle_mode")),
        current_mode=str(getattr(getattr(runtime, "current_mode"), "value", getattr(runtime, "current_mode"))),
        last_mode=str(getattr(getattr(runtime, "last_mode"), "value", getattr(runtime, "last_mode"))),
        mode_reason=str(getattr(runtime, "mode_reason")),
        cycle_count=int(getattr(runtime, "cycle_count")),
        transition_count=int(getattr(runtime, "transition_count")),
        selected_family=(
            str(getattr(getattr(queue_snapshot, "selected_family"), "value", getattr(queue_snapshot, "selected_family")))
            if getattr(queue_snapshot, "selected_family") is not None
            else None
        ),
        deferred_breadcrumb_count=int(getattr(report, "deferred_breadcrumb_count")),
        deferred_request_count=len(getattr(runtime, "deferred_requests")),
        queue_families=queue_families,
        audit_summary_path=Path(getattr(report, "audit_summary_path")).as_posix(),
        audit_history_path=Path(getattr(report, "audit_history_path")).as_posix(),
        audit_summary_present=getattr(report, "audit_summary") is not None,
        latest_gate_decision=(getattr(latest_gate_decision, "decision") if latest_gate_decision is not None else None),
        latest_completion_decision=(
            getattr(latest_completion_decision, "decision") if latest_completion_decision is not None else None
        ),
        completion_allowed=bool(getattr(completion_state, "completion_allowed")),
        completion_reason=str(getattr(completion_state, "reason")),
        updated_at=getattr(runtime, "updated_at"),
        next_poll_at=getattr(runtime, "next_poll_at"),
        interview_questions=tuple(interview_question_summary_view(question) for question in interview_questions),
        audit_summary=research_audit_summary_view(report),
        governance=research_governance_view(report),
        recent_activity=tuple(runtime_event_view(record) for record in recent_activity),
    )


def interview_question_summary_view(question: object) -> InterviewQuestionSummaryView:
    return InterviewQuestionSummaryView(
        question_id=str(getattr(question, "question_id")),
        status=str(getattr(question, "status")),
        spec_id=str(getattr(question, "spec_id")),
        idea_id=str(getattr(question, "idea_id", "")),
        title=str(getattr(question, "title")),
        question=str(getattr(question, "question")),
        why_this_matters=str(getattr(question, "why_this_matters")),
        recommended_answer=str(getattr(question, "recommended_answer")),
        answer_source=str(getattr(question, "answer_source")),
        blocking=bool(getattr(question, "blocking")),
        source_path=Path(getattr(question, "source_path")).as_posix(),
        updated_at=getattr(question, "updated_at", None),
    )


def research_audit_summary_view(report: object) -> ResearchAuditSummaryView | None:
    summary = getattr(report, "audit_summary")
    remediation = getattr(report, "latest_audit_remediation")
    if summary is None and remediation is None:
        return None

    last_outcome = getattr(summary, "last_outcome", None) if summary is not None else None
    counts = getattr(summary, "counts", {}) if summary is not None else {}
    return ResearchAuditSummaryView(
        updated_at=(getattr(summary, "updated_at", None) if summary is not None else None),
        total_count=int(counts.get("total", 0)),
        pass_count=int(counts.get("pass", 0)),
        fail_count=int(counts.get("fail", 0)),
        last_status=str(getattr(last_outcome, "status", "none")),
        last_details=str(getattr(last_outcome, "details", "none")),
        last_at=getattr(last_outcome, "at", None),
        last_title=getattr(last_outcome, "title", None),
        last_decision=getattr(last_outcome, "decision", None),
        last_reason_count=int(getattr(last_outcome, "reason_count", 0) or 0),
        remediation_action=(
            str(getattr(getattr(remediation, "selected_action", None), "value", getattr(remediation, "selected_action")))
            if remediation is not None
            else None
        ),
        remediation_spec_id=(getattr(remediation, "remediation_spec_id", None) if remediation is not None else None),
        remediation_task_id=(getattr(remediation, "remediation_task_id", None) if remediation is not None else None),
        remediation_task_title=(
            getattr(remediation, "remediation_task_title", None) if remediation is not None else None
        ),
    )


def research_governance_view(report: object) -> ResearchGovernanceOverviewView | None:
    governance = getattr(report, "governance")
    if governance is None:
        return None

    queue_governor = getattr(governance, "queue_governor")
    drift = getattr(governance, "drift")
    canary = getattr(governance, "governance_canary")
    watchdog = getattr(governance, "progress_watchdog")
    regeneration = getattr(watchdog, "recovery_regeneration", None)
    view = ResearchGovernanceOverviewView(
        queue_governor_status=str(getattr(queue_governor, "status")),
        queue_governor_reason=str(getattr(queue_governor, "reason")),
        drift_status=str(getattr(drift, "status")),
        drift_reason=str(getattr(drift, "reason")),
        drift_fields=tuple(getattr(drift, "drift_fields")),
        canary_status=str(getattr(canary, "status")),
        canary_reason=str(getattr(canary, "reason")),
        canary_changed_fields=tuple(getattr(canary, "changed_fields")),
        recovery_status=str(getattr(watchdog, "status")),
        recovery_reason=str(getattr(watchdog, "reason")),
        recovery_batch_id=getattr(watchdog, "batch_id") or None,
        recovery_visible_task_count=int(getattr(watchdog, "visible_recovery_task_count", 0)),
        recovery_escalation_action=str(getattr(watchdog, "escalation_action")),
        recovery_regeneration_status=(
            str(getattr(regeneration, "status")) if regeneration is not None else None
        ),
        regenerated_task_id=(
            getattr(regeneration, "regenerated_task_id", None) or None if regeneration is not None else None
        ),
        regenerated_task_title=(
            getattr(regeneration, "regenerated_task_title", None) or None if regeneration is not None else None
        ),
    )
    if governance_has_signal(view):
        return view
    return None


def compounding_governance_view(report: object) -> CompoundingGovernanceOverviewView | None:
    if report is None:
        return None
    return CompoundingGovernanceOverviewView(
        pending_governance_items=int(getattr(report, "pending_governance_items", 0)),
        procedure_pending_review=int(getattr(report, "procedure_pending_review", 0)),
        context_fact_pending_review=int(getattr(report, "context_fact_pending_review", 0)),
        harness_candidate_pending_review=int(getattr(report, "harness_candidate_pending_review", 0)),
        recommendation_pending=int(getattr(report, "recommendation_pending", 0)),
        latest_recommendation_summary=getattr(report, "latest_recommendation_summary", None),
        recent_usage_run_id=getattr(report, "recent_usage_run_id", None),
        recent_usage_procedure_count=int(getattr(report, "recent_usage_procedure_count", 0)),
        recent_usage_context_fact_count=int(getattr(report, "recent_usage_context_fact_count", 0)),
    )


def governance_has_signal(view: ResearchGovernanceOverviewView) -> bool:
    return any(
        (
            view.queue_governor_status != "not_applicable",
            view.drift_status != "not_applicable",
            view.canary_status != "not_configured",
            view.recovery_status != "not_active",
            bool(view.drift_fields),
            bool(view.canary_changed_fields),
            bool(view.recovery_batch_id),
            bool(view.regenerated_task_id),
        )
    )


def publish_overview_view(report: PublishPreflightReport) -> PublishOverviewView:
    selection = report.selection
    return PublishOverviewView(
        staging_repo_dir=selection.staging_repo_dir.as_posix(),
        manifest_source_kind=str(selection.manifest_source_kind),
        manifest_source_ref=str(selection.manifest_source_ref),
        manifest_version=int(selection.manifest_version),
        selected_paths=tuple(selection.selected_paths),
        branch=report.branch,
        commit_message=report.commit_message,
        push_requested=bool(report.push_requested),
        git_worktree_present=bool(report.git_worktree_present),
        git_worktree_valid=bool(report.git_worktree_valid),
        origin_configured=bool(report.origin_configured),
        has_changes=bool(report.has_changes),
        changed_paths=tuple(report.changed_paths),
        commit_allowed=bool(report.commit_allowed),
        publish_allowed=bool(report.publish_allowed),
        status=str(report.status),
        skip_reason=report.skip_reason,
    )


def runs_overview_view(
    control: EngineControl,
    *,
    observed_at: datetime,
    read_provenance: Callable[[Path], object | None] = read_run_provenance,
) -> RunsOverviewView:
    runs_dir = control.paths.runs_dir
    runs: list[RunSummaryView] = []
    if runs_dir.exists():
        run_dirs = [
            path
            for path in runs_dir.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        ]
        for run_dir in sorted(run_dirs, key=run_sort_key, reverse=True)[:RECENT_RUN_LIMIT]:
            runs.append(run_summary_view(run_dir, read_provenance=read_provenance))
    return RunsOverviewView(
        runs_dir=runs_dir.as_posix(),
        scanned_at=observed_at,
        runs=tuple(runs),
    )


def run_summary_view(
    run_dir: Path,
    *,
    read_provenance: Callable[[Path], object | None] = read_run_provenance,
) -> RunSummaryView:
    snapshot_path = run_dir / "resolved_snapshot.json"
    history_path = run_dir / "transition_history.jsonl"
    try:
        report = read_provenance(run_dir)
    except ValidationError as exc:
        return RunSummaryView(
            run_id=run_dir.name,
            snapshot_present=snapshot_path.exists(),
            history_present=history_path.exists(),
            issue=f"invalid provenance: {validation_error_message(exc)}",
        )
    except ValueError as exc:
        return RunSummaryView(
            run_id=run_dir.name,
            snapshot_present=snapshot_path.exists(),
            history_present=history_path.exists(),
            issue=f"invalid provenance: {single_line_message(exc)}",
        )
    except OSError as exc:
        return RunSummaryView(
            run_id=run_dir.name,
            snapshot_present=snapshot_path.exists(),
            history_present=history_path.exists(),
            issue=expected_error_message(exc) or "could not read run artifacts",
        )
    if report is None:
        return RunSummaryView(
            run_id=run_dir.name,
            snapshot_present=snapshot_path.exists(),
            history_present=history_path.exists(),
            note="run provenance artifacts missing",
        )

    compile_snapshot = getattr(report, "compile_snapshot")
    runtime_history = tuple(getattr(report, "runtime_history"))
    latest_transition = runtime_history[-1] if runtime_history else None
    frozen_plan = (
        getattr(compile_snapshot, "frozen_plan", None)
        if compile_snapshot is not None
        else getattr(latest_transition, "frozen_plan", None)
    )
    latest_policy_evidence = getattr(report, "latest_policy_evidence")
    integration_policy = getattr(report, "integration_policy")
    routing_modes = tuple(report.expected_routing_modes())
    note: str | None = None
    if compile_snapshot is None:
        note = "resolved snapshot missing"
    elif not runtime_history:
        note = "transition history not present"
    return RunSummaryView(
        run_id=str(getattr(report, "run_id")),
        compiled_at=(getattr(compile_snapshot, "created_at", None) if compile_snapshot is not None else None),
        selection_ref=(
            registry_ref(getattr(compile_snapshot, "selection_ref", None))
            if compile_snapshot is not None
            else registry_ref(getattr(frozen_plan, "selection_ref", None))
        ),
        frozen_plan_id=(getattr(frozen_plan, "plan_id", None) if frozen_plan is not None else None),
        frozen_plan_hash=(getattr(frozen_plan, "content_hash", None) if frozen_plan is not None else None),
        stage_count=stage_count(compile_snapshot),
        transition_count=len(runtime_history),
        latest_transition_at=(getattr(latest_transition, "timestamp", None) if latest_transition is not None else None),
        latest_transition_label=(
            f"{getattr(latest_transition, 'node_id')} {getattr(latest_transition, 'outcome') or getattr(latest_transition, 'event_name')}"
            if latest_transition is not None
            else None
        ),
        latest_status=(getattr(latest_transition, "status_after", None) if latest_transition is not None else None),
        routing_modes=routing_modes,
        latest_policy_decision=(
            getattr(latest_policy_evidence, "decision", None)
            if latest_policy_evidence is not None
            else getattr(getattr(report, "policy_hooks"), "latest_decision", None)
        ),
        integration_target=(
            getattr(integration_policy, "builder_success_target", None) if integration_policy is not None else None
        ),
        integration_enabled=(
            bool(getattr(integration_policy, "should_run_integration")) if integration_policy is not None else None
        ),
        snapshot_present=snapshot_path.exists(),
        history_present=history_path.exists(),
        note=note,
    )


def run_sort_key(run_dir: Path) -> float:
    timestamps = [run_dir.stat().st_mtime]
    for artifact_name in ("resolved_snapshot.json", "transition_history.jsonl", "frozen_run_plan.json"):
        artifact_path = run_dir / artifact_name
        if artifact_path.exists():
            timestamps.append(artifact_path.stat().st_mtime)
    return max(timestamps)


def policy_evidence_view(evidence: object | None) -> RunPolicyEvidenceView | None:
    if evidence is None:
        return None
    return RunPolicyEvidenceView(
        hook=str(getattr(evidence, "hook")),
        evaluator=str(getattr(evidence, "evaluator")),
        decision=str(getattr(evidence, "decision")),
        timestamp=getattr(evidence, "timestamp"),
        event_name=str(getattr(evidence, "event_name")),
        node_id=str(getattr(evidence, "node_id")),
        routing_mode=getattr(evidence, "routing_mode"),
        notes=tuple(getattr(evidence, "notes")),
        evidence_summaries=tuple(item.summary for item in getattr(evidence, "evidence")),
    )


def integration_policy_view(integration: object | None) -> RunIntegrationSummaryView | None:
    if integration is None:
        return None
    return RunIntegrationSummaryView(
        effective_mode=str(getattr(integration, "effective_mode")),
        builder_success_target=str(getattr(integration, "builder_success_target")),
        should_run_integration=bool(getattr(integration, "should_run_integration")),
        task_gate_required=bool(getattr(integration, "task_gate_required")),
        task_integration_preference=getattr(integration, "task_integration_preference"),
        requested_sequence=tuple(str(item) for item in getattr(integration, "requested_sequence")),
        effective_sequence=tuple(str(item) for item in getattr(integration, "effective_sequence")),
        available_execution_nodes=tuple(getattr(integration, "available_execution_nodes")),
        reason=str(getattr(integration, "reason")),
    )


def run_compounding_view(report: object | None) -> RunCompoundingView | None:
    if report is None:
        return None
    created_procedures = tuple(
        RunCreatedProcedureSummaryView(
            procedure_id=str(getattr(item, "procedure_id")),
            scope=str(getattr(getattr(item, "scope"), "value", getattr(item, "scope"))),
            source_stage=str(getattr(item, "source_stage")),
            title=str(getattr(item, "title")),
        )
        for item in getattr(report, "created_procedures")
    )
    procedure_selections = tuple(
        RunProcedureSelectionSummaryView(
            stage=str(getattr(item, "stage")),
            node_id=str(getattr(item, "node_id")),
            considered_count=int(getattr(item, "considered_count", 0)),
            injected_count=int(getattr(item, "injected_count", 0)),
            injected_ids=tuple(str(getattr(procedure, "procedure_id")) for procedure in getattr(item, "injected_procedures")),
        )
        for item in getattr(report, "procedure_selections")
    )
    context_fact_selections = tuple(
        RunContextFactSelectionSummaryView(
            stage=str(getattr(item, "stage")),
            node_id=str(getattr(item, "node_id")),
            considered_count=int(getattr(item, "considered_count", 0)),
            injected_count=int(getattr(item, "injected_count", 0)),
            injected_ids=tuple(str(getattr(fact, "fact_id")) for fact in getattr(item, "injected_facts")),
        )
        for item in getattr(report, "context_fact_selections")
    )
    return RunCompoundingView(
        created_count=int(getattr(report, "created_count", len(created_procedures))),
        procedure_selection_count=int(getattr(report, "selection_count", len(procedure_selections))),
        context_fact_selection_count=int(getattr(report, "fact_selection_count", len(context_fact_selections))),
        injected_procedure_count=int(
            getattr(report, "injected_procedure_count", sum(item.injected_count for item in procedure_selections))
        ),
        injected_context_fact_count=int(
            getattr(report, "injected_fact_count", sum(item.injected_count for item in context_fact_selections))
        ),
        created_procedures=created_procedures,
        procedure_selections=procedure_selections,
        context_fact_selections=context_fact_selections,
    )


def run_detail_view(report: object) -> RunDetailView:
    policy_hooks = getattr(report, "policy_hooks")
    compile_snapshot = getattr(report, "compile_snapshot")
    runtime_history = tuple(getattr(report, "runtime_history"))
    frozen_plan = (
        getattr(compile_snapshot, "frozen_plan", None)
        if compile_snapshot is not None
        else getattr(runtime_history[-1], "frozen_plan", None) if runtime_history else None
    )
    transitions = tuple(
        RunTransitionView(
            event_id=str(getattr(record, "event_id")),
            timestamp=getattr(record, "timestamp"),
            observed_timestamp=getattr(record, "observed_timestamp"),
            event_name=str(getattr(record, "event_name")),
            source=str(getattr(record, "source")),
            plane=str(getattr(getattr(record, "plane"), "value", getattr(record, "plane"))),
            node_id=str(getattr(record, "node_id")),
            kind_id=getattr(record, "kind_id"),
            outcome=getattr(record, "outcome"),
            status_before=getattr(record, "status_before"),
            status_after=getattr(record, "status_after"),
            active_task_before=getattr(record, "active_task_before"),
            active_task_after=getattr(record, "active_task_after"),
            routing_mode=getattr(record, "routing_mode"),
            queue_mutations_applied=tuple(getattr(record, "queue_mutations_applied")),
            artifacts_emitted=tuple(getattr(record, "artifacts_emitted")),
        )
        for record in runtime_history
    )
    return RunDetailView(
        run_id=str(getattr(report, "run_id")),
        compiled_at=(getattr(compile_snapshot, "created_at", None) if compile_snapshot is not None else None),
        frozen_plan_id=(getattr(frozen_plan, "plan_id", None) if frozen_plan is not None else None),
        frozen_plan_hash=(getattr(frozen_plan, "content_hash", None) if frozen_plan is not None else None),
        stage_count=stage_count(compile_snapshot),
        selection=selection_summary_view(getattr(report, "selection")),
        selection_decision=selection_decision_view(getattr(report, "selection_explanation")),
        current_preview=selection_summary_view(getattr(report, "current_preview")),
        current_preview_decision=selection_decision_view(getattr(report, "current_preview_explanation")),
        current_preview_error=getattr(report, "current_preview_error"),
        routing_modes=tuple(getattr(report, "routing_modes")),
        snapshot_path=(
            Path(getattr(report, "snapshot_path")).as_posix() if getattr(report, "snapshot_path") is not None else None
        ),
        transition_history_path=(
            Path(getattr(report, "transition_history_path")).as_posix()
            if getattr(report, "transition_history_path") is not None
            else None
        ),
        policy_hook_count=(int(getattr(policy_hooks, "record_count")) if policy_hooks is not None else 0),
        latest_policy_decision=(getattr(policy_hooks, "latest_decision") if policy_hooks is not None else None),
        latest_policy_evidence=policy_evidence_view(getattr(report, "latest_policy_evidence")),
        integration_policy=integration_policy_view(getattr(report, "integration_policy")),
        compounding=run_compounding_view(getattr(report, "compounding")),
        transitions=transitions,
    )


def action_result_view(action: str, result: object) -> ActionResultView:
    return ActionResultView(
        action=action,
        message=str(getattr(result, "message")),
        applied=bool(getattr(result, "applied")),
        mode=str(getattr(result, "mode")),
        command_id=getattr(result, "command_id"),
        details=detail_items(dict(getattr(result, "payload"))),
    )


def interview_action_result_view(action: str, report: object) -> ActionResultView:
    question = getattr(report, "question")
    decision = getattr(report, "decision", None)
    details = [
        KeyValueView("question_id", str(getattr(question, "question_id"))),
        KeyValueView("status", str(getattr(question, "status"))),
        KeyValueView("spec_id", str(getattr(question, "spec_id"))),
    ]
    if decision is not None:
        details.append(KeyValueView("decision_source", str(getattr(decision, "decision_source"))))
    messages = {
        "answer": "interview answer recorded",
        "accept": "interview recommendation accepted",
        "skip": "interview question skipped",
    }
    return ActionResultView(
        action=f"interview_{action}",
        message=messages.get(action, "interview updated"),
        applied=True,
        details=tuple(details),
    )


def sync_action_result_view(report: StagingSyncReport) -> ActionResultView:
    return ActionResultView(
        action="publish_sync",
        message="staging synchronized",
        applied=True,
        details=(
            KeyValueView("staging_repo_dir", report.selection.staging_repo_dir.as_posix()),
            KeyValueView("entry_count", str(len(report.entries))),
            KeyValueView("created_staging_dir", "true" if report.created_staging_dir else "false"),
        ),
    )


def commit_action_result_view(report: PublishCommitReport) -> ActionResultView:
    if report.status == "pushed":
        message = "publish committed and pushed"
    elif report.status == "committed":
        if report.push_requested and not report.push_performed:
            if report.skip_reason == "missing_origin":
                message = "publish committed locally; push skipped: origin is missing"
            elif report.skip_reason == "detached_head":
                message = "publish committed locally; push skipped: HEAD is detached"
            else:
                message = "publish committed locally; push skipped"
        else:
            message = "publish committed"
    elif report.status == "no_changes":
        message = "publish skipped: no changes"
    else:
        message = report.skip_reason or "publish skipped"
    details = [
        KeyValueView("status", str(report.status)),
        KeyValueView("marker", str(report.marker)),
    ]
    if report.branch:
        details.append(KeyValueView("branch", report.branch))
    if report.commit_sha:
        details.append(KeyValueView("commit_sha", report.commit_sha))
    return ActionResultView(
        action="publish_commit",
        message=message,
        applied=report.status in {"committed", "pushed"},
        details=tuple(details),
    )


__all__ = [
    "action_result_view",
    "commit_action_result_view",
    "config_overview_view",
    "interview_action_result_view",
    "publish_overview_view",
    "queue_overview_view",
    "research_overview_view",
    "run_detail_view",
    "runs_overview_view",
    "runtime_overview_view",
    "sync_action_result_view",
]
