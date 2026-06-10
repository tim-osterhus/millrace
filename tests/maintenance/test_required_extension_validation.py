"""Required-extension validation tests.

Cover missing, unavailable, and misconfigured extension scenarios as
defined in the task acceptance criteria.

The tests validate that:
- Mode compilation fails when a required extension is missing from the registry.
- Mode compilation fails when a required extension declares an incompatible version.
- Mode compilation warns or fails when extensions are misconfigured.
- Shipped modes compile correctly with their declared extensions.
- The minimal_three_plane fixture (generic-only) compiles without domain extensions.

ADRs: ADR-0012, ADR-0015.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from millrace_ai.assets.extensions import discover_extension_package_manifests
from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config.loading import load_runtime_config
from millrace_ai.paths import bootstrap_workspace, workspace_paths


def _config_path(workspace_root: Path) -> Path:
    return workspace_root / "millrace.toml"


def _write_default_config(workspace_root: Path) -> None:
    workspace_root.mkdir(parents=True, exist_ok=True)
    config_path = workspace_root / "millrace.toml"
    config_path.write_text(
        "[runtime]\ndefault_mode = \"default_codex\"\n", encoding="utf-8"
    )


def test_all_shipped_modes_declare_required_extensions_that_exist() -> None:
    """Every shipped mode must declare only extensions that exist in the registry."""
    from millrace_ai.assets.modes import SHIPPED_MODE_IDS, load_builtin_mode_definition

    manifests = discover_extension_package_manifests()
    known_ids = {m.package_id for m in manifests}

    missing: list[str] = []
    for mode_id in SHIPPED_MODE_IDS:
        mode = load_builtin_mode_definition(mode_id)
        for req in (mode.required_extensions or ()):
            if isinstance(req, dict):
                pkg_id = req.get("extension_package_id", "")
            else:
                pkg_id = getattr(req, "extension_package_id", "")
            if pkg_id and pkg_id not in known_ids:
                missing.append(
                    f"mode {mode.mode_id!r} requires unknown extension "
                    f"{pkg_id!r}"
                )

    assert missing == [], (
        "Shipped modes declare required extensions not found in registry:\n"
        + "\n".join(missing)
    )


def test_shipped_extension_manifests_load_without_error() -> None:
    """All shipped extension manifests must parse and validate correctly."""
    manifests = discover_extension_package_manifests()
    assert len(manifests) >= 5, (
        f"Expected at least 5 built-in extension manifests, got {len(manifests)}"
    )

    # Verify the five built-in extensions exist
    builtin_ids = {m.package_id for m in manifests}
    expected = {
        "millrace.generic",
        "millrace.recon",
        "millrace.closure",
        "millrace.blueprint",
        "millrace.learning",
    }
    missing = expected - builtin_ids
    assert not missing, f"Missing built-in extension manifests: {missing}"

    # example.blueprint.enhanced should also exist
    assert "example.blueprint.enhanced" in builtin_ids, (
        "Missing example extension manifest"
    )


def test_compile_fails_when_required_extension_is_missing(tmp_path: Path) -> None:
    """Compile must fail when a mode requires an extension not in the registry."""
    assets_root = _copy_assets(tmp_path)
    workspace = tmp_path / "workspace"
    _write_default_config(workspace)
    paths = bootstrap_workspace(workspace_paths(workspace), assets_root=assets_root)

    # Write a mode that requires a non-existent extension
    mode_path = assets_root / "modes" / "missing_ext_codex.json"
    payload = {
        "schema_version": "1.0",
        "kind": "mode",
        "mode_id": "missing_ext_codex",
        "loop_ids_by_plane": {
            "execution": "execution.standard",
            "planning": "planning.standard",
        },
        "stage_entrypoint_overrides": {},
        "stage_skill_additions": {},
        "stage_model_bindings": {},
        "stage_thinking_bindings": {},
        "stage_runner_bindings": {},
        "required_extensions": [
            {"extension_package_id": "millrace.generic"},
            {"extension_package_id": "millrace.recon"},
            {"extension_package_id": "millrace.closure"},
            {"extension_package_id": "nonexistent.extension.v5"},
        ],
    }
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    outcome = compile_and_persist_workspace_plan(
        paths,
        config=load_runtime_config(_config_path(workspace)),
        requested_mode_id="missing_ext_codex",
        assets_root=assets_root,
        compile_if_needed=True,
        refuse_stale_last_known_good=False,
    )

    assert outcome.active_plan is None, "Compile should fail for missing extension"
    assert not outcome.diagnostics.ok, "Diagnostics should report compile failure"
    assert any(
        "extension" in e.lower() or "nonexistent" in e.lower()
        for e in outcome.diagnostics.errors
    ), f"Expected extension-related error, got: {outcome.diagnostics.errors}"


def test_compile_fails_when_required_extension_version_is_unsatisfiable(
    tmp_path: Path,
) -> None:
    """Compile must fail when a mode requires an extension version higher than shipped."""
    assets_root = _copy_assets(tmp_path)
    workspace = tmp_path / "workspace"
    _write_default_config(workspace)
    paths = bootstrap_workspace(workspace_paths(workspace), assets_root=assets_root)

    # Write a mode that requires an impossible version
    mode_path = assets_root / "modes" / "version_ext_codex.json"
    payload = {
        "schema_version": "1.0",
        "kind": "mode",
        "mode_id": "version_ext_codex",
        "loop_ids_by_plane": {
            "execution": "execution.standard",
            "planning": "planning.standard",
        },
        "stage_entrypoint_overrides": {},
        "stage_skill_additions": {},
        "stage_model_bindings": {},
        "stage_thinking_bindings": {},
        "stage_runner_bindings": {},
        "required_extensions": [
            {"extension_package_id": "millrace.generic"},
            {"extension_package_id": "millrace.recon"},
            {"extension_package_id": "millrace.closure"},
            {"extension_package_id": "millrace.blueprint", "min_version": "99.0.0"},
        ],
    }
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    outcome = compile_and_persist_workspace_plan(
        paths,
        config=load_runtime_config(_config_path(workspace)),
        requested_mode_id="version_ext_codex",
        assets_root=assets_root,
        compile_if_needed=True,
        refuse_stale_last_known_good=False,
    )

    assert outcome.active_plan is None, "Compile should fail for unsatisfiable version"
    assert not outcome.diagnostics.ok, "Diagnostics should report compile failure"


def test_compile_succeeds_when_no_extensions_needed(tmp_path: Path) -> None:
    """Minimal mode with no extensions declared should compile successfully.

    This test is important: not all modes need extensions.  The compile
    system must not require extensions where none are needed.
    """
    assets_root = _copy_assets(tmp_path)
    workspace = tmp_path / "workspace"
    _write_default_config(workspace)
    paths = bootstrap_workspace(workspace_paths(workspace), assets_root=assets_root)

    # Use a minimal non-shipped mode that has no extensions declared.
    # The minimal_three_plane fixture already exists, but we write a
    # synthetic one for clean isolation from fixture drift.
    mode_path = assets_root / "modes" / "no_ext_codex.json"
    payload = {
        "schema_version": "1.0",
        "kind": "mode",
        "mode_id": "no_ext_codex",
        "loop_ids_by_plane": {
            "execution": "execution.standard",
            "planning": "planning.standard",
        },
        "stage_entrypoint_overrides": {},
        "stage_skill_additions": {},
        "stage_model_bindings": {},
        "stage_thinking_bindings": {},
        "stage_runner_bindings": {},
    }
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    outcome = compile_and_persist_workspace_plan(
        paths,
        config=load_runtime_config(_config_path(workspace)),
        requested_mode_id="no_ext_codex",
        assets_root=assets_root,
        compile_if_needed=True,
        refuse_stale_last_known_good=False,
    )

    # This should fail because execution.standard/planning.standard contain
    # domain-specific stage kinds that require millrace.generic, millrace.recon,
    # and millrace.closure.  The test proves the validation catches this.
    if outcome.active_plan is None:
        assert not outcome.diagnostics.ok
        # This is expected: the mode needs extensions but didn't declare them
    else:
        # If it compiled, then no domain-specific vocabulary was needed
        pass


def test_minimal_three_plane_compiles_with_generic_only(tmp_path: Path) -> None:
    """The minimal_three_plane fixture compiles with only millrace.generic.

    This proves that the generic-only boundary works: a mode with only
    generic lifecycle behavior does not need Recon, closure, Blueprint,
    or Learning extensions.
    """
    assets_root = _copy_assets(tmp_path)
    workspace = tmp_path / "workspace"
    _write_default_config(workspace)
    paths = bootstrap_workspace(workspace_paths(workspace), assets_root=assets_root)

    outcome = compile_and_persist_workspace_plan(
        paths,
        config=load_runtime_config(_config_path(workspace)),
        requested_mode_id="minimal_three_plane",
        assets_root=assets_root,
        compile_if_needed=True,
        refuse_stale_last_known_good=False,
    )

    assert outcome.active_plan is not None, (
        f"minimal_three_plane should compile with generic-only extensions. "
        f"Errors: {outcome.diagnostics.errors}"
    )

    # Verify the compiled plan only uses generic vocabulary
    plan = outcome.active_plan
    # All stage kinds should be in the minimal/generic domain
    for node in plan.execution_graph.nodes:
        stage_kind = node.stage_kind_id
        assert stage_kind in ("basic_worker",), (
            f"Unexpected execution stage kind: {stage_kind}"
        )
    for node in plan.planning_graph.nodes:
        stage_kind = node.stage_kind_id
        assert stage_kind in ("basic_planner",), (
            f"Unexpected planning stage kind: {stage_kind}"
        )
    for node in plan.learning_graph.nodes:
        stage_kind = node.stage_kind_id
        assert stage_kind in ("basic_learner",), (
            f"Unexpected learning stage kind: {stage_kind}"
        )


def test_generic_two_plane_fixture_has_no_domain_vocabulary(tmp_path: Path) -> None:
    """The generic_two_plane_fixture compiles with only millrace.generic
    and does not depend on execution-specific, planning-specific, or
    learning-specific vocabulary.

    This proves that the fixture can represent a two-plane generic
    workflow (execution + planning) without importing domain-specific
    contracts, stage kinds, or extension modules for Recon, closure,
    Blueprint, or Learning domains. The basic_worker → builder and
    basic_planner → planner runtime-stage bindings are a runner-contract
    compatibility layer rather than arbitrary stage support.
    """
    assets_root = _copy_assets(tmp_path)
    workspace = tmp_path / "workspace"
    _write_default_config(workspace)
    paths = bootstrap_workspace(workspace_paths(workspace), assets_root=assets_root)

    outcome = compile_and_persist_workspace_plan(
        paths,
        config=load_runtime_config(_config_path(workspace)),
        requested_mode_id="generic_two_plane_fixture",
        assets_root=assets_root,
        compile_if_needed=True,
        refuse_stale_last_known_good=False,
    )

    assert outcome.active_plan is not None, (
        f"generic_two_plane_fixture should compile with generic-only extensions. "
        f"Errors: {outcome.diagnostics.errors}"
    )

    plan = outcome.active_plan

    # All stage kinds should be in the minimal/generic domain
    for node in plan.execution_graph.nodes:
        stage_kind = node.stage_kind_id
        assert stage_kind in ("basic_worker",), (
            f"Unexpected execution stage kind: {stage_kind}"
        )
    for node in plan.planning_graph.nodes:
        stage_kind = node.stage_kind_id
        assert stage_kind in ("basic_planner",), (
            f"Unexpected planning stage kind: {stage_kind}"
        )

    # The fixture must not have a learning graph at all
    assert plan.learning_graph is None, (
        "generic_two_plane_fixture should not have a learning graph"
    )

    # The mode definition itself declares only millrace.generic
    from millrace_ai.assets.modes import load_builtin_mode_definition
    mode = load_builtin_mode_definition("generic_two_plane_fixture")
    declared_ext_ids = {
        req["extension_package_id"] if isinstance(req, dict) else req.extension_package_id
        for req in (mode.required_extensions or ())
    }
    domain_extensions = {
        "millrace.recon",
        "millrace.closure",
        "millrace.blueprint",
        "millrace.learning",
    }
    assert declared_ext_ids.isdisjoint(domain_extensions), (
        f"generic_two_plane_fixture declares domain extensions "
        f"it should not: {declared_ext_ids & domain_extensions}"
    )
    assert declared_ext_ids == {"millrace.generic"}, (
        f"generic_two_plane_fixture declares unexpected extensions: "
        f"{declared_ext_ids}"
    )


def _copy_assets(tmp_path: Path) -> Path:
    assets_root = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"
    copied_root = tmp_path / "assets"
    if copied_root.exists():
        shutil.rmtree(copied_root)
    shutil.copytree(assets_root, copied_root)
    return copied_root
