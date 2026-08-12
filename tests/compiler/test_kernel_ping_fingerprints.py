from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import cast

import pytest

from millrace.compiler import SelectedRunnerAdapterPolicy, compile_workflow
from millrace.compiler.canonical import (
    authority_fingerprint,
    canonical_authority_bytes,
)
from millrace.contracts import SelectedCompiledPlan, WorkflowVersion
from millrace.workflows import kernel_ping
from support import kernel_ping as kernel_ping_support

Source = dict[str, object]
Record = dict[str, object]


def _source() -> Source:
    return deepcopy(kernel_ping.WORKFLOW_SOURCE)


def _records(source: Source, key: str) -> list[Record]:
    return cast(list[Record], source[key])


def _collapse_to_one_runner(source: Source) -> Record:
    runner = _records(source, "runner_bindings")[0]
    runner_id = str(runner["id"])
    source["runner_bindings"] = [runner]
    runner["stage_kind_ids"] = ("kernel_ping.taskmaster", "kernel_ping.worker")
    for stage in _records(source, "stage_kinds"):
        stage["runner_binding_id"] = runner_id
    for route in _records(source, "external_enqueue_routes"):
        route["runner_binding_id"] = runner_id
    for action in _records(source, "terminal_actions"):
        if action.get("runner_binding_id") is not None:
            action["runner_binding_id"] = runner_id
    return runner


def _compile_plan(
    source: Mapping[str, object],
    *,
    selected_runner_policy: SelectedRunnerAdapterPolicy | None = None,
) -> SelectedCompiledPlan:
    if selected_runner_policy is None:
        result = compile_workflow(source)
    else:
        result = compile_workflow(
            source,
            selected_runner_policy=selected_runner_policy,
        )
    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ] == []
    assert result.plan is not None
    return result.plan


def _compile_error_codes(source: Mapping[str, object]) -> list[str]:
    result = compile_workflow(source)
    assert result.plan is None
    return [
        diagnostic.code
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ]


def _assert_authority_changed(
    base_plan: SelectedCompiledPlan,
    changed_plan: SelectedCompiledPlan,
) -> None:
    assert changed_plan != base_plan
    assert canonical_authority_bytes(base_plan) != canonical_authority_bytes(
        changed_plan
    )
    assert authority_fingerprint(base_plan) != authority_fingerprint(changed_plan)


def _source_with_runner_component() -> Source:
    source = _source()
    source["capabilities"] = [
        {
            "id": "capability.runner.invoke",
            "kind": "runner.invoke",
            "support_status": "supported",
            "grant_status": "granted",
        },
        {
            "id": "capability.runner.audit",
            "kind": "runner.invoke",
            "support_status": "supported",
            "grant_status": "granted",
        },
    ]
    runner = _collapse_to_one_runner(source)
    runner.update(
        {
            "adapter_kind": "codex",
            "required_capability_ids": (
                "capability.runner.invoke",
                "capability.runner.audit",
            ),
            "component_pin": {
                "component_kind": "opaque.runner",
                "component_id": "example.component",
                "component_version": "1.2.3",
                "provider_distribution": "example-provider",
                "provider_version": "4.5.6",
                "descriptor_media_type": "application/vnd.example.runner+json",
                "descriptor_sha256": "a" * 64,
                "required_capability_ids": (
                    "capability.runner.invoke",
                    "capability.runner.audit",
                ),
                "legal_terminal_result_ids": ("COMPLETE", "BLOCKED"),
            },
            "terminal_result_mappings": (
                {
                    "stage_kind_id": "kernel_ping.taskmaster",
                    "runner_result_id": "COMPLETE",
                    "outcome_id": "kernel_ping.taskmaster.task_complete",
                },
                {
                    "stage_kind_id": "kernel_ping.taskmaster",
                    "runner_result_id": "BLOCKED",
                    "outcome_id": "kernel_ping.taskmaster.blocked",
                },
            ),
        }
    )
    return source


