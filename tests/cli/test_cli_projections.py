from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli.test_cli_plan_commands import _invoke
from millrace.adapters.cli.projections import page_records
from millrace.contracts.public_projections import decode_cursor, encode_cursor, wire
from support.run_controls import pause, runtime_with_run


def invoke(runtime, *args):
    code, out, err = _invoke(
        ["--json", "--bounded", "--workspace", str(runtime.paths.workspace_path), *args]
    )
    return code, json.loads(out or err)


def inventory(root: Path):
    return {
        str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mode)
        for p in root.rglob("*")
        if p.is_file()
    }


def test_bounded_discovery_run_fences_budget_and_nonmutation(tmp_path):
    runtime, run = runtime_with_run(tmp_path, session=True)
    pause(runtime, run)
    before = inventory(tmp_path)
    code, output = invoke(runtime, "runs", "show", run.run_ref.run_id)
    assert code == 0, output
    data = output["data"]
    row = data["records"][0]
    assert row["execution_state"] == "paused"
    assert row["budget_binding"] is None
    assert row["expected_session"]["session_fencing_token"]
    assert row["runner_session"]["created_at"] is not None
    assert row["runner_session"]["attribution"] == {"status": "missing"}
    assert row["runner_session"]["diagnostic_status"] == "not_present"
    assert row["runner_session"]["completion_diagnostic_digest"] is None
    assert all(
        row["runner_session"][field] is None
        for field in (
            "cancellation_phase",
            "cancellation_last_operation",
            "cancellation_last_result",
        )
    )
    assert row["pause_control"]["operation_id"]
    assert row["capability_profile"]["qualification_status"] == "unqualified"
    assert row["checkpoint_status"] == {
        "availability": "unavailable",
        "reason": "native_control_not_qualified",
    }
    assert data["compatibility"]["build_identity"] is None
    assert (
        data["compatibility"]["public_schema_digest"]
        != data["compatibility"]["daemon_runtime"]["public_schema_digest"]
    )
    assert "plan.graph" in data["compatibility"]["bounded_commands"]
    assert str(tmp_path) not in json.dumps(data)
    assert inventory(tmp_path) == before
    runtime.close()


@pytest.mark.parametrize("count", [0, 1, 100, 101])
def test_pages_complete_and_bounded(count):
    rows = [{"kind": "node", "id": str(i)} for i in range(count)]
    base = {"identity": {"workspace_id": "w", "instance_id": "i", "store_epoch": "e"}}
    seen = []
    cursor = None
    while True:
        page = page_records(
            rows,
            base,
            SimpleNamespace(page_size=100, cursor=cursor),
            pin=7,
            filters={"run": "r"},
        )
        seen += page["records"]
        assert len(page["records"]) <= 100
        assert len(wire(page)) <= 128 * 1024
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == count
    assert {row["id"] for row in seen} == {row["id"] for row in rows}


def test_actual_wire_bytes_item_limit_and_cursor_pins():
    base = {"identity": {"workspace_id": "w", "instance_id": "i", "store_epoch": "e"}}
    rows = [{"kind": "node", "id": str(i), "label": "☃" * 2000} for i in range(20)]
    ns = SimpleNamespace(page_size=100, cursor=None)
    page = page_records(rows, base, ns, pin=1, filters={})
    assert 1 < len(page["records"]) < 20 and page["has_more"]
    assert len(wire(page)) < 128 * 1024
    ns.cursor = page["next_cursor"]
    with pytest.raises(ValueError, match="snapshot_changed"):
        page_records(rows, base, ns, pin=2, filters={})
    with pytest.raises(ValueError, match="cursor_scope_mismatch"):
        page_records(rows, base, ns, pin=1, filters={"run_id": "other"})
    token = decode_cursor(ns.cursor)
    token["identity"]["store_epoch"] = "other"
    with pytest.raises(ValueError, match="cursor_scope_mismatch"):
        page_records(
            rows,
            base,
            SimpleNamespace(page_size=100, cursor=encode_cursor(token)),
            pin=1,
            filters={},
        )
    with pytest.raises(ValueError, match="projection_item_too_large"):
        page_records(
            [{"kind": "node", "id": "x", "required": "☃" * 3000}],
            base,
            SimpleNamespace(page_size=50),
            pin=1,
            filters={},
        )


