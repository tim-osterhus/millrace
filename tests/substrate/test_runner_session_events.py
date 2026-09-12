from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from millrace.adapters.runner_contract import RedactionPolicy
from millrace.contracts.runner_events import (
    RUNNER_SESSION_EVENT_SCHEMA_VERSION,
    RunnerSessionEvent,
)
from millrace.substrate.runner_session_events import (
    RUNNER_SESSION_EVENT_MAX_BYTES,
    RUNNER_SESSION_EVENT_READ_MAX_RECORDS,
    RUNNER_SESSION_EVENT_REPLAY_KEY_MAX_BYTES,
    RUNNER_SESSION_EVENT_STORE_MAX_BYTES,
    RUNNER_SESSION_EVENT_STORE_MAX_RECORDS,
    RUNNER_SESSION_EVENT_STORE_MAX_STREAMS,
    RUNNER_SESSION_EVENT_UPDATE_RATE_PER_SECOND,
    RunnerSessionEventStore,
    RunnerSessionEventWriter,
    runner_session_event_store_path,
)


def _writer(
    tmp_path,
    *,
    secret_tokens: tuple[str, ...] = (),
) -> RunnerSessionEventWriter:
    return RunnerSessionEventWriter(
        RunnerSessionEventStore.initialize(tmp_path / "session-events.sqlite3"),
        session_id="session-1",
        run_id="run-1",
        dispatch_generation=2,
        redaction_policy=RedactionPolicy(
            policy_id="redaction.default",
            secret_tokens=secret_tokens,
        ),
    )


def test_runner_session_event_envelope_is_versioned_bounded_and_exact() -> None:
    event = RunnerSessionEvent(
        event_id="runner-session-event:" + "a" * 64,
        session_id="session-1",
        run_id="run-1",
        dispatch_generation=1,
        sequence=1,
        kind="session_started",
        observed_at=1,
        bounded_payload={"state": "running"},
        redaction_policy_id="redaction.default",
        truncation_metadata={"truncated": False, "original_bytes": 19},
    )

    assert event.schema_version == RUNNER_SESSION_EVENT_SCHEMA_VERSION
    assert set(event.payload()) == {
        "schema_version",
        "event_id",
        "session_id",
        "run_id",
        "dispatch_generation",
        "sequence",
        "kind",
        "observed_at",
        "bounded_payload",
        "redaction_policy_id",
        "truncation_metadata",
    }
    with pytest.raises(ValueError, match="kind"):
        replace(event, kind="provider_magic")
    with pytest.raises(ValueError, match="sequence"):
        replace(event, sequence=0)
    with pytest.raises(ValueError, match="bounded_payload"):
        replace(event, bounded_payload={"text": "x" * RUNNER_SESSION_EVENT_MAX_BYTES})


def test_event_replay_key_has_an_explicit_byte_ceiling(tmp_path) -> None:
    writer = _writer(tmp_path)

    with pytest.raises(ValueError, match="replay-key ceiling"):
        writer.record_progress(
            {"message": "bounded"},
            observed_at=1,
            replay_key="x" * (RUNNER_SESSION_EVENT_REPLAY_KEY_MAX_BYTES + 1),
        )


def test_streaming_redaction_hides_secret_split_across_chunks(tmp_path) -> None:
    writer = _writer(
        tmp_path,
        secret_tokens=("split-secret", "split", "秘密🔐"),
    )

    writer.record_progress_chunk("before split-", observed_at=1)
    writer.record_progress_chunk("secret and 秘", observed_at=1)
    writer.record_progress_chunk("密🔐 after", observed_at=1, final=True)
    page = writer.store.read("run-1", after_sequence=0)

    serialized = repr([event.payload() for event in page.events])
    assert "split-secret" not in serialized
    assert "秘密🔐" not in serialized
    assert "[REDACTED]" in serialized
    assert "split-secret" not in (
        tmp_path / "session-events.sqlite3"
    ).read_bytes().decode(errors="ignore")


