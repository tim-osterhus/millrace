"""Outage classification and bounded NET_WAIT recovery helpers."""

from __future__ import annotations

import json
import socket
import subprocess
from collections.abc import Sequence
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Protocol

from pydantic import Field, field_validator

from ..config import EngineConfig
from ..contracts import ContractModel, ExecutionStatus, StageType
from ..provenance import read_transition_history
from .hooks import (
    PolicyDecision,
    PolicyEvaluationRecord,
    PolicyEvidence,
    PolicyEvidenceKind,
    PolicyFactSnapshot,
    PolicyRuntimeFacts,
)
from .preflight import ExecutionPreflightContext, execution_preflight_context
from .transport import TransportProbeResult, TransportReadiness


class OutagePolicyError(ValueError):
    """Raised when outage recovery lacks the facts it requires."""


class OutageRoute(str, Enum):
    """Supported escalation routes once probe budget is exhausted."""

    PAUSE_RESUME = "pause_resume"
    BLOCKER = "blocker"
    INCIDENT = "incident"


class OutageAction(str, Enum):
    """Runtime action selected for one outage probe attempt."""

    WAIT = "wait"
    RESUME = "resume"
    ROUTE_TO_BLOCKER = "route_to_blocker"
    ROUTE_TO_INCIDENT = "route_to_incident"


