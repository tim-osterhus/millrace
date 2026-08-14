from __future__ import annotations

import ast
import json
import logging
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from millrace.contracts.compiled_plan import AuthorityValue
from millrace.contracts.runner import RunnerDispatchEnvelope


def _valid_dispatch_envelope(**overrides: object) -> RunnerDispatchEnvelope:
    values: dict[str, object] = {
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
        "runner_binding_id": "kernel_ping.codex_runner",
        "external_enqueue_route_id": "kernel_ping.external_prompt",
        "entrypoint_asset_id": "kernel_ping.taskmaster_prompt",
        "skill_asset_ids": ("kernel_ping.tdd_core",),
        "artifact_schema_ids": ("kernel_ping.task_artifact",),
        "work_item_payload": {"body": "proof it out"},
        "governance_context": {"operator": "local"},
        "terminal_options": (
            {
                "outcome_id": "kernel_ping.taskmaster.complete",
                "marker": "TASK_COMPLETE",
                "action_id": "kernel_ping.taskmaster.emit_task",
                "action_kind": "route",
                "artifact_schema_id": "kernel_ping.task_artifact",
            },
        ),
        "selected_join_evidence": None,
        "selected_wait_evidence": None,
        "context_checkout": None,
    }
    values.update(overrides)
    return RunnerDispatchEnvelope(
        run_id=cast(str, values["run_id"]),
        session_id=cast(str, values["session_id"]),
        dispatch_generation=cast(int, values["dispatch_generation"]),
        session_fencing_token=cast(str, values["session_fencing_token"]),
        work_item_id=cast(str, values["work_item_id"]),
        activation_id=cast(str, values["activation_id"]),
        plan_fingerprint=cast(str, values["plan_fingerprint"]),
        plan_id=cast(str, values["plan_id"]),
        workflow_id=cast(str, values["workflow_id"]),
        workflow_version=cast(str, values["workflow_version"]),
        graph_id=cast(str, values["graph_id"]),
        claim_id=cast(str, values["claim_id"]),
        generation=cast(int, values["generation"]),
        fencing_token=cast(str, values["fencing_token"]),
        queue_family_id=cast(str, values["queue_family_id"]),
        stage_kind_id=cast(str, values["stage_kind_id"]),
        graph_node_id=cast(str, values["graph_node_id"]),
        runner_binding_id=cast(str, values["runner_binding_id"]),
        external_enqueue_route_id=cast(
            str | None,
            values["external_enqueue_route_id"],
        ),
        entrypoint_asset_id=cast(str | None, values["entrypoint_asset_id"]),
        skill_asset_ids=cast(tuple[str, ...], values["skill_asset_ids"]),
        artifact_schema_ids=cast(tuple[str, ...], values["artifact_schema_ids"]),
        work_item_payload=cast(dict[str, AuthorityValue], values["work_item_payload"]),
        governance_context=cast(
            dict[str, AuthorityValue],
            values["governance_context"],
        ),
        terminal_options=cast(
            tuple[dict[str, AuthorityValue], ...],
            values["terminal_options"],
        ),
        selected_join_evidence=cast(
            dict[str, AuthorityValue] | None,
            values["selected_join_evidence"],
        ),
        selected_wait_evidence=cast(
            dict[str, AuthorityValue] | None,
            values["selected_wait_evidence"],
        ),
        context_checkout=cast(
            dict[str, AuthorityValue] | None,
            values["context_checkout"],
        ),
    )


def _context_checkout() -> dict[str, str]:
    return {
        "manifest_digest": "sha256:" + "a" * 64,
        "binding_id": "binding-1",
        "router_asset_id": "router-1",
        "checkout_relative_path": "checkout/session-1/1",
        "router_relative_path": "checkout/session-1/1/CONTEXT.md",
    }


_CORRELATION_IDENTITY = (
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
)
_DUPLICATE_PAYLOAD_DIGEST = "sha256:" + "b" * 64


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
                "payload_digest": _DUPLICATE_PAYLOAD_DIGEST,
                "payload": {"score": 8},
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
                "payload_digest": _DUPLICATE_PAYLOAD_DIGEST,
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
        "source_artifact_id": "artifact-1",
        "source_artifact_schema_id": "SourceRecord",
        "source_artifact_digest": "sha256:" + "d" * 64,
        "source_artifact_payload": {"detail": "selected"},
        "source_action_id": "action-1",
        "source_run_id": "run-source-1",
        "source_work_item_id": "work-source-1",
    }
    evidence.update(overrides)
    return evidence


def _default_selected_projection(
    dispatch: RunnerDispatchEnvelope,
) -> tuple[tuple[object, ...], tuple[object, ...]]:
    from millrace.contracts.compiled_plan import (
        ArtifactSchemaDeclaration,
        RunnerTerminalResultMapping,
    )
    from millrace.contracts.ids import ArtifactSchemaId, OutcomeId, StageKindId

    mappings = tuple(
        RunnerTerminalResultMapping(
            stage_kind_id=StageKindId(dispatch.stage_kind_id),
            runner_result_id=f"RESULT_{option['outcome_id']}",
            outcome_id=OutcomeId(str(option["outcome_id"])),
        )
        for option in dispatch.terminal_options
    )
    schema_ids = sorted(
        {
            str(option["artifact_schema_id"])
            for option in dispatch.terminal_options
            if option["artifact_schema_id"] is not None
        }
    )
    schemas = tuple(
        ArtifactSchemaDeclaration(
            id=ArtifactSchemaId(schema_id),
            schema={"type": "object"},
            presentation={},
        )
        for schema_id in schema_ids
    )
    return mappings, schemas


def _request(
    *,
    adapter_id: str = "codex-default",
    selected_asset_material: dict[str, AuthorityValue] | None = None,
    selected_adapter_kind: str = "codex",
    cancellation_token: str | None = None,
    dispatch_envelope: RunnerDispatchEnvelope | None = None,
    redaction_policy: object | None = None,
    selected_terminal_result_mappings: object | None = None,
    selected_artifact_schemas: object | None = None,
) -> object:
    from millrace.adapters.runner_contract import (
        AdapterInvocationRequest,
        RedactionPolicy,
    )
    from millrace.contracts.compiled_plan import RunnerComponentPin

    dispatch = dispatch_envelope or _valid_dispatch_envelope()
    default_mappings, default_schemas = _default_selected_projection(dispatch)
    policy = redaction_policy or RedactionPolicy(
        policy_id="redact-default",
        secret_tokens=("token-secret",),
    )
    mappings = tuple(
        selected_terminal_result_mappings
        if selected_terminal_result_mappings is not None
        else default_mappings
    )
    schemas = tuple(
        selected_artifact_schemas
        if selected_artifact_schemas is not None
        else default_schemas
    )
    request = AdapterInvocationRequest(
        adapter_id=adapter_id,
        selected_runner_binding_id=dispatch.runner_binding_id,
        selected_adapter_kind=selected_adapter_kind,
        dispatch_envelope=dispatch,

        session_id=dispatch.session_id,

        dispatch_generation=dispatch.dispatch_generation,

        session_fencing_token=dispatch.session_fencing_token,
        timeout_seconds=5,
        correlation_id="corr-1",
        redaction_policy=cast(RedactionPolicy, policy),
        selected_asset_material=selected_asset_material or _asset_material(),
        cancellation_token=cancellation_token,
        selected_terminal_result_mappings=cast(Any, mappings),
        selected_artifact_schemas=cast(Any, schemas),
    )
    object.__setattr__(
        request,
        "selected_component_pin",
        RunnerComponentPin(
            component_kind="runner",
            component_id="codex-default",
            component_version="1",
            provider_distribution="fixture",
            provider_version="1",
            descriptor_media_type="application/json",
            descriptor_sha256="c" * 64,
            required_capability_ids=(),
            legal_terminal_result_ids=tuple(
                sorted(mapping.runner_result_id for mapping in mappings)
            ),
        ),
    )
    return request


def _request_with_policy(redaction_policy: object) -> object:
    return _request(redaction_policy=redaction_policy)


def _asset_material() -> dict[str, AuthorityValue]:
    return {
        "kernel_ping.taskmaster_prompt": {
            "path": "entrypoints/taskmaster.md",
            "body": "Turn prompt into task artifact.",
        },
        "kernel_ping.tdd_core": {
            "path": "skills/tdd/SKILL.md",
            "body": "Use tests to define done.",
        },
        "kernel_ping.task_artifact": {
            "schema": {"required": ("completion_definition", "tests")},
        },
    }


def _config(
    tmp_path: Path,
    *,
    adapter_id: str = "codex-default",
    wrapper_code: str | None = None,
    env_allowlist: dict[str, str] | None = None,
    redaction_policy: Any | None = None,
    max_input_bytes: int = 8192,
    max_stdout_bytes: int = 8192,
    max_stderr_bytes: int = 512,
    timeout_seconds: float = 5,
    pre_cancelled: bool = False,
    wrapper_protocol_version: int = 3,
) -> object:
    from millrace.adapters.codex import CodexAdapterConfig
    from millrace.adapters.runner_contract import RedactionPolicy

    return CodexAdapterConfig(
        adapter_id=adapter_id,
        wrapper_mode="offline_fake",
        wrapper_argv=(
            sys.executable,
            "-c",
            wrapper_code or _success_wrapper_code(),
        ),
        cwd=tmp_path,
        env_allowlist=env_allowlist or {},
        timeout_seconds=timeout_seconds,
        max_input_bundle_bytes=max_input_bytes,
        max_stdout_bytes=max_stdout_bytes,
        max_stderr_diagnostic_bytes=max_stderr_bytes,
        redaction_policy=redaction_policy
        or RedactionPolicy(
            policy_id="redact-default",
            secret_tokens=("token-secret",),
        ),
        live_test_opt_in_env_flags=(),
        pre_cancelled=pre_cancelled,
        wrapper_protocol_version=wrapper_protocol_version,
    )


