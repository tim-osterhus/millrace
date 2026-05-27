from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from pydantic import BaseModel, ValidationError

from millrace_ai.contracts import (
    ActiveRunState,
    ExecutionStageName,
    ExecutionTerminalResult,
    Plane,
    ResultClass,
    RuntimeErrorCode,
    RuntimeFailureOrigin,
    StageResultEnvelope,
    TaskDocument,
)
from millrace_ai.events import read_runtime_events
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.compiled_plans import CompiledPlanAuthorityError
from millrace_ai.runtime.error_recovery import (
    load_runtime_error_context,
    record_post_stage_exception_context,
    schedule_post_stage_exception_recovery,
)
from millrace_ai.runtime.failure_policy import (
    RuntimeEffectFailurePolicyInput,
    RuntimeFailureBoundary,
    classify_failure_origin,
    interpret_runtime_effect_failure_policy,
)
from millrace_ai.runtime.supervisor import RuntimeDaemonSupervisor
from millrace_ai.state_store import load_snapshot, save_snapshot

NOW = datetime(2026, 4, 15, tzinfo=timezone.utc)


class _ValidationFixture(BaseModel):
    value: int


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _task_doc(task_id: str) -> TaskDocument:
    return TaskDocument(
        task_id=task_id,
        title=f"Task {task_id}",
        summary="failure policy task",
        target_paths=["src/millrace_ai/runtime/failure_policy.py"],
        acceptance=["failure origin is classified"],
        required_checks=["pytest tests/runtime/test_runtime_failure_policy.py -q"],
        references=["lab/specs/pending/2026-05-19-millrace-scheduler-lanes-and-context-implementation-plan.md"],
        risk=["unclassified runtime crash"],
        created_at=NOW,
        created_by="tests",
    )


def _runtime_effect_failure_policy(**updates) -> SimpleNamespace:
    policy = {
        "policy_id": "manager_partial_block",
        "applies_to_origins": ("runtime_effect",),
        "applies_to_planes": ("planning",),
        "applies_to_families": ("spec",),
        "applies_to_failure_classes": ("blueprint_partial_mutation",),
        "applies_to_mutation_phases": ("partial_mutation",),
        "applies_to_handler_ids": ("manager_blueprint_manifest_to_blueprint_drafts",),
        "applies_to_source_node_ids": ("manager_blueprint",),
        "applies_to_source_terminal_state_ids": (),
        "action": "block_source_work_item",
        "target_node_id": None,
        "target_terminal_state_id": None,
        "max_attempts": None,
        "incident_severity": None,
    }
    policy.update(updates)
    return SimpleNamespace(**policy)


def _manager_partial_mutation_failure() -> RuntimeEffectFailurePolicyInput:
    return RuntimeEffectFailurePolicyInput(
        failure_class="blueprint_partial_mutation",
        mutation_phase="partial_mutation",
        handler_id="manager_blueprint_manifest_to_blueprint_drafts",
        source_node_id="manager_blueprint",
        source_terminal_state_id="manager_blueprint_complete",
        source_plane="planning",
        source_family_id="spec",
        created_paths=("millrace-agents/blueprints/manifests/manifest-partial.json",),
        message="failed after writing a manifest",
    )


def test_runtime_effect_failure_policy_matches_partial_mutation_block_action() -> None:
    resolution = interpret_runtime_effect_failure_policy(
        (_runtime_effect_failure_policy(),),
        _manager_partial_mutation_failure(),
    )

    assert resolution is not None
    assert resolution.policy_id == "manager_partial_block"
    assert resolution.action == "block_source_work_item"
    assert resolution.failure_class == "blueprint_partial_mutation"


