from __future__ import annotations

import builtins
from collections.abc import Mapping
from dataclasses import replace
from inspect import Parameter, signature
from pathlib import Path
from typing import cast

import pytest

from millrace.contracts.compiled_plan import (
    ArtifactSchemaDeclaration,
    AssetDeclaration,
    AuthorityValue,
    SelectedCompiledPlan,
    SelectedWorkflowPackageAssetPin,
    SelectedWorkflowPackagePin,
    WorkflowIdentity,
)
from millrace.contracts.ids import (
    ArtifactSchemaId,
    AssetId,
    WorkflowId,
    WorkflowVersion,
)
from millrace.contracts.runner import RunnerDispatchEnvelope
from millrace.contracts.workflow_package import asset_digest_for_bytes


def _build_selected_asset_material(
    *,
    selected_plan: SelectedCompiledPlan,
    dispatch_envelope: RunnerDispatchEnvelope,
) -> Mapping[str, AuthorityValue]:
    from millrace.operator.prompt_material import build_selected_asset_material

    return build_selected_asset_material(
        selected_plan=selected_plan,
        dispatch_envelope=dispatch_envelope,
    )


def _materialization_error() -> type[Exception]:
    from millrace.operator.prompt_material import SelectedAssetMaterializationError

    return SelectedAssetMaterializationError


def _asset(asset_id: str, asset_kind: str, body: object) -> AssetDeclaration:
    return AssetDeclaration(
        id=AssetId(asset_id),
        asset_kind=asset_kind,
        body=cast(str, body),
        presentation={
            "display_name": f"Asset {asset_id}",
            "package_path": f"/package/{asset_id}.md",
        },
    )


def _schema(schema_id: str) -> ArtifactSchemaDeclaration:
    return ArtifactSchemaDeclaration(
        id=ArtifactSchemaId(schema_id),
        schema={"type": "object"},
        presentation={"display_name": f"Schema {schema_id}"},
    )


def _plan(
    *,
    assets: tuple[AssetDeclaration, ...],
    artifact_schemas: tuple[ArtifactSchemaDeclaration, ...] = (),
    workflow_package_pin: SelectedWorkflowPackagePin | None = None,
) -> SelectedCompiledPlan:
    return SelectedCompiledPlan(
        workflow=WorkflowIdentity(
            workflow_id=WorkflowId("wf.prompt_material"),
            workflow_version=WorkflowVersion("1"),
            workflow_name="Prompt Material Test Workflow",
        ),
        compatibility_profile=None,
        required_extensions=(),
        graphs=(),
        partitions=(),
        queue_families=(),
        external_enqueue_routes=(),
        generated_work_routes=(),
        artifact_schemas=artifact_schemas,
        assets=assets,
        stage_kinds=(),
        terminal_outcomes=(),
        terminal_actions=(),
        recovery_policies=(),
        runner_bindings=(),
        workflow_package_pin=workflow_package_pin,
    )


def _dispatch(
    *,
    entrypoint_asset_id: str | None = "asset.entrypoint",
    skill_asset_ids: tuple[str, ...] = ("asset.stage_skill",),
    artifact_schema_ids: tuple[str, ...] = ("schema.result",),
    work_item_payload: Mapping[str, AuthorityValue] | None = None,
    governance_context: Mapping[str, AuthorityValue] | None = None,
    terminal_options: tuple[Mapping[str, AuthorityValue], ...] | None = None,
) -> RunnerDispatchEnvelope:
    return RunnerDispatchEnvelope(
        run_id="run-1",
        session_id="session-1",
        dispatch_generation=1,
        session_fencing_token="session-fence-1",
        work_item_id="work-1",
        activation_id="activation-1",
        plan_fingerprint=f"sha256:{'1' * 64}",
        plan_id="wf.prompt_material:1",
        workflow_id="wf.prompt_material",
        workflow_version="1",
        graph_id="wf.prompt_material.graph",
        claim_id="claim-1",
        generation=0,
        fencing_token="fence-1",
        queue_family_id="prompt",
        stage_kind_id="wf.prompt_material.stage",
        graph_node_id="wf.prompt_material.node",
        runner_binding_id="wf.prompt_material.codex",
        external_enqueue_route_id="wf.prompt_material.external",
        entrypoint_asset_id=entrypoint_asset_id,
        skill_asset_ids=skill_asset_ids,
        artifact_schema_ids=artifact_schema_ids,
        work_item_payload=work_item_payload or {"body": "operator prompt"},
        governance_context=governance_context or {"source": "dispatch"},
        terminal_options=terminal_options
        or (
            {
                "outcome_id": "wf.prompt_material.complete",
                "marker": "TASK_COMPLETE",
                "action_id": "wf.prompt_material.route",
                "action_kind": "route",
                "artifact_schema_id": "schema.result",
            },
        ),
    )


