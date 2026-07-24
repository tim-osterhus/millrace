from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any, cast

import pytest

from millrace.contracts.compiled_plan import AuthorityValue
from millrace.contracts.runner import (
    RunnerAdapterProvenance,
    RunnerDispatchEnvelope,
    RunnerResultEvidence,
)


def _valid_dispatch_envelope(**overrides: object) -> RunnerDispatchEnvelope:
    kwargs: dict[str, object] = {
        "run_id": "run-1",
        "work_item_id": "work-1",
        "activation_id": "activation-1",
        "plan_fingerprint": "sha256:abcdef",
        "plan_id": "kernel_ping:0.1",
        "workflow_id": "kernel_ping",
        "workflow_version": "0.1",
        "graph_id": "kernel_ping.graph",
        "claim_id": "claim-1",
        "generation": 0,
        "fencing_token": "fence-1",
        "queue_family_id": "prompt",
        "stage_kind_id": "kernel_ping.taskmaster",
        "graph_node_id": "kernel_ping.start",
        "runner_binding_id": "kernel_ping.fake_local_runner",
        "external_enqueue_route_id": "kernel_ping.external_prompt",
        "entrypoint_asset_id": "kernel_ping.taskmaster_prompt",
        "skill_asset_ids": ("kernel_ping.tdd_core",),
        "artifact_schema_ids": ("kernel_ping.task_artifact",),
        "work_item_payload": {"body": "proof it out"},
        "governance_context": {},
        "terminal_options": (
            {
                "outcome_id": "kernel_ping.taskmaster.complete",
                "marker": "TASK_COMPLETE",
                "action_id": "kernel_ping.taskmaster.emit_task",
                "action_kind": "route",
                "artifact_schema_id": "kernel_ping.task_artifact",
            },
        ),
    }
    kwargs.update(overrides)
    return RunnerDispatchEnvelope(
        run_id=cast(str, kwargs["run_id"]),
        work_item_id=cast(str, kwargs["work_item_id"]),
        activation_id=cast(str, kwargs["activation_id"]),
        plan_fingerprint=cast(str, kwargs["plan_fingerprint"]),
        plan_id=cast(str, kwargs["plan_id"]),
        workflow_id=cast(str, kwargs["workflow_id"]),
        workflow_version=cast(str, kwargs["workflow_version"]),
        graph_id=cast(str, kwargs["graph_id"]),
        claim_id=cast(str, kwargs["claim_id"]),
        generation=cast(int, kwargs["generation"]),
        fencing_token=cast(str, kwargs["fencing_token"]),
        queue_family_id=cast(str, kwargs["queue_family_id"]),
        stage_kind_id=cast(str, kwargs["stage_kind_id"]),
        graph_node_id=cast(str, kwargs["graph_node_id"]),
        runner_binding_id=cast(str, kwargs["runner_binding_id"]),
        external_enqueue_route_id=cast(
            str | None,
            kwargs["external_enqueue_route_id"],
        ),
        entrypoint_asset_id=cast(str | None, kwargs["entrypoint_asset_id"]),
        skill_asset_ids=cast(tuple[str, ...], kwargs["skill_asset_ids"]),
        artifact_schema_ids=cast(tuple[str, ...], kwargs["artifact_schema_ids"]),
        work_item_payload=cast(
            dict[str, AuthorityValue],
            kwargs["work_item_payload"],
        ),
        governance_context=cast(
            dict[str, AuthorityValue],
            kwargs["governance_context"],
        ),
        terminal_options=cast(
            tuple[dict[str, AuthorityValue], ...],
            kwargs["terminal_options"],
        ),
    )


