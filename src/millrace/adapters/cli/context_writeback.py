"""Validate governed context writeback before runner-result acceptance."""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard, cast

from millrace.adapters.cli import context_checkout
from millrace.adapters.cli.context import OpenRuntimeContext
from millrace.contracts.compiled_plan import (
    ContextSourceDeclaration,
    SelectedCompiledPlan,
    StageContextBindingDeclaration,
    authority_fingerprint,
    context_binding_authority_refusal,
)
from millrace.contracts.context_checkout import (
    ContextCheckoutFile,
    ContextCheckoutLegacyManifest,
    ContextCheckoutManifest,
    decode_context_checkout_manifest,
    verify_context_checkout_manifest_digest,
)
from millrace.contracts.runner import RunnerResultEvidence
from millrace.contracts.schema import validate_schema
from millrace.contracts.selected_plan_lookups import (
    terminal_action_for,
    terminal_outcome_for,
)
from millrace.contracts.state import RunnerSessionRecord, RuntimeState
from millrace.substrate.cas import storage_digest_for_bytes

_DIRECT_WRITE = "direct_write"
_PROTECTED_PROPOSAL = "protected_proposal"
_WORKSPACE_SOURCE = "workspace_relative_root"


class ContextDiffRefusal(ValueError):
    """Raised when an active session's selected-root diff cannot be projected."""


@dataclass(frozen=True, slots=True)
class _RootBaseline:
    root: str
    files: Mapping[str, str]
    directories: frozenset[str]
    root_kind: str
    max_files: int
    max_bytes: int


@dataclass(frozen=True, slots=True)
class _LiveSnapshot:
    root: str
    files: Mapping[str, str]
    directories: frozenset[str]
    root_kind: str


def project_context_diff(
    runtime: OpenRuntimeContext,
    *,
    state: RuntimeState,
    session_id: str,
    manifest_digest: str,
) -> list[dict[str, str]]:
    """Project exact direct-write changes for one authenticated live session."""
    try:
        session = state.runner_sessions.get(session_id)
        if session is None:
            raise ContextDiffRefusal("runner session is not current state authority")
        authority = _selected_authority(state, session)
        if authority is None:
            raise ContextDiffRefusal("context diff authority is not current")
        _selected_plan, binding, fingerprint = authority
        if (
            binding is None
            or binding.mutation_policy != "reconcile_selected_writes"
            or not binding.write_rules
        ):
            raise ContextDiffRefusal("session has no selected writeback authority")
        context_checkout._validate_relation(
            session=session,
            plan_fingerprint=fingerprint,
            binding=binding,
            state=state,
            require_created=False,
        )
        if session.context_manifest_digest != manifest_digest:
            raise ContextDiffRefusal(
                "manifest digest is not the attached session authority"
            )
        manifest = _authenticate_checkout(
            runtime,
            session=session,
            binding=binding,
            plan_fingerprint=fingerprint,
        )
        if isinstance(manifest, str):
            raise ContextDiffRefusal(manifest)
        baselines = _baselines_for_binding(manifest, binding)
        if isinstance(baselines, str):
            raise ContextDiffRefusal(baselines)
        snapshots = _scan_selected_roots(runtime, baselines)
        if isinstance(snapshots, str):
            raise ContextDiffRefusal(snapshots)
        write_roots = _write_rule_roots(
            binding,
            selected_roots=tuple(baseline.root for baseline in baselines),
        )
        if isinstance(write_roots, str):
            raise ContextDiffRefusal(write_roots)
        direct_roots, _protected_roots = write_roots
        structure_refusal = _require_structure(
            baselines,
            snapshots,
            allow_file_delete_roots=direct_roots,
        )
        if structure_refusal is not None:
            raise ContextDiffRefusal(structure_refusal)
        baseline_files = _all_files(baselines)
        current_files = _all_files(snapshots)
        changed_paths = tuple(
            sorted(
                (
                    path
                    for path in set(baseline_files) | set(current_files)
                    if baseline_files.get(path) != current_files.get(path)
                ),
                key=lambda value: value.encode("utf-8"),
            )
        )
        if any(not _path_under_any(path, direct_roots) for path in changed_paths):
            raise ContextDiffRefusal(
                "protected or unselected live context path changed"
            )
        return [
            _project_file_change(
                path,
                before=baseline_files.get(path),
                after=current_files.get(path),
            )
            for path in changed_paths
        ]
    except ContextDiffRefusal:
        raise
    except Exception as exc:
        raise ContextDiffRefusal("context diff projection refused") from exc