def _pin(asset_id: str, body: str) -> SelectedWorkflowPackageAssetPin:
    return SelectedWorkflowPackageAssetPin(
        asset_id=asset_id,
        content_digest=asset_digest_for_bytes(body.encode("utf-8")),
    )


def _package_pin(
    pins: tuple[SelectedWorkflowPackageAssetPin, ...],
) -> SelectedWorkflowPackagePin:
    return SelectedWorkflowPackagePin(
        package_id="pkg.prompt_material",
        package_version="1.0.0",
        package_format_version="1",
        workflow_id="wf.prompt_material",
        workflow_version="1",
        entrypoint="default",
        selected_asset_pins=pins,
        selected_dependency_pins=(),
    )


def _corrupt_package_pin(
    pins: tuple[SelectedWorkflowPackageAssetPin, ...],
) -> SelectedWorkflowPackagePin:
    pin = object.__new__(SelectedWorkflowPackagePin)
    object.__setattr__(pin, "package_id", "pkg.prompt_material")
    object.__setattr__(pin, "package_version", "1.0.0")
    object.__setattr__(pin, "package_format_version", "1")
    object.__setattr__(pin, "workflow_id", "wf.prompt_material")
    object.__setattr__(pin, "workflow_version", "1")
    object.__setattr__(pin, "entrypoint", "default")
    object.__setattr__(pin, "selected_asset_pins", pins)
    object.__setattr__(pin, "selected_dependency_pins", ())
    return pin


def test_materializes_only_dispatch_entrypoint_and_skill_assets() -> None:
    selected_plan = _plan(
        assets=(
            _asset("asset.entrypoint", "prompt", "Entrypoint prompt"),
            _asset("asset.stage_skill", "skill", "Stage skill"),
            _asset("asset.unused_prompt", "prompt", "Unused prompt"),
            _asset("asset.unused_skill", "skill", "Unused skill"),
        ),
        artifact_schemas=(_schema("schema.result"),),
    )

    material = _build_selected_asset_material(
        selected_plan=selected_plan,
        dispatch_envelope=_dispatch(),
    )

    assert set(material) == {"asset.entrypoint", "asset.stage_skill"}
    assert dict(cast(Mapping[str, AuthorityValue], material["asset.entrypoint"])) == {
        "asset_id": "asset.entrypoint",
        "asset_kind": "prompt",
        "body": "Entrypoint prompt",
        "content_digest": None,
        "source": "selected_plan_inline",
    }
    assert dict(cast(Mapping[str, AuthorityValue], material["asset.stage_skill"])) == {
        "asset_id": "asset.stage_skill",
        "asset_kind": "skill",
        "body": "Stage skill",
        "content_digest": None,
        "source": "selected_plan_inline",
    }
    with pytest.raises(TypeError):
        cast(dict[str, AuthorityValue], material)["asset.extra"] = "not frozen"


