from __future__ import annotations

import base64
import hashlib

import pytest

from millrace.adapters.cli.setup import SetupService
from millrace.adapters.cli.setup_inventory import installed_component, observe_selection
from millrace.contracts.setup import SetupRefusal, SetupRequest


def distribution(
    root,
    *,
    name="millrace-ai",
    version="0.22.3",
    entry="millrace.adapters.cli.main:cli",
):
    info = root / f"{name.replace('-', '_')}-0.0.0.dist-info"
    info.mkdir(parents=True)
    package_roots = {
        "millrace-ai": "millrace",
        "millrace-plus": "millrace_workflow_package",
        "millrace": None,
    }
    package_root = package_roots[name]
    module = root / package_root / "__init__.py" if package_root else None
    if module is not None:
        module.parent.mkdir()
        module.write_text('"""Fixture package, never imported."""\n')
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\n"
        + (f"Version: {version}\n" if version is not None else "")
    )
    if entry is not None:
        (info / "entry_points.txt").write_text(
            f"[console_scripts]\nmillrace = {entry}\n"
        )
    rows = []
    for file in [*([module] if module is not None else []), *sorted(info.iterdir())]:
        value = (
            base64.urlsafe_b64encode(hashlib.sha256(file.read_bytes()).digest())
            .rstrip(b"=")
            .decode()
        )
        rows.append(f"{file.relative_to(root)},sha256={value},{file.stat().st_size}\n")
    (info / "RECORD").write_text("".join(rows))
    return module


