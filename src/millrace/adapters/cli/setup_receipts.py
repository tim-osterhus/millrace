"""Private local-operator setup journal; no database schema or signing authority."""

from __future__ import annotations

import fcntl
import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from millrace.contracts.setup import SetupRefusal, canonical

MAX_JOURNAL_BYTES = 8 * 1024 * 1024


def secure_directory(path: Path, *, create: bool = False) -> int:
    """Walk absolute directory components without following symlinks."""
    if not path.is_absolute() or any(p in (".", "..") for p in path.parts):
        raise SetupRefusal("setup_unsafe_directory")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for name in path.parts[1:]:
            try:
                child = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=fd,
                )
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(name, 0o700, dir_fd=fd)
                os.fsync(fd)
                child = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=fd,
                )
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if info.st_uid not in (0, os.getuid()) or info.st_mode & 0o022:
                raise SetupRefusal("setup_unsafe_directory_permissions")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _private_file(fd: int) -> None:
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise SetupRefusal("setup_journal_file_unsafe")


class LockedJournal:
    def __init__(
        self, directory_fd: int, state: dict[str, Any], writable: bool
    ) -> None:
        self.directory_fd = directory_fd
        self.state = state
        self.writable = writable

    def commit(self) -> None:
        if not self.writable:
            raise SetupRefusal("setup_journal_readonly")
        data = canonical(self.state)
        if len(data) > MAX_JOURNAL_BYTES:
            raise SetupRefusal("setup_journal_full")
        name = ".state-" + uuid4().hex
        fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=self.directory_fd,
        )
        try:
            offset = 0
            while offset < len(data):
                offset += os.write(fd, data[offset:])
            os.fsync(fd)
            os.replace(
                name,
                "state.json",
                src_dir_fd=self.directory_fd,
                dst_dir_fd=self.directory_fd,
            )
            os.fsync(self.directory_fd)
        finally:
            os.close(fd)
            try:
                os.unlink(name, dir_fd=self.directory_fd)
            except FileNotFoundError:
                pass


class SetupJournal:
    """Permission authority for one operator; same-UID code is not sandboxed."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (
            root if root is not None else Path.home() / ".millrace" / "setup-journal"
        )

    @contextmanager
    def locked(
        self, *, create: bool = False, write: bool = False
    ) -> Iterator[LockedJournal | None]:
        directory_fd = lock_fd = None
        try:
            try:
                directory_fd = secure_directory(self.root, create=create)
            except FileNotFoundError:
                yield None
                return
            info = os.fstat(directory_fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise SetupRefusal("setup_journal_directory_unsafe")
            try:
                lock_fd = os.open(
                    "lock",
                    (os.O_RDWR if create or write else os.O_RDONLY)
                    | os.O_NOFOLLOW
                    | os.O_CLOEXEC,
                    dir_fd=directory_fd,
                )
            except FileNotFoundError:
                if not create:
                    raise SetupRefusal("setup_journal_incomplete") from None
                lock_fd = os.open(
                    "lock",
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=directory_fd,
                )
                os.fsync(directory_fd)
            _private_file(lock_fd)
            fcntl.flock(lock_fd, fcntl.LOCK_EX if create or write else fcntl.LOCK_SH)
            state = self._read(directory_fd)
            yield LockedJournal(directory_fd, state, create or write)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            if isinstance(exc, SetupRefusal):
                raise
            raise SetupRefusal("setup_journal_unavailable") from exc
        finally:
            if lock_fd is not None:
                os.close(lock_fd)
            if directory_fd is not None:
                os.close(directory_fd)

    @staticmethod
    def _read(directory_fd: int) -> dict[str, Any]:
        try:
            fd = os.open(
                "state.json",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            return {
                "version": 1,
                "uid": os.getuid(),
                "consents": {},
                "actions": {},
                "consent_keys": {},
                "action_keys": {},
                "results": {},
            }
        try:
            _private_file(fd)
            if os.fstat(fd).st_size > MAX_JOURNAL_BYTES:
                raise SetupRefusal("setup_journal_full")
            data = bytearray()
            while chunk := os.read(fd, 65536):
                data.extend(chunk)
                if len(data) > MAX_JOURNAL_BYTES:
                    raise SetupRefusal("setup_journal_full")
            state = json.loads(data)
            if type(state) is not dict or set(state) != {
                "version",
                "uid",
                "consents",
                "actions",
                "consent_keys",
                "action_keys",
                "results",
            }:
                raise SetupRefusal("setup_journal_corrupt")
            if (
                type(state["version"]) is not int
                or state["version"] != 1
                or type(state["uid"]) is not int
                or state["uid"] != os.getuid()
            ):
                raise SetupRefusal("setup_journal_owner_mismatch")
            for name in (
                "consents",
                "actions",
                "consent_keys",
                "action_keys",
                "results",
            ):
                if type(state[name]) is not dict or len(state[name]) > 256:
                    raise SetupRefusal("setup_journal_corrupt")
            canonical(state)
            return state
        finally:
            os.close(fd)
