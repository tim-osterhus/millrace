from __future__ import annotations

from collections.abc import Iterable
from typing import cast

import pytest

from millrace.compiler import SelectedRunnerAdapterPolicy
from millrace.compiler import compile_workflow as _raw_compile_workflow
from millrace.contracts import Diagnostic
from millrace.workflows import simple_loop

Source = dict[str, object]
Record = dict[str, object]
_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)


def _compile_codex(source: Source):
    return _raw_compile_workflow(source, selected_runner_policy=_CODEX_POLICY)


def _source() -> Source:
    return simple_loop.workflow_source()


def _workflow(source: Source) -> Record:
    return cast(Record, source["workflow"])


def _records(source: Source, key: str) -> list[Record]:
    return cast(list[Record], source[key])


def _errors(source: Source) -> tuple[Diagnostic, ...]:
    result = _compile_codex(source)
    assert result.plan is None
    return tuple(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    )


def _find_error(errors: Iterable[Diagnostic], code: str) -> Diagnostic:
    matches = [diagnostic for diagnostic in errors if diagnostic.code == code]
    assert matches, f"missing diagnostic code {code!r} in {tuple(errors)!r}"
    return matches[0]


def _action(source: Source, action_id: str) -> Record:
    return next(
        action
        for action in _records(source, "terminal_actions")
        if action["id"] == action_id
    )


def _asset(source: Source, asset_id: str) -> Record:
    return next(
        asset for asset in _records(source, "assets") if asset["id"] == asset_id
    )


def _troubleshooter_prompt_asset(source: Source) -> Record:
    return _asset(source, "simple_loop.troubleshooter_prompt")


def _troubleshooter_stage(source: Source) -> Record:
    return next(
        stage
        for stage in _records(source, "stage_kinds")
        if stage["id"] == "simple_loop.troubleshooter"
    )


def _policy(source: Source) -> Record:
    return _records(source, "recovery_policies")[0]


def _valid_resume_option() -> Record:
    return {
        "id": "simple_loop.resume_lineage",
        "policy_id": "simple_loop.blocked_recovery",
        "kind": "resume_lineage",
        "legal_source_state": "active_lineage_quarantine",
        "target_selector": "selected_quarantine_or_active_quarantine_by_lineage",
        "resume_target_selector": "recorded_source",
        "close_behavior": None,
        "supersede_behavior": "supersede_quarantine",
        "attempt_effect": "resolve_attempt",
        "actor_kind": "local_operator",
        "audit_metadata_requirements": (
            "input_id",
            "input_digest",
            "selected_plan_fingerprint",
            "actor_id",
            "actor_kind",
            "reason",
            "option_id",
            "policy_id",
            "lineage_id",
            "quarantine_id",
            "recovery_attempt_record_id",
            "target_activation_id",
            "empty_payload",
        ),
    }


def _valid_revise_option() -> Record:
    return {
        "id": "simple_loop.revise_lineage",
        "policy_id": "simple_loop.blocked_recovery",
        "kind": "revise_lineage",
        "legal_source_state": "active_lineage_quarantine",
        "target_selector": "selected_quarantine_or_active_quarantine_by_lineage",
        "resume_target_selector": None,
        "close_behavior": None,
        "payload_schema_id": "simple_loop.work_packet",
        "target_queue_family_id": "work_packet",
        "target_stage_kind_id": "simple_loop.worker",
        "target_graph_node_id": "simple_loop.worker.start",
        "target_runner_binding_id": "simple_loop.default_agent_runner",
        "supersede_behavior": "supersede_quarantine",
        "attempt_effect": "resolve_attempt",
        "actor_kind": "local_operator",
        "audit_metadata_requirements": (
            "input_id",
            "input_digest",
            "selected_plan_fingerprint",
            "actor_id",
            "actor_kind",
            "reason",
            "option_id",
            "policy_id",
            "lineage_id",
            "quarantine_id",
            "recovery_attempt_record_id",
            "recovery_attempt_count",
            "target_work_item_id",
            "target_activation_id",
            "payload_digest",
            "payload_reference",
        ),
    }


