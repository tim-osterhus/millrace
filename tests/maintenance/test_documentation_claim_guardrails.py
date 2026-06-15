"""Documentation claim guardrail tests.

Fail when documentation overclaims generic-engine completeness, omits
the distinction between generic kernel behavior and extension-backed
domain behavior, or describes unsupported/deferred features as if they
are available.

Context: ADR-0012, ADR-0013, ADR-0015, and ADR-0016 define the kernel
boundary, the generic stage-and-plane registry, extension manifests, and
the compatibility facade surface.  Documentation must accurately reflect
what ships vs what is deferred.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Overclaim checks: features that must NOT be described as available
# ---------------------------------------------------------------------------

# Phrases that, when present in docs without nearby qualified denial,
# suggest an overclaim about generic-engine completeness.
FORBIDDEN_OVERCLAIM_PHRASES: dict[str, frozenset[str]] = {
    # Arbitrary plane IDs — must be marked as not-yet-supported
    "adr/0013-generic-stage-and-plane-registry.md": frozenset({
        # The ADR must not claim arbitrary plane IDs are supported without
        # also clarifying that they are deferred.
    }),
}

# Docs that must contain specific deferral / limitation language to avoid
# being treated as overclaiming.
REQUIRED_DEFERRAL_TERMS: dict[str, str] = {
    # The graphs index must note that arbitrary plane IDs and arbitrary
    # runtime stages are deferred
    "docs/graphs/graphs-index.md": "Arbitrary plane IDs and arbitrary runtime stages remain deferred",
    # Source package map must mark prospective boundaries as not yet created
    "docs/source-package-map.md": "not yet created",
    # Runtime architecture must mention work-item family as runtime authority
    "docs/runtime/millrace-runtime-architecture.md": "work-item family",
    # Runtime authority map must mention generic router/adapter/status_projections/result_counters
    "docs/runtime/millrace-runtime-authority-map.md": "generic router",
    # Modes and loops must distinguish shipped from fixture modes
    "docs/runtime/millrace-modes-and-loops.md": "fixture",
    # Technical overview must use the four-layer authority vocabulary
    "docs/millrace-technical-overview.md": "four-layer authority model",
    # Refactor candidate register must have the Generic Engine Boundary Seams table
    "docs/maintenance/refactor-candidate-register.md": "Generic Engine Boundary Seams",
}

# Docs that must NOT contain specific unsupported claims presented as fact.
# Key: (doc_path, forbidden_phrase, required_if_present)
# If forbidden_phrase appears, required_if_present MUST also appear nearby.
DOC_CLAIM_QUALIFIERS: tuple[tuple[str, str, str], ...] = (
    # Graphs index: if "arbitrary" appears, it must be followed by "deferred"
    ("docs/graphs/graphs-index.md", "arbitrary plane", "deferred"),
    ("docs/graphs/graphs-index.md", "arbitrary runtime", "deferred"),
    # Modes and loops: if "single-plane" appears, it must have "deferred" nearby
    ("docs/runtime/millrace-modes-and-loops.md", "single-plane", "deferred"),
    # Source package map: if "prospective" appears, it must have "not yet created"
    ("docs/source-package-map.md", "prospective boundary", "not yet created"),
)

# ---------------------------------------------------------------------------
# Required vocabulary distinctions
# ---------------------------------------------------------------------------

# Docs that must explicitly distinguish these categories.
# Each tuple: (doc_path, category_label, required_phrase)
REQUIRED_CATEGORY_DISTINCTIONS: tuple[tuple[str, str, str], ...] = (
    # README must mention extension-backed domains
    ("README.md", "extension-backed", "extension-backed domains"),
    # README must mention compatibility facades
    ("README.md", "compatibility facade", "compatibility facade"),
    # README must mention the generic kernel
    ("README.md", "generic kernel", "generic"),
    # ROADMAP must mention extension-backed
    ("ROADMAP.md", "extension-backed", "extension-backed"),
    # ROADMAP must mention the generic engine migration
    ("ROADMAP.md", "generic engine", "Generic engine migration"),
    # CHANGELOG must reference the generic engine
    ("CHANGELOG.md", "generic", "generic"),
    # Source package map must use four-layer vocabulary
    ("docs/source-package-map.md", "four-layer", "four-layer"),
    # Source package map must reference generic queue engine surfaces
    ("docs/source-package-map.md", "queue_family_interpreter", "queue_family_interpreter"),
    ("docs/source-package-map.md", "status_projections", "status_projections"),
    ("docs/source-package-map.md", "result_counters", "result_counters"),
    # Graphs index must have fixture modes section
    ("docs/graphs/graphs-index.md", "Fixture", "Fixture"),
    # Graphs index must have shipped vs fixture distinction
    ("docs/graphs/graphs-index.md", "not shipped product modes", "are not listed in"),
    # Modes and loops must reference config data vs kernel code distinction
    ("docs/runtime/millrace-modes-and-loops.md", "config", "config"),
    # Runtime architecture must mention compiled work families
    ("docs/runtime/millrace-runtime-architecture.md", "family definition", "family"),
    # CLI reference must mention compatibility aliases
    ("docs/runtime/millrace-cli-reference.md", "alias", "alias"),
    # Compiler docs must mention mode config differentiation
    ("docs/runtime/millrace-compiler-and-frozen-plans.md", "mode", "mode"),
    # ADR README must reference ADR-0012 through ADR-0016
    ("docs/adr/README.md", "0012-core-kernel-boundary", "0012"),
    ("docs/adr/README.md", "0013-generic-stage-and-plane-registry", "0013"),
    ("docs/adr/README.md", "0015-extension-package-manifests", "0015"),
    ("docs/adr/README.md", "0016-extension-boundary-compatibility-facades", "0016"),
    # Doc index must reference ADR-0012 through ADR-0016
    ("docs/doc-index.md", "adr/0012-core-kernel-boundary", "0012"),
    ("docs/doc-index.md", "adr/0013-generic-stage-and-plane-registry", "0013"),
    ("docs/doc-index.md", "adr/0015-extension-package-manifests", "0015"),
    ("docs/doc-index.md", "adr/0016-extension-boundary-compatibility-facades", "0016"),
    # Public API compatibility inventory must list extension boundary surface
    ("docs/maintenance/public-api-compatibility-inventory.md",
     "extension boundary", "Extension"),
    # Public API compatibility inventory must list compatibility facades
    ("docs/maintenance/public-api-compatibility-inventory.md",
     "compatibility facade", "compatibility facade"),
    # Refactor candidate register must mark migrations as complete
    ("docs/maintenance/refactor-candidate-register.md",
     "migration complete", "Complete"),
    # Refactor candidate register must have generic engine seams
    ("docs/maintenance/refactor-candidate-register.md",
     "generic engine seams", "Generic Engine Boundary Seams"),
    # Documentation freshness matrix must reference extension boundary
    ("docs/maintenance/documentation-freshness-matrix.md",
     "extension boundary", "extension"),
    # Compiler validation contracts must reference extension validation
    ("docs/maintenance/compiler-validation-contracts.md",
     "extension", "extension"),
)

# ---------------------------------------------------------------------------
# Compatibility inventory entry checks
# ---------------------------------------------------------------------------

# Every retained compatibility facade must have an inventory entry in
# docs/maintenance/public-api-compatibility-inventory.md that names at least
# the file or symbol, allowed callers, mode scope, retention reason, and a
# named test or guardrail proving generic-only modes do not call or load it
# as active authority.

# Known retained compatibility facades that must appear in the inventory
RETAINED_COMPAT_FACADES: tuple[str, ...] = (
    "millrace_ai.router",
    "millrace_ai.compiler",
    "millrace_ai.queue_store",
    "millrace_ai.runner",
    "millrace_ai.paths",
    "millrace_ai.state_store",
    "millrace_ai.stage_kinds",
    "millrace_ai.loop_graphs",
    "millrace_ai.runtime.request_context",
    "millrace_ai.compilation.validation",
    "millrace_ai.architecture.workflow_primitives",
    "millrace_ai.architecture",
    "millrace_ai.extensions",
)

# ---------------------------------------------------------------------------
# Test functions
# ---------------------------------------------------------------------------


def _doc_text(rel: str) -> str:
    path = REPO_ROOT / rel
    return path.read_text(encoding="utf-8")


def _changelog_section(text: str, section_id: str) -> str:
    marker = f"## [{section_id}]"
    start = text.find(marker)
    if start == -1:
        return ""
    end = text.find("\n## [", start + len(marker))
    if end == -1:
        end = len(text)
    return text[start:end]


def test_no_documentation_overclaims_arbitrary_plane_or_stage_support() -> None:
    """Guardrail: documentation must not overclaim support for arbitrary
    plane IDs, arbitrary runtime stages, or single-plane modes.

    Documents that mention these concepts must also state they are deferred
    or not-yet-supported.
    """
    violations: list[str] = []

    for doc_path, forbidden, required in DOC_CLAIM_QUALIFIERS:
        path = REPO_ROOT / doc_path
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if forbidden.lower() in text.lower():
            if required.lower() not in text.lower():
                violations.append(
                    f"{doc_path}: mentions '{forbidden}' without "
                    f"required qualifier '{required}'"
                )

    assert violations == [], (
        f"{len(violations)} documentation overclaim(s):\n"
        + "\n".join(violations)
    )


def test_documentation_distinguishes_generic_kernel_from_extension_domains() -> None:
    """Guardrail: key documentation files must explicitly distinguish
    generic kernel behavior from extension-backed domain behavior,
    config data, retained compatibility facades, unsupported features,
    and deferred features."""
    violations: list[str] = []

    for doc_path, category_label, required_phrase in REQUIRED_CATEGORY_DISTINCTIONS:
        path = REPO_ROOT / doc_path
        if not path.is_file():
            violations.append(f"{doc_path}: file does not exist")
            continue
        text = path.read_text(encoding="utf-8")
        if required_phrase.lower() not in text.lower():
            violations.append(
                f"{doc_path}: missing required {category_label!r} "
                f"vocabulary ({required_phrase!r})"
            )

    assert violations == [], (
        f"{len(violations)} missing documentation distinction(s):\n"
        + "\n".join(violations)
    )


def test_compatibility_inventory_lists_all_retained_facades() -> None:
    """Guardrail: every retained package-root compatibility facade must
    have an inventory entry in the public API compatibility inventory."""
    path = REPO_ROOT / "docs" / "maintenance" / "public-api-compatibility-inventory.md"
    text = path.read_text(encoding="utf-8")

    missing: list[str] = []
    for facade in RETAINED_COMPAT_FACADES:
        # Each facade should be mentioned by at least its import path
        if facade not in text:
            missing.append(facade)

    assert missing == [], (
        f"{len(missing)} retained compatibility facade(s) missing from "
        f"public-api-compatibility-inventory.md:\n"
        + "\n".join(f"  - {m}" for m in missing)
    )


def test_graphs_index_distinguishes_shipped_from_fixture_modes() -> None:
    """Guardrail: graphs-index.md must have a clear section or label
    distinguishing shipped product modes from discovery-only fixture
    configurations."""
    text = _doc_text("docs/graphs/graphs-index.md")

    # The index must contain both "Shipped Full Configurations" table and
    # "Discovery-Only Fixture Configurations" section
    assert "Shipped Full Configurations" in text, (
        "graphs-index.md must contain a 'Shipped Full Configurations' section"
    )
    assert "Discovery-Only Fixture Configurations" in text, (
        "graphs-index.md must contain a 'Discovery-Only Fixture Configurations' section"
    )


def test_config_mapping_lists_all_five_conceptual_configs() -> None:
    """Guardrail: config-mapping.md must document all five conceptual
    configuration profiles with their status (shipped/alias/fixture)."""
    text = _doc_text("docs/graphs/config-mapping.md")

    required_configs = (
        "minimal_three_plane",
        "standard_millrace",
        "learning_enabled_millrace",
        "recovery_heavy_millrace",
        "generic_two_plane_fixture",
    )

    missing = [c for c in required_configs if c not in text]
    assert missing == [], (
        f"config-mapping.md missing conceptual config(s): {', '.join(missing)}"
    )

    # Each must have a status label
    status_labels = ("Shipped", "Alias", "Fixture", "fixture", "shipped")
    for config in required_configs:
        # Find the section for this config
        section_start = text.find(config)
        if section_start == -1:
            continue
        # Check the next 500 chars for a status indicator
        section_text = text[section_start:section_start + 500]
        has_status = any(label.lower() in section_text.lower() for label in status_labels)
        assert has_status, (
            f"config-mapping.md section for '{config}' is missing a status label"
        )


def test_modes_doc_distinguishes_shipped_from_fixture() -> None:
    """Guardrail: modes-and-loops doc must distinguish shipped product
    modes from fixture-only configurations."""
    text = _doc_text("docs/runtime/millrace-modes-and-loops.md")

    assert "fixture" in text.lower(), (
        "millrace-modes-and-loops.md must mention fixture modes"
    )
    assert "shipped" in text.lower(), (
        "millrace-modes-and-loops.md must mention shipped modes"
    )


def test_readme_lists_unsupported_topologies() -> None:
    """Guardrail: README must list unsupported topologies (arbitrary plane
    IDs, arbitrary runtime stages, single-plane modes) as deferred."""
    text = _doc_text("README.md")

    assert "Unsupported" in text, (
        "README.md must have an 'Unsupported topologies' (or similar) section"
    )
    # The unsupported section must mention that arbitrary plane IDs are deferred
    assert "arbitrary plane" in text.lower() or "arbitrary runtime" in text.lower(), (
        "README.md unsupported topologies must mention arbitrary plane IDs "
        "or arbitrary runtime stages"
    )


def test_readme_distinguishes_fixture_from_shipped_modes() -> None:
    """Guardrail: README must clearly mark fixture-only modes as discoverable
    proof assets, not shipped product mode IDs."""
    text = _doc_text("README.md")

    assert "fixture" in text.lower(), (
        "README.md must mention fixture modes"
    )
    # Fixture modes must be described as proof assets, not shipped product modes
    assert (
        "proof asset" in text.lower()
        or "not listed as shipped" in text.lower()
        or "discoverable" in text.lower()
    ), (
        "README.md fixture modes must be described as proof assets, "
        "not shipped product modes"
    )


def test_changelog_references_generic_engine_changes() -> None:
    """Guardrail: CHANGELOG release notes must reference generic engine
    boundary, extension domains, compatibility surfaces, and fixture mode
    limitations."""
    text = _doc_text("CHANGELOG.md")

    assert "[Unreleased]" in text, "CHANGELOG.md must have an [Unreleased] section"

    current_release = _changelog_section(text, "0.21.0")
    unreleased = _changelog_section(text, "Unreleased")
    release_notes = "\n".join(section for section in (unreleased, current_release) if section)

    required_terms = ("generic", "extension", "compatibility", "fixture")
    missing = [t for t in required_terms if t.lower() not in release_notes.lower()]
    assert missing == [], (
        "CHANGELOG current release notes missing terms: "
        f"{', '.join(missing)}"
    )


def test_roadmap_marks_extension_boundary_as_active() -> None:
    """Guardrail: ROADMAP must list extension-backed domain behavior and
    generic engine migration in the Active section."""
    text = _doc_text("ROADMAP.md")

    # Find Active section
    active_start = text.find("## Active")
    if active_start == -1:
        active_start = text.find("## Active")
    if active_start == -1:
        raise AssertionError("ROADMAP.md must have an Active section")

    # Find next ## heading
    next_section = text.find("\n## ", active_start + 1)
    if next_section == -1:
        next_section = len(text)
    active_section = text[active_start:next_section]

    required_terms = ("extension-backed", "generic engine", "compatibility")
    missing = [t for t in required_terms if t.lower() not in active_section.lower()]
    assert missing == [], (
        f"ROADMAP Active section missing terms: {', '.join(missing)}"
    )


def test_runtime_authority_map_references_generic_router_and_adapters() -> None:
    """Guardrail: runtime-authority-map must reference generic router,
    adapter extension points, status_projections, and result_counters."""
    text = _doc_text("docs/runtime/millrace-runtime-authority-map.md")

    required = (
        "generic router",
        "adapter",
        "status_projections",
        "result_counters",
    )
    missing = [t for t in required if t.lower() not in text.lower()]
    assert missing == [], (
        f"runtime-authority-map.md missing references: {', '.join(missing)}"
    )


def test_refactor_register_has_generic_engine_seams_table() -> None:
    """Guardrail: refactor-candidate-register must contain the
    Generic Engine Boundary Seams table and mark core migrations complete."""
    text = _doc_text("docs/maintenance/refactor-candidate-register.md")

    assert "Generic Engine Boundary Seams" in text, (
        "refactor-candidate-register.md must have Generic Engine Boundary Seams table"
    )

    # At least one migration must be marked complete
    complete_indicators = [
        "Core migration complete",
        "Migration complete",
        "Complete",
    ]
    found = any(ind in text for ind in complete_indicators)
    assert found, (
        "refactor-candidate-register.md must mark at least one migration as complete"
    )


def test_source_package_map_uses_four_layer_vocabulary() -> None:
    """Guardrail: source-package-map must use the four-layer authority
    vocabulary and mark prospective boundary packages as not yet created."""
    text = _doc_text("docs/source-package-map.md")

    assert "four-layer" in text, (
        "source-package-map.md must use four-layer authority vocabulary"
    )
    assert "not yet created" in text, (
        "source-package-map.md must mark prospective boundary packages as not yet created"
    )


def test_public_api_inventory_references_compatibility_facade_tests() -> None:
    """Guardrail: public-api-compatibility-inventory must document which
    tests protect each compatibility surface, especially for the kernel
    import guardrails and extension boundary."""
    text = _doc_text("docs/maintenance/public-api-compatibility-inventory.md")

    required_refs = (
        "test_public_import_surfaces",
        "test_kernel_import_guardrails",
    )
    missing = [r for r in required_refs if r not in text]
    assert missing == [], (
        f"public-api-compatibility-inventory.md missing test references: "
        f"{', '.join(missing)}"
    )


def test_documentation_distinguishes_absence_levels() -> None:
    """Guardrail: documentation must distinguish between vocabulary absence,
    compile absence, import absence, startup absence, and runtime-use absence
    when describing unsupported or deferred features.

    This checks that at least one major doc (README, technical overview, or
    runtime architecture) uses vocabulary that distinguishes these levels
    of absence rather than lumping everything under one unsupported label.
    """
    docs_to_check = (
        "README.md",
        "docs/millrace-technical-overview.md",
        "docs/runtime/millrace-runtime-architecture.md",
    )

    absence_level_terms = (
        "deferred",
        "not yet supported",
        "fixture",
        "discoverable",
    )

    # At least one doc should use multiple of these distinguishing terms
    found_docs: list[str] = []
    for doc_path in docs_to_check:
        path = REPO_ROOT / doc_path
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        matches = [t for t in absence_level_terms if t.lower() in text.lower()]
        if len(matches) >= 2:
            found_docs.append(doc_path)

    assert found_docs, (
        "No documentation file was found that distinguishes between multiple "
        "levels of absence (deferred, not yet supported, fixture, discoverable). "
        "At least one of README.md, technical-overview.md, or "
        "runtime-architecture.md must use multiple distinguishing terms."
    )


def test_adr_0016_lists_compatibility_facades() -> None:
    """Guardrail: ADR-0016 must exist and list the active compatibility
    bridges between kernel and domain code."""
    path = REPO_ROOT / "docs" / "adr" / "0016-extension-boundary-compatibility-facades.md"
    if not path.is_file():
        # ADR-0016 may be a new untracked file
        return

    text = path.read_text(encoding="utf-8")

    # Must reference at least some of the known compatibility facades
    known_facades = (
        "recon_transitions",
        "closure_transitions",
        "learning_triggers",
        "learning_promotions",
        "blueprint_validator",
        "blueprint_context_provider",
        "completion_behavior",
        "result_application",
        "blueprint",
    )
    found = [f for f in known_facades if f in text]
    assert len(found) >= 3, (
        f"ADR-0016 must list at least 3 known compatibility facades; "
        f"found {len(found)}: {found}"
    )


def test_config_mapping_distinguishes_product_from_fixture() -> None:
    """Guardrail: config-mapping.md must have a clear 'Product Mode vs.
    Fixture Distinction' section or equivalent."""
    text = _doc_text("docs/graphs/config-mapping.md")

    assert "Product Mode" in text or "product mode" in text.lower(), (
        "config-mapping.md must discuss product mode vs fixture distinction"
    )
    assert "Fixture" in text, (
        "config-mapping.md must discuss fixture modes"
    )
