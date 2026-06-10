"""Compile-time validation tests for extension manifest contracts.

Tests that:
- Extension manifest data models exist and compile
- Required-extension declarations are expressible in config/plan metadata
- Compile-time validation rejects missing or unavailable required extensions
- Extension selection uses compiler-validated identifiers, not arbitrary imports
- Extension manifest models support built-in domains
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from millrace_ai.assets.extensions import (
    discover_extension_package_manifests,
    load_extension_package_manifest,
)
from millrace_ai.compilation.outcomes import CompilerValidationError
from millrace_ai.compilation.validation.extensions import validate_required_extensions
from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import ModeDefinition, Plane
from millrace_ai.contracts.extensions import RequiredExtensionDeclaration
from millrace_ai.extensions import (
    ExtensionDomain,
    ExtensionItemKind,
    ExtensionItemManifest,
    ExtensionPackageManifest,
)
from millrace_ai.paths import bootstrap_workspace

# ── helpers ────────────────────────────────────────────────────────────────


def _copy_builtin_assets(tmp_path: Path) -> Path:
    assets_root = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"
    copied_root = tmp_path / "assets"
    shutil.copytree(assets_root, copied_root)
    return copied_root


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _compile_with_assets(tmp_path: Path, assets_root: Path):
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    return compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )


# ── data model tests ────────────────────────────────────────────────────────


class TestExtensionManifestModels:
    """Extension manifest data models exist and compile."""

    def test_extension_domain_enum_has_builtin_domains(self):
        """ExtensionDomain supports generic, recon, closure, blueprint, learning."""
        assert ExtensionDomain.GENERIC.value == "generic"
        assert ExtensionDomain.RECON.value == "recon"
        assert ExtensionDomain.CLOSURE.value == "closure"
        assert ExtensionDomain.BLUEPRINT.value == "blueprint"
        assert ExtensionDomain.LEARNING.value == "learning"

    def test_extension_item_kind_has_expected_types(self):
        """ExtensionItemKind covers all ADR-0015 vocabulary item types."""
        kinds = {k.value for k in ExtensionItemKind}
        assert "runtime_effect_operation_runner" in kinds
        assert "terminal_action" in kinds
        assert "request_context_provider" in kinds
        assert "work_item_document_adapter" in kinds
        assert "queue_claim_policy" in kinds
        assert "recovery_policy" in kinds
        assert "failure_policy" in kinds

    def test_item_manifest_constructor_and_roundtrip(self):
        """ExtensionItemManifest can be constructed and serialised round-trip."""
        item = ExtensionItemManifest(
            item_kind=ExtensionItemKind.TERMINAL_ACTION,
            item_id="example.enhanced.blueprint.approve",
            implementation_path="example_extensions.blueprint.actions",
            contract_schema_ref="millrace_ai.architecture.workflow_primitives.TerminalActionDefinition",
            version="1.0.0",
        )
        d = item.to_dict()
        assert d["item_id"] == "example.enhanced.blueprint.approve"
        assert d["item_kind"] == "terminal_action"
        assert d["version"] == "1.0.0"

        roundtripped = ExtensionItemManifest.from_dict(d)
        assert roundtripped.item_id == item.item_id
        assert roundtripped.item_kind == item.item_kind
        assert roundtripped.version == item.version
        assert roundtripped.implementation_path == item.implementation_path

    def test_item_manifest_rejects_invalid_semver(self):
        """ExtensionItemManifest rejects invalid semver strings."""
        with pytest.raises(ValueError, match="valid semver"):
            ExtensionItemManifest(
                item_kind="terminal_action",
                item_id="example.test",
                implementation_path="example.test",
                version="not.a.version",
            )

    def test_item_manifest_rejects_non_identifier_import_path(self):
        """ExtensionItemManifest rejects non-dotted Python paths."""
        with pytest.raises(ValueError, match="dotted Python module path"):
            ExtensionItemManifest(
                item_kind="terminal_action",
                item_id="example.test",
                implementation_path="not a valid python.path!",
                version="1.0.0",
            )

    def test_package_manifest_constructor_and_roundtrip(self):
        """ExtensionPackageManifest constructs and serialises round-trip."""
        items = (
            ExtensionItemManifest(
                item_kind=ExtensionItemKind.OPERATION_RUNNER,
                item_id="example.runner.echo",
                implementation_path="example_extensions.runners",
                version="1.0.0",
            ),
        )
        manifest = ExtensionPackageManifest(
            package_id="example.generic.tools",
            display_name="Example Generic Tools",
            domain=ExtensionDomain.GENERIC,
            version="1.0.0",
            items=items,
        )
        d = manifest.to_dict()
        assert d["schema_version"] == "1.0"
        assert d["kind"] == "extension_package_manifest"
        assert d["package_id"] == "example.generic.tools"
        assert len(d["items"]) == 1

        roundtripped = ExtensionPackageManifest.from_dict(d)
        assert roundtripped.package_id == manifest.package_id
        assert roundtripped.domain == manifest.domain
        assert len(roundtripped.items) == 1

    def test_package_manifest_rejects_duplicate_item_ids(self):
        """ExtensionPackageManifest rejects duplicate item ids."""
        item = ExtensionItemManifest(
            item_kind="terminal_action",
            item_id="dup.id",
            implementation_path="example.test",
            version="1.0.0",
        )
        with pytest.raises(ValueError, match="duplicate item ids"):
            ExtensionPackageManifest(
                package_id="example.test",
                display_name="Test",
                domain="generic",
                version="1.0.0",
                items=(item, item),
            )

    def test_package_manifest_rejects_self_requirement(self):
        """ExtensionPackageManifest rejects self-referencing requires."""
        with pytest.raises(ValueError, match="may not require itself"):
            ExtensionPackageManifest(
                package_id="example.test",
                display_name="Test",
                domain="generic",
                version="1.0.0",
                requires=("example.test",),
            )

    def test_package_manifest_items_by_id(self):
        """ExtensionPackageManifest.items_by_id provides O(1) lookup."""
        item = ExtensionItemManifest(
            item_kind="terminal_action",
            item_id="example.action",
            implementation_path="example.test",
            version="1.0.0",
        )
        manifest = ExtensionPackageManifest(
            package_id="example.test",
            display_name="Test",
            domain="generic",
            version="1.0.0",
            items=(item,),
        )
        assert manifest.items_by_id["example.action"] is item


# ── required-extension declaration tests ────────────────────────────────────


class TestRequiredExtensionDeclarations:
    """Required-extension declarations are expressible in config/plan metadata."""

    def test_required_extension_declaration_constructs(self):
        """RequiredExtensionDeclaration constructs from valid input."""
        decl = RequiredExtensionDeclaration(
            extension_package_id="example.test",
            min_version="1.0.0",
        )
        assert decl.extension_package_id == "example.test"
        assert decl.min_version == "1.0.0"

    def test_required_extension_declaration_rejects_invalid_id(self):
        """RequiredExtensionDeclaration rejects invalid package ids."""
        with pytest.raises(ValueError):
            RequiredExtensionDeclaration(extension_package_id="Invalid ID!")

    def test_required_extension_declaration_rejects_invalid_semver(self):
        """RequiredExtensionDeclaration rejects invalid semver in min_version."""
        with pytest.raises(ValueError, match="valid semver"):
            RequiredExtensionDeclaration(
                extension_package_id="example.test",
                min_version="not.semver",
            )

    def test_mode_accepts_required_extensions(self):
        """ModeDefinition accepts required_extensions tuple."""
        mode = ModeDefinition(
            mode_id="test_mode",
            loop_ids_by_plane={
                Plane.EXECUTION: "execution.standard",
                Plane.PLANNING: "planning.standard",
            },
            required_extensions=(
                {"extension_package_id": "example.test", "min_version": "1.0.0"},
            ),
        )
        assert len(mode.required_extensions) == 1


# ── extension manifest discovery and loading tests ──────────────────────────


class TestExtensionManifestDiscovery:
    """Extension manifests are discoverable and loadable from the asset registry."""

    def test_shipped_example_manifest_is_discovered(self):
        """The shipped example extension manifest is discovered."""
        manifests = discover_extension_package_manifests()
        ids = {m.package_id for m in manifests}
        assert "example.blueprint.enhanced" in ids

    def test_shipped_example_manifest_loads_by_id(self):
        """The shipped example manifest can be loaded by package_id."""
        manifest = load_extension_package_manifest("example.blueprint.enhanced")
        assert manifest.package_id == "example.blueprint.enhanced"
        assert manifest.domain == ExtensionDomain.BLUEPRINT
        assert manifest.version == "1.0.0"
        assert len(manifest.items) == 1

    def test_shipped_example_manifest_item_is_valid(self):
        """The shipped example manifest's item is valid."""
        manifest = load_extension_package_manifest("example.blueprint.enhanced")
        item = manifest.items[0]
        assert item.item_kind == ExtensionItemKind.TERMINAL_ACTION
        assert item.item_id == "example.enhanced.blueprint.approve"
        assert item.implementation_path == "example_extensions.blueprint.actions"
        assert item.version == "1.0.0"

    def test_discovery_rejects_duplicate_package_ids(self, tmp_path: Path):
        """Discovery raises when two manifests share the same package_id."""
        assets_root = tmp_path / "assets"
        ext_root = assets_root / "registry" / "extensions"
        ext_root.mkdir(parents=True)

        manifest = {
            "schema_version": "1.0",
            "kind": "extension_package_manifest",
            "package_id": "duplicate.id",
            "display_name": "Dup",
            "domain": "generic",
            "version": "1.0.0",
            "items": [],
        }
        _write_json(ext_root / "first.json", manifest)
        _write_json(ext_root / "second.json", manifest)

        from millrace_ai.assets.extensions import ExtensionAssetError

        with pytest.raises(ExtensionAssetError, match="Duplicate extension package id"):
            discover_extension_package_manifests(assets_root=assets_root)

    def test_discovery_rejects_wrong_kind(self, tmp_path: Path):
        """Discovery rejects manifests with wrong kind field."""
        assets_root = tmp_path / "assets"
        ext_root = assets_root / "registry" / "extensions"
        ext_root.mkdir(parents=True)

        manifest = {
            "schema_version": "1.0",
            "kind": "not_an_extension_manifest",
            "package_id": "test.wrong",
            "display_name": "Wrong",
            "domain": "generic",
            "version": "1.0.0",
            "items": [],
        }
        _write_json(ext_root / "wrong.json", manifest)

        from millrace_ai.assets.extensions import ExtensionAssetError

        with pytest.raises(ExtensionAssetError, match="must declare kind='extension_package_manifest'"):
            discover_extension_package_manifests(assets_root=assets_root)


