from __future__ import annotations

import json
import os
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from millrace.contracts.compiled_plan import SelectedCompiledPlan
from millrace.contracts.runner import runner_result_evidence_from_payload
from millrace.contracts.state import RuntimeState
from support import vendor_selection
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
    scan_for_secret_canary,
    selected_runner_binding_evidence,
    setup_official_package_workspace,
    workflow_package_pin_evidence,
    write_canonical_json_evidence,
)

REWRITE_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REWRITE_ROOT.parent
GLOBAL_WORKSPACES_ROOT = SOURCE_ROOT.parents[1] / "workspaces"
PACKAGE_ID = "millrace.plus.official"
PACKAGE_VERSION = "0.22.0"
WORKFLOW_ID = "vendor_selection"
WORKFLOW_VERSION = "0.1"
ENTRYPOINT = "default"
EXTERNAL_QUEUE = "purchase_request"
OPERATOR_WAIT_ID = "vendor_selection.award_operator_wait"
OPERATOR_WAIT_SOURCE_ACTION_ID = "vendor_selection.award_decider.operator_required"
FANOUT_IDS = frozenset(
    {
        "vendor_selection.candidate_packager.rubric_fanout",
        "vendor_selection.candidate_packager.conflict_fanout",
    }
)
JOIN_ID = "candidate_evidence_join"
FROZEN_PAYLOAD_JSON = (
    '{"approval_policy_hint":"operator_required","budget_band":"low",'
    '"category":"synthetic_office_supplies","disallowed_vendors":'
    '["Beta Supplies"],"request_id":"e2e-vendor-selection-001",'
    '"requester_label":"local-e2e-operator","required_capabilities":'
    '["standard_office_supplies","net30_invoice"]}'
)
FROZEN_PAYLOAD_SHA256 = (
    "sha256:b0edda53396e9a6b915c16917aeedf5b3473ec1788ae7f5146c5d57b53527ecf"
)


def test_vendor_selection_payload_is_the_frozen_e2e_0005_byte_sequence() -> None:
    assert (
        "sha256:" + sha256(FROZEN_PAYLOAD_JSON.encode()).hexdigest()
        == FROZEN_PAYLOAD_SHA256
    )


def test_vendor_selection_millforge_artifact_root_boundaries(
    tmp_path: Path,
) -> None:
    valid = GLOBAL_WORKSPACES_ROOT / "e2e-mf-vendor-selection-offline-valid"
    assert (
        require_vendor_selection_artifact_root(
            valid,
            workspaces_root=GLOBAL_WORKSPACES_ROOT,
        )
        == valid
    )
    invalid = (
        (GLOBAL_WORKSPACES_ROOT, GLOBAL_WORKSPACES_ROOT / "e2e-wrong-prefix", "e2e-mf"),
        (
            GLOBAL_WORKSPACES_ROOT,
            GLOBAL_WORKSPACES_ROOT / "nested" / valid.name,
            "e2e-mf",
        ),
        (tmp_path, tmp_path / valid.name, "top-level workspaces root"),
    )
    for workspaces_root, artifact_root, match in invalid:
        with pytest.raises(ValueError, match=match):
            require_vendor_selection_artifact_root(
                artifact_root,
                workspaces_root=workspaces_root,
            )
    assert not valid.exists()
    with pytest.raises(ValueError, match="must not already exist"):
        require_vendor_selection_artifact_root(
            REWRITE_ROOT,
            workspaces_root=GLOBAL_WORKSPACES_ROOT,
        )


