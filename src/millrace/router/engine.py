"""Bounded owned workers under the controller's unchanged instance lock."""

from __future__ import annotations

import base64
import json
import os
import queue
import signal
import sys
import threading
import time
from collections.abc import Callable
from types import FrameType
from typing import cast

from .adapters.command import invoke
from .execution import _adapter
from .records import TERMINAL, CommandResult, RouterError, mapping, within
from .store import Store, claim_hold, release_hold


class Signals:
    """The owner translates first stop to pause, then explicit cancellation."""

    def __init__(self) -> None:
        self.count = 0
        self.previous: dict[int, object] = {}

    def receive(self, signum: int, frame: FrameType | None) -> None:
        self.count += 1

    def __enter__(self) -> Signals:
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                self.previous[signum] = signal.signal(signum, self.receive)
        return self

    def __exit__(self, *args: object) -> None:
        for signum, previous in self.previous.items():
            signal.signal(signum, cast(signal.Handlers, previous))


class Worker:
    """One bounded invocation; callbacks exchange facts with the SQLite owner."""

    def __init__(
        self,
        invocation_id: str,
        deadline: float,
        events: queue.Queue[tuple[str, str, object, threading.Event | None]],
        call: Callable[[Callable[[], bool], Callable[[int], None]], CommandResult],
    ) -> None:
        self.invocation_id = invocation_id
        self.deadline = deadline
        self.cancel = threading.Event()
        self.started_failed = threading.Event()
        self.events = events
        self.call = call
        self.thread = threading.Thread(
            target=self.run, name=f"millrace-{invocation_id}"
        )

    def started(self, pid: int) -> None:
        acknowledged = threading.Event()
        self.events.put((self.invocation_id, "started", pid, acknowledged))
        if not acknowledged.wait(max(0.01, min(5.0, self.deadline - time.time()))):
            self.cancel.set()
            raise RouterError("started_ack_timeout")
        if self.started_failed.is_set():
            raise RouterError("started_ack_failed")

    def run(self) -> None:
        try:
            result = self.call(self.cancel.is_set, self.started)
            self.events.put((self.invocation_id, "completed", result, None))
        except BaseException as exc:
            self.events.put((self.invocation_id, "error", exc, None))


def _acknowledge_started(
    store: Store, worker: Worker, pid: int, acknowledgement: threading.Event
) -> None:
    """Commit started on the owner; wake failed callbacks only for cleanup."""
    try:
        store.active(worker.invocation_id, pid)
    except BaseException:
        worker.started_failed.set()
        worker.cancel.set()
        raise
    finally:
        acknowledgement.set()


