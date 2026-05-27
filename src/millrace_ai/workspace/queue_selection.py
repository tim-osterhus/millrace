"""Deterministic queue selection and lineage-scanning helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import ValidationError

from millrace_ai.architecture import PlaneQueueClaimPolicyDefinition, WorkItemFamilyDefinition
from millrace_ai.assets import load_builtin_workflow_primitives
from millrace_ai.contracts import (
    IncidentDocument,
    LearningRequestDocument,
    Plane,
    ProbeDocument,
    SpecDocument,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.contracts.work_refs import legacy_work_item_kind_for_family_id
from millrace_ai.errors import QueueStateError

from .lineage_integrity import effective_root_spec_id
from .paths import WorkspacePaths
from .queue_claims import QueueClaim
from .work_documents import parse_work_document_as
from .work_item_adapters import WorkItemDocumentAdapter, adapter_for_kind, parse_with_adapter

if TYPE_CHECKING:
    from .family_adapters import WorkFamilyQueueAdapter

_DocT = TypeVar(
    "_DocT",
    TaskDocument,
    ProbeDocument,
    SpecDocument,
    IncidentDocument,
    LearningRequestDocument,
)


def claim_next_execution_task(
    paths: WorkspacePaths,
    *,
    root_spec_id: str | None = None,
) -> QueueClaim | None:
    active = _list_markdown_files(paths.tasks_active_dir)
    if len(active) > 1:
        raise QueueStateError("Multiple active execution tasks found")
    if active:
        return None

    while True:
        candidate = _select_oldest_eligible_task(paths, root_spec_id=root_spec_id)
        if candidate is None:
            return None

        task_id, source = candidate
        destination = paths.tasks_active_dir / source.name
        try:
            source.replace(destination)
        except FileNotFoundError:
            continue
        return QueueClaim(
            work_item_kind=WorkItemKind.TASK,
            work_item_id=task_id,
            path=destination,
            source_state="queue",
            source_path=source,
        )


def claim_next_planning_item(
    paths: WorkspacePaths,
    *,
    root_spec_id: str | None = None,
    queue_claim_policy: PlaneQueueClaimPolicyDefinition | None = None,
    work_item_families: tuple[WorkItemFamilyDefinition, ...] | None = None,
) -> QueueClaim | None:
    families_by_id = _families_by_id(work_item_families)
    default_policy = _default_planning_claim_policy()
    family_order = (
        queue_claim_policy.family_order
        if queue_claim_policy is not None
        else (default_policy.family_order if default_policy is not None else ())
    )
    claim_policy_id = (
        queue_claim_policy.policy_id
        if queue_claim_policy is not None
        else (default_policy.policy_id if default_policy is not None else "planning.default")
    )
    active_count = _active_planning_item_count(
        paths,
        family_order=family_order,
        families_by_id=families_by_id,
    )
    if active_count > 1:
        raise QueueStateError("Multiple active planning items found")
    if active_count:
        return None

    while True:
        raced = False
        for claim_order, family_id in enumerate(family_order):
            claim, family_raced = _claim_next_planning_family(
                paths,
                family_id=family_id,
                root_spec_id=root_spec_id,
                families_by_id=families_by_id,
                claim_policy_id=claim_policy_id,
                claim_order=claim_order,
            )
            if claim is not None:
                return claim
            if family_raced:
                raced = True
                break
        if not raced:
            return None


def _default_planning_claim_policy() -> PlaneQueueClaimPolicyDefinition | None:
    for policy in load_builtin_workflow_primitives().queue_claim_policies:
        if policy.plane is Plane.PLANNING:
            return policy
    return None


def claim_next_learning_request(paths: WorkspacePaths) -> QueueClaim | None:
    active = _list_markdown_files(paths.learning_requests_active_dir)
    if len(active) > 1:
        raise QueueStateError("Multiple active learning requests found")
    if active:
        return None

    while True:
        candidate = _select_oldest_learning_request(paths.learning_requests_queue_dir)
        if candidate is None:
            return None

        learning_request_id, source = candidate
        destination = paths.learning_requests_active_dir / source.name
        try:
            source.replace(destination)
        except FileNotFoundError:
            continue
        return QueueClaim(
            work_item_kind=WorkItemKind.LEARNING_REQUEST,
            work_item_id=learning_request_id,
            path=destination,
            source_state="queue",
            source_path=source,
        )


def _select_oldest_task(directory: Path) -> tuple[str, Path] | None:
    candidate = _select_oldest_document_candidate(
        directory=directory,
        adapter=adapter_for_kind(WorkItemKind.TASK),
    )
    if candidate is None:
        return None
    _timestamp, item_id, path = candidate
    return item_id, path


def _select_oldest_eligible_task(
    paths: WorkspacePaths,
    *,
    root_spec_id: str | None = None,
) -> tuple[str, Path] | None:
    completed_task_ids = {path.stem for path in _list_markdown_files(paths.tasks_done_dir)}
    candidates: list[tuple[datetime, str, Path]] = []
    for path in _list_markdown_files(paths.tasks_queue_dir):
        try:
            document = parse_with_adapter(
                adapter_for_kind(WorkItemKind.TASK),
                path.read_text(encoding="utf-8"),
                path=path,
            )
        except FileNotFoundError:
            continue
        except (ValidationError, ValueError) as exc:
            _quarantine_invalid_artifact(paths.tasks_queue_dir, path, str(exc))
            continue
        task_id = document.task_id
        if path.stem != task_id:
            _quarantine_invalid_artifact(
                paths.tasks_queue_dir,
                path,
                f"filename stem does not match task_id: expected {task_id}, found {path.stem}",
            )
            continue
        if root_spec_id is not None and effective_root_spec_id(document) != root_spec_id:
            continue
        if not _task_dependencies_satisfied(document, completed_task_ids):
            continue
        candidates.append((document.created_at, task_id, path))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]))
    _timestamp, task_id, path = candidates[0]
    return task_id, path


def _select_oldest_spec(
    directory: Path,
    *,
    root_spec_id: str | None = None,
) -> tuple[str, Path] | None:
    candidate = _select_oldest_document_candidate(
        directory=directory,
        adapter=adapter_for_kind(WorkItemKind.SPEC),
        root_spec_id=root_spec_id,
    )
    if candidate is None:
        return None
    _timestamp, item_id, path = candidate
    return item_id, path


def _select_oldest_probe(directory: Path) -> tuple[str, Path] | None:
    candidate = _select_oldest_document_candidate(
        directory=directory,
        adapter=adapter_for_kind(WorkItemKind.PROBE),
    )
    if candidate is None:
        return None
    _timestamp, item_id, path = candidate
    return item_id, path


def _claim_next_planning_family(
    paths: WorkspacePaths,
    *,
    family_id: str,
    root_spec_id: str | None,
    families_by_id: dict[str, WorkItemFamilyDefinition],
    claim_policy_id: str,
    claim_order: int,
) -> tuple[QueueClaim | None, bool]:
    family = families_by_id.get(family_id)
    if family is None or family.plane is not Plane.PLANNING:
        raise QueueStateError(f"unsupported planning queue family in claim policy: {family_id}")

    adapter = _queue_adapter_for_family(family)
    if adapter is not None:
        claim = adapter.claim_next(
            paths,
            root_spec_id=root_spec_id,
            work_item_families=tuple(families_by_id.values()),
        )
        if claim is None:
            return None, False
        if claim.family_id != family.family_id:
            raise QueueStateError(
                "planning queue adapter returned mismatched family: "
                f"expected {family.family_id}, got {claim.family_id}"
            )
        return _with_claim_policy(claim, claim_policy_id=claim_policy_id, claim_order=claim_order), False

    claim, raced = _claim_next_generic_planning_family(
        paths,
        family=family,
        root_spec_id=root_spec_id,
    )
    if claim is None:
        return None, raced
    return _with_claim_policy(claim, claim_policy_id=claim_policy_id, claim_order=claim_order), raced


def _claim_next_generic_planning_family(
    paths: WorkspacePaths,
    *,
    family: WorkItemFamilyDefinition,
    root_spec_id: str | None,
) -> tuple[QueueClaim | None, bool]:
    candidate = _select_oldest_generic_family_candidate(
        paths,
        family=family,
        root_spec_id=root_spec_id,
    )
    if candidate is None:
        return None, False
    _timestamp, work_item_id, source = candidate
    destination = paths.runtime_root / family.queue_dirs.active / source.name
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)
    except FileNotFoundError:
        return None, True
    return (
        QueueClaim(
            family_id=family.family_id,
            work_item_kind=legacy_work_item_kind_for_family_id(family.family_id),
            work_item_id=work_item_id,
            path=destination,
            plane=family.plane,
            source_state=family.claimable_state,
            source_path=source,
        ),
        False,
    )


def _with_claim_policy(
    claim: QueueClaim,
    *,
    claim_policy_id: str,
    claim_order: int,
) -> QueueClaim:
    return QueueClaim(
        family_id=claim.family_id,
        work_item_kind=claim.work_item_kind,
        work_item_id=claim.work_item_id,
        path=claim.path,
        plane=claim.plane,
        source_state=claim.source_state,
        source_path=claim.source_path,
        claim_policy_id=claim_policy_id,
        claim_order=claim_order,
    )


def _queue_adapter_for_family(
    family: WorkItemFamilyDefinition,
) -> "WorkFamilyQueueAdapter | None":
    from .family_adapters import queue_adapter_for_id, resolve_queue_lifecycle_adapter_id

    adapter_id = resolve_queue_lifecycle_adapter_id(family)
    if adapter_id is None:
        return None
    adapter = queue_adapter_for_id(adapter_id)
    if adapter is None:
        raise QueueStateError(
            f"planning queue family {family.family_id} references unknown adapter {adapter_id}"
        )
    return adapter


def _select_oldest_generic_family_candidate(
    paths: WorkspacePaths,
    *,
    family: WorkItemFamilyDefinition,
    root_spec_id: str | None,
) -> tuple[datetime, str, Path] | None:
    directory = paths.runtime_root / family.queue_dirs.queue
    candidates: list[tuple[datetime, str, Path]] = []
    for path in _list_family_files(directory, family):
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        payload = _generic_json_payload(directory, path, raw, family)
        if payload is _INVALID_GENERIC_PAYLOAD:
            continue
        if not _generic_document_matches_root_spec_id(
            raw=raw,
            payload=payload,
            family=family,
            root_spec_id=root_spec_id,
        ):
            continue
        item_id = _generic_work_item_id(path, payload=payload, family=family)
        if path.stem != item_id:
            _quarantine_invalid_artifact(
                directory,
                path,
                f"filename stem does not match {family.id_field}: expected {item_id}, found {path.stem}",
            )
            continue
        candidates.append((_generic_sort_timestamp(path, payload=payload, family=family), item_id, path))

    if not candidates:
        return None

    reverse = family.sort_policy == "created_at_desc"
    if family.sort_policy == "lexical_path":
        candidates.sort(key=lambda item: (item[2].name, item[1]))
    else:
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=reverse)
    return candidates[0]


def _select_oldest_incident(
    directory: Path,
    *,
    root_spec_id: str | None = None,
) -> tuple[str, Path] | None:
    candidate = _select_oldest_document_candidate(
        directory=directory,
        adapter=adapter_for_kind(WorkItemKind.INCIDENT),
        root_spec_id=root_spec_id,
    )
    if candidate is None:
        return None
    _timestamp, item_id, path = candidate
    return item_id, path


def _families_by_id(
    work_item_families: tuple[WorkItemFamilyDefinition, ...] | None,
) -> dict[str, WorkItemFamilyDefinition]:
    families = (
        work_item_families
        if work_item_families is not None
        else load_builtin_workflow_primitives().work_item_families
    )
    return {family.family_id: family for family in families}


def _active_planning_item_count(
    paths: WorkspacePaths,
    *,
    family_order: tuple[str, ...],
    families_by_id: dict[str, WorkItemFamilyDefinition],
) -> int:
    count = 0
    for family_id in family_order:
        family = families_by_id.get(family_id)
        if family is None or family.plane is not Plane.PLANNING:
            continue
        active_dir = paths.runtime_root / family.queue_dirs.active
        count += len(_list_family_files(active_dir, family))
    return count


_INVALID_GENERIC_PAYLOAD = object()


def _generic_json_payload(
    directory: Path,
    path: Path,
    raw: str,
    family: WorkItemFamilyDefinition,
) -> dict[str, Any] | object | None:
    if family.file_extension != ".json":
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        _quarantine_invalid_artifact(directory, path, str(exc))
        return _INVALID_GENERIC_PAYLOAD
    if not isinstance(payload, dict):
        _quarantine_invalid_artifact(directory, path, "JSON work item artifact must be an object")
        return _INVALID_GENERIC_PAYLOAD
    return payload


def _generic_document_matches_root_spec_id(
    *,
    raw: str,
    payload: dict[str, Any] | object | None,
    family: WorkItemFamilyDefinition,
    root_spec_id: str | None,
) -> bool:
    if root_spec_id is None or not family.lineage_fields:
        return True
    if isinstance(payload, dict):
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


def _generic_work_item_id(
    path: Path,
    *,
    payload: dict[str, Any] | object | None,
    family: WorkItemFamilyDefinition,
) -> str:
    if family.id_field is not None and isinstance(payload, dict):
        value = payload.get(family.id_field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return path.stem


def _generic_sort_timestamp(
    path: Path,
    *,
    payload: dict[str, Any] | object | None,
    family: WorkItemFamilyDefinition,
) -> datetime:
    if isinstance(payload, dict):
        value = payload.get(family.created_at_field)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return datetime.now(timezone.utc)


def _list_family_files(directory: Path, family: WorkItemFamilyDefinition) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(path for path in directory.glob(f"*{family.file_extension}") if path.is_file())


def _select_oldest_learning_request(directory: Path) -> tuple[str, Path] | None:
    candidate = _select_oldest_document_candidate(
        directory=directory,
        adapter=adapter_for_kind(WorkItemKind.LEARNING_REQUEST),
    )
    if candidate is None:
        return None
    _timestamp, item_id, path = candidate
    return item_id, path


def _select_oldest_document_candidate(
    *,
    directory: Path,
    adapter: WorkItemDocumentAdapter[_DocT],
    root_spec_id: str | None = None,
) -> tuple[datetime, str, Path] | None:
    candidates: list[tuple[datetime, str, Path]] = []
    for path in _list_markdown_files(directory):
        try:
            document = parse_with_adapter(
                adapter,
                path.read_text(encoding="utf-8"),
                path=path,
            )
        except FileNotFoundError:
            continue
        except (ValidationError, ValueError) as exc:
            _quarantine_invalid_artifact(directory, path, str(exc))
            continue
        item_id = adapter.item_id(document)
        if path.stem != item_id:
            _quarantine_invalid_artifact(
                directory,
                path,
                f"filename stem does not match {adapter.id_attr}: expected {item_id}, found {path.stem}",
            )
            continue
        if (
            root_spec_id is not None
            and adapter.supports_root_filter
            and isinstance(document, (TaskDocument, SpecDocument, IncidentDocument))
            and effective_root_spec_id(document) != root_spec_id
        ):
            continue
        timestamp = adapter.timestamp(document)
        candidates.append((timestamp, item_id, path))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0]


def _list_markdown_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.glob("*.md") if path.is_file())


def _task_dependencies_satisfied(task: TaskDocument, completed_task_ids: set[str]) -> bool:
    return all(dependency in completed_task_ids for dependency in task.depends_on)


def list_open_lineage_work_ids(
    paths: WorkspacePaths,
    *,
    root_spec_id: str,
) -> tuple[str, ...]:
    seen: set[str] = set()
    work_item_ids: list[str] = []
    for directory, model, id_attr in _lineage_scan_specs(paths):
        for path in _list_markdown_files(directory):
            try:
                raw = path.read_text(encoding="utf-8")
                document: TaskDocument | SpecDocument | IncidentDocument
                if model is TaskDocument:
                    document = parse_work_document_as(raw, model=TaskDocument, path=path)
                elif model is SpecDocument:
                    document = parse_work_document_as(raw, model=SpecDocument, path=path)
                else:
                    document = parse_work_document_as(raw, model=IncidentDocument, path=path)
            except FileNotFoundError:
                continue
            except (ValidationError, ValueError):
                continue
            if effective_root_spec_id(document) != root_spec_id:
                continue
            work_item_id = str(getattr(document, id_attr))
            if work_item_id in seen:
                continue
            seen.add(work_item_id)
            work_item_ids.append(work_item_id)
    return tuple(work_item_ids)


def list_deferred_root_spec_ids(
    paths: WorkspacePaths,
    *,
    open_root_spec_id: str,
) -> tuple[str, ...]:
    """Return queued root specs deferred by the current workspace-global closure target."""

    deferred: list[tuple[datetime, str]] = []
    for path in _list_markdown_files(paths.specs_queue_dir):
        try:
            document = parse_work_document_as(
                path.read_text(encoding="utf-8"),
                model=SpecDocument,
                path=path,
            )
        except FileNotFoundError:
            continue
        except (ValidationError, ValueError):
            continue
        if not _is_root_spec_document(document):
            continue
        document_root_spec_id = effective_root_spec_id(document)
        if document_root_spec_id is None or document_root_spec_id == open_root_spec_id:
            continue
        deferred.append((document.created_at, document.spec_id))

    deferred.sort(key=lambda item: (item[0], item[1]))
    return tuple(spec_id for _created_at, spec_id in deferred)


def _is_root_spec_document(document: SpecDocument) -> bool:
    if document.root_spec_id is not None:
        return document.spec_id == document.root_spec_id
    if document.parent_spec_id is not None and document.parent_spec_id.strip().lower() != "none":
        return False
    return document.source_type in {"idea", "manual"}


def _lineage_scan_specs(
    paths: WorkspacePaths,
) -> tuple[
    tuple[
        Path,
        type[TaskDocument] | type[SpecDocument] | type[IncidentDocument],
        str,
    ],
    ...,
]:
    return (
        (paths.tasks_queue_dir, TaskDocument, "task_id"),
        (paths.tasks_active_dir, TaskDocument, "task_id"),
        (paths.tasks_blocked_dir, TaskDocument, "task_id"),
        (paths.specs_queue_dir, SpecDocument, "spec_id"),
        (paths.specs_active_dir, SpecDocument, "spec_id"),
        (paths.specs_blocked_dir, SpecDocument, "spec_id"),
        (paths.incidents_incoming_dir, IncidentDocument, "incident_id"),
        (paths.incidents_active_dir, IncidentDocument, "incident_id"),
        (paths.incidents_blocked_dir, IncidentDocument, "incident_id"),
    )


def _quarantine_invalid_artifact(directory: Path, source_path: Path, error: str) -> None:
    destination = source_path.with_suffix(f"{source_path.suffix}.invalid")
    suffix_index = 1
    while destination.exists():
        destination = source_path.with_suffix(f"{source_path.suffix}.invalid.{suffix_index}")
        suffix_index += 1

    try:
        source_path.replace(destination)
    except FileNotFoundError:
        return
    log_path = directory / "invalid-artifacts.jsonl"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "at": datetime.now(timezone.utc).isoformat(),
                    "source_name": source_path.name,
                    "quarantine_name": destination.name,
                    "error": error,
                },
                sort_keys=True,
            )
            + "\n"
        )


__all__ = [
    "QueueClaim",
    "claim_next_execution_task",
    "claim_next_learning_request",
    "claim_next_planning_item",
    "list_deferred_root_spec_ids",
    "list_open_lineage_work_ids",
]
