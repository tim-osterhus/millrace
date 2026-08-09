from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from millrace.compiler import SelectedRunnerAdapterPolicy, compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.compiler.export import (
    CompiledPlanExportError,
    compiled_plan_export_record,
    verify_compiled_plan_export_record,
)
from millrace.contracts import ActionId, QueueFamilyId
from millrace.contracts.compiled_plan import (
    AuthorityValue,
    CompletionBehaviorDeclaration,
    RemediationPolicyDeclaration,
    SelectedCompiledPlan,
    TerminalActionDeclaration,
)
from millrace.contracts.schema import validate_schema
from millrace.contracts.state import (
    Activation,
    AdmittedPlan,
    ArtifactRecord,
    ClosureBlockedRecord,
    ClosureEvaluationRecord,
    ClosureTargetRecord,
    ClosureTerminalRecord,
    GovernanceEventRecord,
    InputReceipt,
    InputReceiptRef,
    PlanRef,
    RemediationWorkRecord,
    RunnerObservationRecord,
    RunRecord,
    RunRef,
    RuntimeState,
    TraceRecord,
    TransitionRecord,
    WorkItem,
    WorkItemRef,
)
from millrace.contracts.transition import (
    AdmitPlan,
    RunnerResultObserved,
    artifact_payload_digest,
    input_payload_digest,
)
from millrace.kernel import apply, decide, empty_runtime_state
from millrace.kernel.decision import (
    _closure_snapshot_authority_refusal,
    _closure_verdict_refusal,
    _completion_request_payload,
)
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.codecs import dumps_cas_object, encode_payload
from millrace.substrate.errors import StorageIntegrityError
from millrace.testing import (
    deterministic_context,
    fake_completed_runner_observation_state,
)
from millrace.testing.fakes import fake_runner_observation_payload
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
    source = generic_lifecycle.source()
    review_stage = next(
        stage
        for stage in cast(list[dict[str, object]], source["stage_kinds"])
        if stage["id"] == "review_stage"
    )
    review_stage["declared_outcome_ids"] = (
        "lifecycle.review.passed",
        "lifecycle.review.gap",
        "lifecycle.review.blocked",
    )
    cast(list[dict[str, object]], source["terminal_outcomes"]).extend(
        {
            "id": outcome_id,
            "stage_kind_id": "review_stage",
            "marker": marker,
        }
        for outcome_id, marker in (
            ("lifecycle.review.passed", "REVIEW_PASSED"),
            ("lifecycle.review.gap", "REVIEW_GAP"),
            ("lifecycle.review.blocked", "REVIEW_BLOCKED"),
        )
    )
    beta_schema = next(
        row
        for row in cast(list[dict[str, object]], source["artifact_schemas"])
        if row["id"] == generic_lifecycle.BETA_REPORT_SCHEMA_ID
    )
    beta_schema["schema"] = _closure_verdict_schema()
    cast(list[dict[str, object]], source["terminal_actions"]).extend(
        (
            {
                "id": "lifecycle.review.pass",
                "stage_kind_id": "review_stage",
                "outcome_id": "lifecycle.review.passed",
                "kind": "complete_work_item",
                "artifact_schema_id": generic_lifecycle.BETA_REPORT_SCHEMA_ID,
            },
            {
                "id": "lifecycle.review.gap",
                "stage_kind_id": "review_stage",
                "outcome_id": "lifecycle.review.gap",
                "kind": "closure_gap",
                "artifact_schema_id": generic_lifecycle.BETA_REPORT_SCHEMA_ID,
            },
            {
                "id": "lifecycle.review.block",
                "stage_kind_id": "review_stage",
                "outcome_id": "lifecycle.review.blocked",
                "kind": "block_work_item",
                "artifact_schema_id": generic_lifecycle.BETA_REPORT_SCHEMA_ID,
            },
        )
    )
    source["completion_behaviors"] = [
        {
            "id": COMPLETION_BEHAVIOR_ID,
            "trigger": "backlog_drained",
            "readiness_rule": "no_open_lineage_work",
            "request_kind": "closure_target",
            "target_selector": "active_closure_target",
            "target_stage_kind_id": "review_stage",
            "target_graph_node_id": "lifecycle.review.start",
            "runner_binding_id": "lifecycle.runner",
            "request_queue_family_id": "joined_bundle",
            "pass_action_id": "lifecycle.review.pass",
            "gap_action_id": "lifecycle.review.gap",
            "blocked_action_id": "lifecycle.review.block",
            "verdict_artifact_schema_id": generic_lifecycle.BETA_REPORT_SCHEMA_ID,
            "evidence_artifact_schema_ids": (generic_lifecycle.BETA_REPORT_SCHEMA_ID,),
            "evidence_item_limit": 64,
            "request_payload_byte_limit": 16_384,
            "remediation_policy_id": "lifecycle.remediation",
            "accepted_root_source_kinds": ("origin", "manual"),
            "root_source_resolution": "runtime_inventory",
            "evidence_window_policy": "lineage",
            "rubric_policy": "reuse_or_create",
            "blocked_work_policy": "suppress",
            "skip_if_closed": True,
        }
    ]
    source["remediation_policies"] = [
        {
            "id": "lifecycle.remediation",
            "source_action_id": "lifecycle.review.gap",
            "target_queue_family_id": "alpha_branch",
            "target_stage_kind_id": "alpha_stage",
            "target_graph_node_id": "lifecycle.alpha.start",
            "target_runner_binding_id": "lifecycle.runner",
            "payload_schema_id": generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
            "guidance_source": "source_artifact",
            "dedupe_key": "closure_target_and_source_artifact",
            "duplicate_policy": "refuse",
            "suppression_policy": "suppress_repeated_same_evidence",
            "root_source_kind": "origin",
        }
    ]
    runner = cast(list[dict[str, object]], source["runner_bindings"])[0]
    runner["component_pin"] = {
        "component_kind": "closure.runner",
        "component_id": "closure-evaluator",
        "component_version": "1",
        "provider_distribution": "millrace-test",
        "provider_version": "1",
        "descriptor_media_type": "application/json",
        "descriptor_sha256": "a" * 64,
        "required_capability_ids": (),
        "legal_terminal_result_ids": ("BLOCKED", "COMPLETE"),
        "max_work_item_payload_bytes": 16_384,
    }
    runner["terminal_result_mappings"] = ()
    return source


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


