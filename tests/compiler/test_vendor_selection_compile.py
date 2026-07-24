from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, cast

from millrace.contracts import ArtifactSchemaId, QueueFamilyId, RunnerBindingId
from millrace.contracts.schema import validate_schema
from support import vendor_selection


class _HasId(Protocol):
    @property
    def id(self) -> object: ...


def _id_values(records: Iterable[_HasId]) -> set[str]:
    return {str(record.id) for record in records}


def _by_id(records: Iterable[_HasId]) -> dict[str, _HasId]:
    return {str(record.id): record for record in records}


def test_vendor_selection_identity_is_selected_workflow_data() -> None:
    plan, _fingerprint = vendor_selection.compile_vendor_selection()

    assert str(plan.workflow.workflow_id) == "vendor_selection"
    assert str(plan.workflow.workflow_version) == "0.1"
    assert plan.workflow.workflow_name == "Vendor Selection"
    assert plan.compatibility_profile is None
    assert plan.required_extensions == ()
    assert plan.effect_declarations == ()


def test_vendor_selection_compiles_selected_source_encoding() -> None:
    plan, _fingerprint = vendor_selection.compile_vendor_selection()

    assert tuple(str(partition.id) for partition in plan.partitions) == (
        "authorization",
        "evaluation",
        "requirements",
        "sourcing",
    )
    assert {partition.partition_kind for partition in plan.partitions} == {"plane"}


def test_vendor_selection_queue_schema_authority_is_route_stage_and_action_based() -> (
    None
):
    plan, _fingerprint = vendor_selection.compile_vendor_selection()
    source = vendor_selection.source()

    assert _id_values(plan.queue_families) == set(vendor_selection.QUEUE_FAMILY_IDS)
    assert {
        str(queue.id)
        for queue in plan.queue_families
        if queue.external_enqueue
    } == {"purchase_request"}
    assert all(
        "item_schema" not in record
        for record in vendor_selection.records(source, "queue_families")
    )

    external_route = plan.external_enqueue_routes[0]
    assert external_route.queue_family_id == QueueFamilyId("purchase_request")
    assert external_route.payload_schema_id == ArtifactSchemaId("PurchaseRequest")

    route_schema_ids = {
        str(route.payload_schema_id)
        for route in (*plan.external_enqueue_routes, *plan.generated_work_routes)
        if route.payload_schema_id is not None
    }
    stage_schema_ids = {
        str(schema_id)
        for stage in plan.stage_kinds
        for schema_id in stage.artifact_schema_ids
    }
    action_schema_ids = {
        str(action.artifact_schema_id)
        for action in plan.terminal_actions
        if action.artifact_schema_id is not None
    }

    assert set(vendor_selection.ARTIFACT_SCHEMA_IDS) <= (
        route_schema_ids | stage_schema_ids | action_schema_ids
    )


def test_vendor_selection_stage_and_runner_authority_has_no_operator_gate_stage() -> (
    None
):
    plan, _fingerprint = vendor_selection.compile_vendor_selection()

    assert _id_values(plan.stage_kinds) == set(vendor_selection.STAGE_KIND_IDS)
    assert "operator_gate" not in _id_values(plan.stage_kinds)
    assert len(plan.runner_bindings) == 1

    runner = plan.runner_bindings[0]
    assert runner.id == RunnerBindingId(vendor_selection.RUNNER_ID)
    assert runner.adapter_kind == "codex"
    assert {str(stage_id) for stage_id in runner.stage_kind_ids} == set(
        vendor_selection.STAGE_KIND_IDS
    )


def test_vendor_selection_terminal_actions_cover_selected_outcomes() -> None:
    plan, _fingerprint = vendor_selection.compile_vendor_selection()

    declared = {
        (str(outcome.stage_kind_id), str(outcome.id))
        for outcome in plan.terminal_outcomes
    }
    covered = {
        (str(action.stage_kind_id), str(action.outcome_id))
        for action in plan.terminal_actions
    }

    assert declared == covered
    assert "deferred_terminal_action" not in {
        action.action_kind for action in plan.terminal_actions
    }
    assert "approve" not in {action.action_kind for action in plan.terminal_actions}
    assert "reject" not in {action.action_kind for action in plan.terminal_actions}


