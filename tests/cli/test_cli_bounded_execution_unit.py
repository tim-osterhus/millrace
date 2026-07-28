from __future__ import annotations

import hashlib
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest

from kernel.kernel_ping_scenarios import bootstrap_to_worker_claim
from millrace.compiler import compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.compiler.runner_bindings import SelectedRunnerAdapterPolicy
from millrace.contracts import QueueFamilyId
from millrace.contracts.compiled_plan import (
    AssetDeclaration,
    SelectedCompiledPlan,
)
from millrace.contracts.fingerprints import AuthorityFingerprint
from millrace.contracts.ids import RunnerBindingId
from millrace.contracts.state import RuntimeState
from millrace.contracts.transition import (
    AdmitPlan,
    ClaimWork,
    EnqueueWork,
    InitializeWorkspace,
    SelectDefaultPlan,
)
from millrace.kernel import apply, decide, empty_runtime_state
from millrace.testing import deterministic_context
from support.kernel_ping import (
    apply_accepted_input,
    compile_kernel_ping,
    kernel_ping_context,
    task_artifact_payload,
)

_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)


def _component_free_codex_source() -> dict[str, object]:
    from millrace.workflows import kernel_ping

    source = kernel_ping.workflow_source()
    runners = cast(list[dict[str, object]], source["runner_bindings"])
    runner = runners[0]
    runner["id"] = "kernel_ping.fake_local_runner"
    runner["adapter_kind"] = "fake_local"
    runner["stage_kind_ids"] = ("kernel_ping.taskmaster", "kernel_ping.worker")
    runner["required_capability_ids"] = ("capability.runner.invoke",)
    runner.pop("component_pin", None)
    runner.pop("terminal_result_mappings", None)
    source["runner_bindings"] = [runner]
    source["capabilities"] = [
        {
            "id": "capability.runner.invoke",
            "kind": "runner.invoke",
            "support_status": "supported",
            "grant_status": "granted",
            "approval_policy_id": None,
        }
    ]
    for stage in cast(list[dict[str, object]], source["stage_kinds"]):
        stage["runner_binding_id"] = runner["id"]
    for route in cast(list[dict[str, object]], source["external_enqueue_routes"]):
        route["runner_binding_id"] = runner["id"]
    for action in cast(list[dict[str, object]], source["terminal_actions"]):
        if action.get("runner_binding_id") is not None:
            action["runner_binding_id"] = runner["id"]
    return source


def _compile_component_free_codex(
    source: dict[str, object] | None = None,
) -> tuple[SelectedCompiledPlan, str]:
    result = compile_workflow(
        source or _component_free_codex_source(),
        selected_runner_policy=_CODEX_POLICY,
    )
    assert result.plan is not None
    return result.plan, authority_fingerprint(result.plan)


def _runtime(tmp_path: Path, state: RuntimeState | None = None) -> object:
    from millrace.adapters.cli.context import CliWorkspacePaths, OpenRuntimeContext
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.sqlite import SQLiteRuntimeStore
    from millrace.testing import materialize_fake_runner_session_cas

    root = tmp_path / "workspace"
    db_path = root / ".millrace" / "runtime.sqlite3"
    cas_path = root / ".millrace" / "cas"
    db_path.parent.mkdir(parents=True)
    cas_path.mkdir(parents=True)
    store = SQLiteRuntimeStore.initialize(db_path)
    cas_store = ContentAddressedByteStore(cas_path)
    runtime_state = materialize_fake_runner_session_cas(
        state=state or empty_runtime_state(),
        cas_store=cas_store,
    )
    store.persist_runtime_state(runtime_state, cas_store)
    return OpenRuntimeContext(
        paths=CliWorkspacePaths(root, db_path, cas_path),
        store=store,
        cas_store=cas_store,
    )


def _reopen_runtime(runtime: object) -> object:
    from millrace.adapters.cli.context import OpenRuntimeContext
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    runtime.store.close()
    return OpenRuntimeContext(
        paths=runtime.paths,
        store=SQLiteRuntimeStore.open(runtime.paths.db_path),
        cas_store=ContentAddressedByteStore(runtime.paths.cas_path),
    )


def _load(runtime: object) -> RuntimeState:
    return runtime.store.load_runtime_state(runtime.cas_store)


def _durable_file_bytes(runtime: object) -> dict[str, bytes]:
    workspace_path = runtime.paths.workspace_path
    return {
        path.relative_to(workspace_path).as_posix(): path.read_bytes()
        for path in sorted(workspace_path.rglob("*"))
        if path.is_file()
    }


def _ready_state() -> tuple[RuntimeState, str]:
    plan, fingerprint = _compile_component_free_codex()
    return _ready_state_for_plan(plan, fingerprint)


