"""Governed removal of derived runner-session context material."""

from __future__ import annotations

import os
import sqlite3
import stat
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from millrace.adapters.cli import (
    context_checkout,
    context_writeback,
    session_persistence,
)
from millrace.adapters.cli.context import OpenRuntimeContext
from millrace.contracts.context_checkout import (
    ContextCheckoutManifest,
    decode_context_checkout_manifest,
    verify_context_checkout_manifest_digest,
)
from millrace.contracts.state import (
    ContextCleanupReceipt,
    ContextHydrationReceipt,
    RunnerSessionCompletionRecord,
    RunnerSessionRecord,
    context_cleanup_receipt_id,
)
from millrace.substrate.errors import SubstrateError


class ContextRetentionError(ValueError):
    """Raised when derived context cannot be removed safely."""


def cleanup_completed_session_context(
    runtime: OpenRuntimeContext,
    *,
    session: RunnerSessionRecord,
    adapter_cleanup_disposition: str,
    adapter_removed_path_classes: tuple[str, ...],
    adapter_removed_file_count: int,
    adapter_removed_byte_count: int,
    event_redaction_policy: session_persistence.RedactionPolicy,
) -> ContextCleanupReceipt | None:
    """Remove one authenticated session checkout after all durable gates pass."""

    if session.context_manifest_digest is None:
        return None

    try:
        return _cleanup_completed_session_context(
            runtime,
            session=session,
            adapter_cleanup_disposition=adapter_cleanup_disposition,
            adapter_removed_path_classes=adapter_removed_path_classes,
            adapter_removed_file_count=adapter_removed_file_count,
            adapter_removed_byte_count=adapter_removed_byte_count,
        )
    except (
        ContextRetentionError,
        OSError,
        sqlite3.Error,
        SubstrateError,
        TypeError,
        ValueError,
    ) as exc:
        _record_cleanup_refusal(
            runtime,
            session=session,
            error=exc,
            event_redaction_policy=event_redaction_policy,
        )
        return None


def _cleanup_completed_session_context(
    runtime: OpenRuntimeContext,
    *,
    session: RunnerSessionRecord,
    adapter_cleanup_disposition: str,
    adapter_removed_path_classes: tuple[str, ...],
    adapter_removed_file_count: int,
    adapter_removed_byte_count: int,
) -> ContextCleanupReceipt:
    if not isinstance(session, RunnerSessionRecord):
        raise ContextRetentionError("session authority is invalid")
    _validate_adapter_cleanup_evidence(
        disposition=adapter_cleanup_disposition,
        removed_path_classes=adapter_removed_path_classes,
        removed_file_count=adapter_removed_file_count,
        removed_byte_count=adapter_removed_byte_count,
    )
    if adapter_cleanup_disposition not in {"complete", "not_required"}:
        raise ContextRetentionError("adapter cleanup is incomplete")

    state = runtime.store.load_runtime_state(runtime.cas_store)
    stored_session = state.runner_sessions.get(session.session_id)
    if stored_session is None or not _same_session_authority(stored_session, session):
        raise ContextRetentionError("session authority is not current")
    if stored_session.state not in {"completed", "failed", "interrupted"}:
        raise ContextRetentionError("session is not durably terminal")
    manifest_digest = stored_session.context_manifest_digest
    if manifest_digest is None:
        raise ContextRetentionError("session has no initial manifest digest")

    completion = state.runner_session_completions.get(session.session_id)
    _validate_completion(
        completion,
        session=stored_session,
        adapter_cleanup_disposition=adapter_cleanup_disposition,
        state_receipts=state.receipts,
    )
    _validate_usage_disposition(runtime, session=stored_session)
    attribution = runtime.store.load_runner_session_attribution_authenticated(
        stored_session.session_id,
        stored_session.dispatch_generation,
        stored_session.session_fencing_token,
    )
    if attribution is None or not attribution.final:
        raise ContextRetentionError("final attribution disposition is missing")

    authority = context_writeback._selected_authority(state, stored_session)
    if authority is None:
        raise ContextRetentionError("selected context authority is not current")
    _selected_plan, binding, plan_fingerprint = authority
    if binding is None:
        raise ContextRetentionError("selected context binding is missing")
    if str(binding.materialization_retention) != "until_session_durable_terminal":
        raise ContextRetentionError("selected retention policy forbids cleanup")

    manifest_bytes = runtime.cas_store.get_bytes(manifest_digest)
    verify_context_checkout_manifest_digest(manifest_bytes, manifest_digest)
    manifest = decode_context_checkout_manifest(manifest_bytes)
    _validate_manifest_authority(
        manifest,
        session=stored_session,
        plan_fingerprint=plan_fingerprint,
        binding_id=str(binding.id),
        router_asset_id=str(binding.router_asset_id),
    )
    hydration_receipts = (
        runtime.store.load_context_hydration_receipts_authenticated(
            stored_session.session_id,
            stored_session.dispatch_generation,
            stored_session.session_fencing_token,
        )
    )
    expected_payloads = _expected_payloads(
        runtime,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        manifest_digest=manifest_digest,
        hydration_receipts=hydration_receipts,
    )

    existing = runtime.store.load_context_cleanup_receipt_authenticated(
        stored_session.session_id,
        stored_session.dispatch_generation,
        manifest_digest,
        stored_session.session_fencing_token,
    )
    if existing is not None:
        return existing

    checkout_relative = context_checkout._safe_relative_path(
        binding.checkout_root,
        "checkout_root",
    )
    checkout_root = runtime.paths.workspace_path / checkout_relative
    final_root = context_checkout._final_root(
        checkout_root,
        session=stored_session,
    )
    _delete_checkout_tree(
        final_root,
        authority_root=checkout_root,
        expected_payloads=expected_payloads,
    )

    path_classes = set(adapter_removed_path_classes)
    path_classes.add("context_checkout")
    if hydration_receipts:
        path_classes.add("selected_materialization")
    receipt = ContextCleanupReceipt(
        receipt_id="context-cleanup:pending",
        session_id=stored_session.session_id,
        dispatch_generation=stored_session.dispatch_generation,
        fencing_token=stored_session.session_fencing_token,
        manifest_digest=manifest_digest,
        removed_path_classes=tuple(sorted(path_classes)),
        removed_file_count=(
            adapter_removed_file_count + len(expected_payloads)
        ),
        removed_byte_count=(
            adapter_removed_byte_count
            + sum(len(payload) for payload in expected_payloads.values())
        ),
        adapter_cleanup_disposition=adapter_cleanup_disposition,
    )
    receipt = replace(receipt, receipt_id=context_cleanup_receipt_id(receipt))
    persisted = runtime.store.record_context_cleanup_receipt(receipt)
    if persisted != receipt:
        raise ContextRetentionError("cleanup receipt persistence contradicted")
    return receipt


