from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

from adapters.test_context_writeback import _writeback_report
from cli.test_cli_bounded_execution_unit import _load, _reopen_runtime, _runtime
from millrace.adapters.cli import session_completion
from millrace.adapters.cli.run import run_bounded_execution_unit
from millrace.adapters.runner_contract import (
    AdapterAttribution,
    AdapterInvocationRequest,
    AdapterSuccessResult,
    AdapterTokenUsage,
)
from millrace.contracts.context_checkout import decode_context_checkout_manifest
from millrace.contracts.runner import runner_session_completion_diagnostic_from_payload
from support.runner_sessions import (
    _config,
    _dispatch_echo,
    _ImmediateHandle,
    _RecordingAdapter,
    _success_start,
)


def test_selected_root_mutation_with_orphan_cleanup_persists_lost_completion(
    tmp_path,
) -> None:
    """Mutation refusal remains durable when runner cleanup is at orphan risk."""
    from compiler.test_context_bindings import _source_with_context_binding
    from kernel.kernel_ping_scenarios import bootstrap_to_taskmaster_claim
    from millrace.adapters.cli.context import contextual_input_id
    from millrace.adapters.cli.context_checkout import prepare_context_checkout
    from millrace.compiler import authority_fingerprint, compile_workflow
    from millrace.contracts.state import DaemonBudgetEpochRecord
    from millrace.contracts.transition import AttachRunnerSessionContext
    from millrace.testing import fake_runner_session_state

    source = _source_with_context_binding(write_enabled=False)
    binding = source["context_bindings"][0]
    binding["required_sources"] = [
        {
            "source_kind": "workspace_relative_root",
            "source_ref": "src",
            "max_files": 8,
            "max_bytes": 4096,
        }
    ]
    binding["discoverable_sources"] = []
    binding["write_rules"] = []
    binding["mutation_policy"] = "forbid_selected_roots"
    result = compile_workflow(source)
    assert result.plan is not None, result.diagnostics
    fingerprint = authority_fingerprint(result.plan)
    state = bootstrap_to_taskmaster_claim(result.plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    runtime = _runtime(tmp_path, state)
    workspace = runtime.paths.workspace_path
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "existing.txt").write_text(
        "before\n",
        encoding="utf-8",
    )
    state = _load(runtime)
    run = state.runs["run-taskmaster"]
    session = state.runner_sessions["test-session:run-taskmaster"]
    binding_decl = next(
        declaration
        for declaration in state.admitted_plans[
            fingerprint
        ].selected_plan.context_bindings
        if str(declaration.stage_kind_id) == str(run.stage_kind_id)
    )
    prepared = prepare_context_checkout(
        paths=runtime.paths,
        session=session,
        plan_fingerprint=fingerprint,
        binding=binding_decl,
        state=state,
        cas_store=runtime.cas_store,
    )
    attachment = AttachRunnerSessionContext(
        f"cli:run.session-context-attach:{session.session_id}",
        run_ref=run.run_ref,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        context_manifest_digest=prepared.manifest_digest,
        selected_binding_id=str(binding_decl.id),
    )
    persisted = session_completion._persist_transition(
        runtime,
        replace(attachment, input_id=contextual_input_id(attachment)),
    )
    assert persisted is not None
    state = _load(runtime)
    run = state.runs[session.run_id]
    session = state.runner_sessions[session.session_id]
    epoch = DaemonBudgetEpochRecord(
        budget_id="b1-orphan-mutation-refusal-usage",
        workspace_path=str(runtime.paths.workspace_path),
        selected_plan_ref=run.run_ref.plan_ref,
        max_wall_seconds=None,
        max_invocations=1,
        max_total_tokens=100,
        started_at=0,
        wall_deadline=None,
        last_observed_at=0,
    )
    runtime.store.create_or_resume_daemon_budget_epoch(epoch)
    runtime.store.reserve_budgeted_runner_start(epoch.budget_id, session)
    mutated_path = workspace / "src" / "mutated.txt"

    def start(request: AdapterInvocationRequest) -> object:
        mutated_path.write_text("mutated\n", encoding="utf-8")
        outcome = AdapterSuccessResult.from_unredacted(
            adapter_id=request.adapter_id,
            dispatch_echo=_dispatch_echo(request),
            redaction_policy=request.redaction_policy,
            marker="TASK_COMPLETE",
            observation_payload_candidate={"summary": "completed"},
            artifact_payload_candidate=_writeback_report(no_op_reason="No update."),
            token_usage=AdapterTokenUsage(
                input_tokens=7,
                output_tokens=5,
                total_tokens=12,
            ),
            attribution=AdapterAttribution(
                cached_input_tokens=11,
                reasoning_tokens=13,
                provider_event_count=17,
                provider_event_bytes=19,
                wrapper_input_bytes=23,
                retained_result_bytes=31,
                tool_call_event_count=37,
                runner_wall_milliseconds=47,
            ),
        )
        return replace(
            _success_start(request),
            handle=_ImmediateHandle(
                outcome,
                cleanup_disposition="orphan_risk",
            ),
        )

    adapter = _RecordingAdapter(start)
    adapter.config = SimpleNamespace(cwd=workspace, wrapper_protocol_version=4)
    before = _load(runtime)
    result2 = run_bounded_execution_unit(
        runtime,
        activation_id=state.runs[session.run_id].activation_id,
        local_config=_config(adapter),
        driving_budget_id=epoch.budget_id,
        on_accepted_start=lambda started: runtime.store.record_budgeted_runner_start(
            epoch.budget_id,
            started,
        ),
    )
    reopened = _reopen_runtime(runtime)
    after = _load(reopened)

    assert result2.code == "adapter_failure"
    assert result2.adapter_error_kind == "context_mutation_refused"
    durable_session = after.runner_sessions[session.session_id]
    assert (durable_session.state, durable_session.cleanup_disposition) == (
        "lost",
        "orphan_risk",
    )
    completion = after.runner_session_completions[session.session_id]
    assert (completion.terminal_state, completion.cleanup_disposition) == (
        "lost",
        "orphan_risk",
    )
    assert completion.exit_kind == "error"
    assert completion.adapter_error_kind == "context_mutation_refused"
    assert completion.runner_result_evidence_digest is None
    assert completion.application_input_id not in after.receipts
    assert after.runner_observations == before.runner_observations
    stored = reopened.cas_store.get_bytes(completion.diagnostic_digest)
    diagnostic = runner_session_completion_diagnostic_from_payload(
        json.loads(stored)
    )
    assert diagnostic.diagnostic == {
        "context_writeback_refusal": "selected live context files changed"
    }
    assert any(
        refusal.reason == "context_mutation_refused" for refusal in after.refusals
    )

    usage = reopened.store.load_runner_session_usage(session.session_id)
    assert usage is not None
    assert usage.final is True
    assert (usage.budget_id, usage.run_id) == (epoch.budget_id, session.run_id)
    assert (usage.dispatch_generation, usage.session_fencing_token) == (
        session.dispatch_generation,
        session.session_fencing_token,
    )
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (7, 5, 12)
    attribution = reopened.store.load_runner_session_attribution_authenticated(
        session.session_id,
        session.dispatch_generation,
        session.session_fencing_token,
    )
    assert attribution is not None
    assert attribution.final is True
    for name, value in {
        "cached_input_tokens": 11,
        "reasoning_tokens": 13,
        "provider_event_count": 17,
        "provider_event_bytes": 19,
        "wrapper_input_bytes": 23,
        "retained_result_bytes": 31,
        "tool_call_event_count": 37,
        "runner_wall_milliseconds": 47,
    }.items():
        metric = attribution.metrics[name]
        assert (metric.value, metric.source, metric.availability) == (
            value,
            "adapter.direct",
            "observed",
        )

    manifest_digest = session.context_manifest_digest
    assert manifest_digest is not None
    manifest_bytes = reopened.cas_store.get_bytes(manifest_digest)
    manifest = decode_context_checkout_manifest(manifest_bytes)
    hydration_receipts = reopened.store.load_context_hydration_receipts_authenticated(
        session.session_id,
        session.dispatch_generation,
        session.session_fencing_token,
    )
    context_values = {
        "manifest_bytes": len(manifest_bytes),
        "catalog_bytes": sum(item.byte_length for item in manifest.catalog),
        "catalog_file_count": len(manifest.catalog),
        "hydrated_bytes": sum(receipt.byte_length for receipt in hydration_receipts),
        "hydrated_file_count": len(hydration_receipts),
        "distinct_content_digest_count": len(
            {receipt.content_digest for receipt in hydration_receipts}
        ),
    }
    manifest_metric_names = {
        "manifest_bytes",
        "catalog_bytes",
        "catalog_file_count",
    }
    for name, value in context_values.items():
        metric = attribution.metrics[name]
        assert (metric.value, metric.source, metric.availability) == (
            value,
            "runtime.context_manifest"
            if name in manifest_metric_names
            else "runtime.hydration_receipts",
            "observed" if name == "manifest_bytes" else "derived",
        )

    cleanup = reopened.store.load_context_cleanup_receipt_authenticated(
        session.session_id,
        session.dispatch_generation,
        manifest_digest,
        session.session_fencing_token,
    )
    assert cleanup is None
    checkout = (
        reopened.paths.workspace_path
        / str(binding_decl.checkout_root)
        / session.session_id
        / str(session.dispatch_generation)
    )
    assert checkout.is_dir()
    assert (checkout / "checkout.manifest.json").is_file()
    assert manifest.files
    assert all(
        (checkout / item.checkout_path).read_bytes()
        == reopened.cas_store.get_bytes(item.content_digest)
        for item in manifest.files
    )
    assert reopened.cas_store.get_bytes(manifest_digest) == manifest_bytes
    reopened.close()