def _success_wrapper_code(
    *,
    marker: str = "TASK_COMPLETE",
    include_secret: bool = False,
) -> str:
    surface_value = "token-secret" if include_secret else "safe-value"
    return (
        "import json, os, pathlib, sys\n"
        "bundle = json.loads(sys.stdin.read())\n"
        "capture = os.environ.get('CAPTURE_BUNDLE_PATH')\n"
        "if capture:\n"
        "    pathlib.Path(capture).write_text(json.dumps(bundle, sort_keys=True))\n"
        "dispatch = bundle['dispatch_envelope']\n"
        "echo = bundle['dispatch_echo']\n"
        "result = {\n"
        "    'outcome_kind': 'success',\n"
        "    'adapter_id': bundle['adapter_id'],\n"
        "    'dispatch_echo': echo,\n"
        "    'redaction_policy_id': bundle['redaction_policy']['policy_id'],\n"
        f"    'marker': {marker!r},\n"
        f"    'captured_stdout': 'runner stdout {surface_value}',\n"
        f"    'captured_stderr': 'runner stderr {surface_value}',\n"
        "    'structured_provider_response': {'provider': 'fake'},\n"
        f"    'artifact_payload_candidate': {{'artifact': '{surface_value}'}},\n"
        f"    'observation_payload_candidate': {{'summary': '{surface_value}'}},\n"
        f"    'evidence_construction_diagnostics': {{'diag': '{surface_value}'}},\n"
        "}\n"
        "print(json.dumps(result, sort_keys=True))\n"
    )


def _secret_surface_wrapper_code(secret: str = "token-secret") -> str:
    return (
        "import json, sys\n"
        "bundle=json.loads(sys.stdin.read())\n"
        "dispatch=bundle['dispatch_envelope']\n"
        "echo=bundle['dispatch_echo']\n"
        f"secret={secret!r}\n"
        "result={\n"
        "    'outcome_kind':'success',\n"
        "    'adapter_id':bundle['adapter_id'],\n"
        "    'dispatch_echo':echo,\n"
        "    'redaction_policy_id':bundle['redaction_policy']['policy_id'],\n"
        "    'marker':'TASK_COMPLETE',\n"
        "    'captured_stdout':'stdout '+secret,\n"
        "    'captured_stderr':'stderr '+secret,\n"
        "    'structured_provider_response':{'provider':secret},\n"
        "    'artifact_payload_candidate':{'artifact':secret},\n"
        "    'observation_payload_candidate':{'summary':secret},\n"
        "    'evidence_construction_diagnostics':{'diag':secret},\n"
        "}\n"
        "print(json.dumps(result, sort_keys=True))\n"
    )


_MISSING = object()


def _error_wrapper_code(
    *,
    error_kind: object = "timeout",
    diagnostics: object = None,
    outcome_kind: object = "error",
    adapter_id: str | None = None,
    redaction_policy_id: str | None = None,
    echo_mutation: tuple[str, object] | None = None,
    missing_key: str | None = None,
    extra_key: str | None = None,
    trailing_data: bool = False,
    trailing_suffix: str | None = None,
    token_usage: object = _MISSING,
) -> str:
    adapter_expression = (
        "bundle['adapter_id']" if adapter_id is None else repr(adapter_id)
    )
    policy_expression = (
        "bundle['redaction_policy']['policy_id']"
        if redaction_policy_id is None
        else repr(redaction_policy_id)
    )
    code = (
        "import json, sys\n"
        "bundle=json.loads(sys.stdin.read())\n"
        "result={\n"
        f"    'outcome_kind':{outcome_kind!r},\n"
        f"    'adapter_id':{adapter_expression},\n"
        f"    'error_kind':{error_kind!r},\n"
        f"    'redaction_policy_id':{policy_expression},\n"
        "    'dispatch_echo':dict(bundle['dispatch_echo']),\n"
        f"    'diagnostics':{diagnostics!r},\n"
        "}\n"
    )
    if echo_mutation is not None:
        field_name, field_value = echo_mutation
        code += f"result['dispatch_echo'][{field_name!r}]={field_value!r}\n"
    if missing_key is not None:
        code += f"del result[{missing_key!r}]\n"
    if extra_key is not None:
        code += f"result[{extra_key!r}]=None\n"
    if token_usage is not _MISSING:
        code += f"result['token_usage']={token_usage!r}\n"
    code += "sys.stdout.write(json.dumps(result))\n"
    if trailing_data:
        suffix = " {}" if trailing_suffix is None else trailing_suffix
        code += f"sys.stdout.write({suffix!r})\n"
    else:
        code += "sys.stdout.write('\\n')\n"
    return code


def _protocol4_success_wrapper_code(
    *,
    token_usage: object = (3, 2, 5),
    diagnostics: str | None = None,
    usage_mapping: object = _MISSING,
    include_token_usage: bool = True,
) -> str:
    usage: object = None
    if usage_mapping is not _MISSING:
        usage = usage_mapping
    elif token_usage is not None:
        input_tokens, output_tokens, total_tokens = cast(
            tuple[object, ...],
            token_usage,
        )
        usage = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }
    wrapper = _success_wrapper_code()
    wrapper = wrapper.replace(
        "    'evidence_construction_diagnostics': {'diag': 'safe-value'},\n",
        "    'evidence_construction_diagnostics': "
        + (diagnostics or "{'diag': 'safe-value'}")
        + ",\n",
    )
    if not include_token_usage:
        return wrapper
    return wrapper.replace(
        "    'evidence_construction_diagnostics': "
        + (diagnostics or "{'diag': 'safe-value'}")
        + ",\n",
        "    'evidence_construction_diagnostics': "
        + (diagnostics or "{'diag': 'safe-value'}")
        + ",\n"
        + f"    'token_usage': {usage!r},\n",
    )


def _duplicate_usage_wrapper_code() -> str:
    wrapper = _protocol4_success_wrapper_code()
    return wrapper.replace(
        "print(json.dumps(result, sort_keys=True))\n",
        "payload = json.dumps(result, sort_keys=True)\n"
        "payload = payload.replace(\"\\\"input_tokens\\\": 3,\", "
        "\"\\\"input_tokens\\\": 3, \\\"input_tokens\\\": 4,\")\n"
        "print(payload)\n",
    )


def _duplicate_error_usage_wrapper_code() -> str:
    wrapper = _error_wrapper_code(
        error_kind="timeout",
        diagnostics={"provider": "timeout"},
        token_usage={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
    )
    return wrapper.replace(
        "sys.stdout.write(json.dumps(result))\n",
        "payload = json.dumps(result)\n"
        "payload = payload.replace(\"\\\"input_tokens\\\": 3,\", "
        "\"\\\"input_tokens\\\": 3, \\\"input_tokens\\\": 4,\")\n"
        "sys.stdout.write(payload)\n",
    )


def _protocol3_duplicate_marker_wrapper_code() -> str:
    wrapper = _success_wrapper_code()
    return wrapper.replace(
        "print(json.dumps(result, sort_keys=True))\n",
        "payload = json.dumps(result, sort_keys=True)\n"
        "payload = payload.replace(\"\\\"marker\\\": \\\"TASK_COMPLETE\\\",\", "
        "\"\\\"marker\\\": \\\"TASK_COMPLETE\\\", "
        "\\\"marker\\\": \\\"TASK_COMPLETE\\\",\")\n"
        "print(payload)\n",
    )


def _provider_marker_plus_null_error_wrapper_code(
    marker_path: Path,
    *,
    error_kind: str = "selected_authority_refused",
) -> str:
    wrapper = _error_wrapper_code(
        error_kind=error_kind,
        diagnostics={"reason": "provider_work_already_started"},
        token_usage=None,
    )
    return wrapper.replace(
        "import json, sys\n",
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(marker_path)!r}).write_text('provider-started')\n",
    )


def _missing_opt_in_preflight_wrapper_code(marker_path: Path) -> str:
    return (
        "import json, pathlib, sys\n"
        "bundle=json.loads(sys.stdin.read())\n"
        f"provider_marker = pathlib.Path({str(marker_path)!r})\n"
        "missing_opt_in_config = True\n"
        "if missing_opt_in_config:\n"
        "    result = {\n"
        "        'outcome_kind': 'error',\n"
        "        'adapter_id': bundle['adapter_id'],\n"
        "        'error_kind': 'missing_opt_in_config',\n"
        "        'redaction_policy_id': bundle['redaction_policy']['policy_id'],\n"
        "        'dispatch_echo': dict(bundle['dispatch_echo']),\n"
        "        'diagnostics': {'reason': 'wrapper_preflight_refused'},\n"
        "        'token_usage': None,\n"
        "    }\n"
        "    sys.stdout.write(json.dumps(result, sort_keys=True))\n"
        "    sys.stdout.write('\\n')\n"
        "    raise SystemExit(0)\n"
        "provider_marker.write_text('provider-started')\n"
        "raise RuntimeError('provider work unexpectedly started')\n"
    )


