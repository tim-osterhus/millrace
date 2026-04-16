from __future__ import annotations

import importlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from millrace_ai.contracts import (
    ExecutionStageName,
    ExecutionTerminalResult,
    Plane,
    PlanningStageName,
    RecoveryCounters,
    RuntimeSnapshot,
    WorkItemKind,
)
from millrace_ai.errors import WorkspaceStateError
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.state_store import (
    ReconciliationSignal,
    collect_reconciliation_signals,
    increment_troubleshoot_attempt,
    load_execution_status,
    load_recovery_counters,
    load_snapshot,
    reset_forward_progress_counters,
    save_recovery_counters,
    save_snapshot,
    set_execution_status,
    set_planning_status,
)

NOW = datetime(2026, 4, 15, tzinfo=timezone.utc)


def _bootstrap(tmp_path: Path):
    paths = workspace_paths(tmp_path / "workspace")
    bootstrap_workspace(paths)
    return paths


def test_state_store_facade_is_split_over_workspace_modules() -> None:
    state_facade = importlib.import_module("millrace_ai.state_store")
    state_store_module = importlib.import_module("millrace_ai.workspace.state_store")
    state_reconciliation_module = importlib.import_module("millrace_ai.workspace.state_reconciliation")

    assert state_facade.load_snapshot is state_store_module.load_snapshot
    assert state_facade.set_execution_status is state_store_module.set_execution_status
    assert state_facade.ReconciliationSignal.__module__ == "millrace_ai.workspace.state_reconciliation"
    assert state_facade.collect_reconciliation_signals is (
        state_reconciliation_module.collect_reconciliation_signals
    )


