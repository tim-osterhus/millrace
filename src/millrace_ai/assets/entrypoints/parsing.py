"""Markdown asset manifest parsing."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

from .discovery import infer_entrypoint_path_target
from .models import ParsedMarkdownAsset


def parse_markdown_asset(path: Path) -> ParsedMarkdownAsset:
    """Parse markdown frontmatter into a compact manifest map."""

    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    manifest: Mapping[str, object]
    if lines and lines[0].strip() == "---":
        frontmatter_text, body = split_frontmatter(raw)
        manifest = parse_frontmatter_map(frontmatter_text, path=path)
    else:
        path_plane, path_stage = infer_entrypoint_path_target(path)
        if path_plane is None:
            raise ValueError("asset file is missing YAML frontmatter start marker")

        manifest = {"asset_type": "entrypoint", "plane": path_plane}
        if path_stage is not None:
            manifest["stage"] = path_stage
        body = raw.strip()

    return ParsedMarkdownAsset(path=path, manifest=manifest, body=body)


def split_frontmatter(raw: str) -> tuple[str, str]:
    lines = raw.splitlines()
    if not lines:
        raise ValueError("asset file is empty")
    if lines[0].strip() != "---":
        raise ValueError("asset file is missing YAML frontmatter start marker")

    end_index: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break

    if end_index is None:
        raise ValueError("asset file is missing YAML frontmatter end marker")

    frontmatter = "\n".join(lines[1:end_index])
    body = "\n".join(lines[end_index + 1 :]).strip()
    return frontmatter, body


def parse_frontmatter_map(frontmatter: str, *, path: Path) -> dict[str, object]:
    manifest: dict[str, object] = {}
    active_list_key: str | None = None

    for line_number, raw_line in enumerate(frontmatter.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if raw_line.startswith("  - ") or raw_line.startswith("- "):
            if active_list_key is None:
                raise ValueError(
                    f"frontmatter parse error in {path.name}:{line_number} (list item without key)"
                )
            item_raw = stripped[2:].strip()
            current = manifest.get(active_list_key)
            if not isinstance(current, list):
                raise ValueError(
                    f"frontmatter parse error in {path.name}:{line_number} (list key malformed)"
                )
            current.append(parse_scalar(item_raw))
            continue

        if ":" not in raw_line:
            raise ValueError(
                f"frontmatter parse error in {path.name}:{line_number} (missing `:` separator)"
            )

        key_raw, value_raw = raw_line.split(":", 1)
        key = key_raw.strip()
        value = value_raw.strip()

        if not key:
            raise ValueError(f"frontmatter parse error in {path.name}:{line_number} (empty key)")

        if value == "":
            manifest[key] = []
            active_list_key = key
        else:
            manifest[key] = parse_scalar(value)
            active_list_key = None

    return manifest


def parse_scalar(value: str) -> object:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False

    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [parse_scalar(item.strip()) for item in inner.split(",")]

    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]

    if re.fullmatch(r"-?\d+", value):
        return int(value)

    return value


def asset_type(asset: ParsedMarkdownAsset) -> str | None:
    return string_value(asset.manifest, "asset_type")


def asset_id(asset: ParsedMarkdownAsset) -> str:
    value = string_value(asset.manifest, "asset_id")
    return value if value is not None else asset.path.stem


def string_value(manifest: Mapping[str, object], key: str) -> str | None:
    value = manifest.get(key)
    return value if isinstance(value, str) else None


def bool_value(manifest: Mapping[str, object], key: str) -> bool | None:
    value = manifest.get(key)
    return value if isinstance(value, bool) else None


def string_list_value(manifest: Mapping[str, object], key: str) -> list[str] | None:
    raw_value = manifest.get(key)
    if raw_value is None:
        return None

    if not isinstance(raw_value, list):
        return None

    values: list[str] = []
    for item in raw_value:
        if not isinstance(item, str):
            return None
        values.append(item)
    return values


__all__ = [
    "asset_id",
    "asset_type",
    "bool_value",
    "parse_frontmatter_map",
    "parse_markdown_asset",
    "parse_scalar",
    "split_frontmatter",
    "string_list_value",
    "string_value",
]