def test_thousands_of_progress_updates_remain_within_fixed_store_bounds(
    tmp_path,
) -> None:
    writer = _writer(tmp_path)
    for index in range(10_000):
        writer.record_progress(
            {"message": f"progress-{index}", "percent": index % 101},
            observed_at=index // 1000,
            replay_key=f"update-{index}",
        )

    stats = writer.store.stats()
    assert stats.retained_records <= RUNNER_SESSION_EVENT_STORE_MAX_RECORDS
    assert stats.retained_bytes <= RUNNER_SESSION_EVENT_STORE_MAX_BYTES
    assert stats.last_sequence == 10_000


def test_terminal_survives_compaction_and_ids_are_replay_safe(tmp_path) -> None:
    writer = _writer(tmp_path)
    started = writer.record(
        "session_started",
        {"state": "running"},
        observed_at=1,
        replay_key="started",
    )
    assert (
        writer.record(
            "session_started",
            {"state": "running"},
            observed_at=1,
            replay_key="started",
        )
        == started
    )
    with pytest.raises(ValueError, match="replay"):
        writer.record(
            "session_started",
            {"state": "misleading"},
            observed_at=1,
            replay_key="started",
        )
    for index in range(2_000):
        writer.record_progress(
            {"message": "x" * 1000, "index": index},
            observed_at=index + 2,
            replay_key=f"progress-{index}",
        )
    for index in range(2_000, 4_000):
        writer.record_progress(
            {"message": "y" * 1000, "index": index},
            observed_at=index + 2,
            replay_key=f"progress-{index}",
        )
    terminal = writer.record(
        "session_terminal",
        {"terminal_state": "completed"},
        observed_at=5_000,
        replay_key="terminal",
    )

    retained = writer.store.read("run-1", after_sequence=0)
    assert retained.gap is not None
    # Retention spans multiple bounded pages; the terminal can be on the last.
    events = list(retained.events)
    while events[-1].sequence < retained.last_sequence:
        page = writer.store.read("run-1", after_sequence=events[-1].sequence)
        assert page.events and page.events[0].sequence > events[-1].sequence
        events.extend(page.events)
        assert len(events) <= RUNNER_SESSION_EVENT_STORE_MAX_RECORDS
    assert terminal in events
    assert [event.sequence for event in events] == sorted(
        event.sequence for event in events
    )
    assert len({event.event_id for event in events}) == len(events)


def test_interleaved_sessions_keep_independent_sequences_and_cursors(
    tmp_path,
) -> None:
    store = RunnerSessionEventStore.initialize(tmp_path / "session-events.sqlite3")
    first = RunnerSessionEventWriter(
        store,
        session_id="session-a",
        run_id="run-a",
        dispatch_generation=1,
        redaction_policy=RedactionPolicy(policy_id="redaction.default"),
    )
    second = RunnerSessionEventWriter(
        store,
        session_id="session-b",
        run_id="run-b",
        dispatch_generation=1,
        redaction_policy=RedactionPolicy(policy_id="redaction.default"),
    )

    first.record_progress({"step": 1}, observed_at=1, replay_key="a-1")
    second.record_progress({"step": 1}, observed_at=1, replay_key="b-1")
    first.record_progress({"step": 2}, observed_at=2, replay_key="a-2")

    first_page = store.read(
        "run-a",
        after_sequence=0,
        session_id="session-a",
    )
    second_page = store.read(
        "run-b",
        after_sequence=0,
        session_id="session-b",
    )
    assert [event.sequence for event in first_page.events] == [1, 2]
    assert first_page.last_sequence == 2
    assert first_page.gap is None
    assert [event.sequence for event in second_page.events] == [1]
    assert second_page.last_sequence == 1
    assert second_page.gap is None


