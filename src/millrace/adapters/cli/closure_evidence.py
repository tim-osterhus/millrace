"""Session-owned read-only closure evidence pages.

Pages are derived from the durable sealed closure snapshot and authenticated by
the session's dispatch projection. They are runner-readable evidence views,
never workflow authority. A session materializes its pages before runner start
and removes them after its durable completion.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
import time
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path, PurePosixPath

from millrace.adapters.cli import session_persistence
from millrace.adapters.cli.context import OpenRuntimeContext
from millrace.contracts.closure_request_overflow import (
    OVERFLOW_KEY,
    ClosureEvidencePage,
    materialization_component,
)
from millrace.contracts.runner import RunnerDispatchEnvelope
from millrace.contracts.state import RunnerSessionRecord, RuntimeState
from millrace.operator.dispatch import closure_evidence_pages_for_run

_TEMP_PREFIX = ".closure-evidence-"


class ClosureEvidenceMaterializationError(ValueError):
    """Session evidence pages cannot be materialized safely."""


def materialize_session_closure_evidence(
    runtime: OpenRuntimeContext,
    *,
    state: RuntimeState,
    session: RunnerSessionRecord,
) -> int:
    """Write or verify this session's pages before start; returns the page count."""
    pages = closure_evidence_pages_for_run(state=state, run_id=session.run_id)
    if not pages:
        return 0
    relative = _shared_directory(pages)
    if relative.name != materialization_component(
        session.session_id, session.dispatch_generation
    ):
        raise ClosureEvidenceMaterializationError("pages are not session-owned")
    directory = _authorized_directory(runtime, relative)
    if _matches(directory, pages):
        return len(pages)
    if _lexists(directory):
        # Session-owned derived bytes only: rebuild from durable authority.
        _remove_tree(directory)
    _write_pages(directory, pages)
    if not _matches(directory, pages):
        raise ClosureEvidenceMaterializationError("materialized pages did not verify")
    return len(pages)


def cleanup_session_closure_evidence(
    runtime: OpenRuntimeContext,
    session: RunnerSessionRecord,
    dispatch: RunnerDispatchEnvelope,
    event_redaction_policy: session_persistence.RedactionPolicy,
) -> str:
    """Remove this session's derived pages after its durable completion."""
    projection = dispatch.work_item_payload.get(OVERFLOW_KEY)
    if not isinstance(projection, Mapping):
        return "not_required"
    try:
        relative = PurePosixPath(str(projection.get("materialization_directory")))
        if relative.name != materialization_component(
            session.session_id, session.dispatch_generation
        ):
            raise ClosureEvidenceMaterializationError("directory is not session-owned")
        directory = _authorized_directory(runtime, relative)
        if not _lexists(directory):
            disposition = "absent"
        else:
            _remove_tree(directory)
            disposition = "removed"
        _remove_empty_parent(directory.parent, stop=runtime.paths.workspace_path)
    except (ClosureEvidenceMaterializationError, OSError, ValueError) as exc:
        disposition = "refused"
        detail = str(exc)[:256]
    else:
        detail = ""
    session_persistence._record_session_event(
        runtime,
        session=session,
        kind="diagnostic",
        observed_at=time.time_ns(),
        payload={
            "closure_evidence_cleanup": disposition,
            **({"detail": detail} if detail else {}),
        },
        replay_key=(
            f"closure-evidence-cleanup:{session.session_id}:"
            f"{session.dispatch_generation}"
        ),
        redaction_policy=event_redaction_policy,
    )
    return disposition


def closure_evidence_drift(
    runtime: OpenRuntimeContext,
    *,
    session: RunnerSessionRecord,
    dispatch: RunnerDispatchEnvelope,
) -> str | None:
    """Re-verify this session's pages before any runner result is applied.

    The authenticated dispatch projection pins the index pages; the verified index
    pins every segment page. Added, removed, replaced or altered files mean the
    evaluator did not read authentic evidence, so the result must be refused as a
    runtime integrity failure instead of being accepted as a verdict.
    """
    projection = dispatch.work_item_payload.get(OVERFLOW_KEY)
    if not isinstance(projection, Mapping):
        return None
    try:
        relative = PurePosixPath(str(projection.get("materialization_directory")))
        if relative.name != materialization_component(
            session.session_id, session.dispatch_generation
        ):
            return "closure evidence directory is not owned by this session"
        directory = _authorized_directory(runtime, relative)
        if not stat.S_ISDIR(directory.lstat().st_mode):
            return "closure evidence directory is not a directory"
        expected = _expected_listing(directory, relative, projection)
        if set(os.listdir(directory)) != set(expected):
            return "closure evidence pages were added or removed during the session"
        for name, (digest, size) in sorted(expected.items()):
            data = _read_exact_regular(directory / name, size)
            if sha256(data).hexdigest() != digest:
                return f"closure evidence page {name} changed during the session"
    except (
        ClosureEvidenceMaterializationError,
        OSError,
        TypeError,
        ValueError,
        KeyError,
    ) as exc:
        return f"closure evidence pages could not be verified: {exc}"[:256]
    return None


def record_materialization_refusal(
    runtime: OpenRuntimeContext,
    *,
    session: RunnerSessionRecord,
    reason: str,
    event_redaction_policy: session_persistence.RedactionPolicy,
) -> None:
    """Best-effort operator diagnostic for a pre-start materialization refusal."""
    session_persistence._record_session_event(
        runtime,
        session=session,
        kind="diagnostic",
        observed_at=time.time_ns(),
        payload={"closure_evidence_materialization_refused": reason[:256]},
        replay_key=(
            f"closure-evidence-refused:{session.session_id}:"
            f"{session.dispatch_generation}"
        ),
        redaction_policy=event_redaction_policy,
    )


