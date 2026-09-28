import copy
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from millrace.adapters.pi_rpc_config import (
    _LOCAL_SCHEMA,
    _PROFILE_SCHEMA,
    PiAttempt,
    PiRpcConfig,
    _validate,
    regular_bytes,
    strict_json,
)


def example(schema):
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema["type"]
    if kind == "object":
        return {k: example(v) for k, v in schema["properties"].items()}
    if kind == "array":
        return (
            [example(v) for v in schema["prefixItems"]]
            if "prefixItems" in schema
            else [example(schema["items"]) for _ in range(schema["minItems"])]
        )
    if kind == "integer":
        return schema["minimum"]
    if schema.get("pattern") == "^[0-9a-f]{64}$":
        return "a" * 64
    if schema.get("pattern", "").startswith("^/"):
        return "/owned/example"
    if schema.get("pattern", "").startswith("^http"):
        return "http://127.0.0.1:1234/v1"
    return "opaque"


@pytest.mark.parametrize("schema", [_LOCAL_SCHEMA, _PROFILE_SCHEMA])
def test_strict_schema_refuses_unknown_missing_and_wrong_types(schema):
    value = example(schema)
    _validate(schema, value)
    with pytest.raises(ValueError):
        _validate(schema, {**value, "unknown": 1})
    with pytest.raises(ValueError):
        _validate(schema, {})


@pytest.mark.parametrize(
    "raw", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":1e999}', "[" * 65 + "0" + "]" * 65]
)
def test_duplicate_nonfinite_and_nested_json_refuse(raw):
    with pytest.raises(ValueError):
        strict_json(raw)


def test_local_parser_refuses_unknown_before_any_file_or_process_work():
    with pytest.raises(ValueError):
        PiRpcConfig.from_json({"unknown": True})


def test_long_session_transport_ceiling_is_finite_and_enforced():
    config = example(_LOCAL_SCHEMA)
    config.update(
        max_event_count=500_000,
        max_stdout_bytes=268_435_456,
        max_event_bytes=1_048_576,
    )
    _validate(_LOCAL_SCHEMA, config)
    for name, too_large in (
        ("max_event_count", 500_001),
        ("max_stdout_bytes", 268_435_457),
    ):
        with pytest.raises(ValueError):
            _validate(_LOCAL_SCHEMA, {**config, name: too_large})


def test_pi_session_timeout_can_cover_followup_builder_without_becoming_unbounded():
    config = example(_LOCAL_SCHEMA)
    _validate(_LOCAL_SCHEMA, {**config, "timeout_seconds": 9_600})
    with pytest.raises(ValueError):
        _validate(_LOCAL_SCHEMA, {**config, "timeout_seconds": 9_601})


def test_regular_input_rejects_symlink_and_oversize(tmp_path):
    path = tmp_path / "input"
    path.write_text("bytes")
    link = tmp_path / "link"
    link.symlink_to(path)
    assert regular_bytes(path) == b"bytes"
    with pytest.raises(ValueError):
        regular_bytes(link)
    with pytest.raises(ValueError):
        regular_bytes(path, 2)


def test_owned_cleanup_preserves_siblings_and_refuses_replacement(tmp_path):
    owned = tmp_path / "attempt"
    owned.mkdir()
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    (sibling / "sentinel").write_text("keep")
    s = owned.stat()
    attempt = PiAttempt(owned, (s.st_dev, s.st_ino), {})
    (owned / "link").symlink_to(sibling, target_is_directory=True)
    attempt.cleanup()
    attempt.cleanup()
    assert (sibling / "sentinel").read_text() == "keep"
    replacement = tmp_path / "replace"
    replacement.mkdir()
    st = replacement.stat()
    other = PiAttempt(replacement, (st.st_dev, st.st_ino), {})
    replacement.rename(tmp_path / "retained")
    replacement.symlink_to(sibling, target_is_directory=True)
    with pytest.raises(ValueError):
        other.cleanup()
    assert (sibling / "sentinel").exists()


