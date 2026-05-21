from __future__ import annotations

from millrace_ai.assets import (
    SHIPPED_WORK_ITEM_FAMILY_IDS,
    load_builtin_work_item_family_definition,
    load_builtin_workflow_primitives,
)
from millrace_ai.contracts import Plane


def test_blueprint_draft_work_item_family_asset_loads() -> None:
    family = load_builtin_work_item_family_definition("blueprint_draft")

    assert "blueprint_draft" in SHIPPED_WORK_ITEM_FAMILY_IDS
    assert family.family_id == "blueprint_draft"
    assert family.plane is Plane.PLANNING
    assert family.file_extension == ".json"
    assert family.queue_dirs.queue == "blueprints/drafts/queue"
    assert family.queue_dirs.active == "blueprints/drafts/active"
    assert family.queue_dirs.done == "blueprints/drafts/approved"
    assert family.queue_dirs.blocked == "blueprints/drafts/blocked"
    assert family.queue_dirs.canceled == "blueprints/drafts/canceled"
    assert family.done_state == "approved"
    assert family.canceled_state == "canceled"
    assert family.dependency_field == "depends_on_draft_ids"
    assert family.document_adapter_id == "blueprint_draft_markdown_v1"


def test_blueprint_draft_document_adapter_is_packaged() -> None:
    bundle = load_builtin_workflow_primitives()
    adapters = {adapter.adapter_id: adapter for adapter in bundle.document_adapters}

    assert "blueprint_draft" in {family.family_id for family in bundle.work_item_families}
    adapter = adapters["blueprint_draft_markdown_v1"]
    assert adapter.family_ids == ("blueprint_draft",)
    assert adapter.supported_file_extensions == (".json",)
    assert adapter.supports_dependencies is True
    assert adapter.supports_lineage is True
