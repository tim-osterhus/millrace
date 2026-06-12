from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from millrace_ai.architecture import PlaneQueueClaimPolicyDefinition, WorkItemFamilyDefinition
from millrace_ai.contracts import (
    IncidentDecision,
    IncidentDocument,
    LearningRequestAction,
    Plane,
    SpecDocument,
    WorkItemKind,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.workspace.queue_selection import (
    claim_next_execution_task,
    claim_next_for_family,
    claim_next_for_plane,
    claim_next_learning_request,
    claim_next_planning_item,
)

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


def test_planning_claim_policies_separate_generic_and_blueprint_position() -> None:
    from millrace_ai.assets import load_builtin_workflow_primitives

    bundle = load_builtin_workflow_primitives()
    planning_policy = next(
        policy for policy in bundle.queue_claim_policies if policy.policy_id == "planning.default"
    )
    blueprint_policy = next(
        policy for policy in bundle.queue_claim_policies if policy.policy_id == "planning.blueprint"
    )

    assert planning_policy.family_order == ("incident", "probe", "spec")
    assert blueprint_policy.family_order == ("incident", "blueprint_draft", "probe", "spec")


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


# ---------------------------------------------------------------------------
# Generic claim_next_for_family
# ---------------------------------------------------------------------------


def test_claim_next_for_family_task(tmp_path: Path) -> None:
    """claim_next_for_family('task') correctly claims the next execution task."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)
    from millrace_ai.contracts import TaskDocument

    store.enqueue_task(
        TaskDocument(
            task_id="gen-task-claim",
            title="Generic claim test",
            summary="Verify claim_next_for_family works for tasks.",
            root_idea_id="idea-001",
            root_spec_id="spec-root-001",
            target_paths=("src/",),
            acceptance=("claim returns task",),
            required_checks=("pytest",),
            references=("test suite",),
            risk=("none",),
            created_at=NOW,
            created_by="tests",
        )
    )

    claim = claim_next_for_family(paths, "task")

    assert claim is not None
    assert claim.family_id == "task"
    assert claim.work_item_id == "gen-task-claim"
    assert claim.plane is Plane.EXECUTION
    assert claim.path == paths.tasks_active_dir / "gen-task-claim.md"
    assert claim.path.is_file()


def test_claim_next_for_family_learning_request(tmp_path: Path) -> None:
    """claim_next_for_family('learning_request') claims the next learning request."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)
    from millrace_ai.contracts import LearningRequestDocument

    store.enqueue_learning_request(
        LearningRequestDocument(
            learning_request_id="lr-gen-001",
            title="Generic LR claim",
            summary="Verify claim_next_for_family works for learning requests.",
            requested_action=LearningRequestAction.CREATE,
            target_stage=None,
            created_at=NOW,
            created_by="tests",
        )
    )

    claim = claim_next_for_family(paths, "learning_request")

    assert claim is not None
    assert claim.family_id == "learning_request"
    assert claim.work_item_id == "lr-gen-001"
    assert claim.plane is Plane.LEARNING
    assert claim.path == paths.learning_requests_active_dir / "lr-gen-001.md"
    assert claim.path.is_file()


def test_claim_next_for_family_spec(tmp_path: Path) -> None:
    """claim_next_for_family('spec') claims a spec via the generic path."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)
    store.enqueue_spec(_spec_doc("spec-gen", created_at=NOW))

    claim = claim_next_for_family(paths, "spec")

    assert claim is not None
    assert claim.family_id == "spec"
    assert claim.work_item_id == "spec-gen"
    assert claim.plane is Plane.PLANNING


# ---------------------------------------------------------------------------
# claim_next_for_plane — unified plane-scoped claim
# ---------------------------------------------------------------------------


def test_claim_next_for_plane_execution(tmp_path: Path) -> None:
    """claim_next_for_plane with Plane.EXECUTION uses compiled policy."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)
    from millrace_ai.contracts import TaskDocument

    store.enqueue_task(
        TaskDocument(
            task_id="plane-exec-task",
            title="Plane exec claim",
            summary="Verify claim_next_for_plane with Plane.EXECUTION.",
            root_idea_id="idea-001",
            root_spec_id="spec-root-001",
            target_paths=("src/",),
            acceptance=("claim via plane path",),
            required_checks=("pytest",),
            references=("test suite",),
            risk=("none",),
            created_at=NOW,
            created_by="tests",
        )
    )
    policy = PlaneQueueClaimPolicyDefinition(
        policy_id="execution.test",
        plane=Plane.EXECUTION,
        family_order=("task",),
        closure_lineage_policy="defer_unrelated",
        empty_behavior="idle",
    )

    claim = claim_next_for_plane(
        paths,
        Plane.EXECUTION,
        queue_claim_policy=policy,
    )

    assert claim is not None
    assert claim.family_id == "task"
    assert claim.plane is Plane.EXECUTION
    assert claim.claim_policy_id == "execution.test"
    assert claim.claim_order == 0


def test_claim_next_for_plane_learning(tmp_path: Path) -> None:
    """claim_next_for_plane with Plane.LEARNING uses compiled policy."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)
    from millrace_ai.contracts import LearningRequestDocument

    store.enqueue_learning_request(
        LearningRequestDocument(
            learning_request_id="lr-plane-001",
            title="Plane LR claim",
            summary="Verify claim_next_for_plane with Plane.LEARNING.",
            requested_action=LearningRequestAction.CREATE,
            target_stage=None,
            created_at=NOW,
            created_by="tests",
        )
    )
    policy = PlaneQueueClaimPolicyDefinition(
        policy_id="learning.test",
        plane=Plane.LEARNING,
        family_order=("learning_request",),
        closure_lineage_policy="defer_unrelated",
        empty_behavior="idle",
    )

    claim = claim_next_for_plane(
        paths,
        Plane.LEARNING,
        queue_claim_policy=policy,
    )

    assert claim is not None
    assert claim.family_id == "learning_request"
    assert claim.plane is Plane.LEARNING
    assert claim.claim_policy_id == "learning.test"
    assert claim.claim_order == 0


def test_claim_next_for_plane_planning(tmp_path: Path) -> None:
    """claim_next_for_plane with Plane.PLANNING uses compiled policy."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)
    store.enqueue_spec(_spec_doc("spec-plane", created_at=NOW))
    policy = PlaneQueueClaimPolicyDefinition(
        policy_id="planning.test2",
        plane=Plane.PLANNING,
        family_order=("spec", "probe"),
        closure_lineage_policy="defer_unrelated",
        empty_behavior="idle",
    )

    claim = claim_next_for_plane(
        paths,
        Plane.PLANNING,
        queue_claim_policy=policy,
    )

    assert claim is not None
    assert claim.family_id == "spec"
    assert claim.plane is Plane.PLANNING
    assert claim.claim_policy_id == "planning.test2"
    assert claim.claim_order == 0


def test_claim_next_for_plane_respects_family_order(tmp_path: Path) -> None:
    """First family in policy order is claimed before later families."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)
    store.enqueue_spec(_spec_doc("spec-later", created_at=NOW))
    store.enqueue_incident(_incident_doc("inc-first", opened_at=NOW - timedelta(minutes=10)))
    policy = PlaneQueueClaimPolicyDefinition(
        policy_id="planning.order",
        plane=Plane.PLANNING,
        family_order=("incident", "spec", "probe"),
        closure_lineage_policy="defer_unrelated",
        empty_behavior="idle",
    )

    claim = claim_next_for_plane(
        paths,
        Plane.PLANNING,
        queue_claim_policy=policy,
    )

    assert claim is not None
    # Incident is first in family_order, so it should be claimed first
    assert claim.family_id == "incident"
    assert claim.work_item_id == "inc-first"
    assert claim.claim_order == 0


# ---------------------------------------------------------------------------
# Compatibility wrappers delegate to generic path
# ---------------------------------------------------------------------------


def test_claim_next_execution_task_is_compatibility_wrapper(tmp_path: Path) -> None:
    """claim_next_execution_task delegates to claim_next_for_family('task')."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)
    from millrace_ai.contracts import TaskDocument

    store.enqueue_task(
        TaskDocument(
            task_id="compat-task",
            title="Compat wrapper",
            summary="Verify execution claim helper is a compatibility wrapper.",
            root_idea_id="idea-001",
            root_spec_id="spec-root-001",
            target_paths=("src/",),
            acceptance=("claim returns task",),
            required_checks=("pytest",),
            references=("test suite",),
            risk=("none",),
            created_at=NOW,
            created_by="tests",
        )
    )

    compat_claim = claim_next_execution_task(paths)
    assert compat_claim is not None
    assert compat_claim.family_id == "task"
    assert compat_claim.work_item_id == "compat-task"


def test_claim_next_learning_request_is_compatibility_wrapper(tmp_path: Path) -> None:
    """claim_next_learning_request delegates to claim_next_for_family('learning_request')."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)
    from millrace_ai.contracts import LearningRequestDocument

    store.enqueue_learning_request(
        LearningRequestDocument(
            learning_request_id="compat-lr",
            title="Compat LR wrapper",
            summary="Verify learning claim helper is a compatibility wrapper.",
            requested_action=LearningRequestAction.CREATE,
            target_stage=None,
            created_at=NOW,
            created_by="tests",
        )
    )

    compat_claim = claim_next_learning_request(paths)
    assert compat_claim is not None
    assert compat_claim.family_id == "learning_request"
    assert compat_claim.work_item_id == "compat-lr"


# ---------------------------------------------------------------------------
# One-active plane-scoped gate
# ---------------------------------------------------------------------------


def test_claim_next_for_plane_refuses_when_active_item_exists(tmp_path: Path) -> None:
    """claim_next_for_plane returns None when an active item exists in the plane."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)
    from millrace_ai.contracts import TaskDocument

    store.enqueue_task(
        TaskDocument(
            task_id="exec-task-1",
            title="First task",
            summary="This will be claimed first.",
            root_idea_id="idea-001",
            root_spec_id="spec-root-001",
            target_paths=("src/",),
            acceptance=("claim first",),
            required_checks=("pytest",),
            references=("tests",),
            risk=("none",),
            created_at=NOW,
            created_by="tests",
        )
    )
    store.enqueue_task(
        TaskDocument(
            task_id="exec-task-2",
            title="Second task",
            summary="This should not be claimable.",
            root_idea_id="idea-001",
            root_spec_id="spec-root-001",
            target_paths=("src/",),
            acceptance=("blocked by one-active",),
            required_checks=("pytest",),
            references=("tests",),
            risk=("none",),
            created_at=NOW + timedelta(minutes=1),
            created_by="tests",
        )
    )
    policy = PlaneQueueClaimPolicyDefinition(
        policy_id="execution.one-active",
        plane=Plane.EXECUTION,
        family_order=("task",),
        closure_lineage_policy="defer_unrelated",
        empty_behavior="idle",
    )

    first = claim_next_for_plane(paths, Plane.EXECUTION, queue_claim_policy=policy)
    assert first is not None
    assert first.work_item_id == "exec-task-1"

    # Second claim should be refused because one is already active
    second = claim_next_for_plane(paths, Plane.EXECUTION, queue_claim_policy=policy)
    assert second is None


# ---------------------------------------------------------------------------
# Lineage filtering via generic claim
# ---------------------------------------------------------------------------


def test_claim_next_for_family_respects_root_spec_id(tmp_path: Path) -> None:
    """claim_next_for_family filters by root_spec_id when provided."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)
    from millrace_ai.contracts import TaskDocument

    store.enqueue_task(
        TaskDocument(
            task_id="task-spec-a",
            title="Task for spec A",
            summary="Belongs to root spec A.",
            root_idea_id="idea-001",
            root_spec_id="spec-a",
            target_paths=("src/",),
            acceptance=("lineage filter",),
            required_checks=("pytest",),
            references=("tests",),
            risk=("none",),
            created_at=NOW,
            created_by="tests",
        )
    )
    store.enqueue_task(
        TaskDocument(
            task_id="task-spec-b",
            title="Task for spec B",
            summary="Belongs to root spec B.",
            root_idea_id="idea-001",
            root_spec_id="spec-b",
            target_paths=("src/",),
            acceptance=("lineage filter",),
            required_checks=("pytest",),
            references=("tests",),
            risk=("none",),
            created_at=NOW,
            created_by="tests",
        )
    )

    claim = claim_next_for_family(paths, "task", root_spec_id="spec-b")

    assert claim is not None
    assert claim.work_item_id == "task-spec-b"


# ---------------------------------------------------------------------------
# claim_next_for_plane with custom family (no built-in adapter)
# ---------------------------------------------------------------------------


def test_claim_next_for_plane_custom_family(tmp_path: Path) -> None:
    """claim_next_for_plane handles custom families with no registered adapter."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    family = _custom_planning_family()
    queue_dir = paths.runtime_root / family.queue_dirs.queue
    queue_dir.mkdir(parents=True, exist_ok=True)
    source = queue_dir / "custom-plane.json"
    source.write_text(
        '{"custom_id":"custom-plane","created_at":"2026-05-19T12:00:00+00:00"}\n',
        encoding="utf-8",
    )
    policy = PlaneQueueClaimPolicyDefinition(
        policy_id="planning.custom2",
        plane=Plane.PLANNING,
        family_order=("custom_review",),
        closure_lineage_policy="defer_unrelated",
        empty_behavior="idle",
    )

    claim = claim_next_for_plane(
        paths,
        Plane.PLANNING,
        queue_claim_policy=policy,
        work_item_families=(family,),
    )

    assert claim is not None
    assert claim.family_id == "custom_review"
    assert claim.plane is Plane.PLANNING
    assert claim.work_item_id == "custom-plane"
    assert claim.claim_policy_id == "planning.custom2"
    assert claim.path.is_file()


# ---------------------------------------------------------------------------
# claim_next_for_plane respects per-family one_active_policy
# ---------------------------------------------------------------------------


def test_claim_next_for_plane_one_active_returns_none_for_saturation(tmp_path: Path) -> None:
    """claim_next_for_plane returns None when one-active blocks across plane."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    store = QueueStore(paths)
    from millrace_ai.contracts import TaskDocument

    store.enqueue_task(
        TaskDocument(
            task_id="exec-only",
            title="Only task",
            summary="One task that will be claimed first.",
            root_idea_id="idea-001",
            root_spec_id="spec-root-001",
            target_paths=("src/",),
            acceptance=("claim first then none",),
            required_checks=("pytest",),
            references=("tests",),
            risk=("none",),
            created_at=NOW,
            created_by="tests",
        )
    )
    policy = PlaneQueueClaimPolicyDefinition(
        policy_id="execution.saturation",
        plane=Plane.EXECUTION,
        family_order=("task",),
        closure_lineage_policy="defer_unrelated",
        empty_behavior="idle",
    )

    first = claim_next_for_plane(paths, Plane.EXECUTION, queue_claim_policy=policy)
    assert first is not None
    assert first.work_item_id == "exec-only"

    # No more queued items, so should return None
    second = claim_next_for_plane(paths, Plane.EXECUTION, queue_claim_policy=policy)
    assert second is None


def test_claim_next_for_plane_custom_family_uses_interpreter(tmp_path: Path) -> None:
    """claim_next_for_plane routes custom families through QueueFamilyInterpreter."""
    from millrace_ai.architecture import WorkItemFamilyDefinition, WorkItemQueueDirs

    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    family = WorkItemFamilyDefinition(
        family_id="custom_exec",
        plane=Plane.EXECUTION,
        entry_key="custom_exec",
        display_name="Custom Exec",
        document_kind="custom_exec",
        runtime_relative_dir="custom/exec",
        file_extension=".json",
        schema_id="custom_exec_v1",
        document_adapter_id="custom_exec_adapter",
        queue_dirs=WorkItemQueueDirs(
            queue="custom/exec/queue",
            active="custom/exec/active",
            done="custom/exec/done",
            blocked="custom/exec/blocked",
        ),
        lifecycle_states=("queue", "active", "done", "blocked"),
        claimable_state="queue",
        active_state="active",
        done_state="done",
        blocked_state="blocked",
        closure_blocking_states=("queue", "active", "blocked"),
        id_field="custom_exec_id",
        created_at_field="created_at",
        lineage_fields=(),
        one_active_policy="family",
    )
    queue_dir = paths.runtime_root / family.queue_dirs.queue
    queue_dir.mkdir(parents=True, exist_ok=True)
    source = queue_dir / "exec-custom-1.json"
    source.write_text(
        '{"custom_exec_id":"exec-custom-1","created_at":"2026-05-19T12:00:00+00:00"}\n',
        encoding="utf-8",
    )
    policy = PlaneQueueClaimPolicyDefinition(
        policy_id="execution.custom_exec",
        plane=Plane.EXECUTION,
        family_order=("custom_exec",),
        closure_lineage_policy="defer_unrelated",
        empty_behavior="idle",
    )

    claim = claim_next_for_plane(
        paths,
        Plane.EXECUTION,
        queue_claim_policy=policy,
        work_item_families=(family,),
    )

    assert claim is not None
    assert claim.family_id == "custom_exec"
    assert claim.work_item_id == "exec-custom-1"
    assert claim.plane is Plane.EXECUTION
    assert claim.path.is_file()