def _prepare(
    store: Store, body: dict[str, object]
) -> tuple[Callable[[Callable[[], bool], Callable[[int], None]], CommandResult], str]:
    """Owner-only preparation captures everything a worker may read."""
    from pathlib import Path

    from .graph import verify_live_workspaces

    revision = store.revision(str(body["revision_id"]))
    verify_live_workspaces(revision, store.config.project_root)
    plane_id, node_id = str(body["plane_id"]), str(body["node"])
    node = revision.graph.planes[plane_id].nodes[node_id]
    root = Path(str(revision.workspaces[plane_id]["path"]))
    invocation_id = str(body["id"])
    controls = store.config.state_dir / "invocations" / invocation_id / "controls"
    controls.mkdir(parents=True)
    for name, raw in revision.files.items():
        destination = within(controls, name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    if any(
        not within(root, name, owner_forbidden=True).is_file() for name in node.inputs
    ):
        raise RouterError("input_missing")
    plan = store.launch(body)
    for name, raw in plan.generated_controls.items():
        destination = within(controls.parent, name, owner_forbidden=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
    env = {
        name: os.environ[name]
        for name in cast(
            list[str],
            mapping(store.run(str(body["run_id"]))["host_settings"])["env_allowlist"],
        )
        if name in os.environ
    }
    env["PATH"] = os.pathsep.join(
        cast(
            list[str], mapping(store.run(str(body["run_id"]))["host_settings"])["path"]
        )
    )
    env.pop("MILLRACE_SEAM_INPUT", None)
    env.update(dict(plan.environment))
    seam = mapping(body["launch"]).get("seam_input")
    if seam is not None:
        item = mapping(seam)
        destination = Path(str(item["path"]))
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(base64.b64decode(str(item["content"]), validate=True))
        env["MILLRACE_SEAM_INPUT"] = str(destination)
    cwd = within(root, node.cwd)
    if not cwd.is_dir():
        raise RouterError("invalid_cwd")
    adapter = _adapter(plan.adapter_id, plan.adapter_contract, store.adapter_resolver)
    deadline = cast(float, body["deadline"])

    def call(poll: Callable[[], bool], started: Callable[[int], None]) -> CommandResult:
        if plan.adapter_id == "command":
            return invoke(list(plan.argv), cwd, env, deadline, poll, started)
        return adapter.invoke(
            plan, cwd=cwd, env=env, deadline=deadline, poll=poll, on_started=started
        )

    return call, invocation_id


def _observe(store: Store, run_id: str) -> None:
    try:
        store.observe(run_id, store.draft(run_id))
    except RouterError as exc:
        store.set_gate(
            run_id,
            "unstable_revision"
            if exc.code == "unstable_revision"
            else "invalid_revision",
        )


def _boundary(store: Store, run_id: str) -> None:
    """Activation and all eligible saved-result routes are one ordered commit."""
    with store.transaction():
        if store.run(run_id)["run_gate"] is not None:
            return
        pending = store.pending_candidate(run_id)
        if pending is not None:
            if pending["diagnostic"] is not None:
                store.set_gate(run_id, "invalid_revision")
                return
            if store.run(run_id)["mode"] == "locked":
                store.set_gate(run_id, "locked_edit")
                return
            try:
                store.activate(run_id)
            except RouterError as exc:
                if exc.code not in {"incompatible_revision", "revision_integrity"}:
                    raise
                store.set_gate(run_id, "incompatible_revision")
                return
        if store.run(run_id)["run_gate"] is not None:
            return
        store.route_pending(run_id)


def run_engine(
    store: Store, run_id: str, *, poll: Callable[[bool], bool]
) -> dict[str, object]:
    """One owner drains controls/events, then joins every invocation before return."""
    run = store.run(run_id)
    claim_hold(store.config, run, allow_missing=run["status"] == "ready")
    if run["status"] == "ready":
        store.recompute(run_id)
    revision = store.revision(str(run["revision_id"]))
    events: queue.Queue[tuple[str, str, object, threading.Event | None]] = queue.Queue(
        maxsize=2 * revision.graph.max_parallel
    )
    workers: dict[str, Worker] = {}
    try:
        while True:
            poll(bool(workers))
            # Worker threads never access Store, control drain or mutable owner state.
            while True:
                try:
                    invocation_id, kind, value, ack = events.get_nowait()
                except queue.Empty:
                    break
                if kind == "started":
                    assert ack is not None
                    _acknowledge_started(
                        store, workers[invocation_id], cast(int, value), ack
                    )
                else:
                    worker = workers.pop(invocation_id)
                    worker.thread.join()
                    if kind == "error":
                        store.uncertain(invocation_id, diagnostic="adapter_exception")
                    else:
                        try:
                            store.complete(invocation_id, cast(CommandResult, value))
                            store.parsed(invocation_id)
                            invocation = mapping(
                                json.loads(
                                    str(
                                        store.db.execute(
                                            "SELECT body FROM invocations WHERE id=?",
                                            (invocation_id,),
                                        ).fetchone()[0]
                                    )
                                )
                            )
                            if (
                                cast(CommandResult, value).classification == "timeout"
                                and invocation["deadline"] == invocation["run_deadline"]
                            ):
                                store.set_gate(run_id, "limit_exhausted")
                        except Exception:
                            store.uncertain(
                                invocation_id, diagnostic="invalid_adapter_result"
                            )
            poll(bool(workers))
            store.validate()
            run = store.run(run_id)
            for row in store.db.execute(
                "SELECT id,status,plane_id FROM invocations WHERE run_id=?", (run_id,)
            ):
                if (
                    row["status"] in {"completed", "uncertain"}
                    and store.writer(str(row["id"])) != "stopped"
                ):
                    store.set_gate(run_id, "writer_unknown")
            planes = store.plane_states(run_id)
            for invocation_id, worker in workers.items():
                row = store.db.execute(
                    "SELECT plane_id FROM invocations WHERE id=?", (invocation_id,)
                ).fetchone()
                if (
                    run["cancel_requested"]
                    or planes[str(row[0])]["pause_reason"] == "operator_cancel"
                ):
                    worker.cancel.set()
            for plane_id, plane in planes.items():
                if (
                    plane["pause_reason"] == "operator_cancel"
                    and not store.inflight(run_id, plane_id)
                    and all(
                        store.writer(str(row[0])) == "stopped"
                        for row in store.db.execute(
                            "SELECT id FROM invocations WHERE run_id=? AND plane_id=?",
                            (run_id, plane_id),
                        )
                    )
                ):
                    was_terminal = plane["status"] == "cancelled"
                    plane.update(status="cancelled", active_invocation=None)
                    if not was_terminal:
                        store.save_plane(
                            run_id, plane, "cancelled", {"plane_cancel": True}
                        )
            _observe(store, run_id)
            if not workers:
                _boundary(store, run_id)
            elif (
                store.pending_candidate(run_id) is None
                and store.run(run_id)["run_gate"] is None
            ):
                _boundary(store, run_id)
            run = store.run(run_id)
            if (
                run["run_gate"] is None
                and not run["cancel_requested"]
                and store.pending_candidate(run_id) is None
            ):
                revision = store.revision(str(run["revision_id"]))
                names = sorted(revision.graph.planes)
                previous = run["last_admitted_plane"]
                if previous in names:
                    index = names.index(previous) + 1
                    names = names[index:] + names[:index]
                for plane_id in names:
                    if len(workers) >= revision.graph.max_parallel:
                        break
                    _observe(store, run_id)
                    if (
                        store.run(run_id)["run_gate"] is not None
                        or store.pending_candidate(run_id) is not None
                    ):
                        break
                    if not store.eligible(run_id, plane_id):
                        continue
                    store.recompute(run_id)
                    try:
                        body = store.reserve(run_id, revision, plane_id)
                    except RouterError as exc:
                        if exc.code != "limit_exhausted" or not workers:
                            store.set_gate(
                                run_id,
                                "limit_exhausted"
                                if exc.code == "limit_exhausted"
                                else "invalid_launch_plan",
                            )
                        break
                    try:
                        call, invocation_id = _prepare(store, body)
                    except (OSError, RouterError) as exc:
                        store.complete(
                            str(body["id"]),
                            CommandResult(
                                b"",
                                b"",
                                "execution_failure",
                                "stopped",
                                exc.code
                                if isinstance(exc, RouterError)
                                else "launch_preparation_failed",
                            ),
                            origin="core_preparation",
                        )
                        store.parsed(str(body["id"]))
                        break
                    worker = Worker(
                        invocation_id, cast(float, body["deadline"]), events, call
                    )
                    workers[invocation_id] = worker
                    worker.thread.start()
            store.recompute(run_id)
            run = store.run(run_id)
            if not workers:
                poll(False)
                if run["status"] in TERMINAL:
                    release_hold(store.config, run)
                    return store.inspection(run_id)
                if run["status"] == "paused":
                    return store.inspection(run_id)
            if workers:
                # Bounded owner wait keeps stop/control drain responsive.
                time.sleep(0.01)
    finally:
        original_error = sys.exc_info()[1]
        cleanup_error: BaseException | None = None
        for worker in workers.values():
            worker.cancel.set()
        while workers:
            invocation_id, kind, value, ack = events.get()
            try:
                if kind == "started":
                    assert ack is not None
                    _acknowledge_started(
                        store, workers[invocation_id], cast(int, value), ack
                    )
                else:
                    worker = workers.pop(invocation_id)
                    worker.thread.join()
                    if kind == "completed":
                        store.complete(invocation_id, cast(CommandResult, value))
                    else:
                        store.uncertain(invocation_id, diagnostic="adapter_exception")
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        if original_error is None and cleanup_error is not None:
            raise cleanup_error
