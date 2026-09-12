from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSION = "0.22.3"
WHEEL_SHA256 = (
    "5e87f92f2330af30395f35860d319db7e5574367da7995a3613276d0a08f163d"
)
SDIST_SHA256 = (
    "4f500e6e474fa087dbc0f8a5d8f8f137370a71d9a04d4663f67eb6af37acf6eb"
)
EXPECTED_ARTIFACTS = {
    "millrace_ai-0.22.3-py3-none-any.whl": WHEEL_SHA256,
    "millrace_ai-0.22.3.tar.gz": SDIST_SHA256,
}


def _build(output_dir: Path) -> Path:
    output_dir.mkdir()
    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = "1580601600"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for artifact_kind in ("--wheel", "--sdist"):
        subprocess.run(
            [
                "uv",
                "build",
                artifact_kind,
                "--offline",
                "--no-create-gitignore",
                "--force-pep517",
                "--out-dir",
                str(output_dir),
            ],
            cwd=ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
    return output_dir


def _artifact_digests(directory: Path) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.iterdir())
        if path.is_file()
    }


def test_runtime_release_identity_is_v023_and_publishes_only_ai() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["name"] == "millrace-ai"
    assert project["project"]["version"] == VERSION

    workflow = (ROOT / ".github/workflows/publish-to-pypi.yml").read_text(
        encoding="utf-8"
    )
    assert set(re.findall(r"\bv\d+\.\d+\.\d+\b", workflow)) == {
        f"v{VERSION}"
    }
    assert "millrace-web" not in workflow
    assert set(
        re.findall(
            r"millrace_ai-\d+\.\d+\.\d+(?:-py3-none-any\.whl|\.tar\.gz)",
            workflow,
        )
    ) == set(EXPECTED_ARTIFACTS)


def test_clean_v023_build_has_exact_reproducible_artifacts(tmp_path: Path) -> None:
    first = _build(tmp_path / "first")
    second = _build(tmp_path / "second")

    assert _artifact_digests(first) == EXPECTED_ARTIFACTS
    assert _artifact_digests(second) == EXPECTED_ARTIFACTS
    for filename in EXPECTED_ARTIFACTS:
        assert (first / filename).read_bytes() == (second / filename).read_bytes()

    with zipfile.ZipFile(first / "millrace_ai-0.22.3-py3-none-any.whl") as archive:
        metadata_name = f"millrace_ai-{VERSION}.dist-info/METADATA"
        metadata = archive.read(metadata_name).decode("utf-8")
    assert "Name: millrace-ai\n" in metadata
    assert f"Version: {VERSION}\n" in metadata


def test_publish_workflow_uses_the_built_v023_hashes() -> None:
    workflow = (ROOT / ".github/workflows/publish-to-pypi.yml").read_text(
        encoding="utf-8"
    )
    entries = re.findall(
        r"(?m)^\s+([0-9a-f]{64})  dist/(millrace_ai-\S+)$",
        workflow,
    )
    assert {filename for _digest, filename in entries} == set(EXPECTED_ARTIFACTS)
    for filename, digest in EXPECTED_ARTIFACTS.items():
        assert [
            candidate_digest
            for candidate_digest, candidate_filename in entries
            if candidate_filename == filename
        ] == [digest, digest]
