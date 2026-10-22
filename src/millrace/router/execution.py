"""Public, explicitly injected execution contract for the standalone router."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol, TypeAlias, cast

from .records import CommandResult as AdapterResult
from .records import RouterError

EXECUTION_CONTRACT = "millrace-router-execution/v2"
JSON_LIMIT = 1048576
GENERATED_LIMIT = 1114112
VALIDATION_ID = "0" * 32
VALIDATION_ROOT = Path("/__millrace_validation__/invocation")
JsonObject: TypeAlias = Mapping[str, object]

__all__ = [
    "EXECUTION_CONTRACT",
    "SeamInput",
    "AdapterConfiguration",
    "InvocationRequest",
    "LaunchPlan",
    "CompletionFacts",
    "ExecutionAdapter",
    "AdapterResolver",
    "AdapterResult",
    "RouterError",
    "JsonObject",
]


@dataclass(frozen=True)
class SeamInput:
    path: Path
    content: bytes
    sha256: str
    seam_id: str
    sequence: int
    producer_invocation_id: str


@dataclass(frozen=True)
class AdapterConfiguration:
    adapter_id: str
    adapter_contract: str
    bindings: Mapping[str, JsonObject]
    nodes: Mapping[tuple[str, str], tuple[str, JsonObject]]
    runtime: JsonObject
    workflow: JsonObject
    planes: Mapping[str, JsonObject]
    files: Mapping[str, bytes]
    host_paths: Mapping[str, str]
    environment_names: tuple[str, ...]


@dataclass(frozen=True)
class InvocationRequest:
    invocation_id: str
    plane_id: str
    node_id: str
    producing_revision: str
    binding_id: str
    adapter_id: str
    adapter_contract: str
    executable: str
    binding_config: JsonObject
    node_config: JsonObject
    runtime: JsonObject
    workflow: JsonObject
    plane: JsonObject
    common_settings: JsonObject
    files: Mapping[str, bytes]
    host_paths: Mapping[str, str]
    environment_names: tuple[str, ...]
    outcomes: tuple[str, ...]
    result_contract: str
    project_root: Path
    invocation_root: Path
    seam_input: SeamInput | None = None


@dataclass(frozen=True)
class LaunchPlan:
    invocation_id: str
    adapter_id: str
    adapter_contract: str
    argv: tuple[str, ...]
    environment: Mapping[str, str]
    generated_controls: Mapping[str, bytes]
    settings: JsonObject
    details: JsonObject


@dataclass(frozen=True)
class CompletionFacts:
    invocation_id: str
    pid: int | None
    stdout: bytes
    stderr: bytes
    transport: str
    writer_state: str
    diagnostic: str
    exit_code: int | None
    evidence: JsonObject | None
    origin: str


class ExecutionAdapter(Protocol):
    def validate_configuration(self, config: AdapterConfiguration) -> None: ...
    def plan(self, request: InvocationRequest) -> LaunchPlan: ...
    def invoke(
        self,
        plan: LaunchPlan,
        *,
        cwd: Path,
        env: Mapping[str, str],
        deadline: float,
        poll: Callable[[], bool],
        on_started: Callable[[int], None],
    ) -> AdapterResult: ...
    def validate_completion(
        self, plan: LaunchPlan, completion: CompletionFacts
    ) -> None: ...


AdapterResolver: TypeAlias = Callable[[str, str], ExecutionAdapter]


def _json(value: object) -> object:
    """Strict defensive JSON copy; also accepts read-only boundary mappings."""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        import math

        if math.isfinite(value):
            return value
    if isinstance(value, Mapping):
        if any(type(k) is not str for k in value):
            raise RouterError("invalid_launch_plan")
        return {k: _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    raise RouterError("invalid_launch_plan")


def _encoded(value: object) -> bytes:
    try:
        return json.dumps(
            _json(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError) as exc:
        raise RouterError("invalid_launch_plan") from exc


def _object(value: object) -> dict[str, object]:
    copied = _json(value)
    if not isinstance(copied, dict):
        raise RouterError("invalid_launch_plan")
    return cast(dict[str, object], copied)


def _adapter(
    adapter_id: str, contract: str, resolver: AdapterResolver | None
) -> ExecutionAdapter:
    if adapter_id == "command":
        if contract != "millrace-command/v1":
            raise RouterError("unsupported_adapter_contract")
        return _COMMAND
    if resolver is None:
        raise RouterError("unsupported_adapter_contract")
    return resolver(adapter_id, contract)


def _projection(plan: LaunchPlan) -> dict[str, object]:
    generated: dict[str, object] = {}
    size = 0
    destinations: set[PurePosixPath] = set()
    from .records import relative

    if not isinstance(plan.generated_controls, Mapping):
        raise RouterError("invalid_launch_plan")
    for name, raw in plan.generated_controls.items():
        if (
            not isinstance(name, str)
            or relative(name, owner_forbidden=True) != name
            or not Path(name).parts
            or name == "."
            or Path(name).as_posix() != name
        ):
            raise RouterError("invalid_launch_plan")
        path = PurePosixPath(unicodedata.normalize("NFC", name).casefold())
        if path.parts[0] in {"controls", "seam-input.json"}:
            raise RouterError("invalid_launch_plan")
        if any(
            path == previous
            or path.is_relative_to(previous)
            or previous.is_relative_to(path)
            for previous in destinations
        ):
            raise RouterError("invalid_launch_plan")
        destinations.add(path)
        if type(raw) is not bytes:
            raise RouterError("invalid_launch_plan")
        size += len(raw)
        if size > GENERATED_LIMIT:
            raise RouterError("invalid_launch_plan")
        generated[name] = {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    return generated


def _admit(plan: LaunchPlan, request: InvocationRequest) -> dict[str, object]:
    if not isinstance(plan, LaunchPlan) or (
        plan.invocation_id,
        plan.adapter_id,
        plan.adapter_contract,
    ) != (request.invocation_id, request.adapter_id, request.adapter_contract):
        raise RouterError("invalid_launch_plan")
    if (
        not isinstance(plan.argv, tuple)
        or not plan.argv
        or any(type(s) is not str or "\x00" in s for s in plan.argv)
        or not plan.argv[0]
    ):
        raise RouterError("invalid_launch_plan")
    if not isinstance(plan.environment, Mapping) or any(
        type(k) is not str or type(v) is not str or not k or "=" in k or "\x00" in k + v
        for k, v in plan.environment.items()
    ):
        raise RouterError("invalid_launch_plan")
    if "MILLRACE_SEAM_INPUT" in request.environment_names or set(plan.environment) & {
        *request.environment_names,
        "PATH",
        "MILLRACE_SEAM_INPUT",
    }:
        raise RouterError("invalid_launch_plan")
    generated = _projection(plan)
    seam_projection: dict[str, object] | None = None
    if request.seam_input is not None:
        import base64

        from .seams import validate_input

        validate_input(request.seam_input, request)
        seam = request.seam_input
        seam_projection = {
            "path": str(seam.path),
            "content": base64.b64encode(seam.content).decode(),
            "sha256": seam.sha256,
            "seam_id": seam.seam_id,
            "sequence": seam.sequence,
            "producer_invocation_id": seam.producer_invocation_id,
        }
    for value in (plan.argv, plan.environment, plan.settings, plan.details, generated):
        if len(_encoded(value)) > JSON_LIMIT:
            raise RouterError("invalid_launch_plan")
    return {
        "invocation_id": plan.invocation_id,
        "plane_id": request.plane_id,
        "seam_input": seam_projection,
        "adapter_id": plan.adapter_id,
        "adapter_contract": plan.adapter_contract,
        "argv": list(plan.argv),
        "environment": _object(plan.environment),
        "generated_controls": generated,
        "settings": _object(plan.settings),
        "details": _object(plan.details),
    }


def _result(facts: CompletionFacts) -> None:
    if (
        type(facts.stdout) is not bytes
        or type(facts.stderr) is not bytes
        or max(len(facts.stdout), len(facts.stderr)) > 65536
    ):
        raise RouterError("completion_integrity")
    if facts.transport not in {
        "completed",
        "execution_failure",
        "protocol_failure",
        "timeout",
        "cancelled",
        "interrupted",
    } or facts.writer_state not in {"stopped", "may_live", "unknown"}:
        raise RouterError("completion_integrity")
    if facts.exit_code is not None and type(facts.exit_code) is not int:
        raise RouterError("completion_integrity")
    if facts.transport == "completed" and (
        type(facts.exit_code) is not int or facts.exit_code != 0
    ):
        raise RouterError("completion_integrity")
    try:
        if (
            type(facts.diagnostic) is not str
            or len(facts.diagnostic.encode("utf-8")) > 65536
        ):
            raise RouterError("completion_integrity")
        if facts.evidence is not None:
            _object(facts.evidence)
    except (UnicodeError, RouterError) as exc:
        raise RouterError("completion_integrity") from exc
    if facts.origin == "adapter":
        if facts.transport == "interrupted":
            raise RouterError("completion_integrity")
    elif facts.origin == "core_preparation":
        if (
            facts.pid is not None
            or facts.transport != "execution_failure"
            or facts.writer_state != "stopped"
            or facts.exit_code is not None
            or facts.stdout
            or facts.stderr
            or facts.evidence is not None
            or facts.diagnostic
            not in {
                "input_missing",
                "invalid_cwd",
                "invalid_path",
                "invalid_workspace",
                "launch_preparation_failed",
                "workspace_identity_changed",
            }
        ):
            raise RouterError("completion_integrity")
    elif facts.origin == "core_interruption":
        if (
            facts.transport != "interrupted"
            or facts.writer_state != "unknown"
            or facts.exit_code is not None
            or facts.stdout
            or facts.stderr
            or facts.evidence is not None
        ):
            raise RouterError("completion_integrity")
    else:
        raise RouterError("completion_integrity")


class _Command:
    def validate_configuration(self, config: AdapterConfiguration) -> None:
        from .records import relative, strings

        if config.runtime or config.workflow or any(config.planes.values()):
            raise RouterError("invalid_adapter_settings")
        if any(config.bindings.values()):
            raise RouterError("invalid_adapter_settings")
        for _, node in config.nodes.values():
            obj = dict(node)
            if set(obj) - {"script", "args"}:
                raise RouterError("invalid_adapter_settings")
            if "script" in obj and relative(obj["script"]) not in config.files:
                raise RouterError("missing_control")
            strings(obj.get("args", []), allow_empty=True)

    def plan(self, request: InvocationRequest) -> LaunchPlan:
        from .records import strings

        script = request.node_config.get("script")
        argv = (
            (request.executable,)
            + (
                (str(request.invocation_root / "controls" / str(script)),)
                if script is not None
                else ()
            )
            + strings(request.node_config.get("args", []), allow_empty=True)
        )
        return LaunchPlan(
            request.invocation_id,
            request.adapter_id,
            request.adapter_contract,
            argv,
            {},
            {},
            {"requested": {}, "effective": {}, "provenance": {}, "warnings": []},
            {},
        )

    def invoke(
        self,
        plan: LaunchPlan,
        *,
        cwd: Path,
        env: Mapping[str, str],
        deadline: float,
        poll: Callable[[], bool],
        on_started: Callable[[int], None],
    ) -> AdapterResult:
        from .adapters.command import invoke

        return invoke(list(plan.argv), cwd, dict(env), deadline, poll, on_started)

    def validate_completion(
        self, plan: LaunchPlan, completion: CompletionFacts
    ) -> None:
        if completion.evidence is not None:
            raise RouterError("completion_integrity")


_COMMAND: ExecutionAdapter = _Command()