def _same_session_authority(
    stored: RunnerSessionRecord,
    supplied: RunnerSessionRecord,
) -> bool:
    return (
        stored.session_id == supplied.session_id
        and stored.run_id == supplied.run_id
        and stored.dispatch_generation == supplied.dispatch_generation
        and stored.session_fencing_token == supplied.session_fencing_token
        and stored.context_manifest_digest == supplied.context_manifest_digest
    )


def _validate_adapter_cleanup_evidence(
    *,
    disposition: str,
    removed_path_classes: tuple[str, ...],
    removed_file_count: int,
    removed_byte_count: int,
) -> None:
    if disposition not in {"not_required", "complete", "orphan_risk"}:
        raise ContextRetentionError("adapter cleanup disposition is invalid")
    if (
        not isinstance(removed_path_classes, tuple)
        or len(removed_path_classes) > 16
        or removed_path_classes != tuple(sorted(set(removed_path_classes)))
        or any(
            not isinstance(path_class, str) or not path_class.strip()
            for path_class in removed_path_classes
        )
    ):
        raise ContextRetentionError("adapter removed path classes are invalid")
    for value in (removed_file_count, removed_byte_count):
        if type(value) is not int or value < 0 or value > 2**63 - 1:
            raise ContextRetentionError("adapter removed counts are invalid")


def _validate_completion(
    completion: RunnerSessionCompletionRecord | None,
    *,
    session: RunnerSessionRecord,
    adapter_cleanup_disposition: str,
    state_receipts: Mapping[str, object],
) -> None:
    if completion is None:
        raise ContextRetentionError("durable completion is missing")
    if (
        completion.session_id != session.session_id
        or completion.run_id != session.run_id
        or completion.dispatch_generation != session.dispatch_generation
        or completion.session_fencing_token != session.session_fencing_token
        or completion.terminal_state != session.state
        or completion.cleanup_disposition != adapter_cleanup_disposition
    ):
        raise ContextRetentionError("durable completion authority contradicted")
    if (
        completion.terminal_state == "completed"
        and completion.application_input_id not in state_receipts
    ):
        raise ContextRetentionError("semantic completion disposition is missing")


def _validate_usage_disposition(
    runtime: OpenRuntimeContext,
    *,
    session: RunnerSessionRecord,
) -> None:
    budget_id = runtime.store.daemon_budget_id_for_session(session.session_id)
    usage = runtime.store.load_runner_session_usage(session.session_id)
    if budget_id is None and usage is None:
        return
    if usage is None or not usage.final:
        raise ContextRetentionError("final usage disposition is missing")
    if (
        usage.session_id != session.session_id
        or usage.run_id != session.run_id
        or usage.dispatch_generation != session.dispatch_generation
        or usage.session_fencing_token != session.session_fencing_token
        or usage.budget_id != budget_id
    ):
        raise ContextRetentionError("final usage authority contradicted")