def validate_context_writeback(
    runtime: OpenRuntimeContext,
    *,
    session: RunnerSessionRecord,
    evidence: RunnerResultEvidence | None,
) -> str | None:
    """Return a refusal detail, or ``None`` when writeback is authenticated."""
    try:
        return _validate_context_writeback(runtime, session=session, evidence=evidence)
    except Exception as exc:
        return f"context writeback validation failed: {exc}"


def _validate_context_writeback(
    runtime: OpenRuntimeContext,
    *,
    session: RunnerSessionRecord,
    evidence: RunnerResultEvidence | None,
) -> str | None:
    state = runtime.store.load_runtime_state(runtime.cas_store)
    authority = _writeback_authority(state, session=session, evidence=evidence)
    if isinstance(authority, str):
        return authority
    selected_plan, binding, fingerprint = authority
    if binding is None:
        return None
    if session.context_manifest_digest is None:
        return "bound session has no authenticated context manifest"

    manifest = _authenticate_checkout(
        runtime,
        session=session,
        binding=binding,
        plan_fingerprint=fingerprint,
    )
    if isinstance(manifest, str):
        return manifest
    baselines = _baselines_for_binding(manifest, binding)
    if isinstance(baselines, str):
        return baselines
    snapshots = _scan_selected_roots(runtime, baselines)
    if isinstance(snapshots, str):
        return snapshots

    if binding.mutation_policy == "forbid_selected_roots":
        return _require_unchanged(baselines, snapshots)
    if binding.mutation_policy == "reconcile_selected_writes":
        linkage = _selected_writeback_linkage(selected_plan, binding, evidence)
        if isinstance(linkage, str):
            return linkage
        if linkage is None:
            return _require_unchanged(baselines, snapshots)
        schema_id, artifact_payload = linkage
        schema = next(
            (
                declaration.schema
                for declaration in selected_plan.artifact_schemas
                if str(declaration.id) == schema_id
            ),
            None,
        )
        if schema is None:
            return "selected writeback artifact schema is missing"
        return _validate_writeback_report(
            artifact_payload,
            schema=cast(Mapping[str, object], schema),
            binding=binding,
            baselines=baselines,
            snapshots=snapshots,
        )
    raise AssertionError("admitted binding has unsupported mutation policy")


def _writeback_authority(
    state: RuntimeState,
    *,
    session: RunnerSessionRecord,
    evidence: RunnerResultEvidence | None,
) -> tuple[
    SelectedCompiledPlan,
    StageContextBindingDeclaration | None,
    str,
] | str:
    authority = _selected_authority(state, session)
    if authority is None:
        return "context writeback authority is not current"
    selected_plan, binding, fingerprint = authority
    if binding is None:
        return selected_plan, binding, fingerprint
    run = state.runs.get(session.run_id)
    if run is None:
        return "context writeback run authority is missing"
    if evidence is not None and not _evidence_matches_run_authority(
        evidence,
        session=session,
        run_stage_kind_id=str(run.stage_kind_id),
        run_runner_binding_id=str(run.runner_binding_id),
        plan_fingerprint=fingerprint,
    ):
        return "runner result evidence is not current run authority"
    return selected_plan, binding, fingerprint


def _selected_authority(
    state: RuntimeState,
    session: RunnerSessionRecord,
) -> tuple[
    SelectedCompiledPlan,
    StageContextBindingDeclaration | None,
    str,
] | None:
    if state.runner_sessions.get(session.session_id) != session:
        return None
    run = state.runs.get(session.run_id)
    if (
        run is None
        or run.current_session_id != session.session_id
        or run.last_dispatch_generation != session.dispatch_generation
    ):
        return None
    fingerprint = run.run_ref.plan_ref.authority_fingerprint
    admitted = state.admitted_plans.get(fingerprint)
    if admitted is None or admitted.plan_ref != run.run_ref.plan_ref:
        return None
    selected_plan = admitted.selected_plan
    try:
        if authority_fingerprint(selected_plan) != fingerprint:
            return None
    except Exception:
        return None
    if context_binding_authority_refusal(selected_plan) is not None:
        return None
    bindings = tuple(
        binding
        for binding in selected_plan.context_bindings
        if str(binding.stage_kind_id) == str(run.stage_kind_id)
    )
    if len(bindings) > 1:
        return None
    return selected_plan, (bindings[0] if bindings else None), fingerprint


