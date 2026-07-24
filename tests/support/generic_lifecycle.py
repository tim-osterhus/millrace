"""Generic selected lifecycle fixture for ORCH-0001 tests."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from typing import Protocol

from millrace.compiler import SelectedRunnerAdapterPolicy, compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts import QueueFamilyId, SelectedCompiledPlan
from millrace.contracts.compiled_plan import AuthorityValue
from millrace.contracts.ids import ActionId, FanoutId, RunnerBindingId, StageKindId
from millrace.contracts.state import (
    Activation,
    ActivationRouteRecord,
    FanoutRecord,
    RuntimeState,
    WorkItem,
    WorkItemRef,
)
from millrace.contracts.transition import (
    AdmitPlan,
    ClaimWork,
    EnqueueWork,
    FanoutFromArtifact,
    InitializeWorkspace,
    JoinFromArtifact,
    RunnerResultObserved,
    SelectDefaultPlan,
    TransitionContext,
    TransitionInput,
    artifact_payload_digest,
)
from millrace.kernel import apply, decide, empty_runtime_state
from millrace.testing import deterministic_context, fake_runner_observation_payload

SOURCE_SCHEMA_ID = "LifecycleSourceBundle"
_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)
ALPHA_REPORT_SCHEMA_ID = "LifecycleAlphaReport"
BETA_REPORT_SCHEMA_ID = "LifecycleBetaReport"
FANOUT_ALPHA_ID = "lifecycle.alpha_fanout"
FANOUT_BETA_ID = "lifecycle.beta_fanout"
JOIN_ID = "lifecycle.report_join"


class _LifecycleCandidate(Protocol):
    @property
    def transition_input(self) -> FanoutFromArtifact | JoinFromArtifact: ...

    @property
    def transition_context(self) -> TransitionContext: ...


def source() -> dict[str, object]:
    return {
        "lineage_policy": "root_from_external_enqueue",
        "workflow": {
            "id": "lifecycle_probe",
            "version": "0.1",
            "name": "Lifecycle Probe",
            "compatibility_profile": None,
            "required_extensions": (),
        },
        "graphs": [
            {
                "id": "lifecycle.graph",
                "node_ids": (
                    "lifecycle.origin.start",
                    "lifecycle.alpha.start",
                    "lifecycle.beta.start",
                    "lifecycle.review.start",
                ),
                "presentation": {"display_name": "Lifecycle Graph"},
            }
        ],
        "partitions": [
            {
                "id": "primary",
                "kind": "lane",
                "presentation": {"display_name": "Primary"},
            }
        ],
        "queue_families": [
            {
                "id": "origin",
                "external_enqueue": True,
                "presentation": {"display_name": "Origin"},
            },
            {
                "id": "alpha_branch",
                "external_enqueue": False,
                "presentation": {"display_name": "Alpha"},
            },
            {
                "id": "beta_branch",
                "external_enqueue": False,
                "presentation": {"display_name": "Beta"},
            },
            {
                "id": "joined_bundle",
                "external_enqueue": False,
                "presentation": {"display_name": "Joined"},
            },
        ],
        "external_enqueue_routes": [
            {
                "id": "route.origin",
                "queue_family_id": "origin",
                "graph_node_id": "lifecycle.origin.start",
                "stage_kind_id": "origin_stage",
                "runner_binding_id": "lifecycle.runner",
                "payload_schema_id": SOURCE_SCHEMA_ID,
            }
        ],
        "generated_work_routes": [
            {
                "id": "route.alpha",
                "queue_family_id": "alpha_branch",
                "graph_node_id": "lifecycle.alpha.start",
                "stage_kind_id": "alpha_stage",
                "runner_binding_id": "lifecycle.runner",
                "payload_schema_id": SOURCE_SCHEMA_ID,
            },
            {
                "id": "route.beta",
                "queue_family_id": "beta_branch",
                "graph_node_id": "lifecycle.beta.start",
                "stage_kind_id": "beta_stage",
                "runner_binding_id": "lifecycle.runner",
                "payload_schema_id": SOURCE_SCHEMA_ID,
            },
            {
                "id": "route.review",
                "queue_family_id": "joined_bundle",
                "graph_node_id": "lifecycle.review.start",
                "stage_kind_id": "review_stage",
                "runner_binding_id": "lifecycle.runner",
                "payload_schema_id": SOURCE_SCHEMA_ID,
            },
        ],
        "artifact_schemas": [
            {
                "id": SOURCE_SCHEMA_ID,
                "schema": {
                    "type": "object",
                    "required": ("bundle_id", "items"),
                    "properties": {
                        "bundle_id": {"type": "string", "min_length": 1},
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
                "presentation": {"display_name": "Source bundle"},
            },
            {
                "id": ALPHA_REPORT_SCHEMA_ID,
                "schema": _report_schema("alpha"),
                "presentation": {"display_name": "Alpha report"},
            },
            {
                "id": BETA_REPORT_SCHEMA_ID,
                "schema": _report_schema("beta"),
                "presentation": {"display_name": "Beta report"},
            },
        ],
        "assets": [
            {
                "id": "asset.origin",
                "kind": "prompt",
                "body": "Produce a source bundle.",
                "presentation": {"display_name": "Origin asset"},
            },
            {
                "id": "asset.alpha",
                "kind": "prompt",
                "body": "Produce alpha evidence.",
                "presentation": {"display_name": "Alpha asset"},
            },
            {
                "id": "asset.beta",
                "kind": "prompt",
                "body": "Produce beta evidence.",
                "presentation": {"display_name": "Beta asset"},
            },
            {
                "id": "asset.review",
                "kind": "prompt",
                "body": "Review joined evidence.",
                "presentation": {"display_name": "Review asset"},
            },
        ],
        "stage_kinds": [
            _stage(
                "origin_stage",
                inputs=("origin",),
                outputs=("origin", "alpha_branch", "beta_branch"),
                schemas=(SOURCE_SCHEMA_ID,),
                assets=("asset.origin",),
                outcomes=("lifecycle.origin.ready",),
            ),
            _stage(
                "alpha_stage",
                inputs=("alpha_branch",),
                outputs=(),
                schemas=(ALPHA_REPORT_SCHEMA_ID,),
                assets=("asset.alpha",),
                outcomes=("lifecycle.alpha.ready",),
            ),
            _stage(
                "beta_stage",
                inputs=("beta_branch",),
                outputs=(),
                schemas=(BETA_REPORT_SCHEMA_ID,),
                assets=("asset.beta",),
                outcomes=("lifecycle.beta.ready",),
            ),
            _stage(
                "review_stage",
                inputs=("joined_bundle",),
                outputs=(),
                schemas=(
                    SOURCE_SCHEMA_ID,
                    ALPHA_REPORT_SCHEMA_ID,
                    BETA_REPORT_SCHEMA_ID,
                ),
                assets=("asset.review",),
                outcomes=(),
            ),
        ],
        "terminal_outcomes": [
            {
                "id": "lifecycle.origin.ready",
                "stage_kind_id": "origin_stage",
                "marker": "SOURCE_READY",
                "presentation": {"display_name": "Source ready"},
            },
            {
                "id": "lifecycle.alpha.ready",
                "stage_kind_id": "alpha_stage",
                "marker": "ALPHA_READY",
                "presentation": {"display_name": "Alpha ready"},
            },
            {
                "id": "lifecycle.beta.ready",
                "stage_kind_id": "beta_stage",
                "marker": "BETA_READY",
                "presentation": {"display_name": "Beta ready"},
            },
        ],
        "terminal_actions": [
            _artifact_action(
                "lifecycle.origin.complete",
                stage_kind_id="origin_stage",
                outcome_id="lifecycle.origin.ready",
                artifact_schema_id=SOURCE_SCHEMA_ID,
            ),
            _artifact_action(
                "lifecycle.alpha.complete",
                stage_kind_id="alpha_stage",
                outcome_id="lifecycle.alpha.ready",
                artifact_schema_id=ALPHA_REPORT_SCHEMA_ID,
            ),
            _artifact_action(
                "lifecycle.beta.complete",
                stage_kind_id="beta_stage",
                outcome_id="lifecycle.beta.ready",
                artifact_schema_id=BETA_REPORT_SCHEMA_ID,
            ),
        ],
        "fanout_declarations": [
            _fanout(FANOUT_ALPHA_ID, target_route_id="route.alpha"),
            _fanout(FANOUT_BETA_ID, target_route_id="route.beta"),
        ],
        "join_declarations": [
            {
                "id": JOIN_ID,
                "target_stage_kind_id": "review_stage",
                "correlation_key": "bundle_id",
                "required_artifact_schema_ids": (
                    ALPHA_REPORT_SCHEMA_ID,
                    BETA_REPORT_SCHEMA_ID,
                ),
                "missing_policy": "wait",
            }
        ],
        "runner_bindings": [
            {
                "id": "lifecycle.runner",
                "adapter_kind": "codex",
                "stage_kind_ids": (
                    "origin_stage",
                    "alpha_stage",
                    "beta_stage",
                    "review_stage",
                ),
                "presentation": {"display_name": "Lifecycle runner"},
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


def source_with_reversed_fanouts() -> dict[str, object]:
    workflow_source = source()
    fanouts = workflow_source["fanout_declarations"]
    assert isinstance(fanouts, list)
    workflow_source["fanout_declarations"] = list(reversed(fanouts))
    return workflow_source


def source_with_optional_item_collection() -> dict[str, object]:
    workflow_source = source()
    schemas = workflow_source["artifact_schemas"]
    assert isinstance(schemas, list)
    source_schema = next(
        schema for schema in schemas if schema["id"] == SOURCE_SCHEMA_ID
    )
    schema = source_schema["schema"]
    assert isinstance(schema, dict)
    schema["required"] = ("bundle_id",)
    return workflow_source


def source_with_optional_note_mapping() -> dict[str, object]:
    workflow_source = deepcopy(source())
    artifact_schemas = workflow_source["artifact_schemas"]
    assert isinstance(artifact_schemas, list)
    source_schema = next(
        schema for schema in artifact_schemas if schema.get("id") == SOURCE_SCHEMA_ID
    )
    assert isinstance(source_schema, dict)
    schema_body = source_schema["schema"]
    assert isinstance(schema_body, dict)
    properties = schema_body["properties"]
    assert isinstance(properties, dict)
    properties["note"] = {"type": "string"}
    fanout_declarations = workflow_source["fanout_declarations"]
    assert isinstance(fanout_declarations, list)
    for fanout in fanout_declarations:
        assert isinstance(fanout, dict)
        mapping = fanout["target_payload_mapping"]
        assert isinstance(mapping, dict)
        mapping["note"] = ("note",)
    return workflow_source


def source_with_version(version: str) -> dict[str, object]:
    workflow_source = source()
    workflow = workflow_source["workflow"]
    assert isinstance(workflow, dict)
    workflow_source["workflow"] = {**workflow, "version": version}
    return workflow_source


def source_with_unrelated_side_stage() -> dict[str, object]:
    workflow_source = source()

    graphs = workflow_source["graphs"]
    assert isinstance(graphs, list)
    graph = graphs[0]
    assert isinstance(graph, dict)
    node_ids = graph["node_ids"]
    assert isinstance(node_ids, tuple)
    graph["node_ids"] = (*node_ids, "lifecycle.side.start")

    queue_families = workflow_source["queue_families"]
    assert isinstance(queue_families, list)
    queue_families.append(
        {
            "id": "side_branch",
            "external_enqueue": True,
            "presentation": {"display_name": "Side"},
        }
    )

    external_routes = workflow_source["external_enqueue_routes"]
    assert isinstance(external_routes, list)
    external_routes.append(
        {
            "id": "route.side",
            "queue_family_id": "side_branch",
            "graph_node_id": "lifecycle.side.start",
            "stage_kind_id": "side_stage",
            "runner_binding_id": "lifecycle.runner",
            "payload_schema_id": ALPHA_REPORT_SCHEMA_ID,
        }
    )

    assets = workflow_source["assets"]
    assert isinstance(assets, list)
    assets.append(
        {
            "id": "asset.side",
            "kind": "prompt",
            "body": "Produce unrelated side evidence.",
            "presentation": {"display_name": "Side asset"},
        }
    )

    stage_kinds = workflow_source["stage_kinds"]
    assert isinstance(stage_kinds, list)
    stage_kinds.append(
        _stage(
            "side_stage",
            inputs=("side_branch",),
            outputs=(),
            schemas=(ALPHA_REPORT_SCHEMA_ID,),
            assets=("asset.side",),
            outcomes=("lifecycle.side.ready",),
        )
    )

    terminal_outcomes = workflow_source["terminal_outcomes"]
    assert isinstance(terminal_outcomes, list)
    terminal_outcomes.append(
        {
            "id": "lifecycle.side.ready",
            "stage_kind_id": "side_stage",
            "marker": "SIDE_READY",
            "presentation": {"display_name": "Side ready"},
        }
    )

    terminal_actions = workflow_source["terminal_actions"]
    assert isinstance(terminal_actions, list)
    terminal_actions.append(
        _artifact_action(
            "lifecycle.side.complete",
            stage_kind_id="side_stage",
            outcome_id="lifecycle.side.ready",
            artifact_schema_id=ALPHA_REPORT_SCHEMA_ID,
        )
    )

    runner_bindings = workflow_source["runner_bindings"]
    assert isinstance(runner_bindings, list)
    runner_binding = runner_bindings[0]
    assert isinstance(runner_binding, dict)
    stage_kind_ids = runner_binding["stage_kind_ids"]
    assert isinstance(stage_kind_ids, tuple)
    runner_binding["stage_kind_ids"] = (*stage_kind_ids, "side_stage")
    return workflow_source


def source_with_routed_alpha_report() -> dict[str, object]:
    workflow_source = source()
    stage_kinds = workflow_source["stage_kinds"]
    assert isinstance(stage_kinds, list)
    alpha_stage = next(
        stage for stage in stage_kinds if stage.get("id") == "alpha_stage"
    )
    alpha_stage["output_queue_family_ids"] = ("joined_bundle",)

    terminal_actions = workflow_source["terminal_actions"]
    assert isinstance(terminal_actions, list)
    alpha_action = next(
        action
        for action in terminal_actions
        if action.get("id") == "lifecycle.alpha.complete"
    )
    alpha_action.update(
        {
            "kind": "route",
            "target_stage_kind_id": "review_stage",
            "target_graph_node_id": "lifecycle.review.start",
            "emitted_queue_family_id": "joined_bundle",
            "runner_binding_id": "lifecycle.runner",
            "payload_projection": {
                "kind": "source",
                "path": ("artifact_payload",),
            },
        }
    )
    return workflow_source


def source_with_alternative_alpha_report_action() -> dict[str, object]:
    workflow_source = source()
    stage_kinds = workflow_source["stage_kinds"]
    assert isinstance(stage_kinds, list)
    alpha_stage = next(
        stage for stage in stage_kinds if stage.get("id") == "alpha_stage"
    )
    alpha_stage["artifact_schema_ids"] = (
        ALPHA_REPORT_SCHEMA_ID,
        BETA_REPORT_SCHEMA_ID,
    )
    alpha_stage["declared_outcome_ids"] = (
        "lifecycle.alpha.ready",
        "lifecycle.alpha.beta_ready",
    )

    terminal_outcomes = workflow_source["terminal_outcomes"]
    assert isinstance(terminal_outcomes, list)
    terminal_outcomes.append(
        {
            "id": "lifecycle.alpha.beta_ready",
            "stage_kind_id": "alpha_stage",
            "marker": "ALPHA_BETA_READY",
            "presentation": {"display_name": "Alpha beta ready"},
        }
    )

    terminal_actions = workflow_source["terminal_actions"]
    assert isinstance(terminal_actions, list)
    terminal_actions.append(
        _artifact_action(
            "lifecycle.alpha.complete_as_beta",
            stage_kind_id="alpha_stage",
            outcome_id="lifecycle.alpha.beta_ready",
            artifact_schema_id=BETA_REPORT_SCHEMA_ID,
        )
    )
    return workflow_source


def _report_schema(kind: str) -> Mapping[str, object]:
    return {
        "type": "object",
        "required": ("bundle_id", "report_kind", "verdict"),
        "properties": {
            "bundle_id": {"type": "string", "min_length": 1},
            "report_kind": {"const": kind},
            "verdict": {"type": "string", "min_length": 1},
        },
    }


def _stage(
    stage_id: str,
    *,
    inputs: tuple[str, ...],
    outputs: tuple[str, ...],
    schemas: tuple[str, ...],
    assets: tuple[str, ...],
    outcomes: tuple[str, ...],
) -> dict[str, object]:
    return {
        "id": stage_id,
        "partition_id": "primary",
        "runner_binding_id": "lifecycle.runner",
        "input_queue_family_ids": inputs,
        "output_queue_family_ids": outputs,
        "artifact_schema_ids": schemas,
        "asset_ids": assets,
        "declared_outcome_ids": outcomes,
        "presentation": {"display_name": stage_id},
    }


def _artifact_action(
    action_id: str,
    *,
    stage_kind_id: str,
    outcome_id: str,
    artifact_schema_id: str,
) -> dict[str, object]:
    return {
        "id": action_id,
        "stage_kind_id": stage_kind_id,
        "outcome_id": outcome_id,
        "kind": "complete_work_item",
        "artifact_schema_id": artifact_schema_id,
        "presentation": {"display_name": action_id},
    }


def _fanout(fanout_id: str, *, target_route_id: str) -> dict[str, object]:
    route_queue = "alpha_branch" if target_route_id.endswith("alpha") else "beta_branch"
    route_stage = "alpha_stage" if target_route_id.endswith("alpha") else "beta_stage"
    route_node = (
        "lifecycle.alpha.start"
        if target_route_id.endswith("alpha")
        else "lifecycle.beta.start"
    )
    return {
        "id": fanout_id,
        "source_action_id": "lifecycle.origin.complete",
        "source_artifact_schema_id": SOURCE_SCHEMA_ID,
        "item_source_path": ("items",),
        "item_id_key": "item_id",
        "target_route_id": target_route_id,
        "target_queue_family_id": route_queue,
        "target_stage_kind_id": route_stage,
        "target_graph_node_id": route_node,
        "target_runner_binding_id": "lifecycle.runner",
        "target_payload_schema_id": SOURCE_SCHEMA_ID,
        "target_payload_mapping": {
            "bundle_id": ("bundle_id",),
            "items": ("items",),
        },
        "duplicate_policy": "refuse",
        "root_lineage_policy": "inherit_source_lineage",
        "dependency_policy": "depends_on_source_work_item",
    }


def compile_lifecycle(
    workflow_source: Mapping[str, object] | None = None,
) -> tuple[SelectedCompiledPlan, str]:
    result = compile_workflow(
        workflow_source or source(), selected_runner_policy=_CODEX_POLICY
    )
    errors = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ]
    assert errors == []
    assert result.plan is not None
    return result.plan, authority_fingerprint(result.plan)


def context(
    input_id: str,
    *,
    work_item_id: str | None = None,
    activation_id: str | None = None,
    run_id: str | None = None,
    claim_id: str | None = None,
    fencing_token: str | None = None,
) -> TransitionContext:
    suffix = (
        input_id.removeprefix("observe-")
        .removeprefix("claim-")
        .removeprefix("enqueue-")
        .removeprefix("fanout-")
        .removeprefix("join-")
    )
    return deterministic_context(
        transition_id=f"transition-{input_id}",
        work_item_id=work_item_id or f"work-{suffix}",
        activation_id=activation_id or f"activation-{suffix}",
        run_id=run_id or f"run-{suffix}",
        claim_id=claim_id or f"claim-{suffix}",
        fencing_token=fencing_token or f"fence-{suffix}",
    )


def source_payload(bundle_id: str = "bundle-a") -> Mapping[str, AuthorityValue]:
    return {
        "bundle_id": bundle_id,
        "items": (
            {"item_id": "one", "body": "One"},
            {"item_id": "two", "body": "Two"},
        ),
    }


def report_payload(
    kind: str, bundle_id: str = "bundle-a"
) -> Mapping[str, AuthorityValue]:
    return {
        "bundle_id": bundle_id,
        "report_kind": kind,
        "verdict": f"{kind} accepted",
    }


def apply_accepted_input(
    state: RuntimeState,
    transition_input: TransitionInput,
    transition_context: TransitionContext,
) -> RuntimeState:
    decision = decide(state, transition_input, transition_context)
    assert decision.accepted is True, decision.refusal
    return apply(state, decision)


def admitted_state(
    *,
    plan: SelectedCompiledPlan | None = None,
    fingerprint: str | None = None,
) -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    if plan is None or fingerprint is None:
        plan, fingerprint = compile_lifecycle()
    state = empty_runtime_state()
    for transition_input in (
        InitializeWorkspace("init-lifecycle"),
        AdmitPlan(
            "admit-lifecycle",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        SelectDefaultPlan("select-lifecycle", authority_fingerprint=fingerprint),
    ):
        state = apply_accepted_input(
            state,
            transition_input,
            context(transition_input.input_id),
        )
    return state, plan, fingerprint


def origin_claimed_state() -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    state, plan, fingerprint = admitted_state()
    state = enqueue_origin(state)
    state = claim_activation(
        state,
        activation_id="activation-origin",
        suffix="origin",
    )
    return state, plan, fingerprint


def enqueue_origin(
    state: RuntimeState, *, input_id: str = "enqueue-origin"
) -> RuntimeState:
    return apply_accepted_input(
        state,
        EnqueueWork(
            input_id,
            queue_family_id=QueueFamilyId("origin"),
            payload=source_payload(),
        ),
        context(
            input_id,
            work_item_id="work-origin" if input_id == "enqueue-origin" else None,
            activation_id="activation-origin" if input_id == "enqueue-origin" else None,
        ),
    )


def claim_activation(
    state: RuntimeState,
    *,
    activation_id: str,
    suffix: str,
) -> RuntimeState:
    return apply_accepted_input(
        state,
        ClaimWork(f"claim-{suffix}", activation_id=activation_id),
        context(
            f"claim-{suffix}",
            run_id=f"run-{suffix}",
            claim_id=f"claim-{suffix}",
            fencing_token=f"fence-{suffix}",
        ),
    )


def origin_closed_state() -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    state, plan, fingerprint = origin_claimed_state()
    return (
        apply_observation(
            state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-origin",
            input_id="observe-origin",
            marker="SOURCE_READY",
            artifact_payload=source_payload(),
        ),
        plan,
        fingerprint,
    )


def origin_closed_with_admitted_plan_ref_drift() -> tuple[
    RuntimeState,
    SelectedCompiledPlan,
    str,
]:
    state, plan, fingerprint = origin_closed_state()
    source_work = state.work_items["work-origin"]
    source_activation = state.activations["activation-origin"]
    source_run = state.runs["run-origin"]
    drifted_ref = replace(
        state.admitted_plans[fingerprint].plan_ref,
        plan_id="lifecycle_probe:drifted-namespace",
    )
    return (
        replace(
            state,
            work_items={
                **state.work_items,
                source_work.ref.work_item_id: replace(
                    source_work,
                    ref=replace(source_work.ref, plan_ref=drifted_ref),
                ),
            },
            activations={
                **state.activations,
                source_activation.activation_id: replace(
                    source_activation,
                    plan_ref=drifted_ref,
                ),
            },
            runs={
                **state.runs,
                source_run.run_ref.run_id: replace(
                    source_run,
                    run_ref=replace(source_run.run_ref, plan_ref=drifted_ref),
                ),
            },
        ),
        plan,
        fingerprint,
    )


def origin_closed_state_from_source(
    workflow_source: Mapping[str, object],
) -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    plan, fingerprint = compile_lifecycle(workflow_source)
    state, plan, fingerprint = admitted_state(plan=plan, fingerprint=fingerprint)
    state = enqueue_origin(state)
    state = claim_activation(state, activation_id="activation-origin", suffix="origin")
    return (
        apply_observation(
            state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-origin",
            input_id="observe-origin",
            marker="SOURCE_READY",
            artifact_payload=source_payload(),
        ),
        plan,
        fingerprint,
    )


def unrelated_side_report_state() -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    plan, fingerprint = compile_lifecycle(source_with_unrelated_side_stage())
    state, plan, fingerprint = admitted_state(plan=plan, fingerprint=fingerprint)
    payload = report_payload("alpha", bundle_id="bundle-side")
    state = apply_accepted_input(
        state,
        EnqueueWork(
            "enqueue-side",
            queue_family_id=QueueFamilyId("side_branch"),
            payload=payload,
        ),
        context(
            "enqueue-side",
            work_item_id="work-side",
            activation_id="activation-side",
        ),
    )
    state = claim_activation(state, activation_id="activation-side", suffix="side")
    state = apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-side",
        input_id="observe-side",
        marker="SIDE_READY",
        artifact_payload=payload,
    )
    return state, plan, fingerprint


def two_plan_origin_closed_state() -> tuple[
    RuntimeState,
    SelectedCompiledPlan,
    str,
    SelectedCompiledPlan,
    str,
]:
    first_plan, first_fingerprint = compile_lifecycle(source_with_version("0.1"))
    second_plan, second_fingerprint = compile_lifecycle(source_with_version("0.2"))
    state = empty_runtime_state()
    state = apply_accepted_input(
        state,
        InitializeWorkspace("init-lifecycle"),
        context("init-lifecycle"),
    )
    for plan, fingerprint, suffix in (
        (first_plan, first_fingerprint, "origin"),
        (second_plan, second_fingerprint, "second-origin"),
    ):
        state = apply_accepted_input(
            state,
            AdmitPlan(
                f"admit-{suffix}",
                selected_plan=plan,
                authority_fingerprint=fingerprint,
            ),
            context(f"admit-{suffix}"),
        )
        state = apply_accepted_input(
            state,
            SelectDefaultPlan(
                f"select-{suffix}",
                authority_fingerprint=fingerprint,
            ),
            context(f"select-{suffix}"),
        )
        state = enqueue_origin(state, input_id=f"enqueue-{suffix}")
        state = claim_activation(
            state,
            activation_id=f"activation-{suffix}",
            suffix=suffix,
        )
        state = apply_observation(
            state,
            plan=plan,
            fingerprint=fingerprint,
            run_id=f"run-{suffix}",
            input_id=f"observe-{suffix}",
            marker="SOURCE_READY",
            artifact_payload=source_payload(),
        )
    return state, first_plan, first_fingerprint, second_plan, second_fingerprint


def origin_closed_with_ready_activation_state() -> tuple[
    RuntimeState,
    SelectedCompiledPlan,
    str,
]:
    state, plan, fingerprint = origin_closed_state()
    return enqueue_origin(state, input_id="enqueue-other-origin"), plan, fingerprint


def accepted_terminal_fanout_state() -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    plan, fingerprint = compile_lifecycle()
    plan = replace(
        plan,
        fanout_declarations=tuple(
            replace(
                fanout,
                source_state_policy="accepted_terminal_observation",
                dependency_policy="none",
            )
            for fanout in plan.fanout_declarations
        ),
    )
    fingerprint = authority_fingerprint(plan)
    state, plan, fingerprint = admitted_state(plan=plan, fingerprint=fingerprint)
    state = enqueue_origin(state)
    state = claim_activation(state, activation_id="activation-origin", suffix="origin")
    return (
        apply_observation(
            state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-origin",
            input_id="observe-origin",
            marker="SOURCE_READY",
            artifact_payload=source_payload(),
        ),
        plan,
        fingerprint,
    )


def accepted_terminal_optional_omission_fanout_state() -> tuple[
    RuntimeState,
    SelectedCompiledPlan,
    str,
]:
    plan, fingerprint = compile_lifecycle(source_with_optional_note_mapping())
    plan = replace(
        plan,
        fanout_declarations=tuple(
            replace(
                fanout,
                source_state_policy="accepted_terminal_observation",
                dependency_policy="none",
            )
            for fanout in plan.fanout_declarations
        ),
    )
    fingerprint = authority_fingerprint(plan)
    state, plan, fingerprint = admitted_state(plan=plan, fingerprint=fingerprint)
    state = enqueue_origin(state)
    state = claim_activation(state, activation_id="activation-origin", suffix="origin")
    return (
        apply_observation(
            state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-origin",
            input_id="observe-origin",
            marker="SOURCE_READY",
            artifact_payload=source_payload(),
        ),
        plan,
        fingerprint,
    )


def accepted_terminal_optional_collection_omission_state() -> tuple[
    RuntimeState,
    SelectedCompiledPlan,
    str,
]:
    state, plan, fingerprint = accepted_terminal_optional_collection_claimed_state()
    return (
        apply_observation(
            state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-origin",
            input_id="observe-origin",
            marker="SOURCE_READY",
            artifact_payload={"bundle_id": "bundle-a"},
        ),
        plan,
        fingerprint,
    )


def accepted_terminal_optional_collection_claimed_state() -> tuple[
    RuntimeState,
    SelectedCompiledPlan,
    str,
]:
    return _optional_collection_claimed_state(
        source_state_policy="accepted_terminal_observation"
    )


def source_closed_optional_collection_omission_state() -> tuple[
    RuntimeState,
    SelectedCompiledPlan,
    str,
]:
    state, plan, fingerprint = _optional_collection_claimed_state(
        source_state_policy="source_closed"
    )
    return (
        apply_observation(
            state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-origin",
            input_id="observe-origin",
            marker="SOURCE_READY",
            artifact_payload={"bundle_id": "bundle-a"},
        ),
        plan,
        fingerprint,
    )


def optional_collection_with_fanout_aftermath_state(
    *,
    source_state_policy: str,
) -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    state, plan, fingerprint = _optional_collection_claimed_state(
        source_state_policy=source_state_policy
    )
    state = apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-origin",
        input_id="observe-origin",
        marker="SOURCE_READY",
        artifact_payload=source_payload(),
    )
    if source_state_policy == "source_closed":
        state = apply_fanout(state, FANOUT_ALPHA_ID, input_id="fanout-alpha")
        state = apply_fanout(state, FANOUT_BETA_ID, input_id="fanout-beta")
    return state, plan, fingerprint


def _optional_collection_claimed_state(
    *,
    source_state_policy: str,
) -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    plan, fingerprint = compile_lifecycle(source_with_optional_item_collection())
    plan = replace(
        plan,
        fanout_declarations=tuple(
            replace(
                fanout,
                source_state_policy=source_state_policy,
                dependency_policy=(
                    "none"
                    if source_state_policy == "accepted_terminal_observation"
                    else fanout.dependency_policy
                ),
            )
            for fanout in plan.fanout_declarations
        ),
    )
    fingerprint = authority_fingerprint(plan)
    state, plan, fingerprint = admitted_state(plan=plan, fingerprint=fingerprint)
    state = enqueue_origin(state)
    return (
        claim_activation(state, activation_id="activation-origin", suffix="origin"),
        plan,
        fingerprint,
    )


def optional_omission_first_fanout_state() -> tuple[
    RuntimeState,
    SelectedCompiledPlan,
    str,
]:
    plan, fingerprint = compile_lifecycle(source_with_optional_note_mapping())
    state, plan, fingerprint = admitted_state(plan=plan, fingerprint=fingerprint)
    state = enqueue_origin(state)
    state = claim_activation(state, activation_id="activation-origin", suffix="origin")
    state = apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-origin",
        input_id="observe-origin",
        marker="SOURCE_READY",
        artifact_payload=source_payload(),
    )
    return (
        apply_fanout(state, FANOUT_ALPHA_ID, input_id="fanout-alpha-optional"),
        plan,
        fingerprint,
    )


def apply_observation(
    state: RuntimeState,
    *,
    plan: SelectedCompiledPlan,
    fingerprint: str,
    run_id: str,
    input_id: str,
    marker: str,
    artifact_payload: Mapping[str, AuthorityValue],
) -> RuntimeState:
    run = state.runs[run_id]
    activation = state.activations[run.activation_id]
    observation = RunnerResultObserved(
        input_id,
        run_id=run.run_ref.run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=activation,
            plan_fingerprint=fingerprint,
            marker=marker,
            artifact_payload=artifact_payload,
        ),
        observed_at=None,
    )
    return apply_accepted_input(state, observation, context(input_id))


def source_artifact_id() -> str:
    return "transition-observe-origin:artifact"


def apply_candidate(
    state: RuntimeState,
    candidate: _LifecycleCandidate,
) -> RuntimeState:
    decision = decide(state, candidate.transition_input, candidate.transition_context)
    assert decision.accepted is True, decision.refusal
    return apply(state, decision)


def apply_fanout(
    state: RuntimeState,
    fanout_id: str,
    *,
    input_id: str,
    artifact_id: str | None = None,
) -> RuntimeState:
    return apply_accepted_input(
        state,
        FanoutFromArtifact(
            input_id,
            fanout_id=fanout_id,
            source_artifact_id=artifact_id or source_artifact_id(),
        ),
        context(input_id),
    )


def two_complete_fanouts_state() -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    state, plan, fingerprint = origin_closed_state()
    state = apply_fanout(state, FANOUT_ALPHA_ID, input_id="fanout-alpha")
    state = apply_fanout(state, FANOUT_BETA_ID, input_id="fanout-beta")
    return state, plan, fingerprint


def fanout_records_for(
    state: RuntimeState,
    fanout_id: str,
) -> tuple[FanoutRecord, ...]:
    return tuple(
        sorted(
            (
                record
                for record in state.fanout_records.values()
                if str(record.fanout_id) == fanout_id
            ),
            key=lambda record: record.item_key,
        )
    )


def with_fanout_item_creator(
    state: RuntimeState,
    *,
    fanout_id: str,
    item_index: int,
    creator_input_id: str,
) -> RuntimeState:
    record = fanout_records_for(state, fanout_id)[item_index]
    target_work = state.work_items[record.target_work_item_id]
    target_activation = state.activations[record.target_activation_id]
    dependency = next(
        candidate
        for candidate in state.work_dependencies.values()
        if candidate.fanout_record_id == record.record_id
    )
    return replace(
        state,
        fanout_records={
            **state.fanout_records,
            record.record_id: replace(
                record,
                created_by_input_id=creator_input_id,
            ),
        },
        work_items={
            **state.work_items,
            target_work.ref.work_item_id: replace(
                target_work,
                created_by_input_id=creator_input_id,
            ),
        },
        activations={
            **state.activations,
            target_activation.activation_id: replace(
                target_activation,
                created_by_input_id=creator_input_id,
            ),
        },
        activation_routes=tuple(
            replace(route, created_by_input_id=creator_input_id)
            if route.target_work_item_id == record.target_work_item_id
            and route.target_activation_id == record.target_activation_id
            else route
            for route in state.activation_routes
        ),
        work_dependencies={
            **state.work_dependencies,
            dependency.dependency_id: replace(
                dependency,
                created_by_input_id=creator_input_id,
            ),
        },
    )


def with_all_fanout_item_creators(
    state: RuntimeState,
    *,
    fanout_id: str,
    creator_input_id: str,
) -> RuntimeState:
    for item_index, _record in enumerate(fanout_records_for(state, fanout_id)):
        state = with_fanout_item_creator(
            state,
            fanout_id=fanout_id,
            item_index=item_index,
            creator_input_id=creator_input_id,
        )
    return state


def without_fanout_aftermath(
    state: RuntimeState,
    *,
    fanout_id: str,
) -> RuntimeState:
    records = fanout_records_for(state, fanout_id)
    record_ids = {record.record_id for record in records}
    target_work_ids = {record.target_work_item_id for record in records}
    target_activation_ids = {record.target_activation_id for record in records}
    return replace(
        state,
        fanout_records={
            record_id: record
            for record_id, record in state.fanout_records.items()
            if record_id not in record_ids
        },
        work_dependencies={
            dependency_id: dependency
            for dependency_id, dependency in state.work_dependencies.items()
            if dependency.fanout_record_id not in record_ids
        },
        activation_routes=tuple(
            route
            for route in state.activation_routes
            if route.target_work_item_id not in target_work_ids
            and route.target_activation_id not in target_activation_ids
        ),
        activations={
            activation_id: activation
            for activation_id, activation in state.activations.items()
            if activation_id not in target_activation_ids
        },
        work_items={
            work_item_id: work_item
            for work_item_id, work_item in state.work_items.items()
            if work_item_id not in target_work_ids
        },
    )


def without_fanout_item_aftermath(
    state: RuntimeState,
    *,
    fanout_id: str,
    item_index: int,
) -> RuntimeState:
    record = fanout_records_for(state, fanout_id)[item_index]
    return replace(
        state,
        fanout_records={
            record_id: candidate
            for record_id, candidate in state.fanout_records.items()
            if record_id != record.record_id
        },
        work_dependencies={
            dependency_id: dependency
            for dependency_id, dependency in state.work_dependencies.items()
            if dependency.fanout_record_id != record.record_id
        },
        activation_routes=tuple(
            route
            for route in state.activation_routes
            if route.target_work_item_id != record.target_work_item_id
            and route.target_activation_id != record.target_activation_id
        ),
        activations={
            activation_id: activation
            for activation_id, activation in state.activations.items()
            if activation_id != record.target_activation_id
        },
        work_items={
            work_item_id: work_item
            for work_item_id, work_item in state.work_items.items()
            if work_item_id != record.target_work_item_id
        },
    )


def with_fanout_route_record_id(
    state: RuntimeState,
    *,
    fanout_id: str,
    item_index: int,
    record_id: str,
) -> RuntimeState:
    fanout_record = fanout_records_for(state, fanout_id)[item_index]
    return replace(
        state,
        activation_routes=tuple(
            replace(route, record_id=record_id)
            if route.target_work_item_id == fanout_record.target_work_item_id
            and route.target_activation_id == fanout_record.target_activation_id
            else route
            for route in state.activation_routes
        ),
    )


def with_receipt_transition_id(
    state: RuntimeState,
    *,
    input_id: str,
    transition_id: str,
) -> RuntimeState:
    receipt = state.receipts[input_id]
    return replace(
        state,
        receipts={
            **state.receipts,
            input_id: replace(receipt, transition_id=transition_id),
        },
    )


def with_receipt_payload_digest(
    state: RuntimeState,
    *,
    input_id: str,
    payload_digest: str = (
        "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    ),
) -> RuntimeState:
    receipt = state.receipts[input_id]
    return replace(
        state,
        receipts={
            **state.receipts,
            input_id: replace(
                receipt,
                receipt_ref=replace(
                    receipt.receipt_ref,
                    input_payload_digest=payload_digest,
                ),
            ),
        },
    )


def without_governance_event(state: RuntimeState, *, input_id: str) -> RuntimeState:
    return replace(
        state,
        governance_events=tuple(
            event for event in state.governance_events if event.input_id != input_id
        ),
    )


def without_trace(state: RuntimeState, *, input_id: str) -> RuntimeState:
    return replace(
        state,
        traces=tuple(trace for trace in state.traces if trace.input_id != input_id),
    )


def with_governance_event_record_id(
    state: RuntimeState,
    *,
    input_id: str,
    record_id: str,
) -> RuntimeState:
    return replace(
        state,
        governance_events=tuple(
            replace(event, record_id=record_id) if event.input_id == input_id else event
            for event in state.governance_events
        ),
    )


def with_trace_record_id(
    state: RuntimeState,
    *,
    input_id: str,
    record_id: str,
) -> RuntimeState:
    return replace(
        state,
        traces=tuple(
            replace(trace, record_id=record_id) if trace.input_id == input_id else trace
            for trace in state.traces
        ),
    )


def with_extra_fanout_creator_work(state: RuntimeState) -> RuntimeState:
    record = fanout_records_for(state, FANOUT_ALPHA_ID)[0]
    source = state.work_items[record.target_work_item_id]
    extra = replace(
        source,
        ref=replace(source.ref, work_item_id="generated-work:extra-creator-output"),
    )
    return replace(
        state,
        work_items={**state.work_items, extra.ref.work_item_id: extra},
    )


def with_all_fanout_records_declaration_drift(state: RuntimeState) -> RuntimeState:
    return replace(
        state,
        fanout_records={
            record_id: replace(record, fanout_id=FanoutId(FANOUT_BETA_ID))
            if str(record.fanout_id) == FANOUT_ALPHA_ID
            else record
            for record_id, record in state.fanout_records.items()
        },
    )


def fanout_integrity_state(case: str) -> RuntimeState:
    if case == "accepted_terminal_wrong_creator_kind":
        state, _plan, _fingerprint = accepted_terminal_fanout_state()
        return replace(
            state,
            transitions=tuple(
                replace(
                    transition,
                    input_kind=FanoutFromArtifact.input_kind,
                )
                if transition.input_id == "observe-origin"
                else transition
                for transition in state.transitions
            ),
        )

    state, _plan, _fingerprint = two_complete_fanouts_state()
    if case == "split_item_creators":
        return with_fanout_item_creator(
            state,
            fanout_id=FANOUT_ALPHA_ID,
            item_index=1,
            creator_input_id="fanout-beta",
        )
    if case == "non_fanout_creator":
        return with_all_fanout_item_creators(
            state,
            fanout_id=FANOUT_ALPHA_ID,
            creator_input_id="observe-origin",
        )
    if case == "missing_aftermath":
        return without_fanout_aftermath(state, fanout_id=FANOUT_ALPHA_ID)
    if case == "partial_item_aftermath":
        return without_fanout_item_aftermath(
            state,
            fanout_id=FANOUT_ALPHA_ID,
            item_index=1,
        )
    if case == "route_record_id":
        return with_fanout_route_record_id(
            state,
            fanout_id=FANOUT_ALPHA_ID,
            item_index=0,
            record_id="drifted-generated-route",
        )
    if case == "receipt_transition_id":
        return with_receipt_transition_id(
            state,
            input_id="fanout-alpha",
            transition_id="wrong-transition",
        )
    if case == "receipt_payload_digest":
        return with_receipt_payload_digest(state, input_id="fanout-alpha")
    if case == "missing_event":
        return without_governance_event(state, input_id="fanout-alpha")
    if case == "drifted_event":
        return with_governance_event_record_id(
            state,
            input_id="fanout-alpha",
            record_id="drifted-fanout-governance",
        )
    if case == "missing_trace":
        return without_trace(state, input_id="fanout-alpha")
    if case == "drifted_trace":
        return with_trace_record_id(
            state,
            input_id="fanout-alpha",
            record_id="drifted-fanout-trace",
        )
    if case == "extra_creator_output":
        return with_extra_fanout_creator_work(state)
    if case == "all_records_declaration_drift":
        return with_all_fanout_records_declaration_drift(state)
    if case == "missing_aftermath_transition_kind":
        missing = without_fanout_aftermath(state, fanout_id=FANOUT_ALPHA_ID)
        return replace(
            missing,
            transitions=tuple(
                replace(
                    transition,
                    input_kind=RunnerResultObserved.input_kind,
                )
                if transition.input_id == "fanout-alpha"
                else transition
                for transition in missing.transitions
            ),
        )
    raise AssertionError(f"unknown fanout integrity case: {case}")


def branch_activation_id(state: RuntimeState, stage_kind_id: str) -> str:
    return next(
        activation.activation_id
        for activation in state.activations.values()
        if str(activation.stage_kind_id) == stage_kind_id
        and activation.claimed_by_run_id is None
    )


def complete_report_targets(
    state: RuntimeState,
    *,
    plan: SelectedCompiledPlan,
    fingerprint: str,
    kind: str,
    bundle_id: str = "bundle-a",
    suffix_prefix: str | None = None,
    marker: str | None = None,
    report_kind: str | None = None,
) -> RuntimeState:
    stage_kind_id = f"{kind}_stage"
    resolved_report_kind = report_kind or kind
    schema_id = (
        ALPHA_REPORT_SCHEMA_ID
        if resolved_report_kind == "alpha"
        else BETA_REPORT_SCHEMA_ID
    )
    existing_count = sum(
        1
        for artifact in state.artifacts.values()
        if str(artifact.schema_id) == schema_id
        and artifact.payload.get("bundle_id") == bundle_id
    )
    activation_ids = sorted(
        activation.activation_id
        for activation in state.activations.values()
        if str(activation.stage_kind_id) == stage_kind_id
        and activation.claimed_by_run_id is None
        and state.work_items[activation.work_item_id].payload.get("bundle_id")
        == bundle_id
    )
    for offset, activation_id in enumerate(activation_ids, start=existing_count + 1):
        base_suffix = suffix_prefix or kind
        suffix = base_suffix if offset == 1 else f"{base_suffix}-{offset}"
        state = claim_activation(state, activation_id=activation_id, suffix=suffix)
        state = apply_observation(
            state,
            plan=plan,
            fingerprint=fingerprint,
            run_id=f"run-{suffix}",
            input_id=f"observe-{suffix}",
            marker=marker or f"{kind.upper()}_READY",
            artifact_payload=report_payload(
                resolved_report_kind,
                bundle_id=bundle_id,
            ),
        )
    return state


def one_report_state() -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    state, plan, fingerprint = origin_closed_state()
    state = apply_fanout(state, FANOUT_ALPHA_ID, input_id="fanout-alpha")
    state = apply_fanout(state, FANOUT_BETA_ID, input_id="fanout-beta")
    alpha_activation = branch_activation_id(state, "alpha_stage")
    state = claim_activation(state, activation_id=alpha_activation, suffix="alpha")
    state = apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-alpha",
        input_id="observe-alpha",
        marker="ALPHA_READY",
        artifact_payload=report_payload("alpha"),
    )
    return state, plan, fingerprint


def two_report_state() -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    state, plan, fingerprint = schema_covered_but_incomplete_report_state()
    state = complete_report_targets(
        state,
        plan=plan,
        fingerprint=fingerprint,
        kind="alpha",
    )
    state = complete_report_targets(
        state,
        plan=plan,
        fingerprint=fingerprint,
        kind="beta",
    )
    return state, plan, fingerprint


def schema_covered_but_incomplete_report_state() -> tuple[
    RuntimeState,
    SelectedCompiledPlan,
    str,
]:
    state, plan, fingerprint = one_report_state()
    beta_activation = branch_activation_id(state, "beta_stage")
    state = claim_activation(state, activation_id=beta_activation, suffix="beta")
    state = apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-beta",
        input_id="observe-beta",
        marker="BETA_READY",
        artifact_payload=report_payload("beta"),
    )
    return state, plan, fingerprint


def complete_multi_item_report_state() -> tuple[
    RuntimeState,
    SelectedCompiledPlan,
    str,
]:
    return two_report_state()


def lower_plan_join_higher_plan_fanout_state() -> tuple[RuntimeState, str, str]:
    (
        state,
        first_plan,
        first_fingerprint,
        second_plan,
        second_fingerprint,
    ) = two_plan_origin_closed_state()
    plan_by_fingerprint = {
        first_fingerprint: (first_plan, "origin"),
        second_fingerprint: (second_plan, "second-origin"),
    }
    lower_fingerprint, higher_fingerprint = sorted(plan_by_fingerprint)
    lower_plan, source_suffix = plan_by_fingerprint[lower_fingerprint]
    bundle_artifact_id = f"transition-observe-{source_suffix}:artifact"

    for kind, fanout_id in (
        ("alpha", FANOUT_ALPHA_ID),
        ("beta", FANOUT_BETA_ID),
    ):
        state = apply_fanout(
            state,
            fanout_id,
            input_id=f"fanout-lower-{kind}",
            artifact_id=bundle_artifact_id,
        )
        activation_id = branch_activation_id(state, f"{kind}_stage")
        state = claim_activation(
            state,
            activation_id=activation_id,
            suffix=f"lower-{kind}",
        )
        state = apply_observation(
            state,
            plan=lower_plan,
            fingerprint=lower_fingerprint,
            run_id=f"run-lower-{kind}",
            input_id=f"observe-lower-{kind}",
            marker=f"{kind.upper()}_READY",
            artifact_payload=report_payload(kind),
        )
        state = complete_report_targets(
            state,
            plan=lower_plan,
            fingerprint=lower_fingerprint,
            kind=kind,
            suffix_prefix=f"lower-{kind}",
        )
    return state, lower_fingerprint, higher_fingerprint


def lower_plan_ready_with_higher_plan_joined_state() -> tuple[
    RuntimeState,
    str,
    str,
]:
    (
        state,
        first_plan,
        first_fingerprint,
        second_plan,
        second_fingerprint,
    ) = two_plan_origin_closed_state()
    plan_by_fingerprint = {
        first_fingerprint: (first_plan, "origin"),
        second_fingerprint: (second_plan, "second-origin"),
    }
    lower_fingerprint, higher_fingerprint = sorted(plan_by_fingerprint)
    for label, fingerprint in (
        ("lower-plan", lower_fingerprint),
        ("higher-plan", higher_fingerprint),
    ):
        plan, source_suffix = plan_by_fingerprint[fingerprint]
        source_artifact = f"transition-observe-{source_suffix}:artifact"
        for kind, fanout_id in (
            ("alpha", FANOUT_ALPHA_ID),
            ("beta", FANOUT_BETA_ID),
        ):
            state = apply_fanout(
                state,
                fanout_id,
                input_id=f"fanout-{label}-{kind}",
                artifact_id=source_artifact,
            )
            activation_ids = sorted(
                activation.activation_id
                for activation in state.activations.values()
                if activation.plan_ref.authority_fingerprint == fingerprint
                and str(activation.stage_kind_id) == f"{kind}_stage"
                and activation.claimed_by_run_id is None
            )
            for index, activation_id in enumerate(activation_ids, start=1):
                suffix = f"{label}-{kind}-{index}"
                state = claim_activation(
                    state,
                    activation_id=activation_id,
                    suffix=suffix,
                )
                state = apply_observation(
                    state,
                    plan=plan,
                    fingerprint=fingerprint,
                    run_id=f"run-{suffix}",
                    input_id=f"observe-{suffix}",
                    marker=f"{kind.upper()}_READY",
                    artifact_payload=report_payload(kind),
                )
    higher_input = JoinFromArtifact(
        "join-higher-plan",
        join_id=JOIN_ID,
        source_artifact_id="transition-observe-higher-plan-beta-1:artifact",
    )
    state = apply_accepted_input(
        state,
        higher_input,
        context(
            higher_input.input_id,
            work_item_id="work-review-higher-plan",
            activation_id="activation-review-higher-plan",
        ),
    )
    return state, lower_fingerprint, higher_fingerprint


def two_report_state_from_source(
    workflow_source: Mapping[str, object],
) -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    state, plan, fingerprint = origin_closed_state_from_source(workflow_source)
    state = apply_fanout(state, FANOUT_ALPHA_ID, input_id="fanout-alpha")
    state = apply_fanout(state, FANOUT_BETA_ID, input_id="fanout-beta")
    for kind, marker in (("alpha", "ALPHA_READY"), ("beta", "BETA_READY")):
        activation_id = branch_activation_id(state, f"{kind}_stage")
        state = claim_activation(
            state,
            activation_id=activation_id,
            suffix=kind,
        )
        state = apply_observation(
            state,
            plan=plan,
            fingerprint=fingerprint,
            run_id=f"run-{kind}",
            input_id=f"observe-{kind}",
            marker=marker,
            artifact_payload=report_payload(kind),
        )
        state = complete_report_targets(
            state,
            plan=plan,
            fingerprint=fingerprint,
            kind=kind,
        )
    return state, plan, fingerprint


def alternative_action_report_state(
    *,
    alpha_uses_alternative: bool,
) -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    state, plan, fingerprint = origin_closed_state_from_source(
        source_with_alternative_alpha_report_action()
    )
    state = apply_fanout(state, FANOUT_ALPHA_ID, input_id="fanout-alpha")
    state = apply_fanout(state, FANOUT_BETA_ID, input_id="fanout-beta")
    state = complete_report_targets(
        state,
        plan=plan,
        fingerprint=fingerprint,
        kind="alpha",
        marker="ALPHA_BETA_READY" if alpha_uses_alternative else "ALPHA_READY",
        report_kind="beta" if alpha_uses_alternative else "alpha",
    )
    state = complete_report_targets(
        state,
        plan=plan,
        fingerprint=fingerprint,
        kind="beta",
    )
    return state, plan, fingerprint


def add_complete_report_group(
    state: RuntimeState,
    *,
    plan: SelectedCompiledPlan,
    fingerprint: str,
    suffix: str,
    bundle_id: str,
) -> RuntimeState:
    origin_suffix = f"{suffix}-origin"
    enqueue_input_id = f"enqueue-{origin_suffix}"
    state = apply_accepted_input(
        state,
        EnqueueWork(
            enqueue_input_id,
            queue_family_id=QueueFamilyId("origin"),
            payload=source_payload(bundle_id),
        ),
        context(
            enqueue_input_id,
            work_item_id=f"work-{origin_suffix}",
            activation_id=f"activation-{origin_suffix}",
        ),
    )
    state = claim_activation(
        state,
        activation_id=f"activation-{origin_suffix}",
        suffix=origin_suffix,
    )
    state = apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=f"run-{origin_suffix}",
        input_id=f"observe-{origin_suffix}",
        marker="SOURCE_READY",
        artifact_payload=source_payload(bundle_id),
    )
    source_artifact_id = f"transition-observe-{origin_suffix}:artifact"
    for kind, fanout_id in (
        ("alpha", FANOUT_ALPHA_ID),
        ("beta", FANOUT_BETA_ID),
    ):
        fanout_input_id = f"fanout-{suffix}-{kind}"
        state = apply_accepted_input(
            state,
            FanoutFromArtifact(
                fanout_input_id,
                fanout_id=fanout_id,
                source_artifact_id=source_artifact_id,
            ),
            context(fanout_input_id),
        )
        branch_suffix = f"{suffix}-{kind}"
        branch_activation = branch_activation_id_for_bundle(
            state,
            stage_kind_id=f"{kind}_stage",
            bundle_id=bundle_id,
        )
        state = claim_activation(
            state,
            activation_id=branch_activation,
            suffix=branch_suffix,
        )
        state = apply_observation(
            state,
            plan=plan,
            fingerprint=fingerprint,
            run_id=f"run-{branch_suffix}",
            input_id=f"observe-{branch_suffix}",
            marker=f"{kind.upper()}_READY",
            artifact_payload=report_payload(kind, bundle_id=bundle_id),
        )
        state = complete_report_targets(
            state,
            plan=plan,
            fingerprint=fingerprint,
            kind=kind,
            bundle_id=bundle_id,
            suffix_prefix=branch_suffix,
        )
    return state


def branch_activation_id_for_bundle(
    state: RuntimeState,
    *,
    stage_kind_id: str,
    bundle_id: str,
) -> str:
    matches = sorted(
        activation.activation_id
        for activation in state.activations.values()
        if str(activation.stage_kind_id) == stage_kind_id
        and activation.claimed_by_run_id is None
        and state.work_items[activation.work_item_id].payload.get("bundle_id")
        == bundle_id
    )
    assert matches
    return matches[0]


def two_group_report_state() -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    state, plan, fingerprint = admitted_state()
    state = add_complete_report_group(
        state,
        plan=plan,
        fingerprint=fingerprint,
        suffix="a",
        bundle_id="bundle-a",
    )
    state = add_complete_report_group(
        state,
        plan=plan,
        fingerprint=fingerprint,
        suffix="b",
        bundle_id="bundle-b",
    )
    return state, plan, fingerprint


def join_transition_for_group(
    suffix: str,
    *,
    input_id: str | None = None,
) -> tuple[JoinFromArtifact, TransitionContext]:
    resolved_input_id = input_id or f"join-{suffix}"
    return (
        JoinFromArtifact(
            resolved_input_id,
            join_id=JOIN_ID,
            source_artifact_id=f"transition-observe-{suffix}-alpha:artifact",
        ),
        context(
            resolved_input_id,
            work_item_id=f"work-review-{suffix}",
            activation_id=f"activation-review-{suffix}",
        ),
    )


def apply_join(state: RuntimeState, *, input_id: str = "join-reports") -> RuntimeState:
    return apply_accepted_input(
        state,
        JoinFromArtifact(
            input_id,
            join_id=JOIN_ID,
            source_artifact_id="transition-observe-beta:artifact",
        ),
        context(
            input_id,
            work_item_id="work-review",
            activation_id="activation-review",
        ),
    )


def with_non_join_join_completion_authorship(state: RuntimeState) -> RuntimeState:
    route = next(
        route for route in state.activation_routes if str(route.action_id) == JOIN_ID
    )
    non_join_input_id = "observe-beta"
    target_work = state.work_items[route.target_work_item_id]
    target_activation = state.activations[route.target_activation_id]
    return replace(
        state,
        work_items={
            **state.work_items,
            target_work.ref.work_item_id: replace(
                target_work,
                created_by_input_id=non_join_input_id,
            ),
        },
        activations={
            **state.activations,
            target_activation.activation_id: replace(
                target_activation,
                created_by_input_id=non_join_input_id,
            ),
        },
        activation_routes=tuple(
            replace(
                candidate,
                record_id="transition-observe-beta:route",
                created_by_input_id=non_join_input_id,
            )
            if candidate.record_id == route.record_id
            else candidate
            for candidate in state.activation_routes
        ),
    )


def without_join_completion_route(state: RuntimeState) -> RuntimeState:
    return replace(
        state,
        activation_routes=tuple(
            route
            for route in state.activation_routes
            if str(route.action_id) != JOIN_ID
        ),
    )


def with_duplicate_join_completion_route(state: RuntimeState) -> RuntimeState:
    route = next(
        route for route in state.activation_routes if str(route.action_id) == JOIN_ID
    )
    return replace(
        state,
        activation_routes=(
            *state.activation_routes,
            replace(route, record_id=f"{route.record_id}:duplicate"),
        ),
    )


def with_unscopable_join_completion_source(state: RuntimeState) -> RuntimeState:
    return replace(
        state,
        activation_routes=tuple(
            replace(
                route,
                source_run_id="run-origin",
                source_work_item_id="work-origin",
            )
            if str(route.action_id) == JOIN_ID
            else route
            for route in state.activation_routes
        ),
    )


def with_unscopable_non_join_join_action_route(state: RuntimeState) -> RuntimeState:
    source_work = state.work_items["work-origin"]
    plan_ref = source_work.ref.plan_ref
    target_work = WorkItem(
        ref=WorkItemRef(
            work_item_id="work-forged-join-route",
            plan_ref=plan_ref,
            generation=0,
        ),
        queue_family_id=QueueFamilyId("joined_bundle"),
        payload=source_payload(),
        lineage_id=source_work.lineage_id,
        created_by_input_id="observe-origin",
    )
    target_activation = Activation(
        activation_id="activation-forged-join-route",
        work_item_id=target_work.ref.work_item_id,
        lineage_id=source_work.lineage_id,
        plan_ref=plan_ref,
        queue_family_id=QueueFamilyId("joined_bundle"),
        graph_node_id="lifecycle.review.start",
        stage_kind_id=StageKindId("review_stage"),
        runner_binding_id=RunnerBindingId("lifecycle.runner"),
        generation=0,
        created_by_input_id="observe-origin",
    )
    route = ActivationRouteRecord(
        record_id="forged-non-join:route",
        action_id=ActionId(JOIN_ID),
        source_run_id="run-origin",
        source_work_item_id="work-origin",
        target_work_item_id=target_work.ref.work_item_id,
        target_activation_id=target_activation.activation_id,
        created_by_input_id="observe-origin",
    )
    return replace(
        state,
        work_items={**state.work_items, target_work.ref.work_item_id: target_work},
        activations={
            **state.activations,
            target_activation.activation_id: target_activation,
        },
        activation_routes=(*state.activation_routes, route),
    )


def with_join_completion_route_record_id(
    state: RuntimeState,
    record_id: str,
) -> RuntimeState:
    return replace(
        state,
        activation_routes=tuple(
            replace(route, record_id=record_id)
            if str(route.action_id) == JOIN_ID
            else route
            for route in state.activation_routes
        ),
    )


def join_bijection_state(case: str) -> RuntimeState:
    state, _plan, _fingerprint = two_report_state()
    if case == "unscopable_non_join_route":
        return with_unscopable_non_join_join_action_route(state)
    completed = apply_join(state)
    if case == "missing_route":
        return without_join_completion_route(completed)
    if case == "duplicate_route":
        return with_duplicate_join_completion_route(completed)
    if case == "unscopable_source":
        return with_unscopable_join_completion_source(completed)
    if case == "route_record_id":
        return with_join_completion_route_record_id(
            completed,
            "drifted-join-route",
        )
    if case == "non_join_creator":
        return with_non_join_join_completion_authorship(completed)
    if case == "receipt_payload_digest":
        return with_receipt_payload_digest(completed, input_id="join-reports")
    if case == "missing_event":
        return without_governance_event(completed, input_id="join-reports")
    if case == "drifted_event":
        return with_governance_event_record_id(
            completed,
            input_id="join-reports",
            record_id="drifted-join-governance",
        )
    if case == "missing_trace":
        return without_trace(completed, input_id="join-reports")
    if case == "drifted_trace":
        return with_trace_record_id(
            completed,
            input_id="join-reports",
            record_id="drifted-join-trace",
        )
    if case == "missing_route_transition_kind":
        missing = without_join_completion_route(completed)
        return replace(
            missing,
            transitions=tuple(
                replace(
                    transition,
                    input_kind=RunnerResultObserved.input_kind,
                )
                if transition.input_id == "join-reports"
                else transition
                for transition in missing.transitions
            ),
        )
    raise AssertionError(f"unknown join bijection case: {case}")


def with_duplicate_alpha_report(state: RuntimeState) -> RuntimeState:
    original = state.artifacts["transition-observe-alpha:artifact"]
    duplicate = replace(
        original,
        artifact_id="duplicate-alpha-report",
        created_by_input_id="corrupt-duplicate-alpha",
        transition_id="transition-corrupt-duplicate-alpha",
    )
    return replace(
        state,
        artifacts={**state.artifacts, duplicate.artifact_id: duplicate},
    )


def with_mismatched_beta_report(state: RuntimeState) -> RuntimeState:
    original = state.artifacts["transition-observe-beta:artifact"]
    payload = report_payload("beta", bundle_id="bundle-other")
    replacement = replace(
        original,
        payload=payload,
        payload_digest=artifact_payload_digest(payload),
    )
    return replace(
        state,
        artifacts={**state.artifacts, original.artifact_id: replacement},
    )
