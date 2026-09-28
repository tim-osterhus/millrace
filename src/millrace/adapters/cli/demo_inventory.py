"""Core-owned demo installation and selected package observations.

Explicit consented setup retains the exact wheels in the active virtual
environment. This is local-operator evidence, not producer authentication.
Inspection and demo execution never acquire evidence or follow metadata URLs.
"""

from __future__ import annotations

import sqlite3
from importlib import metadata
from pathlib import Path
from typing import Any

from millrace.compiler import SelectedRunnerAdapterPolicy, authority_fingerprint
from millrace.contracts.demo import (
    ADAPTER_KIND,
    DESCRIPTOR_SHA256,
    MANIFEST_DIGEST,
    PACKAGE_DIGEST,
    PACKAGE_ID,
    PLAN_FINGERPRINT,
    SOURCE_DIGEST,
    VERSION,
    DemoRefusal,
    serial,
)
from millrace.operator.packages import (
    PackageMutationCommand,
    PackageWorkflowSelectionCommand,
    PackageWorkflowVerifyCommand,
    execute_package_mutation_command,
    execute_package_verify_command,
    execute_package_workflow_selection_command,
    project_current_workflow_package,
)
from millrace.substrate.cas import ContentAddressedByteStore, storage_digest_for_bytes
from millrace.substrate.sqlite import SQLiteRuntimeStore

POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind=ADAPTER_KIND,
    supported_adapter_kinds=frozenset({ADAPTER_KIND}),
    component_bound_adapter_kinds=frozenset({ADAPTER_KIND}),
    default_invalid_adapter_kinds=False,
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)


def artifact_identity(name: str) -> str:
    from millrace.adapters.cli.setup_install_evidence import retained_artifact_identity

    return retained_artifact_identity(name)


def installed_identity() -> dict[str, Any]:
    from millrace.adapters.cli import main

    core = metadata.distribution("millrace-ai")
    if (
        Path(main.__file__).resolve()
        != Path(str(core.locate_file("millrace/adapters/cli/main.py"))).resolve()
    ):
        raise DemoRefusal("core_source_overlay")
    if [
        (e.name, e.value) for e in core.entry_points if e.group == "console_scripts"
    ] != [("millrace", "millrace.adapters.cli.main:cli")]:
        raise DemoRefusal("command_declaration_mismatch")
    return {
        "command": "Core.demo.command.v1",
        "core_version": VERSION,
        "plus_version": VERSION,
        "core_artifact": artifact_identity("millrace-ai"),
        "plus_artifact": artifact_identity("millrace-plus"),
        "source_digest": SOURCE_DIGEST,
        "manifest_digest": MANIFEST_DIGEST,
        "package_digest": PACKAGE_DIGEST,
        "plan_fingerprint": PLAN_FINGERPRINT,
        "component_descriptor": DESCRIPTOR_SHA256,
    }


def import_select(
    store: SQLiteRuntimeStore, cas: ContentAddressedByteStore
) -> tuple[Any, dict[str, Any]]:
    for command in (
        PackageMutationCommand(
            "demo-import",
            "package.import_installed",
            "demo",
            installed_distribution_name="millrace-plus",
        ),
        PackageMutationCommand(
            "demo-enable",
            "package.enable",
            "demo",
            package_id=PACKAGE_ID,
            package_version=VERSION,
        ),
    ):
        result = execute_package_mutation_command(store, cas, command)
        if result.outcome != "succeeded":
            raise DemoRefusal("package_" + command.operation_id + "_failed")
    verify = execute_package_verify_command(
        store,
        cas,
        PackageWorkflowVerifyCommand(
            "demo-verify",
            "demo",
            PACKAGE_ID,
            VERSION,
            "demo",
            "0.1",
            expected_manifest_digest=MANIFEST_DIGEST,
            expected_package_digest=PACKAGE_DIGEST,
            selected_runner_policy=POLICY,
        ),
    )
    if not verify.plan_ready:
        raise DemoRefusal("demo_package_verification_failed")
    selected = execute_package_workflow_selection_command(
        store,
        cas,
        PackageWorkflowSelectionCommand(
            "demo-select",
            "demo",
            PACKAGE_ID,
            VERSION,
            "demo",
            "0.1",
            expected_manifest_digest=MANIFEST_DIGEST,
            expected_package_digest=PACKAGE_DIGEST,
            selected_runner_policy=POLICY,
        ),
    )
    plan = selected.plan
    if plan is None or authority_fingerprint(plan) != PLAN_FINGERPRINT:
        raise DemoRefusal("demo_selected_authority_mismatch")
    pin = plan.runner_bindings[0].component_pin
    if pin is None or pin.descriptor_sha256 != DESCRIPTOR_SHA256:
        raise DemoRefusal("demo_component_mismatch")
    projection = current_package(store, cas)
    return plan, projection


def current_package(
    store: SQLiteRuntimeStore, cas: ContentAddressedByteStore
) -> dict[str, Any]:
    package = project_current_workflow_package(store, cas, PACKAGE_ID, VERSION)
    if (
        package is None
        or not package.selectable
        or package.package_digest != PACKAGE_DIGEST
        or package.manifest_digest != MANIFEST_DIGEST
        or package.provenance.source_digest != SOURCE_DIGEST
    ):
        raise DemoRefusal("demo_current_package_mismatch")
    return {**serial(package), **serial(package.provenance)}


class _MemoryCAS(ContentAddressedByteStore):
    """Read-only setup compilation uses no on-disk workspace or registry."""

    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def put_bytes(self, payload: bytes) -> str:
        key = storage_digest_for_bytes(payload)
        self.values[key] = payload
        return key

    def get_bytes(self, digest: str) -> bytes:
        return self.values[digest]


def planned_package() -> dict[str, Any]:
    from millrace.substrate._sqlite_schema import (
        configure_connection,
        initialize_schema,
    )

    connection = sqlite3.connect(":memory:")
    paths = ("/planned-demo", "/planned-demo/runtime.sqlite3", "/planned-demo/cas")
    try:
        configure_connection(connection)
        initialize_schema(connection, paths)
        _, projection = import_select(
            SQLiteRuntimeStore(connection, paths), _MemoryCAS()
        )
        return projection
    finally:
        connection.close()
