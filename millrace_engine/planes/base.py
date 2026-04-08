"""Shared plane helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from ..config import EngineConfig
from ..paths import RuntimePaths

StageCommandSpec = Sequence[str] | None
StageCommandMap = Mapping[object, StageCommandSpec]


class PlaneRuntime:
    """Shared runtime dependencies for a control plane."""

    def __init__(self, config: EngineConfig, paths: RuntimePaths) -> None:
        self.config = config
        self.paths = paths

    @property
    def working_dir(self) -> Path:
        return self.paths.root
