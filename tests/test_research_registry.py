from __future__ import annotations

from pathlib import Path

from millrace_engine.contracts import PersistedObjectKind, RegistryObjectRef
from millrace_engine.materialization import ArchitectureMaterializer
from millrace_engine.registry import discover_registry_state


def _mode_ref(object_id: str) -> RegistryObjectRef:
    return RegistryObjectRef(kind=PersistedObjectKind.MODE, id=object_id, version="1.0.0")


def test_packaged_research_registry_scaffolds_are_discoverable(tmp_path: Path) -> None:
    state = discover_registry_state(tmp_path)
    packaged_keys = {document.key for document in state.packaged}

    expected_stage_ids = {
        "research.goal-intake",
        "research.objective-profile-sync",
        "research.spec-synthesis",
        "research.spec-interview",
        "research.spec-review",
        "research.taskmaster",
        "research.incident-intake",
        "research.incident-resolve",
        "research.incident-archive",
        "research.audit-intake",
        "research.audit-validate",
        "research.audit-gatekeeper",
    }
    expected_loop_ids = {
        "research.goalspec",
        "research.incident",
        "research.audit",
    }
    expected_mode_ids = {
        "mode.research_goalspec",
        "mode.research_incident",
        "mode.research_audit",
    }

    assert {
        ("registered_stage_kind", object_id, "1.0.0")
        for object_id in expected_stage_ids
    } <= packaged_keys
    assert {("loop_config", object_id, "1.0.0") for object_id in expected_loop_ids} <= packaged_keys
    assert {("mode", object_id, "1.0.0") for object_id in expected_mode_ids} <= packaged_keys


def test_packaged_research_modes_materialize_research_loops(tmp_path: Path) -> None:
    materializer = ArchitectureMaterializer(tmp_path)

    goalspec_mode = materializer.materialize_mode(_mode_ref("mode.research_goalspec"), resolve_assets=False)
    incident_mode = materializer.materialize_mode(_mode_ref("mode.research_incident"), resolve_assets=False)
    audit_mode = materializer.materialize_mode(_mode_ref("mode.research_audit"), resolve_assets=False)

    assert goalspec_mode.research_loop is not None
    assert {
        binding.kind_id for binding in goalspec_mode.research_loop.stage_bindings
    } == {
        "research.goal-intake",
        "research.objective-profile-sync",
        "research.spec-synthesis",
        "research.spec-interview",
        "research.spec-review",
        "research.taskmaster",
    }

    assert incident_mode.research_loop is not None
    assert incident_mode.research_loop.requested_ref == RegistryObjectRef(
        kind=PersistedObjectKind.LOOP_CONFIG,
        id="research.incident",
        version="1.0.0",
    )
    assert {
        binding.kind_id for binding in incident_mode.research_loop.stage_bindings
    } == {
        "research.incident-intake",
        "research.incident-resolve",
        "research.incident-archive",
    }

    assert audit_mode.research_loop is not None
    assert audit_mode.research_loop.requested_ref == RegistryObjectRef(
        kind=PersistedObjectKind.LOOP_CONFIG,
        id="research.audit",
        version="1.0.0",
    )
    assert audit_mode.execution_loop.requested_ref == RegistryObjectRef(
        kind=PersistedObjectKind.LOOP_CONFIG,
        id="execution.standard",
        version="1.0.0",
    )
