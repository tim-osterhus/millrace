from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from tests.compiler.test_context_bindings import _source_with_context_binding

from kernel.kernel_ping_scenarios import bootstrap_to_taskmaster_claim
from millrace.compiler import authority_fingerprint, compile_workflow
from millrace.contracts import (
    AdvanceRunnerSession,
    AttachRunnerSessionContext,
    ContextCheckoutFile,
    ContextCheckoutManifest,
    RecordRunnerSessionCompletion,
    RequestRunnerSessionCancellation,
    RunnerSessionCompletionRecord,
    context_checkout_manifest_digest,
    encode_context_checkout_manifest,
)
from millrace.contracts.state import (
    DURABLE_INT64_MAX,
    AttributionMetric,
    ContextCleanupReceipt,
    ContextHydrationReceipt,
    RunnerSessionAttributionRecord,
    context_cleanup_receipt_id,
    context_hydration_receipt_id,
)
from millrace.kernel import apply, decide
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.errors import StorageIntegrityError
from millrace.substrate.sqlite import SQLiteRuntimeStore
from millrace.testing import deterministic_context
from millrace.workflows import kernel_ping


def _bound_created_state():
    result = compile_workflow(_source_with_context_binding())
    assert result.plan is not None
    plan = result.plan
    plan_fingerprint = authority_fingerprint(plan)
    state = bootstrap_to_taskmaster_claim(plan, plan_fingerprint)
    run = state.runs["run-taskmaster"]
    from millrace.contracts import CreateRunnerSession

    state = apply(
        state,
        decide(
            state,
            CreateRunnerSession(
                "create-session",
                run_ref=run.run_ref,
                session_id="session-1",
                session_fencing_token="session-fence-1",
                created_at=100,
                explicit_retry_intent=False,
            ),
            deterministic_context(transition_id="transition-create-session"),
        ),
    )
    return state, plan, plan_fingerprint


def _context_manifest(
    *,
    state,
    plan,
    plan_fingerprint: str,
    cas_store: ContentAddressedByteStore,
    session_id: str = "session-1",
    dispatch_generation: int = 1,
    file_bytes: bytes = b"Route selected context.\n",
    byte_length: int | None = None,
    binding_id: str | None = None,
    router_asset_id: str | None = None,
    manifest_plan_fingerprint: str | None = None,
):
    binding = plan.context_bindings[0]
    router_id = router_asset_id or str(binding.router_asset_id)
    file_digest = cas_store.put_bytes(file_bytes)
    manifest = ContextCheckoutManifest(
        session_id=session_id,
        dispatch_generation=dispatch_generation,
        plan_fingerprint=manifest_plan_fingerprint or plan_fingerprint,
        binding_id=binding_id or str(binding.id),
        router_asset_id=router_id,
        files=(
            ContextCheckoutFile(
                checkout_path="router.txt",
                source_kind="selected_router",
                source_ref=router_id,
                content_digest=file_digest,
                byte_length=(
                    len(file_bytes) if byte_length is None else byte_length
                ),
                required=True,
            ),
        ),
        omissions=(),
    )
    manifest_bytes = encode_context_checkout_manifest(manifest)
    manifest_digest = cas_store.put_bytes(manifest_bytes)
    assert manifest_digest == context_checkout_manifest_digest(manifest)
    return manifest, manifest_digest


def _attach_state(state, *, digest: str, input_id: str = "attach-session"):
    run = state.runs["run-taskmaster"]
    session = state.runner_sessions["session-1"]
    return apply(
        state,
        decide(
            state,
            AttachRunnerSessionContext(
                input_id,
                run_ref=run.run_ref,
                session_id=session.session_id,
                dispatch_generation=session.dispatch_generation,
                session_fencing_token=session.session_fencing_token,
                context_manifest_digest=digest,
                selected_binding_id="kernel_ping.taskmaster_context",
            ),
            deterministic_context(transition_id=f"transition-{input_id}"),
        ),
    )


