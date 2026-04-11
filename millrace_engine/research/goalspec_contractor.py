"""Inline Contractor execution helpers for GoalSpec."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import re

from ..markdown import write_text_atomic
from ..paths import RuntimePaths
from .goalspec import (
    ContractorClassificationCandidate,
    ContractorClassificationPayload,
    ContractorExecutionRecord,
    ContractorExecutionResult,
    ContractorFallbackMode,
    ContractorProfileArtifact,
    ContractorShapeClass,
    ContractorSpecificityLevel,
    GoalSource,
    GoalSpecSpecializationRecord,
)
from .goalspec_helpers import (
    GoalSpecExecutionError,
    _isoformat_z,
    _relative_path,
    _utcnow,
    _write_json_model,
    resolve_goal_source,
)
from .goalspec_persistence import contractor_record_path, load_contractor_execution_record, load_contractor_profile
from .state import ResearchCheckpoint

_SHAPES_FILE = "EXAMPLES_SHAPES.md"
_PLATFORM_FILE = "EXAMPLES_PLATFORM_EXTENSIONS.md"
_WEB_FILE = "EXAMPLES_WEB_AND_NETWORK.md"
_TOOLS_FILE = "EXAMPLES_TOOLS_AND_LIBRARIES.md"
_AMBIGUOUS_FILE = "EXAMPLES_AMBIGUOUS_AND_EDGE_CASES.md"

_STACK_ENVIRONMENT_HINTS: dict[str, tuple[str, ...]] = {
    "jvm": ("java",),
    "gradle": ("gradle",),
    "python_package": ("python",),
    "react_frontend": ("node",),
    "node_service": ("node",),
    "postgres_backed": ("postgres",),
}

_STACK_PROFILE_IDS: dict[str, str] = {
    "python_package": "stack.python_package@1",
    "react_frontend": "stack.react_frontend@1",
    "node_service": "stack.node_service@1",
    "postgres_backed": "stack.postgres_backed@1",
}
_HOST_PLATFORM_PATTERNS = (
    re.compile(
        r"\b(?:build|create|ship|make)\s+(?:a|an|the)\s+([a-z0-9][a-z0-9+._-]*(?: [a-z0-9][a-z0-9+._-]*){0,2})\s+mod\b"
    ),
    re.compile(
        r"\b(?:build|create|ship|make)\s+(?:a|an|the)\s+([a-z0-9][a-z0-9+._-]*(?: [a-z0-9][a-z0-9+._-]*){0,2})\s+"
        r"(?:plugin|extension|integration|bot|app)\b"
    ),
    re.compile(r"\b(?:plugin|extension|integration|bot|app)\s+for\s+(?:the\s+)?([a-z0-9][a-z0-9+._-]*(?: [a-z0-9][a-z0-9+._-]*){0,2})\b"),
    re.compile(r"\b([a-z0-9][a-z0-9+._-]*(?: [a-z0-9][a-z0-9+._-]*){0,2})\s+(?:plugin|extension|integration|bot|app)\b"),
    re.compile(r"\b([a-z0-9][a-z0-9+._-]*(?: [a-z0-9][a-z0-9+._-]*){0,2})\s+mod\b"),
)
_HOST_PLATFORM_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "app",
        "bot",
        "build",
        "extension",
        "for",
        "host",
        "integration",
        "mod",
        "new",
        "our",
        "platform",
        "plugin",
        "the",
        "usable",
    }
)


@dataclass(frozen=True)
class _ContractorDecision:
    shape_class: ContractorShapeClass
    archetype: str
    host_platform: str
    stack_hints: tuple[str, ...]
    specializations: dict[str, str]
    specificity_level: ContractorSpecificityLevel
    confidence: float
    fallback_mode: ContractorFallbackMode
    resolved_profile_ids: tuple[str, ...]
    unresolved_specializations: tuple[str, ...]
    specialization_provenance: tuple[GoalSpecSpecializationRecord, ...]
    capability_hints: tuple[str, ...]
    environment_hints: tuple[str, ...]
    evidence: tuple[str, ...]
    abstentions: tuple[str, ...]
    contradictions: tuple[str, ...]
    notes: str
    candidate_classifications: tuple[ContractorClassificationCandidate, ...]
    example_shards: tuple[str, ...]


def execute_contractor(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    emitted_at: datetime | None = None,
) -> ContractorExecutionResult:
    """Classify the current GoalSpec source into a validated Contractor profile."""

    emitted_at = emitted_at or _utcnow()
    source = resolve_goal_source(paths, checkpoint)
    record_path = contractor_record_path(paths, run_id=run_id)
    canonical_source_checksum_sha256 = sha256(source.canonical_body.encode("utf-8")).hexdigest()

    reused = _maybe_reuse_existing_execution(
        paths=paths,
        source=source,
        run_id=run_id,
        record_path=record_path,
        canonical_source_checksum_sha256=canonical_source_checksum_sha256,
    )
    if reused is not None:
        return reused

    decision = _build_contractor_decision(paths=paths, source=source)
    profile_path = paths.contractor_profile_file
    report_path = paths.contractor_profile_report_file
    schema_path = paths.packaged_contractor_profile_schema_file

    profile_payload = _build_profile_payload(
        source=source,
        run_id=run_id,
        emitted_at=emitted_at,
        report_path=_relative_path(report_path, relative_to=paths.root),
        decision=decision,
    )
    profile = ContractorProfileArtifact.model_validate(profile_payload)

    profile_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_model(profile_path, profile)
    write_text_atomic(
        report_path,
        _render_contractor_report(
            source=source,
            run_id=run_id,
            emitted_at=emitted_at,
            decision=decision,
            profile=profile,
            schema_path=_relative_path(schema_path, relative_to=paths.root),
        ),
    )

    record = ContractorExecutionRecord(
        run_id=run_id,
        emitted_at=emitted_at,
        goal_id=source.idea_id,
        title=source.title,
        canonical_source_path=source.canonical_relative_source_path,
        current_artifact_path=source.current_artifact_relative_path,
        source_path=source.canonical_relative_source_path,
        source_checksum_sha256=source.checksum_sha256,
        canonical_source_checksum_sha256=canonical_source_checksum_sha256,
        research_brief_path=source.current_artifact_relative_path,
        profile_path=_relative_path(profile_path, relative_to=paths.root),
        report_path=_relative_path(report_path, relative_to=paths.root),
        schema_path=_relative_path(schema_path, relative_to=paths.root),
        record_path=_relative_path(record_path, relative_to=paths.root),
        profile_specificity_level=profile.specificity_level,
        shape_class=profile.shape_class,
        fallback_mode=profile.fallback_mode,
        specialization_provenance=profile.specialization_provenance,
        browse_used=profile.browse_used,
    )
    _write_json_model(record_path, record)

    return ContractorExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        profile_path=_relative_path(profile_path, relative_to=paths.root),
        report_path=_relative_path(report_path, relative_to=paths.root),
        schema_path=_relative_path(schema_path, relative_to=paths.root),
        profile=profile,
    )


def _maybe_reuse_existing_execution(
    *,
    paths: RuntimePaths,
    source: GoalSource,
    run_id: str,
    record_path,
    canonical_source_checksum_sha256: str,
) -> ContractorExecutionResult | None:
    if not record_path.exists():
        return None
    try:
        existing_record = load_contractor_execution_record(paths, run_id=run_id)
        existing_profile = load_contractor_profile(paths)
    except GoalSpecExecutionError:
        return None

    report_path = paths.root / existing_record.report_path
    if not report_path.exists():
        return None
    if existing_record.source_checksum_sha256 and existing_record.source_checksum_sha256 != source.checksum_sha256:
        return None
    if existing_record.canonical_source_checksum_sha256 != canonical_source_checksum_sha256:
        return None
    if existing_record.goal_id != source.idea_id or existing_record.title != source.title:
        return None
    if existing_record.canonical_source_path != source.canonical_relative_source_path:
        return None
    if existing_record.current_artifact_path != source.current_artifact_relative_path:
        return None
    if existing_profile.goal_id != source.idea_id or existing_profile.run_id != run_id:
        return None

    return ContractorExecutionResult(
        record_path=existing_record.record_path,
        profile_path=existing_record.profile_path,
        report_path=existing_record.report_path,
        schema_path=existing_record.schema_path,
        profile=existing_profile,
    )


def _build_profile_payload(
    *,
    source: GoalSource,
    run_id: str,
    emitted_at: datetime,
    report_path: str,
    decision: _ContractorDecision,
) -> dict[str, object]:
    return {
        "goal_id": source.idea_id,
        "run_id": run_id,
        "updated_at": emitted_at,
        "source_path": source.canonical_relative_source_path,
        "canonical_source_path": source.canonical_relative_source_path,
        "current_artifact_path": source.current_artifact_relative_path,
        "profile_report_path": report_path,
        "specificity_level": decision.specificity_level,
        "shape_class": decision.shape_class,
        "classification": ContractorClassificationPayload(
            shape_class=decision.shape_class,
            archetype=decision.archetype,
            host_platform=decision.host_platform,
            stack_hints=decision.stack_hints,
            specializations=decision.specializations,
        ).model_dump(mode="json"),
        "candidate_classifications": [item.model_dump(mode="json") for item in decision.candidate_classifications],
        "confidence": decision.confidence,
        "fallback_mode": decision.fallback_mode,
        "resolved_profile_ids": decision.resolved_profile_ids,
        "unresolved_specializations": decision.unresolved_specializations,
        "specialization_provenance": [item.model_dump(mode="json") for item in decision.specialization_provenance],
        "capability_hints": decision.capability_hints,
        "environment_hints": decision.environment_hints,
        "browse_used": False,
        "browse_notes": "Local evidence was sufficient; no micro-browsing was required.",
        "evidence": decision.evidence,
        "abstentions": decision.abstentions,
        "contradictions": decision.contradictions,
        "notes": decision.notes,
    }


def _build_contractor_decision(*, paths: RuntimePaths, source: GoalSource) -> _ContractorDecision:
    body_text = source.canonical_body.casefold()
    text = f"{source.title}\n{source.canonical_body}".casefold()
    evidence: list[str] = []
    abstentions: list[str] = []
    contradictions: list[str] = []
    capability_hints: list[str] = []
    stack_hints: list[str] = []
    specializations: dict[str, str] = {}
    unresolved_specializations: list[str] = []
    candidate_scores = _shape_scores(text)
    shape_class = _select_shape_class(candidate_scores)
    example_shards = _select_example_shards(text=text, shape_class=shape_class)

    host_platform = _detect_host_platform(body_text) or _detect_host_platform(text)
    if host_platform:
        evidence.append(f"The goal explicitly references the host platform `{host_platform}`.")

    if shape_class == "platform_extension":
        evidence.append("The goal reads like host-loaded work rather than a standalone product.")
        if _contains_any(text, ("mod", "plugin")) and _contains_any(
            text,
            ("progression", "content", "gameplay", "registration"),
        ):
            archetype = "gameplay_mod"
            capability_hints.extend(("registration_assets", "progression_content"))
            evidence.append("The goal explicitly describes a host extension with bounded gameplay/content scope.")
        else:
            archetype = "plugin_integration"
            capability_hints.append("host_platform_integration")
    elif shape_class == "automation_tool":
        archetype = "compiler_toolchain" if "compiler" in text or "toolchain" in text else "developer_cli"
        capability_hints.extend(("command_surface", "exit_code_contract"))
        evidence.append("The goal centers on a developer-facing tool or command surface.")
    elif shape_class == "library_framework":
        archetype = "sdk_library"
        capability_hints.append("public_api_contract")
        evidence.append("The goal is framed as a reusable library or SDK.")
    elif shape_class == "data_system":
        archetype = "etl_pipeline" if _contains_any(text, ("etl", "pipeline", "ingestion")) else ""
        capability_hints.append("data_ingestion")
        evidence.append("The goal emphasizes data movement or storage behavior.")
    elif shape_class == "content_system":
        archetype = "content_pipeline"
        capability_hints.append("publishing_flow")
        evidence.append("The goal is content- or publishing-oriented.")
    elif shape_class == "network_application":
        if _contains_any(text, ("dashboard", "portal")):
            archetype = "dashboard_portal"
            capability_hints.append("dashboard_routes")
        elif _contains_any(text, ("workspace", "crm", "review", "inbox", "approval", "service")):
            archetype = "crud_business_system"
            capability_hints.append("workflow_state")
        else:
            archetype = ""
        evidence.append("The goal describes a networked product surface rather than a local tool.")
    elif shape_class == "service_backend":
        archetype = "crud_business_system" if _contains_any(text, ("workflow", "review", "inbox")) else ""
        capability_hints.append("api_contracts")
        evidence.append("The goal describes a backend or service-oriented runtime surface.")
    else:
        archetype = ""
        evidence.append("The goal does not justify more than a broad software-shape classification.")

    if _contains_any(text, ("java", "kotlin", "gradle", "jvm", "jar")):
        stack_hints.append("jvm")
    if _contains_any(text, ("gradle", "build.gradle", "settings.gradle", "gradle.kts")):
        stack_hints.append("gradle")
    if _contains_any(text, ("python", "pyproject", "pip", "typer", "click", "setuptools")):
        stack_hints.append("python_package")
    if _contains_any(text, ("react", "next.js", "nextjs", "frontend", "front-end")):
        stack_hints.append("react_frontend")
    if _contains_any(text, ("node", "nodejs", "express", "npm", "pnpm")):
        stack_hints.append("node_service")
    if _contains_any(text, ("postgres", "postgresql", "sql", "database")):
        stack_hints.append("postgres_backed")

    loader = _detect_loader(text)
    if loader:
        specializations["loader"] = loader
        unresolved_specializations.append(f"loader={loader}")
        abstentions.append("Loader-specific overlays remain unresolved.")

    if _contains_any(text, ("validation", "test", "gametest", "integration")):
        capability_hints.append("repo_native_behavior_tests")

    if _contains_any(text, ("auth", "login", "permission", "rbac")):
        capability_hints.append("auth_workflows")

    if _contains_any(text, ("service", "api")) and shape_class in {"network_application", "automation_tool", "library_framework"}:
        contradictions.append("The goal mixes standalone product language with service/backend cues.")
    if shape_class == "unknown":
        abstentions.append("No trustworthy host, archetype, or stack specialization is justified yet.")
    elif not host_platform and shape_class == "platform_extension":
        abstentions.append("The host platform is not explicit enough to justify a host-specific overlay.")
    elif not stack_hints and shape_class != "unknown":
        abstentions.append("No stack hints are explicit enough to justify stack overlays.")

    stack_hints_tuple = _ordered_unique(stack_hints)
    capability_hints_tuple = _ordered_unique(capability_hints)
    environment_hints = _environment_hints_for_stack(stack_hints_tuple)
    resolved_profile_ids = _resolved_profile_ids(
        shape_class=shape_class,
        archetype=archetype,
        host_platform=host_platform,
        stack_hints=stack_hints_tuple,
    )
    specificity_level = _specificity_level(
        shape_class=shape_class,
        archetype=archetype,
        host_platform=host_platform,
        stack_hints=stack_hints_tuple,
    )
    fallback_mode: ContractorFallbackMode
    if shape_class == "unknown":
        fallback_mode = "abstain_unknown"
    elif specificity_level == "L1":
        fallback_mode = "conservative_shape_only"
    else:
        fallback_mode = "apply_resolved_profiles_only"

    if not contradictions and candidate_scores["unknown"] >= 0.45:
        contradictions.append("The goal remains underspecified enough that later runs may need conservative fallback planning.")

    notes = (
        "Unsupported specialization signals are preserved as unresolved hints rather than resolved overlays."
        if unresolved_specializations
        else "The profile stays on supported broad layers only."
    )
    confidence = max(0.0, min(1.0, candidate_scores[shape_class]))
    candidate_classifications = _candidate_classifications(
        candidate_scores=candidate_scores,
        selected_shape=shape_class,
        archetype=archetype,
        host_platform=host_platform,
    )
    unresolved_specializations_tuple = _ordered_unique(unresolved_specializations)
    specialization_provenance = _specialization_provenance(
        paths=paths,
        source=source,
        specializations=specializations,
        unresolved_specializations=unresolved_specializations_tuple,
    )

    return _ContractorDecision(
        shape_class=shape_class,
        archetype=archetype,
        host_platform=host_platform,
        stack_hints=stack_hints_tuple,
        specializations=specializations,
        specificity_level=specificity_level,
        confidence=confidence,
        fallback_mode=fallback_mode,
        resolved_profile_ids=resolved_profile_ids,
        unresolved_specializations=unresolved_specializations_tuple,
        specialization_provenance=specialization_provenance,
        capability_hints=capability_hints_tuple,
        environment_hints=environment_hints,
        evidence=_ordered_unique(evidence),
        abstentions=_ordered_unique(abstentions),
        contradictions=_ordered_unique(contradictions),
        notes=notes,
        candidate_classifications=candidate_classifications,
        example_shards=example_shards,
    )


def _shape_scores(text: str) -> dict[ContractorShapeClass, float]:
    scores: dict[ContractorShapeClass, float] = {
        "platform_extension": 0.0,
        "interactive_application": 0.0,
        "network_application": 0.0,
        "service_backend": 0.0,
        "automation_tool": 0.0,
        "library_framework": 0.0,
        "data_system": 0.0,
        "content_system": 0.0,
        "unknown": 0.45,
    }
    if _contains_any(text, ("plugin", "extension", "mod", "host-loaded", "bot for", "app for")):
        scores["platform_extension"] += 0.56
    if _contains_any(text, ("web app", "dashboard", "portal", "crm", "workspace", "review queue", "support tool")):
        scores["network_application"] += 0.34
    if _contains_any(text, ("service", "backend", "api", "webhook", "worker")):
        scores["service_backend"] += 0.28
    if _contains_any(text, ("cli", "command line", "automation tool", "script", "toolchain", "compiler")):
        scores["automation_tool"] += 0.38
    if _contains_any(text, ("sdk", "library", "framework", "package")):
        scores["library_framework"] += 0.38
    if _contains_any(text, ("etl", "pipeline", "warehouse", "dataset", "analytics")):
        scores["data_system"] += 0.4
    if _contains_any(text, ("cms", "content", "publishing", "documentation site", "knowledge base")):
        scores["content_system"] += 0.34
    if _contains_any(text, ("desktop app", "mobile app", "game", "editor", "interactive")):
        scores["interactive_application"] += 0.3
    if max(score for shape, score in scores.items() if shape != "unknown") >= 0.55:
        scores["unknown"] = 0.12
    return scores


def _select_shape_class(candidate_scores: dict[ContractorShapeClass, float]) -> ContractorShapeClass:
    ordered = sorted(candidate_scores.items(), key=lambda item: item[1], reverse=True)
    shape, score = ordered[0]
    if shape == "unknown" or score < 0.35:
        return "unknown"
    return shape


def _select_example_shards(*, text: str, shape_class: ContractorShapeClass) -> tuple[str, ...]:
    selected = [_SHAPES_FILE]
    if shape_class == "platform_extension" or _contains_any(text, ("plugin", "extension", "mod", "host-loaded")):
        selected.append(_PLATFORM_FILE)
    elif shape_class in {"network_application", "service_backend"} or _contains_any(
        text, ("web app", "dashboard", "portal", "crm", "workspace", "support")
    ):
        selected.append(_WEB_FILE)
    elif shape_class in {"automation_tool", "library_framework"} or _contains_any(
        text, ("cli", "sdk", "library", "compiler", "package")
    ):
        selected.append(_TOOLS_FILE)
    if shape_class == "unknown" or _contains_any(text, ("maybe", "unsure", "mixed", "unclear")):
        selected.append(_AMBIGUOUS_FILE)
    return _ordered_unique(selected)


def _specificity_level(
    *,
    shape_class: ContractorShapeClass,
    archetype: str,
    host_platform: str,
    stack_hints: tuple[str, ...],
) -> ContractorSpecificityLevel:
    if shape_class == "unknown":
        return "L0"
    if stack_hints:
        return "L4"
    if host_platform:
        return "L3"
    if archetype:
        return "L2"
    return "L1"


def _resolved_profile_ids(
    *,
    shape_class: ContractorShapeClass,
    archetype: str,
    host_platform: str,
    stack_hints: tuple[str, ...],
) -> tuple[str, ...]:
    profile_ids: list[str] = []
    if shape_class != "unknown":
        profile_ids.append(f"shape.{shape_class}@1")
    if archetype:
        profile_ids.append(f"archetype.{archetype}@1")
    if host_platform:
        profile_ids.append(f"host.{host_platform}@1")
    if "jvm" in stack_hints and "gradle" in stack_hints:
        profile_ids.append("stack.jvm_gradle@1")
    for stack_hint in stack_hints:
        if stack_hint in {"jvm", "gradle"}:
            continue
        profile_id = _STACK_PROFILE_IDS.get(stack_hint)
        if profile_id:
            profile_ids.append(profile_id)
    return _ordered_unique(profile_ids)


def _candidate_classifications(
    *,
    candidate_scores: dict[ContractorShapeClass, float],
    selected_shape: ContractorShapeClass,
    archetype: str,
    host_platform: str,
) -> tuple[ContractorClassificationCandidate, ...]:
    ordered = sorted(candidate_scores.items(), key=lambda item: item[1], reverse=True)[:2]
    candidates: list[ContractorClassificationCandidate] = []
    for shape, score in ordered:
        label = shape
        if shape == selected_shape and archetype:
            label = f"{label}/{archetype}"
            if host_platform:
                label = f"{label}/{host_platform}"
        candidates.append(ContractorClassificationCandidate(label=label, score=max(0.0, min(1.0, score))))
    return tuple(candidates)


def _specialization_provenance(
    *,
    paths: RuntimePaths,
    source: GoalSource,
    specializations: dict[str, str],
    unresolved_specializations: tuple[str, ...],
) -> tuple[GoalSpecSpecializationRecord, ...]:
    if not specializations:
        return ()
    records: list[GoalSpecSpecializationRecord] = []
    unresolved_tokens = set(unresolved_specializations)
    canonical_text = source.canonical_body.casefold()
    for key, value in specializations.items():
        token = f"{key}={value}"
        support_state = "unsupported" if token in unresolved_tokens else "supported"
        grounded_hint = _specialization_grounded_hint(key=key, value=value)
        if grounded_hint in canonical_text:
            records.append(
                GoalSpecSpecializationRecord(
                    key=key,
                    value=value,
                    provenance="source_requested",
                    support_state=support_state,
                    evidence_path=source.canonical_relative_source_path,
                    evidence=(f"The canonical source explicitly references `{value}`.",),
                    notes="Specialization request preserved from the source goal.",
                )
            )
        workspace_evidence = _probe_workspace_specialization_evidence(paths=paths, key=key, value=value)
        if workspace_evidence is not None:
            evidence_path, evidence_summary, notes = workspace_evidence
            records.append(
                GoalSpecSpecializationRecord(
                    key=key,
                    value=value,
                    provenance="workspace_grounded",
                    support_state=support_state,
                    evidence_path=evidence_path,
                    evidence=(evidence_summary,),
                    notes=notes,
                )
            )
        if support_state == "supported":
            records.append(
                GoalSpecSpecializationRecord(
                    key=key,
                    value=value,
                    provenance="contractor_resolved",
                    support_state="supported",
                    evidence=("Contractor resolved this specialization into a supported overlay.",),
                    notes="This specialization is safe for downstream consumers to treat as supported.",
                )
            )
    return tuple(records)


def _environment_hints_for_stack(stack_hints: tuple[str, ...]) -> tuple[str, ...]:
    hints: list[str] = []
    for stack_hint in stack_hints:
        hints.extend(_STACK_ENVIRONMENT_HINTS.get(stack_hint, ()))
    return _ordered_unique(hints)


def _detect_host_platform(text: str) -> str:
    for pattern in _HOST_PLATFORM_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        host = _normalize_host_platform(match.group(1))
        if host:
            return host
    return ""


def _normalize_host_platform(raw: str) -> str:
    tokens = [token for token in re.split(r"[\s/_-]+", raw.casefold()) if token]
    filtered = [token for token in tokens if token not in _HOST_PLATFORM_STOPWORDS]
    if not filtered:
        return ""
    return "-".join(filtered)


def _detect_loader(text: str) -> str:
    if "fabric" in text:
        return "fabric"
    if "neoforge" in text:
        return "neoforge"
    if "forge" in text:
        return "forge"
    return ""


def _specialization_grounded_hint(*, key: str, value: str) -> str:
    if key == "loader":
        return value.casefold()
    return value.casefold()


def _probe_workspace_specialization_evidence(
    *,
    paths: RuntimePaths,
    key: str,
    value: str,
) -> tuple[str, str, str] | None:
    if key != "loader":
        return None
    return _probe_loader_workspace_evidence(paths.root, value.casefold())


def _probe_loader_workspace_evidence(
    workspace_root: Path,
    loader: str,
) -> tuple[str, str, str] | None:
    if loader == "fabric":
        candidate = _first_existing_workspace_path(
            workspace_root,
            direct_paths=(
                "fabric.mod.json",
                "src/main/resources/fabric.mod.json",
            ),
            glob_patterns=(
                "mods/*/src/main/resources/fabric.mod.json",
                "*/src/main/resources/fabric.mod.json",
            ),
        )
        if candidate is not None:
            relative = candidate.relative_to(workspace_root).as_posix()
            return (
                relative,
                "Workspace repo evidence includes Fabric loader metadata.",
                "Local repo files ground the requested Fabric loader without promoting it to a supported overlay.",
            )
        return None
    if loader in {"forge", "neoforge"}:
        token = "neoforge" if loader == "neoforge" else "forge"
        candidate = _first_workspace_text_match(
            workspace_root,
            direct_paths=(
                "build.gradle",
                "build.gradle.kts",
                "gradle.properties",
                "settings.gradle",
                "settings.gradle.kts",
                "src/main/resources/META-INF/mods.toml",
            ),
            glob_patterns=(
                "mods/*/build.gradle",
                "mods/*/build.gradle.kts",
                "mods/*/gradle.properties",
                "mods/*/src/main/resources/META-INF/mods.toml",
            ),
            token=token,
        )
        if candidate is not None:
            relative = candidate.relative_to(workspace_root).as_posix()
            return (
                relative,
                f"Workspace repo evidence references the `{loader}` loader.",
                f"Local repo files ground the requested `{loader}` loader without promoting it to a supported overlay.",
            )
    return None


def _first_existing_workspace_path(
    workspace_root: Path,
    *,
    direct_paths: tuple[str, ...],
    glob_patterns: tuple[str, ...],
) -> Path | None:
    for relative_path in direct_paths:
        candidate = workspace_root / relative_path
        if candidate.exists():
            return candidate
    for pattern in glob_patterns:
        for candidate in sorted(workspace_root.glob(pattern)):
            if candidate.exists():
                return candidate
    return None


def _first_workspace_text_match(
    workspace_root: Path,
    *,
    direct_paths: tuple[str, ...],
    glob_patterns: tuple[str, ...],
    token: str,
) -> Path | None:
    lowered_token = token.casefold()
    for relative_path in direct_paths:
        candidate = workspace_root / relative_path
        if _workspace_file_contains(candidate, lowered_token):
            return candidate
    for pattern in glob_patterns:
        for candidate in sorted(workspace_root.glob(pattern)):
            if _workspace_file_contains(candidate, lowered_token):
                return candidate
    return None


def _workspace_file_contains(path: Path, token: str) -> bool:
    if not path.exists() or not path.is_file():
        return False
    return token in path.read_text(encoding="utf-8", errors="replace").casefold()


def _render_contractor_report(
    *,
    source: GoalSource,
    run_id: str,
    emitted_at: datetime,
    decision: _ContractorDecision,
    profile: ContractorProfileArtifact,
    schema_path: str,
) -> str:
    lines = [
        f"# Contractor Profile: {source.title}",
        "",
        f"- **Run-ID:** {run_id}",
        f"- **Goal-ID:** {source.idea_id}",
        f"- **Updated-At:** {_isoformat_z(emitted_at)}",
        f"- **Canonical-Source-Path:** `{source.canonical_relative_source_path}`",
        f"- **Current-Artifact-Path:** `{source.current_artifact_relative_path}`",
        f"- **Schema:** `{schema_path}`",
        "",
        "## Classification",
        f"- **Shape-Class:** `{profile.shape_class}`",
        f"- **Specificity-Level:** `{profile.specificity_level}`",
        f"- **Fallback-Mode:** `{profile.fallback_mode}`",
        f"- **Archetype:** `{profile.classification.archetype or 'none'}`",
        f"- **Host-Platform:** `{profile.classification.host_platform or 'none'}`",
        (
            f"- **Stack-Hints:** {', '.join(f'`{item}`' for item in profile.classification.stack_hints)}"
            if profile.classification.stack_hints
            else "- **Stack-Hints:** none"
        ),
        (
            f"- **Resolved-Profile-IDs:** {', '.join(f'`{item}`' for item in profile.resolved_profile_ids)}"
            if profile.resolved_profile_ids
            else "- **Resolved-Profile-IDs:** none"
        ),
        (
            "- **Specialization-Provenance:** "
            + "; ".join(_format_specialization_record(item) for item in profile.specialization_provenance)
            if profile.specialization_provenance
            else "- **Specialization-Provenance:** none"
        ),
        "",
        "## Example Shards",
        "- Selected from `EXAMPLES_INDEX.md` for this classification pass:",
        *(f"  - `{item}`" for item in decision.example_shards),
        "",
        "## Evidence",
        *(f"- {item}" for item in profile.evidence),
        "",
        "## Abstentions",
        *(f"- {item}" for item in profile.abstentions),
        *(["- None."] if not profile.abstentions else []),
        "",
        "## Contradictions",
        *(f"- {item}" for item in profile.contradictions),
        *(["- None."] if not profile.contradictions else []),
        "",
        "## Browse Policy",
        "- `browse_used`: `false`",
        "- Local evidence was sufficient; no micro-browsing was required.",
        "",
        "## Notes",
        profile.notes or "None.",
        "",
    ]
    return "\n".join(lines)


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _ordered_unique(items) -> tuple:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return tuple(ordered)


def _format_specialization_record(record: GoalSpecSpecializationRecord) -> str:
    path_suffix = f" @ `{record.evidence_path}`" if record.evidence_path else ""
    return f"`{record.key}={record.value}` ({record.provenance}, {record.support_state}{path_suffix})"


__all__ = ["execute_contractor"]
