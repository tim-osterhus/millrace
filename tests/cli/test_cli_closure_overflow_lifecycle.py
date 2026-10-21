"""Closure-request overflow lifecycle through units, ticks, the daemon, and CLI.

The fake closure evaluator is a codex-kind adapter. It never trusts the inline
work item: it reads the dispatch projection, re-reads every materialized page
from disk, verifies digests and byte counts, reassembles the snapshot, and only
then derives its verdict. Helpers that the sibling kernel and CLI suites define
are copied here rather than imported.
"""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from collections.abc import Mapping
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from millrace.adapters.cli.context import CliWorkspacePaths, OpenRuntimeContext
from millrace.adapters.cli.daemon import DaemonRunOptions, run_daemon_loop
from millrace.adapters.cli.lifecycle import run_lifecycle_transition_once
from millrace.adapters.cli.main import main
from millrace.adapters.cli.run import run_bounded_execution_unit
from millrace.adapters.runner_contract import (
    AdapterLocalConfig,
    AdapterSuccessResult,
    DispatchEcho,
    RunnerCancellationOperationResult,
    RunnerCleanupResult,
    StartedSession,
    Unsupported,
    runner_cancellation_diagnostic_digest,
)
from millrace.contracts.closure_request_overflow import (
    OVERFLOW_KEY,
    SNAPSHOT_KEY,
    closure_capacity_blocked,
    closure_capacity_blocks,
)
from millrace.contracts.runtime_failures import runtime_failure_applied
from millrace.contracts.state import RuntimeState
from millrace.kernel import empty_runtime_state
from millrace.operator.dispatch import list_ready_dispatch_candidates
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.sqlite import SQLiteRuntimeStore
from millrace.testing import materialize_fake_runner_session_cas
from support import closure_overflow as co
from support import generic_admission
from support import generic_lifecycle as gl

REVIEW_STAGE = "review_stage"
ALPHA_STAGE = "alpha_stage"
EVIDENCE_ROOT = co.MATERIALIZATION_ROOT
UNKNOWN_MARKER = "UNKNOWN_REVIEW_MARKER"


# ---------------------------------------------------------------------------
# Workspace and state helpers (copied from the sibling CLI suites).
# ---------------------------------------------------------------------------


def _single(values: Any) -> Any:
    items = list(values)
    assert len(items) == 1, len(items)
    return items[0]


def _values(collection: Any) -> list[Any]:
    if isinstance(collection, Mapping):
        return list(collection.values())
    return list(collection)


def _plain(value: Any) -> Any:
    """Recursively convert mappings and sequences to plain JSON-able values."""
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _runtime(tmp_path: Path, state: RuntimeState | None = None) -> OpenRuntimeContext:
    root = tmp_path / "workspace"
    db_path = root / ".millrace" / "runtime.sqlite3"
    cas_path = root / ".millrace" / "cas"
    db_path.parent.mkdir(parents=True)
    cas_path.mkdir(parents=True)
    store = SQLiteRuntimeStore.initialize(db_path)
    cas_store = ContentAddressedByteStore(cas_path)
    runtime_state = materialize_fake_runner_session_cas(
        state=state or empty_runtime_state(),
        cas_store=cas_store,
    )
    store.persist_runtime_state(runtime_state, cas_store)
    return OpenRuntimeContext(
        paths=CliWorkspacePaths(root, db_path, cas_path),
        store=store,
        cas_store=cas_store,
    )


def _open_runtime(runtime: OpenRuntimeContext) -> OpenRuntimeContext:
    return OpenRuntimeContext(
        paths=runtime.paths,
        store=SQLiteRuntimeStore.open(runtime.paths.db_path),
        cas_store=ContentAddressedByteStore(runtime.paths.cas_path),
    )


def _reopen_runtime(runtime: OpenRuntimeContext) -> OpenRuntimeContext:
    runtime.store.close()
    return _open_runtime(runtime)


def _load(runtime: OpenRuntimeContext) -> RuntimeState:
    return runtime.store.load_runtime_state(runtime.cas_store)


def _evidence_files(root: Path) -> list[Path]:
    base = root / EVIDENCE_ROOT
    if not base.exists():
        return []
    return sorted(path for path in base.rglob("*") if path.is_file())


def _diagnostic_payloads(runtime: OpenRuntimeContext, run_id: str) -> list[dict]:
    if not runtime.session_event_store_exists():
        return []
    store = runtime.open_session_event_store(create=False)
    try:
        page = store.read(run_id, after_sequence=0, limit=500)
    finally:
        close = getattr(store, "close", None)
        if close is not None:
            close()
    return [
        dict(event.bounded_payload)
        for event in page.events
        if event.kind == "diagnostic"
    ]


# ---------------------------------------------------------------------------
# Ready-candidate and request selection.
# ---------------------------------------------------------------------------


def _ready_for(state: RuntimeState, work_item_id: Any) -> Any:
    return _single(
        candidate
        for candidate in list_ready_dispatch_candidates(state).candidates
        if str(candidate.work_item_id) == str(work_item_id)
    )


def _claimed_for(state: RuntimeState, work_item_id: Any) -> Any:
    """Return the activation still claimed by a refused or interrupted run."""
    return _single(
        activation
        for activation in state.activations.values()
        if str(activation.work_item_id) == str(work_item_id)
        and activation.claimed_by_run_id is not None
    )


