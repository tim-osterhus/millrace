from __future__ import annotations

import importlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.architecture import WorkItemDocumentAdapterDefinition, WorkItemFamilyDefinition
from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import (
    ClosureTargetState,
    Plane,
    TaskDocument,
)
from millrace_ai.doctor import run_workspace_doctor
from millrace_ai.extensions.builtin.blueprint.contracts import (
    BlueprintDraftDocument,
    BlueprintManifestDocument,
)
from millrace_ai.extensions.builtin.blueprint.state import enqueue_blueprint_draft
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.runtime_lock import acquire_runtime_ownership_lock
from millrace_ai.work_documents import render_work_document
from millrace_ai.workspace.arbiter_state import save_closure_target_state

NOW = datetime(2026, 4, 15, tzinfo=timezone.utc)


def _bootstrap(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _copy_assets(tmp_path: Path) -> Path:
    source_assets = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"
    destination = tmp_path / "assets"
    shutil.copytree(source_assets, destination)
    return destination


def _blueprint_manifest_doc(
    manifest_id: str = "manifest-blueprint-001",
    *,
    root_spec_id: str = "spec-blueprint-001",
    root_idea_id: str = "idea-blueprint-001",
    draft_ids: tuple[str, ...] = ("draft-blueprint-001",),
    spec_summary: str = "Blueprint manifest fixture.",
) -> BlueprintManifestDocument:
    return BlueprintManifestDocument(
        manifest_id=manifest_id,
        root_spec_id=root_spec_id,
        root_idea_id=root_idea_id,
        source_work_item_kind="spec",
        source_work_item_id=root_spec_id,
        source_spec_id=root_spec_id,
        draft_ids=draft_ids,
        draft_count=len(draft_ids),
        spec_summary=spec_summary,
        decomposition_strategy="Split the work into deterministic test fixtures.",
        global_acceptance_intent=("Doctor reports malformed Blueprint manifest state.",),
        references=("tests/workspace/test_doctor.py",),
        created_at=NOW,
    )


def _blueprint_draft_doc(
    draft_id: str = "draft-blueprint-001",
    *,
    manifest_id: str = "manifest-blueprint-001",
    root_spec_id: str = "spec-blueprint-001",
    root_idea_id: str = "idea-blueprint-001",
) -> BlueprintDraftDocument:
    return BlueprintDraftDocument(
        draft_id=draft_id,
        manifest_id=manifest_id,
        root_spec_id=root_spec_id,
        root_idea_id=root_idea_id,
        source_spec_id=root_spec_id,
        draft_index=1,
        title=f"Blueprint Draft {draft_id}",
        summary="Blueprint draft fixture.",
        target_paths=("src/millrace_ai/doctor.py",),
        acceptance_intent=("Doctor reports Blueprint manifest diagnostics.",),
        context_excerpt="Doctor Blueprint manifest diagnostic fixture.",
        current_revision=0,
        references=("tests/workspace/test_doctor.py",),
        created_at=NOW,
    )


def _write_blueprint_manifest_file(
    paths,
    filename_stem: str,
    manifest: BlueprintManifestDocument,
) -> Path:
    path = paths.runtime_root / "blueprints" / "manifests" / f"{filename_stem}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def _write_blueprint_draft_file(
    paths,
    state: str,
    draft: BlueprintDraftDocument,
) -> Path:
    path = paths.runtime_root / "blueprints" / "drafts" / state / f"{draft.draft_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(draft.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def _persist_custom_json_family(paths) -> WorkItemFamilyDefinition:
    family = WorkItemFamilyDefinition(
        family_id="custom_review",
        plane=Plane.PLANNING,
        entry_key="custom_review",
        display_name="Custom Review",
        document_kind="custom_review",
        runtime_relative_dir="custom/reviews",
        file_extension=".json",
        schema_id="custom_review_document_v1",
        document_adapter_id="custom_review_json_v1",
        queue_dirs={
            "queue": "custom/reviews/queue",
            "active": "custom/reviews/active",
            "done": "custom/reviews/done",
            "blocked": "custom/reviews/blocked",
            "canceled": "custom/reviews/canceled",
        },
        lifecycle_states=("queue", "active", "done", "blocked", "canceled"),
        claimable_state="queue",
        active_state="active",
        done_state="done",
        blocked_state="blocked",
        canceled_state="canceled",
        closure_blocking_states=("queue", "active", "blocked"),
        default_entry_key="custom_review",
        id_field="custom_id",
        created_at_field="created_at",
        lineage_fields=("root_spec_id",),
        operator_capabilities=("cancel", "inspect"),
    )
    adapter = WorkItemDocumentAdapterDefinition(
        adapter_id="custom_review_json_v1",
        schema_id="custom_review_document_v1",
        supported_file_extensions=(".json",),
        family_ids=("custom_review",),
        can_parse=True,
        can_render=True,
        can_summarize=True,
        supports_dependencies=False,
        supports_lineage=True,
    )
    outcome = compile_and_persist_workspace_plan(
        paths.root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.active_plan is not None
    updated = outcome.active_plan.model_copy(
        update={
            "work_item_families_by_id": {
                **outcome.active_plan.work_item_families_by_id,
                family.family_id: family,
            },
            "document_adapters_by_id": {
                **outcome.active_plan.document_adapters_by_id,
                adapter.adapter_id: adapter,
            },
        }
    )
    (paths.state_dir / "compiled_plan.json").write_text(
        updated.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return family


def _persist_custom_markdown_family(paths) -> WorkItemFamilyDefinition:
    family = WorkItemFamilyDefinition(
        family_id="custom_markdown_review",
        plane=Plane.PLANNING,
        entry_key="custom_markdown_review",
        display_name="Custom Markdown Review",
        document_kind="custom_markdown_review",
        runtime_relative_dir="custom/markdown-reviews",
        file_extension=".md",
        schema_id="custom_markdown_review_document_v1",
        document_adapter_id="custom_markdown_review_v1",
        queue_dirs={
            "queue": "custom/markdown-reviews/queue",
            "active": "custom/markdown-reviews/active",
            "done": "custom/markdown-reviews/done",
            "blocked": "custom/markdown-reviews/blocked",
            "canceled": "custom/markdown-reviews/canceled",
        },
        lifecycle_states=("queue", "active", "done", "blocked", "canceled"),
        claimable_state="queue",
        active_state="active",
        done_state="done",
        blocked_state="blocked",
        canceled_state="canceled",
        closure_blocking_states=("queue", "active", "blocked"),
        default_entry_key="custom_markdown_review",
        id_field="custom_id",
        created_at_field="created_at",
        lineage_fields=("root_spec_id",),
        operator_capabilities=("cancel", "inspect"),
    )
    adapter = WorkItemDocumentAdapterDefinition(
        adapter_id="custom_markdown_review_v1",
        schema_id="custom_markdown_review_document_v1",
        supported_file_extensions=(".md",),
        family_ids=("custom_markdown_review",),
        can_parse=True,
        can_render=True,
        can_summarize=True,
        supports_dependencies=False,
        supports_lineage=True,
    )
    outcome = compile_and_persist_workspace_plan(
        paths.root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.active_plan is not None
    updated = outcome.active_plan.model_copy(
        update={
            "work_item_families_by_id": {
                **outcome.active_plan.work_item_families_by_id,
                family.family_id: family,
            },
            "document_adapters_by_id": {
                **outcome.active_plan.document_adapters_by_id,
                adapter.adapter_id: adapter,
            },
        }
    )
    (paths.state_dir / "compiled_plan.json").write_text(
        updated.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return family


def test_workspace_package_exposes_support_module_facades() -> None:
    workspace_package = importlib.import_module("millrace_ai.workspace")
    runtime_lock_module = importlib.import_module("millrace_ai.runtime_lock")
    work_documents_module = importlib.import_module("millrace_ai.work_documents")

    assert hasattr(workspace_package, "workspace_paths")
    assert runtime_lock_module.acquire_runtime_ownership_lock.__module__ == (
        "millrace_ai.workspace.runtime_lock"
    )
    assert work_documents_module.render_work_document.__module__ == (
        "millrace_ai.workspace.work_documents"
    )


def test_doctor_passes_for_bootstrapped_workspace(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)

    report = run_workspace_doctor(paths)

    assert report.ok is True
    assert report.errors == ()


def test_doctor_flags_invalid_status_and_unparseable_queue_artifact(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    paths.execution_status_file.write_text("RUNNING\n", encoding="utf-8")
    (paths.tasks_queue_dir / "bad.md").write_text("# Bad task\nnot a valid task document\n", encoding="utf-8")

    report = run_workspace_doctor(paths)

    assert report.ok is False
    error_codes = {item.code for item in report.errors}
    assert "execution_status_invalid" in error_codes
    assert "queue_artifact_invalid" in error_codes


def test_doctor_flags_queue_filename_and_document_id_mismatch(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    mismatch_doc = TaskDocument(
        task_id="task-mismatch",
        title="Task mismatch",
        summary="mismatched filename and frontmatter id",
        target_paths=["millrace/runtime.py"],
        acceptance=["doctor flags mismatch"],
        required_checks=["uv run pytest tests/workspace/test_doctor.py -q"],
        references=["lab/specs/pending/2026-04-15-millrace-recheck-remediation-task-breakdown.md"],
        risk=["queue routing drift"],
        created_at=NOW,
        created_by="tests",
    )
    (paths.tasks_queue_dir / "task-alias.md").write_text(
        render_work_document(mismatch_doc),
        encoding="utf-8",
    )

    report = run_workspace_doctor(paths)

    assert report.ok is False
    mismatch_errors = [item for item in report.errors if item.code == "queue_artifact_invalid"]
    assert mismatch_errors
    assert any("filename stem does not match task_id" in item.message for item in mismatch_errors)


def test_doctor_validates_blueprint_draft_queue_parseability(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    invalid_draft = paths.runtime_root / "blueprints" / "drafts" / "queue" / "draft-bad.json"
    invalid_draft.parent.mkdir(parents=True, exist_ok=True)
    invalid_draft.write_text("{not-valid-json", encoding="utf-8")

    report = run_workspace_doctor(paths)

    assert report.ok is False
    assert any(
        item.code == "queue_artifact_invalid"
        and "blueprint_draft" in item.message
        and item.path == invalid_draft
        for item in report.errors
    )


def test_doctor_flags_legacy_root_keyed_blueprint_manifest(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    manifest = _blueprint_manifest_doc()
    legacy_path = _write_blueprint_manifest_file(paths, manifest.root_spec_id, manifest)
    _write_blueprint_draft_file(paths, "queue", _blueprint_draft_doc())

    report = run_workspace_doctor(paths)

    assert report.ok is False
    assert any(
        item.code == "blueprint_manifest_legacy_root_keyed"
        and "manifest-blueprint-001" in item.message
        and "spec-blueprint-001" in item.message
        and item.path == legacy_path
        for item in report.errors
    )


def test_doctor_flags_unresolved_blueprint_draft_manifest_reference(
    tmp_path: Path,
) -> None:
    paths = _bootstrap(tmp_path)
    draft_path = _write_blueprint_draft_file(
        paths,
        "queue",
        _blueprint_draft_doc(manifest_id="manifest-missing"),
    )

    report = run_workspace_doctor(paths)

    assert report.ok is False
    assert any(
        item.code == "blueprint_draft_manifest_unresolved"
        and "manifest-missing" in item.message
        and "draft-blueprint-001" in item.message
        and item.path == draft_path
        for item in report.errors
    )


def test_doctor_flags_blueprint_manifest_draft_ids_missing_from_lifecycle(
    tmp_path: Path,
) -> None:
    paths = _bootstrap(tmp_path)
    manifest = _blueprint_manifest_doc(draft_ids=("draft-missing",))
    manifest_path = _write_blueprint_manifest_file(paths, manifest.manifest_id, manifest)

    report = run_workspace_doctor(paths)

    assert report.ok is False
    assert any(
        item.code == "blueprint_manifest_draft_missing"
        and "manifest-blueprint-001" in item.message
        and "draft-missing" in item.message
        and item.path == manifest_path
        for item in report.errors
    )


def test_doctor_requires_blueprint_manifest_draft_ids_under_same_manifest(
    tmp_path: Path,
) -> None:
    paths = _bootstrap(tmp_path)
    first_manifest = _blueprint_manifest_doc(
        manifest_id="manifest-blueprint-001",
        draft_ids=("draft-shared",),
    )
    second_manifest = _blueprint_manifest_doc(
        manifest_id="manifest-blueprint-002",
        draft_ids=("draft-shared",),
    )
    first_manifest_path = _write_blueprint_manifest_file(
        paths, first_manifest.manifest_id, first_manifest
    )
    _write_blueprint_manifest_file(paths, second_manifest.manifest_id, second_manifest)
    _write_blueprint_draft_file(
        paths,
        "queue",
        _blueprint_draft_doc(
            draft_id="draft-shared",
            manifest_id="manifest-blueprint-002",
        ),
    )

    report = run_workspace_doctor(paths)

    assert report.ok is False
    assert any(
        item.code == "blueprint_manifest_draft_missing"
        and "manifest-blueprint-001" in item.message
        and "draft-shared" in item.message
        and item.path == first_manifest_path
        for item in report.errors
    )


def test_doctor_flags_non_equivalent_blueprint_manifests_with_same_id(
    tmp_path: Path,
) -> None:
    paths = _bootstrap(tmp_path)
    manifest = _blueprint_manifest_doc()
    _write_blueprint_manifest_file(paths, manifest.manifest_id, manifest)
    shadow_path = _write_blueprint_manifest_file(
        paths,
        "manifest-blueprint-shadow",
        manifest.model_copy(update={"spec_summary": "Divergent manifest content."}),
    )
    _write_blueprint_draft_file(paths, "queue", _blueprint_draft_doc())

    report = run_workspace_doctor(paths)

    assert report.ok is False
    assert any(
        item.code == "blueprint_manifest_duplicate"
        and "manifest-blueprint-001" in item.message
        and "non-equivalent" in item.message
        and item.path == shadow_path
        for item in report.errors
    )


def test_doctor_flags_blueprint_manifest_draft_root_lineage_mismatch(
    tmp_path: Path,
) -> None:
    paths = _bootstrap(tmp_path)
    manifest = _blueprint_manifest_doc(root_spec_id="spec-blueprint-001")
    _write_blueprint_manifest_file(paths, manifest.manifest_id, manifest)
    draft_path = _write_blueprint_draft_file(
        paths,
        "queue",
        _blueprint_draft_doc(root_spec_id="spec-blueprint-other"),
    )

    report = run_workspace_doctor(paths)

    assert report.ok is False
    assert any(
        item.code == "blueprint_manifest_draft_lineage_mismatch"
        and "manifest-blueprint-001" in item.message
        and "spec-blueprint-001" in item.message
        and "spec-blueprint-other" in item.message
        and item.path == draft_path
        for item in report.errors
    )


def test_doctor_allows_same_root_distinct_blueprint_manifests(
    tmp_path: Path,
) -> None:
    paths = _bootstrap(tmp_path)
    first_manifest = _blueprint_manifest_doc(
        manifest_id="manifest-blueprint-001",
        draft_ids=("draft-blueprint-001",),
    )
    second_manifest = _blueprint_manifest_doc(
        manifest_id="manifest-blueprint-002",
        draft_ids=("draft-blueprint-002",),
        spec_summary="Second same-root decomposition.",
    )
    _write_blueprint_manifest_file(paths, first_manifest.manifest_id, first_manifest)
    _write_blueprint_manifest_file(paths, second_manifest.manifest_id, second_manifest)
    _write_blueprint_draft_file(
        paths,
        "queue",
        _blueprint_draft_doc(
            draft_id="draft-blueprint-001",
            manifest_id="manifest-blueprint-001",
        ),
    )
    _write_blueprint_draft_file(
        paths,
        "queue",
        _blueprint_draft_doc(
            draft_id="draft-blueprint-002",
            manifest_id="manifest-blueprint-002",
        ),
    )

    report = run_workspace_doctor(paths)

    assert report.ok is True
    assert not [
        item for item in report.errors if item.code.startswith("blueprint_manifest")
    ]


def test_doctor_accepts_custom_json_family_with_declared_adapter(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    family = _persist_custom_json_family(paths)
    queue_dir = paths.runtime_root / family.queue_dirs.queue
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "custom-001.json").write_text(
        json.dumps(
            {
                "custom_id": "custom-001",
                "root_spec_id": "spec-custom-001",
                "created_at": NOW.isoformat(),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = run_workspace_doctor(paths)

    assert report.ok is True
    assert not [item for item in report.errors if item.code == "queue_artifact_invalid"]


def test_doctor_accepts_custom_markdown_family_with_declared_id_field(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    family = _persist_custom_markdown_family(paths)
    queue_dir = paths.runtime_root / family.queue_dirs.queue
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "custom-001.md").write_text(
        "\n".join(
            [
                "# Custom Review",
                "",
                "Custom-ID: custom-001",
                "Root-Spec-ID: spec-custom-001",
                f"Created-At: {NOW.isoformat()}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    report = run_workspace_doctor(paths)

    assert report.ok is True
    assert not [item for item in report.errors if item.code == "queue_artifact_invalid"]


def test_doctor_flags_duplicate_task_lifecycle_state(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    document = TaskDocument(
        task_id="task-duplicate",
        title="Task duplicate",
        summary="same logical task in two lifecycle states",
        root_idea_id="idea-001",
        root_spec_id="spec-root-001",
        target_paths=["millrace/runtime.py"],
        acceptance=["doctor flags duplicate lifecycle state"],
        required_checks=["uv run --extra dev python -m pytest tests/workspace/test_doctor.py -q"],
        references=["lab/specs/pending/2026-04-29-millrace-task-lifecycle-duplicate-reconciliation.md"],
        risk=["closure readiness drift"],
        created_at=NOW,
        created_by="tests",
    )
    (paths.tasks_blocked_dir / "task-duplicate.md").write_text(
        render_work_document(document),
        encoding="utf-8",
    )
    (paths.tasks_done_dir / "task-duplicate.md").write_text(
        render_work_document(document.model_copy(update={"summary": "completed continuation"})),
        encoding="utf-8",
    )

    report = run_workspace_doctor(paths)

    assert report.ok is False
    duplicate_errors = [item for item in report.errors if item.code == "duplicate_task_lifecycle_state"]
    assert duplicate_errors
    assert any("task-duplicate" in item.message for item in duplicate_errors)
    assert any("blocked" in item.message and "done" in item.message for item in duplicate_errors)


def test_doctor_flags_closure_lineage_drift(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    canonical_root = "idea-idea-2026-04-27-browser-local-qa"
    stale_root = "idea-2026-04-27-browser-local-qa"
    save_closure_target_state(
        paths,
        ClosureTargetState(
            root_spec_id=canonical_root,
            root_idea_id=canonical_root,
            root_spec_path=f"millrace-agents/arbiter/contracts/root-specs/{canonical_root}.md",
            root_idea_path=f"millrace-agents/arbiter/contracts/ideas/{canonical_root}.md",
            rubric_path=f"millrace-agents/arbiter/rubrics/{canonical_root}.md",
            closure_open=True,
            closure_blocked_by_lineage_work=False,
            blocking_work_ids=(),
            opened_at=NOW,
        ),
    )
    QueueStore(paths).enqueue_task(
        TaskDocument(
            task_id="task-browser-local-qa",
            title="Task browser local qa",
            summary="drifted task",
            root_idea_id=canonical_root,
            root_spec_id=stale_root,
            spec_id=stale_root,
            target_paths=["src/millrace_ai/runtime.py"],
            acceptance=["doctor flags drift"],
            required_checks=["uv run --extra dev python -m pytest tests/workspace/test_doctor.py -q"],
            references=["lab/misc/millrace-failure-mode.md"],
            risk=["closure loop"],
            created_at=NOW,
            created_by="tests",
        )
    )

    report = run_workspace_doctor(paths)

    assert report.ok is False
    drift_errors = [item for item in report.errors if item.code == "closure_lineage_drift"]
    assert drift_errors
    assert any("task-browser-local-qa" in item.message for item in drift_errors)
    assert any(stale_root in item.message and canonical_root in item.message for item in drift_errors)


def test_doctor_reports_missing_closure_root_source(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    target_path = paths.arbiter_targets_dir / "spec-root-001.json"
    target_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "kind": "closure_target_state",
                "root_spec_id": "spec-root-001",
                "root_spec_path": "millrace-agents/arbiter/contracts/root-specs/spec-root-001.md",
                "rubric_path": "millrace-agents/arbiter/rubrics/spec-root-001.md",
                "closure_open": True,
                "opened_at": NOW.isoformat(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    report = run_workspace_doctor(paths)

    assert report.ok is False
    assert any(item.code == "closure_root_source_missing" for item in report.errors)


def test_doctor_reports_unsupported_closure_root_source_kind(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    (paths.arbiter_root_spec_contracts_dir / "spec-root-001.md").write_text(
        "# Root Spec\n",
        encoding="utf-8",
    )
    root_source_path = paths.arbiter_root_source_contracts_dir / "custom" / "custom-root-001.md"
    root_source_path.parent.mkdir(parents=True, exist_ok=True)
    root_source_path.write_text(
        "# Custom Root Source\n",
        encoding="utf-8",
    )
    save_closure_target_state(
        paths,
        ClosureTargetState(
            root_spec_id="spec-root-001",
            root_source={
                "kind": "custom",
                "id": "custom-root-001",
                "path": "millrace-agents/arbiter/contracts/root-sources/custom/custom-root-001.md",
            },
            root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-root-001.md",
            rubric_path="millrace-agents/arbiter/rubrics/spec-root-001.md",
            closure_open=True,
            opened_at=NOW,
        ),
    )

    report = run_workspace_doctor(paths)

    assert report.ok is False
    assert any(item.code == "closure_root_source_kind_unsupported" for item in report.errors)


def test_doctor_reports_closure_root_source_legacy_mismatch(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    (paths.arbiter_root_spec_contracts_dir / "spec-root-001.md").write_text(
        "# Root Spec\n",
        encoding="utf-8",
    )
    root_source_path = paths.arbiter_root_source_contracts_dir / "probe" / "probe-root-001.md"
    root_source_path.parent.mkdir(parents=True, exist_ok=True)
    root_source_path.write_text(
        "# Probe Root Source\n",
        encoding="utf-8",
    )
    save_closure_target_state(
        paths,
        ClosureTargetState(
            root_spec_id="spec-root-001",
            root_source={
                "kind": "probe",
                "id": "probe-root-001",
                "path": "millrace-agents/arbiter/contracts/root-sources/probe/probe-root-001.md",
            },
            root_idea_id="idea-001",
            root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-root-001.md",
            root_idea_path="millrace-agents/arbiter/contracts/ideas/idea-001.md",
            rubric_path="millrace-agents/arbiter/rubrics/spec-root-001.md",
            closure_open=True,
            opened_at=NOW,
        ),
    )

    report = run_workspace_doctor(paths)

    assert report.ok is False
    assert any(item.code == "closure_root_source_legacy_mismatch" for item in report.errors)


def test_doctor_warns_stopped_daemon_with_open_closure_and_graph_backlog(
    tmp_path: Path,
) -> None:
    paths = _bootstrap(tmp_path)
    (paths.arbiter_root_spec_contracts_dir / "spec-blueprint-001.md").write_text(
        "# Root Spec\n",
        encoding="utf-8",
    )
    (paths.arbiter_idea_contracts_dir / "idea-blueprint-001.md").write_text(
        "# Root Idea\n",
        encoding="utf-8",
    )
    save_closure_target_state(
        paths,
        ClosureTargetState(
            root_spec_id="spec-blueprint-001",
            root_idea_id="idea-blueprint-001",
            root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-blueprint-001.md",
            root_idea_path="millrace-agents/arbiter/contracts/ideas/idea-blueprint-001.md",
            rubric_path="millrace-agents/arbiter/rubrics/spec-blueprint-001.md",
            closure_open=True,
            opened_at=NOW,
        ),
    )
    _write_blueprint_manifest_file(paths, "manifest-blueprint-001", _blueprint_manifest_doc())
    enqueue_blueprint_draft(
        paths,
        BlueprintDraftDocument(
            draft_id="draft-blueprint-001",
            manifest_id="manifest-blueprint-001",
            root_spec_id="spec-blueprint-001",
            root_idea_id="idea-blueprint-001",
            source_spec_id="spec-blueprint-001",
            draft_index=1,
            title="Blueprint Draft 001",
            summary="Pending graph-owned closure work.",
            target_paths=("src/millrace_ai/doctor.py",),
            acceptance_intent=("Doctor warns about restart-recoverable graph backlog.",),
            context_excerpt="Doctor restart warning fixture.",
            current_revision=0,
            created_at=NOW,
        ),
    )

    report = run_workspace_doctor(paths)

    assert report.ok is True
    assert any(
        item.code == "daemon_stopped_with_open_graph_work"
        and "blueprint_draft:draft-blueprint-001" in item.message
        and "root_spec_id=spec-blueprint-001" in item.message
        for item in report.warnings
    )


def test_doctor_flags_snapshot_reconciliation_problems(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    compile_and_persist_workspace_plan(
        paths.root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )

    snapshot_payload = json.loads(paths.runtime_snapshot_file.read_text(encoding="utf-8"))
    snapshot_payload.update(
        {
            "process_running": False,
            "active_plane": "execution",
            "active_stage": "checker",
            "active_run_id": "run-001",
            "active_work_item_kind": "task",
            "active_work_item_id": "task-001",
            "active_since": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
        }
    )
    paths.runtime_snapshot_file.write_text(
        json.dumps(snapshot_payload, indent=2) + "\n",
        encoding="utf-8",
    )
    paths.execution_status_file.write_text("### CHECKER_RUNNING\n", encoding="utf-8")

    report = run_workspace_doctor(paths)

    assert report.ok is False
    assert any(
        item.code == "snapshot_reconciliation_signal" and "stale_active_ownership" in item.message
        for item in report.errors
    )


def test_doctor_accepts_compiled_plan_only_planning_node_outcome(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    outcome = compile_and_persist_workspace_plan(
        paths.root,
        config=RuntimeConfig(),
        requested_mode_id="blueprint_" "codex",
    )
    assert outcome.active_plan is not None

    snapshot_payload = json.loads(paths.runtime_snapshot_file.read_text(encoding="utf-8"))
    snapshot_payload.update(
        {
            "process_running": True,
            "active_plane": "planning",
            "active_stage": "manager",
            "active_node_id": "manager_blueprint",
            "active_stage_kind_id": "manager_blueprint",
            "active_run_id": "run-blueprint",
            "active_work_item_family_id": "spec",
            "active_work_item_kind": "spec",
            "active_work_item_id": "spec-blueprint-001",
            "active_since": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
        }
    )
    paths.runtime_snapshot_file.write_text(
        json.dumps(snapshot_payload, indent=2) + "\n",
        encoding="utf-8",
    )
    paths.planning_status_file.write_text(
        "### MANAGER_BLUEPRINT_COMPLETE\n",
        encoding="utf-8",
    )

    report = run_workspace_doctor(paths)

    assert not any(
        item.code == "snapshot_reconciliation_signal"
        and "impossible_planning_status_marker" in item.message
        for item in report.errors
    )


def test_doctor_uses_compiled_plan_for_stage_missing_from_static_marker_maps(
    tmp_path: Path,
) -> None:
    paths = _bootstrap(tmp_path)
    outcome = compile_and_persist_workspace_plan(
        paths.root,
        config=RuntimeConfig(),
        requested_mode_id="default_codex_integrated",
    )
    assert outcome.active_plan is not None

    snapshot_payload = json.loads(paths.runtime_snapshot_file.read_text(encoding="utf-8"))
    snapshot_payload.update(
        {
            "process_running": True,
            "active_plane": "execution",
            "active_stage": "integrator",
            "active_node_id": "integrator",
            "active_stage_kind_id": "integrator",
            "active_run_id": "run-integrator",
            "active_work_item_family_id": "task",
            "active_work_item_kind": "task",
            "active_work_item_id": "task-001",
            "active_since": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
        }
    )
    paths.runtime_snapshot_file.write_text(
        json.dumps(snapshot_payload, indent=2) + "\n",
        encoding="utf-8",
    )
    paths.execution_status_file.write_text(
        "### INTEGRATION_COMPLETE\n",
        encoding="utf-8",
    )

    report = run_workspace_doctor(paths)

    assert not any(
        item.code == "snapshot_reconciliation_signal"
        and "impossible_execution_status_marker" in item.message
        for item in report.errors
    )


def test_doctor_flags_invalid_mode_assets_deterministically(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    assets_root = _copy_assets(tmp_path)

    broken_mode_path = assets_root / "modes" / "lad_codex.json"
    broken_mode_path.write_text("{not-valid-json", encoding="utf-8")

    report = run_workspace_doctor(paths, assets_root=assets_root)

    assert report.ok is False
    assert any(item.code == "mode_definition_invalid" for item in report.errors)


def test_doctor_warns_when_resolved_runner_binary_is_unavailable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    paths = _bootstrap(tmp_path)
    paths.runtime_root.joinpath("millrace.toml").write_text(
        "\n".join(
            [
                "[runtime]",
                'default_mode = "default_pi"',
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("millrace_ai.doctor.shutil.which", lambda command: None)

    report = run_workspace_doctor(paths)

    assert any(item.code == "runner_binary_unavailable" for item in report.warnings)
    assert any("pi_rpc" in item.message for item in report.warnings)


def test_doctor_validates_resolved_learning_stage_runner_posture(tmp_path: Path) -> None:
    assets_root = _copy_assets(tmp_path)
    local_mode_path = assets_root / "modes" / "learning_local.json"
    payload = json.loads((assets_root / "modes" / "learning_lad_codex.json").read_text(encoding="utf-8"))
    payload["mode_id"] = "learning_local"
    for learning_stage in ("analyst", "professor", "curator"):
        payload["stage_runner_bindings"].pop(learning_stage)
    local_mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    paths = _bootstrap(tmp_path)
    shutil.copytree(assets_root, paths.runtime_root, dirs_exist_ok=True)
    paths.runtime_root.joinpath("millrace.toml").write_text(
        "\n".join(
            [
                "[runtime]",
                'default_mode = "learning_local"',
                "",
                "[runners]",
                'default_runner = "unknown_learning_runner"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = run_workspace_doctor(paths)

    assert report.ok is False
    assert any(
        item.code == "configured_runner_unknown" and "unknown_learning_runner" in item.message
        for item in report.errors
    )


def test_doctor_reports_active_runtime_ownership_lock_health(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=os.getpid(),
        owner_session_id="doctor-active",
    )

    report = run_workspace_doctor(paths)

    assert report.ok is True
    assert any(item.code == "runtime_ownership_lock_active" for item in report.warnings)


def test_doctor_flags_stale_runtime_ownership_lock(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=999_999_999,
        owner_session_id="doctor-stale",
    )

    report = run_workspace_doctor(paths)

    assert report.ok is False
    assert any(item.code == "runtime_ownership_lock_stale" for item in report.errors)


def test_doctor_flags_invalid_runtime_ownership_lock_payload(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    paths.runtime_lock_file.write_text("{not-valid-json", encoding="utf-8")

    report = run_workspace_doctor(paths)

    assert report.ok is False
    assert any(item.code == "runtime_ownership_lock_invalid" for item in report.errors)


def test_doctor_flags_missing_baseline_manifest(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    paths.baseline_manifest_file.unlink()

    report = run_workspace_doctor(paths)

    assert report.ok is False
    assert any(item.code == "baseline_manifest_missing" for item in report.errors)


def test_doctor_flags_invalid_baseline_manifest_schema(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    paths.baseline_manifest_file.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "manifest_id": "bad",
                "seed_package_version": "0.0.0",
                "entries": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = run_workspace_doctor(paths)

    assert report.ok is False
    assert any(item.code == "baseline_manifest_invalid" for item in report.errors)


def test_doctor_flags_missing_manifest_tracked_managed_file(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    (paths.runtime_root / "entrypoints" / "execution" / "lad_builder.md").unlink()

    report = run_workspace_doctor(paths)

    assert report.ok is False
    assert any(item.code == "baseline_manifest_managed_file_missing" for item in report.errors)
