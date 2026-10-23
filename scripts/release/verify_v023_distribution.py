"""Verify the standalone 0.23 Core distribution and optional release digests."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path

VERSION = "0.23.1"
RETIRED = {
    "adapters",
    "compiler",
    "contracts",
    "extensions",
    "kernel",
    "operator",
    "substrate",
    "testing",
    "workflows",
}


def verify(wheel: Path, sdist: Path, manifest: Path | None = None) -> dict[str, str]:
    """Reject unexpected payloads, dependency metadata and selected digest drift."""
    assert wheel.name == f"millrace_ai-{VERSION}-py3-none-any.whl"
    assert sdist.name == f"millrace_ai-{VERSION}.tar.gz"
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata = Parser().parsestr(
            archive.read(
                next(n for n in names if n.endswith(".dist-info/METADATA"))
            ).decode()
        )
        entry = archive.read(
            next(n for n in names if n.endswith(".dist-info/entry_points.txt"))
        ).decode()
    assert metadata["Name"] == "millrace-ai" and metadata["Version"] == VERSION
    assert metadata.get_all("Requires-Dist", []) == []
    assert "millrace = millrace.router.cli:cli" in entry
    assert {
        "millrace/__init__.py",
        "millrace/py.typed",
        "millrace/router/control.py",
        "millrace/router/adapters/command.py",
    } <= names
    for name in names:
        assert not name.startswith(("millrace_plus/", "millrace_workflow_package/"))
        if name.startswith("millrace/") and len(name.split("/")) > 2:
            assert name.split("/")[1] not in RETIRED
        assert "__pycache__" not in name and not name.endswith((".pyc", ".DS_Store"))
    with tarfile.open(sdist) as archive:
        members = archive.getnames()
    assert any(n.endswith("/docs/getting-started.md") for n in members)
    for name in members:
        assert "/.github/" not in name
        assert "__pycache__" not in name and not name.endswith((".pyc", ".DS_Store"))
        assert not any(f"/src/millrace/{part}/" in name for part in RETIRED)
    digests = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (wheel, sdist)
    }
    if manifest is not None:
        assert json.loads(manifest.read_text()) == digests
    return digests


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("sdist", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.wheel, args.sdist, args.manifest), sort_keys=True))


if __name__ == "__main__":
    main()
