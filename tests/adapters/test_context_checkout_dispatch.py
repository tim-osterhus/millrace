from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from cli.test_cli_bounded_execution_unit import (
    _compile_codex_with_selected_authority,
    _load,
    _ready_state_for_plan,
    _reopen_runtime,
    _runtime,
)
from millrace.adapters.cli import session_completion
from millrace.adapters.cli.run import run_bounded_execution_unit
from millrace.contracts import context_checkout_manifest_digest
from millrace.contracts.transition import AttachRunnerSessionContext
from support.runner_sessions import (
    _config,
    _indeterminate_start,
    _RecordingAdapter,
    _success_start,
)


def _ready_bound_context_runtime(tmp_path):
    from tests.compiler.test_context_bindings import _source_with_context_binding

    source = _source_with_context_binding()
    binding = cast(dict[str, object], cast(list[object], source["context_bindings"])[0])
    binding["required_sources"] = (
        *cast(tuple[object, ...], binding["required_sources"]),
        {
            "source_kind": "workspace_relative_root",
            "source_ref": "docs",
            "max_files": 4,
            "max_bytes": 4096,
        },
    )
    binding["discoverable_sources"] = []
    plan, fingerprint = _compile_codex_with_selected_authority(source)
    state, _ = _ready_state_for_plan(plan, fingerprint)
    runtime = _runtime(tmp_path, state)
    docs = runtime.paths.workspace_path / "docs"
    docs.mkdir(parents=True)
    source_file = docs / "guide.txt"
    source_file.write_text("original context\n", encoding="utf-8")
    return runtime, source_file


