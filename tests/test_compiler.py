from __future__ import annotations

from pathlib import Path
import json

import pytest
from pydantic import ValidationError

from millrace_engine.compiler import (
    CompilePhase,
    CompileTimeResolvedSnapshot,
    CompileStatus,
    FrozenLoopPlan,
    FrozenExecutionParameterBinder,
    FrozenRunCompiler,
    FrozenRunPlanContent,
    ParameterRebindingError,
)
from millrace_engine.contracts import (
    ControlPlane,
    LoopConfigDefinition,
    PersistedObjectKind,
    RegisteredStageKindDefinition,
    RegistryObjectRef,
)
from millrace_engine.provenance import BoundExecutionParameters, ExecutionParameterRebindingRequest
from millrace_engine.paths import RuntimePaths
from millrace_engine.registry import discover_registry_state, persist_workspace_registry_object


def _ref(kind: PersistedObjectKind, object_id: str, version: str = "1.0.0") -> RegistryObjectRef:
    return RegistryObjectRef(kind=kind, id=object_id, version=version)


def _runtime_paths(workspace_root: Path) -> RuntimePaths:
    return RuntimePaths.from_workspace(workspace_root, workspace_root / "agents")


def _stage_kind_definition(
    *,
    kind_id: str,
    plane: str = "execution",
    allowed_overrides: tuple[str, ...] = ("allow_search",),
) -> RegisteredStageKindDefinition:
    suffix = kind_id.split(".")[-1].upper()
    return RegisteredStageKindDefinition.model_validate(
        {
            "id": kind_id,
            "version": "1.0.0",
            "tier": "golden",
            "title": f"{kind_id} stage",
            "summary": "Workspace stage kind for compiler tests.",
            "source": {"kind": "workspace_defined"},
            "payload": {
                "kind_id": kind_id,
                "contract_version": "1.0.0",
                "plane": plane,
                "handler_ref": f"millrace_engine.stages.{kind_id.split('.')[-1]}:Stage",
                "context_schema_ref": f"{kind_id}.context.v1",
                "result_schema_ref": f"{kind_id}.result.v1",
                "running_status": f"{suffix}_RUNNING",
                "terminal_statuses": (f"{suffix}_COMPLETE", "BLOCKED"),
                "success_statuses": (f"{suffix}_COMPLETE",),
                "input_artifacts": (
                    {
                        "name": "task_card" if plane == "execution" else "spec",
                        "kind": "task_card" if plane == "execution" else "spec",
                        "required": True,
                        "multiplicity": "one",
                    },
                ),
                "output_artifacts": (
                    {
                        "name": "stage_summary",
                        "kind": "stage_summary",
                        "required_on": ("success", f"{suffix}_COMPLETE"),
                        "persistence": "history",
                    },
                ),
                "idempotence_policy": "retry_safe_with_key",
                "retry_policy": {"max_attempts": 1, "backoff_seconds": 0, "exhausted_outcome": "blocked"},
                "queue_mutation_policy": "runtime_only",
                "routing_outcomes": ("success", "blocked"),
                "legal_predecessors": (),
                "legal_successors": (),
                "allowed_overrides": allowed_overrides,
            },
        }
    )


def _invalid_loop_definition(
    *,
    object_id: str,
    build_kind_id: str,
    review_kind_id: str,
) -> LoopConfigDefinition:
    return LoopConfigDefinition.model_validate(
        {
            "id": object_id,
            "version": "1.0.0",
            "tier": "golden",
            "title": f"{object_id} loop",
            "summary": "Workspace loop with deliberate compiler validation faults.",
            "source": {"kind": "workspace_defined"},
            "payload": {
                "plane": "execution",
                "nodes": (
                    {
                        "node_id": "build",
                        "kind_id": build_kind_id,
                        "overrides": {},
                    },
                    {
                        "node_id": "review",
                        "kind_id": review_kind_id,
                        "overrides": {},
                    },
                ),
                "edges": (
                    {
                        "edge_id": "build_retry",
                        "from_node_id": "build",
                        "to_node_id": "build",
                        "on_outcomes": ("blocked",),
                        "kind": "retry",
                        "max_attempts": 3,
                    },
                    {
                        "edge_id": "build_done",
                        "from_node_id": "build",
                        "terminal_state_id": "done",
                        "on_outcomes": ("success",),
                        "kind": "terminal",
                    },
                ),
                "entry_node_id": "build",
                "terminal_states": (
                    {
                        "terminal_state_id": "done",
                        "terminal_class": "success",
                        "writes_status": "QA_COMPLETE",
                        "emits_artifacts": ("stage_summary",),
                        "ends_plane_run": True,
                    },
                ),
            },
        }
    )