def test_actual_record_bytes_are_not_a_wheel_hash(tmp_path, monkeypatch):
    module = distribution(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    observed = installed_component("millrace-ai")
    assert observed.installed_bytes_digest.startswith("sha256:")
    assert observed.pin.artifact_sha256 is None
    assert observed.command_disposition == "declared"
    assert observed.loaded_origin_disposition == "source_overlay"
    module.write_text("changed installed bytes\n")
    assert installed_component("millrace-ai").disposition == "installed_bytes_mismatch"


def test_unknown_installed_version_refuses_compatibility(tmp_path, monkeypatch):
    distribution(tmp_path, version="999.0")
    monkeypatch.syspath_prepend(str(tmp_path))
    actual = installed_component("millrace-ai")
    assert actual.disposition == "incompatible" and actual.pin.contract_version is None


@pytest.mark.parametrize(
    ("name", "version", "contract"),
    [
        ("millrace-ai", "0.22.4.dev6+pi.local01", "setup.v1/store.11/plan.18"),
        ("millrace-ai", "0.22.4", "setup.v1/store.11/plan.18"),
        ("millrace-plus", "0.22.4.dev5+pi.local01", "workflow-package.v1"),
        ("millrace-plus", "0.22.4", "workflow-package.v1"),
        ("millrace", "0.22.4", "dependency-only.v1"),
    ],
)
def test_reviewed_release_versions_keep_exact_contracts(
    tmp_path, monkeypatch, name, version, contract
):
    distribution(
        tmp_path,
        name=name,
        version=version,
        entry="millrace.adapters.cli.main:cli" if name == "millrace-ai" else None,
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    actual = installed_component(name)
    assert actual.disposition == "installed_bytes_correlated"
    assert actual.pin.contract_version == contract


@pytest.mark.parametrize(
    ("name", "version"),
    [
        ("millrace-ai", "0.22.4.dev6+pi.local02"),
        ("millrace-plus", "0.22.4.dev5+pi.local02"),
        ("millrace", "0.22.4.dev0+s02mac02"),
    ],
)
def test_unreviewed_nearby_versions_still_refuse_compatibility(
    tmp_path, monkeypatch, name, version
):
    distribution(
        tmp_path,
        name=name,
        version=version,
        entry="millrace.adapters.cli.main:cli" if name == "millrace-ai" else None,
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    actual = installed_component(name)
    assert actual.disposition == "incompatible" and actual.pin.contract_version is None


def test_changed_real_installation_invalidates_owner_snapshot(tmp_path, monkeypatch):
    module = distribution(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    service = SetupService()
    value = service.inspect().to_wire()
    module.write_text("replacement\n")
    with pytest.raises(SetupRefusal, match="observation_changed"):
        service.validate_response(value)


def files(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def test_readonly_workspace_inspection_preserves_all_bytes(tmp_path):
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    workspace = tmp_path / "workspace"
    cas = workspace / ".millrace" / "cas"
    cas.mkdir(parents=True)
    db = cas.parent / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db, workspace_path=workspace, cas_path=cas)
    store.close()
    before = files(workspace)
    result = observe_selection(
        SetupRequest(action_kind="useful_recipe", workspace=str(workspace))
    )
    assert result.plan_disposition == "plan_not_selected"
    assert result.selection.runtime_identity.workspace_path == str(workspace)
    assert files(workspace) == before


def test_corrupt_store_is_not_repaired(tmp_path):
    cas = tmp_path / ".millrace" / "cas"
    cas.mkdir(parents=True)
    (cas.parent / "runtime.sqlite3").write_bytes(b"not sqlite")
    before = files(tmp_path)
    result = observe_selection(
        SetupRequest(action_kind="useful_recipe", workspace=str(tmp_path))
    )
    assert result.plan_disposition == "workspace_unverifiable"
    assert files(tmp_path) == before


def test_real_selected_plan_and_registry_are_readonly_and_invalidate(tmp_path):
    import io
    import json
    from contextlib import redirect_stdout

    from millrace.adapters.cli.main import main
    from support.workflow_package_active_pinning import package_manifest
    from support.workflow_packages import write_workflow_package_path

    workspace = tmp_path / "workspace"
    package = tmp_path / "package"
    package.mkdir()
    asset = b"Inspection fixture\n"
    write_workflow_package_path(
        package,
        manifest=package_manifest(
            package_id="pkg.setup",
            package_version="1.0.0",
            workflow_id="wf.setup",
            workflow_version="1",
            source_kind="path",
            asset_bytes=asset,
        ),
        asset_bytes=asset,
    )

    def run(*args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["--json", "--workspace", str(workspace), *args])
        assert code == 0, out.getvalue()
        return json.loads(out.getvalue())

    run("workspace", "init", "--input-id", "init")
    run("package", "import-path", str(package), "--command-id", "import")
    run("package", "enable", "pkg.setup", "1.0.0", "--command-id", "enable")
    admitted = run(
        "plan",
        "admit-package",
        "pkg.setup",
        "1.0.0",
        "--workflow-id",
        "wf.setup",
        "--workflow-version",
        "1",
        "--entrypoint",
        "default",
        "--command-id",
        "compile",
        "--input-id",
        "admit",
    )
    fp = admitted["data"]["plan"]["authority_fingerprint"]
    request = SetupRequest(
        action_kind="useful_recipe", workspace=str(workspace), plan_fingerprint=fp
    )
    before = files(workspace)
    service = SetupService(request)
    wire = service.inspect().to_wire()
    assert service.validate_response(wire)
    assert files(workspace) == before
    assert wire["selection"]["package_id"] == "pkg.setup"
    assert wire["selection"]["plan_fingerprint"] == fp
    assert wire["selection"]["source_digest"].startswith("sha256:")
    assert (
        next(c for c in wire["checks"] if c["id"] == "recipe.plan")["disposition"]
        == "pass"
    )
    run("package", "disable", "pkg.setup", "1.0.0", "--command-id", "disable")
    with pytest.raises(SetupRefusal, match="observation_changed"):
        service.validate_response(wire)


@pytest.mark.parametrize(
    "version", [None, "x" * 513, ""], ids=["missing", "overlong", "empty"]
)
def test_malformed_actual_version_is_unavailable(tmp_path, monkeypatch, version):
    distribution(tmp_path, version=version)
    monkeypatch.syspath_prepend(str(tmp_path))
    component = installed_component("millrace-ai")
    assert component.disposition == "version_unverifiable"
    assert component.pin.version == "unavailable"
    assert component.pin.contract_version is None
    assert component.command_disposition == "declared"
    assert component.loaded_origin_disposition == "source_overlay"
    wire = SetupService().inspect().to_wire()
    assert any(b["code"] == "component_version_unverifiable" for b in wire["blockers"])


@pytest.mark.parametrize(
    "entry,expected",
    [
        (None, "missing"),
        ("wrong.module:run", "incompatible"),
        ("millrace.adapters.cli.main:cli", "declared"),
    ],
)
def test_actual_command_declaration_and_origin_are_independent(
    tmp_path, monkeypatch, entry, expected
):
    distribution(tmp_path, entry=entry)
    monkeypatch.syspath_prepend(str(tmp_path))
    observed = installed_component("millrace-ai")
    assert observed.command_disposition == expected
    assert observed.loaded_origin_disposition == "source_overlay"
    wire = SetupService().inspect().to_wire()
    codes = {b["code"] for b in wire["blockers"]}
    assert f"command_declaration_{expected}" in codes
    assert "command_origin_source_overlay" in codes
    assert "demo_unavailable" in codes
    assert {c["id"] for c in wire["checks"]} >= {
        "command.millrace.declaration",
        "command.millrace.origin",
    }
