from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from millrace.compiler import SelectedRunnerAdapterPolicy, compile_workflow
from millrace.compiler.canonical import (
    AUTHORITY_FINGERPRINT_DOMAIN_PREFIX,
    CanonicalAuthorityError,
    authority_fingerprint,
    canonical_authority_bytes,
)
from millrace.contracts import PartitionDeclaration, SelectedCompiledPlan
from millrace.contracts.ids import PartitionId
from millrace.workflows import kernel_ping
from support import kernel_ping as kernel_ping_support
from tests.compiler.test_context_bindings import _source_with_context_binding

Source = dict[str, object]
_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)
EXPORT_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "compiled_plan_exports"
    / "kernel_ping_v0_1.export.json"
)

EXPECTED_EXPORT_KEYS = {
    "record_kind",
    "schema_version",
    "compiler_id",
    "compiler_protocol_version",
    "plan_format_version",
    "workflow_id",
    "workflow_version",
    "canonicalization_algorithm",
    "hash_algorithm",
    "authority_fingerprint_domain",
    "authority_fingerprint",
    "selected_authority",
}

EXPECTED_EXPORT_ALL = (
    "CANONICALIZATION_ALGORITHM",
    "COMPILED_PLAN_EXPORT_RECORD_KIND",
    "COMPILED_PLAN_EXPORT_SCHEMA_VERSION",
    "CompiledPlanExportError",
    "COMPILER_ID",
    "COMPILER_PROTOCOL_VERSION",
    "EXPORT_AUTHORITY_FINGERPRINT_DOMAIN",
    "EXPORT_HASH_ALGORITHM",
    "VerifiedCompiledPlanExport",
    "compiled_plan_export_bytes",
    "compiled_plan_export_record",
    "verify_compiled_plan_export_bytes",
    "verify_compiled_plan_export_record",
)


def _source() -> Source:
    return deepcopy(kernel_ping.WORKFLOW_SOURCE)


def _source_with_effect_declaration() -> Source:
    source = _source()
    stage = next(
        record
        for record in cast(list[dict[str, object]], source["stage_kinds"])
        if record["id"] == "kernel_ping.taskmaster"
    )
    stage["declared_outcome_ids"] = (
        *cast(tuple[str, ...], stage["declared_outcome_ids"]),
        "kernel_ping.taskmaster.effect_ready",
    )
    cast(list[dict[str, object]], source["terminal_outcomes"]).append(
        {
            "id": "kernel_ping.taskmaster.effect_ready",
            "stage_kind_id": "kernel_ping.taskmaster",
            "marker": "EFFECT_READY",
        }
    )
    cast(list[dict[str, object]], source["terminal_actions"]).append(
        {
            "id": "kernel_ping.close_taskmaster_effect_ready",
            "stage_kind_id": "kernel_ping.taskmaster",
            "outcome_id": "kernel_ping.taskmaster.effect_ready",
            "kind": "complete_work_item",
            "artifact_schema_id": "kernel_ping.task_artifact",
        }
    )
    source["effect_declarations"] = [
        {
            "id": "kernel_ping.effect.record_task",
            "terminal_action_id": "kernel_ping.close_taskmaster_effect_ready",
            "artifact_schema_id": "kernel_ping.task_artifact",
            "provider_ref": "provider.fake_local.workspace",
            "capability_policy_ref": "policy.fake_local.no_real_side_effects",
            "target_ref_kind": "workspace_record",
            "target_ref_schema": "kernel_ping.effects.target.workspace_record.v1",
            "allowed_reconciliation_statuses": ("applied", "no_op", "refused"),
            "real_side_effects_allowed": False,
        }
    ]
    return source


def _compile_plan(source: Mapping[str, object]) -> SelectedCompiledPlan:
    result = compile_workflow(source)
    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ] == []
    assert result.plan is not None
    return result.plan


def _compile_codex_plan(source: Mapping[str, object]) -> SelectedCompiledPlan:
    result = compile_workflow(source, selected_runner_policy=_CODEX_POLICY)
    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ] == []
    assert result.plan is not None
    return result.plan


