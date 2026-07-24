"""Hosted vendor_selection workflow fixture for four-plane compile validation."""

from __future__ import annotations

from copy import deepcopy
from typing import cast

from millrace.contracts.operator_waits import _operator_wait_audit_metadata_requirements

Source = dict[str, object]
Record = dict[str, object]

RUNNER_ID = "deterministic_vendor_selection_runner"
JOIN_ID = "candidate_evidence_join"
OPERATOR_WAIT_ID = "vendor_selection.award_operator_wait"
OPERATOR_WAIT_ACTION_ID = "vendor_selection.award_decider.operator_required"

PARTITION_IDS = ("requirements", "sourcing", "evaluation", "authorization")
QUEUE_FAMILY_IDS = (
    "purchase_request",
    "requirement_packet",
    "candidate_bundle",
    "evaluation_report",
    "authorization_decision",
    "operator_decision",
    "decision_pack",
)
STAGE_KIND_IDS = (
    "request_intake",
    "policy_screener",
    "requirement_freezer",
    "catalog_sourcer",
    "candidate_packager",
    "rubric_evaluator",
    "conflict_checker",
    "award_decider",
    "decision_packager",
)
ARTIFACT_SCHEMA_IDS = (
    "PurchaseRequest",
    "RequirementPacket",
    "CandidateBundle",
    "RubricReport",
    "ConflictReport",
    "AwardDecision",
    "OperatorDecision",
    "PolicyDecision",
    "DecisionPack",
)


def source() -> Source:
    return deepcopy(WORKFLOW_SOURCE)


def records(workflow_source: Source, key: str) -> list[Record]:
    return cast(list[Record], workflow_source[key])


def required_string_schema() -> dict[str, object]:
    return {"type": "string", "min_length": 1}


def nullable_string_enum_schema(*allowed_values: str) -> dict[str, object]:
    return {"enum": (*allowed_values, None)}


def required_string_array_schema(*, min_items: int = 1) -> dict[str, object]:
    return {
        "type": "array",
        "min_items": min_items,
        "items": required_string_schema(),
    }


def candidate_array_schema() -> dict[str, object]:
    return {
        "type": "array",
        "min_items": 1,
        "unique_by": "candidate_id",
        "items": {
            "type": "object",
            "required": (
                "candidate_id",
                "vendor_label",
                "capabilities",
                "budget_band",
                "catalog_ref",
                "conflict_status",
            ),
            "properties": {
                "candidate_id": required_string_schema(),
                "vendor_label": required_string_schema(),
                "capabilities": required_string_array_schema(),
                "budget_band": required_string_schema(),
                "catalog_ref": required_string_schema(),
                "conflict_status": {"enum": ("clear", "blocked")},
            },
        },
    }


def _object_schema(
    *,
    required: tuple[str, ...],
    properties: dict[str, object],
) -> dict[str, object]:
    return {"type": "object", "required": required, "properties": properties}


def _source_projection(*path: str) -> dict[str, object]:
    return {"kind": "source", "path": path}


def _route_action(
    *,
    action_id: str,
    stage_kind_id: str,
    outcome_id: str,
    target_stage_kind_id: str,
    target_graph_node_id: str,
    emitted_queue_family_id: str,
    artifact_schema_id: str,
) -> dict[str, object]:
    return {
        "id": action_id,
        "stage_kind_id": stage_kind_id,
        "outcome_id": outcome_id,
        "kind": "route",
        "target_stage_kind_id": target_stage_kind_id,
        "target_graph_node_id": target_graph_node_id,
        "emitted_queue_family_id": emitted_queue_family_id,
        "artifact_schema_id": artifact_schema_id,
        "runner_binding_id": RUNNER_ID,
        "payload_projection": _source_projection("artifact_payload"),
        "presentation": {"display_name": action_id},
    }


def _artifact_action(
    *,
    action_id: str,
    stage_kind_id: str,
    outcome_id: str,
    kind: str,
    artifact_schema_id: str | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "id": action_id,
        "stage_kind_id": stage_kind_id,
        "outcome_id": outcome_id,
        "kind": kind,
        "presentation": {"display_name": action_id},
    }
    if artifact_schema_id is not None:
        record["artifact_schema_id"] = artifact_schema_id
    return record


