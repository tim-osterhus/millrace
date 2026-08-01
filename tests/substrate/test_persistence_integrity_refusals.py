from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

import millrace.operator as operator_api
from kernel.kernel_ping_scenarios import bootstrap_to_worker_claim
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts import QueueFamilyId
from millrace.contracts.compiled_plan import SelectedCompiledPlan
from millrace.contracts.state import (
    DURABLE_INT64_MAX,
    RUNNER_SESSION_TEXT_MAX_BYTES,
    AdmittedPlan,
    DaemonBudgetEpochRecord,
    PlanRef,
    RunnerSessionUsageRecord,
    RuntimeState,
)
from millrace.contracts.transition import (
    AdmitPlan,
    CancelQueuedWork,
    ClaimWork,
    EnqueueWork,
    InitializeWorkspace,
    OperatorResumeWait,
    OperatorReviseWait,
    ResumeDispatch,
    RunnerResultObserved,
    SelectDefaultPlan,
    SuspendDispatch,
    TimerDue,
    input_payload_digest,
)
from millrace.kernel import apply, empty_runtime_state
from millrace.kernel.lookups import external_enqueue_routes, plan_ref_for
from millrace.substrate._sqlite_relations import validate_loaded_runtime_state
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.codecs import (
    decode_payload,
    decode_selected_compiled_plan,
    dumps_cas_object,
    encode_payload,
    encode_selected_compiled_plan,
    loads_cas_object,
)
from millrace.substrate.errors import StorageIntegrityError
from millrace.substrate.records import (
    PAYLOAD_OBJECT_KIND,
    SELECTED_COMPILED_PLAN_OBJECT_KIND,
)
from millrace.substrate.sqlite import SQLiteRuntimeStore
from millrace.testing import (
    decide_with_fake_runner_completion as decide,
)
from millrace.testing import (
    deterministic_context,
    fake_runner_completion_input_id,
    fake_runner_observation_payload,
    materialize_fake_runner_session_cas,
)
from millrace.workflows import kernel_ping
from substrate._runtime_store_support import (
    load_runtime_state,
    persist_and_load_runtime_state,
    persist_runtime_state,
    persist_taskmaster_runtime_state,
    persist_worker_runtime_state,
    runtime_store_paths,
    taskmaster_runtime_state,
    worker_runtime_state,
)
from support import (
    generic_admission,
    generic_fanout,
    generic_lifecycle,
)
from support import (
    kernel_ping as kernel_ping_support,
)


def _component_source() -> dict[str, object]:
    source = generic_lifecycle.source()
    origin_stage = next(
        record
        for record in cast(list[dict[str, object]], source["stage_kinds"])
        if record["id"] == "origin_stage"
    )
    origin_stage["declared_outcome_ids"] = (
        *cast(tuple[str, ...], origin_stage["declared_outcome_ids"]),
        "lifecycle.origin.blocked",
    )
    cast(list[dict[str, object]], source["terminal_outcomes"]).append(
        {
            "id": "lifecycle.origin.blocked",
            "stage_kind_id": "origin_stage",
            "marker": "SOURCE_BLOCKED",
        }
    )
    cast(list[dict[str, object]], source["terminal_actions"]).append(
        {
            "id": "lifecycle.origin.block",
            "stage_kind_id": "origin_stage",
            "outcome_id": "lifecycle.origin.blocked",
            "kind": "complete_work_item",
            "artifact_schema_id": generic_lifecycle.SOURCE_SCHEMA_ID,
        }
    )
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
                "origin_stage",
                "alpha_stage",
                "beta_stage",
                "review_stage",
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
                    "stage_kind_id": "origin_stage",
                    "runner_result_id": "COMPLETE",
                    "outcome_id": "lifecycle.origin.ready",
                },
            ),
        }
    )
    return source


_MILLFORGE_DESCRIPTOR_SHA256 = (
    "0bace7b27871b03cd7ffe59951953348b3da3214536178d6f447a21de4403464"
)
_MILLFORGE_PLAN_FINGERPRINT = (
    "sha256:29d40efa187bef7c2ad2a143f8a685a6f6dbb21dcfdf05258b50c1c1c2586d42"
)


def _millforge_component_source() -> dict[str, object]:
    return kernel_ping.workflow_source()


