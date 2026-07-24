from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from typing import cast

from millrace.compiler import compiled_plan_export_bytes, compiled_plan_export_record
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts import SelectedCompiledPlan
from support import vendor_selection

Source = dict[str, object]
Record = dict[str, object]


def _records(source: Source, key: str) -> list[Record]:
    return vendor_selection.records(source, key)


def _compile_plan(source: Source) -> SelectedCompiledPlan:
    plan, _fingerprint = vendor_selection.compile_vendor_selection(source)
    return plan


def _export_json(plan: SelectedCompiledPlan) -> dict[str, object]:
    parsed = json.loads(compiled_plan_export_bytes(plan).decode("utf-8"))
    assert isinstance(parsed, dict)
    return cast(dict[str, object], parsed)


def _fingerprint(source: Source) -> str:
    return authority_fingerprint(_compile_plan(source))


def _mutate_join(source: Source) -> None:
    join = _records(source, "join_declarations")[0]
    join["id"] = "candidate_evidence_join.v2"


def _mutate_concurrency(source: Source) -> None:
    policy = next(
        policy
        for policy in _records(source, "concurrency_policies")
        if policy["partition_id"] == "evaluation"
    )
    policy["max_active_runs"] = 3


def _mutate_wait(source: Source) -> None:
    wait = _records(source, "operator_waits")[0]
    wait["id"] = "vendor_selection.award_operator_wait.v2"


def _mutate_fanout(source: Source) -> None:
    fanout = _records(source, "fanout_declarations")[0]
    fanout["id"] = "vendor_selection.candidate_packager.rubric_fanout.v2"


def _mutate_schema(source: Source) -> None:
    schema = next(
        schema
        for schema in _records(source, "artifact_schemas")
        if schema["id"] == "PurchaseRequest"
    )
    raw_schema = cast(Record, schema["schema"])
    properties = cast(Record, raw_schema["properties"])
    request_id = cast(Record, properties["request_id"])
    request_id["min_length"] = 2


def _mutate_terminal_action(source: Source) -> None:
    action = _records(source, "terminal_actions")[0]
    action["id"] = "vendor_selection.request_intake.request_ready.v2"


def test_vendor_selection_export_is_stable_and_selected_only() -> None:
    first_source = vendor_selection.source()
    second_source = deepcopy(first_source)
    first_plan = _compile_plan(first_source)
    second_plan = _compile_plan(second_source)

    first_bytes = compiled_plan_export_bytes(first_plan)
    second_bytes = compiled_plan_export_bytes(second_plan)

    assert first_bytes == second_bytes
    assert first_bytes == json.dumps(
        json.loads(first_bytes.decode("utf-8")),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    export = _export_json(first_plan)
    selected = cast(dict[str, object], export["selected_authority"])
    assert export["authority_fingerprint"] == authority_fingerprint(first_plan)
    assert selected["workflow"] == {
        "record_kind": "workflow_identity",
        "schema_version": 1,
        "workflow_id": "vendor_selection",
        "workflow_name": "Vendor Selection",
        "workflow_version": "0.1",
    }
    assert len(cast(list[object], selected["join_declarations"])) == 1
    assert b"vendor_selection.catalog.alpha" not in first_bytes
    assert b"Alpha Stationery" not in first_bytes
    assert b"unselected_catalog" not in first_bytes


def test_vendor_selection_fingerprint_changes_for_selected_join_concurrency_and_wait_authority(  # noqa: E501
) -> None:
    base = vendor_selection.source()
    base_fingerprint = _fingerprint(base)
    mutators: tuple[Callable[[Source], None], ...] = (
        _mutate_join,
        _mutate_concurrency,
        _mutate_wait,
        _mutate_fanout,
        _mutate_schema,
        _mutate_terminal_action,
    )

    changed = []
    for mutator in mutators:
        mutated = vendor_selection.source()
        mutator(mutated)
        changed.append(_fingerprint(mutated))

    assert all(fingerprint != base_fingerprint for fingerprint in changed)
    assert len(set(changed)) == len(changed)


def test_vendor_selection_fingerprint_ignores_unselected_catalog_data() -> None:
    base = vendor_selection.source()
    mutated = vendor_selection.source()
    catalog = _records(mutated, "unselected_catalog")
    catalog[0]["vendor_label"] = "Changed Label"
    catalog.append(
        {
            "id": "vendor_selection.catalog.delta",
            "candidate_id": "vendor_delta",
            "vendor_label": "Delta Supply",
            "capabilities": ("standard_office_supplies",),
            "budget_band": "high",
            "conflict_flag": "clear",
        }
    )

    assert _fingerprint(mutated) == _fingerprint(base)
    assert compiled_plan_export_bytes(_compile_plan(mutated)) == (
        compiled_plan_export_bytes(_compile_plan(base))
    )


def test_vendor_selection_export_has_no_lad_defaults_or_effect_provider_scope() -> None:
    plan = _compile_plan(vendor_selection.source())
    export = compiled_plan_export_record(plan)
    selected = cast(dict[str, object], export["selected_authority"])
    rendered = json.dumps(export, sort_keys=True)

    assert selected["compatibility_profile"] is None
    assert selected["required_extensions"] == []
    assert selected["effect_declarations"] == []
    assert selected["capabilities"] == [
        {
            "approval_policy_id": None,
            "capability_kind": "runner.invoke",
            "grant_status": "granted",
            "id": "capability.runner.invoke",
            "record_kind": "capability_declaration",
            "schema_version": 1,
            "support_status": "supported",
        }
    ]

    forbidden = (
        "lad_codex",
        "kernel_ping",
        "simple_loop",
        "execution",
        "planning",
        "learning",
        "provider.fake_local.workspace",
        "policy.fake_local.no_real_side_effects",
        "deferred_terminal_action",
        "marketplace",
        "plugin",
        "MCP",
        "native_runner",
    )
    assert [fragment for fragment in forbidden if fragment in rendered] == []
    assert "operator_gate" not in {
        cast(dict[str, object], stage)["id"]
        for stage in cast(list[object], selected["stage_kinds"])
    }
    assert "approve" not in {
        cast(dict[str, object], action)["action_kind"]
        for action in cast(list[object], selected["terminal_actions"])
    }
    assert "reject" not in {
        cast(dict[str, object], action)["action_kind"]
        for action in cast(list[object], selected["terminal_actions"])
    }
