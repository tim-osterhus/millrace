"""Canonical instance locators, atomic publication and persistent project holds."""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .records import TERMINAL, RouterError, mapping, resolved, strict_json


def canonical_path(path: Path) -> Path:
    """Use the filesystem's spelling on Darwin, including case/firmlink aliases."""
    path = resolved(path)
    if sys.platform != "darwin":
        return path
    ancestor, suffix = path, []
    while True:
        try:
            descriptor = os.open(ancestor, os.O_RDONLY | os.O_NONBLOCK)
            break
        except FileNotFoundError:
            suffix.append(ancestor.name)
            ancestor = ancestor.parent
        except OSError as exc:
            raise RouterError("invalid_path", str(path)) from exc
    try:
        raw = fcntl.fcntl(descriptor, 50, bytes(1024))  # Darwin F_GETPATH
        return Path(os.fsdecode(raw.split(b"\0", 1)[0])).joinpath(*reversed(suffix))
    except OSError as exc:
        raise RouterError("invalid_path", str(path)) from exc
    finally:
        os.close(descriptor)


def new_id() -> str:
    return uuid.uuid4().hex


def check_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise RouterError("invalid_id")
    return value


@dataclass(frozen=True)
class Config:
    instance_id: str
    project_root: Path
    workflow_file: Path
    state_dir: Path
    path: tuple[str, ...]
    env_allowlist: tuple[str, ...]
    host_paths: dict[str, str]
    instance_version: int = 1
    runtime_defaults: dict[str, object] = field(default_factory=dict)
    operator_policy: dict[str, object] = field(default_factory=dict)

    @property
    def store_path(self) -> Path:
        return self.state_dir / "state.sqlite3"

    def identity(self) -> dict[str, object]:
        return {
            "format": "millrace-router-instance/v2",
            "instance_id": self.instance_id,
            "project_root": str(self.project_root),
            "workflow_file": str(self.workflow_file),
        }

    def settings(self) -> dict[str, object]:
        return {
            "state_version": self.instance_version,
            "path": list(self.path),
            "env_allowlist": list(self.env_allowlist),
            "host_paths": self.host_paths,
            "runtime_defaults": self.runtime_defaults,
            "operator_policy": self.operator_policy,
        }

    def data(self) -> dict[str, object]:
        return {
            "format": "millrace-router-instance/v2",
            "instance_id": self.instance_id,
            "project_root": str(self.project_root),
            "workflow_file": str(self.workflow_file),
            "path": list(self.path),
            "env_allowlist": list(self.env_allowlist),
            "host_paths": self.host_paths,
            "state_version": self.instance_version,
            "runtime_defaults": self.runtime_defaults,
            "operator_policy": self.operator_policy,
        }


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".tmp-" + new_id())
    try:
        with temporary.open("x", encoding="utf-8") as file:
            json.dump(value, file, sort_keys=True)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def file_lock(path: Path, *, blocking: bool = False) -> Iterator[None]:
    with path.open("a+b") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise RouterError("controller_busy") from exc
        try:
            yield
        finally:
            fcntl.flock(file, fcntl.LOCK_UN)


def hold_record(config: Config, run: dict[str, object]) -> dict[str, object]:
    return {
        "hold_id": run["hold_id"],
        "instance_id": config.instance_id,
        "run_id": run["id"],
        "store_path": str(config.store_path),
    }


def _read_owner(path: Path, *, missing_ok: bool = False) -> dict[str, object] | None:
    """Read tiny coordination metadata without waiting on a nonregular target."""
    try:
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except FileNotFoundError:
            # A dangling symlink is present, invalid metadata, not a missing hold.
            try:
                path.lstat()
            except FileNotFoundError:
                if missing_ok:
                    return None
            raise
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise RouterError("owner_store_unverifiable")
            raw = bytearray()
            while len(raw) <= 65536:
                chunk = os.read(descriptor, 65537 - len(raw))
                if not chunk:
                    break
                raw.extend(chunk)
            if len(raw) > 65536:
                raise RouterError("owner_store_unverifiable")
            return mapping(strict_json(bytes(raw)))
        finally:
            os.close(descriptor)
    except (OSError, RouterError) as exc:
        raise RouterError("owner_store_unverifiable") from exc


def claim_hold(
    config: Config, run: dict[str, object], *, allow_missing: bool = False
) -> None:
    folder = config.project_root / ".millrace-router-owner"
    try:
        folder.mkdir(exist_ok=True)
        with file_lock(folder / "lock", blocking=True):
            path = folder / "owner.json"
            expected = hold_record(config, run)
            actual = _read_owner(path, missing_ok=True)
            if actual is not None:
                if actual != expected:
                    raise RouterError(
                        "project_owned", "An unresolved project hold blocks this writer"
                    )
            else:
                if not allow_missing:
                    raise RouterError(
                        "owner_store_unverifiable", "A running run lost its hold"
                    )
                atomic_json(path, expected)
    except OSError as exc:
        raise RouterError("owner_store_unverifiable") from exc


def release_hold(config: Config, run: dict[str, object]) -> None:
    if run["status"] not in TERMINAL:
        raise RouterError("owner_run_nonterminal")
    folder = config.project_root / ".millrace-router-owner"
    try:
        if not folder.exists():
            return
        with file_lock(folder / "lock", blocking=True):
            path = folder / "owner.json"
            actual = _read_owner(path, missing_ok=True)
            if actual is None:
                return
            if actual != hold_record(config, run):
                raise RouterError("project_owned")
            path.unlink()
            descriptor = os.open(folder, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except OSError as exc:
        raise RouterError("owner_store_unverifiable") from exc
