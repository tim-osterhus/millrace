from __future__ import annotations

import base64
import csv
import hashlib
import io
import os
import signal
import zipfile
from types import SimpleNamespace

import pytest

from millrace.contracts.demo import VERSION
from millrace.contracts.setup import SetupRefusal


def installation(tmp_path, monkeypatch):
    from millrace.adapters.cli import setup_install_evidence as evidence

    prefix = tmp_path / "venv"
    root = prefix / "lib/site-packages"
    root.mkdir(parents=True)
    source = tmp_path / "wheelhouse"
    source.mkdir()
    distributions = {}
    for name, package in (
        ("millrace-ai", "millrace"),
        ("millrace-plus", "millrace_workflow_package"),
    ):
        info = name.replace("-", "_") + "-" + VERSION + ".dist-info"
        members = {
            package + "/__init__.py": b"# installed package\n",
            info + "/METADATA": (
                f"Metadata-Version: 2.1\nName: {name}\nVersion: {VERSION}\n"
            ).encode(),
            info + "/WHEEL": (
                b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
            ),
        }
        if name == "millrace-plus":
            members["millrace_plus/__init__.py"] = (
                b"# inert distribution version metadata\n"
            )
        for relative, data in members.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        payload = wheel(members)
        (source / evidence.wheel_filename(name)).write_bytes(payload)
        distributions[name] = SimpleNamespace(
            version=VERSION,
            files=list(members),
            locate_file=lambda item: root / str(item),
        )
    monkeypatch.setattr(evidence.sys, "prefix", str(prefix))
    monkeypatch.setattr(
        evidence,
        "metadata",
        SimpleNamespace(
            distribution=distributions.__getitem__,
            PackageNotFoundError=evidence.metadata.PackageNotFoundError,
        ),
    )
    return evidence, prefix, source, distributions


def wheel(members, *, include_record=True):
    members = dict(members)
    info = next(name.split("/", 1)[0] for name in members if name.endswith("/METADATA"))
    record_name = info + "/RECORD"
    members.pop(record_name, None)
    if include_record:
        rows = [
            (
                name,
                "sha256="
                + base64.urlsafe_b64encode(hashlib.sha256(content).digest())
                .decode()
                .rstrip("="),
                str(len(content)),
            )
            for name, content in members.items()
        ]
        rows.append((record_name, "", ""))
        record = io.StringIO()
        csv.writer(record, lineterminator="\n").writerows(rows)
        members[record_name] = record.getvalue().encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return out.getvalue()


def _archive_members(path):
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _wheel_with_directory(path, *, payload=b"", prefix=b""):
    members = _archive_members(path)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
        if payload:
            # ZipFile discards data for a name ending in '/', so make a
            # same-length regular member and turn its names into a directory
            # entry after the writer has emitted the payload.
            archive.writestr("millrace/xx", payload)
        else:
            archive.writestr("millrace/x/", b"")
    result = output.getvalue()
    if payload:
        assert result.count(b"millrace/xx") == 2
        result = result.replace(b"millrace/xx", b"millrace/x/")
    return prefix + result


def _rewrite_record(path, transform):
    members = _archive_members(path)
    record_name = next(name for name in members if name.endswith("/RECORD"))
    rows = list(csv.reader(io.StringIO(members[record_name].decode())))
    transformed = transform(rows)
    record = io.StringIO()
    csv.writer(record, lineterminator="\n").writerows(transformed)
    members[record_name] = record.getvalue().encode()
    path.write_bytes(wheel(members, include_record=False))


