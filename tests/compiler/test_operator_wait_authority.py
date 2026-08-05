from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import cast

import pytest

from millrace.compiler import SelectedRunnerAdapterPolicy, compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.compiler.export import compiled_plan_export_record
from millrace.contracts import Diagnostic
from support import generic_operator_wait

Source = dict[str, object]
Record = dict[str, object]
_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)


def _source() -> Source:
    return generic_operator_wait.source()


def _records(source: Source, key: str) -> list[Record]:
    return cast(list[Record], source[key])


def _operator_wait(source: Source, wait_id: str) -> Record:
    return next(
        wait for wait in _records(source, "operator_waits") if wait["id"] == wait_id
    )


def _errors(source: Source) -> tuple[Diagnostic, ...]:
    result = compile_workflow(source, selected_runner_policy=_CODEX_POLICY)
    assert result.plan is None
    return tuple(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    )


def _find_error(errors: Iterable[Diagnostic], code: str) -> Diagnostic:
    matches = [diagnostic for diagnostic in errors if diagnostic.code == code]
    assert matches, f"missing diagnostic code {code!r} in {tuple(errors)!r}"
    return matches[0]


def _find_operator_wait_field_error(
    errors: Iterable[Diagnostic],
    field_name: str,
) -> Diagnostic:
    matches = [
        diagnostic
        for diagnostic in errors
        if diagnostic.code == "invalid_operator_wait_field"
        and diagnostic.context.get("field_name") == field_name
    ]
    assert matches, f"missing invalid field {field_name!r} in {tuple(errors)!r}"
    return matches[0]


def _compile(source: Source):
    result = compile_workflow(source)
    assert result.plan is not None, result.diagnostics
    return result.plan


def test_operator_wait_source_projection_is_normalized_and_fingerprinted() -> None:
    omitted_source = _source()
    omitted_plan = _compile(omitted_source)
    omitted_wait = next(
        wait
        for wait in omitted_plan.operator_waits
        if str(wait.id) == generic_operator_wait.REVISE_WAIT_ID
    )
    omitted_close_wait = next(
        wait
        for wait in omitted_plan.operator_waits
        if str(wait.id) == generic_operator_wait.CLOSE_WAIT_ID
    )

    false_source = _source()
    _operator_wait(false_source, generic_operator_wait.REVISE_WAIT_ID)[
        "project_source_artifact"
    ] = False
    false_plan = _compile(false_source)

    true_source = _source()
    _operator_wait(true_source, generic_operator_wait.REVISE_WAIT_ID)[
        "project_source_artifact"
    ] = True
    true_plan = _compile(true_source)
    true_wait = next(
        wait
        for wait in true_plan.operator_waits
        if str(wait.id) == generic_operator_wait.REVISE_WAIT_ID
    )

    assert omitted_wait.project_source_artifact is False
    assert omitted_close_wait.project_source_artifact is False
    assert false_plan == omitted_plan
    assert true_wait.project_source_artifact is True
    selected = cast(
        dict[str, object],
        compiled_plan_export_record(true_plan)["selected_authority"],
    )
    exported_waits = cast(list[dict[str, object]], selected["operator_waits"])
    assert next(
        wait
        for wait in exported_waits
        if wait["id"] == generic_operator_wait.REVISE_WAIT_ID
    )["project_source_artifact"] is True
    assert authority_fingerprint(true_plan) != authority_fingerprint(false_plan)


@pytest.mark.parametrize("value", (None, 0, 1, "true", (), {}))
def test_operator_wait_source_projection_rejects_non_bool(value: object) -> None:
    source = _source()
    _operator_wait(source, generic_operator_wait.REVISE_WAIT_ID)[
        "project_source_artifact"
    ] = value

    error = _find_operator_wait_field_error(
        _errors(source), "project_source_artifact"
    )

    assert error.declaration_path == "operator_waits[0].project_source_artifact"


@pytest.mark.parametrize("value", (False, True))
def test_authored_operator_wait_projection_requires_revise_authority(
    value: bool,
) -> None:
    source = _source()
    _operator_wait(source, generic_operator_wait.CLOSE_WAIT_ID)[
        "project_source_artifact"
    ] = value

    error = _find_operator_wait_field_error(
        _errors(source), "project_source_artifact"
    )

    assert error.context["operator_wait_id"] == generic_operator_wait.CLOSE_WAIT_ID


