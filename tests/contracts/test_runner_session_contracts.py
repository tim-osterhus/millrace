from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, cast

import pytest

from millrace.adapters.runner_contract import (
    START_REFUSAL_DIAGNOSTIC_MAX_BYTES,
    AdapterErrorResult,
    AdapterInvocationRequest,
    CleanupPending,
    Contradiction,
    DispatchEcho,
    RedactionPolicy,
    RunnerCancellationOperationResult,
    RunnerCleanupResult,
    RunnerSessionReconcileRequest,
    StartedSession,
    StartIndeterminate,
    StartRefusedBeforeExternalWork,
    Terminal,
    Unsupported,
    VerifiedLive,
    runner_cancellation_diagnostic_digest,
    start_refusal_diagnostic_bytes,
    start_refusal_diagnostic_digest,
)
from millrace.contracts.runner import (
    RUNNER_SESSION_LOCATOR_MAX_BYTES,
    RunnerDispatchEnvelope,
    RunnerResultEvidence,
    runner_session_locator_bytes,
    runner_session_locator_from_bytes,
)


class _Handle:
    def poll_completion(self) -> None:
        return None

    def request_cancel(self) -> RunnerCancellationOperationResult:
        raise NotImplementedError

    def terminate(self) -> RunnerCancellationOperationResult:
        raise NotImplementedError

    def kill(self) -> RunnerCancellationOperationResult:
        raise NotImplementedError

    def cleanup(self) -> RunnerCleanupResult:
        raise NotImplementedError


def _dispatch_values() -> dict[str, object]:
    return {
        "run_id": "run-1",
        "session_id": "session-1",
        "dispatch_generation": 1,
        "session_fencing_token": "session-fence-1",
        "work_item_id": "work-1",
        "activation_id": "activation-1",
        "plan_fingerprint": "plan-fingerprint",
        "plan_id": "plan-1",
        "workflow_id": "workflow-1",
        "workflow_version": "1.0.0",
        "graph_id": "graph-1",
        "claim_id": "claim-1",
        "generation": 1,
        "fencing_token": "run-fence-1",
        "queue_family_id": "queue-1",
        "stage_kind_id": "stage-1",
        "graph_node_id": "node-1",
        "runner_binding_id": "runner-1",
        "external_enqueue_route_id": None,
        "entrypoint_asset_id": None,
        "skill_asset_ids": (),
        "artifact_schema_ids": (),
        "work_item_payload": {},
        "governance_context": {},
        "terminal_options": (),
        "selected_join_evidence": None,
        "selected_wait_evidence": None,
        "context_checkout": _context_checkout(),
    }


def _context_checkout() -> dict[str, str]:
    return {
        "manifest_digest": "sha256:" + "a" * 64,
        "binding_id": "runner-context-1",
        "router_asset_id": "router-asset-1",
        "checkout_relative_path": "checkout/session-1/1",
        "router_relative_path": "checkout/session-1/1/CONTEXT.md",
    }


def _result_values() -> dict[str, object]:
    return {
        "run_id": "run-1",
        "session_id": "session-1",
        "dispatch_generation": 1,
        "session_fencing_token": "session-fence-1",
        "plan_fingerprint": "plan-fingerprint",
        "claim_id": "claim-1",
        "generation": 1,
        "fencing_token": "run-fence-1",
        "stage_kind_id": "stage-1",
        "graph_node_id": "node-1",
        "runner_binding_id": "runner-1",
        "marker": "done",
        "adapter_provenance": None,
        "observation_payload": {},
        "artifact_payload": {},
    }