def _plan_ref(plan: SelectedCompiledPlan, fingerprint: str) -> PlanRef:
    return PlanRef(
        plan_id=f"{plan.workflow.workflow_id.value}:{plan.workflow.workflow_version.value}",
        authority_fingerprint=fingerprint,
        plan_format_version=plan.schema_version,
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


def _closure_target(
    *,
    target_id: str,
    plan_ref: PlanRef,
    behavior: CompletionBehaviorDeclaration,
    lineage_id: str,
    status: str = "open",
    closed_by_record_id: str | None = None,
) -> ClosureTargetRecord:
    return ClosureTargetRecord(
        closure_target_id=target_id,
        selected_plan_ref=plan_ref,
        completion_behavior_id=behavior.id,
        lineage_id=lineage_id,
        root_source_kind="origin",
        root_source_id=f"root-source-{target_id}",
        closure_root_work_item_id=f"root-origin-{target_id}",
        request_kind=behavior.request_kind,
        target_graph_node_id=behavior.target_graph_node_id,
        evidence_window={"kind": "lineage", "lineage_id": lineage_id},
        status=status,
        opened_by_input_id=f"open-{target_id}",
        closed_by_record_id=closed_by_record_id,
    )


def _root_inventory_work_item(
    target: ClosureTargetRecord,
) -> WorkItem:
    assert target.closure_root_work_item_id is not None
    return WorkItem(
        ref=WorkItemRef(
            work_item_id=target.closure_root_work_item_id,
            plan_ref=target.selected_plan_ref,
            generation=0,
        ),
        queue_family_id=QueueFamilyId(target.root_source_kind),
        payload={
            "title": f"Inventory for {target.closure_target_id}",
            "body": "Root source inventory record.",
            "root_source": {
                "kind": target.root_source_kind,
                "source_id": target.root_source_id,
            },
        },
        lineage_id=target.lineage_id,
        created_by_input_id=f"enqueue-{target.closure_root_work_item_id}",
    )


def _store_payload(cas_root: Path, payload: Mapping[str, AuthorityValue]) -> str:
    return ContentAddressedByteStore(cas_root).put_bytes(
        dumps_cas_object(encode_payload(payload))
    )


def _evaluator_source_records(
    *,
    plan: SelectedCompiledPlan,
    plan_ref: PlanRef,
    behavior: CompletionBehaviorDeclaration,
    closure_target_id: str,
    suffix: str,
    source_action_id: ActionId,
    artifact: tuple[str, Mapping[str, AuthorityValue]] | None,
) -> tuple[
    WorkItem,
    Activation,
    RunRecord,
    ClosureEvaluationRecord,
    RunnerObservationRecord,
    ArtifactRecord | None,
    TransitionRecord,
]:
    lineage_id = f"lineage-{suffix}"
    work_item_id = f"work-evaluator-{suffix}"
    activation_id = f"activation-evaluator-{suffix}"
    run_id = f"run-evaluator-{suffix}"
    input_id = f"observe-evaluator-{suffix}"
    work_item = WorkItem(
        ref=WorkItemRef(work_item_id=work_item_id, plan_ref=plan_ref, generation=0),
        queue_family_id=behavior.request_queue_family_id,
        payload={
            "request_kind": behavior.request_kind,
            "closure_target_id": closure_target_id,
            "graph_node_id": behavior.target_graph_node_id,
        },
        lineage_id=lineage_id,
        created_by_input_id=f"evaluate-{suffix}",
    )
    activation = Activation(
        activation_id=activation_id,
        work_item_id=work_item_id,
        lineage_id=lineage_id,
        plan_ref=plan_ref,
        queue_family_id=behavior.request_queue_family_id,
        graph_node_id=behavior.target_graph_node_id,
        stage_kind_id=behavior.target_stage_kind_id,
        runner_binding_id=behavior.runner_binding_id,
        generation=1,
        created_by_input_id=f"evaluate-{suffix}",
        claimed_by_run_id=run_id,
    )
    run = RunRecord(
        run_ref=RunRef(
            run_id=run_id,
            work_item_id=work_item_id,
            claim_id=f"claim-{suffix}",
            plan_ref=plan_ref,
            generation=0,
            fencing_token=f"fence-{suffix}",
        ),
        work_item_id=work_item_id,
        activation_id=activation_id,
        stage_kind_id=behavior.target_stage_kind_id,
        runner_binding_id=behavior.runner_binding_id,
        created_by_input_id=f"claim-{suffix}",
    )
    evaluator = ClosureEvaluationRecord(
        record_id=f"closure-evaluator:{activation_id}",
        closure_target_id=closure_target_id,
        completion_behavior_id=behavior.id,
        request_kind=behavior.request_kind,
        target_work_item_id=work_item_id,
        target_activation_id=activation_id,
        selected_plan_ref=plan_ref,
        lineage_id=lineage_id,
        created_by_input_id=f"evaluate-{suffix}",
    )
    source_action = next(
        action for action in actions_by_id if action.id == source_action_id
    )
    if artifact is not None and source_action.artifact_schema_id is None:
        raise AssertionError("closure source action must declare an artifact schema")
    artifact_body = artifact[1] if artifact is not None else {}
    marker = next(
        outcome.marker
        for outcome in plan.terminal_outcomes
        if outcome.id == source_action.outcome_id
    )
    observation_payload = fake_runner_observation_payload(
        run=run,
        activation=activation,
        plan_fingerprint=plan_ref.authority_fingerprint,
        marker=marker,
        artifact_payload=artifact_body,
    )
    transition_id = f"transition-{input_id}"
    observation = RunnerObservationRecord(
        observation_id=f"{transition_id}:observation",
        run_id=run_id,
        payload=observation_payload,
        created_by_input_id=input_id,
        observed_at=None,
    )
    artifact_record = (
        ArtifactRecord(
            artifact_id=artifact[0],
            work_item_id=work_item_id,
            schema_id=source_action.artifact_schema_id,
            payload=artifact_body,
            created_by_input_id=input_id,
            source_run_id=run_id,
            source_action_id=source_action_id,
            source_stage_kind_id=behavior.target_stage_kind_id,
            source_graph_node_id=behavior.target_graph_node_id,
            payload_digest=artifact_payload_digest(artifact_body),
            transition_id=transition_id,
        )
        if artifact is not None and source_action.artifact_schema_id is not None
        else None
    )
    transition = TransitionRecord(
        record_id=transition_id,
        input_id=input_id,
        input_kind=RunnerResultObserved.input_kind,
        input_family="workflow_observation",
        accepted=True,
    )
    return (
        work_item,
        activation,
        run,
        evaluator,
        observation,
        artifact_record,
        transition,
    )


actions_by_id: tuple[TerminalActionDeclaration, ...]


def _closure_state() -> RuntimeState:
    global actions_by_id
    plan, fingerprint = _compile_closure_plan()
    plan_ref = _plan_ref(plan, fingerprint)
    behavior, policy, actions = _selected_authority(plan)
    actions_by_id = tuple(actions.values())
    complete_terminal_id = "closure-terminal:transition-observe-evaluator-complete"
    complete_target = _closure_target(
        target_id="closure-target-complete",
        plan_ref=plan_ref,
        behavior=behavior,
        lineage_id="lineage-complete",
        status="closed",
        closed_by_record_id=complete_terminal_id,
    )
    incident_target = _closure_target(
        target_id="closure-target-incident",
        plan_ref=plan_ref,
        behavior=behavior,
        lineage_id="lineage-incident",
    )
    blocked_target = _closure_target(
        target_id="closure-target-blocked",
        plan_ref=plan_ref,
        behavior=behavior,
        lineage_id="lineage-blocked",
    )
    complete = _evaluator_source_records(
        plan=plan,
        plan_ref=plan_ref,
        behavior=behavior,
        closure_target_id=complete_target.closure_target_id,
        suffix="complete",
        source_action_id=behavior.pass_action_id,
        artifact=(
            "transition-observe-evaluator-complete:artifact",
            _artifact_payload(generic_lifecycle.BETA_REPORT_SCHEMA_ID),
        ),
    )
    incident = _evaluator_source_records(
        plan=plan,
        plan_ref=plan_ref,
        behavior=behavior,
        closure_target_id=incident_target.closure_target_id,
        suffix="incident",
        source_action_id=behavior.gap_action_id,
        artifact=(
            "transition-observe-evaluator-incident:artifact",
            _artifact_payload(generic_lifecycle.BETA_REPORT_SCHEMA_ID),
        ),
    )
    blocked = _evaluator_source_records(
        plan=plan,
        plan_ref=plan_ref,
        behavior=behavior,
        closure_target_id=blocked_target.closure_target_id,
        suffix="blocked",
        source_action_id=behavior.blocked_action_id,
        artifact=None,
    )
    incident_record_id = "remediation-record:transition-observe-evaluator-incident"
    remediation_work = WorkItem(
        ref=WorkItemRef(
            work_item_id="work-remediation-target",
            plan_ref=plan_ref,
            generation=0,
        ),
        queue_family_id=policy.target_queue_family_id,
        payload={
            "root_source": {
                "kind": policy.root_source_kind,
                "source_id": incident_record_id,
            }
        },
        lineage_id=incident_target.lineage_id,
        created_by_input_id="observe-evaluator-incident",
    )
    remediation_activation = Activation(
        activation_id="activation-remediation-target",
        work_item_id=remediation_work.ref.work_item_id,
        lineage_id=incident_target.lineage_id,
        plan_ref=plan_ref,
        queue_family_id=policy.target_queue_family_id,
        graph_node_id=policy.target_graph_node_id,
        stage_kind_id=policy.target_stage_kind_id,
        runner_binding_id=policy.target_runner_binding_id,
        generation=0,
        created_by_input_id="observe-evaluator-incident",
    )
    terminal = ClosureTerminalRecord(
        record_id=complete_terminal_id,
        closure_target_id=complete_target.closure_target_id,
        completion_behavior_id=behavior.id,
        terminal_kind="passed",
        source_run_id=complete[2].run_ref.run_id,
        source_action_id=behavior.pass_action_id,
        source_artifact_id="transition-observe-evaluator-complete:artifact",
        selected_plan_ref=plan_ref,
        lineage_id=complete_target.lineage_id,
        created_by_input_id="observe-evaluator-complete",
    )
    remediation = RemediationWorkRecord(
        record_id=incident_record_id,
        remediation_policy_id=policy.id,
        closure_target_id=incident_target.closure_target_id,
        source_run_id=incident[2].run_ref.run_id,
        source_action_id=behavior.gap_action_id,
        source_artifact_id="transition-observe-evaluator-incident:artifact",
        target_work_item_id=remediation_work.ref.work_item_id,
        target_activation_id=remediation_activation.activation_id,
        selected_plan_ref=plan_ref,
        lineage_id=incident_target.lineage_id,
        dedupe_key=f"{incident_target.closure_target_id}:transition-observe-evaluator-incident:artifact",
        created_by_input_id="observe-evaluator-incident",
    )
    blocked_record = ClosureBlockedRecord(
        record_id="closure-blocked:transition-observe-evaluator-blocked",
        closure_target_id=blocked_target.closure_target_id,
        completion_behavior_id=behavior.id,
        source_run_id=blocked[2].run_ref.run_id,
        source_action_id=behavior.blocked_action_id,
        selected_plan_ref=plan_ref,
        lineage_id=blocked_target.lineage_id,
        operator_required=True,
        created_by_input_id="observe-evaluator-blocked",
    )
    root_work_items = tuple(
        _root_inventory_work_item(target)
        for target in (complete_target, incident_target, blocked_target)
    )
    source_work_items = (
        *root_work_items,
        complete[0],
        incident[0],
        blocked[0],
        remediation_work,
    )
    source_activations = (
        complete[1],
        incident[1],
        blocked[1],
        remediation_activation,
    )
    source_runs = (complete[2], incident[2], blocked[2])
    observations = (complete[4], incident[4], blocked[4])
    artifacts = tuple(
        item for item in (complete[5], incident[5], blocked[5]) if item is not None
    )
    transitions = (complete[6], incident[6], blocked[6])
    observation_sources = (
        (complete, behavior.pass_action_id),
        (incident, behavior.gap_action_id),
        (blocked, behavior.blocked_action_id),
    )
    receipts: dict[str, InputReceipt] = {}
    governance_events: list[GovernanceEventRecord] = []
    traces: list[TraceRecord] = []
    for source, action_id in observation_sources:
        run = source[2]
        observation = source[4]
        transition = source[6]
        accepted_input = RunnerResultObserved(
            observation.created_by_input_id,
            run_id=observation.run_id,
            payload=observation.payload,
            observed_at=observation.observed_at,
        )
        receipts[observation.created_by_input_id] = InputReceipt(
            receipt_ref=InputReceiptRef(
                input_id=observation.created_by_input_id,
                input_payload_digest=input_payload_digest(accepted_input),
            ),
            transition_id=transition.record_id,
        )
        audit_fields = {
            "record_id": f"{transition.record_id}:governance",
            "input_id": observation.created_by_input_id,
            "input_kind": RunnerResultObserved.input_kind,
            "input_family": "workflow_observation",
            "disposition": "accepted",
            "plan_fingerprint": plan_ref.authority_fingerprint,
            "work_item_id": run.work_item_id,
            "run_id": run.run_ref.run_id,
            "action_id": action_id,
            "authority_source": "terminal_action",
        }
        governance_events.append(GovernanceEventRecord(**audit_fields))
        traces.append(
            TraceRecord(
                **{
                    **audit_fields,
                    "record_id": f"{transition.record_id}:trace",
                }
            )
        )
    return RuntimeState(
        admitted_plans={
            fingerprint: AdmittedPlan(plan_ref=plan_ref, selected_plan=plan)
        },
        default_plan_ref=plan_ref,
        receipts=receipts,
        work_items={item.ref.work_item_id: item for item in source_work_items},
        activations={item.activation_id: item for item in source_activations},
        runs={item.run_ref.run_id: item for item in source_runs},
        runner_observations={item.observation_id: item for item in observations},
        artifacts={item.artifact_id: item for item in artifacts},
        closure_targets={
            complete_target.closure_target_id: complete_target,
            incident_target.closure_target_id: incident_target,
            blocked_target.closure_target_id: blocked_target,
        },
        closure_evaluations={
            item.record_id: item for item in (complete[3], incident[3], blocked[3])
        },
        closure_terminal_records={terminal.record_id: terminal},
        remediation_work_records={remediation.record_id: remediation},
        closure_blocked_records={blocked_record.record_id: blocked_record},
        governance_events=tuple(governance_events),
        traces=tuple(traces),
        transitions=transitions,
    )


def _renewed_closure_state() -> tuple[
    RuntimeState,
    SelectedCompiledPlan,
    str,
    str,
    str,
]:
    state = _closure_state()
    assert state.default_plan_ref is not None
    plan_ref = state.default_plan_ref
    plan = state.admitted_plans[plan_ref.authority_fingerprint].selected_plan
    behavior, _policy, _actions = _selected_authority(plan)
    target = state.closure_targets["closure-target-incident"]
    source = _evaluator_source_records(
        plan=plan,
        plan_ref=plan_ref,
        behavior=behavior,
        closure_target_id=target.closure_target_id,
        suffix="hostile",
        source_action_id=behavior.pass_action_id,
        artifact=None,
    )
    work_item, activation, run, evaluation, _observation, _artifact, _transition = (
        source
    )
    work_item = replace(work_item, lineage_id=target.lineage_id)
    activation = replace(activation, lineage_id=target.lineage_id)
    evaluation = replace(evaluation, lineage_id=target.lineage_id)
    state = replace(
        state,
        work_items={
            **state.work_items,
            work_item.ref.work_item_id: work_item,
        },
        activations={activation.activation_id: activation, **state.activations},
        runs={run.run_ref.run_id: run, **state.runs},
        closure_evaluations={
            **state.closure_evaluations,
            evaluation.record_id: evaluation,
        },
    )
    stage = next(
        stage
        for stage in plan.stage_kinds
        if stage.id == behavior.target_stage_kind_id
    )
    request_payload, refusal = _completion_request_payload(
        state=state,
        target=target,
        behavior=behavior,
        stage=stage,
    )
    assert refusal is None
    assert request_payload is not None
    work_item = replace(work_item, payload=request_payload)
    state = replace(
        state,
        work_items={
            **state.work_items,
            work_item.ref.work_item_id: work_item,
        },
    )
    return (
        state,
        plan,
        target.closure_target_id,
        work_item.ref.work_item_id,
        run.run_ref.run_id,
    )


def _hostile_closure_verdict(
    *,
    snapshot: Mapping[str, object],
    case: str,
) -> Mapping[str, AuthorityValue]:
    payload = dict(_artifact_payload(generic_lifecycle.BETA_REPORT_SCHEMA_ID))
    root_contract = cast(Mapping[str, object], snapshot["root_contract"])
    payload["closure_target_id"] = snapshot["closure_target_id"]
    payload["root_contract_digest"] = root_contract["payload_digest"]
    payload["freshness_anchor_digest"] = snapshot["freshness_anchor_digest"]
    if case == "wrong_root_contract_digest":
        payload["root_contract_digest"] = "sha256:" + "e" * 64
    elif case == "changed_rubric":
        payload["rubric"] = {
            "criteria": (
                {
                    "criterion_id": "criterion-1",
                    "requirement": "A changed closure requirement.",
                    "evidence_rule": "Use changed evidence.",
                },
            )
        }
    elif case == "stale_freshness_anchor":
        payload["freshness_anchor_digest"] = "sha256:" + "f" * 64
    elif case == "criterion_set_mismatch":
        result = dict(
            cast(
                tuple[Mapping[str, object], ...],
                payload["criterion_results"],
            )[0]
        )
        result["criterion_id"] = "criterion-unknown"
        payload["criterion_results"] = (result,)
    elif case == "pass_marker_status_contradiction":
        result = dict(
            cast(
                tuple[Mapping[str, object], ...],
                payload["criterion_results"],
            )[0]
        )
        result["status"] = "failed"
        payload["criterion_results"] = (result,)
    return cast(Mapping[str, AuthorityValue], payload)


@pytest.mark.parametrize(
    ("case", "marker", "expected_reason", "restart"),
    (
        (
            "wrong_root_contract_digest",
            "REVIEW_PASSED",
            "closure_root_contract_digest_mismatch",
            False,
        ),
        ("changed_rubric", "REVIEW_PASSED", "closure_rubric_mismatch", False),
        (
            "stale_freshness_anchor",
            "REVIEW_PASSED",
            "closure_freshness_anchor_mismatch",
            False,
        ),
        (
            "criterion_set_mismatch",
            "REVIEW_PASSED",
            "closure_criterion_set_mismatch",
            False,
        ),
        (
            "pass_marker_status_contradiction",
            "REVIEW_PASSED",
            "closure_marker_status_mismatch",
            False,
        ),
        (
            "gap_marker_status_contradiction",
            "REVIEW_GAP",
            "closure_marker_status_mismatch",
            False,
        ),
        (
            "blocked_marker_status_contradiction",
            "REVIEW_BLOCKED",
            "closure_marker_status_mismatch",
            True,
        ),
    ),
)
def test_runner_result_observed_refuses_hostile_closure_before_aftermath(
    tmp_path: Path,
    case: str,
    marker: str,
    expected_reason: str,
    restart: bool,
) -> None:
    state, _plan, target_id, work_item_id, run_id = _renewed_closure_state()
    persisted_snapshot = state.work_items[work_item_id].payload[
        "closure_evidence_snapshot"
    ]
    if restart:
        db_path, cas_root = runtime_store_paths(tmp_path)
        persist_runtime_state(db_path, cas_root, state)
        state = load_runtime_state(db_path, cas_root)
        assert (
            state.work_items[work_item_id].payload["closure_evidence_snapshot"]
            == persisted_snapshot
        )

    assert state.default_plan_ref is not None
    target = state.closure_targets[target_id]
    work_item = state.work_items[work_item_id]
    run = state.runs[run_id]
    activation = state.activations[run.activation_id]
    snapshot = cast(
        Mapping[str, object],
        work_item.payload["closure_evidence_snapshot"],
    )
    observation = RunnerResultObserved(
        f"observe-hostile-{case}",
        run_id=run.run_ref.run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=activation,
            plan_fingerprint=target.selected_plan_ref.authority_fingerprint,
            marker=marker,
            artifact_payload=_hostile_closure_verdict(
                snapshot=snapshot,
                case=case,
            ),
        ),
        observed_at=None,
    )
    seeded, authorized = fake_completed_runner_observation_state(
        state=state,
        observation=observation,
    )
    context = deterministic_context(
        transition_id=f"transition-{observation.input_id}",
        work_item_id=work_item.ref.work_item_id,
        activation_id=activation.activation_id,
        run_id=run.run_ref.run_id,
        claim_id=run.run_ref.claim_id,
        fencing_token=run.run_ref.fencing_token,
    )
    decision = decide(seeded, authorized, context)

    assert decision.accepted is False
    assert decision.disposition == "refused"
    assert decision.refusal is not None
    assert decision.refusal.reason == expected_reason

    before_terminal_ids = set(seeded.closure_terminal_records)
    before_remediation_ids = set(seeded.remediation_work_records)
    before_blocked_ids = set(seeded.closure_blocked_records)
    before_work_item_ids = set(seeded.work_items)
    after = apply(seeded, decision)
    transition = next(
        item for item in after.transitions if item.record_id == context.transition_id
    )
    receipt = after.receipts[authorized.input_id]
    assert transition.accepted is False
    assert receipt.accepted is False
    assert receipt.refusal_reason == expected_reason
    assert set(after.closure_terminal_records) == before_terminal_ids
    assert set(after.remediation_work_records) == before_remediation_ids
    assert set(after.closure_blocked_records) == before_blocked_ids
    assert set(after.work_items) == before_work_item_ids
    assert not any(
        item.created_by_input_id == authorized.input_id
        for item in after.work_items.values()
    )


