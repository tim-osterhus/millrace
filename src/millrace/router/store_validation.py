"""Replay selected revision, route and operator authority without repairing history."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .control_requests import (
    canonical,
    digest,
    validate_envelope,
    validate_requests,
)
from .execution import CompletionFacts, _adapter, _result
from .graph import (
    Revision,
    decode_revision,
    resolve_settings,
)
from .ownership import check_id
from .records import (
    TERMINAL,
    RouterError,
    common_settings,
    fields,
    mapping,
    number,
    parse_result,
    recorded_result,
    select_action,
    strict_json,
    strings,
    text,
)

if TYPE_CHECKING:
    from .store import Store


def _settings(value: object) -> dict[str, object]:
    from pathlib import Path

    try:
        obj = mapping(value)
        if set(obj) != {
            "path",
            "env_allowlist",
            "host_paths",
            "state_version",
            "runtime_defaults",
            "operator_policy",
        }:
            raise RouterError("store_integrity")
        if any(not Path(name).is_absolute() for name in strings(obj["path"])):
            raise RouterError("store_integrity")
        strings(obj["env_allowlist"])
        for key, item in mapping(obj["host_paths"]).items():
            text(key)
            text(item)
        number(obj["state_version"], integer=True)
        common_settings(obj["runtime_defaults"], location="runtime_defaults")
        common_settings(obj["operator_policy"], policy=True, location="operator_policy")
    except RouterError as exc:
        raise RouterError("store_integrity") from exc
    return obj


def _applied(
    store: Store,
    request_id: object,
    run_id: str,
    operation: str | None = None,
    *,
    version: int,
) -> dict[str, object]:
    row = store.db.execute(
        "SELECT body,status,receipt FROM controls WHERE request_id=?", (request_id,)
    ).fetchone()
    if row is None or row["status"] != "applied":
        raise RouterError("control_integrity")
    request = mapping(strict_json(str(row["body"])))
    if request["run_id"] != run_id or (
        operation is not None and request["operation"] != operation
    ):
        raise RouterError("control_integrity")
    receipt = mapping(strict_json(str(row["receipt"])))
    if receipt.get("state_version") != version or (
        request["expected_state_version"] is not None
        and request["expected_state_version"] != version - 1
    ):
        raise RouterError("control_integrity")
    op = str(request["operation"])
    actions = {
        "submit_revision": {"submitted"},
        "accept_revision": {"candidate_superseded", "revision_accepted"},
        "pause_run": {"paused"},
        "cancel_run": {"cancelled", "cancel_requested"},
        "resume_run": {"resumed"},
        "retry_invocation": {"retrying"},
        "redirect_plane": {"redirected"},
        "pause_plane": {"paused", "cancel_requested"},
        "cancel_plane": {"cancel_requested"},
        "resume_plane": {"resumed"},
        "reconcile_invocation": {"writers_confirmed"},
    }
    if receipt.get("action") not in actions.get(op, set()):
        raise RouterError("control_integrity")
    if (
        op in {"retry_invocation", "reconcile_invocation"}
        and receipt.get("invocation_id") != mapping(request["payload"])["invocation_id"]
    ):
        raise RouterError("control_integrity")
    if op == "pause_run":
        first = next(
            (
                item["request_id"]
                for item in store.db.execute(
                    "SELECT request_id,receipt FROM controls WHERE run_id=? "
                    "AND operation='pause_run' AND status='applied' ORDER BY seq",
                    (run_id,),
                )
                if mapping(strict_json(str(item["receipt"]))).get("state_version")
                == version
            ),
            None,
        )
        if first != request_id:
            raise RouterError("control_integrity")
    return request


def _control_facts(store: Store, run_id: str) -> None:
    """Applied operator effects must retain their corresponding fact links."""
    required = {
        ("submit_revision", "submitted"): ("candidates",),
        ("accept_revision", "candidate_superseded"): ("candidates",),
        ("accept_revision", "revision_accepted"): ("activations", "states"),
        ("resume_run", "resumed"): ("states",),
        ("cancel_run", "cancelled"): ("states",),
        ("retry_invocation", "retrying"): ("moves",),
        ("redirect_plane", "redirected"): ("moves",),
        ("reconcile_invocation", "writers_confirmed"): ("writer_facts",),
    }
    for row in store.db.execute(
        "SELECT * FROM controls WHERE run_id=? AND status='applied'", (run_id,)
    ):
        receipt = mapping(strict_json(str(row["receipt"])))
        tables = required.get((str(row["operation"]), str(receipt.get("action"))), ())
        for table in tables:
            if (
                store.db.execute(
                    f"SELECT 1 FROM {table} WHERE request_id=? AND version=?",
                    (row["request_id"], receipt.get("state_version")),
                ).fetchone()
                is None
            ):
                raise RouterError("control_integrity")
        gate_kind = (
            "set"
            if (row["operation"], receipt.get("action")) == ("pause_run", "paused")
            else "clear"
            if (row["operation"], receipt.get("action"))
            in {("resume_run", "resumed"), ("accept_revision", "revision_accepted")}
            else None
        )
        if gate_kind is not None:
            matches = [
                item
                for item in store.db.execute(
                    "SELECT * FROM run_gate_events WHERE run_id=? "
                    "AND version=? AND kind=?",
                    (run_id, receipt.get("state_version"), gate_kind),
                )
                if mapping(strict_json(str(item["body"]))).get("request_id")
                == row["request_id"]
            ]
            if len(matches) != 1:
                raise RouterError("control_integrity")


def validate_store(store: Store) -> None:
    snapshot = not store.db.in_transaction
    try:
        if snapshot:
            store.db.execute("BEGIN")
        _validate(store)
    except RouterError:
        raise
    except (
        sqlite3.Error,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        OverflowError,
        AttributeError,
    ) as exc:
        raise RouterError("store_integrity") from exc
    finally:
        if snapshot and store.db.in_transaction:
            store.db.execute("ROLLBACK")


def _validate(store: Store) -> None:
    from .store import FORMAT

    meta = dict(store.db.execute("SELECT key,value FROM meta"))
    if meta.get("format") != FORMAT:
        raise RouterError("unsupported_store_version")
    if (
        set(meta) != {"format", "identity", "settings", "creation"}
        or strict_json(meta["identity"]) != store.config.identity()
    ):
        raise RouterError("store_integrity")
    instance_context = _settings(strict_json(meta["settings"]))
    for adapter_id, raw in mapping(
        mapping(instance_context["runtime_defaults"]).get("adapter_settings", {})
    ).items():
        from .execution import AdapterConfiguration

        namespace = mapping(raw)
        _adapter(
            adapter_id, str(namespace["contract"]), store.adapter_resolver
        ).validate_configuration(
            AdapterConfiguration(
                adapter_id=adapter_id,
                adapter_contract=str(namespace["contract"]),
                bindings={},
                nodes={},
                runtime=mapping(namespace["config"]),
                workflow={},
                planes={},
                files={},
                host_paths={
                    k: str(v)
                    for k, v in mapping(instance_context["host_paths"]).items()
                },
                environment_names=strings(instance_context["env_allowlist"]),
            )
        )
    if (
        store.db.execute("PRAGMA quick_check").fetchone()[0] != "ok"
        or store.db.execute("PRAGMA foreign_key_check").fetchone()
    ):
        raise RouterError("store_integrity")
    validate_requests(store)
    _instance_authority(store, meta)
    revisions = {
        str(row[0]): decode_revision(str(row[1]), str(row[0]))
        for row in store.db.execute("SELECT * FROM revisions")
    }
    for captured_revision in revisions.values():
        for binding in captured_revision.graph.binding_configs.values():
            _adapter(
                str(binding["adapter"]),
                str(binding["contract"]),
                store.adapter_resolver,
            )
    nonterminal = 0
    for row in store.db.execute("SELECT * FROM runs"):
        _run_v7(store, row, revisions)
        nonterminal += mapping(strict_json(str(row["body"])))["status"] not in TERMINAL
    if nonterminal > 1:
        raise RouterError("run_nonterminal")


def _run_v7(store: Store, row: sqlite3.Row, revisions: dict[str, Revision]) -> None:
    import copy

    from .plane_store import initial_states
    from .planes import check_run_compatibility
    from .seams import _envelope, acknowledges

    run = mapping(strict_json(str(row["body"])))
    run_id = str(row["id"])
    check_id(run_id)
    check_id(str(run["hold_id"]))
    if (run["id"], run["instance_id"], run["revision_id"], run["hold_id"]) != (
        run_id,
        store.config.instance_id,
        row["revision_id"],
        row["hold_id"],
    ):
        raise RouterError("run_integrity")
    fields(
        run,
        {
            "id",
            "instance_id",
            "revision_id",
            "startup_revision_id",
            "hold_id",
            "status",
            "mode",
            "run_gate",
            "last_admitted_plane",
            "pending_seq",
            "pause_reason",
            "count",
            "state_version",
            "cap",
            "max_elapsed",
            "startup_cap",
            "startup_elapsed",
            "started_at",
            "startup_deadline",
            "deadline",
            "host_settings",
            "cancel_requested",
            "start_request_id",
        },
    )
    if (
        run["mode"] not in {"locked", "live"}
        or type(run["cancel_requested"]) is not bool
    ):
        raise RouterError("run_integrity")
    _control_facts(store, run_id)
    host = _settings(run["host_settings"])
    initial = revisions[str(run["startup_revision_id"])]
    active = initial
    started = number(run["started_at"])
    if (run["startup_cap"], run["startup_elapsed"], run["startup_deadline"]) != (
        initial.graph.max_invocations,
        initial.graph.max_elapsed,
        started + initial.graph.max_elapsed,
    ):
        raise RouterError("counter_integrity")
    for key in ("state_version", "cap", "startup_cap", "count"):
        number(run[key], integer=True, zero=key == "count")
    versions = [
        r[0]
        for r in store.db.execute(
            "SELECT version FROM run_versions WHERE run_id=? ORDER BY version",
            (run_id,),
        )
    ]
    if versions != list(range(1, cast(int, run["state_version"]) + 1)):
        raise RouterError("counter_integrity")
    for plane_id, workspace in initial.workspaces.items():
        path = Path(str(workspace["path"]))
        if not path.is_relative_to(store.config.project_root) or (
            initial.graph.max_parallel > 1
            and path
            != store.config.project_root / initial.graph.planes[plane_id].workspace
        ):
            raise RouterError("revision_integrity")
    invocations = {
        str(item["id"]): item
        for item in store.db.execute(
            "SELECT * FROM invocations WHERE run_id=? ORDER BY attempt", (run_id,)
        )
    }
    bodies = {
        key: mapping(strict_json(str(item["body"])))
        for key, item in invocations.items()
    }
    if any("launch_settings" not in body for body in bodies.values()):
        raise RouterError("invocation_integrity")
    completions = {
        str(item["invocation_id"]): item
        for item in store.db.execute(
            "SELECT * FROM completions WHERE run_id=?", (run_id,)
        )
    }
    transitions = {
        str(item["completion_id"]): item
        for item in store.db.execute(
            "SELECT * FROM transitions WHERE run_id=?", (run_id,)
        )
    }
    for invocation_id, completion in completions.items():
        if invocation_id not in bodies:
            raise RouterError("completion_integrity")
        body = bodies[invocation_id]
        if "ref_admission" not in body or (
            completion["classification"] is None
            and (completion["outcome"] is not None or body["ref_admission"] is not None)
        ):
            raise RouterError("completion_integrity")
    writer_versions = {
        str(item["invocation_id"]): int(item["version"])
        for item in store.db.execute(
            "SELECT f.* FROM writer_facts f JOIN invocations i ON i.id=f.invocation_id "
            "WHERE i.run_id=?",
            (run_id,),
        )
    }

    def stopped_at(invocation_id: str, version: int) -> bool:
        body = bodies[invocation_id]
        return (
            body["completed_version"] is not None
            and cast(int, body["completed_version"]) <= version
            and (
                (
                    invocation_id in completions
                    and completions[invocation_id]["writer"] == "stopped"
                )
                or writer_versions.get(invocation_id, version + 1) <= version
            )
        )

    moves = list(
        store.db.execute(
            "SELECT * FROM moves WHERE run_id=? ORDER BY version", (run_id,)
        )
    )
    if len(invocations) != run["count"]:
        raise RouterError("counter_integrity")
    activations = list(
        store.db.execute(
            "SELECT * FROM activations WHERE run_id=? ORDER BY version", (run_id,)
        )
    )
    if not activations or (
        activations[0]["version"],
        activations[0]["revision_id"],
        activations[0]["candidate_seq"],
    ) != (1, initial.revision_id, None):
        raise RouterError("revision_integrity")
    initial_state = store.db.execute(
        "SELECT * FROM states WHERE run_id=? AND version=1", (run_id,)
    ).fetchone()
    if initial_state is None or (
        initial_state["status"],
        initial_state["reason"],
        initial_state["run_gate"],
        initial_state["request_id"],
    ) != ("ready", None, None, None):
        raise RouterError("run_integrity")
    states = initial_states(initial)
    retry_counts: dict[tuple[str, str], int] = {}
    gate: object = None
    status, reason = "ready", None
    last_plane: object = None
    causal_versions = {1}
    pending: int | None = None
    candidate_seq = 0
    candidates = list(
        store.db.execute(
            "SELECT * FROM candidates WHERE run_id=? ORDER BY seq", (run_id,)
        )
    )
    for candidate in candidates:
        candidate_seq += 1
        value = mapping(strict_json(str(candidate["body"])))
        if (
            candidate["seq"] != candidate_seq
            or value["seq"] != candidate_seq
            or value["supersedes"] != (candidate_seq - 1 or None)
        ):
            raise RouterError("revision_integrity")
        if value["revision_id"] is not None:
            revision = revisions[str(value["revision_id"])]
            if (
                value["candidate_id"] != revision.revision_id
                or value["diagnostic"] is not None
            ):
                raise RouterError("revision_integrity")
            if value["files"] != {
                name: base64.b64encode(raw).decode()
                for name, raw in revision.files.items()
            }:
                raise RouterError("revision_integrity")
        elif (
            value["candidate_id"]
            != hashlib.sha256(
                json.dumps(
                    {"files": value["files"], "diagnostic": value["diagnostic"]},
                    sort_keys=True,
                ).encode()
            ).hexdigest()
        ):
            raise RouterError("revision_integrity")
    events = list(
        store.db.execute(
            "SELECT * FROM plane_events WHERE run_id=? ORDER BY version,ordinal",
            (run_id,),
        )
    )
    if len(events) < len(states):
        raise RouterError("plane_integrity")
    expected_initial = list(states.items())
    for ordinal, (name, body) in enumerate(expected_initial):
        event = events[ordinal]
        if (event["version"], event["ordinal"], event["plane_id"], event["kind"]) != (
            1,
            ordinal,
            name,
            "created",
        ) or mapping(strict_json(str(event["body"]))) != body:
            raise RouterError("plane_integrity")
    reserved_seen: set[str] = set()
    transitions_seen: set[str] = set()
    moves_seen: set[tuple[int, str]] = set()
    occurrence_expected: dict[tuple[str, int], dict[str, object]] = {}
    selections_expected: dict[tuple[str, int], tuple[str, str, int]] = {}
    acks_expected: dict[tuple[str, int], tuple[str, int]] = {}
    cancellation_versions = [
        cast(int, receipt["state_version"])
        for item in store.db.execute(
            "SELECT receipt FROM controls WHERE run_id=? AND status='applied' "
            "AND operation='cancel_run'",
            (run_id,),
        )
        if (receipt := mapping(strict_json(str(item[0])))).get("action")
        in {"cancel_requested", "cancelled"}
    ]
    stop_versions: dict[int, dict[str, dict[str, object]]] = {}
    for version in versions:
        for candidate in candidates:
            if candidate["version"] == version:
                value = mapping(strict_json(str(candidate["body"])))
                pending = None if value["withdrawn"] else int(candidate["seq"])
                if value["withdrawn"] != (value["candidate_id"] == active.revision_id):
                    raise RouterError("revision_integrity")
                if candidate["request_id"] is not None:
                    _applied(store, candidate["request_id"], run_id, version=version)
                causal_versions.add(version)
        for activation in activations:
            if activation["version"] != version or version == 1:
                continue
            candidate_row = next(
                (c for c in candidates if c["seq"] == activation["candidate_seq"]), None
            )
            if candidate_row is None or pending != activation["candidate_seq"]:
                raise RouterError("revision_integrity")
            candidate = mapping(strict_json(str(candidate_row["body"])))
            proposed = revisions[str(activation["revision_id"])]
            if any(
                cast(int, b["version"]) < version
                and (
                    b["completed_version"] is None
                    or cast(int, b["completed_version"]) > version
                )
                for b in bodies.values()
            ):
                raise RouterError("revision_integrity")
            if candidate["revision_id"] != proposed.revision_id:
                raise RouterError("revision_integrity")
            unrouted = [
                {
                    "plane_id": b["plane_id"],
                    "node_id": b["node"],
                    "classification": completions[key]["classification"]
                    if key in completions
                    else None,
                    "outcome": completions[key]["outcome"]
                    if key in completions
                    else None,
                }
                for key, b in bodies.items()
                if cast(int, b["version"]) < version
                and (key not in transitions or transitions[key]["version"] >= version)
                and not any(
                    m["completion_id"] == key and m["version"] < version for m in moves
                )
            ]
            check_run_compatibility(initial, proposed, states, unrouted)
            if activation["request_id"] is not None:
                _applied(
                    store,
                    activation["request_id"],
                    run_id,
                    "accept_revision",
                    version=version,
                )
            elif run["mode"] != "live":
                raise RouterError("revision_integrity")
            active = proposed
            pending = None
            causal_versions.add(version)
        clear_events = list(
            store.db.execute(
                "SELECT * FROM run_gate_events WHERE run_id=? AND version=? "
                "AND kind='clear'",
                (run_id, version),
            )
        )
        if len(clear_events) > 1:
            raise RouterError("control_integrity")
        for clear_event in clear_events:
            clear_request = mapping(strict_json(str(clear_event["body"])))
            applied = _applied(
                store, clear_request["request_id"], run_id, version=version
            )
            if gate is None or applied["operation"] not in {
                "resume_run",
                "accept_revision",
            }:
                raise RouterError("control_integrity")
            if (
                pending is not None
                or any(value <= version for value in cancellation_versions)
                or any(
                    cast(int, body["version"]) < version
                    and not stopped_at(key, version)
                    for key, body in bodies.items()
                )
            ):
                raise RouterError("control_integrity")
            gate = None
        ordinal = 0
        for event in events:
            if event["version"] != version or version == 1:
                continue
            if event["ordinal"] != ordinal:
                raise RouterError("plane_integrity")
            ordinal += 1
            plane_id = str(event["plane_id"])
            prior = states[plane_id]
            value = fields(strict_json(str(event["body"])), {"state", "cause"})
            state, cause = mapping(value["state"]), mapping(value["cause"])
            fields(state, set(prior))
            if (state["plane_id"], state["workspace"]) != (
                plane_id,
                prior["workspace"],
            ):
                raise RouterError("plane_integrity")
            if (
                state["cursor"] is not None
                and state["cursor"] not in active.graph.planes[plane_id].nodes
            ):
                raise RouterError("plane_integrity")
            expected = copy.deepcopy(prior)
            kind = event["kind"]
            if kind == "reserved":
                invocation_id = str(cause["invocation_id"])
                body = bodies[invocation_id]
                if (
                    body["version"] != version
                    or body["plane_id"] != plane_id
                    or body["node"] != prior["cursor"]
                    or prior["active_invocation"] is not None
                    or prior["status"] != "ready"
                    or gate is not None
                ):
                    raise RouterError("invocation_integrity")
                node = active.graph.planes[plane_id].nodes[str(body["node"])]
                if any(
                    states[dependency.plane_id]["status"] != dependency.status
                    for dependency in node.requires_terminal
                ):
                    raise RouterError("invocation_integrity")
                for seam_id, seam in active.graph.seams.items():
                    if (seam.consumer_plane, seam.consumer_node) == (
                        plane_id,
                        body["node"],
                    ) and seam_id not in mapping(prior["selected"]):
                        raise RouterError("seam_integrity")
                running = [
                    b
                    for b in bodies.values()
                    if cast(int, b["version"]) <= version
                    and (
                        b["completed_version"] is None
                        or cast(int, b["completed_version"]) > version
                    )
                ]
                if (
                    len(running) > active.graph.max_parallel
                    or len([b for b in running if b["plane_id"] == plane_id]) != 1
                ):
                    raise RouterError("invocation_integrity")
                if body["run_deadline"] != min(
                    started + initial.graph.max_elapsed,
                    started + active.graph.max_elapsed,
                ) or cast(int, body["attempt"]) > min(
                    initial.graph.max_invocations, active.graph.max_invocations
                ):
                    raise RouterError("counter_integrity")
                if invocation_id in reserved_seen:
                    raise RouterError("invocation_integrity")
                reserved_seen.add(invocation_id)
                expected.update(active_invocation=invocation_id, status="running")
                last_plane = plane_id
            elif kind == "routed":
                invocation_id = str(cause["invocation_id"])
                if invocation_id in transitions_seen:
                    raise RouterError("transition_integrity")
                transitions_seen.add(invocation_id)
                transition = transitions[invocation_id]
                completion = completions[invocation_id]
                node_id = str(completion["node"])
                if (
                    transition["version"] != version
                    or transition["plane_id"] != plane_id
                    or transition["revision_id"] != active.revision_id
                    or prior["active_invocation"] != invocation_id
                    or gate is not None
                    or prior["status"] == "paused"
                    or not stopped_at(invocation_id, version)
                    or bodies[invocation_id]["parsed_version"] is None
                    or cast(int, bodies[invocation_id]["parsed_version"]) > version
                ):
                    raise RouterError("transition_integrity")
                node = active.graph.planes[plane_id].nodes[node_id]
                count = retry_counts.get((plane_id, node_id), 0)
                action, delta = select_action(
                    node,
                    str(completion["classification"]),
                    completion["outcome"],
                    count,
                )
                target = (
                    node_id
                    if action.kind == "retry"
                    else action.target
                    if action.kind != "finish"
                    else None
                )
                if (
                    transition["action"],
                    transition["target"],
                    transition["retry_delta"],
                    transition["node"],
                ) != (canonical(action.data()), target, delta, node_id):
                    raise RouterError("transition_integrity")
                expected.update(
                    cursor=target,
                    active_invocation=None,
                    pause_reason=None,
                    resume_target=None,
                    status="ready",
                )
                if action.kind == "finish":
                    expected["status"] = action.target
                elif action.kind == "pause":
                    expected.update(
                        status="paused",
                        pause_reason=action.reason,
                        resume_target=action.target,
                    )
                if delta:
                    retry_counts[(plane_id, node_id)] = count + delta
                if completion["classification"] == "semantic":
                    producer = revisions[str(completion["revision_id"])]
                    for seam_id, seam in active.graph.seams.items():
                        if (seam.producer_plane, seam.producer_node, seam.outcome) != (
                            plane_id,
                            node_id,
                            completion["outcome"],
                        ):
                            continue
                        sequence = 1 + sum(
                            key[0] == seam_id for key in occurrence_expected
                        )
                        occurrence_expected[(seam_id, sequence)] = {
                            "format": "millrace-seam-input/v1",
                            "run_id": run_id,
                            "consumer_plane_id": seam.consumer_plane,
                            "consumer_node_id": seam.consumer_node,
                            "seam_id": seam_id,
                            "sequence": sequence,
                            "producer_plane_id": plane_id,
                            "producer_node_id": node_id,
                            "producer_invocation_id": invocation_id,
                            "producing_revision": producer.revision_id,
                            "completion_id": invocation_id,
                            "producer_workspace": producer.workspaces[plane_id]["path"],
                            "result": mapping(strict_json(bytes(completion["stdout"]))),
                        }
                if acknowledges(action, node_id):
                    pins, progress = (
                        mapping(expected["selected"]),
                        mapping(expected["progress"]),
                    )
                    for seam_id, pinned_sequence in list(pins.items()):
                        if active.graph.seams[seam_id].consumer_node != node_id:
                            continue
                        acks_expected[(seam_id, cast(int, pinned_sequence))] = (
                            invocation_id,
                            version,
                        )
                        progress[seam_id] = pinned_sequence
                        del pins[seam_id]
                    expected.update(selected=pins, progress=progress)
            elif kind in {"control", "move"}:
                request = _applied(store, cause["request_id"], run_id, version=version)
                if request["plane_id"] != plane_id:
                    raise RouterError("control_integrity")
                op = request["operation"]
                if kind == "move":
                    move = next(
                        (
                            m
                            for m in moves
                            if m["version"] == version
                            and m["plane_id"] == plane_id
                            and m["request_id"] == cause["request_id"]
                        ),
                        None,
                    )
                    if move is None or prior["status"] != "paused":
                        raise RouterError("transition_integrity")
                    payload = mapping(request["payload"])
                    expected_kind = (
                        "retry"
                        if request["operation"] == "retry_invocation"
                        else "redirect"
                        if request["operation"] == "redirect_plane"
                        else None
                    )
                    if (
                        move["kind"] != expected_kind
                        or move["revision_id"] != active.revision_id
                        or move["reason"] != payload["reason"]
                    ):
                        raise RouterError("control_integrity")
                    if expected_kind == "redirect":
                        if (
                            move["target"] != payload["node_id"]
                            or move["source_invocation"] is not None
                            or mapping(prior["selected"])
                        ):
                            raise RouterError("transition_integrity")
                    else:
                        source_id = str(payload["invocation_id"])
                        source = bodies[source_id]
                        if not stopped_at(source_id, version):
                            raise RouterError("transition_integrity")
                        pinned = mapping(prior["selected"])
                        reference = mapping(source["launch"]).get("seam_input")
                        if pinned:
                            latest = max(
                                (
                                    key
                                    for key, body in bodies.items()
                                    if body["plane_id"] == plane_id
                                    and cast(int, body["version"]) < version
                                ),
                                key=lambda key: cast(int, bodies[key]["attempt"]),
                            )
                            if source_id != latest or reference is None:
                                raise RouterError("seam_integrity")
                            item = mapping(reference)
                            if pinned.get(str(item["seam_id"])) != item["sequence"]:
                                raise RouterError("seam_integrity")
                        if reference is not None:
                            item = mapping(reference)
                            if (
                                str(item["seam_id"]),
                                cast(int, item["sequence"]),
                            ) in acks_expected:
                                raise RouterError("seam_integrity")
                        if move["source_invocation"] != source_id or (
                            source["plane_id"],
                            source["node"],
                        ) != (plane_id, move["target"]):
                            raise RouterError("transition_integrity")
                    unresolved = prior["active_invocation"]
                    if move["completion_id"] != (
                        unresolved
                        if unresolved in completions
                        and unresolved not in transitions_seen
                        else None
                    ):
                        raise RouterError("transition_integrity")
                    moves_seen.add((version, plane_id))
                    expected.update(
                        status="ready",
                        pause_reason=None,
                        cursor=move["target"],
                        resume_target=None,
                        active_invocation=None,
                    )
                    if move["kind"] == "retry":
                        retry_counts[(plane_id, str(move["target"]))] = (
                            retry_counts.get((plane_id, str(move["target"])), 0) + 1
                        )
                elif op == "pause_plane":
                    expected.update(
                        status="paused",
                        pause_reason="operator_pause",
                        resume_target=prior["cursor"],
                    )
                elif op == "cancel_plane":
                    expected.update(
                        status=state["status"], pause_reason="operator_cancel"
                    )
                    if state["status"] == "cancelled":
                        expected["active_invocation"] = None
                    if state["status"] not in {"paused", "cancelled"}:
                        raise RouterError("control_integrity")
                elif op == "resume_plane":
                    if prior["status"] != "paused":
                        raise RouterError("control_integrity")
                    expected.update(
                        cursor=prior["resume_target"]
                        if prior["resume_target"] is not None
                        else prior["cursor"],
                        status="ready",
                        pause_reason=None,
                        resume_target=None,
                    )
                else:
                    raise RouterError("control_integrity")
            elif kind == "selected":
                seam_id, sequence = str(cause["seam_id"]), cast(int, cause["sequence"])
                seam = active.graph.seams[seam_id]
                available = [
                    key[1]
                    for key in occurrence_expected
                    if key[0] == seam_id and key not in acks_expected
                ]
                if (
                    not available
                    or sequence != min(available)
                    or (plane_id, prior["cursor"])
                    != (seam.consumer_plane, seam.consumer_node)
                ):
                    raise RouterError("seam_integrity")
                pins = mapping(expected["selected"])
                pins[seam_id] = sequence
                expected.update(selected=pins, status="ready", pause_reason=None)
                selections_expected[(seam_id, sequence)] = (
                    plane_id,
                    str(prior["cursor"]),
                    version,
                )
            elif kind in {"waiting", "ready"}:
                if (
                    prior["status"] not in {"ready", "waiting"}
                    or prior["active_invocation"] is not None
                ):
                    raise RouterError("plane_integrity")
                if kind == "ready":
                    expected.update(status="ready", pause_reason=None)
                else:
                    if cause["reason"] not in {
                        "dependency_failed",
                        "terminal_dependency",
                        "seam_exhausted",
                        "seam_input",
                    }:
                        raise RouterError("plane_integrity")
                    expected.update(
                        status="paused"
                        if cause["reason"] in {"dependency_failed", "seam_exhausted"}
                        else "waiting",
                        pause_reason=cause["reason"],
                    )
            elif kind == "cancelled":
                if (
                    not cause.get("run_cancel")
                    and prior["pause_reason"] != "operator_cancel"
                ):
                    raise RouterError("control_integrity")
                expected.update(status="cancelled", active_invocation=None)
                if cause.get("run_cancel"):
                    expected["pause_reason"] = "operator_cancel"
            else:
                raise RouterError("plane_integrity")
            if state != expected:
                raise RouterError("plane_integrity")
            states[plane_id] = state
            causal_versions.add(version)
        gate_request = None
        gate_changed = False
        for event in store.db.execute(
            "SELECT * FROM run_gate_events WHERE run_id=? AND version=? "
            "ORDER BY ordinal",
            (run_id, version),
        ):
            gate_changed = True
            value = mapping(strict_json(str(event["body"])))
            if event["kind"] == "set":
                if value["reason"] not in {
                    "operator_pause",
                    "operator_cancel",
                    "writer_unknown",
                    "invalid_revision",
                    "locked_edit",
                    "incompatible_revision",
                    "unstable_revision",
                    "limit_exhausted",
                    "invalid_launch_plan",
                }:
                    raise RouterError("run_integrity")
                if value["request_id"] is not None:
                    _applied(store, value["request_id"], run_id, version=version)
                gate = value
            elif event["kind"] == "clear":
                request = _applied(store, value["request_id"], run_id, version=version)
                if not clear_events or request["operation"] not in {
                    "resume_run",
                    "accept_revision",
                }:
                    raise RouterError("control_integrity")
                gate = None
            else:
                raise RouterError("run_integrity")
            gate_request = value.get("request_id")
            causal_versions.add(version)
        state_rows = list(
            store.db.execute(
                "SELECT * FROM states WHERE run_id=? AND version=?", (run_id, version)
            )
        )
        if gate_changed and not state_rows:
            raise RouterError("control_integrity")
        for state in store.db.execute(
            "SELECT * FROM states WHERE run_id=? AND version=?", (run_id, version)
        ):
            status, reason = str(state["status"]), state["reason"]
            recorded_gate = (
                strict_json(str(state["run_gate"]))
                if state["run_gate"] is not None
                else None
            )
            if recorded_gate != gate:
                raise RouterError("run_integrity")
            if (
                status in {"ready", "running", "succeeded", "failed"}
                and reason is not None
            ):
                raise RouterError("run_integrity")
            if status == "paused":
                expected_reason = (
                    mapping(gate)["reason"]
                    if gate is not None
                    else "no_runnable_planes"
                )
                if reason != expected_reason:
                    raise RouterError("control_integrity")
            derived: tuple[str, object]
            if version == 1:
                derived = ("ready", None)
            elif all(plane["status"] in TERMINAL for plane in states.values()):
                derived = (
                    ("cancelled", "operator_cancel")
                    if any(value <= version for value in cancellation_versions)
                    else (
                        next(
                            value
                            for value in ("failed", "cancelled", "succeeded")
                            if any(
                                plane["status"] == value for plane in states.values()
                            )
                        ),
                        None,
                    )
                )
            elif gate is not None:
                derived = ("paused", mapping(gate)["reason"])
            elif any(
                plane["status"] in {"ready", "running"} for plane in states.values()
            ):
                derived = ("running", None)
            else:
                derived = ("paused", "no_runnable_planes")
            if (status, reason) != derived:
                raise RouterError("run_integrity")
            if gate_changed and state["request_id"] != gate_request:
                raise RouterError("control_integrity")
            if state["request_id"] is not None:
                _applied(store, state["request_id"], run_id, version=version)
            causal_versions.add(version)
        stop_versions[version] = copy.deepcopy(states)
    if (
        states != store.plane_states(run_id)
        or run["run_gate"] != gate
        or run["last_admitted_plane"] != last_plane
    ):
        raise RouterError("plane_integrity")
    if (run["status"], run["pause_reason"], run["revision_id"], run["pending_seq"]) != (
        status,
        reason,
        active.revision_id,
        pending,
    ):
        raise RouterError("run_integrity")
    if (run["cap"], run["max_elapsed"], run["deadline"]) != (
        min(initial.graph.max_invocations, active.graph.max_invocations),
        min(initial.graph.max_elapsed, active.graph.max_elapsed),
        min(started + initial.graph.max_elapsed, started + active.graph.max_elapsed),
    ):
        raise RouterError("counter_integrity")
    for attempt, (invocation_id, item) in enumerate(invocations.items(), 1):
        body = bodies[invocation_id]
        check_id(invocation_id)
        producer = revisions[str(item["revision_id"])]
        plane_id, node_id = str(item["plane_id"]), str(item["node"])
        node = producer.graph.planes[plane_id].nodes[node_id]
        if (
            body["id"],
            body["run_id"],
            body["revision_id"],
            body["plane_id"],
            body["node"],
            body["attempt"],
            item["attempt"],
        ) != (
            invocation_id,
            run_id,
            producer.revision_id,
            plane_id,
            node_id,
            attempt,
            attempt,
        ):
            raise RouterError("invocation_integrity")
        launch = resolve_settings(
            producer,
            plane_id,
            node_id,
            settings_context=host,
            adapter_resolver=store.adapter_resolver,
            project_root=store.config.project_root,
        )
        if canonical(body["launch_settings"]) != canonical(launch):
            raise RouterError("invocation_integrity")
        plan = store.launch(body)
        consumer_seams = [
            name
            for name, seam in producer.graph.seams.items()
            if (seam.consumer_plane, seam.consumer_node) == (plane_id, node_id)
        ]
        seam_reference = mapping(body["launch"]).get("seam_input")
        if bool(consumer_seams) != (seam_reference is not None):
            raise RouterError("seam_integrity")
        if seam_reference is not None:
            reference = mapping(seam_reference)
            selection = store.db.execute(
                "SELECT * FROM seam_selections WHERE run_id=? AND seam_id=? "
                "AND sequence=?",
                (run_id, reference["seam_id"], reference["sequence"]),
            ).fetchone()
            if (
                selection is None
                or selection["version"] > body["version"]
                or reference["seam_id"] not in consumer_seams
                or strict_json(
                    base64.b64decode(str(reference["content"]), validate=True)
                )
                != strict_json(str(selection["body"]))
            ):
                raise RouterError("seam_integrity")
            acknowledged = store.db.execute(
                "SELECT version FROM seam_acknowledgements WHERE run_id=? "
                "AND seam_id=? AND sequence=?",
                (run_id, reference["seam_id"], reference["sequence"]),
            ).fetchone()
            if acknowledged is not None and acknowledged[0] < body["version"]:
                raise RouterError("seam_integrity")
        if (
            body["argv"] != list(plan.argv)
            or body["cwd"]
            != str(Path(str(producer.workspaces[plane_id]["path"])) / node.cwd)
            or body["env_names"] != host["env_allowlist"]
            or body["result_contract"] != "json-v1"
        ):
            raise RouterError("invocation_integrity")
        created = number(body["created_at"])
        deadline = number(body["run_deadline"])
        if (
            body["deadline"]
            != min(
                deadline,
                created
                + cast(
                    float,
                    mapping(mapping(launch["common"])["effective"])["timeout_seconds"],
                ),
            )
            or not created < deadline
        ):
            raise RouterError("counter_integrity")
        if item["status"] not in {"reserved", "active", "completed", "uncertain"} or (
            item["status"] == "reserved" and body["pid"] is not None
        ):
            raise RouterError("invocation_integrity")
        if body["pid"] is not None:
            number(body["pid"], integer=True)
        if item["status"] == "active" and body["pid"] is None:
            raise RouterError("invocation_integrity")
        if (item["status"] == "completed") != (invocation_id in completions):
            raise RouterError("completion_integrity")
        causal_versions.add(cast(int, body["version"]))
        for key in ("started_version", "completed_version", "parsed_version"):
            if body[key] is not None:
                causal_versions.add(cast(int, body[key]))
        if invocation_id in completions:
            _completion(
                store, completions[invocation_id], body, producer, run_id, node_id
            )
    if (
        reserved_seen != set(invocations)
        or transitions_seen != set(transitions)
        or moves_seen
        != {(int(item["version"]), str(item["plane_id"])) for item in moves}
    ):
        raise RouterError("transition_integrity")
    actual_retries = {
        (str(item["plane_id"]), str(item["node"])): item["count"]
        for item in store.db.execute("SELECT * FROM retries WHERE run_id=?", (run_id,))
    }
    if actual_retries != retry_counts:
        raise RouterError("counter_integrity")
    occurrence_rows = list(
        store.db.execute("SELECT * FROM seam_occurrences WHERE run_id=?", (run_id,))
    )
    if len(occurrence_rows) != len(occurrence_expected):
        raise RouterError("seam_integrity")
    for item in occurrence_rows:
        occurrence_key = (str(item["seam_id"]), int(item["sequence"]))
        expected_envelope = occurrence_expected[occurrence_key]
        if (
            _envelope(strict_json(str(item["body"]))) != expected_envelope
            or (
                item["producer_invocation_id"],
                item["completion_id"],
                item["transition_id"],
            )
            != (expected_envelope["producer_invocation_id"],) * 3
            or item["version"] != transitions[str(item["transition_id"])]["version"]
        ):
            raise RouterError("seam_integrity")
    selection_rows = list(
        store.db.execute("SELECT * FROM seam_selections WHERE run_id=?", (run_id,))
    )
    if len(selection_rows) != len(selections_expected):
        raise RouterError("seam_integrity")
    for item in selection_rows:
        occurrence_key = (str(item["seam_id"]), int(item["sequence"]))
        if (
            item["consumer_plane_id"],
            item["consumer_node_id"],
            item["version"],
        ) != selections_expected[occurrence_key] or strict_json(
            str(item["body"])
        ) != occurrence_expected[occurrence_key]:
            raise RouterError("seam_integrity")
    ack_rows = list(
        store.db.execute(
            "SELECT * FROM seam_acknowledgements WHERE run_id=?", (run_id,)
        )
    )
    if len(ack_rows) != len(acks_expected):
        raise RouterError("seam_integrity")
    for item in ack_rows:
        if (item["completion_id"], item["version"]) != acks_expected[
            (str(item["seam_id"]), int(item["sequence"]))
        ]:
            raise RouterError("seam_integrity")
    for item in store.db.execute(
        "SELECT f.* FROM writer_facts f JOIN invocations i ON i.id=f.invocation_id "
        "WHERE i.run_id=?",
        (run_id,),
    ):
        request = _applied(
            store,
            item["request_id"],
            run_id,
            "reconcile_invocation",
            version=item["version"],
        )
        if (
            request["plane_id"] != bodies[str(item["invocation_id"])]["plane_id"]
            or mapping(request["payload"])["invocation_id"] != item["invocation_id"]
            or item["writer"] != "stopped"
        ):
            raise RouterError("control_integrity")
        causal_versions.add(int(item["version"]))
    cancel_effects = [
        mapping(strict_json(str(item[0])))
        for item in store.db.execute(
            "SELECT receipt FROM controls WHERE run_id=? AND status='applied' "
            "AND operation='cancel_run'",
            (run_id,),
        )
    ]
    applied_cancels = [
        receipt
        for receipt in cancel_effects
        if receipt.get("action") in {"cancel_requested", "cancelled"}
    ]
    if len(applied_cancels) > 1:
        raise RouterError("control_integrity")
    causal_versions.update(
        cast(int, receipt["state_version"]) for receipt in applied_cancels
    )
    cancelled = False
    for item in store.db.execute(
        "SELECT * FROM controls WHERE run_id=? AND status='applied' ORDER BY seq",
        (run_id,),
    ):
        request = mapping(strict_json(str(item["body"])))
        receipt = mapping(strict_json(str(item["receipt"])))
        version = cast(int, receipt["state_version"])
        if (
            request["operation"] == "cancel_run"
            and receipt.get("action") != "already_terminal"
        ):
            cancelled = True
        if request["operation"] in {
            "pause_run",
            "cancel_run",
            "pause_plane",
            "cancel_plane",
        }:
            targets = (
                [str(request["plane_id"])]
                if request["plane_id"] is not None
                else sorted(initial.graph.planes)
            )
            active_ids = sorted(
                key
                for key, body in bodies.items()
                if body["plane_id"] in targets
                and cast(int, body["version"]) <= version
                and (
                    body["completed_version"] is None
                    or cast(int, body["completed_version"]) > version
                )
            )
            if (
                receipt.get("target_planes") != targets
                or receipt.get("active_invocations") != active_ids
            ):
                raise RouterError("control_integrity")
    if set(versions) != causal_versions:
        raise RouterError("counter_integrity")
    if run["cancel_requested"] != cancelled or (
        run["status"] in TERMINAL and not store.quiescent(run_id)
    ):
        raise RouterError("run_integrity")


def _completion(
    store: Store,
    completion: sqlite3.Row,
    body: dict[str, object],
    producer: Revision,
    run_id: str,
    node_id: str,
) -> None:
    if (
        (
            completion["run_id"],
            completion["revision_id"],
            completion["plane_id"],
            completion["node"],
        )
        != (run_id, producer.revision_id, body["plane_id"], node_id)
        or completion["writer"] not in {"stopped", "may_live", "unknown"}
        or completion["transport"]
        not in {
            "completed",
            "execution_failure",
            "protocol_failure",
            "timeout",
            "cancelled",
            "interrupted",
        }
        or len(completion["stdout"]) > 65536
        or len(completion["stderr"]) > 65536
    ):
        raise RouterError("completion_integrity")
    if completion["transport"] == "completed" and (
        type(completion["exit_code"]) is not int or completion["exit_code"] != 0
    ):
        raise RouterError("completion_integrity")
    facts = CompletionFacts(
        str(body["id"]),
        cast(int | None, body["pid"]),
        completion["stdout"],
        completion["stderr"],
        str(completion["transport"]),
        str(completion["writer"]),
        completion["diagnostic"],
        completion["exit_code"],
        cast(dict[str, object] | None, body["adapter_evidence"]),
        str(body.get("origin")),
    )
    _result(facts)
    plan = store.launch(body)
    if facts.origin == "adapter":
        _adapter(
            plan.adapter_id, plan.adapter_contract, store.adapter_resolver
        ).validate_completion(plan, facts)
    elif facts.origin == "core_interruption":
        fact = store.db.execute(
            "SELECT request_id,version FROM writer_facts WHERE invocation_id=?",
            (body["id"],),
        ).fetchone()
        if fact is None:
            raise RouterError("completion_integrity")
        request = _applied(
            store,
            fact["request_id"],
            run_id,
            "reconcile_invocation",
            version=fact["version"],
        )
        if mapping(request["payload"])["invocation_id"] != body["id"]:
            raise RouterError("completion_integrity")
    node = producer.graph.planes[str(body["plane_id"])].nodes[node_id]
    if completion["classification"] is None:
        if completion["outcome"] is not None or body["ref_admission"] is not None:
            raise RouterError("completion_integrity")
        if completion["transport"] == "completed":
            parse_result(
                cast(bytes, completion["stdout"]),
                node,
                Path(str(producer.workspaces[str(body["plane_id"])]["path"])),
                resolve_refs=False,
            )
    else:
        expected = recorded_result(
            cast(bytes, completion["stdout"]),
            node,
            Path(str(producer.workspaces[str(body["plane_id"])]["path"])),
            str(completion["transport"]),
            body["ref_admission"],
        )
        if (completion["classification"], completion["outcome"]) != expected:
            raise RouterError("completion_integrity")


def _instance_authority(store: Store, meta: dict[str, str]) -> None:
    """Current settings and each startup derive from one ordered receipt history."""
    creation = mapping(strict_json(meta["creation"]))
    request = validate_envelope(creation["request"], creation=True)
    receipt = mapping(creation["receipt"])
    payload = mapping(request["payload"])
    if (
        request["operation"],
        request["expected_state_version"],
        payload["project_root"],
        payload["workflow_file"],
    ) != (
        "create_instance",
        0,
        str(store.config.project_root),
        str(store.config.workflow_file),
    ):
        raise RouterError("instance_integrity")
    if (
        receipt["request_id"],
        receipt["request_digest"],
        receipt["instance_id"],
        receipt["status"],
        receipt["state_version"],
    ) != (
        request["request_id"],
        digest(request),
        store.config.instance_id,
        "applied",
        1,
    ):
        raise RouterError("instance_integrity")
    settings: dict[str, object] = {
        "path": payload["path"],
        "env_allowlist": payload["env_allowlist"],
        "host_paths": payload["host_paths"],
        "runtime_defaults": payload.get("runtime_defaults", {}),
        "operator_policy": payload.get("operator_policy", {}),
        "state_version": 1,
    }
    _settings(settings)
    seen_runs: set[str] = set()
    for row in store.db.execute(
        "SELECT * FROM controls WHERE status='applied' AND operation IN "
        "('start_run','update_instance_config') ORDER BY seq"
    ):
        request = mapping(strict_json(str(row["body"])))
        receipt = mapping(strict_json(str(row["receipt"])))
        if request["expected_state_version"] != settings["state_version"]:
            raise RouterError("control_integrity")
        if row["operation"] == "start_run":
            run = store.run(str(receipt["run_id"]))
            _settings(run["host_settings"])
            if (
                run["host_settings"] != settings
                or run.get("start_request_id") != request["request_id"]
                or run["mode"] != mapping(request["payload"])["edit_mode"]
                or receipt["state_version"] != 1
                or receipt["instance_state_version"]
                != cast(int, settings["state_version"]) + 1
            ):
                raise RouterError("run_integrity")
            seen_runs.add(str(run["id"]))
        else:
            settings.update(mapping(mapping(request["payload"])["settings"]))
            if receipt["state_version"] != cast(int, settings["state_version"]) + 1:
                raise RouterError("instance_integrity")
        settings["state_version"] = cast(int, settings["state_version"]) + 1
        _settings(settings)
    if strict_json(meta["settings"]) != settings or seen_runs != {
        str(row[0]) for row in store.db.execute("SELECT id FROM runs")
    }:
        raise RouterError("instance_integrity")
