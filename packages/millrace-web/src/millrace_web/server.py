"""Uvicorn server entrypoint."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import uvicorn

from millrace_web.app import create_app


def serve(
    *,
    workspaces: Sequence[Path],
    host: str = "127.0.0.1",
    port: int = 8765,
    view: str = "detail",
    poll_interval_seconds: float = 1.0,
) -> None:
    if host == "0.0.0.0":
        print("Warning: millrace-web is a read-only local dashboard and does not provide authentication.")
    app = create_app(
        workspaces=workspaces,
        poll_interval_seconds=poll_interval_seconds,
        default_view=view,
    )
    uvicorn.run(app, host=host, port=port)
