"""Selected lifecycle reconciliation projection over immutable runtime state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from millrace.contracts.compiled_plan import (
    FanoutDeclaration,
    JoinDeclaration,
    SelectedCompiledPlan,
)
from millrace.contracts.fingerprints import AuthorityFingerprint
from millrace.contracts.state import RuntimeState
from millrace.contracts.transition import (
    FanoutFromArtifact,
    JoinFromArtifact,
    TransitionContext,
)
from millrace.kernel.fanout_policy import (
    PolicyAssessment,
    artifact_relevant_to_fanout,
    assess_fanout,
    sorted_artifacts,
    source_context_for_artifact,
)
from millrace.kernel.join_policy import (
    JoinGroup,
    assess_join_group,
    canonical_correlation_identity,
    join_groups_for_declaration,
)

LifecycleCandidateKind = Literal["fanout", "join"]

_SCHEDULER_ID_DOMAIN = b"millrace-selected-lifecycle-scheduler-v1\0"
_SOURCE_CLOSED_POLICY = "source_closed"
_ACCEPTED_TERMINAL_POLICY = "accepted_terminal_observation"


@dataclass(frozen=True, slots=True)
class LifecycleDiagnostic:
    reason_code: str
    kind: LifecycleCandidateKind | None = None
    plan_fingerprint: AuthorityFingerprint | None = None
    declaration_id: str | None = None
    source_artifact_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectedLifecycleCandidate:
    kind: LifecycleCandidateKind
    plan_fingerprint: AuthorityFingerprint
    declaration_id: str
    source_artifact_id: str
    transition_input: FanoutFromArtifact | JoinFromArtifact
    transition_context: TransitionContext
    correlation_identity: str | None = None


@dataclass(frozen=True, slots=True)
class LifecycleProjection:
    candidate: ProjectedLifecycleCandidate | None
    diagnostics: tuple[LifecycleDiagnostic, ...] = ()


def project_next_lifecycle_transition(state: RuntimeState) -> LifecycleProjection:
    """Project one selected lifecycle transition or a state-corruption diagnostic."""
    authority_diagnostic = _artifact_authority_diagnostic(state)
    if authority_diagnostic is not None:
        return _diagnostic_projection(authority_diagnostic)
    for plan_fingerprint, selected_plan in _selected_plans(state):
        fanout_projection = _project_fanout(
            state,
            plan_fingerprint=plan_fingerprint,
            selected_plan=selected_plan,
        )
        if fanout_projection.candidate is not None or fanout_projection.diagnostics:
            return fanout_projection
        join_projection = _project_join(
            state,
            plan_fingerprint=plan_fingerprint,
            selected_plan=selected_plan,
        )
        if join_projection.candidate is not None or join_projection.diagnostics:
            return join_projection
    return LifecycleProjection(candidate=None)


def _project_fanout(
    state: RuntimeState,
    *,
    plan_fingerprint: AuthorityFingerprint,
    selected_plan: SelectedCompiledPlan,
) -> LifecycleProjection:
    for fanout in sorted(
        selected_plan.fanout_declarations,
        key=lambda declaration: str(declaration.id),
    ):
        if fanout.source_state_policy not in {
            _ACCEPTED_TERMINAL_POLICY,
            _SOURCE_CLOSED_POLICY,
        }:
            continue
        projects_candidate = fanout.source_state_policy == _SOURCE_CLOSED_POLICY
        for artifact in sorted_artifacts(state):
            if not artifact_relevant_to_fanout(artifact, fanout):
                continue
            source_context = source_context_for_artifact(state, artifact)
            if isinstance(source_context, PolicyAssessment):
                return _diagnostic_projection(
                    _diagnostic_from_owner(
                        source_context,
                        kind="fanout",
                        plan_fingerprint=plan_fingerprint,
                        declaration_id=str(fanout.id),
                    )
                )
            if (
                source_context.run.run_ref.plan_ref.authority_fingerprint
                != plan_fingerprint
            ):
                continue
            assessment = assess_fanout(state, source_context, fanout)
            if assessment.status == "partial_or_corrupt":
                return _diagnostic_projection(
                    _diagnostic_from_owner(
                        assessment,
                        kind="fanout",
                        plan_fingerprint=plan_fingerprint,
                        declaration_id=str(fanout.id),
                    )
                )
            if not projects_candidate:
                continue
            if assessment.status in {"not_ready", "complete"}:
                continue
            return LifecycleProjection(
                candidate=_fanout_candidate(
                    plan_fingerprint=plan_fingerprint,
                    fanout=fanout,
                    source_artifact_id=artifact.artifact_id,
                )
            )
    return LifecycleProjection(candidate=None)


def _project_join(
    state: RuntimeState,
    *,
    plan_fingerprint: AuthorityFingerprint,
    selected_plan: SelectedCompiledPlan,
) -> LifecycleProjection:
    for join in sorted(
        selected_plan.join_declarations,
        key=lambda declaration: str(declaration.id),
    ):
        group_result = join_groups_for_declaration(
            state,
            selected_plan=selected_plan,
            plan_fingerprint=plan_fingerprint,
            join=join,
        )
        if isinstance(group_result, PolicyAssessment):
            return _diagnostic_projection(
                _diagnostic_from_owner(
                    group_result,
                    kind="join",
                    plan_fingerprint=plan_fingerprint,
                    declaration_id=str(join.id),
                )
            )
        for group in sorted(
            group_result,
            key=lambda item: (
                _join_source_artifact_id(item),
                canonical_correlation_identity(item.correlation),
            ),
        ):
            assessment = assess_join_group(
                state,
                selected_plan=selected_plan,
                plan_fingerprint=plan_fingerprint,
                join=join,
                group=group,
            )
            if assessment.status == "not_ready":
                continue
            if assessment.status == "partial_or_corrupt":
                return _diagnostic_projection(
                    _join_diagnostic_from_owner(
                        assessment,
                        plan_fingerprint=plan_fingerprint,
                        join=join,
                    )
                )
            if assessment.status == "complete":
                continue
            source_artifact_id = _join_source_artifact_id(group)
            return LifecycleProjection(
                candidate=_join_candidate(
                    plan_fingerprint=plan_fingerprint,
                    join=join,
                    source_artifact_id=source_artifact_id,
                    correlation_identity=canonical_correlation_identity(
                        group.correlation
                    ),
                )
            )
    return LifecycleProjection(candidate=None)


def _selected_plans(
    state: RuntimeState,
) -> tuple[tuple[AuthorityFingerprint, SelectedCompiledPlan], ...]:
    return tuple(
        (fingerprint, admitted.selected_plan)
        for fingerprint, admitted in sorted(
            state.admitted_plans.items(),
            key=lambda item: item[0],
        )
    )


def _artifact_authority_diagnostic(state: RuntimeState) -> LifecycleDiagnostic | None:
    for artifact in sorted_artifacts(state):
        source_context = source_context_for_artifact(state, artifact)
        if isinstance(source_context, PolicyAssessment):
            return _diagnostic_from_owner(source_context)
    return None


def _join_source_artifact_id(group: JoinGroup) -> str:
    artifact_ids = sorted(
        evidence.artifact.artifact_id
        for evidences in group.evidence_by_schema.values()
        for evidence in evidences
    )
    return artifact_ids[0] if artifact_ids else group.bundle_artifact.artifact_id


def _fanout_candidate(
    *,
    plan_fingerprint: AuthorityFingerprint,
    fanout: FanoutDeclaration,
    source_artifact_id: str,
) -> ProjectedLifecycleCandidate:
    suffix = _stable_suffix(
        kind="fanout",
        plan_fingerprint=plan_fingerprint,
        declaration_id=str(fanout.id),
        source_artifact_id=source_artifact_id,
    )
    input_id = _scheduler_input_id("fanout", suffix)
    return ProjectedLifecycleCandidate(
        kind="fanout",
        plan_fingerprint=plan_fingerprint,
        declaration_id=str(fanout.id),
        source_artifact_id=source_artifact_id,
        transition_input=FanoutFromArtifact(
            input_id=input_id,
            fanout_id=str(fanout.id),
            source_artifact_id=source_artifact_id,
        ),
        transition_context=_scheduler_context("fanout", suffix),
    )


def _join_candidate(
    *,
    plan_fingerprint: AuthorityFingerprint,
    join: JoinDeclaration,
    source_artifact_id: str,
    correlation_identity: str,
) -> ProjectedLifecycleCandidate:
    suffix = _stable_suffix(
        kind="join",
        plan_fingerprint=plan_fingerprint,
        declaration_id=str(join.id),
        source_artifact_id=source_artifact_id,
        correlation_identity=correlation_identity,
    )
    input_id = _scheduler_input_id("join", suffix)
    return ProjectedLifecycleCandidate(
        kind="join",
        plan_fingerprint=plan_fingerprint,
        declaration_id=str(join.id),
        source_artifact_id=source_artifact_id,
        transition_input=JoinFromArtifact(
            input_id=input_id,
            join_id=str(join.id),
            source_artifact_id=source_artifact_id,
        ),
        transition_context=_scheduler_context("join", suffix),
        correlation_identity=correlation_identity,
    )


def _scheduler_input_id(kind: LifecycleCandidateKind, suffix: str) -> str:
    return f"cli:run.daemon:lifecycle:{kind}:{suffix}"


def _scheduler_context(kind: LifecycleCandidateKind, suffix: str) -> TransitionContext:
    return TransitionContext(
        transition_id=f"transition:cli:run.daemon:lifecycle:{kind}:{suffix}",
        work_item_id=f"lifecycle-work:{kind}:{suffix}",
        activation_id=f"lifecycle-activation:{kind}:{suffix}",
        run_id=f"lifecycle-run:{kind}:{suffix}",
        claim_id=f"lifecycle-claim:{kind}:{suffix}",
        fencing_token=f"lifecycle-fence:{kind}:{suffix}",
    )


def _stable_suffix(
    *,
    kind: LifecycleCandidateKind,
    plan_fingerprint: AuthorityFingerprint,
    declaration_id: str,
    source_artifact_id: str,
    correlation_identity: str | None = None,
) -> str:
    payload = {
        "kind": kind,
        "plan_fingerprint": plan_fingerprint,
        "declaration_id": declaration_id,
        "source_artifact_id": source_artifact_id,
        "correlation_identity": correlation_identity,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return sha256(_SCHEDULER_ID_DOMAIN + serialized).hexdigest()[:32]


def _diagnostic_projection(diagnostic: LifecycleDiagnostic) -> LifecycleProjection:
    return LifecycleProjection(candidate=None, diagnostics=(diagnostic,))


def _join_diagnostic_from_owner(
    assessment: PolicyAssessment,
    *,
    plan_fingerprint: AuthorityFingerprint,
    join: JoinDeclaration,
) -> LifecycleDiagnostic:
    reason_code = assessment.reason_code or "join_partial_state"
    return _diagnostic(
        reason_code,
        kind="join",
        plan_fingerprint=plan_fingerprint,
        declaration_id=str(join.id),
        source_artifact_id=assessment.source_artifact_id,
        detail=assessment.detail,
    )


def _diagnostic_from_owner(
    assessment: PolicyAssessment,
    *,
    kind: LifecycleCandidateKind | None = None,
    plan_fingerprint: AuthorityFingerprint | None = None,
    declaration_id: str | None = None,
) -> LifecycleDiagnostic:
    return _diagnostic(
        assessment.reason_code or "lifecycle_partial_state",
        kind=kind,
        plan_fingerprint=plan_fingerprint,
        declaration_id=declaration_id,
        source_artifact_id=assessment.source_artifact_id,
        detail=assessment.detail,
    )


def _diagnostic(
    reason_code: str,
    *,
    kind: LifecycleCandidateKind | None = None,
    plan_fingerprint: AuthorityFingerprint | None = None,
    declaration_id: str | None = None,
    source_artifact_id: str | None = None,
    detail: str | None = None,
) -> LifecycleDiagnostic:
    return LifecycleDiagnostic(
        reason_code=reason_code,
        kind=kind,
        plan_fingerprint=plan_fingerprint,
        declaration_id=declaration_id,
        source_artifact_id=source_artifact_id,
        detail=detail,
    )


__all__ = ("project_next_lifecycle_transition",)
