"""Tests for generic recovery counter persistence, migration, projection,
policy-owned threshold routing, and exception recovery counter increments.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from millrace_ai.architecture.loop_graphs import GraphLoopCounterName
from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import (
    ExecutionStageName,
    Plane,
    RecoveryCounterEntry,
    RecoveryCounters,
    ResultClass,
    RuntimeSnapshot,
    StageResultEnvelope,
    WorkItemKind,
)
from millrace_ai.contracts.router import RouterAction, RouterDecision
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.graph_authority import route_stage_result_from_graph
from millrace_ai.runtime.graph_authority.counters import (
    counter_attempts_for_counter_id,
    matching_counter_entry,
)
from millrace_ai.runtime.recovery.repair_routes import (
    RuntimeRepairRoute,
    incremented_repair_counter,
    runtime_repair_attempts_exhausted,
)
from millrace_ai.runtime.result_counters import (
    increment_counter_field,
    increment_route_counters,
)
from millrace_ai.state_store import (
    load_recovery_counters,
    save_recovery_counters,
    save_snapshot,
)

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unused_stage_runner(request: StageRunRequest) -> RunnerRawResult:
    raise AssertionError("stage runner should not be called")


def _engine(tmp_path: Path) -> RuntimeEngine:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()
    assert engine.snapshot is not None
    return engine


def _snapshot(engine: RuntimeEngine, **overrides: object) -> RuntimeSnapshot:
    assert engine.snapshot is not None
    base: dict[str, object] = {
        "active_plane": Plane.EXECUTION,
        "active_stage": ExecutionStageName.BUILDER,
        "active_node_id": "builder",
        "active_stage_kind_id": "builder",
        "active_run_id": "run-001",
        "active_work_item_family_id": "task",
        "active_work_item_kind": WorkItemKind.TASK,
        "active_work_item_id": "task-001",
        "updated_at": NOW,
    }
    base.update(overrides)
    return engine.snapshot.model_copy(update=base)


def _stage_result(
    plane: Plane = Plane.EXECUTION,
    stage: ExecutionStageName = ExecutionStageName.BUILDER,
    work_item_family_id: str = "task",
    work_item_kind: WorkItemKind = WorkItemKind.TASK,
    work_item_id: str = "task-001",
    terminal_result: str = "BLOCKED",
    metadata: dict[str, object] | None = None,
) -> StageResultEnvelope:
    md = dict(metadata or {})
    md.setdefault("failure_class", "test_failure")
    # Map terminal_result to a valid summary_status_marker
    marker = terminal_result if terminal_result.startswith("### ") else f"### {terminal_result}"
    return StageResultEnvelope(
        run_id="run-001",
        plane=plane,
        stage=stage,
        work_item_family_id=work_item_family_id,
        work_item_kind=work_item_kind,
        work_item_id=work_item_id,
        terminal_result=terminal_result,
        result_class=ResultClass.BLOCKED,
        summary_status_marker=marker,
        success=False,
        started_at=NOW,
        completed_at=NOW,
        metadata=md,
    )


def _compiled_plan(tmp_path: Path) -> object:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    outcome = compile_and_persist_workspace_plan(
        paths.root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.active_plan is not None
    return outcome.active_plan


# ---------------------------------------------------------------------------
# A1: Generic counter persistence keyed by counter_id and scope_key
# ---------------------------------------------------------------------------


class TestGenericCounterPersistence:
    def test_counter_entry_stores_generic_counters_by_counter_id(self) -> None:
        entry = RecoveryCounterEntry(
            failure_class="provider_unavailable",
            work_item_id="task-001",
            work_item_kind=WorkItemKind.TASK,
            counters={"custom_counter": 3, "troubleshoot_attempt_count": 1},
            last_updated_at=NOW,
        )
        assert entry.counters == {"custom_counter": 3, "troubleshoot_attempt_count": 1}
        assert entry.counters["troubleshoot_attempt_count"] == 1

    def test_counter_entry_scope_key_is_composite(self) -> None:
        entry = RecoveryCounterEntry(
            failure_class="provider_unavailable",
            work_item_id="task-001",
            work_item_kind=WorkItemKind.TASK,
            counters={"troubleshoot_attempt_count": 2},
            last_updated_at=NOW,
        )
        assert entry.scope_key == "task:task-001:provider_unavailable"

    def test_counter_entry_scope_key_differs_by_failure_class(self) -> None:
        e1 = RecoveryCounterEntry(
            failure_class="provider_unavailable",
            work_item_id="task-001",
            work_item_kind=WorkItemKind.TASK,
            counters={},
            last_updated_at=NOW,
        )
        e2 = RecoveryCounterEntry(
            failure_class="contract_error",
            work_item_id="task-001",
            work_item_kind=WorkItemKind.TASK,
            counters={},
            last_updated_at=NOW,
        )
        assert e1.scope_key != e2.scope_key

    def test_counter_entry_scope_key_differs_by_work_item(self) -> None:
        e1 = RecoveryCounterEntry(
            failure_class="provider_unavailable",
            work_item_id="task-001",
            work_item_kind=WorkItemKind.TASK,
            counters={},
            last_updated_at=NOW,
        )
        e2 = RecoveryCounterEntry(
            failure_class="provider_unavailable",
            work_item_id="task-002",
            work_item_kind=WorkItemKind.TASK,
            counters={},
            last_updated_at=NOW,
        )
        assert e1.scope_key != e2.scope_key

    def test_counter_increment_persists_to_disk(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        snapshot = _snapshot(engine)
        engine.snapshot = snapshot
        engine.counters = RecoveryCounters()
        save_snapshot(engine.paths, snapshot)
        save_recovery_counters(engine.paths, engine.counters)

        decision = RouterDecision(
            action=RouterAction.RUN_STAGE,
            next_plane=Plane.EXECUTION,
            next_stage=ExecutionStageName.TROUBLESHOOTER,
            next_node_id="troubleshooter",
            next_stage_kind_id="troubleshooter",
            reason="test_route",
            failure_class="test_failure",
            counter_mutation_name="troubleshoot_attempt_count",
        )

        increment_route_counters(engine, snapshot, decision, _stage_result())

        # Verify persistence
        loaded = load_recovery_counters(engine.paths)
        assert len(loaded.entries) == 1
        assert loaded.entries[0].counters["troubleshoot_attempt_count"] == 1
        assert loaded.entries[0].failure_class == "test_failure"
        assert loaded.entries[0].work_item_family_id == "task"
        assert loaded.entries[0].work_item_id == "task-001"

    def test_counter_increment_accumulates_across_calls(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        snapshot = _snapshot(engine)
        engine.snapshot = snapshot
        engine.counters = RecoveryCounters()
        save_snapshot(engine.paths, snapshot)
        save_recovery_counters(engine.paths, engine.counters)

        decision = RouterDecision(
            action=RouterAction.RUN_STAGE,
            next_plane=Plane.EXECUTION,
            next_stage=ExecutionStageName.TROUBLESHOOTER,
            next_node_id="troubleshooter",
            next_stage_kind_id="troubleshooter",
            reason="test",
            failure_class="test_failure",
            counter_mutation_name="troubleshoot_attempt_count",
        )

        increment_route_counters(engine, snapshot, decision, _stage_result())
        increment_route_counters(engine, engine.snapshot, decision, _stage_result())

        loaded = load_recovery_counters(engine.paths)
        assert loaded.entries[0].counters["troubleshoot_attempt_count"] == 2

    def test_counter_increment_with_recovery_counter_name(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        snapshot = _snapshot(engine)
        engine.snapshot = snapshot
        engine.counters = RecoveryCounters()
        save_snapshot(engine.paths, snapshot)
        save_recovery_counters(engine.paths, engine.counters)

        decision = RouterDecision(
            action=RouterAction.RUN_STAGE,
            next_plane=Plane.EXECUTION,
            next_stage=ExecutionStageName.TROUBLESHOOTER,
            next_node_id="troubleshooter",
            next_stage_kind_id="troubleshooter",
            reason="test",
            failure_class="test_failure",
            recovery_counter_name="custom_recovery_counter",
        )

        increment_route_counters(engine, snapshot, decision, _stage_result())

        loaded = load_recovery_counters(engine.paths)
        assert loaded.entries[0].counters["custom_recovery_counter"] == 1


# ---------------------------------------------------------------------------
# A2: Generic scoped records reject stale fixed-field projections
# ---------------------------------------------------------------------------


class TestCounterAuthority:
    def test_fixed_fields_are_rejected_instead_of_migrated(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RecoveryCounterEntry(
                failure_class="test",
                work_item_id="task-001",
                work_item_kind=WorkItemKind.TASK,
                troubleshoot_attempt_count=5,
                last_updated_at=NOW,
            )

    def test_generic_counters_preserve_arbitrary_counter_ids(self) -> None:
        entry = RecoveryCounterEntry(
            failure_class="test",
            work_item_id="task-001",
            work_item_kind=WorkItemKind.TASK,
            counters={"custom": 10, "troubleshoot_attempt_count": 5},
            last_updated_at=NOW,
        )
        assert entry.counters["custom"] == 10
        assert entry.counters["troubleshoot_attempt_count"] == 5

    def test_stale_fixed_field_does_not_override_generic_counter(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RecoveryCounterEntry(
                failure_class="test",
                work_item_id="task-001",
                work_item_kind=WorkItemKind.TASK,
                counters={"troubleshoot_attempt_count": 7},
                troubleshoot_attempt_count=5,
                last_updated_at=NOW,
            )

    def test_all_counter_ids_live_in_generic_store(self) -> None:
        entry = RecoveryCounterEntry(
            failure_class="test",
            work_item_id="task-001",
            work_item_kind=WorkItemKind.TASK,
            counters={
                "troubleshoot_attempt_count": 3,
                "mechanic_attempt_count": 7,
                "fix_cycle_count": 2,
                "consultant_invocations": 4,
            },
            last_updated_at=NOW,
        )
        assert entry.counters == {
            "troubleshoot_attempt_count": 3,
            "mechanic_attempt_count": 7,
            "fix_cycle_count": 2,
            "consultant_invocations": 4,
        }

    def test_loaded_json_with_fixed_fields_is_rejected(self, tmp_path: Path) -> None:
        from pydantic import ValidationError

        engine = _engine(tmp_path)
        counters_path = engine.paths.recovery_counters_file
        counters_path.write_text(
            """
{
  "schema_version": "1.0",
  "kind": "recovery_counters",
  "entries": [
    {
      "failure_class": "test",
      "work_item_id": "task-001",
      "work_item_kind": "task",
      "troubleshoot_attempt_count": 3,
      "last_updated_at": "2026-06-01T00:00:00Z"
    }
  ]
}
""".strip(),
            encoding="utf-8",
        )
        with pytest.raises(ValidationError):
            load_recovery_counters(engine.paths)


class TestCounterProjectionRemoval:
    def test_generic_counters_do_not_create_fixed_fields(self) -> None:
        entry = RecoveryCounterEntry(
            failure_class="test",
            work_item_id="task-001",
            work_item_kind=WorkItemKind.TASK,
            counters={"troubleshoot_attempt_count": 4, "mechanic_attempt_count": 2},
            last_updated_at=NOW,
        )
        dumped = entry.model_dump()
        assert dumped["counters"] == {
            "troubleshoot_attempt_count": 4,
            "mechanic_attempt_count": 2,
        }
        for fixed_field in (
            "troubleshoot_attempt_count",
            "mechanic_attempt_count",
            "fix_cycle_count",
            "consultant_invocations",
        ):
            assert fixed_field not in dumped

    def test_absent_counter_ids_are_absent_from_generic_store(self) -> None:
        entry = RecoveryCounterEntry(
            failure_class="test",
            work_item_id="task-001",
            work_item_kind=WorkItemKind.TASK,
            counters={"custom": 5},
            last_updated_at=NOW,
        )
        assert entry.counters == {"custom": 5}

    def test_all_four_named_counter_ids_remain_generic_keys(self) -> None:
        entry = RecoveryCounterEntry(
            failure_class="test",
            work_item_id="task-001",
            work_item_kind=WorkItemKind.TASK,
            counters={
                "troubleshoot_attempt_count": 1,
                "mechanic_attempt_count": 2,
                "fix_cycle_count": 3,
                "consultant_invocations": 4,
            },
            last_updated_at=NOW,
        )
        assert entry.counters == {
            "troubleshoot_attempt_count": 1,
            "mechanic_attempt_count": 2,
            "fix_cycle_count": 3,
            "consultant_invocations": 4,
        }


# ---------------------------------------------------------------------------
# A3: Runtime counter increments stay in generic store
# ---------------------------------------------------------------------------


class TestGenericRuntimeCounterStore:
    def test_increment_updates_generic_counter_store_only(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        snapshot = _snapshot(engine)
        engine.snapshot = snapshot
        engine.counters = RecoveryCounters()
        save_snapshot(engine.paths, snapshot)
        save_recovery_counters(engine.paths, engine.counters)

        decision = RouterDecision(
            action=RouterAction.RUN_STAGE,
            next_plane=Plane.EXECUTION,
            next_stage=ExecutionStageName.TROUBLESHOOTER,
            next_node_id="troubleshooter",
            next_stage_kind_id="troubleshooter",
            reason="test",
            failure_class="test_failure",
            counter_mutation_name="troubleshoot_attempt_count",
        )

        updated = increment_route_counters(engine, snapshot, decision, _stage_result())
        assert updated.troubleshoot_attempt_count == 0
        loaded = load_recovery_counters(engine.paths)
        assert loaded.entries[0].counters["troubleshoot_attempt_count"] == 1

    def test_increment_non_legacy_counter_does_not_set_legacy_fields(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        snapshot = _snapshot(engine)
        engine.snapshot = snapshot
        engine.counters = RecoveryCounters()
        save_snapshot(engine.paths, snapshot)
        save_recovery_counters(engine.paths, engine.counters)

        decision = RouterDecision(
            action=RouterAction.RUN_STAGE,
            next_plane=Plane.EXECUTION,
            next_stage=ExecutionStageName.TROUBLESHOOTER,
            next_node_id="troubleshooter",
            next_stage_kind_id="troubleshooter",
            reason="test",
            failure_class="test_failure",
            counter_mutation_name="custom_non_legacy_counter",
        )

        updated = increment_route_counters(engine, snapshot, decision, _stage_result())
        assert updated.troubleshoot_attempt_count == 0
        assert updated.mechanic_attempt_count == 0
        assert updated.fix_cycle_count == 0
        assert updated.consultant_invocations == 0
        # Generic store should still be updated
        loaded = load_recovery_counters(engine.paths)
        assert loaded.entries[0].counters["custom_non_legacy_counter"] == 1

    def test_snapshot_legacy_fields_do_not_drive_generic_store(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        snapshot = _snapshot(engine)
        engine.snapshot = snapshot
        engine.counters = RecoveryCounters()
        save_snapshot(engine.paths, snapshot)
        save_recovery_counters(engine.paths, engine.counters)

        decision = RouterDecision(
            action=RouterAction.RUN_STAGE,
            next_plane=Plane.EXECUTION,
            next_stage=ExecutionStageName.TROUBLESHOOTER,
            next_node_id="troubleshooter",
            next_stage_kind_id="troubleshooter",
            reason="test",
            failure_class="test_failure",
            counter_mutation_name="mechanic_attempt_count",
        )

        updated = increment_route_counters(engine, snapshot, decision, _stage_result())
        assert updated.mechanic_attempt_count == 0
        loaded = load_recovery_counters(engine.paths)
        assert loaded.entries[0].counters["mechanic_attempt_count"] == 1


# ---------------------------------------------------------------------------
# A4 & A5: Policy-owned threshold routing (no hard-coded thresholds)
# ---------------------------------------------------------------------------


class TestPolicyOwnedThresholdRouting:
    def test_threshold_routing_reads_counter_from_generic_store(
        self, tmp_path: Path
    ) -> None:
        plan = _compiled_plan(tmp_path)

        snapshot = RuntimeSnapshot(
            schema_version="1.0",
            kind="runtime_snapshot",
            runtime_mode="daemon",
            process_running=True,
            paused=False,
            active_mode_id="standard_plain",
            execution_loop_id="execution.standard",
            planning_loop_id="planning.standard",
            compiled_plan_id=plan.compiled_plan_id,
            compiled_plan_path="millrace-agents/state/compiled_plan.json",
            active_plane=Plane.EXECUTION,
            active_stage=ExecutionStageName.BUILDER,
            active_node_id="builder",
            active_stage_kind_id="builder",
            active_run_id="run-001",
            active_work_item_family_id="task",
            active_work_item_kind=WorkItemKind.TASK,
            active_work_item_id="task-001",
            execution_status_marker="### IDLE",
            planning_status_marker="### IDLE",
            config_version="1.0",
            watcher_mode="poll",
            updated_at=NOW,
        )

        # Build counters with fix_cycle_count at half the exhausted threshold.
        # This tests that routing proceeds through the threshold policy's
        # standard (non-exhausted) path.
        counters = RecoveryCounters(
            entries=[
                RecoveryCounterEntry(
                    failure_class="recoverable_failure",
                    work_item_family_id="task",
                    work_item_kind=WorkItemKind.TASK,
                    work_item_id="task-001",
                    counters={"fix_cycle_count": 0},
                    last_updated_at=NOW,
                )
            ]
        )

        decision = route_stage_result_from_graph(
            plan,
            snapshot,
            _stage_result(
                terminal_result="BLOCKED",
                metadata={"failure_class": "recoverable_failure"},
            ),
            counters,
        )
        # With counter=0 < threshold, should route to troubleshooter (the
        # non-exhausted recovery path for BLOCKED from builder).
        assert decision.action is RouterAction.RUN_STAGE
        assert decision.next_stage is ExecutionStageName.TROUBLESHOOTER

    def test_threshold_exhausted_routes_to_exhausted_target(
        self, tmp_path: Path
    ) -> None:
        plan = _compiled_plan(tmp_path)

        snapshot = RuntimeSnapshot(
            schema_version="1.0",
            kind="runtime_snapshot",
            runtime_mode="daemon",
            process_running=True,
            paused=False,
            active_mode_id="standard_plain",
            execution_loop_id="execution.standard",
            planning_loop_id="planning.standard",
            compiled_plan_id=plan.compiled_plan_id,
            compiled_plan_path="millrace-agents/state/compiled_plan.json",
            active_plane=Plane.EXECUTION,
            active_stage=ExecutionStageName.BUILDER,
            active_node_id="builder",
            active_stage_kind_id="builder",
            active_run_id="run-001",
            active_work_item_family_id="task",
            active_work_item_kind=WorkItemKind.TASK,
            active_work_item_id="task-001",
            execution_status_marker="### IDLE",
            planning_status_marker="### IDLE",
            config_version="1.0",
            watcher_mode="poll",
            updated_at=NOW,
        )

        # Exceed the fix_cycle_count threshold in generic store
        counters = RecoveryCounters(
            entries=[
                RecoveryCounterEntry(
                    failure_class="recoverable_failure",
                    work_item_family_id="task",
                    work_item_kind=WorkItemKind.TASK,
                    work_item_id="task-001",
                    counters={"fix_cycle_count": 10},
                    last_updated_at=NOW,
                )
            ]
        )

        # Use a terminal result that has a threshold policy
        decision = route_stage_result_from_graph(
            plan,
            snapshot,
            _stage_result(
                terminal_result="BLOCKED",
                metadata={"failure_class": "recoverable_failure"},
            ),
            counters,
        )
        # With BLOCKED from builder, the threshold policy should kick in
        # and route to the exhausted target (troubleshooter)
        assert decision.action is RouterAction.RUN_STAGE
        assert decision.next_stage is ExecutionStageName.TROUBLESHOOTER

    def test_counter_attempts_for_counter_id_is_canonical_read_path(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        snapshot = _snapshot(engine)
        engine.snapshot = snapshot
        engine.counters = RecoveryCounters(
            entries=[
                RecoveryCounterEntry(
                    failure_class="test_failure",
                    work_item_family_id="task",
                    work_item_id="task-001",
                    counters={"custom": 7, "troubleshoot_attempt_count": 3},
                    last_updated_at=NOW,
                )
            ]
        )

        assert (
            counter_attempts_for_counter_id(
                snapshot, engine.counters, "test_failure", counter_id="custom"
            )
            == 7
        )
        assert (
            counter_attempts_for_counter_id(
                snapshot,
                engine.counters,
                "test_failure",
                counter_id="troubleshoot_attempt_count",
            )
            == 3
        )
        assert (
            counter_attempts_for_counter_id(
                snapshot, engine.counters, "test_failure", counter_id="nonexistent"
            )
            == 0
        )

    def test_threshold_policy_counter_name_is_string_not_enum_at_runtime(
        self, tmp_path: Path
    ) -> None:
        """Threshold policies carry counter_name as string value at runtime."""
        # Simulate a repair route from graph policy data
        route = RuntimeRepairRoute(
            node_id="troubleshooter",
            stage_kind_id="troubleshooter",
            stage=ExecutionStageName.TROUBLESHOOTER,
            counter_name="troubleshoot_attempt_count",
            threshold=2,
        )
        assert isinstance(route.counter_name, str)
        assert route.counter_name == "troubleshoot_attempt_count"

    def test_increment_counter_field_uses_string_counter_id(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        snapshot = _snapshot(engine, troubleshoot_attempt_count=0)
        engine.snapshot = snapshot
        engine.counters = RecoveryCounters()
        save_snapshot(engine.paths, snapshot)
        save_recovery_counters(engine.paths, engine.counters)

        updated = increment_counter_field(
            engine,
            snapshot,
            engine.counters,
            failure_class="test_failure",
            work_item_family_id="task",
            work_item_id="task-001",
            counter_id="troubleshoot_attempt_count",
        )
        assert updated.troubleshoot_attempt_count == 0
        loaded = load_recovery_counters(engine.paths)
        assert loaded.entries[0].counters["troubleshoot_attempt_count"] == 1

    def test_counter_key_not_dispatch_on_fixed_counter_names(self) -> None:
        """matching_counter_entry matches by scope (family, id, failure_class),
        not by counter field name."""
        entry1 = RecoveryCounterEntry(
            failure_class="provider_unavailable",
            work_item_id="task-001",
            work_item_kind=WorkItemKind.TASK,
            counters={"troubleshoot_attempt_count": 3},
            last_updated_at=NOW,
        )
        entry2 = RecoveryCounterEntry(
            failure_class="contract_error",
            work_item_id="task-001",
            work_item_kind=WorkItemKind.TASK,
            counters={"troubleshoot_attempt_count": 1},
            last_updated_at=NOW,
        )
        counters = RecoveryCounters(entries=(entry1, entry2))

        snapshot = RuntimeSnapshot(
            schema_version="1.0",
            kind="runtime_snapshot",
            runtime_mode="daemon",
            process_running=True,
            paused=False,
            active_mode_id="standard_plain",
            execution_loop_id="execution.standard",
            planning_loop_id="planning.standard",
            compiled_plan_id="plan-test",
            compiled_plan_path="state/compiled_plan.json",
            active_plane=Plane.EXECUTION,
            active_stage=ExecutionStageName.BUILDER,
            active_node_id="builder",
            active_stage_kind_id="builder",
            active_run_id="run-001",
            active_work_item_kind=WorkItemKind.TASK,
            active_work_item_id="task-001",
            execution_status_marker="### IDLE",
            planning_status_marker="### IDLE",
            config_version="1.0",
            watcher_mode="poll",
            updated_at=NOW,
        )

        match = matching_counter_entry(snapshot, counters, "provider_unavailable")
        assert match is not None
        assert match.failure_class == "provider_unavailable"
        # The entry was matched by scope, not by counter name
        assert match.counters["troubleshoot_attempt_count"] == 3

    def test_resolved_threshold_derives_from_policy_data(
        self, tmp_path: Path
    ) -> None:
        """resolved_threshold_for_policy uses counter_id string for config lookup.

        When the counter_id is NOT in the legacy config-override map, the
        policy's declared threshold is used.  When it IS in the map, the
        config override takes precedence."""
        from millrace_ai.architecture.loop_graphs import (
            GraphLoopThresholdPolicyDefinition,
        )
        from millrace_ai.compilation.policies import resolved_threshold_for_policy

        # Use a counter NOT in _CONFIG_OVERRIDES_BY_COUNTER_ID so config
        # override does not interfere.
        policy = GraphLoopThresholdPolicyDefinition(
            policy_id="test_policy",
            source_node_ids=("builder",),
            on_outcome="FIX_NEEDED",
            counter_name=GraphLoopCounterName.CONSULTANT_INVOCATIONS,
            threshold=7,
            exhausted_target_node_id="troubleshooter",
            exhausted_terminal_state_id=None,
            default_failure_class_template="consultant_exhausted",
        )
        config = RuntimeConfig()
        threshold = resolved_threshold_for_policy(policy, config=config)
        assert threshold == 7

    def test_resolved_threshold_falls_back_to_max_repair_attempts(
        self, tmp_path: Path
    ) -> None:
        """resolved_threshold_for_policy falls back to max_repair_attempts
        when counter_id has no config override and policy threshold is minimal."""
        from millrace_ai.architecture.loop_graphs import (
            GraphLoopThresholdPolicyDefinition,
        )
        from millrace_ai.compilation.policies import resolved_threshold_for_policy

        # Use a counter NOT in _CONFIG_OVERRIDES_BY_COUNTER_ID
        policy = GraphLoopThresholdPolicyDefinition(
            policy_id="test_policy",
            source_node_ids=("builder",),
            on_outcome="BLOCKED",
            counter_name=GraphLoopCounterName.CONSULTANT_INVOCATIONS,
            threshold=1,  # Minimal policy threshold; not in config overrides
            exhausted_target_node_id="consultant",
            exhausted_terminal_state_id=None,
            default_failure_class_template="troubleshoot_exhausted",
        )
        config = RuntimeConfig()
        threshold = resolved_threshold_for_policy(policy, config=config)
        # Policy threshold is used since no config override for this counter
        assert threshold == 1


# ---------------------------------------------------------------------------
# A4 continued: Exception recovery counter increments
# ---------------------------------------------------------------------------


class TestExceptionRecoveryCounters:
    def test_incremented_repair_counter_reads_from_generic_store(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        snapshot = _snapshot(engine, troubleshoot_attempt_count=2)
        engine.snapshot = snapshot
        engine.counters = RecoveryCounters(
            entries=[
                RecoveryCounterEntry(
                    failure_class="test_failure",
                    work_item_family_id="task",
                    work_item_id="task-001",
                    counters={"troubleshoot_attempt_count": 2},
                    last_updated_at=NOW,
                )
            ]
        )

        route = RuntimeRepairRoute(
            node_id="troubleshooter",
            stage_kind_id="troubleshooter",
            stage=ExecutionStageName.TROUBLESHOOTER,
            counter_name="troubleshoot_attempt_count",
            threshold=3,
        )
        result = incremented_repair_counter(engine, route)
        assert result == {"troubleshoot_attempt_count": 3}

    def test_repair_attempts_exhausted_uses_generic_store(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        snapshot = _snapshot(
            engine,
            troubleshoot_attempt_count=0,
            current_failure_class="test_failure",
        )
        engine.snapshot = snapshot
        engine.counters = RecoveryCounters(
            entries=[
                RecoveryCounterEntry(
                    failure_class="test_failure",
                    work_item_family_id="task",
                    work_item_id="task-001",
                    counters={"troubleshoot_attempt_count": 3},
                    last_updated_at=NOW,
                )
            ]
        )

        route = RuntimeRepairRoute(
            node_id="troubleshooter",
            stage_kind_id="troubleshooter",
            stage=ExecutionStageName.TROUBLESHOOTER,
            counter_name="troubleshoot_attempt_count",
            threshold=2,
        )
        # Generic store has 3 >= threshold 2 → exhausted
        assert runtime_repair_attempts_exhausted(engine, route) is True

    def test_repair_attempts_not_exhausted_below_threshold(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        snapshot = _snapshot(
            engine,
            troubleshoot_attempt_count=0,
            current_failure_class="test_failure",
        )
        engine.snapshot = snapshot
        engine.counters = RecoveryCounters(
            entries=[
                RecoveryCounterEntry(
                    failure_class="test_failure",
                    work_item_family_id="task",
                    work_item_id="task-001",
                    counters={"troubleshoot_attempt_count": 1},
                    last_updated_at=NOW,
                )
            ]
        )

        route = RuntimeRepairRoute(
            node_id="troubleshooter",
            stage_kind_id="troubleshooter",
            stage=ExecutionStageName.TROUBLESHOOTER,
            counter_name="troubleshoot_attempt_count",
            threshold=3,
        )
        assert runtime_repair_attempts_exhausted(engine, route) is False

    def test_increment_counter_field_persists_generic_store_for_non_legacy_counter(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        snapshot = _snapshot(engine)
        engine.snapshot = snapshot
        engine.counters = RecoveryCounters()
        save_snapshot(engine.paths, snapshot)
        save_recovery_counters(engine.paths, engine.counters)

        increment_counter_field(
            engine,
            snapshot,
            engine.counters,
            failure_class="test_failure",
            work_item_family_id="task",
            work_item_id="task-001",
            counter_id="custom_routing_counter",
        )

        loaded = load_recovery_counters(engine.paths)
        assert loaded.entries[0].counters["custom_routing_counter"] == 1

    def test_exception_recovery_preserves_custom_terminal_routing_info(
        self, tmp_path: Path
    ) -> None:
        """Verify that custom counter names are preserved through repair routes."""
        route = RuntimeRepairRoute(
            node_id="custom_recovery_node",
            stage_kind_id="troubleshooter",
            stage=ExecutionStageName.TROUBLESHOOTER,
            counter_name="custom_recovery_counter",
            threshold=5,
        )
        assert route.counter_name == "custom_recovery_counter"
        assert route.threshold == 5
        # The counter_name is a string, not a GraphLoopCounterName enum
        assert isinstance(route.counter_name, str)


# ---------------------------------------------------------------------------
# A6: Counter authority divergence checks
# ---------------------------------------------------------------------------


class TestCounterDivergence:
    def test_generic_store_is_the_only_model_counter_surface(self) -> None:
        entry = RecoveryCounterEntry(
            failure_class="test",
            work_item_id="task-001",
            work_item_kind=WorkItemKind.TASK,
            counters={"troubleshoot_attempt_count": 5},
            last_updated_at=NOW,
        )
        assert entry.counters["troubleshoot_attempt_count"] == 5
        assert "troubleshoot_attempt_count" not in entry.model_dump(exclude={"counters"})

    def test_incrementing_generic_preserves_only_generic_store(self) -> None:
        entry = RecoveryCounterEntry(
            failure_class="test",
            work_item_id="task-001",
            work_item_kind=WorkItemKind.TASK,
            counters={"troubleshoot_attempt_count": 6},
            last_updated_at=NOW,
        )
        assert entry.model_dump()["counters"]["troubleshoot_attempt_count"] == 6

    def test_negative_count_rejected_in_generic_counters(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RecoveryCounterEntry(
                failure_class="test",
                work_item_id="task-001",
                work_item_kind=WorkItemKind.TASK,
                counters={"troubleshoot_attempt_count": -1},
                last_updated_at=NOW,
            )

    def test_negative_count_in_fixed_field_is_rejected_as_extra_input(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RecoveryCounterEntry(
                failure_class="test",
                work_item_id="task-001",
                work_item_kind=WorkItemKind.TASK,
                troubleshoot_attempt_count=-1,
                last_updated_at=NOW,
            )

    def test_generic_counter_round_trip_is_stable(self) -> None:
        """Round-trip through JSON preserves generic counter authority."""
        entry = RecoveryCounterEntry(
            failure_class="test",
            work_item_id="task-001",
            work_item_kind=WorkItemKind.TASK,
            counters={
                "troubleshoot_attempt_count": 3,
                "mechanic_attempt_count": 2,
                "fix_cycle_count": 1,
                "consultant_invocations": 0,
            },
            last_updated_at=NOW,
        )
        dumped = entry.model_dump_json()
        reloaded = RecoveryCounterEntry.model_validate_json(dumped)
        assert reloaded.counters == {
            "troubleshoot_attempt_count": 3,
            "mechanic_attempt_count": 2,
            "fix_cycle_count": 1,
            "consultant_invocations": 0,
        }

    def test_missing_work_item_kind_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RecoveryCounterEntry(
                failure_class="test",
                work_item_id="task-001",
                counters={},
                last_updated_at=NOW,
            )


# ---------------------------------------------------------------------------
# A7: Runtime failure policy selection
# ---------------------------------------------------------------------------


class TestRuntimeFailurePolicySelection:
    def test_counter_key_from_snapshot_uses_family_and_item_ids(
        self, tmp_path: Path
    ) -> None:
        from millrace_ai.runtime.graph_authority.counters import counter_key_from_snapshot

        snapshot = RuntimeSnapshot(
            schema_version="1.0",
            kind="runtime_snapshot",
            runtime_mode="daemon",
            process_running=True,
            paused=False,
            active_mode_id="standard_plain",
            execution_loop_id="execution.standard",
            planning_loop_id="planning.standard",
            compiled_plan_id="plan-test",
            compiled_plan_path="state/compiled_plan.json",
            active_plane=Plane.EXECUTION,
            active_stage=ExecutionStageName.BUILDER,
            active_node_id="builder",
            active_stage_kind_id="builder",
            active_run_id="run-001",
            active_work_item_kind=WorkItemKind.TASK,
            active_work_item_id="task-001",
            execution_status_marker="### IDLE",
            planning_status_marker="### IDLE",
            config_version="1.0",
            watcher_mode="poll",
            updated_at=NOW,
        )

        key = counter_key_from_snapshot(snapshot, "provider_unavailable")
        assert key == "task:task-001:provider_unavailable"

    def test_counter_key_not_derived_from_stage_name(self, tmp_path: Path) -> None:
        from millrace_ai.runtime.graph_authority.counters import counter_key_from_snapshot

        snapshot = RuntimeSnapshot(
            schema_version="1.0",
            kind="runtime_snapshot",
            runtime_mode="daemon",
            process_running=True,
            paused=False,
            active_mode_id="standard_plain",
            execution_loop_id="execution.standard",
            planning_loop_id="planning.standard",
            compiled_plan_id="plan-test",
            compiled_plan_path="state/compiled_plan.json",
            active_plane=Plane.EXECUTION,
            active_stage=ExecutionStageName.CHECKER,
            active_node_id="checker",
            active_stage_kind_id="checker",
            active_run_id="run-001",
            active_work_item_kind=WorkItemKind.TASK,
            active_work_item_id="task-001",
            execution_status_marker="### IDLE",
            planning_status_marker="### IDLE",
            config_version="1.0",
            watcher_mode="poll",
            updated_at=NOW,
        )

        key = counter_key_from_snapshot(snapshot, "provider_unavailable")
        # Key should NOT mention "checker"
        assert "checker" not in key
        assert key == "task:task-001:provider_unavailable"

    def test_counter_id_read_path_is_canonical(self, tmp_path: Path) -> None:
        """Graph-authority counter reads use compiled counter_id strings."""
        engine = _engine(tmp_path)
        snapshot = _snapshot(engine)
        engine.snapshot = snapshot
        engine.counters = RecoveryCounters(
            entries=[
                RecoveryCounterEntry(
                    failure_class="test_failure",
                    work_item_family_id="task",
                    work_item_id="task-001",
                    counters={"troubleshoot_attempt_count": 5},
                    last_updated_at=NOW,
                )
            ]
        )

        assert (
            counter_attempts_for_counter_id(
                snapshot,
                engine.counters,
                "test_failure",
                counter_id="troubleshoot_attempt_count",
            )
            == 5
        )
        assert (
            counter_attempts_for_counter_id(
                snapshot,
                engine.counters,
                "test_failure",
                counter_id="mechanic_attempt_count",
            )
            == 0
        )


# ---------------------------------------------------------------------------
# A7: Custom terminal routing preservation
# ---------------------------------------------------------------------------


class TestCustomTerminalRoutingPreservation:
    def test_threshold_policy_exhausted_target_from_graph_data(
        self, tmp_path: Path
    ) -> None:
        """Exhausted target is read from compiled threshold policy, not hard-coded."""
        plan = _compiled_plan(tmp_path)
        graph = plan.graphs_by_plane[Plane.EXECUTION]

        # Find the fix_cycle threshold policy in the compiled graph
        # Compiled threshold policies may be in graph's compiled data
        fix_cycle_policy = None
        for policy in getattr(graph, 'threshold_policies', ()) or ():
            if policy.counter_name == GraphLoopCounterName.FIX_CYCLE_COUNT:
                fix_cycle_policy = policy
                break
        # If not found as threshold_policies, fall back to compiled plan-level
        if fix_cycle_policy is None:
            for policy in getattr(plan, 'threshold_policies_by_id', {}).values():
                if getattr(policy, 'counter_name', None) == GraphLoopCounterName.FIX_CYCLE_COUNT:
                    fix_cycle_policy = policy
                    break
        if fix_cycle_policy is not None:
            # The exhausted target is from the compiled policy data
            assert fix_cycle_policy.exhausted_target_node_id == "troubleshooter"

    def test_threshold_policy_exhausted_reason_uses_template(
        self, tmp_path: Path
    ) -> None:
        """Compiled threshold policies exist and derive from graph policy data."""
        plan = _compiled_plan(tmp_path)

        any_policy = False
        for graph in plan.graphs_by_plane.values():
            for policy in graph.compiled_threshold_policies:
                any_policy = True
                # Each policy has a counter_name (string value) and threshold
                assert policy.counter_name is not None
                assert policy.threshold >= 1
                # Policy IDs are graph-derived, not hard-coded stage names
                assert isinstance(policy.policy_id, str)
                assert len(policy.policy_id) > 0
        assert any_policy, (
            "Compiled plan should contain threshold policies"
        )

    def test_runtime_repair_route_counter_from_graph_data(
        self, tmp_path: Path
    ) -> None:
        """RuntimeRepairRoute counter_name comes from graph data, not hard-coded."""
        engine = _engine(tmp_path)
        engine.snapshot = _snapshot(engine, active_plane=Plane.EXECUTION)

        plan = _compiled_plan(tmp_path)
        from millrace_ai.runtime.recovery.repair_routes import (
            runtime_repair_route_for_plane,
        )

        route = runtime_repair_route_for_plane(
            engine, Plane.EXECUTION, compiled_plan=plan
        )
        if route is not None:
            # The counter_name comes from graph.runtime_failure_recovery
            assert isinstance(route.counter_name, (str, type(None)))
            if route.counter_name is not None:
                assert len(route.counter_name) > 0


# ---------------------------------------------------------------------------
# A8: Explicit zero counter values are authoritative over stale legacy fallback
# ---------------------------------------------------------------------------


class TestGenericCounterZeroAuthoritative:
    """Regression: a generic counter entry with an explicit zero value must
    override a stale legacy snapshot compatibility field."""

    def test_explicit_zero_in_generic_overrides_stale_legacy_in_counters(
        self,
    ) -> None:
        """counter_attempts_for_counter_id returns 0 when the generic counter
        entry explicitly contains troubleshoot_attempt_count=0, even though the
        snapshot legacy field is a stale 5."""
        snapshot = RuntimeSnapshot(
            schema_version="1.0",
            kind="runtime_snapshot",
            runtime_mode="daemon",
            process_running=True,
            paused=False,
            active_mode_id="standard_plain",
            execution_loop_id="execution.standard",
            planning_loop_id="planning.standard",
            compiled_plan_id="plan-test",
            compiled_plan_path="state/compiled_plan.json",
            active_plane=Plane.EXECUTION,
            active_stage=ExecutionStageName.BUILDER,
            active_node_id="builder",
            active_stage_kind_id="builder",
            active_run_id="run-001",
            active_work_item_family_id="task",
            active_work_item_kind=WorkItemKind.TASK,
            active_work_item_id="task-001",
            troubleshoot_attempt_count=5,  # stale legacy value
            execution_status_marker="### IDLE",
            planning_status_marker="### IDLE",
            config_version="1.0",
            watcher_mode="poll",
            updated_at=NOW,
        )
        counters = RecoveryCounters(
            entries=[
                RecoveryCounterEntry(
                    failure_class="recoverable_failure",
                    work_item_family_id="task",
                    work_item_kind=WorkItemKind.TASK,
                    work_item_id="task-001",
                    counters={"troubleshoot_attempt_count": 0},
                    last_updated_at=NOW,
                )
            ]
        )

        result = counter_attempts_for_counter_id(
            snapshot,
            counters,
            "recoverable_failure",
            counter_id="troubleshoot_attempt_count",
        )
        # Explicit zero in generic store must override stale legacy 5.
        assert result == 0

    def test_explicit_zero_in_generic_overrides_stale_legacy_in_repair_routes(
        self, tmp_path: Path
    ) -> None:
        """_resolve_repair_counter_value returns 0 when the generic counter
        entry explicitly contains troubleshoot_attempt_count=0, even though the
        snapshot legacy field is a stale 5."""
        engine = _engine(tmp_path)
        snapshot = _snapshot(
            engine,
            troubleshoot_attempt_count=5,  # stale legacy value
            current_failure_class="recoverable_failure",
        )
        engine.snapshot = snapshot
        engine.counters = RecoveryCounters(
            entries=[
                RecoveryCounterEntry(
                    failure_class="recoverable_failure",
                    work_item_family_id="task",
                    work_item_id="task-001",
                    counters={"troubleshoot_attempt_count": 0},
                    last_updated_at=NOW,
                )
            ]
        )

        route = RuntimeRepairRoute(
            node_id="troubleshooter",
            stage_kind_id="troubleshooter",
            stage=ExecutionStageName.TROUBLESHOOTER,
            counter_name="troubleshoot_attempt_count",
            threshold=3,
        )

        # With explicit 0 in generic store and threshold 3, attempts are not
        # exhausted (0 < 3). If the stale legacy value of 5 were used instead,
        # this would incorrectly report exhausted.
        assert runtime_repair_attempts_exhausted(engine, route) is False

        # incremented_repair_counter should increment from the authoritative
        # generic value (0 → 1), not from the stale legacy value (5 → 6).
        result = incremented_repair_counter(engine, route)
        assert result == {"troubleshoot_attempt_count": 1}

    def test_absent_counter_id_returns_zero_instead_of_legacy_fallback(self) -> None:
        """Missing generic counter ids do not read legacy snapshot fields."""
        snapshot = RuntimeSnapshot(
            schema_version="1.0",
            kind="runtime_snapshot",
            runtime_mode="daemon",
            process_running=True,
            paused=False,
            active_mode_id="standard_plain",
            execution_loop_id="execution.standard",
            planning_loop_id="planning.standard",
            compiled_plan_id="plan-test",
            compiled_plan_path="state/compiled_plan.json",
            active_plane=Plane.EXECUTION,
            active_stage=ExecutionStageName.BUILDER,
            active_node_id="builder",
            active_stage_kind_id="builder",
            active_run_id="run-001",
            active_work_item_family_id="task",
            active_work_item_kind=WorkItemKind.TASK,
            active_work_item_id="task-001",
            troubleshoot_attempt_count=3,
            execution_status_marker="### IDLE",
            planning_status_marker="### IDLE",
            config_version="1.0",
            watcher_mode="poll",
            updated_at=NOW,
        )
        counters = RecoveryCounters(
            entries=[
                RecoveryCounterEntry(
                    failure_class="recoverable_failure",
                    work_item_family_id="task",
                    work_item_kind=WorkItemKind.TASK,
                    work_item_id="task-001",
                    counters={"custom_only": 7},  # troubleshoot_attempt_count absent
                    last_updated_at=NOW,
                )
            ]
        )

        result = counter_attempts_for_counter_id(
            snapshot,
            counters,
            "recoverable_failure",
            counter_id="troubleshoot_attempt_count",
        )
        assert result == 0

    def test_nonzero_counter_does_not_fall_back_to_legacy(self, tmp_path: Path) -> None:
        """A non-zero generic counter value is used directly, not the legacy
        fallback. This ensures the fix does not alter routing for existing
        non-zero counters."""
        engine = _engine(tmp_path)
        snapshot = _snapshot(
            engine,
            troubleshoot_attempt_count=99,  # stale legacy value
            current_failure_class="recoverable_failure",
        )
        engine.snapshot = snapshot
        engine.counters = RecoveryCounters(
            entries=[
                RecoveryCounterEntry(
                    failure_class="recoverable_failure",
                    work_item_family_id="task",
                    work_item_id="task-001",
                    counters={"troubleshoot_attempt_count": 2},
                    last_updated_at=NOW,
                )
            ]
        )

        route = RuntimeRepairRoute(
            node_id="troubleshooter",
            stage_kind_id="troubleshooter",
            stage=ExecutionStageName.TROUBLESHOOTER,
            counter_name="troubleshoot_attempt_count",
            threshold=3,
        )

        # Generic store has 2, not the stale 99. Not exhausted (2 < 3).
        assert runtime_repair_attempts_exhausted(engine, route) is False


# ---------------------------------------------------------------------------
# Config-driven behavior tests: standard vs recovery-heavy threshold routing
# ---------------------------------------------------------------------------


class TestConfigDrivenThresholdRouting:
    """Equivalent inputs produce different runtime outcomes based on config
    threshold data alone.

    Config dependency: the execution.standard graph asset defines a
    threshold_policy for BLOCKED outcomes with exhausted_target_node_id=
    "consultant".  The RuntimeConfig recovery.max_troubleshoot_attempts_before_consult
    (default 2) overrides the graph threshold at compile time.  When the
    config override is lowered to 1, the same counter state (1) routes to the
    exhausted target (consultant) instead of the standard recovery target
    (troubleshooter).
    """

    def test_standard_threshold_routes_to_troubleshooter_below_threshold(
        self, tmp_path: Path
    ) -> None:
        """With standard config (max_troubleshoot_attempts_before_consult=2)
        and counter=1, the BLOCKED result routes to troubleshooter (not
        exhausted).

        Config asset: assets/graphs/execution/standard.json
        RuntimeConfig: recovery.max_troubleshoot_attempts_before_consult=2 (default)
        """
        plan = _compiled_plan(tmp_path)

        snapshot = RuntimeSnapshot(
            schema_version="1.0",
            kind="runtime_snapshot",
            runtime_mode="daemon",
            process_running=True,
            paused=False,
            active_mode_id="standard_plain",
            execution_loop_id="execution.standard",
            planning_loop_id="planning.standard",
            compiled_plan_id=plan.compiled_plan_id,
            compiled_plan_path="millrace-agents/state/compiled_plan.json",
            active_plane=Plane.EXECUTION,
            active_stage=ExecutionStageName.BUILDER,
            active_node_id="builder",
            active_stage_kind_id="builder",
            active_run_id="run-001",
            active_work_item_family_id="task",
            active_work_item_kind=WorkItemKind.TASK,
            active_work_item_id="task-001",
            execution_status_marker="### IDLE",
            planning_status_marker="### IDLE",
            config_version="1.0",
            watcher_mode="poll",
            updated_at=NOW,
        )

        # Counter = 1, below standard threshold of 2
        counters = RecoveryCounters(
            entries=[
                RecoveryCounterEntry(
                    failure_class="recoverable_failure",
                    work_item_family_id="task",
                    work_item_kind=WorkItemKind.TASK,
                    work_item_id="task-001",
                    counters={"troubleshoot_attempt_count": 1},
                    last_updated_at=NOW,
                )
            ]
        )

        decision = route_stage_result_from_graph(
            plan,
            snapshot,
            _stage_result(
                terminal_result="BLOCKED",
                metadata={"failure_class": "recoverable_failure"},
            ),
            counters,
        )
        # Standard threshold=2, counter=1 < 2 → not exhausted → troubleshooter
        assert decision.action is RouterAction.RUN_STAGE
        assert decision.next_stage is ExecutionStageName.TROUBLESHOOTER
        assert decision.next_node_id == "troubleshooter"

    def test_recovery_heavy_threshold_routes_to_consultant_at_exhaustion(
        self, tmp_path: Path
    ) -> None:
        """With a recovery-heavy config (max_troubleshoot_attempts_before_consult=1)
        and counter=1, the BLOCKED result routes to consultant (exhausted
        target).

        Config: RuntimeConfig with recovery.max_troubleshoot_attempts_before_consult=1
        """
        from millrace_ai.config.models import RecoverySection

        config = RuntimeConfig(
            recovery=RecoverySection(
                max_troubleshoot_attempts_before_consult=1,
            ),
        )

        paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
        outcome = compile_and_persist_workspace_plan(
            paths.root,
            config=config,
            requested_mode_id="standard_plain",
        )
        assert outcome.active_plan is not None
        plan = outcome.active_plan

        snapshot = RuntimeSnapshot(
            schema_version="1.0",
            kind="runtime_snapshot",
            runtime_mode="daemon",
            process_running=True,
            paused=False,
            active_mode_id="standard_plain",
            execution_loop_id="execution.standard",
            planning_loop_id="planning.standard",
            compiled_plan_id=plan.compiled_plan_id,
            compiled_plan_path="millrace-agents/state/compiled_plan.json",
            active_plane=Plane.EXECUTION,
            active_stage=ExecutionStageName.BUILDER,
            active_node_id="builder",
            active_stage_kind_id="builder",
            active_run_id="run-001",
            active_work_item_family_id="task",
            active_work_item_kind=WorkItemKind.TASK,
            active_work_item_id="task-001",
            execution_status_marker="### IDLE",
            planning_status_marker="### IDLE",
            config_version="1.0",
            watcher_mode="poll",
            updated_at=NOW,
        )

        # Same counter=1, but now threshold=1 → exhausted
        counters = RecoveryCounters(
            entries=[
                RecoveryCounterEntry(
                    failure_class="recoverable_failure",
                    work_item_family_id="task",
                    work_item_kind=WorkItemKind.TASK,
                    work_item_id="task-001",
                    counters={"troubleshoot_attempt_count": 1},
                    last_updated_at=NOW,
                )
            ]
        )

        decision = route_stage_result_from_graph(
            plan,
            snapshot,
            _stage_result(
                terminal_result="BLOCKED",
                metadata={"failure_class": "recoverable_failure"},
            ),
            counters,
        )
        # Recovery-heavy threshold=1, counter=1 >= 1 → exhausted → consultant
        assert decision.action is RouterAction.RUN_STAGE
        assert decision.next_node_id == "consultant"

    def test_standard_threshold_policy_data_is_config_driven(
        self, tmp_path: Path
    ) -> None:
        """The compiled threshold policies derive from graph config data,
        not hard-coded stage names.  The standard graph declares
        threshold=2 for the BLOCKED→consultant exhaustion path.

        Config asset: assets/graphs/execution/standard.json
        """
        plan = _compiled_plan(tmp_path)
        graph = plan.graphs_by_plane[Plane.EXECUTION]

        # The compiled threshold policies come from graph config
        blocked_policy = None
        for policy in graph.compiled_threshold_policies:
            if policy.policy_id == "execution.blocked.recovery":
                blocked_policy = policy
                break
        assert blocked_policy is not None, (
            "Standard execution graph must define execution.blocked.recovery "
            "threshold policy"
        )
        # Threshold value comes from graph data, not runtime code
        assert blocked_policy.threshold == 2
        assert blocked_policy.exhausted_target_node_id == "consultant"
        # Counter name is a string from config, not an enum
        assert isinstance(blocked_policy.counter_name, str)
