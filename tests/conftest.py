"""Test configuration for the Millrace runtime suite."""

from __future__ import annotations

import sys
from pathlib import Path

pytest_plugins = ("syrupy", "pytest_textual_snapshot")


MILLRACE_ROOT = Path(__file__).resolve().parents[1]

if str(MILLRACE_ROOT) not in sys.path:
    sys.path.insert(0, str(MILLRACE_ROOT))
