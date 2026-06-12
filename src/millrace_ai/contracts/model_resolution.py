"""Shared helpers for resolving contract models from schema ids."""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel

from millrace_ai import contracts as _contracts_module

_SCHEMA_ID_TO_MODEL_NAME: dict[str, str] = {
    "incident_document_v1": "IncidentDocument",
    "learning_request_document_v1": "LearningRequestDocument",
    "planner_disposition_document_v1": "PlannerDispositionDocument",
    "probe_document_v1": "ProbeDocument",
    "recon_packet_document_v1": "ReconPacketDocument",
    "spec_document_v1": "SpecDocument",
    "stage_result_envelope_v1": "StageResultEnvelope",
    "task_document_v1": "TaskDocument",
    "blueprint_critique_document_v1": "BlueprintCritiqueDocument",
    "blueprint_draft_document_v1": "BlueprintDraftDocument",
    "blueprint_evaluation_document_v1": "BlueprintEvaluationDocument",
    "blueprint_manifest_document_v1": "BlueprintManifestDocument",
    "blueprint_packet_document_v1": "BlueprintPacketDocument",
    "blueprint_promotion_record_v1": "BlueprintPromotionRecord",
    "blueprint_repair_decision_document_v1": "BlueprintRepairDecisionDocument",
}


def schema_id_to_model_name(schema_id: str) -> str:
    return _SCHEMA_ID_TO_MODEL_NAME.get(schema_id, "")


@lru_cache(maxsize=None)
def resolve_contract_model(schema_id: str) -> type[BaseModel] | None:
    model_name = schema_id_to_model_name(schema_id)
    if not model_name:
        return None
    try:
        model = getattr(_contracts_module, model_name)
    except AttributeError:
        return None
    if isinstance(model, type) and issubclass(model, BaseModel):
        return model
    return None
