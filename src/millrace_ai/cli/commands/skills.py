"""Skills operator command group."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer

from millrace_ai.cli.errors import _print_error
from millrace_ai.cli.shared import WorkspaceOption, _require_paths
from millrace_ai.config import load_runtime_config
from millrace_ai.contracts import LearningRequestAction, LearningRequestDocument
from millrace_ai.modes import ModeAssetError, load_builtin_mode_definition, resolve_builtin_mode_id
from millrace_ai.queue_store import QueueStore

skills_app = typer.Typer(add_completion=False, no_args_is_help=True)


@skills_app.command("install")
def skills_install(
    skill_ref: Annotated[str, typer.Argument(help="Local skill directory, SKILL.md file, or remote skill id.")],
    workspace: WorkspaceOption = Path("."),
    target: Annotated[str, typer.Option(help="Write target: workspace or source.")] = "workspace",
    force: Annotated[bool, typer.Option("--force", help="Replace an existing local skill.")] = False,
    update: Annotated[bool, typer.Option("--update", help="Update an existing local skill.")] = False,
) -> None:
    paths = _require_paths(workspace)
    source = _resolve_local_skill_source(skill_ref)
    if source is None:
        raise typer.Exit(code=_print_error(f"cannot resolve skill ref: {skill_ref}"))

    skill_id = _skill_id_for_source(source)
    destination_root = _target_skills_dir(paths.root, target=target)
    destination = destination_root / skill_id
    if destination.exists() and not (force or update):
        raise typer.Exit(code=_print_error(f"skill already exists: {skill_id}"))

    if destination.exists():
        shutil.rmtree(destination)
    _copy_skill_source(source, destination)
    _sync_skills_index(destination_root, skill_id=skill_id, skill_path=f"{skill_id}/SKILL.md")
    _append_skill_operation(
        destination_root,
        operation="install",
        skill_id=skill_id,
        source=str(source),
        destination=str(destination),
    )
    typer.echo(f"installed_skill: {skill_id}")
    typer.echo(f"path: {destination}")


@skills_app.command("create")
def skills_create(
    prompt: Annotated[str, typer.Argument(help="Skill creation prompt.")],
    workspace: WorkspaceOption = Path("."),
    mode: Annotated[str | None, typer.Option(help="Mode to evaluate for learning support.")] = None,
    foreground: Annotated[bool, typer.Option("--foreground", help="Run immediately instead of queueing.")] = False,
) -> None:
    del foreground
    paths = _require_paths(workspace)
    _require_learning_mode(paths.root, mode_id=mode)
    document = _learning_request_document(
        requested_action=LearningRequestAction.CREATE,
        title="Create skill",
        summary=prompt,
        target_skill_id=None,
    )
    destination = QueueStore(paths).enqueue_learning_request(document)
    typer.echo(f"queued_learning_request: {destination}")


@skills_app.command("improve")
def skills_improve(
    skill_id: Annotated[str, typer.Argument(help="Installed skill id to improve.")],
    workspace: WorkspaceOption = Path("."),
    mode: Annotated[str | None, typer.Option(help="Mode to evaluate for learning support.")] = None,
    foreground: Annotated[bool, typer.Option("--foreground", help="Run immediately instead of queueing.")] = False,
) -> None:
    del foreground
    paths = _require_paths(workspace)
    _require_learning_mode(paths.root, mode_id=mode)
    document = _learning_request_document(
        requested_action=LearningRequestAction.IMPROVE,
        title=f"Improve {skill_id}",
        summary=f"Improve installed skill {skill_id}.",
        target_skill_id=skill_id,
    )
    destination = QueueStore(paths).enqueue_learning_request(document)
    typer.echo(f"queued_learning_request: {destination}")


@skills_app.command("promote")
def skills_promote(
    skill_id: Annotated[str, typer.Argument(help="Workspace skill id to promote into source assets.")],
    workspace: WorkspaceOption = Path("."),
) -> None:
    paths = _require_paths(workspace)
    source = paths.skills_dir / skill_id
    if not source.joinpath("SKILL.md").is_file():
        raise typer.Exit(code=_print_error(f"workspace skill not found: {skill_id}"))
    destination_root = _target_skills_dir(paths.root, target="source")
    destination = destination_root / skill_id
    if destination.exists():
        raise typer.Exit(code=_print_error(f"source skill already exists: {skill_id}"))
    shutil.copytree(source, destination)
    _sync_skills_index(destination_root, skill_id=skill_id, skill_path=f"{skill_id}/SKILL.md")
    _append_skill_operation(
        destination_root,
        operation="promote",
        skill_id=skill_id,
        source=str(source),
        destination=str(destination),
    )
    typer.echo(f"promoted_skill: {skill_id}")
    typer.echo(f"path: {destination}")


@skills_app.command("export")
def skills_export(
    skill_id: Annotated[str, typer.Argument(help="Installed skill id to export.")],
    workspace: WorkspaceOption = Path("."),
    output: Annotated[Path | None, typer.Option("--output", help="Destination archive path.")] = None,
) -> None:
    paths = _require_paths(workspace)
    source = paths.skills_dir / skill_id
    if not source.joinpath("SKILL.md").is_file():
        raise typer.Exit(code=_print_error(f"skill not found: {skill_id}"))
    archive_base = output.with_suffix("") if output is not None else paths.root / f"{skill_id}"
    archive = shutil.make_archive(str(archive_base), "zip", root_dir=source)
    _append_skill_operation(
        paths.skills_dir,
        operation="export",
        skill_id=skill_id,
        source=str(source),
        destination=archive,
    )
    typer.echo(f"exported_skill: {archive}")


@skills_app.command("ls")
def skills_ls(workspace: WorkspaceOption = Path(".")) -> None:
    paths = _require_paths(workspace)
    for skill_id in _list_skill_ids(paths.skills_dir):
        typer.echo(skill_id)


@skills_app.command("show")
def skills_show(
    skill_id: Annotated[str, typer.Argument(help="Installed skill id to inspect.")],
    workspace: WorkspaceOption = Path("."),
) -> None:
    paths = _require_paths(workspace)
    skill_path = paths.skills_dir / skill_id / "SKILL.md"
    if not skill_path.is_file():
        raise typer.Exit(code=_print_error(f"skill not found: {skill_id}"))
    typer.echo(f"skill_id: {skill_id}")
    typer.echo(f"path: {skill_path}")
    first_heading = _first_markdown_heading(skill_path)
    if first_heading is not None:
        typer.echo(f"title: {first_heading}")


@skills_app.command("search")
def skills_search(
    query: Annotated[str, typer.Argument(help="Search query.")],
    workspace: WorkspaceOption = Path("."),
) -> None:
    paths = _require_paths(workspace)
    needle = query.strip().lower()
    if not needle:
        raise typer.Exit(code=_print_error("search query is required"))
    for skill_id in _list_skill_ids(paths.skills_dir):
        skill_path = paths.skills_dir / skill_id / "SKILL.md"
        text = skill_path.read_text(encoding="utf-8", errors="replace").lower()
        if needle in skill_id.lower() or needle in text:
            typer.echo(skill_id)


def _resolve_local_skill_source(skill_ref: str) -> Path | None:
    candidate = Path(skill_ref).expanduser()
    if not candidate.exists():
        return None
    if candidate.is_dir() and candidate.joinpath("SKILL.md").is_file():
        return candidate.resolve()
    if candidate.is_file() and candidate.name == "SKILL.md":
        return candidate.parent.resolve()
    return None


def _skill_id_for_source(source: Path) -> str:
    return source.name


def _copy_skill_source(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination)


def _target_skills_dir(workspace_root: Path, *, target: str) -> Path:
    normalized = target.strip().lower()
    if normalized == "workspace":
        return workspace_root / "millrace-agents" / "skills"
    if normalized == "source":
        source_root = _find_source_root(workspace_root)
        if source_root is None:
            raise typer.Exit(code=_print_error("cannot locate source skill asset directory"))
        return source_root / "src" / "millrace_ai" / "assets" / "skills"
    raise typer.Exit(code=_print_error("target must be workspace or source"))


def _find_source_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if candidate.joinpath("src", "millrace_ai", "assets", "skills").is_dir():
            return candidate
    source_root = Path(__file__).resolve().parents[4]
    if source_root.joinpath("src", "millrace_ai", "assets", "skills").is_dir():
        return source_root
    return None


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
) -> None:
    log_path = skills_dir / "skill_operations.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "operation": operation,
            "skill_id": skill_id,
            "source": source,
            "destination": destination,
        },
        sort_keys=True,
    )
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(payload + "\n")


def _list_skill_ids(skills_dir: Path) -> tuple[str, ...]:
    if not skills_dir.is_dir():
        return ()
    return tuple(
        sorted(
            path.name
            for path in skills_dir.iterdir()
            if path.is_dir() and path.joinpath("SKILL.md").is_file()
        )
    )


def _first_markdown_heading(path: Path) -> str | None:
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return None


def _require_learning_mode(workspace_root: Path, *, mode_id: str | None) -> None:
    config = load_runtime_config(workspace_root / "millrace-agents" / "millrace.toml")
    selected_mode_id = resolve_builtin_mode_id(mode_id or config.runtime.default_mode)
    try:
        mode = load_builtin_mode_definition(selected_mode_id)
    except ModeAssetError as exc:
        raise typer.Exit(code=_print_error(str(exc))) from exc
    if not mode.learning_enabled:
        raise typer.Exit(
            code=_print_error("current mode does not enable the learning plane")
        )


def _learning_request_document(
    *,
    requested_action: LearningRequestAction,
    title: str,
    summary: str,
    target_skill_id: str | None,
) -> LearningRequestDocument:
    now = datetime.now(timezone.utc)
    return LearningRequestDocument(
        learning_request_id=f"learn-{uuid4().hex[:12]}",
        title=title,
        summary=summary,
        requested_action=requested_action,
        target_skill_id=target_skill_id,
        created_at=now,
        created_by="millrace skills",
    )


__all__ = ["skills_app"]