def test_static_graph_survives_control_write_mutable_cursor_refuses(tmp_path):
    runtime, run = runtime_with_run(tmp_path)
    fp = str(run.run_ref.plan_ref.authority_fingerprint)
    code, first = invoke(runtime, "--page-size", "1", "plan", "graph", fp)
    assert code == 0, first
    code, overlay = invoke(runtime, "--page-size", "1", "plan", "overlay", fp)
    assert code == 0, overlay
    pause(runtime, run)
    code, continuation = invoke(
        runtime,
        "--page-size",
        "1",
        "--cursor",
        first["data"]["next_cursor"],
        "plan",
        "graph",
        fp,
    )
    assert code == 0, continuation
    code, changed = invoke(
        runtime,
        "--page-size",
        "1",
        "--cursor",
        overlay["data"]["next_cursor"],
        "plan",
        "overlay",
        fp,
    )
    assert code == 3 and changed["code"] == "snapshot_changed"
    runtime.close()


@pytest.mark.parametrize(
    "command",
    [
        ("status",),
        ("workspace", "check"),
        ("doctor",),
        ("plan", "show"),
        ("package", "list"),
    ],
)
def test_healthy_read_commands_do_not_write(tmp_path, command):
    runtime, _ = runtime_with_run(tmp_path)
    before = inventory(tmp_path)
    code, data = invoke(runtime, *command)
    assert code == 0, data
    assert inventory(tmp_path) == before
    runtime.close()


@pytest.mark.parametrize("broken", ["missing", "corrupt", "old"])
def test_invalid_storage_is_nonmutating_and_safe(tmp_path, broken):
    runtime, _ = runtime_with_run(tmp_path)
    path = runtime.paths.db_path
    runtime.close()
    if broken == "missing":
        path.unlink()
    elif broken == "corrupt":
        path.write_bytes(b"private prompt /secret/location not a database")
    else:
        import sqlite3

        db = sqlite3.connect(path)
        db.execute("UPDATE store_metadata SET store_schema_version=10")
        db.commit()
        db.close()
    before = inventory(tmp_path)
    code, data = invoke(runtime, "workspace", "check")
    assert code == 3
    assert "private prompt" not in json.dumps(data) and str(tmp_path) not in json.dumps(
        data
    )
    assert inventory(tmp_path) == before


def test_wrong_and_missing_filters_refuse(tmp_path):
    runtime, _ = runtime_with_run(tmp_path)
    assert invoke(runtime, "runs", "show", "missing")[1]["code"] == "run_not_found"
    assert (
        invoke(runtime, "plan", "graph", "sha256:" + "0" * 64)[1]["code"]
        == "plan_not_admitted"
    )
    runtime.close()


def test_bounded_public_read_enforces_real_caller_deadline(tmp_path, monkeypatch):
    import time

    from millrace.substrate.cas import ContentAddressedByteStore

    runtime, _ = runtime_with_run(tmp_path)
    original = ContentAddressedByteStore.get_bytes

    def slow(self, key):
        time.sleep(6)
        return original(self, key)

    monkeypatch.setattr(ContentAddressedByteStore, "get_bytes", slow)
    before = inventory(tmp_path)
    start = time.monotonic()
    code, result = invoke(runtime, "status")
    elapsed = time.monotonic() - start
    assert code == 3 and result["code"] == "control_deadline_unknown", result
    assert 3.5 <= elapsed < 5
    assert inventory(tmp_path) == before
    runtime.close()


