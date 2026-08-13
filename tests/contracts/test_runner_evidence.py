from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, fields
from typing import Any, cast

import pytest

from millrace.contracts.compiled_plan import AuthorityValue
from millrace.contracts.runner import (
    RUNNER_DISPATCH_RECORD_KIND,
    RUNNER_DISPATCH_SCHEMA_VERSION,
    RUNNER_RESULT_RECORD_KIND,
    RUNNER_RESULT_SCHEMA_VERSION,
    RUNNER_SESSION_COMPLETION_DIAGNOSTIC_RECORD_KIND,
    RUNNER_SESSION_COMPLETION_DIAGNOSTIC_SCHEMA_VERSION,
    RunnerAdapterProvenance,
    RunnerDispatchEnvelope,
    RunnerResultEvidence,
    RunnerSessionCompletionDiagnostic,
    runner_result_evidence_from_payload,
    runner_session_completion_diagnostic_bytes,
    runner_session_completion_diagnostic_from_payload,
)


def _valid_dispatch_payload() -> dict[str, AuthorityValue]:
    return {
        "record_kind": RUNNER_DISPATCH_RECORD_KIND,
        "schema_version": RUNNER_DISPATCH_SCHEMA_VERSION,
        "run_id": "run-1",
        "session_id": "session-1",
        "dispatch_generation": 1,
        "session_fencing_token": "session-fence-1",
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
        "terminal_options": (),
        "context_checkout": None,
    }


def _valid_dispatch_kwargs() -> dict[str, object]:
    payload: dict[str, object] = dict(_valid_dispatch_payload())
    del payload["record_kind"]
    del payload["schema_version"]
    return payload


def _valid_dispatch_envelope(**overrides: object) -> RunnerDispatchEnvelope:
    kwargs = _valid_dispatch_kwargs()
    kwargs.update(overrides)
    return RunnerDispatchEnvelope(
        run_id=cast(str, kwargs["run_id"]),
        session_id=cast(str, kwargs["session_id"]),
        dispatch_generation=cast(int, kwargs["dispatch_generation"]),
        session_fencing_token=cast(str, kwargs["session_fencing_token"]),
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
            Mapping[str, AuthorityValue],
            kwargs["work_item_payload"],
        ),
        governance_context=cast(
            Mapping[str, AuthorityValue],
            kwargs["governance_context"],
        ),
        terminal_options=cast(
            tuple[Mapping[str, AuthorityValue], ...],
            kwargs["terminal_options"],
        ),
        selected_wait_evidence=cast(
            Mapping[str, AuthorityValue] | None,
            kwargs.get("selected_wait_evidence"),
        ),
    )


_CORRELATION_IDENTITY = (
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
)


def _valid_selected_join_evidence(**overrides: object) -> dict[str, object]:
    evidence: dict[str, object] = {
        "record_kind": "selected_join_evidence",
        "schema_version": 1,
        "join_id": "candidate_evidence_join",
        "correlation_key": "candidate_id",
        "correlation_value": "candidate-1",
        "correlation_identity": _CORRELATION_IDENTITY,
        "lineage_id": None,
        "bundle_artifact_id": "artifact-candidate-bundle",
        "bundle_artifact_schema_id": "CandidateBundle",
        "bundle_artifact_digest": "sha256:"
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "required_artifact_schema_ids": ("RubricReport", "RubricReport"),
        "evidence_artifacts": (
            {
                "artifact_id": "artifact-rubric-a",
                "artifact_schema_id": "RubricReport",
                "payload_digest": "sha256:"
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "payload": {
                    "score": 8,
                    "nested": {"verdict": "advance"},
                },
                "source_action_id": "action-rubric-a",
                "source_run_id": "run-rubric-a",
                "source_work_item_id": "work-rubric-a",
                "fanout_id": "fanout-evaluators",
                "fanout_record_id": "fanout-record-a",
                "item_key": "candidate-a",
            },
            {
                "artifact_id": "artifact-rubric-b",
                "artifact_schema_id": "RubricReport",
                "payload_digest": "sha256:"
                "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
                "payload": {"score": 6},
                "source_action_id": "action-rubric-b",
                "source_run_id": "run-rubric-b",
                "source_work_item_id": "work-rubric-b",
                "fanout_id": "fanout-evaluators",
                "fanout_record_id": "fanout-record-b",
                "item_key": "candidate-b",
            },
        ),
    }
    evidence.update(overrides)
    return evidence


