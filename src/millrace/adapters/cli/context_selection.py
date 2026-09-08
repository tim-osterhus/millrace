"""Bounded, receipt-backed hydration for the public context selector."""

from __future__ import annotations

import fcntl
import os
import stat
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from millrace.adapters.cli import context_checkout
from millrace.adapters.cli.context import (
    CliCommandError,
    OpenRuntimeContext,
    open_runtime_context,
    require_nonblank,
)
from millrace.adapters.cli.context_writeback import (
    ContextDiffRefusal,
    project_context_diff,
)
from millrace.adapters.cli.output import CliSuccess, ExitCode, success_result
from millrace.contracts.compiled_plan import StageContextBindingDeclaration
from millrace.contracts.context_checkout import (
    ContextCheckoutCatalogEntry,
    ContextCheckoutContractError,
    ContextCheckoutLegacyManifest,
    ContextCheckoutManifest,
    decode_context_checkout_manifest,
    verify_context_checkout_manifest_digest,
)
from millrace.contracts.state import (
    ContextHydrationReceipt,
    RunnerSessionRecord,
    RuntimeState,
    context_hydration_receipt_id,
)
from millrace.substrate.cas import ContentAddressedByteStore, storage_digest_for_bytes
from millrace.substrate.errors import SubstrateError


class ContextSelectionRefusal(ValueError):
    """Raised when a context selection cannot be authenticated or materialized."""


def handle_context_command(namespace: object) -> CliSuccess:
    command = str(getattr(namespace, "command", "context"))
    if command == "context.select":
        return _select(namespace)
    if command == "context.diff":
        return _diff(namespace)
    raise CliCommandError(
        command=command,
        code="command_not_implemented",
        message="Command is not implemented.",
        exit_code=ExitCode.DOMAIN_REFUSAL,
        details={},
    )


def _diff(namespace: object) -> CliSuccess:
    command = "context.diff"
    session_id = require_nonblank(
        str(getattr(namespace, "session_id", "")),
        option="--session-id",
        command=command,
    )
    manifest_digest = require_nonblank(
        str(getattr(namespace, "manifest_digest", "")),
        option="--manifest-digest",
        command=command,
    )
    runtime = open_runtime_context(namespace, command=command)
    try:
        try:
            state = runtime.store.load_runtime_state(runtime.cas_store)
            changes = project_context_diff(
                runtime,
                state=state,
                session_id=session_id,
                manifest_digest=manifest_digest,
            )
        except (ContextDiffRefusal, SubstrateError) as exc:
            raise CliCommandError(
                command=command,
                code="context_diff_refused",
                message="Context diff was refused.",
                exit_code=ExitCode.DOMAIN_REFUSAL,
                details={},
            ) from exc
    finally:
        runtime.close()
    return success_result(
        command=command,
        code="context_diff_projected",
        message="Context diff projected.",
        data={
            "session_id": session_id,
            "manifest_digest": manifest_digest,
            "changes": changes,
        },
    )


def _select(namespace: object) -> CliSuccess:
    command = "context.select"
    session_id = require_nonblank(
        str(getattr(namespace, "session_id", "")),
        option="--session-id",
        command=command,
    )
    manifest_digest = require_nonblank(
        str(getattr(namespace, "manifest_digest", "")),
        option="--manifest-digest",
        command=command,
    )
    requested_paths = tuple(getattr(namespace, "paths", ()) or ())
    if not requested_paths:
        raise CliCommandError(
            command=command,
            code="invalid_path",
            message="At least one --path is required.",
            exit_code=ExitCode.CLI_USAGE,
            details={},
        )
    runtime = open_runtime_context(namespace, command=command)
    try:
        try:
            state = runtime.store.load_runtime_state(runtime.cas_store)
        except SubstrateError as exc:
            raise CliCommandError(
                command=command,
                code="context_selection_refused",
                message="Context selection was refused.",
                exit_code=ExitCode.DOMAIN_REFUSAL,
                details={},
            ) from exc
        try:
            selected = select_context(
                runtime=runtime,
                state=state,
                session_id=session_id,
                manifest_digest=manifest_digest,
                catalog_paths=requested_paths,
            )
        except ContextSelectionRefusal as exc:
            raise CliCommandError(
                command=command,
                code="context_selection_refused",
                message="Context selection was refused.",
                exit_code=ExitCode.DOMAIN_REFUSAL,
                details={},
            ) from exc
    finally:
        runtime.close()
    return success_result(
        command=command,
        code="context_selected",
        message="Context selected.",
        data={"selected": selected},
    )


