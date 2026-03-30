from __future__ import annotations

from datetime import datetime, timezone
import importlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from millrace_engine.assets.resolver import AssetResolver, AssetSourceKind
from millrace_engine.baseline_assets import iter_packaged_baseline_directories
from millrace_engine.contracts import (
    LoopConfigDefinition,
    PersistedObjectKind,
    PersistedObjectStatus,
    RegisteredStageKindDefinition,
    RegistryObjectRef,
    RegistrySourceKind,
    RegistryTier,
    TaskAuthoringProfileDefinition,
)
from millrace_engine.registry import (
    RegistryError,
    RegistryLayer,
    ensure_workspace_registry_layout,
    demote_workspace_registry_object_to_legacy,
    discover_registry_state,
    persist_workspace_registry_object,
    promote_workspace_registry_object,
    registry_json_relative_path,
    render_registry_companion_markdown,
    render_registry_definition_json,
)


MILLRACE_ROOT = Path(__file__).resolve().parents[1]
ASSETS_REGISTRY_ROOT = MILLRACE_ROOT / "millrace_engine" / "assets" / "registry"
T0 = datetime(2026, 3, 18, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 3, 18, 1, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 3, 18, 2, 0, tzinfo=timezone.utc)


def _task_profile_definition(
    *,
    object_id: str,
    tier: str = "ad_hoc",
    title: str = "Custom Task Profile",
    source_kind: str = "workspace_defined",
) -> TaskAuthoringProfileDefinition:
    return TaskAuthoringProfileDefinition.model_validate(
        {
            "id": object_id,
            "version": "1.0.0",
            "kind": "task_authoring_profile",
            "tier": tier,
            "title": title,
            "summary": "Workspace-defined task-authoring profile.",
            "source": {"kind": source_kind},
            "payload": {
                "decomposition_style": "narrow",
                "expected_card_count": {"min_cards": 1, "max_cards": 3},
                "allowed_task_breadth": "focused",
                "required_metadata_fields": ("spec_id",),
                "acceptance_profile": "standard",
                "gate_strictness": "standard",
                "single_card_synthesis_allowed": False,
                "research_assumption": "consult_if_ambiguous",
                "suitable_use_cases": ("Small targeted changes",),
            },
        }
    )


def _write_workspace_registry_document(
    workspace_root: Path,
    definition: TaskAuthoringProfileDefinition,
) -> tuple[Path, Path]:
    root = ensure_workspace_registry_layout(workspace_root)
    json_path = root / registry_json_relative_path(definition)
    markdown_path = json_path.with_suffix(".md")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(render_registry_definition_json(definition), encoding="utf-8")
    markdown_path.write_text(render_registry_companion_markdown(definition), encoding="utf-8")
    return json_path, markdown_path


