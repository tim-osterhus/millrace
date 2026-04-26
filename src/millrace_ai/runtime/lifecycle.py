"""Runtime startup, shutdown, and ownership-lock helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import fingerprint_runtime_config, load_runtime_config
from millrace_ai.contracts import Plane
from millrace_ai.errors import RuntimeLifecycleError
from millrace_ai.events import write_runtime_event
from millrace_ai.runtime_lock import (
    RuntimeOwnershipLockError,
    acquire_runtime_ownership_lock,
    release_runtime_ownership_lock,
)
from millrace_ai.state_store import load_recovery_counters, load_snapshot, save_snapshot

if TYPE_CHECKING:
    from millrace_ai.contracts import RuntimeSnapshot
    from millrace_ai.runtime.engine import RuntimeEngine


def close_engine(engine: RuntimeEngine) -> None:
    """Release any runtime-owned resources held by the engine session."""

    engine._close_watcher_session()
    engine._release_daemon_ownership_lock(force=False)


def startup_engine(engine: RuntimeEngine) -> RuntimeSnapshot:
    """Load config, compile the active mode, and reconcile stale runtime state."""

    lock_acquired = False
    try:
        engine.config = load_runtime_config(engine.config_path)
        if engine._requires_daemon_ownership_lock():
            lock_acquired = engine._acquire_daemon_ownership_lock()
        engine._rebuild_watcher_session()

        compile_outcome = compile_and_persist_workspace_plan(
            engine.paths,
            config=engine.config,
            requested_mode_id=engine.mode_id,
            assets_root=engine.assets_root,
            compile_if_needed=True,
            refuse_stale_last_known_good=True,
        )
        compiled_plan = compile_outcome.active_plan
        if compiled_plan is None:
            errors = ", ".join(compile_outcome.diagnostics.errors) or "compile failed"
            raise RuntimeLifecycleError(errors)

        engine.compiled_plan = compiled_plan

        engine.snapshot = load_snapshot(engine.paths)
        engine.counters = load_recovery_counters(engine.paths)
        engine._run_reconciliation_if_needed()

        assert engine.snapshot is not None
        snapshot = engine.snapshot.model_copy(
            update={
                "runtime_mode": engine.config.runtime.run_style,
                "process_running": True,
                "active_mode_id": compiled_plan.mode_id,
                "execution_loop_id": compiled_plan.execution_loop_id,
                "planning_loop_id": compiled_plan.planning_loop_id,
                "learning_loop_id": compiled_plan.learning_loop_id,
                "loop_ids_by_plane": compiled_plan.loop_ids_by_plane,
                "compiled_plan_id": compiled_plan.compiled_plan_id,
                "compiled_plan_path": str(
                    (engine.paths.state_dir / "compiled_plan.json").relative_to(engine.paths.root)
                ),
                "queue_depth_execution": engine._execution_queue_depth(),
                "queue_depth_planning": engine._planning_queue_depth(),
                "queue_depth_learning": engine._learning_queue_depth(),
                "queue_depths_by_plane": {
                    Plane.EXECUTION: engine._execution_queue_depth(),
                    Plane.PLANNING: engine._planning_queue_depth(),
                    Plane.LEARNING: engine._learning_queue_depth(),
                },
                "config_version": fingerprint_runtime_config(engine.config),
                "watcher_mode": engine._watcher_mode_value(),
                "last_reload_outcome": None,
                "last_reload_error": None,
                "updated_at": engine._now(),
            }
        )

        engine.snapshot = snapshot
        save_snapshot(engine.paths, snapshot)
        write_runtime_event(
            engine.paths,
            event_type="runtime_started",
            data={
                "mode_id": snapshot.active_mode_id,
                "compiled_plan_id": snapshot.compiled_plan_id,
                "process_running": snapshot.process_running,
            },
        )
        return snapshot
    except Exception:
        engine._close_watcher_session()
        if lock_acquired:
            engine._release_daemon_ownership_lock(force=True)
        raise


def requires_daemon_ownership_lock(engine: RuntimeEngine) -> bool:
    return engine.config is not None


def acquire_daemon_ownership_lock(engine: RuntimeEngine) -> bool:
    if engine._daemon_lock_session_id is not None:
        return False

    session_id = uuid4().hex
    try:
        acquire_runtime_ownership_lock(engine.paths, owner_session_id=session_id)
    except RuntimeOwnershipLockError as exc:
        write_runtime_event(
            engine.paths,
            event_type="runtime_daemon_lock_denied",
            data={"reason": str(exc)},
        )
        raise RuntimeLifecycleError(str(exc)) from exc

    engine._daemon_lock_session_id = session_id
    write_runtime_event(
        engine.paths,
        event_type="runtime_daemon_lock_acquired",
        data={"session_id": session_id},
    )
    return True


def release_daemon_ownership_lock(engine: RuntimeEngine, *, force: bool) -> bool:
    session_id = engine._daemon_lock_session_id
    if session_id is None and not force:
        return False
    released = release_runtime_ownership_lock(
        engine.paths,
        owner_session_id=session_id,
        force=force,
    )
    if released:
        write_runtime_event(
            engine.paths,
            event_type="runtime_daemon_lock_released",
            data={"session_id": session_id},
        )
    engine._daemon_lock_session_id = None
    return released


__all__ = [
    "acquire_daemon_ownership_lock",
    "close_engine",
    "release_daemon_ownership_lock",
    "requires_daemon_ownership_lock",
    "startup_engine",
]
