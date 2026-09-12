"""Opt-in bounded read surface: one durable snapshot, explicit unavailable facts."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from importlib import metadata
from typing import Any

from millrace.adapters.cli.context import (
    CliCommandError,
    OpenRuntimeContext,
    workspace_paths,
)
from millrace.adapters.cli.output import (
    CliSuccess,
    ExitCode,
    json_ready,
    success_result,
)
from millrace.contracts.public_projections import (
    CONTRACT_ID,
    CONTRACT_REVISION,
    MAX_ITEM_BYTES,
    MAX_PAGE_BYTES,
    PUBLIC_SCHEMA_DIGEST,
    decode_cursor,
    digest,
    encode_cursor,
    wire,
)

READ_COMMANDS = frozenset(
    {
        "status",
        "runs.list",
        "runs.show",
        "runs.follow",
        "trace.show",
        "plan.show",
        "plan.graph",
        "plan.overlay",
        "package.list",
        "package.inspect",
        "workspace.check",
        "workspace.identity",
        "doctor",
        "operations.history",
        "daemon.history",
    }
)


def unavailable(reason: str) -> dict[str, Any]:
    return {"availability": "unavailable", "reason": reason}


def page_records(
    records: list[dict[str, Any]],
    base: dict[str, Any],
    namespace: object,
    *,
    pin: object,
    filters: dict[str, Any],
) -> dict[str, Any]:
    limit = getattr(namespace, "page_size", 50)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("invalid_page_size")
    ordered = sorted(
        records,
        key=lambda item: (
            item.get("source_revision", 0),
            str(item["kind"]),
            str(item["id"]),
        ),
    )
    keys = [
        [item.get("source_revision", 0), str(item["kind"]), str(item["id"])]
        for item in ordered
    ]
    if len({tuple(key) for key in keys}) != len(keys):
        raise ValueError("projection_identity_contradiction")
    token = {
        "contract_revision": CONTRACT_REVISION,
        "identity": base["identity"],
        "filter_digest": digest(filters),
        "pin": pin,
    }
    after = None
    cursor = getattr(namespace, "cursor", None)
    if cursor is not None:
        decoded = decode_cursor(cursor)
        if set(decoded) != {*token, "last_key"} or any(
            decoded[key] != token[key] for key in token if key != "pin"
        ):
            raise ValueError("cursor_scope_mismatch")
        if decoded["pin"] != pin:
            raise ValueError("snapshot_changed")
        after = decoded["last_key"]
        if after not in keys:
            raise ValueError("cursor_scope_mismatch")
    start = 0 if after is None else keys.index(after) + 1
    result = {
        **base,
        "records": [],
        "filters": filters,
        "has_more": False,
        "next_cursor": None,
    }
    selected: list[dict[str, Any]] = []
    for index in range(start, len(ordered)):
        item = ordered[index]
        if len(wire(item)) > MAX_ITEM_BYTES:
            raise ValueError("projection_item_too_large")
        if len(selected) == limit:
            break
        candidate = [*selected, item]
        next_cursor = (
            encode_cursor({**token, "last_key": keys[index]})
            if index + 1 < len(ordered)
            else None
        )
        trial = {
            **result,
            "records": candidate,
            "has_more": index + 1 < len(ordered),
            "next_cursor": next_cursor,
        }
        if len(wire(trial)) > MAX_PAGE_BYTES - 1024:
            if not selected:
                raise ValueError("projection_item_too_large")
            break
        selected = candidate
        result = trial
    return result


def session_projection(
    runtime: OpenRuntimeContext, state: Any, session: Any
) -> dict[str, Any]:
    from millrace.adapters.cli.diagnostic_projection import session_diagnostics
    from millrace.adapters.cli.session_coordinator import (
        cooperative_cancel_grace_seconds,
        terminate_grace_seconds,
    )
    from millrace.adapters.cli.status import (
        _attribution_projection,
        _cleanup_projection,
        _usage_evidence_projection,
    )

    control = state.run_execution_controls.get(session.run_id)
    native = None if control is None else control.native
    completion = state.runner_session_completions.get(session.session_id)
    receipt = (
        None
        if completion is None
        else state.receipts.get(completion.application_input_id)
    )
    cancellation = next(
        (
            item
            for item in state.runner_session_cancellation_requests.values()
            if item.session_id == session.session_id and item.primary
        ),
        None,
    )
    attempts = [
        item
        for item in state.runner_session_cancellation_attempts.values()
        if item.session_id == session.session_id
    ]
    last_attempt = max(attempts, key=lambda item: item.sequence) if attempts else None
    budget_id = runtime.store.daemon_budget_id_for_session(session.session_id)
    budget = None
    if budget_id is not None:
        from millrace.operator.status import daemon_budget_projection

        epoch = runtime.store.load_daemon_budget_epoch(budget_id)
        budget = (
            unavailable("budget_binding_evidence_missing")
            if epoch is None
            else {
                key: value
                for key, value in daemon_budget_projection(epoch).items()
                if key != "workspace_path"
            }
        )
    completion_input = _completion_input_id(state, completion)
    fields = (
        "session_id",
        "run_id",
        "dispatch_generation",
        "session_fencing_token",
        "state",
        "created_at",
        "start_intent_at",
        "started_at",
        "ended_at",
        "context_manifest_digest",
        "cleanup_disposition",
    )
    return {
        **{key: getattr(session, key) for key in fields},
        **session_diagnostics(runtime, state, session),
        "cancellation_phase": None
        if cancellation is None
        else (session.state if last_attempt is None else last_attempt.operation),
        "cancellation_last_operation": None
        if last_attempt is None
        else last_attempt.operation,
        "cancellation_last_result": None
        if last_attempt is None
        else last_attempt.result,
        "cooperative_cancel_grace_seconds": cooperative_cancel_grace_seconds,
        "terminate_grace_seconds": terminate_grace_seconds,
        "budget_binding": budget_id,
        "budget": json_ready(budget),
        "completion_id": None if completion is None else completion.completion_id,
        "completion_input_id": completion_input,
        "completion_input_status": "not_completed"
        if completion is None
        else "verified_payload_digest_and_trace"
        if completion_input is not None
        else "unavailable",
        "application_input_id": None
        if completion is None
        else completion.application_input_id,
        "completion_persisted": completion is not None,
        "application_persisted": receipt is not None,
        "application_status": "not_completed"
        if completion is None
        else "not_applicable"
        if completion.runner_result_evidence_digest is None
        else "pending"
        if receipt is None
        else "applied"
        if receipt.accepted
        else "refused",
        "completion_terminal_state": None
        if completion is None
        else completion.terminal_state,
        "completion_exit_kind": None if completion is None else completion.exit_kind,
        "primary_cancellation_request_id": None
        if cancellation is None
        else cancellation.request_id,
        "primary_cancellation_reason": None
        if cancellation is None
        else cancellation.reason,
        "orphan_risk": session.state == "lost"
        or session.cleanup_disposition == "orphan_risk",
        "effect_certainty": unavailable("native_effect_evidence_not_qualified")
        if native is None
        else _native_public(control),
        "descendant_aftermath": unavailable("native_descendant_evidence_not_qualified")
        if native is None
        else {
            "boundary": native["snapshot"]["descendant_boundary"],
            "inventory_at_witness": native["snapshot"]["os_descendants"],
            "cleanup": native.get("cleanup", "not_observed"),
            "daemon_cleanup": "unproved",
        },
        "owner_liveness": unavailable("no_native_owner_observation")
        if native is None
        else {
            "identity": native["owner"],
            "freshness": "historical_witness",
            "observation": native.get("owner_observation", "not_freshly_observed"),
        },
        "recovery_status": unavailable(
            "explicit_recovery_required_if_continuation_lost"
        )
        if native is None
        else {
            "native_continuation": control.state,
            "workflow_recovery": "not_granted",
            "daemon_relaunch": "not_granted",
        },
        "safe_recovery_reference": None
        if native is None or control.state != "retired"
        else list(control.operation_key),
        "attribution": _attribution_projection(runtime, session),
        "context_cleanup": _cleanup_projection(runtime, session),
        "usage_evidence": _usage_evidence_projection(runtime, session.session_id),
    }


def _completion_input_id(state: Any, completion: Any) -> str | None:
    from millrace.contracts.transition import (
        RecordRunnerSessionCompletion,
        input_payload_digest,
    )

    if completion is None or completion.run_id not in state.runs:
        return None
    run_ref = state.runs[completion.run_id].run_ref
    digests = {
        input_payload_digest(
            RecordRunnerSessionCompletion(
                "read-only-digest",
                run_ref=run_ref,
                expected_state=prior,
                completion=completion,
            )
        )
        # Search the finite persisted state vocabulary. The accepted receipt,
        # not this projection, proves the original transition was legal.
        for prior in (
            "created",
            "starting",
            "running",
            "cancellation_requested",
            "terminating",
            "completed",
            "interrupted",
            "failed",
            "lost",
        )
    }
    trace_ids = {
        trace.input_id
        for trace in state.traces
        if trace.input_kind == RecordRunnerSessionCompletion.input_kind
        and trace.disposition == "accepted"
        and trace.run_id == completion.run_id
        and trace.plan_fingerprint == str(run_ref.plan_ref.authority_fingerprint)
    }
    matches = [
        key
        for key, receipt in state.receipts.items()
        if receipt.accepted
        and key in trace_ids
        and receipt.receipt_ref.input_payload_digest in digests
        and receipt.receipt_ref.input_id == key
    ]
    return matches[0] if len(matches) == 1 else None


def run_projection(
    runtime: OpenRuntimeContext, state: Any, run_id: str
) -> dict[str, Any]:
    from millrace.kernel.run_controls import (
        run_control_projection,
        run_eligibility_refusal,
    )

    run = state.runs[run_id]
    session = state.runner_sessions.get(run.current_session_id)
    control = state.run_execution_controls.get(run_id)
    native = None if control is None else control.native
    activation = state.activations.get(run.activation_id)
    work = state.work_items.get(run.work_item_id)
    projected = run_control_projection(state, run_id)
    if native is not None:
        # The full historical snapshot is emitted once, at the existing witness
        # path below. This optional kernel detail otherwise duplicates it.
        projected["execution_hold"]["native"].pop("snapshot")
        projected["execution_hold"]["native"]["snapshot_reference"] = {
            "scope": "same_run_record",
            "json_pointer": "/quiescence_evidence/witness",
        }
    history = runtime.store.control_history_records(run_id=run_id)
    relevant = [
        row
        for row in history
        if control is not None
        and control.operation_key is not None
        and tuple(
            row["key"][key]
            for key in (
                "workspace_id",
                "instance_id",
                "store_epoch",
                "caller_id",
                "operation_id",
            )
        )
        == control.operation_key
    ]
    links = [
        {
            "kind": row["kind"],
            "record_id": row["id"],
            "source_revision": row["source_revision"],
            "operation_key": row["key"],
            "request_digest": row["request_digest"],
        }
        for row in relevant
    ]
    admitted = state.admitted_plans[run.run_ref.plan_ref.authority_fingerprint]
    adapter = next(
        (
            binding.adapter_kind
            for binding in admitted.selected_plan.runner_bindings
            if binding.id == run.runner_binding_id
        ),
        None,
    )
    blocking_reason = run_eligibility_refusal(state, run)
    # This refusal describes unstarted-only control, not the retained native
    # running profile. Historical evidence still does not grant fresh admission.
    if (
        native is not None
        and control.state in {"pause_pending", "paused", "resume_pending", "resumed"}
        and session is not None
        and session.state == "running"
        and blocking_reason == "runner_pause_unsupported"
    ):
        blocking_reason = None
    return {
        "kind": "run",
        "id": run_id,
        **projected,
        "run_id": run_id,
        "work_item_id": run.work_item_id,
        "activation_id": run.activation_id,
        "claim_id": run.run_ref.claim_id,
        "run_generation": run.run_ref.generation,
        "run_fencing_token": run.run_ref.fencing_token,
        "plan_fingerprint": str(run.run_ref.plan_ref.authority_fingerprint),
        "last_dispatch_generation": run.last_dispatch_generation,
        "stage_kind_id": str(run.stage_kind_id),
        "runner_binding_id": str(run.runner_binding_id),
        "graph_node_id": None if activation is None else activation.graph_node_id,
        "queue_family_id": None if work is None else str(work.queue_family_id),
        "expected_session": None
        if session is None
        else {
            key: getattr(session, key)
            for key in (
                "session_id",
                "dispatch_generation",
                "session_fencing_token",
                "state",
            )
        },
        "pause_control": None
        if control is None
        else {
            "pause_id": control.pause_id,
            "state": control.state,
            "operation_id": None
            if control.operation_key is None
            else control.operation_key[4],
            "profile_id": None if native is None else native["profile"]["profile_id"],
        },
        "operation_status": relevant[-1]["record"].get("stage", "unknown")
        if relevant
        else "not_requested",
        "control_receipt_result_references": links,
        "authority_fence": {
            "run_generation": run.run_ref.generation,
            "run_fencing_token": run.run_ref.fencing_token,
            "plan_fingerprint": str(run.run_ref.plan_ref.authority_fingerprint),
            "session_fencing_token": None
            if session is None
            else session.session_fencing_token,
        },
        "effect_status": unavailable("native_effect_evidence_not_qualified")
        if native is None
        else _native_public(control),
        "budget_binding": None
        if session is None
        else runtime.store.daemon_budget_id_for_session(session.session_id),
        "blocking_reasons": [blocking_reason] if blocking_reason else [],
        "runner_session": None
        if session is None
        else session_projection(runtime, state, session),
        "capability_profile": {
            "qualification_status": "unqualified"
            if native is None
            else "recorded_exact_profile_live_admission_required",
            "profile_id": None if native is None else native["profile"]["profile_id"],
            "profile_digest": None
            if native is None
            else native["profile"]["profile_digest"],
            "selected_adapter": adapter,
            "native_states": []
            if native is None
            else [native["profile"]["native_state"]],
            "effect_boundary": None
            if native is None
            else native["profile"]["effect_boundary"],
            "descendant_boundary": None
            if native is None
            else native["snapshot"]["descendant_boundary"],
        },
        "native_control": unavailable("native_control_not_qualified")
        if native is None
        else _native_public(control),
        "quiescence_evidence": unavailable("native_control_not_qualified")
        if native is None
        else {
            "witness": native["snapshot"],
            "scope": "historical_effect_witness",
            "continuation": control.state,
        },
        "checkpoint_status": unavailable("native_control_not_qualified")
        if native is None
        else {
            **unavailable("live_only_continuation"),
            "checkpoint_digest": None,
            "scope": "historical_effect_witness",
            "continuation": native.get("continuation", control.state),
        },
    }


def _base(runtime: OpenRuntimeContext) -> dict[str, Any]:
    from millrace.contracts.daemon_control import runtime_identity

    identity = runtime.store.control_identity()
    schema = runtime.store.schema_metadata()
    version = metadata.version("millrace-ai")
    return {
        "identity": {
            key: identity[key]
            for key in ("workspace_id", "instance_id", "store_epoch", "location_status")
        },
        "observation": {
            "observed_at": datetime.now(UTC).isoformat(),
            "source_revision": identity["source_revision"],
            "store_epoch": identity["store_epoch"],
            "consistency": "transaction_snapshot",
        },
        "compatibility": {
            "public_contract_id": CONTRACT_ID,
            "public_contract_revision": CONTRACT_REVISION,
            "public_schema_digest": PUBLIC_SCHEMA_DIGEST,
            "runtime_distribution": "millrace-ai",
            "runtime_version": version,
            "build_identity": None,
            "build_identity_status": "unavailable_until_attributable_installation",
            "store_schema_version": int(schema["store_schema_version"]),
            "daemon_runtime": runtime_identity(
                version, int(schema["store_schema_version"])
            ),
            "bounded_commands": sorted(READ_COMMANDS),
            "bounded_option": "--bounded",
            "page_default": 50,
            "page_max": 100,
            "page_max_bytes": MAX_PAGE_BYTES,
            "item_max_bytes": MAX_ITEM_BYTES,
            "caller_deadline_seconds": 5,
            "native_control_qualification": "unqualified",
            "lifecycle_platform": "macOS",
            "other_platform_lifecycle": "unknown",
        },
        "redaction_policy_id": "core-public-read-omit-v1",
    }


def handle_projection(namespace: object) -> CliSuccess:
    from millrace.adapters.cli.run_controls import _caller_deadline
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.errors import SubstrateError
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    command = str(getattr(namespace, "command"))
    runtime = None
    try:
        with _caller_deadline():
            if command not in READ_COMMANDS:
                raise ValueError("bounded_read_command_required")
            paths = workspace_paths(namespace)
            if not paths.cas_path.is_dir():
                raise ValueError("cas_root_not_initialized")
            store = SQLiteRuntimeStore.open_readonly(
                paths.db_path,
                workspace_path=paths.workspace_path,
                cas_path=paths.cas_path,
            )
            runtime = OpenRuntimeContext(
                paths, store, ContentAddressedByteStore(paths.cas_path)
            )
            with store.read_transaction():
                base = _base(runtime)
                state = store.load_runtime_state(runtime.cas_store)
                result = _read(runtime, state, base, namespace)
                if len(wire(result)) > MAX_PAGE_BYTES - 512:
                    raise ValueError("projection_page_too_large")
            return success_result(
                command=command,
                code="bounded_projection",
                message="Bounded public snapshot.",
                data=result,
            )
    except (ValueError, SubstrateError, sqlite3.Error, OSError) as exc:
        # Never serialize exception strings containing store paths or untrusted input.
        known = str(exc)
        code = (
            known
            if known
            in {
                "invalid_cursor",
                "invalid_page_size",
                "invalid_after_sequence",
                "cursor_scope_mismatch",
                "cursor_ahead",
                "snapshot_changed",
                "projection_identity_contradiction",
                "projection_item_too_large",
                "projection_page_too_large",
                "graph_dangling_node",
                "run_not_found",
                "plan_not_selected",
                "plan_not_admitted",
                "target_mismatch",
                "daemon_not_found",
                "cas_root_not_initialized",
                "bounded_read_command_required",
                "control_deadline_unknown",
                "control_deadline_timer_unavailable",
            }
            else "projection_unavailable"
        )
        from millrace.adapters.cli.diagnostic_projection import failure_status

        evidence_status = failure_status(exc)
        raise CliCommandError(
            command,
            code,
            "Public read unavailable; no state changed.",
            ExitCode.DOMAIN_REFUSAL,
            {
                "status": evidence_status,
                "diagnostic": {
                    "code": code,
                    "severity": "error",
                    "source": "public_read",
                    "status": evidence_status,
                    "evidence_digest": digest(
                        {"type": type(exc).__name__, "detail": str(exc)}
                    ),
                    "redaction_policy_id": "core-public-read-omit-v1",
                    "truncation": {"detail_omitted": True},
                },
            },
        ) from exc
    finally:
        if runtime is not None:
            runtime.close()


def _read(
    runtime: OpenRuntimeContext, state: Any, base: dict[str, Any], namespace: object
) -> dict[str, Any]:
    from millrace.adapters.cli.graph_projection import graph_records, overlay_records
    from millrace.adapters.cli.history_projection import runner_history

    command = str(getattr(namespace, "command"))
    fingerprint = getattr(namespace, "fingerprint", None) or getattr(
        namespace, "plan_fingerprint", None
    )
    run_id = getattr(namespace, "run_id", None)
    if fingerprint is not None and fingerprint not in state.admitted_plans:
        raise ValueError("plan_not_admitted")
    if run_id is not None:
        if run_id not in state.runs:
            raise ValueError("run_not_found")
        if (
            fingerprint is not None
            and str(state.runs[run_id].run_ref.plan_ref.authority_fingerprint)
            != fingerprint
        ):
            raise ValueError("target_mismatch")
    filters = {
        "command": command,
        "plan_fingerprint": fingerprint,
        "run_id": run_id,
        "package_id": getattr(namespace, "package_id", None),
        "package_version": getattr(namespace, "package_version", None),
        "daemon_id": getattr(namespace, "daemon_id", None),
    }
    revision = base["observation"]["source_revision"]
    records: list[dict[str, Any]] = []
    pin: object = revision
    if command == "runs.follow":
        return {
            **base,
            **runner_history(
                runtime, state, base["identity"], namespace, session_projection
            ),
        }
    if command == "operations.history":
        records = runtime.store.control_history_records(run_id=run_id)
        base = {
            **base,
            "history_status": "available",
            "history_kind": "immutable_control",
            "nonacceptance_proven": False,
            "fresh_state": None
            if run_id is None
            else run_projection(runtime, state, run_id),
            "earliest_retained_sequence": min(
                (row["source_revision"] for row in records), default=None
            ),
            "last_sequence": max(
                (row["source_revision"] for row in records), default=None
            ),
            "gap": None,
            "sequence_basis": "durable_source_revision",
        }
    elif command in {"plan.graph", "plan.overlay"}:
        if fingerprint is None:
            raise ValueError("plan_not_selected")
        plan = state.admitted_plans[fingerprint].selected_plan
        if command == "plan.graph":
            records = graph_records(plan, fingerprint)
            plan_info = _plan_package_records(
                runtime, state, fingerprint, namespace, "plan.show"
            )
            records.extend(
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"registry_association", "is_default"}
                }
                for item in plan_info
            )
            base = {
                **base,
                "package_registry_observation": {
                    "source_revision": revision,
                    "consistency": "separate_mutable_association",
                    "association": plan_info[0]["registry_association"],
                },
            }
            pin = fingerprint
            base = {
                **base,
                "plan_fingerprint": fingerprint,
                "observation": {
                    **base["observation"],
                    "consistency": "immutable_selected_authority",
                },
            }
        else:
            records = [
                run_projection(runtime, state, key)
                for key, run in state.runs.items()
                if str(run.run_ref.plan_ref.authority_fingerprint) == fingerprint
            ]
            records.extend(overlay_records(plan, fingerprint, state))
            for activation in state.activations.values():
                if str(activation.plan_ref.authority_fingerprint) == fingerprint:
                    records.append(
                        {
                            "kind": "activation",
                            "id": activation.activation_id,
                            "graph_node_id": activation.graph_node_id,
                            "stage_kind_id": str(activation.stage_kind_id),
                            "claimed_by_run_id": activation.claimed_by_run_id,
                        }
                    )
            base = {
                **base,
                "plan_fingerprint": fingerprint,
                "edge_traversal": {
                    "availability": "unsupported",
                    "count": None,
                    "reason": "see_per_relationship_exact_trace_availability",
                },
            }
    elif command in {"runs.list", "runs.show", "status"}:
        records = [
            run_projection(runtime, state, key)
            for key, run in state.runs.items()
            if (run_id is None or key == run_id)
            and (
                fingerprint is None
                or str(run.run_ref.plan_ref.authority_fingerprint) == fingerprint
            )
        ]
        if command == "status":
            records.extend(_status_records(state, fingerprint))
        base = {
            **base,
            "selected_plan_fingerprint": None
            if state.default_plan_ref is None
            else str(state.default_plan_ref.authority_fingerprint),
            "dispatch_suspension": None
            if state.dispatch_suspension is None
            else {
                key: json_ready(getattr(state.dispatch_suspension, key))
                for key in (
                    "suspension_id",
                    "status",
                    "generation",
                    "dispatch_generation",
                )
            },
        }
    elif command == "trace.show":
        from millrace.adapters.cli.status import _event_projection, _event_rows

        records = [
            {
                "kind": "trace",
                "id": str(getattr(item, "record_id")),
                **_event_projection(
                    item,
                    run_id_override=run_id
                    if run_id is not None
                    and getattr(item, "input_id")
                    == state.runs[run_id].created_by_input_id
                    else None,
                ),
            }
            for item in _event_rows(state.governance_events, state.traces)
        ]
        records = [
            item
            for item in records
            if (fingerprint is None or item.get("plan_fingerprint") == fingerprint)
            and (run_id is None or item.get("run_id") == run_id)
        ]
    elif command in {"plan.show", "package.list", "package.inspect"}:
        records = _plan_package_records(runtime, state, fingerprint, namespace, command)
    elif command == "daemon.history":
        records = _daemon_records(runtime, state, filters["daemon_id"])
    elif command in {"workspace.check", "workspace.identity", "doctor"}:
        records = [
            {
                "kind": "workspace",
                "id": base["identity"]["workspace_id"],
                "initialized": True,
                "selected_plan_fingerprint": None
                if state.default_plan_ref is None
                else str(state.default_plan_ref.authority_fingerprint),
                "admitted_plan_count": len(state.admitted_plans),
            }
        ]
    if command == "doctor":
        from millrace.adapters.cli.diagnostic_projection import doctor_diagnostics

        records.extend(doctor_diagnostics(runtime, state, fingerprint))
    return page_records(records, base, namespace, pin=pin, filters=filters)


def _plan_package_records(
    runtime: OpenRuntimeContext,
    state: Any,
    fingerprint: str | None,
    namespace: object,
    command: str,
) -> list[dict[str, Any]]:
    from millrace.adapters.cli.plans import _admitted_plan_projection

    registry = runtime.store.load_workflow_package_registry(runtime.cas_store)
    records = []
    if command == "plan.show":
        for fp, admitted in state.admitted_plans.items():
            if fingerprint is not None and str(fp) != fingerprint:
                continue
            pin = admitted.selected_plan.workflow_package_pin
            association: dict[str, Any] = {
                "registry_match": "missing",
                "reason": "no_package_pin",
            }
            if pin is not None:
                candidates = [
                    row
                    for row in registry.records
                    if row.package_id == pin.package_id
                    and row.package_version == pin.package_version
                ]
                matching = [
                    row
                    for row in candidates
                    if row.package_format_version == pin.package_format_version
                    and all(
                        any(
                            asset.asset_id == selected.asset_id
                            and asset.content_digest == selected.content_digest
                            for asset in row.assets
                        )
                        for selected in pin.selected_asset_pins
                    )
                    and all(
                        any(
                            dependency.get("package_id") == selected.package_id
                            and dependency.get("package_version")
                            == selected.package_version
                            and dependency.get("package_format_version")
                            == selected.package_format_version
                            for dependency in row.dependencies
                        )
                        for selected in pin.selected_dependency_pins
                    )
                ]
                association = {
                    "registry_match": "matched"
                    if matching
                    else "mismatch"
                    if candidates
                    else "missing",
                    "basis": "selected_asset_content_digests",
                    "records": [_package_record(row) for row in matching],
                }
            records.append(
                {
                    "kind": "plan",
                    "id": str(fp),
                    **_admitted_plan_projection(
                        admitted, is_default=state.default_plan_ref == admitted.plan_ref
                    ),
                    "workflow_package_pin": json_ready(pin),
                    "registry_association": association,
                }
            )
    else:
        for row in registry.records:
            if not row.is_current:
                continue
            if getattr(namespace, "package_id", None) is not None and (
                row.package_id,
                row.package_version,
            ) != (
                getattr(namespace, "package_id"),
                getattr(namespace, "package_version"),
            ):
                continue
            records.append(_package_record(row))
        if command == "package.inspect" and not records:
            raise ValueError("target_mismatch")
    return records


def _package_record(row: Any) -> dict[str, Any]:
    fields = (
        "package_id",
        "package_version",
        "package_generation",
        "package_format_version",
        "status",
        "status_generation",
        "manifest_digest",
        "package_digest",
        "source_kind",
        "source_digest",
        "source_provenance_digest",
        "import_record_digest",
    )
    return {
        "kind": "package",
        "id": row.record_id,
        **{key: getattr(row, key) for key in fields},
        "latest_registry_audit_id": row.latest_audit_id,
        "assets": [
            {"asset_id": asset.asset_id, "content_digest": asset.content_digest}
            for asset in row.assets
        ],
    }


def _daemon_records(
    runtime: OpenRuntimeContext, state: Any, daemon_id: str | None
) -> list[dict[str, Any]]:
    records = runtime.store.retained_daemon_records()
    if daemon_id is not None and not any(
        record["daemon_id"] == daemon_id for record in records
    ):
        raise ValueError("daemon_not_found")
    output = []
    for record in records:
        if daemon_id is not None and record["daemon_id"] != daemon_id:
            continue
        output.append(
            {
                "kind": "daemon",
                "id": record["daemon_id"],
                "record": record,
                "process_observation": unavailable(
                    "durable_history_only; use_daemon_inspect_for_fresh_challenge"
                ),
                "provider_heartbeat": unavailable("not_retained"),
            }
        )
        for row in runtime.store.retained_daemon_sessions(record["daemon_id"]):
            session = state.runner_sessions.get(row["session_id"])
            times = (
                []
                if session is None
                else [
                    (field, getattr(session, field))
                    for field in (
                        "created_at",
                        "start_intent_at",
                        "started_at",
                        "ended_at",
                    )
                    if getattr(session, field) is not None
                ]
            )
            latest = max(times, key=lambda value: value[1]) if times else None
            output.append(
                {
                    "kind": "daemon_session",
                    "id": record["daemon_id"] + ":" + str(row["sequence"]),
                    **row,
                    "last_known_durable_progress": {
                        "availability": "available" if latest else "unavailable",
                        "observed_at": None if latest is None else latest[1],
                        "source": None
                        if latest is None
                        else "runner_session." + latest[0],
                        "scope": "retained_session_lifecycle; not_provider_heartbeat",
                    },
                }
            )
    return output


def _status_records(state: Any, fingerprint: str | None) -> list[dict[str, Any]]:
    from millrace.operator.status import operator_status

    status = operator_status(state, plan_fingerprint=fingerprint, max_events=0)
    records: list[dict[str, Any]] = []
    fields = {
        "queue_families": (
            "queue_family_id",
            "ready_count",
            "active_count",
            "closed_count",
            "quarantined_count",
            "operator_wait_count",
        ),
        "stage_kinds": (
            "stage_kind_id",
            "partition_id",
            "runner_binding_id",
            "ready_count",
            "active_count",
            "closed_count",
            "operator_wait_count",
        ),
        "operator_waits": (
            "wait_id",
            "operator_wait_id",
            "source_action_id",
            "lineage_id",
            "selected_plan_fingerprint",
            "source_run_id",
            "source_graph_node_id",
            "status",
            "created_input_id",
            "resolved_input_id",
            "payload_digest",
            "target_graph_node_id",
        ),
        "quarantines": (
            "record_id",
            "source_run_id",
            "work_item_id",
            "action_id",
            "created_by_input_id",
            "quarantine_kind",
            "lineage_id",
            "policy_id",
        ),
        "recovery_attempts": (
            "record_id",
            "source_run_id",
            "policy_id",
            "attempt_count",
            "phase",
            "plan_fingerprint",
        ),
        "cooldown_waits": (
            "wait_id",
            "policy_id",
            "lineage_id",
            "recovery_attempt_record_id",
            "plan_fingerprint",
            "attempt_count",
            "source_run_id",
            "source_work_item_id",
            "source_activation_id",
            "recovery_action_id",
            "target_stage_kind_id",
            "target_graph_node_id",
            "target_runner_binding_id",
            "created_input_id",
            "created_at",
            "due_at",
            "consumed_input_id",
            "consumed_at",
            "resulting_recovery_activation_id",
        ),
    }
    for collection, names in fields.items():
        for item in getattr(status, collection):
            projection = {key: json_ready(getattr(item, key)) for key in names}
            records.append(
                {"kind": collection, "id": str(projection[names[0]]), **projection}
            )
    for closure in state.queue_closures.values():
        selected = str(closure.selected_plan_ref.authority_fingerprint)
        if fingerprint is not None and selected != fingerprint:
            continue
        records.append(
            {
                "kind": "queue_closure",
                "id": closure.closure_id,
                "plan_fingerprint": selected,
                "target_kind": closure.target_kind,
                "target_id": closure.target_id,
                "created_by_input_id": closure.created_by_input_id,
            }
        )
        for role in ("closed_work_item_ids", "closed_activation_ids", "closed_run_ids"):
            for member in getattr(closure, role):
                records.append(
                    {
                        "kind": "queue_closure_member",
                        "id": digest([closure.closure_id, role, member]),
                        "closure_id": closure.closure_id,
                        "role": role,
                        "member_id": member,
                        "plan_fingerprint": selected,
                    }
                )
    return records


def _native_public(control: Any) -> dict[str, Any]:
    from millrace.substrate._sqlite_run_controls import native_witness_digest

    native = control.native
    return {
        "control_state": control.state,
        "owner": native["owner"],
        "profile": native["profile"],
        "witness_digest": native_witness_digest(control),
        "witness_source_revision": control.source_revision,
        "fresh_admission_required": True,
        "aftermath_reason": native.get("aftermath_reason"),
        "cleanup": native.get("cleanup", "not_observed"),
        "daemon_cleanup": "unproved",
        "workflow_recovery": "not_granted",
        "continuation": native.get("continuation", control.state),
    }