def test_package_provenance_matches_selected_digests_without_audit(tmp_path):
    from cli.test_cli_plan_commands import (
        _enable_package,
        _import_package_archive,
        _init_workspace,
        _write_package_archive,
    )

    workspace = tmp_path / "workspace"
    archive = tmp_path / "package.tar"
    _write_package_archive(archive)
    _init_workspace(workspace)
    _import_package_archive(workspace, archive, command_id="import")
    _enable_package(workspace, command_id="enable")
    args = ["--json", "--workspace", str(workspace)]
    code, out, err = _invoke(
        [
            *args,
            "plan",
            "admit-package",
            "pkg.example.cli",
            "1.0.0",
            "--workflow-id",
            "wf.cli",
            "--workflow-version",
            "1",
            "--entrypoint",
            "default",
            "--command-id",
            "admit",
            "--input-id",
            "admit",
        ]
    )
    assert code == 0, (out, err)
    fp = json.loads(out)["data"]["plan"]["authority_fingerprint"]
    before = inventory(workspace)
    code, out, err = _invoke([*args, "--bounded", "plan", "show", fp])
    assert code == 0, (out, err)
    row = json.loads(out)["data"]["records"][0]
    assert row["registry_association"]["registry_match"] == "matched"
    assert row["workflow_package_pin"]["entrypoint"] == "default"
    assert row["registry_association"]["records"][0]["package_digest"]
    assert inventory(workspace) == before
    # Historical audited package commands still require explicit command IDs.
    assert _invoke([*args, "package", "list"])[0] == 2
    code, out, err = _invoke([*args, "--bounded", "package", "list"])
    assert code == 0 and inventory(workspace) == before


def test_bounded_trace_keeps_creation_input_and_exact_plan_filter(tmp_path):
    runtime, run = runtime_with_run(tmp_path)
    fp = str(run.run_ref.plan_ref.authority_fingerprint)
    code, output = invoke(
        runtime, "trace", "show", run.run_ref.run_id, "--plan-fingerprint", fp
    )
    assert code == 0, output
    records = output["data"]["records"]
    assert any(
        r["input_id"] == run.created_by_input_id and r["run_id"] == run.run_ref.run_id
        for r in records
    )
    assert (
        invoke(runtime, "runs", "list", "--plan-fingerprint", "missing")[1]["code"]
        == "plan_not_admitted"
    )
    runtime.close()


