"""Shared standalone Core operations; mutators return exact durable receipts."""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import uuid
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import cast

from .control_requests import (
    canonical,
    envelope,
    receipt_base,
    validate_envelope,
)
from .controller import configured, execute
from .execution import AdapterConfiguration, AdapterResolver, _adapter
from .graph import (
    capture,
    capture_candidate,
    decode_revision,
    parse_graph,
    resolve_nodes,
)
from .ownership import (
    Config,
    _read_owner,
    atomic_json,
    canonical_path,
    check_id,
    file_lock,
    new_id,
)
from .planes import check_run_compatibility
from .records import TERMINAL, RouterError, fields, mapping, strict_json, text
from .store import FORMAT, Store
from .store_validation import _settings

_UNSET = object()

__all__ = [
    "create_instance",
    "list_instances",
    "inspect_instance",
    "update_instance_config",
    "validate_revision",
    "submit_revision",
    "start_run",
    "inspect_run",
    "recover_run",
    "pause_run",
    "resume_run",
    "accept_revision",
    "pause_plane",
    "resume_plane",
    "cancel_plane",
    "redirect_plane",
    "retry_invocation",
    "cancel_run",
    "reconcile_invocation",
    "release_project_hold",
    "execute_request",
    "load_instance",
]


def _external_receipt(
    request: dict[str, object], value: object, *, instance_id: str | None = None
) -> dict[str, object]:
    receipt = mapping(value)
    status = receipt.get("status")
    if (
        status not in {"applied", "refused"}
        or type(receipt.get("retryable")) is not bool
    ):
        raise RouterError("control_integrity")
    expected = receipt_base(request, str(status))
    if instance_id is not None and status == "applied":
        expected["instance_id"] = instance_id
    if any(receipt.get(key) != item for key, item in expected.items()):
        raise RouterError("control_integrity")
    if (status == "applied" and receipt.get("code") is not None) or (
        status == "refused" and not isinstance(receipt.get("code"), str)
    ):
        raise RouterError("control_integrity")
    return receipt


def load_instance(
    instance_id: str,
    state_home: Path,
    *,
    adapter_resolver: AdapterResolver | None = None,
) -> Config:
    check_id(instance_id)
    directory = canonical_path(state_home) / "instances" / instance_id
    if canonical_path(directory) != directory:
        raise RouterError("instance_integrity")
    if (
        not (directory / "instance.json").is_file()
        and (directory / "config.json").exists()
    ):
        raise RouterError("unsupported_store_version")
    try:
        obj = fields(
            strict_json((directory / "instance.json").read_bytes()),
            {"format", "instance_id", "project_root", "workflow_file"},
        )
        project, workflow = (
            Path(text(obj["project_root"])),
            Path(text(obj["workflow_file"])),
        )
        if (
            obj["format"] != "millrace-router-instance/v2"
            or obj["instance_id"] != instance_id
            or not project.is_absolute()
            or ".." in project.parts
            or str(project) != obj["project_root"]
            or not workflow.is_absolute()
            or ".." in workflow.parts
            or str(workflow) != obj["workflow_file"]
        ):
            raise RouterError("instance_integrity")
        connection = sqlite3.connect(
            (directory / "state.sqlite3").as_uri() + "?mode=ro", uri=True
        )
        try:
            meta = dict(connection.execute("SELECT key,value FROM meta"))
            if meta.get("format") != FORMAT:
                raise RouterError("unsupported_store_version")
            if strict_json(meta["identity"]) != obj:
                raise RouterError("instance_integrity")
            try:
                settings = _settings(strict_json(meta["settings"]))
            except RouterError as exc:
                raise RouterError("store_integrity") from exc
            creation = mapping(strict_json(meta["creation"]))
            request = mapping(creation["request"])
            check_id(cast(str, request["request_id"]))
            expected_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                str(canonical_path(state_home)) + "/" + str(request["request_id"]),
            ).hex
            if expected_id != instance_id:
                raise RouterError("instance_integrity")
        finally:
            connection.close()
        config = Config(instance_id, project, workflow, directory, (), (), {})
        selected = configured(config, settings)
        # Loading identity checks declared execution dependencies, not mutable
        # control history; execution-capable operations validate the full Store.
        connection = sqlite3.connect(
            (directory / "state.sqlite3").as_uri() + "?mode=ro", uri=True
        )
        try:
            for row in connection.execute("SELECT id,body FROM revisions"):
                revision = decode_revision(str(row[1]), str(row[0]))
                for binding in revision.graph.binding_configs.values():
                    _adapter(
                        str(binding["adapter"]),
                        str(binding["contract"]),
                        adapter_resolver,
                    )
            for name, raw in mapping(
                mapping(settings["runtime_defaults"]).get("adapter_settings", {})
            ).items():
                entry = mapping(raw)
                _adapter(
                    name, str(entry["contract"]), adapter_resolver
                ).validate_configuration(
                    AdapterConfiguration(
                        adapter_id=name,
                        adapter_contract=str(entry["contract"]),
                        bindings={},
                        nodes={},
                        runtime=mapping(entry["config"]),
                        workflow={},
                        planes={},
                        files={},
                        host_paths=selected.host_paths,
                        environment_names=selected.env_allowlist,
                    )
                )
        finally:
            connection.close()
        return selected
    except RouterError as exc:
        if exc.code.endswith("_integrity") or exc.code in {
            "unsupported_store_version",
            "unsupported_adapter_contract",
        }:
            raise
        raise RouterError("instance_integrity") from exc
    except (OSError, sqlite3.Error, KeyError, TypeError, ValueError) as exc:
        raise RouterError("instance_integrity") from exc


