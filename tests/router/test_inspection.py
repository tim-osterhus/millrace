"""Bounded public inspection over genuine command-only instances (UI1)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from tests.router.support import command_graph_json, create_instance, start_run

from millrace.router import control, inspection
from millrace.router.records import RouterError
from millrace.router.store import new_id

RESULT_KEYS = {
    "format",
    "kind",
    "instance_id",
    "run_id",
    "state_version",
    "items",
    "next_cursor",
    "data",
}
INVENTORY_KEYS = {
    "instance_id",
    "settings_version",
    "project_root",
    "workflow_file",
    "store_format",
    "error_code",
}
INSTANCE_DATA_KEYS = {
    "instance_id",
    "project_root",
    "workflow_file",
    "settings_version",
    "runtime_defaults",
    "operator_policy",
}
RUN_ITEM_KEYS = {
    "run_id",
    "status",
    "mode",
    "state_version",
    "active_revision_id",
    "pause_reason",
}
SUMMARY_KEYS = {
    "run_id",
    "status",
    "mode",
    "state_version",
    "run_gate",
    "pause_reason",
    "cancel_requested",
    "invocation_count",
    "limits",
    "startup_revision_id",
    "active_revision_id",
    "pending_candidate",
    "quiescent",
    "inflight_invocations",
    "writer_counts",
    "planes",
    "seams",
}
LIMIT_KEYS = {
    "max_invocations",
    "max_elapsed_seconds",
    "startup_max_invocations",
    "startup_max_elapsed_seconds",
    "started_at",
    "deadline",
    "startup_deadline",
}
PLANE_KEYS = {
    "status",
    "cursor",
    "active_invocation",
    "pause_reason",
    "resume_target",
    "dependencies",
    "selected",
    "progress",
    "pending_completion_ids",
    "last_event",
}
SEAM_KEYS = {
    "latest_occurrence_sequence",
    "latest_selection_sequence",
    "latest_acknowledgement_sequence",
}
CANDIDATE_KEYS = {
    "seq",
    "candidate_id",
    "revision_id",
    "diagnostic",
    "changed_paths",
    "withdrawn",
    "supersedes",
    "version",
    "request_id",
}
INVOCATION_KEYS = {
    "invocation_id",
    "attempt",
    "plane_id",
    "node_id",
    "status",
    "producing_revision_id",
    "created_at",
    "deadline",
    "run_deadline",
    "started_version",
    "completed_version",
    "parsed_version",
    "result_contract",
    "uncertainty_diagnostic",
    "writer",
    "completion",
    "reconciliation",
    "settings",
    "unavailable_reason",
}
COMPLETION_KEYS = {"transport", "exit_code", "classification", "outcome", "diagnostic"}
TRANSITION_KEYS = {
    "event_kind",
    "event_id",
    "completion_id",
    "plane_id",
    "node_id",
    "producing_revision_id",
    "routing_revision_id",
    "outcome",
    "action",
    "target",
    "retry_delta",
    "version",
}
MOVE_KEYS = {
    "event_kind",
    "event_id",
    "move_kind",
    "completion_id",
    "source_invocation_id",
    "plane_id",
    "target",
    "routing_revision_id",
    "request_id",
    "version",
}
GRAPH_KEYS = {
    "revision_id",
    "graph_format",
    "limits",
    "bindings",
    "defaults",
    "planes",
    "seams",
}
GRAPH_LIMIT_KEYS = {
    "max_invocations",
    "max_elapsed_seconds",
    "max_parallel_invocations",
}
GRAPH_NODE_KEYS = {
    "binding",
    "timeout_seconds",
    "outcomes",
    "routes",
    "on_error",
    "on_unknown",
    "requires_terminal",
}
ACTION_KEYS = {"kind", "target", "reason", "limit", "exhausted"}
VALIDATION_KEYS = {
    "valid",
    "revision_id",
    "candidate",
    "diagnostics",
    "diagnostic_details",
    "warnings",
    "validation_context",
    "resolved_nodes",
}
VALIDATION_CANDIDATE_KEYS = {
    "candidate_id",
    "revision_id",
    "diagnostic",
    "changed_paths",
}
FORBIDDEN = (
    '"argv"',
    '"cwd"',
    '"pid"',
    '"env_names"',
    '"env_allowlist"',
    '"host_paths"',
    '"host_settings"',
    '"hold_id"',
    '"launch"',
    '"adapter"',
    '"adapter_settings"',
    '"adapter_evidence"',
    '"stdout"',
    '"stderr"',
    '"stdout_base64"',
    '"files"',
    '"executable"',
    '"executables"',
    '"workspace"',
)


def _instance(workflow: Path, project: Path, home: Path) -> str:
    return str(create_instance(workflow, project, state_home=home)["instance_id"])


def _assert_safe(value: object) -> None:
    text = json.dumps(value)
    for token in FORBIDDEN:
        assert token not in text, token


def _store_totals(path: Path) -> tuple[int, int, int]:
    """Independently count stored rows, UTF-8 octets and characters."""
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        octets = rows = characters = 0
        tables = [
            str(row[0])
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not str(row[0]).startswith("sqlite_")
        ]
        for table in tables:
            for row in db.execute(f'SELECT * FROM "{table}"'):
                rows += 1
                for value in row:
                    if isinstance(value, str):
                        octets += len(value.encode("utf-8"))
                        characters += len(value)
                    elif isinstance(value, bytes):
                        octets += len(value)
                        characters += len(value)
        return octets, rows, characters
    finally:
        db.close()


def _paused_flow(workflow: Path) -> dict[str, Any]:
    graph: dict[str, Any] = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    workflow.write_text(command_graph_json(graph))
    return graph


def _retried(
    flow: tuple[Path, Path, Path], reason: str = "new contract"
) -> tuple[str, str, Path, dict[str, Any]]:
    """Genuine live-mode retry: two invocations under two producing revisions."""
    workflow, project, home = flow
    graph = _paused_flow(workflow)
    instance = _instance(workflow, project, home)
    before = start_run(instance, state_home=home, edit_mode="live")
    graph["planes"]["main"]["nodes"]["draft"]["outcomes"] = ["OTHER"]
    graph["planes"]["main"]["nodes"]["draft"]["on"] = {
        "OTHER": {"finish": "succeeded"}
    }
    (workflow.parent / "scripts/draft.py").write_text(
        'print(\'{"outcome":"OTHER"}\')\n'
    )
    workflow.write_text(command_graph_json(graph))
    run = str(before["id"])
    control.retry_invocation(
        instance,
        run,
        invocation_id=before["invocations"][0]["id"],
        reason=reason,
        state_home=home,
        request_id=new_id(),
        expected_state_version=before["state_version"],
        plane_id="main",
    )
    final = control.inspect_run(instance, run, state_home=home)
    assert final["status"] == "succeeded" and final["count"] == 2
    return instance, run, home, final


def _refuse(instance: str, run: str, home: Path, version: int) -> str:
    """A refused stale request appends a receipt without changing the run."""
    receipt = control.resume_plane(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=version - 1,
        plane_id="main",
    )
    assert receipt["status"] == "refused"
    return str(receipt["request_id"])


def test_inventory_instance_summary_projections_use_frozen_keys(
    flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home = flow
    instance = _instance(workflow, project, home)
    legacy = start_run(instance, state_home=home)
    run = str(legacy["id"])

    page = inspection.list_instances_page(state_home=home)
    assert set(page) == RESULT_KEYS and page["kind"] == "instances"
    assert page["format"] == inspection.INSPECTION_CONTRACT
    assert page["next_cursor"] is None and page["state_version"] is None
    (item,) = page["items"]
    assert set(item) == INVENTORY_KEYS and item["instance_id"] == instance
    assert item["error_code"] is None
    assert item["store_format"] == "millrace-router-store/v8"

    view = inspection.inspect_instance_page(instance, state_home=home)
    assert view["kind"] == "instance" and set(view["data"]) == INSTANCE_DATA_KEYS
    assert view["state_version"] == control.inspect_instance(
        instance, state_home=home
    )["state_version"]
    (run_item,) = view["items"]
    assert set(run_item) == RUN_ITEM_KEYS and run_item["run_id"] == run

    summary = inspection.inspect_run_page(instance, run, state_home=home)
    data = summary["data"]
    assert summary["kind"] == "run-summary" and summary["items"] == []
    assert set(data) == SUMMARY_KEYS and set(data["limits"]) == LIMIT_KEYS
    assert set(data["writer_counts"]) == {"recorded", "effective"}
    assert data["writer_counts"]["effective"] == {"stopped": 2}
    for plane in data["planes"].values():
        assert set(plane) == PLANE_KEYS
        event = plane["last_event"]
        assert event is None or set(event) == TRANSITION_KEYS
    for seam in data["seams"].values():
        assert set(seam) == SEAM_KEYS
    assert (
        data["status"],
        data["state_version"],
        data["quiescent"],
        data["invocation_count"],
        data["active_revision_id"],
        data["pending_candidate"],
    ) == (
        legacy["status"],
        legacy["state_version"],
        legacy["quiescent"],
        legacy["count"],
        legacy["revision_id"],
        None,
    )
    assert data["planes"]["main"]["last_event"]["event_kind"] == "transition"
    for value in (page, view, summary):
        _assert_safe(value)


def test_invocation_pages_exact_selector_and_producing_revision(
    single_flow: tuple[Path, Path, Path],
) -> None:
    instance, run, home, final = _retried(single_flow)
    first = inspection.inspect_run_page(
        instance, run, state_home=home, section="invocations", limit=1
    )
    (newest,) = first["items"]
    assert set(newest) == INVOCATION_KEYS and newest["attempt"] == 2
    second = inspection.inspect_run_page(
        instance,
        run,
        state_home=home,
        section="invocations",
        limit=1,
        cursor=first["next_cursor"],
    )
    (oldest,) = second["items"]
    assert oldest["attempt"] == 1 and second["next_cursor"] is None
    legacy = {item["id"]: item for item in final["invocations"]}
    for item in (newest, oldest):
        assert item["producing_revision_id"] == legacy[item["invocation_id"]][
            "revision_id"
        ]
        assert set(item["writer"]) == {"original", "effective"}
        assert set(item["completion"]) == COMPLETION_KEYS
        assert item["unavailable_reason"] == {}
        _assert_safe(item)
    # The earlier invocation keeps its producing revision after live activation.
    assert oldest["producing_revision_id"] != newest["producing_revision_id"]
    summary = inspection.inspect_run_page(instance, run, state_home=home)["data"]
    assert summary["active_revision_id"] != oldest["producing_revision_id"]

    exact = inspection.inspect_run_page(
        instance,
        run,
        state_home=home,
        section="invocations",
        invocation_id=oldest["invocation_id"],
    )
    assert exact["items"] == [oldest] and exact["next_cursor"] is None
    absent = inspection.inspect_run_page(
        instance, run, state_home=home, section="invocations", invocation_id=new_id()
    )
    assert absent["items"] == []
    other = start_run(instance, state_home=home)
    foreign = other["invocations"][0]["id"]
    assert (
        inspection.inspect_run_page(
            instance,
            run,
            state_home=home,
            section="invocations",
            invocation_id=foreign,
        )["items"]
        == []
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {"section": "invocations", "invocation_id": "a" * 32, "cursor": "c" + "0" * 33},
        {"section": "summary", "invocation_id": "a" * 32},
        {"section": "controls", "request_id": "a" * 32, "cursor": "c" + "0" * 33},
        {"section": "transitions", "request_id": "a" * 32},
        {"section": "summary", "cursor": "c" + "0" * 33},
        {"section": "history"},
        {"limit": 0},
        {"limit": 51},
        {"limit": True},
        {"cursor": "not a cursor"},
    ],
)
def test_selector_and_argument_refusals(
    single_flow: tuple[Path, Path, Path], arguments: dict[str, Any]
) -> None:
    workflow, project, home = single_flow
    instance = _instance(workflow, project, home)
    run = str(start_run(instance, state_home=home)["id"])
    with pytest.raises(RouterError) as error:
        inspection.inspect_run_page(instance, run, state_home=home, **arguments)
    assert error.value.code == "invalid_request"
    for missing_run in ({}, {"section": "invocations", "invocation_id": "a" * 32}):
        with pytest.raises(RouterError) as error:
            inspection.inspect_run_page(instance, None, state_home=home, **missing_run)
        assert error.value.code == "invalid_request"


def test_controls_cursor_keeps_stable_key_across_same_version_append(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home = single_flow
    instance = _instance(workflow, project, home)
    legacy = start_run(instance, state_home=home)
    run, version = str(legacy["id"]), int(legacy["state_version"])
    made = [_refuse(instance, run, home, version) for _ in range(3)]
    first = inspection.inspect_run_page(
        instance, run, state_home=home, section="controls", limit=2
    )
    assert [item["request_id"] for item in first["items"]] == made[::-1][:2]
    later = _refuse(instance, run, home, version)
    assert control.inspect_run(instance, run, state_home=home)["state_version"] == (
        version
    )
    second = inspection.inspect_run_page(
        instance,
        run,
        state_home=home,
        section="controls",
        limit=2,
        cursor=first["next_cursor"],
    )
    assert [item["request_id"] for item in second["items"]] == [made[0]]
    assert second["next_cursor"] is None
    fresh = inspection.inspect_run_page(
        instance, run, state_home=home, section="controls"
    )
    assert [item["request_id"] for item in fresh["items"]] == [later, *made[::-1]]


def test_instance_run_cursor_and_settings_version_binding(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home = single_flow
    instance = _instance(workflow, project, home)
    first_run = str(start_run(instance, state_home=home)["id"])
    second_run = str(start_run(instance, state_home=home)["id"])
    page = inspection.inspect_instance_page(instance, state_home=home, limit=1)
    assert [item["run_id"] for item in page["items"]] == [second_run]
    rest = inspection.inspect_instance_page(
        instance, state_home=home, limit=1, cursor=page["next_cursor"]
    )
    assert [item["run_id"] for item in rest["items"]] == [first_run]
    assert rest["next_cursor"] is None
    before = control.inspect_instance(instance, state_home=home)["state_version"]
    start_run(instance, state_home=home)
    # Store.create_run advances the settings version, so the old page refuses.
    assert control.inspect_instance(instance, state_home=home)["state_version"] != (
        before
    )
    with pytest.raises(RouterError, match="inspection_changed"):
        inspection.inspect_instance_page(
            instance, state_home=home, limit=1, cursor=page["next_cursor"]
        )


def test_event_pages_cover_transitions_and_moves_exactly_once(
    single_flow: tuple[Path, Path, Path],
) -> None:
    instance, run, home, final = _retried(single_flow)
    seen: list[dict[str, Any]] = []
    cursor = None
    for _ in range(10):
        page = inspection.inspect_run_page(
            instance,
            run,
            state_home=home,
            section="transitions",
            limit=1,
            cursor=cursor,
        )
        seen += page["items"]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert cursor is None
    expected = {("transition", row["completion_id"]) for row in final["transitions"]}
    expected |= {("move", row["request_id"]) for row in final["operator_moves"]}
    assert len(seen) == len(expected)
    assert {(item["event_kind"], item["event_id"]) for item in seen} == expected
    versions = [item["version"] for item in seen]
    assert versions == sorted(versions, reverse=True)
    for item in seen:
        keys = TRANSITION_KEYS if item["event_kind"] == "transition" else MOVE_KEYS
        assert set(item) == keys
    moves = [item for item in seen if item["event_kind"] == "move"]
    assert moves and [item["move_kind"] for item in moves] == [
        row["kind"] for row in final["operator_moves"]
    ]


def test_cursor_binding_refusals(single_flow: tuple[Path, Path, Path]) -> None:
    workflow, project, home = single_flow
    _paused_flow(workflow)
    instance = _instance(workflow, project, home)
    paused = start_run(instance, state_home=home)
    run, version = str(paused["id"]), int(paused["state_version"])
    assert paused["status"] == "paused"
    for _ in range(2):
        _refuse(instance, run, home, version)
    page = inspection.inspect_run_page(
        instance, run, state_home=home, section="controls", limit=1
    )
    cursor = page["next_cursor"]
    assert isinstance(cursor, str)
    head = cursor.rpartition("_")[0]
    for arguments, code in (
        ({"section": "invocations", "cursor": cursor}, "inspection_changed"),
        ({"section": "controls", "cursor": head + "_999999"}, "inspection_changed"),
        ({"section": "controls", "cursor": "z" + cursor[1:]}, "invalid_request"),
    ):
        with pytest.raises(RouterError) as error:
            inspection.inspect_run_page(
                instance, run, state_home=home, limit=1, **arguments
            )
        assert error.value.code == code
    resumed = control.resume_plane(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=version,
        plane_id="main",
    )
    assert resumed["status"] == "applied"
    assert control.inspect_run(instance, run, state_home=home)["state_version"] != (
        version
    )
    with pytest.raises(RouterError, match="inspection_changed"):
        inspection.inspect_run_page(
            instance, run, state_home=home, section="controls", limit=1, cursor=cursor
        )


def test_exact_receipt_lookup_start_association_and_mismatch(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home = single_flow
    instance = _instance(workflow, project, home)
    version = control.inspect_instance(instance, state_home=home)["state_version"]
    start_id = new_id()
    receipt = control.start_run(
        instance, state_home=home, request_id=start_id, expected_state_version=version
    )
    run = str(receipt["run_id"])
    listed = control.inspect_instance(instance, state_home=home)
    stored = next(item for item in listed["controls"] if item["request_id"] == start_id)
    by_instance = inspection.inspect_run_page(
        instance, None, state_home=home, section="controls", request_id=start_id
    )
    assert by_instance["items"] == [stored] and by_instance["run_id"] is None
    assert by_instance["state_version"] == listed["state_version"]
    by_run = inspection.inspect_run_page(
        instance, run, state_home=home, section="controls", request_id=start_id
    )
    assert by_run["items"] == [stored] and by_run["next_cursor"] is None
    other = str(start_run(instance, state_home=home)["id"])
    with pytest.raises(RouterError, match="request_target_mismatch"):
        inspection.inspect_run_page(
            instance, other, state_home=home, section="controls", request_id=start_id
        )
    absent = inspection.inspect_run_page(
        instance, None, state_home=home, section="controls", request_id=new_id()
    )
    assert absent["items"] == []


def test_revision_graph_membership_and_allowlist(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    instance, run, home, final = _retried(single_flow)
    revisions = {item["revision_id"] for item in final["invocations"]}
    assert len(revisions) == 2
    for revision_id in revisions:
        graph = inspection.inspect_revision_graph(
            instance, run, revision_id, state_home=home
        )
        data = graph["data"]
        assert graph["kind"] == "graph" and graph["state_version"] == final[
            "state_version"
        ]
        assert set(data) == GRAPH_KEYS and data["revision_id"] == revision_id
        assert set(data["limits"]) == GRAPH_LIMIT_KEYS
        assert data["bindings"] == ["python"]
        plane = data["planes"]["main"]
        assert set(plane) == {"start", "defaults", "nodes"}
        node = plane["nodes"]["draft"]
        assert set(node) == GRAPH_NODE_KEYS
        for action in (node["on_error"], node["on_unknown"], *node["routes"].values()):
            assert set(action) == ACTION_KEYS
        text = json.dumps(data)
        assert "scripts/draft.py" not in text and sys.executable not in text
        _assert_safe(graph)
    for revision_id, code in (
        ("f" * 64, "unknown_revision"),
        ("bad", "invalid_request"),
    ):
        with pytest.raises(RouterError) as error:
            inspection.inspect_revision_graph(
                instance, run, revision_id, state_home=home
            )
        assert error.value.code == code
    monkeypatch.setattr(inspection, "MAX_NODES", 0)
    with pytest.raises(RouterError, match="inspection_too_large"):
        inspection.inspect_revision_graph(
            instance, run, sorted(revisions)[0], state_home=home
        )


def test_validation_summary_projection_and_current_input_limit(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, project, home = single_flow
    instance = _instance(workflow, project, home)
    run = str(start_run(instance, state_home=home)["id"])
    summary = inspection.validate_revision_summary(instance, state_home=home)
    data = summary["data"]
    assert summary["kind"] == "validation" and set(data) == VALIDATION_KEYS
    assert data["valid"] is True and data["candidate"]["changed_paths"] is None
    assert set(data["candidate"]) == VALIDATION_CANDIDATE_KEYS
    for nodes in data["resolved_nodes"].values():
        for settings in nodes.values():
            assert set(settings) == {"format", "common"}
    full = control.validate_revision(instance, state_home=home)
    assert (data["valid"], data["revision_id"], data["validation_context"]) == (
        full["valid"],
        full["revision_id"],
        full["validation_context"],
    )
    assert summary["state_version"] == full["validation_context"]["settings_version"]
    by_run = inspection.validate_revision_summary(instance, state_home=home, run_id=run)
    assert by_run["data"]["candidate"]["changed_paths"] == []
    assert by_run["state_version"] == by_run["data"]["validation_context"][
        "run_state_version"
    ]
    _assert_safe(summary)

    # A current control file larger than every stored revision exceeds only the
    # current-input read limit; ordinary validation is unaffected.
    pad = workflow.parent / "scripts/pad.txt"
    pad.write_text("x" * 65536)
    graph = json.loads(workflow.read_text())
    graph["control_files"].append("scripts/pad.txt")
    workflow.write_text(command_graph_json(graph))
    store = home / "instances" / instance / "state.sqlite3"
    db = sqlite3.connect(store.as_uri() + "?mode=ro", uri=True)
    try:
        largest = db.execute(
            "SELECT MAX(length(CAST(body AS BLOB))) FROM revisions"
        ).fetchone()[0]
    finally:
        db.close()
    assert largest + 1 < 65536
    monkeypatch.setattr(inspection, "INPUT_BYTES", largest + 1)
    with pytest.raises(RouterError, match="inspection_too_large"):
        inspection.validate_revision_summary(instance, state_home=home)
    assert control.validate_revision(instance, state_home=home)["valid"] is True


def test_preflight_counts_utf8_octets_and_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = sqlite3.connect(tmp_path / "unit.sqlite3")
    try:
        db.execute("CREATE TABLE items(body TEXT)")
        text = "é€" * 10
        db.executemany("INSERT INTO items VALUES(?)", [(text,), (None,)])
        db.commit()
        octets = len(text.encode("utf-8"))
        assert len(text) < octets - 1
        monkeypatch.setattr(inspection, "INSTANCE_BYTES", octets)
        monkeypatch.setattr(inspection, "INSTANCE_ROWS", 2)
        work = inspection._Preflight()
        work(db)
        assert (work.rows, work.octets) == (2, octets)
        for name, value in (("INSTANCE_BYTES", octets - 1), ("INSTANCE_ROWS", 1)):
            with monkeypatch.context() as patch:
                patch.setattr(inspection, name, value)
                with pytest.raises(RouterError, match="inspection_too_large"):
                    inspection._Preflight()(db)
    finally:
        db.close()


def test_store_budget_boundary_counts_non_ascii_octets(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    instance, run, home, _ = _retried(single_flow, reason="révision complète €")
    directory = home / "instances" / instance
    octets, rows, characters = _store_totals(directory / "state.sqlite3")
    assert octets > characters  # The free-text move reason is stored raw UTF-8.
    size = (directory / "instance.json").stat().st_size
    monkeypatch.setattr(inspection, "INSTANCE_BYTES", size + octets)
    monkeypatch.setattr(inspection, "INSTANCE_ROWS", rows)
    assert inspection.inspect_run_page(instance, run, state_home=home)["run_id"] == run
    for name, value in (
        ("INSTANCE_BYTES", size + octets - 1),
        ("INSTANCE_ROWS", rows - 1),
    ):
        with monkeypatch.context() as patch:
            patch.setattr(inspection, name, value)
            with pytest.raises(RouterError, match="inspection_too_large"):
                inspection.inspect_run_page(instance, run, state_home=home)


def test_budget_refuses_before_instance_metadata_and_revision_decode(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, project, home = single_flow
    instance = _instance(workflow, project, home)
    size = (home / "instances" / instance / "instance.json").stat().st_size
    decoded: list[object] = []
    real = control.strict_json

    def counted(raw: object) -> object:
        decoded.append(raw)
        return real(raw)

    monkeypatch.setattr(control, "strict_json", counted)
    monkeypatch.setattr(inspection, "INSTANCE_BYTES", size - 1)
    with pytest.raises(RouterError, match="inspection_too_large"):
        inspection.inspect_instance_page(instance, state_home=home)
    assert decoded == []  # instance.json was sized, never read or parsed
    monkeypatch.setattr(inspection, "INSTANCE_BYTES", size + 1)
    with pytest.raises(RouterError, match="inspection_too_large"):
        inspection.inspect_instance_page(instance, state_home=home)
    assert len(decoded) == 1  # instance.json only; metadata never decoded

    revisions: list[object] = []
    monkeypatch.setattr(
        control, "decode_revision", lambda *args: revisions.append(args)
    )
    monkeypatch.setattr(inspection, "INSTANCE_BYTES", 32 * 1_048_576)
    monkeypatch.setattr(inspection, "INPUT_BYTES", 1)
    with pytest.raises(RouterError, match="inspection_too_large"):
        inspection.inspect_instance_page(instance, state_home=home)
    assert revisions == []


def test_bounded_snapshots_are_explicit_and_public_loader_is_unchanged(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, project, home = single_flow
    instance = _instance(workflow, project, home)
    states: list[bool] = []
    original = inspection._Preflight.__call__

    def recording(self: Any, connection: sqlite3.Connection) -> None:
        states.append(connection.in_transaction)
        original(self, connection)

    monkeypatch.setattr(inspection._Preflight, "__call__", recording)
    opened: list[str] = []
    real_connect = sqlite3.connect

    def tracking(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        opened.append(str(args[0]))
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", tracking)
    inspection.inspect_instance_page(instance, state_home=home)
    # One accounted load snapshot, then the Store snapshot, each inside BEGIN.
    assert states == [True, True]
    assert len([uri for uri in opened if "state.sqlite3" in uri]) == 2
    opened.clear()
    control.inspect_instance(instance, state_home=home)
    assert states == [True, True]
    # The public loader keeps its two separate connections, plus the Store.
    assert len([uri for uri in opened if "state.sqlite3" in uri]) == 3


def test_bounded_identity_read_refuses_growth_and_nonregular_without_loading(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, project, home = single_flow
    instance = _instance(workflow, project, home)
    identity = home / "instances" / instance / "instance.json"
    original = identity.read_bytes()
    target = os.stat(identity).st_ino
    real_fstat, real_read, real_json = os.fstat, os.read, control.strict_json
    requested: list[int] = []
    parsed: list[object] = []

    def grow_after_sizing(fd: int) -> os.stat_result:
        info = real_fstat(fd)
        if stat.S_ISREG(info.st_mode) and info.st_ino == target:
            with identity.open("ab") as stream:
                stream.write(b" " * 65536)  # The record grows after it was sized.
        return info

    def reading(fd: int, size: int) -> bytes:
        requested.append(size)
        return real_read(fd, size)

    def counted(raw: object) -> object:
        parsed.append(raw)
        return real_json(raw)

    monkeypatch.setattr(control, "strict_json", counted)
    with monkeypatch.context() as patch:
        patch.setattr(os, "fstat", grow_after_sizing)
        patch.setattr(os, "read", reading)
        with pytest.raises(RouterError, match="inspection_too_large"):
            inspection.inspect_instance_page(instance, state_home=home)
    assert parsed == [] and sum(requested) <= len(original) + 1
    identity.write_bytes(original)
    page = inspection.inspect_instance_page(instance, state_home=home)
    assert page["instance_id"] == instance
    parsed.clear()
    identity.unlink()
    os.mkfifo(identity)  # A nonregular record refuses without waiting for a writer.
    with pytest.raises(RouterError, match="instance_integrity"):
        inspection.inspect_instance_page(instance, state_home=home)
    assert parsed == []


def test_validation_summary_withholds_raw_detail_values(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home = single_flow
    instance = _instance(workflow, project, home)
    sentinel = "/sentinel-cwd-7f3a9c"
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["invoke"]["cwd"] = sentinel
    workflow.write_text(command_graph_json(graph))
    legacy = control.validate_revision(instance, state_home=home)
    # Public validation keeps its existing free-text details.
    assert legacy["valid"] is False
    assert sentinel in json.dumps(legacy["diagnostic_details"])
    summary = inspection.validate_revision_summary(instance, state_home=home)
    data = summary["data"]
    assert data["valid"] is False and data["diagnostics"] == legacy["diagnostics"]
    assert data["diagnostic_details"] == [{"code": "invalid_path", "paths": []}]
    assert sentinel not in json.dumps(summary)


def test_summary_projects_seam_selection_progress_and_acknowledgement_maxima(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import copy

    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    base = graph["planes"].pop("main")
    graph["limits"]["max_invocations"] = 8
    graph["planes"] = {
        name: {**copy.deepcopy(base), "workspace": name}
        for name in ("producer", "consumer")
    }
    for name in graph["planes"]:
        (project / name).mkdir()
    producer = graph["planes"]["producer"]["nodes"]["draft"]
    producer["on"]["READY"] = {
        "retry": {"limit": 1, "exhausted": {"finish": "succeeded"}}
    }
    consumer = graph["planes"]["consumer"]["nodes"]["draft"]
    consumer["invoke"]["config"] = {"script": "consume.py"}
    consumer["on"]["READY"] = {"pause": {"reason": "review", "resume": "draft"}}
    graph["control_files"].append("consume.py")
    graph["seams"] = {
        "handoff": {
            "producer": {"plane": "producer", "node": "draft", "outcome": "READY"},
            "consumer": {"plane": "consumer", "node": "draft"},
        }
    }
    (workflow.parent / "consume.py").write_text(
        "import json\nprint(json.dumps({'outcome':'READY'}))\n"
    )
    workflow.write_text(json.dumps(graph))
    instance = _instance(workflow, project, home)
    run = str(start_run(instance, state_home=home, edit_mode="live")["id"])

    def maxima(legacy: dict[str, Any]) -> dict[str, object]:
        return {
            field: max((row["sequence"] for row in legacy[table]), default=None)
            for field, table in (
                ("latest_occurrence_sequence", "seam_occurrences"),
                ("latest_selection_sequence", "seam_selections"),
                ("latest_acknowledgement_sequence", "seam_acknowledgements"),
            )
        }

    before = control.inspect_run(instance, run, state_home=home)
    assert before["status"] == "paused" and len(before["seam_selections"]) == 1
    assert before["seam_acknowledgements"] == []
    summary = inspection.inspect_run_page(instance, run, state_home=home)["data"]
    assert summary["seams"] == {"handoff": maxima(before)}
    assert summary["seams"]["handoff"]["latest_acknowledgement_sequence"] is None
    selected = summary["planes"]["consumer"]
    assert selected["selected"] == before["planes"]["consumer"]["selected"] == {
        "handoff": 1
    }
    assert selected["progress"] == before["planes"]["consumer"]["progress"]

    consumer["on"]["READY"] = {"goto": "draft"}
    workflow.write_text(json.dumps(graph))
    resumed = control.resume_plane(
        instance,
        run,
        plane_id="consumer",
        state_home=home,
        request_id=new_id(),
        expected_state_version=before["state_version"],
    )
    assert resumed["status"] == "applied", resumed
    after = control.inspect_run(instance, run, state_home=home)
    later = inspection.inspect_run_page(instance, run, state_home=home)["data"]
    assert later["seams"] == {"handoff": maxima(after)}
    assert later["seams"]["handoff"]["latest_acknowledgement_sequence"] == 2
    assert later["planes"]["consumer"]["selected"] == {}
    assert later["planes"]["consumer"]["progress"] == {"handoff": 2}
    _assert_safe(later)
    monkeypatch.setattr(inspection, "MAX_SEAM_ENTRIES", 0)
    with pytest.raises(RouterError, match="inspection_too_large"):
        inspection.inspect_run_page(instance, run, state_home=home)


def test_summary_projects_pending_candidate_and_unrouted_completion(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, project, home = single_flow
    # The genuine command edits its own workflow while it runs (a locked edit).
    (workflow.parent / "scripts/draft.py").write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "path=Path(sys.argv[1])\ngraph=json.loads(path.read_text())\n"
        "graph['planes']['main']['nodes']['draft']['on']['READY']="
        "{'pause':{'reason':'review','resume':'draft'}}\n"
        "path.write_text(json.dumps(graph))\n"
        "print(json.dumps({'outcome':'READY'}))\n"
    )
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["invoke"]["config"]["args"] = [
        str(workflow)
    ]
    workflow.write_text(command_graph_json(graph))
    instance = _instance(workflow, project, home)
    paused = start_run(instance, state_home=home)
    run = str(paused["id"])
    pending = paused["pending_revision"]
    assert paused["status"] == "paused" and pending is not None
    summary = inspection.inspect_run_page(instance, run, state_home=home)["data"]
    candidate = summary["pending_candidate"]
    assert set(candidate) == CANDIDATE_KEYS
    shared = (
        "seq",
        "candidate_id",
        "revision_id",
        "diagnostic",
        "changed_paths",
        "withdrawn",
        "supersedes",
    )
    assert {key: candidate[key] for key in shared} == {
        key: pending[key] for key in shared
    }
    assert candidate["changed_paths"] == ["workflow.json"]
    assert isinstance(candidate["version"], int)
    unrouted = [item["id"] for item in paused["invocations"]]
    assert summary["planes"]["main"]["pending_completion_ids"] == unrouted
    _assert_safe(summary)
    monkeypatch.setattr(inspection, "MAX_LIST_ENTRIES", 0)
    with pytest.raises(RouterError, match="inspection_too_large"):
        inspection.inspect_run_page(instance, run, state_home=home)


def test_inventory_item_refusal_and_page_budget_continuation(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, project, home = single_flow
    busy = _instance(workflow, project, home)
    start_run(busy, state_home=home)
    # Each instance registers its own workflow folder and project.
    second_workflow = workflow.parent.parent / "second-workflow"
    shutil.copytree(workflow.parent, second_workflow)
    second_project = project.parent / "second-project"
    second_project.mkdir()
    (second_project / "request.txt").write_text("A second authored request\n")
    idle = _instance(second_workflow / "workflow.json", second_project, home)
    busy_rows = _store_totals(home / "instances" / busy / "state.sqlite3")[1]
    idle_rows = _store_totals(home / "instances" / idle / "state.sqlite3")[1]
    assert busy_rows > idle_rows
    monkeypatch.setattr(inspection, "INSTANCE_ROWS", idle_rows)
    page = inspection.list_instances_page(state_home=home)
    items = {item["instance_id"]: item for item in page["items"]}
    assert set(items) == {busy, idle} and page["next_cursor"] is None
    assert items[busy]["error_code"] == "inspection_too_large"
    assert {key for key, value in items[busy].items() if value is not None} == {
        "instance_id",
        "error_code",
    }
    assert items[idle]["error_code"] is None
    monkeypatch.setattr(inspection, "PAGE_ROWS", idle_rows + 1)
    first = inspection.list_instances_page(state_home=home)
    assert len(first["items"]) == 1 and isinstance(first["next_cursor"], str)
    second = inspection.list_instances_page(
        state_home=home, cursor=first["next_cursor"]
    )
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    assert {
        first["items"][0]["instance_id"],
        second["items"][0]["instance_id"],
    } == {busy, idle}


def test_reads_leave_store_bytes_and_versions_unchanged(
    single_flow: tuple[Path, Path, Path],
) -> None:
    instance, run, home, final = _retried(single_flow)
    directory = home / "instances" / instance

    def persisted() -> dict[str, str]:
        return {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.iterdir())
            if path.is_file() and not path.name.endswith("-shm")
        }

    before = persisted()
    revision = final["invocations"][0]["revision_id"]
    inspection.list_instances_page(state_home=home)
    inspection.inspect_instance_page(instance, state_home=home)
    for section in ("summary", "invocations", "transitions", "controls"):
        inspection.inspect_run_page(instance, run, state_home=home, section=section)
    inspection.inspect_revision_graph(instance, run, revision, state_home=home)
    inspection.validate_revision_summary(instance, state_home=home, run_id=run)
    assert persisted() == before
    after = control.inspect_run(instance, run, state_home=home)
    assert (after["state_version"], after["controls"], after["candidates"]) == (
        final["state_version"],
        final["controls"],
        final["candidates"],
    )


def test_corrupt_authority_refuses_like_full_validation(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home = single_flow
    instance = _instance(workflow, project, home)
    run = str(start_run(instance, state_home=home)["id"])
    db = sqlite3.connect(home / "instances" / instance / "state.sqlite3")
    try:
        db.execute("UPDATE controls SET receipt=? WHERE run_id IS NULL", ("{}",))
        db.commit()
    finally:
        db.close()
    with pytest.raises(RouterError) as legacy:
        control.inspect_run(instance, run, state_home=home)
    with pytest.raises(RouterError) as bounded:
        inspection.inspect_run_page(instance, run, state_home=home)
    assert bounded.value.code == legacy.value.code != "inspection_too_large"


def test_result_size_limit_refuses_without_partial_result(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, project, home = single_flow
    instance = _instance(workflow, project, home)
    run = str(start_run(instance, state_home=home)["id"])
    monkeypatch.setattr(inspection, "RESULT_BYTES", 64)
    with pytest.raises(RouterError, match="inspection_too_large"):
        inspection.inspect_run_page(instance, run, state_home=home)


@pytest.mark.parametrize(
    "order",
    [
        ("millrace.router.inspection", "millrace.router.control"),
        ("millrace.router.control", "millrace.router.inspection"),
    ],
)
def test_import_order_and_control_reexports(order: tuple[str, str]) -> None:
    code = (
        f"import {order[0]}, {order[1]}\n"
        "from millrace.router import control, inspection\n"
        "names = set(inspection.__all__) - {'INSPECTION_CONTRACT'}\n"
        "assert names <= set(control.__all__)\n"
        "assert 'INSPECTION_CONTRACT' not in control.__all__\n"
        "assert control.INSPECTION_CONTRACT == 'millrace-router-inspection/v1'\n"
        "assert all(getattr(control, n) is getattr(inspection, n) for n in names)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