def test_partitionless_stage_partition_none_and_omitted_are_preserved() -> None:
    source = _source()
    result = _compile_codex(source)
    assert result.plan is not None
    trouble = next(
        stage
        for stage in result.plan.stage_kinds
        if str(stage.id) == "simple_loop.troubleshooter"
    )
    assert trouble.partition_id is None

    omitted = _source()
    _troubleshooter_stage(omitted).pop("partition_id")
    result = _compile_codex(omitted)
    assert result.plan is not None
    trouble = next(
        stage
        for stage in result.plan.stage_kinds
        if str(stage.id) == "simple_loop.troubleshooter"
    )
    assert trouble.partition_id is None


@pytest.mark.parametrize("partition_id", ("", "missing.partition"))
def test_stage_partition_reference_must_be_non_empty_or_declared(
    partition_id: str,
) -> None:
    source = _source()
    _troubleshooter_stage(source)["partition_id"] = partition_id

    error = _find_error(_errors(source), "missing_reference")

    assert error.severity == "error"
    assert error.phase == "semantic_validation"
    assert error.declaration_path == "stage_kinds[3].partition_id"
    assert error.context["referrer_path"] == "stage_kinds[3]"
    assert error.context["reference_kind"] == "partition"
    assert error.context["referenced_id"] == partition_id


@pytest.mark.parametrize(
    ("collection_key", "namespace"),
    (
        ("queue_families", "queue_family"),
        ("stage_kinds", "stage_kind"),
    ),
)
def test_duplicate_ids_remain_generic_diagnostics(
    collection_key: str,
    namespace: str,
) -> None:
    source = _source()
    records = _records(source, collection_key)
    records[1]["id"] = records[0]["id"]

    error = _find_error(_errors(source), "duplicate_id")

    assert error.declaration_path == f"{collection_key}[1].id"
    assert error.related_declaration_path == f"{collection_key}[0].id"
    assert error.context["namespace"] == namespace
    assert error.context["duplicate_id"] == records[0]["id"]


def test_terminal_outcome_without_action_is_refused() -> None:
    source = _source()
    _records(source, "terminal_actions")[:] = [
        action
        for action in _records(source, "terminal_actions")
        if action["outcome_id"] != "simple_loop.worker.failed"
    ]

    error = _find_error(_errors(source), "outcome_without_action")

    assert error.declaration_path == "terminal_outcomes[8].id"
    assert error.context["stage_kind_id"] == "simple_loop.worker"
    assert error.context["outcome_id"] == "simple_loop.worker.failed"


def test_terminal_action_referencing_undeclared_outcome_is_refused() -> None:
    source = _source()
    action = _action(source, "simple_loop.manager.packet_ready")
    action["outcome_id"] = "simple_loop.manager.missing_outcome"

    error = _find_error(_errors(source), "missing_reference")

    assert error.declaration_path == "terminal_actions[0].outcome_id"
    assert error.context["referrer_path"] == "terminal_actions[0]"
    assert error.context["reference_kind"] == "terminal_outcome"
    assert error.context["referenced_id"] == "simple_loop.manager.missing_outcome"


def test_terminal_action_outcome_must_belong_to_action_stage() -> None:
    source = _source()
    action = _action(source, "simple_loop.worker.work_done")
    action["outcome_id"] = "simple_loop.reviewer.accepted"

    error = _find_error(_errors(source), "outcome_stage_mismatch")

    assert error.declaration_path == "terminal_actions[5].outcome_id"
    assert error.context["stage_kind_id"] == "simple_loop.worker"
    assert error.context["outcome_id"] == "simple_loop.reviewer.accepted"
    assert error.context["outcome_stage_kind_id"] == "simple_loop.reviewer"


@pytest.mark.parametrize(
    "action_kind",
    ("unknown_action_kind", "lineage_update"),
)
def test_unsupported_terminal_action_kind_is_refused(action_kind: str) -> None:
    source = _source()
    action = _action(source, "simple_loop.manager.packet_ready")
    action["kind"] = action_kind

    error = _find_error(_errors(source), "unsupported_terminal_action_kind")

    assert error.declaration_path == "terminal_actions[0].kind"
    assert error.context["referrer_path"] == "terminal_actions[0]"
    assert error.context["action_id"] == "simple_loop.manager.packet_ready"
    assert error.context["action_kind"] == action_kind


