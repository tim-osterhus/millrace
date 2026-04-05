"""Discovery helpers for the research queue facade."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..contracts import ControlPlane, ExecutionStatus
from ..paths import RuntimePaths
from .audit import AuditLifecycleStatus, load_audit_queue_record
from .blockers import load_blocker_records
from .incidents import load_incident_document
from .state import ResearchQueueFamily


def research_queue_roots(paths: RuntimePaths):
    """Return the fixed research queue surfaces in deterministic scan order."""

    from .queues import ResearchQueueBoundary, ResearchQueueRoot, ResearchQueueRootKind

    research_owned = ControlPlane.RESEARCH
    execution_owned = ControlPlane.EXECUTION
    directory_item_glob = "*.md"
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
            item_glob=directory_item_glob,
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
            item_glob=directory_item_glob,
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
            item_glob=directory_item_glob,
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
            item_glob=directory_item_glob,
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
            item_glob=directory_item_glob,
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
            item_glob=directory_item_glob,
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
            item_glob=directory_item_glob,
        ),
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
            item_glob=directory_item_glob,
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
            item_glob=directory_item_glob,
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
            item_glob=directory_item_glob,
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
            item_glob=directory_item_glob,
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
            item_glob=directory_item_glob,
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


def discover_research_queues(paths: RuntimePaths):
    """Scan all configured research queues without mutating ownership state."""

    from .queues import ResearchQueueDiscovery, ResearchQueueFamilyScan

    family_order = (
        ResearchQueueFamily.GOALSPEC,
        ResearchQueueFamily.INCIDENT,
        ResearchQueueFamily.BLOCKER,
        ResearchQueueFamily.AUDIT,
    )
    grouped_roots: dict[ResearchQueueFamily, list[object]] = {family: [] for family in family_order}
    grouped_items: dict[ResearchQueueFamily, list[object]] = {family: [] for family in family_order}

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
            for family in family_order
        )
    )


def _scan_root(root):
    if root.kind.value == "directory":
        return _scan_directory_root(root)
    return _scan_blocker_root(root)


def _scan_directory_root(root):
    from .queues import ResearchQueueItem, ResearchQueueItemKind

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
    items: list[object] = []
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


def _scan_blocker_root(root):
    from .queues import ResearchQueueItem, ResearchQueueItemKind

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


def _directory_item_key(root, path: Path) -> str:
    return (root.contract_path / path.relative_to(root.queue_path)).as_posix()


def _directory_scan_sort_key(path: Path) -> tuple[int, str, str]:
    stat_result = path.stat()
    return (stat_result.st_mtime_ns, path.name.casefold(), path.as_posix())
