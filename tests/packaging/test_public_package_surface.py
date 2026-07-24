from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import textwrap
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALLOWED_BASE_WORKFLOW_MODULES = {
    "millrace/workflows/__init__.py",
    "millrace/workflows/inventory.py",
    "millrace/workflows/kernel_ping.py",
}
DONOR_WORKFLOW_MODULES = {
    "millrace/workflows/lad_execution.py",
    "millrace/workflows/lad_learning.py",
    "millrace/workflows/lad_planning.py",
    "millrace/workflows/simple_loop.py",
    "millrace/workflows/vendor_selection.py",
}
LIVE_E2E_ARTIFACT_SUFFIXES = (
    "/tests/support/e2e_actual_model.py",
    "/docs/e2e-live-smoke.md",
)


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPATH", None)
    return env


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True, env=_subprocess_env())


def _venv_python(venv: Path) -> str:
    if os.name == "nt":
        return str(venv / "Scripts" / "python.exe")
    return str(venv / "bin" / "python")


def _copy_project_for_build(target: Path) -> None:
    ignored = shutil.ignore_patterns(
        ".DS_Store",
        "__pycache__",
        "*.pyc",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".venv",
        "dist",
        "build",
        "*.egg-info",
    )
    for filename in ("LICENSE", "README.md", "pyproject.toml"):
        shutil.copy2(PROJECT_ROOT / filename, target / filename)
    for directory in ("docs", "src", "tests"):
        shutil.copytree(
            PROJECT_ROOT / directory,
            target / directory,
            ignore=ignored,
        )


def _generated_runtime_members(names: set[str]) -> list[str]:
    return sorted(
        name
        for name in names
        if "/millrace/" in f"/{name}"
        and (
            "/__pycache__/" in name
            or name.endswith(".pyc")
            or name.endswith(".pyo")
            or name.endswith(".DS_Store")
        )
    )


def _wheel_workflow_modules(names: set[str]) -> set[str]:
    return {
        name
        for name in names
        if name.startswith("millrace/workflows/") and name.endswith(".py")
    }


def _sdist_workflow_modules(names: set[str]) -> set[str]:
    return {
        "/".join(Path(name).parts[-4:])
        for name in names
        if "/src/millrace/workflows/" in name and name.endswith(".py")
    }


def test_built_base_artifacts_ship_only_kernel_ping_workflow_modules(
    tmp_path: Path,
) -> None:
    build_dir = tmp_path / "dist"
    _run(
        [
            "uv",
            "build",
            "--out-dir",
            str(build_dir),
            "--clear",
        ],
        cwd=PROJECT_ROOT,
    )
    wheels = sorted(build_dir.glob("*.whl"))
    sdists = sorted(build_dir.glob("*.tar.gz"))
    assert len(wheels) == 1
    assert len(sdists) == 1

    with zipfile.ZipFile(wheels[0]) as archive:
        wheel_names = set(archive.namelist())
        wheel_workflows = _wheel_workflow_modules(wheel_names)

    with tarfile.open(sdists[0], "r:gz") as archive:
        sdist_names = {member.name for member in archive.getmembers()}
        sdist_workflows = _sdist_workflow_modules(sdist_names)

    assert wheel_workflows == ALLOWED_BASE_WORKFLOW_MODULES
    assert wheel_workflows.isdisjoint(DONOR_WORKFLOW_MODULES)
    assert sdist_workflows == {f"src/{name}" for name in ALLOWED_BASE_WORKFLOW_MODULES}
    assert sdist_workflows.isdisjoint(
        {f"src/{name}" for name in DONOR_WORKFLOW_MODULES}
    )
    for names in (wheel_names, sdist_names):
        assert not any("/tests/e2e/" in f"/{name}" for name in names)
        assert not any(
            f"/{name}".endswith(suffix)
            for name in names
            for suffix in LIVE_E2E_ARTIFACT_SUFFIXES
        )
    assert not any("/tests/" in f"/{name}" for name in sdist_names)


