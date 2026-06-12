"""Tests for data-driven request context providers."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from millrace_ai.contracts import Plane
from millrace_ai.extensions import (
    ExtensionItemKind,
    ExtensionItemManifest,
    ExtensionPackageManifest,
)
from millrace_ai.runtime.engine import RuntimeEngine


def _noop_stage_runner(request: object) -> object:
    raise AssertionError("stage runner should not be called")


BLUEPRINT_IMPL_MODULE_PREFIX = "millrace_ai.extensions.builtin.blueprint"


def _unload_blueprint_impl_modules() -> None:
    for name in list(sys.modules):
        if name.startswith(BLUEPRINT_IMPL_MODULE_PREFIX):
            del sys.modules[name]


def _loaded_blueprint_impl_modules() -> list[str]:
    return sorted(
        name for name in sys.modules if name.startswith(BLUEPRINT_IMPL_MODULE_PREFIX)
    )


class TestRequestContextDataDriven:
    """Request context is derived from compiled plan metadata."""

    def test_compiled_plan_provides_execution_plane_context(
        self, tmp_path: Path
    ) -> None:
        engine = RuntimeEngine(tmp_path, stage_runner=_noop_stage_runner)
        engine.startup()
        assert engine.compiled_plan is not None
        assert engine.compiled_plan.execution_graph is not None

        graph = engine.compiled_plan.execution_graph
        # Compiled plan graph nodes carry stage metadata for dispatch.
        assert len(graph.nodes) > 0
        for node in graph.nodes:
            assert node.node_id
            assert node.stage_kind_id
            assert node.runtime_stage is not None

    def test_compiled_plan_provides_planning_plane_context(
        self, tmp_path: Path
    ) -> None:
        engine = RuntimeEngine(tmp_path, stage_runner=_noop_stage_runner)
        engine.startup()
        assert engine.compiled_plan is not None
        assert engine.compiled_plan.planning_graph is not None

        graph = engine.compiled_plan.planning_graph
        assert len(graph.nodes) > 0
        for node in graph.nodes:
            assert node.node_id
            assert node.stage_kind_id
            assert node.runtime_stage is not None

    def test_work_item_families_from_compiled_plan(self, tmp_path: Path) -> None:
        engine = RuntimeEngine(tmp_path, stage_runner=_noop_stage_runner)
        engine.startup()
        assert engine.compiled_plan is not None

        families = engine.compiled_plan.work_item_families_by_id
        assert len(families) > 0
        for family_id, family in families.items():
            assert family.family_id
            assert family.plane in {Plane.EXECUTION, Plane.PLANNING, Plane.LEARNING}
            assert family.entry_key

    def test_entries_derive_activation_from_graph(self, tmp_path: Path) -> None:
        """Activation entry selection uses compiled graph entries, not hardwired maps."""
        engine = RuntimeEngine(tmp_path, stage_runner=_noop_stage_runner)
        engine.startup()
        assert engine.compiled_plan is not None

        from millrace_ai.contracts import WorkItemKind
        from millrace_ai.runtime.graph_authority import (
            work_item_activation_for_graph,
        )

        activation = work_item_activation_for_graph(
            engine.compiled_plan, WorkItemKind.TASK
        )
        assert activation.plane is Plane.EXECUTION
        assert activation.stage is not None
        assert activation.node_id
        assert activation.stage_kind_id

    def test_completion_entry_uses_graph_metadata(self, tmp_path: Path) -> None:
        """Completion activation reads from compiled graph completion entry."""
        engine = RuntimeEngine(tmp_path, stage_runner=_noop_stage_runner)
        engine.startup()
        assert engine.compiled_plan is not None

        from millrace_ai.runtime.graph_authority import (
            completion_activation_for_graph,
        )

        activation = completion_activation_for_graph(engine.compiled_plan)
        assert activation.plane is Plane.PLANNING
        assert activation.stage is not None
        assert activation.node_id
        assert activation.stage_kind_id

    def test_blueprint_prefix_provider_ids_do_not_load_implementations(self) -> None:
        from millrace_ai.runtime.context import providers

        registry = providers.RequestContextProviderRegistry()
        authority = SimpleNamespace(
            provider_id="blueprint.arbitrary",
            provider_python_registry_id="blueprint.arbitrary",
            render_plan_id="blueprint.arbitrary.default.v1",
        )
        _unload_blueprint_impl_modules()

        provider = providers._provider_for_authority(registry, authority)

        assert provider is None
        assert _loaded_blueprint_impl_modules() == []

    def test_manifest_owned_provider_rejects_cross_package_render_plan_ownership(self) -> None:
        from millrace_ai.runtime.context import providers

        registry = providers.RequestContextProviderRegistry()
        authority = SimpleNamespace(
            provider_id="blueprint.manager",
            provider_python_registry_id="blueprint.manager",
            render_plan_id="stage_request.default.v1",
        )
        _unload_blueprint_impl_modules()

        with pytest.raises(ValueError, match="owned by different extension packages"):
            providers._provider_for_authority(registry, authority)
        assert _loaded_blueprint_impl_modules() == []

    def test_manifest_owned_provider_requires_render_plan_manifest_ownership(self) -> None:
        from millrace_ai.runtime.context import providers

        registry = providers.RequestContextProviderRegistry()
        authority = SimpleNamespace(
            provider_id="blueprint.manager",
            provider_python_registry_id="blueprint.manager",
            render_plan_id="missing.render.v1",
        )
        _unload_blueprint_impl_modules()

        with pytest.raises(ValueError, match="partially selects extension ownership"):
            providers._provider_for_authority(registry, authority)
        assert _loaded_blueprint_impl_modules() == []

    def test_manifest_owned_provider_loads_without_blueprint_domain_special_case(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from millrace_ai.runtime.context import providers

        def external_provider(*args: object) -> object:
            return SimpleNamespace(provider_id="external.provider")

        module = SimpleNamespace(
            built_in_request_context_provider_registrations=lambda: (
                ("external.provider", external_provider),
            )
        )
        manifest = ExtensionPackageManifest(
            package_id="external.context",
            display_name="External Context",
            domain="learning",
            version="1.0.0",
            items=(
                ExtensionItemManifest(
                    item_kind=ExtensionItemKind.CONTEXT_PROVIDER,
                    item_id="external.provider",
                    implementation_path="external.context.providers",
                    version="1.0.0",
                ),
                ExtensionItemManifest(
                    item_kind=ExtensionItemKind.REQUEST_CONTEXT_RENDER_PLAN,
                    item_id="external.render.v1",
                    implementation_path="external.context.rendering",
                    version="1.0.0",
                ),
            ),
        )
        monkeypatch.setattr(
            providers,
            "discover_extension_package_manifests",
            lambda: (manifest,),
        )
        monkeypatch.setattr(
            providers,
            "import_module",
            lambda module_path: module
            if module_path == "external.context.providers"
            else pytest.fail(f"unexpected import {module_path!r}"),
        )
        registry = providers.RequestContextProviderRegistry()
        authority = SimpleNamespace(
            provider_id="external.provider",
            provider_python_registry_id="external.provider",
            render_plan_id="external.render.v1",
        )

        provider = providers._provider_for_authority(registry, authority)

        assert provider is external_provider

    def test_compiled_plan_absent_blueprint_context_does_not_load_implementation_modules(
        self, tmp_path: Path
    ) -> None:
        from millrace_ai.runtime.context import providers

        request = SimpleNamespace(
            request_id="request-absent-plan",
            compiled_plan_id="compiled-plan-missing",
            plane=Plane.PLANNING,
            node_id="manager_blueprint",
            stage_kind_id="manager_blueprint",
            request_context_profile_id="manager_blueprint.default",
            context_render_plan_id="blueprint.manager.default.v1",
        )
        _unload_blueprint_impl_modules()

        with pytest.raises(ValueError, match="compiled plan"):
            providers.build_request_context_plan(
                workspace_root=tmp_path,
                request=request,
                compiled_plan=None,
            )

        assert _loaded_blueprint_impl_modules() == []

    def test_blueprint_provider_loads_with_compiled_manifest_owned_metadata(
        self, tmp_path: Path
    ) -> None:
        from millrace_ai.runtime.context import providers
        from millrace_ai.runtime.context.models import RequestContextAuthority

        engine = RuntimeEngine(
            tmp_path,
            stage_runner=_noop_stage_runner,
            mode_id="blueprint_codex",
        )
        engine.startup()
        assert engine.compiled_plan is not None
        profile = engine.compiled_plan.request_context_profiles_by_id[
            "manager_blueprint.default"
        ]
        provider_definition = engine.compiled_plan.request_context_providers_by_id[
            profile.provider_id
        ]
        render_plan = engine.compiled_plan.request_context_render_plans_by_id[
            "blueprint.manager.default.v1"
        ]
        authority = RequestContextAuthority(
            profile_id=profile.profile_id,
            render_plan_id=render_plan.render_plan_id,
            provider_id=provider_definition.provider_id,
            provider_python_registry_id=provider_definition.python_registry_id,
            profile=profile,
            provider=provider_definition,
            render_plan=render_plan,
        )
        registry = providers.RequestContextProviderRegistry()
        _unload_blueprint_impl_modules()

        provider = providers._provider_for_authority(registry, authority)

        assert provider is not None
        assert any(
            module == "millrace_ai.extensions.builtin.blueprint.context"
            for module in _loaded_blueprint_impl_modules()
        )
        engine.close()