def test_adapter_invocation_request_freezes_selected_runner_authority_projection() -> (
    None
):
    from millrace.adapters.runner_contract import (
        AdapterInvocationRequest,
        RedactionPolicy,
    )
    from millrace.contracts.compiled_plan import (
        ArtifactSchemaDeclaration,
        RunnerComponentPin,
        RunnerTerminalResultMapping,
    )
    from millrace.contracts.ids import (
        ArtifactSchemaId,
        CapabilityId,
        OutcomeId,
        StageKindId,
    )

    dispatch = _valid_dispatch_envelope()
    pin = RunnerComponentPin(
        component_kind="runner",
        component_id="millforge-base",
        component_version="1",
        provider_distribution="millforge",
        provider_version="0.1.0",
        descriptor_media_type="application/json",
        descriptor_sha256="f705d4e0f2c09d8435213a86d4b0909eb08737875a3e1a236fc77a68f88dce05",
        required_capability_ids=(CapabilityId("terminal.intent"),),
        legal_terminal_result_ids=("COMPLETE",),
    )
    mappings = [
        RunnerTerminalResultMapping(
            stage_kind_id=StageKindId(dispatch.stage_kind_id),
            runner_result_id="COMPLETE",
            outcome_id=OutcomeId("kernel_ping.taskmaster.complete"),
        )
    ]
    schemas = [
        ArtifactSchemaDeclaration(
            id=ArtifactSchemaId("kernel_ping.task_artifact"),
            schema={"type": "object", "properties": {}},
            presentation={},
        )
    ]

    request = AdapterInvocationRequest(
        adapter_id="millforge-offline",
        selected_runner_binding_id=dispatch.runner_binding_id,
        selected_adapter_kind="millforge",
        dispatch_envelope=dispatch,
        timeout_seconds=30,
        correlation_id="corr-1",
        redaction_policy=RedactionPolicy(policy_id="redact-default"),
        selected_component_pin=pin,
        selected_terminal_result_mappings=mappings,
        selected_artifact_schemas=schemas,
    )
    mappings.clear()
    schemas.clear()

    assert request.selected_component_pin == pin
    assert request.selected_terminal_result_mappings[0].runner_result_id == "COMPLETE"
    assert request.selected_artifact_schemas[0].id == ArtifactSchemaId(
        "kernel_ping.task_artifact"
    )
    assert isinstance(request.selected_terminal_result_mappings, tuple)
    assert isinstance(request.selected_artifact_schemas, tuple)
    mapping = request.selected_terminal_result_mappings[0]
    schema = request.selected_artifact_schemas[0]

    def construct(
        *,
        current_dispatch: RunnerDispatchEnvelope = dispatch,
        current_mappings: object = (mapping,),
        current_schemas: object = (schema,),
    ) -> AdapterInvocationRequest:
        return AdapterInvocationRequest(
            adapter_id="millforge-offline",
            selected_runner_binding_id=current_dispatch.runner_binding_id,
            selected_adapter_kind="millforge",
            dispatch_envelope=current_dispatch,
            timeout_seconds=30,
            correlation_id="corr-1",
            redaction_policy=RedactionPolicy(policy_id="redact-default"),
            selected_component_pin=pin,
            selected_terminal_result_mappings=cast(
                tuple[RunnerTerminalResultMapping, ...],
                current_mappings,
            ),
            selected_artifact_schemas=cast(
                tuple[ArtifactSchemaDeclaration, ...],
                current_schemas,
            ),
        )

    alternate_dispatch = _valid_dispatch_envelope(
        artifact_schema_ids=("kernel_ping.task_artifact", "other_artifact"),
        terminal_options=(
            {
                "outcome_id": "kernel_ping.taskmaster.complete",
                "marker": "TASK_COMPLETE",
                "action_id": "kernel_ping.taskmaster.emit_task",
                "action_kind": "route",
                "artifact_schema_id": "kernel_ping.task_artifact",
            },
            {
                "outcome_id": "kernel_ping.taskmaster.blocked",
                "marker": "TASK_BLOCKED",
                "action_id": "kernel_ping.taskmaster.block_task",
                "action_kind": "route",
                "artifact_schema_id": "other_artifact",
            },
        ),
    )
    alternate_mapping = RunnerTerminalResultMapping(
        stage_kind_id=StageKindId(alternate_dispatch.stage_kind_id),
        runner_result_id="BLOCKED",
        outcome_id=OutcomeId("kernel_ping.taskmaster.blocked"),
    )
    alternate_schema = ArtifactSchemaDeclaration(
        id=ArtifactSchemaId("other_artifact"),
        schema={"type": "object", "properties": {}},
        presentation={},
    )

    with pytest.raises(ValueError, match="mapping stage"):
        construct(
            current_mappings=(
                RunnerTerminalResultMapping(
                    stage_kind_id=StageKindId("wrong.stage"),
                    runner_result_id="COMPLETE",
                    outcome_id=OutcomeId("kernel_ping.taskmaster.complete"),
                ),
            ),
        )
    with pytest.raises(ValueError, match="mapping outcome"):
        construct(
            current_mappings=(
                RunnerTerminalResultMapping(
                    stage_kind_id=StageKindId(dispatch.stage_kind_id),
                    runner_result_id="COMPLETE",
                    outcome_id=OutcomeId("missing.outcome"),
                ),
            ),
        )
    with pytest.raises(ValueError, match="duplicate mapping"):
        construct(
            current_dispatch=alternate_dispatch,
            current_mappings=(
                RunnerTerminalResultMapping(
                    stage_kind_id=StageKindId(alternate_dispatch.stage_kind_id),
                    runner_result_id="COMPLETE",
                    outcome_id=OutcomeId("kernel_ping.taskmaster.complete"),
                ),
                RunnerTerminalResultMapping(
                    stage_kind_id=StageKindId(alternate_dispatch.stage_kind_id),
                    runner_result_id="COMPLETE",
                    outcome_id=OutcomeId("kernel_ping.taskmaster.blocked"),
                ),
            ),
            current_schemas=(schema, alternate_schema),
        )
    with pytest.raises(ValueError, match="duplicate mapping"):
        construct(
            current_dispatch=alternate_dispatch,
            current_mappings=(
                RunnerTerminalResultMapping(
                    stage_kind_id=StageKindId(alternate_dispatch.stage_kind_id),
                    runner_result_id="COMPLETE",
                    outcome_id=OutcomeId("kernel_ping.taskmaster.complete"),
                ),
                RunnerTerminalResultMapping(
                    stage_kind_id=StageKindId(alternate_dispatch.stage_kind_id),
                    runner_result_id="BLOCKED",
                    outcome_id=OutcomeId("kernel_ping.taskmaster.complete"),
                ),
            ),
            current_schemas=(schema, alternate_schema),
        )
    with pytest.raises(ValueError, match="artifact schema"):
        construct(current_schemas=())
    with pytest.raises(ValueError, match="artifact schema"):
        construct(
            current_schemas=(
                schema,
                ArtifactSchemaDeclaration(
                    id=ArtifactSchemaId("kernel_ping.task_artifact"),
                    schema={"type": "object", "properties": {}},
                    presentation={},
                ),
            ),
        )
    with pytest.raises(ValueError, match="artifact schema"):
        construct(
            current_schemas=(
                ArtifactSchemaDeclaration(
                    id=ArtifactSchemaId("undeclared"),
                    schema={"type": "object", "properties": {}},
                    presentation={},
                ),
            ),
        )
    with pytest.raises(ValueError, match="artifact schema"):
        construct(
            current_dispatch=_valid_dispatch_envelope(
                artifact_schema_ids=(),
            ),
        )
    with pytest.raises(ValueError, match="artifact schema"):
        construct(
            current_dispatch=alternate_dispatch,
            current_mappings=(
                RunnerTerminalResultMapping(
                    stage_kind_id=StageKindId(alternate_dispatch.stage_kind_id),
                    runner_result_id="COMPLETE",
                    outcome_id=OutcomeId("kernel_ping.taskmaster.complete"),
                ),
                alternate_mapping,
            ),
            current_schemas=(schema,),
        )


