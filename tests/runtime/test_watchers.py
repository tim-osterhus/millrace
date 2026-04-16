from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import WatcherMode
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.watchers import build_watcher_session, resolve_watcher_mode

NOW = datetime(2026, 4, 15, tzinfo=timezone.utc)


def _bootstrap(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def test_resolve_watcher_mode_degrades_to_poll_when_watchdog_unavailable() -> None:
    disabled = RuntimeConfig()
    enabled = RuntimeConfig(watchers={"enabled": True})

    assert resolve_watcher_mode(disabled, watchdog_available=False) is WatcherMode.OFF
    assert resolve_watcher_mode(enabled, watchdog_available=False) is WatcherMode.POLL
    assert resolve_watcher_mode(enabled, watchdog_available=True) is WatcherMode.WATCH


def test_poll_watcher_discovers_config_task_spec_and_idea_changes(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config = RuntimeConfig(watchers={"enabled": True, "debounce_ms": 150})

    session = build_watcher_session(
        paths,
        config=config,
        config_path=config_path,
        watchdog_available=False,
        now=NOW,
    )

    assert session.mode is WatcherMode.POLL
    assert session.poll_once(now=NOW) == ()

    ideas_inbox = paths.root / "ideas" / "inbox"
    ideas_inbox.mkdir(parents=True, exist_ok=True)
    (ideas_inbox / "idea-001.md").write_text("New idea\n", encoding="utf-8")
    (paths.tasks_queue_dir / "task-001.md").write_text("# Task 001\n", encoding="utf-8")
    (paths.specs_queue_dir / "spec-001.md").write_text("# Spec 001\n", encoding="utf-8")
    config_path.write_text("[watchers]\nenabled=true\n", encoding="utf-8")

    events = session.poll_once(now=NOW + timedelta(seconds=1))

    seen = {(event.target, event.path.name) for event in events}
    assert ("ideas_inbox", "idea-001.md") in seen
    assert ("tasks_queue", "task-001.md") in seen
    assert ("specs_queue", "spec-001.md") in seen
    assert ("config", "millrace.toml") in seen


def test_poll_watcher_debounces_rapid_repeat_events(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    config = RuntimeConfig(watchers={"enabled": True, "debounce_ms": 250})
    session = build_watcher_session(paths, config=config, watchdog_available=False, now=NOW)

    target = paths.specs_queue_dir / "spec-001.md"
    target.write_text("# Spec 001\n", encoding="utf-8")

    first = session.poll_once(now=NOW + timedelta(seconds=1))
    assert any(event.path == target for event in first)

    target.write_text("# Spec 001 v2\n", encoding="utf-8")
    second = session.poll_once(now=NOW + timedelta(seconds=1, milliseconds=100))
    assert second == ()

    target.write_text("# Spec 001 v3\n", encoding="utf-8")
    third = session.poll_once(now=NOW + timedelta(seconds=2))
    assert any(event.path == target for event in third)


def test_poll_watcher_emits_debounced_change_after_quiet_period_without_new_write(
    tmp_path: Path,
) -> None:
    paths = _bootstrap(tmp_path)
    config = RuntimeConfig(watchers={"enabled": True, "debounce_ms": 400})
    session = build_watcher_session(paths, config=config, watchdog_available=False, now=NOW)

    target = paths.specs_queue_dir / "spec-002.md"
    target.write_text("# Spec 002\n", encoding="utf-8")
    first = session.poll_once(now=NOW + timedelta(seconds=1))
    assert any(event.path == target for event in first)

    target.write_text("# Spec 002 v2\n", encoding="utf-8")
    suppressed = session.poll_once(now=NOW + timedelta(seconds=1, milliseconds=100))
    assert suppressed == ()

    eventual = session.poll_once(now=NOW + timedelta(seconds=2))
    assert any(event.path == target for event in eventual)


def test_poll_watcher_handles_missing_roots_and_deleted_files_safely(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    config = RuntimeConfig(watchers={"enabled": True, "watch_ideas_inbox": True})
    session = build_watcher_session(paths, config=config, watchdog_available=False, now=NOW)

    # Missing ideas root should not fail polling.
    assert session.poll_once(now=NOW) == ()

    target = paths.tasks_queue_dir / "task-001.md"
    target.write_text("# Task 001\n", encoding="utf-8")
    _ = session.poll_once(now=NOW + timedelta(seconds=1))

    target.unlink()
    assert session.poll_once(now=NOW + timedelta(seconds=2)) == ()
