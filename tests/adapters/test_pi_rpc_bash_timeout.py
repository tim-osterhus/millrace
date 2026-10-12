"""Private qualified Bash guard: explicit v3 authority, no discovery widening."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from millrace.adapters import pi_rpc_config as module
from millrace.adapters.pi_rpc_config import PiRpcConfig
from millrace.adapters.pi_rpc_groups import bridge_identity, supervisor_identity
from tests.adapters.test_pi_rpc_config import filesystem_config

GUARD = (
    Path(__file__).resolve().parents[4]
    / "experiment/harness/d-timeout/bash_timeout.mjs"
)
GUARD_SHA256 = "09a688ec90c18afb52aa7a028a3ec02a0ae74cb374df962e0f133d802483d775"


def authored_guard(tmp_path, monkeypatch):
    base = filesystem_config(tmp_path, monkeypatch)
    profile = copy.deepcopy(base.profile)
    profile.update(
        schema_version="pi-rpc-profile.v3",
        foreground_accounting={
            "kind": "trusted-linux-subreaper-v1",
            "platform": "linux",
            "bridge": bridge_identity(),
            "supervisor": supervisor_identity(),
            "protocol": "pi-group-observer.v1",
            "channel": "private-inherited-pipe",
            "survivor_policy": "owned-subreaper-disposal",
            "escaped_descendants": "outside-guarantee",
        },
    )
    # The synthetic install exercises profile/file checks, never executes Pi.
    package = Path(profile["installation"]["package_root"])
    bundle = package / module.BUNDLE
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_text("{}")
    monkeypatch.setattr(module, "BUNDLE_SHA256", hashlib.sha256(b"{}").hexdigest())
    manifest = Path(profile["installation"]["package_manifest"]["path"])
    value = json.loads(manifest.read_text())
    value["entries"].append(
        {
            "path": module.BUNDLE,
            "kind": "file",
            "mode": 0o644,
            "size": 2,
            "sha256": hashlib.sha256(b"{}").hexdigest(),
        }
    )
    manifest.write_text(json.dumps(value))
    manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
    profile["installation"]["package_manifest"]["sha256"] = manifest_sha
    monkeypatch.setattr(
        module,
        "_LINUX_PINNED_SOURCE",
        {
            name: profile["installation"][name]["sha256"]
            for name in module._LINUX_PINNED_SOURCE
        },
    )
    monkeypatch.setattr(module.sys, "platform", "linux")
    extension = tmp_path / "bash_timeout.mjs"
    extension.write_bytes(GUARD.read_bytes())
    assert hashlib.sha256(extension.read_bytes()).hexdigest() == GUARD_SHA256
    audit = tmp_path / "timeout-audit.jsonl"
    audit.touch(mode=0o600)
    profile["bash_timeout"] = {
        "extension": {"path": str(extension), "sha256": GUARD_SHA256},
        "audit_path": str(audit),
    }
    return profile, copy.deepcopy(base.data)


def admit(profile, data):
    path = Path(data["profile"]["path"])
    path.write_text(json.dumps(profile))
    data["profile"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return PiRpcConfig.from_json(data)


def test_guarded_v3_materializes_exact_extension_and_audit_then_cleans(
    tmp_path, monkeypatch
):
    profile, data = authored_guard(tmp_path, monkeypatch)
    config = admit(profile, data)
    assert config.group_profile is not None and config.group_profile.bash_timeout
    assert config.managed_args == tuple(profile["managed_args"]) + (
        "-e",
        profile["bash_timeout"]["extension"]["path"],
    )
    attempt = config.materialize()
    try:
        assert (
            attempt.env["D_TIMEOUT_AUDIT_PATH"] == profile["bash_timeout"]["audit_path"]
        )
        assert "UNRELATED_PROVIDER_KEY" not in attempt.env
        config.prespawn(attempt)
        Path(profile["bash_timeout"]["audit_path"]).write_text('{"event":"fixture"}\n')
        config.prespawn(attempt)  # Legitimate appended evidence is legal aftermath.
        attempt.env["UNSELECTED_EXTENSION"] = "forbidden"
        with pytest.raises(ValueError, match="environment drift"):
            config.prespawn(attempt)
    finally:
        attempt.cleanup()
    assert not attempt.path.exists()
    assert Path(profile["bash_timeout"]["audit_path"]).exists()
    reloaded = admit(profile, data)
    assert reloaded.managed_args == config.managed_args


def test_unguarded_v3_group_profile_has_no_timeout_capability(tmp_path, monkeypatch):
    profile, data = authored_guard(tmp_path, monkeypatch)
    del profile["bash_timeout"]
    config = admit(profile, data)
    assert config.group_profile is not None and not config.group_profile.bash_timeout


def test_unguarded_v2_group_profile_has_no_timeout_capability(tmp_path, monkeypatch):
    profile, data = authored_guard(tmp_path, monkeypatch)
    del profile["bash_timeout"]
    profile["schema_version"] = "pi-rpc-profile.v2"
    profile["foreground_accounting"] = dict(
        kind="trusted-posix-group-v1",
        platform="linux",
        bridge=bridge_identity(),
        protocol="pi-group-observer.v1",
        channel="private-inherited-pipe",
        survivor_policy="sticky-at-root-exit",
        escaped_descendants="outside-guarantee",
    )
    config = admit(profile, data)
    assert config.group_profile is not None and not config.group_profile.bash_timeout


@pytest.mark.parametrize("surface", ["args", "settings", "env"])
def test_guard_does_not_admit_other_extension_surfaces(tmp_path, monkeypatch, surface):
    profile, data = authored_guard(tmp_path, monkeypatch)
    if surface == "args":
        profile["managed_args"] += ["-e", "/unselected.mjs"]
    elif surface == "settings":
        profile["settings"]["extensions"] = ["/unselected.mjs"]
    else:
        data["env_allowlist"]["D_TIMEOUT_AUDIT_PATH"] = "/unselected.jsonl"
    with pytest.raises(ValueError):
        admit(profile, data)


@pytest.mark.parametrize("surface", ["hash", "unknown", "v1", "v2"])
def test_guard_refuses_unqualified_profile_authority(tmp_path, monkeypatch, surface):
    profile, data = authored_guard(tmp_path, monkeypatch)
    if surface == "hash":
        profile["bash_timeout"]["extension"]["sha256"] = "f" * 64
    elif surface == "unknown":
        profile["bash_timeout"]["max_seconds"] = 3600
    else:
        profile["schema_version"] = f"pi-rpc-profile.{surface}"
    with pytest.raises(ValueError):
        admit(profile, data)


@pytest.mark.parametrize("surface", ["relative", "symlink", "drift", "missing"])
def test_guard_refuses_changed_extension_file(tmp_path, monkeypatch, surface):
    profile, data = authored_guard(tmp_path, monkeypatch)
    ref = profile["bash_timeout"]["extension"]
    selected = Path(ref["path"])
    if surface == "relative":
        ref["path"] = "bash_timeout.mjs"
    elif surface == "symlink":
        target = tmp_path / "selected.mjs"
        selected.rename(target)
        selected.symlink_to(target)
    elif surface == "drift":
        selected.write_text("arbitrary executable code")
    else:
        selected.unlink()
    with pytest.raises((ValueError, OSError)):
        admit(profile, data)


@pytest.mark.parametrize("surface", ["mode", "symlink", "fifo", "relative", "attempt"])
def test_guard_refuses_unusable_audit_file(tmp_path, monkeypatch, surface):
    import os

    profile, data = authored_guard(tmp_path, monkeypatch)
    selected = Path(profile["bash_timeout"]["audit_path"])
    if surface == "mode":
        selected.chmod(0o644)
    elif surface == "symlink":
        target = tmp_path / "actual-audit.jsonl"
        selected.rename(target)
        selected.symlink_to(target)
    elif surface == "fifo":
        selected.unlink()
        os.mkfifo(selected, 0o600)
    elif surface == "relative":
        profile["bash_timeout"]["audit_path"] = "timeout-audit.jsonl"
    else:
        target = Path(data["attempt_root"]) / "audit.jsonl"
        selected.rename(target)
        profile["bash_timeout"]["audit_path"] = str(target)
    with pytest.raises((ValueError, OSError)):
        admit(profile, data)


def test_guard_rechecks_hash_and_audit_before_spawn(tmp_path, monkeypatch):
    profile, data = authored_guard(tmp_path, monkeypatch)
    config = admit(profile, data)
    attempt = config.materialize()
    try:
        selected = Path(profile["bash_timeout"]["extension"]["path"])
        original = selected.read_bytes()
        selected.write_text("changed after admission")
        with pytest.raises(ValueError, match="hash mismatch"):
            config.prespawn(attempt)
        selected.write_bytes(original)
        Path(profile["bash_timeout"]["audit_path"]).chmod(0o644)
        with pytest.raises(ValueError, match="audit"):
            config.prespawn(attempt)
    finally:
        attempt.cleanup()