def _persist_initial_state(
    tmp_path: Path,
) -> tuple[object, object, str, ContentAddressedByteStore, Path]:
    state, plan, plan_fingerprint = _bound_created_state()
    db_path = tmp_path / "runtime.sqlite3"
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()
    return state, plan, plan_fingerprint, cas_store, db_path


def test_bound_pre_start_cancellation_round_trips_without_context(
    tmp_path: Path,
) -> None:
    state, _plan, _plan_fingerprint, cas_store, db_path = _persist_initial_state(
        tmp_path
    )
    run = state.runs["run-taskmaster"]
    session = state.runner_sessions["session-1"]
    cancellation = RequestRunnerSessionCancellation(
        "cancel-before-start",
        run_ref=run.run_ref,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        expected_state="created",
        request_id="cancel-1",
        reason="operator_cancel_work",
        source_kind="operator",
        actor_id="operator-1",
        requested_at=110,
        request_order=1,
        primary=True,
    )
    decision = decide(
        state,
        cancellation,
        deterministic_context(transition_id="transition-cancel-before-start"),
    )
    assert decision.accepted
    canceled = apply(state, decision)
    assert canceled.runner_sessions[session.session_id].state == (
        "cancellation_requested"
    )
    assert canceled.runner_sessions[session.session_id].context_manifest_digest is None

    store = SQLiteRuntimeStore.open(db_path)
    try:
        store.persist_runtime_state(canceled, cas_store)
        loaded = store.load_runtime_state(cas_store)
    finally:
        store.close()

    assert loaded == canceled


def test_attached_context_round_trips_and_restart_completion_preserves_digest(
    tmp_path: Path,
) -> None:
    state, plan, plan_fingerprint, cas_store, db_path = _persist_initial_state(
        tmp_path
    )
    _manifest, digest = _context_manifest(
        state=state,
        plan=plan,
        plan_fingerprint=plan_fingerprint,
        cas_store=cas_store,
    )
    attached = _attach_state(state, digest=digest)

    store = SQLiteRuntimeStore.open(db_path)
    try:
        store.persist_runtime_state(attached, cas_store)
    finally:
        store.close()

    restarted_store = SQLiteRuntimeStore.open(db_path)
    try:
        restarted = restarted_store.load_runtime_state(cas_store)
    finally:
        restarted_store.close()
    assert restarted.runner_sessions["session-1"].context_manifest_digest == digest

    run = restarted.runs["run-taskmaster"]
    session = restarted.runner_sessions["session-1"]
    started = apply(
        restarted,
        decide(
            restarted,
            AdvanceRunnerSession(
                "start-session",
                run_ref=run.run_ref,
                session_id=session.session_id,
                dispatch_generation=session.dispatch_generation,
                session_fencing_token=session.session_fencing_token,
                expected_state="created",
                next_state="starting",
                occurred_at=110,
            ),
            deterministic_context(transition_id="transition-start-session"),
        ),
    )
    running = apply(
        started,
        decide(
            started,
            AdvanceRunnerSession(
                "run-session",
                run_ref=run.run_ref,
                session_id=session.session_id,
                dispatch_generation=session.dispatch_generation,
                session_fencing_token=session.session_fencing_token,
                expected_state="starting",
                next_state="running",
                occurred_at=120,
            ),
            deterministic_context(transition_id="transition-run-session"),
        ),
    )
    diagnostic_digest = cas_store.put_bytes(b"completion diagnostic")
    completion = RunnerSessionCompletionRecord(
        completion_id="completion-1",
        session_id=session.session_id,
        run_id=run.run_ref.run_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        terminal_state="failed",
        exit_kind="error",
        adapter_outcome_kind=None,
        adapter_error_kind="invocation_failed",
        runner_result_evidence_digest=None,
        primary_cancellation_request_id=None,
        cleanup_disposition="complete",
        started_at=120,
        cancel_requested_at=None,
        completed_at=130,
        bounds_summary="bounded",
        truncation_metadata="none",
        redaction_policy_id="redaction.default",
        diagnostic_digest=diagnostic_digest,
        application_input_id="cli:run.session-completion:completion-1",
    )
    completed = apply(
        running,
        decide(
            running,
            RecordRunnerSessionCompletion(
                "complete-session",
                run_ref=run.run_ref,
                expected_state="running",
                completion=completion,
            ),
            deterministic_context(transition_id="transition-complete-session"),
        ),
    )
    store = SQLiteRuntimeStore.open(db_path)
    try:
        store.persist_runtime_state(completed, cas_store)
        loaded = store.load_runtime_state(cas_store)
    finally:
        store.close()
    assert loaded.runner_sessions["session-1"].state == "failed"
    assert loaded.runner_sessions["session-1"].context_manifest_digest == digest