def _ready_stage(state: RuntimeState, stage_kind_id: str) -> Any:
    return _single(
        candidate
        for candidate in list_ready_dispatch_candidates(state).candidates
        if str(candidate.stage_kind_id) == stage_kind_id
    )


def _request_id(state: RuntimeState) -> Any:
    return co.closure_request_item(state).ref.work_item_id


def _start_evaluation(
    tmp_path: Path, source: dict[str, Any] | None = None, **evidence: Any
) -> OpenRuntimeContext:
    """Open the closure target and let one lifecycle tick apply evaluate."""
    fixture = co.opened_state(source, **evidence)
    runtime = _runtime(tmp_path, fixture.state)
    tick = run_lifecycle_transition_once(runtime)
    assert tick.code == "lifecycle_transition_applied", tick
    assert len(co.closure_request_items(_load(runtime))) == 1
    return runtime


# ---------------------------------------------------------------------------
# Runner-session fakes (copied from tests/support/runner_sessions.py).
# ---------------------------------------------------------------------------


def _dispatch_echo(request: Any) -> DispatchEcho:
    return DispatchEcho.from_dispatch_envelope(
        request.dispatch_envelope,
        correlation_id=request.correlation_id,
        selected_adapter_kind=request.selected_adapter_kind,
    )


def _started_session(request: Any, handle: Any) -> StartedSession:
    return StartedSession(
        _dispatch_echo(request), handle, f"fake:{request.session_id}", {}
    )


def _operation_result(
    operation: str, result: str, at: int = 0
) -> RunnerCancellationOperationResult:
    detail = {"operation": operation}
    return RunnerCancellationOperationResult(
        operation,
        result,
        at,
        at,
        detail,
        runner_cancellation_diagnostic_digest(detail),
    )


def _cleanup_result(disposition: str, at: int = 0) -> RunnerCleanupResult:
    diagnostic = {"cleanup": disposition}
    return RunnerCleanupResult(
        disposition,
        at,
        at,
        diagnostic,
        runner_cancellation_diagnostic_digest(diagnostic),
    )


class _ImmediateHandle:
    def __init__(self, outcome: Any, cleanup_disposition: str = "not_required") -> None:
        self._outcome: Any = outcome
        self._cleanup = _cleanup_result(cleanup_disposition)

    def poll_completion(self) -> Any:
        outcome = self._outcome
        self._outcome = None
        return outcome

    def request_cancel(self) -> RunnerCancellationOperationResult:
        return _operation_result("cooperative_cancel", "unsupported")

    def terminate(self) -> RunnerCancellationOperationResult:
        return _operation_result("terminate", "unsupported")

    def kill(self) -> RunnerCancellationOperationResult:
        return _operation_result("kill", "unsupported")

    def cleanup(self) -> RunnerCleanupResult:
        return self._cleanup


class _RecordingAdapter:
    adapter_kind = "codex"

    def __init__(self, start: Any) -> None:
        self._start = start
        self.config: Any = None
        self.requests: list[Any] = []
        self.reconcile_requests: list[Any] = []
        # The coordinator maps any start exception to session_reconciliation_required,
        # so fake-side exceptions are captured here and asserted empty by the tests.
        self.errors: list[str] = []

    def start_session(self, request: Any) -> Any:
        self.requests.append(request)
        try:
            return self._start(request)
        except BaseException as error:
            self.errors.append(f"{type(error).__name__}: {error}")
            raise

    def reconcile_session(self, request: Any) -> Any:
        self.reconcile_requests.append(request)
        return Unsupported(_dispatch_echo(request.invocation_request))


# ---------------------------------------------------------------------------
# Fake closure evaluator that reads evidence pages from disk.
# ---------------------------------------------------------------------------


