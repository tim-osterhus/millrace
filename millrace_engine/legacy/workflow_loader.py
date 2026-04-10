"""Legacy workflow-config markdown parser."""

from __future__ import annotations

from pathlib import Path


def load_workflow_values(path: Path) -> dict[str, str]:
    """Parse `## KEY=value` lines from a legacy workflow config markdown file."""

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped.startswith("## "):
            continue
        body = stripped[3:]
        if "=" not in body:
            continue
        key, value = body.split("=", 1)
        values[key.strip()] = value.strip()
    return values