def _collapse_to_one_runner(source: Source) -> dict[str, object]:
    records = cast(list[dict[str, object]], source["runner_bindings"])
    runner = records[0]
    runner_id = str(runner["id"])
    source["runner_bindings"] = [runner]
    runner["stage_kind_ids"] = ("kernel_ping.taskmaster", "kernel_ping.worker")
    for stage in cast(list[dict[str, object]], source["stage_kinds"]):
        stage["runner_binding_id"] = runner_id
    for route in cast(list[dict[str, object]], source["external_enqueue_routes"]):
        route["runner_binding_id"] = runner_id
    for action in cast(list[dict[str, object]], source["terminal_actions"]):
        if action.get("runner_binding_id") is not None:
            action["runner_binding_id"] = runner_id
    return runner


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
    runner = _collapse_to_one_runner(source)
    runner.update(
        {
            "adapter_kind": "codex",
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
    return _compile_plan(source)


def test_export_round_trip_preserves_exact_runner_component_authority() -> None:
    from millrace.compiler import (
        compiled_plan_export_bytes,
        verify_compiled_plan_export_bytes,
    )

    plan = _component_plan()
    verified = verify_compiled_plan_export_bytes(compiled_plan_export_bytes(plan))
    runner = cast(
        list[dict[str, object]],
        verified.selected_authority["runner_bindings"],
    )[0]

    assert runner["component_pin"] == {
        "component_id": "example.component",
        "component_kind": "opaque.runner",
        "component_version": "1.2.3",
        "descriptor_media_type": "application/vnd.example.runner+json",
        "descriptor_sha256": "a" * 64,
        "legal_terminal_result_ids": ["BLOCKED", "COMPLETE"],
        "provider_distribution": "example-provider",
        "provider_version": "4.5.6",
        "record_kind": "runner_component_pin",
        "required_capability_ids": ["capability.runner.invoke"],
        "schema_version": 2,
        "max_work_item_payload_bytes": None,
    }
    assert runner["terminal_result_mappings"] == [
        {
            "outcome_id": "kernel_ping.taskmaster.task_complete",
            "record_kind": "runner_terminal_result_mapping",
            "runner_result_id": "COMPLETE",
            "schema_version": 1,
            "stage_kind_id": "kernel_ping.taskmaster",
        }
    ]


@pytest.mark.parametrize(
    ("corruption", "message"),
    (
        ("extra_pin_key", "extra runner component pin key"),
        ("wrong_pin_kind", "unsupported runner component pin.record_kind"),
        ("wrong_pin_version", "unsupported runner component pin.schema_version"),
        ("malformed_digest", "runner component descriptor_sha256"),
        ("duplicate_mapping", "duplicate runner terminal result mapping"),
        ("duplicate_target", "duplicate runner terminal outcome mapping"),
        (
            "wrong_mapping_kind",
            "unsupported runner terminal result mapping.record_kind",
        ),
        (
            "wrong_mapping_version",
            "unsupported runner terminal result mapping.schema_version",
        ),
    ),
)
def test_export_verifier_refuses_corrupt_runner_component_authority(
    corruption: str,
    message: str,
) -> None:
    from millrace.compiler import (
        CompiledPlanExportError,
        compiled_plan_export_record,
        verify_compiled_plan_export_record,
    )

    record = deepcopy(dict(compiled_plan_export_record(_component_plan())))
    selected = cast(dict[str, object], record["selected_authority"])
    runner = cast(list[dict[str, object]], selected["runner_bindings"])[0]
    pin = cast(dict[str, object], runner["component_pin"])
    mappings = cast(list[dict[str, object]], runner["terminal_result_mappings"])
    if corruption == "extra_pin_key":
        pin["options"] = {}
    elif corruption == "wrong_pin_kind":
        pin["record_kind"] = "wrong"
    elif corruption == "wrong_pin_version":
        pin["schema_version"] = 3
    elif corruption == "malformed_digest":
        pin["descriptor_sha256"] = "A" * 64
    elif corruption == "duplicate_mapping":
        mappings.append(dict(mappings[0]))
    elif corruption == "duplicate_target":
        mappings.insert(
            0,
            {
                **mappings[0],
                "runner_result_id": "BLOCKED",
            },
        )
    elif corruption == "wrong_mapping_kind":
        mappings[0]["record_kind"] = "wrong"
    else:
        mappings[0]["schema_version"] = 2
    record["authority_fingerprint"] = authority_fingerprint(selected)

    with pytest.raises(CompiledPlanExportError, match=message):
        verify_compiled_plan_export_record(record)


@pytest.mark.parametrize(
    "field_name",
    (
        "component_kind",
        "component_id",
        "component_version",
        "provider_distribution",
        "provider_version",
        "descriptor_media_type",
    ),
)
def test_export_verifier_refuses_whitespace_runner_component_identity(
    field_name: str,
) -> None:
    from millrace.compiler import (
        CompiledPlanExportError,
        compiled_plan_export_record,
        verify_compiled_plan_export_record,
    )

    record = deepcopy(dict(compiled_plan_export_record(_component_plan())))
    selected = cast(dict[str, object], record["selected_authority"])
    runner = cast(list[dict[str, object]], selected["runner_bindings"])[0]
    pin = cast(dict[str, object], runner["component_pin"])
    pin[field_name] = " \t"
    record["authority_fingerprint"] = authority_fingerprint(selected)

    with pytest.raises(
        CompiledPlanExportError,
        match=rf"runner component pin\.{field_name} must be a nonblank string",
    ):
        verify_compiled_plan_export_record(record)


def test_export_verifier_refuses_format_14_without_migration() -> None:
    from millrace.compiler import (
        CompiledPlanExportError,
        compiled_plan_export_record,
        verify_compiled_plan_export_record,
    )

    record = deepcopy(dict(compiled_plan_export_record(_component_plan())))
    selected = cast(dict[str, object], record["selected_authority"])
    record["plan_format_version"] = 14
    selected["schema_version"] = 14
    record["authority_fingerprint"] = authority_fingerprint(selected)

    with pytest.raises(
        CompiledPlanExportError,
        match="unsupported plan_format_version",
    ):
        verify_compiled_plan_export_record(record)


def _parsed_export(plan: SelectedCompiledPlan) -> dict[str, object]:
    from millrace.compiler import compiled_plan_export_bytes

    parsed = json.loads(compiled_plan_export_bytes(plan).decode("utf-8"))
    assert isinstance(parsed, dict)
    return cast(dict[str, object], parsed)


def _parsed_context_export(*, write_enabled: bool) -> dict[str, object]:
    result = compile_workflow(_source_with_context_binding(write_enabled=write_enabled))
    assert result.plan is not None
    return _parsed_export(result.plan)


def test_unbound_export_omits_empty_context_bindings_and_verifies() -> None:
    from millrace.compiler import (
        compiled_plan_export_record,
        verify_compiled_plan_export_record,
    )

    record = dict(compiled_plan_export_record(_compile_plan(_source())))
    selected = cast(dict[str, object], record["selected_authority"])

    assert "context_bindings" not in selected
    verified = verify_compiled_plan_export_record(record)
    assert "context_bindings" not in verified.selected_authority


def test_bound_context_bindings_are_preserved_in_canonical_export_and_verification(
) -> None:
    from millrace.compiler import verify_compiled_plan_export_record

    result = compile_workflow(_source_with_context_binding(write_enabled=False))
    assert result.plan is not None
    plan = result.plan
    authority = json.loads(canonical_authority_bytes(plan).decode("utf-8"))
    record = _parsed_export(plan)
    selected = cast(dict[str, object], record["selected_authority"])

    assert isinstance(authority, dict)
    assert authority["context_bindings"]
    assert selected["context_bindings"] == authority["context_bindings"]
    assert record["authority_fingerprint"] == authority_fingerprint(plan)
    verified = verify_compiled_plan_export_record(record)
    assert verified.selected_authority["context_bindings"] == selected[
        "context_bindings"
    ]


@pytest.mark.parametrize(
    ("_case_id", "write_enabled", "record_kind", "expected_detail"),
    (
        pytest.param(
            "binding",
            False,
            "binding",
            "context_binding_shape:0",
            id="binding-header",
        ),
        pytest.param(
            "required-source",
            False,
            "source",
            "context_binding_source_shape:kernel_ping.taskmaster_context:0",
            id="source-header",
        ),
        pytest.param(
            "write-rule",
            True,
            "write_rule",
            "context_binding_write_shape:kernel_ping.taskmaster_context:0",
            id="write-rule-header",
        ),
    ),
)
def test_export_verifier_requires_exact_context_record_headers(
    _case_id: str,
    write_enabled: bool,
    record_kind: str,
    expected_detail: str,
) -> None:
    from millrace.compiler import (
        CompiledPlanExportError,
        verify_compiled_plan_export_record,
    )

    record = _parsed_context_export(write_enabled=write_enabled)
    selected = cast(dict[str, object], record["selected_authority"])
    bindings = cast(list[dict[str, object]], selected["context_bindings"])
    binding = bindings[0]
    if record_kind == "binding":
        target = binding
    elif record_kind == "source":
        target = cast(list[dict[str, object]], binding["required_sources"])[0]
    else:
        target = cast(list[dict[str, object]], binding["write_rules"])[0]
    target.pop("record_kind")
    target.pop("schema_version")
    record["authority_fingerprint"] = authority_fingerprint(selected)

    with pytest.raises(
        CompiledPlanExportError,
        match=rf"selected context binding authority is invalid: {expected_detail}",
    ):
        verify_compiled_plan_export_record(record)


@pytest.mark.parametrize(
    ("write_enabled", "record_kind", "schema_version", "expected_detail"),
    (
        pytest.param(
            False,
            "binding",
            True,
            "context_binding_shape:0",
            id="binding-bool",
        ),
        pytest.param(
            False,
            "binding",
            1.0,
            "context_binding_shape:0",
            id="binding-float",
        ),
        pytest.param(
            False,
            "source",
            True,
            "context_binding_source_shape:kernel_ping.taskmaster_context:0",
            id="source-bool",
        ),
        pytest.param(
            False,
            "source",
            1.0,
            "context_binding_source_shape:kernel_ping.taskmaster_context:0",
            id="source-float",
        ),
        pytest.param(
            True,
            "write_rule",
            True,
            "context_binding_write_shape:kernel_ping.taskmaster_context:0",
            id="write-rule-bool",
        ),
        pytest.param(
            True,
            "write_rule",
            1.0,
            "context_binding_write_shape:kernel_ping.taskmaster_context:0",
            id="write-rule-float",
        ),
    ),
)
def test_export_verifier_rejects_non_integer_context_record_schema_versions(
    write_enabled: bool,
    record_kind: str,
    schema_version: object,
    expected_detail: str,
) -> None:
    from millrace.compiler import (
        CompiledPlanExportError,
        verify_compiled_plan_export_record,
    )

    record = _parsed_context_export(write_enabled=write_enabled)
    selected = cast(dict[str, object], record["selected_authority"])
    binding = cast(list[dict[str, object]], selected["context_bindings"])[0]
    if record_kind == "binding":
        target = binding
    elif record_kind == "source":
        target = cast(list[dict[str, object]], binding["required_sources"])[0]
    else:
        target = cast(list[dict[str, object]], binding["write_rules"])[0]
    target["schema_version"] = schema_version
    _tampered_record_with_recomputed_fingerprint(record, selected)

    with pytest.raises(
        CompiledPlanExportError,
        match=rf"selected context binding authority is invalid: {expected_detail}",
    ):
        verify_compiled_plan_export_record(record)


@pytest.mark.parametrize(
    ("write_enabled", "field_name", "value", "expected_detail"),
    (
        (
            False,
            "writeback_terminal_action_id",
            7,
            "context_binding_read_only_linkage",
        ),
        (
            False,
            "writeback_terminal_action_id",
            "",
            "context_binding_read_only_linkage",
        ),
        (
            False,
            "writeback_artifact_schema_id",
            [],
            "context_binding_read_only_linkage",
        ),
        (
            True,
            "writeback_terminal_action_id",
            7,
            "context_binding_writeback_linkage",
        ),
        (
            True,
            "writeback_terminal_action_id",
            "",
            "context_binding_writeback_linkage",
        ),
        (
            True,
            "writeback_artifact_schema_id",
            [],
            "context_binding_writeback_linkage",
        ),
    ),
)
def test_export_verifier_refuses_malformed_context_writeback_linkage(
    write_enabled: bool,
    field_name: str,
    value: object,
    expected_detail: str,
) -> None:
    from millrace.compiler import (
        CompiledPlanExportError,
        verify_compiled_plan_export_record,
    )

    record = _parsed_context_export(write_enabled=write_enabled)
    selected = cast(dict[str, object], record["selected_authority"])
    binding = cast(list[dict[str, object]], selected["context_bindings"])[0]
    binding[field_name] = value
    _tampered_record_with_recomputed_fingerprint(record, selected)

    with pytest.raises(
        CompiledPlanExportError,
        match=rf"selected context binding authority is invalid: {expected_detail}:",
    ):
        verify_compiled_plan_export_record(record)


@pytest.mark.parametrize(
    ("field_name", "value", "expected_detail"),
    (
        (
            "stage_kind_id",
            7,
            "context_binding_stage_id_type:kernel_ping.taskmaster_context",
        ),
        (
            "router_asset_id",
            [],
            "context_binding_router_asset_id_type:kernel_ping.taskmaster_context",
        ),
    ),
)
def test_export_verifier_refuses_non_string_context_binding_ids(
    field_name: str,
    value: object,
    expected_detail: str,
) -> None:
    from millrace.compiler import (
        CompiledPlanExportError,
        verify_compiled_plan_export_record,
    )

    record = _parsed_context_export(write_enabled=False)
    selected = cast(dict[str, object], record["selected_authority"])
    binding = cast(list[dict[str, object]], selected["context_bindings"])[0]
    binding[field_name] = value
    _tampered_record_with_recomputed_fingerprint(record, selected)

    with pytest.raises(
        CompiledPlanExportError,
        match=rf"selected context binding authority is invalid: {expected_detail}",
    ):
        verify_compiled_plan_export_record(record)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _valid_export_record() -> dict[str, object]:
    return _parsed_export(_compile_plan(_source()))


def _with_non_string_key(record: object) -> Mapping[object, object]:
    mutated = cast(dict[object, object], dict(cast(dict[str, object], record)))
    mutated[1] = "x"
    return mutated


def _contains_key(value: object, key: str) -> bool:
    if isinstance(value, Mapping):
        return key in value or any(
            _contains_key(nested_value, key) for nested_value in value.values()
        )
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False


def test_export_public_api_is_intentional() -> None:
    import millrace.compiler as compiler
    import millrace.compiler.export as compiler_export

    assert compiler_export.__all__ == EXPECTED_EXPORT_ALL
    assert callable(compiler.compiled_plan_export_bytes)
    assert callable(compiler.compiled_plan_export_record)
    assert callable(compiler.verify_compiled_plan_export_bytes)
    assert callable(compiler.verify_compiled_plan_export_record)


def test_export_envelope_has_required_versioned_shape() -> None:
    from millrace.compiler import compiled_plan_export_bytes
    from millrace.compiler.export import EXPORT_AUTHORITY_FINGERPRINT_DOMAIN

    plan = _compile_plan(_source())
    export_bytes = compiled_plan_export_bytes(plan)
    parsed = json.loads(export_bytes.decode("utf-8"))
    assert isinstance(parsed, dict)

    assert set(parsed) == EXPECTED_EXPORT_KEYS
    assert parsed["record_kind"] == "compiled_plan_export"
    assert parsed["schema_version"] == 1
    assert parsed["compiler_id"] == "millrace-ai"
    assert parsed["compiler_protocol_version"] == 1
    assert parsed["plan_format_version"] == SelectedCompiledPlan.schema_version
    assert parsed["canonicalization_algorithm"] == "millrace-canonical-json-v1"
    assert parsed["hash_algorithm"] == "sha256"
    assert parsed["authority_fingerprint_domain"] == "millrace-authority-v1"
    assert parsed["authority_fingerprint"] == authority_fingerprint(plan)
    assert parsed["workflow_id"] == str(plan.workflow.workflow_id)
    assert parsed["workflow_version"] == str(plan.workflow.workflow_version)
    assert (
        EXPORT_AUTHORITY_FINGERPRINT_DOMAIN + "\0"
        == AUTHORITY_FINGERPRINT_DOMAIN_PREFIX.decode("ascii")
    )


def test_export_metadata_matches_selected_authority_root() -> None:
    plan = _compile_plan(_source())
    parsed = _parsed_export(plan)
    selected_authority = cast(dict[str, object], parsed["selected_authority"])
    workflow = cast(dict[str, object], selected_authority["workflow"])

    assert parsed["workflow_id"] == workflow["workflow_id"]
    assert parsed["workflow_version"] == workflow["workflow_version"]
    assert parsed["plan_format_version"] == selected_authority["schema_version"]


def test_export_bytes_are_deterministic_utf8_json_without_runtime_metadata() -> None:
    from millrace.compiler import compiled_plan_export_bytes

    first_plan = _compile_plan(_source())
    second_plan = _compile_plan(_source())

    first_bytes = compiled_plan_export_bytes(first_plan)
    second_bytes = compiled_plan_export_bytes(second_plan)

    assert first_bytes == second_bytes
    parsed = json.loads(first_bytes.decode("utf-8"))
    assert isinstance(parsed, dict)
    assert _canonical_json_bytes(parsed) == first_bytes
    assert not ({"diagnostics", "metadata", "source_spans"} & set(parsed))

    forbidden_fragments = (
        str(Path.cwd()).encode("utf-8"),
        b"source_span",
        b"source_uri",
        b"timestamp",
        b"datetime",
        b"debug",
        b"object at",
    )
    assert [
        fragment for fragment in forbidden_fragments if fragment in first_bytes
    ] == []


def test_selected_authority_in_export_matches_canonical_authority() -> None:
    plan = _compile_plan(_source())
    parsed = _parsed_export(plan)
    selected_authority = parsed["selected_authority"]

    assert _canonical_json_bytes(selected_authority) == canonical_authority_bytes(plan)
    assert authority_fingerprint(selected_authority) == parsed["authority_fingerprint"]
    assert not _contains_key(selected_authority, "presentation")


def test_export_can_verify_generic_partitionless_authority_without_hydration() -> None:
    from millrace.compiler import (
        compiled_plan_export_bytes,
        verify_compiled_plan_export_record,
    )

    base_plan = _compile_plan(_source())
    stage_kinds = list(base_plan.stage_kinds)
    stage_kinds[0] = replace(stage_kinds[0], partition_id=cast(Any, None))
    plan = replace(base_plan, stage_kinds=tuple(stage_kinds))
    parsed = _parsed_export(plan)
    selected_authority = cast(dict[str, object], parsed["selected_authority"])
    exported_stage_kinds = cast(list[object], selected_authority["stage_kinds"])
    exported_stage = cast(dict[str, object], exported_stage_kinds[0])

    assert exported_stage["partition_id"] is None
    assert parsed["authority_fingerprint"] == authority_fingerprint(plan)
    assert _canonical_json_bytes(parsed) == compiled_plan_export_bytes(plan)
    verified = verify_compiled_plan_export_record(parsed)
    assert verified.authority_fingerprint == authority_fingerprint(plan)


def test_export_excludes_declaration_presentation_metadata() -> None:
    from millrace.compiler import compiled_plan_export_bytes

    source = _source()
    partition = cast(dict[str, object], cast(list[object], source["partitions"])[0])
    partition["presentation"] = {
        "display_name": "Local-only",
        "debug_path": str(Path.cwd()),
        "debug_timestamp": "2026-06-21T01:45:00Z",
        "debug_label": "debug sentinel",
    }

    export_bytes = compiled_plan_export_bytes(_compile_plan(source))

    assert b"debug_path" not in export_bytes
    assert b"debug_timestamp" not in export_bytes
    assert b"debug sentinel" not in export_bytes
    assert str(Path.cwd()).encode("utf-8") not in export_bytes


def test_export_record_and_bytes_share_same_authority_payload() -> None:
    from millrace.compiler import (
        compiled_plan_export_bytes,
        compiled_plan_export_record,
    )

    plan = _compile_plan(_source())
    record = compiled_plan_export_record(plan)

    assert set(record) == EXPECTED_EXPORT_KEYS
    assert _canonical_json_bytes(record) == compiled_plan_export_bytes(plan)


def test_valid_export_bytes_verify_without_hydrating_runtime_authority() -> None:
    from millrace.compiler import (
        compiled_plan_export_bytes,
        verify_compiled_plan_export_bytes,
    )
    from millrace.compiler.export import VerifiedCompiledPlanExport

    plan = _compile_plan(_source())

    verified = verify_compiled_plan_export_bytes(compiled_plan_export_bytes(plan))

    assert isinstance(verified, VerifiedCompiledPlanExport)
    assert not isinstance(verified, SelectedCompiledPlan)
    assert verified.authority_fingerprint == authority_fingerprint(plan)
    assert verified.workflow_id == str(plan.workflow.workflow_id)
    assert verified.workflow_version == str(plan.workflow.workflow_version)
    assert verified.plan_format_version == SelectedCompiledPlan.schema_version
    assert _canonical_json_bytes(
        verified.selected_authority
    ) == canonical_authority_bytes(plan)
    assert [
        field
        for field in ("plan", "trusted_plan", "admitted_plan", "runtime_plan")
        if hasattr(verified, field)
    ] == []


def test_valid_export_record_verifies_without_hydrating_runtime_authority() -> None:
    from millrace.compiler import (
        compiled_plan_export_record,
        verify_compiled_plan_export_record,
    )
    from millrace.compiler.export import VerifiedCompiledPlanExport

    plan = _compile_plan(_source())

    verified = verify_compiled_plan_export_record(compiled_plan_export_record(plan))

    assert isinstance(verified, VerifiedCompiledPlanExport)
    assert not isinstance(verified, SelectedCompiledPlan)
    assert verified.authority_fingerprint == authority_fingerprint(plan)
    assert verified.selected_authority == _parsed_export(plan)["selected_authority"]


def test_kernel_ping_export_matches_golden_fixture_exactly() -> None:
    from millrace.compiler import (
        compiled_plan_export_bytes,
        verify_compiled_plan_export_bytes,
    )

    plan = _compile_plan(_source())

    export_bytes = compiled_plan_export_bytes(plan)
    fixture_bytes = EXPORT_FIXTURE.read_bytes()

    assert not fixture_bytes.endswith(b"\n")
    parsed = json.loads(fixture_bytes.decode("utf-8"))
    assert isinstance(parsed, dict)
    assert _canonical_json_bytes(parsed) == fixture_bytes

    assert export_bytes == fixture_bytes

    verified = verify_compiled_plan_export_bytes(fixture_bytes)
    assert verified.authority_fingerprint == authority_fingerprint(plan)
    assert verified.workflow_id == "kernel_ping"
    assert verified.workflow_version == "0.1"


def test_compiler_provenance_does_not_change_selected_authority() -> None:
    from millrace.compiler.export import COMPILER_ID

    plan = _compile_plan(_source())
    authority_bytes = canonical_authority_bytes(plan)

    assert COMPILER_ID == "millrace-ai"
    assert authority_fingerprint(plan) == (
        "sha256:3282a891816a1514bc16cce5e4d0ecc086fb874ad8a95add59cce8d386845e8f"
    )
    assert hashlib.sha256(authority_bytes).hexdigest() == (
        "7945fcd272d2fac07969d811df14b9da169ec808f36bca3e9398db2c29867267"
    )
    assert len(authority_bytes) == 13153


def test_export_refuses_temporary_compiler_identity() -> None:
    from millrace.compiler import verify_compiled_plan_export_record
    from millrace.compiler.export import CompiledPlanExportError

    record = _valid_export_record()
    record["compiler_id"] = "millrace" + "-rewrite"

    with pytest.raises(CompiledPlanExportError, match="unsupported compiler_id"):
        verify_compiled_plan_export_record(record)


def test_verified_selected_authority_is_detached_from_input_record() -> None:
    from millrace.compiler import verify_compiled_plan_export_record

    record = _valid_export_record()
    verified = verify_compiled_plan_export_record(record)
    selected_authority = cast(dict[str, object], record["selected_authority"])
    selected_authority["record_kind"] = "mutated-after-verify"

    assert verified.selected_authority["record_kind"] == "selected_compiled_plan"


def test_verify_refuses_fingerprint_drift() -> None:
    from millrace.compiler import verify_compiled_plan_export_record
    from millrace.compiler.export import CompiledPlanExportError

    plan = _compile_plan(_source())
    record = _parsed_export(plan)
    selected_authority = cast(dict[str, object], record["selected_authority"])
    queue_families = cast(list[object], selected_authority["queue_families"])
    queue_family = cast(dict[str, object], queue_families[0])
    queue_family["display_name"] = "tampered"

    with pytest.raises(
        CompiledPlanExportError,
        match="authority fingerprint mismatch",
    ):
        verify_compiled_plan_export_record(record)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("record_kind", "not_export", "unsupported record_kind"),
        ("schema_version", 99, "unsupported schema_version"),
        ("compiler_id", "other-compiler", "unsupported compiler_id"),
        ("compiler_protocol_version", 99, "unsupported compiler_protocol_version"),
        ("plan_format_version", 99, "unsupported plan_format_version"),
        (
            "canonicalization_algorithm",
            "other-canonical-json",
            "unsupported canonicalization_algorithm",
        ),
        ("hash_algorithm", "md5", "unsupported hash_algorithm"),
        (
            "authority_fingerprint_domain",
            "other-domain",
            "unsupported authority_fingerprint_domain",
        ),
    ),
)
def test_verify_refuses_unsupported_export_constants(
    field: str,
    value: object,
    message: str,
) -> None:
    from millrace.compiler import verify_compiled_plan_export_record
    from millrace.compiler.export import CompiledPlanExportError

    record = _parsed_export(_compile_plan(_source()))
    record[field] = value

    with pytest.raises(CompiledPlanExportError, match=message):
        verify_compiled_plan_export_record(record)