class _Evaluator:
    """Codex-kind fake that reassembles closure evidence from materialized pages."""

    def __init__(self, root: Path, *, cwd: Path | None = None) -> None:
        self.root = root
        self.review_marker: str = co.PASSING_MARKER
        self.alpha_marker: str = "ALPHA_READY"
        self.alpha_artifact: Mapping[str, Any] | None = gl.report_payload("alpha")
        self.mutation: str | None = None
        self.mutated: list[Path] = []
        self.mutated_bytes: list[bytes] = []
        self.snapshots: list[dict[str, Any]] = []
        self.overflowed: list[bool] = []
        self.evidence_roots_at_start: list[bool] = []
        self.directories: list[Path] = []
        self.page_paths: list[list[str]] = []
        self.adapter = _RecordingAdapter(self._start)
        self.adapter.config = SimpleNamespace(cwd=(cwd or root).resolve())

    def local_config(self) -> AdapterLocalConfig:
        return AdapterLocalConfig(adapters={"codex": self.adapter})

    def _start(self, request: Any) -> Any:
        stage = str(request.dispatch_envelope.stage_kind_id)
        if stage == ALPHA_STAGE:
            return self._finish(
                request, marker=self.alpha_marker, artifact=self.alpha_artifact
            )
        assert stage == REVIEW_STAGE, stage
        payload = request.dispatch_envelope.work_item_payload
        self.overflowed.append(OVERFLOW_KEY in payload)
        self.evidence_roots_at_start.append((self.root / EVIDENCE_ROOT).exists())
        snapshot, paths = self._reassemble(request)
        self.snapshots.append(snapshot)
        self.page_paths.append(paths)
        self._drift(paths)
        if self.review_marker == UNKNOWN_MARKER:
            return self._finish(request, marker=self.review_marker, artifact=None)
        body = co.verdict_body(snapshot, marker=self.review_marker)
        prior = snapshot.get("prior_verdict")
        if prior is not None:
            body["rubric"] = prior["payload"]["rubric"]
        return self._finish(request, marker=self.review_marker, artifact=body)

    def _reassemble(self, request: Any) -> tuple[dict[str, Any], list[str]]:
        payload = request.dispatch_envelope.work_item_payload
        projection = payload.get(OVERFLOW_KEY)
        if projection is None:
            return _plain(payload[SNAPSHOT_KEY]), []
        component = hashlib.sha256(
            f"{request.session_id}\n{request.dispatch_generation}".encode()
        ).hexdigest()[:32]
        assert projection["materialization_directory"] == (
            f"{EVIDENCE_ROOT}/{component}"
        ), projection["materialization_directory"]
        directory = self.root / projection["materialization_directory"]
        assert directory.is_dir(), directory
        self.directories.append(directory)

        paths: list[str] = []
        index_bytes = b"".join(
            self._read_page(descriptor, paths)
            for descriptor in projection["index_pages"]
        )
        index = json.loads(index_bytes.decode("utf-8"))
        assert index["snapshot_digest"] == projection["snapshot_digest"]
        assert index["closure_target_id"] == projection["closure_target_id"]

        documents: dict[str, Any] = {}
        evidence: list[tuple[int, Any]] = []
        for segment in index["segments"]:
            body = b"".join(
                self._read_page(descriptor, paths) for descriptor in segment["pages"]
            )
            assert len(body) == segment["document_bytes"], segment["segment"]
            assert hashlib.sha256(body).hexdigest() == segment["document_sha256"]
            document = json.loads(body.decode("utf-8"))
            if segment["segment"] == "evidence_artifact":
                evidence.append((int(segment["position"]), document))
            else:
                documents[segment["segment"]] = document
        evidence.sort(key=lambda item: item[0])
        assert len(evidence) == projection["evidence_artifact_count"]

        listing = {
            path.relative_to(self.root).as_posix()
            for path in directory.rglob("*")
            if path.is_file()
        }
        assert listing == set(paths), sorted(listing ^ set(paths))

        snapshot = {
            "closure_target_id": projection["closure_target_id"],
            "lineage_id": projection["lineage_id"],
            "freshness_anchor_digest": projection["freshness_anchor_digest"],
            "root_contract": documents["root_contract"],
            "prior_verdict": documents.get("prior_verdict"),
            "evidence_artifacts": [document for _, document in evidence],
        }
        return snapshot, paths

    def _read_page(self, descriptor: Mapping[str, Any], paths: list[str]) -> bytes:
        data = (self.root / descriptor["path"]).read_bytes()
        assert len(data) == descriptor["bytes"], descriptor
        assert hashlib.sha256(data).hexdigest() == descriptor["sha256"], descriptor
        paths.append(str(descriptor["path"]))
        return data

    def _drift(self, paths: list[str]) -> None:
        """Alter or delete one page after every page has been read."""
        if self.mutation is None or not paths:
            return
        target = self.root / paths[0]
        # Materialized pages and their directory are read-only; the fake
        # restores owner write access so the drift, not a permission error,
        # is what the runtime observes.
        target.parent.chmod(0o700)
        target.chmod(0o600)
        if self.mutation == "alter":
            target.write_bytes(target.read_bytes() + b"\n# drift\n")
            self.mutated_bytes.append(target.read_bytes())
        elif self.mutation == "delete":
            target.unlink()
            self.mutated_bytes.append(b"")
        else:
            raise AssertionError(self.mutation)
        self.mutated.append(target)

    def _finish(self, request: Any, *, marker: str, artifact: Any) -> Any:
        outcome = AdapterSuccessResult.from_unredacted(
            adapter_id=request.adapter_id,
            dispatch_echo=_dispatch_echo(request),
            redaction_policy=request.redaction_policy,
            marker=marker,
            artifact_payload_candidate=artifact,
        )
        return _started_session(
            request, _ImmediateHandle(outcome, cleanup_disposition="complete")
        )


# ---------------------------------------------------------------------------
# Recovery-capable source (mirrors tests/cli/test_cli_candidate_failure_recovery.py).
# ---------------------------------------------------------------------------


