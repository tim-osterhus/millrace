"""Deterministic workspace bootstrap and health reporting."""

from __future__ import annotations

import tomllib
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, ValidationError, field_validator

from .assets.resolver import AssetResolutionError, AssetResolver, AssetSourceKind
from .baseline_assets import packaged_baseline_bundle_version
from .compounding.integrity import CompoundingIntegrityStatus, build_compounding_integrity_report
from .config import LoadedConfig, build_runtime_paths, load_engine_config
from .contracts import (
    ContractModel,
    ExecutionStatus,
    ResearchMode,
    RunnerKind,
    SpecInterviewPolicy,
    StageType,
)
from .paths import RuntimePaths
from .policies.sizing import SizeStatusError, parse_size_status
from .policies.transport import DefaultTransportProbe, TransportProbeContext, TransportReadiness
from .runner import runner_executable_name
from .standard_runtime import preview_execution_runtime_selection
from .standard_runtime_models import RuntimeSelectionView, StageExecutionBindingView
from .status import ControlPlane, StatusError, StatusStore


class HealthCheckStatus(str, Enum):
    """Deterministic health-check status."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class WorkspaceHealthCheck(ContractModel):
    """One deterministic workspace health check."""

    check_id: str
    category: Literal["config", "workspace", "assets", "execution", "knowledge"]
    status: HealthCheckStatus
    message: str
    details: tuple[str, ...] = ()

    @field_validator("check_id", "message")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("health-check text may not be empty")
        return normalized

    @field_validator("details", mode="before")
    @classmethod
    def normalize_details(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        details: list[str] = []
        seen: set[str] = set()
        for item in value:
            normalized = " ".join(str(item).strip().split())
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            details.append(normalized)
        return tuple(details)


class WorkspaceHealthSummary(ContractModel):
    """Aggregated workspace-health counts."""

    total_checks: int = Field(ge=0)
    passed_checks: int = Field(ge=0)
    warning_checks: int = Field(ge=0)
    failed_checks: int = Field(ge=0)


class ResearchBootstrapReport(ContractModel):
    """Typed first-run research contract surfaced in preflight output."""

    source: Literal["config", "unresolved"]
    contract_state: Literal["stubbed", "active", "unknown"]
    mode: ResearchMode | None = None
    interview_policy: SpecInterviewPolicy | None = None
    summary: str
    next_step: str

    @field_validator("summary", "next_step")
    @classmethod
    def normalize_copy(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("research bootstrap text may not be empty")
        return normalized


class WorkspaceHealthReport(ContractModel):
    """Machine-readable workspace bootstrap and health report."""

    config_path: Path
    workspace_root: Path
    workspace_root_source: Literal["loaded_config", "config_path_parent"]
    config_source_kind: Literal["native_toml", "unresolved"]
    bundle_version: str
    status: HealthCheckStatus
    ok: bool
    bootstrap_ready: bool
    execution_ready: bool
    research_bootstrap: ResearchBootstrapReport
    summary: WorkspaceHealthSummary
    runner_prerequisites: tuple["RunnerPrerequisiteReport", ...] = ()
    checks: tuple[WorkspaceHealthCheck, ...]

    @field_validator("config_path", "workspace_root", mode="before")
    @classmethod
    def normalize_path_fields(cls, value: str | Path) -> Path:
        return Path(value)


class RunnerPrerequisiteReport(ContractModel):
    """Execution-readiness status for one configured external runner."""

    runner: RunnerKind
    executable: str
    available: bool
    message: str
    resolved_path: Path | None = None
    affected_stage_nodes: tuple[str, ...] = ()
    affected_stages: tuple[StageType, ...] = ()
    details: tuple[str, ...] = ()

    @field_validator("resolved_path", mode="before")
    @classmethod
    def normalize_resolved_path(cls, value: str | Path | None) -> Path | None:
        if value is None:
            return None
        return Path(value)


_CORE_WORKSPACE_FILES: tuple[str, ...] = (
    "agents/engine_events.log",
    "agents/historylog.md",
    "agents/size_status.md",
    "agents/research_status.md",
    "agents/status.md",
    "agents/tasks.md",
    "agents/tasksarchive.md",
    "agents/tasksbackburner.md",
    "agents/tasksbacklog.md",
    "agents/tasksblocker.md",
    "agents/taskspending.md",
)


def _single_line_message(value: object) -> str:
    text = str(value).strip()
    if not text:
        return ""
    return " ".join(line.strip() for line in text.splitlines() if line.strip())


def _validation_error_message(exc: ValidationError) -> str:
    details: list[str] = []
    for error in exc.errors(include_url=False):
        location = ".".join(str(token) for token in error.get("loc", ()))
        message = str(error.get("msg", "invalid value")).strip()
        details.append(f"{location}: {message}" if location else message)
    if details:
        return "; ".join(details)
    fallback = _single_line_message(exc)
    return fallback or "validation failed"


def _config_failure_detail(exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError):
        return _single_line_message(exc)
    if isinstance(exc, tomllib.TOMLDecodeError):
        return f"config TOML is invalid: {_single_line_message(exc)}"
    if isinstance(exc, ValidationError):
        return f"config validation failed: {_validation_error_message(exc)}"
    if isinstance(exc, ValueError):
        return _single_line_message(exc)
    return _single_line_message(exc) or "workspace health could not load config"


def _display_path(path: Path, *, workspace_root: Path) -> str:
    try:
        return path.relative_to(workspace_root).as_posix()
    except ValueError:
        return path.as_posix()


def _manifest_workspace_path(
    relative_path: str,
    *,
    workspace_root: Path,
    agents_dir: Path,
) -> Path:
    path = PurePosixPath(relative_path)
    if path.parts and path.parts[0] == "agents":
        return agents_dir.joinpath(*path.parts[1:])
    return workspace_root.joinpath(*path.parts)


def _required_workspace_directories(paths: RuntimePaths) -> tuple[Path, ...]:
    return (
        paths.agents_dir,
        paths.runtime_dir,
        paths.queue_lock_file.parent,
        paths.deferred_dir,
        paths.diagnostics_dir,
        paths.historylog_dir,
        paths.ideas_dir,
        paths.ideas_raw_dir,
        paths.runs_dir,
    )


def _config_surface_files(
    *,
    config_path: Path,
    workspace_root: Path,
    loaded: LoadedConfig | None,
) -> tuple[tuple[str, Path], ...]:
    items: list[tuple[str, Path]] = []
    if loaded is not None:
        items.append(("config", loaded.source.primary_path))
    elif config_path.exists():
        items.append(("config", config_path))
    return tuple(items)


def _required_workspace_file_targets(
    *,
    config_path: Path,
    workspace_root: Path,
    agents_dir: Path,
    loaded: LoadedConfig | None,
) -> tuple[tuple[str, Path], ...]:
    items = list(_config_surface_files(config_path=config_path, workspace_root=workspace_root, loaded=loaded))
    items.extend(
        (
            relative_path,
            _manifest_workspace_path(relative_path, workspace_root=workspace_root, agents_dir=agents_dir),
        )
        for relative_path in _CORE_WORKSPACE_FILES
    )
    return tuple(items)


def _required_asset_targets(
    *,
    paths: RuntimePaths,
    loaded: LoadedConfig | None,
) -> tuple[tuple[str, Path], ...]:
    items: list[tuple[str, Path]] = [
        ("audit completion manifest", paths.audit_completion_manifest_file),
        ("audit strict contract", paths.audit_strict_contract_file),
        ("objective contract", paths.objective_contract_file),
        ("staging manifest", paths.staging_manifest_file),
    ]
    if loaded is not None:
        items.extend(
            (f"stage prompt {stage.value}", stage_config.prompt_file)
            for stage, stage_config in sorted(loaded.config.stages.items(), key=lambda item: item[0].value)
        )
    return tuple(items)


def _build_config_load_check(
    *,
    config_path: Path,
) -> tuple[WorkspaceHealthCheck, LoadedConfig | None, Path, Path, RuntimePaths]:
    workspace_root = config_path.parent
    agents_dir = workspace_root / "agents"
    paths = RuntimePaths.from_workspace(workspace_root, agents_dir)
    try:
        loaded = load_engine_config(config_path)
    except (FileNotFoundError, tomllib.TOMLDecodeError, ValidationError, ValueError) as exc:
        return (
            WorkspaceHealthCheck(
                check_id="config.load",
                category="config",
                status=HealthCheckStatus.FAIL,
                message="workspace config did not load cleanly",
                details=(f"error: {_config_failure_detail(exc)}",),
            ),
            None,
            workspace_root,
            agents_dir,
            paths,
        )

    loaded_paths = build_runtime_paths(loaded.config)
    return (
        WorkspaceHealthCheck(
            check_id="config.load",
            category="config",
            status=HealthCheckStatus.PASS,
            message=f"workspace config loaded from {loaded.source.kind}",
            details=(
                f"primary_path: {loaded.source.primary_path.as_posix()}",
            ),
        ),
        loaded,
        loaded_paths.root,
        loaded_paths.agents_dir,
        loaded_paths,
    )


def _build_required_directories_check(
    *,
    workspace_root: Path,
    paths: RuntimePaths,
) -> WorkspaceHealthCheck:
    issues: list[str] = []
    targets = _required_workspace_directories(paths)
    for target in targets:
        display_path = _display_path(target, workspace_root=workspace_root)
        if not target.exists():
            issues.append(f"missing directory: {display_path}")
        elif not target.is_dir():
            issues.append(f"not a directory: {display_path}")
    if issues:
        return WorkspaceHealthCheck(
            check_id="workspace.directories",
            category="workspace",
            status=HealthCheckStatus.FAIL,
            message=f"required workspace directories are missing or invalid ({len(issues)} issue(s))",
            details=issues,
        )
    return WorkspaceHealthCheck(
        check_id="workspace.directories",
        category="workspace",
        status=HealthCheckStatus.PASS,
        message=(
            "required workspace directories are present "
            f"(count={len(targets)})"
        ),
    )


def _build_required_files_check(
    *,
    config_path: Path,
    workspace_root: Path,
    agents_dir: Path,
    loaded: LoadedConfig | None,
) -> WorkspaceHealthCheck:
    issues: list[str] = []
    targets = _required_workspace_file_targets(
        config_path=config_path,
        workspace_root=workspace_root,
        agents_dir=agents_dir,
        loaded=loaded,
    )
    for label, target in targets:
        display_path = _display_path(target, workspace_root=workspace_root)
        if not target.exists():
            issues.append(f"missing file: {display_path} ({label})")
        elif not target.is_file():
            issues.append(f"not a file: {display_path} ({label})")
    if issues:
        return WorkspaceHealthCheck(
            check_id="workspace.files",
            category="workspace",
            status=HealthCheckStatus.FAIL,
            message=f"required workspace files are missing or invalid ({len(issues)} issue(s))",
            details=issues,
        )
    return WorkspaceHealthCheck(
        check_id="workspace.files",
        category="workspace",
        status=HealthCheckStatus.PASS,
        message=f"required workspace files are present (count={len(targets)})",
    )


def _build_required_assets_check(
    *,
    workspace_root: Path,
    paths: RuntimePaths,
    loaded: LoadedConfig | None,
) -> WorkspaceHealthCheck:
    resolver = AssetResolver(workspace_root)
    issues: list[str] = []
    package_fallbacks: list[str] = []
    targets = _required_asset_targets(paths=paths, loaded=loaded)
    for label, target in targets:
        try:
            resolved = resolver.resolve_file(target)
        except AssetResolutionError as exc:
            issues.append(f"{label}: {_single_line_message(exc)}")
            continue
        if resolved.source_kind is AssetSourceKind.PACKAGE:
            package_fallbacks.append(f"{label}: {resolved.resolved_ref}")
    if issues:
        return WorkspaceHealthCheck(
            check_id="assets.required",
            category="assets",
            status=HealthCheckStatus.FAIL,
            message=f"required assets did not resolve cleanly ({len(issues)} issue(s))",
            details=issues,
        )
    details: list[str] = []
    if package_fallbacks:
        details.extend(sorted(package_fallbacks))
    if loaded is None:
        details.append("configured stage prompt resolution was skipped because config did not load")
        return WorkspaceHealthCheck(
            check_id="assets.required",
            category="assets",
            status=HealthCheckStatus.WARN,
            message=f"core required assets resolved but config-dependent stage prompt checks were skipped ({len(targets)} checked)",
            details=details,
        )
    return WorkspaceHealthCheck(
        check_id="assets.required",
        category="assets",
        status=HealthCheckStatus.PASS,
        message=f"required assets resolved cleanly (checked={len(targets)})",
        details=details,
    )


def _build_compounding_integrity_check(paths: RuntimePaths) -> WorkspaceHealthCheck:
    try:
        report = build_compounding_integrity_report(paths)
    except (OSError, ValidationError, ValueError) as exc:
        return WorkspaceHealthCheck(
            check_id="compounding.integrity",
            category="knowledge",
            status=HealthCheckStatus.FAIL,
            message="governed compounding integrity lint could not complete",
            details=(f"error: {_single_line_message(exc)}",),
        )

    details = tuple(
        f"{issue.severity.value}: {issue.family.value}: {issue.message}"
        for issue in report.issues
    )
    if report.status is CompoundingIntegrityStatus.FAIL:
        return WorkspaceHealthCheck(
            check_id="compounding.integrity",
            category="knowledge",
            status=HealthCheckStatus.FAIL,
            message="governed compounding integrity lint found blocking failures",
            details=details,
        )
    if report.status is CompoundingIntegrityStatus.WARN:
        return WorkspaceHealthCheck(
            check_id="compounding.integrity",
            category="knowledge",
            status=HealthCheckStatus.WARN,
            message="governed compounding integrity lint found warnings",
            details=details,
        )
    return WorkspaceHealthCheck(
        check_id="compounding.integrity",
        category="knowledge",
        status=HealthCheckStatus.PASS,
        message="governed compounding stores passed integrity lint",
    )


def _current_execution_status(paths: RuntimePaths) -> ExecutionStatus | None:
    try:
        return StatusStore(paths.status_file, ControlPlane.EXECUTION).read()
    except (OSError, StatusError):
        return None


def _latched_size(paths: RuntimePaths) -> str:
    if not paths.size_status_file.exists():
        return "SMALL"
    try:
        return parse_size_status(paths.size_status_file.read_text(encoding="utf-8")).value
    except (OSError, SizeStatusError, ValueError):
        return "SMALL"


def _preview_execution_selection(loaded: LoadedConfig, paths: RuntimePaths) -> RuntimeSelectionView:
    return preview_execution_runtime_selection(
        loaded.config,
        paths,
        preview_run_id="workspace-health-readiness",
        size_latch=_latched_size(paths),
        current_status=_current_execution_status(paths),
        resolve_assets=False,
    )


def _external_runner_bindings(selection: RuntimeSelectionView) -> dict[RunnerKind, list[StageExecutionBindingView]]:
    bindings: dict[RunnerKind, list[StageExecutionBindingView]] = {}
    for binding in selection.stage_bindings:
        runner = binding.runner
        if runner not in (RunnerKind.CODEX, RunnerKind.CLAUDE):
            continue
        bindings.setdefault(runner, []).append(binding)
    return bindings


def _build_runner_prerequisite_reports(
    selection: RuntimeSelectionView,
) -> tuple[RunnerPrerequisiteReport, ...]:
    probe = DefaultTransportProbe()
    reports: list[RunnerPrerequisiteReport] = []
    for runner, bindings in sorted(_external_runner_bindings(selection).items(), key=lambda item: item[0].value):
        executable = runner_executable_name(runner) or runner.value
        result = probe.check(TransportProbeContext(runner=runner))
        stage_nodes = tuple(binding.node_id for binding in bindings)
        stages = tuple(binding.stage for binding in bindings if binding.stage is not None)
        details = [result.summary]
        if result.command:
            details.append(f"command: {' '.join(result.command)}")
        if result.details.get("resolved_path"):
            details.append(f"resolved_path: {result.details['resolved_path']}")
        if stage_nodes:
            details.append(f"affected_nodes: {', '.join(stage_nodes)}")
        if stages:
            details.append(f"affected_stages: {', '.join(stage.value for stage in stages)}")
        reports.append(
            RunnerPrerequisiteReport(
                runner=runner,
                executable=executable,
                available=result.readiness is TransportReadiness.READY,
                message=result.summary,
                resolved_path=result.details.get("resolved_path"),
                affected_stage_nodes=stage_nodes,
                affected_stages=stages,
                details=tuple(details),
            )
        )
    return tuple(reports)


def _build_execution_readiness_check(
    loaded: LoadedConfig | None,
    paths: RuntimePaths,
) -> tuple[WorkspaceHealthCheck, tuple[RunnerPrerequisiteReport, ...], bool]:
    if loaded is None:
        check = WorkspaceHealthCheck(
            check_id="execution.runners",
            category="execution",
            status=HealthCheckStatus.FAIL,
            message="execution readiness could not be evaluated because config did not load",
        )
        return check, (), False

    try:
        selection = _preview_execution_selection(loaded, paths)
    except (KeyError, RuntimeError, ValueError) as exc:
        check = WorkspaceHealthCheck(
            check_id="execution.runners",
            category="execution",
            status=HealthCheckStatus.FAIL,
            message="execution readiness preview failed",
            details=(f"error: {_single_line_message(exc)}",),
        )
        return check, (), False

    reports = _build_runner_prerequisite_reports(selection)
    if not reports:
        check = WorkspaceHealthCheck(
            check_id="execution.runners",
            category="execution",
            status=HealthCheckStatus.PASS,
            message="configured execution stages do not require external runner CLIs",
        )
        return check, reports, True

    missing = [report for report in reports if not report.available]
    if missing:
        details = [
            (
                f"missing prerequisite: {report.executable} "
                f"(runner={report.runner.value}; stages={', '.join(stage.value for stage in report.affected_stages) or 'unknown'}; "
                f"nodes={', '.join(report.affected_stage_nodes) or 'unknown'})"
            )
            for report in missing
        ]
        check = WorkspaceHealthCheck(
            check_id="execution.runners",
            category="execution",
            status=HealthCheckStatus.FAIL,
            message=f"configured execution stages are not ready to run ({len(missing)} missing prerequisite(s))",
            details=tuple(details),
        )
        return check, reports, False

    details = tuple(
        f"{report.executable}: ready for nodes {', '.join(report.affected_stage_nodes)}"
        for report in reports
    )
    check = WorkspaceHealthCheck(
        check_id="execution.runners",
        category="execution",
        status=HealthCheckStatus.PASS,
        message=f"configured execution stages are ready to run ({len(reports)} runner prerequisite(s) checked)",
        details=details,
    )
    return check, reports, True


def _build_research_bootstrap_report(loaded: LoadedConfig | None) -> ResearchBootstrapReport:
    if loaded is None:
        return ResearchBootstrapReport(
            source="unresolved",
            contract_state="unknown",
            summary="Research bootstrap contract unavailable because config did not load.",
            next_step="Fix config.load before relying on research preflight details.",
        )

    research = loaded.config.research
    if research.mode is ResearchMode.STUB:
        return ResearchBootstrapReport(
            source="config",
            contract_state="stubbed",
            mode=research.mode,
            interview_policy=research.interview_policy,
            summary="Fresh-workspace research defaults to stub mode with interviews off.",
            next_step="Reconfigure [research] in millrace.toml before expecting active GoalSpec, incident, or audit flows.",
        )

    return ResearchBootstrapReport(
        source="config",
        contract_state="active",
        mode=research.mode,
        interview_policy=research.interview_policy,
        summary="Fresh-workspace research is configured for an active non-stub mode.",
        next_step="Use health/doctor plus research/status surfaces to confirm the selected research flow matches operator intent.",
    )


def _report_status(checks: list[WorkspaceHealthCheck]) -> HealthCheckStatus:
    if any(check.status is HealthCheckStatus.FAIL for check in checks):
        return HealthCheckStatus.FAIL
    if any(check.status is HealthCheckStatus.WARN for check in checks):
        return HealthCheckStatus.WARN
    return HealthCheckStatus.PASS


def _summary_for(checks: list[WorkspaceHealthCheck]) -> WorkspaceHealthSummary:
    passed = sum(1 for check in checks if check.status is HealthCheckStatus.PASS)
    warnings = sum(1 for check in checks if check.status is HealthCheckStatus.WARN)
    failed = sum(1 for check in checks if check.status is HealthCheckStatus.FAIL)
    return WorkspaceHealthSummary(
        total_checks=len(checks),
        passed_checks=passed,
        warning_checks=warnings,
        failed_checks=failed,
    )


def build_workspace_health_report(config_path: Path | str = "millrace.toml") -> WorkspaceHealthReport:
    """Build a deterministic workspace bootstrap and health report."""

    normalized_config_path = Path(config_path).expanduser().resolve(strict=False)
    config_check, loaded, workspace_root, agents_dir, paths = _build_config_load_check(
        config_path=normalized_config_path,
    )
    bootstrap_checks = [
        config_check,
        _build_required_directories_check(workspace_root=workspace_root, paths=paths),
        _build_required_files_check(
            config_path=normalized_config_path,
            workspace_root=workspace_root,
            agents_dir=agents_dir,
            loaded=loaded,
        ),
        _build_required_assets_check(
            workspace_root=workspace_root,
            paths=paths,
            loaded=loaded,
        ),
        _build_compounding_integrity_check(paths),
    ]
    bootstrap_ready = not any(check.status is HealthCheckStatus.FAIL for check in bootstrap_checks)
    execution_check, runner_prerequisites, execution_ready = _build_execution_readiness_check(loaded, paths)
    research_bootstrap = _build_research_bootstrap_report(loaded)
    checks = [*bootstrap_checks, execution_check]
    summary = _summary_for(checks)
    status = _report_status(checks)
    return WorkspaceHealthReport(
        config_path=normalized_config_path,
        workspace_root=workspace_root,
        workspace_root_source="loaded_config" if loaded is not None else "config_path_parent",
        config_source_kind=loaded.source.kind if loaded is not None else "unresolved",
        bundle_version=packaged_baseline_bundle_version(),
        status=status,
        ok=status is not HealthCheckStatus.FAIL,
        bootstrap_ready=bootstrap_ready,
        execution_ready=execution_ready,
        research_bootstrap=research_bootstrap,
        summary=summary,
        runner_prerequisites=runner_prerequisites,
        checks=tuple(checks),
    )
