from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest

from millrace.compiler import SelectedRunnerAdapterPolicy, compile_workflow
from millrace.contracts import QueueFamilyId, RunnerBindingId, StageKindId
from support import lad_learning

Source = dict[str, object]
_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)
Record = dict[str, object]


def _records(source: Source, key: str) -> list[Record]:
    return cast(list[Record], source[key])


def _compile_errors(source: Source):
    return [
        diagnostic
        for diagnostic in compile_workflow(
            source, selected_runner_policy=_CODEX_POLICY
        ).diagnostics
        if diagnostic.severity == "error"
    ]


def _learning_source() -> Source:
    from millrace.workflows import lad_learning as fixture

    return fixture.workflow_source()


def _learning_generated_route(source: Source) -> Record:
    return next(
        route
        for route in _records(source, "generated_work_routes")
        if route["id"] == "learning.trigger.analyst"
    )


def _learning_runner(source: Source) -> Record:
    return next(
        runner
        for runner in _records(source, "runner_bindings")
        if runner["id"] == "learning.standard.local_runner"
    )


def _learning_terminal_action(source: Source, action_id: str) -> Record:
    return next(
        action
        for action in _records(source, "terminal_actions")
        if action["id"] == action_id
    )


def _learning_operator_wait(source: Source, wait_id: str) -> Record:
    return next(
        wait for wait in _records(source, "operator_waits") if wait["id"] == wait_id
    )


def _learning_effect_declaration(source: Source, effect_id: str) -> Record:
    return next(
        effect
        for effect in _records(source, "effect_declarations")
        if effect["id"] == effect_id
    )


def test_learning_standard_selected_authority_closure() -> None:
    plan, _fingerprint = lad_learning.compile_lad_learning()

    assert str(plan.workflow.workflow_id) == "lad.full"
    assert str(plan.workflow.workflow_version) == "0.1"
    graph_ids = {str(graph.id) for graph in plan.graphs}
    assert "learning.standard.graph" in graph_ids
    queue_ids = {queue.id for queue in plan.queue_families}
    assert QueueFamilyId("learning_request") in queue_ids
    routes = {route.id: route for route in plan.external_enqueue_routes}
    assert routes["learning_request"].queue_family_id == QueueFamilyId(
        "learning_request"
    )
    assert routes["learning_request"].graph_node_id == "learning.standard.analyst"
    assert routes["learning_request"].stage_kind_id == StageKindId("analyst")
    assert routes["learning_request"].runner_binding_id == RunnerBindingId(
        "learning.standard.local_runner"
    )

    generated_routes = {route.id: route for route in plan.generated_work_routes}
    assert generated_routes["learning.trigger.analyst"].stage_kind_id == StageKindId(
        "analyst"
    )
    assert generated_routes["learning.trigger.librarian"].stage_kind_id == StageKindId(
        "librarian"
    )
    assert {
        str(stage.id): {str(asset_id) for asset_id in stage.asset_ids}
        for stage in plan.stage_kinds
        if str(stage.id) in {"analyst", "professor", "curator", "librarian"}
    } == {
        "analyst": {
            "learning.entrypoints.analyst",
            "learning.skills.analyst_core",
        },
        "professor": {
            "learning.entrypoints.professor",
            "learning.skills.professor_core",
        },
        "curator": {
            "learning.entrypoints.curator",
            "learning.skills.curator_core",
        },
        "librarian": {
            "learning.entrypoints.librarian",
            "learning.skills.librarian_core",
        },
    }

    fanouts = {str(fanout.id): fanout for fanout in plan.fanout_declarations}
    assert (
        fanouts["learning.trigger.execution.doublechecker_pass"].source_state_policy
        == "accepted_terminal_observation"
    )
    assert (
        fanouts["learning.trigger.planning.planner_complete"].target_route_id
        == "learning.trigger.librarian"
    )
    assert (
        str(fanouts["learning.trigger.execution.needs_planning"].source_action_id)
        == "execution.close_consultant_needs_plan"
    )

    concurrency = {
        str(policy.partition_id): policy for policy in plan.concurrency_policies
    }
    assert set(concurrency) >= {"planning", "execution", "learning"}
    assert concurrency["learning"].max_active_runs == 1
    assert tuple(
        str(item) for item in concurrency["learning"].coexist_partition_ids
    ) == ("planning", "execution")

    blocked_actions = {
        str(action.id): action
        for action in plan.terminal_actions
        if str(action.id).startswith("learning.close_")
        and str(action.id).endswith("_blocked")
    }
    assert {
        action_id: action.action_kind for action_id, action in blocked_actions.items()
    } == {
        "learning.close_analyst_blocked": "operator_wait",
        "learning.close_professor_blocked": "operator_wait",
        "learning.close_curator_blocked": "operator_wait",
        "learning.close_librarian_blocked": "operator_wait",
    }
    waits_by_action = {
        str(source_action_id): wait
        for wait in plan.operator_waits
        for source_action_id in wait.source_action_ids
    }
    assert set(waits_by_action) >= set(blocked_actions)
    for action_id, wait in waits_by_action.items():
        if action_id not in blocked_actions:
            continue
        assert tuple(str(kind) for kind in wait.allowed_resolution_kinds) == (
            "resume_recorded_source",
            "close_recorded_source",
            "revise_recorded_source",
        )
        assert str(wait.payload_schema_id) == lad_learning.LEARNING_REQUEST_SCHEMA_ID
        assert str(wait.target_queue_family_id) == "learning_request"
        assert str(wait.target_stage_kind_id) == "analyst"
        assert wait.target_graph_node_id == "learning.standard.analyst"
        assert str(wait.target_runner_binding_id) == "learning.standard.local_runner"