def _valid_selected_wait_evidence(**overrides: object) -> dict[str, object]:
    evidence: dict[str, object] = {
        "record_kind": "selected_wait_evidence",
        "schema_version": 1,
        "wait_id": "wait-1",
        "operator_wait_id": "operator-wait-1",
        "lineage_id": "lineage-1",
        "source_artifact_id": "artifact-source-1",
        "source_artifact_schema_id": "SourceRecord",
        "source_artifact_digest": "sha256:" + "d" * 64,
        "source_artifact_payload": {"decision": "review"},
        "source_action_id": "action-source-1",
        "source_run_id": "run-source-1",
        "source_work_item_id": "work-source-1",
    }
    evidence.update(overrides)
    return evidence


def _dispatch_with_selected_join_evidence(
    selected_join_evidence: object,
) -> RunnerDispatchEnvelope:
    constructor = cast(Any, RunnerDispatchEnvelope)
    return constructor(
        **_valid_dispatch_kwargs(),
        selected_join_evidence=selected_join_evidence,
    )


def _valid_result_payload() -> dict[str, AuthorityValue]:
    return {
        "record_kind": RUNNER_RESULT_RECORD_KIND,
        "schema_version": RUNNER_RESULT_SCHEMA_VERSION,
        "run_id": "run-1",
        "session_id": "session-1",
        "dispatch_generation": 1,
        "session_fencing_token": "session-fence-1",
        "plan_fingerprint": "sha256:abcdef",
        "claim_id": "claim-1",
        "generation": 0,
        "fencing_token": "fence-1",
        "stage_kind_id": "kernel_ping.taskmaster",
        "graph_node_id": "kernel_ping.start",
        "runner_binding_id": "kernel_ping.fake_local_runner",
        "marker": "TASK_COMPLETE",
        "adapter_provenance": None,
        "observation_payload": {"worker_summary": "ready"},
        "artifact_payload": {"artifact_kind": "kernel_ping.task_artifact"},
    }


def _valid_result_kwargs() -> dict[str, object]:
    payload: dict[str, object] = dict(_valid_result_payload())
    del payload["record_kind"]
    del payload["schema_version"]
    return payload


def _valid_result_raw_payload() -> dict[str, object]:
    return dict(_valid_result_payload())


def _valid_result_evidence(**overrides: object) -> RunnerResultEvidence:
    kwargs = _valid_result_kwargs()
    kwargs.update(overrides)
    return RunnerResultEvidence(
        run_id=cast(str, kwargs["run_id"]),
        session_id=cast(str, kwargs["session_id"]),
        dispatch_generation=cast(int, kwargs["dispatch_generation"]),
        session_fencing_token=cast(str, kwargs["session_fencing_token"]),
        plan_fingerprint=cast(str, kwargs["plan_fingerprint"]),
        claim_id=cast(str, kwargs["claim_id"]),
        generation=cast(int, kwargs["generation"]),
        fencing_token=cast(str, kwargs["fencing_token"]),
        stage_kind_id=cast(str, kwargs["stage_kind_id"]),
        graph_node_id=cast(str, kwargs["graph_node_id"]),
        runner_binding_id=cast(str, kwargs["runner_binding_id"]),
        marker=cast(str, kwargs["marker"]),
        adapter_provenance=kwargs["adapter_provenance"],
        observation_payload=cast(
            Mapping[str, AuthorityValue] | None,
            kwargs["observation_payload"],
        ),
        artifact_payload=cast(
            Mapping[str, AuthorityValue] | None,
            kwargs["artifact_payload"],
        ),
    )


