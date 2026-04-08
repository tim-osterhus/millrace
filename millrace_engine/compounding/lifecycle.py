"""Lifecycle governance helpers for reusable procedures."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from ..contract_compounding import (
    ProcedureLifecycleRecord,
    ProcedureLifecycleState,
    ProcedureScope,
    ReusableProcedureArtifact,
)
from ..markdown import write_text_atomic
from ..paths import RuntimePaths

ProcedureRetrievalStatus = Literal["eligible", "stale", "deprecated", "run_candidate"]
_FILENAME_TOKEN_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True, slots=True)
class StoredProcedureLifecycleRecord:
    """One persisted lifecycle record plus its artifact path."""

    path: Path
    record: ProcedureLifecycleRecord


@dataclass(frozen=True, slots=True)
class GovernedProcedure:
    """One discovered procedure plus its effective lifecycle governance state."""

    artifact: ReusableProcedureArtifact
    artifact_path: Path
    lifecycle_records: tuple[StoredProcedureLifecycleRecord, ...]
    latest_record: StoredProcedureLifecycleRecord | None
    retrieval_status: ProcedureRetrievalStatus
    eligible_for_retrieval: bool


@dataclass(frozen=True, slots=True)
class ProcedureLifecycleMutationResult:
    """Outcome of one lifecycle mutation."""

    action: Literal["promote", "deprecate"]
    procedure: GovernedProcedure
    applied: bool
    lifecycle_record: StoredProcedureLifecycleRecord | None = None
    source_procedure_id: str | None = None
    source_artifact_path: Path | None = None


def discover_governed_procedures(paths: RuntimePaths) -> tuple[GovernedProcedure, ...]:
    """Return every discovered reusable procedure and its effective lifecycle state."""

    records_by_procedure = _records_by_procedure_id(paths)
    procedures: list[GovernedProcedure] = []
    procedures.extend(
        _governed_workspace_procedure(
            artifact=artifact,
            artifact_path=path,
            records=records_by_procedure.get(artifact.procedure_id, ()),
        )
        for path, artifact in _load_workspace_artifacts(paths)
    )
    procedures.extend(
        GovernedProcedure(
            artifact=artifact,
            artifact_path=path,
            lifecycle_records=(),
            latest_record=None,
            retrieval_status="run_candidate",
            eligible_for_retrieval=False,
        )
        for path, artifact in _load_run_scoped_artifacts(paths)
    )
    procedures.sort(key=_procedure_sort_key)
    return tuple(procedures)


def discover_workspace_procedures(paths: RuntimePaths) -> tuple[GovernedProcedure, ...]:
    """Return only workspace-scope procedures with effective lifecycle status."""

    return tuple(
        procedure
        for procedure in discover_governed_procedures(paths)
        if procedure.artifact.scope is ProcedureScope.WORKSPACE
    )


def load_retrievable_workspace_procedures(paths: RuntimePaths) -> tuple[ReusableProcedureArtifact, ...]:
    """Return workspace procedures eligible for broader-scope retrieval."""

    return tuple(
        procedure.artifact
        for procedure in discover_workspace_procedures(paths)
        if procedure.eligible_for_retrieval
    )


def governed_procedure_for_id(
    paths: RuntimePaths,
    procedure_id: str,
    *,
    include_run_candidates: bool = True,
) -> GovernedProcedure:
    """Resolve one discovered procedure by id."""

    normalized_procedure_id = procedure_id.strip()
    if not normalized_procedure_id:
        raise ValueError("procedure_id may not be empty")
    matches = [
        procedure
        for procedure in discover_governed_procedures(paths)
        if procedure.artifact.procedure_id == normalized_procedure_id
        and (include_run_candidates or procedure.artifact.scope is ProcedureScope.WORKSPACE)
    ]
    if not matches:
        raise ValueError(f"procedure not found: {normalized_procedure_id}")
    if len(matches) > 1:
        raise ValueError(f"procedure_id is ambiguous across multiple artifacts: {normalized_procedure_id}")
    return matches[0]


def lifecycle_history_for_procedure(
    paths: RuntimePaths,
    procedure_id: str,
) -> tuple[StoredProcedureLifecycleRecord, ...]:
    """Return lifecycle records for one workspace procedure in newest-first order."""

    procedure = governed_procedure_for_id(paths, procedure_id, include_run_candidates=False)
    return tuple(reversed(procedure.lifecycle_records))


def discover_lifecycle_records(paths: RuntimePaths) -> tuple[StoredProcedureLifecycleRecord, ...]:
    """Return every persisted lifecycle record in newest-first order."""

    records = [
        record
        for records_for_procedure in _records_by_procedure_id(paths).values()
        for record in records_for_procedure
    ]
    return tuple(sorted(records, key=_lifecycle_sort_key, reverse=True))


def workspace_candidate_procedure_id_for(procedure_id: str) -> str:
    """Return the canonical workspace-scope review candidate id for one procedure."""

    return _promoted_procedure_id_for(procedure_id)


def ensure_workspace_candidate_procedure(
    paths: RuntimePaths,
    artifact: ReusableProcedureArtifact,
    *,
    changed_by: str,
    reason: str,
) -> str | None:
    """Materialize one run-scoped procedure into workspace candidate review state."""

    if artifact.scope is not ProcedureScope.RUN:
        return None

    workspace_procedure_id = workspace_candidate_procedure_id_for(artifact.procedure_id)
    target_path = _workspace_artifact_path(paths, workspace_procedure_id)
    if not target_path.exists():
        workspace_artifact = artifact.model_copy(
            update={
                "procedure_id": workspace_procedure_id,
                "scope": ProcedureScope.WORKSPACE,
            }
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(target_path, workspace_artifact.model_dump_json(indent=2) + "\n")

    current = governed_procedure_for_id(paths, workspace_procedure_id, include_run_candidates=False)
    if current.latest_record is not None:
        return None

    _persist_lifecycle_record(
        paths,
        procedure_id=workspace_procedure_id,
        scope=ProcedureScope.WORKSPACE,
        state=ProcedureLifecycleState.CANDIDATE,
        changed_by=changed_by,
        reason=reason,
    )
    return workspace_procedure_id


def promote_procedure(
    paths: RuntimePaths,
    *,
    procedure_id: str,
    changed_by: str,
    reason: str,
) -> ProcedureLifecycleMutationResult:
    """Promote one procedure into broader-scope workspace reuse."""

    source = governed_procedure_for_id(paths, procedure_id, include_run_candidates=True)
    source_procedure_id: str | None = None
    source_artifact_path: Path | None = None

    if source.artifact.scope is ProcedureScope.RUN:
        source_procedure_id = source.artifact.procedure_id
        source_artifact_path = source.artifact_path
        promoted_procedure_id = _promoted_procedure_id_for(source.artifact.procedure_id)
        target_path = _workspace_artifact_path(paths, promoted_procedure_id)
        if target_path.exists():
            workspace_artifact = ReusableProcedureArtifact.model_validate_json(target_path.read_text(encoding="utf-8"))
        else:
            workspace_artifact = source.artifact.model_copy(
                update={
                    "procedure_id": promoted_procedure_id,
                    "scope": ProcedureScope.WORKSPACE,
                }
            )
            target_path.parent.mkdir(parents=True, exist_ok=True)
            write_text_atomic(target_path, workspace_artifact.model_dump_json(indent=2) + "\n")
    else:
        workspace_artifact = source.artifact
        target_path = source.artifact_path

    current = governed_procedure_for_id(paths, workspace_artifact.procedure_id, include_run_candidates=False)
    if current.latest_record is not None and current.latest_record.record.state is ProcedureLifecycleState.PROMOTED:
        return ProcedureLifecycleMutationResult(
            action="promote",
            procedure=current,
            applied=False,
            lifecycle_record=current.latest_record,
            source_procedure_id=source_procedure_id,
            source_artifact_path=source_artifact_path,
        )

    record = _persist_lifecycle_record(
        paths,
        procedure_id=workspace_artifact.procedure_id,
        scope=ProcedureScope.WORKSPACE,
        state=ProcedureLifecycleState.PROMOTED,
        changed_by=changed_by,
        reason=reason,
    )
    updated = governed_procedure_for_id(paths, workspace_artifact.procedure_id, include_run_candidates=False)
    return ProcedureLifecycleMutationResult(
        action="promote",
        procedure=updated,
        applied=True,
        lifecycle_record=record,
        source_procedure_id=source_procedure_id,
        source_artifact_path=source_artifact_path,
    )


def deprecate_procedure(
    paths: RuntimePaths,
    *,
    procedure_id: str,
    changed_by: str,
    reason: str,
    replacement_procedure_id: str | None = None,
) -> ProcedureLifecycleMutationResult:
    """Deprecate one workspace-scope procedure."""

    target = governed_procedure_for_id(paths, procedure_id, include_run_candidates=False)
    if replacement_procedure_id is not None:
        replacement = governed_procedure_for_id(paths, replacement_procedure_id, include_run_candidates=False)
        replacement_procedure_id = replacement.artifact.procedure_id
    if target.latest_record is not None and target.latest_record.record.state is ProcedureLifecycleState.DEPRECATED:
        return ProcedureLifecycleMutationResult(
            action="deprecate",
            procedure=target,
            applied=False,
            lifecycle_record=target.latest_record,
        )

    record = _persist_lifecycle_record(
        paths,
        procedure_id=target.artifact.procedure_id,
        scope=ProcedureScope.WORKSPACE,
        state=ProcedureLifecycleState.DEPRECATED,
        changed_by=changed_by,
        reason=reason,
        replacement_procedure_id=replacement_procedure_id,
    )
    updated = governed_procedure_for_id(paths, target.artifact.procedure_id, include_run_candidates=False)
    return ProcedureLifecycleMutationResult(
        action="deprecate",
        procedure=updated,
        applied=True,
        lifecycle_record=record,
    )


def _records_by_procedure_id(
    paths: RuntimePaths,
) -> dict[str, tuple[StoredProcedureLifecycleRecord, ...]]:
    records: dict[str, list[StoredProcedureLifecycleRecord]] = {}
    if not paths.compounding_lifecycle_records_dir.exists():
        return {}
    for path in sorted(paths.compounding_lifecycle_records_dir.glob("*.json")):
        record = ProcedureLifecycleRecord.model_validate_json(path.read_text(encoding="utf-8"))
        records.setdefault(record.procedure_id, []).append(StoredProcedureLifecycleRecord(path=path, record=record))
    return {
        procedure_id: tuple(sorted(items, key=_lifecycle_sort_key))
        for procedure_id, items in records.items()
    }


def _governed_workspace_procedure(
    *,
    artifact: ReusableProcedureArtifact,
    artifact_path: Path,
    records: tuple[StoredProcedureLifecycleRecord, ...],
) -> GovernedProcedure:
    latest_record = records[-1] if records else None
    retrieval_status: ProcedureRetrievalStatus
    eligible = False
    if latest_record is None or latest_record.record.state is ProcedureLifecycleState.CANDIDATE:
        retrieval_status = "stale"
    elif latest_record.record.state is ProcedureLifecycleState.DEPRECATED:
        retrieval_status = "deprecated"
    else:
        retrieval_status = "eligible"
        eligible = True
    return GovernedProcedure(
        artifact=artifact,
        artifact_path=artifact_path,
        lifecycle_records=records,
        latest_record=latest_record,
        retrieval_status=retrieval_status,
        eligible_for_retrieval=eligible,
    )


def _load_workspace_artifacts(paths: RuntimePaths) -> tuple[tuple[Path, ReusableProcedureArtifact], ...]:
    if not paths.compounding_procedures_dir.exists():
        return ()
    artifacts: list[tuple[Path, ReusableProcedureArtifact]] = []
    for path in sorted(paths.compounding_procedures_dir.glob("*.json")):
        artifact = ReusableProcedureArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        artifacts.append((path, artifact))
    return tuple(artifacts)


def _load_run_scoped_artifacts(paths: RuntimePaths) -> tuple[tuple[Path, ReusableProcedureArtifact], ...]:
    if not paths.compounding_procedures_dir.exists():
        return ()
    artifacts: list[tuple[Path, ReusableProcedureArtifact]] = []
    for directory in sorted(path for path in paths.compounding_procedures_dir.iterdir() if path.is_dir()):
        for path in sorted(directory.glob("*.json")):
            artifact = ReusableProcedureArtifact.model_validate_json(path.read_text(encoding="utf-8"))
            artifacts.append((path, artifact))
    return tuple(artifacts)


def _persist_lifecycle_record(
    paths: RuntimePaths,
    *,
    procedure_id: str,
    scope: ProcedureScope,
    state: ProcedureLifecycleState,
    changed_by: str,
    reason: str,
    replacement_procedure_id: str | None = None,
) -> StoredProcedureLifecycleRecord:
    changed_at = datetime.now(timezone.utc)
    timestamp = changed_at.strftime("%Y%m%dT%H%M%S%fZ")
    record_id = f"{timestamp}.{state.value}.{_filename_token(procedure_id)}"
    record = ProcedureLifecycleRecord(
        record_id=record_id,
        procedure_id=procedure_id,
        state=state,
        scope=scope,
        changed_at=changed_at,
        changed_by=changed_by,
        reason=reason,
        replacement_procedure_id=replacement_procedure_id,
    )
    paths.compounding_lifecycle_records_dir.mkdir(parents=True, exist_ok=True)
    path = paths.compounding_lifecycle_records_dir / f"{record_id}.json"
    write_text_atomic(path, record.model_dump_json(indent=2) + "\n")
    return StoredProcedureLifecycleRecord(path=path, record=record)


def _workspace_artifact_path(paths: RuntimePaths, procedure_id: str) -> Path:
    return paths.compounding_procedures_dir / f"{_filename_token(procedure_id)}.json"


def _promoted_procedure_id_for(procedure_id: str) -> str:
    normalized = procedure_id.strip()
    if normalized.startswith("proc.run."):
        return f"proc.workspace.{normalized[len('proc.run.'):]}"
    if normalized.startswith("proc.workspace."):
        return normalized
    if normalized.startswith("proc."):
        return f"proc.workspace.{normalized[len('proc.'):]}"
    return f"proc.workspace.{normalized}"


def _filename_token(value: str) -> str:
    normalized = _FILENAME_TOKEN_RE.sub("-", value.strip()).strip("-") or "procedure"
    if len(normalized) <= 96:
        return normalized
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
    return f"{normalized[:83]}.{digest}"


def _lifecycle_sort_key(item: StoredProcedureLifecycleRecord) -> tuple[float, str]:
    return (item.record.changed_at.timestamp(), item.record.record_id)


def _procedure_sort_key(item: GovernedProcedure) -> tuple[int, int, str, str]:
    scope_priority = 0 if item.artifact.scope is ProcedureScope.WORKSPACE else 1
    status_priority_map = {"eligible": 0, "stale": 1, "deprecated": 2, "run_candidate": 3}
    status_priority = status_priority_map[item.retrieval_status]
    return (scope_priority, status_priority, item.artifact.title.lower(), item.artifact.procedure_id)