def _single_stage_loop_definition(
    *,
    object_id: str,
    stage_kind_id: str,
    on_outcomes: tuple[str, ...] = ("success",),
    terminal_status: str,
) -> LoopConfigDefinition:
    return LoopConfigDefinition.model_validate(
        {
            "id": object_id,
            "version": "1.0.0",
            "tier": "golden",
            "title": f"{object_id} loop",
            "summary": "Workspace loop for compiler edge validation tests.",
            "source": {"kind": "workspace_defined"},
            "payload": {
                "plane": "execution",
                "nodes": (
                    {
                        "node_id": "build",
                        "kind_id": stage_kind_id,
                        "overrides": {},
                    },
                ),
                "edges": (
                    {
                        "edge_id": "build_done",
                        "from_node_id": "build",
                        "terminal_state_id": "done",
                        "on_outcomes": on_outcomes,
                        "kind": "terminal",
                    },
                ),
                "entry_node_id": "build",
                "terminal_states": (
                    {
                        "terminal_state_id": "done",
                        "terminal_class": "success",
                        "writes_status": terminal_status,
                        "emits_artifacts": ("stage_summary",),
                        "ends_plane_run": True,
                    },
                ),
            },
        }
    )


def test_compiler_emits_mode_artifacts_and_stable_content_hash(tmp_path: Path) -> None:
    compiler = FrozenRunCompiler(_runtime_paths(tmp_path))

    first = compiler.compile_mode(
        _ref(PersistedObjectKind.MODE, "mode.default_autonomous"),
        run_id="run-001",
    )
    second = compiler.compile_mode(
        _ref(PersistedObjectKind.MODE, "mode.default_autonomous"),
        run_id="run-002",
    )

    assert first.status is CompileStatus.OK
    assert first.plan is not None
    assert first.snapshot is not None
    assert first.artifacts is not None
    assert not first.diagnostics
    assert second.status is CompileStatus.OK
    assert second.plan is not None
    assert second.snapshot is not None
    assert first.plan.content_hash == second.plan.content_hash
    assert first.plan.runtime_provenance_context().snapshot_id == first.snapshot.snapshot_id
    builder_parameters = first.snapshot.runtime_provenance_context().stage_parameters_for(
        ControlPlane.EXECUTION,
        "builder",
    )

    diagnostics_payload = json.loads(first.artifacts.compile_diagnostics_json_path.read_text(encoding="utf-8"))
    snapshot_payload = json.loads(first.artifacts.resolved_snapshot_json_path.read_text(encoding="utf-8"))
    payload = json.loads(first.artifacts.frozen_plan_json_path.read_text(encoding="utf-8"))
    markdown = first.artifacts.frozen_plan_markdown_path.read_text(encoding="utf-8")
    snapshot_markdown = first.artifacts.resolved_snapshot_markdown_path.read_text(encoding="utf-8")
    mode_source_ref = next(
        source_ref
        for source_ref in first.plan.content.source_refs
        if source_ref.object_ref == "mode:mode.default_autonomous@1.0.0"
    )

    assert diagnostics_payload["result"] == "ok"
    assert diagnostics_payload["content_hash"] == first.plan.content_hash
    assert diagnostics_payload["diagnostics"] == []
    assert snapshot_payload["snapshot_id"] == first.snapshot.snapshot_id
    assert snapshot_payload["frozen_plan"]["plan_id"] == first.plan.identity.plan_id
    assert snapshot_payload["content"]["selection_ref"]["id"] == "mode.default_autonomous"
    assert payload["content_hash"] == first.plan.content_hash
    assert payload["compile_diagnostics"] == []
    assert payload["content"]["selected_mode_ref"]["id"] == "mode.default_autonomous"
    assert payload["content"]["execution_plan"]["entry_node_id"] == "builder"
    assert {
        (rule["plane"], rule["node_id"], rule["field"], rule["rebind_at_boundary"])
        for rule in payload["content"]["parameter_rebinding_rules"]
    } == {
        ("execution", "builder", "allow_search", "stage_boundary"),
        ("execution", "builder", "effort", "stage_boundary"),
        ("execution", "builder", "model", "stage_boundary"),
        ("execution", "builder", "model_profile_ref", "stage_boundary"),
        ("execution", "builder", "runner", "stage_boundary"),
        ("execution", "builder", "timeout_seconds", "stage_boundary"),
    }
    assert first.plan.content_hash in markdown
    assert "mode.default_autonomous" in markdown
    assert "Execution Plan" in markdown
    assert "Parameter Rebinding Rules" in markdown
    assert "Compile Diagnostics" in markdown
    assert "Compile-time provenance only." in markdown
    assert "routes against this frozen plan" in markdown
    assert "transition_history.jsonl" in markdown
    assert first.snapshot.snapshot_id in snapshot_markdown
    assert first.plan.identity.plan_id in snapshot_markdown
    assert "Resolved Snapshot" in snapshot_markdown
    assert "Compile-time provenance only." in snapshot_markdown
    assert "transition_history.jsonl" in snapshot_markdown
    assert mode_source_ref.source_layer == "packaged"
    assert mode_source_ref.registry_source_kind is not None
    assert mode_source_ref.registry_source_kind.value == "packaged_default"
    assert "default-autonomous" in mode_source_ref.aliases
    assert builder_parameters is not None
    assert first.plan.content.execution_plan is not None
    assert builder_parameters == BoundExecutionParameters(
        model_profile_ref=first.plan.content.execution_plan.stages[0].model_profile_ref,
        runner=first.plan.content.execution_plan.stages[0].runner,
        model=first.plan.content.execution_plan.stages[0].model,
        effort=first.plan.content.execution_plan.stages[0].effort,
        allow_search=first.plan.content.execution_plan.stages[0].allow_search,
        timeout_seconds=first.plan.content.execution_plan.stages[0].timeout_seconds,
    )


