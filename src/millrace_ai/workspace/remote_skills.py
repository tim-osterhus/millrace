"""Remote optional skill registry and installer helpers."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

DEFAULT_REMOTE_SKILLS_INDEX_URL = (
    "https://raw.githubusercontent.com/tim-osterhus/millrace-skills/main/index.md"
)
DEFAULT_REMOTE_SKILLS_TREE_URL = (
    "https://api.github.com/repos/tim-osterhus/millrace-skills/git/trees/main?recursive=1"
)
DEFAULT_REMOTE_SKILLS_RAW_ROOT_URL = (
    "https://raw.githubusercontent.com/tim-osterhus/millrace-skills/main"
)
REMOTE_SKILLS_INDEX_FILENAME = "remote_skills_index.md"
DEFAULT_REMOTE_FETCH_TIMEOUT_SECONDS = 10.0

FetchText = Callable[[str], str]
FetchJson = Callable[[str], Mapping[str, Any]]
FetchBytes = Callable[[str], bytes]


class RemoteSkillError(RuntimeError):
    """Raised when a remote skill cannot be resolved or installed."""


@dataclass(frozen=True, slots=True)
class RemoteSkillEntry:
    skill_id: str
    description: str
    tags: tuple[str, ...]
    path: str
    status: str


@dataclass(frozen=True, slots=True)
class RemoteSkillInstallResult:
    skill_id: str
    destination: Path
    installed_files: tuple[str, ...]
    source_index_url: str
    source_tree_url: str
    source_tree_sha: str | None


def parse_remote_skill_index(index_text: str) -> tuple[RemoteSkillEntry, ...]:
    """Parse the public optional skills markdown index."""

    entries: list[RemoteSkillEntry] = []
    for raw_line in index_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or "`" not in line:
            continue
        columns = _split_markdown_table_row(line)
        if len(columns) < 5 or columns[0].lower() == "skill":
            continue
        if set(columns[0]) <= {"-", " "}:
            continue
        skill_id = _strip_markdown_code(columns[0]).strip()
        path = _strip_markdown_code(columns[3]).strip()
        status = _strip_markdown_code(columns[4]).strip().lower()
        if not skill_id or not path:
            continue
        entries.append(
            RemoteSkillEntry(
                skill_id=skill_id,
                description=_strip_markdown_code(columns[1]).strip(),
                tags=tuple(
                    tag.strip()
                    for tag in _strip_markdown_code(columns[2]).split(",")
                    if tag.strip()
                ),
                path=path,
                status=status,
            )
        )
    return tuple(entries)


def refresh_remote_skill_index(
    skills_dir: Path,
    *,
    index_url: str = DEFAULT_REMOTE_SKILLS_INDEX_URL,
    timeout_seconds: float = DEFAULT_REMOTE_FETCH_TIMEOUT_SECONDS,
    fetch_text: Callable[..., str] | None = None,
) -> Path:
    """Fetch the supported optional-skill index into the workspace skills directory."""

    text_fetcher = fetch_text or _fetch_url_text
    index_text = text_fetcher(index_url, timeout_seconds=timeout_seconds)
    destination = skills_dir / REMOTE_SKILLS_INDEX_FILENAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(index_text, encoding="utf-8")
    _append_skill_operation(
        skills_dir,
        operation="refresh_remote_index",
        skill_id="remote_skills_index",
        source=index_url,
        destination=str(destination),
        extra={"entry_count": len(parse_remote_skill_index(index_text))},
    )
    return destination


def install_remote_skill(
    skills_dir: Path,
    skill_ref: str,
    *,
    force: bool = False,
    update: bool = False,
    index_url: str = DEFAULT_REMOTE_SKILLS_INDEX_URL,
    tree_url: str = DEFAULT_REMOTE_SKILLS_TREE_URL,
    raw_root_url: str = DEFAULT_REMOTE_SKILLS_RAW_ROOT_URL,
    timeout_seconds: float = DEFAULT_REMOTE_FETCH_TIMEOUT_SECONDS,
    fetch_text: Callable[..., str] | None = None,
    fetch_json: Callable[..., Mapping[str, Any]] | None = None,
    fetch_bytes: Callable[..., bytes] | None = None,
) -> RemoteSkillInstallResult:
    """Install one available optional skill from the supported remote registry."""

    text_fetcher = fetch_text or _fetch_url_text
    json_fetcher = fetch_json or _fetch_url_json
    bytes_fetcher = fetch_bytes or _fetch_url_bytes

    index_text = text_fetcher(index_url, timeout_seconds=timeout_seconds)
    entry = _find_available_entry(skill_ref, parse_remote_skill_index(index_text))
    _reject_unsafe_skill_id(entry.skill_id)
    source_dir = _source_directory_for_entry(entry)
    destination = skills_dir / entry.skill_id
    if destination.exists() and not (force or update):
        raise RemoteSkillError(f"skill already exists: {entry.skill_id}")

    tree_payload = json_fetcher(tree_url, timeout_seconds=timeout_seconds)
    tree_sha = tree_payload.get("sha")
    file_paths = _remote_file_paths_for_source_dir(tree_payload, source_dir=source_dir)
    if not file_paths:
        raise RemoteSkillError(f"remote skill package is empty: {entry.skill_id}")

    temporary_destination = destination.with_name(f".{destination.name}.tmp")
    if temporary_destination.exists():
        shutil.rmtree(temporary_destination)
    temporary_destination.mkdir(parents=True, exist_ok=True)

    installed_files: list[str] = []
    file_hashes: dict[str, str] = {}
    try:
        for remote_path in file_paths:
            relative_path = _relative_remote_package_path(
                remote_path,
                source_dir=source_dir,
            )
            raw_url = f"{raw_root_url.rstrip('/')}/{quote(remote_path, safe='/')}"
            content = _fetch_remote_file_bytes(
                raw_url,
                timeout_seconds=timeout_seconds,
                fetch_text=text_fetcher,
                fetch_bytes=bytes_fetcher,
            )
            target = temporary_destination / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            normalized_relative = relative_path.as_posix()
            installed_files.append(normalized_relative)
            file_hashes[normalized_relative] = hashlib.sha256(content).hexdigest()

        if not temporary_destination.joinpath("SKILL.md").is_file():
            raise RemoteSkillError(f"remote skill is missing SKILL.md: {entry.skill_id}")

        _write_remote_source_metadata(
            temporary_destination,
            entry=entry,
            source_index_url=index_url,
            source_tree_url=tree_url,
            source_tree_sha=tree_sha if isinstance(tree_sha, str) else None,
            installed_files=tuple(installed_files),
            file_hashes=file_hashes,
        )
        if destination.exists():
            shutil.rmtree(destination)
        temporary_destination.replace(destination)
    except Exception:
        if temporary_destination.exists():
            shutil.rmtree(temporary_destination)
        raise

    installed_files_tuple = tuple(sorted(installed_files))
    _sync_skills_index(
        skills_dir,
        skill_id=entry.skill_id,
        skill_path=f"{entry.skill_id}/SKILL.md",
    )
    _append_skill_operation(
        skills_dir,
        operation="install_remote",
        skill_id=entry.skill_id,
        source=index_url,
        destination=str(destination),
        extra={
            "source_path": entry.path,
            "source_tree_url": tree_url,
            "source_tree_sha": tree_sha if isinstance(tree_sha, str) else None,
            "installed_files": installed_files_tuple,
        },
    )
    return RemoteSkillInstallResult(
        skill_id=entry.skill_id,
        destination=destination,
        installed_files=installed_files_tuple,
        source_index_url=index_url,
        source_tree_url=tree_url,
        source_tree_sha=tree_sha if isinstance(tree_sha, str) else None,
    )


def _find_available_entry(
    skill_ref: str,
    entries: tuple[RemoteSkillEntry, ...],
) -> RemoteSkillEntry:
    normalized_ref = skill_ref.strip()
    for entry in entries:
        if entry.skill_id == normalized_ref:
            if entry.status != "available":
                raise RemoteSkillError(
                    f"remote skill is not available: {entry.skill_id} status={entry.status}"
                )
            return entry
    raise RemoteSkillError(f"remote skill not found: {normalized_ref}")


def _source_directory_for_entry(entry: RemoteSkillEntry) -> str:
    path = PurePosixPath(entry.path)
    _reject_unsafe_posix_path(path, label="remote skill path")
    if path.name != "SKILL.md":
        raise RemoteSkillError(f"remote skill path must point to SKILL.md: {entry.path}")
    return path.parent.as_posix()


def _remote_file_paths_for_source_dir(
    tree_payload: Mapping[str, Any],
    *,
    source_dir: str,
) -> tuple[str, ...]:
    tree = tree_payload.get("tree")
    if not isinstance(tree, list):
        raise RemoteSkillError("remote skill tree payload is missing tree")

    prefix = f"{source_dir}/"
    paths: list[str] = []
    for item in tree:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") != "blob":
            continue
        path = item.get("path")
        if not isinstance(path, str) or not path.startswith(prefix):
            continue
        paths.append(path)
    return tuple(sorted(paths))


def _relative_remote_package_path(remote_path: str, *, source_dir: str) -> PurePosixPath:
    path = PurePosixPath(remote_path)
    _reject_unsafe_posix_path(path, label="remote file path")
    try:
        relative = path.relative_to(PurePosixPath(source_dir))
    except ValueError as exc:
        raise RemoteSkillError(f"remote file is outside skill package: {remote_path}") from exc
    _reject_unsafe_posix_path(relative, label="remote relative path")
    return relative


def _reject_unsafe_posix_path(path: PurePosixPath, *, label: str) -> None:
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RemoteSkillError(f"unsafe {label}: {path}")


def _reject_unsafe_skill_id(skill_id: str) -> None:
    if not skill_id or "/" in skill_id or "\\" in skill_id or skill_id in {".", ".."}:
        raise RemoteSkillError(f"unsafe remote skill id: {skill_id}")


def _fetch_remote_file_bytes(
    url: str,
    *,
    timeout_seconds: float,
    fetch_text: Callable[..., str],
    fetch_bytes: Callable[..., bytes] | None,
) -> bytes:
    if fetch_bytes is not None:
        return fetch_bytes(url, timeout_seconds=timeout_seconds)
    return fetch_text(url, timeout_seconds=timeout_seconds).encode("utf-8")


def _write_remote_source_metadata(
    destination: Path,
    *,
    entry: RemoteSkillEntry,
    source_index_url: str,
    source_tree_url: str,
    source_tree_sha: str | None,
    installed_files: tuple[str, ...],
    file_hashes: Mapping[str, str],
) -> None:
    payload = {
        "schema_version": "1.0",
        "kind": "remote_skill_source",
        "skill_id": entry.skill_id,
        "source_index_url": source_index_url,
        "source_tree_url": source_tree_url,
        "source_tree_sha": source_tree_sha,
        "source_path": entry.path,
        "installed_files": list(installed_files),
        "file_sha256": dict(sorted(file_hashes.items())),
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
    destination.joinpath("remote_source.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _split_markdown_table_row(line: str) -> list[str]:
    stripped = line.strip().strip("|")
    return [column.strip() for column in stripped.split("|")]


def _strip_markdown_code(value: str) -> str:
    return value.replace("`", "")


def _sync_skills_index(skills_dir: Path, *, skill_id: str, skill_path: str) -> None:
    index_path = skills_dir / "skills_index.md"
    existing = index_path.read_text(encoding="utf-8") if index_path.exists() else "# Skills Index\n"
    entry = f"- {skill_id}: {skill_path}"
    if entry in existing:
        return
    index_path.write_text(existing.rstrip() + "\n" + entry + "\n", encoding="utf-8")


def _append_skill_operation(
    skills_dir: Path,
    *,
    operation: str,
    skill_id: str,
    source: str,
    destination: str,
    extra: Mapping[str, Any] | None = None,
) -> None:
    log_path = skills_dir / "skill_operations.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        "operation": operation,
        "skill_id": skill_id,
        "source": source,
        "destination": destination,
    }
    if extra:
        payload.update(extra)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _fetch_url_text(url: str, *, timeout_seconds: float) -> str:
    return _fetch_url_bytes(url, timeout_seconds=timeout_seconds).decode("utf-8")


def _fetch_url_json(url: str, *, timeout_seconds: float) -> Mapping[str, Any]:
    try:
        payload = json.loads(_fetch_url_text(url, timeout_seconds=timeout_seconds))
    except json.JSONDecodeError as exc:
        raise RemoteSkillError(f"remote JSON response is malformed: {url}") from exc
    if not isinstance(payload, dict):
        raise RemoteSkillError(f"remote JSON response must be an object: {url}")
    return payload


def _fetch_url_bytes(url: str, *, timeout_seconds: float) -> bytes:
    request = Request(url, headers={"User-Agent": "millrace-remote-skills"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return bytes(response.read())
    except (OSError, URLError) as exc:
        raise RemoteSkillError(f"failed to fetch remote skill resource: {url}") from exc


__all__ = [
    "DEFAULT_REMOTE_SKILLS_INDEX_URL",
    "DEFAULT_REMOTE_SKILLS_RAW_ROOT_URL",
    "DEFAULT_REMOTE_SKILLS_TREE_URL",
    "REMOTE_SKILLS_INDEX_FILENAME",
    "RemoteSkillEntry",
    "RemoteSkillError",
    "RemoteSkillInstallResult",
    "install_remote_skill",
    "parse_remote_skill_index",
    "refresh_remote_skill_index",
]
