from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from email.policy import default
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DIST_INFO = "millrace_ai-0.22.0.dist-info"
SDIST_ROOT = "millrace_ai-0.22.0"
DONOR_WORKFLOWS = {
    "lad_execution.py",
    "lad_learning.py",
    "lad_planning.py",
    "simple_loop.py",
    "vendor_selection.py",
}
PUBLIC_DOCS = {
    "FAQ.md",
    "README.md",
    "docs/getting-started.md",
    "docs/migrating-from-v0.21.md",
    "docs/v0.22-compatibility.md",
    "docs/errors.md",
    "docs/how-millrace-works.md",
    "docs/workflow-packages.md",
    "docs/millforge-runner.md",
    "docs/codex-runner.md",
}
FORBIDDEN_ARTIFACT_TEXT = (
    "millrace" + "-rewrite",
    "millrace" + "_rewrite",
    "0." + "0.0",
    "release " + "candidate",
    "release " + "preparation",
    "not part of this " + "rewrite",
    "later Millrace " + "packet",
    "rewrite " + "scaffold",
    "source runtime " + "checkout",
    "after the v0.22 distributions are " + "published",
    "transitional " + "source fixtures",
)


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _expected_runtime_members(*, prefix: str = "") -> set[str]:
    members: set[str] = set()
    for path in (PROJECT_ROOT / "src/millrace").rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(PROJECT_ROOT / "src").as_posix()
        if path.name == "README.md":
            continue
        if "testing" in path.relative_to(PROJECT_ROOT / "src/millrace").parts:
            continue
        if (
            path.parent == PROJECT_ROOT / "src/millrace/workflows"
            and path.name in DONOR_WORKFLOWS
        ):
            continue
        if path.suffix not in {".py", ".typed"}:
            continue
        members.add(f"{prefix}{relative}")
    return members


def _metadata_contract(raw: bytes) -> None:
    headers, body = raw.split(b"\n\n", 1)
    message = BytesParser(policy=default).parsebytes(headers + b"\n\n")
    assert message["Name"] == "millrace-ai"
    assert message["Version"] == "0.22.0"
    assert message["Requires-Python"] == ">=3.11"
    assert message["License-Expression"] == "Apache-2.0"
    assert message.get_all("License-File") == ["LICENSE"]
    assert message["Author-email"] == "Tim Osterhus <tim@millrace.ai>"
    assert message["Description-Content-Type"] == "text/markdown"
    assert set(message.get_all("Project-URL")) == {
        "Homepage, https://github.com/tim-osterhus/millrace",
        "Repository, https://github.com/tim-osterhus/millrace",
        "Issues, https://github.com/tim-osterhus/millrace/issues",
    }
    assert body.decode("utf-8") == (PROJECT_ROOT / "README.md").read_text(
        encoding="utf-8"
    )


def _assert_clean_artifact_text(name: str, raw: bytes) -> None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return
    for phrase in FORBIDDEN_ARTIFACT_TEXT:
        assert phrase not in text, name


def _assert_wheel_contract(wheel: Path) -> None:
    expected = _expected_runtime_members() | {
        f"{DIST_INFO}/METADATA",
        f"{DIST_INFO}/WHEEL",
        f"{DIST_INFO}/entry_points.txt",
        f"{DIST_INFO}/licenses/LICENSE",
        f"{DIST_INFO}/RECORD",
    }
    with zipfile.ZipFile(wheel) as archive:
        names = {
            member.filename for member in archive.infolist() if not member.is_dir()
        }
        assert names == expected
        for name in names:
            _assert_clean_artifact_text(name, archive.read(name))
        _metadata_contract(archive.read(f"{DIST_INFO}/METADATA"))
        assert archive.read(f"{DIST_INFO}/licenses/LICENSE") == (
            PROJECT_ROOT / "LICENSE"
        ).read_bytes()