def test_runner_dispatch_and_result_records_expose_stable_protocol_metadata() -> None:
    assert RunnerDispatchEnvelope.record_kind == RUNNER_DISPATCH_RECORD_KIND
    assert RunnerDispatchEnvelope.schema_version == RUNNER_DISPATCH_SCHEMA_VERSION
    assert RunnerResultEvidence.record_kind == RUNNER_RESULT_RECORD_KIND
    assert RunnerResultEvidence.schema_version == RUNNER_RESULT_SCHEMA_VERSION
    assert RUNNER_RESULT_SCHEMA_VERSION == 3
    assert RunnerAdapterProvenance.record_kind == "runner_adapter_provenance"
    assert RunnerAdapterProvenance.schema_version == 1
    assert "record_kind" not in {field.name for field in fields(RunnerDispatchEnvelope)}
    assert "schema_version" not in {
        field.name for field in fields(RunnerDispatchEnvelope)
    }
    assert "record_kind" not in {field.name for field in fields(RunnerResultEvidence)}
    assert "schema_version" not in {
        field.name for field in fields(RunnerResultEvidence)
    }

    dispatch = _valid_dispatch_envelope()
    evidence = _valid_result_evidence()

    assert dispatch.payload()["record_kind"] == RUNNER_DISPATCH_RECORD_KIND
    assert dispatch.payload()["schema_version"] == RUNNER_DISPATCH_SCHEMA_VERSION
    assert dispatch.payload()["schema_version"] == 7
    assert dispatch.payload()["selected_join_evidence"] is None
    assert dispatch.payload()["selected_wait_evidence"] is None
    assert evidence.payload()["record_kind"] == RUNNER_RESULT_RECORD_KIND
    assert evidence.payload()["schema_version"] == RUNNER_RESULT_SCHEMA_VERSION


def test_runner_session_completion_diagnostic_is_canonical_and_typed() -> None:
    diagnostic = RunnerSessionCompletionDiagnostic(
        run_id="run-1",
        session_id="session-1",
        dispatch_generation=1,
        session_fencing_token="session-fence-1",
        plan_fingerprint="sha256:" + "a" * 64,
        claim_id="claim-1",
        generation=0,
        fencing_token="fence-1",
        stage_kind_id="kernel_ping.taskmaster",
        graph_node_id="kernel_ping.start",
        runner_binding_id="kernel_ping.fake_local_runner",
        diagnostic={"diagnostics": {}},
    )

    payload = diagnostic.payload()
    assert set(payload) == {
        "record_kind",
        "schema_version",
        "run_id",
        "session_id",
        "dispatch_generation",
        "session_fencing_token",
        "plan_fingerprint",
        "claim_id",
        "generation",
        "fencing_token",
        "stage_kind_id",
        "graph_node_id",
        "runner_binding_id",
        "diagnostic",
    }
    assert payload["record_kind"] == RUNNER_SESSION_COMPLETION_DIAGNOSTIC_RECORD_KIND
    assert (
        payload["schema_version"]
        == RUNNER_SESSION_COMPLETION_DIAGNOSTIC_SCHEMA_VERSION
    )
    encoded = runner_session_completion_diagnostic_bytes(diagnostic)
    assert runner_session_completion_diagnostic_from_payload(
        json.loads(encoded)
    ) == diagnostic

    foreign = runner_session_completion_diagnostic_from_payload(
        dict(payload, session_id="foreign-session")
    )
    assert foreign.session_id == "foreign-session"
    with pytest.raises(ValueError):
        runner_session_completion_diagnostic_from_payload(
            dict(payload, unsupported="forbidden")
        )


def _valid_adapter_provenance_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "record_kind": "runner_adapter_provenance",
        "schema_version": 1,
        "adapter_kind": "millforge",
        "component_descriptor_sha256": "a" * 64,
        "invocation_evidence_sha256": "b" * 64,
        "correlation_id": "corr-1",
    }
    payload.update(overrides)
    return payload


