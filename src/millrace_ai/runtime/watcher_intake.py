"""Watcher-session lifecycle and filesystem intake helpers."""

from __future__ import annotations

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
from millrace_ai.workspace.idea_sources import (
    archive_idea_inbox_artifact,
    archive_invalid_legacy_idea_artifacts,
    idea_normalized_artifact_path,
    idea_source_artifact_path,
    stable_idea_id,
    write_idea_normalized_artifact,
    write_idea_source_artifact,
)

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine

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
        normalize_idea_watch_event(engine, event.path, legacy=False)
        return
    if event.target == "legacy_ideas_inbox":
        normalize_idea_watch_event(engine, event.path, legacy=True)
        return

    if event.target in {"tasks_queue", "specs_queue", "config"}:
        return

    write_runtime_event(
        engine.paths,
        event_type="watcher_event_ignored",
        data={"target": event.target, "path": event.path.as_posix()},
    )


def normalize_idea_watch_event(
    engine: RuntimeEngine,
    idea_path: Path,
    *,
    legacy: bool | None = None,
) -> None:
    if not idea_path.is_file():
        return
    if legacy is None:
        legacy = _is_legacy_idea_path(engine, idea_path)
    if legacy:
        write_runtime_event(
            engine.paths,
            event_type="legacy_idea_inbox_compatibility_warning",
            data={
                "idea_path": idea_path.as_posix(),
                "replacement": str(engine.paths.intake_ideas_inbox_dir.relative_to(engine.paths.root)),
            },
        )

    try:
        content = idea_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    title, summary = derive_idea_title_summary(content, fallback=idea_path.stem)
    try:
        idea_reference = str(idea_path.relative_to(engine.paths.root))
    except ValueError:
        idea_reference = idea_path.as_posix()
    if legacy:
        invalid_reason = invalid_legacy_idea_reason(content)
        if invalid_reason is not None:
            archive_invalid_legacy_idea_file(
                engine,
                idea_path,
                reason=invalid_reason,
                detail="legacy idea markdown must start with a non-empty H1 title",
            )
            return
        if legacy_shadowed_by_new_inbox_archive(engine, idea_path):
            archive_legacy_idea_file(engine, idea_path, spec_id="shadowed_by_new_inbox")
            return
    spec_id = safe_spec_id_from_idea_content(content, fallback=idea_path.stem)
    source_artifact_path = idea_source_artifact_path(engine.paths, root_idea_id=spec_id)
    source_artifact_reference = str(source_artifact_path.relative_to(engine.paths.root))
    normalized_artifact_path = idea_normalized_artifact_path(
        engine.paths,
        root_idea_id=spec_id,
    )
    spec_doc = SpecDocument(
        spec_id=spec_id,
        title=title,
        summary=summary,
        source_type="idea",
        source_id=spec_id,
        root_idea_id=spec_id,
        root_spec_id=spec_id,
        goals=(summary,),
        constraints=("generated from idea intake watcher event",),
        acceptance=("planner processes this idea-derived spec",),
        references=_idea_references(
            durable_reference=source_artifact_reference,
            transient_reference=idea_reference,
        ),
        created_at=engine._now(),
        created_by="watcher",
    )
    normalized_metadata = idea_normalized_metadata(
        engine,
        spec_doc=spec_doc,
        source_artifact_reference=source_artifact_reference,
        transient_reference=idea_reference,
        archived_reference=None,
    )
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
        if not source_artifact_path.exists():
            try:
                write_idea_source_artifact(
                    engine.paths,
                    root_idea_id=spec_id,
                    markdown=content,
                )
            except (OSError, ValueError) as exc:
                write_runtime_event(
                    engine.paths,
                    event_type="idea_source_artifact_write_failed",
                    data={
                        "idea_path": idea_path.as_posix(),
                        "spec_id": spec_id,
                        "error": str(exc),
                    },
                )
                return
        if not normalized_artifact_path.exists():
            try:
                write_idea_normalized_artifact(
                    engine.paths,
                    root_idea_id=spec_id,
                    metadata=normalized_metadata,
                )
            except (OSError, ValueError) as exc:
                write_runtime_event(
                    engine.paths,
                    event_type="idea_normalized_artifact_write_failed",
                    data={
                        "idea_path": idea_path.as_posix(),
                        "spec_id": spec_id,
                        "error": str(exc),
                    },
                )
                return
        archive_consumed_idea_file(engine, idea_path, spec_id=spec_id, legacy=legacy)
        return
    try:
        source_artifact = write_idea_source_artifact(
            engine.paths,
            root_idea_id=spec_id,
            markdown=content,
        )
        source_artifact_reference = str(source_artifact.relative_to(engine.paths.root))
    except (OSError, ValueError) as exc:
        write_runtime_event(
            engine.paths,
            event_type="idea_source_artifact_write_failed",
            data={
                "idea_path": idea_path.as_posix(),
                "spec_id": spec_id,
                "error": str(exc),
            },
        )
        return

    try:
        normalized_artifact = write_idea_normalized_artifact(
            engine.paths,
            root_idea_id=spec_id,
            metadata=idea_normalized_metadata(
                engine,
                spec_doc=spec_doc,
                source_artifact_reference=source_artifact_reference,
                transient_reference=idea_reference,
                archived_reference=None,
            ),
        )
        QueueStore(engine.paths).enqueue_spec(spec_doc)
    except (OSError, QueueStateError):
        return

    archived_path = archive_consumed_idea_file(engine, idea_path, spec_id=spec_id, legacy=legacy)
    if archived_path is not None:
        try:
            write_idea_normalized_artifact(
                engine.paths,
                root_idea_id=spec_id,
                metadata=idea_normalized_metadata(
                    engine,
                    spec_doc=spec_doc,
                    source_artifact_reference=source_artifact_reference,
                    transient_reference=idea_reference,
                    archived_reference=str(archived_path.relative_to(engine.paths.root)),
                ),
            )
        except (OSError, ValueError) as exc:
            write_runtime_event(
                engine.paths,
                event_type="idea_normalized_artifact_write_failed",
                data={
                    "idea_path": idea_path.as_posix(),
                    "spec_id": spec_id,
                    "error": str(exc),
                },
            )
            return

    normalized_reference = str(normalized_artifact.relative_to(engine.paths.root))

    write_runtime_event(
        engine.paths,
        event_type="idea_normalized_to_spec",
        data={
            "idea_path": idea_path.as_posix(),
            "source_artifact": source_artifact_reference,
            "normalized_artifact": normalized_reference,
            "spec_id": spec_id,
        },
    )