# ── compile-time validation tests ───────────────────────────────────────────


class TestRequiredExtensionValidation:
    """Compile-time validation rejects missing or unavailable required extensions."""

    def test_validation_passes_when_required_extension_exists(self):
        """Validation passes when required extension is discovered."""
        item = ExtensionItemManifest(
            item_kind="terminal_action",
            item_id="ok.action",
            implementation_path="ok.test",
            version="1.0.0",
        )
        manifest = ExtensionPackageManifest(
            package_id="ok.test",
            display_name="OK Test",
            domain="generic",
            version="1.0.0",
            items=(item,),
        )
        mode = ModeDefinition(
            mode_id="test_mode",
            loop_ids_by_plane={
                Plane.EXECUTION: "execution.standard",
                Plane.PLANNING: "planning.standard",
            },
            required_extensions=(
                {"extension_package_id": "ok.test"},
            ),
        )
        # Should not raise
        validate_required_extensions(
            mode=mode,
            discovered_manifests=(manifest,),
        )

    def test_validation_rejects_missing_extension(self):
        """Validation rejects when required extension is not discovered."""
        mode = ModeDefinition(
            mode_id="test_mode",
            loop_ids_by_plane={
                Plane.EXECUTION: "execution.standard",
                Plane.PLANNING: "planning.standard",
            },
            required_extensions=(
                {"extension_package_id": "missing.extension"},
            ),
        )
        with pytest.raises(
            CompilerValidationError,
            match="requires extension package 'missing.extension'",
        ):
            validate_required_extensions(
                mode=mode,
                discovered_manifests=(),
            )

    def test_validation_rejects_unsatisfied_min_version(self):
        """Validation rejects when discovered version is below min_version."""
        item = ExtensionItemManifest(
            item_kind="terminal_action",
            item_id="old.action",
            implementation_path="old.test",
            version="1.0.0",
        )
        manifest = ExtensionPackageManifest(
            package_id="old.test",
            display_name="Old Test",
            domain="generic",
            version="1.0.0",
            items=(item,),
        )
        mode = ModeDefinition(
            mode_id="test_mode",
            loop_ids_by_plane={
                Plane.EXECUTION: "execution.standard",
                Plane.PLANNING: "planning.standard",
            },
            required_extensions=(
                {"extension_package_id": "old.test", "min_version": "2.0.0"},
            ),
        )
        with pytest.raises(
            CompilerValidationError,
            match="but discovered manifest has version",
        ):
            validate_required_extensions(
                mode=mode,
                discovered_manifests=(manifest,),
            )

    def test_validation_skips_when_no_required_extensions(self):
        """Validation is a no-op when mode declares no required extensions."""
        mode = ModeDefinition(
            mode_id="test_mode",
            loop_ids_by_plane={
                Plane.EXECUTION: "execution.standard",
                Plane.PLANNING: "planning.standard",
            },
        )
        # Should not raise
        validate_required_extensions(mode=mode, discovered_manifests=())

    def test_validation_accepts_satisfied_min_version(self):
        """Validation passes when discovered version satisfies min_version."""
        item = ExtensionItemManifest(
            item_kind="terminal_action",
            item_id="new.action",
            implementation_path="new.test",
            version="2.0.0",
        )
        manifest = ExtensionPackageManifest(
            package_id="new.test",
            display_name="New Test",
            domain="generic",
            version="2.0.0",
            items=(item,),
        )
        mode = ModeDefinition(
            mode_id="test_mode",
            loop_ids_by_plane={
                Plane.EXECUTION: "execution.standard",
                Plane.PLANNING: "planning.standard",
            },
            required_extensions=(
                {"extension_package_id": "new.test", "min_version": "1.0.0"},
            ),
        )
        # Should not raise
        validate_required_extensions(
            mode=mode,
            discovered_manifests=(manifest,),
        )

    def test_validation_error_message_includes_available_extensions(self):
        """Error messages list available extensions to aid debugging."""
        item = ExtensionItemManifest(
            item_kind="terminal_action",
            item_id="avail.action",
            implementation_path="avail.test",
            version="1.0.0",
        )
        manifest = ExtensionPackageManifest(
            package_id="available.extension",
            display_name="Available",
            domain="generic",
            version="1.0.0",
            items=(item,),
        )
        mode = ModeDefinition(
            mode_id="test_mode",
            loop_ids_by_plane={
                Plane.EXECUTION: "execution.standard",
                Plane.PLANNING: "planning.standard",
            },
            required_extensions=(
                {"extension_package_id": "missing.extension"},
            ),
        )
        with pytest.raises(CompilerValidationError) as exc_info:
            validate_required_extensions(
                mode=mode,
                discovered_manifests=(manifest,),
            )
        error_msg = str(exc_info.value)
        assert "Available:" in error_msg
        assert "available.extension" in error_msg


