from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from cli.test_cli_projections import invoke
from millrace.adapters.cli.graph_projection import graph_records
from millrace.compiler import compile_workflow
from millrace.contracts.compiled_plan import (
    CompletionBehaviorDeclaration,
    ExternalEnqueueRouteDeclaration,
    FanoutDeclaration,
    GeneratedWorkRouteDeclaration,
    GraphDeclaration,
    InterventionOptionDeclaration,
    JoinDeclaration,
    OperatorWaitDeclaration,
    RecoveryPolicyDeclaration,
    RemediationPolicyDeclaration,
)
from millrace.contracts.ids import ArtifactSchemaId
from millrace.workflows import kernel_ping
from support.run_controls import runtime_with_run


def selected():
    plan = compile_workflow(kernel_ping.workflow_source()).plan
    assert plan is not None
    actions = (
        replace(
            plan.terminal_actions[0],
            id="a",
            stage_kind_id="s",
            outcome_id="o",
            target_graph_node_id="n",
            target_stage_kind_id="s2",
            dynamic_target_selector=None,
        ),
        replace(
            plan.terminal_actions[0],
            id="end",
            stage_kind_id="s",
            outcome_id="o",
            target_graph_node_id=None,
            target_stage_kind_id=None,
            dynamic_target_selector=None,
        ),
        replace(
            plan.terminal_actions[0],
            id="dynamic",
            stage_kind_id="s",
            outcome_id="o",
            target_graph_node_id=None,
            dynamic_target_selector={"private": "prompt"},
        ),
        replace(
            plan.terminal_actions[0],
            id="conditional",
            stage_kind_id="s",
            outcome_id="o",
            target_graph_node_id="n",
            artifact_field_conditions={"x": 1},
            dynamic_target_selector=None,
        ),
    )
    return replace(
        plan,
        graphs=(GraphDeclaration("g", ("n", "unbound"), {}),),
        stage_kinds=(
            replace(plan.stage_kinds[0], id="s"),
            replace(plan.stage_kinds[0], id="s2"),
        ),
        terminal_actions=actions,
        external_enqueue_routes=(
            ExternalEnqueueRouteDeclaration("external", "q", "n", "s", "b"),
        ),
        generated_work_routes=(
            GeneratedWorkRouteDeclaration("generated", "q", "n", "s2", "b"),
        ),
        fanout_declarations=(
            FanoutDeclaration(
                "fan",
                "a",
                "artifact",
                ("items",),
                "id",
                "generated",
                "keep",
                "q",
                "s2",
                "n",
                "b",
                "artifact",
                {},
                "drop",
                "inherit",
                "none",
            ),
        ),
        remediation_policies=(
            RemediationPolicyDeclaration(
                "rem",
                "a",
                "q",
                "s",
                "n",
                "b",
                "artifact",
                "source",
                "key",
                "drop",
                "none",
                "root",
                {},
            ),
        ),
        completion_behaviors=(
            CompletionBehaviorDeclaration(
                "complete",
                "all_closed",
                "ready",
                "request",
                "selected",
                "s",
                "n",
                "b",
                "q",
                "pass",
                "gap",
                "blocked",
                "verdict",
                (ArtifactSchemaId("artifact"),),
                10,
                100,
                "rem",
                ("root",),
                "root",
                "window",
                "rubric",
                "block",
                False,
                {},
            ),
        ),
        operator_waits=(
            OperatorWaitDeclaration(
                "wait",
                ("a", "end"),
                "run",
                "retain",
                False,
                True,
                ("resume",),
                None,
                "q",
                "s",
                "n",
                "b",
                "operator",
                (),
                "key",
                "once",
                "none",
                "none",
                "none",
                "waiting",
            ),
            OperatorWaitDeclaration(
                "no-target",
                ("a",),
                "run",
                "retain",
                False,
                True,
                ("resume",),
                None,
                None,
                None,
                None,
                None,
                "operator",
                (),
                "key",
                "once",
                "none",
                "none",
                "none",
                "waiting",
            ),
        ),
        intervention_options=(
            InterventionOptionDeclaration(
                "option",
                "policy",
                "resume",
                "waiting",
                "dynamic-source",
                "dynamic-resume",
                None,
                None,
                "q",
                "s",
                "n",
                "b",
                "none",
                "retain",
                "operator",
                (),
            ),
        ),
        join_declarations=(
            JoinDeclaration("join", "s", "key", ("artifact-a", "artifact-b"), "wait"),
        ),
        recovery_policies=(
            RecoveryPolicyDeclaration(
                "policy",
                ("a",),
                ("end",),
                ("conditional",),
                "s",
                "dynamic",
                "run",
                1,
                2,
                3,
                "quarantine",
                (),
                ("dynamic",),
                5,
            ),
        ),
    )


