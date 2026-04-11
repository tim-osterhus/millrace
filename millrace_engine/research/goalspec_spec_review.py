"""GoalSpec spec-review stage executor."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

from ..assets.resolver import AssetResolver, AssetSourceKind
from ..compiler_models import FrozenStagePlan
from ..config import EngineConfig, StageConfig
from ..contracts import ResearchStatus, RunnerKind, StageContext, StageType
from ..markdown import write_text_atomic
from ..paths import RuntimePaths
from ..runner import ClaudeRunner, CodexRunner, SubprocessRunner, detect_last_marker
from .goalspec import (
    GoalSpecReviewBlockedError,
    SpecReviewExecutionResult,
    SpecReviewRemediationExecutionResult,
)
from .goalspec_helpers import (
    GoalSpecExecutionError,
    _load_json_model,
    _load_json_object,
    _markdown_section,
    _relative_path,
    _resolve_path_token,
    _spec_id_for_goal,
    _utcnow,
    _write_json_model,
    resolve_goal_source,
)
from .goalspec_persistence import (
    _build_goal_spec_review_state,
    _load_objective_profile_inputs,
    load_objective_state_contractor_profile,
    _stable_spec_paths_for_review,
)
from .goalspec_product_planning import (
    find_abstract_phase_steps,
    is_product_surface_path,
    minimum_phase_package_count,
    minimum_phase_step_count,
)
from .goalspec_scope_diagnostics import infer_goal_scope_kind
from .goalspec_stage_rendering import (
    render_spec_review_decision,
    render_spec_review_questions,
)
from .specs import (
    GoalSpecLineageRecord,
    GoalSpecReviewRemediationBundle,
    GoalSpecReviewFinding,
    GoalSpecReviewRecord,
    GoalSpecFamilySpecState,
    GoalSpecFamilyState,
    load_goal_spec_family_state,
    load_goal_spec_review_remediation_bundle,
    load_stable_spec_registry,
    refresh_stable_spec_registry,
    stable_spec_metadata_from_file,
    write_goal_spec_family_state,
)
from .state import ResearchCheckpoint, ResearchQueueFamily, ResearchQueueOwnership
from .governance import evaluate_initial_family_plan_guard

_NUMBERED_LINE_RE = re.compile(r"^\d+\.\s+(.*\S)\s*$")
_BACKTICKED_TOKEN_RE = re.compile(r"`([^`\n]+)`")
_PHASE_KEY_LINE_RE = re.compile(r"^- Phase key:\s+`(PHASE_[0-9]{2})`\s*$")
_EPIC_PHASE_STEP_HINTS = (
    "whole project",
    "whole-project",
    "whole suite",
    "whole-suite",
    "entire project",
    "entire repo",
    "whole repo",
    "entire campaign",
    "all remaining",
    "full suite",
    "full acceptance sweep",
    "entire acceptance sweep",
    "everything in this phase",
    "do everything in this phase",
    "whole gate",
    "whole-gate",
    "repo-wide",
)


def _bound_spec_review_stage_config(
    config: EngineConfig,
    *,
    stage_plan: FrozenStagePlan | None,
) -> StageConfig:
    base = config.stages[StageType.SPEC_REVIEW]
    prompt_file = base.prompt_file
    if stage_plan is not None and stage_plan.prompt_asset_ref is not None:
        prompt_file = Path(stage_plan.prompt_asset_ref)
    return StageConfig(
        runner=stage_plan.runner if stage_plan is not None and stage_plan.runner is not None else base.runner,
        model=stage_plan.model if stage_plan is not None and stage_plan.model is not None else base.model,
        effort=stage_plan.effort if stage_plan is not None and stage_plan.effort is not None else base.effort,
        permission_profile=(
            stage_plan.permission_profile
            if stage_plan is not None and stage_plan.permission_profile is not None
            else base.permission_profile
        ),
        timeout_seconds=(
            stage_plan.timeout_seconds
            if stage_plan is not None and stage_plan.timeout_seconds is not None
            else base.timeout_seconds
        ),
        prompt_file=prompt_file,
        allow_search=(
            stage_plan.allow_search
            if stage_plan is not None and stage_plan.allow_search is not None
            else base.allow_search
        ),
    )


def _goalspec_review_remediation_bundle_path(paths: RuntimePaths, *, run_id: str) -> Path:
    return paths.goalspec_runtime_dir / "spec_review_remediation" / f"{run_id}.json"


def _goalspec_mechanic_record_path(paths: RuntimePaths, *, run_id: str) -> Path:
    return paths.goalspec_runtime_dir / "mechanic" / f"{run_id}.json"


def _resolve_spec_review_prompt(
    paths: RuntimePaths,
    *,
    stage_config: StageConfig,
    stage_plan: FrozenStagePlan | None,
) -> tuple[str, Path | None, str]:
    if stage_plan is not None and stage_plan.prompt_asset is not None:
        prompt_path = stage_plan.prompt_asset.workspace_path
        prompt_text = prompt_path.read_text(encoding="utf-8").strip()
        prompt_ref = stage_plan.prompt_asset.resolved_ref
        if stage_plan.prompt_asset.source_kind is AssetSourceKind.WORKSPACE:
            return prompt_text, prompt_path, prompt_ref
        return prompt_text, None, prompt_ref

    prompt_file = stage_config.prompt_file
    if prompt_file is None:
        raise GoalSpecExecutionError("Spec Review stage config is missing its prompt asset")
    resolved = AssetResolver(paths.root).resolve_file(prompt_file)
    prompt_text = resolved.read_text(encoding="utf-8").strip()
    prompt_path = resolved.prompt_path if resolved.source_kind is AssetSourceKind.WORKSPACE else None
    return prompt_text, prompt_path, resolved.resolved_ref


def _agentic_spec_review_marker(
    paths: RuntimePaths,
    marker: str | None,
    raw_marker_line: str | None,
) -> tuple[str | None, str | None]:
    if marker is not None:
        return marker, raw_marker_line
    if not paths.research_status_file.exists():
        return None, None
    return detect_last_marker(paths.research_status_file.read_text(encoding="utf-8", errors="replace"))


def _execute_agentic_spec_review_stage(
    paths: RuntimePaths,
    *,
    config: EngineConfig,
    run_id: str,
    stage_plan: FrozenStagePlan | None = None,
):
    stage_config = _bound_spec_review_stage_config(config, stage_plan=stage_plan)
    prompt_text, prompt_path, prompt_ref = _resolve_spec_review_prompt(
        paths,
        stage_config=stage_config,
        stage_plan=stage_plan,
    )
    prompt = "\n\n".join(
        (
            prompt_text,
            f"Stage: {StageType.SPEC_REVIEW.value}",
            f"Prompt asset: {prompt_ref}",
        )
    ).rstrip("\n") + "\n"
    runners = {
        RunnerKind.SUBPROCESS: SubprocessRunner(paths),
        RunnerKind.CODEX: CodexRunner(paths),
        RunnerKind.CLAUDE: ClaudeRunner(paths),
    }
    context = StageContext.model_validate(
        {
            "stage": StageType.SPEC_REVIEW,
            "runner": stage_config.runner,
            "model": stage_config.model,
            "prompt": prompt,
            "working_dir": paths.root,
            "run_id": run_id,
            "permission_profile": stage_config.permission_profile,
            "timeout_seconds": stage_config.timeout_seconds,
            "prompt_path": prompt_path,
            "status_fallback_path": paths.research_status_file,
            "allow_search": stage_config.allow_search,
            "allow_network": True,
            "effort": stage_config.effort,
        }
    )
    runner_result = runners[stage_config.runner].execute(context)
    detected_marker, raw_marker_line = _agentic_spec_review_marker(
        paths,
        runner_result.detected_marker,
        runner_result.raw_marker_line,
    )
    if detected_marker != runner_result.detected_marker or raw_marker_line != runner_result.raw_marker_line:
        runner_result = runner_result.model_copy(
            update={
                "detected_marker": detected_marker,
                "raw_marker_line": raw_marker_line,
            }
        )
    if runner_result.exit_code != 0:
        raise GoalSpecExecutionError(
            f"Agentic Spec Review runner exited {runner_result.exit_code} before runtime promotion"
        )
    if detected_marker == ResearchStatus.BLOCKED.value:
        raise GoalSpecExecutionError("Agentic Spec Review blocked before runtime promotion")
    if detected_marker != ResearchStatus.IDLE.value:
        raise GoalSpecExecutionError(
            "Agentic Spec Review did not report a successful terminal marker before runtime promotion"
        )
    return runner_result


def _execute_goalspec_mechanic_stage(
    paths: RuntimePaths,
    *,
    config: EngineConfig,
    run_id: str,
    remediation_bundle: GoalSpecReviewRemediationBundle,
):
    stage_config = config.stages[StageType.MECHANIC]
    prompt_text, prompt_path, prompt_ref = _resolve_spec_review_prompt(
        paths,
        stage_config=stage_config,
        stage_plan=None,
    )
    prompt = "\n\n".join(
        (
            prompt_text,
            f"Stage: {StageType.MECHANIC.value}",
            f"Prompt asset: {prompt_ref}",
            "Review remediation bundle:",
            f"- Bundle: {_goalspec_review_remediation_bundle_path(paths, run_id=run_id).relative_to(paths.root).as_posix()}",
            f"- Queue spec: {remediation_bundle.queue_spec_path}",
            f"- Review record: {remediation_bundle.review_record_path}",
            f"- Decision: {remediation_bundle.decision_path}",
            f"- Questions: {remediation_bundle.questions_path}",
            f"- Family state: {remediation_bundle.family_state_path}",
            f"- Stable registry: {remediation_bundle.stable_registry_path}",
            "Allowed edit scope:",
            *(f"- {path}" for path in remediation_bundle.allowed_edit_paths),
            "If you repair the structural issue, keep the runtime ready for Spec Review to rerun next.",
        )
    ).rstrip("\n") + "\n"
    runners = {
        RunnerKind.SUBPROCESS: SubprocessRunner(paths),
        RunnerKind.CODEX: CodexRunner(paths),
        RunnerKind.CLAUDE: ClaudeRunner(paths),
    }
    context = StageContext.model_validate(
        {
            "stage": StageType.MECHANIC,
            "runner": stage_config.runner,
            "model": stage_config.model,
            "prompt": prompt,
            "working_dir": paths.root,
            "run_id": run_id,
            "permission_profile": stage_config.permission_profile,
            "timeout_seconds": stage_config.timeout_seconds,
            "prompt_path": prompt_path,
            "status_fallback_path": paths.research_status_file,
            "allow_search": stage_config.allow_search,
            "allow_network": True,
            "effort": stage_config.effort,
        }
    )
    runner_result = runners[stage_config.runner].execute(context)
    detected_marker, raw_marker_line = _agentic_spec_review_marker(
        paths,
        runner_result.detected_marker,
        runner_result.raw_marker_line,
    )
    if detected_marker != runner_result.detected_marker or raw_marker_line != runner_result.raw_marker_line:
        runner_result = runner_result.model_copy(
            update={
                "detected_marker": detected_marker,
                "raw_marker_line": raw_marker_line,
            }
        )
    if runner_result.exit_code != 0:
        raise GoalSpecExecutionError(f"Mechanic runner exited {runner_result.exit_code} before repair handoff")
    if detected_marker == ResearchStatus.BLOCKED.value:
        raise GoalSpecExecutionError("Mechanic blocked during bounded review remediation")
    if detected_marker != ResearchStatus.IDLE.value:
        raise GoalSpecExecutionError("Mechanic did not report a successful terminal marker before Spec Review rerun")
    return runner_result


def _review_remediation_allowed_edit_paths(
    paths: RuntimePaths,
    *,
    queue_spec_path: Path,
    questions_path: Path,
    decision_path: Path,
    lineage_path: Path,
) -> tuple[str, ...]:
    return tuple(
        _relative_path(path, relative_to=paths.root)
        for path in (
            queue_spec_path,
            questions_path,
            decision_path,
            lineage_path,
            paths.goal_spec_family_state_file,
            paths.specs_index_file,
            paths.research_status_file,
            paths.historylog_file,
            paths.agents_dir / "mechanic_report.md",
            paths.diagnostics_dir,
        )
    )


def _write_review_remediation_bundle(
    paths: RuntimePaths,
    *,
    run_id: str,
    emitted_at: datetime,
    spec_id: str,
    goal_id: str,
    title: str,
    record_path: Path,
    questions_path: Path,
    decision_path: Path,
    queue_spec_path: Path,
    reviewed_path: Path,
    lineage_path: Path,
    findings: tuple[GoalSpecReviewFinding, ...],
) -> GoalSpecReviewRemediationBundle:
    bundle_path = _goalspec_review_remediation_bundle_path(paths, run_id=run_id)
    existing_attempt_count = 0
    existing_report_path = ""
    existing_run_id = ""
    existing_status = "pending"
    if bundle_path.exists():
        existing = load_goal_spec_review_remediation_bundle(bundle_path)
        existing_attempt_count = existing.mechanic_attempt_count
        existing_report_path = existing.last_mechanic_report_path
        existing_run_id = existing.last_mechanic_run_id
        existing_status = existing.last_mechanic_status
    bundle = GoalSpecReviewRemediationBundle(
        run_id=run_id,
        emitted_at=emitted_at,
        spec_id=spec_id,
        goal_id=goal_id,
        title=title,
        review_status="blocked",
        remediation_status="pending",
        review_record_path=_relative_path(record_path, relative_to=paths.root),
        questions_path=_relative_path(questions_path, relative_to=paths.root),
        decision_path=_relative_path(decision_path, relative_to=paths.root),
        queue_spec_path=_relative_path(queue_spec_path, relative_to=paths.root),
        reviewed_path=(
            _relative_path(reviewed_path, relative_to=paths.root)
            if reviewed_path.exists()
            else ""
        ),
        lineage_path=_relative_path(lineage_path, relative_to=paths.root),
        family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
        stable_registry_path=_relative_path(paths.specs_index_file, relative_to=paths.root),
        allowed_edit_paths=_review_remediation_allowed_edit_paths(
            paths,
            queue_spec_path=queue_spec_path,
            questions_path=questions_path,
            decision_path=decision_path,
            lineage_path=lineage_path,
        ),
        findings=findings,
        mechanic_attempt_count=existing_attempt_count,
        last_mechanic_run_id=existing_run_id,
        last_mechanic_status=existing_status,
        last_mechanic_report_path=existing_report_path,
    )
    _write_json_model(bundle_path, bundle)
    return bundle


def _phase_steps(phase_text: str) -> tuple[str, ...]:
    work_plan = _markdown_section(phase_text, "Work Plan")
    steps: list[str] = []
    for raw_line in work_plan.splitlines():
        match = _NUMBERED_LINE_RE.match(raw_line.strip())
        if match is None:
            continue
        steps.append(" ".join(match.group(1).split()))
    return tuple(steps)


def _phase_package_keys(phase_text: str) -> tuple[str, ...]:
    phase_packages = _markdown_section(phase_text, "Phase Packages")
    keys: list[str] = []
    seen: set[str] = set()
    for raw_line in phase_packages.splitlines():
        match = _PHASE_KEY_LINE_RE.match(raw_line.strip())
        if match is None:
            continue
        phase_key = match.group(1)
        if phase_key in seen:
            continue
        seen.add(phase_key)
        keys.append(phase_key)
    return tuple(keys)


def _epic_phase_steps(steps: tuple[str, ...]) -> tuple[str, ...]:
    findings: list[str] = []
    for step in steps:
        lowered = step.casefold()
        if any(token in lowered for token in _EPIC_PHASE_STEP_HINTS):
            findings.append(step)
    return tuple(findings)


def _repo_paths(text: str) -> tuple[str, ...]:
    paths: list[str] = []
    for match in _BACKTICKED_TOKEN_RE.finditer(text):
        token = match.group(1).strip()
        if not token or " " in token or token.startswith(("http://", "https://", "/", "~")):
            continue
        if "/" not in token and "." not in Path(token).name:
            continue
        paths.append(token)
    deduped: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return tuple(deduped)


def _review_text_lines(*texts: str) -> tuple[str, ...]:
    lines: list[str] = []
    for text in texts:
        for raw_line in text.splitlines():
            line = " ".join(raw_line.split())
            if line:
                lines.append(line)
    return tuple(lines)


def _review_finding(
    *,
    finding_id: str,
    severity: str,
    summary: str,
    remediation_intent: str = "none",
    queue_spec_path: Path,
    paths: RuntimePaths,
) -> GoalSpecReviewFinding:
    return GoalSpecReviewFinding(
        finding_id=finding_id,
        severity=severity,
        summary=summary,
        remediation_intent=remediation_intent,
        artifact_path=_relative_path(queue_spec_path, relative_to=paths.root),
    )


def _promotability_findings(
    *,
    family_state: GoalSpecFamilyState,
    spec_id: str,
    spec_state: GoalSpecFamilySpecState,
    queue_spec_path: Path,
    paths: RuntimePaths,
) -> tuple[GoalSpecReviewFinding, ...]:
    findings: list[GoalSpecReviewFinding] = []
    if spec_state.status != "emitted":
        findings.append(
            _review_finding(
                finding_id="REV-PROMOTION-STATE",
                severity="blocker",
                summary=(
                    f"Spec `{spec_id}` is not promotable from family state because its status is "
                    f"`{spec_state.status or 'unknown'}` instead of `emitted`."
                ),
                queue_spec_path=queue_spec_path,
                paths=paths,
            )
        )
    if family_state.active_spec_id and family_state.active_spec_id != spec_id:
        findings.append(
            _review_finding(
                finding_id="REV-ACTIVE-SPEC-MISMATCH",
                severity="blocker",
                summary=(
                    f"Spec `{spec_id}` is not the active family member for promotion; "
                    f"`{family_state.active_spec_id}` is still marked active."
                ),
                queue_spec_path=queue_spec_path,
                paths=paths,
            )
        )
    queue_path = _relative_path(queue_spec_path, relative_to=paths.root)
    if spec_state.queue_path and spec_state.queue_path != queue_path:
        findings.append(
            _review_finding(
                finding_id="REV-QUEUE-PATH-MISMATCH",
                severity="blocker",
                summary=(
                    f"Family state still points `{spec_id}` at queue artifact `{spec_state.queue_path}`, "
                    f"but Spec Review is running against `{queue_path}`."
                ),
                queue_spec_path=queue_spec_path,
                paths=paths,
            )
        )
    return tuple(findings)


def _stable_artifact_findings(
    *,
    paths: RuntimePaths,
    spec_id: str,
    expected_decomposition_profile: str,
    stable_spec_paths: tuple[Path, ...],
    stable_spec_error: str | None,
    queue_spec_path: Path,
) -> tuple[GoalSpecReviewFinding, ...]:
    findings: list[GoalSpecReviewFinding] = []
    if stable_spec_error is not None or not stable_spec_paths:
        findings.append(
            _review_finding(
                finding_id="REV-STABLE-ARTIFACTS-MISSING",
                severity="blocker",
                summary=(
                    f"Stable review artifacts are incomplete for `{spec_id}`: "
                    f"{stable_spec_error or 'expected frozen golden and phase specs before promotion'}."
                ),
                queue_spec_path=queue_spec_path,
                paths=paths,
            )
        )
        return tuple(findings)

    seen_tiers: set[str] = set()
    for stable_path in stable_spec_paths:
        stable_token = _relative_path(stable_path, relative_to=paths.root)
        if "/golden/" in stable_token:
            seen_tiers.add("golden")
        if "/phase/" in stable_token:
            seen_tiers.add("phase")
        try:
            metadata = stable_spec_metadata_from_file(stable_path, relative_to=paths.root)
        except ValueError as exc:
            findings.append(
                _review_finding(
                    finding_id="REV-STABLE-ARTIFACT-METADATA",
                    severity="blocker",
                    summary=f"Stable review artifact `{stable_token}` has invalid frontmatter: {exc}",
                    queue_spec_path=queue_spec_path,
                    paths=paths,
                )
            )
            continue
        if metadata.spec_id != spec_id:
            findings.append(
                _review_finding(
                    finding_id="REV-STABLE-SPEC-ID-MISMATCH",
                    severity="blocker",
                    summary=(
                        f"Stable review artifact `{stable_token}` declares spec id `{metadata.spec_id}`, "
                        f"expected `{spec_id}`."
                    ),
                    queue_spec_path=queue_spec_path,
                    paths=paths,
                )
            )
        if metadata.decomposition_profile and metadata.decomposition_profile != expected_decomposition_profile:
            findings.append(
                _review_finding(
                    finding_id="REV-STABLE-DECOMPOSITION-PROFILE",
                    severity="blocker",
                    summary=(
                        f"Stable review artifact `{stable_token}` declares decomposition profile "
                        f"`{metadata.decomposition_profile or 'missing'}`, expected "
                        f"`{expected_decomposition_profile or 'simple'}`."
                    ),
                    queue_spec_path=queue_spec_path,
                    paths=paths,
                )
            )

    for required_tier in ("golden", "phase"):
        if required_tier in seen_tiers:
            continue
        findings.append(
            _review_finding(
                finding_id="REV-STABLE-ARTIFACT-TIER",
                severity="blocker",
                summary=(
                    f"Stable review artifacts for `{spec_id}` are missing the required `{required_tier}` copy."
                ),
                queue_spec_path=queue_spec_path,
                paths=paths,
            )
        )
    return tuple(findings)


def _frozen_family_integrity_findings(
    *,
    family_state: GoalSpecFamilyState,
    spec_id: str,
    queue_spec_path: Path,
    paths: RuntimePaths,
) -> tuple[GoalSpecReviewFinding, ...]:
    decision = evaluate_initial_family_plan_guard(
        current_state=family_state,
        candidate_spec_id=spec_id,
        proposed_spec_order=family_state.spec_order,
        proposed_specs=family_state.specs,
    )
    if decision.action != "block":
        return ()
    summary_parts = [
        "Frozen initial family plan no longer matches the live family state before promotion",
    ]
    if decision.mutated_spec_ids:
        summary_parts.append(f"mutated specs: {', '.join(f'`{item}`' for item in decision.mutated_spec_ids)}")
    if decision.violation_codes:
        summary_parts.append(f"violations: {', '.join(f'`{item}`' for item in decision.violation_codes)}")
    return (
        _review_finding(
            finding_id="REV-FROZEN-FAMILY-INTEGRITY",
            severity="blocker",
            summary="; ".join(summary_parts) + ".",
            queue_spec_path=queue_spec_path,
            paths=paths,
        ),
    )


def _review_findings(
    *,
    paths: RuntimePaths,
    spec_id: str,
    family_state: GoalSpecFamilyState,
    spec_state: GoalSpecFamilySpecState,
    source_body: str,
    source_title: str,
    decomposition_profile: str,
    stable_spec_paths: tuple[Path, ...],
    stable_spec_error: str | None,
    queue_spec_path: Path,
    queue_spec_text: str,
) -> tuple[GoalSpecReviewFinding, ...]:
    phase_paths = tuple(path for path in stable_spec_paths if "/phase/" in _relative_path(path, relative_to=paths.root))
    phase_steps: list[str] = []
    phase_path_tokens: list[str] = []
    phase_package_keys: list[str] = []
    for phase_path in phase_paths:
        phase_text = phase_path.read_text(encoding="utf-8")
        phase_steps.extend(_phase_steps(phase_text))
        phase_path_tokens.extend(_repo_paths(phase_text))
        phase_package_keys.extend(_phase_package_keys(phase_text))

    findings: list[GoalSpecReviewFinding] = []
    minimum_steps = minimum_phase_step_count(decomposition_profile)
    if len(phase_steps) < minimum_steps:
        findings.append(
            GoalSpecReviewFinding(
                finding_id="REV-DECOMPOSITION-DENSITY",
                severity="blocker",
                summary=(
                    f"Phase package defines {len(phase_steps)} numbered Work Plan step(s), "
                    f"below the active `{decomposition_profile or 'simple'}` floor of {minimum_steps}."
                ),
                artifact_path=_relative_path(phase_paths[0], relative_to=paths.root) if phase_paths else "",
            )
        )

    minimum_packages = minimum_phase_package_count(decomposition_profile)
    declared_package_count = len(tuple(dict.fromkeys(phase_package_keys)))
    package_count = declared_package_count or (1 if phase_paths else 0)
    if package_count < minimum_packages:
        findings.append(
            GoalSpecReviewFinding(
                finding_id="REV-PHASE-PACKAGE-COUNT",
                severity="blocker",
                summary=(
                    f"Phase package set defines {package_count} phase package(s), "
                    f"below the active `{decomposition_profile or 'simple'}` floor of {minimum_packages}; "
                    "this campaign should split into dependent queue specs instead of one giant package."
                ),
                artifact_path=_relative_path(phase_paths[0], relative_to=paths.root) if phase_paths else "",
            )
        )

    abstract_steps = find_abstract_phase_steps(tuple(phase_steps))
    if abstract_steps:
        findings.append(
            GoalSpecReviewFinding(
                finding_id="REV-ABSTRACT-PHASE-STEPS",
                severity="blocker",
                summary=(
                    "Phase plan still contains abstract or handoff-oriented work items: "
                    + "; ".join(abstract_steps[:3])
                ),
                artifact_path=_relative_path(phase_paths[0], relative_to=paths.root) if phase_paths else "",
            )
        )

    epic_steps = _epic_phase_steps(tuple(phase_steps))
    if epic_steps:
        findings.append(
            GoalSpecReviewFinding(
                finding_id="REV-EXECUTION-EPIC-PHASE-STEPS",
                severity="blocker",
                summary=(
                    "Phase plan still contains execution-epic or whole-project/gate work items: "
                    + "; ".join(epic_steps[:3])
                ),
                artifact_path=_relative_path(phase_paths[0], relative_to=paths.root) if phase_paths else "",
            )
        )

    objective_state, profile = _load_objective_profile_inputs(paths)
    load_objective_state_contractor_profile(paths, objective_state)
    expected_scope = infer_goal_scope_kind(
        title=source_title,
        source_body=source_body,
        semantic_summary=profile.semantic_profile.objective_summary,
        capability_domains=tuple(profile.semantic_profile.capability_domains),
    )
    if expected_scope == "product":
        completion_manifest_payload = (
            _load_json_object(paths.audit_completion_manifest_file)
            if paths.audit_completion_manifest_file.exists()
            else {}
        )
        implementation_surfaces = tuple(
            str(item.get("path", "")).strip()
            for item in completion_manifest_payload.get("implementation_surfaces", [])
            if isinstance(item, dict)
        )
        verification_surfaces = tuple(
            str(item.get("path", "")).strip()
            for item in completion_manifest_payload.get("verification_surfaces", [])
            if isinstance(item, dict)
        )
        product_surface_paths = tuple(
            path
            for path in (*implementation_surfaces, *verification_surfaces, *phase_path_tokens)
            if path
        )
        if not any(is_product_surface_path(path) for path in product_surface_paths):
            findings.append(
                GoalSpecReviewFinding(
                    finding_id="REV-PRODUCT-SURFACES",
                    severity="blocker",
                    summary=(
                        "Open product objective still lacks concrete non-`agents/*` implementation or "
                        "verification surfaces in the manifest and phase package."
                    ),
                    artifact_path=_relative_path(phase_paths[0], relative_to=paths.root) if phase_paths else "",
                )
            )
        if not implementation_surfaces or not verification_surfaces:
            findings.append(
                GoalSpecReviewFinding(
                    finding_id="REV-SURFACE-SEPARATION",
                    severity="blocker",
                    summary=(
                        "Completion manifest is missing explicit implementation or verification surfaces, "
                        "so Taskmaster would still need to guess product scope."
                    ),
                    artifact_path=_relative_path(paths.audit_completion_manifest_file, relative_to=paths.root),
                )
            )

    findings.extend(
        _promotability_findings(
            family_state=family_state,
            spec_id=spec_id,
            spec_state=spec_state,
            queue_spec_path=queue_spec_path,
            paths=paths,
        )
    )
    findings.extend(
        _stable_artifact_findings(
            queue_spec_path=queue_spec_path,
            spec_id=spec_id,
            expected_decomposition_profile=decomposition_profile,
            stable_spec_paths=stable_spec_paths,
            stable_spec_error=stable_spec_error,
            paths=paths,
        )
    )
    findings.extend(
        _frozen_family_integrity_findings(
            family_state=family_state,
            spec_id=spec_id,
            queue_spec_path=queue_spec_path,
            paths=paths,
        )
    )

    return tuple(findings)


def execute_spec_review_remediation(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    emitted_at: datetime | None = None,
    config: EngineConfig | None = None,
) -> SpecReviewRemediationExecutionResult:
    """Run one bounded Mechanic pass for a persisted blocked-review remediation bundle."""

    if config is None:
        raise GoalSpecExecutionError("Mechanic remediation requires engine config")
    bundle_path = _goalspec_review_remediation_bundle_path(paths, run_id=run_id)
    if not bundle_path.exists():
        raise GoalSpecExecutionError(f"Missing Spec Review remediation bundle for {run_id}")
    bundle = load_goal_spec_review_remediation_bundle(bundle_path)
    _write_json_model(
        bundle_path,
        bundle.model_copy(
            update={
                "remediation_status": "repairing",
                "last_mechanic_run_id": run_id,
                "last_mechanic_status": "repairing",
            }
        ),
    )
    try:
        _execute_goalspec_mechanic_stage(
            paths,
            config=config,
            run_id=run_id,
            remediation_bundle=bundle,
        )
    except GoalSpecExecutionError:
        _write_json_model(
            bundle_path,
            bundle.model_copy(
                update={
                    "remediation_status": "blocked",
                    "last_mechanic_run_id": run_id,
                    "last_mechanic_status": "blocked",
                }
            ),
        )
        raise
    report_path = ""
    for candidate in (
        paths.agents_dir / "mechanic_report.md",
        paths.diagnostics_dir / run_id / "mechanic_report.md",
    ):
        if candidate.exists():
            report_path = _relative_path(candidate, relative_to=paths.root)
            break
    updated_bundle = bundle.model_copy(
        update={
            "remediation_status": "repaired",
            "mechanic_attempt_count": bundle.mechanic_attempt_count + 1,
            "last_mechanic_run_id": run_id,
            "last_mechanic_status": "repaired",
            "last_mechanic_report_path": report_path,
        }
    )
    _write_json_model(bundle_path, updated_bundle)
    record_path = _goalspec_mechanic_record_path(paths, run_id=run_id)
    _write_json_model(record_path, updated_bundle)
    queue_path = (
        checkpoint.owned_queues[0].queue_path
        if checkpoint.owned_queues
        else paths.ideas_specs_dir
    )
    item_path = (
        checkpoint.owned_queues[0].item_path
        if checkpoint.owned_queues
        else _resolve_path_token(bundle.queue_spec_path, relative_to=paths.root)
    )
    return SpecReviewRemediationExecutionResult(
        remediation_bundle_path=_relative_path(bundle_path, relative_to=paths.root),
        report_path=report_path,
        queue_ownership=ResearchQueueOwnership(
            family=ResearchQueueFamily.GOALSPEC,
            queue_path=queue_path,
            item_path=item_path,
            owner_token=run_id,
            acquired_at=emitted_at or _utcnow(),
        ),
    )


def execute_spec_review(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    emitted_at: datetime | None = None,
    config: EngineConfig | None = None,
    stage_plan: FrozenStagePlan | None = None,
) -> SpecReviewExecutionResult:
    """Promote one synthesized queue spec into reviewed state plus lineage/registry artifacts."""

    emitted_at = emitted_at or _utcnow()
    source = resolve_goal_source(paths, checkpoint)
    family_state = load_goal_spec_family_state(paths.goal_spec_family_state_file)
    spec_id = source.frontmatter.get("spec_id", "").strip() or _spec_id_for_goal(source.idea_id)
    spec_state = family_state.specs.get(spec_id)
    if spec_state is None:
        raise GoalSpecExecutionError(f"GoalSpec family state is missing {spec_id} during Spec Review")

    queue_spec_path = _resolve_path_token(spec_state.queue_path or source.relative_source_path, relative_to=paths.root)
    reviewed_path = paths.ideas_specs_reviewed_dir / Path(spec_state.queue_path or source.relative_source_path).name
    source_slug = Path(spec_state.queue_path or source.relative_source_path).stem
    questions_path = paths.specs_questions_dir / f"{source_slug}__spec-review.md"
    decision_path = paths.specs_decisions_dir / f"{source_slug}__spec-review.md"
    record_path = paths.goalspec_spec_review_records_dir / f"{run_id}.json"
    lineage_path = paths.goalspec_lineage_dir / f"{spec_id}.json"
    stable_spec_error: str | None = None
    try:
        stable_spec_paths = _stable_spec_paths_for_review(paths, spec_id=spec_id)
    except GoalSpecExecutionError as exc:
        stable_spec_paths = ()
        stable_spec_error = str(exc)
    relative_stable_spec_paths = tuple(_relative_path(path, relative_to=paths.root) for path in stable_spec_paths)
    review_timestamp = emitted_at
    queue_spec_text = _resolve_path_token(source.source_path, relative_to=paths.root).read_text(
        encoding="utf-8",
        errors="replace",
    )
    findings = _review_findings(
        paths=paths,
        spec_id=spec_id,
        family_state=family_state,
        spec_state=spec_state,
        source_body=source.body,
        source_title=source.title,
        decomposition_profile=source.decomposition_profile,
        stable_spec_paths=stable_spec_paths,
        stable_spec_error=stable_spec_error,
        queue_spec_path=queue_spec_path,
        queue_spec_text=queue_spec_text,
    )
    blocking_findings = tuple(finding for finding in findings if finding.severity == "blocker")
    if not blocking_findings and config is not None:
        _execute_agentic_spec_review_stage(
            paths,
            config=config,
            run_id=run_id,
            stage_plan=stage_plan,
        )
    review_status = "blocked" if blocking_findings else "no_material_delta"
    remediation_bundle_path = _goalspec_review_remediation_bundle_path(paths, run_id=run_id)
    remediation_bundle_relative_path = _relative_path(remediation_bundle_path, relative_to=paths.root)

    if (
        record_path.exists()
        and questions_path.exists()
        and decision_path.exists()
    ):
        existing_review_record = _load_json_model(record_path, GoalSpecReviewRecord)
        if existing_review_record.reviewed_at is not None:
            review_timestamp = existing_review_record.reviewed_at
        expected_questions = render_spec_review_questions(
            reviewed_at=review_timestamp,
            run_id=run_id,
            goal_id=source.idea_id,
            spec_id=spec_id,
            title=source.title,
            queue_spec_path=_relative_path(queue_spec_path, relative_to=paths.root),
            stable_spec_paths=relative_stable_spec_paths,
            findings=findings,
        )
        expected_decision = render_spec_review_decision(
            reviewed_at=review_timestamp,
            run_id=run_id,
            goal_id=source.idea_id,
            spec_id=spec_id,
            title=source.title,
            review_status=review_status,
            reviewed_path=(
                _relative_path(reviewed_path, relative_to=paths.root)
                if reviewed_path.exists()
                else _relative_path(queue_spec_path, relative_to=paths.root)
            ),
            stable_registry_path=_relative_path(paths.specs_index_file, relative_to=paths.root),
            lineage_path=_relative_path(lineage_path, relative_to=paths.root),
            findings=findings,
            remediation_bundle_path=(remediation_bundle_relative_path if blocking_findings else ""),
        )
        if blocking_findings:
            if (
                existing_review_record
                == GoalSpecReviewRecord(
                    spec_id=spec_id,
                    review_status=review_status,
                    questions_path=_relative_path(questions_path, relative_to=paths.root),
                    decision_path=_relative_path(decision_path, relative_to=paths.root),
                    reviewed_path="",
                    reviewed_at=review_timestamp,
                    findings=findings,
                )
                and questions_path.read_text(encoding="utf-8") == expected_questions
                and decision_path.read_text(encoding="utf-8") == expected_decision
            ):
                _write_review_remediation_bundle(
                    paths,
                    run_id=run_id,
                    emitted_at=review_timestamp,
                    spec_id=spec_id,
                    goal_id=source.idea_id,
                    title=source.title,
                    record_path=record_path,
                    questions_path=questions_path,
                    decision_path=decision_path,
                    queue_spec_path=queue_spec_path,
                    reviewed_path=reviewed_path,
                    lineage_path=lineage_path,
                    findings=findings,
                )
                raise GoalSpecReviewBlockedError(
                    f"Spec Review blocked {spec_id}; resolve the recorded decomposition findings before Taskmaster",
                    remediation_bundle_path=remediation_bundle_relative_path,
                )
        elif reviewed_path.exists() and lineage_path.exists() and paths.specs_index_file.exists():
            expected_family_state, lineage_record = _build_goal_spec_review_state(
                paths=paths,
                spec_id=spec_id,
                goal_id=source.idea_id,
                queue_spec_path=queue_spec_path,
                reviewed_path=reviewed_path,
                questions_path=questions_path,
                decision_path=decision_path,
                stable_spec_paths=stable_spec_paths,
                review_status=review_status,
                emitted_at=review_timestamp,
            )
            current_family_state = load_goal_spec_family_state(paths.goal_spec_family_state_file)
            stable_registry = load_stable_spec_registry(paths.specs_index_file)
            if (
                existing_review_record
                == GoalSpecReviewRecord(
                    spec_id=spec_id,
                    review_status=review_status,
                    questions_path=_relative_path(questions_path, relative_to=paths.root),
                    decision_path=_relative_path(decision_path, relative_to=paths.root),
                    reviewed_path=_relative_path(reviewed_path, relative_to=paths.root),
                    reviewed_at=review_timestamp,
                    findings=findings,
                )
                and GoalSpecLineageRecord.model_validate(_load_json_object(lineage_path)) == lineage_record
                and current_family_state == expected_family_state
                and questions_path.read_text(encoding="utf-8") == expected_questions
                and decision_path.read_text(encoding="utf-8") == expected_decision
                and reviewed_path.read_text(encoding="utf-8") == queue_spec_text
                and {entry.spec_path for entry in stable_registry.stable_specs} >= set(relative_stable_spec_paths)
            ):
                return SpecReviewExecutionResult(
                    record_path=_relative_path(record_path, relative_to=paths.root),
                    questions_path=_relative_path(questions_path, relative_to=paths.root),
                    decision_path=_relative_path(decision_path, relative_to=paths.root),
                    reviewed_path=_relative_path(reviewed_path, relative_to=paths.root),
                    lineage_path=_relative_path(lineage_path, relative_to=paths.root),
                    stable_registry_path=_relative_path(paths.specs_index_file, relative_to=paths.root),
                    family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
                    queue_ownership=ResearchQueueOwnership(
                        family=ResearchQueueFamily.GOALSPEC,
                        queue_path=paths.ideas_specs_reviewed_dir,
                        item_path=reviewed_path,
                        owner_token=run_id,
                        acquired_at=review_timestamp,
                    ),
                )

    questions_path.parent.mkdir(parents=True, exist_ok=True)
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.parent.mkdir(parents=True, exist_ok=True)

    write_text_atomic(
        questions_path,
        render_spec_review_questions(
            reviewed_at=review_timestamp,
            run_id=run_id,
            goal_id=source.idea_id,
            spec_id=spec_id,
            title=source.title,
            queue_spec_path=_relative_path(queue_spec_path, relative_to=paths.root),
            stable_spec_paths=relative_stable_spec_paths,
            findings=findings,
        ),
    )
    write_text_atomic(
        decision_path,
        render_spec_review_decision(
            reviewed_at=review_timestamp,
            run_id=run_id,
            goal_id=source.idea_id,
            spec_id=spec_id,
            title=source.title,
            review_status=review_status,
            reviewed_path=(
                _relative_path(reviewed_path, relative_to=paths.root)
                if review_status != "blocked"
                else _relative_path(queue_spec_path, relative_to=paths.root)
            ),
            stable_registry_path=_relative_path(paths.specs_index_file, relative_to=paths.root),
            lineage_path=_relative_path(lineage_path, relative_to=paths.root),
            findings=findings,
            remediation_bundle_path=(remediation_bundle_relative_path if review_status == "blocked" else ""),
        ),
    )

    if review_status == "blocked":
        _write_review_remediation_bundle(
            paths,
            run_id=run_id,
            emitted_at=review_timestamp,
            spec_id=spec_id,
            goal_id=source.idea_id,
            title=source.title,
            record_path=record_path,
            questions_path=questions_path,
            decision_path=decision_path,
            queue_spec_path=queue_spec_path,
            reviewed_path=reviewed_path,
            lineage_path=lineage_path,
            findings=findings,
        )
        _write_json_model(
            record_path,
            GoalSpecReviewRecord(
                spec_id=spec_id,
                review_status=review_status,
                questions_path=_relative_path(questions_path, relative_to=paths.root),
                decision_path=_relative_path(decision_path, relative_to=paths.root),
                reviewed_path="",
                reviewed_at=review_timestamp,
                findings=findings,
            ),
        )
        raise GoalSpecReviewBlockedError(
            f"Spec Review blocked {spec_id}; resolve the recorded decomposition findings before Taskmaster",
            remediation_bundle_path=remediation_bundle_relative_path,
        )

    lineage_path.parent.mkdir(parents=True, exist_ok=True)
    reviewed_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(reviewed_path, queue_spec_text)
    source_path = _resolve_path_token(source.source_path, relative_to=paths.root)
    if source_path != reviewed_path and source_path.exists():
        source_path.unlink()

    next_family_state, lineage_record = _build_goal_spec_review_state(
        paths=paths,
        spec_id=spec_id,
        goal_id=source.idea_id,
        queue_spec_path=queue_spec_path,
        reviewed_path=reviewed_path,
        questions_path=questions_path,
        decision_path=decision_path,
        stable_spec_paths=stable_spec_paths,
        review_status=review_status,
        emitted_at=review_timestamp,
    )
    write_goal_spec_family_state(
        paths.goal_spec_family_state_file,
        next_family_state,
        updated_at=review_timestamp,
    )
    _write_json_model(lineage_path, lineage_record)
    _write_json_model(
        record_path,
        GoalSpecReviewRecord(
            spec_id=spec_id,
            review_status=review_status,
            questions_path=_relative_path(questions_path, relative_to=paths.root),
            decision_path=_relative_path(decision_path, relative_to=paths.root),
            reviewed_path=_relative_path(reviewed_path, relative_to=paths.root),
            reviewed_at=review_timestamp,
            findings=findings,
        ),
    )
    refresh_stable_spec_registry(
        paths.specs_stable_dir,
        paths.specs_stable_dir / ".frozen",
        paths.specs_index_file,
        relative_to=paths.root,
        updated_at=review_timestamp,
    )

    return SpecReviewExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        questions_path=_relative_path(questions_path, relative_to=paths.root),
        decision_path=_relative_path(decision_path, relative_to=paths.root),
        reviewed_path=_relative_path(reviewed_path, relative_to=paths.root),
        lineage_path=_relative_path(lineage_path, relative_to=paths.root),
        stable_registry_path=_relative_path(paths.specs_index_file, relative_to=paths.root),
        family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
        queue_ownership=ResearchQueueOwnership(
            family=ResearchQueueFamily.GOALSPEC,
            queue_path=paths.ideas_specs_reviewed_dir,
            item_path=reviewed_path,
            owner_token=run_id,
            acquired_at=review_timestamp,
        ),
    )