def _ready_state_for_plan(
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> tuple[RuntimeState, str]:
    state = empty_runtime_state()
    for transition_input in (
        InitializeWorkspace("init"),
        AdmitPlan("admit", selected_plan=plan, authority_fingerprint=fingerprint),
        SelectDefaultPlan("select", authority_fingerprint=fingerprint),
        EnqueueWork(
            "enqueue",
            queue_family_id=QueueFamilyId("prompt"),
            payload={"prompt_id": "prompt-1", "body": "Build the proof"},
        ),
    ):
        state = apply_accepted_input(
            state,
            transition_input,
            kernel_ping_context(transition_input.input_id),
        )
    return state, fingerprint


def _ready_state_with_stage_timeouts() -> tuple[RuntimeState, str]:

    source = _component_free_codex_source()
    runners = cast(list[dict[str, object]], source["runner_bindings"])
    stages = cast(list[dict[str, object]], source["stage_kinds"])
    actions = cast(list[dict[str, object]], source["terminal_actions"])
    runners[0]["stage_kind_ids"] = ("kernel_ping.taskmaster",)
    runners[0]["invocation_timeout_seconds"] = 3600
    runners.append(
        {
            "id": "kernel_ping.worker_runner",
            "adapter_kind": "codex",
            "stage_kind_ids": ("kernel_ping.worker",),
            "required_capability_ids": ("capability.runner.invoke",),
            "invocation_timeout_seconds": 1800,
            "presentation": {},
        }
    )
    next(stage for stage in stages if stage["id"] == "kernel_ping.worker")[
        "runner_binding_id"
    ] = "kernel_ping.worker_runner"
    next(
        action
        for action in actions
        if action["id"] == "kernel_ping.route_taskmaster_success"
    )["runner_binding_id"] = "kernel_ping.worker_runner"
    plan, fingerprint = _compile_component_free_codex(source)
    return _ready_state_for_plan(plan, fingerprint)


def _state_with_runner_kind(kind: str) -> tuple[RuntimeState, str]:
    from millrace.compiler.canonical import authority_fingerprint

    state, fingerprint = _ready_state()
    admitted = state.admitted_plans[fingerprint]
    selected_plan = admitted.selected_plan
    binding = next(
        binding
        for binding in selected_plan.runner_bindings
        if str(binding.id) == "kernel_ping.fake_local_runner"
    )
    replacement = replace(binding, adapter_kind=kind)
    changed_plan = replace(selected_plan, runner_bindings=(replacement,))
    changed_fingerprint = AuthorityFingerprint(authority_fingerprint(changed_plan))
    changed_plan_ref = replace(
        admitted.plan_ref,
        authority_fingerprint=changed_fingerprint,
    )
    work_item = state.work_items["work-prompt"]
    activation = state.activations["activation-taskmaster"]
    changed_admitted = replace(
        admitted,
        plan_ref=changed_plan_ref,
        selected_plan=changed_plan,
    )
    changed_state = replace(
        state,
        admitted_plans={changed_fingerprint: changed_admitted},
        default_plan_ref=changed_plan_ref,
        work_items={
            work_item.ref.work_item_id: replace(
                work_item,
                ref=replace(work_item.ref, plan_ref=changed_plan_ref),
            )
        },
        activations={
            activation.activation_id: replace(
                activation,
                plan_ref=changed_plan_ref,
            )
        },
    )
    return changed_state, changed_fingerprint


def _state_with_selected_plan(
    state: RuntimeState,
    plan: SelectedCompiledPlan,
) -> tuple[RuntimeState, str]:
    from millrace.compiler.canonical import authority_fingerprint

    fingerprint = AuthorityFingerprint(authority_fingerprint(plan))
    admitted = next(iter(state.admitted_plans.values()))
    plan_ref = replace(admitted.plan_ref, authority_fingerprint=fingerprint)
    work_items = {
        work_id: replace(
            work_item,
            ref=replace(work_item.ref, plan_ref=plan_ref),
        )
        for work_id, work_item in state.work_items.items()
    }
    activations = {
        activation_id: replace(activation, plan_ref=plan_ref)
        for activation_id, activation in state.activations.items()
    }
    runs = {
        run_id: replace(
            run,
            run_ref=replace(run.run_ref, plan_ref=plan_ref),
        )
        for run_id, run in state.runs.items()
    }
    return (
        replace(
            state,
            admitted_plans={
                fingerprint: replace(
                    admitted,
                    plan_ref=plan_ref,
                    selected_plan=plan,
                )
            },
            default_plan_ref=plan_ref,
            work_items=work_items,
            activations=activations,
            runs=runs,
        ),
        fingerprint,
    )


def _claim_only_state() -> RuntimeState:
    state, _fingerprint = _ready_state()
    transition_input = __import__(
        "millrace.contracts.transition",
        fromlist=["ClaimWork"],
    ).ClaimWork("claim", activation_id="activation-taskmaster")
    decision = decide(
        state,
        transition_input,
        deterministic_context(
            transition_id="transition-claim",
            run_id="run-taskmaster",
            claim_id="claim-taskmaster",
            fencing_token="fence-taskmaster",
        ),
    )
    assert decision.accepted is True
    return apply(state, decision)


def _claim_only_runtime(tmp_path: Path) -> tuple[object, RuntimeState]:
    state = _claim_only_state()
    return _runtime(tmp_path, state), state


def _codex_success_config(
    *,
    marker: str = "TASK_COMPLETE",
    capture_bundle_path: Path | None = None,
    timeout_seconds: float = 5,
) -> object:
    from millrace.adapters.codex import CodexAdapter, CodexAdapterConfig
    from millrace.adapters.runner_contract import AdapterLocalConfig, RedactionPolicy

    env_allowlist = (
        {}
        if capture_bundle_path is None
        else {"CAPTURE_BUNDLE_PATH": str(capture_bundle_path)}
    )
    return AdapterLocalConfig(
        adapters={
            "codex": CodexAdapter(
                CodexAdapterConfig(
                    adapter_id="codex-default",
                    wrapper_mode="offline_fake",
                    wrapper_argv=(sys.executable, "-c", _codex_success_wrapper(marker)),
                    cwd=Path.cwd(),
                    env_allowlist=env_allowlist,
                    timeout_seconds=timeout_seconds,
                    max_input_bundle_bytes=16384,
                    max_stdout_bytes=8192,
                    max_stderr_diagnostic_bytes=512,
                    redaction_policy=RedactionPolicy(policy_id="redact-default"),
                )
            )
        }
    )


def _codex_error_config() -> object:
    from millrace.adapters.codex import CodexAdapter, CodexAdapterConfig
    from millrace.adapters.runner_contract import AdapterLocalConfig, RedactionPolicy

    return AdapterLocalConfig(
        adapters={
            "codex": CodexAdapter(
                CodexAdapterConfig(
                    adapter_id="codex-default",
                    wrapper_mode="offline_fake",
                    wrapper_argv=(sys.executable, "-c", "import sys; sys.exit(7)"),
                    cwd=Path.cwd(),
                    env_allowlist={},
                    timeout_seconds=5,
                    max_input_bundle_bytes=8192,
                    max_stdout_bytes=8192,
                    max_stderr_diagnostic_bytes=512,
                    redaction_policy=RedactionPolicy(policy_id="redact-default"),
                )
            )
        }
    )


def _codex_timeout_config() -> object:
    from millrace.adapters.codex import CodexAdapter, CodexAdapterConfig
    from millrace.adapters.runner_contract import AdapterLocalConfig, RedactionPolicy

    return AdapterLocalConfig(
        adapters={
            "codex": CodexAdapter(
                CodexAdapterConfig(
                    adapter_id="codex-default",
                    wrapper_mode="offline_fake",
                    wrapper_argv=(
                        sys.executable,
                        "-c",
                        "import time; time.sleep(1)",
                    ),
                    cwd=Path.cwd(),
                    env_allowlist={},
                    timeout_seconds=0.01,
                    max_input_bundle_bytes=16384,
                    max_stdout_bytes=8192,
                    max_stderr_diagnostic_bytes=512,
                    redaction_policy=RedactionPolicy(policy_id="redact-default"),
                )
            )
        }
    )


def _codex_mismatch_config() -> object:
    from millrace.adapters.codex import CodexAdapter, CodexAdapterConfig
    from millrace.adapters.runner_contract import AdapterLocalConfig, RedactionPolicy

    wrapper = _codex_success_wrapper("TASK_COMPLETE").replace(
        "'run_id': dispatch['run_id'],",
        "'run_id': 'wrong-run',",
    )
    return AdapterLocalConfig(
        adapters={
            "codex": CodexAdapter(
                CodexAdapterConfig(
                    adapter_id="codex-default",
                    wrapper_mode="offline_fake",
                    wrapper_argv=(sys.executable, "-c", wrapper),
                    cwd=Path.cwd(),
                    env_allowlist={},
                    timeout_seconds=5,
                    max_input_bundle_bytes=16384,
                    max_stdout_bytes=8192,
                    max_stderr_diagnostic_bytes=512,
                    redaction_policy=RedactionPolicy(policy_id="redact-default"),
                )
            )
        }
    )


def _codex_success_wrapper(marker: str) -> str:
    artifact_json = json.dumps(task_artifact_payload())
    return (
        "import json, os, pathlib, sys\n"
        "bundle = json.loads(sys.stdin.read())\n"
        "capture = os.environ.get('CAPTURE_BUNDLE_PATH')\n"
        "if capture:\n"
        "    pathlib.Path(capture).write_text(json.dumps(bundle, sort_keys=True))\n"
        "dispatch = bundle['dispatch_envelope']\n"
        "marker = " + repr(marker) + "\n"
        "if marker == 'AUTO':\n"
        "    marker = 'WORK_COMPLETE' if dispatch['stage_kind_id'] == "
        "'kernel_ping.worker' else 'TASK_COMPLETE'\n"
        "artifact = {} if marker == 'WORK_COMPLETE' else " + artifact_json + "\n"
        "echo = {\n"
        "    'run_id': dispatch['run_id'],\n"
        "    'session_id': dispatch['session_id'],\n"
        "    'dispatch_generation': dispatch['dispatch_generation'],\n"
        "    'session_fencing_token': dispatch['session_fencing_token'],\n"
        "    'claim_id': dispatch['claim_id'],\n"
        "    'generation': dispatch['generation'],\n"
        "    'fencing_token': dispatch['fencing_token'],\n"
        "    'plan_fingerprint': dispatch['plan_fingerprint'],\n"
        "    'stage_kind_id': dispatch['stage_kind_id'],\n"
        "    'graph_node_id': dispatch['graph_node_id'],\n"
        "    'runner_binding_id': dispatch['runner_binding_id'],\n"
        "    'correlation_id': bundle['correlation_id'],\n"
        "}\n"
        "print(json.dumps({\n"
        "    'outcome_kind': 'success',\n"
        "    'adapter_id': bundle['adapter_id'],\n"
        "    'dispatch_echo': echo,\n"
        "    'redaction_policy_id': bundle['redaction_policy']['policy_id'],\n"
        "    'marker': marker,\n"
        "    'captured_stdout': None,\n"
        "    'captured_stderr': None,\n"
        "    'structured_provider_response': {},\n"
        "    'artifact_payload_candidate': artifact,\n"
        "    'observation_payload_candidate': {'summary': 'ok'},\n"
        "    'evidence_construction_diagnostics': {},\n"
        "}, sort_keys=True))\n"
    )


_MILLFORGE_CAPABILITIES = (
    "terminal.intent",
    "unrestricted.filesystem.read",
    "unrestricted.filesystem.write",
    "unrestricted.process.execute",
)
_MILLFORGE_RESULTS = ("BLOCKED", "TASK_COMPLETE")
_MILLFORGE_DESCRIPTOR_SHA256 = (
    "0bace7b27871b03cd7ffe59951953348b3da3214536178d6f447a21de4403464"
)
_MILLFORGE_WORKER_RESULTS = ("BLOCKED", "NEEDS_REVIEW", "WORK_COMPLETE")
_MILLFORGE_WORKER_DESCRIPTOR_SHA256 = (
    "d6b5c75f48565b939ee4d6e30b83e3ad203764b7bda02890ca515a9bfb3318f0"
)
_MILLFORGE_PLAN_FINGERPRINT = (
    "sha256:29d40efa187bef7c2ad2a143f8a685a6f6dbb21dcfdf05258b50c1c1c2586d42"
)


def _millforge_source() -> dict[str, object]:
    from millrace.workflows import kernel_ping

    return kernel_ping.workflow_source()


def _active_millforge_state_with_codex_default() -> tuple[
    RuntimeState,
    SelectedCompiledPlan,
    str,
    str,
]:

    millforge_plan, millforge_fingerprint = compile_kernel_ping(_millforge_source())
    assert millforge_fingerprint == _MILLFORGE_PLAN_FINGERPRINT
    state, _fingerprint = _ready_state_for_plan(
        millforge_plan,
        millforge_fingerprint,
    )
    claim = ClaimWork(
        "claim-millforge-taskmaster",
        activation_id="activation-taskmaster",
    )
    state = apply_accepted_input(
        state,
        claim,
        deterministic_context(
            transition_id="transition-claim-millforge-taskmaster",
            run_id="run-millforge-taskmaster",
            claim_id="claim-millforge-taskmaster",
            fencing_token="fence-millforge-taskmaster",
        ),
    )

    codex_plan, codex_fingerprint = _compile_component_free_codex()
    for transition_input in (
        AdmitPlan(
            "admit-current-codex-default",
            selected_plan=codex_plan,
            authority_fingerprint=codex_fingerprint,
        ),
        SelectDefaultPlan(
            "select-current-codex-default",
            authority_fingerprint=codex_fingerprint,
        ),
    ):
        state = apply_accepted_input(
            state,
            transition_input,
            deterministic_context(
                transition_id=f"transition-{transition_input.input_id}",
            ),
        )
    assert state.default_plan_ref is not None
    assert str(state.default_plan_ref.authority_fingerprint) == codex_fingerprint
    assert codex_fingerprint != millforge_fingerprint
    return state, millforge_plan, millforge_fingerprint, codex_fingerprint


def _provider_requirements_digest(requirements: tuple[object, ...]) -> str | None:
    if not requirements:
        return None
    payload = [
        {
            "required": item.selected_output.required,
            "schema_sha256": item.selected_output.schema_sha256,
            "terminal_result": item.terminal_result,
        }
        for item in sorted(
            requirements,
            key=lambda item: item.terminal_result.encode("utf-8"),
        )
    ]
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class _OfflineSelectedOutputRequirement(SimpleNamespace):
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


class _OfflineSelectedOutputPresent(SimpleNamespace):
    def __init__(self, *, value: object) -> None:
        super().__init__(present=True, value=value)


class _OfflineSelectedOutputAbsent(SimpleNamespace):
    def __init__(self) -> None:
        super().__init__(present=False)


def _offline_millforge_module() -> ModuleType:
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
        "HarnessExecutionRequest",
        "TerminalSelectedOutputRequirement",
    ):
        setattr(module, name, lambda **kwargs: SimpleNamespace(**kwargs))
    module.SelectedOutputRequirement = _OfflineSelectedOutputRequirement
    module.SelectedOutputPresent = _OfflineSelectedOutputPresent
    module.SelectedOutputAbsent = _OfflineSelectedOutputAbsent
    return module


