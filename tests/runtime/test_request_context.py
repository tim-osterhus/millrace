from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from millrace_ai.assets.workflows import load_builtin_workflow_primitives
from millrace_ai.compilation.outcomes import CompilerValidationError
from millrace_ai.compilation.validation.request_context_profiles import (
    validate_request_context_profiles,
)
from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import (
    ExecutionStageName,
    LearningStageName,
    Plane,
    PlanningStageName,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.activation import activate_claim_for_plane
from millrace_ai.runtime.request_context import (
    RequestContextRenderPlan,
    attach_default_request_context,
    render_request_context,
)
from millrace_ai.state_store import load_snapshot

NOW = datetime(2026, 4, 15, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _unused_stage_runner(request: StageRunRequest) -> RunnerRawResult:
    raise AssertionError(f"stage runner should not be called in request context tests: {request.stage.value}")


def _context_bundle(request: StageRunRequest) -> dict[str, object]:
    assert request.context_bundle_path is not None
    return json.loads(Path(request.context_bundle_path).read_text(encoding="utf-8"))


def _render_manifest(request: StageRunRequest) -> dict[str, object]:
    assert request.rendered_prompt_context_path is not None
    manifest_path = Path(request.rendered_prompt_context_path).with_name("render_manifest.json")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


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


def test_stage_run_request_backfills_missing_node_profile_and_render_plan(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-legacy-ctx-001"))
    claim = queue.claim_next_execution_task()
    assert claim is not None

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()
    activate_claim_for_plane(engine, claim, Plane.EXECUTION)
    assert engine.compiled_plan is not None

    legacy_execution_graph = engine.compiled_plan.execution_graph.model_copy(
        update={
            "nodes": tuple(
                node.model_copy(
                    update={
                        "request_context_profile_id": None,
                        "context_render_plan_id": None,
                    }
                )
                if node.node_id == "builder"
                else node
                for node in engine.compiled_plan.execution_graph.nodes
            ),
        }
    )
    engine.compiled_plan = engine.compiled_plan.model_copy(
        update={
            "execution_graph": legacy_execution_graph,
            "graphs_by_plane": {
                **engine.compiled_plan.graphs_by_plane,
                Plane.EXECUTION: legacy_execution_graph,
            },
        }
    )

    stage_plan = engine._stage_plan_for(Plane.EXECUTION, ExecutionStageName.BUILDER)
    assert stage_plan.request_context_profile_id is None
    assert stage_plan.context_render_plan_id is None

    request = engine._build_stage_run_request(stage_plan)
    bundle = _context_bundle(request)
    manifest = _render_manifest(request)

    assert request.request_context_profile_id == "builder.default"
    assert request.context_render_plan_id == "stage_request.default.v1"
    assert bundle["profile_id"] == "builder.default"
    assert bundle["render_plan_id"] == "stage_request.default.v1"
    assert manifest["profile_id"] == "builder.default"
    assert manifest["render_plan_id"] == "stage_request.default.v1"
    engine.close()


@pytest.mark.parametrize(
    (
        "stage",
        "plane",
        "node_id",
        "stage_kind_id",
        "work_item_kind",
        "work_item_family_id",
        "work_item_id",
        "expected_profile_id",
    ),
    (
        (
            PlanningStageName.PLANNER,
            Plane.PLANNING,
            "planner",
            "planner",
            WorkItemKind.SPEC,
            WorkItemKind.SPEC.value,
            "spec-001",
            "planner.default",
        ),
        (
            PlanningStageName.RECON,
            Plane.PLANNING,
            "recon",
            "recon",
            WorkItemKind.PROBE,
            WorkItemKind.PROBE.value,
            "probe-001",
            "recon.default",
        ),
        (
            ExecutionStageName.INTEGRATOR,
            Plane.EXECUTION,
            "integrator",
            "integrator",
            WorkItemKind.TASK,
            WorkItemKind.TASK.value,
            "task-001",
            "integrator.default",
        ),
        (
            LearningStageName.ANALYST,
            Plane.LEARNING,
            "analyst",
            "analyst",
            WorkItemKind.LEARNING_REQUEST,
            WorkItemKind.LEARNING_REQUEST.value,
            "learning-request-001",
            "analyst.default",
        ),
    ),
)
def test_generic_context_provider_parity_across_stage_families(
    tmp_path: Path,
    stage,
    plane: Plane,
    node_id: str,
    stage_kind_id: str,
    work_item_kind: WorkItemKind,
    work_item_family_id: str,
    work_item_id: str,
    expected_profile_id: str,
) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(
        paths,
        stage_runner=_unused_stage_runner,
        mode_id="learning_codex_integrated",
    )
    engine.startup()
    assert engine.compiled_plan is not None

    active_path_by_family = {
        WorkItemKind.TASK.value: paths.tasks_active_dir / f"{work_item_id}.md",
        WorkItemKind.PROBE.value: paths.probes_active_dir / f"{work_item_id}.md",
        WorkItemKind.SPEC.value: paths.specs_active_dir / f"{work_item_id}.md",
        WorkItemKind.LEARNING_REQUEST.value: paths.learning_requests_active_dir / f"{work_item_id}.md",
    }
    active_path = active_path_by_family[work_item_family_id]
    request = StageRunRequest(
        request_id=f"req-parity-{stage_kind_id}",
        run_id=f"run-parity-{stage_kind_id}",
        plane=plane,
        stage=stage,
        request_kind="learning_request"
        if work_item_kind is WorkItemKind.LEARNING_REQUEST
        else "active_work_item",
        mode_id=engine.compiled_plan.mode_id,
        compiled_plan_id=engine.compiled_plan.compiled_plan_id,
        node_id=node_id,
        stage_kind_id=stage_kind_id,
        entrypoint_path=f"millrace-agents/entrypoints/{plane.value}/{stage_kind_id}.md",
        active_work_item_family_id=work_item_family_id,
        active_work_item_kind=work_item_kind,
        active_work_item_id=work_item_id,
        active_work_item_path=str(active_path),
        run_dir=str(paths.runs_dir / f"run-parity-{stage_kind_id}"),
        summary_status_path=str(paths.execution_status_file),
        runtime_snapshot_path=str(paths.runtime_snapshot_file),
        recovery_counters_path=str(paths.recovery_counters_file),
    )

    enriched = attach_default_request_context(
        workspace_root=paths.root,
        request=request,
        compiled_plan=engine.compiled_plan,
    )
    bundle = _context_bundle(enriched)
    manifest = _render_manifest(enriched)
    expected_ref = f"{work_item_family_id}:{work_item_id}"

    assert enriched.request_context_profile_id == expected_profile_id
    assert enriched.context_render_plan_id == "stage_request.default.v1"
    assert enriched.context_artifact_refs == (expected_ref,)
    assert bundle["profile_id"] == expected_profile_id
    assert bundle["render_plan_id"] == "stage_request.default.v1"
    assert bundle["visible_artifact_refs"] == [expected_ref]
    assert bundle["included_provider_ids"] == []
    assert bundle["redacted_provider_ids"] == []
    assert bundle["omitted_provider_ids"] == []
    assert manifest["profile_id"] == expected_profile_id
    assert manifest["render_plan_id"] == "stage_request.default.v1"
    assert manifest["visible_artifact_refs"] == [expected_ref]
    assert manifest["redacted_artifact_refs"] == [
        f"runtime_snapshot:{request.runtime_snapshot_path}",
        f"recovery_counters:{request.recovery_counters_path}",
    ]
    engine.close()


def test_default_request_context_uses_closure_target_ref_without_active_work_item(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    request = StageRunRequest(
        request_id="req-closure-001",
        run_id="run-closure-001",
        plane=Plane.PLANNING,
        stage=PlanningStageName.ARBITER,
        request_kind="closure_target",
        mode_id="standard_plain",
        compiled_plan_id="plan-001",
        node_id="arbiter",
        stage_kind_id="arbiter",
        running_status_marker="ARBITER_RUNNING",
        legal_terminal_markers=("### ARBITER_COMPLETE", "### BLOCKED"),
        allowed_result_classes_by_outcome={
            "ARBITER_COMPLETE": ("success",),
            "BLOCKED": ("blocked",),
        },
        entrypoint_path="millrace-agents/entrypoints/planning/arbiter.md",
        closure_target_path="millrace-agents/arbiter/targets/spec-root-001.json",
        closure_target_root_spec_id="spec-root-001",
        closure_target_root_source_kind="idea",
        closure_target_root_source_id="idea-001",
        closure_target_root_source_path="ideas/done/idea-001.md",
        closure_target_root_idea_id="idea-001",
        canonical_root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-root-001.md",
        canonical_seed_idea_path="millrace-agents/arbiter/contracts/ideas/idea-001.md",
        preferred_rubric_path="millrace-agents/arbiter/rubrics/spec-root-001.md",
        preferred_verdict_path="millrace-agents/arbiter/verdicts/spec-root-001.json",
        preferred_report_path=str(paths.runs_dir / "run-closure-001" / "arbiter_report.md"),
        run_dir=str(paths.runs_dir / "run-closure-001"),
        summary_status_path=str(paths.planning_status_file),
        runtime_snapshot_path=str(paths.runtime_snapshot_file),
        recovery_counters_path=str(paths.recovery_counters_file),
    )

    enriched = attach_default_request_context(workspace_root=paths.root, request=request)

    assert enriched.request_context_profile_id == "arbiter.default"
    assert enriched.context_render_plan_id == "closure_target.default.v1"
    assert enriched.context_artifact_refs == ("closure_target:spec-root-001",)
    assert enriched.active_work_item_id is None
    assert enriched.active_work_item_path is None
    assert enriched.context_bundle_path is not None
    assert Path(enriched.context_bundle_path).is_file()


def test_stage_plan_lookup_resolves_custom_stage_kind_by_runtime_stage(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_codex")
    engine.startup()

    mechanic_plan = engine._stage_plan_for(Plane.PLANNING, PlanningStageName.MECHANIC)

    assert mechanic_plan.node_id == "mechanic_blueprint"
    assert mechanic_plan.stage_kind_id == "mechanic_blueprint"
    engine.close()


def test_request_context_prefers_compiled_node_profile_authority(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-ctx-001"))
    claim = queue.claim_next_execution_task()
    assert claim is not None
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()
    activate_claim_for_plane(engine, claim, Plane.EXECUTION)

    builder_plan = engine._stage_plan_for(Plane.EXECUTION, ExecutionStageName.BUILDER)
    staged = engine._build_stage_run_request(builder_plan)
    reset_request = staged.model_copy(
        update={
            "node_id": "custom-builder-node",
            "request_context_profile_id": None,
            "context_bundle_path": None,
            "context_artifact_refs": (),
            "context_render_plan_id": None,
            "rendered_prompt_context_path": None,
        }
    )
    assert engine.compiled_plan is not None
    profile = engine.compiled_plan.request_context_profiles_by_id["integrator.default"]
    provider = engine.compiled_plan.request_context_providers_by_id[profile.provider_id]
    render_plan = engine.compiled_plan.request_context_render_plans_by_id[
        "stage_request.default.v1"
    ]
    compiled_authority = SimpleNamespace(
        compiled_plan_id=reset_request.compiled_plan_id,
        request_context_profiles_by_id={"integrator.default": profile},
        request_context_providers_by_id={provider.provider_id: provider},
        request_context_render_plans_by_id={render_plan.render_plan_id: render_plan},
        graphs_by_plane={
            Plane.EXECUTION: SimpleNamespace(
                nodes=(
                    SimpleNamespace(
                        plane=Plane.EXECUTION,
                        node_id="custom-builder-node",
                        stage_kind_id="builder",
                        request_context_profile_id="integrator.default",
                        context_render_plan_id="stage_request.default.v1",
                    ),
                )
            )
        },
        artifact_contracts_by_id={},
    )

    enriched = attach_default_request_context(
        workspace_root=paths.root,
        request=reset_request,
        compiled_plan=compiled_authority,
    )

    assert enriched.request_context_profile_id == "integrator.default"
    assert enriched.context_render_plan_id == "stage_request.default.v1"
    assert enriched.context_artifact_refs == ("task:task-ctx-001",)
    engine.close()


def test_request_context_rejects_unknown_node_profile_id(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-ctx-unknown-profile-001"))
    claim = queue.claim_next_execution_task()
    assert claim is not None

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()
    activate_claim_for_plane(engine, claim, Plane.EXECUTION)
    assert engine.compiled_plan is not None

    request = engine._build_stage_run_request(
        engine._stage_plan_for(Plane.EXECUTION, ExecutionStageName.BUILDER)
    ).model_copy(
        update={
            "request_context_profile_id": None,
            "context_bundle_path": None,
            "context_artifact_refs": (),
            "context_render_plan_id": None,
            "rendered_prompt_context_path": None,
        }
    )

    compiled_authority = SimpleNamespace(
        compiled_plan_id=request.compiled_plan_id,
        request_context_profiles_by_id=engine.compiled_plan.request_context_profiles_by_id,
        request_context_providers_by_id=engine.compiled_plan.request_context_providers_by_id,
        request_context_render_plans_by_id=engine.compiled_plan.request_context_render_plans_by_id,
        graphs_by_plane={
            Plane.EXECUTION: SimpleNamespace(
                nodes=(
                    SimpleNamespace(
                        plane=Plane.EXECUTION,
                        node_id=request.node_id,
                        stage_kind_id=request.stage_kind_id,
                        request_context_profile_id="ghost.default",
                        context_render_plan_id="stage_request.default.v1",
                    ),
                )
            )
        },
        artifact_contracts_by_id={},
    )

    with pytest.raises(ValueError, match="request context profile 'ghost.default' is unavailable"):
        attach_default_request_context(
            workspace_root=paths.root,
            request=request,
            compiled_plan=compiled_authority,
        )

    engine.close()


def test_request_context_rejects_unknown_node_render_plan_id(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-ctx-unknown-render-plan-001"))
    claim = queue.claim_next_execution_task()
    assert claim is not None

    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()
    activate_claim_for_plane(engine, claim, Plane.EXECUTION)
    assert engine.compiled_plan is not None

    request = engine._build_stage_run_request(
        engine._stage_plan_for(Plane.EXECUTION, ExecutionStageName.BUILDER)
    ).model_copy(
        update={
            "request_context_profile_id": None,
            "context_bundle_path": None,
            "context_artifact_refs": (),
            "context_render_plan_id": None,
            "rendered_prompt_context_path": None,
        }
    )

    compiled_authority = SimpleNamespace(
        compiled_plan_id=request.compiled_plan_id,
        request_context_profiles_by_id=engine.compiled_plan.request_context_profiles_by_id,
        request_context_providers_by_id=engine.compiled_plan.request_context_providers_by_id,
        request_context_render_plans_by_id=engine.compiled_plan.request_context_render_plans_by_id,
        graphs_by_plane={
            Plane.EXECUTION: SimpleNamespace(
                nodes=(
                    SimpleNamespace(
                        plane=Plane.EXECUTION,
                        node_id=request.node_id,
                        stage_kind_id=request.stage_kind_id,
                        request_context_profile_id="builder.default",
                        context_render_plan_id="ghost.render_plan",
                    ),
                )
            )
        },
        artifact_contracts_by_id={},
    )

    with pytest.raises(
        ValueError,
        match="request context render plan 'ghost.render_plan' is unavailable",
    ):
        attach_default_request_context(
            workspace_root=paths.root,
            request=request,
            compiled_plan=compiled_authority,
        )

    engine.close()


def _validation_inputs(tmp_path: Path):
    paths = _workspace(tmp_path)
    outcome = compile_and_persist_workspace_plan(
        paths.root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.active_plan is not None
    return outcome.active_plan, load_builtin_workflow_primitives()


def test_request_context_validation_rejects_provider_profile_request_kind_mismatch(
    tmp_path: Path,
) -> None:
    active_plan, primitives = _validation_inputs(tmp_path)
    mutated_profiles = tuple(
        profile.model_copy(update={"request_kind": "closure_target"})
        if profile.profile_id == "builder.default"
        else profile
        for profile in primitives.request_context_profiles
    )
    mutated_primitives = replace(primitives, request_context_profiles=mutated_profiles)

    with pytest.raises(
        CompilerValidationError,
        match=(
            "request context profile builder.default request kind closure_target is not "
            "supported by provider generic.active_work_item"
        ),
    ):
        validate_request_context_profiles(
            artifact_contracts_by_id=active_plan.artifact_contracts_by_id,
            graphs_by_plane=active_plan.graphs_by_plane,
            request_context_profiles_by_id=active_plan.request_context_profiles_by_id,
            request_context_providers_by_id=active_plan.request_context_providers_by_id,
            request_context_render_plans_by_id=active_plan.request_context_render_plans_by_id,
            workflow_primitives=mutated_primitives,
        )


def test_request_context_validation_rejects_missing_provider_capability(
    tmp_path: Path,
) -> None:
    active_plan, primitives = _validation_inputs(tmp_path)
    default_render_plan = active_plan.request_context_render_plans_by_id[
        "stage_request.default.v1"
    ]
    mutated_render_plans_by_id = {
        **active_plan.request_context_render_plans_by_id,
        default_render_plan.render_plan_id: default_render_plan.model_copy(
            update={
                "required_provider_capabilities": (
                    *default_render_plan.required_provider_capabilities,
                    "missing_provider_capability",
                )
            }
        ),
    }

    with pytest.raises(
        CompilerValidationError,
        match=(
            "requires provider capabilities not declared by "
            "generic.active_work_item: missing_provider_capability"
        ),
    ):
        validate_request_context_profiles(
            artifact_contracts_by_id=active_plan.artifact_contracts_by_id,
            graphs_by_plane=active_plan.graphs_by_plane,
            request_context_profiles_by_id=active_plan.request_context_profiles_by_id,
            request_context_providers_by_id=active_plan.request_context_providers_by_id,
            request_context_render_plans_by_id=mutated_render_plans_by_id,
            workflow_primitives=primitives,
        )


def test_request_context_validation_rejects_disallowed_render_plan_override(
    tmp_path: Path,
) -> None:
    active_plan, primitives = _validation_inputs(tmp_path)
    execution_graph = active_plan.execution_graph.model_copy(
        update={
            "nodes": tuple(
                node.model_copy(update={"context_render_plan_id": "closure_target.default.v1"})
                if node.node_id == "builder"
                else node
                for node in active_plan.execution_graph.nodes
            ),
        }
    )
    mutated_graphs_by_plane = {
        **active_plan.graphs_by_plane,
        Plane.EXECUTION: execution_graph,
    }

    with pytest.raises(
        CompilerValidationError,
        match=(
            "graph node builder overrides request context render plan "
            "stage_request.default.v1 with closure_target.default.v1, but profile "
            "builder.default does not allow render plan overrides"
        ),
    ):
        validate_request_context_profiles(
            artifact_contracts_by_id=active_plan.artifact_contracts_by_id,
            graphs_by_plane=mutated_graphs_by_plane,
            request_context_profiles_by_id=active_plan.request_context_profiles_by_id,
            request_context_providers_by_id=active_plan.request_context_providers_by_id,
            request_context_render_plans_by_id=active_plan.request_context_render_plans_by_id,
            workflow_primitives=primitives,
        )
