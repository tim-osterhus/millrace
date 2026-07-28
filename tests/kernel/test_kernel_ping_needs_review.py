from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from kernel.kernel_ping_scenarios import bootstrap_to_worker_claim
from millrace.contracts import ActionId, ArtifactSchemaId, QueueFamilyId
from millrace.contracts.compiled_plan import AuthorityValue
from millrace.kernel import apply
from millrace.kernel.schema import validate_schema
from millrace.testing import decide_with_fake_runner_completion as decide
from support.kernel_ping import (
    action_by_id,
    compile_kernel_ping,
    kernel_ping_context,
    mutation_kinds,
    runner_observation,
    task_artifact_payload,
)


def _worker_incident_candidate(
    **overrides: AuthorityValue,
) -> Mapping[str, AuthorityValue]:
    candidate: dict[str, AuthorityValue] = {
        "worker_summary": "The task artifact lacks an acceptance command.",
        "missing_details": ("exact command", "expected output"),
        "incident_kind": "hostile.kind",
        "incident_version": 999,
        "source_prompt_id": "hostile-prompt",
        "source_task_artifact_id": "hostile-work-item",
        "worker_run_id": "hostile-run",
        "reason": "hostile-reason",
        "requested_taskmaster_action": "hostile-action",
    }
    candidate.update(overrides)
    return candidate


def test_worker_needs_review_creates_schema_valid_incident_and_routes_back() -> None:
    plan, fingerprint = compile_kernel_ping()
    review_action = action_by_id(plan, "kernel_ping.route_worker_review")
    assert review_action.action_kind == "create_incident_route"
    assert review_action.target_graph_node_id is not None
    assert review_action.payload_projection is not None
    assert review_action.emitted_queue_family_id == QueueFamilyId("task_incident")
    assert review_action.artifact_schema_id == ArtifactSchemaId(
        "kernel_ping.task_incident"
    )

    incident_schema = next(
        schema
        for schema in plan.artifact_schemas
        if schema.id == ArtifactSchemaId("kernel_ping.task_incident")
    )
    state = bootstrap_to_worker_claim(
        plan,
        fingerprint,
        task_artifact=task_artifact_payload(objective="Prove the review route"),
    )
    source_lineage_id = state.work_items["work-task-artifact"].lineage_id

    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-worker",
            action_id="kernel_ping.route_worker_review",
            input_id="observe-needs-review",
            artifact_payload=_worker_incident_candidate(),
        ),
        kernel_ping_context("observe-needs-review"),
    )

    assert decision.accepted is True
    assert "mutation.record_artifact" in mutation_kinds(decision)
    assert "mutation.create_work_item" in mutation_kinds(decision)
    assert "mutation.create_activation" in mutation_kinds(decision)
    assert "mutation.route_activation" in mutation_kinds(decision)
    assert "mutation.set_pause" not in mutation_kinds(decision)

    after = apply(state, decision)
    incident_work = after.work_items["work-review-incident"]
    incident_activation = after.activations["activation-review-taskmaster"]
    incident_payload = incident_work.payload
    incident_artifact = after.artifacts["transition-observe-needs-review:artifact"]

    assert validate_schema(incident_schema.schema, incident_payload).accepted is True
    assert dict(incident_payload) == {
        "incident_kind": "kernel_ping.task_incident",
        "incident_version": 1,
        "source_prompt_id": "prompt-1",
        "source_task_artifact_id": "work-task-artifact",
        "worker_run_id": "run-worker",
        "reason": "insufficient_task_detail",
        "worker_summary": "The task artifact lacks an acceptance command.",
        "missing_details": ("exact command", "expected output"),
        "requested_taskmaster_action": "revise_task_artifact",
    }
    assert incident_artifact.payload == incident_payload
    assert incident_work.queue_family_id == review_action.emitted_queue_family_id
    assert incident_work.lineage_id == source_lineage_id
    assert incident_activation.lineage_id == source_lineage_id
    assert incident_activation.stage_kind_id == review_action.target_stage_kind_id
    assert incident_activation.graph_node_id == review_action.target_graph_node_id
    assert after.pause is None
    assert after.quarantines == {}
    assert any(
        route.action_id == ActionId("kernel_ping.route_worker_review")
        and route.target_work_item_id == "work-review-incident"
        for route in after.activation_routes
    )


def test_worker_needs_review_refuses_schema_invalid_projected_artifact() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_worker_claim(plan, fingerprint)

    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-worker",
            action_id="kernel_ping.route_worker_review",
            input_id="observe-needs-review",
            artifact_payload=_worker_incident_candidate(worker_summary=""),
        ),
        kernel_ping_context("observe-needs-review"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_artifact_payload"
    assert {
        "mutation.record_runner_observation",
        "mutation.record_artifact",
        "mutation.create_work_item",
        "mutation.create_activation",
        "mutation.route_activation",
    }.isdisjoint(mutation_kinds(decision))


def test_kernel_source_omits_review_route_workflow_literals() -> None:
    kernel_root = Path(__file__).resolve().parents[2] / "src" / "millrace" / "kernel"
    forbidden = ("NEEDS_REVIEW", "Taskmaster", "Worker", "task_incident")
    matches: list[tuple[Path, str]] = []
    for path in sorted(kernel_root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for literal in forbidden:
            pattern = re.compile(
                rf"(?<![A-Za-z0-9_]){re.escape(literal)}(?![A-Za-z0-9_])"
            )
            if pattern.search(text):
                matches.append((path.relative_to(kernel_root), literal))

    assert matches == []
