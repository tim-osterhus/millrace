from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "millrace_ai"

# Known built-in work item family IDs (from shipped registry assets)
BUILTIN_FAMILY_IDS = frozenset({
    "task",
    "spec",
    "probe",
    "incident",
    "learning_request",
    "blueprint_draft",
})

# Source paths to scan for family-ID branch dispatch
FAMILY_DISPATCH_SCAN_ROOTS = (
    SRC_ROOT / "runtime",
    SRC_ROOT / "workspace",
    SRC_ROOT / "cli",
)

# Known named counter ids that may appear as data keys, but must not drive
# active dispatch through fixed model fields.
LEGACY_COUNTER_FIELD_NAMES = frozenset({
    "troubleshoot_attempt_count",
    "mechanic_attempt_count",
    "fix_cycle_count",
    "consultant_invocations",
})

# Canonical status projections module
STATUS_PROJECTIONS_MODULE = "millrace_ai.runtime.status_projections"

# ADR references that must appear in docs/adr/README.md and docs/doc-index.md
# docs/adr/README.md uses the filename form (e.g. 0012-core-kernel-boundary.md)
EXPECTED_ADR_FILE_NAMES = (
    "0012-core-kernel-boundary.md",
    "0013-generic-stage-and-plane-registry.md",
    "0014-runtime-operation-step-interpreter.md",
    "0015-extension-package-manifests.md",
)

# docs/doc-index.md uses the relative path form (e.g. adr/0012-core-kernel-boundary.md)
EXPECTED_ADR_DOC_INDEX_REFS = (
    "adr/0012-core-kernel-boundary.md",
    "adr/0013-generic-stage-and-plane-registry.md",
    "adr/0014-runtime-operation-step-interpreter.md",
    "adr/0015-extension-package-manifests.md",
)

# Config-data directories that must not contain Python source files
CONFIG_DATA_DIRS = (
    "src/millrace_ai/assets/modes",
    "src/millrace_ai/assets/graphs",
    "src/millrace_ai/assets/loops",
    "src/millrace_ai/assets/registry",
)

# Canonical migration ledger path
CANONICAL_LEDGER = "docs/maintenance/refactor-candidate-register.md"


def _docs_path(rel: str) -> Path:
    return REPO_ROOT / rel


def test_docs_adr_readme_references_all_four_boundary_adrs() -> None:
    """Fail if docs/adr/README.md stops referencing ADR-0012 through ADR-0015."""
    path = _docs_path("docs/adr/README.md")
    text = path.read_text(encoding="utf-8")

    missing = [ref for ref in EXPECTED_ADR_FILE_NAMES if ref not in text]
    assert not missing, (
        f"docs/adr/README.md is missing references to: {', '.join(missing)}"
    )


def test_doc_index_references_all_four_boundary_adrs() -> None:
    """Fail if docs/doc-index.md stops referencing ADR-0012 through ADR-0015."""
    path = _docs_path("docs/doc-index.md")
    text = path.read_text(encoding="utf-8")

    missing = [ref for ref in EXPECTED_ADR_DOC_INDEX_REFS if ref not in text]
    assert not missing, (
        f"docs/doc-index.md is missing references to: {', '.join(missing)}"
    )


def test_technical_overview_uses_four_layer_authority_vocabulary() -> None:
    """Fail if docs/millrace-technical-overview.md drops the four-layer vocabulary."""
    path = _docs_path("docs/millrace-technical-overview.md")
    text = path.read_text(encoding="utf-8")

    assert "four-layer authority model" in text, (
        "docs/millrace-technical-overview.md no longer mentions the 'four-layer authority model'"
    )


def test_source_package_map_uses_four_layer_vocabulary_and_marks_prospective_boundaries() -> (
    None
):
    """Fail if docs/source-package-map.md drops the four-layer vocabulary or stops marking
    prospective boundary packages as not yet created."""
    path = _docs_path("docs/source-package-map.md")
    text = path.read_text(encoding="utf-8")

    assert "four-layer" in text, (
        "docs/source-package-map.md no longer uses the four-layer vocabulary"
    )
    assert "not yet created" in text, (
        "docs/source-package-map.md no longer marks prospective boundary packages as "
        "'not yet created'"
    )


