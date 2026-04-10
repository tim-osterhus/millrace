from __future__ import annotations

from pathlib import Path
import json

import pytest

from millrace_engine.config import build_runtime_paths, load_engine_config
from millrace_engine.control import EngineControl
from millrace_engine.contracts import PersistedObjectKind, RegistryObjectRef, ResearchStatus
from millrace_engine.events import EventRecord, EventSource, EventType
from millrace_engine.paths import RuntimePaths
from millrace_engine.planes.research import ResearchPlane, ResearchStubPlane
from millrace_engine.research.state import (
    ResearchCheckpoint,
    ResearchLockScope,
    ResearchLockState,
    ResearchQueueFamily,
    ResearchQueueSelectionAuthority,
    ResearchRuntimeMode,
    ResearchRuntimeState,
    ResearchStateStore,
    ResearchStageRetryState,
    load_research_runtime_state,
    write_research_runtime_state,
)
from tests.support import load_workspace_fixture, runtime_paths


def test_research_runtime_state_round_trips_shell_compatible_payload_and_checkpoint() -> None:
    state = ResearchRuntimeState.model_validate(
        {
            "schema_version": "1.0",
            "updated_at": "2026-03-19T12:00:00Z",
            "current_mode": "GOALSPEC",
            "last_mode": "AUDIT",
            "mode_reason": "goalspec-ready",
            "cycle_count": 4,
            "transition_count": 2,
            "queue_snapshot": {
                "goalspec_ready": True,
                "incident_ready": False,
                "blocker_ready": False,
                "audit_ready": True,
                "selected_family": "goalspec",
                "ownerships": [
                    {
                        "family": "goalspec",
                        "queue_path": "agents/ideas/raw",
                        "item_path": "agents/ideas/raw/goal.md",
                        "owner_token": "research-run-1",
                        "acquired_at": "2026-03-19T11:58:00Z",
                    }
                ],
                "last_scanned_at": "2026-03-19T11:59:00Z",
            },
            "pending": [
                {
                    "event_type": EventType.IDEA_SUBMITTED.value,
                    "received_at": "2026-03-19T11:57:00Z",
                    "payload": {"path": "agents/ideas/raw/goal.md"},
                    "breadcrumb_path": "agents/.deferred/2026-03-19T11-57-00Z__idea-submitted.json",
                    "queue_family": "goalspec",
                }
            ],
            "retry_state": {
                "attempt": 1,
                "max_attempts": 3,
                "backoff_seconds": 5.0,
                "next_retry_at": "2026-03-19T12:01:00Z",
                "last_failure_reason": "transient drift",
                "last_failure_signature": "mode=GOALSPEC|status=BLOCKED",
            },
            "lock_state": {
                "lock_key": "research",
                "owner_id": "run-1",
                "scope": "plane_run",
                "lock_path": "agents/.locks/research.lock",
                "acquired_at": "2026-03-19T11:56:00Z",
                "heartbeat_at": "2026-03-19T11:59:30Z",
                "expires_at": "2026-03-19T12:04:30Z",
            },
            "checkpoint": {
                "checkpoint_id": "research-goalspec-cycle-4",
                "mode": "GOALSPEC",
                "status": "GOALSPEC_RUNNING",
                "loop_ref": {
                    "kind": "loop_config",
                    "id": "research.goalspec",
                    "version": "1.0.0",
                },
                "node_id": "spec_review",
                "stage_kind_id": "research.spec-review",
                "attempt": 1,
                "started_at": "2026-03-19T11:58:30Z",
                "updated_at": "2026-03-19T12:00:00Z",
                "owned_queues": [
                    {
                        "family": "goalspec",
                        "queue_path": "agents/ideas/raw",
                        "item_path": "agents/ideas/raw/goal.md",
                        "owner_token": "research-run-1",
                        "acquired_at": "2026-03-19T11:58:00Z",
                    }
                ],
                "deferred_follow_ons": [
                    {
                        "event_type": EventType.IDEA_SUBMITTED.value,
                        "received_at": "2026-03-19T11:57:00Z",
                        "payload": {"path": "agents/ideas/raw/goal.md"},
                    }
                ],
            },
            "next_poll_at": "2026-03-19T12:02:00Z",
        }
    )

    assert state.current_mode is ResearchRuntimeMode.GOALSPEC
    assert state.last_mode is ResearchRuntimeMode.AUDIT
    assert state.queue_snapshot.selected_family is ResearchQueueFamily.GOALSPEC
    assert state.retry_state == ResearchStageRetryState.model_validate(
        {
            "attempt": 1,
            "max_attempts": 3,
            "backoff_seconds": 5.0,
            "next_retry_at": "2026-03-19T12:01:00Z",
            "last_failure_reason": "transient drift",
            "last_failure_signature": "mode=GOALSPEC|status=BLOCKED",
        }
    )
    assert state.next_poll_at == ResearchRuntimeState.model_validate(
        {
            "updated_at": "2026-03-19T12:00:00Z",
            "current_mode": "STUB",
            "last_mode": "STUB",
            "mode_reason": "bootstrap",
            "next_poll_at": "2026-03-19T12:02:00Z",
        }
    ).next_poll_at
    assert state.lock_state == ResearchLockState.model_validate(
        {
            "lock_key": "research",
            "owner_id": "run-1",
            "scope": ResearchLockScope.PLANE_RUN,
            "lock_path": "agents/.locks/research.lock",
            "acquired_at": "2026-03-19T11:56:00Z",
            "heartbeat_at": "2026-03-19T11:59:30Z",
            "expires_at": "2026-03-19T12:04:30Z",
        }
    )
    assert state.checkpoint == ResearchCheckpoint.model_validate(
        {
            "checkpoint_id": "research-goalspec-cycle-4",
            "mode": ResearchRuntimeMode.GOALSPEC,
            "status": ResearchStatus.GOALSPEC_RUNNING,
            "loop_ref": RegistryObjectRef(
                kind=PersistedObjectKind.LOOP_CONFIG,
                id="research.goalspec",
                version="1.0.0",
            ),
            "node_id": "spec_review",
            "stage_kind_id": "research.spec-review",
            "attempt": 1,
            "started_at": "2026-03-19T11:58:30Z",
            "updated_at": "2026-03-19T12:00:00Z",
            "owned_queues": (
                {
                    "family": "goalspec",
                    "queue_path": "agents/ideas/raw",
                    "item_path": "agents/ideas/raw/goal.md",
                    "owner_token": "research-run-1",
                    "acquired_at": "2026-03-19T11:58:00Z",
                },
            ),
            "deferred_follow_ons": (
                {
                    "event_type": EventType.IDEA_SUBMITTED.value,
                    "received_at": "2026-03-19T11:57:00Z",
                    "payload": {"path": "agents/ideas/raw/goal.md"},
                },
            ),
        }
    )

    dumped = state.model_dump(mode="json", exclude_none=True)
    assert dumped["current_mode"] == "GOALSPEC"
    assert dumped["last_mode"] == "AUDIT"
    assert dumped["deferred_requests"][0]["breadcrumb_path"].endswith("idea-submitted.json")
    assert dumped["checkpoint"]["loop_ref"]["id"] == "research.goalspec"
    assert dumped["next_poll_at"] == "2026-03-19T12:02:00Z"

    reloaded = ResearchRuntimeState.model_validate(dumped)
    assert reloaded == state