def test_closure_records_survive_restart(tmp_path: Path) -> None:
    state = _closure_state()
    db_path, cas_root = runtime_store_paths(tmp_path)

    persist_runtime_state(db_path, cas_root, state)
    loaded = load_runtime_state(db_path, cas_root)

    assert loaded.closure_targets == state.closure_targets
    assert loaded.closure_evaluations == state.closure_evaluations
    assert loaded.closure_terminal_records == state.closure_terminal_records
    assert loaded.remediation_work_records == state.remediation_work_records
    assert loaded.closure_blocked_records == state.closure_blocked_records


def test_runtime_created_closure_snapshot_survives_restart_and_reauthenticates(
    tmp_path: Path,
) -> None:
    state = _closure_state()
    plan, _fingerprint = _compile_closure_plan()
    behavior, _policy, _actions = _selected_authority(plan)
    target = state.closure_targets["closure-target-incident"]
    stage = next(
        stage
        for stage in plan.stage_kinds
        if stage.id == behavior.target_stage_kind_id
    )
    request_payload, refusal = _completion_request_payload(
        state=state,
        target=target,
        behavior=behavior,
        stage=stage,
    )
    assert refusal is None
    assert request_payload is not None
    snapshot = request_payload["closure_evidence_snapshot"]
    assert isinstance(snapshot, Mapping)

    evaluator = state.work_items["work-evaluator-incident"]
    work_items = dict(state.work_items)
    work_items[evaluator.ref.work_item_id] = replace(
        evaluator,
        payload=request_payload,
    )
    state_with_snapshot = replace(state, work_items=work_items)
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state_with_snapshot)
    loaded = load_runtime_state(db_path, cas_root)

    loaded_payload = loaded.work_items[evaluator.ref.work_item_id].payload
    assert loaded_payload["closure_evidence_snapshot"] == snapshot

    corrupted_snapshot = dict(snapshot)
    corrupted_snapshot["selected_plan_fingerprint"] = "sha256:" + "b" * 64
    corrupted_payload = dict(loaded_payload)
    corrupted_payload["closure_evidence_snapshot"] = corrupted_snapshot
    corrupted_work_items = dict(loaded.work_items)
    corrupted_work_items[evaluator.ref.work_item_id] = replace(
        corrupted_work_items[evaluator.ref.work_item_id],
        payload=corrupted_payload,
    )
    refusal = _closure_snapshot_authority_refusal(
        state=replace(loaded, work_items=corrupted_work_items),
        target=loaded.closure_targets[target.closure_target_id],
        behavior=behavior,
        snapshot=corrupted_snapshot,
        selected_plan=plan,
    )
    assert refusal == "closure_snapshot_authority_mismatch"