def test_runner_adapter_provenance_is_exact_versioned_and_immutable() -> None:
    provenance = RunnerAdapterProvenance(
        adapter_kind="millforge",
        component_descriptor_sha256="a" * 64,
        invocation_evidence_sha256="b" * 64,
        correlation_id="corr-1",
    )
    evidence = _valid_result_evidence(adapter_provenance=provenance)

    assert dict(provenance.payload()) == _valid_adapter_provenance_payload()
    assert evidence.payload()["adapter_provenance"] == provenance.payload()
    assert runner_result_evidence_from_payload(evidence.payload()) == evidence
    with pytest.raises(FrozenInstanceError):
        provenance.correlation_id = "corr-2"
    with pytest.raises(TypeError):
        provenance.payload()["correlation_id"] = "corr-2"  # type: ignore[index]


@pytest.mark.parametrize(
    "adapter_provenance",
    (
        "not-a-record",
        {},
        _valid_adapter_provenance_payload(extra="forbidden"),
        _valid_adapter_provenance_payload(record_kind="wrong"),
        _valid_adapter_provenance_payload(schema_version=2),
        _valid_adapter_provenance_payload(adapter_kind=" "),
        _valid_adapter_provenance_payload(component_descriptor_sha256="A" * 64),
        _valid_adapter_provenance_payload(invocation_evidence_sha256="bad"),
        _valid_adapter_provenance_payload(correlation_id=" "),
    ),
)
def test_runner_result_parser_refuses_malformed_adapter_provenance(
    adapter_provenance: object,
) -> None:
    payload = dict(
        _valid_result_raw_payload(),
        adapter_provenance=adapter_provenance,
    )

    with pytest.raises((TypeError, ValueError)):
        runner_result_evidence_from_payload(payload)


def test_runner_dispatch_selected_join_evidence_is_versioned_exact_and_immutable() -> (
    None
):
    selected_join_evidence = _valid_selected_join_evidence()
    dispatch = _dispatch_with_selected_join_evidence(selected_join_evidence)

    payload = dispatch.payload()
    selected = cast(Mapping[str, AuthorityValue], payload["selected_join_evidence"])
    evidence_artifacts = cast(
        tuple[Mapping[str, AuthorityValue], ...],
        selected["evidence_artifacts"],
    )
    first_artifact = evidence_artifacts[0]
    first_payload = cast(Mapping[str, AuthorityValue], first_artifact["payload"])
    nested_payload = cast(Mapping[str, AuthorityValue], first_payload["nested"])

    assert payload["schema_version"] == 7
    assert selected == selected_join_evidence
    assert set(selected) == {
        "record_kind",
        "schema_version",
        "join_id",
        "correlation_key",
        "correlation_value",
        "correlation_identity",
        "lineage_id",
        "bundle_artifact_id",
        "bundle_artifact_schema_id",
        "bundle_artifact_digest",
        "required_artifact_schema_ids",
        "evidence_artifacts",
    }
    assert selected["record_kind"] == "selected_join_evidence"
    assert selected["schema_version"] == 1
    assert selected["correlation_identity"] == _CORRELATION_IDENTITY
    assert selected["lineage_id"] is None
    assert selected["required_artifact_schema_ids"] == (
        "RubricReport",
        "RubricReport",
    )
    assert len(evidence_artifacts) == 2
    assert set(first_artifact) == {
        "artifact_id",
        "artifact_schema_id",
        "payload_digest",
        "payload",
        "source_action_id",
        "source_run_id",
        "source_work_item_id",
        "fanout_id",
        "fanout_record_id",
        "item_key",
    }
    assert first_artifact["item_key"] == "candidate-a"
    assert first_payload == {"score": 8, "nested": {"verdict": "advance"}}

    selected_join_evidence["join_id"] = "mutated-after-construction"
    assert selected["join_id"] == "candidate_evidence_join"
    with pytest.raises(TypeError):
        selected["join_id"] = "forged"  # type: ignore[index]
    with pytest.raises(TypeError):
        first_artifact["item_key"] = "forged"  # type: ignore[index]
    with pytest.raises(TypeError):
        first_payload["score"] = 0  # type: ignore[index]
    with pytest.raises(TypeError):
        nested_payload["verdict"] = "reject"  # type: ignore[index]


