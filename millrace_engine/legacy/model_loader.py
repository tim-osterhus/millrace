"""Legacy model-config markdown parser."""

from __future__ import annotations

from pathlib import Path


def load_model_values(path: Path) -> dict[str, str]:
    """Parse the active `KEY=value` block from a legacy model config markdown file."""

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped == "---":
            break
        if stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values
