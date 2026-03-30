from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from millrace_engine.contracts import (
    ControlPlane,
    LoopArchitectureCatalog,
    LoopConfigDefinition,
    ModeDefinition,
    ModelProfileDefinition,
    RegisteredStageKindDefinition,
    RegistryTier,
    RunnerKind,
    RunnerResult,
    StageOverrideField,
    StageType,
    StructuredStageResult,
    TaskAuthoringProfileDefinition,
)


def _stage_kind_payload(
    *,
    kind_id: str = "execution.builder",
    plane: str = "execution",
    running_status: str = "BUILDER_RUNNING",
    terminal_status: str = "BUILDER_COMPLETE",
    allowed_overrides: tuple[str, ...] = ("model_profile_ref", "allow_search"),
) -> dict[str, object]:
    return {
        "id": kind_id,
        "version": "1.0.0",
        "tier": "default",
        "title": "Execution Builder" if plane == "execution" else "Spec Review",
        "aliases": ("builder",) if plane == "execution" else ("spec-review",),
        "summary": "Packaged stage registration surface.",
        "source": {"kind": "packaged_default"},
        "labels": (plane, "baseline"),
        "payload": {
            "kind_id": kind_id,
            "contract_version": "1.0.0",
            "plane": plane,
            "handler_ref": f"millrace_engine.stages.{kind_id.split('.')[-1]}:Stage",
            "context_schema_ref": f"{kind_id}.context.v1",
            "result_schema_ref": f"{kind_id}.result.v1",
            "running_status": running_status,
            "terminal_statuses": (terminal_status, "BLOCKED"),
            "success_statuses": (terminal_status,),
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
                    "required_on": ("success", terminal_status),
                    "persistence": "history",
                },
                {
                    "name": "run_bundle",
                    "kind": "run_bundle",
                    "required_on": ("success",),
                    "persistence": "runtime_bundle",
                },
            ),
            "idempotence_policy": "retry_safe_with_key",
            "retry_policy": {
                "max_attempts": 2,
                "backoff_seconds": 0,
                "exhausted_outcome": "terminal_failure",
            },
            "queue_mutation_policy": "runtime_only",
            "routing_outcomes": ("success", "terminal_failure", "blocked"),
            "legal_predecessors": (),
            "legal_successors": (),
            "allowed_overrides": allowed_overrides,
        },
    }


def _task_profile_payload() -> dict[str, object]:
    return {
        "id": "task_authoring.narrow",
        "version": "1.0.0",
        "tier": "golden",
        "title": "Narrow authoring",
        "aliases": ("narrow",),
        "summary": "High-rigor task decomposition.",
        "source": {"kind": "workspace_defined", "ref": "registry/task_authoring/task_authoring.narrow.json"},
        "labels": ("task-authoring", "workspace"),
        "payload": {
            "decomposition_style": "narrow",
            "expected_card_count": {"min_cards": 2, "max_cards": 6},
            "allowed_task_breadth": "focused",
            "required_metadata_fields": ("spec_id", "acceptance_ids"),
            "acceptance_profile": "strict",
            "gate_strictness": "strict",
            "single_card_synthesis_allowed": False,
            "research_assumption": "consult_if_ambiguous",
            "suitable_use_cases": ("High-risk code changes", "Cross-file refactors"),
        },
    }


def _model_profile_payload() -> dict[str, object]:
    return {
        "id": "model.default",
        "version": "1.0.0",
        "tier": "golden",
        "title": "Default model profile",
        "aliases": ("default",),
        "summary": "Workspace default runner/model selection.",
        "source": {"kind": "workspace_defined", "ref": "registry/model_profiles/model.default.json"},
        "labels": ("model", "workspace"),
        "payload": {
            "default_binding": {
                "runner": "codex",
                "model": "gpt-5.3-codex",
                "effort": "medium",
                "allow_search": False,
            },
            "stage_overrides": (
                {
                    "kind_id": "execution.builder",
                    "binding": {
                        "runner": "codex",
                        "model": "gpt-5.3-codex",
                        "effort": "high",
                        "allow_search": True,
                    },
                },
            ),
        },
    }


