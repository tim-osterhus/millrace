"""Control API facade and runtime-state helpers."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal

from pydantic import ValidationError

from .adapters.control_mailbox import ControlCommand, write_command
from .compounding import (
    build_compounding_integrity_report,
    build_compounding_orientation_snapshot,
    discover_governed_procedures,
    discover_harness_benchmark_results,
    discover_harness_candidates,
    discover_harness_recommendations,
    governed_procedure_for_id,
    harness_benchmark_result_for_id,
    harness_candidate_for_id,
    harness_recommendation_for_id,
    lifecycle_history_for_procedure,
    run_harness_benchmark,
    run_harness_search,
)
from .compounding.integrity import CompoundingIntegrityReport
from .config import LoadedConfig, build_runtime_paths
from .context_facts import context_fact_for_id, discover_context_facts
from .contract_compounding import ProcedureScope
from .contracts import AuditGateDecision, CompletionDecision, ExecutionStatus, ResearchStatus
from .control_actions import (
    add_idea as add_idea_operation,
)
from .control_actions import (
    add_task as add_task_operation,
)
from .control_actions import (
    compounding_deprecate as compounding_deprecate_operation,
)
from .control_actions import (
    compounding_promote as compounding_promote_operation,
)
from .control_actions import (
    lifecycle_action,
    normalize_supervisor_issuer,
    operation_with_payload_value,
    supervisor_lifecycle_action,
)
from .control_actions import (
    queue_cleanup_quarantine as queue_cleanup_quarantine_operation,
)
from .control_actions import (
    queue_cleanup_remove as queue_cleanup_remove_operation,
)
from .control_actions import (
    queue_reorder as queue_reorder_operation,
)
from .control_actions import (
    supervisor_add_task as supervisor_add_task_operation,
)
from .control_actions import (
    supervisor_queue_cleanup_quarantine as supervisor_queue_cleanup_quarantine_operation,
)
from .control_actions import (
    supervisor_queue_cleanup_remove as supervisor_queue_cleanup_remove_operation,
)
from .control_actions import (
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
from .control_compounding_surface import (
    compounding_context_fact as compounding_context_fact_surface,
)
from .control_compounding_surface import (
    compounding_context_facts as compounding_context_facts_surface,
)
from .control_compounding_surface import (
    compounding_deprecate as compounding_deprecate_surface,
)
from .control_compounding_surface import (
    compounding_governance_summary as compounding_governance_summary_surface,
)
from .control_compounding_surface import (
    compounding_harness_benchmark as compounding_harness_benchmark_surface,
)
from .control_compounding_surface import (
    compounding_harness_benchmarks as compounding_harness_benchmarks_surface,
)
from .control_compounding_surface import (
    compounding_harness_candidate as compounding_harness_candidate_surface,
)
from .control_compounding_surface import (
    compounding_harness_candidates as compounding_harness_candidates_surface,
)
from .control_compounding_surface import (
    compounding_harness_recommendation as compounding_harness_recommendation_surface,
)
from .control_compounding_surface import (
    compounding_harness_recommendations as compounding_harness_recommendations_surface,
)
from .control_compounding_surface import (
    compounding_harness_run_benchmark as compounding_harness_run_benchmark_surface,
)
from .control_compounding_surface import (
    compounding_harness_run_search as compounding_harness_run_search_surface,
)
from .control_compounding_surface import (
    compounding_lint as compounding_lint_surface,
)
from .control_compounding_surface import (
    compounding_orientation as compounding_orientation_surface,
)
from .control_compounding_surface import (
    compounding_procedure as compounding_procedure_surface,
)
from .control_compounding_surface import (
    compounding_procedures as compounding_procedures_surface,
)
from .control_compounding_surface import (
    compounding_promote as compounding_promote_surface,
)
from .control_interview import (
    interview_accept as interview_accept_operation,
)
from .control_interview import (
    interview_answer as interview_answer_operation,
)
from .control_interview import (
    interview_create as interview_create_operation,
)
from .control_interview import (
    interview_list as interview_list_operation,
)
from .control_interview import (
    interview_show as interview_show_operation,
)
from .control_interview import (
    interview_skip as interview_skip_operation,
)
from .control_models import (
    ActiveTaskRemediationResult,
    AssetFamilyEntryView,
    AssetInventoryView,
    AssetResolutionView,
    CompletionStateView,
    CompoundingContextFactListReport,
    CompoundingContextFactReport,
    CompoundingContextFactView,
    CompoundingGovernanceSummaryView,
    CompoundingHarnessBenchmarkListReport,
    CompoundingHarnessBenchmarkReport,
    CompoundingHarnessBenchmarkView,
    CompoundingHarnessCandidateListReport,
    CompoundingHarnessCandidateReport,
    CompoundingHarnessCandidateView,
    CompoundingHarnessRecommendationListReport,
    CompoundingHarnessRecommendationReport,
    CompoundingHarnessRecommendationView,
    CompoundingLifecycleRecordView,
    CompoundingOrientationArtifactView,
    CompoundingOrientationEntryView,
    CompoundingOrientationReport,
    CompoundingProcedureListReport,
    CompoundingProcedureReport,
    CompoundingProcedureView,
    CompoundingRelationshipClusterView,
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
from .control_mutation_surface import (
    active_task_remediate as active_task_remediate_surface,
)
from .control_mutation_surface import (
    add_idea as add_idea_surface,
)
from .control_mutation_surface import (
    add_task as add_task_surface,
)
from .control_mutation_surface import (
    interview_accept as interview_accept_surface,
)
from .control_mutation_surface import (
    interview_answer as interview_answer_surface,
)
from .control_mutation_surface import (
    interview_create as interview_create_surface,
)
from .control_mutation_surface import (
    interview_list as interview_list_surface,
)
from .control_mutation_surface import (
    interview_show as interview_show_surface,
)
from .control_mutation_surface import (
    interview_skip as interview_skip_surface,
)
from .control_mutation_surface import (
    pause as pause_surface,
)
from .control_mutation_surface import (
    publish_commit as publish_commit_surface,
)
from .control_mutation_surface import (
    publish_preflight as publish_preflight_surface,
)
from .control_mutation_surface import (
    publish_sync as publish_sync_surface,
)
from .control_mutation_surface import (
    queue_cleanup_quarantine as queue_cleanup_quarantine_surface,
)
from .control_mutation_surface import (
    queue_cleanup_remove as queue_cleanup_remove_surface,
)
from .control_mutation_surface import (
    queue_reorder as queue_reorder_surface,
)
from .control_mutation_surface import (
    resume as resume_surface,
)
from .control_mutation_surface import (
    stop as stop_surface,
)
from .control_mutation_surface import (
    supervisor_add_task as supervisor_add_task_surface,
)
from .control_mutation_surface import (
    supervisor_active_task_remediate as supervisor_active_task_remediate_surface,
)
from .control_mutation_surface import (
    supervisor_pause as supervisor_pause_surface,
)
from .control_mutation_surface import (
    supervisor_queue_cleanup_quarantine as supervisor_queue_cleanup_quarantine_surface,
)
from .control_mutation_surface import (
    supervisor_queue_cleanup_remove as supervisor_queue_cleanup_remove_surface,
)
from .control_mutation_surface import (
    supervisor_queue_reorder as supervisor_queue_reorder_surface,
)
from .control_mutation_surface import (
    supervisor_resume as supervisor_resume_surface,
)
from .control_mutation_surface import (
    supervisor_stop as supervisor_stop_surface,
)
from .control_mutations import apply_native_config_value
from .control_publish import (
    publish_commit as publish_commit_operation,
)
from .control_publish import (
    publish_preflight as publish_preflight_operation,
)
from .control_publish import (
    publish_sync as publish_sync_operation,
)
from .control_queue_config_surface import (
    config_reload as config_reload_surface,
)
from .control_queue_config_surface import (
    config_set as config_set_surface,
)
from .control_queue_config_surface import (
    config_show as config_show_surface,
)
from .control_queue_config_surface import (
    queue as queue_surface,
)
from .control_queue_config_surface import (
    queue_inspect as queue_inspect_surface,
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
from .control_runtime_surface import (
    events_subscribe as events_subscribe_surface,
)
from .control_runtime_surface import (
    logs as logs_surface,
)
from .control_runtime_surface import (
    research_history as research_history_surface,
)
from .control_runtime_surface import (
    research_report as research_report_surface,
)
from .control_runtime_surface import (
    run_provenance as run_provenance_surface,
)
from .control_runtime_surface import (
    status as status_surface,
)
from .control_runtime_surface import (
    supervisor_report as supervisor_report_surface,
)
from .engine_runtime import reconcile_runtime_snapshot, start_engine
from .events import EventRecord, EventType, is_research_event_type
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
    WorkspaceInitError,
    WorkspaceInitReport,
    WorkspaceUpgradeApplyReport,
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
                "would_materialize_runtime_owned": report.would_materialize_runtime_owned,
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
                "materialized_runtime_owned": report.materialized_runtime_owned,
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

        state, _liveness = reconcile_runtime_snapshot(read_runtime_state(self.state_path))
        return bool(state is not None and state.process_running)

    def start(self, *, daemon: bool = False, once: bool = False) -> RuntimeState:
        """Start the engine in foreground once or daemon mode."""
        return start_engine(self.config_path, daemon=daemon, once=once)

    def active_task_clear(self, *, reason: str) -> ActiveTaskRemediationResult:
        """Request supported active-task clear semantics."""

        return active_task_remediate_surface(self, "clear", reason=reason)

    def active_task_recover(self, *, reason: str) -> ActiveTaskRemediationResult:
        """Request supported active-task recover semantics."""

        return active_task_remediate_surface(self, "recover", reason=reason)

    def active_task_remediate(self, intent: str, *, reason: str) -> ActiveTaskRemediationResult:
        """Request active-task remediation through the stable control API."""

        return active_task_remediate_surface(self, intent, reason=reason)

    def supervisor_active_task_clear(self, *, reason: str, issuer: str) -> ActiveTaskRemediationResult:
        """Request supervisor-attributed active-task clear semantics."""

        return supervisor_active_task_remediate_surface(self, "clear", reason=reason, issuer=issuer)

    def supervisor_active_task_recover(self, *, reason: str, issuer: str) -> ActiveTaskRemediationResult:
        """Request supervisor-attributed active-task recover semantics."""

        return supervisor_active_task_remediate_surface(self, "recover", reason=reason, issuer=issuer)

    # Delegate bulky surface families into owned modules while keeping EngineControl stable.
    status = status_surface
    supervisor_report = supervisor_report_surface

    def run_provenance(self, run_id: str) -> RunProvenanceReport:
        """Return the compile-time snapshot plus runtime transition history for one run."""

        return run_provenance_surface(
            self,
            run_id,
            selection_view_builder=runtime_selection_view_from_snapshot,
        )

    compounding_procedures = compounding_procedures_surface
    compounding_procedure = compounding_procedure_surface
    compounding_context_facts = compounding_context_facts_surface
    compounding_context_fact = compounding_context_fact_surface
    compounding_governance_summary = compounding_governance_summary_surface
    compounding_orientation = compounding_orientation_surface
    compounding_lint = compounding_lint_surface
    compounding_harness_candidates = compounding_harness_candidates_surface
    compounding_harness_candidate = compounding_harness_candidate_surface
    compounding_harness_benchmarks = compounding_harness_benchmarks_surface
    compounding_harness_benchmark = compounding_harness_benchmark_surface
    compounding_harness_run_benchmark = compounding_harness_run_benchmark_surface
    compounding_harness_run_search = compounding_harness_run_search_surface
    compounding_harness_recommendations = compounding_harness_recommendations_surface
    compounding_harness_recommendation = compounding_harness_recommendation_surface
    compounding_promote = compounding_promote_surface
    compounding_deprecate = compounding_deprecate_surface
    config_show = config_show_surface
    config_reload = config_reload_surface
    config_set = config_set_surface
    queue = queue_surface
    queue_inspect = queue_inspect_surface
    queue_reorder = queue_reorder_surface
    queue_cleanup_remove = queue_cleanup_remove_surface
    queue_cleanup_quarantine = queue_cleanup_quarantine_surface
    supervisor_queue_reorder = supervisor_queue_reorder_surface
    supervisor_queue_cleanup_remove = supervisor_queue_cleanup_remove_surface
    supervisor_queue_cleanup_quarantine = supervisor_queue_cleanup_quarantine_surface

    research_report = research_report_surface
    research_history = research_history_surface
    interview_list = interview_list_surface
    interview_show = interview_show_surface
    interview_create = interview_create_surface
    interview_answer = interview_answer_surface
    interview_accept = interview_accept_surface
    interview_skip = interview_skip_surface

    logs = logs_surface
    events_subscribe = events_subscribe_surface
    add_task = add_task_surface
    supervisor_add_task = supervisor_add_task_surface
    add_idea = add_idea_surface
    publish_sync = publish_sync_surface
    publish_preflight = publish_preflight_surface
    publish_commit = publish_commit_surface
    stop = stop_surface
    supervisor_stop = supervisor_stop_surface
    pause = pause_surface
    supervisor_pause = supervisor_pause_surface
    resume = resume_surface
    supervisor_resume = supervisor_resume_surface
