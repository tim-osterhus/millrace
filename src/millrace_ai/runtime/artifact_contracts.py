"""Runtime artifact resolution through compiled artifact contracts."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel

from millrace_ai.architecture import (
    ArtifactContractDefinition,
    ArtifactFilenameAdapterDefinition,
)
from millrace_ai.assets import discover_artifact_contract_definitions
from millrace_ai.contracts import (
    BlueprintCritiqueDocument,
    BlueprintDraftDocument,
    BlueprintEvaluationDocument,
    BlueprintManifestDocument,
    BlueprintPacketDocument,
    BlueprintRepairDecisionDocument,
    IncidentDocument,
    LearningRequestDocument,
    PlannerDispositionDocument,
    ProbeDocument,
    ReconPacketDocument,
    SpecDocument,
    StageResultEnvelope,
    TaskDocument,
)
from millrace_ai.errors import QueueStateError
from millrace_ai.workspace.work_documents import parse_work_document_as

if TYPE_CHECKING:
    from millrace_ai.architecture import CompiledRunPlan

DeclaredArtifactT = TypeVar("DeclaredArtifactT", bound=object)


@dataclass(frozen=True, slots=True)
class ResolvedRunArtifact:
    artifact_id: str
    path: Path
    contract: ArtifactContractDefinition
    adapter: ArtifactFilenameAdapterDefinition


class RuntimeArtifactError(QueueStateError):
    """Raised when a declared runtime artifact is missing or invalid."""

    def __init__(
        self,
        *,
        artifact_id: str,
        selected_filename: str,
        expected_format: str,
        failure_class: str,
        message: str,
    ) -> None:
        self.artifact_id = artifact_id
        self.selected_filename = selected_filename
        self.expected_format = expected_format
        self.failure_class = failure_class
        super().__init__(
            f"artifact_id={artifact_id} selected_filename={selected_filename} "
            f"expected_format={expected_format} failure_class={failure_class}: {message}"
        )


def resolve_run_artifact(
    compiled_plan: CompiledRunPlan | None,
    artifact_id: str,
    run_dir: Path,
) -> ResolvedRunArtifact:
    """Select the authoritative run artifact path for a declared artifact id."""

    contract = _artifact_contract(compiled_plan, artifact_id)
    canonical_path = run_dir / contract.canonical_filename
    if canonical_path.exists():
        return _resolved_artifact(artifact_id, canonical_path, contract)

    for filename in contract.accepted_filenames:
        candidate = run_dir / filename
        if candidate.exists():
            return _resolved_artifact(artifact_id, candidate, contract)

    expected = ", ".join(contract.all_filenames)
    raise RuntimeArtifactError(
        artifact_id=artifact_id,
        selected_filename="<none>",
        expected_format=_format_value(contract.preferred_format),
        failure_class="artifact_missing",
        message=f"no declared run artifact found in {run_dir}; expected one of: {expected}",
    )


def parse_declared_artifact(contract: ArtifactContractDefinition, path: Path) -> Any:
    """Parse a selected run artifact using its declared per-filename adapter."""

    adapter = contract.filename_adapters_by_name.get(path.name)
    if adapter is None:
        expected = ", ".join(contract.all_filenames)
        raise RuntimeArtifactError(
            artifact_id=contract.artifact_id,
            selected_filename=path.name,
            expected_format=_format_value(contract.preferred_format),
            failure_class="artifact_filename_unsupported",
            message=f"filename is not declared by contract; expected one of: {expected}",
        )

    if adapter.parser_id == "builtin.json":
        return _parse_json_model(contract, path, adapter)
    if adapter.parser_id == "builtin.markdown":
        return _parse_markdown_artifact(contract, path, adapter)

    raise RuntimeArtifactError(
        artifact_id=contract.artifact_id,
        selected_filename=path.name,
        expected_format=_format_value(adapter.format),
        failure_class="artifact_parser_unsupported",
        message=f"unsupported parser_id={adapter.parser_id} schema_id={contract.schema_id}",
    )


def parse_resolved_run_artifact(resolved: ResolvedRunArtifact) -> Any:
    return parse_declared_artifact(resolved.contract, resolved.path)


def parse_resolved_run_artifact_as(
    resolved: ResolvedRunArtifact,
    model: type[DeclaredArtifactT],
) -> DeclaredArtifactT:
    parsed = parse_resolved_run_artifact(resolved)
    if not isinstance(parsed, model):
        raise RuntimeArtifactError(
            artifact_id=resolved.artifact_id,
            selected_filename=resolved.path.name,
            expected_format=_format_value(resolved.adapter.format),
            failure_class="artifact_model_mismatch",
            message=f"expected model {model.__name__}, got {type(parsed).__name__}",
        )
    return parsed


def _resolved_artifact(
    artifact_id: str,
    path: Path,
    contract: ArtifactContractDefinition,
) -> ResolvedRunArtifact:
    adapter = contract.filename_adapters_by_name[path.name]
    return ResolvedRunArtifact(
        artifact_id=artifact_id,
        path=path,
        contract=contract,
        adapter=adapter,
    )


def _artifact_contract(
    compiled_plan: CompiledRunPlan | None,
    artifact_id: str,
) -> ArtifactContractDefinition:
    contracts_by_id = (
        compiled_plan.artifact_contracts_by_id
        if compiled_plan is not None and compiled_plan.artifact_contracts_by_id
        else _packaged_artifact_contracts_by_id()
    )
    contract = contracts_by_id.get(artifact_id)
    if contract is None:
        raise RuntimeArtifactError(
            artifact_id=artifact_id,
            selected_filename="<none>",
            expected_format="<unknown>",
            failure_class="artifact_contract_missing",
            message="compiled plan does not declare artifact contract",
        )
    return contract


@lru_cache(maxsize=1)
def _packaged_artifact_contracts_by_id() -> dict[str, ArtifactContractDefinition]:
    return {
        contract.artifact_id: contract
        for contract in discover_artifact_contract_definitions()
    }


def _parse_json_model(
    contract: ArtifactContractDefinition,
    path: Path,
    adapter: ArtifactFilenameAdapterDefinition,
) -> BaseModel:
    model = _model_for_schema_id(contract)
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise _parse_error(contract, path, adapter, "json_model_parse", exc) from exc


def _parse_markdown_artifact(
    contract: ArtifactContractDefinition,
    path: Path,
    adapter: ArtifactFilenameAdapterDefinition,
) -> BaseModel:
    model = _model_for_schema_id(contract)
    try:
        raw = path.read_text(encoding="utf-8")
        if model is IncidentDocument:
            return parse_work_document_as(raw, model=IncidentDocument, path=path)
        if model is LearningRequestDocument:
            return parse_work_document_as(raw, model=LearningRequestDocument, path=path)
        if model is ProbeDocument:
            return parse_work_document_as(raw, model=ProbeDocument, path=path)
        if model is SpecDocument:
            return parse_work_document_as(raw, model=SpecDocument, path=path)
        if model is TaskDocument:
            return parse_work_document_as(raw, model=TaskDocument, path=path)
        if model is ReconPacketDocument:
            from millrace_ai.recon_packets import parse_recon_packet as _parse_recon_packet

            return _parse_recon_packet(raw, path=path)
    except Exception as exc:
        raise _parse_error(contract, path, adapter, "markdown_parse", exc) from exc

    raise RuntimeArtifactError(
        artifact_id=contract.artifact_id,
        selected_filename=path.name,
        expected_format=_format_value(adapter.format),
        failure_class="artifact_parser_unsupported",
        message=f"no markdown parser for schema_id={contract.schema_id}",
    )


def _parse_error(
    contract: ArtifactContractDefinition,
    path: Path,
    adapter: ArtifactFilenameAdapterDefinition,
    failure_class: str,
    exc: Exception,
) -> RuntimeArtifactError:
    return RuntimeArtifactError(
        artifact_id=contract.artifact_id,
        selected_filename=path.name,
        expected_format=_format_value(adapter.format),
        failure_class=failure_class,
        message=f"parser_id={adapter.parser_id} schema_id={contract.schema_id}: {exc}",
    )


def _model_for_schema_id(contract: ArtifactContractDefinition) -> type[BaseModel]:
    model = _MODEL_BY_SCHEMA_ID.get(contract.schema_id)
    if model is None:
        raise RuntimeArtifactError(
            artifact_id=contract.artifact_id,
            selected_filename=contract.canonical_filename,
            expected_format=_format_value(contract.preferred_format),
            failure_class="artifact_model_unsupported",
            message=f"no model mapping for schema_id={contract.schema_id}",
        )
    return model


def _format_value(value: object) -> str:
    return str(getattr(value, "value", value))


_MODEL_BY_SCHEMA_ID: dict[str, type[BaseModel]] = {
    "blueprint_critique_document_v1": BlueprintCritiqueDocument,
    "blueprint_draft_document_v1": BlueprintDraftDocument,
    "blueprint_evaluation_document_v1": BlueprintEvaluationDocument,
    "blueprint_manifest_document_v1": BlueprintManifestDocument,
    "blueprint_packet_document_v1": BlueprintPacketDocument,
    "blueprint_repair_decision_document_v1": BlueprintRepairDecisionDocument,
    "incident_document_v1": IncidentDocument,
    "learning_request_document_v1": LearningRequestDocument,
    "planner_disposition_document_v1": PlannerDispositionDocument,
    "probe_document_v1": ProbeDocument,
    "recon_packet_document_v1": ReconPacketDocument,
    "spec_document_v1": SpecDocument,
    "stage_result_envelope_v1": StageResultEnvelope,
    "task_document_v1": TaskDocument,
}


__all__ = [
    "ResolvedRunArtifact",
    "RuntimeArtifactError",
    "parse_declared_artifact",
    "parse_resolved_run_artifact",
    "parse_resolved_run_artifact_as",
    "resolve_run_artifact",
]
