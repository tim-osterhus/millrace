"""Narrow engine-launch helpers shared by control surfaces."""

from __future__ import annotations

from pathlib import Path

from .control_common import ControlError
from .control_models import RuntimeState


def start_engine(
    config_path: Path | str,
    *,
    daemon: bool = False,
    once: bool = False,
) -> RuntimeState:
    """Construct and start the runtime engine with control-surface validation."""

    if daemon and once:
        raise ControlError("start may use only one of daemon or once")

    from .engine import MillraceEngine

    engine = MillraceEngine(config_path)
    return engine.start(daemon=daemon, once=once)