def test_materializes_package_entrypoint_prompt_and_stage_skill_assets() -> None:
    entrypoint_body = "Package entrypoint prompt"
    skill_body = "Package stage skill"
    selected_plan = _plan(
        assets=(
            _asset("asset.entrypoint", "entrypoint_prompt", entrypoint_body),
            _asset("asset.stage_skill", "stage_skill", skill_body),
            _asset("asset.unused_shared", "shared_skill", "Shared but unused"),
        ),
        artifact_schemas=(_schema("schema.result"),),
        workflow_package_pin=_package_pin(
            (
                _pin("asset.entrypoint", entrypoint_body),
                _pin("asset.stage_skill", skill_body),
                _pin("asset.unused_shared", "Shared but unused"),
            )
        ),
    )

    material = _build_selected_asset_material(
        selected_plan=selected_plan,
        dispatch_envelope=_dispatch(),
    )

    assert set(material) == {"asset.entrypoint", "asset.stage_skill"}
    entrypoint = cast(Mapping[str, AuthorityValue], material["asset.entrypoint"])
    skill = cast(Mapping[str, AuthorityValue], material["asset.stage_skill"])
    assert entrypoint["asset_kind"] == "entrypoint_prompt"
    assert entrypoint["body"] == entrypoint_body
    assert entrypoint["content_digest"] == asset_digest_for_bytes(
        entrypoint_body.encode("utf-8")
    )
    assert entrypoint["source"] == "selected_package_pin"
    assert skill["asset_kind"] == "stage_skill"
    assert skill["body"] == skill_body
    assert skill["source"] == "selected_package_pin"
    assert "asset.unused_shared" not in material


@pytest.mark.parametrize(
    ("entrypoint_kind", "skill_kind", "expected_asset_id"),
    (
        ("entrypoint_prompt", "skill", "asset.entrypoint"),
        ("prompt", "stage_skill", "asset.stage_skill"),
    ),
)
def test_source_mode_refuses_package_asset_kinds_for_dispatch_material(
    entrypoint_kind: str,
    skill_kind: str,
    expected_asset_id: str,
) -> None:
    selected_plan = _plan(
        assets=(
            _asset("asset.entrypoint", entrypoint_kind, "Entrypoint body"),
            _asset("asset.stage_skill", skill_kind, "Skill body"),
        )
    )

    with pytest.raises(
        _materialization_error(),
        match=f"asset kind .* is not valid .*{expected_asset_id}",
    ):
        _build_selected_asset_material(
            selected_plan=selected_plan,
            dispatch_envelope=_dispatch(),
        )


@pytest.mark.parametrize(
    ("entrypoint_kind", "skill_kind", "expected_asset_id"),
    (
        ("prompt", "stage_skill", "asset.entrypoint"),
        ("entrypoint_prompt", "skill", "asset.stage_skill"),
    ),
)
def test_package_mode_refuses_source_asset_kinds_for_dispatch_material(
    entrypoint_kind: str,
    skill_kind: str,
    expected_asset_id: str,
) -> None:
    entrypoint_body = "Entrypoint body"
    skill_body = "Skill body"
    selected_plan = _plan(
        assets=(
            _asset("asset.entrypoint", entrypoint_kind, entrypoint_body),
            _asset("asset.stage_skill", skill_kind, skill_body),
        ),
        workflow_package_pin=_package_pin(
            (
                _pin("asset.entrypoint", entrypoint_body),
                _pin("asset.stage_skill", skill_body),
            )
        ),
    )

    with pytest.raises(
        _materialization_error(),
        match=f"asset kind .* is not valid .*{expected_asset_id}",
    ):
        _build_selected_asset_material(
            selected_plan=selected_plan,
            dispatch_envelope=_dispatch(),
        )


def test_materialization_refuses_missing_selected_asset_declaration() -> None:
    selected_plan = _plan(assets=(_asset("asset.stage_skill", "skill", "Skill"),))

    with pytest.raises(
        _materialization_error(),
        match="missing selected asset declaration.*asset.entrypoint",
    ):
        _build_selected_asset_material(
            selected_plan=selected_plan,
            dispatch_envelope=_dispatch(),
        )


def test_materialization_refuses_duplicate_referenced_asset_declaration() -> None:
    selected_plan = _plan(
        assets=(
            _asset("asset.entrypoint", "prompt", "First prompt"),
            _asset("asset.entrypoint", "prompt", "Second prompt"),
            _asset("asset.stage_skill", "skill", "Skill"),
        )
    )

    with pytest.raises(
        _materialization_error(),
        match="duplicate selected asset declaration.*asset.entrypoint",
    ):
        _build_selected_asset_material(
            selected_plan=selected_plan,
            dispatch_envelope=_dispatch(),
        )