def test_built_wheel_advertises_typing_and_imports_public_api(
    tmp_path: Path,
) -> None:
    build_dir = tmp_path / "dist"
    _run(
        [
            "uv",
            "build",
            "--wheel",
            "--out-dir",
            str(build_dir),
            "--clear",
        ],
        cwd=PROJECT_ROOT,
    )
    wheels = sorted(build_dir.glob("*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]

    with zipfile.ZipFile(wheel) as archive:
        assert "millrace/py.typed" in set(archive.namelist())

    venv = tmp_path / "wheel-smoke-venv"
    _run([sys.executable, "-m", "venv", str(venv)])
    python = _venv_python(venv)
    _run(
        [
            python,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            str(wheel),
        ]
    )
    smoke_script = textwrap.dedent(
        f"""
        import importlib
        import importlib.resources
        from pathlib import Path

        import millrace
        import millrace.compiler as compiler
        import millrace.compiler.export as compiler_export
        from millrace.compiler import (
            CompiledPlanExportError,
            compiled_plan_export_bytes,
            compiled_plan_export_record,
            compile_workflow,
            verify_compiled_plan_export_bytes,
            verify_compiled_plan_export_record,
        )
        from millrace.contracts import SelectedCompiledPlan
        from millrace.kernel import decide
        from millrace.substrate import (
            ContentAddressedByteStore,
            SQLiteRuntimeStore,
            StorageIntegrityError,
            storage_digest_for_bytes,
        )
        import millrace.workflows as workflows
        from millrace.workflows import (
            INCLUDED_WORKFLOW_IDS,
            IncludedWorkflow,
            included_workflow_source,
            included_workflows,
            kernel_ping,
        )

        assert millrace.__name__ == "millrace"
        assert "{PROJECT_ROOT.resolve()}" not in str(
            Path(millrace.__file__).resolve()
        )
        assert callable(compile_workflow)
        assert callable(compiled_plan_export_bytes)
        assert callable(compiled_plan_export_record)
        assert callable(verify_compiled_plan_export_bytes)
        assert callable(verify_compiled_plan_export_record)
        assert issubclass(CompiledPlanExportError, ValueError)
        assert SelectedCompiledPlan.__name__ == "SelectedCompiledPlan"
        assert callable(decide)
        assert SQLiteRuntimeStore.__name__ == "SQLiteRuntimeStore"
        assert ContentAddressedByteStore.__name__ == "ContentAddressedByteStore"
        assert StorageIntegrityError.__name__ == "StorageIntegrityError"
        assert storage_digest_for_bytes(b"ok").startswith("sha256:")
        assert importlib.resources.files("millrace").joinpath("py.typed").is_file()

        assert compiler.__all__ == (
            "AUTHORITY_FINGERPRINT_DOMAIN_PREFIX",
            "CanonicalAuthorityError",
            "CompiledPlanExportError",
            "CompileResult",
            "DEFAULT_SELECTED_RUNNER_ADAPTER_KIND",
            "DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY",
            "VerifiedCompiledPlanExport",
            "RUNNER_ADAPTER_KIND_DEFAULTED",
            "SelectedRunnerAdapterPolicy",
            "authority_fingerprint",
            "canonical_authority_bytes",
            "compile_workflow",
            "compiled_plan_export_bytes",
            "compiled_plan_export_record",
            "verify_compiled_plan_export_bytes",
            "verify_compiled_plan_export_record",
        )
        assert all(not name.startswith("_") for name in compiler.__all__)
        assert compiler_export.__all__ == (
            "CANONICALIZATION_ALGORITHM",
            "COMPILED_PLAN_EXPORT_RECORD_KIND",
            "COMPILED_PLAN_EXPORT_SCHEMA_VERSION",
            "CompiledPlanExportError",
            "COMPILER_ID",
            "COMPILER_PROTOCOL_VERSION",
            "EXPORT_AUTHORITY_FINGERPRINT_DOMAIN",
            "EXPORT_HASH_ALGORITHM",
            "VerifiedCompiledPlanExport",
            "compiled_plan_export_bytes",
            "compiled_plan_export_record",
            "verify_compiled_plan_export_bytes",
            "verify_compiled_plan_export_record",
        )
        assert all(not name.startswith("_") for name in compiler_export.__all__)
        assert isinstance(compiler_export.COMPILED_PLAN_EXPORT_RECORD_KIND, str)
        assert isinstance(compiler_export.COMPILED_PLAN_EXPORT_SCHEMA_VERSION, int)
        assert isinstance(compiler_export.COMPILER_ID, str)
        assert isinstance(compiler_export.COMPILER_PROTOCOL_VERSION, int)
        assert isinstance(compiler_export.CANONICALIZATION_ALGORITHM, str)
        assert isinstance(compiler_export.EXPORT_HASH_ALGORITHM, str)
        assert isinstance(compiler_export.EXPORT_AUTHORITY_FINGERPRINT_DOMAIN, str)
        assert compiler_export.VerifiedCompiledPlanExport.__name__ == (
            "VerifiedCompiledPlanExport"
        )
        assert not hasattr(millrace, "compiled_plan_export_bytes")
        assert not hasattr(millrace, "import_compiled_plan_export")
        assert not hasattr(compiler, "import_compiled_plan_export")
        assert workflows.__all__ == (
            "kernel_ping",
            "IncludedWorkflow",
            "INCLUDED_WORKFLOW_IDS",
            "included_workflows",
            "included_workflow_source",
        )
        assert INCLUDED_WORKFLOW_IDS == ("kernel_ping",)
        assert included_workflows() == (
            IncludedWorkflow(
                workflow_id="kernel_ping",
                workflow_version="0.1",
                display_name="Kernel Ping",
                source_module="millrace.workflows.kernel_ping",
                provenance="base-included-diagnostic",
            ),
        )
        assert all(
            name not in workflows.__all__
            for name in (
                "lad_execution",
                "lad_learning",
                "lad_planning",
                "simple_loop",
                "vendor_selection",
            )
        )
        try:
            included_workflow_source("simple_loop")
        except KeyError as exc:
            assert exc.args == ("simple_loop",)
        else:
            raise AssertionError("simple_loop must not be a base included workflow")

        donor_modules = (
            "lad_execution",
            "lad_learning",
            "lad_planning",
            "simple_loop",
            "vendor_selection",
        )
        for module_name in donor_modules:
            qualified_name = f"millrace.workflows.{{module_name}}"
            try:
                importlib.import_module(qualified_name)
            except ModuleNotFoundError as exc:
                assert exc.name == qualified_name
            else:
                raise AssertionError(
                    f"donor workflow module {{module_name}} must not be importable"
                )

        result = compile_workflow(included_workflow_source("kernel_ping"))
        assert [d for d in result.diagnostics if d.severity == "error"] == []
        assert result.plan is not None
        export_bytes = compiled_plan_export_bytes(result.plan)
        verified = verify_compiled_plan_export_bytes(export_bytes)
        assert verified.workflow_id == "kernel_ping"
        """
    )
    _run([python, "-c", smoke_script], cwd=tmp_path)


def test_build_artifacts_exclude_generated_files_under_runtime_package(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _copy_project_for_build(project)

    generated_files = (
        project / "src" / "millrace" / ".DS_Store",
        project / "src" / "millrace" / "__pycache__" / "root.pyc",
        project / "src" / "millrace" / "workflows" / ".DS_Store",
        project / "src" / "millrace" / "workflows" / "__pycache__" / "fixture.pyc",
    )
    for path in generated_files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"generated")

    build_dir = tmp_path / "dist"
    _run(
        [
            "uv",
            "build",
            "--out-dir",
            str(build_dir),
            "--clear",
        ],
        cwd=project,
    )
    wheels = sorted(build_dir.glob("*.whl"))
    sdists = sorted(build_dir.glob("*.tar.gz"))
    assert len(wheels) == 1
    assert len(sdists) == 1

    with zipfile.ZipFile(wheels[0]) as archive:
        wheel_generated = _generated_runtime_members(set(archive.namelist()))

    with tarfile.open(sdists[0], "r:gz") as archive:
        sdist_generated = _generated_runtime_members(
            {member.name for member in archive.getmembers()}
        )

    assert wheel_generated == []
    assert sdist_generated == []


def test_public_package_imports_without_millforge_installed() -> None:
    script = textwrap.dedent(
        """
        import builtins

        original_import = builtins.__import__

        def blocked_import(name, *args, **kwargs):
            if name == "millforge" or name.startswith("millforge."):
                raise ImportError("Millforge is intentionally unavailable")
            return original_import(name, *args, **kwargs)

        builtins.__import__ = blocked_import
        import millrace
        from millrace.adapters.millforge import MillforgeAdapter

        assert millrace.__name__ == "millrace"
        assert MillforgeAdapter.__name__ == "MillforgeAdapter"
        """
    )

    _run(["uv", "run", "--frozen", "python", "-c", script], cwd=PROJECT_ROOT)