def _creation(
    request: dict[str, object],
    home: Path,
    *,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    validate_envelope(request, creation=True)
    payload = mapping(request["payload"])
    workflow, project = (
        Path(text(payload["workflow_file"])),
        Path(text(payload["project_root"])),
    )
    instance_id = uuid.uuid5(
        uuid.NAMESPACE_URL, str(home) + "/" + str(request["request_id"])
    ).hex
    directory = home / "instances" / instance_id
    home.mkdir(parents=True, exist_ok=True)
    with file_lock(home / ".registration.lock", blocking=True):
        receipts = home / "creation-receipts"
        receipt_path = receipts / (str(request["request_id"]) + ".json")
        if directory.exists():
            if canonical_path(directory) != directory:
                raise RouterError("instance_integrity")
            connection = sqlite3.connect(
                (directory / "state.sqlite3").as_uri() + "?mode=ro", uri=True
            )
            try:
                meta = dict(connection.execute("SELECT key,value FROM meta"))
                if meta.get("format") != FORMAT:
                    raise RouterError("unsupported_store_version")
                identity = fields(
                    strict_json((directory / "instance.json").read_bytes()),
                    {"format", "instance_id", "project_root", "workflow_file"},
                )
                if (
                    identity != strict_json(meta["identity"])
                    or identity["instance_id"] != instance_id
                ):
                    raise RouterError("instance_integrity")
                old = mapping(strict_json(meta["creation"]))
            finally:
                connection.close()
            if canonical(old["request"]) != canonical(request):
                raise RouterError("request_id_reused")
            receipt = _external_receipt(
                request, old["receipt"], instance_id=instance_id
            )
            if (
                receipt["status"] != "applied"
                or receipt.get("state_version") != 1
                or receipt.get("action") != "created"
                or identity["project_root"] != payload["project_root"]
                or identity["workflow_file"] != payload["workflow_file"]
            ):
                raise RouterError("control_integrity")
            load_instance(instance_id, home, adapter_resolver=adapter_resolver)
            return receipt
        if receipt_path.exists():
            old = mapping(strict_json(receipt_path.read_bytes()))
            if canonical(old["request"]) != canonical(request):
                raise RouterError("request_id_reused")
            receipt = _external_receipt(
                request, old["receipt"], instance_id=instance_id
            )
            if receipt["status"] != "refused" or receipt.get("state_version") != 0:
                raise RouterError("control_integrity")
            return receipt
        try:
            if (
                not workflow.is_absolute()
                or canonical_path(workflow) != workflow
                or not project.is_absolute()
                or canonical_path(project) != project
            ):
                raise RouterError("invalid_roots")
            config = configured(
                Config(instance_id, project, workflow, directory, (), (), {}),
                {**payload, "state_version": 1},
            )
            if request["expected_state_version"] != 0:
                raise RouterError("stale_state_version")
            revision = capture(
                workflow, project, search_path=config.path, host_paths=config.host_paths
            )
            resolve_nodes(
                revision,
                settings_context=config.settings(),
                adapter_resolver=adapter_resolver,
                project_root=config.project_root,
            )
            instances = home / "instances"
            instances.mkdir(exist_ok=True)
            for entry in instances.iterdir():
                if not entry.is_dir():
                    raise RouterError("instance_integrity")
                registered = load_instance(
                    entry.name, home, adapter_resolver=adapter_resolver
                ).workflow_file.parent
                if registered == workflow.parent:
                    raise RouterError("workflow_registered")
                try:
                    registered.stat()
                except (FileNotFoundError, NotADirectoryError):
                    continue
                except OSError as exc:
                    raise RouterError("instance_integrity") from exc
                try:
                    duplicate = registered.samefile(workflow.parent)
                except OSError as exc:
                    raise RouterError("instance_integrity") from exc
                if duplicate:
                    raise RouterError("workflow_registered")
            receipt = receipt_base(request, "applied")
            receipt.update(
                instance_id=instance_id,
                state_version=1,
                action="created",
                code=None,
                retryable=False,
            )
            stage = home / (".creating-" + str(request["request_id"]) + "-" + new_id())
            stage.mkdir()
            staged = Config(
                instance_id,
                project,
                workflow,
                stage,
                config.path,
                config.env_allowlist,
                config.host_paths,
                config.instance_version,
                config.runtime_defaults,
                config.operator_policy,
            )
            try:
                atomic_json(stage / "instance.json", config.identity())
                store = Store(
                    staged,
                    create=True,
                    creation={"request": request, "receipt": receipt},
                    adapter_resolver=adapter_resolver,
                )
                with store.transaction():
                    store.db.execute(
                        "INSERT OR IGNORE INTO revisions VALUES(?,?)",
                        (revision.revision_id, revision.encoded()),
                    )
                store.close()
                os.rename(stage, directory)
                descriptor = os.open(instances, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
            return receipt
        except RouterError as exc:
            if directory.exists():
                # Publication is authoritative even if response-side work failed.
                raise
            receipt = receipt_base(request, "refused")
            receipt.update(code=exc.code, retryable=False, state_version=0)
            receipts.mkdir(exist_ok=True)
            atomic_json(receipt_path, {"request": request, "receipt": receipt})
            return receipt


def create_instance(
    workflow_file: Path,
    project_root: Path,
    *,
    state_home: Path,
    request_id: str,
    expected_state_version: int,
    path: tuple[str, ...] | None = None,
    env_allowlist: tuple[str, ...] = (),
    host_paths: dict[str, str] | None = None,
    runtime_defaults: object = _UNSET,
    operator_policy: object = _UNSET,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    selected_path = (
        path
        if path is not None
        else tuple(
            dict.fromkeys([str(Path(sys.executable).parent), *os.get_exec_path()])
        )
    )
    request: dict[str, object] = {
        "format": "millrace-router-request/v2",
        "operation": "create_instance",
        "instance_id": None,
        "run_id": None,
        "plane_id": None,
        "request_id": request_id,
        "expected_state_version": expected_state_version,
        "payload": {
            "workflow_file": str(canonical_path(workflow_file)),
            "project_root": str(canonical_path(project_root)),
            "path": list(selected_path),
            "env_allowlist": list(env_allowlist),
            "host_paths": host_paths or {},
        },
    }
    # Preserve omission in the request while admitting explicit null for a
    # durable semantic refusal after reachable receipt lookup.
    payload = mapping(request["payload"])
    if runtime_defaults is not _UNSET:
        payload["runtime_defaults"] = runtime_defaults
    if operator_policy is not _UNSET:
        payload["operator_policy"] = operator_policy
    return execute_request(
        request, state_home=state_home, adapter_resolver=adapter_resolver
    )


def list_instances(
    *, state_home: Path, adapter_resolver: AdapterResolver | None = None
) -> list[dict[str, object]]:
    folder = canonical_path(state_home) / "instances"
    if not folder.exists():
        return []
    return [
        inspect_instance(
            entry.name, state_home=state_home, adapter_resolver=adapter_resolver
        )
        for entry in sorted(folder.iterdir())
    ]


def inspect_instance(
    instance_id: str,
    *,
    state_home: Path,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    config = load_instance(instance_id, state_home, adapter_resolver=adapter_resolver)
    store = Store(config, readonly=True, adapter_resolver=adapter_resolver)
    try:
        current = configured(config, store.instance_settings())
        result = current.data()
        result["runs"] = [
            {
                key: run[key]
                for key in (
                    "id",
                    "status",
                    "mode",
                    "state_version",
                    "revision_id",
                    "pending_seq",
                    "pause_reason",
                )
            }
            for row in store.db.execute("SELECT body FROM runs ORDER BY rowid")
            for run in [mapping(strict_json(str(row[0])))]
        ]
        result["controls"] = [
            mapping(strict_json(str(row[0])))
            for row in store.db.execute(
                "SELECT receipt FROM controls WHERE run_id IS NULL ORDER BY seq"
            )
        ]
        return result
    finally:
        store.close()


def validate_revision(
    instance_id: str,
    *,
    state_home: Path,
    workflow_file: Path | None = None,
    run_id: str | None = None,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    config = load_instance(instance_id, state_home, adapter_resolver=adapter_resolver)
    store = Store(config, readonly=True, adapter_resolver=adapter_resolver)
    try:
        run = store.run(run_id) if run_id is not None else None
        if run_id is not None:
            check_id(run_id)
        settings = (
            mapping(run["host_settings"])
            if run is not None
            else store.instance_settings()
        )
        config = configured(config, settings)
        # Only a targeted run supplies retained workspace/topology authority.
        baseline = store.revision(str(run["revision_id"])) if run is not None else None
        context: dict[str, object] = {
            "kind": "run" if run is not None else "instance",
            "instance_id": instance_id,
            "run_id": run_id,
            "settings_version": settings["state_version"],
        }
        if run is not None:
            context["run_state_version"] = run["state_version"]
        try:
            draft = capture_candidate(
                canonical_path(workflow_file)
                if workflow_file
                else config.workflow_file,
                config.project_root,
                search_path=config.path,
                host_paths=config.host_paths,
                baseline=baseline,
            )
            details: list[str] = []
            resolved: dict[str, dict[str, dict[str, object]]] = {}
            diagnostic = draft.diagnostic
            if diagnostic is not None:
                try:
                    parse_graph(draft.files.get("workflow.json") or b"")
                except RouterError as exc:
                    details.append(str(exc))
                missing = [name for name, raw in draft.files.items() if raw is None]
                if missing:
                    details.append(
                        "Missing control files: " + ", ".join(sorted(missing))
                    )
            elif draft.revision is not None:
                try:
                    if run_id is not None:
                        startup, planes, unrouted = store.compatibility_facts(run_id)
                        check_run_compatibility(
                            startup, draft.revision, planes, unrouted
                        )
                    resolved = resolve_nodes(
                        draft.revision,
                        settings_context=settings,
                        adapter_resolver=adapter_resolver,
                        project_root=config.project_root,
                    )
                except RouterError as exc:
                    diagnostic = exc.code
                    details.append(str(exc))
            return {
                "valid": diagnostic is None,
                "revision_id": draft.revision.revision_id if draft.revision else None,
                "draft_revision": draft.data(),
                "diagnostics": [diagnostic] if diagnostic else [],
                "diagnostic_details": details,
                "validation_context": context,
                "resolved_nodes": resolved,
                "warnings": [
                    warning
                    for plane in resolved.values()
                    for node in plane.values()
                    for warning in cast(
                        list[object], mapping(node["common"])["warnings"]
                    )
                ],
            }
        except RouterError as exc:
            return {
                "valid": False,
                "revision_id": None,
                "diagnostics": [exc.code],
                "validation_context": context,
                "resolved_nodes": {},
                "warnings": [],
            }
    finally:
        store.close()


def inspect_run(
    instance_id: str,
    run_id: str,
    *,
    state_home: Path,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    check_id(run_id)
    store = Store(
        load_instance(instance_id, state_home, adapter_resolver=adapter_resolver),
        readonly=True,
        adapter_resolver=adapter_resolver,
    )
    try:
        return store.inspection(run_id)
    finally:
        store.close()


def _run_control(
    op: str,
    instance_id: str,
    run_id: str | None,
    state_home: Path,
    request_id: str,
    expected: int | None,
    payload: dict[str, object],
    *,
    plane_id: str | None = None,
    on_started: Callable[[dict[str, object]], None] | None = None,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    return execute_request(
        envelope(
            op, instance_id, run_id, request_id, expected, payload, plane_id=plane_id
        ),
        state_home=state_home,
        on_started=on_started,
        adapter_resolver=adapter_resolver,
    )


def start_run(
    instance_id: str,
    *,
    state_home: Path,
    request_id: str,
    expected_state_version: int,
    edit_mode: str = "locked",
    on_started: Callable[[dict[str, object]], None] | None = None,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    return _run_control(
        "start_run",
        instance_id,
        None,
        state_home,
        request_id,
        expected_state_version,
        {"edit_mode": edit_mode},
        on_started=on_started,
        adapter_resolver=adapter_resolver,
    )


def recover_run(
    instance_id: str,
    run_id: str,
    *,
    state_home: Path,
    request_id: str,
    expected_state_version: int,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    return _run_control(
        "recover_run",
        instance_id,
        run_id,
        state_home,
        request_id,
        expected_state_version,
        {},
        adapter_resolver=adapter_resolver,
    )


def pause_run(
    instance_id: str,
    run_id: str,
    *,
    state_home: Path,
    request_id: str,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    return _run_control(
        "pause_run",
        instance_id,
        run_id,
        state_home,
        request_id,
        None,
        {},
        adapter_resolver=adapter_resolver,
    )


def cancel_run(
    instance_id: str,
    run_id: str,
    *,
    state_home: Path,
    request_id: str,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    return _run_control(
        "cancel_run",
        instance_id,
        run_id,
        state_home,
        request_id,
        None,
        {},
        adapter_resolver=adapter_resolver,
    )


def resume_run(
    instance_id: str,
    run_id: str,
    *,
    state_home: Path,
    request_id: str,
    expected_state_version: int,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    return _run_control(
        "resume_run",
        instance_id,
        run_id,
        state_home,
        request_id,
        expected_state_version,
        {},
        adapter_resolver=adapter_resolver,
    )


def submit_revision(
    instance_id: str,
    run_id: str,
    *,
    state_home: Path,
    request_id: str,
    expected_state_version: int,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    return _run_control(
        "submit_revision",
        instance_id,
        run_id,
        state_home,
        request_id,
        expected_state_version,
        {},
        adapter_resolver=adapter_resolver,
    )


def accept_revision(
    instance_id: str,
    run_id: str,
    *,
    candidate_id: str,
    state_home: Path,
    request_id: str,
    expected_state_version: int,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    return _run_control(
        "accept_revision",
        instance_id,
        run_id,
        state_home,
        request_id,
        expected_state_version,
        {"candidate_id": candidate_id},
        adapter_resolver=adapter_resolver,
    )


def retry_invocation(
    instance_id: str,
    run_id: str,
    *,
    plane_id: str,
    invocation_id: str,
    reason: str,
    state_home: Path,
    request_id: str,
    expected_state_version: int,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    return _run_control(
        "retry_invocation",
        instance_id,
        run_id,
        state_home,
        request_id,
        expected_state_version,
        {"invocation_id": invocation_id, "reason": reason},
        plane_id=plane_id,
        adapter_resolver=adapter_resolver,
    )


def reconcile_invocation(
    instance_id: str,
    run_id: str,
    *,
    plane_id: str,
    invocation_id: str,
    reason: str,
    stopped: bool,
    state_home: Path,
    request_id: str,
    expected_state_version: int,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    return _run_control(
        "reconcile_invocation",
        instance_id,
        run_id,
        state_home,
        request_id,
        expected_state_version,
        {"invocation_id": invocation_id, "reason": reason, "stopped": stopped},
        plane_id=plane_id,
        adapter_resolver=adapter_resolver,
    )


def update_instance_config(
    instance_id: str,
    *,
    settings: dict[str, object],
    state_home: Path,
    request_id: str,
    expected_state_version: int,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    return _run_control(
        "update_instance_config",
        instance_id,
        None,
        state_home,
        request_id,
        expected_state_version,
        {"settings": settings},
        adapter_resolver=adapter_resolver,
    )


def pause_plane(
    instance_id: str,
    run_id: str,
    *,
    plane_id: str,
    state_home: Path,
    request_id: str,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    return _run_control(
        "pause_plane",
        instance_id,
        run_id,
        state_home,
        request_id,
        None,
        {},
        plane_id=plane_id,
        adapter_resolver=adapter_resolver,
    )


def cancel_plane(
    instance_id: str,
    run_id: str,
    *,
    plane_id: str,
    state_home: Path,
    request_id: str,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    return _run_control(
        "cancel_plane",
        instance_id,
        run_id,
        state_home,
        request_id,
        None,
        {},
        plane_id=plane_id,
        adapter_resolver=adapter_resolver,
    )


def resume_plane(
    instance_id: str,
    run_id: str,
    *,
    plane_id: str,
    state_home: Path,
    request_id: str,
    expected_state_version: int,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    return _run_control(
        "resume_plane",
        instance_id,
        run_id,
        state_home,
        request_id,
        expected_state_version,
        {},
        plane_id=plane_id,
        adapter_resolver=adapter_resolver,
    )


def redirect_plane(
    instance_id: str,
    run_id: str,
    *,
    plane_id: str,
    node_id: str,
    reason: str,
    state_home: Path,
    request_id: str,
    expected_state_version: int,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    return _run_control(
        "redirect_plane",
        instance_id,
        run_id,
        state_home,
        request_id,
        expected_state_version,
        {"node_id": node_id, "reason": reason},
        plane_id=plane_id,
        adapter_resolver=adapter_resolver,
    )


def execute_request(
    request: dict[str, object],
    *,
    state_home: Path,
    on_started: Callable[[dict[str, object]], None] | None = None,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    home = canonical_path(state_home)
    if request.get("operation") == "create_instance":
        return _creation(request, home, adapter_resolver=adapter_resolver)
    if request.get("operation") == "release_project_hold":
        return _release(request, home, adapter_resolver=adapter_resolver)
    validate_envelope(request)
    return execute(
        load_instance(
            cast(str, request["instance_id"]), home, adapter_resolver=adapter_resolver
        ),
        request,
        on_started=on_started,
        adapter_resolver=adapter_resolver,
    )


def release_project_hold(
    *,
    project_root: Path,
    hold_id: str,
    owner_instance_id: str,
    owner_run_id: str,
    reason: str,
    stopped: bool,
    state_home: Path,
    request_id: str,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    request: dict[str, object] = {
        "format": "millrace-router-request/v2",
        "operation": "release_project_hold",
        "instance_id": owner_instance_id,
        "run_id": owner_run_id,
        "plane_id": None,
        "request_id": request_id,
        "expected_state_version": None,
        "payload": {
            "project_root": str(canonical_path(project_root)),
            "hold_id": hold_id,
            "reason": reason,
            "stopped": stopped,
        },
    }
    return execute_request(
        request, state_home=state_home, adapter_resolver=adapter_resolver
    )


def _release(
    request: dict[str, object],
    home: Path,
    *,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    fields(
        request,
        {
            "format",
            "operation",
            "instance_id",
            "run_id",
            "plane_id",
            "request_id",
            "expected_state_version",
            "payload",
        },
    )
    for key in ("request_id", "instance_id", "run_id"):
        check_id(cast(str, request[key]))
    payload = fields(
        request["payload"], {"project_root", "hold_id", "reason", "stopped"}
    )
    check_id(cast(str, payload["hold_id"]))
    text(payload["reason"])
    if (
        request["format"] != "millrace-router-request/v2"
        or request["expected_state_version"] is not None
        or request["plane_id"] is not None
        or payload["stopped"] is not True
    ):
        raise RouterError("writers_not_confirmed")
    project = Path(text(payload["project_root"]))
    folder = project / ".millrace-router-owner"
    # Admit the owner's execution dependencies before receipt writes or replay.
    # Unreadable owners retain the existing durable orphan-refusal behavior.
    try:
        load_instance(
            str(request["instance_id"]), home, adapter_resolver=adapter_resolver
        )
    except RouterError as exc:
        if exc.code == "unsupported_adapter_contract":
            raise
    receipts = home / "orphan-receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    receipt_path = receipts / (str(request["request_id"]) + ".json")
    expected = {
        "hold_id": payload["hold_id"],
        "instance_id": request["instance_id"],
        "run_id": request["run_id"],
        "store_path": str(
            home / "instances" / str(request["instance_id"]) / "state.sqlite3"
        ),
    }
    with file_lock(home / ".orphan-receipts.lock", blocking=True):
        path = folder / "owner.json"
        if receipt_path.exists():
            old = mapping(strict_json(receipt_path.read_bytes()))
            if old["request"] != request:
                raise RouterError("request_id_reused")
            receipt = _external_receipt(request, old["receipt"])
            if receipt["status"] == "applied" and (
                receipt.get("original_hold") != expected
                or receipt.get("reason") != payload["reason"]
                or receipt.get("stopped") is not True
                or receipt.get("action") != "hold_released"
            ):
                raise RouterError("control_integrity")
            if (
                receipt["status"] == "applied"
                and project.is_absolute()
                and canonical_path(project) == project
                and folder.is_dir()
            ):
                with file_lock(folder / "lock", blocking=True):
                    if _read_owner(path, missing_ok=True) == expected:
                        _unlink_hold(path)
            return receipt
        with ExitStack() as locks:
            try:
                if (
                    not project.is_absolute()
                    or canonical_path(project) != project
                    or not project.is_dir()
                ):
                    raise RouterError("invalid_roots")
                folder.mkdir(exist_ok=True)
                locks.enter_context(file_lock(folder / "lock", blocking=True))
                if _read_owner(path, missing_ok=True) != expected:
                    raise RouterError("project_owned")
                owner_dir = home / "instances" / str(request["instance_id"])
                owner_path = owner_dir / "state.sqlite3"
                # Existing owner state is checked before the controller guard: a
                # nonterminal owner always reports its normal non-orphan refusal.
                try:
                    owner_path.lstat()
                    present = True
                except (FileNotFoundError, NotADirectoryError):
                    present = False
                if present:
                    try:
                        owner_config = load_instance(
                            str(request["instance_id"]),
                            home,
                            adapter_resolver=adapter_resolver,
                        )
                        if owner_config.project_root != project:
                            raise RouterError("owner_store_unverifiable")
                        owner_store = Store(
                            owner_config,
                            readonly=True,
                            adapter_resolver=adapter_resolver,
                        )
                        try:
                            owner_run = owner_store.run(str(request["run_id"]))
                            if (
                                owner_run["hold_id"] != payload["hold_id"]
                                or owner_config.project_root != project
                            ):
                                raise RouterError("owner_store_unverifiable")
                            if owner_run["status"] not in TERMINAL:
                                raise RouterError("owner_run_nonterminal")
                        finally:
                            owner_store.close()
                    except RouterError as exc:
                        if exc.code == "owner_run_nonterminal":
                            raise
                        raise RouterError("owner_store_unverifiable") from exc
                if owner_dir.exists():
                    with file_lock(owner_dir / ".controller.lock"):
                        pass
                receipt = receipt_base(request, "applied")
                receipt.update(
                    code=None,
                    retryable=False,
                    action="hold_released",
                    original_hold=expected,
                    reason=payload["reason"],
                    stopped=True,
                )
                atomic_json(receipt_path, {"request": request, "receipt": receipt})
            except (RouterError, OSError) as exc:
                receipt = receipt_base(request, "refused")
                receipt.update(
                    code=exc.code
                    if isinstance(exc, RouterError)
                    else "owner_store_unverifiable",
                    retryable=False,
                )
                atomic_json(receipt_path, {"request": request, "receipt": receipt})
                return receipt
            _unlink_hold(path)
            return receipt


def _unlink_hold(path: Path) -> None:
    path.unlink()
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
