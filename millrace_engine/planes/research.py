"""Research-plane intake, mode selection, and compiled dispatch."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..compiler import FrozenRunCompiler
from ..config import EngineConfig
from ..contracts import ExecutionResearchHandoff, ResearchMode, ResearchStatus, SpecInterviewPolicy
from ..events import EventRecord, EventType
from ..paths import RuntimePaths
from ..research.audit import (
    AuditExecutionError,
    execute_audit_gatekeeper,
    execute_audit_intake,
    execute_audit_validate,
)
from ..research.dispatcher import (
    RESEARCH_AUDIT_MODE_REF,
    RESEARCH_GOALSPEC_MODE_REF,
    RESEARCH_INCIDENT_MODE_REF,
    CompiledResearchDispatch,
    ResearchDispatchError,
    ResearchDispatchSelection,
    compile_research_dispatch,
    resolve_research_dispatch_selection,
)
from ..research.goalspec import (
    GoalSpecReviewBlockedError,
    GoalSpecExecutionError,
    execute_completion_manifest_draft,
    execute_goal_intake,
    execute_objective_profile_sync,
    execute_spec_interview,
    execute_spec_review,
    execute_spec_review_remediation,
    execute_spec_synthesis,
    next_stage_for_success,
    research_stage_for_node,
)
from ..research.goalspec_delivery_integrity import (
    delivery_integrity_error_message,
    sync_goalspec_delivery_integrity,
)
from ..research.incidents import (
    IncidentExecutionError,
    execute_incident_archive,
    execute_incident_intake,
    execute_incident_resolve,
    execute_incident_task_generation,
)
from ..research.queues import discover_research_queues
from ..research.specs import load_goal_spec_family_state
from ..research.state import (
    DeferredResearchRequest,
    ResearchCheckpoint,
    ResearchQueueFamily,
    ResearchQueueSelectionAuthority,
    ResearchRuntimeMode,
    ResearchRuntimeState,
    ResearchStateStore,
    rebind_research_runtime_state,
)
from ..research.supervisor_lifecycle import (
    acquire_lock as _acquire_lock_helper,
)
from ..research.supervisor_lifecycle import (
    configured_runtime_mode as _configured_runtime_mode_helper,
)
from ..research.supervisor_lifecycle import (
    lock_expiry as _lock_expiry_helper,
)
from ..research.supervisor_lifecycle import (
    lock_path as _lock_path_helper,
)
from ..research.supervisor_lifecycle import (
    next_poll_at as _next_poll_at_helper,
)
from ..research.supervisor_lifecycle import (
    next_retry_state as _next_retry_state_helper,
)
from ..research.supervisor_lifecycle import (
    no_work_reason_for_selection as _no_work_reason_for_selection_helper,
)
from ..research.supervisor_lifecycle import (
    record_dispatch_failure as _record_dispatch_failure_helper,
)
from ..research.supervisor_lifecycle import (
    record_lock_failure as _record_lock_failure_helper,
)
from ..research.supervisor_lifecycle import (
    record_no_dispatchable_work as _record_no_dispatchable_work_helper,
)
from ..research.supervisor_lifecycle import (
    release_execution_lock as _release_execution_lock_helper,
)
from ..research.supervisor_lifecycle import (
    release_lock as _release_lock_helper,
)
from ..research.supervisor_lifecycle import (
    set_research_status as _set_research_status_helper,
)
from ..research.supervisor_lifecycle import (
    should_scan as _should_scan_helper,
)
from ..research.supervisor_payloads import discovery_payload as _discovery_payload_helper
from ..research.supervisor_progression import (
    advance_audit_checkpoint as _advance_audit_checkpoint_helper,
)
from ..research.supervisor_progression import (
    advance_goalspec_checkpoint as _advance_goalspec_checkpoint_helper,
)
from ..research.supervisor_progression import (
    advance_incident_checkpoint as _advance_incident_checkpoint_helper,
)
from ..research.supervisor_progression import (
    complete_audit_checkpoint as _complete_audit_checkpoint_helper,
)
from ..research.supervisor_progression import (
    complete_goalspec_checkpoint as _complete_goalspec_checkpoint_helper,
)
from ..research.supervisor_progression import (
    complete_incident_checkpoint as _complete_incident_checkpoint_helper,
)
from ..research.supervisor_progression import (
    next_goalspec_stage as _next_goalspec_stage_helper,
)
from ..research.supervisor_progression import (
    next_incident_stage as _next_incident_stage_helper,
)
from ..research.supervisor_progression import (
    persist_resume_state as _persist_resume_state_helper,
)
from ..research.supervisor_progression import (
    queue_ownership_for_audit_path as _queue_ownership_for_audit_path_helper,
)
from ..research.supervisor_progression import (
    resume_selected_family as _resume_selected_family_helper,
)
from ..research.supervisor_progression import (
    selection_ownerships as _selection_ownerships_helper,
)
from ..research.supervisor_progression import (
    supports_audit_stage_execution as _supports_audit_stage_execution_helper,
)
from ..research.supervisor_progression import (
    supports_goalspec_stage_execution as _supports_goalspec_stage_execution_helper,
)
from ..research.supervisor_progression import (
    supports_incident_stage_execution as _supports_incident_stage_execution_helper,
)
from ..research.supervisor_requests import (
    audit_record_from_event as _audit_record_from_event_helper,
)
from ..research.supervisor_requests import (
    bind_queue_context_to_request as _bind_queue_context_to_request_helper,
)
from ..research.supervisor_requests import (
    breadcrumb_name as _breadcrumb_name_helper,
)
from ..research.supervisor_requests import (
    breadcrumb_path as _breadcrumb_path_helper,
)
from ..research.supervisor_requests import (
    claim_blocker_request_from_latch as _claim_blocker_request_from_latch_helper,
)
from ..research.supervisor_requests import (
    claim_deferred_request as _claim_deferred_request_helper,
)
from ..research.supervisor_requests import (
    claim_dispatch_request as _claim_dispatch_request_helper,
)
from ..research.supervisor_requests import (
    enqueue_request as _enqueue_request_helper,
)
from ..research.supervisor_requests import (
    handoff_from_event as _handoff_from_event_helper,
)
from ..research.supervisor_requests import (
    handoffs_match as _handoffs_match_helper,
)
from ..research.supervisor_requests import (
    incident_path_matches_handoff as _incident_path_matches_handoff_helper,
)
from ..research.supervisor_requests import (
    latch_handoff as _latch_handoff_helper,
)
from ..research.supervisor_requests import (
    paths_match as _paths_match_helper,
)
from ..research.supervisor_requests import (
    request_matches_handoff as _request_matches_handoff_helper,
)
from ..research.supervisor_requests import (
    request_task_id as _request_task_id_helper,
)
from ..research.supervisor_requests import (
    resume_checkpoint_handoff as _resume_checkpoint_handoff_helper,
)
from ..research.supervisor_requests import (
    synthetic_blocker_request as _synthetic_blocker_request_helper,
)
from ..research.supervisor_requests import (
    write_breadcrumb as _write_breadcrumb_helper,
)
from ..research.taskaudit import execute_taskaudit
from ..research.taskmaster import execute_taskmaster
from ..run_ids import timestamped_slug_id
from ..status import ControlPlane, StatusStore


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_MODE_REF_BY_RUNTIME_MODE = {
    ResearchRuntimeMode.GOALSPEC: RESEARCH_GOALSPEC_MODE_REF,
    ResearchRuntimeMode.INCIDENT: RESEARCH_INCIDENT_MODE_REF,
    ResearchRuntimeMode.AUDIT: RESEARCH_AUDIT_MODE_REF,
}
_REQUEST_FAMILY_BY_EVENT = {
    EventType.IDEA_SUBMITTED: ResearchQueueFamily.GOALSPEC,
    EventType.BACKLOG_EMPTY_AUDIT: ResearchQueueFamily.AUDIT,
    EventType.AUDIT_REQUESTED: ResearchQueueFamily.AUDIT,
    EventType.NEEDS_RESEARCH: ResearchQueueFamily.BLOCKER,
}
_GOALSPEC_COMPLETION_MANIFEST_NODE_ID = "completion_manifest_draft"
_GOALSPEC_COMPLETION_MANIFEST_KIND_ID = "research.completion-manifest-draft"
_GOALSPEC_MECHANIC_NODE_ID = "mechanic"
_GOALSPEC_MECHANIC_KIND_ID = "research.mechanic"
_GOALSPEC_TASKAUDIT_NODE_ID = "taskaudit"
_GOALSPEC_TASKAUDIT_KIND_ID = "research.taskaudit"
_GOALSPEC_EARLIER_STAGE_ENTRY_NODE_IDS = frozenset({"goal_intake", "objective_profile_sync"})


@dataclass(frozen=True)
class _GoalSpecSyntheticStage:
    node_id: str
    kind_id: str
    running_status: str


class ResearchLockUnavailableError(ResearchDispatchError):
    """Raised when the research supervisor lock is already held elsewhere."""


class ResearchPlane:
    """Config-aware research plane with compiled dispatcher-backed selection."""

    _lock_error_cls = ResearchLockUnavailableError
    accepted_event_types = frozenset(
        {
            EventType.NEEDS_RESEARCH,
            EventType.BACKLOG_EMPTY_AUDIT,
            EventType.AUDIT_REQUESTED,
            EventType.IDEA_SUBMITTED,
        }
    )

    def __init__(
        self,
        config_or_paths: EngineConfig | RuntimePaths,
        paths: RuntimePaths | None = None,
        *,
        emit_event: Callable[[EventType, dict[str, Any]], None] | None = None,
        state_store: ResearchStateStore | None = None,
        compiler: FrozenRunCompiler | None = None,
    ) -> None:
        if isinstance(config_or_paths, RuntimePaths):
            config = EngineConfig()
            resolved_paths = config_or_paths
        else:
            config = config_or_paths
            if paths is None:
                raise ValueError("research plane requires runtime paths when initialized with an engine config")
            resolved_paths = paths

        self.emit_event = emit_event
        self._custom_state_store = state_store is not None
        self._custom_compiler = compiler is not None
        self._last_dispatch: CompiledResearchDispatch | None = None
        self._lock_handle = None
        self._owner_id = f"research-plane-{os.getpid()}-{id(self):x}"
        self.config = config
        self.paths = resolved_paths
        self.state_store = state_store or ResearchStateStore(
            self.paths.research_state_file,
            deferred_dir=self.paths.deferred_dir,
        )
        self.compiler = compiler or FrozenRunCompiler(self.paths)
        self.status_store = StatusStore(self.paths.research_status_file, ControlPlane.RESEARCH)
        self.state = self.state_store.bootstrap(mode_reason=self._bootstrap_reason())

    def bind_emitter(self, emit_event: Callable[[EventType, dict[str, Any]], None]) -> None:
        """Attach the event-emission callback after bus construction."""

        self.emit_event = emit_event

    def reconfigure(
        self,
        config_or_paths: EngineConfig | RuntimePaths,
        paths: RuntimePaths | None = None,
    ) -> None:
        """Refresh config and paths without losing persisted state."""

        self.shutdown(persist=False)

        if isinstance(config_or_paths, RuntimePaths):
            config = self.config
            resolved_paths = config_or_paths
        else:
            config = config_or_paths
            if paths is None:
                raise ValueError("research plane reconfigure requires runtime paths with an engine config")
            resolved_paths = paths

        self.config = config
        self.paths = resolved_paths
        self.status_store = StatusStore(self.paths.research_status_file, ControlPlane.RESEARCH)
        if not self._custom_state_store:
            self.state_store = ResearchStateStore(
                self.paths.research_state_file,
                deferred_dir=self.paths.deferred_dir,
            )
        if not self._custom_compiler:
            self.compiler = FrozenRunCompiler(self.paths)
        self.state = rebind_research_runtime_state(self.state, deferred_dir=self.paths.deferred_dir)
        # Compiled dispatch artifacts are tied to the previous config/path view.
        self._last_dispatch = None
        self._persist_state()

    def shutdown(self, *, persist: bool = True) -> None:
        """Release any held research lock and clear cached compiled dispatch."""

        self._release_lock()
        self._last_dispatch = None
        if self.state.lock_state is not None:
            self.state = self.state.model_copy(
                update={
                    "updated_at": _utcnow(),
                    "lock_state": None,
                }
            )
        if persist and self.state.lock_state is None:
            self._persist_state()

    def pending_count(self) -> int:
        """Return the number of deferred requests still held in runtime state."""

        return len(self.state.deferred_requests)

    def snapshot_state(self) -> ResearchRuntimeState:
        """Return the current typed research state snapshot."""

        return self.state

    def active_dispatch(self) -> CompiledResearchDispatch | None:
        """Return the most recent compiled dispatch, if one has been selected."""

        return self._last_dispatch

    _audit_record_from_event = _audit_record_from_event_helper
    _bind_queue_context_to_request = _bind_queue_context_to_request_helper
    _breadcrumb_name = _breadcrumb_name_helper
    _breadcrumb_path = _breadcrumb_path_helper
    _claim_blocker_request_from_latch = _claim_blocker_request_from_latch_helper
    _claim_deferred_request = _claim_deferred_request_helper
    _claim_dispatch_request = _claim_dispatch_request_helper
    _configured_runtime_mode = _configured_runtime_mode_helper
    _discovery_payload = _discovery_payload_helper
    _enqueue_request = _enqueue_request_helper
    _handoff_from_event = _handoff_from_event_helper
    _handoffs_match = _handoffs_match_helper
    _incident_path_matches_handoff = _incident_path_matches_handoff_helper
    _latch_handoff = _latch_handoff_helper
    _lock_expiry = _lock_expiry_helper
    _lock_path = _lock_path_helper
    _next_poll_at = _next_poll_at_helper
    _next_retry_state = _next_retry_state_helper
    _no_work_reason_for_selection = _no_work_reason_for_selection_helper
    _paths_match = _paths_match_helper
    _persist_resume_state = _persist_resume_state_helper
    _queue_ownership_for_audit_path = _queue_ownership_for_audit_path_helper
    _record_dispatch_failure = _record_dispatch_failure_helper
    _record_lock_failure = _record_lock_failure_helper
    _record_no_dispatchable_work = _record_no_dispatchable_work_helper
    _release_execution_lock = _release_execution_lock_helper
    _release_lock = _release_lock_helper
    _request_matches_handoff = _request_matches_handoff_helper
    _request_task_id = _request_task_id_helper
    _resume_checkpoint_handoff = _resume_checkpoint_handoff_helper
    _resume_selected_family = _resume_selected_family_helper
    _selection_ownerships = _selection_ownerships_helper
    _set_research_status = _set_research_status_helper
    _should_scan = _should_scan_helper
    _supports_audit_stage_execution = _supports_audit_stage_execution_helper
    _supports_goalspec_stage_execution = _supports_goalspec_stage_execution_helper
    _supports_incident_stage_execution = _supports_incident_stage_execution_helper
    _synthetic_blocker_request = _synthetic_blocker_request_helper
    _write_breadcrumb = _write_breadcrumb_helper
    _acquire_lock = _acquire_lock_helper
    _advance_audit_checkpoint = _advance_audit_checkpoint_helper
    _advance_goalspec_checkpoint = _advance_goalspec_checkpoint_helper
    _advance_incident_checkpoint = _advance_incident_checkpoint_helper
    _complete_audit_checkpoint = _complete_audit_checkpoint_helper
    _complete_goalspec_checkpoint = _complete_goalspec_checkpoint_helper
    _complete_incident_checkpoint = _complete_incident_checkpoint_helper
    _next_goalspec_stage = _next_goalspec_stage_helper
    _next_incident_stage = _next_incident_stage_helper

    def _runtime_selected_family_for_selection(
        self,
        selection: ResearchDispatchSelection,
        discovery: Any,
    ) -> ResearchQueueFamily | None:
        selected_family = selection.queue_snapshot.selected_family
        if selected_family is None:
            return None
        if (
            selection.runtime_mode is ResearchRuntimeMode.INCIDENT
            and discovery.family_scan(ResearchQueueFamily.INCIDENT).ready
        ):
            return ResearchQueueFamily.INCIDENT
        return selected_family

    def _supports_local_goalspec_stage_execution(self, checkpoint: ResearchCheckpoint | None) -> bool:
        if checkpoint is None:
            return False
        if checkpoint.node_id in {
            _GOALSPEC_MECHANIC_NODE_ID,
            _GOALSPEC_COMPLETION_MANIFEST_NODE_ID,
            _GOALSPEC_TASKAUDIT_NODE_ID,
        }:
            return self._resume_selected_family(checkpoint) is ResearchQueueFamily.GOALSPEC
        return self._supports_goalspec_stage_execution(checkpoint)

    def _local_next_goalspec_stage(
        self,
        dispatch: CompiledResearchDispatch,
        checkpoint: ResearchCheckpoint,
    ) -> Any:
        if checkpoint.node_id == "objective_profile_sync":
            return _GoalSpecSyntheticStage(
                node_id=_GOALSPEC_COMPLETION_MANIFEST_NODE_ID,
                kind_id=_GOALSPEC_COMPLETION_MANIFEST_KIND_ID,
                running_status=ResearchStatus.COMPLETION_MANIFEST_RUNNING.value,
            )
        if checkpoint.node_id == _GOALSPEC_COMPLETION_MANIFEST_NODE_ID:
            return next_stage_for_success(dispatch.research_plan, "objective_profile_sync")
        if checkpoint.node_id == _GOALSPEC_MECHANIC_NODE_ID:
            return research_stage_for_node(dispatch.research_plan, "spec_review")
        return self._next_goalspec_stage(dispatch, checkpoint)

    def _advance_local_goalspec_checkpoint(
        self,
        checkpoint: ResearchCheckpoint,
        *,
        next_stage: Any,
        queue_ownership: Any,
        observed_at: datetime,
    ) -> None:
        if (
            next_stage is not None
            and getattr(next_stage, "node_id", None)
            in {
                _GOALSPEC_MECHANIC_NODE_ID,
                _GOALSPEC_COMPLETION_MANIFEST_NODE_ID,
                _GOALSPEC_TASKAUDIT_NODE_ID,
            }
        ):
            next_node_id = getattr(next_stage, "node_id", None)
            next_status = (
                ResearchStatus.GOALSPEC_RUNNING
                if next_node_id == _GOALSPEC_MECHANIC_NODE_ID
                else (
                    ResearchStatus.COMPLETION_MANIFEST_RUNNING
                    if next_node_id == _GOALSPEC_COMPLETION_MANIFEST_NODE_ID
                    else ResearchStatus.TASKAUDIT_RUNNING
                )
            )
            next_kind_id = (
                _GOALSPEC_MECHANIC_KIND_ID
                if next_node_id == _GOALSPEC_MECHANIC_NODE_ID
                else (
                    _GOALSPEC_COMPLETION_MANIFEST_KIND_ID
                    if next_node_id == _GOALSPEC_COMPLETION_MANIFEST_NODE_ID
                    else _GOALSPEC_TASKAUDIT_KIND_ID
                )
            )
            updated = checkpoint.model_copy(
                update={
                    "status": next_status,
                    "node_id": next_node_id,
                    "stage_kind_id": next_kind_id,
                    "updated_at": observed_at,
                    "owned_queues": (queue_ownership,),
                }
            )
            queue_snapshot = self.state.queue_snapshot.model_copy(
                update={
                    "ownerships": updated.owned_queues,
                    "last_scanned_at": observed_at,
                    "selected_family": ResearchQueueFamily.GOALSPEC,
                    "selected_family_authority": ResearchQueueSelectionAuthority.CHECKPOINT,
                }
            )
            self.state = self.state.model_copy(
                update={
                    "updated_at": observed_at,
                    "queue_snapshot": queue_snapshot,
                    "retry_state": None,
                    "checkpoint": updated,
                }
            )
            self._persist_state()
            self._set_research_status(next_status)
            return

        if (
            checkpoint.node_id == _GOALSPEC_MECHANIC_NODE_ID
            and next_stage is not None
            and getattr(next_stage, "node_id", None) == "spec_review"
        ):
            updated = checkpoint.model_copy(
                update={
                    "status": ResearchStatus.SPEC_REVIEW_RUNNING,
                    "node_id": "spec_review",
                    "stage_kind_id": getattr(next_stage, "kind_id", "research.spec-review"),
                    "updated_at": observed_at,
                    "owned_queues": (queue_ownership,),
                }
            )
            queue_snapshot = self.state.queue_snapshot.model_copy(
                update={
                    "ownerships": updated.owned_queues,
                    "last_scanned_at": observed_at,
                    "selected_family": ResearchQueueFamily.GOALSPEC,
                    "selected_family_authority": ResearchQueueSelectionAuthority.CHECKPOINT,
                }
            )
            retry_state = self.state.retry_state
            if retry_state is not None:
                retry_state = retry_state.model_copy(
                    update={
                        "backoff_seconds": 0.0,
                        "next_retry_at": None,
                    }
                )
            self.state = self.state.model_copy(
                update={
                    "updated_at": observed_at,
                    "queue_snapshot": queue_snapshot,
                    "retry_state": retry_state,
                    "checkpoint": updated,
                }
            )
            self._persist_state()
            self._set_research_status(ResearchStatus.SPEC_REVIEW_RUNNING)
            return

        self._advance_goalspec_checkpoint(
            checkpoint,
            next_stage=next_stage,
            queue_ownership=queue_ownership,
            observed_at=observed_at,
        )

    def sync_runtime(
        self,
        *,
        trigger: str,
        run_id: str | None = None,
        resolve_assets: bool = True,
    ) -> CompiledResearchDispatch | None:
        """Advance the research supervisor when restart, retry, or poll windows allow it."""

        if self.config.research.mode is ResearchMode.STUB:
            return None

        observed_at = _utcnow()
        if not self._should_scan(trigger=trigger, observed_at=observed_at):
            return None
        if not self.state.retry_due(observed_at):
            return None
        return self.run_ready_work(
            run_id=run_id,
            trigger=trigger,
            resolve_assets=resolve_assets,
            observed_at=observed_at,
        )

    def dispatch_ready_work(
        self,
        *,
        run_id: str | None = None,
        trigger: str | None = None,
        resolve_assets: bool = True,
        observed_at: datetime | None = None,
    ) -> CompiledResearchDispatch | None:
        """Resolve queue readiness into a compiled research dispatch."""

        if self.config.research.mode is ResearchMode.STUB:
            return None

        started_at = observed_at or _utcnow()
        if self.state.checkpoint is not None:
            return self._resume_checkpoint(
                trigger=trigger,
                resolve_assets=resolve_assets,
                observed_at=started_at,
            )

        discovery = discover_research_queues(self.paths)
        self._emit(
            EventType.RESEARCH_SCAN_COMPLETED,
            self._discovery_payload(discovery, observed_at=started_at),
        )
        try:
            selection = resolve_research_dispatch_selection(
                self.config.research.mode,
                discovery,
                scanned_at=started_at,
            )
        except ResearchDispatchError as exc:
            self._record_dispatch_failure(exc, discovery=discovery, failed_at=started_at)
            raise
        if selection is None:
            delivery_integrity = sync_goalspec_delivery_integrity(
                paths=self.paths,
                queue_discovery=discovery,
                observed_at=started_at,
            )
            if delivery_integrity.status == "failed":
                error = GoalSpecExecutionError(delivery_integrity_error_message(delivery_integrity))
                self._record_dispatch_failure(error, discovery=discovery, failed_at=started_at)
                raise error
            self._record_no_dispatchable_work(
                discovery=discovery,
                observed_at=started_at,
                reason="no-dispatchable-research-work",
            )
            return None

        delivery_integrity = sync_goalspec_delivery_integrity(
            paths=self.paths,
            queue_discovery=discovery,
            entry_node_id=selection.entry_node_id,
            observed_at=started_at,
        )
        if delivery_integrity.status == "failed":
            error = GoalSpecExecutionError(delivery_integrity_error_message(delivery_integrity))
            self._record_dispatch_failure(error, discovery=discovery, failed_at=started_at)
            raise error
        if (
            selection.runtime_mode is ResearchRuntimeMode.GOALSPEC
            and selection.entry_node_id in _GOALSPEC_EARLIER_STAGE_ENTRY_NODE_IDS
            and delivery_integrity.reason == "goalspec-family-taskaudit-finalization-prepared"
            and delivery_integrity.goal_id
            and delivery_integrity.goal_id == delivery_integrity.queue_goal_id
        ):
            self._record_no_dispatchable_work(
                discovery=discovery,
                observed_at=started_at,
                reason="goalspec-delivery-finalization-in-flight",
            )
            return None

        self._emit(
            EventType.RESEARCH_MODE_SELECTED,
            {
                **self._discovery_payload(
                    discovery,
                    observed_at=started_at,
                    selected_family=selection.queue_snapshot.selected_family,
                ),
                "runtime_mode": selection.runtime_mode.value,
                "selected_mode_ref": selection.selected_mode_ref.model_dump(mode="json"),
                "reason": selection.reason,
            },
        )
        if selection.queue_snapshot.selected_family is None:
            self._record_no_dispatchable_work(
                discovery=discovery,
                observed_at=started_at,
                reason=self._no_work_reason_for_selection(selection.runtime_mode),
            )
            return None

        runtime_selected_family = self._runtime_selected_family_for_selection(selection, discovery)
        self._acquire_lock(observed_at=started_at)
        dispatch_run_id = run_id or self._new_run_id(selection.runtime_mode.value.lower())
        try:
            dispatch = compile_research_dispatch(
                self.paths,
                selection,
                run_id=dispatch_run_id,
                compiler=self.compiler,
                queue_discovery=discovery,
                resolve_assets=resolve_assets,
            )
        except ResearchDispatchError as exc:
            self._record_dispatch_failure(exc, discovery=discovery, failed_at=started_at)
            raise

        ownerships = self._selection_ownerships(
            discovery=discovery,
            selected_family=runtime_selected_family,
            owner_token=dispatch_run_id,
            acquired_at=started_at,
        )
        active_request, remaining_requests, parent_handoff = self._claim_dispatch_request(
            runtime_selected_family,
            discovery=discovery,
        )
        active_request, parent_handoff = self._bind_queue_context_to_request(
            active_request=active_request,
            parent_handoff=parent_handoff,
            discovery=discovery,
            selected_family=runtime_selected_family,
            observed_at=started_at,
        )
        previous_mode = self.state.current_mode
        transition_count = self.state.transition_count
        if previous_mode is not selection.runtime_mode:
            transition_count += 1

        queue_snapshot = selection.queue_snapshot.model_copy(
            update={
                "ownerships": ownerships,
                "selected_family": runtime_selected_family,
            }
        )
        checkpoint = dispatch.checkpoint(started_at=started_at).model_copy(
            update={
                "owned_queues": ownerships,
                "active_request": active_request,
                "parent_handoff": parent_handoff,
            }
        )
        self.status_store.write_raw(checkpoint.status)
        self.state = self.state.model_copy(
            update={
                "updated_at": started_at,
                "current_mode": selection.runtime_mode,
                "last_mode": previous_mode,
                "mode_reason": selection.reason if trigger is None else f"{selection.reason}; trigger={trigger}",
                "cycle_count": self.state.cycle_count + 1,
                "transition_count": transition_count,
                "queue_snapshot": queue_snapshot,
                "deferred_requests": remaining_requests,
                "retry_state": None,
                "checkpoint": checkpoint,
                "next_poll_at": None,
            }
        )
        self._last_dispatch = dispatch
        self._persist_state()
        self._emit(
            EventType.RESEARCH_DISPATCH_COMPILED,
            {
                "run_id": dispatch.run_id,
                "configured_mode": self.config.research.mode.value,
                "runtime_mode": selection.runtime_mode.value,
                "reason": self.state.mode_reason,
                "selected_family": (
                    None if runtime_selected_family is None else runtime_selected_family.value
                ),
                "checkpoint_id": checkpoint.checkpoint_id,
                "status": checkpoint.status.value,
                "loop_ref": (
                    None
                    if checkpoint.loop_ref is None
                    else checkpoint.loop_ref.model_dump(mode="json")
                ),
                "node_id": checkpoint.node_id,
                "stage_kind_id": checkpoint.stage_kind_id,
                "owned_queues": [item.model_dump(mode="json") for item in ownerships],
                "active_request_event": (
                    None if active_request is None else active_request.event_type.value
                ),
                "parent_handoff": (
                    None if parent_handoff is None else parent_handoff.model_dump(mode="json")
                ),
                "deferred_request_count": len(self.state.deferred_requests),
            },
        )
        return dispatch

    def run_ready_work(
        self,
        *,
        run_id: str | None = None,
        trigger: str | None = None,
        resolve_assets: bool = True,
        observed_at: datetime | None = None,
    ) -> CompiledResearchDispatch | None:
        """Dispatch research work and execute the supported GoalSpec stages."""

        dispatch = self.dispatch_ready_work(
            run_id=run_id,
            trigger=trigger,
            resolve_assets=resolve_assets,
            observed_at=observed_at,
        )
        if dispatch is None:
            return None
        execution_started_at = observed_at or _utcnow()
        try:
            self._execute_supported_goalspec_stages(dispatch, observed_at=execution_started_at)
            self._execute_supported_incident_stages(dispatch, observed_at=execution_started_at)
            self._execute_supported_audit_stages(dispatch, observed_at=execution_started_at)
        except (ResearchDispatchError, GoalSpecExecutionError, IncidentExecutionError, AuditExecutionError) as exc:
            discovery = discover_research_queues(self.paths)
            self._record_dispatch_failure(exc, discovery=discovery, failed_at=execution_started_at)
            raise
        return self._last_dispatch or dispatch

    def handle(self, event: EventRecord) -> None:
        """Accept supported events and either defer or dispatch work."""

        if event.type not in self.accepted_event_types:
            return

        payload = dict(event.payload)
        audit_record = self._audit_record_from_event(event)
        if audit_record is not None:
            payload.setdefault("path", audit_record.source_path.as_posix())
        request = DeferredResearchRequest.model_validate(
            {
                "event_type": event.type,
                "received_at": event.timestamp,
                "payload": payload,
                "queue_family": _REQUEST_FAMILY_BY_EVENT.get(event.type),
                "handoff": self._handoff_from_event(event),
                "audit_record": audit_record,
            }
        )
        if self.emit_event is not None:
            self.emit_event(
                EventType.RESEARCH_RECEIVED,
                {
                    "source_event": event.type.value,
                    "payload": event.payload,
                    "handoff_id": None if request.handoff is None else request.handoff.handoff_id,
                    "parent_run_id": (
                        None
                        if request.handoff is None or request.handoff.parent_run is None
                        else request.handoff.parent_run.run_id
                    ),
                },
            )

        if self.config.research.mode is ResearchMode.STUB:
            self._enqueue_request(request)
            return

        self._enqueue_request(request)
        if self.state.checkpoint is not None:
            return
        try:
            self.sync_runtime(trigger=event.type.value)
        except (ResearchDispatchError, GoalSpecExecutionError, IncidentExecutionError, AuditExecutionError):
            # The plane state/status already records the failure. As an event-bus
            # subscriber, avoid escalating one bad research selection into an
            # engine-lifecycle crash.
            return

    def _bootstrap_reason(self) -> str:
        if self.config.research.mode is ResearchMode.STUB:
            return "stub-plane-initialized"
        return "research-plane-initialized"

    def _emit(self, event_type: EventType, payload: dict[str, Any]) -> None:
        if self.emit_event is not None:
            self.emit_event(event_type, payload)

    def _persist_state(self) -> None:
        self.state_store.save(self.state)

    def _resume_checkpoint(
        self,
        *,
        trigger: str | None,
        resolve_assets: bool,
        observed_at: datetime,
    ) -> CompiledResearchDispatch | None:
        checkpoint = self.state.checkpoint
        if checkpoint is None:
            return None
        checkpoint = self._resume_checkpoint_handoff(checkpoint)
        if self._last_dispatch is not None and self._last_dispatch.run_id == checkpoint.checkpoint_id:
            self._acquire_lock(observed_at=observed_at)
            self._persist_resume_state(
                checkpoint=checkpoint,
                observed_at=observed_at,
                reason="resume-from-checkpoint" if trigger is None else f"resume-from-checkpoint; trigger={trigger}",
            )
            return self._last_dispatch

        self._acquire_lock(observed_at=observed_at)
        discovery = discover_research_queues(self.paths)
        selected_family = self._resume_selected_family(checkpoint)
        self._emit(
            EventType.RESEARCH_SCAN_COMPLETED,
            self._discovery_payload(
                discovery,
                observed_at=observed_at,
                selected_family=selected_family,
            ),
        )
        selection = ResearchDispatchSelection(
            configured_mode=self.config.research.mode,
            runtime_mode=checkpoint.mode,
            selected_mode_ref=_MODE_REF_BY_RUNTIME_MODE[checkpoint.mode],
            entry_node_id=checkpoint.node_id,
            queue_snapshot=discovery.to_snapshot(
                ownerships=checkpoint.owned_queues,
                last_scanned_at=observed_at,
                selected_family=selected_family,
                selected_family_authority=ResearchQueueSelectionAuthority.CHECKPOINT,
            ),
            reason="resume-from-checkpoint",
        )
        try:
            dispatch = compile_research_dispatch(
                self.paths,
                selection,
                run_id=checkpoint.checkpoint_id,
                compiler=self.compiler,
                queue_discovery=discovery,
                resolve_assets=resolve_assets,
            )
        except ResearchDispatchError as exc:
            self._record_dispatch_failure(exc, discovery=discovery, failed_at=observed_at)
            raise

        reason = "resume-from-checkpoint" if trigger is None else f"resume-from-checkpoint; trigger={trigger}"
        self._persist_resume_state(checkpoint=checkpoint, observed_at=observed_at, reason=reason)
        self._last_dispatch = dispatch
        return dispatch

    def _execute_supported_goalspec_stages(
        self,
        dispatch: CompiledResearchDispatch,
        *,
        observed_at: datetime,
    ) -> None:
        checkpoint = self.state.checkpoint
        if not self._supports_local_goalspec_stage_execution(checkpoint):
            if checkpoint is not None and self._resume_selected_family(checkpoint) is ResearchQueueFamily.GOALSPEC:
                self._release_execution_lock(observed_at=observed_at)
            return

        stage_started_at = _utcnow()
        if checkpoint.node_id == "goal_intake":
            result = execute_goal_intake(
                self.paths,
                checkpoint,
                run_id=dispatch.run_id,
                emitted_at=stage_started_at,
            )
        elif checkpoint.node_id == "objective_profile_sync":
            result = execute_objective_profile_sync(
                self.paths,
                checkpoint,
                run_id=dispatch.run_id,
                emitted_at=stage_started_at,
            )
        elif checkpoint.node_id == _GOALSPEC_COMPLETION_MANIFEST_NODE_ID:
            self._set_research_status(ResearchStatus.COMPLETION_MANIFEST_RUNNING)
            execute_completion_manifest_draft(
                self.paths,
                checkpoint,
                run_id=dispatch.run_id,
                emitted_at=stage_started_at,
            )
            self._advance_local_goalspec_checkpoint(
                checkpoint,
                next_stage=self._local_next_goalspec_stage(dispatch, checkpoint),
                queue_ownership=(
                    checkpoint.owned_queues[0] if checkpoint.owned_queues else self.state.queue_snapshot.ownerships[0]
                ),
                observed_at=stage_started_at,
            )
            self._release_execution_lock(observed_at=_utcnow())
            return
        elif checkpoint.node_id == "spec_synthesis":
            self._set_research_status(ResearchStatus.SPEC_SYNTHESIS_RUNNING)
            result = execute_spec_synthesis(
                self.paths,
                checkpoint,
                run_id=dispatch.run_id,
                emitted_at=stage_started_at,
            )
        elif checkpoint.node_id == "spec_interview":
            self._set_research_status(ResearchStatus.SPEC_INTERVIEW_RUNNING)
            result = execute_spec_interview(
                self.paths,
                checkpoint,
                run_id=dispatch.run_id,
                policy=self.config.research.interview_policy,
                emitted_at=stage_started_at,
            )
            if result.blocked:
                blocked_checkpoint = checkpoint.model_copy(
                    update={
                        "status": ResearchStatus.BLOCKED,
                        "updated_at": stage_started_at,
                        "owned_queues": (result.queue_ownership,),
                    }
                )
                queue_snapshot = self.state.queue_snapshot.model_copy(
                    update={
                        "ownerships": blocked_checkpoint.owned_queues,
                        "last_scanned_at": stage_started_at,
                        "selected_family": ResearchQueueFamily.GOALSPEC,
                        "selected_family_authority": ResearchQueueSelectionAuthority.CHECKPOINT,
                    }
                )
                self.state = self.state.model_copy(
                    update={
                        "updated_at": stage_started_at,
                        "queue_snapshot": queue_snapshot,
                        "retry_state": None,
                        "checkpoint": blocked_checkpoint,
                    }
                )
                self._persist_state()
                self._set_research_status(ResearchStatus.BLOCKED)
                self._release_execution_lock(observed_at=stage_started_at)
                return
        elif checkpoint.node_id == "spec_review":
            self._set_research_status(ResearchStatus.SPEC_REVIEW_RUNNING)
            result = execute_spec_review(
                self.paths,
                checkpoint,
                run_id=dispatch.run_id,
                emitted_at=stage_started_at,
                config=self.config,
                stage_plan=research_stage_for_node(dispatch.research_plan, checkpoint.node_id),
            )
            if result.escalated_to_goal_gap_remediation:
                self._advance_local_goalspec_checkpoint(
                    checkpoint,
                    next_stage=research_stage_for_node(dispatch.research_plan, "objective_profile_sync"),
                    queue_ownership=result.queue_ownership,
                    observed_at=stage_started_at,
                )
                self._release_execution_lock(observed_at=_utcnow())
                return
        elif checkpoint.node_id == _GOALSPEC_MECHANIC_NODE_ID:
            self._set_research_status(ResearchStatus.GOALSPEC_RUNNING)
            result = execute_spec_review_remediation(
                self.paths,
                checkpoint,
                run_id=dispatch.run_id,
                emitted_at=stage_started_at,
                config=self.config,
            )
        elif checkpoint.node_id == "taskmaster":
            self._set_research_status(ResearchStatus.TASKMASTER_RUNNING)
            taskmaster_result = execute_taskmaster(
                self.paths,
                checkpoint,
                dispatch=dispatch,
                run_id=dispatch.run_id,
                emitted_at=stage_started_at,
            )
            family_state = load_goal_spec_family_state(
                (self.paths.root / taskmaster_result.family_state_path)
                if not Path(taskmaster_result.family_state_path).is_absolute()
                else Path(taskmaster_result.family_state_path)
            )
            if family_state.family_complete and family_state.fulfills_initial_family_plan():
                self._advance_local_goalspec_checkpoint(
                    checkpoint,
                    next_stage=_GoalSpecSyntheticStage(
                        node_id=_GOALSPEC_TASKAUDIT_NODE_ID,
                        kind_id=_GOALSPEC_TASKAUDIT_KIND_ID,
                        running_status=ResearchStatus.TASKAUDIT_RUNNING.value,
                    ),
                    queue_ownership=checkpoint.owned_queues[0],
                    observed_at=stage_started_at,
                )
            else:
                self._complete_goalspec_checkpoint(checkpoint, observed_at=stage_started_at)
            self._release_execution_lock(observed_at=_utcnow())
            return
        elif checkpoint.node_id == _GOALSPEC_TASKAUDIT_NODE_ID:
            self._set_research_status(ResearchStatus.TASKAUDIT_RUNNING)
            taskaudit_result = execute_taskaudit(
                self.paths,
                run_id=dispatch.run_id,
                emitted_at=stage_started_at,
                defer_merge=True,
            )
            if taskaudit_result.status == "merged":
                self._complete_goalspec_checkpoint(checkpoint, observed_at=stage_started_at)
            self._release_execution_lock(observed_at=_utcnow())
            return
        else:
            self._release_execution_lock(observed_at=_utcnow())
            return

        next_stage = self._local_next_goalspec_stage(dispatch, checkpoint)
        self._advance_local_goalspec_checkpoint(
            checkpoint,
            next_stage=next_stage,
            queue_ownership=result.queue_ownership,
            observed_at=stage_started_at,
        )

        self._release_execution_lock(observed_at=_utcnow())

    def _execute_supported_incident_stages(
        self,
        dispatch: CompiledResearchDispatch,
        *,
        observed_at: datetime,
    ) -> None:
        checkpoint = self.state.checkpoint
        if not self._supports_incident_stage_execution(checkpoint):
            if checkpoint is not None and self._resume_selected_family(checkpoint) in {
                ResearchQueueFamily.INCIDENT,
                ResearchQueueFamily.BLOCKER,
            }:
                self._release_execution_lock(observed_at=observed_at)
            return

        while checkpoint is not None and checkpoint.node_id in {
            "incident_intake",
            "incident_resolve",
            "incident_archive",
        }:
            stage_started_at = _utcnow()
            if checkpoint.node_id == "incident_intake":
                result = execute_incident_intake(
                    self.paths,
                    checkpoint,
                    run_id=dispatch.run_id,
                    emitted_at=stage_started_at,
                )
            elif checkpoint.node_id == "incident_resolve":
                result = execute_incident_resolve(
                    self.paths,
                    checkpoint,
                    run_id=dispatch.run_id,
                    emitted_at=stage_started_at,
                )
                self._set_research_status(ResearchStatus.TASKMASTER_RUNNING)
                execute_incident_task_generation(
                    self.paths,
                    checkpoint,
                    dispatch=dispatch,
                    run_id=dispatch.run_id,
                    emitted_at=stage_started_at,
                    remediation_record_path=result.remediation_record_path,
                )
                self._set_research_status(ResearchStatus.INCIDENT_RESOLVE_RUNNING)
            elif checkpoint.node_id == "incident_archive":
                result = execute_incident_archive(
                    self.paths,
                    checkpoint,
                    run_id=dispatch.run_id,
                    emitted_at=stage_started_at,
                )
            else:
                break

            next_stage = self._next_incident_stage(dispatch, checkpoint)
            if next_stage is None:
                self._complete_incident_checkpoint(checkpoint, observed_at=stage_started_at)
                checkpoint = None
                continue
            checkpoint = self._advance_incident_checkpoint(
                checkpoint,
                next_stage=next_stage,
                queue_ownership=result.queue_ownership,
                observed_at=stage_started_at,
            )

        self._release_execution_lock(observed_at=_utcnow())

    def _execute_supported_audit_stages(
        self,
        dispatch: CompiledResearchDispatch,
        *,
        observed_at: datetime,
    ) -> None:
        checkpoint = self.state.checkpoint
        if not self._supports_audit_stage_execution(checkpoint):
            if checkpoint is not None and self._resume_selected_family(checkpoint) is ResearchQueueFamily.AUDIT:
                self._release_execution_lock(observed_at=observed_at)
            return

        while checkpoint is not None and checkpoint.node_id in {
            "audit_intake",
            "audit_validate",
            "audit_gatekeeper",
        }:
            stage_started_at = _utcnow()
            if checkpoint.node_id == "audit_intake":
                result = execute_audit_intake(
                    self.paths,
                    checkpoint,
                    run_id=dispatch.run_id,
                    emitted_at=stage_started_at,
                )
                next_stage = next_stage_for_success(dispatch.research_plan, checkpoint.node_id)
                checkpoint = self._advance_audit_checkpoint(
                    checkpoint,
                    next_stage=next_stage,
                    queue_ownership=self._queue_ownership_for_audit_path(
                        audit_path=self.paths.root / result.working_path,
                        run_id=dispatch.run_id,
                        emitted_at=stage_started_at,
                    ),
                    audit_record=result.audit_record,
                    observed_at=stage_started_at,
                )
                continue
            if checkpoint.node_id == "audit_validate":
                self._set_research_status(ResearchStatus.AUDIT_VALIDATE_RUNNING)
                result = execute_audit_validate(
                    self.paths,
                    checkpoint,
                    run_id=dispatch.run_id,
                    emitted_at=stage_started_at,
                )
                next_stage = next_stage_for_success(dispatch.research_plan, checkpoint.node_id)
                checkpoint = self._advance_audit_checkpoint(
                    checkpoint,
                    next_stage=next_stage,
                    queue_ownership=self._queue_ownership_for_audit_path(
                        audit_path=self.paths.root / result.working_path,
                        run_id=dispatch.run_id,
                        emitted_at=stage_started_at,
                    ),
                    audit_record=result.audit_record,
                    observed_at=stage_started_at,
                )
                continue
            if checkpoint.node_id == "audit_gatekeeper":
                result = execute_audit_gatekeeper(
                    self.paths,
                    checkpoint,
                    run_id=dispatch.run_id,
                    emitted_at=stage_started_at,
                )
                self._complete_audit_checkpoint(
                    checkpoint,
                    final_status=result.final_status,
                    observed_at=stage_started_at,
                )
                checkpoint = None
                continue
            break

        self._release_execution_lock(observed_at=_utcnow())

    def _new_run_id(self, label: str) -> str:
        return timestamped_slug_id(label, fallback="event", moment=_utcnow())


ResearchStubPlane = ResearchPlane


__all__ = ["ResearchLockUnavailableError", "ResearchPlane", "ResearchStubPlane"]
