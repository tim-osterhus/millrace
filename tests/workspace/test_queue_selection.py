from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from millrace_ai.architecture import PlaneQueueClaimPolicyDefinition, WorkItemFamilyDefinition
from millrace_ai.contracts import IncidentDecision, IncidentDocument, Plane, SpecDocument, WorkItemKind
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.workspace.queue_selection import claim_next_planning_item

NOW = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)


def _spec_doc(spec_id: str, *, created_at: datetime) -> SpecDocument:
    return SpecDocument(
        spec_id=spec_id,
        title=f"Spec {spec_id}",
        summary="planning input",
        source_type="manual",
        root_idea_id="idea-001",
        root_spec_id="spec-root-001",
        goals=("define implementation plan",),
        constraints=("stay deterministic",),
        acceptance=("planning queue works",),
        references=("operator request",),
        created_at=created_at,
        created_by="tests",
    )


def _incident_doc(incident_id: str, *, opened_at: datetime) -> IncidentDocument:
    return IncidentDocument(
        incident_id=incident_id,
        title=f"Incident {incident_id}",
        summary="execution recovery",
        root_idea_id="idea-001",
        root_spec_id="spec-root-001",
        source_stage="consultant",
        source_plane=Plane.EXECUTION,
        failure_class="malformed_output",
        trigger_reason="bad terminal marker",
        consultant_decision=IncidentDecision.NEEDS_PLANNING,
        opened_at=opened_at,
        opened_by="tests",
    )


def test_planning_claim_order_comes_from_policy(tmp_path: Path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)
    store.enqueue_incident(_incident_doc("inc-001", opened_at=NOW))
    store.enqueue_spec(_spec_doc("spec-001", created_at=NOW + timedelta(minutes=1)))
    policy = PlaneQueueClaimPolicyDefinition(
        policy_id="planning.test",
        plane=Plane.PLANNING,
        family_order=("spec", "incident", "probe"),
        closure_lineage_policy="defer_unrelated",
        empty_behavior="idle",
    )

    claim = claim_next_planning_item(paths, queue_claim_policy=policy)

    assert claim is not None
    assert claim.work_item_kind is WorkItemKind.SPEC
    assert claim.family_id == "spec"
    assert claim.work_item_id == "spec-001"
    assert claim.source_state == "queue"
    assert claim.source_path == paths.specs_queue_dir / "spec-001.md"
    assert claim.claim_policy_id == "planning.test"
    assert claim.claim_order == 0


def test_default_planning_claim_policy_preserves_blueprint_position() -> None:
    from millrace_ai.assets import load_builtin_workflow_primitives

    bundle = load_builtin_workflow_primitives()
    planning_policy = next(
        policy for policy in bundle.queue_claim_policies if policy.policy_id == "planning.default"
    )

    assert planning_policy.family_order == ("incident", "blueprint_draft", "probe", "spec")


def test_planning_claim_policy_claims_custom_compiled_family(tmp_path: Path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    family = _custom_planning_family()
    queue_dir = paths.runtime_root / family.queue_dirs.queue
    queue_dir.mkdir(parents=True, exist_ok=True)
    source = queue_dir / "custom-001.json"
    source.write_text(
        (
            '{"custom_id":"custom-001","root_spec_id":"spec-root-001",'
            '"created_at":"2026-05-19T12:00:00+00:00"}\n'
        ),
        encoding="utf-8",
    )
    policy = PlaneQueueClaimPolicyDefinition(
        policy_id="planning.custom",
        plane=Plane.PLANNING,
        family_order=("custom_review",),
        closure_lineage_policy="defer_unrelated",
        empty_behavior="idle",
    )

    claim = claim_next_planning_item(
        paths,
        root_spec_id="spec-root-001",
        queue_claim_policy=policy,
        work_item_families=(family,),
    )

    assert claim is not None
    assert claim.family_id == "custom_review"
    assert claim.work_item_kind is None
    assert claim.plane is Plane.PLANNING
    assert claim.work_item_id == "custom-001"
    assert claim.path == paths.runtime_root / family.queue_dirs.active / "custom-001.json"
    assert claim.source_state == "queue"
    assert claim.source_path == source
    assert claim.claim_policy_id == "planning.custom"
    assert claim.claim_order == 0
    assert claim.path.is_file()
    assert not source.exists()


def _custom_planning_family() -> WorkItemFamilyDefinition:
    return WorkItemFamilyDefinition(
        family_id="custom_review",
        plane=Plane.PLANNING,
        entry_key="custom_review",
        display_name="Custom Review",
        document_kind="custom_review",
        runtime_relative_dir="custom/reviews",
        file_extension=".json",
        schema_id="custom_review_document_v1",
        document_adapter_id="custom_review_json_v1",
        queue_dirs={
            "queue": "custom/reviews/queue",
            "active": "custom/reviews/active",
            "done": "custom/reviews/done",
            "blocked": "custom/reviews/blocked",
            "canceled": "custom/reviews/canceled",
        },
        lifecycle_states=("queue", "active", "done", "blocked", "canceled"),
        claimable_state="queue",
        active_state="active",
        done_state="done",
        blocked_state="blocked",
        canceled_state="canceled",
        closure_blocking_states=("queue", "active", "blocked"),
        default_entry_key="custom_review",
        id_field="custom_id",
        created_at_field="created_at",
        lineage_fields=("root_spec_id",),
        operator_capabilities=("cancel", "inspect"),
    )
