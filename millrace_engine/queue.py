"""File-backed queue operations."""

from __future__ import annotations

import fcntl
import json
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from typing import Literal, Sequence

from .contracts import BlockerEntry, ExecutionStatus, ResearchRecoveryLatch, TaskCard
from .markdown import (
    TaskStoreDocument,
    append_markdown_block,
    insert_after_preamble,
    parse_task_store,
    render_task_store,
    write_text_atomic,
)
from .paths import RuntimePaths
from .policies.sizing import SizeClass, adaptive_upscope_task_card

DEFAULT_STORE_PREAMBLES = {
    "tasks.md": "# Active Task",
    "tasksbacklog.md": "# Task Backlog",
    "tasksarchive.md": "# Task Archive",
    "tasksbackburner.md": "# Task Backburner",
    "tasksblocker.md": "# Task Blockers",
    "taskspending.md": "# Tasks Pending",
}

DEFAULT_LOCK_TIMEOUT_SECONDS = 15.0
LOCK_RETRY_SLEEP_SECONDS = 0.05
_DEPENDENCY_TAGS_RE = re.compile(r"^-\s+\*\*Dependency(?:\s+|-)Tags:\*\*\s*(.+)$", re.IGNORECASE)


def _research_incidents_module():
    return import_module(".research.incidents", __package__)


class QueueError(RuntimeError):
    """Base queue failure."""


class QueueEmptyError(QueueError):
    """Raised when a queue operation needs a card but none exists."""


class QueueStateError(QueueError):
    """Raised when visible queue files violate queue invariants."""


class QueueLockTimeoutError(QueueError):
    """Raised when queue.lock cannot be acquired in time."""


class QueueMergeConflictError(QueueStateError):
    """Raised when a prepared pending-family merge no longer matches live files."""


@dataclass(frozen=True, slots=True)
class QueueCleanupRecord:
    """One bounded queued-work cleanup result."""

    action: Literal["remove", "quarantine"]
    task: TaskCard
    source_store: Literal["active", "backlog"]
    destination_store: Literal["backburner"]
    reason: str
    cleaned_at: datetime


