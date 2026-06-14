from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

from millrace_ai.doctor import run_workspace_doctor, workspace_checks
from millrace_ai.doctor.checks import DoctorContext, run_doctor_checks
from millrace_ai.doctor.models import DoctorIssue
from millrace_ai.extensions import ExtensionDomain, ExtensionItemKind
from millrace_ai.extensions.builtin.blueprint.contracts import BlueprintManifestDocument
from millrace_ai.extensions.manifest import ExtensionItemManifest, ExtensionPackageManifest
from millrace_ai.paths import bootstrap_workspace, workspace_paths

NOW = datetime(2026, 6, 11, tzinfo=timezone.utc)


def test_doctor_runs_registered_extension_diagnostics(tmp_path: Path, monkeypatch) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    module_name = "tests.doctor._fake_registered_diagnostics"
    module = ModuleType(module_name)

    def run_doctor_diagnostics(context: DoctorContext) -> None:
        context.errors.append(
            DoctorIssue(
                code="registered_diagnostic_ran",
                message="registered diagnostic executed",
                path=paths.root,
            )
        )

    module.run_doctor_diagnostics = run_doctor_diagnostics  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module_name, module)
    monkeypatch.setattr(
        "millrace_ai.doctor.checks.discover_extension_package_manifests",
        lambda *, assets_root=None: (
            ExtensionPackageManifest(
                package_id="example.doctor",
                display_name="Example Doctor Extension",
                domain=ExtensionDomain.GENERIC,
                version="1.0.0",
                items=(
                    ExtensionItemManifest(
                        item_kind=ExtensionItemKind.DOCTOR_DIAGNOSTIC,
                        item_id="example.doctor.diagnostic",
                        implementation_path=module_name,
                        version="1.0.0",
                    ),
                ),
            ),
        ),
    )

    context = DoctorContext(paths=paths, assets_root=paths.runtime_root)
    run_doctor_checks(context)

    assert any(issue.code == "registered_diagnostic_ran" for issue in context.errors)


def test_doctor_does_not_call_unregistered_blueprint_diagnostic_directly(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))

    assert not hasattr(workspace_checks, "check_blueprint_manifest_diagnostics")
    assert not hasattr(workspace_checks, "run_doctor_diagnostics")

    monkeypatch.setattr(
        "millrace_ai.doctor.checks.discover_extension_package_manifests",
        lambda *, assets_root=None: (
            ExtensionPackageManifest(
                package_id="example.no_diagnostics",
                display_name="Example Extension Without Diagnostics",
                domain=ExtensionDomain.GENERIC,
                version="1.0.0",
            ),
        ),
    )

    context = DoctorContext(paths=paths, assets_root=paths.runtime_root)
    run_doctor_checks(context)


def test_doctor_retains_blueprint_manifest_diagnostic_through_extension_registry(
    tmp_path: Path,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    manifest = BlueprintManifestDocument(
        manifest_id="manifest-blueprint-001",
        root_spec_id="spec-blueprint-001",
        root_idea_id="idea-blueprint-001",
        source_work_item_kind="spec",
        source_work_item_id="spec-blueprint-001",
        source_spec_id="spec-blueprint-001",
        draft_ids=("draft-missing",),
        draft_count=1,
        spec_summary="Blueprint manifest fixture.",
        decomposition_strategy="Split the work into deterministic test fixtures.",
        global_acceptance_intent=("Doctor reports malformed Blueprint manifest state.",),
        references=("tests/doctor/test_extension_diagnostics.py",),
        created_at=NOW,
    )
    manifest_path = (
        paths.runtime_root
        / "blueprints"
        / "manifests"
        / f"{manifest.manifest_id}.json"
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")

    report = run_workspace_doctor(paths)

    assert any(
        item.code == "blueprint_manifest_draft_missing"
        and item.path == manifest_path
        for item in report.errors
    )