def test_release_metadata_is_final_and_complete() -> None:
    project = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]

    assert project == {
        "name": "millrace-ai",
        "version": "0.22.0",
        "description": (
            "A governed runtime for compiler-validated, durable agent workflows."
        ),
        "readme": {"file": "README.md", "content-type": "text/markdown"},
        "requires-python": ">=3.11",
        "license": "Apache-2.0",
        "license-files": ["LICENSE"],
        "authors": [{"name": "Tim Osterhus", "email": "tim@millrace.ai"}],
        "dependencies": [],
        "scripts": {"millrace": "millrace.adapters.cli.main:cli"},
        "urls": {
            "Homepage": "https://github.com/tim-osterhus/millrace",
            "Repository": "https://github.com/tim-osterhus/millrace",
            "Issues": "https://github.com/tim-osterhus/millrace/issues",
        },
    }


def test_public_document_set_uses_final_release_paths() -> None:
    required = {
        "README.md",
        "docs/getting-started.md",
        "docs/migrating-from-v0.21.md",
        "docs/v0.22-compatibility.md",
        "docs/errors.md",
        "docs/how-millrace-works.md",
        "docs/workflow-packages.md",
        "docs/millforge-runner.md",
        "docs/codex-runner.md",
        "docs/maintainers/e2e-live-smoke.md",
        "docs/maintainers/error-contract-matrix.md",
    }
    assert all((PROJECT_ROOT / relative_path).is_file() for relative_path in required)
    assert not (PROJECT_ROOT / "docs/v0.22-breaking-changes.md").exists()
    assert not (PROJECT_ROOT / "docs/e2e-live-smoke.md").exists()


def test_public_docs_are_self_contained_and_links_are_release_safe() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    millforge_guide = (PROJECT_ROOT / "docs/millforge-runner.md").read_text(
        encoding="utf-8"
    )
    canonical_root = "https://github.com/tim-osterhus/millrace/blob/v0.22.0/"
    for relative_path in PUBLIC_DOCS - {"README.md"} | {"LICENSE"}:
        assert f"{canonical_root}{relative_path}" in readme
    assert "](docs/" not in readme
    assert "](LICENSE)" not in readme
    assert "source runtime checkout" not in millforge_guide
    assert "After the v0.22 distributions are published" not in millforge_guide
    assert "e2e-live-smoke" not in readme
    assert "e2e-live-smoke" not in millforge_guide

    codex_guide = (PROJECT_ROOT / "docs/codex-runner.md").read_text(
        encoding="utf-8"
    )
    required_contract_terms = {
        "codex_adapter_invocation_bundle",
        "schema_version == 2",
        "selected_runner_binding_id",
        "selected_adapter_kind",
        "request_timeout_seconds",
        "environment_policy_ref",
        "local_config_ref",
        "cancellation_token",
        "selected_asset_material",
        "entrypoint_asset_ref",
        "skill_asset_refs",
        "legal_terminal_markers",
        "selected_asset_refs",
        "outcome_kind",
        "structured_provider_response",
        "artifact_payload_candidate",
        "observation_payload_candidate",
        "evidence_construction_diagnostics",
    }
    assert all(term in codex_guide for term in required_contract_terms)
    assert "`src/" not in codex_guide
    assert "`tests/" not in codex_guide

    maintainer_links = (
        ("docs/maintainers/e2e-live-smoke.md", "../codex-runner.md"),
        ("docs/maintainers/e2e-live-smoke.md", "../millforge-runner.md"),
        ("docs/maintainers/e2e-live-smoke.md", "../errors.md"),
        ("docs/maintainers/error-contract-matrix.md", "../errors.md"),
    )
    for document, target in maintainer_links:
        resolved = (PROJECT_ROOT / document).parent / target
        assert resolved.resolve().is_file(), (document, target)


def test_tracked_release_surface_has_no_temporary_identity() -> None:
    scan_paths = [
        PROJECT_ROOT / "pyproject.toml",
        PROJECT_ROOT / "uv.lock",
        PROJECT_ROOT / "README.md",
        *sorted((PROJECT_ROOT / "src/millrace").rglob("*.py")),
        *sorted((PROJECT_ROOT / "src/millrace").rglob("README.md")),
        *[
            PROJECT_ROOT / relative_path
            for relative_path in (
                "docs/getting-started.md",
                "docs/migrating-from-v0.21.md",
                "docs/v0.22-compatibility.md",
                "docs/errors.md",
                "docs/how-millrace-works.md",
                "docs/workflow-packages.md",
                "docs/millforge-runner.md",
                "docs/codex-runner.md",
            )
        ],
    ]

    for path in scan_paths:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for phrase in FORBIDDEN_ARTIFACT_TEXT:
            assert phrase not in text, path


