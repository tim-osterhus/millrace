"""Rejected final text remains diagnostic evidence, never a runner result."""

import json
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from millrace.adapters.pi_rpc import PiRpcAdapter, PiSession
from millrace.adapters.pi_rpc_client import PiRpcError, parse_candidate
from millrace.adapters.pi_rpc_config import PiAttempt, PiRpcConfig
from millrace.adapters.runner_contract import (
    AdapterErrorResult,
    RedactionPolicy,
    runner_evidence_from_adapter_outcome,
)
from tests.adapters.test_pi_rpc_adapter import request


def candidate(**changes):
    return json.dumps(
        {
            "marker": "TASK_COMPLETE",
            "artifact_payload_candidate": None,
            "observation_payload_candidate": None,
            **changes,
        }
    )


def settled_client(text, *, supervised=False):
    def run():
        if isinstance(text, Exception):
            raise text
        return text

    return SimpleNamespace(
        ready=lambda: None,
        run=run,
        usage=None,
        stderr=b"",
        lifecycle=SimpleNamespace(
            uncertain=set(), settled=1, fallback_usage=lambda: None
        ),
        closed=True,
        process=SimpleNamespace(returncode=0, poll=lambda: 0),
        eof={"stdout": True, "stderr": True},
        errors=[],
        pending={},
        supervisor=SimpleNamespace(result=True, report=None) if supervised else None,
        dispose=lambda: True,
    )


def failed_session(tmp_path, text, *, policy=None, maximum=262144, cancelled=False):
    owned = tmp_path / "attempt"
    owned.mkdir()
    st = owned.stat()
    session = object.__new__(PiSession)
    session.adapter = PiRpcAdapter(
        PiRpcConfig({"adapter_id": "pi-local", "max_result_bytes": maximum}, {}, b"")
    )
    session.request = (
        request() if policy is None else replace(request(), redaction_policy=policy)
    )
    session.client = settled_client(text)
    session.attempt = PiAttempt(owned, (st.st_dev, st.st_ino), {})
    session.outcome = None
    session.cleanup_result = None
    session.cancelled = threading.Event()
    if cancelled:
        session.cancelled.set()
    session.thread = threading.Thread(target=session._run)
    session.thread.start()
    session.thread.join(2)
    assert not session.thread.is_alive()
    assert isinstance(session.outcome, AdapterErrorResult)
    return session


@pytest.mark.parametrize(
    "text,reason",
    [
        ('{"marker":', "invalid_json"),
        ('{"marker":"A","marker":"B"}', "duplicate_json_key"),
        ('{"terminal_marker":"A","artifact":null}', "invalid_envelope"),
        (candidate(extra=True), "invalid_envelope"),
        (candidate(marker=" "), "invalid_marker"),
        (candidate(artifact_payload_candidate=[]), "invalid_payload"),
        (
            candidate(observation_payload_candidate={"runner_report": 42}),
            "invalid_runner_report",
        ),
        (
            candidate(artifact_payload_candidate={"x": float("nan")}),
            "nonfinite_json_number",
        ),
        ("[" * 66 + "0" + "]" * 66, "json_nesting_bound"),
    ],
)
def test_rejected_candidate_retains_safe_parse_reason(tmp_path, text, reason):
    session = failed_session(tmp_path, text)
    result = session.outcome
    assert result.error_kind == "result_parse_failed"
    assert result.diagnostics["failure_phase"] == "candidate_parse"
    assert result.diagnostics["failure_reason"] == reason
    assert result.dispatch_echo.session_id == session.request.session_id
    with pytest.raises(TypeError):
        runner_evidence_from_adapter_outcome(result, session.request)
    session.cleanup()


def test_redaction_key_collision_retains_text_without_accepting_candidate(tmp_path):
    text = candidate(artifact_payload_candidate={"secret-value": 1, "[REDACTED]": 2})
    assert parse_candidate(text, 8192)["marker"] == "TASK_COMPLETE"
    session = failed_session(tmp_path, text)
    result = session.outcome
    assert result.error_kind == "result_parse_failed"
    assert result.diagnostics["failure_phase"] == "evidence_normalization"
    assert result.diagnostics["failure_reason"] == "redacted_mapping_key_collision"
    assert "secret-value" not in result.diagnostics["candidate_text"]
    assert result.diagnostics["candidate_text"].count("[REDACTED]") == 2
    session.cleanup()


def test_normalization_exception_content_is_never_retained(tmp_path, monkeypatch):
    from millrace.adapters import pi_rpc

    def refuse(**kwargs):
        raise TypeError("unsafe exception secret-value /private/path")

    monkeypatch.setattr(pi_rpc.AdapterSuccessResult, "from_unredacted", refuse)
    session = failed_session(tmp_path, candidate())
    diagnostic = dict(session.outcome.diagnostics)
    assert diagnostic["failure_phase"] == "evidence_normalization"
    assert diagnostic["failure_reason"] == "evidence_normalization_failed"
    assert "unsafe exception" not in json.dumps(diagnostic)
    assert "/private/path" not in json.dumps(diagnostic)
    session.cleanup()