def _remove_read_only_checkout(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        path.chmod(0o755 if path.is_dir() else 0o644)
    root.chmod(0o755)
    shutil.rmtree(root)


def _bound_config(runtime, adapter):
    setattr(
        adapter,
        "config",
        SimpleNamespace(
            cwd=runtime.paths.workspace_path,
            wrapper_protocol_version=4,
        ),
    )
    return _config(adapter)


def test_created_attached_restart_rematerializes_cas_without_recapture(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import context_checkout
    from millrace.adapters.cli import run as run_module

    runtime, source_file = _ready_bound_context_runtime(tmp_path)
    original_persist = session_completion._persist_transition

    def crash_before_start(current_runtime, transition):
        if (
            getattr(transition, "expected_state", None) == "created"
            and getattr(transition, "next_state", None) == "starting"
        ):
            raise RuntimeError("crash before bound start")
        return original_persist(current_runtime, transition)

    monkeypatch.setattr(session_completion, "_persist_transition", crash_before_start)
    adapter = _RecordingAdapter(_success_start)
    with pytest.raises(RuntimeError, match="crash before bound start"):
        run_bounded_execution_unit(
            runtime,
            local_config=_bound_config(runtime, adapter),
        )

    current = _load(runtime)
    session = next(iter(current.runner_sessions.values()))
    assert session.state == "created"
    assert session.context_manifest_digest is not None
    checkout = (
        runtime.paths.workspace_path
        / "checkout"
        / session.session_id
        / str(session.dispatch_generation)
    )
    original_bytes = (
        checkout / "required" / "workspace" / "docs" / "guide.txt"
    ).read_bytes()
    _remove_read_only_checkout(checkout)
    source_file.write_text("live drift\n", encoding="utf-8")

    def fail_capture(**_kwargs: object) -> object:
        raise AssertionError("attached restart recaptured live context")

    monkeypatch.setattr(context_checkout, "prepare_context_checkout", fail_capture)
    monkeypatch.setattr(run_module, "prepare_context_checkout", fail_capture)
    monkeypatch.setattr(session_completion, "_persist_transition", original_persist)
    resumed = run_bounded_execution_unit(
        runtime,
        activation_id=current.runs[session.run_id].activation_id,
        local_config=_bound_config(runtime, adapter),
    )

    assert resumed.code == "observation_accepted"
    assert (
        checkout / "required" / "workspace" / "docs" / "guide.txt"
    ).read_bytes() == original_bytes


def test_created_unattached_bound_session_captures_before_start(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import context_checkout
    from millrace.adapters.cli import run as run_module

    runtime, _source_file = _ready_bound_context_runtime(tmp_path)
    original_prepare = context_checkout.prepare_context_checkout
    observed_digests: list[object] = []

    def capture(**kwargs: object) -> object:
        observed_digests.append(kwargs["session"].context_manifest_digest)
        return original_prepare(**kwargs)

    monkeypatch.setattr(context_checkout, "prepare_context_checkout", capture)
    monkeypatch.setattr(run_module, "prepare_context_checkout", capture)
    adapter = _RecordingAdapter(_indeterminate_start)

    result = run_bounded_execution_unit(
        runtime,
        local_config=_bound_config(runtime, adapter),
    )
    session = next(iter(_load(runtime).runner_sessions.values()))

    assert result.code == "session_reconciliation_required"
    assert observed_digests == [None]
    assert session.state == "starting"
    assert session.context_manifest_digest is not None
    assert len(adapter.requests) == 1


def test_created_unattached_retry_fresh_captures_after_attach_failure(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, source_file = _ready_bound_context_runtime(tmp_path)
    original_persist = session_completion._persist_transition

    def fail_context_attach(current_runtime, transition):
        if isinstance(transition, AttachRunnerSessionContext):
            return None
        return original_persist(current_runtime, transition)

    monkeypatch.setattr(session_completion, "_persist_transition", fail_context_attach)
    adapter = _RecordingAdapter(_indeterminate_start)

    first = run_bounded_execution_unit(
        runtime,
        local_config=_bound_config(runtime, adapter),
    )
    current = _load(runtime)
    session = next(iter(current.runner_sessions.values()))
    checkout = (
        runtime.paths.workspace_path
        / "checkout"
        / session.session_id
        / str(session.dispatch_generation)
    )
    old_manifest_digest = context_checkout_manifest_digest(
        (checkout / "checkout.manifest.json").read_bytes()
    )
    old_bytes = (
        checkout / "required" / "workspace" / "docs" / "guide.txt"
    ).read_bytes()

    assert first.code == "session_preparation_refused"
    assert session.state == "created"
    assert session.context_manifest_digest is None
    assert old_bytes == b"original context\n"
    assert checkout.is_dir()

    source_file.write_text("mutated live context\n", encoding="utf-8")
    monkeypatch.setattr(session_completion, "_persist_transition", original_persist)

    retry = run_bounded_execution_unit(
        runtime,
        activation_id=current.runs[session.run_id].activation_id,
        local_config=_bound_config(runtime, adapter),
    )
    retried = _load(runtime)
    attached = retried.runner_sessions[session.session_id]
    new_bytes = (
        checkout / "required" / "workspace" / "docs" / "guide.txt"
    ).read_bytes()
    new_manifest_digest = context_checkout_manifest_digest(
        (checkout / "checkout.manifest.json").read_bytes()
    )

    assert retry.code == "session_reconciliation_required"
    assert attached.state == "starting"
    assert attached.context_manifest_digest == new_manifest_digest
    assert new_manifest_digest != old_manifest_digest
    assert new_bytes == b"mutated live context\n"
    assert new_bytes != old_bytes


def test_created_unattached_retry_fresh_captures_after_persisted_attach_refusal(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, source_file = _ready_bound_context_runtime(tmp_path)
    original_persist = session_completion._persist_transition
    refused_input_ids: list[str] = []

    def refuse_context_attach(current_runtime, transition):
        if isinstance(transition, AttachRunnerSessionContext):
            refused = replace(transition, selected_binding_id="incorrect-binding")
            refused_input_ids.append(refused.input_id)
            return original_persist(current_runtime, refused)
        return original_persist(current_runtime, transition)

    monkeypatch.setattr(
        session_completion,
        "_persist_transition",
        refuse_context_attach,
    )
    adapter = _RecordingAdapter(_indeterminate_start)

    first = run_bounded_execution_unit(
        runtime,
        local_config=_bound_config(runtime, adapter),
    )
    current = _load(runtime)
    session = next(iter(current.runner_sessions.values()))
    checkout = (
        runtime.paths.workspace_path
        / "checkout"
        / session.session_id
        / str(session.dispatch_generation)
    )
    old_manifest_digest = context_checkout_manifest_digest(
        (checkout / "checkout.manifest.json").read_bytes()
    )
    old_bytes = (
        checkout / "required" / "workspace" / "docs" / "guide.txt"
    ).read_bytes()

    assert first.code == "session_preparation_refused"
    assert session.state == "created"
    assert session.context_manifest_digest is None
    assert len(refused_input_ids) == 1
    refused_receipt = current.receipts[refused_input_ids[0]]
    assert refused_receipt.accepted is False
    assert (
        refused_receipt.refusal_reason
        == "runner_session_reconciliation_contradiction"
    )

    source_file.write_text("mutated live context\n", encoding="utf-8")
    monkeypatch.setattr(session_completion, "_persist_transition", original_persist)

    retry = run_bounded_execution_unit(
        runtime,
        activation_id=current.runs[session.run_id].activation_id,
        local_config=_bound_config(runtime, adapter),
    )
    retried = _load(runtime)
    attached = retried.runner_sessions[session.session_id]
    new_bytes = (
        checkout / "required" / "workspace" / "docs" / "guide.txt"
    ).read_bytes()
    new_manifest_digest = context_checkout_manifest_digest(
        (checkout / "checkout.manifest.json").read_bytes()
    )
    attach_input_prefix = f"cli:run.session-context-attach:{session.session_id}:"
    accepted_attach_input_ids = [
        input_id
        for input_id, receipt in retried.receipts.items()
        if input_id.startswith(attach_input_prefix) and receipt.accepted
    ]

    assert retry.code == "session_reconciliation_required"
    assert len(adapter.requests) == 1
    assert len(accepted_attach_input_ids) == 1
    assert accepted_attach_input_ids[0] != refused_input_ids[0]
    assert attached.state == "starting"
    assert attached.context_manifest_digest == new_manifest_digest
    assert new_manifest_digest != old_manifest_digest
    assert new_bytes == b"mutated live context\n"
    assert new_bytes != old_bytes


def test_active_bound_restart_rematerializes_attached_manifest_before_reconcile(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import context_checkout
    from millrace.adapters.cli import run as run_module

    runtime, source_file = _ready_bound_context_runtime(tmp_path)
    adapter = _RecordingAdapter(_indeterminate_start)
    first = run_bounded_execution_unit(
        runtime,
        local_config=_bound_config(runtime, adapter),
    )
    assert first.code == "session_reconciliation_required"
    current = _load(runtime)
    session = next(iter(current.runner_sessions.values()))
    assert session.state == "starting"
    assert session.context_manifest_digest is not None
    checkout = (
        runtime.paths.workspace_path
        / "checkout"
        / session.session_id
        / str(session.dispatch_generation)
    )
    original_bytes = (
        checkout / "required" / "workspace" / "docs" / "guide.txt"
    ).read_bytes()
    _remove_read_only_checkout(checkout)
    source_file.write_text("live drift\n", encoding="utf-8")

    def fail_capture(**_kwargs: object) -> object:
        raise AssertionError("active restart recaptured live context")

    monkeypatch.setattr(context_checkout, "prepare_context_checkout", fail_capture)
    monkeypatch.setattr(run_module, "prepare_context_checkout", fail_capture)
    runtime = _reopen_runtime(runtime)
    restarted = run_bounded_execution_unit(
        runtime,
        activation_id=first.activation_id,
        local_config=_bound_config(runtime, adapter),
    )

    assert restarted.code == "runner_session_orphan_risk"
    assert len(adapter.reconcile_requests) == 1
    assert (
        checkout / "required" / "workspace" / "docs" / "guide.txt"
    ).read_bytes() == original_bytes


def test_active_bound_session_without_attachment_refuses_before_reconcile(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _source_file = _ready_bound_context_runtime(tmp_path)
    adapter = _RecordingAdapter(_indeterminate_start)
    first = run_bounded_execution_unit(
        runtime,
        local_config=_bound_config(runtime, adapter),
    )
    assert first.code == "session_reconciliation_required"
    current = _load(runtime)
    session = next(iter(current.runner_sessions.values()))
    detached = replace(session, context_manifest_digest=None)
    detached_state = replace(
        current,
        runner_sessions={session.session_id: detached},
    )
    monkeypatch.setattr(
        runtime.store,
        "load_runtime_state",
        lambda _cas_store: detached_state,
    )

    resumed = run_bounded_execution_unit(
        runtime,
        activation_id=first.activation_id,
        local_config=_bound_config(runtime, adapter),
    )

    assert resumed.code == "adapter_failure"
    assert len(adapter.requests) == 1
    assert adapter.reconcile_requests == []
