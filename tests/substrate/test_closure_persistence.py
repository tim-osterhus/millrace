from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from tests.operator.test_status_projection import _closure_verdict_payload

from millrace.compiler import SelectedRunnerAdapterPolicy, compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.compiler.export import (
    CompiledPlanExportError,
    compiled_plan_export_record,
    verify_compiled_plan_export_record,
)
from millrace.contracts.compiled_plan import (
    AuthorityValue,
    CompletionBehaviorDeclaration,
    RemediationPolicyDeclaration,
    SelectedCompiledPlan,
    TerminalActionDeclaration,
)
from millrace.contracts.schema import validate_schema
from millrace.contracts.state import (
    ClosureTargetRecord,
    RuntimeState,
)
from millrace.contracts.transition import AdmitPlan
from millrace.kernel import decide, empty_runtime_state
from millrace.kernel.decision import _closure_verdict_refusal
from millrace.substrate.errors import StorageIntegrityError
from millrace.testing import deterministic_context
from substrate._runtime_store_support import (
    load_runtime_state,
    persist_runtime_state,
    runtime_store_paths,
)
from support import generic_lifecycle

COMPLETION_BEHAVIOR_ID = "lifecycle.completion"
_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)


def _closure_source() -> dict[str, object]:
    return generic_lifecycle.source_with_completion_behavior(
        accepted_root_source_kinds=("origin", "manual"),
        remediation_root_source_kind="origin",
    )


def _compile_closure_plan() -> tuple[SelectedCompiledPlan, str]:
    return generic_lifecycle.compile_lifecycle(_closure_source())