class _RecordingMillforgeFacade:
    def __init__(
        self,
        *,
        artifact_payload: object,
        drift_descriptor: bool = False,
        legal_terminal_results: tuple[str, ...] = _MILLFORGE_RESULTS,
        terminal_result: str = "TASK_COMPLETE",
    ) -> None:
        descriptor_sha256 = (
            _MILLFORGE_DESCRIPTOR_SHA256
            if legal_terminal_results == _MILLFORGE_RESULTS
            else _MILLFORGE_WORKER_DESCRIPTOR_SHA256
        )
        descriptor = SimpleNamespace(
            runner_id="millforge-base",
            runner_version=2,
            package_name="millforge",
            package_version="0.1.0",
            descriptor_sha256=descriptor_sha256,
            required_capability_ids=_MILLFORGE_CAPABILITIES,
            legal_terminal_result_ids=legal_terminal_results,
        )
        self.descriptor = (
            SimpleNamespace(**{**vars(descriptor), "descriptor_sha256": "c" * 64})
            if drift_descriptor
            else descriptor
        )
        self.components = SimpleNamespace(
            options=SimpleNamespace(load_context_files=False),
            metadata=SimpleNamespace(context_file_count=0),
            compiled_plan=SimpleNamespace(
                harness_id="millforge-base",
                harness_version=2,
                compiled_sha256="b" * 64,
            ),
            capability_envelope=SimpleNamespace(
                grants=tuple(
                    SimpleNamespace(capability_id=capability_id)
                    for capability_id in _MILLFORGE_CAPABILITIES
                ),
            ),
            model_profile=SimpleNamespace(profile_id="profile-adapt-0002e"),
        )
        self.artifact_payload = artifact_payload
        self.terminal_result = terminal_result
        self.evidence_calls = 0
        self.execute_calls = 0
        self.close_calls = 0
        self.factory_calls = 0
        self.factory_kwargs: dict[str, object] = {}
        self.provider_requests: list[object] = []

    def invocation_evidence_for(self, request: object) -> object:
        self.evidence_calls += 1
        return SimpleNamespace(
            request_id=request.request_id,
            run_id=request.run_id,
            descriptor_sha256=self.descriptor.descriptor_sha256,
            selected_output_requirements_sha256=_provider_requirements_digest(
                request.selected_output_requirements,
            ),
            context_file_count=0,
        )

    async def execute(self, request: object) -> object:
        self.execute_calls += 1
        self.provider_requests.append(request)
        requirement = next(
            item
            for item in request.selected_output_requirements
            if item.terminal_result == self.terminal_result
        ).selected_output
        provider = sys.modules["millforge"]
        selected_output = provider.SelectedOutputPresent(
            value=json.loads(json.dumps(self.artifact_payload)),
        )
        intent = SimpleNamespace(
            request_id=request.request_id,
            run_id=request.run_id,
            stage=request.stage,
            terminal_result=self.terminal_result,
            selected_output=selected_output,
            selected_output_schema_sha256=requirement.schema_sha256,
        )
        return SimpleNamespace(
            status="completed",
            result_class="domain_terminal",
            request_id=request.request_id,
            run_id=request.run_id,
            stage=request.stage,
            terminal_intent=intent,
            compiled_harness=request.compiled_harness,
            selected_output=selected_output,
            selected_output_schema_sha256=requirement.schema_sha256,
        )

    async def aclose(self) -> None:
        self.close_calls += 1


def _millforge_live_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    facade: _RecordingMillforgeFacade,
) -> tuple[object, object]:
    millforge = pytest.importorskip(
        "millforge",
        reason="restart integration proof requires the optional provider",
    )

    from millrace.adapters.millforge import MillforgeAdapter, MillforgeAdapterConfig
    from millrace.adapters.runner_contract import (
        AdapterInvocationRequest,
        AdapterLocalConfig,
        RedactionPolicy,
    )

    class CapturingMillforgeAdapter(MillforgeAdapter):
        def __init__(self, config: MillforgeAdapterConfig) -> None:
            super().__init__(config)
            self.request: AdapterInvocationRequest | None = None

        def invoke(self, request: AdapterInvocationRequest) -> object:
            self.request = request
            return super().invoke(request)

    secret_ref = millforge.SecretRef(
        secret_id="adapt-0002e-provider-key",
        env_var="MILLRACE_ADAPT_0002E_PROVIDER_KEY",
    )
    profile = millforge.ResolvedModelProfile(
        profile_id="profile-adapt-0002e",
        provider_id="offline-fake",
        model_id="offline-fake",
        endpoint=millforge.EndpointConfig(base_url="https://offline.invalid/v1"),
        authentication=millforge.AuthenticationPolicy(
            scheme=millforge.AuthenticationScheme.BEARER,
            secret_ref=secret_ref,
        ),
        source_digest="sha256:adapt-0002e-offline-fake",
    )

    async def create_live_facade(**kwargs: object) -> object:
        facade.factory_calls += 1
        facade.factory_kwargs = kwargs
        return facade

    monkeypatch.setattr(
        millforge,
        "create_millforge_base_live_runner",
        create_live_facade,
    )
    monkeypatch.setenv("MILLRACE_ADAPT_0002E_PROVIDER_KEY", "offline-secret")
    adapter = CapturingMillforgeAdapter(
        MillforgeAdapterConfig.for_live(
            adapter_id="millforge-adapt-0002e",
            workspace_root=tmp_path.resolve(),
            timeout_seconds=30,
            redaction_policy=RedactionPolicy(policy_id="adapt-0002e-redaction"),
            model_profile=profile.model_dump(mode="json"),
            secret_ref=secret_ref.model_dump(mode="json"),
        )
    )
    return AdapterLocalConfig(adapters={"millforge": adapter}), adapter


def _millforge_offline_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    facade: _RecordingMillforgeFacade,
) -> tuple[object, object]:
    from millrace.adapters.millforge import MillforgeAdapter, MillforgeAdapterConfig
    from millrace.adapters.runner_contract import (
        AdapterInvocationRequest,
        AdapterLocalConfig,
        RedactionPolicy,
    )

    class CapturingMillforgeAdapter(MillforgeAdapter):
        def __init__(self, config: MillforgeAdapterConfig) -> None:
            super().__init__(config)
            self.request: AdapterInvocationRequest | None = None

        def invoke(self, request: AdapterInvocationRequest) -> object:
            self.request = request
            return super().invoke(request)

    monkeypatch.setitem(sys.modules, "millforge", _offline_millforge_module())
    adapter = CapturingMillforgeAdapter(
        MillforgeAdapterConfig(
            adapter_id="millforge-offline",
            facade=facade,
            workspace_root=tmp_path,
            timeout_seconds=30,
            redaction_policy=RedactionPolicy(policy_id="millforge-offline"),
        )
    )
    return AdapterLocalConfig(adapters={"millforge": adapter}), adapter


def test_kernel_ping_millforge_needs_review_projects_runtime_authority_and_restarts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_worker_claim(
        plan,
        fingerprint,
        task_artifact=task_artifact_payload(
            objective="Prove the durable NEEDS_REVIEW route",
        ),
    )
    runtime = _reopen_runtime(_runtime(tmp_path, state))
    selected_output = {
        "incident_kind": "kernel_ping.task_incident",
        "incident_version": 999,
        "source_prompt_id": "hostile-prompt",
        "source_task_artifact_id": "hostile-task-artifact",
        "worker_run_id": "hostile-worker-run",
        "reason": "insufficient_task_detail",
        "worker_summary": "The task omits an executable acceptance command.",
        "missing_details": ["exact command", "expected output"],
        "requested_taskmaster_action": "revise_task_artifact",
    }
    facade = _RecordingMillforgeFacade(
        artifact_payload=selected_output,
        legal_terminal_results=("BLOCKED", "NEEDS_REVIEW", "WORK_COMPLETE"),
        terminal_result="NEEDS_REVIEW",
    )
    local_config, adapter = _millforge_offline_config(
        monkeypatch,
        tmp_path,
        facade,
    )

    result = run_bounded_execution_unit(
        runtime,
        activation_id="activation-worker",
        adapter_kind=None,
        local_config=local_config,
    )

    assert result.code == "observation_accepted", result.observation_refusal_reason
    request = adapter.request
    assert request is not None
    assert request.dispatch_envelope.runner_binding_id == "kernel_ping.worker_runner"
    assert request.selected_component_pin == next(
        binding.component_pin
        for binding in plan.runner_bindings
        if str(binding.id) == "kernel_ping.worker_runner"
    )
    assert tuple(
        mapping.runner_result_id
        for mapping in request.selected_terminal_result_mappings
    ) == ("BLOCKED", "NEEDS_REVIEW", "WORK_COMPLETE")

    runtime = _reopen_runtime(runtime)
    reloaded = _load(runtime)
    incident_work = next(
        work_item
        for work_item in reloaded.work_items.values()
        if str(work_item.queue_family_id) == "task_incident"
    )
    assert incident_work.payload == {
        "incident_kind": "kernel_ping.task_incident",
        "incident_version": 1,
        "source_prompt_id": "prompt-1",
        "source_task_artifact_id": "work-task-artifact",
        "worker_run_id": "run-worker",
        "reason": "insufficient_task_detail",
        "worker_summary": selected_output["worker_summary"],
        "missing_details": tuple(cast(list[str], selected_output["missing_details"])),
        "requested_taskmaster_action": "revise_task_artifact",
    }
    review_activation = next(
        activation
        for activation in reloaded.activations.values()
        if activation.work_item_id == incident_work.ref.work_item_id
    )
    claim = ClaimWork(
        "claim-review-taskmaster",
        activation_id=review_activation.activation_id,
    )
    claim_decision = decide(
        reloaded,
        claim,
        deterministic_context(
            transition_id="transition-claim-review-taskmaster",
            run_id="run-review-taskmaster",
            claim_id="claim-review-taskmaster",
            fencing_token="fence-review-taskmaster",
        ),
    )

    assert claim_decision.accepted is True
    claimed = apply(reloaded, claim_decision)
    assert claimed.runs["run-review-taskmaster"].stage_kind_id == (
        review_activation.stage_kind_id
    )
    assert str(claimed.runs["run-review-taskmaster"].runner_binding_id) == (
        "kernel_ping.taskmaster_runner"
    )


