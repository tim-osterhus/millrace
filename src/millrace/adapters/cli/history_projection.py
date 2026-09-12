"""Exact retained session history, observed separately from durable control truth."""

from __future__ import annotations

import sqlite3
import time
from typing import Any, cast

from millrace.adapters.cli.output import json_ready
from millrace.contracts.public_projections import decode_cursor, encode_cursor, wire
from millrace.substrate.runner_session_events import (
    RunnerSessionEventStore,
    runner_session_event_store_path,
)


def runner_history(
    runtime: Any,
    state: Any,
    identity: dict[str, Any],
    namespace: object,
    session_projection: Any,
) -> dict[str, Any]:
    run_id = str(getattr(namespace, "run_id"))
    run = state.runs.get(run_id)
    if run is None:
        raise ValueError("run_not_found")
    after = getattr(namespace, "after_sequence", 0)
    cursor = getattr(namespace, "cursor", None)
    requested_session = getattr(namespace, "session_id", None)
    scope = None if cursor is None else decode_cursor(cursor)
    if scope is not None:
        if set(scope) != {
            "store_epoch",
            "run_id",
            "session_id",
            "dispatch_generation",
            "session_fence",
            "after_sequence",
        }:
            raise ValueError("cursor_scope_mismatch")
        if scope["store_epoch"] != identity["store_epoch"] or scope["run_id"] != run_id:
            raise ValueError("cursor_scope_mismatch")
        if requested_session is not None and requested_session != scope["session_id"]:
            raise ValueError("cursor_scope_mismatch")
        if after != 0 and after != scope["after_sequence"]:
            raise ValueError("cursor_scope_mismatch")
        requested_session = scope["session_id"]
        after = scope["after_sequence"]
    if type(after) is not int or after < 0:
        raise ValueError("invalid_after_sequence")
    session_id = (
        requested_session if requested_session is not None else run.current_session_id
    )
    session = state.runner_sessions.get(session_id)
    if session is None:
        if session_id is not None or after != 0:
            raise ValueError("cursor_scope_mismatch")
        return {
            "run_id": run_id,
            "history_status": "not_started",
            "events": [],
            "gap": None,
            "scope": None,
            "next_cursor": None,
            "has_more": False,
            "last_sequence": None,
            "earliest_retained_sequence": None,
            "durable_final": None,
            "runner_session": None,
        }
    exact = {
        "store_epoch": identity["store_epoch"],
        "run_id": run_id,
        "session_id": session.session_id,
        "dispatch_generation": session.dispatch_generation,
        "session_fence": session.session_fencing_token,
    }
    if session.run_id != run_id or (
        scope is not None and any(scope[key] != value for key, value in exact.items())
    ):
        raise ValueError("cursor_scope_mismatch")
    limit = getattr(namespace, "page_size", 50)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("invalid_page_size")
    data: dict[str, Any] = {
        "run_id": run_id,
        "history_status": "history_unavailable",
        "events": [],
        "gap": {
            "after_sequence": after,
            "resumes_at_sequence": None,
            "reason": "history_unavailable",
        },
        "scope": exact,
        "after_sequence": after,
        "last_sequence": None,
        "earliest_retained_sequence": None,
        "has_more": False,
        "next_after_sequence": after,
        "next_cursor": None,
        "consistency": "separate_sidecar_snapshot_non_authoritative",
        "runner_session": session_projection(runtime, state, session),
        "durable_final": session_projection(runtime, state, session),
        "restart_guidance": "fresh_durable_read; retain_exact_session_scope",
        "capture": None,
        "backing_store": {
            "presence": "unknown",
            "validation": "not_checked",
            "health": "unknown",
        },
    }
    store = None
    try:
        event_path = runner_session_event_store_path(runtime.paths.db_path)
        with event_path.open("rb") as backing:
            data["backing_store"]["presence"] = "present"
            if backing.read(16) != b"SQLite format 3\x00":
                data["backing_store"].update(
                    validation="invalid_header", health="corrupt"
                )
                raise ValueError("history_corrupt")
        data["backing_store"] = {
            "presence": "present",
            "validation": "header_only",
            "health": "unknown",
        }
        store = RunnerSessionEventStore.open_readonly(event_path)
        data["capture"] = {
            "captured_at_ns": store.captured_at_ns,
            "age_seconds": (time.time_ns() - store.captured_at_ns) / 1_000_000_000,
            "current_backing_health_proven": False,
            "source": "writer_published_event_snapshot",
            "consistency": "committed_sidecar_capture",
            "durable_source_revision_applies": False,
        }
        retained = store.stream_scope(session.session_id)
        if retained is None:
            return data
        if (
            retained["run_id"] != run_id
            or retained["dispatch_generation"] != session.dispatch_generation
        ):
            raise ValueError("cursor_scope_mismatch")
        last = retained["last_sequence"]
        if type(last) is not int or last < 0:
            raise ValueError("history_corrupt")
        if after > last:
            raise ValueError("cursor_ahead")
        page = store.read(
            run_id, session_id=session.session_id, after_sequence=after, limit=limit
        )
        events: list[dict[str, Any]] = []
        for event in page.events:
            if (
                event.session_id != session.session_id
                or event.dispatch_generation != session.dispatch_generation
                or event.run_id != run_id
            ):
                raise ValueError("history_corrupt")
            payload = cast(dict[str, Any], dict(event.payload()))
            # Telemetry payloads may contain prompts or arbitrary tool text. Preserve
            # event identity/policy and mark the omitted payload, including its digest.
            from millrace.contracts.public_projections import digest

            payload["bounded_payload"] = {
                "availability": "redacted",
                "digest": digest(json_ready(event.bounded_payload)),
            }
            payload["public_redaction_policy_id"] = "core-public-read-omit-v1"
            payload["truncation_metadata"] = {
                "source_digest": digest(json_ready(event.truncation_metadata)),
                "payload_omitted": True,
            }
            if len(wire(payload)) > 16384:
                raise ValueError("projection_item_too_large")
            if len(wire({**data, "events": [*events, payload]})) > 120 * 1024:
                break
            events.append(payload)
        next_after = after if not events else events[-1]["sequence"]
        gap = json_ready(page.gap)
        has_more = bool(events) and next_after < last
        data.update(
            events=events,
            history_status="history_gap" if gap is not None else "available",
            gap=gap,
            last_sequence=last,
            earliest_retained_sequence=retained["earliest_retained_sequence"],
            next_after_sequence=next_after,
            has_more=has_more,
            next_cursor=encode_cursor({**exact, "after_sequence": next_after})
            if has_more
            else None,
        )
    except FileNotFoundError:
        if data["backing_store"]["presence"] == "unknown":
            data["backing_store"]["presence"] = "missing"
    except (sqlite3.Error, OSError):
        data["history_status"] = "history_corrupt"
        data["gap"]["reason"] = "history_corrupt"
    except ValueError as exc:
        if str(exc) in {
            "cursor_scope_mismatch",
            "cursor_ahead",
            "projection_item_too_large",
        }:
            raise
        data["history_status"] = "history_corrupt"
        data["gap"]["reason"] = "history_corrupt"
    finally:
        if store is not None:
            store.close()
    return data