def _generic_projection_request(
    *,
    selected_terminal_result_mappings: object | None = None,
    selected_artifact_schemas: object | None = None,
) -> tuple[object, tuple[object, ...], tuple[object, ...]]:
    from millrace.contracts.compiled_plan import (
        ArtifactSchemaDeclaration,
        RunnerComponentPin,
        RunnerTerminalResultMapping,
    )
    from millrace.contracts.ids import ArtifactSchemaId, OutcomeId, StageKindId

    alpha_schema = ArtifactSchemaDeclaration(
        id=ArtifactSchemaId("schema.alpha"),
        schema={
            "type": "object",
            "properties": {"alpha": {"type": "string"}},
            "required": ("alpha",),
        },
        presentation={"label": "Alpha"},
    )
    beta_schema = ArtifactSchemaDeclaration(
        id=ArtifactSchemaId("schema.beta"),
        schema={
            "type": "object",
            "properties": {"beta": {"type": "integer"}},
            "required": ("beta",),
        },
        presentation={"label": "Beta"},
    )
    schemas = (alpha_schema, beta_schema)
    dispatch = _valid_dispatch_envelope(
        run_id="run.generic",
        session_id="session.generic",
        plan_id="plan.generic",
        workflow_id="fixture.generic",
        workflow_version="1",
        graph_id="graph.generic",
        work_item_id="item.generic",
        activation_id="activation.generic",
        stage_kind_id="stage.generic",
        graph_node_id="node.generic",
        runner_binding_id="runner.generic",
        external_enqueue_route_id="route.generic",
        entrypoint_asset_id="asset.entrypoint",
        skill_asset_ids=("asset.skill",),
        artifact_schema_ids=("schema.beta", "schema.alpha", "schema.unselected"),
        terminal_options=(
            {
                "outcome_id": "outcome.beta",
                "marker": "BETA_READY",
                "action_id": "action.beta",
                "action_kind": "route",
                "artifact_schema_id": "schema.beta",
            },
            {
                "outcome_id": "outcome.none",
                "marker": "NO_ARTIFACT",
                "action_id": "action.none",
                "action_kind": "close",
                "artifact_schema_id": None,
            },
            {
                "outcome_id": "outcome.alpha",
                "marker": "ALPHA_READY",
                "action_id": "action.alpha",
                "action_kind": "route",
                "artifact_schema_id": "schema.alpha",
            },
        ),
    )
    mappings = (
        RunnerTerminalResultMapping(
            stage_kind_id=StageKindId("stage.generic"),
            runner_result_id="RESULT_BETA",
            outcome_id=OutcomeId("outcome.beta"),
        ),
        RunnerTerminalResultMapping(
            stage_kind_id=StageKindId("stage.generic"),
            runner_result_id="RESULT_NONE",
            outcome_id=OutcomeId("outcome.none"),
        ),
        RunnerTerminalResultMapping(
            stage_kind_id=StageKindId("stage.generic"),
            runner_result_id="RESULT_ALPHA",
            outcome_id=OutcomeId("outcome.alpha"),
        ),
    )
    request = _request(
        dispatch_envelope=dispatch,
        selected_terminal_result_mappings=(
            tuple(selected_terminal_result_mappings)
            if selected_terminal_result_mappings is not None
            else mappings
        ),
        selected_artifact_schemas=(
            tuple(selected_artifact_schemas)
            if selected_artifact_schemas is not None
            else schemas
        ),
    )
    object.__setattr__(
        request,
        "selected_component_pin",
        RunnerComponentPin(
            component_kind="runner",
            component_id="generic-runner",
            component_version="1",
            provider_distribution="fixture",
            provider_version="1",
            descriptor_media_type="application/json",
            descriptor_sha256="c" * 64,
            required_capability_ids=(),
            legal_terminal_result_ids=tuple(
                sorted(mapping.runner_result_id for mapping in mappings)
            ),
        ),
    )
    return request, mappings, schemas


def test_codex_bundle_schema3_projects_selected_schemas_and_terminal_contracts(
    tmp_path: Path,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterSuccessResult

    capture_path = tmp_path / "bundle.json"
    request, _mappings, _schemas = _generic_projection_request()
    adapter = CodexAdapter(
        _config(tmp_path, env_allowlist={"CAPTURE_BUNDLE_PATH": str(capture_path)})
    )

    result = adapter.invoke(request)

    assert isinstance(result, AdapterSuccessResult)
    bundle = json.loads(capture_path.read_text())
    assert bundle["selected_artifact_schemas"] == [
        {
            "id": "schema.alpha",
            "record_kind": "artifact_schema_declaration",
            "schema": {
                "properties": {"alpha": {"type": "string"}},
                "required": ["alpha"],
                "type": "object",
            },
            "schema_version": 1,
        },
        {
            "id": "schema.beta",
            "record_kind": "artifact_schema_declaration",
            "schema": {
                "properties": {"beta": {"type": "integer"}},
                "required": ["beta"],
                "type": "object",
            },
            "schema_version": 1,
        },
    ]
    assert bundle["prompt"]["terminal_artifact_contracts"] == [
        {
            "outcome_id": "outcome.alpha",
            "marker": "ALPHA_READY",
            "action_id": "action.alpha",
            "action_kind": "route",
            "artifact_schema_id": "schema.alpha",
            "json_schema": {
                "properties": {"alpha": {"type": "string"}},
                "required": ["alpha"],
                "type": "object",
            },
        },
        {
            "outcome_id": "outcome.beta",
            "marker": "BETA_READY",
            "action_id": "action.beta",
            "action_kind": "route",
            "artifact_schema_id": "schema.beta",
            "json_schema": {
                "properties": {"beta": {"type": "integer"}},
                "required": ["beta"],
                "type": "object",
            },
        },
        {
            "outcome_id": "outcome.none",
            "marker": "NO_ARTIFACT",
            "action_id": "action.none",
            "action_kind": "close",
            "artifact_schema_id": None,
            "json_schema": None,
        },
    ]


def test_codex_bundle_allows_partial_mapping_with_complete_schema_selection(
    tmp_path: Path,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterSuccessResult
    from millrace.contracts.compiled_plan import RunnerComponentPin

    _unused_request, mappings, schemas = _generic_projection_request()
    capture_path = tmp_path / "bundle.json"
    request, _mappings, _schemas = _generic_projection_request(
        selected_terminal_result_mappings=(mappings[0],),
        selected_artifact_schemas=schemas,
    )
    object.__setattr__(
        request,
        "selected_component_pin",
        RunnerComponentPin(
            component_kind="runner",
            component_id="generic-runner",
            component_version="1",
            provider_distribution="fixture",
            provider_version="1",
            descriptor_media_type="application/json",
            descriptor_sha256="c" * 64,
            required_capability_ids=(),
            legal_terminal_result_ids=tuple(
                sorted(mapping.runner_result_id for mapping in mappings)
            ),
        ),
    )
    adapter = CodexAdapter(
        _config(tmp_path, env_allowlist={"CAPTURE_BUNDLE_PATH": str(capture_path)})
    )

    result = adapter.invoke(request)

    assert isinstance(result, AdapterSuccessResult)
    bundle = json.loads(capture_path.read_text())
    assert [item["id"] for item in bundle["selected_artifact_schemas"]] == [
        "schema.alpha",
        "schema.beta",
    ]
    assert [
        item["outcome_id"] for item in bundle["prompt"]["terminal_artifact_contracts"]
    ] == ["outcome.beta"]


def test_codex_refuses_selected_mapping_outside_component_pin_before_launch(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult
    from millrace.contracts.compiled_plan import RunnerComponentPin

    _unused_request, mappings, schemas = _generic_projection_request()
    request, _mappings, _schemas = _generic_projection_request(
        selected_terminal_result_mappings=(mappings[0],),
        selected_artifact_schemas=schemas,
    )
    object.__setattr__(
        request,
        "selected_component_pin",
        RunnerComponentPin(
            component_kind="runner",
            component_id="generic-runner",
            component_version="1",
            provider_distribution="fixture",
            provider_version="1",
            descriptor_media_type="application/json",
            descriptor_sha256="c" * 64,
            required_capability_ids=(),
            legal_terminal_result_ids=(mappings[0].runner_result_id,),
        ),
    )
    illegal_mapping = replace(
        mappings[0],
        runner_result_id="RESULT_NOT_SELECTED",
    )
    object.__setattr__(
        request,
        "selected_terminal_result_mappings",
        (illegal_mapping,),
    )
    marker_path = tmp_path / "launched.txt"
    adapter = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=(
                "from pathlib import Path\n"
                f"Path({str(marker_path)!r}).write_text('launched')\n"
                + _success_wrapper_code()
            ),
        )
    )

    result = adapter.invoke(request)

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "selected_authority_refused"
    assert not marker_path.exists()


def test_codex_refuses_selected_mapping_without_component_pin_before_launch(
    tmp_path: Path,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    request, _mappings, _schemas = _generic_projection_request()
    object.__setattr__(request, "selected_component_pin", None)
    marker_path = tmp_path / "launched.txt"
    adapter = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=(
                "from pathlib import Path\n"
                f"Path({str(marker_path)!r}).write_text('launched')\n"
                + _success_wrapper_code()
            ),
        )
    )

    result = adapter.invoke(request)

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "selected_authority_refused"
    assert not marker_path.exists()


def test_codex_refuses_artifact_options_without_mappings_or_schemas_before_launch(
    tmp_path: Path,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    marker_path = tmp_path / "launched.txt"
    request, _mappings, _schemas = _generic_projection_request(
        selected_terminal_result_mappings=(),
        selected_artifact_schemas=(),
    )
    adapter = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=(
                "from pathlib import Path\n"
                f"Path({str(marker_path)!r}).write_text('launched')\n"
                + _success_wrapper_code()
            ),
        )
    )

    result = adapter.invoke(request)

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "selected_authority_refused"
    assert not marker_path.exists()


def test_codex_schema3_bundle_bytes_ignore_selected_catalog_order(
    tmp_path: Path,
) -> None:
    from millrace.adapters.codex import (
        CodexAdapterConfig,
        _bundle_stdin_bytes,
    )
    from millrace.adapters.runner_contract import (
        AdapterInvocationRequest,
        DispatchEcho,
    )

    request_a, _mappings_a, _schemas_a = _generic_projection_request()
    request_b, mappings, schemas = _generic_projection_request()
    object.__setattr__(
        request_b,
        "selected_terminal_result_mappings",
        tuple(reversed(mappings)),
    )
    object.__setattr__(
        request_b,
        "selected_artifact_schemas",
        tuple(reversed(schemas)),
    )
    config = cast(CodexAdapterConfig, _config(tmp_path))

    def bundle_bytes(request: object) -> bytes:
        typed_request = cast(AdapterInvocationRequest, request)
        echo = DispatchEcho.from_dispatch_envelope(
            typed_request.dispatch_envelope,
            correlation_id=typed_request.correlation_id,
            selected_adapter_kind=typed_request.selected_adapter_kind,
        )
        return _bundle_stdin_bytes(
            typed_request,
            config=config,
            dispatch_echo=echo,
        )

    assert bundle_bytes(request_a) == bundle_bytes(request_b)


