"""Public inventory for base-package included workflows."""

from __future__ import annotations

from dataclasses import dataclass

from millrace.workflows import kernel_ping


@dataclass(frozen=True, slots=True)
class IncludedWorkflow:
    workflow_id: str
    workflow_version: str
    display_name: str
    source_module: str
    provenance: str


INCLUDED_WORKFLOW_IDS = ("kernel_ping",)

_KERNEL_PING = IncludedWorkflow(
    workflow_id="kernel_ping",
    workflow_version="0.1",
    display_name="Kernel Ping",
    source_module="millrace.workflows.kernel_ping",
    provenance="base-included-diagnostic",
)


def included_workflows() -> tuple[IncludedWorkflow, ...]:
    return (_KERNEL_PING,)


def included_workflow_source(workflow_id: str) -> dict[str, object]:
    if workflow_id != "kernel_ping":
        raise KeyError(workflow_id)
    return kernel_ping.workflow_source()


__all__ = (
    "INCLUDED_WORKFLOW_IDS",
    "IncludedWorkflow",
    "included_workflow_source",
    "included_workflows",
)