@pytest.mark.parametrize(
    "selected_key",
    ("recovery_policies", "intervention_options", "operator_waits", "counters"),
)
def test_no_lineage_policy_rejects_lineage_dependent_authority(
    selected_key: str,
) -> None:
    source = _source()
    source["lineage_policy"] = "none"
    for key in (
        "recovery_policies",
        "intervention_options",
        "operator_waits",
        "counters",
    ):
        if key != selected_key:
            source[key] = []

    error = _find_error(_errors(source), "lineage_policy_conflict")

    assert error.context["lineage_policy"] == "none"
    assert error.context["selected_key"] == selected_key


def test_missing_lineage_policy_is_refused() -> None:
    source = _source()
    del source["lineage_policy"]

    error = _find_error(_errors(source), "missing_lineage_policy")

    assert error.declaration_path == "lineage_policy"


def test_route_action_missing_executable_field_is_refused() -> None:
    source = _source()
    _action(source, "simple_loop.manager.packet_ready")["target_graph_node_id"] = None

    error = _find_error(_errors(source), "terminal_route_missing_field")

    assert error.declaration_path == "terminal_actions[0].target_graph_node_id"
    assert error.context["referrer_path"] == "terminal_actions[0]"
    assert error.context["action_id"] == "simple_loop.manager.packet_ready"
    assert error.context["field_name"] == "target_graph_node_id"


def test_route_action_projection_must_remain_generic_authority() -> None:
    source = _source()
    action = _action(source, "simple_loop.manager.packet_ready")
    action["payload_projection"] = {
        "kind": "source",
        "path": ("unknown_root",),
    }

    error = _find_error(_errors(source), "invalid_terminal_projection")

    assert error.declaration_path == "terminal_actions[0].payload_projection"
    assert error.context["referrer_path"] == "terminal_actions[0]"
    assert error.context["action_id"] == "simple_loop.manager.packet_ready"
    assert error.context["reason"] == "unknown_source_root"
    assert error.context["detail"] == "unknown_root"


def test_route_action_queue_must_be_declared_by_source_stage() -> None:
    source = _source()
    _action(source, "simple_loop.manager.packet_ready")["emitted_queue_family_id"] = (
        "incident_report"
    )

    error = _find_error(_errors(source), "terminal_route_stage_output_mismatch")

    assert error.declaration_path == "terminal_actions[0].emitted_queue_family_id"
    assert error.context["source_stage_kind_id"] == "simple_loop.manager"
    assert error.context["queue_family_id"] == "incident_report"


@pytest.mark.parametrize(
    "field_name",
    (
        "target_stage_kind_id",
        "target_graph_node_id",
        "runner_binding_id",
        "asset_ids",
    ),
)
def test_recovery_route_requires_recovery_target_fields(field_name: str) -> None:
    source = _source()
    action = _action(source, "simple_loop.manager.blocked")
    action[field_name] = () if field_name == "asset_ids" else None

    error = _find_error(_errors(source), "terminal_recovery_route_missing_field")

    assert error.declaration_path == f"terminal_actions[3].{field_name}"
    assert error.context["referrer_path"] == "terminal_actions[3]"
    assert error.context["action_id"] == "simple_loop.manager.blocked"
    assert error.context["field_name"] == field_name


def test_recovery_route_entrypoint_prompt_preserves_authority() -> None:
    source = _source()
    _troubleshooter_prompt_asset(source)["kind"] = "entrypoint_prompt"
    expected_routes: dict[str, tuple[str, str, str, tuple[str, ...]]] = {}
    for action in _records(source, "terminal_actions"):
        if action.get("kind") != "recovery_route":
            continue
        expected_routes[str(action["id"])] = (
            str(action["target_stage_kind_id"]),
            str(action["target_graph_node_id"]),
            str(action["runner_binding_id"]),
            tuple(cast(tuple[str, ...], action["asset_ids"])),
        )

    result = _compile_codex(source)

    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ] == []
    assert result.plan is not None
    actual_routes = {
        str(action.id): (
            str(action.target_stage_kind_id),
            action.target_graph_node_id,
            str(action.runner_binding_id),
            tuple(str(asset_id) for asset_id in action.asset_ids),
        )
        for action in result.plan.terminal_actions
        if action.action_kind == "recovery_route"
    }
    assert actual_routes == expected_routes
    assert actual_routes == {
        "simple_loop.manager.blocked": (
            "simple_loop.troubleshooter",
            "simple_loop.troubleshooter.start",
            "simple_loop.default_agent_runner",
            ("simple_loop.troubleshooter_prompt",),
        ),
        "simple_loop.worker.blocked": (
            "simple_loop.troubleshooter",
            "simple_loop.troubleshooter.start",
            "simple_loop.default_agent_runner",
            ("simple_loop.troubleshooter_prompt",),
        ),
        "simple_loop.worker.failed": (
            "simple_loop.troubleshooter",
            "simple_loop.troubleshooter.start",
            "simple_loop.default_agent_runner",
            ("simple_loop.troubleshooter_prompt",),
        ),
        "simple_loop.reviewer.blocked": (
            "simple_loop.troubleshooter",
            "simple_loop.troubleshooter.start",
            "simple_loop.default_agent_runner",
            ("simple_loop.troubleshooter_prompt",),
        ),
    }