def test_codex_protocol3_bundle_bytes_remain_golden(tmp_path: Path) -> None:
    from hashlib import sha256

    from millrace.adapters.codex import (
        CodexAdapterConfig,
        _bundle_stdin_bytes,
    )
    from millrace.adapters.runner_contract import (
        AdapterInvocationRequest,
        DispatchEcho,
    )

    request = cast(AdapterInvocationRequest, _request())
    config = cast(CodexAdapterConfig, _config(tmp_path))
    echo = DispatchEcho.from_dispatch_envelope(
        request.dispatch_envelope,
        correlation_id=request.correlation_id,
        selected_adapter_kind=request.selected_adapter_kind,
    )
    raw = _bundle_stdin_bytes(request, config=config, dispatch_echo=echo)

    assert len(raw) == 4492
    assert sha256(raw).hexdigest() == (
        "25df07edd911623a2ffb5e2e2bb4f2e81ee711e32edbb638f61b21394b55e3e6"
    )
    assert b'"schema_version":3' in raw
    assert b'"token_usage"' not in raw


@pytest.mark.parametrize(
    "context_checkout",
    (None, _context_checkout()),
)
def test_codex_protocol4_prompt_projects_only_authenticated_context_descriptor(
    tmp_path: Path,
    context_checkout: dict[str, str] | None,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterSuccessResult

    capture_path = tmp_path / "bundle.json"
    request = _request(
        dispatch_envelope=_valid_dispatch_envelope(
            context_checkout=context_checkout,
        ),
    )
    adapter = CodexAdapter(
        _config(
            tmp_path,
            env_allowlist={"CAPTURE_BUNDLE_PATH": str(capture_path)},
            wrapper_code=_protocol4_success_wrapper_code(),
            wrapper_protocol_version=4,
        ),
    )

    result = adapter.invoke(request)

    assert isinstance(result, AdapterSuccessResult)
    bundle = json.loads(capture_path.read_text())
    assert bundle["schema_version"] == 4
    assert bundle["prompt"]["context_checkout"] == context_checkout
    if context_checkout is not None:
        assert set(bundle["prompt"]["context_checkout"]) == {
            "manifest_digest",
            "binding_id",
            "router_asset_id",
            "checkout_relative_path",
            "router_relative_path",
        }
        assert "body" not in repr(bundle["prompt"]["context_checkout"])


def test_codex_protocol3_prompt_has_no_context_checkout_key(tmp_path: Path) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterSuccessResult

    capture_path = tmp_path / "bundle.json"
    adapter = CodexAdapter(
        _config(
            tmp_path,
            env_allowlist={"CAPTURE_BUNDLE_PATH": str(capture_path)},
        ),
    )

    result = adapter.invoke(
        _request(
            dispatch_envelope=_valid_dispatch_envelope(
                context_checkout=_context_checkout(),
            ),
        ),
    )

    assert isinstance(result, AdapterSuccessResult)
    bundle = json.loads(capture_path.read_text())
    assert bundle["schema_version"] == 3
    assert "context_checkout" not in bundle["prompt"]
    assert "token_usage" not in bundle


def test_codex_protocol4_only_exposes_reviewed_usage_capability(
    tmp_path: Path,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import REVIEWED_TOKEN_USAGE_MAPPING

    protocol3 = CodexAdapter(_config(tmp_path))
    protocol4 = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_protocol4_success_wrapper_code(),
            wrapper_protocol_version=4,
        ),
    )

    assert not hasattr(protocol3, "token_usage_mapping_capability")
    assert (
        protocol4.token_usage_mapping_capability is REVIEWED_TOKEN_USAGE_MAPPING
    )


def test_codex_protocol4_maps_success_usage_and_keeps_diagnostics_bounded(
    tmp_path: Path,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import (
        AdapterSuccessResult,
        AdapterTokenUsage,
    )

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_protocol4_success_wrapper_code(
                diagnostics=(
                    "{'cached_input_tokens': 11, 'reasoning_output_tokens': 7}"
                ),
            ),
            wrapper_protocol_version=4,
        ),
    ).invoke(_request())

    assert isinstance(result, AdapterSuccessResult)
    assert result.token_usage == AdapterTokenUsage(
        input_tokens=3,
        output_tokens=2,
        total_tokens=5,
    )
    assert result.evidence_construction_diagnostics == {
        "cached_input_tokens": 11,
        "reasoning_output_tokens": 7,
    }


def test_codex_protocol4_maps_authenticated_error_usage(tmp_path: Path) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult, AdapterTokenUsage

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_error_wrapper_code(
                error_kind="timeout",
                diagnostics={"provider": "timeout"},
                token_usage={
                    "input_tokens": 8,
                    "output_tokens": 2,
                    "total_tokens": 10,
                },
            ),
            wrapper_protocol_version=4,
        ),
    ).invoke(_request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "timeout"
    assert result.token_usage == AdapterTokenUsage(
        input_tokens=8,
        output_tokens=2,
        total_tokens=10,
    )


@pytest.mark.parametrize(
    "usage_mapping",
    (
        {"input_tokens": True, "output_tokens": 2, "total_tokens": 3},
        {"input_tokens": -1, "output_tokens": 2, "total_tokens": 1},
        {
            "input_tokens": 2**63,
            "output_tokens": 0,
            "total_tokens": 2**63,
        },
        {"input_tokens": 3, "output_tokens": 2, "total_tokens": 6},
        {"input_tokens": 3, "output_tokens": 2},
        {
            "input_tokens": 3,
            "output_tokens": 2,
            "total_tokens": 5,
            "cached_input_tokens": 1,
        },
    ),
    ids=(
        "boolean",
        "negative",
        "overflow",
        "mismatched_total",
        "missing_total",
        "extra_key",
    ),
)
def test_codex_protocol4_rejects_malformed_success_usage(
    tmp_path: Path,
    usage_mapping: dict[str, object],
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_protocol4_success_wrapper_code(
                usage_mapping=usage_mapping,
            ),
            wrapper_protocol_version=4,
        ),
    ).invoke(_request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "result_parse_failed"
    assert result.token_usage is None


def test_codex_protocol4_rejects_duplicate_usage_keys(tmp_path: Path) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_duplicate_usage_wrapper_code(),
            wrapper_protocol_version=4,
        ),
    ).invoke(_request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "result_parse_failed"


def test_codex_protocol4_rejects_duplicate_error_usage_keys(tmp_path: Path) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_duplicate_error_usage_wrapper_code(),
            wrapper_protocol_version=4,
        ),
    ).invoke(_request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "result_parse_failed"


def test_codex_protocol3_retains_duplicate_key_parsing_compatibility(
    tmp_path: Path,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterSuccessResult

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_protocol3_duplicate_marker_wrapper_code(),
        ),
    ).invoke(_request())

    assert isinstance(result, AdapterSuccessResult)
    assert result.marker == "TASK_COMPLETE"


def test_codex_protocol4_requires_success_usage(tmp_path: Path) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_protocol4_success_wrapper_code(
                include_token_usage=False,
            ),
            wrapper_protocol_version=4,
        ),
    ).invoke(_request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "result_parse_failed"


def test_codex_protocol4_allows_null_usage_only_for_missing_opt_in_preflight(
    tmp_path: Path,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    marker_path = tmp_path / "provider.marker"
    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_missing_opt_in_preflight_wrapper_code(marker_path),
            wrapper_protocol_version=4,
        ),
    ).invoke(_request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "missing_opt_in_config"
    assert result.token_usage is None
    assert not marker_path.exists()


@pytest.mark.parametrize(
    "error_kind",
    (
        "timeout",
        "cancelled",
        "invocation_failed",
        "result_parse_failed",
        "unsupported_adapter_kind",
        "input_too_large",
        "output_too_large",
        "redaction_refused",
        "selected_authority_refused",
    ),
)
def test_codex_protocol4_rejects_null_usage_for_every_other_error_kind(
    tmp_path: Path,
    error_kind: str,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_error_wrapper_code(
                error_kind=error_kind,
                diagnostics={"reason": "ambiguous_or_post_provider"},
                token_usage=None,
            ),
            wrapper_protocol_version=4,
        ),
    ).invoke(_request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "result_parse_failed"


def test_codex_protocol4_provider_marker_plus_null_requires_parse_refusal(
    tmp_path: Path,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    marker_path = tmp_path / "provider.marker"
    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_provider_marker_plus_null_error_wrapper_code(marker_path),
            wrapper_protocol_version=4,
        ),
    ).invoke(_request())

    assert marker_path.read_text(encoding="utf-8") == "provider-started"
    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "result_parse_failed"
    assert result.token_usage is None