def _recoverable_source() -> dict[str, Any]:
    source = co.overflow_source()
    stages = {stage["id"]: stage for stage in source["stage_kinds"]}
    stages["review_stage"]["declared_outcome_ids"] += ("qa.review.failure",)
    stages["alpha_stage"]["declared_outcome_ids"] += ("qa.recovered", "qa.quarantine")
    source["terminal_outcomes"] += [
        {
            "id": "qa.review.failure",
            "stage_kind_id": "review_stage",
            "marker": "QA_FAILURE",
        },
        {
            "id": "qa.recovered",
            "stage_kind_id": "alpha_stage",
            "marker": "QA_RECOVERED",
        },
        {
            "id": "qa.quarantine",
            "stage_kind_id": "alpha_stage",
            "marker": "QA_QUARANTINE",
        },
    ]
    source["terminal_actions"] += [
        {
            "id": "qa.review.recover",
            "stage_kind_id": "review_stage",
            "outcome_id": "qa.review.failure",
            "kind": "recovery_route",
            "target_stage_kind_id": "alpha_stage",
            "target_graph_node_id": "lifecycle.alpha.start",
            "runner_binding_id": "lifecycle.runner",
            "asset_ids": stages["alpha_stage"]["asset_ids"],
        },
        {
            "id": "qa.return",
            "stage_kind_id": "alpha_stage",
            "outcome_id": "qa.recovered",
            "kind": "return_to_recorded_source",
        },
        {
            "id": "qa.quarantine",
            "stage_kind_id": "alpha_stage",
            "outcome_id": "qa.quarantine",
            "kind": "quarantine_lineage",
        },
    ]
    policy = deepcopy(generic_admission.source()["recovery_policies"][0])
    policy.update(
        id="qa.policy",
        source_recovery_action_ids=("qa.review.recover",),
        return_action_ids=("qa.return",),
        quarantine_action_ids=("qa.quarantine",),
        recovery_stage_kind_id="alpha_stage",
        reset_trigger_action_ids=(),
        cooldown_wait_state_id="qa.cooldown",
    )
    source["recovery_policies"] = [policy]
    source["wait_states"] = [
        {
            "id": "qa.cooldown",
            "kind": "timer",
            "policy_id": "qa.policy",
            "starts_at_attempt": 2,
            "duration_seconds": 900,
        }
    ]
    source["runner_bindings"][0]["runtime_failure_mappings"] = [
        {
            "stage_kind_id": "review_stage",
            "failure_kind": "result_rejected",
            "action_id": "qa.review.recover",
        }
    ]
    binding = source["runner_bindings"][0]
    binding["component_pin"]["legal_terminal_result_ids"] += (
        "QA_RECOVERED",
        "QA_QUARANTINE",
    )
    binding["terminal_result_mappings"] = [
        {
            "stage_kind_id": "alpha_stage",
            "runner_result_id": result,
            "outcome_id": outcome,
        }
        for result, outcome in (
            ("COMPLETE", "lifecycle.alpha.ready"),
            ("QA_RECOVERED", "qa.recovered"),
            ("QA_QUARANTINE", "qa.quarantine"),
        )
    ]
    return source


# ---------------------------------------------------------------------------
# CLI and daemon helpers.
# ---------------------------------------------------------------------------


