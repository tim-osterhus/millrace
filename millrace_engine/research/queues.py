"""Deterministic research queue discovery contracts and scanners."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal
import re

from pydantic import field_validator

from ..contracts import ContractModel, ControlPlane, ExecutionStatus, _normalize_datetime, _normalize_path
from ..paths import RuntimePaths
from .audit import AuditLifecycleStatus, AuditQueueRecord, load_audit_queue_record
from .blockers import BlockerQueueRecord, load_blocker_records
from .incidents import IncidentDocument, load_incident_document
from .state import ResearchQueueFamily, ResearchQueueOwnership, ResearchQueueSnapshot


_BLOCKER_HEADING_RE = re.compile(
    r"^##\s*(?P<occurred_at>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)\s*[—-]\s*(?P<title>.+?)\s*$",
    flags=re.MULTILINE,
)
_FIELD_RE = re.compile(r"^\s*(?:[-*]\s*)?\*\*(?P<name>.+?):\*\*\s*(?P<value>.*)$")
_WHITESPACE_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[^a-z0-9]+")
_DIRECTORY_ITEM_GLOB = "*.md"
_FAMILY_ORDER = (
    ResearchQueueFamily.GOALSPEC,
    ResearchQueueFamily.INCIDENT,
    ResearchQueueFamily.BLOCKER,
    ResearchQueueFamily.AUDIT,
)


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


def _slugify(value: str) -> str:
    slug = _TOKEN_RE.sub("-", value.strip().lower()).strip("-")
    return slug or "item"


def _extract_field_value(block: str, field_name: str) -> str | None:
    target = field_name.casefold()
    for raw_line in block.splitlines():
        match = _FIELD_RE.match(raw_line.strip())
        if match is None:
            continue
        if match.group("name").strip().casefold() != target:
            continue
        value = match.group("value").strip()
        return value or None
    return None


def _strip_ticks(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized.startswith("`") and normalized.endswith("`") and len(normalized) >= 2:
        return normalized[1:-1].strip()
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
    incident_document: IncidentDocument | None = None
    blocker_record: BlockerQueueRecord | None = None
    audit_record: AuditQueueRecord | None = None

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


def research_queue_roots(paths: RuntimePaths) -> tuple[ResearchQueueRoot, ...]:
    """Return the fixed research queue surfaces in deterministic scan order."""

    research_owned = ControlPlane.RESEARCH
    execution_owned = ControlPlane.EXECUTION
    return (
        ResearchQueueRoot(
            family=ResearchQueueFamily.GOALSPEC,
            kind=ResearchQueueRootKind.DIRECTORY,
            contract_path=Path("agents/ideas/raw"),
            queue_path=paths.agents_dir / "ideas" / "raw",
            boundary=ResearchQueueBoundary(
                family=ResearchQueueFamily.GOALSPEC,
                queue_owner=research_owned,
            ),
            item_glob=_DIRECTORY_ITEM_GLOB,
        ),
        ResearchQueueRoot(
            family=ResearchQueueFamily.GOALSPEC,
            kind=ResearchQueueRootKind.DIRECTORY,
            contract_path=Path("agents/ideas/staging"),
            queue_path=paths.agents_dir / "ideas" / "staging",
            boundary=ResearchQueueBoundary(
                family=ResearchQueueFamily.GOALSPEC,
                queue_owner=research_owned,
            ),
            item_glob=_DIRECTORY_ITEM_GLOB,
        ),
        ResearchQueueRoot(
            family=ResearchQueueFamily.GOALSPEC,
            kind=ResearchQueueRootKind.DIRECTORY,
            contract_path=Path("agents/ideas/specs"),
            queue_path=paths.agents_dir / "ideas" / "specs",
            boundary=ResearchQueueBoundary(
                family=ResearchQueueFamily.GOALSPEC,
                queue_owner=research_owned,
            ),
            item_glob=_DIRECTORY_ITEM_GLOB,
        ),
        ResearchQueueRoot(
            family=ResearchQueueFamily.GOALSPEC,
            kind=ResearchQueueRootKind.DIRECTORY,
            contract_path=Path("agents/ideas/specs_reviewed"),
            queue_path=paths.agents_dir / "ideas" / "specs_reviewed",
            boundary=ResearchQueueBoundary(
                family=ResearchQueueFamily.GOALSPEC,
                queue_owner=research_owned,
            ),
            item_glob=_DIRECTORY_ITEM_GLOB,
        ),
        ResearchQueueRoot(
            family=ResearchQueueFamily.INCIDENT,
            kind=ResearchQueueRootKind.DIRECTORY,
            contract_path=Path("agents/ideas/incidents/incoming"),
            queue_path=paths.agents_dir / "ideas" / "incidents" / "incoming",
            boundary=ResearchQueueBoundary(
                family=ResearchQueueFamily.INCIDENT,
                queue_owner=execution_owned,
            ),
            item_glob=_DIRECTORY_ITEM_GLOB,
        ),
        ResearchQueueRoot(
            family=ResearchQueueFamily.INCIDENT,
            kind=ResearchQueueRootKind.DIRECTORY,
            contract_path=Path("agents/ideas/incidents/working"),
            queue_path=paths.agents_dir / "ideas" / "incidents" / "working",
            boundary=ResearchQueueBoundary(
                family=ResearchQueueFamily.INCIDENT,
                queue_owner=research_owned,
            ),
            item_glob=_DIRECTORY_ITEM_GLOB,
        ),
        ResearchQueueRoot(
            family=ResearchQueueFamily.INCIDENT,
            kind=ResearchQueueRootKind.DIRECTORY,
            contract_path=Path("agents/ideas/incidents/resolved"),
            queue_path=paths.agents_dir / "ideas" / "incidents" / "resolved",
            boundary=ResearchQueueBoundary(
                family=ResearchQueueFamily.INCIDENT,
                queue_owner=research_owned,
            ),
            item_glob=_DIRECTORY_ITEM_GLOB,
        ),
        # Terminal family roots remain part of the owned queue surface but do not
        # contribute actionable work during discovery.
        ResearchQueueRoot(
            family=ResearchQueueFamily.INCIDENT,
            kind=ResearchQueueRootKind.DIRECTORY,
            contract_path=Path("agents/ideas/incidents/archived"),
            queue_path=paths.agents_dir / "ideas" / "incidents" / "archived",
            boundary=ResearchQueueBoundary(
                family=ResearchQueueFamily.INCIDENT,
                queue_owner=research_owned,
            ),
        ),
        ResearchQueueRoot(
            family=ResearchQueueFamily.BLOCKER,
            kind=ResearchQueueRootKind.MARKDOWN_LEDGER,
            contract_path=Path("agents/tasksblocker.md"),
            queue_path=paths.blocker_file,
            boundary=ResearchQueueBoundary(
                family=ResearchQueueFamily.BLOCKER,
                queue_owner=execution_owned,
            ),
        ),
        ResearchQueueRoot(
            family=ResearchQueueFamily.BLOCKER,
            kind=ResearchQueueRootKind.DIRECTORY,
            contract_path=Path("agents/ideas/blockers/incoming"),
            queue_path=paths.agents_dir / "ideas" / "blockers" / "incoming",
            boundary=ResearchQueueBoundary(
                family=ResearchQueueFamily.BLOCKER,
                queue_owner=research_owned,
            ),
            item_glob=_DIRECTORY_ITEM_GLOB,
        ),
        ResearchQueueRoot(
            family=ResearchQueueFamily.BLOCKER,
            kind=ResearchQueueRootKind.DIRECTORY,
            contract_path=Path("agents/ideas/blockers/working"),
            queue_path=paths.agents_dir / "ideas" / "blockers" / "working",
            boundary=ResearchQueueBoundary(
                family=ResearchQueueFamily.BLOCKER,
                queue_owner=research_owned,
            ),
            item_glob=_DIRECTORY_ITEM_GLOB,
        ),
        ResearchQueueRoot(
            family=ResearchQueueFamily.BLOCKER,
            kind=ResearchQueueRootKind.DIRECTORY,
            contract_path=Path("agents/ideas/blockers/resolved"),
            queue_path=paths.agents_dir / "ideas" / "blockers" / "resolved",
            boundary=ResearchQueueBoundary(
                family=ResearchQueueFamily.BLOCKER,
                queue_owner=research_owned,
            ),
            item_glob=_DIRECTORY_ITEM_GLOB,
        ),
        ResearchQueueRoot(
            family=ResearchQueueFamily.BLOCKER,
            kind=ResearchQueueRootKind.DIRECTORY,
            contract_path=Path("agents/ideas/blockers/archived"),
            queue_path=paths.agents_dir / "ideas" / "blockers" / "archived",
            boundary=ResearchQueueBoundary(
                family=ResearchQueueFamily.BLOCKER,
                queue_owner=research_owned,
            ),
        ),
        ResearchQueueRoot(
            family=ResearchQueueFamily.AUDIT,
            kind=ResearchQueueRootKind.DIRECTORY,
            contract_path=Path("agents/ideas/audit/incoming"),
            queue_path=paths.agents_dir / "ideas" / "audit" / "incoming",
            boundary=ResearchQueueBoundary(
                family=ResearchQueueFamily.AUDIT,
                queue_owner=execution_owned,
            ),
            item_glob=_DIRECTORY_ITEM_GLOB,
        ),
        ResearchQueueRoot(
            family=ResearchQueueFamily.AUDIT,
            kind=ResearchQueueRootKind.DIRECTORY,
            contract_path=Path("agents/ideas/audit/working"),
            queue_path=paths.agents_dir / "ideas" / "audit" / "working",
            boundary=ResearchQueueBoundary(
                family=ResearchQueueFamily.AUDIT,
                queue_owner=research_owned,
            ),
            item_glob=_DIRECTORY_ITEM_GLOB,
        ),
        ResearchQueueRoot(
            family=ResearchQueueFamily.AUDIT,
            kind=ResearchQueueRootKind.DIRECTORY,
            contract_path=Path("agents/ideas/audit/passed"),
            queue_path=paths.agents_dir / "ideas" / "audit" / "passed",
            boundary=ResearchQueueBoundary(
                family=ResearchQueueFamily.AUDIT,
                queue_owner=research_owned,
            ),
        ),
        ResearchQueueRoot(
            family=ResearchQueueFamily.AUDIT,
            kind=ResearchQueueRootKind.DIRECTORY,
            contract_path=Path("agents/ideas/audit/failed"),
            queue_path=paths.agents_dir / "ideas" / "audit" / "failed",
            boundary=ResearchQueueBoundary(
                family=ResearchQueueFamily.AUDIT,
                queue_owner=research_owned,
            ),
        ),
    )


def discover_research_queues(paths: RuntimePaths) -> ResearchQueueDiscovery:
    """Scan all configured research queues without mutating ownership state."""

    grouped_roots: dict[ResearchQueueFamily, list[ResearchQueueRoot]] = {family: [] for family in _FAMILY_ORDER}
    grouped_items: dict[ResearchQueueFamily, list[ResearchQueueItem]] = {family: [] for family in _FAMILY_ORDER}

    for root in research_queue_roots(paths):
        grouped_roots[root.family].append(root)
        grouped_items[root.family].extend(_scan_root(root))

    return ResearchQueueDiscovery(
        families=tuple(
            ResearchQueueFamilyScan(
                family=family,
                roots=tuple(grouped_roots[family]),
                items=tuple(grouped_items[family]),
            )
            for family in _FAMILY_ORDER
        )
    )


def _scan_root(root: ResearchQueueRoot) -> tuple[ResearchQueueItem, ...]:
    if root.kind is ResearchQueueRootKind.DIRECTORY:
        return _scan_directory_root(root)
    return _scan_blocker_root(root)


def _scan_directory_root(root: ResearchQueueRoot) -> tuple[ResearchQueueItem, ...]:
    if root.item_glob is None or not root.queue_path.is_dir():
        return ()
    candidates = sorted(
        (
            path
            for path in root.queue_path.glob(root.item_glob)
            if path.is_file()
        ),
        key=_directory_scan_sort_key,
    )
    items: list[ResearchQueueItem] = []
    for path in candidates:
        incident_document = load_incident_document(path) if root.family is ResearchQueueFamily.INCIDENT else None
        audit_record = (
            load_audit_queue_record(path, expected_status=AuditLifecycleStatus(root.queue_path.name))
            if root.family is ResearchQueueFamily.AUDIT
            else None
        )
        items.append(
            ResearchQueueItem(
                family=root.family,
                item_key=_directory_item_key(root, path),
                queue_path=root.queue_path,
                item_kind=ResearchQueueItemKind.FILE,
                title=(
                    incident_document.title
                    if incident_document is not None
                    else audit_record.title if audit_record is not None else path.name
                ),
                item_path=path,
                incident_document=incident_document,
                audit_record=audit_record,
            )
        )
    return tuple(items)


def _scan_blocker_root(root: ResearchQueueRoot) -> tuple[ResearchQueueItem, ...]:
    if not root.queue_path.exists():
        return ()
    return tuple(
        ResearchQueueItem(
            family=ResearchQueueFamily.BLOCKER,
            item_key=record.item_key,
            queue_path=root.queue_path,
            item_kind=ResearchQueueItemKind.LEDGER_ENTRY,
            title=record.task_title,
            occurred_at=record.occurred_at,
            source_status=record.status,
            incident_path=record.incident_path,
            stage_blocked=record.stage_blocked,
            blocker_record=record,
        )
        for record in load_blocker_records(root.queue_path, contract_path=root.contract_path)
    )


def _directory_item_key(root: ResearchQueueRoot, path: Path) -> str:
    return (root.contract_path / path.relative_to(root.queue_path)).as_posix()


def _directory_scan_sort_key(path: Path) -> tuple[int, str, str]:
    stat_result = path.stat()
    return (stat_result.st_mtime_ns, path.name.casefold(), path.as_posix())


def _parse_blocker_items(root: ResearchQueueRoot) -> tuple[ResearchQueueItem, ...]:
    text = root.queue_path.read_text(encoding="utf-8")
    matches = list(_BLOCKER_HEADING_RE.finditer(text))
    items: list[ResearchQueueItem] = []

    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        occurred_at = datetime.strptime(match.group("occurred_at"), "%Y-%m-%d %H:%M:%S UTC").replace(
            tzinfo=timezone.utc
        )
        title = _normalize_required_text(match.group("title"), field_name="title")
        status = _parse_blocker_status(_extract_field_value(block, "Status"))
        incident_path = _parse_blocker_path(_extract_field_value(block, "Incident intake"))
        stage_blocked = _extract_field_value(block, "Stage blocked")
        items.append(
            ResearchQueueItem(
                family=ResearchQueueFamily.BLOCKER,
                item_key=(
                    f"{root.contract_path.as_posix()}#"
                    f"{occurred_at.strftime('%Y%m%dT%H%M%SZ')}__{_slugify(title)}"
                ),
                queue_path=root.queue_path,
                item_kind=ResearchQueueItemKind.LEDGER_ENTRY,
                title=title,
                occurred_at=occurred_at,
                source_status=status,
                incident_path=incident_path,
                stage_blocked=stage_blocked,
            )
        )

    return tuple(items)


def _parse_blocker_status(value: str | None) -> ExecutionStatus | None:
    normalized = _strip_ticks(value)
    if normalized is None:
        return None
    marker = normalized.strip()
    if marker.startswith("### "):
        marker = marker[4:]
    marker = marker.strip().upper()
    if not marker:
        return None
    return ExecutionStatus(marker)


def _parse_blocker_path(value: str | None) -> Path | None:
    normalized = _strip_ticks(value)
    if normalized is None:
        return None
    candidate = normalized.strip()
    if not candidate or candidate.casefold() == "n/a":
        return None
    return Path(candidate)


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