def test_compiler_compiles_direct_loop_selection(tmp_path: Path) -> None:
    compiler = FrozenRunCompiler(_runtime_paths(tmp_path))

    result = compiler.compile_loop(
        _ref(PersistedObjectKind.LOOP_CONFIG, "execution.quick_build"),
        run_id="loop-run",
    )

    assert result.status is CompileStatus.OK
    assert result.plan is not None
    assert result.plan.content.selected_mode_ref is None
    assert result.plan.content.selected_execution_loop_ref == _ref(
        PersistedObjectKind.LOOP_CONFIG, "execution.quick_build"
    )
    assert result.plan.content.execution_plan is not None
    assert result.plan.content.research_plan is None
    assert result.plan.content.execution_plan.stages[0].node_id == "builder"


def test_frozen_run_plan_content_rejects_inconsistent_mode_selection_ref(tmp_path: Path) -> None:
    result = FrozenRunCompiler(_runtime_paths(tmp_path)).compile_mode(
        _ref(PersistedObjectKind.MODE, "mode.standard"),
        run_id="invalid-mode-selection",
    )

    assert result.status is CompileStatus.OK
    assert result.plan is not None
    payload = result.plan.content.model_dump(mode="python")
    payload["selection_ref"] = _ref(PersistedObjectKind.LOOP_CONFIG, "execution.standard")

    with pytest.raises(ValidationError, match="selection_ref must match selected_mode_ref"):
        FrozenRunPlanContent.model_validate(payload)


def test_frozen_run_plan_content_rejects_inconsistent_direct_loop_selection_ref(tmp_path: Path) -> None:
    result = FrozenRunCompiler(_runtime_paths(tmp_path)).compile_loop(
        _ref(PersistedObjectKind.LOOP_CONFIG, "execution.quick_build"),
        run_id="invalid-loop-selection",
    )

    assert result.status is CompileStatus.OK
    assert result.plan is not None
    payload = result.plan.content.model_dump(mode="python")
    payload["selection_ref"] = _ref(PersistedObjectKind.LOOP_CONFIG, "execution.standard")

    with pytest.raises(ValidationError, match="selection_ref must match the selected loop ref"):
        FrozenRunPlanContent.model_validate(payload)


def test_compile_time_resolved_snapshot_rejects_mismatched_selection_refs(tmp_path: Path) -> None:
    result = FrozenRunCompiler(_runtime_paths(tmp_path)).compile_mode(
        _ref(PersistedObjectKind.MODE, "mode.standard"),
        run_id="invalid-snapshot-selection",
    )

    assert result.status is CompileStatus.OK
    assert result.snapshot is not None

    payload = result.snapshot.model_dump(mode="python")
    payload["selection_ref"] = _ref(PersistedObjectKind.LOOP_CONFIG, "execution.standard")
    with pytest.raises(ValidationError, match="snapshot selection_ref must match content.selection_ref"):
        CompileTimeResolvedSnapshot.model_validate(payload)

    payload = result.snapshot.model_dump(mode="python")
    payload["frozen_plan"]["selection_ref"] = _ref(
        PersistedObjectKind.LOOP_CONFIG,
        "execution.standard",
    ).model_dump(mode="python")
    with pytest.raises(ValidationError, match="snapshot frozen_plan.selection_ref must match content.selection_ref"):
        CompileTimeResolvedSnapshot.model_validate(payload)


def test_frozen_loop_plan_rejects_resume_state_drift(tmp_path: Path) -> None:
    result = FrozenRunCompiler(_runtime_paths(tmp_path)).compile_loop(
        _ref(PersistedObjectKind.LOOP_CONFIG, "execution.quick_build"),
        run_id="invalid-resume-states",
    )

    assert result.status is CompileStatus.OK
    assert result.plan is not None
    assert result.plan.content.execution_plan is not None
    payload = result.plan.content.execution_plan.model_dump(mode="python")
    payload["resume_states"] = ()

    with pytest.raises(ValidationError, match="resume_states must match the terminal-state index"):
        FrozenLoopPlan.model_validate(payload)