def test_operator_wait_source_projection_requires_source_artifact_schema() -> None:
    source = _source()
    _operator_wait(source, generic_operator_wait.REVISE_WAIT_ID)[
        "project_source_artifact"
    ] = True
    action = next(
        action
        for action in _records(source, "terminal_actions")
        if action["id"] == generic_operator_wait.REVISE_ACTION_ID
    )
    action["artifact_schema_id"] = None

    error = _find_error(
        _errors(source), "operator_wait_projection_source_artifact_required"
    )

    assert error.context["action_id"] == generic_operator_wait.REVISE_ACTION_ID


def test_operator_wait_source_projection_requires_target_stage_schema() -> None:
    source = _source()
    _operator_wait(source, generic_operator_wait.REVISE_WAIT_ID)[
        "project_source_artifact"
    ] = True
    target = next(
        stage
        for stage in _records(source, "stage_kinds")
        if stage["id"] == "kernel_ping.taskmaster"
    )
    target["artifact_schema_ids"] = ("kernel_ping.task_artifact",)

    error = _find_error(
        _errors(source), "operator_wait_projection_target_schema_missing"
    )

    assert error.context["artifact_schema_id"] == "kernel_ping.task_incident"


def test_operator_wait_source_projection_checks_every_source_action_schema() -> None:
    source = _source()
    wait = _operator_wait(source, generic_operator_wait.REVISE_WAIT_ID)
    wait["project_source_artifact"] = True
    wait["source_action_ids"] = (
        generic_operator_wait.REVISE_ACTION_ID,
        generic_operator_wait.CLOSE_ACTION_ID,
    )
    close_action = next(
        action
        for action in _records(source, "terminal_actions")
        if action["id"] == generic_operator_wait.CLOSE_ACTION_ID
    )
    close_action["artifact_schema_id"] = "kernel_ping.task_artifact"
    target = next(
        stage
        for stage in _records(source, "stage_kinds")
        if stage["id"] == "kernel_ping.taskmaster"
    )
    target["artifact_schema_ids"] = ("kernel_ping.task_incident",)
    _records(source, "operator_waits").remove(
        _operator_wait(source, generic_operator_wait.CLOSE_WAIT_ID)
    )

    error = _find_error(
        _errors(source), "operator_wait_projection_target_schema_missing"
    )

    assert error.context["action_id"] == generic_operator_wait.CLOSE_ACTION_ID
    assert error.context["artifact_schema_id"] == "kernel_ping.task_artifact"


def test_duplicate_operator_wait_owner_is_rejected() -> None:
    source = _source()
    original = _operator_wait(source, generic_operator_wait.REVISE_WAIT_ID)
    duplicate = deepcopy(original)
    duplicate["id"] = "test.duplicate_manager_detail_wait"
    _records(source, "operator_waits").append(duplicate)

    error = _find_error(_errors(source), "duplicate_operator_wait_owner")

    assert error.context["action_id"] == generic_operator_wait.REVISE_ACTION_ID
    assert error.context["first_operator_wait_id"] == (
        generic_operator_wait.REVISE_WAIT_ID
    )
    assert error.context["duplicate_operator_wait_id"] == (
        "test.duplicate_manager_detail_wait"
    )


def test_duplicate_operator_wait_source_action_is_rejected() -> None:
    source = _source()
    wait = _operator_wait(source, generic_operator_wait.REVISE_WAIT_ID)
    wait["source_action_ids"] = (
        generic_operator_wait.REVISE_ACTION_ID,
        generic_operator_wait.REVISE_ACTION_ID,
    )

    error = _find_error(_errors(source), "duplicate_operator_wait_source_action")

    assert error.context["operator_wait_id"] == generic_operator_wait.REVISE_WAIT_ID
    assert error.context["action_id"] == generic_operator_wait.REVISE_ACTION_ID


def test_empty_operator_wait_source_actions_are_rejected() -> None:
    source = _source()
    wait = _operator_wait(source, generic_operator_wait.REVISE_WAIT_ID)
    wait["source_action_ids"] = ()

    error = _find_operator_wait_field_error(_errors(source), "source_action_ids")

    assert error.declaration_path == "operator_waits[0].source_action_ids"
    assert error.context["operator_wait_id"] == generic_operator_wait.REVISE_WAIT_ID
    assert error.context["value"] == ""


def test_empty_operator_wait_resolution_kinds_are_rejected() -> None:
    source = _source()
    wait = _operator_wait(source, generic_operator_wait.REVISE_WAIT_ID)
    wait["allowed_resolution_kinds"] = ()
    wait["audit_metadata_requirements"] = ()

    error = _find_operator_wait_field_error(_errors(source), "allowed_resolution_kinds")

    assert error.context["operator_wait_id"] == generic_operator_wait.REVISE_WAIT_ID