def test_session_fields_are_required_in_dispatch_echo_and_result_evidence() -> None:
    dispatch_constructor = cast(Any, RunnerDispatchEnvelope)
    result_constructor = cast(Any, RunnerResultEvidence)
    echo_constructor = cast(Any, DispatchEcho)
    assert RunnerDispatchEnvelope.schema_version == 7
    assert RunnerResultEvidence.schema_version == 3
    dispatch_constructor(**_dispatch_values())
    result_constructor(**_result_values())

    for field_name in (
        "session_id",
        "dispatch_generation",
        "session_fencing_token",
    ):
        dispatch_values = _dispatch_values()
        dispatch_values.pop(field_name)
        with pytest.raises(TypeError):
            dispatch_constructor(**dispatch_values)

        result_values = _result_values()
        result_values.pop(field_name)
        with pytest.raises(TypeError):
            result_constructor(**result_values)

        echo_values = {
            "run_id": "run-1",
            "session_id": "session-1",
            "dispatch_generation": 1,
            "session_fencing_token": "session-fence-1",
            "claim_id": "claim-1",
            "generation": 1,
            "fencing_token": "run-fence-1",
            "plan_fingerprint": "plan-fingerprint",
            "stage_kind_id": "stage-1",
            "graph_node_id": "node-1",
            "runner_binding_id": "runner-1",
            "correlation_id": "correlation-1",
            "selected_authority_digest": "sha256:" + "a" * 64,
        }
        echo_values.pop(field_name)
        with pytest.raises(TypeError):
            echo_constructor(**echo_values)


def test_adapter_request_refuses_session_authority_mismatch() -> None:
    dispatch = RunnerDispatchEnvelope(**_dispatch_values())  # type: ignore[arg-type]
    request_values = {
        "adapter_id": "adapter-1",
        "selected_runner_binding_id": dispatch.runner_binding_id,
        "selected_adapter_kind": "fake",
        "dispatch_envelope": dispatch,
        "session_id": dispatch.session_id,
        "dispatch_generation": dispatch.dispatch_generation,
        "session_fencing_token": dispatch.session_fencing_token,
        "timeout_seconds": 1.0,
        "correlation_id": "correlation-1",
        "redaction_policy": RedactionPolicy("test"),
        "cancellation_token": "cancel-1",
    }

    AdapterInvocationRequest(**request_values)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="session_id"):
        AdapterInvocationRequest(  # type: ignore[arg-type]
            **{**request_values, "session_id": "other-session"}
        )


def test_reconcile_request_carries_v7_selected_authority() -> None:
    dispatch = RunnerDispatchEnvelope(**_dispatch_values())  # type: ignore[arg-type]
    invocation = AdapterInvocationRequest(
        adapter_id="adapter-1",
        selected_runner_binding_id=dispatch.runner_binding_id,
        selected_adapter_kind="fake",
        dispatch_envelope=dispatch,
        session_id=dispatch.session_id,
        dispatch_generation=dispatch.dispatch_generation,
        session_fencing_token=dispatch.session_fencing_token,
        timeout_seconds=1,
        correlation_id="correlation-1",
        redaction_policy=RedactionPolicy("test"),
        cancellation_token="cancel-1",
    )

    reconcile = RunnerSessionReconcileRequest(
        invocation,
        {"provider_request_id": "owned-request"},
    )

    assert reconcile.invocation_request.dispatch_envelope.schema_version == 7
    payload = reconcile.invocation_request.dispatch_envelope.payload()
    assert payload["record_kind"] == "runner_dispatch_envelope"
    assert payload["schema_version"] == 7
    assert payload["context_checkout"] == _context_checkout()
    for field_name in (
        "run_id",
        "claim_id",
        "plan_fingerprint",
        "runner_binding_id",
        "stage_kind_id",
        "graph_node_id",
        "queue_family_id",
        "session_id",
        "dispatch_generation",
        "session_fencing_token",
        "generation",
        "fencing_token",
    ):
        assert payload[field_name] == _dispatch_values()[field_name]
    assert reconcile.invocation_request.session_id == "session-1"
    assert reconcile.invocation_request.dispatch_generation == 1
    assert reconcile.invocation_request.session_fencing_token == "session-fence-1"
    assert reconcile.invocation_request.correlation_id == "correlation-1"


def test_dispatch_echo_refuses_session_authority_mismatch() -> None:
    dispatch = RunnerDispatchEnvelope(**_dispatch_values())  # type: ignore[arg-type]
    echo = DispatchEcho.from_dispatch_envelope(
        dispatch,
        correlation_id="correlation-1",
        selected_adapter_kind="fake",
    )

    with pytest.raises(ValueError, match="dispatch echo mismatch"):
        replace(echo, session_fencing_token="stale-fence").validate_against(
            dispatch,
            correlation_id="correlation-1",
            selected_adapter_kind="fake",
        )