def test_compiler_compiles_packaged_standard_mode_with_full_stage_graph(tmp_path: Path) -> None:
    result = FrozenRunCompiler(_runtime_paths(tmp_path)).compile_mode(
        _ref(PersistedObjectKind.MODE, "mode.standard"),
        run_id="standard-mode-run",
    )

    assert result.status is CompileStatus.OK
    assert result.plan is not None
    assert result.plan.content.selected_execution_loop_ref == _ref(
        PersistedObjectKind.LOOP_CONFIG,
        "execution.standard",
    )
    assert result.plan.content.execution_plan is not None
    assert [stage.node_id for stage in result.plan.content.execution_plan.stages] == [
        "builder",
        "consult",
        "doublecheck",
        "hotfix",
        "integration",
        "qa",
        "troubleshoot",
        "update",
    ]
    assert {
        transition.edge_id for transition in result.plan.content.execution_plan.transitions
    } >= {
        "execution.builder.success.integration",
        "execution.builder.success.qa",
        "execution.qa.quickfix.hotfix",
        "execution.consult.handoff.needs_research",
        "execution.update.success.archive",
    }


def test_compiler_compiles_packaged_large_modes_with_distinct_paths(tmp_path: Path) -> None:
    compiler = FrozenRunCompiler(_runtime_paths(tmp_path))

    thorough = compiler.compile_mode(
        _ref(PersistedObjectKind.MODE, "mode.large"),
        run_id="large-thorough-run",
    )
    direct_update = compiler.compile_mode(
        _ref(PersistedObjectKind.MODE, "mode.large_direct_update"),
        run_id="large-direct-update-run",
    )

    assert thorough.status is CompileStatus.OK
    assert thorough.plan is not None
    assert thorough.plan.content.selected_execution_loop_ref == _ref(
        PersistedObjectKind.LOOP_CONFIG,
        "execution.large",
    )
    assert thorough.plan.content.execution_plan is not None
    assert {
        stage.node_id for stage in thorough.plan.content.execution_plan.stages
    } == {"large_plan", "large_execute", "reassess", "refactor", "qa", "hotfix", "doublecheck", "update"}
    assert {
        transition.edge_id for transition in thorough.plan.content.execution_plan.transitions
    } >= {
        "execution.large.refactor.blocked.qa",
        "execution.large.refactor.success.qa",
        "execution.large.qa.quickfix.hotfix",
        "execution.large.doublecheck.success.update",
    }

    assert direct_update.status is CompileStatus.OK
    assert direct_update.plan is not None
    assert direct_update.plan.content.selected_execution_loop_ref == _ref(
        PersistedObjectKind.LOOP_CONFIG,
        "execution.large_direct_update",
    )
    assert direct_update.plan.content.execution_plan is not None
    assert {
        stage.node_id for stage in direct_update.plan.content.execution_plan.stages
    } == {"large_plan", "large_execute", "reassess", "refactor", "update"}
    assert {
        transition.edge_id for transition in direct_update.plan.content.execution_plan.transitions
    } >= {
        "execution.large.refactor.blocked.update",
        "execution.large.refactor.success.update",
        "execution.large.update.success.archive",
    }
    assert {
        transition.edge_id for transition in direct_update.plan.content.execution_plan.transitions
    }.isdisjoint(
        {
            "execution.large.refactor.success.qa",
            "execution.large.qa.quickfix.hotfix",
            "execution.large.doublecheck.success.update",
        }
    )


def test_compiler_large_mode_shadow_can_change_selected_path_without_code_edits(tmp_path: Path) -> None:
    mode_ref = _ref(PersistedObjectKind.MODE, "mode.large")

    first = FrozenRunCompiler(_runtime_paths(tmp_path)).compile_mode(
        mode_ref,
        run_id="large-profile-shadow",
    )

    assert first.status is CompileStatus.OK
    assert first.plan is not None
    assert first.plan.content.selected_execution_loop_ref == _ref(
        PersistedObjectKind.LOOP_CONFIG,
        "execution.large",
    )

    packaged_mode = next(
        document.definition
        for document in discover_registry_state(tmp_path, validate_catalog=False).packaged
        if document.key == ("mode", "mode.large", "1.0.0")
    )
    shadow_payload = packaged_mode.model_dump(mode="json")
    shadow_payload["title"] = "Workspace Large Direct Update Profile"
    shadow_payload["aliases"] = ["workspace-large-direct-update"]
    shadow_payload["summary"] = (
        "Workspace overlay flips the packaged LARGE default to the direct-update profile."
    )
    shadow_payload["source"] = {"kind": "workspace_defined"}
    shadow_payload["payload"]["execution_loop_ref"] = _ref(
        PersistedObjectKind.LOOP_CONFIG,
        "execution.large_direct_update",
    ).model_dump(mode="json")
    persist_workspace_registry_object(
        tmp_path,
        packaged_mode.__class__.model_validate(shadow_payload),
    )

    second = FrozenRunCompiler(_runtime_paths(tmp_path)).compile_mode(
        mode_ref,
        run_id="large-profile-shadow",
    )

    assert second.status is CompileStatus.OK
    assert second.plan is not None
    assert second.plan.content.selected_execution_loop_ref == _ref(
        PersistedObjectKind.LOOP_CONFIG,
        "execution.large_direct_update",
    )
    assert second.plan.content.execution_plan is not None
    assert {
        stage.node_id for stage in second.plan.content.execution_plan.stages
    } == {"large_plan", "large_execute", "reassess", "refactor", "update"}
    mode_source_ref = next(
        source_ref
        for source_ref in second.plan.content.source_refs
        if source_ref.object_ref == "mode:mode.large@1.0.0"
    )
    assert mode_source_ref.title == "Workspace Large Direct Update Profile"
    assert mode_source_ref.source_layer == "workspace"
    assert mode_source_ref.registry_source_kind.value == "workspace_defined"