class _FakeAdapter:
    adapter_kind = "fake_local"

    def invoke(self, request: object) -> object:
        return request


class _LocalSubprocessFakeAdapter:
    adapter_kind = "local_subprocess"

    def invoke(self, request: object) -> object:
        return request


def test_adapter_request_and_resolver_refuses_bad_values() -> None:
    from millrace.adapters.runner_contract import (
        AdapterInvocationRequest,
        AdapterLocalConfig,
        AdapterResolverError,
        RedactionPolicy,
        resolve_adapter,
    )

    dispatch = _valid_dispatch_envelope()
    config = AdapterLocalConfig(adapters={"fake_local": _FakeAdapter()})

    request = AdapterInvocationRequest(
        adapter_id="adapter-1",
        selected_runner_binding_id=dispatch.runner_binding_id,
        selected_adapter_kind="fake_local",
        dispatch_envelope=dispatch,
        timeout_seconds=30,
        correlation_id="corr-1",
        redaction_policy=RedactionPolicy(policy_id="redact-default"),
    )

    assert request.selected_adapter_kind == "fake_local"
    assert resolve_adapter("fake_local", config).adapter_kind == "fake_local"

    with pytest.raises(ValueError):
        AdapterInvocationRequest(
            adapter_id=" ",
            selected_runner_binding_id=dispatch.runner_binding_id,
            selected_adapter_kind="fake_local",
            dispatch_envelope=dispatch,
            timeout_seconds=30,
            correlation_id="corr-1",
            redaction_policy=RedactionPolicy(policy_id="redact-default"),
        )
    for nonfinite_timeout in (float("nan"), float("inf")):
        with pytest.raises(ValueError, match="finite"):
            AdapterInvocationRequest(
                adapter_id="adapter-1",
                selected_runner_binding_id=dispatch.runner_binding_id,
                selected_adapter_kind="fake_local",
                dispatch_envelope=dispatch,
                timeout_seconds=nonfinite_timeout,
                correlation_id="corr-1",
                redaction_policy=RedactionPolicy(policy_id="redact-default"),
            )
    with pytest.raises(ValueError):
        AdapterInvocationRequest(
            adapter_id="adapter-1",
            selected_runner_binding_id=dispatch.runner_binding_id,
            selected_adapter_kind="fake_local",
            dispatch_envelope=dispatch,
            timeout_seconds=0,
            correlation_id="corr-1",
            redaction_policy=RedactionPolicy(policy_id="redact-default"),
        )
    with pytest.raises(TypeError):
        AdapterInvocationRequest(
            adapter_id="adapter-1",
            selected_runner_binding_id=dispatch.runner_binding_id,
            selected_adapter_kind="fake_local",
            dispatch_envelope=cast(Any, object()),
            timeout_seconds=30,
            correlation_id="corr-1",
            redaction_policy=RedactionPolicy(policy_id="redact-default"),
        )
    with pytest.raises(ValueError):
        AdapterInvocationRequest(
            adapter_id="adapter-1",
            selected_runner_binding_id="wrong-runner",
            selected_adapter_kind="fake_local",
            dispatch_envelope=dispatch,
            timeout_seconds=30,
            correlation_id="corr-1",
            redaction_policy=RedactionPolicy(policy_id="redact-default"),
        )
    with pytest.raises(AdapterResolverError):
        resolve_adapter("missing", config)
    with pytest.raises(AdapterResolverError):
        resolve_adapter(
            "fake_local",
            AdapterLocalConfig(adapters={"fake_local": object()}),
        )
    with pytest.raises(AdapterResolverError):
        resolve_adapter(
            "fake_local",
            AdapterLocalConfig(
                adapters={
                    "fake_local": type(
                        "Other",
                        (),
                        {"adapter_kind": "codex"},
                    )(),
                },
            ),
        )
    with pytest.raises(AdapterResolverError):
        resolve_adapter(
            "local_subprocess",
            AdapterLocalConfig(
                adapters={"local_subprocess": _LocalSubprocessFakeAdapter()},
            ),
        )