@pytest.mark.parametrize(
    "field_name",
    (
        "run_id",
        "session_id",
        "dispatch_generation",
        "session_fencing_token",
        "work_item_id",
        "activation_id",
        "plan_fingerprint",
        "plan_id",
        "workflow_id",
        "workflow_version",
        "graph_id",
        "claim_id",
        "generation",
        "fencing_token",
        "queue_family_id",
        "stage_kind_id",
        "graph_node_id",
        "runner_binding_id",
        "external_enqueue_route_id",
        "entrypoint_asset_id",
        "skill_asset_ids",
        "artifact_schema_ids",
        "work_item_payload",
        "governance_context",
        "terminal_options",
        "selected_join_evidence",
        "selected_wait_evidence",
        "context_checkout",
    ),
)
def test_dispatch_echo_full_authority_proof_refuses_every_v7_mismatch(
    field_name: str,
) -> None:
    dispatch = RunnerDispatchEnvelope(**_dispatch_values())  # type: ignore[arg-type]
    echo = DispatchEcho.from_dispatch_envelope(
        dispatch,
        correlation_id="correlation-1",
        selected_adapter_kind="fake",
    )
    changed = dict(_dispatch_values())
    current = changed[field_name]
    changed[field_name] = (
        "changed"
        if current is None
        else ("changed",)
        if isinstance(current, tuple)
        else {"changed": True}
        if isinstance(current, dict)
        else current + 1
        if isinstance(current, int)
        else f"{current}-changed"
    )
    mismatched = RunnerDispatchEnvelope(**_dispatch_values())  # type: ignore[arg-type]
    object.__setattr__(mismatched, field_name, changed[field_name])

    with pytest.raises(ValueError, match="dispatch echo mismatch"):
        echo.validate_against(
            mismatched,
            correlation_id="correlation-1",
            selected_adapter_kind="fake",
        )


def test_context_checkout_descriptor_is_exact_frozen_and_strict() -> None:
    dispatch = RunnerDispatchEnvelope(**_dispatch_values())  # type: ignore[arg-type]

    descriptor = dispatch.context_checkout
    assert isinstance(descriptor, Mapping)
    assert set(descriptor) == set(_context_checkout())
    with pytest.raises(TypeError):
        descriptor["binding_id"] = "mutated"  # type: ignore[index]

    for malformed in (
        {**_context_checkout(), "extra": "forbidden"},
        {
            key: value
            for key, value in _context_checkout().items()
            if key != "router_relative_path"
        },
        {**_context_checkout(), "manifest_digest": "SHA256:" + "a" * 64},
        {**_context_checkout(), "binding_id": " "},
        {**_context_checkout(), "checkout_relative_path": "../escape"},
        {
            **_context_checkout(),
            "router_relative_path": "checkout/session-1/1/CONTEXT.md/",
        },
    ):
        with pytest.raises((TypeError, ValueError)):
            RunnerDispatchEnvelope(
                **{**_dispatch_values(), "context_checkout": malformed}
            )  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "descriptor_field",
    (
        "manifest_digest",
        "binding_id",
        "router_asset_id",
        "checkout_relative_path",
        "router_relative_path",
    ),
)
def test_dispatch_echo_refuses_each_context_checkout_mutation(
    descriptor_field: str,
) -> None:
    dispatch = RunnerDispatchEnvelope(**_dispatch_values())  # type: ignore[arg-type]
    echo = DispatchEcho.from_dispatch_envelope(
        dispatch,
        correlation_id="correlation-1",
        selected_adapter_kind="fake",
    )
    changed = _context_checkout()
    changed[descriptor_field] = {
        "manifest_digest": "sha256:" + "b" * 64,
        "binding_id": "runner-context-1-changed",
        "router_asset_id": "router-asset-1-changed",
        "checkout_relative_path": "checkout/session-1/2",
        "router_relative_path": "checkout/session-1/2/CONTEXT.md",
    }[descriptor_field]
    mismatched = RunnerDispatchEnvelope(**_dispatch_values())  # type: ignore[arg-type]
    object.__setattr__(mismatched, "context_checkout", changed)

    with pytest.raises(ValueError, match="dispatch echo mismatch"):
        echo.validate_against(
            mismatched,
            correlation_id="correlation-1",
            selected_adapter_kind="fake",
        )


