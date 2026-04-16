"""Optional watcher wiring with safe poll-mode fallback behavior."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import WatcherMode
from millrace_ai.paths import WorkspacePaths, workspace_paths


@dataclass(frozen=True, slots=True)
class WatchTarget:
    """One watched surface for file change discovery."""

    target: str
    root: Path
    pattern: str


@dataclass(frozen=True, slots=True)
class WatchEvent:
    """One normalized file-change event emitted by watcher polling."""

    target: str
    path: Path
    event_kind: str
    observed_at: datetime


@dataclass(slots=True)
class PollWatcher:
    """Filesystem polling adapter with deterministic ordering and debounce."""

    targets: tuple[WatchTarget, ...]
    debounce_seconds: float
    _fingerprints: dict[str, tuple[int, int]] = field(default_factory=dict)
    _last_emitted_at: dict[tuple[str, str], float] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        targets: Iterable[WatchTarget],
        debounce_ms: int,
        now: datetime | None = None,
    ) -> PollWatcher:
        watcher = cls(targets=tuple(targets), debounce_seconds=max(0, debounce_ms) / 1000.0)
        watcher._prime(now=now)
        return watcher

    def poll_once(self, *, now: datetime | None = None) -> tuple[WatchEvent, ...]:
        observed_at = _coerce_time(now)
        timestamp = observed_at.timestamp()
        seen_paths: set[str] = set()
        emitted: list[WatchEvent] = []

        for target in sorted(self.targets, key=lambda item: (item.target, item.root.as_posix())):
            for path, fingerprint in self._iter_target_files(target):
                canonical = path.as_posix()
                seen_paths.add(canonical)

                previous = self._fingerprints.get(canonical)
                if previous == fingerprint:
                    continue

                debounce_key = (target.target, canonical)
                last_emitted = self._last_emitted_at.get(debounce_key)
                if last_emitted is not None and (timestamp - last_emitted) < self.debounce_seconds:
                    continue

                self._fingerprints[canonical] = fingerprint
                self._last_emitted_at[debounce_key] = timestamp
                emitted.append(
                    WatchEvent(
                        target=target.target,
                        path=path,
                        event_kind="changed",
                        observed_at=observed_at,
                    )
                )

        stale_paths = set(self._fingerprints) - seen_paths
        for stale in stale_paths:
            self._fingerprints.pop(stale, None)
            keys_to_drop = [key for key in self._last_emitted_at if key[1] == stale]
            for key in keys_to_drop:
                self._last_emitted_at.pop(key, None)

        emitted.sort(key=lambda event: (event.target, event.path.as_posix()))
        return tuple(emitted)

    def _prime(self, *, now: datetime | None = None) -> None:
        _ = _coerce_time(now)
        for target in self.targets:
            for path, fingerprint in self._iter_target_files(target):
                self._fingerprints[path.as_posix()] = fingerprint

    def _iter_target_files(self, target: WatchTarget) -> Iterable[tuple[Path, tuple[int, int]]]:
        if not target.root.exists() or not target.root.is_dir():
            return

        for path in sorted(target.root.glob(target.pattern), key=lambda item: item.name):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            yield path.resolve(), (stat.st_mtime_ns, stat.st_size)


@dataclass(slots=True)
class WatcherSession:
    """Runtime watcher session abstraction with poll fallback."""

    mode: WatcherMode
    targets: tuple[WatchTarget, ...]
    poller: PollWatcher | None = None

    def poll_once(self, *, now: datetime | None = None) -> tuple[WatchEvent, ...]:
        if self.poller is None:
            return ()
        return self.poller.poll_once(now=now)

    def close(self) -> None:
        self.poller = None


def resolve_watcher_mode(
    config: RuntimeConfig,
    *,
    watchdog_available: bool | None = None,
) -> WatcherMode:
    """Resolve watcher mode from config with deterministic watchdog fallback."""

    if not config.watchers.enabled:
        return WatcherMode.OFF

    available = _watchdog_available() if watchdog_available is None else watchdog_available
    return WatcherMode.WATCH if available else WatcherMode.POLL


def build_watch_targets(
    target: WorkspacePaths | Path | str,
    *,
    config: RuntimeConfig,
    config_path: Path | None = None,
) -> tuple[WatchTarget, ...]:
    """Build watched surfaces using current config/contracts without side effects."""

    paths = target if isinstance(target, WorkspacePaths) else workspace_paths(target)

    if not config.watchers.enabled:
        return ()

    targets: list[WatchTarget] = []

    config_file = (config_path or (paths.runtime_root / "millrace.toml")).expanduser().resolve()
    targets.append(WatchTarget(target="config", root=config_file.parent, pattern=config_file.name))

    targets.append(
        WatchTarget(
            target="tasks_queue",
            root=paths.tasks_queue_dir,
            pattern="*.md",
        )
    )

    if config.watchers.watch_specs_queue:
        targets.append(
            WatchTarget(
                target="specs_queue",
                root=paths.specs_queue_dir,
                pattern="*.md",
            )
        )

    if config.watchers.watch_ideas_inbox:
        targets.append(
            WatchTarget(
                target="ideas_inbox",
                root=(paths.root / "ideas" / "inbox"),
                pattern="*.md",
            )
        )

    return tuple(targets)


def build_watcher_session(
    target: WorkspacePaths | Path | str,
    *,
    config: RuntimeConfig,
    config_path: Path | None = None,
    watchdog_available: bool | None = None,
    now: datetime | None = None,
) -> WatcherSession:
    """Construct watcher session that can always fall back to deterministic polling."""

    mode = resolve_watcher_mode(config, watchdog_available=watchdog_available)
    targets = build_watch_targets(target, config=config, config_path=config_path)

    if mode is WatcherMode.OFF:
        return WatcherSession(mode=WatcherMode.OFF, targets=(), poller=None)

    # Even when watchdog mode is selected, poll remains available as safe fallback.
    poller = PollWatcher.create(
        targets=targets,
        debounce_ms=config.watchers.debounce_ms,
        now=now,
    )
    return WatcherSession(mode=mode, targets=targets, poller=poller)


def _watchdog_available() -> bool:
    try:
        import watchdog.observers  # noqa: F401
    except Exception:
        return False
    return True


def _coerce_time(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "PollWatcher",
    "WatchEvent",
    "WatchTarget",
    "WatcherSession",
    "build_watch_targets",
    "build_watcher_session",
    "resolve_watcher_mode",
]