def test_codex_protocol4_error_requires_top_level_token_usage(tmp_path: Path) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_error_wrapper_code(
                error_kind="timeout",
                diagnostics={"provider": "timeout"},
            ),
            wrapper_protocol_version=4,
        ),
    ).invoke(_request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "result_parse_failed"


@pytest.mark.parametrize(
    "usage_mapping",
    (
        {"input_tokens": 3, "output_tokens": 2},
        {
            "input_tokens": 3,
            "output_tokens": 2,
            "total_tokens": 5,
            "extra": 1,
        },
        {"input_tokens": True, "output_tokens": 2, "total_tokens": 3},
        {"input_tokens": -1, "output_tokens": 2, "total_tokens": 1},
        {
            "input_tokens": 2**63,
            "output_tokens": 0,
            "total_tokens": 2**63,
        },
        {"input_tokens": 3, "output_tokens": 2, "total_tokens": 6},
    ),
    ids=(
        "missing_nested_key",
        "extra_nested_key",
        "boolean",
        "negative",
        "int64_overflow",
        "mismatched_total",
    ),
)
def test_codex_protocol4_rejects_malformed_error_usage(
    tmp_path: Path,
    usage_mapping: dict[str, object],
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_error_wrapper_code(
                error_kind="timeout",
                diagnostics={"provider": "timeout"},
                token_usage=usage_mapping,
            ),
            wrapper_protocol_version=4,
        ),
    ).invoke(_request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "result_parse_failed"
    assert result.token_usage is None


@pytest.mark.parametrize("case", ("missing", "duplicate", "unselected", "mismatched"))
def test_codex_refuses_incoherent_selected_schema_material_before_launch(
    tmp_path: Path,
    case: str,
) -> None:
    from dataclasses import replace

    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult
    from millrace.contracts.compiled_plan import ArtifactSchemaDeclaration
    from millrace.contracts.ids import ArtifactSchemaId

    marker_path = tmp_path / "launched.txt"
    request, mappings, schemas = _generic_projection_request()
    if case == "missing":
        object.__setattr__(request, "selected_artifact_schemas", ())
    elif case == "duplicate":
        object.__setattr__(
            request,
            "selected_artifact_schemas",
            (schemas[0], schemas[0], schemas[1]),
        )
    elif case == "unselected":
        object.__setattr__(
            request,
            "selected_artifact_schemas",
            (
                schemas[0],
                schemas[1],
                ArtifactSchemaDeclaration(
                    id=ArtifactSchemaId("schema.unselected"),
                    schema={"type": "object"},
                    presentation={},
                ),
            ),
        )
    else:
        object.__setattr__(
            request,
            "selected_terminal_result_mappings",
            (replace(mappings[0], outcome_id=mappings[1].outcome_id), *mappings[1:]),
        )

    adapter = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=(
                "from pathlib import Path\n"
                f"Path({str(marker_path)!r}).write_text('launched')\n"
                + _success_wrapper_code()
            ),
        )
    )

    result = adapter.invoke(request)

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "selected_authority_refused"
    assert not marker_path.exists()


def test_codex_adapter_fake_wrapper_receives_selected_bundle_and_returns_success(
    tmp_path: Path,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import (
        AdapterSuccessResult,
        runner_evidence_from_adapter_outcome,
    )

    capture_path = tmp_path / "bundle.json"
    request = _request()
    adapter = CodexAdapter(
        _config(
            tmp_path,
            env_allowlist={"CAPTURE_BUNDLE_PATH": str(capture_path)},
        ),
    )

    result = adapter.invoke(request)

    assert isinstance(result, AdapterSuccessResult)
    assert result.adapter_id == "codex-default"
    assert result.marker == "TASK_COMPLETE"
    assert result.artifact_payload_candidate == {"artifact": "safe-value"}
    assert result.observation_payload_candidate == {"summary": "safe-value"}
    evidence = runner_evidence_from_adapter_outcome(result, request)
    assert evidence.marker == "TASK_COMPLETE"
    bundle = json.loads(capture_path.read_text())
    assert bundle["record_kind"] == "codex_adapter_invocation_bundle"
    assert bundle["schema_version"] == 3
    assert bundle["adapter_id"] == "codex-default"
    assert bundle["selected_adapter_kind"] == "codex"
    assert bundle["dispatch_envelope"]["run_id"] == "run-1"
    assert bundle["dispatch_envelope"]["selected_join_evidence"] is None
    assert bundle["dispatch_envelope"]["selected_wait_evidence"] is None
    assert bundle["prompt"]["dispatch_identity"]["plan_id"] == "kernel_ping:0.1"
    assert bundle["prompt"]["selected_join_evidence"] is None
    assert bundle["prompt"]["selected_wait_evidence"] is None
    assert bundle["dispatch_envelope"]["terminal_options"][0]["marker"] == (
        "TASK_COMPLETE"
    )
    assert bundle["legal_terminal_markers"] == ["TASK_COMPLETE"]
    assert "MADE_UP_MARKER" not in repr(bundle)
    assert (
        bundle["selected_asset_material"]["kernel_ping.taskmaster_prompt"]["body"]
        == "Turn prompt into task artifact."
    )
    assert bundle["entrypoint_asset_ref"] == "kernel_ping.taskmaster_prompt"
    assert bundle["skill_asset_refs"] == ["kernel_ping.tdd_core"]


def test_codex_bundle_v3_exposes_selected_join_evidence_as_first_class_prompt_input(
    tmp_path: Path,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterSuccessResult

    capture_path = tmp_path / "bundle.json"
    selected_join_evidence = _valid_selected_join_evidence()
    dispatch = _valid_dispatch_envelope(
        selected_join_evidence=selected_join_evidence,
    )
    request = _request(dispatch_envelope=dispatch)
    adapter = CodexAdapter(
        _config(
            tmp_path,
            env_allowlist={"CAPTURE_BUNDLE_PATH": str(capture_path)},
        ),
    )

    result = adapter.invoke(request)

    assert isinstance(result, AdapterSuccessResult)
    bundle = json.loads(capture_path.read_text())
    dispatch_evidence = bundle["dispatch_envelope"]["selected_join_evidence"]
    prompt_evidence = bundle["prompt"]["selected_join_evidence"]
    assert bundle["schema_version"] == 3
    assert dispatch_evidence == json.loads(json.dumps(selected_join_evidence))
    assert prompt_evidence == dispatch_evidence
    assert dispatch_evidence["required_artifact_schema_ids"] == [
        "RubricReport",
        "RubricReport",
    ]
    assert [
        (
            item["artifact_schema_id"],
            item["payload_digest"],
            item["item_key"],
        )
        for item in dispatch_evidence["evidence_artifacts"]
    ] == [
        ("RubricReport", _DUPLICATE_PAYLOAD_DIGEST, "candidate-a"),
        ("RubricReport", _DUPLICATE_PAYLOAD_DIGEST, "candidate-b"),
    ]


def test_codex_bundle_v3_exposes_exact_selected_wait_evidence(
    tmp_path: Path,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterSuccessResult

    capture_path = tmp_path / "bundle.json"
    selected_wait_evidence = _valid_selected_wait_evidence()
    request = _request(
        dispatch_envelope=_valid_dispatch_envelope(
            selected_wait_evidence=selected_wait_evidence,
        )
    )
    adapter = CodexAdapter(
        _config(tmp_path, env_allowlist={"CAPTURE_BUNDLE_PATH": str(capture_path)})
    )

    result = adapter.invoke(request)

    assert isinstance(result, AdapterSuccessResult)
    bundle = json.loads(capture_path.read_text())
    dispatch_evidence = bundle["dispatch_envelope"]["selected_wait_evidence"]
    assert bundle["schema_version"] == 3
    assert dispatch_evidence == selected_wait_evidence
    assert bundle["prompt"]["selected_wait_evidence"] == dispatch_evidence


def test_codex_refuses_secret_token_in_selected_wait_evidence_before_invocation(
    tmp_path: Path,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult, RedactionPolicy

    secret = "WAIT_SECRET"
    marker_path = tmp_path / "launched.txt"
    policy = RedactionPolicy(policy_id="redact-default", secret_tokens=(secret,))
    request = _request(
        dispatch_envelope=_valid_dispatch_envelope(
            selected_wait_evidence=_valid_selected_wait_evidence(
                source_artifact_payload={"detail": secret}
            ),
        ),
        redaction_policy=policy,
    )
    adapter = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=(
                "import pathlib\n"
                f"pathlib.Path({str(marker_path)!r}).write_text('launched')\n"
                + _success_wrapper_code()
            ),
            redaction_policy=policy,
        )
    )

    result = adapter.invoke(request)

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "redaction_refused"
    assert not marker_path.exists()


def test_codex_refuses_secret_token_in_selected_join_evidence_before_invocation(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult, RedactionPolicy

    secret = "CONFIG_SECRET"
    policy = RedactionPolicy(
        policy_id="redact-default",
        secret_tokens=(secret,),
    )
    marker_path = tmp_path / "launched.txt"
    selected_join_evidence = _valid_selected_join_evidence(
        correlation_value=secret,
    )
    dispatch = _valid_dispatch_envelope(
        selected_join_evidence=selected_join_evidence,
    )
    adapter = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=(
                "import pathlib\n"
                f"pathlib.Path({str(marker_path)!r}).write_text('launched')\n"
                + _success_wrapper_code()
            ),
            redaction_policy=policy,
        ),
    )

    result = adapter.invoke(
        _request(dispatch_envelope=dispatch, redaction_policy=policy),
    )
    logging.getLogger(__name__).warning("%s %r", result, result)

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "redaction_refused"
    assert not marker_path.exists()
    assert secret not in repr(result)
    assert secret not in repr(result.diagnostics)
    assert secret not in caplog.text


def test_codex_refuses_secret_token_in_nested_selected_evidence_mapping_key(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult, RedactionPolicy

    secret = "NESTED_KEY_SECRET"
    policy = RedactionPolicy(
        policy_id="redact-default",
        secret_tokens=(secret,),
    )
    marker_path = tmp_path / "launched.txt"
    capture_path = tmp_path / "bundle.json"
    selected_join_evidence = _valid_selected_join_evidence()
    evidence_artifacts = list(
        cast(
            tuple[dict[str, object], ...],
            selected_join_evidence["evidence_artifacts"],
        ),
    )
    evidence_artifacts[0] = dict(
        evidence_artifacts[0],
        payload={"nested": {f"field-{secret}": "safe-value"}},
    )
    selected_join_evidence["evidence_artifacts"] = tuple(evidence_artifacts)
    dispatch = _valid_dispatch_envelope(
        selected_join_evidence=selected_join_evidence,
    )
    adapter = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=(
                "import pathlib\n"
                f"pathlib.Path({str(marker_path)!r}).write_text('launched')\n"
                + _success_wrapper_code()
            ),
            env_allowlist={"CAPTURE_BUNDLE_PATH": str(capture_path)},
            redaction_policy=policy,
        ),
    )

    result = adapter.invoke(
        _request(dispatch_envelope=dispatch, redaction_policy=policy),
    )
    logging.getLogger(__name__).warning("%s %r", result, result)

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "redaction_refused"
    assert not marker_path.exists()
    assert not capture_path.exists()
    assert secret not in repr(result)
    assert secret not in repr(result.diagnostics)
    assert secret not in caplog.text


