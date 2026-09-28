"""Private, single-owner demo store with descriptor-relative durable commits.

This is not a public runner or a general concurrent SQLite backend. The caller
must authenticate its receipt and retain its exclusive foreground lock. No
ordinary path-based SQLite connection is opened against retained runtime data.
"""

from __future__ import annotations

import fcntl
import os
import sqlite3
import stat
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, NoReturn

from millrace.adapters.cli.context import CliWorkspacePaths, OpenRuntimeContext
from millrace.substrate import _sqlite_controls as controls
from millrace.substrate._sqlite_schema import (
    configure_connection,
    read_metadata,
    validate_metadata,
    validate_schema_shape,
)
from millrace.substrate.cas import (
    ContentAddressedByteStore,
    _digest_hex,
    storage_digest_for_bytes,
)
from millrace.substrate.errors import CasDigestMismatch, CasObjectNotFound
from millrace.substrate.runner_session_events import (
    RunnerSessionEventSnapshot,
    RunnerSessionEventStore,
)
from millrace.substrate.sqlite import SQLiteRuntimeStore


class OwnedRuntimeRefusal(ValueError):
    pass


def _identity(st: os.stat_result) -> list[int]:
    return [st.st_dev, st.st_ino, st.st_uid, stat.S_IMODE(st.st_mode)]


def _check(st: os.stat_result, directory: bool = False) -> None:
    valid = (
        stat.S_ISDIR(st.st_mode)
        if directory
        else stat.S_ISREG(st.st_mode) and st.st_nlink == 1
    )
    if not valid or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
        raise OwnedRuntimeRefusal("unsafe_owned_member")


class _Tree:
    def __init__(
        self,
        root: Path,
        validate_owner: Callable[[], None],
        expected: dict[str, list[int]] | None = None,
    ) -> None:
        self.root = root
        self.validate_owner = validate_owner
        validate_owner()
        self.fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            _check(os.fstat(self.fd), True)
        except BaseException:
            os.close(self.fd)
            raise
        self.root_identity = _identity(os.fstat(self.fd))
        self.identities = {} if expected is None else dict(expected)
        if expected is not None and self.identities.get(".") != self.root_identity:
            self.close()
            raise OwnedRuntimeRefusal("runtime_identity_changed")
        self.identities["."] = self.root_identity
        self.poisoned = False

    def check(self) -> None:
        if self.poisoned:
            raise OwnedRuntimeRefusal("durability_unknown_reopen_required")
        self.validate_owner()
        st = os.lstat(self.root)
        _check(st, True)
        if _identity(st) != self.root_identity:
            raise OwnedRuntimeRefusal("runtime_identity_changed")

    def directory(self, relative: str) -> int:
        self.check()
        fd = os.dup(self.fd)
        parts = []
        try:
            for part in Path(relative).parts:
                if part in (".", "..", "/"):
                    raise OwnedRuntimeRefusal("invalid_relative_path")
                parts.append(part)
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
                os.close(fd)
                fd = child
                st = os.fstat(fd)
                _check(st, True)
                key = "/".join(parts)
                current = _identity(st)
                if key in self.identities and self.identities[key] != current:
                    raise OwnedRuntimeRefusal("directory_identity_changed")
                self.identities[key] = current
            return fd
        except BaseException:
            os.close(fd)
            raise

    def read(self, relative: str, *, missing: bool = False) -> bytes | None:
        path = Path(relative)
        parent = self.directory(str(path.parent))
        try:
            try:
                fd = os.open(
                    path.name,
                    (os.O_RDWR if relative.endswith(".sqlite3") else os.O_RDONLY)
                    | os.O_NOFOLLOW,
                    dir_fd=parent,
                )
            except FileNotFoundError:
                if missing:
                    return None
                raise
            try:
                st = os.fstat(fd)
                _check(st)
                if relative in (
                    ".millrace/runtime.sqlite3",
                    ".millrace/runtime.sqlite3.runner-session-events.sqlite3",
                ):
                    try:
                        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 512, 0x40000000)
                    except OSError as exc:
                        raise OwnedRuntimeRefusal("active_sqlite_connection") from exc
                if st.st_size > 8 * 1024 * 1024:
                    raise OwnedRuntimeRefusal("owned_file_too_large")
                identity = _identity(st)
                if (
                    relative in self.identities
                    and self.identities[relative] != identity
                ):
                    raise OwnedRuntimeRefusal("file_identity_changed")
                self.identities[relative] = identity
                chunks = []
                while chunk := os.read(fd, 65536):
                    chunks.append(chunk)
                if (
                    _identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False))
                    != identity
                ):
                    raise OwnedRuntimeRefusal("file_changed_during_read")
                self.check()
                return b"".join(chunks)
            finally:
                os.close(fd)
        finally:
            os.close(parent)

    def write(
        self,
        relative: str,
        payload: bytes,
        *,
        expected: bytes | None = None,
        immutable: bool = False,
    ) -> None:
        self.check()
        path = Path(relative)
        parent = self.directory(str(path.parent))
        temporary = ".owned-" + uuid.uuid4().hex
        fd = None
        try:
            prior = self.read(relative, missing=True)
            if expected is not None and prior != expected:
                raise OwnedRuntimeRefusal("stale_database_snapshot")
            if immutable and prior is not None:
                if prior != payload:
                    raise CasDigestMismatch("existing owned CAS bytes differ")
                return
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            view = memoryview(payload)
            while view:
                view = view[os.write(fd, view) :]
            os.fsync(fd)
            # Reopen parents at the mutation consumer; replacement never redirects
            # this descriptor-relative rename into the replacement or symlink.
            current = self.directory(str(path.parent))
            try:
                if _identity(os.fstat(current)) != _identity(os.fstat(parent)):
                    raise OwnedRuntimeRefusal("directory_identity_changed")
                if self.read(relative, missing=True) != prior:
                    raise OwnedRuntimeRefusal("member_changed_before_replace")
                os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
                self.identities[relative] = _identity(os.fstat(fd))
                self.check()
            finally:
                os.close(current)
        except BaseException:
            self.poisoned = True
            raise
        finally:
            if fd is not None:
                os.close(fd)
            # Retain temporary evidence after uncertain writes; no automatic cleanup.
            os.close(parent)

    def profile(self) -> None:
        for relative in (".millrace", ".millrace/cas/sha256"):
            parent = self.directory(relative)
            try:
                names = os.listdir(parent)
                if any(
                    n.startswith((".owned-", ".runner-snapshot-"))
                    or n.startswith("runtime.sqlite3-")
                    or n.startswith("runtime.sqlite3.runner-session-events.sqlite3-")
                    for n in names
                ):
                    raise OwnedRuntimeRefusal("unsettled_database_profile")
            finally:
                os.close(parent)

        event_path = ".millrace/runtime.sqlite3.runner-session-events.sqlite3"
        event_database = self.read(event_path, missing=True)
        if event_database is not None:
            if len(event_database) < 100 or event_database[18:20] != b"\x01\x01":
                raise OwnedRuntimeRefusal("unsupported_event_journal_profile")
        self.read(event_path + ".public.json", missing=True)

    def close(self) -> None:
        os.close(self.fd)


