"""Pure recurring seam envelope and selection-retention rules."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

from .records import (
    Action,
    RouterError,
    fields,
    identifier,
    number,
    relative,
    strict_json,
    strings,
    text,
)

if TYPE_CHECKING:
    from .execution import InvocationRequest, SeamInput


def acknowledges(selected: Action | None, consumer_node: str) -> bool:
    """Only a committed leaving-item transition acknowledges selected input."""
    return (
        selected is not None
        and selected.kind != "retry"
        and not (selected.kind == "pause" and selected.target == consumer_node)
    )


def _envelope(value: object) -> dict[str, object]:
    item = fields(
        value,
        {
            "format",
            "run_id",
            "consumer_plane_id",
            "consumer_node_id",
            "seam_id",
            "sequence",
            "producer_plane_id",
            "producer_node_id",
            "producer_invocation_id",
            "producing_revision",
            "completion_id",
            "producer_workspace",
            "result",
        },
    )
    if item["format"] != "millrace-seam-input/v1":
        raise RouterError("invalid_seam_input")
    for key in ("run_id", "producer_invocation_id", "completion_id"):
        raw = text(item[key])
        if len(raw) != 32 or any(c not in "0123456789abcdef" for c in raw):
            raise RouterError("invalid_seam_input")
    revision = text(item["producing_revision"])
    if len(revision) != 64 or any(c not in "0123456789abcdef" for c in revision):
        raise RouterError("invalid_seam_input")
    for key in (
        "consumer_plane_id",
        "consumer_node_id",
        "seam_id",
        "producer_plane_id",
        "producer_node_id",
    ):
        identifier(item[key])
    number(item["sequence"], integer=True)
    workspace = Path(text(item["producer_workspace"]))
    if (
        not workspace.is_absolute()
        or ".." in workspace.parts
        or str(workspace) != item["producer_workspace"]
    ):
        raise RouterError("invalid_seam_input")
    if item["producer_plane_id"] == item["consumer_plane_id"]:
        raise RouterError("invalid_seam_input")
    result = fields(item["result"], {"outcome"}, {"summary", "refs"})
    identifier(result["outcome"])
    if "summary" in result and not isinstance(result["summary"], str):
        raise RouterError("invalid_seam_input")
    for name in strings(result.get("refs", [])):
        relative(name, owner_forbidden=True)
    return item


def build_input(path: Path, envelope: dict[str, object]) -> SeamInput:
    """Freeze ordinary retained result data; never materialize or read artifacts."""
    from .execution import SeamInput

    item = _envelope(envelope)
    if not path.is_absolute() or path.name != "seam-input.json":
        raise RouterError("invalid_seam_input")
    content = (
        json.dumps(
            item,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode()
    return SeamInput(
        path,
        content,
        hashlib.sha256(content).hexdigest(),
        str(item["seam_id"]),
        int(number(item["sequence"], integer=True)),
        str(item["producer_invocation_id"]),
    )


def validate_input(value: SeamInput, request: InvocationRequest) -> dict[str, object]:
    """Validate immutable DTO identity without ambient environment/filesystem reads."""
    if (
        type(value.content) is not bytes
        or hashlib.sha256(value.content).hexdigest() != value.sha256
        or value.path != request.invocation_root / "seam-input.json"
        or not value.path.is_absolute()
    ):
        raise RouterError("invalid_seam_input")
    item = _envelope(strict_json(value.content))
    if (
        item["consumer_plane_id"],
        item["consumer_node_id"],
        item["seam_id"],
        item["sequence"],
        item["producer_invocation_id"],
    ) != (
        request.plane_id,
        request.node_id,
        value.seam_id,
        value.sequence,
        value.producer_invocation_id,
    ):
        raise RouterError("invalid_seam_input")
    return item
