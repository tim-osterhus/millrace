from __future__ import annotations

from pathlib import Path
import json

from millrace_engine.events import EventBus, EventRecord, EventSource, EventType
from millrace_engine.planes.research import ResearchStubPlane
from tests.support import load_workspace_fixture, runtime_paths


class CaptureSubscriber:
    def __init__(self) -> None:
        self.events: list[EventRecord] = []

    def handle(self, event: EventRecord) -> None:
        self.events.append(event)


def test_research_stub_writes_deferred_breadcrumb_and_emits_follow_on_events(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    paths = runtime_paths(config_path)
    capture = CaptureSubscriber()
    research = ResearchStubPlane(paths)
    bus = EventBus([capture, research])
    research.bind_emitter(lambda event_type, payload: bus.emit(event_type, source=EventSource.RESEARCH, payload=payload))

    bus.emit(
        EventType.IDEA_SUBMITTED,
        source=EventSource.ADAPTER,
        payload={"path": (workspace / "agents/ideas/raw/idea.md").as_posix()},
    )

    deferred_files = sorted(paths.deferred_dir.glob("*.json"))
    assert research.pending_count() == 1
    assert len(deferred_files) == 1
    breadcrumb = json.loads(deferred_files[0].read_text(encoding="utf-8"))
    assert breadcrumb["event_type"] == EventType.IDEA_SUBMITTED.value
    assert breadcrumb["status"] == "deferred"

    event_types = [event.type for event in capture.events]
    assert EventType.IDEA_SUBMITTED in event_types
    assert EventType.RESEARCH_RECEIVED in event_types
    assert EventType.RESEARCH_DEFERRED in event_types


def test_research_stub_accepts_needs_research_and_ignores_unsupported_events(tmp_path: Path) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "needs_research")
    paths = runtime_paths(config_path)
    research = ResearchStubPlane(paths)

    research.handle(
        EventRecord.model_validate(
            {
                "type": EventType.NEEDS_RESEARCH,
                "timestamp": "2026-03-17T12:00:00Z",
                "source": EventSource.EXECUTION,
                "payload": {"task_id": "task-123"},
            }
        )
    )
    research.handle(
        EventRecord.model_validate(
            {
                "type": EventType.TASK_PROMOTED,
                "timestamp": "2026-03-17T12:00:01Z",
                "source": EventSource.EXECUTION,
                "payload": {"task_id": "task-456"},
            }
        )
    )

    assert research.pending_count() == 1