@pytest.mark.parametrize(
    ("corruption", "capacity"),
    (
        ("missing_pin", None),
        ("missing_capacity", "missing"),
        ("null_capacity", None),
        ("zero_capacity", 0),
        ("invalid_capacity", "invalid"),
        ("request_over_capacity", 16_383),
    ),
)
def test_completion_capacity_fails_closed_across_compiler_export_and_admission(
    corruption: str,
    capacity: object,
) -> None:
    source = _closure_source()
    runner = cast(list[dict[str, object]], source["runner_bindings"])[0]
    if corruption == "missing_pin":
        runner.pop("component_pin")
        runner["terminal_result_mappings"] = ()
    else:
        pin = cast(dict[str, object], runner["component_pin"])
        if capacity == "missing":
            pin.pop("max_work_item_payload_bytes")
        else:
            pin["max_work_item_payload_bytes"] = capacity
        if corruption == "request_over_capacity":
            behavior = cast(list[dict[str, object]], source["completion_behaviors"])[0]
            behavior["request_payload_byte_limit"] = 16_384

    compile_result = compile_workflow(
        source,
        selected_runner_policy=_CODEX_POLICY,
    )
    assert compile_result.plan is None
    assert any(
        diagnostic.code == "invalid_completion_behavior_declaration"
        for diagnostic in compile_result.diagnostics
        if diagnostic.severity == "error"
    )

    plan, _fingerprint = _compile_closure_plan()
    binding = plan.runner_bindings[0]
    if corruption == "missing_pin":
        object.__setattr__(binding, "component_pin", None)
    else:
        pin = binding.component_pin
        assert pin is not None
        if capacity == "missing":
            object.__setattr__(pin, "max_work_item_payload_bytes", None)
        else:
            object.__setattr__(pin, "max_work_item_payload_bytes", capacity)
        if corruption == "request_over_capacity":
            behavior = plan.completion_behaviors[0]
            object.__setattr__(behavior, "request_payload_byte_limit", 16_384)

    fingerprint = authority_fingerprint(plan)
    export = dict(compiled_plan_export_record(plan))
    selected = cast(dict[str, object], export["selected_authority"])
    export["authority_fingerprint"] = authority_fingerprint(selected)
    with pytest.raises(CompiledPlanExportError):
        verify_compiled_plan_export_record(export)

    decision = decide(
        empty_runtime_state(),
        AdmitPlan(
            f"admit-{corruption}",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        deterministic_context(transition_id=f"transition-admit-{corruption}"),
    )
    assert decision.accepted is False


@pytest.mark.parametrize(
    (
        "case",
        "expected_diagnostic_code",
        "expected_diagnostic_reason",
        "expected_export_error",
        "expected_admission_detail",
    ),
    (
        (
            "missing_evidence_schema_ids",
            "invalid_completion_behavior_declaration",
            "missing_evidence_artifact_schema_ids",
            "missing completion behavior key: evidence_artifact_schema_ids",
            "completion_behavior_evidence_schema:lifecycle.completion",
        ),
        (
            "duplicate_evidence_schema_ids",
            "invalid_completion_behavior_declaration",
            "duplicate_evidence_artifact_schema_id",
            (
                "completion behavior evidence_artifact_schema_ids must contain "
                "unique values"
            ),
            "completion_behavior_evidence_schema:lifecycle.completion",
        ),
        (
            "unknown_evidence_schema_id",
            "missing_reference",
            None,
            "completion behavior references an unknown evidence artifact schema",
            "completion_behavior_evidence_schema:lifecycle.completion",
        ),
        (
            "evidence_item_limit_zero",
            "invalid_completion_behavior_declaration",
            "invalid_evidence_item_limit",
            "completion behavior evidence_item_limit is outside 1..256",
            "completion_behavior_evidence_item_limit:lifecycle.completion",
        ),
        (
            "evidence_item_limit_too_large",
            "invalid_completion_behavior_declaration",
            "invalid_evidence_item_limit",
            "completion behavior evidence_item_limit is outside 1..256",
            "completion_behavior_evidence_item_limit:lifecycle.completion",
        ),
        (
            "request_payload_limit_zero",
            "invalid_completion_behavior_declaration",
            "invalid_request_payload_byte_limit",
            "completion behavior request_payload_byte_limit must be positive",
            "completion_behavior_request_payload_limit:lifecycle.completion",
        ),
        (
            "request_payload_limit_negative",
            "invalid_completion_behavior_declaration",
            "invalid_request_payload_byte_limit",
            "completion behavior request_payload_byte_limit must be positive",
            "completion_behavior_request_payload_limit:lifecycle.completion",
        ),
        (
            "request_payload_limit_over_capacity",
            "invalid_completion_behavior_declaration",
            "request_payload_byte_limit_exceeds_runner_capacity",
            "completion behavior request payload limit exceeds runner capacity",
            "completion_behavior_request_payload_capacity:lifecycle.completion",
        ),
    ),
)
def test_completion_authority_hostile_matrix_fails_closed_at_all_boundaries(
    case: str,
    expected_diagnostic_code: str,
    expected_diagnostic_reason: str | None,
    expected_export_error: str,
    expected_admission_detail: str,
) -> None:
    source = _closure_source()
    source_behavior = cast(list[dict[str, object]], source["completion_behaviors"])[0]
    if case == "missing_evidence_schema_ids":
        source_behavior.pop("evidence_artifact_schema_ids")
    elif case == "duplicate_evidence_schema_ids":
        source_behavior["evidence_artifact_schema_ids"] = (
            generic_lifecycle.BETA_REPORT_SCHEMA_ID,
            generic_lifecycle.BETA_REPORT_SCHEMA_ID,
        )
    elif case == "unknown_evidence_schema_id":
        source_behavior["evidence_artifact_schema_ids"] = (
            generic_lifecycle.BETA_REPORT_SCHEMA_ID,
            "LifecycleUnknownEvidence",
        )
    elif case == "evidence_item_limit_zero":
        source_behavior["evidence_item_limit"] = 0
    elif case == "evidence_item_limit_too_large":
        source_behavior["evidence_item_limit"] = 257
    elif case == "request_payload_limit_zero":
        source_behavior["request_payload_byte_limit"] = 0
    elif case == "request_payload_limit_negative":
        source_behavior["request_payload_byte_limit"] = -1
    else:
        source_behavior["request_payload_byte_limit"] = 16_385

    compile_result = compile_workflow(
        source,
        selected_runner_policy=_CODEX_POLICY,
    )
    assert compile_result.plan is None
    compiler_diagnostic = next(
        diagnostic
        for diagnostic in compile_result.diagnostics
        if diagnostic.severity == "error"
        and diagnostic.code == expected_diagnostic_code
    )
    if expected_diagnostic_reason is not None:
        assert compiler_diagnostic.context["reason"] == expected_diagnostic_reason
    else:
        assert compiler_diagnostic.context["reference_kind"] == "artifact_schema"
        assert compiler_diagnostic.context["referenced_id"] == (
            "LifecycleUnknownEvidence"
        )

    plan, _fingerprint = _compile_closure_plan()
    plan_behavior, _policy, _actions = _selected_authority(plan)
    if case == "missing_evidence_schema_ids":
        object.__setattr__(plan_behavior, "evidence_artifact_schema_ids", ())
    elif case == "duplicate_evidence_schema_ids":
        object.__setattr__(
            plan_behavior,
            "evidence_artifact_schema_ids",
            (
                generic_lifecycle.BETA_REPORT_SCHEMA_ID,
                generic_lifecycle.BETA_REPORT_SCHEMA_ID,
            ),
        )
    elif case == "unknown_evidence_schema_id":
        object.__setattr__(
            plan_behavior,
            "evidence_artifact_schema_ids",
            (
                generic_lifecycle.BETA_REPORT_SCHEMA_ID,
                "LifecycleUnknownEvidence",
            ),
        )
    elif case == "evidence_item_limit_zero":
        object.__setattr__(plan_behavior, "evidence_item_limit", 0)
    elif case == "evidence_item_limit_too_large":
        object.__setattr__(plan_behavior, "evidence_item_limit", 257)
    elif case == "request_payload_limit_zero":
        object.__setattr__(plan_behavior, "request_payload_byte_limit", 0)
    elif case == "request_payload_limit_negative":
        object.__setattr__(plan_behavior, "request_payload_byte_limit", -1)
    else:
        object.__setattr__(plan_behavior, "request_payload_byte_limit", 16_385)

    record = deepcopy(dict(compiled_plan_export_record(plan)))
    selected = cast(dict[str, object], record["selected_authority"])
    exported_behavior = cast(
        list[dict[str, object]],
        selected["completion_behaviors"],
    )[0]
    if case == "missing_evidence_schema_ids":
        exported_behavior.pop("evidence_artifact_schema_ids")
    elif case == "duplicate_evidence_schema_ids":
        exported_behavior["evidence_artifact_schema_ids"] = [
            generic_lifecycle.BETA_REPORT_SCHEMA_ID,
            generic_lifecycle.BETA_REPORT_SCHEMA_ID,
        ]
    elif case == "unknown_evidence_schema_id":
        exported_behavior["evidence_artifact_schema_ids"] = [
            generic_lifecycle.BETA_REPORT_SCHEMA_ID,
            "LifecycleUnknownEvidence",
        ]
    elif case == "evidence_item_limit_zero":
        exported_behavior["evidence_item_limit"] = 0
    elif case == "evidence_item_limit_too_large":
        exported_behavior["evidence_item_limit"] = 257
    elif case == "request_payload_limit_zero":
        exported_behavior["request_payload_byte_limit"] = 0
    elif case == "request_payload_limit_negative":
        exported_behavior["request_payload_byte_limit"] = -1
    else:
        exported_behavior["request_payload_byte_limit"] = 16_385
    record["authority_fingerprint"] = authority_fingerprint(selected)

    with pytest.raises(CompiledPlanExportError) as export_error:
        verify_compiled_plan_export_record(record)
    assert str(export_error.value) == expected_export_error

    fingerprint = authority_fingerprint(plan)
    decision = decide(
        empty_runtime_state(),
        AdmitPlan(
            f"admit-hostile-{case}",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        deterministic_context(transition_id=f"transition-admit-hostile-{case}"),
    )
    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_selected_authority"
    assert decision.refusal.detail == expected_admission_detail


def test_evidence_only_completion_schema_survives_compile_export_and_admission(
) -> None:
    source = _closure_source()
    evidence_only_schema_id = "LifecycleEvidenceOnly"
    cast(list[dict[str, object]], source["artifact_schemas"]).append(
        {
            "id": evidence_only_schema_id,
            "schema": {
                "type": "object",
                "required": ("summary",),
                "properties": {"summary": {"type": "string", "min_length": 1}},
            },
            "presentation": {"display_name": "Evidence-only report"},
        }
    )
    behavior = cast(list[dict[str, object]], source["completion_behaviors"])[0]
    behavior["evidence_artifact_schema_ids"] = (
        generic_lifecycle.BETA_REPORT_SCHEMA_ID,
        evidence_only_schema_id,
    )

    compile_result = compile_workflow(
        source,
        selected_runner_policy=_CODEX_POLICY,
    )
    assert compile_result.plan is not None
    plan = compile_result.plan
    fingerprint = authority_fingerprint(plan)
    exported = compiled_plan_export_record(plan)
    verified = verify_compiled_plan_export_record(exported)
    selected = cast(dict[str, object], verified.selected_authority)
    exported_schemas = cast(list[dict[str, object]], selected["artifact_schemas"])
    assert evidence_only_schema_id in {
        str(schema["id"]) for schema in exported_schemas
    }

    decision = decide(
        empty_runtime_state(),
        AdmitPlan(
            "admit-evidence-only-schema",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        deterministic_context(transition_id="transition-admit-evidence-only-schema"),
    )
    assert decision.accepted is True


def test_closure_plan_export_round_trip_accepts_canonical_completion_authority(
) -> None:
    plan, _fingerprint = _compile_closure_plan()

    verified = verify_compiled_plan_export_record(compiled_plan_export_record(plan))

    assert verified.workflow_id == "lifecycle_probe"
    assert verified.plan_format_version == plan.schema_version


def test_closure_plan_export_accepts_completion_action_contract() -> None:
    plan, _fingerprint = _compile_closure_plan()

    verified = verify_compiled_plan_export_record(compiled_plan_export_record(plan))

    selected = cast(dict[str, object], verified.selected_authority)
    behavior = cast(list[dict[str, object]], selected["completion_behaviors"])[0]
    actions = {
        str(action["id"]): action
        for action in cast(list[dict[str, object]], selected["terminal_actions"])
    }
    expected_kinds = {
        "pass_action_id": {"close", "complete_work_item"},
        "gap_action_id": {"closure_gap"},
        "blocked_action_id": {"close", "block_work_item"},
    }
    assert all(
        actions[str(behavior[field])]["stage_kind_id"]
        == behavior["target_stage_kind_id"]
        and actions[str(behavior[field])]["action_kind"] in expected_kinds[field]
        and actions[str(behavior[field])]["artifact_schema_id"]
        == behavior["verdict_artifact_schema_id"]
        for field in ("pass_action_id", "gap_action_id", "blocked_action_id")
    )


@pytest.mark.parametrize(
    ("action_field", "mutation"),
    (
        ("pass_action_id", {"stage_kind_id": "alpha_stage"}),
        ("gap_action_id", {"stage_kind_id": "alpha_stage"}),
        ("blocked_action_id", {"stage_kind_id": "alpha_stage"}),
        ("pass_action_id", {"action_kind": "closure_gap"}),
        ("gap_action_id", {"action_kind": "complete_work_item"}),
        ("blocked_action_id", {"action_kind": "closure_gap"}),
    ),
)
def test_closure_plan_export_refuses_completion_action_contract_drift(
    action_field: str,
    mutation: Mapping[str, object],
) -> None:
    plan, _fingerprint = _compile_closure_plan()
    record = deepcopy(dict(compiled_plan_export_record(plan)))
    selected = cast(dict[str, object], record["selected_authority"])
    behavior = cast(list[dict[str, object]], selected["completion_behaviors"])[0]
    action_id = str(behavior[action_field])
    action = next(
        action
        for action in cast(list[dict[str, object]], selected["terminal_actions"])
        if action["id"] == action_id
    )
    action.update(mutation)
    record["authority_fingerprint"] = authority_fingerprint(selected)

    with pytest.raises(
        CompiledPlanExportError,
        match="completion behavior terminal action contract",
    ):
        verify_compiled_plan_export_record(record)


def _artifact_payload(kind: str) -> Mapping[str, AuthorityValue]:
    if kind != generic_lifecycle.BETA_REPORT_SCHEMA_ID:
        return generic_lifecycle.report_payload("alpha")
    return {
        "artifact_kind": "closure_verdict",
        "summary": "Review completed.",
        "closure_target_id": "closure-target",
        "root_contract_digest": "sha256:" + "a" * 64,
        "freshness_anchor_digest": "sha256:" + "b" * 64,
        "rubric": {
            "criteria": (
                {
                    "criterion_id": "criterion-1",
                    "requirement": "The closure contract is satisfied.",
                    "evidence_rule": "Use current review evidence.",
                },
            )
        },
        "criterion_results": (
            {
                "criterion_id": "criterion-1",
                "status": "passed",
                "provenance": "fresh",
                "evidence_refs": (
                    {"evidence_id": "evidence-1", "summary": "reviewed"},
                ),
            },
        ),
        "observations": (),
        "remediation_guidance": (),
        "confidence": "high",
        "residual_uncertainty": "none",
    }


def _closure_verdict_schema() -> dict[str, object]:
    string = {"type": "string", "min_length": 1}
    evidence_ref = {
        "type": "object",
        "required": ("evidence_id", "summary"),
        "properties": {"evidence_id": string, "summary": string},
    }
    criterion = {
        "type": "object",
        "required": ("criterion_id", "requirement", "evidence_rule"),
        "properties": {
            "criterion_id": string,
            "requirement": string,
            "evidence_rule": string,
        },
    }
    result = {
        "type": "object",
        "required": ("criterion_id", "status", "provenance", "evidence_refs"),
        "properties": {
            "criterion_id": string,
            "status": {"enum": ("passed", "failed", "blocked")},
            "provenance": {
                "enum": (
                    "fresh",
                    "revalidated",
                    "historical_only",
                    "missing",
                )
            },
            "evidence_refs": {
                "type": "array",
                "items": evidence_ref,
                "unique_by": "evidence_id",
            },
        },
    }
    guidance = {
        "type": "object",
        "required": ("guidance_id", "summary", "criterion_refs"),
        "properties": {
            "guidance_id": string,
            "summary": string,
            "criterion_refs": {
                "type": "array",
                "min_items": 1,
                "items": {
                    "type": "object",
                    "required": ("criterion_id",),
                    "properties": {"criterion_id": string},
                },
                "unique_by": "criterion_id",
            },
        },
    }
    properties = {
        "bundle_id": string,
        "artifact_kind": string,
        "summary": string,
        "closure_target_id": string,
        "root_contract_digest": string,
        "freshness_anchor_digest": string,
        "rubric": {
            "type": "object",
            "required": ("criteria",),
            "properties": {
                "criteria": {
                    "type": "array",
                    "min_items": 1,
                    "items": criterion,
                    "unique_by": "criterion_id",
                }
            },
        },
        "criterion_results": {
            "type": "array",
            "min_items": 1,
            "items": result,
            "unique_by": "criterion_id",
        },
        "observations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ("observation_id", "summary"),
                "properties": {"observation_id": string, "summary": string},
            },
            "unique_by": "observation_id",
        },
        "remediation_guidance": {
            "type": "array",
            "items": guidance,
            "unique_by": "guidance_id",
        },
        "confidence": {"enum": ("high", "medium", "low")},
        "residual_uncertainty": string,
    }
    return {
        "type": "object",
        "required": tuple(
            key for key in properties if key != "bundle_id"
        ),
        "properties": properties,
    }


def test_compiler_rejects_closure_schema_without_observation_identity_declaration(
) -> None:
    source = _closure_source()
    schema = next(
        row
        for row in cast(list[dict[str, object]], source["artifact_schemas"])
        if row["id"] == generic_lifecycle.BETA_REPORT_SCHEMA_ID
    )
    schema_body = cast(dict[str, object], schema["schema"])
    observations = cast(dict[str, object], schema_body["properties"])["observations"]
    cast(dict[str, object], observations).pop("unique_by")

    result = compile_workflow(source, selected_runner_policy=_CODEX_POLICY)

    assert result.plan is None
    assert any(
        diagnostic.code == "invalid_completion_behavior_declaration"
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    )


def test_terminal_semantics_accept_schema_valid_optional_root_field() -> None:
    payload = dict(_artifact_payload(generic_lifecycle.BETA_REPORT_SCHEMA_ID))
    payload["bundle_id"] = "optional-root-field"
    snapshot = {
        "closure_target_id": "closure-target",
        "root_contract": {"payload_digest": "sha256:" + "a" * 64},
        "freshness_anchor_digest": "sha256:" + "b" * 64,
    }

    assert validate_schema(_closure_verdict_schema(), payload).accepted
    assert (
        _closure_verdict_refusal(
            payload,
            snapshot=snapshot,
            prior_rubric=None,
            terminal_kind="pass",
        )
        is None
    )


def _selected_authority(
    plan: SelectedCompiledPlan,
) -> tuple[
    CompletionBehaviorDeclaration,
    RemediationPolicyDeclaration,
    dict[str, TerminalActionDeclaration],
]:
    behavior = next(
        item
        for item in plan.completion_behaviors
        if str(item.id) == COMPLETION_BEHAVIOR_ID
    )
    policy = next(
        item
        for item in plan.remediation_policies
        if item.id == behavior.remediation_policy_id
    )
    actions = {str(action.id): action for action in plan.terminal_actions}
    return behavior, policy, actions


def _closure_state(marker: str) -> tuple[RuntimeState, ClosureTargetRecord]:
    state, plan, fingerprint = generic_lifecycle.closure_evaluation_state(
        _closure_source()
    )
    evaluation = next(iter(state.closure_evaluations.values()))
    state = generic_lifecycle.claim_activation(
        state,
        activation_id=evaluation.target_activation_id,
        suffix="evaluator",
    )
    work_item = state.work_items[evaluation.target_work_item_id]
    state = generic_lifecycle.apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-evaluator",
        input_id="observe-evaluator",
        marker=marker,
        artifact_payload=_closure_verdict_payload(
            work_item.payload["closure_evidence_snapshot"], marker=marker
        ),
    )
    return state, next(iter(state.closure_targets.values()))


