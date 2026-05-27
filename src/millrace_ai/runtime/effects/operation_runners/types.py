"""Shared types for runtime-effect operation helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)
OperationErrorFactory = Callable[[str, str], Exception]

__all__ = [
    "ModelT",
    "OperationErrorFactory",
]
