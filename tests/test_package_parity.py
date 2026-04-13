from __future__ import annotations

import fnmatch
import json
import re
import tomllib
from pathlib import Path

import pytest

from millrace_engine import __version__
from millrace_engine.config import build_runtime_paths, load_engine_config
from millrace_engine.control import EngineControl
from millrace_engine.contracts import StageType


MILLRACE_ROOT = Path(__file__).resolve().parents[1]
CONTRACTOR_BLUEPRINT_ROOT = (
    MILLRACE_ROOT.parent
    / "work"
    / "contractor_blueprint_bundle"
    / "millrace_engine"
    / "assets"
)
PUBLIC_DOC_PARITY_PATHS = {
    "README.md": "README.md",
    "ADVISOR.md": "ADVISOR.md",
    "SENTINEL.md": "SENTINEL.md",
    "SUPERVISOR.md": "SUPERVISOR.md",
    "OPERATOR_GUIDE.md": "OPERATOR_GUIDE.md",
    "docs/RUNTIME_DEEP_DIVE.md": "docs/RUNTIME_DEEP_DIVE.md",
    "docs/TUI_DOCUMENTATION.md": "docs/TUI_DOCUMENTATION.md",
    "docs/runtime/README.md": "docs/runtime/README.md",
    "docs/runtime/control-plane-command-surface-and-mailbox-semantics.md": (
        "docs/runtime/control-plane-command-surface-and-mailbox-semantics.md"
    ),
    "docs/runtime/runtime-state-status-markers-and-stale-recovery-semantics.md": (
        "docs/runtime/runtime-state-status-markers-and-stale-recovery-semantics.md"
    ),
    "docs/runtime/runtime-loop-lifecycle-and-supervisor-authority.md": (
        "docs/runtime/runtime-loop-lifecycle-and-supervisor-authority.md"
    ),
}
CONTRACTOR_BUNDLE_PARITY_PATHS = {
    "agents/_contractor.md": "agents/_contractor.md",
    "agents/objective/contractor_profile.example.json": "agents/objective/contractor_profile.example.json",
    "agents/objective/contractor_profile.schema.json": "agents/objective/contractor_profile.schema.json",
    "agents/skills/contractor-classification/EXAMPLES_AMBIGUOUS_AND_EDGE_CASES.md": "agents/skills/contractor-classification/EXAMPLES_AMBIGUOUS_AND_EDGE_CASES.md",
    "agents/skills/contractor-classification/EXAMPLES_INDEX.md": "agents/skills/contractor-classification/EXAMPLES_INDEX.md",
    "agents/skills/contractor-classification/EXAMPLES_PLATFORM_EXTENSIONS.md": "agents/skills/contractor-classification/EXAMPLES_PLATFORM_EXTENSIONS.md",
    "agents/skills/contractor-classification/EXAMPLES_SHAPES.md": "agents/skills/contractor-classification/EXAMPLES_SHAPES.md",
    "agents/skills/contractor-classification/EXAMPLES_TOOLS_AND_LIBRARIES.md": "agents/skills/contractor-classification/EXAMPLES_TOOLS_AND_LIBRARIES.md",
    "agents/skills/contractor-classification/EXAMPLES_WEB_AND_NETWORK.md": "agents/skills/contractor-classification/EXAMPLES_WEB_AND_NETWORK.md",
    "agents/skills/contractor-classification/SKILL.md": "agents/skills/contractor-classification/SKILL.md",
}
PACKAGED_RESEARCH_CONTRACT_PATHS = {
    "agents/_objective_profile_sync.md": {
        "absent": (
            "agents/tools/objective_profile_sync.py",
            "agents/tools/validate_objective_contract.py",
        ),
        "present": (
            "millrace_engine/research/goalspec_objective_profile_sync.py",
            "millrace_engine/research/goalspec_semantic_profile.py",
            "millrace_engine/research/governance.py",
        ),
    },
    "agents/_spec_synthesis.md": {
        "absent": ("agents/tools/spec_family_state.py",),
        "present": (
            "millrace_engine/research/goalspec_spec_synthesis.py",
            "millrace_engine/research/goalspec_persistence.py",
            "millrace_engine/research/specs.py",
            "millrace_engine/research/goalspec_scope_diagnostics.py",
        ),
    },
    "agents/_taskmaster.md": {
        "absent": (
            "agents/tools/toposort_specs.py",
            "agents/tools/dedupe_tasks.py",
            "agents/tools/lint_task_cards.py",
        ),
        "present": ("millrace_engine/research/taskmaster.py",),
    },
}
PUBLIC_EXECUTION_STAGES = (
    StageType.BUILDER,
    StageType.INTEGRATION,
    StageType.QA,
    StageType.HOTFIX,
    StageType.DOUBLECHECK,
    StageType.TROUBLESHOOT,
    StageType.CONSULT,
    StageType.UPDATE,
)
EXTERNAL_FIXTURE_PATH = "/".join(
    ("ref-framework", "millrace-temp-main", "agents", "tools", "fixtures")
)
OPERATIONS_SKILL_PATH = "agents/skills/millrace-operator-intake-control/SKILL.md"


