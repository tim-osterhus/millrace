"""Compiler-driven closure-target lifecycle and backlog-drain activation helpers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.architecture import (
    CompiledRunPlan,
    GraphLoopCompletionBehaviorDefinition,
    WorkItemFamilyDefinition,
)
from millrace_ai.assets import load_builtin_workflow_primitives
from millrace_ai.contracts import (
    ClosureBlockingWorkRef,
    ClosureRootSource,
    ClosureTargetState,
    IncidentDocument,
    Plane,
    ProbeDocument,
    SpecDocument,
    WorkItemKind,
)
from millrace_ai.contracts.stage_metadata import validate_safe_identifier
from millrace_ai.errors import WorkspaceStateError
from millrace_ai.events import write_runtime_event
from millrace_ai.paths import WorkspacePaths
from millrace_ai.queue_store import QueueClaim
from millrace_ai.state_store import save_snapshot
from millrace_ai.workspace.arbiter_state import (
    list_open_closure_target_states,
    load_closure_target_state,
    save_closure_target_state,
    write_canonical_idea_contract,
    write_canonical_root_source_contract,
    write_canonical_root_spec_contract,
)
from millrace_ai.workspace.blueprint_state import list_open_blueprint_lineage_work_refs
from millrace_ai.workspace.family_adapters import (
    queue_adapter_for_id,
    resolve_queue_lifecycle_adapter_id,
)
from millrace_ai.workspace.idea_sources import idea_source_artifact_path
from millrace_ai.workspace.lineage_integrity import (
    LineageDriftDiagnostic,
    scan_closure_lineage_drift,
    write_lineage_drift_diagnostic,
)
from millrace_ai.workspace.work_documents import parse_work_document_as
from millrace_ai.workspace.work_inventory import WorkInventoryItemRef, closure_blocking_refs

from .scheduler_policy import backpressure_outcome

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine
    from millrace_ai.workspace.family_adapters import WorkFamilyQueueAdapter

from .active_runs import active_run_from_closure_target, snapshot_with_active_run
from .graph_authority import completion_activation_for_graph
from .lanes import compiled_plan_fingerprint_for_runtime, lane_id_for_plane

_SUPPORTED_ROOT_SOURCE_KINDS = frozenset({"idea", "probe", "manual", "spec", "incident"})


@dataclass(frozen=True, slots=True)
class ClosureTargetPreparation:
    """Result of closure-target preflight for a queued work-item claim."""

    allowed: bool
    target: ClosureTargetState | None = None
    open_root_spec_id: str | None = None
    deferred_root_spec_id: str | None = None


@dataclass(frozen=True, slots=True)
class RootSourceResolution:
    """Resolved source artifact content for a closure target."""

    source: ClosureRootSource
    markdown: str


class RootSourceResolutionError(WorkspaceStateError):
    """Raised when closure-target creation cannot resolve the generic root source."""

    def __init__(
        self,
        *,
        failure_class: str,
        root_source_kind: str | None,
        root_source_id: str | None,
        candidates: tuple[Path, ...] = (),
    ) -> None:
        self.failure_class = failure_class
        self.root_source_kind = root_source_kind
        self.root_source_id = root_source_id
        self.candidates = candidates
        super().__init__(
            "could not resolve closure root source "
            f"kind={root_source_kind} id={root_source_id} failure_class={failure_class}"
        )


def maybe_open_closure_target_for_claim(
    engine: RuntimeEngine,
    claim: QueueClaim,
) -> ClosureTargetState | None:
    preparation = prepare_closure_target_for_claim(engine, claim)
    if not preparation.allowed:
        raise WorkspaceStateError(
            "cannot open closure target while another open closure target exists"
        )
    return preparation.target


def prepare_closure_target_for_claim(
    engine: RuntimeEngine,
    claim: QueueClaim,
) -> ClosureTargetPreparation:
    if claim.work_item_kind is not WorkItemKind.SPEC:
        return ClosureTargetPreparation(allowed=True)

    spec = _load_spec_document(claim.path)
    return _prepare_closure_target_for_spec(engine, spec_path=claim.path, spec=spec)


def maybe_activate_completion_stage(engine: RuntimeEngine) -> ClosureTargetState | None:
    assert engine.snapshot is not None
    assert engine.compiled_plan is not None
    completion_behavior = _completion_behavior_for(engine)
    if completion_behavior is None:
        return None

    target = active_closure_target(engine)
    if target is None:
        target = _recover_or_diagnose_missing_closure_target(engine)
        if target is None:
            return None
    if completion_behavior.skip_if_already_closed and not target.closure_open:
        return None

    target = refresh_closure_target_readiness(engine, target)
    if target.closure_blocked_by_lineage_work:
        return None
    if block_on_closure_lineage_drift_if_present(engine, target):
        return None

    activation = completion_activation_for_graph(engine.compiled_plan)
    active_run = active_run_from_closure_target(
        activation=activation,
        target=target,
        lane_id=lane_id_for_plane(engine.compiled_plan, activation.plane),
        run_id=engine._new_run_id(),
        compiled_plan_id=engine.compiled_plan.compiled_plan_id,
        compiled_plan_fingerprint=compiled_plan_fingerprint_for_runtime(engine.compiled_plan),
        now=engine._now(),
    )
    engine.snapshot = snapshot_with_active_run(
        engine.snapshot,
        active_run,
        now=engine._now(),
        current_failure_class=None,
    )
    save_snapshot(engine.paths, engine.snapshot)
    return target


def active_closure_target(engine: RuntimeEngine) -> ClosureTargetState | None:
    open_targets = list_open_closure_target_states(engine.paths)
    actionable_targets = _actionable_open_closure_targets(open_targets)
    if not actionable_targets:
        return None
    if len(actionable_targets) > 1:
        raise WorkspaceStateError("multiple actionable open closure targets found")
    return actionable_targets[0]


def refresh_closure_target_readiness(
    engine: RuntimeEngine,
    target: ClosureTargetState,
) -> ClosureTargetState:
    normal_refs = _closure_blocking_refs_from_inventory(
        engine.paths,
        root_spec_id=target.root_spec_id,
        compiled_plan=engine.compiled_plan,
    )
    blueprint_refs = list_open_blueprint_lineage_work_refs(
        engine.paths,
        root_spec_id=target.root_spec_id,
    )
    blocking_work_refs = _unique_blocking_refs((*normal_refs, *blueprint_refs))
    lineage_work_ids = _open_lineage_work_ids_from_adapters(
        engine.paths,
        root_spec_id=target.root_spec_id,
        compiled_plan=engine.compiled_plan,
    )
    blocking_work_ids = _unique_ids(
        (
            *lineage_work_ids,
            *_safe_bare_work_ids(blocking_work_refs),
        )
    )
    updated = target.model_copy(
        update={
            "closure_blocked_by_lineage_work": bool(blocking_work_refs or blocking_work_ids),
            "blocking_work_ids": blocking_work_ids,
            "blocking_work_refs": blocking_work_refs,
        }
    )
    save_closure_target_state(engine.paths, updated)
    return updated


def _closure_blocking_refs_from_inventory(
    paths: WorkspacePaths,
    *,
    root_spec_id: str,
    compiled_plan: CompiledRunPlan | None,
) -> tuple[ClosureBlockingWorkRef, ...]:
    return tuple(
        _blocking_ref_from_inventory(paths, ref)
        for ref in closure_blocking_refs(
            paths,
            root_spec_id=root_spec_id,
            compiled_plan=compiled_plan,
        )
        if ref.family_id != WorkItemKind.BLUEPRINT_DRAFT.value
    )


def _open_lineage_work_ids_from_adapters(
    paths: WorkspacePaths,
    *,
    root_spec_id: str,
    compiled_plan: CompiledRunPlan | None,
) -> tuple[str, ...]:
    seen: set[str] = set()
    work_item_ids: list[str] = []
    for family in _work_item_families_for_lineage(compiled_plan):
        adapter = _queue_adapter_for_family(family)
        if adapter is None:
            continue
        for work_item_id in adapter.list_open_lineage_work_ids(
            paths,
            root_spec_id=root_spec_id,
        ):
            if work_item_id in seen:
                continue
            seen.add(work_item_id)
            work_item_ids.append(work_item_id)
    return tuple(work_item_ids)


def _work_item_families_for_lineage(
    compiled_plan: CompiledRunPlan | None,
) -> tuple[WorkItemFamilyDefinition, ...]:
    if compiled_plan is not None and compiled_plan.work_item_families_by_id:
        return tuple(compiled_plan.work_item_families_by_id.values())
    return load_builtin_workflow_primitives().work_item_families


def _queue_adapter_for_family(
    family: WorkItemFamilyDefinition,
) -> WorkFamilyQueueAdapter | None:
    adapter_id = resolve_queue_lifecycle_adapter_id(family)
    if adapter_id is not None:
        adapter = queue_adapter_for_id(adapter_id)
        if adapter is not None:
            return adapter
    return None


def _blocking_ref_from_inventory(
    paths: WorkspacePaths,
    ref: WorkInventoryItemRef,
) -> ClosureBlockingWorkRef:
    return ClosureBlockingWorkRef(
        blocker_type="work_item",
        reason="open_lineage_work",
        work_item_family_id=ref.family_id,
        work_item_id=ref.work_item_id,
        state=ref.state,
        artifact_path=_runtime_relative_path(paths, ref.path),
    )


def _safe_bare_work_ids(refs: tuple[ClosureBlockingWorkRef, ...]) -> tuple[str, ...]:
    return _unique_ids(
        (
            ref.work_item_id
            for ref in refs
            if ref.blocker_type == "work_item"
            and ref.work_item_id is not None
            and _is_safe_identifier(ref.work_item_id)
        )
    )


def _unique_blocking_refs(
    refs: tuple[ClosureBlockingWorkRef, ...],
) -> tuple[ClosureBlockingWorkRef, ...]:
    unique: list[ClosureBlockingWorkRef] = []
    seen: set[tuple[object, ...]] = set()
    for ref in refs:
        key = (
            ref.blocker_type,
            ref.work_item_family_id,
            ref.work_item_id,
            ref.reason,
            ref.artifact_path,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(ref)
    return tuple(unique)


def _is_safe_identifier(value: str) -> bool:
    try:
        validate_safe_identifier(value, field_name="blocking_work_ids")
    except ValueError:
        return False
    return True


def _runtime_relative_path(paths: WorkspacePaths, path: Path) -> str:
    try:
        return path.relative_to(paths.runtime_root).as_posix()
    except ValueError:
        return path.as_posix()


def block_on_closure_lineage_drift_if_present(
    engine: RuntimeEngine,
    target: ClosureTargetState,
) -> bool:
    """Block closure activation when same-lineage work has drifted to another root id."""

    diagnostic = scan_closure_lineage_drift(
        engine.paths,
        target,
        detected_at=engine._now(),
    )
    if diagnostic is None:
        return False

    diagnostic_path = write_lineage_drift_diagnostic(engine.paths, diagnostic)
    _mark_lineage_drift_blocked(
        engine,
        target=target,
        diagnostic=diagnostic,
        diagnostic_path=diagnostic_path,
    )
    return True


def _completion_behavior_for(engine: RuntimeEngine) -> GraphLoopCompletionBehaviorDefinition | None:
    assert engine.compiled_plan is not None
    return engine.compiled_plan.planning_graph.completion_behavior


def _recover_or_diagnose_missing_closure_target(
    engine: RuntimeEngine,
) -> ClosureTargetState | None:
    candidate = _latest_root_spec_candidate(engine)
    if candidate is None:
        return None

    spec_path, spec = candidate
    if spec.root_spec_id is None:
        _mark_completion_behavior_blocked(
            engine,
            failure_class="missing_root_spec_id",
            spec_id=spec.spec_id,
            spec_path=spec_path,
        )
        return None

    existing_target = _existing_target_state(engine, root_spec_id=spec.root_spec_id)
    if existing_target is not None:
        return existing_target if existing_target.closure_open else None

    try:
        target = _open_closure_target_for_spec(engine, spec_path=spec_path, spec=spec)
    except RootSourceResolutionError as exc:
        _mark_completion_behavior_blocked(
            engine,
            failure_class=exc.failure_class,
            spec_id=spec.spec_id,
            spec_path=spec_path,
        )
        write_runtime_event(
            engine.paths,
            event_type="root_source_resolution_failed",
            data={
                "failure_class": exc.failure_class,
                "root_source_kind": exc.root_source_kind,
                "root_source_id": exc.root_source_id,
                "spec_id": spec.spec_id,
                "spec_path": _display_path(engine, spec_path),
                "candidates": [_display_path(engine, candidate) for candidate in exc.candidates],
            },
        )
        return None
    if target is not None:
        write_runtime_event(
            engine.paths,
            event_type="completion_behavior_target_backfilled",
            data={
                "root_spec_id": target.root_spec_id,
                "root_source_kind": target.root_source.kind,
                "root_source_id": target.root_source.id,
                "root_idea_id": target.root_idea_id,
                "spec_path": str(spec_path.relative_to(engine.paths.root)),
            },
        )
    return target


def _existing_target_state(engine: RuntimeEngine, *, root_spec_id: str) -> ClosureTargetState | None:
    try:
        return load_closure_target_state(engine.paths, root_spec_id=root_spec_id)
    except FileNotFoundError:
        return None


def _open_closure_target_for_spec(
    engine: RuntimeEngine,
    *,
    spec_path: Path,
    spec: SpecDocument,
) -> ClosureTargetState | None:
    preparation = _prepare_closure_target_for_spec(engine, spec_path=spec_path, spec=spec)
    if not preparation.allowed:
        raise WorkspaceStateError(
            "cannot open closure target while another open closure target exists"
        )
    return preparation.target


def _prepare_closure_target_for_spec(
    engine: RuntimeEngine,
    *,
    spec_path: Path,
    spec: SpecDocument,
) -> ClosureTargetPreparation:
    if spec.root_spec_id is None:
        return ClosureTargetPreparation(allowed=True)
    if spec.spec_id != spec.root_spec_id:
        return ClosureTargetPreparation(allowed=True)

    existing_target = _existing_target_state(engine, root_spec_id=spec.root_spec_id)
    if existing_target is not None:
        return ClosureTargetPreparation(allowed=True, target=existing_target)

    open_targets = list_open_closure_target_states(engine.paths)
    actionable_targets = _actionable_open_closure_targets(open_targets)
    if len(actionable_targets) > 1:
        raise WorkspaceStateError("multiple actionable open closure targets found")
    if actionable_targets:
        # Consult scheduler-policy backpressure before blocking.
        outcome = backpressure_outcome(
            engine.compiled_plan.scheduler_policy if engine.compiled_plan is not None else None,
            has_open_closure_target=True,
        )
        if outcome == "allow":
            target = _create_closure_target_for_spec(engine, spec_path=spec_path, spec=spec)
            return ClosureTargetPreparation(allowed=True, target=target)
        return ClosureTargetPreparation(
            allowed=False,
            open_root_spec_id=actionable_targets[0].root_spec_id,
            deferred_root_spec_id=spec.root_spec_id,
        )

    target = _create_closure_target_for_spec(engine, spec_path=spec_path, spec=spec)
    return ClosureTargetPreparation(allowed=True, target=target)


def _actionable_open_closure_targets(
    open_targets: tuple[ClosureTargetState, ...],
) -> tuple[ClosureTargetState, ...]:
    return tuple(
        target for target in open_targets if not target.closure_blocked_by_lineage_work
    )


def _unique_ids(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _create_closure_target_for_spec(
    engine: RuntimeEngine,
    *,
    spec_path: Path,
    spec: SpecDocument,
) -> ClosureTargetState:
    assert spec.root_spec_id is not None
    root_spec_id = spec.root_spec_id
    source_resolution = _resolve_root_source(engine, spec_path=spec_path, spec=spec)
    root_spec_markdown = spec_path.read_text(encoding="utf-8")
    root_source_contract = write_canonical_root_source_contract(
        engine.paths,
        root_source_kind=source_resolution.source.kind,
        root_source_id=source_resolution.source.id,
        markdown=source_resolution.markdown,
    )
    root_spec_contract = write_canonical_root_spec_contract(
        engine.paths,
        root_spec_id=root_spec_id,
        markdown=root_spec_markdown,
    )
    root_source = source_resolution.source.model_copy(
        update={"path": _workspace_relative_path(engine, root_source_contract)}
    )
    root_idea_id = root_source.id if root_source.kind == "idea" else None
    root_idea_path = None
    if root_idea_id is not None:
        idea_contract = write_canonical_idea_contract(
            engine.paths,
            root_idea_id=root_idea_id,
            markdown=source_resolution.markdown,
        )
        root_idea_path = _workspace_relative_path(engine, idea_contract)
    target = ClosureTargetState(
        root_spec_id=root_spec_id,
        root_source=root_source,
        root_idea_id=root_idea_id,
        root_spec_path=_workspace_relative_path(engine, root_spec_contract),
        root_idea_path=root_idea_path,
        root_intake_kind=root_source.intake_kind,
        root_intake_id=root_source.intake_id,
        rubric_path=f"millrace-agents/arbiter/rubrics/{root_spec_id}.md",
        latest_verdict_path=None,
        latest_report_path=None,
        closure_open=True,
        closure_blocked_by_lineage_work=False,
        blocking_work_ids=(),
        opened_at=engine._now(),
    )
    save_closure_target_state(engine.paths, target)
    return target


def _load_spec_document(path: Path) -> SpecDocument:
    return parse_work_document_as(
        path.read_text(encoding="utf-8"),
        model=SpecDocument,
        path=path,
    )


def _latest_root_spec_candidate(engine: RuntimeEngine) -> tuple[Path, SpecDocument] | None:
    candidates: list[tuple[SpecDocument, Path]] = []
    for directory in (
        engine.paths.specs_active_dir,
        engine.paths.specs_done_dir,
        engine.paths.specs_queue_dir,
        engine.paths.specs_blocked_dir,
    ):
        for path in sorted(directory.glob("*.md")):
            try:
                spec = _load_spec_document(path)
            except (FileNotFoundError, ValueError):
                continue
            if not _is_root_spec_candidate(spec):
                continue
            candidates.append((spec, path))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0].created_at, item[0].spec_id), reverse=True)
    spec, path = candidates[0]
    return path, spec


def _is_root_spec_candidate(spec: SpecDocument) -> bool:
    if spec.root_spec_id is not None and spec.spec_id == spec.root_spec_id:
        return True
    return spec.source_type in {"idea", "manual", "probe", "incident"} and not _has_parent_spec(spec)


def _has_parent_spec(spec: SpecDocument) -> bool:
    if spec.parent_spec_id is None:
        return False
    return spec.parent_spec_id.strip().lower() != "none"


def _resolve_root_source(
    engine: RuntimeEngine,
    *,
    spec_path: Path,
    spec: SpecDocument,
) -> RootSourceResolution:
    identity = _root_source_identity(spec)
    if identity is None:
        raise RootSourceResolutionError(
            failure_class="missing_root_source",
            root_source_kind=None,
            root_source_id=None,
        )

    root_source_kind, root_source_id = identity
    if root_source_kind not in _supported_root_source_kinds(engine):
        raise RootSourceResolutionError(
            failure_class="root_source_kind_unsupported",
            root_source_kind=root_source_kind,
            root_source_id=root_source_id,
        )

    if root_source_kind in {"manual", "spec"} and root_source_id == spec.spec_id:
        markdown = spec_path.read_text(encoding="utf-8")
    else:
        candidates = _root_source_candidate_groups(
            engine,
            kind=root_source_kind,
            source_id=root_source_id,
            spec=spec,
        )
        source_path = _select_root_source_candidate(
            candidates,
            root_source_kind=root_source_kind,
            root_source_id=root_source_id,
        )
        markdown = source_path.read_text(encoding="utf-8")

    return RootSourceResolution(
        source=ClosureRootSource(
            kind=root_source_kind,
            id=root_source_id,
            path=_canonical_root_source_contract_path(root_source_kind, root_source_id),
            title=spec.title,
            summary=spec.summary,
            intake_kind=spec.root_intake_kind,
            intake_id=spec.root_intake_id,
        ),
        markdown=markdown,
    )


def _root_source_identity(spec: SpecDocument) -> tuple[str, str] | None:
    if spec.root_idea_id is not None:
        return "idea", spec.root_idea_id
    if spec.root_intake_kind is not None and spec.root_intake_id is not None:
        return _root_source_kind_from_value(spec.root_intake_kind.value), spec.root_intake_id
    if spec.source_type == "idea" and spec.source_id is not None:
        return "idea", spec.source_id
    if spec.source_type == "probe" and spec.source_id is not None:
        return "probe", spec.source_id
    if spec.source_type == "incident" and spec.source_id is not None:
        return "incident", spec.source_id
    if spec.source_type == "manual":
        return "manual", spec.spec_id
    return None


def _root_source_kind_from_value(value: str) -> str:
    if value == "derived_spec":
        return "spec"
    return value


def _canonical_root_source_contract_path(kind: str, source_id: str) -> str:
    return f"millrace-agents/arbiter/contracts/root-sources/{kind}/{source_id}.md"


def _root_source_candidate_groups(
    engine: RuntimeEngine,
    *,
    kind: str,
    source_id: str,
    spec: SpecDocument,
) -> tuple[tuple[Path, ...], ...]:
    if kind == "idea":
        return _root_idea_source_candidate_groups(engine, spec)

    durable = (
        engine.paths.intake_sources_dir / kind / f"{source_id}.md",
        engine.paths.intake_dir / f"{kind}s" / f"{source_id}.md",
    )
    lifecycle = _root_source_lifecycle_candidates(engine, kind=kind, source_id=source_id)
    references = _root_source_reference_candidates(
        engine,
        kind=kind,
        source_id=source_id,
        spec=spec,
    )
    return (durable, lifecycle, references)


def _root_source_lifecycle_candidates(
    engine: RuntimeEngine,
    *,
    kind: str,
    source_id: str,
) -> tuple[Path, ...]:
    filename = f"{source_id}.md"
    if kind == "probe":
        return (
            engine.paths.probes_done_dir / filename,
            engine.paths.probes_active_dir / filename,
            engine.paths.probes_blocked_dir / filename,
            engine.paths.probes_queue_dir / filename,
        )
    if kind == "incident":
        return (
            engine.paths.incidents_resolved_dir / filename,
            engine.paths.incidents_active_dir / filename,
            engine.paths.incidents_blocked_dir / filename,
            engine.paths.incidents_incoming_dir / filename,
        )
    if kind == "spec":
        return (
            engine.paths.specs_done_dir / filename,
            engine.paths.specs_active_dir / filename,
            engine.paths.specs_blocked_dir / filename,
            engine.paths.specs_queue_dir / filename,
        )
    return ()


def _select_root_source_candidate(
    candidate_groups: tuple[tuple[Path, ...], ...],
    *,
    root_source_kind: str,
    root_source_id: str,
) -> Path:
    all_candidates: list[Path] = []
    for candidates in candidate_groups:
        existing = tuple(path for path in _existing_ordered_paths(candidates) if path.is_file())
        all_candidates.extend(existing)
        if len(existing) > 1:
            raise RootSourceResolutionError(
                failure_class="root_source_ambiguous",
                root_source_kind=root_source_kind,
                root_source_id=root_source_id,
                candidates=existing,
            )
        if existing:
            return existing[0]
    raise RootSourceResolutionError(
        failure_class="root_source_unresolved",
        root_source_kind=root_source_kind,
        root_source_id=root_source_id,
        candidates=tuple(all_candidates) or tuple(
            path for group in candidate_groups for path in group
        ),
    )


def _existing_ordered_paths(candidates: tuple[Path, ...]) -> tuple[Path, ...]:
    return tuple(dict.fromkeys(candidates))


def _root_idea_source_candidate_groups(
    engine: RuntimeEngine,
    spec: SpecDocument,
) -> tuple[tuple[Path, ...], ...]:
    root_idea_id = spec.root_idea_id or spec.source_id
    durable: list[Path] = []
    if root_idea_id is not None:
        durable.append(idea_source_artifact_path(engine.paths, root_idea_id=root_idea_id))
        durable.append(engine.paths.intake_sources_dir / "idea" / f"{root_idea_id}.md")

    references = tuple(
        candidate
        for reference in spec.references
        if (candidate := _resolve_workspace_reference_path(engine, reference)) is not None
    )

    legacy: list[Path] = []
    if spec.source_id is not None:
        legacy.append(engine.paths.root / "ideas" / "inbox" / f"{spec.source_id}.md")
    if root_idea_id is not None:
        legacy.append(engine.paths.root / "ideas" / "inbox" / f"{root_idea_id}.md")
    return (
        tuple(_existing_ordered_paths(tuple(durable))),
        tuple(_existing_ordered_paths(tuple(legacy))),
        references,
    )


def _root_source_reference_candidates(
    engine: RuntimeEngine,
    *,
    kind: str,
    source_id: str,
    spec: SpecDocument,
) -> tuple[Path, ...]:
    candidates: list[Path] = []
    for reference in spec.references:
        candidate = _resolve_workspace_reference_path(engine, reference)
        if candidate is None:
            continue
        if kind == "idea" or _reference_matches_root_source(candidate, kind=kind, source_id=source_id):
            candidates.append(candidate)
    return tuple(_existing_ordered_paths(tuple(candidates)))


def _reference_matches_root_source(path: Path, *, kind: str, source_id: str) -> bool:
    if not path.is_file():
        return path.name == f"{source_id}.md"
    try:
        if kind == "probe":
            return (
                parse_work_document_as(
                    path.read_text(encoding="utf-8"),
                    model=ProbeDocument,
                    path=path,
                ).probe_id
                == source_id
            )
        if kind == "incident":
            return (
                parse_work_document_as(
                    path.read_text(encoding="utf-8"),
                    model=IncidentDocument,
                    path=path,
                ).incident_id
                == source_id
            )
        if kind == "spec":
            return (
                parse_work_document_as(
                    path.read_text(encoding="utf-8"),
                    model=SpecDocument,
                    path=path,
                ).spec_id
                == source_id
            )
    except (OSError, ValueError):
        return False
    return False


def _supported_root_source_kinds(engine: RuntimeEngine) -> frozenset[str]:
    completion_behavior = _completion_behavior_for(engine)
    if completion_behavior is None:
        return _SUPPORTED_ROOT_SOURCE_KINDS
    return frozenset(completion_behavior.root_source_policy.accepted_kinds)


def _resolve_workspace_reference_path(engine: RuntimeEngine, reference: str) -> Path | None:
    if reference.startswith(("http://", "https://")):
        return None
    candidate = Path(reference)
    resolved = candidate if candidate.is_absolute() else engine.paths.root / candidate
    try:
        resolved.resolve().relative_to(engine.paths.root.resolve())
    except (OSError, ValueError):
        return None
    return resolved


def _workspace_relative_path(engine: RuntimeEngine, path: Path) -> str:
    return str(path.relative_to(engine.paths.root))


def _display_path(engine: RuntimeEngine, path: Path) -> str:
    try:
        return str(path.relative_to(engine.paths.root))
    except ValueError:
        return path.as_posix()


def _mark_completion_behavior_blocked(
    engine: RuntimeEngine,
    *,
    failure_class: str,
    spec_id: str,
    spec_path: Path,
) -> None:
    assert engine.snapshot is not None
    if (
        engine.snapshot.planning_status_marker == "### BLOCKED"
        and engine.snapshot.current_failure_class == failure_class
    ):
        return

    engine.snapshot = engine.snapshot.model_copy(
        update={
            "current_failure_class": failure_class,
            "updated_at": engine._now(),
        }
    )
    engine._set_plane_status_marker(
        plane=Plane.PLANNING,
        marker="### BLOCKED",
        run_id=None,
        source="completion_behavior_blocked",
    )
    write_runtime_event(
        engine.paths,
        event_type="completion_behavior_blocked",
        data={
            "reason": failure_class,
            "spec_id": spec_id,
            "spec_path": str(spec_path.relative_to(engine.paths.root)),
        },
    )


def _mark_lineage_drift_blocked(
    engine: RuntimeEngine,
    *,
    target: ClosureTargetState,
    diagnostic: LineageDriftDiagnostic,
    diagnostic_path: Path,
) -> None:
    blocking_work_ids = tuple(finding.work_item_id for finding in diagnostic.findings)
    blocking_work_refs = tuple(
        ClosureBlockingWorkRef(
            blocker_type="work_item",
            reason="closure_lineage_drift",
            work_item_family_id=finding.work_item_kind.value,
            work_item_kind=finding.work_item_kind,
            work_item_id=finding.work_item_id,
            root_spec_id=finding.actual_root_spec_id or finding.expected_root_spec_id,
            artifact_path=finding.path,
            detail=f"expected_root_spec_id={finding.expected_root_spec_id}",
        )
        for finding in diagnostic.findings
    )
    updated_target = target.model_copy(
        update={
            "closure_blocked_by_lineage_work": True,
            "blocking_work_ids": blocking_work_ids,
            "blocking_work_refs": blocking_work_refs,
        }
    )
    save_closure_target_state(engine.paths, updated_target)
    _mark_completion_behavior_blocked(
        engine,
        failure_class="closure_lineage_drift",
        spec_id=target.root_spec_id,
        spec_path=diagnostic_path,
    )
    write_runtime_event(
        engine.paths,
        event_type="closure_lineage_drift_detected",
        data={
            "root_spec_id": diagnostic.root_spec_id,
            "root_idea_id": diagnostic.root_idea_id,
            "finding_count": len(diagnostic.findings),
            "diagnostic_path": str(diagnostic_path.relative_to(engine.paths.root)),
        },
    )


__all__ = [
    "ClosureTargetPreparation",
    "RootSourceResolution",
    "RootSourceResolutionError",
    "active_closure_target",
    "block_on_closure_lineage_drift_if_present",
    "maybe_activate_completion_stage",
    "maybe_open_closure_target_for_claim",
    "prepare_closure_target_for_claim",
    "refresh_closure_target_readiness",
]
