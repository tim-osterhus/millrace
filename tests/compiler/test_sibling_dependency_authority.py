from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

import pytest

from millrace.compiler import compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.compiler.export import (
    CompiledPlanExportError,
    compiled_plan_export_record,
    verify_compiled_plan_export_record,
)
from millrace.contracts import ActionId
from millrace.contracts.compiled_plan import canonical_authority_bytes
from millrace.contracts.transition import AdmitPlan, InitializeWorkspace
from millrace.kernel import apply, decide, empty_runtime_state
from millrace.substrate.codecs import (
    decode_selected_compiled_plan,
    encode_selected_compiled_plan,
)
from support import generic_fanout, sibling_dependencies


def test_opt_in_contract_roundtrips_and_version_two_fanout_golden_is_preserved():
    old, _ = generic_fanout.compile_fanout()
    old_record = encode_selected_compiled_plan(old).payload["fanout_declarations"][0]
    assert old_record["schema_version"] == 2
    assert "item_dependency_key" not in old_record
    assert "dependency_success_action_ids" not in old_record
    assert (
        sha256(canonical_authority_bytes(old.fanout_declarations[0])).hexdigest()
        == "9b0351dd84a62db2e2641e46bfa1f46fa2314a2e76a7cc64ae4bd7b2651f8c47"
    )
    plan, fingerprint = generic_fanout.compile_fanout(sibling_dependencies.source())
    encoded = encode_selected_compiled_plan(plan)
    assert encoded.payload["fanout_declarations"][0]["schema_version"] == 3
    assert decode_selected_compiled_plan(encoded) == plan
    verified = verify_compiled_plan_export_record(compiled_plan_export_record(plan))
    assert verified.authority_fingerprint == fingerprint
    assert plan.fanout_declarations[0].item_dependency_key == "requires"
    assert plan.fanout_declarations[0].dependency_success_action_ids == (
        ActionId("final.success"),
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_key",
        "missing_success",
        "null_fields",
        "unknown_success",
        "intermediate_success",
        "no_success_artifact",
        "duplicate_success",
        "missing_required_list",
        "wrong_list_type",
        "empty_string_item",
        "wrong_version",
        "unreachable_success",
    ),
)
def test_compiler_refuses_incomplete_dependency_contract(mutation):
    source = sibling_dependencies.source()
    fanout = source["fanout_declarations"][0]
    item_schema = source["artifact_schemas"][0]["schema"]["properties"]["items"][
        "items"
    ]
    if mutation == "missing_key":
        del fanout["item_dependency_key"]
    elif mutation == "missing_success":
        del fanout["dependency_success_action_ids"]
    elif mutation == "null_fields":
        fanout["item_dependency_key"] = fanout["dependency_success_action_ids"] = None
    elif mutation == "unknown_success":
        fanout["dependency_success_action_ids"] = ("unknown",)
    elif mutation == "intermediate_success":
        fanout["dependency_success_action_ids"] = ("child.next",)
    elif mutation == "no_success_artifact":
        next(a for a in source["terminal_actions"] if a["id"] == "final.success")[
            "artifact_schema_id"
        ] = None
    elif mutation == "duplicate_success":
        fanout["dependency_success_action_ids"] *= 2
    elif mutation == "missing_required_list":
        item_schema["required"] = ("item_id", "body")
    elif mutation == "wrong_list_type":
        item_schema["properties"]["requires"]["type"] = "string"
    elif mutation == "empty_string_item":
        del item_schema["properties"]["requires"]["items"]["min_length"]
    elif mutation == "wrong_version":
        fanout["schema_version"] = 4
    elif mutation == "unreachable_success":
        source["terminal_actions"] = [
            a for a in source["terminal_actions"] if a["id"] != "child.next"
        ]
        source["terminal_outcomes"] = [
            o for o in source["terminal_outcomes"] if o["id"] != "child.next"
        ]
        source["stage_kinds"][1]["declared_outcome_ids"] = ("child.blocked",)
    result = compile_workflow(
        source, selected_runner_policy=generic_fanout._CODEX_POLICY
    )
    assert result.plan is None
    errors = [d for d in result.diagnostics if d.code == "invalid_fanout_declaration"]
    assert errors and all(
        d.declaration_path.startswith("fanout_declarations[0]") for d in errors
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "unknown_success",
        "duplicate_success",
        "empty_success",
        "empty_key",
        "same_id_key",
    ),
)
def test_selected_admission_and_export_refuse_mutated_authority(mutation):
    plan, _ = generic_fanout.compile_fanout(sibling_dependencies.source())
    fanout = plan.fanout_declarations[0]
    changes = {
        "unknown_success": {"dependency_success_action_ids": (ActionId("unknown"),)},
        "duplicate_success": {
            "dependency_success_action_ids": fanout.dependency_success_action_ids * 2
        },
        "empty_success": {"dependency_success_action_ids": ()},
        "empty_key": {"item_dependency_key": ""},
        "same_id_key": {"item_dependency_key": "item_id"},
    }[mutation]
    plan = replace(plan, fanout_declarations=(replace(fanout, **changes),))
    state = empty_runtime_state()
    state = apply(
        state,
        decide(state, InitializeWorkspace("init"), generic_fanout.context("init")),
    )
    command = AdmitPlan(
        "bad-admit",
        selected_plan=plan,
        authority_fingerprint=authority_fingerprint(plan),
    )
    decision = decide(state, command, generic_fanout.context("bad-admit"))
    assert not decision.accepted
    assert decision.refusal.reason == "unsupported_selected_authority"
    assert not apply(state, decision).admitted_plans
    with pytest.raises(CompiledPlanExportError, match="sibling dependency"):
        verify_compiled_plan_export_record(compiled_plan_export_record(plan))