def test_vendor_selection_package_selects_literal_millforge_authority(
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
        command_scope="vendor-selection-millforge",
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
    _assert_vendor_selection_package_authority(plan)


def test_fanout_evidence_accepts_multiple_candidates() -> None:
    state, plan, fingerprint = vendor_selection.multi_candidate_schema_covered_state()

    evidence = _fanout_evidence(
        state,
        plan=plan,
        plan_fingerprint=fingerprint,
    )

    assert len(evidence) == 4
    assert {
        fanout_id: sum(row["fanout_id"] == fanout_id for row in evidence)
        for fanout_id in FANOUT_IDS
    } == {fanout_id: 2 for fanout_id in FANOUT_IDS}


def test_live_row_requires_explicit_package_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MILLRACE_E2E_PACKAGE_ROOT", raising=False)

    with pytest.raises(AssertionError, match="MILLRACE_E2E_PACKAGE_ROOT"):
        _required_live_package_root()


def test_live_row_requires_vendor_selection_tick_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for invalid_bound in ("15", "17"):
        monkeypatch.setenv("MILLRACE_E2E_MAX_TICKS_PER_WORKFLOW", invalid_bound)

        with pytest.raises(AssertionError, match="exactly 16"):
            _required_live_tick_bound()


@pytest.mark.live_model
def test_vendor_selection_reaches_operator_wait_live_through_millforge() -> None:
    decision = plan_live_smoke(
        os.environ,
        repo_root=REWRITE_ROOT,
        required_runner="millforge",
    )
    if not decision.run_live:
        pytest.skip(f"{decision.classification}: {decision.reason}")
    if os.environ.get("MILLRACE_E2E_WORKFLOW_FILTER") not in {
        None,
        WORKFLOW_ID,
    }:
        pytest.skip("MILLRACE_E2E_WORKFLOW_FILTER does not include vendor_selection")

    result = run_live_vendor_selection_millforge_row(decision)

    assert result["classification"] == "selected_operator_wait"
    assert result["payload_sha256"] == FROZEN_PAYLOAD_SHA256


def require_vendor_selection_artifact_root(
    artifact_root: Path,
    *,
    workspaces_root: Path,
) -> Path:
    if workspaces_root.resolve() != GLOBAL_WORKSPACES_ROOT.resolve():
        raise ValueError(
            "MILLRACE_E2E_WORKSPACES_ROOT must be the top-level workspaces root"
        )
    if artifact_root.exists():
        raise ValueError("MILLRACE_E2E_ARTIFACT_ROOT must not already exist")
    if (
        artifact_root.resolve().parent != workspaces_root.resolve()
        or not artifact_root.name.startswith("e2e-mf-vendor-selection-")
    ):
        raise ValueError(
            "MILLRACE_E2E_ARTIFACT_ROOT must be a direct child named "
            "e2e-mf-vendor-selection-*"
        )
    return artifact_root


def run_live_vendor_selection_millforge_row(
    decision: HarnessDecision,
) -> dict[str, object]:
    artifact_root = decision.artifact_root
    profile = decision.millforge_profile
    if artifact_root is None or profile is None:
        raise AssertionError("bounded Millforge preflight evidence is missing")
    tick_bound = _required_live_tick_bound()
    package_root = _required_live_package_root()
    workspaces_root = Path(os.environ["MILLRACE_E2E_WORKSPACES_ROOT"])
    artifact_root = require_vendor_selection_artifact_root(
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
        command_scope="vendor-selection-millforge",
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
    _assert_vendor_selection_package_authority(plan)

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
            "enqueue-vendor-selection-millforge-payload",
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
                str(tick_bound),
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
    daemon_evidence = _daemon_evidence(daemon_payload, tick_bound=tick_bound)
    if exit_code != 0:
        raise AssertionError(f"Millforge daemon failed: {daemon_evidence}")

    reloaded = load_runtime_state(artifact_root)
    durable_reload = _assert_live_vendor_selection_aftermath(
        reloaded,
        plan_fingerprint=setup.plan_fingerprint,
    )
    status_payload = _read_projection(artifact_root, "status")
    trace_payload = _read_projection(artifact_root, "trace", "show")
    status_evidence, trace_evidence = _assert_cli_status_and_trace(
        status_payload,
        trace_payload,
        plan_fingerprint=setup.plan_fingerprint,
    )
    assert (
        scan_for_secret_canary((artifact_root,), os.environ[profile.secret_env_var])
        == ()
    )
    result_evidence = {
        "classification": "selected_operator_wait",
        "payload_sha256": FROZEN_PAYLOAD_SHA256,
        "adapter_config_sha256": decision.adapter_config_sha256,
        "profile": millforge_profile_evidence_payload(profile),
        "daemon": daemon_evidence,
        "durable_reload": durable_reload,
        "status": status_evidence,
        "trace": trace_evidence,
    }
    assert os.environ[profile.secret_env_var] not in json.dumps(
        result_evidence, sort_keys=True
    )
    write_canonical_json_evidence(evidence_root / "result.json", result_evidence)
    write_canonical_json_evidence(
        evidence_root / "cleanup.json",
        {
            "contained": True,
            "raw_adapter_config_retained": False,
            "retained_root": str(artifact_root),
        },
    )
    assert (
        scan_for_secret_canary((artifact_root,), os.environ[profile.secret_env_var])
        == ()
    )
    return {
        "classification": "selected_operator_wait",
        "payload_sha256": FROZEN_PAYLOAD_SHA256,
        "artifact_root": str(artifact_root),
    }


def _assert_vendor_selection_package_authority(plan: SelectedCompiledPlan) -> None:
    package_pin = plan.workflow_package_pin
    assert package_pin is not None
    assert str(package_pin.package_id) == PACKAGE_ID
    assert package_pin.package_version == PACKAGE_VERSION
    assert str(package_pin.workflow_id) == WORKFLOW_ID
    assert package_pin.workflow_version == WORKFLOW_VERSION
    assert package_pin.entrypoint == ENTRYPOINT
    assert package_pin.selected_asset_pins
    assert plan.runner_bindings
    for binding in plan.runner_bindings:
        pin = binding.component_pin
        assert binding.adapter_kind == "millforge"
        assert pin is not None
        assert pin.component_id == "millforge-base"
        assert pin.provider_distribution == "millforge"
        assert pin.descriptor_sha256
        assert binding.terminal_result_mappings
    assert {str(fanout.id) for fanout in plan.fanout_declarations} == FANOUT_IDS
    assert {str(join.id) for join in plan.join_declarations} == {JOIN_ID}
    assert {str(wait.id) for wait in plan.operator_waits} == {OPERATOR_WAIT_ID}
    wait = plan.operator_waits[0]
    assert tuple(str(action_id) for action_id in wait.source_action_ids) == (
        OPERATOR_WAIT_SOURCE_ACTION_ID,
    )
    assert wait.actor_kind == "local_operator"
    assert wait.allowed_resolution_kinds == (
        "resume_recorded_source",
        "revise_recorded_source",
    )


def _assert_live_vendor_selection_aftermath(
    state: RuntimeState,
    *,
    plan_fingerprint: str,
) -> dict[str, object]:
    admitted = state.admitted_plans[plan_fingerprint]
    assert state.default_plan_ref == admitted.plan_ref
    plan = admitted.selected_plan
    _assert_vendor_selection_package_authority(plan)
    runner_observations = _runner_observation_evidence(
        state,
        plan=plan,
        plan_fingerprint=plan_fingerprint,
    )
    fanouts = _fanout_evidence(
        state,
        plan=plan,
        plan_fingerprint=plan_fingerprint,
    )
    join = _join_evidence(state, plan=plan, plan_fingerprint=plan_fingerprint)
    wait = _operator_wait_evidence(
        state,
        plan=plan,
        plan_fingerprint=plan_fingerprint,
        join=join,
    )
    assert not state.operator_interventions
    assert not state.effect_proposals
    assert not state.effect_reconciliations
    assert {
        str(run.run_ref.plan_ref.authority_fingerprint) for run in state.runs.values()
    } == {plan_fingerprint}
    return {
        "reloaded": True,
        "plan_fingerprint": plan_fingerprint,
        "workflow_package_pin": workflow_package_pin_evidence(plan),
        "runner_bindings": selected_runner_binding_evidence(plan),
        "runner_observations": runner_observations,
        "fanouts": fanouts,
        "join": join,
        "operator_wait": wait,
        "operator_intervention_count": 0,
        "effect_proposal_count": 0,
        "effect_reconciliation_count": 0,
    }


def _runner_observation_evidence(
    state: RuntimeState,
    *,
    plan: SelectedCompiledPlan,
    plan_fingerprint: str,
) -> list[dict[str, object]]:
    assert state.runner_observations
    retained: list[dict[str, object]] = []
    for observation_id, observation in sorted(state.runner_observations.items()):
        evidence = runner_result_evidence_from_payload(observation.payload)
        run = state.runs[observation.run_id]
        activation = state.activations[run.activation_id]
        binding = next(
            item for item in plan.runner_bindings if item.id == run.runner_binding_id
        )
        pin = binding.component_pin
        provenance = evidence.adapter_provenance
        assert pin is not None and provenance is not None
        assert evidence.run_id == run.run_ref.run_id
        assert evidence.plan_fingerprint == plan_fingerprint
        assert evidence.claim_id == run.run_ref.claim_id
        assert evidence.generation == run.run_ref.generation
        assert evidence.fencing_token == run.run_ref.fencing_token
        assert evidence.stage_kind_id == str(run.stage_kind_id)
        assert evidence.graph_node_id == activation.graph_node_id
        assert evidence.runner_binding_id == str(run.runner_binding_id)
        assert (provenance.adapter_kind, provenance.component_descriptor_sha256) == (
            "millforge",
            pin.descriptor_sha256,
        )
        assert provenance.correlation_id == f"run.bounded:{run.run_ref.run_id}"
        mapping = next(
            item
            for item in binding.terminal_result_mappings
            if item.runner_result_id == evidence.marker
        )
        assert any(
            outcome.id == mapping.outcome_id
            and outcome.stage_kind_id == run.stage_kind_id
            and outcome.marker == evidence.marker
            for outcome in plan.terminal_outcomes
        )
        assert any(
            item.input_id == observation.created_by_input_id and item.accepted
            for item in state.transitions
        )
        retained.append(
            {
                "observation_id": str(observation_id),
                "dispatch_identity": _pick_record(
                    evidence,
                    "run_id",
                    "plan_fingerprint",
                    "claim_id",
                    "generation",
                    "fencing_token",
                    "stage_kind_id",
                    "graph_node_id",
                    "runner_binding_id",
                ),
                "adapter_provenance": dict(provenance.payload()),
                "terminal_mapping": {
                    "stage_kind_id": str(mapping.stage_kind_id),
                    "runner_result_id": mapping.runner_result_id,
                    "outcome_id": str(mapping.outcome_id),
                },
            }
        )
    return retained


def _fanout_evidence(
    state: RuntimeState,
    *,
    plan: SelectedCompiledPlan,
    plan_fingerprint: str,
) -> list[dict[str, object]]:
    retained: list[dict[str, object]] = []
    for fanout_id in sorted(FANOUT_IDS):
        records = tuple(
            record
            for record in state.fanout_records.values()
            if str(record.fanout_id) == fanout_id
        )
        assert records
        declaration = next(
            item for item in plan.fanout_declarations if str(item.id) == fanout_id
        )
        generated_route = next(
            item
            for item in plan.generated_work_routes
            if item.id == declaration.target_route_id
        )
        for record in sorted(records, key=lambda item: item.record_id):
            retained.append(
                _fanout_record_evidence(
                    state,
                    plan=plan,
                    plan_fingerprint=plan_fingerprint,
                    declaration=declaration,
                    generated_route=generated_route,
                    record=record,
                )
            )
    return retained


def _fanout_record_evidence(
    state: RuntimeState,
    *,
    plan: SelectedCompiledPlan,
    plan_fingerprint: str,
    declaration: Any,
    generated_route: Any,
    record: Any,
) -> dict[str, object]:
    dependencies = tuple(
        dependency
        for dependency in state.work_dependencies.values()
        if dependency.fanout_record_id == record.record_id
        and dependency.dependent_work_item_id == record.target_work_item_id
    )
    assert len(dependencies) == 1
    dependency = dependencies[0]
    route = _route_evidence(
        state,
        plan=plan,
        plan_ref=record.selected_plan_ref,
        source_run_id=record.source_run_id,
        source_work_item_id=record.source_work_item_id,
        target_work_item_id=record.target_work_item_id,
        target_activation_id=record.target_activation_id,
        created_by_input_id=record.created_by_input_id,
        action_id=str(declaration.source_action_id),
        input_kind="workflow.fanout_from_artifact",
    )
    assert (
        record.selected_plan_ref.authority_fingerprint,
        record.source_action_id,
        record.target_queue_family_id,
        record.target_stage_kind_id,
        record.target_graph_node_id,
    ) == (
        plan_fingerprint,
        declaration.source_action_id,
        generated_route.queue_family_id,
        generated_route.stage_kind_id,
        generated_route.graph_node_id,
    )
    assert tuple(
        route["target"][key]
        for key in (
            "queue_family_id",
            "stage_kind_id",
            "graph_node_id",
            "runner_binding_id",
        )
    ) == (
        str(generated_route.queue_family_id),
        str(generated_route.stage_kind_id),
        generated_route.graph_node_id,
        str(generated_route.runner_binding_id),
    )
    assert (
        dependency.dependency_work_item_id,
        dependency.selected_plan_ref,
        dependency.lineage_id,
        dependency.created_by_input_id,
    ) == (
        record.source_work_item_id,
        record.selected_plan_ref,
        record.lineage_id,
        record.created_by_input_id,
    )
    assert route["source"]["lineage_id"] == record.lineage_id
    assert route["target"]["lineage_id"] == record.lineage_id
    return {
        "record_id": record.record_id,
        "fanout_id": str(record.fanout_id),
        "source_action_id": str(declaration.source_action_id),
        "target_route_id": declaration.target_route_id,
        "route": route,
        "dependency": _pick_record(
            dependency,
            "dependency_id",
            "dependent_work_item_id",
            "dependency_work_item_id",
            "fanout_record_id",
            "created_by_input_id",
        ),
    }


def _join_evidence(
    state: RuntimeState,
    *,
    plan: SelectedCompiledPlan,
    plan_fingerprint: str,
) -> dict[str, object]:
    declaration = next(
        join for join in plan.join_declarations if str(join.id) == JOIN_ID
    )
    routes = tuple(
        route for route in state.activation_routes if str(route.action_id) == JOIN_ID
    )
    assert len(routes) == 1
    route = routes[0]
    target_routes = tuple(
        item
        for item in plan.generated_work_routes
        if item.stage_kind_id == declaration.target_stage_kind_id
    )
    assert len(target_routes) == 1
    target_route = target_routes[0]
    source_run = state.runs[route.source_run_id]
    evidence = _route_evidence(
        state,
        plan=plan,
        plan_ref=source_run.run_ref.plan_ref,
        source_run_id=route.source_run_id,
        source_work_item_id=route.source_work_item_id,
        target_work_item_id=route.target_work_item_id,
        target_activation_id=route.target_activation_id,
        created_by_input_id=route.created_by_input_id,
        action_id=JOIN_ID,
        input_kind="workflow.join_from_artifact",
    )
    assert evidence["plan_ref"]["authority_fingerprint"] == plan_fingerprint
    assert evidence["target"]["queue_family_id"] == str(target_route.queue_family_id)
    assert evidence["target"]["stage_kind_id"] == str(target_route.stage_kind_id)
    assert evidence["target"]["graph_node_id"] == target_route.graph_node_id
    assert evidence["target"]["runner_binding_id"] == str(
        target_route.runner_binding_id
    )
    return {
        "join_id": JOIN_ID,
        "target_route_id": target_route.id,
        "route": evidence,
    }


def _route_evidence(
    state: RuntimeState,
    *,
    plan: SelectedCompiledPlan,
    plan_ref: Any,
    source_run_id: str,
    source_work_item_id: str,
    target_work_item_id: str,
    target_activation_id: str,
    created_by_input_id: str,
    action_id: str,
    input_kind: str,
) -> dict[str, Any]:
    routes = tuple(
        route
        for route in state.activation_routes
        if route.source_run_id == source_run_id
        and route.source_work_item_id == source_work_item_id
        and route.target_work_item_id == target_work_item_id
        and route.target_activation_id == target_activation_id
        and route.created_by_input_id == created_by_input_id
        and str(route.action_id) == action_id
    )
    assert len(routes) == 1
    route = routes[0]
    run = state.runs[source_run_id]
    source_work = state.work_items[source_work_item_id]
    source_activation = state.activations[run.activation_id]
    target_work = state.work_items[target_work_item_id]
    target_activation = state.activations[target_activation_id]
    receipt = state.receipts[created_by_input_id]
    transitions = tuple(
        item for item in state.transitions if item.record_id == receipt.transition_id
    )
    assert len(transitions) == 1
    transition = transitions[0]
    admitted = state.admitted_plans[str(plan_ref.authority_fingerprint)]
    assert admitted.plan_ref == plan_ref
    assert admitted.selected_plan == plan
    assert {
        run.run_ref.plan_ref,
        source_work.ref.plan_ref,
        source_activation.plan_ref,
        target_work.ref.plan_ref,
        target_activation.plan_ref,
    } == {plan_ref}
    assert (run.run_ref.work_item_id, run.work_item_id) == (source_work_item_id,) * 2
    assert source_work.ref.work_item_id == source_work_item_id
    assert source_activation.work_item_id == source_work_item_id
    assert (source_activation.stage_kind_id, source_activation.runner_binding_id) == (
        run.stage_kind_id,
        run.runner_binding_id,
    )
    assert source_work.lineage_id == source_activation.lineage_id
    assert target_work.lineage_id == target_activation.lineage_id
    assert target_activation.work_item_id == target_work_item_id
    assert (receipt.receipt_ref.input_id, receipt.accepted) == (
        created_by_input_id,
        True,
    )
    assert (
        transition.input_id,
        transition.input_kind,
        transition.input_family,
        transition.accepted,
    ) == (created_by_input_id, input_kind, "workflow_kernel_command", True)
    return {
        "record_id": route.record_id,
        "action_id": action_id,
        "plan_ref": _plan_ref_evidence(plan_ref),
        "source": {
            "run_id": run.run_ref.run_id,
            "work_item_id": source_work_item_id,
            "claim_id": run.run_ref.claim_id,
            "generation": run.run_ref.generation,
            "fencing_token": run.run_ref.fencing_token,
            "activation_id": source_activation.activation_id,
            "graph_node_id": source_activation.graph_node_id,
            "stage_kind_id": str(source_activation.stage_kind_id),
            "runner_binding_id": str(source_activation.runner_binding_id),
            "lineage_id": source_work.lineage_id,
        },
        "target": {
            "work_item_id": target_work.ref.work_item_id,
            "activation_id": target_activation.activation_id,
            "queue_family_id": str(target_work.queue_family_id),
            "graph_node_id": target_activation.graph_node_id,
            "stage_kind_id": str(target_activation.stage_kind_id),
            "runner_binding_id": str(target_activation.runner_binding_id),
            "lineage_id": target_work.lineage_id,
        },
        "receipt": {
            "input_id": receipt.receipt_ref.input_id,
            "input_payload_digest": receipt.receipt_ref.input_payload_digest,
            "transition_id": receipt.transition_id,
        },
        "transition": _pick_record(transition, "record_id", "input_kind", "accepted"),
    }


def _plan_ref_evidence(plan_ref: Any) -> dict[str, object]:
    return {
        "plan_id": plan_ref.plan_id,
        "authority_fingerprint": str(plan_ref.authority_fingerprint),
        "plan_format_version": plan_ref.plan_format_version,
    }


def _pick_record(record: Any, *names: str) -> dict[str, object]:
    return {name: getattr(record, name) for name in names}


def _daemon_evidence(
    payload: dict[str, Any],
    *,
    tick_bound: int,
) -> dict[str, object]:
    details = payload.get("data") or payload.get("details") or {}
    assert isinstance(details, dict)
    last_result = details.get("last_result") or {}
    assert isinstance(last_result, dict)
    return {
        "settings": {
            "max_ticks": tick_bound,
            "idle_sleep": 0,
            "monitor": "none",
            "adapter_kind": "millforge",
        },
        "code": payload.get("code"),
        "iterations": details.get("iterations"),
        "units_started": details.get("units_started"),
        "units_succeeded": details.get("units_succeeded"),
        "units_refused": details.get("units_refused"),
        "adapter_failures": details.get("adapter_failures"),
        "lifecycle_transitions_applied": details.get("lifecycle_transitions_applied"),
        "stopped_reason": details.get("stopped_reason"),
        "last_result_code": last_result.get("code"),
    }


def _read_projection(workspace: Path, *command: str) -> dict[str, Any]:
    return cli_json(
        ["--json", "--workspace", str(workspace), *command, "--max-events", "200"]
    )


def _assert_cli_status_and_trace(
    status_payload: dict[str, Any],
    trace_payload: dict[str, Any],
    *,
    plan_fingerprint: str,
) -> tuple[dict[str, object], dict[str, object]]:
    status = status_payload["data"]
    assert status["selected_plan"]["authority_fingerprint"] == plan_fingerprint
    waits = status["operator_waits"]
    assert len(waits) == 1
    assert (
        waits[0]["operator_wait_id"],
        waits[0]["source_action_id"],
        waits[0]["selected_plan_fingerprint"],
        waits[0]["status"],
    ) == (OPERATOR_WAIT_ID, OPERATOR_WAIT_SOURCE_ACTION_ID, plan_fingerprint, "active")
    joins = [row for row in status["joins"] if row["join_id"] == JOIN_ID]
    assert len(joins) == 1
    assert (
        joins[0]["selected_plan_fingerprint"],
        joins[0]["ready"],
        joins[0]["missing_artifact_schema_ids"],
    ) == (plan_fingerprint, True, [])
    events = trace_payload["data"]["events"]
    common = {
        "source": "trace",
        "plan_fingerprint": plan_fingerprint,
        "disposition": "accepted",
    }
    join_events = [
        event
        for event in events
        if all(event[key] == value for key, value in common.items())
        and event["input_kind"] == "workflow.join_from_artifact"
        and event["authority_source"] == "join_declaration"
    ]
    wait_events = [
        event
        for event in events
        if all(event[key] == value for key, value in common.items())
        and event["input_kind"] == "workflow.runner_result_observed"
        and event["action_id"] == OPERATOR_WAIT_SOURCE_ACTION_ID
        and event["authority_source"] == "terminal_action"
    ]
    assert len(join_events) == 1
    assert len(wait_events) == 1
    return (
        {
            "selected_plan": status["selected_plan"],
            "joins": joins,
            "operator_waits": waits,
        },
        {"events": [*join_events, *wait_events]},
    )


def _operator_wait_evidence(
    state: RuntimeState,
    *,
    plan: SelectedCompiledPlan,
    plan_fingerprint: str,
    join: dict[str, object],
) -> dict[str, object]:
    assert len(state.operator_waits) == 1
    wait = next(iter(state.operator_waits.values()))
    admitted = state.admitted_plans[plan_fingerprint]
    declaration = next(
        item for item in plan.operator_waits if item.id == wait.operator_wait_id
    )
    assert (
        str(wait.operator_wait_id),
        wait.status,
        str(wait.selected_plan_fingerprint),
    ) == (OPERATOR_WAIT_ID, "active", plan_fingerprint)
    assert wait.selected_plan_ref == admitted.plan_ref
    assert (str(wait.source_action_id), declaration.actor_kind) == (
        OPERATOR_WAIT_SOURCE_ACTION_ID,
        "local_operator",
    )
    assert wait.source_action_id in declaration.source_action_ids
    source_run = state.runs[wait.source_run_id]
    source_work = state.work_items[wait.source_work_item_id]
    source_activation = state.activations[wait.source_activation_id]
    assert source_run.run_ref.plan_ref == admitted.plan_ref
    assert source_run.run_ref.run_id == wait.source_run_id
    assert source_run.work_item_id == wait.source_work_item_id
    assert source_run.activation_id == wait.source_activation_id
    assert source_work.ref.plan_ref == admitted.plan_ref
    assert source_work.lineage_id == wait.lineage_id
    assert source_work.queue_family_id == wait.source_queue_family_id
    assert source_activation.plan_ref == admitted.plan_ref
    assert source_activation.work_item_id == wait.source_work_item_id
    assert source_activation.lineage_id == wait.lineage_id
    assert source_activation.queue_family_id == wait.source_queue_family_id
    assert source_activation.graph_node_id == wait.source_graph_node_id
    assert source_activation.stage_kind_id == wait.source_stage_kind_id
    assert source_activation.runner_binding_id == wait.source_runner_binding_id
    join_target = join["route"]["target"]
    assert (
        join_target["work_item_id"],
        join_target["activation_id"],
        join_target["graph_node_id"],
        join_target["stage_kind_id"],
        join_target["runner_binding_id"],
    ) == (
        wait.source_work_item_id,
        wait.source_activation_id,
        wait.source_graph_node_id,
        str(wait.source_stage_kind_id),
        str(wait.source_runner_binding_id),
    )
    assert (
        wait.resolved_input_id,
        wait.resolved_input_payload_digest,
        wait.actor_id,
        wait.actor_kind,
        wait.resolution_kind,
    ) == (None,) * 5
    assert wait.closed_work_item_ids == ()
    assert wait.source_work_item_id not in state.closed_work_items
    observations = tuple(
        observation
        for observation in state.runner_observations.values()
        if observation.run_id == wait.source_run_id
    )
    assert len(observations) == 1
    observed = runner_result_evidence_from_payload(observations[0].payload)
    binding = next(
        item
        for item in plan.runner_bindings
        if item.id == wait.source_runner_binding_id
    )
    observed_mappings = tuple(
        item
        for item in binding.terminal_result_mappings
        if item.stage_kind_id == wait.source_stage_kind_id
        and item.runner_result_id == observed.marker
    )
    assert len(observed_mappings) == 1
    assert observed_mappings[0].runner_result_id == "OPERATOR_REQUIRED"
    assert str(observed_mappings[0].outcome_id) == OPERATOR_WAIT_SOURCE_ACTION_ID
    retained = _pick_record(
        wait,
        "wait_id",
        "status",
        "source_run_id",
        "source_work_item_id",
        "source_activation_id",
        "source_graph_node_id",
        "lineage_id",
    )
    retained.update(
        {
            "operator_wait_id": str(wait.operator_wait_id),
            "source_action_id": str(wait.source_action_id),
            "source_stage_kind_id": str(wait.source_stage_kind_id),
            "source_queue_family_id": str(wait.source_queue_family_id),
            "source_runner_binding_id": str(wait.source_runner_binding_id),
            "allowed_resolution_kinds": list(declaration.allowed_resolution_kinds),
            "actor_kind_requirement": declaration.actor_kind,
            "selected_plan_ref": _plan_ref_evidence(wait.selected_plan_ref),
            "observed_terminal_mapping": {
                "runner_result_id": observed_mappings[0].runner_result_id,
                "outcome_id": str(observed_mappings[0].outcome_id),
            },
            "source_closed": False,
        }
    )
    return retained


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


def _required_live_tick_bound() -> int:
    tick_bound = int(os.environ["MILLRACE_E2E_MAX_TICKS_PER_WORKFLOW"])
    if tick_bound != 16:
        raise AssertionError("MILLRACE_E2E_MAX_TICKS_PER_WORKFLOW must be exactly 16")
    return tick_bound