def _persisted_closure(
    tmp_path: Path, marker: str
) -> tuple[RuntimeState, ClosureTargetRecord, Path, Path]:
    state, target = _closure_state(marker)
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    return state, target, db_path, cas_root


def test_restart_refuses_false_operator_required_block(tmp_path: Path) -> None:
    state, _target, db_path, cas_root = _persisted_closure(
        tmp_path, "REVIEW_BLOCKED"
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE closure_blocked_records SET operator_required = 0")
    with pytest.raises(StorageIntegrityError, match="operator_required"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_closure_terminal_without_matching_evaluator(
    tmp_path: Path,
) -> None:
    state, _target, db_path, cas_root = _persisted_closure(
        tmp_path, "REVIEW_PASSED"
    )
    terminal = next(iter(state.closure_terminal_records.values()))

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE closure_terminal_records
            SET source_run_id = ?, source_artifact_id = NULL
            WHERE record_id = ?
            """,
            (
                "run-evaluator-incident",
                terminal.record_id,
            ),
        )

    with pytest.raises(
        StorageIntegrityError,
        match="closure_terminal_records.source_run_id must reference runs",
    ):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("target_id", "duplicate", "match"),
    (
        (
            "closure-target:caller-chosen",
            False,
            "closure_targets.closure_target_id is noncanonical",
        ),
        (
            "closure-target:duplicate",
            True,
            "closure_targets.logical_key must be unique",
        ),
    ),
)
def test_persistence_rejects_invalid_closure_target_identity(
    tmp_path: Path, target_id: str, duplicate: bool, match: str
) -> None:
    opened = generic_lifecycle.closure_opened_state
    state, _plan, _fingerprint = opened(_closure_source())
    target = next(iter(state.closure_targets.values()))
    invalid = replace(target, closure_target_id=target_id)
    targets = {invalid.closure_target_id: invalid}
    if duplicate:
        targets[target.closure_target_id] = target
    state = replace(
        state,
        closure_targets=targets,
    )
    db_path, cas_root = runtime_store_paths(tmp_path)

    with pytest.raises(StorageIntegrityError, match=match):
        persist_runtime_state(db_path, cas_root, state)


@pytest.mark.parametrize("lineage", (None, " "))
def test_persistence_rejects_bad_root_lineage(tmp_path: Path, lineage: object) -> None:
    opened = generic_lifecycle.closure_opened_state
    state, _plan, _fingerprint = opened(_closure_source())
    root = state.work_items["work-origin"]
    activation = state.activations["activation-origin"]
    state = replace(
        state, work_items={"work-origin": replace(root, lineage_id=lineage)},
        activations={activation.activation_id: replace(activation, lineage_id=lineage)},
    )
    db_path, cas_root = runtime_store_paths(tmp_path)
    with pytest.raises(StorageIntegrityError, match="invalid_closure_root_lineage"):
        persist_runtime_state(db_path, cas_root, state)


def test_persistence_rejects_duplicate_closure_evaluation_authority(
    tmp_path: Path,
) -> None:
    state, _plan, _fingerprint = generic_lifecycle.closure_evaluation_state(
        _closure_source()
    )
    evaluation = next(iter(state.closure_evaluations.values()))
    work = state.work_items[evaluation.target_work_item_id]
    activation = state.activations[evaluation.target_activation_id]
    duplicate_work_id = f"{work.ref.work_item_id}:duplicate"
    duplicate_activation_id = f"{activation.activation_id}:duplicate"
    duplicate_work = replace(
        work,
        ref=replace(work.ref, work_item_id=duplicate_work_id),
    )
    duplicate_activation = replace(
        activation,
        activation_id=duplicate_activation_id,
        work_item_id=duplicate_work_id,
    )
    duplicate_evaluation = replace(
        evaluation,
        record_id=f"closure-evaluator:{duplicate_activation_id}",
        target_work_item_id=duplicate_work_id,
        target_activation_id=duplicate_activation_id,
    )
    state = replace(
        state,
        work_items={**state.work_items, duplicate_work_id: duplicate_work},
        activations={
            **state.activations,
            duplicate_activation_id: duplicate_activation,
        },
        closure_evaluations={
            evaluation.record_id: evaluation,
            duplicate_evaluation.record_id: duplicate_evaluation,
        },
    )
    db_path, cas_root = runtime_store_paths(tmp_path)

    with pytest.raises(
        StorageIntegrityError,
        match="closure_evaluations evidence anchor must be unique",
    ):
        persist_runtime_state(db_path, cas_root, state)


@pytest.mark.parametrize("record_kind", ("target", "evaluation"))
@pytest.mark.parametrize("relation", ("missing_receipt", "forged_input"))
def test_persistence_rejects_invalid_closure_creator_relation(
    tmp_path: Path,
    record_kind: str,
    relation: str,
) -> None:
    state = (
        generic_lifecycle.closure_opened_state(_closure_source())[0]
        if record_kind == "target"
        else generic_lifecycle.closure_evaluation_state(_closure_source())[0]
    )
    if record_kind == "target":
        target = next(iter(state.closure_targets.values()))
        input_id = target.opened_by_input_id
        if relation == "forged_input":
            target = replace(target, opened_by_input_id="init-lifecycle")
            state = replace(
                state,
                closure_targets={target.closure_target_id: target},
            )
    else:
        evaluation = next(iter(state.closure_evaluations.values()))
        input_id = evaluation.created_by_input_id
        if relation == "forged_input":
            work = state.work_items[evaluation.target_work_item_id]
            work = replace(work, created_by_input_id="init-lifecycle")
            state = replace(state, work_items={
                **state.work_items, work.ref.work_item_id: work})
    if relation == "missing_receipt":
        state = replace(
            state,
            receipts={
                receipt_id: receipt
                for receipt_id, receipt in state.receipts.items()
                if receipt_id != input_id
            },
        )
    db_path, cas_root = runtime_store_paths(tmp_path)

    with pytest.raises(StorageIntegrityError):
        persist_runtime_state(db_path, cas_root, state)
