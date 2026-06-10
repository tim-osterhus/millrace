"""Built-in generic context provider adapter.

Thin adapter that delegates to the existing runtime context provider
registrations.  This is a compatibility facade that preserves the
existing runtime behavior behind the extension-owned ContextProvider
interface.

Maintenance guardrail: replace this adapter with a fully extension-owned
implementation when context provider resolution is migrated from direct
module imports to the extension boundary registry.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.extensions.interfaces import BUILTIN_INTERFACE_IDS

if TYPE_CHECKING:
    from millrace_ai.architecture import CompiledRunPlan
    from millrace_ai.runners import StageRunRequest


class GenericBuiltInContextProvider:
    """Built-in generic context provider that delegates to the existing
    runtime/context/providers.py registration path."""

    interface_id: str = BUILTIN_INTERFACE_IDS["context_provider.generic"]
    domain: str = "generic"

    def build_context_plan(
        self,
        workspace_root: Path,
        request: "StageRunRequest",
        authority: object,
        compiled_plan: "CompiledRunPlan | None",
    ) -> object:
        from millrace_ai.runtime.context.generic import generic_active_work_item_context_plan

        return generic_active_work_item_context_plan(
            workspace_root, request, authority, compiled_plan
        )


__all__ = ["GenericBuiltInContextProvider"]
