"""Tests for data-driven workspace state reconciliation."""

from __future__ import annotations

from pathlib import Path

import pytest

from millrace_ai.architecture import CompiledRunPlan
from millrace_ai.contracts import (
    ExecutionStageName,
    Plane,
    RecoveryCounters,
)
from millrace_ai.runtime.engine import RuntimeEngine
from millrace_ai.workspace.state_reconciliation import (
    collect_reconciliation_signals,
    normalize_execution_status_marker,
    normalize_learning_status_marker,
    normalize_planning_status_marker,
    running_status_marker_for_stage,
)


def _noop_stage_runner(request: object) -> object:
    raise AssertionError("stage runner should not be called")


@pytest.fixture
def compiled_plan(tmp_path: Path) -> CompiledRunPlan:
    engine = RuntimeEngine(tmp_path, stage_runner=_noop_stage_runner)
    engine.startup()
    assert engine.compiled_plan is not None
    return engine.compiled_plan


class TestNormalizeStatusMarkers:
    """Status marker normalisation honours compiled plan boundaries."""

    def test_execution_marker_normalisation(self) -> None:
        assert normalize_execution_status_marker("### IDLE") == "### IDLE"
        assert (
            normalize_execution_status_marker("### BUILDER_COMPLETE")
            == "### BUILDER_COMPLETE"
        )

    def test_planning_marker_normalisation(self) -> None:
        assert normalize_planning_status_marker("### IDLE") == "### IDLE"
        assert (
            normalize_planning_status_marker("### PLANNER_COMPLETE")
            == "### PLANNER_COMPLETE"
        )

    def test_learning_marker_normalisation(self) -> None:
        assert normalize_learning_status_marker("### IDLE") == "### IDLE"
        assert (
            normalize_learning_status_marker("### ANALYST_COMPLETE")
            == "### ANALYST_COMPLETE"
        )

    def test_running_marker_for_stage(self) -> None:
        marker = running_status_marker_for_stage(ExecutionStageName.BUILDER)
        assert marker == "### BUILDER_RUNNING"