# ── identifier boundary tests ──────────────────────────────────────────────


class TestCompilerValidatedIdentifiers:
    """Extension selection uses compiler-validated identifiers, not arbitrary imports."""

    def test_implementation_path_is_not_imported_at_construction(self):
        """Constructing an ExtensionItemManifest does not import the implementation."""
        # The implementation path is validated syntactically but not imported.
        item = ExtensionItemManifest(
            item_kind="terminal_action",
            item_id="non.existent.test",
            implementation_path="this.module.does.not.exist.anywhere",
            version="1.0.0",
        )
        assert item.implementation_path == "this.module.does.not.exist.anywhere"

    def test_contract_schema_ref_is_validated_identifier(self):
        """contract_schema_ref must be a canonical dotted identifier."""
        item = ExtensionItemManifest(
            item_kind="terminal_action",
            item_id="valid.schema.ref",
            implementation_path="valid.path",
            contract_schema_ref="millrace_ai.architecture.workflow_primitives",
            version="1.0.0",
        )
        assert item.contract_schema_ref == "millrace_ai.architecture.workflow_primitives"

    def test_contract_schema_ref_rejects_non_identifier(self):
        """contract_schema_ref rejects non-identifier characters."""
        with pytest.raises(ValueError, match="dotted Python identifier path"):
            ExtensionItemManifest(
                item_kind="terminal_action",
                item_id="bad.schema.ref",
                implementation_path="good.path",
                contract_schema_ref="Not A Valid Id!",
                version="1.0.0",
            )


