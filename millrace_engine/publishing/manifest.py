"""Staging-manifest parsing and resolution."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field, field_validator, model_validator

from ..baseline_assets import packaged_baseline_asset
from ..contracts import ContractModel

if TYPE_CHECKING:
    from ..paths import RuntimePaths


_PACKAGED_MANIFEST_REF = "package:agents/staging_manifest.yml"


class StagingManifestError(RuntimeError):
    """Raised when the staging-manifest payload is invalid."""


def _strip_inline_comment(value: str) -> str:
    in_single = False
    in_double = False
    result: list[str] = []
    for char in value:
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            break
        result.append(char)
    return "".join(result).strip()


def _unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _normalize_manifest_path(value: object, *, label: str) -> str:
    text = str(value).strip().rstrip("/")
    if text in {"", ".", ".."}:
        raise ValueError(f"{label} may not be empty")
    if text.startswith("/"):
        raise ValueError(f"{label} must be repo-relative")
    parts = Path(text).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"{label} contains an invalid path segment")
    return Path(*parts).as_posix()


def _normalize_manifest_path_list(value: object, *, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be a list")
    normalized: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value, start=1):
        path = _normalize_manifest_path(item, label=f"{label}[{index}]")
        if path in seen:
            continue
        seen.add(path)
        normalized.append(path)
    return tuple(normalized)


class StagingManifest(ContractModel):
    """Typed staging-manifest payload."""

    version: int = Field(ge=1)
    paths: tuple[str, ...]
    optional_paths: tuple[str, ...] = ()

    @field_validator("paths", "optional_paths", mode="before")
    @classmethod
    def normalize_paths(
        cls,
        value: object,
        info: object,
    ) -> tuple[str, ...]:
        field_name = getattr(info, "field_name", "paths")
        return _normalize_manifest_path_list(value, label=field_name)

    @model_validator(mode="after")
    def validate_required_paths(self) -> "StagingManifest":
        if not self.paths:
            raise ValueError("paths may not be empty")
        overlap = sorted(set(self.paths).intersection(self.optional_paths))
        if overlap:
            raise ValueError(
                "paths and optional_paths may not overlap: " + ", ".join(overlap)
            )
        return self


class LoadedStagingManifest(ContractModel):
    """Manifest plus the source it came from."""

    manifest: StagingManifest
    source_kind: Literal["workspace", "packaged"]
    source_ref: str

    @field_validator("source_ref")
    @classmethod
    def normalize_source_ref(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("source_ref may not be empty")
        return normalized


def parse_staging_manifest(payload: str) -> StagingManifest:
    """Parse the narrow YAML subset used by the staging manifest."""

    version: int | None = None
    sections: dict[str, list[str]] = {"paths": [], "optional_paths": []}
    active_section: str | None = None

    for raw_line in payload.splitlines():
        line = _strip_inline_comment(raw_line)
        if not line:
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            if active_section is None:
                raise StagingManifestError("manifest list item appears before a section header")
            sections.setdefault(active_section, []).append(_unquote(stripped[2:].strip()))
            continue
        if ":" not in stripped:
            raise StagingManifestError(f"unsupported manifest line: {raw_line.strip()}")
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if key == "version":
            if not raw_value:
                raise StagingManifestError("manifest version is missing")
            try:
                version = int(_unquote(raw_value))
            except ValueError as exc:
                raise StagingManifestError("manifest version must be an integer") from exc
            active_section = None
            continue
        if key in {"paths", "optional_paths"}:
            if raw_value:
                raise StagingManifestError(f"{key} must be declared as a list section")
            active_section = key
            sections.setdefault(key, [])
            continue
        raise StagingManifestError(f"unsupported manifest key: {key}")

    if version is None:
        raise StagingManifestError("manifest version is missing")
    try:
        return StagingManifest.model_validate(
            {
                "version": version,
                "paths": sections.get("paths", []),
                "optional_paths": sections.get("optional_paths", []),
            }
        )
    except ValueError as exc:
        raise StagingManifestError(str(exc)) from exc


def load_staging_manifest(paths: "RuntimePaths") -> LoadedStagingManifest:
    """Load the workspace manifest or fall back to the packaged baseline."""

    workspace_manifest = paths.staging_manifest_file
    if workspace_manifest.exists():
        payload = workspace_manifest.read_text(encoding="utf-8")
        manifest = parse_staging_manifest(payload)
        return LoadedStagingManifest(
            manifest=manifest,
            source_kind="workspace",
            source_ref=workspace_manifest.as_posix(),
        )

    packaged_manifest = packaged_baseline_asset("agents/staging_manifest.yml")
    if not packaged_manifest.is_file():
        raise StagingManifestError("packaged staging manifest is missing")
    manifest = parse_staging_manifest(packaged_manifest.read_text(encoding="utf-8"))
    return LoadedStagingManifest(
        manifest=manifest,
        source_kind="packaged",
        source_ref=_PACKAGED_MANIFEST_REF,
    )