def test_dispatch_echo_full_authority_proof_refuses_adapter_kind_mismatch() -> None:
    dispatch = RunnerDispatchEnvelope(**_dispatch_values())  # type: ignore[arg-type]
    echo = DispatchEcho.from_dispatch_envelope(
        dispatch,
        correlation_id="correlation-1",
        selected_adapter_kind="fake",
    )

    with pytest.raises(ValueError, match="dispatch echo mismatch"):
        echo.validate_against(
            dispatch,
            correlation_id="correlation-1",
            selected_adapter_kind="foreign",
        )


def test_runner_session_outcomes_are_exact_typed_records() -> None:
    dispatch = RunnerDispatchEnvelope(**_dispatch_values())  # type: ignore[arg-type]
    echo = DispatchEcho.from_dispatch_envelope(
        dispatch,
        correlation_id="correlation-1",
        selected_adapter_kind="fake",
    )
    request = AdapterInvocationRequest(
        adapter_id="adapter-1",
        selected_runner_binding_id=dispatch.runner_binding_id,
        selected_adapter_kind="fake",
        dispatch_envelope=dispatch,
        session_id=dispatch.session_id,
        dispatch_generation=dispatch.dispatch_generation,
        session_fencing_token=dispatch.session_fencing_token,
        timeout_seconds=1,
        correlation_id="correlation-1",
        redaction_policy=RedactionPolicy("test"),
        cancellation_token="cancel-1",
    )
    error = AdapterErrorResult.from_unredacted(
        adapter_id="adapter-1",
        error_kind="invocation_failed",
        dispatch_echo=echo,
        redaction_policy=RedactionPolicy("test"),
    )
    handle = _Handle()

    assert StartedSession(echo, handle, "handle-1", {}).outcome_kind == "started"
    assert (
        StartRefusedBeforeExternalWork(
            echo,
            error,
            start_refusal_diagnostic_digest(error),
        ).outcome_kind
        == "refused_before_external_work"
    )
    assert StartIndeterminate(echo, None, "sha256:" + "b" * 64).outcome_kind == (
        "indeterminate"
    )
    reconcile = RunnerSessionReconcileRequest(request, {})
    assert reconcile.invocation_request is request
    assert VerifiedLive(echo, handle, "handle-1", {}).outcome_kind == "verified_live"
    assert Terminal(echo, error, "not_required").outcome_kind == "terminal"
    assert CleanupPending(echo, handle, "handle-1").outcome_kind == "cleanup_pending"
    assert Unsupported(echo).outcome_kind == "unsupported"
    assert Contradiction(echo, "sha256:" + "c" * 64).outcome_kind == "contradiction"
    operation_diagnostic = {"operation": "terminate"}
    assert (
        RunnerCancellationOperationResult(
            "terminate",
            "succeeded",
            1,
            2,
            operation_diagnostic,
            runner_cancellation_diagnostic_digest(operation_diagnostic),
        ).result
        == "succeeded"
    )
    cleanup_diagnostic = {"cleanup": "complete"}
    assert (
        RunnerCleanupResult(
            "complete",
            1,
            2,
            cleanup_diagnostic,
            runner_cancellation_diagnostic_digest(cleanup_diagnostic),
        ).disposition
        == "complete"
    )

    with pytest.raises(ValueError, match="diagnostic_digest"):
        RunnerCancellationOperationResult(
            "kill",
            "succeeded",
            1,
            2,
            {"actual": "content"},
            "sha256:" + "f" * 64,
        )


