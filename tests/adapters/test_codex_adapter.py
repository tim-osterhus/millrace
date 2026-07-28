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
    )


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


def _request(
    *,
    adapter_id: str = "codex-default",
    selected_asset_material: dict[str, AuthorityValue] | None = None,
    selected_adapter_kind: str = "codex",
    cancellation_token: str | None = None,
    dispatch_envelope: RunnerDispatchEnvelope | None = None,
    redaction_policy: object | None = None,
) -> object:
    from millrace.adapters.runner_contract import (
        AdapterInvocationRequest,
        RedactionPolicy,
    )

    dispatch = dispatch_envelope or _valid_dispatch_envelope()
    policy = redaction_policy or RedactionPolicy(
        policy_id="redact-default",
        secret_tokens=("token-secret",),
    )
    return AdapterInvocationRequest(
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
    )


def _request_with_policy(redaction_policy: object) -> object:
    from millrace.adapters.runner_contract import AdapterInvocationRequest

    dispatch = _valid_dispatch_envelope()
    return AdapterInvocationRequest(
        adapter_id="codex-default",
        selected_runner_binding_id=dispatch.runner_binding_id,
        selected_adapter_kind="codex",
        dispatch_envelope=dispatch,

        session_id=dispatch.session_id,

        dispatch_generation=dispatch.dispatch_generation,

        session_fencing_token=dispatch.session_fencing_token,
        timeout_seconds=5,
        correlation_id="corr-1",
        redaction_policy=redaction_policy,  # type: ignore[arg-type]
        selected_asset_material=_asset_material(),
    )


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
    )


def _success_wrapper_code(*, marker: str = "TASK_COMPLETE") -> str:
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
        "    'captured_stdout': 'runner stdout token-secret',\n"
        "    'captured_stderr': 'runner stderr token-secret',\n"
        "    'structured_provider_response': {'provider': 'fake'},\n"
        "    'artifact_payload_candidate': {'artifact': 'token-secret'},\n"
        "    'observation_payload_candidate': {'summary': 'token-secret'},\n"
        "    'evidence_construction_diagnostics': {'diag': 'token-secret'},\n"
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
    assert result.artifact_payload_candidate == {"artifact": "[REDACTED]"}
    assert result.observation_payload_candidate == {"summary": "[REDACTED]"}
    evidence = runner_evidence_from_adapter_outcome(result, request)
    assert evidence.marker == "TASK_COMPLETE"
    bundle = json.loads(capture_path.read_text())
    assert bundle["record_kind"] == "codex_adapter_invocation_bundle"
    assert bundle["schema_version"] == 2
    assert bundle["adapter_id"] == "codex-default"
    assert bundle["selected_adapter_kind"] == "codex"
    assert bundle["dispatch_envelope"]["run_id"] == "run-1"
    assert bundle["dispatch_envelope"]["selected_join_evidence"] is None
    assert bundle["prompt"]["dispatch_identity"]["plan_id"] == "kernel_ping:0.1"
    assert bundle["prompt"]["selected_join_evidence"] is None
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


def test_codex_bundle_v2_exposes_selected_join_evidence_as_first_class_prompt_input(
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
    assert bundle["schema_version"] == 2
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


def test_codex_adapter_redacts_wrapper_surfaces_and_logs(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterSuccessResult

    result = CodexAdapter(_config(tmp_path)).invoke(_request())
    logging.getLogger(__name__).warning("%s %r", result, result)

    assert isinstance(result, AdapterSuccessResult)
    assert "token-secret" not in repr(result)
    assert "token-secret" not in caplog.text
    assert result.captured_stdout == "runner stdout [REDACTED]"
    assert result.captured_stderr == "runner stderr [REDACTED]"


def test_codex_adapter_uses_config_policy_not_request_subclass(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterSuccessResult, RedactionPolicy

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

    assert isinstance(result, AdapterSuccessResult)
    assert result.captured_stdout == "stdout [REDACTED]"
    assert result.captured_stderr == "stderr [REDACTED]"
    assert result.structured_provider_response == {"provider": "[REDACTED]"}
    assert result.artifact_payload_candidate == {"artifact": "[REDACTED]"}
    assert result.observation_payload_candidate == {"summary": "[REDACTED]"}
    assert result.evidence_construction_diagnostics == {"diag": "[REDACTED]"}
    assert "token-secret" not in repr(result)
    assert "token-secret" not in caplog.text


def test_codex_adapter_uses_config_policy_not_config_subclass(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from millrace.adapters.codex import CodexAdapter
    from millrace.adapters.runner_contract import AdapterSuccessResult, RedactionPolicy

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

    assert isinstance(result, AdapterSuccessResult)
    assert result.captured_stdout == "stdout [REDACTED]"
    assert result.captured_stderr == "stderr [REDACTED]"
    assert result.structured_provider_response == {"provider": "[REDACTED]"}
    assert result.artifact_payload_candidate == {"artifact": "[REDACTED]"}
    assert result.observation_payload_candidate == {"summary": "[REDACTED]"}
    assert result.evidence_construction_diagnostics == {"diag": "[REDACTED]"}
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
