from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import cast

import pytest

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
from millrace.contracts.runner import RunnerAdapterProvenance, RunnerDispatchEnvelope

_DESCRIPTOR_SHA256 = "d7184074aad3002439d23e4360881ac1701f2a56a3aec27a6576e582f75f23c6"
_CAPABILITIES = (
    "terminal.intent",
    "unrestricted.filesystem.read",
    "unrestricted.filesystem.write",
    "unrestricted.process.execute",
)
_RESULTS = ("BLOCKED", "COMPLETE", "ESCALATED", "REJECTED")


def _api() -> tuple[type[object], type[object]]:
    assert importlib.util.find_spec("millrace.adapters.millforge") is not None
    from millrace.adapters.millforge import MillforgeAdapter, MillforgeAdapterConfig

    return MillforgeAdapter, MillforgeAdapterConfig


def _dispatch(**overrides: object) -> RunnerDispatchEnvelope:
    values: dict[str, object] = {
        "run_id": "run-1",
        "session_id": "session-1",
        "dispatch_generation": 1,
        "session_fencing_token": "session-fence-1",
        "work_item_id": "work-1",
        "activation_id": "activation-1",
        "plan_fingerprint": "sha256:plan",
        "plan_id": "workflow:1",
        "workflow_id": "workflow",
        "workflow_version": "1",
        "graph_id": "graph-1",
        "claim_id": "claim-1",
        "generation": 2,
        "fencing_token": "fence-1",
        "queue_family_id": "queue-1",
        "stage_kind_id": "stage-a",
        "graph_node_id": "node-a",
        "runner_binding_id": "runner-a",
        "external_enqueue_route_id": None,
        "entrypoint_asset_id": "entrypoint",
        "skill_asset_ids": ("skill",),
        "artifact_schema_ids": ("artifact",),
        "work_item_payload": {"task": "prove it"},
        "governance_context": {
            "capabilities": tuple(
                {
                    "id": capability_id,
                    "support_status": "supported",
                    "grant_status": "granted",
                }
                for capability_id in _CAPABILITIES
            ),
        },
        "terminal_options": (
            {
                "outcome_id": "outcome.complete",
                "marker": "DONE",
                "action_id": "action.complete",
                "action_kind": "route",
                "artifact_schema_id": "artifact",
            },
        ),
    }
    values.update(overrides)
    return RunnerDispatchEnvelope(**values)  # type: ignore[arg-type]


def _pin(**overrides: object) -> RunnerComponentPin:
    values: dict[str, object] = {
        "component_kind": "runner",
        "component_id": "millforge-base",
        "component_version": "2",
        "provider_distribution": "millforge",
        "provider_version": "0.1.0",
        "descriptor_media_type": "application/json",
        "descriptor_sha256": _DESCRIPTOR_SHA256,
        "required_capability_ids": tuple(
            CapabilityId(value) for value in _CAPABILITIES
        ),
        "legal_terminal_result_ids": _RESULTS,
    }
    values.update(overrides)
    return RunnerComponentPin(**values)  # type: ignore[arg-type]


def _mapping(
    result_id: str = "COMPLETE",
    outcome_id: str = "outcome.complete",
    stage: str = "stage-a",
) -> RunnerTerminalResultMapping:
    return RunnerTerminalResultMapping(
        stage_kind_id=StageKindId(stage),
        runner_result_id=result_id,
        outcome_id=OutcomeId(outcome_id),
    )


def _schema(
    schema: dict[str, object] | None = None,
    schema_id: str = "artifact",
) -> ArtifactSchemaDeclaration:
    return ArtifactSchemaDeclaration(
        id=ArtifactSchemaId(schema_id),
        schema=schema
        or {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ("ok",)}},
            "required": ("status",),
        },
        presentation={},
    )


def _join_evidence(value: str = "joined evidence") -> dict[str, object]:
    digest = "a" * 64
    return {
        "record_kind": "selected_join_evidence",
        "schema_version": 1,
        "join_id": "join-1",
        "correlation_key": "task",
        "correlation_value": value,
        "correlation_identity": digest,
        "lineage_id": None,
        "bundle_artifact_id": "bundle-1",
        "bundle_artifact_schema_id": "artifact",
        "bundle_artifact_digest": f"sha256:{digest}",
        "required_artifact_schema_ids": ("artifact",),
        "evidence_artifacts": (
            {
                "artifact_id": "artifact-1",
                "artifact_schema_id": "artifact",
                "payload_digest": f"sha256:{digest}",
                "payload": {"detail": value},
                "source_action_id": "action-1",
                "source_run_id": "run-1",
                "source_work_item_id": "work-1",
                "fanout_id": "fanout-1",
                "fanout_record_id": "record-1",
                "item_key": "item-1",
            },
        ),
    }


class _PublicRecord(SimpleNamespace):
    def __eq__(self, other: object) -> bool:
        return isinstance(other, _PublicRecord) and vars(self) == vars(other)


