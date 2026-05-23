"""JSON-backed state helpers for Blueprint Planning loop artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, TypeVar

from millrace_ai.contracts import (
    BlueprintCritiqueDocument,
    BlueprintDraftDocument,
    BlueprintEvaluationDocument,
    BlueprintManifestDocument,
    BlueprintPacketDocument,
    BlueprintPromotionRecord,
    ClosureBlockingWorkRef,
    WorkItemKind,
)
from millrace_ai.errors import QueueStateError

from .paths import WorkspacePaths
from .queue_claims import QueueClaim

PacketState = Literal["candidates", "approved", "rejected", "superseded"]
CritiqueState = Literal["open", "resolved"]

_BlueprintModelT = TypeVar(
    "_BlueprintModelT",
    BlueprintManifestDocument,
    BlueprintDraftDocument,
    BlueprintPacketDocument,
    BlueprintCritiqueDocument,
    BlueprintEvaluationDocument,
    BlueprintPromotionRecord,
)


def write_blueprint_manifest(
    paths: WorkspacePaths,
    manifest: BlueprintManifestDocument,
) -> Path:
    destination = blueprint_manifest_path(paths, manifest.manifest_id)
    existing_entries = _blueprint_manifest_entries_for_id(paths, manifest.manifest_id)
    if existing_entries:
        _resolve_blueprint_manifest_entries(manifest.manifest_id, existing_entries)
        expected_payload = _normalized_blueprint_manifest_payload(manifest)
        if any(
            _normalized_blueprint_manifest_payload(existing) != expected_payload
            for _path, existing in existing_entries
        ):
            _raise_blueprint_manifest_duplicate(manifest.manifest_id, existing_entries)
        if destination.exists():
            return destination
    return _write_unique_document(destination, manifest)


def blueprint_manifest_path(paths: WorkspacePaths, manifest_id: str) -> Path:
    return _blueprint_manifest_dir(paths) / f"{manifest_id}.json"


def read_blueprint_manifest(
    paths: WorkspacePaths,
    manifest_id: str,
) -> BlueprintManifestDocument:
    entries = _blueprint_manifest_entries_for_id(paths, manifest_id)
    if not entries:
        raise QueueStateError(f"blueprint_manifest_missing: {manifest_id}")
    return _resolve_blueprint_manifest_entry(manifest_id, entries)[1]


def resolve_blueprint_manifest_path(
    paths: WorkspacePaths,
    manifest_id: str,
) -> Path:
    entries = _blueprint_manifest_entries_for_id(paths, manifest_id)
    if not entries:
        raise QueueStateError(f"blueprint_manifest_missing: {manifest_id}")
    return _resolve_blueprint_manifest_entry(manifest_id, entries)[0]


def list_blueprint_manifests(paths: WorkspacePaths) -> tuple[BlueprintManifestDocument, ...]:
    grouped: dict[str, list[tuple[Path, BlueprintManifestDocument]]] = {}
    for path in _list_json_files(_blueprint_manifest_dir(paths)):
        manifest = _read_blueprint_manifest_file(path)
        grouped.setdefault(manifest.manifest_id, []).append((path, manifest))
    return tuple(
        _resolve_blueprint_manifest_entries(manifest_id, grouped[manifest_id])
        for manifest_id in sorted(grouped)
    )


def list_blueprint_manifests_for_root(
    paths: WorkspacePaths,
    root_spec_id: str,
) -> tuple[BlueprintManifestDocument, ...]:
    return tuple(
        manifest
        for manifest in list_blueprint_manifests(paths)
        if manifest.root_spec_id == root_spec_id
    )


def enqueue_blueprint_draft(paths: WorkspacePaths, draft: BlueprintDraftDocument) -> Path:
    _ensure_unique_blueprint_draft(paths, draft.draft_id)
    queued = draft.model_copy(update={"status": "queued"})
    return _write_unique_document(_draft_dir(paths, "queue") / f"{draft.draft_id}.json", queued)


def claim_next_blueprint_draft(
    paths: WorkspacePaths,
    *,
    root_spec_id: str | None = None,
    claim_policy_id: str | None = None,
    claim_order: int | None = None,
) -> QueueClaim | None:
    active = _list_json_files(_draft_dir(paths, "active"))
    if len(active) > 1:
        raise QueueStateError("Multiple active blueprint drafts found")
    if active:
        return None

    while True:
        candidate = _select_next_eligible_draft(paths, root_spec_id=root_spec_id)
        if candidate is None:
            return None

        draft, source = candidate
        destination = _draft_dir(paths, "active") / source.name
        active_draft = draft.model_copy(update={"status": "active"})
        try:
            _write_unique_document(destination, active_draft)
            source.unlink()
        except FileNotFoundError:
            if destination.exists():
                destination.unlink()
            continue
        return QueueClaim(
            work_item_kind=WorkItemKind.BLUEPRINT_DRAFT,
            work_item_id=draft.draft_id,
            path=destination,
            source_state="queued",
            source_path=source,
            claim_policy_id=claim_policy_id,
            claim_order=claim_order,
        )


def approve_active_blueprint_draft(paths: WorkspacePaths, draft_id: str) -> Path:
    return _move_blueprint_draft(
        paths,
        draft_id,
        source_state="active",
        target_state="approved",
        status="approved",
    )


def block_active_blueprint_draft(paths: WorkspacePaths, draft_id: str) -> Path:
    return _move_blueprint_draft(
        paths,
        draft_id,
        source_state="active",
        target_state="blocked",
        status="blocked",
    )


def requeue_active_blueprint_draft(paths: WorkspacePaths, draft_id: str) -> Path:
    return _move_blueprint_draft(
        paths,
        draft_id,
        source_state="active",
        target_state="queue",
        status="queued",
    )


def cancel_blueprint_draft(paths: WorkspacePaths, draft_id: str) -> Path:
    for source_state in ("queue", "active", "blocked"):
        source = _draft_dir(paths, source_state) / f"{draft_id}.json"
        if source.exists():
            return _move_blueprint_draft(
                paths,
                draft_id,
                source_state=source_state,
                target_state="canceled",
                status="canceled",
            )
    raise QueueStateError(f"blueprint draft {draft_id} not found")


def read_active_blueprint_draft(paths: WorkspacePaths, draft_id: str) -> BlueprintDraftDocument:
    source = _draft_dir(paths, "active") / f"{draft_id}.json"
    if not source.exists():
        raise QueueStateError(f"active blueprint draft {draft_id} not found")
    return read_blueprint_draft(source)


def update_active_blueprint_draft(
    paths: WorkspacePaths,
    draft: BlueprintDraftDocument,
) -> Path:
    destination = _draft_dir(paths, "active") / f"{draft.draft_id}.json"
    if not destination.exists():
        raise QueueStateError(f"active blueprint draft {draft.draft_id} not found")
    _write_document(destination, draft.model_copy(update={"status": "active"}))
    return destination


def persist_blueprint_packet(
    paths: WorkspacePaths,
    packet: BlueprintPacketDocument,
    *,
    packet_state: PacketState = "candidates",
) -> Path:
    destination = blueprint_packet_path(
        paths,
        packet.blueprint_id,
        packet_state=packet_state,
    )
    return _write_unique_document(destination, packet)


def blueprint_packet_path(
    paths: WorkspacePaths,
    blueprint_id: str,
    *,
    packet_state: PacketState = "candidates",
) -> Path:
    return _blueprints_dir(paths) / "packets" / packet_state / f"{blueprint_id}.json"


def read_blueprint_packet(
    paths: WorkspacePaths,
    blueprint_id: str,
    *,
    packet_state: PacketState = "candidates",
) -> BlueprintPacketDocument:
    source = blueprint_packet_path(paths, blueprint_id, packet_state=packet_state)
    if not source.exists():
        raise QueueStateError(f"blueprint packet {blueprint_id} not found")
    return BlueprintPacketDocument.model_validate_json(source.read_text(encoding="utf-8"))


def move_candidate_blueprint_packet(
    paths: WorkspacePaths,
    blueprint_id: str,
    *,
    target_state: Literal["approved", "rejected"],
) -> Path:
    source = _blueprints_dir(paths) / "packets" / "candidates" / f"{blueprint_id}.json"
    if not source.exists():
        raise QueueStateError(f"candidate blueprint packet {blueprint_id} not found")
    destination = _blueprints_dir(paths) / "packets" / target_state / source.name
    if destination.exists():
        raise QueueStateError(f"blueprint packet {blueprint_id} already exists at destination")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.replace(destination)
    return destination


def persist_blueprint_critique(
    paths: WorkspacePaths,
    critique: BlueprintCritiqueDocument,
    *,
    critique_state: CritiqueState = "open",
) -> Path:
    destination = _blueprints_dir(paths) / "critiques" / critique_state / f"{critique.critique_id}.json"
    return _write_unique_document(destination, critique)


def persist_blueprint_evaluation(
    paths: WorkspacePaths,
    evaluation: BlueprintEvaluationDocument,
) -> Path:
    destination = _blueprints_dir(paths) / "evaluations" / f"{evaluation.evaluation_id}.json"
    return _write_unique_document(destination, evaluation)


def persist_blueprint_promotion(
    paths: WorkspacePaths,
    promotion: BlueprintPromotionRecord,
) -> Path:
    destination = _blueprints_dir(paths) / "promotions" / f"{promotion.promotion_id}.json"
    return _write_unique_document(destination, promotion)


def read_blueprint_draft(path: Path) -> BlueprintDraftDocument:
    return BlueprintDraftDocument.model_validate_json(path.read_text(encoding="utf-8"))


def _blueprint_manifest_entries_for_id(
    paths: WorkspacePaths,
    manifest_id: str,
) -> list[tuple[Path, BlueprintManifestDocument]]:
    entries: list[tuple[Path, BlueprintManifestDocument]] = []
    canonical_path = blueprint_manifest_path(paths, manifest_id)
    if canonical_path.exists():
        canonical_manifest = _read_blueprint_manifest_file(canonical_path)
        if canonical_manifest.manifest_id == manifest_id:
            entries.append((canonical_path, canonical_manifest))

    for path in _list_json_files(_blueprint_manifest_dir(paths)):
        if path == canonical_path:
            continue
        try:
            manifest = _read_blueprint_manifest_file(path)
        except ValueError:
            continue
        if manifest.manifest_id == manifest_id:
            entries.append((path, manifest))
    return entries


def _read_blueprint_manifest_file(path: Path) -> BlueprintManifestDocument:
    return BlueprintManifestDocument.model_validate_json(path.read_text(encoding="utf-8"))


def _resolve_blueprint_manifest_entries(
    manifest_id: str,
    entries: list[tuple[Path, BlueprintManifestDocument]],
) -> BlueprintManifestDocument:
    return _resolve_blueprint_manifest_entry(manifest_id, entries)[1]


def _resolve_blueprint_manifest_entry(
    manifest_id: str,
    entries: list[tuple[Path, BlueprintManifestDocument]],
) -> tuple[Path, BlueprintManifestDocument]:
    if len({_normalized_blueprint_manifest_payload(manifest) for _path, manifest in entries}) > 1:
        _raise_blueprint_manifest_duplicate(manifest_id, entries)
    for path, manifest in entries:
        if path.stem == manifest_id:
            return path, manifest
    return entries[0]


def _normalized_blueprint_manifest_payload(manifest: BlueprintManifestDocument) -> str:
    return json.dumps(
        manifest.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )


def _raise_blueprint_manifest_duplicate(
    manifest_id: str,
    entries: list[tuple[Path, BlueprintManifestDocument]],
) -> None:
    locations = ", ".join(path.as_posix() for path, _manifest in entries)
    raise QueueStateError(
        f"blueprint_manifest_duplicate: manifest_id={manifest_id} has divergent content"
        f" in {locations}"
    )


def list_open_blueprint_lineage_work_ids(
    paths: WorkspacePaths,
    *,
    root_spec_id: str,
) -> tuple[str, ...]:
    return tuple(
        _legacy_blocker_id(ref)
        for ref in list_open_blueprint_lineage_work_refs(paths, root_spec_id=root_spec_id)
    )


def list_open_blueprint_lineage_work_refs(
    paths: WorkspacePaths,
    *,
    root_spec_id: str,
) -> tuple[ClosureBlockingWorkRef, ...]:
    blockers: list[ClosureBlockingWorkRef] = []
    seen: set[tuple[object, ...]] = set()

    for state in ("active", "queue", "blocked"):
        for path in _list_json_files(_draft_dir(paths, state)):
            try:
                draft = read_blueprint_draft(path)
            except FileNotFoundError:
                continue
            except ValueError:
                _append_unique_ref(blockers, seen, _invalid_ref(paths, path))
                continue
            if draft.root_spec_id != root_spec_id:
                continue
            _append_unique_ref(
                blockers,
                seen,
                ClosureBlockingWorkRef(
                    blocker_type="blueprint_draft",
                    reason="open_blueprint_draft",
                    work_item_family_id=WorkItemKind.BLUEPRINT_DRAFT.value,
                    work_item_kind=WorkItemKind.BLUEPRINT_DRAFT,
                    work_item_id=draft.draft_id,
                    state=state,
                    root_spec_id=draft.root_spec_id,
                    root_idea_id=draft.root_idea_id,
                    artifact_path=_runtime_rel(paths, path),
                ),
            )

    approved_blueprint_ids: set[str] = set()
    for path in _list_json_files(_blueprints_dir(paths) / "packets" / "approved"):
        try:
            packet = BlueprintPacketDocument.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except ValueError:
            _append_unique_ref(blockers, seen, _invalid_ref(paths, path))
            continue
        if packet.root_spec_id == root_spec_id:
            approved_blueprint_ids.add(packet.blueprint_id)

    promoted_blueprint_ids: set[str] = set()
    for path in _list_json_files(_blueprints_dir(paths) / "promotions"):
        try:
            promotion = BlueprintPromotionRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except ValueError:
            _append_unique_ref(blockers, seen, _invalid_ref(paths, path))
            continue
        if promotion.root_spec_id != root_spec_id:
            continue
        promoted_blueprint_ids.add(promotion.blueprint_id)
        if _generated_task_done(paths, promotion.generated_task_id):
            continue
        if _generated_task_open(paths, promotion.generated_task_id):
            continue
        _append_unique_ref(
            blockers,
            seen,
            ClosureBlockingWorkRef(
                blocker_type="blueprint_promotion",
                reason="missing_generated_task",
                work_item_family_id="blueprint_promotion",
                work_item_id=promotion.promotion_id,
                state="promoted",
                root_spec_id=promotion.root_spec_id,
                root_idea_id=promotion.root_idea_id,
                artifact_path=_runtime_rel(paths, path),
                detail=f"generated_task_id={promotion.generated_task_id}",
            ),
        )

    for blueprint_id in sorted(approved_blueprint_ids - promoted_blueprint_ids):
        _append_unique_ref(
            blockers,
            seen,
            ClosureBlockingWorkRef(
                blocker_type="blueprint_approved",
                reason="missing_promotion",
                work_item_family_id="blueprint_packet",
                work_item_id=blueprint_id,
                state="approved",
                root_spec_id=root_spec_id,
            ),
        )

    for path in _list_json_files(_blueprints_dir(paths) / "packets" / "candidates"):
        try:
            packet = BlueprintPacketDocument.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except ValueError:
            _append_unique_ref(blockers, seen, _invalid_ref(paths, path))
            continue
        if packet.root_spec_id != root_spec_id:
            continue
        _append_unique_ref(
            blockers,
            seen,
            ClosureBlockingWorkRef(
                blocker_type="blueprint_candidate",
                reason="candidate_packet",
                work_item_family_id="blueprint_packet",
                work_item_id=packet.blueprint_id,
                state="candidates",
                root_spec_id=packet.root_spec_id,
                root_idea_id=packet.root_idea_id,
                artifact_path=_runtime_rel(paths, path),
            ),
        )

    return tuple(blockers)


def blueprint_artifact_ref(paths: WorkspacePaths, path: Path) -> str:
    try:
        return path.relative_to(paths.runtime_root).as_posix()
    except ValueError as exc:
        raise QueueStateError(f"Blueprint artifact is outside runtime root: {path}") from exc


def _select_next_eligible_draft(
    paths: WorkspacePaths,
    *,
    root_spec_id: str | None = None,
) -> tuple[BlueprintDraftDocument, Path] | None:
    completed_draft_ids = _completed_dependency_draft_ids(paths)
    candidates: list[tuple[int, str, str, BlueprintDraftDocument, Path]] = []
    for path in _list_json_files(_draft_dir(paths, "queue")):
        try:
            draft = read_blueprint_draft(path)
        except FileNotFoundError:
            continue
        except ValueError as exc:
            raise QueueStateError(f"Invalid blueprint draft artifact {path}: {exc}") from exc
        if path.stem != draft.draft_id:
            raise QueueStateError(
                f"filename stem does not match draft_id: expected {draft.draft_id}, found {path.stem}"
            )
        if root_spec_id is not None and draft.root_spec_id != root_spec_id:
            continue
        if not set(draft.depends_on_draft_ids).issubset(completed_draft_ids):
            continue
        candidates.append(
            (
                draft.draft_index,
                draft.created_at.isoformat(),
                draft.draft_id,
                draft,
                path,
            )
        )
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    _index, _created_at, _draft_id, draft, path = candidates[0]
    return draft, path


def _move_blueprint_draft(
    paths: WorkspacePaths,
    draft_id: str,
    *,
    source_state: str,
    target_state: str,
    status: str,
) -> Path:
    source = _draft_dir(paths, source_state) / f"{draft_id}.json"
    if not source.exists():
        raise QueueStateError(f"active blueprint draft {draft_id} not found")
    destination = _draft_dir(paths, target_state) / source.name
    if destination.exists():
        raise QueueStateError(f"blueprint draft {draft_id} already exists")

    draft = read_blueprint_draft(source)
    moved = draft.model_copy(update={"status": status})
    _write_unique_document(destination, moved)
    source.unlink()
    return destination


def _ensure_unique_blueprint_draft(paths: WorkspacePaths, draft_id: str) -> None:
    filename = f"{draft_id}.json"
    for state in ("queue", "active", "approved", "blocked", "canceled", "superseded"):
        if (_draft_dir(paths, state) / filename).exists():
            raise QueueStateError(f"blueprint draft {draft_id} already exists")


def _completed_dependency_draft_ids(paths: WorkspacePaths) -> set[str]:
    return {
        path.stem
        for state in ("approved", "canceled", "superseded")
        for path in _list_json_files(_draft_dir(paths, state))
    }


def _approved_blueprint_ids_for_root(paths: WorkspacePaths, *, root_spec_id: str) -> set[str]:
    blueprint_ids: set[str] = set()
    for path in _list_json_files(_blueprints_dir(paths) / "packets" / "approved"):
        try:
            packet = BlueprintPacketDocument.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except ValueError:
            continue
        if packet.root_spec_id == root_spec_id:
            blueprint_ids.add(packet.blueprint_id)
    return blueprint_ids


def _generated_task_done(paths: WorkspacePaths, task_id: str) -> bool:
    return (paths.tasks_done_dir / f"{task_id}.md").exists()


def _generated_task_open(paths: WorkspacePaths, task_id: str) -> bool:
    filename = f"{task_id}.md"
    return any(
        (directory / filename).exists()
        for directory in (paths.tasks_queue_dir, paths.tasks_active_dir, paths.tasks_blocked_dir)
    )


def _append_unique(items: list[str], seen: set[str], item: str) -> None:
    if item in seen:
        return
    seen.add(item)
    items.append(item)


def _append_unique_ref(
    items: list[ClosureBlockingWorkRef],
    seen: set[tuple[object, ...]],
    item: ClosureBlockingWorkRef,
) -> None:
    key = (
        item.blocker_type,
        item.work_item_family_id,
        item.work_item_id,
        item.reason,
        item.artifact_path,
    )
    if key in seen:
        return
    seen.add(key)
    items.append(item)


def _invalid_ref(paths: WorkspacePaths, path: Path) -> ClosureBlockingWorkRef:
    return ClosureBlockingWorkRef(
        blocker_type="blueprint_invalid",
        reason="invalid_artifact",
        artifact_path=_runtime_rel(paths, path),
    )


def _legacy_blocker_id(ref: ClosureBlockingWorkRef) -> str:
    if ref.blocker_type == "blueprint_draft" and ref.work_item_id is not None:
        return f"blueprint_draft:{ref.work_item_id}"
    if ref.blocker_type == "blueprint_candidate" and ref.work_item_id is not None:
        return f"blueprint_candidate:{ref.work_item_id}"
    if ref.blocker_type == "blueprint_promotion" and ref.work_item_id is not None:
        return f"blueprint_promotion:{ref.work_item_id}:missing_generated_task"
    if ref.blocker_type == "blueprint_approved" and ref.work_item_id is not None:
        return f"blueprint_approved:{ref.work_item_id}:missing_promotion"
    if ref.blocker_type == "blueprint_invalid" and ref.artifact_path is not None:
        return f"blueprint_invalid:{ref.artifact_path}"
    if ref.work_item_id is not None:
        return ref.work_item_id
    return ref.artifact_path or "blueprint_invalid"


def _runtime_rel(paths: WorkspacePaths, path: Path) -> str:
    try:
        return path.relative_to(paths.runtime_root).as_posix()
    except ValueError:
        return path.as_posix()


def _write_unique_document(destination: Path, document: _BlueprintModelT) -> Path:
    if destination.exists():
        raise QueueStateError(f"Blueprint artifact already exists: {destination}")
    _write_document(destination, document)
    return destination


def _write_document(destination: Path, document: _BlueprintModelT) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = destination.with_name(f".{destination.name}.tmp")
    payload = json.dumps(
        document.model_dump(mode="json"),
        indent=2,
        sort_keys=True,
    )
    tmp_path.write_text(f"{payload}\n", encoding="utf-8")
    tmp_path.replace(destination)


def _list_json_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(path for path in directory.iterdir() if path.is_file() and path.suffix == ".json")


def _draft_dir(paths: WorkspacePaths, state: str) -> Path:
    return _blueprints_dir(paths) / "drafts" / state


def _blueprint_manifest_dir(paths: WorkspacePaths) -> Path:
    return _blueprints_dir(paths) / "manifests"


def _blueprints_dir(paths: WorkspacePaths) -> Path:
    return paths.runtime_root / "blueprints"


__all__ = [
    "approve_active_blueprint_draft",
    "block_active_blueprint_draft",
    "blueprint_artifact_ref",
    "blueprint_manifest_path",
    "blueprint_packet_path",
    "cancel_blueprint_draft",
    "claim_next_blueprint_draft",
    "enqueue_blueprint_draft",
    "list_blueprint_manifests",
    "list_blueprint_manifests_for_root",
    "list_open_blueprint_lineage_work_refs",
    "list_open_blueprint_lineage_work_ids",
    "move_candidate_blueprint_packet",
    "persist_blueprint_critique",
    "persist_blueprint_evaluation",
    "persist_blueprint_packet",
    "persist_blueprint_promotion",
    "read_active_blueprint_draft",
    "read_blueprint_draft",
    "read_blueprint_manifest",
    "read_blueprint_packet",
    "requeue_active_blueprint_draft",
    "resolve_blueprint_manifest_path",
    "update_active_blueprint_draft",
    "write_blueprint_manifest",
]
