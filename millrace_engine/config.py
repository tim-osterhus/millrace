"""Typed runtime config and loaders."""

from __future__ import annotations

import tomllib
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .config_runtime import (
    ConfigApplyBoundary,
    ConfigBoundaries,
    RoutingConfig,
    StageConfig,
    default_stage_configs,
)
from .contracts import (
    ContractModel,
    HeadlessPermissionProfile,
    PersistedObjectKind,
    ReasoningEffort,
    RegistryObjectRef,
    ResearchMode,
    RunnerKind,
    SpecInterviewPolicy,
    StageType,
)
from .paths import RuntimePaths


class MillraceModel(BaseModel):
    """Shared closed-world Pydantic base model."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def _to_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    if isinstance(value, Path):
        return value
    text = value.strip()
    if not text:
        return None
    return Path(text).expanduser()


def _resolve_path(base_dir: Path, path: Path | None) -> Path | None:
    if path is None:
        return None
    if path.is_absolute():
        return path.resolve()
    return (base_dir / path).resolve()


def _normalize_legacy_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    lowered = value.strip().lower()
    if lowered in {"on", "true", "1", "yes"}:
        return True
    if lowered in {"off", "false", "0", "no", ""}:
        return False
    return default


def _optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    return int(text)


def _optional_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc


def _coerce_integration_mode(value: str | None) -> Literal["always", "large_only", "never"]:
    lowered = (value or "").strip().lower()
    if lowered in {"always", "on", "true"}:
        return "always"
    if lowered in {"large_only", "large", "auto"}:
        return "large_only"
    if lowered in {"manual", "never", "off", ""}:
        return "never"
    return "large_only"


def _coerce_idle_mode(value: str | None, default: Literal["watch", "poll"]) -> Literal["watch", "poll"]:
    lowered = (value or "").strip().lower()
    if lowered in {"watch", "auto"}:
        return "watch"
    if lowered == "poll":
        return "poll"
    return default


def _coerce_size_metric_mode(value: str | None) -> Literal["repo", "task", "hybrid"]:
    lowered = (value or "").strip().lower()
    if lowered in {"repo", "task", "hybrid"}:
        return lowered
    return "hybrid"


def _coerce_headless_permission_profile(value: str | None) -> HeadlessPermissionProfile:
    lowered = (value or "").strip().lower()
    mapping = {
        "": HeadlessPermissionProfile.NORMAL,
        "normal": HeadlessPermissionProfile.NORMAL,
        "elevated": HeadlessPermissionProfile.ELEVATED,
        "maximum": HeadlessPermissionProfile.MAXIMUM,
    }
    return mapping.get(lowered, HeadlessPermissionProfile.NORMAL)


class EngineSettings(MillraceModel):
    mode: Literal["daemon", "once"] = "once"
    idle_mode: Literal["watch", "poll"] = "watch"
    poll_interval_seconds: int = Field(default=60, ge=1)
    inter_task_delay_seconds: int = Field(default=0, ge=0)


class PathsConfig(MillraceModel):
    workspace: Path = Path(".")
    agents_dir: Path = Path("agents")

    @field_validator("workspace", "agents_dir", mode="before")
    @classmethod
    def normalize_paths(cls, value: str | Path) -> Path:
        path = _to_path(value)
        if path is None:
            raise ValueError("path value may not be empty")
        return path


class ExecutionConfig(MillraceModel):
    integration_mode: Literal["always", "large_only", "never"] = "large_only"
    quickfix_max_attempts: int = Field(default=2, ge=0)
    run_update_on_empty: bool = True


class RepoSizingConfig(MillraceModel):
    file_count_threshold: int = Field(default=999_999_999, ge=1)
    nonempty_line_count_threshold: int = Field(default=999_999_999, ge=1)


class TaskSizingConfig(MillraceModel):
    file_count_threshold: int = Field(default=999_999_999, ge=1)
    nonempty_line_count_threshold: int = Field(default=999_999_999, ge=1)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_threshold_keys(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        if (
            "nonempty_line_count_threshold" not in payload
            and "body_nonempty_line_count_threshold" in payload
        ):
            payload["nonempty_line_count_threshold"] = payload["body_nonempty_line_count_threshold"]
        payload.pop("body_nonempty_line_count_threshold", None)
        payload.pop("dependency_count_threshold", None)
        payload.pop("requirement_count_threshold", None)
        payload.pop("acceptance_count_threshold", None)
        return payload


class SizingConfig(MillraceModel):
    mode: Literal["repo", "task", "hybrid"] = "hybrid"
    repo: RepoSizingConfig = Field(default_factory=RepoSizingConfig)
    task: TaskSizingConfig = Field(default_factory=TaskSizingConfig)


class ResearchConfig(MillraceModel):
    mode: ResearchMode = ResearchMode.STUB
    idle_mode: Literal["watch", "poll"] = "poll"
    idle_poll_seconds: int = Field(default=60, ge=1)
    stage_retry_max: int = Field(default=1, ge=0)
    stage_retry_backoff_seconds: int = Field(default=5, ge=0)
    interview_policy: SpecInterviewPolicy = SpecInterviewPolicy.OFF


class SentinelDiagnosticConfig(MillraceModel):
    runner: RunnerKind = RunnerKind.CODEX
    model: str = "gpt-5.3-codex"
    effort: ReasoningEffort | None = ReasoningEffort.MEDIUM


class SentinelCadenceStep(MillraceModel):
    activate_after_seconds: int = Field(default=0, ge=0)
    interval_seconds: int = Field(default=300, ge=1)


def _default_sentinel_cadence() -> tuple[SentinelCadenceStep, ...]:
    return (
        SentinelCadenceStep(activate_after_seconds=0, interval_seconds=300),
        SentinelCadenceStep(activate_after_seconds=900, interval_seconds=450),
        SentinelCadenceStep(activate_after_seconds=1800, interval_seconds=600),
        SentinelCadenceStep(activate_after_seconds=3600, interval_seconds=1200),
        SentinelCadenceStep(activate_after_seconds=10800, interval_seconds=1800),
    )


class SentinelProgressThresholds(MillraceModel):
    no_progress_seconds: int = Field(default=900, ge=1)
    stale_status_seconds: int = Field(default=1800, ge=1)
    stalled_recovery_seconds: int = Field(default=1800, ge=1)


class SentinelCapPolicy(MillraceModel):
    soft_cap_threshold: int = Field(default=2, ge=1)
    hard_cap_threshold: int = Field(default=3, ge=1)
    halt_on_hard_cap: bool = False

    @model_validator(mode="after")
    def validate_threshold_order(self) -> "SentinelCapPolicy":
        if self.hard_cap_threshold < self.soft_cap_threshold:
            raise ValueError("hard_cap_threshold must be greater than or equal to soft_cap_threshold")
        return self


class SentinelNotifyConfig(MillraceModel):
    enabled: bool = False
    adapter: str | None = None
    allow_direct_notify_when_supervised: bool = False
    openclaw_command: tuple[str, ...] = ()
    openclaw_timeout_seconds: int = Field(default=10, ge=1)

    @field_validator("openclaw_command", mode="before")
    @classmethod
    def normalize_openclaw_command(
        cls,
        value: tuple[str, ...] | list[str] | str | None,
    ) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            normalized = " ".join(value.strip().split())
            return () if not normalized else (normalized,)
        return tuple(str(item).strip() for item in value if str(item).strip())


class SentinelConfig(MillraceModel):
    enabled: bool = True
    diagnostic: SentinelDiagnosticConfig = Field(default_factory=SentinelDiagnosticConfig)
    cadence: tuple[SentinelCadenceStep, ...] = Field(default_factory=_default_sentinel_cadence)
    progress_thresholds: SentinelProgressThresholds = Field(default_factory=SentinelProgressThresholds)
    reset_cadence_on_recovery: bool = True
    caps: SentinelCapPolicy = Field(default_factory=SentinelCapPolicy)
    notify: SentinelNotifyConfig = Field(default_factory=SentinelNotifyConfig)

    @field_validator("cadence", mode="before")
    @classmethod
    def normalize_cadence(
        cls,
        value: tuple[SentinelCadenceStep, ...] | list[SentinelCadenceStep | dict[str, Any]] | None,
    ) -> tuple[SentinelCadenceStep, ...]:
        if value is None:
            return _default_sentinel_cadence()
        return tuple(value)

    @field_validator("cadence")
    @classmethod
    def validate_cadence(
        cls,
        value: tuple[SentinelCadenceStep, ...],
    ) -> tuple[SentinelCadenceStep, ...]:
        if not value:
            raise ValueError("sentinel cadence may not be empty")
        previous_start = -1
        for step in value:
            if step.activate_after_seconds <= previous_start:
                raise ValueError("sentinel cadence steps must use strictly increasing activate_after_seconds")
            previous_start = step.activate_after_seconds
        if value[0].activate_after_seconds != 0:
            raise ValueError("sentinel cadence must start at activate_after_seconds=0")
        return value


class WatchRoot(str, Enum):
    AGENTS = "agents"
    IDEAS_RAW = "ideas_raw"
    COMMANDS_INCOMING = "commands_incoming"
    CONFIG_FILE = "config_file"


class WatcherConfig(MillraceModel):
    debounce_seconds: float = Field(default=0.5, ge=0.0)
    roots: tuple[WatchRoot, ...] = (
        WatchRoot.AGENTS,
        WatchRoot.IDEAS_RAW,
        WatchRoot.COMMANDS_INCOMING,
        WatchRoot.CONFIG_FILE,
    )

    @field_validator("roots", mode="before")
    @classmethod
    def normalize_roots(
        cls,
        value: tuple[WatchRoot, ...] | list[WatchRoot | str] | None,
    ) -> tuple[WatchRoot, ...]:
        if value is None:
            return (
                WatchRoot.AGENTS,
                WatchRoot.IDEAS_RAW,
                WatchRoot.COMMANDS_INCOMING,
                WatchRoot.CONFIG_FILE,
            )
        normalized: list[WatchRoot] = []
        seen: set[WatchRoot] = set()
        for item in value:
            root = item if isinstance(item, WatchRoot) else WatchRoot(str(item))
            if root in seen:
                continue
            seen.add(root)
            normalized.append(root)
        if not normalized:
            raise ValueError("watcher roots may not be empty")
        return tuple(normalized)


class SearchPolicy(MillraceModel):
    execution_enabled: bool = False
    execution_exception: bool = False
    research_enabled: bool = False
    research_exception: bool = False


class CompoundingProfile(str, Enum):
    BASELINE = "baseline"
    COMPOUNDING = "compounding"
    GOVERNED_PLUS = "governed_plus"
    LAB = "lab"


class CompoundingPolicy(MillraceModel):
    profile: CompoundingProfile = CompoundingProfile.COMPOUNDING
    governed_plus_budget_characters: int = Field(default=3200, ge=1)


class ComplexityBand(str, Enum):
    MODERATE = "moderate"
    INVOLVED = "involved"
    COMPLEX = "complex"


def _default_model_profile_ref(object_id: str) -> RegistryObjectRef:
    return RegistryObjectRef(
        kind=PersistedObjectKind.MODEL_PROFILE,
        id=object_id,
        version="1.0.0",
    )


class ComplexityProfileRefs(MillraceModel):
    moderate: RegistryObjectRef = Field(
        default_factory=lambda: _default_model_profile_ref("model.complexity.moderate")
    )
    involved: RegistryObjectRef = Field(
        default_factory=lambda: _default_model_profile_ref("model.complexity.involved")
    )
    complex: RegistryObjectRef = Field(
        default_factory=lambda: _default_model_profile_ref("model.complexity.complex")
    )

    @field_validator("moderate", "involved", "complex")
    @classmethod
    def validate_model_profile_refs(cls, value: RegistryObjectRef) -> RegistryObjectRef:
        if value.kind is not PersistedObjectKind.MODEL_PROFILE:
            raise ValueError("complexity profile refs must reference model_profile objects")
        return value


class ComplexityRoutingConfig(MillraceModel):
    enabled: bool = False
    default_band: ComplexityBand = ComplexityBand.MODERATE
    profiles: ComplexityProfileRefs = Field(default_factory=ComplexityProfileRefs)


class ComplexitySelectionReason(str, Enum):
    DISABLED = "disabled"
    DEFAULT_BAND = "default_band"
    TASK_COMPLEXITY = "task_complexity"
    NO_ROUTED_STAGES = "no_routed_stages"


class ComplexityRouteSelection(ContractModel):
    enabled: bool
    task_complexity: str | None = None
    band: ComplexityBand
    reason: ComplexitySelectionReason
    selected_model_profile_ref: RegistryObjectRef | None = None
    routed_node_ids: tuple[str, ...] = ()
    routed_stage_types: tuple[StageType, ...] = ()

    @field_validator("task_complexity")
    @classmethod
    def normalize_task_complexity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split()).upper()
        return normalized or None

    @field_validator("routed_node_ids", mode="before")
    @classmethod
    def normalize_routed_node_ids(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if not value:
            return ()
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            node_id = str(item).strip().lower()
            if not node_id or node_id in seen:
                continue
            seen.add(node_id)
            normalized.append(node_id)
        return tuple(normalized)


class UsageThresholds(MillraceModel):
    remaining_threshold: Decimal | None = None
    consumed_threshold: Decimal | None = None
    legacy_threshold: Decimal | None = None
    refresh_utc: str = "MON 00:00"

    @field_validator("remaining_threshold", "consumed_threshold", "legacy_threshold", mode="before")
    @classmethod
    def normalize_decimal_fields(cls, value: Decimal | str | None) -> Decimal | None:
        if value is None or value == "":
            return None
        if isinstance(value, Decimal):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return Decimal(str(value))
        return _optional_decimal(value)


class UsagePolicy(MillraceModel):
    enabled: bool = False
    provider: Literal["codex", "env", "command"] = "codex"
    cache_max_age_secs: int = Field(default=900, ge=0)
    orch_command: str | None = None
    research_command: str | None = None
    codex_auth_source_dir: Path | None = None
    codex_runtime_home: Path | None = None
    execution: UsageThresholds = Field(default_factory=UsageThresholds)
    research: UsageThresholds = Field(default_factory=UsageThresholds)

    @field_validator("codex_auth_source_dir", "codex_runtime_home", mode="before")
    @classmethod
    def normalize_optional_paths(cls, value: str | Path | None) -> Path | None:
        return _to_path(value)


class NetworkGuardPolicy(MillraceModel):
    enabled: bool = False
    execution_policy: Literal["allow", "deny"] = "deny"
    research_policy: Literal["allow", "deny"] = "deny"
    execution_exception: bool = False
    research_exception: bool = False


class PreflightPolicy(MillraceModel):
    enabled: bool = True
    transport_check: bool = True


class OutagePolicy(MillraceModel):
    enabled: bool = True
    wait_initial_seconds: int = Field(default=15, ge=0)
    wait_max_seconds: int = Field(default=300, ge=0)
    max_probes: int = Field(default=0, ge=0)
    probe_timeout_seconds: int = Field(default=5, ge=1)
    probe_host: str = "api.openai.com"
    probe_port: int = Field(default=443, ge=1, le=65535)
    probe_command: str | None = None
    policy: Literal["pause_resume", "incident", "blocker"] = "pause_resume"
    route_to_blocker: bool = False
    route_to_incident: bool = False


class PolicyConfig(MillraceModel):
    search: SearchPolicy = Field(default_factory=SearchPolicy)
    compounding: CompoundingPolicy = Field(default_factory=CompoundingPolicy)
    complexity: ComplexityRoutingConfig = Field(default_factory=ComplexityRoutingConfig)
    usage: UsagePolicy = Field(default_factory=UsagePolicy)
    network_guard: NetworkGuardPolicy = Field(default_factory=NetworkGuardPolicy)
    preflight: PreflightPolicy = Field(default_factory=PreflightPolicy)
    outage: OutagePolicy = Field(default_factory=OutagePolicy)


class EngineConfig(MillraceModel):
    engine: EngineSettings = Field(default_factory=EngineSettings)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    sizing: SizingConfig = Field(default_factory=SizingConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    sentinel: SentinelConfig = Field(default_factory=SentinelConfig)
    watchers: WatcherConfig = Field(default_factory=WatcherConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    policies: PolicyConfig = Field(default_factory=PolicyConfig)
    stages: dict[StageType, StageConfig] = Field(default_factory=default_stage_configs)
    boundaries: ConfigBoundaries = Field(default_factory=ConfigBoundaries)

    @field_validator("stages", mode="before")
    @classmethod
    def normalize_stage_mapping(cls, value: Any) -> Any:
        if value is None:
            return default_stage_configs()
        if not isinstance(value, dict):
            return value
        normalized: dict[StageType, Any] = {}
        for key, stage_value in value.items():
            if isinstance(key, StageType):
                stage_key = key
            else:
                stage_key = StageType(str(key))
            normalized[stage_key] = stage_value
        return normalized


class ConfigSourceInfo(MillraceModel):
    kind: Literal["native_toml"]
    primary_path: Path

    @field_validator("primary_path", mode="before")
    @classmethod
    def normalize_primary_path(cls, value: str | Path) -> Path:
        path = _to_path(value)
        if path is None:
            raise ValueError("primary path may not be empty")
        return path


class LoadedConfig(MillraceModel):
    config: EngineConfig
    source: ConfigSourceInfo

    def runtime_paths(self) -> RuntimePaths:
        """Build canonical runtime paths from the loaded config."""
        return RuntimePaths.from_workspace(
            workspace_root=self.config.paths.workspace,
            agents_dir=self.config.paths.agents_dir,
        )


def _diff_payload(prefix: str, current: Any, updated: Any) -> list[str]:
    if isinstance(current, dict) and isinstance(updated, dict):
        changed: list[str] = []
        keys = sorted(set(current) | set(updated))
        for key in keys:
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            if key not in current or key not in updated:
                changed.append(child_prefix)
                continue
            changed.extend(_diff_payload(child_prefix, current[key], updated[key]))
        return changed

    if current != updated:
        return [prefix]
    return []


def diff_config_fields(current: EngineConfig, updated: EngineConfig) -> tuple[str, ...]:
    """Return dotted field names that differ between two configs."""

    changed = _diff_payload(
        "",
        current.model_dump(mode="json"),
        updated.model_dump(mode="json"),
    )
    deduped: list[str] = []
    seen: set[str] = set()
    for field_name in changed:
        if not field_name or field_name in seen:
            continue
        seen.add(field_name)
        deduped.append(field_name)
    return tuple(deduped)


def _finalize_config(config: EngineConfig, base_dir: Path) -> EngineConfig:
    workspace = _resolve_path(base_dir, config.paths.workspace)
    if workspace is None:
        raise ValidationError.from_exception_data("EngineConfig", [])
    agents_dir = _resolve_path(workspace, config.paths.agents_dir)
    stage_data: dict[StageType, dict[str, Any]] = {}
    for stage, stage_config in config.stages.items():
        item = stage_config.model_dump()
        item["prompt_file"] = _resolve_path(workspace, stage_config.prompt_file)
        stage_data[stage] = item
    payload = config.model_dump()
    payload["paths"]["workspace"] = workspace
    payload["paths"]["agents_dir"] = agents_dir
    payload["stages"] = stage_data
    return EngineConfig.model_validate(payload)


def load_engine_config(
    config_path: Path | str | None = None,
) -> LoadedConfig:
    """Load config from native TOML."""

    resolved_config_path = Path(config_path or "millrace.toml").expanduser()
    if resolved_config_path.exists():
        with resolved_config_path.open("rb") as fh:
            raw = tomllib.load(fh)
        config = EngineConfig.model_validate(raw)
        config = _finalize_config(config, resolved_config_path.parent.resolve())
        return LoadedConfig(
            config=config,
            source=ConfigSourceInfo(
                kind="native_toml",
                primary_path=resolved_config_path.resolve(),
            ),
        )

    raise FileNotFoundError(f"native Millrace config not found: {resolved_config_path}")


def build_runtime_paths(config: EngineConfig) -> RuntimePaths:
    """Return the canonical runtime paths for the loaded config."""

    return RuntimePaths.from_workspace(
        workspace_root=config.paths.workspace,
        agents_dir=config.paths.agents_dir,
    )