# ── full compilation integration tests ──────────────────────────────────────


class TestFullCompilationIntegration:
    """Extension validation integrates with the full compilation pipeline."""

    def test_default_compilation_succeeds_with_shipped_extensions(self, tmp_path: Path):
        """Default mode compilation succeeds with shipped example extensions."""
        assets_root = _copy_builtin_assets(tmp_path)
        outcome = _compile_with_assets(tmp_path, assets_root)
        assert outcome.diagnostics.ok
        assert outcome.active_plan is not None

    def test_compilation_rejects_missing_required_extension(self, tmp_path: Path):
        """Full compilation rejects when a mode requires a missing extension."""
        assets_root = _copy_builtin_assets(tmp_path)

        # Path to the default_codex mode asset
        mode_path = assets_root / "modes" / "default_codex.json"
        mode_data = json.loads(mode_path.read_text(encoding="utf-8"))
        # Keep base required extensions and add a missing one
        mode_data["required_extensions"] = [
            {"extension_package_id": "millrace.generic"},
            {"extension_package_id": "millrace.recon"},
            {"extension_package_id": "millrace.closure"},
            {"extension_package_id": "does.not.exist"},
        ]
        mode_path.write_text(json.dumps(mode_data, indent=2) + "\n", encoding="utf-8")

        outcome = _compile_with_assets(tmp_path, assets_root)
        assert not outcome.diagnostics.ok
        assert "does.not.exist" in outcome.diagnostics.errors[0]

    def test_compilation_succeeds_when_required_extension_exists(self, tmp_path: Path):
        """Full compilation succeeds when required extension is available."""
        assets_root = _copy_builtin_assets(tmp_path)

        # The example extension is already shipped; just reference it
        # alongside the base required extensions for this mode
        mode_path = assets_root / "modes" / "default_codex.json"
        mode_data = json.loads(mode_path.read_text(encoding="utf-8"))
        mode_data["required_extensions"] = [
            {"extension_package_id": "millrace.generic"},
            {"extension_package_id": "millrace.recon"},
            {"extension_package_id": "millrace.closure"},
            {"extension_package_id": "example.blueprint.enhanced"},
        ]
        mode_path.write_text(json.dumps(mode_data, indent=2) + "\n", encoding="utf-8")

        outcome = _compile_with_assets(tmp_path, assets_root)
        assert outcome.diagnostics.ok
        assert outcome.active_plan is not None


