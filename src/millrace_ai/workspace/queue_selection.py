"""Deterministic queue selection and lineage-scanning helpers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from millrace_ai.architecture import PlaneQueueClaimPolicyDefinition, WorkItemFamilyDefinition
from millrace_ai.assets import load_builtin_workflow_primitives
from millrace_ai.contracts import (
    IncidentDocument,
    Plane,
    SpecDocument,
    TaskDocument,
)
from millrace_ai.contracts.model_resolution import resolve_contract_model
from millrace_ai.errors import QueueStateError

from .lineage_integrity import effective_root_spec_id
from .paths import WorkspacePaths
from .queue_claims import QueueClaim
from .work_documents import parse_work_document_as

if TYPE_CHECKING:
    from .family_adapters import WorkFamilyQueueAdapter


def claim_next_for_family(
    paths: WorkspacePaths,
    family_id: str,
    *,
    root_spec_id: str | None = None,
    families: tuple[WorkItemFamilyDefinition, ...] | None = None,
) -> QueueClaim | None:
    """Generic claim API: claim the next queued work item for *family_id*.

    This is the canonical family-agnostic claim path.  It uses compiled
    family definitions and document adapters via the QueueFamilyInterpreter.
    No family-ID branch dispatch is performed.
    """
    from .queue_family_interpreter import QueueFamilyInterpreter

    interpreter = QueueFamilyInterpreter(paths, families=families)
    return interpreter.claim_next(
        family_id,
        root_spec_id=root_spec_id,
        document_validator=_make_pydantic_document_validator(
            family_id,
            families=families,
        ),
    )


def claim_next_for_plane(
    paths: WorkspacePaths,
    plane: Plane,
    *,
    root_spec_id: str | None = None,
    queue_claim_policy: PlaneQueueClaimPolicyDefinition | None = None,
    work_item_families: tuple[WorkItemFamilyDefinition, ...] | None = None,
) -> QueueClaim | None:
    """Claim the next queued item for *plane* using compiled claim policy and families.

    Iterates family_order from *queue_claim_policy* (falling back to built-in
    defaults when *queue_claim_policy* is None), tries each family via the
    family-specific adapter when one exists or the generic interpreter path
    otherwise, and returns the first successful claim.
    """
    families_by_id = _families_by_id(work_item_families)
    default_policy = _default_claim_policy_for_plane(plane)
    family_order = (
        queue_claim_policy.family_order
        if queue_claim_policy is not None
        else (default_policy.family_order if default_policy is not None else ())
    )
    claim_policy_id = (
        queue_claim_policy.policy_id
        if queue_claim_policy is not None
        else (default_policy.policy_id if default_policy is not None else f"{plane.value}.default")
    )

    # Race-safe policy-driven claim loop.
    # Per-family one-active policies are enforced inside each family's
    # claim path (adapter or QueueFamilyInterpreter).  Normal one-active
    # saturation returns None; corrupted state raises QueueStateError.
    while True:
        raced = False
        for claim_order, family_id in enumerate(family_order):
            claim, family_raced = _claim_next_family(
                paths,
                family_id=family_id,
                root_spec_id=root_spec_id,
                families_by_id=families_by_id,
                claim_policy_id=claim_policy_id,
                claim_order=claim_order,
                plane=plane,
            )
            if claim is not None:
                return claim
            if family_raced:
                raced = True
                break
        if not raced:
            return None


def claim_next_execution_task(
    paths: WorkspacePaths,
    *,
    root_spec_id: str | None = None,
) -> QueueClaim | None:
    """Compatibility wrapper: claim next execution task via the generic path."""
    return claim_next_for_family(paths, "task", root_spec_id=root_spec_id)


def claim_next_planning_item(
    paths: WorkspacePaths,
    *,
    root_spec_id: str | None = None,
    queue_claim_policy: PlaneQueueClaimPolicyDefinition | None = None,
    work_item_families: tuple[WorkItemFamilyDefinition, ...] | None = None,
) -> QueueClaim | None:
    """Compatibility wrapper: claim next planning item via the generic plane claim path."""
    return claim_next_for_plane(
        paths,
        Plane.PLANNING,
        root_spec_id=root_spec_id,
        queue_claim_policy=queue_claim_policy,
        work_item_families=work_item_families,
    )


def _default_claim_policy_for_plane(plane: Plane) -> PlaneQueueClaimPolicyDefinition | None:
    for policy in load_builtin_workflow_primitives().queue_claim_policies:
        if policy.plane is plane:
            return policy
    return None


def claim_next_learning_request(paths: WorkspacePaths) -> QueueClaim | None:
    """Compatibility wrapper: claim next learning request via the generic path."""
    return claim_next_for_family(paths, "learning_request")


def _claim_next_family(
    paths: WorkspacePaths,
    *,
    family_id: str,
    root_spec_id: str | None,
    families_by_id: dict[str, WorkItemFamilyDefinition],
    claim_policy_id: str,
    claim_order: int,
    plane: Plane,
) -> tuple[QueueClaim | None, bool]:
    """Claim next item for a single family, adapter-first then generic fallback."""
    family = families_by_id.get(family_id)
    if family is None or family.plane is not plane:
        raise QueueStateError(f"unsupported {plane.value} queue family in claim policy: {family_id}")

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
                f"queue adapter returned mismatched family: "
                f"expected {family.family_id}, got {claim.family_id}"
            )
        return _with_claim_policy(claim, claim_policy_id=claim_policy_id, claim_order=claim_order), False

    # No registered adapter — use the generic QueueFamilyInterpreter path
    from .queue_family_interpreter import QueueFamilyInterpreter

    interpreter = QueueFamilyInterpreter(
        paths, families=tuple(families_by_id.values())
    )
    claim = interpreter.claim_next(
        family.family_id,
        root_spec_id=root_spec_id,
    )
    if claim is None:
        return None, False
    return _with_claim_policy(claim, claim_policy_id=claim_policy_id, claim_order=claim_order), False


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
            f"queue family {family.family_id} references unknown adapter {adapter_id}"
        )
    return adapter





def _families_by_id(
    work_item_families: tuple[WorkItemFamilyDefinition, ...] | None,
) -> dict[str, WorkItemFamilyDefinition]:
    families = (
        work_item_families
        if work_item_families is not None
        else load_builtin_workflow_primitives().work_item_families
    )
    return {family.family_id: family for family in families}


def _list_markdown_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.glob("*.md") if path.is_file())


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


def _make_pydantic_document_validator(
    family_id: str,
    *,
    families: tuple[WorkItemFamilyDefinition, ...] | None = None,
) -> Callable[[str, Path], tuple[bool, str | None]]:
    """Create a document validator that checks pydantic model compliance."""
    from millrace_ai.work_documents import parse_work_document_as

    family = _family_by_id(family_id, families)
    if family is None:
        return lambda _fid, _p: (True, None)

    model_cls = resolve_contract_model(family.schema_id)
    if model_cls is None:
        return lambda _fid, _p: (True, None)

    def _validate(_family_id: str, path: Path) -> tuple[bool, str | None]:
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return True, None  # Raced away; don't quarantine
        except (OSError, UnicodeDecodeError) as exc:
            return False, str(exc)
        try:
            if path.suffix == ".json":
                model_cls.model_validate_json(raw)
            else:
                parse_work_document_as(raw, model=model_cls, path=path)
        except Exception as exc:
            return False, str(exc)
        return True, None

    return _validate


def _family_by_id(
    family_id: str,
    families: tuple[WorkItemFamilyDefinition, ...] | None,
) -> WorkItemFamilyDefinition | None:
    if families is None:
        families = load_builtin_workflow_primitives().work_item_families
    for family in families:
        if family.family_id == family_id:
            return family
    return None


__all__ = [
    "QueueClaim",
    "_make_pydantic_document_validator",
    "claim_next_execution_task",
    "claim_next_for_family",
    "claim_next_for_plane",
    "claim_next_learning_request",
    "claim_next_planning_item",
    "list_deferred_root_spec_ids",
    "list_open_lineage_work_ids",
]