def _pyproject_payload() -> dict[str, object]:
    return tomllib.loads((MILLRACE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _runtime_version_from_init(repo_root: Path) -> str | None:
    init_path = repo_root / "millrace_engine" / "__init__.py"
    if not init_path.is_file():
        return None
    match = re.search(
        r'^__version__\s*=\s*"(?P<version>\d+\.\d+\.\d+)"\s*$',
        init_path.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    return match.group("version") if match else None


def _runtime_packages_on_disk() -> set[str]:
    packages: set[str] = set()
    for path in sorted((MILLRACE_ROOT / "millrace_engine").rglob("__init__.py")):
        package_path = path.parent.relative_to(MILLRACE_ROOT)
        packages.add(".".join(package_path.parts))
    return packages


def _assert_public_docs_match_packaged_copies() -> None:
    assets_root = MILLRACE_ROOT / "millrace_engine" / "assets"
    for public_relative, asset_relative in PUBLIC_DOC_PARITY_PATHS.items():
        public_text = (MILLRACE_ROOT / public_relative).read_text(encoding="utf-8")
        asset_text = (assets_root / asset_relative).read_text(encoding="utf-8")
        assert public_text == asset_text, public_relative


def _assert_contractor_bundle_matches_blueprint_source_docs() -> None:
    if not CONTRACTOR_BLUEPRINT_ROOT.is_dir():
        return

    assets_root = MILLRACE_ROOT / "millrace_engine" / "assets"
    for blueprint_relative, asset_relative in CONTRACTOR_BUNDLE_PARITY_PATHS.items():
        blueprint_path = CONTRACTOR_BLUEPRINT_ROOT / blueprint_relative
        assert blueprint_path.is_file(), blueprint_relative
        blueprint_text = blueprint_path.read_text(encoding="utf-8")
        asset_text = (assets_root / asset_relative).read_text(encoding="utf-8")
        assert asset_text == blueprint_text, asset_relative


def test_contractor_bundle_matches_blueprint_source_docs() -> None:
    _assert_contractor_bundle_matches_blueprint_source_docs()


def test_packaged_docs_and_operator_assets_exist() -> None:
    for relative in (
        "README.md",
        "docs/RUNTIME_DEEP_DIVE.md",
        "docs/runtime/README.md",
        "docs/runtime/control-plane-command-surface-and-mailbox-semantics.md",
        "docs/runtime/runtime-state-status-markers-and-stale-recovery-semantics.md",
        "docs/runtime/runtime-loop-lifecycle-and-supervisor-authority.md",
        "ADVISOR.md",
        "SENTINEL.md",
        "SUPERVISOR.md",
        "OPERATOR_GUIDE.md",
        "docs/TUI_DOCUMENTATION.md",
    ):
        assert (MILLRACE_ROOT / relative).exists(), relative

    assets_root = MILLRACE_ROOT / "millrace_engine" / "assets"
    for relative in (
        "README.md",
        "ADVISOR.md",
        "SENTINEL.md",
        "SUPERVISOR.md",
        "OPERATOR_GUIDE.md",
        "docs/RUNTIME_DEEP_DIVE.md",
        "docs/runtime/README.md",
        "docs/runtime/control-plane-command-surface-and-mailbox-semantics.md",
        "docs/runtime/runtime-state-status-markers-and-stale-recovery-semantics.md",
        "docs/runtime/runtime-loop-lifecycle-and-supervisor-authority.md",
        "docs/TUI_DOCUMENTATION.md",
        "millrace.toml",
    ):
        assert (assets_root / relative).exists(), relative

    manifest = json.loads((assets_root / "manifest.json").read_text(encoding="utf-8"))
    manifest_directories = {entry["path"] for entry in manifest["directories"]}
    manifest_paths = {entry["path"] for entry in manifest["files"]}
    assert "docs" in manifest_directories
    assert "docs/runtime" in manifest_directories
    assert "README.md" in manifest_paths
    assert "SENTINEL.md" in manifest_paths
    assert "SUPERVISOR.md" in manifest_paths
    assert "OPERATOR_GUIDE.md" in manifest_paths
    assert "docs/RUNTIME_DEEP_DIVE.md" in manifest_paths
    assert "docs/runtime/README.md" in manifest_paths
    assert "docs/runtime/control-plane-command-surface-and-mailbox-semantics.md" in manifest_paths
    assert "docs/runtime/runtime-state-status-markers-and-stale-recovery-semantics.md" in manifest_paths
    assert "docs/runtime/runtime-loop-lifecycle-and-supervisor-authority.md" in manifest_paths
    assert "docs/TUI_DOCUMENTATION.md" in manifest_paths
    assert OPERATIONS_SKILL_PATH in manifest_paths
    assert "agents/skills/millrace-operator-intake-control/EXAMPLES.md" in manifest_paths
    assert "agents/_contractor.md" in manifest_paths
    assert "agents/_completion_manifest_draft.md" in manifest_paths
    assert "agents/objective/contractor_profile.schema.json" in manifest_paths
    assert "agents/objective/contractor_profile.example.json" in manifest_paths
    assert "agents/skills/contractor-classification/SKILL.md" in manifest_paths
    assert "agents/skills/contractor-classification/EXAMPLES_INDEX.md" in manifest_paths
    assert "agents/skills/contractor-classification/EXAMPLES_SHAPES.md" in manifest_paths
    assert "agents/skills/contractor-classification/EXAMPLES_PLATFORM_EXTENSIONS.md" in manifest_paths
    assert "agents/skills/contractor-classification/EXAMPLES_WEB_AND_NETWORK.md" in manifest_paths
    assert "agents/skills/contractor-classification/EXAMPLES_TOOLS_AND_LIBRARIES.md" in manifest_paths
    assert "agents/skills/contractor-classification/EXAMPLES_AMBIGUOUS_AND_EDGE_CASES.md" in manifest_paths
    for removed_path in (
        "agents/legacy",
        "agents/legacy/README.md",
        "agents/legacy/_orchestrate.md",
        "agents/legacy/_supervisor.md",
        "agents/_sentinel.md",
        "agents/options/model_config.md",
        "agents/options/workflow_config.md",
        "agents/orchestrate_loop.sh",
        "agents/research_loop.sh",
    ):
        assert removed_path not in manifest_paths
    _assert_public_docs_match_packaged_copies()
    _assert_contractor_bundle_matches_blueprint_source_docs()

    readme = (MILLRACE_ROOT / "README.md").read_text(encoding="utf-8")
    assert "autonomous" in readme.lower()
    assert "## Why Millrace Exists" in readme
    assert "## How Millrace Is Different" in readme
    assert "## Design Philosophy" in readme
    assert "## Initialized Workspace Layout" in readme
    assert "they are not expected at the public repo root" in readme.lower()
    assert "python3 -m pip install millrace-ai" in readme
    assert "millrace init /absolute/path/to/workspace" in readme
    assert "OPERATOR_GUIDE.md" in readme
    assert "ADVISOR.md" in readme
    assert "OpenClaw-style supervisor agents" in readme
    assert "millrace health --json" in readme
    assert "pass `--config /absolute/path/to/workspace/millrace.toml` explicitly" in readme
    assert 'supervisor cleanup remove <task-id> --issuer <name> --reason "Invalid queued work" --json' in readme
    assert '[research] mode = "stub"' in readme
    assert 'interview_policy = "off"' in readme
    assert "Release verification is narrower than source-checkout contributor verification" in readme
    assert "upgrade --apply" in readme
    assert "`engine_config_coordinator.py`" in readme
    assert "`engine_mailbox_processor.py`" in readme
    assert "`engine_runtime_loop.py`" in readme
    assert "`planes/execution_flows/`" in readme
    assert "`millrace_engine/research/goalspec_stage_support.py`" in readme
    assert "`goalspec_completion_manifest_draft.py`" in readme
    assert "`goalspec_stage_rendering.py`" in readme
    assert "`tools/repo_guardrails.py`" in readme
    assert "same-change ratchets" in readme
    assert "## Governed Compounding Model" in readme
    assert "raw -> compiled -> query -> lint" in readme
    assert "packaged `agents/skills` are the shipped operator/agent guidance surface" in readme
    assert "compounding orient --query builder" in readme
    assert "compounding lint" in readme
    assert "Derived orientation surface only; governed compounding artifacts remain the source of truth." in readme
    assert "goal_intake -> objective_profile_sync (begins with inline Contractor classification) -> completion_manifest_draft -> spec_synthesis" in readme
    assert "`agents/_contractor.md` runs inline at the start of `objective_profile_sync`" in readme
    assert "`agents/_completion_manifest_draft.md` remains the dedicated completion-manifest draft asset" in readme
    assert "`agents/skills/contractor-classification/`" in readme
    assert "semantic_profile_seed.json`, `.yaml`, or `.yml`" in readme
    assert "full bounded initial-family cap" in readme
    assert "sibling-specific decomposition profiles" in readme
    assert "bounded one-spec agentic review/edit pass" in readme
    assert "deterministic structural validator before Taskmaster promotion" in readme
    assert "mixed-ready GoalSpec, incident, and audit queues follow deterministic family precedence" in readme
    assert "bounded local Mechanic repair" in readme
    assert "GoalSpec-owned `goal_gap_remediation` family" in readme
    assert "goal-gap remediation-family staging" in readme
    assert "Execution `IDLE` is the execution plane's neutral state" in readme

    advisor = (MILLRACE_ROOT / "ADVISOR.md").read_text(encoding="utf-8")
    assert "This file is for agents acting as the operator shell" in advisor
    assert "This prompt assumes you are operating inside an initialized Millrace workspace" in advisor
    assert "install `millrace-ai`" in advisor
    assert "millrace init /absolute/path/to/workspace" in advisor
    assert "## Supported Local Workflow" in advisor
    assert "Start with CLI JSON inspection when the runtime state is unknown" in advisor
    assert "Use the TUI when you want an interactive local control shell" in advisor
    assert "do not tell Millrace to run GoalSpec, Spec Review, Taskmaster, audit, or other internal stages" in advisor
    assert 'queue cleanup remove <task-id> --reason "Invalid duplicate task"' in advisor
    assert 'supervisor cleanup remove <task-id> --issuer <name> --reason "Invalid queued work" --json' in advisor
    assert "health --json" in advisor
    assert "OpenClaw Supervisor agent" in advisor
    assert "publish preflight --json" in advisor
    assert OPERATIONS_SKILL_PATH in advisor
    assert "Read execution `IDLE` as the execution plane's neutral state" in advisor

    sentinel = (MILLRACE_ROOT / "SENTINEL.md").read_text(encoding="utf-8")
    assert "one-workspace Sentinel companion monitor" in sentinel
    assert OPERATIONS_SKILL_PATH in sentinel
    assert "millrace --config millrace.toml sentinel check --json" in sentinel
    assert "millrace --config millrace.toml sentinel status --json" in sentinel
    assert "millrace --config millrace.toml sentinel watch --json" in sentinel
    assert "millrace --config millrace.toml sentinel acknowledge --issuer <name> --reason \"...\" --json" in sentinel
    assert "millrace --config millrace.toml sentinel incident --failure-signature <token> --summary \"...\" --json" in sentinel
    assert "millrace --config millrace.toml recovery request troubleshoot --issuer <name> --reason \"...\" --force-queue --json" in sentinel
    assert "The shipped public Sentinel CLI in this repo includes `check`, `status`, `watch`, `acknowledge`, and `incident`." in sentinel
    assert "supervisor report --json" in sentinel
    assert "Execution `IDLE` is the execution plane's neutral state." in sentinel
    assert "Sentinel is a first-class Supervisor-lineage companion monitor." in sentinel
    assert "live.millrace.ai" in sentinel
    assert "does not turn Millrace core into a multi-workspace Sentinel portfolio supervisor" in sentinel

    supervisor = (MILLRACE_ROOT / "SUPERVISOR.md").read_text(encoding="utf-8")
    assert "This file is for agents acting as the external one-workspace supervisor" in supervisor
    assert OPERATIONS_SKILL_PATH in supervisor
    assert "supervisor report --json" in supervisor
    assert "`attention_reason`, `attention_summary`, and `allowed_actions`" in supervisor
    assert "report's `sentinel` section" in supervisor
    assert "poll frequency, heartbeat strategy, and wakeup delivery" in supervisor
    assert 'supervisor add-task "Example task" --issuer <name> --json' in supervisor
    assert 'supervisor cleanup remove <task-id> --issuer <name> --reason "Invalid queued work" --json' in supervisor
    assert "Use `ADVISOR.md` instead" in supervisor
    assert "Read execution `IDLE` as the execution plane's neutral state" in supervisor

    skill = (
        MILLRACE_ROOT
        / "millrace_engine"
        / "assets"
        / "agents/skills/millrace-operator-intake-control/SKILL.md"
    ).read_text(encoding="utf-8")
    assert "before `millrace queue cleanup ...` or `millrace supervisor cleanup ...`" in skill
    assert "local: `millrace queue cleanup remove|quarantine" in skill
    assert "external: `millrace --config millrace.toml supervisor cleanup remove|quarantine" in skill

    operator_guide = (MILLRACE_ROOT / "OPERATOR_GUIDE.md").read_text(encoding="utf-8")
    assert "OpenClaw or another external supervisor harness" in operator_guide
    assert 'supervisor cleanup remove <task-id> --issuer <name> --reason "Invalid queued work" --json' in operator_guide
    assert '[research] mode = "stub"' in operator_guide
    assert 'interview_policy = "off"' in operator_guide
    assert "Release CI verifies a narrower contract than a contributor source checkout" in operator_guide
    assert "upgrade --apply" in operator_guide
    assert "## Governed Compounding Operating Model" in operator_guide
    assert "raw -> compiled -> query -> lint" in operator_guide
    assert "Execution `IDLE` is the execution plane's neutral state" in operator_guide
    assert "governed compounding authority lives in typed artifacts under `agents/compounding/`" in operator_guide
    assert "compounding orient --query builder" in operator_guide
    assert "compounding lint" in operator_guide
    assert "Derived orientation surface only; governed compounding artifacts remain the source of truth." in operator_guide
    assert "goal_intake -> objective_profile_sync (begins with inline Contractor classification) -> completion_manifest_draft -> spec_synthesis" in operator_guide
    assert "`agents/_contractor.md` runs inline at the start of `objective_profile_sync`" in operator_guide
    assert "`agents/_completion_manifest_draft.md` remains the dedicated completion-manifest draft asset" in operator_guide
    assert "`agents/skills/contractor-classification/`" in operator_guide
    assert "semantic_profile_seed.json`, `.yaml`, or `.yml`" in operator_guide
    assert "full bounded initial-family cap" in operator_guide
    assert "sibling-specific decomposition profiles" in operator_guide
    assert "uses the shipped agentic review contract" in operator_guide
    assert "deterministic structural validator before Taskmaster promotion" in operator_guide
    assert "mixed-ready GoalSpec, incident, and audit queues follow deterministic family precedence" in operator_guide
    assert "bounded local Mechanic repair" in operator_guide
    assert "GoalSpec-owned `goal_gap_remediation` family" in operator_guide
    assert "goal-gap remediation-family staging" in operator_guide
    assert "## Sentinel Monitor Workflow" in operator_guide
    assert "sentinel watch --json" in operator_guide
    assert "sentinel` section" in operator_guide
    assert "live.millrace.ai" in operator_guide

    runtime_deep_dive = (MILLRACE_ROOT / "docs" / "RUNTIME_DEEP_DIVE.md").read_text(encoding="utf-8")
    assert "### 5.7 Governed Compounding Operating Model" in runtime_deep_dive
    assert "raw -> compiled -> query -> lint" in runtime_deep_dive
    assert "`procedures/` for reusable procedure artifacts" in runtime_deep_dive
    assert "`millrace compounding orient` derives `agents/compounding/indexes/governed_store_index.json`" in runtime_deep_dive
    assert "`compounding.integrity` workspace check" in runtime_deep_dive
    assert "Derived orientation surface only; governed compounding artifacts remain the source of truth." in runtime_deep_dive
    assert "### 22.3 External Supervisor Surface" in runtime_deep_dive
    assert "`attention_reason`, `attention_summary`, and `allowed_actions`" in runtime_deep_dive
    assert "`sentinel` summary derived from those persisted artifacts" in runtime_deep_dive
    assert "supervisor cleanup remove|quarantine" in runtime_deep_dive
    assert "structured runtime policy lives in `millrace_engine/execution_prompt_contracts.py`" in runtime_deep_dive
    assert "the markdown files remain the instruction layer" in runtime_deep_dive
    assert "`engine_runtime.py`: shared engine runtime dependency bundle" in runtime_deep_dive
    assert "`engine_config_coordinator.py`: config reload/apply/rollback coordinator" in runtime_deep_dive
    assert "`engine_mailbox_processor.py`: daemon mailbox intake, dispatch, and archive coordinator" in runtime_deep_dive
    assert "overview snapshots of runtime, Sentinel summary, config, queue, and research state" in runtime_deep_dive

    readme_tui = (MILLRACE_ROOT / "docs" / "TUI_DOCUMENTATION.md").read_text(encoding="utf-8")
    assert "persisted one-workspace Sentinel summary exported through `supervisor report --json`" in readme_tui
    assert "runtime/sentinel/latest-run/research/governance/attention detail cards" in readme_tui
    assert "not a hosted dashboard or a multi-workspace supervision surface" in readme_tui

    assert "## Sentinel Companion Monitor" in readme
    assert "sentinel acknowledge --issuer <name> --reason \"...\" --json" in readme
    assert "hosted `live.millrace.ai` dashboard" in readme
    assert "`engine_runtime_loop.py`: daemon-loop, watcher, wakeup, and post-cycle control coordinator" in runtime_deep_dive
    assert "`execution_flows/`: quickfix, QA, builder-success, and cycle-runner flow-family modules" in runtime_deep_dive
    assert "`goalspec_stage_support.py` remains as a thin routing/re-export facade" in runtime_deep_dive
    assert "`tools/repo_guardrails.py`" in runtime_deep_dive
    assert "same-change ratchets" in runtime_deep_dive
    assert "### 17.3A GoalSpec Staged Contract" in runtime_deep_dive
    assert "goal_intake -> objective_profile_sync (begins with inline Contractor classification) -> completion_manifest_draft -> spec_synthesis" in runtime_deep_dive
    assert "`agents/_completion_manifest_draft.md` is the dedicated completion-manifest draft asset" in runtime_deep_dive
    assert "`_contractor.md` is not the completion-manifest entrypoint" in runtime_deep_dive
    assert "`agents/skills/contractor-classification/`" in runtime_deep_dive
    assert "semantic_profile_seed.yaml" in runtime_deep_dive
    assert "full bounded initial-family cap" in runtime_deep_dive
    assert "sibling-specific decomposition profiles" in runtime_deep_dive
    assert "bounded one-spec agentic review/edit stage" in runtime_deep_dive
    assert "deterministic structural validator" in runtime_deep_dive
    assert "Taskmaster emits product-first per-spec shards" in runtime_deep_dive
    assert "mixed-ready `AUTO` queues follow deterministic family precedence" in runtime_deep_dive
    assert "bounded local Mechanic repair" in runtime_deep_dive
    assert "GoalSpec-owned `goal_gap_remediation` family" in runtime_deep_dive
    assert "goal-gap remediation-family staging" in runtime_deep_dive
    assert "`thaw()` rehydrates previously frozen cards once visible backlog work reappears" in runtime_deep_dive

    tui_doc = (MILLRACE_ROOT / "docs" / "TUI_DOCUMENTATION.md").read_text(encoding="utf-8")
    assert "## External Supervisor Boundary" in tui_doc

    for relative in (
        "millrace_engine/engine_config_coordinator.py",
        "millrace_engine/engine_mailbox_processor.py",
        "millrace_engine/engine_mailbox_command_handlers.py",
        "millrace_engine/engine_runtime.py",
        "millrace_engine/engine_runtime_loop.py",
        "millrace_engine/planes/execution_flows/builder_flow.py",
        "millrace_engine/planes/execution_flows/qa_flow.py",
        "millrace_engine/planes/execution_flows/quickfix_flow.py",
        "millrace_engine/planes/execution_flows/cycle_runner.py",
        "millrace_engine/research/goalspec_goal_intake.py",
        "millrace_engine/research/goalspec_objective_profile_sync.py",
        "millrace_engine/research/goalspec_completion_manifest_draft.py",
        "millrace_engine/research/goalspec_spec_synthesis.py",
        "millrace_engine/research/goalspec_spec_interview.py",
        "millrace_engine/research/goalspec_spec_review.py",
        "millrace_engine/research/goalspec_stage_rendering.py",
        "tools/repo_guardrails.py",
    ):
        assert (MILLRACE_ROOT / relative).exists(), relative


def test_packaged_goalspec_truth_surfaces_describe_review_recovery_contract() -> None:
    assets_root = MILLRACE_ROOT / "millrace_engine" / "assets"

    goalspec_loop = json.loads(
        (
            assets_root
            / "registry"
            / "loops"
            / "research"
            / "research.goalspec__1.0.0.json"
        ).read_text(encoding="utf-8")
    )
    goalspec_loop_md = (
        assets_root
        / "registry"
        / "loops"
        / "research"
        / "research.goalspec__1.0.0.md"
    ).read_text(encoding="utf-8")
    spec_review_stage = json.loads(
        (
            assets_root
            / "registry"
            / "stages"
            / "research.spec-review__1.0.0.json"
        ).read_text(encoding="utf-8")
    )
    spec_review_stage_md = (
        assets_root
        / "registry"
        / "stages"
        / "research.spec-review__1.0.0.md"
    ).read_text(encoding="utf-8")
    mechanic_prompt = (assets_root / "agents" / "_mechanic.md").read_text(
        encoding="utf-8"
    )

    for contents in (goalspec_loop["summary"], goalspec_loop_md):
        assert "broad bounded family synthesis" in contents
        assert "sibling-specific sizing" in contents
        assert "runtime-owned local repair or remediation-family escalation" in contents

    for contents in (spec_review_stage["summary"], spec_review_stage_md):
        assert "bounded GoalSpec review stage" in contents
        assert "shipped agentic review contract" in contents
        assert "structural validator" in contents
        assert "bounded remediation routing" in contents

    assert "bounded local repair" in mechanic_prompt
    assert "structural remediation bundle already exists" in mechanic_prompt
    assert "broader remediation family" in mechanic_prompt


def test_default_public_stage_prompt_assets_exist(tmp_path: Path) -> None:
    workspace = tmp_path / "public-stage-workspace"
    init_result = EngineControl.init_workspace(workspace)

    assert init_result.applied is True
    assert (workspace / "docs" / "TUI_DOCUMENTATION.md").exists()

    loaded = load_engine_config(workspace / "millrace.toml")
    live_agents_root = (workspace / "agents").resolve()

    for stage in PUBLIC_EXECUTION_STAGES:
        prompt_path = loaded.config.stages[stage].prompt_file
        assert prompt_path is not None, stage.value
        assert prompt_path.exists(), prompt_path
        assert prompt_path.is_relative_to(live_agents_root), prompt_path


def test_runtime_compounding_namespace_does_not_collide_with_packaged_skills(tmp_path: Path) -> None:
    workspace = tmp_path / "compounding-namespace-workspace"
    init_result = EngineControl.init_workspace(workspace)

    assert init_result.applied is True

    loaded = load_engine_config(workspace / "millrace.toml")
    runtime_paths = build_runtime_paths(loaded.config)
    packaged_skills_root = (MILLRACE_ROOT / "millrace_engine" / "assets" / "agents" / "skills").resolve()

    assert packaged_skills_root.is_dir()
    assert runtime_paths.compounding_dir == (workspace / "agents/compounding").resolve()
    assert runtime_paths.compounding_procedures_dir == (
        workspace / "agents/compounding/procedures"
    ).resolve()
    assert runtime_paths.compounding_dir != packaged_skills_root
    assert runtime_paths.compounding_dir.parent == (workspace / "agents").resolve()
    assert runtime_paths.compounding_dir.name == "compounding"


def test_packaged_research_entrypoint_docs_match_shipped_python_runtime_contract() -> None:
    assets_root = MILLRACE_ROOT / "millrace_engine" / "assets"

    for relative_path, expectations in PACKAGED_RESEARCH_CONTRACT_PATHS.items():
        contents = (assets_root / relative_path).read_text(encoding="utf-8")
        for marker in expectations["absent"]:
            assert marker not in contents, f"{relative_path} still references absent helper {marker}"
        for marker in expectations["present"]:
            assert marker in contents, f"{relative_path} missing shipped runtime seam {marker}"


def test_advisor_supervisor_and_sentinel_entrypoints_explicitly_load_shared_operations_skill() -> None:
    assets_root = MILLRACE_ROOT / "millrace_engine" / "assets"

    public_advisor = (MILLRACE_ROOT / "ADVISOR.md").read_text(encoding="utf-8")
    public_sentinel = (MILLRACE_ROOT / "SENTINEL.md").read_text(encoding="utf-8")
    public_supervisor = (MILLRACE_ROOT / "SUPERVISOR.md").read_text(encoding="utf-8")
    packaged_advisor = (assets_root / "agents" / "_advisor.md").read_text(encoding="utf-8")
    packaged_supervisor = (assets_root / "agents" / "_supervisor.md").read_text(encoding="utf-8")

    for contents in (public_advisor, public_sentinel, public_supervisor, packaged_advisor, packaged_supervisor):
        assert OPERATIONS_SKILL_PATH in contents

    assert "SUPERVISOR.md" in packaged_advisor
    assert "ADVISOR.md" in packaged_supervisor


def test_packaged_tests_do_not_reference_repo_external_fixtures() -> None:
    for path in sorted((MILLRACE_ROOT / "tests").glob("test_*.py")):
        contents = path.read_text(encoding="utf-8")
        assert EXTERNAL_FIXTURE_PATH not in contents, path.name


def test_project_version_is_sourced_from_runtime_module() -> None:
    pyproject = _pyproject_payload()
    project = pyproject["project"]
    setuptools_dynamic = pyproject["tool"]["setuptools"]["dynamic"]

    assert "version" not in project
    assert project["dynamic"] == ["version"]
    assert setuptools_dynamic["version"] == {"attr": "millrace_engine.__version__"}
    assert __version__
    assert len(__version__.split(".")) == 3
    assert all(part.isdigit() for part in __version__.split("."))


def test_staged_clean_repo_version_matches_source_when_present() -> None:
    staged_clean_root = MILLRACE_ROOT.parent / "clean"
    staged_clean_version = _runtime_version_from_init(staged_clean_root)

    if staged_clean_version is None:
        pytest.skip("staged clean repo not present")

    assert staged_clean_version == __version__


def test_setuptools_package_discovery_covers_non_legacy_runtime_packages() -> None:
    pyproject = _pyproject_payload()
    find = pyproject["tool"]["setuptools"]["packages"]["find"]
    packages_on_disk = _runtime_packages_on_disk()

    include_patterns = find["include"]
    exclude_patterns = find["exclude"]
    discovered = {
        package
        for package in packages_on_disk
        if any(fnmatch.fnmatchcase(package, pattern) for pattern in include_patterns)
        and not any(fnmatch.fnmatchcase(package, pattern) for pattern in exclude_patterns)
    }

    assert include_patterns == ["millrace_engine*"]
    assert exclude_patterns == ["millrace_engine.legacy*"]
    if "millrace_engine.legacy" in packages_on_disk:
        assert discovered == packages_on_disk - {"millrace_engine.legacy"}
    else:
        assert discovered == packages_on_disk


def test_shipped_runtime_package_does_not_import_dropped_legacy_package() -> None:
    for path in sorted((MILLRACE_ROOT / "millrace_engine").rglob("*.py")):
        if "build" in path.parts:
            continue
        contents = path.read_text(encoding="utf-8")
        assert "from .legacy" not in contents, path.relative_to(MILLRACE_ROOT).as_posix()
        assert "import .legacy" not in contents, path.relative_to(MILLRACE_ROOT).as_posix()
        assert "from millrace_engine.legacy" not in contents, path.relative_to(MILLRACE_ROOT).as_posix()
