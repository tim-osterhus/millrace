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
from types import SimpleNamespace

import pytest

from millrace_ai.architecture import GraphLoopDefinition
from millrace_ai.assets.extensions import (
    discover_extension_package_manifests,
    load_extension_package_manifest,
)
from millrace_ai.compilation.outcomes import CompilerValidationError
from millrace_ai.compilation.plan_authority import has_required_workflow_authority
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


def _compile_with_assets(
    tmp_path: Path,
    assets_root: Path,
    *,
    requested_mode_id: str = "standard_plain",
):
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    return compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id=requested_mode_id,
        assets_root=assets_root,
    )


def _force_generated_two_plane_scheduler_fixture(assets_root: Path) -> None:
    scheduler_path = assets_root / "registry" / "scheduler_policies" / "default_two_plane.json"
    payload = json.loads(scheduler_path.read_text(encoding="utf-8"))
    for item in payload["definitions"]:
        if item["policy_id"] != "default.two_plane":
            continue
        item["plane_order"] = ["execution"]
        item["lanes"] = [
            lane for lane in item["lanes"] if lane["plane"] == "execution"
        ]
        item["claim_policies_by_plane"] = {
            "execution": item["claim_policies_by_plane"]["execution"]
        }
        item["completion_check_order"] = ["execution"]
        item["foreground_order"] = ["execution"]
        item["rules"] = []
    scheduler_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


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
        assert "runtime_effect_operation" in kinds
        assert "runtime_effect_runner" in kinds
        assert "runtime_effect_handler" in kinds
        assert "runtime_effect_rule" in kinds
        assert "runtime_effect_primitive" in kinds
        assert "runtime_effect_validator" in kinds
        assert "runtime_effect_store" in kinds
        assert "terminal_action" in kinds
        assert "request_context_provider" in kinds
        assert "request_context_profile" in kinds
        assert "request_context_render_plan" in kinds
        assert "work_item_document_adapter" in kinds
        assert "work_item_family" in kinds
        assert "queue_claim_policy" in kinds
        assert "queue_lifecycle_policy" in kinds
        assert "recovery_policy" in kinds
        assert "failure_policy" in kinds
        assert "runtime_failure_policy" in kinds
        assert "scheduler_policy" in kinds
        assert "lifecycle_mutation_plan" in kinds
        assert "artifact_contract" in kinds
        assert "workspace_schema_epoch" in kinds
        assert "doctor_diagnostic" in kinds
        assert "status_projection" in kinds

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

    def test_validation_rejects_duplicate_manifest_item_ownership(self):
        """Two extension packages cannot own the same manifest item."""
        item_a = ExtensionItemManifest(
            item_kind="stage_kind",
            item_id="extension.stage",
            implementation_path="example.test",
            version="1.0.0",
        )
        item_b = ExtensionItemManifest(
            item_kind="stage_kind",
            item_id="extension.stage",
            implementation_path="other.test",
            version="1.0.0",
        )
        manifest_a = ExtensionPackageManifest(
            package_id="owner.a",
            display_name="Owner A",
            domain="generic",
            version="1.0.0",
            items=(item_a,),
        )
        manifest_b = ExtensionPackageManifest(
            package_id="owner.b",
            display_name="Owner B",
            domain="generic",
            version="1.0.0",
            items=(item_b,),
        )
        mode = ModeDefinition(
            mode_id="test_mode",
            loop_ids_by_plane={
                Plane.EXECUTION: "execution.standard",
                Plane.PLANNING: "planning.standard",
            },
        )

        with pytest.raises(CompilerValidationError, match="Duplicate extension-owned"):
            validate_required_extensions(
                mode=mode,
                discovered_manifests=(manifest_a, manifest_b),
            )

    def test_validation_rejects_unknown_manifest_item_for_registry_family(self):
        """Manifest-owned registry items must exist in the matching asset family."""
        manifest = ExtensionPackageManifest(
            package_id="owner.test",
            display_name="Owner Test",
            domain="generic",
            version="1.0.0",
            items=(
                ExtensionItemManifest(
                    item_kind="stage_kind",
                    item_id="missing.stage",
                    implementation_path="example.test",
                    version="1.0.0",
                ),
            ),
        )
        mode = ModeDefinition(
            mode_id="test_mode",
            loop_ids_by_plane={
                Plane.EXECUTION: "execution.standard",
                Plane.PLANNING: "planning.standard",
            },
        )

        with pytest.raises(CompilerValidationError) as exc_info:
            validate_required_extensions(
                mode=mode,
                discovered_manifests=(manifest,),
                stage_kinds={},
            )

        error_msg = str(exc_info.value)
        assert "owner.test" in error_msg
        assert "stage_kind" in error_msg
        assert "missing.stage" in error_msg

    def test_validation_rejects_undeclared_manifest_owned_graph_reference(self):
        """Graph references to manifest-owned items require the owning package."""
        manifest = ExtensionPackageManifest(
            package_id="owner.test",
            display_name="Owner Test",
            domain="generic",
            version="1.0.0",
            items=(
                ExtensionItemManifest(
                    item_kind="stage_kind",
                    item_id="extension.stage",
                    implementation_path="example.test",
                    version="1.0.0",
                ),
            ),
        )
        mode = ModeDefinition(
            mode_id="test_mode",
            loop_ids_by_plane={
                Plane.EXECUTION: "execution.extension",
                Plane.PLANNING: "planning.standard",
            },
            required_extensions=(),
        )
        graph = GraphLoopDefinition.model_validate(
            {
                "loop_id": "execution.extension",
                "plane": "execution",
                "nodes": [
                    {"node_id": "extension_node", "stage_kind_id": "extension.stage"}
                ],
                "entry_nodes": [{"entry_key": "task", "node_id": "extension_node"}],
                "edges": [
                    {
                        "edge_id": "done",
                        "from_node_id": "extension_node",
                        "terminal_state_id": "done",
                        "on_outcomes": ["DONE"],
                        "kind": "terminal",
                    }
                ],
                "terminal_states": [
                    {
                        "terminal_state_id": "done",
                        "terminal_class": "success",
                        "terminal_action_id": "complete_work_item",
                        "writes_status": "DONE",
                    }
                ],
            }
        )

        with pytest.raises(CompilerValidationError) as exc_info:
            validate_required_extensions(
                mode=mode,
                discovered_manifests=(manifest,),
                graph_loops={Plane.EXECUTION: graph},
                stage_kinds={"extension.stage": object()},  # type: ignore[dict-item]
            )

        error_msg = str(exc_info.value)
        assert "test_mode" in error_msg
        assert "owner.test" in error_msg
        assert "stage_kind" in error_msg
        assert "extension.stage" in error_msg
        assert "extension_node" in error_msg

    def test_validation_rejects_undeclared_family_document_and_queue_lifecycle_refs(self):
        """Selected work-item family adapter dependencies require owning packages."""
        manifest = ExtensionPackageManifest(
            package_id="owner.test",
            display_name="Owner Test",
            domain="generic",
            version="1.0.0",
            items=(
                ExtensionItemManifest(
                    item_kind="work_item_document_adapter",
                    item_id="owner.document_adapter",
                    implementation_path="example.document_adapter",
                    version="1.0.0",
                ),
                ExtensionItemManifest(
                    item_kind="queue_lifecycle_policy",
                    item_id="owner.queue_lifecycle",
                    implementation_path="example.queue_lifecycle",
                    version="1.0.0",
                ),
            ),
        )
        mode = ModeDefinition(
            mode_id="test_mode",
            loop_ids_by_plane={
                Plane.EXECUTION: "execution.extension",
                Plane.PLANNING: "planning.standard",
            },
            required_extensions=(),
        )
        graph = GraphLoopDefinition.model_validate(
            {
                "loop_id": "execution.extension",
                "plane": "execution",
                "nodes": [
                    {"node_id": "worker", "stage_kind_id": "basic_worker"}
                ],
                "entry_nodes": [{"entry_key": "task", "node_id": "worker"}],
                "edges": [
                    {
                        "edge_id": "done",
                        "from_node_id": "worker",
                        "terminal_state_id": "done",
                        "on_outcomes": ["DONE"],
                        "kind": "terminal",
                    }
                ],
                "terminal_states": [
                    {
                        "terminal_state_id": "done",
                        "terminal_class": "success",
                        "terminal_action_id": "complete_work_item",
                        "writes_status": "DONE",
                    }
                ],
            }
        )
        workflow_primitives = SimpleNamespace(
            artifact_contracts=(),
            request_context_profiles=(),
            request_context_providers=(),
            request_context_render_plans=(),
            work_item_families=(
                SimpleNamespace(
                    family_id="task",
                    document_adapter_id="owner.document_adapter",
                    queue_lifecycle_adapter_id="owner.queue_lifecycle",
                ),
            ),
            document_adapters=(SimpleNamespace(adapter_id="owner.document_adapter"),),
            queue_claim_policies=(),
            terminal_actions=(),
            lifecycle_mutation_plans=(),
            runtime_effect_handlers=(),
            runtime_effect_runners=(),
            runtime_effect_rules=(),
            effect_stores=(),
            effect_validators=(),
            runtime_effect_operations=(),
            runtime_operations=(),
            runtime_effect_primitives=(),
            recovery_policies=(),
            runtime_failure_policies=(),
            scheduler_policies=(),
            workspace_schema_epoch=None,
        )

        with pytest.raises(CompilerValidationError) as exc_info:
            validate_required_extensions(
                mode=mode,
                discovered_manifests=(manifest,),
                graph_loops={Plane.EXECUTION: graph},
                stage_kinds={"basic_worker": object()},  # type: ignore[dict-item]
                workflow_primitives=workflow_primitives,
            )

        error_msg = str(exc_info.value)
        assert "test_mode" in error_msg
        assert "owner.test" in error_msg
        assert "work_item_document_adapter" in error_msg
        assert "owner.document_adapter" in error_msg
        assert "graph 'execution.extension' entry 'task'" in error_msg

        declared_mode = mode.model_copy(
            update={"required_extensions": [{"extension_package_id": "owner.test"}]}
        )
        validate_required_extensions(
            mode=declared_mode,
            discovered_manifests=(manifest,),
            graph_loops={Plane.EXECUTION: graph},
            stage_kinds={"basic_worker": object()},  # type: ignore[dict-item]
            workflow_primitives=workflow_primitives,
        )

    def test_validation_rejects_undeclared_runtime_effect_refs(self):
        """Graph-selected runtime-effect metadata requires owning packages."""
        manifest = ExtensionPackageManifest(
            package_id="owner.test",
            display_name="Owner Test",
            domain="generic",
            version="1.0.0",
            items=(
                ExtensionItemManifest(
                    item_kind="runtime_effect_rule",
                    item_id="owner.effect_rule",
                    implementation_path="example.effect_rule",
                    version="1.0.0",
                ),
                ExtensionItemManifest(
                    item_kind="runtime_effect_handler",
                    item_id="owner.effect_handler",
                    implementation_path="example.effect_handler",
                    version="1.0.0",
                ),
                ExtensionItemManifest(
                    item_kind="runtime_effect_operation",
                    item_id="owner.effect_operation",
                    implementation_path="example.effect_operation",
                    version="1.0.0",
                ),
                ExtensionItemManifest(
                    item_kind="runtime_effect_runner",
                    item_id="owner.effect_runner",
                    implementation_path="example.effect_runner",
                    version="1.0.0",
                ),
            ),
        )
        mode = ModeDefinition(
            mode_id="test_mode",
            loop_ids_by_plane={
                Plane.EXECUTION: "execution.extension",
                Plane.PLANNING: "planning.standard",
            },
            required_extensions=(),
        )
        graph = GraphLoopDefinition.model_validate(
            {
                "loop_id": "execution.extension",
                "plane": "execution",
                "nodes": [
                    {"node_id": "worker", "stage_kind_id": "basic_worker"}
                ],
                "entry_nodes": [{"entry_key": "task", "node_id": "worker"}],
                "edges": [
                    {
                        "edge_id": "done",
                        "from_node_id": "worker",
                        "terminal_state_id": "done",
                        "on_outcomes": ["DONE"],
                        "kind": "terminal",
                    }
                ],
                "terminal_states": [
                    {
                        "terminal_state_id": "done",
                        "terminal_class": "success",
                        "terminal_action_id": "complete_work_item",
                        "writes_status": "DONE",
                    }
                ],
            }
        )
        workflow_primitives = SimpleNamespace(
            artifact_contracts=(),
            request_context_profiles=(),
            request_context_providers=(),
            request_context_render_plans=(),
            work_item_families=(),
            document_adapters=(),
            queue_claim_policies=(),
            terminal_actions=(),
            lifecycle_mutation_plans=(),
            runtime_effect_handlers=(SimpleNamespace(handler_id="owner.effect_handler"),),
            runtime_effect_runners=(
                SimpleNamespace(
                    runner_id="owner.effect_runner",
                    operation_ids=("owner.effect_operation",),
                    legacy_handler_ids=("owner.effect_handler",),
                    legacy_handler_operation_ids={},
                ),
            ),
            runtime_effect_rules=(
                SimpleNamespace(
                    rule_id="owner.effect_rule",
                    source_node_id="worker",
                    effect_operation_id="owner.effect_operation",
                    handler_id="owner.effect_handler",
                    destination_family_id=None,
                    lifecycle_mutation_plan_id=None,
                    source_completion_lifecycle_mutation_plan_id=None,
                    source_blocking_lifecycle_mutation_plan_id=None,
                    source_completion_lifecycle_mutation_plan_ids_by_family={},
                    source_blocking_lifecycle_mutation_plan_ids_by_family={},
                    required_run_artifacts=(),
                ),
            ),
            effect_stores=(),
            effect_validators=(),
            runtime_effect_operations=(
                SimpleNamespace(
                    operation_id="owner.effect_operation",
                    legacy_handler_ids=("owner.effect_handler",),
                    required_artifacts=(),
                    produced_artifacts=(),
                    steps=(),
                    failure_mappings=(),
                    repair_closure_contracts=(),
                ),
            ),
            runtime_operations=(),
            runtime_effect_primitives=(),
            recovery_policies=(),
            runtime_failure_policies=(),
            scheduler_policies=(),
            workspace_schema_epoch=None,
        )

        with pytest.raises(CompilerValidationError) as exc_info:
            validate_required_extensions(
                mode=mode,
                discovered_manifests=(manifest,),
                graph_loops={Plane.EXECUTION: graph},
                stage_kinds={"basic_worker": object()},  # type: ignore[dict-item]
                workflow_primitives=workflow_primitives,
            )

        error_msg = str(exc_info.value)
        assert "test_mode" in error_msg
        assert "owner.test" in error_msg
        assert "runtime_effect_rule" in error_msg
        assert "owner.effect_rule" in error_msg
        assert "graph 'execution.extension' node 'worker'" in error_msg

        declared_mode = mode.model_copy(
            update={"required_extensions": [{"extension_package_id": "owner.test"}]}
        )
        validate_required_extensions(
            mode=declared_mode,
            discovered_manifests=(manifest,),
            graph_loops={Plane.EXECUTION: graph},
            stage_kinds={"basic_worker": object()},  # type: ignore[dict-item]
            workflow_primitives=workflow_primitives,
        )

    def test_validation_rejects_undeclared_runtime_failure_policy_refs(self):
        """Active runtime-failure policies require the owning package."""
        manifest = ExtensionPackageManifest(
            package_id="owner.test",
            display_name="Owner Test",
            domain="generic",
            version="1.0.0",
            items=(
                ExtensionItemManifest(
                    item_kind="runtime_failure_policy",
                    item_id="owner.failure_policy",
                    implementation_path="example.failure_policy",
                    version="1.0.0",
                ),
            ),
        )
        mode = ModeDefinition(
            mode_id="test_mode",
            loop_ids_by_plane={
                Plane.EXECUTION: "execution.extension",
                Plane.PLANNING: "planning.standard",
            },
            required_extensions=(),
        )
        graph = GraphLoopDefinition.model_validate(
            {
                "loop_id": "execution.extension",
                "plane": "execution",
                "nodes": [
                    {"node_id": "worker", "stage_kind_id": "basic_worker"}
                ],
                "entry_nodes": [{"entry_key": "task", "node_id": "worker"}],
                "edges": [
                    {
                        "edge_id": "done",
                        "from_node_id": "worker",
                        "terminal_state_id": "done",
                        "on_outcomes": ["DONE"],
                        "kind": "terminal",
                    }
                ],
                "terminal_states": [
                    {
                        "terminal_state_id": "done",
                        "terminal_class": "success",
                        "terminal_action_id": "complete_work_item",
                        "writes_status": "DONE",
                    }
                ],
            }
        )
        workflow_primitives = SimpleNamespace(
            artifact_contracts=(),
            request_context_profiles=(),
            request_context_providers=(),
            request_context_render_plans=(),
            work_item_families=(),
            document_adapters=(),
            queue_claim_policies=(),
            terminal_actions=(),
            lifecycle_mutation_plans=(),
            runtime_effect_handlers=(),
            runtime_effect_runners=(),
            runtime_effect_rules=(),
            effect_stores=(),
            effect_validators=(),
            runtime_effect_operations=(),
            runtime_operations=(),
            runtime_effect_primitives=(),
            recovery_policies=(),
            runtime_failure_policies=(
                SimpleNamespace(
                    policy_id="owner.failure_policy",
                    applies_to_planes=(Plane.EXECUTION,),
                    applies_to_source_node_ids=("worker",),
                    applies_to_families=(),
                    applies_to_operation_ids=(),
                    applies_to_handler_ids=(),
                    recovery_node_id=None,
                    target_node_id=None,
                    repair_closure_mappings=(),
                ),
            ),
            scheduler_policies=(),
            workspace_schema_epoch=None,
        )

        with pytest.raises(CompilerValidationError) as exc_info:
            validate_required_extensions(
                mode=mode,
                discovered_manifests=(manifest,),
                graph_loops={Plane.EXECUTION: graph},
                stage_kinds={"basic_worker": object()},  # type: ignore[dict-item]
                workflow_primitives=workflow_primitives,
            )

        error_msg = str(exc_info.value)
        assert "test_mode" in error_msg
        assert "owner.test" in error_msg
        assert "runtime_failure_policy" in error_msg
        assert "owner.failure_policy" in error_msg
        assert "graph 'execution.extension' node 'worker'" in error_msg

        declared_mode = mode.model_copy(
            update={"required_extensions": [{"extension_package_id": "owner.test"}]}
        )
        validate_required_extensions(
            mode=declared_mode,
            discovered_manifests=(manifest,),
            graph_loops={Plane.EXECUTION: graph},
            stage_kinds={"basic_worker": object()},  # type: ignore[dict-item]
            workflow_primitives=workflow_primitives,
        )


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

    def test_compiled_plan_authority_requires_scheduler_policy(self, tmp_path: Path):
        """A persisted plan missing compiled scheduler authority must not be reused."""
        assets_root = _copy_builtin_assets(tmp_path)
        outcome = _compile_with_assets(tmp_path, assets_root)
        assert outcome.active_plan is not None

        stripped = outcome.active_plan.model_copy(
            update={
                "scheduler_policy": None,
            }
        )

        assert not has_required_workflow_authority(stripped)

    def test_compiled_plan_authority_accepts_auto_derived_scheduler_policy(
        self,
        tmp_path: Path,
    ):
        """Auto-derived scheduler policies use a registry asset as source authority."""
        assets_root = _copy_builtin_assets(tmp_path)
        outcome = _compile_with_assets(
            tmp_path,
            assets_root,
            requested_mode_id="lad_codex",
        )
        assert outcome.active_plan is not None
        assert outcome.active_plan.scheduler_policy is not None
        assert outcome.active_plan.scheduler_policy.policy_id == "lad_codex.scheduler"
        assert outcome.active_plan.selected_scheduler_policy_asset_id == "default.two_plane"

        assert has_required_workflow_authority(outcome.active_plan)

    def test_compiled_plan_authority_accepts_explicit_scheduler_policy_id(
        self,
        tmp_path: Path,
    ):
        """Modes with explicit scheduler_policy_id record that registry source."""
        assets_root = _copy_builtin_assets(tmp_path)
        outcome = _compile_with_assets(
            tmp_path,
            assets_root,
            requested_mode_id="blueprint_lad_codex",
        )
        assert outcome.active_plan is not None
        assert outcome.active_plan.scheduler_policy is not None
        assert outcome.active_plan.scheduler_policy.policy_id == "default.two_plane.blueprint"
        assert outcome.active_plan.scheduler_policy_authority_kind == "registry"
        assert (
            outcome.active_plan.selected_scheduler_policy_asset_id
            == "default.two_plane.blueprint"
        )
        assert has_required_workflow_authority(outcome.active_plan)

        stripped = outcome.active_plan.model_copy(
            update={
                "resolved_assets": tuple(
                    ref
                    for ref in outcome.active_plan.resolved_assets
                    if ref.logical_id != "scheduler_policy:default.two_plane.blueprint"
                )
            }
        )

        assert not has_required_workflow_authority(stripped)

    def test_compiled_plan_authority_accepts_generated_scheduler_policy(
        self,
        tmp_path: Path,
    ):
        """Generated scheduler policies are authoritative without a source asset id."""
        assets_root = _copy_builtin_assets(tmp_path)
        _force_generated_two_plane_scheduler_fixture(assets_root)

        outcome = _compile_with_assets(
            tmp_path,
            assets_root,
            requested_mode_id="lad_codex",
        )
        assert outcome.active_plan is not None
        assert outcome.active_plan.scheduler_policy is not None
        assert outcome.active_plan.scheduler_policy.policy_id == "lad_codex.scheduler"
        assert outcome.active_plan.scheduler_policy_authority_kind == "generated"
        assert outcome.active_plan.selected_scheduler_policy_asset_id is None

        assert has_required_workflow_authority(outcome.active_plan)

    def test_compiled_plan_authority_generated_scheduler_does_not_require_scheduler_refs(
        self,
        tmp_path: Path,
    ):
        """Generated scheduler policies do not need scheduler-policy resolved refs."""
        assets_root = _copy_builtin_assets(tmp_path)
        _force_generated_two_plane_scheduler_fixture(assets_root)

        outcome = _compile_with_assets(
            tmp_path,
            assets_root,
            requested_mode_id="lad_codex",
        )
        assert outcome.active_plan is not None
        assert outcome.active_plan.scheduler_policy_authority_kind == "generated"

        stripped = outcome.active_plan.model_copy(
            update={
                "resolved_assets": tuple(
                    ref
                    for ref in outcome.active_plan.resolved_assets
                    if ref.asset_family != "scheduler_policy"
                )
            }
        )

        assert has_required_workflow_authority(stripped)

    def test_compiled_plan_authority_rejects_generated_scheduler_with_selected_asset_id(
        self,
        tmp_path: Path,
    ):
        """Generated scheduler policies must not claim a selected registry asset."""
        assets_root = _copy_builtin_assets(tmp_path)
        _force_generated_two_plane_scheduler_fixture(assets_root)

        outcome = _compile_with_assets(
            tmp_path,
            assets_root,
            requested_mode_id="lad_codex",
        )
        assert outcome.active_plan is not None
        assert outcome.active_plan.scheduler_policy_authority_kind == "generated"

        stripped = outcome.active_plan.model_copy(
            update={"selected_scheduler_policy_asset_id": "default.two_plane"}
        )

        assert not has_required_workflow_authority(stripped)

    def test_compiled_plan_authority_requires_selected_scheduler_asset_ref(
        self,
        tmp_path: Path,
    ):
        """Registry-backed scheduler authority requires its exact resolved asset ref."""
        assets_root = _copy_builtin_assets(tmp_path)
        outcome = _compile_with_assets(
            tmp_path,
            assets_root,
            requested_mode_id="lad_codex",
        )
        assert outcome.active_plan is not None
        assert outcome.active_plan.selected_scheduler_policy_asset_id == "default.two_plane"

        stripped = outcome.active_plan.model_copy(
            update={
                "resolved_assets": tuple(
                    ref
                    for ref in outcome.active_plan.resolved_assets
                    if ref.logical_id != "scheduler_policy:default.two_plane"
                )
            }
        )

        assert not has_required_workflow_authority(stripped)

    def test_compiled_plan_authority_requires_selected_recovery_policies(
        self,
        tmp_path: Path,
    ):
        """Selected recovery policy definitions are required authority surfaces."""
        assets_root = _copy_builtin_assets(tmp_path)
        outcome = _compile_with_assets(
            tmp_path,
            assets_root,
            requested_mode_id="recovery_heavy_millrace",
        )
        assert outcome.active_plan is not None
        assert outcome.active_plan.selected_workflow_recovery_policy_ids
        assert outcome.active_plan.workflow_recovery_policies_by_id

        stripped = outcome.active_plan.model_copy(
            update={"workflow_recovery_policies_by_id": {}}
        )

        assert not has_required_workflow_authority(stripped)

    def test_compiled_plan_authority_requires_selected_recovery_policy_ids(
        self,
        tmp_path: Path,
    ):
        """Selected recovery policy id metadata distinguishes old plans from empty selection."""
        assets_root = _copy_builtin_assets(tmp_path)
        outcome = _compile_with_assets(
            tmp_path,
            assets_root,
            requested_mode_id="recovery_heavy_millrace",
        )
        assert outcome.active_plan is not None
        assert outcome.active_plan.selected_workflow_recovery_policy_ids

        stripped = outcome.active_plan.model_copy(
            update={"selected_workflow_recovery_policy_ids": None}
        )

        assert not has_required_workflow_authority(stripped)

    def test_compiled_plan_authority_rejects_mismatched_selected_recovery_policy_definition(
        self,
        tmp_path: Path,
    ):
        """Selected recovery policy map keys must match each definition's policy_id."""
        assets_root = _copy_builtin_assets(tmp_path)
        outcome = _compile_with_assets(
            tmp_path,
            assets_root,
            requested_mode_id="recovery_heavy_millrace",
        )
        assert outcome.active_plan is not None
        policies = dict(outcome.active_plan.workflow_recovery_policies_by_id)
        execution_policy = policies["execution.blocked_recovery_heavy"]
        policies["execution.blocked_recovery_heavy"] = execution_policy.model_copy(
            update={"policy_id": "planning.blocked_recovery_heavy"}
        )

        stripped = outcome.active_plan.model_copy(
            update={"workflow_recovery_policies_by_id": policies}
        )

        assert not has_required_workflow_authority(stripped)

    def test_compiled_plan_authority_rejects_extra_unselected_recovery_policy_definition(
        self,
        tmp_path: Path,
    ):
        """Plans may not carry extra recovery policies outside the selected id set."""
        assets_root = _copy_builtin_assets(tmp_path)
        outcome = _compile_with_assets(
            tmp_path,
            assets_root,
            requested_mode_id="recovery_heavy_millrace",
        )
        assert outcome.active_plan is not None
        policies = dict(outcome.active_plan.workflow_recovery_policies_by_id)
        assert outcome.active_plan.selected_workflow_recovery_policy_ids is not None
        selected_ids = set(outcome.active_plan.selected_workflow_recovery_policy_ids)
        assert "execution.blocked_recovery_heavy" in selected_ids

        extra_policy = policies["execution.blocked_recovery_heavy"].model_copy(
            update={"policy_id": "execution.unselected_extra"}
        )
        policies["execution.unselected_extra"] = extra_policy

        stripped = outcome.active_plan.model_copy(
            update={"workflow_recovery_policies_by_id": policies}
        )

        assert not has_required_workflow_authority(stripped)

    def test_compiled_plan_authority_requires_selected_recovery_policy_asset_ref(
        self,
        tmp_path: Path,
    ):
        """Selected recovery policy authority requires its exact resolved asset refs."""
        assets_root = _copy_builtin_assets(tmp_path)
        outcome = _compile_with_assets(
            tmp_path,
            assets_root,
            requested_mode_id="recovery_heavy_millrace",
        )
        assert outcome.active_plan is not None
        assert outcome.active_plan.selected_workflow_recovery_policy_ids
        selected_id = outcome.active_plan.selected_workflow_recovery_policy_ids[0]

        stripped = outcome.active_plan.model_copy(
            update={
                "resolved_assets": tuple(
                    ref
                    for ref in outcome.active_plan.resolved_assets
                    if ref.logical_id != f"workflow_recovery_policy:{selected_id}"
                )
            }
        )

        assert not has_required_workflow_authority(stripped)

    def test_compilation_rejects_missing_required_extension(self, tmp_path: Path):
        """Full compilation rejects when a mode requires a missing extension."""
        assets_root = _copy_builtin_assets(tmp_path)

        # Path to the default_codex mode asset
        mode_path = assets_root / "modes" / "lad_codex.json"
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
        mode_path = assets_root / "modes" / "lad_codex.json"
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

    def test_blueprint_mode_rejects_missing_blueprint_extension(self, tmp_path: Path):
        """Blueprint-owned vocabulary requires millrace.blueprint."""
        assets_root = _copy_builtin_assets(tmp_path)
        mode_path = assets_root / "modes" / "blueprint_lad_codex.json"
        mode_data = json.loads(mode_path.read_text(encoding="utf-8"))
        mode_data["required_extensions"] = [
            entry
            for entry in mode_data["required_extensions"]
            if entry["extension_package_id"] != "millrace.blueprint"
        ]
        _write_json(mode_path, mode_data)

        workspace_root = tmp_path / "workspace"
        bootstrap_workspace(workspace_root)
        outcome = compile_and_persist_workspace_plan(
            workspace_root,
            config=RuntimeConfig(),
            requested_mode_id="blueprint_codex",
            assets_root=assets_root,
        )

        assert not outcome.diagnostics.ok
        error_msg = outcome.diagnostics.errors[0]
        assert "millrace.blueprint" in error_msg
        assert "item_kind='stage_kind'" in error_msg
        assert "manager_blueprint" in error_msg

    @pytest.mark.parametrize(
        ("mode_id", "removed_extension", "expected_item_id"),
        (
            ("lad_codex", "millrace.recon", "recon"),
            ("lad_codex", "millrace.closure", "lad_arbiter"),
            ("learning_lad_codex", "millrace.learning", "analyst"),
        ),
    )
    def test_domain_modes_reject_missing_declared_owner(
        self,
        tmp_path: Path,
        mode_id: str,
        removed_extension: str,
        expected_item_id: str,
    ):
        """Domain-owned vocabulary reports the missing owner and item kind."""
        assets_root = _copy_builtin_assets(tmp_path)
        mode_path = assets_root / "modes" / f"{mode_id}.json"
        mode_data = json.loads(mode_path.read_text(encoding="utf-8"))
        mode_data["required_extensions"] = [
            entry
            for entry in mode_data["required_extensions"]
            if entry["extension_package_id"] != removed_extension
        ]
        _write_json(mode_path, mode_data)

        workspace_root = tmp_path / "workspace"
        bootstrap_workspace(workspace_root)
        outcome = compile_and_persist_workspace_plan(
            workspace_root,
            config=RuntimeConfig(),
            requested_mode_id=mode_id,
            assets_root=assets_root,
        )

        assert not outcome.diagnostics.ok
        error_msg = outcome.diagnostics.errors[0]
        assert removed_extension in error_msg
        assert "item_kind='stage_kind'" in error_msg
        assert expected_item_id in error_msg

    @pytest.mark.parametrize(
        "mode_id",
        ("lad_codex", "lad_pi", "generic_two_plane_fixture"),
    )
    def test_non_blueprint_modes_do_not_require_blueprint_extension(
        self, tmp_path: Path, mode_id: str
    ):
        """Non-Blueprint shipped modes compile without millrace.blueprint."""
        assets_root = _copy_builtin_assets(tmp_path)
        mode_path = assets_root / "modes" / f"{mode_id}.json"
        mode_data = json.loads(mode_path.read_text(encoding="utf-8"))
        assert all(
            entry["extension_package_id"] != "millrace.blueprint"
            for entry in mode_data["required_extensions"]
        )

        workspace_root = tmp_path / "workspace"
        bootstrap_workspace(workspace_root)
        outcome = compile_and_persist_workspace_plan(
            workspace_root,
            config=RuntimeConfig(),
            requested_mode_id=mode_id,
            assets_root=assets_root,
        )

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
    - assets/modes/lad_codex.json — mode with base required extensions
    - assets/registry/extensions/example_blueprint_enhanced.json — shipped extension
    """

    def test_valid_mode_config_compiles_with_base_extensions(
        self, tmp_path: Path
    ) -> None:
        """The shipped default_codex mode compiles successfully because its
        required extensions (millrace.generic, millrace.recon, millrace.closure)
        are all available.

        Config asset: assets/modes/lad_codex.json
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

        Config asset: assets/modes/lad_codex.json (modified)
        """
        assets_root = _copy_builtin_assets(tmp_path)

        mode_path = assets_root / "modes" / "lad_codex.json"
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

        Config asset: assets/modes/lad_codex.json (modified)
        """
        assets_root = _copy_builtin_assets(tmp_path)

        mode_path = assets_root / "modes" / "lad_codex.json"
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

        mode_path = assets_root / "modes" / "lad_codex.json"
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
        - assets/modes/lad_codex.json (with/without missing extension)
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
        mode_path_b = assets_b / "modes" / "lad_codex.json"
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
