"""Kernel import guardrail tests.

Fail if active kernel code imports Recon, Blueprint, closure, Learning,
Planner disposition, or other extension-domain modules directly instead
of using the declared extension boundary interfaces.

Context: ADR-0012 defines the kernel boundary.  Kernel code
(runtime/``, ``workspace/``, ``compilation/``) must not own workflow
semantics.  Domain-specific behavior should be reached through the
BuiltInExtensionBoundaryRegistry or extension-owned interfaces defined
in ``src/millrace_ai/extensions/interfaces.py``.

The existing compatibility facades documented in ADR-0016 are allowed
— kernel modules that *already* import domain code and are recorded as
active bridges.  This test ensures the set of direct kernel-to-domain
imports does not grow.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "millrace_ai"

# ---------------------------------------------------------------------------
# Extension-domain module paths that kernel code must not import directly.
# These are resolved through BuiltInExtensionBoundaryRegistry instead.
# ---------------------------------------------------------------------------

DOMAIN_MODULES: dict[str, frozenset[str]] = {
    "recon": frozenset({
        "millrace_ai.runtime.recon_transitions",
        "millrace_ai.recon_packets",
    }),
    "closure": frozenset({
        "millrace_ai.runtime.closure_transitions",
        "millrace_ai.workspace.arbiter_state",
        "millrace_ai.runtime.completion_behavior",
    }),
    "authority": frozenset({
        "millrace_ai.runtime.graph_authority.validation",
    }),
    "blueprint": frozenset({
        "millrace_ai.contracts.blueprint",
        "millrace_ai.workspace.blueprint_state",
        "millrace_ai.runtime.context.blueprint",
        "millrace_ai.runtime.effects.operation_runners",
    }),
    "learning": frozenset({
        "millrace_ai.runtime.learning_triggers",
        "millrace_ai.runtime.learning_promotions",
    }),
    "planner": frozenset({
        "millrace_ai.runtime.planner_effects",
    }),
}

# ---------------------------------------------------------------------------
# Known compatibility facades — modules that already import domain code
# and are documented in ADR-0016 (extension-boundary-compatibility-facades.md).
# These imports are allowed to prevent the guardrail from breaking on
# existing, documented bridges.
# ---------------------------------------------------------------------------

# Files that are allowed to import specific domain modules.
# Keys: kernel file (relative to SRC_ROOT).  Values: allowed domain import targets.
ALLOWED_DIRECT_IMPORTS: dict[str, frozenset[str]] = {
    # Recon domain — result_application.py bridges through
    # BuiltInExtensionBoundaryRegistry but also imports completion_behavior
    # which is a separate compatibility facade.
    "runtime/recon_transitions.py": frozenset({
        "millrace_ai.recon_packets",
    }),
    "runtime/artifact_contracts.py": frozenset({
        "millrace_ai.recon_packets",
    }),
    "runtime/error_recovery.py": frozenset({
        "millrace_ai.runtime.recon_transitions",
    }),
    # Closure domain
    "runtime/closure_transitions.py": frozenset({
        "millrace_ai.workspace.arbiter_state",
    }),
    # Blueprint domain — effect operation runners implement Blueprint behavior
    # for the legacy handler-backed path (ADR-0016).
    "runtime/effects/operation_runners/candidate_evaluation.py": frozenset({
        "millrace_ai.contracts.blueprint",
        "millrace_ai.workspace.blueprint_state",
    }),
    "runtime/effects/operation_runners/decomposition_manifest.py": frozenset({
        "millrace_ai.contracts.blueprint",
        "millrace_ai.workspace.blueprint_state",
    }),
    "runtime/effects/operation_runners/candidate_packet.py": frozenset({
        "millrace_ai.contracts.blueprint",
        "millrace_ai.workspace.blueprint_state",
    }),
    "runtime/effects/operation_runners/mechanic_repair.py": frozenset({
        "millrace_ai.contracts.blueprint",
        "millrace_ai.workspace.blueprint_state",
    }),
    "runtime/effects/operation_runners/repair_application.py": frozenset({
        "millrace_ai.contracts.blueprint",
        "millrace_ai.workspace.blueprint_state",
    }),
    "runtime/effects/operation_runners/manager_manifest.py": frozenset({
        "millrace_ai.contracts.blueprint",
    }),
    # Learning domain
    "runtime/learning_triggers.py": frozenset({
        "millrace_ai.contracts",  # imports LearningRequestDocument
    }),
    "runtime/learning_promotions.py": frozenset({
        # learning_promotions is the domain module itself
    }),
    # Planner effects — legacy handler registration imports planner_effects
    "runtime/effects/legacy.py": frozenset({
        "millrace_ai.runtime.planner_effects",
    }),
    # Closure boundary — named boundary that delegates to completion_behavior
    "runtime/closure_boundary.py": frozenset({
        "millrace_ai.runtime.completion_behavior",
    }),
    # Completion behavior — internal implementation behind closure_boundary (ADR-0016)
    "runtime/completion_behavior.py": frozenset({
        "millrace_ai.workspace.work_inventory",
        "millrace_ai.workspace.arbiter_state",
        "millrace_ai.workspace.blueprint_state",
    }),
    # Blueprint context provider — documented compatibility facade in ADR-0016
    "runtime/context/blueprint.py": frozenset({
        "millrace_ai.workspace.blueprint_state",
        "millrace_ai.contracts.blueprint",
    }),
    # State reconciliation — lazy-imports Blueprint diagnostics from
    # blueprint_state.py (ADR-0016, C4 remediation)
    "workspace/state_reconciliation.py": frozenset({
        "millrace_ai.workspace.blueprint_state",
    }),
    # Result application — bridges through extension boundary; closure lineage
    # check routes through closure_boundary (the named boundary).
    "runtime/result_application.py": frozenset({
        "millrace_ai.runtime.closure_boundary",
    }),
    "runtime/effect_execution.py": frozenset({
        "millrace_ai.runtime.closure_boundary",
    }),
    # Engine imports closure_boundary as a lazy-loading namespace
    "runtime/engine.py": frozenset({
        "millrace_ai.runtime.closure_boundary",
    }),
    # Activation routes claim backpressure through closure_boundary
    "runtime/activation.py": frozenset({
        "millrace_ai.runtime.closure_boundary",
    }),
    # Extensions package — the boundary registry itself
    "extensions/builtin/recon_transition_handler.py": frozenset({
        "millrace_ai.runtime.recon_transitions",
    }),
    "extensions/builtin/closure_transition_handler.py": frozenset({
        "millrace_ai.runtime.closure_transitions",
    }),
    "extensions/builtin/learning_trigger_handler.py": frozenset({
        "millrace_ai.runtime.learning_triggers",
    }),
    "extensions/builtin/learning_promotion_handler.py": frozenset({
        "millrace_ai.runtime.learning_promotions",
    }),
    "extensions/builtin/blueprint_validator.py": frozenset({
        "millrace_ai.contracts.blueprint",
        "millrace_ai.workspace.blueprint_state",
    }),
    "extensions/builtin/blueprint_context_provider.py": frozenset({
        "millrace_ai.runtime.context.blueprint",
        "millrace_ai.contracts.blueprint",
    }),
    # Compilation imports
    "compilation/learning_triggers.py": frozenset({
        "millrace_ai.contracts",  # imports LearningStageName
    }),
    "runtime/recovery/repair_routes.py": frozenset({
        "millrace_ai.compilation.validation.repair_closures",
    }),
}

# Kernel source directories to scan
KERNEL_ROOTS = (
    SRC_ROOT / "runtime",
    SRC_ROOT / "workspace",
    SRC_ROOT / "compilation",
)


def _python_files(*roots: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for root in roots
            for path in root.rglob("*.py")
            if "__pycache__" not in path.parts
        )
    )


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _relative(path: Path) -> str:
    return path.relative_to(SRC_ROOT).as_posix()


def _imported_module_name(node: ast.Import | ast.ImportFrom) -> str | None:
    """Return the full imported module name from an import statement."""
    if isinstance(node, ast.Import):
        if node.names:
            return node.names[0].name
        return None
    if isinstance(node, ast.ImportFrom):
        if node.module is None:
            return None
        return node.module
    return None


def test_kernel_code_does_not_add_new_direct_domain_imports() -> None:
    """Guardrail: kernel code must not import domain modules directly.

    Scans runtime/, workspace/, and compilation/ for imports of
    Recon, Blueprint, closure, Learning, and Planner domain modules.

    Known compatibility facades are allowed (ALLOWED_DIRECT_IMPORTS).
    Any new direct import violates the kernel boundary (ADR-0012).
    """
    # Build the reverse index: for each domain module, which kernel files
    # are allowed to import it.
    allowed: dict[str, set[str]] = {}
    for file_rel, allowed_targets in ALLOWED_DIRECT_IMPORTS.items():
        for target in allowed_targets:
            if target not in allowed:
                allowed[target] = set()
            allowed[target].add(file_rel)

    violations: list[str] = []

    for path in _python_files(*KERNEL_ROOTS):
        rel = _relative(path)
        tree = _tree(path)

        for node in ast.walk(tree):
            module_name = None
            if isinstance(node, ast.Import):
                module_name = _imported_module_name(node)
            elif isinstance(node, ast.ImportFrom):
                module_name = _imported_module_name(node)

            if module_name is None:
                continue

            # Check if this module name matches or is a subpath of any
            # domain module
            matched_domain = None
            for domain, targets in DOMAIN_MODULES.items():
                for target in targets:
                    if module_name == target or module_name.startswith(target + "."):
                        matched_domain = domain
                        # Use the parent target for allowlist lookup
                        domain_target = target
                        break
                if matched_domain:
                    break

            if matched_domain is None:
                continue

            # Check if this file is in the allowlist for this target
            if rel in allowed.get(domain_target, set()):
                continue

            # Also check sub-targets — e.g. if the file is allowed to import
            # the parent target, importing a sub-target is also allowed
            sub_allowed = False
            for check_target, files in allowed.items():
                if domain_target.startswith(check_target + ".") or check_target.startswith(
                    domain_target + "."
                ):
                    if rel in files:
                        sub_allowed = True
                        break
            if sub_allowed:
                # More conservative: only allow if the file is explicitly in
                # the parent allowlist
                if rel in allowed.get(domain_target.rsplit(".", 1)[0] if "." in domain_target else domain_target, set()):
                    continue

            violations.append(
                f"{rel} line {node.lineno}: imports domain module "
                f"{module_name!r} ({matched_domain} domain)"
            )

    # Deduplicate
    violations = sorted(set(violations))

    assert violations == [], (
        f"{len(violations)} kernel import guardrail violation(s):\n"
        + "\n".join(violations)
        + "\n\nKernel code must not import domain modules directly. "
        "Use the BuiltInExtensionBoundaryRegistry or extension-owned interfaces."
    )


# ---------------------------------------------------------------------------
# Forbidden domain string literal guardrails
# ---------------------------------------------------------------------------

# Domain names that must not appear as string literals in kernel control flow.
# These are the domain labels that, when used in comparisons or branch dispatch,
# indicate domain-specific routing leaking into generic kernel code.
FORBIDDEN_DOMAIN_STRINGS = frozenset({
    "recon",
    "closure",
    "blueprint",
    "learning",
})

# Kernel files where domain string literals are explicitly allowed in
# control-flow contexts because they are documented compatibility bridges
# or necessary path/name inference (not domain-specific dispatch).
DOMAIN_STRING_CONTROL_FLOW_ALLOWED: dict[str, frozenset[int]] = {
    # run_traces.py infers work-item kind from directory path components
    # (e.g. "learning" in parts → learning_request).  This is path-based
    # identity resolution, not domain-specific dispatch.
    "runtime/run_traces.py": frozenset({530}),
    # snapshot_state.py constructs default plane-keyed status dicts with
    # literal plane-name keys.  These are data initialization, not control
    # flow dispatch on domain identity.
    "runtime/snapshot_state.py": frozenset({49}),
    # context/generic.py registers context providers with domain-name keys
    # in a provider registration tuple.  This is a data structure, not
    # control flow.
    "runtime/context/generic.py": frozenset({97}),
    # recovery/queue_mutation.py constructs a versioned adapter key from
    # string components for document adapter lookup.  This is identity
    # key construction, not domain dispatch.
    "runtime/recovery/queue_mutation.py": frozenset({131}),
    # compilation/validation/extensions.py maps stage kind name substrings
    # to extension domains during compile-time validation.  This is a
    # static domain-derivation heuristic, not runtime control flow.
    "compilation/validation/extensions.py": frozenset({87}),
}


def test_kernel_control_flow_has_no_forbidden_domain_string_literals() -> None:
    """Guardrail: kernel code must not use forbidden domain string literals
    ("recon", "closure", "blueprint", "learning") in control-flow comparisons.

    Scans runtime/, workspace/, and compilation/ for Compare nodes and
    If/IfExp test conditions where one operand is a string constant matching
    a forbidden domain name.

    Docstrings, comments, log messages, import paths, and module-name
    references are not flagged — only comparisons that branch on domain
    identity.
    """
    violations: list[str] = []

    for path in _python_files(*KERNEL_ROOTS):
        rel = _relative(path)
        allowed_lines = DOMAIN_STRING_CONTROL_FLOW_ALLOWED.get(rel, frozenset())
        tree = _tree(path)

        for node in ast.walk(tree):
            # Scan Compare nodes for forbidden domain string comparisons
            if isinstance(node, ast.Compare):
                for op_node, comparator in zip(node.ops, node.comparators):
                    is_eq = isinstance(op_node, (ast.Eq, ast.Is, ast.NotEq, ast.IsNot))
                    is_membership = isinstance(op_node, (ast.In, ast.NotIn))
                    const_val = _str_constant(comparator)
                    if const_val and const_val in FORBIDDEN_DOMAIN_STRINGS:
                        if is_eq and node.lineno not in allowed_lines:
                            violations.append(
                                f"{rel} line {node.lineno}: compares against "
                                f"domain string literal {const_val!r}"
                            )
                        elif is_membership and node.lineno not in allowed_lines:
                            violations.append(
                                f"{rel} line {node.lineno}: membership test "
                                f"on domain string literal {const_val!r}"
                            )
                # Also check the left side
                left_val = _str_constant(node.left)
                if left_val and left_val in FORBIDDEN_DOMAIN_STRINGS:
                    if node.lineno not in allowed_lines:
                        violations.append(
                            f"{rel} line {node.lineno}: left operand is "
                            f"domain string literal {left_val!r}"
                        )

            # Scan Match/As patterns for domain string literals
            if isinstance(node, ast.Match):
                subject_val = _str_constant(node.subject)
                if subject_val and subject_val in FORBIDDEN_DOMAIN_STRINGS:
                    if node.lineno not in allowed_lines:
                        violations.append(
                            f"{rel} line {node.lineno}: match subject is "
                            f"domain string literal {subject_val!r}"
                        )

            # Scan MatchValue patterns in case clauses
            if isinstance(node, ast.MatchValue):
                pat_val = _str_constant(node.value)
                if pat_val and pat_val in FORBIDDEN_DOMAIN_STRINGS:
                    if node.lineno not in allowed_lines:
                        violations.append(
                            f"{rel} line {node.lineno}: match case value is "
                            f"domain string literal {pat_val!r}"
                        )

    # Deduplicate
    violations = sorted(set(violations))

    assert violations == [], (
        f"{len(violations)} forbidden domain string literal violation(s) "
        f"in kernel control flow:\n" + "\n".join(violations)
        + "\n\nKernel code must not branch on domain identity strings. "
        "Use compiled metadata (plane, stage kind, extension domain enum) "
        "instead of hard-coded domain string comparisons."
    )


# ---------------------------------------------------------------------------
# Extension code public interface import guardrails
# ---------------------------------------------------------------------------

# Public runtime service interfaces that extensions are allowed to import.
# These are the stable, documented API surfaces that extension code should
# use to interact with the runtime kernel.
PUBLIC_RUNTIME_INTERFACES = frozenset({
    "millrace_ai.contracts",
    "millrace_ai.architecture",
    "millrace_ai.router",
    "millrace_ai.runners",
    "millrace_ai.runtime.engine",
    "millrace_ai.runtime.context",
    "millrace_ai.runtime.context.providers",
    "millrace_ai.runtime.context.generic",
    "millrace_ai.runtime.request_context",
    "millrace_ai.runtime.effects",
    "millrace_ai.runtime.effects.primitives",
    "millrace_ai.runtime.effects.journal",
    "millrace_ai.runtime.effects.interpreter",
    "millrace_ai.runtime.effects.registry",
    "millrace_ai.runtime.compiled_plans",
    "millrace_ai.extensions",
    "millrace_ai.extensions.interfaces",
    "millrace_ai.extensions.boundaries",
    "millrace_ai.runtime.recon_transitions",
    "millrace_ai.runtime.closure_transitions",
    "millrace_ai.runtime.learning_triggers",
    "millrace_ai.runtime.learning_promotions",
    "millrace_ai.runtime.context.blueprint",
    "millrace_ai.runtime.planner_effects",
    "millrace_ai.recon_packets",
    "millrace_ai.workspace.arbiter_state",
    "millrace_ai.workspace.blueprint_state",
    "millrace_ai.contracts.blueprint",
})

# Kernel-internal modules that extension code must NOT import directly.
# These are implementation-detail modules that extension code should reach
# through public interfaces rather than importing their internals.
KERNEL_INTERNALS = frozenset({
    "millrace_ai.runtime.activation",
    "millrace_ai.runtime.result_application",
    "millrace_ai.runtime.supervisor",
    "millrace_ai.runtime.lifecycle",
    "millrace_ai.runtime.lifecycle_interpreter",
    "millrace_ai.runtime.result_counters",
    "millrace_ai.runtime.stage_requests",
    "millrace_ai.runtime.stage_result_persistence",
    "millrace_ai.runtime.snapshot_state",
    "millrace_ai.runtime.work_item_transitions",
    "millrace_ai.runtime.run_traces",
    "millrace_ai.runtime.tick_cycle",
    "millrace_ai.runtime.scheduler_policy",
    "millrace_ai.runtime.completion_behavior",
    "millrace_ai.runtime.graph_authority.routing",
    "millrace_ai.runtime.graph_authority.generic_router",
    "millrace_ai.runtime.graph_authority.terminal_actions",
    "millrace_ai.runtime.graph_authority.counters",
    "millrace_ai.runtime.recovery",
    "millrace_ai.runtime.recovery.queue_mutation",
    "millrace_ai.runtime.recovery.repair_routes",
    "millrace_ai.runtime.capability_gates",
    "millrace_ai.runtime.approvals",
    "millrace_ai.runtime.effect_execution",
    "millrace_ai.runtime.effects.operation_runners",
    "millrace_ai.runtime.effects.legacy",
    "millrace_ai.workspace.queue_store",
    "millrace_ai.workspace.queue_selection",
    "millrace_ai.workspace.queue_lifecycle",
    "millrace_ai.workspace.queue_family_interpreter",
    "millrace_ai.workspace.work_inventory",
    "millrace_ai.workspace.work_item_adapters",
    "millrace_ai.workspace.work_documents",
    "millrace_ai.workspace.operator_interventions",
    "millrace_ai.workspace.lineage_integrity",
    "millrace_ai.workspace.bootstrap_files",
    "millrace_ai.workspace.schema_epoch",
    "millrace_ai.cli",
    "millrace_ai.compiler",
    "millrace_ai.compilation",
    "millrace_ai.control",
    "millrace_ai.config",
})

# Extension files that are allowed to import kernel internals because
# they are documented compatibility bridges (ADR-0016).
EXTENSION_KERNEL_ALLOWLIST: dict[str, frozenset[str]] = {
    # recon_transition_handler bridges to runtime/recon_transitions.py
    "extensions/builtin/recon_transition_handler.py": frozenset({
        "millrace_ai.runtime.recon_transitions",
    }),
    # closure_transition_handler bridges to runtime/closure_transitions.py
    "extensions/builtin/closure_transition_handler.py": frozenset({
        "millrace_ai.runtime.closure_transitions",
    }),
    # learning_trigger_handler bridges to runtime/learning_triggers.py
    "extensions/builtin/learning_trigger_handler.py": frozenset({
        "millrace_ai.runtime.learning_triggers",
    }),
    # learning_promotion_handler bridges to runtime/learning_promotions.py
    "extensions/builtin/learning_promotion_handler.py": frozenset({
        "millrace_ai.runtime.learning_promotions",
    }),
    # blueprint_validator bridges to Blueprint contracts and state
    "extensions/builtin/blueprint_validator.py": frozenset({
        "millrace_ai.contracts.blueprint",
        "millrace_ai.workspace.blueprint_state",
    }),
    # blueprint_context_provider bridges to runtime/context/blueprint.py
    "extensions/builtin/blueprint_context_provider.py": frozenset({
        "millrace_ai.runtime.context.blueprint",
        "millrace_ai.runtime.context.providers",
    }),
}


def test_extension_code_imports_public_interfaces_not_kernel_internals() -> None:
    """Guardrail: extension code must import public runtime service interfaces
    rather than kernel internals.

    Scans src/millrace_ai/extensions/ for imports of kernel-internal modules
    that are not in the documented public interface surface or the
    compatibility-bridge allowlist.

    Extension code should use:
    - millrace_ai.runtime.engine (RuntimeEngine) for runtime mutation
    - millrace_ai.contracts for data models
    - millrace_ai.architecture for compiled plan types
    - millrace_ai.runners for request types
    - millrace_ai.extensions.interfaces for boundary Protocols

    It must not reach into kernel implementation details like activation,
    result_application, supervisor, queue_store internals, etc.
    """
    extensions_root = SRC_ROOT / "extensions"
    if not extensions_root.is_dir():
        return

    violations: list[str] = []

    for path in sorted(extensions_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = _relative(path)
        tree = _tree(path)

        allowed_for_file = EXTENSION_KERNEL_ALLOWLIST.get(rel, frozenset())

        for node in ast.walk(tree):
            module_name = None
            if isinstance(node, ast.Import):
                module_name = _imported_module_name(node)
            elif isinstance(node, ast.ImportFrom):
                module_name = _imported_module_name(node)

            if module_name is None:
                continue

            # Check if this module is a kernel internal
            if module_name not in KERNEL_INTERNALS:
                # Also check parent modules
                matched = False
                for internal in KERNEL_INTERNALS:
                    if module_name == internal or module_name.startswith(internal + "."):
                        matched = True
                        break
                if not matched:
                    continue

            # Check if the module is in the public interface set
            if module_name in PUBLIC_RUNTIME_INTERFACES:
                continue
            # Check parent module match against public interfaces
            public_match = False
            for pub in PUBLIC_RUNTIME_INTERFACES:
                if module_name == pub or module_name.startswith(pub + "."):
                    public_match = True
                    break
            if public_match:
                continue

            # Check if the file is in the allowlist for this import
            if module_name in allowed_for_file:
                continue
            # Check parent match in allowlist
            allowlist_match = False
            for allowed_mod in allowed_for_file:
                if module_name == allowed_mod or module_name.startswith(allowed_mod + "."):
                    allowlist_match = True
                    break
            if allowlist_match:
                continue

            violations.append(
                f"{rel} line {node.lineno}: imports kernel internal "
                f"{module_name!r}"
            )

    violations = sorted(set(violations))

    assert violations == [], (
        f"{len(violations)} extension kernel-internal import violation(s):\n"
        + "\n".join(violations)
        + "\n\nExtension code must import public runtime service interfaces, "
        "not kernel internals. Use RuntimeEngine, contracts, architecture, "
        "and runners top-level types instead of importing activation, "
        "result_application, queue_store internals, etc."
    )


def _str_constant(node: ast.expr) -> str | None:
    """Return the string value if the node is a string Constant."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def test_extensions_package_imports_are_not_treated_as_kernel_violations() -> None:
    """Positive proof: the extensions/ package is not scanned by kernel guardrails.

    The BuiltInExtensionBoundaryRegistry in extensions/boundaries.py and
    the built-in adapters in extensions/builtin/ are the *intended* bridge
    surface between kernel and domain code.  They must be allowed to import
    domain modules.
    """
    extensions_root = SRC_ROOT / "extensions"
    if not extensions_root.is_dir():
        return  # no extensions package

    # The kernel scan should not flag extensions/ files
    kernel_files = _python_files(*KERNEL_ROOTS)
    kernel_rels = {_relative(p) for p in kernel_files}

    for path in sorted(extensions_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel_path = _relative(path)
        # extensions/ files are not kernel files — the guardrail only
        # scans KERNEL_ROOTS, so this assertion is about test correctness,
        # not behavior
        assert rel_path not in kernel_rels, (
            f"extensions/ file {rel_path} appears in kernel scan; "
            f"this would violate the guardrail exclusion"
        )
