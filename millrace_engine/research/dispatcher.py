"""Research-mode selection and compiled dispatch helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

from pydantic import field_validator, model_validator

from ..compiler import CompileResult, CompileStatus, FrozenLoopPlan, FrozenRunCompiler, FrozenStagePlan
from ..contracts import ContractModel, PersistedObjectKind, RegistryObjectRef, ResearchMode, ResearchStatus, StageType
from ..paths import RuntimePaths
from .queues import ResearchQueueDiscovery
from .state import ResearchCheckpoint, ResearchQueueFamily, ResearchQueueSnapshot, ResearchRuntimeMode


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_required_text(value: str, *, field_name: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError(f"{field_name} may not be empty")
    return normalized


def _mode_ref(object_id: str) -> RegistryObjectRef:
    return RegistryObjectRef(
        kind=PersistedObjectKind.MODE,
        id=object_id,
        version="1.0.0",
    )


RESEARCH_GOALSPEC_MODE_REF = _mode_ref("mode.research_goalspec")
RESEARCH_INCIDENT_MODE_REF = _mode_ref("mode.research_incident")
RESEARCH_AUDIT_MODE_REF = _mode_ref("mode.research_audit")

_MODE_REF_BY_RUNTIME_MODE: dict[ResearchRuntimeMode, RegistryObjectRef] = {
    ResearchRuntimeMode.GOALSPEC: RESEARCH_GOALSPEC_MODE_REF,
    ResearchRuntimeMode.INCIDENT: RESEARCH_INCIDENT_MODE_REF,
    ResearchRuntimeMode.AUDIT: RESEARCH_AUDIT_MODE_REF,
}
_AUTO_GROUP_ORDER = ("incident", "goalspec", "audit")
_ENTRY_STAGE_TYPE_BY_NODE_ID: dict[str, StageType] = {
    "goal_intake": StageType.GOAL_INTAKE,
    "objective_profile_sync": StageType.OBJECTIVE_PROFILE_SYNC,
    "spec_synthesis": StageType.SPEC_SYNTHESIS,
    "spec_review": StageType.SPEC_REVIEW,
    "taskmaster": StageType.TASKMASTER,
    "incident_intake": StageType.INCIDENT_INTAKE,
    "incident_resolve": StageType.INCIDENT_RESOLVE,
    "incident_archive": StageType.INCIDENT_ARCHIVE,
    "audit_intake": StageType.AUDIT_INTAKE,
    "audit_validate": StageType.AUDIT_VALIDATE,
    "audit_gatekeeper": StageType.AUDIT_GATEKEEPER,
}


class ResearchDispatchError(RuntimeError):
    """Raised when the research plane cannot resolve or compile a dispatch."""


class UnsupportedResearchQueueCombinationError(ResearchDispatchError):
    """Raised when AUTO dispatch sees an ambiguous queue combination."""


class CompiledResearchDispatchError(ResearchDispatchError):
    """Raised when a compiled research mode cannot produce a usable plan."""


class ResearchDispatchSelection(ContractModel):
    """Explainable selection facts for one research dispatch attempt."""

    configured_mode: ResearchMode
    runtime_mode: ResearchRuntimeMode
    selected_mode_ref: RegistryObjectRef
    queue_snapshot: ResearchQueueSnapshot
    reason: str

    @field_validator("runtime_mode", mode="before")
    @classmethod
    def normalize_runtime_mode(
        cls,
        value: ResearchRuntimeMode | ResearchMode | str,
    ) -> ResearchRuntimeMode:
        return ResearchRuntimeMode.from_value(value)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="reason")

    @model_validator(mode="after")
    def validate_mode_alignment(self) -> "ResearchDispatchSelection":
        if self.runtime_mode is ResearchRuntimeMode.STUB:
            raise ValueError("dispatch selection may not target stub mode")
        expected_ref = _MODE_REF_BY_RUNTIME_MODE.get(self.runtime_mode)
        if expected_ref is None:
            raise ValueError(f"dispatch selection cannot resolve mode ref for {self.runtime_mode.value}")
        if self.selected_mode_ref != expected_ref:
            raise ValueError("selected_mode_ref must match runtime_mode")
        return self


class CompiledResearchDispatch(ContractModel):
    """Successful compiled research dispatch with frozen-plan facts."""

    selection: ResearchDispatchSelection
    queue_discovery: ResearchQueueDiscovery
    compile_result: CompileResult

    @model_validator(mode="after")
    def validate_compile_result(self) -> "CompiledResearchDispatch":
        if self.compile_result.status is not CompileStatus.OK:
            raise ValueError("compiled research dispatch requires a successful compile result")
        if self.compile_result.plan is None:
            raise ValueError("compiled research dispatch requires a frozen plan")
        if self.compile_result.plan.content.research_plan is None:
            raise ValueError("compiled research dispatch requires a compiled research plan")
        if self.compile_result.selection_ref != self.selection.selected_mode_ref:
            raise ValueError("compile_result.selection_ref must match the selected research mode ref")
        return self

    @property
    def run_id(self) -> str:
        return self.compile_result.run_id

    @property
    def research_plan(self) -> FrozenLoopPlan:
        plan = self.compile_result.plan
        if plan is None or plan.content.research_plan is None:
            raise CompiledResearchDispatchError("compiled research dispatch is missing a frozen research plan")
        return plan.content.research_plan

    @property
    def entry_stage(self) -> FrozenStagePlan:
        entry_node_id = self.research_plan.entry_node_id
        for stage in self.research_plan.stages:
            if stage.node_id == entry_node_id:
                return stage
        raise CompiledResearchDispatchError(
            f"compiled research plan is missing its entry node {entry_node_id}"
        )

    def checkpoint(self, *, started_at: datetime | None = None) -> ResearchCheckpoint:
        started_at = started_at or _utcnow()
        entry_stage = self.entry_stage
        return ResearchCheckpoint(
            checkpoint_id=self.run_id,
            mode=self.selection.runtime_mode,
            status=ResearchStatus(entry_stage.running_status),
            loop_ref=self.research_plan.requested_ref,
            node_id=entry_stage.node_id,
            stage_kind_id=entry_stage.kind_id,
            attempt=0,
            started_at=started_at,
            updated_at=started_at,
        )


def resolve_research_dispatch_selection(
    configured_mode: ResearchMode,
    queue_discovery: ResearchQueueDiscovery,
    *,
    scanned_at: datetime | None = None,
) -> ResearchDispatchSelection | None:
    """Resolve one explainable research dispatch selection from config and queues."""

    scanned_at = scanned_at or _utcnow()
    if configured_mode is ResearchMode.STUB:
        return None

    if configured_mode is not ResearchMode.AUTO:
        runtime_mode = ResearchRuntimeMode.from_value(configured_mode)
        selected_family = _forced_selected_family(runtime_mode, queue_discovery)
        return ResearchDispatchSelection(
            configured_mode=configured_mode,
            runtime_mode=runtime_mode,
            selected_mode_ref=_MODE_REF_BY_RUNTIME_MODE[runtime_mode],
            queue_snapshot=queue_discovery.to_snapshot(
                last_scanned_at=scanned_at,
                selected_family=selected_family,
            ),
            reason="forced-by-config",
        )

    auto_candidate = _resolve_auto_candidate(queue_discovery)
    if auto_candidate is None:
        return None

    runtime_mode, selected_family, reason = auto_candidate
    return ResearchDispatchSelection(
        configured_mode=configured_mode,
        runtime_mode=runtime_mode,
        selected_mode_ref=_MODE_REF_BY_RUNTIME_MODE[runtime_mode],
        queue_snapshot=queue_discovery.to_snapshot(
            last_scanned_at=scanned_at,
            selected_family=selected_family,
        ),
        reason=reason,
    )


def compile_research_dispatch(
    paths: RuntimePaths,
    selection: ResearchDispatchSelection,
    *,
    run_id: str,
    compiler: FrozenRunCompiler | None = None,
    queue_discovery: ResearchQueueDiscovery | None = None,
    resolve_assets: bool = True,
) -> CompiledResearchDispatch:
    """Compile one selected research mode into a frozen research dispatch."""

    compiler = compiler or FrozenRunCompiler(paths)
    queue_discovery = queue_discovery or ResearchQueueDiscovery(
        families=(),
    )
    compile_result = compiler.compile_mode(
        selection.selected_mode_ref,
        run_id=run_id,
        resolve_assets=resolve_assets,
    )
    if compile_result.status is not CompileStatus.OK:
        diagnostic_summary = "; ".join(diagnostic.message for diagnostic in compile_result.diagnostics) or "unknown"
        raise CompiledResearchDispatchError(
            f"research mode compile failed for {selection.selected_mode_ref.id}: {diagnostic_summary}"
        )
    return CompiledResearchDispatch(
        selection=selection,
        queue_discovery=queue_discovery,
        compile_result=compile_result,
    )


def entry_stage_type_for_dispatch(dispatch: CompiledResearchDispatch) -> StageType:
    """Return the public stage type for the compiled dispatch entry node."""

    try:
        return _ENTRY_STAGE_TYPE_BY_NODE_ID[dispatch.entry_stage.node_id]
    except KeyError as exc:
        raise CompiledResearchDispatchError(
            f"compiled research entry node {dispatch.entry_stage.node_id} has no public stage mapping"
        ) from exc


def _forced_selected_family(
    runtime_mode: ResearchRuntimeMode,
    queue_discovery: ResearchQueueDiscovery,
) -> ResearchQueueFamily | None:
    if runtime_mode is ResearchRuntimeMode.GOALSPEC:
        return _ready_or_none(queue_discovery, ResearchQueueFamily.GOALSPEC)
    if runtime_mode is ResearchRuntimeMode.INCIDENT:
        if queue_discovery.family_scan(ResearchQueueFamily.INCIDENT).ready:
            return ResearchQueueFamily.INCIDENT
        return _ready_or_none(queue_discovery, ResearchQueueFamily.BLOCKER)
    if runtime_mode is ResearchRuntimeMode.AUDIT:
        return _ready_or_none(queue_discovery, ResearchQueueFamily.AUDIT)
    return None


def _ready_or_none(
    queue_discovery: ResearchQueueDiscovery,
    family: ResearchQueueFamily,
) -> ResearchQueueFamily | None:
    return family if queue_discovery.family_scan(family).ready else None


def _resolve_auto_candidate(
    queue_discovery: ResearchQueueDiscovery,
) -> tuple[ResearchRuntimeMode, ResearchQueueFamily, str] | None:
    incident_ready = queue_discovery.family_scan(ResearchQueueFamily.INCIDENT).ready
    blocker_ready = queue_discovery.family_scan(ResearchQueueFamily.BLOCKER).ready
    goalspec_ready = queue_discovery.family_scan(ResearchQueueFamily.GOALSPEC).ready
    audit_ready = queue_discovery.family_scan(ResearchQueueFamily.AUDIT).ready

    grouped_candidates: dict[str, tuple[ResearchRuntimeMode, ResearchQueueFamily, str]] = {}
    if incident_ready or blocker_ready:
        grouped_candidates["incident"] = (
            ResearchRuntimeMode.INCIDENT,
            ResearchQueueFamily.BLOCKER,
            "incident-queue-ready" if incident_ready else "blocker-queue-ready",
        )
    if goalspec_ready:
        grouped_candidates["goalspec"] = (
            ResearchRuntimeMode.GOALSPEC,
            ResearchQueueFamily.GOALSPEC,
            "goal-or-spec-queue-ready",
        )
    if audit_ready:
        grouped_candidates["audit"] = (ResearchRuntimeMode.AUDIT, ResearchQueueFamily.AUDIT, "audit-queue-ready")

    if not grouped_candidates:
        return None
    if len(grouped_candidates) > 1:
        ready_families = ", ".join(family.value for family in queue_discovery.ready_families)
        grouped = ", ".join(name for name in _AUTO_GROUP_ORDER if name in grouped_candidates)
        raise UnsupportedResearchQueueCombinationError(
            "auto research dispatch does not support simultaneous ready queue groups: "
            f"{grouped} (families: {ready_families})"
        )
    candidate_group = next(iter(grouped_candidates.values()))
    return candidate_group


class ResearchStage:
    """Lightweight research-stage marker base for packaged research handler refs."""

    stage_type: ClassVar[StageType]
    running_status: ClassVar[ResearchStatus]
    success_statuses: ClassVar[tuple[ResearchStatus, ...]]

    @classmethod
    def entry_status(cls) -> ResearchStatus:
        return cls.running_status


class GoalIntakeStage(ResearchStage):
    stage_type = StageType.GOAL_INTAKE
    running_status = ResearchStatus.GOAL_INTAKE_RUNNING
    success_statuses = (ResearchStatus.IDLE,)


class ObjectiveProfileSyncStage(ResearchStage):
    stage_type = StageType.OBJECTIVE_PROFILE_SYNC
    running_status = ResearchStatus.OBJECTIVE_PROFILE_SYNC_RUNNING
    success_statuses = (ResearchStatus.IDLE,)


class SpecSynthesisStage(ResearchStage):
    stage_type = StageType.SPEC_SYNTHESIS
    running_status = ResearchStatus.SPEC_SYNTHESIS_RUNNING
    success_statuses = (ResearchStatus.IDLE,)


class SpecReviewStage(ResearchStage):
    stage_type = StageType.SPEC_REVIEW
    running_status = ResearchStatus.SPEC_REVIEW_RUNNING
    success_statuses = (ResearchStatus.IDLE,)


class TaskmasterStage(ResearchStage):
    stage_type = StageType.TASKMASTER
    running_status = ResearchStatus.TASKMASTER_RUNNING
    success_statuses = (ResearchStatus.IDLE,)


class IncidentIntakeStage(ResearchStage):
    stage_type = StageType.INCIDENT_INTAKE
    running_status = ResearchStatus.INCIDENT_INTAKE_RUNNING
    success_statuses = (ResearchStatus.IDLE,)


class IncidentResolveStage(ResearchStage):
    stage_type = StageType.INCIDENT_RESOLVE
    running_status = ResearchStatus.INCIDENT_RESOLVE_RUNNING
    success_statuses = (ResearchStatus.IDLE,)


class IncidentArchiveStage(ResearchStage):
    stage_type = StageType.INCIDENT_ARCHIVE
    running_status = ResearchStatus.INCIDENT_ARCHIVE_RUNNING
    success_statuses = (ResearchStatus.IDLE,)


class AuditIntakeStage(ResearchStage):
    stage_type = StageType.AUDIT_INTAKE
    running_status = ResearchStatus.AUDIT_INTAKE_RUNNING
    success_statuses = (ResearchStatus.IDLE,)


class AuditValidateStage(ResearchStage):
    stage_type = StageType.AUDIT_VALIDATE
    running_status = ResearchStatus.AUDIT_VALIDATE_RUNNING
    success_statuses = (ResearchStatus.AUDIT_RUNNING,)


class AuditGatekeeperStage(ResearchStage):
    stage_type = StageType.AUDIT_GATEKEEPER
    running_status = ResearchStatus.AUDIT_RUNNING
    success_statuses = (ResearchStatus.AUDIT_PASS,)


__all__ = [
    "AuditGatekeeperStage",
    "AuditIntakeStage",
    "AuditValidateStage",
    "CompiledResearchDispatch",
    "CompiledResearchDispatchError",
    "GoalIntakeStage",
    "IncidentArchiveStage",
    "IncidentIntakeStage",
    "IncidentResolveStage",
    "ObjectiveProfileSyncStage",
    "RESEARCH_AUDIT_MODE_REF",
    "RESEARCH_GOALSPEC_MODE_REF",
    "RESEARCH_INCIDENT_MODE_REF",
    "ResearchDispatchError",
    "ResearchDispatchSelection",
    "ResearchStage",
    "SpecReviewStage",
    "SpecSynthesisStage",
    "TaskmasterStage",
    "UnsupportedResearchQueueCombinationError",
    "compile_research_dispatch",
    "entry_stage_type_for_dispatch",
    "resolve_research_dispatch_selection",
]