class _SelectedOutputRequirement(_PublicRecord):
    def __init__(self, *, required: bool, json_schema: dict[str, object]) -> None:
        canonical = json.dumps(
            json_schema,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        super().__init__(
            required=required,
            json_schema=json_schema,
            canonical_schema_bytes=canonical,
            schema_sha256=hashlib.sha256(canonical).hexdigest(),
        )


class _TerminalSelectedOutputRequirement(_PublicRecord):
    def __init__(
        self,
        *,
        terminal_result: str,
        selected_output: object,
    ) -> None:
        super().__init__(
            terminal_result=terminal_result,
            selected_output=selected_output,
        )


class _HarnessExecutionRequest(_PublicRecord):
    def __init__(self, **kwargs: object) -> None:
        expected_fields = {
            "request_id",
            "run_id",
            "work_item_id",
            "task",
            "stage",
            "compiled_harness",
            "capability_envelope",
            "input_artifacts",
            "run_directory",
            "timeout",
            "cancellation",
            "secret_refs",
            "model_profile",
            "selected_output_requirements",
        }
        if set(kwargs) != expected_fields:
            raise TypeError(
                "HarnessExecutionRequest fields do not match public contract"
            )
        super().__init__(**kwargs)


class _SelectedOutputAbsent(_PublicRecord):
    def __init__(self) -> None:
        super().__init__(present=False)


class _SelectedOutputPresent(_PublicRecord):
    def __init__(self, value: object) -> None:
        super().__init__(present=True, value=value)


def _record(**kwargs: object) -> _PublicRecord:
    return _PublicRecord(**kwargs)


def _requirements_digest(requirements: object) -> str | None:
    records = tuple(cast(tuple[object, ...], requirements))
    if not records:
        return None
    payload = [
        {
            "required": item.selected_output.required,
            "schema_sha256": item.selected_output.schema_sha256,
            "terminal_result": item.terminal_result,
        }
        for item in sorted(
            records,
            key=lambda item: item.terminal_result.encode("utf-8"),
        )
    ]
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _evidence_for(request: object, **overrides: object) -> _PublicRecord:
    values: dict[str, object] = {
        "request_id": request.request_id,
        "run_id": request.run_id,
        "descriptor_sha256": _DESCRIPTOR_SHA256,
        "selected_output_requirements_sha256": _requirements_digest(
            request.selected_output_requirements
        ),
        "context_file_count": 0,
    }
    values.update(overrides)
    return _record(**values)


def _invocation_evidence_digest(request: object) -> str:
    evidence = _evidence_for(request)
    snapshot = {
        "request_id": evidence.request_id,
        "run_id": evidence.run_id,
        "descriptor_sha256": evidence.descriptor_sha256,
        "context_file_count": evidence.context_file_count,
        "selected_output_requirements_sha256": (
            evidence.selected_output_requirements_sha256
        ),
    }
    canonical = json.dumps(
        snapshot,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _drifted_requirements_digest(request: object, field_name: str) -> str:
    records = list(request.selected_output_requirements)
    original = records[0]
    terminal_result = original.terminal_result
    selected_output = original.selected_output
    if field_name == "terminal_result":
        terminal_result = "DIFFERENT"
    elif field_name == "required":
        selected_output = _SelectedOutputRequirement(
            required=not selected_output.required,
            json_schema=selected_output.json_schema,
        )
    else:
        selected_output = _SelectedOutputRequirement(
            required=selected_output.required,
            json_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {},
                "required": [],
            },
        )
    records[0] = _TerminalSelectedOutputRequirement(
        terminal_result=terminal_result,
        selected_output=selected_output,
    )
    digest = _requirements_digest(tuple(records))
    assert digest is not None
    return digest


def _public_millforge_module() -> ModuleType:
    module = ModuleType("millforge")
    for name in (
        "StageIdentity",
        "HarnessTaskInput",
        "CompiledHarnessIdentity",
        "CompiledHarnessHash",
        "CompiledHarnessRef",
        "CapabilityGrant",
        "CapabilityEnvelope",
        "RunDirRef",
        "TimeoutRef",
        "CancellationRef",
        "ModelProfileRef",
    ):
        setattr(module, name, _record)
    module.HarnessExecutionRequest = _HarnessExecutionRequest
    module.SelectedOutputRequirement = _SelectedOutputRequirement
    module.TerminalSelectedOutputRequirement = _TerminalSelectedOutputRequirement
    module.SelectedOutputAbsent = _SelectedOutputAbsent
    module.SelectedOutputPresent = _SelectedOutputPresent
    return module


class _FakeFacade:
    def __init__(
        self,
        *,
        terminal_result: str = "COMPLETE",
        selected_output: object | None = None,
        result_class: str = "domain_terminal",
        execute_error: BaseException | None = None,
        close_error: BaseException | None = None,
        result_mutator: Callable[[_PublicRecord, _PublicRecord, _PublicRecord], None]
        | None = None,
        evidence_mutator: Callable[[_PublicRecord, _PublicRecord], _PublicRecord]
        | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.calls = 0
        self.evidence_calls = 0
        self.close_calls = 0
        self.requests: list[_PublicRecord] = []
        self.terminal_result = terminal_result
        self.selected_output = selected_output
        self.result_class = result_class
        self.execute_error = execute_error
        self.close_error = close_error
        self.result_mutator = result_mutator
        self.evidence_mutator = evidence_mutator
        self.events = [] if events is None else events
        self._descriptor = _record(
            runner_id="millforge-base",
            runner_version=2,
            package_name="millforge",
            package_version="0.1.0",
            descriptor_sha256=_DESCRIPTOR_SHA256,
            required_capability_ids=_CAPABILITIES,
            legal_terminal_result_ids=_RESULTS,
        )
        self.components = _record(
            options=_record(load_context_files=False),
            metadata=_record(context_file_count=0),
            compiled_plan=_record(
                harness_id="millforge-base",
                harness_version=2,
                compiled_sha256="b" * 64,
            ),
            capability_envelope=_record(
                grants=tuple(_record(capability_id=value) for value in _CAPABILITIES),
            ),
            model_profile=_record(profile_id="profile-1"),
        )

    @property
    def descriptor(self) -> _PublicRecord:
        self.events.append("preflight")
        return self._descriptor

    def invocation_evidence_for(self, request: _PublicRecord) -> _PublicRecord:
        self.evidence_calls += 1
        self.events.append("evidence")
        evidence = _evidence_for(
            request,
            descriptor_sha256=self._descriptor.descriptor_sha256,
        )
        if self.evidence_mutator is not None:
            return self.evidence_mutator(evidence, request)
        return evidence

    async def execute(self, request: _PublicRecord) -> _PublicRecord:
        self.calls += 1
        self.events.append("execute")
        self.requests.append(request)
        if self.execute_error is not None:
            raise self.execute_error
        selected = self.selected_output
        requirements = {
            item.terminal_result: item.selected_output
            for item in request.selected_output_requirements
        }
        requirement = requirements.get(self.terminal_result)
        selected_digest = (
            None
            if selected is None or requirement is None
            else requirement.schema_sha256
        )
        intent = _record(
            request_id=request.request_id,
            run_id=request.run_id,
            stage=request.stage,
            terminal_result=self.terminal_result,
            summary="provider summary",
            artifact_refs=("provider-only-path",),
            selected_output=selected,
            selected_output_schema_sha256=selected_digest,
        )
        result = _record(
            status="completed",
            result_class=self.result_class,
            request_id=request.request_id,
            run_id=request.run_id,
            stage=request.stage,
            terminal_intent=intent,
            compiled_harness=request.compiled_harness,
            selected_output=selected,
            selected_output_schema_sha256=selected_digest,
            diagnostic=_record(code="safe", message="safe"),
        )
        if self.result_mutator is not None:
            self.result_mutator(result, intent, request)
        return result

    async def aclose(self) -> None:
        self.close_calls += 1
        self.events.append("close")
        if self.close_error is not None:
            raise self.close_error


class _CloseOnlyFacade:
    def __init__(self) -> None:
        self.close_calls = 0
        self.events: list[str] = []

    async def aclose(self) -> None:
        self.close_calls += 1
        self.events.append("close")


class _LiveProfile:
    def __init__(self, values: object) -> None:
        if not isinstance(values, dict):
            raise ValueError("profile must be an object")
        self.values = values
        self.profile_id = values["profile_id"]
        self.authentication = _record(secret_ref=_LiveSecretRef(values["secret_ref"]))

    @classmethod
    def model_validate(cls, values: object) -> _LiveProfile:
        return cls(values)


class _LiveSecretRef:
    def __init__(self, values: object) -> None:
        if not isinstance(values, dict):
            raise ValueError("secret_ref must be an object")
        self.values = values
        self.secret_id = values["secret_id"]
        self.env_var = values["env_var"]

    @classmethod
    def model_validate(cls, values: object) -> _LiveSecretRef:
        return cls(values)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _LiveSecretRef) and vars(self) == vars(other)


class _LiveSecret:
    def __init__(self, value: str) -> None:
        if not value:
            raise ValueError("secret must be nonblank")
        self.value = value

    def __repr__(self) -> str:
        return "_LiveSecret(<redacted>)"


class _UniformTimeouts:
    @classmethod
    def uniform(cls, value: float) -> _PublicRecord:
        return _record(timeout_seconds=value)


class _LiveOptions:
    def __init__(self, *, load_context_files: bool) -> None:
        self.load_context_files = load_context_files


def _live_public_millforge_module(
    facade: object,
    calls: dict[str, object],
) -> ModuleType:
    module = _public_millforge_module()
    module.ResolvedModelProfile = _LiveProfile
    module.SecretRef = _LiveSecretRef
    module.ResolvedSecret = _LiveSecret
    module.OpenAICompatibleTimeouts = _UniformTimeouts
    module.MillforgeBaseOptions = _LiveOptions

    async def create_millforge_base_live_runner(**kwargs: object) -> object:
        calls["factory_calls"] = int(calls.get("factory_calls", 0)) + 1
        calls["factory_kwargs"] = kwargs
        events = getattr(facade, "events", None)
        if isinstance(events, list):
            events.append("factory")
        failure = calls.get("factory_failure")
        if isinstance(failure, BaseException):
            raise failure
        return facade

    module.create_millforge_base_live_runner = create_millforge_base_live_runner
    return module


def _live_adapter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    facade: object,
    calls: dict[str, object],
    *,
    secret_env_var: str = "MILLRACE_TEST_PROVIDER_KEY",
    model_profile: Mapping[str, object] | None = None,
    secret_ref: Mapping[str, object] | None = None,
) -> object:
    adapter_type, config_type = _api()
    monkeypatch.setitem(
        sys.modules,
        "millforge",
        _live_public_millforge_module(facade, calls),
    )
    monkeypatch.setenv(secret_env_var, "live-test-secret")
    from millrace.adapters.runner_contract import RedactionPolicy

    return adapter_type(
        config_type.for_live(
            adapter_id="millforge-offline",
            workspace_root=tmp_path,
            timeout_seconds=10,
            redaction_policy=RedactionPolicy(
                policy_id="redact-millforge",
                secret_tokens=("secret-value",),
            ),
            model_profile=(
                {
                    "profile_id": "profile-1",
                    "secret_ref": {
                        "secret_id": "provider-key",
                        "env_var": secret_env_var,
                    },
                }
                if model_profile is None
                else model_profile
            ),
            secret_ref=(
                {
                    "secret_id": "provider-key",
                    "env_var": secret_env_var,
                }
                if secret_ref is None
                else secret_ref
            ),
        )
    )


def _request(
    *,
    dispatch: RunnerDispatchEnvelope | None = None,
    pin: RunnerComponentPin | None = None,
    mappings: tuple[RunnerTerminalResultMapping, ...] | None = None,
    schemas: tuple[ArtifactSchemaDeclaration, ...] | None = None,
    selected_asset_material: dict[str, object] | None = None,
) -> object:
    from millrace.adapters.runner_contract import (
        AdapterInvocationRequest,
        RedactionPolicy,
    )

    current_dispatch = dispatch or _dispatch()
    return AdapterInvocationRequest(
        adapter_id="millforge-offline",
        selected_runner_binding_id=current_dispatch.runner_binding_id,
        selected_adapter_kind="millforge",
        dispatch_envelope=current_dispatch,

        session_id=current_dispatch.session_id,

        dispatch_generation=current_dispatch.dispatch_generation,

        session_fencing_token=current_dispatch.session_fencing_token,
        timeout_seconds=30,
        correlation_id="corr-1",
        redaction_policy=RedactionPolicy(
            policy_id="redact-millforge",
            secret_tokens=("secret-value",),
        ),
        selected_asset_material=selected_asset_material
        or {
            "entrypoint": {"body": "Use selected authority."},
            "skill": {"body": "Keep output typed."},
        },
        selected_component_pin=_pin() if pin is None else pin,
        selected_terminal_result_mappings=(
            (_mapping(),) if mappings is None else mappings
        ),
        selected_artifact_schemas=(_schema(),) if schemas is None else schemas,
    )


def _adapter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    facade: _FakeFacade,
    *,
    timeout_seconds: float = 10,
) -> object:
    adapter_type, config_type = _api()
    monkeypatch.setitem(sys.modules, "millforge", _public_millforge_module())
    from millrace.adapters.runner_contract import RedactionPolicy

    return adapter_type(
        config_type(
            adapter_id="millforge-offline",
            facade=facade,
            workspace_root=tmp_path,
            timeout_seconds=timeout_seconds,
            redaction_policy=RedactionPolicy(
                policy_id="redact-millforge",
                secret_tokens=("secret-value",),
            ),
        )
    )


def _drive_session(adapter: object, request: object) -> object:
    from millrace.adapters.runner_contract import (
        StartedSession,
        StartRefusedBeforeExternalWork,
    )

    started = adapter.start_session(request)
    if isinstance(started, StartRefusedBeforeExternalWork):
        return started.adapter_error
    assert isinstance(started, StartedSession)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        outcome = started.handle.poll_completion()
        if outcome is not None:
            return outcome
        time.sleep(0.001)
    raise AssertionError("Millforge session did not complete")


def test_millforge_starts_live_handle_and_polls_terminal_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import AdapterSuccessResult, StartedSession

    release = threading.Event()

    class BlockingFacade(_FakeFacade):
        async def execute(self, request: _PublicRecord) -> _PublicRecord:
            await asyncio.to_thread(release.wait)
            return await super().execute(request)

    facade = BlockingFacade(
        selected_output=_SelectedOutputPresent({"status": "ok"}),
    )
    started = _adapter(monkeypatch, tmp_path, facade).start_session(_request())

    assert isinstance(started, StartedSession)
    assert started.handle.poll_completion() is None
    release.set()
    deadline = time.monotonic() + 2
    outcome = None
    while outcome is None and time.monotonic() < deadline:
        outcome = started.handle.poll_completion()
        time.sleep(0.001)
    assert isinstance(outcome, AdapterSuccessResult)
    assert started.handle.poll_completion() is None


