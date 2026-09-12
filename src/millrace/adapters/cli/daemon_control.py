"""Public Core daemon lifecycle; the socket never dispatches runtime work."""

from __future__ import annotations

import os
import signal
import threading
import time
from contextvars import ContextVar
from importlib import metadata
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from millrace.adapters.cli.context import (
    CliCommandError,
    CliWorkspacePaths,
    OpenRuntimeContext,
    open_runtime_context,
)
from millrace.adapters.cli.daemon_listener import (
    DaemonListener,
    endpoint_path,
    exchange,
)
from millrace.adapters.cli.daemon_process import observe_process, process_identity
from millrace.adapters.cli.output import CliSuccess, ExitCode, success_result
from millrace.contracts.controls import ControlRequest, canonical_json, revision_field
from millrace.contracts.daemon_control import runtime_identity
from millrace.substrate.errors import ControlOperationError

ACTIVE_LIFECYCLE: ContextVar[DaemonLifecycle | None] = ContextVar(
    "core_daemon_lifecycle", default=None
)


def _open(paths: CliWorkspacePaths) -> OpenRuntimeContext:
    return open_runtime_context(
        SimpleNamespace(
            workspace=str(paths.workspace_path),
            db=str(paths.db_path),
            cas=str(paths.cas_path),
        ),
        command="daemon.inspect",
    )


def _target(record: dict[str, Any], revision: int) -> dict[str, Any]:
    return {
        **{
            key: record[key]
            for key in (
                "workspace_id",
                "instance_id",
                "store_epoch",
                "daemon_id",
                "daemon_generation",
                "process_nonce",
                "plan_fingerprint",
            )
        },
        "expected_source_revision": revision,
        "expected_runtime": record["runtime"],
    }


def _fence(session: Any) -> dict[str, Any]:
    return {
        key: getattr(session, key)
        for key in (
            "session_id",
            "run_id",
            "dispatch_generation",
            "session_fencing_token",
        )
    }


def _projection(
    record: dict[str, Any], revision: int, current_plan: str | None = None
) -> dict[str, Any]:
    summary = record["final_summary"]
    process = observe_process(record["process"])
    cleanup = "pending" if summary is None else summary["runtime_cleanup"]
    clean = (
        record["status"] == "shutdown_complete"
        and process == "not_live"
        and cleanup in {"complete", "not_required"}
    )
    target = _target(record, revision)
    target["plan_fingerprint"] = current_plan
    return {
        "target": target,
        "status": "stopped_clean"
        if clean
        else ("unknown" if process != "live" and summary is None else record["status"]),
        "durable_status": record["status"],
        "daemon_process": process,
        "runtime_cleanup": cleanup,
        "control_acceptance": "accepted_pending"
        if record["stop_key"]
        else "not_requested",
        "stop_key": record["stop_key"],
        "launch_correlation_id": record["launch_correlation_id"],
        "process": record["process"],
        "final_summary": summary,
        "session_count": record["session_count"],
        "source_revision": revision,
        "observed_at_ns": time.time_ns(),
        "readiness": {
            "state": record["status"],
            "daemon_revision": record["revision"],
            "source_revision": revision,
            "basis": "durable_startup_classification",
            "fresh_challenge_required": True,
        },
        "runtime_progress": {
            "availability": "available"
            if record.get("runtime_progress")
            else ("no_work_yet" if "runtime_progress" in record else "unavailable"),
            "last_runtime_progress_at": None
            if not record.get("runtime_progress")
            else record["runtime_progress"]["captured_at_ns"],
            "clock": "unix_nanoseconds_at_runtime_transaction",
            "basis": "accepted_durable_workflow_transition",
            "evidence": record.get("runtime_progress"),
            "reason": "legacy_capture_absent"
            if "runtime_progress" not in record
            else None,
        },
        "consistency": "store_snapshot_with_separate_process_observation",
        "ready": False,
        "challenge_verified": False,
        "relaunch_allowed": clean,
    }


