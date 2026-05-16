from __future__ import annotations

import json
import shutil
from pathlib import Path

from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import CapabilityDecisionState, Plane
from millrace_ai.paths import bootstrap_workspace


def _copy_builtin_assets(tmp_path: Path) -> Path:
    assets_root = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"
    copied_root = tmp_path / "assets"
    shutil.copytree(assets_root, copied_root)
    return copied_root


def test_compile_seals_default_execution_grants_in_node_plan(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="default_codex",
    )

    assert outcome.active_plan is not None
    builder = next(node for node in outcome.active_plan.execution_graph.nodes if node.node_id == "builder")
    grant_ids = {grant.capability_id for grant in builder.execution_capability_grants}
    assert {"runner.invoke", "workspace.read", "artifact.write"} <= grant_ids
    assert builder.execution_capability_grants[0].fingerprint
    assert outcome.active_plan.execution_capability_summary["total_grants"] > 0


def test_config_policy_can_make_capability_approval_required(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    assets_root = _copy_builtin_assets(tmp_path)
    bootstrap_workspace(workspace_root, assets_root=assets_root)
    builder_path = assets_root / "registry" / "stage_kinds" / "execution" / "builder.json"
    payload = json.loads(builder_path.read_text(encoding="utf-8"))
    payload["execution_capability_requests"] = [
        {
            "request_id": "builder-package-install",
            "capability_id": "package.install",
            "access": "execute",
            "scope": {"kind": "package_manager", "value": "uv"},
            "reason": "test request",
        }
    ]
    builder_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(
            execution_capabilities={
                "defaults": {
                    "package_install": "approval_required",
                }
            }
        ),
        requested_mode_id="default_codex",
        assets_root=assets_root,
    )

    assert outcome.active_plan is not None
    builder = next(node for node in outcome.active_plan.execution_graph.nodes if node.node_id == "builder")
    grant = next(grant for grant in builder.execution_capability_grants if grant.capability_id == "package.install")
    assert grant.decision_state is CapabilityDecisionState.APPROVAL_REQUIRED
    assert grant.approval_policy_ref is not None


def test_strict_required_advisory_grant_fails_compile(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(
            execution_capabilities={
                "fail_required_advisory": True,
            }
        ),
        requested_mode_id="default_codex",
    )

    assert outcome.active_plan is None
    assert outcome.diagnostics.ok is False
    assert any("requires enforcement" in error or "advisory" in error for error in outcome.diagnostics.errors)


def test_mode_policy_cannot_override_runtime_config_denial(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    assets_root = _copy_builtin_assets(tmp_path)
    bootstrap_workspace(workspace_root, assets_root=assets_root)
    mode_path = assets_root / "modes" / "default_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["execution_capability_policies"] = [
        {
            "capability_id": "network.access",
            "decision": "allow",
            "reason": "mode wants network",
        }
    ]
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    builder_path = assets_root / "registry" / "stage_kinds" / "execution" / "builder.json"
    builder_payload = json.loads(builder_path.read_text(encoding="utf-8"))
    builder_payload["execution_capability_requests"] = [
        {
            "request_id": "builder-network",
            "capability_id": "network.access",
            "access": "execute",
            "scope": {"kind": "network_class", "value": "raw"},
        }
    ]
    builder_path.write_text(json.dumps(builder_payload, indent=2) + "\n", encoding="utf-8")

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(execution_capabilities={"defaults": {"network_access": "deny"}}),
        requested_mode_id="default_codex",
        assets_root=assets_root,
    )

    assert outcome.active_plan is not None
    builder = next(node for node in outcome.active_plan.graphs_by_plane[Plane.EXECUTION].nodes if node.node_id == "builder")
    network_grant = next(grant for grant in builder.execution_capability_grants if grant.capability_id == "network.access")
    assert network_grant.decision_state is CapabilityDecisionState.DENIED