def _durable_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _assert_corrupt_millforge_selected_plan_refuses_load(
    tmp_path: Path,
    mutate: Callable[[dict[str, object]], None],
) -> None:
    plan, fingerprint = kernel_ping_support.compile_kernel_ping(
        _millforge_component_source()
    )
    assert fingerprint == _MILLFORGE_PLAN_FINGERPRINT
    state = bootstrap_to_worker_claim(plan, fingerprint)
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    selected_plan_record = json.loads(
        dumps_cas_object(encode_selected_compiled_plan(plan)).decode("utf-8")
    )
    payload = cast(dict[str, object], selected_plan_record["payload"])
    mutate(payload)
    corrupt_bytes = json.dumps(
        selected_plan_record,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    corrupt_envelope = loads_cas_object(
        corrupt_bytes,
        expected_object_kind=SELECTED_COMPILED_PLAN_OBJECT_KIND,
    )
    corrupt_plan = decode_selected_compiled_plan(corrupt_envelope)
    assert authority_fingerprint(corrupt_plan) != fingerprint
    corrupt_digest = ContentAddressedByteStore(cas_root).put_bytes(corrupt_bytes)
    _replace_selected_plan_digest(db_path, corrupt_digest)
    durable_before = _durable_bytes(tmp_path)

    with pytest.raises(StorageIntegrityError, match="authority fingerprint"):
        load_runtime_state(db_path, cas_root)

    assert _durable_bytes(tmp_path) == durable_before


def _component_free_capability_source() -> dict[str, object]:
    source = generic_lifecycle.source()
    source["capabilities"] = [
        {
            "id": "capability.runner.invoke",
            "kind": "runner.invoke",
            "support_status": "supported",
            "grant_status": "granted",
        }
    ]
    for runner in cast(list[dict[str, object]], source["runner_bindings"]):
        runner["adapter_kind"] = "codex"
        runner["required_capability_ids"] = ("capability.runner.invoke",)
        runner.pop("component_pin", None)
        runner.pop("terminal_result_mappings", None)
    return source


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
        mapping["stage_kind_id"] = "alpha_stage"
        mapping["outcome_id"] = "lifecycle.alpha.ready"
    elif field_name == "mapping_runner_result_id":
        mapping["runner_result_id"] = "BLOCKED"
    else:
        mapping["outcome_id"] = "lifecycle.origin.blocked"


def _selected_stage_record(
    payload: dict[str, object],
    stage_kind_id: str,
) -> dict[str, object]:
    return next(
        stage
        for stage in cast(list[dict[str, object]], payload["stage_kinds"])
        if stage["id"] == stage_kind_id
    )


@pytest.mark.parametrize(
    "source_state_policy",
    ("accepted_terminal_observation", "source_closed"),
)
def test_restart_refuses_fanout_aftermath_for_absent_optional_collection(
    tmp_path: Path,
    source_state_policy: str,
) -> None:
    populated, _plan, _fingerprint = (
        generic_lifecycle.optional_collection_with_fanout_aftermath_state(
            source_state_policy=source_state_policy,
        )
    )
    zero_item, _plan, _fingerprint = (
        generic_lifecycle.accepted_terminal_optional_collection_omission_state()
        if source_state_policy == "accepted_terminal_observation"
        else generic_lifecycle.source_closed_optional_collection_omission_state()
    )
    transition_by_id = {item.record_id: item for item in populated.transitions}
    transition_by_id.update({item.record_id: item for item in zero_item.transitions})
    event_by_id = {item.record_id: item for item in populated.governance_events}
    event_by_id.update({item.record_id: item for item in zero_item.governance_events})
    trace_by_id = {item.record_id: item for item in populated.traces}
    trace_by_id.update({item.record_id: item for item in zero_item.traces})
    route_by_id = {item.record_id: item for item in populated.activation_routes}
    route_by_id.update({item.record_id: item for item in zero_item.activation_routes})
    source_artifact = zero_item.artifacts[generic_lifecycle.source_artifact_id()]
    fanout_records = {
        record_id: replace(
            record,
            source_artifact_digest=source_artifact.payload_digest,
        )
        for record_id, record in populated.fanout_records.items()
    }
    drifted = replace(
        zero_item,
        receipts={**populated.receipts, **zero_item.receipts},
        work_items=populated.work_items,
        activations=populated.activations,
        activation_routes=tuple(route_by_id.values()),
        fanout_records=fanout_records,
        work_dependencies=populated.work_dependencies,
        governance_events=tuple(event_by_id.values()),
        traces=tuple(trace_by_id.values()),
        transitions=tuple(transition_by_id.values()),
    )
    db_path, cas_root = runtime_store_paths(tmp_path)

    with pytest.raises(
        StorageIntegrityError,
        match="fanout_records item_key must reference selected source item",
    ):
        persist_runtime_state(db_path, cas_root, drifted)


def test_restart_refuses_missing_selected_plan_cas_object(tmp_path: Path) -> None:
    db_path, cas_root, _state = persist_taskmaster_runtime_state(tmp_path)
    missing_digest = f"sha256:{'0' * 64}"
    _replace_selected_plan_digest(db_path, missing_digest)

    with pytest.raises(
        StorageIntegrityError,
        match="admitted plan selected_plan_digest references missing CAS object",
    ):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize("old_record", ("selected_plan_v13", "runner_binding_v2"))
def test_restart_refuses_pre_component_selected_authority_versions(
    tmp_path: Path,
    old_record: str,
) -> None:
    db_path, cas_root, state = persist_taskmaster_runtime_state(tmp_path)
    plan = next(iter(state.admitted_plans.values())).selected_plan
    selected_plan_record = json.loads(
        dumps_cas_object(encode_selected_compiled_plan(plan)).decode("utf-8")
    )
    payload = cast(dict[str, object], selected_plan_record["payload"])
    if old_record == "selected_plan_v13":
        payload["schema_version"] = 13
    else:
        runner = cast(list[dict[str, object]], payload["runner_bindings"])[0]
        runner["schema_version"] = 2
        runner.pop("component_pin")
        runner.pop("terminal_result_mappings")
    old_bytes = json.dumps(
        selected_plan_record,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    old_digest = ContentAddressedByteStore(cas_root).put_bytes(old_bytes)
    _replace_selected_plan_digest(db_path, old_digest)

    with pytest.raises(
        StorageIntegrityError,
        match="selected_plan_digest references malformed CAS object",
    ):
        load_runtime_state(db_path, cas_root)


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
def test_restart_refuses_invalid_current_runner_timeout(
    tmp_path: Path,
    corruption: str,
    authored_value: object,
) -> None:
    db_path, cas_root, state = persist_taskmaster_runtime_state(tmp_path)
    plan = next(iter(state.admitted_plans.values())).selected_plan
    selected_plan_record = json.loads(
        dumps_cas_object(encode_selected_compiled_plan(plan)).decode("utf-8")
    )
    payload = cast(dict[str, object], selected_plan_record["payload"])
    runner = cast(list[dict[str, object]], payload["runner_bindings"])[0]
    if corruption == "missing":
        runner.pop("invocation_timeout_seconds")
    else:
        runner["invocation_timeout_seconds"] = authored_value
    corrupt_bytes = json.dumps(
        selected_plan_record,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    corrupt_digest = ContentAddressedByteStore(cas_root).put_bytes(corrupt_bytes)
    _replace_selected_plan_digest(db_path, corrupt_digest)

    with pytest.raises(
        StorageIntegrityError,
        match="selected_plan_digest references malformed CAS object",
    ):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    "adapter_kind",
    (pytest.param("", id="blank"), pytest.param(" \t", id="whitespace")),
)
def test_restart_refuses_malformed_runner_adapter_kind(
    tmp_path: Path,
    adapter_kind: str,
) -> None:
    db_path, cas_root, state = persist_taskmaster_runtime_state(tmp_path)
    plan = next(iter(state.admitted_plans.values())).selected_plan
    selected_plan_record = json.loads(
        dumps_cas_object(encode_selected_compiled_plan(plan)).decode("utf-8")
    )
    payload = cast(dict[str, object], selected_plan_record["payload"])
    runner = cast(list[dict[str, object]], payload["runner_bindings"])[0]
    runner["adapter_kind"] = adapter_kind
    corrupt_bytes = json.dumps(
        selected_plan_record,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    corrupt_digest = ContentAddressedByteStore(cas_root).put_bytes(corrupt_bytes)
    _replace_selected_plan_digest(db_path, corrupt_digest)

    with pytest.raises(
        StorageIntegrityError,
        match="selected_plan_digest references malformed CAS object",
    ):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    "corruption",
    (
        "digest",
        "mapping_stage",
        "mapping_outcome",
        "capability_closure",
        "noncanonical_capabilities",
        "noncanonical_results",
        "missing_outcome",
        "undeclared_outcome",
        "missing_selected_capability",
    ),
)
def test_restart_refuses_corrupt_runner_component_authority(
    tmp_path: Path,
    corruption: str,
) -> None:
    plan, fingerprint = generic_lifecycle.compile_lifecycle(_component_source())
    state = _generic_admitted_only_state(plan, fingerprint)
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    selected_plan_record = json.loads(
        dumps_cas_object(encode_selected_compiled_plan(plan)).decode("utf-8")
    )
    payload = cast(dict[str, object], selected_plan_record["payload"])
    runner = cast(list[dict[str, object]], payload["runner_bindings"])[0]
    pin = cast(dict[str, object], runner["component_pin"])
    mapping = cast(list[dict[str, object]], runner["terminal_result_mappings"])[0]
    if corruption == "digest":
        pin["descriptor_sha256"] = "A" * 64
    elif corruption == "mapping_stage":
        mapping["stage_kind_id"] = "alpha_stage"
    elif corruption == "mapping_outcome":
        mapping["outcome_id"] = "lifecycle.alpha.ready"
    elif corruption == "capability_closure":
        runner["required_capability_ids"] = []
    elif corruption == "noncanonical_capabilities":
        pin["required_capability_ids"] = [
            "capability.runner.invoke",
            "capability.runner.invoke",
        ]
    elif corruption == "noncanonical_results":
        pin["legal_terminal_result_ids"] = ["COMPLETE", "BLOCKED"]
    elif corruption == "missing_outcome":
        mapping["outcome_id"] = "missing.outcome"
    elif corruption == "undeclared_outcome":
        origin = _selected_stage_record(payload, "origin_stage")
        origin["declared_outcome_ids"] = []
    else:
        payload["capabilities"] = []
    corrupt_bytes = json.dumps(
        selected_plan_record,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    corrupt_digest = ContentAddressedByteStore(cas_root).put_bytes(corrupt_bytes)
    _replace_selected_plan_digest(db_path, corrupt_digest)

    with pytest.raises(
        StorageIntegrityError,
        match="selected_plan_digest references malformed CAS object",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_corrupt_millforge_component_pin_before_adapter_resolution(
    tmp_path: Path,
) -> None:
    def corrupt_pin(payload: dict[str, object]) -> None:
        runner = cast(list[dict[str, object]], payload["runner_bindings"])[0]
        pin = cast(dict[str, object], runner["component_pin"])
        assert pin["descriptor_sha256"] == _MILLFORGE_DESCRIPTOR_SHA256
        pin["descriptor_sha256"] = "b" * 64

    _assert_corrupt_millforge_selected_plan_refuses_load(tmp_path, corrupt_pin)


def test_restart_refuses_corrupt_millforge_mapping_before_provider_work(
    tmp_path: Path,
) -> None:
    def corrupt_mappings(payload: dict[str, object]) -> None:
        runner = cast(list[dict[str, object]], payload["runner_bindings"])[0]
        mappings = cast(list[dict[str, object]], runner["terminal_result_mappings"])
        by_result = {
            cast(str, mapping["runner_result_id"]): mapping for mapping in mappings
        }
        assert {
            result_id: mapping["outcome_id"] for result_id, mapping in by_result.items()
        } == {
            "BLOCKED": "kernel_ping.taskmaster.blocked",
            "TASK_COMPLETE": "kernel_ping.taskmaster.task_complete",
        }
        by_result["BLOCKED"]["outcome_id"] = "kernel_ping.taskmaster.task_complete"
        by_result["TASK_COMPLETE"]["outcome_id"] = "kernel_ping.taskmaster.blocked"

    _assert_corrupt_millforge_selected_plan_refuses_load(
        tmp_path,
        corrupt_mappings,
    )


@pytest.mark.parametrize("corruption", ("missing", "duplicate"))
def test_restart_refuses_component_free_capability_cardinality(
    tmp_path: Path,
    corruption: str,
) -> None:
    plan, fingerprint = generic_lifecycle.compile_lifecycle(
        _component_free_capability_source()
    )
    assert plan.runner_bindings[0].component_pin is None
    state = _generic_admitted_only_state(plan, fingerprint)
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    selected_plan_record = json.loads(
        dumps_cas_object(encode_selected_compiled_plan(plan)).decode("utf-8")
    )
    payload = cast(dict[str, object], selected_plan_record["payload"])
    capabilities = cast(list[dict[str, object]], payload["capabilities"])
    payload["capabilities"] = (
        [] if corruption == "missing" else [*capabilities, dict(capabilities[0])]
    )
    corrupt_bytes = json.dumps(
        selected_plan_record,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    corrupt_digest = ContentAddressedByteStore(cas_root).put_bytes(corrupt_bytes)
    _replace_selected_plan_digest(db_path, corrupt_digest)

    with pytest.raises(
        StorageIntegrityError,
        match="selected_plan_digest references malformed CAS object",
    ):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize("field_name", _RUNNER_COMPONENT_AUTHORITY_FIELDS)
def test_restart_refuses_valid_component_authority_drift_against_stale_pin(
    tmp_path: Path,
    field_name: str,
) -> None:
    plan, fingerprint = generic_lifecycle.compile_lifecycle(_component_source())
    state = _generic_admitted_only_state(plan, fingerprint)
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    selected_plan_record = json.loads(
        dumps_cas_object(encode_selected_compiled_plan(plan)).decode("utf-8")
    )
    payload = cast(dict[str, object], selected_plan_record["payload"])
    _mutate_valid_runner_component_authority(payload, field_name)
    changed_bytes = json.dumps(
        selected_plan_record,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    changed_envelope = loads_cas_object(
        changed_bytes,
        expected_object_kind=SELECTED_COMPILED_PLAN_OBJECT_KIND,
    )
    changed_plan = decode_selected_compiled_plan(changed_envelope)
    assert authority_fingerprint(changed_plan) != fingerprint
    changed_digest = ContentAddressedByteStore(cas_root).put_bytes(changed_bytes)
    _replace_selected_plan_digest(db_path, changed_digest)

    with pytest.raises(StorageIntegrityError, match="authority fingerprint"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_selected_plan_authority_fingerprint_mismatch(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = persist_taskmaster_runtime_state(tmp_path)
    wrong_fingerprint = f"sha256:{'f' * 64}"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE admitted_plan_pins SET authority_fingerprint = ?",
            (wrong_fingerprint,),
        )
        connection.execute(
            "UPDATE default_plan SET authority_fingerprint = ?",
            (wrong_fingerprint,),
        )

    with pytest.raises(
        StorageIntegrityError,
        match="selected plan authority fingerprint mismatch",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_default_plan_pin_mismatch(tmp_path: Path) -> None:
    db_path, cas_root, _state = persist_taskmaster_runtime_state(tmp_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE default_plan SET plan_id = ?",
            ("corrupt-plan-id",),
        )

    with pytest.raises(
        StorageIntegrityError,
        match="default_plan PlanRef must match admitted plan pin",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_default_plan_digest_mismatch(tmp_path: Path) -> None:
    db_path, cas_root, _state = persist_taskmaster_runtime_state(tmp_path)
    wrong_digest = f"sha256:{'1' * 64}"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE default_plan SET selected_plan_digest = ?",
            (wrong_digest,),
        )

    with pytest.raises(
        StorageIntegrityError,
        match="default_plan.selected_plan_digest must match admitted plan pin",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_admitted_plan_pin_mismatching_selected_plan_identity(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    corrupted_plan_id = "kernel_ping:0.1:corrupt"
    for table_name, column_name in (
        ("admitted_plan_pins", "plan_id"),
        ("default_plan", "plan_id"),
        ("work_items", "plan_id"),
        ("activations", "plan_id"),
        ("runs", "plan_id"),
    ):
        _disable_checks_and_execute(
            db_path,
            f"UPDATE {table_name} SET {column_name} = ?",
            (corrupted_plan_id,),
        )

    with pytest.raises(
        StorageIntegrityError,
        match="admitted_plan_pins PlanRef must match selected plan",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_missing_payload_cas_object_for_work_item(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = persist_taskmaster_runtime_state(tmp_path)
    payload_digest = _single_text_value(
        db_path,
        "SELECT payload_digest FROM work_items LIMIT 1",
    )
    _delete_cas_object(cas_root, payload_digest)

    with pytest.raises(
        StorageIntegrityError,
        match="work item payload_digest references missing CAS object",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_missing_artifact_payload_cas_object(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    payload_digest = _single_text_value(
        db_path,
        "SELECT payload_digest FROM artifacts LIMIT 1",
    )
    _delete_cas_object(cas_root, payload_digest)

    with pytest.raises(
        StorageIntegrityError,
        match="artifact payload_digest references missing CAS object",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_wrong_kind_cas_object_reference(tmp_path: Path) -> None:
    db_path, cas_root, _state = persist_taskmaster_runtime_state(tmp_path)
    wrong_kind_digest = ContentAddressedByteStore(cas_root).put_bytes(
        dumps_cas_object(encode_payload({"not": "a selected plan"}))
    )
    _replace_selected_plan_digest(db_path, wrong_kind_digest)

    with pytest.raises(
        StorageIntegrityError,
        match="admitted plan selected_plan_digest references wrong CAS object kind",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_corrupt_selected_join_declaration(tmp_path: Path) -> None:
    db_path, cas_root = _persist_generic_join_with_corrupt_join(
        tmp_path,
        lambda join: join.__setitem__("required_artifact_schema_ids", []),
    )

    with pytest.raises(
        StorageIntegrityError,
        match="selected join declaration is invalid",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_selected_join_correlation_key_schema_mismatch(
    tmp_path: Path,
) -> None:
    db_path, cas_root = _persist_generic_join_with_corrupt_join(
        tmp_path,
        lambda join: join.__setitem__("correlation_key", "missing_key"),
    )

    with pytest.raises(
        StorageIntegrityError,
        match="selected join declaration is invalid",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_selected_join_without_unique_generated_target_route(
    tmp_path: Path,
) -> None:
    def remove_award_route(payload: dict[str, object]) -> None:
        routes = cast(list[dict[str, object]], payload["generated_work_routes"])
        routes[:] = [route for route in routes if route["id"] != "route.review"]

    db_path, cas_root = _persist_generic_join_with_corrupt_selected_plan_payload(
        tmp_path,
        remove_award_route,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="selected join declaration is invalid",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_selected_join_duplicate_generated_target_route(
    tmp_path: Path,
) -> None:
    def duplicate_award_route(payload: dict[str, object]) -> None:
        routes = cast(list[dict[str, object]], payload["generated_work_routes"])
        route = next(route for route in routes if route["id"] == "route.review")
        duplicate = dict(route)
        duplicate["id"] = "route.review.v2"
        routes.append(duplicate)

    db_path, cas_root = _persist_generic_join_with_corrupt_selected_plan_payload(
        tmp_path,
        duplicate_award_route,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="selected join declaration is invalid",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_selected_join_id_collision_with_terminal_action(
    tmp_path: Path,
) -> None:
    db_path, cas_root = _persist_generic_join_with_corrupt_join(
        tmp_path,
        lambda join: join.__setitem__(
            "id",
            "lifecycle.origin.complete",
        ),
    )

    with pytest.raises(
        StorageIntegrityError,
        match="selected join declaration is invalid",
    ):
        load_runtime_state(db_path, cas_root)


def _persist_generic_join_with_corrupt_join(
    tmp_path: Path,
    mutate_join: Callable[[dict[str, object]], None],
) -> tuple[Path, Path]:
    return _persist_generic_join_with_corrupt_selected_plan_payload(
        tmp_path,
        lambda payload: mutate_join(
            cast(list[dict[str, object]], payload["join_declarations"])[0]
        ),
    )


def _persist_generic_join_with_corrupt_selected_plan_payload(
    tmp_path: Path,
    mutate_payload: Callable[[dict[str, object]], None],
) -> tuple[Path, Path]:
    plan, fingerprint = generic_lifecycle.compile_lifecycle()
    plan_ref = plan_ref_for(plan, fingerprint)
    state = RuntimeState(
        admitted_plans={
            fingerprint: AdmittedPlan(
                plan_ref=plan_ref,
                selected_plan=plan,
                external_enqueue_routes=external_enqueue_routes(plan),
            )
        },
        default_plan_ref=plan_ref,
    )
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    assert load_runtime_state(db_path, cas_root) == state

    selected_plan_record = json.loads(
        dumps_cas_object(encode_selected_compiled_plan(plan)).decode("utf-8")
    )
    payload = cast(dict[str, object], selected_plan_record["payload"])
    mutate_payload(payload)
    corrupt_selected_plan_bytes = json.dumps(
        selected_plan_record,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    corrupt_envelope = loads_cas_object(
        corrupt_selected_plan_bytes,
        expected_object_kind=SELECTED_COMPILED_PLAN_OBJECT_KIND,
    )
    corrupt_plan = decode_selected_compiled_plan(corrupt_envelope)
    corrupt_fingerprint = authority_fingerprint(corrupt_plan)
    corrupt_digest = ContentAddressedByteStore(cas_root).put_bytes(
        corrupt_selected_plan_bytes
    )
    with sqlite3.connect(db_path) as connection:
        _replace_plan_authority_fingerprint(
            connection,
            old_fingerprint=fingerprint,
            new_fingerprint=corrupt_fingerprint,
        )
        connection.execute(
            "UPDATE admitted_plan_pins SET selected_plan_digest = ?",
            (corrupt_digest,),
        )
        connection.execute(
            "UPDATE default_plan SET selected_plan_digest = ?",
            (corrupt_digest,),
        )
    return db_path, cas_root


def test_restart_refuses_corrupted_sqlite_record_payload(tmp_path: Path) -> None:
    db_path, cas_root, _state = persist_taskmaster_runtime_state(tmp_path)
    corrupt_payload_digest = ContentAddressedByteStore(cas_root).put_bytes(
        b"not a JSON CAS envelope"
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE work_items SET payload_digest = ?",
            (corrupt_payload_digest,),
        )

    with pytest.raises(
        StorageIntegrityError,
        match="work item payload_digest references malformed CAS object",
    ):
        load_runtime_state(db_path, cas_root)


def test_unreferenced_cas_objects_do_not_prevent_restart(tmp_path: Path) -> None:
    db_path, cas_root, state = persist_taskmaster_runtime_state(tmp_path)
    ContentAddressedByteStore(cas_root).put_bytes(b"not a referenced CAS envelope")

    assert load_runtime_state(db_path, cas_root) == state


def test_restart_refuses_non_boolean_input_receipt_flag(tmp_path: Path) -> None:
    db_path, cas_root, _state = persist_taskmaster_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE input_receipts
        SET accepted = 2
        WHERE rowid = (SELECT rowid FROM input_receipts LIMIT 1)
        """,
    )

    with pytest.raises(StorageIntegrityError, match="input_receipts.accepted"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_negative_work_item_generation(tmp_path: Path) -> None:
    db_path, cas_root, _state = persist_taskmaster_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE work_items
        SET generation = -1
        WHERE rowid = (SELECT rowid FROM work_items LIMIT 1)
        """,
    )

    with pytest.raises(StorageIntegrityError, match="work_items.generation"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_unsupported_plan_format_version(tmp_path: Path) -> None:
    db_path, cas_root, _state = persist_taskmaster_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE work_items
        SET plan_format_version = 99
        WHERE rowid = (SELECT rowid FROM work_items LIMIT 1)
        """,
    )

    with pytest.raises(StorageIntegrityError, match="plan_format_version"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_empty_durable_identifier(tmp_path: Path) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE runs
        SET run_id = ''
        WHERE rowid = (SELECT rowid FROM runs LIMIT 1)
        """,
    )

    with pytest.raises(StorageIntegrityError, match="runs.run_id"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_activation_with_missing_work_item(tmp_path: Path) -> None:
    db_path, cas_root, _state = persist_taskmaster_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE activations
        SET work_item_id = 'missing-work-item'
        WHERE rowid = (SELECT rowid FROM activations LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="activations.work_item_id must reference work_items",
    ):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("column_name", "corrupt_value", "expected_message"),
    (
        (
            "queue_family_id",
            "wrong-queue",
            "activations.queue_family_id must match work_items.queue_family_id",
        ),
        (
            "lineage_id",
            "wrong-lineage",
            "activations.lineage_id must match work_items.lineage_id",
        ),
    ),
)
def test_restart_refuses_activation_that_mismatches_work_item(
    tmp_path: Path,
    column_name: str,
    corrupt_value: object,
    expected_message: str,
) -> None:
    db_path, cas_root, _state = persist_taskmaster_runtime_state(tmp_path)
    _disable_checks_and_execute(
        db_path,
        f"""
        UPDATE activations
        SET {column_name} = ?
        WHERE rowid = (SELECT rowid FROM activations LIMIT 1)
        """,
        (corrupt_value,),
    )

    with pytest.raises(StorageIntegrityError, match=expected_message):
        load_runtime_state(db_path, cas_root)


def test_relation_validator_refuses_activation_plan_ref_mismatching_work_item() -> None:
    state = taskmaster_runtime_state()
    other_plan_ref, other_admitted_plan = _extra_admitted_plan_ref(state)
    activation = next(iter(state.activations.values()))
    corrupt_activation = replace(activation, plan_ref=other_plan_ref)
    corrupt_state = replace(
        state,
        admitted_plans={
            **state.admitted_plans,
            other_plan_ref.authority_fingerprint: other_admitted_plan,
        },
        activations={
            **state.activations,
            corrupt_activation.activation_id: corrupt_activation,
        },
    )

    with pytest.raises(
        StorageIntegrityError,
        match="activations PlanRef must match work_items PlanRef",
    ):
        validate_loaded_runtime_state(corrupt_state)


def test_restart_refuses_work_item_with_missing_admitted_plan(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = persist_taskmaster_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE work_items
        SET plan_authority_fingerprint = 'missing-plan'
        WHERE rowid = (SELECT rowid FROM work_items LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="work_items.plan_authority_fingerprint must reference admitted_plan_pins",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_work_item_plan_ref_that_mismatches_admitted_pin(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = persist_taskmaster_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE work_items
        SET plan_id = 'wrong-plan'
        WHERE rowid = (SELECT rowid FROM work_items LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="work_items PlanRef must match admitted plan pin",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_claimed_activation_with_missing_run(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = persist_taskmaster_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE activations
        SET claimed_by_run_id = 'missing-run'
        WHERE rowid = (SELECT rowid FROM activations LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="activations.claimed_by_run_id must reference runs",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_run_with_missing_activation(tmp_path: Path) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE runs
        SET activation_id = 'missing-activation'
        WHERE rowid = (SELECT rowid FROM runs LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="runs.activation_id must reference activations",
    ):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("column_name", "corrupt_value", "expected_message"),
    (
        (
            "stage_kind_id",
            "wrong-stage",
            "runs.stage_kind_id must match activations.stage_kind_id",
        ),
        (
            "runner_binding_id",
            "wrong-runner",
            "runs.runner_binding_id must match activations.runner_binding_id",
        ),
    ),
)
def test_restart_refuses_run_that_mismatches_activation(
    tmp_path: Path,
    column_name: str,
    corrupt_value: object,
    expected_message: str,
) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    _disable_checks_and_execute(
        db_path,
        f"""
        UPDATE runs
        SET {column_name} = ?
        WHERE rowid = (SELECT rowid FROM runs LIMIT 1)
        """,
        (corrupt_value,),
    )

    with pytest.raises(StorageIntegrityError, match=expected_message):
        load_runtime_state(db_path, cas_root)


def test_relation_validator_refuses_run_plan_ref_mismatching_activation() -> None:
    state = worker_runtime_state()
    other_plan_ref, other_admitted_plan = _extra_admitted_plan_ref(state)
    run = next(iter(state.runs.values()))
    corrupt_run = replace(
        run,
        run_ref=replace(run.run_ref, plan_ref=other_plan_ref),
    )
    corrupt_state = replace(
        state,
        admitted_plans={
            **state.admitted_plans,
            other_plan_ref.authority_fingerprint: other_admitted_plan,
        },
        runs={**state.runs, corrupt_run.run_ref.run_id: corrupt_run},
    )

    with pytest.raises(
        StorageIntegrityError,
        match="runs PlanRef must match activations PlanRef",
    ):
        validate_loaded_runtime_state(corrupt_state)


def test_relation_validator_refuses_duplicate_run_activation_ids() -> None:
    state = worker_runtime_state()
    first_run, second_run = tuple(state.runs.values())
    corrupt_second_run = replace(
        second_run,
        activation_id=first_run.activation_id,
        work_item_id=first_run.work_item_id,
        run_ref=replace(
            second_run.run_ref,
            work_item_id=first_run.work_item_id,
            plan_ref=first_run.run_ref.plan_ref,
        ),
        stage_kind_id=first_run.stage_kind_id,
        runner_binding_id=first_run.runner_binding_id,
    )
    corrupt_state = replace(
        state,
        runs={
            **state.runs,
            corrupt_second_run.run_ref.run_id: corrupt_second_run,
        },
    )

    with pytest.raises(
        StorageIntegrityError,
        match="runs.activation_id must be unique",
    ):
        validate_loaded_runtime_state(corrupt_state)


def test_restart_refuses_duplicate_run_claim_ids(tmp_path: Path) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    claim_id = _single_text_value(db_path, "SELECT claim_id FROM runs LIMIT 1")
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE runs
        SET claim_id = ?
        WHERE rowid = (
            SELECT rowid
            FROM runs
            WHERE claim_id != ?
            LIMIT 1
        )
        """,
        (claim_id, claim_id),
    )

    with pytest.raises(
        StorageIntegrityError,
        match="runs.claim_id must be unique",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_runner_observation_with_missing_run(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE runner_observations
        SET run_id = 'missing-run'
        WHERE rowid = (SELECT rowid FROM runner_observations LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="runner_observations.run_id must reference runs",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_duplicate_runner_observations_for_one_run(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    run_id = _single_text_value(
        db_path,
        "SELECT run_id FROM runner_observations LIMIT 1",
    )
    payload_digest = _single_text_value(
        db_path,
        "SELECT payload_digest FROM runner_observations LIMIT 1",
    )
    _disable_checks_and_execute(
        db_path,
        """
        INSERT INTO runner_observations (
            observation_id,
            run_id,
            payload_digest,
            created_by_input_id,
            observed_at_order
        )
        VALUES ('duplicate-observation', ?, ?, 'input', 999)
        """,
        (run_id, payload_digest),
    )

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "runner_observations accepted-input authority invalid: observation_identity"
        ),
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_runner_observation_observed_at_drift(
    tmp_path: Path,
) -> None:
    state, _plan, _fingerprint = generic_lifecycle.origin_closed_state()
    observation = _observation_for_input(state, "observe-origin")
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    _disable_checks_and_update_one(
        db_path,
        "UPDATE runner_observations SET observed_at = 1 WHERE observation_id = ?",
        (observation.observation_id,),
    )

    with pytest.raises(
        StorageIntegrityError,
        match="runner_observations accepted-input authority invalid: receipt_authority",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_runner_observation_receipt_authority_drift(
    tmp_path: Path,
) -> None:
    state, _plan, _fingerprint = generic_lifecycle.origin_closed_state()
    observation = _observation_for_input(state, "observe-origin")
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    _disable_checks_and_update_one(
        db_path,
        "UPDATE input_receipts SET input_payload_digest = ? WHERE input_id = ?",
        (f"sha256:{'0' * 64}", observation.created_by_input_id),
    )

    with pytest.raises(
        StorageIntegrityError,
        match="runner_observations accepted-input authority invalid: receipt_authority",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_runner_observation_identity_drift(
    tmp_path: Path,
) -> None:
    state, _plan, _fingerprint = generic_lifecycle.origin_closed_state()
    observation = _observation_for_input(state, "observe-origin")
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    _disable_checks_and_update_one(
        db_path,
        "UPDATE runner_observations SET observation_id = ? WHERE observation_id = ?",
        ("wrong-observation-id", observation.observation_id),
    )

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "runner_observations accepted-input authority invalid: observation_identity"
        ),
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_runner_observation_evidence_authority_drift(
    tmp_path: Path,
) -> None:
    state, _plan, _fingerprint = generic_lifecycle.origin_closed_state()
    observation = _observation_for_input(state, "observe-origin")
    changed_payload = {
        **observation.payload,
        "runner_binding_id": "wrong.runner",
    }
    reconstructed = RunnerResultObserved(
        observation.created_by_input_id,
        run_id=observation.run_id,
        payload=changed_payload,
        observed_at=observation.observed_at,
    )
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    changed_digest = ContentAddressedByteStore(cas_root).put_bytes(
        dumps_cas_object(encode_payload(changed_payload))
    )
    with sqlite3.connect(db_path) as connection:
        observation_cursor = connection.execute(
            """
            UPDATE runner_observations
            SET payload_digest = ?
            WHERE observation_id = ?
            """,
            (changed_digest, observation.observation_id),
        )
        receipt_cursor = connection.execute(
            "UPDATE input_receipts SET input_payload_digest = ? WHERE input_id = ?",
            (
                input_payload_digest(reconstructed),
                observation.created_by_input_id,
            ),
        )
        assert observation_cursor.rowcount == 1
        assert receipt_cursor.rowcount == 1

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "runner_observations accepted-input authority invalid: evidence_authority"
        ),
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_preserves_close_only_observation_receipt_authority(
    tmp_path: Path,
) -> None:
    state = _kernel_ping_close_only_state()
    observation = _observation_for_input(state, "observe-worker")

    loaded = persist_and_load_runtime_state(tmp_path, state)
    loaded_observation = _observation_for_input(loaded, "observe-worker")
    reconstructed = RunnerResultObserved(
        loaded_observation.created_by_input_id,
        run_id=loaded_observation.run_id,
        payload=loaded_observation.payload,
        observed_at=loaded_observation.observed_at,
    )

    assert loaded_observation == observation
    assert loaded.artifacts.keys() == state.artifacts.keys()
    assert "transition-observe-worker:artifact" not in loaded.artifacts
    assert loaded.receipts[
        loaded_observation.created_by_input_id
    ].receipt_ref.input_payload_digest == input_payload_digest(reconstructed)


@pytest.mark.parametrize("field", ("payload", "observed_at"))
def test_persist_refuses_close_only_observation_payload_or_time_drift_without_mutation(
    tmp_path: Path,
    field: str,
) -> None:
    state = _kernel_ping_close_only_state()
    corrupt = _with_observation_drift(state, "observe-worker", field=field)
    db_path, cas_root = runtime_store_paths(tmp_path)
    cas_store = ContentAddressedByteStore(cas_root)
    materialize_fake_runner_session_cas(state=state, cas_store=cas_store)

    with pytest.raises(
        StorageIntegrityError,
        match="runner_observations accepted-input authority invalid: receipt_authority",
    ):
        store = SQLiteRuntimeStore.initialize(db_path)
        try:
            store.persist_runtime_state(corrupt, cas_store)
        finally:
            store.close()

    assert load_runtime_state(db_path, cas_root) == RuntimeState()


@pytest.mark.parametrize("field", ("payload", "observed_at"))
def test_restart_refuses_close_only_observation_payload_or_time_drift(
    tmp_path: Path,
    field: str,
) -> None:
    state = _kernel_ping_close_only_state()
    observation = _observation_for_input(state, "observe-worker")
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    if field == "payload":
        _replace_observation_payload_object(
            db_path,
            cas_root,
            observation,
            {**observation.payload, "marker": "CORRUPT_MARKER"},
        )
    else:
        _disable_checks_and_update_one(
            db_path,
            "UPDATE runner_observations SET observed_at = 1 WHERE observation_id = ?",
            (observation.observation_id,),
        )

    with pytest.raises(
        StorageIntegrityError,
        match="runner_observations accepted-input authority invalid: receipt_authority",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_artifact_with_missing_work_item(tmp_path: Path) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE artifacts
        SET work_item_id = 'missing-work-item'
        WHERE rowid = (SELECT rowid FROM artifacts LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="artifacts.work_item_id must reference work_items",
    ):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("column_name", "corrupt_value", "expected_message"),
    (
        (
            "source_run_id",
            "missing-run",
            "artifacts runner-observation provenance invalid: "
            "artifact_source_authority",
        ),
        (
            "source_action_id",
            "missing-action",
            "artifacts runner-observation provenance invalid: "
            "artifact_payload_authority",
        ),
        (
            "source_stage_kind_id",
            "wrong-stage",
            "artifacts runner-observation provenance invalid: "
            "artifact_source_authority",
        ),
        (
            "source_graph_node_id",
            "wrong-node",
            "artifacts runner-observation provenance invalid: "
            "artifact_source_authority",
        ),
    ),
)
def test_restart_refuses_artifact_source_context_corruption(
    tmp_path: Path,
    column_name: str,
    corrupt_value: str,
    expected_message: str,
) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    _disable_checks_and_execute(
        db_path,
        f"""
        UPDATE artifacts
        SET {column_name} = ?
        WHERE rowid = (SELECT rowid FROM artifacts LIMIT 1)
        """,
        (corrupt_value,),
    )

    with pytest.raises(StorageIntegrityError, match=expected_message):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_artifact_with_corrupt_record_payload_digest(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE artifacts
            SET artifact_payload_digest = ?
            WHERE rowid = (SELECT rowid FROM artifacts LIMIT 1)
            """,
            (f"sha256:{'f' * 64}",),
        )

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "artifacts runner-observation provenance invalid: "
            "artifact_payload_authority"
        ),
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_artifact_created_by_input_drift(tmp_path: Path) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE artifacts
        SET created_by_input_id = (
            SELECT input_id
            FROM transitions
            WHERE input_id != artifacts.created_by_input_id
            ORDER BY transition_order DESC
            LIMIT 1
        )
        WHERE rowid = (SELECT rowid FROM artifacts LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "artifacts runner-observation provenance invalid: artifact_source_authority"
        ),
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_artifact_transition_drift(tmp_path: Path) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE artifacts
        SET transition_id = (
            SELECT record_id
            FROM transitions
            WHERE record_id != artifacts.transition_id
            ORDER BY transition_order DESC
            LIMIT 1
        )
        WHERE rowid = (SELECT rowid FROM artifacts LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "artifacts runner-observation provenance invalid: artifact_source_authority"
        ),
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_artifact_with_non_observation_transition(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    wrong_transition_id = _single_text_value(
        db_path,
        """
        SELECT record_id
        FROM transitions
        WHERE input_kind != 'workflow.runner_result_observed'
        ORDER BY transition_order DESC
        LIMIT 1
        """,
    )
    wrong_input_id = _single_text_value(
        db_path,
        """
        SELECT input_id
        FROM transitions
        WHERE record_id = 'transition-claim-worker'
        LIMIT 1
        """,
    )
    _disable_checks_and_execute(
        db_path,
        """
        UPDATE artifacts
        SET transition_id = ?,
            created_by_input_id = ?
        WHERE rowid = (SELECT rowid FROM artifacts LIMIT 1)
        """,
        (wrong_transition_id, wrong_input_id),
    )

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "artifacts runner-observation provenance invalid: artifact_source_authority"
        ),
    ):
        load_runtime_state(db_path, cas_root)


def test_relation_validator_refuses_artifact_with_refused_observation_transition() -> (
    None
):
    state = worker_runtime_state()
    artifact = next(iter(state.artifacts.values()))
    corrupt_transitions = tuple(
        replace(transition, accepted=False)
        if transition.record_id == artifact.transition_id
        else transition
        for transition in state.transitions
    )
    corrupt_state = replace(state, transitions=corrupt_transitions)

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "runner_observations accepted-input authority invalid: transition_authority"
        ),
    ):
        validate_loaded_runtime_state(corrupt_state)


def test_restart_refuses_artifact_runner_observation_input_drift(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE runner_observations
        SET created_by_input_id = (
            SELECT input_id
            FROM transitions
            WHERE input_id != runner_observations.created_by_input_id
            ORDER BY transition_order DESC
            LIMIT 1
        )
        WHERE rowid = (SELECT rowid FROM runner_observations LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "runner_observations accepted-input authority invalid: transition_authority"
        ),
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_activation_route_with_missing_target_activation(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE activation_routes
        SET target_activation_id = 'missing-activation'
        WHERE rowid = (SELECT rowid FROM activation_routes LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="activation_routes.target_activation_id must reference activations",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_activation_route_source_pair_mismatch(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE activation_routes
        SET source_work_item_id = (
            SELECT work_item_id
            FROM work_items
            WHERE work_item_id != (
                SELECT source_work_item_id
                FROM activation_routes
                LIMIT 1
            )
            LIMIT 1
        )
        WHERE rowid = (SELECT rowid FROM activation_routes LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="activation_routes.source_work_item_id must match source_run_id",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_pause_state_with_missing_run(tmp_path: Path) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    work_item_id = _single_text_value(
        db_path,
        "SELECT work_item_id FROM work_items LIMIT 1",
    )
    _disable_checks_and_execute(
        db_path,
        """
        INSERT INTO pause_state (
            id,
            record_id,
            source_run_id,
            work_item_id,
            action_id,
            created_by_input_id,
            paused_at_order
        )
        VALUES (1, 'pause-corrupt', 'missing-run', ?, 'action', 'input', 0)
        """,
        (work_item_id,),
    )

    with pytest.raises(
        StorageIntegrityError,
        match="pause_state.source_run_id must reference runs",
    ):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("table_name", "order_column", "expected_message"),
    (
        (
            "closed_work_items",
            "closed_at_order",
            "closed_work_items.source_run_id must reference run for work_item_id",
        ),
        (
            "pause_state",
            "paused_at_order",
            "pause_state.source_run_id must reference run for work_item_id",
        ),
        (
            "quarantine_records",
            "created_at_order",
            "quarantine_records.source_run_id must reference run for work_item_id",
        ),
    ),
)
def test_restart_refuses_terminal_record_source_run_for_different_work_item(
    tmp_path: Path,
    table_name: str,
    order_column: str,
    expected_message: str,
) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    source_run_id = _single_text_value(
        db_path,
        "SELECT run_id FROM runs WHERE work_item_id = 'work-prompt'",
    )
    other_work_item_id = _single_text_value(
        db_path,
        "SELECT work_item_id FROM work_items WHERE work_item_id != 'work-prompt'",
    )
    id_column = "id, " if table_name == "pause_state" else ""
    id_value = "1, " if table_name == "pause_state" else ""
    close_columns = (
        "operator_intervention_record_id, close_kind,"
        if table_name == "closed_work_items"
        else ""
    )
    close_values = (
        "NULL, 'terminal_action'," if table_name == "closed_work_items" else ""
    )
    _disable_checks_and_execute(
        db_path,
        f"""
        INSERT INTO {table_name} (
            {id_column}
            record_id,
            work_item_id,
            source_run_id,
            action_id,
            {close_columns}
            created_by_input_id,
            {order_column}
        )
        VALUES (
            {id_value}
            'terminal-corrupt',
            ?,
            ?,
            'action',
            {close_values}
            'input',
            0
        )
        """,
        (other_work_item_id, source_run_id),
    )

    with pytest.raises(StorageIntegrityError, match=expected_message):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_empty_optional_audit_action_id_as_typed_error(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = persist_taskmaster_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE governance_events
        SET action_id = ''
        WHERE rowid = (SELECT rowid FROM governance_events LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="governance_events.action_id",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_duplicate_quarantine_rows_for_one_work_item(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    work_item_id = _single_text_value(
        db_path,
        "SELECT work_item_id FROM work_items LIMIT 1",
    )
    run_id = _single_text_value(db_path, "SELECT run_id FROM runs LIMIT 1")
    _disable_checks_and_execute(
        db_path,
        """
        INSERT INTO quarantine_records (
            record_id,
            work_item_id,
            source_run_id,
            action_id,
            created_by_input_id,
            created_at_order
        )
        VALUES
            ('quarantine-corrupt-1', ?, ?, 'action', 'input', 0),
            ('quarantine-corrupt-2', ?, ?, 'action', 'input', 1)
        """,
        (work_item_id, run_id, work_item_id, run_id),
    )

    with pytest.raises(
        StorageIntegrityError,
        match="quarantine_records.work_item_id must be unique",
    ):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("column_name", "corrupt_value", "expected_message"),
    (
        (
            "source_run_id",
            "missing-run",
            "recovery_attempts.source_run_id must reference runs",
        ),
        (
            "source_work_item_id",
            "missing-work-item",
            "recovery_attempts.source_work_item_id must reference work_items",
        ),
        (
            "source_activation_id",
            "missing-activation",
            "recovery_attempts.source_activation_id must reference activations",
        ),
    ),
)
def test_restart_refuses_recovery_attempt_with_missing_source_reference(
    tmp_path: Path,
    column_name: str,
    corrupt_value: object,
    expected_message: str,
) -> None:
    db_path, cas_root, _state = _persist_generic_recovery_runtime_state(tmp_path)
    _disable_checks_and_execute(
        db_path,
        f"""
        UPDATE recovery_attempts
        SET {column_name} = ?
        WHERE rowid = (SELECT rowid FROM recovery_attempts LIMIT 1)
        """,
        (corrupt_value,),
    )

    with pytest.raises(StorageIntegrityError, match=expected_message):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_recovery_attempt_lineage_mismatch(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_recovery_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE recovery_attempts
        SET lineage_id = 'wrong-lineage'
        WHERE rowid = (SELECT rowid FROM recovery_attempts LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="recovery_attempts.lineage_id must match source work item lineage",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_recovery_attempt_policy_outside_selected_plan(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_recovery_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE recovery_attempts
        SET policy_id = 'missing.policy'
        WHERE rowid = (SELECT rowid FROM recovery_attempts LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="recovery_attempts.policy_id must reference selected recovery_policies",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_recovery_attempt_action_outside_selected_policy(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_recovery_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE recovery_attempts
        SET recovery_action_id = 'admission.recover_alternate'
        WHERE rowid = (SELECT rowid FROM recovery_attempts LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "recovery_attempts.recovery_action_id must reference "
            "policy source_recovery_action_ids"
        ),
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_generic_recovery_attempt_action_drift(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_recovery_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE recovery_attempts
        SET recovery_action_id = 'admission.recover_alternate'
        WHERE policy_id = 'admission.recovery_policy'
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "recovery_attempts.recovery_action_id must reference "
            "policy source_recovery_action_ids"
        ),
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_generic_threshold_attempt_action_drift(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_recovery_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE recovery_attempts
        SET recovery_action_id = 'admission.recover_alternate'
        WHERE policy_id = 'admission.recovery_policy'
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "recovery_attempts.recovery_action_id must reference "
            "policy source_recovery_action_ids"
        ),
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_recovery_attempt_record_id_key_mismatch(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_recovery_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE recovery_attempts
        SET record_id = 'recovery-attempt:wrong-fingerprint:wrong-policy:wrong-lineage'
        WHERE rowid = (SELECT rowid FROM recovery_attempts LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match=("recovery_attempts.record_id must match plan/policy/lineage/input key"),
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_latest_recovery_activation_outside_policy_stage(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_recovery_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE recovery_attempts
        SET latest_recovery_activation_id = 'activation-generic-parent',
            latest_recovery_run_id = NULL
        WHERE rowid = (SELECT rowid FROM recovery_attempts LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "recovery_attempts latest recovery activation must match policy "
            "recovery stage"
        ),
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_latest_recovery_run_outside_policy_stage(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_recovery_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE recovery_attempts
        SET latest_recovery_activation_id = NULL,
            latest_recovery_run_id = 'run-generic-parent'
        WHERE rowid = (SELECT rowid FROM recovery_attempts LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "recovery_attempts latest recovery run must match policy recovery stage"
        ),
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_generic_counter_key_drift(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_closed_after_recovery_runtime_state()
    )
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE counters
        SET counter_id = 'admission.recovery_counter'
        WHERE counter_id = 'admission.counter'
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="counters.record_id must match plan/counter/lineage key",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_generic_closed_source_run_drift(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_closed_after_recovery_runtime_state()
    )
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE closed_work_items
        SET source_run_id = 'missing-counter-run'
        WHERE action_id = 'admission.retry'
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="closed_work_items.source_run_id must reference runs",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_generic_closed_action_drift(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_closed_counter_runtime_state()
    )
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE closed_work_items
        SET action_id = 'admission.child.complete'
        WHERE action_id = 'admission.retry'
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="closed_work_items.action_id must match source stage",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_generic_closed_existing_source_run_drift(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_closed_after_recovery_runtime_state()
    )
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE closed_work_items
        SET source_run_id = 'run-generic-recovery'
        WHERE action_id = 'admission.retry'
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="closed_work_items.action_id must match source stage",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_generic_closed_observation_input_drift(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_closed_after_recovery_runtime_state()
    )
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE closed_work_items
        SET source_run_id = 'run-generic-parent'
        WHERE action_id = 'admission.retry'
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "closed_work_items.created_by_input_id must match source runner observation"
        ),
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_escalation_when_selected_plan_regains_old_action_kind(
    tmp_path: Path,
) -> None:
    plan, fingerprint = generic_admission.compile_plan()
    action = next(
        item
        for item in plan.terminal_actions
        if str(item.id) == generic_admission.ESCALATION_ACTION_ID
    )
    assert action.action_kind == "close_with_escalation"
    state = _generic_escalation_runtime_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    assert load_runtime_state(db_path, cas_root) == state

    old_action_plan = _with_terminal_action_kind(
        plan,
        generic_admission.ESCALATION_ACTION_ID,
        "escalate_to_planning",
    )
    old_kind_fingerprint = authority_fingerprint(old_action_plan)
    old_kind_digest = ContentAddressedByteStore(cas_root).put_bytes(
        dumps_cas_object(encode_selected_compiled_plan(old_action_plan))
    )
    with sqlite3.connect(db_path) as connection:
        _replace_plan_authority_fingerprint(
            connection,
            old_fingerprint=fingerprint,
            new_fingerprint=old_kind_fingerprint,
        )
        connection.execute(
            "UPDATE admitted_plan_pins SET selected_plan_digest = ?",
            (old_kind_digest,),
        )
        connection.execute(
            "UPDATE default_plan SET selected_plan_digest = ?",
            (old_kind_digest,),
        )

    with pytest.raises(
        StorageIntegrityError,
        match="selected terminal action kind is unsupported",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_deferred_terminal_action_kind(tmp_path: Path) -> None:
    plan, fingerprint = generic_admission.compile_plan()
    state = _generic_admitted_only_state(plan, fingerprint)
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    load_runtime_state(db_path, cas_root)

    deferred_action_plan = _with_terminal_action_kind(
        plan,
        generic_admission.ESCALATION_ACTION_ID,
        "deferred_terminal_action",
    )
    deferred_fingerprint = authority_fingerprint(deferred_action_plan)
    deferred_digest = ContentAddressedByteStore(cas_root).put_bytes(
        dumps_cas_object(encode_selected_compiled_plan(deferred_action_plan))
    )
    with sqlite3.connect(db_path) as connection:
        _replace_plan_authority_fingerprint(
            connection,
            old_fingerprint=fingerprint,
            new_fingerprint=deferred_fingerprint,
        )
        connection.execute(
            "UPDATE admitted_plan_pins SET selected_plan_digest = ?",
            (deferred_digest,),
        )
        connection.execute(
            "UPDATE default_plan SET selected_plan_digest = ?",
            (deferred_digest,),
        )

    with pytest.raises(
        StorageIntegrityError,
        match="selected terminal action kind is unsupported",
    ):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    (
        ("target_stage_kind_id", "admission.child"),
        ("target_graph_node_id", "admission.child.start"),
        ("emitted_queue_family_id", "child"),
        ("runner_binding_id", "admission.runner"),
        ("payload_projection", {"kind": "source", "path": ("artifact_payload",)}),
        (
            "dynamic_target_selector",
            {
                "kind": "observation_payload_route_target",
                "field_names": ("target",),
                "targets": {
                    "child": {
                        "target_stage_kind_id": "admission.child",
                        "target_graph_node_id": "admission.child.start",
                        "emitted_queue_family_id": "child",
                        "runner_binding_id": "admission.runner",
                    },
                },
            },
        ),
    ),
)
def test_restart_refuses_close_with_escalation_route_authority_drift(
    tmp_path: Path,
    field_name: str,
    field_value: object,
) -> None:
    plan, fingerprint = generic_admission.compile_plan()
    state = _generic_admitted_only_state(plan, fingerprint)
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    load_runtime_state(db_path, cas_root)

    tampered_plan = _with_terminal_action_fields(
        plan,
        generic_admission.ESCALATION_ACTION_ID,
        **{field_name: field_value},
    )
    tampered_fingerprint = authority_fingerprint(tampered_plan)
    tampered_digest = ContentAddressedByteStore(cas_root).put_bytes(
        dumps_cas_object(encode_selected_compiled_plan(tampered_plan))
    )
    with sqlite3.connect(db_path) as connection:
        _replace_plan_authority_fingerprint(
            connection,
            old_fingerprint=fingerprint,
            new_fingerprint=tampered_fingerprint,
        )
        connection.execute(
            "UPDATE admitted_plan_pins SET selected_plan_digest = ?",
            (tampered_digest,),
        )
        connection.execute(
            "UPDATE default_plan SET selected_plan_digest = ?",
            (tampered_digest,),
        )

    with pytest.raises(
        StorageIntegrityError,
        match="close_with_escalation selected action cannot carry route authority",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_cooldown_wait_policy_outside_selected_plan(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_cooldown_runtime_state()
    )
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE cooldown_waits
        SET policy_id = 'missing.policy'
        WHERE rowid = (SELECT rowid FROM cooldown_waits LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="cooldown_waits.policy_id must reference selected recovery_policies",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_cooldown_wait_action_outside_selected_policy(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_cooldown_runtime_state()
    )
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE cooldown_waits
        SET recovery_action_id = 'admission.recover_alternate'
        WHERE rowid = (SELECT rowid FROM cooldown_waits LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "cooldown_waits.recovery_action_id must reference "
            "policy source_recovery_action_ids"
        ),
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_generic_threshold_cooldown_wait_action_drift(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_cooldown_runtime_state()
    )
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE cooldown_waits
        SET recovery_action_id = 'admission.recover_alternate'
        WHERE policy_id = 'admission.recovery_policy'
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "cooldown_waits.recovery_action_id must reference "
            "policy source_recovery_action_ids"
        ),
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_cooldown_wait_missing_attempt_reference(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_cooldown_runtime_state()
    )
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE cooldown_waits
        SET recovery_attempt_record_id = 'missing-attempt'
        WHERE rowid = (SELECT rowid FROM cooldown_waits LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="cooldown_waits.recovery_attempt_record_id must reference "
        "recovery_attempts",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_cooldown_wait_lineage_mismatch(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_cooldown_runtime_state()
    )
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE cooldown_waits
        SET lineage_id = 'wrong-lineage'
        WHERE rowid = (SELECT rowid FROM cooldown_waits LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="cooldown_waits.lineage_id must match recovery attempt lineage",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_cooldown_wait_plan_outside_selected_pins(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_cooldown_runtime_state()
    )
    corrupt_fingerprint = f"sha256:{'f' * 64}"
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE cooldown_waits
        SET plan_authority_fingerprint = ?
        WHERE rowid = (SELECT rowid FROM cooldown_waits LIMIT 1)
        """,
        (corrupt_fingerprint,),
    )

    with pytest.raises(
        StorageIntegrityError,
        match="cooldown_waits.plan_authority_fingerprint must reference "
        "admitted_plan_pins",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_consumed_cooldown_wait_wrong_existing_source_run(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_consumed_cooldown_runtime_state()
    )
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE cooldown_waits
        SET source_run_id = 'run-generic-recovery'
        WHERE rowid = (SELECT rowid FROM cooldown_waits LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="cooldown_waits source run must match source activation",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_consumed_cooldown_wait_wrong_existing_source_activation(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_consumed_cooldown_runtime_state()
    )
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE cooldown_waits
        SET source_activation_id = 'activation-generic-recovery'
        WHERE rowid = (SELECT rowid FROM cooldown_waits LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="cooldown_waits source run must match source activation",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_consumed_cooldown_wait_wrong_consumed_input_id(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_consumed_cooldown_runtime_state()
    )
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE cooldown_waits
        SET consumed_input_id = 'observe-generic-return'
        WHERE rowid = (SELECT rowid FROM cooldown_waits LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "cooldown_waits resulting activation created_by_input_id must match "
            "consumed_input_id"
        ),
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_receipt_with_missing_transition(tmp_path: Path) -> None:
    db_path, cas_root, _state = persist_taskmaster_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE input_receipts
        SET transition_id = 'missing-transition'
        WHERE rowid = (SELECT rowid FROM input_receipts LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="input_receipts.transition_id must reference transitions",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_receipt_with_transition_for_different_input(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE input_receipts
        SET transition_id = (
            SELECT record_id
            FROM transitions
            WHERE input_id != input_receipts.input_id
            LIMIT 1
        )
        WHERE rowid = (SELECT rowid FROM input_receipts LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="input_receipts.transition_id must match receipt input",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_receipt_with_accepted_flag_mismatch(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE input_receipts
        SET accepted = 0
        WHERE rowid = (SELECT rowid FROM input_receipts LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="input_receipts.transition_id must match receipt input",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_audit_record_with_missing_transition_order(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = persist_taskmaster_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE governance_events
        SET transition_order = 999
        WHERE rowid = (SELECT rowid FROM governance_events LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="governance_events.transition_order must reference transitions",
    ):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize("table_name", ("governance_events", "traces"))
def test_restart_refuses_audit_record_with_disposition_mismatch(
    tmp_path: Path,
    table_name: str,
) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        f"""
        UPDATE {table_name}
        SET disposition = 'refused'
        WHERE rowid = (SELECT rowid FROM {table_name} LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match=f"{table_name}.disposition must match transition accepted flag",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_refusal_for_accepted_transition(tmp_path: Path) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    _disable_checks_and_execute(
        db_path,
        """
        INSERT INTO refusals (
            record_id,
            transition_order,
            input_id,
            input_kind,
            input_family,
            reason,
            detail,
            created_at_order
        )
        SELECT
            'refusal-for-accepted-transition',
            transition_order,
            input_id,
            input_kind,
            input_family,
            'manual-refusal',
            NULL,
            0
        FROM transitions
        WHERE accepted = 1
        LIMIT 1
        """,
        (),
    )

    with pytest.raises(
        StorageIntegrityError,
        match="refusals.transition_order must reference refused transition",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_trace_that_disagrees_with_governance_event(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE traces
        SET authority_source = 'trace-drift'
        WHERE rowid = (SELECT rowid FROM traces LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="traces must match governance_events for transition_order",
    ):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize("table_name", ("governance_events", "traces"))
def test_restart_refuses_duplicate_audit_rows_for_one_transition(
    tmp_path: Path,
    table_name: str,
) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    _disable_checks_and_execute(
        db_path,
        f"""
        INSERT INTO {table_name} (
            record_id,
            transition_order,
            input_id,
            input_kind,
            input_family,
            disposition,
            plan_fingerprint,
            work_item_id,
            run_id,
            action_id,
            authority_source,
            refusal_reason,
            created_at_order
        )
        SELECT
            'duplicate-' || record_id,
            transition_order,
            input_id,
            input_kind,
            input_family,
            disposition,
            plan_fingerprint,
            work_item_id,
            run_id,
            action_id,
            authority_source,
            refusal_reason,
            999
        FROM governance_events
        LIMIT 1
        """,
        (),
    )

    with pytest.raises(
        StorageIntegrityError,
        match=f"{table_name}.transition_order must be unique",
    ):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("table_name", "expected_message"),
    (
        (
            "governance_events",
            "governance_events transition fields must match transitions",
        ),
        ("traces", "traces transition fields must match transitions"),
        ("refusals", "refusals transition fields must match transitions"),
    ),
)
def test_restart_refuses_audit_record_with_mismatched_transition_fields(
    tmp_path: Path,
    table_name: str,
    expected_message: str,
) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    if table_name == "refusals":
        _disable_checks_and_execute(
            db_path,
            """
            INSERT INTO refusals (
                record_id,
                transition_order,
                input_id,
                input_kind,
                input_family,
                reason,
                detail,
                created_at_order
            )
            VALUES (
                'refusal-corrupt',
                0,
                'wrong-input',
                'control.initialize_workspace',
                'control',
                'manual-refusal',
                NULL,
                0
            )
            """,
            (),
        )
    else:
        _disable_checks_and_update_one(
            db_path,
            f"""
            UPDATE {table_name}
            SET input_id = 'wrong-input'
            WHERE rowid = (SELECT rowid FROM {table_name} LIMIT 1)
            """,
        )

    with pytest.raises(StorageIntegrityError, match=expected_message):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_transition_created_at_drift(tmp_path: Path) -> None:
    db_path, cas_root, _state = persist_worker_runtime_state(tmp_path)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE transitions
        SET created_at = 'wrong-created-at'
        WHERE rowid = (SELECT rowid FROM transitions LIMIT 1)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match="transitions.created_at must match transition_order",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_operator_wait_with_corrupt_wait_id(tmp_path: Path) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_operator_wait_runtime_state()
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE operator_waits SET wait_id = 'operator-wait:corrupt'"
        )

    with pytest.raises(
        StorageIntegrityError,
        match="operator_waits.wait_id must match authority",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_operator_wait_missing_required_source_artifact(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_operator_wait_runtime_state()
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE operator_waits SET source_artifact_id = NULL")

    with pytest.raises(
        StorageIntegrityError,
        match="operator_waits source artifact is required by source action",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_resolved_operator_wait_missing_resolution_audit(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_resolved_operator_wait_state()
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE operator_waits SET target_activation_id = NULL")

    with pytest.raises(
        StorageIntegrityError,
        match="operator_waits resume audit fields are incoherent",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_operator_wait_with_corrupt_created_input_digest(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_operator_wait_runtime_state()
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE operator_waits SET created_input_payload_digest = 'not-a-digest'"
        )

    with pytest.raises(
        StorageIntegrityError,
        match="operator_waits.created_input_payload_digest must be sha256 digest",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_resolved_operator_wait_with_corrupt_resolved_digest(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_resolved_operator_wait_state()
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE operator_waits SET resolved_input_payload_digest = ?",
            (f"sha256:{'0' * 63}",),
        )

    with pytest.raises(
        StorageIntegrityError,
        match="operator_waits.resolved_input_payload_digest must be sha256 digest",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_resolved_operator_wait_with_corrupt_payload_digest(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_resolved_operator_wait_state()
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE operator_waits SET payload_digest = ?",
            (f"sha256:{'0' * 63}",),
        )

    with pytest.raises(
        StorageIntegrityError,
        match="operator_waits.payload_digest must be sha256 digest",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_resolved_operator_wait_with_wrong_actor_kind(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_resolved_operator_wait_state()
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE operator_waits SET actor_kind = 'remote_operator'")

    with pytest.raises(
        StorageIntegrityError,
        match="operator_waits.actor_kind must match selected operator_wait",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_active_leave_open_operator_wait_with_closed_source(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_operator_wait_runtime_state()
    )
    with sqlite3.connect(db_path) as connection:
        wait_row = connection.execute(
            """
            SELECT
                source_work_item_id,
                source_run_id,
                source_action_id,
                created_input_id
            FROM operator_waits
            """
        ).fetchone()
        assert wait_row is not None
        connection.execute(
            """
            INSERT INTO closed_work_items (
                record_id,
                work_item_id,
                source_run_id,
                action_id,
                operator_intervention_record_id,
                close_kind,
                created_by_input_id,
                closed_at_order
            )
            VALUES (?, ?, ?, ?, NULL, 'terminal_action', ?, 999)
            """,
            (
                "closed-corrupt-operator-wait-source",
                wait_row[0],
                wait_row[1],
                wait_row[2],
                wait_row[3],
            ),
        )

    with pytest.raises(
        StorageIntegrityError,
        match="operator_waits leave_open source must remain open",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_active_close_on_create_operator_wait_with_open_source(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_operator_wait_runtime_state(close_on_create=True)
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            DELETE FROM closed_work_items
            WHERE work_item_id = (SELECT source_work_item_id FROM operator_waits)
            """
        )

    with pytest.raises(
        StorageIntegrityError,
        match="operator_waits close_on_create source must be closed",
    ):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("action_id", "expected_message"),
    (
        (
            "admission.retry",
            "closed_work_items.action_id must match source runner observation",
        ),
        (
            "admission.recover",
            "closed_work_items.action_id must reference selected close action",
        ),
    ),
)
def test_restart_refuses_active_close_on_create_operator_wait_close_action_drift(
    tmp_path: Path,
    action_id: str,
    expected_message: str,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_operator_wait_runtime_state(close_on_create=True)
    )
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE closed_work_items
        SET action_id = ?
        WHERE work_item_id = (SELECT source_work_item_id FROM operator_waits)
        """,
        (action_id,),
    )

    with pytest.raises(StorageIntegrityError, match=expected_message):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_active_close_on_create_operator_wait_audit_action_drift(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_operator_wait_runtime_state(close_on_create=True)
    )
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE governance_events
        SET action_id = 'admission.retry'
        WHERE input_id = (SELECT created_input_id FROM operator_waits)
        """,
    )
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE traces
        SET action_id = 'admission.retry'
        WHERE input_id = (SELECT created_input_id FROM operator_waits)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match=("runner_observations accepted-input authority invalid: audit_authority"),
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_incident_route_creator_input_drift(
    tmp_path: Path,
) -> None:
    state = _kernel_ping_incident_route_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    artifact = next(
        candidate
        for candidate in state.artifacts.values()
        if candidate.created_by_input_id
        == fake_runner_completion_input_id("observe-needs-review")
    )
    route = next(
        candidate
        for candidate in state.activation_routes
        if candidate.created_by_input_id == artifact.created_by_input_id
        and candidate.source_run_id == artifact.source_run_id
        and candidate.action_id == artifact.source_action_id
    )
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE activation_routes
        SET created_by_input_id = 'wrong-input'
        WHERE record_id = ?
        """,
        (route.record_id,),
    )

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "artifacts runner-observation provenance invalid: "
            "artifact_source_authority:work_item"
        ),
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_resumed_operator_wait_source_closed_by_wait_creation_run(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_resolved_operator_wait_state()
    )
    with sqlite3.connect(db_path) as connection:
        wait_row = connection.execute(
            """
            SELECT
                source_work_item_id,
                source_run_id,
                source_action_id,
                created_input_id
            FROM operator_waits
            """
        ).fetchone()
        assert wait_row is not None
        connection.execute(
            """
            INSERT INTO closed_work_items (
                record_id,
                work_item_id,
                source_run_id,
                action_id,
                operator_intervention_record_id,
                close_kind,
                created_by_input_id,
                closed_at_order
            )
            VALUES (?, ?, ?, ?, NULL, 'terminal_action', ?, 999)
            """,
            (
                "closed-corrupt-resumed-source-by-wait-creation-run",
                wait_row[0],
                wait_row[1],
                wait_row[2],
                wait_row[3],
            ),
        )

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "operator_waits resume source close must originate from resumed activation"
        ),
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_resumed_operator_wait_source_close_arbitrary_input_drift(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_resumed_operator_wait_source_closed_state()
    )
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE closed_work_items
        SET created_by_input_id = 'bogus-later-input'
        WHERE work_item_id = (SELECT source_work_item_id FROM operator_waits)
        """,
    )

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "closed_work_items.created_by_input_id must match source runner observation"
        ),
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_resumed_operator_wait_source_close_source_action_drift(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_resumed_operator_wait_source_closed_state()
    )
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE closed_work_items
            SET action_id = (SELECT source_action_id FROM operator_waits)
            WHERE work_item_id = (SELECT source_work_item_id FROM operator_waits)
            """
        )
        assert cursor.rowcount == 1

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "operator_waits resume source close action must not be wait source action"
        ),
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_resumed_operator_wait_source_close_non_close_action_drift(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_resumed_operator_wait_source_closed_state()
    )
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE closed_work_items
                SET action_id = 'admission.recover'
            WHERE work_item_id = (SELECT source_work_item_id FROM operator_waits)
            """
        )
        assert cursor.rowcount == 1

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "operator_waits resume source close action must reference "
            "selected close action"
        ),
    ):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize("input_column", ("created_input_id", "resolved_input_id"))
def test_restart_refuses_resumed_operator_wait_source_close_created_input_drift(
    tmp_path: Path,
    input_column: str,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_resumed_operator_wait_source_closed_state()
    )
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            f"""
            UPDATE closed_work_items
            SET created_by_input_id = (SELECT {input_column} FROM operator_waits)
            WHERE work_item_id = (SELECT source_work_item_id FROM operator_waits)
            """
        )
        assert cursor.rowcount == 1

    with pytest.raises(
        StorageIntegrityError,
        match=(
            "operator_waits resume source close input must be later "
            "than wait creation and resolution"
        ),
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_resumed_operator_wait_target_work_item_drift(
    tmp_path: Path,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_resolved_operator_wait_state()
    )
    with sqlite3.connect(db_path) as connection:
        wait_row = connection.execute(
            "SELECT source_work_item_id, target_activation_id FROM operator_waits"
        ).fetchone()
        assert wait_row is not None
        source_work_item_id, target_activation_id = wait_row
        connection.execute(
            """
            INSERT INTO work_items (
                work_item_id,
                plan_id,
                plan_authority_fingerprint,
                plan_format_version,
                generation,
                payload_digest,
                queue_family_id,
                lineage_id,
                created_by_input_id,
                created_at_order
            )
            SELECT
                'work-corrupt-resume-target',
                plan_id,
                plan_authority_fingerprint,
                plan_format_version,
                generation,
                payload_digest,
                queue_family_id,
                lineage_id,
                created_by_input_id,
                created_at_order
            FROM work_items
            WHERE work_item_id = ?
            """,
            (source_work_item_id,),
        )
        cursor = connection.execute(
            "UPDATE activations SET work_item_id = ? WHERE activation_id = ?",
            ("work-corrupt-resume-target", target_activation_id),
        )
        assert cursor.rowcount == 1

    with pytest.raises(
        StorageIntegrityError,
        match="operator_waits resume target work item must match recorded source",
    ):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("column", "value", "message"),
    (
        (
            "queue_family_id",
            "child",
            "operator_waits resume target queue family must match recorded source",
        ),
        (
            "graph_node_id",
            "test.wrong_graph_node",
            "operator_waits resume target graph node must match recorded source",
        ),
        (
            "stage_kind_id",
            "admission.child",
            "operator_waits resume target stage kind must match recorded source",
        ),
        (
            "runner_binding_id",
            "test.wrong_runner",
            "operator_waits resume target runner binding must match recorded source",
        ),
    ),
)
def test_restart_refuses_resumed_operator_wait_target_authority_drift(
    tmp_path: Path,
    column: str,
    value: str,
    message: str,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_resolved_operator_wait_state()
    )
    with sqlite3.connect(db_path) as connection:
        wait_row = connection.execute(
            "SELECT target_activation_id FROM operator_waits"
        ).fetchone()
        assert wait_row is not None
        (target_activation_id,) = wait_row
        cursor = connection.execute(
            f"UPDATE activations SET {column} = ? WHERE activation_id = ?",
            (value, target_activation_id),
        )
        assert cursor.rowcount == 1

    with pytest.raises(StorageIntegrityError, match=message):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("column", "value", "message"),
    (
        (
            "queue_family_id",
            "child",
            "operator_waits revise target queue family must match "
            "selected operator_wait",
        ),
        (
            "graph_node_id",
            "test.wrong_graph_node",
            "operator_waits revise target graph node must match selected operator_wait",
        ),
        (
            "stage_kind_id",
            "admission.child",
            "operator_waits revise target stage kind must match selected operator_wait",
        ),
        (
            "runner_binding_id",
            "test.wrong_runner",
            "operator_waits revise target runner binding must match "
            "selected operator_wait",
        ),
    ),
)
def test_restart_refuses_revised_operator_wait_target_authority_drift(
    tmp_path: Path,
    column: str,
    value: str,
    message: str,
) -> None:
    db_path, cas_root, _state = _persist_generic_state(
        tmp_path, _generic_revised_operator_wait_state()
    )
    with sqlite3.connect(db_path) as connection:
        wait_row = connection.execute(
            "SELECT target_work_item_id, target_activation_id FROM operator_waits"
        ).fetchone()
        assert wait_row is not None
        target_work_item_id, target_activation_id = wait_row
        if column == "queue_family_id":
            connection.execute(
                "UPDATE work_items SET queue_family_id = ? WHERE work_item_id = ?",
                (value, target_work_item_id),
            )
        connection.execute(
            f"UPDATE activations SET {column} = ? WHERE activation_id = ?",
            (value, target_activation_id),
        )

    with pytest.raises(StorageIntegrityError, match=message):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    "case",
    (
        "target_extra_property",
        "target_required_property_removal",
        "target_wrong_type",
        "target_explicit_null_non_nullable",
        "target_payload_cas_mismatch",
        "source_artifact_digest_drift",
        "fanout_item_key_drift",
        "route_target_drift",
        "activation_target_drift",
        "partial_dependency_aftermath",
    ),
)
def test_restart_refuses_fanout_target_payload_digest_drift(
    tmp_path: Path,
    case: str,
) -> None:
    state, _plan, _fingerprint = (
        generic_lifecycle.optional_omission_first_fanout_state()
    )
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    if case == "target_extra_property":
        _replace_first_fanout_target_payload(
            db_path,
            cas_root,
            lambda payload: {**payload, "unexpected": "extra"},
        )
    elif case == "target_required_property_removal":
        _replace_first_fanout_target_payload(
            db_path,
            cas_root,
            lambda payload: {
                key: value for key, value in payload.items() if key != "bundle_id"
            },
        )
    elif case == "target_wrong_type":
        _replace_first_fanout_target_payload(
            db_path,
            cas_root,
            lambda payload: {**payload, "items": "not-items"},
        )
    elif case == "target_explicit_null_non_nullable":
        _replace_first_fanout_target_payload(
            db_path,
            cas_root,
            lambda payload: {**payload, "note": None},
        )
    elif case == "target_payload_cas_mismatch":
        _tamper_first_fanout_target_payload_cas(db_path, cas_root)
    elif case == "source_artifact_digest_drift":
        _disable_checks_and_update_one(
            db_path,
            "UPDATE fanout_records SET source_artifact_digest = ? WHERE item_key = ?",
            (f"sha256:{'1' * 64}", "one"),
        )
    elif case == "fanout_item_key_drift":
        _disable_checks_and_update_one(
            db_path,
            "UPDATE fanout_records SET item_key = ? WHERE item_key = ?",
            ("missing-item", "one"),
        )
    elif case == "route_target_drift":
        target_work_item_id = _first_fanout_target_work_item_id(db_path)
        _disable_checks_and_update_one(
            db_path,
            """
            UPDATE activation_routes
            SET target_activation_id = ?
            WHERE target_work_item_id = ?
            """,
            ("wrong-activation", target_work_item_id),
        )
    elif case == "activation_target_drift":
        target_activation_id = _first_fanout_target_activation_id(db_path)
        _disable_checks_and_update_one(
            db_path,
            "UPDATE activations SET work_item_id = ? WHERE activation_id = ?",
            ("wrong-work", target_activation_id),
        )
    elif case == "partial_dependency_aftermath":
        _disable_checks_and_update_one(
            db_path,
            """
            DELETE FROM work_dependencies
            WHERE fanout_record_id = (
                SELECT record_id FROM fanout_records ORDER BY item_key LIMIT 1
            )
            """,
        )
    else:  # pragma: no cover - assertion guard
        raise AssertionError(f"unknown case: {case}")

    with pytest.raises(StorageIntegrityError):
        load_runtime_state(db_path, cas_root)


def _first_fanout_target_work_item_id(db_path: Path) -> str:
    return _single_text_value(
        db_path,
        """
        SELECT target_work_item_id
        FROM fanout_records
        ORDER BY item_key
        LIMIT 1
        """,
    )


def _first_fanout_target_activation_id(db_path: Path) -> str:
    return _single_text_value(
        db_path,
        """
        SELECT target_activation_id
        FROM fanout_records
        ORDER BY item_key
        LIMIT 1
        """,
    )


def _first_fanout_target_payload_digest(db_path: Path) -> str:
    target_work_item_id = _first_fanout_target_work_item_id(db_path)
    return _single_text_value(
        db_path,
        """
        SELECT payload_digest
        FROM work_items
        WHERE work_item_id = ?
        """,
        target_work_item_id,
    )


def _replace_first_fanout_target_payload(
    db_path: Path,
    cas_root: Path,
    mutate: Callable[[dict[str, object]], Mapping[str, object]],
) -> None:
    target_work_item_id = _first_fanout_target_work_item_id(db_path)
    payload_digest = _first_fanout_target_payload_digest(db_path)
    store = ContentAddressedByteStore(cas_root)
    payload = dict(
        decode_payload(
            loads_cas_object(
                store.get_bytes(payload_digest),
                expected_object_kind=PAYLOAD_OBJECT_KIND,
            )
        )
    )
    changed_digest = store.put_bytes(dumps_cas_object(encode_payload(mutate(payload))))
    _disable_checks_and_update_one(
        db_path,
        "UPDATE work_items SET payload_digest = ? WHERE work_item_id = ?",
        (changed_digest, target_work_item_id),
    )


def _tamper_first_fanout_target_payload_cas(db_path: Path, cas_root: Path) -> None:
    payload_digest = _first_fanout_target_payload_digest(db_path)
    object_path = cas_root / "sha256" / payload_digest.removeprefix("sha256:")
    assert object_path.exists()
    object_path.write_bytes(b"not the original payload")


def _replace_selected_plan_digest(db_path: Path, digest: str) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE admitted_plan_pins SET selected_plan_digest = ?",
            (digest,),
        )
        connection.execute(
            "UPDATE default_plan SET selected_plan_digest = ?",
            (digest,),
        )


def _replace_plan_authority_fingerprint(
    connection: sqlite3.Connection,
    *,
    old_fingerprint: str,
    new_fingerprint: str,
) -> None:
    tables = tuple(
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        if isinstance(row[0], str)
    )
    for table_name in tables:
        columns = {
            row[1]
            for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
            if isinstance(row[1], str)
        }
        for column_name in (
            "authority_fingerprint",
            "plan_authority_fingerprint",
            "plan_fingerprint",
        ):
            if column_name not in columns:
                continue
            connection.execute(
                f"UPDATE {table_name} SET {column_name} = ? WHERE {column_name} = ?",
                (new_fingerprint, old_fingerprint),
            )


def _with_terminal_action_kind(
    plan: SelectedCompiledPlan,
    action_id: str,
    action_kind: str,
) -> SelectedCompiledPlan:
    return _with_terminal_action_fields(plan, action_id, action_kind=action_kind)


def _with_terminal_action_fields(
    plan: SelectedCompiledPlan,
    action_id: str,
    **field_values: object,
) -> SelectedCompiledPlan:
    terminal_actions = list(plan.terminal_actions)
    for index, action in enumerate(terminal_actions):
        if str(action.id) == action_id:
            terminal_actions[index] = replace(cast(Any, action), **field_values)
            return replace(plan, terminal_actions=tuple(terminal_actions))
    raise AssertionError(f"missing action {action_id!r}")


def _single_text_value(
    db_path: Path,
    query: str,
    *parameters: object,
) -> str:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(query, parameters).fetchone()
    assert row is not None
    value = row[0]
    assert isinstance(value, str)
    return value


def _observation_for_input(state: RuntimeState, input_id: str):
    input_id = fake_runner_completion_input_id(input_id)
    return next(
        observation
        for observation in state.runner_observations.values()
        if observation.created_by_input_id == input_id
    )


def _with_observation_drift(
    state: RuntimeState,
    input_id: str,
    *,
    field: str,
) -> RuntimeState:
    observation = _observation_for_input(state, input_id)
    changed = (
        replace(
            observation,
            payload={**observation.payload, "marker": "CORRUPT_MARKER"},
        )
        if field == "payload"
        else replace(observation, observed_at=1)
    )
    return replace(
        state,
        runner_observations={
            **state.runner_observations,
            observation.observation_id: changed,
        },
    )


def _replace_observation_payload_object(
    db_path: Path,
    cas_root: Path,
    observation,
    payload: dict[str, object],
) -> None:
    payload_digest = ContentAddressedByteStore(cas_root).put_bytes(
        dumps_cas_object(encode_payload(payload))
    )
    _disable_checks_and_update_one(
        db_path,
        "UPDATE runner_observations SET payload_digest = ? WHERE observation_id = ?",
        (payload_digest, observation.observation_id),
    )


def _kernel_ping_close_only_state() -> RuntimeState:
    plan, fingerprint = kernel_ping_support.compile_kernel_ping()
    claimed = bootstrap_to_worker_claim(plan, fingerprint)
    return kernel_ping_support.apply_accepted_input(
        claimed,
        kernel_ping_support.runner_observation(
            state=claimed,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-worker",
            action_id="kernel_ping.close_worker_success",
            input_id="observe-worker",
            artifact_payload={},
        ),
        kernel_ping_support.kernel_ping_context("observe-worker"),
    )


def _kernel_ping_incident_route_state() -> RuntimeState:
    plan, fingerprint = kernel_ping_support.compile_kernel_ping()
    claimed = bootstrap_to_worker_claim(plan, fingerprint)
    return kernel_ping_support.apply_accepted_input(
        claimed,
        kernel_ping_support.runner_observation(
            state=claimed,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-worker",
            action_id="kernel_ping.route_worker_review",
            input_id="observe-needs-review",
            artifact_payload={
                "worker_summary": "The task lacks an acceptance command.",
                "missing_details": ("exact command", "expected output"),
            },
        ),
        kernel_ping_support.kernel_ping_context("observe-needs-review"),
    )


def _delete_cas_object(cas_root: Path, digest: str) -> None:
    (cas_root / "sha256" / digest.removeprefix("sha256:")).unlink()


def _extra_admitted_plan_ref(
    state: RuntimeState,
) -> tuple[PlanRef, AdmittedPlan]:
    admitted_plan = next(iter(state.admitted_plans.values()))
    plan_ref = replace(
        admitted_plan.plan_ref,
        authority_fingerprint=f"sha256:{'2' * 64}",
    )
    return plan_ref, replace(admitted_plan, plan_ref=plan_ref)


def _apply_accepted_input(
    state: RuntimeState,
    transition_input: object,
    context: object,
) -> RuntimeState:
    decision = decide(state, transition_input, context)
    assert decision.accepted is True
    return apply(state, decision)


def _generic_parent_claimed_state() -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    plan, fingerprint = generic_admission.compile_plan()
    state = empty_runtime_state()
    transitions = (
        (
            InitializeWorkspace("generic-init"),
            deterministic_context(transition_id="transition-generic-init"),
        ),
        (
            AdmitPlan(
                "generic-admit",
                selected_plan=plan,
                authority_fingerprint=fingerprint,
            ),
            deterministic_context(transition_id="transition-generic-admit"),
        ),
        (
            SelectDefaultPlan("generic-select", authority_fingerprint=fingerprint),
            deterministic_context(transition_id="transition-generic-select"),
        ),
        (
            EnqueueWork(
                "generic-enqueue",
                queue_family_id=QueueFamilyId("parent"),
                payload=generic_fanout.packet_payload(),
            ),
            deterministic_context(
                transition_id="transition-generic-enqueue",
                work_item_id="work-generic-parent",
                activation_id="activation-generic-parent",
            ),
        ),
        (
            ClaimWork("generic-claim", activation_id="activation-generic-parent"),
            deterministic_context(
                transition_id="transition-generic-claim",
                run_id="run-generic-parent",
                claim_id="claim-generic-parent",
                fencing_token="fence-generic-parent",
            ),
        ),
    )
    for transition_input, context in transitions:
        state = _apply_accepted_input(state, transition_input, context)
    return state, plan, fingerprint


def _apply_generic_observation(
    state: RuntimeState,
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    run_id: str,
    action_id: str,
    input_id: str,
    context: object,
    observed_at: int | None = None,
) -> RuntimeState:
    action = next(item for item in plan.terminal_actions if str(item.id) == action_id)
    marker = next(
        outcome.marker
        for outcome in plan.terminal_outcomes
        if outcome.id == action.outcome_id
    )
    run = state.runs[run_id]
    activation = state.activations[run.activation_id]
    return _apply_accepted_input(
        state,
        RunnerResultObserved(
            input_id,
            run_id=run_id,
            payload=fake_runner_observation_payload(
                run=run,
                activation=activation,
                plan_fingerprint=fingerprint,
                marker=marker,
                artifact_payload=generic_fanout.packet_payload(),
            ),
            observed_at=observed_at,
        ),
        context,
    )


def _generic_recovery_runtime_state() -> RuntimeState:
    state, plan, fingerprint = _generic_parent_claimed_state()
    return _apply_generic_observation(
        state,
        plan,
        fingerprint,
        run_id="run-generic-parent",
        action_id=generic_admission.RECOVERY_SOURCE_ACTION_ID,
        input_id="observe-generic-recovery",
        context=deterministic_context(
            transition_id="transition-observe-generic-recovery",
            activation_id="activation-generic-recovery",
        ),
    )


def _persist_generic_recovery_runtime_state(
    tmp_path: Path,
) -> tuple[Path, Path, RuntimeState]:
    state = _generic_recovery_runtime_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    return db_path, cas_root, state


def _generic_closed_counter_runtime_state() -> RuntimeState:
    state, plan, fingerprint = _generic_parent_claimed_state()
    closed = _apply_generic_observation(
        state,
        plan,
        fingerprint,
        run_id="run-generic-parent",
        action_id=generic_admission.COUNTER_INCREMENT_ACTION_ID,
        input_id="observe-generic-counter-close",
        context=deterministic_context(
            transition_id="transition-observe-generic-counter-close"
        ),
    )
    enqueued = _apply_accepted_input(
        closed,
        EnqueueWork(
            "enqueue-generic-auxiliary",
            queue_family_id=QueueFamilyId("child"),
            payload={"child_id": "auxiliary", "body": "Auxiliary work"},
        ),
        deterministic_context(
            transition_id="transition-enqueue-generic-auxiliary",
            work_item_id="work-generic-auxiliary",
            activation_id="activation-generic-auxiliary",
        ),
    )
    return _apply_accepted_input(
        enqueued,
        ClaimWork(
            "claim-generic-auxiliary",
            activation_id="activation-generic-auxiliary",
        ),
        deterministic_context(
            transition_id="transition-claim-generic-auxiliary",
            run_id="run-generic-auxiliary",
            claim_id="claim-generic-auxiliary",
            fencing_token="fence-generic-auxiliary",
        ),
    )


def _generic_escalation_runtime_state() -> RuntimeState:
    state, plan, fingerprint = _generic_parent_claimed_state()
    return _apply_generic_observation(
        state,
        plan,
        fingerprint,
        run_id="run-generic-parent",
        action_id=generic_admission.ESCALATION_ACTION_ID,
        input_id="observe-generic-escalation",
        context=deterministic_context(
            transition_id="transition-observe-generic-escalation"
        ),
    )


def _generic_admitted_only_state(
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    plan_ref = plan_ref_for(plan, fingerprint)
    return RuntimeState(
        admitted_plans={
            fingerprint: AdmittedPlan(plan_ref=plan_ref, selected_plan=plan),
        },
        default_plan_ref=plan_ref,
    )


def _generic_returned_parent_claimed_state() -> tuple[
    RuntimeState, SelectedCompiledPlan, str
]:
    first = _generic_recovery_runtime_state()
    plan = next(iter(first.admitted_plans.values())).selected_plan
    fingerprint = next(iter(first.admitted_plans))
    recovery_activation_id = next(
        activation.activation_id
        for activation in first.activations.values()
        if str(activation.stage_kind_id) == generic_admission.RECOVERY_STAGE_ID
    )
    claimed_recovery = _apply_accepted_input(
        first,
        ClaimWork("claim-generic-recovery", activation_id=recovery_activation_id),
        deterministic_context(
            transition_id="transition-claim-generic-recovery",
            run_id="run-generic-recovery",
            claim_id="claim-generic-recovery",
            fencing_token="fence-generic-recovery",
        ),
    )
    returned = _apply_generic_observation(
        claimed_recovery,
        plan,
        fingerprint,
        run_id="run-generic-recovery",
        action_id=generic_admission.RECOVERY_RETURN_ACTION_ID,
        input_id="observe-generic-return",
        context=deterministic_context(
            transition_id="transition-observe-generic-return",
            activation_id="activation-generic-returned-parent",
        ),
    )
    claimed_parent = _apply_accepted_input(
        returned,
        ClaimWork(
            "claim-generic-returned-parent",
            activation_id="activation-generic-returned-parent",
        ),
        deterministic_context(
            transition_id="transition-claim-generic-returned-parent",
            run_id="run-generic-returned-parent",
            claim_id="claim-generic-returned-parent",
            fencing_token="fence-generic-returned-parent",
        ),
    )
    return claimed_parent, plan, fingerprint


def _generic_cooldown_runtime_state() -> RuntimeState:
    claimed_parent, plan, fingerprint = _generic_returned_parent_claimed_state()
    return _apply_generic_observation(
        claimed_parent,
        plan,
        fingerprint,
        run_id="run-generic-returned-parent",
        action_id=generic_admission.RECOVERY_SOURCE_ACTION_ID,
        input_id="observe-generic-recovery-second",
        observed_at=1000,
        context=deterministic_context(
            transition_id="transition-observe-generic-recovery-second"
        ),
    )


def _generic_closed_after_recovery_runtime_state() -> RuntimeState:
    claimed_parent, plan, fingerprint = _generic_returned_parent_claimed_state()
    return _apply_generic_observation(
        claimed_parent,
        plan,
        fingerprint,
        run_id="run-generic-returned-parent",
        action_id=generic_admission.COUNTER_INCREMENT_ACTION_ID,
        input_id="observe-generic-counter-after-recovery",
        context=deterministic_context(
            transition_id="transition-observe-generic-counter-after-recovery"
        ),
    )


def _generic_consumed_cooldown_runtime_state() -> RuntimeState:
    waiting = _generic_cooldown_runtime_state()
    wait = next(iter(waiting.cooldown_waits.values()))
    return _apply_accepted_input(
        waiting,
        TimerDue("generic-cooldown-due", wait_id=wait.wait_id, observed_at=1900),
        deterministic_context(
            transition_id="transition-generic-cooldown-due",
            activation_id="activation-generic-recovery-resumed",
        ),
    )


def _generic_operator_wait_runtime_state(
    *, close_on_create: bool = False
) -> RuntimeState:
    state, plan, fingerprint = _generic_parent_claimed_state()
    action_id = (
        generic_admission.CLOSE_WAIT_ACTION_ID
        if close_on_create
        else generic_admission.REVISE_ACTION_ID
    )
    return _apply_generic_observation(
        state,
        plan,
        fingerprint,
        run_id="run-generic-parent",
        action_id=action_id,
        input_id="observe-generic-operator-wait",
        context=deterministic_context(
            transition_id="transition-observe-generic-operator-wait"
        ),
    )


def _generic_resolved_operator_wait_state() -> RuntimeState:
    waiting = _generic_operator_wait_runtime_state()
    wait = next(iter(waiting.operator_waits.values()))
    return _apply_accepted_input(
        waiting,
        OperatorResumeWait(
            "generic-resume-wait",
            selected_plan_ref=wait.selected_plan_ref,
            wait_id=wait.wait_id,
            lineage_id=wait.lineage_id,
            actor_id="local-operator",
            actor_kind="local_operator",
            payload={},
        ),
        deterministic_context(
            transition_id="transition-generic-resume-wait",
            activation_id="activation-generic-resumed-parent",
        ),
    )


def _generic_resumed_operator_wait_source_closed_state() -> RuntimeState:
    resumed = _generic_resolved_operator_wait_state()
    plan = next(iter(resumed.admitted_plans.values())).selected_plan
    fingerprint = next(iter(resumed.admitted_plans))
    claimed = _apply_accepted_input(
        resumed,
        ClaimWork(
            "claim-generic-resumed-parent",
            activation_id="activation-generic-resumed-parent",
        ),
        deterministic_context(
            transition_id="transition-claim-generic-resumed-parent",
            run_id="run-generic-resumed-parent",
            claim_id="claim-generic-resumed-parent",
            fencing_token="fence-generic-resumed-parent",
        ),
    )
    return _apply_generic_observation(
        claimed,
        plan,
        fingerprint,
        run_id="run-generic-resumed-parent",
        action_id=generic_admission.COUNTER_INCREMENT_ACTION_ID,
        input_id="observe-generic-resumed-close",
        context=deterministic_context(
            transition_id="transition-observe-generic-resumed-close"
        ),
    )


def _generic_revised_operator_wait_state() -> RuntimeState:
    waiting = _generic_operator_wait_runtime_state()
    wait = next(iter(waiting.operator_waits.values()))
    return _apply_accepted_input(
        waiting,
        OperatorReviseWait(
            "generic-revise-wait",
            selected_plan_ref=wait.selected_plan_ref,
            wait_id=wait.wait_id,
            lineage_id=wait.lineage_id,
            actor_id="local-operator",
            actor_kind="local_operator",
            payload=generic_fanout.packet_payload(item_ids=("revised",)),
        ),
        deterministic_context(
            transition_id="transition-generic-revise-wait",
            work_item_id="work-generic-revised-parent",
            activation_id="activation-generic-revised-parent",
        ),
    )


def _persist_generic_state(
    tmp_path: Path,
    state: RuntimeState,
) -> tuple[Path, Path, RuntimeState]:
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    return db_path, cas_root, state


def persist_dispatch_suspended_runtime_state(
    tmp_path: Path,
) -> tuple[Path, Path, RuntimeState]:
    state = taskmaster_runtime_state()
    assert state.default_plan_ref is not None
    transition_input = SuspendDispatch(
        "suspend-for-corruption",
        plan_fingerprint=state.default_plan_ref.authority_fingerprint,
        actor_id="operator-a",
        reason="corruption proof",
    )
    state = apply(
        state,
        decide(
            state,
            transition_input,
            deterministic_context(transition_id="transition-suspend-for-corruption"),
        ),
    )
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    return db_path, cas_root, state


@pytest.mark.parametrize(
    ("column", "value", "message"),
    (
        ("schema_version", 2, "dispatch_suspension.schema_version is unsupported"),
        (
            "plan_authority_fingerprint",
            f"sha256:{'0' * 64}",
            "dispatch_suspension.plan_authority_fingerprint",
        ),
        (
            "suspension_id",
            "dispatch-suspension:corrupted",
            "dispatch_suspension.suspension_id must match suspended input",
        ),
        (
            "suspended_by_input_id",
            "corrupted-suspend-input",
            "dispatch_suspension.suspension_id must match suspended input",
        ),
        (
            "dispatch_generation",
            0,
            "active dispatch_suspension dispatch generation is stale",
        ),
        ("generation", 99, "dispatch_suspension"),
        ("actor_id", "corrupted-actor", "dispatch_suspension"),
        ("reason", "corrupted reason", "dispatch_suspension"),
    ),
)
def test_corrupt_dispatch_suspension_row_refuses_without_mutation(
    tmp_path: Path,
    column: str,
    value: object,
    message: str,
) -> None:
    db_path, cas_root, state = persist_dispatch_suspended_runtime_state(tmp_path)
    assert state.dispatch_suspension is not None
    assert state.dispatch_suspension.dispatch_generation > 0
    _disable_checks_and_update_one(
        db_path,
        f"UPDATE dispatch_suspension SET {column} = ? WHERE id = 1",
        (value,),
    )
    before = _durable_bytes(tmp_path)

    with pytest.raises(StorageIntegrityError, match=message):
        load_runtime_state(db_path, cas_root)

    assert _durable_bytes(tmp_path) == before


def persist_queue_closure_runtime_state(
    tmp_path: Path,
) -> tuple[Path, Path, RuntimeState]:
    plan, fingerprint = kernel_ping_support.compile_kernel_ping()
    state = empty_runtime_state()
    for transition_input in (
        InitializeWorkspace("queue-close-init"),
        AdmitPlan(
            "queue-close-admit",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        SelectDefaultPlan(
            "queue-close-select",
            authority_fingerprint=fingerprint,
        ),
        EnqueueWork(
            "queue-close-enqueue",
            queue_family_id=QueueFamilyId("prompt"),
            payload={"prompt_id": "queue-close", "body": "close safely"},
        ),
    ):
        state = apply(
            state,
            decide(
                state,
                transition_input,
                deterministic_context(
                    transition_id=f"transition-{transition_input.input_id}",
                    work_item_id="queue-close-work",
                    activation_id="queue-close-activation",
                ),
            ),
        )
    state = apply(
        state,
        decide(
            state,
            CancelQueuedWork(
                "queue-close-input",
                work_item_id="queue-close-work",
                plan_fingerprint=fingerprint,
                actor_id="queue-operator",
                reason="corruption proof",
            ),
            deterministic_context(transition_id="transition-queue-close-input"),
        ),
    )
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    return db_path, cas_root, state


@pytest.mark.parametrize(
    ("statement", "parameters", "message"),
    (
        (
            "UPDATE queue_closures SET schema_version = ?",
            (2,),
            "queue_closures.schema_version is unsupported",
        ),
        (
            "UPDATE queue_closures SET plan_authority_fingerprint = ?",
            (f"sha256:{'0' * 64}",),
            "queue_closures.plan_authority_fingerprint",
        ),
        (
            "UPDATE queue_closures SET target_kind = ?",
            ("lineage",),
            "queue_closures.created_by_input_id",
        ),
        (
            "UPDATE queue_closures SET target_id = ?",
            ("different-work",),
            "work-item queue closure target must match closed work",
        ),
        (
            "UPDATE queue_closures SET actor_id = ?",
            ("corrupted-actor",),
            "queue_closures input payload digest",
        ),
        (
            "UPDATE queue_closures SET reason = ?",
            ("corrupted reason",),
            "queue_closures input payload digest",
        ),
        (
            "UPDATE queue_closures SET created_by_input_id = ?",
            ("other-queue-input",),
            "queue_closures must match accepted queue closure transitions",
        ),
        (
            "UPDATE queue_closures SET closed_work_item_ids_json = ?",
            ('["different-work"]',),
            "work-item queue closure target must match closed work",
        ),
        (
            "UPDATE queue_closures SET closed_activation_ids_json = ?",
            ("[]",),
            "queue_closures activation or run membership",
        ),
        (
            "UPDATE queue_closures SET closed_run_ids_json = ?",
            ('["different-run"]',),
            "queue_closures activation or run membership",
        ),
        (
            "UPDATE closed_work_items SET source_run_id = ?",
            ("different-run",),
            "queue-cancelled work cannot have runner",
        ),
        (
            "UPDATE closed_work_items SET action_id = ?",
            ("different-action",),
            "queue-cancelled work cannot have runner",
        ),
        (
            "UPDATE closed_work_items SET operator_intervention_record_id = ?",
            ("different-intervention",),
            "queue-cancelled work cannot have runner",
        ),
    ),
)
def test_corrupt_queue_closure_row_refuses_without_mutation(
    tmp_path: Path,
    statement: str,
    parameters: tuple[object, ...],
    message: str,
) -> None:
    db_path, cas_root, _state = persist_queue_closure_runtime_state(tmp_path)
    _disable_checks_and_update_one(db_path, statement, parameters)
    before = _durable_bytes(tmp_path)

    with pytest.raises(StorageIntegrityError, match=message):
        load_runtime_state(db_path, cas_root)

    assert _durable_bytes(tmp_path) == before


def _dispatch_suspension_lifecycle_state() -> tuple[RuntimeState, RuntimeState]:
    state = taskmaster_runtime_state()
    assert state.default_plan_ref is not None
    fingerprint = state.default_plan_ref.authority_fingerprint
    first_suspended = apply(
        state,
        decide(
            state,
            SuspendDispatch(
                "suspend-lifecycle-one",
                plan_fingerprint=fingerprint,
                actor_id="operator-one",
                reason="first maintenance window",
            ),
            deterministic_context(transition_id="transition-suspend-lifecycle-one"),
        ),
    )
    first_resumed = apply(
        first_suspended,
        decide(
            first_suspended,
            ResumeDispatch(
                "resume-lifecycle-one",
                plan_fingerprint=fingerprint,
                suspension_id=first_suspended.dispatch_suspension.suspension_id,
                actor_id="operator-one",
                reason="first maintenance complete",
            ),
            deterministic_context(transition_id="transition-resume-lifecycle-one"),
        ),
    )
    second_suspended = apply(
        first_resumed,
        decide(
            first_resumed,
            SuspendDispatch(
                "suspend-lifecycle-two",
                plan_fingerprint=fingerprint,
                actor_id="operator-two",
                reason="second maintenance window",
            ),
            deterministic_context(transition_id="transition-suspend-lifecycle-two"),
        ),
    )
    second_resumed = apply(
        second_suspended,
        decide(
            second_suspended,
            ResumeDispatch(
                "resume-lifecycle-two",
                plan_fingerprint=fingerprint,
                suspension_id=second_suspended.dispatch_suspension.suspension_id,
                actor_id="operator-two",
                reason="second maintenance complete",
            ),
            deterministic_context(transition_id="transition-resume-lifecycle-two"),
        ),
    )
    return first_resumed, second_resumed


def test_restart_refuses_dispatch_suspension_lifecycle_rollback_without_mutation(
    tmp_path: Path,
) -> None:
    first_resumed, latest_resumed = _dispatch_suspension_lifecycle_state()
    first = first_resumed.dispatch_suspension
    latest = latest_resumed.dispatch_suspension
    assert first is not None
    assert latest is not None
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, latest_resumed)
    _disable_checks_and_update_one(
        db_path,
        """
        UPDATE dispatch_suspension
        SET suspension_id = ?, generation = ?, dispatch_generation = ?,
            actor_id = ?, reason = ?, suspended_by_input_id = ?,
            resumed_by_input_id = ?, resume_actor_id = ?, resume_reason = ?
        WHERE id = 1
        """,
        (
            first.suspension_id,
            first.generation,
            first.dispatch_generation,
            first.actor_id,
            first.reason,
            first.suspended_by_input_id,
            first.resumed_by_input_id,
            first.resume_actor_id,
            first.resume_reason,
        ),
    )
    before = _durable_bytes(tmp_path)

    with pytest.raises(StorageIntegrityError, match="dispatch_suspension"):
        load_runtime_state(db_path, cas_root)

    assert _durable_bytes(tmp_path) == before


@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("resume_actor_id", "corrupted-resume-actor"),
        ("resume_reason", "corrupted resume reason"),
        ("resumed_by_input_id", "resume-lifecycle-one"),
    ),
)
def test_restart_refuses_dispatch_suspension_resume_metadata_drift_without_mutation(
    tmp_path: Path,
    column: str,
    value: object,
) -> None:
    _first_resumed, latest_resumed = _dispatch_suspension_lifecycle_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, latest_resumed)
    _disable_checks_and_update_one(
        db_path,
        f"UPDATE dispatch_suspension SET {column} = ? WHERE id = 1",
        (value,),
    )
    before = _durable_bytes(tmp_path)

    with pytest.raises(StorageIntegrityError, match="dispatch_suspension"):
        load_runtime_state(db_path, cas_root)

    assert _durable_bytes(tmp_path) == before


@pytest.mark.parametrize(
    "column",
    ("resumed_by_input_id", "resume_actor_id", "resume_reason"),
)
def test_restart_refuses_incomplete_resumed_dispatch_metadata_without_mutation(
    tmp_path: Path,
    column: str,
) -> None:
    _first_resumed, latest_resumed = _dispatch_suspension_lifecycle_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, latest_resumed)
    _disable_checks_and_update_one(
        db_path,
        f"UPDATE dispatch_suspension SET {column} = NULL WHERE id = 1",
    )
    before = _durable_bytes(tmp_path)

    with pytest.raises(StorageIntegrityError, match="dispatch_suspension"):
        load_runtime_state(db_path, cas_root)

    assert _durable_bytes(tmp_path) == before


def test_restart_refuses_resumed_dispatch_generation_from_later_claim(
    tmp_path: Path,
) -> None:
    _first_resumed, state = _dispatch_suspension_lifecycle_state()
    assert state.default_plan_ref is not None
    state = apply(
        state,
        decide(
            state,
            EnqueueWork(
                "enqueue-after-resume",
                queue_family_id=QueueFamilyId("prompt"),
                payload={"body": "claim after resumed dispatch"},
            ),
            deterministic_context(
                transition_id="transition-enqueue-after-resume",
                work_item_id="work-after-resume",
                activation_id="activation-after-resume",
            ),
        ),
    )
    state = apply(
        state,
        decide(
            state,
            ClaimWork(
                "claim-after-resume",
                activation_id="activation-after-resume",
            ),
            deterministic_context(
                transition_id="transition-claim-after-resume",
                run_id="run-after-resume",
                claim_id="claim-after-resume",
                fencing_token="fence-after-resume",
            ),
        ),
    )
    assert state.dispatch_suspension is not None
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    _disable_checks_and_update_one(
        db_path,
        "UPDATE dispatch_suspension SET dispatch_generation = ? WHERE id = 1",
        (len(state.runs),),
    )
    before = _durable_bytes(tmp_path)

    with pytest.raises(StorageIntegrityError, match="dispatch_suspension"):
        load_runtime_state(db_path, cas_root)

    assert _durable_bytes(tmp_path) == before


def _persist_intrinsic_daemon_budget_records(
    tmp_path: Path,
) -> tuple[Path, Path, str, str]:
    state = worker_runtime_state()
    assert state.default_plan_ref is not None
    session = next(iter(state.runner_sessions.values()))
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    store = SQLiteRuntimeStore.open(db_path)
    try:
        epoch = DaemonBudgetEpochRecord(
            budget_id="budget-intrinsic-records",
            workspace_path=str(tmp_path),
            selected_plan_ref=state.default_plan_ref,
            max_wall_seconds=10,
            max_invocations=5,
            max_total_tokens=100,
            started_at=10,
            wall_deadline=20,
            last_observed_at=10,
        )
        store.create_or_resume_daemon_budget_epoch(epoch)
        store.reserve_budgeted_runner_start(epoch.budget_id, session)
        store.record_budgeted_runner_start(epoch.budget_id, session)
        store.record_runner_session_usage(
            RunnerSessionUsageRecord(
                budget_id=epoch.budget_id,
                session_id=session.session_id,
                run_id=session.run_id,
                dispatch_generation=session.dispatch_generation,
                session_fencing_token=session.session_fencing_token,
                input_tokens=1,
                output_tokens=2,
                total_tokens=3,
                observed_at=10,
                final=False,
            )
        )
    finally:
        store.close()
    return db_path, cas_root, epoch.budget_id, session.session_id


@pytest.mark.parametrize(
    ("changes", "match"),
    (
        ({"budget_id": ""}, "budget_id must be non-blank"),
        (
            {"workspace_path": "x" * (RUNNER_SESSION_TEXT_MAX_BYTES + 1)},
            "workspace_path must be at most",
        ),
        (
            {
                "max_wall_seconds": None,
                "max_invocations": None,
                "max_total_tokens": None,
            },
            "requires at least one limit",
        ),
        ({"max_wall_seconds": True}, "max_wall_seconds must be"),
        ({"max_wall_seconds": 0}, "max_wall_seconds must be"),
        ({"max_wall_seconds": -1}, "max_wall_seconds must be"),
        (
            {"max_wall_seconds": DURABLE_INT64_MAX + 1},
            "max_wall_seconds must be",
        ),
        ({"max_invocations": True}, "max_invocations must be"),
        ({"max_invocations": 0}, "max_invocations must be"),
        ({"max_invocations": -1}, "max_invocations must be"),
        (
            {"max_invocations": DURABLE_INT64_MAX + 1},
            "max_invocations must be",
        ),
        ({"max_total_tokens": True}, "max_total_tokens must be"),
        ({"max_total_tokens": 0}, "max_total_tokens must be"),
        ({"max_total_tokens": -1}, "max_total_tokens must be"),
        (
            {"max_total_tokens": DURABLE_INT64_MAX + 1},
            "max_total_tokens must be",
        ),
        ({"started_at": True}, "started_at must be"),
        ({"started_at": -1}, "started_at must be"),
        ({"started_at": DURABLE_INT64_MAX + 1}, "started_at must be"),
        ({"last_observed_at": True}, "last_observed_at must be"),
        ({"last_observed_at": -1}, "last_observed_at must be"),
        (
            {"last_observed_at": DURABLE_INT64_MAX + 1},
            "last_observed_at must be",
        ),
        (
            {"last_observed_at": 9},
            "last_observed_at cannot precede started_at",
        ),
        ({"wall_deadline": 21}, "wall_deadline must equal"),
        ({"accepted_start_count": True}, "accepted_start_count must be"),
        ({"accepted_start_count": -1}, "accepted_start_count must be"),
        (
            {"accepted_start_count": DURABLE_INT64_MAX + 1},
            "accepted_start_count must be",
        ),
        ({"cumulative_input_tokens": True}, "cumulative_input_tokens must be"),
        ({"cumulative_input_tokens": -1}, "cumulative_input_tokens must be"),
        (
            {"cumulative_input_tokens": DURABLE_INT64_MAX + 1},
            "cumulative_input_tokens must be",
        ),
        ({"cumulative_output_tokens": True}, "cumulative_output_tokens must be"),
        ({"cumulative_output_tokens": -1}, "cumulative_output_tokens must be"),
        (
            {"cumulative_output_tokens": DURABLE_INT64_MAX + 1},
            "cumulative_output_tokens must be",
        ),
        ({"cumulative_total_tokens": True}, "cumulative_total_tokens must be"),
        ({"cumulative_total_tokens": -1}, "cumulative_total_tokens must be"),
        (
            {"cumulative_total_tokens": DURABLE_INT64_MAX + 1},
            "cumulative_total_tokens must be",
        ),
        ({"cumulative_total_tokens": 1}, "token counters are contradictory"),
        ({"status": "unknown"}, "unsupported daemon budget status"),
        (
            {"status": "stopped", "terminal_reason": None},
            "requires terminal_reason",
        ),
        (
            {
                "status": "stopped",
                "terminal_reason": "x" * (RUNNER_SESSION_TEXT_MAX_BYTES + 1),
            },
            "terminal_reason must be at most",
        ),
    ),
)
def test_daemon_budget_epoch_constructor_refuses_intrinsic_field_boundaries(
    changes: dict[str, object],
    match: str,
) -> None:
    state = worker_runtime_state()
    assert state.default_plan_ref is not None
    epoch = DaemonBudgetEpochRecord(
        budget_id="budget-constructor",
        workspace_path="/workspace",
        selected_plan_ref=state.default_plan_ref,
        max_wall_seconds=10,
        max_invocations=1,
        max_total_tokens=10,
        started_at=10,
        wall_deadline=20,
        last_observed_at=10,
    )

    with pytest.raises(ValueError, match=match):
        replace(epoch, **changes)


@pytest.mark.parametrize(
    ("changes", "match"),
    (
        ({"budget_id": ""}, "budget_id must be non-blank"),
        (
            {"session_id": "x" * (RUNNER_SESSION_TEXT_MAX_BYTES + 1)},
            "session_id must be at most",
        ),
        ({"run_id": ""}, "run_id must be non-blank"),
        (
            {"session_fencing_token": ("x" * (RUNNER_SESSION_TEXT_MAX_BYTES + 1))},
            "session_fencing_token must be at most",
        ),
        ({"dispatch_generation": True}, "dispatch_generation is outside"),
        ({"dispatch_generation": 0}, "dispatch_generation is outside"),
        ({"dispatch_generation": -1}, "dispatch_generation is outside"),
        (
            {"dispatch_generation": DURABLE_INT64_MAX + 1},
            "dispatch_generation is outside",
        ),
        ({"input_tokens": True}, "input_tokens is outside"),
        ({"input_tokens": -1}, "input_tokens is outside"),
        ({"input_tokens": DURABLE_INT64_MAX + 1}, "input_tokens is outside"),
        ({"output_tokens": True}, "output_tokens is outside"),
        ({"output_tokens": -1}, "output_tokens is outside"),
        ({"output_tokens": DURABLE_INT64_MAX + 1}, "output_tokens is outside"),
        ({"total_tokens": True}, "total_tokens is outside"),
        ({"total_tokens": -1}, "total_tokens is outside"),
        ({"total_tokens": DURABLE_INT64_MAX + 1}, "total_tokens is outside"),
        ({"observed_at": True}, "observed_at is outside"),
        ({"observed_at": -1}, "observed_at is outside"),
        ({"observed_at": DURABLE_INT64_MAX + 1}, "observed_at is outside"),
        ({"total_tokens": 4}, "token counters are contradictory"),
        ({"final": 1}, "final must be a boolean"),
    ),
)
def test_runner_session_usage_constructor_refuses_intrinsic_field_boundaries(
    changes: dict[str, object],
    match: str,
) -> None:
    usage = RunnerSessionUsageRecord(
        budget_id="budget-constructor",
        session_id="session-constructor",
        run_id="run-constructor",
        dispatch_generation=1,
        session_fencing_token="fence-constructor",
        input_tokens=1,
        output_tokens=2,
        total_tokens=3,
        observed_at=10,
        final=False,
    )

    with pytest.raises(ValueError, match=match):
        replace(usage, **changes)


@pytest.mark.parametrize(
    ("statement", "parameters"),
    (
        ("UPDATE daemon_budget_epochs SET schema_version = ?", (2,)),
        (
            "UPDATE daemon_budget_epochs SET workspace_path = ?",
            ("x" * (RUNNER_SESSION_TEXT_MAX_BYTES + 1),),
        ),
        (
            "UPDATE daemon_budget_epochs SET max_wall_seconds = ?",
            (sqlite3.Binary(b"not-an-integer"),),
        ),
        ("UPDATE daemon_budget_epochs SET max_wall_seconds = ?", (0,)),
        ("UPDATE daemon_budget_epochs SET max_wall_seconds = ?", (-1,)),
        (
            "UPDATE daemon_budget_epochs SET max_wall_seconds = ?",
            (float(2**63),),
        ),
        (
            "UPDATE daemon_budget_epochs SET max_invocations = ?",
            (sqlite3.Binary(b"not-an-integer"),),
        ),
        ("UPDATE daemon_budget_epochs SET max_invocations = ?", (0,)),
        ("UPDATE daemon_budget_epochs SET max_invocations = ?", (-1,)),
        (
            "UPDATE daemon_budget_epochs SET max_invocations = ?",
            (float(2**63),),
        ),
        (
            "UPDATE daemon_budget_epochs SET max_total_tokens = ?",
            (sqlite3.Binary(b"not-an-integer"),),
        ),
        ("UPDATE daemon_budget_epochs SET max_total_tokens = ?", (0,)),
        ("UPDATE daemon_budget_epochs SET max_total_tokens = ?", (-1,)),
        (
            "UPDATE daemon_budget_epochs SET max_total_tokens = ?",
            (float(2**63),),
        ),
        (
            "UPDATE daemon_budget_epochs SET started_at = ?",
            (sqlite3.Binary(b"not-an-integer"),),
        ),
        ("UPDATE daemon_budget_epochs SET started_at = ?", (-1,)),
        ("UPDATE daemon_budget_epochs SET started_at = ?", (float(2**63),)),
        (
            "UPDATE daemon_budget_epochs SET wall_deadline = ?",
            (sqlite3.Binary(b"not-an-integer"),),
        ),
        ("UPDATE daemon_budget_epochs SET wall_deadline = ?", (-1,)),
        ("UPDATE daemon_budget_epochs SET wall_deadline = ?", (float(2**63),)),
        ("UPDATE daemon_budget_epochs SET wall_deadline = ?", (21,)),
        (
            "UPDATE daemon_budget_epochs SET last_observed_at = ?",
            (sqlite3.Binary(b"not-an-integer"),),
        ),
        ("UPDATE daemon_budget_epochs SET last_observed_at = ?", (9,)),
        (
            "UPDATE daemon_budget_epochs SET last_observed_at = ?",
            (float(2**63),),
        ),
        (
            "UPDATE daemon_budget_epochs SET accepted_start_count = ?",
            (sqlite3.Binary(b"not-an-integer"),),
        ),
        ("UPDATE daemon_budget_epochs SET accepted_start_count = ?", (-1,)),
        (
            "UPDATE daemon_budget_epochs SET accepted_start_count = ?",
            (float(2**63),),
        ),
        (
            "UPDATE daemon_budget_epochs SET cumulative_input_tokens = ?",
            (sqlite3.Binary(b"not-an-integer"),),
        ),
        ("UPDATE daemon_budget_epochs SET cumulative_input_tokens = ?", (-1,)),
        (
            "UPDATE daemon_budget_epochs SET cumulative_input_tokens = ?",
            (float(2**63),),
        ),
        (
            "UPDATE daemon_budget_epochs SET cumulative_output_tokens = ?",
            (sqlite3.Binary(b"not-an-integer"),),
        ),
        ("UPDATE daemon_budget_epochs SET cumulative_output_tokens = ?", (-1,)),
        (
            "UPDATE daemon_budget_epochs SET cumulative_output_tokens = ?",
            (float(2**63),),
        ),
        (
            "UPDATE daemon_budget_epochs SET cumulative_total_tokens = ?",
            (sqlite3.Binary(b"not-an-integer"),),
        ),
        ("UPDATE daemon_budget_epochs SET cumulative_total_tokens = ?", (-1,)),
        (
            "UPDATE daemon_budget_epochs SET cumulative_total_tokens = ?",
            (float(2**63),),
        ),
        ("UPDATE daemon_budget_epochs SET cumulative_total_tokens = ?", (4,)),
        ("UPDATE daemon_budget_epochs SET status = ?", ("unknown",)),
        (
            """
            UPDATE daemon_budget_epochs
            SET status = 'stopped', terminal_reason = ?
            """,
            ("x" * (RUNNER_SESSION_TEXT_MAX_BYTES + 1),),
        ),
    ),
)
def test_raw_daemon_budget_epoch_intrinsic_corruption_refuses_without_mutation(
    tmp_path: Path,
    statement: str,
    parameters: tuple[object, ...],
) -> None:
    db_path, _cas_root, budget_id, _session_id = (
        _persist_intrinsic_daemon_budget_records(tmp_path)
    )
    _disable_checks_and_update_one(db_path, statement, parameters)
    before = _durable_bytes(tmp_path)
    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(
            ValueError,
            match=(
                "daemon budget epoch schema version is unsupported"
                "|invalid daemon budget epoch row"
            ),
        ):
            store.load_daemon_budget_epoch(budget_id)
    finally:
        store.close()

    assert _durable_bytes(tmp_path) == before


@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("schema_version", 2),
        ("budget_id", sqlite3.Binary(b"not-text")),
        ("budget_id", "x" * (RUNNER_SESSION_TEXT_MAX_BYTES + 1)),
        ("run_id", sqlite3.Binary(b"not-text")),
        ("run_id", "x" * (RUNNER_SESSION_TEXT_MAX_BYTES + 1)),
        ("dispatch_generation", sqlite3.Binary(b"not-an-integer")),
        ("dispatch_generation", 0),
        ("dispatch_generation", -1),
        ("dispatch_generation", float(2**63)),
        ("session_fencing_token", sqlite3.Binary(b"not-text")),
        (
            "session_fencing_token",
            "x" * (RUNNER_SESSION_TEXT_MAX_BYTES + 1),
        ),
        ("accepted_at", sqlite3.Binary(b"not-an-integer")),
        ("accepted_at", -1),
        ("accepted_at", float(2**63)),
    ),
)
def test_raw_daemon_budget_binding_intrinsic_corruption_refuses_without_mutation(
    tmp_path: Path,
    column: str,
    value: object,
) -> None:
    db_path, _cas_root, _budget_id, session_id = (
        _persist_intrinsic_daemon_budget_records(tmp_path)
    )
    _disable_checks_and_update_one(
        db_path,
        f"UPDATE daemon_budget_sessions SET {column} = ?",
        (value,),
    )
    before = _durable_bytes(tmp_path)
    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(ValueError, match="invalid daemon budget session binding"):
            store.daemon_budget_id_for_session(session_id)
    finally:
        store.close()

    assert _durable_bytes(tmp_path) == before


@pytest.mark.parametrize(
    ("column", "value", "mirror_binding"),
    (
        ("schema_version", 2, False),
        ("budget_id", sqlite3.Binary(b"not-text"), True),
        (
            "budget_id",
            "x" * (RUNNER_SESSION_TEXT_MAX_BYTES + 1),
            True,
        ),
        ("run_id", sqlite3.Binary(b"not-text"), True),
        ("run_id", "x" * (RUNNER_SESSION_TEXT_MAX_BYTES + 1), True),
        ("dispatch_generation", sqlite3.Binary(b"not-an-integer"), True),
        ("dispatch_generation", 0, True),
        ("dispatch_generation", -1, True),
        ("dispatch_generation", float(2**63), True),
        ("session_fencing_token", sqlite3.Binary(b"not-text"), True),
        (
            "session_fencing_token",
            "x" * (RUNNER_SESSION_TEXT_MAX_BYTES + 1),
            True,
        ),
        ("input_tokens", sqlite3.Binary(b"not-an-integer"), False),
        ("input_tokens", -1, False),
        ("input_tokens", float(2**63), False),
        ("output_tokens", sqlite3.Binary(b"not-an-integer"), False),
        ("output_tokens", -1, False),
        ("output_tokens", float(2**63), False),
        ("total_tokens", sqlite3.Binary(b"not-an-integer"), False),
        ("total_tokens", -1, False),
        ("total_tokens", float(2**63), False),
        ("total_tokens", 4, False),
        ("observed_at", sqlite3.Binary(b"not-an-integer"), False),
        ("observed_at", -1, False),
        ("observed_at", float(2**63), False),
        ("final", sqlite3.Binary(b"not-an-integer"), False),
        ("final", 2, False),
    ),
)
def test_raw_runner_usage_intrinsic_corruption_refuses_without_mutation(
    tmp_path: Path,
    column: str,
    value: object,
    mirror_binding: bool,
) -> None:
    db_path, _cas_root, _budget_id, session_id = (
        _persist_intrinsic_daemon_budget_records(tmp_path)
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA ignore_check_constraints = ON")
        cursor = connection.execute(
            f"UPDATE runner_session_usage SET {column} = ?",
            (value,),
        )
        assert cursor.rowcount == 1
        if mirror_binding:
            cursor = connection.execute(
                f"UPDATE daemon_budget_sessions SET {column} = ?",
                (value,),
            )
            assert cursor.rowcount == 1
    before = _durable_bytes(tmp_path)
    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(
            ValueError,
            match=(
                "runner session usage schema version is unsupported"
                "|invalid runner session usage row"
            ),
        ):
            store.load_runner_session_usage(session_id)
    finally:
        store.close()

    assert _durable_bytes(tmp_path) == before


def test_raw_daemon_budget_usage_sum_overflow_refuses_without_mutation(
    tmp_path: Path,
) -> None:
    db_path, _cas_root, budget_id, _session_id = (
        _persist_intrinsic_daemon_budget_records(tmp_path)
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """
            INSERT INTO runner_session_usage (
                session_id, schema_version, budget_id, run_id,
                dispatch_generation, session_fencing_token,
                input_tokens, output_tokens, total_tokens, observed_at, final
            ) VALUES (?, 1, ?, ?, 1, ?, ?, 0, ?, 10, 1)
            """,
            (
                "corrupt-extra-session",
                budget_id,
                "corrupt-extra-run",
                "corrupt-extra-fence",
                DURABLE_INT64_MAX,
                DURABLE_INT64_MAX,
            ),
        )
    before = _durable_bytes(tmp_path)
    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(
            ValueError,
            match="invalid daemon budget aggregate authority",
        ):
            store.load_daemon_budget_epoch(budget_id)
    finally:
        store.close()

    assert _durable_bytes(tmp_path) == before


@pytest.mark.parametrize(
    ("table_name", "column", "missing_value"),
    (
        pytest.param(
            "daemon_budget_epochs",
            "plan_authority_fingerprint",
            "sha256:" + ("0" * 64),
            id="epoch-missing-selected-plan",
        ),
        pytest.param(
            "daemon_budget_sessions",
            "budget_id",
            "missing-budget",
            id="binding-missing-epoch",
        ),
        pytest.param(
            "daemon_budget_sessions",
            "session_id",
            "missing-session",
            id="binding-missing-runner-session",
        ),
        pytest.param(
            "daemon_budget_sessions",
            "run_id",
            "missing-run",
            id="binding-missing-run",
        ),
        pytest.param(
            "runner_session_usage",
            "budget_id",
            "missing-budget",
            id="usage-missing-epoch",
        ),
        pytest.param(
            "runner_session_usage",
            "session_id",
            "missing-session",
            id="usage-missing-runner-session",
        ),
        pytest.param(
            "runner_session_usage",
            "run_id",
            "missing-run",
            id="usage-missing-run",
        ),
    ),
)
def test_daemon_budget_missing_relationship_is_deferred_and_rolls_back(
    tmp_path: Path,
    table_name: str,
    column: str,
    missing_value: str,
) -> None:
    db_path, _cas_root, _budget_id, _session_id = (
        _persist_intrinsic_daemon_budget_records(tmp_path)
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        before = _daemon_budget_relationship_rows(connection)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("PRAGMA defer_foreign_keys = ON")
        cursor = connection.execute(
            f"UPDATE {table_name} SET {column} = ?",
            (missing_value,),
        )
        assert cursor.rowcount == 1
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint"):
            connection.execute("COMMIT")
        assert connection.in_transaction is True
        connection.execute("ROLLBACK")
        assert _daemon_budget_relationship_rows(connection) == before


@pytest.mark.parametrize(
    "case",
    (
        "missing_run",
        "missing_runner_session",
        "missing_epoch",
        "mismatched_selected_plan",
        "mismatched_run",
        "mismatched_dispatch_generation",
        "mismatched_fencing_token",
        "mismatched_accepted_timestamp",
    ),
)
def test_raw_daemon_budget_binding_relationship_refuses_without_mutation(
    tmp_path: Path,
    case: str,
) -> None:
    db_path, _cas_root, budget_id, session_id = (
        _persist_intrinsic_daemon_budget_records(tmp_path)
    )
    lookup_session_id = session_id
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        if case == "missing_run":
            connection.execute(
                "UPDATE daemon_budget_sessions SET run_id = 'missing-run'"
            )
        elif case == "missing_runner_session":
            lookup_session_id = "missing-session"
            connection.execute(
                "UPDATE daemon_budget_sessions SET session_id = ?",
                (lookup_session_id,),
            )
        elif case == "missing_epoch":
            connection.execute(
                "UPDATE daemon_budget_sessions SET budget_id = 'missing-budget'"
            )
        elif case == "mismatched_selected_plan":
            connection.execute("UPDATE daemon_budget_epochs SET plan_id = 'wrong-plan'")
        elif case == "mismatched_run":
            (run_id,) = connection.execute(
                """
                SELECT run_id
                FROM runs
                WHERE run_id != (
                    SELECT run_id FROM daemon_budget_sessions LIMIT 1
                )
                ORDER BY run_id
                LIMIT 1
                """
            ).fetchone()
            connection.execute(
                "UPDATE daemon_budget_sessions SET run_id = ?",
                (run_id,),
            )
        elif case == "mismatched_dispatch_generation":
            connection.execute(
                """
                UPDATE daemon_budget_sessions
                SET dispatch_generation = dispatch_generation + 1
                """
            )
        elif case == "mismatched_fencing_token":
            connection.execute(
                """
                UPDATE daemon_budget_sessions
                SET session_fencing_token = 'wrong-fence'
                """
            )
        else:
            connection.execute(
                """
                UPDATE daemon_budget_sessions
                SET accepted_at = 2
                """
            )
    before = _durable_bytes(tmp_path)
    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(
            ValueError,
            match=(
                "daemon budget epoch is missing"
                "|daemon budget plan pin is invalid"
                "|runner_session_budget_identity_mismatch"
            ),
        ):
            store.daemon_budget_id_for_session(lookup_session_id)
    finally:
        store.close()

    assert _durable_bytes(tmp_path) == before


@pytest.mark.parametrize(
    "case",
    (
        "missing_run",
        "missing_runner_session",
        "missing_epoch",
        "mismatched_selected_plan",
        "mismatched_run",
        "mismatched_dispatch_generation",
        "mismatched_fencing_token",
    ),
)
def test_raw_runner_usage_relationship_refuses_without_mutation(
    tmp_path: Path,
    case: str,
) -> None:
    db_path, _cas_root, _budget_id, session_id = (
        _persist_intrinsic_daemon_budget_records(tmp_path)
    )
    lookup_session_id = session_id
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        if case == "missing_run":
            for table_name in ("daemon_budget_sessions", "runner_session_usage"):
                connection.execute(f"UPDATE {table_name} SET run_id = 'missing-run'")
        elif case == "missing_runner_session":
            lookup_session_id = "missing-session"
            for table_name in ("daemon_budget_sessions", "runner_session_usage"):
                connection.execute(
                    f"UPDATE {table_name} SET session_id = ?",
                    (lookup_session_id,),
                )
        elif case == "missing_epoch":
            for table_name in ("daemon_budget_sessions", "runner_session_usage"):
                connection.execute(
                    f"UPDATE {table_name} SET budget_id = 'missing-budget'"
                )
        elif case == "mismatched_selected_plan":
            connection.execute("UPDATE daemon_budget_epochs SET plan_id = 'wrong-plan'")
        elif case == "mismatched_run":
            (run_id,) = connection.execute(
                """
                SELECT run_id
                FROM runs
                WHERE run_id != (
                    SELECT run_id FROM daemon_budget_sessions LIMIT 1
                )
                ORDER BY run_id
                LIMIT 1
                """
            ).fetchone()
            for table_name in ("daemon_budget_sessions", "runner_session_usage"):
                connection.execute(
                    f"UPDATE {table_name} SET run_id = ?",
                    (run_id,),
                )
        elif case == "mismatched_dispatch_generation":
            for table_name in ("daemon_budget_sessions", "runner_session_usage"):
                connection.execute(
                    f"""
                    UPDATE {table_name}
                    SET dispatch_generation = dispatch_generation + 1
                    """
                )
        else:
            for table_name in ("daemon_budget_sessions", "runner_session_usage"):
                connection.execute(
                    f"""
                    UPDATE {table_name}
                    SET session_fencing_token = 'wrong-fence'
                    """
                )
    before = _durable_bytes(tmp_path)
    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(ValueError, match="runner_usage_evidence_refused"):
            store.load_runner_session_usage(lookup_session_id)
    finally:
        store.close()

    assert _durable_bytes(tmp_path) == before


@pytest.mark.parametrize(
    "case",
    ("mismatched_run", "mismatched_dispatch_generation", "mismatched_fencing_token"),
)
def test_budget_binding_transaction_refuses_wrong_session_authority(
    tmp_path: Path,
    case: str,
) -> None:
    state = worker_runtime_state()
    assert state.default_plan_ref is not None
    session = next(iter(state.runner_sessions.values()))
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    store = SQLiteRuntimeStore.open(db_path)
    epoch = DaemonBudgetEpochRecord(
        budget_id="budget-binding-transaction",
        workspace_path=str(tmp_path),
        selected_plan_ref=state.default_plan_ref,
        max_wall_seconds=None,
        max_invocations=2,
        max_total_tokens=None,
        started_at=10,
        wall_deadline=None,
        last_observed_at=10,
    )
    store.create_or_resume_daemon_budget_epoch(epoch)
    if case == "mismatched_run":
        (run_id,) = store._connection.execute(
            """
            SELECT run_id
            FROM runs
            WHERE run_id != ?
            ORDER BY run_id
            LIMIT 1
            """,
            (session.run_id,),
        ).fetchone()
        candidate = replace(session, run_id=run_id)
    elif case == "mismatched_dispatch_generation":
        candidate = replace(
            session,
            dispatch_generation=session.dispatch_generation + 1,
        )
    else:
        candidate = replace(session, session_fencing_token="wrong-fence")
    before = _durable_bytes(tmp_path)
    try:
        with pytest.raises(
            ValueError,
            match="runner_session_budget_identity_mismatch",
        ):
            store.reserve_budgeted_runner_start(epoch.budget_id, candidate)
    finally:
        store.close()

    assert _durable_bytes(tmp_path) == before


def _disable_checks_and_update_one(
    db_path: Path,
    statement: str,
    parameters: tuple[object, ...] = (),
) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA ignore_check_constraints = ON")
        cursor = connection.execute(statement, parameters)
        assert cursor.rowcount == 1


def _disable_checks_and_execute(
    db_path: Path,
    statement: str,
    parameters: tuple[object, ...],
) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(statement, parameters)


def _daemon_budget_relationship_rows(
    connection: sqlite3.Connection,
) -> tuple[tuple[object, ...], ...]:
    rows: list[tuple[object, ...]] = []
    for table_name in (
        "admitted_plan_pins",
        "runs",
        "runner_sessions",
        "daemon_budget_epochs",
        "daemon_budget_sessions",
        "runner_session_usage",
    ):
        rows.extend(
            (table_name, *row)
            for row in connection.execute(
                f"SELECT * FROM {table_name} ORDER BY 1"
            ).fetchall()
        )
    return tuple(rows)


def _generic_claimed_recovery_state() -> RuntimeState:
    state = _generic_recovery_runtime_state()
    activation = next(
        item
        for item in state.activations.values()
        if str(item.stage_kind_id) == generic_admission.RECOVERY_STAGE_ID
    )
    return _apply_accepted_input(
        state,
        ClaimWork("claim-generic-recovery", activation_id=activation.activation_id),
        deterministic_context(
            transition_id="transition-claim-generic-recovery",
            run_id="run-generic-recovery",
            claim_id="claim-generic-recovery",
            fencing_token="fence-generic-recovery",
        ),
    )


def _generic_operator_needed_quarantine_state() -> RuntimeState:
    claimed = _generic_claimed_recovery_state()
    plan = next(iter(claimed.admitted_plans.values())).selected_plan
    fingerprint = next(iter(claimed.admitted_plans))
    return _apply_generic_observation(
        claimed,
        plan,
        fingerprint,
        run_id="run-generic-recovery",
        action_id=generic_admission.RECOVERY_QUARANTINE_ACTION_ID,
        input_id="observe-generic-quarantine",
        context=deterministic_context(
            transition_id="transition-observe-generic-quarantine"
        ),
    )


def _generic_lineage_intervention_state(kind: str) -> RuntimeState:
    quarantined = _generic_operator_needed_quarantine_state()
    quarantine = next(iter(quarantined.lineage_quarantines.values()))
    input_id = f"operator-{kind}-generic-lineage"
    common = {
        "input_id": input_id,
        "option_id": f"admission.{kind}",
        "selected_plan_ref": quarantine.selected_plan_ref,
        "quarantine_id": quarantine.quarantine_id,
        "lineage_id": None,
        "actor_id": "local-operator",
        "actor_kind": "local_operator",
        "reason": f"operator {kind}d generic lineage",
    }
    if kind == "resume":
        constructor = getattr(operator_api, "OperatorResumeLineageInput")
        transition_input = operator_api.build_resume_lineage(
            quarantined,
            constructor(**common, payload=None),
        )
        context = deterministic_context(
            transition_id=f"transition-{input_id}",
            activation_id="activation-generic-operator-resumed",
        )
    elif kind == "close":
        constructor = getattr(operator_api, "OperatorCloseLineageInput")
        transition_input = operator_api.build_close_lineage(
            quarantined,
            constructor(**common, payload={}),
        )
        context = deterministic_context(transition_id=f"transition-{input_id}")
    else:
        constructor = getattr(operator_api, "OperatorReviseLineageInput")
        transition_input = operator_api.build_revise_lineage(
            quarantined,
            constructor(
                **common,
                payload=generic_fanout.packet_payload(item_ids=("revised",)),
            ),
        )
        context = deterministic_context(
            transition_id=f"transition-{input_id}",
            work_item_id="work-generic-operator-revised",
            activation_id="activation-generic-operator-revised",
        )
    return _apply_accepted_input(quarantined, transition_input, context)


def test_generic_restart_preserves_first_recovery_attempt_state(
    tmp_path: Path,
) -> None:
    state = _generic_recovery_runtime_state()
    loaded = persist_and_load_runtime_state(tmp_path, state)
    attempt = next(iter(loaded.recovery_attempts.values()))
    assert loaded.recovery_attempts == state.recovery_attempts
    assert str(attempt.policy_id) == generic_admission.RECOVERY_POLICY_ID
    assert attempt.attempt_count == 1
    assert attempt.phase == "active_recovery"


def test_generic_restart_preserves_claimed_recovery_attempt_latest_run(
    tmp_path: Path,
) -> None:
    state = _generic_claimed_recovery_state()
    loaded = persist_and_load_runtime_state(tmp_path, state)
    attempt = next(iter(loaded.recovery_attempts.values()))
    assert attempt.latest_recovery_run_id == "run-generic-recovery"


def test_generic_restart_preserves_pending_cooldown_and_due_timer(
    tmp_path: Path,
) -> None:
    waiting = _generic_cooldown_runtime_state()
    loaded = persist_and_load_runtime_state(tmp_path, waiting)
    wait = next(iter(loaded.cooldown_waits.values()))
    early = decide(
        loaded,
        TimerDue("generic-early", wait_id=wait.wait_id, observed_at=1899),
        deterministic_context(transition_id="transition-generic-early"),
    )
    due = decide(
        loaded,
        TimerDue("generic-due", wait_id=wait.wait_id, observed_at=1900),
        deterministic_context(
            transition_id="transition-generic-due",
            activation_id="activation-generic-due-recovery",
        ),
    )
    assert early.accepted is False
    assert early.refusal is not None and early.refusal.reason == "wait_not_due"
    assert due.accepted is True


def test_generic_restart_preserves_no_artifact_observation_receipt(
    tmp_path: Path,
) -> None:
    state = _generic_cooldown_runtime_state()
    observation = _observation_for_input(state, "observe-generic-recovery-second")
    loaded = persist_and_load_runtime_state(tmp_path, state)
    assert (
        _observation_for_input(loaded, "observe-generic-recovery-second") == observation
    )
    assert all(
        artifact.created_by_input_id != observation.created_by_input_id
        for artifact in loaded.artifacts.values()
    )


@pytest.mark.parametrize("field", ("payload", "observed_at"))
def test_generic_restart_refuses_no_artifact_observation_drift(
    tmp_path: Path,
    field: str,
) -> None:
    state = _generic_cooldown_runtime_state()
    observation = _observation_for_input(state, "observe-generic-recovery-second")
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    with sqlite3.connect(db_path) as connection:
        if field == "observed_at":
            connection.execute(
                "UPDATE runner_observations SET observed_at = observed_at + 1 "
                "WHERE observation_id = ?",
                (observation.observation_id,),
            )
        else:
            payload_digest = ContentAddressedByteStore(cas_root).put_bytes(
                dumps_cas_object(
                    encode_payload({**observation.payload, "marker": "CORRUPT"})
                )
            )
            connection.execute(
                "UPDATE runner_observations SET payload_digest = ? "
                "WHERE observation_id = ?",
                (payload_digest, observation.observation_id),
            )
    with pytest.raises(StorageIntegrityError, match="receipt_authority"):
        load_runtime_state(db_path, cas_root)


def test_generic_restart_preserves_consumed_cooldown_and_claimed_run(
    tmp_path: Path,
) -> None:
    consumed = _generic_consumed_cooldown_runtime_state()
    activation = consumed.activations["activation-generic-recovery-resumed"]
    claimed = _apply_accepted_input(
        consumed,
        ClaimWork("claim-generic-resumed", activation_id=activation.activation_id),
        deterministic_context(
            transition_id="transition-claim-generic-resumed",
            run_id="run-generic-recovery-resumed",
            claim_id="claim-generic-recovery-resumed",
            fencing_token="fence-generic-recovery-resumed",
        ),
    )
    loaded = persist_and_load_runtime_state(tmp_path, claimed)
    wait = next(iter(loaded.cooldown_waits.values()))
    assert wait.consumed_input_id == "generic-cooldown-due"
    assert next(iter(loaded.recovery_attempts.values())).latest_recovery_run_id == (
        "run-generic-recovery-resumed"
    )


def test_generic_restart_preserves_consumed_cooldown_after_attempt_advances(
    tmp_path: Path,
) -> None:
    consumed = _generic_consumed_cooldown_runtime_state()
    loaded = persist_and_load_runtime_state(tmp_path, consumed)
    wait = next(iter(loaded.cooldown_waits.values()))
    duplicate = decide(
        loaded,
        TimerDue("generic-due-again", wait_id=wait.wait_id, observed_at=1901),
        deterministic_context(transition_id="transition-generic-due-again"),
    )
    assert duplicate.accepted is False
    assert duplicate.refusal is not None
    assert duplicate.refusal.reason == "wait_already_consumed"


def test_generic_restart_preserves_lineage_quarantine_and_status(
    tmp_path: Path,
) -> None:
    state = _generic_operator_needed_quarantine_state()
    loaded = persist_and_load_runtime_state(tmp_path, state)
    quarantine = next(iter(loaded.lineage_quarantines.values()))
    assert quarantine.status == "active"
    assert str(quarantine.policy_id) == generic_admission.RECOVERY_POLICY_ID


def test_generic_restart_preserves_quarantine_and_allows_resume(
    tmp_path: Path,
) -> None:
    state = _generic_operator_needed_quarantine_state()
    loaded = persist_and_load_runtime_state(tmp_path, state)
    resumed = _generic_lineage_intervention_state("resume")
    assert loaded.lineage_quarantines == state.lineage_quarantines
    assert next(iter(resumed.lineage_quarantines.values())).status == "superseded"


def test_generic_restart_refuses_quarantine_with_active_attempt(
    tmp_path: Path,
) -> None:
    state = _generic_operator_needed_quarantine_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE recovery_attempts SET phase = 'active_recovery'")
    with pytest.raises(StorageIntegrityError, match="quarantine_eligible"):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize("kind", ("resume", "close", "revise"))
def test_generic_restart_preserves_operator_intervention_state(
    tmp_path: Path,
    kind: str,
) -> None:
    state = _generic_lineage_intervention_state(kind)
    loaded = persist_and_load_runtime_state(tmp_path, state)
    assert loaded.operator_interventions == state.operator_interventions
    assert next(iter(loaded.lineage_quarantines.values())).status == "superseded"


@pytest.mark.parametrize(
    ("column_name", "corrupt_value", "expected_message"),
    (
        ("action_id", "missing-action", "lineage_quarantines.action_id"),
        (
            "original_source_run_id",
            "missing-run",
            "lineage_quarantines.original_source_run_id",
        ),
        (
            "emitting_recovery_run_id",
            "missing-run",
            "lineage_quarantines.emitting_recovery_run_id",
        ),
    ),
)
def test_generic_restart_refuses_corrupt_lineage_quarantine_rows(
    tmp_path: Path,
    column_name: str,
    corrupt_value: str,
    expected_message: str,
) -> None:
    state = _generic_operator_needed_quarantine_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"UPDATE lineage_quarantines SET {column_name} = ?",
            (corrupt_value,),
        )
    with pytest.raises(StorageIntegrityError, match=expected_message):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("column_name", "corrupt_value", "expected_message"),
    (
        ("policy_id", "missing.policy", "operator_interventions.policy_id"),
        ("option_id", "missing.option", "operator_interventions.option_id"),
        ("actor_kind", "runtime", "operator_interventions.actor_kind"),
        ("quarantine_id", "missing", "operator_interventions.quarantine_id"),
        (
            "recovery_attempt_record_id",
            "missing",
            "operator_interventions.recovery_attempt_record_id",
        ),
        (
            "target_work_item_id",
            "missing",
            "operator_interventions.target_work_item_id",
        ),
    ),
)
def test_generic_restart_refuses_corrupt_operator_intervention_rows(
    tmp_path: Path,
    column_name: str,
    corrupt_value: str,
    expected_message: str,
) -> None:
    state = _generic_lineage_intervention_state("close")
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"UPDATE operator_interventions SET {column_name} = ?",
            (corrupt_value,),
        )
    with pytest.raises(StorageIntegrityError, match=expected_message):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("column_name", "corrupt_value", "expected_message"),
    (
        (
            "target_activation_id",
            "missing",
            "operator_interventions.target_activation_id",
        ),
        (
            "payload_reference",
            "work_item:other:payload",
            "operator_interventions payload_reference",
        ),
        (
            "payload_digest",
            f"sha256:{'0' * 64}",
            "operator_interventions payload_digest",
        ),
    ),
)
def test_generic_restart_refuses_corrupt_revise_intervention_rows(
    tmp_path: Path,
    column_name: str,
    corrupt_value: str,
    expected_message: str,
) -> None:
    state = _generic_lineage_intervention_state("revise")
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"UPDATE operator_interventions SET {column_name} = ?",
            (corrupt_value,),
        )
    with pytest.raises(StorageIntegrityError, match=expected_message):
        load_runtime_state(db_path, cas_root)


def test_generic_restart_preserves_active_operator_wait_and_resolves_after_load(
    tmp_path: Path,
) -> None:
    waiting = _generic_operator_wait_runtime_state()
    loaded = persist_and_load_runtime_state(tmp_path, waiting)
    wait = next(iter(loaded.operator_waits.values()))
    resolved = _apply_accepted_input(
        loaded,
        OperatorResumeWait(
            "generic-resume-after-load",
            selected_plan_ref=wait.selected_plan_ref,
            wait_id=wait.wait_id,
            lineage_id=wait.lineage_id,
            actor_id="local-operator",
            actor_kind="local_operator",
            payload={},
        ),
        deterministic_context(
            transition_id="transition-generic-resume-after-load",
            activation_id="activation-generic-resumed-after-load",
        ),
    )
    assert resolved.operator_waits[wait.wait_id].status == "resolved"


def test_generic_restart_preserves_resolved_wait_and_refuses_second_resolution(
    tmp_path: Path,
) -> None:
    resolved = _generic_resolved_operator_wait_state()
    loaded = persist_and_load_runtime_state(tmp_path, resolved)
    wait = next(iter(loaded.operator_waits.values()))
    duplicate = decide(
        loaded,
        OperatorResumeWait(
            "generic-resume-duplicate",
            selected_plan_ref=wait.selected_plan_ref,
            wait_id=wait.wait_id,
            lineage_id=wait.lineage_id,
            actor_id="local-operator",
            actor_kind="local_operator",
            payload={},
        ),
        deterministic_context(transition_id="transition-generic-resume-duplicate"),
    )
    assert duplicate.accepted is False
    assert duplicate.refusal is not None
    assert duplicate.refusal.reason == "operator_wait_not_active"


def test_generic_restart_allows_resolved_wait_source_compiled_close(
    tmp_path: Path,
) -> None:
    state = _generic_resumed_operator_wait_source_closed_state()
    loaded = persist_and_load_runtime_state(tmp_path, state)
    wait = next(iter(loaded.operator_waits.values()))
    assert wait.status == "resolved"
    assert wait.source_work_item_id in loaded.closed_work_items


def test_generic_restart_refuses_corrupt_wait_or_close_link(tmp_path: Path) -> None:
    state = _generic_operator_wait_runtime_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE operator_waits SET operator_wait_id = 'admission.missing_wait'"
        )
    with pytest.raises(StorageIntegrityError, match="operator_wait_id"):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("source_action_id", generic_admission.COUNTER_INCREMENT_ACTION_ID),
        ("source_stage_kind_id", generic_admission.CHILD_STAGE_ID),
        ("source_graph_node_id", generic_admission.CHILD_NODE_ID),
        ("source_queue_family_id", "child"),
        ("source_runner_binding_id", "missing.runner"),
    ),
)
def test_generic_restart_refuses_corrupt_operator_wait_source_links(
    tmp_path: Path,
    column: str,
    value: str,
) -> None:
    state = _generic_operator_wait_runtime_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    with sqlite3.connect(db_path) as connection:
        connection.execute(f"UPDATE operator_waits SET {column} = ?", (value,))
    with pytest.raises(StorageIntegrityError):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("queue_family_id", "child"),
        ("graph_node_id", generic_admission.CHILD_NODE_ID),
        ("stage_kind_id", generic_admission.CHILD_STAGE_ID),
        ("runner_binding_id", "missing.runner"),
    ),
)
def test_generic_restart_refuses_corrupt_revise_wait_target_route(
    tmp_path: Path,
    column: str,
    value: str,
) -> None:
    state = _generic_revised_operator_wait_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    with sqlite3.connect(db_path) as connection:
        wait = connection.execute(
            "SELECT target_work_item_id, target_activation_id FROM operator_waits"
        ).fetchone()
        assert wait is not None
        work_item_id, activation_id = wait
        if column == "queue_family_id":
            connection.execute(
                "UPDATE work_items SET queue_family_id = ? WHERE work_item_id = ?",
                (value, work_item_id),
            )
        connection.execute(
            f"UPDATE activations SET {column} = ? WHERE activation_id = ?",
            (value, activation_id),
        )
    with pytest.raises(StorageIntegrityError):
        load_runtime_state(db_path, cas_root)