def test_compiler_returns_structured_diagnostics_for_invalid_loop(tmp_path: Path) -> None:
    build_stage = _stage_kind_definition(kind_id="execution.invalid_builder")
    review_stage = _stage_kind_definition(kind_id="research.invalid_review", plane="research")
    invalid_loop = _invalid_loop_definition(
        object_id="execution.invalid_loop",
        build_kind_id=build_stage.id,
        review_kind_id=review_stage.id,
    )
    persist_workspace_registry_object(tmp_path, build_stage)
    persist_workspace_registry_object(tmp_path, review_stage)
    persist_workspace_registry_object(tmp_path, invalid_loop)

    compiler = FrozenRunCompiler(_runtime_paths(tmp_path))
    result = compiler.compile_loop(
        _ref(PersistedObjectKind.LOOP_CONFIG, invalid_loop.id),
        run_id="invalid-run",
    )

    assert result.status is CompileStatus.FAIL
    assert result.plan is None
    assert result.artifacts is not None
    assert all(diagnostic.phase is CompilePhase.VALIDATE for diagnostic in result.diagnostics)

    codes = {diagnostic.code for diagnostic in result.diagnostics}
    assert "STAGE_PLANE_MISMATCH" in codes
    assert "GRAPH_UNREACHABLE_STAGE" in codes
    assert "NODE_MISSING_OUTGOING_EDGE" in codes
    assert "RETRY_ATTEMPTS_EXCEED_STAGE_KIND" in codes
    assert "TERMINAL_STATUS_UNSUPPORTED" in codes
    diagnostics_payload = json.loads(result.artifacts.compile_diagnostics_json_path.read_text(encoding="utf-8"))
    assert diagnostics_payload["result"] == "fail"
    assert {diagnostic["code"] for diagnostic in diagnostics_payload["diagnostics"]} == codes
    assert not (_runtime_paths(tmp_path).runs_dir / "invalid-run" / "frozen_run_plan.json").exists()
    assert not (_runtime_paths(tmp_path).runs_dir / "invalid-run" / "frozen_run_plan.md").exists()


def test_compiler_rejects_retry_edges_that_do_not_self_loop(tmp_path: Path) -> None:
    build_stage = _stage_kind_definition(kind_id="execution.retry_builder")
    review_stage = _stage_kind_definition(kind_id="execution.retry_review")
    loop = LoopConfigDefinition.model_validate(
        {
            "id": "execution.retry_loop",
            "version": "1.0.0",
            "tier": "golden",
            "title": "execution.retry_loop loop",
            "summary": "Workspace loop with an illegal retry target.",
            "source": {"kind": "workspace_defined"},
            "payload": {
                "plane": "execution",
                "nodes": (
                    {
                        "node_id": "build",
                        "kind_id": build_stage.id,
                        "overrides": {},
                    },
                    {
                        "node_id": "review",
                        "kind_id": review_stage.id,
                        "overrides": {},
                    },
                ),
                "edges": (
                    {
                        "edge_id": "retry_to_review",
                        "from_node_id": "build",
                        "to_node_id": "review",
                        "on_outcomes": ("blocked",),
                        "kind": "retry",
                        "max_attempts": 1,
                    },
                    {
                        "edge_id": "build_done",
                        "from_node_id": "build",
                        "terminal_state_id": "build_done",
                        "on_outcomes": ("success",),
                        "kind": "terminal",
                    },
                    {
                        "edge_id": "review_done",
                        "from_node_id": "review",
                        "terminal_state_id": "review_done",
                        "on_outcomes": ("success",),
                        "kind": "terminal",
                    },
                ),
                "entry_node_id": "build",
                "terminal_states": (
                    {
                        "terminal_state_id": "build_done",
                        "terminal_class": "success",
                        "writes_status": "RETRY_BUILDER_COMPLETE",
                        "emits_artifacts": ("stage_summary",),
                        "ends_plane_run": True,
                    },
                    {
                        "terminal_state_id": "review_done",
                        "terminal_class": "success",
                        "writes_status": "RETRY_REVIEW_COMPLETE",
                        "emits_artifacts": ("stage_summary",),
                        "ends_plane_run": True,
                    },
                ),
            },
        }
    )
    persist_workspace_registry_object(tmp_path, build_stage)
    persist_workspace_registry_object(tmp_path, review_stage)
    persist_workspace_registry_object(tmp_path, loop)

    result = FrozenRunCompiler(_runtime_paths(tmp_path)).compile_loop(
        _ref(PersistedObjectKind.LOOP_CONFIG, loop.id),
        run_id="retry-run",
    )

    assert result.status is CompileStatus.FAIL
    assert {diagnostic.code for diagnostic in result.diagnostics} == {"RETRY_EDGE_NOT_SELF_LOOP"}


