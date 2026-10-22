"""Small router records and strict boundary parsing."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
TERMINAL = frozenset({"succeeded", "failed", "cancelled"})


class RouterError(Exception):
    """A refusal with a stable machine-readable code."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise RouterError("invalid_shape")
    return cast(dict[str, object], value)


def fields(
    value: object, required: set[str], optional: set[str] | None = None
) -> dict[str, object]:
    result = mapping(value)
    if not required <= result.keys() or result.keys() - required - (optional or set()):
        raise RouterError("invalid_fields", "Missing or unknown fields")
    return result


def text(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise RouterError("invalid_string")
    return value


def identifier(value: object) -> str:
    result = text(value)
    if not IDENTIFIER.fullmatch(result):
        raise RouterError("invalid_identifier")
    return result


def number(value: object, *, integer: bool = False, zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RouterError("invalid_number")
    try:
        finite = math.isfinite(value)
    except OverflowError as exc:
        raise RouterError("invalid_number") from exc
    if not finite or value < 0 or (not zero and value == 0):
        raise RouterError("invalid_number")
    if integer and not isinstance(value, int):
        raise RouterError("invalid_number")
    return value


def common_settings(
    value: object, *, policy: bool = False, location: str = "settings"
) -> dict[str, object]:
    """Validate explicit common fields without resolving or filling omissions."""
    binding = "binding_pin" if policy else "binding"
    timeout = "timeout_ceiling_seconds" if policy else "timeout_seconds"
    try:
        obj = fields(
            value,
            set(),
            {binding, timeout} | (set() if policy else {"adapter_settings"}),
        )
    except RouterError as exc:
        raise RouterError("invalid_settings", location) from exc
    result: dict[str, object] = {}
    for key, item in obj.items():
        try:
            if key == "adapter_settings":
                namespaces = mapping(item)
                clean: dict[str, object] = {}
                for name, namespace in namespaces.items():
                    identifier(name)
                    entry = fields(namespace, {"contract", "config"})
                    text(entry["contract"])
                    mapping(entry["config"])
                    clean[name] = entry
                result[key] = clean
            else:
                result[key] = identifier(item) if key == binding else number(item)
        except RouterError as exc:
            raise RouterError("invalid_settings", f"{location}.{key}") from exc
    return result


def strings(value: object, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise RouterError("invalid_list")
    return tuple(
        item
        if allow_empty and isinstance(item, str) and "\x00" not in item
        else text(item)
        for item in value
    )


def relative(value: object, *, owner_forbidden: bool = False) -> str:
    result = text(value)
    path = Path(result)
    if path.is_absolute() or ".." in path.parts:
        raise RouterError("invalid_path", result)
    if owner_forbidden and any(
        part.casefold() == ".millrace-router-owner" for part in path.parts
    ):
        raise RouterError("invalid_path", result)
    return result


def resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError) as exc:
        raise RouterError("invalid_path", str(path)) from exc


def within(root: Path, name: str, *, owner_forbidden: bool = False) -> Path:
    result = resolved(root / name)
    base = resolved(root)
    if not result.is_relative_to(base):
        raise RouterError("invalid_path", name)
    if owner_forbidden and (
        any(
            part.casefold() == ".millrace-router-owner"
            for part in result.relative_to(base).parts
        )
        or result.is_relative_to(resolved(base / ".millrace-router-owner"))
    ):
        raise RouterError("invalid_path", name)
    return result


def strict_json(raw: bytes | str) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise RouterError("duplicate_key", key)
            result[key] = value
        return result

    def constant(value: str) -> object:
        raise RouterError("nonfinite_json", value)

    try:
        return cast(
            object,
            json.loads(
                raw.decode("utf-8") if isinstance(raw, bytes) else raw,
                object_pairs_hook=pairs,
                parse_constant=constant,
            ),
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise RouterError("invalid_json") from exc


@dataclass(frozen=True)
class Action:
    kind: str
    target: str | None = None
    reason: str | None = None
    limit: int = 0
    exhausted: Action | None = None

    def data(self) -> dict[str, object]:
        if self.kind in {"goto", "finish"}:
            return {self.kind: self.target}
        if self.kind == "pause":
            value: dict[str, object] = {"reason": self.reason}
            if self.target is not None:
                value["resume"] = self.target
            return {"pause": value}
        assert self.exhausted is not None
        return {"retry": {"limit": self.limit, "exhausted": self.exhausted.data()}}


def action(value: object, *, exhausted: bool = False) -> Action:
    obj = mapping(value)
    if len(obj) != 1:
        raise RouterError("invalid_action")
    kind, body = next(iter(obj.items()))
    if exhausted and kind not in {"goto", "pause", "finish"}:
        raise RouterError("invalid_retry_exhaustion")
    if kind == "goto":
        return Action(kind, identifier(body))
    if kind == "finish":
        if body not in ("succeeded", "failed"):
            raise RouterError("invalid_finish")
        return Action(kind, body)
    if kind == "pause":
        item = fields(body, {"reason"}, {"resume"})
        return Action(
            kind,
            identifier(item["resume"]) if "resume" in item else None,
            identifier(item["reason"]),
        )
    if kind == "retry":
        item = fields(body, {"limit", "exhausted"})
        return Action(
            kind,
            limit=int(number(item["limit"], integer=True, zero=True)),
            exhausted=action(item["exhausted"], exhausted=True),
        )
    raise RouterError("invalid_action")


@dataclass(frozen=True)
class TerminalDependency:
    plane_id: str
    status: str


@dataclass(frozen=True)
class Plane:
    start: str
    workspace: str
    nodes: dict[str, Node]
    defaults: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Seam:
    producer_plane: str
    producer_node: str
    outcome: str
    consumer_plane: str
    consumer_node: str

    def data(self) -> dict[str, object]:
        return {
            "producer": {
                "plane": self.producer_plane,
                "node": self.producer_node,
                "outcome": self.outcome,
            },
            "consumer": {"plane": self.consumer_plane, "node": self.consumer_node},
        }


@dataclass(frozen=True)
class Node:
    binding: str | None
    cwd: str
    timeout: float | None
    inputs: tuple[str, ...]
    outcomes: tuple[str, ...]
    routes: dict[str, Action]
    on_error: Action
    on_unknown: Action
    config: dict[str, object] = field(default_factory=dict)
    requires_terminal: tuple[TerminalDependency, ...] = ()


@dataclass(frozen=True)
class Graph:
    max_invocations: int
    max_elapsed: float
    bindings: dict[str, str]
    planes: dict[str, Plane]
    controls: tuple[str, ...]
    defaults: dict[str, object] = field(default_factory=dict)
    binding_configs: dict[str, dict[str, object]] = field(default_factory=dict)
    seams: dict[str, Seam] = field(default_factory=dict)
    max_parallel: int = 1


@dataclass(frozen=True)
class CommandResult:
    stdout: bytes
    stderr: bytes
    classification: str
    writer_state: str
    diagnostic: str = ""
    exit_code: int | None = None
    adapter_evidence: dict[str, object] | None = None


def parse_result(
    raw: bytes, node: Node, project: Path, *, resolve_refs: bool = True
) -> tuple[str, str | None]:
    try:
        if len(raw) > 65536:
            raise RouterError("result_too_large")
        obj = fields(strict_json(raw.strip()), {"outcome"}, {"summary", "refs"})
        outcome = identifier(obj["outcome"])
        if "summary" in obj and not isinstance(obj["summary"], str):
            raise RouterError("invalid_summary")
        for ref in strings(obj.get("refs", [])):
            name = relative(ref, owner_forbidden=True)
            if resolve_refs:
                within(project, name, owner_forbidden=True)
        return ("semantic" if outcome in node.outcomes else "unknown"), outcome
    except RouterError:
        return "protocol_failure", None


def recorded_result(
    raw: bytes, node: Node, project: Path, transport: str, admission: object
) -> tuple[str, str | None]:
    """Check parsed facts without rereading mutable project context."""
    pure = (
        parse_result(raw, node, project, resolve_refs=False)
        if transport == "completed"
        else (transport, None)
    )
    if pure[0] not in {"semantic", "unknown"}:
        if admission != "not_applicable":
            raise RouterError("completion_integrity")
        return pure
    if admission == "accepted":
        return pure
    if admission == "rejected" and strings(
        mapping(strict_json(raw.strip())).get("refs", [])
    ):
        return "protocol_failure", None
    raise RouterError("completion_integrity")


def select_action(
    node: Node, classification: str, outcome: str | None, count: int
) -> tuple[Action, int]:
    if classification == "semantic":
        assert outcome is not None
        selected = node.routes[outcome]
    elif classification == "unknown":
        selected = node.on_unknown
    else:
        selected = node.on_error
    if selected.kind == "retry":
        if count < selected.limit:
            return selected, 1
        assert selected.exhausted is not None
        return selected.exhausted, 0
    return selected, 0
