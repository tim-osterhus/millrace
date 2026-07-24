"""CLI result envelopes and JSON/text presentation helpers."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from enum import IntEnum
from typing import Any, TextIO, cast


class ExitCode(IntEnum):
    SUCCESS = 0
    INTERNAL_ERROR = 1
    CLI_USAGE = 2
    DOMAIN_REFUSAL = 3
    PERSISTENCE_FAILURE = 4
    RUNNER_FAILURE = 5


def _empty_object() -> dict[str, object]:
    return {}


@dataclass(frozen=True, slots=True)
class CliSuccess:
    command: str
    code: str
    message: str
    data: dict[str, object] = field(default_factory=_empty_object)


@dataclass(frozen=True, slots=True)
class CliError:
    command: str
    code: str
    message: str
    exit_code: ExitCode
    details: dict[str, object] = field(default_factory=_empty_object)


def success_result(
    *,
    command: str,
    code: str,
    message: str,
    data: dict[str, object] | None = None,
) -> CliSuccess:
    return CliSuccess(
        command=command,
        code=code,
        message=message,
        data=data or {},
    )


def error_result(
    *,
    command: str,
    code: str,
    message: str,
    exit_code: ExitCode,
    details: dict[str, object] | None = None,
) -> CliError:
    return CliError(
        command=command,
        code=code,
        message=message,
        exit_code=exit_code,
        details=details or {},
    )


def render_success(
    result: CliSuccess,
    *,
    json_mode: bool,
    stream: TextIO | None = None,
) -> int:
    output = stream if stream is not None else sys.stdout
    if json_mode:
        _write_json(
            {
                "ok": True,
                "command": result.command,
                "code": result.code,
                "message": result.message,
                "data": result.data,
            },
            output,
        )
    else:
        output.write(f"{result.message}\n")
    return int(ExitCode.SUCCESS)


def render_error(
    error: CliError,
    *,
    json_mode: bool,
    stream: TextIO | None = None,
) -> int:
    output = stream if stream is not None else sys.stderr
    if json_mode:
        _write_json(
            {
                "ok": False,
                "command": error.command,
                "code": error.code,
                "message": error.message,
                "details": error.details,
            },
            output,
        )
    else:
        output.write(f"{error.code}: {error.message}\n")
    return int(error.exit_code)


def _write_json(payload: dict[str, object], stream: TextIO) -> None:
    json.dump(payload, stream, separators=(",", ":"), sort_keys=False)
    stream.write("\n")


def json_ready(value: object) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Mapping):
        return {str(key): json_ready(nested) for key, nested in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_ready(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: json_ready(getattr(value, field.name))
            for field in fields(cast(Any, value))
        }
    return str(value)


def diagnostic_projections(diagnostics: tuple[object, ...]) -> list[dict[str, object]]:
    return [diagnostic_projection(item) for item in diagnostics]


def diagnostic_projection(diagnostic: object) -> dict[str, object]:
    projection = {
        "code": getattr(diagnostic, "code"),
        "message": getattr(diagnostic, "message"),
    }
    severity = getattr(diagnostic, "severity", None)
    if severity is not None:
        projection["severity"] = severity
        projection["phase"] = getattr(diagnostic, "phase")
        projection["declaration_path"] = getattr(diagnostic, "declaration_path")
        projection["hint"] = getattr(diagnostic, "hint")
        projection["context"] = json_ready(getattr(diagnostic, "context"))
    return projection