def test_closure_members_are_complete_paged_rows_and_cooldown_fields_survive(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    import millrace.operator.status as status_module
    from millrace.adapters.cli.projections import _status_records
    from millrace.contracts.state import QueueClosureRecord

    runtime, run = runtime_with_run(tmp_path)
    state = runtime.store.load_runtime_state(runtime.cas_store)
    closure = QueueClosureRecord(
        "closure",
        run.run_ref.plan_ref,
        "lineage",
        "lineage",
        "operator",
        "private reason",
        "close-input",
        tuple(f"work-{n:03}" for n in range(101)),
        (),
        (),
    )
    cooldown = SimpleNamespace(
        wait_id="wait",
        policy_id="policy",
        lineage_id="lineage",
        recovery_attempt_record_id="attempt",
        plan_fingerprint=str(run.run_ref.plan_ref.authority_fingerprint),
        attempt_count=2,
        source_run_id=run.run_ref.run_id,
        source_work_item_id="work",
        source_activation_id="activation",
        recovery_action_id="action",
        target_stage_kind_id="stage",
        target_graph_node_id="node",
        target_runner_binding_id="binding",
        created_input_id="created",
        created_at=1,
        due_at=2,
        consumed_input_id=None,
        consumed_at=None,
        resulting_recovery_activation_id=None,
    )
    monkeypatch.setattr(
        status_module,
        "operator_status",
        lambda *a, **kw: SimpleNamespace(
            queue_families=(),
            stage_kinds=(),
            operator_waits=(),
            quarantines=(),
            recovery_attempts=(),
            cooldown_waits=(cooldown,),
        ),
    )
    records = _status_records(replace(state, queue_closures={"closure": closure}), None)
    members = [r for r in records if r["kind"] == "queue_closure_member"]
    assert len(members) == 101 and {r["member_id"] for r in members} == set(
        closure.closed_work_item_ids
    )
    assert len({r["id"] for r in members}) == 101
    wait = next(r for r in records if r["kind"] == "cooldown_waits")
    assert wait["due_at"] == 2 and wait["source_run_id"] == run.run_ref.run_id
    assert "private reason" not in json.dumps(records)
    runtime.close()


def _persist_lost_with_diagnostic(tmp_path):
    from millrace.adapters.cli.context import transition_context
    from millrace.contracts.runner import (
        RunnerSessionCompletionDiagnostic,
        runner_session_completion_diagnostic_bytes,
    )
    from millrace.contracts.state import RunnerSessionCompletionRecord
    from millrace.contracts.transition import RecordRunnerSessionCompletion
    from millrace.kernel import apply, decide
    from support.run_controls import start_intent

    runtime, run = runtime_with_run(tmp_path, session=True)
    start_intent(runtime, run)
    state = runtime.store.load_runtime_state(runtime.cas_store)
    session = state.runner_sessions[run.current_session_id]
    diagnostic = RunnerSessionCompletionDiagnostic(
        run_id=session.run_id,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        plan_fingerprint=str(run.run_ref.plan_ref.authority_fingerprint),
        claim_id=run.run_ref.claim_id,
        generation=run.run_ref.generation,
        fencing_token=run.run_ref.fencing_token,
        stage_kind_id=str(run.stage_kind_id),
        graph_node_id=state.activations[run.activation_id].graph_node_id,
        runner_binding_id=str(run.runner_binding_id),
        diagnostic={
            "message": "SECRET /private/locator prompt",
            "token": "credential-value",
        },
    )
    evidence_digest = runtime.cas_store.put_bytes(
        runner_session_completion_diagnostic_bytes(diagnostic)
    )
    completion = RunnerSessionCompletionRecord(
        completion_id="lost-completion",
        session_id=session.session_id,
        run_id=session.run_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        terminal_state="lost",
        exit_kind="lost",
        adapter_outcome_kind="unsupported",
        adapter_error_kind=None,
        runner_result_evidence_digest=None,
        primary_cancellation_request_id=None,
        cleanup_disposition="orphan_risk",
        started_at=102,
        cancel_requested_at=None,
        completed_at=103,
        bounds_summary="bounded",
        truncation_metadata="none",
        redaction_policy_id="fixture",
        diagnostic_digest=evidence_digest,
        application_input_id="cli:run.session-completion:lost-completion",
    )
    command = RecordRunnerSessionCompletion(
        "lost-input",
        run_ref=run.run_ref,
        expected_state="starting",
        completion=completion,
    )
    decision = decide(
        state,
        command,
        transition_context(command="fixture", input_id_value=command.input_id),
    )
    assert decision.accepted, decision.refusal
    runtime.store.persist_runtime_state(apply(state, decision), runtime.cas_store)
    return runtime, run, evidence_digest


def test_doctor_pages_real_lost_orphan_diagnostics_and_run_evidence_without_payload(
    tmp_path,
):
    runtime, run, evidence_digest = _persist_lost_with_diagnostic(tmp_path)
    before = inventory(tmp_path)
    rows, cursor = [], None
    for _ in range(10):
        args = [] if cursor is None else ["--cursor", cursor]
        code, output = invoke(runtime, "--page-size", "1", *args, "doctor")
        assert code == 0, output
        rows.extend(output["data"]["records"])
        cursor = output["data"]["next_cursor"]
        if cursor is None:
            break
    assert cursor is None
    codes = {r["code"] for r in rows if r["kind"] == "diagnostic"}
    assert {
        "runner_session_lost",
        "runner_session_orphan_risk",
        "runner_session_reconciliation_unsupported",
        "runner_session_completion_diagnostic",
    } <= codes
    for row in rows:
        if row["kind"] == "diagnostic":
            assert {
                "severity",
                "source",
                "status",
                "evidence_digest",
                "redaction_policy_id",
                "truncation",
            } <= row.keys()
    code, output = invoke(runtime, "runs", "show", run.run_ref.run_id)
    assert code == 0, output
    session = output["data"]["records"][0]["runner_session"]
    assert (
        session["diagnostic_status"] == "available"
        and session["completion_diagnostic_digest"] == evidence_digest
    )
    assert session["orphan_risk"] and session["state"] == "lost"
    assert (
        session["cancellation_phase"] is None
        and session["cancellation_last_result"] is None
    )
    serialized = json.dumps([rows, output])
    assert all(
        secret not in serialized
        for secret in ("SECRET", "/private/locator", "credential-value")
    )
    assert inventory(tmp_path) == before
    runtime.close()


@pytest.mark.parametrize("damage", ["missing", "digest_mismatch"])
def test_required_diagnostic_damage_fails_read_with_specific_safe_status(
    tmp_path, damage
):
    runtime, run, evidence_digest = _persist_lost_with_diagnostic(tmp_path)
    path = runtime.paths.cas_path / "sha256" / evidence_digest[7:]
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"SECRET corrupted diagnostic /private/locator")
    before = inventory(tmp_path)
    for command in (("doctor",), ("runs", "show", run.run_ref.run_id)):
        code, output = invoke(runtime, *command)
        assert code != 0, output
        evidence = output["details"]["diagnostic"]
        assert evidence["status"] == ("missing" if damage == "missing" else "corrupt")
        assert evidence["severity"] == "error" and evidence["redaction_policy_id"]
        assert "SECRET" not in json.dumps(
            output
        ) and "/private/locator" not in json.dumps(output)
    assert inventory(tmp_path) == before
    runtime.close()