def _loop_payload(
    *,
    stage_kind_id: str = "execution.builder",
    plane: str = "execution",
    overrides: dict[str, object] | None = None,
    include_profile_refs: bool = True,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "execution.quick_build",
        "version": "1.0.0",
        "tier": "golden",
        "title": "Quick build",
        "aliases": ("quick-build",),
        "summary": "Workspace execution loop.",
        "source": {"kind": "workspace_defined", "ref": "registry/loops/execution.quick_build.json"},
        "labels": ("execution", "workspace"),
        "payload": {
            "plane": plane,
            "nodes": (
                {
                    "node_id": "builder",
                    "kind_id": stage_kind_id,
                    "overrides": overrides or {
                        "model_profile_ref": {
                            "kind": "model_profile",
                            "id": "model.default",
                            "version": "1.0.0",
                        }
                    },
                },
            ),
            "edges": (
                {
                    "edge_id": "builder_to_completed",
                    "from_node_id": "builder",
                    "terminal_state_id": "completed",
                    "on_outcomes": ("success",),
                    "kind": "terminal",
                },
            ),
            "entry_node_id": "builder",
            "terminal_states": (
                {
                    "terminal_state_id": "completed",
                    "terminal_class": "success",
                    "writes_status": "BUILDER_COMPLETE",
                    "emits_artifacts": ("stage_summary",),
                    "ends_plane_run": True,
                },
            ),
            "outline_policy": {"mode": "hybrid"},
        },
    }
    if include_profile_refs:
        payload["payload"]["task_authoring_profile_ref"] = {
            "kind": "task_authoring_profile",
            "id": "task_authoring.narrow",
            "version": "1.0.0",
        }
        payload["payload"]["task_authoring_required"] = True
        payload["payload"]["model_profile_ref"] = {
            "kind": "model_profile",
            "id": "model.default",
            "version": "1.0.0",
        }
    return payload


def _mode_payload() -> dict[str, object]:
    return {
        "id": "mode.default_autonomous",
        "version": "1.0.0",
        "tier": "golden",
        "title": "Default autonomous mode",
        "aliases": ("default-autonomous",),
        "summary": "Composed execution defaults.",
        "source": {"kind": "workspace_defined", "ref": "registry/modes/mode.default_autonomous.json"},
        "labels": ("mode", "workspace"),
        "payload": {
            "execution_loop_ref": {"kind": "loop_config", "id": "execution.quick_build", "version": "1.0.0"},
            "task_authoring_profile_ref": {
                "kind": "task_authoring_profile",
                "id": "task_authoring.narrow",
                "version": "1.0.0",
            },
            "model_profile_ref": {"kind": "model_profile", "id": "model.default", "version": "1.0.0"},
            "research_participation": "none",
            "outline_policy": {"mode": "hybrid"},
        },
    }


def test_loop_architecture_catalog_validates_packaged_and_workspace_objects() -> None:
    stage_kind = RegisteredStageKindDefinition.model_validate(_stage_kind_payload())
    task_profile = TaskAuthoringProfileDefinition.model_validate(_task_profile_payload())
    model_profile = ModelProfileDefinition.model_validate(_model_profile_payload())
    loop_config = LoopConfigDefinition.model_validate(_loop_payload())
    mode = ModeDefinition.model_validate(_mode_payload())

    catalog = LoopArchitectureCatalog.model_validate(
        {"objects": (stage_kind, task_profile, model_profile, loop_config, mode)}
    )

    assert len(catalog.objects) == 5
    assert loop_config.payload.nodes[0].overrides.override_fields() == {
        StageOverrideField.MODEL_PROFILE_REF
    }
    assert mode.payload.execution_loop_ref.id == "execution.quick_build"
    assert task_profile.tier is RegistryTier.GOLDEN


def test_loop_architecture_catalog_rejects_mixed_plane_stage_bindings() -> None:
    research_stage = RegisteredStageKindDefinition.model_validate(
        _stage_kind_payload(
            kind_id="research.spec_review",
            plane="research",
            running_status="SPEC_REVIEW_RUNNING",
            terminal_status="SPEC_REVIEW_COMPLETE",
        )
    )
    loop_config = LoopConfigDefinition.model_validate(
        _loop_payload(stage_kind_id="research.spec_review", include_profile_refs=False)
    )

    with pytest.raises(
        ValidationError,
        match="loop execution.quick_build node builder references research stage kind research.spec_review from a execution loop",
    ):
        LoopArchitectureCatalog.model_validate({"objects": (research_stage, loop_config)})