@pytest.mark.parametrize(
    "field",
    sorted(EXPECTED_EXPORT_KEYS),
)
def test_verify_refuses_each_missing_export_key(field: str) -> None:
    from millrace.compiler import verify_compiled_plan_export_record
    from millrace.compiler.export import CompiledPlanExportError

    record = _valid_export_record()
    record.pop(field)

    with pytest.raises(CompiledPlanExportError, match=f"missing export key: {field}"):
        verify_compiled_plan_export_record(record)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("schema_version", True, "unsupported schema_version"),
        ("schema_version", 1.0, "unsupported schema_version"),
        ("schema_version", "1", "unsupported schema_version"),
        (
            "compiler_protocol_version",
            True,
            "unsupported compiler_protocol_version",
        ),
        (
            "compiler_protocol_version",
            1.0,
            "unsupported compiler_protocol_version",
        ),
        ("compiler_protocol_version", "1", "unsupported compiler_protocol_version"),
        ("plan_format_version", True, "unsupported plan_format_version"),
        ("plan_format_version", 1.0, "unsupported plan_format_version"),
        ("plan_format_version", "1", "unsupported plan_format_version"),
    ),
)
def test_verify_refuses_export_version_type_traps(
    field: str,
    value: object,
    message: str,
) -> None:
    from millrace.compiler import verify_compiled_plan_export_record
    from millrace.compiler.export import CompiledPlanExportError

    record = _valid_export_record()
    record[field] = value

    with pytest.raises(CompiledPlanExportError, match=message):
        verify_compiled_plan_export_record(record)