def test_discover_registry_state_reads_packaged_defaults_and_workspace_shadowing(tmp_path: Path) -> None:
    state = discover_registry_state(tmp_path)

    packaged_keys = {document.key for document in state.packaged}
    assert ("registered_stage_kind", "execution.builder", "1.0.0") in packaged_keys
    assert ("registered_stage_kind", "execution.integration", "1.0.0") in packaged_keys
    assert ("registered_stage_kind", "execution.qa", "1.0.0") in packaged_keys
    assert ("registered_stage_kind", "execution.hotfix", "1.0.0") in packaged_keys
    assert ("registered_stage_kind", "execution.doublecheck", "1.0.0") in packaged_keys
    assert ("registered_stage_kind", "execution.troubleshoot", "1.0.0") in packaged_keys
    assert ("registered_stage_kind", "execution.consult", "1.0.0") in packaged_keys
    assert ("registered_stage_kind", "execution.update", "1.0.0") in packaged_keys
    assert ("registered_stage_kind", "execution.large-plan", "1.0.0") in packaged_keys
    assert ("registered_stage_kind", "execution.large-execute", "1.0.0") in packaged_keys
    assert ("registered_stage_kind", "execution.reassess", "1.0.0") in packaged_keys
    assert ("registered_stage_kind", "execution.refactor", "1.0.0") in packaged_keys
    assert ("task_authoring_profile", "task_authoring.narrow", "1.0.0") in packaged_keys
    assert ("model_profile", "model.default", "1.0.0") in packaged_keys
    assert ("loop_config", "execution.large", "1.0.0") in packaged_keys
    assert ("loop_config", "execution.large_direct_update", "1.0.0") in packaged_keys
    assert ("loop_config", "execution.quick_build", "1.0.0") in packaged_keys
    assert ("loop_config", "execution.standard", "1.0.0") in packaged_keys
    assert ("mode", "mode.default_autonomous", "1.0.0") in packaged_keys
    assert ("mode", "mode.large", "1.0.0") in packaged_keys
    assert ("mode", "mode.large_direct_update", "1.0.0") in packaged_keys
    assert ("mode", "mode.standard", "1.0.0") in packaged_keys
    assert not state.workspace
    assert state.catalog is not None

    for document in state.packaged:
        markdown_path = ASSETS_REGISTRY_ROOT / document.markdown_relative_path
        assert markdown_path.read_text(encoding="utf-8") == render_registry_companion_markdown(
            document.definition
        )

    packaged_loop = next(
        document.definition for document in state.packaged if document.key == ("loop_config", "execution.quick_build", "1.0.0")
    )
    workspace_loop = packaged_loop.__class__.model_validate(
        {
            **packaged_loop.model_dump(mode="json"),
            "title": "Workspace Quick Build",
            "summary": "Workspace loop shadows the packaged default.",
            "source": {"kind": "workspace_defined"},
        }
    )
    persist_workspace_registry_object(tmp_path, workspace_loop, timestamp=T0)

    shadowed_state = discover_registry_state(tmp_path)
    effective_loop = next(
        document for document in shadowed_state.effective if document.key == ("loop_config", "execution.quick_build", "1.0.0")
    )

    assert effective_loop.layer is RegistryLayer.WORKSPACE
    assert effective_loop.definition.title == "Workspace Quick Build"
    assert ("loop_config", "execution.quick_build", "1.0.0") in {
        document.key for document in shadowed_state.shadowed_packaged
    }


def test_persist_workspace_registry_object_writes_json_and_synced_markdown(tmp_path: Path) -> None:
    definition = _task_profile_definition(object_id="task_authoring.custom")

    report = persist_workspace_registry_object(tmp_path, definition, timestamp=T0)

    assert report.created is True
    assert report.json_path.relative_to(tmp_path).as_posix() == (
        "agents/registry/task_authoring/task_authoring.custom__1.0.0.json"
    )
    assert report.markdown_path.relative_to(tmp_path).as_posix() == (
        "agents/registry/task_authoring/task_authoring.custom__1.0.0.md"
    )
    assert report.json_path.read_text(encoding="utf-8") == render_registry_definition_json(report.definition)
    assert report.markdown_path.read_text(encoding="utf-8") == render_registry_companion_markdown(
        report.definition
    )


def test_workspace_registry_layout_matches_scaffold_manifest(tmp_path: Path) -> None:
    ensure_workspace_registry_layout(tmp_path)

    manifest_registry_directories = {
        entry["path"]
        for entry in iter_packaged_baseline_directories()
        if entry.get("family") == "registry"
    }
    actual_registry_directories = {
        path.relative_to(tmp_path).as_posix()
        for path in (tmp_path / "agents/registry").rglob("*")
        if path.is_dir()
    }
    actual_registry_directories.add("agents/registry")

    assert actual_registry_directories == manifest_registry_directories


