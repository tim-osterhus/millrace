"""Owned writable consumers, deterministic filesystem fault injection."""

import os
import sqlite3
from pathlib import Path

import pytest

from millrace.adapters.cli.demo_owned_runtime import (
    OwnedRuntimeRefusal,
    _identity,
    open_owned_runtime,
)
from millrace.kernel import empty_runtime_state
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.sqlite import SQLiteRuntimeStore


def fixture(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    inner = root / ".millrace"
    inner.mkdir(mode=0o700)
    cas = inner / "cas"
    (cas / "sha256").mkdir(parents=True, mode=0o700)
    cas.chmod(0o700)
    db = inner / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db)
    store.persist_runtime_state(empty_runtime_state(), ContentAddressedByteStore(cas))
    store.close()
    expected = {".": _identity(root.stat())}
    for parent, dirs, files in os.walk(root):
        for n in dirs + files:
            p = Path(parent) / n
            p.chmod(0o700 if p.is_dir() else 0o600)
            expected[str(p.relative_to(root))] = _identity(p.stat())
    return root, expected


def test_positive_durable_core_and_cas(tmp_path):
    root, expected = fixture(tmp_path)
    rt = open_owned_runtime(root, validate_owner=lambda: None, expected=expected)
    digest = rt.cas_store.put_bytes(b"new durable artifact")
    # Real Core mutation and actual SQLite commit must precede observable success.
    state = rt.store.load_runtime_state(rt.cas_store)
    rt.store.persist_runtime_state(state, rt.cas_store)
    expected = rt.retained_identity()
    rt.close()
    with sqlite3.connect(root / ".millrace/runtime.sqlite3") as actual:
        assert actual.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    again = open_owned_runtime(root, validate_owner=lambda: None, expected=expected)
    assert again.cas_store.get_bytes(digest) == b"new durable artifact"
    assert again.store.load_runtime_state(again.cas_store) == state
    again.close()


@pytest.mark.parametrize(
    "relative",
    [".millrace", ".millrace/runtime.sqlite3", ".millrace/cas", ".millrace/cas/sha256"],
)
@pytest.mark.parametrize("attack", ["symlink", "replacement", "mode"])
def test_path_consumer_refusal(tmp_path, relative, attack):
    root, expected = fixture(tmp_path)
    rt = open_owned_runtime(root, validate_owner=lambda: None, expected=expected)
    target = root / relative
    outside = tmp_path / "unrelated"
    outside.mkdir(mode=0o700)
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"preserve")
    if attack == "mode":
        target.chmod(0o777)
    else:
        saved = target.with_name(target.name + "-saved")
        target.rename(saved)
        if attack == "symlink":
            target.symlink_to(outside if saved.is_dir() else sentinel)
        elif saved.is_dir():
            target.mkdir(mode=0o700)
        else:
            target.write_bytes(saved.read_bytes())
            target.chmod(0o600)
    with pytest.raises((OwnedRuntimeRefusal, OSError)):
        if "runtime.sqlite3" in relative or relative == ".millrace":
            rt.store.load_runtime_state(rt.cas_store)
        else:
            rt.cas_store.put_bytes(b"new")
    assert sentinel.read_bytes() == b"preserve" and list(outside.iterdir()) == [
        sentinel
    ]
    rt.close()


def test_object_link_at_read_and_write_consumers(tmp_path):
    root, expected = fixture(tmp_path)
    rt = open_owned_runtime(root, validate_owner=lambda: None, expected=expected)
    digest = rt.cas_store.put_bytes(b"owned")
    obj = root / ".millrace/cas/sha256" / digest.split(":")[1]
    outside = tmp_path / "unrelated"
    outside.write_bytes(b"owned")
    obj.unlink()
    obj.symlink_to(outside)
    for operation in [
        lambda: rt.cas_store.get_bytes(digest),
        lambda: rt.cas_store.put_bytes(b"owned"),
    ]:
        with pytest.raises((OwnedRuntimeRefusal, OSError)):
            operation()
    assert outside.read_bytes() == b"owned"
    rt.close()


@pytest.mark.parametrize("sidecar", ["-wal", "-shm", "-journal"])
def test_sidecar_refused(tmp_path, sidecar):
    root, expected = fixture(tmp_path)
    (root / (".millrace/runtime.sqlite3" + sidecar)).write_bytes(b"pending")
    with pytest.raises(OwnedRuntimeRefusal, match="unsettled"):
        open_owned_runtime(root, validate_owner=lambda: None, expected=expected)


