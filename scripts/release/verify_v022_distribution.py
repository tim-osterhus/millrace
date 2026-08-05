#!/usr/bin/env python3
"""Read-only verification for the Millrace v0.22 distribution bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path
from typing import Any

EXPECTED_WHEELS = {
    "millforge-0.1.0-py3-none-any.whl": ("millforge", "0.1.0"),
    "millrace_ai-0.22.2-py3-none-any.whl": ("millrace-ai", "0.22.2"),
    "millrace_plus-0.22.2-py3-none-any.whl": ("millrace-plus", "0.22.2"),
    "millrace-0.22.2-py3-none-any.whl": ("millrace", "0.22.2"),
}
EXPECTED_SOURCE_PINS = frozenset(
    {"millforge", "millrace-ai", "millrace-plus", "millrace"}
)
EXPECTED_BUNDLE_REQUIREMENTS = (
    "millforge==0.1.0",
    "millrace-ai==0.22.2",
    "millrace-plus==0.22.2",
)
EXPECTED_WORKFLOW_IDS = frozenset(
    {
        "simple_loop",
        "execution.lad",
        "execution.lad_integrator",
        "planning.lad",
        "lad.full",
        "vendor_selection",
    }
)
EXPECTED_SKILL_IDS = frozenset(
    {
        "millrace-entrypoint-authoring",
        "millrace-instruction-manual",
        "millrace-loop-configuration",
    }
)
RELEASE_SEQUENCE = ("millrace-ai", "millrace-plus", "millrace")
BOOTSTRAP_DISTRIBUTIONS = frozenset({"pip", "setuptools", "wheel"})
SECRET_PATTERN = re.compile(
    r"(?:api[_-]?key|authorization|bearer|credential|password|secret|token)",
    re.IGNORECASE,
)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
MANIFEST_FIELDS = frozenset(
    {"distribution", "filename", "sha256", "size", "version"}
)
COMMAND_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9.-]*")
COMMAND_RESULT_STATES = frozenset({"blocked", "passed", "skipped"})


class VerificationError(RuntimeError):
    """The selected release evidence is incomplete or inconsistent."""


@dataclass(frozen=True)
class ArtifactRecord:
    filename: str
    distribution: str
    version: str
    sha256: str
    size: int
    requires_python: str | None
    requires_dist: tuple[str, ...]

    def as_evidence(self) -> dict[str, object]:
        return {
            "distribution": self.distribution,
            "filename": self.filename,
            "requires_dist": list(self.requires_dist),
            "requires_python": self.requires_python,
            "sha256": self.sha256,
            "size": self.size,
            "version": self.version,
        }


@dataclass(frozen=True)
class ManifestRecord:
    filename: str
    distribution: str
    version: str
    sha256: str
    size: int


@dataclass(frozen=True)
class HashManifest:
    product_wheels: tuple[ManifestRecord, ...]
    resolver_closure: tuple[ManifestRecord, ...]


def normalize_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_assignment(value: str, *, label: str) -> tuple[str, str]:
    key, separator, assigned = value.partition("=")
    if not separator or not key or not assigned:
        raise VerificationError(f"{label} must use NAME=VALUE")
    return key, assigned


def validate_source_pins(values: Sequence[str]) -> dict[str, str]:
    pins: dict[str, str] = {}
    for value in values:
        name, commit = parse_assignment(value, label="source pin")
        normalized = normalize_distribution(name)
        if normalized in pins:
            raise VerificationError(f"duplicate source pin: {normalized}")
        if COMMIT_PATTERN.fullmatch(commit) is None:
            raise VerificationError(f"invalid source commit for {normalized}")
        pins[normalized] = commit
    if pins.keys() != EXPECTED_SOURCE_PINS:
        raise VerificationError(
            f"source pins must be exactly {sorted(EXPECTED_SOURCE_PINS)}"
        )
    return dict(sorted(pins.items()))


def verify_source_roots(
    values: Sequence[str],
    pins: Mapping[str, str],
) -> list[dict[str, object]]:
    roots: dict[str, Path] = {}
    for value in values:
        name, path = parse_assignment(value, label="source root")
        normalized = normalize_distribution(name)
        if normalized in roots:
            raise VerificationError(f"duplicate source root: {normalized}")
        roots[normalized] = Path(path)
    if roots.keys() != pins.keys():
        raise VerificationError("source roots must exactly match source pins")

    evidence: list[dict[str, object]] = []
    for name, root in sorted(roots.items()):
        try:
            head = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            status = subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "status",
                    "--porcelain=v1",
                    "-z",
                    "--untracked-files=all",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError) as exc:
            raise VerificationError(
                f"source root is not a readable Git repo: {name}"
            ) from exc
        if head != pins[name]:
            raise VerificationError(f"source root HEAD does not match pin: {name}")
        dirty = [
            entry
            for entry in status.split("\0")
            if entry and Path(entry[3:]).name != ".DS_Store"
        ]
        if dirty:
            raise VerificationError(f"source root is not clean: {name}")
        evidence.append({"clean": True, "commit": head, "source": name})
    return evidence


def validate_command_results(values: Sequence[str]) -> list[dict[str, str]]:
    if not values:
        raise VerificationError("at least one command result is required")
    results: dict[str, str] = {}
    for value in values:
        name, result = parse_assignment(value, label="command result")
        if COMMAND_NAME_PATTERN.fullmatch(name) is None:
            raise VerificationError(f"invalid command result name: {name}")
        if result not in COMMAND_RESULT_STATES:
            raise VerificationError(f"invalid command result state: {result}")
        if name in results:
            raise VerificationError(f"duplicate command result: {name}")
        results[name] = result
    return [
        {"command": name, "result": result}
        for name, result in sorted(results.items())
    ]


def _manifest_records(value: object, *, section: str) -> tuple[ManifestRecord, ...]:
    if not isinstance(value, list) or not value:
        raise VerificationError(f"{section} must be a nonempty manifest list")
    records: list[ManifestRecord] = []
    for value_record in value:
        if not isinstance(value_record, dict) or set(value_record) != MANIFEST_FIELDS:
            raise VerificationError(f"invalid {section} manifest record")
        filename = value_record["filename"]
        distribution = value_record["distribution"]
        version = value_record["version"]
        sha256 = value_record["sha256"]
        size = value_record["size"]
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not filename.endswith(".whl")
        ):
            raise VerificationError(f"invalid filename in {section} manifest record")
        if (
            not isinstance(distribution, str)
            or not distribution
            or normalize_distribution(distribution) != distribution
        ):
            raise VerificationError(
                f"invalid distribution in {section} manifest record"
            )
        if not isinstance(version, str) or not version or version == "0.0.0":
            raise VerificationError(f"invalid version in {section} manifest record")
        if not isinstance(sha256, str) or SHA256_PATTERN.fullmatch(sha256) is None:
            raise VerificationError(f"invalid SHA-256 in {section} manifest record")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise VerificationError(f"invalid size in {section} manifest record")
        records.append(
            ManifestRecord(
                filename=filename,
                distribution=distribution,
                version=version,
                sha256=sha256,
                size=size,
            )
        )
    if len({record.filename for record in records}) != len(records):
        raise VerificationError(f"duplicate filename in {section} manifest")
    if len({record.distribution for record in records}) != len(records):
        raise VerificationError(f"duplicate distribution in {section} manifest")
    return tuple(sorted(records, key=lambda record: record.filename))


def load_hash_manifest(path: Path) -> HashManifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"hash manifest is unreadable: {path}") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "product_wheels",
        "resolver_closure",
    }:
        raise VerificationError(
            "hash manifest must contain product_wheels and resolver_closure"
        )
    products = _manifest_records(
        payload["product_wheels"],
        section="product_wheels",
    )
    closure = _manifest_records(
        payload["resolver_closure"],
        section="resolver_closure",
    )
    product_identity = {
        record.filename: (record.distribution, record.version) for record in products
    }
    if product_identity != EXPECTED_WHEELS:
        raise VerificationError("product_wheels must name exactly four products")
    reserved = {
        *EXPECTED_SOURCE_PINS,
        *BOOTSTRAP_DISTRIBUTIONS,
    }
    if any(record.distribution in reserved for record in closure):
        raise VerificationError("resolver_closure contains a reserved distribution")
    return HashManifest(product_wheels=products, resolver_closure=closure)


def _wheel_metadata(path: Path) -> tuple[str, str, str | None, tuple[str, ...]]:
    try:
        with zipfile.ZipFile(path) as archive:
            metadata_files = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_files) != 1:
                raise VerificationError(f"{path.name} has invalid METADATA inventory")
            metadata = BytesParser().parsebytes(archive.read(metadata_files[0]))
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise VerificationError(f"wheel is unreadable: {path.name}") from exc
    name = metadata.get("Name")
    version = metadata.get("Version")
    if name is None or version is None:
        raise VerificationError(f"{path.name} lacks Name or Version metadata")
    return (
        normalize_distribution(name),
        version,
        metadata.get("Requires-Python"),
        tuple(metadata.get_all("Requires-Dist", [])),
    )


def _inspect_wheelhouse(
    wheelhouse: Path,
    manifest_records: Sequence[ManifestRecord],
    *,
    label: str,
) -> tuple[ArtifactRecord, ...]:
    if not wheelhouse.is_dir():
        raise VerificationError(f"{label} does not exist: {wheelhouse}")
    actual = {path.name: path for path in wheelhouse.iterdir()}
    declared = {record.filename: record for record in manifest_records}
    if set(actual) != set(declared):
        raise VerificationError(
            f"{label} must contain exactly {sorted(declared)}; "
            f"found {sorted(actual)}"
        )
    records: list[ArtifactRecord] = []
    for filename, expected in declared.items():
        path = actual[filename]
        if path.is_symlink() or not path.is_file():
            raise VerificationError(f"wheel must be a regular file: {filename}")
        size = path.stat().st_size
        if size != expected.size:
            raise VerificationError(f"size mismatch for {filename}")
        digest = sha256_file(path)
        if digest != expected.sha256:
            raise VerificationError(f"SHA-256 mismatch for {filename}")
        name, version, requires_python, requires_dist = _wheel_metadata(path)
        if (name, version) != (expected.distribution, expected.version):
            raise VerificationError(
                f"metadata mismatch for {filename}: {(name, version)}"
            )
        records.append(
            ArtifactRecord(
                filename=filename,
                distribution=name,
                version=version,
                sha256=digest,
                size=size,
                requires_python=requires_python,
                requires_dist=tuple(sorted(requires_dist)),
            )
        )
    return tuple(sorted(records, key=lambda record: record.filename))


def inspect_product_wheelhouse(
    wheelhouse: Path,
    manifest_records: Sequence[ManifestRecord],
) -> tuple[ArtifactRecord, ...]:
    if {record.filename for record in manifest_records} != set(EXPECTED_WHEELS):
        raise VerificationError("product wheelhouse declaration is not exact")
    return _inspect_wheelhouse(
        wheelhouse,
        manifest_records,
        label="product wheelhouse",
    )


def inspect_resolver_closure(
    wheelhouse: Path,
    manifest_records: Sequence[ManifestRecord],
) -> tuple[ArtifactRecord, ...]:
    return _inspect_wheelhouse(
        wheelhouse,
        manifest_records,
        label="resolver closure wheelhouse",
    )


def validate_bundle_metadata(records: Sequence[ArtifactRecord]) -> None:
    by_name = {record.distribution: record for record in records}
    if set(by_name) != EXPECTED_SOURCE_PINS:
        raise VerificationError("product distribution set is not exact")
    meta = by_name["millrace"]
    if (
        meta.requires_python is None
        or meta.requires_python.replace(" ", "") != ">=3.12"
    ):
        raise VerificationError("millrace meta package must require Python >=3.12")
    if set(meta.requires_dist) != set(EXPECTED_BUNDLE_REQUIREMENTS):
        raise VerificationError("millrace meta package dependencies are not exact pins")
    for name in ("millrace-ai", "millrace-plus"):
        if by_name[name].requires_dist:
            raise VerificationError(
                f"{name} unexpectedly declares runtime dependencies"
            )


def build_install_command(
    python: Path,
    product_wheelhouse: Path,
    resolver_closure: Path,
) -> tuple[str, ...]:
    return (
        str(python),
        "-I",
        "-m",
        "pip",
        "install",
        "--no-index",
        "--find-links",
        str(product_wheelhouse.resolve()),
        "--find-links",
        str(resolver_closure.resolve()),
        "--only-binary=:all:",
        "--no-cache-dir",
        "millrace==0.22.2",
    )


def isolated_environment(
    *,
    home: Path,
    executable_dir: Path,
    temp_dir: Path,
) -> dict[str, str]:
    return {
        "HOME": str(home.resolve()),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": str(executable_dir.resolve()),
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_CACHE_DIR": "1",
        "PIP_NO_INDEX": "1",
        "PIP_REQUIRE_VIRTUALENV": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": "",
        "TMPDIR": str(temp_dir.resolve()),
    }


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=dict(env),
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise VerificationError(
            f"command failed ({' '.join(command)}): {detail}"
        ) from exc


def _venv_paths(root: Path) -> tuple[Path, Path]:
    executable_dir = root / ("Scripts" if os.name == "nt" else "bin")
    python = executable_dir / ("python.exe" if os.name == "nt" else "python")
    return executable_dir, python


def _probe_script() -> str:
    return r"""
