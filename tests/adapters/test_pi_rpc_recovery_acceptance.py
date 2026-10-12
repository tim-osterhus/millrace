"""Synthetic recovered-result evidence; installed/native qualification is separate."""

import copy
import json
import queue
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from millrace.adapters.pi_rpc_client import PiLifecycle, PiRpcClient, PiRpcError
from millrace.adapters.pi_rpc_groups import GroupProfile
from millrace.adapters.runner_contract import AdapterTokenUsage
from tests.adapters.test_pi_rpc_recovery import (
    compaction_end,
    compaction_start,
    continued_run,
    continued_tool_run,
    failed_run,
    retry_start,
)
from tests.support.pi_rpc import LIMITS, MODEL, lifecycle


def parser(events, *, enabled=True, limit=1000):
    value = PiLifecycle(
        MODEL, "prompt", recovery_accounting=enabled, recovery_limit=limit
    )
    for event in events:
        value.event(copy.deepcopy(event))
    if (
        events
        and events[-1]["type"] == "agent_settled"
        and any(e["type"] == "compaction_end" for e in events)
        and not any(e["type"] == "auto_retry_start" for e in events)
    ):
        value.bind_recovery_entries(with_entries(events)[1])
    return value


def threshold_events(**flags):
    events = lifecycle("prompt", tool=("read", {"path": "opaque"}), result="text")
    events[11:11] = [compaction_start(), compaction_end(**flags)]
    if "result" in events[12]:
        events[12]["result"].update(
            firstKeptEntryId="entry-2",
            tokensBefore=123,
            estimatedTokensAfter=20,
            usage=dict(input=10, output=2, cacheRead=0, cacheWrite=0, totalTokens=12),
            details=dict(readFiles=[], modifiedFiles=[]),
        )
    return events


def retry_events(*, progressive=False, tool=False):
    events = failed_run() + [retry_start()]
    if progressive:
        more = continued_run(stop="error")[:-1]
        more[-1]["willRetry"] = True
        events += more + [retry_start(2)]
    events += (
        continued_tool_run(stop="stop", retry_end=True) + [dict(type="agent_settled")]
        if tool
        else continued_run(retry_end=True, attempt=2 if progressive else 1)
    )
    return with_entries(events)


def with_entries(events):
    """One active transcript, exact native null edits, no trusted wrapper facts."""
    entries = [
        dict(
            type="model_change",
            id="model",
            parentId=None,
            timestamp="2026-10-07T00:00:00.000Z",
            provider=MODEL["provider"],
            modelId=MODEL["id"],
        ),
        dict(
            type="thinking_level_change",
            id="thinking",
            parentId="model",
            timestamp="2026-10-07T00:00:00.000Z",
            thinkingLevel="xhigh",
        ),
    ]
    wire = []
    leaf, failed = "thinking", None
    for event in copy.deepcopy(events):
        wire.append(event)
        if event["type"] == "message_end":
            message = event["message"]
            ident = "entry-" + str(len(entries))
            entries.append(
                dict(
                    type="message",
                    id=ident,
                    parentId=leaf,
                    timestamp="2026-10-07T00:00:00.000Z",
                    message=copy.deepcopy(message),
                )
            )
            leaf = ident
            if message["role"] == "assistant" and message["stopReason"] == "error":
                failed = ident
        elif event["type"] == "auto_retry_start":
            ident = "edit-" + str(len(entries))
            entry = dict(
                type="context_edit",
                id=ident,
                parentId=leaf,
                timestamp="2026-10-07T00:00:00.000Z",
                targetId=failed,
                replacement=None,
            )
            wire.append(dict(type="entry_appended", entry=copy.deepcopy(entry)))
            entries.append(entry)
            leaf = ident
        elif event["type"] == "compaction_end" and "result" in event:
            result = event["result"]
            result.setdefault("firstKeptEntryId", entries[2]["id"])
            result.setdefault("tokensBefore", 123)
            ident = "compact-" + str(len(entries))
            entries.append(
                dict(
                    type="compaction",
                    id=ident,
                    parentId=leaf,
                    timestamp="2026-10-07T00:00:00.000Z",
                    fromHook=False,
                    **{
                        k: result[k]
                        for k in (
                            "summary",
                            "firstKeptEntryId",
                            "tokensBefore",
                            "usage",
                            "details",
                        )
                        if k in result
                    },
                )
            )
            leaf = ident
    return wire, dict(entries=entries, leafId=leaf)