def test_local_disclosure_is_read_only_and_binds_actual_source(tmp_path, monkeypatch):
    evidence, prefix, source, _ = installation(tmp_path, monkeypatch)
    before = sorted(str(p) for p in prefix.rglob("*"))
    plan = evidence.inspect_evidence_source(str(source))
    assert plan["source"] == str(source)
    assert plan["destination"] == str(prefix / "share/millrace/install-artifacts")
    assert len(plan["wheels"]) == 2
    assert all(row["sha256"].startswith("sha256:") for row in plan["wheels"])
    assert sorted(str(p) for p in prefix.rglob("*")) == before
    selected = source / evidence.wheel_filename("millrace-ai")
    selected.write_bytes(selected.read_bytes() + b"changed")
    assert evidence.inspect_evidence_source(str(source)) != plan


def test_verified_wheels_are_retained_without_overwrite(tmp_path, monkeypatch):
    evidence, prefix, source, _ = installation(tmp_path, monkeypatch)
    evidence.acquire_evidence(str(source))
    target = prefix / "share/millrace/install-artifacts"
    assert sorted(p.name for p in target.iterdir()) == sorted(
        p.name for p in source.iterdir()
    )
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in target.iterdir())
    evidence.acquire_evidence(str(source))
    corrupt = target / evidence.wheel_filename("millrace-ai")
    corrupt.write_bytes(b"corrupt")
    with pytest.raises(SetupRefusal):
        evidence.acquire_evidence(str(source))
    assert corrupt.read_bytes() == b"corrupt"


@pytest.mark.parametrize(
    "attack", ["traversal", "duplicate", "omitted", "mismatch", "symlink", "oversize"]
)
def test_hostile_wheel_refuses_before_evidence_write(tmp_path, monkeypatch, attack):
    evidence, prefix, source, distributions = installation(tmp_path, monkeypatch)
    selected = source / evidence.wheel_filename("millrace-ai")
    if attack == "symlink":
        real = source / "other.whl"
        selected.rename(real)
        selected.symlink_to(real)
    else:
        with zipfile.ZipFile(selected) as archive:
            members = {n: archive.read(n) for n in archive.namelist()}
        if attack == "traversal":
            members["../outside"] = b"bad"
        elif attack == "omitted":
            members.pop("millrace/__init__.py")
        elif attack == "mismatch":
            members["millrace/__init__.py"] = b"changed"
        elif attack == "oversize":
            monkeypatch.setattr(evidence, "MAX_WHEEL_BYTES", 10)
        selected.write_bytes(wheel(members))
        if attack == "duplicate":
            with pytest.warns(UserWarning), zipfile.ZipFile(selected, "a") as archive:
                archive.writestr("millrace/__init__.py", b"# installed package\n")
    with pytest.raises(SetupRefusal):
        evidence.acquire_evidence(str(source))
    assert not (prefix / "share").exists()


def test_valid_archive_record_is_accepted_for_both_components(tmp_path, monkeypatch):
    evidence, _, source, _ = installation(tmp_path, monkeypatch)
    for name in ("millrace-ai", "millrace-plus"):
        payload = (source / evidence.wheel_filename(name)).read_bytes()
        assert evidence.verify_wheel(name, payload).startswith("sha256:")


def test_malformed_directory_local_header_is_refused(tmp_path, monkeypatch):
    evidence, _, source, _ = installation(tmp_path, monkeypatch)
    selected = source / evidence.wheel_filename("millrace-ai")
    payload = _wheel_with_directory(selected)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        directory = next(entry for entry in archive.infolist() if entry.is_dir())
        header_offset = directory.header_offset
    malformed = bytearray(payload)
    assert malformed[header_offset : header_offset + 4] == b"PK\x03\x04"
    malformed[header_offset : header_offset + 2] = b"XX"
    with zipfile.ZipFile(io.BytesIO(malformed)) as archive:
        with pytest.raises(zipfile.BadZipFile, match="Bad magic number"):
            archive.read("millrace/x/")
    with pytest.raises(SetupRefusal, match="invalid_install_artifact"):
        evidence.verify_wheel("millrace-ai", bytes(malformed))


