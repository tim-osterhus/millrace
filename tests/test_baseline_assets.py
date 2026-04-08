from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path
from pathlib import PurePosixPath

import pytest

from millrace_engine.baseline_assets import (
    iter_packaged_baseline_directories,
    iter_packaged_baseline_files,
    load_packaged_baseline_manifest,
    packaged_baseline_asset,
    packaged_baseline_bundle_version,
)


MILLRACE_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = MILLRACE_ROOT / "millrace_engine" / "assets"
TRANSIENT_SEGMENTS = {"__pycache__", ".pytest_cache", ".mypy_cache", "build", "dist"}
TRANSIENT_SUFFIXES = {".pyc", ".pyo"}
REPO_LOCAL_MARKERS = (
    "/Users/timinator/Desktop/Millrace-2.0",
    "/Users/timinator/Desktop/Millrace-2.0/millrace",
)
REQUIRED_BUNDLE_PATHS = (
    "README.md",
    "docs/RUNTIME_DEEP_DIVE.md",
    "ADVISOR.md",
    "SUPERVISOR.md",
    "OPERATOR_GUIDE.md",
    "docs/TUI_DOCUMENTATION.md",
    "millrace.toml",
    "agents/_goal_intake.md",
    "agents/_objective_profile_sync.md",
    "agents/_spec_synthesis.md",
    "agents/_spec_interview.md",
    "agents/_spec_review.md",
    "agents/_taskmaster.md",
    "agents/_taskaudit.md",
    "agents/_start_large_plan.md",
    "agents/_start_large_execute.md",
    "agents/prompts/builder_cycle.md",
    "agents/prompts/qa_execute_cycle.md",
    "agents/roles/research-router.md",
    "agents/roles/research-phase-designer.md",
    "agents/roles/qa-test-engineer.md",
    "agents/skills/spec-writing-research-core/SKILL.md",
    "agents/skills/playwright-ui-verification/SKILL.md",
    "agents/skills/millrace-operator-intake-control/SKILL.md",
    "agents/skills/millrace-operator-intake-control/EXAMPLES.md",
    "agents/objective/contract.yaml",
    "agents/objective/contract.schema.json",
    "agents/audit/completion_manifest.json",
    "agents/audit/strict_contract.json",
    "agents/specs/templates/phase_spec_template.md",
    "agents/specs/templates/audit_template.md",
    "agents/specs/governance/decision_log_schema.json",
)
RUNTIME_DOC_PATHS = (
    "README.md",
    "ADVISOR.md",
    "SUPERVISOR.md",
    "OPERATOR_GUIDE.md",
    "docs/RUNTIME_DEEP_DIVE.md",
    "docs/TUI_DOCUMENTATION.md",
)
STALE_RUN06_DOC_MARKERS = (
    "workspace-local override resolution and precedence are not yet implemented",
    "missing workspace files are not yet scaffolded from the packaged bundle",
    "this run does not yet scaffold missing files or resolve workspace-local overrides over packaged defaults",
)
REQUIRED_RUNTIME_DOC_MARKERS = {
    "README.md": (
        "autonomous",
        "## How Millrace Is Different",
        "## Design Philosophy",
        "## Initialized Workspace Layout",
        "## Governed Compounding Model",
        "raw -> compiled -> query -> lint",
        "packaged `agents/skills` are the shipped operator/agent guidance surface",
        "compounding orient --query builder",
        "compounding lint",
        "Derived orientation surface only; governed compounding artifacts remain the source of truth.",
        "goal_intake -> objective_profile_sync -> completion_manifest_draft -> spec_synthesis",
        "semantic_profile_seed.json`, `.yaml`, or `.yml`",
        "## External Supervisor Workflow",
        "they are not expected at the public repo root",
        "a Python CLI and a Textual TUI",
        "OpenClaw-style supervisor agents",
        "python3 -m millrace_engine.tui --config /absolute/path/to/workspace/millrace.toml",
        "supervisor report --json",
        'supervisor cleanup remove <task-id> --issuer <name> --reason "Invalid queued work" --json',
        "Scheduling, messaging, wakeups, and multi-workspace registry stay outside Millrace core.",
        "OPERATOR_GUIDE.md",
    ),
    "ADVISOR.md": (
        "This file is for agents acting as the operator shell",
        "This prompt assumes you are operating inside an initialized Millrace workspace",
        "agents/skills/millrace-operator-intake-control/SKILL.md",
        "## Supported Local Workflow",
        "Start with CLI JSON inspection when the runtime state is unknown",
        "Use the TUI when you want an interactive local control shell",
        "do not tell Millrace to run GoalSpec, Spec Review, Taskmaster, audit, or other internal stages",
        'queue cleanup remove <task-id> --reason "Invalid duplicate task"',
        "### Workspace Setup",
        "### External Supervisor",
        "health --json",
        "OpenClaw Supervisor agent",
        "supervisor report --json",
        "supervisor pause --issuer <name>",
        "supervisor cleanup remove",
        "Do not write `agents/.runtime/commands/incoming/`",
        "run-provenance <run-id> --json",
        "config show --json",
    ),
    "SUPERVISOR.md": (
        "This file is for agents acting as the external one-workspace supervisor",
        "agents/skills/millrace-operator-intake-control/SKILL.md",
        "supervisor report --json",
        "`attention_reason`, `attention_summary`, and `allowed_actions`",
        "poll frequency, heartbeat strategy, and wakeup delivery",
        "supervisor pause --issuer <name> --json",
        "supervisor add-task \"Example task\" --issuer <name> --json",
        "supervisor cleanup remove <task-id> --issuer <name>",
        "Use `ADVISOR.md` instead",
    ),
    "OPERATOR_GUIDE.md": (
        "## Governed Compounding Operating Model",
        "raw -> compiled -> query -> lint",
        "governed compounding authority lives in typed artifacts under `agents/compounding/`",
        "compounding orient --query builder",
        "compounding lint",
        "Derived orientation surface only; governed compounding artifacts remain the source of truth.",
        "## External Supervisor Workflow",
        "## TUI Workflow",
        "OpenClaw or another external supervisor harness",
        "python3 -m millrace_engine.tui --config millrace.toml",
        "goal_intake -> objective_profile_sync -> completion_manifest_draft -> spec_synthesis",
        "semantic_profile_seed.json`, `.yaml`, or `.yml`",
        "`attention_reason`, `attention_summary`, and `allowed_actions`",
        "supervisor add-task \"Example task\" --issuer <name>",
        "supervisor cleanup remove <task-id> --issuer <name>",
        "Mailbox files remain runtime-owned.",
        "mailbox-safe daemon mutation rules",
    ),
    "docs/RUNTIME_DEEP_DIVE.md": (
        "### 5.7 Governed Compounding Operating Model",
        "raw -> compiled -> query -> lint",
        "`procedures/` for reusable procedure artifacts",
        "`millrace compounding orient` derives `agents/compounding/indexes/governed_store_index.json`",
        "`compounding.integrity` workspace check",
        "Derived orientation surface only; governed compounding artifacts remain the source of truth.",
        "### 22.2 TUI Surface",
        "### 22.3 External Supervisor Surface",
        "OpenClaw or another external supervisor harness",
        "millrace_engine/tui/",
        "The TUI is an operator shell, not a second runtime engine.",
        "### 17.3A GoalSpec Staged Contract",
        "goal_intake -> objective_profile_sync -> completion_manifest_draft -> spec_synthesis",
        "semantic_profile_seed.yaml",
        "Taskmaster emits product-first per-spec shards",
        "`attention_reason`, `attention_summary`, and `allowed_actions`",
        "supervisor cleanup remove|quarantine",
        "multi-workspace portfolio logic stay outside the core runtime",
    ),
    "docs/TUI_DOCUMENTATION.md": (
        "## External Supervisor Boundary",
        "OpenClaw or another external supervisor harness",
        "supervisor report --json",
        "supervisor cleanup remove <task-id> --issuer <name>",
        "it is not the remote harness interface",
    ),
}


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _has_transient_artifact(path: PurePosixPath) -> bool:
    return any(part in TRANSIENT_SEGMENTS for part in path.parts) or path.suffix in TRANSIENT_SUFFIXES