def archive_consumed_idea_file(
    engine: RuntimeEngine,
    idea_path: Path,
    *,
    spec_id: str,
    legacy: bool,
) -> Path | None:
    try:
        destination = archive_idea_inbox_artifact(engine.paths, idea_path, legacy=legacy)
    except OSError as exc:
        event_type = "legacy_idea_archive_failed" if legacy else "idea_inbox_archive_failed"
        write_runtime_event(
            engine.paths,
            event_type=event_type,
            data={
                "idea_path": idea_path.as_posix(),
                "spec_id": spec_id,
                "error": str(exc),
            },
        )
        return None
    event_type = "legacy_idea_archived" if legacy else "idea_inbox_archived"
    write_runtime_event(
        engine.paths,
        event_type=event_type,
        data={
            "idea_path": idea_path.as_posix(),
            "archive_path": str(destination.relative_to(engine.paths.root)),
            "spec_id": spec_id,
        },
    )
    return destination


def archive_legacy_idea_file(engine: RuntimeEngine, idea_path: Path, *, spec_id: str) -> Path | None:
    try:
        idea_path.relative_to(engine.paths.root / "ideas" / "inbox")
    except ValueError:
        return None
    return archive_consumed_idea_file(engine, idea_path, spec_id=spec_id, legacy=True)


def archive_invalid_legacy_idea_file(
    engine: RuntimeEngine,
    idea_path: Path,
    *,
    reason: str,
    detail: str,
) -> tuple[Path, Path] | None:
    try:
        markdown_path, metadata_path = archive_invalid_legacy_idea_artifacts(
            engine.paths,
            idea_path,
            reason=reason,
            detail=detail,
        )
    except OSError as exc:
        write_runtime_event(
            engine.paths,
            event_type="legacy_idea_invalid_archive_failed",
            data={
                "idea_path": idea_path.as_posix(),
                "reason": reason,
                "error": str(exc),
            },
        )
        return None
    write_runtime_event(
        engine.paths,
        event_type="legacy_idea_invalid_archived",
        data={
            "idea_path": idea_path.as_posix(),
            "invalid_artifact": str(markdown_path.relative_to(engine.paths.root)),
            "diagnostic_artifact": str(metadata_path.relative_to(engine.paths.root)),
            "reason": reason,
        },
    )
    return markdown_path, metadata_path


def _is_legacy_idea_path(engine: RuntimeEngine, idea_path: Path) -> bool:
    try:
        idea_path.relative_to(engine.paths.root / "ideas" / "inbox")
    except ValueError:
        return False
    return True


def safe_spec_id_from_idea_content(content: str, *, fallback: str) -> str:
    title, _summary = derive_idea_title_summary(content, fallback=fallback)
    return stable_idea_id(title=title, markdown=content)


def safe_spec_id_from_idea_path(path: Path) -> str:
    if path.is_file():
        return safe_spec_id_from_idea_content(path.read_text(encoding="utf-8"), fallback=path.stem)
    return stable_idea_id(title=path.stem, markdown=path.stem)


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


def invalid_legacy_idea_reason(content: str) -> str | None:
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") and stripped.lstrip("#").strip():
            return None
        return "missing_h1_title"
    return "empty_markdown"


def legacy_shadowed_by_new_inbox_archive(engine: RuntimeEngine, idea_path: Path) -> bool:
    return (engine.paths.intake_ideas_archived_dir / idea_path.name).is_file()


def idea_normalized_metadata(
    engine: RuntimeEngine,
    *,
    spec_doc: SpecDocument,
    source_artifact_reference: str,
    transient_reference: str,
    archived_reference: str | None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "schema_version": 1,
        "root_idea_id": spec_doc.root_idea_id,
        "root_spec_id": spec_doc.root_spec_id,
        "spec_id": spec_doc.spec_id,
        "title": spec_doc.title,
        "summary": spec_doc.summary,
        "source_type": spec_doc.source_type,
        "source_id": spec_doc.source_id,
        "source_artifact": source_artifact_reference,
        "source_path": transient_reference,
        "references": list(spec_doc.references),
        "created_at": engine._now().isoformat(),
        "created_by": spec_doc.created_by,
    }
    if archived_reference is not None:
        metadata["archived_source"] = archived_reference
    return metadata


def _idea_references(
    *,
    durable_reference: str,
    transient_reference: str,
) -> tuple[str, ...]:
    if durable_reference == transient_reference:
        return (durable_reference,)
    return (durable_reference, transient_reference)


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
    "safe_spec_id_from_idea_content",
    "normalize_idea_watch_event",
    "archive_legacy_idea_file",
    "rebuild_watcher_session",
    "safe_spec_id_from_idea_path",
    "watcher_mode_value",
]