def test_learning_artifact_schemas_are_selected_authority() -> None:
    plan, _fingerprint = lad_learning.compile_lad_learning()

    schema_ids = {str(schema.id) for schema in plan.artifact_schemas}
    assert {
        lad_learning.LEARNING_RESEARCH_PACKET_SCHEMA_ID,
        lad_learning.LEARNING_SKILL_CANDIDATE_SCHEMA_ID,
        lad_learning.LEARNING_PROFESSOR_NOTES_SCHEMA_ID,
        lad_learning.LEARNING_SKILL_UPDATE_SCHEMA_ID,
        lad_learning.LEARNING_CURATOR_DECISION_SCHEMA_ID,
        lad_learning.LEARNING_SKILL_INSTALL_REPORT_SCHEMA_ID,
        lad_learning.LEARNING_REPORT_SCHEMA_ID,
    }.issubset(schema_ids)

    stages = {str(stage.id): stage for stage in plan.stage_kinds}
    assert {str(schema_id) for schema_id in stages["analyst"].artifact_schema_ids} >= {
        lad_learning.LEARNING_RESEARCH_PACKET_SCHEMA_ID,
        lad_learning.LEARNING_REPORT_SCHEMA_ID,
    }
    assert all(
        lad_learning.LEARNING_STAGE_RESULT_SCHEMA_ID
        not in {str(schema_id) for schema_id in stage.artifact_schema_ids}
        for stage_id, stage in stages.items()
        if stage_id in {"analyst", "professor", "curator", "librarian"}
    )
    professor_schema_ids = {
        str(schema_id) for schema_id in stages["professor"].artifact_schema_ids
    }
    assert professor_schema_ids >= {
        lad_learning.LEARNING_RESEARCH_PACKET_SCHEMA_ID,
        lad_learning.LEARNING_SKILL_CANDIDATE_SCHEMA_ID,
        lad_learning.LEARNING_PROFESSOR_NOTES_SCHEMA_ID,
        lad_learning.LEARNING_REPORT_SCHEMA_ID,
    }
    assert {str(schema_id) for schema_id in stages["curator"].artifact_schema_ids} >= {
        lad_learning.LEARNING_SKILL_CANDIDATE_SCHEMA_ID,
        lad_learning.LEARNING_SKILL_UPDATE_SCHEMA_ID,
        lad_learning.LEARNING_CURATOR_DECISION_SCHEMA_ID,
        lad_learning.LEARNING_REPORT_SCHEMA_ID,
    }
    librarian_schema_ids = {
        str(schema_id) for schema_id in stages["librarian"].artifact_schema_ids
    }
    assert librarian_schema_ids >= {
        lad_learning.LEARNING_SKILL_INSTALL_REPORT_SCHEMA_ID,
        lad_learning.LEARNING_REPORT_SCHEMA_ID,
    }

    actions = {str(action.id): action for action in plan.terminal_actions}
    assert str(actions["learning.route_analyst_complete"].artifact_schema_id) == (
        lad_learning.LEARNING_RESEARCH_PACKET_SCHEMA_ID
    )
    assert str(actions["learning.close_analyst_noop"].artifact_schema_id) == (
        lad_learning.LEARNING_RESEARCH_PACKET_SCHEMA_ID
    )
    assert str(actions["learning.route_professor_complete"].artifact_schema_id) == (
        lad_learning.LEARNING_SKILL_CANDIDATE_SCHEMA_ID
    )
    assert str(actions["learning.close_professor_noop"].artifact_schema_id) == (
        lad_learning.LEARNING_PROFESSOR_NOTES_SCHEMA_ID
    )
    assert str(actions["learning.close_curator_complete"].artifact_schema_id) == (
        lad_learning.LEARNING_SKILL_UPDATE_SCHEMA_ID
    )
    assert str(actions["learning.close_curator_noop"].artifact_schema_id) == (
        lad_learning.LEARNING_CURATOR_DECISION_SCHEMA_ID
    )
    assert str(actions["learning.close_librarian_complete"].artifact_schema_id) == (
        lad_learning.LEARNING_SKILL_INSTALL_REPORT_SCHEMA_ID
    )
    assert str(actions["learning.close_librarian_noop"].artifact_schema_id) == (
        lad_learning.LEARNING_SKILL_INSTALL_REPORT_SCHEMA_ID
    )
    assert {
        str(actions[f"learning.close_{stage_id}_blocked"].artifact_schema_id)
        for stage_id in ("analyst", "professor", "curator", "librarian")
    } == {lad_learning.LEARNING_REPORT_SCHEMA_ID}


