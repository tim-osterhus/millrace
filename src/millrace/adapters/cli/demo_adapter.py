"""Private deterministic adapter. Core owns permits and durable result storage."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from millrace.adapters.runner_contract import (
    AdapterInvocationRequest,
    AdapterSuccessResult,
    DispatchEcho,
    RunnerCancellationOperationResult,
    RunnerCleanupResult,
    RunnerSessionReconcileRequest,
    StartedSession,
    Terminal,
    runner_cancellation_diagnostic_digest,
)
from millrace.contracts.demo import ADAPTER_KIND, DemoRefusal, serial


def synthetic_result(request: AdapterInvocationRequest) -> tuple[str, dict[str, Any]]:
    stage = request.dispatch_envelope.stage_kind_id
    payload = {
        "synthetic": True,
        "bundle_id": "synthetic-bundle",
        "summary": "Synthetic demo only",
    }
    if stage == "intake":
        return "FANOUT", {**payload, "items": [{"item_id": "one"}]}
    if stage == "producer_a":
        return "ARTIFACT_A", {**payload, "summary": "Synthetic artifact A: complete"}
    if stage == "producer_b":
        return "ARTIFACT_B", {
            **payload,
            "summary": "Synthetic artifact B: checksum missing",
        }
    incoming = request.dispatch_envelope.work_item_payload
    if stage == "verify_first":
        evidence: Any = request.dispatch_envelope.selected_join_evidence
        values = (
            {
                x["artifact_schema_id"]: x["payload"]["summary"]
                for x in evidence["evidence_artifacts"]
            }
            if evidence
            else {}
        )
        if values != {
            "branch_a": "Synthetic artifact A: complete",
            "branch_b": "Synthetic artifact B: checksum missing",
        }:
            raise DemoRefusal("join_evidence_mismatch")
        return "EXPECTED_FAIL", {
            **payload,
            "check_state": "defective",
            "summary": "Synthetic verifier: artifact B checksum missing",
        }
    if stage == "repair":
        if incoming["check_state"] != "defective":
            raise DemoRefusal("repair_input_mismatch")
        return "CORRECTED", {
            **payload,
            "check_state": "corrected",
            "summary": "Synthetic artifact B: checksum supplied",
        }
    if stage == "verify_pass":
        if (
            incoming["check_state"] != "corrected"
            or incoming["summary"] != "Synthetic artifact B: checksum supplied"
        ):
            raise DemoRefusal("verification_input_mismatch")
        return "PASS", {
            **payload,
            "check_state": "passed",
            "summary": "Synthetic verifier: corrected artifact passed",
        }
    if stage == "wait":
        if incoming["check_state"] != "passed":
            raise DemoRefusal("wait_input_mismatch")
        return "DECIDE", {
            "changes": [],
            "proposals": [],
            "no_op_reason": "Synthetic: joined artifacts repaired and verified.",
        }
    if stage == "revise":
        return "REVISED", {
            **payload,
            "check_state": "revised",
            "summary": "Synthetic: one requested revision completed",
        }
    if stage == "close":
        return "CLOSED", {
            "changes": [],
            "proposals": [],
            "no_op_reason": "Synthetic demo closed; no context changes.",
        }
    raise DemoRefusal("undeclared_synthetic_stage")


class _Handle:
    def __init__(
        self,
        result: AdapterSuccessResult,
        validate: Callable[[], None],
        before_poll: Callable[[], bool],
    ) -> None:
        self.result = result
        self.validate = validate
        self.returned = False
        self.before_poll = before_poll

    def poll_completion(self) -> AdapterSuccessResult | None:
        self.validate()
        if not self.before_poll():
            return None
        if self.returned:
            return None
        self.returned = True
        return self.result

    def _operation(self, operation: str) -> RunnerCancellationOperationResult:
        self.validate()
        diagnostic = {"operation": operation, "result": "succeeded", "source": "demo"}
        return RunnerCancellationOperationResult(
            operation,
            "succeeded",
            1,
            1,
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
        self.validate()
        diagnostic = {"operation": "cleanup", "result": "complete", "source": "demo"}
        return RunnerCleanupResult(
            "complete",
            1,
            1,
            diagnostic,
            runner_cancellation_diagnostic_digest(diagnostic),
        )


class _DemoAdapter:
    adapter_kind = ADAPTER_KIND

    def __init__(
        self,
        validate: Callable[[AdapterInvocationRequest], None],
        retain: Callable[[str, dict[str, Any]], None],
        read: Callable[[str], dict[str, Any] | None],
        before_poll: Callable[[AdapterInvocationRequest], bool],
    ) -> None:
        self.validate = validate
        self.retain = retain
        self.read = read
        self.before_poll = before_poll

    @staticmethod
    def _echo(request: AdapterInvocationRequest) -> DispatchEcho:
        return DispatchEcho.from_dispatch_envelope(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
            selected_adapter_kind=ADAPTER_KIND,
        )

    def start_session(self, request: AdapterInvocationRequest) -> StartedSession:
        self.validate(request)
        if self.read(request.session_id) is not None:
            raise DemoRefusal("duplicate_synthetic_start")
        marker, payload = synthetic_result(request)
        result = AdapterSuccessResult.from_unredacted(
            adapter_id=request.adapter_id,
            dispatch_echo=self._echo(request),
            redaction_policy=request.redaction_policy,
            marker=marker,
            artifact_payload_candidate=payload,
            observation_payload_candidate={"synthetic": True, "demo_only": True},
        )
        # Write-through owner storage precedes reporting recoverable completion.
        self.retain(
            request.session_id, {"request": serial(request), "result": serial(result)}
        )
        return StartedSession(
            self._echo(request),
            _Handle(
                result,
                lambda: self.validate(request),
                lambda: self.before_poll(request),
            ),
            "demo:" + request.session_id,
            {"provider_request_id": "demo:" + request.session_id},
        )

    def reconcile_session(self, request: RunnerSessionReconcileRequest) -> Terminal:
        invocation = request.invocation_request
        self.validate(invocation)
        retained = self.read(invocation.session_id)
        if retained is None:
            raise DemoRefusal("retained_session_evidence_missing")
        if retained["request"] != serial(invocation):
            raise DemoRefusal("retained_session_authority_mismatch")
        values = dict(retained["result"])
        values["dispatch_echo"] = self._echo(invocation)
        return Terminal(
            self._echo(invocation), AdapterSuccessResult(**values), "complete"
        )