def test_registry_tier_lifecycle_enforces_autosaved_immutability_and_legacy_demotion(tmp_path: Path) -> None:
    autosaved = _task_profile_definition(
        object_id="task_authoring.snapshot",
        tier="autosaved",
        title="Autosaved Snapshot",
        source_kind="advisor_saved",
    )
    persist_workspace_registry_object(tmp_path, autosaved, timestamp=T0)

    mutated = TaskAuthoringProfileDefinition.model_validate(
        {
            **autosaved.model_dump(mode="json"),
            "title": "Mutated Snapshot",
        }
    )
    with pytest.raises(RegistryError, match="immutable until promotion"):
        persist_workspace_registry_object(tmp_path, mutated, overwrite=True, timestamp=T1)

    bypassed_promotion = TaskAuthoringProfileDefinition.model_validate(
        {
            **autosaved.model_dump(mode="json"),
            "tier": "ad_hoc",
            "source": {"kind": "workspace_defined"},
            "title": "Bypassed Promotion",
        }
    )
    with pytest.raises(RegistryError, match="immutable until promotion"):
        persist_workspace_registry_object(tmp_path, bypassed_promotion, overwrite=True, timestamp=T1)

    ref = RegistryObjectRef(
        kind=PersistedObjectKind.TASK_AUTHORING_PROFILE,
        id="task_authoring.snapshot",
        version="1.0.0",
    )
    with pytest.raises(RegistryError, match="must be promoted before demotion"):
        demote_workspace_registry_object_to_legacy(tmp_path, ref, timestamp=T1)

    promoted = promote_workspace_registry_object(
        tmp_path,
        ref,
        target_tier=RegistryTier.AD_HOC,
        source_kind=RegistrySourceKind.WORKSPACE_DEFINED,
        timestamp=T1,
    )
    assert promoted.definition.tier is RegistryTier.AD_HOC
    assert promoted.definition.status is PersistedObjectStatus.ACTIVE
    assert promoted.definition.source.kind is RegistrySourceKind.WORKSPACE_DEFINED
    assert promoted.markdown_path.read_text(encoding="utf-8") == render_registry_companion_markdown(
        promoted.definition
    )

    demoted = demote_workspace_registry_object_to_legacy(tmp_path, ref, timestamp=T2)
    assert demoted.definition.tier is RegistryTier.LEGACY
    assert demoted.definition.status is PersistedObjectStatus.LEGACY
    assert demoted.markdown_path.read_text(encoding="utf-8") == render_registry_companion_markdown(
        demoted.definition
    )


def test_discover_registry_state_rejects_workspace_markdown_drift(tmp_path: Path) -> None:
    definition = _task_profile_definition(object_id="task_authoring.drift")
    report = persist_workspace_registry_object(tmp_path, definition, timestamp=T0)

    report.markdown_path.write_text("# Drifted Companion\n", encoding="utf-8")

    with pytest.raises(RegistryError, match="companion markdown is out of sync"):
        discover_registry_state(tmp_path, validate_catalog=False)


def test_discover_registry_state_rejects_workspace_source_ref_drift(tmp_path: Path) -> None:
    definition = _task_profile_definition(object_id="task_authoring.bad_ref")
    drifted = TaskAuthoringProfileDefinition.model_validate(
        {
            **definition.model_dump(mode="json"),
            "source": {"kind": "workspace_defined", "ref": "agents/registry/task_authoring/not-canonical.json"},
        }
    )
    _write_workspace_registry_document(tmp_path, drifted)

    with pytest.raises(RegistryError, match="declares source ref"):
        discover_registry_state(tmp_path, validate_catalog=False)


def test_persist_workspace_registry_object_rejects_ephemeral_source_kind(tmp_path: Path) -> None:
    definition = _task_profile_definition(
        object_id="task_authoring.ephemeral",
        source_kind="ephemeral",
    )

    with pytest.raises(RegistryError, match="persisted workspace source kinds"):
        persist_workspace_registry_object(tmp_path, definition, timestamp=T0)


