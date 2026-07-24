"""CLI adapter for compiled-plan admission and default selection."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, cast

from millrace.adapters.cli.context import (
    CliCommandError,
    actor_id,
    apply_control_transition,
    command_id,
    input_id,
    open_runtime_context,
    package_command_failed,
)
from millrace.adapters.cli.output import (
    CliSuccess,
    ExitCode,
    diagnostic_projections,
    success_result,
)
from millrace.compiler import authority_fingerprint

if TYPE_CHECKING:
    from millrace.contracts.compiled_plan import SelectedCompiledPlan


def handle_plan_command(namespace: object) -> CliSuccess:
    command = str(getattr(namespace, "command", "plan"))
    if command == "plan.admit":
        return _admit_export(namespace)
    if command == "plan.admit-package":
        return _admit_package(namespace)
    if command == "plan.select-default":
        return _select_default(namespace)
    if command == "plan.show":
        return _show(namespace)
    raise CliCommandError(
        command=command,
        code="command_not_implemented",
        message="Command is not implemented.",
        exit_code=ExitCode.DOMAIN_REFUSAL,
        details={},
    )


def _admit_export(namespace: object) -> CliSuccess:
    from millrace.contracts.transition import AdmitPlan

    command = "plan.admit"
    input_id_value = input_id(namespace, command=command)
    selected_plan, fingerprint = _selected_plan_from_export(
        Path(str(getattr(namespace, "compiled_plan_json"))),
        command=command,
    )
    decision, state = apply_control_transition(
        namespace,
        AdmitPlan(
            input_id=input_id_value,
            selected_plan=selected_plan,
            authority_fingerprint=fingerprint,
        ),
        command=command,
    )
    admitted = state.admitted_plans[fingerprint]
    return success_result(
        command=command,
        code="plan_admitted",
        message="Plan admitted.",
        data={
            "transition_disposition": decision.disposition,
            "input_id": input_id_value,
            "plan": _admitted_plan_projection(
                admitted,
                is_default=fingerprint == _default_plan_fingerprint(state),
            ),
        },
    )


def _admit_package(namespace: object) -> CliSuccess:
    from millrace.contracts.transition import AdmitPlan
    from millrace.operator.packages import (
        PackageCommandError,
        PackageWorkflowSelectionCommand,
        execute_package_workflow_selection_command,
    )

    command = "plan.admit-package"
    input_id_value = input_id(namespace, command=command)
    command_id_value = command_id(namespace, command=command)
    runtime = open_runtime_context(namespace, command=command)
    try:
        selection = execute_package_workflow_selection_command(
            runtime.store,
            runtime.cas_store,
            PackageWorkflowSelectionCommand(
                command_id=command_id_value,
                actor_id=actor_id(namespace, command=command),
                package_id=str(getattr(namespace, "package_id")),
                package_version=str(getattr(namespace, "package_version")),
                workflow_id=str(getattr(namespace, "workflow_id")),
                workflow_version=str(getattr(namespace, "workflow_version")),
                entrypoint=str(getattr(namespace, "entrypoint")),
            ),
        )
    except PackageCommandError as exc:
        raise package_command_failed(
            command=command,
            code=str(exc),
            details={"command_id": command_id_value},
        ) from exc
    finally:
        runtime.close()

    if selection.outcome != "succeeded" or selection.plan is None:
        raise package_command_failed(
            command=command,
            code=selection.command_audit.error_code or "package_selection_failed",
            details={
                "command_audit_id": selection.command_audit.command_audit_id,
                "command_id": selection.command_audit.command_id,
            },
        )
    fingerprint = authority_fingerprint(selection.plan)
    decision, state = apply_control_transition(
        namespace,
        AdmitPlan(
            input_id=input_id_value,
            selected_plan=selection.plan,
            authority_fingerprint=fingerprint,
        ),
        command=command,
    )
    admitted = state.admitted_plans[fingerprint]
    return success_result(
        command=command,
        code="plan_admitted",
        message="Plan admitted.",
        data={
            "transition_disposition": decision.disposition,
            "input_id": input_id_value,
            "package_selection_command_audit_id": (
                selection.command_audit.command_audit_id
            ),
            "diagnostics": diagnostic_projections(selection.diagnostics),
            "plan": _admitted_plan_projection(
                admitted,
                is_default=fingerprint == _default_plan_fingerprint(state),
            ),
        },
    )


def _select_default(namespace: object) -> CliSuccess:
    from millrace.contracts.transition import SelectDefaultPlan

    command = "plan.select-default"
    input_id_value = input_id(namespace, command=command)
    fingerprint = str(getattr(namespace, "fingerprint"))
    decision, state = apply_control_transition(
        namespace,
        SelectDefaultPlan(
            input_id=input_id_value,
            authority_fingerprint=fingerprint,
        ),
        command=command,
    )
    default_fingerprint = _default_plan_fingerprint(state)
    projected_fingerprint = default_fingerprint or fingerprint
    return success_result(
        command=command,
        code="default_plan_selected",
        message="Default plan selected.",
        data={
            "transition_disposition": decision.disposition,
            "input_id": input_id_value,
            "default_plan_fingerprint": default_fingerprint,
            "plan": _admitted_plan_projection(
                state.admitted_plans[projected_fingerprint],
                is_default=projected_fingerprint == default_fingerprint,
            ),
        },
    )


def _show(namespace: object) -> CliSuccess:
    command = "plan.show"
    requested_fingerprint = getattr(namespace, "fingerprint", None)
    runtime = open_runtime_context(namespace, command=command)
    try:
        state = runtime.store.load_runtime_state(runtime.cas_store)
    finally:
        runtime.close()

    default_fingerprint = _default_plan_fingerprint(state)
    admitted_items = sorted(state.admitted_plans.items(), key=lambda item: item[0])
    if requested_fingerprint is not None:
        admitted_items = [
            item for item in admitted_items if item[0] == requested_fingerprint
        ]
        if not admitted_items:
            raise CliCommandError(
                command=command,
                code="plan_not_admitted",
                message="Plan is not admitted.",
                exit_code=ExitCode.DOMAIN_REFUSAL,
                details={"authority_fingerprint": requested_fingerprint},
            )
    return success_result(
        command=command,
        code="plans_shown",
        message="Admitted plans shown.",
        data={
            "default_plan_fingerprint": default_fingerprint,
            "plans": [
                _admitted_plan_projection(
                    admitted,
                    is_default=fingerprint == default_fingerprint,
                )
                for fingerprint, admitted in admitted_items
            ],
        },
    )


def _selected_plan_from_export(
    path: Path,
    *,
    command: str,
) -> tuple[SelectedCompiledPlan, str]:
    from millrace.compiler import (
        CompiledPlanExportError,
        verify_compiled_plan_export_bytes,
    )
    from millrace.substrate.codecs import decode_selected_compiled_plan
    from millrace.substrate.errors import SubstrateError
    from millrace.substrate.records import (
        SELECTED_COMPILED_PLAN_OBJECT_KIND,
        CasObjectEnvelope,
        JsonValue,
    )

    try:
        export_bytes = path.read_bytes()
    except OSError as exc:
        raise CliCommandError(
            command=command,
            code="compiled_plan_export_unreadable",
            message="Compiled plan export could not be read.",
            exit_code=ExitCode.PERSISTENCE_FAILURE,
            details={"path": str(path), "error": str(exc)},
        ) from exc
    try:
        verified = verify_compiled_plan_export_bytes(export_bytes)
        hydratable_authority = _codec_hydratable_selected_authority(
            verified.selected_authority
        )
        selected_plan = decode_selected_compiled_plan(
            CasObjectEnvelope(
                object_kind=SELECTED_COMPILED_PLAN_OBJECT_KIND,
                payload=cast(Mapping[str, JsonValue], hydratable_authority),
            )
        )
    except (CompiledPlanExportError, SubstrateError, ValueError) as exc:
        raise CliCommandError(
            command=command,
            code="compiled_plan_export_invalid",
            message="Compiled plan export is invalid.",
            exit_code=ExitCode.DOMAIN_REFUSAL,
            details={"path": str(path), "error": str(exc)},
        ) from exc

    fingerprint = authority_fingerprint(selected_plan)
    if fingerprint != verified.authority_fingerprint:
        raise CliCommandError(
            command=command,
            code="plan_fingerprint_drift",
            message="Compiled plan export fingerprint drifted after typed decoding.",
            exit_code=ExitCode.DOMAIN_REFUSAL,
            details={
                "export_authority_fingerprint": verified.authority_fingerprint,
                "decoded_authority_fingerprint": fingerprint,
            },
        )
    return selected_plan, fingerprint


_PRESENTATION_SEQUENCE_KEYS = frozenset(
    {
        "graphs",
        "partitions",
        "queue_families",
        "artifact_schemas",
        "assets",
        "stage_kinds",
        "terminal_outcomes",
        "terminal_actions",
        "completion_behaviors",
        "remediation_policies",
        "runner_bindings",
    }
)


def _codec_hydratable_selected_authority(
    selected_authority: Mapping[str, object],
) -> dict[str, object]:
    hydratable = dict(selected_authority)
    for key in _PRESENTATION_SEQUENCE_KEYS:
        hydratable[key] = [
            _record_with_empty_presentation(item)
            for item in _record_items(hydratable[key])
        ]
    return hydratable


def _record_items(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _record_with_empty_presentation(
    record: Mapping[str, object],
) -> dict[str, object]:
    hydrated = dict(record)
    hydrated.setdefault("presentation", {})
    return hydrated


def _admitted_plan_projection(
    admitted: object,
    *,
    is_default: bool,
) -> dict[str, object]:
    plan_ref = getattr(admitted, "plan_ref")
    selected_plan = getattr(admitted, "selected_plan")
    workflow = getattr(selected_plan, "workflow")
    return {
        "authority_fingerprint": getattr(plan_ref, "authority_fingerprint"),
        "plan_id": getattr(plan_ref, "plan_id"),
        "plan_format_version": getattr(plan_ref, "plan_format_version"),
        "workflow_id": str(getattr(workflow, "workflow_id")),
        "workflow_version": str(getattr(workflow, "workflow_version")),
        "is_default": is_default,
        "workflow_package_pin": _workflow_package_pin_projection(
            getattr(selected_plan, "workflow_package_pin")
        ),
    }


def _default_plan_fingerprint(state: object) -> str | None:
    default_plan_ref = getattr(state, "default_plan_ref")
    if default_plan_ref is None:
        return None
    return cast(str, getattr(default_plan_ref, "authority_fingerprint"))


def _workflow_package_pin_projection(pin: object | None) -> dict[str, object] | None:
    if pin is None:
        return None
    return {
        "package_id": getattr(pin, "package_id"),
        "package_version": getattr(pin, "package_version"),
        "package_format_version": getattr(pin, "package_format_version"),
        "workflow_id": getattr(pin, "workflow_id"),
        "workflow_version": getattr(pin, "workflow_version"),
        "entrypoint": getattr(pin, "entrypoint"),
        "selected_asset_count": len(getattr(pin, "selected_asset_pins")),
        "selected_dependency_count": len(getattr(pin, "selected_dependency_pins")),
    }
