"""Watcher-session lifecycle and filesystem intake helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from millrace_ai.contracts import SpecDocument, WatcherMode
from millrace_ai.errors import QueueStateError
from millrace_ai.events import write_runtime_event
from millrace_ai.queue_store import QueueStore
from millrace_ai.state_store import save_snapshot
from millrace_ai.watchers import WatchEvent, build_watcher_session
from millrace_ai.work_documents import read_work_document_as

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine

_IDEA_ID_SANITIZER = re.compile(r"[^A-Za-z0-9._-]+")


def rebuild_watcher_session(engine: RuntimeEngine) -> None:
    assert engine.config is not None
    close_watcher_session(engine)
    engine._watcher_session = build_watcher_session(
        engine.paths,
        config=engine.config,
        config_path=engine.config_path,
    )


def close_watcher_session(engine: RuntimeEngine) -> None:
    if engine._watcher_session is None:
        return
    engine._watcher_session.close()
    engine._watcher_session = None


def watcher_mode_value(engine: RuntimeEngine) -> WatcherMode:
    if engine._watcher_session is None:
        if engine.snapshot is not None:
            return engine.snapshot.watcher_mode
        return WatcherMode.OFF
    return engine._watcher_session.mode


def consume_watcher_events(engine: RuntimeEngine) -> None:
    if engine._watcher_session is None:
        return
    events = engine._watcher_session.poll_once(now=engine._now())
    if not events:
        return

    for event in events:
        handle_watch_event(engine, event)

    assert engine.snapshot is not None
    engine.snapshot = engine.snapshot.model_copy(update={"updated_at": engine._now()})
    save_snapshot(engine.paths, engine.snapshot)
    write_runtime_event(
        engine.paths,
        event_type="watcher_events_consumed",
        data={"count": len(events)},
    )


def handle_watch_event(engine: RuntimeEngine, event: WatchEvent) -> None:
    if event.target == "ideas_inbox":
        normalize_idea_watch_event(engine, event.path)
        return

    if event.target in {"tasks_queue", "specs_queue", "config"}:
        return

    write_runtime_event(
        engine.paths,
        event_type="watcher_event_ignored",
        data={"target": event.target, "path": event.path.as_posix()},
    )


def normalize_idea_watch_event(engine: RuntimeEngine, idea_path: Path) -> None:
    if not idea_path.is_file():
        return

    try:
        content = idea_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    title, summary = derive_idea_title_summary(content, fallback=idea_path.stem)
    spec_id = safe_spec_id_from_idea_path(idea_path)
    try:
        idea_reference = str(idea_path.relative_to(engine.paths.root))
    except ValueError:
        idea_reference = idea_path.as_posix()
    if idea_already_represented(engine, spec_id=spec_id, idea_reference=idea_reference):
        write_runtime_event(
            engine.paths,
            event_type="idea_normalization_skipped",
            data={
                "idea_path": idea_path.as_posix(),
                "spec_id": spec_id,
                "reason": "already_represented",
            },
        )
        return
    spec_doc = SpecDocument(
        spec_id=spec_id,
        title=title,
        summary=summary,
        source_type="idea",
        source_id=spec_id,
        root_idea_id=spec_id,
        root_spec_id=spec_id,
        goals=(summary,),
        constraints=("generated from ideas/inbox watcher event",),
        acceptance=("planner processes this idea-derived spec",),
        references=(idea_reference,),
        created_at=engine._now(),
        created_by="watcher",
    )

    try:
        QueueStore(engine.paths).enqueue_spec(spec_doc)
    except (OSError, QueueStateError):
        return

    write_runtime_event(
        engine.paths,
        event_type="idea_normalized_to_spec",
        data={"idea_path": idea_path.as_posix(), "spec_id": spec_id},
    )


def safe_spec_id_from_idea_path(path: Path) -> str:
    normalized = _IDEA_ID_SANITIZER.sub("-", path.stem).strip("-.")
    if not normalized:
        normalized = "idea"
    if normalized.startswith("idea-"):
        return normalized
    return f"idea-{normalized}"


def derive_idea_title_summary(content: str, *, fallback: str) -> tuple[str, str]:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    title = fallback
    for line in lines:
        if line.startswith("#"):
            candidate = line.lstrip("#").strip()
            if candidate:
                title = candidate
                break
    if title == fallback and lines:
        title = lines[0]

    summary = ""
    for line in lines:
        candidate = line.lstrip("#").strip()
        if candidate and candidate != title:
            summary = candidate
            break
    if not summary:
        summary = f"Idea captured from {fallback}"
    return title, summary


def idea_already_represented(
    engine: RuntimeEngine,
    *,
    spec_id: str,
    idea_reference: str,
) -> bool:
    for spec_path in _spec_document_paths(engine):
        try:
            document = read_work_document_as(spec_path, model=SpecDocument)
        except (OSError, UnicodeDecodeError, ValueError, ValidationError):
            continue
        if document.spec_id == spec_id:
            return True
        if document.root_idea_id == spec_id:
            return True
        if idea_reference in document.references:
            return True
    return False


def _spec_document_paths(engine: RuntimeEngine) -> tuple[Path, ...]:
    paths = engine.paths
    directories = (
        paths.specs_queue_dir,
        paths.specs_active_dir,
        paths.specs_done_dir,
        paths.specs_blocked_dir,
    )
    return tuple(
        path
        for directory in directories
        for path in sorted(directory.glob("*.md"))
        if path.is_file()
    )


__all__ = [
    "close_watcher_session",
    "consume_watcher_events",
    "derive_idea_title_summary",
    "handle_watch_event",
    "idea_already_represented",
    "normalize_idea_watch_event",
    "rebuild_watcher_session",
    "safe_spec_id_from_idea_path",
    "watcher_mode_value",
]
