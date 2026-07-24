"""Test-owned helpers for E2E-0001 actual-model smoke preflight."""

from __future__ import annotations

import io
import json
import re
import tomllib
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from hashlib import sha256
from math import isfinite
from pathlib import Path
from typing import Any, cast

from millrace.contracts.compiled_plan import RunnerComponentPin, SelectedCompiledPlan
from millrace.contracts.diagnostics import Diagnostic

CLASSIFICATIONS = frozenset(
    {
        "skipped_missing_opt_in",
        "skipped_missing_credentials_or_network",
        "blocked_no_selected_live_runner_binding",
        "blocked_unbounded_live_config",
        "package_import_failure",
        "package_selection_failure",
        "runner_adapter_failure",
        "model_provider_failure",
        "model_output_schema_refusal",
        "runtime_governance_refusal",
        "prompt_or_asset_quality_blocker",
        "operator_visible_blocker",
        "selected_operator_wait",
        "closed_successfully",
    }
)
PACKAGE_SOURCE_MODES = frozenset({"path", "archive", "installed"})
REQUIRED_ENV_BOUNDS = (
    "MILLRACE_E2E_MAX_TICKS_PER_WORKFLOW",
    "MILLRACE_E2E_MAX_ADAPTER_TIMEOUT_SECONDS",
    "MILLRACE_E2E_MAX_INPUT_BUNDLE_BYTES",
    "MILLRACE_E2E_MAX_STDOUT_BYTES",
    "MILLRACE_E2E_MAX_STDERR_DIAGNOSTIC_BYTES",
    "MILLRACE_E2E_MAX_WORKFLOW_SECONDS",
    "MILLRACE_E2E_MAX_TOTAL_SECONDS",
    "MILLRACE_E2E_MAX_RETRIES",
)
REQUIRED_CODEX_CONFIG_KEYS = (
    "adapter_id",
    "wrapper_mode",
    "wrapper_argv",
    "cwd",
    "env_allowlist",
    "timeout_seconds",
    "max_input_bundle_bytes",
    "max_stdout_bytes",
    "max_stderr_diagnostic_bytes",
    "redaction_policy",
    "live_test_opt_in_env_flags",
)
MILLFORGE_BASE_REQUIRED_MODEL_CAPABILITIES = (
    "system_messages",
    "tool_calls",
    "tool_result_messages",
)
MILLFORGE_BASE_REQUIRED_REQUEST_OPTIONS = ("parallel_tool_calls",)
EXTERNAL_PACKAGE_ROOT_UNCONFIGURED_REASON = (
    "MILLRACE_E2E_PACKAGE_ROOT is not set; coordinated workflow E2E "
    "is unconfigured."
)


@dataclass(frozen=True, slots=True)
class HarnessDecision:
    run_live: bool
    classification: str | None
    reason: str
    runner: str | None = None
    adapter_config_path: Path | None = None
    artifact_root: Path | None = None
    adapter_config_sha256: str | None = None
    adapter_config_bytes: bytes | None = field(default=None, repr=False)
    millforge_profile: MillforgeProfileEvidence | None = None


@dataclass(frozen=True, slots=True)
class MillforgeProfileEvidence:
    profile_id: str
    provider_id: str
    model_id: str
    adapter_timeout_seconds: float
    profile_timeout_seconds: float
    maximum_output_tokens: int
    reasoning_mode: str | None
    reasoning_effort: str | None
    secret_env_var: str


@dataclass(frozen=True, slots=True)
class OfficialPackageSetup:
    workspace: Path
    plan_fingerprint: str
    warning_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RunnerPreflight:
    live_capable: bool
    classification: str | None
    selected_adapter_kinds: tuple[str, ...]
    warning_codes: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class SmokeRow:
    row_id: str
    source: str
    workflow_id: str
    workflow_version: str
    entrypoint: str | None
    external_queue: str
    package_source_mode: str | None
    owns_live_row: bool
    package_id: str | None = None
    package_version: str | None = None


def external_package_root(env: Mapping[str, str]) -> Path | None:
    configured = env.get("MILLRACE_E2E_PACKAGE_ROOT")
    if configured is None:
        return None
    if not configured.strip():
        raise ValueError(
            "MILLRACE_E2E_PACKAGE_ROOT must be a nonblank absolute path."
        )
    package_root = Path(configured)
    if not package_root.is_absolute():
        raise ValueError("MILLRACE_E2E_PACKAGE_ROOT must be an absolute path.")
    if not package_root.is_dir():
        raise ValueError(
            "MILLRACE_E2E_PACKAGE_ROOT must be an existing directory."
        )
    if not (package_root / "manifest.json").is_file():
        raise ValueError(
            "MILLRACE_E2E_PACKAGE_ROOT must contain manifest.json."
        )
    return package_root.resolve()


