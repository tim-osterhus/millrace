"""Finite exact-target run hold commands; no provider or native control calls."""

from __future__ import annotations

import os
import signal
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from math import isfinite
from types import FrameType
from typing import Any, cast
from uuid import uuid4

from millrace.adapters.cli.context import (
    CliCommandError,
    OpenRuntimeContext,
    open_runtime_context,
)
from millrace.adapters.cli.output import CliSuccess, ExitCode, success_result
from millrace.contracts.controls import (
    ControlRequest,
    InvalidControlRequest,
    canonical_json,
)
from millrace.contracts.state import RunnerSessionRecord, RunRef
from millrace.substrate.errors import ControlOperationError


@contextmanager
def _caller_deadline() -> Iterator[float]:
    # Run commands execute in a process main thread. A real timer also bounds
    # Python/CAS preparation; SQL progress handlers alone cannot interrupt it.
    if threading.current_thread() is not threading.main_thread() or signal.getitimer(
        signal.ITIMER_REAL
    ) != (0.0, 0.0):
        raise ControlOperationError("control_deadline_timer_unavailable")
    previous = signal.getsignal(signal.SIGALRM)

    def expire(signum: int, frame: FrameType | None) -> None:
        raise ControlOperationError("control_deadline_unknown")

    deadline = time.monotonic() + 4
    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, 4)
    try:
        yield deadline
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def handle_run_control_command(namespace: object) -> CliSuccess:
    result: CliSuccess | None = None
    failure: CliCommandError | None = None
    try:
        with _caller_deadline() as deadline:
            try:
                result = _handle_run_control_command(namespace, deadline)
            except CliCommandError as exc:
                failure = exc
    except ControlOperationError as exc:
        raise CliCommandError(
            command=str(getattr(namespace, "command")),
            code=str(exc),
            message="Run control deadline could not be established or was exceeded.",
            exit_code=ExitCode.DOMAIN_REFUSAL,
            details={"status": "unknown"},
        ) from exc

    if failure is not None:
        raise failure
    assert result is not None
    return result


def _handle_run_control_command(namespace: object, deadline: float) -> CliSuccess:
    command = str(getattr(namespace, "command"))
    try:
        request = ControlRequest.parse(str(getattr(namespace, "request_json")))
        if request.payload["action"] != command:
            raise InvalidControlRequest("control_action_mismatch")
    except InvalidControlRequest as exc:
        raise CliCommandError(
            command=command,
            code="invalid_control_request",
            message="Control request is invalid or exceeds its bound.",
            exit_code=ExitCode.CLI_USAGE,
            details={"receipt_persisted": False},
        ) from exc
    try:
        runtime = open_runtime_context(namespace, command=command)
        try:
            result = _execute_control(runtime, request, deadline)
        finally:
            runtime.close()
    except ControlOperationError as exc:
        raise CliCommandError(
            command=command,
            code=str(exc),
            message="Run control could not be resolved; retain the same operation key.",
            exit_code=ExitCode.DOMAIN_REFUSAL,
            details={
                "status": "unknown",
                "key": list(request.key),
                "request_digest": request.digest,
                "target": request.payload["target"],
            },
        ) from exc
    if not result["receipt"]["accepted"]:
        raise CliCommandError(
            command=command,
            code=result["receipt"]["reason_code"],
            message=(
                "Native retirement was refused. Owner-loss aftermath was recorded "
                "under the original accepted control."
                if result["receipt"]["reason_code"]
                == "native_recovery_witness_unavailable_loss_observed"
                else "Run control was refused without effect."
            ),
            exit_code=ExitCode.DOMAIN_REFUSAL,
            details=result,
        )
    return success_result(
        command=command,
        code=result["receipt"]["reason_code"],
        message="Run control committed.",
        data=result,
    )


