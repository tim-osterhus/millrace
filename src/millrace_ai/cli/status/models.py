"""View models for CLI status output."""

from __future__ import annotations

from dataclasses import dataclass

from millrace_ai.compiler import CompiledPlanCurrentness
from millrace_ai.contracts import RuntimeSnapshot
from millrace_ai.events import RuntimeEventRecord
from millrace_ai.paths import WorkspacePaths
from millrace_ai.runtime.usage_governance import UsageGovernanceState
from millrace_ai.workspace.baseline import BaselineManifest


@dataclass(frozen=True, slots=True)
class StatusViewModel:
    """Collected status data before text or JSON rendering."""

    paths: WorkspacePaths
    snapshot: RuntimeSnapshot
    baseline_manifest: BaselineManifest | None
    compile_currentness: CompiledPlanCurrentness | None
    compile_currentness_error: str | None
    runtime_ownership_lock: str
    process_running: bool
    queue_depths: dict[str, int]
    queue_depths_by_family: dict[str, int]
    closure_status: dict[str, object]
    extension_statuses: dict[str, dict[str, object]]
    blueprint_status: dict[str, object]
    latest_runtime_error_report_path: str | None
    latest_runtime_failure_origin: str | None
    latest_operator_intervention: RuntimeEventRecord | None
    latest_runtime_effect: dict[str, str]
    work_item_families: list[dict[str, object]]
    usage_governance_config_enabled: bool
    usage_governance_state: UsageGovernanceState
    blocked_idle: bool


__all__ = ["StatusViewModel"]
