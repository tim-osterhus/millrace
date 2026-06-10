"""Generic local filesystem queue-family interpreter.

This interpreter is family-agnostic: it uses compiled family definitions and
document adapter definitions to operate on any work-item family without
family-ID branch dispatch.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from millrace_ai.architecture import (
    WorkItemDocumentAdapterDefinition,
    WorkItemFamilyDefinition,
)
from millrace_ai.assets import load_builtin_workflow_primitives
from millrace_ai.contracts import Plane
from millrace_ai.errors import QueueStateError

from .paths import WorkspacePaths
from .queue_claims import QueueClaim


class QueueFamilyInterpreter:
    """Generic family-agnostic local filesystem queue interpreter.

    Operations are driven by compiled family definitions and document adapter
    definitions.  No family-ID branch dispatch is performed.
    """

    def __init__(
        self,
        paths: WorkspacePaths,
        *,
        families: tuple[WorkItemFamilyDefinition, ...] | None = None,
        document_adapters: tuple[WorkItemDocumentAdapterDefinition, ...] | None = None,
    ) -> None:
        self._paths = paths
        family_defs = (
            families
            if families is not None
            else load_builtin_workflow_primitives().work_item_families
        )
        adapter_defs = (
            document_adapters
            if document_adapters is not None
            else load_builtin_workflow_primitives().document_adapters
        )
        self._families_by_id: dict[str, WorkItemFamilyDefinition] = {
            f.family_id: f for f in family_defs
        }
        self._adapters_by_id: dict[str, WorkItemDocumentAdapterDefinition] = {
            a.adapter_id: a for a in adapter_defs
        }

    # -- path confinement ----------------------------------------------------

    def _confined_dir(self, relative_dir: str) -> Path:
        """Return a confined absolute path for a runtime-relative directory."""
        parts = PurePosixPath(relative_dir).parts
        if ".." in parts or PurePosixPath(relative_dir).is_absolute():
            raise QueueStateError(
                f"family directory path escapes runtime root: {relative_dir}"
            )
        resolved = (self._paths.runtime_root / relative_dir).resolve()
        try:
            resolved.relative_to(self._paths.runtime_root.resolve())
        except ValueError:
            raise QueueStateError(
                f"family directory path escapes runtime root: {relative_dir}"
            )
        return resolved

    # -- family lookup -------------------------------------------------------

    def _family(self, family_id: str) -> WorkItemFamilyDefinition:
        family = self._families_by_id.get(family_id)
        if family is None:
            raise QueueStateError(f"unknown work item family: {family_id}")
        return family

    def _adapter_for_family(
        self, family: WorkItemFamilyDefinition
    ) -> WorkItemDocumentAdapterDefinition | None:
        return self._adapters_by_id.get(family.document_adapter_id)

    # -- file listing --------------------------------------------------------

    def list_queue_files(
        self, family_id: str
    ) -> tuple[Path, ...]:
        """List all queue files for a family, confined to the queue directory."""
        family = self._family(family_id)
        directory = self._confined_dir(family.queue_dirs.queue)
        if not directory.is_dir():
            return ()
        return tuple(
            sorted(
                p
                for p in directory.glob(f"*{family.file_extension}")
                if p.is_file()
            )
        )

    def list_all_state_files(
        self, family_id: str
    ) -> dict[str, tuple[Path, ...]]:
        """List files in every lifecycle state directory for a family."""
        family = self._family(family_id)
        result: dict[str, tuple[Path, ...]] = {}
        for state in family.lifecycle_states:
            dir_attr = _state_dir_attr_from_work_item_family(family, state)
            if dir_attr is None:
                continue
            directory = self._confined_dir(dir_attr)
            if not directory.is_dir():
                result[state] = ()
                continue
            result[state] = tuple(
                sorted(
                    p
                    for p in directory.glob(f"*{family.file_extension}")
                    if p.is_file()
                )
            )
        return result

    # -- work-item ID extraction ---------------------------------------------

    def extract_work_item_id(
        self,
        family_id: str,
        path: Path,
    ) -> str:
        """Extract the work-item ID from a file path.

        Tries the family's id_field from document content first, then falls
        back to the filename stem.
        """
        family = self._family(family_id)
        if not path.is_file():
            raise QueueStateError(
                f"{family.family_id} file not found: {path}"
            )
        payload = _read_payload(path, family=family)
        if family.id_field and isinstance(payload, dict):
            value = payload.get(family.id_field)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return path.stem

    # -- filename / id validation --------------------------------------------

    def validate_work_item_filename(
        self,
        family_id: str,
        path: Path,
    ) -> tuple[bool, str | None]:
        """Validate that a work-item file's stem matches the document ID.

        For markdown documents, also verifies the id field label is present
        in the frontmatter.  Returns (is_valid, error_message).
        """
        family = self._family(family_id)
        if not path.is_file():
            return False, f"file not found: {path}"
        if path.suffix != family.file_extension:
            return (
                False,
                f"unexpected extension {path.suffix}, expected {family.file_extension}",
            )
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return False, str(exc)

        # For markdown families, verify the id field label appears in frontmatter
        if family.file_extension == ".md" and family.id_field:
            id_label = _derive_id_label(family)
            if id_label is not None and not _has_frontmatter_label(raw, id_label):
                return (
                    False,
                    f"missing {family.id_field} field ({id_label}) in markdown frontmatter",
                )

        try:
            _read_payload(path, family=family)
        except QueueStateError as exc:
            return False, str(exc)
        try:
            item_id = self.extract_work_item_id(family_id, path)
        except Exception as exc:
            return False, f"cannot extract work-item id: {exc}"
        if path.stem != item_id:
            return (
                False,
                f"filename stem does not match {family.id_field}: "
                f"expected {item_id}, found {path.stem}",
            )
        return True, None

    def validate_work_item_id_format(
        self,
        family_id: str,
        work_item_id: str,
    ) -> tuple[bool, str | None]:
        """Validate that a work-item ID is non-empty and safe for filenames."""
        if not work_item_id or not work_item_id.strip():
            return False, "work-item ID may not be empty"
        if "/" in work_item_id or "\\" in work_item_id:
            return False, "work-item ID may not contain path separators"
        if work_item_id in {".", ".."}:
            return False, "work-item ID may not be a path component"
        return True, None

    # -- root lineage filtering ----------------------------------------------

    def matches_root_spec(
        self,
        family_id: str,
        path: Path,
        *,
        root_spec_id: str,
    ) -> bool:
        """Check whether a queue file belongs to the given root spec lineage.

        Matches when the document's root_spec_id field (or any lineage field)
        equals the given root_spec_id.  A document without lineage fields
        always matches (no lineage filter).
        """
        family = self._family(family_id)
        if not family.lineage_fields:
            return True
        if not path.is_file():
            return False
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False
        try:
            payload = _read_payload(path, family=family)
        except QueueStateError:
            payload = None
        if isinstance(payload, dict):
            # Check the canonical root_spec_id field first (exact match),
            # then fall back to any lineage field.
            doc_root_spec = payload.get("root_spec_id")
            if isinstance(doc_root_spec, str) and doc_root_spec:
                return doc_root_spec == root_spec_id
            # No root_spec_id field; check all lineage fields
            lineage_values = [
                v
                for field in family.lineage_fields
                if isinstance(payload.get(field), str)
                and (v := payload[field])
            ]
            if not lineage_values:
                return True
            return root_spec_id in lineage_values
        # Markdown fallback
        root_line_prefix = "Root-Spec-ID:"
        for line in raw.splitlines():
            if line.startswith(root_line_prefix):
                value = line.removeprefix(root_line_prefix).strip()
                return not value or value == root_spec_id
        return True

    # -- dependency filtering ------------------------------------------------

    def _resolved_dependency_ids(
        self, family: WorkItemFamilyDefinition
    ) -> set[str]:
        """Collect resolved (done) work-item IDs for dependency checking."""
        done_dir = self._confined_dir(family.queue_dirs.done)
        if not done_dir.is_dir():
            return set()
        resolved: set[str] = set()
        for path in done_dir.glob(f"*{family.file_extension}"):
            if not path.is_file():
                continue
            try:
                resolved.add(self.extract_work_item_id(family.family_id, path))
            except Exception:
                continue
        return resolved

    def dependencies_satisfied(
        self,
        family_id: str,
        path: Path,
    ) -> tuple[bool, list[str] | None]:
        """Check whether all dependencies of a queued work item are resolved.

        Returns (satisfied, unresolved_ids).  If the family has no dependency
        field, always returns (True, None).
        """
        family = self._family(family_id)
        if not family.dependency_field:
            return True, None
        if not path.is_file():
            return False, None
        payload = _read_payload(path, family=family)
        deps = _extract_dependencies(payload, family=family)
        if not deps:
            return True, None
        resolved = self._resolved_dependency_ids(family)
        unresolved = [d for d in deps if d not in resolved]
        return len(unresolved) == 0, unresolved if unresolved else None

    # -- one-active policy checks --------------------------------------------

    def one_active_policy_check(
        self,
        family_id: str,
    ) -> tuple[bool, str | None]:
        """Check the one-active policy for a family.

        Returns (allowed, reason) indicating whether a new active claim is
        permitted.  A return of (False, reason) represents normal one-active
        saturation; callers should return None rather than raise.

        Raises QueueStateError for corrupted active state (multiple active
        items when policy forbids it) and for unsupported policy values.
        """
        family = self._family(family_id)
        policy = family.one_active_policy

        if policy == "work_item":
            return True, None
        if policy == "custom_partition":
            return True, None
        if policy == "lane":
            raise QueueStateError(
                "one_active_policy='lane' is not currently supported. "
                "Use 'family' or 'plane' instead."
            )
        if policy == "lineage":
            raise QueueStateError(
                "one_active_policy='lineage' is not currently supported. "
                "Use 'family' or 'plane' instead."
            )

        if policy == "family":
            active_count = self._count_active_files(family)
            if active_count > 1:
                raise QueueStateError(
                    f"Corrupted active state: {active_count} active items "
                    f"found in {family.family_id} active directory "
                    f"(one_active_policy=family)"
                )
            if active_count == 1:
                return (
                    False,
                    f"one_active_policy=family: {family.family_id} "
                    f"already has an active item",
                )
            return True, None

        if policy == "plane":
            plane_families = [
                f for f in self._families_by_id.values()
                if f.plane is family.plane
            ]
            total_active = 0
            for pf in plane_families:
                total_active += self._count_active_files(pf)
            if total_active > 1:
                raise QueueStateError(
                    f"Corrupted active state: {total_active} active items "
                    f"found across {family.plane.value} plane "
                    f"(one_active_policy=plane)"
                )
            if total_active == 1:
                return (
                    False,
                    f"one_active_policy=plane: an active item already "
                    f"exists in {family.plane.value} plane",
                )
            return True, None

        return True, None

    def _count_active_files(
        self, family: WorkItemFamilyDefinition
    ) -> int:
        """Count active files for a family, returning 0 if dir doesn't exist."""
        active_dir = self._confined_dir(family.queue_dirs.active)
        if not active_dir.is_dir():
            return 0
        return len([
            p for p in active_dir.glob(f"*{family.file_extension}")
            if p.is_file()
        ])

    # -- race-safe queue-to-active movement ----------------------------------

    def claim_next(
        self,
        family_id: str,
        *,
        root_spec_id: str | None = None,
        document_validator: "Callable[[str, Path], tuple[bool, str | None]] | None" = None,
    ) -> QueueClaim | None:
        """Claim the next queued work item with atomic queue-to-active rename.

        This is race-safe: pathlib.Path.replace() is atomic on the same
        filesystem.  An optional *document_validator* callback can be
        supplied to validate document content before claiming; invalid
        documents are quarantined.
        """
        family = self._family(family_id)

        # One-active check: normal saturation returns None, corrupted
        # state raises QueueStateError from within one_active_policy_check.
        allowed, reason = self.one_active_policy_check(family_id)
        if not allowed:
            return None

        # Gather eligible candidates
        candidates = self._eligible_candidates(
            family_id,
            root_spec_id=root_spec_id,
            document_validator=document_validator,
        )
        if not candidates:
            return None

        # Sort according to family sort policy
        candidates.sort(key=lambda c: (c[0], c[1]))
        if family.sort_policy == "created_at_desc":
            candidates.reverse()

        active_dir = self._confined_dir(family.queue_dirs.active)

        # Race-safe loop: atomically move the next candidate
        while candidates:
            _timestamp, item_id, source = candidates.pop(0)
            destination = active_dir / source.name
            try:
                active_dir.mkdir(parents=True, exist_ok=True)
                source.replace(destination)
            except FileNotFoundError:
                # Raced away, re-scan and try again
                candidates = self._eligible_candidates(
                    family_id,
                    root_spec_id=root_spec_id,
                    document_validator=document_validator,
                )
                if candidates:
                    if family.sort_policy == "created_at_desc":
                        candidates.sort(key=lambda c: (c[0], c[1]))
                        candidates.reverse()
                    else:
                        candidates.sort(key=lambda c: (c[0], c[1]))
                continue
            return QueueClaim(
                family_id=family.family_id,
                work_item_id=item_id,
                path=destination,
                plane=family.plane,
                source_state=family.claimable_state,
                source_path=source,
            )

        return None

    def _eligible_candidates(
        self,
        family_id: str,
        *,
        root_spec_id: str | None = None,
        document_validator: "Callable[[str, Path], tuple[bool, str | None]] | None" = None,
    ) -> list[tuple[datetime, str, Path]]:
        """Collect and validate all eligible queue candidates for a family."""
        candidates: list[tuple[datetime, str, Path]] = []

        for path in self.list_queue_files(family_id):
            # Validate filename/id
            is_valid, error = self.validate_work_item_filename(family_id, path)
            if not is_valid:
                self.quarantine_invalid_artifact(
                    family_id, path, error or "filename/id mismatch"
                )
                continue

            # Optional document-level validator (e.g., pydantic model check)
            if document_validator is not None:
                doc_valid, doc_error = document_validator(family_id, path)
                if not doc_valid:
                    self.quarantine_invalid_artifact(
                        family_id, path, doc_error or "invalid document"
                    )
                    continue

            # Root lineage filtering
            if root_spec_id is not None and not self.matches_root_spec(
                family_id, path, root_spec_id=root_spec_id
            ):
                continue

            # Dependency filtering
            deps_ok, _unresolved = self.dependencies_satisfied(family_id, path)
            if not deps_ok:
                continue

            item_id = self.extract_work_item_id(family_id, path)
            timestamp = self._sort_timestamp(family_id, path)
            candidates.append((timestamp, item_id, path))

        return candidates

    # -- queue depth ---------------------------------------------------------

    def queue_depth(self, family_id: str) -> int:
        """Return the number of files in the family queue directory."""
        return len(self.list_queue_files(family_id))

    def queue_depths_by_family(
        self,
    ) -> dict[str, int]:
        """Return queue depth for every known family."""
        return {
            family_id: self.queue_depth(family_id)
            for family_id in sorted(self._families_by_id)
        }

    # -- invalid artifact identification and quarantine ----------------------

    def identify_invalid_artifacts(
        self, family_id: str
    ) -> tuple[tuple[Path, str], ...]:
        """Scan the queue directory and return (path, error) for invalid files."""
        family = self._family(family_id)
        invalid: list[tuple[Path, str]] = []
        for path in self.list_queue_files(family_id):
            is_valid, error = self.validate_work_item_filename(family_id, path)
            if not is_valid:
                invalid.append((path, error or "invalid artifact"))
            else:
                # Check parseability
                try:
                    _read_payload(path, family=family)
                except Exception as exc:
                    invalid.append((path, str(exc)))
        return tuple(invalid)

    def quarantine_invalid_artifact(
        self,
        family_id: str,
        source_path: Path,
        error: str,
    ) -> Path | None:
        """Quarantine an invalid artifact by renaming it and writing diagnostics.

        Returns the quarantine destination path, or None if the file no
        longer exists (raced away).
        """
        family = self._family(family_id)
        policy = family.invalid_artifact_policy

        if policy == "reject":
            # Just log, don't move
            self._write_quarantine_diagnostic(
                family, source_path, error_message=error
            )
            return None

        if policy in {"block_source", "quarantine"}:
            destination = source_path.with_suffix(
                f"{source_path.suffix}.invalid"
            )
            suffix_index = 1
            while destination.exists():
                destination = source_path.with_suffix(
                    f"{source_path.suffix}.invalid.{suffix_index}"
                )
                suffix_index += 1

            try:
                source_path.replace(destination)
            except FileNotFoundError:
                return None

            self._write_quarantine_diagnostic(
                family, source_path, error_message=error
            )
            return destination

        return None

    def _write_quarantine_diagnostic(
        self,
        family: WorkItemFamilyDefinition,
        source_path: Path,
        *,
        error_message: str,
    ) -> Path:
        """Write a quarantine diagnostics entry."""
        queue_dir = self._confined_dir(family.queue_dirs.queue)
        log_path = queue_dir / "invalid-artifacts.jsonl"
        adapter = self._adapter_for_family(family)
        payload = {
            "at": datetime.now(timezone.utc).isoformat(),
            "family_id": family.family_id,
            "source_path": str(source_path),
            "source_name": source_path.name,
            "adapter_id": adapter.adapter_id if adapter else None,
            "error_class": "QueueStateError",
            "error_message": error_message,
        }
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
        return log_path

    # -- timestamp extraction ------------------------------------------------

    def _sort_timestamp(
        self,
        family_id: str,
        path: Path,
    ) -> datetime:
        family = self._family(family_id)
        try:
            payload = _read_payload(path, family=family)
        except QueueStateError:
            payload = None
        if isinstance(payload, dict):
            value = payload.get(family.created_at_field)
            if isinstance(value, str):
                try:
                    return datetime.fromisoformat(
                        value.replace("Z", "+00:00")
                    )
                except ValueError:
                    pass
        try:
            return datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            )
        except OSError:
            return datetime.now(timezone.utc)

    # -- lifecycle state transitions ----------------------------------------

    def move_active_to_done(
        self,
        family_id: str,
        work_item_id: str,
    ) -> Path:
        """Move an active work item to the done state."""
        return self._move_between_states(
            family_id,
            work_item_id,
            source_state=self._family(family_id).active_state,
            destination_state=self._family(family_id).done_state,
        )

    def move_active_to_blocked(
        self,
        family_id: str,
        work_item_id: str,
    ) -> Path:
        """Move an active work item to the blocked state."""
        return self._move_between_states(
            family_id,
            work_item_id,
            source_state=self._family(family_id).active_state,
            destination_state=self._family(family_id).blocked_state,
        )

    def move_active_to_queue(
        self,
        family_id: str,
        work_item_id: str,
        *,
        reason: str,
    ) -> Path:
        """Requeue an active work item and write a requeue reason audit event."""
        destination = self._move_between_states(
            family_id,
            work_item_id,
            source_state=self._family(family_id).active_state,
            destination_state=self._family(family_id).claimable_state,
        )
        self._write_requeue_reason(
            family_id,
            work_item_id,
            reason=reason,
        )
        return destination

    def move_blocked_to_queue(
        self,
        family_id: str,
        work_item_id: str,
        *,
        reason: str,
        actor: str | None = None,
        auto: bool | None = None,
        failure_class: str | None = None,
        attempt_number: int | None = None,
    ) -> Path:
        """Move a blocked work item back to the queue and write a requeue reason audit event."""
        destination = self._move_between_states(
            family_id,
            work_item_id,
            source_state=self._family(family_id).blocked_state,
            destination_state=self._family(family_id).claimable_state,
        )
        self._write_requeue_reason(
            family_id,
            work_item_id,
            reason=reason,
            actor=actor,
            auto=auto,
            source_state="blocked",
            destination_state="queue",
            failure_class=failure_class,
            attempt_number=attempt_number,
        )
        return destination

    def _move_between_states(
        self,
        family_id: str,
        work_item_id: str,
        *,
        source_state: str,
        destination_state: str,
    ) -> Path:
        """Atomically move a work item between lifecycle state directories."""
        family = self._family(family_id)
        source_dir_attr = _state_dir_attr_from_work_item_family(family, source_state)
        dest_dir_attr = _state_dir_attr_from_work_item_family(family, destination_state)
        if source_dir_attr is None:
            raise QueueStateError(
                f"unknown source state {source_state} for family {family_id}"
            )
        if dest_dir_attr is None:
            raise QueueStateError(
                f"unknown destination state {destination_state} for family {family_id}"
            )
        source_dir = self._confined_dir(source_dir_attr)
        dest_dir = self._confined_dir(dest_dir_attr)
        source = source_dir / f"{work_item_id}{family.file_extension}"
        if not source.is_file():
            raise QueueStateError(
                f"{family.family_id} {work_item_id} is not {source_state}"
            )
        destination = dest_dir / source.name
        if destination.exists():
            raise QueueStateError(
                f"{family.family_id} {work_item_id} already exists at destination"
            )
        dest_dir.mkdir(parents=True, exist_ok=True)
        if family.file_extension == ".json":
            _move_json_document(source, destination, destination_state=destination_state)
        else:
            source.replace(destination)
        return destination

    def _write_requeue_reason(
        self,
        family_id: str,
        work_item_id: str,
        *,
        reason: str,
        actor: str | None = None,
        auto: bool | None = None,
        source_state: str | None = None,
        destination_state: str | None = None,
        failure_class: str | None = None,
        attempt_number: int | None = None,
    ) -> Path:
        """Write a requeue reason audit event to the queue directory."""
        family = self._family(family_id)
        cleaned_reason = reason.strip()
        if not cleaned_reason:
            raise QueueStateError("requeue reason is required")
        queue_dir = self._confined_dir(family.queue_dirs.queue)
        log_path = queue_dir / f"{work_item_id}.requeue.jsonl"
        payload: dict[str, Any] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "family_id": family_id,
            "reason": cleaned_reason,
        }
        if actor is not None:
            payload["actor"] = actor
        if auto is not None:
            payload["auto"] = auto
        if source_state is not None:
            payload["source_state"] = source_state
        if destination_state is not None:
            payload["destination_state"] = destination_state
        if failure_class is not None:
            payload["failure_class"] = failure_class
        if attempt_number is not None:
            payload["attempt_number"] = attempt_number
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True) + "\n")
        return log_path

    # -- plane accessors -----------------------------------------------------

    @property
    def families(self) -> tuple[WorkItemFamilyDefinition, ...]:
        return tuple(self._families_by_id.values())

    def families_for_plane(self, plane: Plane) -> tuple[WorkItemFamilyDefinition, ...]:
        return tuple(f for f in self.families if f.plane is plane)

    def family(self, family_id: str) -> WorkItemFamilyDefinition:
        return self._family(family_id)