def closed_client(value, *, usage=True):
    client = object.__new__(PiRpcClient)
    client.lifecycle = value
    client.prompt = "prompt"
    client.process = SimpleNamespace(returncode=0, poll=lambda: 0, stdin=None)
    client.closed = True
    client.eof = dict(stdout=True, stderr=True, bridge=True)
    client.errors, client.pending, client.threads = [], {}, []
    client.supervisor = SimpleNamespace(finish=lambda: True)
    client.groups = SimpleNamespace(finish=lambda *args: True)
    client.deadline = time.monotonic() + 10
    client.cancel_requested = threading.Event()
    client.usage = AdapterTokenUsage(400, 40, 440) if usage else None
    client.cleanup_draining = False
    client.close_failed = False
    return client


def test_threshold_qualified_only_after_same_run_successful_tool_aftermath():
    events = threshold_events()
    value = parser(events[:-1])
    assert not value.recovery_qualified()
    value.event(events[-1])
    assert not value.recovery_qualified()
    value.bind_recovery_entries(with_entries(events)[1])
    assert value.recovery_qualified()
    assert not value.uncertain and value.auxiliary
    assert value.fallback_usage() is None and value.runs == 1


@pytest.mark.parametrize(
    "tool,progressive", [(False, False), (True, False), (False, True)]
)
def test_retry_requires_exact_persisted_omission_and_complete_aftermath(
    tool, progressive
):
    events, entries = retry_events(tool=tool, progressive=progressive)
    value = parser(events)
    assert not value.recovery_qualified()
    value.bind_recovery_entries(entries)
    assert value.recovery_qualified() and not value.uncertain
    assert value.fallback_usage() is None
    assert closed_client(value).close()


@pytest.mark.parametrize(
    "field,bad",
    [
        ("replacement", {}),
        ("targetId", "foreign"),
        ("id", ""),
        ("parentId", None),
        ("timestamp", "secret invalid date"),
        ("type", "message"),
        ("targetId", True),
    ],
)
def test_context_edit_corruption_refuses_forever(field, bad):
    events, entries = retry_events()
    edit = next(e for e in events if e["type"] == "entry_appended")
    edit["entry"][field] = bad
    value = parser(events)
    value.bind_recovery_entries(entries)
    assert not value.recovery_qualified()
    assert not closed_client(value).close()


@pytest.mark.parametrize(
    "corruption",
    [
        "missing",
        "duplicate",
        "message",
        "edit",
        "leaf",
        "cycle",
        "foreign_parent",
        "inactive",
        "target_role",
        "target_stop",
        "extra_keys",
        "typed_count",
    ],
)
def test_transcript_corruption_never_binds_owning_failure(corruption):
    events, entries = retry_events()
    rows = entries["entries"]
    edit = next(e for e in rows if e["type"] == "context_edit")
    target = next(e for e in rows if e["id"] == edit["targetId"])
    if corruption == "missing":
        rows.remove(edit)
    elif corruption == "duplicate":
        rows.append(copy.deepcopy(target))
    elif corruption == "message":
        target["message"]["content"] = [dict(type="text", text="foreign secret")]
    elif corruption == "edit":
        edit["timestamp"] = "2026-10-08T00:00:00.000Z"
    elif corruption == "leaf":
        entries["leafId"] = "foreign"
    elif corruption == "cycle":
        rows[0]["parentId"] = entries["leafId"]
    elif corruption == "foreign_parent":
        target["parentId"] = "foreign"
    elif corruption == "inactive":
        entries["leafId"] = rows[0]["id"]
    elif corruption == "target_role":
        target["message"]["role"] = "user"
    elif corruption == "target_stop":
        target["message"]["stopReason"] = "stop"
    elif corruption == "extra_keys":
        entries["sessionId"] = "foreign"
    else:
        entries["entries"] = True
    value = parser(events)
    value.bind_recovery_entries(entries)
    assert not value.recovery_qualified()
    # A later clean readback cannot repair a failed binding.
    value.bind_recovery_entries(retry_events()[1])
    assert not value.recovery_qualified()


