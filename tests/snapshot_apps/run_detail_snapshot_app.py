from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

from textual.app import App


MILLRACE_ROOT = Path(__file__).resolve().parents[2]

if str(MILLRACE_ROOT) not in sys.path:
    sys.path.insert(0, str(MILLRACE_ROOT))

import millrace_engine.tui.screens.run_detail_modal as run_detail_modal_module

from millrace_engine.tui.messages import RefreshPayload
from millrace_engine.tui.models import GatewayResult
from millrace_engine.tui.screens.run_detail_modal import RunDetailModal
from tests.tui_support import sample_run_detail


class _FakeGateway:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path

    def load_run_detail(self, run_id: str) -> GatewayResult[RefreshPayload]:
        return GatewayResult(
            value=RefreshPayload(
                refreshed_at=datetime(2026, 3, 25, tzinfo=timezone.utc),
                run_detail=sample_run_detail(run_id=run_id),
            )
        )


class RunDetailSnapshotApp(App[None]):
    CSS_PATH = [
        str(MILLRACE_ROOT / "millrace_engine" / "tui" / "styles" / "app.tcss"),
        str(MILLRACE_ROOT / "millrace_engine" / "tui" / "styles" / "shell.tcss"),
        str(MILLRACE_ROOT / "millrace_engine" / "tui" / "styles" / "panels.tcss"),
    ]

    def on_mount(self) -> None:
        self.push_screen(RunDetailModal(config_path=Path("/tmp/millrace.toml"), run_id="smoke-standard"))


run_detail_modal_module.RuntimeGateway = _FakeGateway
app = RunDetailSnapshotApp()


if __name__ == "__main__":
    app.run()
