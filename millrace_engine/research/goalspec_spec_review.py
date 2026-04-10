"""GoalSpec spec-review stage executor."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from ..markdown import write_text_atomic
from ..paths import RuntimePaths
from .goalspec import SpecReviewExecutionResult
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
    surface_paths,
)
from .goalspec_scope_diagnostics import infer_goal_scope_kind
from .goalspec_stage_rendering import (
    render_spec_review_decision,
    render_spec_review_questions,
)
from .specs import (
    GoalSpecLineageRecord,
    GoalSpecReviewFinding,
    GoalSpecReviewRecord,
    load_goal_spec_family_state,
    load_stable_spec_registry,
    refresh_stable_spec_registry,
    write_goal_spec_family_state,
)
from .state import ResearchCheckpoint, ResearchQueueFamily, ResearchQueueOwnership

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
_SPECIALIZATION_TOKEN_RE = re.compile(r"\b([a-z_]+=[a-z0-9._-]+)\b", re.IGNORECASE)
_SAFE_UNRESOLVED_LINE_HINTS = ("unresolved", "unsupported", "abstention", "fallback", "do not invent")
_MINECRAFT_LOADER_HINTS = {
    "fabric": ("fabric", "fabric.mod.json"),
    "forge": ("forge", "mods.toml"),
    "neoforge": ("neoforge", "neoforge.mods.toml"),
    "quilt": ("quilt", "quilt.mod.json"),
}


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


def _line_mentions_specialization(line: str, *, token: str, hints: tuple[str, ...]) -> bool:
    lowered = line.casefold()
    token_value = token.partition("=")[2].casefold()
    if token.casefold() in lowered:
        return True
    return any(hint.casefold() in lowered for hint in hints if hint)


def _line_is_safe_unresolved_reference(line: str, *, token: str) -> bool:
    lowered = line.casefold()
    return token.casefold() in lowered and any(hint in lowered for hint in _SAFE_UNRESOLVED_LINE_HINTS)


def _contractor_specificity_findings(
    *,
    contractor_profile: object | None,
    artifact_lines: tuple[str, ...],
    queue_spec_path: Path,
    paths: RuntimePaths,
) -> tuple[GoalSpecReviewFinding, ...]:
    from .goalspec import ContractorProfileArtifact

    if not isinstance(contractor_profile, ContractorProfileArtifact):
        return ()

    findings: list[GoalSpecReviewFinding] = []
    unresolved_specializations = set(contractor_profile.unresolved_specializations)
    specialization_keys = {
        specialization.partition("=")[0].strip().casefold()
        for specialization in contractor_profile.unresolved_specializations
        if "=" in specialization
    }
    explicit_specializations = {
        match.group(1).strip()
        for line in artifact_lines
        for match in _SPECIALIZATION_TOKEN_RE.finditer(line)
        if match.group(1).partition("=")[0].strip().casefold() in specialization_keys
    }
    for specialization in sorted(explicit_specializations):
        if specialization in unresolved_specializations:
            continue
        findings.append(
            GoalSpecReviewFinding(
                finding_id="REV-CONTRACTOR-INVENTED-SPECIALIZATION",
                severity="blocker",
                summary=(
                    "Stable planning text invents unsupported specialization "
                    f"`{specialization}` beyond the contractor profile."
                ),
                artifact_path=_relative_path(queue_spec_path, relative_to=paths.root),
            )
        )

    if (
        contractor_profile.shape_class == "platform_extension"
        and contractor_profile.classification.host_platform == "minecraft"
    ):
        unresolved_loaders = {
            specialization.partition("=")[2].casefold()
            for specialization in contractor_profile.unresolved_specializations
            if specialization.startswith("loader=")
        }
        for loader, hints in _MINECRAFT_LOADER_HINTS.items():
            for line in artifact_lines:
                if not _line_mentions_specialization(line, token=f"loader={loader}", hints=hints):
                    continue
                if loader in unresolved_loaders and _line_is_safe_unresolved_reference(line, token=f"loader={loader}"):
                    continue
                findings.append(
                    GoalSpecReviewFinding(
                        finding_id="REV-CONTRACTOR-UNSUPPORTED-SPECIALIZATION",
                        severity="blocker",
                        summary=(
                            "Stable planning text resolves loader-specific specialization "
                            f"`loader={loader}` beyond contractor grounding: {line}"
                        ),
                        artifact_path=_relative_path(queue_spec_path, relative_to=paths.root),
                    )
                )
                break

    return tuple(findings)


def _review_findings(
    *,
    paths: RuntimePaths,
    source_body: str,
    source_title: str,
    decomposition_profile: str,
    stable_spec_paths: tuple[Path, ...],
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
    contractor_profile = load_objective_state_contractor_profile(paths, objective_state)
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

    artifact_lines = _review_text_lines(
        queue_spec_text,
        *(path.read_text(encoding="utf-8") for path in stable_spec_paths),
    )
    findings.extend(
        _contractor_specificity_findings(
            contractor_profile=contractor_profile,
            artifact_lines=artifact_lines,
            queue_spec_path=queue_spec_path,
            paths=paths,
        )
    )

    return tuple(findings)


def execute_spec_review(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    emitted_at: datetime | None = None,
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
    stable_spec_paths = _stable_spec_paths_for_review(paths, spec_id=spec_id)
    relative_stable_spec_paths = tuple(_relative_path(path, relative_to=paths.root) for path in stable_spec_paths)
    review_timestamp = emitted_at
    queue_spec_text = _resolve_path_token(source.source_path, relative_to=paths.root).read_text(
        encoding="utf-8",
        errors="replace",
    )
    findings = _review_findings(
        paths=paths,
        source_body=source.body,
        source_title=source.title,
        decomposition_profile=source.decomposition_profile,
        stable_spec_paths=stable_spec_paths,
        queue_spec_path=queue_spec_path,
        queue_spec_text=queue_spec_text,
    )
    review_status = "blocked" if findings else "no_material_delta"
    finding_summaries = tuple(finding.summary for finding in findings)

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
            findings=finding_summaries,
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
            findings=finding_summaries,
        )
        if findings:
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
                raise GoalSpecExecutionError(
                    f"Spec Review blocked {spec_id}; resolve the recorded decomposition findings before Taskmaster"
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
                    findings=(),
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
            findings=finding_summaries,
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
                if not findings
                else _relative_path(queue_spec_path, relative_to=paths.root)
            ),
            stable_registry_path=_relative_path(paths.specs_index_file, relative_to=paths.root),
            lineage_path=_relative_path(lineage_path, relative_to=paths.root),
            findings=finding_summaries,
        ),
    )

    if findings:
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
        raise GoalSpecExecutionError(
            f"Spec Review blocked {spec_id}; resolve the recorded decomposition findings before Taskmaster"
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
            findings=(),
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
