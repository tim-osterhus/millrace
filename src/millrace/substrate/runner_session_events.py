"""Separately versioned bounded storage for non-authoritative session events."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Final, Protocol

from millrace.contracts.compiled_plan import AuthorityValue
from millrace.contracts.runner_events import (
    RUNNER_SESSION_EVENT_KINDS,
    RUNNER_SESSION_EVENT_MAX_PAYLOAD_BYTES,
    RunnerSessionEvent,
)

RUNNER_SESSION_EVENT_STORE_SCHEMA_VERSION: Final = 1
RUNNER_SESSION_EVENT_MAX_BYTES: Final = 20 * 1024
RUNNER_SESSION_EVENT_REPLAY_KEY_MAX_BYTES: Final = 512
RUNNER_SESSION_EVENT_STORE_MAX_RECORDS: Final = 256
RUNNER_SESSION_EVENT_STORE_MAX_BYTES: Final = 1024 * 1024
RUNNER_SESSION_EVENT_STORE_MAX_STREAMS: Final = 128
RUNNER_SESSION_EVENT_READ_MAX_RECORDS: Final = 100
RUNNER_SESSION_EVENT_READ_MAX_BYTES: Final = 128 * 1024
RUNNER_SESSION_EVENT_UPDATE_RATE_PER_SECOND: Final = 20
_PROVIDER_EVENT_KINDS = frozenset(
    {"runner_progress", "tool_activity", "usage_update", "diagnostic"}
)
_EXPECTED_COLUMNS = {
    "session_event_metadata": ("singleton", "schema_version"),
    "session_event_sequences": (
        "session_id",
        "run_id",
        "dispatch_generation",
        "last_sequence",
        "terminal_recorded",
        "admission_second",
        "admission_count",
    ),
    "session_events": (
        "event_id",
        "replay_key",
        "session_id",
        "run_id",
        "dispatch_generation",
        "sequence",
        "kind",
        "observed_at",
        "payload_json",
        "redaction_policy_id",
        "truncation_json",
        "byte_size",
    ),
}


class _RedactionPolicy(Protocol):
    @property
    def policy_id(self) -> str: ...

    @property
    def secret_tokens(self) -> tuple[str, ...]: ...

    def redact_text(self, value: str) -> str: ...

    def redact_authority_value(self, value: object) -> AuthorityValue: ...


def runner_session_event_store_path(runtime_db_path: str | Path) -> Path:
    path = Path(runtime_db_path)
    return path.with_name(f"{path.name}.runner-session-events.sqlite3")


def _admission_second() -> int:
    return time.time_ns() // 1_000_000_000


@dataclass(frozen=True, slots=True)
class RunnerSessionEventGap:
    after_sequence: int
    resumes_at_sequence: int | None
    reason: str


@dataclass(frozen=True, slots=True)
class RunnerSessionEventPage:
    events: tuple[RunnerSessionEvent, ...]
    gap: RunnerSessionEventGap | None
    last_sequence: int
    stream_found: bool


@dataclass(frozen=True, slots=True)
class RunnerSessionEventStoreStats:
    retained_records: int
    retained_bytes: int
    retained_streams: int
    last_sequence: int


class RunnerSessionEventStore:
    """Synchronous WAL store; readers never participate in session execution."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @classmethod
    def initialize(cls, path: str | Path) -> RunnerSessionEventStore:
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(db_path, timeout=0.1)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS session_event_metadata (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS session_event_sequences (
                session_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                dispatch_generation INTEGER NOT NULL,
                last_sequence INTEGER NOT NULL,
                terminal_recorded INTEGER NOT NULL CHECK (
                    terminal_recorded IN (0, 1)
                ),
                admission_second INTEGER NOT NULL,
                admission_count INTEGER NOT NULL CHECK (admission_count >= 0)
            );
            CREATE TABLE IF NOT EXISTS session_events (
                event_id TEXT PRIMARY KEY,
                replay_key TEXT NOT NULL,
                session_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                dispatch_generation INTEGER NOT NULL,
                sequence INTEGER NOT NULL,
                kind TEXT NOT NULL,
                observed_at INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                redaction_policy_id TEXT NOT NULL,
                truncation_json TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                UNIQUE (session_id, replay_key),
                UNIQUE (session_id, sequence)
            );
            CREATE INDEX IF NOT EXISTS session_events_run_sequence
            ON session_events (run_id, sequence);
            """
        )
        row = connection.execute(
            "SELECT schema_version FROM session_event_metadata WHERE singleton = 1"
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO session_event_metadata VALUES (1, ?)",
                (RUNNER_SESSION_EVENT_STORE_SCHEMA_VERSION,),
            )
        elif row[0] != RUNNER_SESSION_EVENT_STORE_SCHEMA_VERSION:
            connection.close()
            raise ValueError("unsupported runner-session event store schema")
        for table_name, expected_columns in _EXPECTED_COLUMNS.items():
            columns = tuple(
                str(column[1])
                for column in connection.execute(
                    "SELECT cid, name FROM pragma_table_info(?)",
                    (table_name,),
                ).fetchall()
            )
            if columns != expected_columns:
                connection.close()
                raise ValueError("invalid runner-session event store schema shape")
        connection.commit()
        return cls(connection)

    @classmethod
    def open(cls, path: str | Path) -> RunnerSessionEventStore:
        db_path = Path(path)
        if not db_path.is_file():
            raise FileNotFoundError(db_path)
        store = cls.initialize(db_path)
        return store

    def append(
        self,
        *,
        session_id: str,
        run_id: str,
        dispatch_generation: int,
        kind: str,
        observed_at: int,
        bounded_payload: Mapping[str, AuthorityValue],
        redaction_policy_id: str,
        truncation_metadata: Mapping[str, AuthorityValue],
        replay_key: str,
    ) -> RunnerSessionEvent:
        if (
            not isinstance(replay_key, str)
            or not replay_key.startswith("sha256:")
            or len(replay_key) != 71
            or any(
                character not in "0123456789abcdef"
                for character in replay_key.removeprefix("sha256:")
            )
        ):
            raise ValueError("replay_key must be an opaque sha256 identity")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self._connection.execute(
                "SELECT * FROM session_events "
                "WHERE session_id = ? AND replay_key = ?",
                (session_id, replay_key),
            ).fetchone()
            if existing is not None:
                replayed = _event_from_row(existing)
                self._validate_replay(
                    replayed,
                    run_id=run_id,
                    dispatch_generation=dispatch_generation,
                    kind=kind,
                    observed_at=observed_at,
                    bounded_payload=bounded_payload,
                    redaction_policy_id=redaction_policy_id,
                    truncation_metadata=truncation_metadata,
                )
                self._connection.commit()
                return replayed
            sequence_row = self._connection.execute(
                "SELECT run_id, dispatch_generation, last_sequence, "
                "terminal_recorded FROM session_event_sequences "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if sequence_row is None:
                self._make_stream_room()
                sequence = 1
                self._connection.execute(
                    "INSERT INTO session_event_sequences "
                    "VALUES (?, ?, ?, ?, 0, ?, 0)",
                    (
                        session_id,
                        run_id,
                        dispatch_generation,
                        sequence,
                        _admission_second(),
                    ),
                )
            elif sequence_row[:2] != (run_id, dispatch_generation):
                raise ValueError("session event authority changed")
            else:
                if bool(sequence_row[3]):
                    raise ValueError("terminal runner-session event stream is closed")
                sequence = int(sequence_row[2]) + 1
                self._connection.execute(
                    "UPDATE session_event_sequences SET last_sequence = ? "
                    "WHERE session_id = ?",
                    (sequence, session_id),
                )
            drop_progress = self._enforce_update_rate(
                session_id=session_id,
                kind=kind,
            )
            event_id = _event_id(session_id, dispatch_generation, replay_key)
            event = RunnerSessionEvent(
                event_id=event_id,
                session_id=session_id,
                run_id=run_id,
                dispatch_generation=dispatch_generation,
                sequence=sequence,
                kind=kind,
                observed_at=observed_at,
                bounded_payload=bounded_payload,
                redaction_policy_id=redaction_policy_id,
                truncation_metadata=truncation_metadata,
            )
            payload_json = _canonical_json(event.bounded_payload)
            truncation_json = _canonical_json(event.truncation_metadata)
            byte_size = len(_canonical_json(event.payload()).encode()) + len(
                replay_key.encode()
            )
            if byte_size > RUNNER_SESSION_EVENT_MAX_BYTES:
                raise ValueError("runner-session event exceeds the record ceiling")
            self._connection.execute(
                "INSERT INTO session_events VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    replay_key,
                    session_id,
                    run_id,
                    dispatch_generation,
                    sequence,
                    kind,
                    observed_at,
                    payload_json,
                    redaction_policy_id,
                    truncation_json,
                    byte_size,
                ),
            )
            if kind == "session_terminal":
                self._connection.execute(
                    "UPDATE session_event_sequences SET terminal_recorded = 1 "
                    "WHERE session_id = ?",
                    (session_id,),
                )
            if drop_progress:
                self._drop_oldest_progress(session_id)
            self._enforce_bounds()
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return event

    def read(
        self,
        run_id: str,
        *,
        after_sequence: int,
        limit: int = RUNNER_SESSION_EVENT_READ_MAX_RECORDS,
        session_id: str | None = None,
    ) -> RunnerSessionEventPage:
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("after_sequence must be a nonnegative integer")
        bounded_limit = min(max(int(limit), 1), RUNNER_SESSION_EVENT_READ_MAX_RECORDS)
        authority_clause = "" if session_id is None else " AND session_id = ?"
        authority_values: tuple[object, ...] = (
            (run_id,) if session_id is None else (run_id, session_id)
        )
        candidate_rows = self._connection.execute(
            "SELECT * FROM session_events WHERE run_id = ?"
            f"{authority_clause} AND sequence > ? ORDER BY sequence LIMIT ?",
            (*authority_values, after_sequence, bounded_limit),
        ).fetchall()
        rows: list[tuple[object, ...]] = []
        page_bytes = 0
        for row in candidate_rows:
            row_bytes = int(str(row[11]))
            if rows and page_bytes + row_bytes > RUNNER_SESSION_EVENT_READ_MAX_BYTES:
                break
            rows.append(row)
            page_bytes += row_bytes
        if session_id is None:
            last_row = self._connection.execute(
                "SELECT MAX(last_sequence) FROM session_event_sequences "
                "WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        else:
            last_row = self._connection.execute(
                "SELECT last_sequence FROM session_event_sequences "
                "WHERE run_id = ? AND session_id = ?",
                (run_id, session_id),
            ).fetchone()
        last = None if last_row is None else last_row[0]
        gap = None
        expected = after_sequence + 1
        for row in rows:
            sequence = int(str(row[5]))
            if sequence > expected:
                gap = RunnerSessionEventGap(
                    expected - 1,
                    sequence,
                    "compacted",
                )
                break
            expected = sequence + 1
        if (
            gap is None
            and not rows
            and last is not None
            and int(last) > after_sequence
        ):
            gap = RunnerSessionEventGap(
                after_sequence,
                None,
                "history_unavailable",
            )
        return RunnerSessionEventPage(
            tuple(_event_from_row(row) for row in rows),
            gap,
            0 if last is None else int(last),
            last is not None,
        )

    def stats(self) -> RunnerSessionEventStoreStats:
        count, retained_bytes = self._connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(byte_size), 0) FROM session_events"
        ).fetchone()
        last = self._connection.execute(
            "SELECT COALESCE(MAX(last_sequence), 0) FROM session_event_sequences"
        ).fetchone()[0]
        streams = self._connection.execute(
            "SELECT COUNT(*) FROM session_event_sequences"
        ).fetchone()[0]
        return RunnerSessionEventStoreStats(
            int(count),
            int(retained_bytes),
            int(streams),
            int(last),
        )

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _validate_replay(
        replayed: RunnerSessionEvent,
        *,
        run_id: str,
        dispatch_generation: int,
        kind: str,
        observed_at: int,
        bounded_payload: Mapping[str, AuthorityValue],
        redaction_policy_id: str,
        truncation_metadata: Mapping[str, AuthorityValue],
    ) -> None:
        if (
            replayed.run_id != run_id
            or replayed.dispatch_generation != dispatch_generation
            or replayed.kind != kind
            or replayed.observed_at != observed_at
            or _canonical_json(replayed.bounded_payload)
            != _canonical_json(bounded_payload)
            or replayed.redaction_policy_id != redaction_policy_id
            or _canonical_json(replayed.truncation_metadata)
            != _canonical_json(truncation_metadata)
        ):
            raise ValueError("runner-session event replay contradicts prior event")

    def _enforce_update_rate(
        self,
        *,
        session_id: str,
        kind: str,
    ) -> bool:
        if kind not in _PROVIDER_EVENT_KINDS:
            return False
        now_second = _admission_second()
        rate_second, rate_count = self._connection.execute(
            "SELECT admission_second, admission_count "
            "FROM session_event_sequences WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        admitted = int(rate_count) if int(rate_second) == now_second else 0
        if admitted >= RUNNER_SESSION_EVENT_UPDATE_RATE_PER_SECOND:
            if kind != "runner_progress":
                raise ValueError(
                    "runner-session event update-rate ceiling reached"
                )
            return True
        self._connection.execute(
            "UPDATE session_event_sequences "
            "SET admission_second = ?, admission_count = ? "
            "WHERE session_id = ?",
            (now_second, admitted + 1, session_id),
        )
        return False

    def _enforce_bounds(self) -> None:
        while True:
            count, retained_bytes = self._connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(byte_size), 0) FROM session_events"
            ).fetchone()
            if (
                count <= RUNNER_SESSION_EVENT_STORE_MAX_RECORDS
                and retained_bytes <= RUNNER_SESSION_EVENT_STORE_MAX_BYTES
            ):
                return
            victim = self._connection.execute(
                "SELECT event_id FROM session_events "
                "WHERE kind IN ('runner_progress', 'tool_activity', "
                "'usage_update', 'diagnostic') "
                "ORDER BY observed_at, sequence LIMIT 1"
            ).fetchone()
            if victim is None:
                if not self._evict_oldest_closed_stream():
                    raise ValueError(
                        "runner-session event store hard ceiling reached"
                    )
            else:
                self._connection.execute(
                    "DELETE FROM session_events WHERE event_id = ?",
                    victim,
                )

    def _drop_oldest_progress(self, session_id: str) -> None:
        victim = self._connection.execute(
            "SELECT event_id FROM session_events "
            "WHERE session_id = ? AND kind = 'runner_progress' "
            "ORDER BY sequence LIMIT 1",
            (session_id,),
        ).fetchone()
        if victim is not None:
            self._connection.execute(
                "DELETE FROM session_events WHERE event_id = ?",
                victim,
            )

    def _evict_oldest_closed_stream(self) -> bool:
        victim = self._connection.execute(
            "SELECT sequence.session_id FROM session_event_sequences AS sequence "
            "LEFT JOIN session_events AS event "
            "ON event.session_id = sequence.session_id "
            "WHERE sequence.terminal_recorded = 1 GROUP BY sequence.session_id "
            "ORDER BY COALESCE(MIN(event.observed_at), -1), "
            "COALESCE(MIN(event.sequence), -1) LIMIT 1"
        ).fetchone()
        if victim is None:
            return False
        self._connection.execute(
            "DELETE FROM session_events WHERE session_id = ?",
            victim,
        )
        self._connection.execute(
            "DELETE FROM session_event_sequences WHERE session_id = ?",
            victim,
        )
        return True

    def _make_stream_room(self) -> None:
        stream_count = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM session_event_sequences"
            ).fetchone()[0]
        )
        if stream_count < RUNNER_SESSION_EVENT_STORE_MAX_STREAMS:
            return
        if not self._evict_oldest_closed_stream():
            raise ValueError("runner-session event stream ceiling reached")


class RunnerSessionEventWriter:
    """Redacts and bounds producer input before it reaches the event store."""

    def __init__(
        self,
        store: RunnerSessionEventStore,
        *,
        session_id: str,
        run_id: str,
        dispatch_generation: int,
        redaction_policy: _RedactionPolicy,
    ) -> None:
        self.store = store
        self.session_id = session_id
        self.run_id = run_id
        self.dispatch_generation = dispatch_generation
        self.redaction_policy = redaction_policy
        self._redactor = _StreamingRedactor(redaction_policy)

    def record(
        self,
        kind: str,
        payload: Mapping[str, object],
        *,
        observed_at: int,
        replay_key: str,
    ) -> RunnerSessionEvent:
        if kind not in RUNNER_SESSION_EVENT_KINDS:
            raise ValueError("unsupported runner-session event kind")
        redacted = self.redaction_policy.redact_authority_value(payload)
        if not isinstance(redacted, Mapping):
            raise TypeError("event payload must redact to a mapping")
        bounded, truncation = _bound_payload(redacted)
        if not isinstance(replay_key, str):
            raise TypeError("replay_key must be a string")
        if not replay_key.strip():
            raise ValueError("replay_key must be nonblank")
        replay_key_bytes = replay_key.encode()
        if len(replay_key_bytes) > RUNNER_SESSION_EVENT_REPLAY_KEY_MAX_BYTES:
            raise ValueError("replay_key exceeds the event replay-key ceiling")
        replay_identity = (
            "sha256:"
            + sha256(
                b"millrace.runner-session-event.replay-key.v1\0"
                + replay_key_bytes
            ).hexdigest()
        )
        return self.store.append(
            session_id=self.session_id,
            run_id=self.run_id,
            dispatch_generation=self.dispatch_generation,
            kind=kind,
            observed_at=observed_at,
            bounded_payload=bounded,
            redaction_policy_id=self.redaction_policy.redact_text(
                self.redaction_policy.policy_id
            ),
            truncation_metadata=truncation,
            replay_key=replay_identity,
        )

    def record_progress(
        self,
        payload: Mapping[str, object],
        *,
        observed_at: int,
        replay_key: str,
    ) -> RunnerSessionEvent:
        return self.record(
            "runner_progress",
            payload,
            observed_at=observed_at,
            replay_key=replay_key,
        )

    def record_progress_chunk(
        self,
        chunk: str,
        *,
        observed_at: int,
        final: bool = False,
    ) -> RunnerSessionEvent | None:
        redacted = self._redactor.feed(chunk, final=final)
        if not redacted:
            return None
        digest = sha256(
            f"{observed_at}:{redacted}:{final}".encode()
        ).hexdigest()
        return self.record_progress(
            {"text": redacted, "final": final},
            observed_at=observed_at,
            replay_key=f"progress-chunk:{digest}",
        )


class _StreamingRedactor:
    def __init__(self, policy: _RedactionPolicy) -> None:
        self._tokens = policy.secret_tokens
        self._buffer = ""
        self._max_token = max((len(token) for token in self._tokens), default=0)

    def feed(self, chunk: str, *, final: bool) -> str:
        if not isinstance(chunk, str):
            raise TypeError("chunk must be a string")
        self._buffer += chunk
        if not self._tokens:
            immediate_output, self._buffer = self._buffer, ""
            return immediate_output
        safe_end = len(self._buffer) if final else max(
            0,
            len(self._buffer) - self._max_token + 1,
        )
        parts: list[str] = []
        index = 0
        while index < safe_end:
            match = next(
                (
                    token
                    for token in self._tokens
                    if self._buffer.startswith(token, index)
                ),
                None,
            )
            if match is None:
                parts.append(self._buffer[index])
                index += 1
            else:
                parts.append("[REDACTED]")
                index += len(match)
        self._buffer = self._buffer[index:]
        return "".join(parts)


def _bound_payload(
    payload: Mapping[str, AuthorityValue],
) -> tuple[Mapping[str, AuthorityValue], Mapping[str, AuthorityValue]]:
    raw = _canonical_json(payload).encode("utf-8")
    if len(raw) <= RUNNER_SESSION_EVENT_MAX_PAYLOAD_BYTES:
        return payload, {"truncated": False, "original_bytes": len(raw)}
    digest = sha256(raw).hexdigest()
    preview = raw[: RUNNER_SESSION_EVENT_MAX_PAYLOAD_BYTES // 2].decode(
        "utf-8",
        errors="ignore",
    )
    return (
        {"preview": preview, "sha256": digest},
        {"truncated": True, "original_bytes": len(raw)},
    )


def _event_id(session_id: str, dispatch_generation: int, replay_key: str) -> str:
    digest = sha256(
        f"{session_id}\0{dispatch_generation}\0{replay_key}".encode()
    ).hexdigest()
    return f"runner-session-event:{digest}"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )


def _json_default(value: object) -> object:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _event_from_row(row: tuple[object, ...]) -> RunnerSessionEvent:
    return RunnerSessionEvent(
        event_id=str(row[0]),
        session_id=str(row[2]),
        run_id=str(row[3]),
        dispatch_generation=int(str(row[4])),
        sequence=int(str(row[5])),
        kind=str(row[6]),
        observed_at=int(str(row[7])),
        bounded_payload=json.loads(str(row[8])),
        redaction_policy_id=str(row[9]),
        truncation_metadata=json.loads(str(row[10])),
    )


__all__ = (
    "RUNNER_SESSION_EVENT_MAX_BYTES",
    "RUNNER_SESSION_EVENT_READ_MAX_RECORDS",
    "RUNNER_SESSION_EVENT_REPLAY_KEY_MAX_BYTES",
    "RUNNER_SESSION_EVENT_READ_MAX_BYTES",
    "RUNNER_SESSION_EVENT_STORE_MAX_BYTES",
    "RUNNER_SESSION_EVENT_STORE_MAX_RECORDS",
    "RUNNER_SESSION_EVENT_STORE_MAX_STREAMS",
    "RUNNER_SESSION_EVENT_STORE_SCHEMA_VERSION",
    "RUNNER_SESSION_EVENT_UPDATE_RATE_PER_SECOND",
    "RunnerSessionEventGap",
    "RunnerSessionEventPage",
    "RunnerSessionEventStore",
    "RunnerSessionEventStoreStats",
    "RunnerSessionEventWriter",
    "runner_session_event_store_path",
)