class TestCollectReconciliationSignalsWithCompiledPlan:
    """Reconciliation signals are derived from compiled plan metadata."""

    def test_idle_snapshot_with_compiled_plan_produces_no_signals(
        self, tmp_path: Path
    ) -> None:
        engine = RuntimeEngine(tmp_path, stage_runner=_noop_stage_runner)
        engine.startup()
        assert engine.snapshot is not None
        assert engine.compiled_plan is not None

        signals = collect_reconciliation_signals(
            snapshot=engine.snapshot,
            counters=RecoveryCounters(entries=()),
            execution_status_marker="### IDLE",
            planning_status_marker="### IDLE",
            learning_status_marker="### IDLE",
            compiled_plan=engine.compiled_plan,
        )
        assert len(signals) == 0

    def test_stale_active_execution_detected_with_compiled_plan(
        self, tmp_path: Path
    ) -> None:
        engine = RuntimeEngine(tmp_path, stage_runner=_noop_stage_runner)
        engine.startup()
        assert engine.snapshot is not None
        assert engine.compiled_plan is not None

        stale_snapshot = engine.snapshot.model_copy(
            update={
                "process_running": False,
                "active_plane": Plane.EXECUTION,
                "active_stage": ExecutionStageName.BUILDER,
                "active_node_id": ExecutionStageName.BUILDER.value,
                "active_stage_kind_id": ExecutionStageName.BUILDER.value,
            }
        )
        signals = collect_reconciliation_signals(
            snapshot=stale_snapshot,
            counters=RecoveryCounters(entries=()),
            execution_status_marker="### BUILDER_RUNNING",
            planning_status_marker="### IDLE",
            learning_status_marker="### IDLE",
            compiled_plan=engine.compiled_plan,
        )
        stale_signal = next(
            (s for s in signals if s.code == "stale_active_ownership"), None
        )
        assert stale_signal is not None
        assert stale_signal.plane == Plane.EXECUTION
        assert stale_signal.failure_class == "stale_active_ownership"

    def test_impossible_execution_marker_with_compiled_plan(
        self, tmp_path: Path
    ) -> None:
        engine = RuntimeEngine(tmp_path, stage_runner=_noop_stage_runner)
        engine.startup()
        assert engine.snapshot is not None
        assert engine.compiled_plan is not None

        bad_snapshot = engine.snapshot.model_copy(
            update={
                "active_plane": Plane.EXECUTION,
                "active_stage": ExecutionStageName.BUILDER,
                "active_node_id": ExecutionStageName.BUILDER.value,
                "active_stage_kind_id": ExecutionStageName.BUILDER.value,
            }
        )
        signals = collect_reconciliation_signals(
            snapshot=bad_snapshot,
            counters=RecoveryCounters(entries=()),
            execution_status_marker="### PLANNER_COMPLETE",
            planning_status_marker="### IDLE",
            learning_status_marker="### IDLE",
            compiled_plan=engine.compiled_plan,
        )
        impossible = [s for s in signals if "impossible" in s.code]
        assert len(impossible) >= 1
        assert impossible[0].plane == Plane.EXECUTION

    def test_learning_marker_accepted_by_compiled_plan(
        self, tmp_path: Path
    ) -> None:
        """Learning plane status markers are validated through compiled plan."""
        from millrace_ai.contracts import LearningStageName

        engine = RuntimeEngine(tmp_path, stage_runner=_noop_stage_runner)
        engine.startup()
        assert engine.snapshot is not None
        assert engine.compiled_plan is not None

        # Use the compiled plan's running marker for the learning entry node.
        if engine.compiled_plan.learning_graph is None:
            pytest.skip("compiled plan has no learning graph")
        entry_node = engine.compiled_plan.learning_graph.compiled_entries[0]
        running_marker = f"### {entry_node.node_id.upper()}_RUNNING"

        learning_snapshot = engine.snapshot.model_copy(
            update={
                "active_plane": Plane.LEARNING,
                "active_stage": LearningStageName.ANALYST,
                "active_node_id": entry_node.node_id,
                "active_stage_kind_id": entry_node.node_id,
            }
        )
        signals = collect_reconciliation_signals(
            snapshot=learning_snapshot,
            counters=RecoveryCounters(entries=()),
            execution_status_marker="### IDLE",
            planning_status_marker="### IDLE",
            learning_status_marker=running_marker,
            compiled_plan=engine.compiled_plan,
        )
        impossible_learning = [
            s for s in signals if "impossible_learning" in s.code
        ]
        assert len(impossible_learning) == 0

    def test_recommended_stage_from_compiled_plan_recovery(
        self, tmp_path: Path
    ) -> None:
        """Recommended repair stage for stale ownership comes from compiled plan."""
        engine = RuntimeEngine(tmp_path, stage_runner=_noop_stage_runner)
        engine.startup()
        assert engine.snapshot is not None
        assert engine.compiled_plan is not None

        stale_snapshot = engine.snapshot.model_copy(
            update={
                "process_running": False,
                "active_plane": Plane.EXECUTION,
                "active_stage": ExecutionStageName.BUILDER,
                "active_node_id": ExecutionStageName.BUILDER.value,
                "active_stage_kind_id": ExecutionStageName.BUILDER.value,
                "active_work_item_family_id": "task",
                "active_work_item_id": "task-001",
                "active_work_item_kind": "task",
            }
        )
        signals = collect_reconciliation_signals(
            snapshot=stale_snapshot,
            counters=RecoveryCounters(entries=()),
            execution_status_marker="### BUILDER_RUNNING",
            planning_status_marker="### IDLE",
            learning_status_marker="### IDLE",
            compiled_plan=engine.compiled_plan,
        )
        stale = [s for s in signals if s.code == "stale_active_ownership"]
        assert len(stale) >= 1
        assert stale[0].recommended_stage is not None

    def test_allowed_markers_derived_from_compiled_plan(
        self, tmp_path: Path
    ) -> None:
        """Valid markers pass when checked against the compiled plan graph."""
        engine = RuntimeEngine(tmp_path, stage_runner=_noop_stage_runner)
        engine.startup()
        assert engine.snapshot is not None
        assert engine.compiled_plan is not None

        builder_snapshot = engine.snapshot.model_copy(
            update={
                "active_plane": Plane.EXECUTION,
                "active_stage": ExecutionStageName.BUILDER,
                "active_node_id": ExecutionStageName.BUILDER.value,
                "active_stage_kind_id": ExecutionStageName.BUILDER.value,
            }
        )
        signals = collect_reconciliation_signals(
            snapshot=builder_snapshot,
            counters=RecoveryCounters(entries=()),
            execution_status_marker="### BUILDER_COMPLETE",
            planning_status_marker="### IDLE",
            learning_status_marker="### IDLE",
            compiled_plan=engine.compiled_plan,
        )
        impossible = [s for s in signals if "impossible" in s.code]
        assert len(impossible) == 0
