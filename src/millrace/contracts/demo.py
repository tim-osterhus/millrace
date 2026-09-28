"""Private demo records shared by its Core-owned coordinator and observations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from typing import Any

VERSION = "0.22.4"
COMMAND_VERSION = "Core.demo.command.v1"
ADAPTER_KIND = "millrace.demo.synthetic"
PACKAGE_ID = "millrace.plus.official"
SOURCE_DIGEST = (
    "sha256:b71a2333a73cc0699a5957de6017c479cec46707fff15987be93d987383a36b8"
)
MANIFEST_DIGEST = (
    "sha256:d02d5c37da53894fd5b8a72f4bd4e1c5c019b102a61b527b6ec32f232cd1d256"
)
PACKAGE_DIGEST = (
    "sha256:127edbd02dab839c3544bc770db50fc0f6eea8c8e408cbc96758858037ae3c09"
)
PLAN_FINGERPRINT = (
    "sha256:909510340be39e137740e5669e1255c45e2980b6a61e9845ed8aebd5326a1c1b"
)
DESCRIPTOR_SHA256 = "bf7a28946685360586d9a8ec8ad98d865711bb129334506279308b5718bc905a"


class DemoRefusal(ValueError):
    pass


def serial(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: serial(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {str(k): serial(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [serial(v) for v in value]
    return value
