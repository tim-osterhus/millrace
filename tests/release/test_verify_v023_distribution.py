from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSION = "0.22.4"
RELEASE_VERSION = "0.22.4"
EXPECTED_ARTIFACTS = {
    f"millrace_ai-{VERSION}-py3-none-any.whl",
    f"millrace_ai-{VERSION}.tar.gz",
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


def test_runtime_identity_matches_release_workflow() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["name"] == "millrace-ai"
    assert project["project"]["version"] == VERSION

    workflow = (ROOT / ".github/workflows/publish-to-pypi.yml").read_text(
        encoding="utf-8"
    )
    assert set(re.findall(r"\bv\d+\.\d+\.\d+\b", workflow)) == {
        f"v{RELEASE_VERSION}"
    }
    assert VERSION in workflow
    assert "millrace-web" not in workflow
    assert set(
        re.findall(
            r"millrace_ai-\d+\.\d+\.\d+(?:-py3-none-any\.whl|\.tar\.gz)",
            workflow,
        )
    ) == {
        f"millrace_ai-{RELEASE_VERSION}-py3-none-any.whl",
        f"millrace_ai-{RELEASE_VERSION}.tar.gz",
    }


def test_clean_v024_build_has_exact_reproducible_artifacts(tmp_path: Path) -> None:
    first = _build(tmp_path / "first")
    second = _build(tmp_path / "second")

    assert set(_artifact_digests(first)) == EXPECTED_ARTIFACTS
    assert set(_artifact_digests(second)) == EXPECTED_ARTIFACTS
    assert _artifact_digests(first) == _artifact_digests(second)
    for filename in EXPECTED_ARTIFACTS:
        assert (first / filename).read_bytes() == (second / filename).read_bytes()

    with zipfile.ZipFile(first / f"millrace_ai-{VERSION}-py3-none-any.whl") as archive:
        metadata_name = f"millrace_ai-{VERSION}.dist-info/METADATA"
        metadata = archive.read(metadata_name).decode("utf-8")
    assert "Name: millrace-ai\n" in metadata
    assert f"Version: {VERSION}\n" in metadata


def test_publish_workflow_uses_the_reviewed_v024_hashes() -> None:
    workflow = (ROOT / ".github/workflows/publish-to-pypi.yml").read_text(
        encoding="utf-8"
    )
    entries = re.findall(
        r"(?m)^\s+([0-9a-f]{64})  dist/(millrace_ai-\S+)$",
        workflow,
    )
    expected_published = {
        f"millrace_ai-{RELEASE_VERSION}-py3-none-any.whl":
        "828ca3b41f4f5dcc11c495001443a86b1e4438ee7256e154f0edf435305cb9fd",
        f"millrace_ai-{RELEASE_VERSION}.tar.gz":
        "828007e31bdd978b9898aa5830f90ebc0da0746dd1c0ab44ac18591ea0b8eb23",
    }
    assert {filename for _digest, filename in entries} == set(expected_published)
    for filename, digest in expected_published.items():
        assert [
            candidate_digest
            for candidate_digest, candidate_filename in entries
            if candidate_filename == filename
        ] == [digest, digest]
