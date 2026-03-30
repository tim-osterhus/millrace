from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys
import tempfile


MILLRACE_ROOT = Path(__file__).resolve().parents[2]

if str(MILLRACE_ROOT) not in sys.path:
    sys.path.insert(0, str(MILLRACE_ROOT))

import millrace_engine.tui.gateway as gateway_module
import millrace_engine.tui.screens.shell as shell_module

from millrace_engine.tui.app import MillraceTUIApplication
from tests.tui_support import SNAPSHOT_WORKER_SETTINGS, load_operator_workspace


shell_module.stream_event_updates = lambda *args, **kwargs: None
gateway_module._utcnow = lambda: datetime(2026, 3, 25, tzinfo=timezone.utc)
_snapshot_root = Path(tempfile.gettempdir()) / "millrace-tui-shell-snapshot"
shutil.rmtree(_snapshot_root, ignore_errors=True)
_snapshot_root.mkdir(parents=True, exist_ok=True)
_workspace, _config_path = load_operator_workspace(_snapshot_root)
app = MillraceTUIApplication.from_config_path(_config_path, worker_settings=SNAPSHOT_WORKER_SETTINGS)


if __name__ == "__main__":
    app.run()