def test_success_result_converts_only_after_dispatch_echo_matches() -> None:
    from millrace.adapters.runner_contract import (
        AdapterInvocationRequest,
        AdapterSuccessResult,
        DispatchEcho,
        RedactionPolicy,
        runner_evidence_from_adapter_outcome,
    )

    dispatch = _valid_dispatch_envelope()
    policy = RedactionPolicy(policy_id="redact-default")
    request = AdapterInvocationRequest(
        adapter_id="adapter-1",
        selected_runner_binding_id=dispatch.runner_binding_id,
        selected_adapter_kind="fake_local",
        dispatch_envelope=dispatch,
        timeout_seconds=30,
        correlation_id="corr-1",
        redaction_policy=policy,
    )
    success = AdapterSuccessResult.from_unredacted(
        adapter_id="adapter-1",
        dispatch_echo=DispatchEcho.from_dispatch_envelope(
            dispatch,
            correlation_id="corr-1",
        ),
        marker="UNSELECTED_BUT_STILL_EVIDENCE",
        observation_payload_candidate={"worker_summary": "ready"},
        artifact_payload_candidate={"artifact_kind": "kernel_ping.task_artifact"},
        captured_stdout="stdout",
        captured_stderr="stderr",
        structured_provider_response={"provider": "fake"},
        evidence_construction_diagnostics={"note": "diagnostic"},
        redaction_policy=policy,
    )

    evidence = runner_evidence_from_adapter_outcome(success, request)

    assert isinstance(evidence, RunnerResultEvidence)
    assert evidence.marker == "UNSELECTED_BUT_STILL_EVIDENCE"
    assert evidence.run_id == dispatch.run_id
    assert evidence.runner_binding_id == dispatch.runner_binding_id
    assert evidence.adapter_provenance is None
    assert not hasattr(evidence, "adapter_id")

    mismatched = AdapterSuccessResult.from_unredacted(
        adapter_id="adapter-1",
        dispatch_echo=DispatchEcho.from_dispatch_envelope(
            _valid_dispatch_envelope(run_id="run-2"),
            correlation_id="corr-1",
        ),
        marker="TASK_COMPLETE",
        redaction_policy=policy,
    )
    with pytest.raises(ValueError, match="dispatch echo"):
        runner_evidence_from_adapter_outcome(mismatched, request)

    wrong_correlation = AdapterSuccessResult.from_unredacted(
        adapter_id="adapter-1",
        dispatch_echo=DispatchEcho.from_dispatch_envelope(
            dispatch,
            correlation_id="corr-2",
        ),
        marker="TASK_COMPLETE",
        redaction_policy=policy,
    )
    with pytest.raises(ValueError, match="dispatch echo"):
        runner_evidence_from_adapter_outcome(wrong_correlation, request)