def test_loop_architecture_catalog_rejects_unsupported_node_override_fields() -> None:
    stage_kind = RegisteredStageKindDefinition.model_validate(
        _stage_kind_payload(allowed_overrides=("model_profile_ref",))
    )
    task_profile = TaskAuthoringProfileDefinition.model_validate(_task_profile_payload())
    model_profile = ModelProfileDefinition.model_validate(_model_profile_payload())
    invalid_loop = LoopConfigDefinition.model_validate(
        _loop_payload(overrides={"model": "gpt-5.3-codex"})
    )

    with pytest.raises(
        ValidationError,
        match="loop node builder overrides unsupported fields for execution.builder: model",
    ):
        LoopArchitectureCatalog.model_validate(
            {"objects": (stage_kind, task_profile, model_profile, invalid_loop)}
        )


def test_loop_architecture_surface_rejects_node_override_refs_to_non_model_profiles() -> None:
    invalid_loop = _loop_payload()
    invalid_loop["payload"]["nodes"][0]["overrides"] = {
        "model_profile_ref": {
            "kind": "mode",
            "id": "mode.default_autonomous",
            "version": "1.0.0",
        }
    }

    with pytest.raises(ValidationError, match="node model_profile_ref must reference a model_profile object"):
        LoopConfigDefinition.model_validate(invalid_loop)


def test_loop_architecture_surface_rejects_invalid_ids_aliases_and_tiers() -> None:
    invalid_id = _task_profile_payload()
    invalid_id["id"] = "Task.Authoring.Narrow"
    with pytest.raises(ValidationError, match="id must match"):
        TaskAuthoringProfileDefinition.model_validate(invalid_id)

    invalid_alias = _task_profile_payload()
    invalid_alias["aliases"] = ("Bad Alias!",)
    with pytest.raises(ValidationError, match="aliases must match"):
        TaskAuthoringProfileDefinition.model_validate(invalid_alias)

    invalid_tier = _task_profile_payload()
    invalid_tier["tier"] = "unsupported"
    with pytest.raises(ValidationError) as exc_info:
        TaskAuthoringProfileDefinition.model_validate(invalid_tier)
    error_text = str(exc_info.value)
    assert "tier" in error_text
    assert "unsupported" in error_text


def test_loop_architecture_catalog_rejects_invalid_binding_sources_and_edge_triggers() -> None:
    stage_kind = RegisteredStageKindDefinition.model_validate(_stage_kind_payload())

    invalid_binding_loop = LoopConfigDefinition.model_validate(
        _loop_payload(
            include_profile_refs=False,
            overrides={"model_profile_ref": None},
        )
    )
    invalid_binding_payload = invalid_binding_loop.model_dump(mode="python")
    invalid_binding_payload["payload"]["nodes"] = (
        invalid_binding_payload["payload"]["nodes"][0],
        {
            "node_id": "qa",
            "kind_id": "execution.builder",
            "artifact_bindings": (
                {
                    "input_artifact": "task_card",
                    "source_node_id": "builder",
                    "source_artifact": "missing_artifact",
                },
            ),
        },
    )
    invalid_binding_payload["payload"]["edges"] = (
        {
            "edge_id": "builder_to_qa",
            "from_node_id": "builder",
            "to_node_id": "qa",
            "on_outcomes": ("success",),
            "kind": "normal",
        },
        {
            "edge_id": "qa_to_completed",
            "from_node_id": "qa",
            "terminal_state_id": "completed",
            "on_outcomes": ("success",),
            "kind": "terminal",
        },
    )
    invalid_binding_loop = LoopConfigDefinition.model_validate(invalid_binding_payload)

    with pytest.raises(
        ValidationError,
        match="loop execution.quick_build node qa binds unknown source artifact missing_artifact from node builder",
    ):
        LoopArchitectureCatalog.model_validate({"objects": (stage_kind, invalid_binding_loop)})

    invalid_edge_payload = _loop_payload(include_profile_refs=False)
    invalid_edge_payload["payload"]["edges"] = (
        {
            "edge_id": "builder_to_completed",
            "from_node_id": "builder",
            "terminal_state_id": "completed",
            "on_outcomes": ("not_a_real_outcome",),
            "kind": "terminal",
        },
    )
    invalid_edge_loop = LoopConfigDefinition.model_validate(invalid_edge_payload)

    with pytest.raises(
        ValidationError,
        match="loop execution.quick_build edge builder_to_completed uses triggers not declared by execution.builder: not_a_real_outcome",
    ):
        LoopArchitectureCatalog.model_validate({"objects": (stage_kind, invalid_edge_loop)})