@pytest.mark.parametrize("flags", [dict(failed=True), dict(aborted=True)])
def test_failed_threshold_is_sticky_after_later_valid_text(flags):
    value = parser(threshold_events(**flags))
    assert value.final_text() and not value.recovery_qualified()


@pytest.mark.parametrize("enabled", [False, True])
def test_parser_damage_cannot_be_repaired_by_later_valid_events(enabled):
    value = parser([], enabled=enabled)
    with pytest.raises(PiRpcError):
        value.event(dict(type="auto_retry_end", success=True, attempt=1))
    for event in threshold_events():
        value.event(event)
    assert not value.recovery_qualified()
    assert not closed_client(value).close()


@pytest.mark.parametrize(
    "cause",
    [
        "unqualified update",
        "unsupported tool",
        "unqualified tool result",
        "tool update",
        "unqualified queue event",
        "unqualified thinking event",
        "unqualified session event",
        "invalid observer channel",
        "process control observed",
        "failed invocation",
        "cancelled",
        "incomplete closure",
    ],
)
def test_qualified_recovery_preserves_each_unrelated_uncertainty(cause):
    value = parser(threshold_events())
    value.uncertain.add(cause)
    assert cause in value.uncertain
    assert not closed_client(value).close()


@pytest.mark.parametrize(
    "fault", ["usage", "pending", "reader", "deadline", "cancel", "group", "supervisor"]
)
def test_recovery_never_relaxes_closure_and_cannot_improve_on_repeated_close(fault):
    value = parser(threshold_events())
    client = closed_client(value)
    if fault == "usage":
        client.usage = None
    elif fault == "pending":
        client.pending = {"id": "get_entries"}
    elif fault == "reader":
        client.threads = [SimpleNamespace(is_alive=lambda: True)]
    elif fault == "deadline":
        client.deadline = time.monotonic() - 1
    elif fault == "cancel":
        client.cancel_requested.set()
    elif fault == "group":
        client.groups.finish = lambda *args: False
    else:
        client.supervisor.finish = lambda: False
    assert not client.close()
    client.usage = AdapterTokenUsage(400, 40, 440)
    client.pending = {}
    client.threads = []
    client.deadline = time.monotonic() + 10
    client.cancel_requested.clear()
    client.groups.finish = lambda *args: True
    client.supervisor.finish = lambda: True
    assert not client.close(), "failed execution closure is monotonic"


def test_missing_duplicate_and_unrelated_entry_events_remain_refused():
    for mutation in ("missing", "duplicate", "other"):
        events, entries = retry_events()
        index = next(i for i, e in enumerate(events) if e["type"] == "entry_appended")
        if mutation == "missing":
            events.pop(index)
        elif mutation == "duplicate":
            events.insert(index, copy.deepcopy(events[index]))
        else:
            events[index]["entry"]["type"] = "session_info"
        value = parser(events)
        value.bind_recovery_entries(entries)
        assert not value.recovery_qualified()


def test_bounded_ledger_never_evicts_old_failure_to_gain_acceptance():
    events, entries = retry_events(progressive=True)
    value = parser(events, limit=4)
    value.bind_recovery_entries(entries)
    assert not value.recovery_qualified()


@pytest.mark.parametrize(
    "supervised,platform", [(False, "linux"), (True, "darwin"), (True, "linux")]
)
def test_capability_is_derived_only_from_existing_linux_supervisor(
    monkeypatch, tmp_path, supervised, platform
):
    seen = []
    original = PiLifecycle.__init__

    def record(self, *args, **kwargs):
        seen.append(kwargs.get("recovery_accounting", False))
        original(self, *args, **kwargs)

    monkeypatch.setattr(PiLifecycle, "__init__", record)
    profile = GroupProfile(
        Path("/bridge"), "a" * 64, Path("/package"), "b" * 64, platform, supervised
    )
    with pytest.raises(PiRpcError):
        PiRpcClient(
            ("never-spawn",),
            tmp_path,
            {},
            LIMITS,
            MODEL,
            "prompt",
            0,
            group_profile=profile,
            deadline=0,
        )
    assert seen == [supervised and platform == "linux"]