def test_packaged_large_stage_registrations_align_with_chain_contract_and_assets(tmp_path: Path) -> None:
    state = discover_registry_state(tmp_path)
    large_stages = {
        document.definition.id: document.definition
        for document in state.packaged
        if isinstance(document.definition, RegisteredStageKindDefinition)
        and document.definition.id
        in {
            "execution.large-plan",
            "execution.large-execute",
            "execution.reassess",
            "execution.refactor",
        }
    }
    resolver = AssetResolver(MILLRACE_ROOT)
    expected = {
        "execution.large-plan": {
            "prompt_asset_ref": "agents/_start_large_plan.md",
            "success_status": "LARGE_PLAN_COMPLETE",
            "legal_predecessors": (),
            "legal_successors": ("execution.large-execute",),
        },
        "execution.large-execute": {
            "prompt_asset_ref": "agents/_start_large_execute.md",
            "success_status": "LARGE_EXECUTE_COMPLETE",
            "legal_predecessors": ("execution.large-plan",),
            "legal_successors": ("execution.reassess",),
        },
        "execution.reassess": {
            "prompt_asset_ref": "agents/prompts/reassess.md",
            "success_status": "LARGE_REASSESS_COMPLETE",
            "legal_predecessors": ("execution.large-execute",),
            "legal_successors": ("execution.refactor",),
        },
        "execution.refactor": {
            "prompt_asset_ref": "agents/_refactor.md",
            "success_status": "LARGE_REFACTOR_COMPLETE",
            "legal_predecessors": ("execution.reassess",),
            "legal_successors": ("execution.qa", "execution.update"),
        },
    }

    assert large_stages.keys() == expected.keys()

    for kind_id, contract in expected.items():
        definition = large_stages[kind_id]
        payload = definition.payload
        module_name, _, attr_name = payload.handler_ref.partition(":")
        handler = getattr(importlib.import_module(module_name), attr_name)
        resolved_asset = resolver.resolve_ref(contract["prompt_asset_ref"])

        assert payload.running_status == "BUILDER_RUNNING"
        assert payload.routing_outcomes == ("success", "blocked")
        assert payload.success_statuses == (contract["success_status"],)
        assert payload.terminal_statuses == (contract["success_status"], "BLOCKED")
        assert payload.legal_predecessors == contract["legal_predecessors"]
        assert payload.legal_successors == contract["legal_successors"]
        assert handler.kind_id == kind_id
        assert handler.prompt_asset_ref == contract["prompt_asset_ref"]
        assert handler.success_status == contract["success_status"]
        assert handler.terminal_statuses == payload.terminal_statuses
        assert resolved_asset.resolved_ref == f"package:{contract['prompt_asset_ref']}"
        assert resolved_asset.source_kind is AssetSourceKind.PACKAGE


def test_packaged_large_modes_publish_explainable_profile_metadata(tmp_path: Path) -> None:
    state = discover_registry_state(tmp_path)
    large_loops = {
        document.definition.id: document.definition
        for document in state.packaged
        if document.key[0] == "loop_config"
        and document.definition.id in {"execution.large", "execution.large_direct_update"}
    }
    large_modes = {
        document.definition.id: document.definition
        for document in state.packaged
        if document.key[0] == "mode"
        and document.definition.id in {"mode.large", "mode.large_direct_update"}
    }

    assert large_loops["execution.large"].title == "Large Thorough Execution Loop"
    assert large_loops["execution.large"].aliases == ("large", "large-thorough", "thorough-execution")
    assert large_loops["execution.large"].labels == ("execution", "large", "qa-verified", "thorough")
    assert {
        node.node_id for node in large_loops["execution.large"].payload.nodes
    } == {"large_plan", "large_execute", "reassess", "refactor", "qa", "hotfix", "doublecheck", "update"}
    assert large_loops["execution.large_direct_update"].title == "Large Direct Update Loop"
    assert large_loops["execution.large_direct_update"].aliases == ("large-direct-update", "large-no-qa")
    assert large_loops["execution.large_direct_update"].labels == (
        "direct-update",
        "execution",
        "large",
        "skip-post-refactor-qa",
    )
    assert {
        node.node_id for node in large_loops["execution.large_direct_update"].payload.nodes
    } == {"large_plan", "large_execute", "reassess", "refactor", "update"}
    thorough_edges = {
        edge.edge_id: edge for edge in large_loops["execution.large"].payload.edges
    }
    direct_update_edges = {
        edge.edge_id: edge for edge in large_loops["execution.large_direct_update"].payload.edges
    }
    assert thorough_edges["execution.large.refactor.success.qa"].to_node_id == "qa"
    assert thorough_edges["execution.large.refactor.blocked.qa"].to_node_id == "qa"
    assert thorough_edges["execution.large.refactor.blocked.qa"].on_outcomes == ("blocked",)
    assert "non-blocking refactor recovery" in (large_loops["execution.large"].summary or "")
    assert direct_update_edges["execution.large.refactor.success.update"].to_node_id == "update"
    assert direct_update_edges["execution.large.refactor.blocked.update"].to_node_id == "update"
    assert direct_update_edges["execution.large.refactor.blocked.update"].on_outcomes == ("blocked",)
    assert "non-blocking refactor recovery" in (
        large_loops["execution.large_direct_update"].summary or ""
    )

    for mode_id, expected_loop_id in {
        "mode.large": "execution.large",
        "mode.large_direct_update": "execution.large_direct_update",
    }.items():
        mode = large_modes[mode_id]
        assert mode.payload.execution_loop_ref == RegistryObjectRef(
            kind=PersistedObjectKind.LOOP_CONFIG,
            id=expected_loop_id,
            version="1.0.0",
        )
        assert mode.payload.task_authoring_profile_ref == RegistryObjectRef(
            kind=PersistedObjectKind.TASK_AUTHORING_PROFILE,
            id="task_authoring.narrow",
            version="1.0.0",
        )
        assert mode.payload.model_profile_ref == RegistryObjectRef(
            kind=PersistedObjectKind.MODEL_PROFILE,
            id="model.default",
            version="1.0.0",
        )
        assert mode.summary is not None
        assert "registry data alone" in mode.summary or "post-refactor QA" in mode.summary


