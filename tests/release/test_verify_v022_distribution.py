from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "verify_v022_distribution.py"


def _load_verifier() -> ModuleType:
    spec = importlib.util.spec_from_file_location("cut_v022_verifier", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


verifier = _load_verifier()


def _release_projects() -> tuple[Path, ...]:
    plus_env = os.environ.get("CUT_PLUS_ROOT")
    meta_env = os.environ.get("CUT_META_ROOT")
    if (plus_env is None) != (meta_env is None):
        raise AssertionError("CUT_PLUS_ROOT and CUT_META_ROOT must be set together")
    if plus_env is not None and meta_env is not None:
        roots = (Path(plus_env), Path(meta_env))
        if (
            not plus_env
            or not meta_env
            or any(not root.is_absolute() for root in roots)
            or len({ROOT.resolve(), *(root.resolve() for root in roots)}) != 3
        ):
            raise AssertionError("CUT external roots must be absolute and distinct")
        expected_names = ("millrace-plus", "millrace")
        for root, expected_name in zip(roots, expected_names, strict=True):
            try:
                with (root / "pyproject.toml").open("rb") as handle:
                    project_name = tomllib.load(handle)["project"]["name"]
            except (KeyError, OSError, tomllib.TOMLDecodeError) as exc:
                raise AssertionError("CUT external root is not a project") from exc
            if project_name != expected_name:
                raise AssertionError(
                    f"CUT external root must own {expected_name}"
                )
        return (ROOT, *roots)
    return (ROOT,)


def _write_wheel(
    path: Path,
    *,
    distribution: str,
    version: str,
    requires_python: str | None = None,
    requires_dist: tuple[str, ...] = (),
) -> None:
    dist_info = f"{distribution.replace('-', '_')}-{version}.dist-info"
    metadata = [
        "Metadata-Version: 2.4",
        f"Name: {distribution}",
        f"Version: {version}",
    ]
    if requires_python is not None:
        metadata.append(f"Requires-Python: {requires_python}")
    metadata.extend(f"Requires-Dist: {requirement}" for requirement in requires_dist)
    metadata.append("")
    metadata.append("")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{dist_info}/METADATA", "\n".join(metadata))
        archive.writestr(
            f"{dist_info}/WHEEL",
            "Wheel-Version: 1.0\nTag: py3-none-any\n",
        )
        archive.writestr(f"{dist_info}/RECORD", "")


def _wheelhouse(
    root: Path,
    *,
    millforge_requires: tuple[str, ...] = ("fake-dep>=1",),
) -> Path:
    root.mkdir()
    for filename, (distribution, version) in verifier.EXPECTED_WHEELS.items():
        requires_python = ">=3.12" if distribution == "millrace" else ">=3.11"
        requirements = (
            verifier.EXPECTED_BUNDLE_REQUIREMENTS
            if distribution == "millrace"
            else millforge_requires
            if distribution == "millforge"
            else ()
        )
        path = root / filename
        _write_wheel(
            path,
            distribution=distribution,
            version=version,
            requires_python=requires_python,
            requires_dist=requirements,
        )
    return root


def _closure_wheelhouse(
    root: Path,
    wheels: tuple[tuple[str, str, str], ...] = (
        ("fake_dep-1.2.0-py3-none-any.whl", "fake-dep", "1.2.0"),
    ),
) -> Path:
    root.mkdir()
    for filename, distribution, version in wheels:
        _write_wheel(root / filename, distribution=distribution, version=version)
    return root


def _manifest_records(wheelhouse: Path) -> list[dict[str, object]]:
    records = []
    for wheel in sorted(wheelhouse.iterdir()):
        distribution, version, _requires_python, _requires_dist = (
            verifier._wheel_metadata(wheel)
        )
        records.append(
            {
                "distribution": distribution,
                "filename": wheel.name,
                "sha256": verifier.sha256_file(wheel),
                "size": wheel.stat().st_size,
                "version": version,
            }
        )
    return records


def _hash_manifest(path: Path, products: Path, closure: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "product_wheels": _manifest_records(products),
                "resolver_closure": _manifest_records(closure),
            }
        ),
        encoding="utf-8",
    )
    return path


