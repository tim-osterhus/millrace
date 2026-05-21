from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.contracts import ExecutionStageName, Plane, PlanningStageName, TaskDocument
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.activation import activate_claim_for_plane
from millrace_ai.runtime.request_context import RequestContextRenderPlan, render_request_context
from millrace_ai.state_store import load_snapshot

NOW = datetime(2026, 4, 15, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _unused_stage_runner(request: StageRunRequest) -> RunnerRawResult:
    raise AssertionError(f"stage runner should not be called in request context tests: {request.stage.value}")


def _task_doc(task_id: str) -> TaskDocument:
    return TaskDocument(
        task_id=task_id,
        title=f"Task {task_id}",
        summary="request context task",
        target_paths=["src/millrace_ai/runtime/request_context.py"],
        acceptance=["context renders deterministically"],
        required_checks=["pytest tests/runtime/test_request_context.py -q"],
        references=["lab/specs/pending/2026-05-19-millrace-scheduler-lanes-and-context-implementation-plan.md"],
        risk=["prompt visibility drift"],
        created_at=NOW,
        created_by="tests",
    )


def test_request_context_render_excludes_operator_only_refs(tmp_path: Path) -> None:
    plan = RequestContextRenderPlan(
        render_plan_id="contractor_blueprint.v1",
        context_bundle_path="runs/run-001/context/context.json",
        visible_artifact_refs=("draft:blueprint-001",),
        operator_only_artifact_refs=("spec:root-spec",),
        inline_sections=("active_work_item",),
        omitted_provider_ids=("full_manifest",),
    )

    first = render_request_context(plan, workspace_root=tmp_path)
    second = render_request_context(plan, workspace_root=tmp_path)

    assert first.text == second.text
    assert "blueprint-001" in first.text
    assert "root-spec" not in first.text
    assert first.manifest["omitted_provider_ids"] == ["full_manifest"]
    assert Path(first.rendered_prompt_context_path).is_file()


def test_stage_run_request_writes_default_context_artifacts(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001"))
    claim = queue.claim_next_execution_task()
    assert claim is not None
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()
    activate_claim_for_plane(engine, claim, Plane.EXECUTION)

    request = engine._build_stage_run_request(
        engine._stage_plan_for(Plane.EXECUTION, ExecutionStageName.BUILDER)
    )
    snapshot = load_snapshot(paths)

    assert request.request_context_profile_id == "builder.default"
    assert request.context_render_plan_id == "stage_request.default.v1"
    assert request.context_bundle_path is not None
    assert request.rendered_prompt_context_path is not None
    assert request.context_artifact_refs == ("task:task-001",)
    assert Path(request.context_bundle_path).is_file()
    assert Path(request.rendered_prompt_context_path).is_file()
    assert snapshot.active_run_id == request.run_id
    engine.close()


def test_stage_plan_lookup_resolves_custom_stage_kind_by_runtime_stage(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_codex")
    engine.startup()

    mechanic_plan = engine._stage_plan_for(Plane.PLANNING, PlanningStageName.MECHANIC)

    assert mechanic_plan.node_id == "mechanic_blueprint"
    assert mechanic_plan.stage_kind_id == "mechanic_blueprint"
    engine.close()
