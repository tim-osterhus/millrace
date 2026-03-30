"""Registry discovery and persistence helpers for Phase 01B objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from importlib.resources import as_file, files
from pathlib import Path, PurePosixPath
from typing import Any
import json

from pydantic import TypeAdapter

from .contracts import (
    LoopArchitectureCatalog,
    LoopConfigDefinition,
    PersistedArchitectureObject,
    PersistedObjectKind,
    PersistedObjectStatus,
    RegistryObjectRef,
    RegistrySourceKind,
    RegistryTier,
)
from .markdown import write_text_atomic


_PACKAGED_REGISTRY_PACKAGE = "millrace_engine.assets"
_PACKAGED_REGISTRY_DIR = "registry"
_WORKSPACE_REGISTRY_ROOT = PurePosixPath("agents", "registry")
_REGISTRY_OBJECT_ADAPTER = TypeAdapter(PersistedArchitectureObject)
_WORKSPACE_PERSISTED_SOURCE_KINDS = frozenset(
    {
        RegistrySourceKind.WORKSPACE_DEFINED,
        RegistrySourceKind.ADVISOR_SAVED,
        RegistrySourceKind.IMPORTED,
    }
)


class RegistryError(RuntimeError):
    """Raised when registry discovery or persistence fails."""


class RegistryLayer(str, Enum):
    PACKAGED = "packaged"
    WORKSPACE = "workspace"


@dataclass(frozen=True, slots=True)
class RegistryDocument:
    """One discovered registry object plus its deterministic relative paths."""

    definition: PersistedArchitectureObject
    layer: RegistryLayer
    json_relative_path: PurePosixPath
    markdown_relative_path: PurePosixPath

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.definition.kind, self.definition.id, self.definition.version)


@dataclass(frozen=True, slots=True)
class RegistryDiscovery:
    """Discovered packaged/workspace registry state."""

    packaged: tuple[RegistryDocument, ...]
    workspace: tuple[RegistryDocument, ...]
    effective: tuple[RegistryDocument, ...]
    shadowed_packaged: tuple[RegistryDocument, ...]
    catalog: LoopArchitectureCatalog | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceRegistryWriteReport:
    """Deterministic summary of one workspace registry write."""

    definition: PersistedArchitectureObject
    json_path: Path
    markdown_path: Path
    created: bool


def workspace_registry_root(workspace_root: Path | str) -> Path:
    """Return the workspace registry root under one Millrace workspace."""

    return Path(workspace_root).expanduser().resolve().joinpath(*_WORKSPACE_REGISTRY_ROOT.parts)


def ensure_workspace_registry_layout(workspace_root: Path | str) -> Path:
    """Create the standard workspace registry tree if it is missing."""

    root = workspace_registry_root(workspace_root)
    for relative_path in _registry_directory_paths():
        root.joinpath(*relative_path.parts).mkdir(parents=True, exist_ok=True)
    return root


def render_registry_definition_json(definition: PersistedArchitectureObject) -> str:
    """Render one canonical JSON registry object deterministically."""

    payload = definition.model_dump(mode="json")
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def render_registry_companion_markdown(definition: PersistedArchitectureObject) -> str:
    """Render the generated Markdown companion for one registry object."""

    aliases = ", ".join(f"`{alias}`" for alias in definition.aliases) if definition.aliases else "_none_"
    labels = ", ".join(f"`{label}`" for label in definition.labels) if definition.labels else "_none_"
    source_ref = f"`{definition.source.ref}`" if definition.source.ref else "_none_"
    extends = "_none_"
    if definition.extends is not None:
        extends = (
            f"`{definition.extends.kind.value}:{definition.extends.id}@{definition.extends.version}`"
        )
    summary = definition.summary or "_none_"
    created_at = _format_datetime(definition.created_at) if definition.created_at is not None else "_none_"
    updated_at = _format_datetime(definition.updated_at) if definition.updated_at is not None else "_none_"
    payload_text = json.dumps(definition.payload.model_dump(mode="json"), indent=2, sort_keys=True)

    lines = [
        f"# {definition.title}",
        "",
        "> Generated from the canonical JSON registry object. Edit the JSON file, not this companion.",
        "",
        f"- Kind: `{definition.kind}`",
        f"- Canonical ID: `{definition.id}`",
        f"- Version: `{definition.version}`",
        f"- Tier: `{definition.tier.value}`",
        f"- Status: `{definition.status.value}`",
        f"- Source Kind: `{definition.source.kind.value}`",
        f"- Source Ref: {source_ref}",
        f"- Aliases: {aliases}",
        f"- Labels: {labels}",
        f"- Extends: {extends}",
        f"- Created At: {created_at}",
        f"- Updated At: {updated_at}",
        "",
        "## Summary",
        "",
        summary,
        "",
        "## Payload",
        "",
        "```json",
        payload_text,
        "```",
        "",
    ]
    return "\n".join(lines)


def discover_registry_state(
    workspace_root: Path | str,
    *,
    validate_catalog: bool = True,
) -> RegistryDiscovery:
    """Discover packaged defaults plus workspace registry objects."""

    packaged = _discover_packaged_documents()
    workspace = _discover_workspace_documents(workspace_root)

    effective_map: dict[tuple[str, str, str], RegistryDocument] = {document.key: document for document in packaged}
    shadowed_packaged: list[RegistryDocument] = []
    for document in workspace:
        if document.key in effective_map:
            shadowed_packaged.append(effective_map[document.key])
        effective_map[document.key] = document

    effective = tuple(sorted(effective_map.values(), key=_document_sort_key))
    catalog = None
    if validate_catalog:
        catalog = LoopArchitectureCatalog.model_validate(
            {"objects": [document.definition for document in effective]}
        )

    return RegistryDiscovery(
        packaged=packaged,
        workspace=workspace,
        effective=effective,
        shadowed_packaged=tuple(sorted(shadowed_packaged, key=_document_sort_key)),
        catalog=catalog,
    )


def persist_workspace_registry_object(
    workspace_root: Path | str,
    definition: PersistedArchitectureObject,
    *,
    overwrite: bool = False,
    timestamp: datetime | None = None,
    _allow_autosaved_transition: bool = False,
) -> WorkspaceRegistryWriteReport:
    """Persist one workspace registry object and its generated Markdown companion."""

    if definition.source.kind not in _WORKSPACE_PERSISTED_SOURCE_KINDS:
        allowed = ", ".join(sorted(kind.value for kind in _WORKSPACE_PERSISTED_SOURCE_KINDS))
        raise RegistryError(
            "workspace registry objects must use one of the persisted workspace source kinds: "
            f"{allowed}"
        )

    root = ensure_workspace_registry_layout(workspace_root)
    existing_document = _find_workspace_document(root, definition)
    if existing_document is not None:
        if existing_document.definition.tier is RegistryTier.AUTOSAVED and not _allow_autosaved_transition:
            raise RegistryError(
                f"autosaved registry object {definition.kind}:{definition.id}@{definition.version} is immutable until promotion"
            )
        if not overwrite:
            raise RegistryError(
                f"workspace registry object already exists: {definition.kind}:{definition.id}@{definition.version}"
            )

    normalized = _normalize_workspace_definition(definition, timestamp=timestamp, existing=existing_document)
    relative_json_path = registry_json_relative_path(normalized)
    json_path = root.joinpath(*relative_json_path.parts)
    markdown_path = json_path.with_suffix(".md")

    write_text_atomic(json_path, render_registry_definition_json(normalized))
    write_text_atomic(markdown_path, render_registry_companion_markdown(normalized))

    return WorkspaceRegistryWriteReport(
        definition=normalized,
        json_path=json_path,
        markdown_path=markdown_path,
        created=existing_document is None,
    )


def promote_workspace_registry_object(
    workspace_root: Path | str,
    ref: RegistryObjectRef,
    *,
    target_tier: RegistryTier,
    source_kind: RegistrySourceKind | None = None,
    timestamp: datetime | None = None,
) -> WorkspaceRegistryWriteReport:
    """Promote one autosaved workspace object into a mutable tier."""

    if target_tier in {RegistryTier.AUTOSAVED, RegistryTier.LEGACY}:
        raise RegistryError("promotion target_tier must be one of default, golden, niche, or ad_hoc")

    document = load_workspace_registry_document(workspace_root, ref)
    if document.definition.tier is not RegistryTier.AUTOSAVED:
        raise RegistryError(
            f"registry object {ref.kind.value}:{ref.id}@{ref.version} is not autosaved and cannot be promoted"
        )

    updated = _revalidate_definition(
        document.definition,
        {
            "tier": target_tier.value,
            "status": PersistedObjectStatus.ACTIVE.value,
            "source": {
                "kind": (source_kind or document.definition.source.kind).value,
                "ref": _workspace_source_ref(document.definition),
            },
            "updated_at": _timestamp_value(timestamp).isoformat(),
        },
    )
    return persist_workspace_registry_object(
        workspace_root,
        updated,
        overwrite=True,
        timestamp=timestamp,
        _allow_autosaved_transition=True,
    )


def demote_workspace_registry_object_to_legacy(
    workspace_root: Path | str,
    ref: RegistryObjectRef,
    *,
    timestamp: datetime | None = None,
) -> WorkspaceRegistryWriteReport:
    """Demote one workspace registry object into the legacy tier instead of deleting it."""

    document = load_workspace_registry_document(workspace_root, ref)
    if document.definition.tier is RegistryTier.AUTOSAVED:
        raise RegistryError(
            f"autosaved registry object {ref.kind.value}:{ref.id}@{ref.version} must be promoted before demotion"
        )
    updated = _revalidate_definition(
        document.definition,
        {
            "tier": RegistryTier.LEGACY.value,
            "status": PersistedObjectStatus.LEGACY.value,
            "updated_at": _timestamp_value(timestamp).isoformat(),
        },
    )
    return persist_workspace_registry_object(workspace_root, updated, overwrite=True, timestamp=timestamp)


def load_workspace_registry_document(workspace_root: Path | str, ref: RegistryObjectRef) -> RegistryDocument:
    """Load one workspace registry document by canonical object reference."""

    root = workspace_registry_root(workspace_root)
    if not root.exists():
        raise RegistryError(f"workspace registry root does not exist: {root.as_posix()}")
    for document in _iter_documents_from_root(root, layer=RegistryLayer.WORKSPACE):
        if document.key == (ref.kind.value, ref.id, ref.version):
            return document
    raise RegistryError(f"workspace registry object is missing: {ref.kind.value}:{ref.id}@{ref.version}")


def registry_json_relative_path(definition: PersistedArchitectureObject) -> PurePosixPath:
    """Return the deterministic JSON relative path for one registry object."""

    filename = f"{definition.id}__{definition.version}.json"
    return _family_relative_dir(definition).joinpath(filename)


def _discover_packaged_documents() -> tuple[RegistryDocument, ...]:
    root = files(_PACKAGED_REGISTRY_PACKAGE).joinpath(_PACKAGED_REGISTRY_DIR)
    with as_file(root) as packaged_root:
        return _iter_documents_from_root(packaged_root, layer=RegistryLayer.PACKAGED)


def _discover_workspace_documents(workspace_root: Path | str) -> tuple[RegistryDocument, ...]:
    root = workspace_registry_root(workspace_root)
    if not root.exists():
        return ()
    return _iter_documents_from_root(root, layer=RegistryLayer.WORKSPACE)


def _iter_documents_from_root(root: Path, *, layer: RegistryLayer) -> tuple[RegistryDocument, ...]:
    documents: list[RegistryDocument] = []
    seen: dict[tuple[str, str, str], PurePosixPath] = {}
    if not root.exists():
        return ()
    for path in sorted(root.rglob("*.json")):
        relative_path = PurePosixPath(path.relative_to(root).as_posix())
        definition = _load_registry_definition(path)
        expected_relative_path = registry_json_relative_path(definition)
        if relative_path != expected_relative_path:
            raise RegistryError(
                f"registry object {definition.kind}:{definition.id}@{definition.version} is stored at "
                f"{relative_path.as_posix()} but must live at {expected_relative_path.as_posix()}"
            )
        _validate_registry_document_files(path, definition, layer=layer)
        key = (definition.kind, definition.id, definition.version)
        if key in seen:
            raise RegistryError(
                f"registry layer {layer.value} contains duplicate object {definition.kind}:{definition.id}@{definition.version} "
                f"at {seen[key].as_posix()} and {relative_path.as_posix()}"
            )
        seen[key] = relative_path
        documents.append(
            RegistryDocument(
                definition=definition,
                layer=layer,
                json_relative_path=relative_path,
                markdown_relative_path=relative_path.with_suffix(".md"),
            )
        )
    return tuple(sorted(documents, key=_document_sort_key))


def _load_registry_definition(path: Path) -> PersistedArchitectureObject:
    try:
        return _REGISTRY_OBJECT_ADAPTER.validate_json(path.read_bytes())
    except Exception as exc:  # pragma: no cover - pydantic exception details are sufficient
        raise RegistryError(f"invalid registry object at {path.as_posix()}: {exc}") from exc


def _find_workspace_document(root: Path, definition: PersistedArchitectureObject) -> RegistryDocument | None:
    if not root.exists():
        return None
    target_key = (definition.kind, definition.id, definition.version)
    for document in _iter_documents_from_root(root, layer=RegistryLayer.WORKSPACE):
        if document.key == target_key:
            return document
    return None


def _normalize_workspace_definition(
    definition: PersistedArchitectureObject,
    *,
    timestamp: datetime | None,
    existing: RegistryDocument | None,
) -> PersistedArchitectureObject:
    moment = _timestamp_value(timestamp)
    created_at = definition.created_at or (existing.definition.created_at if existing is not None else moment)
    updated_at = moment
    return _revalidate_definition(
        definition,
        {
            "source": {
                "kind": definition.source.kind.value,
                "ref": _workspace_source_ref(definition),
            },
            "created_at": created_at.isoformat(),
            "updated_at": updated_at.isoformat(),
        },
    )


def _workspace_source_ref(definition: PersistedArchitectureObject) -> str:
    return _WORKSPACE_REGISTRY_ROOT.joinpath(*registry_json_relative_path(definition).parts).as_posix()


def _packaged_source_ref(definition: PersistedArchitectureObject) -> str:
    return PurePosixPath(_PACKAGED_REGISTRY_DIR).joinpath(*registry_json_relative_path(definition).parts).as_posix()


def _revalidate_definition(
    definition: PersistedArchitectureObject,
    updates: dict[str, Any],
) -> PersistedArchitectureObject:
    payload = definition.model_dump(mode="json")
    payload.update(updates)
    return _REGISTRY_OBJECT_ADAPTER.validate_python(payload)


def _family_relative_dir(definition: PersistedArchitectureObject) -> PurePosixPath:
    kind = PersistedObjectKind(definition.kind)
    if kind is PersistedObjectKind.REGISTERED_STAGE_KIND:
        return PurePosixPath("stages")
    if kind is PersistedObjectKind.LOOP_CONFIG:
        loop_definition = definition
        if not isinstance(loop_definition, LoopConfigDefinition):
            raise RegistryError("loop_config objects must validate as LoopConfigDefinition")
        return PurePosixPath("loops", loop_definition.payload.plane.value)
    if kind is PersistedObjectKind.MODE:
        return PurePosixPath("modes")
    if kind is PersistedObjectKind.TASK_AUTHORING_PROFILE:
        return PurePosixPath("task_authoring")
    if kind is PersistedObjectKind.MODEL_PROFILE:
        return PurePosixPath("model_profiles")
    raise RegistryError(f"unsupported registry kind: {kind.value}")


def _registry_directory_paths() -> tuple[PurePosixPath, ...]:
    return (
        PurePosixPath("."),
        PurePosixPath("stages"),
        PurePosixPath("loops"),
        PurePosixPath("loops", "execution"),
        PurePosixPath("loops", "research"),
        PurePosixPath("modes"),
        PurePosixPath("task_authoring"),
        PurePosixPath("model_profiles"),
    )


def _document_sort_key(document: RegistryDocument) -> tuple[str, str, str, str]:
    return (
        document.definition.kind,
        document.definition.id,
        document.definition.version,
        document.layer.value,
    )


def _validate_registry_document_files(
    json_path: Path,
    definition: PersistedArchitectureObject,
    *,
    layer: RegistryLayer,
) -> None:
    expected_source_ref = (
        _packaged_source_ref(definition) if layer is RegistryLayer.PACKAGED else _workspace_source_ref(definition)
    )
    if definition.source.ref != expected_source_ref:
        raise RegistryError(
            f"registry object {definition.kind}:{definition.id}@{definition.version} declares source ref "
            f"{definition.source.ref!r} but must use {expected_source_ref!r}"
        )

    if layer is RegistryLayer.PACKAGED:
        if definition.source.kind is not RegistrySourceKind.PACKAGED_DEFAULT:
            raise RegistryError(
                f"packaged registry object {definition.kind}:{definition.id}@{definition.version} must use "
                "source kind packaged_default"
            )
    elif definition.source.kind not in _WORKSPACE_PERSISTED_SOURCE_KINDS:
        allowed = ", ".join(sorted(kind.value for kind in _WORKSPACE_PERSISTED_SOURCE_KINDS))
        raise RegistryError(
            f"workspace registry object {definition.kind}:{definition.id}@{definition.version} must use one of: {allowed}"
        )

    markdown_path = json_path.with_suffix(".md")
    if not markdown_path.exists():
        raise RegistryError(
            f"registry object {definition.kind}:{definition.id}@{definition.version} is missing companion markdown "
            f"{markdown_path.name}"
        )
    expected_markdown = render_registry_companion_markdown(definition)
    if markdown_path.read_text(encoding="utf-8") != expected_markdown:
        raise RegistryError(
            f"registry object {definition.kind}:{definition.id}@{definition.version} companion markdown is out of sync"
        )


def _format_datetime(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def _timestamp_value(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "RegistryDiscovery",
    "RegistryDocument",
    "RegistryError",
    "RegistryLayer",
    "WorkspaceRegistryWriteReport",
    "demote_workspace_registry_object_to_legacy",
    "discover_registry_state",
    "ensure_workspace_registry_layout",
    "load_workspace_registry_document",
    "persist_workspace_registry_object",
    "promote_workspace_registry_object",
    "registry_json_relative_path",
    "render_registry_companion_markdown",
    "render_registry_definition_json",
    "workspace_registry_root",
]