def _git_repo(path: Path) -> str:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "cut@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "CUT test"],
        check=True,
    )
    tracked = path / "tracked.txt"
    tracked.write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _probe(environment_root: Path = Path("/isolated")) -> dict[str, Any]:
    site_packages = environment_root / "lib/python3.12/site-packages"
    distributions = {
        "fake-dep": "1.2.0",
        "millforge": "0.1.0",
        "millrace-ai": "0.22.0",
        "millrace-plus": "0.22.0",
        "millrace": "0.22.0",
        "pip": "25.0",
    }
    return {
        "cli": str(environment_root / "bin/millrace"),
        "distributions": distributions,
        "entry_points": {
            "millforge": [],
            "millrace": [],
            "millrace-ai": [
                {
                    "group": "console_scripts",
                    "name": "millrace",
                    "value": "millrace.adapters.cli.main:cli",
                }
            ],
            "millrace-plus": [],
        },
        "meta_owned_files": [],
        "meta_requires": list(verifier.EXPECTED_BUNDLE_REQUIREMENTS),
        "millforge_descriptor": {
            "package_version": "0.1.0",
            "runner_id": "millforge-base",
            "runner_version": 2,
        },
        "millforge_file": str(site_packages / "millforge/__init__.py"),
        "millrace_file": str(site_packages / "millrace/__init__.py"),
        "package_owners": {
            "millforge": ["millforge"],
            "millrace": ["millrace-ai"],
            "millrace_plus": ["millrace-plus"],
        },
        "prefix": str(environment_root.resolve()),
        "skill_ids": sorted(verifier.EXPECTED_SKILL_IDS),
        "workflow_ids": sorted(verifier.EXPECTED_WORKFLOW_IDS),
    }


def _write_workflow_root(root: Path, *, legacy: bool = False) -> None:
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("name: CI\n", encoding="utf-8")
    (workflows / "publish-to-pypi.yml").write_text(
        "name: Publish\n",
        encoding="utf-8",
    )
    if legacy:
        (workflows / "repo-guardrails.yml").write_text(
            "name: Legacy\n",
            encoding="utf-8",
        )


def _publish_workflow(version: str = "0.22.0") -> str:
    return f"""\
name: Publish
on:
  push:
    tags:
      - v{version}
jobs:
  publish:
    if: ${{{{ github.event_name == 'push' && github.ref == \
'refs/tags/v{version}' }}}}
    runs-on: ubuntu-latest
"""


def test_rejects_contaminated_or_incomplete_wheelhouse(tmp_path: Path) -> None:
    products = _wheelhouse(tmp_path / "products")
    closure = _closure_wheelhouse(tmp_path / "closure")
    manifest = verifier.load_hash_manifest(
        _hash_manifest(tmp_path / "hashes.json", products, closure)
    )
    product_records = verifier.inspect_product_wheelhouse(
        products,
        manifest.product_wheels,
    )
    closure_records = verifier.inspect_resolver_closure(
        closure,
        manifest.resolver_closure,
    )
    verifier.validate_bundle_metadata(product_records)
    assert {record.distribution for record in product_records} == {
        "millforge",
        "millrace",
        "millrace-ai",
        "millrace-plus",
    }
    assert [(record.distribution, record.version) for record in closure_records] == [
        ("fake-dep", "1.2.0")
    ]

    extra = products / "unexpected-1.0.tar.gz"
    extra.write_bytes(b"not a wheel")
    with pytest.raises(verifier.VerificationError, match="exactly"):
        verifier.inspect_product_wheelhouse(products, manifest.product_wheels)
    extra.unlink()

    missing = products / "millrace-0.22.0-py3-none-any.whl"
    missing.unlink()
    with pytest.raises(verifier.VerificationError, match="exactly"):
        verifier.inspect_product_wheelhouse(products, manifest.product_wheels)

    extra_closure = closure / "unused_dep-1.0-py3-none-any.whl"
    _write_wheel(extra_closure, distribution="unused-dep", version="1.0")
    with pytest.raises(verifier.VerificationError, match="exactly"):
        verifier.inspect_resolver_closure(closure, manifest.resolver_closure)
    extra_closure.unlink()

    (closure / "fake_dep-1.2.0-py3-none-any.whl").unlink()
    with pytest.raises(verifier.VerificationError, match="exactly"):
        verifier.inspect_resolver_closure(closure, manifest.resolver_closure)


