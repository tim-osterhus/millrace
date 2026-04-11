"""GoalSpec stage-support facade and plan-navigation helpers."""

from __future__ import annotations

from datetime import datetime

from ..compiler_models import FrozenLoopPlan, FrozenStagePlan
from ..config import EngineConfig
from ..contracts import SpecInterviewPolicy
from ..paths import RuntimePaths
from .goalspec import (
    CompletionManifestDraftExecutionResult,
    GoalIntakeExecutionResult,
    ObjectiveProfileSyncExecutionResult,
    SpecInterviewExecutionResult,
    SpecReviewRemediationExecutionResult,
    SpecReviewExecutionResult,
    SpecSynthesisExecutionResult,
)
from .goalspec_completion_manifest_draft import (
    execute_completion_manifest_draft as _execute_completion_manifest_draft,
)
from .goalspec_goal_intake import execute_goal_intake as _execute_goal_intake
from .goalspec_helpers import (
    GoalSpecExecutionError,
)
from .goalspec_objective_profile_sync import (
    execute_objective_profile_sync as _execute_objective_profile_sync,
)
from .goalspec_spec_interview import execute_spec_interview as _execute_spec_interview
from .goalspec_spec_review import execute_spec_review as _execute_spec_review
from .goalspec_spec_review import execute_spec_review_remediation as _execute_spec_review_remediation
from .goalspec_spec_synthesis import execute_spec_synthesis as _execute_spec_synthesis
from .state import ResearchCheckpoint


def research_stage_for_node(plan: FrozenLoopPlan, node_id: str) -> FrozenStagePlan:
    """Return one stage plan by node id."""

    for stage in plan.stages:
        if stage.node_id == node_id:
            return stage
    raise GoalSpecExecutionError(f"compiled research plan is missing stage node {node_id}")


def next_stage_for_success(plan: FrozenLoopPlan, node_id: str) -> FrozenStagePlan | None:
    """Return the normal-success successor stage for one node."""

    for transition in sorted(plan.transitions, key=lambda item: (-item.priority, item.edge_id)):
        if transition.from_node_id != node_id:
            continue
        if "success" not in transition.on_outcomes:
            continue
        if transition.to_node_id is None:
            return None
        return research_stage_for_node(plan, transition.to_node_id)
    raise GoalSpecExecutionError(f"compiled research plan has no success transition from {node_id}")


def execute_goal_intake(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    emitted_at: datetime | None = None,
) -> GoalIntakeExecutionResult:
    """Delegate Goal Intake execution to the dedicated tranche-one module."""

    return _execute_goal_intake(paths, checkpoint, run_id=run_id, emitted_at=emitted_at)


def execute_objective_profile_sync(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    emitted_at: datetime | None = None,
) -> ObjectiveProfileSyncExecutionResult:
    """Delegate objective-profile sync execution to the dedicated tranche-one module."""

    return _execute_objective_profile_sync(paths, checkpoint, run_id=run_id, emitted_at=emitted_at)


def execute_completion_manifest_draft(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    emitted_at: datetime | None = None,
) -> CompletionManifestDraftExecutionResult:
    """Delegate completion-manifest drafting to the dedicated tranche-one module."""

    return _execute_completion_manifest_draft(paths, checkpoint, run_id=run_id, emitted_at=emitted_at)


def execute_spec_synthesis(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    completion_manifest=None,
    emitted_at: datetime | None = None,
) -> SpecSynthesisExecutionResult:
    """Delegate Spec Synthesis execution to the dedicated later-stage module."""

    return _execute_spec_synthesis(
        paths,
        checkpoint,
        run_id=run_id,
        completion_manifest=completion_manifest,
        emitted_at=emitted_at,
    )


def execute_spec_interview(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    policy: SpecInterviewPolicy,
    emitted_at: datetime | None = None,
) -> SpecInterviewExecutionResult:
    """Delegate Spec Interview execution to the dedicated later-stage module."""

    return _execute_spec_interview(paths, checkpoint, run_id=run_id, policy=policy, emitted_at=emitted_at)


def execute_spec_review(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    emitted_at: datetime | None = None,
    config: EngineConfig | None = None,
    stage_plan: FrozenStagePlan | None = None,
) -> SpecReviewExecutionResult:
    """Delegate Spec Review execution to the dedicated later-stage module."""

    return _execute_spec_review(
        paths,
        checkpoint,
        run_id=run_id,
        emitted_at=emitted_at,
        config=config,
        stage_plan=stage_plan,
    )


def execute_spec_review_remediation(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    emitted_at: datetime | None = None,
    config: EngineConfig | None = None,
) -> SpecReviewRemediationExecutionResult:
    """Delegate bounded Mechanic repair for blocked Spec Review outcomes."""

    return _execute_spec_review_remediation(
        paths,
        checkpoint,
        run_id=run_id,
        emitted_at=emitted_at,
        config=config,
    )