@pytest.mark.parametrize(
    "asset_kind",
    (
        "stage_skill",
        "shared_skill",
        "template",
        "schema",
        "example",
        "fixture",
        "blob",
        "skill",
    ),
)
def test_recovery_route_asset_must_be_prompt_like(asset_kind: str) -> None:
    source = _source()
    _troubleshooter_prompt_asset(source)["kind"] = asset_kind

    errors = _errors(source)
    mismatches = [
        error
        for error in errors
        if error.code == "terminal_recovery_route_asset_kind_mismatch"
    ]

    assert {error.declaration_path for error in mismatches} == {
        "terminal_actions[3].asset_ids[0]",
        "terminal_actions[7].asset_ids[0]",
        "terminal_actions[8].asset_ids[0]",
        "terminal_actions[12].asset_ids[0]",
    }
    for error in mismatches:
        assert error.context["asset_id"] == "simple_loop.troubleshooter_prompt"
        assert error.context["asset_kind"] == asset_kind


def test_recovery_route_runner_must_match_target_stage_runner() -> None:
    source = _source()
    _records(source, "runner_bindings").append(
        {
            "id": "simple_loop.alternate_runner",
            "adapter_kind": "fake_local",
            "stage_kind_ids": ("simple_loop.troubleshooter",),
            "presentation": {},
        }
    )
    _action(source, "simple_loop.manager.blocked")["runner_binding_id"] = (
        "simple_loop.alternate_runner"
    )

    error = _find_error(
        _errors(source),
        "terminal_recovery_route_stage_runner_mismatch",
    )

    assert error.declaration_path == "terminal_actions[3].runner_binding_id"
    assert error.context["target_stage_kind_id"] == "simple_loop.troubleshooter"
    assert error.context["route_runner_binding_id"] == "simple_loop.alternate_runner"
    assert (
        error.context["stage_runner_binding_id"] == "simple_loop.default_agent_runner"
    )


def test_recovery_route_runner_must_list_target_stage() -> None:
    source = _source()
    runner = _records(source, "runner_bindings")[0]
    runner["stage_kind_ids"] = (
        "simple_loop.manager",
        "simple_loop.worker",
        "simple_loop.reviewer",
    )

    error = _find_error(
        _errors(source),
        "terminal_recovery_route_runner_stage_mismatch",
    )

    assert error.declaration_path == "terminal_actions[3].runner_binding_id"
    assert error.context["target_stage_kind_id"] == "simple_loop.troubleshooter"
    assert error.context["runner_binding_id"] == "simple_loop.default_agent_runner"


