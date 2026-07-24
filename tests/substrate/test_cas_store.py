from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path

import pytest


def _stored_files(root: Path) -> tuple[Path, ...]:
    return tuple(path for path in root.rglob("*") if path.is_file())


def _single_stored_file(root: Path) -> Path:
    files = _stored_files(root)
    assert len(files) == 1
    return files[0]


def test_put_bytes_returns_stable_digest_for_same_bytes(tmp_path: Path) -> None:
    from millrace.substrate.cas import ContentAddressedByteStore

    store = ContentAddressedByteStore(tmp_path)
    payload = b"kernel ping compiled object bytes"

    digest = store.put_bytes(payload)

    assert digest == f"sha256:{sha256(payload).hexdigest()}"
    assert store.put_bytes(payload) == digest
    assert store.get_bytes(digest) == payload
    assert len(_stored_files(tmp_path)) == 1


def test_get_bytes_refuses_missing_digest(tmp_path: Path) -> None:
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.errors import CasObjectNotFound

    missing_digest = f"sha256:{'0' * 64}"

    with pytest.raises(CasObjectNotFound, match=missing_digest):
        ContentAddressedByteStore(tmp_path).get_bytes(missing_digest)


def test_get_bytes_refuses_digest_content_mismatch(tmp_path: Path) -> None:
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.errors import CasDigestMismatch

    store = ContentAddressedByteStore(tmp_path)
    digest = store.put_bytes(b"canonical bytes")
    _single_stored_file(tmp_path).write_bytes(b"tampered bytes")

    with pytest.raises(CasDigestMismatch, match=digest):
        store.get_bytes(digest)


def test_cas_write_is_atomic_for_existing_digest(tmp_path: Path) -> None:
    from millrace.substrate.cas import ContentAddressedByteStore

    store = ContentAddressedByteStore(tmp_path)
    payload = b"existing immutable object"
    digest = store.put_bytes(payload)
    object_path = _single_stored_file(tmp_path)
    sentinel_mtime_ns = 1_700_000_000_000_000_000
    os.utime(object_path, ns=(sentinel_mtime_ns, sentinel_mtime_ns))

    assert store.put_bytes(payload) == digest
    assert object_path.stat().st_mtime_ns == sentinel_mtime_ns
    assert object_path.read_bytes() == payload


def test_substrate_readme_does_not_overclaim_cas_power_loss_durability() -> None:
    readme = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "millrace"
        / "substrate"
        / "README.md"
    ).read_text(encoding="utf-8")

    assert "process-crash-safe" in readme
    assert "power-loss durable" not in readme