def filesystem_config(tmp_path, monkeypatch):
    """Synthetic install identities test filesystem mechanics, not Pi qualification."""
    import hashlib

    from millrace.adapters import pi_rpc_config as module

    p = example(_PROFILE_SCHEMA)
    c = example(_LOCAL_SCHEMA)

    def ref(path):
        return {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    package = tmp_path / "package"
    package.mkdir()
    for name in (
        "dist/bundle/cli.js",
        "dist/bundle/cli-runtime.js",
        "package.json",
        "npm-shrinkwrap.json",
    ):
        file = package / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("{}")
    node = tmp_path / "node"
    node.write_text("fixture, never executed")
    node.chmod(0o755)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "path": str(f.relative_to(package)),
                        "kind": "file",
                        "mode": 0o644,
                        "size": f.stat().st_size,
                        "sha256": ref(f)["sha256"],
                    }
                    for f in sorted(package.rglob("*"))
                    if f.is_file()
                ]
            }
        )
    )
    p["installation"].update(
        package_root=str(package),
        node=ref(node),
        package_manifest=ref(manifest),
        loader=ref(package / "dist/bundle/cli.js"),
        runtime=ref(package / "dist/bundle/cli-runtime.js"),
        package_json=ref(package / "package.json"),
        shrinkwrap=ref(package / "npm-shrinkwrap.json"),
    )
    monkeypatch.setattr(
        module,
        "_PINNED_SOURCE",
        {name: p["installation"][name]["sha256"] for name in module._PINNED_SOURCE},
    )
    monkeypatch.setattr(
        module,
        "_LINUX_PINNED_SOURCE",
        {
            name: p["installation"][name]["sha256"]
            for name in module._LINUX_PINNED_SOURCE
        },
    )
    p["managed_args"][4] = p["model"]["provider"]
    p["managed_args"][6] = p["model"]["id"]
    template = tmp_path / "templates"
    template.mkdir()
    p["template_dir"] = str(template)
    m = p["model"]
    models = {
        "providers": {
            m["provider"]: {
                "api": m["api"],
                "apiKey": "${PI_RPC_API_KEY}",
                "baseUrl": m["endpoint"],
                "compat": {
                    "supportsDeveloperRole": False,
                    "supportsReasoningEffort": True,
                },
                "models": [
                    {
                        "id": m["id"],
                        "name": "fixture",
                        "reasoning": True,
                        "input": ["text"],
                        "contextWindow": m["context_window"],
                        "maxTokens": m["max_output_tokens"],
                        "cost": {
                            "input": 0,
                            "output": 0,
                            "cacheRead": 0,
                            "cacheWrite": 0,
                        },
                        "thinkingLevelMap": dict(
                            off="none",
                            minimal="minimal",
                            low="low",
                            medium="medium",
                            high="high",
                            xhigh="xhigh",
                            max="max",
                        ),
                    }
                ],
            }
        }
    }
    for name, value in [("auth", {}), ("settings", p["settings"]), ("models", models)]:
        file = template / (name + ".json")
        file.write_text(json.dumps(value))
        p["files"][name] = ref(file)
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps(p))
    attempts = tmp_path / "attempts"
    attempts.mkdir(mode=0o700)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    c.update(
        profile=ref(profile),
        executable=str(node),
        argv=[str(package / "dist/bundle/cli.js")],
        attempt_root=str(attempts),
        cwd=str(cwd),
    )
    c["env_allowlist"]["PATH"] = "/bin"
    monkeypatch.setenv("PI_RPC_API_KEY", "fixture-secret")
    monkeypatch.setenv("UNRELATED_PROVIDER_KEY", "must-not-leak")
    return PiRpcConfig.from_json(c)


def test_accepted_transport_limits_reach_bridge_and_client(tmp_path, monkeypatch):
    from millrace.adapters.pi_rpc_client import PiRpcClient
    from millrace.adapters.pi_rpc_groups import GroupProfile
    from tests.support.pi_rpc import MODEL

    original = filesystem_config(tmp_path, monkeypatch)
    authored = {
        **original.data,
        "max_event_count": 500_000,
        "max_stdout_bytes": 268_435_456,
        "max_event_bytes": 1_048_576,
    }
    accepted = PiRpcConfig.from_json(authored)
    group = GroupProfile(
        Path("/package/bridge.mjs"), "b" * 64, Path("/package"), "c" * 64
    )
    argv = group.argv(("node", "pi"), 11, accepted.data)
    assert parse_qs(urlsplit(argv[2]).query) == {
        "fd": ["11"],
        "profile": ["c" * 64],
        "records": ["500000"],
        "bytes": ["268435456"],
        "line": ["1048576"],
    }
    client = PiRpcClient(
        (
            sys.executable,
            str(Path(__file__).parents[1] / "fixtures/pi_rpc_process.py"),
            "normal",
        ),
        accepted.cwd,
        {"HOME": str(tmp_path), "TMPDIR": str(tmp_path)},
        accepted.data,
        MODEL,
        "prompt",
        1,
    )
    try:
        assert client.events.maxsize == 500_000
        assert client.limits["max_stdout_bytes"] == 268_435_456
    finally:
        client.dispose()