def test_duplicate_operator_wait_resolution_kind_is_rejected() -> None:
    source = _source()
    wait = _operator_wait(source, generic_operator_wait.REVISE_WAIT_ID)
    wait["allowed_resolution_kinds"] = (
        "resume_recorded_source",
        "resume_recorded_source",
    )

    error = _find_error(_errors(source), "duplicate_operator_wait_resolution_kind")

    assert error.context["operator_wait_id"] == generic_operator_wait.REVISE_WAIT_ID
    assert error.context["resolution_kind"] == "resume_recorded_source"


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("wait_scope", "workspace"),
        ("source_work_item_behavior", "pause"),
        ("unrelated_lineages_continue", False),
        ("actor_kind", "remote_operator"),
        ("audit_metadata_requirements", ()),
        ("correlation_key", "lineage_id"),
        ("idempotency", "none"),
        ("timeout_policy", "fifteen_minutes"),
        ("expiry_policy", "after_timeout"),
        ("cancellation_policy", "runtime_cancel"),
        ("status_effect", "blocked"),
    ),
)
def test_operator_wait_fixed_policy_fields_are_rejected(
    field_name: str,
    value: object,
) -> None:
    source = _source()
    wait = _operator_wait(source, generic_operator_wait.REVISE_WAIT_ID)
    wait[field_name] = value

    error = _find_operator_wait_field_error(_errors(source), field_name)

    assert error.context["operator_wait_id"] == generic_operator_wait.REVISE_WAIT_ID


@pytest.mark.parametrize(
    "resolution_kind",
    ("resume_recorded_source", "revise_recorded_source"),
)
def test_close_on_create_wait_rejects_non_close_resolution_kind(
    resolution_kind: str,
) -> None:
    source = _source()
    wait = _operator_wait(source, generic_operator_wait.CLOSE_WAIT_ID)
    wait["allowed_resolution_kinds"] = ("close_recorded_source", resolution_kind)
    if resolution_kind == "revise_recorded_source":
        wait.update(
            {
                "payload_schema_id": "kernel_ping.task_artifact",
                "target_queue_family_id": "prompt",
                "target_stage_kind_id": "kernel_ping.taskmaster",
                "target_graph_node_id": "kernel_ping.taskmaster.start",
                "target_runner_binding_id": "kernel_ping.taskmaster_runner",
            }
        )

    error = _find_operator_wait_field_error(
        _errors(source),
        "source_work_item_behavior",
    )

    assert error.context["operator_wait_id"] == generic_operator_wait.CLOSE_WAIT_ID
    assert error.context["value"] == "close_on_create"


def test_revise_target_fields_without_revise_authority_are_rejected() -> None:
    source = _source()
    wait = _operator_wait(source, generic_operator_wait.CLOSE_WAIT_ID)
    wait["payload_schema_id"] = "kernel_ping.task_artifact"

    error = _find_operator_wait_field_error(_errors(source), "payload_schema_id")

    assert error.context["operator_wait_id"] == generic_operator_wait.CLOSE_WAIT_ID


def test_revise_resolution_requires_all_target_fields() -> None:
    source = _source()
    wait = _operator_wait(source, generic_operator_wait.REVISE_WAIT_ID)
    del wait["target_runner_binding_id"]

    error = _find_error(_errors(source), "missing_operator_wait_field")

    assert error.context["referrer_path"] == "operator_waits[0]"
    assert error.context["field_name"] == "target_runner_binding_id"


def test_operator_wait_revise_payload_schema_must_match_target_route_schema() -> None:
    source = _source()
    external_route = _records(source, "external_enqueue_routes")[0]
    external_route["payload_schema_id"] = "kernel_ping.task_artifact"
    wait = _operator_wait(source, generic_operator_wait.REVISE_WAIT_ID)
    wait["payload_schema_id"] = "kernel_ping.task_incident"

    error = _find_error(_errors(source), "intervention_target_payload_schema_mismatch")

    assert error.declaration_path == "operator_waits[0].payload_schema_id"
    assert error.context["referrer_path"] == "operator_waits[0]"
    assert error.context["payload_schema_id"] == "kernel_ping.task_incident"
    assert error.context["target_payload_schema_id"] == "kernel_ping.task_artifact"
