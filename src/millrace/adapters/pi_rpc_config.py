"""Strict, local-only Pi 0.87.0 profile and attempt materialization.

Hashes detect drift; they are not a sandbox or publisher authentication.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from millrace.adapters.pi_rpc_groups import (
    BUNDLE,
    BUNDLE_SHA256,
    GroupProfile,
    bridge_identity,
    supervisor_identity,
)
from millrace.adapters.runner_contract import RedactionPolicy

_BONSAI_SCOPE = "loopback-bonsai2-openai-completions"
_BONSAI_SAMPLING = "bonsai2-pinned-1.0-0.95-20"


def strict_json(raw: str | bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(value: str) -> Any:
        raise ValueError("nonfinite JSON number")

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except RecursionError as exc:
        raise ValueError("JSON nesting bound") from exc

    def walk(item: Any, depth: int = 0) -> None:
        if depth > 64:
            raise ValueError("JSON nesting bound")
        if isinstance(item, float):
            import math

            if not math.isfinite(item):
                raise ValueError("nonfinite JSON number")
        if isinstance(item, dict):
            for child in item.values():
                walk(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                walk(child, depth + 1)

    walk(value)
    return value


def regular_bytes(path: Path, maximum: int = 65536) -> bytes:
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("noncanonical input path")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("input type")
    # NONBLOCK closes the FIFO replacement race before descriptor fstat.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise ValueError("input type or size")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(maximum + 1)
        after = os.fstat(fd)
        if len(data) > maximum or (
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError("input drift")
        return data
    finally:
        os.close(fd)


def _identity(ref: dict[str, Any], maximum: int = 268435456) -> bytes:
    data = regular_bytes(Path(ref["path"]), maximum)
    if hashlib.sha256(data).hexdigest() != ref["sha256"]:
        raise ValueError("input hash mismatch")
    return data


def _validate(schema: dict[str, Any], value: Any) -> None:
    """Only the fixed v1 schema vocabulary below; not a general schema engine."""
    if "const" in schema:
        if json.dumps(value, sort_keys=True) != json.dumps(
            schema["const"], sort_keys=True
        ):
            raise ValueError("profile constant mismatch")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("profile enum mismatch")
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict) or set(value) != set(schema["required"]):
            raise ValueError("profile object keys")
        for name, child in schema["properties"].items():
            _validate(child, value[name])
    elif kind == "array":
        if not isinstance(value, list) or not schema.get("minItems", 0) <= len(
            value
        ) <= schema.get("maxItems", 100):
            raise ValueError("profile array")
        for index, child in enumerate(value):
            _validate(
                schema["prefixItems"][index]
                if "prefixItems" in schema
                else schema["items"],
                child,
            )
    elif kind == "integer":
        if (
            type(value) is not int
            or not schema["minimum"] <= value <= schema["maximum"]
        ):
            raise ValueError("profile integer")
    elif kind == "string":
        if not isinstance(value, str) or not schema.get("minLength", 0) <= len(
            value
        ) <= schema.get("maxLength", 8192):
            raise ValueError("profile string")
        if "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError("profile control character")
        pattern = schema.get("pattern")
        # The frozen absolute-path expression is overescaped. Enforce its intent.
        if pattern and not (
            value.startswith("/")
            if pattern.startswith("^/")
            else re.fullmatch(pattern, value)
        ):
            raise ValueError("profile string pattern")


def _validate_profile(profile: Any) -> None:
    if (
        isinstance(profile, dict)
        and profile.get("schema_version") in {"pi-rpc-profile.v2", "pi-rpc-profile.v3"}
    ):
        legacy = dict(profile)
        accounting = legacy.pop("foreground_accounting", None)
        platform = accounting.get("platform") if isinstance(accounting, dict) else None
        supervised = profile["schema_version"] == "pi-rpc-profile.v3"
        expected = dict(
            kind=(
                "trusted-linux-subreaper-v1"
                if supervised
                else "trusted-posix-group-v1"
            ),
            platform=platform,
            bridge=bridge_identity(),
            protocol="pi-group-observer.v1",
            channel="private-inherited-pipe",
            survivor_policy=(
                "owned-subreaper-disposal" if supervised else "sticky-at-root-exit"
            ),
            escaped_descendants="outside-guarantee",
        )
        if supervised:
            expected["supervisor"] = supervisor_identity()
        if (
            not isinstance(platform, str)
            or platform not in ({"linux"} if supervised else {"darwin", "linux"})
            or accounting != expected
        ):
            raise ValueError(
                "unqualified Linux supervisor profile"
                if supervised
                else "unqualified group observer profile"
            )
        legacy["schema_version"] = "pi-rpc-profile.v1"
        _validate(_PROFILE_SCHEMA, legacy)
    else:
        _validate(_PROFILE_SCHEMA, profile)
    model = profile["model"]
    if profile["qualification_scope"] == _BONSAI_SCOPE:
        if (
            model["provider"] != "local"
            or model["id"] != "bonsai2-27b-pq2"
            or model["context_window"] != 262144
            or model["max_output_tokens"] != 262144
            or model["sampling"] != _BONSAI_SAMPLING
        ):
            raise ValueError("unqualified Bonsai model profile")
    elif model["sampling"] != "no-overrides-provider-default-unqualified":
        raise ValueError("sampling profile/scope mismatch")


@dataclass(frozen=True, repr=False)
class PiRpcConfig:
    data: dict[str, Any]
    profile: dict[str, Any]
    profile_bytes: bytes
    config_path: Path | None = None
    config_bytes: bytes | None = None
    root_identity: tuple[int, int] = field(default=(0, 0))

    @property
    def adapter_id(self) -> str:
        return str(self.data["adapter_id"])

    @property
    def cwd(self) -> Path:
        return Path(self.data["cwd"])

    @property
    def timeout_seconds(self) -> float:
        return float(self.data["timeout_seconds"])

    @property
    def group_profile(self) -> GroupProfile | None:
        if self.profile["schema_version"] not in {
            "pi-rpc-profile.v2",
            "pi-rpc-profile.v3",
        }:
            return None
        accounting = self.profile["foreground_accounting"]
        identity = accounting["bridge"]
        supervised = self.profile["schema_version"] == "pi-rpc-profile.v3"
        supervisor = accounting["supervisor"] if supervised else None
        return GroupProfile(
            Path(identity["path"]),
            identity["sha256"],
            Path(self.profile["installation"]["package_root"]),
            hashlib.sha256(self.profile_bytes).hexdigest(),
            accounting["platform"],
            supervised=supervised,
            supervisor=Path(supervisor["path"]) if supervisor else None,
            supervisor_sha256=supervisor["sha256"] if supervisor else None,
        )

    @property
    def redaction_policy(self) -> RedactionPolicy:
        secret = os.environ.get("PI_RPC_API_KEY", "")
        return RedactionPolicy("pi-rpc-credentials.v1", (secret,) if secret else ())

    @classmethod
    def from_json(cls, value: Any, *, config_path: Path | None = None) -> PiRpcConfig:
        _validate(_LOCAL_SCHEMA, value)
        profile_bytes = _identity(value["profile"], 65536)
        profile = strict_json(profile_bytes)
        _validate_profile(profile)
        root = Path(value["attempt_root"])
        s = root.stat()
        result = cls(
            value,
            profile,
            profile_bytes,
            config_path,
            regular_bytes(config_path) if config_path else None,
            (s.st_dev, s.st_ino),
        )
        result.verify()
        return result

    def verify(self) -> dict[str, bytes]:
        c, p = self.data, self.profile
        _validate(_LOCAL_SCHEMA, c)
        _validate_profile(p)
        if self.config_path and regular_bytes(self.config_path) != self.config_bytes:
            raise ValueError("config drift")
        if _identity(c["profile"], 65536) != self.profile_bytes:
            raise ValueError("profile drift")
        root = Path(c["attempt_root"])
        s = root.stat()
        if (
            root.resolve() != root
            or not root.is_dir()
            or s.st_uid != os.getuid()
            or stat.S_IMODE(s.st_mode) != 0o700
            or (s.st_dev, s.st_ino) != self.root_identity
        ):
            raise ValueError("attempt root identity")
        if strict_json(self.profile_bytes) != p:
            raise ValueError("in-memory profile drift")
        i, m = p["installation"], p["model"]
        for key, expected_hash in _PINNED_SOURCE.items():
            if sys.platform == "linux":
                expected_hash = _LINUX_PINNED_SOURCE.get(key, expected_hash)
            if i[key]["sha256"] != expected_hash:
                raise ValueError("unqualified implementation identity")
        template, package = Path(p["template_dir"]), Path(i["package_root"])
        if self.group_profile is not None:
            if sys.platform != self.group_profile.platform:
                raise ValueError("group observer platform")
            _identity(p["foreground_accounting"]["bridge"])
            if self.group_profile.supervised:
                _identity(p["foreground_accounting"]["supervisor"])
            try:
                _identity({"path": str(package / BUNDLE), "sha256": BUNDLE_SHA256})
            except (OSError, ValueError) as exc:
                raise ValueError("group bundle identity") from exc
        protected = [template, package, self.cwd, Path(c["profile"]["path"])]
        if self.config_path:
            protected.append(self.config_path)
        if any(
            root == path or root.is_relative_to(path) or path.is_relative_to(root)
            for path in protected
        ):
            raise ValueError("overlapping attempt root")
        if not self.cwd.is_dir() or self.cwd.resolve() != self.cwd:
            raise ValueError("cwd identity")
        if template.resolve() != template or {x.name for x in template.iterdir()} != {
            "auth.json",
            "models.json",
            "settings.json",
        }:
            raise ValueError("ambient template contents")
        templates = {}
        for name, ref in p["files"].items():
            if Path(ref["path"]) != template / (name + ".json"):
                raise ValueError("template path mismatch")
            templates[name] = _identity(ref, 65536)
        if (
            strict_json(templates["auth"]) != {}
            or strict_json(templates["settings"]) != p["settings"]
        ):
            raise ValueError("template semantics")
        actual = strict_json(templates["models"])
        model_name = (
            actual.get("providers", {})
            .get(m["provider"], {})
            .get("models", [{}])[0]
            .get("name")
        )
        if not isinstance(model_name, str) or not 1 <= len(model_name) <= 128:
            raise ValueError("model name")
        expected = {
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
                            "name": model_name,
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
                            "thinkingLevelMap": {
                                x: x
                                for x in (
                                    "minimal",
                                    "low",
                                    "medium",
                                    "high",
                                    "xhigh",
                                    "max",
                                )
                            }
                            | {"off": "none"},
                        }
                    ],
                }
            }
        }
        if p["qualification_scope"] == _BONSAI_SCOPE:
            provider = expected["providers"][m["provider"]]
            provider["compat"]["supportsReasoningEffort"] = False
            provider["models"][0]["compat"] = {
                "maxTokensField": "max_tokens",
                "supportsReasoningEffort": False,
                "thinkingFormat": "chat-template",
                "chatTemplateKwargs": {"enable_thinking": {"$var": "thinking.enabled"}},
            }
            provider["models"][0]["samplingParams"] = {
                "temperature": 1.0,
                "top_p": 0.95,
                "top_k": 20,
            }
        if json.dumps(actual, sort_keys=True) != json.dumps(expected, sort_keys=True):
            raise ValueError("models semantics")
        url = urlsplit(m["endpoint"])
        if (
            url.username
            or url.password
            or url.query
            or url.fragment
            or not url.hostname
        ):
            raise ValueError("endpoint credentials or routing")
        if p["qualification_scope"] in {
            "loopback-scripted-openai-completions",
            _BONSAI_SCOPE,
        }:
            if (
                url.scheme != "http"
                or url.hostname != "127.0.0.1"
                or not url.port
                or url.path != "/v1"
            ):
                raise ValueError("loopback endpoint")
        elif url.scheme != "https":
            raise ValueError("remote endpoint")
        if (
            m["max_output_tokens"] > m["context_window"]
            or p["managed_args"][4] != m["provider"]
            or p["managed_args"][6] != m["id"]
        ):
            raise ValueError("model bounds/arguments")
        if (
            c["executable"] != i["node"]["path"]
            or c["argv"] != [i["loader"]["path"]]
            or not os.access(c["executable"], os.X_OK)
        ):
            raise ValueError("executable identity")
        for name in ("max_event_bytes", "max_result_bytes", "max_input_bundle_bytes"):
            if c[name] > c["max_stdout_bytes"]:
                raise ValueError("stream bound relation")
        if c["max_stderr_diagnostic_bytes"] > c["max_stderr_bytes"]:
            raise ValueError("stderr bound relation")
        if any(
            not x or not Path(x).is_absolute() or not Path(x).is_dir()
            for x in c["env_allowlist"]["PATH"].split(":")
        ):
            raise ValueError("PATH")
        for name, relative_name in {
            "loader": "dist/bundle/cli.js",
            "runtime": "dist/bundle/cli-runtime.js",
            "package_json": "package.json",
            "shrinkwrap": "npm-shrinkwrap.json",
        }.items():
            if Path(i[name]["path"]) != package / relative_name:
                raise ValueError("package member identity")
            _identity(i[name])
        _identity(i["node"])
        manifest = strict_json(_identity(i["package_manifest"], 8388608))
        seen = set()
        for entry in manifest["entries"]:
            relative = Path(entry["path"])
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or str(relative) != entry["path"]
                or str(relative) in seen
            ):
                raise ValueError("package manifest path")
            seen.add(str(relative))
            target = package / relative
            if stat.S_IMODE(target.lstat().st_mode) != entry["mode"]:
                raise ValueError("package mode drift")
            if entry["kind"] == "symlink":
                if (
                    not target.is_symlink()
                    or str(target.readlink()) != entry["target"]
                    or not target.resolve().is_relative_to(package)
                ):
                    raise ValueError("package symlink drift")
            elif (
                len(_identity({"path": str(target), "sha256": entry["sha256"]}))
                != entry["size"]
            ):
                raise ValueError("package size drift")
        if {
            str(x.relative_to(package))
            for x in package.rglob("*")
            if x.is_file() or x.is_symlink()
        } != seen:
            raise ValueError("package inventory drift")
        return templates

    def probe_versions(self, attempt: PiAttempt, deadline: float) -> None:
        # Pinned Node --version and Pi's early --version branch were source
        # audited: neither resolves packages, opens providers nor invokes VCS.
        for argv, expected in (
            ([self.data["executable"], "--version"], "v24.18.0"),
            ([self.data["executable"], *self.data["argv"], "--version"], "0.87.0"),
        ):
            remaining = min(2.0, deadline - time.monotonic())
            if remaining <= 0:
                raise ValueError("version deadline")
            try:
                result = subprocess.run(
                    argv,
                    cwd=self.cwd,
                    env=attempt.env,
                    capture_output=True,
                    timeout=remaining,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ValueError("version deadline") from exc
            if (
                result.returncode != 0
                or result.stdout.strip() != expected.encode()
                or result.stderr
            ):
                raise ValueError("version probe mismatch")

    def materialize(self) -> PiAttempt:
        templates = self.verify()
        credential = os.environ.get("PI_RPC_API_KEY", "")
        if not credential or len(credential.encode()) > 4096 or "\x00" in credential:
            raise ValueError("missing or invalid credential")
        path = Path(tempfile.mkdtemp(prefix="attempt-", dir=self.data["attempt_root"]))
        s = path.stat()
        attempt = PiAttempt(path, (s.st_dev, s.st_ino), {})
        try:
            for name in ("home", "agent", "sessions", "tmp"):
                (path / name).mkdir(mode=0o700)
            for name, data in templates.items():
                fd = os.open(
                    path / "agent" / (name + ".json"),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                    0o600,
                )
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
            attempt.env.update(self.data["env_allowlist"])
            attempt.env.update(
                HOME=str(path / "home"),
                TMPDIR=str(path / "tmp"),
                PI_CODING_AGENT_DIR=str(path / "agent"),
                PI_CODING_AGENT_SESSION_DIR=str(path / "sessions"),
                PI_RPC_API_KEY=credential,
            )
            self.prespawn(attempt)
            return attempt
        except BaseException:
            attempt.cleanup()
            raise

    def prespawn(self, attempt: PiAttempt) -> None:
        templates = self.verify()
        expected_env = dict(self.data["env_allowlist"])
        expected_env.update(
            HOME=str(attempt.path / "home"),
            TMPDIR=str(attempt.path / "tmp"),
            PI_CODING_AGENT_DIR=str(attempt.path / "agent"),
            PI_CODING_AGENT_SESSION_DIR=str(attempt.path / "sessions"),
            PI_RPC_API_KEY=os.environ.get("PI_RPC_API_KEY", ""),
        )
        if attempt.env != expected_env:
            raise ValueError("attempt environment drift")
        attempt.owned()
        if {p.name for p in attempt.path.iterdir()} != {
            "home",
            "agent",
            "sessions",
            "tmp",
        }:
            raise ValueError("attempt inventory")
        for name in ("home", "sessions", "tmp"):
            p = attempt.path / name
            if p.is_symlink() or not p.is_dir() or list(p.iterdir()):
                raise ValueError("attempt ambient contents")
        agent = attempt.path / "agent"
        if agent.is_symlink() or {p.name for p in agent.iterdir()} != {
            "auth.json",
            "settings.json",
            "models.json",
        }:
            raise ValueError("attempt agent inventory")
        for name, data in templates.items():
            if regular_bytes(agent / (name + ".json")) != data:
                raise ValueError("attempt template drift")


@dataclass(repr=False)
class PiAttempt:
    path: Path
    identity: tuple[int, int]
    env: dict[str, str]
    removed: bool = False

    def owned(self) -> None:
        s = self.path.lstat()
        if (
            self.path.is_symlink()
            or (s.st_dev, s.st_ino) != self.identity
            or s.st_uid != os.getuid()
        ):
            raise ValueError("attempt ownership lost")

    def cleanup(self) -> None:
        if not self.removed:
            try:
                self.owned()
            except FileNotFoundError:
                # Only this recorded exclusive child is already absent.
                pass
            else:
                shutil.rmtree(self.path)
            self.removed = True
            self.env.clear()


_PROFILE_SCHEMA: dict[str, Any] = {
    "properties": {
        "adapter_kind": {"const": "pi_rpc"},
        "credential_env_names": {"const": ["PI_RPC_API_KEY"]},
        "discovery": {
            "const": {
                "context_files": False,
                "extensions": False,
                "packages": [],
                "project_trust": False,
                "prompt_templates": False,
                "skills": False,
                "system_prompt_files": "absent",
                "themes": False,
            }
        },
        "files": {
            "properties": {
                "auth": {
                    "properties": {
                        "path": {
                            "maxLength": 4096,
                            "minLength": 2,
                            "pattern": "^/[^\\u0000\\r\\n]+$",
                            "type": "string",
                        },
                        "sha256": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
                    },
                    "required": ["path", "sha256"],
                    "type": "object",
                },
                "models": {
                    "properties": {
                        "path": {
                            "maxLength": 4096,
                            "minLength": 2,
                            "pattern": "^/[^\\u0000\\r\\n]+$",
                            "type": "string",
                        },
                        "sha256": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
                    },
                    "required": ["path", "sha256"],
                    "type": "object",
                },
                "settings": {
                    "properties": {
                        "path": {
                            "maxLength": 4096,
                            "minLength": 2,
                            "pattern": "^/[^\\u0000\\r\\n]+$",
                            "type": "string",
                        },
                        "sha256": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
                    },
                    "required": ["path", "sha256"],
                    "type": "object",
                },
            },
            "required": ["settings", "models", "auth"],
            "type": "object",
        },
        "installation": {
            "properties": {
                "archive_sha256": {
                    "const": (
                        "9a6733c0e6a31d592b53dc60df43dd0c26"
                        "fa793ccf2384ed123fc17b0448866a"
                    )
                },
                "loader": {
                    "properties": {
                        "path": {
                            "maxLength": 4096,
                            "minLength": 2,
                            "pattern": "^/[^\\u0000\\r\\n]+$",
                            "type": "string",
                        },
                        "sha256": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
                    },
                    "required": ["path", "sha256"],
                    "type": "object",
                },
                "node": {
                    "properties": {
                        "path": {
                            "maxLength": 4096,
                            "minLength": 2,
                            "pattern": "^/[^\\u0000\\r\\n]+$",
                            "type": "string",
                        },
                        "sha256": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
                    },
                    "required": ["path", "sha256"],
                    "type": "object",
                },
                "node_origin": {"const": "existing-host-read-only"},
                "node_version": {"const": "v24.18.0"},
                "package_json": {
                    "properties": {
                        "path": {
                            "maxLength": 4096,
                            "minLength": 2,
                            "pattern": "^/[^\\u0000\\r\\n]+$",
                            "type": "string",
                        },
                        "sha256": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
                    },
                    "required": ["path", "sha256"],
                    "type": "object",
                },
                "package_manifest": {
                    "properties": {
                        "path": {
                            "maxLength": 4096,
                            "minLength": 2,
                            "pattern": "^/[^\\u0000\\r\\n]+$",
                            "type": "string",
                        },
                        "sha256": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
                    },
                    "required": ["path", "sha256"],
                    "type": "object",
                },
                "package_name": {"const": "@earendil-works/pi-coding-agent"},
                "package_root": {
                    "maxLength": 4096,
                    "minLength": 2,
                    "pattern": "^/[^\\u0000\\r\\n]+$",
                    "type": "string",
                },
                "package_version": {"const": "0.87.0"},
                "runtime": {
                    "properties": {
                        "path": {
                            "maxLength": 4096,
                            "minLength": 2,
                            "pattern": "^/[^\\u0000\\r\\n]+$",
                            "type": "string",
                        },
                        "sha256": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
                    },
                    "required": ["path", "sha256"],
                    "type": "object",
                },
                "shrinkwrap": {
                    "properties": {
                        "path": {
                            "maxLength": 4096,
                            "minLength": 2,
                            "pattern": "^/[^\\u0000\\r\\n]+$",
                            "type": "string",
                        },
                        "sha256": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
                    },
                    "required": ["path", "sha256"],
                    "type": "object",
                },
            },
            "required": [
                "package_name",
                "package_version",
                "archive_sha256",
                "package_root",
                "package_manifest",
                "loader",
                "runtime",
                "package_json",
                "shrinkwrap",
                "node_version",
                "node",
                "node_origin",
            ],
            "type": "object",
        },
        "managed_args": {
            "items": False,
            "maxItems": 17,
            "minItems": 17,
            "prefixItems": [
                {"const": "--mode"},
                {"const": "rpc"},
                {"const": "--no-session"},
                {"const": "--provider"},
                {
                    "maxLength": 128,
                    "minLength": 1,
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9._/-]*$",
                    "type": "string",
                },
                {"const": "--model"},
                {
                    "maxLength": 128,
                    "minLength": 1,
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9._/-]*$",
                    "type": "string",
                },
                {"const": "--thinking"},
                {"const": "xhigh"},
                {"const": "--no-context-files"},
                {"const": "--no-skills"},
                {"const": "--no-extensions"},
                {"const": "--no-prompt-templates"},
                {"const": "--no-themes"},
                {"const": "--no-approve"},
                {"const": "--tools"},
                {"const": "read,bash,edit,write"},
            ],
            "type": "array",
        },
        "model": {
            "properties": {
                "api": {"const": "openai-completions"},
                "context_window": {
                    "maximum": 2097152,
                    "minimum": 1024,
                    "type": "integer",
                },
                "endpoint": {
                    "maxLength": 2048,
                    "pattern": "^https?://[^\\s]+$",
                    "type": "string",
                },
                "id": {
                    "maxLength": 128,
                    "minLength": 1,
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9._/-]*$",
                    "type": "string",
                },
                "input": {"const": ["text"]},
                "max_output_tokens": {
                    "maximum": 262144,
                    "minimum": 1,
                    "type": "integer",
                },
                "provider": {
                    "maxLength": 128,
                    "minLength": 1,
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9._/-]*$",
                    "type": "string",
                },
                "sampling": {
                    "enum": [
                        "no-overrides-provider-default-unqualified",
                        _BONSAI_SAMPLING,
                    ]
                },
                "thinking": {"const": "xhigh"},
            },
            "required": [
                "provider",
                "id",
                "api",
                "endpoint",
                "thinking",
                "context_window",
                "max_output_tokens",
                "input",
                "sampling",
            ],
            "type": "object",
        },
        "persistence": {"const": "in-memory-no-continuation"},
        "protocol": {"const": "pi-0.87.0-jsonl.v1"},
        "qualification_scope": {
            "enum": [
                "loopback-scripted-openai-completions",
                _BONSAI_SCOPE,
                "unqualified-openai-completions",
            ]
        },
        "schema_version": {"const": "pi-rpc-profile.v1"},
        "settings": {
            "const": {
                "cacheWarming": "off",
                "compaction": {
                    "enabled": True,
                    "keepRecentTokens": 20000,
                    "reserveTokens": 16384,
                },
                "defaultProjectTrust": "never",
                "defaultTools": ["read", "bash", "edit", "write"],
                "enableAnalytics": False,
                "enableInstallTelemetry": False,
                "extensions": [],
                "httpIdleTimeoutMs": 300000,
                "packages": [],
                "prompts": [],
                "retry": {
                    "baseDelayMs": 2000,
                    "enabled": True,
                    "maxAgentDelayMs": 60000,
                    "maxRetries": 3,
                    "provider": {
                        "maxRetries": 0,
                        "maxRetryDelayMs": 60000,
                        "timeoutMs": 300000,
                    },
                },
                "shellPath": "/bin/bash",
                "skills": [],
                "themes": [],
                "transport": "auto",
            }
        },
        "template_dir": {
            "maxLength": 4096,
            "minLength": 2,
            "pattern": "^/[^\\u0000\\r\\n]+$",
            "type": "string",
        },
        "tool_boundary": {"const": "unrestricted-no-sandbox-no-tool-approval"},
        "tools": {"const": ["read", "bash", "edit", "write"]},
    },
    "required": [
        "schema_version",
        "adapter_kind",
        "qualification_scope",
        "protocol",
        "installation",
        "template_dir",
        "files",
        "managed_args",
        "model",
        "settings",
        "discovery",
        "persistence",
        "tools",
        "tool_boundary",
        "credential_env_names",
    ],
    "type": "object",
}


_LOCAL_SCHEMA: dict[str, Any] = {
    "properties": {
        "adapter_id": {
            "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
            "type": "string",
        },
        "argv": {
            "items": {
                "maxLength": 4096,
                "minLength": 2,
                "pattern": "^/[^\\u0000\\r\\n]+$",
                "type": "string",
            },
            "maxItems": 1,
            "minItems": 1,
            "type": "array",
        },
        "attempt_root": {
            "maxLength": 4096,
            "minLength": 2,
            "pattern": "^/[^\\u0000\\r\\n]+$",
            "type": "string",
        },
        "credential_env_names": {"const": ["PI_RPC_API_KEY"]},
        "cwd": {
            "maxLength": 4096,
            "minLength": 2,
            "pattern": "^/[^\\u0000\\r\\n]+$",
            "type": "string",
        },
        "env_allowlist": {
            "properties": {
                "LANG": {"const": "C"},
                "LC_ALL": {"const": "C"},
                "NODE_DISABLE_COMPILE_CACHE": {"const": "1"},
                "NO_COLOR": {"const": "1"},
                "PATH": {
                    "maxLength": 8192,
                    "minLength": 2,
                    "pattern": "^/[^\\u0000\\r\\n]+$",
                    "type": "string",
                },
                "PI_OFFLINE": {"const": "1"},
                "PI_SKIP_VERSION_CHECK": {"const": "1"},
                "SHELL": {"const": "/bin/bash"},
                "TERM": {"const": "dumb"},
            },
            "required": [
                "PATH",
                "PI_OFFLINE",
                "PI_SKIP_VERSION_CHECK",
                "NO_COLOR",
                "TERM",
                "SHELL",
                "LANG",
                "LC_ALL",
                "NODE_DISABLE_COMPILE_CACHE",
            ],
            "type": "object",
        },
        "executable": {
            "maxLength": 4096,
            "minLength": 2,
            "pattern": "^/[^\\u0000\\r\\n]+$",
            "type": "string",
        },
        "max_event_bytes": {"maximum": 4194304, "minimum": 1024, "type": "integer"},
        "max_event_count": {"maximum": 500000, "minimum": 1, "type": "integer"},
        "max_input_bundle_bytes": {
            "maximum": 1048576,
            "minimum": 1024,
            "type": "integer",
        },
        "max_result_bytes": {"maximum": 1048576, "minimum": 128, "type": "integer"},
        "max_stderr_bytes": {"maximum": 4194304, "minimum": 1024, "type": "integer"},
        "max_stderr_diagnostic_bytes": {
            "maximum": 65536,
            "minimum": 0,
            "type": "integer",
        },
        "max_stdout_bytes": {"maximum": 268435456, "minimum": 1024, "type": "integer"},
        "profile": {
            "properties": {
                "path": {
                    "maxLength": 4096,
                    "minLength": 2,
                    "pattern": "^/[^\\u0000\\r\\n]+$",
                    "type": "string",
                },
                "sha256": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
            },
            "required": ["path", "sha256"],
            "type": "object",
        },
        "redaction_policy": {
            "const": {"redact_credentials": True, "retain_raw_events": False}
        },
        "schema_version": {"const": "pi-rpc-local-config.v1"},
        "timeout_seconds": {"maximum": 9600, "minimum": 1, "type": "integer"},
    },
    "required": [
        "schema_version",
        "adapter_id",
        "cwd",
        "executable",
        "argv",
        "env_allowlist",
        "credential_env_names",
        "profile",
        "timeout_seconds",
        "max_input_bundle_bytes",
        "max_event_bytes",
        "max_stdout_bytes",
        "max_event_count",
        "max_result_bytes",
        "max_stderr_bytes",
        "max_stderr_diagnostic_bytes",
        "redaction_policy",
        "attempt_root",
    ],
    "type": "object",
}


# Exact implementations underpinning the bounded text-tool cleanup proof.
# An operator-written replacement manifest cannot qualify different code.
_PINNED_SOURCE = {
    "node": "ee6fb0e015284d83a91e8ec5213f43a157f8a392b58555301682892ba928c04a",
    "loader": "e79626f2dd6f94aa45d30f3fa63cd84319a6eefcd150b353cfaf274366926774",
    "runtime": "afaec240d4a30b956abdb2540500176c2da469993c859143b8e61a0c3773ccf6",
    "package_json": "9bb655451e850a8593ba87f563c26d3d2af2f503b76318e2f1bbe53eb28c9180",
    "shrinkwrap": "3b5bdc0a158f6440d0836faa0953bece98b0bc1771407b64d127260744b891da",
    "package_manifest": (
        "4166903ec074bff90682dfb3cde9c5ddb68956723f1431fcabacdd599bfb8fa2"
    ),
}


# Exact bytes of the qualified Ubuntu 24.04 x86_64 Node/Pi installation.
_LINUX_PINNED_SOURCE = {
    "node": "41a74efb34cbde5c7632cdac0cf8bd1a14d0b8d73dc1e82755014d9a9ce70f5c",
    "shrinkwrap": "e4ee9f9aa362ff8e3608e5f07db14cf3887be10638c5c73d8a1c9afde0d0af42",
    "package_manifest": (
        "d2870c5b458491bdf600f0843de6a4a444dd7cd39f5f94d06c06438dd01a6eed"
    ),
}
