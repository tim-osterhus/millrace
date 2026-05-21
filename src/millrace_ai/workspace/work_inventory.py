"""Read-only inventory for graph-owned work-item families."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from millrace_ai.architecture import CompiledRunPlan, WorkItemFamilyDefinition
from millrace_ai.assets import load_builtin_workflow_primitives
from millrace_ai.contracts import Plane

from .paths import WorkspacePaths


@dataclass(frozen=True, slots=True)
class WorkInventoryItemRef:
    family_id: str
    plane: Plane
    state: str
    path: Path
    work_item_id: str


@dataclass(frozen=True, slots=True)
class WorkInventory:
    family_counts: dict[str, dict[str, int]]
    queue_depths_by_plane: dict[Plane, int]
    active_counts_by_plane: dict[Plane, int]
    blocked_counts_by_plane: dict[Plane, int]
    closure_blocking_refs: tuple[WorkInventoryItemRef, ...]


def build_work_inventory(
    paths: WorkspacePaths,
    *,
    compiled_plan: CompiledRunPlan | None = None,
    root_spec_id: str | None = None,
) -> WorkInventory:
    counts = family_counts(paths, compiled_plan=compiled_plan)
    return WorkInventory(
        family_counts=counts,
        queue_depths_by_plane=queue_depths_by_plane(
            paths,
            compiled_plan=compiled_plan,
            family_counts_by_id=counts,
        ),
        active_counts_by_plane=active_counts_by_plane(
            paths,
            compiled_plan=compiled_plan,
            family_counts_by_id=counts,
        ),
        blocked_counts_by_plane=blocked_counts_by_plane(
            paths,
            compiled_plan=compiled_plan,
            family_counts_by_id=counts,
        ),
        closure_blocking_refs=closure_blocking_refs(
            paths,
            root_spec_id=root_spec_id,
            compiled_plan=compiled_plan,
        ),
    )


def family_counts(
    paths: WorkspacePaths,
    *,
    compiled_plan: CompiledRunPlan | None = None,
) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for family in _work_item_families(compiled_plan):
        family_state_counts: dict[str, int] = {}
        for dir_key, directory in _family_state_dirs(paths, family):
            count = _count_family_files(directory, family)
            family_state_counts[dir_key] = count
            semantic_state = _semantic_state_for_dir_key(family, dir_key)
            if semantic_state != dir_key:
                family_state_counts[semantic_state] = count
        counts[family.family_id] = family_state_counts
    return counts


def queue_depths_by_plane(
    paths: WorkspacePaths,
    *,
    compiled_plan: CompiledRunPlan | None = None,
    family_counts_by_id: dict[str, dict[str, int]] | None = None,
) -> dict[Plane, int]:
    return _counts_by_plane_for_dir_key(
        paths,
        "queue",
        compiled_plan=compiled_plan,
        family_counts_by_id=family_counts_by_id,
    )


def active_counts_by_plane(
    paths: WorkspacePaths,
    *,
    compiled_plan: CompiledRunPlan | None = None,
    family_counts_by_id: dict[str, dict[str, int]] | None = None,
) -> dict[Plane, int]:
    return _counts_by_plane_for_dir_key(
        paths,
        "active",
        compiled_plan=compiled_plan,
        family_counts_by_id=family_counts_by_id,
    )


def blocked_counts_by_plane(
    paths: WorkspacePaths,
    *,
    compiled_plan: CompiledRunPlan | None = None,
    family_counts_by_id: dict[str, dict[str, int]] | None = None,
) -> dict[Plane, int]:
    return _counts_by_plane_for_dir_key(
        paths,
        "blocked",
        compiled_plan=compiled_plan,
        family_counts_by_id=family_counts_by_id,
    )


def closure_blocking_refs(
    paths: WorkspacePaths,
    *,
    root_spec_id: str | None = None,
    compiled_plan: CompiledRunPlan | None = None,
) -> tuple[WorkInventoryItemRef, ...]:
    refs: list[WorkInventoryItemRef] = []
    for family in _work_item_families(compiled_plan):
        dir_key_by_semantic_state = {
            _semantic_state_for_dir_key(family, dir_key): dir_key
            for dir_key, _directory in _family_state_dirs(paths, family)
        }
        for state in family.closure_blocking_states:
            dir_key = dir_key_by_semantic_state.get(state)
            if dir_key is None:
                continue
            directory = paths.runtime_root / getattr(family.queue_dirs, dir_key)
            for path in _family_files(directory, family):
                if not _document_matches_root_spec_id(path, family, root_spec_id):
                    continue
                refs.append(
                    WorkInventoryItemRef(
                        family_id=family.family_id,
                        plane=family.plane,
                        state=state,
                        path=path,
                        work_item_id=path.stem,
                    )
                )
    return tuple(sorted(refs, key=lambda ref: (ref.plane.value, ref.family_id, ref.state, ref.work_item_id)))


def _counts_by_plane_for_dir_key(
    paths: WorkspacePaths,
    dir_key: str,
    *,
    compiled_plan: CompiledRunPlan | None,
    family_counts_by_id: dict[str, dict[str, int]] | None,
) -> dict[Plane, int]:
    counts = family_counts_by_id or family_counts(paths, compiled_plan=compiled_plan)
    by_plane = {Plane.EXECUTION: 0, Plane.PLANNING: 0, Plane.LEARNING: 0}
    for family in _work_item_families(compiled_plan):
        by_plane[family.plane] += counts.get(family.family_id, {}).get(dir_key, 0)
    return by_plane


def _work_item_families(
    compiled_plan: CompiledRunPlan | None,
) -> tuple[WorkItemFamilyDefinition, ...]:
    if compiled_plan is not None and compiled_plan.work_item_families_by_id:
        return tuple(compiled_plan.work_item_families_by_id.values())
    return load_builtin_workflow_primitives().work_item_families


def _family_state_dirs(
    paths: WorkspacePaths,
    family: WorkItemFamilyDefinition,
) -> tuple[tuple[str, Path], ...]:
    dirs: list[tuple[str, Path]] = []
    for dir_key in ("queue", "active", "done", "blocked", "canceled", "superseded"):
        relative = getattr(family.queue_dirs, dir_key)
        if relative is None:
            continue
        dirs.append((dir_key, paths.runtime_root / relative))
    return tuple(dirs)


def _semantic_state_for_dir_key(family: WorkItemFamilyDefinition, dir_key: str) -> str:
    if dir_key == "queue":
        return family.claimable_state
    if dir_key == "active":
        return family.active_state
    if dir_key == "done":
        return family.done_state
    if dir_key == "blocked":
        return family.blocked_state
    if dir_key == "canceled" and family.canceled_state is not None:
        return family.canceled_state
    return dir_key


def _count_family_files(directory: Path, family: WorkItemFamilyDefinition) -> int:
    return len(tuple(_family_files(directory, family)))


def _family_files(directory: Path, family: WorkItemFamilyDefinition) -> tuple[Path, ...]:
    if not directory.exists():
        return ()
    return tuple(sorted(path for path in directory.glob(f"*{family.file_extension}") if path.is_file()))


def _document_matches_root_spec_id(
    path: Path,
    family: WorkItemFamilyDefinition,
    root_spec_id: str | None,
) -> bool:
    if root_spec_id is None or not family.lineage_fields:
        return True
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return True
    if path.suffix == ".json":
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return True
        if not isinstance(payload, dict):
            return True
        lineage_values = [
            payload.get(field)
            for field in (*family.lineage_fields, "root_spec_id")
            if isinstance(payload.get(field), str) and payload.get(field)
        ]
        if not lineage_values:
            return True
        return root_spec_id in lineage_values

    root_line_prefix = "Root-Spec-ID:"
    for line in raw.splitlines():
        if not line.startswith(root_line_prefix):
            continue
        value = line.removeprefix(root_line_prefix).strip()
        return not value or value == root_spec_id
    return True


__all__ = [
    "WorkInventory",
    "WorkInventoryItemRef",
    "active_counts_by_plane",
    "blocked_counts_by_plane",
    "build_work_inventory",
    "closure_blocking_refs",
    "family_counts",
    "queue_depths_by_plane",
]
