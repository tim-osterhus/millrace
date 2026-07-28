"""CLI adapter for local workspace and package diagnostics."""

from __future__ import annotations

from collections import Counter

from millrace.adapters.cli.context import CliCommandError, open_runtime_context
from millrace.adapters.cli.output import CliSuccess, ExitCode, success_result
from millrace.adapters.cli.session_coordinator import (
    cooperative_cancel_grace_seconds,
    terminate_grace_seconds,
)
from millrace.operator.dispatch import list_ready_dispatch_candidates


def handle_doctor_command(namespace: object) -> CliSuccess:
    command = str(getattr(namespace, "command", "doctor"))
    if command != "doctor":
        raise CliCommandError(
            command=command,
            code="command_not_implemented",
            message="Command is not implemented.",
            exit_code=ExitCode.DOMAIN_REFUSAL,
            details={},
        )
    runtime = open_runtime_context(namespace, command=command)
    try:
        metadata = runtime.store.schema_metadata()
        state = runtime.store.load_runtime_state(runtime.cas_store)
        registry = runtime.store.load_workflow_package_registry(runtime.cas_store)
        ready = list_ready_dispatch_candidates(state)
    finally:
        runtime.close()

    severity_counts = Counter(
        diagnostic.severity for diagnostic in ready.diagnostics
    )
    session_diagnostic_items: list[dict[str, str]] = []
    for session in state.runner_sessions.values():
        base = {"session_id": session.session_id, "run_id": session.run_id}
        if session.state == "lost":
            session_diagnostic_items.append(
                {"code": "runner_session_lost", **base}
            )
        completion = state.runner_session_completions.get(session.session_id)
        if (
            completion is not None
            and completion.adapter_outcome_kind == "unsupported"
        ):
            session_diagnostic_items.append(
                {
                    "code": "runner_session_reconciliation_unsupported",
                    **base,
                }
            )
        if session.cleanup_disposition == "orphan_risk":
            session_diagnostic_items.append(
                {"code": "runner_session_orphan_risk", **base}
            )
        if (
            session.cleanup_disposition == "pending"
            and session.state in {"cancellation_requested", "terminating"}
        ):
            session_diagnostic_items.append(
                {"code": "runner_session_cleanup_pending", **base}
            )
    session_diagnostics = tuple(session_diagnostic_items)
    session_diagnostic_counts = Counter(
        str(item["code"]) for item in session_diagnostics
    )
    default_plan = (
        None
        if state.default_plan_ref is None
        else {
            "plan_id": state.default_plan_ref.plan_id,
            "authority_fingerprint": state.default_plan_ref.authority_fingerprint,
            "plan_format_version": state.default_plan_ref.plan_format_version,
            "admitted": (
                state.default_plan_ref.authority_fingerprint in state.admitted_plans
            ),
        }
    )
    return success_result(
        command=command,
        code="doctor_ok",
        message="Doctor projection complete.",
        data={
            "workspace": {"path": str(runtime.paths.workspace_path)},
            "store": {
                "initialized": True,
                "schema_version": metadata["store_schema_version"],
                "admitted_plan_count": len(state.admitted_plans),
            },
            "cas": {
                "initialized": runtime.paths.cas_path.is_dir(),
                "path": str(runtime.paths.cas_path),
            },
            "default_plan": default_plan,
            "packages": {"registered_count": len(registry.records)},
            "ready_dispatch": {
                "candidate_count": len(ready.candidates),
                "diagnostic_count": len(ready.diagnostics),
                "severity_counts": dict(sorted(severity_counts.items())),
            },
            "runner_session_mechanics": {
                "cooperative_cancel_grace_seconds": (
                    cooperative_cancel_grace_seconds
                ),
                "terminate_grace_seconds": terminate_grace_seconds,
            },
            "runner_session_diagnostics": {
                "diagnostic_counts": dict(sorted(session_diagnostic_counts.items())),
                "diagnostics": list(session_diagnostics),
            },
            "required_paths": {
                "workspace_path": {
                    "path": str(runtime.paths.workspace_path),
                    "exists": runtime.paths.workspace_path.exists(),
                },
                "db_path": {
                    "path": str(runtime.paths.db_path),
                    "exists": runtime.paths.db_path.exists(),
                },
                "cas_path": {
                    "path": str(runtime.paths.cas_path),
                    "exists": runtime.paths.cas_path.exists(),
                },
            },
        },
    )