def test_codex_adapter_requires_selected_codex_config_and_not_bare_transport(
    tmp_path: Path,
) -> None:
    from millrace.adapters.codex import CodexAdapter, CodexAdapterConfig
    from millrace.adapters.runner_contract import (
        AdapterErrorResult,
        AdapterLocalConfig,
        AdapterResolverError,
        RedactionPolicy,
        resolve_adapter,
    )
    from millrace.adapters.subprocess_transport import SubprocessTransport

    with pytest.raises(AdapterResolverError):
        resolve_adapter("codex", AdapterLocalConfig(adapters={}))
    with pytest.raises(AdapterResolverError):
        resolve_adapter(
            "codex",
            AdapterLocalConfig(adapters={"codex": SubprocessTransport()}),
        )

    adapter = CodexAdapter(
        CodexAdapterConfig(
            adapter_id="codex-default",
            wrapper_mode="missing",
            wrapper_argv=None,
            cwd=tmp_path,
            env_allowlist={},
            timeout_seconds=5,
            max_input_bundle_bytes=8192,
            max_stdout_bytes=8192,
            max_stderr_diagnostic_bytes=512,
            redaction_policy=RedactionPolicy(policy_id="redact-default"),
            live_test_opt_in_env_flags=(),
        ),
    )
    assert resolve_adapter("codex", AdapterLocalConfig(adapters={"codex": adapter}))

    result = adapter.invoke(_request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "missing_opt_in_config"


def test_codex_adapter_refuses_shell_strings_and_local_subprocess_kind(
    tmp_path: Path,
) -> None:
    from millrace.adapters.codex import CodexAdapter, CodexAdapterConfig
    from millrace.adapters.runner_contract import (
        AdapterErrorResult,
        AdapterLocalConfig,
        AdapterResolverError,
        RedactionPolicy,
        resolve_adapter,
    )

    with pytest.raises(TypeError):
        CodexAdapterConfig(
            adapter_id="codex-default",
            wrapper_mode="offline_fake",
            wrapper_argv=cast(Any, f"{sys.executable} -c pass"),
            cwd=tmp_path,
            env_allowlist={},
            timeout_seconds=5,
            max_input_bundle_bytes=8192,
            max_stdout_bytes=8192,
            max_stderr_diagnostic_bytes=512,
            redaction_policy=RedactionPolicy(policy_id="redact-default"),
            live_test_opt_in_env_flags=(),
        )
    with pytest.raises(ValueError, match="wrapper_argv is required"):
        CodexAdapterConfig(
            adapter_id="codex-default",
            wrapper_mode="offline_fake",
            wrapper_argv=None,
            cwd=tmp_path,
            env_allowlist={},
            timeout_seconds=5,
            max_input_bundle_bytes=8192,
            max_stdout_bytes=8192,
            max_stderr_diagnostic_bytes=512,
            redaction_policy=RedactionPolicy(policy_id="redact-default"),
            live_test_opt_in_env_flags=(),
        )

    adapter = CodexAdapter(_config(tmp_path))
    with pytest.raises(AdapterResolverError):
        resolve_adapter(
            "local_subprocess",
            AdapterLocalConfig(adapters={"codex": adapter}),
        )

    result = adapter.invoke(_request(selected_adapter_kind="fake_local"))

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "unsupported_adapter_kind"


def test_codex_adapter_input_too_large_refuses_before_launch(
    tmp_path: Path,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    marker_path = tmp_path / "launched.txt"
    request = _request(
        selected_asset_material={
            "kernel_ping.taskmaster_prompt": {"body": "x" * 4000},
        },
    )
    adapter = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=(
                "import pathlib\n"
                f"pathlib.Path({str(marker_path)!r}).write_text('launched')\n"
            ),
            max_input_bytes=128,
        ),
    )

    result = adapter.invoke(request)

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "input_too_large"
    assert not marker_path.exists()


def test_codex_adapter_adapter_id_mismatch_refuses_before_launch(
    tmp_path: Path,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    marker_path = tmp_path / "launched.txt"
    adapter = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=(
                "import pathlib\n"
                f"pathlib.Path({str(marker_path)!r}).write_text('launched')\n"
            ),
        ),
    )

    result = adapter.invoke(_request(adapter_id="other-codex"))

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "missing_opt_in_config"
    assert result.diagnostics == {"reason": "adapter_id mismatch"}
    assert not marker_path.exists()


def test_codex_adapter_identity_error_hides_configured_identity_tokens(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult, RedactionPolicy

    secret = "CONFIG_SECRET"
    policy = RedactionPolicy(
        policy_id=f"redact-{secret}",
        secret_tokens=(secret,),
    )
    marker_path = tmp_path / "launched.txt"
    adapter = CodexAdapter(
        _config(
            tmp_path,
            adapter_id=f"codex-{secret}",
            redaction_policy=policy,
            wrapper_code=(
                "import pathlib\n"
                f"pathlib.Path({str(marker_path)!r}).write_text('launched')\n"
            ),
        ),
    )

    result = adapter.invoke(_request(adapter_id="other-codex"))
    logging.getLogger(__name__).warning("%s %r", result, result)

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "missing_opt_in_config"
    assert result.adapter_id == "codex-[REDACTED]"
    assert result.redaction_policy_id == "redact-[REDACTED]"
    assert not marker_path.exists()
    assert secret not in repr(result)
    assert secret not in caplog.text


@pytest.mark.parametrize(
    ("wrapper_code", "expected_error", "max_stdout_bytes"),
    (
        ("import sys; sys.stdout.write('not-json')", "result_parse_failed", 8192),
        ("import sys; sys.stdout.write('{}\\n{}')", "result_parse_failed", 8192),
        (
            "import json, sys\n"
            "bundle=json.loads(sys.stdin.read())\n"
            "dispatch=bundle['dispatch_envelope']\n"
            "echo={'run_id':'wrong','claim_id':dispatch['claim_id'],"
            "'generation':dispatch['generation'],"
            "'fencing_token':dispatch['fencing_token'],"
            "'plan_fingerprint':dispatch['plan_fingerprint'],"
            "'stage_kind_id':dispatch['stage_kind_id'],"
            "'graph_node_id':dispatch['graph_node_id'],"
            "'runner_binding_id':dispatch['runner_binding_id'],"
            "'correlation_id':bundle['correlation_id']}\n"
            "print(json.dumps({'outcome_kind':'success',"
            "'adapter_id':bundle['adapter_id'],'dispatch_echo':echo,"
            "'redaction_policy_id':bundle['redaction_policy']['policy_id'],"
            "'marker':'TASK_COMPLETE','captured_stdout':None,"
            "'captured_stderr':None,'structured_provider_response':{},"
            "'artifact_payload_candidate':None,"
            "'observation_payload_candidate':None,"
            "'evidence_construction_diagnostics':{}}))\n",
            "result_parse_failed",
            8192,
        ),
        (
            "import sys; print('x' * 100000); sys.stdout.flush()",
            "output_too_large",
            16,
        ),
        (
            "import sys; print('bad', file=sys.stderr); raise SystemExit(3)",
            "invocation_failed",
            8192,
        ),
        ("import time; time.sleep(5)", "timeout", 8192),
    ),
)
def test_codex_adapter_maps_wrapper_failures_to_adapter_errors(
    tmp_path: Path,
    wrapper_code: str,
    expected_error: str,
    max_stdout_bytes: int,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import (
        AdapterErrorResult,
        runner_evidence_from_adapter_outcome,
    )

    request = _request()
    adapter = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=wrapper_code,
            max_stdout_bytes=max_stdout_bytes,
            timeout_seconds=0.2,
        ),
    )

    result = adapter.invoke(request)

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == expected_error
    with pytest.raises(TypeError):
        runner_evidence_from_adapter_outcome(result, request)


def test_codex_adapter_rejects_invalid_utf8_stdout(tmp_path: Path) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import (
        AdapterErrorResult,
        runner_evidence_from_adapter_outcome,
    )

    wrapper_code = "import sys\nsys.stdout.buffer.write(b'invalid-utf8-\\xff')\n"
    request = _request()

    result = CodexAdapter(_config(tmp_path, wrapper_code=wrapper_code)).invoke(request)

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "invocation_failed"
    with pytest.raises(TypeError):
        runner_evidence_from_adapter_outcome(result, request)