def _authenticate_checkout(
    runtime: OpenRuntimeContext,
    *,
    session: RunnerSessionRecord,
    binding: StageContextBindingDeclaration,
    plan_fingerprint: str,
) -> ContextCheckoutManifest | str:
    digest = session.context_manifest_digest
    if digest is None:
        return "bound session has no context manifest digest"
    checkout_relative_path = context_checkout._safe_relative_path(
        binding.checkout_root,
        "checkout_root",
    )
    checkout_root = runtime.paths.workspace_path / checkout_relative_path
    context_checkout._reject_symlink_components(checkout_root, stop=None)
    final_root = context_checkout._final_root(checkout_root, session=session)
    if not context_checkout._path_exists_without_following(final_root):
        return "authenticated context checkout is missing"
    context_checkout._validate_materialization_target(final_root)
    manifest_bytes = runtime.cas_store.get_bytes(digest)
    verify_context_checkout_manifest_digest(manifest_bytes, digest)
    manifest = decode_context_checkout_manifest(manifest_bytes)
    if isinstance(manifest, ContextCheckoutLegacyManifest):
        return "context root baseline is unavailable for legacy manifest"
    if (
        manifest.session_id != session.session_id
        or manifest.dispatch_generation != session.dispatch_generation
        or manifest.plan_fingerprint != plan_fingerprint
        or manifest.binding_id != str(binding.id)
        or manifest.router_asset_id != str(binding.router_asset_id)
    ):
        return "authenticated context checkout manifest identity drifted"
    selections = tuple(
        context_checkout._SourceSelection(required=True, declaration=source)
        for source in binding.required_sources
    ) + tuple(
        context_checkout._SourceSelection(required=False, declaration=source)
        for source in binding.discoverable_sources
    )
    context_checkout._validate_checkout_manifest_shape(
        manifest,
        binding=binding,
        selections=selections,
    )
    payloads = context_checkout._load_existing_checkout_payloads(
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        manifest_digest=digest,
        cas_store=runtime.cas_store,
    )
    hydration_receipts = runtime.store.load_context_hydration_receipts_authenticated(
        session.session_id,
        session.dispatch_generation,
        session.session_fencing_token,
    )
    context_checkout._verify_existing_checkout(
        final_root=final_root,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        manifest_digest=digest,
        payload_by_path=payloads,
        cas_store=runtime.cas_store,
        hydration_receipts=hydration_receipts,
    )
    return manifest


def _baselines_for_binding(
    manifest: ContextCheckoutManifest,
    binding: StageContextBindingDeclaration,
) -> tuple[_RootBaseline, ...] | str:
    declarations = tuple(
        (True, source) for source in binding.required_sources
    ) + tuple((False, source) for source in binding.discoverable_sources)
    workspace_declarations = tuple(
        (required, source)
        for required, source in declarations
        if source.source_kind == _WORKSPACE_SOURCE
    )
    roots: list[_RootBaseline] = []
    seen_roots: set[str] = set()
    seen_files: set[str] = set()
    for required, declaration in workspace_declarations:
        root = context_checkout._safe_relative_path(
            declaration.source_ref,
            "workspace_source_ref",
        )
        if root in seen_roots:
            return "selected workspace source roots are duplicated"
        seen_roots.add(root)
        baseline = _baseline_for_workspace_source(
            manifest,
            required=required,
            declaration=declaration,
            root=root,
            seen_files=seen_files,
            require_root_state=(
                binding.mutation_policy == "forbid_selected_roots"
            ),
        )
        if isinstance(baseline, str):
            return baseline
        if baseline is None:
            continue
        roots.append(baseline)
    for left_index, left in enumerate(roots):
        for right in roots[left_index + 1 :]:
            if _path_is_under(left.root, right.root) or _path_is_under(
                right.root,
                left.root,
            ):
                return "selected workspace source roots overlap"
    return tuple(roots)


