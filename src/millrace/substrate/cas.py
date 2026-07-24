"""Filesystem-backed content-addressed byte storage."""

from __future__ import annotations

import os
import tempfile
from hashlib import sha256
from pathlib import Path

from millrace.substrate.errors import (
    CasDigestMismatch,
    CasObjectNotFound,
    InvalidCasDigest,
)

DIGEST_PREFIX = "sha256:"
_SHA256_HEX_LENGTH = 64


def storage_digest_for_bytes(payload: bytes) -> str:
    return f"{DIGEST_PREFIX}{sha256(payload).hexdigest()}"


class ContentAddressedByteStore:
    """Stores immutable bytes under their storage digest."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def put_bytes(self, payload: bytes) -> str:
        digest = storage_digest_for_bytes(payload)
        object_path = self._object_path(digest)
        if object_path.exists():
            self.get_bytes(digest)
            return digest

        object_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{object_path.name}.",
            suffix=".tmp",
            dir=object_path.parent,
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as temp_file:
                temp_file.write(payload)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            try:
                os.link(temp_path, object_path)
            except FileExistsError:
                self.get_bytes(digest)
            return digest
        finally:
            temp_path.unlink(missing_ok=True)

    def get_bytes(self, digest: str) -> bytes:
        object_path = self._object_path(digest)
        if not object_path.exists():
            raise CasObjectNotFound(f"CAS object not found: {digest}")
        payload = object_path.read_bytes()
        actual_digest = storage_digest_for_bytes(payload)
        if actual_digest != digest:
            raise CasDigestMismatch(
                f"CAS object digest mismatch: expected {digest}, got {actual_digest}"
            )
        return payload

    def _object_path(self, digest: str) -> Path:
        digest_hex = _digest_hex(digest)
        return self._root / "sha256" / digest_hex


def _digest_hex(digest: str) -> str:
    if not digest.startswith(DIGEST_PREFIX):
        raise InvalidCasDigest(f"unsupported CAS digest: {digest}")
    digest_hex = digest.removeprefix(DIGEST_PREFIX)
    if len(digest_hex) != _SHA256_HEX_LENGTH or any(
        character not in "0123456789abcdef" for character in digest_hex
    ):
        raise InvalidCasDigest(f"unsupported CAS digest: {digest}")
    return digest_hex


__all__ = (
    "ContentAddressedByteStore",
    "DIGEST_PREFIX",
    "storage_digest_for_bytes",
)
