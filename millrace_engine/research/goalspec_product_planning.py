"""Deterministic product-surface and phase-planning helpers for GoalSpec."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .goalspec import AcceptanceProfileRecord, CompletionManifestDraftSurface, GoalSource
from .goalspec_helpers import GoalSpecExecutionError, _slugify
from .goalspec_scope_diagnostics import infer_goal_scope_kind

RepoKind = Literal["millrace_python_runtime", "minecraft_fabric_mod", "python_product", "generic_product"]

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
_MINECRAFT_HINTS = (
    "minecraft",
    "fabric",
    "forge",
    "mod",
    "block",
    "item",
    "recipe",
    "gametest",
    "advancement",
    "boss",
    "infuser",
    "conduit",
    "reservoir",
    "aura",
)
_PYTHON_HINTS = (
    "python",
    "cli",
    "command",
    "terminal",
    "service",
    "api",
    "pytest",
    "worker",
    "pipeline",
)
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

    repo_kind: RepoKind
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


def infer_repo_kind(*, source: GoalSource, profile: AcceptanceProfileRecord) -> RepoKind:
    """Infer one bounded repo kind from the product objective."""

    semantic_haystack = "\n".join(
        (
            source.title,
            profile.semantic_profile.objective_summary,
            *profile.semantic_profile.capability_domains,
            *profile.semantic_profile.progression_lines,
        )
    ).lower()
    if any(_contains_hint(semantic_haystack, token) for token in _MINECRAFT_HINTS):
        return "minecraft_fabric_mod"
    if any(_contains_hint(semantic_haystack, token) for token in _FRAMEWORK_HINTS):
        return "millrace_python_runtime"
    if any(_contains_hint(semantic_haystack, token) for token in _PYTHON_HINTS):
        return "python_product"
    return "generic_product"


def derive_goal_product_plan(*, source: GoalSource, profile: AcceptanceProfileRecord) -> GoalProductPlan:
    """Build a deterministic product plan for the current goal."""

    _raise_if_contaminated_planning_inputs(source=source, profile=profile)
    repo_kind = infer_repo_kind(source=source, profile=profile)
    if repo_kind == "millrace_python_runtime":
        return _millrace_python_runtime_plan(source=source, profile=profile)
    if repo_kind == "minecraft_fabric_mod":
        return _minecraft_fabric_mod_plan(source=source, profile=profile)
    if repo_kind == "python_product":
        return _python_product_plan(source=source, profile=profile)
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


def _camel_case(label: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", label)
    if not words:
        return "Feature"
    return "".join(word[:1].upper() + word[1:] for word in words)


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


def _module_slug(source: GoalSource, profile: AcceptanceProfileRecord) -> str:
    first_domain = next(iter(profile.semantic_profile.capability_domains), "")
    if first_domain:
        words = _token_words(first_domain)
        if words:
            return _slugify(words[0])
    title_words = _token_words(source.title)
    if title_words:
        return _slugify(title_words[0])
    return _slugify(source.idea_id)


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


def _millrace_python_runtime_plan(*, source: GoalSource, profile: AcceptanceProfileRecord) -> GoalProductPlan:
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
        repo_kind="millrace_python_runtime",
        implementation_surfaces=implementation_surfaces,
        verification_surfaces=verification_surfaces,
        phase_steps=steps,
        verification_commands=(
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q tests/test_research_dispatcher.py -k goalspec",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q tests/test_goalspec_state.py",
        ),
    )


def _minecraft_fabric_mod_plan(*, source: GoalSource, profile: AcceptanceProfileRecord) -> GoalProductPlan:
    module_slug = _module_slug(source, profile)
    package_path = f"com/example/{module_slug}"
    package_name = package_path.replace("/", ".")
    title_class = _camel_case(source.title)
    component_labels = _component_labels(profile, fallback=source.title)
    component_classes = tuple(_camel_case(label) for label in component_labels)
    registration_path = f"src/main/java/{package_path}/{title_class}Content.java"
    lang_path = f"src/main/resources/assets/{module_slug}/lang/en_us.json"
    recipe_path = f"src/main/resources/data/{module_slug}/recipes/{module_slug}_core.json"
    advancement_path = f"src/main/resources/data/{module_slug}/advancements/{module_slug}_progression.json"
    implementation_surfaces = (
        _surface(
            surface_kind="registration",
            path=registration_path,
            purpose="Register the primary gameplay content for this bounded slice.",
        ),
        *(
            _surface(
                surface_kind="gameplay_logic",
                path=f"src/main/java/{package_path}/{component_class}Block.java",
                purpose=f"Implement {label} behavior for the bounded vertical slice.",
            )
            for label, component_class in zip(component_labels, component_classes)
        ),
        _surface(
            surface_kind="resources",
            path=lang_path,
            purpose="Ship player-facing strings for the first playable slice.",
        ),
        _surface(
            surface_kind="data_pack",
            path=recipe_path,
            purpose="Encode the first bounded recipe or progression data path.",
        ),
        _surface(
            surface_kind="progression",
            path=advancement_path,
            purpose="Carry the first bounded progression milestone into game data.",
        ),
    )
    flow_test_path = f"src/test/java/{package_path}/{title_class}FlowTest.java"
    game_test_path = f"src/gametest/java/{package_path}/{title_class}GameTest.java"
    verification_surfaces = (
        _surface(
            surface_kind="flow_test",
            path=flow_test_path,
            purpose="Exercise the first bounded gameplay flow with deterministic tests.",
        ),
        _surface(
            surface_kind="gametest",
            path=game_test_path,
            purpose="Exercise in-game registration and progression behavior.",
        ),
    )
    first_logic_path = implementation_surfaces[1].path if len(implementation_surfaces) > 1 else registration_path
    second_logic_path = implementation_surfaces[2].path if len(implementation_surfaces) > 2 else first_logic_path
    third_logic_path = implementation_surfaces[3].path if len(implementation_surfaces) > 3 else second_logic_path
    minimum = minimum_phase_step_count(source.decomposition_profile)
    steps = _finalize_phase_steps(
        [
            (
                f"Register the first playable {module_slug} content in `{registration_path}` "
                f"and localize the player-facing names in `{lang_path}`."
            ),
            (
                f"Implement the opening gameplay loop for {component_labels[0]} and {component_labels[min(1, len(component_labels) - 1)]} "
                f"in `{first_logic_path}` and `{second_logic_path}`."
            ),
            (
                f"Implement storage, routing, or payoff behavior for {component_labels[min(2, len(component_labels) - 1)]} "
                f"in `{third_logic_path}` and connect the progression assets in `{recipe_path}`."
            ),
            (
                f"Wire the first validated progression path {_progression_fragment(profile)} in `{advancement_path}` "
                f"and keep the recipe/data path consistent in `{recipe_path}`."
            ),
            (
                f"Add deterministic validation for the bounded vertical slice in `{flow_test_path}` "
                f"and `{game_test_path}`."
            ),
        ],
        supplemental_steps=(
            (
                f"Wire registration flow from `{registration_path}` into `{first_logic_path}` "
                f"and keep player-facing naming aligned in `{lang_path}`."
            ),
            (
                f"Implement bounded interaction rules between `{second_logic_path}` and `{third_logic_path}` "
                f"without widening beyond the first playable slice."
            ),
            (
                f"Persist recipe and progression data alignment in `{recipe_path}` and `{advancement_path}` "
                f"for the first validated gameplay loop."
            ),
            (
                f"Add deterministic happy-path assertions in `{flow_test_path}` for {_progression_fragment(profile)}."
            ),
            (
                f"Add in-game regression coverage in `{game_test_path}` for registration, routing, and progression proof."
            ),
            (
                f"Re-run the focused gameplay validation through `{flow_test_path}` and `{game_test_path}`, "
                f"fixing any path-specific failures in `{registration_path}` and the gameplay classes."
            ),
        ),
        minimum=minimum,
        fallback_paths=(
            registration_path,
            first_logic_path,
            second_logic_path,
            third_logic_path,
            recipe_path,
            advancement_path,
            flow_test_path,
            game_test_path,
        ),
        fallback_focus="the first playable gameplay loop",
    )
    return GoalProductPlan(
        repo_kind="minecraft_fabric_mod",
        implementation_surfaces=implementation_surfaces,
        verification_surfaces=verification_surfaces,
        phase_steps=steps,
        verification_commands=(
            f"./gradlew test --tests {package_name}.{title_class}FlowTest",
            f"./gradlew runGameTest --tests {package_name}.{title_class}GameTest",
        ),
    )


def _python_product_plan(*, source: GoalSource, profile: AcceptanceProfileRecord) -> GoalProductPlan:
    slug = _slugify(source.title)
    summary = profile.semantic_profile.objective_summary.casefold()
    if "cli" in summary or "command" in summary or "terminal" in summary:
        entry_path = f"src/{slug}/cli.py"
        service_path = f"src/{slug}/workflow.py"
        support_path = f"src/{slug}/storage.py"
        verification_paths = (
            f"tests/test_{slug}_cli.py",
            f"tests/test_{slug}_workflow.py",
        )
    else:
        entry_path = f"src/{slug}/api.py"
        service_path = f"src/{slug}/service.py"
        support_path = f"src/{slug}/models.py"
        verification_paths = (
            f"tests/test_{slug}_api.py",
            f"tests/test_{slug}_service.py",
        )
    implementation_surfaces = (
        _surface(
            surface_kind="entrypoint",
            path=entry_path,
            purpose="Expose the bounded product entrypoint for this slice.",
        ),
        _surface(
            surface_kind="core_logic",
            path=service_path,
            purpose="Implement the main bounded workflow for this product slice.",
        ),
        _surface(
            surface_kind="supporting_logic",
            path=support_path,
            purpose="Persist the supporting state or data contract for this slice.",
        ),
    )
    verification_surfaces = tuple(
        _surface(
            surface_kind="pytest",
            path=path,
            purpose="Lock the bounded product behavior with executable regression coverage.",
        )
        for path in verification_paths
    )
    minimum = minimum_phase_step_count(source.decomposition_profile)
    steps = _finalize_phase_steps(
        [
            f"Expose the bounded product entrypoint in `{entry_path}`.",
            f"Implement the core workflow and domain behavior in `{service_path}`.",
            f"Persist the supporting state or contract in `{support_path}`.",
            f"Add regression coverage in `{verification_paths[0]}` and `{verification_paths[1]}`.",
        ],
        supplemental_steps=(
            f"Wire the bounded entry flow from `{entry_path}` into `{service_path}`.",
            f"Handle bounded validation and failure branches in `{service_path}` and `{support_path}`.",
            f"Persist serialization or storage transitions in `{support_path}` for the first shipped slice.",
            f"Add focused entrypoint coverage in `{verification_paths[0]}` for the bounded product path.",
            f"Add workflow edge-case coverage in `{verification_paths[1]}` for the main service contract.",
            (
                f"Re-run the targeted product checks in `{verification_paths[0]}` and `{verification_paths[1]}`, "
                f"fixing any path-specific regressions in `{entry_path}`, `{service_path}`, and `{support_path}`."
            ),
        ),
        minimum=minimum,
        fallback_paths=(
            entry_path,
            service_path,
            support_path,
            verification_paths[0],
            verification_paths[1],
        ),
        fallback_focus="the bounded product workflow",
    )
    return GoalProductPlan(
        repo_kind="python_product",
        implementation_surfaces=implementation_surfaces,
        verification_surfaces=verification_surfaces,
        phase_steps=steps,
        verification_commands=tuple(
            f"PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q {path}" for path in verification_paths
        ),
    )


def _generic_product_plan(*, source: GoalSource, profile: AcceptanceProfileRecord) -> GoalProductPlan:
    slug = _slugify(source.title)
    implementation_surfaces = (
        _surface(
            surface_kind="core_logic",
            path=f"src/{slug}/feature.py",
            purpose="Implement the bounded feature logic for this product slice.",
        ),
        _surface(
            surface_kind="workflow",
            path=f"src/{slug}/workflow.py",
            purpose="Wire the bounded product workflow for this slice.",
        ),
    )
    verification_surfaces = (
        _surface(
            surface_kind="integration_test",
            path=f"tests/test_{slug}_flow.py",
            purpose="Lock the bounded end-to-end flow with regression coverage.",
        ),
    )
    minimum = minimum_phase_step_count(source.decomposition_profile)
    steps = _finalize_phase_steps(
        [
            f"Implement the core feature path in `{implementation_surfaces[0].path}`.",
            f"Wire the bounded workflow in `{implementation_surfaces[1].path}`.",
            f"Add regression coverage for {_progression_fragment(profile)} in `{verification_surfaces[0].path}`.",
        ],
        supplemental_steps=(
            (
                f"Connect `{implementation_surfaces[0].path}` into `{implementation_surfaces[1].path}` "
                f"for the first bounded product flow."
            ),
            (
                f"Handle bounded validation and state transitions in `{implementation_surfaces[0].path}` "
                f"and `{implementation_surfaces[1].path}`."
            ),
            f"Add focused happy-path assertions in `{verification_surfaces[0].path}` for {_progression_fragment(profile)}.",
            (
                f"Add bounded edge-case assertions in `{verification_surfaces[0].path}` "
                f"for the workflow implemented in `{implementation_surfaces[1].path}`."
            ),
            (
                f"Re-run the product flow regression in `{verification_surfaces[0].path}`, "
                f"fixing any path-specific failures in `{implementation_surfaces[0].path}` and `{implementation_surfaces[1].path}`."
            ),
        ),
        minimum=minimum,
        fallback_paths=(
            implementation_surfaces[0].path,
            implementation_surfaces[1].path,
            verification_surfaces[0].path,
        ),
        fallback_focus="the bounded product flow",
    )
    return GoalProductPlan(
        repo_kind="generic_product",
        implementation_surfaces=implementation_surfaces,
        verification_surfaces=verification_surfaces,
        phase_steps=steps,
        verification_commands=(
            f"PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q {verification_surfaces[0].path}",
        ),
    )


__all__ = [
    "GoalProductPlan",
    "RepoKind",
    "derive_goal_product_plan",
    "find_abstract_phase_steps",
    "infer_repo_kind",
    "is_product_surface_path",
    "minimum_phase_package_count",
    "minimum_phase_step_count",
    "surface_paths",
]