@pytest.mark.parametrize(
    ("field", "message"),
    (
        ("workflow_id", "workflow_id mismatch"),
        ("workflow_version", "workflow_version mismatch"),
    ),
)
def test_verify_refuses_envelope_metadata_drift(
    field: str,
    message: str,
) -> None:
    from millrace.compiler import verify_compiled_plan_export_record
    from millrace.compiler.export import CompiledPlanExportError

    record = _parsed_export(_compile_plan(_source()))
    record[field] = "tampered"

    with pytest.raises(CompiledPlanExportError, match=message):
        verify_compiled_plan_export_record(record)


def _tampered_record_with_recomputed_fingerprint(
    record: dict[str, object],
    selected_authority: object,
) -> dict[str, object]:
    record["selected_authority"] = selected_authority
    try:
        record["authority_fingerprint"] = authority_fingerprint(selected_authority)
    except CanonicalAuthorityError:
        pass
    return record


def _mutate_selected_authority(
    mutator: Callable[[object], object],
) -> dict[str, object]:
    record = _valid_export_record()
    selected_authority = record["selected_authority"]
    return _tampered_record_with_recomputed_fingerprint(
        record,
        mutator(selected_authority),
    )


@pytest.mark.parametrize(
    ("mutator", "message"),
    (
        (
            lambda selected: "not an object",
            "selected_authority must be an object",
        ),
        (
            lambda selected: {**cast(dict[str, object], selected), "record_kind": "x"},
            "unsupported selected_authority.record_kind",
        ),
        (
            lambda selected: {
                **cast(dict[str, object], selected),
                "schema_version": 99,
            },
            "unsupported selected_authority.schema_version",
        ),
        (
            lambda selected: {
                key: value
                for key, value in cast(dict[str, object], selected).items()
                if key != "partitions"
            },
            "missing selected_authority key: partitions",
        ),
        (
            lambda selected: {
                **cast(dict[str, object], selected),
                "unexpected": "extra",
            },
            "extra selected_authority key: unexpected",
        ),
        (
            lambda selected: {
                **cast(dict[str, object], selected),
                "workflow": {
                    **cast(
                        dict[str, object],
                        cast(dict[str, object], selected)["workflow"],
                    ),
                    "workflow_id": "tampered",
                },
            },
            "workflow_id mismatch",
        ),
        (
            lambda selected: {
                **cast(dict[str, object], selected),
                "workflow": {
                    **cast(
                        dict[str, object],
                        cast(dict[str, object], selected)["workflow"],
                    ),
                    "workflow_version": "tampered",
                },
            },
            "workflow_version mismatch",
        ),
    ),
)
def test_verify_refuses_invalid_selected_authority_root_shape(
    mutator: Callable[[object], object],
    message: str,
) -> None:
    from millrace.compiler import verify_compiled_plan_export_record
    from millrace.compiler.export import CompiledPlanExportError

    tampered = _mutate_selected_authority(mutator)

    with pytest.raises(CompiledPlanExportError, match=message):
        verify_compiled_plan_export_record(tampered)