def bonsai_local_data(tmp_path, monkeypatch):
    """Build an authored local Bonsai profile from the existing pinned fixture."""
    config = filesystem_config(tmp_path, monkeypatch)
    profile = copy.deepcopy(config.profile)
    profile["qualification_scope"] = "loopback-bonsai2-openai-completions"
    profile["model"].update(
        provider="local",
        id="bonsai2-27b-pq2",
        endpoint="http://127.0.0.1:8080/v1",
        context_window=262144,
        max_output_tokens=262144,
        sampling="bonsai2-pinned-1.0-0.95-20",
    )
    profile["managed_args"][4] = "local"
    profile["managed_args"][6] = "bonsai2-27b-pq2"
    model_path = Path(profile["files"]["models"]["path"])
    models = json.loads(model_path.read_text())
    provider = models["providers"].pop(config.profile["model"]["provider"])
    models["providers"]["local"] = provider
    provider["baseUrl"] = profile["model"]["endpoint"]
    provider["compat"]["supportsReasoningEffort"] = False
    model = provider["models"][0]
    model.update(
        id="bonsai2-27b-pq2",
        contextWindow=262144,
        maxTokens=262144,
        compat={
            "maxTokensField": "max_tokens",
            "supportsReasoningEffort": False,
            "thinkingFormat": "chat-template",
            "chatTemplateKwargs": {"enable_thinking": {"$var": "thinking.enabled"}},
        },
        samplingParams={"temperature": 1.0, "top_p": 0.95, "top_k": 20},
    )
    local = copy.deepcopy(config.data)
    save_profile_and_models(local, profile, models)
    return local, profile, models


def save_profile_and_models(local, profile, models):
    model_path = Path(profile["files"]["models"]["path"])
    model_path.write_text(json.dumps(models))
    profile["files"]["models"]["sha256"] = hashlib.sha256(
        model_path.read_bytes()
    ).hexdigest()
    profile_path = Path(local["profile"]["path"])
    profile_path.write_text(json.dumps(profile))
    local["profile"]["sha256"] = hashlib.sha256(profile_path.read_bytes()).hexdigest()


def test_local_bonsai_profile_accepts_full_context_and_template(tmp_path, monkeypatch):
    local, profile, models = bonsai_local_data(tmp_path, monkeypatch)
    config = PiRpcConfig.from_json(local)
    assert config.profile == profile
    assert config.verify()["models"] == json.dumps(models).encode()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:8080/v1",
        "http://127.0.0.2:8080/v1",
        "http://0.0.0.0:8080/v1",
        "https://127.0.0.1:8080/v1",
        "http://127.0.0.1:8080/other",
    ],
)
def test_local_bonsai_refuses_other_endpoints(tmp_path, monkeypatch, endpoint):
    local, profile, models = bonsai_local_data(tmp_path, monkeypatch)
    profile["model"]["endpoint"] = endpoint
    models["providers"]["local"]["baseUrl"] = endpoint
    save_profile_and_models(local, profile, models)
    with pytest.raises(ValueError):
        PiRpcConfig.from_json(local)


@pytest.mark.parametrize(
    "changed",
    [
        ("context_window", 196608),
        ("max_output_tokens", 256),
        ("id", "other-model"),
        ("sampling", "no-overrides-provider-default-unqualified"),
    ],
)
def test_local_bonsai_refuses_changed_model_authority(tmp_path, monkeypatch, changed):
    local, profile, models = bonsai_local_data(tmp_path, monkeypatch)
    key, value = changed
    profile["model"][key] = value
    if key == "context_window":
        models["providers"]["local"]["models"][0]["contextWindow"] = value
    elif key == "max_output_tokens":
        models["providers"]["local"]["models"][0]["maxTokens"] = value
    elif key == "id":
        models["providers"]["local"]["models"][0]["id"] = value
        profile["managed_args"][6] = value
    save_profile_and_models(local, profile, models)
    with pytest.raises(ValueError):
        PiRpcConfig.from_json(local)