import importlib.metadata as md
import json
import pathlib
import shutil
import sys
import sysconfig

def normalized(value):
    return value.lower().replace("_", "-").replace(".", "-")

distributions = {
    normalized(dist.metadata["Name"]): dist.version
    for dist in md.distributions()
    if dist.metadata["Name"]
}
package_owners = {
    name: sorted(normalized(owner) for owner in owners)
    for name, owners in md.packages_distributions().items()
    if name in {"millrace", "millforge", "millrace_plus"}
}
entry_points = {}
for dist_name in ("millrace", "millrace-ai", "millrace-plus", "millforge"):
    dist = md.distribution(dist_name)
    entry_points[dist_name] = sorted(
        (
            {"name": ep.name, "group": ep.group, "value": ep.value}
            for ep in dist.entry_points
        ),
        key=lambda row: (row["group"], row["name"], row["value"]),
    )

meta = md.distribution("millrace")
meta_owned = sorted(
    str(path) for path in (meta.files or ())
    if ".dist-info/" not in str(path) and not str(path).endswith(".dist-info")
)
plus = md.distribution("millrace-plus")
manifest_path = plus.locate_file("millrace_workflow_package/manifest.json")
manifest = json.loads(pathlib.Path(manifest_path).read_text(encoding="utf-8"))
workflow_ids = sorted(row["workflow_id"] for row in manifest["workflows"])
skill_prefix = "millrace_plus/skills/"
skill_ids = sorted({
    str(path).removeprefix(skill_prefix).split("/", 1)[0]
    for path in (plus.files or ())
    if str(path).startswith(skill_prefix) and str(path).endswith("/SKILL.md")
})