def admit_runner_start(
    runtime: OpenRuntimeContext,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    *,
    occurred_at: int,
    driving_budget_id: str | None,
    driving_budget_clock: Callable[[], float] | None,
    on_start_reserved: Callable[[RunnerSessionRecord], None] | None,
    on_accepted_start: Callable[[RunnerSessionRecord], None] | None,
) -> RunnerSessionRecord | None:
    from millrace.adapters.cli import session_completion as complete
    from millrace.contracts.transition import AdvanceRunnerSession

    intent = AdvanceRunnerSession(
        f"cli:run.session-start-intent:{session.session_id}",
        run_ref=run_ref,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        expected_state="created",
        next_state="starting",
        occurred_at=occurred_at,
    )
    if driving_budget_id is None:
        persisted = complete._persist_transition(runtime, intent)
    else:
        persisted = complete._persist_transition(
            runtime,
            intent,
            driving_budget_id=driving_budget_id,
            driving_budget_clock=driving_budget_clock,
        )
    if persisted is None:
        return None
    started = persisted.runner_sessions[session.session_id]
    if driving_budget_id is not None:
        runtime.store.record_budgeted_runner_start(driving_budget_id, started)
    if on_start_reserved is not None:
        on_start_reserved(session)
    if on_accepted_start is not None:
        on_accepted_start(started)
    return started


def validate_effective_timeout(effective_timeout_seconds: float | None) -> None:
    if effective_timeout_seconds is None:
        return
    if type(effective_timeout_seconds) not in {int, float}:
        raise TypeError("effective_timeout_seconds must be a number")
    if effective_timeout_seconds <= 0 or not isfinite(float(effective_timeout_seconds)):
        raise ValueError("effective_timeout_seconds must be finite and positive")


@dataclass
class _NativeOwner:
    paths: Any
    session: RunnerSessionRecord
    control: Any
    process: dict[str, Any]


_NATIVE_OWNERS: dict[str, _NativeOwner] = {}
_NATIVE_LOCK = threading.Lock()


def _open_owner(owner: _NativeOwner) -> OpenRuntimeContext:
    from millrace.adapters.cli.daemon_control import _open

    return _open(owner.paths)


def drive_native_control(
    runtime: OpenRuntimeContext, session: RunnerSessionRecord, handle: Any
) -> None:
    native_method = getattr(handle, "native_control", None)
    control = native_method() if callable(native_method) else None
    if control is None:
        return
    from millrace.adapters.cli.daemon_process import process_identity

    registered = False
    with _NATIVE_LOCK:
        owner = _NATIVE_OWNERS.get(session.session_id)
        if owner is None:
            owner = _NativeOwner(
                runtime.paths, session, control, process_identity(os.getpid())
            )
            _NATIVE_OWNERS[session.session_id] = owner

            def journal(evidence: dict[str, Any]) -> None:
                opened = _open_owner(owner)
                try:
                    opened.store.update_native_control(
                        session.run_id,
                        control.owner_id,
                        control.snapshot(),
                        reason=evidence["reason"],
                    )
                finally:
                    opened.close()

            control.set_journal(journal)
            registered = True
    if registered:

        def ended() -> None:
            with _NATIVE_LOCK:
                if _NATIVE_OWNERS.get(session.session_id) is owner:
                    del _NATIVE_OWNERS[session.session_id]
            control.set_journal(None)

        handle.register_native_owner_end(ended)
    state = runtime.store.load_runtime_state(runtime.cas_store)
    current = state.run_execution_controls.get(session.run_id)
    if current is None or current.native is None:
        return
    if current.state not in {"pause_pending", "resume_pending"}:
        snapshot = control.snapshot()
        reason = snapshot.get("reason") or ""
        if current.state == "resumed" and reason.startswith("unsupported_"):
            runtime.store.update_native_control(
                session.run_id, control.owner_id, snapshot, reason=reason
            )
        return
    key = current.operation_key
    assert key is not None
    snapshot = control.snapshot()
    operation = snapshot["operations"].get(canonical_json(list(key)))
    if operation is None:
        action = "runs.pause" if current.state == "pause_pending" else "runs.resume"
        with control.reservation(
            dict(current.native["profile"]),
            action,
            current.pause_id if action == "runs.resume" else None,
        ):
            control.accept(
                canonical_json(list(key)),
                current.native["digest"],
                action,
                current.pause_id,
            )
        snapshot = control.snapshot()
    runtime.store.update_native_control(session.run_id, control.owner_id, snapshot)
    # Only release the post-resume effect barrier after durable settlement.
    current = runtime.store.load_runtime_state(
        runtime.cas_store
    ).run_execution_controls[session.run_id]
    if current.state in {"paused", "resumed"}:
        control.confirm_settlement(canonical_json(list(key)))