def _expected_listing(
    directory: Path,
    relative: PurePosixPath,
    projection: Mapping[str, object],
) -> dict[str, tuple[str, int]]:
    expected: dict[str, tuple[str, int]] = {}

    def admit(descriptor: object) -> str:
        if not isinstance(descriptor, Mapping):
            raise ClosureEvidenceMaterializationError("invalid page descriptor")
        path = PurePosixPath(str(descriptor["path"]))
        digest, size = descriptor["sha256"], descriptor["bytes"]
        if (
            path.parent != relative
            or not isinstance(digest, str)
            or type(size) is not int
            or path.name in expected
        ):
            raise ClosureEvidenceMaterializationError("invalid page descriptor")
        expected[path.name] = (digest, size)
        return path.name

    index_pages = projection.get("index_pages")
    if not isinstance(index_pages, (list, tuple)) or not index_pages:
        raise ClosureEvidenceMaterializationError("projection has no index pages")
    index = bytearray()
    for descriptor in index_pages:
        name = admit(descriptor)
        data = _read_exact_regular(directory / name, expected[name][1])
        if sha256(data).hexdigest() != expected[name][0]:
            raise ClosureEvidenceMaterializationError(f"index page {name} changed")
        index += data
    document = json.loads(bytes(index))
    for segment in document["segments"]:
        for descriptor in segment["pages"]:
            admit(descriptor)
    return expected


def _shared_directory(pages: Sequence[ClosureEvidencePage]) -> PurePosixPath:
    parents = {PurePosixPath(page.path).parent for page in pages}
    if len(parents) != 1:
        raise ClosureEvidenceMaterializationError("pages must share one directory")
    return parents.pop()


def _authorized_directory(runtime: OpenRuntimeContext, relative: PurePosixPath) -> Path:
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or ".millrace" in relative.parts
        or "\\" in str(relative)
    ):
        raise ClosureEvidenceMaterializationError("unsafe materialization directory")
    paths = runtime.paths
    workspace = Path(paths.workspace_path).resolve()
    directory = workspace.joinpath(*relative.parts)
    current = workspace
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ClosureEvidenceMaterializationError(
                "materialization path passes through a symlink"
            )
    for protected in (Path(paths.db_path), Path(paths.cas_path)):
        resolved = protected.resolve()
        if _overlaps(directory, resolved):
            raise ClosureEvidenceMaterializationError(
                "materialization path overlaps a protected runtime path"
            )
    return directory


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _lexists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _matches(directory: Path, pages: Sequence[ClosureEvidencePage]) -> bool:
    try:
        if not stat.S_ISDIR(directory.lstat().st_mode):
            return False
        expected = {PurePosixPath(page.path).name: page.data for page in pages}
        names = set(os.listdir(directory))
        if names != set(expected):
            return False
        for name, data in expected.items():
            if _read_exact_regular(directory / name, len(data)) != data:
                return False
    except (ClosureEvidenceMaterializationError, OSError):
        return False
    return True


def _read_exact_regular(path: Path, size: int) -> bytes:
    """Read one page without following links or blocking on special files.

    The open never follows a final symlink and never waits on a FIFO or device;
    the opened descriptor must be a regular file of exactly the authenticated
    length, and at most that many bytes plus one are read.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ClosureEvidenceMaterializationError(
            f"page {path.name} is not a readable regular file"
        ) from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size != size:
            raise ClosureEvidenceMaterializationError(
                f"page {path.name} is not the expected regular file"
            )
        chunks: list[bytes] = []
        remaining = size + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(remaining, 1 << 20))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(descriptor)
    data = b"".join(chunks)
    if len(data) != size:
        raise ClosureEvidenceMaterializationError(
            f"page {path.name} changed while being read"
        )
    return data


def _write_pages(directory: Path, pages: Sequence[ClosureEvidencePage]) -> None:
    directory.parent.mkdir(parents=True, exist_ok=True)
    temp = directory.parent / f"{_TEMP_PREFIX}{secrets.token_hex(8)}"
    temp.mkdir(mode=0o700)
    try:
        for page in pages:
            path = temp / PurePosixPath(page.path).name
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(page.data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(path, 0o444)
        # Rename while writable: a read-only directory cannot be renamed on
        # every platform (it must update its own parent link).
        os.rename(temp, directory)
        os.chmod(directory, 0o555)
    except BaseException:
        if _lexists(temp):
            _remove_tree(temp)
        raise


def _remove_tree(directory: Path) -> None:
    if not stat.S_ISDIR(directory.lstat().st_mode):
        raise ClosureEvidenceMaterializationError("evidence path is not a directory")
    os.chmod(directory, 0o700)
    for entry in os.scandir(directory):
        if entry.is_dir(follow_symlinks=False):
            os.chmod(entry.path, 0o700)
    shutil.rmtree(directory)


def _remove_empty_parent(parent: Path, *, stop: Path) -> None:
    try:
        if parent != Path(stop).resolve() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        return


__all__ = (
    "ClosureEvidenceMaterializationError",
    "cleanup_session_closure_evidence",
    "closure_evidence_drift",
    "materialize_session_closure_evidence",
    "record_materialization_refusal",
)
