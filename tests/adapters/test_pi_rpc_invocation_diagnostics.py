"""Native invocation failures must remain diagnosable after attempt cleanup."""

import json
from types import SimpleNamespace

import pytest

from millrace.adapters.pi_rpc_client import PiRpcError
from millrace.adapters.pi_rpc_diagnostics import native_failure_diagnostics
from millrace.adapters.pi_rpc_event_diagnostics import PiEventHistory
from millrace.adapters.runner_contract import (
    AdapterErrorResult,
    DispatchEcho,
    adapter_error_diagnostic_bytes,
)
from tests.adapters.test_pi_rpc_adapter import request
from tests.adapters.test_pi_rpc_result_diagnostics import failed_session


@pytest.mark.parametrize(
    "error,reason",
    [
        (PiRpcError("agent end snapshot"), "agent_end_snapshot"),
        (PiRpcError("usage snapshot conflict"), "usage_snapshot_conflict"),
        (PiRpcError("premature EOF"), "premature_eof"),
        (PiRpcError("deadline", "timeout"), "deadline"),
        (PiRpcError("retry boundary"), "retry_boundary"),
        (PiRpcError("retry sequence"), "retry_sequence"),
        (PiRpcError("retry outcome"), "retry_outcome"),
        (PiRpcError("compaction boundary"), "compaction_boundary"),
        (PiRpcError("compaction outcome"), "compaction_outcome"),
        (PiRpcError("compaction result"), "compaction_result"),
        (PiRpcError("summary boundary"), "summary_boundary"),
        (PiRpcError("summary sequence"), "summary_sequence"),
    ],
)
def test_native_reason_survives_attempt_disposal(tmp_path, error, reason):
    session = failed_session(tmp_path, error)
    outcome = session.outcome
    assert outcome.diagnostics["failure_phase"] == "invocation"
    assert outcome.diagnostics["failure_reason"] == reason
    session.cleanup()
    assert not session.attempt.path.exists()
    assert outcome.diagnostics["failure_reason"] == reason


@pytest.mark.parametrize(
    "error", [PiRpcError("secret-value /private/path"), ValueError("secret-value")]
)
def test_unclassified_native_error_never_retains_exception_contents(tmp_path, error):
    session = failed_session(tmp_path, error)
    raw = adapter_error_diagnostic_bytes(session.outcome, request=session.request)
    diagnostic = json.loads(raw)["diagnostics"]
    assert diagnostic["failure_phase"] == "invocation"
    assert diagnostic["failure_reason"] == "unclassified_invocation_failure"
    assert "secret-value" not in json.dumps(diagnostic)
    assert "/private/path" not in json.dumps(diagnostic)
    session.cleanup()


def test_native_os_failure_retains_errno_without_path(tmp_path):
    session = failed_session(tmp_path, OSError(32, "secret-value", "/private/path"))
    raw = adapter_error_diagnostic_bytes(session.outcome, request=session.request)
    diagnostic = json.loads(raw)["diagnostics"]
    assert diagnostic["failure_reason"] == "native_os_error"
    assert diagnostic["os_errno"] == 32
    assert "secret-value" not in json.dumps(dict(diagnostic))
    assert "/private/path" not in json.dumps(dict(diagnostic))
    session.cleanup()


def test_recovery_event_history_projects_through_bounded_adapter_error():
    history = PiEventHistory()
    history.record(
        {
            "type": "auto_retry_start",
            "attempt": 1,
            "maxAttempts": 3,
            "delayMs": 20,
            "errorMessage": "private provider detail",
            "prompt": "private candidate prompt",
            "toolPayload": "private tool result",
        },
        {
            "run": False,
            "turn": False,
            "message_open": False,
            "tools_active": False,
            "continuation": False,
            "will_retry": False,
            "auxiliary": False,
            "runs": 1,
            "users": 1,
            "settled": 0,
            "recovery": "none",
        },
    )
    client = SimpleNamespace(
        lifecycle=SimpleNamespace(
            uncertain={"intrinsic recovery"}, event_history=history
        ),
        stream_bytes={},
        stream_events={},
        stream_error_codes={},
        errors=[],
        pending={},
        stderr=b"",
        process=SimpleNamespace(poll=lambda: 1),
    )
    diagnostics = native_failure_diagnostics(
        client, PiRpcError("recovery boundary")
    )
    invocation = request()
    outcome = AdapterErrorResult.from_unredacted(
        adapter_id=invocation.adapter_id,
        error_kind="invocation_failed",
        dispatch_echo=DispatchEcho.from_dispatch_envelope(
            invocation.dispatch_envelope,
            correlation_id=invocation.correlation_id,
            selected_adapter_kind=invocation.selected_adapter_kind,
        ),
        redaction_policy=invocation.redaction_policy,
        diagnostics=diagnostics,
    )

    raw = adapter_error_diagnostic_bytes(outcome, request=invocation)
    assert raw is not None
    assert len(raw) <= 16 * 1024
    projected = json.loads(raw)["diagnostics"]["event_history"]
    assert projected["latest_event"]["type"] == "auto_retry_start"
    assert projected["latest_event"]["fields"]["attempt"] == 1
    serialized = json.dumps(projected)
    assert "private provider detail" not in serialized
    assert "private candidate prompt" not in serialized
    assert "private tool result" not in serialized