def test_runner_component_and_mapping_order_do_not_change_fingerprint() -> None:
    base_source = _source_with_runner_component()
    reordered_source = _source_with_runner_component()
    runner = _records(reordered_source, "runner_bindings")[0]
    pin = cast(Record, runner["component_pin"])
    pin["required_capability_ids"] = tuple(
        reversed(cast(tuple[str, ...], pin["required_capability_ids"]))
    )
    pin["legal_terminal_result_ids"] = tuple(
        reversed(cast(tuple[str, ...], pin["legal_terminal_result_ids"]))
    )
    runner["terminal_result_mappings"] = tuple(
        reversed(cast(tuple[Record, ...], runner["terminal_result_mappings"]))
    )

    base_plan = _compile_plan(base_source)
    reordered_plan = _compile_plan(reordered_source)

    assert canonical_authority_bytes(base_plan) == canonical_authority_bytes(
        reordered_plan
    )
    assert authority_fingerprint(base_plan) == authority_fingerprint(reordered_plan)


@pytest.mark.parametrize(
    "change",
    (
        "component_kind",
        "component_id",
        "component_version",
        "provider_distribution",
        "provider_version",
        "descriptor_media_type",
        "descriptor_sha256",
        "required_capability_ids",
        "legal_terminal_result_ids",
        "mapping_stage",
        "mapping_result",
        "mapping_outcome",
    ),
)
def test_selected_runner_component_or_mapping_change_changes_fingerprint(
    change: str,
) -> None:
    base_source = _source_with_runner_component()
    changed_source = _source_with_runner_component()
    runner = _records(changed_source, "runner_bindings")[0]
    pin = cast(Record, runner["component_pin"])
    mappings = list(cast(tuple[Record, ...], runner["terminal_result_mappings"]))
    if change in {
        "component_kind",
        "component_id",
        "component_version",
        "provider_distribution",
        "provider_version",
        "descriptor_media_type",
    }:
        pin[change] = f"changed-{change}"
    elif change == "descriptor_sha256":
        pin[change] = "b" * 64
    elif change == "required_capability_ids":
        pin[change] = ("capability.runner.invoke",)
    elif change == "legal_terminal_result_ids":
        pin[change] = ("COMPLETE", "BLOCKED", "RETRY")
    elif change == "mapping_stage":
        mappings[0] = {
            **mappings[0],
            "stage_kind_id": "kernel_ping.worker",
            "outcome_id": "kernel_ping.worker.work_complete",
        }
    elif change == "mapping_result":
        mappings[0] = {**mappings[0], "runner_result_id": "BLOCKED"}
        mappings.pop(1)
    else:
        mappings[0] = {
            **mappings[0],
            "outcome_id": "kernel_ping.taskmaster.blocked",
        }
        mappings.pop(1)
    runner["terminal_result_mappings"] = tuple(mappings)

    _assert_authority_changed(
        _compile_plan(base_source),
        _compile_plan(changed_source),
    )


def test_omitted_runner_invocation_timeout_normalizes_to_3600() -> None:
    plan = _compile_plan(_source())

    assert getattr(plan.runner_bindings[0], "invocation_timeout_seconds", None) == 3600


def test_runner_invocation_timeout_default_is_canonical_authority() -> None:
    omitted_plan = _compile_plan(_source())
    explicit_default_source = _source()
    _records(explicit_default_source, "runner_bindings")[0][
        "invocation_timeout_seconds"
    ] = 3600
    explicit_default_plan = _compile_plan(explicit_default_source)
    selected_override_source = _source()
    _records(selected_override_source, "runner_bindings")[0][
        "invocation_timeout_seconds"
    ] = 1800
    selected_override_plan = _compile_plan(selected_override_source)

    assert canonical_authority_bytes(omitted_plan) == canonical_authority_bytes(
        explicit_default_plan
    )
    assert authority_fingerprint(omitted_plan) == authority_fingerprint(
        explicit_default_plan
    )
    _assert_authority_changed(explicit_default_plan, selected_override_plan)