def test_live_millforge_cancel_signals_public_token_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import AdapterErrorResult, StartedSession

    calls: dict[str, object] = {}
    observed: dict[str, object] = {}

    class CancellationAwareFacade(_FakeFacade):
        async def execute(self, request: _PublicRecord) -> _PublicRecord:
            factory = cast(dict[str, object], calls["factory_kwargs"])
            resolver = factory["cancellation_resolver"]
            token = resolver.resolve(request.cancellation)
            observed["token"] = token
            await token.wait()
            self.result_class = "cancelled"
            return await super().execute(request)

    facade = CancellationAwareFacade()
    adapter = _live_adapter(monkeypatch, tmp_path, facade, calls)
    request = replace(_request(), cancellation_token="cancel-1")
    started = adapter.start_session(request)
    assert isinstance(started, StartedSession)

    deadline = time.monotonic() + 2
    while "token" not in observed and time.monotonic() < deadline:
        time.sleep(0.001)
    first = started.handle.request_cancel()
    second = started.handle.request_cancel()
    assert first.result == "succeeded"
    assert second.result == "failed"
    token = observed["token"]
    assert token.cancellation_id == "cancel-1"
    assert token.is_cancelled() is True
    assert token.reason == "millrace_cooperative_cancel"

    outcome = None
    while outcome is None and time.monotonic() < deadline:
        outcome = started.handle.poll_completion()
        time.sleep(0.001)
    assert isinstance(outcome, AdapterErrorResult)
    assert outcome.error_kind == "cancelled"
    assert facade.close_calls == 1
    assert started.handle.cleanup().disposition == "complete"
    assert started.handle.cleanup().disposition == "complete"


def test_live_millforge_cancel_loses_truthfully_to_normal_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import AdapterSuccessResult, StartedSession

    facade = _FakeFacade(
        selected_output=_SelectedOutputPresent({"status": "ok"}),
    )
    adapter = _live_adapter(monkeypatch, tmp_path, facade, {})
    started = adapter.start_session(_request())
    assert isinstance(started, StartedSession)
    deadline = time.monotonic() + 2
    outcome = None
    while outcome is None and time.monotonic() < deadline:
        outcome = started.handle.poll_completion()
        time.sleep(0.001)

    assert isinstance(outcome, AdapterSuccessResult)
    assert started.handle.request_cancel().result == "failed"
    assert facade.close_calls == 1
    assert started.handle.cleanup().disposition == "complete"


def test_millforge_handle_truthfully_reports_unsupported_escalation_and_orphan_risk(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import StartedSession

    release = threading.Event()

    class BlockingFacade(_FakeFacade):
        async def execute(self, request: _PublicRecord) -> _PublicRecord:
            await asyncio.to_thread(release.wait)
            return await super().execute(request)

    started = _adapter(
        monkeypatch,
        tmp_path,
        BlockingFacade(selected_output=_SelectedOutputPresent({"status": "ok"})),
    ).start_session(_request())
    assert isinstance(started, StartedSession)
    assert started.handle.terminate().result == "unsupported"
    assert started.handle.kill().result == "unsupported"
    assert started.handle.cleanup().disposition == "orphan_risk"
    release.set()
    deadline = time.monotonic() + 2
    while started.handle.poll_completion() is None and time.monotonic() < deadline:
        time.sleep(0.001)
    assert started.handle.cleanup().disposition == "not_required"


def test_injected_millforge_handle_does_not_claim_cooperative_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import StartedSession

    release = threading.Event()

    class BlockingFacade(_FakeFacade):
        async def execute(self, request: _PublicRecord) -> _PublicRecord:
            await asyncio.to_thread(release.wait)
            return await super().execute(request)

    started = _adapter(monkeypatch, tmp_path, BlockingFacade()).start_session(
        _request()
    )
    assert isinstance(started, StartedSession)
    assert started.handle.request_cancel().result == "unsupported"
    release.set()


def test_terminal_poll_then_cleanup_never_spuriously_reports_orphan_risk(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import StartedSession

    for _ in range(50):
        started = _adapter(
            monkeypatch,
            tmp_path,
            _FakeFacade(
                selected_output=_SelectedOutputPresent({"status": "ok"}),
            ),
        ).start_session(_request())
        assert isinstance(started, StartedSession)
        deadline = time.monotonic() + 2
        outcome = None
        while outcome is None and time.monotonic() < deadline:
            outcome = started.handle.poll_completion()
            time.sleep(0.001)
        assert outcome is not None
        assert started.handle.cleanup().disposition == "not_required"


def test_millforge_start_session_works_inside_active_event_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    facade = _FakeFacade(selected_output=_SelectedOutputPresent({"status": "ok"}))
    adapter = _adapter(monkeypatch, tmp_path, facade)

    async def start_and_drive() -> object:
        return await asyncio.to_thread(_drive_session, adapter, _request())

    from millrace.adapters.runner_contract import AdapterSuccessResult

    assert isinstance(asyncio.run(start_and_drive()), AdapterSuccessResult)


def test_millforge_adapter_exposes_only_session_lifecycle() -> None:
    adapter_type, _ = _api()
    source = Path(sys.modules[adapter_type.__module__].__file__).read_text()

    assert not hasattr(adapter_type, "invoke")
    assert "_CompletedMillforgeCompatibilityHandle" not in source
    assert "temporary_synchronous_compatibility_shim" not in source


@pytest.mark.parametrize("result_class", ("domain_terminal", "domain_rejected"))
def test_millforge_adapter_matches_configured_descriptor_and_invokes_once(  # noqa: E501
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    result_class: str,
) -> None:
    from millrace.adapters.runner_contract import AdapterSuccessResult

    facade = _FakeFacade(
        selected_output=_SelectedOutputPresent({"status": "ok"}),
        result_class=result_class,
    )
    request = _request()
    result = _drive_session(_adapter(monkeypatch, tmp_path, facade), request)

    assert isinstance(result, AdapterSuccessResult)
    assert result.marker == "DONE"
    assert result.dispatch_echo.run_id == "run-1"
    assert facade.calls == 1
    provider_request = facade.requests[0]
    assert vars(provider_request.stage) == {
        "plane": "execution",
        "node_id": "millforge-base",
        "stage_kind_id": "millforge_base",
    }
    assert result.artifact_payload_candidate == {"status": "ok"}
    assert result.adapter_provenance == RunnerAdapterProvenance(
        adapter_kind="millforge",
        component_descriptor_sha256=_DESCRIPTOR_SHA256,
        invocation_evidence_sha256=_invocation_evidence_digest(provider_request),
        correlation_id="corr-1",
    )
    assert "adapter_provenance" not in result.artifact_payload_candidate
    assert result.observation_payload_candidate is None


def test_millforge_adapter_snapshots_invocation_evidence_before_execute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import AdapterSuccessResult

    captured: dict[str, _PublicRecord] = {}

    def capture_evidence(
        evidence: _PublicRecord,
        request: _PublicRecord,
    ) -> _PublicRecord:
        captured["evidence"] = evidence
        return evidence

    def mutate_evidence_during_execute(
        result: _PublicRecord,
        intent: _PublicRecord,
        request: _PublicRecord,
    ) -> None:
        evidence = captured["evidence"]
        evidence.request_id = "mutated-request"
        evidence.run_id = "mutated-run"
        evidence.descriptor_sha256 = "0" * 64
        evidence.context_file_count = 9
        evidence.selected_output_requirements_sha256 = "1" * 64

    facade = _FakeFacade(
        selected_output=_SelectedOutputPresent({"status": "ok"}),
        evidence_mutator=capture_evidence,
        result_mutator=mutate_evidence_during_execute,
    )
    result = _drive_session(_adapter(monkeypatch, tmp_path, facade), _request())

    assert isinstance(result, AdapterSuccessResult)
    assert result.adapter_provenance is not None
    assert (
        result.adapter_provenance.invocation_evidence_sha256
        == _invocation_evidence_digest(facade.requests[0])
    )


def test_millforge_adapter_uses_configured_redaction_before_evidence_or_execute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import AdapterErrorResult, RedactionPolicy

    facade = _FakeFacade(selected_output=_SelectedOutputPresent({"status": "ok"}))
    request = replace(
        _request(),
        redaction_policy=RedactionPolicy(policy_id="weaker-request"),
    )

    result = _drive_session(_adapter(monkeypatch, tmp_path, facade), request)

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "redaction_refused"
    assert facade.evidence_calls == 0
    assert facade.calls == 0


@pytest.mark.parametrize(
    ("mutate", "expected_evidence_calls"),
    (
        (
            lambda facade, request: replace(
                request,
                selected_component_pin=replace(
                    request.selected_component_pin,
                    component_kind="wrong",
                ),
            ),
            0,
        ),
        (
            lambda facade, request: replace(
                request,
                selected_component_pin=replace(
                    request.selected_component_pin,
                    component_id="wrong",
                ),
            ),
            0,
        ),
        (
            lambda facade, request: replace(
                request,
                selected_component_pin=replace(
                    request.selected_component_pin,
                    component_version="3",
                ),
            ),
            0,
        ),
        (
            lambda facade, request: replace(
                request,
                selected_component_pin=replace(
                    request.selected_component_pin,
                    provider_distribution="wrong",
                ),
            ),
            0,
        ),
        (
            lambda facade, request: replace(
                request,
                selected_component_pin=replace(
                    request.selected_component_pin,
                    provider_version="2.0.0",
                ),
            ),
            0,
        ),
        (
            lambda facade, request: replace(
                request,
                selected_component_pin=replace(
                    request.selected_component_pin,
                    descriptor_media_type="text/plain",
                ),
            ),
            0,
        ),
        (
            lambda facade, request: replace(
                request,
                selected_component_pin=replace(
                    request.selected_component_pin,
                    descriptor_sha256="0" * 64,
                ),
            ),
            0,
        ),
        (
            lambda facade, request: replace(
                request,
                selected_component_pin=replace(
                    request.selected_component_pin,
                    required_capability_ids=(CapabilityId("wrong.capability"),),
                ),
            ),
            0,
        ),
        (
            lambda facade, request: replace(
                request,
                selected_component_pin=replace(
                    request.selected_component_pin,
                    legal_terminal_result_ids=("COMPLETE",),
                ),
            ),
            0,
        ),
        (
            lambda facade, request: replace(
                request,
                selected_component_pin=None,
                selected_terminal_result_mappings=(),
                selected_artifact_schemas=(),
            ),
            0,
        ),
        (
            lambda facade, request: replace(
                request,
                selected_terminal_result_mappings=(),
                selected_artifact_schemas=(),
            ),
            0,
        ),
        (
            lambda facade, request: replace(
                request,
                selected_terminal_result_mappings=(
                    _mapping("UNMAPPED", "outcome.complete"),
                ),
            ),
            0,
        ),
        (
            lambda facade, request: (
                setattr(facade.components.options, "load_context_files", True),
                request,
            )[1],
            0,
        ),
        (
            lambda facade, request: (
                setattr(facade.components.metadata, "context_file_count", 1),
                request,
            )[1],
            0,
        ),
        (
            lambda facade, request: (
                setattr(facade.components.compiled_plan, "harness_id", "wrong"),
                request,
            )[1],
            0,
        ),
        (
            lambda facade, request: (
                setattr(facade.components.model_profile, "profile_id", ""),
                request,
            )[1],
            0,
        ),
        (
            lambda facade, request: (
                setattr(
                    facade.components.capability_envelope,
                    "grants",
                    facade.components.capability_envelope.grants[:-1],
                ),
                request,
            )[1],
            0,
        ),
        (
            lambda facade, request: replace(
                request,
                dispatch_envelope=replace(
                    request.dispatch_envelope,
                    governance_context={"capabilities": ()},
                ),
            ),
            0,
        ),
        (
            lambda facade, request: replace(
                request,
                dispatch_envelope=replace(
                    request.dispatch_envelope,
                    governance_context={
                        "capabilities": (
                            {
                                "id": _CAPABILITIES[0],
                                "support_status": "unsupported",
                                "grant_status": "granted",
                            },
                        )
                    },
                ),
            ),
            0,
        ),
        (
            lambda facade, request: replace(
                request,
                dispatch_envelope=replace(
                    request.dispatch_envelope,
                    governance_context={
                        "capabilities": (
                            {
                                "id": _CAPABILITIES[0],
                                "support_status": "supported",
                                "grant_status": "denied",
                            },
                        )
                    },
                ),
            ),
            0,
        ),
        (
            lambda facade, request: (
                setattr(
                    facade,
                    "evidence_mutator",
                    lambda evidence, provider_request: _evidence_for(
                        provider_request,
                        request_id="wrong-request",
                    ),
                ),
                request,
            )[1],
            1,
        ),
        (
            lambda facade, request: (
                setattr(
                    facade,
                    "evidence_mutator",
                    lambda evidence, provider_request: _evidence_for(
                        provider_request,
                        context_file_count=1,
                    ),
                ),
                request,
            )[1],
            1,
        ),
        (
            lambda facade, request: (
                setattr(
                    facade,
                    "evidence_mutator",
                    lambda evidence, provider_request: _evidence_for(
                        provider_request,
                        run_id="wrong-run",
                    ),
                ),
                request,
            )[1],
            1,
        ),
        (
            lambda facade, request: (
                setattr(
                    facade,
                    "evidence_mutator",
                    lambda evidence, provider_request: _evidence_for(
                        provider_request,
                        descriptor_sha256="0" * 64,
                    ),
                ),
                request,
            )[1],
            1,
        ),
        (
            lambda facade, request: (
                setattr(
                    facade,
                    "evidence_mutator",
                    lambda evidence, provider_request: _evidence_for(
                        provider_request,
                        selected_output_requirements_sha256=(
                            _drifted_requirements_digest(
                                provider_request,
                                "terminal_result",
                            )
                        ),
                    ),
                ),
                request,
            )[1],
            1,
        ),
        (
            lambda facade, request: (
                setattr(
                    facade,
                    "evidence_mutator",
                    lambda evidence, provider_request: _evidence_for(
                        provider_request,
                        selected_output_requirements_sha256=(
                            _drifted_requirements_digest(
                                provider_request,
                                "schema",
                            )
                        ),
                    ),
                ),
                request,
            )[1],
            1,
        ),
        (
            lambda facade, request: (
                setattr(
                    facade,
                    "evidence_mutator",
                    lambda evidence, provider_request: _evidence_for(
                        provider_request,
                        selected_output_requirements_sha256=(
                            _drifted_requirements_digest(
                                provider_request,
                                "required",
                            )
                        ),
                    ),
                ),
                request,
            )[1],
            1,
        ),
        (
            lambda facade, request: (
                setattr(
                    facade,
                    "evidence_mutator",
                    lambda evidence, provider_request: _evidence_for(
                        provider_request,
                        selected_output_requirements_sha256=None,
                    ),
                ),
                request,
            )[1],
            1,
        ),
        (
            lambda facade, request: (
                setattr(
                    facade,
                    "evidence_mutator",
                    lambda evidence, provider_request: _evidence_for(
                        provider_request,
                        selected_output_requirements_sha256="malformed",
                    ),
                ),
                request,
            )[1],
            1,
        ),
    ),
)
def test_millforge_adapter_refuses_component_capability_context_and_evidence_drift_before_execute(  # noqa: E501
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutate: object,
    expected_evidence_calls: int,
) -> None:
    from millrace.adapters.runner_contract import AdapterErrorResult

    facade = _FakeFacade()
    request = mutate(facade, _request())  # type: ignore[operator]
    result = _drive_session(_adapter(monkeypatch, tmp_path, facade), request)

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "selected_authority_refused"
    assert facade.evidence_calls == expected_evidence_calls
    assert facade.calls == 0
    assert not hasattr(result, "adapter_provenance")


def test_millforge_adapter_projects_schema_deterministically_and_retains_residual_validation(  # noqa: E501
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import AdapterErrorResult

    schema = _schema(
        {
            "type": "object",
            "properties": {
                "rows": {
                    "type": "array",
                    "min_items": 1,
                    "unique_by": "id",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "name": {"type": "string", "min_length": 2},
                        },
                        "required": ("id", "name"),
                    },
                },
                "state": {"type": "string", "const": "ok"},
            },
            "required": ("rows", "state"),
        }
    )
    facade = _FakeFacade(
        selected_output=_SelectedOutputPresent(
            {"state": "wrong", "rows": [{"id": 1, "name": "ok"}]}
        )
    )
    result = _drive_session(
        _adapter(monkeypatch, tmp_path, facade),
        _request(schemas=(schema,)),
    )

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "result_parse_failed"
    projected = (
        facade.requests[0].selected_output_requirements[0].selected_output.json_schema
    )
    assert projected == {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "rows": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string", "minLength": 2},
                    },
                    "required": ["id", "name"],
                },
            },
            "state": {"type": "string", "const": "ok"},
        },
        "required": ["rows", "state"],
    }


