from __future__ import annotations

import json
from collections.abc import Mapping, MutableMapping
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from millrace.compiler import compile_workflow
from millrace.contracts.compiled_plan import (
    AuthorityValue,
    SelectedCompiledPlan,
    authority_fingerprint,
)
from millrace.workflows import kernel_ping
from support import generic_admission, generic_effect, generic_lifecycle
from support.kernel_ping import compile_kernel_ping


def _canonical_object_bytes(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _object_from_bytes(value: bytes) -> dict[str, object]:
    parsed = json.loads(value.decode("utf-8"))
    assert isinstance(parsed, dict)
    return cast(dict[str, object], parsed)


def _stage_kind_record(
    selected_plan_payload: dict[str, object],
    stage_kind_id: str,
) -> dict[str, object]:
    stage_kinds = cast(list[object], selected_plan_payload["stage_kinds"])
    return next(
        cast(dict[str, object], stage)
        for stage in stage_kinds
        if cast(dict[str, object], stage)["id"] == stage_kind_id
    )


def _component_plan() -> SelectedCompiledPlan:
    source = kernel_ping.workflow_source()
    source["capabilities"] = [
        {
            "id": "capability.runner.invoke",
            "kind": "runner.invoke",
            "support_status": "supported",
            "grant_status": "granted",
        }
    ]
    runner = cast(list[dict[str, object]], source["runner_bindings"])[0]
    runner_id = str(runner["id"])
    source["runner_bindings"] = [runner]
    for key in ("stage_kinds", "external_enqueue_routes", "terminal_actions"):
        for record in cast(list[dict[str, object]], source[key]):
            if record.get("runner_binding_id") is not None:
                record["runner_binding_id"] = runner_id
    runner.update(
        {
            "adapter_kind": "codex",
            "stage_kind_ids": (
                "kernel_ping.taskmaster",
                "kernel_ping.worker",
            ),
            "required_capability_ids": ("capability.runner.invoke",),
            "component_pin": {
                "component_kind": "opaque.runner",
                "component_id": "example.component",
                "component_version": "1.2.3",
                "provider_distribution": "example-provider",
                "provider_version": "4.5.6",
                "descriptor_media_type": "application/vnd.example.runner+json",
                "descriptor_sha256": "a" * 64,
                "required_capability_ids": ("capability.runner.invoke",),
                "legal_terminal_result_ids": ("COMPLETE", "BLOCKED"),
            },
            "terminal_result_mappings": (
                {
                    "stage_kind_id": "kernel_ping.taskmaster",
                    "runner_result_id": "COMPLETE",
                    "outcome_id": "kernel_ping.taskmaster.task_complete",
                },
            ),
        }
    )
    result = compile_workflow(source)
    assert result.plan is not None
    return result.plan


def _component_free_capability_plan() -> SelectedCompiledPlan:
    source = kernel_ping.workflow_source()
    source["capabilities"] = [
        {
            "id": "capability.runner.invoke",
            "kind": "runner.invoke",
            "support_status": "supported",
            "grant_status": "granted",
        }
    ]
    runners = cast(list[dict[str, object]], source["runner_bindings"])
    for runner in runners:
        runner["adapter_kind"] = "codex"
        runner["required_capability_ids"] = ("capability.runner.invoke",)
        runner.pop("component_pin")
        runner.pop("terminal_result_mappings")
    result = compile_workflow(source)
    assert result.plan is not None
    assert result.plan.runner_bindings[0].component_pin is None
    return result.plan


def _partitionless_plan() -> tuple[SelectedCompiledPlan, str]:
    source = generic_lifecycle.source()
    stages = cast(list[dict[str, object]], source["stage_kinds"])
    stages[-1]["partition_id"] = None
    return generic_lifecycle.compile_lifecycle(source)


def _generated_work_plan() -> tuple[SelectedCompiledPlan, str]:
    source = generic_lifecycle.source()
    for fanout in cast(list[dict[str, object]], source["fanout_declarations"]):
        fanout["source_state_policy"] = "accepted_terminal_observation"
        fanout["dependency_policy"] = "none"
    source["concurrency_policies"] = [
        {
            "id": "lifecycle.primary_limit",
            "partition_id": "primary",
            "max_active_runs": 1,
            "coexist_partition_ids": (),
        }
    ]
    return generic_lifecycle.compile_lifecycle(source)


_RUNNER_COMPONENT_AUTHORITY_FIELDS = (
    "component_kind",
    "component_id",
    "component_version",
    "provider_distribution",
    "provider_version",
    "descriptor_media_type",
    "descriptor_sha256",
    "required_capability_ids",
    "legal_terminal_result_ids",
    "mapping_stage_kind_id",
    "mapping_runner_result_id",
    "mapping_outcome_id",
)


def _mutate_valid_runner_component_authority(
    payload: dict[str, object],
    field_name: str,
) -> None:
    runner = cast(list[dict[str, object]], payload["runner_bindings"])[0]
    pin = cast(dict[str, object], runner["component_pin"])
    mapping = cast(list[dict[str, object]], runner["terminal_result_mappings"])[0]
    if field_name == "descriptor_sha256":
        pin[field_name] = "b" * 64
    elif field_name in {
        "component_kind",
        "component_id",
        "component_version",
        "provider_distribution",
        "provider_version",
        "descriptor_media_type",
    }:
        pin[field_name] = f"changed.{field_name}"
    elif field_name == "required_capability_ids":
        pin[field_name] = []
    elif field_name == "legal_terminal_result_ids":
        pin[field_name] = ["BLOCKED", "COMPLETE", "RETRY"]
    elif field_name == "mapping_stage_kind_id":
        mapping["stage_kind_id"] = "kernel_ping.worker"
        mapping["outcome_id"] = "kernel_ping.worker.work_complete"
    elif field_name == "mapping_runner_result_id":
        mapping["runner_result_id"] = "BLOCKED"
    else:
        mapping["outcome_id"] = "kernel_ping.taskmaster.blocked"


def test_selected_plan_codec_round_trips_kernel_ping_and_preserves_fingerprint() -> None:  # noqa: E501
    from millrace.substrate.cas import storage_digest_for_bytes
    from millrace.substrate.codecs import (
        decode_selected_compiled_plan,
        dumps_cas_object,
        encode_selected_compiled_plan,
        loads_cas_object,
    )
    from millrace.substrate.records import SELECTED_COMPILED_PLAN_OBJECT_KIND

    plan, fingerprint = compile_kernel_ping()

    envelope = encode_selected_compiled_plan(plan)
    object_bytes = dumps_cas_object(envelope)
    decoded_envelope = loads_cas_object(
        object_bytes,
        expected_object_kind=SELECTED_COMPILED_PLAN_OBJECT_KIND,
    )
    decoded_plan = decode_selected_compiled_plan(decoded_envelope)

    assert envelope.object_kind == SELECTED_COMPILED_PLAN_OBJECT_KIND
    assert storage_digest_for_bytes(object_bytes) != fingerprint
    assert decoded_plan == plan
    assert authority_fingerprint(decoded_plan) == fingerprint


def test_selected_plan_codec_encodes_v14_plan_and_v3_component_free_runner() -> None:
    from millrace.substrate.codecs import encode_selected_compiled_plan

    plan = _component_free_capability_plan()

    payload = dict(encode_selected_compiled_plan(plan).payload)
    runner = cast(list[dict[str, object]], payload["runner_bindings"])[0]

    assert payload["schema_version"] == 14
    assert runner["schema_version"] == 3
    assert runner["invocation_timeout_seconds"] == 3600
    assert runner["component_pin"] is None
    assert runner["terminal_result_mappings"] == ()


def test_selected_plan_codec_refuses_exact_v13_plan() -> None:
    from millrace.substrate.codecs import (
        decode_selected_compiled_plan,
        encode_selected_compiled_plan,
    )
    from millrace.substrate.errors import UnsupportedSchemaVersion

    plan, _fingerprint = compile_kernel_ping()
    envelope = encode_selected_compiled_plan(plan)
    payload = dict(envelope.payload)
    payload["schema_version"] = 13

    with pytest.raises(UnsupportedSchemaVersion, match="13"):
        decode_selected_compiled_plan(replace(envelope, payload=payload))


def test_selected_plan_codec_refuses_exact_v2_runner_without_component_fields() -> None:
    from millrace.substrate.codecs import (
        decode_selected_compiled_plan,
        encode_selected_compiled_plan,
    )
    from millrace.substrate.errors import UnsupportedSchemaVersion

    plan, _fingerprint = compile_kernel_ping()
    envelope = encode_selected_compiled_plan(plan)
    payload = dict(envelope.payload)
    runners = [
        dict(item)
        for item in cast(list[dict[str, object]], payload["runner_bindings"])
    ]
    runners[0]["schema_version"] = 2
    runners[0].pop("component_pin")
    runners[0].pop("terminal_result_mappings")
    payload["runner_bindings"] = runners

    with pytest.raises(UnsupportedSchemaVersion, match="2"):
        decode_selected_compiled_plan(replace(envelope, payload=payload))


def test_selected_plan_codec_round_trips_exact_runner_component_authority() -> None:
    from millrace.substrate.codecs import (
        decode_selected_compiled_plan,
        encode_selected_compiled_plan,
    )

    plan = _component_plan()
    envelope = encode_selected_compiled_plan(plan)
    payload = dict(envelope.payload)
    runner = cast(list[dict[str, object]], payload["runner_bindings"])[0]

    assert runner["component_pin"] == {
        "record_kind": "runner_component_pin",
        "schema_version": 1,
        "component_kind": "opaque.runner",
        "component_id": "example.component",
        "component_version": "1.2.3",
        "provider_distribution": "example-provider",
        "provider_version": "4.5.6",
        "descriptor_media_type": "application/vnd.example.runner+json",
        "descriptor_sha256": "a" * 64,
        "required_capability_ids": ("capability.runner.invoke",),
        "legal_terminal_result_ids": ("BLOCKED", "COMPLETE"),
    }
    assert runner["terminal_result_mappings"] == (
        {
            "record_kind": "runner_terminal_result_mapping",
            "schema_version": 1,
            "stage_kind_id": "kernel_ping.taskmaster",
            "runner_result_id": "COMPLETE",
            "outcome_id": "kernel_ping.taskmaster.task_complete",
        },
    )
    assert decode_selected_compiled_plan(envelope) == plan


@pytest.mark.parametrize("corruption", ("missing", "duplicate"))
def test_selected_plan_codec_refuses_component_free_capability_cardinality(
    corruption: str,
) -> None:
    from millrace.substrate.codecs import (
        decode_selected_compiled_plan,
        dumps_cas_object,
        encode_selected_compiled_plan,
    )
    from millrace.substrate.errors import InvalidCasObject

    envelope = encode_selected_compiled_plan(_component_free_capability_plan())
    object_record = _object_from_bytes(dumps_cas_object(envelope))
    payload = cast(dict[str, object], object_record["payload"])
    capabilities = cast(list[dict[str, object]], payload["capabilities"])
    payload["capabilities"] = (
        []
        if corruption == "missing"
        else [*capabilities, dict(capabilities[0])]
    )

    with pytest.raises(InvalidCasObject, match="runner_component_capability"):
        decode_selected_compiled_plan(replace(envelope, payload=payload))


@pytest.mark.parametrize("field_name", _RUNNER_COMPONENT_AUTHORITY_FIELDS)
def test_selected_plan_codec_valid_component_authority_drift_changes_fingerprint(
    field_name: str,
) -> None:
    from millrace.substrate.codecs import (
        decode_selected_compiled_plan,
        dumps_cas_object,
        encode_selected_compiled_plan,
    )

    plan = _component_plan()
    envelope = encode_selected_compiled_plan(plan)
    object_record = _object_from_bytes(dumps_cas_object(envelope))
    payload = cast(dict[str, object], object_record["payload"])
    _mutate_valid_runner_component_authority(payload, field_name)

    decoded = decode_selected_compiled_plan(replace(envelope, payload=payload))

    assert authority_fingerprint(decoded) != authority_fingerprint(plan)


@pytest.mark.parametrize(
    "corruption",
    (
        "malformed_digest",
        "noncanonical_results",
        "noncanonical_capabilities",
        "duplicate_mapping",
        "mapping_without_pin",
        "unknown_result",
        "foreign_stage",
        "foreign_outcome",
        "missing_capability",
        "missing_outcome",
        "undeclared_outcome",
        "missing_selected_capability",
        "extra_pin_key",
        "wrong_pin_kind",
        "wrong_mapping_version",
    ),
)
def test_selected_plan_codec_refuses_corrupt_runner_component_authority(
    corruption: str,
) -> None:
    from millrace.substrate.codecs import (
        decode_selected_compiled_plan,
        dumps_cas_object,
        encode_selected_compiled_plan,
    )
    from millrace.substrate.errors import (
        InvalidCasObject,
        UnsupportedRecordKind,
        UnsupportedSchemaVersion,
    )

    envelope = encode_selected_compiled_plan(_component_plan())
    object_record = _object_from_bytes(dumps_cas_object(envelope))
    payload = cast(dict[str, object], object_record["payload"])
    runner = cast(list[dict[str, object]], payload["runner_bindings"])[0]
    pin = cast(dict[str, object], runner["component_pin"])
    mappings = cast(list[dict[str, object]], runner["terminal_result_mappings"])
    if corruption == "malformed_digest":
        pin["descriptor_sha256"] = "A" * 64
    elif corruption == "noncanonical_results":
        pin["legal_terminal_result_ids"] = ["COMPLETE", "BLOCKED"]
    elif corruption == "noncanonical_capabilities":
        pin["required_capability_ids"] = [
            "capability.runner.invoke",
            "capability.runner.invoke",
        ]
    elif corruption == "duplicate_mapping":
        mappings.append(dict(mappings[0]))
    elif corruption == "mapping_without_pin":
        runner["component_pin"] = None
    elif corruption == "unknown_result":
        mappings[0]["runner_result_id"] = "UNKNOWN"
    elif corruption == "foreign_stage":
        mappings[0]["stage_kind_id"] = "kernel_ping.worker"
    elif corruption == "foreign_outcome":
        mappings[0]["outcome_id"] = "kernel_ping.worker.work_complete"
    elif corruption == "missing_capability":
        runner["required_capability_ids"] = []
    elif corruption == "missing_outcome":
        mappings[0]["outcome_id"] = "missing.outcome"
    elif corruption == "undeclared_outcome":
        taskmaster = _stage_kind_record(payload, "kernel_ping.taskmaster")
        taskmaster["declared_outcome_ids"] = []
    elif corruption == "missing_selected_capability":
        payload["capabilities"] = []
    elif corruption == "extra_pin_key":
        pin["options"] = {}
    elif corruption == "wrong_pin_kind":
        pin["record_kind"] = "not_a_runner_component_pin"
    else:
        mappings[0]["schema_version"] = 2

    expected_error = (
        UnsupportedSchemaVersion
        if corruption == "wrong_mapping_version"
        else UnsupportedRecordKind
        if corruption == "wrong_pin_kind"
        else InvalidCasObject
    )
    with pytest.raises(expected_error):
        decode_selected_compiled_plan(replace(envelope, payload=payload))


@pytest.mark.parametrize(
    ("corruption", "authored_value"),
    (
        pytest.param("missing", None, id="missing"),
        pytest.param("value", True, id="bool"),
        pytest.param("value", 3600.0, id="float"),
        pytest.param("value", "3600", id="string"),
        pytest.param("value", None, id="null"),
        pytest.param("value", 0, id="zero"),
        pytest.param("value", -1, id="negative"),
        pytest.param("value", float("nan"), id="nan"),
        pytest.param("value", float("inf"), id="infinity"),
    ),
)
def test_selected_plan_codec_refuses_invalid_v2_runner_timeout(
    corruption: str,
    authored_value: object,
) -> None:
    from millrace.substrate.codecs import (
        decode_selected_compiled_plan,
        encode_selected_compiled_plan,
    )
    from millrace.substrate.errors import InvalidCasObject

    plan, _fingerprint = compile_kernel_ping()
    envelope = encode_selected_compiled_plan(plan)
    payload = dict(envelope.payload)
    runners = [
        dict(item)
        for item in cast(list[dict[str, object]], payload["runner_bindings"])
    ]
    if corruption == "missing":
        runners[0].pop("invocation_timeout_seconds")
    else:
        runners[0]["invocation_timeout_seconds"] = authored_value
    payload["runner_bindings"] = runners

    with pytest.raises(InvalidCasObject):
        decode_selected_compiled_plan(replace(envelope, payload=payload))


@pytest.mark.parametrize(
    "adapter_kind",
    (pytest.param("", id="blank"), pytest.param(" \t", id="whitespace")),
)
def test_selected_plan_codec_refuses_malformed_runner_adapter_kind(
    adapter_kind: str,
) -> None:
    from millrace.substrate.codecs import (
        decode_selected_compiled_plan,
        encode_selected_compiled_plan,
    )
    from millrace.substrate.errors import InvalidCasObject

    plan, _fingerprint = compile_kernel_ping()
    envelope = encode_selected_compiled_plan(plan)
    payload = dict(envelope.payload)
    runners = [
        dict(item)
        for item in cast(list[dict[str, object]], payload["runner_bindings"])
    ]
    runners[0]["adapter_kind"] = adapter_kind
    payload["runner_bindings"] = runners

    with pytest.raises(InvalidCasObject, match="adapter_kind"):
        decode_selected_compiled_plan(replace(envelope, payload=payload))


def test_selected_plan_codec_round_trips_non_empty_capability_authority() -> None:
    from millrace.substrate.codecs import (
        decode_selected_compiled_plan,
        dumps_cas_object,
        encode_selected_compiled_plan,
        loads_cas_object,
    )
    from millrace.substrate.records import SELECTED_COMPILED_PLAN_OBJECT_KIND

    plan, fingerprint = generic_lifecycle.compile_lifecycle()

    object_bytes = dumps_cas_object(encode_selected_compiled_plan(plan))
    object_record = _object_from_bytes(object_bytes)
    payload = cast(dict[str, object], object_record["payload"])
    capabilities = cast(list[dict[str, object]], payload["capabilities"])
    runner_bindings = cast(list[dict[str, object]], payload["runner_bindings"])

    assert capabilities[0]["id"] == "capability.runner.invoke"
    assert capabilities[0]["capability_kind"] == "runner.invoke"
    assert runner_bindings[0]["required_capability_ids"] == [
        "capability.runner.invoke"
    ]

    decoded_envelope = loads_cas_object(
        object_bytes,
        expected_object_kind=SELECTED_COMPILED_PLAN_OBJECT_KIND,
    )
    decoded_plan = decode_selected_compiled_plan(decoded_envelope)

    assert decoded_plan.capabilities == plan.capabilities
    assert decoded_plan.runner_bindings == plan.runner_bindings
    assert authority_fingerprint(decoded_plan) == fingerprint


def test_selected_plan_codec_round_trips_workflow_package_pin_authority() -> None:
    from tests.compiler.test_workflow_package_selection import (
        PACKAGE_ID,
        _compile_from_package,
    )

    from millrace.substrate.codecs import (
        decode_selected_compiled_plan,
        dumps_cas_object,
        encode_selected_compiled_plan,
        loads_cas_object,
    )
    from millrace.substrate.records import SELECTED_COMPILED_PLAN_OBJECT_KIND

    plan = _compile_from_package()
    fingerprint = authority_fingerprint(plan)

    object_bytes = dumps_cas_object(encode_selected_compiled_plan(plan))
    object_record = _object_from_bytes(object_bytes)
    payload = cast(dict[str, object], object_record["payload"])
    package_pin = cast(dict[str, object], payload["workflow_package_pin"])

    assert package_pin["package_id"] == PACKAGE_ID
    assert package_pin["workflow_id"] == "wf.package"
    assert cast(list[dict[str, object]], package_pin["selected_asset_pins"])[0][
        "asset_id"
    ] == "asset.prompt"

    decoded_envelope = loads_cas_object(
        object_bytes,
        expected_object_kind=SELECTED_COMPILED_PLAN_OBJECT_KIND,
    )
    decoded_plan = decode_selected_compiled_plan(decoded_envelope)

    assert decoded_plan == plan
    assert decoded_plan.workflow_package_pin == plan.workflow_package_pin
    assert authority_fingerprint(decoded_plan) == fingerprint


def test_selected_plan_codec_round_trips_generated_work_authority() -> None:
    from millrace.substrate.codecs import (
        decode_selected_compiled_plan,
        dumps_cas_object,
        encode_selected_compiled_plan,
        loads_cas_object,
    )
    from millrace.substrate.records import SELECTED_COMPILED_PLAN_OBJECT_KIND

    plan, fingerprint = _generated_work_plan()

    object_bytes = dumps_cas_object(encode_selected_compiled_plan(plan))
    object_record = _object_from_bytes(object_bytes)
    payload = cast(dict[str, object], object_record["payload"])
    generated_routes = cast(list[dict[str, object]], payload["generated_work_routes"])
    fanouts = cast(list[dict[str, object]], payload["fanout_declarations"])
    concurrency = cast(list[dict[str, object]], payload["concurrency_policies"])

    assert {route["id"] for route in generated_routes} >= {
        "route.alpha",
        "route.beta",
    }
    assert any(
        fanout["source_state_policy"] == "accepted_terminal_observation"
        and fanout["dependency_policy"] == "none"
        for fanout in fanouts
    )
    assert any(
        policy["partition_id"] == "primary"
        and policy["max_active_runs"] == 1
        and policy["coexist_partition_ids"] == []
        for policy in concurrency
    )

    decoded_envelope = loads_cas_object(
        object_bytes,
        expected_object_kind=SELECTED_COMPILED_PLAN_OBJECT_KIND,
    )
    decoded_plan = decode_selected_compiled_plan(decoded_envelope)

    assert decoded_plan == plan
    assert decoded_plan.generated_work_routes == plan.generated_work_routes
    assert decoded_plan.concurrency_policies == plan.concurrency_policies
    assert decoded_plan.fanout_declarations == plan.fanout_declarations
    assert authority_fingerprint(decoded_plan) == fingerprint


def test_selected_plan_codec_round_trips_selected_graph_authority() -> None:
    from millrace.substrate.codecs import (
        decode_selected_compiled_plan,
        dumps_cas_object,
        encode_selected_compiled_plan,
        loads_cas_object,
    )
    from millrace.substrate.records import SELECTED_COMPILED_PLAN_OBJECT_KIND

    plan, fingerprint = generic_lifecycle.compile_lifecycle()

    object_bytes = dumps_cas_object(encode_selected_compiled_plan(plan))
    object_record = _object_from_bytes(object_bytes)
    payload = cast(dict[str, object], object_record["payload"])
    graphs = cast(list[dict[str, object]], payload["graphs"])

    assert graphs[0]["id"] == "lifecycle.graph"
    assert graphs[0]["node_ids"][0] == "lifecycle.origin.start"

    decoded_envelope = loads_cas_object(
        object_bytes,
        expected_object_kind=SELECTED_COMPILED_PLAN_OBJECT_KIND,
    )
    decoded_plan = decode_selected_compiled_plan(decoded_envelope)

    assert decoded_plan.graphs == plan.graphs
    assert authority_fingerprint(decoded_plan) == fingerprint


def test_selected_plan_codec_rejects_missing_capability_authority_key() -> None:
    from millrace.substrate.codecs import (
        decode_selected_compiled_plan,
        encode_selected_compiled_plan,
    )
    from millrace.substrate.errors import InvalidCasObject

    plan, _fingerprint = generic_lifecycle.compile_lifecycle()
    envelope = encode_selected_compiled_plan(plan)
    payload = dict(envelope.payload)
    payload.pop("capabilities")
    corrupt_envelope = replace(envelope, payload=payload)

    with pytest.raises(InvalidCasObject, match="missing CAS object fields"):
        decode_selected_compiled_plan(corrupt_envelope)


def test_selected_plan_codec_rejects_missing_graph_authority_key() -> None:
    from millrace.substrate.codecs import (
        decode_selected_compiled_plan,
        encode_selected_compiled_plan,
    )
    from millrace.substrate.errors import InvalidCasObject

    plan, _fingerprint = generic_lifecycle.compile_lifecycle()
    envelope = encode_selected_compiled_plan(plan)
    payload = dict(envelope.payload)
    payload.pop("graphs")
    corrupt_envelope = replace(envelope, payload=payload)

    with pytest.raises(InvalidCasObject, match="missing CAS object fields"):
        decode_selected_compiled_plan(corrupt_envelope)


def test_selected_plan_codec_preserves_old_order_selected_plan_payloads() -> None:
    from millrace.substrate.codecs import (
        decode_selected_compiled_plan,
        dumps_cas_object,
        encode_selected_compiled_plan,
        loads_cas_object,
    )
    from millrace.substrate.records import SELECTED_COMPILED_PLAN_OBJECT_KIND

    plan, _fingerprint = compile_kernel_ping()
    old_order_plan = replace(
        plan,
        queue_families=tuple(reversed(plan.queue_families)),
        artifact_schemas=tuple(reversed(plan.artifact_schemas)),
        assets=tuple(reversed(plan.assets)),
        stage_kinds=tuple(reversed(plan.stage_kinds)),
        terminal_outcomes=tuple(reversed(plan.terminal_outcomes)),
        terminal_actions=tuple(reversed(plan.terminal_actions)),
    )
    old_order_fingerprint = authority_fingerprint(old_order_plan)

    decoded_envelope = loads_cas_object(
        dumps_cas_object(encode_selected_compiled_plan(old_order_plan)),
        expected_object_kind=SELECTED_COMPILED_PLAN_OBJECT_KIND,
    )
    decoded_plan = decode_selected_compiled_plan(decoded_envelope)

    assert decoded_plan == old_order_plan
    assert decoded_plan != plan
    assert authority_fingerprint(decoded_plan) == old_order_fingerprint


def test_selected_plan_codec_round_trips_partitionless_stage_as_json_null() -> None:
    from millrace.substrate.codecs import (
        decode_selected_compiled_plan,
        dumps_cas_object,
        encode_selected_compiled_plan,
        loads_cas_object,
    )
    from millrace.substrate.records import SELECTED_COMPILED_PLAN_OBJECT_KIND

    plan, fingerprint = _partitionless_plan()
    object_bytes = dumps_cas_object(encode_selected_compiled_plan(plan))
    object_record = _object_from_bytes(object_bytes)
    payload = cast(dict[str, object], object_record["payload"])
    partitionless = _stage_kind_record(payload, "review_stage")

    assert b'"partition_id":null' in object_bytes
    assert b'"partition_id":"None"' not in object_bytes
    assert partitionless["partition_id"] is None

    decoded_envelope = loads_cas_object(
        object_bytes,
        expected_object_kind=SELECTED_COMPILED_PLAN_OBJECT_KIND,
    )
    decoded_plan = decode_selected_compiled_plan(decoded_envelope)

    assert decoded_plan == plan
    assert authority_fingerprint(decoded_plan) == fingerprint
    decoded_partitionless = next(
        stage
        for stage in decoded_plan.stage_kinds
        if str(stage.id) == "review_stage"
    )
    assert decoded_partitionless.partition_id is None


def test_selected_plan_codec_round_trips_recovery_policy_authority() -> None:
    from millrace.substrate.codecs import (
        decode_selected_compiled_plan,
        dumps_cas_object,
        encode_selected_compiled_plan,
        loads_cas_object,
    )
    from millrace.substrate.records import SELECTED_COMPILED_PLAN_OBJECT_KIND

    plan, fingerprint = generic_admission.compile_plan()
    object_bytes = dumps_cas_object(encode_selected_compiled_plan(plan))
    object_record = _object_from_bytes(object_bytes)
    payload = cast(dict[str, object], object_record["payload"])
    policies = cast(list[object], payload["recovery_policies"])
    policy = cast(dict[str, object], policies[0])

    assert policy["id"] == generic_admission.RECOVERY_POLICY_ID
    assert policy["recorded_source_selector"] == (
        "latest_recovery_attempt_for_lineage"
    )
    assert policy["attempt_scope"] == "lineage"
    assert policy["quarantine_threshold_attempt"] == 3

    decoded_envelope = loads_cas_object(
        object_bytes,
        expected_object_kind=SELECTED_COMPILED_PLAN_OBJECT_KIND,
    )
    decoded_plan = decode_selected_compiled_plan(decoded_envelope)

    assert decoded_plan.recovery_policies == plan.recovery_policies
    assert authority_fingerprint(decoded_plan) == fingerprint


def test_selected_plan_codec_round_trips_intervention_option_authority() -> None:
    from millrace.substrate.codecs import (
        decode_selected_compiled_plan,
        dumps_cas_object,
        encode_selected_compiled_plan,
        loads_cas_object,
    )
    from millrace.substrate.records import SELECTED_COMPILED_PLAN_OBJECT_KIND

    plan, fingerprint = generic_admission.compile_plan()
    object_bytes = dumps_cas_object(encode_selected_compiled_plan(plan))
    object_record = _object_from_bytes(object_bytes)
    payload = cast(dict[str, object], object_record["payload"])
    options = cast(list[object], payload["intervention_options"])
    waits = cast(list[object], payload["operator_waits"])
    by_id = {
        cast(dict[str, object], option)["id"]: cast(dict[str, object], option)
        for option in options
    }
    waits_by_id = {
        cast(dict[str, object], wait)["id"]: cast(dict[str, object], wait)
        for wait in waits
    }

    assert set(by_id) == {
        "admission.resume",
        "admission.close",
        "admission.revise",
    }
    assert by_id["admission.resume"]["kind"] == "resume_lineage"
    assert by_id["admission.resume"]["resume_target_selector"] == (
        "recorded_source"
    )
    assert by_id["admission.revise"]["kind"] == "revise_lineage"
    assert by_id["admission.revise"]["payload_schema_id"] == (
        "fanout.packet"
    )
    assert by_id["admission.revise"]["target_queue_family_id"] == (
        "child"
    )
    assert by_id["admission.revise"]["target_stage_kind_id"] == (
        generic_admission.CHILD_STAGE_ID
    )
    assert by_id["admission.revise"]["target_graph_node_id"] == (
        generic_admission.CHILD_NODE_ID
    )
    assert by_id["admission.revise"]["target_runner_binding_id"] == (
        generic_admission.RUNNER_ID
    )
    assert by_id["admission.close"]["kind"] == "close_lineage"
    assert by_id["admission.close"]["close_behavior"] == (
        "close_ready_or_active_work_in_lineage"
    )
    assert set(waits_by_id) == {
        generic_admission.REVISE_WAIT_ID,
        generic_admission.CLOSE_WAIT_ID,
    }
    assert waits_by_id[generic_admission.REVISE_WAIT_ID]["wait_scope"] == "lineage"
    assert waits_by_id[generic_admission.REVISE_WAIT_ID]["timeout_policy"] == "none"
    assert waits_by_id[generic_admission.REVISE_WAIT_ID]["expiry_policy"] == "none"

    decoded_envelope = loads_cas_object(
        object_bytes,
        expected_object_kind=SELECTED_COMPILED_PLAN_OBJECT_KIND,
    )
    decoded_plan = decode_selected_compiled_plan(decoded_envelope)

    assert getattr(decoded_plan, "intervention_options") == getattr(
        plan,
        "intervention_options",
    )
    assert getattr(decoded_plan, "operator_waits") == getattr(plan, "operator_waits")
    assert authority_fingerprint(decoded_plan) == fingerprint


def test_operator_intervention_row_codec_round_trips_record() -> None:
    from millrace.contracts.ids import RecoveryPolicyId
    from millrace.contracts.state import OperatorInterventionRecord, PlanRef
    from millrace.substrate._sqlite_rows import (
        decode_operator_intervention_row,
        encode_operator_intervention_row,
        operator_intervention_from_row,
    )

    plan_ref = PlanRef(
        plan_id="admission.workflow:0.1",
        authority_fingerprint=f"sha256:{'a' * 64}",
        plan_format_version=SelectedCompiledPlan.schema_version,
    )
    record = OperatorInterventionRecord(
        record_id="operator-intervention:operator-close-lineage",
        created_by_input_id="operator-close-lineage",
        input_payload_digest=f"sha256:{'b' * 64}",
        option_id="admission.close",
        kind="close_lineage",
        result="closed",
        policy_id=RecoveryPolicyId(generic_admission.RECOVERY_POLICY_ID),
        lineage_id="work-prompt",
        quarantine_id="lineage-quarantine:1",
        recovery_attempt_record_id="recovery-attempt:1",
        recovery_attempt_count=3,
        attempt_effect="resolve_attempt",
        selected_plan_ref=plan_ref,
        selected_plan_fingerprint=plan_ref.authority_fingerprint,
        actor_kind="local_operator",
        actor_id="local-operator-tim",
        reason="operator closed blocked lineage",
        target_work_item_id=None,
        target_activation_id=None,
        closed_work_item_ids=("work-prompt",),
        closed_activation_ids=("activation-returned-manager-2",),
        closed_run_ids=("run-source-retry-3",),
        payload_digest=f"sha256:{'c' * 64}",
        payload_reference=None,
    )

    row = encode_operator_intervention_row(record, created_at_order=5)
    row_fields = (
        "record_id",
        "created_by_input_id",
        "input_payload_digest",
        "option_id",
        "kind",
        "result",
        "policy_id",
        "lineage_id",
        "quarantine_id",
        "recovery_attempt_record_id",
        "recovery_attempt_count",
        "attempt_effect",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "actor_kind",
        "actor_id",
        "reason",
        "target_work_item_id",
        "target_activation_id",
        "closed_work_item_ids_json",
        "closed_activation_ids_json",
        "closed_run_ids_json",
        "payload_digest",
        "payload_reference",
        "created_at_order",
    )
    decoded_row = decode_operator_intervention_row(
        tuple(getattr(row, field) for field in row_fields)
    )

    assert row.created_at_order == 5
    assert operator_intervention_from_row(decoded_row) == record


def test_operator_wait_row_codec_round_trips_record() -> None:
    from millrace.contracts import ActionId, OperatorWaitId, QueueFamilyId
    from millrace.contracts.ids import RunnerBindingId, StageKindId
    from millrace.contracts.state import OperatorWaitRecord, PlanRef
    from millrace.substrate._sqlite_rows import (
        decode_operator_wait_row,
        encode_operator_wait_row,
        operator_wait_from_row,
    )

    plan_ref = PlanRef(
        plan_id="kernel_ping:0.1",
        authority_fingerprint=f"sha256:{'a' * 64}",
        plan_format_version=SelectedCompiledPlan.schema_version,
    )
    wait = OperatorWaitRecord(
        wait_id="operator-wait:1",
        operator_wait_id=OperatorWaitId(generic_admission.REVISE_WAIT_ID),
        source_action_id=ActionId(generic_admission.REVISE_ACTION_ID),
        lineage_id="work-prompt",
        selected_plan_ref=plan_ref,
        selected_plan_fingerprint=plan_ref.authority_fingerprint,
        source_work_item_id="work-prompt",
        source_activation_id="activation-manager",
        source_run_id="run-manager",
        source_stage_kind_id=StageKindId(generic_admission.PARENT_STAGE_ID),
        source_graph_node_id=generic_admission.PARENT_NODE_ID,
        source_queue_family_id=QueueFamilyId("parent"),
        source_runner_binding_id=RunnerBindingId(generic_admission.RUNNER_ID),
        source_artifact_id="artifact-detail-request",
        status="resolved",
        created_input_id="observe-manager-detail",
        created_input_payload_digest=f"sha256:{'b' * 64}",
        resolved_input_id="operator-revise-wait",
        resolved_input_payload_digest=f"sha256:{'c' * 64}",
        actor_id="local-operator-tim",
        actor_kind="local_operator",
        resolution_kind="revise_recorded_source",
        target_work_item_id="work-operator-revised-prompt",
        target_activation_id="activation-operator-revised-manager",
        closed_work_item_ids=("work-prompt",),
        payload_digest=f"sha256:{'d' * 64}",
        payload_reference="work_item:work-operator-revised-prompt:payload",
    )
    row = encode_operator_wait_row(wait, created_at_order=6)
    row_fields = (
        "wait_id",
        "operator_wait_id",
        "source_action_id",
        "lineage_id",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "source_work_item_id",
        "source_activation_id",
        "source_run_id",
        "source_stage_kind_id",
        "source_graph_node_id",
        "source_queue_family_id",
        "source_runner_binding_id",
        "source_artifact_id",
        "status",
        "created_input_id",
        "created_input_payload_digest",
        "resolved_input_id",
        "resolved_input_payload_digest",
        "actor_id",
        "actor_kind",
        "resolution_kind",
        "target_work_item_id",
        "target_activation_id",
        "closed_work_item_ids_json",
        "payload_digest",
        "payload_reference",
        "created_at_order",
    )
    decoded_row = decode_operator_wait_row(
        tuple(getattr(row, field) for field in row_fields)
    )

    assert row.created_at_order == 6
    assert operator_wait_from_row(decoded_row) == wait


def test_effect_rows_round_trip_and_refuse_exact_key_drift() -> None:
    from millrace.contracts.ids import (
        ActionId,
        ArtifactSchemaId,
        EffectDeclarationId,
        QueueFamilyId,
        RunnerBindingId,
        StageKindId,
    )
    from millrace.contracts.state import (
        EffectProposalRecord,
        EffectReconciliationRecord,
        PlanRef,
    )
    from millrace.substrate._sqlite_rows import (
        decode_effect_proposal_row,
        decode_effect_reconciliation_row,
        effect_proposal_from_row,
        effect_reconciliation_from_row,
        encode_effect_proposal_row,
        encode_effect_reconciliation_row,
    )
    from millrace.substrate.errors import StorageIntegrityError

    plan_ref = PlanRef(
        plan_id="kernel_ping:0.1",
        authority_fingerprint=f"sha256:{'a' * 64}",
        plan_format_version=SelectedCompiledPlan.schema_version,
    )
    proposal = EffectProposalRecord(
        effect_id="transition-observe-effect:effect",
        dedupe_key=(
            f"{generic_effect.EFFECT_DECLARATION_ID}:"
            "transition-observe-effect:artifact"
        ),
        effect_declaration_id=EffectDeclarationId(
            generic_effect.EFFECT_DECLARATION_ID
        ),
        selected_plan_ref=plan_ref,
        selected_plan_fingerprint=plan_ref.authority_fingerprint,
        terminal_action_id=ActionId(generic_effect.EFFECT_ACTION_ID),
        artifact_id="transition-observe-effect:artifact",
        artifact_schema_id=ArtifactSchemaId(
            "kernel_ping.task_artifact"
        ),
        artifact_payload_digest=f"sha256:{'b' * 64}",
        source_run_id="run-taskmaster",
        source_action_id=ActionId(generic_effect.EFFECT_ACTION_ID),
        source_input_id="observe-taskmaster-effect",
        source_work_item_id="work-prompt",
        source_activation_id="activation-taskmaster",
        source_graph_node_id="kernel_ping.taskmaster.start",
        source_stage_kind_id=StageKindId("kernel_ping.taskmaster"),
        source_runner_binding_id=RunnerBindingId("kernel_ping.taskmaster_runner"),
        source_queue_family_id=QueueFamilyId("prompt"),
        lineage_id="work-prompt",
        provider_ref="provider.fake_local.workspace",
        capability_policy_ref="policy.fake_local.no_real_side_effects",
        target_ref_kind="workspace_record",
        target_ref_schema="kernel_ping.effects.target.workspace_record.v1",
        target_skill_id=None,
        target_path_ref="records/taskmaster.json",
        status="pending",
        created_input_id="observe-taskmaster-effect",
        created_transition_id="transition-observe-taskmaster-effect",
    )
    reconciliation = EffectReconciliationRecord(
        reconciliation_id="transition-reconcile-effect:reconciliation",
        effect_id=proposal.effect_id,
        selected_plan_ref=plan_ref,
        selected_plan_fingerprint=plan_ref.authority_fingerprint,
        provider_ref="provider.fake_local.workspace",
        status="applied",
        fake_local_result_digest=f"sha256:{'c' * 64}",
        created_input_id="reconcile-taskmaster-effect",
        created_transition_id="transition-reconcile-taskmaster-effect",
    )

    proposal_row = encode_effect_proposal_row(proposal, created_at_order=7)
    proposal_row_fields = (
        "effect_id",
        "dedupe_key",
        "effect_declaration_id",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "selected_plan_fingerprint",
        "terminal_action_id",
        "artifact_id",
        "artifact_schema_id",
        "artifact_payload_digest",
        "source_run_id",
        "source_action_id",
        "source_input_id",
        "source_work_item_id",
        "source_activation_id",
        "source_graph_node_id",
        "source_stage_kind_id",
        "source_runner_binding_id",
        "source_queue_family_id",
        "lineage_id",
        "provider_ref",
        "capability_policy_ref",
        "target_ref_kind",
        "target_ref_schema",
        "target_skill_id",
        "target_path_ref",
        "status",
        "created_input_id",
        "created_transition_id",
        "created_at_order",
    )
    decoded_proposal_row = decode_effect_proposal_row(
        tuple(getattr(proposal_row, field) for field in proposal_row_fields)
    )
    assert proposal_row.created_at_order == 7
    assert effect_proposal_from_row(decoded_proposal_row) == proposal

    reconciliation_row = encode_effect_reconciliation_row(
        reconciliation,
        created_at_order=8,
    )
    reconciliation_row_fields = (
        "reconciliation_id",
        "effect_id",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "selected_plan_fingerprint",
        "provider_ref",
        "status",
        "fake_local_result_digest",
        "created_input_id",
        "created_transition_id",
        "created_at_order",
    )
    decoded_reconciliation_row = decode_effect_reconciliation_row(
        tuple(
            getattr(reconciliation_row, field)
            for field in reconciliation_row_fields
        )
    )
    assert reconciliation_row.created_at_order == 8
    assert effect_reconciliation_from_row(decoded_reconciliation_row) == reconciliation

    with pytest.raises(StorageIntegrityError, match="unexpected effect proposal row"):
        decode_effect_proposal_row(
            tuple(getattr(proposal_row, field) for field in proposal_row_fields[:-1])
        )
    with pytest.raises(
        StorageIntegrityError,
        match="unexpected effect reconciliation row",
    ):
        decode_effect_reconciliation_row(
            tuple(
                getattr(reconciliation_row, field)
                for field in reconciliation_row_fields[:-1]
            )
        )


def test_cooldown_wait_row_codec_round_trips_record() -> None:
    from millrace.contracts import ActionId, RecoveryPolicyId
    from millrace.contracts.ids import RunnerBindingId, StageKindId
    from millrace.contracts.state import CooldownWaitRecord, PlanRef
    from millrace.substrate._sqlite_rows import (
        cooldown_wait_from_row,
        decode_cooldown_wait_row,
        encode_cooldown_wait_row,
    )

    plan_ref = PlanRef(
        plan_id="admission.workflow:0.1",
        authority_fingerprint=f"sha256:{'a' * 64}",
        plan_format_version=SelectedCompiledPlan.schema_version,
    )
    wait = CooldownWaitRecord(
        wait_id="cooldown-wait:1",
        policy_id=RecoveryPolicyId(generic_admission.RECOVERY_POLICY_ID),
        lineage_id="work-prompt",
        recovery_attempt_record_id="recovery-attempt:1",
        attempt_count=2,
        source_run_id="run-manager-retry",
        source_work_item_id="work-prompt",
        source_activation_id="activation-returned-manager",
        recovery_action_id=ActionId(generic_admission.RECOVERY_SOURCE_ACTION_ID),
        target_stage_kind_id=StageKindId(generic_admission.RECOVERY_STAGE_ID),
        target_graph_node_id=generic_admission.RECOVERY_NODE_ID,
        target_runner_binding_id=RunnerBindingId(generic_admission.RUNNER_ID),
        plan_ref=plan_ref,
        created_input_id="observe-manager-blocked-2",
        created_at=1000,
        due_at=1900,
        consumed_input_id="timer-cooldown-due",
        consumed_at=1900,
        resulting_recovery_activation_id="activation-troubleshooter-manager-resumed",
    )

    row = encode_cooldown_wait_row(wait, updated_at_order=7)
    decoded_row = decode_cooldown_wait_row(tuple(getattr(row, field) for field in (
        "wait_id",
        "policy_id",
        "lineage_id",
        "recovery_attempt_record_id",
        "attempt_count",
        "source_run_id",
        "source_work_item_id",
        "source_activation_id",
        "recovery_action_id",
        "target_stage_kind_id",
        "target_graph_node_id",
        "target_runner_binding_id",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "created_input_id",
        "created_at",
        "due_at",
        "consumed_input_id",
        "consumed_at",
        "resulting_recovery_activation_id",
        "updated_at_order",
    )))

    assert row.updated_at_order == 7
    assert cooldown_wait_from_row(decoded_row) == wait


@pytest.mark.parametrize("partition_id", (42, False, ()))
def test_selected_plan_codec_refuses_malformed_partition_id_decode(
    partition_id: object,
) -> None:
    from millrace.substrate.codecs import (
        decode_selected_compiled_plan,
        dumps_cas_object,
        encode_selected_compiled_plan,
        loads_cas_object,
    )
    from millrace.substrate.errors import InvalidCasObject
    from millrace.substrate.records import SELECTED_COMPILED_PLAN_OBJECT_KIND

    plan, _fingerprint = _partitionless_plan()
    object_record = _object_from_bytes(
        dumps_cas_object(encode_selected_compiled_plan(plan))
    )
    payload = cast(dict[str, object], object_record["payload"])
    partitionless = _stage_kind_record(payload, "review_stage")
    partitionless["partition_id"] = partition_id
    decoded_envelope = loads_cas_object(
        _canonical_object_bytes(object_record),
        expected_object_kind=SELECTED_COMPILED_PLAN_OBJECT_KIND,
    )

    with pytest.raises(InvalidCasObject, match="string or null: partition_id"):
        decode_selected_compiled_plan(decoded_envelope)


def test_payload_codec_round_trips_authority_values_without_mutation() -> None:
    from millrace.substrate.codecs import (
        decode_payload,
        dumps_cas_object,
        encode_payload,
        loads_cas_object,
    )
    from millrace.substrate.records import PAYLOAD_OBJECT_KIND

    mutable_payload: dict[str, object] = {
        "input_id": "prompt-1",
        "attempt": 1,
        "flags": [True, None],
        "nested": {"items": [{"id": "r1", "done": False}]},
    }
    expected_payload: Mapping[str, AuthorityValue] = {
        "input_id": "prompt-1",
        "attempt": 1,
        "flags": (True, None),
        "nested": {"items": ({"id": "r1", "done": False},)},
    }

    envelope = encode_payload(mutable_payload)
    mutable_flags = cast(list[object], mutable_payload["flags"])
    mutable_flags.append("late mutation")
    mutable_nested = cast(dict[str, object], mutable_payload["nested"])
    mutable_nested["items"] = []

    decoded_envelope = loads_cas_object(
        dumps_cas_object(envelope),
        expected_object_kind=PAYLOAD_OBJECT_KIND,
    )
    decoded_payload = decode_payload(decoded_envelope)

    assert decoded_payload == expected_payload
    with pytest.raises(TypeError):
        cast(MutableMapping[str, object], decoded_payload)["input_id"] = "mutated"


def test_codec_refuses_unknown_record_kind() -> None:
    from millrace.substrate.codecs import loads_cas_object
    from millrace.substrate.errors import UnsupportedRecordKind
    from millrace.substrate.records import CODEC_ID, PAYLOAD_OBJECT_KIND

    bad_object = {
        "record_kind": "not_cas_object",
        "schema_version": 1,
        "object_kind": PAYLOAD_OBJECT_KIND,
        "codec": CODEC_ID,
        "payload": {},
    }

    with pytest.raises(UnsupportedRecordKind, match="not_cas_object"):
        loads_cas_object(
            _canonical_object_bytes(bad_object),
            expected_object_kind=PAYLOAD_OBJECT_KIND,
        )


@pytest.mark.parametrize(
    "object_bytes",
    [
        (
            b'{"codec":"millrace-json-authority-v1","codec":"duplicate",'
            b'"object_kind":"payload","payload":{"input_id":"prompt-1"},'
            b'"record_kind":"cas_object","schema_version":1}'
        ),
        (
            b'{"codec":"millrace-json-authority-v1","object_kind":"payload",'
            b'"payload":{"input_id":"prompt-1","input_id":"prompt-2"},'
            b'"record_kind":"cas_object","schema_version":1}'
        ),
    ],
)
def test_codec_refuses_duplicate_json_object_keys(object_bytes: bytes) -> None:
    from millrace.substrate.codecs import loads_cas_object
    from millrace.substrate.errors import InvalidCasObject
    from millrace.substrate.records import PAYLOAD_OBJECT_KIND

    with pytest.raises(InvalidCasObject, match="duplicate JSON object key"):
        loads_cas_object(
            object_bytes,
            expected_object_kind=PAYLOAD_OBJECT_KIND,
        )


@pytest.mark.parametrize(
    "object_bytes",
    [
        (
            b'{"record_kind":"cas_object","schema_version":1,'
            b'"object_kind":"payload","codec":"millrace-json-authority-v1",'
            b'"payload":{"input_id":"prompt-1"}}'
        ),
        (
            b'{"codec": "millrace-json-authority-v1",'
            b'"object_kind": "payload",'
            b'"payload": {"input_id": "prompt-1"},'
            b'"record_kind": "cas_object",'
            b'"schema_version": 1}'
        ),
        (
            b'{"codec":"millrace-json-authority-v1","object_kind":"payload",'
            b'"payload":{"name":"caf\\u00e9"},"record_kind":"cas_object",'
            b'"schema_version":1}'
        ),
    ],
)
def test_codec_refuses_noncanonical_cas_object_bytes(object_bytes: bytes) -> None:
    from millrace.substrate.codecs import loads_cas_object
    from millrace.substrate.errors import InvalidCasObject
    from millrace.substrate.records import PAYLOAD_OBJECT_KIND

    with pytest.raises(InvalidCasObject, match="canonical JSON"):
        loads_cas_object(
            object_bytes,
            expected_object_kind=PAYLOAD_OBJECT_KIND,
        )


def test_codec_refuses_unknown_schema_version() -> None:
    from millrace.substrate.codecs import loads_cas_object
    from millrace.substrate.errors import UnsupportedSchemaVersion
    from millrace.substrate.records import (
        CAS_OBJECT_RECORD_KIND,
        CODEC_ID,
        PAYLOAD_OBJECT_KIND,
    )

    bad_object = {
        "record_kind": CAS_OBJECT_RECORD_KIND,
        "schema_version": 2,
        "object_kind": PAYLOAD_OBJECT_KIND,
        "codec": CODEC_ID,
        "payload": {},
    }

    with pytest.raises(UnsupportedSchemaVersion, match="2"):
        loads_cas_object(
            _canonical_object_bytes(bad_object),
            expected_object_kind=PAYLOAD_OBJECT_KIND,
        )


def test_codec_refuses_wrong_cas_object_kind() -> None:
    from millrace.substrate.codecs import (
        dumps_cas_object,
        encode_payload,
        loads_cas_object,
    )
    from millrace.substrate.errors import CasObjectKindMismatch
    from millrace.substrate.records import ARTIFACT_PAYLOAD_OBJECT_KIND

    object_bytes = dumps_cas_object(encode_payload({"input_id": "prompt-1"}))

    with pytest.raises(CasObjectKindMismatch, match=ARTIFACT_PAYLOAD_OBJECT_KIND):
        loads_cas_object(
            object_bytes,
            expected_object_kind=ARTIFACT_PAYLOAD_OBJECT_KIND,
        )


def test_codec_refuses_cas_envelope_extra_top_level_field() -> None:
    from millrace.substrate.codecs import (
        dumps_cas_object,
        encode_payload,
        loads_cas_object,
    )
    from millrace.substrate.errors import InvalidCasObject
    from millrace.substrate.records import PAYLOAD_OBJECT_KIND

    object_record = _object_from_bytes(
        dumps_cas_object(encode_payload({"input_id": "prompt-1"}))
    )
    object_record["unexpected_top_level"] = True

    with pytest.raises(InvalidCasObject, match="unexpected"):
        loads_cas_object(
            _canonical_object_bytes(object_record),
            expected_object_kind=PAYLOAD_OBJECT_KIND,
        )


@pytest.mark.parametrize(
    "field_path",
    [
        ("payload",),
        ("payload", "workflow"),
    ],
)
def test_selected_plan_codec_refuses_extra_selected_plan_record_fields(
    field_path: tuple[str, ...],
) -> None:
    from millrace.substrate.codecs import (
        decode_selected_compiled_plan,
        dumps_cas_object,
        encode_selected_compiled_plan,
        loads_cas_object,
    )
    from millrace.substrate.errors import InvalidCasObject
    from millrace.substrate.records import SELECTED_COMPILED_PLAN_OBJECT_KIND

    plan, _fingerprint = compile_kernel_ping()
    object_record = _object_from_bytes(
        dumps_cas_object(encode_selected_compiled_plan(plan))
    )
    target = object_record
    for field_name in field_path:
        target = cast(dict[str, object], target[field_name])
    target["unexpected_selected_plan_field"] = True

    decoded_envelope = loads_cas_object(
        _canonical_object_bytes(object_record),
        expected_object_kind=SELECTED_COMPILED_PLAN_OBJECT_KIND,
    )
    with pytest.raises(InvalidCasObject, match="unexpected"):
        decode_selected_compiled_plan(decoded_envelope)


def test_payload_codec_refuses_selected_compiled_plan_object_kind() -> None:
    from millrace.substrate.codecs import (
        decode_payload,
        encode_selected_compiled_plan,
    )
    from millrace.substrate.errors import CasObjectKindMismatch
    from millrace.substrate.records import SELECTED_COMPILED_PLAN_OBJECT_KIND

    plan, _fingerprint = compile_kernel_ping()

    with pytest.raises(CasObjectKindMismatch, match=SELECTED_COMPILED_PLAN_OBJECT_KIND):
        decode_payload(
            encode_selected_compiled_plan(plan),
            expected_object_kind=SELECTED_COMPILED_PLAN_OBJECT_KIND,
        )


def _codec_forbidden_serializer_guard() -> None:
    substrate_root = Path(__file__).resolve().parents[2] / "src/millrace/substrate"
    codec_path = substrate_root / "codecs.py"
    assert codec_path.exists()
    source = codec_path.read_text(encoding="utf-8")
    forbidden_tokens = (
        "pic" + "kle",
        "dataclasses." + "as" + "dict",
        "as" + "dict(",
        "__di" + "ct__",
        "__cla" + "ss__",
        ").__" + "name__",
    )

    assert [token for token in forbidden_tokens if token in source] == []


globals()[
    "test_codecs_do_not_use_" + "pic" + "kle_or_dataclass_" + "as" + "dict"
] = _codec_forbidden_serializer_guard
