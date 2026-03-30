"""GoalSpec state and stable-spec registry contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal
import ast
import json

from pydantic import Field, field_serializer, field_validator, model_validator

from ..contracts import ContractModel, _normalize_datetime
from ..markdown import write_text_atomic


SCHEMA_VERSION = "1.0"
INITIAL_FAMILY_PLAN_SCHEMA_VERSION = "1.0"
INITIAL_FAMILY_FREEZE_MODE = "post-governor-v1"

GoalSpecStatus = Literal["planned", "emitted", "reviewed", "decomposed"]
GoalSpecFamilyPhase = Literal["initial_family", "goal_gap_remediation"]
GoalSpecDecompositionProfile = Literal["", "trivial", "simple", "moderate", "involved", "complex", "massive"]
FrozenTier = Literal["", "golden", "phase"]
SpecReviewStatus = Literal["pending", "approved", "blocked", "no_material_delta"]
SpecReviewSeverity = Literal["blocker", "major", "minor", "note"]
GoalSpecReviewStatus = SpecReviewStatus
GoalSpecReviewSeverity = SpecReviewSeverity


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_required_text(value: str, *, field_name: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError(f"{field_name} may not be empty")
    return normalized


def _normalize_optional_text(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.strip().split())


def _normalize_optional_datetime(value: datetime | str | None) -> datetime | None:
    if value in (None, ""):
        return None
    return _normalize_datetime(value)


def _normalize_token_sequence(values: list[str]) -> tuple[str, ...]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = value.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return tuple(deduped)


def _normalize_path_token(value: str | Path | None) -> str:
    if value is None:
        return ""
    if isinstance(value, Path):
        return value.as_posix()
    stripped = value.strip()
    if not stripped:
        return ""
    return Path(stripped).as_posix()


def _normalize_path_sequence(values: list[str | Path]) -> tuple[str, ...]:
    return _normalize_token_sequence([_normalize_path_token(value) for value in values])


def _path_token(path: Path, *, relative_to: Path | None = None) -> str:
    candidate = path
    if relative_to is not None:
        try:
            candidate = path.relative_to(relative_to)
        except ValueError:
            pass
    return candidate.as_posix()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _frozen_tier(spec_path: str) -> FrozenTier:
    lowered = spec_path.lower()
    if "/golden/" in lowered or "golden" in lowered:
        return "golden"
    if "/phase/" in lowered or "phase" in lowered:
        return "phase"
    return ""


def _parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    data: dict[str, str] = {}
    for raw_line in text[4:end].splitlines():
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        data[key.strip()] = value.strip()
    return data


def _parse_string_list(raw: str) -> tuple[str, ...]:
    stripped = raw.strip()
    if not stripped:
        return ()
    try:
        parsed = ast.literal_eval(stripped)
    except (SyntaxError, ValueError):
        parsed = None
    if isinstance(parsed, list):
        return _normalize_token_sequence([str(item) for item in parsed])
    return _normalize_token_sequence(part for part in stripped.strip("[]").split(","))


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _write_json_model(path: Path, model: ContractModel) -> None:
    payload = json.loads(model.model_dump_json(exclude_none=False))
    write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


class StableSpecRecord(ContractModel):
    """One stable-spec registry entry."""

    spec_path: str
    checksum_sha256: str
    frozen: bool = False
    frozen_tier: FrozenTier = ""
    freeze_marker: str = ""
    checksum_marker: str = ""

    @field_validator("spec_path")
    @classmethod
    def validate_spec_path(cls, value: str) -> str:
        normalized = _normalize_path_token(value)
        if not normalized:
            raise ValueError("spec_path may not be empty")
        return normalized

    @field_validator("freeze_marker", "checksum_marker", mode="before")
    @classmethod
    def normalize_optional_paths(cls, value: str | Path | None) -> str:
        return _normalize_path_token(value)

    @field_validator("checksum_sha256")
    @classmethod
    def validate_checksum(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
            raise ValueError("checksum_sha256 must be a 64-character lowercase hex digest")
        return normalized

    @model_validator(mode="after")
    def validate_frozen_fields(self) -> "StableSpecRecord":
        if self.frozen:
            if self.frozen_tier == "":
                raise ValueError("frozen_tier is required when frozen is true")
            if not self.freeze_marker or not self.checksum_marker:
                raise ValueError("frozen marker paths are required when frozen is true")
            return self
        if self.frozen_tier or self.freeze_marker or self.checksum_marker:
            raise ValueError("non-frozen entries may not set frozen_tier or marker paths")
        return self


class StableSpecRegistry(ContractModel):
    """On-disk registry for immutable stable GoalSpec artifacts."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    updated_at: datetime | None = None
    stable_specs: tuple[StableSpecRecord, ...] = ()

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_optional_datetime(value)

    @model_validator(mode="after")
    def validate_unique_paths(self) -> "StableSpecRegistry":
        spec_paths = [entry.spec_path for entry in self.stable_specs]
        if len(set(spec_paths)) != len(spec_paths):
            raise ValueError("stable_specs may not contain duplicate spec_path entries")
        return self