def test_millforge_adapter_projects_distinct_requirements_for_selected_results(  # noqa: E501
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import AdapterSuccessResult

    complete_schema = _schema(
        {
            "type": "object",
            "properties": {"status": {"const": "ok"}},
            "required": ("status",),
        },
        "complete-artifact",
    )
    blocked_schema = _schema(
        {
            "type": "object",
            "properties": {"reason": {"type": "string", "min_length": 1}},
            "required": ("reason",),
        },
        "blocked-artifact",
    )
    dispatch = _dispatch(
        artifact_schema_ids=("blocked-artifact", "complete-artifact"),
        terminal_options=(
            {
                "outcome_id": "outcome.complete",
                "marker": "DONE",
                "action_id": "action.complete",
                "action_kind": "route",
                "artifact_schema_id": "complete-artifact",
            },
            {
                "outcome_id": "outcome.blocked",
                "marker": "BLOCKED",
                "action_id": "action.blocked",
                "action_kind": "route",
                "artifact_schema_id": "blocked-artifact",
            },
            {
                "outcome_id": "outcome.escalated",
                "marker": "ESCALATED",
                "action_id": "action.escalated",
                "action_kind": "route",
                "artifact_schema_id": None,
            },
        ),
    )
    facade = _FakeFacade(
        terminal_result="COMPLETE",
        selected_output=_SelectedOutputPresent({"status": "ok"}),
    )

    result = _drive_session(
        _adapter(monkeypatch, tmp_path, facade),
        _request(
            dispatch=dispatch,
            mappings=(
                _mapping("COMPLETE", "outcome.complete"),
                _mapping("BLOCKED", "outcome.blocked"),
                _mapping("ESCALATED", "outcome.escalated"),
            ),
            schemas=(complete_schema, blocked_schema),
        ),
    )

    assert isinstance(result, AdapterSuccessResult)
    assert facade.calls == 1
    requirements = facade.requests[0].selected_output_requirements
    assert tuple(item.terminal_result for item in requirements) == (
        "BLOCKED",
        "COMPLETE",
    )
    assert all(item.selected_output.required is True for item in requirements)
    assert requirements[0].selected_output.json_schema == {
        "type": "object",
        "additionalProperties": False,
        "properties": {"reason": {"type": "string", "minLength": 1}},
        "required": ["reason"],
    }
    assert requirements[1].selected_output.json_schema == {
        "type": "object",
        "additionalProperties": False,
        "properties": {"status": {"const": "ok"}},
        "required": ["status"],
    }
    assert _requirements_digest(requirements) is not None


def test_kernel_ping_default_bindings_prepare_selected_outputs_offline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import AdapterSuccessResult
    from millrace.compiler import compile_workflow
    from millrace.workflows import kernel_ping

    compile_result = compile_workflow(kernel_ping.workflow_source())
    assert compile_result.plan is not None
    plan = compile_result.plan
    schemas = plan.artifact_schemas
    stages = {str(stage.id): stage for stage in plan.stage_kinds}
    outcomes = {str(outcome.id): outcome for outcome in plan.terminal_outcomes}
    actions = {str(action.outcome_id): action for action in plan.terminal_actions}
    cases = (
        (
            "kernel_ping.taskmaster_runner",
            "kernel_ping.taskmaster",
            "TASK_COMPLETE",
            {
                "artifact_kind": "kernel_ping.task_artifact",
                "artifact_version": 1,
                "source_prompt_id": "prompt-1",
                "title": "Executable task",
                "objective": "Prove the adapter boundary",
                "requirements": [
                    {"id": "r1", "description": "Compile selected authority"}
                ],
                "completion_tests": [
                    {
                        "id": "t1",
                        "description": "Run focused tests",
                        "expected_result": "pass",
                    }
                ],
            },
            "kernel_ping.task_artifact",
        ),
        (
            "kernel_ping.worker_runner",
            "kernel_ping.worker",
            "NEEDS_REVIEW",
            {
                "incident_kind": "kernel_ping.task_incident",
                "incident_version": 999,
                "source_prompt_id": "hostile-prompt",
                "source_task_artifact_id": "hostile-artifact",
                "worker_run_id": "hostile-run",
                "reason": "insufficient_task_detail",
                "worker_summary": "The acceptance command is missing.",
                "missing_details": ["exact command", "expected output"],
                "requested_taskmaster_action": "revise_task_artifact",
            },
            "kernel_ping.task_incident",
        ),
    )

    for binding_id, stage_id, terminal_result, selected_output, schema_id in cases:
        binding = next(
            item for item in plan.runner_bindings if str(item.id) == binding_id
        )
        pin = binding.component_pin
        assert pin is not None
        stage = stages[stage_id]
        terminal_options = tuple(
            {
                "outcome_id": str(outcome_id),
                "marker": outcomes[str(outcome_id)].marker,
                "action_id": str(actions[str(outcome_id)].id),
                "action_kind": actions[str(outcome_id)].action_kind,
                "artifact_schema_id": (
                    None
                    if actions[str(outcome_id)].artifact_schema_id is None
                    else str(actions[str(outcome_id)].artifact_schema_id)
                ),
            }
            for outcome_id in stage.declared_outcome_ids
        )
        facade = _FakeFacade(
            terminal_result=terminal_result,
            selected_output=_SelectedOutputPresent(selected_output),
        )
        facade._descriptor = _record(  # noqa: SLF001 - selected fake preflight data
            runner_id=pin.component_id,
            runner_version=int(pin.component_version),
            package_name=pin.provider_distribution,
            package_version=pin.provider_version,
            descriptor_sha256=pin.descriptor_sha256,
            required_capability_ids=tuple(
                str(value) for value in pin.required_capability_ids
            ),
            legal_terminal_result_ids=pin.legal_terminal_result_ids,
        )
        facade.components.capability_envelope = _record(
            grants=tuple(
                _record(capability_id=str(value))
                for value in pin.required_capability_ids
            )
        )
        dispatch = _dispatch(
            workflow_id="kernel_ping",
            workflow_version="0.1",
            stage_kind_id=stage_id,
            runner_binding_id=binding_id,
            artifact_schema_ids=tuple(
                str(value) for value in stage.artifact_schema_ids
            ),
            governance_context={
                "capabilities": tuple(
                    {
                        "id": str(value),
                        "support_status": "supported",
                        "grant_status": "granted",
                    }
                    for value in binding.required_capability_ids
                )
            },
            terminal_options=terminal_options,
        )
        terminal_schema_ids = {
            option["artifact_schema_id"]
            for option in terminal_options
            if option["artifact_schema_id"] is not None
        }

        result = _drive_session(
            _adapter(monkeypatch, tmp_path, facade),
            _request(
                dispatch=dispatch,
                pin=pin,
                mappings=binding.terminal_result_mappings,
                schemas=tuple(
                    schema
                    for schema in schemas
                    if str(schema.id) in terminal_schema_ids
                ),
            ),
        )

        assert isinstance(result, AdapterSuccessResult)
        assert result.marker == terminal_result
        requirements = facade.requests[0].selected_output_requirements
        assert tuple(item.terminal_result for item in requirements) == (
            terminal_result,
        )
        requirement = requirements[0].selected_output.json_schema
        assert requirement["type"] == "object"
        assert requirement["additionalProperties"] is False
        if schema_id == "kernel_ping.task_artifact":
            assert requirement["properties"]["requirements"]["items"]["type"] == (
                "object"
            )
            assert (
                requirement["properties"]["completion_tests"]["items"]["type"]
                == "object"
            )
        else:
            assert set(requirement["required"]) >= {
                "worker_summary",
                "missing_details",
            }


def test_millforge_adapter_schema_less_result_has_no_requirement_or_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import (
        AdapterErrorResult,
        AdapterSuccessResult,
    )

    dispatch = _dispatch(
        artifact_schema_ids=(),
        terminal_options=(
            {
                "outcome_id": "outcome.complete",
                "marker": "DONE",
                "action_id": "action.complete",
                "action_kind": "route",
                "artifact_schema_id": None,
            },
        ),
    )
    request = _request(dispatch=dispatch, schemas=())
    facade = _FakeFacade()

    result = _drive_session(_adapter(monkeypatch, tmp_path, facade), request)

    assert isinstance(result, AdapterSuccessResult)
    assert result.artifact_payload_candidate is None
    assert facade.requests[0].selected_output_requirements == ()

    unexpected = _FakeFacade(selected_output=_SelectedOutputPresent({"status": "ok"}))
    refused = _drive_session(_adapter(monkeypatch, tmp_path, unexpected), request)
    assert isinstance(refused, AdapterErrorResult)
    assert refused.error_kind == "result_parse_failed"
    assert unexpected.calls == 1


def test_millforge_adapter_preserves_public_const_enum_and_full_schema_authority(
    tmp_path: Path,
) -> None:
    provider = pytest.importorskip("millforge")
    adapter_type, config_type = _api()
    from millrace.adapters.runner_contract import (
        AdapterErrorResult,
        AdapterSuccessResult,
        RedactionPolicy,
    )

    schema = _schema(
        {
            "type": "object",
            "properties": {
                "const_only": {"const": "fixed"},
                "mixed": {"enum": (None, "ready")},
                "rank": {"type": "integer", "enum": (2, 1)},
                "typed_const": {"type": "string", "const": "locked"},
                "rows": {
                    "type": "array",
                    "unique_by": "id",
                    "items": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}},
                        "required": ("id",),
                    },
                },
            },
            "required": ("const_only", "mixed", "rank", "rows", "typed_const"),
        }
    )
    value = {
        "const_only": "fixed",
        "mixed": None,
        "rank": 1,
        "typed_const": "locked",
        "rows": [{"id": 1}, {"id": 2}],
    }
    facade = _FakeFacade(
        selected_output=provider.SelectedOutputPresent(value=value),
    )
    adapter = adapter_type(
        config_type(
            adapter_id="millforge-offline",
            facade=facade,
            workspace_root=tmp_path,
            timeout_seconds=10,
            redaction_policy=RedactionPolicy(
                policy_id="redact-millforge",
                secret_tokens=("secret-value",),
            ),
        )
    )

    result = _drive_session(adapter, _request(schemas=(schema,)))

    assert isinstance(result, AdapterSuccessResult)
    requirement = facade.requests[0].selected_output_requirements[0].selected_output
    properties = requirement.json_schema["properties"]
    assert properties["const_only"] == {"const": "fixed"}
    assert properties["mixed"] == {"enum": ["ready", None]}
    assert properties["rank"] == {"type": "integer", "enum": [1, 2]}
    assert properties["typed_const"] == {"type": "string", "const": "locked"}
    assert "unique_by" not in properties["rows"]

    duplicate_rows = dict(value)
    duplicate_rows["rows"] = [{"id": 1}, {"id": 1}]
    invalid_facade = _FakeFacade(
        selected_output=provider.SelectedOutputPresent(value=duplicate_rows),
    )
    invalid_adapter = adapter_type(
        config_type(
            adapter_id="millforge-offline",
            facade=invalid_facade,
            workspace_root=tmp_path,
            timeout_seconds=10,
            redaction_policy=RedactionPolicy(
                policy_id="redact-millforge",
                secret_tokens=("secret-value",),
            ),
        )
    )
    refused = _drive_session(invalid_adapter, _request(schemas=(schema,)))
    assert isinstance(refused, AdapterErrorResult)
    assert refused.error_kind == "result_parse_failed"