def test_packaged_baseline_manifest_exposes_stable_bundle_version() -> None:
    manifest = load_packaged_baseline_manifest()

    assert manifest["bundle_version"] == "baseline-bundle-v1"
    assert packaged_baseline_bundle_version() == "baseline-bundle-v1"
    assert manifest["schema_version"] == 1
    assert manifest["source_roots"] == {
        "reference": "ref-framework/millrace-temp-main",
        "runtime": "millrace",
    }


def test_packaged_baseline_manifest_entries_resolve_with_helper() -> None:
    directory_entries = iter_packaged_baseline_directories()
    file_entries = iter_packaged_baseline_files()

    assert directory_entries
    assert file_entries
    assert len({entry["path"] for entry in directory_entries}) == len(directory_entries)
    assert len({entry["path"] for entry in file_entries}) == len(file_entries)

    for entry in directory_entries:
        path = PurePosixPath(entry["path"])
        assert not path.is_absolute(), entry["path"]
        assert not any(part in {"", ".", ".."} for part in path.parts), entry["path"]
        asset = packaged_baseline_asset(entry["path"])
        if entry["path"].startswith("agents/registry"):
            assert entry["family"] == "registry"
            assert not asset.exists(), entry["path"]
        else:
            assert asset.is_dir(), entry["path"]

    for entry in file_entries:
        path = PurePosixPath(entry["path"])
        assert not path.is_absolute(), entry["path"]
        assert not any(part in {"", ".", ".."} for part in path.parts), entry["path"]

        asset = packaged_baseline_asset(entry["path"])
        payload = asset.read_bytes()

        assert asset.is_file(), entry["path"]
        assert _sha256_bytes(payload) == entry["sha256"], entry["path"]
        assert len(payload) == entry["size_bytes"], entry["path"]


