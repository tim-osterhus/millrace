from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import WatcherMode
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.runner import StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.watcher_intake import normalize_idea_watch_event, safe_spec_id_from_idea_content
from millrace_ai.watchers import build_watcher_session, resolve_watcher_mode

NOW = datetime(2026, 4, 15, tzinfo=timezone.utc)


def _bootstrap(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _unused_stage_runner(request: StageRunRequest):
    raise AssertionError("stage runner should not be called")


def test_resolve_watcher_mode_degrades_to_poll_when_watchdog_unavailable() -> None:
    disabled = RuntimeConfig(watchers={"enabled": False})
    enabled = RuntimeConfig()

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

    paths.intake_ideas_inbox_dir.mkdir(parents=True, exist_ok=True)
    (paths.intake_ideas_inbox_dir / "idea-001.md").write_text("New idea\n", encoding="utf-8")
    legacy_ideas_inbox = paths.root / "ideas" / "inbox"
    legacy_ideas_inbox.mkdir(parents=True, exist_ok=True)
    (legacy_ideas_inbox / "legacy-idea-001.md").write_text("Legacy idea\n", encoding="utf-8")
    (paths.tasks_queue_dir / "task-001.md").write_text("# Task 001\n", encoding="utf-8")
    (paths.specs_queue_dir / "spec-001.md").write_text("# Spec 001\n", encoding="utf-8")
    config_path.write_text("[watchers]\nenabled=true\n", encoding="utf-8")

    events = session.poll_once(now=NOW + timedelta(seconds=1))

    seen = {(event.target, event.path.name) for event in events}
    assert ("ideas_inbox", "idea-001.md") in seen
    assert ("legacy_ideas_inbox", "legacy-idea-001.md") in seen
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


def test_idea_ids_use_normalized_title_and_content_hash() -> None:
    first = "# Same Title\n\nFirst version\n"
    second = "# Same Title\n\nSecond version\n"

    first_id = safe_spec_id_from_idea_content(first, fallback="ignored")
    repeat_id = safe_spec_id_from_idea_content(first, fallback="renamed-file")
    second_id = safe_spec_id_from_idea_content(second, fallback="ignored")

    assert first_id.startswith("idea-same-title-")
    assert repeat_id == first_id
    assert second_id.startswith("idea-same-title-")
    assert second_id != first_id


def test_normalize_new_idea_writes_json_metadata_source_and_archive(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    idea_markdown = "# Durable Idea\n\nPreserve this exact markdown.\n"
    idea_path = paths.intake_ideas_inbox_dir / "submitted.md"
    idea_path.write_text(idea_markdown, encoding="utf-8")

    normalize_idea_watch_event(engine, idea_path, legacy=False)

    spec_id = safe_spec_id_from_idea_content(idea_markdown, fallback="submitted")
    source = paths.intake_sources_idea_dir / f"{spec_id}.md"
    normalized = paths.intake_ideas_normalized_dir / f"{spec_id}.json"
    archived = paths.intake_ideas_archived_dir / "submitted.md"

    assert source.read_text(encoding="utf-8") == idea_markdown
    assert archived.read_text(encoding="utf-8") == idea_markdown
    assert not idea_path.exists()
    assert (paths.specs_queue_dir / f"{spec_id}.md").is_file()

    metadata = json.loads(normalized.read_text(encoding="utf-8"))
    assert metadata["schema_version"] == 1
    assert metadata["root_idea_id"] == spec_id
    assert metadata["root_spec_id"] == spec_id
    assert metadata["title"] == "Durable Idea"
    assert metadata["source_artifact"] == f"millrace-agents/intake/sources/idea/{spec_id}.md"
    assert metadata["archived_source"] == "millrace-agents/intake/ideas/archived/submitted.md"


def test_new_inbox_wins_before_legacy_and_legacy_archives_deterministically(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    canonical_markdown = "# Duplicate Idea\n\nCanonical wins.\n"
    canonical_path = paths.intake_ideas_inbox_dir / "duplicate.md"
    canonical_path.write_text(canonical_markdown, encoding="utf-8")
    legacy_path = paths.root / "ideas" / "inbox" / "duplicate.md"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("# Duplicate Idea\n\nLegacy is archived only.\n", encoding="utf-8")

    normalize_idea_watch_event(engine, canonical_path, legacy=False)
    normalize_idea_watch_event(engine, legacy_path, legacy=True)

    spec_id = safe_spec_id_from_idea_content(canonical_markdown, fallback="duplicate")
    assert len(tuple(paths.specs_queue_dir.glob("idea-duplicate-idea-*.md"))) == 1
    assert (paths.intake_sources_idea_dir / f"{spec_id}.md").read_text(encoding="utf-8") == canonical_markdown
    assert not legacy_path.exists()
    archived = paths.intake_ideas_archived_legacy_dir / "duplicate.md"
    assert archived.read_text(encoding="utf-8") == "# Duplicate Idea\n\nLegacy is archived only.\n"


def test_invalid_legacy_idea_is_archived_with_diagnostic_metadata(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    legacy_path = paths.root / "ideas" / "inbox" / "invalid.md"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("missing markdown heading\n", encoding="utf-8")

    normalize_idea_watch_event(engine, legacy_path, legacy=True)

    assert not legacy_path.exists()
    invalid_markdown = paths.intake_ideas_invalid_dir / "invalid.md"
    invalid_metadata = paths.intake_ideas_invalid_dir / "invalid.json"
    assert invalid_markdown.read_text(encoding="utf-8") == "missing markdown heading\n"
    metadata = json.loads(invalid_metadata.read_text(encoding="utf-8"))
    assert metadata["schema_version"] == 1
    assert metadata["reason"] == "missing_h1_title"
    assert metadata["original_path"] == "ideas/inbox/invalid.md"
    assert metadata["invalid_artifact"] == "millrace-agents/intake/ideas/invalid/invalid.md"