def _baseline_for_workspace_source(
    manifest: ContextCheckoutManifest,
    *,
    required: bool,
    declaration: ContextSourceDeclaration,
    root: str,
    seen_files: set[str],
    require_root_state: bool,
) -> _RootBaseline | str | None:
    omission = next(
        (
            item
            for item in manifest.omissions
            if item.source_kind == declaration.source_kind
            and item.source_ref == declaration.source_ref
        ),
        None,
    )
    root_states = getattr(manifest, "root_states", ())
    root_state = next(
        (
            item
            for item in root_states
            if item.source_kind == declaration.source_kind
            and item.source_ref == declaration.source_ref
        ),
        None,
    )
    if root_state is None:
        if require_root_state:
            return "context root baseline is unavailable for the selected manifest"
        if not required and omission is not None:
            return None
    if (
        not required
        and not require_root_state
        and omission is not None
        and omission.reason == "source_missing"
    ):
        return None

    files = _workspace_files_for_source(
        manifest,
        required=required,
        declaration=declaration,
        root=root,
        seen_files=seen_files,
    )
    if isinstance(files, str):
        return files

    if omission is not None and omission.reason in {
        "file_limit_exceeded",
        "byte_limit_exceeded",
    }:
        if required:
            return "required workspace source is not represented"
        return None

    if root_state is None:
        if not files and omission is not None:
            return None
        if not files:
            return _RootBaseline(
                root,
                {},
                frozenset(),
                "missing",
                declaration.max_files,
                declaration.max_bytes,
            )
        if root in files:
            return _RootBaseline(
                root,
                files,
                frozenset(),
                "file",
                declaration.max_files,
                declaration.max_bytes,
            )
        return _RootBaseline(
            root,
            files,
            frozenset(_directories_for_files(files, root)),
            "directory",
            declaration.max_files,
            declaration.max_bytes,
        )

    actual_relative_files = {
        "" if path == root else path.removeprefix(f"{root}/")
        for path in files
    }
    if actual_relative_files != set(root_state.files):
        return "context manifest root-state files are inconsistent"
    if omission is not None and omission.reason == "source_missing":
        if files or root_state.root_kind not in {"missing", "directory"}:
            return "context manifest root-state omission is inconsistent"
    elif omission is not None:
        return "context manifest root-state omission is unsupported"

    directories = frozenset(
        root if path == "" else f"{root}/{path}"
        for path in root_state.directories
    )
    if root_state.root_kind == "missing":
        if files or directories:
            return "context manifest missing root-state is inconsistent"
    elif root_state.root_kind == "file":
        if directories or set(root_state.files) != {""} or root not in files:
            return "context manifest file root-state is inconsistent"
    elif root not in directories:
        return "context manifest directory root-state is inconsistent"
    return _RootBaseline(
        root,
        files,
        directories,
        root_state.root_kind,
        declaration.max_files,
        declaration.max_bytes,
    )



def _workspace_files_for_source(
    manifest: ContextCheckoutManifest,
    *,
    required: bool,
    declaration: ContextSourceDeclaration,
    root: str,
    seen_files: set[str],
) -> dict[str, str] | str:
    prefix = f"{'required' if required else 'discoverable'}/workspace/{root}"
    files: dict[str, str] = {}
    for item in manifest.files:
        if (
            item.source_kind != _WORKSPACE_SOURCE
            or item.source_ref != declaration.source_ref
        ):
            continue
        if item.required != required:
            return "context manifest workspace source requirement drifted"
        workspace_path = _workspace_path_for_manifest_item(item, prefix, root)
        if workspace_path is None:
            return "context manifest workspace source layout drifted"
        if workspace_path in seen_files:
            return "context manifest workspace paths are duplicated"
        seen_files.add(workspace_path)
        files[workspace_path] = item.content_digest
    if not required:
        for catalog_item in manifest.catalog:
            if (
                catalog_item.source_kind != _WORKSPACE_SOURCE
                or catalog_item.source_ref != declaration.source_ref
            ):
                continue
            workspace_path = _workspace_path_for_source_path(
                catalog_item.logical_path,
                prefix,
                root,
            )
            if workspace_path is None:
                return "context manifest workspace source layout drifted"
            if workspace_path in seen_files:
                return "context manifest workspace paths are duplicated"
            seen_files.add(workspace_path)
            files[workspace_path] = catalog_item.content_digest
    return files


def _workspace_path_for_manifest_item(
    item: ContextCheckoutFile,
    prefix: str,
    root: str,
) -> str | None:
    return _workspace_path_for_source_path(item.checkout_path, prefix, root)


def _workspace_path_for_source_path(
    source_path: str,
    prefix: str,
    root: str,
) -> str | None:
    if source_path == prefix:
        return root
    if source_path.startswith(f"{prefix}/"):
        suffix = source_path.removeprefix(f"{prefix}/")
        return f"{root}/{suffix}"
    return None


def _directories_for_files(files: Mapping[str, str], root: str) -> set[str]:
    directories = {root}
    for file_path in files:
        path = Path(file_path)
        for parent in path.parents:
            parent_text = parent.as_posix()
            if parent_text != "." and _path_is_under(parent_text, root):
                directories.add(parent_text)
    return directories


class _UncomparableLiveRoot(Exception):
    pass


def _scan_selected_roots(
    runtime: OpenRuntimeContext,
    baselines: Sequence[_RootBaseline],
) -> tuple[_LiveSnapshot, ...] | str:
    snapshots: list[_LiveSnapshot] = []
    for baseline in baselines:
        root_path = runtime.paths.workspace_path / baseline.root
        try:
            context_checkout._reject_symlink_components(root_path, stop=None)
            snapshots.append(
                _scan_root(
                    root_path,
                    baseline.root,
                    max_files=baseline.max_files,
                    max_bytes=baseline.max_bytes,
                )
            )
        except Exception as exc:
            return f"live context root scan failed: {exc}"
    return tuple(snapshots)


