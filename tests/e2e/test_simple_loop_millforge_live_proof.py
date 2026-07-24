from __future__ import annotations

import json
import os
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from millrace.contracts.compiled_plan import SelectedCompiledPlan
from millrace.contracts.runner import runner_result_evidence_from_payload
from support.e2e_actual_model import (
    EXTERNAL_PACKAGE_ROOT_UNCONFIGURED_REASON,
    HarnessDecision,
    cli_json,
    external_package_root,
    invoke_cli,
    json_output,
    load_runtime_state,
    materialize_adapter_config_snapshot,
    millforge_profile_evidence_payload,
    plan_live_smoke,
    preflight_selected_runner_authority,
    runner_component_pin_evidence,
    scan_for_secret_canary,
    selected_runner_binding_evidence,
    setup_official_package_workspace,
    workflow_package_pin_evidence,
    write_canonical_json_evidence,
)

SOURCE_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ID = "millrace.plus.official"
PACKAGE_VERSION = "0.22.0"
WORKFLOW_ID = "simple_loop"
WORKFLOW_VERSION = "0.1"
ENTRYPOINT = "default"
EXTERNAL_QUEUE = "work_prompt"
FROZEN_PAYLOAD_JSON = (
    '{"body":"In this isolated Millrace E2E workspace, create a file named '
    "e2e-simple-loop-result.txt containing exactly: simple_loop actual-model "
    "smoke passed. Completion definition: the workspace contains "
    "e2e-simple-loop-result.txt and its content is exactly simple_loop "
    "actual-model smoke passed. Constraints: use only the assigned E2E "
    "workspace; do not read or write outside the assigned workspace; do not "
    "use network access; do not use credentials or adapter configuration "
    "secrets; return only selected legal terminal evidence.\","
    '"prompt_id":"e2e-simple-loop-001"}'
)
FROZEN_PAYLOAD_BYTES = FROZEN_PAYLOAD_JSON.encode("utf-8")
FROZEN_PAYLOAD_SHA256 = (
    "sha256:b80be95630e8a61a51e20f570c9028504f59f48c8cb95d90f08799de4e5f51bb"
)


def test_simple_loop_payload_is_the_frozen_e2e_0002_byte_sequence() -> None:
    assert json.loads(FROZEN_PAYLOAD_BYTES) == {
        "prompt_id": "e2e-simple-loop-001",
        "body": (
            "In this isolated Millrace E2E workspace, create a file named "
            "e2e-simple-loop-result.txt containing exactly: simple_loop "
            "actual-model smoke passed. Completion definition: the workspace "
            "contains e2e-simple-loop-result.txt and its content is exactly "
            "simple_loop actual-model smoke passed. Constraints: use only the "
            "assigned E2E workspace; do not read or write outside the assigned "
            "workspace; do not use network access; do not use credentials or "
            "adapter configuration secrets; return only selected legal "
            "terminal evidence."
        ),
    }
    assert "sha256:" + sha256(FROZEN_PAYLOAD_BYTES).hexdigest() == (
        FROZEN_PAYLOAD_SHA256
    )


def test_simple_loop_millforge_artifact_root_is_top_level_and_closed(
    tmp_path: Path,
) -> None:
    workspaces_root = tmp_path / "workspaces"
    valid = workspaces_root / "e2e-mf-simple-loop-20260720T120000Z"
    invalid = (
        workspaces_root / "e2e-simple-loop-wrong-prefix",
        workspaces_root / "nested" / "e2e-mf-simple-loop-run",
        tmp_path / "outside" / "e2e-mf-simple-loop-run",
    )

    assert require_millforge_artifact_root(
        valid,
        workspaces_root=workspaces_root,
    ) == valid
    for artifact_root in invalid:
        with pytest.raises(ValueError, match="e2e-mf-simple-loop"):
            require_millforge_artifact_root(
                artifact_root,
                workspaces_root=workspaces_root,
            )
    assert not valid.exists()
    assert not any(path.exists() for path in invalid)


