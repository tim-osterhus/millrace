from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


def _direct_codex_config(tmp_path: Path, **overrides: object) -> object:
    from millrace.adapters.codex import CodexAdapterConfig
    from millrace.adapters.runner_contract import RedactionPolicy

    values: dict[str, object] = {
        "adapter_id": "codex-local",
        "wrapper_mode": "missing",
        "wrapper_argv": None,
        "cwd": tmp_path,
        "env_allowlist": {},
        "timeout_seconds": 5,
        "max_input_bundle_bytes": 8192,
        "max_stdout_bytes": 8192,
        "max_stderr_diagnostic_bytes": 512,
        "redaction_policy": RedactionPolicy(policy_id="local"),
    }
    values.update(overrides)
    return CodexAdapterConfig(**values)


def _codex_json(tmp_path: Path, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "adapter_id": "codex-local",
        "wrapper_mode": "missing",
        "wrapper_argv": None,
        "cwd": str(tmp_path),
        "env_allowlist": {},
        "timeout_seconds": 5,
        "max_input_bundle_bytes": 8192,
        "max_stdout_bytes": 8192,
        "max_stderr_diagnostic_bytes": 512,
        "redaction_policy": {"policy_id": "local", "secret_tokens": []},
    }
    values.update(overrides)
    return values


