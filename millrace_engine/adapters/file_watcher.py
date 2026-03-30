"""Watchdog and poll-based runtime input adapter."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from time import monotonic
from typing import Callable
import sys

from pydantic import Field, field_validator

from ..config import WatchRoot
from ..contracts import ContractModel
from ..paths import RuntimePaths

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    WATCHDOG_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - guarded by install and fallback
    FileSystemEvent = object  # type: ignore[assignment]
    FileSystemEventHandler = object  # type: ignore[assignment]
    Observer = None  # type: ignore[assignment]
    WATCHDOG_AVAILABLE = False


DEFAULT_DEBOUNCE_SECONDS = 0.5
FALLBACK_WATCH_POLL_SECONDS = 0.5


def watch_mode_supported(
    *,
    watchdog_available: bool = WATCHDOG_AVAILABLE,
    platform_name: str | None = None,
    python_version: tuple[int, int] | None = None,
) -> bool:
    """Return True when native watchdog observer mode is safe to use."""

    if not watchdog_available:
        return False
    platform_name = sys.platform if platform_name is None else platform_name
    python_version = sys.version_info[:2] if python_version is None else python_version
    # watchdog's macOS FSEvents path is unstable under CPython 3.14 in our
    # daemon/watcher teardown tests; prefer poll mode until that runtime
    # combination is proven safe.
    if platform_name == "darwin" and python_version >= (3, 14):
        return False
    return True


def _normalize_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        moment = value
    else:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _normalize_path(value: str | Path) -> Path:
    path = Path(value)
    return path.expanduser().resolve(strict=False)


def _signature(path: Path) -> tuple[int, int] | None:
    if not path.exists() or not path.is_file():
        return None
    stat = path.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _is_ignored_name(name: str) -> bool:
    return (
        not name
        or name.startswith(".")
        or name.endswith("~")
        or name.endswith(".tmp")
        or name.endswith(".swp")
        or name.endswith(".part")
        or name.endswith(".crdownload")
    )


class RuntimeInputKind(str, Enum):
    IDEA_SUBMITTED = "IDEA_SUBMITTED"
    BACKLOG_CHANGED = "BACKLOG_CHANGED"
    CONFIG_CHANGED = "CONFIG_CHANGED"
    STOP_AUTONOMY = "STOP_AUTONOMY"
    AUTONOMY_COMPLETE = "AUTONOMY_COMPLETE"
    CONTROL_COMMAND_AVAILABLE = "CONTROL_COMMAND_AVAILABLE"


class RuntimeInputEvent(ContractModel):
    """Normalized engine intake event."""

    kind: RuntimeInputKind
    path: Path
    timestamp: datetime
    raw_event: str

    @field_validator("path", mode="before")
    @classmethod
    def normalize_paths(cls, value: str | Path) -> Path:
        return _normalize_path(value)

    @field_validator("timestamp", mode="before")
    @classmethod
    def normalize_timestamps(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)


class RuntimeInputRouter:
    """Normalize raw filesystem paths into debounced logical events."""

    def __init__(
        self,
        paths: RuntimePaths,
        *,
        config_path: Path | None = None,
        watch_roots: tuple[WatchRoot, ...] | None = None,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        self.paths = paths
        self.config_path = config_path.expanduser().resolve(strict=False) if config_path is not None else None
        self.watch_roots = frozenset(watch_roots or tuple(WatchRoot))
        self.debounce_seconds = debounce_seconds
        self.monotonic_clock = monotonic_clock
        self._last_emitted: dict[tuple[RuntimeInputKind, str], float] = {}

    def _classify_path(self, path: Path) -> tuple[RuntimeInputKind, Path] | None:
        if _is_ignored_name(path.name):
            return None

        resolved = path.expanduser().resolve(strict=False)
        if WatchRoot.CONFIG_FILE in self.watch_roots and self.config_path is not None and resolved == self.config_path:
            return RuntimeInputKind.CONFIG_CHANGED, resolved
        if WatchRoot.AGENTS in self.watch_roots and resolved == self.paths.backlog_file:
            return RuntimeInputKind.BACKLOG_CHANGED, resolved
        if WatchRoot.AGENTS in self.watch_roots and resolved == (self.paths.agents_dir / "STOP_AUTONOMY").resolve(strict=False):
            return RuntimeInputKind.STOP_AUTONOMY, resolved
        if WatchRoot.AGENTS in self.watch_roots and resolved == (self.paths.agents_dir / "AUTONOMY_COMPLETE").resolve(strict=False):
            return RuntimeInputKind.AUTONOMY_COMPLETE, resolved
        if (
            WatchRoot.COMMANDS_INCOMING in self.watch_roots
            and resolved.parent == self.paths.commands_incoming_dir
            and resolved.suffix == ".json"
        ):
            return RuntimeInputKind.CONTROL_COMMAND_AVAILABLE, resolved
        ideas_dir = (self.paths.agents_dir / "ideas" / "raw").resolve(strict=False)
        if WatchRoot.IDEAS_RAW in self.watch_roots and resolved.parent == ideas_dir and resolved.is_file():
            return RuntimeInputKind.IDEA_SUBMITTED, resolved
        return None

    def submit_candidate(self, path: Path | str, *, raw_event: str, is_directory: bool = False) -> RuntimeInputEvent | None:
        """Return one debounced logical event or None."""

        if is_directory:
            return None

        classified = self._classify_path(Path(path))
        if classified is None:
            return None
        kind, resolved = classified
        key = (kind, resolved.as_posix())
        now = self.monotonic_clock()
        previous = self._last_emitted.get(key)
        if previous is not None and now - previous < self.debounce_seconds:
            return None
        self._last_emitted[key] = now
        return RuntimeInputEvent.model_validate(
            {
                "kind": kind,
                "path": resolved,
                "timestamp": datetime.now(timezone.utc),
                "raw_event": raw_event,
            }
        )


class _PollingSnapshot:
    def __init__(
        self,
        paths: RuntimePaths,
        *,
        config_path: Path | None,
        watch_roots: frozenset[WatchRoot],
    ) -> None:
        self.backlog = _signature(paths.backlog_file) if WatchRoot.AGENTS in watch_roots else None
        self.stop_marker = _signature(paths.agents_dir / "STOP_AUTONOMY") if WatchRoot.AGENTS in watch_roots else None
        self.complete_marker = (
            _signature(paths.agents_dir / "AUTONOMY_COMPLETE")
            if WatchRoot.AGENTS in watch_roots
            else None
        )
        self.idea_files = (
            self._dir_snapshot(paths.agents_dir / "ideas" / "raw")
            if WatchRoot.IDEAS_RAW in watch_roots
            else {}
        )
        self.command_files = (
            self._dir_snapshot(paths.commands_incoming_dir)
            if WatchRoot.COMMANDS_INCOMING in watch_roots
            else {}
        )
        self.config_file = (
            _signature(config_path)
            if WatchRoot.CONFIG_FILE in watch_roots and config_path is not None
            else None
        )

    @staticmethod
    def _dir_snapshot(root: Path) -> dict[Path, tuple[int, int]]:
        if not root.exists():
            return {}
        snapshot: dict[Path, tuple[int, int]] = {}
        for path in sorted(root.iterdir()):
            if not path.is_file() or _is_ignored_name(path.name):
                continue
            signature = _signature(path)
            if signature is not None:
                snapshot[path.resolve(strict=False)] = signature
        return snapshot


class PollingRuntimeInputSource:
    """Simple poll fallback using the same normalization layer."""

    def __init__(self, paths: RuntimePaths, router: RuntimeInputRouter) -> None:
        self.paths = paths
        self.router = router
        self.snapshot = _PollingSnapshot(
            paths,
            config_path=router.config_path,
            watch_roots=router.watch_roots,
        )

    def poll_once(self) -> list[RuntimeInputEvent]:
        """Return newly observed logical input events."""

        events: list[RuntimeInputEvent] = []

        current_backlog = _signature(self.paths.backlog_file)
        if (
            WatchRoot.AGENTS in self.router.watch_roots
            and current_backlog is not None
            and current_backlog != self.snapshot.backlog
        ):
            event = self.router.submit_candidate(self.paths.backlog_file, raw_event="poll")
            if event is not None:
                events.append(event)
        self.snapshot.backlog = current_backlog

        if WatchRoot.AGENTS in self.router.watch_roots:
            for marker_name, attr in (("STOP_AUTONOMY", "stop_marker"), ("AUTONOMY_COMPLETE", "complete_marker")):
                marker_path = self.paths.agents_dir / marker_name
                current = _signature(marker_path)
                previous = getattr(self.snapshot, attr)
                if current is not None and current != previous:
                    event = self.router.submit_candidate(marker_path, raw_event="poll")
                    if event is not None:
                        events.append(event)
                setattr(self.snapshot, attr, current)

        if WatchRoot.IDEAS_RAW in self.router.watch_roots:
            current_ideas = _PollingSnapshot._dir_snapshot(self.paths.agents_dir / "ideas" / "raw")
            for path, signature in current_ideas.items():
                if self.snapshot.idea_files.get(path) != signature:
                    event = self.router.submit_candidate(path, raw_event="poll")
                    if event is not None:
                        events.append(event)
            self.snapshot.idea_files = current_ideas

        if WatchRoot.COMMANDS_INCOMING in self.router.watch_roots:
            current_commands = _PollingSnapshot._dir_snapshot(self.paths.commands_incoming_dir)
            for path, signature in current_commands.items():
                if self.snapshot.command_files.get(path) != signature:
                    event = self.router.submit_candidate(path, raw_event="poll")
                    if event is not None:
                        events.append(event)
            self.snapshot.command_files = current_commands

        if WatchRoot.CONFIG_FILE in self.router.watch_roots and self.router.config_path is not None:
            current_config = _signature(self.router.config_path)
            if current_config is not None and current_config != self.snapshot.config_file:
                event = self.router.submit_candidate(self.router.config_path, raw_event="poll")
                if event is not None:
                    events.append(event)
            self.snapshot.config_file = current_config

        return events


class _WatchdogHandler(FileSystemEventHandler):
    """Thin intake-only watchdog callback bridge."""

    def __init__(self, router: RuntimeInputRouter, dispatch: Callable[[RuntimeInputEvent], None]) -> None:
        super().__init__()
        self.router = router
        self._emit = dispatch

    def _submit(self, path: str, *, raw_event: str, is_directory: bool) -> None:
        event = self.router.submit_candidate(path, raw_event=raw_event, is_directory=is_directory)
        if event is not None:
            self._emit(event)

    def on_created(self, event: FileSystemEvent) -> None:  # type: ignore[override]
        self._submit(event.src_path, raw_event="created", is_directory=event.is_directory)

    def on_modified(self, event: FileSystemEvent) -> None:  # type: ignore[override]
        self._submit(event.src_path, raw_event="modified", is_directory=event.is_directory)

    def on_moved(self, event: FileSystemEvent) -> None:  # type: ignore[override]
        destination = getattr(event, "dest_path", None) or event.src_path
        self._submit(destination, raw_event="moved", is_directory=event.is_directory)


class FileWatcherAdapter:
    """Runtime input adapter with watchdog and poll modes."""

    def __init__(
        self,
        paths: RuntimePaths,
        *,
        emit: Callable[[RuntimeInputEvent], None],
        config_path: Path | None = None,
        watch_roots: tuple[WatchRoot, ...] | None = None,
        mode: str = "watch",
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
        observer_factory: Callable[[], Observer] | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self.paths = paths
        self.emit = emit
        self.requested_mode = mode
        self.loop = loop
        self.router = RuntimeInputRouter(
            paths,
            config_path=config_path,
            watch_roots=watch_roots,
            debounce_seconds=debounce_seconds,
        )
        self.poller = PollingRuntimeInputSource(paths, self.router)
        self.observer_factory = observer_factory or Observer
        self._observer: Observer | None = None
        self._handler: _WatchdogHandler | None = None

    @property
    def mode(self) -> str:
        if self.requested_mode == "watch" and watch_mode_supported():
            return "watch"
        return "poll"

    def wakeup_timeout_seconds(self, configured_timeout: float) -> float:
        """Return the engine wakeup cadence for this watcher mode."""

        timeout = max(float(configured_timeout), 0.0)
        if self.requested_mode == "watch" and self.mode == "poll":
            return min(timeout, max(self.router.debounce_seconds, FALLBACK_WATCH_POLL_SECONDS))
        return timeout

    def start(self) -> None:
        """Start the watchdog observer when watch mode is active."""

        if self.mode != "watch":
            return
        if self._observer is not None:
            return

        self._handler = _WatchdogHandler(self.router, self._dispatch)
        observer = self.observer_factory()
        roots: set[Path] = set()
        if WatchRoot.IDEAS_RAW in self.router.watch_roots:
            roots.add((self.paths.agents_dir / "ideas" / "raw").resolve(strict=False))
        if WatchRoot.AGENTS in self.router.watch_roots:
            roots.add(self.paths.agents_dir.resolve(strict=False))
        if WatchRoot.COMMANDS_INCOMING in self.router.watch_roots:
            roots.add(self.paths.commands_incoming_dir.resolve(strict=False))
        if WatchRoot.CONFIG_FILE in self.router.watch_roots and self.router.config_path is not None:
            roots.add(self.router.config_path.parent.resolve(strict=False))
        for root in sorted(roots):
            root.mkdir(parents=True, exist_ok=True)
            observer.schedule(self._handler, str(root), recursive=False)
        observer.start()
        self._observer = observer

    def _dispatch(self, event: RuntimeInputEvent) -> None:
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.emit, event)
            return
        self.emit(event)

    def stop(self) -> None:
        """Stop and join the watchdog observer."""

        if self._observer is None:
            return
        self._observer.stop()
        self._observer.join(timeout=5.0)
        self._observer = None
        self._handler = None

    def poll_once(self) -> list[RuntimeInputEvent]:
        """Poll watched inputs once when poll mode is active."""

        return self.poller.poll_once()

    def handle_watchdog_path(
        self,
        path: Path | str,
        *,
        raw_event: str = "created",
        is_directory: bool = False,
    ) -> RuntimeInputEvent | None:
        """Testing hook for watchdog-path normalization."""

        return self.router.submit_candidate(path, raw_event=raw_event, is_directory=is_directory)