def test_reconnect_after_compaction_gets_explicit_gap_and_bounded_page(
    tmp_path,
) -> None:
    writer = _writer(tmp_path)
    for index in range(RUNNER_SESSION_EVENT_STORE_MAX_RECORDS * 3):
        writer.record_progress(
            {"message": f"progress-{index}"},
            observed_at=index,
            replay_key=f"progress-{index}",
        )

    page = writer.store.read(
        "run-1",
        after_sequence=1,
        limit=RUNNER_SESSION_EVENT_READ_MAX_RECORDS * 10,
    )
    assert page.gap is not None
    assert page.gap.after_sequence == 1
    assert page.gap.resumes_at_sequence == page.events[0].sequence
    assert len(page.events) <= RUNNER_SESSION_EVENT_READ_MAX_RECORDS
    assert (
        writer.store.read(
            "run-1",
            after_sequence=page.last_sequence + 10,
        ).gap
        is None
    )


def test_slow_reader_does_not_block_event_production(tmp_path) -> None:
    path = tmp_path / "session-events.sqlite3"
    writer = _writer(tmp_path)
    reader = RunnerSessionEventStore.open(path)
    reader._connection.execute("BEGIN")  # noqa: SLF001 - deliberate slow consumer
    reader._connection.execute("SELECT * FROM session_events").fetchall()  # noqa: SLF001

    event = writer.record_progress(
        {"message": "producer remains independent"},
        observed_at=1,
        replay_key="while-reader-idle",
    )

    assert event.sequence == 1
    reader._connection.rollback()  # noqa: SLF001
    reader.close()


def test_sidecar_events_cannot_mutate_runtime_database_or_runtime_state(
    tmp_path,
) -> None:
    runtime_db = tmp_path / "runtime.sqlite3"
    runtime_db.write_bytes(b"authoritative-runtime-state")
    before = runtime_db.read_bytes()
    writer = _writer(tmp_path)

    writer.record(
        "session_terminal",
        {
            "terminal_state": "completed",
            "claimed_workflow_mutation": True,
            "claimed_runner_observation": True,
        },
        observed_at=1,
        replay_key="misleading-terminal",
    )

    assert runtime_db.read_bytes() == before
    assert writer.store.read("run-1", after_sequence=0).events


def test_sidecar_path_isolated_for_runtime_databases_in_same_directory(
    tmp_path,
) -> None:
    first = runner_session_event_store_path(tmp_path / "first.sqlite3")
    second = runner_session_event_store_path(tmp_path / "second.sqlite3")

    assert first != second
    assert first.parent == second.parent == tmp_path


def test_sidecar_schema_shape_is_exact_and_separately_versioned(tmp_path) -> None:
    path = tmp_path / "session-events.sqlite3"
    store = RunnerSessionEventStore.initialize(path)
    store.close()
    connection = sqlite3.connect(path)
    connection.execute("ALTER TABLE session_events ADD COLUMN workflow_authority TEXT")
    connection.commit()
    connection.close()

    with pytest.raises(ValueError, match="schema shape"):
        RunnerSessionEventStore.initialize(path)


def test_many_historical_sessions_cannot_grow_stream_metadata_unbounded(
    tmp_path,
) -> None:
    store = RunnerSessionEventStore.initialize(tmp_path / "session-events.sqlite3")
    for index in range(RUNNER_SESSION_EVENT_STORE_MAX_STREAMS * 3):
        RunnerSessionEventWriter(
            store,
            session_id=f"session-{index}",
            run_id=f"run-{index}",
            dispatch_generation=1,
            redaction_policy=RedactionPolicy(policy_id="redaction.default"),
        ).record(
            "session_terminal",
            {"terminal_state": "completed"},
            observed_at=index,
            replay_key="terminal",
        )

    stats = store.stats()
    assert stats.retained_streams <= RUNNER_SESSION_EVENT_STORE_MAX_STREAMS
    assert stats.retained_records <= RUNNER_SESSION_EVENT_STORE_MAX_RECORDS
    assert stats.retained_bytes <= RUNNER_SESSION_EVENT_STORE_MAX_BYTES