def test_packaged_large_loop_and_mode_jsons_match_canonical_rendering(tmp_path: Path) -> None:
    state = discover_registry_state(tmp_path, validate_catalog=False)
    target_ids = {
        "execution.large",
        "execution.large_direct_update",
        "mode.large",
        "mode.large_direct_update",
    }

    matched_ids: set[str] = set()
    for document in state.packaged:
        if document.definition.id not in target_ids:
            continue
        matched_ids.add(document.definition.id)
        json_path = ASSETS_REGISTRY_ROOT / document.json_relative_path
        assert json_path.read_text(encoding="utf-8") == render_registry_definition_json(
            document.definition
        )

    assert matched_ids == target_ids


def test_discover_registry_state_rejects_illegal_large_stage_success_chain(tmp_path: Path) -> None:
    illegal_loop = LoopConfigDefinition.model_validate(
        {
            "id": "execution.large_illegal_order",
            "version": "1.0.0",
            "tier": "ad_hoc",
            "title": "Illegal LARGE order",
            "summary": "Workspace loop that wires the LARGE success chain out of order.",
            "source": {"kind": "workspace_defined"},
            "payload": {
                "plane": "execution",
                "nodes": (
                    {"node_id": "plan", "kind_id": "execution.large-plan", "overrides": {}},
                    {"node_id": "reassess", "kind_id": "execution.reassess", "overrides": {}},
                    {"node_id": "refactor", "kind_id": "execution.refactor", "overrides": {}},
                    {"node_id": "update", "kind_id": "execution.update", "overrides": {}},
                ),
                "edges": (
                    {
                        "edge_id": "plan_to_reassess",
                        "from_node_id": "plan",
                        "to_node_id": "reassess",
                        "on_outcomes": ("success",),
                        "kind": "normal",
                    },
                    {
                        "edge_id": "reassess_to_refactor",
                        "from_node_id": "reassess",
                        "to_node_id": "refactor",
                        "on_outcomes": ("success",),
                        "kind": "normal",
                    },
                    {
                        "edge_id": "refactor_to_update",
                        "from_node_id": "refactor",
                        "to_node_id": "update",
                        "on_outcomes": ("success",),
                        "kind": "normal",
                    },
                    {
                        "edge_id": "update_done",
                        "from_node_id": "update",
                        "terminal_state_id": "done",
                        "on_outcomes": ("success",),
                        "kind": "terminal",
                    },
                ),
                "entry_node_id": "plan",
                "terminal_states": (
                    {
                        "terminal_state_id": "done",
                        "terminal_class": "success",
                        "writes_status": "UPDATE_COMPLETE",
                        "emits_artifacts": ("stage_summary",),
                        "ends_plane_run": True,
                    },
                ),
            },
        }
    )
    persist_workspace_registry_object(tmp_path, illegal_loop, timestamp=T0)

    with pytest.raises(
        ValidationError,
        match=(
            "loop execution.large_illegal_order edge plan_to_reassess routes execution.large-plan "
            "to execution.reassess on a success trigger but legal_successors allows: execution.large-execute"
        ),
    ):
        discover_registry_state(tmp_path)