def select_context(
    *,
    runtime: OpenRuntimeContext,
    state: RuntimeState,
    session_id: str,
    manifest_digest: str,
    catalog_paths: Sequence[str],
) -> list[dict[str, object]]:
    """Authenticate and durably materialize one bounded selection request."""
    lock_descriptor: int | None = None
    try:
        session = state.runner_sessions.get(session_id)
        if session is None:
            _refuse("runner session is not current state authority")
        run = state.runs.get(session.run_id)
        if run is None:
            _refuse("runner session run is missing")
        admitted = state.admitted_plans.get(run.run_ref.plan_ref.authority_fingerprint)
        if admitted is None:
            _refuse("runner session plan is not admitted")
        bindings = tuple(
            binding
            for binding in admitted.selected_plan.context_bindings
            if binding.stage_kind_id == run.stage_kind_id
        )
        if len(bindings) != 1:
            _refuse("runner session context binding is not unique")
        binding = bindings[0]
        context_checkout._validate_relation(
            session=session,
            plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
            binding=binding,
            state=state,
            require_created=False,
        )
        if session.context_manifest_digest != manifest_digest:
            _refuse("manifest digest is not the attached session authority")
        path_authority = context_checkout._validate_paths(
            paths=runtime.paths,
            binding=binding,
            cas_store=runtime.cas_store,
        )
        final_root = context_checkout._final_root(
            path_authority.checkout_root,
            session=session,
        )
        context_checkout._validate_materialization_target(final_root)
        if not context_checkout._path_exists_without_following(final_root):
            _refuse("attached context checkout is missing")
        lock_descriptor = _acquire_selection_lock(final_root)
        locked_state = runtime.store.load_runtime_state(runtime.cas_store)
        if locked_state.runner_sessions.get(session_id) != session:
            _refuse("runner session is not current state authority")
        context_checkout._validate_relation(
            session=session,
            plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
            binding=binding,
            state=locked_state,
            require_created=False,
        )

        manifest_bytes = _load_manifest_bytes(
            runtime.cas_store,
            manifest_digest=manifest_digest,
        )
        manifest = _decode_manifest(
            manifest_bytes,
            manifest_digest=manifest_digest,
        )
        if (
            manifest.session_id != session.session_id
            or manifest.dispatch_generation != session.dispatch_generation
            or manifest.plan_fingerprint != run.run_ref.plan_ref.authority_fingerprint
            or manifest.binding_id != str(binding.id)
            or manifest.router_asset_id != str(binding.router_asset_id)
        ):
            _refuse("context manifest authority does not match active session")
        selections = context_checkout._validate_sources(
            binding=binding,
            path_authority=path_authority,
        )
        context_checkout._validate_checkout_manifest_shape(
            manifest,
            binding=binding,
            selections=selections,
        )
        payload_by_path = context_checkout._load_existing_checkout_payloads(
            manifest=manifest,
            manifest_bytes=manifest_bytes,
            manifest_digest=manifest_digest,
            cas_store=runtime.cas_store,
        )
        prior_receipts = runtime.store.load_context_hydration_receipts_authenticated(
            session.session_id,
            session.dispatch_generation,
            session.session_fencing_token,
        )
        context_checkout._verify_existing_checkout(
            final_root=final_root,
            manifest=manifest,
            manifest_bytes=manifest_bytes,
            manifest_digest=manifest_digest,
            payload_by_path=payload_by_path,
            cas_store=runtime.cas_store,
            hydration_receipts=prior_receipts,
        )
        entries = _requested_catalog_entries(manifest, catalog_paths)
        prior_by_catalog = {
            receipt.catalog_path: receipt for receipt in prior_receipts
        }
        new_entries = tuple(
            entry for entry in entries if entry.logical_path not in prior_by_catalog
        )
        _validate_cumulative_limits(
            binding=binding,
            prior_receipts=prior_receipts,
            new_entries=new_entries,
        )
        payloads = {
            entry.logical_path: _load_catalog_payload(
                runtime.cas_store,
                entry,
            )
            for entry in new_entries
        }
        selected_paths = {
            entry.logical_path: _selected_path(entry)
            for entry in new_entries
        }
        for selected_path in selected_paths.values():
            context_checkout._safe_relative_path(
                selected_path,
                "selected checkout path",
            )
            selected_path_path = final_root / selected_path
            context_checkout._reject_symlink_components(
                selected_path_path,
                stop=None,
            )
            if context_checkout._path_exists_without_following(selected_path_path):
                _refuse("selected checkout target conflicts with existing material")

        created_files: dict[str, Path] = {}
        created_directories: list[Path] = []
        changed_modes: dict[Path, int] = {}
        persisted_catalog_paths: set[str] = set()
        try:
            _prepare_selection_directories(
                final_root=final_root,
                selected_paths=tuple(selected_paths.values()),
                changed_modes=changed_modes,
                created_directories=created_directories,
            )
            for entry in new_entries:
                selected_path = selected_paths[entry.logical_path]
                destination = final_root / selected_path
                _atomic_create_file(destination, payloads[entry.logical_path])
                created_files[entry.logical_path] = destination
            for entry in new_entries:
                receipt = _hydration_receipt(
                    session=session,
                    manifest=manifest,
                    manifest_digest=manifest_digest,
                    entry=entry,
                    selected_path=selected_paths[entry.logical_path],
                )
                stored = runtime.store.record_context_hydration_receipt(receipt)
                if stored != receipt:
                    _refuse("context hydration receipt replay conflicts")
                persisted_catalog_paths.add(entry.logical_path)
        except ContextSelectionRefusal:
            raise
        except (OSError, SubstrateError, TypeError, ValueError) as exc:
            _refuse("selected context materialization refused", exc)
        finally:
            if len(persisted_catalog_paths) != len(created_files):
                for catalog_path, path in created_files.items():
                    if catalog_path not in persisted_catalog_paths:
                        _unlink_owned_file(path)
            _restore_selection_modes(
                changed_modes=changed_modes,
                created_directories=created_directories,
            )

        all_receipts = tuple(
            [*prior_receipts]
            + [
                _hydration_receipt(
                    session=session,
                    manifest=manifest,
                    manifest_digest=manifest_digest,
                    entry=entry,
                    selected_path=selected_paths[entry.logical_path],
                )
                for entry in new_entries
            ]
        )
        context_checkout._verify_existing_checkout(
            final_root=final_root,
            manifest=manifest,
            manifest_bytes=manifest_bytes,
            manifest_digest=manifest_digest,
            payload_by_path=payload_by_path,
            cas_store=runtime.cas_store,
            hydration_receipts=all_receipts,
        )
        receipt_by_catalog = {
            receipt.catalog_path: receipt for receipt in all_receipts
        }
        return [
            {
                "catalog_path": entry.logical_path,
                "content_digest": entry.content_digest,
                "byte_length": entry.byte_length,
                "receipt_id": receipt_by_catalog[entry.logical_path].receipt_id,
            }
            for entry in entries
        ]
    except ContextSelectionRefusal:
        raise
    except (
        ContextCheckoutContractError,
        OSError,
        SubstrateError,
        TypeError,
        ValueError,
    ) as exc:
        _refuse("context selection refused", exc)
    finally:
        if lock_descriptor is not None:
            _release_selection_lock(lock_descriptor)