def admit_native_control(
    runtime: OpenRuntimeContext, request: ControlRequest, deadline: float
) -> dict[str, Any]:
    history = runtime.store.show_operation(request)
    if history["receipt"] is not None:
        return history
    target = request.payload["target"]
    session_target = target.get("expected_session")
    with _NATIVE_LOCK:
        owner = _NATIVE_OWNERS.get(
            "" if session_target is None else session_target["session_id"]
        )
    if (
        owner is None
        or owner.paths != runtime.paths
        or owner.session.run_id != target["run_id"]
    ):
        return runtime.store.execute_operation(
            request, lambda: _native_refusal("native_owner_unavailable")
        )
    # All CAS/context staging and process observation occur before reservation/SQL.
    revision = runtime.store.control_identity()["source_revision"]
    state = runtime.store.load_runtime_state(runtime.cas_store, _optimistic=True)
    if time.monotonic() >= deadline:
        raise ControlOperationError("control_deadline_unknown")
    value = request.payload
    try:
        with owner.control.reservation(
            value["profile"], value["action"], value["pause_id"]
        ):
            snapshot = owner.control.snapshot()
            result = runtime.store.accept_native_control(
                request,
                state,
                revision,
                {"process": owner.process, "owner_id": owner.control.owner_id},
                snapshot,
                deadline,
            )
            if result["receipt"]["accepted"]:
                owner.control.accept(
                    canonical_json(list(request.key)),
                    request.digest,
                    value["action"],
                    result["receipt"]["pause_id"],
                )
            return result
    except ControlOperationError:
        raise
    except RuntimeError as exc:
        reason = str(exc)
        return runtime.store.execute_operation(
            request, lambda: _native_refusal(reason), deadline=deadline
        )


def _native_refusal(reason: str) -> Any:
    from millrace.substrate.sqlite import ControlDecision

    return ControlDecision("rejected_no_effect", reason)


def _execute_control(
    runtime: OpenRuntimeContext, request: ControlRequest, deadline: float
) -> dict[str, Any]:
    if request.payload["action"] == "runs.recover":
        return recover_native_control(runtime, request, deadline)
    history = runtime.store.show_operation(request)
    if history["receipt"] is not None:
        return history
    if request.payload["profile"] is None:
        return runtime.store.execute_run_control(
            request,
            runtime.cas_store,
            supported_adapter_kinds=frozenset({"codex", "millforge"}),
            deadline=deadline,
        )
    from millrace.adapters.cli.daemon_listener import endpoint_path, exchange

    try:
        response = exchange(
            endpoint_path(runtime.paths.workspace_path),
            {
                "method": "native_control",
                "challenge": str(uuid4()),
                "request": request.payload,
            },
            deadline=min(deadline, time.monotonic() + 1.5),
        )
        result = cast(dict[str, Any], response["operation"])
    except (OSError, ValueError, KeyError):
        result = runtime.store.show_operation(request)
        if result["receipt"] is None:
            raise ControlOperationError("native_acceptance_unknown") from None
    while (
        result["receipt"]["accepted"]
        and result["results"][-1]["stage"] == "pending"
        and time.monotonic() + 0.1 < deadline
    ):
        time.sleep(0.05)
        result = runtime.store.show_operation(request)
    return result