@pytest.mark.parametrize(
    "failure",
    ("missing_manifest", "malformed_manifest", "missing_file", "bad_length"),
)
def test_invalid_context_cas_refuses_candidate_without_sqlite_commit(
    tmp_path: Path,
    failure: str,
) -> None:
    state, plan, plan_fingerprint, cas_store, db_path = _persist_initial_state(
        tmp_path
    )
    if failure == "missing_manifest":
        file_digest = cas_store.put_bytes(b"router\n")
        manifest = ContextCheckoutManifest(
            session_id="session-1",
            dispatch_generation=1,
            plan_fingerprint=plan_fingerprint,
            binding_id="kernel_ping.taskmaster_context",
            router_asset_id="kernel_ping.context_router",
            files=(
                ContextCheckoutFile(
                    checkout_path="router.txt",
                    source_kind="selected_router",
                    source_ref="kernel_ping.context_router",
                    content_digest=file_digest,
                    byte_length=7,
                    required=True,
                ),
            ),
            omissions=(),
        )
        manifest_digest = context_checkout_manifest_digest(manifest)
    elif failure == "malformed_manifest":
        manifest_digest = cas_store.put_bytes(b"not canonical manifest")
    else:
        _manifest, manifest_digest = _context_manifest(
            state=state,
            plan=plan,
            plan_fingerprint=plan_fingerprint,
            cas_store=cas_store,
            byte_length=(999 if failure == "bad_length" else None),
        )
        if failure == "missing_file":
            manifest = _manifest
            missing_digest = manifest.files[0].content_digest
            object_path = (
                tmp_path / "cas" / "sha256" / missing_digest.removeprefix("sha256:")
            )
            object_path.unlink()

    candidate = _attach_state(state, digest=manifest_digest)
    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(StorageIntegrityError):
            store.persist_runtime_state(candidate, cas_store)
    finally:
        store.close()

    store = SQLiteRuntimeStore.open(db_path)
    try:
        durable = store.load_runtime_state(cas_store)
    finally:
        store.close()
    assert durable.runner_sessions["session-1"].context_manifest_digest is None
    assert "attach-session" not in durable.receipts


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("session_id", "other-session"),
        ("dispatch_generation", 2),
        ("plan_fingerprint", "sha256:" + "c" * 64),
        ("binding_id", "other-binding"),
        ("router_asset_id", "other-router"),
    ),
)
def test_manifest_relation_authority_mismatch_fails_closed(
    tmp_path: Path,
    field_name: str,
    value: object,
) -> None:
    state, plan, plan_fingerprint, cas_store, db_path = _persist_initial_state(
        tmp_path
    )
    kwargs: dict[str, object] = {field_name: value}
    manifest_plan_fingerprint = kwargs.pop("plan_fingerprint", None)
    _manifest, digest = _context_manifest(
        state=state,
        plan=plan,
        plan_fingerprint=plan_fingerprint,
        cas_store=cas_store,
        manifest_plan_fingerprint=manifest_plan_fingerprint,
        **kwargs,
    )
    candidate = _attach_state(state, digest=digest)

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(StorageIntegrityError):
            store.persist_runtime_state(candidate, cas_store)
    finally:
        store.close()
    store = SQLiteRuntimeStore.open(db_path)
    try:
        assert store.load_runtime_state(cas_store).runner_sessions[
            "session-1"
        ].context_manifest_digest is None
    finally:
        store.close()


