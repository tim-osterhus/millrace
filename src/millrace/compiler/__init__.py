"""Public compiler API."""

from millrace.compiler.canonical import (
    AUTHORITY_FINGERPRINT_DOMAIN_PREFIX,
    CanonicalAuthorityError,
    authority_fingerprint,
    canonical_authority_bytes,
)
from millrace.compiler.compile import CompileResult, compile_workflow
from millrace.compiler.export import (
    CompiledPlanExportError,
    VerifiedCompiledPlanExport,
    compiled_plan_export_bytes,
    compiled_plan_export_record,
    verify_compiled_plan_export_bytes,
    verify_compiled_plan_export_record,
)
from millrace.compiler.runner_bindings import (
    DEFAULT_SELECTED_RUNNER_ADAPTER_KIND,
    DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY,
    RUNNER_ADAPTER_KIND_DEFAULTED,
    SelectedRunnerAdapterPolicy,
)

__all__ = (
    "AUTHORITY_FINGERPRINT_DOMAIN_PREFIX",
    "CanonicalAuthorityError",
    "CompiledPlanExportError",
    "CompileResult",
    "DEFAULT_SELECTED_RUNNER_ADAPTER_KIND",
    "DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY",
    "VerifiedCompiledPlanExport",
    "RUNNER_ADAPTER_KIND_DEFAULTED",
    "SelectedRunnerAdapterPolicy",
    "authority_fingerprint",
    "canonical_authority_bytes",
    "compile_workflow",
    "compiled_plan_export_bytes",
    "compiled_plan_export_record",
    "verify_compiled_plan_export_bytes",
    "verify_compiled_plan_export_record",
)