@pytest.mark.parametrize(
    "part,key,value",
    [
        ("compat", "maxTokensField", "max_completion_tokens"),
        ("compat", "supportsReasoningEffort", True),
        ("compat", "thinkingFormat", "reasoning_effort"),
        ("compat", "chatTemplateKwargs", {"enable_thinking": True}),
        ("samplingParams", "temperature", 0.7),
        ("samplingParams", "top_p", 0.9),
        ("samplingParams", "top_k", 40),
    ],
)
def test_local_bonsai_refuses_changed_wire_template(
    tmp_path, monkeypatch, part, key, value
):
    local, profile, models = bonsai_local_data(tmp_path, monkeypatch)
    models["providers"]["local"]["models"][0][part][key] = value
    save_profile_and_models(local, profile, models)
    with pytest.raises(ValueError):
        PiRpcConfig.from_json(local)


def test_local_bonsai_refuses_scripted_or_remote_scope_alias(tmp_path, monkeypatch):
    local, profile, models = bonsai_local_data(tmp_path, monkeypatch)
    for scope in (
        "loopback-scripted-openai-completions",
        "unqualified-openai-completions",
    ):
        profile["qualification_scope"] = scope
        save_profile_and_models(local, profile, models)
        with pytest.raises(ValueError):
            PiRpcConfig.from_json(local)


def test_unqualified_remote_https_profile_remains_accepted(tmp_path, monkeypatch):
    config = filesystem_config(tmp_path, monkeypatch)
    local = copy.deepcopy(config.data)
    profile = copy.deepcopy(config.profile)
    profile["qualification_scope"] = "unqualified-openai-completions"
    profile["model"]["endpoint"] = "https://example.test/v1"
    model_path = Path(profile["files"]["models"]["path"])
    models = json.loads(model_path.read_text())
    models["providers"][profile["model"]["provider"]]["baseUrl"] = (
        "https://example.test/v1"
    )
    save_profile_and_models(local, profile, models)
    assert PiRpcConfig.from_json(local).profile == profile


def test_stable_templates_fresh_isolated_attempts_and_drift_refusal(
    tmp_path, monkeypatch
):
    config = filesystem_config(tmp_path, monkeypatch)
    a = config.materialize()
    b = config.materialize()
    try:
        assert a.path != b.path
        assert "UNRELATED_PROVIDER_KEY" not in a.env
        assert a.env["HOME"] != b.env["HOME"]
        (a.path / "agent/settings.json").write_text("poison")
        assert (b.path / "agent/settings.json").read_bytes() == config.verify()[
            "settings"
        ]
        with pytest.raises(ValueError):
            config.prespawn(a)
        assert config.verify()
    finally:
        a.cleanup()
        b.cleanup()
    assert not list(Path(config.data["attempt_root"]).iterdir())


def test_stable_input_drift_and_partial_setup_never_spawn(tmp_path, monkeypatch):
    config = filesystem_config(tmp_path, monkeypatch)
    monkeypatch.setattr(
        PiRpcConfig,
        "prespawn",
        lambda self, attempt: (_ for _ in ()).throw(ValueError("injected after copy")),
    )
    with pytest.raises(ValueError):
        config.materialize()
    assert not list(Path(config.data["attempt_root"]).iterdir())
    Path(config.profile["files"]["settings"]["path"]).write_text("{}")
    with pytest.raises(ValueError):
        config.verify()


def test_environment_injection_after_materialization_refuses(tmp_path, monkeypatch):
    config = filesystem_config(tmp_path, monkeypatch)
    attempt = config.materialize()
    try:
        attempt.env["NODE_OPTIONS"] = "--require injected"
        with pytest.raises(ValueError, match="environment drift"):
            config.prespawn(attempt)
    finally:
        attempt.cleanup()