def _stage(
    *,
    stage_id: str,
    partition_id: str,
    inputs: tuple[str, ...],
    outputs: tuple[str, ...],
    schemas: tuple[str, ...],
    outcomes: tuple[str, ...],
) -> dict[str, object]:
    return {
        "id": stage_id,
        "partition_id": partition_id,
        "runner_binding_id": RUNNER_ID,
        "input_queue_family_ids": inputs,
        "output_queue_family_ids": outputs,
        "artifact_schema_ids": schemas,
        "asset_ids": (),
        "declared_outcome_ids": outcomes,
        "presentation": {"display_name": stage_id},
    }


def _outcome(stage_kind_id: str, suffix: str, marker: str) -> dict[str, object]:
    return {
        "id": f"vendor_selection.{stage_kind_id}.{suffix}",
        "stage_kind_id": stage_kind_id,
        "marker": marker,
        "presentation": {"display_name": marker},
    }


WORKFLOW_SOURCE: Source = {
    "lineage_policy": "root_from_external_enqueue",
    "workflow": {
        "id": "vendor_selection",
        "version": "0.1",
        "name": "Vendor Selection",
        "compatibility_profile": None,
        "required_extensions": (),
    },
    "graphs": [
        {
            "id": "vendor_selection.graph",
            "node_ids": (
                "vendor_selection.request_intake.start",
                "vendor_selection.policy_screener.start",
                "vendor_selection.requirement_freezer.start",
                "vendor_selection.catalog_sourcer.start",
                "vendor_selection.candidate_packager.start",
                "vendor_selection.rubric_evaluator.start",
                "vendor_selection.conflict_checker.start",
                "vendor_selection.award_decider.start",
                "vendor_selection.decision_packager.start",
            ),
            "presentation": {"display_name": "Vendor Selection Graph"},
        }
    ],
    "partitions": [
        {
            "id": partition_id,
            "kind": "plane",
            "presentation": {"display_name": partition_id.replace("_", " ").title()},
        }
        for partition_id in PARTITION_IDS
    ],
    "queue_families": [
        {
            "id": queue_family_id,
            "external_enqueue": queue_family_id == "purchase_request",
            "presentation": {
                "display_name": queue_family_id.replace("_", " ").title()
            },
        }
        for queue_family_id in QUEUE_FAMILY_IDS
    ],
    "external_enqueue_routes": [
        {
            "id": "vendor_selection.purchase_request",
            "queue_family_id": "purchase_request",
            "graph_node_id": "vendor_selection.request_intake.start",
            "stage_kind_id": "request_intake",
            "runner_binding_id": RUNNER_ID,
            "payload_schema_id": "PurchaseRequest",
        }
    ],
    "generated_work_routes": [
        {
            "id": "vendor_selection.requirement_packet",
            "queue_family_id": "requirement_packet",
            "graph_node_id": "vendor_selection.catalog_sourcer.start",
            "stage_kind_id": "catalog_sourcer",
            "runner_binding_id": RUNNER_ID,
            "payload_schema_id": "RequirementPacket",
        },
        {
            "id": "vendor_selection.candidate_bundle_packaging",
            "queue_family_id": "candidate_bundle",
            "graph_node_id": "vendor_selection.candidate_packager.start",
            "stage_kind_id": "candidate_packager",
            "runner_binding_id": RUNNER_ID,
            "payload_schema_id": "CandidateBundle",
        },
        {
            "id": "vendor_selection.rubric_work",
            "queue_family_id": "candidate_bundle",
            "graph_node_id": "vendor_selection.rubric_evaluator.start",
            "stage_kind_id": "rubric_evaluator",
            "runner_binding_id": RUNNER_ID,
            "payload_schema_id": "CandidateBundle",
        },
        {
            "id": "vendor_selection.conflict_work",
            "queue_family_id": "candidate_bundle",
            "graph_node_id": "vendor_selection.conflict_checker.start",
            "stage_kind_id": "conflict_checker",
            "runner_binding_id": RUNNER_ID,
            "payload_schema_id": "CandidateBundle",
        },
        {
            "id": "vendor_selection.award_join_work",
            "queue_family_id": "candidate_bundle",
            "graph_node_id": "vendor_selection.award_decider.start",
            "stage_kind_id": "award_decider",
            "runner_binding_id": RUNNER_ID,
            "payload_schema_id": "CandidateBundle",
        },
        {
            "id": "vendor_selection.authorization_decision",
            "queue_family_id": "authorization_decision",
            "graph_node_id": "vendor_selection.decision_packager.start",
            "stage_kind_id": "decision_packager",
            "runner_binding_id": RUNNER_ID,
            "payload_schema_id": "AwardDecision",
        },
        {
            "id": "vendor_selection.operator_decision_payload",
            "queue_family_id": "decision_pack",
            "graph_node_id": "vendor_selection.decision_packager.start",
            "stage_kind_id": "decision_packager",
            "runner_binding_id": RUNNER_ID,
            "payload_schema_id": "OperatorDecision",
        },
        {
            "id": "vendor_selection.decision_pack",
            "queue_family_id": "decision_pack",
            "graph_node_id": "vendor_selection.decision_packager.start",
            "stage_kind_id": "decision_packager",
            "runner_binding_id": RUNNER_ID,
            "payload_schema_id": "DecisionPack",
        },
    ],
    "artifact_schemas": [
        {
            "id": "PurchaseRequest",
            "schema": _object_schema(
                required=(
                    "request_id",
                    "requester_label",
                    "category",
                    "budget_band",
                    "required_capabilities",
                    "disallowed_vendors",
                    "approval_policy_hint",
                ),
                properties={
                    "request_id": required_string_schema(),
                    "requester_label": required_string_schema(),
                    "category": required_string_schema(),
                    "budget_band": required_string_schema(),
                    "required_capabilities": required_string_array_schema(),
                    "disallowed_vendors": required_string_array_schema(min_items=0),
                    "approval_policy_hint": {"enum": ("none", "operator_required")},
                },
            ),
            "presentation": {"display_name": "PurchaseRequest"},
        },
        {
            "id": "RequirementPacket",
            "schema": _object_schema(
                required=(
                    "source_request_id",
                    "approval_policy_hint",
                    "frozen_requirements",
                    "policy_status",
                    "selection_rubric_id",
                    "conflict_rules",
                    "candidate_count_min",
                    "candidate_count_max",
                ),
                properties={
                    "source_request_id": required_string_schema(),
                    "approval_policy_hint": {"enum": ("none", "operator_required")},
                    "frozen_requirements": required_string_array_schema(),
                    "policy_status": {
                        "enum": ("allowed", "blocked", "clarification_required")
                    },
                    "selection_rubric_id": required_string_schema(),
                    "conflict_rules": required_string_array_schema(),
                    "candidate_count_min": {"type": "integer"},
                    "candidate_count_max": {"type": "integer"},
                },
            ),
            "presentation": {"display_name": "RequirementPacket"},
        },
        {
            "id": "CandidateBundle",
            "schema": _object_schema(
                required=(
                    "source_requirement_id",
                    "bundle_id",
                    "candidate_vendors",
                    "deterministic_source_refs",
                    "approval_policy_hint",
                    "conflict_rules",
                ),
                properties={
                    "source_requirement_id": required_string_schema(),
                    "bundle_id": required_string_schema(),
                    "candidate_vendors": candidate_array_schema(),
                    "deterministic_source_refs": required_string_array_schema(),
                    "approval_policy_hint": {"enum": ("none", "operator_required")},
                    "conflict_rules": required_string_array_schema(),
                },
            ),
            "presentation": {"display_name": "CandidateBundle"},
        },
        {
            "id": "RubricReport",
            "schema": _object_schema(
                required=(
                    "bundle_id",
                    "evaluator_kind",
                    "score_table",
                    "threshold_result",
                    "recommended_candidate_id",
                ),
                properties={
                    "bundle_id": required_string_schema(),
                    "evaluator_kind": {"const": "rubric"},
                    "score_table": {
                        "type": "array",
                        "min_items": 1,
                        "unique_by": "candidate_id",
                        "items": {
                            "type": "object",
                            "required": ("candidate_id", "score"),
                            "properties": {
                                "candidate_id": required_string_schema(),
                                "score": {"type": "integer"},
                            },
                        },
                    },
                    "threshold_result": {"enum": ("pass", "fail")},
                    "recommended_candidate_id": required_string_schema(),
                },
            ),
            "presentation": {"display_name": "RubricReport"},
        },
        {
            "id": "ConflictReport",
            "schema": _object_schema(
                required=(
                    "bundle_id",
                    "evaluator_kind",
                    "conflict_findings",
                    "clearance_result",
                ),
                properties={
                    "bundle_id": required_string_schema(),
                    "evaluator_kind": {"const": "conflict"},
                    "conflict_findings": required_string_array_schema(min_items=0),
                    "clearance_result": {"enum": ("clear", "blocked")},
                },
            ),
            "presentation": {"display_name": "ConflictReport"},
        },
        {
            "id": "AwardDecision",
            "schema": _object_schema(
                required=(
                    "bundle_id",
                    "decision_kind",
                    "selected_candidate_id",
                    "required_evidence_refs",
                    "operator_gate_required",
                    "reason",
                ),
                properties={
                    "bundle_id": required_string_schema(),
                    "decision_kind": {
                        "enum": (
                            "award",
                            "re_source",
                            "reject",
                            "operator_required",
                            "blocked",
                        )
                    },
                    "selected_candidate_id": nullable_string_enum_schema(
                        "vendor_alpha",
                        "vendor_beta",
                        "vendor_gamma",
                    ),
                    "required_evidence_refs": _object_schema(
                        required=("rubric_report_ref", "conflict_report_ref"),
                        properties={
                            "rubric_report_ref": required_string_schema(),
                            "conflict_report_ref": required_string_schema(),
                        },
                    ),
                    "operator_gate_required": {"type": "boolean"},
                    "reason": required_string_schema(),
                },
            ),
            "presentation": {"display_name": "AwardDecision"},
        },
        {
            "id": "OperatorDecision",
            "schema": _object_schema(
                required=(
                    "gate_id",
                    "bundle_id",
                    "decision",
                    "actor_kind",
                    "audit_reason",
                ),
                properties={
                    "gate_id": required_string_schema(),
                    "bundle_id": required_string_schema(),
                    "decision": {"enum": ("approve", "reject")},
                    "actor_kind": {"const": "local_operator"},
                    "audit_reason": required_string_schema(),
                },
            ),
            "presentation": {"display_name": "OperatorDecision"},
        },
        {
            "id": "PolicyDecision",
            "schema": _object_schema(
                required=(
                    "source_request_id",
                    "policy_status",
                    "violated_policy_facts",
                    "reason",
                ),
                properties={
                    "source_request_id": required_string_schema(),
                    "policy_status": {"const": "blocked"},
                    "violated_policy_facts": {
                        "type": "array",
                        "min_items": 1,
                        "items": {
                            "enum": (
                                "category_not_permitted",
                                "budget_band_not_permitted",
                            )
                        },
                    },
                    "reason": required_string_schema(),
                },
            ),
            "presentation": {"display_name": "PolicyDecision"},
        },
        {
            "id": "DecisionPack",
            "schema": _object_schema(
                required=(
                    "source_request_id",
                    "bundle_id",
                    "selected_candidate_id",
                    "final_refusal_reason",
                    "evidence_refs",
                    "selected_plan_id",
                    "selected_plan_fingerprint",
                    "close_reason",
                ),
                properties={
                    "source_request_id": required_string_schema(),
                    "bundle_id": required_string_schema(),
                    "selected_candidate_id": nullable_string_enum_schema(
                        "vendor_alpha",
                        "vendor_beta",
                        "vendor_gamma",
                    ),
                    "final_refusal_reason": nullable_string_enum_schema(
                        "policy_blocked",
                        "no_viable_vendor",
                        "operator_rejected",
                        "blocked",
                    ),
                    "evidence_refs": _object_schema(
                        required=("rubric_report_ref", "conflict_report_ref"),
                        properties={
                            "rubric_report_ref": required_string_schema(),
                            "conflict_report_ref": required_string_schema(),
                            "operator_decision_ref": required_string_schema(),
                        },
                    ),
                    "selected_plan_id": required_string_schema(),
                    "selected_plan_fingerprint": required_string_schema(),
                    "close_reason": {
                        "enum": (
                            "awarded",
                            "policy_blocked",
                            "no_viable_vendor",
                            "operator_rejected",
                            "blocked",
                        )
                    },
                },
            ),
            "presentation": {"display_name": "DecisionPack"},
        },
    ],
    "assets": (),
    "stage_kinds": [
        _stage(
            stage_id="request_intake",
            partition_id="requirements",
            inputs=("purchase_request",),
            outputs=("purchase_request", "decision_pack"),
            schemas=("PurchaseRequest", "DecisionPack"),
            outcomes=(
                "vendor_selection.request_intake.request_ready",
                "vendor_selection.request_intake.needs_clarification",
            ),
        ),
        _stage(
            stage_id="policy_screener",
            partition_id="requirements",
            inputs=("purchase_request",),
            outputs=("purchase_request", "decision_pack"),
            schemas=("PurchaseRequest", "PolicyDecision"),
            outcomes=(
                "vendor_selection.policy_screener.policy_allowed",
                "vendor_selection.policy_screener.policy_blocked",
            ),
        ),
        _stage(
            stage_id="requirement_freezer",
            partition_id="requirements",
            inputs=("purchase_request",),
            outputs=("requirement_packet",),
            schemas=("PurchaseRequest", "RequirementPacket"),
            outcomes=("vendor_selection.requirement_freezer.requirements_ready",),
        ),
        _stage(
            stage_id="catalog_sourcer",
            partition_id="sourcing",
            inputs=("requirement_packet",),
            outputs=("candidate_bundle", "decision_pack"),
            schemas=("RequirementPacket", "CandidateBundle", "DecisionPack"),
            outcomes=(
                "vendor_selection.catalog_sourcer.candidates_ready",
                "vendor_selection.catalog_sourcer.no_viable_vendor",
            ),
        ),
        _stage(
            stage_id="candidate_packager",
            partition_id="sourcing",
            inputs=("candidate_bundle",),
            outputs=("candidate_bundle",),
            schemas=("CandidateBundle",),
            outcomes=("vendor_selection.candidate_packager.candidates_ready",),
        ),
        _stage(
            stage_id="rubric_evaluator",
            partition_id="evaluation",
            inputs=("candidate_bundle",),
            outputs=("evaluation_report",),
            schemas=("CandidateBundle", "RubricReport"),
            outcomes=("vendor_selection.rubric_evaluator.rubric_complete",),
        ),
        _stage(
            stage_id="conflict_checker",
            partition_id="evaluation",
            inputs=("candidate_bundle",),
            outputs=("evaluation_report",),
            schemas=("CandidateBundle", "ConflictReport"),
            outcomes=("vendor_selection.conflict_checker.conflict_complete",),
        ),
        _stage(
            stage_id="award_decider",
            partition_id="authorization",
            inputs=("candidate_bundle", "evaluation_report"),
            outputs=("authorization_decision", "requirement_packet", "decision_pack"),
            schemas=(
                "CandidateBundle",
                "RubricReport",
                "ConflictReport",
                "AwardDecision",
                "RequirementPacket",
                "DecisionPack",
            ),
            outcomes=(
                "vendor_selection.award_decider.award_ready",
                "vendor_selection.award_decider.resource_required",
                "vendor_selection.award_decider.operator_required",
                "vendor_selection.award_decider.no_viable_vendor",
                "vendor_selection.award_decider.blocked",
            ),
        ),
        _stage(
            stage_id="decision_packager",
            partition_id="authorization",
            inputs=("authorization_decision", "decision_pack"),
            outputs=("decision_pack",),
            schemas=("AwardDecision", "OperatorDecision", "DecisionPack"),
            outcomes=("vendor_selection.decision_packager.decision_pack_ready",),
        ),
    ],
    "terminal_outcomes": [
        _outcome("request_intake", "request_ready", "REQUEST_READY"),
        _outcome(
            "request_intake",
            "needs_clarification",
            "REQUEST_NEEDS_CLARIFICATION",
        ),
        _outcome("policy_screener", "policy_allowed", "POLICY_ALLOWED"),
        _outcome("policy_screener", "policy_blocked", "POLICY_BLOCKED"),
        _outcome("requirement_freezer", "requirements_ready", "REQUIREMENTS_READY"),
        _outcome("catalog_sourcer", "candidates_ready", "CANDIDATES_READY"),
        _outcome("catalog_sourcer", "no_viable_vendor", "NO_VIABLE_VENDOR"),
        _outcome("candidate_packager", "candidates_ready", "CANDIDATES_READY"),
        _outcome("rubric_evaluator", "rubric_complete", "RUBRIC_COMPLETE"),
        _outcome("conflict_checker", "conflict_complete", "CONFLICT_COMPLETE"),
        _outcome("award_decider", "award_ready", "AWARD_READY"),
        _outcome("award_decider", "resource_required", "RESOURCE_REQUIRED"),
        _outcome("award_decider", "operator_required", "OPERATOR_REQUIRED"),
        _outcome("award_decider", "no_viable_vendor", "NO_VIABLE_VENDOR"),
        _outcome("award_decider", "blocked", "BLOCKED"),
        _outcome("decision_packager", "decision_pack_ready", "DECISION_PACK_READY"),
    ],
    "terminal_actions": [
        _route_action(
            action_id="vendor_selection.request_intake.request_ready",
            stage_kind_id="request_intake",
            outcome_id="vendor_selection.request_intake.request_ready",
            target_stage_kind_id="policy_screener",
            target_graph_node_id="vendor_selection.policy_screener.start",
            emitted_queue_family_id="purchase_request",
            artifact_schema_id="PurchaseRequest",
        ),
        _artifact_action(
            action_id="vendor_selection.request_intake.needs_clarification",
            stage_kind_id="request_intake",
            outcome_id="vendor_selection.request_intake.needs_clarification",
            kind="close",
            artifact_schema_id="DecisionPack",
        ),
        _route_action(
            action_id="vendor_selection.policy_screener.policy_allowed",
            stage_kind_id="policy_screener",
            outcome_id="vendor_selection.policy_screener.policy_allowed",
            target_stage_kind_id="requirement_freezer",
            target_graph_node_id="vendor_selection.requirement_freezer.start",
            emitted_queue_family_id="purchase_request",
            artifact_schema_id="PurchaseRequest",
        ),
        _artifact_action(
            action_id="vendor_selection.policy_screener.policy_blocked",
            stage_kind_id="policy_screener",
            outcome_id="vendor_selection.policy_screener.policy_blocked",
            kind="close",
            artifact_schema_id="PolicyDecision",
        ),
        _route_action(
            action_id="vendor_selection.requirement_freezer.requirements_ready",
            stage_kind_id="requirement_freezer",
            outcome_id="vendor_selection.requirement_freezer.requirements_ready",
            target_stage_kind_id="catalog_sourcer",
            target_graph_node_id="vendor_selection.catalog_sourcer.start",
            emitted_queue_family_id="requirement_packet",
            artifact_schema_id="RequirementPacket",
        ),
        _route_action(
            action_id="vendor_selection.catalog_sourcer.candidates_ready",
            stage_kind_id="catalog_sourcer",
            outcome_id="vendor_selection.catalog_sourcer.candidates_ready",
            target_stage_kind_id="candidate_packager",
            target_graph_node_id="vendor_selection.candidate_packager.start",
            emitted_queue_family_id="candidate_bundle",
            artifact_schema_id="CandidateBundle",
        ),
        _route_action(
            action_id="vendor_selection.catalog_sourcer.no_viable_vendor",
            stage_kind_id="catalog_sourcer",
            outcome_id="vendor_selection.catalog_sourcer.no_viable_vendor",
            target_stage_kind_id="decision_packager",
            target_graph_node_id="vendor_selection.decision_packager.start",
            emitted_queue_family_id="decision_pack",
            artifact_schema_id="DecisionPack",
        ),
        _artifact_action(
            action_id="vendor_selection.candidate_packager.candidates_ready",
            stage_kind_id="candidate_packager",
            outcome_id="vendor_selection.candidate_packager.candidates_ready",
            kind="complete_work_item",
            artifact_schema_id="CandidateBundle",
        ),
        _artifact_action(
            action_id="vendor_selection.rubric_evaluator.rubric_complete",
            stage_kind_id="rubric_evaluator",
            outcome_id="vendor_selection.rubric_evaluator.rubric_complete",
            kind="complete_work_item",
            artifact_schema_id="RubricReport",
        ),
        _artifact_action(
            action_id="vendor_selection.conflict_checker.conflict_complete",
            stage_kind_id="conflict_checker",
            outcome_id="vendor_selection.conflict_checker.conflict_complete",
            kind="complete_work_item",
            artifact_schema_id="ConflictReport",
        ),
        _route_action(
            action_id="vendor_selection.award_decider.award_ready",
            stage_kind_id="award_decider",
            outcome_id="vendor_selection.award_decider.award_ready",
            target_stage_kind_id="decision_packager",
            target_graph_node_id="vendor_selection.decision_packager.start",
            emitted_queue_family_id="authorization_decision",
            artifact_schema_id="AwardDecision",
        ),
        _route_action(
            action_id="vendor_selection.award_decider.resource_required",
            stage_kind_id="award_decider",
            outcome_id="vendor_selection.award_decider.resource_required",
            target_stage_kind_id="catalog_sourcer",
            target_graph_node_id="vendor_selection.catalog_sourcer.start",
            emitted_queue_family_id="requirement_packet",
            artifact_schema_id="RequirementPacket",
        ),
        _artifact_action(
            action_id=OPERATOR_WAIT_ACTION_ID,
            stage_kind_id="award_decider",
            outcome_id="vendor_selection.award_decider.operator_required",
            kind="operator_wait",
            artifact_schema_id="AwardDecision",
        ),
        _route_action(
            action_id="vendor_selection.award_decider.no_viable_vendor",
            stage_kind_id="award_decider",
            outcome_id="vendor_selection.award_decider.no_viable_vendor",
            target_stage_kind_id="decision_packager",
            target_graph_node_id="vendor_selection.decision_packager.start",
            emitted_queue_family_id="decision_pack",
            artifact_schema_id="DecisionPack",
        ),
        _route_action(
            action_id="vendor_selection.award_decider.blocked",
            stage_kind_id="award_decider",
            outcome_id="vendor_selection.award_decider.blocked",
            target_stage_kind_id="decision_packager",
            target_graph_node_id="vendor_selection.decision_packager.start",
            emitted_queue_family_id="decision_pack",
            artifact_schema_id="DecisionPack",
        ),
        _artifact_action(
            action_id="vendor_selection.decision_packager.decision_pack_ready",
            stage_kind_id="decision_packager",
            outcome_id="vendor_selection.decision_packager.decision_pack_ready",
            kind="complete_work_item",
            artifact_schema_id="DecisionPack",
        ),
    ],
    "fanout_declarations": [
        {
            "id": "vendor_selection.candidate_packager.rubric_fanout",
            "source_action_id": "vendor_selection.candidate_packager.candidates_ready",
            "source_artifact_schema_id": "CandidateBundle",
            "item_source_path": ("candidate_vendors",),
            "item_id_key": "candidate_id",
            "target_route_id": "vendor_selection.rubric_work",
            "target_payload_schema_id": "CandidateBundle",
            "target_payload_mapping": {
                "source_requirement_id": ("source_requirement_id",),
                "bundle_id": ("bundle_id",),
                "candidate_vendors": ("candidate_vendors",),
                "deterministic_source_refs": ("deterministic_source_refs",),
                "approval_policy_hint": ("approval_policy_hint",),
                "conflict_rules": ("conflict_rules",),
            },
            "duplicate_policy": "refuse",
            "root_lineage_policy": "inherit_source_lineage",
            "dependency_policy": "depends_on_source_work_item",
        },
        {
            "id": "vendor_selection.candidate_packager.conflict_fanout",
            "source_action_id": "vendor_selection.candidate_packager.candidates_ready",
            "source_artifact_schema_id": "CandidateBundle",
            "item_source_path": ("candidate_vendors",),
            "item_id_key": "candidate_id",
            "target_route_id": "vendor_selection.conflict_work",
            "target_payload_schema_id": "CandidateBundle",
            "target_payload_mapping": {
                "source_requirement_id": ("source_requirement_id",),
                "bundle_id": ("bundle_id",),
                "candidate_vendors": ("candidate_vendors",),
                "deterministic_source_refs": ("deterministic_source_refs",),
                "approval_policy_hint": ("approval_policy_hint",),
                "conflict_rules": ("conflict_rules",),
            },
            "duplicate_policy": "refuse",
            "root_lineage_policy": "inherit_source_lineage",
            "dependency_policy": "depends_on_source_work_item",
        },
    ],
    "join_declarations": [
        {
            "id": JOIN_ID,
            "target_stage_kind_id": "award_decider",
            "correlation_key": "bundle_id",
            "required_artifact_schema_ids": ("RubricReport", "ConflictReport"),
            "missing_policy": "wait",
        }
    ],
    "concurrency_policies": [
        {
            "id": "requirements.standard",
            "partition_id": "requirements",
            "max_active_runs": 1,
            "coexist_partition_ids": ("sourcing", "evaluation", "authorization"),
        },
        {
            "id": "sourcing.standard",
            "partition_id": "sourcing",
            "max_active_runs": 1,
            "coexist_partition_ids": ("requirements", "evaluation", "authorization"),
        },
        {
            "id": "evaluation.parallel",
            "partition_id": "evaluation",
            "max_active_runs": 2,
            "coexist_partition_ids": ("requirements", "sourcing", "authorization"),
        },
        {
            "id": "authorization.standard",
            "partition_id": "authorization",
            "max_active_runs": 1,
            "coexist_partition_ids": ("requirements", "sourcing", "evaluation"),
        },
    ],
    "recovery_policies": (),
    "wait_states": (),
    "counters": (),
    "completion_behaviors": (),
    "remediation_policies": (),
    "intervention_options": (),
    "operator_waits": [
        {
            "id": OPERATOR_WAIT_ID,
            "source_action_ids": (OPERATOR_WAIT_ACTION_ID,),
            "wait_scope": "lineage",
            "source_work_item_behavior": "leave_open",
            "unrelated_lineages_continue": True,
            "allowed_resolution_kinds": (
                "resume_recorded_source",
                "revise_recorded_source",
            ),
            "payload_schema_id": "OperatorDecision",
            "target_queue_family_id": "decision_pack",
            "target_stage_kind_id": "decision_packager",
            "target_graph_node_id": "vendor_selection.decision_packager.start",
            "target_runner_binding_id": RUNNER_ID,
            "actor_kind": "local_operator",
            "audit_metadata_requirements": _operator_wait_audit_metadata_requirements(
                ("resume_recorded_source", "revise_recorded_source")
            ),
            "correlation_key": "wait_id",
            "idempotency": "input_receipt_and_active_wait_status",
            "timeout_policy": "none",
            "expiry_policy": "none",
            "cancellation_policy": "selected_resolution_only",
            "status_effect": "operator_wait_active",
        }
    ],
    "runner_bindings": [
        {
            "id": RUNNER_ID,
            "adapter_kind": "fake_local",
            "stage_kind_ids": STAGE_KIND_IDS,
            "presentation": {"display_name": "Deterministic vendor runner"},
            "required_capability_ids": ("capability.runner.invoke",),
        }
    ],
    "capabilities": [
        {
            "id": "capability.runner.invoke",
            "kind": "runner.invoke",
            "support_status": "supported",
            "grant_status": "granted",
            "approval_policy_id": None,
        }
    ],
    "unselected_catalog": [
        {
            "id": "vendor_selection.catalog.alpha",
            "candidate_id": "vendor_alpha",
            "vendor_label": "Alpha Stationery",
            "capabilities": ("standard_office_supplies", "net30_invoice"),
            "budget_band": "low",
            "conflict_flag": "clear",
        },
        {
            "id": "vendor_selection.catalog.beta",
            "candidate_id": "vendor_beta",
            "vendor_label": "Beta Supplies",
            "capabilities": ("standard_office_supplies", "rush_delivery"),
            "budget_band": "medium",
            "conflict_flag": "blocked",
        },
        {
            "id": "vendor_selection.catalog.gamma",
            "candidate_id": "vendor_gamma",
            "vendor_label": "Gamma Office",
            "capabilities": (
                "standard_office_supplies",
                "net30_invoice",
                "rush_delivery",
            ),
            "budget_band": "medium",
            "conflict_flag": "clear",
        },
    ],
}
