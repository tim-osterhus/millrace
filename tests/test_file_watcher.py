from __future__ import annotations

from pathlib import Path

from watchdog.events import FileCreatedEvent

from millrace_engine.adapters.file_watcher import (
    FileWatcherAdapter,
    RuntimeInputKind,
    RuntimeInputRouter,
    _WatchdogHandler,
    watch_mode_supported,
)
from tests.support import load_workspace_fixture, runtime_paths


def test_runtime_input_router_debounces_duplicate_modify_bursts(tmp_path: Path) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "watcher_stop_completion")
    paths = runtime_paths(config_path)
    ticks = iter([10.0, 10.1, 10.8])
    router = RuntimeInputRouter(paths, debounce_seconds=0.5, monotonic_clock=lambda: next(ticks))

    first = router.submit_candidate(paths.backlog_file, raw_event="modified")
    second = router.submit_candidate(paths.backlog_file, raw_event="modified")
    third = router.submit_candidate(paths.backlog_file, raw_event="modified")

    assert first is not None
    assert first.kind is RuntimeInputKind.BACKLOG_CHANGED
    assert second is None
    assert third is not None
    assert third.kind is RuntimeInputKind.BACKLOG_CHANGED


def test_poll_fallback_detects_marker_file_creation(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "watcher_stop_completion")
    paths = runtime_paths(config_path)
    watcher = FileWatcherAdapter(paths, emit=lambda event: None, mode="poll")

    (workspace / "agents/STOP_AUTONOMY").write_text("stop\n", encoding="utf-8")
    events = watcher.poll_once()

    assert [event.kind for event in events] == [RuntimeInputKind.STOP_AUTONOMY]


def test_runtime_input_router_classifies_config_file_changes_when_enabled(tmp_path: Path) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "watcher_stop_completion")
    paths = runtime_paths(config_path)
    router = RuntimeInputRouter(paths, config_path=config_path)

    event = router.submit_candidate(config_path, raw_event="modified")

    assert event is not None
    assert event.kind is RuntimeInputKind.CONFIG_CHANGED
    assert event.path == config_path.resolve()


def test_poll_fallback_detects_config_file_change(tmp_path: Path) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "watcher_stop_completion")
    paths = runtime_paths(config_path)
    watcher = FileWatcherAdapter(paths, emit=lambda event: None, mode="poll", config_path=config_path)

    config_path.write_text(config_path.read_text(encoding="utf-8") + "\n# watched change\n", encoding="utf-8")
    events = watcher.poll_once()

    assert [event.kind for event in events] == [RuntimeInputKind.CONFIG_CHANGED]
    assert events[0].path == config_path.resolve()


def test_watchdog_normalization_detects_idea_file_drop(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "watcher_stop_completion")
    paths = runtime_paths(config_path)
    watcher = FileWatcherAdapter(paths, emit=lambda event: None, mode="watch")
    idea_path = workspace / "agents/ideas/raw/idea-drop.md"
    idea_path.write_text("# idea\n", encoding="utf-8")

    event = watcher.handle_watchdog_path(idea_path, raw_event="created")

    assert event is not None
    assert event.kind is RuntimeInputKind.IDEA_SUBMITTED
    assert event.path == idea_path.resolve()


def test_watchdog_dispatch_normalizes_raw_filesystem_events(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "watcher_stop_completion")
    paths = runtime_paths(config_path)
    idea_path = workspace / "agents/ideas/raw/idea-drop.md"
    idea_path.write_text("# idea\n", encoding="utf-8")
    emitted = []
    handler = _WatchdogHandler(RuntimeInputRouter(paths), emitted.append)

    handler.dispatch(FileCreatedEvent(str(idea_path)))

    assert len(emitted) == 1
    assert emitted[0].kind is RuntimeInputKind.IDEA_SUBMITTED
    assert emitted[0].path == idea_path.resolve()


def test_watch_mode_supported_falls_back_to_poll_on_macos_python_314() -> None:
    assert watch_mode_supported(
        watchdog_available=True,
        platform_name="darwin",
        python_version=(3, 14),
    ) is False
    assert watch_mode_supported(
        watchdog_available=True,
        platform_name="linux",
        python_version=(3, 14),
    ) is True


def test_watch_mode_poll_fallback_uses_short_wakeup_timeout(tmp_path: Path) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "watcher_stop_completion")
    paths = runtime_paths(config_path)
    watcher = FileWatcherAdapter(
        paths,
        emit=lambda event: None,
        mode="watch",
        watch_support_check=lambda: False,
    )

    assert watcher.requested_mode == "watch"
    assert watcher.effective_mode == "poll"
    assert watcher.mode == "poll"
    assert watcher.wakeup_timeout_seconds(60) == 0.5


def test_watch_mode_reports_effective_watch_when_native_support_is_available(tmp_path: Path) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "watcher_stop_completion")
    paths = runtime_paths(config_path)
    watcher = FileWatcherAdapter(
        paths,
        emit=lambda event: None,
        mode="watch",
        watch_support_check=lambda: True,
    )

    assert watcher.requested_mode == "watch"
    assert watcher.effective_mode == "watch"
    assert watcher.mode == "watch"
    assert watcher.wakeup_timeout_seconds(60) == 60
