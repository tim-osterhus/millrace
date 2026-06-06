"""Runtime effect operation journal and idempotent resume helpers.

Writes durable JSONL journal records to
millrace-agents/state/runtime-effect-journal/<operation_id>.jsonl so the
interpreted operation interpreter can skip completed steps on resume and
fail non-equivalent duplicates.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def journal_file_path(journal_dir: Path, operation_id: str) -> Path:
    """Return the JSONL journal file path for an operation."""
    return journal_dir / f"{operation_id}.jsonl"


def write_started_record(
    journal_dir: Path,
    operation_id: str,
    step_id: str,
    params: dict[str, Any],
    reads_artifact_ids: tuple[str, ...],
    *,
    runner_id: str = "",
    source_work_item_family: str | None = None,
    source_work_item_id: str | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
    primitive_id: str | None = None,
) -> None:
    """Write a ``started`` journal record before a mutating primitive runs."""
    journal_dir.mkdir(parents=True, exist_ok=True)
    idempotency_hash = compute_idempotency_hash(
        operation_id, step_id, params, reads_artifact_ids
    )
    record = {
        "record_type": "started",
        "status": "started",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "operation_id": operation_id,
        "runner_id": runner_id,
        "source_work_item_family": source_work_item_family,
        "source_work_item_id": source_work_item_id,
        "run_id": run_id,
        "request_id": request_id,
        "step_id": step_id,
        "primitive_id": primitive_id,
        "idempotency_hash": idempotency_hash,
        "params": _canonicalize_params(params),
        "reads_artifact_ids": sorted(reads_artifact_ids),
    }
    with open(journal_file_path(journal_dir, operation_id), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def write_completed_record(
    journal_dir: Path,
    operation_id: str,
    step_id: str,
    params: dict[str, Any],
    reads_artifact_ids: tuple[str, ...],
    *,
    runner_id: str = "",
    source_work_item_family: str | None = None,
    source_work_item_id: str | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
    primitive_id: str | None = None,
) -> str:
    """Write a ``completed`` journal record with an idempotency hash.

    Returns the idempotency hash so callers can use it immediately.
    """
    journal_dir.mkdir(parents=True, exist_ok=True)
    idempotency_hash = compute_idempotency_hash(
        operation_id, step_id, params, reads_artifact_ids
    )
    record = {
        "record_type": "completed",
        "status": "completed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "operation_id": operation_id,
        "runner_id": runner_id,
        "source_work_item_family": source_work_item_family,
        "source_work_item_id": source_work_item_id,
        "run_id": run_id,
        "request_id": request_id,
        "step_id": step_id,
        "primitive_id": primitive_id,
        "idempotency_hash": idempotency_hash,
    }
    with open(journal_file_path(journal_dir, operation_id), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    return idempotency_hash


def write_failed_record(
    journal_dir: Path,
    operation_id: str,
    step_id: str,
    params: dict[str, Any],
    reads_artifact_ids: tuple[str, ...],
    *,
    runner_id: str = "",
    failure_class: str,
    failure_message: str,
    source_work_item_family: str | None = None,
    source_work_item_id: str | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
    primitive_id: str | None = None,
) -> None:
    """Write a ``failed`` journal record for a mutating primitive that
    failed before or during mutation.

    This does not duplicate queue or store side effects. It only appends
    a durable failure record so downstream stages (and eventual retries)
    can reconstruct the failure context without re-executing.
    """
    journal_dir.mkdir(parents=True, exist_ok=True)
    idempotency_hash = compute_idempotency_hash(
        operation_id, step_id, params, reads_artifact_ids
    )
    record = {
        "record_type": "failed",
        "status": "failed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "operation_id": operation_id,
        "runner_id": runner_id,
        "source_work_item_family": source_work_item_family,
        "source_work_item_id": source_work_item_id,
        "run_id": run_id,
        "request_id": request_id,
        "step_id": step_id,
        "primitive_id": primitive_id,
        "idempotency_hash": idempotency_hash,
        "failure_class": failure_class,
        "failure_message": failure_message,
    }
    with open(journal_file_path(journal_dir, operation_id), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def compute_idempotency_hash(
    operation_id: str,
    step_id: str,
    params: dict[str, Any],
    reads_artifact_ids: tuple[str, ...],
) -> str:
    """Compute a stable SHA-256 idempotency hash for a mutation step.

    The hash covers operation identity, step identity, canonicalised
    parameters, and sorted artifact ids so that equivalent steps
    produce matching hashes across restarts.
    """
    payload = {
        "operation_id": operation_id,
        "step_id": step_id,
        "params": _canonicalize_params(params),
        "reads_artifact_ids": sorted(reads_artifact_ids),
    }
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_journal_records(
    journal_dir: Path, operation_id: str
) -> list[dict[str, Any]]:
    """Read every journal record for *operation_id* in append order."""
    path = journal_file_path(journal_dir, operation_id)
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").strip().split("\n"):
        if line.strip():
            records.append(json.loads(line))
    return records


def completed_hashes_by_step(
    journal_dir: Path, operation_id: str
) -> dict[str, set[str]]:
    """Return completed idempotency hashes keyed by step_id.

    One step may have multiple completed records (e.g. partial retries);
    the caller is responsible for deciding which hash set to treat as
    authoritative.
    """
    by_step: dict[str, set[str]] = {}
    for record in read_journal_records(journal_dir, operation_id):
        if record.get("record_type") == "completed":
            step_id = record.get("step_id", "")
            h = record.get("idempotency_hash")
            if step_id and h:
                by_step.setdefault(step_id, set()).add(h)
    return by_step


def has_started_record(
    journal_dir: Path, operation_id: str, step_id: str
) -> bool:
    """Return ``True`` when *step_id* has an uncompleted started record.

    A started record without a following completed record signals an
    interrupted step that should be replayed.
    """
    started = False
    for record in read_journal_records(journal_dir, operation_id):
        if record.get("record_type") == "started" and record.get("step_id") == step_id:
            started = True
        if record.get("record_type") == "completed" and record.get("step_id") == step_id:
            started = False
    return started


def _canonicalize_params(params: dict[str, Any]) -> dict[str, Any]:
    """Produce a deterministic, hash-friendly copy of *params*."""
    return dict(sorted(params.items()))


__all__ = [
    "completed_hashes_by_step",
    "compute_idempotency_hash",
    "has_started_record",
    "journal_file_path",
    "read_journal_records",
    "write_completed_record",
    "write_failed_record",
    "write_started_record",
]
