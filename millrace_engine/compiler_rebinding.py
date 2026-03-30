"""Runtime parameter rebinding helpers for frozen plans."""

from __future__ import annotations

from .compiler_models import FrozenRunPlan, FrozenRunPlanContent, FrozenStagePlan
from .contracts import ControlPlane
from .provenance import (
    AppliedExecutionParameterRebinding,
    BoundExecutionParameters,
    ExecutionParameterRebindingRequest,
    is_runtime_rebindable_stage_field,
    runtime_stage_parameter_key,
)


def resolved_snapshot_id_for_run(run_id: str, content_hash: str) -> str:
    """Return the stable compile-time snapshot id for one run plan."""

    return f"resolved-snapshot:{run_id}:{content_hash[:12]}"


def bound_execution_parameters_for_stage(stage: FrozenStagePlan) -> BoundExecutionParameters:
    return BoundExecutionParameters(
        model_profile_ref=stage.model_profile_ref,
        runner=stage.runner,
        model=stage.model,
        effort=stage.effort,
        allow_search=stage.allow_search,
        timeout_seconds=stage.timeout_seconds,
    )


def runtime_stage_parameter_map(content: FrozenRunPlanContent) -> dict[str, BoundExecutionParameters]:
    stage_parameters: dict[str, BoundExecutionParameters] = {}
    for loop in (content.execution_plan, content.research_plan):
        if loop is None:
            continue
        for stage in loop.stages:
            stage_parameters[runtime_stage_parameter_key(stage.plane, stage.node_id)] = (
                bound_execution_parameters_for_stage(stage)
            )
    return stage_parameters


class ParameterRebindingError(RuntimeError):
    """Raised when a runtime parameter rebinding request is illegal for a frozen plan."""


class FrozenExecutionParameterBinder:
    """Stateful bounded rebinding for future stage invocations on one frozen plan."""

    def __init__(self, plan: FrozenRunPlan) -> None:
        self.plan = plan
        self.plan_identity = plan.identity
        self._current: dict[tuple[ControlPlane, str], BoundExecutionParameters] = {}
        self._rules = {}

        for loop in (plan.content.execution_plan, plan.content.research_plan):
            if loop is None:
                continue
            for stage in loop.stages:
                key = (stage.plane, stage.node_id)
                self._current[key] = bound_execution_parameters_for_stage(stage)

        for rule in plan.content.parameter_rebinding_rules:
            key = (rule.plane, rule.node_id)
            self._rules.setdefault(key, {})[rule.field] = rule

    def bound_parameters_for(self, plane: ControlPlane, node_id: str) -> BoundExecutionParameters:
        key = (plane, node_id)
        try:
            return self._current[key]
        except KeyError as exc:
            raise ParameterRebindingError(f"frozen plan is missing stage {plane.value}.{node_id}") from exc

    def apply(self, request: ExecutionParameterRebindingRequest) -> AppliedExecutionParameterRebinding:
        key = (request.plane, request.node_id)
        current = self.bound_parameters_for(request.plane, request.node_id)
        stage_rules = self._rules.get(key, {})
        requested_fields = sorted(request.parameters.override_fields(), key=lambda item: item.value)
        if not requested_fields:
            raise ParameterRebindingError(
                f"rebinding request for {request.plane.value}.{request.node_id} did not set any execution parameters"
            )

        for field in requested_fields:
            if not is_runtime_rebindable_stage_field(field):
                raise ParameterRebindingError(f"{field.value} is not a runtime-rebindable execution parameter")
            rule = stage_rules.get(field)
            if rule is None:
                raise ParameterRebindingError(
                    f"{field.value} is not declared as rebindable for {request.plane.value}.{request.node_id}"
                )
            if request.boundary is not rule.rebind_at_boundary:
                raise ParameterRebindingError(
                    f"{field.value} may only rebind at {rule.rebind_at_boundary.value}, not {request.boundary.value}"
                )

        updated = current.apply(request.parameters)
        self._current[key] = updated
        return AppliedExecutionParameterRebinding(
            plane=request.plane,
            node_id=request.node_id,
            boundary=request.boundary,
            applied_fields=tuple(requested_fields),
            previous_parameters=current,
            requested_parameters=request.parameters,
            updated_parameters=updated,
            reason=request.reason,
        )


__all__ = [
    "FrozenExecutionParameterBinder",
    "ParameterRebindingError",
    "bound_execution_parameters_for_stage",
    "resolved_snapshot_id_for_run",
    "runtime_stage_parameter_map",
]