def test_stale_bytes_refused_on_actual_sql(tmp_path):
    root, expected = fixture(tmp_path)
    rt = open_owned_runtime(root, validate_owner=lambda: None, expected=expected)
    db = root / ".millrace/runtime.sqlite3"
    with db.open("r+b") as f:
        f.seek(100)
        f.write(b"wrong")
    with pytest.raises(OwnedRuntimeRefusal, match="stale"):
        rt.store.load_runtime_state(rt.cas_store)
    rt.close()


@pytest.mark.parametrize("phase", ["before_replace", "after_replace"])
def test_commit_failure_never_reports_success(tmp_path, monkeypatch, phase):
    root, expected = fixture(tmp_path)
    rt = open_owned_runtime(root, validate_owner=lambda: None, expected=expected)
    old = (root / ".millrace/runtime.sqlite3").read_bytes()
    original = os.replace

    def fault(*args, **kwargs):
        if phase == "after_replace":
            original(*args, **kwargs)
        raise OSError("injected durable replace failure")

    monkeypatch.setattr(os, "replace", fault)
    connection = rt.store._connection
    with pytest.raises(OSError):
        connection.execute("UPDATE store_metadata SET created_by='fault-proof'")
        connection.commit()
    with pytest.raises(OwnedRuntimeRefusal, match="durability_unknown"):
        connection.execute("SELECT 1")
    disk = (root / ".millrace/runtime.sqlite3").read_bytes()
    assert (disk == old) == (phase == "before_replace")
    with sqlite3.connect(root / ".millrace/runtime.sqlite3") as actual:
        assert actual.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    rt.close()


def test_parent_substitution_at_write_opener(tmp_path, monkeypatch):
    root, expected = fixture(tmp_path)
    rt = open_owned_runtime(root, validate_owner=lambda: None, expected=expected)
    target = root / ".millrace/cas/sha256"
    outside = tmp_path / "outside"
    outside.mkdir()
    original = os.open
    injected = False

    def attack(path, flags, *args, **kwargs):
        nonlocal injected
        if str(path).startswith(".owned-") and not injected:
            injected = True
            target.rename(target.with_name("old"))
            target.symlink_to(outside)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", attack)
    with pytest.raises((OwnedRuntimeRefusal, OSError)):
        rt.cas_store.put_bytes(b"fault")
    assert injected and list(outside.iterdir()) == []
    rt.close()


def test_active_sqlite_reader_refused(tmp_path):
    import datetime
    import json
    import subprocess
    import sys

    root, expected = fixture(tmp_path)
    db = root / ".millrace/runtime.sqlite3"
    argv = [
        sys.executable,
        "-B",
        "-c",
        "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); "
        "c.execute('BEGIN'); c.execute('SELECT * FROM store_metadata').fetchall(); "
        "print('locked',flush=True); sys.stdin.read(); c.close()",
        str(db),
    ]
    # The child performs only a real SQLite read transaction in this test root.
    child_env = {
        k: os.environ[k]
        for k in ["PATH", "HOME", "TMPDIR", "PYTHONDONTWRITEBYTECODE"]
        if k in os.environ
    }
    started = datetime.datetime.now(datetime.UTC).isoformat()
    child = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env,
    )
    assert child.stdout.readline().strip() == "locked"
    try:
        with pytest.raises(OwnedRuntimeRefusal, match="active_sqlite_connection"):
            open_owned_runtime(root, validate_owner=lambda: None, expected=expected)
    finally:
        stdout, stderr = child.communicate("release")
        record = {
            "argv": argv,
            "cwd": os.getcwd(),
            "pid": child.pid,
            "exit": child.returncode,
            "stdout": "locked\n" + stdout,
            "stderr": stderr,
            "env": child_env,
            "start": started,
            "end": datetime.datetime.now(datetime.UTC).isoformat(),
        }
        (tmp_path / "sqlite-child.json").write_text(json.dumps(record, indent=2))
        assert child.returncode == 0


def test_db_consumer_rejects_wal_header(tmp_path):
    root, expected = fixture(tmp_path)
    db = root / ".millrace/runtime.sqlite3"
    with sqlite3.connect(db) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    with pytest.raises(OwnedRuntimeRefusal, match="profile"):
        open_owned_runtime(root, validate_owner=lambda: None, expected=expected)


