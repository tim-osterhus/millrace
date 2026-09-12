"""Selected declarations projected as typed relationships, never compiled anew."""

from __future__ import annotations

from typing import Any

from millrace.contracts.compiled_plan import SelectedCompiledPlan
from millrace.contracts.public_projections import digest


def graph_records(plan: SelectedCompiledPlan, fingerprint: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    memberships: dict[str, list[str]] = {}
    bindings: dict[str, set[str]] = {}
    for graph in plan.graphs:
        records.append(
            {"kind": "graph", "id": str(graph.id), "graph_id": str(graph.id)}
        )
        for node in graph.node_ids:
            memberships.setdefault(node, []).append(str(graph.id))
            bindings.setdefault(node, set())

    def ref(kind: str, identity: object) -> dict[str, Any]:
        return {"kind": kind, "id": None if identity is None else str(identity)}

    def edge(
        declaration: Any,
        role: str,
        source: dict[str, Any],
        target: dict[str, Any],
        *,
        availability: str = "available",
        condition: str = "unconditional",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        kind = declaration.record_kind
        identity = str(declaration.id)
        if target["kind"] == "node" and target["id"] is not None:
            if target["id"] not in memberships:
                raise ValueError("graph_dangling_node")
            stage = (metadata or {}).get("target_stage_kind_id")
            if stage is not None:
                bindings[target["id"]].add(str(stage))
        canonical = {
            "plan_fingerprint": fingerprint,
            "declaration_kind": kind,
            "declaration_id": identity,
            "relation_role": role,
            "source": source,
            "target": target,
        }
        edge_id = digest(canonical)
        records.append(
            {
                "kind": "relationship",
                "id": edge_id,
                "edge_id": edge_id,
                **canonical,
                "condition_kind": condition,
                "availability": availability,
                "reason": None if availability == "available" else availability,
                "traversal": {
                    "availability": "unsupported",
                    "count": None,
                    "reason": "no_authenticated_edge_traversal_projection",
                },
                **{
                    key: None if value is None else str(value)
                    for key, value in (metadata or {}).items()
                },
            }
        )

    def target_metadata(item: Any) -> dict[str, Any]:
        return {
            "target_stage_kind_id": getattr(item, "target_stage_kind_id", None),
            "target_queue_family_id": getattr(item, "target_queue_family_id", None),
            "target_runner_binding_id": getattr(item, "target_runner_binding_id", None),
        }

    route: Any
    for route in (*plan.external_enqueue_routes, *plan.generated_work_routes):
        edge(
            route,
            "route_target",
            ref("route", route.id),
            ref("node", route.graph_node_id),
            metadata={
                "target_stage_kind_id": route.stage_kind_id,
                "target_queue_family_id": route.queue_family_id,
                "target_runner_binding_id": route.runner_binding_id,
            },
        )
    for action in plan.terminal_actions:
        dynamic = action.dynamic_target_selector is not None
        target = (
            ref("node", action.target_graph_node_id)
            if action.target_graph_node_id
            else ref("terminal", action.outcome_id)
        )
        if dynamic:
            target = ref("node", None)
        edge(
            action,
            "action_target",
            ref("action", action.id),
            target,
            availability="dynamic_unresolved" if dynamic else "available",
            condition="artifact_conditional"
            if action.artifact_field_conditions
            else "unconditional",
            metadata={
                "source_stage_kind_id": action.stage_kind_id,
                "outcome_id": action.outcome_id,
                "action_kind": action.action_kind,
                "target_stage_kind_id": action.target_stage_kind_id,
                "target_queue_family_id": action.emitted_queue_family_id,
                "target_runner_binding_id": action.runner_binding_id,
                "selector_kind": "dynamic_target_selector" if dynamic else None,
            },
        )
        # Even a dynamic selector does not excuse a malformed explicit declaration.
        if (
            action.target_graph_node_id is not None
            and action.target_graph_node_id not in memberships
        ):
            raise ValueError("graph_dangling_node")
    item: Any
    for item in (*plan.fanout_declarations, *plan.remediation_policies):
        edge(
            item,
            "action_target",
            ref("action", item.source_action_id),
            ref("node", item.target_graph_node_id),
            metadata={
                **target_metadata(item),
                "target_route_id": getattr(item, "target_route_id", None),
            },
        )
    for completion in plan.completion_behaviors:
        edge(
            completion,
            "completion_target",
            ref("selected_scope", completion.id),
            ref("node", completion.target_graph_node_id),
            condition="readiness",
            metadata={
                "trigger": completion.trigger,
                "readiness_rule": completion.readiness_rule,
                "target_stage_kind_id": completion.target_stage_kind_id,
                "target_queue_family_id": completion.request_queue_family_id,
                "target_runner_binding_id": completion.runner_binding_id,
            },
        )
    for wait in plan.operator_waits:
        for action_id in wait.source_action_ids:
            edge(
                wait,
                "wait_continuation",
                ref("action", action_id),
                ref("node", wait.target_graph_node_id),
                availability="available"
                if wait.target_graph_node_id is not None
                else "unavailable_endpoint",
                condition="operator_resolution",
                metadata=target_metadata(wait),
            )
    for option in plan.intervention_options:
        edge(
            option,
            "intervention_target",
            ref("selected_scope", option.policy_id),
            ref("node", option.target_graph_node_id),
            availability="dynamic_unresolved",
            condition="operator_resolution",
            metadata={
                **target_metadata(option),
                "policy_id": option.policy_id,
                "selector_kind": "target_selector",
                "source_availability": "dynamic_unresolved",
                "resume_selector_kind": "resume_target_selector"
                if option.resume_target_selector is not None
                else None,
                "target_availability": "available"
                if option.target_graph_node_id
                else "dynamic_unresolved",
            },
        )
    for join in plan.join_declarations:
        for schema in join.required_artifact_schema_ids:
            edge(
                join,
                "artifact_dependency:" + str(schema),
                ref("selected_scope", join.id),
                ref("stage", join.target_stage_kind_id),
                condition="artifact_dependency",
                metadata={"artifact_schema_id": schema, "join_id": join.id},
            )
    for policy in plan.recovery_policies:
        edge(
            policy,
            "unsupported_topology",
            ref("selected_scope", policy.id),
            ref("stage", policy.recovery_stage_kind_id),
            availability="unsupported_topology",
        )
        for role in (
            "source_recovery_action_ids",
            "return_action_ids",
            "quarantine_action_ids",
            "reset_trigger_action_ids",
        ):
            for action_id in getattr(policy, role):
                edge(
                    policy,
                    role,
                    ref("action", action_id),
                    ref("selected_scope", policy.id),
                    availability="unsupported_topology",
                )
    for stage in plan.stage_kinds:
        records.append(
            {
                "kind": "stage",
                "id": str(stage.id),
                "stage_kind_id": str(stage.id),
                "partition_id": None
                if stage.partition_id is None
                else str(stage.partition_id),
                "runner_binding_id": str(stage.runner_binding_id),
            }
        )
    for node, graphs in memberships.items():
        stages = sorted(bindings[node])
        records.append(
            {
                "kind": "node",
                "id": node,
                "node_id": node,
                "graph_ids": sorted(graphs),
                "stage_bindings": stages,
                "membership": {
                    "availability": "available" if stages else "unavailable",
                    "reason": None if stages else "no_explicit_stage_binding",
                    "ambiguous": len(stages) > 1,
                },
            }
        )
    return records


def overlay_records(
    plan: SelectedCompiledPlan, fingerprint: str, state: Any
) -> list[dict[str, Any]]:
    """Counts come from activation membership or retained exact action traces."""
    from millrace.operator.dispatch import list_ready_dispatch_candidates

    ready = list_ready_dispatch_candidates(state)
    selected = graph_records(plan, fingerprint)
    output = []
    for row in selected:
        if row["kind"] == "node":
            activations = [
                a
                for a in state.activations.values()
                if a.graph_node_id == row["node_id"]
                and str(a.plan_ref.authority_fingerprint) == fingerprint
            ]
            output.append(
                {
                    "kind": "node_overlay",
                    "id": row["id"],
                    "node_id": row["node_id"],
                    "activation_count": len(activations),
                    "ready_count": sum(
                        c.graph_node_id == row["id"]
                        and c.plan_fingerprint == fingerprint
                        for c in ready.candidates
                    ),
                    "claimed_count": sum(
                        a.claimed_by_run_id is not None for a in activations
                    ),
                    "closed_count": sum(
                        a.work_item_id in state.closed_work_items for a in activations
                    ),
                    "availability": "available",
                    "basis": "same_snapshot_authoritative_activation_membership",
                }
            )
        elif row["kind"] == "relationship":
            traces = (
                [
                    trace
                    for trace in state.traces
                    if str(trace.plan_fingerprint) == fingerprint
                    and trace.action_id is not None
                    and str(trace.action_id) == row["declaration_id"]
                    and trace.disposition == "accepted"
                ]
                if row["declaration_kind"] == "terminal_action_declaration"
                and row["availability"] == "available"
                else []
            )
            output.append(
                {
                    "kind": "edge_overlay",
                    "id": row["id"],
                    "edge_id": row["edge_id"],
                    "availability": "available" if traces else "unsupported",
                    "count": len(traces) if traces else None,
                    "reason": None if traces else "no_exact_retained_action_trace",
                    "basis": "accepted_terminal_action_trace" if traces else None,
                }
            )
            for trace in traces:
                output.append(
                    {
                        "kind": "edge_trace",
                        "id": row["id"] + ":" + trace.record_id,
                        "edge_id": row["id"],
                        "trace_id": trace.record_id,
                        "action_id": str(trace.action_id),
                        "run_id": trace.run_id,
                    }
                )
    return output
