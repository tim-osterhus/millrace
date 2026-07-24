from __future__ import annotations

import builtins
from collections.abc import Iterable
from typing import Any, cast

import pytest

from millrace.compiler.workflow_package_manifest import (
    validate_workflow_package_manifest,
)
from millrace.contracts import Diagnostic

ManifestSource = dict[str, object]
Record = dict[str, object]

DIGEST = "sha256:" + ("1" * 64)


def _minimal_manifest() -> ManifestSource:
    return {
        "record_kind": "millrace.workflow_package_manifest",
        "manifest_format_version": "1",
        "package": {
            "package_id": "pkg.example.diagnostic",
            "package_version": "1.0.0",
            "package_format_version": "1",
            "package_role": "workflow_package",
            "publisher": "Example",
            "base_millrace_compatibility": ">=0.22,<0.23",
        },
        "workflows": [
            {
                "workflow_id": "wf.echo_check",
                "workflow_version": "1",
                "visibility": "test_only",
                "entrypoints": ["default"],
                "source_refs": ["evil_package.workflow:manifest"],
                "selected_authority": {
                    "graphs": ["graph.echo"],
                    "stage_kinds": ["stage.receive", "stage.respond"],
                    "terminal_outcomes": ["outcome.accepted"],
                    "terminal_actions": ["action.close"],
                },
                "required_assets": [
                    {"asset_id": "asset.echo_prompt", "content_digest": DIGEST}
                ],
            }
        ],
        "assets": [
            {
                "asset_id": "asset.echo_prompt",
                "asset_kind": "entrypoint_prompt",
                "media_type": "text/markdown; charset=utf-8",
                "encoding": "utf-8",
                "content_digest": DIGEST,
                "byte_length": 12,
                "package_path": "prompts/echo.md",
                "selection": "required",
                "selected_authority_participation": "yes",
            }
        ],
        "dependencies": [],
        "compatibility": {"base_millrace": ">=0.22,<0.23"},
        "canonicalization": {"algorithm": "millrace-json-v1", "hash": "sha256"},
        "manifest_digest": None,
        "non_authoritative_metadata": {},
    }


def _workflow(source: ManifestSource) -> Record:
    return cast(Record, cast(list[object], source["workflows"])[0])


def _selected_authority(source: ManifestSource) -> Record:
    return cast(Record, _workflow(source)["selected_authority"])


def _errors(source: ManifestSource) -> tuple[Diagnostic, ...]:
    result = validate_workflow_package_manifest(source)
    assert result.manifest is None
    return result.diagnostics


def _find_error(errors: Iterable[Diagnostic], code: str) -> Diagnostic:
    matches = [diagnostic for diagnostic in errors if diagnostic.code == code]
    assert matches, f"missing diagnostic code {code!r} in {tuple(errors)!r}"
    return matches[0]


def test_workflow_package_manifest_refuses_hidden_default_authority() -> None:
    source = _minimal_manifest()
    _selected_authority(source)["hidden_defaults"] = ["base_workflow"]

    error = _find_error(_errors(source), "hidden_default_authority")

    assert error.declaration_path == "workflows[0].selected_authority.hidden_defaults"


def test_workflow_package_manifest_refuses_provider_credentials() -> None:
    source = _minimal_manifest()
    _selected_authority(source)["effect_declarations"] = [
        {
            "effect_declaration_id": "effect.email",
            "provider_ref": "provider.email",
            "provider_credentials": {"api_key": "secret"},
        }
    ]

    error = _find_error(_errors(source), "provider_credentials")

    assert (
        error.declaration_path
        == "workflows[0].selected_authority.effect_declarations[0].provider_credentials"
    )


def test_workflow_package_manifest_refuses_native_runner_implementations() -> None:
    source = _minimal_manifest()
    _selected_authority(source)["runner_bindings"] = [
        {
            "runner_binding_id": "runner.native",
            "native_runner_implementation": {"python_module": "evil_package.runner"},
        }
    ]

    error = _find_error(_errors(source), "native_runner_implementation")

    expected_path = (
        "workflows[0].selected_authority.runner_bindings[0]"
        ".native_runner_implementation"
    )
    assert (
        error.declaration_path
        == expected_path
    )


def test_workflow_package_manifest_refuses_plugin_or_mcp_execution_claims() -> None:
    plugin_source = _minimal_manifest()
    _selected_authority(plugin_source)["plugin_execution"] = {
        "module": "evil_package.plugin"
    }
    mcp_source = _minimal_manifest()
    _selected_authority(mcp_source)["mcp_execution"] = {"server": "evil"}

    plugin_error = _find_error(_errors(plugin_source), "plugin_execution_claim")
    mcp_error = _find_error(_errors(mcp_source), "mcp_execution_claim")

    assert plugin_error.declaration_path.endswith(".plugin_execution")
    assert mcp_error.declaration_path.endswith(".mcp_execution")