def test_runtime_effect_failure_policy_matches_operation_id_without_handler_id() -> None:
    failure = replace(
        _manager_partial_mutation_failure(),
        handler_id=None,
        operation_id="manager_blueprint_manifest_to_blueprint_drafts",
    )

    resolution = interpret_runtime_effect_failure_policy(
        (
            _runtime_effect_failure_policy(
                applies_to_handler_ids=(),
                applies_to_operation_ids=("manager_blueprint_manifest_to_blueprint_drafts",),
            ),
        ),
        failure,
    )

    assert resolution is not None
    assert resolution.policy_id == "manager_partial_block"


def test_runtime_effect_failure_policy_prefers_operation_id_over_handler_id() -> None:
    failure = replace(
        _manager_partial_mutation_failure(),
        handler_id="stale_legacy_handler",
        operation_id="manager_blueprint_manifest_to_blueprint_drafts",
        legacy_handler_id="stale_legacy_handler",
    )

    resolution = interpret_runtime_effect_failure_policy(
        (
            _runtime_effect_failure_policy(
                applies_to_operation_ids=("manager_blueprint_manifest_to_blueprint_drafts",),
                applies_to_handler_ids=("manager_blueprint_manifest_to_blueprint_drafts",),
            ),
        ),
        failure,
    )

    assert resolution is not None
    assert resolution.policy_id == "manager_partial_block"


def test_runtime_effect_failure_policy_matches_legacy_handler_alias() -> None:
    failure = replace(
        _manager_partial_mutation_failure(),
        handler_id=None,
        legacy_handler_id="manager_blueprint_manifest_to_blueprint_drafts",
    )

    resolution = interpret_runtime_effect_failure_policy(
        (_runtime_effect_failure_policy(),),
        failure,
    )

    assert resolution is not None
    assert resolution.policy_id == "manager_partial_block"


def test_runtime_effect_failure_policy_does_not_route_partial_mutation_to_node() -> None:
    resolution = interpret_runtime_effect_failure_policy(
        (
            _runtime_effect_failure_policy(
                policy_id="invalid_partial_route",
                action="route_to_node",
                target_node_id="mechanic_blueprint",
            ),
            _runtime_effect_failure_policy(policy_id="manager_partial_block"),
        ),
        _manager_partial_mutation_failure(),
    )

    assert resolution is not None
    assert resolution.policy_id == "manager_partial_block"
    assert resolution.action == "block_source_work_item"
    assert resolution.target_node_id is None


def test_failure_origin_mapping_covers_declared_origins() -> None:
    validation_error = None
    try:
        _ValidationFixture(value="not-int")
    except ValidationError as exc:
        validation_error = exc
    assert validation_error is not None

    cases = [
        (
            RuntimeError("model provider unavailable"),
            RuntimeFailureBoundary.RUNNER_INVOCATION,
            RuntimeFailureOrigin.MODEL_PROVIDER_UNAVAILABLE,
        ),
        (
            OSError("network unavailable"),
            RuntimeFailureBoundary.RUNNER_INVOCATION,
            RuntimeFailureOrigin.NETWORK_UNAVAILABLE,
        ),
        (
            RuntimeError("provider failed"),
            RuntimeFailureBoundary.REQUEST_CONTEXT,
            RuntimeFailureOrigin.REQUEST_CONTEXT_PROVIDER_FAILURE,
        ),
        (
            OSError("prompt context file missing"),
            RuntimeFailureBoundary.PROMPT_RENDER,
            RuntimeFailureOrigin.PROMPT_RENDER_FAILURE,
        ),
        (
            RuntimeError("primitive exploded"),
            RuntimeFailureBoundary.RUNTIME_PRIMITIVE,
            RuntimeFailureOrigin.RUNTIME_PRIMITIVE_EXCEPTION,
        ),
        (
            json.JSONDecodeError("bad json", "{}", 0),
            RuntimeFailureBoundary.DOCUMENT_ADAPTER,
            RuntimeFailureOrigin.DOCUMENT_ADAPTER_PARSE_FAILURE,
        ),
        (
            validation_error,
            RuntimeFailureBoundary.DOCUMENT_ADAPTER,
            RuntimeFailureOrigin.DOCUMENT_ADAPTER_VALIDATION_FAILURE,
        ),
        (
            OSError("disk full"),
            RuntimeFailureBoundary.FILESYSTEM_IO,
            RuntimeFailureOrigin.FILESYSTEM_IO_FAILURE,
        ),
        (
            CompiledPlanAuthorityError("bad plan", stale=False),
            RuntimeFailureBoundary.RESULT_APPLICATION,
            RuntimeFailureOrigin.WORKSPACE_INTEGRITY_FAILURE,
        ),
    ]

    for error, boundary, expected in cases:
        assert classify_failure_origin(error, boundary=boundary) is expected


