"""String-backed terminal outcome contracts."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import core_schema


class TerminalOutcome(str):
    """Terminal outcome value with enum-like `.value` compatibility."""

    def __new__(cls, value: str | Enum) -> "TerminalOutcome":
        raw_value = value.value if isinstance(value, Enum) else value
        if not isinstance(raw_value, str):
            raise TypeError("terminal outcome must be a string")
        if not raw_value:
            raise ValueError("terminal outcome must be non-empty")
        return str.__new__(cls, raw_value)

    @property
    def value(self) -> str:
        return str(self)

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        _source_type: Any,
        _handler: GetCoreSchemaHandler,
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_after_validator_function(
            cls,
            core_schema.union_schema(
                [
                    core_schema.is_instance_schema(cls),
                    core_schema.str_schema(),
                ]
            ),
            serialization=core_schema.plain_serializer_function_ser_schema(
                str,
                return_schema=core_schema.str_schema(),
            ),
        )

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        _core_schema: core_schema.CoreSchema,
        _handler: GetJsonSchemaHandler,
    ) -> JsonSchemaValue:
        return {"type": "string"}


def terminal_outcome_value(value: str | Enum | TerminalOutcome) -> str:
    """Return the normalized terminal outcome string for comparisons."""

    return value.value if isinstance(value, (Enum, TerminalOutcome)) else value


TerminalResult = TerminalOutcome


__all__ = ["TerminalOutcome", "TerminalResult", "terminal_outcome_value"]