def test_dispatch_diagnostics_keep_codes_and_scope_but_omit_detail(
    tmp_path, monkeypatch
):
    import millrace.operator.dispatch as dispatch
    from millrace.adapters.cli.diagnostic_projection import doctor_diagnostics
    from millrace.operator.dispatch import ReadyDispatchDiagnostic

    runtime, run = runtime_with_run(tmp_path)
    state = runtime.store.load_runtime_state(runtime.cas_store)
    fp = str(run.run_ref.plan_ref.authority_fingerprint)
    item = ReadyDispatchDiagnostic(
        run.activation_id,
        run.work_item_id,
        "run_control_paused",
        "policy_refusal",
        fp,
        "PRIVATE prompt",
        "SECRET /private/locator",
    )
    monkeypatch.setattr(
        dispatch,
        "list_ready_dispatch_candidates",
        lambda state: SimpleNamespace(diagnostics=(item,)),
    )
    (row,) = doctor_diagnostics(runtime, state, fp)
    assert row["code"] == "run_control_paused" and row["severity"] == "policy_refusal"
    assert (
        row["source"] == "ready_dispatch" and row["activation_id"] == run.activation_id
    )
    assert row["status"] == "available" and row["evidence_digest"]
    assert all(
        text not in json.dumps(row)
        for text in ("PRIVATE", "SECRET", "/private/locator")
    )
    assert doctor_diagnostics(runtime, state, "wrong-fingerprint") == []
    runtime.close()


def test_native_snapshot_reference_schema_and_required_identity_byte_refusal():
    from millrace.contracts.public_projections import (
        CONTRACT_REVISION,
        MAX_ITEM_BYTES,
        PUBLIC_SCHEMA,
        PUBLIC_SCHEMA_DIGEST,
        digest,
    )

    types = PUBLIC_SCHEMA["types"]
    assert CONTRACT_REVISION == 1
    assert PUBLIC_SCHEMA_DIGEST == digest(PUBLIC_SCHEMA)
    assert types["retained-run-control"]["native"] == (
        "retained-native-evidence-with-snapshot-reference|null"
    )
    native = types["retained-native-evidence-with-snapshot-reference"]
    assert "snapshot" not in native
    assert native["snapshot_reference"] == "same-run-record-snapshot-reference"
    assert types["same-run-record-snapshot-reference"] == {
        "scope": "same_run_record",
        "json_pointer": "/quiescence_evidence/witness",
    }
    # Required identity is neither shortened nor excused by witness deduplication.
    row = {"kind": "run", "id": "雪" * 6000}
    before = dict(row)
    assert len(json.dumps(row, ensure_ascii=False)) < MAX_ITEM_BYTES
    assert len(json.dumps(row, ensure_ascii=False).encode("utf-8")) > MAX_ITEM_BYTES
    assert len(wire(row)) > MAX_ITEM_BYTES
    with pytest.raises(ValueError, match="projection_item_too_large"):
        page_records(
            [row],
            {"identity": {"workspace_id": "w", "instance_id": "i", "store_epoch": "e"}},
            SimpleNamespace(page_size=50, cursor=None),
            pin=1,
            filters={},
        )
    assert row == before