def test_compiler_rejects_running_status_edge_triggers(tmp_path: Path) -> None:
    stage = _stage_kind_definition(kind_id="execution.running_status_builder")
    loop = _single_stage_loop_definition(
        object_id="execution.running_status_loop",
        stage_kind_id=stage.id,
        on_outcomes=("RUNNING_STATUS_BUILDER_RUNNING",),
        terminal_status="RUNNING_STATUS_BUILDER_COMPLETE",
    )
    persist_workspace_registry_object(tmp_path, stage)
    persist_workspace_registry_object(tmp_path, loop)

    result = FrozenRunCompiler(_runtime_paths(tmp_path)).compile_loop(
        _ref(PersistedObjectKind.LOOP_CONFIG, loop.id),
        run_id="running-status-run",
    )

    assert result.status is CompileStatus.FAIL
    assert {diagnostic.code for diagnostic in result.diagnostics} == {"EDGE_OUTCOME_UNDECLARED"}


def test_compiler_rejects_duplicate_terminal_statuses(tmp_path: Path) -> None:
    stage = _stage_kind_definition(kind_id="execution.duplicate_terminal_builder")
    loop = LoopConfigDefinition.model_validate(
        {
            "id": "execution.duplicate_terminal_loop",
            "version": "1.0.0",
            "tier": "golden",
            "title": "execution.duplicate_terminal_loop loop",
            "summary": "Workspace loop with duplicate terminal statuses.",
            "source": {"kind": "workspace_defined"},
            "payload": {
                "plane": "execution",
                "nodes": (
                    {
                        "node_id": "build",
                        "kind_id": stage.id,
                        "overrides": {},
                    },
                ),
                "edges": (
                    {
                        "edge_id": "build_success",
                        "from_node_id": "build",
                        "terminal_state_id": "success_done",
                        "on_outcomes": ("success",),
                        "kind": "terminal",
                    },
                    {
                        "edge_id": "build_blocked",
                        "from_node_id": "build",
                        "terminal_state_id": "blocked_done",
                        "on_outcomes": ("blocked",),
                        "kind": "terminal",
                    },
                ),
                "entry_node_id": "build",
                "terminal_states": (
                    {
                        "terminal_state_id": "success_done",
                        "terminal_class": "success",
                        "writes_status": "BLOCKED",
                        "emits_artifacts": ("stage_summary",),
                        "ends_plane_run": True,
                    },
                    {
                        "terminal_state_id": "blocked_done",
                        "terminal_class": "blocked",
                        "writes_status": "BLOCKED",
                        "emits_artifacts": ("stage_summary",),
                        "ends_plane_run": True,
                    },
                ),
            },
        }
    )
    persist_workspace_registry_object(tmp_path, stage)
    persist_workspace_registry_object(tmp_path, loop)

    result = FrozenRunCompiler(_runtime_paths(tmp_path)).compile_loop(
        _ref(PersistedObjectKind.LOOP_CONFIG, loop.id),
        run_id="duplicate-terminal-run",
    )

    assert result.status is CompileStatus.FAIL
    assert {diagnostic.code for diagnostic in result.diagnostics} == {"DUPLICATE_TERMINAL_STATUS"}