def test_nonempty_directory_payload_is_refused(tmp_path, monkeypatch):
    evidence, _, source, _ = installation(tmp_path, monkeypatch)
    selected = source / evidence.wheel_filename("millrace-ai")
    payload = _wheel_with_directory(selected, payload=b"unexpected directory data")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        directory = next(entry for entry in archive.infolist() if entry.is_dir())
        assert archive.read(directory) == b"unexpected directory data"
    with pytest.raises(SetupRefusal, match="invalid_install_artifact"):
        evidence.verify_wheel("millrace-ai", payload)


def test_empty_directory_entry_is_valid(tmp_path, monkeypatch):
    evidence, _, source, _ = installation(tmp_path, monkeypatch)
    selected = source / evidence.wheel_filename("millrace-ai")
    payload = _wheel_with_directory(selected)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        directory = next(entry for entry in archive.infolist() if entry.is_dir())
        assert archive.read(directory) == b""
    assert evidence.verify_wheel("millrace-ai", payload).startswith("sha256:")


def test_valid_archive_prefix_with_empty_directory_is_accepted(
    tmp_path, monkeypatch
):
    evidence, _, source, _ = installation(tmp_path, monkeypatch)
    selected = source / evidence.wheel_filename("millrace-ai")
    payload = _wheel_with_directory(selected, prefix=b"self-extracting prefix\n")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        directory = next(entry for entry in archive.infolist() if entry.is_dir())
        assert archive.read(directory) == b""
    assert evidence.verify_wheel("millrace-ai", payload).startswith("sha256:")


@pytest.mark.parametrize("record_case", ["missing", "malformed", "duplicate"])
def test_invalid_archive_record_is_refused(tmp_path, monkeypatch, record_case):
    evidence, prefix, source, _ = installation(tmp_path, monkeypatch)
    selected = source / evidence.wheel_filename("millrace-ai")
    if record_case == "missing":
        selected.write_bytes(wheel(_archive_members(selected), include_record=False))
    elif record_case == "malformed":
        _rewrite_record(selected, lambda rows: [["not", "a", "record", "row"]])
    else:
        _rewrite_record(selected, lambda rows: rows + [rows[0]])
    with pytest.raises(SetupRefusal, match="invalid_install_artifact"):
        evidence.acquire_evidence(str(source))
    assert not (prefix / "share").exists()


@pytest.mark.parametrize("record_case", ["omitted", "extra"])
def test_archive_record_must_cover_exact_archive_members(
    tmp_path, monkeypatch, record_case
):
    evidence, prefix, source, _ = installation(tmp_path, monkeypatch)
    selected = source / evidence.wheel_filename("millrace-ai")

    def transform(rows):
        if record_case == "omitted":
            return [row for row in rows if row[0] != "millrace/__init__.py"]
        return rows + [["millrace/missing.py", "sha256=", "0"]]

    _rewrite_record(selected, transform)
    with pytest.raises(SetupRefusal, match="invalid_install_artifact"):
        evidence.acquire_evidence(str(source))
    assert not (prefix / "share").exists()


@pytest.mark.parametrize("record_case", ["digest", "weak_digest", "size"])
def test_archive_record_requires_strong_matching_hash_and_size(
    tmp_path, monkeypatch, record_case
):
    evidence, prefix, source, _ = installation(tmp_path, monkeypatch)
    selected = source / evidence.wheel_filename("millrace-ai")

    def transform(rows):
        updated = [row[:] for row in rows]
        row = next(row for row in updated if row[0] == "millrace/__init__.py")
        if record_case == "digest":
            row[1] = "sha256=" + "A" * 43
        elif record_case == "weak_digest":
            row[1] = "md5=" + "A" * 22
        else:
            row[2] = str(int(row[2]) + 1)
        return updated

    _rewrite_record(selected, transform)
    with pytest.raises(SetupRefusal, match="invalid_install_artifact"):
        evidence.acquire_evidence(str(source))
    assert not (prefix / "share").exists()