@pytest.mark.parametrize(
    "field",
    (
        "record_kind",
        "schema_version",
        "workflow",
        "compatibility_profile",
        "workflow_package_pin",
        "required_extensions",
        "partitions",
        "queue_families",
        "graphs",
        "external_enqueue_routes",
        "generated_work_routes",
        "fanout_declarations",
        "join_declarations",
        "concurrency_policies",
        "artifact_schemas",
        "assets",
        "stage_kinds",
        "terminal_outcomes",
        "terminal_actions",
        "effect_declarations",
        "recovery_policies",
        "completion_behaviors",
        "remediation_policies",
        "wait_states",
        "counters",
        "lineage_policy",
        "intervention_options",
        "runner_bindings",
        "operator_waits",
        "capabilities",
    ),
)
def test_verify_refuses_each_missing_selected_authority_key(field: str) -> None:
    from millrace.compiler import verify_compiled_plan_export_record
    from millrace.compiler.export import CompiledPlanExportError

    tampered = _mutate_selected_authority(
        lambda selected: {
            key: value
            for key, value in cast(dict[str, object], selected).items()
            if key != field
        }
    )

    with pytest.raises(
        CompiledPlanExportError,
        match=f"missing selected_authority key: {field}",
    ):
        verify_compiled_plan_export_record(tampered)


