from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile


MILLRACE_ROOT = Path(__file__).resolve().parents[2]

if str(MILLRACE_ROOT) not in sys.path:
    sys.path.insert(0, str(MILLRACE_ROOT))

from millrace_engine.tui.app import MillraceTUIApplication
from tests.support import load_workspace_fixture
from tests.tui_support import SNAPSHOT_WORKER_SETTINGS


_temp_root = Path(tempfile.gettempdir()) / "millrace-tui-health-gate-snapshot"
shutil.rmtree(_temp_root, ignore_errors=True)
_temp_root.mkdir(parents=True, exist_ok=True)
_workspace, _config_path = load_workspace_fixture(_temp_root, "control_mailbox")
app = MillraceTUIApplication.from_config_path(_config_path, worker_settings=SNAPSHOT_WORKER_SETTINGS)


if __name__ == "__main__":
    app.run()
