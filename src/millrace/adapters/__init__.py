"""Environment-facing adapter contracts."""

from millrace.adapters.runner_contract import (
    AdapterErrorResult,
    AdapterEvidenceConversionError,
    AdapterInvocationOutcome,
    AdapterInvocationRequest,
    AdapterLocalConfig,
    AdapterResolverError,
    AdapterSuccessResult,
    DispatchEcho,
    RedactionPolicy,
    RunnerAdapter,
    resolve_adapter,
    runner_evidence_from_adapter_outcome,
)

__all__ = (
    "AdapterEvidenceConversionError",
    "AdapterErrorResult",
    "AdapterInvocationOutcome",
    "AdapterInvocationRequest",
    "AdapterLocalConfig",
    "AdapterResolverError",
    "AdapterSuccessResult",
    "DispatchEcho",
    "RedactionPolicy",
    "RunnerAdapter",
    "resolve_adapter",
    "runner_evidence_from_adapter_outcome",
)