def test_discover_registry_state_rejects_illegal_large_stage_blocked_chain(tmp_path: Path) -> None:
    illegal_loop = LoopConfigDefinition.model_validate(
        {
            "id": "execution.large_illegal_blocked_order",
            "version": "1.0.0",
            "tier": "ad_hoc",
            "title": "Illegal LARGE blocked order",
            "summary": "Workspace loop that wires the LARGE blocked chain out of order.",
            "source": {"kind": "workspace_defined"},
            "payload": {
                "plane": "execution",
                "nodes": (
                    {"node_id": "plan", "kind_id": "execution.large-plan", "overrides": {}},
                    {"node_id": "execute", "kind_id": "execution.large-execute", "overrides": {}},
                    {"node_id": "reassess", "kind_id": "execution.reassess", "overrides": {}},
                    {"node_id": "refactor", "kind_id": "execution.refactor", "overrides": {}},
                    {"node_id": "update", "kind_id": "execution.update", "overrides": {}},
                ),
                "edges": (
                    {
                        "edge_id": "plan_to_execute",
                        "from_node_id": "plan",
                        "to_node_id": "execute",
                        "on_outcomes": ("success",),
                        "kind": "normal",
                    },
                    {
                        "edge_id": "plan_to_update_on_blocked",
                        "from_node_id": "plan",
                        "to_node_id": "update",
                        "on_outcomes": ("blocked",),
                        "kind": "normal",
                    },
                    {
                        "edge_id": "execute_to_reassess",
                        "from_node_id": "execute",
                        "to_node_id": "reassess",
                        "on_outcomes": ("success",),
                        "kind": "normal",
                    },
                    {
                        "edge_id": "execute_blocked",
                        "from_node_id": "execute",
                        "terminal_state_id": "blocked",
                        "on_outcomes": ("blocked",),
                        "kind": "terminal",
                    },
                    {
                        "edge_id": "reassess_to_refactor",
                        "from_node_id": "reassess",
                        "to_node_id": "refactor",
                        "on_outcomes": ("success",),
                        "kind": "normal",
                    },
                    {
                        "edge_id": "reassess_blocked",
                        "from_node_id": "reassess",
                        "terminal_state_id": "blocked",
                        "on_outcomes": ("blocked",),
                        "kind": "terminal",
                    },
                    {
                        "edge_id": "refactor_to_update",
                        "from_node_id": "refactor",
                        "to_node_id": "update",
                        "on_outcomes": ("success",),
                        "kind": "normal",
                    },
                    {
                        "edge_id": "refactor_blocked",
                        "from_node_id": "refactor",
                        "terminal_state_id": "blocked",
                        "on_outcomes": ("blocked",),
                        "kind": "terminal",
                    },
                    {
                        "edge_id": "update_done",
                        "from_node_id": "update",
                        "terminal_state_id": "done",
                        "on_outcomes": ("success",),
                        "kind": "terminal",
                    },
                ),
                "entry_node_id": "plan",
                "terminal_states": (
                    {
                        "terminal_state_id": "done",
                        "terminal_class": "success",
                        "writes_status": "UPDATE_COMPLETE",
                        "emits_artifacts": ("stage_summary",),
                        "ends_plane_run": True,
                    },
                    {
                        "terminal_state_id": "blocked",
                        "terminal_class": "blocked",
                        "writes_status": "BLOCKED",
                        "emits_artifacts": ("stage_summary",),
                        "ends_plane_run": True,
                    },
                ),
            },
        }
    )
    persist_workspace_registry_object(tmp_path, illegal_loop, timestamp=T0)

    with pytest.raises(
        ValidationError,
        match=(
            "loop execution.large_illegal_blocked_order edge plan_to_update_on_blocked routes "
            "execution.large-plan to execution.update on a blocked trigger but legal_successors "
            "allows: execution.large-execute"
        ),
    ):
        discover_registry_state(tmp_path)