@pytest.mark.parametrize(
    ("entrypoint_kind", "skill_kind", "expected_asset_id"),
    (
        ("skill", "skill", "asset.entrypoint"),
        ("stage_skill", "skill", "asset.entrypoint"),
        ("prompt", "prompt", "asset.stage_skill"),
        ("prompt", "entrypoint_prompt", "asset.stage_skill"),
        ("prompt", "shared_skill", "asset.stage_skill"),
        ("prompt", "schema", "asset.stage_skill"),
        ("prompt", "fixture", "asset.stage_skill"),
    ),
)
def test_materialization_refuses_role_kind_mismatch_and_shared_skill(
    entrypoint_kind: str,
    skill_kind: str,
    expected_asset_id: str,
) -> None:
    selected_plan = _plan(
        assets=(
            _asset("asset.entrypoint", entrypoint_kind, "Entrypoint body"),
            _asset("asset.stage_skill", skill_kind, "Skill body"),
        )
    )

    with pytest.raises(
        _materialization_error(),
        match=f"asset kind .* is not valid .*{expected_asset_id}",
    ):
        _build_selected_asset_material(
            selected_plan=selected_plan,
            dispatch_envelope=_dispatch(),
        )


@pytest.mark.parametrize(
    ("body", "expected_reason"),
    (
        ("   ", "blank"),
        (123, "not text"),
    ),
)
def test_materialization_refuses_malformed_selected_bodies(
    body: object,
    expected_reason: str,
) -> None:
    selected_plan = _plan(
        assets=(
            _asset("asset.entrypoint", "prompt", body),
            _asset("asset.stage_skill", "skill", "Skill body"),
        )
    )

    with pytest.raises(
        _materialization_error(),
        match=f"selected asset body is {expected_reason}.*asset.entrypoint",
    ):
        _build_selected_asset_material(
            selected_plan=selected_plan,
            dispatch_envelope=_dispatch(),
        )


def test_materialization_from_package_pin_verifies_selected_body_digest() -> None:
    selected_body = "Selected package body"
    selected_plan = _plan(
        assets=(
            _asset("asset.entrypoint", "entrypoint_prompt", selected_body),
            _asset("asset.stage_skill", "stage_skill", "Stage skill"),
        ),
        workflow_package_pin=_package_pin(
            (
                _pin("asset.entrypoint", selected_body),
                _pin("asset.stage_skill", "Stage skill"),
            )
        ),
    )

    material = _build_selected_asset_material(
        selected_plan=selected_plan,
        dispatch_envelope=_dispatch(),
    )

    entrypoint = cast(Mapping[str, AuthorityValue], material["asset.entrypoint"])
    assert entrypoint["body"] == selected_body
    assert entrypoint["content_digest"] == asset_digest_for_bytes(
        selected_body.encode("utf-8")
    )
    assert "cas_digest" not in entrypoint


def test_materialization_refuses_missing_pin_duplicate_pin_and_digest_mismatch(
) -> None:
    selected_plan = _plan(
        assets=(
            _asset("asset.entrypoint", "entrypoint_prompt", "Prompt body"),
            _asset("asset.stage_skill", "stage_skill", "Skill body"),
        ),
        workflow_package_pin=_package_pin((_pin("asset.entrypoint", "Prompt body"),)),
    )
    with pytest.raises(
        _materialization_error(),
        match="missing selected package asset pin.*asset.stage_skill",
    ):
        _build_selected_asset_material(
            selected_plan=selected_plan,
            dispatch_envelope=_dispatch(),
        )

    duplicate_pin = _pin("asset.entrypoint", "Prompt body")
    selected_plan = replace(
        selected_plan,
        workflow_package_pin=_corrupt_package_pin((duplicate_pin, duplicate_pin)),
    )
    with pytest.raises(
        _materialization_error(),
        match="duplicate selected package asset pin.*asset.entrypoint",
    ):
        _build_selected_asset_material(
            selected_plan=selected_plan,
            dispatch_envelope=_dispatch(skill_asset_ids=()),
        )

    selected_plan = replace(
        selected_plan,
        workflow_package_pin=_package_pin(
            (
                SelectedWorkflowPackageAssetPin(
                    asset_id="asset.entrypoint",
                    content_digest=asset_digest_for_bytes(b"old package body"),
                ),
            )
        ),
    )
    with pytest.raises(
        _materialization_error(),
        match="selected package asset digest mismatch.*asset.entrypoint",
    ):
        _build_selected_asset_material(
            selected_plan=selected_plan,
            dispatch_envelope=_dispatch(skill_asset_ids=()),
        )


