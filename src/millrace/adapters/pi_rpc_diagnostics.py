"""Bounded native failure facts without exception contents or candidate authority."""

from __future__ import annotations

from pathlib import Path

from millrace.adapters.pi_rpc_client import PiRpcClient, PiRpcError
from millrace.adapters.pi_rpc_event_diagnostics import PiEventHistory
from millrace.adapters.pi_rpc_recovery import PiRecoveryEvidence

# Only protocol-owned literals may become durable failure reasons. Arbitrary
# exception messages can contain paths, prompts or credentials.
_NATIVE_REASONS = frozenset(
    """Pi session identity drift|Pi statistics session drift|RPC rejected|
agent end snapshot|agent start boundary|assistant stop reason|bash update shape|
closed stdin|compaction reason|content must be an array|content text|
contradictory response|deadline|delta index|delta text|duplicate request|
duplicate tool declaration|event after settlement|event before owned prompt|
event bound|event count|expected protocol boolean|expected protocol identifier|
expected protocol object|final assistant still declares tools|final text conflict|
incomplete process closure|incomplete settlement|invalid stderr UTF-8|
invalid stream|message end mismatch|message role|message start boundary|
message timestamp|message update boundary|model drift|multiple assistants in turn|
no successful final assistant|nonassistant message conflict|nonfresh session|
partial final line|premature EOF|queued prompts|readiness model drift|
readiness state|recovery boundary|response correlation|result bound|retry attempt|
retry error message|retry numeric field|second prompt|session name|stream bound|
summary source|summary boundary|summary sequence|system message|thinking drift|
tool end mismatch|tool result conflict|tool start mismatch|tool update mismatch|
turn end snapshot|turn start boundary|unknown assistant update|unknown content block|
unknown or forbidden event|unowned prompt|unterminated event bound|
usage snapshot conflict|retry boundary|retry sequence|retry outcome|
compaction boundary|compaction outcome|compaction result|cancelled|
incomplete recovery evidence""".replace("\n", "").split("|")
)
_UNCERTAINTIES = (
    "assistant error/abort",
    "intrinsic recovery",
    "invalid observer channel",
    "tool update",
    "unqualified queue event",
    "unqualified session event",
    "unqualified thinking event",
    "unqualified tool result",
    "unqualified update",
    "unsupported tool",
    "failed invocation",
    "incomplete closure",
    "cancelled",
    "invalid group observer",
    "process control observed",
    "group observer fault",
    "Pi nonzero exit",
    "signal or surviving group",
    "group identity discontinuity",
    "premature stream destroy",
    "shell spawn error",
    "incomplete group evidence",
)
_STREAM_CODES = frozenset(
    {
        "none",
        "partial_final_line",
        "stream_bound",
        "event_bound",
        "event_count",
        "unterminated_event_bound",
        "event_queue_full",
        "invalid_stream",
        "invalid_stderr_utf8",
    }
)


def native_failure_diagnostics(
    client: PiRpcClient, exc: Exception
) -> dict[str, object]:
    """Project native protocol reasons and numeric state, never exception text."""
    reason = "unclassified_invocation_failure"
    if isinstance(exc, PiRpcError) and str(exc) in _NATIVE_REASONS:
        reason = str(exc).lower().replace(" ", "_")
    elif isinstance(exc, OSError):
        reason = "native_os_error"
    diagnostic: dict[str, object] = {
        "failure_phase": "invocation",
        "failure_reason": reason,
        "exception_kind": (
            "pi_rpc_error"
            if isinstance(exc, PiRpcError)
            else "os_error"
            if isinstance(exc, OSError)
            else "type_error"
            if isinstance(exc, TypeError)
            else "value_error"
        ),
    }
    if isinstance(exc, OSError) and type(exc.errno) is int:
        diagnostic["os_errno"] = exc.errno
    frames = []
    frame = exc.__traceback__
    while frame is not None:
        name = Path(frame.tb_frame.f_code.co_filename).name
        if name in {"pi_rpc.py", "pi_rpc_client.py"}:
            frames.append(
                {
                    "module": name,
                    "function": frame.tb_frame.f_code.co_name,
                    "line": frame.tb_lineno,
                }
            )
        frame = frame.tb_next
    diagnostic["failure_frames"] = frames[-8:]
    lifecycle = client.lifecycle
    recovery = getattr(lifecycle, "recovery_evidence", None)
    if isinstance(recovery, PiRecoveryEvidence):
        summary = recovery.diagnostic()
        summary["qualified"] = lifecycle.recovery_qualified()
        diagnostic["recovery_qualification"] = summary
    event_history = getattr(lifecycle, "event_history", None)
    if isinstance(event_history, PiEventHistory):
        diagnostic["event_history"] = event_history.snapshot()
    diagnostic["lifecycle_uncertainty_count"] = len(lifecycle.uncertain)
    uncertainties = [
        value.replace(" ", "_").replace("/", "_")
        for value in _UNCERTAINTIES
        if value in lifecycle.uncertain
    ]
    diagnostic["lifecycle_uncertainties"] = uncertainties
    diagnostic["lifecycle_unclassified_uncertainty_count"] = len(
        lifecycle.uncertain
    ) - len(uncertainties)
    for field in ("run", "turn", "settled", "will_retry", "auxiliary"):
        value = getattr(lifecycle, field, None)
        if type(value) in {bool, int}:
            diagnostic["lifecycle_" + field] = value
    for stream in ("stdout", "stderr", "bridge"):
        for field in ("bytes", "events"):
            value = getattr(client, "stream_" + field, {}).get(stream, 0)
            if type(value) is int and 0 <= value <= 2**63 - 1:
                diagnostic[stream + "_" + field] = value
        code = getattr(client, "stream_error_codes", {}).get(stream, "none")
        diagnostic[stream + "_error_code"] = (
            code if code in _STREAM_CODES else "unclassified_stream_error"
        )
    diagnostic["stream_error_count"] = len(client.errors)
    diagnostic["pending_response_count"] = len(client.pending)
    diagnostic["native_stderr_bytes_retained"] = len(client.stderr)
    diagnostic["native_process_exited"] = client.process.poll() is not None
    return diagnostic