def test_archive_record_self_row_must_be_empty(tmp_path, monkeypatch):
    evidence, prefix, source, _ = installation(tmp_path, monkeypatch)
    selected = source / evidence.wheel_filename("millrace-ai")

    def transform(rows):
        updated = [row[:] for row in rows]
        row = next(row for row in updated if row[0].endswith(".dist-info/RECORD"))
        row[1:] = ["sha256=" + "A" * 43, "0"]
        return updated

    _rewrite_record(selected, transform)
    with pytest.raises(SetupRefusal, match="invalid_install_artifact"):
        evidence.acquire_evidence(str(source))
    assert not (prefix / "share").exists()


def test_fifo_wheelhouse_member_refuses_without_waiting(tmp_path, monkeypatch):
    evidence, _, source, _ = installation(tmp_path, monkeypatch)
    selected = source / evidence.wheel_filename("millrace-ai")
    selected.unlink()
    os.mkfifo(selected, 0o600)

    def timeout(_signum, _frame):
        raise TimeoutError("FIFO read did not reach regular-file validation")

    previous = signal.signal(signal.SIGALRM, timeout)
    signal.alarm(1)
    try:
        with pytest.raises(SetupRefusal, match="unsafe_install_artifact"):
            evidence.inspect_evidence_source(str(source))
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def test_pypi_inspection_never_downloads(tmp_path, monkeypatch):
    evidence, prefix, _, _ = installation(tmp_path, monkeypatch)
    monkeypatch.setattr(
        evidence, "_fetch", lambda *a: pytest.fail("network before consent")
    )
    plan = evidence.inspect_evidence_source("pypi")
    assert plan["source"] == "pypi"
    assert not (prefix / "share").exists()


@pytest.mark.parametrize(
    "url",
    [
        "http://pypi.org/a",
        "https://evil.example/a",
        "https://user:secret@pypi.org/a",
        "https://pypi.org:443/a",
        "https://files.pythonhosted.org.evil/a",
    ],
)
def test_download_origin_refuses_unapproved_urls(url):
    from millrace.adapters.cli import setup_install_evidence as evidence

    with pytest.raises(SetupRefusal):
        evidence.validate_download_url(url)


def test_transfer_stops_slow_stream_at_total_deadline(monkeypatch):
    from contextlib import nullcontext

    from millrace.adapters.cli import setup_install_evidence as evidence

    ticks = iter((0.0, 1.0, 31.0))
    monkeypatch.setattr(evidence, "monotonic", lambda: next(ticks), raising=False)
    response = SimpleNamespace(
        geturl=lambda: "https://pypi.org/test",
        headers={},
        read=lambda maximum: b"complete",
        read1=lambda maximum: b"x",
    )
    monkeypatch.setattr(
        evidence.urllib.request,
        "build_opener",
        lambda *a: SimpleNamespace(open=lambda *a, **kw: nullcontext(response)),
    )
    with pytest.raises(SetupRefusal, match="transfer_deadline"):
        evidence._fetch("https://pypi.org/test", 100)


def test_installed_hardlink_refuses_before_evidence_write(tmp_path, monkeypatch):
    import os

    evidence, prefix, source, distributions = installation(tmp_path, monkeypatch)
    target = distributions["millrace-ai"].locate_file("millrace/__init__.py")
    os.link(target, prefix / "alias")
    with pytest.raises(SetupRefusal):
        evidence.acquire_evidence(str(source))
    assert not (prefix / "share").exists()


def test_installed_change_at_final_guard_refuses_before_write(tmp_path, monkeypatch):
    evidence, prefix, source, distributions = installation(tmp_path, monkeypatch)
    target = distributions["millrace-ai"].locate_file("millrace/__init__.py")
    with pytest.raises(SetupRefusal):
        evidence.acquire_evidence(
            str(source), guard=lambda: target.write_bytes(b"changed")
        )
    assert not (prefix / "share").exists()


