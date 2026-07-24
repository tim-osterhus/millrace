"""Minimal local-operator intake helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from millrace.contracts.compiled_plan import (
    ArtifactSchemaDeclaration,
    AuthorityValue,
    InterventionOptionDeclaration,
    OperatorWaitDeclaration,
    SelectedCompiledPlan,
    UnsupportedAuthorityValueError,
    freeze_authority_mapping,
)
from millrace.contracts.fingerprints import AuthorityFingerprint
from millrace.contracts.ids import QueueFamilyId
from millrace.contracts.schema import validate_schema
from millrace.contracts.state import (
    LineageQuarantineRecord,
    OperatorWaitRecord,
    PlanRef,
    RuntimeState,
)
from millrace.contracts.transition import (
    EnqueueWork,
    OperatorCloseLineage,
    OperatorCloseWait,
    OperatorResumeLineage,
    OperatorResumeWait,
    OperatorReviseLineage,
    OperatorReviseWait,
)


@dataclass(frozen=True, slots=True)
class OperatorInputError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class OperatorEnqueueInput:
    input_id: str
    queue_family_id: str
    payload: object
    plan_fingerprint: AuthorityFingerprint | None = None


@dataclass(frozen=True, slots=True)
class OperatorResumeLineageInput:
    input_id: str
    option_id: str
    selected_plan_ref: PlanRef
    quarantine_id: str | None = None
    lineage_id: str | None = None
    actor_id: str = ""
    actor_kind: str | None = None
    reason: str = ""
    payload: object = None


@dataclass(frozen=True, slots=True)
class OperatorCloseLineageInput:
    input_id: str
    option_id: str
    selected_plan_ref: PlanRef
    quarantine_id: str | None = None
    lineage_id: str | None = None
    actor_id: str = ""
    actor_kind: str | None = None
    reason: str = ""
    payload: object = None


@dataclass(frozen=True, slots=True)
class OperatorReviseLineageInput:
    input_id: str
    option_id: str
    selected_plan_ref: PlanRef
    payload: object
    quarantine_id: str | None = None
    lineage_id: str | None = None
    actor_id: str = ""
    actor_kind: str | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class OperatorResumeWaitInput:
    input_id: str
    selected_plan_ref: PlanRef
    wait_id: str | None = None
    lineage_id: str | None = None
    actor_id: str = ""
    actor_kind: str | None = None
    payload: object = None


@dataclass(frozen=True, slots=True)
class OperatorCloseWaitInput:
    input_id: str
    selected_plan_ref: PlanRef
    wait_id: str | None = None
    lineage_id: str | None = None
    actor_id: str = ""
    actor_kind: str | None = None
    payload: object = None


@dataclass(frozen=True, slots=True)
class OperatorReviseWaitInput:
    input_id: str
    selected_plan_ref: PlanRef
    payload: object
    wait_id: str | None = None
    lineage_id: str | None = None
    actor_id: str = ""
    actor_kind: str | None = None


def build_enqueue_work(
    state: RuntimeState,
    operator_input: OperatorEnqueueInput,
) -> EnqueueWork:
    """Preflight a local-operator enqueue request into a kernel input."""
    input_id = _require_nonblank(operator_input.input_id, "empty_input_id")
    queue_family_id = _require_nonblank(
        operator_input.queue_family_id,
        "empty_queue_family_id",
    )
    payload = _freeze_payload(operator_input.payload)

    default_plan_ref = state.default_plan_ref
    if default_plan_ref is None:
        raise OperatorInputError("missing_default_plan")
    if (
        operator_input.plan_fingerprint is not None
        and operator_input.plan_fingerprint != default_plan_ref.authority_fingerprint
    ):
        raise OperatorInputError("plan_fingerprint_mismatch")

    admitted = state.admitted_plans.get(default_plan_ref.authority_fingerprint)
    if admitted is None:
        raise OperatorInputError("missing_default_plan")

    queue_family = next(
        (
            family
            for family in admitted.selected_plan.queue_families
            if str(family.id) == queue_family_id
        ),
        None,
    )
    if queue_family is None:
        raise OperatorInputError("unknown_queue_family")
    if not queue_family.external_enqueue:
        raise OperatorInputError("queue_family_not_external")
    typed_queue_family_id = QueueFamilyId(queue_family_id)
    if typed_queue_family_id not in admitted.external_enqueue_routes:
        raise OperatorInputError("missing_external_enqueue_route")
    route = admitted.external_enqueue_routes[typed_queue_family_id]
    if route.payload_schema_id is not None:
        schema = _artifact_schema_for(
            admitted.selected_plan,
            str(route.payload_schema_id),
        )
        if schema is None:
            raise OperatorInputError("unknown_payload_schema")
        validation = validate_schema(schema.schema, payload)
        if not validation.accepted:
            raise OperatorInputError("invalid_payload_schema")

    return EnqueueWork(
        input_id=input_id,
        queue_family_id=typed_queue_family_id,
        payload=payload,
    )


def build_resume_lineage(
    state: RuntimeState,
    operator_input: OperatorResumeLineageInput,
) -> OperatorResumeLineage:
    input_id, option_id, selected_plan_ref, quarantine, actor_id, payload = (
        _preflight_lineage_intervention(
            state,
            input_id=operator_input.input_id,
            option_id=operator_input.option_id,
            selected_plan_ref=operator_input.selected_plan_ref,
            quarantine_id=operator_input.quarantine_id,
            lineage_id=operator_input.lineage_id,
            actor_id=operator_input.actor_id,
            actor_kind=operator_input.actor_kind,
            payload=operator_input.payload,
            option_kind="resume_lineage",
        )
    )
    reason = _require_nonblank(operator_input.reason, "empty_reason")
    return OperatorResumeLineage(
        input_id=input_id,
        option_id=option_id,
        selected_plan_ref=selected_plan_ref,
        quarantine_id=quarantine.quarantine_id,
        lineage_id=quarantine.lineage_id,
        actor_id=actor_id,
        actor_kind="local_operator",
        reason=reason,
        payload=payload,
    )


def build_close_lineage(
    state: RuntimeState,
    operator_input: OperatorCloseLineageInput,
) -> OperatorCloseLineage:
    input_id, option_id, selected_plan_ref, quarantine, actor_id, payload = (
        _preflight_lineage_intervention(
            state,
            input_id=operator_input.input_id,
            option_id=operator_input.option_id,
            selected_plan_ref=operator_input.selected_plan_ref,
            quarantine_id=operator_input.quarantine_id,
            lineage_id=operator_input.lineage_id,
            actor_id=operator_input.actor_id,
            actor_kind=operator_input.actor_kind,
            payload=operator_input.payload,
            option_kind="close_lineage",
        )
    )
    reason = _require_nonblank(operator_input.reason, "empty_reason")
    return OperatorCloseLineage(
        input_id=input_id,
        option_id=option_id,
        selected_plan_ref=selected_plan_ref,
        quarantine_id=quarantine.quarantine_id,
        lineage_id=quarantine.lineage_id,
        actor_id=actor_id,
        actor_kind="local_operator",
        reason=reason,
        payload=payload,
    )


def build_revise_lineage(
    state: RuntimeState,
    operator_input: OperatorReviseLineageInput,
) -> OperatorReviseLineage:
    input_id, option_id, selected_plan_ref, quarantine, actor_id, payload = (
        _preflight_lineage_intervention(
            state,
            input_id=operator_input.input_id,
            option_id=operator_input.option_id,
            selected_plan_ref=operator_input.selected_plan_ref,
            quarantine_id=operator_input.quarantine_id,
            lineage_id=operator_input.lineage_id,
            actor_id=operator_input.actor_id,
            actor_kind=operator_input.actor_kind,
            payload=operator_input.payload,
            option_kind="revise_lineage",
        )
    )
    reason = _require_nonblank(operator_input.reason, "empty_reason")
    return OperatorReviseLineage(
        input_id=input_id,
        option_id=option_id,
        selected_plan_ref=selected_plan_ref,
        quarantine_id=quarantine.quarantine_id,
        lineage_id=quarantine.lineage_id,
        actor_id=actor_id,
        actor_kind="local_operator",
        reason=reason,
        payload=payload,
    )


def build_resume_wait(
    state: RuntimeState,
    operator_input: OperatorResumeWaitInput,
) -> OperatorResumeWait:
    input_id, selected_plan_ref, wait_id, lineage_id, actor_id, payload = (
        _preflight_operator_wait(
            state,
            input_id=operator_input.input_id,
            selected_plan_ref=operator_input.selected_plan_ref,
            wait_id=operator_input.wait_id,
            lineage_id=operator_input.lineage_id,
            actor_id=operator_input.actor_id,
            actor_kind=operator_input.actor_kind,
            payload=operator_input.payload,
            resolution_kind="resume_recorded_source",
        )
    )
    return OperatorResumeWait(
        input_id=input_id,
        selected_plan_ref=selected_plan_ref,
        wait_id=wait_id,
        lineage_id=lineage_id,
        actor_id=actor_id,
        actor_kind="local_operator",
        payload=payload,
    )


def build_close_wait(
    state: RuntimeState,
    operator_input: OperatorCloseWaitInput,
) -> OperatorCloseWait:
    input_id, selected_plan_ref, wait_id, lineage_id, actor_id, payload = (
        _preflight_operator_wait(
            state,
            input_id=operator_input.input_id,
            selected_plan_ref=operator_input.selected_plan_ref,
            wait_id=operator_input.wait_id,
            lineage_id=operator_input.lineage_id,
            actor_id=operator_input.actor_id,
            actor_kind=operator_input.actor_kind,
            payload=operator_input.payload,
            resolution_kind="close_recorded_source",
        )
    )
    return OperatorCloseWait(
        input_id=input_id,
        selected_plan_ref=selected_plan_ref,
        wait_id=wait_id,
        lineage_id=lineage_id,
        actor_id=actor_id,
        actor_kind="local_operator",
        payload=payload,
    )


def build_revise_wait(
    state: RuntimeState,
    operator_input: OperatorReviseWaitInput,
) -> OperatorReviseWait:
    input_id, selected_plan_ref, wait_id, lineage_id, actor_id, payload = (
        _preflight_operator_wait(
            state,
            input_id=operator_input.input_id,
            selected_plan_ref=operator_input.selected_plan_ref,
            wait_id=operator_input.wait_id,
            lineage_id=operator_input.lineage_id,
            actor_id=operator_input.actor_id,
            actor_kind=operator_input.actor_kind,
            payload=operator_input.payload,
            resolution_kind="revise_recorded_source",
        )
    )
    return OperatorReviseWait(
        input_id=input_id,
        selected_plan_ref=selected_plan_ref,
        wait_id=wait_id,
        lineage_id=lineage_id,
        actor_id=actor_id,
        actor_kind="local_operator",
        payload=payload,
    )


def _preflight_lineage_intervention(
    state: RuntimeState,
    *,
    input_id: object,
    option_id: object,
    selected_plan_ref: object,
    quarantine_id: object,
    lineage_id: object,
    actor_id: object,
    actor_kind: object,
    payload: object,
    option_kind: str,
) -> tuple[
    str,
    str,
    PlanRef,
    LineageQuarantineRecord,
    str,
    Mapping[str, AuthorityValue],
]:
    typed_input_id = _require_nonblank(input_id, "empty_input_id")
    typed_option_id = _require_nonblank(option_id, "empty_option_id")
    if not isinstance(selected_plan_ref, PlanRef):
        raise OperatorInputError("invalid_selected_plan_ref")
    actor = _require_nonblank(actor_id, "empty_actor_id")
    if actor_kind not in (None, "local_operator"):
        raise OperatorInputError("invalid_actor_kind")

    quarantine = _target_lineage_quarantine(
        state,
        selected_plan_ref=selected_plan_ref,
        quarantine_id=quarantine_id,
        lineage_id=lineage_id,
    )
    if quarantine.selected_plan_ref != selected_plan_ref:
        raise OperatorInputError("selected_plan_ref_mismatch")
    admitted = state.admitted_plans.get(selected_plan_ref.authority_fingerprint)
    if admitted is None or admitted.plan_ref != selected_plan_ref:
        raise OperatorInputError("unknown_plan_ref")
    option = _intervention_option_for(admitted.selected_plan, typed_option_id)
    if option is None:
        raise OperatorInputError("unknown_intervention_option")
    if option.option_kind != option_kind:
        raise OperatorInputError("intervention_option_kind_mismatch")
    if option.actor_kind != "local_operator":
        raise OperatorInputError("invalid_actor_kind")
    if option.policy_id != quarantine.policy_id:
        raise OperatorInputError("intervention_policy_mismatch")
    typed_payload = (
        _freeze_revise_payload(admitted.selected_plan, option, payload)
        if option_kind == "revise_lineage"
        else _freeze_empty_payload(payload)
    )
    return (
        typed_input_id,
        typed_option_id,
        selected_plan_ref,
        quarantine,
        actor,
        typed_payload,
    )


def _preflight_operator_wait(
    state: RuntimeState,
    *,
    input_id: object,
    selected_plan_ref: object,
    wait_id: object,
    lineage_id: object,
    actor_id: object,
    actor_kind: object,
    payload: object,
    resolution_kind: str,
) -> tuple[str, PlanRef, str, str, str, Mapping[str, AuthorityValue]]:
    typed_input_id = _require_nonblank(input_id, "empty_input_id")
    if not isinstance(selected_plan_ref, PlanRef):
        raise OperatorInputError("invalid_selected_plan_ref")
    actor = _require_nonblank(actor_id, "empty_actor_id")
    if actor_kind not in (None, "local_operator"):
        raise OperatorInputError("invalid_actor_kind")
    wait = _target_operator_wait(
        state,
        selected_plan_ref=selected_plan_ref,
        wait_id=wait_id,
        lineage_id=lineage_id,
    )
    admitted = state.admitted_plans.get(selected_plan_ref.authority_fingerprint)
    if admitted is None or admitted.plan_ref != selected_plan_ref:
        raise OperatorInputError("unknown_plan_ref")
    declaration = _operator_wait_for(admitted.selected_plan, str(wait.operator_wait_id))
    if declaration is None:
        raise OperatorInputError("unknown_operator_wait")
    if declaration.actor_kind != "local_operator":
        raise OperatorInputError("invalid_actor_kind")
    if resolution_kind not in set(declaration.allowed_resolution_kinds):
        raise OperatorInputError("operator_wait_resolution_forbidden")
    typed_payload = (
        _freeze_operator_wait_revise_payload(
            admitted.selected_plan,
            declaration,
            payload,
        )
        if resolution_kind == "revise_recorded_source"
        else _freeze_empty_payload(payload)
    )
    return (
        typed_input_id,
        selected_plan_ref,
        wait.wait_id,
        wait.lineage_id,
        actor,
        typed_payload,
    )


def _target_operator_wait(
    state: RuntimeState,
    *,
    selected_plan_ref: PlanRef,
    wait_id: object,
    lineage_id: object,
) -> OperatorWaitRecord:
    has_wait_id = isinstance(wait_id, str) and bool(wait_id.strip())
    has_lineage_id = isinstance(lineage_id, str) and bool(lineage_id.strip())
    if has_wait_id == has_lineage_id:
        raise OperatorInputError("invalid_operator_wait_target")
    if has_lineage_id:
        for record in state.operator_waits.values():
            if (
                record.lineage_id == str(lineage_id)
                and record.selected_plan_ref == selected_plan_ref
                and record.status == "active"
            ):
                return record
        raise OperatorInputError("unknown_operator_wait")
    wait_record = state.operator_waits.get(str(wait_id))
    if wait_record is None or wait_record.status != "active":
        raise OperatorInputError("unknown_operator_wait")
    if wait_record.selected_plan_ref != selected_plan_ref:
        raise OperatorInputError("selected_plan_ref_mismatch")
    return wait_record


def _operator_wait_for(
    selected_plan: SelectedCompiledPlan,
    operator_wait_id: str,
) -> OperatorWaitDeclaration | None:
    return next(
        (
            wait
            for wait in selected_plan.operator_waits
            if str(wait.id) == operator_wait_id
        ),
        None,
    )


def _target_lineage_quarantine(
    state: RuntimeState,
    *,
    selected_plan_ref: PlanRef,
    quarantine_id: object,
    lineage_id: object,
) -> LineageQuarantineRecord:
    has_quarantine_id = isinstance(quarantine_id, str) and bool(quarantine_id.strip())
    has_lineage_id = isinstance(lineage_id, str) and bool(lineage_id.strip())
    if has_quarantine_id == has_lineage_id:
        raise OperatorInputError("invalid_intervention_target")
    if has_lineage_id:
        for record in state.lineage_quarantines.values():
            if (
                record.lineage_id == str(lineage_id)
                and record.selected_plan_ref == selected_plan_ref
                and record.status == "active"
            ):
                return record
        raise OperatorInputError("unknown_lineage_quarantine")
    for record in state.lineage_quarantines.values():
        if record.quarantine_id == str(quarantine_id):
            return record
    raise OperatorInputError("unknown_lineage_quarantine")


def _intervention_option_for(
    selected_plan: SelectedCompiledPlan,
    option_id: str,
) -> InterventionOptionDeclaration | None:
    return next(
        (
            option
            for option in selected_plan.intervention_options
            if str(option.id) == option_id
        ),
        None,
    )


def _artifact_schema_for(
    selected_plan: SelectedCompiledPlan,
    schema_id: str,
) -> ArtifactSchemaDeclaration | None:
    return next(
        (
            artifact_schema
            for artifact_schema in selected_plan.artifact_schemas
            if str(artifact_schema.id) == schema_id
        ),
        None,
    )


def _require_nonblank(value: object, reason: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OperatorInputError(reason)
    return value


def _freeze_payload(value: object) -> Mapping[str, AuthorityValue]:
    if not isinstance(value, Mapping):
        raise OperatorInputError("invalid_payload")
    try:
        return freeze_authority_mapping(value)
    except UnsupportedAuthorityValueError as exc:
        raise OperatorInputError("invalid_payload") from exc


def _freeze_empty_payload(value: object) -> Mapping[str, AuthorityValue]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise OperatorInputError("payload_forbidden")
    payload = _freeze_payload(value)
    if payload:
        raise OperatorInputError("payload_forbidden")
    return payload


def _freeze_revise_payload(
    selected_plan: SelectedCompiledPlan,
    option: InterventionOptionDeclaration,
    value: object,
) -> Mapping[str, AuthorityValue]:
    payload = _freeze_payload(value)
    payload_schema_id = option.payload_schema_id
    if payload_schema_id is None:
        raise OperatorInputError("missing_payload_schema")
    schema = _artifact_schema_for(selected_plan, str(payload_schema_id))
    if schema is None:
        raise OperatorInputError("unknown_payload_schema")
    validation = validate_schema(schema.schema, payload)
    if not validation.accepted:
        raise OperatorInputError("invalid_payload_schema")
    return payload


def _freeze_operator_wait_revise_payload(
    selected_plan: SelectedCompiledPlan,
    wait: OperatorWaitDeclaration,
    value: object,
) -> Mapping[str, AuthorityValue]:
    payload = _freeze_payload(value)
    payload_schema_id = wait.payload_schema_id
    if payload_schema_id is None:
        raise OperatorInputError("missing_payload_schema")
    schema = _artifact_schema_for(selected_plan, str(payload_schema_id))
    if schema is None:
        raise OperatorInputError("unknown_payload_schema")
    validation = validate_schema(schema.schema, payload)
    if not validation.accepted:
        raise OperatorInputError("invalid_payload_schema")
    return payload


__all__ = (
    "OperatorEnqueueInput",
    "OperatorInputError",
    "OperatorCloseLineageInput",
    "OperatorCloseWaitInput",
    "OperatorResumeLineageInput",
    "OperatorResumeWaitInput",
    "OperatorReviseLineageInput",
    "OperatorReviseWaitInput",
    "build_enqueue_work",
    "build_close_lineage",
    "build_close_wait",
    "build_resume_lineage",
    "build_resume_wait",
    "build_revise_lineage",
    "build_revise_wait",
)
