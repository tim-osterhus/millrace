"""Deterministic workspace bootstrap and health reporting."""

from __future__ import annotations

from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Literal
import tomllib

from pydantic import Field, ValidationError, field_validator

from .assets.resolver import AssetResolutionError, AssetResolver, AssetSourceKind
from .baseline_assets import packaged_baseline_bundle_version
from .config import LoadedConfig, build_runtime_paths, load_engine_config
from .contracts import ContractModel
from .config_compat import LegacyPolicyCompatStatus
from .paths import RuntimePaths


class HealthCheckStatus(str, Enum):
    """Deterministic health-check status."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class WorkspaceHealthCheck(ContractModel):
    """One deterministic workspace health check."""

    check_id: str
    category: Literal["config", "workspace", "assets"]
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


class WorkspaceHealthReport(ContractModel):
    """Machine-readable workspace bootstrap and health report."""

    config_path: Path
    workspace_root: Path
    workspace_root_source: Literal["loaded_config", "config_path_parent"]
    config_source_kind: Literal["native_toml", "legacy_markdown", "unresolved"]
    bundle_version: str
    status: HealthCheckStatus
    ok: bool
    summary: WorkspaceHealthSummary
    checks: tuple[WorkspaceHealthCheck, ...]

    @field_validator("config_path", "workspace_root", mode="before")
    @classmethod
    def normalize_path_fields(cls, value: str | Path) -> Path:
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
        if loaded.source.kind == "native_toml":
            items.append(("config", config_path))
        else:
            items.append(("legacy workflow config", loaded.source.primary_path))
            for path in loaded.source.secondary_paths:
                items.append(("legacy model config", path))
    elif config_path.exists():
        items.append(("config", config_path))
    else:
        items.append(("legacy workflow config", workspace_root / "agents/options/workflow_config.md"))
        items.append(("legacy model config", workspace_root / "agents/options/model_config.md"))
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
                *(
                    f"secondary_path: {path.as_posix()}"
                    for path in loaded.source.secondary_paths
                ),
            ),
        ),
        loaded,
        loaded_paths.root,
        loaded_paths.agents_dir,
        loaded_paths,
    )


def _build_legacy_compat_check(loaded: LoadedConfig | None) -> WorkspaceHealthCheck:
    if loaded is None:
        return WorkspaceHealthCheck(
            check_id="config.legacy_compat",
            category="config",
            status=HealthCheckStatus.WARN,
            message="legacy compatibility audit skipped because config did not load",
        )
    if loaded.source.kind != "legacy_markdown":
        return WorkspaceHealthCheck(
            check_id="config.legacy_compat",
            category="config",
            status=HealthCheckStatus.PASS,
            message="native config is active; no legacy compatibility warnings",
        )

    report = loaded.source.legacy_policy_compatibility
    if report is None:
        return WorkspaceHealthCheck(
            check_id="config.legacy_compat",
            category="config",
            status=HealthCheckStatus.WARN,
            message="legacy config loaded without a compatibility audit report",
        )

    details: list[str] = []
    for status in (
        LegacyPolicyCompatStatus.PARTIALLY_MAPPED,
        LegacyPolicyCompatStatus.DEPRECATED,
        LegacyPolicyCompatStatus.UNSUPPORTED,
    ):
        keys = [entry.key for entry in report.entries_for_status(status, present_only=True)]
        if keys:
            details.append(f"{status.value}: {', '.join(keys)}")
    if loaded.source.unmapped_keys:
        details.append(f"unmapped: {', '.join(loaded.source.unmapped_keys)}")
    if details:
        return WorkspaceHealthCheck(
            check_id="config.legacy_compat",
            category="config",
            status=HealthCheckStatus.WARN,
            message="legacy config has compatibility items that still need operator review",
            details=details,
        )
    counts = report.status_counts(present_only=True)
    return WorkspaceHealthCheck(
        check_id="config.legacy_compat",
        category="config",
        status=HealthCheckStatus.PASS,
        message=(
            "legacy config compatibility is clean "
            f"(mapped={counts['mapped']})"
        ),
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
    checks = [
        config_check,
        _build_legacy_compat_check(loaded),
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
    ]
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
        summary=summary,
        checks=tuple(checks),
    )
