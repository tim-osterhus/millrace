"""Deterministic product-surface and phase-planning helpers for GoalSpec."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .goalspec import AcceptanceProfileRecord, CompletionManifestDraftSurface, GoalSource
from .goalspec_helpers import GoalSpecExecutionError, _slugify
from .goalspec_scope_diagnostics import infer_goal_scope_kind

PlanningProfile = Literal["framework_runtime", "generic_product"]

_FRAMEWORK_HINTS = (
    "goalspec",
    "goal intake",
    "objective profile",
    "objective sync",
    "completion manifest",
    "taskmaster",
    "taskaudit",
    "research runtime",
    "research plane",
    "dispatcher",
    "queue governor",
    "millrace",
)
_PLANNING_EXACT_ADMIN_LABELS = frozenset(
    {
        "agents",
        "archive",
        "audit",
        "goal intake",
        "goal_intake",
        "ideas",
        "objective",
        "objective profile sync",
        "objective_profile_sync",
        "raw",
        "reports",
        "spec review",
        "spec synthesis",
        "spec_review",
        "spec_synthesis",
        "specs",
        "specs reviewed",
        "specs_reviewed",
        "staging",
        "taskaudit",
        "taskmaster",
        "tasksbacklog",
        "taskspending",
    }
)
_PLANNING_ADMIN_LANGUAGE_TOKENS = (
    "canonical source",
    "checkpoint",
    "completion manifest",
    "current artifact",
    "dispatch",
    "frontmatter",
    "goal intake",
    "golden spec",
    "objective profile",
    "phase spec",
    "queue family",
    "queue root",
    "queue spec",
    "research plane",
    "route decision",
    "semantic seed",
    "source artifact",
    "spec review",
    "spec synthesis",
    "stage contract",
    "task generation",
    "taskaudit",
    "taskmaster",
    "trace metadata",
    "traceability",
)
_CONTROL_DOC_FILENAME_RE = re.compile(r"\b[a-z0-9._-]+\.(?:md|json|ya?ml|toml)\b", re.IGNORECASE)
_PROFILE_MIN_STEP_COUNT = {
    "trivial": 1,
    "simple": 3,
    "moderate": 6,
    "involved": 10,
    "complex": 14,
    "massive": 20,
    "": 3,
}
_PROFILE_MIN_PHASE_PACKAGE_COUNT = {
    "trivial": 1,
    "simple": 1,
    "moderate": 1,
    "involved": 2,
    "complex": 2,
    "massive": 3,
    "": 1,
}
_ABSTRACT_STEP_HINTS = (
    "implement the bounded slice",
    "implement the first bounded capability slice",
    "add or update proof",
    "close this phase with bounded handoff evidence",
    "close handoff evidence",
    "preserve traceability",
    "handoff evidence",
    "reviewable runtime implementation slice",
    "run all gates and fix failures",
    "fix until green",
    "iterate until pass",
)


@dataclass(frozen=True)
class GoalProductPlan:
    """Derived product-facing plan used across completion, synthesis, review, and Taskmaster."""

    planning_profile: PlanningProfile
    implementation_surfaces: tuple[CompletionManifestDraftSurface, ...]
    verification_surfaces: tuple[CompletionManifestDraftSurface, ...]
    phase_steps: tuple[str, ...]
    verification_commands: tuple[str, ...]


@dataclass(frozen=True)
class PlanningContaminationFinding:
    """One semantic label the planner refuses to trust for product-scoped goals."""

    source: Literal["objective_summary", "capability_domain", "progression_line"]
    label: str
    reason: Literal["administrative_language", "path_shaped"]


def infer_planning_profile(*, source: GoalSource, profile: AcceptanceProfileRecord) -> PlanningProfile:
    """Infer the bounded planning profile for the current objective."""

    semantic_haystack = "\n".join(
        (
            source.title,
            profile.semantic_profile.objective_summary,
            *profile.semantic_profile.capability_domains,
            *profile.semantic_profile.progression_lines,
        )
    ).lower()
    if any(_contains_hint(semantic_haystack, token) for token in _FRAMEWORK_HINTS):
        return "framework_runtime"
    return "generic_product"


def derive_goal_product_plan(*, source: GoalSource, profile: AcceptanceProfileRecord) -> GoalProductPlan:
    """Build a deterministic product plan for the current goal."""

    _raise_if_contaminated_planning_inputs(source=source, profile=profile)
    planning_profile = infer_planning_profile(source=source, profile=profile)
    if planning_profile == "framework_runtime":
        return _framework_runtime_plan(source=source, profile=profile)
    return _generic_product_plan(source=source, profile=profile)


def minimum_phase_step_count(decomposition_profile: str) -> int:
    """Return the active review/task density floor for one decomposition profile."""

    normalized = decomposition_profile.strip().lower()
    return _PROFILE_MIN_STEP_COUNT.get(normalized, _PROFILE_MIN_STEP_COUNT[""])


def minimum_phase_package_count(decomposition_profile: str) -> int:
    """Return the active minimum number of phase packages for one decomposition profile."""

    normalized = decomposition_profile.strip().lower()
    return _PROFILE_MIN_PHASE_PACKAGE_COUNT.get(normalized, _PROFILE_MIN_PHASE_PACKAGE_COUNT[""])


def find_abstract_phase_steps(steps: tuple[str, ...]) -> tuple[str, ...]:
    """Return work-plan steps that are too abstract to decompose safely."""

    findings: list[str] = []
    for step in steps:
        lowered = step.casefold()
        if any(token in lowered for token in _ABSTRACT_STEP_HINTS):
            findings.append(step)
            continue
        if "`" not in step and "agents/" not in lowered and "tests/" not in lowered and "src/" not in lowered:
            if "implement" in lowered and "verify" not in lowered:
                findings.append(step)
    return tuple(findings)


def is_product_surface_path(path: str) -> bool:
    normalized = path.strip()
    return bool(normalized) and not normalized.startswith("agents/")


def surface_paths(surfaces: tuple[CompletionManifestDraftSurface, ...]) -> tuple[str, ...]:
    deduped: list[str] = []
    seen: set[str] = set()
    for surface in surfaces:
        path = surface.path.strip()
        if not path or path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return tuple(deduped)


def _surface(*, surface_kind: str, path: str, purpose: str) -> CompletionManifestDraftSurface:
    return CompletionManifestDraftSurface(surface_kind=surface_kind, path=path, purpose=purpose)


def _token_words(text: str) -> tuple[str, ...]:
    return tuple(token for token in re.findall(r"[A-Za-z0-9]+", text.lower()) if token)


def _contains_hint(text: str, hint: str) -> bool:
    pattern = r"\b" + re.escape(hint.casefold()) + r"\b"
    return re.search(pattern, text.casefold()) is not None


def _normalize_admin_probe(text: str) -> str:
    return re.sub(r"[\s_-]+", " ", text.casefold()).strip()


def _planning_label_rejection_reason(text: str) -> Literal["administrative_language", "path_shaped"] | None:
    if not text:
        return None
    if "`" in text or "/" in text or "\\" in text:
        return "path_shaped"
    probe = _normalize_admin_probe(text)
    if probe in _PLANNING_EXACT_ADMIN_LABELS:
        return "administrative_language"
    if any(_normalize_admin_probe(token) in probe for token in _PLANNING_ADMIN_LANGUAGE_TOKENS):
        return "administrative_language"
    if _CONTROL_DOC_FILENAME_RE.search(text):
        return "administrative_language"
    return None


def _collect_planning_contamination_findings(
    *,
    profile: AcceptanceProfileRecord,
) -> tuple[PlanningContaminationFinding, ...]:
    findings: list[PlanningContaminationFinding] = []
    for source_kind, values in (
        ("objective_summary", (profile.semantic_profile.objective_summary,)),
        ("capability_domain", tuple(profile.semantic_profile.capability_domains)),
        ("progression_line", tuple(profile.semantic_profile.progression_lines)),
    ):
        for value in values:
            normalized = value.strip()
            if not normalized:
                continue
            rejection_reason = _planning_label_rejection_reason(normalized)
            if rejection_reason is None:
                continue
            findings.append(
                PlanningContaminationFinding(
                    source=source_kind,
                    label=normalized,
                    reason=rejection_reason,
                )
            )
    return tuple(findings)


def _raise_if_contaminated_planning_inputs(*, source: GoalSource, profile: AcceptanceProfileRecord) -> None:
    expected_scope = infer_goal_scope_kind(
        title=source.title,
        source_body=source.canonical_body,
        semantic_summary="",
        capability_domains=(),
    )
    if expected_scope != "product":
        return
    findings = _collect_planning_contamination_findings(profile=profile)
    if not findings:
        return
    summary = "; ".join(
        f"{finding.source}=`{finding.label}` ({finding.reason.replace('_', ' ')})"
        for finding in findings
    )
    raise GoalSpecExecutionError(
        "Planner refused contaminated semantic labels for a product-scoped goal: "
        f"{summary}"
    )


def _dedupe_text(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = value.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return tuple(deduped)


def _component_labels(profile: AcceptanceProfileRecord, *, fallback: str) -> tuple[str, ...]:
    labels = list(profile.semantic_profile.capability_domains[:4])
    if not labels:
        labels.append(fallback)
    return _dedupe_text(labels)


def _progression_fragment(profile: AcceptanceProfileRecord) -> str:
    line = next(iter(profile.semantic_profile.progression_lines), "").strip()
    if not line:
        return "the first validated product flow"
    lowered = line[:1].lower() + line[1:]
    return lowered.rstrip(".")


def _component_surface_pairs(
    *,
    source: GoalSource,
    profile: AcceptanceProfileRecord,
) -> tuple[tuple[str, str], ...]:
    slug = _slugify(source.title)
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, label in enumerate(_component_labels(profile, fallback=source.title), start=1):
        component_slug = _slugify(label) or f"capability-{index:02d}"
        path = f"src/{slug}/{component_slug}"
        if path in seen:
            continue
        seen.add(path)
        pairs.append((label, path))
    return tuple(pairs)


def _finalize_phase_steps(
    base_steps: list[str],
    *,
    supplemental_steps: tuple[str, ...],
    minimum: int,
    fallback_paths: tuple[str, ...],
    fallback_focus: str,
) -> tuple[str, ...]:
    deduped = list(_dedupe_text(base_steps))
    if not deduped:
        return ()
    for step in supplemental_steps:
        if len(deduped) >= minimum:
            break
        if step not in deduped:
            deduped.append(step)
    if len(deduped) >= minimum:
        return tuple(deduped)

    usable_paths = tuple(path for path in _dedupe_text(fallback_paths) if path.strip()) or ("src/product/feature.py",)
    follow_up_index = 1
    while len(deduped) < minimum:
        primary = usable_paths[(follow_up_index - 1) % len(usable_paths)]
        secondary = usable_paths[follow_up_index % len(usable_paths)]
        deduped.append(
            (
                f"Tighten bounded follow-up slice {follow_up_index:02d} in `{primary}` "
                f"and `{secondary}` while preserving {fallback_focus}."
            )
        )
        follow_up_index += 1
    return tuple(deduped)


def _framework_runtime_plan(*, source: GoalSource, profile: AcceptanceProfileRecord) -> GoalProductPlan:
    implementation_surfaces = (
        _surface(
            surface_kind="runtime_stage",
            path="millrace_engine/research/goalspec_goal_intake.py",
            purpose="Own the staged goal intake path for the bounded runtime slice.",
        ),
        _surface(
            surface_kind="runtime_stage",
            path="millrace_engine/research/goalspec_objective_profile_sync.py",
            purpose="Persist the synced objective profile for the bounded runtime slice.",
        ),
        _surface(
            surface_kind="runtime_facade",
            path="millrace_engine/research/goalspec_stage_support.py",
            purpose="Preserve the GoalSpec stage facade and handoff wiring.",
        ),
    )
    verification_surfaces = (
        _surface(
            surface_kind="integration_test",
            path="tests/test_research_dispatcher.py",
            purpose="Lock the end-to-end GoalSpec runtime path.",
        ),
        _surface(
            surface_kind="contract_test",
            path="tests/test_goalspec_state.py",
            purpose="Lock the GoalSpec state and persistence contract.",
        ),
    )
    minimum = minimum_phase_step_count(source.decomposition_profile)
    steps = _finalize_phase_steps(
        [
            (
                "Implement the staged intake edge in `millrace_engine/research/goalspec_goal_intake.py` "
                "so the queue artifact is archived, restaged, and normalized without losing product scope."
            ),
            (
                "Persist the synced profile state in `millrace_engine/research/goalspec_objective_profile_sync.py` "
                "and keep the GoalSpec facade coherent in `millrace_engine/research/goalspec_stage_support.py`."
            ),
            (
                "Extend regression coverage in `tests/test_research_dispatcher.py` and `tests/test_goalspec_state.py` "
                "for the concrete runtime path from staged goal to synced objective profile."
            ),
            (
                "Verify the bounded runtime slice stays restart-safe through `tests/test_research_dispatcher.py` "
                "and keep the state contract explicit in `tests/test_goalspec_state.py`."
            ),
        ],
        supplemental_steps=(
            (
                "Normalize staged goal metadata and archive/restage transitions in "
                "`millrace_engine/research/goalspec_goal_intake.py` and "
                "`millrace_engine/research/goalspec_stage_support.py`."
            ),
            (
                "Keep objective-profile product scope aligned with staged inputs in "
                "`millrace_engine/research/goalspec_objective_profile_sync.py` and "
                "`millrace_engine/research/goalspec_goal_intake.py`."
            ),
            (
                "Wire checkpoint-facing GoalSpec handoff metadata in "
                "`millrace_engine/research/goalspec_stage_support.py` and "
                "`millrace_engine/research/goalspec_objective_profile_sync.py`."
            ),
            (
                "Add regression coverage for staged archive/restage behavior in "
                "`tests/test_research_dispatcher.py` and `tests/test_goalspec_state.py`."
            ),
            (
                "Add regression coverage for checkpoint-safe resume of the bounded runtime slice in "
                "`tests/test_research_dispatcher.py` and `tests/test_goalspec_state.py`."
            ),
            (
                "Re-run the GoalSpec runtime regressions in `tests/test_research_dispatcher.py` "
                "and `tests/test_goalspec_state.py`, fixing any path-specific failures in the research modules."
            ),
        ),
        minimum=minimum,
        fallback_paths=(
            "millrace_engine/research/goalspec_goal_intake.py",
            "millrace_engine/research/goalspec_objective_profile_sync.py",
            "millrace_engine/research/goalspec_stage_support.py",
            "tests/test_research_dispatcher.py",
            "tests/test_goalspec_state.py",
        ),
        fallback_focus="the staged GoalSpec runtime path",
    )
    return GoalProductPlan(
        planning_profile="framework_runtime",
        implementation_surfaces=implementation_surfaces,
        verification_surfaces=verification_surfaces,
        phase_steps=steps,
        verification_commands=(
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q tests/test_research_dispatcher.py -k goalspec",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q tests/test_goalspec_state.py",
        ),
    )


def _generic_product_plan(*, source: GoalSource, profile: AcceptanceProfileRecord) -> GoalProductPlan:
    slug = _slugify(source.title)
    component_pairs = _component_surface_pairs(source=source, profile=profile)
    entry_path = f"src/{slug}/entrypoint"
    workflow_path = f"src/{slug}/workflow"
    flow_path = f"tests/{slug}/flow"
    regression_path = f"tests/{slug}/regression"
    implementation_surfaces = (
        _surface(
            surface_kind="entrypoint",
            path=entry_path,
            purpose="Expose the bounded product entry surface for the synthesized slice.",
        ),
        *(
            _surface(
                surface_kind="capability_surface",
                path=path,
                purpose=f"Implement {label} behavior for the bounded product slice.",
            )
            for label, path in component_pairs[:3]
        ),
        _surface(
            surface_kind="workflow",
            path=workflow_path,
            purpose="Wire the bounded product workflow for the synthesized slice.",
        ),
    )
    verification_surfaces = (
        _surface(
            surface_kind="flow_verification",
            path=flow_path,
            purpose="Lock the primary bounded product flow with explicit verification coverage.",
        ),
        _surface(
            surface_kind="regression_verification",
            path=regression_path,
            purpose="Lock bounded edge cases and regression expectations for the synthesized slice.",
        ),
    )
    primary_capability = component_pairs[0] if component_pairs else (source.title, entry_path)
    secondary_capability = component_pairs[1] if len(component_pairs) > 1 else primary_capability
    tertiary_capability = component_pairs[2] if len(component_pairs) > 2 else secondary_capability
    minimum = minimum_phase_step_count(source.decomposition_profile)
    steps = _finalize_phase_steps(
        [
            (
                f"Expose the bounded entry surface in `{entry_path}` and land the first product capability for "
                f"{primary_capability[0]} in `{primary_capability[1]}`."
            ),
            (
                f"Implement the next bounded capability surfaces for {secondary_capability[0]} and {tertiary_capability[0]} "
                f"in `{secondary_capability[1]}` and `{tertiary_capability[1]}`."
            ),
            (
                f"Wire the bounded workflow for {_progression_fragment(profile)} in `{workflow_path}` "
                f"without widening beyond the synthesized slice."
            ),
            f"Add focused flow coverage in `{flow_path}` for {_progression_fragment(profile)}.",
            f"Add bounded regression coverage in `{regression_path}` for capability handoff and failure handling.",
        ],
        supplemental_steps=(
            (
                f"Connect `{entry_path}` into `{primary_capability[1]}` and `{workflow_path}` "
                f"for the first bounded product flow."
            ),
            (
                f"Handle bounded validation and state transitions in `{secondary_capability[1]}` "
                f"and `{workflow_path}`."
            ),
            f"Add focused happy-path assertions in `{flow_path}` for {_progression_fragment(profile)}.",
            (
                f"Add bounded edge-case assertions in `{regression_path}` "
                f"for the workflow implemented in `{workflow_path}`."
            ),
            (
                f"Re-run the bounded product verification anchored to `{flow_path}` and `{regression_path}`, "
                f"fixing any path-specific failures in `{entry_path}`, `{primary_capability[1]}`, and `{workflow_path}`."
            ),
        ),
        minimum=minimum,
        fallback_paths=(
            entry_path,
            *(path for _, path in component_pairs[:3]),
            workflow_path,
            flow_path,
            regression_path,
        ),
        fallback_focus="the bounded product flow",
    )
    return GoalProductPlan(
        planning_profile="generic_product",
        implementation_surfaces=implementation_surfaces,
        verification_surfaces=verification_surfaces,
        phase_steps=steps,
        verification_commands=(
            f"confirm repo-native flow verification covering {flow_path}",
            f"confirm repo-native regression verification covering {regression_path}",
        ),
    )


__all__ = [
    "GoalProductPlan",
    "PlanningProfile",
    "derive_goal_product_plan",
    "find_abstract_phase_steps",
    "infer_planning_profile",
    "is_product_surface_path",
    "minimum_phase_package_count",
    "minimum_phase_step_count",
    "surface_paths",
]