class DaemonLifecycle:
    """Owned only by the public daemon launch after the legacy exclusive lock."""

    def __init__(self, paths: CliWorkspacePaths, launch_correlation_id: str) -> None:
        self.paths = paths
        self.launch_correlation_id = launch_correlation_id
        self.scope: dict[str, Any] = {}
        self.stop_event: threading.Event | None = None
        self.listener: DaemonListener | None = None

    def initialize(self) -> None:
        runtime = _open(self.paths)
        try:
            revision = runtime.store.control_identity()["source_revision"]
            state = runtime.store.load_runtime_state(
                runtime.cas_store, _optimistic=True
            )
            records = runtime.store.daemon_records()
            if records and not _projection(records[-1], revision)["relaunch_allowed"]:
                raise ControlOperationError("daemon_aftermath_unknown")
            process = process_identity(os.getpid())
            if process["status"] != "live" or process["uid"] != os.geteuid():
                raise ControlOperationError("daemon_process_identity_unknown")
            self.scope = runtime.store.register_daemon(
                {
                    "daemon_id": str(uuid4()),
                    "process_nonce": str(uuid4()),
                    "process": process,
                    "launch_correlation_id": self.launch_correlation_id,
                    "runtime": runtime_identity(
                        metadata.version("millrace-ai"),
                        int(runtime.store.schema_metadata()["store_schema_version"]),
                    ),
                },
                revision,
            )
            fences = [
                _fence(session)
                for session in state.runner_sessions.values()
                if session.state
                in {
                    "created",
                    "starting",
                    "running",
                    "cancellation_requested",
                    "terminating",
                    "lost",
                }
                or session.cleanup_disposition not in {"complete", "not_required"}
            ]
            self.scope = runtime.store.attach_daemon_sessions(
                self.scope, fences, role="startup_reconciliation"
            )
        finally:
            runtime.close()
        self.listener = DaemonListener(
            endpoint_path(self.paths.workspace_path), self._handle
        )
        self.listener.start()

    def ready(self, status: str, stop_event: threading.Event) -> None:
        self.stop_event = stop_event
        runtime = _open(self.paths)
        try:
            runtime.store.update_daemon(self.scope, status=status)
        finally:
            runtime.close()
        self.stop_requested()

    def stop_requested(self) -> bool:
        runtime = _open(self.paths)
        try:
            record = runtime.store.daemon_records()[-1]
            if record["daemon_id"] != self.scope["daemon_id"]:
                raise ControlOperationError("daemon_incarnation_mismatch")
            if record["stop_key"] is not None:
                if self.stop_event is not None:
                    self.stop_event.set()
                return True
            return False
        finally:
            runtime.close()

    def signal_stop(self) -> None:
        """Called by normal loop code, never by the signal handler itself."""
        runtime = _open(self.paths)
        try:
            runtime.store.signal_daemon_stop(self.scope)
        finally:
            runtime.close()

    def _handle(self, message: dict[str, Any], deadline: float) -> dict[str, Any]:
        if process_identity(os.getpid()) != self.scope["process"]:
            raise ControlOperationError("daemon_process_identity_unknown")
        runtime = _open(self.paths)
        try:
            if message["method"] == "native_control":
                from millrace.adapters.cli.run_controls import admit_native_control

                request = ControlRequest.parse(canonical_json(message["request"]))
                if request.payload["action"] not in {"runs.pause", "runs.resume"}:
                    raise ControlOperationError("native_action_unsupported")
                return {
                    "challenge": message["challenge"],
                    "operation": admit_native_control(runtime, request, deadline),
                }
            result = None
            if message["method"] == "stop":
                request = ControlRequest.parse(canonical_json(message["request"]))
                result = runtime.store.accept_daemon_stop(
                    self.scope, request, deadline=deadline
                )
                if (
                    result["receipt"]["accepted"]
                    and self.stop_event is not None
                    and all(
                        result["receipt"]["target"][key] == self.scope[key]
                        for key in ("daemon_id", "daemon_generation", "process_nonce")
                    )
                ):
                    self.stop_event.set()
            snapshot = runtime.store.daemon_snapshot(
                after_session=message.get("after_session", 0),
                expected_revision=message.get("expected_source_revision"),
            )
            record = snapshot["record"]
            if record is None or record["daemon_id"] != self.scope["daemon_id"]:
                raise ControlOperationError("daemon_incarnation_mismatch")
            projection = _projection(
                record,
                snapshot["identity"]["source_revision"],
                snapshot["default_plan"],
            )
            projection.update(snapshot["page"])
            projection["challenge_verified"] = True
            projection["ready"] = (
                record["status"] in {"ready_idle", "ready_active"}
                and snapshot["default_plan"] == record["plan_fingerprint"]
            )
            return {
                "challenge": message["challenge"],
                "daemon": projection,
                "operation": result,
            }
        finally:
            runtime.close()

    def finish(self, stopped_reason: str) -> None:
        listener_clean = self.listener is not None and self.listener.close()
        if not self.scope:
            return
        runtime = _open(self.paths)
        try:
            revision = runtime.store.control_identity()["source_revision"]
            state = runtime.store.load_runtime_state(
                runtime.cas_store, _optimistic=True
            )
            owned = runtime.store.daemon_owned_sessions(self.scope)
            results = []
            for row in owned:
                fence = {
                    key: row[key]
                    for key in (
                        "session_id",
                        "run_id",
                        "dispatch_generation",
                        "session_fencing_token",
                    )
                }
                session = state.runner_sessions.get(fence["session_id"])
                disposition = "unknown"
                if session is not None and _fence(session) == fence:
                    if (
                        session.state == "created"
                        and session.start_intent_at is None
                        and runtime.store.daemon_budget_id_for_session(
                            session.session_id
                        )
                        is None
                    ):
                        disposition = "not_required"
                    else:
                        completion = state.runner_session_completions.get(
                            session.session_id
                        )
                        if (
                            completion is not None
                            and _fence(completion) == fence
                            and completion.terminal_state == session.state
                            and completion.cleanup_disposition
                            == session.cleanup_disposition
                        ):
                            disposition = session.cleanup_disposition
                results.append({"fence": fence, "cleanup": disposition})
            runtime.store.finish_daemon(
                self.scope,
                {
                    "listener_teardown": "complete" if listener_clean else "unknown",
                    "stopped_reason": stopped_reason,
                },
                results,
                revision,
            )
        finally:
            runtime.close()