def _tracking_codex_config(tmp_path: Path) -> tuple[object, object]:
    from millrace.adapters.codex import CodexAdapter, CodexAdapterConfig
    from millrace.adapters.runner_contract import (
        AdapterInvocationRequest,
        AdapterLocalConfig,
        RedactionPolicy,
    )

    class TrackingCodexAdapter(CodexAdapter):
        def __init__(self) -> None:
            super().__init__(
                CodexAdapterConfig(
                    adapter_id="codex-current-default",
                    wrapper_mode="missing",
                    wrapper_argv=None,
                    cwd=tmp_path,
                    env_allowlist={},
                    timeout_seconds=5,
                    max_input_bundle_bytes=8192,
                    max_stdout_bytes=8192,
                    max_stderr_diagnostic_bytes=512,
                    redaction_policy=RedactionPolicy(policy_id="codex-tracker"),
                )
            )
            self.calls = 0
            self.request: AdapterInvocationRequest | None = None

        def start_session(self, request: AdapterInvocationRequest) -> object:
            self.calls += 1
            self.request = request
            return super().start_session(request)

    adapter = TrackingCodexAdapter()
    return AdapterLocalConfig(adapters={"codex": adapter}), adapter


def test_active_codex_plan_is_not_rebound_to_millforge_default(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, codex_fingerprint = _ready_state()
    current_plan, current_fingerprint = compile_kernel_ping()
    assert current_fingerprint != codex_fingerprint
    assert {binding.adapter_kind for binding in current_plan.runner_bindings} == {
        "millforge"
    }
    runtime = _reopen_runtime(_runtime(tmp_path, state))
    local_config, adapter = _tracking_codex_config(tmp_path)

    result = run_bounded_execution_unit(runtime, local_config=local_config)

    assert result.code == "adapter_failure"
    assert adapter.calls == 1
    assert adapter.request is not None
    assert adapter.request.selected_adapter_kind == "codex"
    assert adapter.request.selected_component_pin is None
    assert adapter.request.selected_terminal_result_mappings == ()
    assert adapter.request.dispatch_envelope.plan_fingerprint == codex_fingerprint


def test_restarted_active_millforge_run_uses_persisted_plan_not_current_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, millforge_plan, millforge_fingerprint, codex_fingerprint = (
        _active_millforge_state_with_codex_default()
    )
    runtime = _reopen_runtime(_runtime(tmp_path, state))
    before = _load(runtime)
    assert before.default_plan_ref is not None
    assert str(before.default_plan_ref.authority_fingerprint) == codex_fingerprint
    active_run = before.runs["run-millforge-taskmaster"]
    assert str(active_run.run_ref.plan_ref.authority_fingerprint) == (
        millforge_fingerprint
    )
    assert before.default_plan_ref.authority_fingerprint != (
        active_run.run_ref.plan_ref.authority_fingerprint
    )
    current_default = before.admitted_plans[
        before.default_plan_ref.authority_fingerprint
    ].selected_plan
    assert current_default.runner_bindings[0].adapter_kind == "codex"
    facade = _RecordingMillforgeFacade(
        artifact_payload=task_artifact_payload(
            objective="Use the persisted Millforge plan",
        )
    )
    local_config, adapter = _millforge_live_config(
        monkeypatch,
        tmp_path,
        facade,
    )

    result = run_bounded_execution_unit(
        runtime,
        activation_id="activation-taskmaster",
        adapter_kind=None,
        local_config=local_config,
    )

    request = adapter.request
    assert result.code == "observation_accepted"
    assert request is not None
    assert request.selected_adapter_kind == "millforge"
    assert request.dispatch_envelope.plan_fingerprint == millforge_fingerprint
    assert request.dispatch_envelope.plan_fingerprint != codex_fingerprint
    assert request.dispatch_envelope.runner_binding_id == (
        "kernel_ping.fake_local_runner"
    )
    assert request.selected_component_pin == (
        millforge_plan.runner_bindings[0].component_pin
    )
    assert (
        tuple(
            mapping.runner_result_id
            for mapping in request.selected_terminal_result_mappings
        )
        == _MILLFORGE_RESULTS
    )
    assert facade.factory_calls == 1
    assert facade.factory_kwargs["legal_terminal_results"] == _MILLFORGE_RESULTS
    assert facade.provider_requests[0].run_id == "run-millforge-taskmaster"


def test_restarted_active_millforge_run_uses_exact_persisted_component_pin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, millforge_plan, millforge_fingerprint, _codex_fingerprint = (
        _active_millforge_state_with_codex_default()
    )
    runtime = _reopen_runtime(_runtime(tmp_path, state))
    facade = _RecordingMillforgeFacade(
        artifact_payload=task_artifact_payload(
            objective="Retain exact component authority after restart",
        )
    )
    local_config, adapter = _millforge_live_config(
        monkeypatch,
        tmp_path,
        facade,
    )

    result = run_bounded_execution_unit(
        runtime,
        activation_id="activation-taskmaster",
        adapter_kind=None,
        local_config=local_config,
    )
    after = _load(runtime)

    request = adapter.request
    assert result.code == "observation_accepted"
    assert request is not None
    pin = request.selected_component_pin
    assert pin is not None
    assert (
        pin.component_kind,
        pin.component_id,
        pin.component_version,
        pin.provider_distribution,
        pin.provider_version,
        pin.descriptor_media_type,
        pin.descriptor_sha256,
        tuple(str(value) for value in pin.required_capability_ids),
        pin.legal_terminal_result_ids,
    ) == (
        "runner",
        "millforge-base",
        "2",
        "millforge",
        "0.1.0",
        "application/json",
        _MILLFORGE_DESCRIPTOR_SHA256,
        _MILLFORGE_CAPABILITIES,
        _MILLFORGE_RESULTS,
    )
    expected_mappings = millforge_plan.runner_bindings[0].terminal_result_mappings
    assert request.selected_terminal_result_mappings == expected_mappings
    assert tuple(
        (mapping.runner_result_id, str(mapping.outcome_id))
        for mapping in expected_mappings
    ) == (
        ("BLOCKED", "kernel_ping.taskmaster.blocked"),
        ("TASK_COMPLETE", "kernel_ping.taskmaster.task_complete"),
    )
    assert tuple(str(schema.id) for schema in request.selected_artifact_schemas) == (
        "kernel_ping.task_artifact",
    )
    provider_request = facade.provider_requests[0]
    assert tuple(
        item.terminal_result for item in provider_request.selected_output_requirements
    ) == ("TASK_COMPLETE",)
    requirement = provider_request.selected_output_requirements[0].selected_output
    assert requirement.required is True
    assert set(requirement.json_schema["required"]) == {
        "artifact_kind",
        "source_prompt_id",
        "title",
        "objective",
        "requirements",
        "completion_tests",
    }
    assert facade.execute_calls == 1
    assert facade.close_calls == 1

    observation = next(iter(after.runner_observations.values()))
    assert observation.run_id == "run-millforge-taskmaster"
    assert observation.payload["marker"] == "TASK_COMPLETE"
    assert len(after.artifacts) == 1
    assert str(next(iter(after.artifacts.values())).schema_id) == (
        "kernel_ping.task_artifact"
    )
    assert len(after.activation_routes) == 1
    assert str(after.activation_routes[0].action_id) == (
        "kernel_ping.route_taskmaster_success"
    )
    transition = next(
        item
        for item in after.transitions
        if item.input_id == observation.created_by_input_id
    )
    assert transition.accepted is True
    assert transition.input_kind == "workflow.runner_result_observed"

    runtime = _reopen_runtime(runtime)
    after_second_reload = _load(runtime)
    reloaded_plan = after_second_reload.admitted_plans[
        AuthorityFingerprint(millforge_fingerprint)
    ].selected_plan
    assert after_second_reload.runner_observations == after.runner_observations
    assert after_second_reload.artifacts == after.artifacts
    assert after_second_reload.activation_routes == after.activation_routes
    assert after_second_reload.transitions == after.transitions
    assert reloaded_plan.runner_bindings[0].component_pin == pin
    assert (
        reloaded_plan.runner_bindings[0].terminal_result_mappings == expected_mappings
    )


def test_restarted_millforge_claim_survives_missing_optional_service(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, _plan, millforge_fingerprint, _codex_fingerprint = (
        _active_millforge_state_with_codex_default()
    )
    runtime = _reopen_runtime(_runtime(tmp_path, state))
    before = _load(runtime)
    before_bytes = _durable_file_bytes(runtime)
    codex_only_config, codex = _tracking_codex_config(tmp_path)

    result = run_bounded_execution_unit(
        runtime,
        activation_id="activation-taskmaster",
        adapter_kind=None,
        local_config=codex_only_config,
    )
    after = _load(runtime)

    assert result.code == "adapter_failure"
    assert result.activation_id == "activation-taskmaster"
    assert result.run_id == "run-millforge-taskmaster"
    assert after == before
    assert _durable_file_bytes(runtime) == before_bytes
    assert after.activations["activation-taskmaster"].claimed_by_run_id == (
        "run-millforge-taskmaster"
    )
    assert (
        str(
            after.runs[
                "run-millforge-taskmaster"
            ].run_ref.plan_ref.authority_fingerprint
        )
        == millforge_fingerprint
    )
    assert codex.calls == 0

    runtime = _reopen_runtime(runtime)
    assert _load(runtime) == before


def test_restarted_millforge_descriptor_drift_refuses_before_provider_execute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, _plan, _millforge_fingerprint, _codex_fingerprint = (
        _active_millforge_state_with_codex_default()
    )
    runtime = _reopen_runtime(_runtime(tmp_path, state))
    before = _load(runtime)
    before_bytes = _durable_file_bytes(runtime)
    facade = _RecordingMillforgeFacade(
        artifact_payload=task_artifact_payload(),
        drift_descriptor=True,
    )
    local_config, adapter = _millforge_live_config(
        monkeypatch,
        tmp_path,
        facade,
    )

    result = run_bounded_execution_unit(
        runtime,
        activation_id="activation-taskmaster",
        adapter_kind=None,
        local_config=local_config,
    )

    assert result.code == "adapter_failure"
    assert result.adapter_error_kind == "selected_authority_refused"
    assert adapter.request is not None
    assert facade.factory_calls == 1
    assert facade.evidence_calls == 0
    assert facade.execute_calls == 0
    assert facade.close_calls == 1
    assert _load(runtime) == before
    assert _durable_file_bytes(runtime) == before_bytes


def test_restarted_active_millforge_claim_never_falls_back_to_current_codex_default(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, _plan, millforge_fingerprint, codex_fingerprint = (
        _active_millforge_state_with_codex_default()
    )
    runtime = _reopen_runtime(_runtime(tmp_path, state))
    before = _load(runtime)
    codex_only_config, codex = _tracking_codex_config(tmp_path)

    result = run_bounded_execution_unit(
        runtime,
        activation_id="activation-taskmaster",
        adapter_kind=None,
        local_config=codex_only_config,
    )

    assert result.code == "adapter_failure"
    assert result.run_id == "run-millforge-taskmaster"
    assert codex.calls == 0
    assert _load(runtime) == before
    run = before.runs["run-millforge-taskmaster"]
    assert str(run.run_ref.plan_ref.authority_fingerprint) == millforge_fingerprint
    assert before.default_plan_ref is not None
    assert str(before.default_plan_ref.authority_fingerprint) == codex_fingerprint
    assert run.run_ref.plan_ref != before.default_plan_ref


def test_bounded_execution_projects_selected_millforge_authority_without_mutation_on_refusal(  # noqa: E501
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.adapters.runner_contract import (
        AdapterErrorResult,
        AdapterLocalConfig,
        DispatchEcho,
        RedactionPolicy,
    StartRefusedBeforeExternalWork,
    Unsupported,
    start_refusal_diagnostic_digest,
    )
    from millrace.contracts.compiled_plan import (
        CapabilityDeclaration,
        RunnerComponentPin,
        RunnerTerminalResultMapping,
    )
    from millrace.contracts.ids import CapabilityId, OutcomeId, StageKindId

    class RefusingMillforgeAdapter:
        adapter_kind = "millforge"

        def __init__(self) -> None:
            self.request: object | None = None

        def start_session(self, request: object) -> object:
            self.request = request
            error = AdapterErrorResult.from_unredacted(
                adapter_id="millforge-offline",
                error_kind="selected_authority_refused",
                dispatch_echo=DispatchEcho.from_dispatch_envelope(
                    request.dispatch_envelope,
                    correlation_id=request.correlation_id,
                ),
                redaction_policy=request.redaction_policy,
                diagnostics={"reason": "offline refusal"},
            )
            return StartRefusedBeforeExternalWork(
                error.dispatch_echo,
                error,
                start_refusal_diagnostic_digest(error),
            )

        def reconcile_session(self, request: object) -> object:
            invocation = request.invocation_request
            return Unsupported(
                DispatchEcho.from_dispatch_envelope(
                    invocation.dispatch_envelope,
                    correlation_id=invocation.correlation_id,
                )
            )

    state, _fingerprint = _ready_state()
    plan = next(iter(state.admitted_plans.values())).selected_plan
    binding = plan.runner_bindings[0]
    pin = RunnerComponentPin(
        component_kind="runner",
        component_id="millforge-base",
        component_version="1",
        provider_distribution="millforge",
        provider_version="0.1.0",
        descriptor_media_type="application/json",
        descriptor_sha256="a" * 64,
        required_capability_ids=(CapabilityId("capability.runner.invoke"),),
        legal_terminal_result_ids=("COMPLETE",),
    )
    millforge_binding = replace(
        binding,
        adapter_kind="millforge",
        required_capability_ids=(CapabilityId("capability.runner.invoke"),),
        component_pin=pin,
        terminal_result_mappings=(
            RunnerTerminalResultMapping(
                stage_kind_id=StageKindId("kernel_ping.taskmaster"),
                runner_result_id="COMPLETE",
                outcome_id=OutcomeId("kernel_ping.taskmaster.task_complete"),
            ),
        ),
    )
    selected_state, _selected_fingerprint = _state_with_selected_plan(
        state,
        replace(
            plan,
            runner_bindings=(millforge_binding,),
            capabilities=(
                CapabilityDeclaration(
                    id=CapabilityId("capability.runner.invoke"),
                    capability_kind="runner.invoke",
                    support_status="supported",
                    grant_status="granted",
                ),
            ),
        ),
    )
    runtime = _runtime(tmp_path, selected_state)
    adapter = RefusingMillforgeAdapter()

    result = run_bounded_execution_unit(
        runtime,
        local_config=AdapterLocalConfig(adapters={"millforge": adapter}),
    )
    after = _load(runtime)

    assert result.code == "adapter_failure"
    assert result.adapter_error_kind == "selected_authority_refused"
    assert adapter.request is not None
    assert adapter.request.adapter_id == "millforge"
    assert adapter.request.redaction_policy == RedactionPolicy(policy_id="cli-default")
    assert adapter.request.selected_component_pin == pin
    assert adapter.request.selected_terminal_result_mappings == (
        millforge_binding.terminal_result_mappings[0],
    )
    assert adapter.request.selected_artifact_schemas
    assert after.runner_observations == {}
    assert after.artifacts == {}

    partially_bound_binding = replace(
        millforge_binding,
        terminal_result_mappings=(),
    )
    partially_bound_state, _partially_bound_fingerprint = _state_with_selected_plan(
        state,
        replace(
            plan,
            runner_bindings=(partially_bound_binding,),
            capabilities=(
                CapabilityDeclaration(
                    id=CapabilityId("capability.runner.invoke"),
                    capability_kind="runner.invoke",
                    support_status="supported",
                    grant_status="granted",
                ),
            ),
        ),
    )
    partially_bound_runtime = _runtime(
        tmp_path / "partially-bound", partially_bound_state
    )
    partially_bound_adapter = RefusingMillforgeAdapter()

    partially_bound_result = run_bounded_execution_unit(
        partially_bound_runtime,
        local_config=AdapterLocalConfig(
            adapters={"millforge": partially_bound_adapter},
        ),
    )
    partially_bound_after = _load(partially_bound_runtime)

    assert partially_bound_result.code == "adapter_failure"
    assert partially_bound_result.adapter_error_kind == "selected_authority_refused"
    assert partially_bound_adapter.request is not None
    assert partially_bound_adapter.request.selected_component_pin == pin
    assert partially_bound_adapter.request.selected_terminal_result_mappings == ()
    assert partially_bound_adapter.request.selected_artifact_schemas
    assert partially_bound_after.runner_observations == {}
    assert partially_bound_after.artifacts == {}


def test_bounded_execution_omits_component_authority_for_default_codex_request(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.adapters.codex import CodexAdapter, CodexAdapterConfig
    from millrace.adapters.runner_contract import (
        AdapterErrorResult,
        AdapterInvocationRequest,
        AdapterLocalConfig,
        DispatchEcho,
        RedactionPolicy,
    )

    class CapturingCodexAdapter(CodexAdapter):
        def __init__(self) -> None:
            super().__init__(
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
                )
            )
            self.request: AdapterInvocationRequest | None = None

        def _transport_request(
            self,
            request: AdapterInvocationRequest,
        ) -> object:
            self.request = request
            return AdapterErrorResult.from_unredacted(
                adapter_id="codex-default",
                error_kind="selected_authority_refused",
                dispatch_echo=DispatchEcho.from_dispatch_envelope(
                    request.dispatch_envelope,
                    correlation_id=request.correlation_id,
                ),
                redaction_policy=request.redaction_policy,
            )

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    adapter = CapturingCodexAdapter()

    result = run_bounded_execution_unit(
        runtime,
        local_config=AdapterLocalConfig(adapters={"codex": adapter}),
    )

    assert result.code == "adapter_failure"
    assert adapter.request is not None
    assert any(
        option["artifact_schema_id"] is not None
        for option in adapter.request.dispatch_envelope.terminal_options
    )
    assert adapter.request.selected_component_pin is None
    assert adapter.request.selected_terminal_result_mappings == ()
    assert adapter.request.selected_artifact_schemas == ()


def _observed_counts(state: RuntimeState) -> dict[str, int]:
    return {
        "runs": len(state.runs),
        "observations": len(state.runner_observations),
        "artifacts": len(state.artifacts),
        "routes": len(state.activation_routes),
        "closed": len(state.closed_work_items),
        "receipts": len(state.receipts),
    }


def _invoke(argv: list[str]) -> tuple[int, str, str]:
    from millrace.adapters.cli.main import main

    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(argv)
    return exit_code, stdout.getvalue(), stderr.getvalue()


def _json(raw: str) -> dict[str, Any]:
    assert raw.endswith("\n")
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    return parsed


def test_bounded_unit_no_ready_work_is_successful_noop(tmp_path: Path) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    runtime = _runtime(tmp_path)
    before = _load(runtime)

    result = run_bounded_execution_unit(runtime, local_config=_codex_success_config())

    assert result.code == "no_ready_work"
    assert result.accepted is False
    assert _load(runtime) == before


def test_bounded_unit_uses_operator_dispatch_ready_candidate_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import run as run_module
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.operator.dispatch import ReadyDispatchProjection

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    called = False

    def fake_projection(state_arg: RuntimeState) -> object:
        nonlocal called
        called = True
        assert state_arg == state
        return ReadyDispatchProjection(candidates=(), diagnostics=())

    monkeypatch.setattr(run_module, "list_ready_dispatch_candidates", fake_projection)

    result = run_bounded_execution_unit(runtime, local_config=_codex_success_config())

    assert called is True
    assert result.code == "no_ready_work"
    assert _load(runtime) == state


def test_bounded_unit_corrupt_ready_diagnostics_are_not_no_ready_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import run as run_module
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.operator.dispatch import (
        ReadyDispatchDiagnostic,
        ReadyDispatchProjection,
    )

    state, _fingerprint = _ready_state()
    policy = ReadyDispatchDiagnostic(
        activation_id="activation-taskmaster",
        work_item_id="work-prompt",
        reason_code="workspace_paused",
        severity="policy_refusal",
        plan_fingerprint=state.default_plan_ref.authority_fingerprint
        if state.default_plan_ref is not None
        else None,
        message="Activation is paused.",
    )
    corrupt = ReadyDispatchDiagnostic(
        activation_id="activation-taskmaster",
        work_item_id="work-prompt",
        reason_code="missing_admitted_plan",
        severity="corrupt_authority",
        plan_fingerprint="missing-fingerprint",
        message="Activation plan authority is not admitted.",
    )

    refused_runtime = _runtime(tmp_path / "refused", state)
    corrupt_runtime = _runtime(tmp_path / "corrupt", state)

    monkeypatch.setattr(
        run_module,
        "list_ready_dispatch_candidates",
        lambda _state: ReadyDispatchProjection(
            candidates=(),
            diagnostics=(cast(ReadyDispatchDiagnostic, policy),),
        ),
    )
    refused = run_bounded_execution_unit(
        refused_runtime,
        local_config=_codex_success_config(),
    )
    monkeypatch.setattr(
        run_module,
        "list_ready_dispatch_candidates",
        lambda _state: ReadyDispatchProjection(
            candidates=(),
            diagnostics=(cast(ReadyDispatchDiagnostic, corrupt),),
        ),
    )
    corrupt_result = run_bounded_execution_unit(
        corrupt_runtime,
        local_config=_codex_success_config(),
    )

    assert refused.code == "ready_state_refused"
    assert refused.diagnostics[0]["severity"] == "policy_refusal"
    assert _load(refused_runtime) == state
    assert corrupt_result.code == "ready_state_corrupt"
    assert corrupt_result.diagnostics[0]["severity"] == "corrupt_authority"
    assert _load(corrupt_runtime) == state


def test_bounded_unit_maps_real_claim_refusal_after_stale_ready_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import run as run_module
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.operator.dispatch import (
        ReadyDispatchCandidate,
        ReadyDispatchProjection,
        list_ready_dispatch_candidates,
    )

    state, _fingerprint = _ready_state()
    candidate = list_ready_dispatch_candidates(state).candidates[0]
    already_claimed = _claim_only_state()
    monkeypatch.setattr(
        run_module,
        "list_ready_dispatch_candidates",
        lambda _state: ReadyDispatchProjection(
            candidates=(cast(ReadyDispatchCandidate, candidate),),
            diagnostics=(),
        ),
    )
    runtime = _runtime(tmp_path, already_claimed)

    result = run_bounded_execution_unit(runtime, local_config=_codex_success_config())

    assert result.code == "no_ready_work"
    assert _load(runtime) == already_claimed


def test_bounded_unit_claims_one_ready_activation_invokes_and_observes_success(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)

    result = run_bounded_execution_unit(runtime, local_config=_codex_success_config())
    after = _load(runtime)

    assert result.code == "observation_accepted"
    assert result.activation_id == "activation-taskmaster"
    assert result.run_id in after.runs
    assert result.claim_id == after.runs[cast(str, result.run_id)].run_ref.claim_id
    assert len(after.runs) == 1
    assert after.activations["activation-taskmaster"].claimed_by_run_id == result.run_id
    assert len(after.runner_observations) == 1
    assert len(after.artifacts) == 1
    assert len(after.activation_routes) == 1
    assert len(after.work_items) == 2

    runtime = _reopen_runtime(runtime)
    explicit_retry = run_bounded_execution_unit(
        runtime,
        activation_id="activation-taskmaster",
        local_config=_codex_success_config(),
    )
    after_explicit_retry = _load(runtime)

    assert explicit_retry.code == "no_ready_work"
    assert explicit_retry.activation_id == "activation-taskmaster"
    assert explicit_retry.run_id == result.run_id
    assert explicit_retry.diagnostics[0]["reason"] == "run_observed"
    assert set(after_explicit_retry.runs) == set(after.runs)
    assert len(after_explicit_retry.runner_observations) == 1


def test_codex_config_and_bounded_execution_are_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    monkeypatch.setitem(sys.modules, "millforge", None)
    config_path = tmp_path / "codex.json"
    config_path.write_text(
        json.dumps(
            {
                "codex": {
                    "adapter_id": "codex-default",
                    "wrapper_mode": "offline_fake",
                    "wrapper_argv": [
                        sys.executable,
                        "-c",
                        _codex_success_wrapper("TASK_COMPLETE"),
                    ],
                    "cwd": str(tmp_path),
                    "env_allowlist": {},
                    "timeout_seconds": 5,
                        "max_input_bundle_bytes": 16384,
                    "max_stdout_bytes": 8192,
                    "max_stderr_diagnostic_bytes": 512,
                    "redaction_policy": {
                        "policy_id": "redact-default",
                        "secret_tokens": [],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)

    result = run_bounded_execution_unit(runtime, local_config_path=config_path)
    after = _load(runtime)

    assert result.code == "observation_accepted"
    assert result.run_id in after.runs
    assert len(after.runner_observations) == 1


@pytest.mark.parametrize(
    ("selected_timeout", "local_timeout", "effective_timeout"),
    (
        (3600, 120, 120),
        (1800, 7200, 1800),
    ),
)
def test_bounded_unit_preserves_selected_timeout_and_applies_local_maximum(
    tmp_path: Path,
    selected_timeout: int,
    local_timeout: float,
    effective_timeout: float,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, _fingerprint = _ready_state()
    plan = next(iter(state.admitted_plans.values())).selected_plan
    if selected_timeout != 3600:
        binding = plan.runner_bindings[0]
        plan = replace(
            plan,
            runner_bindings=(
                replace(
                    binding,
                    invocation_timeout_seconds=selected_timeout,
                ),
            ),
        )
        state, _fingerprint = _state_with_selected_plan(state, plan)
    runtime = _runtime(tmp_path, state)
    bundle_path = tmp_path / "invocation-bundle.json"

    result = run_bounded_execution_unit(
        runtime,
        local_config=_codex_success_config(
            capture_bundle_path=bundle_path,
            timeout_seconds=local_timeout,
        ),
    )

    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert result.code == "observation_accepted"
    assert bundle["request_timeout_seconds"] == selected_timeout
    assert bundle["timeout_seconds"] == effective_timeout


def test_bounded_unit_uses_each_dispatched_stage_runner_timeout(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, _fingerprint = _ready_state_with_stage_timeouts()
    runtime = _runtime(tmp_path, state)
    bundle_path = tmp_path / "invocation-bundle.json"
    config = _codex_success_config(
        marker="AUTO",
        capture_bundle_path=bundle_path,
        timeout_seconds=7200,
    )

    taskmaster = run_bounded_execution_unit(runtime, local_config=config)
    taskmaster_bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    worker = run_bounded_execution_unit(runtime, local_config=config)
    worker_bundle = json.loads(bundle_path.read_text(encoding="utf-8"))

    assert taskmaster.code == "observation_accepted"
    assert taskmaster_bundle["dispatch_envelope"]["stage_kind_id"] == (
        "kernel_ping.taskmaster"
    )
    assert taskmaster_bundle["request_timeout_seconds"] == 3600
    assert taskmaster_bundle["timeout_seconds"] == 3600
    assert worker.code == "observation_accepted", worker
    assert worker_bundle["dispatch_envelope"]["stage_kind_id"] == ("kernel_ping.worker")
    assert worker_bundle["request_timeout_seconds"] == 1800
    assert worker_bundle["timeout_seconds"] == 1800


def test_bounded_unit_refuses_duplicate_runner_binding_before_claim(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, _fingerprint = _ready_state()
    plan = next(iter(state.admitted_plans.values())).selected_plan
    binding = plan.runner_bindings[0]
    corrupted, _fingerprint = _state_with_selected_plan(
        state,
        replace(plan, runner_bindings=(binding, binding)),
    )
    runtime = _runtime(tmp_path, corrupted)

    result = run_bounded_execution_unit(
        runtime,
        local_config=_codex_success_config(),
    )

    assert result.code == "ready_state_corrupt"
    assert _load(runtime) == corrupted


def test_bounded_unit_adapter_error_does_not_mutate_runtime_state(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)

    result = run_bounded_execution_unit(runtime, local_config=_codex_error_config())
    after = _load(runtime)

    assert result.code == "adapter_failure"
    assert result.activation_id == "activation-taskmaster"
    assert result.run_id in after.runs
    assert result.claim_id is not None
    assert result.fencing_token is not None
    assert _observed_counts(after) == {
        "runs": 1,
        "observations": 0,
        "artifacts": 0,
        "routes": 0,
        "closed": 0,
        "receipts": _observed_counts(state)["receipts"] + 4,
    }
    session = after.runner_sessions[
        after.runs[cast(str, result.run_id)].current_session_id
    ]
    assert session.state == "failed"
    assert session.session_id in after.runner_session_completions


def test_bounded_unit_timeout_failure_creates_no_observation_or_action(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)

    result = run_bounded_execution_unit(
        runtime,
        local_config=_codex_timeout_config(),
    )
    after = _load(runtime)

    assert result.code == "adapter_failure"
    assert result.adapter_error_kind == "cancelled"
    session_id = after.runs[cast(str, result.run_id)].current_session_id
    assert session_id is not None
    assert after.runner_sessions[session_id].state == "interrupted"
    assert session_id in after.runner_session_completions
    cancellation = next(iter(after.runner_session_cancellation_requests.values()))
    assert (cancellation.reason, cancellation.source_kind) == (
        "runner_timeout",
        "runtime",
    )

    assert result.activation_id is not None
    retry = run_bounded_execution_unit(
        runtime,
        activation_id=result.activation_id,
        local_config=_codex_success_config(),
    )
    after_retry = _load(runtime)

    assert retry.code == "observation_accepted"
    assert after_retry.runner_observations
    assert after_retry.artifacts
    assert after_retry.activation_routes


def test_bounded_unit_adapter_conversion_refusal_creates_no_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import session_coordinator
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.adapters.runner_contract import AdapterEvidenceConversionError

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)

    def refuse_conversion(*_args: object, **_kwargs: object) -> None:
        raise AdapterEvidenceConversionError("refused for characterization")

    monkeypatch.setattr(
        session_coordinator,
        "runner_evidence_from_adapter_outcome",
        refuse_conversion,
    )

    result = run_bounded_execution_unit(
        runtime,
        local_config=_codex_success_config(),
    )
    after = _load(runtime)

    assert result.code == "adapter_conversion_refused"
    assert result.run_id in after.runs
    assert result.claim_id is not None
    assert result.fencing_token is not None
    assert _observed_counts(after) == {
        "runs": 1,
        "observations": 0,
        "artifacts": 0,
        "routes": 0,
        "closed": 0,
        "receipts": _observed_counts(state)["receipts"] + 5,
    }


def test_bounded_unit_adapter_failure_after_claim_allows_explicit_active_retry(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    failed = run_bounded_execution_unit(runtime, local_config=_codex_error_config())
    after_failure = _load(runtime)

    runtime = _reopen_runtime(runtime)
    assert _load(runtime) == after_failure
    automatic = run_bounded_execution_unit(
        runtime,
        local_config=_codex_success_config(),
    )
    runtime = _reopen_runtime(runtime)
    assert _load(runtime) == after_failure
    explicit = run_bounded_execution_unit(
        runtime,
        activation_id=cast(str, failed.activation_id),
        local_config=_codex_success_config(),
    )
    after_retry = _load(runtime)

    assert automatic.code == "no_ready_work"
    assert explicit.code == "observation_accepted"
    assert explicit.run_id == failed.run_id
    assert explicit.claim_id == failed.claim_id
    assert explicit.fencing_token == failed.fencing_token
    assert after_retry.runs.keys() == after_failure.runs.keys()
    assert len(after_retry.runner_observations) == 1

    runtime = _reopen_runtime(runtime)
    after_final_reload = _load(runtime)
    assert after_final_reload.runs.keys() == after_failure.runs.keys()
    assert len(after_final_reload.runner_observations) == 1


def test_bounded_unit_postclaim_adapter_failure_preserves_only_claim(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)

    result = run_bounded_execution_unit(runtime, local_config=_codex_error_config())
    after = _load(runtime)
    automatic = run_bounded_execution_unit(
        runtime,
        local_config=_codex_success_config(),
    )

    assert result.code == "adapter_failure"
    assert result.run_id in after.runs
    assert result.claim_id is not None
    assert result.fencing_token is not None
    assert automatic.code == "no_ready_work"
    assert after.runner_observations == {}
    assert after.artifacts == {}
    assert after.activation_routes == ()


def test_bounded_unit_missing_local_adapter_config_refuses_before_new_claim(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.adapters.runner_contract import AdapterLocalConfig

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)

    result = run_bounded_execution_unit(runtime, local_config=AdapterLocalConfig())

    assert result.code == "adapter_failure"
    assert result.run_id is None
    assert _load(runtime) == state


def test_bounded_unit_local_config_and_adapter_kind_refuse_before_new_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import run as run_module
    from millrace.adapters.cli.run import (
        load_adapter_local_config,
        run_bounded_execution_unit,
    )
    from millrace.adapters.codex import CodexAdapter, CodexAdapterConfig
    from millrace.adapters.runner_contract import AdapterLocalConfig, RedactionPolicy

    bad_config = tmp_path / "bad.json"
    bad_config.write_text("{", encoding="utf-8")
    state, _fingerprint = _ready_state()
    codex_state, _codex_fingerprint = _state_with_runner_kind("codex")
    unsupported_state, _unsupported_fingerprint = _state_with_runner_kind(
        "unsupported_adapter",
    )

    with pytest.raises(Exception) as error:
        load_adapter_local_config(bad_config)
    assert getattr(error.value, "exit_code") == 2

    mismatch_runtime = _runtime(tmp_path / "mismatch", state)
    mismatch_before = _durable_file_bytes(mismatch_runtime)
    with monkeypatch.context() as resolver_guard:
        resolver_guard.setattr(
            run_module,
            "resolve_adapter",
            lambda *_args, **_kwargs: pytest.fail(
                "requested/selected mismatch reached local adapter resolution"
            ),
        )
        mismatch = run_bounded_execution_unit(
            mismatch_runtime,
            adapter_kind="fake_local",
            local_config=AdapterLocalConfig(),
        )

    missing_codex_runtime = _runtime(tmp_path / "missing-codex", codex_state)
    missing_codex = run_bounded_execution_unit(
        missing_codex_runtime,
        local_config=AdapterLocalConfig(),
    )
    unsupported_runtime = _runtime(tmp_path / "unsupported", unsupported_state)
    unsupported_before = _durable_file_bytes(unsupported_runtime)
    with monkeypatch.context() as resolver_guard:
        resolver_guard.setattr(
            run_module,
            "resolve_adapter",
            lambda *_args, **_kwargs: pytest.fail(
                "unavailable selected kind reached local adapter resolution"
            ),
        )
        unsupported = run_bounded_execution_unit(
            unsupported_runtime,
            local_config=AdapterLocalConfig(),
        )
    marker_path = tmp_path / "codex-launched.txt"
    live_codex_runtime = _runtime(tmp_path / "live-codex", codex_state)
    live_codex = run_bounded_execution_unit(
        live_codex_runtime,
        local_config=AdapterLocalConfig(
            adapters={
                "codex": CodexAdapter(
                    CodexAdapterConfig(
                        adapter_id="codex-default",
                        wrapper_mode="local_argv",
                        wrapper_argv=(
                            sys.executable,
                            "-c",
                            (
                                "from pathlib import Path; "
                                f"Path({str(marker_path)!r}).write_text('x')"
                            ),
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
            }
        ),
    )

    assert mismatch.code == "adapter_kind_refused"
    assert mismatch.activation_id is None
    assert mismatch.run_id is None
    assert mismatch.claim_id is None
    assert _load(mismatch_runtime) == state
    assert _durable_file_bytes(mismatch_runtime) == mismatch_before
    assert missing_codex.code == "adapter_failure"
    assert _load(missing_codex_runtime) == codex_state
    assert unsupported.code == "adapter_kind_refused"
    assert unsupported.activation_id is None
    assert unsupported.run_id is None
    assert unsupported.claim_id is None
    assert _load(unsupported_runtime) == unsupported_state
    assert _durable_file_bytes(unsupported_runtime) == unsupported_before
    assert live_codex.code == "adapter_failure"
    assert live_codex.adapter_error_kind == "missing_opt_in_config"
    assert _load(live_codex_runtime) == codex_state
    assert not marker_path.exists()


@pytest.mark.parametrize(
    ("kwargs", "field_name"),
    (
        ({"activation_id": " "}, "activation_id"),
        ({"adapter_kind": " "}, "adapter_kind"),
        ({"actor_id": " "}, "actor_id"),
    ),
)
def test_bounded_unit_malformed_local_options_are_cli_usage_before_mutation(
    tmp_path: Path,
    kwargs: dict[str, str],
    field_name: str,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)

    with pytest.raises(Exception) as error:
        run_bounded_execution_unit(
            runtime,
            local_config=_codex_success_config(),
            **kwargs,
        )

    assert getattr(error.value, "exit_code") == 2
    assert getattr(error.value, "code") == "invalid_run_option"
    assert getattr(error.value, "details") == {"field": field_name}
    assert _load(runtime) == state


def test_bounded_unit_local_config_path_invalid_json_refuses_before_new_claim(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    bad_config = tmp_path / "bad-config.json"
    bad_config.write_text("{", encoding="utf-8")

    with pytest.raises(Exception) as error:
        run_bounded_execution_unit(runtime, local_config_path=bad_config)

    assert getattr(error.value, "exit_code") == 2
    assert getattr(error.value, "code") == "invalid_adapter_config"
    assert _load(runtime) == state


def test_bounded_unit_dispatch_echo_mismatch_refuses_before_kernel_observation(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)

    result = run_bounded_execution_unit(
        runtime,
        local_config=_codex_mismatch_config(),
    )
    after = _load(runtime)

    assert result.code == "adapter_failure"
    assert result.adapter_error_kind == "result_parse_failed"
    assert len(after.runs) == 1
    assert after.runner_observations == {}
    assert after.artifacts == {}
    assert after.activation_routes == ()


def test_bounded_unit_uses_prompt_0001_materializer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import run as run_module
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    captured: dict[str, object] = {}
    bundle_path = tmp_path / "bundle.json"

    def fake_materializer(
        *,
        selected_plan: object,
        dispatch_envelope: object,
    ) -> object:
        captured["selected_plan"] = selected_plan
        captured["dispatch_envelope"] = dispatch_envelope
        return {"asset.material": {"body": "from materializer"}}

    monkeypatch.setattr(run_module, "build_selected_asset_material", fake_materializer)

    result = run_bounded_execution_unit(
        runtime,
        local_config=_codex_success_config(capture_bundle_path=bundle_path),
    )

    captured_bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert result.code == "observation_accepted"
    assert captured["selected_plan"] == state.admitted_plans[fingerprint].selected_plan
    assert captured["dispatch_envelope"].activation_id == "activation-taskmaster"
    assert captured_bundle["selected_asset_material"] == {
        "asset.material": {"body": "from materializer"}
    }


def test_selected_asset_material_refusal_after_claim_preserves_only_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import run as run_module
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.operator.prompt_material import SelectedAssetMaterializationError

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)

    def refuse_materializer(**_kwargs: object) -> object:
        raise SelectedAssetMaterializationError("selected material refused")

    monkeypatch.setattr(
        run_module,
        "build_selected_asset_material",
        refuse_materializer,
    )

    result = run_bounded_execution_unit(runtime, local_config=_codex_success_config())
    after = _load(runtime)

    assert result.code == "asset_material_refused"
    assert len(after.runs) == 1
    assert after.runner_observations == {}
    assert after.artifacts == {}
    assert after.activation_routes == ()


def test_selected_asset_material_real_refusal_after_claim_survives_reload(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, fingerprint = _ready_state()
    admitted = state.admitted_plans[fingerprint]
    selected_plan = admitted.selected_plan
    corrupted_asset = next(
        asset
        for asset in selected_plan.assets
        if str(asset.id) == "kernel_ping.taskmaster_prompt"
    )
    corrupted_plan = replace(
        selected_plan,
        assets=tuple(
            AssetDeclaration(
                id=asset.id,
                asset_kind=asset.asset_kind,
                body=" ",
                presentation=asset.presentation,
            )
            if asset.id == corrupted_asset.id
            else asset
            for asset in selected_plan.assets
        ),
    )
    corrupted_state, _corrupted_fingerprint = _state_with_selected_plan(
        state,
        corrupted_plan,
    )
    runtime = _runtime(tmp_path, corrupted_state)

    result = run_bounded_execution_unit(runtime, local_config=_codex_success_config())
    after = _load(runtime)

    assert result.code == "asset_material_refused"
    assert result.run_id in after.runs
    assert result.fencing_token is not None
    assert after.runner_observations == {}
    assert after.artifacts == {}
    assert after.activation_routes == ()

    runtime = _reopen_runtime(runtime)
    after_reload = _load(runtime)
    assert after_reload.runs.keys() == after.runs.keys()
    assert after_reload.runner_observations == {}
    assert after_reload.artifacts == {}
    assert after_reload.activation_routes == ()


def test_bounded_unit_kernel_observation_refusal_after_claim_preserves_only_claim(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)

    result = run_bounded_execution_unit(
        runtime,
        local_config=_codex_success_config(marker="UNDECLARED"),
    )
    after = _load(runtime)

    assert result.code == "observation_refused"
    assert result.observation_refusal_reason == "undeclared_terminal_outcome"
    assert len(after.runs) == 1
    assert after.runner_observations == {}
    assert after.artifacts == {}
    assert after.activation_routes == ()


def test_explicit_active_retry_requires_coherent_run_activation(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)

    result = run_bounded_execution_unit(
        runtime,
        activation_id="missing-activation",
        local_config=_codex_success_config(),
    )

    assert result.code in {"ready_state_corrupt", "adapter_failure"}
    assert _load(runtime) == state


@pytest.mark.parametrize(
    "corruption",
    (
        "missing_run",
        "run_activation_mismatch",
        "missing_admitted_plan",
        "missing_runner_binding",
        "duplicate_runner_binding",
        "wrong_runner_binding",
        "missing_selected_asset",
    ),
)
def test_explicit_active_retry_refuses_corrupt_loaded_active_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.adapters.runner_contract import AdapterLocalConfig

    runtime, _clean_state = _claim_only_runtime(tmp_path)
    state = _claim_only_state()
    run = state.runs["run-taskmaster"]
    admitted = next(iter(state.admitted_plans.values()))
    selected_plan = admitted.selected_plan

    if corruption == "missing_run":
        corrupted = replace(state, runs={})
    elif corruption == "run_activation_mismatch":
        corrupted = replace(
            state,
            runs={
                run.run_ref.run_id: replace(
                    run,
                    activation_id="other-activation",
                )
            },
        )
    elif corruption == "missing_admitted_plan":
        corrupted = replace(state, admitted_plans={})
    elif corruption == "missing_runner_binding":
        corrupted = replace(
            state,
            admitted_plans={
                run.run_ref.plan_ref.authority_fingerprint: replace(
                    admitted,
                    selected_plan=replace(selected_plan, runner_bindings=()),
                )
            },
        )
    elif corruption == "duplicate_runner_binding":
        binding = selected_plan.runner_bindings[0]
        corrupted = replace(
            state,
            admitted_plans={
                run.run_ref.plan_ref.authority_fingerprint: replace(
                    admitted,
                    selected_plan=replace(
                        selected_plan,
                        runner_bindings=(binding, binding),
                    ),
                )
            },
        )
    elif corruption == "wrong_runner_binding":
        corrupted = replace(
            state,
            runs={
                run.run_ref.run_id: replace(
                    run,
                    runner_binding_id=RunnerBindingId("kernel_ping.wrong_runner"),
                )
            },
        )
    elif corruption == "missing_selected_asset":
        corrupted = replace(
            state,
            admitted_plans={
                run.run_ref.plan_ref.authority_fingerprint: replace(
                    admitted,
                    selected_plan=replace(selected_plan, assets=()),
                )
            },
        )
    else:  # pragma: no cover - parametrization guard
        raise AssertionError(corruption)
    monkeypatch.setattr(
        runtime.store,
        "load_runtime_state",
        lambda _cas_store: corrupted,
    )

    result = run_bounded_execution_unit(
        runtime,
        activation_id="activation-taskmaster",
        local_config=AdapterLocalConfig()
        if corruption == "missing_selected_asset"
        else _codex_success_config(),
    )

    assert result.code == (
        "adapter_failure"
        if corruption == "missing_selected_asset"
        else "ready_state_corrupt"
    )
    assert corrupted.runner_observations == {}
    assert corrupted.artifacts == {}
    assert corrupted.activation_routes == ()


def test_explicit_active_retry_refuses_coherent_wrong_claim_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.compiler.canonical import authority_fingerprint

    runtime, _clean_state = _claim_only_runtime(tmp_path)
    state = _claim_only_state()
    run = state.runs["run-taskmaster"]
    activation = state.activations["activation-taskmaster"]
    work_item = state.work_items["work-prompt"]
    admitted = next(iter(state.admitted_plans.values()))
    changed_plan = replace(
        admitted.selected_plan,
        workflow=replace(
            admitted.selected_plan.workflow,
            workflow_name="retargeted authority",
        ),
    )
    changed_fingerprint = AuthorityFingerprint(authority_fingerprint(changed_plan))
    changed_plan_ref = replace(
        run.run_ref.plan_ref,
        authority_fingerprint=changed_fingerprint,
    )
    corrupted = replace(
        state,
        admitted_plans={
            changed_fingerprint: replace(
                admitted,
                plan_ref=changed_plan_ref,
                selected_plan=changed_plan,
            )
        },
        default_plan_ref=changed_plan_ref,
        work_items={
            work_item.ref.work_item_id: replace(
                work_item,
                ref=replace(work_item.ref, plan_ref=changed_plan_ref),
            )
        },
        activations={
            activation.activation_id: replace(
                activation,
                plan_ref=changed_plan_ref,
            )
        },
        runs={
            run.run_ref.run_id: replace(
                run,
                run_ref=replace(run.run_ref, plan_ref=changed_plan_ref),
            )
        },
    )
    monkeypatch.setattr(
        runtime.store,
        "load_runtime_state",
        lambda _cas_store: corrupted,
    )

    result = run_bounded_execution_unit(
        runtime,
        activation_id="activation-taskmaster",
        local_config=_codex_success_config(),
    )

    assert result.code == "ready_state_corrupt"
    assert corrupted.runner_observations == {}
    assert corrupted.artifacts == {}
    assert corrupted.activation_routes == ()


def test_no_public_run_once_tick_observe_or_dispatch_invoke_commands(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    for argv in (
        ["run", "once"],
        ["tick"],
        ["observe"],
        ["dispatch", "invoke"],
    ):
        exit_code, stdout, stderr = _invoke(
            ["--json", "--workspace", str(workspace), *argv]
        )
        assert exit_code in {2, 3}
        assert stdout == ""
        assert _json(stderr)["code"] in {
            "argument_parse_error",
            "command_not_implemented",
        }
