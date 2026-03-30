from __future__ import annotations

from pathlib import Path

import pytest

from millrace_engine.assets.resolver import AssetResolutionError, AssetResolver, AssetSourceKind
from millrace_engine.baseline_assets import packaged_baseline_asset
from millrace_engine.control import EngineControl


def scaffold_workspace(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    result = EngineControl.init_workspace(workspace)
    assert result.applied is True
    return workspace, workspace / "millrace.toml"


def test_asset_resolver_prefers_workspace_prompt_override(tmp_path: Path) -> None:
    workspace, _ = scaffold_workspace(tmp_path)
    prompt_path = workspace / "agents" / "_start.md"
    prompt_path.write_text("Workspace override prompt\n", encoding="utf-8")

    resolved = AssetResolver(workspace).resolve_file(prompt_path)

    assert resolved.source_kind is AssetSourceKind.WORKSPACE
    assert resolved.resolved_ref == "workspace:agents/_start.md"
    assert resolved.read_text(encoding="utf-8") == "Workspace override prompt\n"


def test_asset_resolver_falls_back_to_packaged_prompt_when_workspace_copy_is_absent(tmp_path: Path) -> None:
    workspace, _ = scaffold_workspace(tmp_path)
    prompt_path = workspace / "agents" / "_start.md"
    prompt_path.unlink()

    resolved = AssetResolver(workspace).resolve_file(prompt_path)

    assert resolved.source_kind is AssetSourceKind.PACKAGE
    assert resolved.resolved_ref == "package:agents/_start.md"
    assert resolved.read_text(encoding="utf-8") == packaged_baseline_asset("agents/_start.md").read_text(
        encoding="utf-8"
    )


def test_asset_resolver_resolves_explicit_workspace_and_package_refs(tmp_path: Path) -> None:
    workspace, _ = scaffold_workspace(tmp_path)
    prompt_path = workspace / "agents" / "_start.md"
    prompt_path.write_text("Workspace explicit ref\n", encoding="utf-8")

    resolver = AssetResolver(workspace)
    workspace_resolved = resolver.resolve_ref("workspace:agents/_start.md")
    package_resolved = resolver.resolve_ref("package:agents/_start.md")

    assert workspace_resolved.source_kind is AssetSourceKind.WORKSPACE
    assert workspace_resolved.resolved_ref == "workspace:agents/_start.md"
    assert workspace_resolved.read_text(encoding="utf-8") == "Workspace explicit ref\n"
    assert package_resolved.source_kind is AssetSourceKind.PACKAGE
    assert package_resolved.resolved_ref == "package:agents/_start.md"
    assert package_resolved.read_text(encoding="utf-8") == packaged_baseline_asset("agents/_start.md").read_text(
        encoding="utf-8"
    )


def test_asset_resolver_explicit_workspace_ref_does_not_fall_back_to_package(tmp_path: Path) -> None:
    workspace, _ = scaffold_workspace(tmp_path)
    prompt_path = workspace / "agents" / "_start.md"
    prompt_path.unlink()

    with pytest.raises(AssetResolutionError, match="workspace-backed asset is missing"):
        AssetResolver(workspace).resolve_ref("workspace:agents/_start.md")


def test_asset_resolver_missing_required_prompt_fails_deterministically(tmp_path: Path) -> None:
    workspace, _ = scaffold_workspace(tmp_path)

    with pytest.raises(AssetResolutionError, match="asset is missing from workspace and package"):
        AssetResolver(workspace).resolve_file("agents/not-a-real-prompt.md")


def test_asset_resolver_open_families_are_additive_and_workspace_override_wins(tmp_path: Path) -> None:
    workspace, _ = scaffold_workspace(tmp_path)
    role_override = workspace / "agents" / "roles" / "research-router.md"
    role_override.parent.mkdir(parents=True, exist_ok=True)
    role_override.write_text("workspace role override\n", encoding="utf-8")
    role_custom = workspace / "agents" / "roles" / "custom-role.md"
    role_custom.write_text("custom role\n", encoding="utf-8")
    (workspace / "agents" / "roles" / "qa-test-engineer.md").unlink()

    skill_override = workspace / "agents" / "skills" / "spec-writing-research-core" / "SKILL.md"
    skill_override.parent.mkdir(parents=True, exist_ok=True)
    skill_override.write_text("workspace skill override\n", encoding="utf-8")
    skill_custom = workspace / "agents" / "skills" / "custom-skill" / "SKILL.md"
    skill_custom.parent.mkdir(parents=True, exist_ok=True)
    skill_custom.write_text("custom skill\n", encoding="utf-8")
    (workspace / "agents" / "skills" / "README.md").unlink()

    resolver = AssetResolver(workspace)
    role_entries = {entry.relative_path.as_posix(): entry for entry in resolver.iter_open_family("roles")}
    skill_entries = {entry.relative_path.as_posix(): entry for entry in resolver.iter_open_family("skills")}

    assert role_entries["agents/roles/research-router.md"].source_kind is AssetSourceKind.WORKSPACE
    assert role_entries["agents/roles/custom-role.md"].source_kind is AssetSourceKind.WORKSPACE
    assert role_entries["agents/roles/qa-test-engineer.md"].source_kind is AssetSourceKind.PACKAGE

    assert (
        skill_entries["agents/skills/spec-writing-research-core/SKILL.md"].source_kind
        is AssetSourceKind.WORKSPACE
    )
    assert skill_entries["agents/skills/custom-skill/SKILL.md"].source_kind is AssetSourceKind.WORKSPACE
    assert skill_entries["agents/skills/README.md"].source_kind is AssetSourceKind.PACKAGE


def test_control_surfaces_expose_asset_inventory_truthfully(tmp_path: Path) -> None:
    workspace, config_path = scaffold_workspace(tmp_path)
    (workspace / "agents" / "_start.md").unlink()
    custom_role = workspace / "agents" / "roles" / "custom-role.md"
    custom_role.parent.mkdir(parents=True, exist_ok=True)
    custom_role.write_text("custom role\n", encoding="utf-8")

    control = EngineControl(config_path)
    status = control.status(detail=True)
    config_payload = control.config_show()

    assert status.runtime.asset_bundle_version == "baseline-bundle-v1"
    assert status.selection.scope == "preview"
    assert status.selection.mode is not None
    assert status.selection.mode.ref.id == "mode.standard"
    assert status.selection.execution_loop is not None
    assert status.selection.execution_loop.ref.id == "execution.standard"
    assert [binding.node_id for binding in status.selection.stage_bindings] == [
        "builder",
        "consult",
        "doublecheck",
        "hotfix",
        "integration",
        "qa",
        "troubleshoot",
        "update",
    ]
    assert status.assets is not None
    assert status.assets.bundle_version == "baseline-bundle-v1"
    assert status.assets.stage_prompts["builder"].source_kind == "package"
    assert status.assets.stage_prompts["builder"].resolved_ref == "package:agents/_start.md"
    assert any(entry.relative_path == "agents/roles/custom-role.md" and entry.source_kind == "workspace" for entry in status.assets.roles)

    assert config_payload.assets.bundle_version == "baseline-bundle-v1"
    assert config_payload.selection.mode is not None
    assert config_payload.selection.mode.ref.id == "mode.standard"
    assert config_payload.selection.execution_loop is not None
    assert config_payload.selection.execution_loop.ref.id == "execution.standard"
    assert config_payload.assets.stage_prompts["builder"].source_kind == "package"
    assert any(
        entry.relative_path == "agents/roles/custom-role.md" and entry.source_kind == "workspace"
        for entry in config_payload.assets.roles
    )