def test_simple_loop_package_selects_literal_millforge_authority(
    tmp_path: Path,
) -> None:
    setup = setup_official_package_workspace(
        tmp_path / "workspace",
        package_root=_package_root_or_skip(),
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=WORKFLOW_ID,
        workflow_version=WORKFLOW_VERSION,
        entrypoint=ENTRYPOINT,
        command_scope="simple-loop-millforge",
    )
    state = load_runtime_state(setup.workspace)
    plan = state.admitted_plans[setup.plan_fingerprint].selected_plan
    preflight = preflight_selected_runner_authority(
        plan,
        (),
        required_adapter_kind="millforge",
    )

    assert setup.warning_codes == ()
    assert preflight.live_capable is True
    assert preflight.selected_adapter_kinds == ("millforge",)
    _assert_simple_loop_package_authority(plan)


def test_live_row_requires_explicit_package_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MILLRACE_E2E_PACKAGE_ROOT", raising=False)

    with pytest.raises(AssertionError, match="MILLRACE_E2E_PACKAGE_ROOT"):
        _required_live_package_root()


@pytest.mark.live_model
def test_simple_loop_completes_live_through_millforge() -> None:
    decision = plan_live_smoke(
        os.environ,
        repo_root=SOURCE_ROOT,
        required_runner="millforge",
    )
    if not decision.run_live:
        pytest.skip(f"{decision.classification}: {decision.reason}")
    if os.environ.get("MILLRACE_E2E_WORKFLOW_FILTER") not in {None, WORKFLOW_ID}:
        pytest.skip("MILLRACE_E2E_WORKFLOW_FILTER does not include simple_loop")

    result = run_live_simple_loop_millforge_row(decision)

    assert result["classification"] == "closed_successfully"
    assert result["payload_sha256"] == FROZEN_PAYLOAD_SHA256


def require_millforge_artifact_root(
    artifact_root: Path,
    *,
    workspaces_root: Path,
) -> Path:
    if (
        artifact_root.resolve().parent != workspaces_root.resolve()
        or not artifact_root.name.startswith("e2e-mf-simple-loop-")
    ):
        raise ValueError(
            "MILLRACE_E2E_ARTIFACT_ROOT must be a direct child named "
            "e2e-mf-simple-loop-*"
        )
    return artifact_root