def test_legacy_recovery_retains_original_labels_and_result_refusal():
    value = parser(threshold_events(), enabled=False)
    assert "intrinsic recovery" in value.uncertain
    assert not value.recovery_qualified()
    assert not closed_client(value).close()


def test_each_later_failure_needs_its_own_successful_cycle():
    events, entries = with_entries(
        failed_run()
        + [retry_start()]
        + continued_tool_run(retry_end=True)
        + [retry_start()]
        + continued_run(retry_end=True)
    )
    value = parser(events)
    value.bind_recovery_entries(entries)
    assert value.recovery_qualified()
    assert value.recovery_evidence.diagnostic()["retry_cycle_count"] == 2
    assert value.recovery_evidence.diagnostic()["assistant_failure_count"] == 2


def test_threshold_success_then_independent_retry_keeps_both_receipts():
    first = lifecycle("prompt", tool=("read", {"path": "opaque"}), result="text")
    second = lifecycle("prompt", tool=("read", {"path": "later"}), result="text")
    second = json.loads(json.dumps(second).replace("call-1", "call-2"))
    end = threshold_events()[12]
    events = first[:11] + [compaction_start(), end]
    events += [dict(type="turn_start")] + second[4:11]
    events += [dict(type="turn_start")] + failed_run()[4:]
    events[-1]["messages"] = [
        e["message"] for e in events if e["type"] == "message_end"
    ]
    events, entries = with_entries(
        events + [retry_start()] + continued_run(retry_end=True)
    )
    value = parser(events)
    value.bind_recovery_entries(entries)
    assert value.recovery_qualified()
    assert value.recovery_evidence.diagnostic()["threshold_cycle_count"] == 1
    assert value.recovery_evidence.diagnostic()["retry_cycle_count"] == 1


def test_threshold_then_separately_matched_retry_and_later_tool_aftermath():
    events = threshold_events()[:-1]
    last = next(e["message"] for e in reversed(events) if e["type"] == "message_end")
    last["stopReason"] = "error"
    events[-1]["willRetry"] = True
    resumed = json.loads(
        json.dumps(continued_tool_run(stop="stop", retry_end=True)).replace(
            "call-1", "call-2"
        )
    )
    events, entries = with_entries(
        events + [retry_start()] + resumed + [dict(type="agent_settled")]
    )
    value = parser(events)
    value.bind_recovery_entries(entries)
    # The resumed threshold turn itself ended in error, so retry cannot erase it.
    assert not value.recovery_qualified()


@pytest.mark.parametrize(
    "stamp",
    [
        "2026-10-07",
        "2026-10-07T00:00:00Z",
        "2026-10-07T00:00:00.000+00:00",
        "2026-10-07T00:00:00.000ZZ",
        "2026-02-30T00:00:00.000Z",
        "2026-10-07 00:00:00.000Z",
    ],
)
def test_only_source_utc_millisecond_timestamp_can_bind_retry(stamp):
    events, entries = retry_events()
    for event in events:
        if event["type"] == "entry_appended":
            event["entry"]["timestamp"] = stamp
    value = parser(events)
    value.bind_recovery_entries(entries)
    assert not value.recovery_qualified()


