"""Deterministic request-context artifact rendering primitives."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import RenderedRequestContext, RequestContextRenderPlan


def render_request_context(
    plan: RequestContextRenderPlan,
    *,
    workspace_root: Path,
) -> RenderedRequestContext:
    """Write context bundle, rendered markdown, and manifest for a render plan."""

    bundle_path = _resolve_workspace_path(workspace_root, plan.context_bundle_path)
    rendered_path = _resolve_workspace_path(
        workspace_root,
        plan.rendered_prompt_context_path or str(bundle_path.with_name("prompt_context.md")),
    )
    manifest_path = rendered_path.with_name("render_manifest.json")
    bundle_payload = _context_bundle_payload(plan)
    text = _render_markdown(plan)
    manifest = _render_manifest(plan, text=text)

    _atomic_write_text(bundle_path, json.dumps(bundle_payload, indent=2, sort_keys=True) + "\n")
    _atomic_write_text(rendered_path, text)
    _atomic_write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return RenderedRequestContext(
        context_bundle_path=str(bundle_path),
        rendered_prompt_context_path=str(rendered_path),
        render_manifest_path=str(manifest_path),
        text=text,
        manifest=manifest,
    )


def _context_bundle_payload(plan: RequestContextRenderPlan) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "kind": "request_context_bundle",
        "profile_id": plan.profile_id,
        "render_plan_id": plan.render_plan_id,
        "visible_artifact_refs": list(plan.visible_artifact_refs),
        "operator_only_artifact_refs": list(plan.operator_only_artifact_refs),
        "included_provider_ids": list(plan.included_provider_ids),
        "redacted_provider_ids": list(plan.redacted_provider_ids),
        "inline_sections": list(plan.inline_sections),
        "omitted_provider_ids": list(plan.omitted_provider_ids),
        "artifact_contract_source": plan.artifact_contract_source,
        "output_artifact_contract_ids": list(plan.output_artifact_contract_ids),
    }


def _render_markdown(plan: RequestContextRenderPlan) -> str:
    lines = [
        "# Request Context",
        "",
        f"Render Plan ID: {plan.render_plan_id}",
        f"Profile ID: {plan.profile_id}",
        "",
        "Included Providers:",
    ]
    if plan.included_provider_ids:
        lines.extend(f"- {provider_id}" for provider_id in plan.included_provider_ids)
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "Redacted Providers:",
        ]
    )
    if plan.redacted_provider_ids:
        lines.extend(f"- {provider_id}" for provider_id in plan.redacted_provider_ids)
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "Visible Artifacts:",
        ]
    )
    if plan.visible_artifact_refs:
        lines.extend(f"- {artifact_ref}" for artifact_ref in plan.visible_artifact_refs)
    else:
        lines.append("- none")
    lines.extend(["", "Inline Sections:"])
    if plan.inline_sections:
        lines.extend(f"- {section}" for section in plan.inline_sections)
    else:
        lines.append("- none")
    lines.extend(["", "Omitted Providers:"])
    if plan.omitted_provider_ids:
        lines.extend(f"- {provider_id}" for provider_id in plan.omitted_provider_ids)
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _render_manifest(plan: RequestContextRenderPlan, *, text: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "kind": "request_context_render_manifest",
        "render_plan_id": plan.render_plan_id,
        "profile_id": plan.profile_id,
        "included_provider_ids": list(plan.included_provider_ids),
        "visible_artifact_refs": list(plan.visible_artifact_refs),
        "redacted_provider_ids": list(plan.redacted_provider_ids),
        "redacted_artifact_refs": list(plan.operator_only_artifact_refs),
        "omitted_provider_ids": list(plan.omitted_provider_ids),
        "artifact_contract_source": plan.artifact_contract_source,
        "output_artifact_contract_ids": list(plan.output_artifact_contract_ids),
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def _resolve_workspace_path(workspace_root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return workspace_root / path


def _atomic_write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(payload, encoding="utf-8")
    temp_path.replace(path)


__all__ = ["render_request_context"]