def run_live_simple_loop_millforge_row(
    decision: HarnessDecision,
) -> dict[str, object]:
    artifact_root = decision.artifact_root
    profile = decision.millforge_profile
    if artifact_root is None or profile is None:
        raise AssertionError("bounded Millforge preflight evidence is missing")
    package_root = _required_live_package_root()
    workspaces_root = Path(os.environ["MILLRACE_E2E_WORKSPACES_ROOT"])
    artifact_root = require_millforge_artifact_root(
        artifact_root,
        workspaces_root=workspaces_root,
    )
    setup = setup_official_package_workspace(
        artifact_root,
        package_root=package_root,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=WORKFLOW_ID,
        workflow_version=WORKFLOW_VERSION,
        entrypoint=ENTRYPOINT,
        command_scope="simple-loop-millforge",
    )
    state = load_runtime_state(artifact_root)
    plan = state.admitted_plans[setup.plan_fingerprint].selected_plan
    selected = preflight_selected_runner_authority(
        plan,
        (),
        required_adapter_kind="millforge",
    )
    if not selected.live_capable or setup.warning_codes:
        raise AssertionError("package did not select literal Millforge authority")
    _assert_simple_loop_package_authority(plan)

    evidence_root = artifact_root / "evidence"
    evidence_root.mkdir()
    write_canonical_json_evidence(
        evidence_root / "preflight.json",
        {
            "package_id": PACKAGE_ID,
            "package_version": PACKAGE_VERSION,
            "workflow_id": WORKFLOW_ID,
            "workflow_version": WORKFLOW_VERSION,
            "entrypoint": ENTRYPOINT,
            "plan_fingerprint": setup.plan_fingerprint,
            "payload_sha256": FROZEN_PAYLOAD_SHA256,
            "adapter_config_sha256": decision.adapter_config_sha256,
            "profile": millforge_profile_evidence_payload(profile),
            "workflow_package_pin": workflow_package_pin_evidence(plan),
            "runner_bindings": selected_runner_binding_evidence(plan),
        },
    )
    cli_json(
        [
            "--json",
            "--workspace",
            str(artifact_root),
            "queue",
            "enqueue",
            EXTERNAL_QUEUE,
            "--payload-json",
            FROZEN_PAYLOAD_JSON,
            "--plan-fingerprint",
            setup.plan_fingerprint,
            "--input-id",
            "enqueue-simple-loop-millforge-payload",
        ]
    )

    with materialize_adapter_config_snapshot(
        decision,
        artifact_root=artifact_root,
    ) as config_path:
        exit_code, stdout, stderr = invoke_cli(
            [
                "--json",
                "--workspace",
                str(artifact_root),
                "run",
                "daemon",
                "--max-ticks",
                os.environ["MILLRACE_E2E_MAX_TICKS_PER_WORKFLOW"],
                "--idle-sleep",
                "0",
                "--monitor",
                "none",
                "--adapter-kind",
                "millforge",
                "--adapter-config-json",
                str(config_path),
            ]
        )
    daemon_payload = json_output(stdout if exit_code == 0 else stderr)
    reloaded = load_runtime_state(artifact_root)
    durable_reload = _assert_live_simple_loop_aftermath(
        reloaded,
        plan_fingerprint=setup.plan_fingerprint,
    )

    result_path = artifact_root / "e2e-simple-loop-result.txt"
    assert result_path.read_text(encoding="utf-8") == (
        "simple_loop actual-model smoke passed."
    )
    assert tuple(artifact_root.rglob(result_path.name)) == (result_path,)
    status_payload = cli_json(
        [
            "--json",
            "--workspace",
            str(artifact_root),
            "status",
            "--max-events",
            "100",
        ]
    )
    trace_payload = cli_json(
        [
            "--json",
            "--workspace",
            str(artifact_root),
            "trace",
            "show",
            "--max-events",
            "100",
        ]
    )
    secret = os.environ[profile.secret_env_var]
    assert scan_for_secret_canary((artifact_root,), secret) == ()
    result_evidence = {
        "classification": "closed_successfully",
        "payload_sha256": FROZEN_PAYLOAD_SHA256,
        "daemon": daemon_payload,
        "result_file_sha256": sha256(result_path.read_bytes()).hexdigest(),
        "durable_reload": durable_reload,
        "status": status_payload,
        "trace": trace_payload,
    }
    assert secret not in json.dumps(result_evidence, sort_keys=True)
    write_canonical_json_evidence(evidence_root / "result.json", result_evidence)
    write_canonical_json_evidence(
        evidence_root / "cleanup.json",
        {"contained": True, "retained_root": str(artifact_root)},
    )
    assert scan_for_secret_canary((artifact_root,), secret) == ()
    return {
        "classification": "closed_successfully",
        "payload_sha256": FROZEN_PAYLOAD_SHA256,
        "artifact_root": str(artifact_root),
    }


def _assert_simple_loop_package_authority(plan: SelectedCompiledPlan) -> None:
    package_pin = plan.workflow_package_pin
    assert package_pin is not None
    assert str(package_pin.package_id) == PACKAGE_ID
    assert package_pin.package_version == PACKAGE_VERSION
    assert str(package_pin.workflow_id) == WORKFLOW_ID
    assert package_pin.workflow_version == WORKFLOW_VERSION
    assert package_pin.entrypoint == ENTRYPOINT
    assert package_pin.selected_asset_pins
    assert len(plan.runner_bindings) == 4
    for binding in plan.runner_bindings:
        pin = binding.component_pin
        assert binding.adapter_kind == "millforge"
        assert pin is not None
        assert pin.component_id == "millforge-base"
        assert pin.provider_distribution == "millforge"
        assert pin.descriptor_sha256
        assert binding.terminal_result_mappings