def test_research_queue_snapshot_rejects_checkpoint_authority_without_matching_owned_family() -> None:
    with pytest.raises(ValueError, match="checkpoint-selected family must be owned inside queue_snapshot"):
        ResearchRuntimeState.model_validate(
            {
                "updated_at": "2026-03-19T12:00:00Z",
                "current_mode": "GOALSPEC",
                "last_mode": "GOALSPEC",
                "mode_reason": "resume-from-checkpoint",
                "queue_snapshot": {
                    "goalspec_ready": False,
                    "incident_ready": False,
                    "blocker_ready": False,
                    "audit_ready": False,
                    "selected_family": "goalspec",
                    "selected_family_authority": "checkpoint",
                    "ownerships": [],
                },
            }
        )


def test_research_queue_snapshot_requires_fresh_readiness_without_checkpoint_authority() -> None:
    with pytest.raises(ValueError, match="selected_family must be ready inside queue_snapshot"):
        ResearchRuntimeState.model_validate(
            {
                "updated_at": "2026-03-19T12:00:00Z",
                "current_mode": "GOALSPEC",
                "last_mode": "GOALSPEC",
                "mode_reason": "forced-by-config",
                "queue_snapshot": {
                    "goalspec_ready": False,
                    "incident_ready": False,
                    "blocker_ready": False,
                    "audit_ready": False,
                    "selected_family": "goalspec",
                    "selected_family_authority": ResearchQueueSelectionAuthority.DISCOVERY.value,
                },
            }
        )


