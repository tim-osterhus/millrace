"""Owner-only bounded Unix transport for the three daemon lifecycle methods."""

from __future__ import annotations

import json
import os
import socket
import stat
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from millrace.adapters.cli.daemon_process import same_uid_peer
from millrace.contracts.controls import canonical_json, revision_field, uuid_field

MAX_WIRE_BYTES = 120 * 1024


def endpoint_path(workspace: Path) -> Path:
    return workspace / ".millrace" / "d" / "s"


def validate_path(path: Path, *, socket_exists: bool = True) -> None:
    if not path.is_absolute() or len(os.fsencode(path)) >= 104:
        raise ValueError("daemon_endpoint_path_unsupported")
    for parent in (*reversed(path.parents), path):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            if parent == path and not socket_exists:
                continue
            raise ValueError("daemon_endpoint_missing") from None
        if (
            stat.S_ISLNK(info.st_mode)
            or info.st_uid not in {0, os.geteuid()}
            or (parent != path and stat.S_IMODE(info.st_mode) & 0o022)
        ):
            raise ValueError("daemon_endpoint_unsafe")
        if parent == path.parent and (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise ValueError("daemon_directory_unsafe")
        if parent == path and (
            not stat.S_ISSOCK(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ValueError("daemon_socket_unsafe")


def _receive(
    connection: socket.socket, deadline: float, maximum: int
) -> dict[str, Any]:
    data = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("daemon_observation_timeout")
        connection.settimeout(remaining)
        chunk = connection.recv(min(4096, maximum + 1 - len(data)))
        if not chunk:
            raise ValueError("daemon_incomplete_message")
        data.extend(chunk)
        if len(data) > maximum:
            raise ValueError("daemon_message_too_large")
        if b"\n" in chunk:
            if not data.endswith(b"\n") or data.count(b"\n") != 1:
                raise ValueError("daemon_invalid_framing")
            break

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("daemon_duplicate_field")
            value[key] = item
        return value

    value = json.loads(data, object_pairs_hook=unique)
    if type(value) is not dict:
        raise ValueError("daemon_invalid_message")
    return value


def _send(connection: socket.socket, value: dict[str, Any], deadline: float) -> None:
    raw = json.dumps(value, separators=(",", ":"), allow_nan=False).encode() + b"\n"
    if len(raw) > MAX_WIRE_BYTES:
        raise ValueError("daemon_projection_too_large")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("daemon_observation_timeout")
    connection.settimeout(remaining)
    connection.sendall(raw)


class DaemonListener:
    def __init__(
        self, path: Path, handler: Callable[[dict[str, Any], float], dict[str, Any]]
    ) -> None:
        self.path = path
        self.handler = handler
        self.closed = threading.Event()
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.thread: threading.Thread | None = None
        self.identity: tuple[int, int] | None = None
        self.native_thread: threading.Thread | None = None
        self.native_lane = threading.Lock()

    def start(self) -> None:
        # Never adopt/delete an existing endpoint, including an old socket.
        if not self.path.is_absolute() or len(os.fsencode(self.path)) >= 104:
            raise ValueError("daemon_endpoint_path_unsupported")
        for parent in self.path.parent.parents:
            info = parent.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or info.st_uid not in {0, os.geteuid()}
                or stat.S_IMODE(info.st_mode) & 0o022
            ):
                raise ValueError("daemon_endpoint_unsafe")
        self.path.parent.mkdir(mode=0o700, exist_ok=True)
        validate_path(self.path, socket_exists=False)
        if self.path.exists():
            raise ValueError("daemon_endpoint_exists")
        try:
            self.socket.bind(str(self.path))
            os.chmod(self.path, 0o600, follow_symlinks=False)
            validate_path(self.path)
            info = self.path.lstat()
            self.identity = (info.st_dev, info.st_ino)
            self.socket.listen(8)
            self.socket.settimeout(0.1)
            self.thread = threading.Thread(
                target=self._serve, name="core-daemon-control", daemon=True
            )
            self.thread.start()
        except BaseException:
            self.close()
            raise

    def _serve(self) -> None:
        while not self.closed.is_set():
            try:
                connection, _ = self.socket.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with connection:
                deadline = time.monotonic() + 1.5
                try:
                    validate_path(self.path)
                    info = self.path.lstat()
                    if self.identity != (info.st_dev, info.st_ino):
                        continue
                    if not same_uid_peer(connection):
                        continue
                    message = _receive(
                        connection, min(deadline, time.monotonic() + 0.5), 16384
                    )
                    canonical_json(message)
                    method = message.get("method")
                    required = {"method", "challenge"} | (
                        {"request"} if method in {"stop", "native_control"} else set()
                    )
                    if (
                        method not in {"challenge", "status", "stop", "native_control"}
                        or set(message) - {"after_session", "expected_source_revision"}
                        != required
                    ):
                        raise ValueError("daemon_method_refused")
                    uuid_field(message["challenge"])
                    revision_field(message.get("after_session", 0))
                    if message.get("expected_source_revision") is not None:
                        revision_field(message["expected_source_revision"])
                    if method == "native_control":
                        if not self.native_lane.acquire(blocking=False):
                            _send(
                                connection,
                                {
                                    "challenge": message["challenge"],
                                    "status": "native_control_busy",
                                },
                                deadline,
                            )
                            continue
                        owned = connection.dup()
                        self.native_thread = threading.Thread(
                            target=self._native_request,
                            args=(owned, message, deadline),
                            name="core-native-admission",
                            daemon=True,
                        )
                        try:
                            self.native_thread.start()
                        except BaseException:
                            owned.close()
                            self.native_lane.release()
                            raise
                        continue
                    result = self.handler(message, deadline)
                    _send(connection, result, deadline)
                except (OSError, ValueError, RuntimeError):
                    # Lost response never rolls back already committed stop authority.
                    continue

    def _native_request(
        self, connection: socket.socket, message: dict[str, Any], deadline: float
    ) -> None:
        try:
            with connection:
                result = self.handler(message, deadline)
                _send(connection, result, deadline)
        except (OSError, ValueError, RuntimeError):
            pass
        finally:
            self.native_lane.release()

    def close(self) -> bool:
        self.closed.set()
        self.socket.close()
        if self.native_thread is not None:
            self.native_thread.join(timeout=0.1)
            if self.native_thread.is_alive():
                return False
        if self.thread is not None:
            self.thread.join(timeout=2)
            if self.thread.is_alive():
                return False
        try:
            info = self.path.lstat()
            if self.identity != (info.st_dev, info.st_ino) or not stat.S_ISSOCK(
                info.st_mode
            ):
                return self.identity is None
            self.path.unlink()
        except FileNotFoundError:
            return self.identity is None
        return True


def exchange(path: Path, message: dict[str, Any], *, deadline: float) -> dict[str, Any]:
    validate_path(path)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(max(0.001, deadline - time.monotonic()))
        connection.connect(str(path))
        validate_path(path)
        if not same_uid_peer(connection):
            raise ValueError("daemon_peer_refused")
        _send(connection, message, deadline)
        result = _receive(connection, deadline, MAX_WIRE_BYTES)
        if result.get("challenge") != message["challenge"]:
            raise ValueError("daemon_challenge_mismatch")
        return result