@pytest.mark.parametrize(
    "coexisting_records",
    (
        "closure_evaluations",
        "closure_terminal_records",
        "remediation_work_records",
        "closure_blocked_records",
        "runner_observations",
        "artifacts",
        "receipts",
        "governance_events",
        "traces",
        "transitions",
    ),
)
def test_restart_refuses_closure_root_drift_with_coexisting_generic_records(
    tmp_path: Path,
    coexisting_records: str,
) -> None:
    state = _closure_state()
    assert getattr(state, coexisting_records)
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE closure_targets
            SET closure_root_work_item_id = ?
            WHERE closure_target_id = ?
            """,
            ("wrong-root-work-item", "closure-target-complete"),
        )

    with pytest.raises(StorageIntegrityError, match="closure_root_work_item_id"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_corrupt_closure_target_authority_link(
    tmp_path: Path,
) -> None:
    state = _closure_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE closure_targets
            SET target_graph_node_id = ?
            WHERE closure_target_id = ?
            """,
            ("wrong-node", "closure-target-complete"),
        )

    with pytest.raises(StorageIntegrityError, match="target_graph_node_id"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_missing_closure_root_inventory(tmp_path: Path) -> None:
    state = _closure_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE closure_targets
            SET root_source_id = ?
            WHERE closure_target_id = ?
            """,
            ("missing-root-source", "closure-target-complete"),
        )

    with pytest.raises(StorageIntegrityError, match="root source"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_closure_root_lineage_drift(tmp_path: Path) -> None:
    state = _closure_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE work_items
            SET lineage_id = ?
            WHERE work_item_id = ?
            """,
            ("different-lineage", "root-origin-closure-target-complete"),
        )

    with pytest.raises(StorageIntegrityError, match="lineage"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_closure_root_work_item_id_drift(tmp_path: Path) -> None:
    state = _closure_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE closure_targets
            SET closure_root_work_item_id = ?
            WHERE closure_target_id = ?
            """,
            ("wrong-root-work-item", "closure-target-complete"),
        )

    with pytest.raises(StorageIntegrityError, match="closure_root_work_item_id"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_non_manual_missing_closure_root_work_item_id(
    tmp_path: Path,
) -> None:
    state = _closure_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE closure_targets
            SET closure_root_work_item_id = NULL
            WHERE closure_target_id = ?
            """,
            ("closure-target-complete",),
        )

    with pytest.raises(StorageIntegrityError, match="closure_root_work_item_id"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_closure_root_queue_family_drift(tmp_path: Path) -> None:
    state = _closure_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE work_items
            SET queue_family_id = ?
            WHERE work_item_id = ?
            """,
            ("joined_bundle", "root-origin-closure-target-complete"),
        )
        connection.execute(
            """
            UPDATE activations
            SET queue_family_id = ?
            WHERE work_item_id = ?
            """,
            ("joined_bundle", "root-origin-closure-target-complete"),
        )

    with pytest.raises(StorageIntegrityError, match="root source"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_closure_root_plan_ref_drift(tmp_path: Path) -> None:
    state = _closure_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE work_items
            SET plan_authority_fingerprint = ?
            WHERE work_item_id = ?
            """,
            ("sha256:drifted-root-plan", "root-origin-closure-target-complete"),
        )

    with pytest.raises(StorageIntegrityError, match="work_items"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_closure_root_payload_source_kind_drift(
    tmp_path: Path,
) -> None:
    state = _closure_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    drifted_payload_digest = _store_payload(
        cas_root,
        {
            "title": "Drifted inventory",
            "body": "Root source inventory with wrong kind.",
            "root_source": {
                "kind": "manual",
                "source_id": "root-source-closure-target-complete",
            },
        },
    )

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE work_items
            SET payload_digest = ?
            WHERE work_item_id = ?
            """,
            (drifted_payload_digest, "root-origin-closure-target-complete"),
        )

    with pytest.raises(StorageIntegrityError, match="root source"):
        load_runtime_state(db_path, cas_root)


def test_persist_refuses_manual_missing_closure_root_work_item_id(
    tmp_path: Path,
) -> None:
    state = _closure_state()
    base_target = next(iter(state.closure_targets.values()))
    manual_target = replace(
        base_target,
        closure_target_id="closure-target-manual",
        lineage_id="manual-lineage-1",
        root_source_kind="manual",
        root_source_id="manual-source-1",
        closure_root_work_item_id=None,
        evidence_window={"kind": "lineage", "lineage_id": "manual-lineage-1"},
        status="open",
        opened_by_input_id="open-manual-root",
        closed_by_record_id=None,
    )
    legal = replace(
        state,
        closure_targets={
            **state.closure_targets,
            manual_target.closure_target_id: manual_target,
        },
    )
    db_path, cas_root = runtime_store_paths(tmp_path)

    with pytest.raises(StorageIntegrityError, match="closure_root_work_item_id"):
        persist_runtime_state(db_path, cas_root, legal)


def test_restart_refuses_closure_terminal_without_matching_evaluator(
    tmp_path: Path,
) -> None:
    state = _closure_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE closure_terminal_records
            SET source_run_id = ?, source_artifact_id = NULL
            WHERE record_id = ?
            """,
            (
                "run-evaluator-incident",
                "closure-terminal:transition-observe-evaluator-complete",
            ),
        )

    with pytest.raises(StorageIntegrityError, match="closure evaluator activation"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_remediation_missing_required_source_artifact(
    tmp_path: Path,
) -> None:
    state = _closure_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE remediation_work_records
            SET source_artifact_id = NULL
            WHERE record_id = ?
            """,
            ("remediation-record:transition-observe-evaluator-incident",),
        )

    with pytest.raises(StorageIntegrityError, match="source_artifact_id"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_remediation_wrong_dedupe_key(tmp_path: Path) -> None:
    state = _closure_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE remediation_work_records
            SET dedupe_key = ?
            WHERE record_id = ?
            """,
            (
                "wrong-dedupe-key",
                "remediation-record:transition-observe-evaluator-incident",
            ),
        )

    with pytest.raises(StorageIntegrityError, match="dedupe_key"):
        load_runtime_state(db_path, cas_root)


def test_persist_refuses_duplicate_remediation_dedupe_key(tmp_path: Path) -> None:
    state = _closure_state()
    remediation = next(iter(state.remediation_work_records.values()))
    duplicate = replace(
        remediation,
        record_id="remediation-record:duplicate",
        created_by_input_id="observe-evaluator-incident-duplicate",
    )
    duplicated = replace(
        state,
        remediation_work_records={
            remediation.record_id: remediation,
            duplicate.record_id: duplicate,
        },
    )
    db_path, cas_root = runtime_store_paths(tmp_path)

    with pytest.raises(StorageIntegrityError, match="dedupe_key"):
        persist_runtime_state(db_path, cas_root, duplicated)