def test_registered_stage_result_exposes_structured_router_fields() -> None:
    stage_kind = RegisteredStageKindDefinition.model_validate(_stage_kind_payload())
    started_at = datetime(2026, 3, 18, 18, 0, tzinfo=timezone.utc)
    completed_at = datetime(2026, 3, 18, 18, 0, 3, tzinfo=timezone.utc)
    runner_result = RunnerResult.model_validate(
        {
            "stage": StageType.BUILDER,
            "runner": RunnerKind.CODEX,
            "model": "gpt-5.3-codex",
            "command": ("codex", "run"),
            "exit_code": 0,
            "duration_seconds": 3.0,
            "stdout": "builder ok\n",
            "stderr": "",
            "started_at": started_at,
            "completed_at": completed_at,
        }
    )

    result = StructuredStageResult.model_validate(
        {
            "stage_node_id": "builder",
            "kind_id": "execution.builder",
            "plane": "execution",
            "outcome": "success",
            "status": "BUILDER_COMPLETE",
            "artifacts": {
                "stage_summary": {
                    "kind": "stage_summary",
                    "summary": "Builder completed cleanly.",
                },
                "run_bundle": {
                    "kind": "run_bundle",
                    "ref": "agents/runs/run-001/builder",
                },
            },
            "runner_result": runner_result,
        }
    )

    validated = stage_kind.payload.validate_stage_result(result)

    assert validated.outcome == "success"
    assert validated.status == "BUILDER_COMPLETE"
    assert validated.artifacts["stage_summary"].summary == "Builder completed cleanly."
    assert validated.metadata.runner is RunnerKind.CODEX
    assert validated.metadata.model == "gpt-5.3-codex"
    assert validated.runner_result is runner_result
    assert validated.metadata.effort is None
    assert stage_kind.payload.retry_policy.max_attempts == 2


def test_registered_stage_result_rejects_undeclared_or_mismatched_artifacts() -> None:
    stage_kind = RegisteredStageKindDefinition.model_validate(_stage_kind_payload())

    extra_artifact_result = StructuredStageResult.model_validate(
        {
            "stage_node_id": "builder",
            "kind_id": "execution.builder",
            "plane": "execution",
            "outcome": "success",
            "status": "BUILDER_COMPLETE",
            "artifacts": {
                "stage_summary": {
                    "kind": "stage_summary",
                    "summary": "Builder completed cleanly.",
                },
                "run_bundle": {
                    "kind": "run_bundle",
                    "ref": "agents/runs/run-001/builder",
                },
                "ghost": {
                    "kind": "ghost",
                    "summary": "Unexpected artifact.",
                },
            },
        }
    )
    with pytest.raises(
        ValueError,
        match="stage result for execution.builder contains undeclared artifacts: ghost",
    ):
        stage_kind.payload.validate_stage_result(extra_artifact_result)

    wrong_kind_result = StructuredStageResult.model_validate(
        {
            "stage_node_id": "builder",
            "kind_id": "execution.builder",
            "plane": "execution",
            "outcome": "success",
            "status": "BUILDER_COMPLETE",
            "artifacts": {
                "stage_summary": {
                    "kind": "wrong_kind",
                    "summary": "Builder completed cleanly.",
                },
                "run_bundle": {
                    "kind": "run_bundle",
                    "ref": "agents/runs/run-001/builder",
                },
            },
        }
    )
    with pytest.raises(
        ValueError,
        match="stage result artifact stage_summary for execution.builder must declare kind stage_summary, got wrong_kind",
    ):
        stage_kind.payload.validate_stage_result(wrong_kind_result)
