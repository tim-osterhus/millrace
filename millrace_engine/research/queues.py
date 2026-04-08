"""Deterministic research queue discovery contracts and scanners."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from importlib import import_module
from pathlib import Path
from typing import Literal
import re

from pydantic import field_validator

from ..contracts import ContractModel, ControlPlane, ExecutionStatus, _normalize_datetime, _normalize_path
from ..paths import RuntimePaths
from .blockers import BlockerQueueRecord, load_blocker_records
from .state import ResearchQueueFamily, ResearchQueueOwnership, ResearchQueueSnapshot


_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_required_text(value: str, *, field_name: str) -> str:
    normalized = _WHITESPACE_RE.sub(" ", value.strip())
    if not normalized:
        raise ValueError(f"{field_name} may not be empty")
    return normalized


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _WHITESPACE_RE.sub(" ", value.strip())
    if not normalized:
        return None
    return normalized

class ResearchQueueRootKind(str, Enum):
    """Supported research queue surface kinds."""

    DIRECTORY = "directory"
    MARKDOWN_LEDGER = "markdown_ledger"


class ResearchQueueItemKind(str, Enum):
    """One discovered work-item encoding."""

    FILE = "file"
    LEDGER_ENTRY = "ledger_entry"


class ResearchQueueBoundary(ContractModel):
    """Ownership boundary between one queue family and the research plane."""

    family: ResearchQueueFamily
    queue_owner: ControlPlane
    consumer_plane: ControlPlane = ControlPlane.RESEARCH
    discovery_mode: Literal["read_only"] = "read_only"
    ownership_state_plane: ControlPlane = ControlPlane.RESEARCH


class ResearchQueueRoot(ContractModel):
    """One configured queue surface participating in family discovery."""

    family: ResearchQueueFamily
    kind: ResearchQueueRootKind
    contract_path: Path
    queue_path: Path
    boundary: ResearchQueueBoundary
    item_glob: str | None = None

    @field_validator("contract_path", "queue_path", mode="before")
    @classmethod
    def normalize_path_field(cls, value: str | Path, info: object) -> Path:
        normalized = _normalize_path(value)
        field_name = getattr(info, "field_name", "path")
        if normalized is None:
            raise ValueError(f"{field_name} may not be empty")
        return normalized

    @field_validator("item_glob")
    @classmethod
    def normalize_item_glob(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_required_text(value, field_name="item_glob")


class ResearchQueueItem(ContractModel):
    """One deterministically discovered research queue item."""

    family: ResearchQueueFamily
    item_key: str
    queue_path: Path
    item_kind: ResearchQueueItemKind
    title: str
    item_path: Path | None = None
    occurred_at: datetime | None = None
    source_status: ExecutionStatus | None = None
    incident_path: Path | None = None
    stage_blocked: str | None = None
    incident_document: object | None = None
    blocker_record: BlockerQueueRecord | None = None
    audit_record: object | None = None

    @field_validator("item_key", "title")
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "text")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("queue_path", "item_path", "incident_path", mode="before")
    @classmethod
    def normalize_paths(cls, value: str | Path | None) -> Path | None:
        return _normalize_path(value)

    @field_validator("occurred_at", mode="before")
    @classmethod
    def normalize_occurred_at(cls, value: datetime | str | None) -> datetime | None:
        if value is None:
            return None
        return _normalize_datetime(value)

    @field_validator("stage_blocked")
    @classmethod
    def normalize_stage_blocked(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)


class ResearchQueueFamilyScan(ContractModel):
    """Discovery result for one research queue family."""

    family: ResearchQueueFamily
    roots: tuple[ResearchQueueRoot, ...] = ()
    items: tuple[ResearchQueueItem, ...] = ()

    @property
    def queue_paths(self) -> tuple[Path, ...]:
        return tuple(root.queue_path for root in self.roots)

    @property
    def contract_paths(self) -> tuple[Path, ...]:
        return tuple(root.contract_path for root in self.roots)

    @property
    def boundaries(self) -> tuple[ResearchQueueBoundary, ...]:
        boundaries: list[ResearchQueueBoundary] = []
        for root in self.roots:
            if root.boundary in boundaries:
                continue
            boundaries.append(root.boundary)
        return tuple(boundaries)

    @property
    def boundary(self) -> ResearchQueueBoundary | None:
        if len(self.boundaries) != 1:
            return None
        return self.boundaries[0]

    @property
    def ready(self) -> bool:
        return bool(self.items)

    @property
    def first_item(self) -> ResearchQueueItem | None:
        if not self.items:
            return None
        return self.items[0]


class ResearchQueueDiscovery(ContractModel):
    """Aggregate deterministic queue discovery across research families."""

    families: tuple[ResearchQueueFamilyScan, ...]

    @property
    def ready_families(self) -> tuple[ResearchQueueFamily, ...]:
        return tuple(scan.family for scan in self.families if scan.ready)

    def family_scan(self, family: ResearchQueueFamily) -> ResearchQueueFamilyScan:
        for scan in self.families:
            if scan.family is family:
                return scan
        raise KeyError(f"unknown research queue family: {family.value}")

    def to_snapshot(
        self,
        *,
        ownerships: tuple[ResearchQueueOwnership, ...] = (),
        last_scanned_at: datetime | None = None,
        selected_family: ResearchQueueFamily | None = None,
    ) -> ResearchQueueSnapshot:
        return ResearchQueueSnapshot(
            goalspec_ready=self.family_scan(ResearchQueueFamily.GOALSPEC).ready,
            incident_ready=self.family_scan(ResearchQueueFamily.INCIDENT).ready,
            blocker_ready=self.family_scan(ResearchQueueFamily.BLOCKER).ready,
            audit_ready=self.family_scan(ResearchQueueFamily.AUDIT).ready,
            selected_family=selected_family,
            ownerships=ownerships,
            last_scanned_at=last_scanned_at,
        )

def _research_queue_discovery_module():
    return import_module(".research_queue_discovery", __package__)


def discover_research_queues(*args, **kwargs):
    return _research_queue_discovery_module().discover_research_queues(*args, **kwargs)


def research_queue_roots(*args, **kwargs):
    return _research_queue_discovery_module().research_queue_roots(*args, **kwargs)


__all__ = [
    "ResearchQueueBoundary",
    "ResearchQueueDiscovery",
    "ResearchQueueFamilyScan",
    "ResearchQueueItem",
    "ResearchQueueItemKind",
    "ResearchQueueRoot",
    "ResearchQueueRootKind",
    "discover_research_queues",
    "research_queue_roots",
]