def _refused_context_completion(tmp_path, *, bound=True):
    """Persist a kernel completion and refused application without a runner."""
    from millrace.adapters.cli.context import transition_context
    from millrace.adapters.cli.session_diagnostics import (
        _completion_diagnostic_bytes_for_dispatch,
    )
    from millrace.adapters.runner_contract import RedactionPolicy
    from millrace.contracts import (
        ContextCheckoutFile,
        ContextCheckoutManifest,
        encode_context_checkout_manifest,
    )
    from millrace.contracts.runner import (
        RunnerResultEvidence,
        runner_result_evidence_bytes,
    )
    from millrace.contracts.state import RunnerSessionCompletionRecord
    from millrace.contracts.transition import (
        AdvanceRunnerSession,
        AttachRunnerSessionContext,
        RecordRunnerSessionCompletion,
        RunnerResultObserved,
    )
    from millrace.kernel import apply, decide
    from millrace.operator.dispatch import build_dispatch_envelope_for_run

    runtime, run = runtime_with_run(tmp_path, session=True, bound=bound)
    state = runtime.store.load_runtime_state(runtime.cas_store)
    session = state.runner_sessions[run.current_session_id]

    def transition(item, *, accepted=True):
        nonlocal state
        decision = decide(
            state,
            item,
            transition_context(command="fixture", input_id_value=item.input_id),
        )
        assert decision.accepted is accepted, decision
        state = apply(state, decision)
        return decision

    manifest = None
    if bound:
        plan = state.admitted_plans[
            run.run_ref.plan_ref.authority_fingerprint
        ].selected_plan
        binding = plan.context_bindings[0]
        body = b"private router material"
        manifest = ContextCheckoutManifest(
            session_id=session.session_id,
            dispatch_generation=session.dispatch_generation,
            plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
            binding_id=str(binding.id),
            router_asset_id=str(binding.router_asset_id),
            files=(
                ContextCheckoutFile(
                    checkout_path="router.txt",
                    source_kind="selected_router",
                    source_ref=str(binding.router_asset_id),
                    content_digest=runtime.cas_store.put_bytes(body),
                    byte_length=len(body),
                    required=True,
                ),
            ),
        )
        digest = runtime.cas_store.put_bytes(encode_context_checkout_manifest(manifest))
        transition(
            AttachRunnerSessionContext(
                "attach",
                run_ref=run.run_ref,
                session_id=session.session_id,
                dispatch_generation=session.dispatch_generation,
                session_fencing_token=session.session_fencing_token,
                context_manifest_digest=digest,
                selected_binding_id=str(binding.id),
            )
        )
    for before, after, stamp in (
        ("created", "starting", 110),
        ("starting", "running", 120),
    ):
        transition(
            AdvanceRunnerSession(
                "advance-" + after,
                run_ref=run.run_ref,
                session_id=session.session_id,
                dispatch_generation=session.dispatch_generation,
                session_fencing_token=session.session_fencing_token,
                expected_state=before,
                next_state=after,
                occurred_at=stamp,
            )
        )
    dispatch = build_dispatch_envelope_for_run(state=state, run_id=run.run_ref.run_id)
    names = (
        "run_id",
        "session_id",
        "dispatch_generation",
        "session_fencing_token",
        "plan_fingerprint",
        "claim_id",
        "generation",
        "fencing_token",
        "stage_kind_id",
        "graph_node_id",
        "runner_binding_id",
    )
    evidence = RunnerResultEvidence(
        **{name: getattr(dispatch, name) for name in names},
        marker="TASK_COMPLETE",
        adapter_provenance=None,
        observation_payload=None,
        artifact_payload={},
    )
    evidence_digest = runtime.cas_store.put_bytes(
        runner_result_evidence_bytes(evidence)
    )
    diagnostic = runtime.cas_store.put_bytes(
        _completion_diagnostic_bytes_for_dispatch(
            dispatch,
            {"private": "completion diagnostic"},
            redaction_policy=RedactionPolicy(policy_id="fixture"),
        )
    )
    completion = RunnerSessionCompletionRecord(
        completion_id="refused-completion",
        session_id=session.session_id,
        run_id=run.run_ref.run_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        terminal_state="completed",
        exit_kind="success",
        adapter_outcome_kind="success",
        adapter_error_kind=None,
        runner_result_evidence_digest=evidence_digest,
        primary_cancellation_request_id=None,
        cleanup_disposition="complete",
        started_at=120,
        cancel_requested_at=None,
        completed_at=130,
        bounds_summary="fixture",
        truncation_metadata="none",
        redaction_policy_id="fixture",
        diagnostic_digest=diagnostic,
        application_input_id="cli:run.session-completion:refused-completion",
    )
    transition(
        RecordRunnerSessionCompletion(
            "record-completion",
            run_ref=run.run_ref,
            expected_state="running",
            completion=completion,
        )
    )
    runtime.store.persist_runtime_state(state, runtime.cas_store)
    decision = transition(
        RunnerResultObserved(
            completion.application_input_id,
            run_id=run.run_ref.run_id,
            payload=evidence.payload(),
            observed_at=0,
        ),
        accepted=False,
    )
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_artifact_payload"
    runtime.store.persist_runtime_state(state, runtime.cas_store)
    loaded = runtime.store.load_runtime_state(runtime.cas_store)
    assert loaded.runner_session_completions[session.session_id] == completion
    assert loaded.receipts[completion.application_input_id].accepted is False
    return runtime, run, loaded.runner_sessions[session.session_id], manifest


