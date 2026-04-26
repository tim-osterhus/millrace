from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path

import pytest

from millrace_ai.architecture import RecoveryRole, StageIdempotencePolicy
from millrace_ai.contracts import ExecutionStageName, LearningStageName, Plane, PlanningStageName, ResultClass
from millrace_ai.errors import AssetValidationError, MillraceError
from millrace_ai.stage_kinds import (
    SHIPPED_STAGE_KIND_IDS,
    ArchitectureAssetError,
    discover_stage_kind_definitions,
    load_builtin_stage_kind_definition,
    load_builtin_stage_kind_definitions,
    load_stage_kind_definition,
)


def _copy_builtin_assets(tmp_path: Path) -> Path:
    assets_root = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"
    copied_root = tmp_path / "assets"
    shutil.copytree(assets_root, copied_root)
    return copied_root


def _write_synthetic_stage_kind_asset(assets_root: Path) -> None:
    stage_kind_path = (
        assets_root / "registry" / "stage_kinds" / "execution" / "synthetic_worker.json"
    )
    payload = {
        "schema_version": "1.0",
        "kind": "registered_stage_kind",
        "stage_kind_id": "synthetic_worker",
        "plane": "execution",
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


def test_stage_kinds_module_is_assets_facade() -> None:
    stage_kinds_facade = importlib.import_module("millrace_ai.stage_kinds")
    stage_kinds_module = importlib.import_module("millrace_ai.assets.architecture")
    assets_public_module = importlib.import_module("millrace_ai.assets")

    assert (
        stage_kinds_facade.load_builtin_stage_kind_definition
        is stage_kinds_module.load_builtin_stage_kind_definition
    )
    assert (
        stage_kinds_facade.load_builtin_stage_kind_definitions
        is stage_kinds_module.load_builtin_stage_kind_definitions
    )
    assert stage_kinds_module.SHIPPED_STAGE_KIND_IDS == stage_kinds_facade.SHIPPED_STAGE_KIND_IDS
    assert (
        assets_public_module.load_builtin_stage_kind_definition
        is stage_kinds_module.load_builtin_stage_kind_definition
    )
    assert assets_public_module.ArchitectureAssetError is stage_kinds_module.ArchitectureAssetError


def test_builtin_stage_kinds_load_and_validate() -> None:
    stage_kinds = load_builtin_stage_kind_definitions()

    assert len(stage_kinds) == 15
    assert [stage_kind.stage_kind_id for stage_kind in stage_kinds] == list(SHIPPED_STAGE_KIND_IDS)
    assert {stage_kind.plane for stage_kind in stage_kinds} == {
        Plane.EXECUTION,
        Plane.PLANNING,
        Plane.LEARNING,
    }
    assert all(stage_kind.required_skill_paths for stage_kind in stage_kinds)
    assert all(stage_kind.success_outcomes for stage_kind in stage_kinds)
    assert all(
        set(stage_kind.success_outcomes).issubset(stage_kind.legal_outcomes)
        for stage_kind in stage_kinds
    )
    assert all(set(stage_kind.allowed_result_classes_by_outcome) == set(stage_kind.legal_outcomes) for stage_kind in stage_kinds)


def test_shipped_stage_kind_ids_are_stable() -> None:
    assert SHIPPED_STAGE_KIND_IDS == (
        "builder",
        "checker",
        "fixer",
        "doublechecker",
        "updater",
        "troubleshooter",
        "consultant",
        "planner",
        "manager",
        "mechanic",
        "auditor",
        "arbiter",
        "analyst",
        "professor",
        "curator",
    )


def test_builtin_stage_kinds_cover_current_shipped_stage_enums() -> None:
    expected_stage_ids = {
        *(stage.value for stage in ExecutionStageName),
        *(stage.value for stage in PlanningStageName),
        *(stage.value for stage in LearningStageName),
    }

    assert set(SHIPPED_STAGE_KIND_IDS) == expected_stage_ids


def test_specific_builtin_stage_kind_fields_are_expected() -> None:
    builder = load_builtin_stage_kind_definition("builder")
    arbiter = load_builtin_stage_kind_definition("arbiter")
    troubleshooter = load_builtin_stage_kind_definition("troubleshooter")
    analyst = load_builtin_stage_kind_definition("analyst")

    assert builder.plane is Plane.EXECUTION
    assert builder.default_entrypoint_path == "entrypoints/execution/builder.md"
    assert builder.required_skill_paths == ("skills/stage/execution/builder-core/SKILL.md",)
    assert builder.allowed_result_classes_by_outcome == {
        "BUILDER_COMPLETE": (ResultClass.SUCCESS,),
        "BLOCKED": (ResultClass.BLOCKED, ResultClass.RECOVERABLE_FAILURE),
    }
    assert builder.can_start_tasks is True
    assert builder.idempotence_policy is StageIdempotencePolicy.RETRY_SAFE_WITH_KEY

    assert troubleshooter.recovery_role is RecoveryRole.LOCAL_REPAIR
    assert troubleshooter.running_status_marker == "TROUBLESHOOTER_RUNNING"
    assert troubleshooter.allowed_result_classes_by_outcome["BLOCKED"] == (
        ResultClass.BLOCKED,
        ResultClass.RECOVERABLE_FAILURE,
    )

    assert arbiter.plane is Plane.PLANNING
    assert arbiter.closure_role is True
    assert arbiter.failure_outcomes == ("REMEDIATION_NEEDED", "BLOCKED")
    assert arbiter.allowed_result_classes_by_outcome["REMEDIATION_NEEDED"] == (
        ResultClass.FOLLOWUP_NEEDED,
    )
    assert arbiter.idempotence_policy is StageIdempotencePolicy.SINGLE_ATTEMPT_ONLY

    assert analyst.plane is Plane.LEARNING
    assert analyst.can_start_learning_requests is True
    assert analyst.required_skill_paths == ("skills/stage/learning/analyst-core/SKILL.md",)


def test_stage_kind_asset_errors_use_project_error_hierarchy() -> None:
    assert issubclass(AssetValidationError, MillraceError)
    assert issubclass(ArchitectureAssetError, AssetValidationError)


def test_unknown_stage_kind_fails_deterministically() -> None:
    with pytest.raises(ArchitectureAssetError, match=r"^Unknown built-in stage kind id: no_such_stage$"):
        load_builtin_stage_kind_definition("no_such_stage")


def test_invalid_stage_kind_json_fails_deterministically(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    stage_kind_path = assets_root / "registry" / "stage_kinds" / "execution" / "builder.json"
    stage_kind_path.write_text("{not-valid-json", encoding="utf-8")

    with pytest.raises(ArchitectureAssetError, match="Invalid JSON in stage kind asset"):
        load_builtin_stage_kind_definition("builder", assets_root=assets_root)


def test_stage_kind_requires_allowed_result_classes_by_outcome(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    stage_kind_path = assets_root / "registry" / "stage_kinds" / "execution" / "builder.json"
    payload = json.loads(stage_kind_path.read_text(encoding="utf-8"))
    payload.pop("allowed_result_classes_by_outcome", None)
    stage_kind_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ArchitectureAssetError, match="Invalid stage kind definition in asset"):
        load_builtin_stage_kind_definition("builder", assets_root=assets_root)


def test_stage_kind_id_mismatch_fails_deterministically(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    stage_kind_path = assets_root / "registry" / "stage_kinds" / "execution" / "builder.json"
    payload = json.loads(stage_kind_path.read_text(encoding="utf-8"))
    payload["stage_kind_id"] = "wrong_id"
    stage_kind_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ArchitectureAssetError, match="Stage kind asset id mismatch"):
        load_builtin_stage_kind_definition("builder", assets_root=assets_root)


def test_discover_stage_kind_definitions_includes_synthetic_stage_kind(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    _write_synthetic_stage_kind_asset(assets_root)

    discovered = discover_stage_kind_definitions(assets_root=assets_root)
    discovered_ids = [stage_kind.stage_kind_id for stage_kind in discovered]
    synthetic = load_stage_kind_definition("synthetic_worker", assets_root=assets_root)

    assert "synthetic_worker" in discovered_ids
    assert synthetic.stage_kind_id == "synthetic_worker"
    assert synthetic.legal_outcomes == ("SYNTHETIC_COMPLETE", "BLOCKED")
    assert synthetic.allowed_result_classes_by_outcome["BLOCKED"] == (
        ResultClass.BLOCKED,
        ResultClass.RECOVERABLE_FAILURE,
    )