def test_changed_selected_file_bytes_fail_on_reload(tmp_path: Path) -> None:
    state, plan, plan_fingerprint, cas_store, db_path = _persist_initial_state(
        tmp_path
    )
    manifest, digest = _context_manifest(
        state=state,
        plan=plan,
        plan_fingerprint=plan_fingerprint,
        cas_store=cas_store,
    )
    attached = _attach_state(state, digest=digest)
    store = SQLiteRuntimeStore.open(db_path)
    try:
        store.persist_runtime_state(attached, cas_store)
    finally:
        store.close()

    file_digest = manifest.files[0].content_digest.removeprefix("sha256:")
    (tmp_path / "cas" / "sha256" / file_digest).write_bytes(b"changed\n")
    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(StorageIntegrityError):
            store.load_runtime_state(cas_store)
    finally:
        store.close()


def test_changed_valid_context_manifest_digest_fails_on_reload(
    tmp_path: Path,
) -> None:
    state, plan, plan_fingerprint, cas_store, db_path = _persist_initial_state(
        tmp_path
    )
    first_manifest, first_digest = _context_manifest(
        state=state,
        plan=plan,
        plan_fingerprint=plan_fingerprint,
        cas_store=cas_store,
        file_bytes=b"Route selected context.\n",
    )
    second_manifest, second_digest = _context_manifest(
        state=state,
        plan=plan,
        plan_fingerprint=plan_fingerprint,
        cas_store=cas_store,
        file_bytes=b"Route selected context!\n",
    )
    assert first_digest != second_digest
    assert (
        first_manifest.session_id,
        first_manifest.dispatch_generation,
        first_manifest.plan_fingerprint,
        first_manifest.binding_id,
        first_manifest.router_asset_id,
    ) == (
        second_manifest.session_id,
        second_manifest.dispatch_generation,
        second_manifest.plan_fingerprint,
        second_manifest.binding_id,
        second_manifest.router_asset_id,
    )
    attached = _attach_state(state, digest=first_digest)

    store = SQLiteRuntimeStore.open(db_path)
    try:
        store.persist_runtime_state(attached, cas_store)
    finally:
        store.close()

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE runner_sessions SET context_manifest_digest = ? "
            "WHERE session_id = 'session-1'",
            (second_digest,),
        )

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(StorageIntegrityError):
            store.load_runtime_state(cas_store)
    finally:
        store.close()


def test_unbound_session_cannot_gain_context_authority_in_persistence(
    tmp_path: Path,
) -> None:
    result = compile_workflow(kernel_ping.WORKFLOW_SOURCE)
    assert result.plan is not None
    plan = result.plan
    plan_fingerprint = authority_fingerprint(plan)
    state = bootstrap_to_taskmaster_claim(plan, plan_fingerprint)
    from millrace.contracts import CreateRunnerSession

    run = state.runs["run-taskmaster"]
    state = apply(
        state,
        decide(
            state,
            CreateRunnerSession(
                "create-session",
                run_ref=run.run_ref,
                session_id="session-1",
                session_fencing_token="session-fence-1",
                created_at=100,
                explicit_retry_intent=False,
            ),
            deterministic_context(transition_id="transition-create-session"),
        ),
    )
    db_path = tmp_path / "runtime.sqlite3"
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()

    context_digest = "sha256:" + "e" * 64
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE runner_sessions SET context_manifest_digest = ? "
            "WHERE session_id = 'session-1'",
            (context_digest,),
        )
    database_before = db_path.read_bytes()

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(
            StorageIntegrityError,
            match="unbound runner session cannot reference context manifest",
        ):
            store.load_runtime_state(cas_store)
    finally:
        store.close()
    assert db_path.read_bytes() == database_before


def test_bound_starting_row_without_context_is_rejected_on_reload(
    tmp_path: Path,
) -> None:
    state, plan, plan_fingerprint, cas_store, db_path = _persist_initial_state(
        tmp_path
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE runner_sessions SET state = 'starting', "
            "start_intent_at = 110 WHERE session_id = 'session-1'"
        )
    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(StorageIntegrityError):
            store.load_runtime_state(cas_store)
    finally:
        store.close()