def _millforge_component_pin():
    from millrace.contracts.compiled_plan import RunnerComponentPin
    from millrace.contracts.ids import CapabilityId

    return RunnerComponentPin(
        component_kind="runner",
        component_id="millforge-base",
        component_version="1",
        provider_distribution="millforge",
        provider_version="0.1.0",
        descriptor_media_type="application/json",
        descriptor_sha256="a" * 64,
        required_capability_ids=(CapabilityId("terminal.intent"),),
        legal_terminal_result_ids=("COMPLETE",),
    )


def test_success_result_converts_request_bound_provenance_without_provider_data() -> (
    None
):
    from millrace.adapters.runner_contract import (
        AdapterInvocationRequest,
        AdapterSuccessResult,
        DispatchEcho,
        RedactionPolicy,
        runner_evidence_from_adapter_outcome,
    )

    dispatch = _valid_dispatch_envelope()
    policy = RedactionPolicy(policy_id="redact-default")
    request = AdapterInvocationRequest(
        adapter_id="adapter-1",
        selected_runner_binding_id=dispatch.runner_binding_id,
        selected_adapter_kind="millforge",
        dispatch_envelope=dispatch,
        timeout_seconds=30,
        correlation_id="corr-1",
        redaction_policy=policy,
        selected_component_pin=_millforge_component_pin(),
    )
    provenance = RunnerAdapterProvenance(
        adapter_kind="millforge",
        component_descriptor_sha256="a" * 64,
        invocation_evidence_sha256="b" * 64,
        correlation_id="corr-1",
    )
    success = AdapterSuccessResult.from_unredacted(
        adapter_id="adapter-1",
        dispatch_echo=DispatchEcho.from_dispatch_envelope(
            dispatch,
            correlation_id="corr-1",
        ),
        marker="TASK_COMPLETE",
        adapter_provenance=provenance,
        structured_provider_response={"provider": "non-durable"},
        evidence_construction_diagnostics={"note": "non-durable"},
        redaction_policy=policy,
    )

    evidence = runner_evidence_from_adapter_outcome(success, request)
    payload = evidence.payload()

    assert evidence.adapter_provenance == provenance
    assert "structured_provider_response" not in payload
    assert "evidence_construction_diagnostics" not in payload
    assert "adapter_provenance" not in evidence.observation_payload
    assert "adapter_provenance" not in evidence.artifact_payload


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    (
        ("adapter_kind", "codex"),
        ("component_descriptor_sha256", "c" * 64),
        ("correlation_id", "corr-2"),
        ("invocation_evidence_sha256", "malformed"),
    ),
)
def test_success_result_conversion_refuses_unbound_or_malformed_provenance(
    field_name: str,
    field_value: str,
) -> None:
    from millrace.adapters.runner_contract import (
        AdapterEvidenceConversionError,
        AdapterInvocationRequest,
        AdapterSuccessResult,
        DispatchEcho,
        RedactionPolicy,
        runner_evidence_from_adapter_outcome,
    )

    dispatch = _valid_dispatch_envelope()
    policy = RedactionPolicy(policy_id="redact-default")
    request = AdapterInvocationRequest(
        adapter_id="adapter-1",
        selected_runner_binding_id=dispatch.runner_binding_id,
        selected_adapter_kind="millforge",
        dispatch_envelope=dispatch,
        timeout_seconds=30,
        correlation_id="corr-1",
        redaction_policy=policy,
        selected_component_pin=_millforge_component_pin(),
    )
    success = AdapterSuccessResult.from_unredacted(
        adapter_id="adapter-1",
        dispatch_echo=DispatchEcho.from_dispatch_envelope(
            dispatch,
            correlation_id="corr-1",
        ),
        marker="TASK_COMPLETE",
        adapter_provenance=RunnerAdapterProvenance(
            adapter_kind="millforge",
            component_descriptor_sha256="a" * 64,
            invocation_evidence_sha256="b" * 64,
            correlation_id="corr-1",
        ),
        redaction_policy=policy,
    )
    provenance = cast(RunnerAdapterProvenance, success.adapter_provenance)
    object.__setattr__(provenance, field_name, field_value)

    with pytest.raises(AdapterEvidenceConversionError, match="adapter provenance"):
        runner_evidence_from_adapter_outcome(success, request)


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    (
        ("run_id", "run-2"),
        ("claim_id", "claim-2"),
        ("generation", 1),
        ("fencing_token", "fence-2"),
        ("plan_fingerprint", "sha256:changed"),
        ("stage_kind_id", "kernel_ping.other_stage"),
        ("graph_node_id", "kernel_ping.other_node"),
        ("runner_binding_id", "kernel_ping.other_runner"),
        ("correlation_id", "corr-2"),
    ),
)
def test_success_result_refuses_each_dispatch_echo_identity_mismatch(
    field_name: str,
    field_value: object,
) -> None:
    from dataclasses import replace

    from millrace.adapters.runner_contract import (
        AdapterInvocationRequest,
        AdapterSuccessResult,
        DispatchEcho,
        RedactionPolicy,
        runner_evidence_from_adapter_outcome,
    )

    dispatch = _valid_dispatch_envelope()
    policy = RedactionPolicy(policy_id="redact-default")
    request = AdapterInvocationRequest(
        adapter_id="adapter-1",
        selected_runner_binding_id=dispatch.runner_binding_id,
        selected_adapter_kind="fake_local",
        dispatch_envelope=dispatch,
        timeout_seconds=30,
        correlation_id="corr-1",
        redaction_policy=policy,
    )
    echo = DispatchEcho.from_dispatch_envelope(dispatch, correlation_id="corr-1")
    mismatched_echo = replace(echo, **{field_name: field_value})
    success = AdapterSuccessResult.from_unredacted(
        adapter_id="adapter-1",
        dispatch_echo=mismatched_echo,
        marker="TASK_COMPLETE",
        redaction_policy=policy,
    )

    with pytest.raises(ValueError, match="dispatch echo"):
        runner_evidence_from_adapter_outcome(success, request)