def test_owner_revocation_refuses_before_cas_or_db(tmp_path):
    root, expected = fixture(tmp_path)
    allowed = True

    def owner():
        if not allowed:
            raise OwnedRuntimeRefusal("foreground_lock_missing")

    rt = open_owned_runtime(root, validate_owner=owner, expected=expected)
    allowed = False
    with pytest.raises(OwnedRuntimeRefusal, match="foreground_lock"):
        rt.cas_store.put_bytes(b"forbidden")
    with pytest.raises(OwnedRuntimeRefusal, match="foreground_lock"):
        rt.store.load_runtime_state(rt.cas_store)
    rt.close()


def _event(store, key="one"):
    from millrace.adapters.runner_contract import RedactionPolicy
    from millrace.substrate.runner_session_events import RunnerSessionEventWriter

    return RunnerSessionEventWriter(
        store,
        session_id="owned-session",
        run_id="owned-run",
        dispatch_generation=1,
        redaction_policy=RedactionPolicy(policy_id="test", secret_tokens=()),
    ).record("runner_progress", {"text": key}, observed_at=1, replay_key=key)


def test_owned_event_store_and_public_snapshot_are_durable(tmp_path):
    root, expected = fixture(tmp_path)
    rt = open_owned_runtime(root, validate_owner=lambda: None, expected=expected)
    events = rt.open_session_event_store()
    first = _event(events)
    assert events.stats().retained_records == 1
    events.close()
    snapshot = rt.session_event_snapshot()
    assert snapshot.read(
        "owned-run", after_sequence=0, session_id="owned-session"
    ).events == (first,)
    expected = rt.retained_identity()
    rt.close()
    again = open_owned_runtime(root, validate_owner=lambda: None, expected=expected)
    events = again.open_session_event_store()
    assert _event(events) == first
    assert events.stats().retained_records == 1
    events.close()
    again.close()
    db = root / ".millrace/runtime.sqlite3.runner-session-events.sqlite3"
    with sqlite3.connect(db) as real:
        assert real.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert real.execute("SELECT count(*) FROM session_events").fetchone() == (1,)
    assert db.read_bytes()[18:20] == b"\x01\x01"
    assert db.with_name(db.name + ".public.json").is_file()


@pytest.mark.parametrize("member", [".sqlite3", ".sqlite3.public.json"])
@pytest.mark.parametrize("attack", ["symlink", "replacement", "mode"])
def test_owned_event_consumers_refuse_unsafe_members(tmp_path, member, attack):
    root, expected = fixture(tmp_path)
    rt = open_owned_runtime(root, validate_owner=lambda: None, expected=expected)
    events = rt.open_session_event_store()
    _event(events)
    events.close()
    target = root / (".millrace/runtime.sqlite3.runner-session-events" + member)
    outside = tmp_path / "outside"
    outside.write_bytes(b"must survive")
    if attack == "mode":
        target.chmod(0o666)
    else:
        old = target.with_name(target.name + ".saved")
        target.rename(old)
        if attack == "symlink":
            target.symlink_to(outside)
        else:
            target.write_bytes(old.read_bytes())
            target.chmod(0o600)
    for consumer in [rt.open_session_event_store, rt.session_event_snapshot]:
        with pytest.raises((OwnedRuntimeRefusal, OSError)):
            consumer()
    assert outside.read_bytes() == b"must survive"
    rt.close()


@pytest.mark.parametrize(
    "name",
    [
        "runtime.sqlite3.runner-session-events.sqlite3-wal",
        "runtime.sqlite3.runner-session-events.sqlite3-shm",
        "runtime.sqlite3.runner-session-events.sqlite3-journal",
        ".runner-snapshot-interrupted",
    ],
)
def test_owned_event_unfinished_sidecars_refuse(tmp_path, name):
    root, expected = fixture(tmp_path)
    residue = root / ".millrace" / name
    residue.write_bytes(b"unfinished")
    with pytest.raises(OwnedRuntimeRefusal, match="unsettled"):
        open_owned_runtime(root, validate_owner=lambda: None, expected=expected)
    assert residue.read_bytes() == b"unfinished"