def test_json_float_evidence_is_rejected_without_losing_failure_phase(tmp_path):
    text = candidate(artifact_payload_candidate={"unsupported_authority_float": 1.5})
    assert parse_candidate(text, 8192)["artifact_payload_candidate"]
    session = failed_session(tmp_path, text)
    assert session.outcome.diagnostics["failure_phase"] == "evidence_normalization"
    assert (
        session.outcome.diagnostics["failure_reason"] == "evidence_normalization_failed"
    )
    session.cleanup()


def test_invalid_utf8_candidate_omits_unserializable_text(tmp_path):
    session = failed_session(tmp_path, "\ud800")
    assert session.outcome.diagnostics["failure_reason"] == "invalid_text_encoding"
    assert (
        session.outcome.diagnostics["candidate_evidence_status"]
        == "omitted_unsafe_text"
    )
    assert "candidate_text" not in session.outcome.diagnostics
    session.cleanup()


def test_rejected_candidate_redacts_request_and_config_credentials(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PI_RPC_API_KEY", "config-credential")
    # Mixed JSON escapes must not bypass literal secret redaction.
    text = '{"message":"secret-\\u0076alue config-credential","extra":true}'
    session = failed_session(tmp_path, text)
    retained = session.outcome.diagnostics["candidate_text"]
    assert json.loads(retained)["message"] == "[REDACTED] [REDACTED]"
    assert "config-credential" not in json.dumps(dict(session.outcome.diagnostics))
    session.cleanup()


@pytest.mark.parametrize(
    "secret", ['quote"slash\\token', "雪-credential"], ids=["escaped", "unicode"]
)
def test_candidate_redaction_decodes_json_escaped_credentials(
    tmp_path, monkeypatch, secret
):
    monkeypatch.setenv("PI_RPC_API_KEY", secret)
    session = failed_session(tmp_path, json.dumps({"extra": secret}))
    retained = session.outcome.diagnostics["candidate_text"]
    assert json.loads(retained)["extra"] == "[REDACTED]"
    assert secret not in retained
    session.cleanup()


def test_diagnostic_key_redaction_collision_still_completes_fail_closed(tmp_path):
    policy = RedactionPolicy("redact", ("failure_phase", "failure_reason"))
    session = failed_session(tmp_path, "{}", policy=policy)
    # A single list-valued fallback avoids creating another colliding mapping.
    assert session.outcome.diagnostics["result_failure"] == (
        "candidate_parse",
        "invalid_envelope",
        "omitted_redaction_failed",
    )
    session.cleanup()


def test_redaction_expansion_precedes_serialized_candidate_bound(tmp_path):
    session = failed_session(
        tmp_path,
        json.dumps({"extra": "s" * 3000}),
        policy=RedactionPolicy("redact", ("s",)),
    )
    diagnostic = session.outcome.diagnostics
    # Policy redacts diagnostic keys too; locate the excerpt by its long value.
    excerpt = next(
        value
        for value in diagnostic.values()
        if isinstance(value, str) and len(value) > 500
    )
    assert "s" not in excerpt
    assert len(json.dumps(excerpt).encode()) <= 4096
    session.cleanup()


def test_secret_in_redaction_marker_omits_text_before_repeated_redaction(tmp_path):
    session = failed_session(
        tmp_path,
        json.dumps({"extra": "RED" * 1000}),
        policy=RedactionPolicy("redact", ("RED",)),
    )
    diagnostic = session.outcome.diagnostics
    assert diagnostic["failure_reason"] == "invalid_envelope"
    assert diagnostic["candidate_evidence_status"] == "omitted_unsafe_text"
    assert "candidate_text" not in diagnostic
    session.cleanup()


def test_materialized_credential_is_redacted_after_environment_changes(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PI_RPC_API_KEY", "current-credential")
    session = failed_session(tmp_path, "{}")
    session.attempt.env["PI_RPC_API_KEY"] = "original-credential"
    session.client = settled_client('{"text":"original-credential current-credential"}')
    session._run()
    assert "original-credential" not in session.outcome.diagnostics["candidate_text"]
    assert "current-credential" not in session.outcome.diagnostics["candidate_text"]
    session.cleanup()


@pytest.mark.parametrize("text", ['{"secret-value', '{"x":"secret-\\x76alue"}'])
def test_unsafe_string_encoding_omits_candidate_but_preserves_reason(tmp_path, text):
    session = failed_session(tmp_path, text)
    diagnostic = session.outcome.diagnostics
    assert diagnostic["failure_reason"] == "invalid_json"
    assert diagnostic["candidate_evidence_status"] == "omitted_unsafe_text"
    assert "candidate_text" not in diagnostic
    session.cleanup()


@pytest.mark.parametrize(
    "body",
    ["x" * 20000, "😀" * 10000, "\n\t" * 20000],
    ids=["ascii", "unicode", "escaped-controls"],
)
def test_retained_candidate_is_bounded_in_serialized_json_bytes(tmp_path, body):
    session = failed_session(tmp_path, json.dumps({"extra": body}))
    diagnostic = session.outcome.diagnostics
    assert diagnostic["failure_reason"] == "invalid_envelope"
    assert diagnostic["candidate_evidence_status"] == "truncated"
    assert len(json.dumps(diagnostic["candidate_text"]).encode()) <= 4096
    assert len(json.dumps(dict(diagnostic)).encode()) < 8192
    session.cleanup()


def test_over_result_bound_omits_text_and_keeps_existing_error_kind(tmp_path):
    session = failed_session(tmp_path, "x" * 8193, maximum=8192)
    assert session.outcome.error_kind == "output_too_large"
    assert session.outcome.diagnostics["failure_reason"] == "result_byte_limit"
    assert (
        session.outcome.diagnostics["candidate_evidence_status"]
        == "omitted_result_bound"
    )
    assert "candidate_text" not in session.outcome.diagnostics
    session.cleanup()


@pytest.mark.parametrize("cancelled", [False, True])
def test_protocol_and_cancellation_failures_do_not_claim_candidate_failure(
    tmp_path, cancelled
):
    text = candidate() if cancelled else PiRpcError("secret-value protocol failure")
    session = failed_session(tmp_path, text, cancelled=cancelled)
    assert session.outcome.error_kind == (
        "cancelled" if cancelled else "invocation_failed"
    )
    assert "candidate_text" not in session.outcome.diagnostics
    if cancelled:
        assert "failure_reason" not in session.outcome.diagnostics
    else:
        assert session.outcome.diagnostics["failure_phase"] == "invocation"
        assert (
            session.outcome.diagnostics["failure_reason"]
            == "unclassified_invocation_failure"
        )
    cleanup = session.cleanup()
    assert cleanup.disposition == "orphan_risk"
    assert session.cleanup() is cleanup
    assert not session.attempt.path.exists()


def test_failed_candidate_survives_idempotent_attempt_cleanup(tmp_path):
    session = failed_session(tmp_path, candidate(extra="secret-value"))
    result = session.outcome
    first = session.cleanup()
    assert first.disposition == "orphan_risk"
    assert session.cleanup() is first
    assert not session.attempt.path.exists()
    assert session.poll_completion() is result
    assert result.diagnostics["candidate_text"]
    assert "secret-value" not in result.diagnostics["candidate_text"]


@pytest.mark.parametrize("binding_failed", [False, True])
def test_content_free_recovery_summary_survives_bound_cas_reload(
    tmp_path, monkeypatch, binding_failed
):
    """Use the existing completion path and all eleven foreign-binding probes."""
    from millrace.adapters.pi_rpc_client import PiLifecycle
    from tests.adapters.test_pi_rpc_recovery import failed_run
    from tests.cli import test_cli_pi_result_diagnostics as durable

    original_loader = durable.runner_session_completion_diagnostic_from_payload

    def checked_loader(payload):
        bound = original_loader(payload)
        summary = bound.diagnostic["diagnostics"]["recovery_qualification"]
        assert summary["assistant_failure_count"] == 1
        assert summary["persisted_binding_verified"] is False
        assert summary["tainted"] is True and summary["qualified"] is False
        assert summary["reason"] == (
            "binding_failed" if binding_failed else "parser_damage"
        )
        assert len(json.dumps(dict(summary)).encode()) <= 1024
        return bound

    def lifecycle(model, prompt):
        value = PiLifecycle(model, prompt, recovery_accounting=True)
        original_event = value.event

        def event(raw):
            if raw["type"] == "auto_retry_start" and "payload" in raw:
                # A distinct validated error assistant is left unresolved; the
                # following invalid open-run retry permanently taints evidence.
                original_event(dict(type="turn_start"))
                for item in failed_run()[4:7]:
                    original_event(item)
                if binding_failed:
                    value.bind_recovery_entries({})
            original_event(raw)

        value.event = event
        return value

    monkeypatch.setattr(durable, "PiLifecycle", lifecycle)
    monkeypatch.setattr(
        durable, "runner_session_completion_diagnostic_from_payload", checked_loader
    )
    durable.test_failed_pi_diagnostic_survives_disposal_reload_and_refuses_foreign_binding(
        tmp_path,
        monkeypatch,
        supervised=True,
        large=False,
        invocation_failure="recovery",
    )