def test_materialization_does_not_read_cas_registry_package_or_workspace_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_plan = _plan(
        assets=(
            _asset("asset.entrypoint", "prompt", "Prompt body"),
            _asset("asset.stage_skill", "skill", "Skill body"),
        )
    )
    dispatch = _dispatch(
        work_item_payload={"workspace_path": "/tmp/workspace/prompt.md"},
        governance_context={"package_path": "/tmp/package/prompts/main.md"},
    )

    def fail_file_access(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("materializer must not read package or workspace files")

    monkeypatch.setattr(builtins, "open", fail_file_access)
    monkeypatch.setattr(Path, "open", fail_file_access)
    monkeypatch.setattr(Path, "read_text", fail_file_access)
    monkeypatch.setattr(Path, "read_bytes", fail_file_access)

    material = _build_selected_asset_material(
        selected_plan=selected_plan,
        dispatch_envelope=dispatch,
    )

    assert cast(Mapping[str, AuthorityValue], material["asset.entrypoint"])[
        "body"
    ] == "Prompt body"


def test_materialization_refuses_extra_or_external_material() -> None:
    from millrace.operator.prompt_material import build_selected_asset_material

    parameters = signature(build_selected_asset_material).parameters
    assert list(parameters) == ["selected_plan", "dispatch_envelope"]
    assert all(
        parameter.kind is Parameter.KEYWORD_ONLY
        for parameter in parameters.values()
    )

    selected_plan = _plan(
        assets=(
            _asset("asset.entrypoint", "prompt", "Prompt body"),
            _asset("asset.stage_skill", "skill", "Skill body"),
        )
    )
    dispatch = _dispatch()

    with pytest.raises(TypeError):
        build_selected_asset_material(
            selected_plan=selected_plan,
            dispatch_envelope=dispatch,
            selected_asset_material={"asset.external": {"body": "external"}},
        )
    with pytest.raises(TypeError):
        build_selected_asset_material(
            selected_plan=selected_plan,
            dispatch_envelope=dispatch,
            asset_ids=("asset.entrypoint", "asset.external"),
        )
    with pytest.raises(TypeError):
        build_selected_asset_material(
            selected_plan=selected_plan,
            dispatch_envelope=dispatch,
            package_path="/tmp/package",
        )


def test_materialization_allows_null_entrypoint_ref_but_only_materializes_skills(
) -> None:
    selected_plan = _plan(
        assets=(
            _asset("asset.entrypoint", "prompt", "Prompt body"),
            _asset("asset.stage_skill", "skill", "Skill body"),
        )
    )

    material = _build_selected_asset_material(
        selected_plan=selected_plan,
        dispatch_envelope=_dispatch(entrypoint_asset_id=None),
    )

    assert set(material) == {"asset.stage_skill"}
    assert cast(Mapping[str, AuthorityValue], material["asset.stage_skill"])[
        "body"
    ] == "Skill body"


def test_materialization_preserves_terminal_options_as_dispatch_authority_only(
) -> None:
    prompt_text = (
        "This prose mentions MADE_UP_MARKER and action_id=evil, but it is only "
        "selected prompt material."
    )
    selected_plan = _plan(
        assets=(
            _asset("asset.entrypoint", "prompt", prompt_text),
            _asset("asset.stage_skill", "skill", "Skill body"),
        ),
        artifact_schemas=(_schema("schema.result"),),
    )

    material = _build_selected_asset_material(
        selected_plan=selected_plan,
        dispatch_envelope=_dispatch(
            terminal_options=(
                {
                    "outcome_id": "wf.prompt_material.complete",
                    "marker": "TASK_COMPLETE",
                    "action_id": "wf.prompt_material.route",
                    "action_kind": "route",
                    "artifact_schema_id": "schema.result",
                },
            )
        ),
    )

    entrypoint = cast(Mapping[str, AuthorityValue], material["asset.entrypoint"])
    assert entrypoint["body"] == prompt_text
    assert set(entrypoint) == {
        "asset_id",
        "asset_kind",
        "body",
        "content_digest",
        "source",
    }
    assert "terminal_options" not in entrypoint
    assert "artifact_schema_ids" not in entrypoint
    assert "action_id" not in entrypoint
    assert "marker" not in entrypoint
