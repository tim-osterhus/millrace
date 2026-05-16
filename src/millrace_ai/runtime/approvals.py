"""Execution capability approval storage helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field

from millrace_ai.contracts import ExecutionCapabilityGrant, Plane, WorkItemKind
from millrace_ai.contracts.base import ContractModel
from millrace_ai.paths import WorkspacePaths

ApprovalStatus = Literal["pending", "approved", "denied", "expired", "cancelled"]


class ExecutionCapabilityApproval(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["execution_capability_approval"] = "execution_capability_approval"

    approval_id: str
    status: ApprovalStatus = "pending"
    workspace_id: str
    run_id: str
    request_id: str
    work_item_kind: WorkItemKind | None = None
    work_item_id: str | None = None
    plane: Plane
    node_id: str
    stage_kind_id: str
    grant_id: str
    capability_id: str
    reason: str
    requested_by: str
    decided_by: str | None = None
    created_at: datetime
    decided_at: datetime | None = None
    expires_at: datetime | None = None
    decision_reason: str | None = None
    grant: ExecutionCapabilityGrant
    metadata: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExecutionCapabilityApprovalListing:
    pending: tuple[ExecutionCapabilityApproval, ...]
    resolved: tuple[ExecutionCapabilityApproval, ...]


def ensure_execution_capability_approval(
    paths: WorkspacePaths,
    *,
    request_id: str,
    run_id: str,
    plane: Plane,
    node_id: str,
    stage_kind_id: str,
    work_item_kind: WorkItemKind | None,
    work_item_id: str | None,
    grant: ExecutionCapabilityGrant,
    now: datetime,
    requested_by: str = "runtime",
) -> ExecutionCapabilityApproval:
    existing = find_approval_for_grant(
        paths,
        run_id=run_id,
        request_id=request_id,
        grant_id=grant.grant_id,
    )
    if existing is not None:
        return existing

    approval = ExecutionCapabilityApproval(
        approval_id=f"approval-{grant.grant_id}-{uuid4().hex[:8]}",
        workspace_id=paths.root.name,
        run_id=run_id,
        request_id=request_id,
        work_item_kind=work_item_kind,
        work_item_id=work_item_id,
        plane=plane,
        node_id=node_id,
        stage_kind_id=stage_kind_id,
        grant_id=grant.grant_id,
        capability_id=grant.capability_id,
        reason=grant.decision_reason,
        requested_by=requested_by,
        created_at=now,
        expires_at=(
            now + timedelta(seconds=grant.approval_policy_ref.expiration_seconds)
            if grant.approval_policy_ref is not None
            and grant.approval_policy_ref.expiration_seconds is not None
            else None
        ),
        grant=grant,
    )
    path = _pending_dir(paths) / f"{approval.approval_id}.json"
    _write_approval(path, approval)
    return approval


def find_approval_for_grant(
    paths: WorkspacePaths,
    *,
    run_id: str,
    request_id: str,
    grant_id: str,
) -> ExecutionCapabilityApproval | None:
    for approval in (*list_execution_capability_approvals(paths).pending, *list_execution_capability_approvals(paths).resolved):
        if (
            approval.run_id == run_id
            and approval.request_id == request_id
            and approval.grant_id == grant_id
        ):
            return approval
    return None


def list_execution_capability_approvals(
    paths: WorkspacePaths,
) -> ExecutionCapabilityApprovalListing:
    pending = tuple(
        sorted(
            (_read_approval(path) for path in _pending_dir(paths).glob("*.json")),
            key=lambda approval: approval.approval_id,
        )
    )
    resolved = tuple(
        sorted(
            (_read_approval(path) for path in _resolved_dir(paths).glob("*.json")),
            key=lambda approval: approval.approval_id,
        )
    )
    return ExecutionCapabilityApprovalListing(pending=pending, resolved=resolved)


def approve_execution_capability_request(
    paths: WorkspacePaths,
    approval_id: str,
    *,
    decided_by: str,
    reason: str,
    now: datetime,
) -> ExecutionCapabilityApproval:
    return _resolve_approval(
        paths,
        approval_id,
        status="approved",
        decided_by=decided_by,
        reason=reason,
        now=now,
    )


def deny_execution_capability_request(
    paths: WorkspacePaths,
    approval_id: str,
    *,
    decided_by: str,
    reason: str,
    now: datetime,
) -> ExecutionCapabilityApproval:
    return _resolve_approval(
        paths,
        approval_id,
        status="denied",
        decided_by=decided_by,
        reason=reason,
        now=now,
    )


def _resolve_approval(
    paths: WorkspacePaths,
    approval_id: str,
    *,
    status: Literal["approved", "denied"],
    decided_by: str,
    reason: str,
    now: datetime,
) -> ExecutionCapabilityApproval:
    pending_path = _pending_dir(paths) / f"{approval_id}.json"
    if not pending_path.is_file():
        raise FileNotFoundError(f"approval not found: {approval_id}")
    approval = _read_approval(pending_path).model_copy(
        update={
            "status": status,
            "decided_by": decided_by,
            "decided_at": now,
            "decision_reason": reason,
        }
    )
    resolved_path = _resolved_dir(paths) / f"{approval_id}.json"
    _write_approval(resolved_path, approval)
    pending_path.unlink()
    return approval


def _pending_dir(paths: WorkspacePaths) -> Path:
    path = paths.runtime_root / "approvals" / "pending"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolved_dir(paths: WorkspacePaths) -> Path:
    path = paths.runtime_root / "approvals" / "resolved"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_approval(path: Path) -> ExecutionCapabilityApproval:
    return ExecutionCapabilityApproval.model_validate_json(path.read_text(encoding="utf-8"))


def _write_approval(path: Path, approval: ExecutionCapabilityApproval) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(approval.model_dump_json(indent=2) + "\n", encoding="utf-8")


__all__ = [
    "ApprovalStatus",
    "ExecutionCapabilityApproval",
    "ExecutionCapabilityApprovalListing",
    "approve_execution_capability_request",
    "deny_execution_capability_request",
    "ensure_execution_capability_approval",
    "find_approval_for_grant",
    "list_execution_capability_approvals",
]
