"""Built-in generic artifact adapter.

Thin adapter over runtime artifact contract loading.  Currently
delegates to workspace-level document loaders.

Maintenance guardrail: replace with extension-owned artifact adapter
implementations when per-domain artifact contracts are migrated.
"""

from __future__ import annotations

from pathlib import Path

from millrace_ai.extensions.interfaces import BUILTIN_INTERFACE_IDS


class GenericBuiltInArtifactAdapter:
    """Built-in artifact adapter that delegates to workspace-level loaders."""

    interface_id: str = BUILTIN_INTERFACE_IDS["artifact_adapter.generic"]
    domain: str = "generic"
    artifact_contract_id: str = "generic.default"

    def load_artifact(
        self,
        artifact_path: Path,
        *,
        workspace_root: Path,
    ) -> object:
        if not artifact_path.exists():
            raise FileNotFoundError(f"artifact not found: {artifact_path}")
        if artifact_path.suffix == ".json":
            import json

            return json.loads(artifact_path.read_text(encoding="utf-8"))
        return artifact_path.read_text(encoding="utf-8")

    def validate_artifact(
        self,
        artifact: object,
        *,
        workspace_root: Path,
    ) -> None:
        # Currently no-op; validators are domain-specific.
        pass


__all__ = ["GenericBuiltInArtifactAdapter"]
