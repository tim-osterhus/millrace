"""Typed policy fact collection and hook scaffolding."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Mapping

from pydantic import ConfigDict, Field, field_validator, model_validator

from ..contracts import (
    ContractModel,
    ControlPlane,
    HeadlessPermissionProfile,
    ModePolicyToggles,
    OptionalPermissionProfileModel,
    OutlinePolicy,
    ReasoningEffort,
    RegistryObjectRef,
    ResearchParticipationMode,
    RunnerKind,
    StageType,
    TaskCard,
)

if TYPE_CHECKING:
    from ..contracts import ExecutionStatus


POLICY_CYCLE_NODE_ID = "__cycle_boundary__"


class PolicyHookError(ValueError):
    """Raised when a policy hook cannot collect the facts it requires."""


class PolicyHook(str, Enum):
    """Supported policy-evaluation hook points."""

    CYCLE_BOUNDARY = "cycle_boundary"
    PRE_STAGE = "pre_stage"
    POST_STAGE = "post_stage"


class PolicyDecision(str, Enum):
    """Current scaffold decision surface for policy hooks."""

    NOT_EVALUATED = "not_evaluated"
    PASS = "pass"
    ENV_BLOCKED = "env_blocked"
    POLICY_BLOCKED = "policy_blocked"
    NET_WAIT = "net_wait"


class PolicyEvidenceKind(str, Enum):
    """Typed evidence sources attached to one policy evaluation."""

    FROZEN_PLAN = "frozen_plan"
    STAGE_BINDING = "stage_binding"
    RUNTIME_STATE = "runtime_state"
    STAGE_RESULT = "stage_result"
    POLICY_STATE = "policy_state"
    TRANSPORT_CHECK = "transport_check"
    NETWORK_GUARD = "network_guard"
    INTEGRATION_POLICY = "integration_policy"
    OUTAGE_POLICY = "outage_policy"
    OUTAGE_PROBE = "outage_probe"
    USAGE_BUDGET = "usage_budget"
    USAGE_SAMPLE = "usage_sample"
    PACING_POLICY = "pacing_policy"


class PolicyTaskFacts(ContractModel):
    """Task facts made available to policy hooks."""

    task_id: str
    title: str
    spec_id: str | None = None
    complexity: str | None = None
    gates: tuple[str, ...] = ()
    integration_preference: str | None = None

    @field_validator("task_id", "title", "spec_id", "complexity", "integration_preference")
    @classmethod
    def validate_text(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            field_name = getattr(info, "field_name", "value")
            raise ValueError(f"{field_name} may not be empty")
        return normalized

    @field_validator("gates", mode="before")
    @classmethod
    def normalize_gates(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            token = str(item).strip().upper()
            if not token or token in seen:
                continue
            seen.add(token)
            normalized.append(token)
        return tuple(normalized)


class PolicyQueueFacts(ContractModel):
    """Queue-state facts available during hook evaluation."""

    backlog_depth: int = Field(ge=0)
    backlog_empty: bool
    active_task_id: str | None = None

    @field_validator("active_task_id")
    @classmethod
    def validate_active_task_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("active_task_id may not be empty")
        return normalized


class PolicyRuntimeFacts(ContractModel):
    """Runtime-state facts that do not come from the frozen plan."""

    execution_status: str

    @field_validator("execution_status")
    @classmethod
    def validate_execution_status(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("execution_status may not be empty")
        return normalized


class PolicyPlanFacts(ContractModel):
    """Frozen-plan facts exposed to policy hooks."""

    snapshot_id: str | None = None
    frozen_plan_id: str
    frozen_plan_hash: str
    selection_ref: RegistryObjectRef
    selected_mode_ref: RegistryObjectRef | None = None
    selected_execution_loop_ref: RegistryObjectRef | None = None
    research_participation: ResearchParticipationMode
    outline_policy: OutlinePolicy | None = None
    policy_toggles: ModePolicyToggles | None = None
    execution_node_ids: tuple[str, ...] = ()

    @field_validator("snapshot_id", "frozen_plan_id", "frozen_plan_hash")
    @classmethod
    def validate_text(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            field_name = getattr(info, "field_name", "value")
            raise ValueError(f"{field_name} may not be empty")
        return normalized

    @field_validator("execution_node_ids", mode="before")
    @classmethod
    def normalize_execution_node_ids(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            node_id = str(item).strip()
            if not node_id or node_id in seen:
                continue
            seen.add(node_id)
            normalized.append(node_id)
        return tuple(normalized)


class PolicyStageFacts(OptionalPermissionProfileModel):
    """Compile-time stage facts for a stage-scoped policy hook."""

    stage: StageType
    node_id: str
    kind_id: str
    stage_kind_ref: RegistryObjectRef
    model_profile_ref: RegistryObjectRef | None = None
    runner: RunnerKind | None = None
    model: str | None = None
    effort: ReasoningEffort | None = None
    permission_profile: HeadlessPermissionProfile | None = None
    allow_search: bool | None = None
    timeout_seconds: int | None = Field(default=None, ge=1)
    prompt_asset_ref: str | None = None
    running_status: str
    success_statuses: tuple[str, ...]
    terminal_statuses: tuple[str, ...]

    @field_validator("node_id", "kind_id", "model", "prompt_asset_ref", "running_status")
    @classmethod
    def validate_text(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            field_name = getattr(info, "field_name", "value")
            raise ValueError(f"{field_name} may not be empty")
        return normalized

    @field_validator("success_statuses", "terminal_statuses", mode="before")
    @classmethod
    def normalize_statuses(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return tuple(normalized)


class PolicyFactSnapshot(ContractModel):
    """Immutable fact bundle passed into a policy evaluator."""

    hook: PolicyHook
    plane: ControlPlane = ControlPlane.EXECUTION
    run_id: str
    routing_mode: str | None = None
    transition_history_count: int = Field(default=0, ge=0)
    plan: PolicyPlanFacts | None = None
    stage: PolicyStageFacts | None = None
    task: PolicyTaskFacts | None = None
    queue: PolicyQueueFacts
    runtime: PolicyRuntimeFacts
    stage_result_status: str | None = None
    stage_result_exit_code: int | None = Field(default=None, ge=0)

    @field_validator("run_id", "routing_mode", "stage_result_status")
    @classmethod
    def validate_text(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            field_name = getattr(info, "field_name", "value")
            raise ValueError(f"{field_name} may not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_hook_requirements(self) -> "PolicyFactSnapshot":
        if self.hook in {PolicyHook.PRE_STAGE, PolicyHook.POST_STAGE}:
            if self.plan is None:
                raise ValueError(f"{self.hook.value} hooks require frozen plan facts")
            if self.stage is None:
                raise ValueError(f"{self.hook.value} hooks require stage facts")
        if self.hook is PolicyHook.PRE_STAGE:
            if self.stage_result_status is not None or self.stage_result_exit_code is not None:
                raise ValueError("pre_stage hooks may not carry stage results")
        if self.hook is PolicyHook.POST_STAGE:
            if self.stage_result_status is None or self.stage_result_exit_code is None:
                raise ValueError("post_stage hooks require stage result facts")
        return self


class PolicyEvidence(ContractModel):
    """Structured evidence item attached to one policy evaluation."""

    kind: PolicyEvidenceKind
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("summary may not be empty")
        return normalized

    @field_validator("details", mode="before")
    @classmethod
    def normalize_details(cls, value: Mapping[str, Any] | None) -> dict[str, Any]:
        return {str(key): item for key, item in (value or {}).items()}


class PolicyEvaluationRecord(ContractModel):
    """One policy evaluation record, even when no concrete policy module runs yet."""

    evaluator: str
    hook: PolicyHook
    decision: PolicyDecision = PolicyDecision.NOT_EVALUATED
    facts: PolicyFactSnapshot
    evidence: tuple[PolicyEvidence, ...] = ()
    notes: tuple[str, ...] = ()

    @field_validator("evaluator")
    @classmethod
    def validate_evaluator(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("evaluator may not be empty")
        return normalized

    @field_validator("notes", mode="before")
    @classmethod
    def normalize_notes(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = " ".join(str(item).strip().split())
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return tuple(normalized)

    @model_validator(mode="after")
    def validate_alignment(self) -> "PolicyEvaluationRecord":
        if self.hook is not self.facts.hook:
            raise ValueError("policy evaluation hook must match fact snapshot hook")
        return self


PolicyEvaluator = Callable[[PolicyFactSnapshot], PolicyEvaluationRecord | None]


class PolicyHookRuntime:
    """Collect policy facts at deterministic execution boundaries."""

    def __init__(
        self,
        evaluators: Mapping[PolicyHook, tuple[PolicyEvaluator, ...] | list[PolicyEvaluator]] | None = None,
    ) -> None:
        self._evaluators = {
            hook: tuple(raw_evaluators)
            for hook, raw_evaluators in (evaluators or {}).items()
        }

    def evaluate_cycle_boundary(
        self,
        *,
        run_id: str,
        routing_mode: str | None,
        execution_status: ExecutionStatus | str,
        active_task: TaskCard | None,
        backlog_depth: int,
        transition_history_count: int,
        frozen_plan: Any | None,
        snapshot_id: str | None,
    ) -> tuple[PolicyEvaluationRecord, ...]:
        facts = PolicyFactSnapshot(
            hook=PolicyHook.CYCLE_BOUNDARY,
            run_id=run_id,
            routing_mode=routing_mode,
            transition_history_count=transition_history_count,
            plan=self._plan_facts(frozen_plan, snapshot_id=snapshot_id),
            task=self._task_facts(active_task),
            queue=self._queue_facts(active_task=active_task, backlog_depth=backlog_depth),
            runtime=self._runtime_facts(execution_status),
        )
        return self._evaluate(facts)

    def evaluate_pre_stage(
        self,
        *,
        run_id: str,
        routing_mode: str | None,
        execution_status: ExecutionStatus | str,
        active_task: TaskCard | None,
        backlog_depth: int,
        transition_history_count: int,
        frozen_plan: Any | None,
        snapshot_id: str | None,
        stage_type: StageType,
        node_id: str,
    ) -> tuple[PolicyEvaluationRecord, ...]:
        facts = PolicyFactSnapshot(
            hook=PolicyHook.PRE_STAGE,
            run_id=run_id,
            routing_mode=routing_mode,
            transition_history_count=transition_history_count,
            plan=self._require_plan_facts(frozen_plan, snapshot_id=snapshot_id, hook=PolicyHook.PRE_STAGE),
            stage=self._stage_facts(frozen_plan, stage_type=stage_type, node_id=node_id),
            task=self._task_facts(active_task),
            queue=self._queue_facts(active_task=active_task, backlog_depth=backlog_depth),
            runtime=self._runtime_facts(execution_status),
        )
        return self._evaluate(facts)

    def evaluate_post_stage(
        self,
        *,
        run_id: str,
        routing_mode: str | None,
        execution_status: ExecutionStatus | str,
        active_task: TaskCard | None,
        backlog_depth: int,
        transition_history_count: int,
        frozen_plan: Any | None,
        snapshot_id: str | None,
        stage_type: StageType,
        node_id: str,
        stage_result_status: str,
        stage_result_exit_code: int,
    ) -> tuple[PolicyEvaluationRecord, ...]:
        facts = PolicyFactSnapshot(
            hook=PolicyHook.POST_STAGE,
            run_id=run_id,
            routing_mode=routing_mode,
            transition_history_count=transition_history_count,
            plan=self._require_plan_facts(frozen_plan, snapshot_id=snapshot_id, hook=PolicyHook.POST_STAGE),
            stage=self._stage_facts(frozen_plan, stage_type=stage_type, node_id=node_id),
            task=self._task_facts(active_task),
            queue=self._queue_facts(active_task=active_task, backlog_depth=backlog_depth),
            runtime=self._runtime_facts(execution_status),
            stage_result_status=stage_result_status,
            stage_result_exit_code=stage_result_exit_code,
        )
        return self._evaluate(facts)

    def _evaluate(self, facts: PolicyFactSnapshot) -> tuple[PolicyEvaluationRecord, ...]:
        evaluators = self._evaluators.get(facts.hook, ())
        if not evaluators:
            return (self._scaffold_record(facts),)
        records: list[PolicyEvaluationRecord] = []
        for evaluator in evaluators:
            result = evaluator(facts)
            if result is None:
                continue
            records.append(result)
        if records:
            return tuple(records)
        return (self._scaffold_record(facts),)

    def _scaffold_record(self, facts: PolicyFactSnapshot) -> PolicyEvaluationRecord:
        evidence = list(self._default_evidence(facts))
        notes = ["No concrete policy evaluator is registered for this hook yet."]
        return PolicyEvaluationRecord(
            evaluator="policy_hook_scaffold",
            hook=facts.hook,
            facts=facts,
            evidence=tuple(evidence),
            notes=tuple(notes),
        )

    def _default_evidence(self, facts: PolicyFactSnapshot) -> tuple[PolicyEvidence, ...]:
        evidence: list[PolicyEvidence] = [
            PolicyEvidence(
                kind=PolicyEvidenceKind.RUNTIME_STATE,
                summary="Runtime state captured for policy hook evaluation.",
                details={
                    "execution_status": facts.runtime.execution_status,
                    "backlog_depth": facts.queue.backlog_depth,
                    "active_task_id": facts.queue.active_task_id,
                    "routing_mode": facts.routing_mode,
                },
            )
        ]
        if facts.plan is not None:
            evidence.append(
                PolicyEvidence(
                    kind=PolicyEvidenceKind.FROZEN_PLAN,
                    summary="Frozen-plan identity captured for policy evaluation.",
                    details={
                        "snapshot_id": facts.plan.snapshot_id,
                        "frozen_plan_id": facts.plan.frozen_plan_id,
                        "frozen_plan_hash": facts.plan.frozen_plan_hash,
                        "selection_ref": facts.plan.selection_ref.model_dump(mode="json"),
                    },
                )
            )
        if facts.stage is not None:
            evidence.append(
                PolicyEvidence(
                    kind=PolicyEvidenceKind.STAGE_BINDING,
                    summary="Compile-time stage binding captured for policy evaluation.",
                    details={
                        "node_id": facts.stage.node_id,
                        "stage": facts.stage.stage.value,
                        "kind_id": facts.stage.kind_id,
                        "runner": facts.stage.runner.value if facts.stage.runner is not None else None,
                        "model": facts.stage.model,
                        "allow_search": facts.stage.allow_search,
                        "timeout_seconds": facts.stage.timeout_seconds,
                    },
                )
            )
        if facts.stage_result_status is not None:
            evidence.append(
                PolicyEvidence(
                    kind=PolicyEvidenceKind.STAGE_RESULT,
                    summary="Observed stage result captured for post-stage policy evaluation.",
                    details={
                        "stage_result_status": facts.stage_result_status,
                        "stage_result_exit_code": facts.stage_result_exit_code,
                    },
                )
            )
        return tuple(evidence)

    def _task_facts(self, task: TaskCard | None) -> PolicyTaskFacts | None:
        if task is None:
            return None
        return PolicyTaskFacts(
            task_id=task.task_id,
            title=task.title,
            spec_id=task.spec_id,
            complexity=task.complexity,
            gates=task.gates,
            integration_preference=task.integration_preference,
        )

    def _queue_facts(self, *, active_task: TaskCard | None, backlog_depth: int) -> PolicyQueueFacts:
        return PolicyQueueFacts(
            backlog_depth=backlog_depth,
            backlog_empty=backlog_depth == 0,
            active_task_id=active_task.task_id if active_task is not None else None,
        )

    def _runtime_facts(self, execution_status: ExecutionStatus | str) -> PolicyRuntimeFacts:
        status_text = execution_status.value if hasattr(execution_status, "value") else str(execution_status)
        return PolicyRuntimeFacts(execution_status=status_text)

    def _require_plan_facts(
        self,
        frozen_plan: Any | None,
        *,
        snapshot_id: str | None,
        hook: PolicyHook,
    ) -> PolicyPlanFacts:
        plan = self._plan_facts(frozen_plan, snapshot_id=snapshot_id)
        if plan is None:
            raise PolicyHookError(f"{hook.value} requires an active frozen execution plan")
        return plan

    def _plan_facts(self, frozen_plan: Any | None, *, snapshot_id: str | None) -> PolicyPlanFacts | None:
        if frozen_plan is None:
            return None
        identity = getattr(frozen_plan, "identity", None)
        content = getattr(frozen_plan, "content", None)
        execution_plan = getattr(content, "execution_plan", None)
        if identity is None or content is None:
            raise PolicyHookError("frozen plan is missing identity or content")
        return PolicyPlanFacts(
            snapshot_id=snapshot_id,
            frozen_plan_id=identity.plan_id,
            frozen_plan_hash=identity.content_hash,
            selection_ref=content.selection_ref,
            selected_mode_ref=content.selected_mode_ref,
            selected_execution_loop_ref=content.selected_execution_loop_ref,
            research_participation=content.research_participation,
            outline_policy=content.outline_policy,
            policy_toggles=content.policy_toggles,
            execution_node_ids=(
                tuple(stage.node_id for stage in execution_plan.stages)
                if execution_plan is not None
                else ()
            ),
        )

    def _stage_facts(self, frozen_plan: Any | None, *, stage_type: StageType, node_id: str) -> PolicyStageFacts:
        if frozen_plan is None:
            raise PolicyHookError("stage-scoped policy hooks require an active frozen execution plan")
        content = getattr(frozen_plan, "content", None)
        execution_plan = getattr(content, "execution_plan", None)
        if execution_plan is None:
            raise PolicyHookError("frozen plan does not include an execution plan")
        for stage_plan in execution_plan.stages:
            if stage_plan.node_id != node_id:
                continue
            try:
                expected_stage_type = StageType(node_id)
            except ValueError as exc:
                raise PolicyHookError(f"frozen plan node {node_id} cannot map to a public execution stage") from exc
            if expected_stage_type is not stage_type:
                raise PolicyHookError(
                    f"stage-scoped policy hook expected {expected_stage_type.value} for node {node_id}, got {stage_type.value}"
                )
            return PolicyStageFacts(
                stage=stage_type,
                node_id=node_id,
                kind_id=stage_plan.kind_id,
                stage_kind_ref=stage_plan.stage_kind_ref,
                model_profile_ref=stage_plan.model_profile_ref,
                runner=stage_plan.runner,
                model=stage_plan.model,
                effort=stage_plan.effort,
                permission_profile=stage_plan.permission_profile,
                allow_search=stage_plan.allow_search,
                timeout_seconds=stage_plan.timeout_seconds,
                prompt_asset_ref=stage_plan.prompt_asset_ref,
                running_status=stage_plan.running_status,
                success_statuses=stage_plan.success_statuses,
                terminal_statuses=stage_plan.terminal_statuses,
            )
        raise PolicyHookError(f"frozen plan does not define execution node: {node_id}")


__all__ = [
    "POLICY_CYCLE_NODE_ID",
    "PolicyDecision",
    "PolicyEvaluationRecord",
    "PolicyEvidence",
    "PolicyEvidenceKind",
    "PolicyFactSnapshot",
    "PolicyHook",
    "PolicyHookError",
    "PolicyHookRuntime",
    "PolicyPlanFacts",
    "PolicyQueueFacts",
    "PolicyRuntimeFacts",
    "PolicyStageFacts",
    "PolicyTaskFacts",
]