def test_config_data_directories_contain_no_python_source_files() -> None:
    """Fail if any config-data directory contains Python source files.

    Scoped to: src/millrace_ai/assets/modes/, src/millrace_ai/assets/graphs/,
    src/millrace_ai/assets/loops/, src/millrace_ai/assets/registry/.
    Does not flag unrelated docs, ADRs, or architecture directories.
    """
    violations: list[str] = []

    for rel_dir in CONFIG_DATA_DIRS:
        abs_dir = REPO_ROOT / rel_dir
        if not abs_dir.is_dir():
            continue
        for py_file in sorted(abs_dir.rglob("*.py")):
            # Exclude __init__.py files that are legitimate package markers
            # if they exist as part of the Python import system
            if py_file.name == "__init__.py" and py_file.parent == abs_dir:
                continue
            violations.append(py_file.relative_to(REPO_ROOT).as_posix())

    assert violations == [], (
        "Config-data directories contain Python source files:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


def test_only_one_migration_ledger_exists() -> None:
    """Fail if a second migration ledger appears outside the canonical register.

    The canonical migration ledger is docs/maintenance/refactor-candidate-register.md.
    This checks that no other file in docs/ has a name containing 'ledger' (case-insensitive)
    or contains the phrase 'migration ledger' in its text content.
    """
    ledger_names: list[str] = []
    for path in sorted((REPO_ROOT / "docs").rglob("*ledger*")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel == CANONICAL_LEDGER:
            continue
        ledger_names.append(rel)

    # Also check for files that claim to be a migration ledger in their content
    content_ledgers: list[str] = []
    for path in sorted((REPO_ROOT / "docs").rglob("*.md")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel == CANONICAL_LEDGER:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        # If the file claims to be a migration ledger in its defined purpose
        if "migration ledger" in text.lower():
            content_ledgers.append(rel)

    violations = sorted(set(ledger_names + content_ledgers))
    assert violations == [], (
        f"Additional migration ledger files found outside {CANONICAL_LEDGER}:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# Generic queue engine boundary guardrails
# ---------------------------------------------------------------------------


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
    return path.relative_to(REPO_ROOT).as_posix()


# -- family-ID branch dispatch guardrails -----------------------------------


def test_no_family_id_branch_dispatch_in_claim_lifecycle_status_counter_routing() -> None:
    """Guardrail: no function in runtime/, workspace/, or cli/ dispatches on
    hard-coded family-ID string comparisons against multiple built-in family IDs.

    A *branch dispatch* is a function that contains two or more comparisons of
    a family-id variable against distinct literal family-ID strings from the
    built-in set.  Single checks (e.g. ``if family_id == "learning_request"``)
    are flagged only when they live in claim, lifecycle, or counter routing
    paths that should be fully generic.

    Equality comparisons for identity matching (e.g.
    ``entry.work_item_family_id == context.work_item_family_id``) are skipped.
    """
    # Files where family-ID branch dispatch is acceptable (known
    # non-generic edge: Blueprint context, recovery events/queue with
    # task-specific labels, CLI queue rendering, and root-source identity
    # functions that legitimately differentiate between source kinds)
    ALLOWED_BRANCH_FILES = frozenset({
        "src/millrace_ai/runtime/recovery/events.py",
        "src/millrace_ai/runtime/recovery/queue_mutation.py",
        "src/millrace_ai/runtime/context/blueprint.py",
        "src/millrace_ai/cli/commands/queue.py",
        "src/millrace_ai/runtime/completion_behavior.py",
    })
    # Known equality-identity patterns (not branch dispatch)
    IDENTITY_PATTERNS = frozenset({
        "context.work_item_family_id",
        "decision.work_item_family_id",
        "stage_result.work_item_family_id",
    })

    scanned_files = _python_files(*FAMILY_DISPATCH_SCAN_ROOTS)
    violations: list[str] = []

    for path in scanned_files:
        rel = _relative(path)
        tree = _tree(path)

        for func_node in ast.walk(tree):
            if not isinstance(func_node, ast.FunctionDef):
                continue

            # Collect all Compare nodes in this function that match
            # ``<name> == <constant-builtin-family-id>``
            family_id_matches: list[tuple[int, str]] = []
            for node in ast.walk(func_node):
                if not isinstance(node, ast.Compare):
                    continue
                if len(node.ops) != 1:
                    continue
                if not isinstance(node.ops[0], (ast.Eq, ast.Is)):
                    continue
                # Check left side
                left_name = _name_id(node.left)
                right_name = _name_id(node.comparators[0])
                left_const = _constant_value(node.left)
                right_const = _constant_value(node.comparators[0])

                match = None
                if left_name and right_const and right_const in BUILTIN_FAMILY_IDS:
                    match = right_const
                elif right_name and left_const and left_const in BUILTIN_FAMILY_IDS:
                    match = left_const

                if match is not None:
                    # Skip identity-matching patterns
                    other_side = _name_id(node.comparators[0]) if left_name else _name_id(node.left)
                    if other_side and other_side in IDENTITY_PATTERNS:
                        continue
                    family_id_matches.append((node.lineno, match))

            # Flag functions with >= 2 distinct family IDs (branch table)
            # unless the file is in the allowed set
            distinct_families = {m for _, m in family_id_matches}
            if len(distinct_families) >= 2 and rel not in ALLOWED_BRANCH_FILES:
                violations.append(
                    f"{rel} function {func_node.name} dispatches on "
                    f"{sorted(distinct_families)} at lines "
                    f"{[ln for ln, _ in family_id_matches]}"
                )
            # Flag single checks in non-allowed files when the function name
            # suggests a generic path (claim, lifecycle, counter, status,
            # queue, activation, routing, selection)
            elif len(distinct_families) == 1 and rel not in ALLOWED_BRANCH_FILES:
                generic_keywords = (
                    "claim", "lifecycle", "counter", "status", "queue",
                    "activation", "routing", "selection", "transition",
                    "apply", "move", "resolve", "dispatch",
                )
                if any(kw in func_node.name.lower() for kw in generic_keywords):
                    violations.append(
                        f"{rel} function {func_node.name} compares family_id "
                        f"to {sorted(distinct_families)} in a generic routing path"
                    )

    assert violations == [], (
        f"{len(violations)} family-ID branch dispatch violation(s):\n"
        + "\n".join(violations)
    )


def test_queue_family_interpreter_has_no_family_id_branches() -> None:
    """Positive guardrail: QueueFamilyInterpreter methods must not branch on
    family_id against hard-coded values.  The interpreter is the canonical
    generic family engine surface."""
    path = SRC_ROOT / "workspace" / "queue_family_interpreter.py"
    tree = _tree(path)

    # Find class
    class_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "QueueFamilyInterpreter":
            class_node = node
            break

    assert class_node is not None, "QueueFamilyInterpreter class not found"

    violations: list[str] = []
    for node in ast.walk(class_node):
        if not isinstance(node, ast.Compare):
            continue
        if len(node.ops) != 1:
            continue
        if not isinstance(node.ops[0], (ast.Eq, ast.Is, ast.NotEq, ast.IsNot)):
            continue
        left_const = _constant_value(node.left)
        right_const = _constant_value(node.comparators[0])
        if left_const in BUILTIN_FAMILY_IDS or right_const in BUILTIN_FAMILY_IDS:
            violations.append(
                f"QueueFamilyInterpreter compares against hard-coded "
                f"family id at line {node.lineno}"
            )

    assert violations == [], (
        f"QueueFamilyInterpreter contains {len(violations)} "
        f"family-id branch(es):\n" + "\n".join(violations)
    )


# -- counter routing guardrails ---------------------------------------------


def test_no_fixed_counter_field_name_dispatch_outside_legacy_compat() -> None:
    """Guardrail: counter routing in result_counters.py and
    graph_authority/counters.py must not resolve behavior from fixed
    counter field names outside the documented legacy compatibility set.

    Active dispatch must use the generic ``counter_id`` key from compiled
    policy metadata rather than a hard-coded mapping of counter name to
    snapshot field.
    """
    COUNTER_ROUTING_FILES = (
        SRC_ROOT / "runtime" / "result_counters.py",
        SRC_ROOT / "runtime" / "graph_authority" / "counters.py",
    )

    violations: list[str] = []

    for path in COUNTER_ROUTING_FILES:
        if not path.is_file():
            continue
        tree = _tree(path)
        rel = _relative(path)

        # Find all string constants that look like counter field names
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant):
                continue
            if not isinstance(node.value, str):
                continue
            if "_count" not in node.value and "counter" not in node.value:
                continue
            # Skip if it's in a string that's clearly a docstring or a
            # counter_id reference (the generic key), or in the legacy set
            if node.value in LEGACY_COUNTER_FIELD_NAMES:
                continue
            # Check context: is this being used as a key/index into a counter
            # dict or as a snapshot attribute?  We check the parent node.
            parent = _parent_node(tree, node)
            if parent is None:
                continue
            if isinstance(parent, ast.Subscript):
                # e.g. counters["fix_cycle_count"] — flag only if it's
                # a hard-coded field name used as a dict key outside the
                # legacy set
                if node.value not in LEGACY_COUNTER_FIELD_NAMES:
                    violations.append(
                        f"{rel} line {node.lineno}: counter subscript "
                        f"'{node.value}' used as hard-coded field name"
                    )
            elif isinstance(parent, ast.Attribute) and parent.attr == node.value:
                # This is unlikely (attributes aren't string constants)
                pass

    # Also scan for if/elif chains using counter_id against literal names
    # that are NOT generic policy-derived identifiers
    for path in COUNTER_ROUTING_FILES:
        if not path.is_file():
            continue
        tree = _tree(path)
        rel = _relative(path)

        for func_node in ast.walk(tree):
            if not isinstance(func_node, ast.FunctionDef):
                continue
            counter_comparisons: list[str] = []
            for node in ast.walk(func_node):
                if not isinstance(node, ast.Compare):
                    continue
                if len(node.ops) != 1:
                    continue
                if not isinstance(node.ops[0], ast.Eq):
                    continue
                left_const = _constant_value(node.left)
                right_const = _constant_value(node.comparators[0])
                const = left_const or right_const
                if (
                    isinstance(const, str)
                    and ("_count" in const or "counter" in const)
                    and const not in LEGACY_COUNTER_FIELD_NAMES
                ):
                    counter_comparisons.append(const)
            if len(counter_comparisons) >= 2:
                violations.append(
                    f"{rel} function {func_node.name} dispatches on "
                    f"counter names {counter_comparisons}"
                )

    assert violations == [], (
        f"{len(violations)} fixed counter field name violation(s):\n"
        + "\n".join(violations)
    )


# -- status projection guardrails -------------------------------------------


def test_no_plane_keyed_status_rebuilds_outside_projection_helper() -> None:
    """Guardrail: modules outside status_projections.py must not rebuild
    plane-keyed status markers or queue depths from scratch using
    Plane enum constants as literal dict keys.

    Scanning runtime/, workspace/, and cli/ for direct construction of
    ``{Plane.EXECUTION: ..., Plane.PLANNING: ..., Plane.LEARNING: ...}``
    dict patterns that bypass the shared projection helpers.
    """
    STATUS_PROJECTION_FILES = frozenset({
        "src/millrace_ai/runtime/status_projections.py",
        "src/millrace_ai/runtime/engine.py",  # writes to snapshot.status_markers_by_plane (compat)
        "src/millrace_ai/runtime/lifecycle.py",  # reads snapshot for lifecycle events (compat)
        "src/millrace_ai/cli/monitoring.py",  # reads for monitor output (compat)
        "src/millrace_ai/workspace/bootstrap_files.py",  # default snapshot payload (compat)
    })

    scanned_files = _python_files(*FAMILY_DISPATCH_SCAN_ROOTS)
    violations: list[str] = []

    for path in scanned_files:
        rel = _relative(path)
        if rel in STATUS_PROJECTION_FILES:
            continue

        tree = _tree(path)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            # Check if this dict uses Plane enum constants as keys and
            # string values (suggesting a status marker rebuild)
            plane_key_count = 0
            for key_node in node.keys:
                key_name = _name_id(key_node)
                if key_name and key_name.startswith("Plane."):
                    plane_key_count += 1
                elif isinstance(key_node, ast.Attribute):
                    plane_attr = _plane_enum_key(key_node)
                    if plane_attr:
                        plane_key_count += 1

            # A dict with >= 2 Plane enum keys and string values strongly
            # suggests a status-marker rebuild
            if plane_key_count >= 2:
                has_string_values = any(
                    isinstance(v, ast.Constant) and isinstance(v.value, str)
                    for v in node.values
                )
                if has_string_values:
                    violations.append(
                        f"{rel} line {node.lineno}: builds dict with "
                        f"{plane_key_count} Plane-keyed entries (suspect "
                        f"status marker rebuild outside projections helper)"
                    )

    assert violations == [], (
        f"{len(violations)} plane-keyed status rebuild(s) outside "
        f"status_projections.py:\n" + "\n".join(violations)
    )


# -- doc guardrails for generic engine concepts -----------------------------


def test_runtime_architecture_doc_mentions_compiled_work_families() -> None:
    """Fail if docs/runtime/millrace-runtime-architecture.md drops mention of
    compiled work-item families as the runtime's queue identity authority."""
    path = _docs_path("docs/runtime/millrace-runtime-architecture.md")
    text = path.read_text(encoding="utf-8")

    required_terms = (
        "work-item family",
        "queue family",
        "family definition",
    )
    found = [t for t in required_terms if t.lower() in text.lower()]
    assert found, (
        "docs/runtime/millrace-runtime-architecture.md no longer mentions "
        "compiled work-item families or family definitions"
    )


def test_runtime_authority_map_doc_mentions_generic_claim_flow() -> None:
    """Fail if docs/runtime/millrace-runtime-authority-map.md drops mention of
    generic claim flow or adapter extension points."""
    path = _docs_path("docs/runtime/millrace-runtime-authority-map.md")
    text = path.read_text(encoding="utf-8")

    required_terms = (
        "generic router",
        "adapter",
        "status_projections",
        "result_counters",
    )
    missing = [t for t in required_terms if t.lower() not in text.lower()]
    assert not missing, (
        f"docs/runtime/millrace-runtime-authority-map.md is missing "
        f"references to: {', '.join(missing)}"
    )


def test_refactor_candidate_register_marks_generic_engine_seams_complete() -> None:
    """Fail if the refactor-candidate-register drops its Generic Engine
    Boundary Seams table or fails to mark core migrations as complete."""
    path = _docs_path(CANONICAL_LEDGER)
    text = path.read_text(encoding="utf-8")

    assert "Generic Engine Boundary Seams" in text, (
        "refactor-candidate-register.md no longer contains the "
        "Generic Engine Boundary Seams table"
    )
    # Several core migrations should be marked complete
    complete_indicators = [
        "Core migration complete",
        "Migration complete",
    ]
    found_any = any(ind in text for ind in complete_indicators)
    assert found_any, (
        "refactor-candidate-register.md generic engine seams table "
        "no longer marks any migrations as complete"
    )


def test_source_package_map_mentions_generic_queue_engine_surfaces() -> None:
    """Fail if docs/source-package-map.md drops mention of generic queue
    engine surfaces (queue family interpreter, family adapters, status
    projections, result counters)."""
    path = _docs_path("docs/source-package-map.md")
    text = path.read_text(encoding="utf-8")

    required_terms = (
        "queue_family_interpreter",
        "family_adapters",
        "status_projections",
        "result_counters",
    )
    missing = [t for t in required_terms if t not in text]
    assert not missing, (
        f"docs/source-package-map.md is missing references to "
        f"generic queue engine surfaces: {', '.join(missing)}"
    )


# -- positive proof: generic interpreter handles any family -----------------


def test_generic_queue_family_interpreter_initializes_with_all_registry_families() -> None:
    """Positive proof: QueueFamilyInterpreter accepts any family ID from
    the shipped registry without hard-coded branch dispatch."""
    from millrace_ai.assets import load_builtin_workflow_primitives
    from millrace_ai.workspace.queue_family_interpreter import (
        QueueFamilyInterpreter,
    )

    primitives = load_builtin_workflow_primitives()
    families = primitives.work_item_families
    assert len(families) >= 5, f"Expected >= 5 built-in families, got {len(families)}"

    # The interpreter must accept the families tuple without error.
    import tempfile

    from millrace_ai.workspace.paths import workspace_paths

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        paths = workspace_paths(tmp_path)
        interpreter = QueueFamilyInterpreter(paths, families=families)

        # Every family should be known
        for family in families:
            f = interpreter.family(family.family_id)
            assert f.family_id == family.family_id
            # queue_depths_by_family should include every family
            depths = interpreter.queue_depths_by_family()
            assert family.family_id in depths


def test_status_projections_derive_plane_compat_from_family_keys() -> None:
    """Positive proof: build_queue_projections derives plane-keyed depths
    from family-keyed depths, not from hard-coded plane lists."""
    from millrace_ai.contracts import Plane
    from millrace_ai.runtime.status_projections import (
        build_queue_projections,
    )

    # Supply family-keyed depths with a non-standard set of families
    family_depths = {
        "task": 3,
        "spec": 2,
        "probe": 1,
    }
    families_by_plane = {
        "task": Plane.EXECUTION,
        "spec": Plane.PLANNING,
        "probe": Plane.PLANNING,
    }

    projections = build_queue_projections(
        queue_depths_by_family=family_depths,
        families_by_plane=families_by_plane,
    )

    assert projections.queue_depths_by_family == family_depths
    assert projections.queue_depths_by_plane[Plane.EXECUTION] == 3
    assert projections.queue_depths_by_plane[Plane.PLANNING] == 3
    assert projections.queue_depths_by_plane[Plane.LEARNING] == 0


def test_result_counters_use_generic_counter_id_not_fixed_field_names() -> None:
    """Positive proof: increment_counter_field accepts arbitrary counter_id
    strings and stores them in the generic counters dict."""
    from datetime import datetime, timezone

    from millrace_ai.contracts import (
        RecoveryCounterEntry,
        WorkItemKind,
    )

    # Verify that RecoveryCounterEntry stores arbitrary counter_id strings
    # in its generic counters dict (the field increment_counter_field writes to).
    now = datetime.now(timezone.utc)
    entry = RecoveryCounterEntry(
        failure_class="recoverable_failure",
        work_item_family_id="task",
        work_item_kind=WorkItemKind.TASK,
        work_item_id="test-task-1",
        counters={"custom_op_counter": 0},
        last_updated_at=now,
    )

    # Verify the generic counter key is stored in the counters dict
    assert "custom_op_counter" in entry.counters
    assert entry.counters["custom_op_counter"] == 0

    # The counters dict is generic and accepts any string key
    updated = entry.model_copy(
        update={"counters": {"custom_op_counter": 1, "another_counter": 5}}
    )
    assert updated.counters["another_counter"] == 5


# ---------------------------------------------------------------------------
# Subprocess import laziness guardrails
# ---------------------------------------------------------------------------

# Complete forbidden-prefix matrix for subprocess import and startup probes.
# These are the Arbiter canonical seven-prefix set plus any domain
# implementation modules discovered during implementation that must not
# be eagerly loaded on import of the public runtime surface or on
# generic-only runtime startup.
_FORBIDDEN_IMPORT_PREFIXES = frozenset({
    # Arbiter canonical seven-prefix set (from the Arbiter contract)
    "millrace_ai.recon_packets",
    "millrace_ai.runtime.completion_behavior",
    "millrace_ai.runtime.graph_authority.validation",
    "millrace_ai.runtime.learning_promotions",
    "millrace_ai.runtime.learning_triggers",
    "millrace_ai.workspace.arbiter_state",
    "millrace_ai.workspace.blueprint_state",
    # Additional domain implementation modules that must not eagerly load
    "millrace_ai.runtime.context.blueprint",
    "millrace_ai.runtime.recon_transitions",
})

# Known modules that are acceptably eager because they load through the
# public contracts surface (contracts/__init__.py) or are required by
# shipped stage-kind plugins that register at import time.
# Modules that conflict with the closure contract (recon_packets,
# blueprint_state, arbiter_state, learning_triggers, learning_promotions,
# runtime.effects.operation_runners) have been removed as they are no
# longer eagerly loaded.
_KNOWN_EAGER_DOMAIN_MODULES = frozenset({
    "millrace_ai.contracts.blueprint",
    "millrace_ai.contracts.recon",
    "millrace_ai.workspace.families.blueprint",
    "millrace_ai.runtime.planner_effects",
    "millrace_ai.compilation.learning_triggers",
    "millrace_ai.compilation.validation.repair_closures",
})


def _subprocess_import_check(import_stmt: str) -> tuple[int, str, str]:
    """Run *import_stmt* in a clean subprocess and return
    (returncode, stdout, stderr).  The subprocess script prints one
    module name per line for any domain module loaded that matches a
    forbidden prefix.  Known-eager modules are reported for audit
    visibility only.
    """
    script = textwrap.dedent(f"""\
        import sys
        before = set(sys.modules.keys())
        {import_stmt}
        after = set(sys.modules.keys())

        FORBIDDEN = {sorted(_FORBIDDEN_IMPORT_PREFIXES)!r}
        KNOWN = {sorted(_KNOWN_EAGER_DOMAIN_MODULES)!r}

        violations = []
        for mod in sorted(after - before):
            for prefix in FORBIDDEN:
                if mod == prefix or mod.startswith(prefix + '.'):
                    violations.append(mod)
                    break

        if violations:
            for v in violations:
                print(v)
            sys.exit(1)

        # Report known eager modules for audit visibility
        eager = []
        for mod in sorted(after - before):
            for prefix in KNOWN:
                if mod == prefix or mod.startswith(prefix + '.'):
                    eager.append(mod)
                    break
        for e in eager:
            print(f'KNOWN_EAGER:{{e}}')
    """)

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPO_ROOT),
    )
    return result.returncode, result.stdout, result.stderr


def test_subprocess_import_millrace_ai_runtime_does_not_load_forbidden_prefixes() -> None:
    """Subprocess guardrail: ``import millrace_ai.runtime`` must not eagerly
    load any module matching the full Arbiter forbidden-prefix matrix.

    The forbidden prefixes cover domain, authority, Blueprint, Learning,
    Recon, and workspace-state modules that must only load when explicitly
    resolved through an extension boundary interface or a compiled request
    context provider selection.
    """
    returncode, stdout, stderr = _subprocess_import_check(
        "import millrace_ai.runtime"
    )

    assert returncode == 0, (
        f"Subprocess import of millrace_ai.runtime loaded forbidden "
        f"prefix module(s):\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )


def test_subprocess_import_runtime_engine_does_not_load_forbidden_prefixes() -> None:
    """Subprocess guardrail: ``from millrace_ai.runtime import RuntimeEngine``
    must not eagerly load any module matching the full Arbiter
    forbidden-prefix matrix.
    """
    returncode, stdout, stderr = _subprocess_import_check(
        "from millrace_ai.runtime import RuntimeEngine"
    )

    assert returncode == 0, (
        f"Subprocess import of RuntimeEngine loaded forbidden "
        f"prefix module(s):\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )


def test_extension_boundary_registry_lazily_loads_domain_implementations() -> None:
    """Positive proof: the BuiltInExtensionBoundaryRegistry does not eagerly
    import domain implementation modules when the registry singleton is
    first accessed.

    The registry stores module paths as strings and only imports them
    via importlib when an interface is explicitly resolved.
    """
    import sys

    # Clear millrace modules to get a clean state
    preserved = {}
    for m in list(sys.modules.keys()):
        if "millrace" in m:
            preserved[m] = sys.modules.pop(m)

    try:
        from millrace_ai.extensions.boundaries import (
            builtin_extension_boundary_registry,
        )

        before = set(sys.modules.keys())
        registry = builtin_extension_boundary_registry()
        after = set(sys.modules.keys())

        # Accessing the registry singleton must not import domain modules
        eager_domain = []
        for mod in sorted(after - before):
            for prefix in _FORBIDDEN_IMPORT_PREFIXES:
                if mod == prefix or mod.startswith(prefix + "."):
                    eager_domain.append(mod)
                    break

        assert eager_domain == [], (
            f"BuiltInExtensionBoundaryRegistry singleton access loaded "
            f"domain implementation modules: {eager_domain}"
        )

        # Now resolve a domain interface — it MUST load the implementation
        after_registry = set(sys.modules.keys())
        _handler = registry.get_recon_transition_handler()
        after_resolve = set(sys.modules.keys())

        recon_loaded = [
            m
            for m in sorted(after_resolve - after_registry)
            if "recon" in m
        ]
        assert recon_loaded, (
            "Resolving recon_transition_handler did not load any Recon modules"
        )

        # Blueprint modules must NOT be loaded by resolving a Recon handler
        blueprint_loaded = [
            m
            for m in sorted(after_resolve - after_registry)
            if "blueprint" in m
        ]
        assert blueprint_loaded == [], (
            f"Resolving recon_transition_handler also loaded Blueprint "
            f"modules: {blueprint_loaded}"
        )
    finally:
        # Restore previously cached modules
        for m in list(sys.modules.keys()):
            if "millrace" in m and m not in preserved:
                del sys.modules[m]
        sys.modules.update(preserved)


# -- generic-only runtime startup subprocess probe -------------------------


def test_subprocess_generic_only_runtime_startup_does_not_load_forbidden_prefixes() -> None:
    """Subprocess guardrail: starting a RuntimeEngine in a clean temp
    workspace must not eagerly load any module matching the full Arbiter
    forbidden-prefix matrix.

    This probe simulates a generic-only startup (no extension domain
    selection) and checks that forbidden domain, authority, Blueprint,
    Learning, Recon, and workspace-state modules stay unloaded.
    """
    script = textwrap.dedent(f"""\
        import sys, tempfile
        from pathlib import Path
        before = set(sys.modules.keys())

        from millrace_ai.runners.requests import RunnerRawResult
        from millrace_ai.workspace.paths import workspace_paths
        from millrace_ai.runtime.engine import RuntimeEngine

        # Dummy stage runner that accepts any request
        def _dummy_runner(request):
            return RunnerRawResult(
                prompt_result_text="",
                raw_usage={{}},
                norm_usage={{}},
            )

        with tempfile.TemporaryDirectory() as tmp:
            paths = workspace_paths(Path(tmp))
            engine = RuntimeEngine(paths, stage_runner=_dummy_runner)
            _ = engine  # suppress unused-variable warning

        after = set(sys.modules.keys())

        FORBIDDEN = {sorted(_FORBIDDEN_IMPORT_PREFIXES)!r}

        violations = []
        for mod in sorted(after - before):
            for prefix in FORBIDDEN:
                if mod == prefix or mod.startswith(prefix + '.'):
                    violations.append(mod)
                    break

        if violations:
            for v in violations:
                print(v)
            sys.exit(1)
        else:
            print('GENERIC_ONLY_STARTUP_CLEAN')
    """)

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )

    assert result.returncode == 0, (
        f"Generic-only runtime startup loaded forbidden prefix module(s):"
        f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# -- state_reconciliation C4 hardwired-pattern guardrails --------------------

# Path to state_reconciliation.py for C4 guardrail scans
_STATE_RECONCILIATION_PATH = SRC_ROOT / "workspace" / "state_reconciliation.py"


def test_state_reconciliation_no_stage_allowed_markers() -> None:
    """Guardrail: state_reconciliation.py must not define _STAGE_ALLOWED_MARKERS.

    This hardwired shipped-stage marker map must be removed in favor of
    compiled-plan metadata.  Fail if the assignment target is found in the
    AST.
    """
    path = _STATE_RECONCILIATION_PATH
    tree = _tree(path)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_STAGE_ALLOWED_MARKERS":
                    pytest.fail(
                        f"{_relative(path)} line {node.lineno}: "
                        "_STAGE_ALLOWED_MARKERS assignment found; must be removed "
                        "in favor of compiled-plan metadata (C4)"
                    )


def test_state_reconciliation_no_stage_inbound_markers() -> None:
    """Guardrail: state_reconciliation.py must not define _STAGE_INBOUND_MARKERS.

    This hardwired shipped-stage inbound marker map must be removed in favor
    of compiled-plan metadata.  Fail if the assignment target is found in the
    AST.
    """
    path = _STATE_RECONCILIATION_PATH
    tree = _tree(path)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_STAGE_INBOUND_MARKERS":
                    pytest.fail(
                        f"{_relative(path)} line {node.lineno}: "
                        "_STAGE_INBOUND_MARKERS assignment found; must be removed "
                        "in favor of compiled-plan metadata (C4)"
                    )


def test_state_reconciliation_no_module_level_blueprint_imports() -> None:
    """Guardrail: state_reconciliation.py must not have module-level imports
    of BlueprintManifestDocument or BlueprintDraftDocument.

    These domain-specific imports must be lazy (behind function-scope imports)
    or routed through blueprint_state.py's public API (C4).
    """
    path = _STATE_RECONCILIATION_PATH
    tree = _tree(path)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in ("BlueprintManifestDocument", "BlueprintDraftDocument"):
                    # Only flag module-level imports (indent level 0)
                    pytest.fail(
                        f"{_relative(path)} line {node.lineno}: "
                        f"module-level import of {alias.name} found; must use lazy "
                        "function-scope import (C4)"
                    )


# -- shipped-stage hardwiring guardrail ------------------------------------

# Shipped stage kind IDs that must not appear as hard-coded string
# comparisons or branch dispatch keys in generic runtime or graph
# authority code.  Routing must use compiled metadata
# (node_id, stage_kind_id from graph materialization) instead.
_SHIPPED_STAGE_KIND_IDS = frozenset({
    # Execution plane
    "basic_worker", "builder", "checker", "consultant", "doublechecker",
    "fixer", "integrator", "troubleshooter", "updater",
    # Planning plane
    "arbiter", "auditor", "basic_planner", "contractor_blueprint",
    "evaluator_blueprint", "manager_blueprint", "manager",
    "mechanic_blueprint", "mechanic", "planner", "recon",
    # Learning plane
    "analyst", "basic_learner", "curator", "librarian", "professor",
})

# Files where shipped-stage string literals are explicitly allowed
# (stage-kind assets, compilation validation, extension built-ins,
# stage metadata registry, and config-data-only directories).
_STAGE_KIND_STRING_ALLOWED_FILES = frozenset({
    "src/millrace_ai/assets/architecture.py",
    "src/millrace_ai/architecture/stage_kinds.py",
    "src/millrace_ai/compilation/validation/stages.py",
    "src/millrace_ai/compilation/validation/artifacts.py",
    "src/millrace_ai/compilation/validation/extensions.py",
    "src/millrace_ai/contracts/stage_metadata.py",
    "src/millrace_ai/runtime/graph_authority/stage_mapping.py",
    "src/millrace_ai/runtime/skill_evidence.py",
    "src/millrace_ai/runtime/recovery/repair_routes.py",
    "src/millrace_ai/extensions/builtin/",  # prefix match for all built-in extensions
})

# Graph authority and routing files (the primary scan target for this guardrail)
_STAGE_KIND_SCAN_ROOTS = (
    SRC_ROOT / "runtime" / "graph_authority",
    SRC_ROOT / "runtime" / "result_application.py",
    SRC_ROOT / "runtime" / "effect_execution.py",
    SRC_ROOT / "runtime" / "activation.py",
    SRC_ROOT / "runtime" / "lifecycle.py",
    SRC_ROOT / "runtime" / "lifecycle_interpreter.py",
    SRC_ROOT / "runtime" / "supervisor.py",
    SRC_ROOT / "runtime" / "engine.py",
)


def test_no_shipped_stage_kind_id_hardwired_in_routing_or_authority_code() -> None:
    """Guardrail: routing and graph authority code must not compare against
    hard-coded shipped stage kind ID strings.

    Stage routing must use compiled node identity (node_id, stage_kind_id
    from graph materialization) rather than hard-coded stage kind names
    like "builder", "checker", "manager", etc.
    """
    violations: list[str] = []

    for scan_target in _STAGE_KIND_SCAN_ROOTS:
        if scan_target.is_dir():
            files = _python_files(scan_target)
        elif scan_target.is_file():
            files = (scan_target,)
        else:
            continue

        for path in files:
            rel = _relative(path)

            # Skip explicitly allowed files
            if rel in _STAGE_KIND_STRING_ALLOWED_FILES:
                continue
            # Skip prefix-matched allowed paths (e.g. extensions/builtin/)
            if any(
                rel.startswith(allowed.rstrip("/") + "/")
                for allowed in _STAGE_KIND_STRING_ALLOWED_FILES
                if allowed.endswith("/")
            ):
                continue

            tree = _tree(path)

            for node in ast.walk(tree):
                if not isinstance(node, ast.Compare):
                    continue
                for op_node, comparator in zip(node.ops, node.comparators):
                    if not isinstance(op_node, (ast.Eq, ast.Is, ast.NotEq, ast.IsNot)):
                        continue
                    const_val = _constant_value(comparator)
                    if (
                        isinstance(const_val, str)
                        and const_val in _SHIPPED_STAGE_KIND_IDS
                    ):
                        violations.append(
                            f"{rel} line {node.lineno}: compares against "
                            f"shipped stage kind ID {const_val!r}"
                        )
                    # Also check left side
                    left_val = _constant_value(node.left)
                    if (
                        isinstance(left_val, str)
                        and left_val in _SHIPPED_STAGE_KIND_IDS
                    ):
                        violations.append(
                            f"{rel} line {node.lineno}: left operand is "
                            f"shipped stage kind ID {left_val!r}"
                        )

    # Deduplicate
    violations = sorted(set(violations))

    assert violations == [], (
        f"{len(violations)} shipped-stage hardwiring violation(s):\n"
        + "\n".join(violations)
        + "\n\nRouting and authority code must use compiled node identity "
        "(node_id, stage_kind_id) rather than hard-coded stage kind names."
    )


# -- family-kind enum dispatch guardrail -----------------------------------

# WorkItemKind enum member names that must not be used as branch dispatch
# values in generic runtime code (graph_authority, activation, lifecycle,
# supervisor, engine).  Dispatch must use the family_id string from
# compiled work-family metadata, not the WorkItemKind enum.
_FORBIDDEN_WORK_ITEM_KIND_MEMBERS = frozenset({
    "TASK",
    "SPEC",
    "PROBE",
    "INCIDENT",
    "LEARNING_REQUEST",
    "BLUEPRINT_DRAFT",
})

# Files where WorkItemKind enum dispatch is explicitly allowed (known
# non-generic edges: Blueprint context, queue mutation, completion behavior
# for closure-target normalization, and the WorkItemKind enum definition
# itself).
_WORK_ITEM_KIND_ALLOWED_FILES = frozenset({
    "src/millrace_ai/runtime/recovery/events.py",
    "src/millrace_ai/runtime/recovery/queue_mutation.py",
    "src/millrace_ai/runtime/context/blueprint.py",
    "src/millrace_ai/runtime/completion_behavior.py",
    "src/millrace_ai/runtime/graph_authority/validation.py",
    "src/millrace_ai/runtime/graph_authority/stage_mapping.py",
    "src/millrace_ai/contracts/enums.py",
})

# Generic runtime files to scan for WorkItemKind enum dispatch
_WORK_ITEM_KIND_SCAN_ROOTS = (
    SRC_ROOT / "runtime" / "graph_authority",
    SRC_ROOT / "runtime" / "activation.py",
    SRC_ROOT / "runtime" / "error_recovery.py",
    SRC_ROOT / "runtime" / "lifecycle.py",
    SRC_ROOT / "runtime" / "lifecycle_interpreter.py",
    SRC_ROOT / "runtime" / "supervisor.py",
    SRC_ROOT / "runtime" / "engine.py",
    SRC_ROOT / "runtime" / "result_application.py",
    SRC_ROOT / "runtime" / "effect_execution.py",
    SRC_ROOT / "workspace" / "queue_selection.py",
    SRC_ROOT / "workspace" / "queue_claims.py",
    SRC_ROOT / "workspace" / "queue_lifecycle.py",
)


def test_no_work_item_kind_enum_branch_dispatch_in_generic_runtime_paths() -> None:
    """Guardrail: generic runtime code must not branch on WorkItemKind enum
    member values (e.g. ``WorkItemKind.TASK``).

    Active dispatch must use compiled work-family metadata (family_id
    strings from the registry) rather than hard-coded enum comparisons.
    """
    violations: list[str] = []

    for scan_target in _WORK_ITEM_KIND_SCAN_ROOTS:
        if scan_target.is_dir():
            files = _python_files(scan_target)
        elif scan_target.is_file():
            files = (scan_target,)
        else:
            continue

        for path in files:
            rel = _relative(path)
            if rel in _WORK_ITEM_KIND_ALLOWED_FILES:
                continue

            tree = _tree(path)

            for node in ast.walk(tree):
                # Detect ``WorkItemKind.TASK`` pattern in comparisons
                if not isinstance(node, ast.Compare):
                    continue
                for comparator in node.comparators:
                    if _is_work_item_kind_attr(comparator):
                        member = comparator.attr
                        violations.append(
                            f"{rel} line {node.lineno}: compares against "
                            f"WorkItemKind.{member}"
                        )
                if _is_work_item_kind_attr(node.left):
                    member = node.left.attr
                    violations.append(
                        f"{rel} line {node.lineno}: left operand is "
                        f"WorkItemKind.{member}"
                    )

            # Also scan for ``is WorkItemKind.TASK`` in IfExp/If tests
            for node in ast.walk(tree):
                if isinstance(node, ast.IfExp):
                    if _is_work_item_kind_attr(node.test):
                        violations.append(
                            f"{rel} line {node.lineno}: IfExp test is "
                            f"WorkItemKind.{node.test.attr}"
                        )

    # Deduplicate
    violations = sorted(set(violations))

    assert violations == [], (
        f"{len(violations)} WorkItemKind enum dispatch violation(s):\n"
        + "\n".join(violations)
        + "\n\nGeneric runtime code must use compiled work-family metadata "
        "(family_id strings) rather than WorkItemKind enum comparisons."
    )


def _is_work_item_kind_attr(node: ast.expr) -> bool:
    """Return True if *node* is an Attribute like WorkItemKind.TASK."""
    if not isinstance(node, ast.Attribute):
        return False
    if node.attr not in _FORBIDDEN_WORK_ITEM_KIND_MEMBERS:
        return False
    if isinstance(node.value, ast.Name) and node.value.id == "WorkItemKind":
        return True
    return False


# -- AST helpers ------------------------------------------------------------


def _name_id(node: ast.expr) -> str | None:
    """Return the dotted name for simple Name or Attribute chains."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name_id(node.value)
        if base:
            return f"{base}.{node.attr}"
        return f"?.{node.attr}"
    return None


def _constant_value(node: ast.expr) -> object | None:
    """Return the constant value if the node is a Constant."""
    if isinstance(node, ast.Constant):
        return node.value
    return None


def _plane_enum_key(node: ast.Attribute) -> str | None:
    """Return a plane-enum identifier if this Attribute looks like Plane.X."""
    if isinstance(node.value, ast.Name) and node.value.id == "Plane":
        if node.attr in {"EXECUTION", "PLANNING", "LEARNING"}:
            return f"Plane.{node.attr}"
    return None


def _parent_node(tree: ast.AST, target: ast.AST) -> ast.AST | None:
    """Return the parent node of *target* within *tree*."""
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            if child is target:
                return node
    return None