def test_empty_installed_resource_is_valid(tmp_path, monkeypatch):
    evidence, prefix, source, distributions = installation(tmp_path, monkeypatch)
    selected = source / evidence.wheel_filename("millrace-ai")
    with zipfile.ZipFile(selected) as archive:
        members = {n: archive.read(n) for n in archive.namelist()}
    members["millrace/__init__.py"] = b""
    distributions["millrace-ai"].locate_file("millrace/__init__.py").write_bytes(b"")
    selected.write_bytes(wheel(members))
    evidence.acquire_evidence(str(source))
    assert (prefix / "share/millrace/install-artifacts" / selected.name).is_file()


def test_real_owner_acquires_local_wheels_and_recovers_exact_receipt(
    tmp_path, monkeypatch
):
    from millrace.adapters.cli.setup_actions import SetupActionService
    from millrace.contracts.setup import SetupConsentRequest, SetupIntent, SetupRequest

    evidence, prefix, source, _ = installation(tmp_path, monkeypatch)
    service = SetupActionService(SetupRequest(), journal_root=tmp_path / "journal")
    intent = SetupIntent("acquire_install_evidence", str(source), None, "real-wheels")
    disclosed = service.disclose(intent)
    consent_request = SetupConsentRequest(
        intent,
        disclosed.selection_digest,
        disclosed.trust_disclosure.disclosure_digest,
        "real-consent",
        True,
    )
    accepted = service.accept(consent_request)
    result = service.resume(accepted.trust_acceptance.receipt_id)
    assert result.outcome == "applied"
    assert len(list((prefix / "share/millrace/install-artifacts").iterdir())) == 2
    fresh = SetupActionService(SetupRequest(), journal_root=tmp_path / "journal")
    assert fresh.resume(accepted.trust_acceptance.receipt_id) == result
    assert fresh.accept(consent_request) == accepted


def test_interrupted_real_retention_blocks_fresh_interactive_entry(
    tmp_path, monkeypatch
):
    from millrace.adapters.cli import setup_actions
    from millrace.adapters.cli.setup_actions import SetupActionService
    from millrace.contracts.setup import SetupConsentRequest, SetupIntent, SetupRequest

    evidence, prefix, source, _ = installation(tmp_path, monkeypatch)
    service = SetupActionService(SetupRequest(), journal_root=tmp_path / "journal")
    intent = SetupIntent(
        "acquire_install_evidence", str(source), None, "interrupted-wheels"
    )
    disclosed = service.disclose(intent)
    accepted = service.accept(
        SetupConsentRequest(
            intent,
            disclosed.selection_digest,
            disclosed.trust_disclosure.disclosure_digest,
            "interrupted-consent",
            True,
        )
    )
    perform = setup_actions._perform

    def interrupted(*args):
        perform(*args)
        raise KeyboardInterrupt

    monkeypatch.setattr(setup_actions, "_perform", interrupted)
    with pytest.raises(KeyboardInterrupt):
        service.resume(accepted.trust_acceptance.receipt_id)
    assert len(list((prefix / "share/millrace/install-artifacts").iterdir())) == 2
    fresh = SetupActionService(SetupRequest(), journal_root=tmp_path / "journal")
    with pytest.raises(SetupRefusal, match="unresolved_install_evidence"):
        fresh.check_evidence_recovery()
    result = fresh.resume(accepted.trust_acceptance.receipt_id)
    assert result.outcome == "unknown"
    with pytest.raises(SetupRefusal, match="unresolved_install_evidence"):
        fresh.check_evidence_recovery()


def test_special_file_wheel_member_refuses(tmp_path, monkeypatch):
    import stat

    evidence, prefix, source, _ = installation(tmp_path, monkeypatch)
    selected = source / evidence.wheel_filename("millrace-ai")
    with zipfile.ZipFile(selected) as archive:
        members = {n: archive.read(n) for n in archive.namelist()}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in members.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFCHR | 0o644) << 16
            archive.writestr(info, data)
    selected.write_bytes(output.getvalue())
    with pytest.raises(SetupRefusal):
        evidence.acquire_evidence(str(source))
    assert not (prefix / "share").exists()