def test_offline_meta_install_uses_only_selected_wheelhouse(tmp_path: Path) -> None:
    python = tmp_path / "environment" / "bin" / "python"
    products = tmp_path / "products"
    closure = tmp_path / "closure"
    command = verifier.build_install_command(python, products, closure)

    assert command == (
        str(python),
        "-I",
        "-m",
        "pip",
        "install",
        "--no-index",
        "--find-links",
        str(products.resolve()),
        "--find-links",
        str(closure.resolve()),
        "--only-binary=:all:",
        "--no-cache-dir",
        "millrace==0.22.0",
    )
    assert "--require-hashes" not in command
    assert all("millforge" not in part for part in command)
    assert all("millrace-ai" not in part for part in command)
    assert all("millrace-plus" not in part for part in command)


def test_installed_bundle_has_exact_roles_and_versions(tmp_path: Path) -> None:
    compile(verifier._probe_script(), "<installed-probe>", "exec")
    environment_root = tmp_path / "isolated"
    probe = _probe(environment_root)
    closure = (
        verifier.ArtifactRecord(
            filename="fake_dep-1.2.0-py3-none-any.whl",
            distribution="fake-dep",
            version="1.2.0",
            sha256="a" * 64,
            size=1,
            requires_python=None,
            requires_dist=(),
        ),
    )
    verifier.validate_installed_probe(probe, environment_root, closure)

    probe["package_owners"]["millrace"] = ["millrace", "millrace-ai"]
    with pytest.raises(verifier.VerificationError, match="millrace import"):
        verifier.validate_installed_probe(probe, environment_root, closure)

    probe = _probe(environment_root)
    probe["distributions"].pop("fake-dep")
    with pytest.raises(verifier.VerificationError, match="installed distribution"):
        verifier.validate_installed_probe(probe, environment_root, closure)

    probe["distributions"]["fake-dep"] = "1.2.0"
    probe["distributions"]["ambient-dep"] = "9.0"
    with pytest.raises(verifier.VerificationError, match="installed distribution"):
        verifier.validate_installed_probe(probe, environment_root, closure)

    probe["distributions"].pop("ambient-dep")
    probe["millrace_file"] = str(tmp_path / "host/millrace/__init__.py")
    with pytest.raises(verifier.VerificationError, match="outside"):
        verifier.validate_installed_probe(probe, environment_root, closure)