def test_vendor_selection_fanout_uses_two_selected_declarations() -> None:
    plan, _fingerprint = vendor_selection.compile_vendor_selection()

    assert len(plan.fanout_declarations) == 2
    by_target = {
        str(fanout.target_stage_kind_id): fanout
        for fanout in plan.fanout_declarations
    }
    assert set(by_target) == {"rubric_evaluator", "conflict_checker"}
    expected_mapping = {
        "source_requirement_id": ("source_requirement_id",),
        "bundle_id": ("bundle_id",),
        "candidate_vendors": ("candidate_vendors",),
        "deterministic_source_refs": ("deterministic_source_refs",),
        "approval_policy_hint": ("approval_policy_hint",),
        "conflict_rules": ("conflict_rules",),
    }
    for fanout in by_target.values():
        assert str(fanout.source_action_id) == (
            "vendor_selection.candidate_packager.candidates_ready"
        )
        assert str(fanout.source_artifact_schema_id) == "CandidateBundle"
        assert fanout.item_source_path == ("candidate_vendors",)
        assert fanout.item_id_key == "candidate_id"
        assert fanout.target_payload_mapping == expected_mapping
        assert fanout.duplicate_policy == "refuse"
        assert fanout.root_lineage_policy == "inherit_source_lineage"
        assert fanout.dependency_policy == "depends_on_source_work_item"


def test_vendor_selection_declares_required_decision_context_handoffs() -> None:
    plan, _fingerprint = vendor_selection.compile_vendor_selection()
    schemas = _by_id(plan.artifact_schemas)

    requirement_schema = cast(dict[str, object], schemas["RequirementPacket"].schema)
    assert tuple(cast(Iterable[str], requirement_schema["required"])) == (
        "source_request_id",
        "approval_policy_hint",
        "frozen_requirements",
        "policy_status",
        "selection_rubric_id",
        "conflict_rules",
        "candidate_count_min",
        "candidate_count_max",
    )
    requirement_properties = cast(dict[str, object], requirement_schema["properties"])
    assert requirement_properties["approval_policy_hint"] == {
        "enum": ("none", "operator_required")
    }

    candidate_schema = cast(dict[str, object], schemas["CandidateBundle"].schema)
    assert tuple(cast(Iterable[str], candidate_schema["required"])) == (
        "source_requirement_id",
        "bundle_id",
        "candidate_vendors",
        "deterministic_source_refs",
        "approval_policy_hint",
        "conflict_rules",
    )
    candidate_properties = cast(dict[str, object], candidate_schema["properties"])
    assert candidate_properties["approval_policy_hint"] == {
        "enum": ("none", "operator_required")
    }
    assert candidate_properties["conflict_rules"] == {
        "items": {"min_length": 1, "type": "string"},
        "min_items": 1,
        "type": "array",
    }
    candidate_items = cast(
        dict[str, object],
        cast(dict[str, object], candidate_properties["candidate_vendors"])["items"],
    )
    assert tuple(cast(Iterable[str], candidate_items["required"])) == (
        "candidate_id",
        "vendor_label",
        "capabilities",
        "budget_band",
        "catalog_ref",
        "conflict_status",
    )
    candidate_item_properties = cast(dict[str, object], candidate_items["properties"])
    assert candidate_item_properties["conflict_status"] == {
        "enum": ("clear", "blocked")
    }


def test_vendor_selection_join_declaration_exports_candidate_evidence_join() -> None:
    plan, _fingerprint = vendor_selection.compile_vendor_selection()

    assert len(plan.join_declarations) == 1
    join = plan.join_declarations[0]

    assert str(join.id) == "candidate_evidence_join"
    assert str(join.target_stage_kind_id) == "award_decider"
    assert join.correlation_key == "bundle_id"
    assert tuple(str(schema_id) for schema_id in join.required_artifact_schema_ids) == (
        "RubricReport",
        "ConflictReport",
    )
    assert join.missing_policy == "wait"


def test_vendor_selection_concurrency_policies_are_selected_partitions() -> None:
    plan, _fingerprint = vendor_selection.compile_vendor_selection()

    by_partition = {
        str(policy.partition_id): policy
        for policy in plan.concurrency_policies
    }
    assert set(by_partition) == set(vendor_selection.PARTITION_IDS)
    assert by_partition["evaluation"].id == "evaluation.parallel"
    assert by_partition["evaluation"].max_active_runs == 2
    evaluation_coexist_partitions = {
        str(partition_id)
        for partition_id in by_partition["evaluation"].coexist_partition_ids
    }
    assert evaluation_coexist_partitions == {
        "requirements",
        "sourcing",
        "authorization",
    }