def test_codex_wrapper_protocol_defaults_are_stable_for_direct_and_json_config(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import load_adapter_local_config
    from millrace.adapters.codex import CodexAdapter

    direct = _direct_codex_config(tmp_path)
    assert direct.wrapper_protocol_version == 3

    path = tmp_path / "adapter.json"
    path.write_text(json.dumps({"codex": _codex_json(tmp_path)}), encoding="utf-8")
    loaded = load_adapter_local_config(path)
    adapter = loaded.adapters["codex"]
    assert isinstance(adapter, CodexAdapter)
    assert adapter._config.wrapper_protocol_version == 3


def test_codex_config_appended_protocol_field_preserves_positional_default(
    tmp_path: Path,
) -> None:
    from millrace.adapters.codex import CodexAdapterConfig
    from millrace.adapters.runner_contract import RedactionPolicy

    config = CodexAdapterConfig(
        "codex-local",
        "missing",
        None,
        tmp_path,
        {},
        5,
        8192,
        8192,
        512,
        RedactionPolicy(policy_id="local"),
        (),
        False,
    )

    assert config.wrapper_protocol_version == 3


def test_codex_wrapper_protocol_accepts_version_four_direct_and_json(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import load_adapter_local_config
    from millrace.adapters.codex import CodexAdapter

    direct = _direct_codex_config(tmp_path, wrapper_protocol_version=4)
    assert direct.wrapper_protocol_version == 4

    path = tmp_path / "adapter.json"
    path.write_text(
        json.dumps(
            {"codex": _codex_json(tmp_path, wrapper_protocol_version=4)},
        ),
        encoding="utf-8",
    )
    loaded = load_adapter_local_config(path)
    adapter = loaded.adapters["codex"]
    assert isinstance(adapter, CodexAdapter)
    assert adapter._config.wrapper_protocol_version == 4


@pytest.mark.parametrize(
    "value",
    (True, False, 2, 5, 3.0, "4", None),
)
def test_codex_wrapper_protocol_rejects_every_non_three_or_four_direct_value(
    tmp_path: Path,
    value: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        _direct_codex_config(tmp_path, wrapper_protocol_version=value)


@pytest.mark.parametrize(
    "value",
    (True, False, 2, 5, 3.0, "4", None),
)
def test_codex_wrapper_protocol_rejects_every_non_three_or_four_json_value(
    tmp_path: Path,
    value: object,
) -> None:
    from millrace.adapters.cli.run import load_adapter_local_config

    path = tmp_path / "adapter.json"
    path.write_text(
        json.dumps(
            {"codex": _codex_json(tmp_path, wrapper_protocol_version=value)},
        ),
        encoding="utf-8",
    )

    with pytest.raises(Exception) as error:
        load_adapter_local_config(path)

    assert getattr(error.value, "exit_code") == 2


def test_cli_adapter_config_instantiates_codex_config_without_exposing_secrets(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import load_adapter_local_config
    from millrace.adapters.codex import CodexAdapter

    config_path = tmp_path / "adapter.json"
    config = {
        "codex": {
            "adapter_id": "codex-secret",
            "wrapper_mode": "offline_fake",
            "wrapper_argv": [sys.executable, "-c", "print('ok')"],
            "cwd": str(tmp_path),
            "env_allowlist": {"TOKEN": "SECRET"},
            "timeout_seconds": 5,
            "max_input_bundle_bytes": 8192,
            "max_stdout_bytes": 8192,
            "max_stderr_diagnostic_bytes": 512,
            "redaction_policy": {
                "policy_id": "redact-secret",
                "secret_tokens": ["SECRET", "secret"],
            },
        }
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")

    local_config = load_adapter_local_config(config_path)

    adapter = local_config.adapters["codex"]
    assert isinstance(adapter, CodexAdapter)
    exposed = repr(local_config) + repr(adapter)
    assert "SECRET" not in exposed
    assert "secret" not in exposed


@pytest.mark.parametrize(
    "raw",
    (
        "",
        "[]",
        '{"codex": {"wrapper_mode": "offline_fake"}}',
        '{"fake_local": {}}',
    ),
)
def test_cli_adapter_config_invalid_json_or_shape_is_cli_usage(
    tmp_path: Path,
    raw: str,
) -> None:
    from millrace.adapters.cli.run import load_adapter_local_config

    path = tmp_path / "adapter.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(Exception) as error:
        load_adapter_local_config(path)

    assert getattr(error.value, "exit_code") == 2


def test_cli_millforge_config_builds_lazy_adapter_without_facade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli.run import load_adapter_local_config
    from millrace.adapters.millforge import MillforgeAdapter

    path = tmp_path / "adapter.json"
    path.write_text(
        json.dumps(
            {
                "millforge": {
                    "adapter_id": "millforge-local",
                    "workspace_root": str(tmp_path),
                    "timeout_seconds": 30,
                    "model_profile": {"profile_id": "profile-1"},
                    "secret_ref": {
                        "secret_id": "provider-key",
                        "env_var": "MILLRACE_TEST_PROVIDER_KEY",
                    },
                    "redaction_policy": {
                        "policy_id": "local-redaction",
                        "secret_tokens": [],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setitem(sys.modules, "millforge", None)

    local_config = load_adapter_local_config(path)

    adapter = local_config.adapters["millforge"]
    assert isinstance(adapter, MillforgeAdapter)
    assert adapter.config.facade is None
    assert adapter.config.live_config is not None
    assert "provider-key" not in repr(adapter.config)


def test_millforge_invalid_envelope_refuses_before_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli.run import load_adapter_local_config

    path = tmp_path / "adapter.json"
    path.write_text(
        json.dumps(
            {
                "millforge": {
                    "adapter_id": "millforge-local",
                    "workspace_root": "relative-root",
                    "timeout_seconds": 30,
                    "model_profile": {},
                    "secret_ref": {},
                    "redaction_policy": {
                        "policy_id": "local-redaction",
                        "secret_tokens": [],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setitem(sys.modules, "millforge", None)

    with pytest.raises(Exception) as error:
        load_adapter_local_config(path)

    assert getattr(error.value, "exit_code") == 2


@pytest.mark.parametrize(
    ("model_profile", "secret_tokens"),
    (
        ({"configured_headers": {"X-Test": "value"}}, []),
        ({}, ["literal-secret"]),
    ),
)
def test_millforge_config_refuses_headers_and_literal_secrets(
    tmp_path: Path,
    model_profile: dict[str, object],
    secret_tokens: list[str],
) -> None:
    from millrace.adapters.cli.run import load_adapter_local_config

    path = tmp_path / "adapter.json"
    path.write_text(
        json.dumps(
            {
                "millforge": {
                    "adapter_id": "millforge-local",
                    "workspace_root": str(tmp_path),
                    "timeout_seconds": 30,
                    "model_profile": model_profile,
                    "secret_ref": {
                        "secret_id": "provider-key",
                        "env_var": "MILLRACE_TEST_PROVIDER_KEY",
                    },
                    "redaction_policy": {
                        "policy_id": "local-redaction",
                        "secret_tokens": secret_tokens,
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(Exception) as error:
        load_adapter_local_config(path)

    assert getattr(error.value, "exit_code") == 2
