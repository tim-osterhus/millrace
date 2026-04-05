"""Shared JSON persistence and checksum helpers for research modules."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, TypeVar
import json

from ..contracts import ContractModel
from ..markdown import write_text_atomic


_ContractModelT = TypeVar("_ContractModelT", bound=ContractModel)


def _sha256_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.as_posix()} must contain a JSON object")
    return payload


def _load_json_model(path: Path, model_cls: type[_ContractModelT]) -> _ContractModelT:
    return model_cls.model_validate(_load_json_object(path))


def _write_json_model(
    path: Path,
    model: ContractModel,
    *,
    create_parent: bool = False,
    by_alias: bool = False,
) -> None:
    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(model.model_dump_json(exclude_none=False, by_alias=by_alias))
    write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
