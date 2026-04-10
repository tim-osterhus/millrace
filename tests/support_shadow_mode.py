from __future__ import annotations

from pathlib import Path
import json
import shutil
import subprocess

from millrace_engine.config import build_runtime_paths, load_engine_config
from millrace_engine.control import EngineControl
from millrace_engine.contracts import AuditGateDecision, AuditGateDecisionCounts, CompletionDecision
from millrace_engine.paths import RuntimePaths
from millrace_engine.publishing import commit_staging_repo, preflight_staging_publish, sync_staging_repo
from millrace_engine.research.governance import evaluate_initial_family_plan_guard, resolve_family_governor_state
from millrace_engine.research.specs import GoalSpecFamilySpecState, GoalSpecFamilyState
from tests.support import fixture_source, load_workspace_fixture


TESTS_ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = TESTS_ROOT.parent
REPO_ROOT = RUNTIME_ROOT.parent


def _resolve_reference_root() -> Path:
    candidates = (
        REPO_ROOT / "ref-framework" / "millrace-temp-main",
        REPO_ROOT / "ref-framework" / "millrace-temp-main-old",
        REPO_ROOT / "ref-framework" / "millrace-temp-main-new",
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise AssertionError("reference framework checkout is not available")


REFERENCE_ROOT = _resolve_reference_root()
REFERENCE_TOOLS_DIR = REFERENCE_ROOT / "agents" / "tools"
SHADOW_MODE_FIXTURE = "parity/shadow_mode_equivalence"
PUBLISH_COMMIT_MESSAGE = "Shadow mode parity publish"


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _git(repo_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return _run(["git", "-C", repo_dir.as_posix(), *args], cwd=repo_dir)


def _materialize_shadow_mode_workspace(tmp_path: Path, *, suffix: str) -> tuple[Path, Path]:
    return load_workspace_fixture(tmp_path / suffix, SHADOW_MODE_FIXTURE)


def _init_pushable_staging_repo(parent: Path, *, name: str) -> Path:
    remote_dir = parent / f"{name}-remote.git"
    repo_dir = parent / name
    repo_dir.mkdir(parents=True, exist_ok=True)

    init_remote = _run(["git", "init", "--bare", remote_dir.as_posix()], cwd=parent)
    assert init_remote.returncode == 0, init_remote.stderr

    init_repo = _git(repo_dir, "init")
    assert init_repo.returncode == 0, init_repo.stderr
    checkout = _git(repo_dir, "checkout", "-B", "main")
    assert checkout.returncode == 0, checkout.stderr
    assert _git(repo_dir, "config", "user.email", "shadow-mode@example.com").returncode == 0
    assert _git(repo_dir, "config", "user.name", "Shadow Mode Tests").returncode == 0
    assert _git(repo_dir, "remote", "add", "origin", remote_dir.as_posix()).returncode == 0
    assert _git(repo_dir, "commit", "--allow-empty", "-m", "Initial staging state").returncode == 0
    push = _git(repo_dir, "push", "-u", "origin", "main")
    assert push.returncode == 0, push.stderr
    return repo_dir


def _parse_bash_sync_stdout(stdout: str) -> dict[str, object]:
    entries: list[dict[str, str]] = []
    created_staging_dir = False
    complete_line = ""
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("STAGING_DIR_CREATED "):
            created_staging_dir = True
            continue
        if line.startswith("SYNCED "):
            entries.append({"path": line.split(" ", 1)[1], "action": "synced"})
            continue
        if line.startswith("REMOVED_OPTIONAL "):
            entries.append({"path": line.split(" ", 1)[1], "action": "removed_optional"})
            continue
        if line.startswith("SKIPPED_OPTIONAL "):
            entries.append({"path": line.split(" ", 1)[1], "action": "skipped_optional"})
            continue
        if line.startswith("SYNC_COMPLETE "):
            complete_line = line
    return {
        "created_staging_dir": created_staging_dir,
        "entries": entries,
        "complete_line": complete_line,
    }


def _last_non_empty_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _normalize_python_sync_entries(entries: tuple[object, ...]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for entry in entries:
        normalized.append(
            {
                "path": str(getattr(entry, "path")),
                "action": str(getattr(entry, "action")),
            }
        )
    return normalized


def _write_completion_reports(workspace: Path) -> None:
    counts = AuditGateDecisionCounts(
        required_total=1,
        required_pass=1,
        required_fail=0,
        required_blocked=0,
        completion_required=1,
        completion_pass=1,
        open_gaps=0,
        task_store_cards=0,
        active_task_cards=0,
        backlog_cards=0,
        pending_task_cards=0,
    )
    gate_path = workspace / "agents" / "reports" / "audit_gate_decision.json"
    completion_path = workspace / "agents" / "reports" / "completion_decision.json"
    gate_path.parent.mkdir(parents=True, exist_ok=True)
    gate_path.write_text(
        AuditGateDecision(
            run_id="shadow-mode-cutover",
            audit_id="AUD-SHADOW-CUTOVER",
            generated_at="2026-03-22T18:00:00Z",
            decision="PASS",
            counts=counts,
            gate_decision_path="agents/reports/audit_gate_decision.json",
            objective_contract_path="agents/objective/contract.yaml",
            completion_manifest_path="agents/audit/completion_manifest.json",
            execution_report_path="agents/.research_runtime/audit/execution/shadow-mode-cutover.json",
            validate_record_path="agents/.research_runtime/audit/validate/shadow-mode-cutover.json",
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )
    completion_path.write_text(
        CompletionDecision(
            run_id="shadow-mode-cutover",
            audit_id="AUD-SHADOW-CUTOVER",
            generated_at="2026-03-22T18:00:00Z",
            decision="PASS",
            counts=counts,
            completion_decision_path="agents/reports/completion_decision.json",
            gate_decision_path="agents/reports/audit_gate_decision.json",
            objective_contract_path="agents/objective/contract.yaml",
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )


def _build_governance_comparison(tmp_path: Path) -> dict[str, object]:
    bash_root = tmp_path / "bash-governance"
    (bash_root / "agents" / "objective").mkdir(parents=True, exist_ok=True)
    (bash_root / "agents" / ".research_runtime").mkdir(parents=True, exist_ok=True)
    (bash_root / "agents" / "ideas" / "raw").mkdir(parents=True, exist_ok=True)
    (bash_root / "agents" / "ideas" / "raw" / "goal.md").write_text("# Goal\n", encoding="utf-8")

    policy_payload = {
        "family_cap_mode": "adaptive",
        "initial_scope_mode": "bounded_initial_family",
        "initial_family_max_specs": 2,
        "remediation_family_max_specs": 5,
        "defer_overflow_follow_ons": True,
        "overflow_registry_path": "agents/.research_runtime/deferred_follow_ons.json",
    }
    state_payload = {
        "goal_id": "IDEA-SHADOW-001",
        "source_idea_path": "agents/ideas/raw/goal.md",
        "family_phase": "initial_family",
        "family_complete": False,
        "active_spec_id": "SPEC-BETA",
        "spec_order": ["SPEC-ALPHA", "SPEC-BETA", "SPEC-GAMMA"],
        "specs": {
            "SPEC-ALPHA": {
                "status": "emitted",
                "title": "Alpha",
                "decomposition_profile": "moderate",
            },
            "SPEC-BETA": {
                "status": "planned",
                "title": "Beta",
                "decomposition_profile": "moderate",
                "depends_on_specs": ["SPEC-ALPHA"],
            },
            "SPEC-GAMMA": {
                "status": "planned",
                "title": "Gamma",
                "decomposition_profile": "simple",
                "depends_on_specs": ["SPEC-BETA"],
            },
        },
    }
    policy_path = bash_root / "agents" / "objective" / "family_policy.json"
    state_path = bash_root / "agents" / ".research_runtime" / "spec_family_state.json"
    policy_path.write_text(json.dumps(policy_payload, indent=2) + "\n", encoding="utf-8")
    state_path.write_text(json.dumps(state_payload, indent=2) + "\n", encoding="utf-8")

    bash_result = _run(
        [
            "python3",
            (REFERENCE_TOOLS_DIR / "family_governor.py").as_posix(),
            "--state",
            "agents/.research_runtime/spec_family_state.json",
            "--policy",
            "agents/objective/family_policy.json",
            "--trigger-spec-id",
            "SPEC-BETA",
            "--source-idea-path",
            "agents/ideas/raw/goal.md",
            "--json",
        ],
        cwd=bash_root,
    )
    assert bash_result.returncode == 0, bash_result.stderr
    bash_summary = json.loads(bash_result.stdout)
    bash_state_after = json.loads(state_path.read_text(encoding="utf-8"))
    bash_registry = json.loads(
        (bash_root / "agents" / ".research_runtime" / "deferred_follow_ons.json").read_text(encoding="utf-8")
    )

    python_root = tmp_path / "python-governance"
    python_paths = RuntimePaths.from_workspace(python_root, Path("agents"))
    python_paths.objective_dir.mkdir(parents=True, exist_ok=True)
    python_paths.ideas_raw_dir.mkdir(parents=True, exist_ok=True)
    python_paths.objective_family_policy_file.write_text(
        json.dumps({"initial_family_max_specs": 2, "remediation_family_max_specs": 5}) + "\n",
        encoding="utf-8",
    )
    python_current_state = GoalSpecFamilyState.model_validate(
        {
            "goal_id": "IDEA-SHADOW-001",
            "source_idea_path": "agents/ideas/raw/goal.md",
            "family_phase": "initial_family",
            "family_complete": False,
            "active_spec_id": "SPEC-BETA",
            "spec_order": ["SPEC-ALPHA", "SPEC-BETA"],
            "specs": {
                "SPEC-ALPHA": {
                    "status": "emitted",
                    "title": "Alpha",
                    "decomposition_profile": "moderate",
                },
                "SPEC-BETA": {
                    "status": "planned",
                    "title": "Beta",
                    "decomposition_profile": "moderate",
                    "depends_on_specs": ["SPEC-ALPHA"],
                },
            },
        }
    )
    governor = resolve_family_governor_state(
        paths=python_paths,
        current_state=python_current_state,
        policy_payload={"initial_family_max_specs": 2, "remediation_family_max_specs": 5},
    )
    guarded_state = python_current_state.model_copy(update={"family_governor": governor})
    proposed_specs = dict(guarded_state.specs)
    proposed_specs["SPEC-GAMMA"] = GoalSpecFamilySpecState(
        status="planned",
        title="Gamma",
        decomposition_profile="simple",
        depends_on_specs=("SPEC-BETA",),
    )
    python_decision = evaluate_initial_family_plan_guard(
        current_state=guarded_state,
        candidate_spec_id="SPEC-GAMMA",
        proposed_spec_order=("SPEC-ALPHA", "SPEC-BETA", "SPEC-GAMMA"),
        proposed_specs=proposed_specs,
    )

    return {
        "name": "family_governor_cap",
        "bash_reference_source": "live_reference_subprocess",
        "shared": {
            "family_phase": "initial_family",
            "applied_family_max_specs": 2,
            "overflow_candidate": "SPEC-GAMMA",
            "overflow_admitted": False,
            "active_family_size_after_enforcement": 2,
        },
        "bash_reference": {
            "budget_hit": bash_summary["budget_hit"],
            "deferred_spec_ids": bash_summary["deferred_spec_ids"],
            "overflow_registry_written": bash_summary["overflow_registry_written"],
            "active_spec_count_after": len(bash_state_after["spec_order"]),
            "registry_deferred_spec_ids": [item["spec_id"] for item in bash_registry["deferred_specs"]],
        },
        "python_runtime": {
            "action": python_decision.action,
            "reason": python_decision.reason,
            "violation_codes": list(python_decision.violation_codes),
            "proposed_spec_count": python_decision.proposed_spec_count,
        },
        "difference_codes": ["overflow_handling_strategy"],
        "difference_explanations": [
            "The bash reference defers overflow specs into a follow-on registry, while the Python runtime blocks the extra spec at the initial-family guard boundary instead of mutating state in place."
        ],
    }


def _build_staging_sync_comparison(tmp_path: Path) -> dict[str, object]:
    workspace, config_path = _materialize_shadow_mode_workspace(tmp_path, suffix="sync-workspace")
    manifest_path = workspace / "agents" / "staging_manifest.yml"

    bash_repo_dir = _init_pushable_staging_repo(tmp_path, name="bash-sync-staging")
    bash_result = _run(
        [
            "bash",
            (REFERENCE_TOOLS_DIR / "staging_sync.sh").as_posix(),
            workspace.as_posix(),
            manifest_path.as_posix(),
            bash_repo_dir.as_posix(),
        ],
        cwd=workspace,
    )
    assert bash_result.returncode == 0, bash_result.stderr
    bash_sync = _parse_bash_sync_stdout(bash_result.stdout)

    python_repo_dir = _init_pushable_staging_repo(tmp_path, name="python-sync-staging")
    paths = build_runtime_paths(load_engine_config(config_path).config)
    python_sync = sync_staging_repo(paths, staging_repo_dir=python_repo_dir)

    shared_entries = [{"path": "README.md", "action": "synced"}]
    return {
        "name": "staging_manifest_sync",
        "bash_reference_source": "live_reference_subprocess",
        "shared": {
            "required_paths": ["README.md"],
            "entries": shared_entries,
            "created_staging_dir": False,
        },
        "bash_reference": {
            "entries": bash_sync["entries"],
            "complete_line": bash_sync["complete_line"],
        },
        "python_runtime": {
            "manifest_source_kind": python_sync.selection.manifest_source_kind,
            "entries": _normalize_python_sync_entries(python_sync.entries),
            "created_staging_dir": python_sync.created_staging_dir,
        },
        "difference_codes": [],
        "difference_explanations": [],
    }


def _build_publish_commit_comparison(tmp_path: Path) -> dict[str, object]:
    workspace, config_path = _materialize_shadow_mode_workspace(tmp_path, suffix="publish-workspace")
    manifest_path = workspace / "agents" / "staging_manifest.yml"

    bash_repo_dir = _init_pushable_staging_repo(tmp_path, name="bash-publish-staging")
    bash_sync = _run(
        [
            "bash",
            (REFERENCE_TOOLS_DIR / "staging_sync.sh").as_posix(),
            workspace.as_posix(),
            manifest_path.as_posix(),
            bash_repo_dir.as_posix(),
        ],
        cwd=workspace,
    )
    assert bash_sync.returncode == 0, bash_sync.stderr
    bash_commit = _run(
        [
            "bash",
            (REFERENCE_TOOLS_DIR / "staging_commit.sh").as_posix(),
            bash_repo_dir.as_posix(),
            PUBLISH_COMMIT_MESSAGE,
        ],
        cwd=workspace,
    )
    assert bash_commit.returncode == 0, bash_commit.stderr

    python_repo_dir = _init_pushable_staging_repo(tmp_path, name="python-publish-staging")
    paths = build_runtime_paths(load_engine_config(config_path).config)
    sync_staging_repo(paths, staging_repo_dir=python_repo_dir)
    preflight = preflight_staging_publish(
        paths,
        staging_repo_dir=python_repo_dir,
        commit_message=PUBLISH_COMMIT_MESSAGE,
        push=True,
    )
    commit = commit_staging_repo(
        paths,
        staging_repo_dir=python_repo_dir,
        commit_message=PUBLISH_COMMIT_MESSAGE,
        push=True,
    )

    return {
        "name": "publish_preflight_commit",
        "bash_reference_source": "live_reference_subprocess",
        "shared": {
            "branch": "main",
            "publish_performed": True,
            "commit_marker": "PUSH_OK remote=origin branch=main",
        },
        "bash_reference": {
            "commit_exit_code": bash_commit.returncode,
            "commit_marker": _last_non_empty_line(bash_commit.stdout),
        },
        "python_runtime": {
            "preflight_status": preflight.status,
            "changed_paths": list(preflight.changed_paths),
            "commit_status": commit.status,
            "commit_marker": commit.marker,
        },
        "difference_codes": ["python_preflight_surface"],
        "difference_explanations": [
            "The bash reference exposes only the final commit/push marker, while the Python runtime adds an explicit preflight readiness surface before performing the same push-capable commit path."
        ],
    }


def _build_cutover_comparison(tmp_path: Path) -> dict[str, object]:
    cutover_contract = json.loads(
        (
            fixture_source(SHADOW_MODE_FIXTURE) / "bash_reference_autonomy_complete.json"
        ).read_text(encoding="utf-8")
    )

    workspace, config_path = _materialize_shadow_mode_workspace(tmp_path, suffix="cutover-workspace")
    _write_completion_reports(workspace)
    controller = EngineControl(config_path)
    status_report = controller.status(detail=True)
    assert status_report.research is not None
    completion_state = status_report.research.completion_state

    return {
        "name": "autonomy_complete_marker",
        "bash_reference_source": "reproducible_reference_probe_contract",
        "bash_reference_provenance": {
            "contract_source": cutover_contract["contract_source"],
            "source_script_path": cutover_contract["source_script_path"],
            "source_fixture_path": cutover_contract["source_fixture_path"],
            "represented_commands": cutover_contract["represented_commands"],
            "expected_exit_codes": cutover_contract["expected_exit_codes"],
            "live_execution_in_qa_ci": cutover_contract["live_execution_in_qa_ci"],
            "live_execution_not_used_reason": cutover_contract["live_execution_not_used_reason"],
            "probe_observations": cutover_contract["probe_observations"],
        },
        "shared": {
            "marker_present": True,
            "completion_respected": True,
        },
        "bash_reference": {
            "scenario_expected_orchestrate_exit_code": cutover_contract["expected_exit_codes"]["orchestrate_loop"],
            "scenario_expected_research_exit_code": cutover_contract["expected_exit_codes"]["research_loop"],
            "scenario_expected_marker_logged": cutover_contract["marker_logged"],
        },
        "python_runtime": {
            "completion_allowed": completion_state.completion_allowed,
            "marker_honored": completion_state.marker_honored,
            "reason": completion_state.reason,
        },
        "difference_codes": ["split_loop_exit_semantics"],
        "difference_explanations": [
            "The bash reference reports separate orchestrate and research loop exits, while the Python runtime exposes the authoritative completion state through its control-plane report after the audit pass is present."
        ],
    }


def build_shadow_mode_equivalence_report(tmp_path: Path) -> dict[str, object]:
    comparisons = [
        _build_governance_comparison(tmp_path / "governance"),
        _build_staging_sync_comparison(tmp_path / "sync"),
        _build_publish_commit_comparison(tmp_path / "publish"),
        _build_cutover_comparison(tmp_path / "cutover"),
    ]
    return {
        "scenario": "shadow_mode_equivalence",
        "result": "equivalent_with_bounded_differences",
        "allowed_difference_codes": [
            "overflow_handling_strategy",
            "python_preflight_surface",
            "split_loop_exit_semantics",
        ],
        "comparisons": comparisons,
    }