def test_export_preserves_non_empty_capability_authority() -> None:
    plan = _compile_plan(_source())
    parsed = _parsed_export(plan)
    selected_authority = cast(dict[str, object], parsed["selected_authority"])

    capabilities = cast(list[dict[str, object]], selected_authority["capabilities"])
    runner_bindings = cast(
        list[dict[str, object]],
        selected_authority["runner_bindings"],
    )

    runner_invoke = next(
        capability
        for capability in capabilities
        if capability["id"] == "capability.runner.invoke"
    )
    assert runner_invoke["capability_kind"] == "runner.invoke"
    assert runner_invoke["grant_status"] == "granted"
    assert all(
        "capability.runner.invoke" in binding["required_capability_ids"]
        for binding in runner_bindings
    )


def test_effect_declarations_round_trip_through_export_and_codecs() -> None:
    from millrace.substrate.codecs import (
        decode_selected_compiled_plan,
        dumps_cas_object,
        encode_selected_compiled_plan,
        loads_cas_object,
    )
    from millrace.substrate.records import SELECTED_COMPILED_PLAN_OBJECT_KIND

    plan = _compile_plan(_source_with_effect_declaration())
    parsed = _parsed_export(plan)
    selected_authority = cast(dict[str, object], parsed["selected_authority"])
    effect_declarations = cast(
        list[dict[str, object]],
        selected_authority["effect_declarations"],
    )

    assert effect_declarations == [
        {
            "effect_declaration_id": "kernel_ping.effect.record_task",
            "terminal_action_id": "kernel_ping.close_taskmaster_effect_ready",
            "artifact_schema_id": "kernel_ping.task_artifact",
            "provider_ref": "provider.fake_local.workspace",
            "capability_policy_ref": "policy.fake_local.no_real_side_effects",
            "target_ref_kind": "workspace_record",
            "target_ref_schema": "kernel_ping.effects.target.workspace_record.v1",
            "allowed_reconciliation_statuses": ["applied", "no_op", "refused"],
            "real_side_effects_allowed": False,
            "record_kind": "effect_declaration",
            "schema_version": 1,
        }
    ]

    envelope = encode_selected_compiled_plan(plan)
    decoded_envelope = loads_cas_object(
        dumps_cas_object(envelope),
        expected_object_kind=SELECTED_COMPILED_PLAN_OBJECT_KIND,
    )
    decoded = decode_selected_compiled_plan(decoded_envelope)

    assert decoded == plan
    assert decoded.effect_declarations == plan.effect_declarations