def _invoke(argv: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def _json(raw: str) -> dict[str, Any]:
    assert raw.endswith("\n"), raw
    payload = json.loads(raw)
    assert isinstance(payload, dict), payload
    return payload


def _cli(root: Path, *command: str) -> dict[str, Any]:
    code, stdout, stderr = _invoke(["--json", "--workspace", str(root), *command])
    assert code == 0, (command, code, stdout, stderr)
    envelope = _json(stdout)
    assert envelope["ok"] is True, envelope
    return envelope


def _daemon_options(
    paths: CliWorkspacePaths,
    *,
    max_ticks: int,
    adapter_kind: str | None = None,
    local_config: AdapterLocalConfig | None = None,
    monitor: str = "none",
) -> DaemonRunOptions:
    return DaemonRunOptions(
        paths=paths,
        idle_sleep_seconds=0.0,
        max_ticks=max_ticks,
        activation_id=None,
        adapter_kind=adapter_kind,
        local_config=local_config,
        monitor=monitor,
        actor_id="local_operator",
    )


# ---------------------------------------------------------------------------
# Case (a): oversized PASS through the bounded unit, the daemon, and reopen.
# ---------------------------------------------------------------------------


def test_oversized_pass_materializes_pages_closes_target_and_survives_reopen(
    tmp_path: Path,
) -> None:
    runtime = _start_evaluation(tmp_path)
    root = runtime.paths.workspace_path
    evaluator = _Evaluator(root)
    before = _load(runtime)
    snapshot_before = co.durable_snapshot(before)
    request_id = _request_id(before)

    result = run_bounded_execution_unit(
        runtime,
        activation_id=_ready_for(before, request_id).activation_id,
        local_config=evaluator.local_config(),
    )

    assert result.code == "observation_accepted", result
    assert result.accepted is True
    assert evaluator.adapter.errors == [], evaluator.adapter.errors
    assert evaluator.overflowed == [True]
    assert evaluator.evidence_roots_at_start == [True]
    recorded = evaluator.snapshots[0]
    for key in (
        "closure_target_id",
        "freshness_anchor_digest",
        "root_contract",
        "prior_verdict",
        "evidence_artifacts",
    ):
        assert _plain(recorded[key]) == _plain(snapshot_before[key]), key
    assert evaluator.directories and not any(d.exists() for d in evaluator.directories)
    assert _evidence_files(root) == []

    after = _load(runtime)
    assert len(_values(after.closure_terminal_records)) == (
        len(_values(before.closure_terminal_records)) + 1
    )
    reopened = _reopen_runtime(runtime)
    assert _load(reopened) == after


def test_daemon_tick_one_applies_evaluate_and_tick_two_runs_oversized_evaluator(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path, co.opened_state().state)
    root = runtime.paths.workspace_path
    evaluator = _Evaluator(root)
    runtime.close()

    first = run_daemon_loop(
        _daemon_options(
            runtime.paths,
            max_ticks=1,
            adapter_kind="codex",
            local_config=evaluator.local_config(),
        )
    )
    assert first.lifecycle_transitions_applied == 1, first
    assert first.units_started == 0
    assert first.last_result["code"] == "lifecycle_transition_applied", (
        first.last_result
    )
    assert evaluator.overflowed == []

    second = run_daemon_loop(
        _daemon_options(
            runtime.paths,
            max_ticks=1,
            adapter_kind="codex",
            local_config=evaluator.local_config(),
        )
    )
    assert second.units_started == 1, second
    assert second.last_result["code"] == "observation_accepted", second.last_result
    assert evaluator.overflowed == [True]
    assert _evidence_files(root) == []

    runtime = _open_runtime(runtime)
    state = _load(runtime)
    assert len(_values(state.closure_terminal_records)) == 1
    runtime.close()


# ---------------------------------------------------------------------------
# Case (b): GAP, ALPHA remediation, new alpha evidence, then oversized PASS.
# ---------------------------------------------------------------------------


def test_gap_then_alpha_remediation_then_oversized_pass_carries_prior_rubric(
    tmp_path: Path,
) -> None:
    source = co.overflow_source()
    source["completion_behaviors"][0]["evidence_artifact_schema_ids"] = [
        gl.BETA_REPORT_SCHEMA_ID,
        gl.ALPHA_REPORT_SCHEMA_ID,
    ]
    runtime = _start_evaluation(tmp_path, source)
    root = runtime.paths.workspace_path
    evaluator = _Evaluator(root)
    evaluator.review_marker = co.GAP_MARKER

    first_state = _load(runtime)
    first_request = _request_id(first_state)
    gap = run_bounded_execution_unit(
        runtime,
        activation_id=_ready_for(first_state, first_request).activation_id,
        local_config=evaluator.local_config(),
    )
    assert gap.code == "observation_accepted", gap
    assert evaluator.adapter.errors == [], evaluator.adapter.errors

    gap_state = _load(runtime)
    gap_verdict = _single(
        artifact
        for artifact in gap_state.artifacts.values()
        if artifact.work_item_id == first_request
    )

    # Fresh alpha evidence large enough that the second evaluation also overflows.
    evaluator.alpha_artifact = {
        **gl.report_payload("alpha"),
        "verdict": "alpha accepted " + "y" * 6000,
    }
    alpha = run_bounded_execution_unit(
        runtime,
        activation_id=_ready_stage(gap_state, ALPHA_STAGE).activation_id,
        local_config=evaluator.local_config(),
    )
    assert alpha.code == "observation_accepted", alpha
    assert len(evaluator.adapter.requests) == 2

    tick = run_lifecycle_transition_once(runtime)
    assert tick.code == "lifecycle_transition_applied", tick

    state = _load(runtime)
    second_request = _single(
        item.ref.work_item_id
        for item in co.closure_request_items(state)
        if item.ref.work_item_id != first_request
    )
    evaluator.review_marker = co.PASSING_MARKER
    final = run_bounded_execution_unit(
        runtime,
        activation_id=_ready_for(state, second_request).activation_id,
        local_config=evaluator.local_config(),
    )
    assert final.code == "observation_accepted", final
    assert evaluator.adapter.errors == [], evaluator.adapter.errors

    assert evaluator.overflowed == [True, True]
    prior = evaluator.snapshots[1]["prior_verdict"]
    assert prior is not None
    assert prior["artifact_id"] == gap_verdict.artifact_id
    assert _plain(prior["payload"]["rubric"]) == _plain(gap_verdict.payload["rubric"])
    assert [
        str(entry["artifact_schema_id"])
        for entry in evaluator.snapshots[1]["evidence_artifacts"]
    ] == [gl.ALPHA_REPORT_SCHEMA_ID]
    assert evaluator.snapshots[1]["freshness_anchor_digest"] == prior["payload_digest"]

    final_verdict = _single(
        artifact
        for artifact in _load(runtime).artifacts.values()
        if artifact.work_item_id == second_request
    )
    assert _plain(final_verdict.payload["rubric"]) == _plain(
        gap_verdict.payload["rubric"]
    )
    assert _evidence_files(root) == []


# ---------------------------------------------------------------------------
# Case (c): BLOCKED from complete evidence records a block and suppresses repeats.
# ---------------------------------------------------------------------------


def test_blocked_verdict_from_complete_evidence_records_block_and_suppresses_repeat(
    tmp_path: Path,
) -> None:
    runtime = _start_evaluation(tmp_path)
    root = runtime.paths.workspace_path
    evaluator = _Evaluator(root)
    evaluator.review_marker = co.BLOCKED_MARKER
    state = _load(runtime)
    request_id = _request_id(state)

    result = run_bounded_execution_unit(
        runtime,
        activation_id=_ready_for(state, request_id).activation_id,
        local_config=evaluator.local_config(),
    )
    assert result.code == "observation_accepted", result

    blocked_state = _load(runtime)
    assert len(_values(blocked_state.closure_blocked_records)) == 1
    assert not [
        candidate
        for candidate in list_ready_dispatch_candidates(blocked_state).candidates
        if str(candidate.work_item_id) == str(request_id)
    ]

    repeat = run_lifecycle_transition_once(runtime)
    assert repeat.code == "no_ready_work", repeat
    after = _load(runtime)
    assert len(co.closure_request_items(after)) == len(
        co.closure_request_items(blocked_state)
    )
    assert len(_values(after.closure_blocked_records)) == 1
    assert _evidence_files(root) == []


# ---------------------------------------------------------------------------
# Case (d): drift after read is refused; explicit retry then passes.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mutation", ["alter", "delete"])
def test_page_drift_after_read_is_refused_without_terminal_then_retry_passes(
    tmp_path: Path, mutation: str
) -> None:
    runtime = _start_evaluation(tmp_path)
    root = runtime.paths.workspace_path
    evaluator = _Evaluator(root)
    evaluator.mutation = mutation
    state = _load(runtime)
    request_id = _request_id(state)
    terminals_before = len(_values(state.closure_terminal_records))

    refused = run_bounded_execution_unit(
        runtime,
        activation_id=_ready_for(state, request_id).activation_id,
        local_config=evaluator.local_config(),
    )

    assert evaluator.adapter.errors == [], evaluator.adapter.errors
    assert refused.code == "adapter_failure", refused
    assert refused.adapter_error_kind == "context_mutation_refused", refused
    assert len(evaluator.mutated) == 1
    # Pages are removed by cleanup after the refusal, so the drift is checked
    # against the bytes recorded at mutation time.
    if mutation == "delete":
        assert evaluator.mutated_bytes == [b""]
    else:
        assert evaluator.mutated_bytes[0].endswith(b"# drift\n")
    drifted = _load(runtime)
    assert len(_values(drifted.closure_terminal_records)) == terminals_before
    assert _values(drifted.closure_blocked_records) == []

    evaluator.mutation = None
    retry = run_bounded_execution_unit(
        runtime,
        activation_id=_claimed_for(drifted, request_id).activation_id,
        local_config=evaluator.local_config(),
    )
    assert retry.code == "observation_accepted", retry
    assert len(evaluator.snapshots) == 2
    assert evaluator.overflowed == [True, True]
    assert not any(directory.exists() for directory in evaluator.directories[1:])
    assert len(_values(_load(runtime).closure_terminal_records)) == terminals_before + 1


# ---------------------------------------------------------------------------
# Case (e): evaluator runtime failure, recovery, return, and fresh re-dispatch.
# ---------------------------------------------------------------------------


def test_evaluator_runtime_failure_recovers_returns_and_redispatches_with_fresh_pages(
    tmp_path: Path,
) -> None:
    runtime = _start_evaluation(tmp_path, _recoverable_source())
    root = runtime.paths.workspace_path
    evaluator = _Evaluator(root)
    evaluator.review_marker = UNKNOWN_MARKER
    state = _load(runtime)
    request_id = _request_id(state)

    failed = run_bounded_execution_unit(
        runtime,
        activation_id=_ready_for(state, request_id).activation_id,
        local_config=evaluator.local_config(),
    )
    assert failed.code == "runtime_failure_recovery_applied", failed
    routed = _load(runtime)
    assert runtime_failure_applied(routed, failed.run_id)
    assert evaluator.overflowed == [True]

    evaluator.alpha_marker = "QA_RECOVERED"
    evaluator.alpha_artifact = None
    recovered = run_bounded_execution_unit(
        runtime,
        activation_id=_ready_stage(routed, ALPHA_STAGE).activation_id,
        local_config=evaluator.local_config(),
    )
    assert recovered.code == "observation_accepted", recovered

    returned_state = _load(runtime)
    returned = _ready_stage(returned_state, REVIEW_STAGE)
    assert str(returned.work_item_id) == str(request_id)

    evaluator.review_marker = co.PASSING_MARKER
    final = run_bounded_execution_unit(
        runtime,
        activation_id=returned.activation_id,
        local_config=evaluator.local_config(),
    )
    assert final.code == "observation_accepted", final
    assert evaluator.overflowed == [True, True]
    assert len(_values(_load(runtime).closure_terminal_records)) == 1
    assert _evidence_files(root) == []


# ---------------------------------------------------------------------------
# Case (f): symlinked materialization root refuses before any start.
# ---------------------------------------------------------------------------


def test_symlinked_materialization_root_refuses_before_start_and_retry_succeeds(
    tmp_path: Path,
) -> None:
    runtime = _start_evaluation(tmp_path)
    root = runtime.paths.workspace_path
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = root / EVIDENCE_ROOT
    link.symlink_to(elsewhere, target_is_directory=True)
    evaluator = _Evaluator(root)
    state = _load(runtime)
    request_id = _request_id(state)

    refused = run_bounded_execution_unit(
        runtime,
        activation_id=_ready_for(state, request_id).activation_id,
        local_config=evaluator.local_config(),
    )

    assert refused.code == "session_preparation_refused", refused
    assert evaluator.adapter.requests == []
    assert list(elsewhere.iterdir()) == []
    assert link.is_symlink()
    diagnostics = _diagnostic_payloads(runtime, refused.run_id)
    refusals = [
        payload["closure_evidence_materialization_refused"]
        for payload in diagnostics
        if "closure_evidence_materialization_refused" in payload
    ]
    assert any("symlink" in reason for reason in refusals), diagnostics

    link.unlink()
    # The refused run keeps its activation claimed (no ready candidate exists),
    # so the legal explicit retry targets that claimed activation directly.
    retry = run_bounded_execution_unit(
        runtime,
        activation_id=_claimed_for(_load(runtime), request_id).activation_id,
        local_config=evaluator.local_config(),
    )
    assert retry.code == "observation_accepted", retry
    assert len(evaluator.snapshots) == 1
    assert evaluator.overflowed == [True]


# ---------------------------------------------------------------------------
# Case (g): inline small evaluation never creates the materialization root.
# ---------------------------------------------------------------------------


def test_inline_small_evaluation_creates_no_closure_evidence_directory(
    tmp_path: Path,
) -> None:
    runtime = _start_evaluation(tmp_path, None, count=1, summary_bytes=64)
    root = runtime.paths.workspace_path
    evaluator = _Evaluator(root)
    state = _load(runtime)

    result = run_bounded_execution_unit(
        runtime,
        activation_id=_ready_for(state, _request_id(state)).activation_id,
        local_config=evaluator.local_config(),
    )

    assert result.code == "observation_accepted", result
    assert evaluator.adapter.errors == [], evaluator.adapter.errors
    assert evaluator.overflowed == [False]
    assert evaluator.evidence_roots_at_start == [False]
    assert not (root / EVIDENCE_ROOT).exists()


# ---------------------------------------------------------------------------
# Case (h): read-only CLI handlers succeed on an oversized closure request.
# ---------------------------------------------------------------------------


def test_read_only_handlers_succeed_on_oversized_closure_request(
    tmp_path: Path,
) -> None:
    runtime = _start_evaluation(tmp_path)
    root = runtime.paths.workspace_path
    runtime.store.close()

    _cli(root, "status")
    _cli(root, "runs", "list")
    _cli(root, "trace", "show")
    _cli(root, "waits", "list")
    _cli(root, "interventions", "list")
    _cli(root, "doctor")
    assert _evidence_files(root) == []

    runtime = _open_runtime(runtime)
    evaluator = _Evaluator(root)
    state = _load(runtime)
    result = run_bounded_execution_unit(
        runtime,
        activation_id=_ready_for(state, _request_id(state)).activation_id,
        local_config=evaluator.local_config(),
    )
    assert result.code == "observation_accepted", result
    run_id = str(result.run_id)
    runtime.store.close()

    _cli(root, "runs", "show", run_id)
    _cli(root, "trace", "show", run_id)
    _cli(root, "status")
    _cli(root, "runs", "list")
    _cli(root, "waits", "list")
    _cli(root, "interventions", "list")
    _cli(root, "doctor")
    assert _evidence_files(root) == []


# ---------------------------------------------------------------------------
# Case (i): capacity block through a lifecycle tick and through one daemon tick.
# ---------------------------------------------------------------------------


def test_capacity_block_through_lifecycle_tick_is_durable_and_suppresses_repeat(
    tmp_path: Path,
) -> None:
    source = co.overflow_source(max_snapshot_bytes=9000)
    fixture = co.opened_state(source, count=3, summary_bytes=4000)
    runtime = _runtime(tmp_path, fixture.state)
    root = runtime.paths.workspace_path

    blocked = run_lifecycle_transition_once(runtime)
    assert blocked.code == "lifecycle_closure_blocked", blocked
    assert blocked.accepted is False
    assert blocked.observation_refusal_reason == (
        "closure_request_overflow_snapshot_limit_exceeded"
    )

    state = _load(runtime)
    blocks = list(closure_capacity_blocks(state))
    assert len(blocks) == 1, blocks
    block = blocks[0]
    assert block.reason == "closure_request_overflow_snapshot_limit_exceeded"
    assert block.detail_status == "unverified"
    assert block.measurements is None
    assert block.selected_capacity_limits["max_snapshot_bytes"] == 9000
    assert block.freshness_status == "unknown"
    assert closure_capacity_blocked(state, block.input_id)
    assert len(state.work_items) == len(fixture.state.work_items)
    assert len(co.closure_request_items(state)) == 0

    runtime.store.close()
    status = _cli(root, "status")
    entries = status["data"]["closure_capacity_blocks"]
    assert len(entries) == 1, entries
    assert entries[0]["reason"] == block.reason
    assert entries[0]["detail_status"] == "unverified"
    assert entries[0]["measurements"] is None
    assert entries[0]["selected_capacity_limits"]["max_snapshot_bytes"] == 9000
    assert entries[0]["freshness_status"] == "unknown"

    runtime = _open_runtime(runtime)
    assert closure_capacity_blocked(_load(runtime), block.input_id)
    second = run_lifecycle_transition_once(runtime)
    assert second.code == "no_ready_work", second
    after = _load(runtime)
    assert len(list(closure_capacity_blocks(after))) == 1
    assert len(after.refusals) == len(state.refusals)
    runtime.close()


def test_capacity_block_through_one_daemon_tick_then_no_ready_work(
    tmp_path: Path,
) -> None:
    source = co.overflow_source(max_snapshot_bytes=9000)
    fixture = co.opened_state(source, count=3, summary_bytes=4000)
    runtime = _runtime(tmp_path, fixture.state)
    evaluator = _Evaluator(runtime.paths.workspace_path)
    runtime.close()

    first = run_daemon_loop(
        _daemon_options(
            runtime.paths,
            max_ticks=1,
            adapter_kind="codex",
            local_config=evaluator.local_config(),
        )
    )
    assert first.last_result["code"] == "lifecycle_closure_blocked", first.last_result
    assert first.units_started == 0
    assert evaluator.adapter.requests == []

    second = run_daemon_loop(
        _daemon_options(
            runtime.paths,
            max_ticks=1,
            adapter_kind="codex",
            local_config=evaluator.local_config(),
        )
    )
    assert second.last_result["code"] == "no_ready_work", second.last_result
    assert second.units_started == 0
    assert evaluator.adapter.requests == []

    runtime = _open_runtime(runtime)
    state = _load(runtime)
    assert len(list(closure_capacity_blocks(state))) == 1
    runtime.close()


def _forge_refusal_detail(db_path: Path, input_id: str, case: str) -> None:
    """Hostile durable edit of only the stored refusal detail (owned test DB)."""
    connection = sqlite3.connect(db_path)
    try:
        (detail,) = connection.execute(
            "SELECT detail FROM refusals WHERE input_id = ?", (input_id,)
        ).fetchone()
        record = json.loads(detail)
        if case == "foreign_target":
            record["closure_target_id"] = "closure-target-that-does-not-exist"
            forged: str | None = json.dumps(record)
        elif case == "missing_detail":
            forged = None
        elif case == "coherent_forged_measurements":
            record["measurements"].update(
                snapshot_bytes=1 << 40,
                request_bytes=1 << 40,
                evidence_items=1 << 40,
                page_count=1 << 40,
            )
            forged = json.dumps(record)
        elif case == "schema_two":
            record["schema_version"] = 2
            forged = json.dumps(record)
        else:
            record["measurements"].update(snapshot_bytes=0, max_snapshot_bytes=-1)
            forged = json.dumps(record)
        with connection:
            connection.execute(
                "UPDATE refusals SET detail = ? WHERE input_id = ?", (forged, input_id)
            )
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("case", "detail_status"),
    [
        ("foreign_target", "invalid"),
        ("missing_detail", "absent"),
        ("forged_measurements", "invalid"),
        ("coherent_forged_measurements", "unverified"),
        ("schema_two", "invalid"),
    ],
)
def test_forged_durable_block_detail_cannot_hide_the_hold_in_public_status(
    tmp_path: Path, case: str, detail_status: str
) -> None:
    source = co.overflow_source(max_snapshot_bytes=9000)
    fixture = co.opened_state(source, count=3, summary_bytes=4000)
    runtime = _runtime(tmp_path, fixture.state)
    root = runtime.paths.workspace_path
    blocked = run_lifecycle_transition_once(runtime)
    assert blocked.code == "lifecycle_closure_blocked", blocked
    (genuine,) = closure_capacity_blocks(_load(runtime))
    runtime.store.close()

    _forge_refusal_detail(runtime.paths.db_path, genuine.input_id, case)

    entries = _cli(root, "status")["data"]["closure_capacity_blocks"]
    assert len(entries) == 1, entries
    assert entries[0]["closure_target_id"] == genuine.closure_target_id
    assert entries[0]["input_id"] == genuine.input_id
    assert entries[0]["reason"] == genuine.reason
    assert entries[0]["detail_status"] == detail_status
    assert entries[0]["measurements"] is None

    runtime = _open_runtime(runtime)
    assert closure_capacity_blocked(_load(runtime), genuine.input_id)
    again = run_lifecycle_transition_once(runtime)
    assert again.code == "no_ready_work", again
    runtime.close()


