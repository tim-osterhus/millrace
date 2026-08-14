"""Narrow runner-session CAS and governed-usage persistence helpers."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping

from millrace.adapters.cli.context import (
    OpenRuntimeContext,
    terminalize_daemon_budget_with_suspension,
)
from millrace.adapters.runner_contract import AdapterInvocationOutcome, RedactionPolicy
from millrace.contracts.runner import (
    RunnerResultEvidence,
    runner_result_evidence_from_payload,
)
from millrace.contracts.state import (
    DaemonBudgetEpochRecord,
    RunnerSessionRecord,
    RunnerSessionUsageRecord,
)

_COMMAND = "run.session"


def _load_evidence(
    runtime: OpenRuntimeContext,
    digest: str,
) -> RunnerResultEvidence:
    parsed = json.loads(runtime.cas_store.get_bytes(digest))
    if not isinstance(parsed, dict):
        raise ValueError("runner result evidence CAS object must be a mapping")
    return runner_result_evidence_from_payload(parsed)


def _persist_governed_runner_usage(
    runtime: OpenRuntimeContext,
    session: RunnerSessionRecord,
    outcome: AdapterInvocationOutcome | None,
) -> bool:
    budget_id: str | None = None
    try:
        budget_id = runtime.store.daemon_budget_id_for_session(session.session_id)
        if budget_id is None:
            return True
        epoch = runtime.store.load_daemon_budget_epoch(budget_id)
        if epoch is None:
            return False
        if epoch.max_total_tokens is None:
            return True
        usage = None if outcome is None else outcome.token_usage
        if usage is None:
            _refuse_governed_usage(runtime, epoch)
            return False
        runtime.store.record_runner_session_usage(
            RunnerSessionUsageRecord(
                budget_id=budget_id,
                session_id=session.session_id,
                run_id=session.run_id,
                dispatch_generation=session.dispatch_generation,
                session_fencing_token=session.session_fencing_token,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
                observed_at=max(epoch.last_observed_at, int(time.time())),
                final=True,
            )
        )
    except (TypeError, ValueError):
        epoch = (
            None
            if budget_id is None
            else runtime.store.load_daemon_budget_epoch(budget_id)
        )
        if epoch is not None and epoch.status == "active":
            _refuse_governed_usage(runtime, epoch)
        return False
    return True


def _refuse_governed_usage(
    runtime: OpenRuntimeContext,
    epoch: DaemonBudgetEpochRecord,
) -> None:
    terminalize_daemon_budget_with_suspension(
        runtime,
        budget_id=epoch.budget_id,
        observed_at=max(epoch.last_observed_at, int(time.time())),
        status="refused",
        reason="runner_usage_evidence_refused",
        command=_COMMAND,
    )


def _record_session_event(
    runtime: OpenRuntimeContext,
    *,
    session: RunnerSessionRecord,
    kind: str,
    observed_at: int,
    payload: Mapping[str, object],
    replay_key: str,
    redaction_policy: RedactionPolicy,
) -> None:
    """Best-effort projection after durable state; never session authority."""

    from millrace.substrate.runner_session_events import (
        RunnerSessionEventStore,
        RunnerSessionEventWriter,
        runner_session_event_store_path,
    )

    store = None
    try:
        store = RunnerSessionEventStore.initialize(
            runner_session_event_store_path(runtime.paths.db_path)
        )
        RunnerSessionEventWriter(
            store,
            session_id=session.session_id,
            run_id=session.run_id,
            dispatch_generation=session.dispatch_generation,
            redaction_policy=redaction_policy,
        ).record(
            kind,
            payload,
            observed_at=observed_at,
            replay_key=replay_key,
        )
    except Exception:
        return
    finally:
        if store is not None:
            store.close()