def _acquire_selection_lock(final_root: Path) -> int:
    lock_path = final_root.parent
    context_checkout._reject_symlink_components(lock_path, stop=None)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if no_follow == 0 or directory == 0:
        _refuse("context selection directory locking is unsupported")
    flags = os.O_RDONLY | no_follow | directory | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open(lock_path, flags)
        value = os.fstat(descriptor)
        if not stat.S_ISDIR(value.st_mode) or stat.S_ISLNK(value.st_mode):
            _refuse("context selection lock is not a directory")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except ContextSelectionRefusal:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        _refuse("context selection lock cannot be acquired", exc)
    return descriptor


def _release_selection_lock(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


def _load_manifest_bytes(
    cas_store: ContentAddressedByteStore,
    *,
    manifest_digest: str,
) -> bytes:
    try:
        manifest_bytes = cas_store.get_bytes(manifest_digest)
        verify_context_checkout_manifest_digest(manifest_bytes, manifest_digest)
    except (
        ContextCheckoutContractError,
        OSError,
        SubstrateError,
        TypeError,
        ValueError,
    ) as exc:
        _refuse("context manifest CAS material is not authentic", exc)
    if type(manifest_bytes) is not bytes:
        _refuse("context manifest CAS material is not bytes")
    return manifest_bytes


def _decode_manifest(
    manifest_bytes: bytes,
    *,
    manifest_digest: str,
) -> ContextCheckoutManifest:
    try:
        manifest = decode_context_checkout_manifest(manifest_bytes)
        verify_context_checkout_manifest_digest(manifest, manifest_digest)
    except (ContextCheckoutContractError, TypeError, ValueError) as exc:
        _refuse("context manifest is not canonical", exc)
    if isinstance(manifest, ContextCheckoutLegacyManifest):
        _refuse("context manifest is inspect-only")
    return manifest


def _requested_catalog_entries(
    manifest: ContextCheckoutManifest,
    catalog_paths: Sequence[str],
) -> tuple[ContextCheckoutCatalogEntry, ...]:
    catalog_by_path = {entry.logical_path: entry for entry in manifest.catalog}
    unique_paths: set[str] = set()
    for raw_path in catalog_paths:
        if not isinstance(raw_path, str):
            _refuse("catalog path must be a string")
        path = context_checkout._safe_relative_path(raw_path, "catalog path")
        if path in unique_paths:
            continue
        unique_paths.add(path)
    ordered_paths = sorted(unique_paths, key=lambda value: value.encode("utf-8"))
    entries: list[ContextCheckoutCatalogEntry] = []
    for path in ordered_paths:
        entry = catalog_by_path.get(path)
        if entry is None:
            _refuse("requested catalog path is not declared")
        entries.append(entry)
    return tuple(entries)


def _validate_cumulative_limits(
    *,
    binding: StageContextBindingDeclaration,
    prior_receipts: Sequence[ContextHydrationReceipt],
    new_entries: Sequence[ContextCheckoutCatalogEntry],
) -> None:
    if (
        type(binding.max_hydrated_files) is not int
        or type(binding.max_hydrated_bytes) is not int
        or binding.max_hydrated_files < 1
        or binding.max_hydrated_bytes < 1
    ):
        _refuse("context hydration limits are invalid")
    file_count = len(prior_receipts) + len(new_entries)
    byte_count = sum(receipt.byte_length for receipt in prior_receipts) + sum(
        entry.byte_length for entry in new_entries
    )
    if file_count > binding.max_hydrated_files:
        _refuse("cumulative hydrated file limit exceeded")
    if byte_count > binding.max_hydrated_bytes:
        _refuse("cumulative hydrated byte limit exceeded")


def _load_catalog_payload(
    cas_store: ContentAddressedByteStore,
    entry: ContextCheckoutCatalogEntry,
) -> bytes:
    try:
        payload = cas_store.get_bytes(entry.content_digest)
    except (OSError, SubstrateError, TypeError, ValueError) as exc:
        _refuse("catalog CAS object is not available", exc)
    if (
        type(payload) is not bytes
        or len(payload) != entry.byte_length
        or storage_digest_for_bytes(payload) != entry.content_digest
    ):
        _refuse("catalog CAS object does not match its declaration")
    return payload


def _selected_path(entry: ContextCheckoutCatalogEntry) -> str:
    return f"selected/{entry.logical_path}"


def _hydration_receipt(
    *,
    session: RunnerSessionRecord,
    manifest: ContextCheckoutManifest,
    manifest_digest: str,
    entry: ContextCheckoutCatalogEntry,
    selected_path: str,
) -> ContextHydrationReceipt:
    receipt = ContextHydrationReceipt(
        receipt_id="pending",
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        fencing_token=session.session_fencing_token,
        manifest_digest=manifest_digest,
        catalog_path=entry.logical_path,
        content_digest=entry.content_digest,
        byte_length=entry.byte_length,
        selected_path=selected_path,
    )
    return ContextHydrationReceipt(
        receipt_id=context_hydration_receipt_id(receipt),
        session_id=receipt.session_id,
        dispatch_generation=receipt.dispatch_generation,
        fencing_token=receipt.fencing_token,
        manifest_digest=receipt.manifest_digest,
        catalog_path=receipt.catalog_path,
        content_digest=receipt.content_digest,
        byte_length=receipt.byte_length,
        selected_path=receipt.selected_path,
    )


def _prepare_selection_directories(
    *,
    final_root: Path,
    selected_paths: Sequence[str],
    changed_modes: dict[Path, int],
    created_directories: list[Path],
) -> None:
    for selected_path in selected_paths:
        parent = (final_root / selected_path).parent
        relative_parts = parent.relative_to(final_root).parts
        current = final_root
        for part in (None, *relative_parts):
            if part is not None:
                current /= part
            context_checkout._reject_symlink_components(current, stop=None)
            try:
                current_stat = current.lstat()
            except FileNotFoundError:
                try:
                    current.mkdir(mode=0o755)
                except OSError as exc:
                    _refuse("selected checkout directory cannot be created", exc)
                created_directories.append(current)
                continue
            except OSError as exc:
                _refuse("selected checkout directory cannot be inspected", exc)
            if not stat.S_ISDIR(current_stat.st_mode):
                _refuse("selected checkout parent is not a directory")
            if current not in changed_modes:
                changed_modes[current] = current_stat.st_mode & 0o777
            try:
                current.chmod(0o755)
            except OSError as exc:
                _refuse("selected checkout directory cannot be opened", exc)


def _atomic_create_file(destination: Path, payload: bytes) -> None:
    parent = destination.parent
    context_checkout._reject_symlink_components(destination, stop=None)
    if context_checkout._path_exists_without_following(destination):
        _refuse("selected checkout target conflicts with existing material")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o444)
        try:
            os.link(temporary, destination)
        except FileExistsError as exc:
            _refuse("selected checkout target conflicts with existing material", exc)
        try:
            directory_descriptor = os.open(parent, os.O_RDONLY)
        except OSError:
            pass
        else:
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    except ContextSelectionRefusal:
        raise
    except (OSError, TypeError, ValueError) as exc:
        _refuse("selected checkout file cannot be created", exc)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _restore_selection_modes(
    *,
    changed_modes: dict[Path, int],
    created_directories: Sequence[Path],
) -> None:
    for path in sorted(
        (*created_directories, *changed_modes),
        key=lambda value: len(value.parts),
        reverse=True,
    ):
        mode = 0o555 if path in created_directories else changed_modes[path]
        try:
            path.chmod(mode)
        except OSError as exc:
            _refuse("selected checkout cannot be made read-only", exc)


def _unlink_owned_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _refuse(message: str, cause: BaseException | None = None) -> NoReturn:
    if cause is None:
        raise ContextSelectionRefusal(message)
    raise ContextSelectionRefusal(message) from cause


__all__ = ("ContextSelectionRefusal", "handle_context_command", "select_context")