def test_active_stream_ceiling_refuses_new_stream_without_resetting_sequences(
    tmp_path,
) -> None:
    store = RunnerSessionEventStore.initialize(tmp_path / "session-events.sqlite3")
    writers = []
    for index in range(RUNNER_SESSION_EVENT_STORE_MAX_STREAMS):
        writer = RunnerSessionEventWriter(
            store,
            session_id=f"active-session-{index}",
            run_id=f"run-{index}",
            dispatch_generation=1,
            redaction_policy=RedactionPolicy(policy_id="redaction.default"),
        )
        writer.record_progress({"step": 1}, observed_at=index, replay_key="step-1")
        writers.append(writer)
    refused = RunnerSessionEventWriter(
        store,
        session_id="one-too-many",
        run_id="one-too-many",
        dispatch_generation=1,
        redaction_policy=RedactionPolicy(policy_id="redaction.default"),
    )

    with pytest.raises(ValueError, match="stream ceiling"):
        refused.record_progress({"step": 1}, observed_at=999, replay_key="step-1")
    resumed = writers[0].record_progress(
        {"step": 2},
        observed_at=1_000_000_000,
        replay_key="step-2",
    )

    assert resumed.sequence == 2
    assert store.stats().retained_streams == RUNNER_SESSION_EVENT_STORE_MAX_STREAMS