def test_artifact_text_guard_rejects_temporary_release_residue() -> None:
    with pytest.raises(AssertionError):
        _assert_clean_artifact_text(
            "millrace/example.py",
            ("Temporary " + "millrace-rewrite" + " module.").encode(),
        )


def test_fresh_artifacts_match_the_release_contract(tmp_path: Path) -> None:
    build_dir = tmp_path / "dist"
    _run(
        [
            "uv",
            "build",
            "--wheel",
            "--offline",
            "--no-create-gitignore",
            "--out-dir",
            str(build_dir),
        ],
        cwd=PROJECT_ROOT,
    )
    _run(
        [
            "uv",
            "build",
            "--sdist",
            "--offline",
            "--no-create-gitignore",
            "--out-dir",
            str(build_dir),
        ],
        cwd=PROJECT_ROOT,
    )
    wheel = build_dir / "millrace_ai-0.22.0-py3-none-any.whl"
    sdist = build_dir / "millrace_ai-0.22.0.tar.gz"
    assert wheel.is_file()
    assert sdist.is_file()
    _assert_wheel_contract(wheel)

    with tarfile.open(sdist, "r:gz") as archive:
        names = {member.name for member in archive.getmembers() if member.isfile()}
        expected = {
            f"{SDIST_ROOT}/{relative_path}" for relative_path in PUBLIC_DOCS
        } | {
            f"{SDIST_ROOT}/LICENSE",
            f"{SDIST_ROOT}/pyproject.toml",
            f"{SDIST_ROOT}/PKG-INFO",
        } | _expected_runtime_members(prefix=f"{SDIST_ROOT}/src/")
        assert names == expected
        for name in names:
            member = archive.extractfile(name)
            assert member is not None
            _assert_clean_artifact_text(name, member.read())
        _metadata_contract(archive.extractfile(f"{SDIST_ROOT}/PKG-INFO").read())
        assert archive.extractfile(f"{SDIST_ROOT}/LICENSE").read() == (
            PROJECT_ROOT / "LICENSE"
        ).read_bytes()

    rebuilt_dir = tmp_path / "rebuilt"
    _run(
        [
            "uv",
            "build",
            "--wheel",
            "--offline",
            "--no-create-gitignore",
            "--out-dir",
            str(rebuilt_dir),
            str(sdist),
        ],
        cwd=tmp_path,
    )
    rebuilt_wheel = rebuilt_dir / "millrace_ai-0.22.0-py3-none-any.whl"
    assert rebuilt_wheel.is_file()
    _assert_wheel_contract(rebuilt_wheel)

    for index, candidate in enumerate((wheel, rebuilt_wheel), start=1):
        venv = tmp_path / f"venv-{index}"
        _run([sys.executable, "-m", "venv", str(venv)], cwd=tmp_path)
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        millrace = venv / (
            "Scripts/millrace.exe" if os.name == "nt" else "bin/millrace"
        )
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-deps",
                str(candidate),
            ],
            cwd=tmp_path,
        )
        smoke = _run(
            [
                str(python),
                "-c",
                (
                    "from importlib.metadata import version;"
                    "import importlib, millrace;"
                    "assert version('millrace-ai') == '0.22.0';"
                    "\ntry: importlib.import_module('millrace.testing')\n"
                    "except ModuleNotFoundError: pass\n"
                    "else: raise AssertionError('millrace.testing shipped')"
                ),
            ],
            cwd=tmp_path,
        )
        assert smoke.stdout == ""
        version_result = _run([str(millrace), "--version"], cwd=tmp_path)
        assert version_result.stdout == "millrace 0.22.0\n"
