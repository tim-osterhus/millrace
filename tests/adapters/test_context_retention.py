from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from millrace.adapters.runner_contract import (
    RedactionPolicy,
    RunnerCleanupResult,
    runner_cancellation_diagnostic_digest,
)

_EVENT_POLICY = RedactionPolicy(policy_id="test-context-retention")


def _adapter_cleanup() -> RunnerCleanupResult:
    diagnostic = {"disposition": "complete"}
    return RunnerCleanupResult(
        disposition="complete",
        started_at=1,
        completed_at=2,
        diagnostic=diagnostic,
        diagnostic_digest=runner_cancellation_diagnostic_digest(diagnostic),
    )


def _cleanup_kwargs() -> dict[str, object]:
    cleanup = _adapter_cleanup()
    return {
        "adapter_cleanup_disposition": cleanup.disposition,
        "adapter_removed_path_classes": cleanup.removed_path_classes,
        "adapter_removed_file_count": cleanup.removed_file_count,
        "adapter_removed_byte_count": cleanup.removed_byte_count,
        "event_redaction_policy": _EVENT_POLICY,
    }


def test_active_session_context_is_not_removed(tmp_path: Path) -> None:
    from adapters.test_context_writeback import _bound_fixture
    from millrace.adapters.cli.context_retention import (
        cleanup_completed_session_context,
    )

    runtime, _state, session, binding = _bound_fixture(tmp_path)
    checkout = (
        runtime.paths.workspace_path
        / str(binding.checkout_root)
        / session.session_id
        / str(session.dispatch_generation)
    )

    receipt = cleanup_completed_session_context(
        runtime,
        session=session,
        **_cleanup_kwargs(),
    )

    assert receipt is None
    assert checkout.is_dir()
    assert runtime.store.load_context_cleanup_receipt_authenticated(
        session.session_id,
        session.dispatch_generation,
        session.context_manifest_digest,
        session.session_fencing_token,
    ) is None


@pytest.mark.parametrize(
    "missing_disposition",
    ("completion", "usage", "attribution"),
)
def test_incomplete_durable_disposition_does_not_remove_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_disposition: str,
) -> None:
    from adapters.test_context_writeback import _bound_fixture
    from millrace.adapters.cli import context_retention

    runtime, state, session, binding = _bound_fixture(tmp_path)
    terminal_at = session.created_at
    terminal_session = replace(
        session,
        state="completed",
        cleanup_disposition="complete",
        start_intent_at=terminal_at,
        started_at=terminal_at,
        ended_at=terminal_at,
    )
    terminal_state = replace(
        state,
        runner_sessions={session.session_id: terminal_session},
    )
    monkeypatch.setattr(
        type(runtime.store),
        "load_runtime_state",
        lambda _store, _cas_store: terminal_state,
    )
    checkout = (
        runtime.paths.workspace_path
        / str(binding.checkout_root)
        / session.session_id
        / str(session.dispatch_generation)
    )

    if missing_disposition in {"usage", "attribution"}:
        monkeypatch.setattr(
            context_retention,
            "_validate_completion",
            lambda *args, **kwargs: None,
        )
    if missing_disposition == "usage":
        monkeypatch.setattr(
            type(runtime.store),
            "daemon_budget_id_for_session",
            lambda _store, _session_id: "budget-1",
        )
    if missing_disposition == "attribution":
        monkeypatch.setattr(
            context_retention,
            "_validate_usage_disposition",
            lambda *args, **kwargs: None,
        )

    receipt = context_retention.cleanup_completed_session_context(
        runtime,
        session=session,
        **_cleanup_kwargs(),
    )

    assert receipt is None
    assert checkout.is_dir()


def test_exact_checkout_deletion_refuses_escape_and_symlink(tmp_path: Path) -> None:
    from millrace.adapters.cli.context_retention import (
        ContextRetentionError,
        _delete_checkout_tree,
    )

    authority_root = tmp_path / "checkout"
    authority_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(ContextRetentionError, match="outside authority root"):
        _delete_checkout_tree(
            outside,
            authority_root=authority_root,
            expected_payloads={"keep.txt": b"keep"},
        )

    target = authority_root / "session" / "1"
    target.parent.mkdir()
    target.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ContextRetentionError, match="symlink"):
        _delete_checkout_tree(
            target,
            authority_root=authority_root,
            expected_payloads={"keep.txt": b"keep"},
        )

    real_authority = tmp_path / "real-checkout"
    real_authority.mkdir()
    linked_authority = tmp_path / "linked-checkout"
    linked_authority.symlink_to(real_authority, target_is_directory=True)
    with pytest.raises(ContextRetentionError, match="symlink"):
        _delete_checkout_tree(
            linked_authority / "session" / "1",
            authority_root=linked_authority,
            expected_payloads={},
        )

    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_exact_checkout_deletion_is_idempotent_after_partial_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli.context_retention import (
        ContextRetentionError,
        _delete_checkout_tree,
    )

    authority_root = tmp_path / "checkout"
    target = authority_root / "session" / "1"
    target.mkdir(parents=True)
    first = target / "first.txt"
    second = target / "second.txt"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    original_unlink = Path.unlink

    def fail_second(path: Path, *args: object, **kwargs: object) -> None:
        if path.name == "second.txt":
            raise OSError("synthetic deletion failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_second)
    with pytest.raises(ContextRetentionError, match="checkout removal failed"):
        _delete_checkout_tree(
            target,
            authority_root=authority_root,
            expected_payloads={"first.txt": b"first", "second.txt": b"second"},
        )

    assert second.exists()
    monkeypatch.setattr(Path, "unlink", original_unlink)
    _delete_checkout_tree(
        target,
        authority_root=authority_root,
        expected_payloads={"first.txt": b"first", "second.txt": b"second"},
    )
    _delete_checkout_tree(
        target,
        authority_root=authority_root,
        expected_payloads={"first.txt": b"first", "second.txt": b"second"},
    )

    assert not target.exists()
