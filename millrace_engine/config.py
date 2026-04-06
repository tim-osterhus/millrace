"""Typed runtime config and loaders."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Literal
import tomllib

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .contracts import (
    ContractModel,
    PersistedObjectKind,
    RegistryObjectRef,
    ResearchMode,
    SpecInterviewPolicy,
    StageType,
)
from .config_compat import (
    LegacyPolicyCompatReport,
    build_legacy_policy_compatibility_report,
    load_model_values,
    load_workflow_values,
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


# Runtime-stage routing and config-application helpers live in a sibling
# module but stay re-exported here to preserve the stable config facade.
from .config_runtime import (
    ConfigApplyBoundary,
    ConfigBoundaries,
    RoutingConfig,
    StageConfig,
    default_stage_configs,
)


class SearchPolicy(MillraceModel):
    execution_enabled: bool = False
    execution_exception: bool = False
    research_enabled: bool = False
    research_exception: bool = False


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
    kind: Literal["native_toml", "legacy_markdown"]
    primary_path: Path
    secondary_paths: tuple[Path, ...] = ()
    unmapped_keys: tuple[str, ...] = ()
    legacy_policy_compatibility: LegacyPolicyCompatReport | None = None

    @field_validator("primary_path", mode="before")
    @classmethod
    def normalize_primary_path(cls, value: str | Path) -> Path:
        path = _to_path(value)
        if path is None:
            raise ValueError("primary path may not be empty")
        return path

    @field_validator("secondary_paths", mode="before")
    @classmethod
    def normalize_secondary_paths(cls, value: tuple[Path, ...] | list[Path] | None) -> tuple[Path, ...]:
        if not value:
            return ()
        paths: list[Path] = []
        for item in value:
            path = _to_path(item)
            if path is not None:
                paths.append(path)
        return tuple(paths)


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


def _build_legacy_config(
    workflow_values: dict[str, str],
    model_values: dict[str, str],
    base_dir: Path,
    workflow_path: Path,
    model_path: Path,
) -> LoadedConfig:
    consumed_keys: set[str] = set()
    legacy_policy_compatibility = build_legacy_policy_compatibility_report(workflow_values)

    raw_config: dict[str, Any] = {
        "engine": {
            "mode": "once",
            "idle_mode": "watch",
            "poll_interval_seconds": 60,
            "inter_task_delay_seconds": _optional_int(workflow_values.get("ORCH_INTER_TASK_DELAY_SECS")) or 0,
        },
        "paths": {
            "workspace": str(base_dir),
            "agents_dir": "agents",
        },
        "execution": {
            "integration_mode": _coerce_integration_mode(workflow_values.get("INTEGRATION_MODE")),
            "quickfix_max_attempts": 2,
            "run_update_on_empty": _normalize_legacy_bool(workflow_values.get("RUN_UPDATE_ON_EMPTY"), default=True),
        },
        "sizing": {
            "mode": _coerce_size_metric_mode(workflow_values.get("SIZE_METRIC_MODE")),
            "repo": {
                "file_count_threshold": _optional_int(workflow_values.get("LARGE_FILES_THRESHOLD")) or 999_999_999,
                "nonempty_line_count_threshold": (
                    _optional_int(workflow_values.get("LARGE_LOC_THRESHOLD")) or 999_999_999
                ),
            },
            "task": {
                "file_count_threshold": (
                    _optional_int(workflow_values.get("TASK_LARGE_FILES_THRESHOLD")) or 999_999_999
                ),
                "nonempty_line_count_threshold": (
                    _optional_int(workflow_values.get("TASK_LARGE_LOC_THRESHOLD")) or 999_999_999
                ),
            },
        },
        "research": {
            "mode": (workflow_values.get("RESEARCH_MODE") or "AUTO").strip().lower(),
            "idle_mode": _coerce_idle_mode(workflow_values.get("RESEARCH_IDLE_MODE"), default="poll"),
            "idle_poll_seconds": _optional_int(workflow_values.get("RESEARCH_IDLE_POLL_SECS")) or 60,
            "stage_retry_max": _optional_int(workflow_values.get("STAGE_RETRY_MAX")) or 1,
            "stage_retry_backoff_seconds": _optional_int(workflow_values.get("STAGE_RETRY_BACKOFF_SECS")) or 5,
        },
        "policies": {
            "search": {
                "execution_enabled": _normalize_legacy_bool(workflow_values.get("ORCH_ALLOW_SEARCH")),
                "execution_exception": _normalize_legacy_bool(workflow_values.get("ORCH_ALLOW_SEARCH_EXCEPTION")),
                "research_enabled": _normalize_legacy_bool(workflow_values.get("RESEARCH_ALLOW_SEARCH")),
                "research_exception": _normalize_legacy_bool(workflow_values.get("RESEARCH_ALLOW_SEARCH_EXCEPTION")),
            },
            "usage": {
                "enabled": _normalize_legacy_bool(workflow_values.get("USAGE_AUTOPAUSE_MODE")),
                "provider": (workflow_values.get("USAGE_SAMPLER_PROVIDER") or "codex").strip().lower() or "codex",
                "cache_max_age_secs": _optional_int(workflow_values.get("USAGE_SAMPLER_CACHE_MAX_AGE_SECS")) or 900,
                "orch_command": workflow_values.get("USAGE_SAMPLER_ORCH_CMD") or None,
                "research_command": workflow_values.get("USAGE_SAMPLER_RESEARCH_CMD") or None,
                "codex_auth_source_dir": workflow_values.get("CODEX_AUTH_SOURCE_DIR") or None,
                "codex_runtime_home": workflow_values.get("CODEX_RUNTIME_HOME") or None,
                "execution": {
                    "remaining_threshold": workflow_values.get("ORCH_WEEKLY_USAGE_REMAINING_THRESHOLD"),
                    "consumed_threshold": workflow_values.get("ORCH_WEEKLY_USAGE_CONSUMED_THRESHOLD"),
                    "legacy_threshold": workflow_values.get("ORCH_WEEKLY_USAGE_THRESHOLD"),
                    "refresh_utc": workflow_values.get("ORCH_WEEKLY_REFRESH_UTC") or "MON 00:00",
                },
                "research": {
                    "remaining_threshold": workflow_values.get("RESEARCH_WEEKLY_USAGE_REMAINING_THRESHOLD"),
                    "consumed_threshold": workflow_values.get("RESEARCH_WEEKLY_USAGE_CONSUMED_THRESHOLD"),
                    "legacy_threshold": workflow_values.get("RESEARCH_WEEKLY_USAGE_THRESHOLD"),
                    "refresh_utc": workflow_values.get("RESEARCH_WEEKLY_REFRESH_UTC") or "MON 00:00",
                },
            },
            "network_guard": {
                "enabled": _normalize_legacy_bool(workflow_values.get("NETWORK_GUARD_MODE")),
                "execution_policy": (workflow_values.get("ORCH_NETWORK_GUARD_POLICY") or "deny").strip().lower(),
                "research_policy": (workflow_values.get("RESEARCH_NETWORK_GUARD_POLICY") or "deny").strip().lower(),
                "execution_exception": _normalize_legacy_bool(workflow_values.get("ORCH_NETWORK_POLICY_EXCEPTION")),
                "research_exception": _normalize_legacy_bool(workflow_values.get("RESEARCH_NETWORK_POLICY_EXCEPTION")),
            },
            "preflight": {
                "enabled": _normalize_legacy_bool(workflow_values.get("ENV_PREFLIGHT_MODE"), default=True),
                "transport_check": _normalize_legacy_bool(
                    workflow_values.get("ENV_PREFLIGHT_TRANSPORT_CHECK"),
                    default=True,
                ),
            },
            "outage": {
                "enabled": _normalize_legacy_bool(workflow_values.get("NETWORK_OUTAGE_RESILIENCE_MODE"), default=True),
                "wait_initial_seconds": _optional_int(workflow_values.get("NETWORK_OUTAGE_WAIT_INITIAL_SECS")) or 15,
                "wait_max_seconds": _optional_int(workflow_values.get("NETWORK_OUTAGE_WAIT_MAX_SECS")) or 300,
                "max_probes": _optional_int(workflow_values.get("NETWORK_OUTAGE_MAX_PROBES")) or 0,
                "probe_timeout_seconds": _optional_int(workflow_values.get("NETWORK_OUTAGE_PROBE_TIMEOUT_SECS")) or 5,
                "probe_host": workflow_values.get("NETWORK_OUTAGE_PROBE_HOST") or "api.openai.com",
                "probe_port": _optional_int(workflow_values.get("NETWORK_OUTAGE_PROBE_PORT")) or 443,
                "probe_command": workflow_values.get("NETWORK_OUTAGE_PROBE_CMD") or None,
                "policy": (workflow_values.get("NETWORK_OUTAGE_POLICY") or "pause_resume").strip().lower(),
                "route_to_blocker": _normalize_legacy_bool(workflow_values.get("NETWORK_OUTAGE_ROUTE_TO_BLOCKER")),
                "route_to_incident": _normalize_legacy_bool(workflow_values.get("NETWORK_OUTAGE_ROUTE_TO_INCIDENT")),
            },
        },
        "stages": {},
    }

    consumed_keys.update(
        {
            "INTEGRATION_MODE",
            "ORCH_INTER_TASK_DELAY_SECS",
            "RUN_UPDATE_ON_EMPTY",
            "SIZE_METRIC_MODE",
            "LARGE_FILES_THRESHOLD",
            "LARGE_LOC_THRESHOLD",
            "TASK_LARGE_FILES_THRESHOLD",
            "TASK_LARGE_LOC_THRESHOLD",
            "RESEARCH_MODE",
            "RESEARCH_IDLE_MODE",
            "RESEARCH_IDLE_POLL_SECS",
            "STAGE_RETRY_MAX",
            "STAGE_RETRY_BACKOFF_SECS",
            "ORCH_ALLOW_SEARCH",
            "ORCH_ALLOW_SEARCH_EXCEPTION",
            "RESEARCH_ALLOW_SEARCH",
            "RESEARCH_ALLOW_SEARCH_EXCEPTION",
            "USAGE_AUTOPAUSE_MODE",
            "USAGE_SAMPLER_PROVIDER",
            "USAGE_SAMPLER_CACHE_MAX_AGE_SECS",
            "USAGE_SAMPLER_ORCH_CMD",
            "USAGE_SAMPLER_RESEARCH_CMD",
            "CODEX_AUTH_SOURCE_DIR",
            "CODEX_RUNTIME_HOME",
            "ORCH_WEEKLY_USAGE_REMAINING_THRESHOLD",
            "ORCH_WEEKLY_USAGE_CONSUMED_THRESHOLD",
            "ORCH_WEEKLY_USAGE_THRESHOLD",
            "ORCH_WEEKLY_REFRESH_UTC",
            "RESEARCH_WEEKLY_USAGE_REMAINING_THRESHOLD",
            "RESEARCH_WEEKLY_USAGE_CONSUMED_THRESHOLD",
            "RESEARCH_WEEKLY_USAGE_THRESHOLD",
            "RESEARCH_WEEKLY_REFRESH_UTC",
            "NETWORK_GUARD_MODE",
            "ORCH_NETWORK_GUARD_POLICY",
            "RESEARCH_NETWORK_GUARD_POLICY",
            "ORCH_NETWORK_POLICY_EXCEPTION",
            "RESEARCH_NETWORK_POLICY_EXCEPTION",
            "ENV_PREFLIGHT_MODE",
            "ENV_PREFLIGHT_TRANSPORT_CHECK",
            "NETWORK_OUTAGE_RESILIENCE_MODE",
            "NETWORK_OUTAGE_WAIT_INITIAL_SECS",
            "NETWORK_OUTAGE_WAIT_MAX_SECS",
            "NETWORK_OUTAGE_MAX_PROBES",
            "NETWORK_OUTAGE_PROBE_TIMEOUT_SECS",
            "NETWORK_OUTAGE_PROBE_HOST",
            "NETWORK_OUTAGE_PROBE_PORT",
            "NETWORK_OUTAGE_PROBE_CMD",
            "NETWORK_OUTAGE_POLICY",
            "NETWORK_OUTAGE_ROUTE_TO_BLOCKER",
            "NETWORK_OUTAGE_ROUTE_TO_INCIDENT",
        }
    )

    stages = default_stage_configs()
    stage_fields = {"RUNNER", "MODEL", "EFFORT"}
    for key, value in model_values.items():
        for stage in StageType:
            prefix = f"{stage.name}_"
            if not key.startswith(prefix):
                continue
            field_name = key[len(prefix) :]
            if field_name not in stage_fields:
                continue
            stage_config = stages[stage].model_copy(deep=True)
            if field_name == "RUNNER":
                stage_config.runner = RunnerKind(value.strip().lower())
            elif field_name == "MODEL":
                stage_config.model = value.strip()
            elif field_name == "EFFORT":
                stage_config.effort = ReasoningEffort(value.strip().lower())
            stages[stage] = stage_config
            consumed_keys.add(key)
            break

    raw_config["stages"] = {
        stage.value: stage_config.model_dump()
        for stage, stage_config in stages.items()
    }

    config = _finalize_config(EngineConfig.model_validate(raw_config), base_dir)
    explicitly_reported_keys = set(legacy_policy_compatibility.explicitly_reported_keys())
    workflow_unmapped_keys = set(workflow_values) - consumed_keys - explicitly_reported_keys
    model_unmapped_keys = set(model_values) - consumed_keys
    unmapped_keys = tuple(sorted(workflow_unmapped_keys | model_unmapped_keys))
    return LoadedConfig(
        config=config,
        source=ConfigSourceInfo(
            kind="legacy_markdown",
            primary_path=workflow_path,
            secondary_paths=(model_path,),
            unmapped_keys=unmapped_keys,
            legacy_policy_compatibility=legacy_policy_compatibility,
        ),
    )


def load_engine_config(
    config_path: Path | str | None = None,
    *,
    legacy_workflow_path: Path | str | None = None,
    legacy_model_path: Path | str | None = None,
) -> LoadedConfig:
    """Load config from native TOML or legacy markdown config files."""

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

    workflow_path = Path(legacy_workflow_path or resolved_config_path.parent / "agents/options/workflow_config.md").expanduser()
    model_path = Path(legacy_model_path or resolved_config_path.parent / "agents/options/model_config.md").expanduser()

    if workflow_path.exists() and model_path.exists():
        workflow_values = load_workflow_values(workflow_path)
        model_values = load_model_values(model_path)
        return _build_legacy_config(
            workflow_values=workflow_values,
            model_values=model_values,
            base_dir=resolved_config_path.parent.resolve(),
            workflow_path=workflow_path.resolve(),
            model_path=model_path.resolve(),
        )

    raise FileNotFoundError(
        "No native config found and legacy markdown config pair is incomplete: "
        f"{resolved_config_path}, {workflow_path}, {model_path}"
    )


def build_runtime_paths(config: EngineConfig) -> RuntimePaths:
    """Return the canonical runtime paths for the loaded config."""

    return RuntimePaths.from_workspace(
        workspace_root=config.paths.workspace,
        agents_dir=config.paths.agents_dir,
    )
