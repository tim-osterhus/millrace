"""Runtime asset source resolution and workspace deployment."""

from __future__ import annotations

from pathlib import Path

from .paths import WorkspacePaths

RUNTIME_ASSET_DIRS: tuple[str, ...] = (
    "entrypoints",
    "skills",
    "modes",
    "loops",
    "graphs",
    "registry",
)


def deploy_runtime_assets(paths: WorkspacePaths, *, assets_root: Path | str | None) -> None:
    """Copy missing packaged runtime assets into an initialized workspace."""

    source_root = resolve_asset_source_root(assets_root)

    for directory_name in RUNTIME_ASSET_DIRS:
        source_dir = source_root / directory_name
        if not source_dir.exists():
            continue

        destination_dir = paths.runtime_root / directory_name
        for source_file in source_dir.rglob("*"):
            if source_file.is_dir():
                continue

            if any(part.startswith(".") for part in source_file.relative_to(source_dir).parts):
                continue

            relative_path = source_file.relative_to(source_dir)
            destination = destination_dir / relative_path
            if destination.exists():
                continue

            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source_file.read_bytes())


def resolve_asset_source_root(assets_root: Path | str | None) -> Path:
    """Resolve the runtime asset source root for bootstrap and baseline code."""

    if assets_root is not None:
        return Path(assets_root).expanduser().resolve()

    from millrace_ai.modes import ASSETS_ROOT

    return ASSETS_ROOT


__all__ = ["RUNTIME_ASSET_DIRS", "deploy_runtime_assets", "resolve_asset_source_root"]
