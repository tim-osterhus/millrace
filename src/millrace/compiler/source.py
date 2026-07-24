"""Source access helpers for compiler passes.

This module owns untyped source coercion only. It must not validate workflow
semantics or construct selected authority.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypeGuard

from millrace.contracts.compiled_plan import AuthorityValue, freeze_authority_mapping

SourceRecord = Mapping[str, object]


def records(source: Mapping[str, object], key: str) -> tuple[SourceRecord, ...]:
    raw_records = source.get(key, ())
    if not is_sequence(raw_records):
        return ()
    return tuple(mapping(item) for item in raw_records)


def mapping(value: object) -> SourceRecord:
    if isinstance(value, Mapping):
        return value
    return {}


def authority_mapping(value: object) -> Mapping[str, AuthorityValue]:
    return freeze_authority_mapping(mapping(value))


def text_tuple(value: object) -> tuple[str, ...]:
    if not is_sequence(value):
        return ()
    return tuple(str(item) for item in value if is_non_empty_text(item))


def is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def is_non_empty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


__all__ = (
    "SourceRecord",
    "authority_mapping",
    "is_non_empty_text",
    "is_sequence",
    "mapping",
    "records",
    "text_tuple",
)
