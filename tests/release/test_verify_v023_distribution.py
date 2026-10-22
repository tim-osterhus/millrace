"""Exercise the release verifier against real built artifacts and digest drift."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_built_distribution_and_selected_digest_refusal(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "release_verifier", ROOT / "scripts/release/verify_v023_distribution.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    subprocess.run(
        ["uv", "build", "--offline", "--out-dir", str(tmp_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    wheel, sdist = next(tmp_path.glob("*.whl")), next(tmp_path.glob("*.tar.gz"))
    digests = module.verify(wheel, sdist)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(digests))
    assert module.verify(wheel, sdist, manifest) == digests
    manifest.write_text(json.dumps({**digests, wheel.name: "0" * 64}))
    with pytest.raises(AssertionError):
        module.verify(wheel, sdist, manifest)
