"""Shared helpers and persisted-object contracts for loop architecture."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING
import re

from pydantic import field_validator, model_validator

from .contracts import ContractModel, _normalize_datetime


CANONICAL_ID_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
ALIAS_RE = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
LABEL_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9][A-Za-z0-9.-]*)?$")
REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
STATUS_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
ROUTING_TOKEN_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
STAGE_SELECTOR_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?:\.\*)?$")


if TYPE_CHECKING:
    from .loop_architecture_stage_contracts import RegisteredStageKindDefinition


def _normalize_text(value: str, *, field_label: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError(f"{field_label} may not be empty")
    return normalized


def _normalize_optional_text(value: str | None, *, field_label: str) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.strip().split())
    return normalized or None


def _normalize_canonical_id(value: str, *, field_label: str) -> str:
    normalized = value.strip()
    if not CANONICAL_ID_RE.fullmatch(normalized):
        raise ValueError(
            f"{field_label} must match {CANONICAL_ID_RE.pattern!r} and use lowercase canonical tokens"
        )
    return normalized


def _normalize_alias(value: str) -> str:
    normalized = value.strip().lower()
    if not ALIAS_RE.fullmatch(normalized):
        raise ValueError(f"aliases must match {ALIAS_RE.pattern!r}")
    return normalized


def _normalize_label(value: str) -> str:
    normalized = value.strip().lower()
    if not LABEL_RE.fullmatch(normalized):
        raise ValueError(f"labels must match {LABEL_RE.pattern!r}")
    return normalized


def _normalize_reference(value: str, *, field_label: str) -> str:
    normalized = value.strip()
    if not REFERENCE_RE.fullmatch(normalized):
        raise ValueError(f"{field_label} must match {REFERENCE_RE.pattern!r}")
    return normalized


def _normalize_semver(value: str, *, field_label: str) -> str:
    normalized = value.strip()
    if not SEMVER_RE.fullmatch(normalized):
        raise ValueError(f"{field_label} must match {SEMVER_RE.pattern!r}")
    return normalized


def _normalize_status(value: str, *, field_label: str) -> str:
    normalized = value.strip().upper()
    if not STATUS_RE.fullmatch(normalized):
        raise ValueError(f"{field_label} must match {STATUS_RE.pattern!r}")
    return normalized


def _normalize_routing_token(value: str, *, field_label: str) -> str:
    normalized = value.strip().lower()
    if not ROUTING_TOKEN_RE.fullmatch(normalized):
        raise ValueError(f"{field_label} must match {ROUTING_TOKEN_RE.pattern!r}")
    return normalized


def _normalize_trigger_token(value: str, *, field_label: str) -> str:
    stripped = value.strip()
    if STATUS_RE.fullmatch(stripped):
        return stripped
    if ROUTING_TOKEN_RE.fullmatch(stripped.lower()):
        return stripped.lower()
    raise ValueError(
        f"{field_label} must be a lowercase routing token or uppercase status marker"
    )


def _normalize_stage_selector(value: str) -> str:
    normalized = value.strip().lower()
    if not STAGE_SELECTOR_RE.fullmatch(normalized):
        raise ValueError(f"stage selectors must match {STAGE_SELECTOR_RE.pattern!r}")
    return normalized


def _stage_selector_matches(selector: str, kind_id: str) -> bool:
    if selector.endswith(".*"):
        return kind_id.startswith(selector[:-1])
    return kind_id == selector


def _matches_stage_selector_set(kind_id: str, selectors: tuple[str, ...]) -> bool:
    return any(_stage_selector_matches(selector, kind_id) for selector in selectors)


def _dedupe(values: list[str], *, field_label: str) -> tuple[str, ...]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    if not deduped and field_label:
        raise ValueError(f"{field_label} may not be empty")
    return tuple(deduped)


def _normalize_datetime_or_none(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    return _normalize_datetime(value)


class ControlPlane(str, Enum):
    EXECUTION = "execution"
    RESEARCH = "research"


class RegistryTier(str, Enum):
    DEFAULT = "default"
    GOLDEN = "golden"
    NICHE = "niche"
    AD_HOC = "ad_hoc"
    AUTOSAVED = "autosaved"
    LEGACY = "legacy"


class PersistedObjectStatus(str, Enum):
    ACTIVE = "active"
    DRAFT = "draft"
    DISABLED = "disabled"
    LEGACY = "legacy"


class RegistrySourceKind(str, Enum):
    PACKAGED_DEFAULT = "packaged_default"
    WORKSPACE_DEFINED = "workspace_defined"
    ADVISOR_SAVED = "advisor_saved"
    IMPORTED = "imported"
    EPHEMERAL = "ephemeral"


class PersistedObjectKind(str, Enum):
    REGISTERED_STAGE_KIND = "registered_stage_kind"
    LOOP_CONFIG = "loop_config"
    MODE = "mode"
    TASK_AUTHORING_PROFILE = "task_authoring_profile"
    MODEL_PROFILE = "model_profile"


class ArtifactMultiplicity(str, Enum):
    ONE = "one"
    ZERO_OR_MORE = "zero_or_more"
    ONE_OR_MORE = "one_or_more"


class ArtifactPersistence(str, Enum):
    RUNTIME_BUNDLE = "runtime_bundle"
    DIAGNOSTICS = "diagnostics"
    HISTORY = "history"
    OPTIONAL = "optional"


class StageIdempotencePolicy(str, Enum):
    IDEMPOTENT = "idempotent"
    RETRY_SAFE_WITH_KEY = "retry_safe_with_key"
    SINGLE_ATTEMPT_ONLY = "single_attempt_only"


class QueueMutationPolicy(str, Enum):
    RUNTIME_ONLY = "runtime_only"


class StageOverrideField(str, Enum):
    MODEL_PROFILE_REF = "model_profile_ref"
    RUNNER = "runner"
    MODEL = "model"
    EFFORT = "effort"
    ALLOW_SEARCH = "allow_search"
    PROMPT_ASSET_REF = "prompt_asset_ref"
    TIMEOUT_SECONDS = "timeout_seconds"


class LoopEdgeKind(str, Enum):
    NORMAL = "normal"
    RETRY = "retry"
    ESCALATION = "escalation"
    HANDOFF = "handoff"
    TERMINAL = "terminal"


class LoopTerminalClass(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    BLOCKED = "blocked"
    HANDOFF = "handoff"
    CANCELLED = "cancelled"


class OutlineMode(str, Enum):
    MONOLITHIC = "monolithic"
    INDEX_SHARDED = "index_sharded"
    HYBRID = "hybrid"


class TaskDecompositionStyle(str, Enum):
    NARROW = "narrow"
    BROAD = "broad"


class TaskBreadth(str, Enum):
    FOCUSED = "focused"
    STANDARD = "standard"
    BROAD = "broad"


class AcceptanceProfile(str, Enum):
    LIGHT = "light"
    STANDARD = "standard"
    STRICT = "strict"


class GateStrictness(str, Enum):
    RELAXED = "relaxed"
    STANDARD = "standard"
    STRICT = "strict"


class ResearchAssumption(str, Enum):
    ASSUME_LOCAL_CONTEXT = "assume_local_context"
    CONSULT_IF_AMBIGUOUS = "consult_if_ambiguous"
    RESEARCH_EXPECTED = "research_expected"


class ResearchParticipationMode(str, Enum):
    NONE = "none"
    CONSULT_ONLY = "consult_only"
    SELECTED_RESEARCH_STAGES = "selected_research_stages"
    FULL_RESEARCH_HANDOFF = "full_research_handoff"


class RegistryObjectRef(ContractModel):
    kind: PersistedObjectKind
    id: str
    version: str

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _normalize_canonical_id(value, field_label="reference id")

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        return _normalize_semver(value, field_label="reference version")


class RegistryObjectSource(ContractModel):
    kind: RegistrySourceKind
    ref: str | None = None

    @field_validator("ref")
    @classmethod
    def validate_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_reference(value, field_label="source ref")


class PersistedObjectEnvelope(ContractModel):
    id: str
    version: str
    tier: RegistryTier
    title: str
    aliases: tuple[str, ...] = ()
    summary: str | None = None
    status: PersistedObjectStatus = PersistedObjectStatus.ACTIVE
    source: RegistryObjectSource
    extends: RegistryObjectRef | None = None
    labels: tuple[str, ...] = ()
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _normalize_canonical_id(value, field_label="id")

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        return _normalize_semver(value, field_label="version")

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _normalize_text(value, field_label="title")

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value, field_label="summary")

    @field_validator("aliases", mode="before")
    @classmethod
    def normalize_aliases(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        aliases = [_normalize_alias(str(item)) for item in value]
        return _dedupe(aliases, field_label="")

    @field_validator("labels", mode="before")
    @classmethod
    def normalize_labels(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        labels = [_normalize_label(str(item)) for item in value]
        return _dedupe(labels, field_label="")

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def normalize_datetimes(
        cls,
        value: datetime | str | None,
    ) -> datetime | None:
        return _normalize_datetime_or_none(value)

    @model_validator(mode="after")
    def validate_timestamp_order(self) -> "PersistedObjectEnvelope":
        if self.created_at and self.updated_at and self.updated_at < self.created_at:
            raise ValueError("updated_at may not be earlier than created_at")
        return self


__all__ = [
    "AcceptanceProfile",
    "ArtifactMultiplicity",
    "ArtifactPersistence",
    "ControlPlane",
    "GateStrictness",
    "LoopEdgeKind",
    "LoopTerminalClass",
    "OutlineMode",
    "PersistedObjectEnvelope",
    "PersistedObjectKind",
    "PersistedObjectStatus",
    "QueueMutationPolicy",
    "RegistryObjectRef",
    "RegistryObjectSource",
    "RegistrySourceKind",
    "RegistryTier",
    "ResearchAssumption",
    "ResearchParticipationMode",
    "StageIdempotencePolicy",
    "StageOverrideField",
    "TaskBreadth",
    "TaskDecompositionStyle",
]