def test_learning_effect_declarations_are_selected_authority() -> None:
    plan, _fingerprint = lad_learning.compile_lad_learning()

    effects = {
        str(effect.effect_declaration_id): effect for effect in plan.effect_declarations
    }

    assert set(effects) == {
        lad_learning.CURATOR_EFFECT_DECLARATION_ID,
        lad_learning.LIBRARIAN_EFFECT_DECLARATION_ID,
    }
    assert (
        str(effects[lad_learning.CURATOR_EFFECT_DECLARATION_ID].terminal_action_id)
        == "learning.close_curator_complete"
    )
    assert (
        str(effects[lad_learning.CURATOR_EFFECT_DECLARATION_ID].artifact_schema_id)
        == lad_learning.LEARNING_SKILL_UPDATE_SCHEMA_ID
    )
    assert (
        str(effects[lad_learning.LIBRARIAN_EFFECT_DECLARATION_ID].terminal_action_id)
        == "learning.close_librarian_complete"
    )
    assert (
        str(effects[lad_learning.LIBRARIAN_EFFECT_DECLARATION_ID].artifact_schema_id)
        == lad_learning.LEARNING_SKILL_INSTALL_REPORT_SCHEMA_ID
    )
    assert {effect.provider_ref for effect in effects.values()} == {
        lad_learning.FAKE_LOCAL_EFFECT_PROVIDER_REF
    }
    assert {effect.capability_policy_ref for effect in effects.values()} == {
        lad_learning.FAKE_LOCAL_EFFECT_CAPABILITY_POLICY_REF
    }
    assert all(
        effect.allowed_reconciliation_statuses == ("applied", "no_op", "refused")
        and effect.real_side_effects_allowed is False
        and effect.target_ref_kind.startswith("workspace_")
        and effect.target_ref_schema
        for effect in effects.values()
    )