def _context_hydration_receipt(
    *,
    manifest_digest: str,
    catalog_path: str = "catalog/one.md",
    receipt_id: str | None = None,
    session_id: str = "session-1",
    dispatch_generation: int = 1,
    fencing_token: str = "session-fence-1",
    content_digest: str = "sha256:" + "a" * 64,
    byte_length: int = 11,
    selected_path: str = "selected/catalog/one.md",
) -> ContextHydrationReceipt:
    receipt = ContextHydrationReceipt(
        receipt_id=("fixture-receipt-id" if receipt_id is None else receipt_id),
        session_id=session_id,
        dispatch_generation=dispatch_generation,
        fencing_token=fencing_token,
        manifest_digest=manifest_digest,
        catalog_path=catalog_path,
        content_digest=content_digest,
        byte_length=byte_length,
        selected_path=selected_path,
    )
    if receipt_id is None:
        return replace(receipt, receipt_id=context_hydration_receipt_id(receipt))
    return receipt


def _attribution_record(
    *,
    session_id: str = "session-1",
    dispatch_generation: int = 1,
    fencing_token: str = "session-fence-1",
    final: bool = True,
    metrics: dict[str, AttributionMetric] | None = None,
) -> RunnerSessionAttributionRecord:
    return RunnerSessionAttributionRecord(
        session_id=session_id,
        dispatch_generation=dispatch_generation,
        fencing_token=fencing_token,
        final=final,
        metrics=(
            metrics
            if metrics is not None
            else {
                "cached_input_tokens": AttributionMetric(
                    value=None,
                    source="provider",
                    availability="provider_did_not_report",
                ),
                "wrapper_input_bytes": AttributionMetric(
                    value=7,
                    source="wrapper",
                    availability="observed",
                ),
            }
        ),
    )


def _context_cleanup_receipt(
    *,
    manifest_digest: str,
    receipt_id: str | None = None,
    session_id: str = "session-1",
    dispatch_generation: int = 1,
    fencing_token: str = "session-fence-1",
    removed_file_count: int = 2,
    removed_byte_count: int = 33,
) -> ContextCleanupReceipt:
    receipt = ContextCleanupReceipt(
        receipt_id=("fixture-receipt-id" if receipt_id is None else receipt_id),
        session_id=session_id,
        dispatch_generation=dispatch_generation,
        fencing_token=fencing_token,
        manifest_digest=manifest_digest,
        removed_path_classes=("checkout", "selected"),
        removed_file_count=removed_file_count,
        removed_byte_count=removed_byte_count,
        adapter_cleanup_disposition="complete",
    )
    if receipt_id is None:
        return replace(receipt, receipt_id=context_cleanup_receipt_id(receipt))
    return receipt


def test_context_hydration_receipts_are_separate_fenced_rows_with_public_projection(
    tmp_path: Path,
) -> None:
    state, plan, plan_fingerprint, cas_store, db_path = _persist_initial_state(tmp_path)
    _manifest, manifest_digest = _context_manifest(
        state=state,
        plan=plan,
        plan_fingerprint=plan_fingerprint,
        cas_store=cas_store,
    )
    first = _context_hydration_receipt(manifest_digest=manifest_digest)
    second = _context_hydration_receipt(
        manifest_digest=manifest_digest,
        catalog_path="catalog/two.md",
        content_digest="sha256:" + "b" * 64,
        byte_length=22,
        selected_path="selected/catalog/two.md",
    )

    store = SQLiteRuntimeStore.open(db_path)
    try:
        assert store.record_context_hydration_receipt(first) == first
        assert store.record_context_hydration_receipt(second) == second
        assert store.record_context_hydration_receipt(first) == first
        authenticated = store.load_context_hydration_receipts_authenticated(
            "session-1", 1, "session-fence-1"
        )
        assert authenticated == (first, second)
        public = store.load_context_hydration_receipts("session-1")
    finally:
        store.close()

    assert tuple(item["catalog_path"] for item in public) == (
        "catalog/one.md",
        "catalog/two.md",
    )
    assert all("fencing_token" not in item for item in public)
    assert all("session_fencing_token" not in item for item in public)

    conflicting_hydration = replace(
        first,
        selected_path="selected/catalog/changed.md",
    )
    conflicting_hydration = replace(
        conflicting_hydration,
        receipt_id=context_hydration_receipt_id(conflicting_hydration),
    )
    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(ValueError, match="context hydration evidence conflict"):
            store.record_context_hydration_receipt(conflicting_hydration)
        assert store.load_context_hydration_receipts_authenticated(
            "session-1", 1, "session-fence-1"
        ) == (first, second)
        assert store.load_runtime_state(cas_store) == state
    finally:
        store.close()