def test_selected_terminal_action_change_alters_bytes_and_fingerprint() -> None:
    base_plan = _compile_plan(_source())
    changed_source = _source()
    close_action = next(
        action
        for action in _records(changed_source, "terminal_actions")
        if action["id"] == "kernel_ping.close_worker_success"
    )
    close_action["kind"] = "pause_quarantine"

    _assert_authority_changed(base_plan, _compile_plan(changed_source))


def test_nested_stage_asset_order_remains_authority() -> None:
    base_plan = _compile_plan(_source())
    changed_source = _source()
    stage = next(
        stage
        for stage in _records(changed_source, "stage_kinds")
        if len(cast(tuple[str, ...], stage["asset_ids"])) > 1
    )
    asset_ids = cast(tuple[str, ...], stage["asset_ids"])
    stage["asset_ids"] = tuple(reversed(asset_ids))

    _assert_authority_changed(base_plan, _compile_plan(changed_source))


def test_selected_artifact_schema_requirement_change_alters_fingerprint() -> None:
    base_plan = _compile_plan(_source())
    changed_source = _source()
    task_schema = next(
        schema
        for schema in _records(changed_source, "artifact_schemas")
        if schema["id"] == "kernel_ping.task_artifact"
    )
    schema_body = cast(Record, task_schema["schema"])
    schema_body["required"] = (
        *cast(tuple[str, ...], schema_body["required"]),
        "allowed_files",
    )

    _assert_authority_changed(base_plan, _compile_plan(changed_source))


def test_selected_runner_binding_contract_change_alters_bytes_and_fingerprint() -> None:
    base_plan = _compile_plan(_source())
    changed_source = _source()
    runner = _records(changed_source, "runner_bindings")[0]
    runner["adapter_kind"] = "codex.experimental"
    policy = SelectedRunnerAdapterPolicy(
        supported_adapter_kinds=frozenset({"codex", "codex.experimental", "millforge"}),
    )

    _assert_authority_changed(
        base_plan,
        _compile_plan(changed_source, selected_runner_policy=policy),
    )


def test_kernel_ping_millforge_default_fingerprint_is_stable() -> None:
    plan = _compile_plan(_source())

    assert authority_fingerprint(plan) == (
        "sha256:3282a891816a1514bc16cce5e4d0ecc086fb874ad8a95add59cce8d386845e8f"
    )


def test_selected_external_enqueue_route_change_alters_bytes_and_fingerprint() -> None:
    base_plan = _compile_plan(_source())
    changed_source = _source()
    graph = _records(changed_source, "graphs")[0]
    graph["node_ids"] = (
        *cast(tuple[str, ...], graph["node_ids"]),
        "kernel_ping.taskmaster.alternate_start",
    )
    external_route = _records(changed_source, "external_enqueue_routes")[0]
    external_route["graph_node_id"] = "kernel_ping.taskmaster.alternate_start"

    _assert_authority_changed(base_plan, _compile_plan(changed_source))


def test_selected_terminal_action_target_graph_change_alters_fingerprint() -> None:
    base_plan = _compile_plan(_source())
    changed_source = _source()
    graph = _records(changed_source, "graphs")[0]
    graph["node_ids"] = (
        *cast(tuple[str, ...], graph["node_ids"]),
        "kernel_ping.worker.alternate_start",
    )
    success_action = next(
        action
        for action in _records(changed_source, "terminal_actions")
        if action["id"] == "kernel_ping.route_taskmaster_success"
    )
    success_action["target_graph_node_id"] = "kernel_ping.worker.alternate_start"

    _assert_authority_changed(base_plan, _compile_plan(changed_source))