@pytest.mark.parametrize(
    ("field_name", "field_value", "diagnostic_context"),
    (
        (
            "provider_ref",
            "provider.real.network",
            {
                "field_name": "provider_ref",
                "effect_declaration_id": lad_learning.CURATOR_EFFECT_DECLARATION_ID,
            },
        ),
        (
            "capability_policy_ref",
            "policy.real.install",
            {
                "field_name": "capability_policy_ref",
                "effect_declaration_id": lad_learning.CURATOR_EFFECT_DECLARATION_ID,
            },
        ),
        (
            "real_side_effects_allowed",
            True,
            {
                "field_name": "real_side_effects_allowed",
                "effect_declaration_id": lad_learning.CURATOR_EFFECT_DECLARATION_ID,
            },
        ),
        (
            "allowed_reconciliation_statuses",
            ("applied", "refused"),
            {
                "field_name": "allowed_reconciliation_statuses",
                "effect_declaration_id": lad_learning.CURATOR_EFFECT_DECLARATION_ID,
            },
        ),
    ),
)
def test_learning_effect_diagnostics_reject_invalid_provider_policy_and_real_side_effects(  # noqa: E501
    field_name: str,
    field_value: object,
    diagnostic_context: dict[str, object],
) -> None:
    source = _learning_source()
    effect = _learning_effect_declaration(
        source,
        lad_learning.CURATOR_EFFECT_DECLARATION_ID,
    )
    effect[field_name] = field_value

    errors = _compile_errors(source)

    error = next(
        diagnostic
        for diagnostic in errors
        if diagnostic.code == "invalid_effect_declaration"
    )
    assert error.declaration_path.endswith(f".{field_name}")
    for key, value in diagnostic_context.items():
        assert error.context[key] == value


@pytest.mark.parametrize(
    ("field_name", "field_value", "path_suffix"),
    (
        ("terminal_action_id", "learning.close_curator_noop", ".terminal_action_id"),
        (
            "artifact_schema_id",
            lad_learning.LEARNING_CURATOR_DECISION_SCHEMA_ID,
            ".artifact_schema_id",
        ),
    ),
)
def test_learning_effect_diagnostics_reject_terminal_action_drift(
    field_name: str,
    field_value: object,
    path_suffix: str,
) -> None:
    source = _learning_source()
    effect = _learning_effect_declaration(
        source,
        lad_learning.CURATOR_EFFECT_DECLARATION_ID,
    )
    effect[field_name] = field_value

    errors = _compile_errors(source)

    error = next(
        diagnostic
        for diagnostic in errors
        if diagnostic.code == "invalid_effect_declaration"
        and diagnostic.declaration_path.endswith(path_suffix)
    )
    assert error.context["effect_declaration_id"] == (
        lad_learning.CURATOR_EFFECT_DECLARATION_ID
    )
    assert error.context["terminal_action_id"] == (
        field_value
        if field_name == "terminal_action_id"
        else "learning.close_curator_complete"
    )


def test_learning_effect_diagnostics_reject_duplicate_terminal_action_binding() -> None:
    source = _learning_source()
    effects = _records(source, "effect_declarations")
    duplicate = dict(
        _learning_effect_declaration(
            source,
            lad_learning.LIBRARIAN_EFFECT_DECLARATION_ID,
        )
    )
    duplicate["id"] = "learning.effect.duplicate.curator"
    duplicate["terminal_action_id"] = "learning.close_curator_complete"
    duplicate["artifact_schema_id"] = lad_learning.LEARNING_SKILL_UPDATE_SCHEMA_ID
    duplicate["target_ref_kind"] = "workspace_skill_update"
    duplicate["target_ref_schema"] = "learning.effects.target.workspace_skill_update.v1"
    source["effect_declarations"] = (*effects, duplicate)

    errors = _compile_errors(source)

    error = next(
        diagnostic
        for diagnostic in errors
        if diagnostic.code == "invalid_effect_declaration"
        and diagnostic.declaration_path.endswith(".terminal_action_id")
        and diagnostic.context["effect_declaration_id"]
        == "learning.effect.duplicate.curator"
    )
    assert error.context["terminal_action_id"] == "learning.close_curator_complete"


