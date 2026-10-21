"""Opt-in component-free Pi adapter. Output is candidate evidence only."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from typing import Any, cast

from millrace.adapters.pi_rpc_client import (
    PiRpcClient,
    PiRpcError,
    PiRpcNotStarted,
    parse_candidate,
)
from millrace.adapters.pi_rpc_config import PiAttempt, PiRpcConfig
from millrace.adapters.pi_rpc_diagnostics import native_failure_diagnostics
from millrace.adapters.runner_contract import (
    REVIEWED_TOKEN_USAGE_MAPPING,
    AdapterErrorResult,
    AdapterInvocationOutcome,
    AdapterInvocationRequest,
    AdapterSuccessResult,
    DispatchEcho,
    RedactionPolicy,
    RunnerCancellationOperationResult,
    RunnerCleanupResult,
    RunnerSessionReconcileRequest,
    RunnerSessionStartOutcome,
    StartedSession,
    StartIndeterminate,
    StartRefusedBeforeExternalWork,
    Unsupported,
    canonicalize_redaction_policy,
    runner_cancellation_diagnostic_digest,
    start_refusal_diagnostic_digest,
)
from millrace.contracts.compiled_plan import AuthorityValue
from millrace.contracts.runner_payload_capacity import validate_payload_capacity_pin
from millrace.contracts.transition import canonical_authority_mapping_bytes

PI_ADAPTER_KIND = "pi_rpc"
_FAILURE_CANDIDATE_MAX_BYTES = 4096
_CANDIDATE_FAILURE_REASONS = {
    "duplicate JSON key": "duplicate_json_key",
    "nonfinite JSON number": "nonfinite_json_number",
    "JSON nesting bound": "json_nesting_bound",
    "candidate envelope": "invalid_envelope",
    "candidate marker": "invalid_marker",
    "candidate payload": "invalid_payload",
    "runner_report must be text": "invalid_runner_report",
}


def _failure_reason(phase: str, exc: Exception) -> str:
    """Project only known codes; exception-local contents are never retained."""
    if isinstance(exc, PiRpcError) and exc.kind == "output_too_large":
        return "result_byte_limit"
    if phase == "evidence_normalization":
        return (
            "redacted_mapping_key_collision"
            if type(exc) is ValueError and str(exc) == "redacted mapping keys collide"
            else "evidence_normalization_failed"
        )
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_json"
    if isinstance(exc, UnicodeError):
        return "invalid_text_encoding"
    return (
        _CANDIDATE_FAILURE_REASONS.get(str(exc), "candidate_parse_failed")
        if type(exc) is ValueError
        else "candidate_parse_failed"
    )


def _redacted_candidate_text(text: str, policy: RedactionPolicy) -> str:
    """Decode string literals so JSON escapes cannot hide configured secrets.

    Retain text, including duplicate keys and invalid outer structure, rather
    than normalizing it into a candidate mapping. Ambiguous string encodings
    are omitted by the caller.
    """
    if any(secret in "[REDACTED]" for secret in policy.secret_tokens):
        # Downstream diagnostic codecs reapply redaction. An unstable mask can
        # expand past the bound or itself contain a configured secret.
        raise ValueError("unstable candidate redaction mask")
    text = policy.redact_text(text)
    parts: list[str] = []
    decoder = json.JSONDecoder()
    cursor = 0
    while cursor < len(text):
        quote = text.find('"', cursor)
        end = len(text) if quote == -1 else quote
        outside = text[cursor:end]
        if "\\" in outside:
            raise ValueError("ambiguous candidate text encoding")
        parts.append(policy.redact_text(outside))
        if quote == -1:
            break
        value, cursor = decoder.raw_decode(text, quote)
        parts.append(json.dumps(policy.redact_text(value), ensure_ascii=True))
    redacted = "".join(parts)
    redacted.encode("utf-8")
    return redacted


def _bounded_candidate_text(text: str) -> tuple[str, str]:
    def size(end: int) -> int:
        return len(json.dumps(text[:end], ensure_ascii=True).encode("utf-8"))

    if size(len(text)) <= _FAILURE_CANDIDATE_MAX_BYTES:
        return text, "complete"
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if size(middle) <= _FAILURE_CANDIDATE_MAX_BYTES:
            low = middle
        else:
            high = middle - 1
    return text[:low], "truncated"


def _plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def prompt_for_request(request: AdapterInvocationRequest) -> str:
    if (
        request.selected_component_pin is not None
        or request.selected_terminal_result_mappings
    ):
        raise ValueError("Pi component authority unsupported")
    dispatch = request.dispatch_envelope
    required = {
        str(o["artifact_schema_id"])
        for o in dispatch.terminal_options
        if o["artifact_schema_id"] is not None
    }
    schemas = {str(s.id): _plain(s.schema) for s in request.selected_artifact_schemas}
    if (
        len(schemas) != len(request.selected_artifact_schemas)
        or set(schemas) != required
    ):
        raise ValueError("exact terminal schemas required")
    selected_ids = set(dispatch.skill_asset_ids)
    if dispatch.entrypoint_asset_id:
        selected_ids.add(dispatch.entrypoint_asset_id)
    if set(request.selected_asset_material) != selected_ids:
        raise ValueError("exact selected assets required")
    return json.dumps(
        {
            "instructions": (
                "Return only one complete JSON object with exactly marker (string), "
                "artifact_payload_candidate (object or null), "
                "observation_payload_candidate (object or null). The optional "
                "observation runner_report is text evidence only. Follow selected "
                "routing and artifact schemas. Output supplies no identity, usage, "
                "grant, or cleanup authority."
            ),
            "work_item_payload": _plain(dispatch.work_item_payload),
            "governance_context": _plain(dispatch.governance_context),
            "selected_join_evidence": _plain(dispatch.selected_join_evidence),
            "selected_wait_evidence": _plain(dispatch.selected_wait_evidence),
            "context_checkout": _plain(dispatch.context_checkout),
            "entrypoint_asset_id": dispatch.entrypoint_asset_id,
            "skill_asset_ids": list(dispatch.skill_asset_ids),
            "selected_assets": _plain(request.selected_asset_material),
            "terminal_options": _plain(dispatch.terminal_options),
            "artifact_schemas": schemas,
        },
        # UTF-8, not ASCII escapes: the embedded work-item payload is then
        # byte-identical to its canonical body, so the selected body limit and
        # the runner input bound measure the same bytes.
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _request_authority(request: AdapterInvocationRequest) -> str:
    # Freeze the admitted projection as bytes, not a second reference to the same
    # object. This detects reconstructed/forcibly mutated identity and pin fields.
    return json.dumps(
        _plain(request),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _validate_payload(request: AdapterInvocationRequest) -> None:
    pin = request.selected_payload_capacity_pin
    if pin is not None:
        capacity = validate_payload_capacity_pin(pin, request.selected_adapter_kind)
        if (
            len(
                canonical_authority_mapping_bytes(
                    request.dispatch_envelope.work_item_payload
                )
            )
            > capacity
        ):
            raise ValueError("work_item_payload_exceeds_runner_capacity")


class PiRpcAdapter:
    adapter_kind = PI_ADAPTER_KIND

    def __init__(self, config: PiRpcConfig) -> None:
        self.config = config
        if (
            config.profile.get("qualification_scope")
            == "loopback-scripted-openai-completions"
        ):
            self.token_usage_mapping_capability = REVIEWED_TOKEN_USAGE_MAPPING
        self._admitted: dict[int, tuple[AdapterInvocationRequest, str]] = {}
        self._pending_cleanup: tuple[PiRpcClient, PiAttempt] | None = None

    def admit_request(self, request: AdapterInvocationRequest) -> None:
        """Called only by trusted selected-plan construction after grant checks.

        The invocation record intentionally has no capability declarations. A
        directly constructed request cannot silently skip the trusted preclaim.
        """
        _validate_payload(request)
        self._admitted[id(request)] = (request, _request_authority(request))

    def cleanup_retained_attempt(self) -> bool:
        """Retry only this adapter's retained failed-start resources, without work."""
        if self._pending_cleanup is None:
            return True
        client, attempt = self._pending_cleanup
        try:
            if (
                not client.dispose()
                or not client.closed
                or client.process.returncode is None
            ):
                return False
            attempt.cleanup()
        except (OSError, ValueError):
            return False
        self._pending_cleanup = None
        return True

    def error(
        self,
        request: AdapterInvocationRequest,
        kind: str,
        *,
        usage: Any = None,
        diagnostics: Mapping[str, object] | None = None,
    ) -> AdapterErrorResult:
        values: Mapping[str, object] = {
            "protocol": "pi-0.87.0-jsonl.v1",
            **(diagnostics or {}),
        }

        def build(values: Mapping[str, object]) -> AdapterErrorResult:
            return AdapterErrorResult.from_unredacted(
                adapter_id=self.config.adapter_id,
                error_kind=kind,
                redaction_policy=request.redaction_policy,
                dispatch_echo=DispatchEcho.from_dispatch_envelope(
                    request.dispatch_envelope,
                    correlation_id=request.correlation_id,
                    selected_adapter_kind=request.selected_adapter_kind,
                ),
                token_usage=usage,
                diagnostics=values,
            )

        try:
            return build(values)
        except ValueError:
            if diagnostics is None:
                raise
            # A policy can redact different fixed field names to the same key.
            # Keep content-free codes in one list; do not strand the session or
            # retain candidate text when diagnostic redaction cannot be built.
            return build(
                {
                    "result_failure": (
                        diagnostics["failure_phase"],
                        diagnostics["failure_reason"],
                        "omitted_redaction_failed",
                    )
                }
            )

    def start_session(
        self, request: AdapterInvocationRequest
    ) -> RunnerSessionStartOutcome:
        deadline = time.monotonic() + min(
            float(self.config.data.get("timeout_seconds", request.timeout_seconds)),
            request.timeout_seconds,
        )
        error_kind = "selected_authority_refused"
        attempt: PiAttempt | None = None
        client: PiRpcClient | None = None
        try:
            if request.selected_adapter_kind != PI_ADAPTER_KIND:
                error_kind = "unsupported_adapter_kind"
                raise ValueError("adapter kind")
            admitted = self._admitted.pop(id(request), None)
            if (
                admitted is None
                or admitted[0] is not request
                or admitted[1] != _request_authority(request)
            ):
                raise ValueError("missing or changed trusted preclaim")
            _validate_payload(request)
            if not self.cleanup_retained_attempt():
                raise ValueError("previous startup disposal unresolved")
            prompt = prompt_for_request(request)
            error_kind = "missing_opt_in_config"
            if request.adapter_id != self.config.adapter_id:
                raise ValueError("adapter id")
            error_kind = "redaction_refused"
            policy = self.config.redaction_policy
            if (
                policy != request.redaction_policy
                or policy.redact_text(prompt) != prompt
            ):
                raise ValueError("redaction mismatch")
            error_kind = "input_too_large"
            if len(prompt.encode()) > self.config.data["max_input_bundle_bytes"]:
                raise ValueError("prompt bound")
            error_kind = "missing_opt_in_config"
            if time.monotonic() >= deadline:
                raise ValueError("startup deadline")
            attempt = self.config.materialize()
            if time.monotonic() >= deadline:
                raise ValueError("startup deadline")
            self.config.prespawn(attempt)
            if time.monotonic() >= deadline:
                raise ValueError("startup deadline")
            self.config.probe_versions(attempt, deadline)
            if time.monotonic() >= deadline:
                raise ValueError("startup deadline")
            self.config.prespawn(attempt)
            _validate_payload(request)
            if self.config.redaction_policy != request.redaction_policy:
                raise ValueError("credential drift")
            if time.monotonic() >= deadline:
                raise ValueError("startup deadline")
        except (OSError, ValueError, TypeError, KeyError):
            if attempt is not None:
                try:
                    attempt.cleanup()
                except (OSError, ValueError):
                    outcome = self.error(request, "invocation_failed")
                    return StartIndeterminate(
                        cast(DispatchEcho, outcome.dispatch_echo),
                        None,
                        start_refusal_diagnostic_digest(outcome),
                    )
            outcome = self.error(request, error_kind)
            return StartRefusedBeforeExternalWork(
                cast(DispatchEcho, outcome.dispatch_echo),
                outcome,
                start_refusal_diagnostic_digest(outcome),
            )
        try:
            client = PiRpcClient(
                tuple(
                    [
                        self.config.data["executable"],
                        *self.config.data["argv"],
                        *self.config.managed_args,
                    ]
                ),
                self.config.cwd,
                attempt.env,
                self.config.data,
                self.config.profile["model"],
                prompt,
                deadline - time.monotonic(),
                deadline=deadline,
                **(
                    {"group_profile": self.config.group_profile}
                    if self.config.group_profile is not None
                    else {}
                ),
            )
            client.ready()
        except (OSError, ValueError, TypeError) as exc:
            no_spawn = isinstance(exc, PiRpcNotStarted)
            closed = no_spawn
            if client is not None:
                try:
                    closed = (
                        client.dispose()
                        and client.closed
                        and client.process.returncode is not None
                    )
                except (OSError, ValueError):
                    pass
            if closed:
                try:
                    attempt.cleanup()
                except (OSError, ValueError):
                    closed = False
            if not closed and client is not None:
                self._pending_cleanup = (client, attempt)
            outcome = self.error(request, "invocation_failed")
            if closed and (no_spawn or (client is not None and not client.sent_prompt)):
                return StartRefusedBeforeExternalWork(
                    cast(DispatchEcho, outcome.dispatch_echo),
                    outcome,
                    start_refusal_diagnostic_digest(outcome),
                )
            return StartIndeterminate(
                cast(DispatchEcho, outcome.dispatch_echo),
                None,
                start_refusal_diagnostic_digest(outcome),
            )
        echo = DispatchEcho.from_dispatch_envelope(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
            selected_adapter_kind=request.selected_adapter_kind,
        )
        return StartedSession(
            echo,
            PiSession(self, request, client, attempt),
            f"pi_rpc:{request.session_id}:{request.dispatch_generation}",
            {"protocol": "pi-0.87.0-jsonl.v1"},
        )

    def reconcile_session(self, request: RunnerSessionReconcileRequest) -> Unsupported:
        r = request.invocation_request
        return Unsupported(
            DispatchEcho.from_dispatch_envelope(
                r.dispatch_envelope,
                correlation_id=r.correlation_id,
                selected_adapter_kind=r.selected_adapter_kind,
            )
        )

    def invoke(self, request: AdapterInvocationRequest) -> AdapterInvocationOutcome:
        start = self.start_session(request)
        if isinstance(start, StartRefusedBeforeExternalWork):
            return start.adapter_error
        if isinstance(start, StartIndeterminate):
            return self.error(request, "invocation_failed")
        while (outcome := start.handle.poll_completion()) is None:
            time.sleep(0.01)
        start.handle.cleanup()
        return outcome