def _assert_live_simple_loop_aftermath(
    state: Any,
    *,
    plan_fingerprint: str,
) -> dict[str, object]:
    assert state.default_plan_ref is not None
    assert str(state.default_plan_ref.authority_fingerprint) == plan_fingerprint
    plan = state.admitted_plans[plan_fingerprint].selected_plan
    _assert_simple_loop_package_authority(plan)
    assert state.runner_observations
    durable_observations: list[dict[str, object]] = []
    for observation_id, observation in sorted(state.runner_observations.items()):
        evidence = runner_result_evidence_from_payload(observation.payload)
        run = state.runs[observation.run_id]
        activation = state.activations[run.activation_id]
        assert evidence.run_id == run.run_ref.run_id
        assert evidence.plan_fingerprint == plan_fingerprint
        assert evidence.claim_id == run.run_ref.claim_id
        assert evidence.generation == run.run_ref.generation
        assert evidence.fencing_token == run.run_ref.fencing_token
        assert evidence.stage_kind_id == str(run.stage_kind_id)
        assert evidence.graph_node_id == activation.graph_node_id
        assert evidence.runner_binding_id == str(run.runner_binding_id)
        provenance = evidence.adapter_provenance
        assert provenance is not None
        binding = next(
            candidate
            for candidate in plan.runner_bindings
            if candidate.id == run.runner_binding_id
        )
        assert binding.component_pin is not None
        assert provenance.adapter_kind == "millforge"
        assert provenance.component_descriptor_sha256 == (
            binding.component_pin.descriptor_sha256
        )
        assert provenance.correlation_id == f"run.bounded:{run.run_ref.run_id}"
        assert "adapter_provenance" not in evidence.observation_payload
        assert "adapter_provenance" not in evidence.artifact_payload
        selected_mapping = next(
            mapping
            for mapping in binding.terminal_result_mappings
            if mapping.runner_result_id == evidence.marker
        )
        assert any(
            outcome.id == selected_mapping.outcome_id
            and outcome.stage_kind_id == run.stage_kind_id
            and outcome.marker == evidence.marker
            for outcome in plan.terminal_outcomes
        )
        assert any(
            transition.input_id == observation.created_by_input_id
            and transition.accepted
            for transition in state.transitions
        )
        durable_observations.append(
            {
                "observation_id": str(observation_id),
                "dispatch_identity": {
                    "run_id": evidence.run_id,
                    "plan_fingerprint": evidence.plan_fingerprint,
                    "claim_id": evidence.claim_id,
                    "generation": evidence.generation,
                    "fencing_token": evidence.fencing_token,
                    "stage_kind_id": evidence.stage_kind_id,
                    "graph_node_id": evidence.graph_node_id,
                    "runner_binding_id": evidence.runner_binding_id,
                },
                "adapter_provenance": dict(provenance.payload()),
                "terminal_mapping": {
                    "stage_kind_id": str(selected_mapping.stage_kind_id),
                    "runner_result_id": selected_mapping.runner_result_id,
                    "outcome_id": str(selected_mapping.outcome_id),
                },
                "component_pin": runner_component_pin_evidence(
                    binding.component_pin
                ),
                "created_by_input_id": observation.created_by_input_id,
            }
        )
    assert any(
        str(closed.action_id) == "simple_loop.reviewer.accepted"
        for closed in state.closed_work_items.values()
    )
    assert not state.operator_waits
    assert {
        str(run.run_ref.plan_ref.authority_fingerprint) for run in state.runs.values()
    } == {plan_fingerprint}
    return {
        "reloaded": True,
        "plan_fingerprint": plan_fingerprint,
        "workflow_package_pin": workflow_package_pin_evidence(plan),
        "runner_observations": durable_observations,
        "closed_action_ids": sorted(
            str(closed.action_id) for closed in state.closed_work_items.values()
        ),
        "operator_wait_count": len(state.operator_waits),
    }


def _package_root_or_skip() -> Path:
    package_root = external_package_root(os.environ)
    if package_root is None:
        pytest.skip(EXTERNAL_PACKAGE_ROOT_UNCONFIGURED_REASON)
    return package_root


def _required_live_package_root() -> Path:
    package_root = external_package_root(os.environ)
    if package_root is None:
        raise AssertionError(
            "MILLRACE_E2E_PACKAGE_ROOT is required for live package authority"
        )
    return package_root
