from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from millrace_ai.architecture import CompiledRunPlan
from millrace_ai.compilation.graph_exports import export_compiled_stage_graph
from millrace_ai.compilation.persistence import load_existing_plan
from millrace_ai.compiler import (
    CompilerValidationError,
    compile_and_persist_workspace_plan,
    inspect_workspace_plan_currentness,
    preview_graph_loop_plan,
)
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import CompileDiagnostics, Plane, ResultClass
from millrace_ai.errors import ConfigurationError, MillraceError
from millrace_ai.modes import SHIPPED_MODE_IDS
from millrace_ai.paths import bootstrap_workspace, workspace_paths


def test_compiler_consumes_config_and_assets_package_surfaces() -> None:
    assets_package = importlib.import_module("millrace_ai.assets")
    compiler_module = importlib.import_module("millrace_ai.compiler")
    config_module = importlib.import_module("millrace_ai.config")

    assert compiler_module.RuntimeConfig is config_module.RuntimeConfig
    assert compiler_module.load_builtin_mode_definition is assets_package.load_builtin_mode_definition


def test_compiler_public_exports_remain_importable() -> None:
    compiler_module = importlib.import_module("millrace_ai.compiler")

    for name in compiler_module.__all__:
        assert hasattr(compiler_module, name), name


def _copy_builtin_assets(tmp_path: Path) -> Path:
    assets_root = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"
    copied_root = tmp_path / "assets"
    shutil.copytree(assets_root, copied_root)
    return copied_root


