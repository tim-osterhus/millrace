"""Built-in Blueprint validator adapter.

Thin adapter that validates Blueprint artifacts using existing contract
model validation.  This preserves the existing validation behind the
extension-owned BlueprintValidator interface.

Maintenance guardrail: the underlying validation logic relies on Pydantic
model_validate.  When per-domain validator registrations are fully
supported, this adapter should delegate to domain-specific validators
registered through extension manifests.
"""

from __future__ import annotations

from millrace_ai.extensions.interfaces import BUILTIN_INTERFACE_IDS


class BuiltInBlueprintValidator:
    """Built-in Blueprint validator using existing Pydantic contract models."""

    interface_id: str = BUILTIN_INTERFACE_IDS["blueprint_validator"]
    domain: str = "blueprint"

    def validate_manifest(self, manifest: object) -> None:
        from millrace_ai.contracts import BlueprintManifestDocument

        if isinstance(manifest, BlueprintManifestDocument):
            return
        BlueprintManifestDocument.model_validate(manifest)

    def validate_draft(self, draft: object) -> None:
        from millrace_ai.contracts import BlueprintDraftDocument

        if isinstance(draft, BlueprintDraftDocument):
            return
        BlueprintDraftDocument.model_validate(draft)

    def validate_packet(self, packet: object) -> None:
        from millrace_ai.contracts import BlueprintPacketDocument

        if isinstance(packet, BlueprintPacketDocument):
            return
        BlueprintPacketDocument.model_validate(packet)

    def validate_evaluation(self, evaluation: object) -> None:
        from millrace_ai.contracts import BlueprintEvaluationDocument

        if isinstance(evaluation, BlueprintEvaluationDocument):
            return
        BlueprintEvaluationDocument.model_validate(evaluation)

    def validate_generated_task(self, task: object) -> None:
        from millrace_ai.contracts import TaskDocument

        if isinstance(task, TaskDocument):
            return
        TaskDocument.model_validate(task)


__all__ = ["BuiltInBlueprintValidator"]