def test_compiler_removes_stale_frozen_plan_artifacts_after_failed_recompile(tmp_path: Path) -> None:
    stage = _stage_kind_definition(kind_id="execution.stale_builder")
    valid_loop = _single_stage_loop_definition(
        object_id="execution.stale_loop",
        stage_kind_id=stage.id,
        terminal_status="STALE_BUILDER_COMPLETE",
    )
    invalid_loop = _single_stage_loop_definition(
        object_id=valid_loop.id,
        stage_kind_id=stage.id,
        terminal_status="QA_COMPLETE",
    )
    persist_workspace_registry_object(tmp_path, stage)
    persist_workspace_registry_object(tmp_path, valid_loop)

    compiler = FrozenRunCompiler(_runtime_paths(tmp_path))
    first_result = compiler.compile_loop(
        _ref(PersistedObjectKind.LOOP_CONFIG, valid_loop.id),
        run_id="stale-run",
    )
    assert first_result.status is CompileStatus.OK
    assert first_result.artifacts is not None
    assert first_result.artifacts.frozen_plan_json_path is not None
    assert first_result.artifacts.frozen_plan_markdown_path is not None
    assert first_result.artifacts.frozen_plan_json_path.exists()
    assert first_result.artifacts.frozen_plan_markdown_path.exists()
    stale_history_path = _runtime_paths(tmp_path).runs_dir / "stale-run" / "transition_history.jsonl"
    stale_history_path.write_text('{"event_id":"stale-run-transition-0001"}\n', encoding="utf-8")

    persist_workspace_registry_object(tmp_path, invalid_loop, overwrite=True)

    second_result = FrozenRunCompiler(_runtime_paths(tmp_path)).compile_loop(
        _ref(PersistedObjectKind.LOOP_CONFIG, invalid_loop.id),
        run_id="stale-run",
    )

    assert second_result.status is CompileStatus.FAIL
    assert second_result.artifacts is not None
    assert not (_runtime_paths(tmp_path).runs_dir / "stale-run" / "resolved_snapshot.json").exists()
    assert not (_runtime_paths(tmp_path).runs_dir / "stale-run" / "resolved_snapshot.md").exists()
    assert not (_runtime_paths(tmp_path).runs_dir / "stale-run" / "frozen_run_plan.json").exists()
    assert not (_runtime_paths(tmp_path).runs_dir / "stale-run" / "frozen_run_plan.md").exists()
    assert not stale_history_path.exists()
    diagnostics_payload = json.loads(second_result.artifacts.compile_diagnostics_json_path.read_text(encoding="utf-8"))
    assert diagnostics_payload["result"] == "fail"


def test_compiler_clears_stale_transition_history_before_successful_recompile(tmp_path: Path) -> None:
    stage = _stage_kind_definition(kind_id="execution.clean_history_builder")
    loop = _single_stage_loop_definition(
        object_id="execution.clean_history_loop",
        stage_kind_id=stage.id,
        terminal_status="CLEAN_HISTORY_BUILDER_COMPLETE",
    )
    persist_workspace_registry_object(tmp_path, stage)
    persist_workspace_registry_object(tmp_path, loop)

    run_dir = _runtime_paths(tmp_path).runs_dir / "history-run"
    run_dir.mkdir(parents=True, exist_ok=True)
    stale_history_path = run_dir / "transition_history.jsonl"
    stale_history_path.write_text('{"event_id":"history-run-transition-0001"}\n', encoding="utf-8")

    result = FrozenRunCompiler(_runtime_paths(tmp_path)).compile_loop(
        _ref(PersistedObjectKind.LOOP_CONFIG, loop.id),
        run_id="history-run",
    )

    assert result.status is CompileStatus.OK
    assert result.artifacts is not None
    assert result.artifacts.resolved_snapshot_json_path is not None
    assert result.artifacts.resolved_snapshot_json_path.exists()
    assert not stale_history_path.exists()


