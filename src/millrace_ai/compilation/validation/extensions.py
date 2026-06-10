"""Extension manifest compile-time validation.

Validates that required extension declarations in mode configs are satisfied
by discovered extension package manifests.  Rejects missing or unavailable
extensions with clear compiler diagnostics.

Also checks that graph-loop stage-kind vocabulary owned by undeclared
extension domains is rejected at compile time rather than discovered
at runtime.

ADRs: ADR-0012, ADR-0015.
"""

from __future__ import annotations

from packaging.version import InvalidVersion, Version

from millrace_ai.architecture import GraphLoopDefinition, RegisteredStageKindDefinition
from millrace_ai.contracts import ModeDefinition, Plane
from millrace_ai.contracts.extensions import RequiredExtensionDeclaration
from millrace_ai.extensions import ExtensionDomain, ExtensionPackageManifest

from ..outcomes import CompilerValidationError

# ---------------------------------------------------------------------------
# Domain → extension package id mapping for built-in extensions.
# Each shipped built-in extension manifest follows this convention.
# ---------------------------------------------------------------------------

_BUILTIN_DOMAIN_PACKAGE_IDS: dict[ExtensionDomain, str] = {
    ExtensionDomain.GENERIC: "millrace.generic",
    ExtensionDomain.RECON: "millrace.recon",
    ExtensionDomain.CLOSURE: "millrace.closure",
    ExtensionDomain.BLUEPRINT: "millrace.blueprint",
    ExtensionDomain.LEARNING: "millrace.learning",
}


# Known built-in stage-kind ids that belong to specific extension domains.
# Custom stage kinds (basic_worker, basic_planner, basic_learner) are NOT
# listed here because they reuse generic lifecycle outcomes and skills.
_BUILTIN_DOMAIN_STAGE_KINDS: dict[str, ExtensionDomain] = {
    "recon": ExtensionDomain.RECON,
    "arbiter": ExtensionDomain.CLOSURE,
    "manager_blueprint": ExtensionDomain.BLUEPRINT,
    "contractor_blueprint": ExtensionDomain.BLUEPRINT,
    "evaluator_blueprint": ExtensionDomain.BLUEPRINT,
    "mechanic_blueprint": ExtensionDomain.BLUEPRINT,
    "analyst": ExtensionDomain.LEARNING,
    "professor": ExtensionDomain.LEARNING,
    "curator": ExtensionDomain.LEARNING,
    "librarian": ExtensionDomain.LEARNING,
}

# Known terminal action ids that belong to specific extension domains.
# Generic terminal actions (complete_work_item, block_work_item, etc.) are
# NOT listed here because they are runtime-owned.
_BUILTIN_DOMAIN_TERMINAL_ACTIONS: dict[str, ExtensionDomain] = {
    "recon_enqueue_task": ExtensionDomain.RECON,
    "recon_enqueue_spec": ExtensionDomain.RECON,
    "recon_noop": ExtensionDomain.RECON,
    "recon_block_work_item": ExtensionDomain.RECON,
    "closure_pass": ExtensionDomain.CLOSURE,
    "closure_gap": ExtensionDomain.CLOSURE,
    "closure_blocked": ExtensionDomain.CLOSURE,
}


def _derive_stage_kind_domain(
    stage_kind_id: str,
    stage_kind: RegisteredStageKindDefinition | None,
) -> ExtensionDomain | None:
    """Return the extension domain a stage kind belongs to, or None.

    Only well-known built-in domain-specific stage kinds are mapped.
    Custom or fixture stage kinds that reuse generic lifecycle outcomes
    and entrypoints (e.g. basic_worker, basic_planner, basic_learner)
    are NOT assigned to a domain-specific extension.
    """
    kid = stage_kind_id.lower()
    if kid in _BUILTIN_DOMAIN_STAGE_KINDS:
        return _BUILTIN_DOMAIN_STAGE_KINDS[kid]

    # Fallback for anything with blueprint in the id that isn't in the
    # explicit map (robustness for any future blueprint-like stage kinds).
    if "blueprint" in kid:
        return ExtensionDomain.BLUEPRINT

    return None


def _derive_terminal_action_domain(
    terminal_action_id: str,
) -> ExtensionDomain | None:
    """Return the extension domain a terminal action belongs to, or None.

    Only well-known domain-specific terminal actions are mapped.
    Generic terminal actions (complete_work_item, block_work_item, etc.)
    are NOT assigned to a domain-specific extension.
    """
    tid = terminal_action_id.lower()
    if tid in _BUILTIN_DOMAIN_TERMINAL_ACTIONS:
        return _BUILTIN_DOMAIN_TERMINAL_ACTIONS[tid]
    return None


def _derive_graph_domains(
    graph_loop: GraphLoopDefinition,
    stage_kinds: dict[str, RegisteredStageKindDefinition],
) -> set[ExtensionDomain]:
    """Return the set of extension domains referenced by a single graph loop."""
    domains: set[ExtensionDomain] = set()
    for node in graph_loop.nodes:
        sk = stage_kinds.get(node.stage_kind_id)
        domain = _derive_stage_kind_domain(node.stage_kind_id, sk)
        if domain is not None:
            domains.add(domain)
    # Also check terminal action domains
    for ts in graph_loop.terminal_states:
        domain = _derive_terminal_action_domain(ts.terminal_action_id)
        if domain is not None:
            domains.add(domain)
    return domains


def _collect_used_domains(
    graph_loops: dict[Plane, GraphLoopDefinition],
    stage_kinds: dict[str, RegisteredStageKindDefinition],
) -> set[ExtensionDomain]:
    """Return the set of extension domains referenced across all graph loops."""
    used: set[ExtensionDomain] = set()
    for graph_loop in graph_loops.values():
        used.update(_derive_graph_domains(graph_loop, stage_kinds))
    return used