# ---------------------------------------------------------------------------
# Config-driven behavior tests: required-extension declaration changes
# ---------------------------------------------------------------------------


class TestConfigDrivenExtensionValidation:
    """Required-extension declaration changes (adding or removing an extension
    from mode config) alter compile validation outcomes without runtime code
    edits.

    Config dependency:
    - assets/modes/default_codex.json — mode with base required extensions
    - assets/registry/extensions/example_blueprint_enhanced.json — shipped extension
    """

    def test_valid_mode_config_compiles_with_base_extensions(
        self, tmp_path: Path
    ) -> None:
        """The shipped default_codex mode compiles successfully because its
        required extensions (millrace.generic, millrace.recon, millrace.closure)
        are all available.

        Config asset: assets/modes/default_codex.json
        """
        assets_root = _copy_builtin_assets(tmp_path)
        outcome = _compile_with_assets(tmp_path, assets_root)
        assert outcome.diagnostics.ok
        assert outcome.active_plan is not None

    def test_adding_missing_extension_to_config_causes_compile_failure(
        self, tmp_path: Path
    ) -> None:
        """Adding a non-existent required extension to the mode config
        causes compile validation to reject the configuration.

        Config asset: assets/modes/default_codex.json (modified)
        """
        assets_root = _copy_builtin_assets(tmp_path)

        mode_path = assets_root / "modes" / "default_codex.json"
        mode_data = json.loads(mode_path.read_text(encoding="utf-8"))
        # Add a required extension that does not exist
        mode_data["required_extensions"] = [
            {"extension_package_id": "millrace.generic"},
            {"extension_package_id": "millrace.recon"},
            {"extension_package_id": "millrace.closure"},
            {"extension_package_id": "nonexistent.extension.v2"},
        ]
        mode_path.write_text(json.dumps(mode_data, indent=2) + "\n", encoding="utf-8")

        outcome = _compile_with_assets(tmp_path, assets_root)
        assert not outcome.diagnostics.ok
        assert "nonexistent.extension.v2" in outcome.diagnostics.errors[0]

    def test_removing_required_extension_allows_compile(
        self, tmp_path: Path
    ) -> None:
        """Removing a non-existent required extension from mode config
        restores successful compilation.

        Config asset: assets/modes/default_codex.json (modified)
        """
        assets_root = _copy_builtin_assets(tmp_path)

        mode_path = assets_root / "modes" / "default_codex.json"
        mode_data = json.loads(mode_path.read_text(encoding="utf-8"))
        # Use only the base extensions (which all exist)
        mode_data["required_extensions"] = [
            {"extension_package_id": "millrace.generic"},
            {"extension_package_id": "millrace.recon"},
            {"extension_package_id": "millrace.closure"},
        ]
        mode_path.write_text(json.dumps(mode_data, indent=2) + "\n", encoding="utf-8")

        outcome = _compile_with_assets(tmp_path, assets_root)
        assert outcome.diagnostics.ok
        assert outcome.active_plan is not None

    def test_adding_available_extension_allows_compile(
        self, tmp_path: Path
    ) -> None:
        """Adding a shipped extension (example.blueprint.enhanced) to
        required_extensions still compiles successfully because the
        extension is available.

        Config asset: assets/registry/extensions/example_blueprint_enhanced.json
        """
        assets_root = _copy_builtin_assets(tmp_path)

        mode_path = assets_root / "modes" / "default_codex.json"
        mode_data = json.loads(mode_path.read_text(encoding="utf-8"))
        mode_data["required_extensions"] = [
            {"extension_package_id": "millrace.generic"},
            {"extension_package_id": "millrace.recon"},
            {"extension_package_id": "millrace.closure"},
            {"extension_package_id": "example.blueprint.enhanced"},
        ]
        mode_path.write_text(json.dumps(mode_data, indent=2) + "\n", encoding="utf-8")

        outcome = _compile_with_assets(tmp_path, assets_root)
        assert outcome.diagnostics.ok
        assert outcome.active_plan is not None

    def test_same_source_code_different_config_outcome(
        self, tmp_path: Path
    ) -> None:
        """The same source code produces different compile outcomes based
        solely on config data (required_extension declarations).  This
        proves the behavior is config-data-driven, not code-dependent.

        Config assets:
        - assets/modes/default_codex.json (with/without missing extension)
        """
        # Config A: only existing extensions → succeeds
        assets_a = _copy_builtin_assets(tmp_path / "cfg_a")
        ws_a = tmp_path / "ws_a"
        bootstrap_workspace(ws_a)
        outcome_a = compile_and_persist_workspace_plan(
            ws_a,
            config=RuntimeConfig(),
            requested_mode_id="default_codex",
            assets_root=assets_a,
        )

        # Config B: add a missing extension → fails
        import shutil as _shutil

        assets_b = tmp_path / "cfg_b"
        _shutil.copytree(assets_a, assets_b)
        mode_path_b = assets_b / "modes" / "default_codex.json"
        mode_data_b = json.loads(mode_path_b.read_text(encoding="utf-8"))
        mode_data_b["required_extensions"] = [
            {"extension_package_id": "millrace.generic"},
            {"extension_package_id": "millrace.recon"},
            {"extension_package_id": "millrace.closure"},
            {"extension_package_id": "config.driven.missing"},
        ]
        mode_path_b.write_text(json.dumps(mode_data_b, indent=2) + "\n", encoding="utf-8")
        ws_b = tmp_path / "ws_b"
        bootstrap_workspace(ws_b)
        outcome_b = compile_and_persist_workspace_plan(
            ws_b,
            config=RuntimeConfig(),
            requested_mode_id="default_codex",
            assets_root=assets_b,
        )

        # Same code, different config → different outcomes
        assert outcome_a.diagnostics.ok
        assert not outcome_b.diagnostics.ok
        assert "config.driven.missing" in outcome_b.diagnostics.errors[0]