def test_context_attribution_is_fixed_key_fenced_and_preserves_unavailable_null(
    tmp_path: Path,
) -> None:
    _state, _plan, _fingerprint, cas_store, db_path = _persist_initial_state(tmp_path)
    record = _attribution_record()

    store = SQLiteRuntimeStore.open(db_path)
    try:
        assert store.record_runner_session_attribution(record) == record
        assert store.record_runner_session_attribution(record) == record
        assert store.load_runner_session_attribution_authenticated(
            "session-1", 1, "session-fence-1"
        ) == record
        public = store.load_runner_session_attribution("session-1", 1)
        with pytest.raises(ValueError, match="attribution evidence conflict"):
            store.record_runner_session_attribution(
                replace(record, final=False)
            )
    finally:
        store.close()

    assert "fencing_token" not in public
    assert public["metrics"]["cached_input_tokens"]["value"] is None
    assert public["metrics"]["wrapper_input_bytes"]["value"] == 7

    store = SQLiteRuntimeStore.open(db_path)
    try:
        assert store.load_runtime_state(cas_store) == _state
    finally:
        store.close()


def test_context_cleanup_receipt_is_separate_fenced_row_with_public_projection(
    tmp_path: Path,
) -> None:
    state, plan, plan_fingerprint, cas_store, db_path = _persist_initial_state(tmp_path)
    _manifest, manifest_digest = _context_manifest(
        state=state,
        plan=plan,
        plan_fingerprint=plan_fingerprint,
        cas_store=cas_store,
    )
    receipt = _context_cleanup_receipt(manifest_digest=manifest_digest)

    store = SQLiteRuntimeStore.open(db_path)
    try:
        assert store.record_context_cleanup_receipt(receipt) == receipt
        assert store.record_context_cleanup_receipt(receipt) == receipt
        assert store.load_context_cleanup_receipt_authenticated(
            "session-1", 1, manifest_digest, "session-fence-1"
        ) == receipt
        public = store.load_context_cleanup_receipt(
            "session-1", 1, manifest_digest
        )
        conflicting_cleanup = replace(receipt, removed_byte_count=34)
        conflicting_cleanup = replace(
            conflicting_cleanup,
            receipt_id=context_cleanup_receipt_id(conflicting_cleanup),
        )
        with pytest.raises(ValueError, match="context cleanup evidence conflict"):
            store.record_context_cleanup_receipt(conflicting_cleanup)
        assert store.load_runtime_state(cas_store) == state
    finally:
        store.close()

    assert "fencing_token" not in public
    assert public["removed_byte_count"] == 33
    assert public["removed_path_classes"] == ("checkout", "selected")



def test_context_receipt_ids_are_production_derived_not_caller_chosen(
    tmp_path: Path,
) -> None:
    state, plan, plan_fingerprint, cas_store, db_path = _persist_initial_state(
        tmp_path
    )
    _manifest, manifest_digest = _context_manifest(
        state=state,
        plan=plan,
        plan_fingerprint=plan_fingerprint,
        cas_store=cas_store,
    )
    hydration = _context_hydration_receipt(
        manifest_digest=manifest_digest,
        receipt_id="caller-chosen-hydration-id",
    )
    cleanup = _context_cleanup_receipt(
        manifest_digest=manifest_digest,
        receipt_id="caller-chosen-cleanup-id",
    )

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(ValueError, match="deterministic"):
            store.record_context_hydration_receipt(hydration)
        with pytest.raises(ValueError, match="deterministic"):
            store.record_context_cleanup_receipt(cleanup)
        assert store.load_context_hydration_receipts("session-1") == ()
        assert store.load_context_cleanup_receipts("session-1") == ()
    finally:
        store.close()