def test_codex_adapter_refuses_extra_top_level_success_fields(tmp_path: Path) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    wrapper_code = (
        "import json, sys\n"
        "bundle=json.loads(sys.stdin.read())\n"
            "dispatch=bundle['dispatch_envelope']\n"
            "echo={'run_id':dispatch['run_id'],"
            "'session_id':dispatch['session_id'],"
            "'dispatch_generation':dispatch['dispatch_generation'],"
            "'session_fencing_token':dispatch['session_fencing_token'],"
            "'claim_id':dispatch['claim_id'],"
        "'generation':dispatch['generation'],"
        "'fencing_token':dispatch['fencing_token'],"
        "'plan_fingerprint':dispatch['plan_fingerprint'],"
        "'stage_kind_id':dispatch['stage_kind_id'],"
        "'graph_node_id':dispatch['graph_node_id'],"
        "'runner_binding_id':dispatch['runner_binding_id'],"
        "'correlation_id':bundle['correlation_id']}\n"
        "result={'outcome_kind':'success','adapter_id':bundle['adapter_id'],"
        "'dispatch_echo':echo,"
        "'redaction_policy_id':bundle['redaction_policy']['policy_id'],"
        "'marker':'TASK_COMPLETE','captured_stdout':None,"
        "'captured_stderr':None,'structured_provider_response':{},"
        "'artifact_payload_candidate':None,'observation_payload_candidate':None,"
        "'evidence_construction_diagnostics':{},'extra':'forbidden'}\n"
        "print(json.dumps(result))\n"
    )

    result = CodexAdapter(_config(tmp_path, wrapper_code=wrapper_code)).invoke(
        _request(),
    )

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "result_parse_failed"


@pytest.mark.parametrize(
    ("adapter_id", "redaction_policy_id"),
    (
        ("wrong-adapter", "redact-default"),
        ("codex-default", "wrong-redaction-policy"),
    ),
)
def test_codex_adapter_refuses_wrapper_identity_mismatch(
    tmp_path: Path,
    adapter_id: str,
    redaction_policy_id: str,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import (
        AdapterErrorResult,
        runner_evidence_from_adapter_outcome,
    )

    wrapper_code = (
        "import json, sys\n"
        "bundle=json.loads(sys.stdin.read())\n"
        "echo=bundle['dispatch_echo']\n"
        "result={'outcome_kind':'success',"
        f"'adapter_id':{adapter_id!r},"
        "'dispatch_echo':echo,"
        f"'redaction_policy_id':{redaction_policy_id!r},"
        "'marker':'TASK_COMPLETE','captured_stdout':None,"
        "'captured_stderr':None,'structured_provider_response':{},"
        "'artifact_payload_candidate':None,'observation_payload_candidate':None,"
        "'evidence_construction_diagnostics':{}}\n"
        "print(json.dumps(result))\n"
    )
    request = _request()

    result = CodexAdapter(_config(tmp_path, wrapper_code=wrapper_code)).invoke(request)

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "result_parse_failed"
    with pytest.raises(TypeError):
        runner_evidence_from_adapter_outcome(result, request)


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    (
        ("marker", ""),
        ("marker", 7),
        ("structured_provider_response", []),
        ("structured_provider_response", {"nested": {"bad": 1.25}}),
        ("artifact_payload_candidate", "not-a-mapping"),
        ("artifact_payload_candidate", {"bad": 1.25}),
        ("observation_payload_candidate", "not-a-mapping"),
        ("observation_payload_candidate", {"bad": 1.25}),
        ("evidence_construction_diagnostics", []),
        ("evidence_construction_diagnostics", {"bad": 1.25}),
    ),
)
def test_codex_adapter_malformed_success_fields_are_parse_errors(
    tmp_path: Path,
    field_name: str,
    field_value: object,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import (
        AdapterErrorResult,
        runner_evidence_from_adapter_outcome,
    )

    wrapper_code = (
        "import json, sys\n"
        "bundle=json.loads(sys.stdin.read())\n"
        "dispatch=bundle['dispatch_envelope']\n"
        "echo={'run_id':dispatch['run_id'],'claim_id':dispatch['claim_id'],"
        "'generation':dispatch['generation'],"
        "'fencing_token':dispatch['fencing_token'],"
        "'plan_fingerprint':dispatch['plan_fingerprint'],"
        "'stage_kind_id':dispatch['stage_kind_id'],"
        "'graph_node_id':dispatch['graph_node_id'],"
        "'runner_binding_id':dispatch['runner_binding_id'],"
        "'correlation_id':bundle['correlation_id']}\n"
        "result={'outcome_kind':'success','adapter_id':bundle['adapter_id'],"
        "'dispatch_echo':echo,"
        "'redaction_policy_id':bundle['redaction_policy']['policy_id'],"
        "'marker':'TASK_COMPLETE','captured_stdout':None,"
        "'captured_stderr':None,'structured_provider_response':{},"
        "'artifact_payload_candidate':None,'observation_payload_candidate':None,"
        "'evidence_construction_diagnostics':{}}\n"
        f"result[{field_name!r}]={field_value!r}\n"
        "print(json.dumps(result))\n"
    )
    request = _request()

    result = CodexAdapter(_config(tmp_path, wrapper_code=wrapper_code)).invoke(request)

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "result_parse_failed"
    with pytest.raises(TypeError):
        runner_evidence_from_adapter_outcome(result, request)


@pytest.mark.parametrize(
    ("field_name", "mutated_value"),
    (
        ("run_id", "mutated-run"),
        ("session_id", "mutated-session"),
        ("dispatch_generation", 99),
        ("session_fencing_token", "mutated-session-fence"),
        ("claim_id", "mutated-claim"),
        ("generation", 99),
        ("fencing_token", "mutated-fence"),
        ("plan_fingerprint", "sha256:" + "0" * 64),
        ("stage_kind_id", "mutated-stage"),
        ("graph_node_id", "mutated-node"),
        ("runner_binding_id", "mutated-binding"),
        ("correlation_id", "mutated-correlation"),
        ("selected_authority_digest", "sha256:" + "1" * 64),
    ),
)
def test_codex_error_envelope_rejects_every_dispatch_echo_mutation(
    tmp_path: Path,
    field_name: str,
    mutated_value: object,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_error_wrapper_code(
                echo_mutation=(field_name, mutated_value),
            ),
        ),
    ).invoke(_request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "result_parse_failed"


@pytest.mark.parametrize(
    ("missing_key", "extra_key"),
    (("diagnostics", None), (None, "unexpected")),
)
def test_codex_error_envelope_requires_exact_top_level_keys(
    tmp_path: Path,
    missing_key: str | None,
    extra_key: str | None,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_error_wrapper_code(
                missing_key=missing_key,
                extra_key=extra_key,
            ),
        ),
    ).invoke(_request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "result_parse_failed"


@pytest.mark.parametrize(
    ("adapter_id", "redaction_policy_id"),
    (("wrong-codex", None), (None, "wrong-redaction-policy")),
)
def test_codex_error_envelope_requires_runtime_identity(
    tmp_path: Path,
    adapter_id: str | None,
    redaction_policy_id: str | None,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_error_wrapper_code(
                adapter_id=adapter_id,
                redaction_policy_id=redaction_policy_id,
            ),
        ),
    ).invoke(_request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "result_parse_failed"


def test_codex_error_envelope_rejects_unknown_outcome_kind(tmp_path: Path) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_error_wrapper_code(outcome_kind="unknown"),
        ),
    ).invoke(_request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "result_parse_failed"


def test_codex_error_envelope_rejects_unsupported_error_kind(tmp_path: Path) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_error_wrapper_code(error_kind="not-supported"),
        ),
    ).invoke(_request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "result_parse_failed"


@pytest.mark.parametrize("diagnostics", ([], {"bad": 1.25}))
def test_codex_error_envelope_rejects_malformed_diagnostics(
    tmp_path: Path,
    diagnostics: object,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_error_wrapper_code(diagnostics=diagnostics),
        ),
    ).invoke(_request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "result_parse_failed"


def test_codex_error_envelope_rejects_trailing_json(tmp_path: Path) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_error_wrapper_code(trailing_data=True),
        ),
    ).invoke(_request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "result_parse_failed"


def test_codex_accepts_authenticated_typed_error_envelope(tmp_path: Path) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult, DispatchEcho

    request = _request()
    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_error_wrapper_code(
                error_kind="timeout",
                diagnostics={"message": "provider timed out", "attempt": 1},
            ),
        ),
    ).invoke(request)

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "timeout"
    assert result.adapter_id == "codex-default"
    assert result.redaction_policy_id == "redact-default"
    assert result.dispatch_echo == DispatchEcho.from_dispatch_envelope(
        request.dispatch_envelope,
        correlation_id=request.correlation_id,
        selected_adapter_kind=request.selected_adapter_kind,
    )
    assert result.diagnostics == {
        "message": "provider timed out",
        "attempt": 1,
    }