def test_research_stub_plane_exposes_typed_runtime_state_snapshot(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    paths = runtime_paths(config_path)
    research = ResearchStubPlane(paths)

    research.handle(
        EventRecord.model_validate(
            {
                "type": EventType.IDEA_SUBMITTED,
                "timestamp": "2026-03-19T12:30:00Z",
                "source": EventSource.ADAPTER,
                "payload": {"path": (workspace / "agents/ideas/raw/idea.md").as_posix()},
            }
        )
    )

    snapshot = research.snapshot_state()

    assert snapshot.current_mode is ResearchRuntimeMode.STUB
    assert snapshot.last_mode is ResearchRuntimeMode.STUB
    assert snapshot.mode_reason == "stub-plane-initialized"
    assert len(snapshot.deferred_requests) == 1
    assert snapshot.deferred_requests[0].breadcrumb_path is not None
    assert snapshot.deferred_requests[0].event_type is EventType.IDEA_SUBMITTED


def test_research_runtime_state_file_round_trips_deterministically(tmp_path: Path) -> None:
    state_path = tmp_path / "agents" / "research_state.json"
    state = ResearchRuntimeState.model_validate(
        {
            "updated_at": "2026-03-19T12:00:00Z",
            "current_mode": "STUB",
            "last_mode": "STUB",
            "mode_reason": "stub-plane-initialized",
            "deferred_requests": [
                {
                    "event_type": EventType.NEEDS_RESEARCH.value,
                    "received_at": "2026-03-19T11:57:00Z",
                    "payload": {"task_id": "task-123"},
                    "breadcrumb_path": "agents/.deferred/2026-03-19T11-57-00Z__needs-research.json",
                }
            ],
        }
    )

    write_research_runtime_state(state_path, state)
    first_text = state_path.read_text(encoding="utf-8")
    write_research_runtime_state(state_path, state)

    assert state_path.read_text(encoding="utf-8") == first_text
    assert load_research_runtime_state(state_path) == state


def test_research_runtime_state_round_trips_typed_incident_and_blocker_request_contracts(tmp_path: Path) -> None:
    state_path = tmp_path / "agents" / "research_state.json"
    state = ResearchRuntimeState.model_validate(
        {
            "updated_at": "2026-03-21T12:10:00Z",
            "current_mode": "AUTO",
            "last_mode": "STUB",
            "mode_reason": "incident-and-blocker-ready",
            "deferred_requests": [
                {
                    "event_type": EventType.IDEA_SUBMITTED.value,
                    "received_at": "2026-03-21T12:00:00Z",
                    "queue_family": "incident",
                    "payload": {"path": "agents/ideas/incidents/incoming/INC-401.md"},
                    "incident_document": {
                        "source_path": "agents/ideas/incidents/incoming/INC-401.md",
                        "incident_id": "INC-401",
                        "title": "Minimal incident contract",
                        "lifecycle_status": "incoming",
                        "severity": "S3",
                        "summary": "Carry typed incident metadata through state.",
                    },
                },
                {
                    "event_type": EventType.NEEDS_RESEARCH.value,
                    "received_at": "2026-03-21T12:05:00Z",
                    "queue_family": "blocker",
                    "payload": {"task_id": "2026-03-21__typed-blocker"},
                    "blocker_record": {
                        "ledger_path": "agents/tasksblocker.md",
                        "item_key": "agents/tasksblocker.md#20260321T120500Z__typed-blocker",
                        "occurred_at": "2026-03-21T12:05:00Z",
                        "task_title": "Typed blocker",
                        "status": "NEEDS_RESEARCH",
                        "stage_blocked": "consult",
                        "source_task": "agents/tasks.md :: ## 2026-03-21 - Typed blocker",
                        "incident_path": "agents/ideas/incidents/incoming/INC-401.md",
                        "next_action": "Route to incident intake.",
                    },
                },
                {
                    "event_type": EventType.AUDIT_REQUESTED.value,
                    "received_at": "2026-03-21T12:08:00Z",
                    "queue_family": "audit",
                    "payload": {"path": "agents/ideas/audit/incoming/AUD-401.md"},
                    "audit_record": {
                        "source_path": "agents/ideas/audit/incoming/AUD-401.md",
                        "audit_id": "AUD-401",
                        "title": "Audit AUD-401",
                        "scope": "explicit-audit-contract",
                        "trigger": "manual",
                        "lifecycle_status": "incoming",
                        "owner": "research-plane",
                        "created_at": "2026-03-21T12:08:00Z",
                        "updated_at": "2026-03-21T12:08:00Z",
                    },
                },
            ],
        }
    )

    write_research_runtime_state(state_path, state)
    reloaded = load_research_runtime_state(state_path)

    assert reloaded is not None
    assert reloaded.deferred_requests[0].incident_document is not None
    assert reloaded.deferred_requests[0].incident_document.incident_id == "INC-401"
    assert reloaded.deferred_requests[1].blocker_record is not None
    assert reloaded.deferred_requests[1].blocker_record.incident_path == Path(
        "agents/ideas/incidents/incoming/INC-401.md"
    )
    assert reloaded.deferred_requests[2].audit_record is not None
    assert reloaded.deferred_requests[2].event_type is EventType.AUDIT_REQUESTED
    assert reloaded.deferred_requests[2].audit_record.audit_id == "AUD-401"
    assert reloaded.deferred_requests[2].queue_family is ResearchQueueFamily.AUDIT
    dumped = reloaded.model_dump(mode="json", exclude_none=True)
    assert dumped["deferred_requests"][0]["incident_document"]["title"] == "Minimal incident contract"
    assert dumped["deferred_requests"][1]["blocker_record"]["status"] == "NEEDS_RESEARCH"
    assert dumped["deferred_requests"][2]["audit_record"]["trigger"] == "manual"


def test_research_runtime_state_loader_repairs_initialized_null_updated_at_deterministically(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "agents" / "research_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "updated_at": None,
                "current_mode": "AUDIT",
                "last_mode": "AUDIT",
                "mode_reason": "initialized",
                "cycle_count": 0,
                "transition_count": 0,
                "queue_snapshot": {
                    "goalspec_ready": False,
                    "incident_ready": False,
                    "audit_ready": False,
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    first = load_research_runtime_state(state_path)
    second = load_research_runtime_state(state_path)

    assert first is not None
    assert second is not None
    assert first == second
    assert first.mode_reason == "initialized"
    assert first.updated_at is not None


def test_research_stub_breadcrumbs_remain_restart_safe_for_same_second_duplicate_events(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    paths = runtime_paths(config_path)
    research = ResearchStubPlane(paths)
    event = EventRecord.model_validate(
        {
            "type": EventType.IDEA_SUBMITTED,
            "timestamp": "2026-03-19T12:30:00Z",
            "source": EventSource.ADAPTER,
            "payload": {"path": (workspace / "agents/ideas/raw/idea.md").as_posix()},
        }
    )

    research.handle(event)
    research.handle(event)

    deferred_files = sorted(paths.deferred_dir.glob("*.json"))
    assert len(deferred_files) == 2
    assert deferred_files[0].name != deferred_files[1].name

    paths.research_state_file.unlink()
    restarted = ResearchStubPlane(paths)

    assert len(restarted.snapshot_state().deferred_requests) == 2


def test_research_control_report_round_trips_deterministically_across_restart_reads(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace('mode = "stub"', 'mode = "auto"', 1),
        encoding="utf-8",
    )
    incident_path = workspace / "agents" / "ideas" / "incidents" / "incoming" / "incident.md"
    incident_path.parent.mkdir(parents=True, exist_ok=True)
    incident_path.write_text("# incident\n", encoding="utf-8")

    loaded = load_engine_config(config_path)
    paths = build_runtime_paths(loaded.config)
    plane = ResearchPlane(loaded.config, paths)
    try:
        plane.dispatch_ready_work(run_id="research-auto-run", resolve_assets=False)
        first_report = EngineControl(config_path).research_report()
        first_payload = first_report.model_dump(mode="json")
        second_payload = EngineControl(config_path).research_report().model_dump(mode="json")
    finally:
        plane.shutdown()

    assert first_payload == second_payload
    assert first_payload["configured_mode"] == "auto"
    assert first_payload["status"] == "INCIDENT_INTAKE_RUNNING"
    assert first_payload["runtime"]["current_mode"] == "INCIDENT"
    assert first_payload["runtime"]["checkpoint"]["checkpoint_id"] == "research-auto-run"
    assert first_payload["queue_families"][1]["family"] == "incident"
    assert first_payload["queue_families"][1]["item_count"] == 1
    assert first_payload["queue_families"][1]["ownerships"][0]["owner_token"] == "research-auto-run"
    assert first_payload["deferred_breadcrumb_count"] == len(list(paths.deferred_dir.iterdir()))
    assert first_report.__class__.model_validate(first_payload) == first_report


def test_research_state_store_bootstrap_clears_stale_lock_metadata(tmp_path: Path) -> None:
    state_path = tmp_path / "agents" / "research_state.json"
    state = ResearchRuntimeState.model_validate(
        {
            "updated_at": "2026-03-19T12:00:00Z",
            "current_mode": "AUTO",
            "last_mode": "STUB",
            "mode_reason": "resume-from-checkpoint",
            "lock_state": {
                "lock_key": "research-loop",
                "owner_id": "stale-owner",
                "scope": "plane_run",
                "lock_path": "agents/.locks/research_loop.lock",
                "acquired_at": "2026-03-19T11:59:00Z",
                "heartbeat_at": "2026-03-19T12:00:00Z",
                "expires_at": "2026-03-19T12:10:00Z",
            },
        }
    )
    write_research_runtime_state(state_path, state)

    store = ResearchStateStore(state_path, deferred_dir=tmp_path / "agents" / ".deferred")
    bootstrapped = store.bootstrap(mode_reason="research-plane-initialized")

    assert bootstrapped.lock_state is None
    persisted = load_research_runtime_state(state_path)
    assert persisted is not None
    assert persisted.lock_state is None


def test_research_runtime_state_does_not_retry_when_budget_is_exhausted() -> None:
    state = ResearchRuntimeState.model_validate(
        {
            "updated_at": "2026-03-19T12:00:00Z",
            "current_mode": "AUTO",
            "last_mode": "AUTO",
            "mode_reason": "transient compile failure",
            "retry_state": {
                "attempt": 2,
                "max_attempts": 2,
                "backoff_seconds": 0.0,
                "next_retry_at": None,
                "last_failure_reason": "transient compile failure",
                "last_failure_signature": "CompiledResearchDispatchError:transient compile failure",
            },
        }
    )

    assert state.retry_state is not None
    assert state.retry_state.exhausted() is True
    assert state.retry_due(state.updated_at) is False


def test_research_stub_plane_restores_persisted_state_on_restart(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    paths = runtime_paths(config_path)
    first_plane = ResearchStubPlane(paths)

    first_plane.handle(
        EventRecord.model_validate(
            {
                "type": EventType.IDEA_SUBMITTED,
                "timestamp": "2026-03-19T12:30:00Z",
                "source": EventSource.ADAPTER,
                "payload": {"path": (workspace / "agents/ideas/raw/idea.md").as_posix()},
            }
        )
    )

    restarted_plane = ResearchStubPlane(paths)
    snapshot = restarted_plane.snapshot_state()

    assert paths.research_state_file.exists()
    assert len(snapshot.deferred_requests) == 1
    assert snapshot.deferred_requests[0].event_type is EventType.IDEA_SUBMITTED
    assert snapshot.deferred_requests[0].breadcrumb_path is not None


def test_research_stub_plane_reconfigure_rebinds_breadcrumb_paths(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    first_paths = RuntimePaths.from_workspace(workspace_root, workspace_root / "agents-a")
    second_paths = RuntimePaths.from_workspace(workspace_root, workspace_root / "agents-b")
    plane = ResearchStubPlane(first_paths)

    plane.handle(
        EventRecord.model_validate(
            {
                "type": EventType.IDEA_SUBMITTED,
                "timestamp": "2026-03-19T12:30:00Z",
                "source": EventSource.ADAPTER,
                "payload": {"path": "agents-a/ideas/raw/idea.md"},
            }
        )
    )

    original_breadcrumb_path = plane.snapshot_state().deferred_requests[0].breadcrumb_path
    assert original_breadcrumb_path is not None
    assert original_breadcrumb_path.exists()

    plane.reconfigure(second_paths)

    snapshot = plane.snapshot_state()
    rebound_breadcrumb_path = snapshot.deferred_requests[0].breadcrumb_path

    assert rebound_breadcrumb_path == second_paths.deferred_dir / original_breadcrumb_path.name
    assert rebound_breadcrumb_path is not None
    assert rebound_breadcrumb_path.exists()
    assert load_research_runtime_state(second_paths.research_state_file) == snapshot
