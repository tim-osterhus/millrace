"""Tests for data-driven request context providers."""

from __future__ import annotations

from pathlib import Path

from millrace_ai.contracts import Plane
from millrace_ai.runtime.engine import RuntimeEngine


def _noop_stage_runner(request: object) -> object:
    raise AssertionError("stage runner should not be called")


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