@pytest.mark.parametrize(
    "mutation", ["missing", "provider", "model", "thinking", "duplicate", "custom"]
)
def test_initial_metadata_is_exact_and_later_mutations_are_not_eligible(mutation):
    events, entries = retry_events()
    rows = entries["entries"]
    if mutation == "missing":
        rows.pop(0)
    elif mutation == "provider":
        rows[0]["provider"] = "foreign"
    elif mutation == "model":
        rows[0]["modelId"] = "foreign"
    elif mutation == "thinking":
        rows[1]["thinkingLevel"] = "high"
    elif mutation == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    else:
        rows[-1]["type"] = "custom"
    value = parser(events)
    value.bind_recovery_entries(entries)
    assert not value.recovery_qualified()


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "usage_missing",
        "usage_input",
        "text",
        "session",
        "observer",
        "entries",
        "query_deadline",
    ],
)
@pytest.mark.parametrize("recovery", ["retry", "post_final_threshold"])
def test_client_final_readback_is_bounded_bound_to_session_and_discarded(
    fault, recovery
):
    events, entries = (
        retry_events() if recovery == "retry" else post_final_threshold_events()
    )
    value = parser(events)
    client = closed_client(value)
    client.closed = False
    client.process.stdout = client.process.stderr = None
    client.bridge_stream = None
    client.stop = threading.Event()
    client.events = queue.Queue()
    client.pi_session_id = "owned-session"
    client.responses = {"owned-prompt": dict(command="prompt", success=True)}
    client.send = lambda *args, **kwargs: "owned-prompt"
    client.response = lambda ident: {}
    client.state = lambda: None
    calls = []

    def query(command):
        calls.append(command)
        if command == "get_last_assistant_text":
            return dict(text="foreign" if fault == "text" else value.final_text())
        if command == "get_session_stats":
            tokens = dict(input=200, output=20, cacheRead=40, cacheWrite=0, total=260)
            if recovery == "post_final_threshold":
                tokens = dict(
                    input=110, output=12, cacheRead=20, cacheWrite=0, total=142
                )
            if fault == "usage_missing":
                tokens = {}
            elif fault == "usage_input":
                tokens = dict(input=1, output=259, cacheRead=0, cacheWrite=0, total=260)
            return dict(
                sessionId="foreign" if fault == "session" else "owned-session",
                tokens=tokens,
            )
        assert command == "get_entries"
        if fault == "query_deadline":
            client.deadline = time.monotonic() - 1
            client.check()
        data = {} if fault == "entries" else entries
        client.responses["entries-id"] = dict(command=command, data=data)
        return data

    client.query = query
    if fault == "observer":
        client.groups.finish = lambda *args: False
    if fault is None:
        assert client.run() == value.final_text()
        assert client.closed and client.close()
        assert calls == ["get_last_assistant_text", "get_session_stats", "get_entries"]
    else:
        with pytest.raises(PiRpcError):
            client.run()
        assert not client.close()
    if "entries-id" in client.responses:
        assert client.responses["entries-id"]["data"] == {}


def test_shutdown_tail_cannot_complete_unfinished_threshold():
    events = threshold_events()
    value = parser(events[:13])
    client = closed_client(value)
    assert not client.close()
    for event in events[13:]:
        value.event(event)
    value.bind_recovery_entries(with_entries(events)[1])
    assert value.recovery_qualified()
    assert not client.close(), "later disposal facts cannot repair failed execution"


@pytest.mark.parametrize("mutation", ["end_error", "extra_start", "empty_tool_use"])
def test_retry_qualification_refuses_unproved_success_shapes(mutation):
    events, entries = retry_events()
    if mutation == "end_error":
        next(e for e in events if e["type"] == "auto_retry_end")["finalError"] = (
            "opaque"
        )
    elif mutation == "extra_start":
        next(e for e in events if e["type"] == "auto_retry_start")["foreign"] = True
    else:
        # Keep the authoritative snapshots coherent while claiming toolUse with
        # no declared tool: native retry success alone cannot qualify it.
        for event in events:
            if event["type"] in {"message_start", "message_end", "turn_end"}:
                message = event["message"]
                if (
                    message.get("role") == "assistant"
                    and message.get("stopReason") == "stop"
                ):
                    message["stopReason"] = "toolUse"
            if event["type"] == "agent_end":
                for message in event["messages"]:
                    if (
                        message.get("role") == "assistant"
                        and message.get("stopReason") == "stop"
                    ):
                        message["stopReason"] = "toolUse"
        # Add an otherwise valid later final assistant in the same run.
        last_agent = next(
            i
            for i, e in enumerate(events)
            if e["type"] == "agent_end" and not e["willRetry"]
        )
        later = lifecycle("prompt")[4:7]
        events[last_agent:last_agent] = [dict(type="turn_start")] + later
        events[last_agent + 4]["messages"] += [later[1]["message"]]
        # Transcript shape follows the wire; only the missing tool declaration
        # in the recovered success is illegal.
        events = [e for e in events if e["type"] != "entry_appended"]
        events, entries = with_entries(events)
    value = parser(events)
    value.bind_recovery_entries(entries)
    assert not value.recovery_qualified()