def _sha256_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _normalize_dependency_token(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def _parse_dependency_tokens(value: str | None) -> frozenset[str]:
    if value is None:
        return frozenset()
    return frozenset(
        token
        for token in (_normalize_dependency_token(raw) for raw in value.split(","))
        if token
    )


@dataclass(frozen=True, slots=True)
class _DependencyQuarantineMetadata:
    identity_tokens: frozenset[str]
    overlap_tokens: frozenset[str]
    has_dependency_metadata: bool


def _dependency_tags_from_body(body: str) -> frozenset[str]:
    tags: set[str] = set()
    for raw_line in body.splitlines():
        match = _DEPENDENCY_TAGS_RE.match(raw_line.strip())
        if not match:
            continue
        tags.update(_parse_dependency_tokens(match.group(1)))
    return frozenset(tags)


def _dependency_quarantine_metadata(card: TaskCard) -> _DependencyQuarantineMetadata:
    spec_ids = _parse_dependency_tokens(card.spec_id)
    dependency_tags = _dependency_tags_from_body(card.body)
    depends_on = frozenset(
        token for token in (_normalize_dependency_token(item) for item in card.depends_on) if token
    )
    blocks = frozenset(
        token for token in (_normalize_dependency_token(item) for item in card.blocks) if token
    )
    provides = frozenset(
        token for token in (_normalize_dependency_token(item) for item in card.provides) if token
    )
    identity_tokens = set(spec_ids | dependency_tags | blocks | provides)
    for value in (card.task_id, card.title, card.heading):
        token = _normalize_dependency_token(value)
        if token:
            identity_tokens.add(token)
    if card.date:
        dated_title = _normalize_dependency_token(f"{card.date} - {card.title}")
        if dated_title:
            identity_tokens.add(dated_title)
    overlap_tokens = spec_ids | dependency_tags | depends_on | blocks | provides
    return _DependencyQuarantineMetadata(
        identity_tokens=frozenset(identity_tokens),
        overlap_tokens=frozenset(overlap_tokens),
        has_dependency_metadata=bool(spec_ids or dependency_tags or depends_on or blocks or provides),
    )


def _dependency_quarantine_overlap(
    active_metadata: _DependencyQuarantineMetadata,
    candidate_metadata: _DependencyQuarantineMetadata,
) -> bool:
    return bool(
        active_metadata.identity_tokens.intersection(candidate_metadata.overlap_tokens)
        or candidate_metadata.identity_tokens.intersection(active_metadata.overlap_tokens)
    )


def load_research_recovery_latch(path: Path) -> ResearchRecoveryLatch | None:
    """Load a persisted recovery latch if present."""

    if not path.exists():
        return None
    return ResearchRecoveryLatch.model_validate(json.loads(path.read_text(encoding="utf-8")))


def write_research_recovery_latch(path: Path, latch: ResearchRecoveryLatch) -> None:
    """Persist one recovery latch snapshot atomically."""

    write_text_atomic(
        path,
        latch.model_dump_json(indent=2, exclude_none=True) + "\n",
    )


class TaskQueue:
    """Visible Millrace task-store mutation layer."""

    def __init__(self, paths: RuntimePaths, *, lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS) -> None:
        self.paths = paths
        self.lock_timeout_seconds = lock_timeout_seconds

    @contextmanager
    def _locked(self) -> None:
        self.paths.queue_lock_file.parent.mkdir(parents=True, exist_ok=True)
        with self.paths.queue_lock_file.open("a+", encoding="utf-8") as handle:
            deadline = time.monotonic() + self.lock_timeout_seconds
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise QueueLockTimeoutError("timed out waiting for queue.lock") from None
                    time.sleep(LOCK_RETRY_SLEEP_SECONDS)
                    continue
                break
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _default_preamble(self, path: Path) -> str:
        return DEFAULT_STORE_PREAMBLES.get(path.name, "")

    def _read_text(self, path: Path) -> str:
        if not path.exists():
            preamble = self._default_preamble(path)
            return (preamble + "\n") if preamble else ""
        return path.read_text(encoding="utf-8")

    def _read_store(self, path: Path) -> TaskStoreDocument:
        document = parse_task_store(self._read_text(path), source_file=path)
        if not document.preamble and self._default_preamble(path):
            return TaskStoreDocument(preamble=self._default_preamble(path), cards=document.cards)
        return document

    def _write_store(self, path: Path, document: TaskStoreDocument) -> None:
        preamble = document.preamble or self._default_preamble(path)
        write_text_atomic(path, render_task_store(TaskStoreDocument(preamble=preamble, cards=document.cards)))

    def _visible_store_label(self, path: Path) -> Literal["active", "backlog"]:
        if path == self.paths.tasks_file:
            return "active"
        if path == self.paths.backlog_file:
            return "backlog"
        raise QueueStateError(f"unsupported cleanup store: {path.name}")

    def _read_active_document(self) -> TaskStoreDocument:
        document = self._read_store(self.paths.tasks_file)
        if len(document.cards) > 1:
            raise QueueStateError("agents/tasks.md may contain at most one active task card")
        return document

    def _active_task_fingerprint(self) -> str | None:
        text = self._read_text(self.paths.tasks_file)
        if not text.strip():
            return None
        return _sha256_text(text)

    def _resolve_active_card(self, expected: TaskCard | None = None) -> TaskCard:
        active = self._read_active_document().cards
        if not active:
            raise QueueEmptyError("no active task card is present")
        current = active[0]
        if expected is not None and current.task_id != expected.task_id:
            raise QueueStateError(
                f"active task mismatch: expected {expected.task_id}, found {current.task_id}"
            )
        return current

    def _visible_recovery_cards(self, remediation_spec_id: str) -> tuple[TaskCard, ...]:
        cards: list[TaskCard] = []
        for store_path in (self.paths.tasks_file, self.paths.backlog_file, self.paths.taskspending_file):
            cards.extend(card for card in self._read_store(store_path).cards if card.spec_id == remediation_spec_id)
        return tuple(cards)

    def _find_visible_task(self, task_id: str) -> tuple[Path, TaskStoreDocument, int, TaskCard]:
        normalized_task_id = task_id.strip()
        if not normalized_task_id:
            raise QueueStateError("queue cleanup requires a task id")

        matches: list[tuple[Path, TaskStoreDocument, int, TaskCard]] = []
        for store_path in (self.paths.tasks_file, self.paths.backlog_file):
            document = self._read_store(store_path)
            for index, card in enumerate(document.cards):
                if card.task_id == normalized_task_id:
                    matches.append((store_path, document, index, card))

        if not matches:
            raise QueueEmptyError(f"queued task not found: {normalized_task_id}")
        if len(matches) > 1:
            raise QueueStateError(f"queued task appears in multiple visible stores: {normalized_task_id}")
        return matches[0]

    def _render_cleanup_record(
        self,
        *,
        action: Literal["remove", "quarantine"],
        source_store: Literal["active", "backlog"],
        reason: str,
        cleaned_at: datetime,
        task: TaskCard,
    ) -> str:
        cleaned_at_label = cleaned_at.isoformat().replace("+00:00", "Z")
        lines = [
            f"<!-- queue_cleanup:{action}:start {task.task_id} -->",
            f"<!-- cleaned_at: {cleaned_at_label} -->",
            f"<!-- source_store: {source_store} -->",
            f"<!-- reason: {reason} -->",
            task.render_markdown(),
            f"<!-- queue_cleanup:{action}:end {task.task_id} -->",
        ]
        return "\n\n".join(lines)

    def backlog_empty(self) -> bool:
        """Return True when the backlog file has no visible task cards."""

        return not self._read_store(self.paths.backlog_file).cards

    def backlog_depth(self) -> int:
        """Return the current backlog depth."""

        return len(self._read_store(self.paths.backlog_file).cards)

    def peek_next(self) -> TaskCard | None:
        """Return the next backlog card without mutation."""

        backlog = self._read_store(self.paths.backlog_file).cards
        return backlog[0] if backlog else None

    def active_task(self) -> TaskCard | None:
        """Return the currently active task card."""

        active = self._read_active_document().cards
        return active[0] if active else None

    def reorder(self, task_ids: list[str] | tuple[str, ...]) -> tuple[TaskCard, ...]:
        """Rewrite the backlog in one validated deterministic order."""

        requested_ids = [task_id.strip() for task_id in task_ids if task_id.strip()]
        if not requested_ids:
            raise QueueStateError("queue reorder requires at least one backlog task id")
        if len(set(requested_ids)) != len(requested_ids):
            raise QueueStateError("queue reorder does not allow duplicate task ids")

        with self._locked():
            backlog_document = self._read_store(self.paths.backlog_file)
            backlog_cards = list(backlog_document.cards)
            known_ids = [card.task_id for card in backlog_cards]
            if len(requested_ids) != len(known_ids):
                raise QueueStateError(
                    "queue reorder must specify the full backlog order exactly once"
                )

            by_id = {card.task_id: card for card in backlog_cards}
            unknown_ids = [task_id for task_id in requested_ids if task_id not in by_id]
            missing_ids = [task_id for task_id in known_ids if task_id not in requested_ids]
            if unknown_ids or missing_ids:
                details: list[str] = []
                if unknown_ids:
                    details.append(f"unknown={','.join(unknown_ids)}")
                if missing_ids:
                    details.append(f"missing={','.join(missing_ids)}")
                raise QueueStateError("queue reorder id mismatch: " + " ".join(details))

            reordered_cards = [by_id[task_id] for task_id in requested_ids]
            self._write_store(
                self.paths.backlog_file,
                TaskStoreDocument(
                    preamble=backlog_document.preamble,
                    cards=reordered_cards,
                ),
            )
            return tuple(reordered_cards)

    def cleanup(
        self,
        task_id: str,
        *,
        action: Literal["remove", "quarantine"],
        reason: str,
    ) -> QueueCleanupRecord:
        """Remove one visible queued task and append one bounded cleanup record."""

        normalized_reason = " ".join(reason.strip().split())
        if not normalized_reason:
            raise QueueStateError("queue cleanup requires a reason")

        with self._locked():
            store_path, document, index, card = self._find_visible_task(task_id)
            remaining_cards = list(document.cards)
            removed_card = remaining_cards.pop(index)
            cleaned_at = datetime.now(timezone.utc)
            source_store = self._visible_store_label(store_path)
            cleanup_record = self._render_cleanup_record(
                action=action,
                source_store=source_store,
                reason=normalized_reason,
                cleaned_at=cleaned_at,
                task=removed_card,
            )

            self._write_store(
                store_path,
                TaskStoreDocument(preamble=document.preamble, cards=remaining_cards),
            )
            updated_backburner = append_markdown_block(
                self._read_text(self.paths.backburner_file),
                cleanup_record,
            )
            write_text_atomic(self.paths.backburner_file, updated_backburner)
            return QueueCleanupRecord(
                action=action,
                task=removed_card,
                source_store=source_store,
                destination_store="backburner",
                reason=normalized_reason,
                cleaned_at=cleaned_at,
            )

    def remove_task(self, task_id: str, *, reason: str) -> QueueCleanupRecord:
        """Remove one visible queued task and retain an audit trail."""

        return self.cleanup(task_id, action="remove", reason=reason)

    def quarantine_task(self, task_id: str, *, reason: str) -> QueueCleanupRecord:
        """Quarantine one visible queued task into backburner with a cleanup record."""

        return self.cleanup(task_id, action="quarantine", reason=reason)

    def merge_pending_family(
        self,
        *,
        expected_backlog_sha256: str,
        expected_pending_sha256: str,
        ordered_backlog_cards: Sequence[TaskCard],
        pending_preamble: str = "",
        clear_shard_paths: Sequence[Path] = (),
    ) -> tuple[TaskCard, ...]:
        """Atomically merge one prepared pending family into backlog and clear pending."""

        merged_cards = [card for card in ordered_backlog_cards]
        if not merged_cards:
            raise QueueStateError("pending-family merge requires at least one backlog card")

        merged_ids = [card.task_id for card in merged_cards]
        if len(set(merged_ids)) != len(merged_ids):
            raise QueueStateError("pending-family merge does not allow duplicate task ids")

        with self._locked():
            backlog_text = self._read_text(self.paths.backlog_file)
            pending_text = self._read_text(self.paths.taskspending_file)
            if _sha256_text(backlog_text) != expected_backlog_sha256:
                raise QueueMergeConflictError("pending-family merge backlog snapshot changed before commit")
            if _sha256_text(pending_text) != expected_pending_sha256:
                raise QueueMergeConflictError("pending-family merge pending snapshot changed before commit")

            backlog_document = self._read_store(self.paths.backlog_file)
            self._write_store(
                self.paths.backlog_file,
                TaskStoreDocument(
                    preamble=backlog_document.preamble,
                    cards=merged_cards,
                ),
            )
            self._write_store(
                self.paths.taskspending_file,
                TaskStoreDocument(preamble=pending_preamble, cards=[]),
            )
            for shard_path in clear_shard_paths:
                shard_path.unlink(missing_ok=True)
            return tuple(merged_cards)

    def promote_next(self) -> TaskCard:
        """Promote the next backlog card into the active task store."""

        with self._locked():
            active_document = self._read_active_document()
            if active_document.cards:
                raise QueueStateError("cannot promote when an active task already exists")

            backlog_document = self._read_store(self.paths.backlog_file)
            if not backlog_document.cards:
                raise QueueEmptyError("cannot promote from an empty backlog")

            card = backlog_document.cards[0]
            self._write_store(
                self.paths.backlog_file,
                TaskStoreDocument(preamble=backlog_document.preamble, cards=backlog_document.cards[1:]),
            )
            self._write_store(
                self.paths.tasks_file,
                TaskStoreDocument(preamble=active_document.preamble, cards=[card]),
            )
            return card

    def archive(self, card: TaskCard) -> None:
        """Move the active task card into the archive store."""

        with self._locked():
            active_card = self._resolve_active_card(card)
            active_document = self._read_active_document()
            archive_document = self._read_store(self.paths.archive_file)
            self._write_store(
                self.paths.archive_file,
                TaskStoreDocument(
                    preamble=archive_document.preamble,
                    cards=[*archive_document.cards, active_card],
                ),
            )
            self._write_store(
                self.paths.tasks_file,
                TaskStoreDocument(preamble=active_document.preamble, cards=[]),
            )

    def demote(self, card: TaskCard) -> None:
        """Move the active task card into backburner."""

        with self._locked():
            active_card = self._resolve_active_card(card)
            active_document = self._read_active_document()
            backburner_text = self._read_text(self.paths.backburner_file)
            updated_backburner = append_markdown_block(backburner_text, active_card.render_markdown())
            write_text_atomic(self.paths.backburner_file, updated_backburner)
            self._write_store(
                self.paths.tasks_file,
                TaskStoreDocument(preamble=active_document.preamble, cards=[]),
            )

    def record_adaptive_upscope(
        self,
        card: TaskCard,
        *,
        target: SizeClass,
        rule: str,
        stage: str,
        reason: str,
    ) -> TaskCard:
        """Rewrite the active task card with one explicit adaptive-upscope block."""

        with self._locked():
            active_card = self._resolve_active_card(card)
            active_document = self._read_active_document()
            updated_card = adaptive_upscope_task_card(
                active_card,
                target=target,
                rule=rule,
                stage=stage,
                reason=reason,
            )
            self._write_store(
                self.paths.tasks_file,
                TaskStoreDocument(preamble=active_document.preamble, cards=[updated_card]),
            )
            return updated_card

    def quarantine(
        self,
        card: TaskCard,
        reason: str,
        incident_path: Path | None = None,
        *,
        stage: str = "Consult",
        status: ExecutionStatus = ExecutionStatus.NEEDS_RESEARCH,
        run_dir: Path | None = None,
        diagnostics_dir: Path | None = None,
        prompt_artifact: Path | None = None,
        notes: str | None = None,
        retain_backlog_cards: int = 0,
        quarantine_mode_requested: Literal["full", "dependency"] = "full",
        failure_signature: str = "needs_research",
    ) -> ResearchRecoveryLatch:
        """Quarantine the active task and freeze backlog state for research handoff."""

        with self._locked():
            active_card = self._resolve_active_card(card)
            active_document = self._read_active_document()
            backlog_document = self._read_store(self.paths.backlog_file)
            fingerprint = self._active_task_fingerprint()
            canonical_incident_path = incident_path
            if status is ExecutionStatus.NEEDS_RESEARCH:
                canonical_incident_path = _research_incidents_module().resolve_deduplicated_incident_path(
                    self.paths,
                    fingerprint=fingerprint,
                    failure_signature=failure_signature,
                    preferred_path=incident_path,
                )

            retained_cards = list(backlog_document.cards[:retain_backlog_cards])
            candidate_backlog_cards = list(backlog_document.cards[retain_backlog_cards:])
            requested_mode = quarantine_mode_requested
            if requested_mode not in {"full", "dependency"}:
                requested_mode = "full"
                quarantine_reason = f"fallback_full_invalid_mode:{quarantine_mode_requested}"
            else:
                quarantine_reason = "requested"
            applied_mode = requested_mode
            frozen_backlog_cards = list(candidate_backlog_cards)
            missing_metadata_quarantined = 0

            if requested_mode == "dependency":
                active_metadata = _dependency_quarantine_metadata(active_card)
                if not active_metadata.has_dependency_metadata:
                    applied_mode = "full"
                    quarantine_reason = "fallback_full_active_metadata_missing"
                else:
                    applied_mode = "dependency"
                    quarantine_reason = "dependency_overlap_match"
                    frozen_backlog_cards = []
                    for backlog_card in candidate_backlog_cards:
                        backlog_metadata = _dependency_quarantine_metadata(backlog_card)
                        if not backlog_metadata.has_dependency_metadata:
                            missing_metadata_quarantined += 1
                            frozen_backlog_cards.append(backlog_card)
                            continue
                        if _dependency_quarantine_overlap(active_metadata, backlog_metadata):
                            frozen_backlog_cards.append(backlog_card)
                        else:
                            retained_cards.append(backlog_card)
            else:
                applied_mode = "full"
                if quarantine_reason == "requested":
                    quarantine_reason = "full_mode"

            frozen_cards = [active_card, *frozen_backlog_cards]
            write_thaw_latch = not (applied_mode == "dependency" and retained_cards)

            now = datetime.now(timezone.utc)
            batch_id = now.strftime("%Y%m%dT%H%M%SZ")
            incident_ref = canonical_incident_path
            latch = ResearchRecoveryLatch.model_validate(
                {
                    "batch_id": batch_id,
                    "frozen_at": now,
                    "run_dir": run_dir,
                    "diag_dir": diagnostics_dir,
                    "fingerprint": fingerprint,
                    "failure_signature": failure_signature,
                    "incident_path": incident_ref,
                    "stage": stage,
                    "reason": reason,
                    "frozen_backlog_cards": len(frozen_backlog_cards),
                    "retained_backlog_cards": len(retained_cards),
                    "quarantine_mode_requested": requested_mode,
                    "quarantine_mode_applied": applied_mode,
                    "quarantine_reason": quarantine_reason,
                    "missing_metadata_quarantined": missing_metadata_quarantined,
                }
            )

            blocker_entry = BlockerEntry.model_validate(
                {
                    "occurred_at": now,
                    "task_title": active_card.title,
                    "status": status,
                    "stage_blocked": stage,
                    "source_task": f"agents/tasks.md :: {active_card.heading}",
                    "prompt_artifact": prompt_artifact,
                    "run_dir": run_dir,
                    "diagnostics_dir": diagnostics_dir,
                    "root_cause_summary": reason,
                    "next_action": (
                        (
                            "Research handoff via incident intake and pending-task regeneration"
                            if write_thaw_latch
                            else "Research handoff via incident intake while unrelated backlog work continues promotion"
                        )
                        if status is ExecutionStatus.NEEDS_RESEARCH
                        else "Operator review of blocker state"
                    ),
                    "incident_path": incident_ref,
                    "notes": notes,
                }
            )

            freeze_lines = [
                f"<!-- research_recovery:freeze:start {batch_id} -->",
                f"<!-- frozen_at: {latch.frozen_at.isoformat().replace('+00:00', 'Z')} -->",
            ]
            if incident_ref is not None:
                freeze_lines.append(f"<!-- incident_path: {incident_ref} -->")
            freeze_lines.extend(card.render_markdown() for card in frozen_cards)
            freeze_lines.append(f"<!-- research_recovery:freeze:end {batch_id} -->")
            freeze_block = "\n\n".join(freeze_lines)

            backburner_text = self._read_text(self.paths.backburner_file)
            blocker_text = self._read_text(self.paths.blocker_file)
            updated_backburner = append_markdown_block(backburner_text, freeze_block)
            updated_blocker = insert_after_preamble(blocker_text, blocker_entry.render_markdown())

            self._write_store(
                self.paths.backlog_file,
                TaskStoreDocument(preamble=backlog_document.preamble, cards=retained_cards),
            )
            self._write_store(
                self.paths.tasks_file,
                TaskStoreDocument(preamble=active_document.preamble, cards=[]),
            )
            write_text_atomic(self.paths.backburner_file, updated_backburner)
            write_text_atomic(self.paths.blocker_file, updated_blocker)
            if write_thaw_latch:
                write_research_recovery_latch(self.paths.research_recovery_latch_file, latch)
            else:
                self.paths.research_recovery_latch_file.unlink(missing_ok=True)
            if status is ExecutionStatus.NEEDS_RESEARCH:
                _research_incidents_module().record_incident_recurrence(
                    self.paths,
                    fingerprint=fingerprint,
                    failure_signature=failure_signature,
                    observed_at=now,
                    source="execution_quarantine",
                    incident_path=incident_ref,
                    source_task=active_card.task_id,
                )
            return latch

    def thaw(self, latch: ResearchRecoveryLatch) -> int:
        """Restore frozen cards once visible backlog work shows research has regenerated queue state."""

        with self._locked():
            backlog_document = self._read_store(self.paths.backlog_file)
            if not backlog_document.cards:
                return 0

            backburner_text = self._read_text(self.paths.backburner_file)
            marker_start = f"<!-- research_recovery:freeze:start {latch.batch_id} -->"
            marker_end = f"<!-- research_recovery:freeze:end {latch.batch_id} -->"
            start = backburner_text.find(marker_start)
            end = backburner_text.find(marker_end)
            if start == -1 or end == -1 or end <= start:
                self.paths.research_recovery_latch_file.unlink(missing_ok=True)
                return 0

            end += len(marker_end)
            frozen_block = backburner_text[start:end]
            block_body = frozen_block.replace(marker_start, "", 1).replace(marker_end, "", 1)
            frozen_cards = parse_task_store(block_body).cards

            updated_backlog = TaskStoreDocument(
                preamble=backlog_document.preamble,
                cards=[*backlog_document.cards, *frozen_cards],
            )
            stripped_backburner = (backburner_text[:start] + backburner_text[end:]).strip("\n")
            if stripped_backburner:
                stripped_backburner += "\n"
            else:
                stripped_backburner = (self._default_preamble(self.paths.backburner_file) + "\n")

            self._write_store(self.paths.backlog_file, updated_backlog)
            write_text_atomic(self.paths.backburner_file, stripped_backburner)
            self.paths.research_recovery_latch_file.unlink(missing_ok=True)
            return len(frozen_cards)
