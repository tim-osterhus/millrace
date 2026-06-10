from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import millrace_ai.runtime.stage_requests as stage_requests_module
from millrace_ai.architecture import WorkItemFamilyDefinition
from millrace_ai.contracts import (
    ClosureTargetState,
    ExecutionStageName,
    Plane,
    PlanningStageName,
    TaskDocument,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.runners import RunnerRawResult, StageRunRequest
from millrace_ai.runtime.activation import activate_claim_for_plane
from millrace_ai.runtime.engine import RuntimeEngine
from millrace_ai.runtime.stage_requests import planning_queue_depth
from millrace_ai.workspace.work_inventory import family_counts, queue_depths_by_plane

NOW = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
_BLUEPRINT_MODE_ID = "blueprint_" "codex"


def _task_doc(task_id: str) -> TaskDocument:
    return TaskDocument(
        task_id=task_id,
        title=f"Task {task_id}",
        summary="stage request test task",
        target_paths=["src/millrace_ai/runtime/stage_requests.py"],
        acceptance=["Stage request guard rejects invalid context provider implementations."],
        required_checks=["pytest tests/runtime/test_stage_requests.py -q"],
        references=["lab/specs/misc/maintainability-follow-up-refactor/00-index.md"],
        risk=["context provider misconfiguration"],
        created_at=NOW,
        created_by="tests",
    )


def _custom_review_family() -> WorkItemFamilyDefinition:
    return WorkItemFamilyDefinition(
        family_id="custom_review",
        plane=Plane.PLANNING,
        entry_key="custom_review",
        display_name="Custom Review",
        document_kind="custom_review",
        runtime_relative_dir="custom/reviews",
        file_extension=".json",
        schema_id="custom_review_document_v1",
        document_adapter_id="custom_review_json_v1",
        queue_lifecycle_adapter_id="tests.custom.review.adapter",
        queue_dirs={
            "queue": "custom/reviews/queue",
            "active": "custom/reviews/active",
            "done": "custom/reviews/done",
            "blocked": "custom/reviews/blocked",
            "canceled": "custom/reviews/canceled",
        },
        lifecycle_states=("queue", "active", "done", "blocked", "canceled"),
        claimable_state="queue",
        active_state="active",
        done_state="done",
        blocked_state="blocked",
        canceled_state="canceled",
        closure_blocking_states=("queue", "active", "blocked"),
        default_entry_key="custom_review",
        id_field="custom_id",
        created_at_field="created_at",
        lineage_fields=("root_spec_id",),
        operator_capabilities=("cancel", "retry", "inspect"),
    )


def test_planning_queue_depth_uses_shared_inventory_for_blueprint_drafts(tmp_path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    blueprint_queue = paths.runtime_root / "blueprints" / "drafts" / "queue"
    blueprint_queue.mkdir(parents=True, exist_ok=True)
    (blueprint_queue / "draft-001.json").write_text(json.dumps({"draft_id": "draft-001"}), encoding="utf-8")

    engine = SimpleNamespace(paths=paths, compiled_plan=None)

    assert planning_queue_depth(engine) == 1
    assert queue_depths_by_plane(paths)[Plane.PLANNING] == 1
    assert family_counts(paths)["blueprint_draft"]["queue"] == 1


def test_active_work_item_path_uses_family_adapter_for_blueprint_family(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))

    class _BlueprintAdapter:
        adapter_id = "builtin.queue_lifecycle.blueprint_draft"

        def active_path(self, paths, *, work_item_id: str):
            return paths.runtime_root / "planning" / "active-blueprints" / f"{work_item_id}.json"

    monkeypatch.setattr(
        stage_requests_module,
        "queue_adapter_for_id",
        lambda adapter_id: _BlueprintAdapter() if adapter_id == "builtin.queue_lifecycle.blueprint_draft" else None,
    )

    engine = SimpleNamespace(paths=paths, compiled_plan=None)
    active_path = stage_requests_module.active_work_item_path(
        engine,
        work_item_kind=None,
        work_item_id="draft-001",
        work_item_family_id="blueprint_draft",
    )

    assert active_path == paths.runtime_root / "planning" / "active-blueprints" / "draft-001.json"


def test_active_work_item_path_uses_custom_family_adapter_id_from_compiled_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    family = _custom_review_family()
    requested_adapter_ids: list[str] = []

    class _CustomAdapter:
        def active_path(self, paths, *, work_item_id: str):
            return paths.runtime_root / "custom" / "review-active" / f"{work_item_id}.json"

    def fake_queue_adapter_for_id(adapter_id: str):
        requested_adapter_ids.append(adapter_id)
        return _CustomAdapter() if adapter_id == family.queue_lifecycle_adapter_id else None

    monkeypatch.setattr(stage_requests_module, "queue_adapter_for_id", fake_queue_adapter_for_id)
    engine = SimpleNamespace(
        paths=paths,
        compiled_plan=SimpleNamespace(work_item_families_by_id={family.family_id: family}),
    )

    active_path = stage_requests_module.active_work_item_path(
        engine,
        work_item_kind=None,
        work_item_id="custom-001",
        work_item_family_id=family.family_id,
    )

    assert active_path == paths.runtime_root / "custom" / "review-active" / "custom-001.json"
    assert requested_adapter_ids == ["tests.custom.review.adapter"]


def test_graph_driven_stage_request_carries_model_alias_provenance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    (paths.runtime_root / "millrace.toml").write_text(
        "\n".join(
            [
                "[runtime]",
                f'default_mode = "{_BLUEPRINT_MODE_ID}"',
                "",
                "[model_assignment.by_loop]",
                '"planning.blueprint" = "deep"',
            ]
        ),
        encoding="utf-8",
    )

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage runner should not be invoked")

    monkeypatch.setattr(
        stage_requests_module,
        "attach_default_request_context",
        lambda *, workspace_root, request, compiled_plan: request,
    )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()
    assert engine.compiled_plan is not None

    target = ClosureTargetState(
        root_spec_id="spec-root-001",
        root_idea_id="idea-001",
        root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-root-001.md",
        root_idea_path="millrace-agents/arbiter/contracts/ideas/idea-001.md",
        rubric_path="millrace-agents/arbiter/rubrics/spec-root-001.md",
        latest_verdict_path="millrace-agents/arbiter/verdicts/spec-root-001.json",
        latest_report_path="millrace-agents/arbiter/reports/run-001.md",
        closure_open=True,
        closure_blocked_by_lineage_work=False,
        blocking_work_ids=(),
        opened_at=NOW,
    )
    contractor_plan = next(
        node
        for node in engine.compiled_plan.planning_graph.nodes
        if node.stage_kind_id == "contractor_blueprint"
    )

    request = engine._build_closure_target_stage_run_request(contractor_plan, target)

    assert request.stage is PlanningStageName.MANAGER
    assert request.stage_kind_id == "contractor_blueprint"
    assert request.model_name == "gpt-5.5"
    assert request.thinking_level == "xhigh"
    assert request.closure_target_root_source_kind == "idea"
    assert request.closure_target_root_source_id == "idea-001"
    assert request.closure_target_root_source_path == (
        "millrace-agents/arbiter/contracts/ideas/idea-001.md"
    )
    assert request.model_reasoning_effort == "xhigh"
    assert request.model_assignment_alias_id == "deep"
    assert request.model_assignment_source == "loop:planning.blueprint"


def test_stage_request_rejects_unregistered_context_provider_implementation(
    tmp_path,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-ctx-guard-001"))
    claim = queue.claim_next_execution_task()
    assert claim is not None

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        raise AssertionError("stage runner should not be invoked")

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.startup()
    activate_claim_for_plane(engine, claim, Plane.EXECUTION)
    assert engine.compiled_plan is not None
    stage_plan = engine._stage_plan_for(Plane.EXECUTION, ExecutionStageName.BUILDER)
    profile_id = stage_plan.request_context_profile_id
    assert profile_id is not None
    profile = engine.compiled_plan.request_context_profiles_by_id[profile_id]
    provider = engine.compiled_plan.request_context_providers_by_id[profile.provider_id]
    broken_provider = provider.model_copy(
        update={"python_registry_id": "runtime.context.missing.implementation"}
    )
    engine.compiled_plan = engine.compiled_plan.model_copy(
        update={
            "request_context_providers_by_id": {
                **engine.compiled_plan.request_context_providers_by_id,
                provider.provider_id: broken_provider,
            }
        }
    )

    with pytest.raises(ValueError, match="no registered runtime implementation exists"):
        engine._build_stage_run_request(stage_plan)

    engine.close()