@pytest.mark.parametrize("field_name", ("target_ref_kind", "target_ref_schema"))
@pytest.mark.parametrize("delete_field", (False, True))
def test_learning_effect_diagnostics_reject_missing_or_blank_target_refs(
    field_name: str,
    delete_field: bool,
) -> None:
    source = _learning_source()
    effect = _learning_effect_declaration(
        source,
        lad_learning.CURATOR_EFFECT_DECLARATION_ID,
    )
    if delete_field:
        del effect[field_name]
    else:
        effect[field_name] = ""

    errors = _compile_errors(source)

    error = next(
        diagnostic
        for diagnostic in errors
        if diagnostic.code == "invalid_effect_declaration"
        and diagnostic.declaration_path.endswith(f".{field_name}")
    )
    assert error.context["field_name"] == field_name


def test_learning_artifact_action_diagnostics_reject_unselected_schema() -> None:
    source = _learning_source()
    action = _learning_terminal_action(source, "learning.route_analyst_complete")
    action["artifact_schema_id"] = "learning.artifacts.missing"

    errors = _compile_errors(source)

    error = next(
        diagnostic
        for diagnostic in errors
        if diagnostic.declaration_path.endswith(".artifact_schema_id")
    )
    assert error.code == "missing_reference"
    assert error.context["reference_kind"] == "artifact_schema"
    assert error.context["referenced_id"] == "learning.artifacts.missing"


@pytest.mark.parametrize("schema_owner_stage_id", ("analyst", "professor"))
def test_learning_artifact_action_diagnostics_reject_route_schema_contract_drift(
    schema_owner_stage_id: str,
) -> None:
    source = _learning_source()
    schema_id = f"learning.artifacts.{schema_owner_stage_id}_only_route_probe"
    _records(source, "artifact_schemas").append(
        {
            "id": schema_id,
            "schema": {
                "type": "object",
                "required": ("artifact_kind", "summary"),
                "properties": {
                    "artifact_kind": {"const": schema_id},
                    "summary": {"type": "string", "min_length": 1},
                },
            },
            "presentation": {"display_name": "Learning route contract probe"},
        }
    )
    schema_owner = next(
        stage
        for stage in _records(source, "stage_kinds")
        if stage["id"] == schema_owner_stage_id
    )
    schema_owner["artifact_schema_ids"] = (
        *cast(tuple[str, ...], schema_owner["artifact_schema_ids"]),
        schema_id,
    )
    action = next(
        item
        for item in _records(source, "terminal_actions")
        if item["id"] == "learning.route_analyst_complete"
    )
    action["artifact_schema_id"] = schema_id

    errors = _compile_errors(source)

    error = next(
        diagnostic
        for diagnostic in errors
        if diagnostic.code == "terminal_route_artifact_schema_mismatch"
    )
    assert error.declaration_path.endswith(".artifact_schema_id")
    assert error.context["action_id"] == "learning.route_analyst_complete"
    assert error.context["source_stage_kind_id"] == "analyst"
    assert error.context["target_stage_kind_id"] == "professor"
    assert error.context["artifact_schema_id"] == schema_id


@pytest.mark.parametrize(
    ("action_id", "diagnostic_code"),
    (
        (
            "learning.route_analyst_complete",
            "terminal_route_artifact_schema_mismatch",
        ),
        (
            "learning.close_analyst_noop",
            "terminal_action_artifact_schema_mismatch",
        ),
    ),
)
def test_learning_artifact_action_diagnostics_reject_stage_result_reselection(
    action_id: str,
    diagnostic_code: str,
) -> None:
    source = _learning_source()
    action = _learning_terminal_action(source, action_id)
    action["artifact_schema_id"] = lad_learning.LEARNING_STAGE_RESULT_SCHEMA_ID

    errors = _compile_errors(source)

    error = next(
        diagnostic
        for diagnostic in errors
        if diagnostic.code == diagnostic_code
        and diagnostic.declaration_path.endswith(".artifact_schema_id")
    )
    assert error.context["action_id"] == action_id
    assert error.context["artifact_schema_id"] == (
        lad_learning.LEARNING_STAGE_RESULT_SCHEMA_ID
    )