# -- helpers ----------------------------------------------------------------

def _read_payload_or_none(
    path: Path,
    *,
    family: WorkItemFamilyDefinition,
) -> dict[str, Any] | None:
    """Read a payload dict for JSON families, None for non-JSON."""
    if family.file_extension != ".json":
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _move_json_document(
    source: Path,
    destination: Path,
    *,
    destination_state: str,
) -> None:
    """Move a JSON document and update its status field to match the destination state."""
    import json as _json

    try:
        raw = source.read_text(encoding="utf-8")
        payload = _json.loads(raw)
    except (OSError, _json.JSONDecodeError):
        source.replace(destination)
        return
    if not isinstance(payload, dict):
        source.replace(destination)
        return
    if "status" in payload:
        payload["status"] = destination_state
    destination.write_text(_json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    source.unlink()


def _read_payload(
    path: Path,
    *,
    family: WorkItemFamilyDefinition,
) -> dict[str, Any] | None:
    """Read raw text and return parsed dict (JSON) or parsed markdown fields.

    For non-JSON families, returns a dict of field-name -> value parsed from
    the markdown frontmatter.  Scalar fields use the string value after the
    colon.  List fields collect bullet items after the label line.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise QueueStateError(f"cannot read {family.family_id} file {path}: {exc}")

    if family.file_extension == ".json":
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise QueueStateError(
                f"invalid JSON in {family.family_id} file {path}: {exc}"
            )
        if not isinstance(payload, dict):
            raise QueueStateError(
                f"JSON work item artifact must be an object: {path}"
            )
        return payload

    # Markdown: parse known scalar and list fields.
    # Extend label mapping with the family's id_field so custom families work.
    label_to_name = dict(_FIELD_LABEL_TO_NAME)
    if family.id_field:
        id_label = _derive_id_label(family)
        if id_label is not None:
            # Strip trailing colon added by _derive_id_label
            label_key = id_label.rstrip(":")
            label_to_name[label_key] = family.id_field

    result: dict[str, Any] = {}
    lines = raw.splitlines()
    idx = 0
    while idx < len(lines):
        stripped = lines[idx].strip()
        idx += 1
        if not stripped:
            continue
        colon_pos = stripped.find(":")
        if colon_pos == -1:
            continue
        label = stripped[:colon_pos].strip()
        value_after = stripped[colon_pos + 1:].strip()
        field_name = label_to_name.get(label)
        if field_name is None:
            continue
        if field_name in result:
            continue  # duplicate
        if value_after:
            # Scalar value on the same line
            if field_name in _KNOWN_LIST_FIELDS:
                result[field_name] = [value_after]
            else:
                result[field_name] = value_after
            continue
        # Multi-line list: collect bullet items
        items: list[str] = []
        while idx < len(lines):
            next_line = lines[idx].strip()
            if not next_line:
                idx += 1
                if items:
                    break
                continue
            if next_line.startswith("- "):
                items.append(next_line[2:].strip())
                idx += 1
            elif ":" in next_line and "- " not in next_line[:6]:
                # Next field
                break
            else:
                idx += 1  # skip non-bullet continuation
        if items:
            result[field_name] = items
    return result


_KNOWN_LIST_FIELDS: set[str] = {
    "depends_on",
    "dependencies",
    "blocks",
    "tags",
    "target_paths",
    "acceptance",
    "required_checks",
    "references",
    "risk",
    "goals",
    "non_goals",
    "scope",
    "constraints",
    "assumptions",
    "entrypoints",
    "required_skills",
    "decomposition_hints",
    "source_refs",
    "preferred_output_paths",
    "originating_run_ids",
    "artifact_paths",
    "observed_symptoms",
    "failed_attempts",
    "evidence_paths",
    "related_run_ids",
    "related_stage_results",
}


# Label-to-field-name mapping for markdown documents
_FIELD_LABEL_TO_NAME: dict[str, str] = {
    "Task-ID": "task_id",
    "Spec-ID": "spec_id",
    "Probe-ID": "probe_id",
    "Incident-ID": "incident_id",
    "Learning-Request-ID": "learning_request_id",
    "Root-Spec-ID": "root_spec_id",
    "Root-Idea-ID": "root_idea_id",
    "Draft-ID": "draft_id",
    "Created-At": "created_at",
    "Depends-On": "depends_on",
    "Dependency-Field": "dependencies",
    "Title": "title",
    "Summary": "summary",
    "Manifest-ID": "manifest_id",
}


_MARKDOWN_DEP_FIELD_ALIASES: dict[str, str] = {
    "dependencies": "depends_on",
}


def _extract_dependencies(
    payload: dict[str, Any] | None,
    *,
    family: WorkItemFamilyDefinition,
) -> list[str]:
    """Extract dependency IDs from a document payload."""
    if payload is None or not family.dependency_field:
        return []
    dep_field = family.dependency_field
    deps = payload.get(dep_field)
    # Fall back to markdown alias when the family field (e.g. "dependencies")
    # differs from the actual markdown-parsed key (e.g. "depends_on").
    if deps is None and dep_field in _MARKDOWN_DEP_FIELD_ALIASES:
        deps = payload.get(_MARKDOWN_DEP_FIELD_ALIASES[dep_field])
    if deps is None:
        return []
    if isinstance(deps, list):
        # Filter out "none" placeholder used for empty dependencies
        return [str(d) for d in deps if d and str(d).strip().lower() != "none"]
    if isinstance(deps, str):
        cleaned = deps.strip()
        if not cleaned or cleaned.lower() == "none":
            return []
        return [cleaned]
    return []


def _state_dir_attr_from_work_item_family(
    family: WorkItemFamilyDefinition,
    state: str,
) -> str | None:
    """Map a lifecycle state name to the queue_dirs attribute value."""
    normalized = state.strip()
    if normalized == family.claimable_state:
        return family.queue_dirs.queue
    if normalized == family.active_state:
        return family.queue_dirs.active
    if normalized == family.done_state:
        return family.queue_dirs.done
    if normalized == family.blocked_state:
        return family.queue_dirs.blocked
    if (
        family.canceled_state is not None
        and normalized == family.canceled_state
    ):
        return family.queue_dirs.canceled
    if (
        family.queue_dirs.superseded is not None
        and normalized in {"superseded"}
    ):
        return family.queue_dirs.superseded
    if (
        family.queue_dirs.canceled is not None
        and normalized in {"canceled"}
    ):
        return family.queue_dirs.canceled
    return None


__all__ = [
    "QueueFamilyInterpreter",
]


# -- family id field labels -----------------------------------------------

_FAMILY_ID_LABELS: dict[str, str] = {
    "task": "Task-ID:",
    "spec": "Spec-ID:",
    "probe": "Probe-ID:",
    "incident": "Incident-ID:",
    "learning_request": "Learning-Request-ID:",
    "blueprint_draft": "Draft-ID:",
}


def _derive_id_label(family: WorkItemFamilyDefinition) -> str | None:
    """Derive the frontmatter id-label for a family's id_field.

    Uses the static mapping for built-in families and derives a
    PascalCase label for custom families (e.g. custom_id -> Custom-ID:).
    Returns None when the family has no id_field.
    """
    if not family.id_field:
        return None
    builtin_label = _FAMILY_ID_LABELS.get(family.family_id)
    if builtin_label is not None:
        return builtin_label
    # Derive label from id_field using standard title-case convention
    label_title = family.id_field.replace("_", "-").title()
    return f"{label_title}:"


def _has_frontmatter_label(raw: str, label: str) -> bool:
    """Check if a markdown document contains the given frontmatter label."""
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith(label):
            return True
        # Stop at first non-empty, non-frontmatter line (content started)
        if stripped and not stripped.startswith("#") and ":" not in stripped:
            break
    return False


# -- markdown document validation helpers --------------------------------


def _validate_markdown_document_content(
    *,
    path: Path,
    family: WorkItemFamilyDefinition,
) -> tuple[bool, str | None]:
    """Validate a markdown document parseability.

    For families with known document models, attempts pydantic model parsing
    to catch malformed documents.  For custom families without a known model,
    always returns valid.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return False, str(exc)

    # For built-in families, attempt pydantic model validation as a
    # best-effort check.  Parse failures indicate malformed documents that
    # should be quarantined.  Parsing uses strict=False to only catch
    # critical structural issues, not missing optional fields.
    _FAMILY_MODEL_MAP: dict[str, str] = {
        "task": "TaskDocument",
        "spec": "SpecDocument",
        "probe": "ProbeDocument",
        "incident": "IncidentDocument",
        "learning_request": "LearningRequestDocument",
        "blueprint_draft": "BlueprintDraftDocument",
    }
    model_name = _FAMILY_MODEL_MAP.get(family.family_id)
    if model_name is None:
        return True, None

    # Only validate if the document looks like it has the family's id field
    # in its frontmatter.  If the markdown is fully malformed (no frontmatter),
    # the parse_work_document_as call will catch it.
    has_id_label = False
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" in line:
            has_id_label = True
            break
        # If we hit content (# heading) without any frontmatter, this is
        # likely a malformed document.
        if line.startswith("#") and not has_id_label:
            return False, "missing frontmatter fields in markdown artifact"

    if not has_id_label:
        return False, "missing frontmatter fields in markdown artifact"

    # Attempt pydantic model validation as best-effort
    from millrace_ai.work_documents import parse_work_document_as

    model_cls = _resolve_model_class(model_name)
    if model_cls is None:
        return True, None
    try:
        parse_work_document_as(raw, model=model_cls, path=path)
    except Exception as exc:
        return False, str(exc)
    return True, None


def _resolve_model_class(model_name: str):
    """Resolve a document model class by name from millrace_ai.contracts."""
    import importlib

    mod = importlib.import_module("millrace_ai.contracts")
    return getattr(mod, model_name, None)
