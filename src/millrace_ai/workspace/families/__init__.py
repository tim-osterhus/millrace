"""Built-in work-family queue adapter registrations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .blueprint import blueprint_draft_queue_family_adapter
from .builtin import builtin_queue_family_adapters

if TYPE_CHECKING:
    from ..family_adapters import WorkFamilyQueueAdapter


def builtin_work_family_queue_adapters() -> tuple["WorkFamilyQueueAdapter", ...]:
    return (
        *builtin_queue_family_adapters(),
        blueprint_draft_queue_family_adapter(),
    )


__all__ = ["builtin_work_family_queue_adapters"]