def test_workflow_package_manifest_refuses_provider_code_distribution() -> None:
    source = _minimal_manifest()
    _selected_authority(source)["effect_declarations"] = [
        {
            "effect_declaration_id": "effect.email",
            "provider_ref": "provider.email",
            "provider_code_distribution": "evil_package.provider",
        }
    ]

    error = _find_error(_errors(source), "provider_code_distribution")

    expected_path = (
        "workflows[0].selected_authority.effect_declarations[0]"
        ".provider_code_distribution"
    )
    assert (
        error.declaration_path
        == expected_path
    )


@pytest.mark.parametrize(
    ("mutate", "path", "actual_type"),
    [
        (
            lambda source: cast(Record, source["compatibility"]).__setitem__(
                "base_millrace",
                0.22,
            ),
            "compatibility.base_millrace",
            "float",
        ),
        (
            lambda source: cast(Record, source["package"]).__setitem__(
                "display",
                {("not", "text"): "Package"},
            ),
            "package.display.<non_string_key>",
            "tuple",
        ),
        (
            lambda source: _selected_authority(source).__setitem__(
                "opaque",
                object(),
            ),
            "workflows[0].selected_authority.opaque",
            "object",
        ),
        (
            lambda source: source.__setitem__(
                "non_authoritative_metadata",
                {"local": object()},
            ),
            "non_authoritative_metadata.local",
            "object",
        ),
    ],
)
def test_workflow_package_manifest_returns_diagnostics_for_invalid_nested_values(
    mutate: object,
    path: str,
    actual_type: str,
) -> None:
    source = _minimal_manifest()
    cast(Any, mutate)(source)

    error = _find_error(_errors(source), "invalid_manifest_value")

    assert error.declaration_path == path
    assert error.context["actual_type"] == actual_type


def test_workflow_package_manifest_refuses_runtime_code_execution() -> None:
    source = _minimal_manifest()
    _selected_authority(source)["runtime_code_execution"] = {
        "module": "evil_package.runtime"
    }

    error = _find_error(_errors(source), "runtime_code_execution_claim")

    assert error.declaration_path.endswith(".runtime_code_execution")


def test_workflow_package_manifest_refuses_marketplace_or_remote_install() -> None:
    source = _minimal_manifest()
    _selected_authority(source)["marketplace_install"] = {
        "remote_url": "https://example.invalid/pkg"
    }

    error = _find_error(_errors(source), "marketplace_install_claim")

    assert error.declaration_path.endswith(".marketplace_install")


def test_workflow_package_manifest_refuses_undeclared_substrate_mutation() -> None:
    source = _minimal_manifest()
    _selected_authority(source)["undeclared_substrate_mutation"] = {
        "target": "registry"
    }

    error = _find_error(_errors(source), "substrate_mutation_claim")

    assert error.declaration_path.endswith(".undeclared_substrate_mutation")


def test_workflow_package_manifest_refuses_package_granted_capabilities() -> None:
    source = _minimal_manifest()
    _selected_authority(source)["package_granted_capability"] = "runner.invoke"

    error = _find_error(_errors(source), "package_granted_capability")

    assert error.declaration_path.endswith(".package_granted_capability")


def test_workflow_package_manifest_refuses_package_granted_approvals() -> None:
    source = _minimal_manifest()
    _selected_authority(source)["package_granted_approval"] = "operator.approve"

    error = _find_error(_errors(source), "package_granted_approval")

    assert error.declaration_path.endswith(".package_granted_approval")


def test_workflow_package_manifest_allows_declared_policy_refs_as_data() -> None:
    source = _minimal_manifest()
    _selected_authority(source)["effect_declarations"] = [
        {
            "effect_declaration_id": "effect.email",
            "provider_ref": "provider.email",
            "capability_policy_ref": "capability.email.send",
        }
    ]
    _selected_authority(source)["approval_policies"] = [
        {"approval_policy_ref": "approval.operator_review"}
    ]

    result = validate_workflow_package_manifest(source)

    assert result.diagnostics == ()
    assert result.manifest is not None


def test_workflow_package_manifest_validation_does_not_import_package_code_or_workflow_fixtures(  # noqa: E501
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _minimal_manifest()
    cast(Record, source["package"])["source_ref"] = "evil_package.workflow"
    _workflow(source)["source_refs"] = [
        "evil_package.workflow:WORKFLOW_SOURCE",
        "millrace.workflows.kernel_ping:WORKFLOW_SOURCE",
    ]
    real_import = builtins.__import__

    def guarded_import(
        name: str,
        globals: object = None,
        locals: object = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "evil_package" or name.startswith("evil_package."):
            raise AssertionError(f"unexpected package code import: {name}")
        if name == "millrace.workflows" or name.startswith("millrace.workflows."):
            raise AssertionError(f"unexpected workflow fixture import: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", cast(Any, guarded_import))

    result = validate_workflow_package_manifest(source)

    assert result.diagnostics == ()
    assert result.manifest is not None