def test_packaged_baseline_includes_required_bundle_families() -> None:
    bundled_paths = {entry["path"] for entry in iter_packaged_baseline_files()}

    for relative_path in REQUIRED_BUNDLE_PATHS:
        assert relative_path in bundled_paths
        assert packaged_baseline_asset(relative_path).is_file(), relative_path


def test_packaged_asset_tree_excludes_transient_artifacts() -> None:
    pyproject = tomllib.loads((MILLRACE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data_patterns = pyproject["tool"]["setuptools"]["package-data"]["millrace_engine.assets"]
    packaged_asset_paths = {
        path.relative_to(ASSETS_ROOT).as_posix()
        for pattern in package_data_patterns
        for path in ASSETS_ROOT.glob(pattern)
        if path.is_file()
    }
    manifest_transient_paths = [
        entry["path"]
        for entry in iter_packaged_baseline_files()
        if _has_transient_artifact(PurePosixPath(entry["path"]))
    ]
    packaged_transient_paths = [
        relative_path
        for relative_path in packaged_asset_paths
        if _has_transient_artifact(PurePosixPath(relative_path))
    ]
    manifest_paths = {entry["path"] for entry in iter_packaged_baseline_files()} | {"manifest.json"}
    registry_paths = {
        path.relative_to(ASSETS_ROOT).as_posix()
        for path in (ASSETS_ROOT / "registry").rglob("*")
        if path.is_file()
    }

    assert packaged_asset_paths == manifest_paths | registry_paths
    assert not packaged_transient_paths
    assert not manifest_transient_paths


def test_packaged_registry_defaults_stay_separate_from_workspace_scaffold() -> None:
    manifest_directory_paths = {entry["path"] for entry in iter_packaged_baseline_directories()}
    manifest_file_paths = {entry["path"] for entry in iter_packaged_baseline_files()}
    packaged_registry_files = {
        path.relative_to(ASSETS_ROOT).as_posix()
        for path in (ASSETS_ROOT / "registry").rglob("*")
        if path.is_file()
    }

    assert (ASSETS_ROOT / "registry").is_dir()
    assert packaged_registry_files
    assert not (ASSETS_ROOT / "agents" / "registry").exists()
    assert not any(path.startswith("agents/registry/") for path in manifest_file_paths)
    assert {
        "agents/registry",
        "agents/registry/stages",
        "agents/registry/loops/execution",
        "agents/registry/loops/research",
        "agents/registry/modes",
        "agents/registry/task_authoring",
        "agents/registry/model_profiles",
    } <= manifest_directory_paths


def test_packaged_markdown_assets_do_not_embed_repo_local_paths() -> None:
    for path in sorted(ASSETS_ROOT.rglob("*.md")):
        contents = path.read_text(encoding="utf-8")
        for marker in REPO_LOCAL_MARKERS:
            assert marker not in contents, path.relative_to(ASSETS_ROOT).as_posix()


def test_packaged_runtime_docs_reflect_current_resolver_behavior() -> None:
    for relative_path in RUNTIME_DOC_PATHS:
        contents = packaged_baseline_asset(relative_path).read_text(encoding="utf-8")
        for marker in STALE_RUN06_DOC_MARKERS:
            assert marker not in contents, relative_path
        for marker in REQUIRED_RUNTIME_DOC_MARKERS[relative_path]:
            assert marker in contents, f"{relative_path} missing {marker!r}"


@pytest.mark.parametrize(
    "relative_path",
    ("", "/manifest.json", "../manifest.json", "./manifest.json", "agents/../README.md"),
)
def test_packaged_baseline_asset_rejects_invalid_relative_paths(relative_path: str) -> None:
    with pytest.raises(ValueError):
        packaged_baseline_asset(relative_path)