class OutagePolicySnapshot(ContractModel):
    """Config-derived outage policy snapshot frozen for one recovery loop."""

    enabled: bool
    wait_initial_seconds: int = Field(ge=0)
    wait_max_seconds: int = Field(ge=0)
    max_probes: int = Field(ge=0)
    probe_timeout_seconds: int = Field(ge=1)
    probe_host: str
    probe_port: int = Field(ge=1, le=65535)
    probe_command: tuple[str, ...] = ()
    policy: OutageRoute
    route_to_blocker: bool
    route_to_incident: bool

    @field_validator("probe_host")
    @classmethod
    def validate_probe_host(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("probe_host may not be empty")
        return normalized

    @field_validator("probe_command", mode="before")
    @classmethod
    def normalize_probe_command(cls, value: str | Sequence[str] | None) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            parts = tuple(part for part in value.strip().split() if part)
            return parts
        return tuple(str(part).strip() for part in value if str(part).strip())

    @classmethod
    def from_config(cls, config: EngineConfig) -> "OutagePolicySnapshot":
        outage = config.policies.outage
        probe_command = ()
        if outage.probe_command is not None:
            probe_command = tuple(part for part in outage.probe_command.strip().split() if part)
        return cls(
            enabled=outage.enabled,
            wait_initial_seconds=outage.wait_initial_seconds,
            wait_max_seconds=outage.wait_max_seconds,
            max_probes=outage.max_probes,
            probe_timeout_seconds=outage.probe_timeout_seconds,
            probe_host=outage.probe_host,
            probe_port=outage.probe_port,
            probe_command=probe_command,
            policy=OutageRoute(outage.policy),
            route_to_blocker=outage.route_to_blocker,
            route_to_incident=outage.route_to_incident,
        )

    def selected_route(self) -> OutageRoute:
        """Resolve the effective route, letting explicit booleans override the base policy."""

        if self.route_to_incident:
            return OutageRoute.INCIDENT
        if self.route_to_blocker:
            return OutageRoute.BLOCKER
        return self.policy


class OutageTrigger(ContractModel):
    """Typed NET_WAIT trigger reconstructed from frozen-plan policy evidence."""

    run_id: str
    stage: StageType
    node_id: str
    task_id: str | None = None
    task_title: str | None = None
    preflight: ExecutionPreflightContext
    evaluation: PolicyEvaluationRecord
    history_path: Path

    @field_validator("run_id", "node_id", "task_id", "task_title", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("text fields may not be empty")
        return normalized

    @field_validator("history_path", mode="before")
    @classmethod
    def normalize_history_path(cls, value: str | Path) -> Path:
        return Path(value)

    @classmethod
    def from_history(cls, history_path: Path) -> "OutageTrigger":
        records = read_transition_history(history_path)
        for record in reversed(records):
            evaluation = record.policy_evaluation_record()
            if evaluation is None:
                continue
            if evaluation.decision is not PolicyDecision.NET_WAIT:
                continue
            if evaluation.evaluator != "execution_preflight_policy":
                continue
            preflight = execution_preflight_context(evaluation)
            if preflight is None:
                raise OutagePolicyError("NET_WAIT policy record is missing persisted preflight context")
            if preflight.block_status is not ExecutionStatus.NET_WAIT:
                raise OutagePolicyError("NET_WAIT policy record does not carry NET_WAIT block status")
            stage = evaluation.facts.stage
            if stage is None:
                raise OutagePolicyError("NET_WAIT policy record is missing frozen-plan stage facts")
            task = evaluation.facts.task
            return cls(
                run_id=evaluation.facts.run_id,
                stage=stage.stage,
                node_id=stage.node_id,
                task_id=task.task_id if task is not None else None,
                task_title=task.title if task is not None else None,
                preflight=preflight,
                evaluation=evaluation,
                history_path=history_path,
            )
        raise OutagePolicyError("run history does not contain a persisted NET_WAIT preflight record")


class OutageProbeResult(ContractModel):
    """Result of one outage recovery probe attempt."""

    readiness: TransportReadiness
    summary: str
    details: dict[str, object] = Field(default_factory=dict)

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("summary may not be empty")
        return normalized


class OutageAttempt(ContractModel):
    """One bounded wait-loop probe attempt."""

    timestamp: datetime
    attempt: int = Field(ge=1)
    wait_seconds: int = Field(ge=0)
    probe: OutageProbeResult

    @field_validator("timestamp", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: datetime | str) -> datetime:
        if isinstance(value, datetime):
            moment = value
        else:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)


class OutageDecision(ContractModel):
    """Selected recovery action for one probe attempt."""

    action: OutageAction
    policy_decision: PolicyDecision
    reason: str
    route: OutageRoute
    next_wait_seconds: int | None = Field(default=None, ge=0)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("reason may not be empty")
        return normalized


class OutageProbe(Protocol):
    """Probe used by the daemon recovery loop to verify network recovery."""

    def check(self, policy: OutagePolicySnapshot) -> OutageProbeResult:
        """Run one outage recovery probe."""


class DefaultOutageProbe:
    """Socket-or-command recovery probe used by the daemon loop."""

    def check(self, policy: OutagePolicySnapshot) -> OutageProbeResult:
        if policy.probe_command:
            return self._check_command(policy)
        return self._check_socket(policy)

    def _check_command(self, policy: OutagePolicySnapshot) -> OutageProbeResult:
        try:
            completed = subprocess.run(
                policy.probe_command,
                capture_output=True,
                text=True,
                timeout=policy.probe_timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            return OutageProbeResult(
                readiness=TransportReadiness.ENV_BLOCKED,
                summary=f"outage probe command is unavailable: {exc.filename or policy.probe_command[0]}",
                details={"command": list(policy.probe_command)},
            )
        except subprocess.TimeoutExpired:
            return OutageProbeResult(
                readiness=TransportReadiness.NET_WAIT,
                summary=(
                    f"outage probe command timed out after {policy.probe_timeout_seconds}s: "
                    f"{' '.join(policy.probe_command)}"
                ),
                details={"command": list(policy.probe_command), "timeout_seconds": policy.probe_timeout_seconds},
            )
        if completed.returncode == 0:
            return OutageProbeResult(
                readiness=TransportReadiness.READY,
                summary=f"outage probe command succeeded: {' '.join(policy.probe_command)}",
                details={"command": list(policy.probe_command), "returncode": completed.returncode},
            )
        return OutageProbeResult(
            readiness=TransportReadiness.NET_WAIT,
            summary=f"outage probe command returned {completed.returncode}: {' '.join(policy.probe_command)}",
            details={"command": list(policy.probe_command), "returncode": completed.returncode},
        )

    def _check_socket(self, policy: OutagePolicySnapshot) -> OutageProbeResult:
        try:
            with socket.create_connection(
                (policy.probe_host, policy.probe_port),
                timeout=policy.probe_timeout_seconds,
            ):
                pass
        except socket.gaierror as exc:
            return OutageProbeResult(
                readiness=TransportReadiness.ENV_BLOCKED,
                summary=f"outage probe host could not be resolved: {policy.probe_host}",
                details={"host": policy.probe_host, "port": policy.probe_port, "error": str(exc)},
            )
        except OSError as exc:
            return OutageProbeResult(
                readiness=TransportReadiness.NET_WAIT,
                summary=f"outage probe could not reach {policy.probe_host}:{policy.probe_port}",
                details={"host": policy.probe_host, "port": policy.probe_port, "error": str(exc)},
            )
        return OutageProbeResult(
            readiness=TransportReadiness.READY,
            summary=f"outage probe reached {policy.probe_host}:{policy.probe_port}",
            details={"host": policy.probe_host, "port": policy.probe_port},
        )


class StaticOutageProbe:
    """Deterministic probe for tests."""

    def __init__(self, results: OutageProbeResult | Sequence[OutageProbeResult]) -> None:
        if isinstance(results, OutageProbeResult):
            self._results = [results]
        else:
            self._results = list(results)
        if not self._results:
            raise ValueError("StaticOutageProbe requires at least one result")
        self._index = 0

    def check(self, policy: OutagePolicySnapshot) -> OutageProbeResult:
        del policy
        if self._index >= len(self._results):
            return self._results[-1]
        result = self._results[self._index]
        self._index += 1
        return result


def evaluate_outage_attempt(
    policy: OutagePolicySnapshot,
    attempt: OutageAttempt,
) -> OutageDecision:
    """Select the bounded-loop action for one outage probe attempt."""

    route = policy.selected_route()
    if attempt.probe.readiness is TransportReadiness.READY:
        return OutageDecision(
            action=OutageAction.RESUME,
            policy_decision=PolicyDecision.PASS,
            reason=attempt.probe.summary,
            route=route,
        )
    if attempt.probe.readiness is TransportReadiness.ENV_BLOCKED:
        return OutageDecision(
            action=(
                OutageAction.ROUTE_TO_INCIDENT if route is OutageRoute.INCIDENT else OutageAction.ROUTE_TO_BLOCKER
            ),
            policy_decision=PolicyDecision.ENV_BLOCKED,
            reason=attempt.probe.summary,
            route=route,
        )
    if policy.max_probes > 0 and attempt.attempt >= policy.max_probes and route is not OutageRoute.PAUSE_RESUME:
        return OutageDecision(
            action=(
                OutageAction.ROUTE_TO_INCIDENT if route is OutageRoute.INCIDENT else OutageAction.ROUTE_TO_BLOCKER
            ),
            policy_decision=PolicyDecision.POLICY_BLOCKED,
            reason=(
                f"{attempt.probe.summary}; probe budget exhausted at attempt {attempt.attempt} "
                f"with route={route.value}"
            ),
            route=route,
        )
    return OutageDecision(
        action=OutageAction.WAIT,
        policy_decision=PolicyDecision.NET_WAIT,
        reason=attempt.probe.summary,
        route=route,
        next_wait_seconds=attempt.wait_seconds,
    )


def outage_policy_record(
    *,
    trigger: OutageTrigger,
    policy: OutagePolicySnapshot,
    attempt: OutageAttempt,
    decision: OutageDecision,
    transition_history_count: int,
    current_status: ExecutionStatus,
) -> PolicyEvaluationRecord:
    """Build one persisted outage policy record from frozen-plan and runtime facts."""

    facts = PolicyFactSnapshot.model_validate(
        trigger.evaluation.facts.model_dump(mode="python")
        | {
            "transition_history_count": transition_history_count,
            "runtime": PolicyRuntimeFacts(execution_status=current_status.value),
        }
    )
    evidence = (
        PolicyEvidence(
            kind=PolicyEvidenceKind.OUTAGE_POLICY,
            summary="Outage recovery evaluated the frozen NET_WAIT trigger against the outage policy snapshot.",
            details={
                "selected_route": decision.route.value,
                "policy": policy.policy.value,
                "route_to_blocker": policy.route_to_blocker,
                "route_to_incident": policy.route_to_incident,
                "wait_initial_seconds": policy.wait_initial_seconds,
                "wait_max_seconds": policy.wait_max_seconds,
                "max_probes": policy.max_probes,
                "probe_timeout_seconds": policy.probe_timeout_seconds,
                "probe_host": policy.probe_host,
                "probe_port": policy.probe_port,
                "probe_command": list(policy.probe_command),
                "trigger_reason": trigger.preflight.reason,
                "trigger_status": trigger.preflight.block_status.value if trigger.preflight.block_status is not None else None,
            },
        ),
        PolicyEvidence(
            kind=PolicyEvidenceKind.OUTAGE_PROBE,
            summary=decision.reason,
            details={
                "action": decision.action.value,
                "attempt": attempt.attempt,
                "wait_seconds": attempt.wait_seconds,
                "timestamp": attempt.timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                **attempt.probe.model_dump(mode="json"),
            },
        ),
    )
    notes = [decision.reason]
    if decision.action is OutageAction.WAIT and decision.next_wait_seconds is not None:
        notes.append(f"Retry scheduled after {decision.next_wait_seconds}s.")
    elif decision.action is OutageAction.RESUME:
        notes.append("Outage probe recovered; the engine can resume the frozen plan.")
    else:
        notes.append(f"Outage route selected: {decision.route.value}.")
    return PolicyEvaluationRecord(
        evaluator="execution_outage_policy",
        hook=facts.hook,
        decision=decision.policy_decision,
        facts=facts,
        evidence=evidence,
        notes=tuple(notes),
    )


def append_outage_attempt_log(
    diagnostics_dir: Path,
    *,
    trigger: OutageTrigger,
    policy: OutagePolicySnapshot,
    attempt: OutageAttempt,
    decision: OutageDecision,
) -> None:
    """Persist one operator-visible probe attempt into the diagnostics bundle."""

    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    attempt_path = diagnostics_dir / "outage_probe_attempts.jsonl"
    payload = {
        "run_id": trigger.run_id,
        "stage": trigger.stage.value,
        "node_id": trigger.node_id,
        "task_id": trigger.task_id,
        "task_title": trigger.task_title,
        "selected_route": decision.route.value,
        "policy": policy.policy.value,
        "route_to_blocker": policy.route_to_blocker,
        "route_to_incident": policy.route_to_incident,
        "attempt": attempt.attempt,
        "wait_seconds": attempt.wait_seconds,
        "timestamp": attempt.timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "decision": decision.policy_decision.value,
        "action": decision.action.value,
        "reason": decision.reason,
        "probe": attempt.probe.model_dump(mode="json"),
    }
    with attempt_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


__all__ = [
    "DefaultOutageProbe",
    "OutageAction",
    "OutageAttempt",
    "OutageDecision",
    "OutagePolicyError",
    "OutagePolicySnapshot",
    "OutageProbe",
    "OutageProbeResult",
    "OutageRoute",
    "OutageTrigger",
    "StaticOutageProbe",
    "append_outage_attempt_log",
    "evaluate_outage_attempt",
    "outage_policy_record",
]