def test_effect_declaration_drift_changes_selected_fingerprint() -> None:
    source = _source_with_effect_declaration()
    drifted_source = deepcopy(source)
    effect = next(
        cast(dict[str, object], item)
        for item in cast(list[object], drifted_source["effect_declarations"])
        if cast(dict[str, object], item)["id"]
        == "kernel_ping.effect.record_task"
    )
    effect["target_ref_schema"] = "kernel_ping.effects.target.workspace_record.v2"

    base_plan = _compile_plan(source)
    drifted_plan = _compile_plan(drifted_source)

    assert authority_fingerprint(base_plan) != authority_fingerprint(drifted_plan)
    assert (
        _parsed_export(base_plan)["authority_fingerprint"]
        != _parsed_export(drifted_plan)["authority_fingerprint"]
    )


def test_export_preserves_selected_graph_authority() -> None:
    plan = _compile_plan(_source())
    parsed = _parsed_export(plan)
    selected_authority = cast(dict[str, object], parsed["selected_authority"])
    graphs = cast(list[dict[str, object]], selected_authority["graphs"])

    assert graphs == [
        {
            "id": "kernel_ping.graph",
            "node_ids": [
                "kernel_ping.taskmaster.start",
                "kernel_ping.worker.start",
                "kernel_ping.taskmaster.review",
            ],
            "record_kind": "graph_declaration",
            "schema_version": 1,
        }
    ]


@pytest.mark.parametrize(
    ("mutator", "message"),
    (
        (
            lambda selected: {
                **cast(dict[str, object], selected),
                "schema_version": True,
            },
            "unsupported selected_authority.schema_version",
        ),
        (
            lambda selected: {
                **cast(dict[str, object], selected),
                "schema_version": 1.0,
            },
            "unsupported selected_authority.schema_version",
        ),
        (
            lambda selected: {
                **cast(dict[str, object], selected),
                "schema_version": "1",
            },
            "unsupported selected_authority.schema_version",
        ),
        (
            lambda selected: {
                **cast(dict[str, object], selected),
                "workflow": "not an object",
            },
            "selected_authority.workflow must be an object",
        ),
        (
            lambda selected: {
                **cast(dict[str, object], selected),
                "workflow": {
                    key: value
                    for key, value in cast(
                        dict[str, object],
                        cast(dict[str, object], selected)["workflow"],
                    ).items()
                    if key != "workflow_id"
                },
            },
            "workflow_id mismatch",
        ),
        (
            lambda selected: {
                **cast(dict[str, object], selected),
                "workflow": {
                    **cast(
                        dict[str, object],
                        cast(dict[str, object], selected)["workflow"],
                    ),
                    "workflow_id": 1,
                },
            },
            "workflow_id mismatch",
        ),
    ),
)
def test_verify_refuses_selected_authority_type_and_workflow_shape_traps(
    mutator: Callable[[object], object],
    message: str,
) -> None:
    from millrace.compiler import verify_compiled_plan_export_record
    from millrace.compiler.export import CompiledPlanExportError

    tampered = _mutate_selected_authority(mutator)

    with pytest.raises(CompiledPlanExportError, match=message):
        verify_compiled_plan_export_record(tampered)


def test_verify_wraps_selected_authority_canonicalization_errors() -> None:
    from millrace.compiler import verify_compiled_plan_export_record
    from millrace.compiler.export import CompiledPlanExportError

    record = _valid_export_record()
    selected_authority = cast(dict[str, object], record["selected_authority"])
    queue_families = cast(list[object], selected_authority["queue_families"])
    queue_family = cast(dict[str, object], queue_families[0])
    queue_family["external_enqueue"] = 1.5
    record["authority_fingerprint"] = "sha256:" + ("0" * 64)

    with pytest.raises(
        CompiledPlanExportError,
        match="selected_authority is not canonical",
    ):
        verify_compiled_plan_export_record(record)


def test_verify_refuses_non_json_like_in_memory_selected_authority_value() -> None:
    from millrace.compiler import verify_compiled_plan_export_record
    from millrace.compiler.export import CompiledPlanExportError

    record = _valid_export_record()
    selected_authority = cast(dict[str, object], record["selected_authority"])
    selected_authority["partitions"] = (
        PartitionDeclaration(
            id=PartitionId("kernel_ping.extra"),
            partition_kind="extra",
            presentation={},
        ),
    )

    with pytest.raises(
        CompiledPlanExportError,
        match="unsupported export value type: PartitionDeclaration",
    ):
        verify_compiled_plan_export_record(record)


def test_verify_record_wraps_unpaired_surrogate_canonicalization_failure() -> None:
    from millrace.compiler import verify_compiled_plan_export_record
    from millrace.compiler.export import CompiledPlanExportError

    record = _valid_export_record()
    selected_authority = cast(dict[str, object], record["selected_authority"])
    workflow = cast(dict[str, object], selected_authority["workflow"])
    workflow["workflow_name"] = "\ud800"

    with pytest.raises(
        CompiledPlanExportError,
        match="selected_authority is not canonical",
    ):
        verify_compiled_plan_export_record(record)


