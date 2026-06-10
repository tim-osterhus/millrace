"""Tests for the generic queue-family interpreter."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from millrace_ai.architecture import (
    WorkItemDocumentAdapterDefinition,
    WorkItemFamilyDefinition,
    WorkItemQueueDirs,
)
from millrace_ai.assets import load_builtin_workflow_primitives
from millrace_ai.contracts import Plane
from millrace_ai.errors import QueueStateError
from millrace_ai.workspace.paths import WorkspacePaths, workspace_paths
from millrace_ai.workspace.queue_family_interpreter import QueueFamilyInterpreter

NOW = datetime(2026, 5, 21, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Custom fixture family
# ---------------------------------------------------------------------------

CUSTOM_FAMILY = WorkItemFamilyDefinition(
    schema_version="1.0",
    kind="work_item_family",
    family_id="custom_fixture",
    plane=Plane.EXECUTION,
    entry_key="custom_fixture",
    display_name="Custom Fixture",
    document_kind="custom_fixture",
    runtime_relative_dir="custom_fixtures",
    file_extension=".md",
    schema_id="custom_fixture_v1",
    document_adapter_id="custom_fixture_adapter",
    queue_dirs=WorkItemQueueDirs(
        queue="custom_fixtures/queue",
        active="custom_fixtures/active",
        done="custom_fixtures/done",
        blocked="custom_fixtures/blocked",
    ),
    lifecycle_states=("queue", "active", "done", "blocked"),
    claimable_state="queue",
    active_state="active",
    done_state="done",
    blocked_state="blocked",
    closure_blocking_states=("queue", "active", "blocked"),
    id_field="custom_id",
    created_at_field="created_at",
    lineage_fields=("root_spec_id",),
    dependency_field="depends_on",
    one_active_policy="family",
    invalid_artifact_policy="quarantine",
    sort_policy="created_at_asc",
)

CUSTOM_ADAPTER = WorkItemDocumentAdapterDefinition(
    schema_version="1.0",
    kind="work_item_document_adapter",
    adapter_id="custom_fixture_adapter",
    schema_id="custom_fixture_v1",
    supported_file_extensions=(".md",),
    family_ids=("custom_fixture",),
    can_parse=True,
    can_render=True,
    can_summarize=True,
    supports_dependencies=True,
    supports_lineage=True,
)

REJECT_FAMILY = WorkItemFamilyDefinition(
    schema_version="1.0",
    kind="work_item_family",
    family_id="reject_test",
    plane=Plane.EXECUTION,
    entry_key="reject_test",
    display_name="Reject Test",
    document_kind="reject_test",
    runtime_relative_dir="reject_test",
    file_extension=".md",
    schema_id="reject_test_v1",
    document_adapter_id="reject_test_adapter",
    queue_dirs=WorkItemQueueDirs(
        queue="reject_test/queue",
        active="reject_test/active",
        done="reject_test/done",
        blocked="reject_test/blocked",
    ),
    lifecycle_states=("queue", "active", "done", "blocked"),
    claimable_state="queue",
    active_state="active",
    done_state="done",
    blocked_state="blocked",
    closure_blocking_states=("queue", "active", "blocked"),
    id_field="reject_id",
    created_at_field="created_at",
    lineage_fields=(),
    dependency_field=None,
    one_active_policy="family",
    invalid_artifact_policy="reject",
    sort_policy="created_at_asc",
)

REJECT_ADAPTER = WorkItemDocumentAdapterDefinition(
    schema_version="1.0",
    kind="work_item_document_adapter",
    adapter_id="reject_test_adapter",
    schema_id="reject_test_v1",
    supported_file_extensions=(".md",),
    family_ids=("reject_test",),
    can_parse=True,
    can_render=True,
    can_summarize=True,
    supports_dependencies=False,
    supports_lineage=False,
)


def _bootstrap_family_dirs(paths: WorkspacePaths, family: WorkItemFamilyDefinition) -> None:
    runtime = paths.runtime_root
    for attr_name in ("queue", "active", "done", "blocked"):
        relative = getattr(family.queue_dirs, attr_name)
        if relative:
            (runtime / relative).mkdir(parents=True, exist_ok=True)


def _make_markdown_doc(family: WorkItemFamilyDefinition, item_id: str, **extra) -> str:
    """Render a simple markdown work document for testing."""
    lines = [
        f"# Test {item_id}",
        "",
        f"{_id_label(family)}: {item_id}",
        f"Created-At: {NOW.isoformat()}",
    ]
    for label, value in extra.items():
        if isinstance(value, list):
            lines.append(f"{label}:")
            lines.extend(f"- {v}" for v in value)
        else:
            lines.append(f"{label}: {value}")
    return "\n".join(lines) + "\n"


def _id_label(family: WorkItemFamilyDefinition) -> str:
    mapping = {
        "task_id": "Task-ID",
        "spec_id": "Spec-ID",
        "probe_id": "Probe-ID",
        "incident_id": "Incident-ID",
        "learning_request_id": "Learning-Request-ID",
        "custom_id": "Custom-Id",
    }
    return mapping.get(family.id_field, family.id_field.replace("_", "-").title())


def _enqueue_markdown(paths: WorkspacePaths, family: WorkItemFamilyDefinition, item_id: str, **extra) -> Path:
    queue_dir = paths.runtime_root / family.queue_dirs.queue
    queue_dir.mkdir(parents=True, exist_ok=True)
    dest = queue_dir / f"{item_id}{family.file_extension}"
    dest.write_text(_make_markdown_doc(family, item_id, **extra), encoding="utf-8")
    return dest


def _enqueue_done(paths: WorkspacePaths, family: WorkItemFamilyDefinition, item_id: str) -> Path:
    done_dir = paths.runtime_root / family.queue_dirs.done
    done_dir.mkdir(parents=True, exist_ok=True)
    dest = done_dir / f"{item_id}{family.file_extension}"
    dest.write_text(_make_markdown_doc(family, item_id), encoding="utf-8")
    return dest


def _enqueue_active(paths: WorkspacePaths, family: WorkItemFamilyDefinition, item_id: str) -> Path:
    active_dir = paths.runtime_root / family.queue_dirs.active
    active_dir.mkdir(parents=True, exist_ok=True)
    dest = active_dir / f"{item_id}{family.file_extension}"
    dest.write_text(_make_markdown_doc(family, item_id), encoding="utf-8")
    return dest


# ---------------------------------------------------------------------------
# Interpreter construction
# ---------------------------------------------------------------------------

def _make_interpreter(tmp_path: Path) -> QueueFamilyInterpreter:
    paths = workspace_paths(tmp_path / "workspace")
    paths.runtime_root.mkdir(parents=True, exist_ok=True)
    return QueueFamilyInterpreter(paths)


def _make_interpreter_with_custom(tmp_path: Path) -> QueueFamilyInterpreter:
    paths = workspace_paths(tmp_path / "workspace")
    paths.runtime_root.mkdir(parents=True, exist_ok=True)
    builtin = load_builtin_workflow_primitives()
    families = (*builtin.work_item_families, CUSTOM_FAMILY)
    adapters = (*builtin.document_adapters, CUSTOM_ADAPTER)
    return QueueFamilyInterpreter(paths, families=families, document_adapters=adapters)


def _make_interpreter_with_reject(tmp_path: Path) -> QueueFamilyInterpreter:
    paths = workspace_paths(tmp_path / "workspace")
    paths.runtime_root.mkdir(parents=True, exist_ok=True)
    builtin = load_builtin_workflow_primitives()
    families = (*builtin.work_item_families, REJECT_FAMILY)
    adapters = (*builtin.document_adapters, REJECT_ADAPTER)
    return QueueFamilyInterpreter(paths, families=families, document_adapters=adapters)


# ---------------------------------------------------------------------------
# Path confinement
# ---------------------------------------------------------------------------

def test_path_confinement_rejects_parent_traversal(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    # override the queue dir to a path with .. which fails validation
    with pytest.raises(QueueStateError, match="escapes runtime root"):
        interp._confined_dir("../etc")


def test_path_confinement_rejects_absolute(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    with pytest.raises(QueueStateError, match="escapes runtime root"):
        interp._confined_dir("/etc")


def test_path_confinement_accepts_safe_relative(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    result = interp._confined_dir("tasks/queue")
    assert result == interp._paths.runtime_root / "tasks" / "queue"


# ---------------------------------------------------------------------------
# Queue file listing
# ---------------------------------------------------------------------------

def test_list_queue_files_empty(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    _bootstrap_family_dirs(interp._paths, family)
    assert interp.list_queue_files("task") == ()


def test_list_queue_files_with_items(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    _enqueue_markdown(paths, family, "task-one")
    _enqueue_markdown(paths, family, "task-two")
    files = interp.list_queue_files("task")
    assert len(files) == 2
    assert all(f.suffix == ".md" for f in files)


def test_list_all_state_files(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    _enqueue_markdown(paths, family, "task-queue")
    _enqueue_active(paths, family, "task-active")
    states = interp.list_all_state_files("task")
    assert "queue" in states
    assert "active" in states
    assert len(states["queue"]) == 1
    assert len(states["active"]) == 1


# ---------------------------------------------------------------------------
# Work-item ID extraction
# ---------------------------------------------------------------------------

def test_extract_work_item_id_from_content(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    path = _enqueue_markdown(paths, family, "task-extract-test")
    item_id = interp.extract_work_item_id("task", path)
    assert item_id == "task-extract-test"


def test_extract_work_item_id_fallback_to_stem(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    path = _enqueue_markdown(paths, family, "no-id-in-content")
    # Overwrite with content that lacks task-id
    path.write_text("# No ID\n\nSome body text\n", encoding="utf-8")
    item_id = interp.extract_work_item_id("task", path)
    assert item_id == "no-id-in-content"


# ---------------------------------------------------------------------------
# Filename / ID validation
# ---------------------------------------------------------------------------

def test_validate_filename_valid(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    path = _enqueue_markdown(paths, family, "task-valid")
    is_valid, error = interp.validate_work_item_filename("task", path)
    assert is_valid
    assert error is None


def test_validate_filename_mismatch(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    path = _enqueue_markdown(paths, family, "task-one")
    # Rewrite with a different task-id in content
    path.write_text(
        _make_markdown_doc(family, "task-different"),
        encoding="utf-8",
    )
    is_valid, error = interp.validate_work_item_filename("task", path)
    assert not is_valid
    assert error is not None
    assert "filename stem does not match" in error


def test_validate_filename_wrong_extension(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    queue_dir = paths.runtime_root / family.queue_dirs.queue
    path = queue_dir / "bad.xyz"
    path.write_text("test", encoding="utf-8")
    is_valid, error = interp.validate_work_item_filename("task", path)
    assert not is_valid
    assert "unexpected extension" in error


def test_validate_work_item_id_format(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    assert interp.validate_work_item_id_format("task", "good-id") == (True, None)
    is_valid, error = interp.validate_work_item_id_format("task", "")
    assert not is_valid
    assert "may not be empty" in error
    is_valid, error = interp.validate_work_item_id_format("task", "bad/id")
    assert not is_valid
    assert "path" in error.lower() or "separator" in error.lower()


# ---------------------------------------------------------------------------
# Root lineage filtering
# ---------------------------------------------------------------------------

def test_matches_root_spec_exact_match(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    path = _enqueue_markdown(
        paths, family, "task-lineage", **{"Root-Spec-ID": "spec-root-1"}
    )
    assert interp.matches_root_spec("task", path, root_spec_id="spec-root-1")
    assert not interp.matches_root_spec("task", path, root_spec_id="spec-other")


def test_matches_root_spec_no_lineage_fields(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    # Probe has no lineage_fields
    family = interp.family("probe")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    path = _enqueue_markdown(paths, family, "probe-any")
    assert interp.matches_root_spec("probe", path, root_spec_id="any")


# ---------------------------------------------------------------------------
# Dependency filtering
# ---------------------------------------------------------------------------

def test_dependencies_satisfied_no_deps_field(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("spec")  # spec has no dependency_field
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    path = _enqueue_markdown(paths, family, "spec-no-deps")
    satisfied, unresolved = interp.dependencies_satisfied("spec", path)
    assert satisfied
    assert unresolved is None


def test_dependencies_satisfied_with_resolved(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    _enqueue_done(paths, family, "task-dep-a")
    path = _enqueue_markdown(
        paths, family, "task-main", **{"Depends-On": ["task-dep-a"]}
    )
    satisfied, unresolved = interp.dependencies_satisfied("task", path)
    assert satisfied
    assert unresolved is None


def test_dependencies_unsatisfied(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    path = _enqueue_markdown(
        paths, family, "task-main", **{"Depends-On": ["task-missing"]}
    )
    satisfied, unresolved = interp.dependencies_satisfied("task", path)
    assert not satisfied
    assert unresolved == ["task-missing"]


# ---------------------------------------------------------------------------
# One-active policy checks
# ---------------------------------------------------------------------------

def test_one_active_policy_allows_when_empty(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    allowed, reason = interp.one_active_policy_check("task")
    assert allowed
    assert reason is None


def test_one_active_policy_blocks_when_occupied(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    _enqueue_active(paths, family, "task-already-active")
    allowed, reason = interp.one_active_policy_check("task")
    assert not allowed
    assert reason is not None


# ---------------------------------------------------------------------------
# Race-safe claim_next
# ---------------------------------------------------------------------------

def test_claim_next_returns_none_when_empty(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    assert interp.claim_next("task") is None


def test_claim_next_atomic_move(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    enqueued = _enqueue_markdown(paths, family, "task-claimed")
    claim = interp.claim_next("task")
    assert claim is not None
    assert claim.work_item_id == "task-claimed"
    assert claim.path.is_file()
    assert not enqueued.exists()  # moved away
    # Active dir
    active_dir = paths.runtime_root / family.queue_dirs.active
    assert (active_dir / "task-claimed.md").is_file()


def test_claim_next_respects_lineage(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    _enqueue_markdown(paths, family, "task-a", **{"Root-Spec-ID": "spec-x"})
    _enqueue_markdown(paths, family, "task-b", **{"Root-Spec-ID": "spec-y"})
    claim = interp.claim_next("task", root_spec_id="spec-y")
    assert claim is not None
    assert claim.work_item_id == "task-b"


def test_claim_next_respects_dependencies(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    _enqueue_markdown(paths, family, "task-blocked", **{"Depends-On": ["task-missing"]})
    _enqueue_markdown(paths, family, "task-ready")
    claim = interp.claim_next("task")
    assert claim is not None
    assert claim.work_item_id == "task-ready"


def test_claim_next_blocks_when_active_present(tmp_path: Path) -> None:
    """Normal one-active saturation returns None (not an error)."""
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    _enqueue_active(paths, family, "task-active")
    _enqueue_markdown(paths, family, "task-queued")
    claim = interp.claim_next("task")
    assert claim is None


# ---------------------------------------------------------------------------
# Queue depth
# ---------------------------------------------------------------------------

def test_queue_depth_zero(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    _bootstrap_family_dirs(interp._paths, family)
    assert interp.queue_depth("task") == 0


def test_queue_depth_with_items(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    _enqueue_markdown(paths, family, "t1")
    _enqueue_markdown(paths, family, "t2")
    _enqueue_markdown(paths, family, "t3")
    assert interp.queue_depth("task") == 3


def test_queue_depths_by_family(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    for family in interp.families:
        _bootstrap_family_dirs(interp._paths, family)
    depths = interp.queue_depths_by_family()
    assert "task" in depths
    assert "spec" in depths
    assert "incident" in depths
    assert all(d == 0 for d in depths.values())


# ---------------------------------------------------------------------------
# Invalid artifact identification and quarantine
# ---------------------------------------------------------------------------

def test_identify_invalid_artifacts_mismatched_id(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    path = _enqueue_markdown(paths, family, "task-name-mismatch")
    path.write_text(
        _make_markdown_doc(family, "task-other"),
        encoding="utf-8",
    )
    invalid = interp.identify_invalid_artifacts("task")
    assert len(invalid) == 1
    assert invalid[0][0] == path


def test_quarantine_moves_and_logs(tmp_path: Path) -> None:
    interp = _make_interpreter_with_custom(tmp_path)
    paths = interp._paths
    _bootstrap_family_dirs(paths, CUSTOM_FAMILY)
    path = _enqueue_markdown(paths, CUSTOM_FAMILY, "custom-mismatch")
    path.write_text(
        _make_markdown_doc(CUSTOM_FAMILY, "custom-other"),
        encoding="utf-8",
    )
    dest = interp.quarantine_invalid_artifact(
        "custom_fixture", path, "test quarantine error"
    )
    assert dest is not None
    assert not path.exists()
    assert dest.exists()
    assert dest.suffix == ".invalid" or ".invalid." in dest.suffix
    # Check diagnostic log
    log_path = (
        paths.runtime_root / CUSTOM_FAMILY.queue_dirs.queue / "invalid-artifacts.jsonl"
    )
    assert log_path.is_file()
    log_entry = json.loads(log_path.read_text().strip())
    assert log_entry["family_id"] == "custom_fixture"
    assert log_entry["adapter_id"] == "custom_fixture_adapter"
    assert log_entry["error_message"] == "test quarantine error"
    assert "at" in log_entry
    assert "source_path" in log_entry


def test_quarantine_block_source(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")  # task has block_source policy
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    path = _enqueue_markdown(paths, family, "task-bad")
    path.write_text(
        _make_markdown_doc(family, "task-different"),
        encoding="utf-8",
    )
    dest = interp.quarantine_invalid_artifact(
        "task", path, "block source test"
    )
    assert dest is not None
    assert not path.exists()
    assert ".invalid" in dest.suffix


def test_invalid_artifacts_skipped_during_claim(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    # Enqueue a valid task and an invalid one (mismatched ID)
    bad = _enqueue_markdown(paths, family, "task-bad")
    bad.write_text(_make_markdown_doc(family, "task-other"), encoding="utf-8")
    _enqueue_markdown(paths, family, "task-good")
    claim = interp.claim_next("task")
    assert claim is not None
    assert claim.work_item_id == "task-good"


def test_quarantine_reject_policy_logs_without_moving(tmp_path: Path) -> None:
    """Reject policy writes diagnostics but does NOT move the source file."""
    interp = _make_interpreter_with_reject(tmp_path)
    paths = interp._paths
    _bootstrap_family_dirs(paths, REJECT_FAMILY)
    path = _enqueue_markdown(paths, REJECT_FAMILY, "reject-mismatch")
    path.write_text(
        _make_markdown_doc(REJECT_FAMILY, "reject-other"),
        encoding="utf-8",
    )
    dest = interp.quarantine_invalid_artifact(
        "reject_test", path, "reject policy test"
    )
    # Reject returns None (no destination)
    assert dest is None
    # Source file is NOT moved
    assert path.exists()
    # Diagnostic log is written
    log_path = (
        paths.runtime_root / REJECT_FAMILY.queue_dirs.queue / "invalid-artifacts.jsonl"
    )
    assert log_path.is_file()
    log_entry = json.loads(log_path.read_text().strip())
    assert log_entry["family_id"] == "reject_test"
    assert log_entry["adapter_id"] == "reject_test_adapter"
    assert log_entry["error_message"] == "reject policy test"
    assert "at" in log_entry
    assert "source_path" in log_entry


# ---------------------------------------------------------------------------
# Adapter-aware dependency filtering: raw artifact key vs normalized attribute
# ---------------------------------------------------------------------------

def test_dependency_filtering_raw_key_differs_from_normalized(tmp_path: Path) -> None:
    """The task family's dependency_field='dependencies' differs from the raw
    markdown frontmatter key 'Depends-On:' which parses to 'depends_on'.
    Dependency filtering must bridge this gap."""
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)

    # Place a resolved dependency in done
    _enqueue_done(paths, family, "task-dep-resolved")

    # Enqueue a task whose raw artifact uses Depends-On (parsed to depends_on)
    # but the family's dependency_field is 'dependencies'
    path = _enqueue_markdown(
        paths, family, "task-adapter-aware",
        **{"Depends-On": ["task-dep-resolved"]}
    )

    # Verify the family uses 'dependencies' as dependency_field
    assert family.dependency_field == "dependencies"

    # Verify the raw markdown uses Depends-On which parses to depends_on
    raw = path.read_text()
    assert "Depends-On:" in raw

    # Dependency check should still work correctly across the mapping
    satisfied, unresolved = interp.dependencies_satisfied("task", path)
    assert satisfied
    assert unresolved is None

    # Also verify claim_next respects this adapter-aware filtering
    claim = interp.claim_next("task")
    assert claim is not None
    assert claim.work_item_id == "task-adapter-aware"


def test_dependency_filtering_unsatisfied_with_raw_key_mapping(tmp_path: Path) -> None:
    """When a dependency is missing, the adapter-aware check correctly
    reports it as unresolved even when the raw key differs from the field."""
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)

    path = _enqueue_markdown(
        paths, family, "task-missing-dep",
        **{"Depends-On": ["task-nonexistent"]}
    )

    satisfied, unresolved = interp.dependencies_satisfied("task", path)
    assert not satisfied
    assert unresolved == ["task-nonexistent"]


# ---------------------------------------------------------------------------
# Built-in family coverage: task
# ---------------------------------------------------------------------------

def test_task_family_listing(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    _enqueue_markdown(paths, family, "task-alpha")
    assert interp.queue_depth("task") == 1
    assert interp.list_queue_files("task")[0].stem == "task-alpha"


# ---------------------------------------------------------------------------
# Built-in family coverage: spec
# ---------------------------------------------------------------------------

def test_spec_family_listing_and_claim(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("spec")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    _enqueue_markdown(paths, family, "spec-first")
    claim = interp.claim_next("spec")
    assert claim is not None
    assert claim.work_item_id == "spec-first"
    assert claim.plane == Plane.PLANNING


# ---------------------------------------------------------------------------
# Built-in family coverage: probe
# ---------------------------------------------------------------------------

def test_probe_family_queue_depth(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("probe")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    _enqueue_markdown(paths, family, "probe-alpha")
    _enqueue_markdown(paths, family, "probe-beta")
    assert interp.queue_depth("probe") == 2


# ---------------------------------------------------------------------------
# Built-in family coverage: incident
# ---------------------------------------------------------------------------

def test_incident_family_claim(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("incident")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    _enqueue_markdown(paths, family, "incident-001")
    claim = interp.claim_next("incident")
    assert claim is not None
    assert claim.work_item_id == "incident-001"
    assert claim.plane == Plane.PLANNING


# ---------------------------------------------------------------------------
# Built-in family coverage: learning_request
# ---------------------------------------------------------------------------

def test_learning_request_family(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("learning_request")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    _enqueue_markdown(paths, family, "lr-001")
    files = interp.list_queue_files("learning_request")
    assert len(files) == 1
    assert files[0].stem == "lr-001"


# ---------------------------------------------------------------------------
# Custom fixture family
# ---------------------------------------------------------------------------

def test_custom_family_claim_and_depth(tmp_path: Path) -> None:
    interp = _make_interpreter_with_custom(tmp_path)
    paths = interp._paths
    _bootstrap_family_dirs(paths, CUSTOM_FAMILY)
    _enqueue_markdown(paths, CUSTOM_FAMILY, "custom-one")
    _enqueue_markdown(paths, CUSTOM_FAMILY, "custom-two")
    assert interp.queue_depth("custom_fixture") == 2
    claim = interp.claim_next("custom_fixture")
    assert claim is not None
    assert claim.family_id == "custom_fixture"
    assert claim.work_item_id in {"custom-one", "custom-two"}
    assert interp.queue_depth("custom_fixture") == 1


def test_custom_family_one_active_policy(tmp_path: Path) -> None:
    interp = _make_interpreter_with_custom(tmp_path)
    paths = interp._paths
    _bootstrap_family_dirs(paths, CUSTOM_FAMILY)
    _enqueue_active(paths, CUSTOM_FAMILY, "custom-active")
    _enqueue_markdown(paths, CUSTOM_FAMILY, "custom-queued")
    # custom_fixture has one_active_policy = "family"
    allowed, reason = interp.one_active_policy_check("custom_fixture")
    assert not allowed


def test_custom_family_dependency_filtering(tmp_path: Path) -> None:
    interp = _make_interpreter_with_custom(tmp_path)
    paths = interp._paths
    _bootstrap_family_dirs(paths, CUSTOM_FAMILY)
    _enqueue_done(paths, CUSTOM_FAMILY, "custom-dep-a")
    _enqueue_markdown(
        paths, CUSTOM_FAMILY, "custom-main",
        **{"Depends-On": ["custom-dep-a"]}
    )
    satisfied, unresolved = interp.dependencies_satisfied("custom_fixture", interp.list_queue_files("custom_fixture")[0])
    assert satisfied


# ---------------------------------------------------------------------------
# No family-ID branch dispatch test
# ---------------------------------------------------------------------------

def test_no_active_kernel_family_id_dispatch(tmp_path: Path) -> None:
    """Verify interpreter works uniformly across families without branch dispatch."""
    interp = _make_interpreter(tmp_path)
    for family in interp.families:
        _bootstrap_family_dirs(interp._paths, family)
    # All operations should work uniformly without family-ID switching
    for fid in ("task", "spec", "probe", "incident", "learning_request"):
        depth = interp.queue_depth(fid)
        assert isinstance(depth, int)
        states = interp.list_all_state_files(fid)
        assert isinstance(states, dict)
        allowed, _ = interp.one_active_policy_check(fid)
        assert isinstance(allowed, bool)


# ---------------------------------------------------------------------------
# families_for_plane
# ---------------------------------------------------------------------------

def test_families_for_plane(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    exec_fams = interp.families_for_plane(Plane.EXECUTION)
    assert any(f.family_id == "task" for f in exec_fams)
    plan_fams = interp.families_for_plane(Plane.PLANNING)
    assert any(f.family_id == "spec" for f in plan_fams)
    assert any(f.family_id == "probe" for f in plan_fams)
    learn_fams = interp.families_for_plane(Plane.LEARNING)
    assert any(f.family_id == "learning_request" for f in learn_fams)


# ---------------------------------------------------------------------------
# Blueprint draft family (JSON-based)
# ---------------------------------------------------------------------------

def test_blueprint_draft_family_operations(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("blueprint_draft")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    queue_dir = paths.runtime_root / family.queue_dirs.queue
    queue_dir.mkdir(parents=True, exist_ok=True)
    # Blueprint draft uses .json
    doc = {
        "schema_version": "1.0",
        "kind": "blueprint_draft",
        "draft_id": "bp-draft-001",
        "created_at": NOW.isoformat(),
    }
    path = queue_dir / "bp-draft-001.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert interp.queue_depth("blueprint_draft") == 1
    item_id = interp.extract_work_item_id("blueprint_draft", path)
    assert item_id == "bp-draft-001"


def test_blueprint_draft_invalid_json_quarantine(tmp_path: Path) -> None:
    interp = _make_interpreter(tmp_path)
    family = interp.family("blueprint_draft")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    queue_dir = paths.runtime_root / family.queue_dirs.queue
    path = queue_dir / "bad.json"
    path.write_text("not valid json", encoding="utf-8")
    invalid = interp.identify_invalid_artifacts("blueprint_draft")
    assert len(invalid) == 1
    assert invalid[0][0] == path


# ---------------------------------------------------------------------------
# one_active_policy = "plane" scoped across families in same plane
# ---------------------------------------------------------------------------


def test_one_active_policy_plane_scoped_across_families(tmp_path: Path) -> None:
    """When any family in the same plane has an active item,
    one_active_policy='plane' blocks all families in that plane."""
    interp = _make_interpreter(tmp_path)
    task_fam = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, task_fam)
    # task has one_active_policy="plane" (built-in default)
    # Add an active item to task family
    _enqueue_active(paths, task_fam, "task-already-active")
    allowed, reason = interp.one_active_policy_check("task")
    assert not allowed
    assert reason is not None
    assert "plane" in reason


def test_one_active_policy_plane_corrupted_state(tmp_path: Path) -> None:
    """Multiple active items in the same plane is corrupted state and raises."""
    interp = _make_interpreter(tmp_path)
    task_fam = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, task_fam)
    _enqueue_active(paths, task_fam, "task-active-1")
    _enqueue_active(paths, task_fam, "task-active-2")
    with pytest.raises(QueueStateError, match="Corrupted active state"):
        interp.one_active_policy_check("task")


def test_one_active_policy_family_corrupted_state(tmp_path: Path) -> None:
    """Multiple active items in a family with one_active_policy='family' raises."""
    interp = _make_interpreter_with_custom(tmp_path)
    paths = interp._paths
    _bootstrap_family_dirs(paths, CUSTOM_FAMILY)
    _enqueue_active(paths, CUSTOM_FAMILY, "custom-active-1")
    _enqueue_active(paths, CUSTOM_FAMILY, "custom-active-2")
    with pytest.raises(QueueStateError, match="Corrupted active state"):
        interp.one_active_policy_check("custom_fixture")


# ---------------------------------------------------------------------------
# one_active_policy = "lane" and "lineage" raise unsupported errors
# ---------------------------------------------------------------------------

LANE_FAMILY = WorkItemFamilyDefinition(
    schema_version="1.0",
    kind="work_item_family",
    family_id="lane_test",
    plane=Plane.EXECUTION,
    entry_key="lane_test",
    display_name="Lane Test",
    document_kind="lane_test",
    runtime_relative_dir="lane_test",
    file_extension=".md",
    schema_id="lane_test_v1",
    document_adapter_id="lane_test_adapter",
    queue_dirs=WorkItemQueueDirs(
        queue="lane_test/queue",
        active="lane_test/active",
        done="lane_test/done",
        blocked="lane_test/blocked",
    ),
    lifecycle_states=("queue", "active", "done", "blocked"),
    claimable_state="queue",
    active_state="active",
    done_state="done",
    blocked_state="blocked",
    closure_blocking_states=("queue", "active", "blocked"),
    id_field="lane_id",
    created_at_field="created_at",
    lineage_fields=(),
    dependency_field=None,
    one_active_policy="lane",
    sort_policy="created_at_asc",
)

LANE_ADAPTER = WorkItemDocumentAdapterDefinition(
    schema_version="1.0",
    kind="work_item_document_adapter",
    adapter_id="lane_test_adapter",
    schema_id="lane_test_v1",
    supported_file_extensions=(".md",),
    family_ids=("lane_test",),
    can_parse=True,
    can_render=True,
    can_summarize=True,
    supports_dependencies=False,
    supports_lineage=False,
)

LINEAGE_FAMILY = WorkItemFamilyDefinition(
    schema_version="1.0",
    kind="work_item_family",
    family_id="lineage_test",
    plane=Plane.EXECUTION,
    entry_key="lineage_test",
    display_name="Lineage Test",
    document_kind="lineage_test",
    runtime_relative_dir="lineage_test",
    file_extension=".md",
    schema_id="lineage_test_v1",
    document_adapter_id="lineage_test_adapter",
    queue_dirs=WorkItemQueueDirs(
        queue="lineage_test/queue",
        active="lineage_test/active",
        done="lineage_test/done",
        blocked="lineage_test/blocked",
    ),
    lifecycle_states=("queue", "active", "done", "blocked"),
    claimable_state="queue",
    active_state="active",
    done_state="done",
    blocked_state="blocked",
    closure_blocking_states=("queue", "active", "blocked"),
    id_field="lineage_id",
    created_at_field="created_at",
    lineage_fields=(),
    dependency_field=None,
    one_active_policy="lineage",
    sort_policy="created_at_asc",
)

LINEAGE_ADAPTER = WorkItemDocumentAdapterDefinition(
    schema_version="1.0",
    kind="work_item_document_adapter",
    adapter_id="lineage_test_adapter",
    schema_id="lineage_test_v1",
    supported_file_extensions=(".md",),
    family_ids=("lineage_test",),
    can_parse=True,
    can_render=True,
    can_summarize=True,
    supports_dependencies=False,
    supports_lineage=False,
)


def _make_interpreter_with_lane(tmp_path: Path) -> QueueFamilyInterpreter:
    paths = workspace_paths(tmp_path / "workspace")
    paths.runtime_root.mkdir(parents=True, exist_ok=True)
    builtin = load_builtin_workflow_primitives()
    families = (*builtin.work_item_families, LANE_FAMILY)
    adapters = (*builtin.document_adapters, LANE_ADAPTER)
    return QueueFamilyInterpreter(paths, families=families, document_adapters=adapters)


def _make_interpreter_with_lineage(tmp_path: Path) -> QueueFamilyInterpreter:
    paths = workspace_paths(tmp_path / "workspace")
    paths.runtime_root.mkdir(parents=True, exist_ok=True)
    builtin = load_builtin_workflow_primitives()
    families = (*builtin.work_item_families, LINEAGE_FAMILY)
    adapters = (*builtin.document_adapters, LINEAGE_ADAPTER)
    return QueueFamilyInterpreter(paths, families=families, document_adapters=adapters)


def test_one_active_policy_lane_raises_unsupported(tmp_path: Path) -> None:
    """one_active_policy='lane' raises QueueStateError with clear message."""
    interp = _make_interpreter_with_lane(tmp_path)
    _bootstrap_family_dirs(interp._paths, LANE_FAMILY)
    with pytest.raises(QueueStateError, match="lane.*not currently supported"):
        interp.one_active_policy_check("lane_test")


def test_one_active_policy_lineage_raises_unsupported(tmp_path: Path) -> None:
    """one_active_policy='lineage' raises QueueStateError with clear message."""
    interp = _make_interpreter_with_lineage(tmp_path)
    _bootstrap_family_dirs(interp._paths, LINEAGE_FAMILY)
    with pytest.raises(QueueStateError, match="lineage.*not currently supported"):
        interp.one_active_policy_check("lineage_test")


# ---------------------------------------------------------------------------
# claim_next returns None for normal one-active saturation
# ---------------------------------------------------------------------------


def test_claim_next_returns_none_for_one_active_saturation(tmp_path: Path) -> None:
    """claim_next returns None (not raises) when one-active policy blocks."""
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    _enqueue_active(paths, family, "task-active")
    _enqueue_markdown(paths, family, "task-queued")
    claim = interp.claim_next("task")
    assert claim is None


def test_claim_next_raises_for_corrupted_active_state(tmp_path: Path) -> None:
    """claim_next propagates QueueStateError for corrupted active state (>1 active)."""
    interp = _make_interpreter(tmp_path)
    family = interp.family("task")
    paths = interp._paths
    _bootstrap_family_dirs(paths, family)
    _enqueue_active(paths, family, "task-active-1")
    _enqueue_active(paths, family, "task-active-2")
    _enqueue_markdown(paths, family, "task-queued")
    with pytest.raises(QueueStateError, match="Corrupted active state"):
        interp.claim_next("task")


# ---------------------------------------------------------------------------
# Custom family id_field validation with derived label
# ---------------------------------------------------------------------------


def test_custom_family_id_label_derived_from_id_field(tmp_path: Path) -> None:
    """Custom families with id_field not in _FAMILY_ID_LABELS still validate."""
    interp = _make_interpreter_with_custom(tmp_path)
    paths = interp._paths
    _bootstrap_family_dirs(paths, CUSTOM_FAMILY)
    path = _enqueue_markdown(paths, CUSTOM_FAMILY, "custom-valid")
    is_valid, error = interp.validate_work_item_filename("custom_fixture", path)
    assert is_valid
    assert error is None


def test_custom_family_mismatched_filename_quarantined(tmp_path: Path) -> None:
    """Custom markdown docs with mismatched filename and declared ID are quarantined."""
    interp = _make_interpreter_with_custom(tmp_path)
    paths = interp._paths
    _bootstrap_family_dirs(paths, CUSTOM_FAMILY)
    path = _enqueue_markdown(paths, CUSTOM_FAMILY, "custom-mismatch")
    # Rewrite with different ID in content
    path.write_text(
        _make_markdown_doc(CUSTOM_FAMILY, "custom-other"),
        encoding="utf-8",
    )
    is_valid, error = interp.validate_work_item_filename("custom_fixture", path)
    assert not is_valid
    assert "filename stem does not match" in error


# ---------------------------------------------------------------------------
# claim_next_for_family uses generic interpreter path for custom families
# ---------------------------------------------------------------------------


def test_claim_next_for_family_custom_markdown_family(tmp_path: Path) -> None:
    """claim_next_for_family claims from custom markdown family via interpreter."""
    from millrace_ai.workspace.queue_selection import claim_next_for_family

    paths = workspace_paths(tmp_path / "workspace")
    paths.runtime_root.mkdir(parents=True, exist_ok=True)
    builtin = load_builtin_workflow_primitives()
    families = (*builtin.work_item_families, CUSTOM_FAMILY)

    # Set up directories
    _bootstrap_family_dirs(paths, CUSTOM_FAMILY)

    queue_dir = paths.runtime_root / CUSTOM_FAMILY.queue_dirs.queue
    dest = queue_dir / "custom-via-family.md"
    dest.write_text(_make_markdown_doc(CUSTOM_FAMILY, "custom-via-family"), encoding="utf-8")

    claim = claim_next_for_family(paths, "custom_fixture", families=families)

    assert claim is not None
    assert claim.family_id == "custom_fixture"
    assert claim.work_item_id == "custom-via-family"
    assert claim.plane == Plane.EXECUTION