def _write_custom_family_assets(assets_root: Path) -> None:
    family_dir = assets_root / "registry" / "work_item_families"
    adapter_dir = assets_root / "registry" / "document_adapters"
    family_dir.mkdir(parents=True, exist_ok=True)
    adapter_dir.mkdir(parents=True, exist_ok=True)
    (family_dir / "custom_review.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "kind": "work_item_family",
                "family_id": "custom_review",
                "plane": "planning",
                "entry_key": "custom_review",
                "display_name": "Custom Review",
                "document_kind": "custom_review",
                "runtime_relative_dir": "custom/reviews",
                "file_extension": ".json",
                "schema_id": "custom_review_document_v1",
                "document_adapter_id": "custom_review_json_v1",
                "queue_dirs": {
                    "queue": "custom/reviews/queue",
                    "active": "custom/reviews/active",
                    "done": "custom/reviews/done",
                    "blocked": "custom/reviews/blocked",
                    "canceled": "custom/reviews/canceled",
                },
                "lifecycle_states": ["queue", "active", "done", "blocked", "canceled"],
                "claimable_state": "queue",
                "active_state": "active",
                "done_state": "done",
                "blocked_state": "blocked",
                "canceled_state": "canceled",
                "closure_blocking_states": ["queue", "active", "blocked"],
                "default_entry_key": "custom_review",
                "id_field": "custom_id",
                "created_at_field": "created_at",
                "lineage_fields": ["root_spec_id"],
                "operator_capabilities": ["cancel", "inspect"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (adapter_dir / "custom_review_json_v1.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "kind": "work_item_document_adapter",
                "adapter_id": "custom_review_json_v1",
                "schema_id": "custom_review_document_v1",
                "supported_file_extensions": [".json"],
                "family_ids": ["custom_review"],
                "can_parse": True,
                "can_render": True,
                "can_summarize": True,
                "supports_dependencies": False,
                "supports_lineage": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_workspace_local_graph_mode_assets(assets_root: Path) -> None:
    execution_graph_path = assets_root / "graphs" / "execution" / "local_review.json"
    execution_graph = json.loads(
        (assets_root / "graphs" / "execution" / "lad.json").read_text(encoding="utf-8")
    )
    execution_graph["loop_id"] = "execution.local_review"
    execution_graph_path.write_text(json.dumps(execution_graph, indent=2) + "\n", encoding="utf-8")

    planning_graph_path = assets_root / "graphs" / "planning" / "local_review.json"
    planning_graph = json.loads(
        (assets_root / "graphs" / "planning" / "lad.json").read_text(encoding="utf-8")
    )
    planning_graph["loop_id"] = "planning.local_review"
    planning_graph_path.write_text(json.dumps(planning_graph, indent=2) + "\n", encoding="utf-8")

    mode_path = assets_root / "modes" / "local_review_codex.json"
    mode_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "kind": "mode",
                "mode_id": "local_review_codex",
                "loop_ids_by_plane": {
                    "execution": "execution.local_review",
                    "planning": "planning.local_review",
                },
                "stage_entrypoint_overrides": {},
                "stage_skill_additions": {},
                "stage_model_bindings": {"checker": "gpt-5.4"},
                "stage_thinking_bindings": {"checker": "high"},
                "stage_runner_bindings": {
                    "builder": "codex_cli",
                    "checker": "codex_cli",
                    "fixer": "codex_cli",
                    "doublechecker": "codex_cli",
                    "updater": "codex_cli",
                    "troubleshooter": "codex_cli",
                    "consultant": "codex_cli",
                    "planner": "codex_cli",
                    "manager": "codex_cli",
                    "mechanic": "codex_cli",
                    "auditor": "codex_cli",
                    "arbiter": "codex_cli",
                },
                "required_extensions": [
                    {"extension_package_id": "millrace.generic"},
                    {"extension_package_id": "millrace.recon"},
                    {"extension_package_id": "millrace.closure"}
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_synthetic_stage_kind_asset(assets_root: Path) -> None:
    stage_kind_path = (
        assets_root / "registry" / "stage_kinds" / "execution" / "synthetic_worker.json"
    )
    payload = {
        "schema_version": "1.0",
        "kind": "registered_stage_kind",
        "stage_kind_id": "synthetic_worker",
        "plane": "execution",
        "runtime_stage": "builder",
        "display_name": "Synthetic Worker",
        "default_entrypoint_path": "entrypoints/execution/builder.md",
        "required_skill_paths": ["skills/stage/execution/builder-core/SKILL.md"],
        "suggested_skill_paths": [],
        "running_status_marker": "SYNTHETIC_RUNNING",
        "legal_outcomes": ["SYNTHETIC_COMPLETE", "BLOCKED"],
        "success_outcomes": ["SYNTHETIC_COMPLETE"],
        "failure_outcomes": ["BLOCKED"],
        "allowed_result_classes_by_outcome": {
            "SYNTHETIC_COMPLETE": ["success"],
            "BLOCKED": ["blocked", "recoverable_failure"],
        },
        "allowed_input_artifacts": [],
        "declared_output_artifacts": ["stage_result", "report"],
        "allowed_work_item_families": ["task"],
        "idempotence_policy": "retry_safe_with_key",
        "allowed_overrides": [
            "entrypoint_path",
            "runner_name",
            "model_name",
            "timeout_seconds",
            "attached_skill_additions",
        ],
        "can_start_tasks": True,
        "can_start_specs": False,
        "can_start_incidents": False,
        "recovery_role": None,
        "closure_role": False,
    }
    stage_kind_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_synthetic_graph_loop_asset(assets_root: Path) -> None:
    graph_path = assets_root / "graphs" / "execution" / "synthetic.json"
    payload = {
        "schema_version": "1.0",
        "kind": "graph_loop",
        "loop_id": "execution.synthetic",
        "plane": "execution",
        "nodes": [{"node_id": "synthetic_worker", "stage_kind_id": "synthetic_worker"}],
        "entry_nodes": [{"entry_key": "task", "node_id": "synthetic_worker"}],
        "edges": [
            {
                "edge_id": "synthetic-complete-to-terminal",
                "from_node_id": "synthetic_worker",
                "terminal_state_id": "synthetic_complete",
                "on_outcomes": ["SYNTHETIC_COMPLETE"],
                "kind": "terminal",
            },
            {
                "edge_id": "synthetic-blocked-to-terminal",
                "from_node_id": "synthetic_worker",
                "terminal_state_id": "blocked",
                "on_outcomes": ["BLOCKED"],
                "kind": "terminal",
            },
        ],
        "terminal_states": [
            {
                "terminal_state_id": "synthetic_complete",
                "terminal_class": "success",
                "terminal_action_id": "complete_work_item",
                "writes_status": "SYNTHETIC_COMPLETE",
                "emits_artifacts": ["stage_result", "report"],
            },
            {
                "terminal_state_id": "blocked",
                "terminal_class": "blocked",
                "terminal_action_id": "block_work_item",
                "writes_status": "BLOCKED",
                "emits_artifacts": ["stage_result", "report"],
            },
        ],
    }
    graph_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _all_nodes(plan: CompiledRunPlan):
    return tuple(node for graph in plan.graphs_by_plane.values() for node in graph.nodes)


def test_compiler_validation_errors_use_project_error_hierarchy() -> None:
    assert issubclass(ConfigurationError, MillraceError)
    assert issubclass(CompilerValidationError, ConfigurationError)


def test_compile_writes_compiled_plan_and_diagnostics_artifacts(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )

    paths = workspace_paths(workspace_root)
    compiled_plan_path = paths.state_dir / "compiled_plan.json"
    diagnostics_path = paths.state_dir / "compile_diagnostics.json"

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    assert outcome.used_last_known_good is False
    assert compiled_plan_path.is_file()
    assert not (paths.state_dir / "compiled_graph_plan.json").exists()
    assert diagnostics_path.is_file()

    persisted_plan = CompiledRunPlan.model_validate_json(compiled_plan_path.read_text(encoding="utf-8"))
    persisted_diagnostics = CompileDiagnostics.model_validate_json(
        diagnostics_path.read_text(encoding="utf-8")
    )

    assert persisted_plan.mode_id == "lad_codex"
    assert persisted_plan.loop_ids_by_plane == {
        Plane.EXECUTION: "execution.lad",
        Plane.PLANNING: "planning.lad",
    }
    assert set(persisted_plan.graphs_by_plane) == {Plane.EXECUTION, Plane.PLANNING}
    assert persisted_plan.execution_loop_id == "execution.lad"
    assert persisted_plan.planning_loop_id == "planning.lad"
    assert persisted_plan.learning_graph is None
    assert persisted_plan.learning_trigger_rules == ()
    assert persisted_plan.resolved_assets
    assert set(persisted_plan.work_item_families_by_id) == {
        "task",
        "spec",
        "probe",
        "incident",
        "learning_request",
        "blueprint_draft",
    }
    assert persisted_plan.work_item_families_by_id["task"].default_entry_key == "task"
    assert persisted_plan.document_adapters_by_id["builtin_markdown_v1"].supports_lineage is True
    assert persisted_plan.document_adapters_by_id["blueprint_draft_markdown_v1"].supports_lineage is True
    assert persisted_plan.queue_claim_policies_by_plane[Plane.EXECUTION].family_order == ("task",)
    assert persisted_plan.queue_claim_policies_by_plane[Plane.PLANNING].family_order == (
        "incident",
        "probe",
        "spec",
    )
    assert {"complete_work_item", "block_work_item"}.issubset(persisted_plan.terminal_actions_by_id)
    assert "complete_work_item" in persisted_plan.lifecycle_mutation_plans_by_id
    assert "evaluator_blueprint_approved_to_task" in persisted_plan.runtime_effect_handlers_by_id
    assert "generated_task" in persisted_plan.artifact_contracts_by_id
    assert persisted_plan.artifact_contracts_by_id["generated_task"].canonical_filename == "generated_task.json"
    assert "generated_task.md" in persisted_plan.artifact_contracts_by_id["generated_task"].accepted_filenames
    assert any(contract.artifact_id == "blueprint_critique" for contract in persisted_plan.artifact_contracts)
    assert persisted_plan.workspace_schema_epoch is not None
    assert persisted_plan.workspace_schema_epoch.epoch_id == "v0.20"
    assert {entry.entry_key.value: entry.node_id for entry in persisted_plan.execution_graph.compiled_entries} == {
        "task": "builder"
    }
    assert {entry.entry_key.value: entry.node_id for entry in persisted_plan.planning_graph.compiled_entries} == {
        "incident": "auditor",
        "probe": "recon",
        "spec": "planner",
    }
    assert persisted_plan.planning_graph.compiled_completion_entry is not None
    assert persisted_plan.planning_graph.compiled_completion_entry.node_id == "arbiter"
    assert any(ref.startswith("graph_completion_behavior:") for ref in persisted_plan.source_refs)
    assert any(ref.logical_id == "work_item_family:task" for ref in persisted_plan.resolved_assets)
    assert any(ref.logical_id == "terminal_action:complete_work_item" for ref in persisted_plan.resolved_assets)
    assert any(ref.logical_id == "workspace_schema_epoch:v0.20" for ref in persisted_plan.resolved_assets)
    assert persisted_diagnostics.ok is True
    assert persisted_diagnostics.mode_id == "lad_codex"


def test_load_existing_plan_accepts_legacy_plan_without_artifact_contract_fields(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    compiled_plan_path = workspace_paths(workspace_root).state_dir / "compiled_plan.json"
    payload = json.loads(compiled_plan_path.read_text(encoding="utf-8"))
    payload.pop("artifact_contracts_by_id")
    payload.pop("artifact_contracts")
    compiled_plan_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    loaded = load_existing_plan(compiled_plan_path)

    assert loaded is not None
    assert loaded.artifact_contracts_by_id == {}
    assert loaded.artifact_contracts == ()


def test_load_existing_plan_rejects_missing_runtime_stage_for_blueprint_nodes(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="blueprint_" "lad_codex",
    )
    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    compiled_plan_path = workspace_paths(workspace_root).state_dir / "compiled_plan.json"
    payload = json.loads(compiled_plan_path.read_text(encoding="utf-8"))
    for graph_key in ("planning_graph",):
        for node in payload[graph_key]["nodes"]:
            if node["stage_kind_id"] in {
                "manager_blueprint",
                "contractor_blueprint",
                "evaluator_blueprint",
                "mechanic_blueprint",
            }:
                node.pop("runtime_stage", None)
    for node in payload["graphs_by_plane"]["planning"]["nodes"]:
        if node["stage_kind_id"] in {
            "manager_blueprint",
            "contractor_blueprint",
            "evaluator_blueprint",
            "mechanic_blueprint",
        }:
            node.pop("runtime_stage", None)
    compiled_plan_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    assert load_existing_plan(compiled_plan_path) is None

    currentness = inspect_workspace_plan_currentness(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="blueprint_" "lad_codex",
    )

    assert currentness.state == "missing"
    assert currentness.persisted_plan_id is None


def test_load_existing_plan_rejects_unknown_legacy_terminal_state_without_action(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    paths = workspace_paths(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=paths.runtime_root,
    )
    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    compiled_plan_path = paths.state_dir / "compiled_plan.json"
    payload = json.loads(compiled_plan_path.read_text(encoding="utf-8"))
    execution_terminal_state = payload["execution_graph"]["terminal_states"][0]
    execution_terminal_state["terminal_state_id"] = "custom_legacy_done"
    execution_terminal_state.pop("terminal_action_id", None)
    mirrored_terminal_state = payload["graphs_by_plane"]["execution"]["terminal_states"][0]
    mirrored_terminal_state["terminal_state_id"] = "custom_legacy_done"
    mirrored_terminal_state.pop("terminal_action_id", None)
    compiled_plan_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    assert load_existing_plan(compiled_plan_path) is None

    currentness = inspect_workspace_plan_currentness(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=paths.runtime_root,
    )

    assert currentness.state == "missing"
    assert currentness.persisted_plan_id is None


def test_legacy_plan_without_artifact_authority_is_stale_and_recompiled(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    paths = workspace_paths(workspace_root)

    initial = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=paths.runtime_root,
    )
    assert initial.diagnostics.ok is True
    assert initial.active_plan is not None

    compiled_plan_path = paths.state_dir / "compiled_plan.json"
    payload = json.loads(compiled_plan_path.read_text(encoding="utf-8"))
    payload.pop("artifact_contracts_by_id")
    payload.pop("artifact_contracts")
    compiled_plan_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    currentness = inspect_workspace_plan_currentness(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=paths.runtime_root,
    )
    assert currentness.state == "stale"

    recompiled = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=paths.runtime_root,
        compile_if_needed=True,
    )

    assert recompiled.diagnostics.ok is True
    assert recompiled.active_plan is not None
    assert recompiled.active_plan.artifact_contracts_by_id
    assert recompiled.active_plan.artifact_contracts
    assert recompiled.active_plan.artifact_contracts_by_id["generated_task"].canonical_filename == (
        "generated_task.json"
    )


def test_compiled_plan_rejects_mismatched_artifact_contract_surfaces(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    payload = outcome.active_plan.model_dump(mode="json")
    payload["artifact_contracts_by_id"] = {}

    with pytest.raises(ValidationError, match="artifact_contracts_by_id must match artifact_contracts"):
        CompiledRunPlan.model_validate(payload)


def test_compiled_plan_rejects_duplicate_artifact_contract_tuple_entries(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    payload = outcome.active_plan.model_dump(mode="json")
    payload["artifact_contracts"].append(payload["artifact_contracts"][0])

    with pytest.raises(ValidationError, match="artifact_contracts contains duplicate artifact id"):
        CompiledRunPlan.model_validate(payload)


def test_compile_includes_custom_work_item_family_assets(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    assets_root = _copy_builtin_assets(tmp_path)
    _write_custom_family_assets(assets_root)
    bootstrap_workspace(workspace_root, assets_root=assets_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=workspace_root / "millrace-agents",
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    assert "custom_review" in outcome.active_plan.work_item_families_by_id
    assert "custom_review_json_v1" in outcome.active_plan.document_adapters_by_id


def test_compile_asset_driven_mode_materializes_custom_planning_graph(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="blueprint_" "lad_codex",
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    plan = outcome.active_plan
    assert plan.mode_id == "blueprint_" "lad_codex"
    assert plan.planning_loop_id == "planning.blueprint"
    assert {entry.entry_key.value: entry.node_id for entry in plan.planning_graph.compiled_entries} == {
        "probe": "recon",
        "spec": "planner",
        "incident": "auditor",
        "blueprint_draft": "contractor_blueprint",
    }
    stage_kind_ids = {node.stage_kind_id for node in plan.planning_graph.nodes}
    assert {
        "manager_blueprint",
        "contractor_blueprint",
        "evaluator_blueprint",
        "mechanic_blueprint",
    }.issubset(stage_kind_ids)
    assert "mechanic_blueprint_repair_apply" in plan.runtime_effect_handlers_by_id
    repair_rule = next(
        rule
        for rule in plan.runtime_effect_rules
        if rule.rule_id == "mechanic_blueprint_repair_apply"
    )
    assert repair_rule.source_node_id == "mechanic_blueprint"
    assert repair_rule.on_outcomes == ("MECHANIC_BLUEPRINT_COMPLETE",)
    approval_policy = plan.runtime_failure_policies_by_id[
        "blueprint_approval_pre_mutation_effect_validation"
    ]
    assert approval_policy.target_node_id == "mechanic_blueprint"
    assert approval_policy.applies_to_failure_classes == (
        "generated_task_missing",
        "generated_task_invalid",
    )
    assert any(ref.logical_id == "mode:" "blueprint_" "lad_codex" for ref in plan.resolved_assets)
    assert any(ref.logical_id == "graph_loop:planning.blueprint" for ref in plan.resolved_assets)
    assert any(ref.logical_id == "stage_kind:evaluator_blueprint" for ref in plan.resolved_assets)
    assert any(ref.logical_id == "work_item_family:blueprint_draft" for ref in plan.resolved_assets)


def test_compile_asset_driven_learning_mode_materializes_planning_and_learning_graphs(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="blueprint_" "learning_lad_codex",
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    plan = outcome.active_plan
    assert plan.mode_id == "blueprint_" "learning_lad_codex"
    assert plan.loop_ids_by_plane == {
        Plane.EXECUTION: "execution.lad",
        Plane.PLANNING: "planning.blueprint",
        Plane.LEARNING: "learning.standard",
    }
    assert plan.learning_graph is not None
    assert {node.node_id for node in plan.learning_graph.nodes} == {
        "analyst",
        "professor",
        "curator",
        "librarian",
    }
    assert any(
        ref.logical_id == "mode:" "blueprint_" "learning_lad_codex"
        for ref in plan.resolved_assets
    )
    assert any(ref.logical_id == "graph_loop:planning.blueprint" for ref in plan.resolved_assets)
    assert any(ref.logical_id == "graph_loop:learning.standard" for ref in plan.resolved_assets)
    assert {
        (rule.source_stage.value, rule.on_terminal_results, rule.target_stage.value)
        for rule in plan.learning_trigger_rules
    } >= {
        ("planner", ("PLANNER_COMPLETE",), "librarian"),
    }


def test_compile_materializes_compiled_plan_graph_surface(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    plan = outcome.active_plan
    builder_node = next(node for node in plan.execution_graph.nodes if node.node_id == "builder")
    arbiter_node = next(node for node in plan.planning_graph.nodes if node.node_id == "arbiter")

    assert builder_node.entrypoint_contract_id == "builder.contract.v1"
    assert builder_node.running_status_marker == "BUILDER_RUNNING"
    assert builder_node.allowed_result_classes_by_outcome["BLOCKED"] == (
        ResultClass.BLOCKED,
        ResultClass.RECOVERABLE_FAILURE,
    )
    assert builder_node.declared_output_artifacts == ("stage_result", "report")
    assert arbiter_node.entrypoint_contract_id == "arbiter.contract.v1"
    assert arbiter_node.running_status_marker == "ARBITER_RUNNING"
    assert any(ref.logical_id == "mode:lad_codex" for ref in plan.resolved_assets)
    assert any(ref.logical_id == "stage_kind:lad_builder" for ref in plan.resolved_assets)
    assert any(ref.logical_id == "entrypoint:entrypoints/execution/lad_builder.md" for ref in plan.resolved_assets)
    assert {
        (
            policy.policy_id,
            policy.source_node_id,
            policy.on_outcome,
            policy.default_target_node_id,
            policy.metadata_stage_keys,
        )
        for policy in plan.execution_graph.compiled_resume_policies
    } == {
        (
            "execution.troubleshooter.resume",
            "troubleshooter",
            "TROUBLESHOOT_COMPLETE",
            "builder",
            ("resume_stage",),
        ),
        (
            "execution.consultant.resume",
            "consultant",
            "CONSULT_COMPLETE",
            "troubleshooter",
            ("target_stage", "resume_stage"),
        ),
    }
    assert {
        (
            policy.policy_id,
            policy.counter_name.value,
            policy.threshold,
            policy.exhausted_target_node_id,
            policy.exhausted_terminal_state_id,
        )
        for policy in plan.execution_graph.compiled_threshold_policies
    } == {
        (
            "execution.fix-needed.exhaustion",
            "fix_cycle_count",
            2,
            "troubleshooter",
            None,
        ),
        (
            "execution.blocked.recovery",
            "troubleshoot_attempt_count",
            2,
            "consultant",
            None,
        ),
    }
    assert {
        (transition.source_node_id, transition.outcome, transition.target_node_id)
        for transition in plan.execution_graph.compiled_transitions
        if transition.target_node_id is not None
    } >= {
        ("builder", "BUILDER_COMPLETE", "checker"),
        ("checker", "CHECKER_PASS", "updater"),
        ("fixer", "FIXER_COMPLETE", "doublechecker"),
        ("troubleshooter", "TROUBLESHOOT_COMPLETE", "builder"),
    }
    assert plan.planning_graph.completion_behavior is not None
    assert plan.planning_graph.completion_behavior.target_node_id == "arbiter"
    assert plan.planning_graph.compiled_completion_entry is not None
    assert plan.planning_graph.compiled_completion_entry.entry_key.value == "closure_target"
    assert plan.planning_graph.compiled_completion_entry.node_id == "arbiter"
    assert plan.planning_graph.compiled_completion_entry.stage_kind_id == "lad_arbiter"
    assert plan.planning_graph.compiled_completion_entry.request_kind == "closure_target"
    assert plan.planning_graph.compiled_completion_entry.target_selector == "active_closure_target"
    assert plan.execution_graph.transitions
    assert plan.planning_graph.transitions


def test_compile_materializes_configured_recovery_thresholds_into_compiled_plan(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(
            recovery={
                "max_fix_cycles": 1,
                "max_troubleshoot_attempts_before_consult": 1,
                "max_mechanic_attempts": 1,
            }
        ),
        requested_mode_id="standard_plain",
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    # Operator config can only lower thresholds (the tighter constraint wins).
    assert {
        (policy.policy_id, policy.threshold)
        for policy in outcome.active_plan.execution_graph.compiled_threshold_policies
    } == {
        ("execution.fix-needed.exhaustion", 1),
        ("execution.blocked.recovery", 1),
    }
    assert {
        (policy.policy_id, policy.threshold)
        for policy in outcome.active_plan.planning_graph.compiled_threshold_policies
    } == {
        ("planning.blocked.recovery", 1),
    }


def test_compile_materializes_workspace_local_mode_contract(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    _write_workspace_local_graph_mode_assets(assets_root)
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root, assets_root=assets_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(
            recovery={"max_fix_cycles": 5},
            model_assignment={"enabled": False},
        ),
        requested_mode_id="local_review_codex",
        assets_root=workspace_root / "millrace-agents",
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    assert outcome.active_plan.mode_id == "local_review_codex"
    assert outcome.active_plan.execution_loop_id == "execution.local_review"
    assert outcome.active_plan.planning_loop_id == "planning.local_review"

    execution_nodes = {node.node_id: node for node in outcome.active_plan.execution_graph.nodes}
    fix_threshold = next(
        policy
        for policy in outcome.active_plan.execution_graph.compiled_threshold_policies
        if policy.policy_id == "execution.fix-needed.exhaustion"
    )

    assert execution_nodes["checker"].model_name == "gpt-5.4"
    assert execution_nodes["checker"].thinking_level == "high"
    assert execution_nodes["checker"].model_reasoning_effort == "high"
    # Operator config can only lower; policy threshold is the binding floor.
    # With config max_fix_cycles=5 and policy threshold=2 (from graph loop),
    # min(2, 5) = 2.
    assert fix_threshold.threshold == 2
    assert {
        (asset.asset_family, asset.logical_id, asset.compile_time_path)
        for asset in outcome.active_plan.resolved_assets
        if asset.logical_id in {
            "mode:local_review_codex",
            "graph_loop:execution.local_review",
            "graph_loop:planning.local_review",
        }
    } == {
        ("mode", "mode:local_review_codex", "modes/local_review_codex.json"),
        ("graph_loop", "graph_loop:execution.local_review", "graphs/execution/local_review.json"),
        ("graph_loop", "graph_loop:planning.local_review", "graphs/planning/local_review.json"),
    }


def test_compile_materializes_learning_mode_planes_and_trigger_rules(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(
            model_assignment={"enabled": False},
            stages={
                "professor": {
                    "model": "gpt-5.4",
                    "model_reasoning_effort": "high",
                },
            }
        ),
        requested_mode_id="learning_lad_codex",
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    assert outcome.active_plan.mode_id == "learning_lad_codex"
    assert outcome.active_plan.loop_ids_by_plane == {
        Plane.EXECUTION: "execution.lad",
        Plane.PLANNING: "planning.lad",
        Plane.LEARNING: "learning.standard",
    }
    assert outcome.active_plan.learning_graph is not None
    assert [node.node_id for node in outcome.active_plan.learning_graph.nodes] == [
        "analyst",
        "professor",
        "curator",
        "librarian",
    ]
    learning_nodes = {node.node_id: node for node in outcome.active_plan.learning_graph.nodes}
    assert learning_nodes["professor"].model_name == "gpt-5.4"
    assert learning_nodes["professor"].thinking_level == "high"
    assert learning_nodes["professor"].model_reasoning_effort == "high"
    assert {node.runner_name for node in _all_nodes(outcome.active_plan)} == {"codex_cli"}
    assert {
        (
            rule.source_stage.value,
            rule.on_terminal_results,
            rule.target_stage.value,
            rule.requested_action,
        )
        for rule in outcome.active_plan.learning_trigger_rules
    } == {
        ("doublechecker", ("DOUBLECHECK_PASS",), "analyst", "improve"),
        ("troubleshooter", ("TROUBLESHOOT_COMPLETE", "BLOCKED"), "analyst", "improve"),
        ("consultant", ("CONSULT_COMPLETE", "NEEDS_PLANNING", "BLOCKED"), "analyst", "improve"),
        ("planner", ("PLANNER_COMPLETE",), "librarian", "install"),
    }
    assert "graph_loop:learning.standard" in outcome.active_plan.source_refs


def test_compile_rejects_direct_curator_trigger_without_destination(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    paths = workspace_paths(workspace_root)
    mode_path = paths.runtime_root / "modes" / "learning_lad_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["learning_trigger_rules"] = [
        {
            "rule_id": "execution.doublechecker.unsafe-to-curator",
            "source_plane": "execution",
            "source_stage": "doublechecker",
            "on_terminal_results": ["DOUBLECHECK_PASS"],
            "target_stage": "curator",
            "requested_action": "improve",
        }
    ]
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="learning_lad_codex",
        assets_root=paths.runtime_root,
        refuse_stale_last_known_good=True,
    )

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "targets curator without a safe destination" in outcome.diagnostics.errors[0]


def test_compile_accepts_direct_curator_trigger_with_destination(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    paths = workspace_paths(workspace_root)
    mode_path = paths.runtime_root / "modes" / "learning_lad_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["learning_trigger_rules"] = [
        {
            "rule_id": "execution.doublechecker.precise-to-curator",
            "source_plane": "execution",
            "source_stage": "doublechecker",
            "on_terminal_results": ["DOUBLECHECK_PASS"],
            "target_stage": "curator",
            "requested_action": "improve",
            "target_skill_id": "doublechecker-core",
        }
    ]
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="learning_lad_codex",
        assets_root=paths.runtime_root,
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    rule = outcome.active_plan.learning_trigger_rules[0]
    assert rule.target_stage.value == "curator"
    assert rule.target_skill_id == "doublechecker-core"


def test_preview_graph_loop_plan_compiles_synthetic_discovered_loop(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    _write_synthetic_stage_kind_asset(assets_root)
    _write_synthetic_graph_loop_asset(assets_root)

    graph_plan = preview_graph_loop_plan(
        "execution.synthetic",
        config=RuntimeConfig(),
        assets_root=assets_root,
    )

    entry_nodes = {entry.entry_key.value: entry.node_id for entry in graph_plan.entry_nodes}

    assert graph_plan.loop_id == "execution.synthetic"
    assert graph_plan.plane is Plane.EXECUTION
    assert [node.stage_kind_id for node in graph_plan.nodes] == ["synthetic_worker"]
    assert entry_nodes == {"task": "synthetic_worker"}
    assert {state.terminal_state_id for state in graph_plan.terminal_states} == {
        "synthetic_complete",
        "blocked",
    }


def test_compiled_graph_export_surfaces_terminal_action_metadata(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex",
    )

    assert outcome.active_plan is not None
    graph_export = export_compiled_stage_graph(outcome.active_plan, Plane.EXECUTION)
    update_complete = next(
        state
        for state in graph_export.terminal_states
        if state.terminal_state_id == "update_complete"
    )
    assert update_complete.terminal_action_id == "complete_work_item"
    assert update_complete.terminal_action_router_consequence == "idle"
    assert update_complete.lifecycle_mutation_plan_id == "complete_work_item"
    assert update_complete.lifecycle_action_id == "complete"
    assert update_complete.writes_status == "UPDATE_COMPLETE"


def test_standard_plain_alias_and_lad_codex_compile_to_identical_plan_ids(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    alias_outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    canonical_outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex",
    )

    assert alias_outcome.diagnostics.ok is True
    assert canonical_outcome.diagnostics.ok is True
    assert alias_outcome.active_plan is not None
    assert canonical_outcome.active_plan is not None
    assert alias_outcome.active_plan.mode_id == "lad_codex"
    assert canonical_outcome.active_plan.mode_id == "lad_codex"
    assert alias_outcome.active_plan.compiled_plan_id == canonical_outcome.active_plan.compiled_plan_id


def test_lad_pi_compiles_with_pi_runner_bound_for_every_node(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="lad_pi",
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    assert outcome.active_plan.mode_id == "lad_pi"
    assert {node.runner_name for node in _all_nodes(outcome.active_plan)} == {"pi_rpc"}


def test_compile_resolves_runner_neutral_thinking_precedence_for_pi(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)

    execution_graph_path = assets_root / "graphs" / "execution" / "lad.json"
    execution_graph = json.loads(execution_graph_path.read_text(encoding="utf-8"))
    for node in execution_graph["nodes"]:
        if node["node_id"] == "builder":
            node["thinking_level"] = "low"
    execution_graph_path.write_text(json.dumps(execution_graph, indent=2) + "\n", encoding="utf-8")

    mode_path = assets_root / "modes" / "lad_pi.json"
    mode = json.loads(mode_path.read_text(encoding="utf-8"))
    mode["stage_thinking_bindings"] = {
        "builder": "high",
        "checker": None,
    }
    mode_path.write_text(json.dumps(mode, indent=2) + "\n", encoding="utf-8")

    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(
            model_assignment={"enabled": False},
            stages={
                "builder": {"thinking_level": "medium"},
                "checker": {"thinking_level": "xhigh"},
                "fixer": {"model_reasoning_effort": "high"},
            }
        ),
        requested_mode_id="lad_pi",
        assets_root=assets_root,
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    nodes = {node.node_id: node for node in outcome.active_plan.execution_graph.nodes}
    assert nodes["builder"].thinking_level == "high"
    assert nodes["builder"].model_reasoning_effort is None
    assert nodes["checker"].thinking_level is None
    assert nodes["checker"].model_reasoning_effort is None
    assert nodes["fixer"].thinking_level == "high"
    assert nodes["fixer"].model_reasoning_effort is None


def test_compile_materializes_graph_loop_thinking_default(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)

    execution_graph_path = assets_root / "graphs" / "execution" / "lad.json"
    execution_graph = json.loads(execution_graph_path.read_text(encoding="utf-8"))
    for node in execution_graph["nodes"]:
        if node["node_id"] == "builder":
            node["thinking_level"] = "high"
    execution_graph_path.write_text(json.dumps(execution_graph, indent=2) + "\n", encoding="utf-8")

    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex",
        assets_root=assets_root,
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    builder = next(node for node in outcome.active_plan.execution_graph.nodes if node.node_id == "builder")
    assert builder.thinking_level == "medium"
    assert builder.model_reasoning_effort == "medium"
    assert builder.model_assignment_alias_id == "standard"


def test_compile_rejects_stage_thinking_binding_outside_selected_loops(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    mode_path = assets_root / "modes" / "lad_codex.json"
    mode = json.loads(mode_path.read_text(encoding="utf-8"))
    mode["stage_thinking_bindings"] = {"professor": "high"}
    mode_path.write_text(json.dumps(mode, indent=2) + "\n", encoding="utf-8")

    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex",
        assets_root=assets_root,
    )

    assert outcome.diagnostics.ok is False
    assert outcome.diagnostics.errors == (
        "Mode map `stage_thinking_bindings` references stage outside selected loops: professor",
    )


def test_compile_rejects_custom_stage_entrypoint_override_outside_selected_loops(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    mode_path = assets_root / "modes" / "lad_codex.json"
    mode = json.loads(mode_path.read_text(encoding="utf-8"))
    mode["stage_entrypoint_overrides"] = {"not_a_stage": "entrypoints/planning/planner.md"}
    mode_path.write_text(json.dumps(mode, indent=2) + "\n", encoding="utf-8")

    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex",
        assets_root=assets_root,
    )

    assert outcome.diagnostics.ok is False
    assert outcome.diagnostics.errors == (
        "Mode map `stage_entrypoint_overrides` references stage outside selected loops: not_a_stage",
    )


def test_compile_resolves_minimal_required_stage_skills(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    required_by_stage = {
        node.node_id: node.required_skill_paths
        for node in _all_nodes(outcome.active_plan)
    }

    assert len(required_by_stage) == 13
    assert required_by_stage["builder"] == ("skills/stage/execution/builder-core/SKILL.md",)
    assert required_by_stage["checker"] == ("skills/stage/execution/checker-core/SKILL.md",)
    assert required_by_stage["recon"] == ("skills/stage/planning/recon-core/SKILL.md",)
    assert required_by_stage["planner"] == ("skills/stage/planning/planner-core/SKILL.md",)
    assert required_by_stage["auditor"] == ("skills/stage/planning/auditor-core/SKILL.md",)
    assert required_by_stage["arbiter"] == ("skills/stage/planning/arbiter-core/SKILL.md",)


def test_compile_plan_identity_changes_when_graph_completion_behavior_changes(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    baseline = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )

    assert baseline.diagnostics.ok is True
    assert baseline.active_plan is not None

    assets_root = _copy_builtin_assets(tmp_path / "mutated")
    planning_graph_path = assets_root / "graphs" / "planning" / "lad.json"
    payload = json.loads(planning_graph_path.read_text(encoding="utf-8"))
    payload["completion_behavior"]["skip_if_already_closed"] = False
    planning_graph_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    mutated = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )

    assert mutated.diagnostics.ok is True
    assert mutated.active_plan is not None
    assert mutated.active_plan.compiled_plan_id != baseline.active_plan.compiled_plan_id


def test_compile_uses_one_hour_default_stage_timeout_when_stage_config_omits_it(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    assert {node.timeout_seconds for node in _all_nodes(outcome.active_plan)} == {3600}


def test_compile_surfaces_stage_skill_attachments_without_role_overlays(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    workspace_skill = workspace_paths(workspace_root).runtime_root / "skills" / "execution" / "builder.md"
    workspace_skill.parent.mkdir(parents=True, exist_ok=True)
    workspace_skill.write_text("builder attached skill\n", encoding="utf-8")

    assets_root = _copy_builtin_assets(tmp_path)
    mode_path = assets_root / "modes" / "lad_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["stage_skill_additions"] = {
        "builder": ["skills/execution/builder.md"],
    }
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    builder_plan = next(node for node in outcome.active_plan.execution_graph.nodes if node.node_id == "builder")

    assert builder_plan.required_skill_paths == ("skills/stage/execution/builder-core/SKILL.md",)
    assert builder_plan.attached_skill_additions == ("skills/execution/builder.md",)
    assert "role_overlays" not in builder_plan.model_dump(mode="json")


def test_compile_rejects_invalid_entrypoint_override_deterministically(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    assets_root = _copy_builtin_assets(tmp_path)
    mode_path = assets_root / "modes" / "lad_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["stage_entrypoint_overrides"] = {"builder": "roles/not-an-entrypoint.md"}
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert outcome.used_last_known_good is False
    assert outcome.diagnostics.errors == (
        "Invalid entrypoint override for stage `builder`: roles/not-an-entrypoint.md",
    )


def test_compile_rejects_entrypoint_override_path_traversal(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    assets_root = _copy_builtin_assets(tmp_path)
    mode_path = assets_root / "modes" / "lad_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["stage_entrypoint_overrides"] = {"builder": "../entrypoints/execution/builder.md"}
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert outcome.used_last_known_good is False
    assert outcome.diagnostics.errors == (
        "Invalid entrypoint override for stage `builder`: ../entrypoints/execution/builder.md",
    )


def test_compile_ignores_removed_stage_role_overlay_field_in_mode_assets(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    assets_root = _copy_builtin_assets(tmp_path)
    mode_path = assets_root / "modes" / "lad_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["stage_role_overlays"] = {"builder": ["roles/execution/builder_advisory.md"]}
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert outcome.used_last_known_good is False
    assert outcome.diagnostics.errors == (
        "Invalid mode definition in asset: "
        f"{assets_root / 'modes' / 'lad_codex.json'}",
    )


def test_recompile_failure_keeps_last_known_good_plan(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    initial = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert initial.diagnostics.ok is True
    assert initial.active_plan is not None

    paths = workspace_paths(workspace_root)
    compiled_plan_path = paths.state_dir / "compiled_plan.json"
    baseline_plan_text = compiled_plan_path.read_text(encoding="utf-8")

    assets_root = _copy_builtin_assets(tmp_path / "recompile")
    mode_path = assets_root / "modes" / "lad_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["planning_loop_id"] = "planning.unknown"
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    failed = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )

    assert failed.diagnostics.ok is False
    assert failed.active_plan is not None
    assert failed.used_last_known_good is True
    assert failed.active_plan.compiled_plan_id == initial.active_plan.compiled_plan_id
    assert compiled_plan_path.read_text(encoding="utf-8") == baseline_plan_text

    diagnostics_path = paths.state_dir / "compile_diagnostics.json"
    diagnostics = CompileDiagnostics.model_validate_json(diagnostics_path.read_text(encoding="utf-8"))
    assert diagnostics.ok is False
    assert diagnostics.mode_id == "lad_codex"
    assert diagnostics.errors[0] == "Unknown graph loop id: planning.unknown"


def test_inspect_workspace_plan_currentness_detects_current_and_stale_inputs(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    paths = workspace_paths(workspace_root)

    compiled = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex",
        assets_root=paths.runtime_root,
    )
    assert compiled.active_plan is not None

    current = inspect_workspace_plan_currentness(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex",
        assets_root=paths.runtime_root,
    )
    assert current.state == "current"
    assert current.persisted_plan_id == compiled.active_plan.compiled_plan_id

    (paths.runtime_root / "entrypoints" / "execution" / "lad_builder.md").write_text(
        "stale builder entrypoint\n",
        encoding="utf-8",
    )
    stale = inspect_workspace_plan_currentness(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex",
        assets_root=paths.runtime_root,
    )

    assert stale.state == "stale"
    assert stale.persisted_plan_id == compiled.active_plan.compiled_plan_id


def test_inspect_workspace_plan_currentness_ignores_unreferenced_asset_changes(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    paths = workspace_paths(workspace_root)

    compiled = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex",
        assets_root=paths.runtime_root,
    )
    assert compiled.active_plan is not None

    (paths.runtime_root / "skills" / "unused-skill.md").write_text(
        "unused skill drift\n",
        encoding="utf-8",
    )
    (paths.runtime_root / "entrypoints" / "execution" / "unused.md").write_text(
        "unused entrypoint drift\n",
        encoding="utf-8",
    )

    current = inspect_workspace_plan_currentness(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex",
        assets_root=paths.runtime_root,
    )

    assert current.state == "current"
    assert current.persisted_plan_id == compiled.active_plan.compiled_plan_id


def test_inspect_workspace_plan_currentness_detects_attached_skill_drift(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    paths = workspace_paths(workspace_root)
    workspace_skill = paths.runtime_root / "skills" / "execution" / "builder.md"
    workspace_skill.parent.mkdir(parents=True, exist_ok=True)
    workspace_skill.write_text("builder attached skill\n", encoding="utf-8")

    assets_root = _copy_builtin_assets(tmp_path)
    mode_path = assets_root / "modes" / "lad_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["stage_skill_additions"] = {
        "builder": ["skills/execution/builder.md"],
    }
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    compiled = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )
    assert compiled.active_plan is not None

    workspace_skill.write_text("attached skill drift\n", encoding="utf-8")

    stale = inspect_workspace_plan_currentness(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )

    assert stale.state == "stale"
    assert stale.persisted_plan_id == compiled.active_plan.compiled_plan_id


def test_inspect_workspace_plan_currentness_detects_missing_attached_skill_becoming_present(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    paths = workspace_paths(workspace_root)

    assets_root = _copy_builtin_assets(tmp_path)
    mode_path = assets_root / "modes" / "lad_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["stage_skill_additions"] = {
        "builder": ["skills/execution/builder.md"],
    }
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    compiled = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )
    assert compiled.active_plan is not None

    current = inspect_workspace_plan_currentness(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )
    assert current.state == "current"

    workspace_skill = paths.runtime_root / "skills" / "execution" / "builder.md"
    workspace_skill.parent.mkdir(parents=True, exist_ok=True)
    workspace_skill.write_text("late attached skill\n", encoding="utf-8")

    stale = inspect_workspace_plan_currentness(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )

    assert stale.state == "stale"
    assert stale.persisted_plan_id == compiled.active_plan.compiled_plan_id


def test_compile_refuses_stale_last_known_good_when_requested(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    paths = workspace_paths(workspace_root)

    initial = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex",
        assets_root=paths.runtime_root,
    )
    assert initial.diagnostics.ok is True
    assert initial.active_plan is not None

    mode_path = paths.runtime_root / "modes" / "lad_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["loop_ids_by_plane"]["planning"] = "planning.unknown"
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    failed = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex",
        assets_root=paths.runtime_root,
        refuse_stale_last_known_good=True,
    )

    assert failed.diagnostics.ok is False
    assert failed.active_plan is None
    assert failed.used_last_known_good is False


def test_compile_minimal_three_plane_fixture_succeeds_with_three_planes(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root, assets_root=assets_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="minimal_three_plane",
        assets_root=workspace_root / "millrace-agents",
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    plan = outcome.active_plan

    # ── mode identity ──────────────────────────────────────────────────────
    assert plan.mode_id == "minimal_three_plane"
    assert "minimal_three_plane" not in SHIPPED_MODE_IDS

    # ── three-plane resolution ─────────────────────────────────────────────
    assert plan.loop_ids_by_plane == {
        Plane.EXECUTION: "execution.minimal_three_plane",
        Plane.PLANNING: "planning.minimal_three_plane",
        Plane.LEARNING: "learning.minimal_three_plane",
    }
    assert plan.execution_loop_id == "execution.minimal_three_plane"
    assert plan.planning_loop_id == "planning.minimal_three_plane"
    assert plan.learning_loop_id == "learning.minimal_three_plane"
    assert plan.learning_graph is not None
    assert set(plan.graphs_by_plane) == {Plane.EXECUTION, Plane.PLANNING, Plane.LEARNING}

    # ── execution graph ────────────────────────────────────────────────────
    exec_nodes = {node.node_id: node for node in plan.execution_graph.nodes}
    worker = exec_nodes["basic_worker"]
    assert worker.stage_kind_id == "basic_worker"
    assert worker.runtime_stage == "builder"
    assert worker.plane is Plane.EXECUTION
    assert worker.runner_name == "pi_rpc"
    assert worker.request_context_profile_id == "builder.default"
    assert worker.context_render_plan_id == "stage_request.default.v1"
    assert worker.entrypoint_path == "entrypoints/execution/lad_builder.md"
    assert worker.required_skill_paths == ("skills/stage/execution/builder-core/SKILL.md",)
    assert worker.allowed_work_item_families == ("task",)
    assert worker.running_status_marker == "BASIC_EXECUTION_RUNNING"
    assert worker.allowed_result_classes_by_outcome == {
        "BASIC_EXECUTION_COMPLETE": (ResultClass.SUCCESS,),
        "BASIC_EXECUTION_BLOCKED": (ResultClass.BLOCKED, ResultClass.RECOVERABLE_FAILURE),
    }

    exec_entry_map = {entry.entry_key.value: entry.node_id for entry in plan.execution_graph.compiled_entries}
    assert exec_entry_map == {"task": "basic_worker"}

    exec_terminal_ids = {state.terminal_state_id for state in plan.execution_graph.terminal_states}
    assert exec_terminal_ids == {"worker_complete", "blocked"}

    # ── planning graph ─────────────────────────────────────────────────────
    plan_nodes = {node.node_id: node for node in plan.planning_graph.nodes}
    planner = plan_nodes["basic_planner"]
    assert planner.stage_kind_id == "basic_planner"
    assert planner.runtime_stage == "planner"
    assert planner.plane is Plane.PLANNING
    assert planner.runner_name == "pi_rpc"
    assert planner.request_context_profile_id == "planner.default"
    assert planner.context_render_plan_id == "stage_request.default.v1"
    assert planner.entrypoint_path == "entrypoints/planning/lad_planner.md"
    assert planner.required_skill_paths == ("skills/stage/planning/planner-core/SKILL.md",)
    assert planner.allowed_work_item_families == ("spec",)
    assert planner.running_status_marker == "BASIC_PLANNING_RUNNING"
    assert planner.allowed_result_classes_by_outcome == {
        "BASIC_PLANNING_COMPLETE": (ResultClass.SUCCESS,),
        "BASIC_PLANNING_BLOCKED": (ResultClass.BLOCKED, ResultClass.RECOVERABLE_FAILURE),
    }

    plan_entry_map = {entry.entry_key.value: entry.node_id for entry in plan.planning_graph.compiled_entries}
    assert plan_entry_map == {"spec": "basic_planner"}

    plan_terminal_ids = {state.terminal_state_id for state in plan.planning_graph.terminal_states}
    assert plan_terminal_ids == {"planner_complete", "blocked"}

    # ── learning graph ─────────────────────────────────────────────────────
    learn_nodes = {node.node_id: node for node in plan.learning_graph.nodes}
    learner = learn_nodes["basic_learner"]
    assert learner.stage_kind_id == "basic_learner"
    assert learner.runtime_stage == "analyst"
    assert learner.plane is Plane.LEARNING
    assert learner.runner_name == "pi_rpc"
    assert learner.request_context_profile_id == "analyst.default"
    assert learner.context_render_plan_id == "stage_request.default.v1"
    assert learner.entrypoint_path == "entrypoints/learning/analyst.md"
    assert learner.required_skill_paths == ("skills/stage/learning/analyst-core/SKILL.md",)
    assert learner.allowed_work_item_families == ("learning_request",)
    assert learner.running_status_marker == "BASIC_LEARNING_RUNNING"
    assert learner.allowed_result_classes_by_outcome == {
        "BASIC_LEARNING_COMPLETE": (ResultClass.SUCCESS,),
        "BASIC_LEARNING_NOOP": (ResultClass.NO_OP,),
        "BASIC_LEARNING_BLOCKED": (ResultClass.BLOCKED, ResultClass.RECOVERABLE_FAILURE),
    }

    learn_entry_map = {entry.entry_key.value: entry.node_id for entry in plan.learning_graph.compiled_entries}
    assert learn_entry_map == {"learning_request": "basic_learner"}

    learn_terminal_ids = {state.terminal_state_id for state in plan.learning_graph.terminal_states}
    assert learn_terminal_ids == {"learner_complete", "learner_noop", "blocked"}

    # ── resolved assets cover the fixture ─────────────────────────────────
    resolved_ids = {ref.logical_id for ref in plan.resolved_assets}
    assert "mode:minimal_three_plane" in resolved_ids
    assert "graph_loop:execution.minimal_three_plane" in resolved_ids
    assert "graph_loop:planning.minimal_three_plane" in resolved_ids
    assert "graph_loop:learning.minimal_three_plane" in resolved_ids
    assert "stage_kind:basic_worker" in resolved_ids
    assert "stage_kind:basic_planner" in resolved_ids
    assert "stage_kind:basic_learner" in resolved_ids


# ── config-swap compilation tests ──────────────────────────────────────────


def test_config_swap_standard_millrace_compiles_with_standard_stages(
    tmp_path: Path,
) -> None:
    """standard_millrace compiles with all standard execution and planning
    stages."""
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_millrace",
    )

    assert outcome.diagnostics.ok is True, (
        f"standard_millrace compilation failed: {outcome.diagnostics.errors}"
    )
    assert outcome.active_plan is not None

    plan = outcome.active_plan
    assert plan.mode_id == "lad_pi"
    assert plan.execution_loop_id == "execution.lad"
    assert plan.planning_loop_id == "planning.lad"

    exec_nodes = {node.stage_kind_id for node in plan.execution_graph.nodes}
    assert "lad_builder" in exec_nodes
    assert "lad_checker" in exec_nodes
    assert "lad_fixer" in exec_nodes
    assert "lad_troubleshooter" in exec_nodes

    plan_nodes = {node.stage_kind_id for node in plan.planning_graph.nodes}
    assert "recon" in plan_nodes
    assert "lad_planner" in plan_nodes
    assert "lad_manager" in plan_nodes
    assert "lad_arbiter" in plan_nodes

    # All nodes use pi_rpc
    assert {node.runner_name for node in plan.execution_graph.nodes} == {"pi_rpc"}
    assert {node.runner_name for node in plan.planning_graph.nodes} == {"pi_rpc"}


def test_config_swap_learning_enabled_millrace_compiles_with_learning_plane(
    tmp_path: Path,
) -> None:
    """learning_enabled_millrace compiles with Learning plane and triggers."""
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="learning_enabled_millrace",
    )

    assert outcome.diagnostics.ok is True, (
        f"learning_enabled_millrace compilation failed: {outcome.diagnostics.errors}"
    )
    assert outcome.active_plan is not None

    plan = outcome.active_plan
    assert plan.mode_id == "learning_lad_pi"
    assert plan.learning_graph is not None
    assert Plane.LEARNING in plan.loop_ids_by_plane
    assert plan.learning_loop_id == "learning.standard"

    # Learning graph stages
    learn_nodes = {node.stage_kind_id for node in plan.learning_graph.nodes}
    assert learn_nodes == {"analyst", "professor", "curator", "librarian"}

    # Learning triggers fire from execution and planning into learning
    trigger_rules = {
        (rule.source_stage.value, rule.target_stage.value, rule.requested_action.value)
        for rule in plan.learning_trigger_rules
    }
    assert ("doublechecker", "analyst", "improve") in trigger_rules
    assert ("troubleshooter", "analyst", "improve") in trigger_rules
    assert ("consultant", "analyst", "improve") in trigger_rules
    assert ("planner", "librarian", "install") in trigger_rules


def test_config_swap_recovery_heavy_millrace_compiles_elevated_thresholds(
    tmp_path: Path,
) -> None:
    """recovery_heavy_millrace compiles with different recovery thresholds than
    standard configs, driven by mode-declared policy selection."""
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="recovery_heavy_millrace",
    )

    assert outcome.diagnostics.ok is True, (
        f"recovery_heavy_millrace compilation failed: {outcome.diagnostics.errors}"
    )
    assert outcome.active_plan is not None

    plan = outcome.active_plan
    assert plan.mode_id == "recovery_heavy_millrace"

    # Recovery-heavy mode selects recovery_heavy_policies.json which
    # lowers blocked-recovery thresholds to 1.
    thresholds = {
        (p.policy_id, p.threshold)
        for graph in plan.graphs_by_plane.values()
        for p in graph.compiled_threshold_policies
    }
    assert ("execution.blocked.recovery", 1) in thresholds
    assert ("planning.blocked.recovery", 1) in thresholds

    # Compare with standard thresholds
    standard_ws = tmp_path / "standard_ws"
    bootstrap_workspace(standard_ws)
    standard_outcome = compile_and_persist_workspace_plan(
        standard_ws,
        config=RuntimeConfig(),
        requested_mode_id="standard_millrace",
    )
    assert standard_outcome.active_plan is not None

    standard_thresholds = {
        (p.policy_id, p.threshold)
        for graph in standard_outcome.active_plan.graphs_by_plane.values()
        for p in graph.compiled_threshold_policies
    }

    assert thresholds != standard_thresholds, (
        f"recovery_heavy thresholds {thresholds} should differ from "
        f"standard thresholds {standard_thresholds}"
    )


def test_config_swap_generic_two_plane_fixture_compiles_minimal_planes(
    tmp_path: Path,
) -> None:
    """generic_two_plane_fixture compiles two planes with only basic
    stage kinds."""
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="generic_two_plane_fixture",
    )

    assert outcome.diagnostics.ok is True, (
        f"generic_two_plane_fixture compilation failed: {outcome.diagnostics.errors}"
    )
    assert outcome.active_plan is not None

    plan = outcome.active_plan
    assert plan.mode_id == "generic_two_plane_fixture"
    assert plan.learning_graph is None
    assert Plane.LEARNING not in plan.loop_ids_by_plane

    # Only two planes
    assert set(plan.graphs_by_plane) == {Plane.EXECUTION, Plane.PLANNING}

    # Only basic stage kinds
    all_stages = {
        node.stage_kind_id
        for graph in plan.graphs_by_plane.values()
        for node in graph.nodes
    }
    assert all_stages == {"basic_worker", "basic_planner"}

    # No threshold policies (minimal graph)
    for graph in plan.graphs_by_plane.values():
        assert graph.compiled_threshold_policies == ()

    # Entry points use basic stages
    exec_entry_map = {
        entry.entry_key.value: entry.node_id
        for entry in plan.execution_graph.compiled_entries
    }
    assert exec_entry_map == {"task": "basic_worker"}

    plan_entry_map = {
        entry.entry_key.value: entry.node_id
        for entry in plan.planning_graph.compiled_entries
    }
    assert plan_entry_map == {"spec": "basic_planner"}


def test_config_swap_same_kernel_different_compiled_plans(
    tmp_path: Path,
) -> None:
    """The same kernel produces materially different compiled plans when
    config-swapping across all five configs."""
    config_specs = [
        ("minimal_three_plane", RuntimeConfig()),
        ("standard_millrace", RuntimeConfig()),
        ("learning_enabled_millrace", RuntimeConfig()),
        ("recovery_heavy_millrace", RuntimeConfig()),
        ("generic_two_plane_fixture", RuntimeConfig()),
    ]

    plans: dict[str, "CompiledRunPlan"] = {}

    for mode_id, rt_config in config_specs:
        workspace_root = tmp_path / mode_id
        bootstrap_workspace(workspace_root)

        outcome = compile_and_persist_workspace_plan(
            workspace_root,
            config=rt_config,
            requested_mode_id=mode_id,
        )

        assert outcome.diagnostics.ok is True, (
            f"Config-swap `{mode_id}` compilation failed: {outcome.diagnostics.errors}"
        )
        assert outcome.active_plan is not None
        plans[mode_id] = outcome.active_plan

    # ── every plan has a unique compiled_plan_id ───────────────────────────
    plan_ids = {plan.compiled_plan_id for plan in plans.values()}
    assert len(plan_ids) == len(plans), (
        f"Config-swap produced duplicate compiled_plan_ids: {plan_ids}"
    )

    # ── stage-kinds differ across configs ──────────────────────────────────
    stages_by_config = {
        mode_id: {
            node.stage_kind_id
            for graph in plan.graphs_by_plane.values()
            for node in graph.nodes
        }
        for mode_id, plan in plans.items()
    }

    # minimal_three_plane and generic_two_plane_fixture are distinct from
    # standard/learning/recovery
    minimal_kinds = stages_by_config["minimal_three_plane"]
    generic_kinds = stages_by_config["generic_two_plane_fixture"]

    assert minimal_kinds != generic_kinds, (
        f"minimal_three_plane and generic_two_plane_fixture should differ: "
        f"{minimal_kinds} vs {generic_kinds}"
    )

    for mode_id in ("standard_millrace", "learning_enabled_millrace",
                     "recovery_heavy_millrace"):
        assert stages_by_config[mode_id] != minimal_kinds, (
            f"{mode_id} should differ from minimal_three_plane"
        )

    # ── learning_enabled has triggers; standard does not ───────────────────
    assert len(plans["learning_enabled_millrace"].learning_trigger_rules) > 0
    assert plans["standard_millrace"].learning_trigger_rules == ()
    assert plans["learning_enabled_millrace"].learning_graph is not None
    assert plans["standard_millrace"].learning_graph is None

    # ── recovery_heavy thresholds differ from standard ─────────────────────
    heavy_thresholds = {
        (p.policy_id, p.threshold)
        for graph in plans["recovery_heavy_millrace"].graphs_by_plane.values()
        for p in graph.compiled_threshold_policies
    }
    standard_thresholds = {
        (p.policy_id, p.threshold)
        for graph in plans["standard_millrace"].graphs_by_plane.values()
        for p in graph.compiled_threshold_policies
    }
    assert heavy_thresholds != standard_thresholds

    # ── no config-swap test mutated kernel source code ────────────────────
    # All plans were produced by the same compile function without code edits.