def _scan_root(
    root_path: Path,
    root: str,
    *,
    max_files: int,
    max_bytes: int,
) -> _LiveSnapshot:
    if not context_checkout._path_exists_without_following(root_path):
        return _LiveSnapshot(root, {}, frozenset(), "missing")
    root_stat = root_path.lstat()
    if stat.S_ISLNK(root_stat.st_mode):
        raise ValueError("selected live root is a symlink")
    if stat.S_ISREG(root_stat.st_mode):
        if root_stat.st_size > max_bytes:
            raise _UncomparableLiveRoot()
        return _LiveSnapshot(
            root,
            {
                root: _digest_live_file(
                    root_path,
                    expected_identity=context_checkout._file_identity(root_stat),
                    max_bytes=max_bytes,
                )
            },
            frozenset(),
            "file",
        )
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ValueError("selected live root is a special file")

    files: dict[str, tuple[int, int, int, int, int]] = {}
    directories: set[str] = {root}
    pending: list[tuple[Path, str]] = [(root_path, root)]
    entries_seen = 0
    while pending:
        current, relative_root = pending.pop()
        try:
            entries = os.scandir(current)
        except OSError as exc:
            raise ValueError("selected live root could not be inspected") from exc
        try:
            for entry in entries:
                entries_seen += 1
                relative = f"{relative_root}/{entry.name}"
                _validate_live_path(relative)
                child = Path(entry.path)
                try:
                    child_stat = child.lstat()
                except OSError as exc:
                    raise ValueError(
                        "selected live root entry could not be inspected"
                    ) from exc
                if stat.S_ISLNK(child_stat.st_mode):
                    raise ValueError("selected live root contains a symlink")
                if stat.S_ISDIR(child_stat.st_mode):
                    if entries_seen > max_files:
                        raise _UncomparableLiveRoot()
                    directories.add(relative)
                    pending.append((child, relative))
                elif stat.S_ISREG(child_stat.st_mode):
                    if entries_seen > max_files:
                        raise _UncomparableLiveRoot()
                    files[relative] = context_checkout._file_identity(child_stat)
                else:
                    raise ValueError("selected live root contains a special file")
                if entries_seen > max_files:
                    raise _UncomparableLiveRoot()
        finally:
            entries.close()

    total_bytes = sum(identity[3] for identity in files.values())
    if total_bytes > max_bytes:
        raise _UncomparableLiveRoot()
    digests: dict[str, str] = {}
    remaining_bytes = max_bytes
    for path in sorted(files, key=lambda value: value.encode("utf-8")):
        identity = files[path]
        file_path = (
            root_path
            if path == root
            else root_path / path.removeprefix(f"{root}/")
        )
        digest = _digest_live_file(
            file_path,
            expected_identity=identity,
            max_bytes=remaining_bytes,
        )
        digests[path] = digest
        remaining_bytes -= identity[3]
    return _LiveSnapshot(root, digests, frozenset(directories), "directory")


def _digest_live_file(
    path: Path,
    *,
    expected_identity: tuple[int, int, int, int, int] | None = None,
    max_bytes: int | None = None,
) -> str:
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if no_follow == 0:
        raise ValueError("live file no-follow inspection is unsupported")
    descriptor = os.open(path, os.O_RDONLY | no_follow)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or stat.S_ISLNK(opened.st_mode):
            raise ValueError("live file is not regular")
        if expected_identity is not None and (
            context_checkout._file_identity(opened) != expected_identity
        ):
            raise ValueError("live file identity changed during comparison")
        if max_bytes is not None and opened.st_size > max_bytes:
            raise _UncomparableLiveRoot()
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            payload = stream.read() if max_bytes is None else stream.read(max_bytes)
            if max_bytes is not None and len(payload) > max_bytes:
                raise _UncomparableLiveRoot()
            return storage_digest_for_bytes(payload)
    finally:
        if descriptor >= 0:
            os.close(descriptor)

def _selected_writeback_linkage(
    selected_plan: SelectedCompiledPlan,
    binding: StageContextBindingDeclaration,
    evidence: RunnerResultEvidence | None,
) -> tuple[str, Mapping[str, object]] | None | str:
    if evidence is None:
        return None
    if evidence.stage_kind_id != str(binding.stage_kind_id):
        return "runner result stage is not selected binding authority"
    outcome = terminal_outcome_for(
        selected_plan,
        str(binding.stage_kind_id),
        evidence.marker,
    )
    if outcome is None:
        return None
    action = terminal_action_for(
        selected_plan,
        str(binding.stage_kind_id),
        str(outcome.id),
    )
    if action is None:
        return None
    if not binding.write_rules:
        return None
    if (
        binding.writeback_terminal_action_id is None
        or binding.writeback_artifact_schema_id is None
        or str(action.id) != str(binding.writeback_terminal_action_id)
        or action.artifact_schema_id is None
        or str(action.artifact_schema_id) != str(binding.writeback_artifact_schema_id)
    ):
        return None
    artifact = evidence.artifact_payload
    if artifact is None or not isinstance(artifact, Mapping):
        return "selected writeback result has no artifact payload"
    return str(binding.writeback_artifact_schema_id), artifact