def test_codex_error_stdout_secret_takes_redaction_precedence(tmp_path: Path) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_error_wrapper_code(
                diagnostics={"message": "token-secret"},
            ),
        ),
    ).invoke(_request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "redaction_refused"
    assert "token-secret" not in repr(result)
    assert "token-secret" not in repr(result.diagnostics)


def test_codex_decoded_wrapper_secret_takes_redaction_precedence(
    tmp_path: Path,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    wrapper_code = _error_wrapper_code(
        diagnostics={"message": "token-secret"},
    ).replace(
        "sys.stdout.write(json.dumps(result))\n",
        "payload=json.dumps(result)\n"
        "payload=payload.replace('token-secret', 'token\\\\u002dsecret')\n"
        "sys.stdout.write(payload)\n",
    )

    result = CodexAdapter(
        _config(tmp_path, wrapper_code=wrapper_code),
    ).invoke(_request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "redaction_refused"
    assert "token-secret" not in repr(result)


def test_codex_stdout_secret_precedes_trailing_result_parse_failure(
    tmp_path: Path,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_error_wrapper_code(
                trailing_data=True,
                trailing_suffix=" {} token-secret",
            ),
        ),
    ).invoke(_request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "redaction_refused"
    assert "token-secret" not in repr(result)


def test_codex_stdout_secret_precedes_success_result_parsing(tmp_path: Path) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_success_wrapper_code(include_secret=True),
        ),
    ).invoke(_request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "redaction_refused"
    assert "token-secret" not in repr(result)


def test_codex_adapter_pre_cancelled_invocation_returns_cancelled(
    tmp_path: Path,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    marker_path = tmp_path / "launched.txt"
    adapter = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=(
                "import pathlib\n"
                f"pathlib.Path({str(marker_path)!r}).write_text('launched')\n"
            ),
            pre_cancelled=True,
        ),
    )

    result = adapter.invoke(_request(cancellation_token="token-1"))

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "cancelled"
    assert not marker_path.exists()


def test_codex_adapter_missing_live_opt_in_refuses_before_launch(
    tmp_path: Path,
) -> None:
    from millrace.adapters.codex import CodexAdapter, CodexAdapterConfig
    from millrace.adapters.runner_contract import AdapterErrorResult, RedactionPolicy

    marker_path = tmp_path / "launched.txt"
    adapter = CodexAdapter(
        CodexAdapterConfig(
            adapter_id="codex-default",
            wrapper_mode="local_argv",
            wrapper_argv=(
                sys.executable,
                "-c",
                f"from pathlib import Path; Path({str(marker_path)!r}).write_text('x')",
            ),
            cwd=tmp_path,
            env_allowlist={},
            timeout_seconds=5,
            max_input_bundle_bytes=8192,
            max_stdout_bytes=8192,
            max_stderr_diagnostic_bytes=512,
            redaction_policy=RedactionPolicy(policy_id="redact-default"),
            live_test_opt_in_env_flags=("MILLRACE_CODEX_LIVE_OPT_IN",),
        ),
    )

    result = adapter.invoke(_request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "missing_opt_in_config"
    assert not marker_path.exists()


def test_codex_adapter_unselected_marker_remains_evidence_only(tmp_path: Path) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import (
        AdapterSuccessResult,
        runner_evidence_from_adapter_outcome,
    )

    request = _request()
    result = CodexAdapter(
        _config(tmp_path, wrapper_code=_success_wrapper_code(marker="UNSELECTED")),
    ).invoke(request)

    assert isinstance(result, AdapterSuccessResult)
    assert result.marker == "UNSELECTED"
    evidence = runner_evidence_from_adapter_outcome(result, request)
    assert evidence.marker == "UNSELECTED"


def test_codex_adapter_stderr_is_diagnostics_not_marker_authority(
    tmp_path: Path,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterSuccessResult

    wrapper_code = (
        "import json, sys\n"
        "print('UNSELECTED_FROM_STDERR token-secret', file=sys.stderr)\n"
        + _success_wrapper_code(marker="TASK_COMPLETE")
    )

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=wrapper_code,
            max_stderr_bytes=24,
        ),
    ).invoke(_request())

    assert isinstance(result, AdapterSuccessResult)
    assert result.marker == "TASK_COMPLETE"
    assert "UNSELECTED_FROM_STDERR" not in repr(result.artifact_payload_candidate)
    assert "token-secret" not in repr(result)


def test_codex_adapter_refuses_wrapper_secret_surface_before_parsing(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_success_wrapper_code(include_secret=True),
        ),
    ).invoke(_request())
    logging.getLogger(__name__).warning("%s %r", result, result)

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "redaction_refused"
    assert "token-secret" not in repr(result)
    assert "token-secret" not in caplog.text


def test_codex_adapter_uses_config_policy_not_request_subclass(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult, RedactionPolicy

    class NoOpRequestPolicy(RedactionPolicy):
        def __getattribute__(self, name: str) -> object:
            if name == "secret_tokens":
                return ()
            return super().__getattribute__(name)

        def redact_text(self, value: str) -> str:
            return value

    result = CodexAdapter(
        _config(tmp_path, wrapper_code=_secret_surface_wrapper_code()),
    ).invoke(
        _request_with_policy(
            NoOpRequestPolicy(
                policy_id="redact-default",
                secret_tokens=("token-secret",),
            ),
        ),
    )
    logging.getLogger(__name__).warning("%s %r", result, result)

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "redaction_refused"
    assert "token-secret" not in repr(result)
    assert "token-secret" not in caplog.text


def test_codex_adapter_uses_config_policy_not_config_subclass(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult, RedactionPolicy

    class NoOpConfigPolicy(RedactionPolicy):
        def __getattribute__(self, name: str) -> object:
            if name == "secret_tokens":
                return ()
            return super().__getattribute__(name)

        def redact_text(self, value: str) -> str:
            return value

        def redact_authority_value(self, value: object) -> AuthorityValue:
            return cast(AuthorityValue, value)

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=_secret_surface_wrapper_code(),
            redaction_policy=NoOpConfigPolicy(
                policy_id="redact-default",
                secret_tokens=("token-secret",),
            ),
        ),
    ).invoke(_request())
    logging.getLogger(__name__).warning("%s %r", result, result)

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "redaction_refused"
    assert "token-secret" not in repr(result)
    assert "token-secret" not in caplog.text


def test_codex_adapter_redaction_failure_is_structured_without_leak(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult, RedactionPolicy

    wrapper_code = (
        "import json, sys\n"
        "bundle=json.loads(sys.stdin.read())\n"
        "echo=bundle['dispatch_echo']\n"
        "result={'outcome_kind':'success','adapter_id':bundle['adapter_id'],"
        "'dispatch_echo':echo,"
        "'redaction_policy_id':bundle['redaction_policy']['policy_id'],"
        "'marker':'TASK_COMPLETE','captured_stdout':None,"
        "'captured_stderr':None,"
        "'structured_provider_response':{'CONFIG_SECRET':'a','[REDACTED]':'b'},"
        "'artifact_payload_candidate':None,"
        "'observation_payload_candidate':None,"
        "'evidence_construction_diagnostics':{}}\n"
        "payload=json.dumps(result)\n"
        "payload=payload.replace('\"CONFIG_SECRET\"', '\"\\\\u0043ONFIG_SECRET\"')\n"
        "print(payload)\n"
    )
    policy = RedactionPolicy(
        policy_id="redact-default",
        secret_tokens=("CONFIG_SECRET",),
    )

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=wrapper_code,
            redaction_policy=policy,
        ),
    ).invoke(_request_with_policy(policy))
    logging.getLogger(__name__).warning("%s %r", result, result)

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "redaction_refused"
    assert "redaction failed" in repr(result.diagnostics)
    assert "CONFIG_SECRET" not in repr(result)
    assert "CONFIG_SECRET" not in caplog.text


def test_codex_adapter_config_repr_hides_redaction_secret_tokens(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from millrace.adapters.runner_contract import RedactionPolicy

    class LeakyReprPolicy(RedactionPolicy):
        def __repr__(self) -> str:
            return "leaky CONFIG_SECRET policy"

    config = _config(
        tmp_path,
        adapter_id="codex-CONFIG_SECRET",
        wrapper_code="print('CONFIG_SECRET')",
        env_allowlist={"TOKEN": "CONFIG_SECRET"},
        redaction_policy=LeakyReprPolicy(
            policy_id="redact-CONFIG_SECRET",
            secret_tokens=("CONFIG_SECRET",),
        ),
    )
    logging.getLogger(__name__).warning(
        "%s %r %s %r %s %r",
        config,
        config,
        config.redaction_policy,
        config.redaction_policy,
        config.wrapper_argv,
        config.env_allowlist,
    )

    assert "CONFIG_SECRET" not in repr(config)
    assert "CONFIG_SECRET" not in str(config)
    assert "CONFIG_SECRET" not in repr(config.redaction_policy)
    assert "CONFIG_SECRET" not in str(config.redaction_policy)
    assert "CONFIG_SECRET" not in repr(config.wrapper_argv)
    assert "CONFIG_SECRET" not in str(config.wrapper_argv)
    assert "CONFIG_SECRET" not in repr(config.env_allowlist)
    assert "CONFIG_SECRET" not in str(config.env_allowlist)
    assert "CONFIG_SECRET" not in caplog.text


def test_codex_adapter_config_redaction_mismatch_refuses_before_launch(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterErrorResult, RedactionPolicy

    marker_path = tmp_path / "launched.txt"
    wrapper_code = (
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(marker_path)!r}).write_text('launched')\n"
        "bundle=json.loads(sys.stdin.read())\n"
        "print(json.dumps({'outcome_kind':'success',"
        "'adapter_id':bundle['adapter_id'],"
        "'dispatch_echo':bundle['dispatch_echo'],"
        "'redaction_policy_id':bundle['redaction_policy']['policy_id'],"
        "'marker':'TASK_COMPLETE',"
        "'captured_stdout':'CONFIG_SECRET',"
        "'captured_stderr':None,"
        "'structured_provider_response':{},"
        "'artifact_payload_candidate':None,"
        "'observation_payload_candidate':None,"
        "'evidence_construction_diagnostics':{}}))\n"
    )

    result = CodexAdapter(
        _config(
            tmp_path,
            wrapper_code=wrapper_code,
            redaction_policy=RedactionPolicy(
                policy_id="redact-default",
                secret_tokens=("CONFIG_SECRET",),
            ),
        ),
    ).invoke(_request())
    logging.getLogger(__name__).warning("%s %r", result, result)

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "redaction_refused"
    assert not marker_path.exists()
    assert "CONFIG_SECRET" not in repr(result)
    assert "CONFIG_SECRET" not in caplog.text


def test_codex_adapter_imports_stay_below_runtime_authority() -> None:
    import millrace.adapters.codex as codex_module

    assert not hasattr(codex_module, "CodexRunnerAdapter")

    module_path = Path("src/millrace/adapters/codex.py")
    tree = ast.parse(module_path.read_text())
    forbidden_prefixes = (
        "millrace.compiler",
        "millrace.kernel",
        "millrace.operator",
        "millrace.packaging",
        "millrace.substrate",
        "millrace.workflows",
    )
    forbidden_calls = {
        "open",
        "exec",
        "eval",
        "read_text",
        "read_bytes",
        "write_text",
        "write_bytes",
    }
    imports: list[str] = []
    calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.append(node.func.attr)

    assert not [
        imported for imported in imports if imported.startswith(forbidden_prefixes)
    ]
    assert not (set(calls) & forbidden_calls)