@pytest.mark.parametrize("operation", ["get", "put", "reopen"])
def test_nested_cas_residue_refuses_every_consumer(tmp_path, monkeypatch, operation):
    root, expected = fixture(tmp_path)
    rt = open_owned_runtime(root, validate_owner=lambda: None, expected=expected)
    digest = rt.cas_store.put_bytes(b"existing")
    expected = rt.retained_identity()
    original = os.replace

    def fail(*args, **kwargs):
        raise OSError("injected CAS publication failure")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        rt.cas_store.put_bytes(b"unpublished")
    residue = list((root / ".millrace/cas/sha256").glob(".owned-*"))
    assert len(residue) == 1
    monkeypatch.setattr(os, "replace", original)
    rt.close()
    if operation == "reopen":
        with pytest.raises(OwnedRuntimeRefusal, match="unsettled"):
            open_owned_runtime(root, validate_owner=lambda: None, expected=expected)
    else:
        # Exercise actual CAS consumers against a newly constructed tree before
        # any SQLite load; this prevents only testing the outer opener.
        from millrace.adapters.cli.demo_owned_runtime import _CAS, _Tree

        tree = _Tree(root, lambda: None, expected)
        cas = _CAS(tree)
        with pytest.raises(OwnedRuntimeRefusal, match="unsettled"):
            cas.get_bytes(digest) if operation == "get" else cas.put_bytes(b"next")
        tree.close()
    assert residue[0].read_bytes() == b"unpublished"


@pytest.mark.parametrize("member", ["event-db", "public-snapshot"])
@pytest.mark.parametrize("phase", ["before", "after"])
def test_owned_event_failed_publication_retains_uncertainty(
    tmp_path, monkeypatch, member, phase
):
    root, expected = fixture(tmp_path)
    rt = open_owned_runtime(root, validate_owner=lambda: None, expected=expected)
    events = rt.open_session_event_store()
    _event(events)
    events.close()
    expected = rt.retained_identity()
    events = rt.open_session_event_store()
    original = os.replace
    suffix = ".public.json" if member == "public-snapshot" else "events.sqlite3"

    def fail(src, dst, **kwargs):
        if str(dst).endswith(suffix):
            if phase == "after":
                original(src, dst, **kwargs)
            raise OSError("injected event publication uncertainty")
        return original(src, dst, **kwargs)

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        _event(events, "second")
    with pytest.raises(OwnedRuntimeRefusal):
        rt.session_event_snapshot()
    events.close()
    rt.close()
    monkeypatch.setattr(os, "replace", original)
    with pytest.raises(OwnedRuntimeRefusal):
        open_owned_runtime(root, validate_owner=lambda: None, expected=expected)


@pytest.mark.parametrize("consumer", ["initialize", "snapshot-read", "snapshot-write"])
def test_event_substitution_at_actual_consumer(tmp_path, monkeypatch, consumer):
    root, expected = fixture(tmp_path)
    rt = open_owned_runtime(root, validate_owner=lambda: None, expected=expected)
    if consumer != "initialize":
        events = rt.open_session_event_store()
        _event(events)
        events.close()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"preserve")
    original = os.open
    injected = False

    def attack(name, flags, *args, **kwargs):
        nonlocal injected
        trigger = (
            str(name).endswith(".public.json")
            if consumer == "snapshot-read"
            else str(name).startswith(".owned-")
        )
        if trigger and not injected:
            injected = True
            parent = root / ".millrace"
            parent.rename(root / "saved-millrace")
            parent.symlink_to(outside)
        return original(name, flags, *args, **kwargs)

    if consumer == "snapshot-write":
        events = rt.open_session_event_store()
        original_write = rt._owned_tree.write

        def publication(relative, payload, **kwargs):
            if relative.endswith(".public.json"):
                monkeypatch.setattr(os, "open", attack)
            return original_write(relative, payload, **kwargs)

        monkeypatch.setattr(rt._owned_tree, "write", publication)

        def operation():
            return _event(events, "late")
    else:
        monkeypatch.setattr(os, "open", attack)
        operation = (
            rt.open_session_event_store
            if consumer == "initialize"
            else rt.session_event_snapshot
        )
    with pytest.raises((OwnedRuntimeRefusal, OSError)):
        operation()
    assert injected and list(outside.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"preserve"
    if consumer == "snapshot-write":
        events.close()
    rt.close()