def test_learning_route_action_diagnostics_reject_missing_target_graph_node() -> None:
    source = _learning_source()
    action = _learning_terminal_action(source, "learning.route_analyst_complete")
    action.pop("target_graph_node_id")

    errors = _compile_errors(source)

    error = next(
        diagnostic
        for diagnostic in errors
        if diagnostic.code == "terminal_route_missing_field"
        and diagnostic.declaration_path.endswith(".target_graph_node_id")
    )
    assert error.context["action_id"] == "learning.route_analyst_complete"
    assert error.context["field_name"] == "target_graph_node_id"


@pytest.mark.parametrize(
    ("target_graph_node_id", "owner_stage_id"),
    (
        ("learning.standard.analyst", "analyst"),
        ("learning.standard.librarian", "librarian"),
    ),
)
def test_learning_route_action_diagnostics_reject_target_graph_node_owner_drift(
    target_graph_node_id: str,
    owner_stage_id: str,
) -> None:
    source = _learning_source()
    action = _learning_terminal_action(source, "learning.route_analyst_complete")
    action["target_graph_node_id"] = target_graph_node_id

    errors = _compile_errors(source)

    error = next(
        diagnostic
        for diagnostic in errors
        if diagnostic.code == "terminal_route_graph_node_stage_mismatch"
    )
    assert error.declaration_path.endswith(".target_graph_node_id")
    assert error.context["action_id"] == "learning.route_analyst_complete"
    assert error.context["target_stage_kind_id"] == "professor"
    assert error.context["target_graph_node_id"] == target_graph_node_id
    assert error.context["graph_node_stage_kind_id"] == owner_stage_id


@pytest.mark.parametrize(
    ("mutate", "diagnostic_code", "context"),
    (
        (
            lambda source: next(
                stage
                for stage in _records(source, "stage_kinds")
                if stage["id"] == "professor"
            ).__setitem__("input_queue_family_ids", ()),
            "terminal_route_stage_input_mismatch",
            {"target_stage_kind_id": "professor", "queue_family_id": "stage_result"},
        ),
        (
            lambda source: _learning_terminal_action(
                source,
                "learning.route_analyst_complete",
            ).__setitem__("runner_binding_id", "planning.lad.local_runner"),
            "terminal_route_stage_runner_mismatch",
            {
                "route_runner_binding_id": "planning.lad.local_runner",
                "stage_runner_binding_id": "learning.standard.local_runner",
            },
        ),
        (
            lambda source: _learning_runner(source).__setitem__(
                "stage_kind_ids",
                ("analyst", "curator", "librarian"),
            ),
            "terminal_route_runner_stage_mismatch",
            {
                "runner_binding_id": "learning.standard.local_runner",
                "target_stage_kind_id": "professor",
            },
        ),
    ),
)
def test_learning_route_action_diagnostics_reject_remaining_contract_drift(
    mutate: Callable[[Source], object],
    diagnostic_code: str,
    context: dict[str, object],
) -> None:
    source = _learning_source()
    mutate(source)

    errors = _compile_errors(source)

    error = next(
        diagnostic for diagnostic in errors if diagnostic.code == diagnostic_code
    )
    assert error.context["action_id"] == "learning.route_analyst_complete"
    for key, value in context.items():
        assert error.context[key] == value


def test_learning_trigger_target_diagnostics_reject_invalid_target_authority() -> None:
    source = _learning_source()
    generated_route = next(
        route
        for route in _records(source, "generated_work_routes")
        if route["id"] == "learning.trigger.librarian"
    )
    generated_route["payload_schema_id"] = "learning.artifacts.stage_result"

    errors = _compile_errors(source)

    error = next(
        diagnostic
        for diagnostic in errors
        if diagnostic.declaration_path.endswith(".target_route_id")
    )
    assert error.code == "invalid_fanout_declaration"
    assert error.context["reason"] == "target_route_contract_mismatch"


def test_generated_route_diagnostics_reject_missing_queue_family() -> None:
    source = _learning_source()
    generated_route = _learning_generated_route(source)
    generated_route["queue_family_id"] = "missing-learning-queue"

    errors = _compile_errors(source)

    error = next(
        diagnostic
        for diagnostic in errors
        if diagnostic.declaration_path.endswith(".queue_family_id")
    )
    assert error.code == "missing_reference"
    assert error.context["reference_kind"] == "queue_family"


