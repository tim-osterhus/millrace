"""Control API facade and runtime-state helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal
import time

from pydantic import ValidationError

from .adapters.control_mailbox import ControlCommand, write_command
from .config import LoadedConfig, build_runtime_paths
from .contracts import AuditGateDecision, CompletionDecision, ExecutionStatus, ResearchStatus
from .control_actions import (
    add_idea as add_idea_operation,
    add_task as add_task_operation,
    lifecycle_action,
    normalize_supervisor_issuer,
    operation_with_payload_value,
    queue_cleanup_quarantine as queue_cleanup_quarantine_operation,
    queue_cleanup_remove as queue_cleanup_remove_operation,
    queue_reorder as queue_reorder_operation,
    supervisor_add_task as supervisor_add_task_operation,
    supervisor_queue_cleanup_quarantine as supervisor_queue_cleanup_quarantine_operation,
    supervisor_queue_cleanup_remove as supervisor_queue_cleanup_remove_operation,
    supervisor_lifecycle_action,
    supervisor_queue_reorder as supervisor_queue_reorder_operation,
)
from .control_common import (
    ControlError,
    event_log_control_error,
    expected_error_message,
    load_control_config,
    queue_control_error,
    single_line_message,
    validation_error_message,
)
from .control_models import (
    AssetFamilyEntryView,
    AssetInventoryView,
    AssetResolutionView,
    CompletionStateView,
    ConfigShowReport,
    InterviewListReport,
    InterviewMutationReport,
    InterviewQuestionReport,
    InterviewQuestionSummary,
    OperationResult,
    PolicyHookSummary,
    QueueItemView,
    QueueSnapshot,
    ResearchQueueFamilyView,
    ResearchReport,
    RunProvenanceReport,
    RuntimeState,
    SelectionExplanationView,
    StatusReport,
    SupervisorAction,
    SupervisorAttentionReason,
    SupervisorReport,
)
from .control_interview import (
    interview_accept as interview_accept_operation,
    interview_answer as interview_answer_operation,
    interview_create as interview_create_operation,
    interview_list as interview_list_operation,
    interview_show as interview_show_operation,
    interview_skip as interview_skip_operation,
)
from .control_mutations import apply_native_config_value
from .control_publish import (
    publish_commit as publish_commit_operation,
    publish_preflight as publish_preflight_operation,
    publish_sync as publish_sync_operation,
)
from .control_reports import (
    asset_inventory_for,
    build_live_runtime_state,
    completion_state_view,
    config_hash,
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
    task_view,
    write_runtime_state,
)
from .events import EventRecord, EventType, is_research_event_type
from .engine_runtime import start_engine
from .health import WorkspaceHealthReport, build_workspace_health_report
from .paths import RuntimePaths
from .policies import (
    ExecutionIntegrationSnapshot,
    resolve_execution_integration_context,
)
from .publishing import (
    PublishCommitReport,
    PublishPreflightReport,
    StagingPublishError,
    StagingSyncReport,
    commit_staging_repo,
    preflight_staging_publish,
    sync_staging_repo,
)
from .queue import QueueError, TaskQueue
from .research.audit import load_audit_remediation_record, load_audit_summary
from .research.governance import ResearchGovernanceReport, build_research_governance_report
from .research.interview import InterviewError, list_interview_questions
from .research.queues import discover_research_queues
from .research.state import ResearchQueueFamily, ResearchQueueOwnership, ResearchRuntimeState
from .standard_runtime import RuntimeSelectionView, runtime_selection_view_from_snapshot
from .status import ControlPlane, StatusError, StatusStore
from .workspace_init import (
    PersistedStateMigrationApplyReport,
    PersistedStateMigrationPreviewReport,
    WorkspaceUpgradeApplyReport,
    WorkspaceInitError,
    WorkspaceInitReport,
    WorkspaceUpgradePreviewReport,
    apply_workspace_upgrade,
    initialize_workspace,
    preview_workspace_upgrade,
)


_decision_report_paths = decision_report_paths


def _persisted_state_migration_preview_payload(
    report: PersistedStateMigrationPreviewReport,
) -> dict[str, object]:
    return {
        "state_family": report.state_family,
        "action": report.action,
        "state_path": report.state_path.as_posix(),
        "deferred_dir": report.deferred_dir.as_posix(),
        "breadcrumb_file_count": report.breadcrumb_file_count,
        "would_write_state_file": report.would_write_state_file,
        "summary": report.summary,
    }


def _persisted_state_migration_apply_payload(
    report: PersistedStateMigrationApplyReport,
) -> dict[str, object]:
    return {
        "state_family": report.state_family,
        "action": report.action,
        "state_path": report.state_path.as_posix(),
        "deferred_dir": report.deferred_dir.as_posix(),
        "breadcrumb_file_count": report.breadcrumb_file_count,
        "wrote_state_file": report.wrote_state_file,
        "summary": report.summary,
    }


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


class EngineControl:
    """Thin control API for CLI and tests."""

    @classmethod
    def init_workspace(cls, destination: Path | str, *, force: bool = False) -> OperationResult:
        """Initialize one workspace from the packaged baseline bundle."""

        try:
            report = initialize_workspace(destination, force=force)
        except WorkspaceInitError as exc:
            raise ControlError(str(exc)) from exc
        return cls._workspace_init_result(report)

    @classmethod
    def health_report(cls, config_path: Path | str = "millrace.toml") -> WorkspaceHealthReport:
        """Build a deterministic workspace health report without requiring a valid control instance."""

        try:
            return build_workspace_health_report(config_path)
        except RuntimeError as exc:
            raise ControlError(single_line_message(exc) or "workspace health report failed") from exc

    def __init__(self, config_path: Path | str = "millrace.toml") -> None:
        self.config_path = Path(config_path).expanduser().resolve()
        self.loaded = load_control_config(self.config_path)
        self.paths = build_runtime_paths(self.loaded.config)

    def preview_workspace_upgrade(self) -> OperationResult:
        """Return a non-mutating preview of manifest-tracked workspace upgrade impact."""

        try:
            report = preview_workspace_upgrade(self.paths.root)
        except WorkspaceInitError as exc:
            raise ControlError(str(exc)) from exc
        return self._workspace_upgrade_preview_result(report)

    def apply_workspace_upgrade(self) -> OperationResult:
        """Apply a manifest-tracked baseline refresh without resetting preserved files."""

        try:
            report = apply_workspace_upgrade(self.paths.root)
        except WorkspaceInitError as exc:
            raise ControlError(str(exc)) from exc
        return self._workspace_upgrade_apply_result(report)

    @staticmethod
    def _workspace_init_result(report: WorkspaceInitReport) -> OperationResult:
        return OperationResult(
            mode="direct",
            applied=True,
            message="workspace initialized",
            payload={
                "workspace_root": report.workspace_root.as_posix(),
                "bundle_version": report.bundle_version,
                "created_file_count": report.created_file_count,
                "overwritten_file_count": report.overwritten_file_count,
                "created_directory_count": report.created_directory_count,
            },
        )

    @staticmethod
    def _workspace_upgrade_preview_result(report: WorkspaceUpgradePreviewReport) -> OperationResult:
        return OperationResult(
            mode="direct",
            applied=False,
            message="workspace upgrade preview generated",
            payload={
                "workspace_root": report.workspace_root.as_posix(),
                "bundle_version": report.bundle_version,
                "manifest_file_count": report.manifest_file_count,
                "manifest_directory_count": report.manifest_directory_count,
                "would_create": report.would_create,
                "would_update": report.would_update,
                "unchanged": report.unchanged,
                "conflicting_paths": report.conflicting_paths,
                "preserved_runtime_owned": report.preserved_runtime_owned,
                "preserved_operator_owned": report.preserved_operator_owned,
                "persisted_state_migrations": tuple(
                    _persisted_state_migration_preview_payload(item)
                    for item in report.persisted_state_migrations
                ),
            },
        )

    @staticmethod
    def _workspace_upgrade_apply_result(report: WorkspaceUpgradeApplyReport) -> OperationResult:
        return OperationResult(
            mode="direct",
            applied=True,
            message="workspace upgrade applied",
            payload={
                "workspace_root": report.workspace_root.as_posix(),
                "bundle_version": report.bundle_version,
                "manifest_file_count": report.manifest_file_count,
                "manifest_directory_count": report.manifest_directory_count,
                "created_directory_count": report.created_directory_count,
                "created_file_count": report.created_file_count,
                "updated_file_count": report.updated_file_count,
                "created_files": report.created_files,
                "updated_files": report.updated_files,
                "unchanged": report.unchanged,
                "conflicting_paths": report.conflicting_paths,
                "preserved_runtime_owned": report.preserved_runtime_owned,
                "preserved_operator_owned": report.preserved_operator_owned,
                "persisted_state_migrations": tuple(
                    _persisted_state_migration_apply_payload(item)
                    for item in report.persisted_state_migrations
                ),
            },
        )

    @property
    def state_path(self) -> Path:
        return self.paths.runtime_dir / "state.json"

    @staticmethod
    def _normalize_supervisor_issuer(issuer: str) -> str:
        return normalize_supervisor_issuer(issuer)

    @staticmethod
    def _operation_with_payload_value(operation: OperationResult, *, key: str, value: object) -> OperationResult:
        return operation_with_payload_value(operation, key=key, value=value)

    def reload_local_config(self) -> LoadedConfig:
        """Reload config from disk and refresh cached paths."""

        self.loaded = load_control_config(self.config_path)
        self.paths = build_runtime_paths(self.loaded.config)
        return self.loaded

    def health(self) -> WorkspaceHealthReport:
        """Return the deterministic workspace health report for this control root."""

        return self.health_report(self.config_path)

    def is_daemon_running(self) -> bool:
        """Return True when the persisted runtime snapshot says the daemon is active."""

        state = read_runtime_state(self.state_path)
        return bool(state is not None and state.process_running)

    def start(self, *, daemon: bool = False, once: bool = False) -> RuntimeState:
        """Start the engine in foreground once or daemon mode."""
        return start_engine(self.config_path, daemon=daemon, once=once)

    def status(self, *, detail: bool = False) -> StatusReport:
        """Return the current runtime status."""

        active_task = TaskQueue(self.paths).active_task()
        size = size_status_view(self.loaded, task=active_task)
        snapshot = read_control_runtime_state(self.state_path)
        if snapshot is None:
            try:
                runtime = build_live_runtime_state(
                    self.loaded,
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
            self.loaded,
            size=size,
            current_status=runtime.execution_status,
        )
        queue = self.queue_inspect() if detail else self.queue()
        return StatusReport.model_validate(
            {
                "runtime": runtime,
                "source_kind": source_kind,
                "config_path": self.config_path,
                "config_source_kind": self.loaded.source.kind,
                "config_source": self.loaded.source,
                "selection": selection,
                "selection_explanation": selection_explanation(
                    size=size,
                    current_status=runtime.execution_status,
                    selection=selection,
                ),
                "size": size,
                "integration_policy": resolve_execution_integration_context(
                    ExecutionIntegrationSnapshot.from_config(self.loaded.config),
                    task=active_task,
                    policy_toggle_integration_mode=(
                        selection.policy_toggles.integration_mode
                        if selection.policy_toggles is not None
                        else None
                    ),
                    execution_node_ids=tuple(binding.node_id for binding in selection.stage_bindings),
                ),
                "assets": asset_inventory_for(self.loaded) if detail else None,
                "research": self.research_report() if detail else None,
                "active_task": queue.active_task if detail else None,
                "next_task": queue.next_task if detail else None,
            }
        )

    def supervisor_report(self, *, recent_event_limit: int = 10) -> SupervisorReport:
        """Return one aggregated external-supervisor report for this workspace."""

        if recent_event_limit < 0:
            raise ControlError("supervisor_report requires a non-negative recent event limit")

        generated_at = datetime.now(timezone.utc)
        health = self.health()
        status = self.status(detail=True)
        research = status.research
        if research is None:
            raise ControlError("supervisor report requires detailed research visibility")

        recent_events = tuple(self.logs(n=recent_event_limit))
        try:
            pending_blocking_interview = any(
                question.status == "pending" and question.blocking
                for question in list_interview_questions(self.paths)
            )
        except (InterviewError, ValidationError, ValueError) as exc:
            raise ControlError(f"supervisor report failed: {expected_error_message(exc)}") from exc

        attention_reason, attention_summary = _attention_reason_for(
            health=health,
            status=status,
            pending_blocking_interview=pending_blocking_interview,
        )
        current_run_id, current_stage, time_in_current_status_seconds = _derive_supervisor_run_context(
            recent_events,
            runtime=status.runtime,
            research_status=research.status,
            generated_at=generated_at,
        )

        return SupervisorReport(
            workspace_root=self.paths.root,
            config_path=self.config_path,
            generated_at=generated_at,
            health_status=health.status,
            health_summary=health.summary,
            bootstrap_ready=health.bootstrap_ready,
            execution_ready=health.execution_ready,
            process_running=status.runtime.process_running,
            paused=status.runtime.paused,
            execution_status=status.runtime.execution_status,
            research_status=research.status,
            status_source_kind=status.source_kind,
            research_source_kind=research.source_kind,
            active_task=status.active_task,
            next_task=status.next_task,
            backlog_depth=status.runtime.backlog_depth,
            deferred_queue_size=status.runtime.deferred_queue_size,
            current_run_id=current_run_id,
            current_stage=current_stage,
            time_in_current_status_seconds=time_in_current_status_seconds,
            attention_reason=attention_reason,
            attention_summary=attention_summary,
            allowed_actions=_supervisor_allowed_actions(
                status.runtime,
                backlog_depth=status.runtime.backlog_depth,
                active_task_present=status.active_task is not None,
            ),
            recent_events=recent_events,
        )

    def run_provenance(self, run_id: str) -> RunProvenanceReport:
        """Return the compile-time snapshot plus runtime transition history for one run."""

        normalized_run_id = run_id.strip()
        if not normalized_run_id:
            raise ControlError("run_provenance requires a run_id")
        try:
            report = read_run_provenance(self.paths.runs_dir / normalized_run_id)
        except ValidationError as exc:
            raise ControlError(f"run provenance is invalid: {validation_error_message(exc)}") from exc
        except ValueError as exc:
            raise ControlError(f"run provenance is invalid: {single_line_message(exc)}") from exc
        if report is None:
            raise ControlError(f"run provenance not found: {normalized_run_id}")
        try:
            selection = (
                runtime_selection_view_from_snapshot(report.compile_snapshot, workspace_root=self.paths.root)
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
            active_task = TaskQueue(self.paths).active_task()
            current_status = StatusStore(self.paths.status_file, ControlPlane.EXECUTION).read()
            size = size_status_view(self.loaded, task=active_task)
            current_preview = selection_preview_for(
                self.loaded,
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
                    snapshot_selection_explanation(selection)
                    if selection is not None
                    else None
                ),
                current_preview=current_preview,
                current_preview_explanation=current_preview_explanation,
                current_preview_error=current_preview_error,
                routing_modes=routing_modes,
            )
        except ValidationError as exc:
            raise ControlError(f"run provenance is invalid: {validation_error_message(exc)}") from exc

    def config_show(self) -> ConfigShowReport:
        """Return the current config payload for rendering."""

        active_task = TaskQueue(self.paths).active_task()
        size = size_status_view(self.loaded, task=active_task)
        current_status = StatusStore(self.paths.status_file, ControlPlane.EXECUTION).read()
        if not isinstance(current_status, ExecutionStatus):
            raise ControlError("execution status could not be read")
        selection = selection_preview_for(
            self.loaded,
            size=size,
            current_status=current_status,
        )
        return ConfigShowReport(
            source=self.loaded.source,
            config=self.loaded.config,
            config_hash=config_hash(self.loaded.config),
            selection=selection,
            selection_explanation=selection_explanation(
                size=size,
                current_status=current_status,
                selection=selection,
            ),
            assets=asset_inventory_for(self.loaded),
        )

    def config_reload(self) -> OperationResult:
        """Reload config directly or by mailbox."""

        if self.is_daemon_running():
            envelope = write_command(self.paths, ControlCommand.RELOAD_CONFIG)
            return OperationResult(
                command_id=envelope.command_id,
                mode="mailbox",
                applied=True,
                message="reload_config queued",
            )
        reloaded = self.reload_local_config()
        return OperationResult(
            mode="direct",
            applied=True,
            message="config reloaded",
            payload={"config_hash": config_hash(reloaded.config)},
        )

    def config_set(self, key: str, value: str) -> OperationResult:
        """Persist one config mutation."""

        if self.is_daemon_running():
            envelope = write_command(
                self.paths,
                ControlCommand.SET_CONFIG,
                payload={"key": key, "value": value},
            )
            return OperationResult(
                command_id=envelope.command_id,
                mode="mailbox",
                applied=True,
                message="set_config queued",
                payload={"key": key},
            )

        self.loaded = apply_native_config_value(
            self.config_path,
            self.loaded,
            key,
            value,
            reject_startup_only=False,
        )
        self.paths = build_runtime_paths(self.loaded.config)
        return OperationResult(
            mode="direct",
            applied=True,
            message="config updated",
            payload={"key": key, "config_hash": config_hash(self.loaded.config)},
        )

    def queue(self) -> QueueSnapshot:
        """Return queue summary without full backlog detail."""

        queue = TaskQueue(self.paths)
        try:
            return QueueSnapshot(
                active_task=task_view(queue.active_task()),
                backlog_depth=queue.backlog_depth(),
                next_task=task_view(queue.peek_next()),
            )
        except (FileNotFoundError, QueueError, ValidationError, ValueError) as exc:
            raise queue_control_error(exc, prefix="queue state could not be read") from exc

    def queue_inspect(self) -> QueueSnapshot:
        """Return queue summary with full backlog titles."""

        queue = TaskQueue(self.paths)
        try:
            from .markdown import parse_task_store

            backlog = parse_task_store(
                self.paths.backlog_file.read_text(encoding="utf-8"),
                source_file=self.paths.backlog_file,
            ).cards
            return QueueSnapshot(
                active_task=task_view(queue.active_task()),
                backlog_depth=len(backlog),
                next_task=task_view(backlog[0] if backlog else None),
                backlog=tuple(task_view(card) for card in backlog if task_view(card) is not None),
            )
        except (FileNotFoundError, QueueError, ValidationError, ValueError) as exc:
            raise queue_control_error(exc, prefix="queue state could not be read") from exc

    def queue_reorder(self, task_ids: list[str] | tuple[str, ...]) -> OperationResult:
        """Rewrite the backlog order exactly as requested."""

        return queue_reorder_operation(
            self.paths,
            task_ids=task_ids,
            daemon_running=self.is_daemon_running(),
        )

    def queue_cleanup_remove(self, task_id: str, *, reason: str) -> OperationResult:
        """Remove one visible queued task through the local cleanup path."""

        return queue_cleanup_remove_operation(
            self.paths,
            task_id=task_id,
            reason=reason,
            daemon_running=self.is_daemon_running(),
        )

    def queue_cleanup_quarantine(self, task_id: str, *, reason: str) -> OperationResult:
        """Quarantine one visible queued task through the local cleanup path."""

        return queue_cleanup_quarantine_operation(
            self.paths,
            task_id=task_id,
            reason=reason,
            daemon_running=self.is_daemon_running(),
        )

    def supervisor_queue_reorder(self, task_ids: list[str] | tuple[str, ...], *, issuer: str) -> OperationResult:
        """Rewrite backlog order through the supervisor-safe mutation path."""

        return supervisor_queue_reorder_operation(
            self.paths,
            task_ids=task_ids,
            issuer=issuer,
            daemon_running=self.is_daemon_running(),
        )

    def supervisor_queue_cleanup_remove(self, task_id: str, *, reason: str, issuer: str) -> OperationResult:
        """Remove one visible queued task through the supervisor-safe cleanup path."""

        return supervisor_queue_cleanup_remove_operation(
            self.paths,
            task_id=task_id,
            reason=reason,
            issuer=issuer,
            daemon_running=self.is_daemon_running(),
        )

    def supervisor_queue_cleanup_quarantine(self, task_id: str, *, reason: str, issuer: str) -> OperationResult:
        """Quarantine one visible queued task through the supervisor-safe cleanup path."""

        return supervisor_queue_cleanup_quarantine_operation(
            self.paths,
            task_id=task_id,
            reason=reason,
            issuer=issuer,
            daemon_running=self.is_daemon_running(),
        )

    def research_report(self) -> ResearchReport:
        """Return a typed visibility report for the research runtime."""

        observed_at = datetime.now(timezone.utc)
        state = read_control_research_state(self.paths)
        source_kind: Literal["snapshot", "live"] = "snapshot"
        if state is None:
            state = live_research_runtime_state(self.loaded, observed_at=observed_at)
            source_kind = "live"

        try:
            status = StatusStore(self.paths.research_status_file, ControlPlane.RESEARCH).read()
        except (FileNotFoundError, StatusError, ValidationError, ValueError) as exc:
            raise ControlError(f"research status could not be read: {expected_error_message(exc)}") from exc

        try:
            discovery = discover_research_queues(self.paths)
        except (ValidationError, ValueError) as exc:
            raise ControlError(f"research queue state could not be read: {expected_error_message(exc)}") from exc
        if source_kind == "live":
            state = state.model_copy(update={"queue_snapshot": discovery.to_snapshot(last_scanned_at=observed_at)})

        ownership_map: dict[ResearchQueueFamily, tuple[ResearchQueueOwnership, ...]] = {
            family: tuple(item for item in state.queue_snapshot.ownerships if item.family is family)
            for family in ResearchQueueFamily
        }
        gate_decision_path, completion_decision_path = decision_report_paths(self.paths)
        latest_gate_decision = None
        latest_completion_decision = None
        latest_audit_remediation = None
        audit_summary = load_audit_summary(self.paths)
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
                    self.paths,
                    run_id=latest_gate_decision.run_id,
                )
        except ValidationError as exc:
            raise ControlError(f"research decision state is invalid: {validation_error_message(exc)}") from exc
        except ValueError as exc:
            raise ControlError(f"research decision state is invalid: {single_line_message(exc)}") from exc
        try:
            governance = build_research_governance_report(self.paths)
        except ValidationError as exc:
            raise ControlError(f"governance report state is invalid: {validation_error_message(exc)}") from exc
        except ValueError as exc:
            raise ControlError(f"governance report state is invalid: {single_line_message(exc)}") from exc

        return ResearchReport(
            config_path=self.config_path,
            source_kind=source_kind,
            configured_mode=self.loaded.config.research.mode,
            configured_idle_mode=self.loaded.config.research.idle_mode,
            status=status,
            runtime=state,
            queue_families=tuple(
                research_queue_family_view(scan, ownerships=ownership_map[scan.family])
                for scan in discovery.families
            ),
            deferred_breadcrumb_count=count_deferred(self.paths),
            audit_history_path=self.paths.agents_dir / "audit_history.md",
            audit_summary_path=self.paths.agents_dir / "audit_summary.json",
            audit_summary=audit_summary,
            latest_gate_decision=latest_gate_decision,
            latest_completion_decision=latest_completion_decision,
            latest_audit_remediation=latest_audit_remediation,
            governance=governance,
            completion_state=completion_state_view(
                self.paths,
                latest_completion_decision=latest_completion_decision,
            ),
        )

    def research_history(self, limit: int = 20) -> list[EventRecord]:
        """Return recent research-related events from the durable engine event log."""

        if limit < 0:
            raise ControlError("research_history requires a non-negative limit")
        if limit == 0:
            return []
        try:
            return [event for event in read_event_log(self.paths.engine_events_log) if is_research_event_type(event.type)][
                -limit:
            ]
        except (ValidationError, ValueError) as exc:
            raise event_log_control_error(exc) from exc

    def interview_list(self) -> InterviewListReport:
        """Return all persisted manual interview questions."""

        return interview_list_operation(self.config_path, self.paths)

    def interview_show(self, question_id: str) -> InterviewQuestionReport:
        """Return one persisted interview question plus any recorded decision."""

        return interview_show_operation(self.config_path, self.paths, question_id)

    def interview_create(
        self,
        *,
        source_path: str | Path,
        question: str,
        why_this_matters: str,
        recommended_answer: str,
        answer_source: Literal["repo", "operator", "assumption"] = "assumption",
        blocking: bool = True,
        evidence: tuple[str, ...] | list[str] | None = None,
    ) -> InterviewMutationReport:
        """Create one pending interview question for a selected staged idea or spec."""

        return interview_create_operation(
            self.config_path,
            self.paths,
            source_path=source_path,
            question=question,
            why_this_matters=why_this_matters,
            recommended_answer=recommended_answer,
            answer_source=answer_source,
            blocking=blocking,
            evidence=evidence,
        )

    def interview_answer(
        self,
        question_id: str,
        *,
        text: str,
        evidence: tuple[str, ...] | list[str] | None = None,
    ) -> InterviewMutationReport:
        """Resolve one pending interview question with an explicit operator answer."""

        return interview_answer_operation(
            self.config_path,
            self.paths,
            question_id,
            text=text,
            evidence=evidence,
        )

    def interview_accept(
        self,
        question_id: str,
        *,
        evidence: tuple[str, ...] | list[str] | None = None,
    ) -> InterviewMutationReport:
        """Resolve one pending interview question by accepting its recommended answer."""

        return interview_accept_operation(
            self.config_path,
            self.paths,
            question_id,
            evidence=evidence,
        )

    def interview_skip(
        self,
        question_id: str,
        *,
        reason: str | None = None,
        evidence: tuple[str, ...] | list[str] | None = None,
    ) -> InterviewMutationReport:
        """Resolve one pending interview question by skipping it with an assumption record."""

        return interview_skip_operation(
            self.config_path,
            self.paths,
            question_id,
            reason=reason,
            evidence=evidence,
        )

    def logs(self, n: int = 50) -> list[EventRecord]:
        """Return the most recent structured engine events."""

        if n < 0:
            raise ControlError("logs requires a non-negative tail size")
        if n == 0:
            return []
        try:
            return read_event_log(self.paths.engine_events_log)[-n:]
        except (ValidationError, ValueError) as exc:
            raise event_log_control_error(exc) from exc

    def events_subscribe(
        self,
        *,
        start_at_end: bool = True,
        poll_interval_seconds: float = 0.2,
        idle_timeout_seconds: float | None = None,
    ) -> Iterator[EventRecord]:
        """Yield structured events by following the durable JSONL event log."""

        if poll_interval_seconds <= 0:
            raise ControlError("poll_interval_seconds must be greater than zero")
        if idle_timeout_seconds is not None and idle_timeout_seconds <= 0:
            raise ControlError("idle_timeout_seconds must be greater than zero")

        log_path = self.paths.engine_events_log
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

    def add_task(self, title: str, *, body: str | None = None, spec_id: str | None = None) -> OperationResult:
        """Add one task card to the backlog or daemon mailbox."""

        return add_task_operation(
            self.paths,
            title=title,
            body=body,
            spec_id=spec_id,
            daemon_running=self.is_daemon_running(),
        )

    def supervisor_add_task(
        self,
        title: str,
        *,
        issuer: str,
        body: str | None = None,
        spec_id: str | None = None,
    ) -> OperationResult:
        """Add one task through the supported supervisor-safe mutation path."""

        return supervisor_add_task_operation(
            self.paths,
            title=title,
            issuer=issuer,
            body=body,
            spec_id=spec_id,
            daemon_running=self.is_daemon_running(),
        )

    def add_idea(self, file: Path | str) -> OperationResult:
        """Queue one idea file."""

        return add_idea_operation(
            self.paths,
            file=file,
            daemon_running=self.is_daemon_running(),
        )

    def publish_sync(self, *, staging_repo_dir: Path | str | None = None) -> StagingSyncReport:
        """Sync the manifest-selected workspace surface into the staging repo."""

        return publish_sync_operation(self.paths, staging_repo_dir=staging_repo_dir)

    def publish_preflight(
        self,
        *,
        staging_repo_dir: Path | str | None = None,
        commit_message: str | None = None,
        push: bool = False,
    ) -> PublishPreflightReport:
        """Return publish readiness for the staging repo without mutating git state."""

        return publish_preflight_operation(
            self.paths,
            staging_repo_dir=staging_repo_dir,
            commit_message=commit_message,
            push=push,
        )

    def publish_commit(
        self,
        *,
        staging_repo_dir: Path | str | None = None,
        commit_message: str | None = None,
        push: bool = False,
    ) -> PublishCommitReport:
        """Commit staging-repo changes and optionally push them."""

        return publish_commit_operation(
            self.paths,
            staging_repo_dir=staging_repo_dir,
            commit_message=commit_message,
            push=push,
        )

    def stop(self) -> OperationResult:
        """Request daemon stop."""

        return lifecycle_action(
            self.paths,
            command=ControlCommand.STOP,
            daemon_running=self.is_daemon_running(),
        )

    def supervisor_stop(self, *, issuer: str) -> OperationResult:
        """Request daemon stop through the supervisor-safe mutation path."""

        return supervisor_lifecycle_action(
            self.paths,
            command=ControlCommand.STOP,
            issuer=issuer,
            daemon_running=self.is_daemon_running(),
        )

    def pause(self) -> OperationResult:
        """Request daemon pause."""

        return lifecycle_action(
            self.paths,
            command=ControlCommand.PAUSE,
            daemon_running=self.is_daemon_running(),
        )

    def supervisor_pause(self, *, issuer: str) -> OperationResult:
        """Request daemon pause through the supervisor-safe mutation path."""

        return supervisor_lifecycle_action(
            self.paths,
            command=ControlCommand.PAUSE,
            issuer=issuer,
            daemon_running=self.is_daemon_running(),
        )

    def resume(self) -> OperationResult:
        """Request daemon resume."""

        return lifecycle_action(
            self.paths,
            command=ControlCommand.RESUME,
            daemon_running=self.is_daemon_running(),
        )

    def supervisor_resume(self, *, issuer: str) -> OperationResult:
        """Request daemon resume through the supervisor-safe mutation path."""

        return supervisor_lifecycle_action(
            self.paths,
            command=ControlCommand.RESUME,
            issuer=issuer,
            daemon_running=self.is_daemon_running(),
        )