def test_installed_bundle_has_no_external_skill_materialization(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills"
    skill_root.mkdir()
    existing = skill_root / "existing.md"
    existing.write_text("unchanged", encoding="utf-8")
    before = verifier._snapshot_files([skill_root])
    assert before == {str(existing.resolve()): hashlib.sha256(b"unchanged").hexdigest()}

    added = skill_root / "materialized.md"
    added.write_text("forbidden", encoding="utf-8")
    after = verifier._snapshot_files([skill_root])
    assert after != before
    assert str(added.resolve()) in after


def test_isolation_leakage_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PIP_INDEX_URL", "https://example.invalid/simple")
    monkeypatch.setenv("PIP_TRUSTED_HOST", "example.invalid")
    monkeypatch.setenv("PYTHONPATH", "/host/source")
    monkeypatch.setenv("VIRTUAL_ENV", "/host/venv")
    monkeypatch.setenv("API_TOKEN", "forbidden")

    env = verifier.isolated_environment(
        home=tmp_path / "home",
        executable_dir=tmp_path / "bin",
        temp_dir=tmp_path / "tmp",
    )

    assert env["PYTHONPATH"] == ""
    assert env["PIP_NO_INDEX"] == "1"
    assert env["PIP_NO_CACHE_DIR"] == "1"
    assert set(env).isdisjoint(
        {"PIP_INDEX_URL", "PIP_TRUSTED_HOST", "VIRTUAL_ENV", "API_TOKEN"}
    )
    with pytest.raises(
        verifier.VerificationError,
        match="must not contain prior state",
    ):
        root = tmp_path / "dirty-isolation"
        root.mkdir()
        (root / "old").write_text("state", encoding="utf-8")
        verifier.install_and_probe_bundle(
            host_python=Path(sys.executable),
            wheelhouse=tmp_path,
            resolver_closure=tmp_path,
            closure_records=(),
            isolation_root=root,
            external_skill_roots=[],
        )


def test_downloaded_artifact_hash_and_metadata_verification(tmp_path: Path) -> None:
    local = _wheelhouse(tmp_path / "local")
    closure = _closure_wheelhouse(tmp_path / "closure")
    manifest = verifier.load_hash_manifest(
        _hash_manifest(tmp_path / "hashes.json", local, closure)
    )
    downloaded = tmp_path / "downloaded"
    downloaded.mkdir()
    cut_wheels = [
        local / "millrace_ai-0.22.0-py3-none-any.whl",
        local / "millrace_plus-0.22.0-py3-none-any.whl",
        local / "millrace-0.22.0-py3-none-any.whl",
    ]

    for index, wheel in enumerate(cut_wheels, start=1):
        (downloaded / wheel.name).write_bytes(wheel.read_bytes())
        records = verifier.compare_downloaded_artifacts(
            local_dir=local,
            downloaded_dir=downloaded,
            manifest=manifest,
        )
        assert [record.distribution for record in records] == [
            "millrace-ai",
            "millrace-plus",
            "millrace",
        ][:index]

    changed = downloaded / cut_wheels[0].name
    changed.write_bytes(changed.read_bytes() + b"changed")
    with pytest.raises(verifier.VerificationError, match="byte-for-byte"):
        verifier.compare_downloaded_artifacts(
            local_dir=local,
            downloaded_dir=downloaded,
            manifest=manifest,
        )


def test_downloaded_artifacts_refuse_non_prefix_and_millforge(
    tmp_path: Path,
) -> None:
    local = _wheelhouse(tmp_path / "local")
    closure = _closure_wheelhouse(tmp_path / "closure")
    manifest = verifier.load_hash_manifest(
        _hash_manifest(tmp_path / "hashes.json", local, closure)
    )
    downloaded = tmp_path / "downloaded"
    downloaded.mkdir()

    plus = local / "millrace_plus-0.22.0-py3-none-any.whl"
    (downloaded / plus.name).write_bytes(plus.read_bytes())
    with pytest.raises(verifier.VerificationError, match="ordered CUT prefix"):
        verifier.compare_downloaded_artifacts(
            local_dir=local,
            downloaded_dir=downloaded,
            manifest=manifest,
        )

    (downloaded / plus.name).unlink()
    millforge = local / "millforge-0.1.0-py3-none-any.whl"
    (downloaded / millforge.name).write_bytes(millforge.read_bytes())
    with pytest.raises(verifier.VerificationError, match="ordered CUT prefix"):
        verifier.compare_downloaded_artifacts(
            local_dir=local,
            downloaded_dir=downloaded,
            manifest=manifest,
        )


def test_release_sequence_never_skips_or_advances_after_failure() -> None:
    assert verifier.next_release_member([], previous_step_succeeded=True) == (
        "millrace-ai"
    )
    assert verifier.next_release_member(
        ["millrace-ai"],
        previous_step_succeeded=True,
    ) == "millrace-plus"
    assert verifier.next_release_member(
        ["millrace-ai", "millrace-plus"],
        previous_step_succeeded=True,
    ) == "millrace"
    assert verifier.next_release_member(
        ["millrace-ai", "millrace-plus", "millrace"],
        previous_step_succeeded=True,
    ) is None
    assert verifier.next_release_member(
        ["millrace-ai"],
        previous_step_succeeded=False,
    ) is None
    with pytest.raises(verifier.VerificationError, match="skipped or reordered"):
        verifier.next_release_member(
            ["millrace-plus"],
            previous_step_succeeded=True,
        )


def test_rollback_matrix_for_every_publication_phase() -> None:
    expected = {
        "before-upload": ("rebuild-and-reverify", [], False),
        "after-ai-upload": ("stop-yank-defective-members", ["millrace-ai"], True),
        "after-plus-upload": (
            "stop-yank-defective-members",
            ["millrace-ai", "millrace-plus"],
            True,
        ),
        "after-meta-upload": (
            "yank-defective-bundle-and-members",
            ["millrace-ai", "millrace-plus", "millrace"],
            True,
        ),
        "index-verification-unknown": (
            "retain-evidence-and-retry-verification",
            [],
            False,
        ),
        "publisher-identity-mismatch": (
            "revoke-binding-and-treat-upload-as-compromised",
            ["applicable-cut-owned-artifacts"],
            True,
        ),
        "millforge-identity-mismatch": (
            "return-to-mfpkg-before-ai-publication",
            [],
            False,
        ),
    }
    for phase, (response, yank, new_meta_version) in expected.items():
        decision = verifier.rollback_decision(phase)
        assert decision == {
            "advance": False,
            "new_meta_version": new_meta_version,
            "response": response,
            "yank": yank,
        }
    with pytest.raises(verifier.VerificationError, match="unknown rollback phase"):
        verifier.rollback_decision("invented")


def test_bundle_composition_reuses_compatible_unchanged_members() -> None:
    initial = verifier.compose_bundle(
        meta_version="0.22.0",
        millforge_version="0.1.0",
        ai_version="0.22.0",
        plus_version="0.22.0",
    )
    ai_patch = verifier.compose_bundle(
        meta_version="0.22.1",
        millforge_version=initial["millforge"],
        ai_version="0.22.1",
        plus_version=initial["millrace-plus"],
    )
    assert ai_patch == {
        "millforge": "0.1.0",
        "millrace-ai": "0.22.1",
        "millrace-plus": "0.22.0",
        "millrace": "0.22.1",
    }
    with pytest.raises(verifier.VerificationError, match="explicit release versions"):
        verifier.compose_bundle(
            meta_version="0.22.1",
            millforge_version="0.1.0",
            ai_version="0.0.0",
            plus_version="0.22.0",
        )


def test_all_release_workflows_require_exact_project_version_tag() -> None:
    for project in _release_projects():
        version = verifier.project_version(project)
        workflow = project / ".github" / "workflows" / "publish-to-pypi.yml"
        cases = {
            f"refs/tags/v{version}": True,
            f"v{version}": False,
            f"refs/tags/{version}": False,
            f"refs/tags/release-v{version}": False,
            f"refs/tags/v{version}-post1": False,
            "refs/tags/v0.21.1": False,
            f"refs/tags/v{version}-rc1": False,
        }
        assert {
            ref: verifier.workflow_accepts_ref(workflow, version, ref)
            for ref in cases
        } == cases


def test_release_projects_refuse_malformed_external_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CUT_PLUS_ROOT", str(ROOT))
    monkeypatch.setenv("CUT_META_ROOT", str(ROOT))
    with pytest.raises(AssertionError, match="absolute and distinct"):
        _release_projects()


def test_exact_tag_proof_rejects_permissive_workflows(tmp_path: Path) -> None:
    expected_ref = "refs/tags/v0.22.0"
    exact = _publish_workflow()
    hostile = {
        "permissive.yml": exact.replace(
            "github.ref == 'refs/tags/v0.22.0'",
            "github.ref == 'refs/tags/v0.22.0' || true",
        ),
        "second-ref.yml": exact.replace(
            "github.ref == 'refs/tags/v0.22.0'",
            "github.ref == 'refs/tags/v0.22.0' || "
            "github.ref == 'refs/tags/v0.22.1'",
        ),
        "wildcard.yml": exact.replace("- v0.22.0", "- v*"),
        "second-tag.yml": exact.replace("- v0.22.0", "- v0.22.0\n      - v0.22.1"),
        "extra-trigger.yml": exact.replace(
            "  push:\n",
            "  workflow_dispatch:\n  push:\n",
        ),
        "mismatched-guard.yml": exact.replace(
            "refs/tags/v0.22.0",
            "refs/tags/v0.22.1",
            1,
        ),
    }
    exact_path = tmp_path / "exact.yml"
    exact_path.write_text(exact, encoding="utf-8")
    assert verifier.workflow_accepts_ref(exact_path, "0.22.0", expected_ref)
    for filename, text in hostile.items():
        path = tmp_path / filename
        path.write_text(text, encoding="utf-8")
        assert not verifier.workflow_accepts_ref(path, "0.22.0", expected_ref)


def test_publish_workflows_keep_source_cleanliness_gates() -> None:
    for project in _release_projects():
        text = (
            project / ".github" / "workflows" / "publish-to-pypi.yml"
        ).read_text(encoding="utf-8")
        assert "git status --porcelain=v1 --untracked-files=all" in text
        assert "continue-on-error:" not in text
        assert "|| true" not in text


def test_promoted_workflow_inventory_retires_legacy_guardrails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    rewrite = tmp_path / "rewrite"
    canonical = tmp_path / "canonical"
    _write_workflow_root(rewrite)
    _write_workflow_root(canonical)
    verifier.verify_workflow_inventory(rewrite, canonical)

    legacy = tmp_path / "legacy"
    _write_workflow_root(legacy, legacy=True)
    with pytest.raises(verifier.VerificationError, match="workflow inventory"):
        verifier.verify_workflow_inventory(rewrite, legacy)

    canonical_root = os.environ.get("CUT_CANONICAL_ROOT")
    if canonical_root:
        verifier.verify_workflow_inventory(ROOT, Path(canonical_root))


def test_workflow_inventory_inputs_are_both_or_neither_and_offline_only(
    tmp_path: Path,
) -> None:
    rewrite = tmp_path / "rewrite"
    canonical = tmp_path / "canonical"
    _write_workflow_root(rewrite)
    _write_workflow_root(canonical)
    assert (
        verifier.workflow_inventory_status(
            mode="offline-wheelhouse",
            rewrite_root=rewrite,
            canonical_root=canonical,
        )
        == "passed"
    )
    assert (
        verifier.workflow_inventory_status(
            mode="offline-wheelhouse",
            rewrite_root=None,
            canonical_root=None,
        )
        == "not-run"
    )
    with pytest.raises(verifier.VerificationError, match="both or neither"):
        verifier.workflow_inventory_status(
            mode="offline-wheelhouse",
            rewrite_root=rewrite,
            canonical_root=None,
        )
    with pytest.raises(verifier.VerificationError, match="offline"):
        verifier.workflow_inventory_status(
            mode="downloaded-index-artifacts",
            rewrite_root=rewrite,
            canonical_root=canonical,
        )


def test_command_results_and_promotion_provenance(tmp_path: Path) -> None:
    assert verifier.validate_command_results(
        ["rewrite.pytest=passed", "e2e.vendor=skipped"]
    ) == [
        {"command": "e2e.vendor", "result": "skipped"},
        {"command": "rewrite.pytest", "result": "passed"},
    ]
    with pytest.raises(verifier.VerificationError, match="at least one"):
        verifier.validate_command_results([])
    with pytest.raises(verifier.VerificationError, match="duplicate"):
        verifier.validate_command_results(
            ["rewrite.pytest=passed", "rewrite.pytest=blocked"]
        )

    rewrite = tmp_path / "rewrite"
    _git_repo(rewrite)
    _write_workflow_root(rewrite)
    subprocess.run(["git", "-C", str(rewrite), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(rewrite), "commit", "-qm", "reviewed"],
        check=True,
    )
    reviewed_commit = subprocess.run(
        ["git", "-C", str(rewrite), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    canonical = tmp_path / "canonical"
    original_commit = _git_repo(canonical)
    subprocess.run(
        [
            "git",
            "-C",
            str(canonical),
            "commit",
            "--allow-empty",
            "-qm",
            "actual pre-promotion head",
        ],
        check=True,
    )
    old_commit = subprocess.run(
        ["git", "-C", str(canonical), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _write_workflow_root(canonical)
    subprocess.run(["git", "-C", str(canonical), "add", "-A"], check=True)
    with pytest.raises(verifier.VerificationError, match="pre-commit HEAD"):
        verifier.promotion_provenance(
            rewrite_root=rewrite,
            canonical_root=canonical,
            old_canonical_commit=original_commit,
            reviewed_rewrite_commit=reviewed_commit,
        )
    staged = verifier.promotion_provenance(
        rewrite_root=rewrite,
        canonical_root=canonical,
        old_canonical_commit=old_commit,
        reviewed_rewrite_commit=reviewed_commit,
    )
    assert staged["status"] == "staged-pre-commit"
    assert staged["promoted_canonical_commit"] is None
    assert staged["tracked_tree_equal"] is True

    subprocess.run(
        ["git", "-C", str(canonical), "commit", "-qm", "promoted"],
        check=True,
    )
    committed = verifier.promotion_provenance(
        rewrite_root=rewrite,
        canonical_root=canonical,
        old_canonical_commit=old_commit,
        reviewed_rewrite_commit=reviewed_commit,
    )
    assert committed["status"] == "committed"
    assert committed["promoted_canonical_commit"] is not None
    with pytest.raises(verifier.VerificationError, match="directly follow"):
        verifier.promotion_provenance(
            rewrite_root=rewrite,
            canonical_root=canonical,
            old_canonical_commit=original_commit,
            reviewed_rewrite_commit=reviewed_commit,
        )


def test_cli_smoke_asserts_fingerprint_and_readable_export(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    selected = "a" * 64

    def fake_run(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
    ) -> dict[str, Any]:
        del cwd, env
        if "export-archive" in command:
            output = Path(command[command.index("--output") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            member = tmp_path / "member.txt"
            member.write_text("payload", encoding="utf-8")
            with tarfile.open(output, "w") as archive:
                archive.add(member, arcname="member.txt")
        if "admit-package" in command:
            return {
                "data": {"plan": {"authority_fingerprint": selected}},
                "ok": True,
            }
        if command[-1] == "status":
            return {
                "data": {
                    "selected_plan": {"authority_fingerprint": selected}
                },
                "ok": True,
            }
        return {"data": {}, "ok": True}

    monkeypatch.setattr(verifier, "_run_json_command", fake_run)
    result = verifier.run_installed_cli_smoke(
        executable_dir=tmp_path / "environment/bin",
        workspace=tmp_path / "workspace",
        environment={},
    )
    assert result == {
        "admitted_authority_fingerprint": selected,
        "archive_export": {
            "exists": True,
            "nonempty": True,
            "readable_tar": True,
        },
        "selected_status_fingerprint": selected,
    }

    def mismatched_status(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
    ) -> dict[str, Any]:
        result = fake_run(command, cwd=cwd, env=env)
        if command[-1] == "status":
            result["data"]["selected_plan"]["authority_fingerprint"] = "b" * 64
        return result

    monkeypatch.setattr(verifier, "_run_json_command", mismatched_status)
    with pytest.raises(verifier.VerificationError, match="fingerprint"):
        verifier.run_installed_cli_smoke(
            executable_dir=tmp_path / "environment/bin",
            workspace=tmp_path / "workspace-mismatch",
            environment={},
        )


def test_cli_smoke_refuses_missing_or_unreadable_export(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    selected = "a" * 64

    def fake_run(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
    ) -> dict[str, Any]:
        del cwd, env
        if "admit-package" in command:
            return {
                "data": {"plan": {"authority_fingerprint": selected}},
                "ok": True,
            }
        if command[-1] == "status":
            return {
                "data": {
                    "selected_plan": {"authority_fingerprint": selected}
                },
                "ok": True,
            }
        return {"data": {}, "ok": True}

    monkeypatch.setattr(verifier, "_run_json_command", fake_run)
    with pytest.raises(verifier.VerificationError, match="export"):
        verifier.run_installed_cli_smoke(
            executable_dir=tmp_path / "environment/bin",
            workspace=tmp_path / "workspace",
            environment={},
        )


def test_source_roots_must_match_pins_and_be_clean(tmp_path: Path) -> None:
    names = ("millforge", "millrace-ai", "millrace-plus", "millrace")
    roots: list[str] = []
    pins: list[str] = []
    paths: dict[str, Path] = {}
    for name in names:
        root = tmp_path / name
        commit = _git_repo(root)
        paths[name] = root
        roots.append(f"{name}={root}")
        pins.append(f"{name}={commit}")

    evidence = verifier.verify_source_roots(roots, verifier.validate_source_pins(pins))
    assert evidence == [
        {"clean": True, "commit": pin.split("=", 1)[1], "source": name}
        for name, pin in sorted(zip(names, pins, strict=True))
    ]
    assert str(tmp_path) not in json.dumps(evidence)

    (paths["millforge"] / ".DS_Store").write_bytes(b"ignored")
    verifier.verify_source_roots(roots, verifier.validate_source_pins(pins))

    (paths["millrace-ai"] / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(verifier.VerificationError, match="not clean"):
        verifier.verify_source_roots(roots, verifier.validate_source_pins(pins))


def test_source_roots_refuse_missing_or_mismatched_input(tmp_path: Path) -> None:
    names = ("millforge", "millrace-ai", "millrace-plus", "millrace")
    roots = []
    pins = []
    for name in names:
        root = tmp_path / name
        commit = _git_repo(root)
        roots.append(f"{name}={root}")
        pins.append(f"{name}={commit}")
    validated_pins = verifier.validate_source_pins(pins)

    with pytest.raises(verifier.VerificationError, match="source roots"):
        verifier.verify_source_roots(roots[:-1], validated_pins)
    wrong_pins = dict(validated_pins)
    wrong_pins["millrace"] = "f" * 40
    with pytest.raises(verifier.VerificationError, match="HEAD"):
        verifier.verify_source_roots(roots, wrong_pins)


def test_evidence_record_is_complete_deterministic_and_secret_free(
    tmp_path: Path,
) -> None:
    products = _wheelhouse(tmp_path / "products")
    closure = _closure_wheelhouse(tmp_path / "closure")
    manifest = verifier.load_hash_manifest(
        _hash_manifest(tmp_path / "hashes.json", products, closure)
    )
    product_records = verifier.inspect_product_wheelhouse(
        products,
        manifest.product_wheels,
    )
    closure_records = verifier.inspect_resolver_closure(
        closure,
        manifest.resolver_closure,
    )
    payload = {
        "command_results": [
            {"command": "rewrite.pytest", "result": "passed"}
        ],
        "install": None,
        "mode": "downloaded-index-artifacts",
        "product_wheels": [
            record.as_evidence() for record in product_records
        ],
        "promotion": {"status": "not-run"},
        "release_matrix": verifier.release_matrix(("millrace-ai",)),
        "resolver_closure": [
            record.as_evidence() for record in closure_records
        ],
        "rollback_matrix": verifier.rollback_matrix(),
        "schema_version": 2,
        "sources": [
            {"clean": True, "commit": "a" * 40, "source": "millforge"},
            {"clean": True, "commit": "b" * 40, "source": "millrace"},
            {"clean": True, "commit": "c" * 40, "source": "millrace-ai"},
            {"clean": True, "commit": "d" * 40, "source": "millrace-plus"},
        ],
        "target": {"platform": "test-platform", "python": "3.12.9"},
        "workflow_inventory": "not-run",
    }
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    assert verifier.write_evidence(first, payload) == verifier.write_evidence(
        second,
        payload,
    )
    assert first.read_bytes() == second.read_bytes()
    assert json.loads(first.read_text(encoding="ascii")) == payload

    with pytest.raises(verifier.VerificationError, match="secret-like"):
        verifier.write_evidence(tmp_path / "secret.json", {"api_token": "value"})
    with pytest.raises(verifier.VerificationError, match="absolute path"):
        verifier.write_evidence(tmp_path / "path.json", {"value": str(tmp_path)})


def test_hash_manifest_and_source_pins_refuse_ambiguous_inputs(
    tmp_path: Path,
) -> None:
    products = _wheelhouse(tmp_path / "products")
    closure = _closure_wheelhouse(tmp_path / "closure")
    manifest_path = _hash_manifest(
        tmp_path / "hashes.json",
        products,
        closure,
    )
    manifest = verifier.load_hash_manifest(manifest_path)
    assert {record.filename for record in manifest.product_wheels} == set(
        verifier.EXPECTED_WHEELS
    )
    assert [record.distribution for record in manifest.resolver_closure] == [
        "fake-dep"
    ]
    pins = [
        "millforge=" + "a" * 40,
        "millrace-ai=" + "b" * 40,
        "millrace-plus=" + "c" * 40,
        "millrace=" + "d" * 40,
    ]
    assert set(verifier.validate_source_pins(pins)) == verifier.EXPECTED_SOURCE_PINS
    with pytest.raises(verifier.VerificationError, match="source pins"):
        verifier.validate_source_pins(pins[:-1])

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    del payload["resolver_closure"][0]["size"]
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(verifier.VerificationError, match="manifest record"):
        verifier.load_hash_manifest(manifest_path)