def _cleanup_receipt(runtime, session, manifest, *, conflicting=False):
    from dataclasses import replace

    from millrace.contracts import encode_context_checkout_manifest
    from millrace.contracts.state import (
        ContextCleanupReceipt,
        context_cleanup_receipt_id,
    )

    if conflicting:
        body = b"different private context"
        manifest = replace(
            manifest,
            files=(
                replace(
                    manifest.files[0],
                    content_digest=runtime.cas_store.put_bytes(body),
                    byte_length=len(body),
                ),
            ),
        )
    digest = runtime.cas_store.put_bytes(encode_context_checkout_manifest(manifest))
    receipt = ContextCleanupReceipt(
        receipt_id="pending",
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        fencing_token=session.session_fencing_token,
        manifest_digest=digest,
        removed_path_classes=("selected_router",),
        removed_file_count=1,
        removed_byte_count=manifest.files[0].byte_length,
        adapter_cleanup_disposition="complete",
    )
    receipt = replace(receipt, receipt_id=context_cleanup_receipt_id(receipt))
    runtime.store.record_context_cleanup_receipt(receipt)
    return receipt


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("missing", "missing"),
        ("matching", "available"),
        ("conflicting", "contradictory"),
        ("matching-and-conflicting", "contradictory"),
        ("unbound", "not_applicable"),
    ],
)
def test_refused_completion_cleanup_public_classification(tmp_path, kind, expected):
    runtime, run, session, manifest = _refused_context_completion(
        tmp_path, bound=kind != "unbound"
    )
    try:
        if kind in {"matching", "matching-and-conflicting"}:
            _cleanup_receipt(runtime, session, manifest)
        if kind in {"conflicting", "matching-and-conflicting"}:
            _cleanup_receipt(runtime, session, manifest, conflicting=True)
        before = inventory(tmp_path)
        for _ in range(2):
            code, output = invoke(runtime, "runs", "show", run.run_ref.run_id)
            assert code == 0, output
            row = output["data"]["records"][0]
            projected = row["runner_session"]
            assert projected["completion_persisted"] is True
            assert projected["application_persisted"] is True
            assert projected["application_status"] == "refused"
            assert projected["usage_evidence"] == {"status": "missing"}
            cleanup = projected["context_cleanup"]
            assert cleanup["status"] == expected
            if expected != "available":
                assert "adapter_cleanup_disposition" not in cleanup
                assert "removed_file_count" not in cleanup
            serialized = json.dumps(cleanup)
            assert session.session_fencing_token not in serialized
            assert str(tmp_path) not in serialized
            assert "private" not in serialized
        assert inventory(tmp_path) == before
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "column,value",
    [
        ("fencing_token", "foreign-fence"),
        ("dispatch_generation", 2),
        ("removed_path_classes_json", "invalid-json"),
    ],
)
def test_cleanup_corruption_preserves_whole_read_refusal(tmp_path, column, value):
    runtime, run, session, manifest = _refused_context_completion(tmp_path)
    try:
        _cleanup_receipt(runtime, session, manifest, conflicting=True)
        runtime.store._connection.execute(
            f"UPDATE context_cleanup_receipts SET {column} = ?", (value,)
        )
        runtime.store._connection.commit()
        before = inventory(tmp_path)
        code, output = invoke(runtime, "runs", "show", run.run_ref.run_id)
        assert code != 0
        assert output["ok"] is False
        assert "records" not in output.get("data", {})
        assert inventory(tmp_path) == before
    finally:
        runtime.close()