@pytest.mark.parametrize(
    ("record_factory", "field_name"),
    (
        (
            lambda digest: _context_hydration_receipt(
                manifest_digest=digest, byte_length=2**63
            ),
            "byte_length",
        ),
        (
            lambda _digest: _attribution_record(
                metrics={
                    "wrapper_input_bytes": AttributionMetric(
                        value=2**63,
                        source="wrapper",
                        availability="observed",
                    )
                }
            ),
            "value",
        ),
        (
            lambda digest: _context_cleanup_receipt(
                manifest_digest=digest, removed_file_count=2**63
            ),
            "removed_file_count",
        ),
        (
            lambda digest: _context_cleanup_receipt(
                manifest_digest=digest, removed_byte_count=2**63
            ),
            "removed_byte_count",
        ),
    ),
)
def test_context_evidence_refuses_values_above_signed_int64(
    tmp_path: Path,
    record_factory,
    field_name: str,
) -> None:
    state, plan, plan_fingerprint, cas_store, _db_path = _persist_initial_state(
        tmp_path
    )
    _manifest, manifest_digest = _context_manifest(
        state=state,
        plan=plan,
        plan_fingerprint=plan_fingerprint,
        cas_store=cas_store,
    )
    with pytest.raises(ValueError, match=field_name):
        record_factory(manifest_digest)

    assert DURABLE_INT64_MAX == 2**63 - 1


def test_context_evidence_accepts_signed_int64_max_and_rejects_arbitrary_metric_key(
    tmp_path: Path,
) -> None:
    state, plan, plan_fingerprint, cas_store, db_path = _persist_initial_state(tmp_path)
    _manifest, manifest_digest = _context_manifest(
        state=state,
        plan=plan,
        plan_fingerprint=plan_fingerprint,
        cas_store=cas_store,
    )
    hydration = _context_hydration_receipt(
        manifest_digest=manifest_digest,
        byte_length=DURABLE_INT64_MAX,
    )
    cleanup = _context_cleanup_receipt(
        manifest_digest=manifest_digest,
        removed_file_count=DURABLE_INT64_MAX,
        removed_byte_count=DURABLE_INT64_MAX,
    )
    with pytest.raises(ValueError, match="approved metric names"):
        _attribution_record(
            metrics={
                "provider_secret": AttributionMetric(
                    value=1,
                    source="provider",
                    availability="observed",
                )
            }
        )

    store = SQLiteRuntimeStore.open(db_path)
    try:
        assert store.record_context_hydration_receipt(hydration) == hydration
        assert store.record_context_cleanup_receipt(cleanup) == cleanup
    finally:
        store.close()


@pytest.mark.parametrize("record_kind", ("hydration", "attribution", "cleanup"))
@pytest.mark.parametrize(
    ("authority_field", "authority_value"),
    (
        ("session_id", "foreign-session"),
        ("dispatch_generation", 2),
        ("fencing_token", "wrong-fence"),
    ),
)
def test_context_evidence_refuses_foreign_generation_and_fencing_authority(
    tmp_path: Path,
    record_kind: str,
    authority_field: str,
    authority_value: object,
) -> None:
    state, plan, plan_fingerprint, cas_store, db_path = _persist_initial_state(tmp_path)
    _manifest, manifest_digest = _context_manifest(
        state=state,
        plan=plan,
        plan_fingerprint=plan_fingerprint,
        cas_store=cas_store,
    )
    if record_kind == "hydration":
        record = _context_hydration_receipt(manifest_digest=manifest_digest)
    elif record_kind == "attribution":
        record = _attribution_record()
    else:
        record = _context_cleanup_receipt(manifest_digest=manifest_digest)
    record = replace(record, **{authority_field: authority_value})

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(ValueError, match="context evidence authority mismatch"):
            if record_kind == "hydration":
                store.record_context_hydration_receipt(record)
            elif record_kind == "attribution":
                store.record_runner_session_attribution(record)
            else:
                store.record_context_cleanup_receipt(record)
    finally:
        store.close()
