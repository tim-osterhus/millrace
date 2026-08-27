from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import cast

import pytest

from millrace.compiler import authority_fingerprint, compile_workflow
from millrace.compiler.export import compiled_plan_export_record
from millrace.substrate.codecs import _decode_terminal_action, _encode_terminal_action
from millrace.workflows import kernel_ping


def _source_with_conditions(
    conditions: object,
) -> dict[str, object]:
    source = deepcopy(kernel_ping.WORKFLOW_SOURCE)
    actions = cast(list[dict[str, object]], source["terminal_actions"])
    action = next(
        item
        for item in actions
        if item["id"] == "kernel_ping.route_taskmaster_success"
    )
    action["artifact_field_conditions"] = conditions
    return source


def _conditioned_action_source() -> dict[str, object]:
    return _source_with_conditions(
        {
            "artifact_kind": "kernel_ping.task_artifact",
            "title": "Executable task",
        }
    )


def test_terminal_action_conditions_are_compiled_and_exported() -> None:
    source = _conditioned_action_source()
    baseline = compile_workflow(kernel_ping.workflow_source())
    result = compile_workflow(source)

    assert result.plan is not None
    assert baseline.plan is not None
    action = next(
        item
        for item in result.plan.terminal_actions
        if str(item.id) == "kernel_ping.route_taskmaster_success"
    )
    assert action.artifact_field_conditions == {
        "artifact_kind": "kernel_ping.task_artifact",
        "title": "Executable task",
    }
    assert authority_fingerprint(result.plan) != authority_fingerprint(baseline.plan)

    exported = compiled_plan_export_record(result.plan)
    selected_authority = cast(dict[str, object], exported["selected_authority"])
    actions = cast(list[dict[str, object]], selected_authority["terminal_actions"])
    exported_action = next(
        item
        for item in actions
        if item["id"] == "kernel_ping.route_taskmaster_success"
    )
    assert exported_action["artifact_field_conditions"] == {
        "artifact_kind": "kernel_ping.task_artifact",
        "title": "Executable task",
    }

    encoded = _encode_terminal_action(action)
    assert encoded["artifact_field_conditions"] == {
        "artifact_kind": "kernel_ping.task_artifact",
        "title": "Executable task",
    }
    assert _decode_terminal_action(encoded) == action

    legacy_record = dict(encoded)
    legacy_record.pop("artifact_field_conditions")
    assert _decode_terminal_action(legacy_record) == replace(
        action,
        artifact_field_conditions={},
    )


def test_empty_terminal_action_conditions_preserve_legacy_export() -> None:
    result = compile_workflow(kernel_ping.workflow_source())

    assert result.plan is not None
    exported = compiled_plan_export_record(result.plan)
    selected_authority = cast(dict[str, object], exported["selected_authority"])
    actions = cast(list[dict[str, object]], selected_authority["terminal_actions"])
    assert all("artifact_field_conditions" not in action for action in actions)


@pytest.mark.parametrize(
    "conditions",
    (
        {"unknown_field": "value"},
        {"artifact_version": 1},
        {"requirements.id": "value"},
        {"title": 1},
        {"title": 1.5},
        {"title": ["value"]},
    ),
)
def test_terminal_action_conditions_are_rejected_deterministically(
    conditions: object,
) -> None:
    result = compile_workflow(_source_with_conditions(conditions))

    assert result.plan is None
    diagnostics = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code == "invalid_terminal_action_artifact_field_condition"
    ]
    assert len(diagnostics) == 1
    assert diagnostics[0].declaration_path.startswith(
        "terminal_actions[0].artifact_field_conditions"
    )