import millforge
import millrace
descriptor = millforge.describe_millforge_base()
print(json.dumps({
    "cli": shutil.which("millrace"),
    "distributions": distributions,
    "entry_points": entry_points,
    "meta_owned_files": meta_owned,
    "meta_requires": meta.requires,
    "millforge_descriptor": {
        "package_version": descriptor.package_version,
        "runner_id": descriptor.runner_id,
        "runner_version": descriptor.runner_version,
    },
    "millforge_file": millforge.__file__,
    "millrace_file": millrace.__file__,
    "package_owners": package_owners,
    "platform": sysconfig.get_platform(),
    "prefix": sys.prefix,
    "skill_ids": skill_ids,
    "workflow_ids": workflow_ids,
}, sort_keys=True))
"""


def validate_installed_probe(
    probe: Mapping[str, Any],
    environment_root: Path,
    closure_records: Sequence[ArtifactRecord],
) -> None:
    installed = {
        name: version
        for name, version in probe["distributions"].items()
        if name not in BOOTSTRAP_DISTRIBUTIONS
    }
    expected = {
        **{name: version for name, version in EXPECTED_WHEELS.values()},
        **{
            record.distribution: record.version
            for record in closure_records
        },
    }
    if installed != expected:
        raise VerificationError(f"installed distribution set is invalid: {installed}")
    resolved_root = environment_root.resolve()
    if probe["prefix"] != str(resolved_root):
        raise VerificationError("probe escaped the selected virtual environment")
    for field in ("cli", "millforge_file", "millrace_file"):
        value = probe.get(field)
        if not isinstance(value, str) or not Path(value).resolve().is_relative_to(
            resolved_root
        ):
            raise VerificationError(f"{field} is outside the isolated environment")
    if probe["package_owners"].get("millrace") != ["millrace-ai"]:
        raise VerificationError("millrace import is not owned only by millrace-ai")
    if probe["package_owners"].get("millforge") != ["millforge"]:
        raise VerificationError("millforge import is not owned by millforge")
    if probe["package_owners"].get("millrace_plus") != ["millrace-plus"]:
        raise VerificationError("millrace_plus import is not owned by millrace-plus")
    if probe["entry_points"]["millrace"] or probe["meta_owned_files"]:
        raise VerificationError("meta package owns an execution or resource surface")
    ai_entry_points = probe["entry_points"]["millrace-ai"]
    if not any(
        row == {
            "group": "console_scripts",
            "name": "millrace",
            "value": "millrace.adapters.cli.main:cli",
        }
        for row in ai_entry_points
    ):
        raise VerificationError("millrace CLI is not owned by millrace-ai")
    if set(probe["meta_requires"]) != set(EXPECTED_BUNDLE_REQUIREMENTS):
        raise VerificationError("installed meta dependency pins drifted")
    if frozenset(probe["workflow_ids"]) != EXPECTED_WORKFLOW_IDS:
        raise VerificationError("installed Plus workflow inventory drifted")
    if frozenset(probe["skill_ids"]) != EXPECTED_SKILL_IDS:
        raise VerificationError("installed Plus skill inventory drifted")
    descriptor = probe["millforge_descriptor"]
    if descriptor != {
        "package_version": "0.1.0",
        "runner_id": "millforge-base",
        "runner_version": 2,
    }:
        raise VerificationError("installed Millforge descriptor identity drifted")


def _run_json_command(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> dict[str, Any]:
    completed = _run(command, cwd=cwd, env=env)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise VerificationError(f"command returned non-JSON output: {command}") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise VerificationError(f"command did not return success: {command}")
    return payload


def run_installed_cli_smoke(
    *,
    executable_dir: Path,
    workspace: Path,
    environment: Mapping[str, str],
) -> dict[str, object]:
    cli = executable_dir / ("millrace.exe" if os.name == "nt" else "millrace")
    prefix = [str(cli), "--json", "--workspace", str(workspace)]
    package = [*prefix, "package"]
    official = ["millrace.plus.official", "0.22.2"]
    workflow = [
        "--workflow-id",
        "simple_loop",
        "--workflow-version",
        "0.1",
        "--entrypoint",
        "default",
    ]
    archive_path = workspace / "official.mrpkg.tar"
    commands = (
        ("workspace.init", [*prefix, "workspace", "init", "--input-id", "cut-init"]),
        (
            "package.import-installed",
            [
                *package,
                "import-installed",
                "millrace-plus",
                "--resource-root",
                "millrace_workflow_package",
                "--command-id",
                "cut-import",
            ],
        ),
        (
            "package.enable",
            [
                *package,
                "enable",
                *official,
                "--command-id",
                "cut-enable",
            ],
        ),
        (
            "package.verify",
            [
                *package,
                "verify",
                *official,
                *workflow,
                "--command-id",
                "cut-verify",
            ],
        ),
        (
            "package.select-workflow",
            [
                *package,
                "select-workflow",
                *official,
                *workflow,
                "--command-id",
                "cut-select-workflow",
            ],
        ),
        (
            "package.export-archive",
            [
                *package,
                "export-archive",
                *official,
                "--output",
                str(archive_path),
                "--command-id",
                "cut-export",
            ],
        ),
        (
            "plan.admit-package",
            [
                *prefix,
                "plan",
                "admit-package",
                *official,
                *workflow,
                "--command-id",
                "cut-admit",
                "--input-id",
                "cut-admit",
            ],
        ),
    )
    results: dict[str, dict[str, Any]] = {}
    for name, command in commands:
        results[name] = _run_json_command(
            command,
            cwd=workspace.parent,
            env=environment,
        )
    plan = results["plan.admit-package"]["data"]["plan"]
    fingerprint = plan["authority_fingerprint"]
    _run_json_command(
        [
            *prefix,
            "plan",
            "select-default",
            fingerprint,
            "--input-id",
            "cut-select-default",
        ],
        cwd=workspace.parent,
        env=environment,
    )
    status = _run_json_command(
        [*prefix, "status"],
        cwd=workspace.parent,
        env=environment,
    )
    selected = status["data"]["selected_plan"]["authority_fingerprint"]
    if selected != fingerprint:
        raise VerificationError(
            "selected status fingerprint differs from admitted plan"
        )
    if archive_path.is_symlink() or not archive_path.is_file():
        raise VerificationError("package export archive is missing")
    if archive_path.stat().st_size == 0:
        raise VerificationError("package export archive is empty")
    try:
        with tarfile.open(archive_path, "r:*") as archive:
            archive.getmembers()
    except (OSError, tarfile.TarError) as exc:
        raise VerificationError("package export archive is unreadable") from exc
    return {
        "admitted_authority_fingerprint": fingerprint,
        "archive_export": {
            "exists": True,
            "nonempty": True,
            "readable_tar": True,
        },
        "selected_status_fingerprint": selected,
    }


def _snapshot_files(roots: Iterable[Path]) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                snapshot[str(path.resolve())] = sha256_file(path)
            elif path.is_symlink():
                snapshot[str(path.resolve(strict=False))] = (
                    f"symlink:{os.readlink(path)}"
                )
    return snapshot


def install_and_probe_bundle(
    *,
    host_python: Path,
    wheelhouse: Path,
    resolver_closure: Path,
    closure_records: Sequence[ArtifactRecord],
    isolation_root: Path,
    external_skill_roots: Sequence[Path],
) -> dict[str, object]:
    if isolation_root.exists() and any(isolation_root.iterdir()):
        raise VerificationError("isolation root must not contain prior state")
    isolation_root.mkdir(parents=True, exist_ok=True)
    home = isolation_root / "home"
    temp_dir = isolation_root / "tmp"
    environment_root = isolation_root / "environment"
    workspace = isolation_root / "workspace"
    for path in (home, temp_dir):
        path.mkdir()
    before_skills = _snapshot_files(external_skill_roots)
    bootstrap_env = isolated_environment(
        home=home,
        executable_dir=host_python.parent,
        temp_dir=temp_dir,
    )
    bootstrap_env["PIP_REQUIRE_VIRTUALENV"] = "0"
    version = _run(
        [
            str(host_python),
            "-I",
            "-c",
            "import sys; print('.'.join(map(str, sys.version_info[:3])))",
        ],
        cwd=home,
        env=bootstrap_env,
    ).stdout.strip()
    major, minor, _patch = (int(part) for part in version.split("."))
    if (major, minor) < (3, 12):
        raise VerificationError(f"offline bundle proof requires Python 3.12: {version}")
    _run(
        [str(host_python), "-I", "-m", "venv", str(environment_root)],
        cwd=home,
        env=bootstrap_env,
    )
    executable_dir, environment_python = _venv_paths(environment_root)
    env = isolated_environment(
        home=home,
        executable_dir=executable_dir,
        temp_dir=temp_dir,
    )
    _run(
        build_install_command(environment_python, wheelhouse, resolver_closure),
        cwd=home,
        env=env,
    )
    probe_result = _run(
        [str(environment_python), "-I", "-c", _probe_script()],
        cwd=home,
        env=env,
    )
    probe = json.loads(probe_result.stdout)
    validate_installed_probe(probe, environment_root, closure_records)
    smoke = run_installed_cli_smoke(
        executable_dir=executable_dir,
        workspace=workspace,
        environment=env,
    )
    after_skills = _snapshot_files(external_skill_roots)
    if after_skills != before_skills:
        raise VerificationError("bundle installation materialized external skills")
    return {
        "external_skill_materialization": False,
        "installed_distributions": {
            name: version
            for name, version in sorted(probe["distributions"].items())
            if name not in BOOTSTRAP_DISTRIBUTIONS
        },
        "millforge_descriptor": probe["millforge_descriptor"],
        "skill_ids": probe["skill_ids"],
        "smoke": smoke,
        "target": {
            "platform": probe["platform"],
            "python": version,
        },
        "workflow_ids": probe["workflow_ids"],
    }


def compare_downloaded_artifacts(
    *,
    local_dir: Path,
    downloaded_dir: Path,
    manifest: HashManifest,
) -> tuple[ArtifactRecord, ...]:
    local = inspect_product_wheelhouse(local_dir, manifest.product_wheels)
    by_distribution = {record.distribution: record for record in local}
    actual = {path.name for path in downloaded_dir.iterdir()}
    prefix_names = [
        {
            by_distribution[name].filename
            for name in RELEASE_SEQUENCE[:length]
        }
        for length in range(1, len(RELEASE_SEQUENCE) + 1)
    ]
    if actual not in prefix_names:
        raise VerificationError("downloaded artifacts must be an ordered CUT prefix")
    selected = tuple(
        by_distribution[name]
        for name in RELEASE_SEQUENCE
        if by_distribution[name].filename in actual
    )
    for record in selected:
        local_path = local_dir / record.filename
        downloaded_path = downloaded_dir / record.filename
        if (
            downloaded_path.is_symlink()
            or local_path.read_bytes() != downloaded_path.read_bytes()
        ):
            raise VerificationError(
                f"downloaded artifact is not byte-for-byte equal: {record.filename}"
            )
    downloaded = _inspect_wheelhouse(
        downloaded_dir,
        tuple(
            manifest_record
            for manifest_record in manifest.product_wheels
            if manifest_record.filename in actual
        ),
        label="downloaded CUT artifacts",
    )
    selected_evidence = {
        record.distribution: record.as_evidence() for record in selected
    }
    downloaded_evidence = {
        record.distribution: record.as_evidence() for record in downloaded
    }
    if selected_evidence != downloaded_evidence:
        raise VerificationError("downloaded artifact metadata differs from local proof")
    return tuple(
        next(record for record in downloaded if record.distribution == name)
        for name in RELEASE_SEQUENCE[: len(downloaded)]
    )


def next_release_member(
    completed: Sequence[str],
    *,
    previous_step_succeeded: bool,
) -> str | None:
    if not previous_step_succeeded:
        return None
    if tuple(completed) != RELEASE_SEQUENCE[: len(completed)]:
        raise VerificationError("release sequence skipped or reordered a member")
    if len(completed) == len(RELEASE_SEQUENCE):
        return None
    return RELEASE_SEQUENCE[len(completed)]


def release_matrix(completed: Sequence[str]) -> list[dict[str, str]]:
    if tuple(completed) != RELEASE_SEQUENCE[: len(completed)]:
        raise VerificationError("release matrix is not an ordered CUT prefix")
    return [
        {
            "distribution": name,
            "status": "verified" if name in completed else "pending",
        }
        for name in RELEASE_SEQUENCE
    ]


def rollback_decision(failure_phase: str) -> dict[str, object]:
    decisions: dict[str, dict[str, object]] = {
        "before-upload": {
            "advance": False,
            "new_meta_version": False,
            "response": "rebuild-and-reverify",
            "yank": [],
        },
        "after-ai-upload": {
            "advance": False,
            "new_meta_version": True,
            "response": "stop-yank-defective-members",
            "yank": ["millrace-ai"],
        },
        "after-plus-upload": {
            "advance": False,
            "new_meta_version": True,
            "response": "stop-yank-defective-members",
            "yank": ["millrace-ai", "millrace-plus"],
        },
        "after-meta-upload": {
            "advance": False,
            "new_meta_version": True,
            "response": "yank-defective-bundle-and-members",
            "yank": ["millrace-ai", "millrace-plus", "millrace"],
        },
        "index-verification-unknown": {
            "advance": False,
            "new_meta_version": False,
            "response": "retain-evidence-and-retry-verification",
            "yank": [],
        },
        "publisher-identity-mismatch": {
            "advance": False,
            "new_meta_version": True,
            "response": "revoke-binding-and-treat-upload-as-compromised",
            "yank": ["applicable-cut-owned-artifacts"],
        },
        "millforge-identity-mismatch": {
            "advance": False,
            "new_meta_version": False,
            "response": "return-to-mfpkg-before-ai-publication",
            "yank": [],
        },
    }
    try:
        return decisions[failure_phase]
    except KeyError as exc:
        raise VerificationError(f"unknown rollback phase: {failure_phase}") from exc


def rollback_matrix() -> dict[str, dict[str, object]]:
    return {
        phase: rollback_decision(phase)
        for phase in (
            "before-upload",
            "after-ai-upload",
            "after-plus-upload",
            "after-meta-upload",
            "index-verification-unknown",
            "publisher-identity-mismatch",
            "millforge-identity-mismatch",
        )
    }


def compose_bundle(
    *,
    meta_version: str,
    millforge_version: str,
    ai_version: str,
    plus_version: str,
) -> dict[str, str]:
    values = {
        "millforge": millforge_version,
        "millrace-ai": ai_version,
        "millrace-plus": plus_version,
        "millrace": meta_version,
    }
    if any(not value or value == "0.0.0" for value in values.values()):
        raise VerificationError("bundle versions must be explicit release versions")
    return values


def project_version(project_root: Path) -> str:
    with (project_root / "pyproject.toml").open("rb") as handle:
        payload = tomllib.load(handle)
    return str(payload["project"]["version"])


def workflow_accepts_ref(workflow_path: Path, version: str, ref: str) -> bool:
    text = workflow_path.read_text(encoding="utf-8")
    expected_ref = f"refs/tags/v{version}"
    lines = text.splitlines()
    on_indexes = [index for index, line in enumerate(lines) if line == "on:"]
    if len(on_indexes) != 1:
        return False
    start = on_indexes[0]
    trigger_lines: list[tuple[int, str]] = []
    for line in lines[start:]:
        stripped = line.strip()
        indentation = len(line) - len(line.lstrip())
        if trigger_lines and stripped and indentation == 0:
            break
        if stripped and not stripped.startswith("#"):
            trigger_lines.append((indentation, stripped))
    expected_trigger = [
        (0, "on:"),
        (2, "push:"),
        (4, "tags:"),
        (6, f"- v{version}"),
    ]
    expected_guard = (
        "${{ github.event_name == 'push' && "
        f"github.ref == '{expected_ref}'"
        " }}"
    )
    guarded_jobs = [
        line.strip().removeprefix("if:").strip()
        for line in lines
        if line.strip().startswith("if:") and "github.ref" in line
    ]
    return (
        trigger_lines == expected_trigger
        and bool(guarded_jobs)
        and all(guard == expected_guard for guard in guarded_jobs)
        and text.count("github.ref") == len(guarded_jobs)
        and ref == expected_ref
    )


def verify_workflow_inventory(rewrite_root: Path, canonical_root: Path) -> None:
    expected = {"ci.yml", "publish-to-pypi.yml"}
    for root in (rewrite_root, canonical_root):
        workflow_root = root / ".github" / "workflows"
        actual = {path.name for path in workflow_root.iterdir() if path.is_file()}
        if actual != expected:
            raise VerificationError(f"workflow inventory drift at {root}: {actual}")
    for name in expected:
        if (rewrite_root / ".github/workflows" / name).read_bytes() != (
            canonical_root / ".github/workflows" / name
        ).read_bytes():
            raise VerificationError(f"promoted workflow differs: {name}")


def workflow_inventory_status(
    *,
    mode: str,
    rewrite_root: Path | None,
    canonical_root: Path | None,
) -> str:
    if (rewrite_root is None) != (canonical_root is None):
        raise VerificationError("workflow inventory roots must be both or neither")
    if rewrite_root is None:
        return "not-run"
    if mode != "offline-wheelhouse":
        raise VerificationError("workflow inventory proof is offline-only")
    verify_workflow_inventory(rewrite_root, canonical_root)
    return "passed"


def promotion_provenance(
    *,
    rewrite_root: Path | None,
    canonical_root: Path | None,
    old_canonical_commit: str | None,
    reviewed_rewrite_commit: str,
) -> dict[str, object]:
    if rewrite_root is None or canonical_root is None:
        if old_canonical_commit is not None:
            raise VerificationError(
                "old canonical commit requires workflow inventory roots"
            )
        return {"status": "not-run"}
    if (
        old_canonical_commit is None
        or COMMIT_PATTERN.fullmatch(old_canonical_commit) is None
    ):
        raise VerificationError("promotion proof requires an old canonical commit")

    def git_output(root: Path, *arguments: str) -> str:
        try:
            return subprocess.run(
                ["git", "-C", str(root), *arguments],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise VerificationError("promotion Git evidence is unavailable") from exc

    rewrite_commit = git_output(rewrite_root, "rev-parse", "HEAD")
    if rewrite_commit != reviewed_rewrite_commit:
        raise VerificationError("reviewed Rewrite commit does not match source pin")
    rewrite_tree = git_output(rewrite_root, "rev-parse", "HEAD^{tree}")
    canonical_head = git_output(canonical_root, "rev-parse", "HEAD")
    git_output(canonical_root, "cat-file", "-e", f"{old_canonical_commit}^{{commit}}")
    staged_tree = git_output(canonical_root, "write-tree")
    try:
        subprocess.run(
            ["git", "-C", str(canonical_root), "diff", "--quiet"],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise VerificationError(
            "canonical promotion contains unstaged tracked changes"
        ) from exc
    if staged_tree != rewrite_tree:
        raise VerificationError("canonical staged tree differs from reviewed Rewrite")

    promoted_commit: str | None = None
    canonical_head_tree = git_output(canonical_root, "rev-parse", "HEAD^{tree}")
    if canonical_head_tree != staged_tree:
        if canonical_head != old_canonical_commit:
            raise VerificationError(
                "staged pre-commit HEAD is not the old canonical commit"
            )
        status = "staged-pre-commit"
    else:
        if canonical_head == old_canonical_commit:
            raise VerificationError("canonical promotion has not advanced")
        parents = git_output(
            canonical_root,
            "rev-list",
            "--parents",
            "-n",
            "1",
            "HEAD",
        ).split()
        if len(parents) != 2 or parents[1] != old_canonical_commit:
            raise VerificationError(
                "promoted canonical commit does not directly follow old canonical"
            )
        if git_output(
            canonical_root,
            "status",
            "--porcelain=v1",
            "--untracked-files=no",
        ):
            raise VerificationError("promoted canonical commit is not clean")
        promoted_commit = canonical_head
        status = "committed"
    return {
        "old_canonical_commit": old_canonical_commit,
        "promoted_canonical_commit": promoted_commit,
        "promoted_tree_sha": staged_tree,
        "reviewed_rewrite_commit": rewrite_commit,
        "reviewed_rewrite_tree_sha": rewrite_tree,
        "status": status,
        "tracked_tree_equal": True,
    }


def canonical_json_bytes(payload: Mapping[str, object]) -> bytes:
    assert_secret_free(payload)
    return (
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("ascii")


def assert_secret_free(value: object, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if SECRET_PATTERN.search(str(key)):
                raise VerificationError(f"secret-like evidence key at {path}.{key}")
            assert_secret_free(child, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            assert_secret_free(child, path=f"{path}[{index}]")
    elif isinstance(value, str):
        if SECRET_PATTERN.search(value) or "://" in value and "@" in value:
            raise VerificationError(f"secret-like evidence value at {path}")
        if Path(value).is_absolute() or re.match(r"^[A-Za-z]:[\\/]", value):
            raise VerificationError(f"absolute path in evidence at {path}")


def write_evidence(path: Path, payload: Mapping[str, object]) -> str:
    encoded = canonical_json_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)
    return hashlib.sha256(encoded).hexdigest()


def verify(
    *,
    mode: str,
    source_pins: Sequence[str],
    source_roots: Sequence[str],
    command_results: Sequence[str],
    wheelhouse: Path,
    resolver_closure: Path,
    isolation_root: Path,
    hash_manifest: Path,
    evidence_output: Path,
    python: Path,
    local_artifacts: Path | None,
    external_skill_roots: Sequence[Path],
    rewrite_root: Path | None,
    canonical_root: Path | None,
    old_canonical_commit: str | None,
) -> dict[str, object]:
    pins = validate_source_pins(source_pins)
    sources = verify_source_roots(source_roots, pins)
    commands = validate_command_results(command_results)
    manifest = load_hash_manifest(hash_manifest)
    closure_records = inspect_resolver_closure(
        resolver_closure,
        manifest.resolver_closure,
    )
    inventory = workflow_inventory_status(
        mode=mode,
        rewrite_root=rewrite_root,
        canonical_root=canonical_root,
    )
    promotion = promotion_provenance(
        rewrite_root=rewrite_root,
        canonical_root=canonical_root,
        old_canonical_commit=old_canonical_commit,
        reviewed_rewrite_commit=pins["millrace-ai"],
    )
    if mode == "offline-wheelhouse":
        records = inspect_product_wheelhouse(wheelhouse, manifest.product_wheels)
        validate_bundle_metadata(records)
        installed = install_and_probe_bundle(
            host_python=python,
            wheelhouse=wheelhouse,
            resolver_closure=resolver_closure,
            closure_records=closure_records,
            isolation_root=isolation_root,
            external_skill_roots=external_skill_roots,
        )
        target = installed.pop("target")
        completed: tuple[str, ...] = ()
    elif mode == "downloaded-index-artifacts":
        if local_artifacts is None:
            raise VerificationError("downloaded mode requires --local-artifacts")
        local_records = inspect_product_wheelhouse(
            local_artifacts,
            manifest.product_wheels,
        )
        validate_bundle_metadata(local_records)
        records = compare_downloaded_artifacts(
            local_dir=local_artifacts,
            downloaded_dir=wheelhouse,
            manifest=manifest,
        )
        installed = None
        completed = tuple(record.distribution for record in records)
        target_result = _run(
            [
                str(python),
                "-I",
                "-c",
                (
                    "import json,platform,sysconfig;"
                    "print(json.dumps({'platform':sysconfig.get_platform(),"
                    "'python':platform.python_version()},sort_keys=True))"
                ),
            ],
            cwd=Path.cwd(),
            env={
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": str(python.parent.resolve()),
                "PYTHONNOUSERSITE": "1",
                "PYTHONPATH": "",
            },
        )
        target = json.loads(target_result.stdout)
    else:
        raise VerificationError(f"unsupported mode: {mode}")
    evidence: dict[str, object] = {
        "command_results": commands,
        "install": (
            {
                "policy": {
                    "cache": "disabled",
                    "index": "disabled",
                    "requirement": "millrace==0.22.2",
                    "wheel_sources": ["product_wheels", "resolver_closure"],
                },
                "result": installed,
            }
            if installed is not None
            else None
        ),
        "mode": mode,
        "product_wheels": [record.as_evidence() for record in records],
        "promotion": promotion,
        "release_matrix": release_matrix(completed),
        "resolver_closure": [
            record.as_evidence() for record in closure_records
        ],
        "rollback_matrix": rollback_matrix(),
        "schema_version": 2,
        "sources": sources,
        "target": target,
        "workflow_inventory": inventory,
    }
    write_evidence(evidence_output, evidence)
    return evidence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=("offline-wheelhouse", "downloaded-index-artifacts"),
    )
    parser.add_argument("--source-pin", action="append", required=True)
    parser.add_argument("--source-root", action="append", required=True)
    parser.add_argument("--command-result", action="append", required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--resolver-closure", type=Path, required=True)
    parser.add_argument("--isolation-root", type=Path, required=True)
    parser.add_argument("--hash-manifest", type=Path, required=True)
    parser.add_argument("--evidence-output", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--local-artifacts", type=Path)
    parser.add_argument("--external-skill-root", type=Path, action="append", default=[])
    parser.add_argument("--rewrite-root", type=Path)
    parser.add_argument("--canonical-root", type=Path)
    parser.add_argument("--old-canonical-commit")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    external_roots = args.external_skill_root or [
        args.isolation_root / "external-agent-skills"
    ]
    try:
        verify(
            mode=args.mode,
            source_pins=args.source_pin,
            source_roots=args.source_root,
            command_results=args.command_result,
            wheelhouse=args.wheelhouse,
            resolver_closure=args.resolver_closure,
            isolation_root=args.isolation_root,
            hash_manifest=args.hash_manifest,
            evidence_output=args.evidence_output,
            python=args.python,
            local_artifacts=args.local_artifacts,
            external_skill_roots=external_roots,
            rewrite_root=args.rewrite_root,
            canonical_root=args.canonical_root,
            old_canonical_commit=args.old_canonical_commit,
        )
    except VerificationError as exc:
        print(f"CUT-0002 verification refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