def _validate_manifest_authority(
    manifest: ContextCheckoutManifest,
    *,
    session: RunnerSessionRecord,
    plan_fingerprint: str,
    binding_id: str,
    router_asset_id: str,
) -> None:
    if (
        manifest.session_id != session.session_id
        or manifest.dispatch_generation != session.dispatch_generation
        or manifest.plan_fingerprint != plan_fingerprint
        or manifest.binding_id != binding_id
        or manifest.router_asset_id != router_asset_id
    ):
        raise ContextRetentionError("initial manifest authority contradicted")


def _expected_payloads(
    runtime: OpenRuntimeContext,
    *,
    manifest: ContextCheckoutManifest,
    manifest_bytes: bytes,
    manifest_digest: str,
    hydration_receipts: Sequence[ContextHydrationReceipt],
) -> dict[str, bytes]:
    expected = {"checkout.manifest.json": manifest_bytes}
    for item in manifest.files:
        payload = runtime.cas_store.get_bytes(item.content_digest)
        if len(payload) != item.byte_length:
            raise ContextRetentionError("manifest payload length contradicted")
        expected[item.checkout_path] = payload
    for receipt in hydration_receipts:
        if (
            receipt.manifest_digest != manifest_digest
            or receipt.selected_path in expected
        ):
            raise ContextRetentionError("hydration receipt authority contradicted")
        payload = runtime.cas_store.get_bytes(receipt.content_digest)
        if len(payload) != receipt.byte_length:
            raise ContextRetentionError("hydration payload length contradicted")
        expected[receipt.selected_path] = payload
    return expected


def _delete_checkout_tree(
    final_root: Path,
    *,
    authority_root: Path,
    expected_payloads: Mapping[str, bytes],
) -> None:
    try:
        relative_root = final_root.relative_to(authority_root)
    except ValueError as exc:
        raise ContextRetentionError("checkout is outside authority root") from exc
    if not relative_root.parts:
        raise ContextRetentionError("checkout cannot equal authority root")
    try:
        context_checkout._reject_symlink_components(final_root, stop=None)
    except ValueError as exc:
        raise ContextRetentionError("checkout path contains a symlink") from exc
    if not context_checkout._path_exists_without_following(final_root):
        return
    root_stat = final_root.lstat()
    if stat.S_ISLNK(root_stat.st_mode):
        raise ContextRetentionError("checkout root is a symlink")
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ContextRetentionError("checkout root is not a directory")

    expected_files = set(expected_payloads)
    expected_dirs = context_checkout._expected_directories(expected_files)
    files: list[tuple[Path, str]] = []
    directories: list[tuple[Path, str]] = [(final_root, "")]
    pending = [(final_root, "")]
    while pending:
        current, relative = pending.pop()
        for entry in os.scandir(current):
            path = Path(entry.path)
            nested = entry.name if not relative else f"{relative}/{entry.name}"
            entry_stat = path.lstat()
            if stat.S_ISLNK(entry_stat.st_mode):
                raise ContextRetentionError("checkout contains a symlink")
            if stat.S_ISDIR(entry_stat.st_mode):
                if nested not in expected_dirs:
                    raise ContextRetentionError("checkout contains an unexpected path")
                directories.append((path, nested))
                pending.append((path, nested))
                continue
            if not stat.S_ISREG(entry_stat.st_mode) or nested not in expected_files:
                raise ContextRetentionError("checkout contains an unexpected path")
            payload = context_checkout._read_regular_file_without_following(path)
            if payload != expected_payloads[nested]:
                raise ContextRetentionError("checkout payload contradicted authority")
            files.append((path, nested))

    try:
        for directory, _relative in sorted(
            directories,
            key=lambda item: len(item[0].parts),
        ):
            directory.chmod(0o755)
        for path, _relative in sorted(files, key=lambda item: item[1]):
            path.unlink()
        for directory, _relative in sorted(
            directories,
            key=lambda item: len(item[0].parts),
            reverse=True,
        ):
            directory.rmdir()
    except OSError as exc:
        raise ContextRetentionError("checkout removal failed") from exc


def _record_cleanup_refusal(
    runtime: OpenRuntimeContext,
    *,
    session: RunnerSessionRecord,
    error: Exception,
    event_redaction_policy: session_persistence.RedactionPolicy,
) -> None:
    reason = (
        str(error)
        if isinstance(error, ContextRetentionError)
        else "context cleanup failed"
    )
    session_persistence._record_session_event(
        runtime,
        session=session,
        kind="context_cleanup_refused",
        observed_at=time.time_ns(),
        payload={"reason": reason[:256]},
        replay_key=(
            f"context-cleanup-refused:{session.session_id}:"
            f"{session.dispatch_generation}"
        ),
        redaction_policy=event_redaction_policy,
    )


__all__ = (
    "ContextRetentionError",
    "cleanup_completed_session_context",
)