@pytest.mark.parametrize(
    "mutation",
    [
        "empty",
        "summary",
        "tokens",
        "usage",
        "details",
        "extra",
        "persisted",
        "foreign_kept",
    ],
)
def test_threshold_requires_exact_typed_result_and_persisted_compaction(mutation):
    events = threshold_events()
    result = events[12]["result"]
    if mutation == "empty":
        events[12]["result"] = {}
    elif mutation == "summary":
        result["summary"] = True
    elif mutation == "tokens":
        result["tokensBefore"] = True
    elif mutation == "usage":
        result["usage"]["totalTokens"] = 99
    elif mutation == "details":
        result["details"]["readFiles"] = [True]
    elif mutation == "extra":
        result["foreign"] = "opaque"
    elif mutation == "foreign_kept":
        result["firstKeptEntryId"] = "foreign"
    if mutation == "empty":
        value = parser(events[:-1])
        value.event(events[-1])
    else:
        value = parser(events)
        if mutation == "persisted":
            rows = with_entries(events)[1]
            next(e for e in rows["entries"] if e["type"] == "compaction")["summary"] = (
                "foreign"
            )
            value.bind_recovery_entries(rows)
    assert not value.recovery_qualified()


@pytest.mark.parametrize("fault", [None, "missing", "negative", "one_millisecond"])
def test_compaction_copies_observed_system_and_exact_entry_epoch(fault):
    events = copy.deepcopy(threshold_events())
    system = dict(
        role="system",
        content="private system sentinel",
        sections={"opaque": "private section sentinel"},
        timestamp=1,
    )
    events[2:2] = [
        dict(type="message_start", message=copy.deepcopy(system)),
        dict(type="message_end", message=copy.deepcopy(system)),
    ]
    next(e for e in events if e["type"] == "agent_end")["messages"].insert(0, system)
    events, entries = with_entries(events)
    compact = next(e for e in entries["entries"] if e["type"] == "compaction")
    user = next(
        e
        for e in entries["entries"]
        if e["type"] == "message" and e["message"]["role"] == "user"
    )
    next(e for e in events if e["type"] == "compaction_end")["result"][
        "firstKeptEntryId"
    ] = user["id"]
    compact["firstKeptEntryId"] = user["id"]
    stamp = datetime.fromisoformat(compact["timestamp"].replace("Z", "+00:00"))
    delta = stamp - datetime(1970, 1, 1, tzinfo=UTC)
    system["timestamp"] = (
        delta.days * 86400 + delta.seconds
    ) * 1000 + delta.microseconds // 1000
    compact["systemMessage"] = system
    if fault == "missing":
        compact.pop("systemMessage")
    elif fault == "negative":
        compact["systemMessage"]["timestamp"] = -1
    elif fault == "one_millisecond":
        compact["systemMessage"]["timestamp"] += 1
    value = parser(events[:-1])
    value.event(events[-1])
    value.bind_recovery_entries(entries)
    assert value.recovery_qualified() is (fault is None)
    assert "private" not in json.dumps(value.recovery_evidence.diagnostic())


def test_recovery_diagnosis_is_bounded_and_content_free():
    from millrace.adapters.pi_rpc_diagnostics import native_failure_diagnostics

    events, entries = retry_events(progressive=True)
    value = parser(events)
    next(e for e in entries["entries"] if e["type"] == "message")["message"]["content"][
        0
    ]["text"] = "secret-like-body"
    value.bind_recovery_entries(entries)
    client = closed_client(value)
    client.stderr = b""
    diagnostic = native_failure_diagnostics(
        client, PiRpcError("incomplete recovery evidence")
    )
    assert diagnostic["failure_reason"] == "incomplete_recovery_evidence"
    summary = diagnostic["recovery_qualification"]
    assert summary["assistant_failure_count"] == 2
    assert summary["retry_cycle_count"] == 1
    assert type(summary["qualified"]) is bool
    assert len(json.dumps(summary).encode()) <= 1024
    assert "secret-like-body" not in json.dumps(diagnostic)
    assert "entry-" not in json.dumps(summary)


def post_final_threshold_events(*, tool=False):
    events = lifecycle(
        "prompt", tool=("read", {"path": "opaque"}) if tool else None, result="text"
    )
    events[-1:-1] = [compaction_start(), copy.deepcopy(threshold_events()[12])]
    return with_entries(events)