def test_supervisor_runner_exception_records_failure_origin(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001"))

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise OSError("network unavailable")

    async def scenario() -> None:
        engine = RuntimeEngine(paths, stage_runner=stage_runner)
        engine.startup()
        supervisor = RuntimeDaemonSupervisor(engine)
        assert await supervisor.dispatch_ready_work() == 1
        completions = await supervisor.drain_completed(wait=True)
        assert completions[0].stage_result.metadata["failure_class"] == "network_unavailable"
        events = read_runtime_events(paths)
        assert any(
            event.event_type == "runtime_worker_exception"
            and event.data.get("failure_origin") == "network_unavailable"
            for event in events
        )
        engine.close()

    asyncio.run(scenario())


def test_post_stage_exception_context_persists_failure_origin(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=lambda request: (_ for _ in ()).throw(AssertionError(request)))
    engine.startup()
    stage_result = StageResultEnvelope(
        run_id="run-001",
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.BUILDER,
        node_id="builder",
        stage_kind_id="builder",
        work_item_kind="task",
        work_item_id="task-001",
        terminal_result=ExecutionTerminalResult.BUILDER_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BUILDER_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )

    record_post_stage_exception_context(
        engine,
        stage_result=stage_result,
        error=CompiledPlanAuthorityError("fingerprint mismatch", stale=False),
        router_decision=None,
        stage_result_path=None,
        error_code=RuntimeErrorCode.WORKSPACE_INTEGRITY_FAILURE,
        repair_stage=ExecutionStageName.TROUBLESHOOTER,
        captured_at=NOW,
    )

    context = load_runtime_error_context(paths)
    assert context is not None
    assert context.failure_origin is RuntimeFailureOrigin.WORKSPACE_INTEGRITY_FAILURE
    engine.close()


def test_post_stage_exception_recovery_updates_plane_indexed_queue_depths(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=lambda request: (_ for _ in ()).throw(AssertionError(request)))
    engine.startup()
    assert engine.snapshot is not None

    QueueStore(paths).enqueue_task(_task_doc("task-queued"))
    active_run = ActiveRunState(
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.BUILDER,
        node_id="builder",
        stage_kind_id="builder",
        run_id="run-001",
        compiled_plan_id=engine.snapshot.compiled_plan_id,
        compiled_plan_fingerprint=engine.snapshot.compiled_plan_fingerprint,
        request_kind="active_work_item",
        work_item_kind="task",
        work_item_id="task-active",
        active_since=NOW,
    )
    engine.snapshot = engine.snapshot.model_copy(
        update={
            "active_runs_by_plane": {Plane.EXECUTION: active_run},
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, engine.snapshot)
    stage_result = StageResultEnvelope(
        run_id="run-001",
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.BUILDER,
        node_id="builder",
        stage_kind_id="builder",
        work_item_kind="task",
        work_item_id="task-active",
        terminal_result=ExecutionTerminalResult.BUILDER_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BUILDER_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )

    schedule_post_stage_exception_recovery(
        engine,
        stage_result=stage_result,
        error=RuntimeError("post-stage mutation failed"),
        router_decision=None,
        stage_result_path=None,
    )

    snapshot = load_snapshot(paths)
    assert snapshot.queue_depth_execution == 1
    assert snapshot.queue_depths_by_plane[Plane.EXECUTION] == 1
    engine.close()