def _declared_extension_domains(
    manifests_by_id: dict[str, ExtensionPackageManifest],
    declared_ids: set[str],
) -> set[ExtensionDomain]:
    """Return the set of ExtensionDomains covered by declared required extensions."""
    domains: set[ExtensionDomain] = set()
    for ext_id in declared_ids:
        manifest = manifests_by_id.get(ext_id)
        if manifest is not None:
            domains.add(manifest.domain)
    return domains


def _parse_version_safe(version_str: str) -> Version | None:
    """Parse a semver string safely, returning None on failure."""
    try:
        return Version(version_str)
    except InvalidVersion:
        return None


# Reverse index for canonical domain ownership validation.
_PACKAGE_ID_TO_CANONICAL_DOMAIN: dict[str, ExtensionDomain] = {
    pkg_id: domain for domain, pkg_id in _BUILTIN_DOMAIN_PACKAGE_IDS.items()
}


def validate_required_extensions(
    *,
    mode: ModeDefinition,
    discovered_manifests: tuple[ExtensionPackageManifest, ...],
    graph_loops: dict[Plane, GraphLoopDefinition] | None = None,
    stage_kinds: dict[str, RegisteredStageKindDefinition] | None = None,
) -> None:
    """Validate that every required extension declared by a mode is available.

    Rejects with clear CompilerValidationError messages when:
    - A required extension package_id is not found among discovered manifests
    - A required extension declares a min_version that the discovered manifest
      does not satisfy
    - A discovered extension manifest declares a domain that conflicts with
      the canonical domain mapping (source-of-truth ownership check)
    - A graph loop references stage-kind or terminal-action vocabulary owned
      by an extension domain that the mode does not declare as required

    When graph_loops and stage_kinds are provided, also checks that all
    extension-domain vocabulary used by the compiled plan is covered by
    the mode's required_extensions declarations.
    """
    required_extensions_raw = getattr(mode, "required_extensions", None) or ()

    manifests_by_id: dict[str, ExtensionPackageManifest] = {
        manifest.package_id: manifest for manifest in discovered_manifests
    }

    # --- Canonical domain ownership cross-validation ---
    # Each discovered extension manifest must declare a domain that matches
    # the canonical source-of-truth mapping.  This catches conflicting
    # owners when per-manifest metadata disagrees with the central mapping.
    for manifest in discovered_manifests:
        canonical_domain = _PACKAGE_ID_TO_CANONICAL_DOMAIN.get(manifest.package_id)
        if canonical_domain is not None and manifest.domain != canonical_domain:
            raise CompilerValidationError(
                f"Extension manifest {manifest.package_id!r} declares domain "
                f"{manifest.domain.value!r} but canonical source of truth expects "
                f"{canonical_domain.value!r}.  Fix the manifest domain to match "
                f"the canonical mapping."
            )

    declared_extension_ids: set[str] = set()

    for raw_entry in required_extensions_raw:
        if isinstance(raw_entry, dict):
            try:
                declaration = RequiredExtensionDeclaration.model_validate(raw_entry)
            except Exception as exc:
                raise CompilerValidationError(
                    f"Invalid required-extension declaration in mode {mode.mode_id!r}: {exc}"
                ) from exc
        elif isinstance(raw_entry, RequiredExtensionDeclaration):
            declaration = raw_entry
        else:
            raise CompilerValidationError(
                f"Invalid required-extension declaration type in mode {mode.mode_id!r}: "
                f"expected dict or RequiredExtensionDeclaration, got {type(raw_entry).__name__}"
            )

        extension_id = declaration.extension_package_id
        declared_extension_ids.add(extension_id)
        manifest = manifests_by_id.get(extension_id)

        if manifest is None:
            available = sorted(manifests_by_id) if manifests_by_id else []
            available_msg = (
                f" Available: {', '.join(available)}"
                if available
                else " No extension manifests discovered."
            )
            raise CompilerValidationError(
                f"Mode {mode.mode_id!r} requires extension package {extension_id!r} "
                f"which was not found among discovered manifests.{available_msg}"
            )

        if declaration.min_version is not None:
            declared_version = _parse_version_safe(manifest.version)
            min_version = _parse_version_safe(declaration.min_version)

            if declared_version is not None and min_version is not None:
                if declared_version < min_version:
                    raise CompilerValidationError(
                        f"Mode {mode.mode_id!r} requires extension package "
                        f"{extension_id!r} >= {declaration.min_version}, "
                        f"but discovered manifest has version {manifest.version}"
                    )

    # --- Undeclared domain vocabulary check ---
    if graph_loops is not None and stage_kinds is not None:
        used_domains = _collect_used_domains(graph_loops, stage_kinds)
        declared_domains = _declared_extension_domains(
            manifests_by_id, declared_extension_ids
        )

        # generic domain is always considered covered (it is always needed).
        used_domains.discard(ExtensionDomain.GENERIC)

        undeclared = used_domains - declared_domains
        if undeclared:
            undeclared_package_ids = sorted(
                _BUILTIN_DOMAIN_PACKAGE_IDS[domain]
                for domain in undeclared
                if domain in _BUILTIN_DOMAIN_PACKAGE_IDS
            )
            missing_msg = ", ".join(undeclared_package_ids)
            raise CompilerValidationError(
                f"Mode {mode.mode_id!r} references extension-owned vocabulary "
                f"(stage kinds from domains: {', '.join(sorted(d.value for d in undeclared))}) "
                f"without declaring the required extension(s): {missing_msg}. "
                f"Add the missing package ids to required_extensions."
            )


__all__ = [
    "validate_required_extensions",
]