class _CAS(ContentAddressedByteStore):
    def __init__(self, tree: _Tree) -> None:
        self.tree = tree
        super().__init__(tree.root / ".millrace/cas")

    def get_bytes(self, digest: str) -> bytes:
        self.tree.profile()
        payload = self.tree.read(
            ".millrace/cas/sha256/" + _digest_hex(digest), missing=True
        )
        if payload is None:
            raise CasObjectNotFound(digest)
        if storage_digest_for_bytes(payload) != digest:
            raise CasDigestMismatch(digest)
        return payload

    def put_bytes(self, payload: bytes) -> str:
        self.tree.profile()
        digest = storage_digest_for_bytes(payload)
        self.tree.write(
            ".millrace/cas/sha256/" + _digest_hex(digest), payload, immutable=True
        )
        return digest


class _Connection(sqlite3.Connection):
    """Core SQL operations return only after a completed transaction is durable."""

    tree: _Tree | None = None
    persisted: bytes | None = None
    database_relative: str = ".millrace/runtime.sqlite3"

    def _before(self) -> None:
        if self.tree is not None:
            self.tree.profile()
            if self.tree.read(self.database_relative, missing=True) != self.persisted:
                raise OwnedRuntimeRefusal("stale_database_snapshot")

    def _after(self) -> None:
        if self.tree is not None and not self.in_transaction:
            payload = self.serialize()
            if payload != self.persisted:
                self.tree.write(
                    self.database_relative, payload, expected=self.persisted
                )
                self.persisted = payload

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        self._before()
        result = super().execute(sql, parameters)
        self._after()
        return result

    def executemany(self, sql: str, parameters: Iterable[Any], /) -> sqlite3.Cursor:
        self._before()
        result = super().executemany(sql, parameters)
        self._after()
        return result

    def executescript(self, sql_script: str, /) -> sqlite3.Cursor:
        if self.tree is None:
            return super().executescript(sql_script)
        raise OwnedRuntimeRefusal("scripts_unsupported_in_owned_store")

    def cursor(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise OwnedRuntimeRefusal("raw_cursor_unsupported_in_owned_store")

    def commit(self) -> None:
        self._before()
        super().commit()
        self._after()

    def rollback(self) -> None:
        super().rollback()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False


def open_owned_runtime(
    workspace: Path,
    *,
    validate_owner: Callable[[], None],
    expected: dict[str, list[int]],
) -> OwnedRuntime:
    """Open a receipt-authenticated, exclusively owned rollback-profile runtime.

    expected is the inventory retained by the command owner at handled close.
    The callback must verify current receipt/foreground-lock authority on each
    consumer operation. Idle external connections are outside this private
    single-owner profile; this function does not claim to detect them.
    """
    workspace = Path(workspace)
    tree = _Tree(workspace, validate_owner, expected)
    connection = None
    try:
        tree.profile()
        database = tree.read(".millrace/runtime.sqlite3")
        if database is None or len(database) < 100 or database[18:20] != b"\x01\x01":
            raise OwnedRuntimeRefusal("unsupported_database_journal_profile")
        os.close(tree.directory(".millrace/cas/sha256"))  # pinned before consumption
        connection = sqlite3.connect(":memory:", factory=_Connection)
        connection.deserialize(database)
        configure_connection(connection)
        validate_metadata(read_metadata(connection))
        validate_schema_shape(connection)
        paths = (
            str(workspace),
            str(workspace / ".millrace/runtime.sqlite3"),
            str(workspace / ".millrace/cas"),
        )
        controls.identity(connection, paths)
        connection.persisted = database
        connection.tree = tree
        store = SQLiteRuntimeStore(connection, paths)
        return OwnedRuntime(
            CliWorkspacePaths(*map(Path, paths)), store, _CAS(tree), tree
        )
    except BaseException:
        if connection is not None:
            connection.close()
        tree.close()
        raise


class OwnedRuntime(OpenRuntimeContext):
    _owned_tree: _Tree

    def __init__(
        self,
        paths: CliWorkspacePaths,
        store: SQLiteRuntimeStore,
        cas_store: ContentAddressedByteStore,
        tree: _Tree,
    ) -> None:
        super().__init__(paths, store, cas_store)
        object.__setattr__(self, "_owned_tree", tree)

    def retained_identity(self) -> dict[str, list[int]]:
        self._owned_tree.check()
        return dict(self._owned_tree.identities)

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._owned_tree.close()

    def open_session_event_store(
        self, *, create: bool = True
    ) -> RunnerSessionEventStore:
        tree = self._owned_tree
        tree.profile()
        relative = ".millrace/runtime.sqlite3.runner-session-events.sqlite3"
        database = tree.read(relative, missing=True)
        if database is None and not create:
            raise FileNotFoundError(relative)
        connection = sqlite3.connect(":memory:", factory=_Connection)
        try:
            if database is not None:
                if len(database) < 100 or database[18:20] != b"\x01\x01":
                    raise OwnedRuntimeRefusal("unsupported_event_journal_profile")
                connection.deserialize(database)

            def publish(raw: bytes) -> None:
                tree.profile()
                tree.write(relative + ".public.json", raw)

            store = RunnerSessionEventStore.from_connection(
                connection, snapshot_writer=publish
            )
            payload = connection.serialize()
            if payload != database:
                tree.write(relative, payload, expected=database)
            connection.persisted = payload
            connection.database_relative = relative
            connection.tree = tree
            return store
        except BaseException:
            connection.close()
            raise

    def session_event_snapshot(self) -> RunnerSessionEventSnapshot:
        tree = self._owned_tree
        tree.profile()
        raw = tree.read(
            ".millrace/runtime.sqlite3.runner-session-events.sqlite3.public.json"
        )
        if raw is None:
            raise OwnedRuntimeRefusal("event_snapshot_missing")
        return RunnerSessionEventSnapshot.from_bytes(raw)

    def session_event_store_exists(self) -> bool:
        self._owned_tree.profile()
        return (
            self._owned_tree.read(
                ".millrace/runtime.sqlite3.runner-session-events.sqlite3", missing=True
            )
            is not None
        )

    def session_event_header(self) -> bytes:
        self._owned_tree.profile()
        raw = self._owned_tree.read(
            ".millrace/runtime.sqlite3.runner-session-events.sqlite3"
        )
        if raw is None:
            raise FileNotFoundError("owned event store missing")
        return raw[:16]


def initialize_owned_runtime(
    workspace: Path, *, validate_owner: Callable[[], None]
) -> OwnedRuntime:
    """Create the private rollback image through the same owned write consumer."""
    from millrace.substrate._sqlite_schema import initialize_schema

    tree = _Tree(workspace, validate_owner)
    connection = sqlite3.connect(":memory:", factory=_Connection)
    try:
        paths = (
            str(workspace),
            str(workspace / ".millrace/runtime.sqlite3"),
            str(workspace / ".millrace/cas"),
        )
        configure_connection(connection)
        initialize_schema(connection, paths)
        validate_schema_shape(connection)
        payload = connection.serialize()
        if tree.read(".millrace/runtime.sqlite3", missing=True) is not None:
            raise OwnedRuntimeRefusal("already_initialized")
        tree.write(".millrace/runtime.sqlite3", payload, immutable=True)
        connection.persisted = payload
        connection.tree = tree
        return OwnedRuntime(
            CliWorkspacePaths(*map(Path, paths)),
            SQLiteRuntimeStore(connection, paths),
            _CAS(tree),
            tree,
        )
    except BaseException:
        connection.close()
        tree.close()
        raise