def test_compiler_reused_run_id_rewrites_snapshot_and_frozen_plan_with_latest_compile_truth(tmp_path: Path) -> None:
    compiler = FrozenRunCompiler(_runtime_paths(tmp_path))
    run_id = "rerun-truth-run"
    loop_ref = _ref(PersistedObjectKind.LOOP_CONFIG, "execution.quick_build")

    first_result = compiler.compile_loop(loop_ref, run_id=run_id)

    assert first_result.status is CompileStatus.OK
    assert first_result.plan is not None
    assert first_result.snapshot is not None
    assert first_result.artifacts is not None

    packaged_loop = next(
        document.definition
        for document in discover_registry_state(tmp_path, validate_catalog=False).packaged
        if document.key == ("loop_config", loop_ref.id, loop_ref.version)
    )
    shadow_payload = packaged_loop.model_dump(mode="json")
    shadow_payload["title"] = "Workspace quick build shadow"
    shadow_payload["aliases"] = ["workspace-quick-build"]
    shadow_payload["source"] = {"kind": "workspace_defined"}
    persist_workspace_registry_object(
        tmp_path,
        packaged_loop.__class__.model_validate(shadow_payload),
    )

    second_result = FrozenRunCompiler(_runtime_paths(tmp_path)).compile_loop(loop_ref, run_id=run_id)

    assert second_result.status is CompileStatus.OK
    assert second_result.plan is not None
    assert second_result.snapshot is not None
    assert second_result.artifacts is not None
    assert first_result.artifacts.run_dir == second_result.artifacts.run_dir
    assert first_result.artifacts.compile_diagnostics_json_path == second_result.artifacts.compile_diagnostics_json_path
    assert first_result.artifacts.resolved_snapshot_json_path == second_result.artifacts.resolved_snapshot_json_path
    assert first_result.artifacts.frozen_plan_json_path == second_result.artifacts.frozen_plan_json_path
    assert second_result.plan.content_hash != first_result.plan.content_hash
    assert second_result.snapshot.snapshot_id != first_result.snapshot.snapshot_id

    diagnostics_payload = json.loads(second_result.artifacts.compile_diagnostics_json_path.read_text(encoding="utf-8"))
    snapshot_payload = json.loads(second_result.artifacts.resolved_snapshot_json_path.read_text(encoding="utf-8"))
    plan_payload = json.loads(second_result.artifacts.frozen_plan_json_path.read_text(encoding="utf-8"))
    loop_source_ref = next(
        source_ref
        for source_ref in plan_payload["content"]["source_refs"]
        if source_ref["object_ref"] == "loop_config:execution.quick_build@1.0.0"
    )

    assert diagnostics_payload["result"] == "ok"
    assert diagnostics_payload["content_hash"] == second_result.plan.content_hash
    assert snapshot_payload["snapshot_id"] == second_result.snapshot.snapshot_id
    assert snapshot_payload["frozen_plan"]["content_hash"] == second_result.plan.content_hash
    assert plan_payload["content_hash"] == second_result.plan.content_hash
    assert plan_payload["content"]["selection_ref"]["id"] == "execution.quick_build"
    assert loop_source_ref["title"] == "Workspace quick build shadow"
    assert loop_source_ref["source_layer"] == "workspace"
    assert loop_source_ref["registry_source_kind"] == "workspace_defined"


def test_compiler_filters_parameter_rebinding_rules_to_runtime_execution_fields(tmp_path: Path) -> None:
    stage = _stage_kind_definition(
        kind_id="execution.rebindable_builder",
        allowed_overrides=("model", "allow_search", "prompt_asset_ref"),
    )
    loop = _single_stage_loop_definition(
        object_id="execution.rebindable_loop",
        stage_kind_id=stage.id,
        terminal_status="REBINDABLE_BUILDER_COMPLETE",
    )
    persist_workspace_registry_object(tmp_path, stage)
    persist_workspace_registry_object(tmp_path, loop)

    result = FrozenRunCompiler(_runtime_paths(tmp_path)).compile_loop(
        _ref(PersistedObjectKind.LOOP_CONFIG, loop.id),
        run_id="rebindable-run",
    )

    assert result.status is CompileStatus.OK
    assert result.plan is not None
    assert {
        (rule.node_id, rule.field.value)
        for rule in result.plan.content.parameter_rebinding_rules
    } == {
        ("build", "allow_search"),
        ("build", "model"),
    }


def test_frozen_execution_parameter_binder_applies_legal_rebinds_without_mutating_plan_topology(tmp_path: Path) -> None:
    stage = _stage_kind_definition(
        kind_id="execution.bound_builder",
        allowed_overrides=("model", "allow_search"),
    )
    loop = _single_stage_loop_definition(
        object_id="execution.bound_loop",
        stage_kind_id=stage.id,
        terminal_status="BOUND_BUILDER_COMPLETE",
    )
    persist_workspace_registry_object(tmp_path, stage)
    persist_workspace_registry_object(tmp_path, loop)

    result = FrozenRunCompiler(_runtime_paths(tmp_path)).compile_loop(
        _ref(PersistedObjectKind.LOOP_CONFIG, loop.id),
        run_id="bound-run",
    )

    assert result.status is CompileStatus.OK
    assert result.plan is not None

    binder = FrozenExecutionParameterBinder(result.plan)
    assert result.plan.content.execution_plan is not None
    original_edges = tuple(result.plan.content.execution_plan.transitions)
    applied = binder.apply(
        ExecutionParameterRebindingRequest(
            plane=ControlPlane.EXECUTION,
            node_id="build",
            parameters=BoundExecutionParameters(model="fixture-model-v2", allow_search=True),
            reason="runtime retry after compile-time audit",
        )
    )

    assert tuple(field.value for field in applied.applied_fields) == ("allow_search", "model")
    assert binder.bound_parameters_for(ControlPlane.EXECUTION, "build").model == "fixture-model-v2"
    assert binder.bound_parameters_for(ControlPlane.EXECUTION, "build").allow_search is True
    assert tuple(result.plan.content.execution_plan.transitions) == original_edges

    with pytest.raises(
        ParameterRebindingError,
        match="timeout_seconds is not declared as rebindable",
    ):
        binder.apply(
            ExecutionParameterRebindingRequest(
                plane=ControlPlane.EXECUTION,
                node_id="build",
                parameters=BoundExecutionParameters(timeout_seconds=120),
            )
        )