@pytest.mark.parametrize("entrypoint", ["regular", "cli"])
def test_fifo_acquisition_refuses_with_finite_reaped_watchdog(tmp_path, entrypoint):
    import os
    import subprocess
    import sys

    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    code = """from pathlib import Path
from millrace.adapters.pi_rpc_config import regular_bytes
from millrace.adapters.cli.run import load_adapter_local_config, CliCommandError
import sys
try:
    reader = regular_bytes if sys.argv[2] == "regular" else load_adapter_local_config
    reader(Path(sys.argv[1]))
except CliCommandError:
    assert sys.argv[2] == "cli"
    raise SystemExit(0)
except (ValueError, OSError):
    assert sys.argv[2] == "regular"
    raise SystemExit(0)
raise SystemExit(2)
"""
    env = {
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(Path(__file__).parents[2] / "src"),
    }
    child = subprocess.Popen(
        [sys.executable, "-B", "-c", code, str(fifo), entrypoint],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    blocked = False
    try:
        child.communicate(timeout=1)
    except subprocess.TimeoutExpired:
        blocked = True
        child.terminate()
        child.communicate(timeout=2)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=2)
    assert not blocked, "regular input acquisition blocked before fstat"
    assert child.returncode == 0


def test_identity_owned_already_absent_cleanup_is_idempotent(tmp_path):
    path = tmp_path / "owned"
    path.mkdir()
    s = path.stat()
    attempt = PiAttempt(path, (s.st_dev, s.st_ino), {"secret": "fixture"})
    path.rmdir()
    attempt.cleanup()
    attempt.cleanup()
    assert attempt.removed and not attempt.env