def test_verify_bytes_wraps_unpaired_surrogate_canonicalization_failure() -> None:
    from millrace.compiler import verify_compiled_plan_export_bytes
    from millrace.compiler.export import CompiledPlanExportError

    record = _valid_export_record()
    selected_authority = cast(dict[str, object], record["selected_authority"])
    workflow = cast(dict[str, object], selected_authority["workflow"])
    workflow["workflow_name"] = "\ud800"
    record["authority_fingerprint"] = "sha256:bad"
    payload = json.dumps(
        record,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")

    with pytest.raises(
        CompiledPlanExportError,
        match="selected_authority is not canonical",
    ):
        verify_compiled_plan_export_bytes(payload)


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        (
            b'{"record_kind":"compiled_plan_export","record_kind":"other"}',
            "duplicate JSON object key: record_kind",
        ),
        (
            b'{"selected_authority":{"record_kind":"selected_compiled_plan",'
            b'"record_kind":"other"}}',
            "duplicate JSON object key: record_kind",
        ),
        (
            '{"caf\u00e9":1,"cafe\u0301":2}'.encode("utf-8"),
            "duplicate JSON object key: café",
        ),
        (
            '{"cafe\u0301":1}'.encode("utf-8"),
            "non-NFC JSON object key: café",
        ),
    ),
)
def test_verify_refuses_duplicate_and_non_nfc_json_keys(
    payload: bytes,
    message: str,
) -> None:
    from millrace.compiler import verify_compiled_plan_export_bytes
    from millrace.compiler.export import CompiledPlanExportError

    with pytest.raises(CompiledPlanExportError, match=message):
        verify_compiled_plan_export_bytes(payload)


def test_verify_refuses_duplicate_nested_key_in_otherwise_valid_export_json() -> None:
    from millrace.compiler import (
        compiled_plan_export_bytes,
        verify_compiled_plan_export_bytes,
    )
    from millrace.compiler.export import CompiledPlanExportError

    plan = _compile_plan(_source())
    payload = (
        compiled_plan_export_bytes(plan)
        .decode("utf-8")
        .replace(
            '"selected_authority":{',
            '"selected_authority":{"record_kind":"duplicate",',
            1,
        )
    )

    with pytest.raises(
        CompiledPlanExportError,
        match="duplicate JSON object key: record_kind",
    ):
        verify_compiled_plan_export_bytes(payload.encode("utf-8"))


@pytest.mark.parametrize(
    ("mutator", "message"),
    (
        (
            lambda record: {
                **cast(dict[str, object], record),
                "selected_authority": {
                    **cast(dict[str, object], record["selected_authority"]),
                    "nested": {"cafe\u0301": 1},
                },
            },
            "non-NFC export key: café",
        ),
        (
            lambda record: {
                **cast(dict[str, object], record),
                "selected_authority": {
                    **cast(dict[str, object], record["selected_authority"]),
                    "nested": {"café": 1, "cafe\u0301": 2},
                },
            },
            "duplicate export key: café",
        ),
        (
            lambda record: {
                **cast(dict[str, object], record),
                "selected_authority": {
                    **cast(dict[str, object], record["selected_authority"]),
                    "nested": {1: "bad"},
                },
            },
            "export key must be a string",
        ),
    ),
)
def test_verify_refuses_nested_in_memory_object_key_issues(
    mutator: Callable[[Mapping[str, object]], Mapping[object, object]],
    message: str,
) -> None:
    from millrace.compiler import verify_compiled_plan_export_record
    from millrace.compiler.export import CompiledPlanExportError

    with pytest.raises(CompiledPlanExportError, match=message):
        mutated = mutator(_valid_export_record())
        verify_compiled_plan_export_record(mutated)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        (b"{invalid", "invalid JSON"),
        (b"\xff", "invalid UTF-8 export bytes"),
        (b"NaN", "invalid JSON constant: NaN"),
        (b"Infinity", "invalid JSON constant: Infinity"),
        (b"-Infinity", "invalid JSON constant: -Infinity"),
        (b"[]", "export root must be an object"),
        (b'"not an object"', "export root must be an object"),
    ),
)
def test_verify_refuses_malformed_export_bytes(
    payload: bytes,
    message: str,
) -> None:
    from millrace.compiler import verify_compiled_plan_export_bytes
    from millrace.compiler.export import CompiledPlanExportError

    with pytest.raises(CompiledPlanExportError, match=message):
        verify_compiled_plan_export_bytes(payload)


@pytest.mark.parametrize(
    ("mutator", "message"),
    (
        (
            lambda record: {
                key: value
                for key, value in cast(dict[str, object], record).items()
                if key != "hash_algorithm"
            },
            "missing export key: hash_algorithm",
        ),
        (
            lambda record: {**cast(dict[str, object], record), "extra": "x"},
            "extra export key: extra",
        ),
        (
            _with_non_string_key,
            "export key must be a string",
        ),
    ),
)
def test_verify_refuses_missing_extra_and_non_string_record_keys(
    mutator: Callable[[object], Mapping[object, object]],
    message: str,
) -> None:
    from millrace.compiler import verify_compiled_plan_export_record
    from millrace.compiler.export import CompiledPlanExportError

    record = _parsed_export(_compile_plan(_source()))
    mutated = mutator(record)

    with pytest.raises(CompiledPlanExportError, match=message):
        verify_compiled_plan_export_record(mutated)  # type: ignore[arg-type]


@pytest.mark.parametrize("root", ([], "not an object", None))
def test_verify_record_refuses_non_mapping_roots(root: object) -> None:
    from millrace.compiler import verify_compiled_plan_export_record
    from millrace.compiler.export import CompiledPlanExportError

    with pytest.raises(CompiledPlanExportError, match="export root must be an object"):
        verify_compiled_plan_export_record(root)  # type: ignore[arg-type]


def test_export_bytes_change_when_selected_authority_changes() -> None:
    from millrace.compiler import compiled_plan_export_bytes

    base_plan = _compile_plan(_source())
    no_pause_plan = _compile_plan(kernel_ping_support.no_pause_workflow_source())

    base_export = _parsed_export(base_plan)
    no_pause_export = _parsed_export(no_pause_plan)

    assert authority_fingerprint(base_plan) != authority_fingerprint(no_pause_plan)
    assert compiled_plan_export_bytes(base_plan) != compiled_plan_export_bytes(
        no_pause_plan
    )
    assert base_export["schema_version"] == no_pause_export["schema_version"]
    assert (
        base_export["canonicalization_algorithm"]
        == no_pause_export["canonicalization_algorithm"]
    )


def test_export_module_does_not_read_runtime_environment_or_substrate_codecs() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "millrace"
        / "compiler"
        / "export.py"
    ).read_text(encoding="utf-8")

    forbidden_tokens = (
        "importlib.metadata",
        "import os",
        "from os",
        "import pathlib",
        "from pathlib",
        "import datetime",
        "from datetime",
        "import time",
        "from time",
        "import uuid",
        "from uuid",
        "import random",
        "from random",
        "repr(",
        "millrace.substrate",
        "encode_selected_compiled_plan",
        "decode_selected_compiled_plan",
    )
    assert [token for token in forbidden_tokens if token in source] == []