def recover_native_control(
    runtime: OpenRuntimeContext, request: ControlRequest, deadline: float
) -> dict[str, Any]:
    """Fresh local retirement. Every process observation occurs outside SQL."""
    from millrace.adapters.cli.daemon_process import observe_process
    from millrace.adapters.cli.session_reconciliation import retire_lost_native_session
    from millrace.substrate._sqlite_run_controls import native_witness_digest

    history = runtime.store.show_operation(request)
    if (
        history["receipt"] is not None
        and history["receipt"]["reason_code"]
        == "native_recovery_witness_unavailable_loss_observed"
    ):
        _finish_observed_native_loss(runtime, request, history)
        return runtime.store.show_operation(request)
    if history["receipt"] is not None and (
        not history["receipt"]["accepted"]
        or history["results"][-1]["stage"] != "pending"
    ):
        return history
    revision = runtime.store.control_identity()["source_revision"]
    state = runtime.store.load_runtime_state(runtime.cas_store, _optimistic=True)
    target = request.payload["target"]
    control = state.run_execution_controls.get(target["run_id"])
    reason: str | None = None
    if (
        control is not None
        and control.native is not None
        and control.state
        in {"pause_pending", "resume_pending", "unknown", "unqualified"}
    ):
        process = control.native["owner"]["process"]
        if process.get("status") == "live" and observe_process(process) == "not_live":
            history = runtime.store.accept_native_recovery(
                request, state, revision, deadline, observe_loss=True
            )
            if (
                history["receipt"]["reason_code"]
                == "native_recovery_witness_unavailable_loss_observed"
            ):
                _finish_observed_native_loss(runtime, request, history)
            return runtime.store.show_operation(request)
    if (
        control is None
        or control.native is None
        or control.state not in {"paused", "recover_pending"}
    ):
        reason = "native_recovery_witness_unavailable"
    else:
        native = control.native
        snapshot = native["snapshot"]
        process = native["owner"]["process"]
        if (
            snapshot["state"] != "held"
            or snapshot["active_effects"] != 0
            or snapshot.get("parked") is not True
            or not snapshot["eligible"]
            or snapshot["invalidation_pending"]
            or snapshot["os_descendants"] != []
            or snapshot["descendant_boundary"]
            != "no-processes-before-unsupported-entry"
            or process.get("status") != "live"
            or native["owner"]["owner_id"] != target["owner_id"]
        ):
            reason = "native_recovery_effects_unproved"
        elif observe_process(process) != "not_live":
            reason = "native_recovery_owner_not_absent"
        elif (
            control.state == "paused"
            and native_witness_digest(control) != target["witness_digest"]
        ):
            reason = "native_recovery_witness_mismatch"
        elif any(
            c.session_id == target["expected_session"]["session_id"]
            for c in state.runner_session_cancellation_requests.values()
        ):
            reason = "native_recovery_authority_changed"
    if reason is not None:
        if history["receipt"] is not None:
            raise ControlOperationError(reason)
        return runtime.store.execute_operation(
            request, lambda: _native_refusal(reason), deadline=deadline
        )
    assert control is not None and control.native is not None
    if history["receipt"] is None:
        history = runtime.store.accept_native_recovery(
            request, state, revision, deadline
        )
        if not history["receipt"]["accepted"]:
            return history
    session = state.runner_sessions[target["expected_session"]["session_id"]]
    # The held profile has no child processes and all owned asynchronous model
    # tasks have settled before the durable held witness. Process absence settles
    # the exact native loop. No additional mechanical native cleanup is required.
    retire_lost_native_session(runtime, session)
    if observe_process(dict(control.native["owner"]["process"])) != "not_live":
        raise ControlOperationError("native_recovery_owner_not_absent")
    runtime.store.finish_native_recovery(request, deadline)
    return runtime.store.show_operation(request)


def _finish_observed_native_loss(
    runtime: OpenRuntimeContext, request: ControlRequest, history: dict[str, Any]
) -> None:
    """Replay independent loss recording without admitting unsafe retirement."""
    from millrace.adapters.cli.session_reconciliation import retire_lost_native_session
    from millrace.kernel.run_controls import exact_run_target_refusal

    observation = history["results"][0]["evidence"]["owner_loss_observation"]
    if (
        observation["recovery_request_key"] != list(request.key)
        or observation["recovery_request_digest"] != request.digest
    ):
        raise ControlOperationError("native_recovery_observation_mismatch")
    target = request.payload["target"]
    records = runtime.store.control_history_records(run_id=target["run_id"])
    linked = [
        row
        for row in records
        if row["kind"] == "result"
        and row["record"]["result_id"] == observation["result_id"]
        and [
            row["key"][field]
            for field in (
                "workspace_id",
                "instance_id",
                "store_epoch",
                "caller_id",
                "operation_id",
            )
        ]
        == observation["operation_key"]
    ]
    if len(linked) != 1 or linked[0]["record"]["evidence"] != observation:
        raise ControlOperationError("native_recovery_observation_mismatch")
    state = runtime.store.load_runtime_state(runtime.cas_store)
    control = state.run_execution_controls.get(target["run_id"])
    session = state.runner_sessions.get(observation["session"]["session_id"])
    if (
        control is None
        or control.native is None
        or session is None
        or control.native["owner"]["owner_id"] != observation["owner_id"]
        or list(control.operation_key or ()) != observation["operation_key"]
        or session.dispatch_generation != observation["session"]["dispatch_generation"]
        or session.session_fencing_token
        != observation["session"]["session_fencing_token"]
    ):
        raise ControlOperationError("native_recovery_authority_changed")
    current_target = {
        **target,
        "expected_control_revision": control.control_revision,
        "expected_session": {**observation["session"], "state": session.state},
    }
    if exact_run_target_refusal(state, current_target) is not None:
        raise ControlOperationError("native_recovery_authority_changed")
    retire_lost_native_session(runtime, session)