@pytest.mark.parametrize(
    ("field_name", "bad_value", "expected_code", "reference_kind"),
    (
        (
            "source_recovery_action_ids",
            ("simple_loop.manager.packet_ready",),
            "invalid_recovery_policy_action_kind",
            "terminal_action",
        ),
        (
            "source_recovery_action_ids",
            ("missing.action",),
            "missing_reference",
            "terminal_action",
        ),
        (
            "return_action_ids",
            ("simple_loop.troubleshooter.operator_needed",),
            "invalid_recovery_policy_action_kind",
            "terminal_action",
        ),
        (
            "return_action_ids",
            ("missing.return",),
            "missing_reference",
            "terminal_action",
        ),
        (
            "quarantine_action_ids",
            ("simple_loop.troubleshooter.resolved",),
            "invalid_recovery_policy_action_kind",
            "terminal_action",
        ),
        (
            "quarantine_action_ids",
            ("missing.quarantine",),
            "missing_reference",
            "terminal_action",
        ),
        (
            "recovery_stage_kind_id",
            "missing.stage",
            "missing_reference",
            "stage_kind",
        ),
        (
            "attempt_scope",
            "workspace",
            "unsupported_recovery_policy_value",
            "recovery_policy",
        ),
        (
            "recorded_source_selector",
            "source_stage_name",
            "unsupported_recovery_policy_value",
            "recovery_policy",
        ),
        (
            "immediate_recovery_limit",
            0,
            "invalid_recovery_policy_threshold",
            "recovery_policy",
        ),
        (
            "cooldown_starts_at_attempt",
            0,
            "invalid_recovery_policy_threshold",
            "recovery_policy",
        ),
        (
            "quarantine_threshold_attempt",
            1,
            "invalid_recovery_policy_threshold",
            "recovery_policy",
        ),
        (
            "default_cooldown_seconds",
            0,
            "invalid_recovery_policy_threshold",
            "recovery_policy",
        ),
    ),
)
def test_invalid_recovery_policy_authority_is_refused(
    field_name: str,
    bad_value: object,
    expected_code: str,
    reference_kind: str,
) -> None:
    source = _source()
    _policy(source)[field_name] = bad_value

    error = _find_error(_errors(source), expected_code)

    assert error.severity == "error"
    assert error.phase == "semantic_validation"
    assert error.declaration_path.startswith("recovery_policies[0].")
    assert error.context["referrer_path"] == "recovery_policies[0]"
    assert error.context["reference_kind"] == reference_kind


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    (
        ("source_recovery_action_ids", "simple_loop.manager.blocked"),
        ("return_action_ids", "simple_loop.troubleshooter.resolved"),
        ("quarantine_action_ids", "simple_loop.troubleshooter.operator_needed"),
        ("reset_trigger_action_ids", "simple_loop.manager.packet_ready"),
        ("return_allowed_phases", "active_recovery"),
        ("recovery_stage_kind_id", ("simple_loop.troubleshooter",)),
        ("recorded_source_selector", ("latest_recovery_attempt_for_lineage",)),
        ("attempt_scope", ("lineage",)),
        ("threshold_behavior", ("quarantine_eligible_at_or_above_threshold",)),
        ("immediate_recovery_limit", "1"),
        ("cooldown_starts_at_attempt", "2"),
        ("quarantine_threshold_attempt", "3"),
        ("default_cooldown_seconds", "900"),
    ),
)
def test_malformed_recovery_policy_field_shapes_are_refused(
    field_name: str,
    bad_value: object,
) -> None:
    source = _source()
    _policy(source)[field_name] = bad_value

    result = _compile_codex(source)
    assert result.plan is None
    error = _find_error(result.diagnostics, "unsupported_authority_value")

    assert error.severity == "error"
    assert error.phase == "semantic_validation"
    assert error.declaration_path == f"recovery_policies[0].{field_name}"
    assert error.context["value_kind"] == "value"
    assert error.context["unsupported_type"] == type(bad_value).__name__


def test_recovery_policy_requires_policy_id() -> None:
    source = _source()
    _policy(source)["id"] = ""

    error = _find_error(_errors(source), "missing_id")

    assert error.declaration_path == "recovery_policies[0].id"
    assert error.context["namespace"] == "recovery_policy"
    assert error.context["field"] == "id"


def test_recovery_policy_source_action_must_target_policy_stage() -> None:
    source = _source()
    _action(source, "simple_loop.manager.blocked")["target_stage_kind_id"] = (
        "simple_loop.worker"
    )

    error = _find_error(_errors(source), "recovery_policy_source_target_mismatch")

    assert error.declaration_path == (
        "recovery_policies[0].source_recovery_action_ids[0]"
    )
    assert error.context["referrer_path"] == "recovery_policies[0]"
    assert error.context["referenced_id"] == "simple_loop.manager.blocked"
    assert error.context["recovery_stage_kind_id"] == "simple_loop.troubleshooter"


