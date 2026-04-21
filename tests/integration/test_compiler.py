from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path

from millrace_ai.compiler import CompilerValidationError, compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import CompileDiagnostics, FrozenRunPlan
from millrace_ai.errors import ConfigurationError, MillraceError
from millrace_ai.paths import bootstrap_workspace, workspace_paths


def test_compiler_consumes_config_and_assets_package_surfaces() -> None:
    assets_package = importlib.import_module("millrace_ai.assets")
    compiler_module = importlib.import_module("millrace_ai.compiler")
    config_module = importlib.import_module("millrace_ai.config")

    assert compiler_module.RuntimeConfig is config_module.RuntimeConfig
    assert compiler_module.load_builtin_mode_bundle is assets_package.load_builtin_mode_bundle


def _copy_builtin_assets(tmp_path: Path) -> Path:
    assets_root = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"
    copied_root = tmp_path / "assets"
    shutil.copytree(assets_root, copied_root)
    return copied_root


def test_compiler_validation_errors_use_project_error_hierarchy() -> None:
    assert issubclass(ConfigurationError, MillraceError)
    assert issubclass(CompilerValidationError, ConfigurationError)


def test_compile_writes_frozen_plan_and_diagnostics_artifacts(tmp_path: Path) -> None:
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
    assert diagnostics_path.is_file()

    persisted_plan = FrozenRunPlan.model_validate_json(compiled_plan_path.read_text(encoding="utf-8"))
    persisted_diagnostics = CompileDiagnostics.model_validate_json(
        diagnostics_path.read_text(encoding="utf-8")
    )

    assert persisted_plan.mode_id == "default_codex"
    assert persisted_plan.execution_loop_id == "execution.standard"
    assert persisted_plan.planning_loop_id == "planning.standard"
    assert len(persisted_plan.stage_plans) == 12
    assert persisted_plan.completion_behavior is not None
    assert persisted_plan.completion_behavior.stage.value == "arbiter"
    assert any(ref.startswith("completion_behavior:") for ref in persisted_plan.source_refs)
    assert persisted_diagnostics.ok is True
    assert persisted_diagnostics.mode_id == "default_codex"


def test_standard_plain_alias_and_default_codex_compile_to_identical_plan_ids(
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
        requested_mode_id="default_codex",
    )

    assert alias_outcome.diagnostics.ok is True
    assert canonical_outcome.diagnostics.ok is True
    assert alias_outcome.active_plan is not None
    assert canonical_outcome.active_plan is not None
    assert alias_outcome.active_plan.mode_id == "default_codex"
    assert canonical_outcome.active_plan.mode_id == "default_codex"
    assert alias_outcome.active_plan.compiled_plan_id == canonical_outcome.active_plan.compiled_plan_id


def test_default_pi_compiles_with_pi_runner_bound_for_every_stage(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="default_pi",
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    assert outcome.active_plan.mode_id == "default_pi"
    assert {stage_plan.runner_name for stage_plan in outcome.active_plan.stage_plans} == {"pi_rpc"}


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
        stage_plan.stage.value: stage_plan.required_skills
        for stage_plan in outcome.active_plan.stage_plans
    }

    assert len(required_by_stage) == 12
    assert required_by_stage["builder"] == ("skills/stage/execution/builder-core/SKILL.md",)
    assert required_by_stage["checker"] == ("skills/stage/execution/checker-core/SKILL.md",)
    assert required_by_stage["planner"] == ("skills/stage/planning/planner-core/SKILL.md",)
    assert required_by_stage["auditor"] == ("skills/stage/planning/auditor-core/SKILL.md",)
    assert required_by_stage["arbiter"] == ("skills/stage/planning/arbiter-core/SKILL.md",)


def test_compile_plan_identity_changes_when_completion_behavior_changes(tmp_path: Path) -> None:
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
    planning_loop_path = assets_root / "loops" / "planning" / "default.json"
    payload = json.loads(planning_loop_path.read_text(encoding="utf-8"))
    payload["completion_behavior"]["skip_if_already_closed"] = False
    planning_loop_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

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
    assert {stage_plan.timeout_seconds for stage_plan in outcome.active_plan.stage_plans} == {3600}


def test_compile_surfaces_stage_skill_attachments_without_role_overlays(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    assets_root = _copy_builtin_assets(tmp_path)
    mode_path = assets_root / "modes" / "default_codex.json"
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

    builder_plan = next(
        stage_plan for stage_plan in outcome.active_plan.stage_plans if stage_plan.stage.value == "builder"
    )

    assert builder_plan.required_skills == ("skills/stage/execution/builder-core/SKILL.md",)
    assert builder_plan.attached_skill_additions == ("skills/execution/builder.md",)
    assert "role_overlays" not in builder_plan.model_dump(mode="json")


def test_compile_rejects_invalid_entrypoint_override_deterministically(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    assets_root = _copy_builtin_assets(tmp_path)
    mode_path = assets_root / "modes" / "default_codex.json"
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
    mode_path = assets_root / "modes" / "default_codex.json"
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
    mode_path = assets_root / "modes" / "default_codex.json"
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
        f"{assets_root / 'modes' / 'default_codex.json'}",
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
    mode_path = assets_root / "modes" / "default_codex.json"
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
    assert diagnostics.mode_id == "default_codex"
    assert diagnostics.errors[0] == "Unknown built-in loop id: planning.unknown"