def test_error_outcomes_and_half_success_records_cannot_convert_to_evidence() -> None:
    from millrace.adapters.runner_contract import (
        AdapterErrorResult,
        AdapterInvocationRequest,
        AdapterSuccessResult,
        DispatchEcho,
        RedactionPolicy,
        runner_evidence_from_adapter_outcome,
    )

    dispatch = _valid_dispatch_envelope()
    policy = RedactionPolicy(policy_id="redact-default")
    request = AdapterInvocationRequest(
        adapter_id="adapter-1",
        selected_runner_binding_id=dispatch.runner_binding_id,
        selected_adapter_kind="fake_local",
        dispatch_envelope=dispatch,
        timeout_seconds=30,
        correlation_id="corr-1",
        redaction_policy=policy,
    )
    error = AdapterErrorResult.from_unredacted(
        adapter_id="adapter-1",
        error_kind="timeout",
        dispatch_echo=DispatchEcho.from_dispatch_envelope(
            dispatch,
            correlation_id="corr-1",
        ),
        diagnostics={"secret": "safe"},
        redaction_policy=policy,
    )

    assert not hasattr(error, "marker")
    assert not hasattr(error, "artifact_payload_candidate")
    with pytest.raises(TypeError):
        runner_evidence_from_adapter_outcome(error, request)

    success_constructor = cast(Any, AdapterSuccessResult)
    with pytest.raises(TypeError):
        success_constructor(
            adapter_id="adapter-1",
            dispatch_echo=DispatchEcho.from_dispatch_envelope(
                dispatch,
                correlation_id="corr-1",
            ),
            marker="TASK_COMPLETE",
            error_kind="timeout",
            redaction_policy=policy,
        )


@pytest.mark.parametrize(
    "error_kind",
    (
        "timeout",
        "cancelled",
        "missing_opt_in_config",
        "invocation_failed",
        "result_parse_failed",
        "unsupported_adapter_kind",
        "input_too_large",
        "output_too_large",
        "redaction_refused",
        "selected_authority_refused",
    ),
)
def test_each_adapter_error_kind_cannot_convert_to_evidence(error_kind: str) -> None:
    from millrace.adapters.runner_contract import (
        AdapterErrorResult,
        AdapterInvocationRequest,
        DispatchEcho,
        RedactionPolicy,
        runner_evidence_from_adapter_outcome,
    )

    dispatch = _valid_dispatch_envelope()
    policy = RedactionPolicy(policy_id="redact-default")
    request = AdapterInvocationRequest(
        adapter_id="adapter-1",
        selected_runner_binding_id=dispatch.runner_binding_id,
        selected_adapter_kind="fake_local",
        dispatch_envelope=dispatch,
        timeout_seconds=30,
        correlation_id="corr-1",
        redaction_policy=policy,
    )
    error = AdapterErrorResult.from_unredacted(
        adapter_id="adapter-1",
        error_kind=error_kind,
        dispatch_echo=DispatchEcho.from_dispatch_envelope(
            dispatch,
            correlation_id="corr-1",
        ),
        diagnostics={"safe": "diagnostic"},
        redaction_policy=policy,
    )

    with pytest.raises(TypeError):
        runner_evidence_from_adapter_outcome(error, request)