def test_generated_route_diagnostics_reject_external_route_id_ambiguity() -> None:
    source = _learning_source()
    generated_route = _learning_generated_route(source)
    generated_route["id"] = "learning_request"

    errors = _compile_errors(source)

    error = next(
        diagnostic
        for diagnostic in errors
        if diagnostic.code == "ambiguous_selected_enqueue_route"
    )
    assert error.declaration_path.endswith(".id")
    assert error.context["route_id"] == "learning_request"


@pytest.mark.parametrize(
    ("mutate", "diagnostic_code", "path_suffix", "context"),
    (
        (
            lambda source: _learning_generated_route(source).__setitem__(
                "graph_node_id",
                "missing.learning.node",
            ),
            "missing_reference",
            ".graph_node_id",
            {"reference_kind": "graph_node", "referenced_id": "missing.learning.node"},
        ),
        (
            lambda source: _learning_generated_route(source).__setitem__(
                "stage_kind_id",
                "missing_learning_stage",
            ),
            "missing_reference",
            ".stage_kind_id",
            {"reference_kind": "stage_kind", "referenced_id": "missing_learning_stage"},
        ),
        (
            lambda source: _learning_generated_route(source).__setitem__(
                "payload_schema_id",
                "missing.learning.payload",
            ),
            "missing_reference",
            ".payload_schema_id",
            {
                "reference_kind": "artifact_schema",
                "referenced_id": "missing.learning.payload",
            },
        ),
        (
            lambda source: _learning_generated_route(source).update(
                {
                    "stage_kind_id": "professor",
                    "graph_node_id": "learning.standard.professor",
                }
            ),
            "generated_work_route_stage_input_mismatch",
            ".queue_family_id",
            {"queue_family_id": "learning_request", "stage_kind_id": "professor"},
        ),
        (
            lambda source: _learning_generated_route(source).__setitem__(
                "runner_binding_id",
                "planning.lad.local_runner",
            ),
            "generated_work_route_stage_runner_mismatch",
            ".runner_binding_id",
            {
                "route_runner_binding_id": "planning.lad.local_runner",
                "stage_runner_binding_id": "learning.standard.local_runner",
            },
        ),
        (
            lambda source: _learning_runner(source).__setitem__(
                "stage_kind_ids",
                ("professor", "curator", "librarian"),
            ),
            "generated_work_route_runner_stage_mismatch",
            ".runner_binding_id",
            {
                "runner_binding_id": "learning.standard.local_runner",
                "stage_kind_id": "analyst",
            },
        ),
    ),
)
def test_generated_route_diagnostics_reject_structural_corruption(
    mutate: Callable[[Source], object],
    diagnostic_code: str,
    path_suffix: str,
    context: dict[str, object],
) -> None:
    source = _learning_source()
    mutate(source)

    errors = _compile_errors(source)

    error = next(
        diagnostic
        for diagnostic in errors
        if diagnostic.code == diagnostic_code
        and diagnostic.declaration_path.endswith(path_suffix)
    )
    for key, value in context.items():
        assert error.context[key] == value


@pytest.mark.parametrize(
    ("mutate", "reason"),
    (
        (
            lambda source: _records(source, "concurrency_policies")[2].__setitem__(
                "coexist_partition_ids",
                ("learning",),
            ),
            "self_coexist",
        ),
        (
            lambda source: _records(source, "concurrency_policies")[2].__setitem__(
                "coexist_partition_ids",
                ("planning", "planning"),
            ),
            "duplicate_coexist_partition",
        ),
        (
            lambda source: _records(source, "concurrency_policies")[0].__setitem__(
                "coexist_partition_ids",
                (),
            ),
            "asymmetric_coexist",
        ),
    ),
)
def test_learning_concurrency_diagnostics_reject_policy_shape_corruption(
    mutate: Callable[[Source], object],
    reason: str,
) -> None:
    source = _learning_source()
    mutate(source)

    errors = _compile_errors(source)

    error = next(
        diagnostic
        for diagnostic in errors
        if diagnostic.code == "invalid_concurrency_policy"
        and diagnostic.context.get("reason") == reason
    )
    assert error.context["reason"] == reason


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    (
        ("target_stage_kind_id", "analyst"),
        ("target_graph_node_id", "learning.standard.analyst"),
        ("emitted_queue_family_id", "learning_request"),
        ("runner_binding_id", "learning.standard.local_runner"),
        ("payload_projection", {"kind": "source", "path": ("artifact_payload",)}),
    ),
)
def test_needs_planning_trigger_diagnostics_reject_route_bearing_close_action(
    field_name: str,
    field_value: object,
) -> None:
    from millrace.workflows import lad_learning as fixture

    source = fixture.workflow_source()
    action = next(
        item
        for item in _records(source, "terminal_actions")
        if item["id"] == "execution.close_consultant_needs_plan"
    )
    action[field_name] = field_value

    errors = _compile_errors(source)

    assert any(
        diagnostic.code == "terminal_close_with_escalation_route_authority"
        for diagnostic in errors
    )