def _evidence_matches_run_authority(
    evidence: RunnerResultEvidence,
    *,
    session: RunnerSessionRecord,
    run_stage_kind_id: str,
    run_runner_binding_id: str,
    plan_fingerprint: str,
) -> bool:
    return (
        evidence.run_id == session.run_id
        and evidence.session_id == session.session_id
        and evidence.dispatch_generation == session.dispatch_generation
        and evidence.session_fencing_token == session.session_fencing_token
        and evidence.plan_fingerprint == plan_fingerprint
        and evidence.stage_kind_id == run_stage_kind_id
        and evidence.runner_binding_id == run_runner_binding_id
    )


def _require_unchanged(
    baselines: Sequence[_RootBaseline],
    snapshots: Sequence[_LiveSnapshot],
) -> str | None:
    structure_refusal = _require_structure(
        baselines,
        snapshots,
        strict_structure=True,
    )
    if structure_refusal is not None:
        return structure_refusal
    baseline_files = _all_files(baselines)
    current_files = _all_files(snapshots)
    if baseline_files != current_files:
        return "selected live context files changed"
    return None


def _require_unchanged_for_write_enabled_binding(
    binding: StageContextBindingDeclaration,
    baselines: Sequence[_RootBaseline],
    snapshots: Sequence[_LiveSnapshot],
) -> str | None:
    if binding.mutation_policy == "forbid_selected_roots":
        return _require_unchanged(baselines, snapshots)
    if binding.mutation_policy == "reconcile_selected_writes":
        if not binding.write_rules:
            raise AssertionError("admitted binding has unsupported mutation policy")
        return _require_unchanged(baselines, snapshots)
    raise AssertionError("admitted binding has unsupported mutation policy")


def _require_structure(
    baselines: Sequence[_RootBaseline],
    snapshots: Sequence[_LiveSnapshot],
    *,
    allow_file_delete_roots: Sequence[str] = (),
    strict_structure: bool = False,
) -> str | None:
    for baseline, snapshot in zip(baselines, snapshots, strict=True):
        if baseline.root_kind != snapshot.root_kind:
            if (
                baseline.root_kind == "file"
                and snapshot.root_kind == "missing"
                and baseline.root in allow_file_delete_roots
            ):
                continue
            return "selected live context root changed type"
        if (
            (
                strict_structure
                and set(baseline.directories) != set(snapshot.directories)
            )
            or (
                not strict_structure
                and not set(baseline.directories) <= set(snapshot.directories)
            )
            or any(
                path in snapshot.files
                for path in baseline.directories
            )
            or any(
                path in snapshot.directories
                for path in baseline.files
            )
        ):
            return "selected live context directory set changed"
    return None


def _validate_writeback_report(
    payload: Mapping[str, object],
    *,
    schema: Mapping[str, object],
    binding: StageContextBindingDeclaration,
    baselines: Sequence[_RootBaseline],
    snapshots: Sequence[_LiveSnapshot],
) -> str | None:
    schema_result = validate_schema(schema, payload)
    if not schema_result.accepted:
        return "writeback artifact does not satisfy the selected schema"
    if set(payload) not in (
        {"changes", "proposals"},
        {"changes", "proposals", "no_op_reason"},
    ):
        return "writeback report has an invalid top-level shape"
    changes = payload.get("changes")
    proposals = payload.get("proposals")
    if not _is_sequence(changes) or not _is_sequence(proposals):
        return "writeback report arrays are invalid"

    selected_roots = tuple(baseline.root for baseline in baselines)
    write_roots = _write_rule_roots(binding, selected_roots=selected_roots)
    if isinstance(write_roots, str):
        return write_roots
    direct_roots, protected_write_roots = write_roots
    change_map = _parse_writeback_entries(
        changes,
        proposals,
        direct_roots=direct_roots,
        protected_roots=protected_write_roots,
    )
    if isinstance(change_map, str):
        return change_map

    if not changes and not proposals:
        reason = payload.get("no_op_reason")
        if not isinstance(reason, str) or not reason.strip():
            return "writeback no-op requires a nonblank reason"
    elif "no_op_reason" in payload:
        return "writeback no-op reason is only valid for a no-op report"

    return _reconcile_writeback_files(
        change_map,
        baselines,
        snapshots,
        direct_roots=direct_roots,
    )


