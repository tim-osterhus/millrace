"""Compiler-driven closure-target lifecycle and backlog-drain activation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.architecture import GraphLoopCompletionBehaviorDefinition
from millrace_ai.contracts import ClosureTargetState, Plane, SpecDocument, WorkItemKind
from millrace_ai.errors import WorkspaceStateError
from millrace_ai.events import write_runtime_event
from millrace_ai.queue_store import QueueClaim
from millrace_ai.state_store import save_snapshot
from millrace_ai.workspace.arbiter_state import (
    list_open_closure_target_states,
    load_closure_target_state,
    save_closure_target_state,
    write_canonical_idea_contract,
    write_canonical_root_spec_contract,
)
from millrace_ai.workspace.lineage_integrity import (
    LineageDriftDiagnostic,
    scan_closure_lineage_drift,
    write_lineage_drift_diagnostic,
)
from millrace_ai.workspace.queue_selection import list_open_lineage_work_ids
from millrace_ai.workspace.work_documents import parse_work_document_as

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine

from .active_runs import active_run_from_closure_target, snapshot_with_active_run
from .graph_authority import completion_activation_for_graph


@dataclass(frozen=True, slots=True)
class ClosureTargetPreparation:
    """Result of closure-target preflight for a queued work-item claim."""

    allowed: bool
    target: ClosureTargetState | None = None
    open_root_spec_id: str | None = None
    deferred_root_spec_id: str | None = None


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
        run_id=engine._new_run_id(),
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
    if not open_targets:
        return None
    if len(open_targets) > 1:
        raise WorkspaceStateError("multiple open closure targets found")
    return open_targets[0]


def refresh_closure_target_readiness(
    engine: RuntimeEngine,
    target: ClosureTargetState,
) -> ClosureTargetState:
    blocking_work_ids = list_open_lineage_work_ids(
        engine.paths,
        root_spec_id=target.root_spec_id,
    )
    updated = target.model_copy(
        update={
            "closure_blocked_by_lineage_work": bool(blocking_work_ids),
            "blocking_work_ids": blocking_work_ids,
        }
    )
    save_closure_target_state(engine.paths, updated)
    return updated


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
    if spec.root_spec_id is None or spec.root_idea_id is None:
        _mark_completion_behavior_blocked(
            engine,
            failure_class="missing_root_lineage",
            spec_id=spec.spec_id,
            spec_path=spec_path,
        )
        return None

    existing_target = _existing_target_state(engine, root_spec_id=spec.root_spec_id)
    if existing_target is not None:
        return existing_target if existing_target.closure_open else None

    target = _open_closure_target_for_spec(engine, spec_path=spec_path, spec=spec)
    if target is not None:
        write_runtime_event(
            engine.paths,
            event_type="completion_behavior_target_backfilled",
            data={
                "root_spec_id": target.root_spec_id,
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
    if spec.root_spec_id is None or spec.root_idea_id is None:
        return ClosureTargetPreparation(allowed=True)
    if spec.spec_id != spec.root_spec_id:
        return ClosureTargetPreparation(allowed=True)

    existing_target = _existing_target_state(engine, root_spec_id=spec.root_spec_id)
    if existing_target is not None:
        return ClosureTargetPreparation(allowed=True, target=existing_target)

    open_targets = list_open_closure_target_states(engine.paths)
    if len(open_targets) > 1:
        raise WorkspaceStateError("multiple open closure targets found")
    if open_targets:
        return ClosureTargetPreparation(
            allowed=False,
            open_root_spec_id=open_targets[0].root_spec_id,
            deferred_root_spec_id=spec.root_spec_id,
        )

    target = _create_closure_target_for_spec(engine, spec_path=spec_path, spec=spec)
    return ClosureTargetPreparation(allowed=True, target=target)


def _create_closure_target_for_spec(
    engine: RuntimeEngine,
    *,
    spec_path: Path,
    spec: SpecDocument,
) -> ClosureTargetState:
    assert spec.root_idea_id is not None
    assert spec.root_spec_id is not None
    root_idea_id = spec.root_idea_id
    root_spec_id = spec.root_spec_id
    idea_markdown = _load_root_idea_markdown(engine, spec)
    root_spec_markdown = spec_path.read_text(encoding="utf-8")
    idea_contract = write_canonical_idea_contract(
        engine.paths,
        root_idea_id=root_idea_id,
        markdown=idea_markdown,
    )
    root_spec_contract = write_canonical_root_spec_contract(
        engine.paths,
        root_spec_id=root_spec_id,
        markdown=root_spec_markdown,
    )
    target = ClosureTargetState(
        root_spec_id=root_spec_id,
        root_idea_id=root_idea_id,
        root_spec_path=_workspace_relative_path(engine, root_spec_contract),
        root_idea_path=_workspace_relative_path(engine, idea_contract),
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
    return spec.source_type in {"idea", "manual"} and not _has_parent_spec(spec)


def _has_parent_spec(spec: SpecDocument) -> bool:
    if spec.parent_spec_id is None:
        return False
    return spec.parent_spec_id.strip().lower() != "none"


def _load_root_idea_markdown(engine: RuntimeEngine, spec: SpecDocument) -> str:
    for candidate in _root_idea_source_candidates(engine, spec):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    raise WorkspaceStateError(
        f"could not resolve source idea markdown for root_idea_id={spec.root_idea_id}"
    )


def _root_idea_source_candidates(engine: RuntimeEngine, spec: SpecDocument) -> tuple[Path, ...]:
    candidates: list[Path] = []
    for reference in spec.references:
        resolved = _resolve_reference_path(engine, reference)
        if resolved not in candidates:
            candidates.append(resolved)
    if spec.source_id is not None:
        source_candidate = engine.paths.root / "ideas" / "inbox" / f"{spec.source_id}.md"
        if source_candidate not in candidates:
            candidates.append(source_candidate)
    root_candidate = engine.paths.root / "ideas" / "inbox" / f"{spec.root_idea_id}.md"
    if root_candidate not in candidates:
        candidates.append(root_candidate)
    return tuple(candidates)


def _resolve_reference_path(engine: RuntimeEngine, reference: str) -> Path:
    candidate = Path(reference)
    if candidate.is_absolute():
        return candidate
    return engine.paths.root / candidate


def _workspace_relative_path(engine: RuntimeEngine, path: Path) -> str:
    return str(path.relative_to(engine.paths.root))


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
    updated_target = target.model_copy(
        update={
            "closure_blocked_by_lineage_work": True,
            "blocking_work_ids": blocking_work_ids,
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
    "active_closure_target",
    "block_on_closure_lineage_drift_if_present",
    "maybe_activate_completion_stage",
    "maybe_open_closure_target_for_claim",
    "prepare_closure_target_for_claim",
    "refresh_closure_target_readiness",
]
