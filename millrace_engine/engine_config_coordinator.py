"""Config lifecycle ownership for the runtime engine."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from .config import (
    ConfigApplyBoundary,
    LoadedConfig,
    build_runtime_paths,
    diff_config_fields,
    load_engine_config,
)
from .control_common import ControlError
from .control_mutations import _assert_reload_safe
from .control_models import OperationResult
from .control_reports import config_hash
from .events import EventSource, EventType
from .paths import RuntimePaths


@dataclass(frozen=True)
class EngineConfigCoordinatorState:
    """Snapshot of engine-owned config lifecycle state."""

    loaded: LoadedConfig
    paths: RuntimePaths
    pending_loaded: LoadedConfig | None
    previous_loaded: LoadedConfig | None
    pending_boundary: ConfigApplyBoundary | None
    pending_changed_fields: tuple[str, ...]
    rollback_armed: bool


@dataclass(frozen=True)
class EngineConfigCoordinatorHooks:
    """Explicit engine-owned side effects used by config coordination."""

    emit_event: Callable[[EventType, EventSource, dict[str, object]], None]
    install_loaded_config: Callable[[LoadedConfig, RuntimePaths], None]
    sync_ready_research_dispatch: Callable[[str], object | None]


class EngineConfigCoordinator:
    """Own config apply, reload, rejection, and rollback workflows."""

    def __init__(
        self,
        *,
        config_path: Path,
        initial_loaded: LoadedConfig,
        initial_paths: RuntimePaths,
        hooks: EngineConfigCoordinatorHooks,
    ) -> None:
        self._config_path = config_path
        self._hooks = hooks
        self._lock = RLock()
        self._loaded = initial_loaded
        self._paths = initial_paths
        self._pending_loaded: LoadedConfig | None = None
        self._previous_loaded: LoadedConfig | None = None
        self._pending_boundary: ConfigApplyBoundary | None = None
        self._pending_changed_fields: tuple[str, ...] = ()
        self._rollback_armed = False

    @property
    def loaded(self) -> LoadedConfig:
        return self._loaded

    @property
    def paths(self) -> RuntimePaths:
        return self._paths

    @property
    def pending_loaded(self) -> LoadedConfig | None:
        return self._pending_loaded

    @property
    def previous_loaded(self) -> LoadedConfig | None:
        return self._previous_loaded

    @property
    def pending_boundary(self) -> ConfigApplyBoundary | None:
        return self._pending_boundary

    @property
    def pending_changed_fields(self) -> tuple[str, ...]:
        return self._pending_changed_fields

    @property
    def rollback_armed(self) -> bool:
        return self._rollback_armed

    def snapshot_state(self) -> EngineConfigCoordinatorState:
        with self._lock:
            return EngineConfigCoordinatorState(
                loaded=self._loaded,
                paths=self._paths,
                pending_loaded=self._pending_loaded,
                previous_loaded=self._previous_loaded,
                pending_boundary=self._pending_boundary,
                pending_changed_fields=self._pending_changed_fields,
                rollback_armed=self._rollback_armed,
            )

    def queue_or_apply_reloaded_config(
        self,
        loaded: LoadedConfig,
        *,
        command_id: str | None,
        key: str | None = None,
    ) -> tuple[OperationResult, bool]:
        applied_operation: OperationResult | None = None
        restart_watcher = False
        with self._lock:
            changed_fields = diff_config_fields(self._loaded.config, loaded.config)
            boundary = self._loaded.config.boundaries.classify_fields(changed_fields)
            if not changed_fields or boundary is None:
                return (
                    OperationResult(mode="direct", applied=False, message="config already current"),
                    False,
                )
            if boundary is ConfigApplyBoundary.STARTUP_ONLY:
                raise ControlError(f"cannot change startup-only field at runtime: {changed_fields[0]}")

            if boundary is ConfigApplyBoundary.LIVE_IMMEDIATE:
                self._emit_config_changed(
                    command_id=command_id,
                    boundary=boundary,
                    changed_fields=changed_fields,
                    loaded=loaded,
                    key=key,
                )
                previous_hash, active_hash = self._apply_loaded_config_locked(
                    loaded,
                    changed_fields=changed_fields,
                )
                self._emit_config_applied(
                    command_id=command_id,
                    boundary=boundary,
                    changed_fields=changed_fields,
                    active_hash=active_hash,
                    previous_hash=previous_hash,
                )
                applied_operation = OperationResult(
                    mode="direct",
                    applied=True,
                    message="config applied immediately",
                    payload={
                        "boundary": boundary.value,
                        "config_hash": active_hash,
                        "previous_config_hash": previous_hash,
                        "changed_fields": changed_fields,
                        **({"key": key} if key is not None else {}),
                    },
                )
                restart_watcher = self._watcher_restart_required(changed_fields)
            else:
                self._pending_loaded = loaded
                self._pending_boundary = boundary
                self._pending_changed_fields = changed_fields
                self._emit_config_changed(
                    command_id=command_id,
                    boundary=boundary,
                    changed_fields=changed_fields,
                    loaded=loaded,
                    key=key,
                )
                return (
                    OperationResult(
                        mode="direct",
                        applied=True,
                        message=f"config queued for {boundary.value}",
                        payload={
                            "boundary": boundary.value,
                            "pending_config_hash": config_hash(loaded.config),
                            "changed_fields": changed_fields,
                            **({"key": key} if key is not None else {}),
                        },
                    ),
                    False,
                )

        self._hooks.sync_ready_research_dispatch("config-applied")
        if applied_operation is None:
            raise RuntimeError("live config apply did not produce an operation result")
        return applied_operation, restart_watcher

    def apply_pending_config_if_due(
        self,
        boundary: ConfigApplyBoundary,
        *,
        command_id: str | None = None,
    ) -> bool:
        with self._lock:
            if self._pending_loaded is None or self._pending_boundary is None:
                return False
            if not self._boundary_allows(boundary, self._pending_boundary):
                return False
            changed_fields = self._pending_changed_fields
            applied_boundary = self._pending_boundary
            previous_hash, active_hash = self._apply_loaded_config_locked(
                self._pending_loaded,
                changed_fields=changed_fields,
            )
        self._emit_config_applied(
            command_id=command_id,
            boundary=applied_boundary,
            changed_fields=changed_fields,
            active_hash=active_hash,
            previous_hash=previous_hash,
        )
        self._hooks.sync_ready_research_dispatch("config-applied")
        return self._watcher_restart_required(changed_fields)

    def clear_rollback_guard(self) -> None:
        with self._lock:
            self._previous_loaded = None
            self._rollback_armed = False

    def rollback_active_config(self, reason: str) -> bool:
        with self._lock:
            if not self._rollback_armed or self._previous_loaded is None:
                return False
            failed_hash = config_hash(self._loaded.config)
            changed_fields = diff_config_fields(self._previous_loaded.config, self._loaded.config)
            boundary = self._previous_loaded.config.boundaries.classify_fields(
                changed_fields
            ) or ConfigApplyBoundary.STAGE_BOUNDARY
            self._install_loaded_config(self._previous_loaded)
            restored_hash = config_hash(self._loaded.config)
            self._previous_loaded = None
            self._pending_loaded = None
            self._pending_boundary = None
            self._pending_changed_fields = ()
            self._rollback_armed = False
        self._emit_config_applied(
            command_id=None,
            boundary=boundary,
            changed_fields=changed_fields,
            active_hash=restored_hash,
            previous_hash=failed_hash,
            rollback=True,
            reason=reason,
        )
        return True

    async def reload_config_from_disk(self, *, trigger_path: Path | None = None) -> tuple[bool, bool]:
        reloaded: LoadedConfig | None = None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                reloaded = load_engine_config(self._config_path)
                _assert_reload_safe(self._loaded, reloaded)
            except Exception as exc:  # noqa: BLE001 - config reload must not crash the daemon
                last_error = exc
                if attempt == 2:
                    self._emit_config_rejected(reason=str(exc), path=trigger_path)
                    return False, False
                await asyncio.sleep(0.1 * (attempt + 1))
                continue
            break

        if reloaded is None:
            if last_error is not None:
                self._emit_config_rejected(reason=str(last_error), path=trigger_path)
            return False, False

        operation, restart_watcher = self.queue_or_apply_reloaded_config(
            reloaded,
            command_id=None,
        )
        return operation.applied, restart_watcher

    def _install_loaded_config(self, loaded: LoadedConfig) -> None:
        paths = build_runtime_paths(loaded.config)
        self._loaded = loaded
        self._paths = paths
        self._hooks.install_loaded_config(loaded, paths)

    def _boundary_allows(
        self,
        current: ConfigApplyBoundary,
        pending: ConfigApplyBoundary,
    ) -> bool:
        order = {
            ConfigApplyBoundary.LIVE_IMMEDIATE: 0,
            ConfigApplyBoundary.STAGE_BOUNDARY: 1,
            ConfigApplyBoundary.CYCLE_BOUNDARY: 2,
            ConfigApplyBoundary.STARTUP_ONLY: 3,
        }
        return order[pending] <= order[current]

    def _watcher_restart_required(self, changed_fields: tuple[str, ...]) -> bool:
        return any(
            field == "engine.idle_mode"
            or field.startswith("engine.idle_mode.")
            or field == "watchers"
            or field.startswith("watchers.")
            for field in changed_fields
        )

    def _apply_loaded_config_locked(
        self,
        loaded: LoadedConfig,
        *,
        changed_fields: tuple[str, ...],
    ) -> tuple[str, str]:
        previous_hash = config_hash(self._loaded.config)
        self._previous_loaded = self._loaded
        self._pending_loaded = None
        self._pending_boundary = None
        self._pending_changed_fields = ()
        self._rollback_armed = True
        self._install_loaded_config(loaded)
        return previous_hash, config_hash(self._loaded.config)

    def _emit_config_changed(
        self,
        *,
        command_id: str | None,
        boundary: ConfigApplyBoundary,
        changed_fields: tuple[str, ...],
        loaded: LoadedConfig,
        key: str | None = None,
    ) -> None:
        payload = {
            "command_id": command_id,
            "boundary": boundary.value,
            "changed_fields": changed_fields,
            "pending_config_hash": config_hash(loaded.config),
        }
        if key is not None:
            payload["key"] = key
        self._hooks.emit_event(
            EventType.CONFIG_CHANGED,
            EventSource.CONTROL,
            payload,
        )

    def _emit_config_applied(
        self,
        *,
        command_id: str | None,
        boundary: ConfigApplyBoundary,
        changed_fields: tuple[str, ...],
        active_hash: str,
        previous_hash: str | None,
        rollback: bool = False,
        reason: str | None = None,
    ) -> None:
        payload = {
            "command_id": command_id,
            "boundary": boundary.value,
            "changed_fields": changed_fields,
            "config_hash": active_hash,
            "rollback": rollback,
        }
        if previous_hash is not None:
            payload["previous_config_hash"] = previous_hash
        if reason is not None:
            payload["reason"] = reason
        self._hooks.emit_event(
            EventType.CONFIG_APPLIED,
            EventSource.CONTROL,
            payload,
        )

    def _emit_config_rejected(self, *, reason: str, path: Path | None = None) -> None:
        payload: dict[str, object] = {
            "rejected": True,
            "reason": reason,
        }
        if path is not None:
            payload["path"] = path
        self._hooks.emit_event(
            EventType.CONFIG_CHANGED,
            EventSource.ADAPTER,
            payload,
        )