def _parse_writeback_entries(
    changes: Sequence[object],
    proposals: Sequence[object],
    *,
    direct_roots: Sequence[str],
    protected_roots: Sequence[str],
) -> dict[str, Mapping[str, object]] | str:
    seen_paths: set[str] = set()
    change_map: dict[str, Mapping[str, object]] = {}
    for raw_entry in changes:
        entry = cast(Mapping[str, object], raw_entry)
        if not isinstance(entry, Mapping):
            return "writeback change entry is not an object"
        parsed = _validate_change_entry(
            entry,
            direct_roots=direct_roots,
            seen_paths=seen_paths,
        )
        if isinstance(parsed, str):
            return parsed
        change_map[parsed[0]] = entry

    for raw_entry in proposals:
        entry = cast(Mapping[str, object], raw_entry)
        if not isinstance(entry, Mapping):
            return "writeback proposal entry is not an object"
        proposal_result = _validate_proposal_entry(
            entry,
            protected_roots=protected_roots,
            direct_roots=direct_roots,
            seen_paths=seen_paths,
        )
        if proposal_result is not None:
            return proposal_result
    return change_map


def _reconcile_writeback_files(
    change_map: Mapping[str, Mapping[str, object]],
    baselines: Sequence[_RootBaseline],
    snapshots: Sequence[_LiveSnapshot],
    *,
    direct_roots: Sequence[str],
) -> str | None:
    file_delete_roots = tuple(
        path
        for path, entry in change_map.items()
        if entry.get("change_kind") == "delete" and path in direct_roots
    )
    structure_refusal = _require_structure(
        baselines,
        snapshots,
        allow_file_delete_roots=file_delete_roots,
    )
    if structure_refusal is not None:
        return structure_refusal
    baseline_files = _all_files(baselines)
    current_files = _all_files(snapshots)
    path_refusal = _validate_reported_file_paths(
        change_map,
        baseline_files=baseline_files,
        current_files=current_files,
        direct_roots=direct_roots,
    )
    if path_refusal is not None:
        return path_refusal
    return _validate_reported_file_digests(
        change_map,
        baseline_files=baseline_files,
        current_files=current_files,
    )


def _validate_reported_file_paths(
    change_map: Mapping[str, Mapping[str, object]],
    *,
    baseline_files: Mapping[str, str],
    current_files: Mapping[str, str],
    direct_roots: Sequence[str],
) -> str | None:
    actual_diffs = {
        path
        for path in set(baseline_files) | set(current_files)
        if baseline_files.get(path) != current_files.get(path)
    }
    for path in actual_diffs:
        if not _path_under_any(path, direct_roots):
            return "protected or unselected live context path changed"
    if set(change_map) != actual_diffs:
        return "writeback report does not account for every live file change"
    return None


def _validate_reported_file_digests(
    change_map: Mapping[str, Mapping[str, object]],
    *,
    baseline_files: Mapping[str, str],
    current_files: Mapping[str, str],
) -> str | None:
    for path, entry in change_map.items():
        before = baseline_files.get(path)
        after = current_files.get(path)
        refusal = _validate_reported_file_digest(
            entry,
            before=before,
            after=after,
        )
        if refusal is not None:
            return refusal
    return None


def _validate_reported_file_digest(
    entry: Mapping[str, object],
    *,
    before: str | None,
    after: str | None,
) -> str | None:
    kind = entry.get("change_kind")
    if kind == "create":
        if before is not None or after is None:
            return "writeback create does not match filesystem truth"
        if entry.get("after_sha256") != after:
            return "writeback create digest does not match filesystem truth"
        return None
    if kind == "modify":
        if before is None or after is None or before == after:
            return "writeback modify does not match filesystem truth"
        if (
            entry.get("before_sha256") != before
            or entry.get("after_sha256") != after
        ):
            return "writeback modify digest does not match filesystem truth"
        return None
    if kind == "delete":
        if before is None or after is not None:
            return "writeback delete does not match filesystem truth"
        if entry.get("before_sha256") != before:
            return "writeback delete digest does not match filesystem truth"
        return None
    return "writeback change kind is invalid"


def _project_file_change(
    path: str,
    *,
    before: str | None,
    after: str | None,
) -> dict[str, str]:
    if before is None and after is not None:
        return {
            "path": path,
            "change_kind": "create",
            "after_sha256": after,
        }
    if before is not None and after is None:
        return {
            "path": path,
            "change_kind": "delete",
            "before_sha256": before,
        }
    if before is not None and after is not None and before != after:
        return {
            "path": path,
            "change_kind": "modify",
            "before_sha256": before,
            "after_sha256": after,
        }
    raise AssertionError("projected context change is not a filesystem diff")


