from __future__ import annotations

import asyncio
from pathlib import Path

import millrace_engine.engine_config_coordinator as coordinator_module
from millrace_engine.config import ConfigApplyBoundary, LoadedConfig, build_runtime_paths, load_engine_config
from millrace_engine.contracts import ResearchMode, StageType
from millrace_engine.engine_config_coordinator import (
    EngineConfigCoordinator,
    EngineConfigCoordinatorHooks,
)
from millrace_engine.events import EventSource, EventType
from millrace_engine.paths import RuntimePaths

from .support import load_workspace_fixture


class _CoordinatorHarness:
    def __init__(self, config_path: Path) -> None:
        initial_loaded = load_engine_config(config_path)
        initial_paths = build_runtime_paths(initial_loaded.config)
        self.events: list[tuple[EventType, EventSource, dict[str, object]]] = []
        self.install_calls: list[tuple[LoadedConfig, RuntimePaths]] = []
        self.dispatch_triggers: list[str] = []
        self.coordinator = EngineConfigCoordinator(
            config_path=config_path,
            initial_loaded=initial_loaded,
            initial_paths=initial_paths,
            hooks=EngineConfigCoordinatorHooks(
                emit_event=self._emit_event,
                install_loaded_config=self._install_loaded_config,
                sync_ready_research_dispatch=self._sync_ready_research_dispatch,
            ),
        )

    def _emit_event(self, event_type: EventType, source: EventSource, payload: dict[str, object]) -> None:
        self.events.append((event_type, source, payload))

    def _install_loaded_config(self, loaded: LoadedConfig, paths: RuntimePaths) -> None:
        self.install_calls.append((loaded, paths))

    def _sync_ready_research_dispatch(self, trigger: str) -> None:
        self.dispatch_triggers.append(trigger)


def test_engine_config_coordinator_applies_pending_cycle_boundary_change_and_syncs_dispatch(
    tmp_path: Path,
) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    harness = _CoordinatorHarness(config_path)
    reloaded = load_engine_config(config_path)
    reloaded.config.research.mode = ResearchMode.AUTO

    operation, restart_watcher = harness.coordinator.queue_or_apply_reloaded_config(
        reloaded,
        command_id="cfg-1",
    )

    assert operation.applied is True
    assert operation.message == "config queued for cycle_boundary"
    assert restart_watcher is False
    assert harness.coordinator.pending_boundary is ConfigApplyBoundary.CYCLE_BOUNDARY
    assert harness.dispatch_triggers == []

    restart_on_apply = harness.coordinator.apply_pending_config_if_due(
        ConfigApplyBoundary.CYCLE_BOUNDARY
    )

    assert restart_on_apply is False
    assert harness.coordinator.loaded.config.research.mode is ResearchMode.AUTO
    assert harness.coordinator.pending_loaded is None
    assert harness.dispatch_triggers == ["config-applied"]
    assert [event[0] for event in harness.events] == [
        EventType.CONFIG_CHANGED,
        EventType.CONFIG_APPLIED,
    ]


def test_engine_config_coordinator_marks_cycle_boundary_watcher_changes_for_restart(
    tmp_path: Path,
) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "config_hotswap")
    harness = _CoordinatorHarness(config_path)
    reloaded = load_engine_config(config_path)
    reloaded.config.watchers.debounce_seconds = 2

    operation, restart_watcher = harness.coordinator.queue_or_apply_reloaded_config(
        reloaded,
        command_id="cfg-watcher",
        key="watchers.debounce_seconds",
    )

    assert operation.applied is True
    assert operation.message == "config queued for cycle_boundary"
    assert restart_watcher is False

    restart_on_apply = harness.coordinator.apply_pending_config_if_due(
        ConfigApplyBoundary.CYCLE_BOUNDARY
    )

    assert restart_on_apply is True
    assert harness.coordinator.loaded.config.watchers.debounce_seconds == 2


def test_engine_config_coordinator_rolls_back_applied_stage_boundary_change(tmp_path: Path) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "config_hotswap")
    harness = _CoordinatorHarness(config_path)
    initial_model = harness.coordinator.loaded.config.stages[StageType.QA].model
    reloaded = load_engine_config(config_path)
    reloaded.config.stages[StageType.QA].model = "bad-model"

    operation, restart_watcher = harness.coordinator.queue_or_apply_reloaded_config(
        reloaded,
        command_id="cfg-rollback",
        key="stages.qa.model",
    )

    assert operation.applied is True
    assert operation.message == "config queued for stage_boundary"
    assert restart_watcher is False

    restart_on_apply = harness.coordinator.apply_pending_config_if_due(
        ConfigApplyBoundary.STAGE_BOUNDARY
    )

    assert restart_on_apply is False
    assert harness.coordinator.loaded.config.stages[StageType.QA].model == "bad-model"
    assert harness.coordinator.rollback_armed is True

    rolled_back = harness.coordinator.rollback_active_config("qa failed")

    assert rolled_back is True
    assert harness.coordinator.loaded.config.stages[StageType.QA].model == initial_model
    assert harness.coordinator.pending_loaded is None
    assert harness.coordinator.previous_loaded is None
    assert harness.coordinator.rollback_armed is False
    assert any(
        event_type is EventType.CONFIG_APPLIED and payload.get("rollback") is True
        for event_type, _source, payload in harness.events
    )


def test_engine_config_coordinator_rejects_invalid_reload_after_retries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "config_hotswap")
    harness = _CoordinatorHarness(config_path)
    trigger_path = Path("/tmp/rejected-millrace.toml")

    def _raise_invalid_reload(_config_path: Path) -> LoadedConfig:
        raise ValueError("invalid config payload")

    async def _no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(coordinator_module, "load_engine_config", _raise_invalid_reload)
    monkeypatch.setattr(coordinator_module.asyncio, "sleep", _no_sleep)

    applied, restart_watcher = asyncio.run(
        harness.coordinator.reload_config_from_disk(trigger_path=trigger_path)
    )

    assert applied is False
    assert restart_watcher is False
    assert harness.events[-1] == (
        EventType.CONFIG_CHANGED,
        EventSource.ADAPTER,
        {
            "rejected": True,
            "reason": "invalid config payload",
            "path": trigger_path,
        },
    )