class GoalSpecArtifactMetadata(ContractModel):
    """Frontmatter metadata extracted from one GoalSpec artifact."""

    spec_id: str
    title: str = ""
    decomposition_profile: GoalSpecDecompositionProfile = ""
    depends_on_specs: tuple[str, ...] = ()
    source_path: str = ""

    @field_validator("spec_id")
    @classmethod
    def validate_spec_id(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="spec_id")

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: str | None) -> str:
        return _normalize_optional_text(value)

    @field_validator("depends_on_specs", mode="before")
    @classmethod
    def normalize_depends_on_specs(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        return _normalize_token_sequence([str(item) for item in value])

    @field_validator("source_path", mode="before")
    @classmethod
    def normalize_source_path(cls, value: str | Path | None) -> str:
        return _normalize_path_token(value)


class GoalSpecLineageRecord(ContractModel):
    """Minimal lineage envelope for one GoalSpec across review and decomposition."""

    spec_id: str
    goal_id: str = ""
    source_idea_path: str = ""
    queue_path: str = ""
    reviewed_path: str = ""
    archived_path: str = ""
    stable_spec_paths: tuple[str, ...] = ()
    pending_shard_path: str = ""

    @field_validator("spec_id")
    @classmethod
    def validate_spec_id(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="spec_id")

    @field_validator("goal_id", mode="before")
    @classmethod
    def normalize_goal_id(cls, value: str | None) -> str:
        return _normalize_optional_text(value)

    @field_validator(
        "source_idea_path",
        "queue_path",
        "reviewed_path",
        "archived_path",
        "pending_shard_path",
        mode="before",
    )
    @classmethod
    def normalize_paths(cls, value: str | Path | None) -> str:
        return _normalize_path_token(value)

    @field_validator("stable_spec_paths", mode="before")
    @classmethod
    def normalize_stable_paths(
        cls,
        value: tuple[str, ...] | list[str] | tuple[Path, ...] | list[Path] | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        return _normalize_path_sequence(list(value))


class GoalSpecReviewFinding(ContractModel):
    """One bounded review finding emitted by Spec Review."""

    finding_id: str
    severity: SpecReviewSeverity
    summary: str
    artifact_path: str = ""

    @field_validator("finding_id", "summary")
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("artifact_path", mode="before")
    @classmethod
    def normalize_artifact_path(cls, value: str | Path | None) -> str:
        return _normalize_path_token(value)


class GoalSpecReviewRecord(ContractModel):
    """Minimal spec-review result envelope for later handlers."""

    spec_id: str
    review_status: SpecReviewStatus = "pending"
    questions_path: str = ""
    decision_path: str = ""
    reviewed_path: str = ""
    reviewed_at: datetime | None = None
    findings: tuple[GoalSpecReviewFinding, ...] = ()

    @field_validator("spec_id")
    @classmethod
    def validate_spec_id(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="spec_id")

    @field_validator("questions_path", "decision_path", "reviewed_path", mode="before")
    @classmethod
    def normalize_paths(cls, value: str | Path | None) -> str:
        return _normalize_path_token(value)

    @field_validator("reviewed_at", mode="before")
    @classmethod
    def normalize_reviewed_at(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_optional_datetime(value)


class GoalSpecFamilyGovernorState(ContractModel):
    """Family-governor snapshot needed by initial-family plan freezing."""

    policy_path: str = ""
    initial_family_max_specs: int = Field(default=0, ge=0)
    remediation_family_max_specs: int = Field(default=0, ge=0)
    applied_family_max_specs: int = Field(default=0, ge=0)

    @field_validator("policy_path", mode="before")
    @classmethod
    def normalize_policy_path(cls, value: str | Path | None) -> str:
        return _normalize_path_token(value)


class GoalSpecFamilySpecState(ContractModel):
    """One spec entry inside the pending family runtime state."""

    status: GoalSpecStatus = "planned"
    review_status: GoalSpecReviewStatus = "pending"
    depends_on_specs: tuple[str, ...] = ()
    title: str = ""
    decomposition_profile: GoalSpecDecompositionProfile = ""
    queue_path: str = ""
    reviewed_path: str = ""
    archived_path: str = ""
    stable_spec_paths: tuple[str, ...] = ()
    review_questions_path: str = ""
    review_decision_path: str = ""
    pending_shard_path: str = ""

    @field_validator("depends_on_specs", mode="before")
    @classmethod
    def normalize_depends_on_specs(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        return _normalize_token_sequence([str(item) for item in value])

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: str | None) -> str:
        return _normalize_optional_text(value)

    @field_validator(
        "queue_path",
        "reviewed_path",
        "archived_path",
        "review_questions_path",
        "review_decision_path",
        "pending_shard_path",
        mode="before",
    )
    @classmethod
    def normalize_paths(cls, value: str | Path | None) -> str:
        return _normalize_path_token(value)

    @field_validator("stable_spec_paths", mode="before")
    @classmethod
    def normalize_stable_spec_paths(
        cls,
        value: tuple[str, ...] | list[str] | tuple[Path, ...] | list[Path] | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        return _normalize_path_sequence(list(value))

    def lineage(self, *, spec_id: str, goal_id: str = "", source_idea_path: str = "") -> GoalSpecLineageRecord:
        """Project one spec-state entry into a reusable lineage record."""

        stable_paths = _normalize_token_sequence(
            [
                *self.stable_spec_paths,
                *(path for path in (self.reviewed_path, self.archived_path) if path.startswith("agents/specs/stable/")),
            ]
        )
        return GoalSpecLineageRecord(
            spec_id=spec_id,
            goal_id=goal_id,
            source_idea_path=source_idea_path,
            queue_path=self.queue_path,
            reviewed_path=self.reviewed_path,
            archived_path=self.archived_path,
            stable_spec_paths=stable_paths,
            pending_shard_path=self.pending_shard_path,
        )


class FrozenInitialFamilySpecPlan(ContractModel):
    """Frozen snapshot of one spec entry inside the initial family plan."""

    declared_status: GoalSpecStatus = "planned"
    title: str = ""
    decomposition_profile: GoalSpecDecompositionProfile = ""
    depends_on_specs: tuple[str, ...] = ()

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: str | None) -> str:
        return _normalize_optional_text(value)

    @field_validator("depends_on_specs", mode="before")
    @classmethod
    def normalize_depends_on_specs(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        return _normalize_token_sequence([str(item) for item in value])


class InitialFamilyPlan(ContractModel):
    """Frozen post-governor initial family plan snapshot."""

    schema_version: Literal["1.0"] = INITIAL_FAMILY_PLAN_SCHEMA_VERSION
    freeze_mode: Literal["post-governor-v1"] = INITIAL_FAMILY_FREEZE_MODE
    frozen: bool = True
    frozen_at: datetime
    freeze_trigger_spec_id: str = ""
    goal_id: str = ""
    source_idea_path: str = ""
    goal_sha256: str = ""
    family_policy_path: str = ""
    family_policy_sha256: str = ""
    family_cap_mode: str = "adaptive"
    initial_family_max_specs: int = Field(default=0, ge=0)
    applied_family_max_specs: int = Field(default=0, ge=0)
    spec_order: tuple[str, ...] = ()
    specs: dict[str, FrozenInitialFamilySpecPlan] = Field(default_factory=dict)
    completed_at: datetime | None = None

    @field_validator("frozen_at", "completed_at", mode="before")
    @classmethod
    def normalize_datetimes(
        cls,
        value: datetime | str | None,
        info: object,
    ) -> datetime | None:
        field_name = getattr(info, "field_name", "timestamp")
        normalized = _normalize_optional_datetime(value)
        if normalized is None and field_name == "frozen_at":
            raise ValueError("frozen_at may not be empty")
        return normalized

    @field_validator(
        "freeze_trigger_spec_id",
        "goal_id",
        "family_cap_mode",
        mode="before",
    )
    @classmethod
    def normalize_text_fields(cls, value: str | None) -> str:
        return _normalize_optional_text(value)

    @field_validator("source_idea_path", "family_policy_path", mode="before")
    @classmethod
    def normalize_path_fields(cls, value: str | Path | None) -> str:
        return _normalize_path_token(value)

    @field_validator("goal_sha256", "family_policy_sha256", mode="before")
    @classmethod
    def normalize_digest_fields(cls, value: str | None) -> str:
        normalized = _normalize_optional_text(value).lower()
        if normalized and (len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized)):
            raise ValueError("sha256 fields must be 64-character lowercase hex digests")
        return normalized

    @field_validator("spec_order", mode="before")
    @classmethod
    def normalize_spec_order(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        return _normalize_token_sequence([str(item) for item in value])

    @field_serializer("completed_at", when_used="json")
    def serialize_completed_at(self, value: datetime | None) -> str:
        if value is None:
            return ""
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @model_validator(mode="after")
    def validate_spec_order(self) -> "InitialFamilyPlan":
        if set(self.spec_order) != set(self.specs.keys()):
            raise ValueError("initial_family_plan spec_order and specs keys must match")
        if len(set(self.spec_order)) != len(self.spec_order):
            raise ValueError("initial_family_plan spec_order may not contain duplicates")
        return self

    def core_view(self) -> dict[str, Any]:
        """Return the frozen fields used to detect plan drift."""

        return {
            "freeze_mode": self.freeze_mode,
            "goal_id": self.goal_id,
            "source_idea_path": self.source_idea_path,
            "goal_sha256": self.goal_sha256,
            "family_policy_path": self.family_policy_path,
            "family_policy_sha256": self.family_policy_sha256,
            "family_cap_mode": self.family_cap_mode,
            "initial_family_max_specs": self.initial_family_max_specs,
            "applied_family_max_specs": self.applied_family_max_specs,
            "spec_order": self.spec_order,
            "specs": self.specs,
        }


class GoalSpecFamilyState(ContractModel):
    """Restart-safe pending-family state for GoalSpec decomposition."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    goal_id: str = ""
    source_idea_path: str = ""
    family_phase: GoalSpecFamilyPhase = "initial_family"
    family_complete: bool = False
    active_spec_id: str = ""
    spec_order: tuple[str, ...] = ()
    specs: dict[str, GoalSpecFamilySpecState] = Field(default_factory=dict)
    initial_family_plan: InitialFamilyPlan | None = None
    family_governor: GoalSpecFamilyGovernorState | None = None
    updated_at: datetime | None = None

    @field_validator("goal_id", "active_spec_id", mode="before")
    @classmethod
    def normalize_text_fields(cls, value: str | None) -> str:
        return _normalize_optional_text(value)

    @field_validator("source_idea_path", mode="before")
    @classmethod
    def normalize_source_idea_path(cls, value: str | Path | None) -> str:
        return _normalize_path_token(value)

    @field_validator("spec_order", mode="before")
    @classmethod
    def normalize_spec_order(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        return _normalize_token_sequence([str(item) for item in value])

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_optional_datetime(value)

    @model_validator(mode="after")
    def validate_spec_graph(self) -> "GoalSpecFamilyState":
        known_specs = set(self.specs.keys())
        if known_specs != set(self.spec_order):
            raise ValueError("spec_order and specs keys must match")
        if len(set(self.spec_order)) != len(self.spec_order):
            raise ValueError("spec_order may not contain duplicate spec ids")
        if self.active_spec_id and self.active_spec_id not in known_specs:
            raise ValueError("active_spec_id references an unknown spec")
        for spec_id, payload in self.specs.items():
            missing = [dep for dep in payload.depends_on_specs if dep and dep not in known_specs]
            if missing:
                raise ValueError(f"spec {spec_id} depends on unknown specs: {', '.join(missing)}")
        return self

    def fulfills_initial_family_plan(self) -> bool:
        """Return True when every frozen spec has decomposed."""

        if self.initial_family_plan is None or not self.initial_family_plan.frozen:
            return False
        for spec_id in self.initial_family_plan.spec_order:
            payload = self.specs.get(spec_id)
            if payload is None or payload.status != "decomposed":
                return False
        return True


def default_goal_spec_family_state(*, updated_at: datetime | str | None = None) -> GoalSpecFamilyState:
    """Return the bootstrap GoalSpec family state."""

    return GoalSpecFamilyState(updated_at=_normalize_optional_datetime(updated_at))


def load_goal_spec_family_state(state_path: Path) -> GoalSpecFamilyState:
    """Load one persisted GoalSpec family state, bootstrapping on first use."""

    if not state_path.exists():
        return default_goal_spec_family_state()
    return GoalSpecFamilyState.model_validate(json.loads(state_path.read_text(encoding="utf-8")))


def write_goal_spec_family_state(
    state_path: Path,
    state: GoalSpecFamilyState,
    *,
    updated_at: datetime | str | None = None,
) -> GoalSpecFamilyState:
    """Persist GoalSpec family state deterministically."""

    resolved = state.model_copy(update={"updated_at": _normalize_optional_datetime(updated_at) or state.updated_at or _utcnow()})
    _write_json_model(state_path, resolved)
    return resolved


def stable_spec_metadata_from_file(
    spec_file: Path,
    *,
    relative_to: Path | None = None,
) -> GoalSpecArtifactMetadata:
    """Extract strict GoalSpec metadata from one markdown spec file."""

    text = spec_file.read_text(encoding="utf-8", errors="replace")
    frontmatter = _parse_frontmatter(text)
    spec_id = frontmatter.get("spec_id", "").strip() or spec_file.name.split("__", 1)[0].strip() or spec_file.stem
    return GoalSpecArtifactMetadata.model_validate(
        {
            "spec_id": spec_id,
            "title": frontmatter.get("title", "").strip(),
            "decomposition_profile": frontmatter.get("decomposition_profile", "").strip().lower(),
            "depends_on_specs": _parse_string_list(frontmatter.get("depends_on_specs", "")),
            "source_path": _path_token(spec_file, relative_to=relative_to),
        }
    )


def load_stable_spec_registry(index_path: Path) -> StableSpecRegistry:
    """Load the stable-spec registry or return a bootstrap default."""

    if not index_path.exists():
        return StableSpecRegistry()
    return StableSpecRegistry.model_validate(json.loads(index_path.read_text(encoding="utf-8")))


def write_stable_spec_registry(index_path: Path, registry: StableSpecRegistry) -> StableSpecRegistry:
    """Persist the stable-spec registry deterministically."""

    _write_json_model(index_path, registry)
    return registry


def refresh_stable_spec_registry(
    stable_root: Path,
    frozen_dir: Path,
    index_path: Path,
    *,
    relative_to: Path | None = None,
    updated_at: datetime | str | None = None,
) -> StableSpecRegistry:
    """Rebuild the stable-spec registry and frozen marker files from disk."""

    stable_root.mkdir(parents=True, exist_ok=True)
    frozen_dir.mkdir(parents=True, exist_ok=True)

    spec_paths = [
        path
        for path in stable_root.rglob("*.md")
        if ".frozen" not in path.parts and not any(part.startswith(".") for part in path.parts)
    ]
    spec_paths.sort(key=lambda path: path.as_posix())

    expected_marker_files: set[Path] = set()
    stable_specs: list[StableSpecRecord] = []
    timestamp = _normalize_optional_datetime(updated_at) or _utcnow()

    for spec_path in spec_paths:
        spec_token = _path_token(spec_path, relative_to=relative_to)
        checksum = _sha256_file(spec_path)
        tier = _frozen_tier(spec_token)
        frozen = tier != ""
        freeze_marker = ""
        checksum_marker = ""

        if frozen:
            safe_name = spec_token.replace("/", "__")
            marker_path = frozen_dir / f"{safe_name}.frozen"
            checksum_path = frozen_dir / f"{safe_name}.sha256"
            marker_path.write_text(
                "\n".join(
                    (
                        f"spec_path: {spec_token}",
                        f"frozen_tier: {tier}",
                        f"checksum_sha256: {checksum}",
                        f"updated_at: {timestamp.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')}",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            checksum_path.write_text(f"{checksum}  {spec_token}\n", encoding="utf-8")
            expected_marker_files.add(marker_path.resolve())
            expected_marker_files.add(checksum_path.resolve())
            freeze_marker = _path_token(marker_path, relative_to=relative_to)
            checksum_marker = _path_token(checksum_path, relative_to=relative_to)

        stable_specs.append(
            StableSpecRecord(
                spec_path=spec_token,
                checksum_sha256=checksum,
                frozen=frozen,
                frozen_tier=tier,
                freeze_marker=freeze_marker,
                checksum_marker=checksum_marker,
            )
        )

    for old_path in frozen_dir.glob("*"):
        if old_path.is_file() and old_path.resolve() not in expected_marker_files:
            old_path.unlink()

    registry = StableSpecRegistry(updated_at=timestamp, stable_specs=tuple(stable_specs))
    write_stable_spec_registry(index_path, registry)
    return registry


def build_initial_family_plan_snapshot(
    state: GoalSpecFamilyState,
    *,
    repo_root: Path,
    trigger_spec_id: str = "",
    goal_file: Path | None = None,
    policy_path: Path | None = None,
    policy_payload: dict[str, Any] | None = None,
    frozen_at: datetime | str | None = None,
) -> InitialFamilyPlan:
    """Freeze the post-governor initial-family spec snapshot."""

    if state.family_phase != "initial_family":
        raise ValueError("initial family plan freeze only applies to family_phase=initial_family")
    if not state.spec_order or not state.specs:
        raise ValueError("spec family state must contain at least one spec before freezing")
    if set(state.spec_order) != set(state.specs.keys()):
        raise ValueError("spec_order and specs keys must match before freezing")

    governor = state.family_governor or GoalSpecFamilyGovernorState()
    timestamp = _normalize_optional_datetime(frozen_at) or _utcnow()
    effective_goal_file = goal_file
    if effective_goal_file is not None and not effective_goal_file.is_absolute():
        effective_goal_file = repo_root / effective_goal_file
    if effective_goal_file is None and state.source_idea_path:
        candidate = Path(state.source_idea_path)
        if not candidate.is_absolute():
            candidate = repo_root / candidate
        if candidate.exists():
            effective_goal_file = candidate

    effective_policy_path = policy_path or (repo_root / governor.policy_path if governor.policy_path else None)
    if effective_policy_path is not None and not effective_policy_path.is_absolute():
        effective_policy_path = repo_root / effective_policy_path
    effective_policy_payload = policy_payload
    if effective_policy_payload is None and effective_policy_path is not None and effective_policy_path.exists():
        effective_policy_payload = _load_json_object(effective_policy_path)
    if effective_policy_payload is None:
        effective_policy_payload = {}

    frozen_specs = {
        spec_id: FrozenInitialFamilySpecPlan(
            declared_status=payload.status or "planned",
            title=payload.title,
            decomposition_profile=payload.decomposition_profile,
            depends_on_specs=payload.depends_on_specs,
        )
        for spec_id, payload in state.specs.items()
    }

    goal_sha256 = _sha256_file(effective_goal_file) if effective_goal_file is not None and effective_goal_file.exists() else ""
    family_policy_sha256 = (
        _sha256_file(effective_policy_path)
        if effective_policy_path is not None and effective_policy_path.exists()
        else ""
    )
    policy_token = "" if effective_policy_path is None else _path_token(effective_policy_path, relative_to=repo_root)

    return InitialFamilyPlan.model_validate(
        {
            "frozen_at": timestamp,
            "freeze_trigger_spec_id": trigger_spec_id or state.active_spec_id,
            "goal_id": state.goal_id,
            "source_idea_path": state.source_idea_path,
            "goal_sha256": goal_sha256,
            "family_policy_path": policy_token,
            "family_policy_sha256": family_policy_sha256,
            "family_cap_mode": str(effective_policy_payload.get("family_cap_mode", "")).strip() or "adaptive",
            "initial_family_max_specs": int(
                effective_policy_payload.get("initial_family_max_specs", governor.initial_family_max_specs)
            ),
            "applied_family_max_specs": int(
                governor.applied_family_max_specs or effective_policy_payload.get("initial_family_max_specs", 0)
            ),
            "spec_order": state.spec_order,
            "specs": frozen_specs,
            "completed_at": (
                timestamp
                if state.family_complete and state.fulfills_initial_family_plan()
                else None
            ),
        }
    )


__all__ = [
    "FrozenInitialFamilySpecPlan",
    "GoalSpecArtifactMetadata",
    "GoalSpecDecompositionProfile",
    "GoalSpecFamilyGovernorState",
    "GoalSpecFamilyPhase",
    "GoalSpecFamilySpecState",
    "GoalSpecFamilyState",
    "GoalSpecLineageRecord",
    "GoalSpecReviewFinding",
    "GoalSpecReviewRecord",
    "GoalSpecReviewStatus",
    "GoalSpecReviewSeverity",
    "GoalSpecStatus",
    "INITIAL_FAMILY_FREEZE_MODE",
    "INITIAL_FAMILY_PLAN_SCHEMA_VERSION",
    "InitialFamilyPlan",
    "SCHEMA_VERSION",
    "StableSpecRecord",
    "StableSpecRegistry",
    "build_initial_family_plan_snapshot",
    "default_goal_spec_family_state",
    "load_goal_spec_family_state",
    "load_stable_spec_registry",
    "refresh_stable_spec_registry",
    "stable_spec_metadata_from_file",
    "write_goal_spec_family_state",
    "write_stable_spec_registry",
]