def inspect_daemon(
    paths: CliWorkspacePaths,
    *,
    deadline: float,
    after_session: int = 0,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    runtime = _open(paths)
    try:
        snapshot = runtime.store.daemon_snapshot(
            after_session=after_session, expected_revision=expected_revision
        )
        record = snapshot["record"]
        if record is None:
            return {
                "status": "incompatible_unmanaged"
                if (paths.workspace_path / ".millrace" / "daemon.lock").exists()
                else "not_found",
                "ready": False,
                "relaunch_allowed": False,
                "daemon_process": "unknown",
            }
        projection = _projection(
            record, snapshot["identity"]["source_revision"], snapshot["default_plan"]
        )
        projection.update(snapshot["page"])
    finally:
        runtime.close()
    if (
        record["status"] == "shutdown_complete"
        or projection["daemon_process"] != "live"
    ):
        return projection
    try:
        result = exchange(
            endpoint_path(paths.workspace_path),
            {
                "method": "challenge",
                "challenge": str(uuid4()),
                "after_session": after_session,
                "expected_source_revision": expected_revision,
            },
            deadline=deadline,
        )
        fresh = result["daemon"]
        if (
            any(
                fresh["target"][key] != record[key]
                for key in ("daemon_id", "daemon_generation", "process_nonce")
            )
            or fresh["process"] != record["process"]
        ):
            raise ValueError("daemon_challenge_mismatch")
        return dict(fresh)
    except (OSError, ValueError, KeyError, TypeError):
        projection["status"] = "unknown"
        return projection


def handle_daemon_control(namespace: object) -> CliSuccess:
    if threading.current_thread() is not threading.main_thread() or signal.getitimer(
        signal.ITIMER_REAL
    ) != (0.0, 0.0):
        raise CliCommandError(
            command=str(getattr(namespace, "command")),
            code="daemon_deadline_unavailable",
            message="Daemon observation requires a bounded caller timer.",
            exit_code=ExitCode.DOMAIN_REFUSAL,
            details={"status": "unknown"},
        )
    previous = signal.getsignal(signal.SIGALRM)

    def expire(_signum: int, _frame: object) -> None:
        raise ControlOperationError("daemon_observation_timeout")

    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(
        signal.ITIMER_REAL, 9.75 if getattr(namespace, "wait_for", None) else 1.75
    )
    try:
        return _handle_daemon_control(namespace)
    except ControlOperationError as exc:
        raise CliCommandError(
            command=str(getattr(namespace, "command")),
            code="daemon_control_unknown",
            message="Daemon observation timed out; retain the exact operation key.",
            exit_code=ExitCode.DOMAIN_REFUSAL,
            details={"status": "unknown"},
        ) from exc
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _handle_daemon_control(namespace: object) -> CliSuccess:
    from millrace.adapters.cli.context import workspace_paths

    command = str(getattr(namespace, "command"))
    paths = workspace_paths(namespace)
    deadline = time.monotonic() + 2
    try:
        if command == "daemon.inspect":
            after_session = revision_field(getattr(namespace, "after_session", 0))
            expected_revision = getattr(namespace, "expected_source_revision", None)
            if after_session and expected_revision is None:
                raise ValueError("daemon_cursor_scope_required")
            if expected_revision is not None:
                revision_field(expected_revision)
            wait_for = getattr(namespace, "wait_for", None)
            observation_deadline = time.monotonic() + (9.5 if wait_for else 1.5)
            while True:
                result = inspect_daemon(
                    paths,
                    deadline=min(observation_deadline, time.monotonic() + 1.5),
                    after_session=after_session,
                    expected_revision=expected_revision,
                )
                observed = (
                    result.get("ready")
                    if wait_for == "readiness"
                    else result.get("daemon_process") == "not_live"
                    if wait_for == "exit"
                    else result.get("final_summary") is not None
                    and result.get("runtime_cleanup") in {"complete", "not_required"}
                )
                if not wait_for or observed or time.monotonic() >= observation_deadline:
                    if wait_for:
                        result["observation_condition"] = wait_for
                        result["observation_satisfied"] = bool(observed)
                    break
                time.sleep(min(0.05, max(0, observation_deadline - time.monotonic())))
        else:
            request = ControlRequest.parse(str(getattr(namespace, "request_json")))
            if request.payload["action"] != "daemon.stop":
                raise ValueError("daemon_action_mismatch")
            # Historical replay is available after listener/process exit.
            runtime = _open(paths)
            try:
                history = runtime.store.show_operation(request)
            finally:
                runtime.close()
            if history["receipt"] is not None:
                result = history
                # Reassert only a still-serving exact incarnation, never a replacement.
                try:
                    exchange(
                        endpoint_path(paths.workspace_path),
                        {
                            "method": "stop",
                            "challenge": str(uuid4()),
                            "request": request.payload,
                        },
                        deadline=deadline,
                    )
                except (OSError, ValueError):
                    pass
            else:
                response = exchange(
                    endpoint_path(paths.workspace_path),
                    {
                        "method": "stop",
                        "challenge": str(uuid4()),
                        "request": request.payload,
                    },
                    deadline=deadline,
                )
                result = response["operation"]
        if command == "daemon.stop" and not result["receipt"]["accepted"]:
            raise CliCommandError(
                command=command,
                code=result["receipt"]["reason_code"],
                message="Daemon stop was refused without effect.",
                exit_code=ExitCode.DOMAIN_REFUSAL,
                details=result,
            )
        return success_result(
            command=command,
            code="daemon_observed",
            message="Daemon lifecycle observation.",
            data=result,
        )
    except CliCommandError:
        raise
    except (OSError, ValueError, ControlOperationError) as exc:
        raise CliCommandError(
            command=command,
            code="daemon_control_unknown",
            message="Daemon control is unresolved; retain the exact operation key.",
            exit_code=ExitCode.DOMAIN_REFUSAL,
            details={"status": "unknown"},
        ) from exc