def _write_rule_roots(
    binding: StageContextBindingDeclaration,
    *,
    selected_roots: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]] | str:
    direct: list[str] = []
    protected: list[str] = []
    for rule in binding.write_rules:
        root = context_checkout._safe_relative_path(
            rule.relative_root,
            "write_rule.relative_root",
        )
        if rule.disposition == _DIRECT_WRITE:
            direct.append(root)
        elif rule.disposition == _PROTECTED_PROPOSAL:
            protected.append(root)
        else:
            return "selected write rule disposition is invalid"
        if not _path_under_any(root, selected_roots):
            return "selected write rule is outside selected workspace roots"
    all_roots = direct + protected
    if len(set(all_roots)) != len(all_roots):
        return "selected write rules are duplicated"
    for left_index, left in enumerate(all_roots):
        for right in all_roots[left_index + 1 :]:
            if _path_is_under(left, right) or _path_is_under(right, left):
                return "selected write rules overlap"
    return tuple(direct), tuple(protected)


def _validate_change_entry(
    entry: Mapping[str, object],
    *,
    direct_roots: Sequence[str],
    seen_paths: set[str],
) -> tuple[str, str] | str:
    kind = entry.get("change_kind")
    if kind not in {"create", "modify", "delete"}:
        return "writeback change kind is invalid"
    expected = {
        "create": {
            "path",
            "change_kind",
            "after_sha256",
            "evidence_refs",
            "classification",
        },
        "modify": {
            "path",
            "change_kind",
            "before_sha256",
            "after_sha256",
            "evidence_refs",
            "classification",
        },
        "delete": {
            "path",
            "change_kind",
            "before_sha256",
            "evidence_refs",
            "classification",
        },
    }[kind]
    if set(entry) != expected:
        return "writeback change entry shape is invalid"
    if entry.get("classification") != _DIRECT_WRITE:
        return "writeback change classification is invalid"
    path = _safe_report_path(entry.get("path"))
    if path is None:
        return "writeback change path is invalid"
    if path in seen_paths:
        return "writeback report paths are duplicated"
    if not _path_under_any(path, direct_roots):
        return "writeback change path is outside direct-write roots"
    seen_paths.add(path)
    for field_name in ("before_sha256", "after_sha256"):
        if field_name in entry and not _is_digest(entry[field_name]):
            return "writeback change digest is invalid"
    return path, kind


def _validate_proposal_entry(
    entry: Mapping[str, object],
    *,
    protected_roots: Sequence[str],
    direct_roots: Sequence[str],
    seen_paths: set[str],
) -> str | None:
    expected = {
        "path",
        "proposed_content",
        "proposed_content_sha256",
        "evidence_refs",
        "classification",
    }
    if set(entry) != expected:
        return "writeback proposal entry shape is invalid"
    if entry.get("classification") != _PROTECTED_PROPOSAL:
        return "writeback proposal classification is invalid"
    path = _safe_report_path(entry.get("path"))
    content = entry.get("proposed_content")
    if path is None or not isinstance(content, str):
        return "writeback proposal path or content is invalid"
    if path in seen_paths:
        return "writeback report paths are duplicated"
    if not _path_under_any(path, protected_roots) or _path_under_any(
        path,
        direct_roots,
    ):
        return "writeback proposal path is outside protected roots"
    try:
        expected_digest = storage_digest_for_bytes(content.encode("utf-8"))
    except UnicodeEncodeError:
        return "writeback proposal content is not valid UTF-8"
    if entry.get("proposed_content_sha256") != expected_digest:
        return "writeback proposal content digest does not match"
    if not _is_digest(entry.get("proposed_content_sha256")):
        return "writeback proposal digest is invalid"
    seen_paths.add(path)
    return None


def _all_files(
    snapshots: Sequence[_RootBaseline | _LiveSnapshot],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for snapshot in snapshots:
        result.update(snapshot.files)
    return result


def _safe_report_path(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        path = context_checkout._safe_relative_path(value, "report.path")
    except Exception:
        return None
    return path


def _validate_live_path(path: str) -> None:
    if _safe_report_path(path) is None:
        raise ValueError("live path is not a safe relative path")


def _is_digest(value: object) -> bool:
    if not isinstance(value, str) or len(value) != len("sha256:") + 64:
        return False
    return value.startswith("sha256:") and all(
        character in "0123456789abcdef" for character in value[7:]
    )


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _path_is_under(path: str, root: str) -> bool:
    return path == root or path.startswith(f"{root}/")


def _path_under_any(path: str, roots: Sequence[str]) -> bool:
    return any(_path_is_under(path, root) for root in roots)


__all__ = (
    "ContextDiffRefusal",
    "project_context_diff",
    "validate_context_writeback",
)