def test_every_relationship_family_has_exact_independent_endpoints_and_ids():
    plan = selected()
    rows = graph_records(plan, "sha256:" + "a" * 64)
    edges = [row for row in rows if row["kind"] == "relationship"]
    actual = {
        (
            e["declaration_id"],
            e["relation_role"],
            e["source"]["kind"],
            e["source"]["id"],
            e["target"]["kind"],
            e["target"]["id"],
        )
        for e in edges
    }
    expected = {
        ("external", "route_target", "route", "external", "node", "n"),
        ("generated", "route_target", "route", "generated", "node", "n"),
        ("a", "action_target", "action", "a", "node", "n"),
        ("end", "action_target", "action", "end", "terminal", "o"),
        ("dynamic", "action_target", "action", "dynamic", "node", None),
        ("conditional", "action_target", "action", "conditional", "node", "n"),
        ("fan", "action_target", "action", "a", "node", "n"),
        ("rem", "action_target", "action", "a", "node", "n"),
        ("complete", "completion_target", "selected_scope", "complete", "node", "n"),
        ("wait", "wait_continuation", "action", "a", "node", "n"),
        ("wait", "wait_continuation", "action", "end", "node", "n"),
        ("no-target", "wait_continuation", "action", "a", "node", None),
        ("option", "intervention_target", "selected_scope", "policy", "node", "n"),
        (
            "join",
            "artifact_dependency:artifact-a",
            "selected_scope",
            "join",
            "stage",
            "s",
        ),
        (
            "join",
            "artifact_dependency:artifact-b",
            "selected_scope",
            "join",
            "stage",
            "s",
        ),
        ("policy", "unsupported_topology", "selected_scope", "policy", "stage", "s"),
        (
            "policy",
            "source_recovery_action_ids",
            "action",
            "a",
            "selected_scope",
            "policy",
        ),
        ("policy", "return_action_ids", "action", "end", "selected_scope", "policy"),
        (
            "policy",
            "quarantine_action_ids",
            "action",
            "conditional",
            "selected_scope",
            "policy",
        ),
        (
            "policy",
            "reset_trigger_action_ids",
            "action",
            "dynamic",
            "selected_scope",
            "policy",
        ),
    }
    assert actual == expected
    assert len(edges) == len(expected)
    for edge in edges:
        canonical = {
            key: edge[key]
            for key in (
                "plan_fingerprint",
                "declaration_kind",
                "declaration_id",
                "relation_role",
                "source",
                "target",
            )
        }
        assert (
            edge["edge_id"]
            == hashlib.sha256(
                json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
    reversed_rows = graph_records(
        replace(
            plan,
            terminal_actions=tuple(reversed(plan.terminal_actions)),
            operator_waits=tuple(reversed(plan.operator_waits)),
        ),
        "sha256:" + "a" * 64,
    )
    assert {e["edge_id"] for e in edges} == {
        e["edge_id"] for e in reversed_rows if e["kind"] == "relationship"
    }
    nodes = {row["id"]: row for row in rows if row["kind"] == "node"}
    assert nodes["n"]["stage_bindings"] == ["s", "s2"]
    assert nodes["n"]["membership"]["ambiguous"]
    assert nodes["unbound"]["membership"]["availability"] == "unavailable"
    assert (
        next(e for e in edges if e["declaration_id"] == "dynamic")["availability"]
        == "dynamic_unresolved"
    )
    assert (
        next(e for e in edges if e["declaration_id"] == "conditional")["condition_kind"]
        == "artifact_conditional"
    )
    assert "private" not in json.dumps(rows)


def test_explicit_dangling_node_refuses_even_with_dynamic_selector():
    plan = selected()
    for action in plan.terminal_actions:
        with pytest.raises(ValueError, match="graph_dangling_node"):
            graph_records(
                replace(
                    plan,
                    terminal_actions=(
                        replace(action, target_graph_node_id="dangling"),
                    ),
                ),
                "fp",
            )


def test_real_admitted_graph_every_page_is_serializable(tmp_path):
    runtime, run = runtime_with_run(tmp_path)
    fingerprint = str(run.run_ref.plan_ref.authority_fingerprint)
    cursor = None
    seen = []
    while True:
        args = [] if cursor is None else ["--cursor", cursor]
        code, result = invoke(
            runtime, *args, "--page-size", "3", "plan", "graph", fingerprint
        )
        assert code == 0, result
        seen.extend(result["data"]["records"])
        cursor = result["data"]["next_cursor"]
        if cursor is None:
            break
    assert {r["kind"] for r in seen} == {
        "graph",
        "node",
        "stage",
        "relationship",
        "plan",
    }
    assert len({(r["kind"], r["id"]) for r in seen}) == len(seen)
    runtime.close()


def test_overlay_only_counts_exact_accepted_action_traces():
    from types import SimpleNamespace
    from unittest.mock import patch

    from millrace.adapters.cli.graph_projection import overlay_records

    plan = selected()
    traces = [
        SimpleNamespace(
            plan_fingerprint="fp",
            action_id="a",
            disposition="accepted",
            record_id="t1",
            run_id="r",
        ),
        SimpleNamespace(
            plan_fingerprint="other",
            action_id="a",
            disposition="accepted",
            record_id="t2",
            run_id="r",
        ),
        SimpleNamespace(
            plan_fingerprint="fp",
            action_id="a",
            disposition="refused",
            record_id="t3",
            run_id="r",
        ),
    ]
    state = SimpleNamespace(traces=traces, activations={}, closed_work_items={})
    with patch(
        "millrace.operator.dispatch.list_ready_dispatch_candidates",
        return_value=SimpleNamespace(candidates=[]),
    ):
        rows = overlay_records(plan, "fp", state)
    edge = next(e for e in graph_records(plan, "fp") if e.get("declaration_id") == "a")
    overlay = next(
        row
        for row in rows
        if row["kind"] == "edge_overlay" and row["edge_id"] == edge["id"]
    )
    assert overlay["availability"] == "available" and overlay["count"] == 1
    links = [row for row in rows if row["kind"] == "edge_trace"]
    assert len(links) == 1 and links[0]["trace_id"] == "t1"
    assert all(
        row["count"] is None
        for row in rows
        if row["kind"] == "edge_overlay" and row != overlay
    )