def test_millforge_adapter_partial_mapping_refuses_unmapped_returned_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import AdapterErrorResult

    dispatch = _dispatch(
        terminal_options=(
            {
                "outcome_id": "outcome.complete",
                "marker": "DONE",
                "action_id": "action.complete",
                "action_kind": "route",
                "artifact_schema_id": "artifact",
            },
            {
                "outcome_id": "outcome.blocked",
                "marker": "BLOCKED",
                "action_id": "action.blocked",
                "action_kind": "route",
                "artifact_schema_id": None,
            },
        ),
    )
    facade = _FakeFacade(terminal_result="BLOCKED")

    result = _drive_session(
        _adapter(monkeypatch, tmp_path, facade),
        _request(dispatch=dispatch, mappings=(_mapping(),)),
    )

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "result_parse_failed"
    assert facade.evidence_calls == 1
    assert facade.calls == 1
    assert not hasattr(result, "adapter_provenance")


@pytest.mark.parametrize(
    "schema",
    (
        _schema({"type": "array", "items": {"type": "string"}}),
        _schema({"type": "object", "properties": {}, "required": ("missing",)}),
        _schema(
            {
                "type": "object",
                "properties": {"x": {"type": "string", "min_length": -1}},
            }
        ),
        _schema(
            {
                "type": "object",
                "properties": {
                    f"field_{index}": {"type": "string"} for index in range(65)
                },
            }
        ),
        _schema(
            {
                "type": "object",
                "properties": {
                    "values": {
                        "type": "array",
                        "items": {"type": "string"},
                        "min_items": 1025,
                    }
                },
            }
        ),
        _schema(
            {
                "type": "object",
                "properties": {"value": {"type": "string", "min_length": 65_537}},
            }
        ),
        _schema({"type": "object", "unknown": True}),
    ),
)
def test_millforge_adapter_refuses_invalid_selected_schema_authority_before_execute(  # noqa: E501
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    schema: ArtifactSchemaDeclaration,
) -> None:
    from millrace.adapters.runner_contract import AdapterErrorResult

    facade = _FakeFacade()
    result = _drive_session(
        _adapter(monkeypatch, tmp_path, facade),
        _request(schemas=(schema,)),
    )

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "selected_authority_refused"
    assert facade.evidence_calls == 0
    assert facade.calls == 0