def test_vendor_selection_operator_wait_uses_existing_resolution_kinds() -> None:
    plan, _fingerprint = vendor_selection.compile_vendor_selection()

    assert len(plan.operator_waits) == 1
    wait = plan.operator_waits[0]

    assert str(wait.id) == vendor_selection.OPERATOR_WAIT_ID
    assert tuple(str(action_id) for action_id in wait.source_action_ids) == (
        vendor_selection.OPERATOR_WAIT_ACTION_ID,
    )
    assert wait.allowed_resolution_kinds == (
        "resume_recorded_source",
        "revise_recorded_source",
    )
    assert wait.target_queue_family_id == QueueFamilyId("decision_pack")
    assert str(wait.target_stage_kind_id) == "decision_packager"
    assert wait.actor_kind == "local_operator"


def test_vendor_selection_schema_rejects_blank_required_strings() -> None:
    plan, _fingerprint = vendor_selection.compile_vendor_selection()
    schemas = _by_id(plan.artifact_schemas)
    purchase_request = schemas["PurchaseRequest"]
    schema = cast(dict[str, object], purchase_request.schema)

    result = validate_schema(
        schema,
        {
            "request_id": "",
            "requester_label": "ops",
            "category": "office",
            "budget_band": "low",
            "required_capabilities": ("standard_office_supplies",),
            "disallowed_vendors": (),
            "approval_policy_hint": "none",
        },
    )

    assert result.accepted is False
    assert any(issue.path == "$.request_id" for issue in result.issues)


def test_vendor_selection_schema_rejects_invalid_array_items() -> None:
    plan, _fingerprint = vendor_selection.compile_vendor_selection()
    schemas = _by_id(plan.artifact_schemas)
    purchase_request = schemas["PurchaseRequest"]
    schema = cast(dict[str, object], purchase_request.schema)

    result = validate_schema(
        schema,
        {
            "request_id": "PR-1",
            "requester_label": "ops",
            "category": "office",
            "budget_band": "low",
            "required_capabilities": ("standard_office_supplies", 42),
            "disallowed_vendors": (),
            "approval_policy_hint": "none",
        },
    )

    assert result.accepted is False
    assert any(issue.path == "$.required_capabilities[1]" for issue in result.issues)


def test_policy_block_uses_stage_owned_truthful_artifact_schema() -> None:
    plan, _fingerprint = vendor_selection.compile_vendor_selection()
    schemas = _by_id(plan.artifact_schemas)
    actions = _by_id(plan.terminal_actions)
    policy_action = actions["vendor_selection.policy_screener.policy_blocked"]

    assert str(policy_action.artifact_schema_id) == "PolicyDecision"
    schema = cast(dict[str, object], schemas["PolicyDecision"].schema)
    payload = {
        "source_request_id": "request-office-001",
        "policy_status": "blocked",
        "violated_policy_facts": ["category_not_permitted"],
        "reason": "Category is outside the selected package policy.",
    }

    assert validate_schema(schema, payload).accepted is True
    assert "evidence_refs" not in cast(dict[str, object], schema["properties"])

    missing_violation = {**payload, "violated_policy_facts": []}
    result = validate_schema(schema, missing_violation)
    assert result.accepted is False
    assert any(issue.path == "$.violated_policy_facts" for issue in result.issues)


def test_vendor_selection_award_decision_allows_nullable_candidate_id() -> None:
    plan, _fingerprint = vendor_selection.compile_vendor_selection()
    schemas = _by_id(plan.artifact_schemas)
    award_decision = schemas["AwardDecision"]
    schema = cast(dict[str, object], award_decision.schema)
    base_payload = {
        "bundle_id": "bundle-1",
        "decision_kind": "reject",
        "selected_candidate_id": None,
        "required_evidence_refs": {
            "rubric_report_ref": "rubric-1",
            "conflict_report_ref": "conflict-1",
        },
        "operator_gate_required": False,
        "reason": "no viable vendor",
    }

    assert validate_schema(schema, base_payload).accepted is True

    blank_candidate = {**base_payload, "selected_candidate_id": ""}
    blank_result = validate_schema(schema, blank_candidate)

    assert blank_result.accepted is False
    assert any(issue.path == "$.selected_candidate_id" for issue in blank_result.issues)