def test_redaction_runs_before_result_exposure_across_all_result_surfaces(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from millrace.adapters.runner_contract import (
        AdapterErrorResult,
        AdapterSuccessResult,
        DispatchEcho,
        RedactionPolicy,
    )

    dispatch = _valid_dispatch_envelope()
    secret = "token-secret"
    policy = RedactionPolicy(policy_id="redact-default", secret_tokens=(secret,))
    echo = DispatchEcho.from_dispatch_envelope(dispatch, correlation_id="corr-1")
    success = AdapterSuccessResult.from_unredacted(
        adapter_id="adapter-1",
        dispatch_echo=echo,
        marker="TASK_COMPLETE",
        captured_stdout=f"stdout {secret}",
        captured_stderr=f"stderr {secret}",
        structured_provider_response={"provider": secret, "items": [secret]},
        observation_payload_candidate={"obs": secret, "items": [secret]},
        artifact_payload_candidate={"artifact": secret, "items": [secret]},
        evidence_construction_diagnostics={"diag": secret, "items": [secret]},
        redaction_policy=policy,
    )
    error = AdapterErrorResult.from_unredacted(
        adapter_id="adapter-1",
        error_kind="invocation_failed",
        dispatch_echo=echo,
        diagnostics={"exception": f"failed with {secret}", "items": [secret]},
        redaction_policy=policy,
    )

    logger = logging.getLogger(__name__)
    logger.warning("%s %r %s %r", success, success, error, error)

    exposed = (
        success.captured_stdout,
        success.captured_stderr,
        success.structured_provider_response,
        success.observation_payload_candidate,
        success.artifact_payload_candidate,
        success.evidence_construction_diagnostics,
        error.diagnostics,
        str(success),
        repr(success),
        str(error),
        repr(error),
        caplog.text,
    )
    assert secret not in repr(exposed)


def test_redaction_canonicalizes_policy_and_hides_identity_repr_surfaces(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from millrace.adapters.runner_contract import (
        AdapterErrorResult,
        AdapterInvocationRequest,
        AdapterSuccessResult,
        DispatchEcho,
        RedactionPolicy,
        canonicalize_redaction_policy,
        runner_evidence_from_adapter_outcome,
    )

    class SpoofingPolicy(RedactionPolicy):
        def __getattribute__(self, name: str) -> object:
            if name == "secret_tokens":
                return ()
            return super().__getattribute__(name)

        def redact_text(self, value: str) -> str:
            return value

        def redact_authority_value(self, value: object) -> AuthorityValue:
            return cast(AuthorityValue, value)

    secret = "CONFIG_SECRET"
    policy = SpoofingPolicy(
        policy_id=f"policy-{secret}",
        secret_tokens=(secret,),
    )
    dispatch = _valid_dispatch_envelope(
        run_id=f"run-{secret}",
        claim_id=f"claim-{secret}",
        fencing_token=f"fence-{secret}",
        plan_fingerprint=f"sha256:{secret}",
    )
    echo = DispatchEcho.from_dispatch_envelope(dispatch, correlation_id="corr-1")
    request = AdapterInvocationRequest(
        adapter_id=f"adapter-{secret}",
        selected_runner_binding_id=dispatch.runner_binding_id,
        selected_adapter_kind="fake_local",
        dispatch_envelope=dispatch,
        timeout_seconds=30,
        correlation_id="corr-1",
        redaction_policy=policy,
    )
    success = AdapterSuccessResult.from_unredacted(
        adapter_id=f"adapter-{secret}",
        dispatch_echo=echo,
        marker="TASK_COMPLETE",
        captured_stdout=secret,
        structured_provider_response={"provider": secret},
        redaction_policy=policy,
    )
    error = AdapterErrorResult.from_unredacted(
        adapter_id=f"adapter-{secret}",
        error_kind="invocation_failed",
        dispatch_echo=echo,
        diagnostics={"diag": secret},
        redaction_policy=policy,
    )

    logging.getLogger(__name__).warning(
        "%s %r %s %r %s %r %s %r",
        policy,
        policy,
        echo,
        echo,
        success,
        success,
        error,
        error,
    )

    canonical = canonicalize_redaction_policy(policy)
    evidence = runner_evidence_from_adapter_outcome(success, request)
    exposed = (
        repr(policy),
        str(policy),
        repr(echo),
        str(echo),
        repr(success),
        str(success),
        repr(error),
        str(error),
        caplog.text,
    )
    assert canonical.policy_id == f"policy-{secret}"
    assert canonical.secret_tokens == (secret,)
    assert success.adapter_id == "adapter-[REDACTED]"
    assert success.redaction_policy_id == "policy-[REDACTED]"
    assert error.adapter_id == "adapter-[REDACTED]"
    assert error.redaction_policy_id == "policy-[REDACTED]"
    assert evidence.run_id == f"run-{secret}"
    assert secret not in repr(exposed)


def test_adapter_invocation_request_repr_hides_selected_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from millrace.adapters.runner_contract import (
        AdapterInvocationRequest,
        RedactionPolicy,
    )

    secret = "CONFIG_SECRET"
    policy = RedactionPolicy(
        policy_id=f"policy-{secret}",
        secret_tokens=(secret,),
    )
    dispatch = _valid_dispatch_envelope(
        run_id=f"run-{secret}",
        claim_id=f"claim-{secret}",
        fencing_token=f"fence-{secret}",
        plan_fingerprint=f"sha256:{secret}",
        work_item_payload={"body": secret},
        governance_context={"token": secret},
    )
    request = AdapterInvocationRequest(
        adapter_id=f"adapter-{secret}",
        selected_runner_binding_id=dispatch.runner_binding_id,
        selected_adapter_kind=f"fake-{secret}",
        dispatch_envelope=dispatch,
        timeout_seconds=30,
        correlation_id=f"corr-{secret}",
        redaction_policy=policy,
        selected_asset_material={"asset": {"body": secret}},
        environment_policy_ref=f"env-{secret}",
        local_config_ref=f"local-{secret}",
        cancellation_token=f"cancel-{secret}",
    )
    logging.getLogger(__name__).warning("%s %r", request, request)

    assert request.dispatch_envelope.run_id == f"run-{secret}"
    assert request.selected_asset_material["asset"] == {"body": secret}
    assert secret not in repr(request)
    assert secret not in str(request)
    assert secret not in caplog.text


def test_redaction_covers_mapping_keys_and_overlapping_tokens(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from millrace.adapters.runner_contract import (
        AdapterErrorResult,
        AdapterSuccessResult,
        DispatchEcho,
        RedactionPolicy,
    )

    dispatch = _valid_dispatch_envelope()
    secret = "token-secret"
    policy = RedactionPolicy(
        policy_id="redact-default",
        secret_tokens=("token", secret),
    )
    echo = DispatchEcho.from_dispatch_envelope(dispatch, correlation_id="corr-1")

    success = AdapterSuccessResult.from_unredacted(
        adapter_id="adapter-1",
        dispatch_echo=echo,
        marker="TASK_COMPLETE",
        structured_provider_response={
            secret: "provider-value",
            "nested": {secret: "nested-value"},
            "overlap": secret,
        },
        observation_payload_candidate={secret: "obs-value"},
        artifact_payload_candidate={secret: "artifact-value"},
        evidence_construction_diagnostics={secret: "diag-value"},
        redaction_policy=policy,
    )
    error = AdapterErrorResult.from_unredacted(
        adapter_id="adapter-1",
        error_kind="invocation_failed",
        dispatch_echo=echo,
        diagnostics={secret: "exception-value", "overlap": secret},
        redaction_policy=policy,
    )

    logger = logging.getLogger(f"{__name__}.key_redaction")
    logger.warning("%s %r %s %r", success, success, error, error)

    exposed = (
        success.structured_provider_response,
        success.observation_payload_candidate,
        success.artifact_payload_candidate,
        success.evidence_construction_diagnostics,
        error.diagnostics,
        str(success),
        repr(success),
        str(error),
        repr(error),
        caplog.text,
    )
    exposed_text = repr(exposed)
    assert secret not in exposed_text
    assert "[REDACTED]-secret" not in exposed_text


def test_redaction_refuses_colliding_redacted_mapping_keys_without_leaking() -> None:
    from millrace.adapters.runner_contract import RedactionPolicy

    policy = RedactionPolicy(
        policy_id="redact-default",
        secret_tokens=("secret-a", "secret-b"),
    )

    with pytest.raises(ValueError) as error:
        policy.redact_authority_value({"secret-a": "a", "secret-b": "b"})

    assert "redacted mapping keys collide" in str(error.value)
    assert "secret-a" not in str(error.value)
    assert "secret-b" not in str(error.value)


def test_adapter_contract_imports_only_allowed_contract_modules() -> None:
    module_path = Path("src/millrace/adapters/runner_contract.py")
    tree = ast.parse(module_path.read_text())
    forbidden_prefixes = (
        "millrace.compiler",
        "millrace.kernel",
        "millrace.operator",
        "millrace.substrate",
        "millrace.workflows",
    )
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)

    assert not [
        imported for imported in imports if imported.startswith(forbidden_prefixes)
    ]
