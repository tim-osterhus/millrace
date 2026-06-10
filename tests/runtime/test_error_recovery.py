"""Tests for data-driven runtime error recovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from millrace_ai.contracts import Plane
from millrace_ai.runtime.engine import RuntimeEngine


def _noop_stage_runner(request: object) -> object:
    raise AssertionError("stage runner should not be called")


class TestRepairRouteDataDriven:
    """Runtime repair routes are resolved from compiled plan metadata."""

    def test_execution_repair_route_from_compiled_plan(self, tmp_path: Path) -> None:
        engine = RuntimeEngine(tmp_path, stage_runner=_noop_stage_runner)
        engine.startup()
        assert engine.compiled_plan is not None

        from millrace_ai.runtime.recovery.repair_routes import (
            runtime_repair_route_for_plane,
        )

        route = runtime_repair_route_for_plane(engine, Plane.EXECUTION)
        assert route is not None
        assert route.node_id
        assert route.stage_kind_id
        # The stage must come from compiled graph metadata.
        assert route.stage is not None

    def test_planning_repair_route_from_compiled_plan(self, tmp_path: Path) -> None:
        engine = RuntimeEngine(tmp_path, stage_runner=_noop_stage_runner)
        engine.startup()
        assert engine.compiled_plan is not None

        from millrace_ai.runtime.recovery.repair_routes import (
            runtime_repair_route_for_plane,
        )

        route = runtime_repair_route_for_plane(engine, Plane.PLANNING)
        assert route is not None
        assert route.node_id
        assert route.stage_kind_id
        assert route.stage is not None

    def test_repair_counter_uses_compiled_counter_name(self, tmp_path: Path) -> None:
        engine = RuntimeEngine(tmp_path, stage_runner=_noop_stage_runner)
        engine.startup()
        assert engine.compiled_plan is not None

        from millrace_ai.runtime.recovery.repair_routes import (
            runtime_repair_route_for_plane,
        )

        route = runtime_repair_route_for_plane(engine, Plane.EXECUTION)
        assert route is not None
        if route.counter_name is not None:
            # Counter name is derived from compiled plan metadata, not hardcoded.
            assert isinstance(route.counter_name, str)
        if route.threshold is not None:
            assert route.threshold > 0

    def test_repair_attempts_not_exhausted_for_fresh_engine(
        self, tmp_path: Path
    ) -> None:
        engine = RuntimeEngine(tmp_path, stage_runner=_noop_stage_runner)
        engine.startup()
        assert engine.compiled_plan is not None

        from millrace_ai.runtime.recovery.repair_routes import (
            RuntimeRepairRoute,
            runtime_repair_attempts_exhausted,
        )

        route = RuntimeRepairRoute(
            node_id="troubleshooter",
            stage_kind_id="troubleshooter",
            stage="troubleshooter",
            counter_name="troubleshoot_attempt_count",
            threshold=3,
        )
        assert not runtime_repair_attempts_exhausted(engine, route)


class TestErrorContextDataDriven:
    """Runtime error context uses compiled plan metadata, not hardwired domain."""

    def test_pre_dispatch_failure_stage_from_compiled_plan(
        self, tmp_path: Path
    ) -> None:
        """Pre-dispatch failed stage resolution uses compiled plan entries."""
        engine = RuntimeEngine(tmp_path, stage_runner=_noop_stage_runner)
        engine.startup()
        assert engine.compiled_plan is not None

        from millrace_ai.runtime.error_recovery import _pre_dispatch_work_identity

        # When active run identity is present, it takes precedence.
        family_id, kind, item_id = _pre_dispatch_work_identity(
            engine,
            plane=Plane.EXECUTION,
            work_item_family_id="task",
            work_item_kind=None,
            work_item_id="task-001",
            closure_target_root_spec_id=None,
        )
        assert family_id == "task"
        assert item_id == "task-001"

    def test_closure_target_identity_preserved(self, tmp_path: Path) -> None:
        """Closure target root spec id takes precedence in pre-dispatch identity."""
        engine = RuntimeEngine(tmp_path, stage_runner=_noop_stage_runner)
        engine.startup()
        assert engine.compiled_plan is not None

        from millrace_ai.runtime.error_recovery import _pre_dispatch_work_identity

        family_id, kind, item_id = _pre_dispatch_work_identity(
            engine,
            plane=Plane.PLANNING,
            work_item_family_id=None,
            work_item_kind=None,
            work_item_id=None,
            closure_target_root_spec_id="spec-root-001",
        )
        assert family_id == "spec"
        assert item_id == "spec-root-001"

    def test_lane_based_identity_when_no_specific_work(self, tmp_path: Path) -> None:
        """When no specific work is known, lane identity is used."""
        engine = RuntimeEngine(tmp_path, stage_runner=_noop_stage_runner)
        engine.startup()
        assert engine.compiled_plan is not None

        from millrace_ai.runtime.error_recovery import _pre_dispatch_work_identity

        family_id, kind, item_id = _pre_dispatch_work_identity(
            engine,
            plane=Plane.EXECUTION,
            work_item_family_id=None,
            work_item_kind=None,
            work_item_id=None,
            closure_target_root_spec_id=None,
        )
        # Lane-based identity uses compiled plan lane id, not hardcoded "task".
        assert "execution" in family_id

    def test_context_stage_requires_compiled_plan(self) -> None:
        """Hardwired domain fallback is removed — compiled plan is required."""
        from millrace_ai.runtime.error_recovery import _context_stage_for_plane

        with pytest.raises(ValueError, match="compiled plan"):
            _context_stage_for_plane(Plane.EXECUTION)
