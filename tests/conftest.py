from __future__ import annotations

import sys

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_configure() -> None:
    sys.dont_write_bytecode = True