def test_recovery_policy_return_action_must_originate_from_policy_stage() -> None:
    source = _source()
    _action(source, "simple_loop.troubleshooter.resolved")["stage_kind_id"] = (
        "simple_loop.manager"
    )

    error = _find_error(_errors(source), "recovery_policy_action_stage_mismatch")

    assert error.declaration_path == "recovery_policies[0].return_action_ids[0]"
    assert error.context["referrer_path"] == "recovery_policies[0]"
    assert error.context["referenced_id"] == "simple_loop.troubleshooter.resolved"
    assert error.context["recovery_stage_kind_id"] == "simple_loop.troubleshooter"


def test_recovery_policy_quarantine_action_must_originate_from_policy_stage() -> None:
    source = _source()
    _action(source, "simple_loop.troubleshooter.operator_needed")["stage_kind_id"] = (
        "simple_loop.manager"
    )

    error = _find_error(_errors(source), "recovery_policy_action_stage_mismatch")

    assert error.declaration_path == "recovery_policies[0].quarantine_action_ids[0]"
    assert error.context["referrer_path"] == "recovery_policies[0]"
    assert (
        error.context["referenced_id"] == "simple_loop.troubleshooter.operator_needed"
    )
    assert error.context["recovery_stage_kind_id"] == "simple_loop.troubleshooter"


def test_recovery_policy_reset_triggers_must_be_exact_selected_actions() -> None:
    source = _source()
    policy = _policy(source)
    policy["reset_trigger_action_ids"] = (
        *cast(tuple[str, ...], policy["reset_trigger_action_ids"]),
        "simple_loop.manager.packet_ready",
        "example.reset",
    )

    errors = _errors(source)
    duplicate = _find_error(errors, "duplicate_recovery_policy_reset_trigger")
    example = _find_error(errors, "missing_reference")

    assert duplicate.declaration_path == (
        "recovery_policies[0].reset_trigger_action_ids[8]"
    )
    assert duplicate.context["referenced_id"] == "simple_loop.manager.packet_ready"
    assert example.declaration_path == (
        "recovery_policies[0].reset_trigger_action_ids[9]"
    )
    assert example.context["referenced_id"] == "example.reset"


def test_intervention_option_missing_required_field_has_source_path() -> None:
    source = _source()
    option = _valid_resume_option()
    option.pop("policy_id")
    source["intervention_options"] = [option]

    error = _find_error(_errors(source), "missing_intervention_option_field")

    assert error.severity == "error"
    assert error.phase == "semantic_validation"
    assert error.declaration_path == "intervention_options[0].policy_id"
    assert error.context["referrer_path"] == "intervention_options[0]"
    assert error.context["field_name"] == "policy_id"


def test_intervention_option_unknown_field_has_source_path() -> None:
    source = _source()
    option = _valid_resume_option()
    option["payload_schema"] = {"type": "object"}
    source["intervention_options"] = [option]

    error = _find_error(_errors(source), "unknown_intervention_option_field")

    assert error.declaration_path == "intervention_options[0].payload_schema"
    assert error.context["referrer_path"] == "intervention_options[0]"
    assert error.context["field_name"] == "payload_schema"


@pytest.mark.parametrize(
    ("field_name", "bad_value", "expected_code"),
    (
        ("policy_id", "missing.policy", "missing_reference"),
        ("kind", "revise_with_payload", "unsupported_intervention_option_kind"),
        ("kind", "unknown_kind", "unsupported_intervention_option_kind"),
        ("legal_source_state", "ready", "invalid_intervention_option_field"),
        (
            "target_selector",
            "current_default_plan_quarantine",
            "invalid_intervention_option_field",
        ),
        (
            "resume_target_selector",
            "new_work_item",
            "invalid_intervention_option_field",
        ),
        ("supersede_behavior", "keep_active", "invalid_intervention_option_field"),
        ("attempt_effect", "leave_attempt_active", "invalid_intervention_option_field"),
        ("actor_kind", "runtime", "invalid_intervention_option_field"),
    ),
)
def test_invalid_intervention_option_authority_is_refused(
    field_name: str,
    bad_value: object,
    expected_code: str,
) -> None:
    source = _source()
    option = _valid_resume_option()
    option[field_name] = bad_value
    source["intervention_options"] = [option]

    error = _find_error(_errors(source), expected_code)

    assert error.severity == "error"
    assert error.phase == "semantic_validation"
    assert error.declaration_path.startswith("intervention_options[0].")
    assert error.context["referrer_path"] == "intervention_options[0]"


