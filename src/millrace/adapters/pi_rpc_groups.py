"""Private Pi observer facts for the explicitly trusted POSIX group profile.

No group signals or descendant scans: absent groups retire permanently; uncertain
ownership retains the attempt. Escaped descendants are outside this predicate.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

PROTOCOL = "pi-group-observer.v1"
BUNDLE = "dist/bundle/chunks/chunk-4DKZACXI.js"
BUNDLE_SHA256 = "2b60c86e356ef338ab1cea32b63f8f01e8d8d3bb7f14342fcc876a8d9a8b5505"
BRIDGE_SHA256 = "f9f6107955c4d90142e6accc889cbbcc5665b5b6e6d498afd16fea4ffe5b1648"
SUPERVISOR_SHA256 = "288590dd11028f383cae507f1312264052f9ca9dd81133f54ab3476d47aea9b1"


def bridge_identity() -> dict[str, str]:
    return {
        "path": str(Path(__file__).with_name("pi_group_bridge.mjs").resolve()),
        "sha256": BRIDGE_SHA256,
    }


def supervisor_identity() -> dict[str, str]:
    return {
        "path": str(Path(__file__).with_name("pi_rpc_supervisor.py").resolve()),
        "sha256": SUPERVISOR_SHA256,
    }


def command_digest(command: str) -> str:
    return hashlib.sha256(command.encode()).hexdigest()


def environment_digest(env: dict[str, str]) -> str:
    return command_digest(
        json.dumps(sorted(env.items()), ensure_ascii=False, separators=(",", ":"))
    )


@dataclass(frozen=True)
class GroupProfile:
    bridge: Path
    bridge_sha256: str
    package: Path
    profile_sha256: str
    platform: str = "darwin"
    supervised: bool = False
    supervisor: Path | None = None
    supervisor_sha256: str | None = None

    def argv(
        self, argv: tuple[str, ...], fd: int, limits: dict[str, Any]
    ) -> tuple[str, ...]:
        query = urlencode(
            {
                "fd": fd,
                "profile": self.profile_sha256,
                "records": limits["max_event_count"],
                "bytes": limits["max_stdout_bytes"],
                "line": limits["max_event_bytes"],
            }
        )
        return (argv[0], "--import", self.bridge.as_uri() + "?" + query, *argv[1:])


def _group_absent(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def _shape(payload: Any, fields: dict[str, str]) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != set(fields):
        raise ValueError("observer fields")
    for name, kind in fields.items():
        value = payload[name]
        nullable = kind.endswith("?")
        if nullable and value is None:
            continue
        kind = kind.removesuffix("?")
        checks: dict[str, Callable[[], bool]] = {
            "text": lambda: isinstance(value, str) and bool(value),
            "int": lambda: type(value) is int and 0 <= value <= 2**53 - 1,
            "pid": lambda: type(value) is int and 1 <= value <= 2**31 - 1,
            "target": lambda: type(value) is int and 1 <= abs(value) <= 2**31 - 1,
            "bool": lambda: type(value) is bool,
            "list": lambda: isinstance(value, list),
            "hash": lambda: (
                isinstance(value, str)
                and len(value) == 64
                and all(c in "0123456789abcdef" for c in value)
            ),
            "timeout": lambda: type(value) in (int, float) and 0 < value <= 2147483.647,
        }
        if not checks[kind]():
            raise ValueError("observer field type")
    return payload


_SCHEMAS = {
    "hello": dict(
        pid="pid",
        cwd="text",
        bridge_sha256="hash",
        profile_sha256="hash",
        env_sha256="hash",
        platform="text",
        node="text",
    ),
    "rpc": dict(index="int", sha256="hash"),
    "start": dict(call="text", args_sha256="hash", timeout="timeout?"),
    "spawn": dict(
        call="text?",
        pid="pid",
        command_sha256="hash",
        cwd="text",
        env_sha256="hash",
        shell="text",
        args="list",
        detached="bool",
        stdio="list",
        windows_hide="bool",
    ),
    "root_exit": dict(pid="pid", code="int?", signal="text?", group="text"),
    "stream_end": dict(pid="pid", name="text"),
    "stream_destroy": dict(pid="pid", name="text", ended="bool"),
    "child_close": dict(pid="pid", code="int?", signal="text?"),
    "spawn_error": dict(pid="pid", code="text"),
    "control": dict(pid="target", signal="text", frames="list"),
    "end": dict(call="text"),
    "fault": dict(reason="text"),
    "bye": dict(code="int", rpc_count="int", spawn_count="int"),
}


@dataclass
class _Spawn:
    call: str | None
    root: tuple[int | None, str | None] | None = None
    ends: set[str] = field(default_factory=set)
    close: tuple[int | None, str | None] | None = None
    retired: bool = False


class PiGroups:
    def __init__(
        self,
        profile: GroupProfile,
        cwd: Path,
        env: dict[str, str],
        model: dict[str, Any],
        pi_pid: int,
        uncertain: set[str],
    ) -> None:
        self.profile, self.cwd, self.model = profile, cwd, model
        self.env = dict(env)
        # Darwin CoreFoundation inserts this before the pinned Node preload runs.
        if profile.platform == "darwin":
            self.env.setdefault("__CF_USER_TEXT_ENCODING", f"0x{os.getuid():X}:0x0:0x0")
        self.pi_pid, self.uncertain = pi_pid, uncertain
        self.session_id: str | None = None
        self.sequence = 0
        self.hello = self.bye = False
        self.integrity = True
        self.starts: dict[str, tuple[str, int | float | None]] = {}
        self.ends: set[str] = set()
        self.spawns: dict[int, _Spawn] = {}
        self.calls: dict[str, int] = {}
        self.rpc_hashes: list[str] = []
        self.observed_hashes: list[str] = []
        self.rpc_starts: dict[str, dict[str, Any]] = {}
        self.control_causes: set[str] = set()
        self.supervisor_disposed = False

    def expected_environment_digest(self) -> str:
        env = dict(self.env)
        env.update(PI_CODING_AGENT="true", AI_AGENT="pi")
        agent_bin = str(Path(env.get("PI_CODING_AGENT_DIR", "")) / "bin")
        paths = env.get("PATH", "").split(":")
        if agent_bin not in paths:
            env["PATH"] = ":".join([agent_bin, *filter(None, paths)])
        env.update(
            PI_SESSION_ID=self.session_id or "",
            PI_PROVIDER=self.model["provider"],
            PI_MODEL=self.model["id"],
            PI_REASONING_LEVEL="xhigh",
        )
        return environment_digest(env)

    def rpc(self, line: bytes, event: dict[str, Any]) -> None:
        self.rpc_hashes.append(hashlib.sha256(line).hexdigest())
        if (
            event.get("type") == "tool_execution_start"
            and event.get("toolName") == "bash"
        ):
            call = event.get("toolCallId")
            if not isinstance(call, str) or call in self.rpc_starts:
                self.integrity = False
                raise ValueError("observer RPC start identity")
            self.rpc_starts[call] = event

    def record(self, event: dict[str, Any]) -> None:
        try:
            self._record(event)
        except (ValueError, TypeError, KeyError):
            self.integrity = False
            self.uncertain.add("invalid group observer")
            raise ValueError("invalid group observer") from None

    def _record(self, event: dict[str, Any]) -> None:
        if (
            set(event) != {"v", "seq", "kind", "payload"}
            or event["v"] != PROTOCOL
            or type(event["seq"]) is not int
            or event["seq"] != self.sequence
            or self.bye
        ):
            raise ValueError("observer envelope/sequence")
        kind = event["kind"]
        if not isinstance(kind, str) or kind not in _SCHEMAS:
            raise ValueError("observer kind")
        p = _shape(event["payload"], _SCHEMAS[kind])
        self.sequence += 1
        if kind == "hello":
            if (
                self.hello
                or self.sequence != 1
                or p
                != dict(
                    pid=self.pi_pid,
                    cwd=str(self.cwd),
                    bridge_sha256=self.profile.bridge_sha256,
                    profile_sha256=self.profile.profile_sha256,
                    env_sha256=environment_digest(self.env),
                    platform=self.profile.platform,
                    node="v24.18.0",
                )
            ):
                raise ValueError("observer identity")
            self.hello = True
            return
        if not self.hello:
            raise ValueError("missing observer hello")
        if kind == "rpc":
            if p["index"] != len(self.observed_hashes):
                raise ValueError("observer RPC order")
            self.observed_hashes.append(p["sha256"])
        elif kind == "start":
            if p["call"] in self.starts:
                raise ValueError("duplicate observer start")
            self.starts[p["call"]] = p["args_sha256"], p["timeout"]
        elif kind == "spawn":
            pid, call = p["pid"], p["call"]
            if pid in self.spawns or pid == self.pi_pid:
                raise ValueError("duplicate observer PID")
            # Retain even unsupported spawns for truthful disposal.
            self.spawns[pid] = _Spawn(call)
            if (
                call not in self.starts
                or call in self.calls
                or call in self.ends
                or p["command_sha256"] != self.starts[call][0]
                or p["cwd"] != str(self.cwd)
                or p["env_sha256"] != self.expected_environment_digest()
                or p["shell"] != "/bin/bash"
                or p["args"] != ["-c"]
                or p["stdio"] != ["ignore", "pipe", "pipe"]
                or p["detached"] is not True
                or p["windows_hide"] is not True
            ):
                raise ValueError("unqualified stock spawn")
            self.calls[call] = pid
        elif kind == "control":
            self.control_causes.add(self._cause(p["frames"]))
            self.uncertain.add("process control observed")
        elif kind == "fault":
            self.uncertain.add("group observer fault")
        elif kind == "end":
            if p["call"] not in self.starts or p["call"] in self.ends:
                raise ValueError("observer tool end")
            self.ends.add(p["call"])
        elif kind == "bye":
            if (
                p["code"] > 255
                or p["rpc_count"] != len(self.observed_hashes)
                or p["spawn_count"] != len(self.spawns)
            ):
                raise ValueError("observer final counts")
            if p["code"] != 0:
                self.uncertain.add("Pi nonzero exit")
            self.bye = True
        else:
            child = self.spawns[p["pid"]]
            if kind == "root_exit":
                if (
                    child.root is not None
                    or (p["code"] is None) == (p["signal"] is None)
                    or (p["code"] is not None and p["code"] > 255)
                ):
                    raise ValueError("root status")
                child.root = p["code"], p["signal"]
                if p["group"] not in {"absent", "present", "unknown"}:
                    raise ValueError("group probe state")
                if (
                    p["signal"] is not None
                    or p["group"] == "unknown"
                    or (p["group"] == "present" and not self.profile.supervised)
                ):
                    self.uncertain.add("signal or surviving group")
                if self.profile.supervised:
                    pass
                elif p["group"] == "absent" and _group_absent(p["pid"]):
                    child.retired = True
                elif p["group"] == "absent":
                    self.uncertain.add("group identity discontinuity")
            elif kind in {"stream_end", "stream_destroy"}:
                name = p["name"]
                if name not in {"stdout", "stderr"}:
                    raise ValueError("observer stream name")
                if kind == "stream_end":
                    if name in child.ends:
                        raise ValueError("duplicate stream end")
                    child.ends.add(name)
                elif p["ended"] != (name in child.ends):
                    raise ValueError("stream end contradiction")
                elif not p["ended"]:
                    self.uncertain.add("premature stream destroy")
            elif kind == "child_close":
                if child.close is not None or child.root != (p["code"], p["signal"]):
                    raise ValueError("child close status")
                child.close = p["code"], p["signal"]
            else:
                self.uncertain.add("shell spawn error")

    def _cause(self, frames: list[Any]) -> str:
        if len(frames) > 12:
            raise ValueError("control stack bound")
        for frame in frames:
            _shape(frame, dict(file="text?", line="int?", column="int?"))
        expected = (self.profile.package / BUNDLE).as_uri()
        for index, frame in enumerate(frames[:-1]):
            if frame == dict(file=expected, line=611, column=1791):
                caller = frames[index + 1]
                if caller["file"] == expected:
                    return {(1107, 615): "timeout", (1107, 509): "abort"}.get(
                        (caller["line"], caller["column"]), "unknown"
                    )
        return "unknown"

    def disposed(self) -> bool:
        if (
            (self.profile.supervised and not self.supervisor_disposed)
            or not self.integrity
            or not self.hello
            or not self.bye
            or self.rpc_hashes != self.observed_hashes
            or set(self.starts) != self.ends
            or set(self.starts) != set(self.calls)
            or set(self.starts) != set(self.rpc_starts)
        ):
            return False
        for pid, child in self.spawns.items():
            if child.root is None:
                return False
            if not child.retired:
                if self.profile.supervised:
                    if not self.supervisor_disposed:
                        return False
                    child.retired = True
                    continue
                if not _group_absent(pid):
                    return False
                child.retired = True
        return True

    def finish(
        self,
        declared: dict[str, tuple[str, dict[str, Any]]],
        completed: dict[str, dict[str, Any]],
        results: set[str],
    ) -> bool:
        bash = {call: args for call, (name, args) in declared.items() if name == "bash"}
        valid = (
            self.integrity
            and self.hello
            and self.bye
            and self.rpc_hashes == self.observed_hashes
            and set(bash)
            == set(self.starts)
            == self.ends
            == set(self.calls)
            == set(self.rpc_starts)
            and set(bash) <= results
            and self.disposed()
        )
        for call, args in bash.items():
            child = self.spawns.get(self.calls.get(call, -1))
            start = self.rpc_starts.get(call, {})
            end = completed.get(call, {})
            if (
                set(args) - {"command", "timeout"}
                or not isinstance(args.get("command"), str)
                or start.get("args") != args
                or self.starts.get(call)
                != (command_digest(args.get("command", "")), args.get("timeout"))
                or child is None
                or child.root is None
                or child.root[0] is None
                or child.root[1] is not None
                or child.close != child.root
                or child.ends != {"stdout", "stderr"}
                or end.get("isError") is not (child.root[0] != 0)
            ):
                valid = False
        if not valid:
            self.uncertain.add("incomplete group evidence")
        return bool(valid and not self.uncertain)

    def diagnostic(self) -> dict[str, str | int]:
        facts = [
            dict(
                call=child.call,
                root=child.root,
                ends=sorted(child.ends),
                closed=child.close,
                retired=child.retired,
            )
            for child in self.spawns.values()
        ]
        raw = json.dumps(
            dict(calls=facts, integrity=self.integrity, complete=self.bye),
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            "scope": (
                "trusted_linux_owned_subreaper"
                if self.profile.supervised
                else "trusted_per_command_posix_groups"
            ),
            "bash_call_count": len(self.calls),
            "numeric_nonzero_count": sum(
                child.root is not None and child.root[0] not in (None, 0)
                for child in self.spawns.values()
            ),
            "observer_integrity": self.integrity,
            "observer_hello": self.hello,
            "observer_bye": self.bye,
            "rpc_hashes_match": self.rpc_hashes == self.observed_hashes,
            "call_sets_match": (
                set(self.starts) == self.ends == set(self.calls) == set(self.rpc_starts)
            ),
            "spawns_without_root_count": sum(
                child.root is None for child in self.spawns.values()
            ),
            "unretired_spawn_count": sum(
                not child.retired for child in self.spawns.values()
            ),
            "unretired_present_group_count": sum(
                not child.retired
                and (self.profile.supervised or not _group_absent(pid))
                for pid, child in self.spawns.items()
            ),
            "supervisor_disposed": self.supervisor_disposed,
            "witness_digest": command_digest(raw),
        }