def test_nonregular_device_and_descriptor_replacement_refuse(tmp_path, monkeypatch):
    import os

    from millrace.adapters import pi_rpc_config as module

    # Existing null device is read-only; no device is created or modified.
    with pytest.raises(ValueError):
        regular_bytes(Path("/dev/null"))
    path = tmp_path / "replace-on-open"
    path.write_bytes(b"original")
    original_open = os.open

    def raced_open(selected, flags, *args, **kwargs):
        if selected == path:
            assert flags & os.O_NONBLOCK
            path.unlink()
            os.mkfifo(path)
        return original_open(selected, flags, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", raced_open)
    with pytest.raises(ValueError, match="input type"):
        regular_bytes(path)


@pytest.mark.parametrize("which", ["profile", "settings", "manifest"])
def test_config_inputs_reject_oversize_before_hash_or_parse(
    tmp_path, monkeypatch, which
):
    config = filesystem_config(tmp_path, monkeypatch)
    if which == "profile":
        path, maximum = Path(config.data["profile"]["path"]), 65536
    elif which == "settings":
        path, maximum = Path(config.profile["files"]["settings"]["path"]), 65536
    else:
        path = Path(config.profile["installation"]["package_manifest"]["path"])
        maximum = 8388608
    with path.open("wb") as stream:
        stream.truncate(maximum + 1)
    with pytest.raises(ValueError, match="input type or size"):
        config.verify()


def test_v2_profile_is_explicit_and_requires_qualified_installed_bridge(
    tmp_path, monkeypatch
):
    import hashlib

    from millrace.adapters import pi_rpc_config as module
    from millrace.adapters.pi_rpc_groups import bridge_identity

    config = filesystem_config(tmp_path, monkeypatch)
    profile = config.profile.copy()
    profile.update(
        schema_version="pi-rpc-profile.v2",
        foreground_accounting={
            "kind": "trusted-posix-group-v1",
            "platform": module.sys.platform,
            "bridge": bridge_identity(),
            "protocol": "pi-group-observer.v1",
            "channel": "private-inherited-pipe",
            "survivor_policy": "sticky-at-root-exit",
            "escaped_descendants": "outside-guarantee",
        },
    )
    # Synthetic fixture has no qualified bundle; explicit v2 must reach that check,
    # rather than silently downgrading or accepting arbitrary executable content.
    path = Path(config.data["profile"]["path"])
    path.write_text(json.dumps(profile))
    data = {
        **config.data,
        "profile": {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
    }
    with pytest.raises(ValueError, match="group bundle identity"):
        PiRpcConfig.from_json(data)


def test_linux_v2_profile_selects_linux_witness_and_rejects_wrong_host(
    tmp_path, monkeypatch
):
    import hashlib

    from millrace.adapters import pi_rpc_config as module
    from millrace.adapters.pi_rpc_groups import bridge_identity

    config = filesystem_config(tmp_path, monkeypatch)
    profile = {
        **config.profile,
        "schema_version": "pi-rpc-profile.v2",
        "foreground_accounting": {
            "kind": "trusted-posix-group-v1",
            "platform": "linux",
            "bridge": bridge_identity(),
            "protocol": "pi-group-observer.v1",
            "channel": "private-inherited-pipe",
            "survivor_policy": "sticky-at-root-exit",
            "escaped_descendants": "outside-guarantee",
        },
    }
    path = Path(config.data["profile"]["path"])
    path.write_text(json.dumps(profile))
    data = {
        **config.data,
        "profile": {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
    }
    monkeypatch.setattr(module.sys, "platform", "darwin")
    with pytest.raises(ValueError, match="group observer platform"):
        PiRpcConfig.from_json(data)
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(
        module,
        "_LINUX_PINNED_SOURCE",
        {
            name: config.profile["installation"][name]["sha256"]
            for name in ("node", "shrinkwrap", "package_manifest")
        },
    )
    with pytest.raises(ValueError, match="group bundle identity"):
        PiRpcConfig.from_json(data)


def test_linux_v3_supervisor_identity_and_platform_are_explicit(tmp_path, monkeypatch):
    import hashlib

    from millrace.adapters import pi_rpc_config as module
    from millrace.adapters.pi_rpc_groups import bridge_identity, supervisor_identity

    config = filesystem_config(tmp_path, monkeypatch)
    profile = {
        **config.profile,
        "schema_version": "pi-rpc-profile.v3",
        "foreground_accounting": {
            "kind": "trusted-linux-subreaper-v1",
            "platform": "linux",
            "bridge": bridge_identity(),
            "supervisor": supervisor_identity(),
            "protocol": "pi-group-observer.v1",
            "channel": "private-inherited-pipe",
            "survivor_policy": "owned-subreaper-disposal",
            "escaped_descendants": "outside-guarantee",
        },
    }
    path = Path(config.data["profile"]["path"])
    path.write_text(json.dumps(profile))
    data = {
        **config.data,
        "profile": {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
    }
    monkeypatch.setattr(module.sys, "platform", "darwin")
    with pytest.raises(ValueError, match="group observer platform"):
        PiRpcConfig.from_json(data)
    profile["foreground_accounting"]["supervisor"]["sha256"] = "f" * 64
    path.write_text(json.dumps(profile))
    data["profile"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="unqualified Linux supervisor profile"):
        PiRpcConfig.from_json(data)


def test_linux_uses_its_own_installed_node_and_package_pins(tmp_path, monkeypatch):
    from millrace.adapters import pi_rpc_config as module

    config = filesystem_config(tmp_path, monkeypatch)
    monkeypatch.setattr(module.sys, "platform", "linux")
    pins = {
        name: config.profile["installation"][name]["sha256"]
        for name in ("node", "shrinkwrap", "package_manifest")
    }
    monkeypatch.setattr(module, "_LINUX_PINNED_SOURCE", pins)
    config.verify()
    pins["node"] = "f" * 64
    with pytest.raises(ValueError, match="unqualified implementation identity"):
        config.verify()


@pytest.mark.parametrize("platform", [[], {}, True, None, "freebsd"])
def test_v2_profile_refuses_malformed_or_unqualified_platform_as_config_error(
    tmp_path, monkeypatch, platform
):
    import hashlib

    from millrace.adapters.pi_rpc_groups import bridge_identity

    config = filesystem_config(tmp_path, monkeypatch)
    profile = {
        **config.profile,
        "schema_version": "pi-rpc-profile.v2",
        "foreground_accounting": {
            "kind": "trusted-posix-group-v1",
            "platform": platform,
            "bridge": bridge_identity(),
            "protocol": "pi-group-observer.v1",
            "channel": "private-inherited-pipe",
            "survivor_policy": "sticky-at-root-exit",
            "escaped_descendants": "outside-guarantee",
        },
    }
    path = Path(config.data["profile"]["path"])
    path.write_text(json.dumps(profile))
    data = {
        **config.data,
        "profile": {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
    }
    with pytest.raises(ValueError, match="unqualified group observer profile"):
        PiRpcConfig.from_json(data)