def test_close_intervention_option_requires_close_behavior_not_resume_target() -> None:
    source = _source()
    option = _valid_resume_option()
    option["id"] = "simple_loop.close_lineage"
    option["kind"] = "close_lineage"
    option["resume_target_selector"] = "recorded_source"
    option["close_behavior"] = None
    source["intervention_options"] = [option]

    errors = _errors(source)
    bad_resume = [
        error
        for error in errors
        if error.code == "invalid_intervention_option_field"
        and error.declaration_path == "intervention_options[0].resume_target_selector"
    ]
    missing_close = [
        error
        for error in errors
        if error.code == "missing_intervention_option_field"
        and error.declaration_path == "intervention_options[0].close_behavior"
    ]

    assert bad_resume
    assert missing_close


@pytest.mark.parametrize(
    "field_name",
    (
        "payload_schema_id",
        "target_queue_family_id",
        "target_stage_kind_id",
        "target_graph_node_id",
        "target_runner_binding_id",
    ),
)
def test_revise_intervention_option_requires_payload_schema_and_target_fields(
    field_name: str,
) -> None:
    source = _source()
    option = _valid_revise_option()
    option.pop(field_name)
    source["intervention_options"] = [option]

    error = _find_error(_errors(source), "missing_intervention_option_field")

    assert error.declaration_path == f"intervention_options[0].{field_name}"
    assert error.context["referrer_path"] == "intervention_options[0]"
    assert error.context["field_name"] == field_name


@pytest.mark.parametrize(
    ("field_name", "bad_value", "reference_kind"),
    (
        ("payload_schema_id", "missing.schema", "artifact_schema"),
        ("target_queue_family_id", "missing.queue", "queue_family"),
        ("target_stage_kind_id", "missing.stage", "stage_kind"),
        ("target_graph_node_id", "missing.node", "graph_node"),
        ("target_runner_binding_id", "missing.runner", "runner_binding"),
    ),
)
def test_revise_intervention_option_references_must_resolve_with_source_paths(
    field_name: str,
    bad_value: str,
    reference_kind: str,
) -> None:
    source = _source()
    option = _valid_revise_option()
    option[field_name] = bad_value
    source["intervention_options"] = [option]

    error = _find_error(_errors(source), "missing_reference")

    assert error.declaration_path == f"intervention_options[0].{field_name}"
    assert error.context["referrer_path"] == "intervention_options[0]"
    assert error.context["reference_kind"] == reference_kind
    assert error.context["referenced_id"] == bad_value


def test_revise_intervention_payload_schema_must_match_target_route_schema() -> None:
    source = _source()
    _records(source, "artifact_schemas").append(
        {
            "id": "simple_loop.unused_operator_payload",
            "schema": {
                "type": "object",
                "required": ("artifact_kind",),
                "properties": {
                    "artifact_kind": {"const": "simple_loop.unused_operator_payload"}
                },
            },
            "presentation": {"display_name": "Unused operator payload"},
        }
    )
    option = _valid_revise_option()
    option["payload_schema_id"] = "simple_loop.unused_operator_payload"
    source["intervention_options"] = [option]

    error = _find_error(_errors(source), "intervention_target_payload_schema_mismatch")

    assert error.declaration_path == "intervention_options[0].payload_schema_id"
    assert error.context["payload_schema_id"] == "simple_loop.unused_operator_payload"
    assert error.context["target_payload_schema_id"] == "simple_loop.work_packet"


def test_revise_intervention_graph_node_must_belong_to_target_stage() -> None:
    source = _source()
    option = _valid_revise_option()
    option["target_graph_node_id"] = "simple_loop.reviewer.start"
    source["intervention_options"] = [option]

    error = _find_error(
        _errors(source),
        "intervention_target_graph_node_stage_mismatch",
    )

    assert error.declaration_path == "intervention_options[0].target_graph_node_id"
    assert error.context["referrer_path"] == "intervention_options[0]"
    assert error.context["stage_kind_id"] == "simple_loop.worker"
    assert error.context["target_graph_node_id"] == "simple_loop.reviewer.start"