@pytest.mark.parametrize("attack", ["length", "stream", "redirect"])
def test_transfer_bounds_and_redirect_refuse_without_credentials(monkeypatch, attack):
    from contextlib import nullcontext

    from millrace.adapters.cli import setup_install_evidence as evidence

    headers = {"Content-Length": "101"} if attack == "length" else {}
    response = SimpleNamespace(
        geturl=lambda: (
            "https://evil.example/" if attack == "redirect" else "https://pypi.org/test"
        ),
        headers=headers,
        read1=lambda maximum: b"x" * 101,
    )
    captured = []

    def opener(*handlers):
        captured.extend(handlers)
        return SimpleNamespace(open=lambda *a, **kw: nullcontext(response))

    monkeypatch.setattr(evidence.urllib.request, "build_opener", opener)
    with pytest.raises(SetupRefusal):
        evidence._fetch("https://pypi.org/test", 100)
    assert captured[0].proxies == {}
    with pytest.raises(SetupRefusal, match="redirect_refused"):
        captured[1].redirect_request(None, None, 302, "", {}, "https://pypi.org/next")


@pytest.mark.parametrize("attack", ["missing", "duplicate", "digest", "origin"])
def test_download_metadata_refuses_unmatched_wheel(monkeypatch, attack):
    import hashlib
    import json

    from millrace.adapters.cli import setup_install_evidence as evidence

    payload = b"wheel"
    row = {
        "filename": evidence.wheel_filename("millrace-ai"),
        "url": "https://files.pythonhosted.org/file.whl",
        "size": len(payload),
        "digests": {"sha256": hashlib.sha256(payload).hexdigest()},
    }
    rows = [row]
    if attack == "missing":
        rows = []
    elif attack == "duplicate":
        rows = [row, row]
    elif attack == "digest":
        row["digests"]["sha256"] = "0" * 64
    else:
        row["url"] = "https://evil.example/file.whl"
    monkeypatch.setattr(
        evidence,
        "_fetch",
        lambda url, maximum: (
            json.dumps({"urls": rows}).encode() if url.endswith("/json") else payload
        ),
    )
    with pytest.raises(SetupRefusal):
        evidence._download_wheel("millrace-ai")


def test_unrecorded_nested_record_is_not_exempt_from_wheel_membership(
    tmp_path, monkeypatch
):
    evidence, prefix, source, _ = installation(tmp_path, monkeypatch)
    selected = source / evidence.wheel_filename("millrace-ai")
    nested = "millrace_ai-" + VERSION + ".dist-info/nested/RECORD"
    with zipfile.ZipFile(selected, "a") as archive:
        archive.writestr(nested, b"unrecorded extra payload")
    with pytest.raises(SetupRefusal):
        evidence.acquire_evidence(str(source))
    assert not (prefix / "share").exists()


def test_installed_nested_record_is_an_ordinary_verified_resource(
    tmp_path, monkeypatch
):
    evidence, _, source, distributions = installation(tmp_path, monkeypatch)
    selected = source / evidence.wheel_filename("millrace-ai")
    nested = "millrace_ai-" + VERSION + ".dist-info/nested/RECORD"
    payload = b"ordinary resource whose basename is RECORD\n"
    target = distributions["millrace-ai"].locate_file(nested)
    target.parent.mkdir()
    target.write_bytes(payload)
    distributions["millrace-ai"].files.append(nested)
    members = _archive_members(selected)
    members[nested] = payload
    selected.write_bytes(wheel(members))
    assert evidence.verify_wheel("millrace-ai", selected.read_bytes()).startswith(
        "sha256:"
    )
