from __future__ import annotations

import importlib.util


def test_base_millrace_ai_does_not_expose_web_package() -> None:
    assert importlib.util.find_spec("millrace_ai.web") is None