def plan_live_smoke(
    env: Mapping[str, str],
    *,
    repo_root: Path,
    adapter_config_reader: Callable[[Path], Mapping[str, object]] | None = None,
    required_runner: str = "codex",
) -> HarnessDecision:
    """Preflight the opt-in live harness without creating runtime artifacts."""

    if required_runner not in {"codex", "millforge"}:
        raise ValueError("required_runner must be codex or millforge")

    if env.get("MILLRACE_E2E_ACTUAL_MODEL") != "1":
        return _blocked(
            "skipped_missing_opt_in",
            "Set MILLRACE_E2E_ACTUAL_MODEL=1 to run live actual-model smoke.",
        )

    runner = env.get("MILLRACE_E2E_RUNNER")
    if runner != required_runner:
        return _blocked(
            "blocked_no_selected_live_runner_binding",
            f"MILLRACE_E2E_RUNNER must be {required_runner} for live smoke.",
            runner=runner,
        )

    repo_root = repo_root.resolve()
    workspaces_root_text = env.get("MILLRACE_E2E_WORKSPACES_ROOT")
    if (
        workspaces_root_text is None
        or not workspaces_root_text.strip()
        or not Path(workspaces_root_text).is_absolute()
    ):
        return _blocked(
            "blocked_unbounded_live_config",
            "MILLRACE_E2E_WORKSPACES_ROOT must be a nonblank absolute path.",
            runner=runner,
        )
    artifact_root_text = env.get("MILLRACE_E2E_ARTIFACT_ROOT")
    if (
        artifact_root_text is None
        or not artifact_root_text.strip()
        or not Path(artifact_root_text).is_absolute()
    ):
        return _blocked(
            "blocked_unbounded_live_config",
            "MILLRACE_E2E_ARTIFACT_ROOT must be a nonblank absolute path.",
            runner=runner,
        )
    workspaces_root = Path(workspaces_root_text)
    artifact_root = Path(artifact_root_text)
    if _is_relative_to(artifact_root.resolve(), repo_root):
        return _blocked(
            "blocked_unbounded_live_config",
            "MILLRACE_E2E_ARTIFACT_ROOT must be outside the source repo.",
            runner=runner,
            artifact_root=artifact_root,
        )
    if not _is_relative_to(artifact_root.resolve(), workspaces_root.resolve()):
        return _blocked(
            "blocked_unbounded_live_config",
            "MILLRACE_E2E_ARTIFACT_ROOT must be inside the workspace E2E root.",
            runner=runner,
            artifact_root=artifact_root,
        )

    missing_bounds = [key for key in REQUIRED_ENV_BOUNDS if not env.get(key)]
    invalid_bounds = _invalid_bound_keys(env)
    if missing_bounds or invalid_bounds:
        return _blocked(
            "blocked_unbounded_live_config",
            "Missing or invalid bounded live env: "
            + ", ".join((*missing_bounds, *invalid_bounds)),
            runner=runner,
            artifact_root=artifact_root,
        )

    if env.get("MILLRACE_E2E_MAX_RETRIES") != "0":
        return _blocked(
            "blocked_unbounded_live_config",
            "MILLRACE_E2E_MAX_RETRIES must be 0 unless a packet names "
            "retryable errors.",
            runner=runner,
            artifact_root=artifact_root,
        )

    canary = env.get("MILLRACE_E2E_SECRET_CANARY")
    if runner == "codex" and (canary is None or not canary.strip()):
        return _blocked(
            "blocked_unbounded_live_config",
            "MILLRACE_E2E_SECRET_CANARY is required for redaction preflight.",
            runner=runner,
            artifact_root=artifact_root,
        )

    config_path_text = env.get("MILLRACE_E2E_ADAPTER_CONFIG")
    if config_path_text is None or not config_path_text.strip():
        return _blocked(
            "blocked_unbounded_live_config",
            "MILLRACE_E2E_ADAPTER_CONFIG is required before daemon execution.",
            runner=runner,
            artifact_root=artifact_root,
        )
    config_path = Path(config_path_text)
    if not config_path.is_absolute():
        return _blocked(
            "blocked_unbounded_live_config",
            "MILLRACE_E2E_ADAPTER_CONFIG must be an absolute path.",
            runner=runner,
            adapter_config_path=config_path,
            artifact_root=artifact_root,
        )

    config_bytes: bytes | None = None
    if runner == "codex":
        reader = adapter_config_reader or _read_json_object
        try:
            parsed = reader(config_path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return _blocked(
                "blocked_unbounded_live_config",
                "MILLRACE_E2E_ADAPTER_CONFIG must be readable bounded JSON.",
                runner=runner,
                adapter_config_path=config_path,
                artifact_root=artifact_root,
            )

        config_errors = _codex_config_errors(
            parsed,
            env,
            canary=cast(str, canary),
            repo_root=repo_root,
            artifact_root=artifact_root,
        )
        if config_errors:
            return _blocked(
                "blocked_unbounded_live_config",
                "Adapter config is unbounded: " + ", ".join(config_errors),
                runner=runner,
                adapter_config_path=config_path,
                artifact_root=artifact_root,
            )
        config_sha256 = None
        millforge_profile = None
    else:
        try:
            config_bytes, config_sha256, millforge_profile = _load_millforge_preflight(
                config_path,
                env,
                artifact_root=artifact_root,
            )
        except (OSError, TypeError, ValueError):
            return _blocked(
                "blocked_unbounded_live_config",
                "Millforge adapter config is invalid or exceeds E2E bounds.",
                runner=runner,
                adapter_config_path=config_path,
                artifact_root=artifact_root,
            )

    return HarnessDecision(
        run_live=True,
        classification=None,
        reason="bounded live preflight accepted",
        runner=runner,
        adapter_config_path=config_path,
        artifact_root=artifact_root,
        adapter_config_sha256=config_sha256,
        adapter_config_bytes=config_bytes,
        millforge_profile=millforge_profile,
    )


@contextmanager
def materialize_adapter_config_snapshot(
    decision: HarnessDecision,
    *,
    artifact_root: Path,
) -> Iterator[Path]:
    """Materialize the exact preflighted Millforge config for one invocation."""

    raw = decision.adapter_config_bytes
    expected = decision.adapter_config_sha256
    if raw is None or expected is None:
        raise AssertionError("Millforge preflight did not freeze adapter config")
    accepted_root = decision.artifact_root
    if accepted_root is None or artifact_root.resolve() != accepted_root.resolve():
        raise AssertionError(
            "adapter config snapshot must use the accepted artifact root"
        )
    if not artifact_root.is_dir():
        raise AssertionError(
            "adapter config snapshot requires an established artifact root"
        )
    if sha256(raw).hexdigest() != expected:
        raise AssertionError("preflighted adapter config digest mismatch")
    path = artifact_root / ".millrace-e2e-adapter-config.json"
    try:
        with path.open("xb") as stream:
            stream.write(raw)
        if (
            path.read_bytes() != raw
            or sha256(path.read_bytes()).hexdigest() != expected
        ):
            raise AssertionError("materialized adapter config digest mismatch")
        yield path
    finally:
        path.unlink(missing_ok=True)


def preflight_selected_runner_authority(
    selected_plan: SelectedCompiledPlan,
    diagnostics: Sequence[Diagnostic],
    *,
    required_adapter_kind: str = "codex",
) -> RunnerPreflight:
    kinds = tuple(
        sorted({binding.adapter_kind for binding in selected_plan.runner_bindings})
    )
    warnings = tuple(
        diagnostic.code
        for diagnostic in diagnostics
        if diagnostic.severity == "warning"
    )
    if not kinds or kinds != (required_adapter_kind,):
        return RunnerPreflight(
            live_capable=False,
            classification="blocked_no_selected_live_runner_binding",
            selected_adapter_kinds=kinds,
            warning_codes=warnings,
            reason=(
                "Selected runner bindings must be codex; fake_local is not "
                "actual-model evidence."
            ),
        )
    return RunnerPreflight(
        live_capable=True,
        classification=None,
        selected_adapter_kinds=kinds,
        warning_codes=warnings,
        reason="selected runner bindings are live-capable",
    )


def classify_package_setup_failure(phase: str) -> str:
    if phase == "import":
        return "package_import_failure"
    if phase == "selection":
        return "package_selection_failure"
    raise ValueError(f"unsupported package setup phase: {phase}")


def classify_daemon_result(result: object) -> str:
    code = _result_value(result, "code")
    adapter_error_kind = _result_value(result, "adapter_error_kind")
    if code == "adapter_conversion_refused":
        return "model_output_schema_refusal"
    if code == "adapter_failure" and adapter_error_kind == "result_parse_failed":
        return "model_output_schema_refusal"
    if code == "adapter_failure":
        return "runner_adapter_failure"
    if code == "asset_material_refused":
        return "prompt_or_asset_quality_blocker"
    if code in {
        "observation_refused",
        "ready_state_corrupt",
        "ready_state_refused",
        "adapter_kind_refused",
    }:
        return "runtime_governance_refusal"
    if code == "no_ready_work":
        return "operator_visible_blocker"
    if code == "observation_accepted":
        raise ValueError(
            "accepted daemon units require status/trace evidence before "
            "closed_successfully classification"
        )
    raise ValueError(f"unsupported daemon result code: {code}")


def payload_digest(payload: Mapping[str, object]) -> str:
    return _payload_digest(payload)


def is_relative_to(path: Path, parent: Path) -> bool:
    return _is_relative_to(path, parent)


def bounded_codex_adapter_config(
    artifact_root: Path,
    canary: str,
) -> dict[str, object]:
    return {
        "codex": {
            "adapter_id": "codex-live-smoke",
            "wrapper_mode": "local_argv",
            "wrapper_argv": ["/usr/bin/true"],
            "cwd": str(artifact_root),
            "env_allowlist": {},
            "timeout_seconds": 120,
            "max_input_bundle_bytes": 65536,
            "max_stdout_bytes": 65536,
            "max_stderr_diagnostic_bytes": 4096,
            "redaction_policy": {
                "policy_id": "e2e-redaction",
                "secret_tokens": [canary],
            },
            "live_test_opt_in_env_flags": ["MILLRACE_E2E_ACTUAL_MODEL"],
        }
    }


def bounded_codex_live_env(
    tmp_path: Path,
    *,
    workspaces_root: Path,
    artifact_root: Path,
    canary: str,
) -> dict[str, str]:
    return {
        "MILLRACE_E2E_ACTUAL_MODEL": "1",
        "MILLRACE_E2E_RUNNER": "codex",
        "MILLRACE_E2E_WORKSPACES_ROOT": str(workspaces_root),
        "MILLRACE_E2E_ARTIFACT_ROOT": str(artifact_root),
        "MILLRACE_E2E_ADAPTER_CONFIG": str(tmp_path / "adapter.json"),
        "MILLRACE_E2E_SECRET_CANARY": canary,
        "MILLRACE_E2E_MAX_TICKS_PER_WORKFLOW": "8",
        "MILLRACE_E2E_MAX_ADAPTER_TIMEOUT_SECONDS": "120",
        "MILLRACE_E2E_MAX_INPUT_BUNDLE_BYTES": "65536",
        "MILLRACE_E2E_MAX_STDOUT_BYTES": "65536",
        "MILLRACE_E2E_MAX_STDERR_DIAGNOSTIC_BYTES": "4096",
        "MILLRACE_E2E_MAX_WORKFLOW_SECONDS": "600",
        "MILLRACE_E2E_MAX_TOTAL_SECONDS": "1800",
        "MILLRACE_E2E_MAX_RETRIES": "0",
    }


def invoke_cli(argv: list[str]) -> tuple[int, str, str]:
    from millrace.adapters.cli.main import main

    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(argv)
    return exit_code, stdout.getvalue(), stderr.getvalue()


def json_output(raw: str) -> dict[str, Any]:
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    return cast(dict[str, Any], parsed)


def cli_json(
    argv: list[str],
    *,
    expect_exit: int = 0,
    stream: str = "stdout",
) -> dict[str, Any]:
    exit_code, stdout, stderr = invoke_cli(argv)
    assert exit_code == expect_exit, (argv, exit_code, stdout, stderr)
    raw = stdout if stream == "stdout" else stderr
    return json_output(raw)


def setup_official_package_workspace(
    workspace: Path,
    *,
    package_root: Path,
    package_id: str,
    package_version: str,
    workflow_id: str,
    workflow_version: str,
    entrypoint: str,
    command_scope: str,
) -> OfficialPackageSetup:
    cli_json(
        [
            "--json",
            "--workspace",
            str(workspace),
            "workspace",
            "init",
            "--input-id",
            f"init-{command_scope}-workspace",
        ]
    )
    cli_json(
        [
            "--json",
            "--workspace",
            str(workspace),
            "package",
            "import-path",
            str(package_root),
            "--command-id",
            f"import-{command_scope}-package",
        ]
    )
    cli_json(
        [
            "--json",
            "--workspace",
            str(workspace),
            "package",
            "enable",
            package_id,
            package_version,
            "--command-id",
            f"enable-{command_scope}-package",
        ]
    )
    verified = cli_json(
        [
            "--json",
            "--workspace",
            str(workspace),
            "package",
            "verify",
            package_id,
            package_version,
            "--workflow-id",
            workflow_id,
            "--workflow-version",
            workflow_version,
            "--entrypoint",
            entrypoint,
            "--command-id",
            f"verify-{command_scope}-package",
        ]
    )
    assert verified["data"]["plan_ready"] is True
    admitted = cli_json(
        [
            "--json",
            "--workspace",
            str(workspace),
            "plan",
            "admit-package",
            package_id,
            package_version,
            "--workflow-id",
            workflow_id,
            "--workflow-version",
            workflow_version,
            "--entrypoint",
            entrypoint,
            "--command-id",
            f"admit-{command_scope}-package",
            "--input-id",
            f"admit-{command_scope}-plan",
        ]
    )
    plan_fingerprint = str(admitted["data"]["plan"]["authority_fingerprint"])
    cli_json(
        [
            "--json",
            "--workspace",
            str(workspace),
            "plan",
            "select-default",
            plan_fingerprint,
            "--input-id",
            f"select-{command_scope}-plan",
        ]
    )
    return OfficialPackageSetup(
        workspace=workspace,
        plan_fingerprint=plan_fingerprint,
        warning_codes=tuple(
            diagnostic["code"] for diagnostic in admitted["data"]["diagnostics"]
        ),
    )


def selected_runner_binding_evidence(
    plan: SelectedCompiledPlan,
) -> list[dict[str, object]]:
    return [
        {
            "binding_id": str(binding.id),
            "adapter_kind": binding.adapter_kind,
            "component_pin": runner_component_pin_evidence(binding.component_pin),
            "terminal_result_mappings": [
                {
                    "stage_kind_id": str(mapping.stage_kind_id),
                    "runner_result_id": mapping.runner_result_id,
                    "outcome_id": str(mapping.outcome_id),
                }
                for mapping in binding.terminal_result_mappings
            ],
        }
        for binding in plan.runner_bindings
        if binding.component_pin is not None
    ]


def runner_component_pin_evidence(
    pin: RunnerComponentPin,
) -> dict[str, object]:
    return {
        "record_kind": pin.record_kind,
        "schema_version": pin.schema_version,
        "component_kind": pin.component_kind,
        "component_id": pin.component_id,
        "component_version": pin.component_version,
        "provider_distribution": pin.provider_distribution,
        "provider_version": pin.provider_version,
        "descriptor_media_type": pin.descriptor_media_type,
        "descriptor_sha256": pin.descriptor_sha256,
        "required_capability_ids": [
            str(capability_id) for capability_id in pin.required_capability_ids
        ],
        "legal_terminal_result_ids": list(pin.legal_terminal_result_ids),
    }


def workflow_package_pin_evidence(
    plan: SelectedCompiledPlan,
) -> dict[str, object]:
    pin = plan.workflow_package_pin
    assert pin is not None
    return {
        "record_kind": pin.record_kind,
        "schema_version": pin.schema_version,
        "package_id": str(pin.package_id),
        "package_version": pin.package_version,
        "package_format_version": pin.package_format_version,
        "workflow_id": str(pin.workflow_id),
        "workflow_version": pin.workflow_version,
        "entrypoint": pin.entrypoint,
        "selected_asset_pins": [
            {
                "asset_id": asset.asset_id,
                "content_digest": asset.content_digest,
            }
            for asset in pin.selected_asset_pins
        ],
        "selected_dependency_pins": [
            {
                "package_id": dependency.package_id,
                "package_version": dependency.package_version,
                "package_format_version": dependency.package_format_version,
            }
            for dependency in pin.selected_dependency_pins
        ],
    }


def millforge_profile_evidence_payload(
    profile: MillforgeProfileEvidence,
) -> dict[str, object]:
    return {
        "profile_id": profile.profile_id,
        "provider_id": profile.provider_id,
        "model_id": profile.model_id,
        "adapter_timeout_seconds": profile.adapter_timeout_seconds,
        "profile_timeout_seconds": profile.profile_timeout_seconds,
        "maximum_output_tokens": profile.maximum_output_tokens,
        "reasoning_mode": profile.reasoning_mode,
        "reasoning_effort": profile.reasoning_effort,
    }


def write_canonical_json_evidence(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def live_cli_json(argv: list[str]) -> dict[str, Any]:
    return cli_json(argv)


def open_runtime(workspace: Path) -> Any:
    from millrace.adapters.cli.context import CliWorkspacePaths, OpenRuntimeContext
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    return OpenRuntimeContext(
        paths=CliWorkspacePaths(workspace, db_path, cas_path),
        store=SQLiteRuntimeStore.open(db_path),
        cas_store=ContentAddressedByteStore(cas_path),
    )


def load_runtime_state(workspace: Path) -> Any:
    runtime = open_runtime(workspace)
    try:
        return runtime.store.load_runtime_state(runtime.cas_store)
    finally:
        runtime.close()


def payload_code(value: object) -> object:
    if isinstance(value, Mapping):
        return value.get("code")
    return None


def payload_mapping(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = payload.get(key)
    if isinstance(value, Mapping):
        return value
    return {}


def scan_for_secret_canary(
    roots: Sequence[Path],
    canary: str,
) -> tuple[Path, ...]:
    if not canary:
        raise ValueError("canary must be nonblank")
    matches: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        paths = (root,) if root.is_file() else tuple(root.rglob("*"))
        for path in paths:
            if path.is_file() and _file_contains(path, canary):
                matches.append(path)
    return tuple(sorted(matches))


def redact_review_text(value: object, canary: str) -> str:
    return json.dumps(
        _redact_value(value, canary),
        sort_keys=True,
        separators=(",", ":"),
    )


def smoke_matrix(
    *,
    package_source_mode: str,
    pyproject_path: Path,
) -> tuple[SmokeRow, ...]:
    if package_source_mode not in PACKAGE_SOURCE_MODES:
        raise ValueError("package_source_mode must be path, archive, or installed")
    _assert_no_hidden_millrace_plus_dependency(pyproject_path)
    package_rows = (
        ("plus.simple_loop", "simple_loop", "work_prompt", False),
        ("plus.execution_lad", "execution.lad", "task", False),
        ("plus.execution_lad_integrator", "execution.lad_integrator", "task", False),
        ("plus.planning_lad", "planning.lad", "spec", False),
        ("plus.lad_full_spec", "lad.full", "spec", False),
        ("plus.lad_full_learning_request", "lad.full", "learning_request", False),
        ("plus.vendor_selection", "vendor_selection", "purchase_request", False),
    )
    return (
        SmokeRow(
            row_id="base.kernel_ping",
            source="millrace-ai compiled export",
            workflow_id="kernel_ping",
            workflow_version="0.1",
            entrypoint=None,
            external_queue="prompt",
            package_source_mode=None,
            owns_live_row=True,
        ),
        *(
            SmokeRow(
                row_id=row_id,
                source="millrace-plus package",
                workflow_id=workflow_id,
                workflow_version="0.1",
                entrypoint="default",
                external_queue=queue,
                package_source_mode=package_source_mode,
                owns_live_row=owns_live,
                package_id="millrace.plus.official",
                package_version="0.22.0",
            )
            for row_id, workflow_id, queue, owns_live in package_rows
        ),
    )


def _blocked(
    classification: str,
    reason: str,
    *,
    runner: str | None = None,
    adapter_config_path: Path | None = None,
    artifact_root: Path | None = None,
) -> HarnessDecision:
    if classification not in CLASSIFICATIONS:
        raise ValueError("unsupported classification")
    return HarnessDecision(
        run_live=False,
        classification=classification,
        reason=reason,
        runner=runner,
        adapter_config_path=adapter_config_path,
        artifact_root=artifact_root,
    )


def _invalid_bound_keys(env: Mapping[str, str]) -> tuple[str, ...]:
    invalid: list[str] = []
    for key in REQUIRED_ENV_BOUNDS:
        value = env.get(key)
        if value is None:
            continue
        try:
            parsed = int(value)
        except ValueError:
            invalid.append(key)
            continue
        if parsed < 0 or (parsed == 0 and key != "MILLRACE_E2E_MAX_RETRIES"):
            invalid.append(key)
    return tuple(invalid)


def _read_json_object(path: Path) -> Mapping[str, object]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, Mapping):
        raise ValueError("adapter config must be a JSON object")
    return parsed


def _load_millforge_preflight(
    config_path: Path,
    env: Mapping[str, str],
    *,
    artifact_root: Path,
) -> tuple[bytes, str, MillforgeProfileEvidence]:
    from millrace.adapters.cli.context import CliCommandError
    from millrace.adapters.cli.run import load_adapter_local_config
    from millrace.adapters.millforge import MillforgeAdapter

    raw = config_path.read_bytes()
    try:
        local_config = load_adapter_local_config(config_path)
    except CliCommandError as exc:
        raise ValueError("production adapter config refused Millforge config") from exc
    if config_path.read_bytes() != raw:
        raise ValueError("adapter config changed during preflight")
    if set(local_config.adapters) != {"millforge"}:
        raise ValueError("Millforge must be the only configured E2E adapter")
    adapter = local_config.adapters["millforge"]
    if not isinstance(adapter, MillforgeAdapter):
        raise ValueError("production loader did not construct Millforge adapter")

    config = adapter.config
    if config.workspace_root != artifact_root.resolve():
        raise ValueError("Millforge workspace_root must equal the E2E artifact root")
    timeout_cap = int(env["MILLRACE_E2E_MAX_ADAPTER_TIMEOUT_SECONDS"])
    if config.timeout_seconds > timeout_cap:
        raise ValueError("Millforge timeout exceeds the E2E adapter cap")
    live_config = config.live_config
    if live_config is None:
        raise ValueError("Millforge E2E requires lazy live configuration")

    profile, secret_ref = _validate_millforge_public_records(
        live_config.model_profile,
        live_config.secret_ref,
    )
    profile_timeout = _required_positive_number(
        getattr(profile, "timeout_seconds", None),
        "model_profile.timeout_seconds",
    )
    if profile_timeout > config.timeout_seconds:
        raise ValueError("profile timeout exceeds adapter timeout")
    output_cap = getattr(profile, "maximum_output_tokens", None)
    if type(output_cap) is not int or output_cap <= 0:
        raise ValueError("maximum_output_tokens must be a positive integer")

    env_var = getattr(secret_ref, "env_var", None)
    if not isinstance(env_var, str) or not env_var.strip():
        raise ValueError("secret_ref.env_var must be nonblank")
    secret = env.get(env_var)
    if secret is None or not secret.strip():
        raise ValueError("secret_ref.env_var must name an available secret")
    if secret.encode("utf-8") in raw:
        raise ValueError("resolved secret must not appear in adapter config bytes")

    reasoning = getattr(profile, "reasoning", None)
    reasoning_mode = _optional_public_string(getattr(reasoning, "mode", None))
    reasoning_effort = _optional_public_string(getattr(reasoning, "effort", None))

    return raw, sha256(raw).hexdigest(), MillforgeProfileEvidence(
        profile_id=_required_nonblank_string(
            getattr(profile, "profile_id", None),
            "profile_id",
        ),
        provider_id=_required_nonblank_string(
            getattr(profile, "provider_id", None),
            "provider_id",
        ),
        model_id=_required_nonblank_string(
            getattr(profile, "model_id", None),
            "model_id",
        ),
        adapter_timeout_seconds=float(config.timeout_seconds),
        profile_timeout_seconds=profile_timeout,
        maximum_output_tokens=output_cap,
        reasoning_mode=reasoning_mode,
        reasoning_effort=reasoning_effort,
        secret_env_var=env_var,
    )


def _validate_millforge_public_records(
    profile_payload: Mapping[str, object],
    secret_ref_payload: Mapping[str, object],
) -> tuple[object, object]:
    try:
        import millforge  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ValueError("Millforge public records are unavailable") from exc
    profile_type = getattr(millforge, "ResolvedModelProfile", None)
    secret_ref_type = getattr(millforge, "SecretRef", None)
    if not callable(getattr(profile_type, "model_validate", None)) or not callable(
        getattr(secret_ref_type, "model_validate", None)
    ):
        raise ValueError("Millforge public profile contract is unavailable")
    try:
        profile = cast(Any, profile_type).model_validate(dict(profile_payload))
        secret_ref = cast(Any, secret_ref_type).model_validate(
            dict(secret_ref_payload)
        )
    except Exception as exc:
        raise ValueError("Millforge public profile records are invalid") from exc
    if getattr(getattr(profile, "authentication", None), "secret_ref", None) != (
        secret_ref
    ):
        raise ValueError("Millforge profile and adapter secret references differ")
    supported = getattr(millforge, "CapabilitySupport", None)
    capabilities = getattr(profile, "capabilities", None)
    state_for = getattr(capabilities, "state_for", None)
    if supported is None or not callable(state_for):
        raise ValueError("Millforge model capability contract is unavailable")
    if any(
        state_for(capability) is not supported.SUPPORTED
        for capability in MILLFORGE_BASE_REQUIRED_MODEL_CAPABILITIES
    ):
        raise ValueError(
            "Millforge profile lacks required millforge-base model capabilities"
        )
    allowed_options = getattr(
        getattr(profile, "request_options", None),
        "allowed_options",
        (),
    )
    if any(
        option not in allowed_options
        for option in MILLFORGE_BASE_REQUIRED_REQUEST_OPTIONS
    ):
        raise ValueError(
            "Millforge profile lacks required millforge-base request options"
        )
    return profile, secret_ref


def _required_positive_number(value: object, field_name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(float(value))
        or float(value) <= 0
    ):
        raise ValueError(f"{field_name} must be finite and positive")
    return float(value)


def _required_nonblank_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be nonblank")
    return value


def _optional_public_string(value: object) -> str | None:
    if value is None:
        return None
    public_value = getattr(value, "value", value)
    return _required_nonblank_string(public_value, "reasoning value")


def _codex_config_errors(
    parsed: Mapping[str, object],
    env: Mapping[str, str],
    *,
    canary: str,
    repo_root: Path,
    artifact_root: Path,
) -> tuple[str, ...]:
    errors: list[str] = []
    if set(parsed) != {"codex"}:
        return ("codex",)
    codex = parsed.get("codex")
    if not isinstance(codex, Mapping):
        return ("codex",)
    errors.extend(key for key in REQUIRED_CODEX_CONFIG_KEYS if key not in codex)

    if codex.get("wrapper_mode") != "local_argv":
        errors.append("wrapper_mode")
    wrapper_argv = codex.get("wrapper_argv")
    if not isinstance(wrapper_argv, list) or not wrapper_argv:
        errors.append("wrapper_argv")
    elif any(not isinstance(item, str) or not item.strip() for item in wrapper_argv):
        errors.append("wrapper_argv")
    elif not Path(wrapper_argv[0]).is_absolute():
        errors.append("wrapper_argv")
    cwd = codex.get("cwd")
    if not isinstance(cwd, str) or not cwd.strip():
        errors.append("cwd")
    else:
        cwd_path = Path(cwd)
        if (
            not cwd_path.is_absolute()
            or _is_relative_to(cwd_path.resolve(), repo_root)
            or not _is_relative_to(cwd_path.resolve(), artifact_root.resolve())
        ):
            errors.append("cwd")

    errors.extend(
        _cap_error(
            codex,
            "timeout_seconds",
            env,
            "MILLRACE_E2E_MAX_ADAPTER_TIMEOUT_SECONDS",
        )
    )
    errors.extend(
        _cap_error(
            codex,
            "max_input_bundle_bytes",
            env,
            "MILLRACE_E2E_MAX_INPUT_BUNDLE_BYTES",
        )
    )
    errors.extend(
        _cap_error(codex, "max_stdout_bytes", env, "MILLRACE_E2E_MAX_STDOUT_BYTES")
    )
    errors.extend(
        _cap_error(
            codex,
            "max_stderr_diagnostic_bytes",
            env,
            "MILLRACE_E2E_MAX_STDERR_DIAGNOSTIC_BYTES",
        )
    )
    redaction_policy = codex.get("redaction_policy")
    if not isinstance(redaction_policy, Mapping):
        errors.append("redaction_policy")
    else:
        secret_tokens = redaction_policy.get("secret_tokens")
        if not isinstance(secret_tokens, list) or canary not in secret_tokens:
            errors.append("MILLRACE_E2E_SECRET_CANARY")
    opt_in_flags = codex.get("live_test_opt_in_env_flags")
    if (
        not isinstance(opt_in_flags, list)
        or "MILLRACE_E2E_ACTUAL_MODEL" not in opt_in_flags
    ):
        errors.append("live_test_opt_in_env_flags")
    return tuple(dict.fromkeys(errors))


def _cap_error(
    codex: Mapping[object, object],
    config_key: str,
    env: Mapping[str, str],
    env_key: str,
) -> tuple[str, ...]:
    raw_config = codex.get(config_key)
    raw_env = env.get(env_key)
    if isinstance(raw_config, bool) or not isinstance(raw_config, (int, float)):
        return (config_key,)
    if raw_env is None:
        return (config_key,)
    try:
        max_value = int(raw_env)
    except ValueError:
        return (env_key,)
    config_value = float(raw_config)
    if not isfinite(config_value) or config_value <= 0 or config_value > max_value:
        return (config_key,)
    return ()


def _result_value(result: object, key: str) -> object:
    if isinstance(result, Mapping):
        return result.get(key)
    return getattr(result, key, None)


def _file_contains(path: Path, canary: str) -> bool:
    try:
        return canary in path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def _redact_value(value: object, canary: str) -> object:
    if isinstance(value, str):
        return value.replace(canary, "[REDACTED]")
    if isinstance(value, Mapping):
        return {
            str(key): _redact_value(nested, canary)
            for key, nested in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_value(nested, canary) for nested in value]
    return value


def _assert_no_hidden_millrace_plus_dependency(pyproject_path: Path) -> None:
    project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    dependencies = project.get("project", {}).get("dependencies", [])
    normalized = {_dependency_name(dependency) for dependency in dependencies}
    if "millrace-plus" in normalized:
        raise AssertionError("millrace-plus hidden base dependency is forbidden")


def _dependency_name(dependency: object) -> str:
    value = str(dependency).strip()
    value = re.split(r"\s+@|[<>=!~;]", value, maxsplit=1)[0].strip()
    value = value.split("[", maxsplit=1)[0].strip()
    return re.sub(r"[-_.]+", "-", value).lower()


def _payload_digest(payload: Mapping[str, object]) -> str:
    return "sha256:" + sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


__all__ = (
    "CLASSIFICATIONS",
    "HarnessDecision",
    "MillforgeProfileEvidence",
    "OfficialPackageSetup",
    "RunnerPreflight",
    "SmokeRow",
    "bounded_codex_adapter_config",
    "bounded_codex_live_env",
    "classify_daemon_result",
    "classify_package_setup_failure",
    "cli_json",
    "invoke_cli",
    "is_relative_to",
    "json_output",
    "live_cli_json",
    "load_runtime_state",
    "materialize_adapter_config_snapshot",
    "millforge_profile_evidence_payload",
    "open_runtime",
    "payload_code",
    "payload_digest",
    "payload_mapping",
    "plan_live_smoke",
    "preflight_selected_runner_authority",
    "redact_review_text",
    "scan_for_secret_canary",
    "selected_runner_binding_evidence",
    "setup_official_package_workspace",
    "smoke_matrix",
    "runner_component_pin_evidence",
    "workflow_package_pin_evidence",
    "write_canonical_json_evidence",
)
