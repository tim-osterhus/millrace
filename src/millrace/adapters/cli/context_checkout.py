"""CLI-local capture and materialization of an immutable context checkout."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import shutil
import stat
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, TypeAlias
from unicodedata import normalize

from millrace.adapters.cli.context import CliWorkspacePaths
from millrace.contracts.compiled_plan import (
    ContextSourceDeclaration,
    SelectedCompiledPlan,
    StageContextBindingDeclaration,
    TerminalActionDeclaration,
    authority_fingerprint,
    canonical_authority_bytes,
    context_binding_authority_refusal,
)
from millrace.contracts.context_checkout import (
    ContextCheckoutContractError,
    ContextCheckoutFile,
    ContextCheckoutManifest,
    ContextCheckoutOmission,
    context_checkout_manifest_digest,
    decode_context_checkout_manifest,
    encode_context_checkout_manifest,
    verify_context_checkout_manifest_digest,
)
from millrace.contracts.state import (
    Activation,
    AdmittedPlan,
    ArtifactRecord,
    GovernanceEventRecord,
    RecoveryAttemptRecord,
    RunnerSessionRecord,
    RunRecord,
    RuntimeState,
    TraceRecord,
    TransitionRecord,
    WorkItem,
)
from millrace.contracts.transition import (
    RecordArtifact,
    RunnerResultObserved,
    TransitionContext,
    input_payload_digest,
)
from millrace.kernel import decide
from millrace.operator.dispatch import build_dispatch_envelope_for_run
from millrace.substrate.cas import (
    ContentAddressedByteStore,
    storage_digest_for_bytes,
)

if TYPE_CHECKING:
    from millrace.contracts.runner import RunnerDispatchEnvelope


class ContextCheckoutPreparationError(ValueError):
    """Raised when context checkout preparation is unsafe or inconsistent."""


@dataclass(frozen=True, slots=True)
class PreparedContextCheckout:
    manifest: ContextCheckoutManifest
    manifest_digest: str
    materialized_checkout_root: Path

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, ContextCheckoutManifest):
            _refuse("manifest must be a ContextCheckoutManifest")
        if not isinstance(self.materialized_checkout_root, Path):
            _refuse("materialized_checkout_root must be a Path")
        try:
            verify_context_checkout_manifest_digest(
                self.manifest,
                self.manifest_digest,
            )
        except ContextCheckoutContractError as exc:
            _refuse("manifest_digest does not authenticate manifest", exc)


JSONValue: TypeAlias = (
    None
    | bool
    | int
    | str
    | list["JSONValue"]
    | dict[str, "JSONValue"]
)


class _CaptureInstability(Exception):
    pass


@dataclass(frozen=True, slots=True)
class _SourceSelection:
    required: bool
    declaration: ContextSourceDeclaration


@dataclass(frozen=True, slots=True)
class _CapturedFile:
    checkout_path: str
    source_kind: str
    source_ref: str
    required: bool
    payload: bytes


@dataclass(frozen=True, slots=True)
class _AuthenticatedArtifactSource:
    run: RunRecord
    work_item: WorkItem
    activation: Activation
    selected_plan: SelectedCompiledPlan


@dataclass(frozen=True, slots=True)
class _CaptureResult:
    files: tuple[_CapturedFile, ...]
    omission: ContextCheckoutOmission | None


@dataclass(frozen=True, slots=True)
class _Relation:
    state: RuntimeState
    run: RunRecord
    work_item: WorkItem
    activation: Activation
    admitted: AdmittedPlan
    selected_plan: SelectedCompiledPlan
    envelope: RunnerDispatchEnvelope
    router_body: str


@dataclass(frozen=True, slots=True)
class _PathAuthority:
    workspace: Path
    db_path: Path
    cas_root: Path
    checkout_root: Path


@dataclass(frozen=True, slots=True)
class _Attempt:
    relation_snapshot: _RelationSnapshot
    captures: tuple[_CaptureResult, ...]
    runtime_files: tuple[_CapturedFile, ...]
    runtime_omissions: tuple[ContextCheckoutOmission, ...]
    files: tuple[_CapturedFile, ...]
    omissions: tuple[ContextCheckoutOmission, ...]
    manifest: ContextCheckoutManifest
    manifest_bytes: bytes
    manifest_digest: str
    payload_by_path: Mapping[str, bytes]


@dataclass(frozen=True, slots=True)
class _RelationSnapshot:
    selected_plan_fingerprint: str
    binding_fingerprint: str
    router_body: str
    envelope_bytes: bytes
    run_identity: tuple[object, ...]
    activation_identity: tuple[object, ...]
    work_item_identity: tuple[object, ...]


def _refuse(message: str, cause: BaseException | None = None) -> NoReturn:
    if cause is None:
        raise ContextCheckoutPreparationError(message)
    raise ContextCheckoutPreparationError(message) from cause


def prepare_context_checkout(
    *,
    paths: CliWorkspacePaths,
    session: RunnerSessionRecord,
    plan_fingerprint: str,
    binding: StageContextBindingDeclaration,
    state: RuntimeState,
    cas_store: ContentAddressedByteStore,
    reuse_existing: bool = True,
) -> PreparedContextCheckout:
    """Capture and atomically publish one canonical runner context checkout."""
    try:
        _validate_relation(
            session=session,
            plan_fingerprint=plan_fingerprint,
            binding=binding,
            state=state,
        )
        path_authority = _validate_paths(
            paths=paths,
            binding=binding,
            cas_store=cas_store,
        )
        selections = _validate_sources(
            binding=binding,
            path_authority=path_authority,
        )
        final_root = _final_root(path_authority.checkout_root, session=session)
        _validate_materialization_target(final_root)
        if _path_exists_without_following(final_root):
            if reuse_existing:
                return _reuse_existing_checkout(
                    final_root=final_root,
                    session=session,
                    plan_fingerprint=plan_fingerprint,
                    binding=binding,
                    selections=selections,
                    cas_store=cas_store,
                )
            _discard_unattached_checkout(
                final_root=final_root,
                session=session,
                plan_fingerprint=plan_fingerprint,
                binding=binding,
                selections=selections,
                cas_store=cas_store,
            )

        attempt_result: _Attempt | None = None
        for attempt in range(2):
            try:
                relation = _validate_relation(
                    session=session,
                    plan_fingerprint=plan_fingerprint,
                    binding=binding,
                    state=state,
                )
                captures = _capture_workspace_sources(
                    selections=selections,
                    workspace=path_authority.workspace,
                )
                runtime_files, runtime_omissions = _runtime_files(
                    selections=selections,
                    relation=relation,
                )
                captured_files = tuple(
                    file_record
                    for capture in captures
                    for file_record in capture.files
                ) + tuple(runtime_files)
                capture_omissions = tuple(
                    capture.omission
                    for capture in captures
                    if capture.omission is not None
                )
                omissions = tuple(
                    sorted(
                        capture_omissions + tuple(runtime_omissions),
                        key=lambda item: (
                            item.source_kind.encode("utf-8"),
                            item.source_ref.encode("utf-8"),
                            item.reason.encode("utf-8"),
                        ),
                    )
                )
                index = _render_context_index(
                    session=session,
                    plan_fingerprint=plan_fingerprint,
                    binding=binding,
                    selections=selections,
                    omissions=omissions,
                    payload_files=captured_files,
                )
                files = captured_files + (
                    _CapturedFile(
                        checkout_path="CONTEXT.md",
                        source_kind="selected_router",
                        source_ref=str(binding.router_asset_id),
                        required=True,
                        payload=(relation.router_body + "\n\n" + index).encode(
                            "utf-8"
                        ),
                    ),
                )
                _validate_captured_paths(files)
                manifest = _manifest_for_files(
                    session=session,
                    plan_fingerprint=plan_fingerprint,
                    binding=binding,
                    files=files,
                    omissions=omissions,
                )
                manifest_bytes = encode_context_checkout_manifest(manifest)
                manifest_digest = context_checkout_manifest_digest(manifest_bytes)
                final_root = _final_root(
                    path_authority.checkout_root,
                    session=session,
                )
                _validate_materialization_target(final_root)
                payload_by_path = {
                    file_record.checkout_path: file_record.payload
                    for file_record in files
                }
                _validate_manifest_payloads(manifest, payload_by_path)
                attempt_result = _Attempt(
                    relation_snapshot=_relation_snapshot(relation, binding),
                    captures=captures,
                    runtime_files=tuple(runtime_files),
                    runtime_omissions=tuple(runtime_omissions),
                    files=files,
                    omissions=omissions,
                    manifest=manifest,
                    manifest_bytes=manifest_bytes,
                    manifest_digest=manifest_digest,
                    payload_by_path=payload_by_path,
                )
                _validate_attempt_stability(
                    attempt=attempt_result,
                    selections=selections,
                    workspace=path_authority.workspace,
                    session=session,
                    plan_fingerprint=plan_fingerprint,
                    binding=binding,
                    state=state,
                )
                break
            except _CaptureInstability as exc:
                if attempt == 1:
                    _refuse("selected context changed during capture", exc)
                attempt_result = None
        if attempt_result is None:
            _refuse("selected context capture did not complete")

        prepared = PreparedContextCheckout(
            manifest=attempt_result.manifest,
            manifest_digest=attempt_result.manifest_digest,
            materialized_checkout_root=final_root,
        )
        _put_cas_bytes(
            cas_store=cas_store,
            payloads=attempt_result.files,
            manifest_bytes=attempt_result.manifest_bytes,
            manifest_digest=attempt_result.manifest_digest,
        )
        _publish_checkout(
            final_root=final_root,
            manifest=attempt_result.manifest,
            manifest_bytes=attempt_result.manifest_bytes,
            payload_by_path=attempt_result.payload_by_path,
            cas_store=cas_store,
            manifest_digest=attempt_result.manifest_digest,
        )
        return prepared
    except ContextCheckoutPreparationError:
        raise
    except Exception as exc:
        _refuse("context checkout preparation refused", exc)


def rematerialize_attached_context_checkout(
    *,
    paths: CliWorkspacePaths,
    session: RunnerSessionRecord,
    plan_fingerprint: str,
    binding: StageContextBindingDeclaration,
    manifest_digest: str,
    state: RuntimeState,
    cas_store: ContentAddressedByteStore,
) -> PreparedContextCheckout:
    """Verify or materialize an already-attached checkout from its CAS manifest."""
    try:
        _validate_relation(
            session=session,
            plan_fingerprint=plan_fingerprint,
            binding=binding,
            state=state,
            require_created=False,
        )
        if not isinstance(session, RunnerSessionRecord):
            _refuse("session must be a RunnerSessionRecord")
        _require_identity(session.session_id, "session_id")
        _require_identity(plan_fingerprint, "plan_fingerprint")
        if not isinstance(manifest_digest, str):
            _refuse("manifest_digest must be a string")
        if session.context_manifest_digest != manifest_digest:
            _refuse("manifest digest is not the attached session authority")
        path_authority = _validate_paths(
            paths=paths,
            binding=binding,
            cas_store=cas_store,
        )
        selections = _validate_sources(
            binding=binding,
            path_authority=path_authority,
        )
        final_root = _final_root(path_authority.checkout_root, session=session)
        _validate_materialization_target(final_root)
        try:
            manifest_bytes = cas_store.get_bytes(manifest_digest)
            manifest = decode_context_checkout_manifest(manifest_bytes)
            verify_context_checkout_manifest_digest(manifest_bytes, manifest_digest)
        except ContextCheckoutPreparationError:
            raise
        except Exception as exc:
            _refuse("attached context manifest CAS material is not authentic", exc)
        if (
            manifest.session_id != session.session_id
            or manifest.dispatch_generation != session.dispatch_generation
            or manifest.plan_fingerprint != plan_fingerprint
            or manifest.binding_id != str(binding.id)
            or manifest.router_asset_id != str(binding.router_asset_id)
        ):
            _refuse("attached context manifest authority does not match")
        _validate_checkout_manifest_shape(
            manifest,
            binding=binding,
            selections=selections,
        )
        payload_by_path = _load_existing_checkout_payloads(
            manifest=manifest,
            manifest_bytes=manifest_bytes,
            manifest_digest=manifest_digest,
            cas_store=cas_store,
        )
        if _path_exists_without_following(final_root):
            _verify_existing_checkout(
                final_root=final_root,
                manifest=manifest,
                manifest_bytes=manifest_bytes,
                manifest_digest=manifest_digest,
                payload_by_path=payload_by_path,
                cas_store=cas_store,
            )
        else:
            _publish_checkout(
                final_root=final_root,
                manifest=manifest,
                manifest_bytes=manifest_bytes,
                payload_by_path=payload_by_path,
                cas_store=cas_store,
                manifest_digest=manifest_digest,
            )
        return PreparedContextCheckout(
            manifest=manifest,
            manifest_digest=manifest_digest,
            materialized_checkout_root=final_root,
        )
    except ContextCheckoutPreparationError:
        raise
    except Exception as exc:
        _refuse("attached context checkout rematerialization refused", exc)


def _validate_relation(
    *,
    session: RunnerSessionRecord,
    plan_fingerprint: str,
    binding: StageContextBindingDeclaration,
    state: RuntimeState,
    require_created: bool = True,
) -> _Relation:
    if not isinstance(session, RunnerSessionRecord):
        _refuse("session must be a RunnerSessionRecord")
    if not isinstance(state, RuntimeState):
        _refuse("state must be a RuntimeState")
    if not isinstance(binding, StageContextBindingDeclaration):
        _refuse("binding must be a StageContextBindingDeclaration")
    _require_identity(session.session_id, "session_id")
    _require_identity(session.run_id, "run_id")
    if (
        type(session.dispatch_generation) is not int
        or session.dispatch_generation < 1
    ):
        _refuse("session dispatch generation must be positive")
    _require_identity(plan_fingerprint, "plan_fingerprint")
    if require_created:
        if session.state != "created":
            _refuse("runner session must be in created state")
    elif session.state not in {
        "created",
        "starting",
        "running",
        "cancellation_requested",
        "terminating",
    }:
        _refuse("runner session is not an attached live session")
    stored_session = state.runner_sessions.get(session.session_id)
    if stored_session != session:
        _refuse("supplied runner session is not current state authority")
    run = state.runs.get(session.run_id)
    if run is None:
        _refuse("runner session run is missing")
    if run.run_ref.run_id != session.run_id:
        _refuse("runner session run identity does not match run reference")
    if run.current_session_id != session.session_id:
        _refuse("run current session identity does not match")
    if run.last_dispatch_generation != session.dispatch_generation:
        _refuse("run dispatch generation does not match session")
    if run.run_ref.plan_ref.authority_fingerprint != plan_fingerprint:
        _refuse("supplied plan fingerprint does not match run")
    admitted = state.admitted_plans.get(plan_fingerprint)
    if admitted is None or admitted.plan_ref != run.run_ref.plan_ref:
        _refuse("run plan is not the admitted selected plan")
    selected_plan = admitted.selected_plan
    try:
        selected_plan_fingerprint = authority_fingerprint(selected_plan)
    except Exception as exc:
        _refuse("selected plan authority is not canonical", exc)
    if selected_plan_fingerprint != plan_fingerprint:
        _refuse("selected plan authority fingerprint does not match run")
    try:
        binding_refusal = context_binding_authority_refusal(selected_plan)
    except Exception as exc:
        _refuse("selected context binding authority is malformed", exc)
    if binding_refusal is not None:
        _refuse(f"selected context binding authority refused: {binding_refusal}")
    matching_bindings = tuple(
        candidate
        for candidate in selected_plan.context_bindings
        if candidate.id == binding.id
    )
    if len(matching_bindings) != 1 or matching_bindings[0] != binding:
        _refuse("supplied context binding is not selected plan authority")
    if binding.stage_kind_id != run.stage_kind_id:
        _refuse("context binding stage does not match run")
    stage_bindings = tuple(
        candidate
        for candidate in selected_plan.context_bindings
        if candidate.stage_kind_id == run.stage_kind_id
    )
    if len(stage_bindings) != 1 or stage_bindings[0] != binding:
        _refuse("supplied context binding is not unique for run stage")
    selected_stage = tuple(
        stage for stage in selected_plan.stage_kinds if stage.id == run.stage_kind_id
    )
    if len(selected_stage) != 1 or (
        selected_stage[0].runner_binding_id != run.runner_binding_id
    ):
        _refuse("run runner binding does not match selected stage authority")

    activation = state.activations.get(run.activation_id)
    work_item = state.work_items.get(run.work_item_id)
    if activation is None or work_item is None:
        _refuse("run activation or work item is missing")
    if run.work_item_id in state.closed_work_items:
        _refuse("run work item is closed")
    if _run_links_are_drifted(
        run=run,
        activation=activation,
        work_item=work_item,
        plan_ref=run.run_ref.plan_ref,
    ):
        _refuse("run, activation, and work item identities have drifted")

    try:
        envelope = _dispatch_envelope_for_relation(
            state=state,
            session=session,
        )
    except Exception as exc:
        _refuse("dispatch relation authority refused", exc)
    router_assets = tuple(
        asset for asset in selected_plan.assets if asset.id == binding.router_asset_id
    )
    if len(router_assets) != 1:
        _refuse("context router asset must resolve exactly once")
    router = router_assets[0]
    if router.asset_kind != "template" or not isinstance(router.body, str):
        _refuse("context router asset is not a UTF-8 template")
    try:
        router.body.encode("utf-8")
    except UnicodeEncodeError as exc:
        _refuse("context router asset is not valid UTF-8", exc)
    if "\x00" in router.body:
        _refuse("context router asset contains NUL")
    return _Relation(
        state=state,
        run=run,
        work_item=work_item,
        activation=activation,
        admitted=admitted,
        selected_plan=selected_plan,
        envelope=envelope,
        router_body=router.body,
    )


def _dispatch_envelope_for_relation(
    *,
    state: RuntimeState,
    session: RunnerSessionRecord,
) -> RunnerDispatchEnvelope:
    if session.context_manifest_digest is None:
        if session.state != "created":
            _refuse("attached live session has no context manifest")
        transient_digest = "sha256:" + "0" * 64
        transient_session = replace(
            session,
            context_manifest_digest=transient_digest,
        )
        transient_state = replace(
            state,
            runner_sessions={
                **state.runner_sessions,
                session.session_id: transient_session,
            },
        )
        try:
            transient_envelope = build_dispatch_envelope_for_run(
                state=transient_state,
                run_id=session.run_id,
            )
        except Exception as exc:
            _refuse("dispatch relation authority refused", exc)
        return replace(transient_envelope, context_checkout=None)
    try:
        return build_dispatch_envelope_for_run(
            state=state,
            run_id=session.run_id,
        )
    except Exception as exc:
        _refuse("dispatch relation authority refused", exc)


def _run_links_are_drifted(
    *,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
    plan_ref: object,
) -> bool:
    return (
        run.run_ref.plan_ref != plan_ref
        or activation.plan_ref != run.run_ref.plan_ref
        or work_item.ref.plan_ref != run.run_ref.plan_ref
        or run.run_ref.work_item_id != run.work_item_id
        or run.run_ref.work_item_id != work_item.ref.work_item_id
        or activation.activation_id != run.activation_id
        or activation.work_item_id != work_item.ref.work_item_id
        or activation.claimed_by_run_id != run.run_ref.run_id
        or run.run_ref.generation != work_item.ref.generation
        or activation.generation != run.run_ref.generation + 1
        or activation.lineage_id != work_item.lineage_id
        or activation.queue_family_id != work_item.queue_family_id
        or activation.stage_kind_id != run.stage_kind_id
        or activation.runner_binding_id != run.runner_binding_id
    )


def _relation_snapshot(
    relation: _Relation,
    binding: StageContextBindingDeclaration,
) -> _RelationSnapshot:
    run = relation.run
    activation = relation.activation
    work_item = relation.work_item
    return _RelationSnapshot(
        selected_plan_fingerprint=authority_fingerprint(relation.selected_plan),
        binding_fingerprint=authority_fingerprint(binding),
        router_body=relation.router_body,
        envelope_bytes=canonical_authority_bytes(relation.envelope.payload()),
        run_identity=(
            run.run_ref.run_id,
            run.run_ref.work_item_id,
            run.run_ref.claim_id,
            run.run_ref.plan_ref.plan_id,
            run.run_ref.plan_ref.authority_fingerprint,
            run.run_ref.plan_ref.plan_format_version,
            run.run_ref.generation,
            run.run_ref.fencing_token,
            run.work_item_id,
            run.activation_id,
            str(run.stage_kind_id),
            str(run.runner_binding_id),
            run.current_session_id,
            run.last_dispatch_generation,
        ),
        activation_identity=(
            activation.activation_id,
            activation.work_item_id,
            activation.lineage_id,
            activation.plan_ref.plan_id,
            activation.plan_ref.authority_fingerprint,
            activation.plan_ref.plan_format_version,
            str(activation.queue_family_id),
            activation.graph_node_id,
            str(activation.stage_kind_id),
            str(activation.runner_binding_id),
            activation.generation,
            activation.claimed_by_run_id,
        ),
        work_item_identity=(
            work_item.ref.work_item_id,
            work_item.ref.plan_ref.plan_id,
            work_item.ref.plan_ref.authority_fingerprint,
            work_item.ref.plan_ref.plan_format_version,
            work_item.ref.generation,
            str(work_item.queue_family_id),
            canonical_authority_bytes(work_item.payload),
            work_item.lineage_id,
        ),
    )


def _validate_attempt_stability(
    *,
    attempt: _Attempt,
    selections: Sequence[_SourceSelection],
    workspace: Path,
    session: RunnerSessionRecord,
    plan_fingerprint: str,
    binding: StageContextBindingDeclaration,
    state: RuntimeState,
) -> None:
    second_relation = _validate_relation(
        session=session,
        plan_fingerprint=plan_fingerprint,
        binding=binding,
        state=state,
    )
    if (
        attempt.relation_snapshot
        != _relation_snapshot(second_relation, binding)
        or attempt.manifest.session_id != session.session_id
        or attempt.manifest.dispatch_generation != session.dispatch_generation
    ):
        raise _CaptureInstability("selected runtime authority changed")

    second_captures = _capture_workspace_sources(
        selections=selections,
        workspace=workspace,
    )
    if second_captures != attempt.captures:
        raise _CaptureInstability("workspace material changed during validation")

    second_runtime_files, second_runtime_omissions = _runtime_files(
        selections=selections,
        relation=second_relation,
    )
    if (
        tuple(second_runtime_files) != attempt.runtime_files
        or tuple(second_runtime_omissions) != attempt.runtime_omissions
    ):
        raise _CaptureInstability("runtime projection changed during validation")


def _validate_paths(
    *,
    paths: CliWorkspacePaths,
    binding: StageContextBindingDeclaration,
    cas_store: ContentAddressedByteStore,
) -> _PathAuthority:
    if not isinstance(paths, CliWorkspacePaths):
        _refuse("paths must be CliWorkspacePaths")
    path_values = (
        paths.workspace_path,
        paths.db_path,
        paths.cas_path,
    )
    if any(
        not isinstance(value, Path) or not value.is_absolute()
        for value in path_values
    ):
        _refuse("workspace paths must be absolute Path values")
    workspace = _resolved_path(paths.workspace_path, "workspace")
    _reject_symlink_components(paths.workspace_path, stop=None)
    if not workspace.is_dir():
        _refuse("workspace is not an initialized directory")

    db_path = _resolved_path(paths.db_path, "database")
    cas_root = _resolved_path(paths.cas_path, "CAS root")
    _reject_symlink_components(paths.db_path, stop=None)
    _reject_symlink_components(paths.cas_path, stop=None)
    if _path_exists_without_following(paths.db_path):
        if not paths.db_path.is_file():
            _refuse("database path is not a regular file")
    elif not paths.db_path.parent.is_dir():
        _refuse("database parent directory is missing")
    if not cas_root.is_dir():
        _refuse("CAS root is not an initialized directory")

    store_root = getattr(cas_store, "_root", None)
    if (
        not isinstance(store_root, Path)
        or _resolved_path(store_root, "CAS store") != cas_root
    ):
        _refuse("CAS store root does not match supplied paths")
    if isinstance(store_root, Path):
        _reject_symlink_components(store_root, stop=None)

    checkout_relative = _safe_relative_path(binding.checkout_root, "checkout_root")
    checkout_root = workspace / checkout_relative
    _reject_symlink_components(checkout_root, stop=workspace)
    for protected in (db_path, cas_root):
        if _paths_overlap(checkout_root, protected):
            _refuse("checkout root overlaps a protected runtime path")
    return _PathAuthority(
        workspace=workspace,
        db_path=db_path,
        cas_root=cas_root,
        checkout_root=checkout_root,
    )


def _validate_sources(
    *,
    binding: StageContextBindingDeclaration,
    path_authority: _PathAuthority,
) -> tuple[_SourceSelection, ...]:
    selections = tuple(
        _SourceSelection(required=True, declaration=source)
        for source in binding.required_sources
    ) + tuple(
        _SourceSelection(required=False, declaration=source)
        for source in binding.discoverable_sources
    )
    seen: set[tuple[str, str]] = set()
    workspace_roots: list[Path] = []
    for selection in selections:
        source = selection.declaration
        if not isinstance(source, ContextSourceDeclaration):
            _refuse("context source declaration is malformed")
        source_kind = _require_identity(source.source_kind, "source_kind")
        source_ref = _require_identity(source.source_ref, "source_ref")
        if type(source.max_files) is not int or source.max_files < 1:
            _refuse("context source max_files must be positive")
        if type(source.max_bytes) is not int or source.max_bytes < 1:
            _refuse("context source max_bytes must be positive")
        key = (source_kind, source_ref)
        if key in seen:
            _refuse("context sources must be unique")
        seen.add(key)
        if source_kind not in {
            "dispatch_material",
            "accepted_lineage_artifacts",
            "lineage_attempt_history",
            "workspace_relative_root",
        }:
            _refuse("unsupported context source kind")
        if source_kind != "workspace_relative_root":
            expected_ref = (
                "current"
                if source_kind == "dispatch_material"
                else "current_lineage"
            )
            if source_ref != expected_ref:
                _refuse("runtime context source reference is unsupported")
            continue
        relative = _safe_relative_path(source_ref, "workspace source_ref")
        root = path_authority.workspace / relative
        _reject_symlink_components(root, stop=path_authority.workspace)
        for protected in (
            path_authority.db_path,
            path_authority.cas_root,
        ):
            if _paths_overlap(root, protected):
                _refuse("workspace source overlaps a protected runtime path")
        workspace_roots.append(root)

    for left_index, left in enumerate(workspace_roots):
        for right in workspace_roots[left_index + 1 :]:
            if _paths_overlap(left, right):
                _refuse("workspace source roots overlap")
        if _paths_overlap(left, path_authority.checkout_root):
            _refuse("workspace source overlaps checkout root")

    for rule in binding.write_rules:
        if not hasattr(rule, "relative_root") or not hasattr(rule, "disposition"):
            _refuse("context write rule is malformed")
        relative = _safe_relative_path(rule.relative_root, "write relative_root")
        if rule.disposition not in {"direct_write", "protected_proposal"}:
            _refuse("unsupported context write disposition")
        root = path_authority.workspace / relative
        _reject_symlink_components(root, stop=path_authority.workspace)
        if _paths_overlap(root, path_authority.db_path) or _paths_overlap(
            root, path_authority.cas_root
        ):
            _refuse("write rule overlaps a protected runtime path")
    return tuple(
        sorted(
            selections,
            key=lambda item: (
                0 if item.required else 1,
                item.declaration.source_kind.encode("utf-8"),
                item.declaration.source_ref.encode("utf-8"),
            ),
        )
    )


def _capture_workspace_sources(
    *,
    selections: Sequence[_SourceSelection],
    workspace: Path,
) -> tuple[_CaptureResult, ...]:
    results: list[_CaptureResult] = []
    for selection in selections:
        source = selection.declaration
        if source.source_kind != "workspace_relative_root":
            results.append(_CaptureResult(files=(), omission=None))
            continue
        source_path = workspace / source.source_ref
        results.append(
            _capture_workspace_source(
                selection=selection,
                source_path=source_path,
            )
        )
    return tuple(results)


def _capture_workspace_source(
    *,
    selection: _SourceSelection,
    source_path: Path,
) -> _CaptureResult:
    source = selection.declaration
    before = _snapshot_tree(source_path)
    if before is None:
        after = _snapshot_tree(source_path)
        if before != after:
            raise _CaptureInstability("source appeared while being captured")
        if selection.required:
            _refuse("required workspace source is missing")
        return _CaptureResult(
            files=(),
            omission=ContextCheckoutOmission(
                source_kind=source.source_kind,
                source_ref=source.source_ref,
                reason="source_missing",
            ),
        )
    payloads: list[_CapturedFile] = []
    file_paths = sorted(
        (
            relative
            for relative, identity in before.items()
            if stat.S_ISREG(identity[0])
        ),
        key=lambda value: value.encode("utf-8"),
    )
    directory_root = stat.S_ISDIR(before[""][0])
    for relative in file_paths:
        path = source_path if relative == "" else source_path / relative
        payload = _read_regular_file(path, before[relative])
        _validate_utf8_text(payload)
        checkout_path = (
            f"{'required' if selection.required else 'discoverable'}/workspace/"
            f"{source.source_ref}"
            if not directory_root
            else f"{'required' if selection.required else 'discoverable'}/workspace/"
            f"{source.source_ref}/{relative}"
        )
        payloads.append(
            _CapturedFile(
                checkout_path=checkout_path,
                source_kind=source.source_kind,
                source_ref=source.source_ref,
                required=selection.required,
                payload=payload,
            )
        )
    after = _snapshot_tree(source_path)
    if before != after:
        raise _CaptureInstability("workspace changed while being captured")
    for relative, captured in zip(file_paths, payloads):
        if after is None or relative not in after:
            raise _CaptureInstability("workspace file disappeared after capture")
        path = source_path if relative == "" else source_path / relative
        verified_payload = _read_regular_file(path, after[relative])
        if verified_payload != captured.payload:
            raise _CaptureInstability("workspace content changed during capture")
    if not payloads:
        if selection.required:
            _refuse("required workspace source is empty and cannot be represented")
        return _CaptureResult(
            files=(),
            omission=ContextCheckoutOmission(
                source_kind=source.source_kind,
                source_ref=source.source_ref,
                reason="source_missing",
            ),
        )
    total_bytes = sum(len(file_record.payload) for file_record in payloads)
    if len(payloads) > source.max_files:
        return _bounded_capture_result(
            selection=selection,
            reason="file_limit_exceeded",
        )
    if total_bytes > source.max_bytes:
        return _bounded_capture_result(
            selection=selection,
            reason="byte_limit_exceeded",
        )
    return _CaptureResult(files=tuple(payloads), omission=None)


def _bounded_capture_result(
    *,
    selection: _SourceSelection,
    reason: str,
) -> _CaptureResult:
    if selection.required:
        _refuse("required workspace source exceeds its capture bound")
    return _CaptureResult(
        files=(),
        omission=ContextCheckoutOmission(
            source_kind=selection.declaration.source_kind,
            source_ref=selection.declaration.source_ref,
            reason=reason,
        ),
    )


def _snapshot_tree(path: Path) -> dict[str, tuple[int, int, int, int, int]] | None:
    try:
        root_stat = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _CaptureInstability("workspace source could not be inspected") from exc
    root_identity = _file_identity(root_stat)
    if stat.S_ISLNK(root_stat.st_mode):
        _refuse("workspace source cannot be a symlink")
    if not stat.S_ISREG(root_stat.st_mode) and not stat.S_ISDIR(root_stat.st_mode):
        _refuse("workspace source must be a regular file or directory")
    snapshot = {"": root_identity}
    if stat.S_ISREG(root_stat.st_mode):
        return snapshot
    pending: list[tuple[Path, str]] = [(path, "")]
    while pending:
        current, relative_root = pending.pop()
        try:
            entries = list(os.scandir(current))
        except FileNotFoundError as exc:
            raise _CaptureInstability("workspace directory disappeared") from exc
        except OSError as exc:
            raise _CaptureInstability("workspace directory could not be read") from exc
        entries.sort(key=lambda entry: os.fsencode(entry.name))
        for entry in entries:
            relative = (
                entry.name
                if not relative_root
                else f"{relative_root}/{entry.name}"
            )
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except FileNotFoundError as exc:
                raise _CaptureInstability("workspace entry disappeared") from exc
            except OSError as exc:
                raise _CaptureInstability(
                    "workspace entry could not be inspected"
                ) from exc
            identity = _file_identity(entry_stat)
            if stat.S_ISLNK(entry_stat.st_mode):
                _refuse("workspace source tree cannot contain symlinks")
            if stat.S_ISREG(entry_stat.st_mode):
                snapshot[relative] = identity
            elif stat.S_ISDIR(entry_stat.st_mode):
                snapshot[relative] = identity
                pending.append((Path(entry.path), relative))
            else:
                _refuse("workspace source tree contains a special file")
    return snapshot


def _file_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_mode,
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
    )


def _read_regular_file(
    path: Path,
    expected_identity: tuple[int, int, int, int, int],
) -> bytes:
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, os.O_RDONLY | no_follow)
    except FileNotFoundError as exc:
        raise _CaptureInstability("workspace file disappeared") from exc
    except OSError as exc:
        if getattr(exc, "errno", None) in {40, 62}:
            raise _CaptureInstability("workspace file became a symlink") from exc
        raise ContextCheckoutPreparationError(
            "workspace file could not be opened"
        ) from exc
    try:
        descriptor_stat = os.fstat(descriptor)
        identity = _file_identity(descriptor_stat)
        if identity != expected_identity:
            raise _CaptureInstability("workspace file identity changed")
        if not stat.S_ISREG(descriptor_stat.st_mode):
            _refuse("workspace file is not regular")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            return stream.read()
    except OSError as exc:
        raise _CaptureInstability("workspace file could not be read") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _runtime_files(
    *,
    selections: Sequence[_SourceSelection],
    relation: _Relation,
) -> tuple[list[_CapturedFile], list[ContextCheckoutOmission]]:
    files: list[_CapturedFile] = []
    omissions: list[ContextCheckoutOmission] = []
    for selection in selections:
        source = selection.declaration
        if source.source_kind == "workspace_relative_root":
            continue
        records: tuple[bytes, ...] = ()
        if source.source_kind == "dispatch_material":
            records = (
                _canonical_runtime_record(relation.envelope.payload()),
            )
        elif source.source_kind == "accepted_lineage_artifacts":
            records = _artifact_records(relation)
        elif source.source_kind == "lineage_attempt_history":
            records = _attempt_records(relation)
        else:
            _refuse("unsupported runtime context source kind")
        payloads = tuple(records)
        total_bytes = sum(len(payload) for payload in payloads)
        if len(payloads) > source.max_files:
            _runtime_bound_result(
                selection=selection,
                reason="file_limit_exceeded",
                omissions=omissions,
            )
            continue
        if total_bytes > source.max_bytes:
            _runtime_bound_result(
                selection=selection,
                reason="byte_limit_exceeded",
                omissions=omissions,
            )
            continue
        if not payloads and not selection.required:
            omissions.append(
                ContextCheckoutOmission(
                    source_kind=source.source_kind,
                    source_ref=source.source_ref,
                    reason="source_missing",
                )
            )
            continue
        bucket = "required" if selection.required else "discoverable"
        for index, payload in enumerate(payloads):
            files.append(
                _CapturedFile(
                    checkout_path=(
                        f"{bucket}/runtime/{source.source_kind}/{index:06d}.json"
                    ),
                    source_kind=source.source_kind,
                    source_ref=source.source_ref,
                    required=selection.required,
                    payload=payload,
                )
            )
    return files, omissions


def _runtime_bound_result(
    *,
    selection: _SourceSelection,
    reason: str,
    omissions: list[ContextCheckoutOmission],
) -> None:
    if selection.required:
        _refuse("required runtime source exceeds its capture bound")
    omissions.append(
        ContextCheckoutOmission(
            source_kind=selection.declaration.source_kind,
            source_ref=selection.declaration.source_ref,
            reason=reason,
        )
    )


def _artifact_records(relation: _Relation) -> tuple[bytes, ...]:
    lineage = relation.work_item.lineage_id
    accepted: list[tuple[str, JSONValue]] = []
    artifacts = sorted(
        relation.state.artifacts.values(),
        key=lambda item: item.artifact_id.encode("utf-8"),
    )
    for artifact in artifacts:
        if _artifact_source_is_foreign_or_refuse(relation, artifact):
            continue
        try:
            source = _authenticate_artifact_source(
                relation.state,
                artifact,
            )
        except Exception as exc:
            _refuse("relevant artifact provenance could not be authenticated", exc)
        if (
            source.run.run_ref.plan_ref != relation.run.run_ref.plan_ref
            or source.selected_plan != relation.selected_plan
            or source.work_item.lineage_id != lineage
            or source.activation.lineage_id != lineage
        ):
            _refuse("relevant artifact provenance is outside current lineage")
        record: JSONValue = {
            "artifact_id": artifact.artifact_id,
            "payload_digest": artifact.payload_digest,
            "schema_id": str(artifact.schema_id),
            "payload": _json_ready(artifact.payload),
            "provenance": {
                "created_by_input_id": artifact.created_by_input_id,
                "lineage_id": lineage,
                "plan_fingerprint": relation.run.run_ref.plan_ref.authority_fingerprint,
                "source_action_id": str(artifact.source_action_id),
                "source_graph_node_id": artifact.source_graph_node_id,
                "source_run_id": artifact.source_run_id,
                "source_stage_kind_id": str(artifact.source_stage_kind_id),
                "source_work_item_id": artifact.work_item_id,
                "transition_id": artifact.transition_id,
            },
        }
        accepted.append((artifact.artifact_id, record))
    accepted.sort(key=lambda item: item[0].encode("utf-8"))
    return tuple(_canonical_runtime_record(record) for _, record in accepted)


def _authenticate_artifact_source(
    state: RuntimeState,
    artifact: ArtifactRecord,
) -> _AuthenticatedArtifactSource:
    observations = tuple(
        observation
        for observation in state.runner_observations.values()
        if observation.run_id == artifact.source_run_id
        or observation.created_by_input_id == artifact.created_by_input_id
    )
    if len(observations) != 1:
        _refuse("relevant artifact observation identity is invalid")
    observation = observations[0]
    input_id = observation.created_by_input_id
    transition = next(
        (
            candidate
            for candidate in state.transitions
            if candidate.record_id == artifact.transition_id
        ),
        None,
    )
    receipt = state.receipts.get(input_id)
    if (
        transition is None
        or receipt is None
        or receipt.transition_id != transition.record_id
        or not receipt.accepted
        or receipt.refusal_reason is not None
        or transition.input_id != input_id
        or not transition.accepted
        or transition.input_kind != RunnerResultObserved.input_kind
        or transition.input_family != "workflow_observation"
    ):
        _refuse("relevant artifact observation transition is invalid")
    observed_input = RunnerResultObserved(
        input_id,
        run_id=observation.run_id,
        payload=observation.payload,
        observed_at=observation.observed_at,
    )
    if receipt.receipt_ref.input_payload_digest != input_payload_digest(
        observed_input
    ):
        _refuse("relevant artifact observation receipt is invalid")
    replay_state = replace(
        state,
        receipts={
            key: value
            for key, value in state.receipts.items()
            if key != input_id
        },
        runner_observations={
            key: value
            for key, value in state.runner_observations.items()
            if key != observation.observation_id
        },
    )
    context = TransitionContext(
        transition_id=f"context-checkout:{input_id}:transition",
        work_item_id=f"context-checkout:{input_id}:work-item",
        activation_id=f"context-checkout:{input_id}:activation",
        run_id=f"context-checkout:{input_id}:run",
        claim_id=f"context-checkout:{input_id}:claim",
        fencing_token=f"context-checkout:{input_id}:fencing",
    )
    try:
        decision = decide(replay_state, observed_input, context)
    except Exception as exc:
        _refuse("relevant artifact observation could not be revalidated", exc)
    if not decision.accepted:
        _refuse("relevant artifact observation was refused")
    generated_artifacts = tuple(
        mutation.artifact
        for mutation in decision.mutations
        if isinstance(mutation, RecordArtifact) and mutation.artifact is not None
    )
    if len(generated_artifacts) != 1 or not _artifact_payload_matches(
        generated_artifacts[0],
        artifact,
    ):
        _refuse("relevant artifact payload authority is invalid")
    if not _audit_records_match(
        state=state,
        transition=transition,
        decision_events=decision.governance_events,
        decision_traces=decision.trace_records,
        input_id=input_id,
    ):
        _refuse("relevant artifact observation audit is invalid")
    run = state.runs.get(observation.run_id)
    if run is None:
        _refuse("relevant artifact source run is missing")
    work_item = state.work_items.get(run.work_item_id)
    activation = state.activations.get(run.activation_id)
    admitted = state.admitted_plans.get(run.run_ref.plan_ref.authority_fingerprint)
    if work_item is None or activation is None or admitted is None:
        _refuse("relevant artifact source authority is incomplete")
    source = _AuthenticatedArtifactSource(
        run=run,
        work_item=work_item,
        activation=activation,
        selected_plan=admitted.selected_plan,
    )
    action = next(
        (
            candidate
            for candidate in source.selected_plan.terminal_actions
            if candidate.id == artifact.source_action_id
        ),
        None,
    )
    if action is None or not _artifact_work_item_matches(
        state=state,
        artifact=artifact,
        source=source,
        action=action,
    ):
        _refuse("relevant artifact source work item is invalid")
    if not _artifact_payload_plan_pin_matches(artifact, source):
        _refuse("relevant artifact payload plan pin is invalid")
    return source


def _artifact_payload_matches(
    expected: ArtifactRecord,
    actual: ArtifactRecord,
) -> bool:
    return (
        expected.schema_id == actual.schema_id
        and expected.payload == actual.payload
        and expected.created_by_input_id == actual.created_by_input_id
        and expected.source_run_id == actual.source_run_id
        and expected.source_action_id == actual.source_action_id
        and expected.source_stage_kind_id == actual.source_stage_kind_id
        and expected.source_graph_node_id == actual.source_graph_node_id
        and expected.payload_digest == actual.payload_digest
    )


def _artifact_work_item_matches(
    *,
    state: RuntimeState,
    artifact: ArtifactRecord,
    source: _AuthenticatedArtifactSource,
    action: TerminalActionDeclaration,
) -> bool:
    if action.action_kind != "create_incident_route":
        return artifact.work_item_id == source.work_item.ref.work_item_id
    routes = tuple(
        route
        for route in state.activation_routes
        if route.created_by_input_id == artifact.created_by_input_id
        or (
            route.source_run_id == source.run.run_ref.run_id
            and route.action_id == action.id
        )
    )
    if len(routes) != 1:
        return False
    route = routes[0]
    target_work = state.work_items.get(route.target_work_item_id)
    target_activation = state.activations.get(route.target_activation_id)
    return (
        route.created_by_input_id == artifact.created_by_input_id
        and route.source_run_id == source.run.run_ref.run_id
        and route.source_work_item_id == source.work_item.ref.work_item_id
        and route.action_id == action.id
        and route.target_work_item_id == artifact.work_item_id
        and target_work is not None
        and target_work.created_by_input_id == artifact.created_by_input_id
        and target_activation is not None
        and target_activation.work_item_id == artifact.work_item_id
        and target_activation.created_by_input_id == artifact.created_by_input_id
    )


def _artifact_payload_plan_pin_matches(
    artifact: ArtifactRecord,
    source: _AuthenticatedArtifactSource,
) -> bool:
    plan_ref = source.run.run_ref.plan_ref
    selected_plan_id = artifact.payload.get("selected_plan_id")
    selected_fingerprint = artifact.payload.get("selected_plan_fingerprint")
    return (
        selected_plan_id is None or selected_plan_id == plan_ref.plan_id
    ) and (
        selected_fingerprint is None
        or selected_fingerprint == plan_ref.authority_fingerprint
    )


def _audit_records_match(
    *,
    state: RuntimeState,
    transition: TransitionRecord,
    decision_events: Sequence[GovernanceEventRecord],
    decision_traces: Sequence[TraceRecord],
    input_id: str,
) -> bool:
    if len(decision_events) != 1 or len(decision_traces) != 1:
        return False
    events = tuple(
        event
        for event in state.governance_events
        if event.input_id == input_id
        or event.record_id == f"{transition.record_id}:governance"
    )
    traces = tuple(
        trace
        for trace in state.traces
        if trace.input_id == input_id
        or trace.record_id == f"{transition.record_id}:trace"
    )
    if len(events) != 1 or len(traces) != 1:
        return False
    expected_event = decision_events[0]
    expected_trace = decision_traces[0]
    event = events[0]
    trace = traces[0]
    return (
        event.record_id == f"{transition.record_id}:governance"
        and trace.record_id == f"{transition.record_id}:trace"
        and _audit_fields(event) == _audit_fields(expected_event)
        and _audit_fields(trace) == _audit_fields(expected_trace)
        and event.action_id is not None
        and event.action_id == trace.action_id
    )


def _audit_fields(
    record: GovernanceEventRecord | TraceRecord,
) -> tuple[object, ...]:
    return (
        record.input_id,
        record.input_kind,
        record.input_family,
        record.disposition,
        record.plan_fingerprint,
        record.work_item_id,
        record.run_id,
        record.action_id,
        record.authority_source,
        record.refusal_reason,
    )


def _artifact_source_is_foreign_or_refuse(
    relation: _Relation,
    artifact: ArtifactRecord,
) -> bool:
    source_run = relation.state.runs.get(artifact.source_run_id)
    if source_run is None:
        _refuse("artifact source run is missing")

    source_work_item = relation.state.work_items.get(source_run.work_item_id)
    source_activation = relation.state.activations.get(source_run.activation_id)
    if source_work_item is None or source_activation is None:
        _refuse("artifact source links are incomplete")
    if (
        source_work_item.ref.plan_ref != source_run.run_ref.plan_ref
        or source_activation.plan_ref != source_run.run_ref.plan_ref
    ):
        _refuse("artifact source links are incoherent")
    if (
        source_run.run_ref.run_id != artifact.source_run_id
        or source_run.run_ref.work_item_id != source_work_item.ref.work_item_id
        or source_run.work_item_id != source_work_item.ref.work_item_id
        or source_run.activation_id != source_activation.activation_id
        or source_activation.work_item_id != source_work_item.ref.work_item_id
        or source_activation.lineage_id != source_work_item.lineage_id
        or source_activation.queue_family_id != source_work_item.queue_family_id
        or source_activation.stage_kind_id != source_run.stage_kind_id
        or source_activation.runner_binding_id != source_run.runner_binding_id
        or source_activation.claimed_by_run_id != source_run.run_ref.run_id
        or source_run.run_ref.generation != source_work_item.ref.generation
        or source_activation.generation != source_run.run_ref.generation + 1
    ):
        _refuse("relevant artifact source links are incoherent")
    if (
        source_run.run_ref.plan_ref != relation.run.run_ref.plan_ref
        or source_work_item.lineage_id != relation.work_item.lineage_id
    ):
        return True
    return False


def _attempt_records(relation: _Relation) -> tuple[bytes, ...]:
    records: list[tuple[int, str, JSONValue]] = []
    active_keys: set[tuple[str, str, str]] = set()
    for record_key, attempt in relation.state.recovery_attempts.items():
        if not _attempt_is_current_or_foreign(relation, attempt):
            continue
        if record_key != attempt.record_id:
            _refuse("relevant recovery attempt mapping key is not its record id")
        _validate_recovery_attempt(relation, attempt)
        active_key = (
            str(attempt.plan_ref.authority_fingerprint),
            str(attempt.policy_id),
            attempt.lineage_id,
        )
        if attempt.phase != "resolved" and active_key in active_keys:
            _refuse("relevant recovery attempt active key is duplicated")
        if attempt.phase != "resolved":
            active_keys.add(active_key)
        record: JSONValue = {
            "attempt_count": attempt.attempt_count,
            "created_by_input_id": attempt.created_by_input_id,
            "latest_recovery_activation_id": attempt.latest_recovery_activation_id,
            "latest_recovery_run_id": attempt.latest_recovery_run_id,
            "latest_return_action_id": (
                None
                if attempt.latest_return_action_id is None
                else str(attempt.latest_return_action_id)
            ),
            "lineage_id": attempt.lineage_id,
            "phase": attempt.phase,
            "plan_ref": {
                "authority_fingerprint": attempt.plan_ref.authority_fingerprint,
                "plan_format_version": attempt.plan_ref.plan_format_version,
                "plan_id": attempt.plan_ref.plan_id,
            },
            "policy_id": str(attempt.policy_id),
            "recovery_action_id": str(attempt.recovery_action_id),
            "record_id": attempt.record_id,
            "source_activation_id": attempt.source_activation_id,
            "source_graph_node_id": attempt.source_graph_node_id,
            "source_queue_family_id": str(attempt.source_queue_family_id),
            "source_run_id": attempt.source_run_id,
            "source_stage_kind_id": str(attempt.source_stage_kind_id),
            "source_runner_binding_id": str(attempt.source_runner_binding_id),
            "source_work_item_id": attempt.source_work_item_id,
            "updated_by_input_id": attempt.updated_by_input_id,
        }
        records.append((attempt.attempt_count, attempt.record_id, record))
    records.sort(key=lambda item: (item[0], item[1].encode("utf-8")))
    return tuple(_canonical_runtime_record(record) for _, _, record in records)


def _attempt_is_current_or_foreign(
    relation: _Relation,
    attempt: RecoveryAttemptRecord,
) -> bool:
    source_run = relation.state.runs.get(attempt.source_run_id)
    if source_run is None:
        _refuse("recovery attempt source run is missing")
    source_work_item = relation.state.work_items.get(source_run.work_item_id)
    source_activation = relation.state.activations.get(source_run.activation_id)
    if source_work_item is None or source_activation is None:
        _refuse("recovery attempt source links are incomplete")
    coherent = (
        source_run.run_ref.run_id == attempt.source_run_id
        and source_run.run_ref.work_item_id == source_work_item.ref.work_item_id
        and source_run.work_item_id == source_work_item.ref.work_item_id
        and source_run.activation_id == source_activation.activation_id
        and source_activation.work_item_id == source_work_item.ref.work_item_id
        and source_run.run_ref.plan_ref == source_work_item.ref.plan_ref
        and source_run.run_ref.plan_ref == source_activation.plan_ref
        and source_activation.queue_family_id == source_work_item.queue_family_id
        and source_activation.stage_kind_id == source_run.stage_kind_id
        and source_activation.runner_binding_id == source_run.runner_binding_id
        and source_activation.claimed_by_run_id == source_run.run_ref.run_id
        and source_run.run_ref.generation == source_work_item.ref.generation
        and source_activation.generation == source_run.run_ref.generation + 1
    )
    if not coherent:
        _refuse("recovery attempt source links are incoherent")
    if (
        source_run.run_ref.plan_ref != relation.run.run_ref.plan_ref
        or source_work_item.lineage_id != relation.work_item.lineage_id
    ):
        return False
    return True


def _validate_recovery_attempt(
    relation: _Relation,
    attempt: RecoveryAttemptRecord,
) -> None:
    if attempt.plan_ref != relation.run.run_ref.plan_ref:
        _refuse("relevant recovery attempt plan is not current authority")
    if attempt.lineage_id != relation.work_item.lineage_id:
        _refuse("relevant recovery attempt lineage is not current authority")
    if type(attempt.attempt_count) is not int or attempt.attempt_count < 1:
        _refuse("relevant recovery attempt count is invalid")

    policy = next(
        (
            candidate
            for candidate in relation.selected_plan.recovery_policies
            if candidate.id == attempt.policy_id
        ),
        None,
    )
    if policy is None:
        _refuse("relevant recovery attempt policy is not selected authority")
    if attempt.recovery_action_id not in policy.source_recovery_action_ids:
        _refuse("relevant recovery attempt action is not selected policy authority")
    if (
        attempt.latest_return_action_id is not None
        and attempt.latest_return_action_id not in policy.return_action_ids
    ):
        _refuse("relevant recovery attempt return action is not selected authority")
    if attempt.phase not in {
        "active_recovery",
        "pending_cooldown",
        "quarantine_eligible",
        "resolved",
    }:
        _refuse("relevant recovery attempt phase is unsupported")

    source_run = relation.state.runs.get(attempt.source_run_id)
    source_work_item = relation.state.work_items.get(attempt.source_work_item_id)
    source_activation = relation.state.activations.get(attempt.source_activation_id)
    if source_run is None or source_work_item is None or source_activation is None:
        _refuse("relevant recovery attempt source reference is missing")
    if (
        attempt.lineage_id != source_work_item.lineage_id
        or source_run.work_item_id != attempt.source_work_item_id
        or source_run.activation_id != attempt.source_activation_id
        or source_activation.work_item_id != attempt.source_work_item_id
        or source_work_item.ref.plan_ref != attempt.plan_ref
        or source_run.run_ref.plan_ref != attempt.plan_ref
        or source_activation.plan_ref != attempt.plan_ref
        or source_activation.graph_node_id != attempt.source_graph_node_id
        or source_run.stage_kind_id != attempt.source_stage_kind_id
        or source_run.runner_binding_id != attempt.source_runner_binding_id
        or source_work_item.queue_family_id != attempt.source_queue_family_id
    ):
        _refuse("relevant recovery attempt source context is incoherent")

    stage = next(
        (
            candidate
            for candidate in relation.selected_plan.stage_kinds
            if candidate.id == attempt.source_stage_kind_id
        ),
        None,
    )
    runner = next(
        (
            candidate
            for candidate in relation.selected_plan.runner_bindings
            if candidate.id == attempt.source_runner_binding_id
        ),
        None,
    )
    queue = next(
        (
            candidate
            for candidate in relation.selected_plan.queue_families
            if candidate.id == attempt.source_queue_family_id
        ),
        None,
    )
    graph = next(
        (
            candidate
            for candidate in relation.selected_plan.graphs
            if attempt.source_graph_node_id in candidate.node_ids
        ),
        None,
    )
    action = next(
        (
            candidate
            for candidate in relation.selected_plan.terminal_actions
            if candidate.id == attempt.recovery_action_id
        ),
        None,
    )
    target_stage = next(
        (
            candidate
            for candidate in relation.selected_plan.stage_kinds
            if candidate.id == policy.recovery_stage_kind_id
        ),
        None,
    )
    target_runner = next(
        (
            candidate
            for candidate in relation.selected_plan.runner_bindings
            if action is not None and candidate.id == action.runner_binding_id
        ),
        None,
    )
    target_graph = next(
        (
            candidate
            for candidate in relation.selected_plan.graphs
            if action is not None
            and action.target_graph_node_id is not None
            and action.target_graph_node_id in candidate.node_ids
        ),
        None,
    )
    if (
        stage is None
        or runner is None
        or queue is None
        or graph is None
        or action is None
        or target_stage is None
        or target_runner is None
        or target_graph is None
        or stage.runner_binding_id != attempt.source_runner_binding_id
        or attempt.source_stage_kind_id not in runner.stage_kind_ids
        or attempt.source_queue_family_id not in stage.input_queue_family_ids
        or attempt.source_stage_kind_id != action.stage_kind_id
        or action.action_kind != "recovery_route"
        or action.target_stage_kind_id != policy.recovery_stage_kind_id
        or action.target_graph_node_id is None
        or action.runner_binding_id is None
        or target_stage.runner_binding_id != action.runner_binding_id
        or target_stage.id not in target_runner.stage_kind_ids
    ):
        _refuse("relevant recovery attempt selected route authority is incoherent")

    expected_record_id = (
        "recovery-attempt:"
        f"{attempt.plan_ref.authority_fingerprint}:"
        f"{attempt.policy_id}:"
        f"{attempt.lineage_id}:"
        f"{attempt.created_by_input_id}"
    )
    if attempt.record_id != expected_record_id:
        _refuse("relevant recovery attempt record identity is invalid")

    if attempt.latest_recovery_activation_id is not None:
        latest_activation = relation.state.activations.get(
            attempt.latest_recovery_activation_id
        )
        if latest_activation is None or (
            latest_activation.plan_ref != attempt.plan_ref
            or latest_activation.lineage_id != attempt.lineage_id
            or latest_activation.stage_kind_id != policy.recovery_stage_kind_id
        ):
            _refuse("relevant recovery attempt latest activation is invalid")
    if attempt.latest_recovery_run_id is not None:
        latest_run = relation.state.runs.get(attempt.latest_recovery_run_id)
        if latest_run is None or (
            attempt.latest_recovery_activation_id is not None
            and latest_run.activation_id != attempt.latest_recovery_activation_id
        ) or latest_run.run_ref.plan_ref != attempt.plan_ref or (
            latest_run.stage_kind_id != policy.recovery_stage_kind_id
        ):
            _refuse("relevant recovery attempt latest run is invalid")
    if attempt.latest_return_action_id is not None:
        return_action = next(
            (
                candidate
                for candidate in relation.selected_plan.terminal_actions
                if candidate.id == attempt.latest_return_action_id
            ),
            None,
        )
        if return_action is None or (
            return_action.action_kind != "return_to_recorded_source"
        ):
            _refuse("relevant recovery attempt latest return action is invalid")


def _canonical_runtime_record(value: object) -> bytes:
    try:
        ready = _json_ready(value)
        return (
            json.dumps(
                ready,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        _refuse("runtime context record is not canonical JSON", exc)


def _json_ready(value: object) -> JSONValue:
    if value is None or isinstance(value, (str, bool)):
        if isinstance(value, str):
            _require_runtime_text(value)
        return value
    if type(value) is int:
        return value
    if isinstance(value, Mapping):
        result: dict[str, JSONValue] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                _refuse("runtime JSON mapping keys must be strings")
            _require_runtime_text(key)
            result[key] = _json_ready(nested)
        return result
    if isinstance(value, (tuple, list)):
        return [_json_ready(item) for item in value]
    _refuse("runtime context contains an unsupported value")


def _render_context_index(
    *,
    session: RunnerSessionRecord,
    plan_fingerprint: str,
    binding: StageContextBindingDeclaration,
    selections: Sequence[_SourceSelection],
    omissions: Sequence[ContextCheckoutOmission],
    payload_files: Sequence[_CapturedFile],
) -> str:
    required = tuple(item for item in selections if item.required)
    discoverable = tuple(item for item in selections if not item.required)
    lines = [
        "# Millrace Context Index",
        "",
        "Authority boundary:",
        f"- plan_fingerprint: {plan_fingerprint}",
        f"- binding_id: {binding.id}",
        f"- session_id: {session.session_id}",
        f"- dispatch_generation: {session.dispatch_generation}",
        "",
        "Required reads:",
    ]
    lines.extend(_source_lines(required))
    lines.extend(("", "Discoverable sources:"))
    lines.extend(_source_lines(discoverable))
    lines.extend(("", "Omissions:"))
    lines.extend(
        f"- {item.source_kind}/{item.source_ref}: {item.reason}"
        for item in omissions
    )
    if not omissions:
        lines.append("- none")
    lines.extend(("", "Live project root: .", "", "Files:"))
    lines.append("- CONTEXT.md: selected_router")
    lines.extend(
        f"- {item.checkout_path}: {item.source_kind}/{item.source_ref}"
        for item in sorted(
            payload_files,
            key=lambda item: item.checkout_path.encode("utf-8"),
        )
    )
    lines.extend(("", "Selected write rules:"))
    lines.extend(
        f"- {rule.relative_root}: {rule.disposition}"
        for rule in sorted(
            binding.write_rules,
            key=lambda item: item.relative_root.encode("utf-8"),
        )
    )
    if not binding.write_rules:
        lines.append("- none")
    lines.extend(
        (
            "",
            "Legal output channel: runner result markers and selected workspace "
            "write rules.",
        )
    )
    return "\n".join(lines)


def _source_lines(selections: Sequence[_SourceSelection]) -> list[str]:
    if not selections:
        return ["- none"]
    return [
        f"- {item.declaration.source_kind}/{item.declaration.source_ref}"
        for item in selections
    ]


def _manifest_for_files(
    *,
    session: RunnerSessionRecord,
    plan_fingerprint: str,
    binding: StageContextBindingDeclaration,
    files: tuple[_CapturedFile, ...],
    omissions: tuple[ContextCheckoutOmission, ...],
) -> ContextCheckoutManifest:
    return ContextCheckoutManifest(
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        plan_fingerprint=plan_fingerprint,
        binding_id=str(binding.id),
        router_asset_id=str(binding.router_asset_id),
        files=tuple(
            ContextCheckoutFile(
                checkout_path=file_record.checkout_path,
                source_kind=file_record.source_kind,
                source_ref=file_record.source_ref,
                content_digest=storage_digest_for_bytes(file_record.payload),
                byte_length=len(file_record.payload),
                required=file_record.required,
            )
            for file_record in files
        ),
        omissions=omissions,
    )


def _validate_captured_paths(files: Sequence[_CapturedFile]) -> None:
    paths = [file_record.checkout_path for file_record in files]
    if len(paths) != len(set(paths)):
        _refuse("generated checkout destinations collide")
    for path in paths:
        _safe_relative_path(path, "generated checkout path")
    ordered_paths = sorted(paths, key=lambda value: value.encode("utf-8"))
    for parent, child in zip(ordered_paths, ordered_paths[1:]):
        if child.startswith(f"{parent}/"):
            _refuse("generated checkout destinations collide")


def _validate_manifest_payloads(
    manifest: ContextCheckoutManifest,
    payload_by_path: Mapping[str, bytes],
) -> None:
    if set(payload_by_path) != {item.checkout_path for item in manifest.files}:
        _refuse("manifest file set does not match captured payloads")
    for item in manifest.files:
        payload = payload_by_path[item.checkout_path]
        if len(payload) != item.byte_length:
            _refuse("manifest byte length does not match payload")
        if storage_digest_for_bytes(payload) != item.content_digest:
            _refuse("manifest content digest does not match payload")


def _put_cas_bytes(
    *,
    cas_store: ContentAddressedByteStore,
    payloads: Sequence[_CapturedFile],
    manifest_bytes: bytes,
    manifest_digest: str,
) -> None:
    for file_record in payloads:
        returned = cas_store.put_bytes(file_record.payload)
        expected = storage_digest_for_bytes(file_record.payload)
        if returned != expected:
            _refuse("CAS returned an unexpected content digest")
    returned_manifest_digest = cas_store.put_bytes(manifest_bytes)
    if returned_manifest_digest != manifest_digest:
        _refuse("CAS returned an unexpected manifest digest")


def _reuse_existing_checkout(
    *,
    final_root: Path,
    session: RunnerSessionRecord,
    plan_fingerprint: str,
    binding: StageContextBindingDeclaration,
    selections: Sequence[_SourceSelection],
    cas_store: ContentAddressedByteStore,
) -> PreparedContextCheckout:
    manifest_bytes = _read_regular_file_without_following(
        final_root / "checkout.manifest.json"
    )
    try:
        manifest = decode_context_checkout_manifest(manifest_bytes)
    except ContextCheckoutContractError as exc:
        _refuse("existing checkout manifest is not canonical", exc)
    if manifest.session_id != session.session_id:
        _refuse("existing checkout manifest session identity does not match")
    if manifest.dispatch_generation != session.dispatch_generation:
        _refuse("existing checkout manifest generation does not match")
    if manifest.plan_fingerprint != plan_fingerprint:
        _refuse("existing checkout manifest plan fingerprint does not match")
    if manifest.binding_id != str(binding.id):
        _refuse("existing checkout manifest binding identity does not match")
    if manifest.router_asset_id != str(binding.router_asset_id):
        _refuse("existing checkout manifest router identity does not match")
    _validate_checkout_manifest_shape(
        manifest,
        binding=binding,
        selections=selections,
    )
    manifest_digest = context_checkout_manifest_digest(manifest_bytes)
    payload_by_path = _load_existing_checkout_payloads(
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        manifest_digest=manifest_digest,
        cas_store=cas_store,
    )
    _verify_existing_checkout(
        final_root=final_root,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        manifest_digest=manifest_digest,
        payload_by_path=payload_by_path,
        cas_store=cas_store,
    )
    return PreparedContextCheckout(
        manifest=manifest,
        manifest_digest=manifest_digest,
        materialized_checkout_root=final_root,
    )


def _discard_unattached_checkout(
    *,
    final_root: Path,
    session: RunnerSessionRecord,
    plan_fingerprint: str,
    binding: StageContextBindingDeclaration,
    selections: Sequence[_SourceSelection],
    cas_store: ContentAddressedByteStore,
) -> None:
    _reuse_existing_checkout(
        final_root=final_root,
        session=session,
        plan_fingerprint=plan_fingerprint,
        binding=binding,
        selections=selections,
        cas_store=cas_store,
    )
    _reject_symlink_components(final_root, stop=None)
    try:
        root_stat = final_root.lstat()
    except OSError as exc:
        _refuse("existing checkout root cannot be inspected", exc)
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
        _refuse("existing checkout root is not a regular directory")
    _cleanup_published_checkout(
        final_root,
        expected_identity=(root_stat.st_dev, root_stat.st_ino),
    )
    if _path_exists_without_following(final_root):
        _refuse("validated existing checkout could not be discarded")


def _validate_checkout_manifest_shape(
    manifest: ContextCheckoutManifest,
    *,
    binding: StageContextBindingDeclaration,
    selections: Sequence[_SourceSelection],
) -> None:
    selected_sources = {
        (
            selection.declaration.source_kind,
            selection.declaration.source_ref,
        ): selection
        for selection in selections
    }
    files_by_source: dict[tuple[str, str], list[ContextCheckoutFile]] = {}
    runtime_indices: dict[tuple[str, str], list[int]] = {}
    router_files = tuple(
        item for item in manifest.files if item.source_kind == "selected_router"
    )
    if len(router_files) != 1:
        _refuse("existing checkout manifest must contain one selected router")
    router_file = router_files[0]
    if (
        router_file.checkout_path != "CONTEXT.md"
        or router_file.source_ref != str(binding.router_asset_id)
        or not router_file.required
    ):
        _refuse("existing checkout manifest selected router is invalid")
    if any(item.checkout_path == "checkout.manifest.json" for item in manifest.files):
        _refuse("existing checkout manifest cannot list its own manifest file")
    for item in manifest.files:
        if item.source_kind == "selected_router":
            continue
        source_key = (item.source_kind, item.source_ref)
        selection = selected_sources.get(source_key)
        if selection is None:
            _refuse("existing checkout manifest contains an unselected source")
        if item.required != selection.required:
            _refuse("existing checkout manifest source requirement is invalid")
        bucket = "required" if selection.required else "discoverable"
        if item.source_kind == "workspace_relative_root":
            prefix = f"{bucket}/workspace/{item.source_ref}"
            if item.checkout_path != prefix and not item.checkout_path.startswith(
                f"{prefix}/"
            ):
                _refuse("existing checkout manifest workspace layout is invalid")
        else:
            prefix = f"{bucket}/runtime/{item.source_kind}/"
            suffix = item.checkout_path.removeprefix(prefix)
            if (
                not item.checkout_path.startswith(prefix)
                or len(suffix) != 11
                or suffix[6:] != ".json"
                or not suffix[:6].isdigit()
            ):
                _refuse("existing checkout manifest runtime layout is invalid")
            runtime_indices.setdefault(source_key, []).append(int(suffix[:6]))
        files_by_source.setdefault(source_key, []).append(item)
    for source_key, indices in runtime_indices.items():
        if sorted(indices) != list(range(len(indices))):
            _refuse("existing checkout manifest runtime ordering is invalid")
    omissions_by_source: set[tuple[str, str]] = set()
    for omission in manifest.omissions:
        source_key = (omission.source_kind, omission.source_ref)
        selection = selected_sources.get(source_key)
        if selection is None or selection.required:
            _refuse("existing checkout manifest omission is not discoverable")
        if source_key in omissions_by_source or source_key in files_by_source:
            _refuse("existing checkout manifest omission is not closed")
        omissions_by_source.add(source_key)
    for selection in selections:
        source_key = (
            selection.declaration.source_kind,
            selection.declaration.source_ref,
        )
        has_files = bool(files_by_source.get(source_key))
        if selection.required:
            if selection.declaration.source_kind in {
                "dispatch_material",
                "workspace_relative_root",
            } and not has_files:
                _refuse("existing checkout required source is not represented")
        elif not has_files and source_key not in omissions_by_source:
            _refuse("existing checkout discoverable source is not closed")


def _load_existing_checkout_payloads(
    *,
    manifest: ContextCheckoutManifest,
    manifest_bytes: bytes,
    manifest_digest: str,
    cas_store: ContentAddressedByteStore,
) -> dict[str, bytes]:
    payload_by_path: dict[str, bytes] = {}
    try:
        for item in manifest.files:
            payload = cas_store.get_bytes(item.content_digest)
            if (
                type(payload) is not bytes
                or len(payload) != item.byte_length
                or storage_digest_for_bytes(payload) != item.content_digest
            ):
                _refuse("existing checkout CAS file object is not authentic")
            payload_by_path[item.checkout_path] = payload
        cas_manifest = cas_store.get_bytes(manifest_digest)
        if cas_manifest != manifest_bytes:
            _refuse("existing checkout CAS manifest is not canonical")
    except ContextCheckoutPreparationError:
        raise
    except Exception as exc:
        _refuse("existing checkout CAS material is not authentic", exc)
    return payload_by_path


def _publish_checkout(
    *,
    final_root: Path,
    manifest: ContextCheckoutManifest,
    manifest_bytes: bytes,
    payload_by_path: Mapping[str, bytes],
    cas_store: ContentAddressedByteStore,
    manifest_digest: str,
) -> None:
    checkout_parent = final_root.parent
    checkout_parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_components(final_root, stop=None)
    if _path_exists_without_following(final_root):
        _verify_existing_checkout(
            final_root=final_root,
            manifest=manifest,
            manifest_bytes=manifest_bytes,
            manifest_digest=manifest_digest,
            payload_by_path=payload_by_path,
            cas_store=cas_store,
        )
        return
    temporary_root = Path(
        tempfile.mkdtemp(prefix=f".{final_root.name}.", dir=checkout_parent)
    )
    try:
        files = dict(payload_by_path)
        files["checkout.manifest.json"] = manifest_bytes
        for bucket in ("required", "discoverable"):
            (temporary_root / bucket).mkdir(parents=True, exist_ok=True)
        for checkout_path, payload in files.items():
            destination = temporary_root / checkout_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(payload)
        _set_checkout_modes(temporary_root, root_mode=0o555)
        _verify_existing_checkout(
            final_root=temporary_root,
            manifest=manifest,
            manifest_bytes=manifest_bytes,
            manifest_digest=manifest_digest,
            payload_by_path=payload_by_path,
            cas_store=cas_store,
        )
        temporary_stat = temporary_root.lstat()
        published_identity = (temporary_stat.st_dev, temporary_stat.st_ino)
        temporary_root.chmod(0o755)
        try:
            _atomic_no_replace_rename(temporary_root, final_root)
        except FileExistsError:
            _verify_existing_checkout(
                final_root=final_root,
                manifest=manifest,
                manifest_bytes=manifest_bytes,
                manifest_digest=manifest_digest,
                payload_by_path=payload_by_path,
                cas_store=cas_store,
            )
        else:
            try:
                final_root.chmod(0o555)
            except OSError as exc:
                _cleanup_published_checkout(
                    final_root,
                    expected_identity=published_identity,
                )
                _refuse("published checkout cannot be made read-only", exc)
    finally:
        _cleanup_temporary_root(temporary_root)


def _atomic_no_replace_rename(source: Path, destination: Path) -> None:
    if sys.platform == "darwin":
        _renameatx_np_swap(source, destination)
    elif sys.platform.startswith("linux"):
        _renameat2_swap(source, destination)
    else:
        _refuse("atomic no-replace directory publication is unsupported")


def _renameatx_np_swap(source: Path, destination: Path) -> None:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameatx_np = libc.renameatx_np
    except (AttributeError, OSError) as exc:
        _refuse("atomic no-replace directory publication is unsupported", exc)
    renameatx_np.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameatx_np.restype = ctypes.c_int
    result = renameatx_np(
        -2,
        os.fsencode(source),
        -2,
        os.fsencode(destination),
        0x00000004,
    )
    _raise_atomic_rename_result(result, source, destination)


def _renameat2_swap(source: Path, destination: Path) -> None:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (AttributeError, OSError) as exc:
        _refuse("atomic no-replace directory publication is unsupported", exc)
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        -100,
        os.fsencode(source),
        -100,
        os.fsencode(destination),
        0x00000001,
    )
    _raise_atomic_rename_result(result, source, destination)


def _cleanup_temporary_root(root: Path) -> None:
    try:
        root.lstat()
    except FileNotFoundError:
        return
    except OSError:
        return
    try:
        _set_checkout_writable(root)
    except Exception:
        pass
    try:
        shutil.rmtree(root)
        return
    except Exception:
        pass
    _force_checkout_writable(root)
    try:
        shutil.rmtree(root)
    except Exception:
        try:
            os.rmdir(root)
        except OSError:
            pass


def _cleanup_published_checkout(
    root: Path,
    *,
    expected_identity: tuple[int, int],
) -> None:
    try:
        root_stat = root.lstat()
    except (FileNotFoundError, OSError):
        return
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or (root_stat.st_dev, root_stat.st_ino) != expected_identity
    ):
        return
    _cleanup_temporary_root(root)


def _force_checkout_writable(root: Path) -> None:
    try:
        os.chmod(root, 0o755, follow_symlinks=False)
    except (NotImplementedError, OSError):
        pass
    try:
        for current, directories, files in os.walk(root, followlinks=False):
            for name in (*directories, *files):
                path = os.path.join(current, name)
                mode = 0o755 if name in directories else 0o644
                try:
                    os.chmod(path, mode, follow_symlinks=False)
                except (NotImplementedError, OSError):
                    pass
    except OSError:
        pass


def _raise_atomic_rename_result(
    result: int,
    source: Path,
    destination: Path,
) -> None:
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise FileExistsError(error_number, os.strerror(error_number), destination)
    raise OSError(error_number, os.strerror(error_number), source, destination)


def _verify_existing_checkout(
    *,
    final_root: Path,
    manifest: ContextCheckoutManifest,
    manifest_bytes: bytes,
    manifest_digest: str,
    payload_by_path: Mapping[str, bytes],
    cas_store: ContentAddressedByteStore,
) -> None:
    try:
        root_stat = final_root.lstat()
    except OSError as exc:
        _refuse("existing checkout root cannot be inspected", exc)
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
        _refuse("existing checkout root is not a regular directory")
    expected_files = set(payload_by_path) | {"checkout.manifest.json"}
    expected_dirs = _expected_directories(expected_files)
    actual_files: set[str] = set()
    actual_dirs: set[str] = {""}
    pending = [(final_root, "")]
    while pending:
        current, relative_root = pending.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            _refuse("existing checkout cannot be inspected", exc)
        for entry in entries:
            relative = (
                entry.name
                if not relative_root
                else f"{relative_root}/{entry.name}"
            )
            entry_path = Path(entry.path)
            try:
                entry_stat = entry_path.lstat()
            except OSError as exc:
                _refuse("existing checkout entry cannot be inspected", exc)
            if stat.S_ISLNK(entry_stat.st_mode):
                _refuse("existing checkout contains a symlink")
            if stat.S_ISDIR(entry_stat.st_mode):
                actual_dirs.add(relative)
                pending.append((entry_path, relative))
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                _refuse("existing checkout contains a special file")
            actual_files.add(relative)
            if entry_stat.st_mode & 0o777 != 0o444:
                _refuse("existing checkout file mode drifted")
    if actual_files != expected_files or actual_dirs != expected_dirs:
        _refuse("existing checkout path set drifted")
    if any(
        entry_path_mode != 0o555
        for entry_path_mode in (
            Path(final_root / relative).lstat().st_mode & 0o777
            for relative in actual_dirs
        )
    ):
        _refuse("existing checkout directory mode drifted")
    _validate_manifest_payloads(manifest, payload_by_path)
    try:
        for item in manifest.files:
            cas_payload = cas_store.get_bytes(item.content_digest)
            if (
                type(cas_payload) is not bytes
                or len(cas_payload) != item.byte_length
                or storage_digest_for_bytes(cas_payload) != item.content_digest
                or cas_payload != payload_by_path[item.checkout_path]
            ):
                _refuse("CAS file bytes do not match manifest payload")
        cas_manifest = cas_store.get_bytes(manifest_digest)
    except ContextCheckoutPreparationError:
        raise
    except Exception as exc:
        _refuse("existing checkout CAS material is not authentic", exc)
    if cas_manifest != manifest_bytes:
        _refuse("CAS manifest bytes do not match canonical manifest")
    for checkout_path, payload in payload_by_path.items():
        actual = _read_regular_file_without_identity(final_root / checkout_path)
        if actual != payload:
            _refuse("existing checkout payload drifted")
    actual_manifest = _read_regular_file_without_identity(
        final_root / "checkout.manifest.json"
    )
    if actual_manifest != manifest_bytes:
        _refuse("existing checkout manifest drifted")


def _read_regular_file_without_identity(path: Path) -> bytes:
    return _read_regular_file_without_following(path)


def _read_regular_file_without_following(path: Path) -> bytes:
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if no_follow == 0:
        _refuse("checkout file no-follow inspection is unsupported")
    try:
        descriptor = os.open(path, os.O_RDONLY | no_follow)
    except OSError as exc:
        _refuse("checkout file cannot be opened without following symlinks", exc)
    try:
        value = os.fstat(descriptor)
        if not stat.S_ISREG(value.st_mode) or stat.S_ISLNK(value.st_mode):
            _refuse("checkout file is not regular")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            return stream.read()
    except OSError as exc:
        _refuse("checkout file cannot be read", exc)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _expected_directories(files: set[str]) -> set[str]:
    directories = {"", "required", "discoverable"}
    for file_name in files:
        path = Path(file_name)
        for parent in path.parents:
            parent_name = parent.as_posix()
            if parent_name != ".":
                directories.add(parent_name)
    return directories


def _set_checkout_modes(root: Path, *, root_mode: int) -> None:
    files: list[Path] = []
    directories: list[Path] = []
    pending = [root]
    while pending:
        current = pending.pop()
        directories.append(current)
        for entry in current.iterdir():
            if entry.is_dir() and not entry.is_symlink():
                pending.append(entry)
            elif entry.is_file() and not entry.is_symlink():
                files.append(entry)
            else:
                _refuse("temporary checkout contains a non-regular entry")
    for path in files:
        path.chmod(0o444)
    for path in sorted(directories, key=lambda value: len(value.parts), reverse=True):
        path.chmod(root_mode if path == root else 0o555)


def _set_checkout_writable(root: Path) -> None:
    pending = [root]
    while pending:
        current = pending.pop()
        current.chmod(0o755)
        for entry in current.iterdir():
            if entry.is_dir() and not entry.is_symlink():
                pending.append(entry)
            elif entry.is_file() and not entry.is_symlink():
                entry.chmod(0o644)


def _final_root(checkout_root: Path, *, session: RunnerSessionRecord) -> Path:
    session_component = _safe_component(session.session_id, "session_id")
    return checkout_root / session_component / str(session.dispatch_generation)


def _validate_materialization_target(final_root: Path) -> None:
    _reject_symlink_components(final_root, stop=None)
    for ancestor in final_root.parents:
        if not _path_exists_without_following(ancestor):
            continue
        try:
            ancestor_stat = ancestor.lstat()
        except OSError as exc:
            _refuse("checkout ancestor cannot be inspected", exc)
        if not stat.S_ISDIR(ancestor_stat.st_mode):
            _refuse("checkout ancestor is not a directory")
    if _path_exists_without_following(final_root):
        try:
            final_stat = final_root.lstat()
        except OSError as exc:
            _refuse("checkout generation cannot be inspected", exc)
        if not stat.S_ISDIR(final_stat.st_mode):
            _refuse("checkout generation is not a directory")


def _safe_component(value: object, field_name: str) -> str:
    text = _require_identity(value, field_name)
    if text in {".", ".."} or "/" in text or "\\" in text:
        _refuse(f"{field_name} must be one safe path component")
    return text


def _safe_relative_path(value: object, field_name: str) -> str:
    text = _require_identity(value, field_name)
    if ":" in text.split("/", 1)[0]:
        _refuse(f"{field_name} must be a safe relative POSIX path")
    parts = text.split("/")
    if (
        text.startswith("/")
        or "\\" in text
        or any(part in {"", ".", ".."} for part in parts)
        or any(part == ".millrace" for part in parts)
    ):
        _refuse(f"{field_name} must be a safe relative POSIX path")
    return text


def _require_identity(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _refuse(f"{field_name} must be a non-blank string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        _refuse(f"{field_name} must be valid UTF-8", exc)
    if "\x00" in value:
        _refuse(f"{field_name} must not contain NUL")
    if normalize("NFC", value) != value:
        _refuse(f"{field_name} must be NFC")
    return value


def _require_runtime_text(value: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        _refuse("runtime JSON text must be valid UTF-8", exc)
    if "\x00" in value:
        _refuse("runtime JSON text must not contain NUL")
    if normalize("NFC", value) != value:
        _refuse("runtime JSON text must be NFC")


def _validate_utf8_text(payload: bytes) -> None:
    try:
        decoded = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        _refuse("workspace file is not UTF-8", exc)
    if "\x00" in decoded:
        _refuse("workspace file contains NUL")


def _resolved_path(path: Path, label: str) -> Path:
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        _refuse(f"{label} path cannot be resolved", exc)


def _reject_symlink_components(path: Path, stop: Path | None) -> None:
    current = Path(path.anchor)
    stop_path = None if stop is None else Path(stop)
    for part in path.parts[1:]:
        current /= part
        if stop_path is not None and not _is_descendant_or_equal(
            current,
            stop_path,
        ):
            continue
        try:
            if current.is_symlink():
                _refuse("path authority cannot pass through a symlink")
        except OSError as exc:
            _refuse("path authority cannot be inspected", exc)


def _is_descendant_or_equal(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _paths_overlap(left: Path, right: Path) -> bool:
    left = _resolved_path(left, "path")
    right = _resolved_path(right, "path")
    return _is_descendant_or_equal(left, right) or _is_descendant_or_equal(right, left)


def _path_exists_without_following(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        _refuse("path cannot be inspected", exc)
    return True


__all__ = (
    "ContextCheckoutPreparationError",
    "PreparedContextCheckout",
    "prepare_context_checkout",
    "rematerialize_attached_context_checkout",
)