def test_runner_dispatch_selected_wait_evidence_is_versioned_exact_and_immutable() -> (
    None
):
    evidence = _valid_selected_wait_evidence()
    dispatch = _valid_dispatch_envelope(selected_wait_evidence=evidence)

    selected = cast(
        Mapping[str, AuthorityValue],
        dispatch.payload()["selected_wait_evidence"],
    )
    assert selected == evidence
    assert set(selected) == set(evidence)
    evidence["wait_id"] = "mutated"
    assert selected["wait_id"] == "wait-1"
    with pytest.raises(TypeError):
        selected["wait_id"] = "forged"  # type: ignore[index]


@pytest.mark.parametrize(
    "evidence",
    (
        "not-a-record",
        _valid_selected_wait_evidence(record_kind="wrong"),
        _valid_selected_wait_evidence(schema_version=2),
        _valid_selected_wait_evidence(wait_id=" "),
        _valid_selected_wait_evidence(lineage_id=None),
        _valid_selected_wait_evidence(source_artifact_digest="d" * 64),
        _valid_selected_wait_evidence(source_artifact_payload=[]),
        _valid_selected_wait_evidence(extra="forbidden"),
    ),
)
def test_runner_dispatch_rejects_malformed_selected_wait_evidence(
    evidence: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        _valid_dispatch_envelope(selected_wait_evidence=evidence)


@pytest.mark.parametrize(
    "selected_join_evidence",
    (
        dict(_valid_selected_join_evidence(), record_kind="wrong"),
        dict(_valid_selected_join_evidence(), schema_version=2),
        dict(_valid_selected_join_evidence(), join_id=" \t "),
        dict(_valid_selected_join_evidence(), lineage_id=""),
        dict(
            _valid_selected_join_evidence(),
            correlation_identity="sha256:" + "0" * 64,
        ),
        dict(_valid_selected_join_evidence(), correlation_identity="A" * 64),
        dict(_valid_selected_join_evidence(), correlation_identity="0" * 63),
        dict(_valid_selected_join_evidence(), bundle_artifact_digest="0" * 64),
        dict(
            _valid_selected_join_evidence(),
            required_artifact_schema_ids=["RubricReport"],
        ),
        dict(_valid_selected_join_evidence(), evidence_artifacts=[]),
        dict(_valid_selected_join_evidence(), unsupported="field"),
        dict(
            _valid_selected_join_evidence(),
            evidence_artifacts=(
                dict(
                    cast(
                        tuple[Mapping[str, object], ...],
                        _valid_selected_join_evidence()["evidence_artifacts"],
                    )[0],
                    payload_digest="not-sha256-prefixed",
                ),
            ),
        ),
        dict(
            _valid_selected_join_evidence(),
            evidence_artifacts=(
                dict(
                    cast(
                        tuple[Mapping[str, object], ...],
                        _valid_selected_join_evidence()["evidence_artifacts"],
                    )[0],
                    item_key="",
                ),
            ),
        ),
        dict(
            _valid_selected_join_evidence(),
            evidence_artifacts=(
                dict(
                    cast(
                        tuple[Mapping[str, object], ...],
                        _valid_selected_join_evidence()["evidence_artifacts"],
                    )[0],
                    unsupported="field",
                ),
            ),
        ),
    ),
)
def test_runner_dispatch_rejects_malformed_selected_join_evidence(
    selected_join_evidence: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        _dispatch_with_selected_join_evidence(selected_join_evidence)


@pytest.mark.parametrize(
    "invalid_digest",
    (
        pytest.param("sha256:" + "g" * 64, id="nonhex"),
        pytest.param("sha256:" + "A" * 64, id="uppercase"),
        pytest.param("sha256:" + "a" * 63, id="63-char"),
        pytest.param("sha256:" + "a" * 65, id="65-char"),
        pytest.param("sha256:" + "a" * 64 + " ", id="whitespace-suffix"),
    ),
)
@pytest.mark.parametrize(
    "artifact_index",
    (
        pytest.param(None, id="bundle"),
        pytest.param(0, id="evidence-0"),
        pytest.param(1, id="evidence-1"),
    ),
)
def test_runner_dispatch_rejects_noncanonical_selected_join_digests(
    invalid_digest: str,
    artifact_index: int | None,
) -> None:
    selected_join_evidence = _valid_selected_join_evidence()
    if artifact_index is None:
        selected_join_evidence["bundle_artifact_digest"] = invalid_digest
    else:
        evidence_artifacts = list(
            cast(
                tuple[Mapping[str, object], ...],
                selected_join_evidence["evidence_artifacts"],
            ),
        )
        evidence_artifacts[artifact_index] = dict(
            evidence_artifacts[artifact_index],
            payload_digest=invalid_digest,
        )
        selected_join_evidence["evidence_artifacts"] = tuple(evidence_artifacts)

    with pytest.raises(ValueError):
        _dispatch_with_selected_join_evidence(selected_join_evidence)


def test_runner_record_payloads_are_immutable() -> None:
    dispatch = _valid_dispatch_envelope()
    evidence = _valid_result_evidence()

    with pytest.raises(FrozenInstanceError):
        dispatch.work_item_id = "run-2"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        evidence.marker = "BLOCKED"  # type: ignore[misc]
    with pytest.raises(TypeError):
        dispatch.work_item_payload["body"] = "mutated"  # type: ignore[index]
    with pytest.raises(TypeError):
        dispatch.governance_context["counters"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        evidence.observation_payload["result"] = "fail"  # type: ignore[index]
    wrong_artifact = "kernel_ping.wrong"
    with pytest.raises(TypeError):
        evidence.artifact_payload["artifact_kind"] = wrong_artifact  # type: ignore[index]


def test_runner_dispatch_terminal_options_are_payload_visible_and_immutable() -> None:
    terminal_options = (
        {
            "outcome_id": "kernel_ping.taskmaster.complete",
            "marker": "TASK_COMPLETE",
            "action_id": "kernel_ping.taskmaster.emit_task",
            "action_kind": "route",
            "artifact_schema_id": "kernel_ping.task_artifact",
        },
    )

    dispatch = _valid_dispatch_envelope(terminal_options=terminal_options)

    assert dispatch.terminal_options == terminal_options
    assert dispatch.payload()["terminal_options"] == terminal_options
    with pytest.raises(FrozenInstanceError):
        dispatch.terminal_options = ()  # type: ignore[misc]
    with pytest.raises(TypeError):
        dispatch.terminal_options[0]["marker"] = "FORGED"  # type: ignore[index]


def test_runner_dispatch_rejects_malformed_terminal_options() -> None:
    with pytest.raises((TypeError, ValueError)):
        _valid_dispatch_envelope(terminal_options=({"marker": "TASK_COMPLETE"},))

    with pytest.raises((TypeError, ValueError)):
        _valid_dispatch_envelope(terminal_options=({"marker": object()},))

    with pytest.raises((TypeError, ValueError)):
        _valid_dispatch_envelope(terminal_options=[{"marker": "TASK_COMPLETE"}])


@pytest.mark.parametrize(
    "field_name",
    (
        "run_id",
        "work_item_id",
        "activation_id",
        "plan_fingerprint",
        "plan_id",
        "workflow_id",
        "workflow_version",
        "graph_id",
        "claim_id",
        "fencing_token",
        "queue_family_id",
        "stage_kind_id",
        "graph_node_id",
        "runner_binding_id",
    ),
)
def test_runner_dispatch_constructor_rejects_whitespace_only_ids(
    field_name: str,
) -> None:
    with pytest.raises(ValueError):
        _valid_dispatch_envelope(**{field_name: " \t "})


@pytest.mark.parametrize(
    "field_name",
    (
        "run_id",
        "plan_fingerprint",
        "claim_id",
        "fencing_token",
        "stage_kind_id",
        "graph_node_id",
        "runner_binding_id",
        "marker",
    ),
)
def test_runner_result_constructor_rejects_whitespace_only_ids_and_markers(
    field_name: str,
) -> None:
    with pytest.raises(ValueError):
        _valid_result_evidence(**{field_name: " \t "})


def test_runner_constructors_reject_protocol_metadata_as_instance_data() -> None:
    dispatch_constructor = cast(Any, RunnerDispatchEnvelope)
    result_constructor = cast(Any, RunnerResultEvidence)

    with pytest.raises(TypeError):
        dispatch_constructor(
            **_valid_dispatch_kwargs(),
            schema_version=True,
        )
    with pytest.raises(TypeError):
        result_constructor(
            **_valid_result_kwargs(),
            schema_version=True,
        )


def test_runner_constructors_reject_non_mapping_payload_fields_deliberately() -> None:
    with pytest.raises((TypeError, ValueError)):
        _valid_dispatch_envelope(work_item_payload=("not", "mapping"))
    with pytest.raises((TypeError, ValueError)):
        _valid_dispatch_envelope(governance_context=("not", "mapping"))
    with pytest.raises((TypeError, ValueError)):
        _valid_result_evidence(observation_payload=("not", "mapping"))
    with pytest.raises((TypeError, ValueError)):
        _valid_result_evidence(artifact_payload=("not", "mapping"))


def test_runner_result_evidence_parser_is_exact_and_type_strict() -> None:
    payload = _valid_result_raw_payload()
    _ = runner_result_evidence_from_payload(payload)

    payload = dict(_valid_result_raw_payload(), schema_version=1)
    with pytest.raises((TypeError, ValueError)):
        _ = runner_result_evidence_from_payload(payload)

    payload = dict(_valid_result_raw_payload(), schema_version=True)
    with pytest.raises((TypeError, ValueError)):
        _ = runner_result_evidence_from_payload(payload)

    payload = dict(_valid_result_raw_payload(), schema_version=1.0)
    with pytest.raises((TypeError, ValueError)):
        _ = runner_result_evidence_from_payload(payload)

    payload = dict(_valid_result_raw_payload(), generation=True)
    with pytest.raises((TypeError, ValueError)):
        _ = runner_result_evidence_from_payload(payload)

    payload = dict(_valid_result_raw_payload(), generation="0")
    with pytest.raises((TypeError, ValueError)):
        _ = runner_result_evidence_from_payload(payload)

    payload = dict(_valid_result_raw_payload(), schema_version="1")
    with pytest.raises((TypeError, ValueError)):
        _ = runner_result_evidence_from_payload(payload)

    payload = _valid_result_raw_payload()
    payload.pop("observation_payload")
    with pytest.raises((TypeError, ValueError)):
        _ = runner_result_evidence_from_payload(payload)

    payload = _valid_result_raw_payload()
    payload.pop("adapter_provenance")
    with pytest.raises((TypeError, ValueError)):
        _ = runner_result_evidence_from_payload(payload)

    payload = dict(_valid_result_raw_payload(), artifact_payload="not-a-map")
    with pytest.raises((TypeError, ValueError)):
        _ = runner_result_evidence_from_payload(payload)

    payload = dict(_valid_result_raw_payload(), observation_payload="not-a-map")
    with pytest.raises((TypeError, ValueError)):
        _ = runner_result_evidence_from_payload(payload)

    payload = _valid_result_raw_payload()
    payload["wrong"] = "top-level"
    with pytest.raises((TypeError, ValueError)):
        _ = runner_result_evidence_from_payload(payload)

    payload = dict(_valid_result_raw_payload(), run_id="")
    with pytest.raises((TypeError, ValueError)):
        _ = runner_result_evidence_from_payload(payload)

    payload = dict(_valid_result_raw_payload(), stage_kind_id=1)
    with pytest.raises((TypeError, ValueError)):
        _ = runner_result_evidence_from_payload(payload)

    payload = _valid_result_raw_payload()
    payload["artifact_payload"] = {1: "bad-key"}
    with pytest.raises((TypeError, ValueError)):
        _ = runner_result_evidence_from_payload(payload)

    payload = dict(_valid_result_raw_payload(), observation_payload={"good": object()})
    with pytest.raises((TypeError, ValueError)):
        _ = runner_result_evidence_from_payload(payload)


def test_runner_dispatch_payload_from_dataclass_is_identity_preserving() -> None:
    dispatch_a = _valid_dispatch_envelope()
    dispatch_b = _valid_dispatch_envelope()
    evidence_a = _valid_result_evidence()
    evidence_b = _valid_result_evidence()

    assert dispatch_a.payload() == dispatch_b.payload()
    assert evidence_a.payload() == evidence_b.payload()
    assert dispatch_a == _valid_dispatch_envelope()
    assert evidence_a == _valid_result_evidence()


def test_result_evidence_requires_string_value_fields() -> None:
    payload = _valid_result_raw_payload()
    payload["artifact_payload"] = {"artifact_kind": True}

    evidence = runner_result_evidence_from_payload(payload)
    assert evidence.artifact_payload == {"artifact_kind": True}


def test_runner_result_evidence_payload_round_trips_exact_schema_keys() -> None:
    payload = dict(
        _valid_result_raw_payload(),
        adapter_provenance=_valid_adapter_provenance_payload(),
    )
    evidence = runner_result_evidence_from_payload(payload)

    round_trip = evidence.payload()

    assert set(round_trip.keys()) == set(_valid_result_payload().keys())
    assert round_trip["record_kind"] == RUNNER_RESULT_RECORD_KIND
    assert round_trip["schema_version"] == RUNNER_RESULT_SCHEMA_VERSION
    assert round_trip["run_id"] == "run-1"
    assert round_trip["runner_binding_id"] == "kernel_ping.fake_local_runner"
    assert round_trip["adapter_provenance"] == _valid_adapter_provenance_payload()
    assert round_trip["observation_payload"] == {"worker_summary": "ready"}
    assert "adapter_provenance" not in cast(
        Mapping[str, AuthorityValue],
        round_trip["observation_payload"],
    )
    assert "adapter_provenance" not in cast(
        Mapping[str, AuthorityValue],
        round_trip["artifact_payload"],
    )


@pytest.mark.parametrize(
    ("observation_payload", "artifact_payload"),
    ((None, {}), ({}, None), (None, None), ({}, {})),
    ids=("null-empty", "empty-null", "null-null", "empty-empty"),
)
def test_runner_result_evidence_preserves_null_and_present_empty_candidates(
    observation_payload: Mapping[str, AuthorityValue] | None,
    artifact_payload: Mapping[str, AuthorityValue] | None,
) -> None:
    evidence = _valid_result_evidence(
        observation_payload=observation_payload,
        artifact_payload=artifact_payload,
    )

    payload = evidence.payload()
    if observation_payload is None:
        assert payload["observation_payload"] is None
    else:
        assert payload["observation_payload"] == observation_payload
    if artifact_payload is None:
        assert payload["artifact_payload"] is None
    else:
        assert payload["artifact_payload"] == artifact_payload
    parsed = runner_result_evidence_from_payload(payload)
    if observation_payload is None:
        assert parsed.observation_payload is None
    else:
        assert parsed.observation_payload == observation_payload
    if artifact_payload is None:
        assert parsed.artifact_payload is None
    else:
        assert parsed.artifact_payload == artifact_payload