def test_learning_concurrency_diagnostics_reject_invalid_policy_refs() -> None:
    from millrace.workflows import lad_learning as fixture

    source = fixture.workflow_source()
    policy = _records(source, "concurrency_policies")[0]
    policy["coexist_partition_ids"] = ("missing",)

    errors = _compile_errors(source)

    error = next(
        diagnostic
        for diagnostic in errors
        if diagnostic.declaration_path.endswith(".coexist_partition_ids[0]")
    )
    assert error.code == "missing_reference"
    assert error.context["reference_kind"] == "partition"


def test_learning_recovery_intervention_diagnostics_reject_invalid_policy() -> None:
    source = _learning_source()
    wait = _learning_operator_wait(source, "learning.analyst_blocked_wait")
    wait["allowed_resolution_kinds"] = (
        "resume_recorded_source",
        "delegate_to_learning",
    )
    wait["audit_metadata_requirements"] = ()

    errors = _compile_errors(source)

    error = next(
        diagnostic
        for diagnostic in errors
        if diagnostic.code == "invalid_operator_wait_field"
        and diagnostic.context["field_name"] == "allowed_resolution_kinds"
    )
    assert error.context["operator_wait_id"] == "learning.analyst_blocked_wait"
    assert error.context["value"] == "delegate_to_learning"


def test_learning_recovery_intervention_diagnostics_reject_wrong_stage_or_queue_refs() -> (  # noqa: E501
    None
):
    source = _learning_source()
    wait = _learning_operator_wait(source, "learning.analyst_blocked_wait")
    wait["target_queue_family_id"] = "stage_result"

    errors = _compile_errors(source)

    error = next(
        diagnostic
        for diagnostic in errors
        if diagnostic.code == "intervention_target_route_mismatch"
        and diagnostic.context["referrer_path"].startswith("operator_waits[")
    )
    assert error.context["queue_family_id"] == "stage_result"
    assert error.context["stage_kind_id"] == "analyst"
    assert error.context["target_graph_node_id"] == "learning.standard.analyst"


def test_learning_revise_intervention_requires_learning_request_schema() -> None:
    source = _learning_source()
    wait = _learning_operator_wait(source, "learning.analyst_blocked_wait")
    wait["payload_schema_id"] = lad_learning.LEARNING_REPORT_SCHEMA_ID

    errors = _compile_errors(source)

    error = next(
        diagnostic
        for diagnostic in errors
        if diagnostic.code == "intervention_target_payload_schema_mismatch"
    )
    assert error.context["payload_schema_id"] == lad_learning.LEARNING_REPORT_SCHEMA_ID
    assert error.context["target_payload_schema_id"] == (
        lad_learning.LEARNING_REQUEST_SCHEMA_ID
    )


def test_learning_blocked_operator_wait_diagnostics_reject_block_work_item_or_recovery_policy_mismatch() -> (  # noqa: E501
    None
):
    source = _learning_source()
    action = _learning_terminal_action(source, "learning.close_analyst_blocked")
    action["kind"] = "block_work_item"

    errors = _compile_errors(source)

    error = next(
        diagnostic
        for diagnostic in errors
        if diagnostic.code == "invalid_operator_wait_action_kind"
    )
    assert error.context["action_id"] == "learning.close_analyst_blocked"
    assert error.context["action_kind"] == "block_work_item"