def test_millforge_adapter_maps_nonlegacy_results_only_through_selected_stage(  # noqa: E501
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import AdapterSuccessResult

    dispatch = _dispatch(
        artifact_schema_ids=(),
        terminal_options=(
            {
                "outcome_id": "outcome.escalated",
                "marker": "ESCALATE",
                "action_id": "action.escalated",
                "action_kind": "route",
                "artifact_schema_id": None,
            },
        ),
    )
    facade = _FakeFacade(terminal_result="ESCALATED")
    result = _drive_session(
        _adapter(monkeypatch, tmp_path, facade),
        _request(
            dispatch=dispatch,
            mappings=(_mapping("ESCALATED", "outcome.escalated"),),
            schemas=(),
        ),
    )

    assert isinstance(result, AdapterSuccessResult)
    assert result.marker == "ESCALATE"
    second_dispatch = _dispatch(
        stage_kind_id="stage-b",
        artifact_schema_ids=(),
        terminal_options=(
            {
                "outcome_id": "outcome.escalated.second",
                "marker": "REVIEW",
                "action_id": "action.escalated.second",
                "action_kind": "route",
                "artifact_schema_id": None,
            },
        ),
    )
    second = _adapter(monkeypatch, tmp_path, _FakeFacade(terminal_result="ESCALATED"))
    second_result = _drive_session(
        second,
        _request(
            dispatch=second_dispatch,
            mappings=(
                _mapping(
                    "ESCALATED",
                    "outcome.escalated.second",
                    "stage-b",
                ),
            ),
            schemas=(),
        ),
    )
    assert isinstance(second_result, AdapterSuccessResult)
    assert second_result.marker == "REVIEW"


def test_millforge_adapter_refuses_unknown_identity_or_selected_output_mismatch(  # noqa: E501
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import (
        AdapterErrorResult,
        AdapterSuccessResult,
    )

    optional_dispatch = _dispatch(
        artifact_schema_ids=("artifact",),
        terminal_options=(
            {
                "outcome_id": "outcome.complete",
                "marker": "DONE",
                "action_id": "action.complete",
                "action_kind": "route",
                "artifact_schema_id": "artifact",
            },
            {
                "outcome_id": "outcome.escalated",
                "marker": "ESCALATE",
                "action_id": "action.escalated",
                "action_kind": "route",
                "artifact_schema_id": None,
            },
        ),
    )
    absent_facade = _FakeFacade(terminal_result="ESCALATED")
    absent_result = _drive_session(
        _adapter(monkeypatch, tmp_path, absent_facade),
        _request(
            dispatch=optional_dispatch,
            mappings=(
                _mapping("COMPLETE", "outcome.complete"),
                _mapping("ESCALATED", "outcome.escalated"),
            ),
        ),
    )
    assert isinstance(absent_result, AdapterSuccessResult)
    assert absent_result.marker == "ESCALATE"

    def changed_request(
        result: _PublicRecord, intent: _PublicRecord, request: _PublicRecord
    ) -> None:
        result.request_id = "wrong-request"

    def changed_run(
        result: _PublicRecord, intent: _PublicRecord, request: _PublicRecord
    ) -> None:
        result.run_id = "wrong-run"

    def changed_result_stage(
        result: _PublicRecord,
        intent: _PublicRecord,
        request: _PublicRecord,
    ) -> None:
        result.stage = _record(plane="wrong", node_id="wrong", stage_kind_id="wrong")

    def changed_intent_request(
        result: _PublicRecord,
        intent: _PublicRecord,
        request: _PublicRecord,
    ) -> None:
        intent.request_id = "wrong-request"

    def changed_intent_run(
        result: _PublicRecord,
        intent: _PublicRecord,
        request: _PublicRecord,
    ) -> None:
        intent.run_id = "wrong-run"

    def changed_intent_stage(
        result: _PublicRecord,
        intent: _PublicRecord,
        request: _PublicRecord,
    ) -> None:
        intent.stage = _record(plane="wrong", node_id="wrong", stage_kind_id="wrong")

    def changed_harness(
        result: _PublicRecord,
        intent: _PublicRecord,
        request: _PublicRecord,
    ) -> None:
        result.compiled_harness = _record(
            identity=request.compiled_harness.identity,
            path=request.compiled_harness.path,
            expected_hash=_record(algorithm="sha256", digest="0" * 64),
        )

    def changed_result_digest(
        result: _PublicRecord,
        intent: _PublicRecord,
        request: _PublicRecord,
    ) -> None:
        result.selected_output_schema_sha256 = "0" * 64

    def changed_intent_digest(
        result: _PublicRecord,
        intent: _PublicRecord,
        request: _PublicRecord,
    ) -> None:
        intent.selected_output_schema_sha256 = "0" * 64

    def mismatched_presence(
        result: _PublicRecord,
        intent: _PublicRecord,
        request: _PublicRecord,
    ) -> None:
        intent.selected_output = _SelectedOutputAbsent()

    def foreign_result_output(
        result: _PublicRecord,
        intent: _PublicRecord,
        request: _PublicRecord,
    ) -> None:
        result.selected_output = _record(present=True, value={"status": "ok"})

    def foreign_intent_output(
        result: _PublicRecord,
        intent: _PublicRecord,
        request: _PublicRecord,
    ) -> None:
        intent.selected_output = _record(present=True, value={"status": "ok"})

    for terminal_result, mutator in (
        ("UNKNOWN", None),
        ("COMPLETE", changed_request),
        ("COMPLETE", changed_run),
        ("COMPLETE", changed_result_stage),
        ("COMPLETE", changed_intent_request),
        ("COMPLETE", changed_intent_run),
        ("COMPLETE", changed_intent_stage),
        ("COMPLETE", changed_harness),
        ("COMPLETE", changed_result_digest),
        ("COMPLETE", changed_intent_digest),
        ("COMPLETE", mismatched_presence),
        ("COMPLETE", foreign_result_output),
        ("COMPLETE", foreign_intent_output),
    ):
        facade = _FakeFacade(
            terminal_result=terminal_result,
            selected_output=_SelectedOutputPresent({"status": "ok"}),
            result_mutator=mutator,
        )
        result = _drive_session(
            _adapter(monkeypatch, tmp_path, facade),
            _request(),
        )

        assert isinstance(result, AdapterErrorResult)
        assert result.error_kind == "result_parse_failed"
        assert not hasattr(result, "adapter_provenance")
    assert facade.evidence_calls == 1
    assert facade.calls == 1


def test_millforge_adapter_returned_result_selects_only_its_requirement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import (
        AdapterErrorResult,
        AdapterSuccessResult,
    )

    dispatch = _dispatch(
        artifact_schema_ids=("blocked-artifact", "complete-artifact"),
        terminal_options=(
            {
                "outcome_id": "outcome.complete",
                "marker": "DONE",
                "action_id": "action.complete",
                "action_kind": "route",
                "artifact_schema_id": "complete-artifact",
            },
            {
                "outcome_id": "outcome.blocked",
                "marker": "BLOCKED",
                "action_id": "action.blocked",
                "action_kind": "route",
                "artifact_schema_id": "blocked-artifact",
            },
        ),
    )
    request = _request(
        dispatch=dispatch,
        mappings=(
            _mapping("COMPLETE", "outcome.complete"),
            _mapping("BLOCKED", "outcome.blocked"),
        ),
        schemas=(
            _schema(
                {
                    "type": "object",
                    "properties": {"status": {"const": "ok"}},
                    "required": ("status",),
                },
                "complete-artifact",
            ),
            _schema(
                {
                    "type": "object",
                    "properties": {"reason": {"type": "string"}},
                    "required": ("reason",),
                },
                "blocked-artifact",
            ),
        ),
    )
    legal_facade = _FakeFacade(
        terminal_result="BLOCKED",
        selected_output=_SelectedOutputPresent({"reason": "waiting"}),
    )
    legal = _drive_session(_adapter(monkeypatch, tmp_path, legal_facade), request)
    assert isinstance(legal, AdapterSuccessResult)
    assert legal.marker == "BLOCKED"

    crossed_value_facade = _FakeFacade(
        terminal_result="BLOCKED",
        selected_output=_SelectedOutputPresent({"status": "ok"}),
    )
    crossed_value = _drive_session(
        _adapter(monkeypatch, tmp_path, crossed_value_facade),
        request,
    )
    assert isinstance(crossed_value, AdapterErrorResult)
    assert crossed_value.error_kind == "result_parse_failed"

    def cross_digest(
        result: _PublicRecord,
        intent: _PublicRecord,
        provider_request: _PublicRecord,
    ) -> None:
        complete = next(
            item
            for item in provider_request.selected_output_requirements
            if item.terminal_result == "COMPLETE"
        )
        result.selected_output_schema_sha256 = complete.selected_output.schema_sha256
        intent.selected_output_schema_sha256 = complete.selected_output.schema_sha256

    crossed_digest_facade = _FakeFacade(
        terminal_result="BLOCKED",
        selected_output=_SelectedOutputPresent({"reason": "waiting"}),
        result_mutator=cross_digest,
    )
    crossed_digest = _drive_session(
        _adapter(monkeypatch, tmp_path, crossed_digest_facade),
        request,
    )
    assert isinstance(crossed_digest, AdapterErrorResult)
    assert crossed_digest.error_kind == "result_parse_failed"


def test_millforge_adapter_refuses_missing_required_selected_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import AdapterErrorResult

    facade = _FakeFacade(selected_output=None)

    result = _drive_session(_adapter(monkeypatch, tmp_path, facade), _request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "result_parse_failed"
    assert facade.evidence_calls == 1
    assert facade.calls == 1


def test_millforge_adapter_refuses_different_result_and_intent_selected_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import AdapterErrorResult

    schema = _schema(
        {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ("status",),
        }
    )

    def change_intent_value(
        result: _PublicRecord,
        intent: _PublicRecord,
        request: _PublicRecord,
    ) -> None:
        intent.selected_output = _SelectedOutputPresent({"status": "intent"})

    facade = _FakeFacade(
        selected_output=_SelectedOutputPresent({"status": "result"}),
        result_mutator=change_intent_value,
    )

    result = _drive_session(
        _adapter(monkeypatch, tmp_path, facade),
        _request(schemas=(schema,)),
    )

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "result_parse_failed"
    assert facade.evidence_calls == 1
    assert facade.calls == 1


@pytest.mark.parametrize(
    ("result_class", "expected_kind"),
    (("timed_out", "timeout"), ("cancelled", "cancelled")),
)
def test_millforge_adapter_composes_timeout_and_translates_timeout_or_cancel(  # noqa: E501
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    result_class: str,
    expected_kind: str,
) -> None:
    from millrace.adapters.runner_contract import AdapterErrorResult

    facade = _FakeFacade(result_class=result_class)
    result = _drive_session(
        _adapter(monkeypatch, tmp_path, facade, timeout_seconds=5),
        _request(),
    )

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == expected_kind
    assert facade.requests[0].timeout.timeout_seconds == 5
    assert not hasattr(result, "adapter_provenance")


@pytest.mark.parametrize(
    ("execute_error", "expected_kind"),
    (
        (TimeoutError(), "timeout"),
        (RuntimeError("execute failed"), "invocation_failed"),
    ),
)
def test_millforge_worker_translates_execute_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    execute_error: BaseException,
    expected_kind: str,
) -> None:
    from millrace.adapters.runner_contract import AdapterErrorResult

    facade = _FakeFacade(execute_error=execute_error)
    result = _drive_session(_adapter(monkeypatch, tmp_path, facade), _request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == expected_kind
    assert facade.calls == 1
    assert facade.close_calls == 0


def test_millforge_worker_enforces_selected_local_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import AdapterErrorResult

    class NeverCompletesFacade(_FakeFacade):
        async def execute(self, request: _PublicRecord) -> _PublicRecord:
            self.calls += 1
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    facade = NeverCompletesFacade()
    result = _drive_session(
        _adapter(monkeypatch, tmp_path, facade, timeout_seconds=0.01),
        _request(),
    )

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "timeout"
    assert facade.calls == 1


def test_millforge_adapter_runs_from_active_event_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import AdapterSuccessResult, StartedSession

    facade = _FakeFacade(selected_output=_SelectedOutputPresent({"status": "ok"}))
    adapter = _adapter(monkeypatch, tmp_path, facade)

    async def invoke() -> object:
        started = adapter.start_session(_request())
        assert isinstance(started, StartedSession)
        outcome = None
        deadline = time.monotonic() + 2
        while outcome is None and time.monotonic() < deadline:
            outcome = started.handle.poll_completion()
            await asyncio.sleep(0.001)
        return outcome

    result = asyncio.run(invoke())
    assert isinstance(result, AdapterSuccessResult)
    assert facade.evidence_calls == 1
    assert facade.calls == 1


def test_millforge_adapter_instruction_is_bounded_and_run_path_is_contained(  # noqa: E501
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import AdapterErrorResult

    facade = _FakeFacade()
    result = _drive_session(_adapter(monkeypatch, tmp_path, facade), _request())
    assert isinstance(result, AdapterErrorResult)
    assert facade.calls == 1
    provider_request = facade.requests[0]
    assert provider_request.task.instruction.encode("utf-8")
    assert Path(provider_request.run_directory.path).is_relative_to(tmp_path.resolve())

    for dispatch in (
        _dispatch(work_item_payload={"task": "nul\x00payload"}),
        _dispatch(selected_join_evidence=_join_evidence("nul\x00join")),
        _dispatch(work_item_payload={"task": "x" * 65_537}),
    ):
        bounded_facade = _FakeFacade()
        refused = _drive_session(
            _adapter(monkeypatch, tmp_path, bounded_facade),
            _request(dispatch=dispatch),
        )
        assert isinstance(refused, AdapterErrorResult)
        assert refused.error_kind == "input_too_large"
        assert bounded_facade.evidence_calls == 0
        assert bounded_facade.calls == 0


@pytest.mark.parametrize(
    "request_factory",
    (
        lambda: _request(
            selected_asset_material={"entrypoint": {"body": "secret-value"}},
        ),
        lambda: _request(
            dispatch=_dispatch(work_item_payload={"task": "secret-value"}),
        ),
        lambda: _request(
            dispatch=_dispatch(selected_join_evidence=_join_evidence("secret-value")),
        ),
    ),
)
def test_millforge_adapter_redacts_and_never_promotes_summary_or_paths(  # noqa: E501
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    request_factory: object,
) -> None:
    from millrace.adapters.runner_contract import (
        AdapterErrorResult,
        AdapterSuccessResult,
    )

    facade = _FakeFacade()
    adapter = _adapter(monkeypatch, tmp_path, facade)
    refused = _drive_session(adapter, request_factory())  # type: ignore[operator]

    assert isinstance(refused, AdapterErrorResult)
    assert refused.error_kind == "redaction_refused"
    assert facade.evidence_calls == 0
    assert facade.calls == 0

    facade = _FakeFacade()
    adapter = _adapter(monkeypatch, tmp_path, facade)
    result = _drive_session(
        adapter,
        _request(
            dispatch=_dispatch(
                terminal_options=(
                    {
                        "outcome_id": "outcome.complete",
                        "marker": "DONE",
                        "action_id": "action.complete",
                        "action_kind": "route",
                        "artifact_schema_id": None,
                    },
                )
            ),
            schemas=(),
        ),
    )

    assert isinstance(result, AdapterSuccessResult)
    assert result.artifact_payload_candidate is None
    assert "provider summary" not in repr(result)
    assert "provider-only-path" not in repr(result)
    assert "secret-value" not in repr(result)


def test_millforge_live_config_uses_only_public_factory_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    terminal_results = ("CUSTOM_BLOCKED", "CUSTOM_DONE")
    facade = _FakeFacade(
        terminal_result="CUSTOM_DONE",
        selected_output=_SelectedOutputPresent({"status": "ok"}),
    )
    facade.descriptor.legal_terminal_result_ids = terminal_results
    calls: dict[str, object] = {}
    adapter = _live_adapter(monkeypatch, tmp_path, facade, calls)

    result = _drive_session(
        adapter,
        _request(
            pin=_pin(legal_terminal_result_ids=terminal_results),
            mappings=(_mapping("CUSTOM_DONE"),),
        ),
    )

    from millrace.adapters.runner_contract import AdapterSuccessResult

    assert isinstance(result, AdapterSuccessResult)
    factory = calls["factory_kwargs"]
    assert isinstance(factory, dict)
    assert set(factory) == {
        "legal_terminal_results",
        "profile_id",
        "model_profile",
        "secret_ref",
        "secret_resolver",
        "cwd",
        "clock",
        "cancellation_resolver",
        "timeouts",
        "options",
    }
    assert factory["legal_terminal_results"] == terminal_results
    assert factory["profile_id"] == "profile-1"
    assert factory["model_profile"].profile_id == "profile-1"
    assert factory["secret_ref"].secret_id == "provider-key"
    assert factory["cwd"] == tmp_path.resolve()
    assert factory["options"].load_context_files is False
    assert factory["timeouts"].timeout_seconds == 10
    assert "http_transport" not in factory
    assert facade.calls == 1
    assert facade.close_calls == 1
    assert len(facade.requests[0].secret_refs) == 1
    assert facade.requests[0].secret_refs[0].secret_id == "provider-key"
    assert facade.requests[0].timeout.timeout_seconds == 10


def test_live_facade_construct_execute_and_close_share_one_sync_bridge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    facade = _FakeFacade(selected_output=_SelectedOutputPresent({"status": "ok"}))
    calls: dict[str, object] = {}
    adapter = _live_adapter(monkeypatch, tmp_path, facade, calls)

    result = _drive_session(adapter, _request())

    from millrace.adapters.runner_contract import AdapterSuccessResult

    assert isinstance(result, AdapterSuccessResult)
    assert calls["factory_calls"] == 1
    assert facade.evidence_calls == 1
    assert facade.calls == 1
    assert facade.close_calls == 1
    assert facade.events == ["factory", "preflight", "evidence", "execute", "close"]

    failed_facade = _FakeFacade(execute_error=RuntimeError("execute failed"))
    failed_calls: dict[str, object] = {}
    failed_adapter = _live_adapter(
        monkeypatch,
        tmp_path,
        failed_facade,
        failed_calls,
    )
    failed = _drive_session(failed_adapter, _request())

    from millrace.adapters.runner_contract import AdapterErrorResult

    assert isinstance(failed, AdapterErrorResult)
    assert failed.error_kind == "invocation_failed"
    assert failed_facade.calls == 1
    assert failed_facade.close_calls == 1
    assert failed_facade.events == [
        "factory",
        "preflight",
        "evidence",
        "execute",
        "close",
    ]

    close_failed_facade = _FakeFacade(
        selected_output=_SelectedOutputPresent({"status": "ok"}),
        close_error=RuntimeError("close failed"),
    )
    close_failed_calls: dict[str, object] = {}
    close_failed_adapter = _live_adapter(
        monkeypatch,
        tmp_path,
        close_failed_facade,
        close_failed_calls,
    )

    from millrace.adapters.runner_contract import StartedSession

    close_failed_started = close_failed_adapter.start_session(_request())
    assert isinstance(close_failed_started, StartedSession)
    deadline = time.monotonic() + 2
    close_failed = None
    while close_failed is None and time.monotonic() < deadline:
        close_failed = close_failed_started.handle.poll_completion()
        time.sleep(0.001)

    assert isinstance(close_failed, AdapterErrorResult)
    assert close_failed.error_kind == "invocation_failed"
    assert close_failed_started.handle.cleanup().disposition == "orphan_risk"
    assert close_failed_facade.calls == 1
    assert close_failed_facade.close_calls == 1
    assert close_failed_facade.events == [
        "factory",
        "preflight",
        "evidence",
        "execute",
        "close",
    ]


def test_live_facade_descriptor_drift_refuses_before_execute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import AdapterErrorResult

    facade = _FakeFacade()
    facade.descriptor.descriptor_sha256 = "c" * 64
    facade.events.clear()
    calls: dict[str, object] = {}
    adapter = _live_adapter(monkeypatch, tmp_path, facade, calls)

    result = _drive_session(adapter, _request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "selected_authority_refused"
    assert facade.evidence_calls == 0
    assert facade.calls == 0
    assert facade.close_calls == 1
    assert facade.events == ["factory", "preflight", "close"]


def test_live_millforge_active_event_loop_starts_worker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import AdapterSuccessResult, StartedSession

    facade = _FakeFacade(selected_output=_SelectedOutputPresent({"status": "ok"}))
    calls: dict[str, object] = {}
    adapter = _live_adapter(monkeypatch, tmp_path, facade, calls)

    async def invoke() -> object:
        started = adapter.start_session(_request())
        assert isinstance(started, StartedSession)
        outcome = None
        deadline = time.monotonic() + 2
        while outcome is None and time.monotonic() < deadline:
            outcome = started.handle.poll_completion()
            await asyncio.sleep(0.001)
        return outcome

    result = asyncio.run(invoke())
    assert isinstance(result, AdapterSuccessResult)
    assert calls["factory_calls"] == 1
    assert facade.calls == 1
    assert facade.close_calls == 1


def test_invalid_live_config_or_factory_failure_has_no_observation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import AdapterErrorResult

    facade = _FakeFacade()
    calls: dict[str, object] = {"factory_failure": ValueError("local failure")}
    adapter = _live_adapter(monkeypatch, tmp_path, facade, calls)

    result = _drive_session(adapter, _request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "invocation_failed"
    assert facade.evidence_calls == 0
    assert facade.calls == 0
    assert facade.close_calls == 0
    assert "live-test-secret" not in repr(result)


def test_missing_live_secret_refuses_before_factory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import AdapterErrorResult

    facade = _FakeFacade()
    calls: dict[str, object] = {}
    adapter = _live_adapter(monkeypatch, tmp_path, facade, calls)
    monkeypatch.delenv("MILLRACE_TEST_PROVIDER_KEY")

    result = _drive_session(adapter, _request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "invocation_failed"
    assert calls == {}
    assert facade.calls == 0


def test_millforge_invalid_public_profile_refuses_before_secret_or_factory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import AdapterErrorResult

    facade = _FakeFacade()
    calls: dict[str, object] = {}
    adapter = _live_adapter(
        monkeypatch,
        tmp_path,
        facade,
        calls,
        model_profile={
            "profile_id": "profile-1",
            "secret_ref": {"secret_id": "wrong", "env_var": "WRONG"},
        },
    )

    result = _drive_session(adapter, _request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "invocation_failed"
    assert "factory_calls" not in calls
    assert facade.calls == 0


def test_live_millforge_missing_selected_pin_refuses_before_secret_or_factory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import AdapterErrorResult

    facade = _FakeFacade()
    calls: dict[str, object] = {}
    adapter = _live_adapter(monkeypatch, tmp_path, facade, calls)

    result = _drive_session(
        adapter,
        replace(_request(), selected_component_pin=None),
    )

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "selected_authority_refused"
    assert calls == {}
    assert facade.calls == 0


def test_live_factory_cancellation_bridge_is_non_cancelled_correlation_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    facade = _FakeFacade()
    calls: dict[str, object] = {}
    adapter = _live_adapter(monkeypatch, tmp_path, facade, calls)
    request = replace(_request(), cancellation_token="cancel-1")

    _drive_session(adapter, request)

    factory = calls["factory_kwargs"]
    assert isinstance(factory, dict)
    resolver = factory["cancellation_resolver"]
    token = resolver.resolve(_record(cancellation_id="cancel-1"))
    assert token.cancellation_id == "cancel-1"
    assert token.is_cancelled() is False
    assert token.reason is None


def test_injected_millforge_facade_remains_caller_owned(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    facade = _FakeFacade(selected_output=_SelectedOutputPresent({"status": "ok"}))

    result = _drive_session(_adapter(monkeypatch, tmp_path, facade), _request())

    from millrace.adapters.runner_contract import AdapterSuccessResult

    assert isinstance(result, AdapterSuccessResult)
    assert facade.calls == 1
    assert facade.close_calls == 0


def test_millforge_live_config_never_enters_request_or_selected_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import (
        AdapterSuccessResult,
        runner_evidence_from_adapter_outcome,
    )
    from millrace.contracts.compiled_plan import canonical_authority_bytes

    facade = _FakeFacade(selected_output=_SelectedOutputPresent({"status": "ok"}))
    calls: dict[str, object] = {}
    adapter = _live_adapter(
        monkeypatch,
        tmp_path,
        facade,
        calls,
        model_profile={
            "profile_id": "profile-1",
            "secret_ref": {
                "secret_id": "provider-key",
                "env_var": "MILLRACE_TEST_PROVIDER_KEY",
            },
            "endpoint": {"base_url": "https://operator.example"},
        },
        secret_ref={
            "secret_id": "provider-key",
            "env_var": "MILLRACE_TEST_PROVIDER_KEY",
        },
    )
    request = _request()

    result = _drive_session(adapter, request)

    assert isinstance(result, AdapterSuccessResult)
    evidence = runner_evidence_from_adapter_outcome(result, request)
    projection_bytes = (
        canonical_authority_bytes(evidence.payload()),
        canonical_authority_bytes(asdict(result.dispatch_echo)),
        canonical_authority_bytes(
            {
                "component_pin": request.selected_component_pin,
                "terminal_mappings": request.selected_terminal_result_mappings,
                "artifact_schemas": request.selected_artifact_schemas,
            }
        ),
    )
    assert "live-test-secret" not in repr(adapter.config)
    assert str(tmp_path) not in repr(adapter.config)
    for local_value in (
        "live-test-secret",
        "provider-key",
        "https://operator.example",
        str(tmp_path),
    ):
        assert local_value not in repr(request)
        assert local_value not in repr(result)
        assert all(
            local_value.encode("utf-8") not in value for value in projection_bytes
        )


def test_millforge_live_config_snapshots_nested_provider_records(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    original_endpoint = "https://original.example"
    original_env_var = "MILLRACE_TEST_PROVIDER_KEY"
    model_profile: dict[str, object] = {
        "profile_id": "profile-1",
        "secret_ref": {
            "secret_id": "provider-key",
            "env_var": original_env_var,
            "metadata": {"endpoint_ref": original_endpoint},
        },
        "endpoint": {"base_url": original_endpoint},
        "authentication": {
            "secret_ref": {
                "secret_id": "provider-key",
                "env_var": original_env_var,
            },
        },
    }
    secret_ref: dict[str, object] = {
        "secret_id": "provider-key",
        "env_var": original_env_var,
        "metadata": {"endpoint_ref": original_endpoint},
    }
    facade = _FakeFacade(selected_output=_SelectedOutputPresent({"status": "ok"}))
    calls: dict[str, object] = {}
    adapter = _live_adapter(
        monkeypatch,
        tmp_path,
        facade,
        calls,
        model_profile=model_profile,
        secret_ref=secret_ref,
    )

    profile_secret_ref = cast(dict[str, object], model_profile["secret_ref"])
    profile_secret_ref["env_var"] = "MUTATED"
    cast(dict[str, object], profile_secret_ref["metadata"])["endpoint_ref"] = "mutated"
    cast(dict[str, object], model_profile["endpoint"])["base_url"] = (
        "https://mutated.example"
    )
    cast(dict[str, object], model_profile["authentication"])["secret_ref"] = {
        "secret_id": "mutated",
        "env_var": "MUTATED",
    }
    cast(dict[str, object], secret_ref["metadata"])["endpoint_ref"] = "mutated"
    secret_ref["env_var"] = "MUTATED"

    result = _drive_session(adapter, _request())

    from millrace.adapters.runner_contract import AdapterSuccessResult

    assert isinstance(result, AdapterSuccessResult)
    factory = calls["factory_kwargs"]
    assert isinstance(factory, dict)
    profile = factory["model_profile"]
    provider_secret_ref = factory["secret_ref"]
    assert profile.values["endpoint"]["base_url"] == original_endpoint
    assert profile.values["authentication"]["secret_ref"]["env_var"] == original_env_var
    assert provider_secret_ref.env_var == original_env_var
    assert provider_secret_ref.values["metadata"]["endpoint_ref"] == original_endpoint


def test_invalid_live_facade_is_closed_before_bounded_refusal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import AdapterErrorResult

    facade = _CloseOnlyFacade()
    calls: dict[str, object] = {}
    adapter = _live_adapter(monkeypatch, tmp_path, facade, calls)

    result = _drive_session(adapter, _request())

    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "invocation_failed"
    assert calls["factory_calls"] == 1
    assert facade.close_calls == 1
    assert facade.events == ["factory", "close"]


def test_invalid_live_facade_without_close_reports_orphan_risk(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters.runner_contract import AdapterErrorResult, StartedSession

    facade = _FakeFacade()
    facade.aclose = None  # type: ignore[method-assign]
    calls: dict[str, object] = {}
    adapter = _live_adapter(monkeypatch, tmp_path, facade, calls)

    started = adapter.start_session(_request())
    assert isinstance(started, StartedSession)
    deadline = time.monotonic() + 2
    outcome = None
    while outcome is None and time.monotonic() < deadline:
        outcome = started.handle.poll_completion()
        time.sleep(0.001)

    assert isinstance(outcome, AdapterErrorResult)
    assert outcome.error_kind == "invocation_failed"
    assert started.handle.cleanup().disposition == "orphan_risk"


def test_millforge_thread_start_failure_is_indeterminate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from millrace.adapters import millforge as millforge_module
    from millrace.adapters.runner_contract import StartIndeterminate

    facade = _FakeFacade(selected_output=_SelectedOutputPresent({"status": "ok"}))
    adapter = _adapter(monkeypatch, tmp_path, facade)

    def fail_start(_thread: object) -> None:
        raise RuntimeError("thread start failed")

    monkeypatch.setattr(millforge_module.threading.Thread, "start", fail_start)
    outcome = adapter.start_session(_request())

    assert isinstance(outcome, StartIndeterminate)
    assert outcome.durable_locator_metadata is None
    assert outcome.diagnostic_digest.startswith("sha256:")
    assert outcome.dispatch_echo.session_id == "session-1"
    assert facade.calls == 0


def test_python_311_import_without_millforge_preserves_codex(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import millrace
    from millrace.adapters import millforge as millforge_module
    from millrace.adapters.runner_contract import AdapterErrorResult

    facade = _FakeFacade()
    calls: dict[str, object] = {}
    adapter = _live_adapter(monkeypatch, tmp_path, facade, calls)
    monkeypatch.setattr(millforge_module, "_optional_provider", lambda: None)

    result = _drive_session(adapter, _request())

    assert millrace.__name__ == "millrace"
    assert isinstance(result, AdapterErrorResult)
    assert result.error_kind == "missing_opt_in_config"
    assert calls == {}