def test_selected_terminal_action_projection_change_alters_fingerprint() -> None:
    base_plan = _compile_plan(_source())
    changed_source = _source()
    success_action = next(
        action
        for action in _records(changed_source, "terminal_actions")
        if action["id"] == "kernel_ping.route_taskmaster_success"
    )
    success_action["payload_projection"] = {
        "kind": "object",
        "fields": {
            "routed": {
                "kind": "source",
                "path": ("artifact_payload",),
            }
        },
    }

    _assert_authority_changed(base_plan, _compile_plan(changed_source))


def test_unselected_source_catalog_data_is_absent_from_authority() -> None:
    base_plan = _compile_plan(_source())
    source_with_unselected_catalog = (
        kernel_ping_support.workflow_source_with_unselected_catalog()
    )
    result = compile_workflow(source_with_unselected_catalog)
    assert result.plan is not None
    changed_plan = result.plan

    unselected_id = kernel_ping_support.UNSELECTED_CATALOG_ID
    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "warning"
        and diagnostic.context.get("runner_binding_id") == unselected_id
    ] == []
    assert unselected_id not in repr(changed_plan)
    assert unselected_id.encode() not in canonical_authority_bytes(changed_plan)
    assert canonical_authority_bytes(base_plan) == canonical_authority_bytes(
        changed_plan
    )
    assert authority_fingerprint(base_plan) == authority_fingerprint(changed_plan)


def test_unselected_catalog_timeout_mutation_does_not_change_authority() -> None:
    base_source = kernel_ping_support.workflow_source_with_unselected_catalog()
    changed_source = deepcopy(base_source)
    catalog_entry = cast(tuple[Record, ...], changed_source["unselected_catalog"])[0]
    catalog_payload = cast(Record, catalog_entry["catalog_payload"])
    catalog_payload["invocation_timeout_seconds"] = 1800

    base_plan = _compile_plan(base_source)
    changed_plan = _compile_plan(changed_source)

    assert canonical_authority_bytes(base_plan) == canonical_authority_bytes(
        changed_plan
    )
    assert authority_fingerprint(base_plan) == authority_fingerprint(changed_plan)


def test_malformed_unselected_catalog_data_is_diagnosed() -> None:
    source = kernel_ping_support.workflow_source_with_unselected_catalog()
    catalog_entry = cast(tuple[Record, ...], source["unselected_catalog"])[0]
    catalog_payload = cast(Record, catalog_entry["catalog_payload"])
    catalog_payload["unsupported_float"] = 1.5

    assert _compile_error_codes(source) == ["unsupported_authority_value"]


def test_malformed_non_sequence_unselected_catalog_is_diagnosed() -> None:
    source = _source()
    source["unselected_catalog"] = {
        "id": kernel_ping_support.UNSELECTED_CATALOG_ID,
        "catalog_payload": {"contract_version": 1},
    }

    assert _compile_error_codes(source) == ["unsupported_authority_value"]


def test_no_pause_revision_differs_without_mutating_revision_0_1() -> None:
    base_source = _source()
    base_plan = _compile_plan(base_source)
    alternate_plan = _compile_plan(kernel_ping_support.no_pause_workflow_source())
    after_alternate_base_plan = _compile_plan(_source())

    assert base_plan.workflow.workflow_version == WorkflowVersion("0.1")
    assert alternate_plan.workflow.workflow_version == WorkflowVersion("0.1-no-pause")
    assert after_alternate_base_plan == base_plan

    base_action_kinds = {
        str(action.id): action.action_kind for action in base_plan.terminal_actions
    }
    alternate_action_kinds = {
        str(action.id): action.action_kind for action in alternate_plan.terminal_actions
    }
    assert base_action_kinds["kernel_ping.pause_taskmaster_blocked"] == (
        "pause_quarantine"
    )
    assert alternate_action_kinds["kernel_ping.pause_taskmaster_blocked"] == "route"
    assert base_action_kinds["kernel_ping.pause_worker_blocked"] == ("pause_quarantine")
    assert alternate_action_kinds["kernel_ping.pause_worker_blocked"] == "route"

    _assert_authority_changed(base_plan, alternate_plan)
