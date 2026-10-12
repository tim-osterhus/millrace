"""Private bounded provenance for admitted supervised Pi recovery evidence.

This ledger cannot qualify transport, tools, usage, disposal or candidate text.
No content or native entry identifiers leave its content-free diagnostic.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _identity(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or not 1 <= len(value.encode("utf-8")) <= 64
    ):
        raise ValueError("entry identity")
    return value


def _entry(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("entry object")
    _identity(value.get("id"))
    parent = value.get("parentId")
    if parent is not None:
        _identity(parent)
    if "parentId" not in value or parent == value["id"]:
        raise ValueError("entry parent")
    stamp = value.get("timestamp")
    if (
        not isinstance(stamp, str)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", stamp) is None
    ):
        raise ValueError("entry timestamp")
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("entry timestamp")
    if not isinstance(value.get("type"), str):
        raise ValueError("entry type")
    return value


@dataclass(frozen=True)
class _Failure:
    assistant: int
    run: int
    turn: int
    message_sha256: str


@dataclass(frozen=True)
class _Omission:
    failure: int
    entry_id: str
    parent_id: str
    target_id: str
    entry_sha256: str


@dataclass
class _Cycle:
    kind: Literal["threshold", "retry"]
    run: int
    turn: int
    post_final: bool = False
    outcome: Literal["open", "aftermath", "succeeded", "failed"] = "open"
    attempt: int = 0
    maximum: int = 0
    failures: list[int] = field(default_factory=list)
    success_assistant: int | None = None
    success_run: int | None = None
    success_turn: int | None = None
    usage_floor: tuple[int, int, int] = (0, 0, 0)


class PiRecoveryEvidence:
    def __init__(
        self, enabled: bool, limit: int, byte_limit: int, model: dict[str, Any]
    ) -> None:
        self.enabled, self.limit, self.byte_limit = enabled, limit, byte_limit
        self.provider, self.model_id = model["provider"], model["id"]
        self.run = self.turn = self.assistant = 0
        self.last_stop: str | None = None
        self.last_failure: int | None = None
        self.failed_run: int | None = None
        self.prior_tool_turn = False
        self.turn_tools = 0
        self.cycles: list[_Cycle] = []
        self.failures: dict[int, _Failure] = {}
        self.omissions: dict[int, _Omission] = {}
        self.active: _Cycle | None = None
        self.transcript: list[tuple[str, str]] = []
        self.observed = self.tainted = self.binding_verified = False
        self.reason = "none"

    def taint(self, reason: str = "parser_damage") -> None:
        self.tainted = True
        if self.reason == "none":
            self.reason = reason

    def _room(self, count: int = 1) -> bool:
        if (
            len(self.failures)
            + len(self.cycles)
            + len(self.omissions)
            + len(self.transcript)
            + count
            > self.limit
        ):
            self.taint("bound_exceeded")
            return False
        return True

    @staticmethod
    def _compaction_fields(value: dict[str, Any]) -> dict[str, Any]:
        return {
            k: value[k]
            for k in ("summary", "firstKeptEntryId", "tokensBefore", "details", "usage")
            if k in value
        }

    def _compaction_result(self, value: Any) -> tuple[int, int, int]:
        """The stock pinned compactor result, without retaining its content."""
        if not isinstance(value, dict) or set(value) != {
            "summary",
            "firstKeptEntryId",
            "tokensBefore",
            "estimatedTokensAfter",
            "usage",
            "details",
        }:
            raise ValueError("compaction result shape")
        if not isinstance(value["summary"], str) or not value["summary"].strip():
            raise ValueError("compaction summary")
        _identity(value["firstKeptEntryId"])
        for name in ("tokensBefore", "estimatedTokensAfter"):
            if type(value[name]) is not int or not 0 <= value[name] <= 2**53 - 1:
                raise ValueError("compaction token count")
        details = value["details"]
        if not isinstance(details, dict) or set(details) != {
            "readFiles",
            "modifiedFiles",
        }:
            raise ValueError("compaction details")
        for files in details.values():
            if (
                not isinstance(files, list)
                or len(files) > self.limit
                or not all(isinstance(path, str) for path in files)
            ):
                raise ValueError("compaction files")
        usage = value["usage"]
        fields = {"input", "output", "cacheRead", "cacheWrite", "totalTokens"}
        if (
            not isinstance(usage, dict)
            or not fields <= set(usage)
            or set(usage) - fields - {"cost", "reasoning", "cacheWrite1h"}
        ):
            raise ValueError("compaction usage")
        if any(
            type(usage[k]) is not int or not 0 <= usage[k] <= 2**53 - 1 for k in fields
        ):
            raise ValueError("compaction usage count")
        # Pinned Pi Usage breakdowns are subsets, already included in totals.
        for optional, total in (
            ("reasoning", "output"),
            ("cacheWrite1h", "cacheWrite"),
        ):
            if optional in usage and (
                type(usage[optional]) is not int
                or not 0 <= usage[optional] <= usage[total]
            ):
                raise ValueError("compaction usage breakdown")
        inp = usage["input"] + usage["cacheRead"] + usage["cacheWrite"]
        out = usage["output"]
        if inp + out != usage["totalTokens"]:
            raise ValueError("compaction usage sum")
        _digest(value)
        return inp, out, inp + out

    def context_edit(self, event: dict[str, Any]) -> bool:
        """Claim only a typed omission immediately awaiting its continued run."""
        cycle = self.active
        if not self.enabled:
            return False
        if not self._room(2):
            return False
        try:
            if (
                set(event) != {"type", "entry"}
                or cycle is None
                or cycle.kind != "retry"
                or cycle.outcome != "open"
                or self.failed_run != self.run
                or not cycle.failures
            ):
                raise ValueError("omission boundary")
            failure = cycle.failures[-1]
            if failure in self.omissions:
                raise ValueError("duplicate omission")
            entry = _entry(event["entry"])
            if (
                set(entry)
                != {"type", "id", "parentId", "timestamp", "targetId", "replacement"}
                or entry["type"] != "context_edit"
                or entry["replacement"] is not None
                or entry["parentId"] is None
            ):
                raise ValueError("omission shape")
            target = _identity(entry["targetId"])
            if target == entry["id"]:
                raise ValueError("omission target")
            self.omissions[failure] = _Omission(
                failure, entry["id"], entry["parentId"], target, _digest(entry)
            )
            self.transcript.append(("context_edit", _digest(entry)))
            return True
        except (ValueError, TypeError, UnicodeError, KeyError, OverflowError):
            self.taint("binding_failed")
            return False

    def observe(self, event: dict[str, Any], *, run_open: bool) -> None:
        """Consume only events already validated by the authoritative lifecycle."""
        kind = event["type"]
        if self.reason == "bound_exceeded":
            return
        if self.enabled and kind == "message_end":
            if not self._room():
                return
            self.transcript.append(("message", _digest(event["message"])))
        if kind == "agent_start":
            if self.active is not None and self.active.kind == "retry":
                if (
                    not self.active.failures
                    or self.active.failures[-1] not in self.omissions
                ):
                    self.taint("missing_omission")
                self.failed_run = None
            self.run += 1
        elif kind == "turn_start":
            self.turn += 1
            self.turn_tools = 0
        elif kind == "message_end" and event["message"]["role"] == "assistant":
            self.assistant += 1
            message = event["message"]
            self.last_stop = message["stopReason"]
            self.turn_tools = sum(c["type"] == "toolCall" for c in message["content"])
            if self.enabled and (
                (
                    self.last_stop == "toolUse"
                    and not self.turn_tools
                    and self.active is not None
                )
                or (self.last_stop == "error" and self.turn_tools)
            ):
                self.taint("unsupported_recovery")
            self.last_failure = None
            if self.last_stop == "error":
                self.observed = True
                self.last_failure = self.assistant
                if self.enabled:
                    if not self._room():
                        return
                    self.failures[self.assistant] = _Failure(
                        self.assistant, self.run, self.turn, _digest(message)
                    )
            cycle = self.active
            if cycle is not None and cycle.kind == "threshold":
                if (
                    cycle.post_final
                    or cycle.outcome != "aftermath"
                    or self.run != cycle.run
                    or self.turn != cycle.turn + 1
                    or self.last_stop not in {"stop", "toolUse"}
                ):
                    self.taint("unfinished_recovery")
                else:
                    cycle.success_assistant = self.assistant
                    cycle.success_run, cycle.success_turn = self.run, self.turn
        elif kind == "turn_end":
            cycle = self.active
            if cycle is not None and cycle.outcome == "aftermath":
                if (
                    cycle.success_assistant != self.assistant
                    or cycle.success_run != self.run
                    or cycle.success_turn != self.turn
                ):
                    self.taint("unfinished_recovery")
                else:
                    cycle.outcome = "succeeded"
                    self.active = None
            self.prior_tool_turn = self.last_stop == "toolUse" and self.turn_tools > 0
        elif kind == "agent_end":
            self.failed_run = (
                self.run if self.last_stop == "error" and event["willRetry"] else None
            )
        elif kind == "agent_settled":
            cycle = self.active
            if cycle is not None and cycle.post_final:
                if (
                    cycle.outcome != "aftermath"
                    or cycle.success_assistant != self.assistant
                    or cycle.success_run != self.run
                    or cycle.success_turn != self.turn
                    or self.last_stop != "stop"
                ):
                    self.taint("unfinished_recovery")
                else:
                    cycle.outcome = "succeeded"
                    self.active = None
        elif kind in {
            "compaction_start",
            "compaction_end",
            "auto_retry_start",
            "auto_retry_end",
            "summarization_retry_scheduled",
            "summarization_retry_attempt_start",
            "summarization_retry_finished",
        }:
            self.observed = True
            if not self.enabled:
                return
            shapes = {
                "auto_retry_start": {
                    "type",
                    "attempt",
                    "maxAttempts",
                    "delayMs",
                    "errorMessage",
                },
                "auto_retry_end": {"type", "success", "attempt"},
                "compaction_start": {"type", "reason"},
                "compaction_end": {"type", "reason", "result", "aborted", "willRetry"},
            }
            if kind in shapes and set(event) != shapes[kind]:
                self.taint("unsupported_recovery")
            if kind == "compaction_start":
                post_final = not run_open
                if (
                    event["reason"] != "threshold"
                    or (run_open and not self.prior_tool_turn)
                    or (post_final and (not self.run or self.last_stop != "stop"))
                    or self.active is not None
                ):
                    self.taint("unsupported_recovery")
                    return
                self.active = _Cycle(
                    "threshold", self.run, self.turn, post_final=post_final
                )
                if post_final:
                    # _handlePostAgentRun compacts after the successful run.
                    # It emits settlement, without a new assistant/turn.
                    self.active.success_assistant = self.assistant
                    self.active.success_run, self.active.success_turn = (
                        self.run,
                        self.turn,
                    )
                if not self._room():
                    return
                self.cycles.append(self.active)
            elif kind == "compaction_end":
                cycle = self.active
                if (
                    cycle is None
                    or cycle.kind != "threshold"
                    or cycle.outcome != "open"
                    or event["reason"] != "threshold"
                    or event["aborted"]
                    or event["willRetry"]
                    or "errorMessage" in event
                ):
                    self.taint("unsupported_recovery")
                    if cycle is not None:
                        cycle.outcome = "failed"
                    return
                cycle.outcome = "aftermath"
                try:
                    cycle.usage_floor = self._compaction_result(event["result"])
                except (ValueError, TypeError, UnicodeError, OverflowError):
                    self.taint("unsupported_recovery")
                    cycle.outcome = "failed"
                    return
                if not self._room():
                    return
                self.transcript.append(
                    ("compaction", _digest(self._compaction_fields(event["result"])))
                )
            elif kind == "auto_retry_start":
                failure = self.failures.get(self.last_failure or -1)
                if (
                    failure is None
                    or self.failed_run != self.run
                    or failure.run != self.run
                    or failure.turn != self.turn
                ):
                    self.taint("unresolved_failure")
                    return
                cycle = self.active
                if cycle is None:
                    if not self._room():
                        return
                    cycle = _Cycle("retry", self.run, self.turn)
                    self.cycles.append(cycle)
                    self.active = cycle
                if (
                    cycle.kind != "retry"
                    or cycle.outcome != "open"
                    or event["attempt"] != cycle.attempt + 1
                    or (cycle.attempt and event["maxAttempts"] != cycle.maximum)
                    or failure.assistant in cycle.failures
                    or failure.assistant in self.omissions
                ):
                    self.taint("unresolved_failure")
                    return
                cycle.attempt, cycle.maximum = event["attempt"], event["maxAttempts"]
                cycle.failures.append(failure.assistant)
            elif kind == "auto_retry_end":
                cycle = self.active
                if (
                    cycle is None
                    or cycle.kind != "retry"
                    or cycle.outcome != "open"
                    or not event["success"]
                    or event["attempt"] != cycle.attempt
                    or self.last_stop not in {"stop", "toolUse"}
                    or not cycle.failures
                    or cycle.failures[-1] not in self.omissions
                    or self.run != self.failures[cycle.failures[-1]].run + 1
                ):
                    self.taint("unfinished_recovery")
                    if cycle is not None:
                        cycle.outcome = "failed"
                    return
                cycle.outcome = "aftermath"
                cycle.success_assistant = self.assistant
                cycle.success_run, cycle.success_turn = self.run, self.turn
            else:
                self.taint("unsupported_recovery")

    def needs_entries(self) -> bool:
        return self.enabled and bool(self.cycles) and not self.tainted

    def usage_floor(self) -> tuple[int, int, int]:
        return (
            sum(c.usage_floor[0] for c in self.cycles),
            sum(c.usage_floor[1] for c in self.cycles),
            sum(c.usage_floor[2] for c in self.cycles),
        )

    def bind_entries(self, data: Any) -> None:
        """Validate one final read-only transcript; keep only the verdict/digests."""
        if not self.enabled or self.tainted:
            return
        try:
            if not isinstance(data, dict) or set(data) != {"entries", "leafId"}:
                raise ValueError("transcript shape")
            entries = data["entries"]
            if not isinstance(entries, list) or not 1 <= len(entries) <= self.limit:
                raise ValueError("transcript bound")
            if (
                len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode())
                > self.byte_limit
            ):
                raise ValueError("transcript bytes")
            rows = {}
            transcript = []
            previous = None
            current_system_sha256 = None
            for index, raw in enumerate(entries):
                entry = _entry(raw)
                if entry["id"] in rows or entry["parentId"] != previous:
                    raise ValueError("entry order")
                rows[entry["id"]] = entry
                previous = entry["id"]
                base = {"type", "id", "parentId", "timestamp"}
                kind = entry["type"]
                if index == 0:
                    if (
                        set(entry) != base | {"provider", "modelId"}
                        or kind != "model_change"
                        or entry["provider"] != self.provider
                        or entry["modelId"] != self.model_id
                    ):
                        raise ValueError("initial model metadata")
                elif index == 1:
                    if (
                        set(entry) != base | {"thinkingLevel"}
                        or kind != "thinking_level_change"
                        or entry["thinkingLevel"] != "xhigh"
                    ):
                        raise ValueError("initial thinking metadata")
                elif kind == "message" and set(entry) == base | {"message"}:
                    message = entry["message"]
                    if not isinstance(message, dict):
                        raise ValueError("transcript message")
                    if message.get("role") == "system":
                        current_system_sha256 = _digest(
                            {k: v for k, v in message.items() if k != "timestamp"}
                        )
                    transcript.append((kind, _digest(entry["message"])))
                elif kind == "context_edit":
                    transcript.append((kind, _digest(entry)))
                elif kind == "compaction":
                    required = base | {
                        "summary",
                        "firstKeptEntryId",
                        "tokensBefore",
                        "fromHook",
                    }
                    if (
                        not required <= set(entry)
                        or set(entry) - required - {"details", "usage", "systemMessage"}
                        or entry["fromHook"] is not False
                        or entry["firstKeptEntryId"] == entry["id"]
                        or entry["firstKeptEntryId"] not in rows
                        or rows[entry["firstKeptEntryId"]]["type"]
                        not in {"message", "compaction"}
                    ):
                        raise ValueError("compaction entry")
                    if ("systemMessage" in entry) != (
                        current_system_sha256 is not None
                    ):
                        raise ValueError("compaction system presence")
                    if "systemMessage" in entry:
                        system = entry["systemMessage"]
                        stamp = datetime.fromisoformat(
                            entry["timestamp"].replace("Z", "+00:00")
                        )
                        delta = stamp - datetime(1970, 1, 1, tzinfo=UTC)
                        epoch_ms = (
                            delta.days * 86400 + delta.seconds
                        ) * 1000 + delta.microseconds // 1000
                        if (
                            not isinstance(system, dict)
                            or type(system.get("timestamp")) is not int
                            or system["timestamp"] != epoch_ms
                            or _digest(
                                {k: v for k, v in system.items() if k != "timestamp"}
                            )
                            != current_system_sha256
                        ):
                            raise ValueError("compaction system provenance")
                    transcript.append((kind, _digest(self._compaction_fields(entry))))
                else:
                    raise ValueError("unsupported transcript entry")
            if transcript != self.transcript or data["leafId"] != previous:
                raise ValueError("transcript event binding")
            for entry in rows.values():
                if entry["parentId"] is not None and entry["parentId"] not in rows:
                    raise ValueError("foreign entry parent")
            path = []
            leaf = _identity(data["leafId"])
            while leaf is not None:
                if leaf in path or leaf not in rows:
                    raise ValueError("active ancestry")
                path.append(leaf)
                leaf = rows[leaf]["parentId"]
            edits, targets = set(), set()
            for failure, omission in self.omissions.items():
                if (
                    omission.entry_id in edits
                    or omission.target_id in targets
                    or omission.entry_id not in path
                    or omission.target_id not in path
                    or path.index(omission.target_id) <= path.index(omission.entry_id)
                ):
                    raise ValueError("omission ancestry")
                edits.add(omission.entry_id)
                targets.add(omission.target_id)
                edit, target = rows[omission.entry_id], rows[omission.target_id]
                if (
                    _digest(edit) != omission.entry_sha256
                    or set(target) != {"type", "id", "parentId", "timestamp", "message"}
                    or target["type"] != "message"
                    or not isinstance(target["message"], dict)
                    or target["message"].get("role") != "assistant"
                    or target["message"].get("stopReason") != "error"
                    or _digest(target["message"])
                    != self.failures[failure].message_sha256
                ):
                    raise ValueError("omission identity")
            self.binding_verified = bool(self.cycles)
        except (
            ValueError,
            TypeError,
            UnicodeError,
            KeyError,
            OverflowError,
            RecursionError,
        ):
            self.taint("binding_failed")

    def qualified(self) -> bool:
        if self.tainted or (self.observed and not self.enabled):
            return False
        if self.active is not None or any(
            c.outcome != "succeeded" for c in self.cycles
        ):
            return False
        covered = [f for c in self.cycles if c.kind == "retry" for f in c.failures]
        return (
            len(covered) == len(set(covered))
            and set(covered) == set(self.failures)
            and (not self.cycles or self.binding_verified)
            and (
                not self.failures
                or (self.binding_verified and set(self.omissions) == set(self.failures))
            )
        )

    def diagnostic(self) -> dict[str, bool | int | str]:
        reason = self.reason
        if reason == "none" and self.observed:
            if not self.enabled:
                reason = "legacy_profile"
            elif self.active is not None or any(
                c.outcome != "succeeded" for c in self.cycles
            ):
                reason = "unfinished_recovery"
            elif set(self.failures) != set(self.omissions):
                reason = "unresolved_failure"
            elif self.cycles and not self.binding_verified:
                reason = "binding_unverified"
        return dict(
            enabled=self.enabled,
            observed=self.observed,
            tainted=self.tainted,
            reason=reason,
            qualified=self.qualified(),
            assistant_failure_count=len(self.failures),
            threshold_cycle_count=sum(c.kind == "threshold" for c in self.cycles),
            retry_cycle_count=sum(c.kind == "retry" for c in self.cycles),
            unfinished_cycle_count=sum(c.outcome != "succeeded" for c in self.cycles),
            omission_count=len(self.omissions),
            persisted_binding_verified=self.binding_verified,
        )