class PiSession:
    def __init__(
        self,
        adapter: PiRpcAdapter,
        request: AdapterInvocationRequest,
        client: PiRpcClient,
        attempt: PiAttempt,
    ) -> None:
        self.adapter, self.request, self.client, self.attempt = (
            adapter,
            request,
            client,
            attempt,
        )
        self.outcome: AdapterInvocationOutcome | None = None
        self.cleanup_result: RunnerCleanupResult | None = None
        self.cancelled = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        kind = "invocation_failed"
        phase = "invocation"
        text: str | None = None
        try:
            text = self.client.run()
            kind = "result_parse_failed"
            phase = "candidate_parse"
            candidate = parse_candidate(
                text, self.adapter.config.data["max_result_bytes"]
            )
            if self.cancelled.is_set():
                raise PiRpcError("cancelled", "cancelled")
            phase = "evidence_normalization"
            self.outcome = AdapterSuccessResult.from_unredacted(
                adapter_id=self.adapter.config.adapter_id,
                dispatch_echo=DispatchEcho.from_dispatch_envelope(
                    self.request.dispatch_envelope,
                    correlation_id=self.request.correlation_id,
                    selected_adapter_kind=self.request.selected_adapter_kind,
                ),
                redaction_policy=self.request.redaction_policy,
                marker=candidate["marker"],
                artifact_payload_candidate=candidate["artifact_payload_candidate"],
                observation_payload_candidate=candidate[
                    "observation_payload_candidate"
                ],
                captured_stderr=bytes(self.client.stderr).decode(
                    "utf-8", errors="replace"
                ),
                token_usage=self.client.usage,
            )
        except (ValueError, TypeError, OSError, KeyError) as exc:
            self.client.lifecycle.uncertain.add("failed invocation")
            if isinstance(exc, PiRpcError):
                kind = exc.kind
            self.outcome = self.adapter.error(
                self.request,
                kind,
                usage=self.client.usage or self.client.lifecycle.fallback_usage(),
                diagnostics=(
                    self._failure_diagnostics(text, phase, exc)
                    if text is not None
                    and kind in {"result_parse_failed", "output_too_large"}
                    else native_failure_diagnostics(self.client, exc)
                    if phase == "invocation"
                    else None
                ),
            )

    def _failure_diagnostics(
        self,
        text: str,
        phase: str,
        exc: Exception,
    ) -> Mapping[str, object]:
        diagnostic: dict[str, object] = {
            "failure_phase": phase,
            "failure_reason": _failure_reason(phase, exc),
        }
        if isinstance(exc, PiRpcError) and exc.kind == "output_too_large":
            diagnostic["candidate_evidence_status"] = "omitted_result_bound"
            return diagnostic
        try:
            request_policy = canonicalize_redaction_policy(
                self.request.redaction_policy
            )
            config_policy = canonicalize_redaction_policy(
                self.adapter.config.redaction_policy
            )
            credential = self.attempt.env.get("PI_RPC_API_KEY", "")
            policy = RedactionPolicy(
                request_policy.policy_id,
                tuple(
                    set(
                        request_policy.secret_tokens
                        + config_policy.secret_tokens
                        + ((credential,) if credential else ())
                    )
                ),
            )
            redacted = _redacted_candidate_text(text, policy)
            excerpt, status = _bounded_candidate_text(redacted)
        except (TypeError, ValueError):
            diagnostic["candidate_evidence_status"] = "omitted_unsafe_text"
            return diagnostic
        diagnostic["candidate_text"] = excerpt
        diagnostic["candidate_evidence_status"] = status
        return diagnostic

    def poll_completion(self) -> AdapterInvocationOutcome | None:
        return self.outcome

    def _operation(self, operation: str) -> RunnerCancellationOperationResult:
        start = time.time_ns() // 1_000_000
        result = "succeeded"
        self.cancelled.set()
        self.client.lifecycle.uncertain.add("cancelled")
        try:
            if operation == "cooperative_cancel":
                # One writer owns JSONL. Cancel wakes it; it sends abort while
                # consuming the prompt lifecycle, preserving response correlation.
                self.client.cancel_requested.set()
                if not self.client.abort_ack.wait(0.2):
                    result = "timed_out"
            elif self.client.process.poll() is None:
                self.client.signal_pi(operation)
        except (OSError, ValueError):
            result = "failed"
        diagnostic = {"scope": "owned_parent_only"}
        return RunnerCancellationOperationResult(
            operation,
            result,
            start,
            time.time_ns() // 1_000_000,
            diagnostic,
            runner_cancellation_diagnostic_digest(diagnostic),
        )

    def request_cancel(self) -> RunnerCancellationOperationResult:
        return self._operation("cooperative_cancel")

    def terminate(self) -> RunnerCancellationOperationResult:
        return self._operation("terminate")

    def kill(self) -> RunnerCancellationOperationResult:
        return self._operation("kill")

    def cleanup(self) -> RunnerCleanupResult:
        if self.cleanup_result is not None:
            return self.cleanup_result
        start = time.time_ns() // 1_000_000
        self.thread.join(0.2)
        complete = (
            self.client.closed
            and self.client.process.returncode == 0
            and all(self.client.eof.values())
            and not self.client.errors
            and not self.client.pending
        )
        groups = getattr(self.client, "groups", None)
        supervisor = getattr(self.client, "supervisor", None)
        disposed = (
            self.client.closed
            and self.client.process.returncode is not None
            and (
                supervisor.result is True
                if supervisor is not None
                else groups is None or groups.disposed()
            )
        )
        if self.thread.is_alive() or not disposed:
            self.client.lifecycle.uncertain.add("incomplete closure")
            try:
                disposed = (
                    self.client.dispose()
                    and self.client.closed
                    and self.client.process.returncode is not None
                )
            except (OSError, ValueError):
                disposed = False
            self.thread.join(0.5)
        try:
            if self.thread.is_alive() or not disposed:
                raise ValueError("owned process disposal unresolved")
            self.attempt.cleanup()
        except (OSError, ValueError):
            complete = False
        if groups is not None or supervisor is not None:
            complete = (
                disposed
                and self.client.closed
                and self.client.process.returncode is not None
            )
            if supervisor is None:
                complete = (
                    complete
                    and all(self.client.eof.values())
                    and not self.client.errors
                )
        complete = (
            complete
            and self.attempt.removed
            and not self.thread.is_alive()
            and (
                groups is not None
                or supervisor is not None
                or not self.client.lifecycle.uncertain
            )
            and (
                groups is not None
                or supervisor is not None
                or self.client.lifecycle.settled == 1
            )
        )
        diagnostic: dict[str, AuthorityValue] = {
            "scope": "observed_text_tools_and_owned_resources",
            "uncertainty_count": len(self.client.lifecycle.uncertain),
            "root_exited": self.client.process.returncode is not None,
            "root_exit_zero": self.client.process.returncode == 0,
            "client_closed": self.client.closed,
            "stdout_eof": self.client.eof.get("stdout", False),
            "stderr_eof": self.client.eof.get("stderr", False),
            "bridge_eof": self.client.eof.get("bridge", False),
            "reader_threads_joined": not any(
                thread.is_alive() for thread in getattr(self.client, "threads", ())
            ),
            "session_thread_joined": not self.thread.is_alive(),
            "stream_error_count": len(self.client.errors),
            "pending_response_count": len(self.client.pending),
            "attempt_removed": self.attempt.removed,
            "disposed": disposed,
        }
        for name in ("stdout", "stderr", "bridge"):
            diagnostic[f"{name}_bytes"] = getattr(self.client, "stream_bytes", {}).get(
                name, 0
            )
            diagnostic[f"{name}_events"] = getattr(
                self.client, "stream_events", {}
            ).get(name, 0)
            diagnostic[f"{name}_error_code"] = getattr(
                self.client, "stream_error_codes", {}
            ).get(name, "none")
        if groups is not None:
            diagnostic.update(groups.diagnostic())
        if supervisor is not None:
            report = supervisor.report
            diagnostic["supervisor_reported"] = report is not None
            diagnostic["supervisor_disposed"] = supervisor.result is True
            diagnostic["supervisor_helper_exit_zero"] = (
                self.client.process.returncode == 0
            )
            if self.client.process.returncode is not None:
                diagnostic["supervisor_helper_returncode"] = (
                    self.client.process.returncode
                )
            if report is not None:
                diagnostic["supervisor_pi_returncode"] = report["pi_returncode"]
                diagnostic["supervisor_pi_exit_zero"] = (
                    report["pi_returncode"] == 0
                )
                for key in (
                    "reaped_children",
                    "term_count",
                    "kill_count",
                    "remaining_children",
                ):
                    diagnostic[f"supervisor_{key}"] = report[key]
        result = RunnerCleanupResult(
            "complete" if complete else "orphan_risk",
            start,
            time.time_ns() // 1_000_000,
            diagnostic,
            runner_cancellation_diagnostic_digest(diagnostic),
        )
        if disposed and not self.thread.is_alive() and self.attempt.removed:
            self.cleanup_result = result
        return result