@pytest.mark.parametrize(
    "kind",
    ("runner_progress", "tool_activity", "usage_update", "diagnostic"),
)
def test_provider_event_rate_is_bounded_per_kind_and_second(
    tmp_path,
    kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.substrate import runner_session_events

    monkeypatch.setattr(runner_session_events, "_admission_second", lambda: 42)
    writer = _writer(tmp_path)
    accepted = 0
    for index in range(RUNNER_SESSION_EVENT_UPDATE_RATE_PER_SECOND + 5):
        try:
            writer.record(
                kind,
                {"index": index},
                observed_at=1_000_000_000,
                replay_key=f"{kind}-{index}",
            )
        except ValueError:
            continue
        accepted += 1

    retained = writer.store.read("run-1", after_sequence=0).events
    assert len(retained) <= RUNNER_SESSION_EVENT_UPDATE_RATE_PER_SECOND
    if kind == "runner_progress":
        assert accepted == RUNNER_SESSION_EVENT_UPDATE_RATE_PER_SECOND + 5
    else:
        assert accepted == RUNNER_SESSION_EVENT_UPDATE_RATE_PER_SECOND


def test_provider_event_rate_ceiling_is_aggregate_across_kinds(tmp_path) -> None:
    writer = _writer(tmp_path)
    for index in range(10):
        writer.record(
            "tool_activity",
            {"index": index},
            observed_at=2_000_000_000,
            replay_key=f"tool-{index}",
        )
        writer.record(
            "usage_update",
            {"index": index},
            observed_at=2_000_000_000,
            replay_key=f"usage-{index}",
        )

    with pytest.raises(ValueError, match="update-rate ceiling"):
        writer.record(
            "diagnostic",
            {"overflow": True},
            observed_at=2_000_000_000,
            replay_key="diagnostic-overflow",
        )


def test_backdated_provider_events_cannot_reset_admission_after_compaction(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.substrate import runner_session_events

    monkeypatch.setattr(runner_session_events, "_admission_second", lambda: 42)
    writer = _writer(tmp_path)
    accepted = 0
    kinds = ("tool_activity", "usage_update", "diagnostic")
    for index in range(RUNNER_SESSION_EVENT_UPDATE_RATE_PER_SECOND):
        writer.record(
            kinds[index % len(kinds)],
            {"index": index},
            observed_at=index,
            replay_key=f"backdated-{index}",
        )
        accepted += 1
    for stream_index in range(40):
        filler = RunnerSessionEventWriter(
            writer.store,
            session_id=f"filler-session-{stream_index}",
            run_id=f"filler-run-{stream_index}",
            dispatch_generation=1,
            redaction_policy=RedactionPolicy(policy_id="redaction.default"),
        )
        for index in range(10):
            filler.record(
                "diagnostic",
                {"index": index},
                observed_at=1_000_000_000_000 + stream_index * 10 + index,
                replay_key=f"filler-{stream_index}-{index}",
            )
    assert (
        len(
            writer.store.read(
                "run-1",
                after_sequence=0,
                session_id="session-1",
            ).events
        )
        < accepted
    )
    for index in range(
        RUNNER_SESSION_EVENT_UPDATE_RATE_PER_SECOND,
        30,
    ):
        try:
            writer.record(
                kinds[index % len(kinds)],
                {"index": index},
                observed_at=(10_000 - index) * 1_000_000_000,
                replay_key=f"backdated-{index}",
            )
        except ValueError:
            continue
        accepted += 1

    assert accepted == RUNNER_SESSION_EVENT_UPDATE_RATE_PER_SECOND


def test_alternating_observed_timestamps_share_store_admission_bucket(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.substrate import runner_session_events

    monkeypatch.setattr(runner_session_events, "_admission_second", lambda: 7)
    writer = _writer(tmp_path)
    for index in range(RUNNER_SESSION_EVENT_UPDATE_RATE_PER_SECOND):
        writer.record(
            "tool_activity" if index % 2 else "usage_update",
            {"index": index},
            observed_at=(index % 2) * 9_000_000_000_000,
            replay_key=f"alternating-{index}",
        )

    with pytest.raises(ValueError, match="update-rate ceiling"):
        writer.record(
            "diagnostic",
            {"overflow": True},
            observed_at=123,
            replay_key="alternating-overflow",
        )


def test_lifecycle_and_terminal_survive_provider_pressure(tmp_path) -> None:
    writer = _writer(tmp_path)
    started = writer.record(
        "session_started",
        {"state": "running"},
        observed_at=0,
        replay_key="started",
    )
    cancelled = writer.record(
        "cancellation_progress",
        {"state": "requested"},
        observed_at=1,
        replay_key="cancelled",
    )
    for index in range(300):
        kind = ("diagnostic", "tool_activity")[index % 2]
        try:
            writer.record(
                kind,
                {"index": index, "padding": "x" * 4000},
                observed_at=(index + 1) * 1_000_000_000,
                replay_key=f"provider-{index}",
            )
        except ValueError:
            pass
    terminal = writer.record(
        "session_terminal",
        {"terminal_state": "completed"},
        observed_at=400_000_000_000,
        replay_key="terminal",
    )

    retained_ids = {
        str(row[0])
        for row in writer.store._connection.execute(  # noqa: SLF001
            "SELECT event_id FROM session_events WHERE session_id = 'session-1'"
        ).fetchall()
    }
    assert {started.event_id, cancelled.event_id, terminal.event_id} <= retained_ids


def test_known_stream_with_no_successor_returns_history_unavailable_gap(
    tmp_path,
) -> None:
    writer = _writer(tmp_path)
    writer.record_progress(
        {"message": "temporary"},
        observed_at=1,
        replay_key="temporary",
    )
    writer.store._connection.execute(  # noqa: SLF001 - compaction fixture
        "DELETE FROM session_events WHERE session_id = 'session-1'"
    )
    writer.store._connection.commit()  # noqa: SLF001

    page = writer.store.read(
        "run-1",
        after_sequence=0,
        session_id="session-1",
    )
    assert page.stream_found is True
    assert page.events == ()
    assert page.gap is not None
    assert page.gap.resumes_at_sequence is None


def test_identical_concurrent_first_append_replays_one_event(tmp_path) -> None:
    path = tmp_path / "session-events.sqlite3"
    RunnerSessionEventStore.initialize(path).close()

    def append_once() -> RunnerSessionEvent:
        store = RunnerSessionEventStore.open(path)
        try:
            return RunnerSessionEventWriter(
                store,
                session_id="concurrent-session",
                run_id="concurrent-run",
                dispatch_generation=1,
                redaction_policy=RedactionPolicy(policy_id="redaction.default"),
            ).record_progress(
                {"message": "same"},
                observed_at=1,
                replay_key="same-replay",
            )
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = tuple(executor.map(lambda _index: append_once(), range(2)))

    assert first == second
    store = RunnerSessionEventStore.open(path)
    assert store.stats().retained_records == 1
    store.close()


def test_secret_replay_keys_are_opaque_and_remain_distinct(tmp_path) -> None:
    secret = "REPLAY_SECRET"
    writer = _writer(tmp_path, secret_tokens=(secret,))
    first = writer.record_progress(
        {"message": "first"},
        observed_at=1,
        replay_key=f"key-{secret}-one",
    )
    second = writer.record_progress(
        {"message": "second"},
        observed_at=2,
        replay_key=f"key-{secret}-two",
    )

    assert first.event_id != second.event_id
    connection = writer.store._connection  # noqa: SLF001
    identities = [
        str(row[0])
        for row in connection.execute(
            "SELECT replay_key FROM session_events ORDER BY sequence"
        ).fetchall()
    ]
    assert all(identity.startswith("sha256:") for identity in identities)
    assert secret not in repr(identities)


def test_public_snapshot_reads_live_and_closed_wal_without_byte_or_mode_changes(
    tmp_path,
):
    from millrace.substrate.runner_session_events import public_snapshot_path

    writer = _writer(tmp_path)
    writer.record_progress({"message": "first"}, observed_at=1, replay_key="first")
    path = tmp_path / "session-events.sqlite3"

    def inventory():
        return {
            p.name: (p.read_bytes(), p.stat().st_mode)
            for p in tmp_path.iterdir()
            if p.is_file()
        }

    for closed in (False, True):
        if closed:
            writer.store.close()
        before = inventory()
        reader = RunnerSessionEventStore.open_readonly(path)
        assert (
            reader.read("run-1", session_id="session-1", after_sequence=0).last_sequence
            == 1
        )
        assert reader.captured_at_ns > 0
        reader.close()
        assert inventory() == before
    assert public_snapshot_path(path).stat().st_mode & 0o777 == 0o600


def test_failed_publication_keeps_committed_event_and_next_writer_recovers(
    tmp_path, monkeypatch
):
    import os

    from millrace.substrate.runner_session_events import public_snapshot_path

    writer = _writer(tmp_path)
    writer.record_progress({"message": "first"}, observed_at=1, replay_key="first")
    replace = os.replace

    def fail(*args):
        raise OSError("publication fixture failure")

    monkeypatch.setattr(os, "replace", fail)
    writer.record_progress({"message": "second"}, observed_at=2, replay_key="second")
    assert (
        writer.store.read(
            "run-1", session_id="session-1", after_sequence=0
        ).last_sequence
        == 2
    )
    assert not public_snapshot_path(tmp_path / "session-events.sqlite3").exists()
    monkeypatch.setattr(os, "replace", replace)
    writer.record_progress({"message": "third"}, observed_at=3, replay_key="third")
    reader = RunnerSessionEventStore.open_readonly(tmp_path / "session-events.sqlite3")
    assert (
        reader.read("run-1", session_id="session-1", after_sequence=0).last_sequence
        == 3
    )
    writer.store.close()


def test_snapshot_rejects_corrupt_oversized_or_missing_capture(tmp_path):
    from millrace.substrate.runner_session_events import public_snapshot_path

    writer = _writer(tmp_path)
    writer.record_progress({"message": "first"}, observed_at=1, replay_key="first")
    writer.store.close()
    path = tmp_path / "session-events.sqlite3"
    capture = public_snapshot_path(path)
    valid = capture.read_bytes()
    for invalid in (
        b"not json",
        valid.replace(b'"last', b'"lost'),
        b"x" * (2 * 1024 * 1024 + 1),
    ):
        if invalid == valid:
            invalid = valid.replace(b"first", b"other")
        capture.write_bytes(invalid)
        with pytest.raises(ValueError):
            RunnerSessionEventStore.open_readonly(path)
    capture.unlink()
    with pytest.raises(FileNotFoundError):
        RunnerSessionEventStore.open_readonly(path)


def test_concurrent_snapshot_publishers_never_regress_committed_sequences(tmp_path):
    from millrace.adapters.runner_contract import RedactionPolicy

    path = tmp_path / "events.sqlite3"
    initial = RunnerSessionEventStore.initialize(path)
    initial.close()

    def write(label):
        store = RunnerSessionEventStore.open(path)
        writer = RunnerSessionEventWriter(
            store,
            session_id=label,
            run_id=label,
            dispatch_generation=1,
            redaction_policy=RedactionPolicy(policy_id="test"),
        )
        for n in range(8):
            writer.record_progress({"n": n}, observed_at=n, replay_key=str(n))
        store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(write, ("one", "two")))
    snapshot = RunnerSessionEventStore.open_readonly(path)
    assert snapshot.stream_scope("one")["last_sequence"] == 8
    assert snapshot.stream_scope("two")["last_sequence"] == 8


@pytest.mark.parametrize(
    "point",
    [
        "before_invalidation",
        "after_invalidation_before_commit",
        "after_commit",
        "after_replace",
    ],
)
def test_snapshot_fault_boundaries_preserve_committed_event_outcomes(
    tmp_path, monkeypatch, point
):
    import os
    from pathlib import Path

    from millrace.substrate.runner_session_events import public_snapshot_path

    writer = _writer(tmp_path)
    writer.record_progress({"n": 1}, observed_at=1, replay_key="one")
    capture = public_snapshot_path(tmp_path / "session-events.sqlite3")
    original_unlink, original_replace = Path.unlink, os.replace
    publish = writer.store._publish_snapshot
    connection = writer.store._connection
    if point == "before_invalidation":

        def fail_unlink(path, *args, **kwargs):
            if path == capture:
                raise OSError("invalidation fault")
            return original_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", fail_unlink)
        with pytest.raises(OSError):
            writer.record_progress({"n": 2}, observed_at=2, replay_key="two")
        expected = 1
    elif point == "after_invalidation_before_commit":

        class CommitFailure:
            def __getattr__(self, name):
                return getattr(connection, name)

            def commit(self):
                raise RuntimeError("interrupted before commit")

        writer.store._connection = CommitFailure()
        with pytest.raises(RuntimeError):
            writer.record_progress({"n": 2}, observed_at=2, replay_key="two")
        expected = 1
        assert not capture.exists()
    elif point == "after_commit":

        def interrupted():
            raise RuntimeError("simulated interruption after commit")

        monkeypatch.setattr(writer.store, "_publish_snapshot", interrupted)
        with pytest.raises(RuntimeError):
            writer.record_progress({"n": 2}, observed_at=2, replay_key="two")
        expected = 2
        assert not capture.exists()
    else:

        def after_replace(*args):
            original_replace(*args)
            raise OSError("replacement completed but delivery failed")

        monkeypatch.setattr(os, "replace", after_replace)
        writer.record_progress({"n": 2}, observed_at=2, replay_key="two")
        expected = 2
        assert not capture.exists()
    assert (
        writer.store.read(
            "run-1", session_id="session-1", after_sequence=0
        ).last_sequence
        == expected
    )
    monkeypatch.setattr(Path, "unlink", original_unlink)
    monkeypatch.setattr(os, "replace", original_replace)
    monkeypatch.setattr(writer.store, "_publish_snapshot", publish)
    writer.store._connection = connection
    writer.record_progress({"n": 3}, observed_at=3, replay_key="three")
    reader = RunnerSessionEventStore.open_readonly(tmp_path / "session-events.sqlite3")
    assert (
        reader.read("run-1", session_id="session-1", after_sequence=0).last_sequence
        == expected + 1
    )
    writer.store.close()


def test_snapshot_publication_lock_duration_is_bounded(tmp_path, capsys):
    import time

    writer = _writer(tmp_path)
    durations = []
    publish = writer.store._publish_snapshot

    def measured():
        start = time.monotonic()
        publish()
        durations.append(time.monotonic() - start)

    writer.store._publish_snapshot = measured
    for n in range(32):
        writer.record_progress({"n": n}, observed_at=n, replay_key=str(n))
    assert max(durations) < 1.0
    with capsys.disabled():
        print(
            {
                "snapshot_publications": len(durations),
                "maximum_capture_publication_seconds": max(durations),
                "total_seconds": sum(durations),
            }
        )
    writer.store.close()