# ---------------------------------------------------------------------------
# Case (ii): context-unbound overflow with a mismatched or correct codex cwd.
# ---------------------------------------------------------------------------


def test_context_unbound_overflow_with_mismatched_codex_cwd_refuses_before_start(
    tmp_path: Path,
) -> None:
    runtime = _start_evaluation(tmp_path)
    root = runtime.paths.workspace_path
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    evaluator = _Evaluator(root, cwd=elsewhere)
    state = _load(runtime)

    result = run_bounded_execution_unit(
        runtime,
        activation_id=_ready_for(state, _request_id(state)).activation_id,
        local_config=evaluator.local_config(),
    )

    assert result.code == "adapter_failure", result
    assert evaluator.adapter.requests == []
    assert evaluator.snapshots == []
    reasons = [dict(entry).get("reason") for entry in result.diagnostics]
    assert "closure_evidence_codex_cwd_mismatch" in reasons, result.diagnostics


def test_context_unbound_overflow_with_correct_codex_cwd_proceeds(
    tmp_path: Path,
) -> None:
    runtime = _start_evaluation(tmp_path)
    root = runtime.paths.workspace_path
    evaluator = _Evaluator(root)
    state = _load(runtime)

    result = run_bounded_execution_unit(
        runtime,
        activation_id=_ready_for(state, _request_id(state)).activation_id,
        local_config=evaluator.local_config(),
    )

    assert result.code == "observation_accepted", result
    assert len(evaluator.adapter.requests) == 1
    assert evaluator.overflowed == [True]
    assert _evidence_files(root) == []