@pytest.mark.parametrize("tool", [False, True])
def test_post_final_threshold_qualifies_only_after_binding_and_settlement(tool):
    # Pinned _handlePostAgentRun invokes threshold compaction after agent_end.
    # No assistant/turn continuation is emitted for willRetry=false.
    events, entries = post_final_threshold_events(tool=tool)
    value = parser(events[:-1])
    original = copy.deepcopy(value.last)
    assert not value.recovery_qualified()
    value.event(events[-1])
    assert not value.recovery_qualified()
    value.bind_recovery_entries(entries)
    assert value.recovery_qualified()
    assert value.last == original and value.runs == 1 and not value.uncertain
    assert value.recovery_evidence.usage_floor() == (10, 2, 12)
    assert value.fallback_usage() is None
    client = closed_client(value)
    assert client.close() and client.close()


@pytest.mark.parametrize(
    "fault", ["result", "persisted", "usage", "deadline", "continuation"]
)
def test_post_final_threshold_never_relaxes_result_binding_or_closure(fault):
    events, entries = post_final_threshold_events()
    if fault == "result":
        next(e for e in events if e["type"] == "compaction_end")["result"] = {}
    elif fault == "persisted":
        next(e for e in entries["entries"] if e["type"] == "compaction")["summary"] = (
            "foreign"
        )
    elif fault == "continuation":
        # A fresh valid run after post-final compaction cannot change its
        # bound final assistant and then qualify this restricted pathway.
        later = continued_run()[:-1]
        events[-1:-1] = later
        with pytest.raises(PiRpcError, match="agent start boundary"):
            parser(events)
        return
    value = parser(events)
    value.bind_recovery_entries(entries)
    client = closed_client(value)
    if fault == "usage":
        client.usage = None
    elif fault == "deadline":
        client.deadline = time.monotonic() - 1
    assert not client.close()
    client.deadline = time.monotonic() + 10
    client.usage = AdapterTokenUsage(400, 40, 440)
    assert not client.close()


@pytest.mark.parametrize("post_final", [False, True])
@pytest.mark.parametrize(
    "breakdowns",
    [
        {"reasoning": 0},
        {"reasoning": 2},
        {"cacheWrite1h": 0},
        {"reasoning": 2, "cacheWrite1h": 3},
    ],
)
def test_usage_breakdowns_preserve_original_totals_and_provenance(
    post_final, breakdowns
):
    events = post_final_threshold_events()[0] if post_final else threshold_events()
    usage = next(e for e in events if e["type"] == "compaction_end")["result"]["usage"]
    usage.update(cacheWrite=3, totalTokens=15, **breakdowns)
    value = parser(events)
    assert value.recovery_qualified() and not value.uncertain
    assert value.recovery_evidence.usage_floor() == (13, 2, 15)
    assert value.fallback_usage() is None
    assert closed_client(value).close()
    entries = with_entries(events)[1]
    persisted = next(e for e in entries["entries"] if e["type"] == "compaction")
    field = next(iter(breakdowns))
    persisted["usage"][field] += 1
    value.bind_recovery_entries(entries)
    assert not value.recovery_qualified()
    assert not closed_client(value).close()


@pytest.mark.parametrize("field", ["reasoning", "cacheWrite1h"])
@pytest.mark.parametrize(
    "bad", [None, True, False, "0", 0.0, -1, 2**53, float("nan"), float("inf"), [], {}]
)
def test_optional_compaction_usage_requires_safe_integer_subset(field, bad):
    events = threshold_events()
    usage = events[12]["result"]["usage"]
    usage.update(cacheWrite=3, totalTokens=15)
    usage[field] = bad
    value = parser(events)
    assert not value.recovery_qualified()
    assert value.recovery_evidence.tainted
    assert not closed_client(value).close()


@pytest.mark.parametrize(
    "field,bad", [("reasoning", 3), ("cacheWrite1h", 4), ("foreign_usage", 0)]
)
def test_optional_compaction_usage_rejects_outside_subset_and_unknown_key(field, bad):
    events = threshold_events()
    events[12]["result"]["usage"].update(cacheWrite=3, totalTokens=15, **{field: bad})
    value = parser(events)
    assert not value.recovery_qualified() and value.recovery_evidence.tainted
    assert not closed_client(value).close()