def test_runner_session_locator_codec_is_canonical_bounded_and_mapping_only() -> None:
    prefix = len(b'{"value":""}')
    locator = {"value": "x" * (RUNNER_SESSION_LOCATOR_MAX_BYTES - prefix)}
    payload = runner_session_locator_bytes(locator)

    assert len(payload) == RUNNER_SESSION_LOCATOR_MAX_BYTES
    assert runner_session_locator_bytes(runner_session_locator_from_bytes(payload)) == (
        payload
    )
    with pytest.raises(ValueError, match="at most"):
        runner_session_locator_from_bytes(payload + b" ")
    with pytest.raises(ValueError, match="mapping"):
        runner_session_locator_from_bytes(b"[]")
    with pytest.raises(ValueError, match="JSON"):
        runner_session_locator_from_bytes(b"{")
    with pytest.raises(ValueError, match="canonical"):
        runner_session_locator_from_bytes(b'{"value": "x"}')
    with pytest.raises(ValueError, match="authority"):
        runner_session_locator_from_bytes(b'{"value":1.5}')
    with pytest.raises(ValueError, match="selected authority"):
        runner_session_locator_from_bytes(b'{"nested":{"claim_id":"hostile-claim"}}')


@pytest.mark.parametrize(
    "field_name",
    (
        *tuple(_dispatch_values()),
        "selected_adapter_kind",
        "selected_runner_binding_id",
        "correlation_id",
        "cancellation_token",
        "adapter_id",
        "handle_id",
    ),
)
def test_runner_session_locator_rejects_every_invocation_authority_key(
    field_name: str,
) -> None:
    payload = json.dumps(
        {"nested": {field_name: "hostile"}},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    with pytest.raises(ValueError, match="selected authority"):
        runner_session_locator_from_bytes(payload)


def test_start_refusal_diagnostic_owns_real_bounded_redacted_content() -> None:
    dispatch = RunnerDispatchEnvelope(**_dispatch_values())  # type: ignore[arg-type]
    echo = DispatchEcho.from_dispatch_envelope(
        dispatch,
        correlation_id="correlation-1",
        selected_adapter_kind="fake",
    )
    error = AdapterErrorResult.from_unredacted(
        adapter_id="adapter-1",
        error_kind="invocation_failed",
        dispatch_echo=echo,
        redaction_policy=RedactionPolicy("test", ("secret",)),
        diagnostics={"message": "secret failed", "attempt": 1},
    )
    payload = start_refusal_diagnostic_bytes(error)
    digest = start_refusal_diagnostic_digest(error)

    assert b"secret" not in payload
    assert b"[REDACTED]" in payload
    assert b'"error_kind":"invocation_failed"' in payload
    assert StartRefusedBeforeExternalWork(echo, error, digest).diagnostic_digest == (
        digest
    )
    with pytest.raises(ValueError, match="diagnostic_digest"):
        StartRefusedBeforeExternalWork(echo, error, "sha256:" + "f" * 64)

    oversized = AdapterErrorResult(
        adapter_id="adapter-1",
        error_kind="invocation_failed",
        redaction_policy_id="test",
        dispatch_echo=echo,
        diagnostics={"message": "x" * START_REFUSAL_DIAGNOSTIC_MAX_BYTES},
    )
    oversized_payload = start_refusal_diagnostic_bytes(oversized)
    oversized_summary = json.loads(oversized_payload)
    assert len(oversized_payload) <= START_REFUSAL_DIAGNOSTIC_MAX_BYTES
    assert oversized_summary["diagnostics"]["truncated"] is True
    assert oversized_summary["diagnostics"]["observed_bytes"] > (
        START_REFUSAL_DIAGNOSTIC_MAX_BYTES
    )
    assert oversized_summary["diagnostics"]["full_diagnostic_digest"].startswith(
        "sha256:"
    )


@pytest.mark.parametrize(
    "constructor",
    (
        lambda echo: StartedSession(echo, object(), "handle-1", {}),
        lambda echo: VerifiedLive(echo, object(), "handle-1", {}),
        lambda echo: CleanupPending(echo, object(), "handle-1"),
    ),
)
def test_live_session_records_require_complete_handle_protocol(
    constructor: Any,
) -> None:
    dispatch = RunnerDispatchEnvelope(**_dispatch_values())  # type: ignore[arg-type]
    echo = DispatchEcho.from_dispatch_envelope(
        dispatch,
        correlation_id="correlation-1",
        selected_adapter_kind="fake",
    )

    with pytest.raises(TypeError, match="handle"):
        constructor(echo)