def test_revise_intervention_target_must_match_declared_route_tuple() -> None:
    source = _source()
    option = _valid_revise_option()
    option["target_graph_node_id"] = "simple_loop.worker.gaps"
    source["intervention_options"] = [option]

    error = _find_error(_errors(source), "intervention_target_route_mismatch")

    assert error.declaration_path == "intervention_options[0].target_graph_node_id"
    assert error.context["referrer_path"] == "intervention_options[0]"
    assert error.context["queue_family_id"] == "work_packet"
    assert error.context["stage_kind_id"] == "simple_loop.worker"
    assert error.context["target_graph_node_id"] == "simple_loop.worker.gaps"
    assert error.context["runner_binding_id"] == "simple_loop.default_agent_runner"


def test_duplicate_artifact_schema_ids_are_refused() -> None:
    source = _source()
    schemas = _records(source, "artifact_schemas")
    duplicate = dict(schemas[0])
    schemas.append(duplicate)

    error = _find_error(_errors(source), "duplicate_id")

    assert error.declaration_path == "artifact_schemas[7].id"
    assert error.related_declaration_path == "artifact_schemas[0].id"
    assert error.context["namespace"] == "artifact_schema"
    assert error.context["duplicate_id"] == "simple_loop.work_prompt"


def test_duplicate_intervention_option_ids_are_refused() -> None:
    source = _source()
    first = _valid_resume_option()
    second = _valid_resume_option()
    source["intervention_options"] = [first, second]

    error = _find_error(_errors(source), "duplicate_id")

    assert error.declaration_path == "intervention_options[1].id"
    assert error.related_declaration_path == "intervention_options[0].id"
    assert error.context["namespace"] == "intervention_option"
    assert error.context["duplicate_id"] == "simple_loop.resume_lineage"


def test_partitionless_stage_still_rejects_missing_references() -> None:
    runner_source = _source()
    _troubleshooter_stage(runner_source)["runner_binding_id"] = "missing.runner"
    runner_error = _find_error(_errors(runner_source), "missing_reference")
    assert runner_error.declaration_path == "stage_kinds[3].runner_binding_id"
    assert runner_error.context["reference_kind"] == "runner_binding"

    asset_source = _source()
    _troubleshooter_stage(asset_source)["asset_ids"] = ("missing.asset",)
    asset_error = _find_error(_errors(asset_source), "missing_reference")
    assert asset_error.declaration_path == "stage_kinds[3].asset_ids[0]"
    assert asset_error.context["reference_kind"] == "asset"

    outcome_source = _source()
    _troubleshooter_stage(outcome_source)["declared_outcome_ids"] = (
        "simple_loop.troubleshooter.missing",
    )
    outcome_error = _find_error(_errors(outcome_source), "missing_reference")
    assert outcome_error.declaration_path == "stage_kinds[3].declared_outcome_ids[0]"
    assert outcome_error.context["reference_kind"] == "terminal_outcome"

    action_source = _source()
    _records(action_source, "terminal_actions")[:] = [
        action
        for action in _records(action_source, "terminal_actions")
        if action["outcome_id"] != "simple_loop.troubleshooter.resolved"
    ]
    action_error = _find_error(_errors(action_source), "outcome_without_action")
    assert action_error.context["stage_kind_id"] == "simple_loop.troubleshooter"
    assert action_error.context["outcome_id"] == "simple_loop.troubleshooter.resolved"


def test_non_null_compatibility_profile_and_required_extensions_are_refused() -> None:
    profile_source = _source()
    _workflow(profile_source)["compatibility_profile"] = "lad_codex"

    profile_error = _find_error(
        _errors(profile_source),
        "unsupported_compatibility_profile",
    )
    assert profile_error.declaration_path == "workflow.compatibility_profile"
    assert profile_error.context["compatibility_profile"] == "lad_codex"

    extension_source = _source()
    _workflow(extension_source)["required_extensions"] = ("some.extension",)

    extension_error = _find_error(
        _errors(extension_source),
        "unsupported_required_extensions",
    )
    assert extension_error.declaration_path == "workflow.required_extensions"
    assert extension_error.context["required_extensions"] == ("some.extension",)


def test_workflow_source_returns_deep_copy_for_mutation_tests() -> None:
    first = simple_loop.workflow_source()
    second = simple_loop.workflow_source()
    assert first == second

    first_records = _records(first, "queue_families")
    first_records[0]["id"] = "mutated"

    assert _records(second, "queue_families")[0]["id"] == "work_prompt"
    assert simple_loop.workflow_source() == second