def test_save_snapshot_and_load_snapshot_round_trip(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    snapshot = load_snapshot(paths).model_copy(
        update={
            "paused": True,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.CHECKER,
            "active_run_id": "run-001",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )

    save_snapshot(paths, snapshot)
    loaded = load_snapshot(paths)

    assert loaded.paused is True
    assert loaded.active_stage == "checker"
    assert loaded.active_work_item_id == "task-001"


def test_save_snapshot_is_atomic_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _bootstrap(tmp_path)
    before = paths.runtime_snapshot_file.read_text(encoding="utf-8")
    snapshot = load_snapshot(paths).model_copy(update={"paused": True, "updated_at": NOW})

    def fail_replace(_: str | Path, __: str | Path) -> None:
        raise OSError("replace failure")

    monkeypatch.setattr("millrace_ai.workspace.state_store.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failure"):
        save_snapshot(paths, snapshot)

    assert paths.runtime_snapshot_file.read_text(encoding="utf-8") == before
    leftovers = tuple(paths.state_dir.glob(f".{paths.runtime_snapshot_file.name}.tmp-*"))
    assert leftovers == ()


def test_load_snapshot_validates_payload_on_read(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    paths.runtime_snapshot_file.write_text('{"kind":"runtime_snapshot"}\n', encoding="utf-8")

    with pytest.raises(ValidationError):
        load_snapshot(paths)


def test_load_snapshot_rejects_non_object_payload_with_typed_workspace_error(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    paths.runtime_snapshot_file.write_text('["not-an-object"]\n', encoding="utf-8")

    with pytest.raises(WorkspaceStateError, match="Expected object payload"):
        load_snapshot(paths)


def test_recovery_counter_helpers_persist_and_reset_forward_progress(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)

    first = increment_troubleshoot_attempt(
        paths,
        failure_class="stale_active_ownership",
        work_item_kind="task",
        work_item_id="task-001",
        now=NOW,
    )
    second = increment_troubleshoot_attempt(
        paths,
        failure_class="stale_active_ownership",
        work_item_kind="task",
        work_item_id="task-001",
        now=NOW,
    )

    assert first.troubleshoot_attempt_count == 1
    assert second.troubleshoot_attempt_count == 2

    counters = load_recovery_counters(paths)
    assert len(counters.entries) == 1
    assert counters.entries[0].troubleshoot_attempt_count == 2

    reset_forward_progress_counters(
        paths,
        work_item_kind="task",
        work_item_id="task-001",
    )
    after = load_recovery_counters(paths)
    assert after.entries == ()


def test_save_recovery_counters_and_load_round_trip(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    counters = RecoveryCounters(
        entries=[
            {
                "failure_class": "missing_terminal_result",
                "work_item_kind": "task",
                "work_item_id": "task-001",
                "troubleshoot_attempt_count": 1,
                "last_updated_at": NOW,
            }
        ]
    )

    save_recovery_counters(paths, counters)
    loaded = load_recovery_counters(paths)

    assert len(loaded.entries) == 1
    assert loaded.entries[0].failure_class == "missing_terminal_result"


def test_set_execution_status_enforces_terminal_only_marker_rules(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)

    set_execution_status(paths, "### CHECKER_PASS")

    assert load_execution_status(paths) == "### CHECKER_PASS"
    assert paths.execution_status_file.read_text(encoding="utf-8") == "### CHECKER_PASS\n"

    with pytest.raises(WorkspaceStateError, match="single line"):
        set_execution_status(paths, "### CHECKER_PASS\n### FIX_NEEDED")

    with pytest.raises(WorkspaceStateError, match="Unknown execution status marker"):
        set_execution_status(paths, "### RUNNING")


def test_set_planning_status_rejects_execution_marker(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)

    with pytest.raises(WorkspaceStateError, match="Unknown planning status marker"):
        set_planning_status(paths, "### CHECKER_PASS")


def test_collect_reconciliation_signals_detects_stale_execution_ownership(
    tmp_path: Path,
) -> None:
    paths = _bootstrap(tmp_path)
    snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": False,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.CHECKER,
            "active_run_id": "run-001",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )

    signals = collect_reconciliation_signals(
        snapshot=snapshot,
        counters=RecoveryCounters(),
        execution_status_marker=f"### {ExecutionTerminalResult.CHECKER_PASS.value}",
        planning_status_marker="### IDLE",
    )

    assert signals
    stale = next(signal for signal in signals if signal.code == "stale_active_ownership")
    assert stale.failure_class == "stale_active_ownership"
    assert stale.plane == "execution"
    assert stale.recommended_stage == "troubleshooter"


def test_collect_reconciliation_signals_escalates_repeated_execution_stale_state(
    tmp_path: Path,
) -> None:
    paths = _bootstrap(tmp_path)
    snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": False,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.CHECKER,
            "active_run_id": "run-001",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    counters = RecoveryCounters(
        entries=[
            {
                "failure_class": "stale_active_ownership",
                "work_item_kind": WorkItemKind.TASK,
                "work_item_id": "task-001",
                "troubleshoot_attempt_count": 2,
                "last_updated_at": NOW,
            }
        ]
    )

    signals = collect_reconciliation_signals(
        snapshot=snapshot,
        counters=counters,
        execution_status_marker=f"### {ExecutionTerminalResult.CHECKER_PASS.value}",
        planning_status_marker="### IDLE",
    )

    stale = next(signal for signal in signals if signal.code == "stale_active_ownership")
    assert stale.recommended_stage == "consultant"


def test_collect_reconciliation_signals_flags_impossible_planning_marker(
    tmp_path: Path,
) -> None:
    paths = _bootstrap(tmp_path)
    snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": True,
            "active_plane": Plane.PLANNING,
            "active_stage": PlanningStageName.PLANNER,
            "active_run_id": "run-001",
            "active_work_item_kind": WorkItemKind.SPEC,
            "active_work_item_id": "spec-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )

    signals = collect_reconciliation_signals(
        snapshot=snapshot,
        counters=RecoveryCounters(),
        execution_status_marker="### IDLE",
        planning_status_marker="### CHECKER_PASS",
    )

    impossible = next(
        signal
        for signal in signals
        if signal.code == "impossible_planning_status_marker"
    )
    assert impossible.failure_class == "impossible_status_marker"
    assert impossible.recommended_stage == "mechanic"


def test_collect_reconciliation_signals_flags_impossible_execution_marker(
    tmp_path: Path,
) -> None:
    paths = _bootstrap(tmp_path)
    snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": True,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.BUILDER,
            "active_run_id": "run-001",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )

    signals = collect_reconciliation_signals(
        snapshot=snapshot,
        counters=RecoveryCounters(),
        execution_status_marker="### CHECKER_PASS",
        planning_status_marker="### IDLE",
    )

    impossible = next(
        signal
        for signal in signals
        if signal.code == "impossible_execution_status_marker"
    )
    assert impossible.failure_class == "impossible_status_marker"
    assert impossible.recommended_stage == "troubleshooter"


def test_collect_reconciliation_signals_handles_malformed_marker_without_raising(
    tmp_path: Path,
) -> None:
    paths = _bootstrap(tmp_path)
    snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": True,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.BUILDER,
            "active_run_id": "run-001",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )

    signals = collect_reconciliation_signals(
        snapshot=snapshot,
        counters=RecoveryCounters(),
        execution_status_marker="### BUILDER_COMPLETE\n### EXTRA",
        planning_status_marker="### IDLE",
    )

    impossible = next(
        signal for signal in signals if signal.code == "impossible_execution_status_marker"
    )
    assert impossible.failure_class == "impossible_status_marker"


def test_collect_reconciliation_signals_allows_expected_execution_transition_marker(
    tmp_path: Path,
) -> None:
    paths = _bootstrap(tmp_path)
    snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": True,
            "active_plane": Plane.EXECUTION,
            "active_stage": ExecutionStageName.CHECKER,
            "active_run_id": "run-001",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )

    signals = collect_reconciliation_signals(
        snapshot=snapshot,
        counters=RecoveryCounters(),
        execution_status_marker="### BUILDER_COMPLETE",
        planning_status_marker="### IDLE",
    )

    assert all(signal.code != "impossible_execution_status_marker" for signal in signals)


def test_collect_reconciliation_signals_flags_orphaned_recovery_counters(
    tmp_path: Path,
) -> None:
    paths = _bootstrap(tmp_path)
    snapshot = load_snapshot(paths).model_copy(update={"updated_at": NOW})
    counters = RecoveryCounters(
        entries=[
            {
                "failure_class": "missing_terminal_result",
                "work_item_kind": WorkItemKind.TASK,
                "work_item_id": "task-123",
                "troubleshoot_attempt_count": 1,
                "last_updated_at": NOW,
            }
        ]
    )

    signals = collect_reconciliation_signals(
        snapshot=snapshot,
        counters=counters,
        execution_status_marker="### IDLE",
        planning_status_marker="### IDLE",
    )

    orphaned = next(
        signal for signal in signals if signal.code == "orphaned_recovery_counters"
    )
    assert orphaned.failure_class == "stale_recovery_without_active_stage"
    assert orphaned.recommended_stage == "troubleshooter"


def test_collect_reconciliation_signals_allows_expected_planning_transition_marker(
    tmp_path: Path,
) -> None:
    paths = _bootstrap(tmp_path)
    snapshot = RuntimeSnapshot.model_validate(
        {
            **load_snapshot(paths).model_dump(mode="python"),
            "process_running": True,
            "active_plane": Plane.PLANNING,
            "active_stage": PlanningStageName.MANAGER,
            "active_run_id": "run-001",
            "active_work_item_kind": WorkItemKind.SPEC,
            "active_work_item_id": "spec-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )

    signals = collect_reconciliation_signals(
        snapshot=snapshot,
        counters=RecoveryCounters(),
        execution_status_marker="### IDLE",
        planning_status_marker="### PLANNER_COMPLETE",
    )

    assert all(signal.code != "impossible_planning_status_marker" for signal in signals)


def test_reconciliation_signal_is_immutable_dataclass() -> None:
    signal = ReconciliationSignal(
        code="stale_active_ownership",
        failure_class="stale_active_ownership",
        plane=Plane.EXECUTION,
        recommended_stage=ExecutionStageName.TROUBLESHOOTER,
        message="runtime snapshot has active ownership while process is not running",
    )

    with pytest.raises(AttributeError):
        signal.code = "different"
