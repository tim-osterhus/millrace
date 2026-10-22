"""One execution-capable holder owns ordered controls, final drain and engine work."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from contextlib import ExitStack
from typing import cast

from .control_requests import (
    INSTANCE_OPS,
    PLANE_OPS,
    STOPS,
    append,
    envelope,
    finish,
    lookup,
    validate_envelope,
    validate_requests,
)
from .engine import Signals, run_engine
from .execution import AdapterResolver, _adapter
from .graph import capture, resolve_nodes
from .ownership import (
    Config,
    _read_owner,
    atomic_json,
    claim_hold,
    file_lock,
    hold_record,
    new_id,
    release_hold,
)
from .records import (
    TERMINAL,
    RouterError,
    common_settings,
    mapping,
    strict_json,
    strings,
    text,
)
from .store import Store


def configured(config: Config, settings: dict[str, object]) -> Config:
    from pathlib import Path

    path = strings(settings["path"])
    if any(not Path(value).is_absolute() for value in path):
        raise RouterError("invalid_instance_path")
    return Config(
        config.instance_id,
        config.project_root,
        config.workflow_file,
        config.state_dir,
        path,
        strings(settings["env_allowlist"]),
        {
            text(key): text(value)
            for key, value in mapping(settings["host_paths"]).items()
        },
        cast(int, settings["state_version"]),
        common_settings(
            settings.get("runtime_defaults", {}), location="runtime_defaults"
        ),
        common_settings(
            settings.get("operator_policy", {}), policy=True, location="operator_policy"
        ),
    )


class Controller:
    """A local latch cannot transfer, outlive or silently reopen its held lock."""

    def __init__(
        self,
        store: Store,
        signals: Signals,
        *,
        on_started: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self.store = store
        self.signals = signals
        self.seen_signals = 0
        self.holder_id = new_id()
        self.draining = False
        self.integrity_error: RouterError | None = None
        self.on_started = on_started

    def queue_state(self, state: str) -> None:
        atomic_json(
            self.store.config.state_dir / ".queue.json",
            {
                "instance_id": self.store.config.instance_id,
                "holder_id": self.holder_id,
                "state": state,
            },
        )

    def interrupted(self) -> None:
        for row in self.store.db.execute(
            "SELECT id FROM invocations WHERE status IN ('reserved','active') "
            "ORDER BY attempt"
        ):
            self.store.uncertain(str(row[0]))

    def current(self) -> dict[str, object] | None:
        for row in self.store.db.execute("SELECT body FROM runs ORDER BY rowid DESC"):
            run = mapping(strict_json(str(row[0])))
            if run["status"] not in TERMINAL:
                return run
        return None

    def poll(self, active: bool) -> bool:
        """Keep control corruption inside the owner until raw completion is saved."""
        if self.integrity_error is None:
            try:
                observed = self.signals.count
                self.admit_signals(observed)
                frontier = (
                    int(
                        self.store.db.execute(
                            "SELECT COALESCE(MAX(seq),0) FROM controls"
                        ).fetchone()[0]
                    )
                    if active
                    else None
                )
                self.drain(through=frontier)
                # Idle drain may create a target or observe another stop. Recheck
                # after each complete drain before promotion or closing exit.
                while not active and self.admit_signals(self.signals.count):
                    self.drain()
                self.settle_cancellation()
            except RouterError as exc:
                if exc.code != "control_integrity":
                    raise
                self.integrity_error = exc
                self.draining = True
        if self.integrity_error is not None and not active:
            raise self.integrity_error
        run = self.current()
        return bool(run and run["cancel_requested"])

    def admit_signals(self, observed: int) -> bool:
        admitted = False
        while self.seen_signals < observed:
            run = self.current()
            if run is None:
                break
            request = envelope(
                "pause_run" if self.seen_signals == 0 else "cancel_run",
                self.store.config.instance_id,
                cast(str, run["id"]),
                new_id(),
                None,
                {},
                plane_id=None,
            )
            append(self.store, request)
            self.seen_signals += 1
            admitted = True
        return admitted

    def drain(self, *, through: int | None = None) -> None:
        validate_requests(self.store)
        while True:
            row = self.store.db.execute(
                "SELECT body FROM controls WHERE status='queued' "
                + ("AND seq<=? " if through is not None else "")
                + "ORDER BY seq LIMIT 1",
                (through,) if through is not None else (),
            ).fetchone()
            if row is None:
                return
            request = mapping(strict_json(str(row[0])))
            self.apply(request)
            self.settle_cancellation()
            validate_requests(self.store)

    def settle_cancellation(self) -> None:
        run = self.current()
        if (
            run is not None
            and run["cancel_requested"]
            and self.store.quiescent(cast(str, run["id"]))
        ):
            self.store.recompute(cast(str, run["id"]))

    def apply(self, request: dict[str, object]) -> dict[str, object]:
        op = cast(str, request["operation"])
        try:
            if request["instance_id"] != self.store.config.instance_id:
                raise RouterError("invalid_target")
            run = (
                self.store.run(cast(str, request["run_id"]))
                if op not in INSTANCE_OPS
                else None
            )
            if (
                op in {"retry_invocation", "reconcile_invocation"}
                and not self.store.db.execute(
                    "SELECT 1 FROM invocations WHERE id=? AND run_id=? AND plane_id=?",
                    (
                        mapping(request["payload"])["invocation_id"],
                        request["run_id"],
                        request["plane_id"],
                    ),
                ).fetchone()
            ):
                raise RouterError("invalid_target")
            if self.draining and op not in STOPS:
                raise RouterError("shutting_down")
            version = (
                run["state_version"]
                if run
                else self.store.instance_settings()["state_version"]
            )
            if op not in STOPS and request["expected_state_version"] != version:
                raise RouterError("stale_state_version")
            with self.store.transaction():
                result = self.effect(request, run)
                receipt = finish(self.store, request, result=result)
                if op == "reconcile_invocation":
                    self.store.validate()
            if op == "start_run" and self.on_started is not None:
                self.on_started(
                    {
                        "instance_id": self.store.config.instance_id,
                        "run_id": receipt["run_id"],
                    }
                )
            return receipt
        except RouterError as exc:
            with self.store.transaction():
                return finish(
                    self.store,
                    request,
                    code=exc.code,
                    retryable=exc.code in {"shutting_down", "busy", "controller_busy"},
                )

    def effect(
        self, request: dict[str, object], run: dict[str, object] | None
    ) -> dict[str, object]:
        op, request_id = str(request["operation"]), str(request["request_id"])
        payload = mapping(request["payload"])
        if op == "start_run":
            self.store.config = configured(
                self.store.config, self.store.instance_settings()
            )
            config = self.store.config
            revision = capture(
                config.workflow_file,
                config.project_root,
                search_path=config.path,
                host_paths=config.host_paths,
            )
            created = self.store.create_run(revision, cast(str, payload["edit_mode"]))
            created["start_request_id"] = request_id
            self.store.db.execute(
                "UPDATE runs SET body=? WHERE id=?",
                (json.dumps(created), created["id"]),
            )
            return {
                "action": "started",
                "run_id": created["id"],
                "state_version": 1,
                "instance_state_version": self.store.instance_settings()[
                    "state_version"
                ],
            }
        if op == "update_instance_config":
            if self.current() is not None:
                raise RouterError("run_nonterminal")
            settings = self.store.instance_settings()
            settings.update(mapping(payload["settings"]))
            settings["state_version"] = cast(int, settings["state_version"]) + 1
            config = configured(self.store.config, settings)
            revision = capture(
                config.workflow_file,
                config.project_root,
                search_path=config.path,
                host_paths=config.host_paths,
            )
            resolve_nodes(
                revision,
                settings_context=config.settings(),
                adapter_resolver=self.store.adapter_resolver,
                project_root=config.project_root,
            )
            self.store.db.execute(
                "UPDATE meta SET value=? WHERE key='settings'",
                (json.dumps(settings, sort_keys=True),),
            )
            self.store.config = config
            return {"action": "configured", "state_version": settings["state_version"]}
        assert run is not None
        run_id = cast(str, run["id"])
        planes = self.store.plane_states(run_id)
        plane_id = str(request["plane_id"]) if op in PLANE_OPS else None
        if plane_id is not None and plane_id not in planes:
            raise RouterError("invalid_target")
        if op in STOPS:
            targets = [plane_id] if plane_id is not None else sorted(planes)
            active = self.store.inflight(run_id, plane_id)
            snapshot = {"target_planes": targets, "active_invocations": active}
            if run["status"] in TERMINAL:
                return {"action": "already_terminal", **snapshot}
            if op == "pause_run":
                if run["run_gate"] is not None:
                    return {"action": "already_paused", **snapshot}
                if run["status"] == "ready":
                    try:
                        claim_hold(self.store.config, run, allow_missing=True)
                    except RouterError as exc:
                        if exc.code == "owner_store_unverifiable":
                            self.draining = True
                        raise
                self.store.set_gate(run_id, "operator_pause", request_id=request_id)
                self.draining = True
                return {"action": "paused", **snapshot}
            if op == "cancel_run":
                if run["cancel_requested"]:
                    return {"action": "already_cancel_requested", **snapshot}
                run["cancel_requested"] = True
                self.store.update_run(run)
                self.store.set_gate(run_id, "operator_cancel", request_id=request_id)
                self.draining = True
                self.store.recompute(run_id, request_id=request_id)
                return {
                    "action": "cancelled"
                    if self.store.run(run_id)["status"] == "cancelled"
                    else "cancel_requested",
                    **snapshot,
                }
            assert plane_id is not None
            if run["status"] == "ready":
                try:
                    claim_hold(self.store.config, run, allow_missing=True)
                except RouterError as exc:
                    if exc.code == "owner_store_unverifiable":
                        self.draining = True
                    raise
            plane = planes[plane_id]
            if plane["status"] in TERMINAL:
                return {"action": "already_terminal", **snapshot}
            if op == "pause_plane":
                if plane["status"] == "paused":
                    return {"action": "already_paused", **snapshot}
                plane.update(
                    status="paused",
                    pause_reason="operator_pause",
                    resume_target=plane["cursor"],
                )
            else:
                if plane["pause_reason"] == "operator_cancel":
                    return {"action": "cancel_requested", **snapshot}
                plane.update(
                    status="paused"
                    if active
                    or any(
                        self.store.writer(str(row[0])) != "stopped"
                        for row in self.store.db.execute(
                            "SELECT id FROM invocations WHERE run_id=? AND plane_id=?",
                            (run_id, plane_id),
                        )
                    )
                    else "cancelled",
                    pause_reason="operator_cancel",
                )
                if plane["status"] == "cancelled":
                    plane["active_invocation"] = None
            self.store.save_plane(run_id, plane, "control", {"request_id": request_id})
            self.store.recompute(run_id)
            return {
                "action": "paused" if op == "pause_plane" else "cancel_requested",
                **snapshot,
            }
        if run["status"] in TERMINAL:
            if op == "recover_run":
                return {"action": "already_terminal"}
            raise RouterError("run_terminal")
        if op == "recover_run":
            return {"action": "recovered"}
        if op == "submit_revision":
            candidate = self.store.draft(run_id)
            changed = self.store.observe(run_id, candidate, request_id=request_id)
            return {
                "action": "submitted" if changed else "unchanged",
                "candidate_id": candidate.candidate_id,
            }
        if op == "reconcile_invocation":
            if run["run_gate"] is None or self.store.inflight(run_id, plane_id):
                raise RouterError("invocation_not_reconcilable")
            self.store.reconcile(
                run_id,
                str(payload["invocation_id"]),
                str(payload["reason"]),
                request_id,
            )
            return {
                "action": "writers_confirmed",
                "invocation_id": payload["invocation_id"],
            }
        if run["cancel_requested"]:
            raise RouterError("run_cancelled")
        if op in {"resume_run", "accept_revision"}:
            if run["run_gate"] is None:
                raise RouterError("run_not_paused")
            if not self.store.quiescent(run_id):
                raise RouterError("writer_unknown")
            pending = self.store.pending_candidate(run_id)
            if op == "accept_revision":
                if (
                    pending is None
                    or pending["candidate_id"] != payload["candidate_id"]
                ):
                    raise RouterError("stale_candidate")
                candidate = self.store.draft(run_id)
                if candidate.candidate_id != payload["candidate_id"]:
                    self.store.observe(run_id, candidate, request_id=request_id)
                    return {
                        "action": "candidate_superseded",
                        "accepted_revision": False,
                        "new_pending_id": candidate.candidate_id,
                        "observed_candidate_id": candidate.candidate_id,
                    }
                if candidate.revision is None:
                    raise RouterError("invalid_revision")
                self.store.activate(run_id, request_id=request_id)
                run = self.store.run(run_id)
            elif pending is not None:
                raise RouterError(
                    "invalid_revision" if pending["diagnostic"] else "locked_edit"
                )
            if self.store.pending_completion(run_id) is None:
                self.check_bounds(run)
            self.store.clear_gate(run_id, request_id)
            if op == "accept_revision":
                self.store.route_pending(run_id)
            return (
                {
                    "action": "revision_accepted",
                    "accepted_revision": True,
                    "revision_id": self.store.run(run_id)["revision_id"],
                }
                if op == "accept_revision"
                else {"action": "resumed"}
            )
        assert plane_id is not None
        plane = planes[plane_id]
        if plane["status"] in TERMINAL:
            raise RouterError("plane_terminal")
        if plane["status"] != "paused" or self.store.inflight(run_id, plane_id):
            raise RouterError("plane_not_paused")
        if plane["pause_reason"] == "operator_cancel":
            raise RouterError("plane_cancelled")
        if op == "resume_plane":
            target = (
                plane["resume_target"]
                if plane["resume_target"] is not None
                else plane["cursor"]
            )
            if target is None:
                raise RouterError("redirect_required")
            if self.store.pending_completion(run_id, plane_id) is None:
                self.check_bounds(run)
            plane.update(
                cursor=target, status="ready", pause_reason=None, resume_target=None
            )
            self.store.save_plane(run_id, plane, "control", {"request_id": request_id})
            self.store.recompute(run_id)
            return {"action": "resumed"}
        self.check_bounds(run)
        if op == "redirect_plane":
            self.store.move(
                run_id,
                plane_id,
                str(payload["node_id"]),
                "redirect",
                str(payload["reason"]),
                request_id,
            )
            return {"action": "redirected", "node_id": payload["node_id"]}
        if op == "retry_invocation":
            source = self.store.db.execute(
                "SELECT node FROM invocations WHERE id=? AND run_id=? AND plane_id=?",
                (payload["invocation_id"], run_id, plane_id),
            ).fetchone()
            if source is None:
                raise RouterError("invalid_target")
            self.store.move(
                run_id,
                plane_id,
                str(source["node"]),
                "retry",
                str(payload["reason"]),
                request_id,
                source_invocation=str(payload["invocation_id"]),
            )
            return {"action": "retrying", "invocation_id": payload["invocation_id"]}
        raise RouterError("invalid_operation")

    @staticmethod
    def check_bounds(run: dict[str, object]) -> None:
        if cast(int, run["count"]) >= cast(int, run["cap"]) or time.time() >= cast(
            float, run["deadline"]
        ):
            raise RouterError("limit_exhausted")

    def release_terminal_hold(self) -> None:
        """Best-effort cleanup cannot prevent controls from draining."""
        folder = self.store.config.project_root / ".millrace-router-owner"
        try:
            if not folder.is_dir():
                return
            with file_lock(folder / "lock", blocking=True):
                owner = _read_owner(folder / "owner.json")
                assert owner is not None
                if owner.get("instance_id") != self.store.config.instance_id:
                    return
                run_id = owner.get("run_id")
                if not isinstance(run_id, str):
                    return
                run = self.store.run(run_id)
                if run["status"] not in TERMINAL or owner != hold_record(
                    self.store.config, run
                ):
                    return
            # release_hold reacquires the lock and rechecks the exact record.
            release_hold(self.store.config, run)
        except (RouterError, OSError):
            return

    def run(self) -> None:
        self.interrupted()
        self.release_terminal_hold()
        while True:
            self.poll(False)
            run = self.current()
            if (
                run is not None
                and run["status"] in {"ready", "running"}
                and not self.draining
            ):
                run_engine(
                    self.store,
                    cast(str, run["id"]),
                    poll=self.poll,
                )
                continue
            # Appenders observe closing under exactly the lock used for append.
            with file_lock(
                self.store.config.state_dir / ".commands.lock", blocking=True
            ):
                self.queue_state("closing")
            self.poll(False)
            run = self.current()
            if (
                run is not None
                and run["status"] in {"ready", "running"}
                and not self.draining
            ):
                with file_lock(
                    self.store.config.state_dir / ".commands.lock", blocking=True
                ):
                    self.queue_state("open")
                continue
            self.release_terminal_hold()
            return


def execute(
    config: Config,
    request: dict[str, object],
    *,
    on_started: Callable[[dict[str, object]], None] | None = None,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    validate_envelope(request)
    if request["instance_id"] != config.instance_id:
        raise RouterError("invalid_target")
    store = Store(config, adapter_resolver=adapter_resolver)
    owner = ExitStack()
    try:
        if request["operation"] == "update_instance_config":
            # Shape errors keep their ordinary durable settings refusal. A valid
            # new namespace needs its execution contract before append or replay.
            try:
                runtime = common_settings(
                    mapping(mapping(request["payload"])["settings"]).get(
                        "runtime_defaults", {}
                    ),
                    location="runtime_defaults",
                )
            except RouterError:
                runtime = {}
            for name, raw in mapping(runtime.get("adapter_settings", {})).items():
                _adapter(name, str(mapping(raw)["contract"]), adapter_resolver)
        with file_lock(config.state_dir / ".commands.lock", blocking=True):
            old = lookup(store, request)
            if old is not None and old["status"] != "queued":
                return old
            try:
                owner.enter_context(file_lock(config.state_dir / ".controller.lock"))
            except RouterError as exc:
                if exc.code != "controller_busy":
                    raise
                if old is not None:
                    return old
                path = config.state_dir / ".queue.json"
                try:
                    queue = mapping(strict_json(path.read_bytes()))
                except (OSError, RouterError):
                    queue = {}
                code = (
                    "controller_busy"
                    if request["operation"] == "recover_run"
                    else "busy"
                    if queue.get("state") != "open"
                    or queue.get("instance_id") != config.instance_id
                    else None
                )
                with store.transaction():
                    queued = append(store, request)
                    if code is not None:
                        return finish(store, request, code=code, retryable=True)
                    return queued
            signals = owner.enter_context(Signals())
            controller = Controller(store, signals, on_started=on_started)
            controller.queue_state("open")
            append(store, request)
        # The same holder and signal owner survive every queue→engine promotion.
        controller.run()
        receipt = lookup(store, request)
        assert receipt is not None
        return receipt
    finally:
        owner.close()
        store.close()
