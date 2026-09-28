"""Explicit, consent-owned acquisition of complete installed wheel evidence.

Hashes correlate local operator-selected bytes; they do not authenticate a
publisher. This module never installs packages or follows installer URL hints.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from email.parser import BytesParser
from importlib import metadata
from pathlib import Path, PurePosixPath
from time import monotonic
from typing import Any

from millrace.adapters.cli.setup_receipts import secure_directory
from millrace.contracts.demo import VERSION
from millrace.contracts.setup import SetupRefusal

COMPONENTS = ("millrace-ai", "millrace-plus")
MAX_WHEEL_BYTES = 16 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 4096


def wheel_filename(name: str) -> str:
    if name not in COMPONENTS:
        raise SetupRefusal("unsupported_install_component")
    return name.replace("-", "_") + "-" + VERSION + "-py3-none-any.whl"


def evidence_directory() -> Path:
    prefix = Path(sys.prefix)
    if sys.prefix == sys.base_prefix or prefix.is_symlink():
        raise SetupRefusal("setup_evidence_requires_virtual_environment")
    descriptor = secure_directory(prefix)
    os.close(descriptor)
    return prefix / "share/millrace/install-artifacts"


def _read_file(path: Path, *, allow_empty: bool = False) -> bytes:
    directory = secure_directory(path.parent)
    try:
        fd = os.open(
            path.name,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=directory,
        )
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_uid != os.getuid()
                or before.st_mode & 0o022
                or not (0 if allow_empty else 1) <= before.st_size <= MAX_WHEEL_BYTES
            ):
                raise SetupRefusal("unsafe_install_artifact")
            with os.fdopen(os.dup(fd), "rb") as stream:
                payload = stream.read(MAX_WHEEL_BYTES + 1)
            after = os.fstat(fd)
            if (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ) != (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) or len(payload) != before.st_size:
                raise SetupRefusal("install_artifact_changed")
            return payload
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def inspect_evidence_source(source: str) -> dict[str, Any]:
    """Bind local observations without creating files or accessing a network."""
    try:
        destination = evidence_directory()
        if source != "pypi":
            selected = Path(source)
            if not selected.is_absolute() or str(selected) != source:
                raise SetupRefusal("setup_wheelhouse_requires_absolute_path")
            descriptor = secure_directory(selected)
            os.close(descriptor)
        rows = []
        for name in COMPONENTS:
            dist = metadata.distribution(name)
            if dist.version != VERSION:
                raise SetupRefusal("incompatible_component_version")
            root = Path(str(dist.locate_file(""))).resolve()
            if not root.is_relative_to(Path(sys.prefix).resolve()):
                raise SetupRefusal("component_outside_environment")
            row: dict[str, Any] = {
                "distribution": name,
                "version": dist.version,
                "filename": wheel_filename(name),
                "installed_root": str(root),
                "sha256": None,
            }
            if source != "pypi":
                payload = _read_file(Path(source) / wheel_filename(name))
                row["sha256"] = "sha256:" + hashlib.sha256(payload).hexdigest()
            rows.append(row)
        return {"source": source, "destination": str(destination), "wheels": rows}
    except (OSError, metadata.PackageNotFoundError) as exc:
        raise SetupRefusal("setup_install_evidence_source_unavailable") from exc


def validate_download_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc not in {"pypi.org", "files.pythonhosted.org"}
        or parsed.fragment
        or parsed.query
        or "\\" in url
    ):
        raise SetupRefusal("setup_install_evidence_origin_refused")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        raise SetupRefusal("setup_install_evidence_redirect_refused")


def _fetch(url: str, maximum: int) -> bytes:
    validate_download_url(url)
    # Empty ProxyHandler deliberately ignores ambient proxy configuration.
    # No cookie, password, netrc, package-manager config or auth handler exists.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    deadline = monotonic() + 30
    try:
        with opener.open(url, timeout=15) as response:
            if response.geturl() != url:
                raise SetupRefusal("setup_install_evidence_redirect_refused")
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isdigit() or int(length) > maximum):
                raise SetupRefusal("setup_install_evidence_transfer_bound")
            chunks = bytearray()
            while True:
                if monotonic() >= deadline:
                    raise SetupRefusal("setup_install_evidence_transfer_deadline")
                # read1 makes one bounded socket read, so slow-drip transfers
                # cannot restart a full response.read timeout indefinitely.
                chunk = response.read1(min(65536, maximum + 1 - len(chunks)))
                if not chunk:
                    return bytes(chunks)
                chunks.extend(chunk)
                if len(chunks) > maximum:
                    raise SetupRefusal("setup_install_evidence_transfer_bound")
    except (urllib.error.URLError, OSError) as exc:
        raise SetupRefusal("setup_install_evidence_download_failed") from exc


def _download_wheel(name: str) -> bytes:
    url = (
        "https://pypi.org/pypi/"
        + name
        + "/"
        + urllib.parse.quote(VERSION, safe="")
        + "/json"
    )
    try:
        info = json.loads(_fetch(url, 1024 * 1024))
        candidates = [
            row for row in info["urls"] if row["filename"] == wheel_filename(name)
        ]
        if len(candidates) != 1:
            raise SetupRefusal("setup_exact_wheel_unavailable_use_wheelhouse")
        selected = candidates[0]
        parsed = urllib.parse.urlsplit(selected["url"])
        if parsed.netloc != "files.pythonhosted.org":
            raise SetupRefusal("setup_install_evidence_origin_refused")
        payload = _fetch(selected["url"], MAX_WHEEL_BYTES)
        if (
            selected["size"] != len(payload)
            or selected["digests"]["sha256"] != hashlib.sha256(payload).hexdigest()
        ):
            raise SetupRefusal("setup_download_digest_mismatch")
        return payload
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, SetupRefusal):
            raise
        raise SetupRefusal("setup_install_evidence_metadata_invalid") from exc


def _record_digest(algorithm: str, payload: bytes) -> str:
    if algorithm == "sha256":
        digest = hashlib.sha256(payload).digest()
    elif algorithm == "sha384":
        digest = hashlib.sha384(payload).digest()
    elif algorithm == "sha512":
        digest = hashlib.sha512(payload).digest()
    else:
        raise SetupRefusal("invalid_install_artifact")
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _validate_archive_record(
    record_payload: bytes,
    record_name: str,
    members: dict[str, bytes],
) -> None:
    try:
        rows = csv.reader(io.StringIO(record_payload.decode("utf-8")), strict=True)
        covered: set[str] = set()
        self_rows = 0
        for row in rows:
            if len(row) != 3:
                raise SetupRefusal("invalid_install_artifact")
            path, digest, size = row
            if path == record_name:
                if digest or size or self_rows:
                    raise SetupRefusal("invalid_install_artifact")
                self_rows += 1
                continue
            parsed = PurePosixPath(path)
            if (
                not path
                or parsed.is_absolute()
                or not parsed.parts
                or ".." in parsed.parts
                or str(parsed) != path
                or "\\" in path
                or path not in members
                or path in covered
            ):
                raise SetupRefusal("invalid_install_artifact")
            algorithm, separator, encoded = digest.partition("=")
            if not separator or not encoded:
                raise SetupRefusal("invalid_install_artifact")
            if digest != f"{algorithm}={_record_digest(algorithm, members[path])}":
                raise SetupRefusal("invalid_install_artifact")
            if not size.isascii() or not size.isdigit():
                raise SetupRefusal("invalid_install_artifact")
            if int(size) != len(members[path]) or str(int(size)) != size:
                raise SetupRefusal("invalid_install_artifact")
            covered.add(path)
        if self_rows != 1 or covered != set(members):
            raise SetupRefusal("invalid_install_artifact")
    except (csv.Error, UnicodeDecodeError, ValueError) as exc:
        raise SetupRefusal("invalid_install_artifact") from exc


def verify_wheel(name: str, payload: bytes) -> str:
    """Verify complete supported wheel membership against the active installation."""
    if not 0 < len(payload) <= MAX_WHEEL_BYTES:
        raise SetupRefusal("unsafe_install_artifact")
    dist = metadata.distribution(name)
    if dist.version != VERSION:
        raise SetupRefusal("incompatible_component_version")
    root = Path(str(dist.locate_file(""))).resolve()
    if not root.is_relative_to(Path(sys.prefix).resolve()):
        raise SetupRefusal("component_outside_environment")
    package = "millrace" if name == "millrace-ai" else "millrace_workflow_package"
    packages = {package} if name == "millrace-ai" else {package, "millrace_plus"}
    info = name.replace("-", "_") + "-" + VERSION + ".dist-info"
    omitted = {"RECORD", "INSTALLER", "REQUESTED", "direct_url.json"}
    record_name = info + "/RECORD"
    installed = {
        str(item)
        for item in (dist.files or ())
        if PurePosixPath(str(item)).parts[0] in packages | {info}
        and "__pycache__" not in PurePosixPath(str(item)).parts
        and not (
            PurePosixPath(str(item)).parent == PurePosixPath(info)
            and PurePosixPath(str(item)).name in omitted
        )
    }
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            if (
                len(entries) > MAX_MEMBERS
                or len({entry.filename for entry in entries}) != len(entries)
                or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES
            ):
                raise SetupRefusal("unsafe_wheel_members")
            actual = set()
            members: dict[str, bytes] = {}
            record_entries = [
                entry
                for entry in entries
                if entry.filename == record_name and not entry.is_dir()
            ]
            if len(record_entries) != 1:
                raise SetupRefusal("invalid_install_artifact")
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or not path.parts
                    or str(path) != entry.filename.rstrip("/")
                    or "\\" in entry.filename
                    or stat.S_IFMT(entry.external_attr >> 16)
                    not in {0, stat.S_IFREG, stat.S_IFDIR}
                    or entry.file_size > MAX_WHEEL_BYTES
                    or entry.flag_bits & 1
                ):
                    raise SetupRefusal("unsafe_wheel_member")
                if entry.is_dir():
                    if archive.read(entry):
                        raise SetupRefusal("invalid_install_artifact")
                    continue
                if path.parts[0] not in packages | {info}:
                    raise SetupRefusal("unsupported_wheel_layout")
                if entry.filename == record_name:
                    continue
                actual.add(entry.filename)
                member = archive.read(entry)
                members[entry.filename] = member
                target = root.joinpath(*path.parts)
                for part in [target, *target.parents]:
                    if part == root:
                        break
                    if part.is_symlink():
                        raise SetupRefusal("unsafe_installed_member")
                if _read_file(target, allow_empty=True) != member:
                    raise SetupRefusal("installed_artifact_mismatch")
            _validate_archive_record(
                archive.read(record_entries[0]), record_name, members
            )
            if actual != installed or not any(
                n.startswith(package + "/") for n in actual
            ):
                raise SetupRefusal("installed_artifact_membership_mismatch")
            header = BytesParser().parsebytes(archive.read(info + "/METADATA"))
            if header.get("Name") != name or header.get("Version") != VERSION:
                raise SetupRefusal("installed_artifact_metadata_mismatch")
    except (OSError, KeyError, zipfile.BadZipFile, RuntimeError) as exc:
        raise SetupRefusal("invalid_install_artifact") from exc
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def acquire_evidence(source: str, *, guard: Callable[[], object] | None = None) -> None:
    """Acquire only after owner consent; callers journal uncertain effects."""
    plan = inspect_evidence_source(source)
    payloads = {}
    try:
        for name in COMPONENTS:
            payload = (
                _download_wheel(name)
                if source == "pypi"
                else _read_file(Path(source) / wheel_filename(name))
            )
            verify_wheel(name, payload)
            payloads[name] = payload
        if inspect_evidence_source(source) != plan:
            raise SetupRefusal("setup_install_evidence_source_changed")
        if guard is not None:
            guard()
        for name, payload in payloads.items():
            verify_wheel(name, payload)
        destination = Path(plan["destination"])
        # Validate every existing destination before creating any new evidence.
        for name, payload in payloads.items():
            target = destination / wheel_filename(name)
            if target.exists() or target.is_symlink():
                if _read_file(target) != payload:
                    raise SetupRefusal("setup_install_evidence_conflict")
        directory = secure_directory(destination, create=True)
        try:
            for name, payload in payloads.items():
                try:
                    fd = os.open(
                        wheel_filename(name),
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | os.O_NOFOLLOW
                        | os.O_CLOEXEC,
                        0o600,
                        dir_fd=directory,
                    )
                except FileExistsError:
                    if _read_file(destination / wheel_filename(name)) != payload:
                        raise SetupRefusal("setup_install_evidence_conflict")
                    continue
                try:
                    offset = 0
                    while offset < len(payload):
                        offset += os.write(fd, payload[offset:])
                    os.fsync(fd)
                finally:
                    os.close(fd)
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise SetupRefusal("setup_install_evidence_filesystem_refused") from exc


def retained_artifact_identity(name: str) -> str:
    """Read and revalidate the exact retained wheel; never acquire implicitly."""
    try:
        return verify_wheel(
            name, _read_file(evidence_directory() / wheel_filename(name))
        )
    except FileNotFoundError as exc:
        raise SetupRefusal("install_evidence_missing_run_setup_interactive") from exc
