"""Compiler-driven closure-target lifecycle and backlog-drain activation helpers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.contracts import ClosureTargetState, CompletionBehaviorDefinition, FrozenStagePlan, SpecDocument, WorkItemKind
from millrace_ai.errors import WorkspaceStateError
from millrace_ai.queue_store import QueueClaim
from millrace_ai.state_store import save_snapshot
from millrace_ai.workspace.arbiter_state import (
    list_open_closure_target_states,
    load_closure_target_state,
    save_closure_target_state,
    write_canonical_idea_contract,
    write_canonical_root_spec_contract,
)
from millrace_ai.workspace.queue_selection import list_open_lineage_work_ids
from millrace_ai.workspace.work_documents import parse_work_document_as

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine


def maybe_open_closure_target_for_claim(
    engine: RuntimeEngine,
    claim: QueueClaim,
) -> ClosureTargetState | None:
    if claim.work_item_kind is not WorkItemKind.SPEC:
        return None

    spec = _load_spec_document(claim.path)
    if spec.root_spec_id is None or spec.root_idea_id is None:
        return None
    if spec.spec_id != spec.root_spec_id:
        return None

    existing_target = _existing_target_state(engine, root_spec_id=spec.root_spec_id)
    if existing_target is not None and existing_target.closure_open:
        return existing_target

    open_targets = list_open_closure_target_states(engine.paths)
    if open_targets:
        raise WorkspaceStateError("cannot open closure target while another open closure target exists")

    idea_markdown = _load_root_idea_markdown(engine, spec)
    root_spec_markdown = claim.path.read_text(encoding="utf-8")
    idea_contract = write_canonical_idea_contract(
        engine.paths,
        root_idea_id=spec.root_idea_id,
        markdown=idea_markdown,
    )
    root_spec_contract = write_canonical_root_spec_contract(
        engine.paths,
        root_spec_id=spec.root_spec_id,
        markdown=root_spec_markdown,
    )
    target = ClosureTargetState(
        root_spec_id=spec.root_spec_id,
        root_idea_id=spec.root_idea_id,
        root_spec_path=_workspace_relative_path(engine, root_spec_contract),
        root_idea_path=_workspace_relative_path(engine, idea_contract),
        rubric_path=f"millrace-agents/arbiter/rubrics/{spec.root_spec_id}.md",
        latest_verdict_path=None,
        latest_report_path=None,
        closure_open=True,
        closure_blocked_by_lineage_work=False,
        blocking_work_ids=(),
        opened_at=engine._now(),
    )
    save_closure_target_state(engine.paths, target)
    return target


def maybe_activate_completion_stage(engine: RuntimeEngine) -> ClosureTargetState | None:
    assert engine.snapshot is not None
    completion_behavior = _completion_behavior_for(engine)
    if completion_behavior is None:
        return None

    target = active_closure_target(engine)
    if target is None:
        return None
    if completion_behavior.skip_if_already_closed and not target.closure_open:
        return None

    target = refresh_closure_target_readiness(engine, target)
    if target.closure_blocked_by_lineage_work:
        return None

    stage_plan = _completion_stage_plan(engine, completion_behavior)
    engine.snapshot = engine.snapshot.model_copy(
        update={
            "active_plane": stage_plan.plane,
            "active_stage": stage_plan.stage,
            "active_run_id": engine._new_run_id(),
            "active_work_item_kind": None,
            "active_work_item_id": None,
            "active_since": engine._now(),
            "current_failure_class": None,
            "updated_at": engine._now(),
        }
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


def _completion_behavior_for(engine: RuntimeEngine) -> CompletionBehaviorDefinition | None:
    assert engine.compiled_plan is not None
    return engine.compiled_plan.completion_behavior


def _completion_stage_plan(
    engine: RuntimeEngine,
    completion_behavior: CompletionBehaviorDefinition,
) -> FrozenStagePlan:
    assert engine.compiled_plan is not None
    for stage_plan in engine.compiled_plan.stage_plans:
        if stage_plan.stage == completion_behavior.stage:
            return stage_plan
    raise WorkspaceStateError(
        f"completion stage {completion_behavior.stage.value} is missing from compiled stage plans"
    )


def _existing_target_state(engine: RuntimeEngine, *, root_spec_id: str) -> ClosureTargetState | None:
    try:
        return load_closure_target_state(engine.paths, root_spec_id=root_spec_id)
    except FileNotFoundError:
        return None


def _load_spec_document(path: Path) -> SpecDocument:
    return parse_work_document_as(
        path.read_text(encoding="utf-8"),
        model=SpecDocument,
        path=path,
    )


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


__all__ = [
    "active_closure_target",
    "maybe_activate_completion_stage",
    "maybe_open_closure_target_for_claim",
    "refresh_closure_target_readiness",
]
