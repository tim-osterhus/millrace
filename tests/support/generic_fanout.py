"""Generic selected artifact-fanout scenario for LAD-B routing tests."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace

from millrace.compiler import SelectedRunnerAdapterPolicy, compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts import ArtifactSchemaId, QueueFamilyId, SelectedCompiledPlan
from millrace.contracts.compiled_plan import AuthorityValue
from millrace.contracts.state import RuntimeState
from millrace.contracts.transition import (
    AdmitPlan,
    ClaimWork,
    EnqueueWork,
    InitializeWorkspace,
    RunnerResultObserved,
    SelectDefaultPlan,
    TransitionContext,
    TransitionInput,
)
from millrace.kernel import apply, empty_runtime_state
from millrace.testing import (
    decide_with_fake_runner_completion as decide,
)
from millrace.testing import (
    deterministic_context,
    fake_completed_runner_observation_state,
    fake_runner_observation_payload,
)

PACKET_SCHEMA_ID = "fanout.packet"
CHILD_SCHEMA_ID = "fanout.child"
_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)


def source() -> dict[str, object]:
    return {
        "lineage_policy": "root_from_external_enqueue",
        "workflow": {
            "id": "fanout.workflow",
            "version": "0.1",
            "name": "Fanout Workflow",
            "compatibility_profile": None,
            "required_extensions": (),
        },
        "graphs": [
            {
                "id": "fanout.graph",
                "node_ids": ("fanout.parent.start", "fanout.child.start"),
                "presentation": {"display_name": "Fanout Graph"},
            }
        ],
        "partitions": [
            {
                "id": "primary",
                "kind": "plane",
                "presentation": {"display_name": "Primary"},
            }
        ],
        "queue_families": [
            {
                "id": "parent",
                "external_enqueue": True,
                "presentation": {"display_name": "Parent"},
            },
            {
                "id": "child",
                "external_enqueue": True,
                "presentation": {"display_name": "Child"},
            },
        ],
        "external_enqueue_routes": [
            {
                "id": "parent",
                "queue_family_id": "parent",
                "graph_node_id": "fanout.parent.start",
                "stage_kind_id": "parent_stage",
                "runner_binding_id": "fanout.runner",
            },
            {
                "id": "child",
                "queue_family_id": "child",
                "graph_node_id": "fanout.child.start",
                "stage_kind_id": "child_stage",
                "runner_binding_id": "fanout.runner",
                "payload_schema_id": CHILD_SCHEMA_ID,
            },
        ],
        "artifact_schemas": [
            {
                "id": PACKET_SCHEMA_ID,
                "schema": {
                    "type": "object",
                    "required": ("artifact_kind", "items"),
                    "properties": {
                        "artifact_kind": {"const": PACKET_SCHEMA_ID},
                        "items": {
                            "type": "array",
                            "min_items": 1,
                            "unique_by": "item_id",
                            "items": {
                                "type": "object",
                                "required": ("item_id", "body"),
                                "properties": {
                                    "item_id": {"type": "string", "min_length": 1},
                                    "body": {"type": "string", "min_length": 1},
                                },
                            },
                        },
                    },
                },
                "presentation": {"display_name": "Packet"},
            },
            {
                "id": CHILD_SCHEMA_ID,
                "schema": {
                    "type": "object",
                    "required": ("child_id", "body"),
                    "properties": {
                        "child_id": {"type": "string", "min_length": 1},
                        "body": {"type": "string", "min_length": 1},
                    },
                },
                "presentation": {"display_name": "Child payload"},
            },
        ],
        "assets": [
            {
                "id": "fanout.parent.prompt",
                "kind": "prompt",
                "body": "Produce child work.",
                "presentation": {"display_name": "Parent prompt"},
            },
            {
                "id": "fanout.child.prompt",
                "kind": "prompt",
                "body": "Execute child work.",
                "presentation": {"display_name": "Child prompt"},
            },
        ],
        "stage_kinds": [
            {
                "id": "parent_stage",
                "partition_id": "primary",
                "runner_binding_id": "fanout.runner",
                "input_queue_family_ids": ("parent",),
                "output_queue_family_ids": ("parent",),
                "artifact_schema_ids": (PACKET_SCHEMA_ID,),
                "asset_ids": ("fanout.parent.prompt",),
                "declared_outcome_ids": ("fanout.parent.done",),
                "presentation": {"display_name": "Parent"},
            },
            {
                "id": "child_stage",
                "partition_id": "primary",
                "runner_binding_id": "fanout.runner",
                "input_queue_family_ids": ("child",),
                "output_queue_family_ids": (),
                "artifact_schema_ids": (),
                "asset_ids": ("fanout.child.prompt",),
                "declared_outcome_ids": (),
                "presentation": {"display_name": "Child"},
            },
        ],
        "terminal_outcomes": [
            {
                "id": "fanout.parent.done",
                "stage_kind_id": "parent_stage",
                "marker": "FANOUT_READY",
                "presentation": {"display_name": "Done"},
            }
        ],
        "terminal_actions": [
            {
                "id": "fanout.parent.close",
                "stage_kind_id": "parent_stage",
                "outcome_id": "fanout.parent.done",
                "kind": "complete_work_item",
                "artifact_schema_id": PACKET_SCHEMA_ID,
                "presentation": {"display_name": "Close parent"},
            }
        ],
        "fanout_declarations": [
            {
                "id": "fanout.packet.children",
                "source_action_id": "fanout.parent.close",
                "source_artifact_schema_id": PACKET_SCHEMA_ID,
                "item_source_path": ("items",),
                "item_id_key": "item_id",
                "target_route_id": "child",
                "target_payload_schema_id": CHILD_SCHEMA_ID,
                "target_payload_mapping": {
                    "child_id": ("item_id",),
                    "body": ("body",),
                },
                "duplicate_policy": "refuse",
                "root_lineage_policy": "inherit_source_lineage",
                "dependency_policy": "depends_on_source_work_item",
            }
        ],
        "recovery_policies": (),
        "wait_states": (),
        "counters": (),
        "intervention_options": (),
        "operator_waits": (),
        "runner_bindings": [
            {
                "id": "fanout.runner",
                "adapter_kind": "fake_local",
                "stage_kind_ids": ("parent_stage", "child_stage"),
                "presentation": {"display_name": "Fanout runner"},
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
    }


def source_with_optional_child_note(
    *,
    note_required: bool = False,
    source_note_type: str = "string",
    target_note_type: str = "string",
) -> dict[str, object]:
    workflow_source = deepcopy(source())
    artifact_schemas = workflow_source["artifact_schemas"]
    assert isinstance(artifact_schemas, list)
    packet_schema = next(
        schema for schema in artifact_schemas if schema.get("id") == PACKET_SCHEMA_ID
    )
    child_schema = next(
        schema for schema in artifact_schemas if schema.get("id") == CHILD_SCHEMA_ID
    )
    assert isinstance(packet_schema, dict)
    assert isinstance(child_schema, dict)
    packet_body = packet_schema["schema"]
    child_body = child_schema["schema"]
    assert isinstance(packet_body, dict)
    assert isinstance(child_body, dict)
    packet_properties = packet_body["properties"]
    child_properties = child_body["properties"]
    assert isinstance(packet_properties, dict)
    assert isinstance(child_properties, dict)
    packet_properties["note"] = {"type": source_note_type}
    items_schema = packet_properties["items"]
    assert isinstance(items_schema, dict)
    item_schema = items_schema["items"]
    assert isinstance(item_schema, dict)
    item_properties = item_schema["properties"]
    assert isinstance(item_properties, dict)
    item_properties["note"] = {"type": source_note_type}
    child_properties["note"] = {"type": target_note_type}
    if note_required:
        required = child_body["required"]
        assert isinstance(required, tuple)
        child_body["required"] = (*required, "note")
    fanout_declarations = workflow_source["fanout_declarations"]
    assert isinstance(fanout_declarations, list)
    fanout = fanout_declarations[0]
    assert isinstance(fanout, dict)
    mapping = fanout["target_payload_mapping"]
    assert isinstance(mapping, dict)
    mapping["note"] = ("note",)
    return workflow_source


def compile_fanout(
    workflow_source: Mapping[str, object] | None = None,
) -> tuple[SelectedCompiledPlan, str]:
    result = compile_workflow(
        workflow_source or source(), selected_runner_policy=_CODEX_POLICY
    )
    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ] == []
    assert result.plan is not None
    return result.plan, authority_fingerprint(result.plan)


def plan_with_valid_route_source_action(
    plan: SelectedCompiledPlan,
) -> SelectedCompiledPlan:
    child_route = plan.external_enqueue_routes[1]
    stage_kinds = tuple(
        replace(
            stage,
            artifact_schema_ids=(
                *stage.artifact_schema_ids,
                ArtifactSchemaId(PACKET_SCHEMA_ID),
            ),
        )
        if str(stage.id) == "child_stage"
        else stage
        for stage in plan.stage_kinds
    )
    terminal_action = replace(
        plan.terminal_actions[0],
        action_kind="route",
        target_stage_kind_id=child_route.stage_kind_id,
        target_graph_node_id=child_route.graph_node_id,
        emitted_queue_family_id=child_route.queue_family_id,
        runner_binding_id=child_route.runner_binding_id,
        payload_projection={"kind": "source", "path": ("artifact_payload",)},
    )
    return replace(
        plan,
        stage_kinds=stage_kinds,
        terminal_actions=(terminal_action, *plan.terminal_actions[1:]),
    )


def context(input_id: str) -> TransitionContext:
    suffix = input_id.removeprefix("observe-").removeprefix("claim-")
    return deterministic_context(
        transition_id=f"transition-{input_id}",
        work_item_id=f"work-{suffix}",
        activation_id=f"activation-{suffix}",
        run_id=f"run-{suffix}",
        claim_id=f"claim-{suffix}",
        fencing_token=f"fence-{suffix}",
    )


def packet_payload(
    *,
    item_ids: tuple[str, ...] = ("one", "two"),
    source_note: object = None,
    item_notes: Mapping[str, object] | None = None,
) -> Mapping[str, AuthorityValue]:
    payload: dict[str, object] = {
        "artifact_kind": PACKET_SCHEMA_ID,
        "items": tuple(
            {
                **{"item_id": item_id, "body": f"Body for {item_id}"},
                **(
                    {}
                    if item_notes is None or item_id not in item_notes
                    else {"note": item_notes[item_id]}
                ),
            }
            for item_id in item_ids
        ),
    }
    if source_note is not None:
        payload["note"] = source_note
    return payload


def apply_accepted_input(
    state: RuntimeState,
    transition_input: TransitionInput,
    transition_context: TransitionContext,
) -> RuntimeState:
    if isinstance(transition_input, RunnerResultObserved):
        state, transition_input = fake_completed_runner_observation_state(
            state=state,
            observation=transition_input,
        )
    decision = decide(state, transition_input, transition_context)
    assert decision.accepted is True
    return apply(state, decision)


def parent_closed_state(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    artifact_payload: Mapping[str, AuthorityValue] | None = None,
) -> RuntimeState:
    state = empty_runtime_state()
    for transition_input in (
        InitializeWorkspace("init-fanout"),
        AdmitPlan(
            "admit-fanout",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        SelectDefaultPlan("select-fanout", authority_fingerprint=fingerprint),
        EnqueueWork(
            "enqueue-parent",
            queue_family_id=QueueFamilyId("parent"),
            payload={"body": "Parent input"},
        ),
        ClaimWork("claim-parent", activation_id="activation-enqueue-parent"),
    ):
        state = apply_accepted_input(
            state,
            transition_input,
            context(transition_input.input_id),
        )

    run = state.runs["run-parent"]
    activation = state.activations[run.activation_id]
    observation = RunnerResultObserved(
        "observe-parent-done",
        run_id=run.run_ref.run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=activation,
            plan_fingerprint=fingerprint,
            marker="FANOUT_READY",
            artifact_payload=artifact_payload or packet_payload(),
        ),
        observed_at=None,
    )
    return apply_accepted_input(state, observation, context("observe-parent-done"))
