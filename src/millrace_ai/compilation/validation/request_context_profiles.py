"""Request-context profile validation helpers."""

from __future__ import annotations

from millrace_ai.architecture import (
    ArtifactContractDefinition,
    FrozenGraphPlanePlan,
    RequestContextProfileDefinition,
    RequestContextProviderDefinition,
    RequestContextRenderPlan,
)
from millrace_ai.assets import WorkflowPrimitiveBundle
from millrace_ai.contracts import Plane

from ..outcomes import CompilerValidationError


def validate_request_context_profiles(
    *,
    artifact_contracts_by_id: dict[str, ArtifactContractDefinition],
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    request_context_profiles_by_id: dict[str, RequestContextProfileDefinition],
    request_context_providers_by_id: dict[str, RequestContextProviderDefinition],
    request_context_render_plans_by_id: dict[str, RequestContextRenderPlan],
    workflow_primitives: WorkflowPrimitiveBundle,
) -> None:
    for profile in workflow_primitives.request_context_profiles:
        provider = request_context_providers_by_id.get(profile.provider_id)
        if provider is None:
            raise CompilerValidationError(
                f"request context profile {profile.profile_id} references unknown "
                f"provider {profile.provider_id}"
            )
        if profile.request_kind not in provider.supported_request_kinds:
            supported = ", ".join(provider.supported_request_kinds)
            raise CompilerValidationError(
                f"request context profile {profile.profile_id} request kind "
                f"{profile.request_kind} is not supported by provider {provider.provider_id}; "
                f"supported request kinds: {supported}"
            )
        render_plan = request_context_render_plans_by_id.get(profile.primary_render_plan_id)
        if render_plan is None:
            raise CompilerValidationError(
                f"request context profile {profile.profile_id} references unknown "
                f"render plan {profile.primary_render_plan_id}"
            )
        missing_capabilities = sorted(
            set(render_plan.required_provider_capabilities) - set(provider.capabilities)
        )
        if missing_capabilities:
            raise CompilerValidationError(
                f"request context profile {profile.profile_id} render plan "
                f"{render_plan.render_plan_id} requires provider capabilities not declared "
                f"by {provider.provider_id}: {', '.join(missing_capabilities)}"
            )
        missing_required_providers = sorted(
            set(profile.required_providers) - set(provider.capabilities)
        )
        if missing_required_providers:
            raise CompilerValidationError(
                f"request context profile {profile.profile_id} requires provider "
                f"capabilities not declared by {provider.provider_id}: "
                f"{', '.join(missing_required_providers)}"
            )
        for artifact_id, filename in profile.output_path_preferences.items():
            if artifact_id not in artifact_contracts_by_id:
                raise CompilerValidationError(
                    f"request context profile {profile.profile_id} references unknown "
                    f"output artifact {artifact_id}"
                )
            contract = artifact_contracts_by_id[artifact_id]
            if filename not in contract.all_filenames:
                allowed = ", ".join(contract.all_filenames)
                raise CompilerValidationError(
                    f"request context profile {profile.profile_id} maps artifact "
                    f"{artifact_id} to filename {filename}; artifact contract mismatch: "
                    f"artifact contract {artifact_id} allows filenames {allowed}"
                )
    for graph in graphs_by_plane.values():
        for node in graph.nodes:
            profile_id = node.request_context_profile_id
            if profile_id is None:
                raise CompilerValidationError(
                    f"graph node {node.node_id} has no request context profile"
                )
            node_profile = request_context_profiles_by_id.get(profile_id)
            if node_profile is None:
                raise CompilerValidationError(
                    f"graph node {node.node_id} references unknown request context "
                    f"profile {profile_id}"
                )
            provider = request_context_providers_by_id[node_profile.provider_id]
            if graph.plane not in provider.supported_planes:
                raise CompilerValidationError(
                    f"request context profile {node_profile.profile_id} provider "
                    f"{provider.provider_id} does not support plane {graph.plane.value}"
                )
            render_plan_id = node.context_render_plan_id
            if render_plan_id is None:
                raise CompilerValidationError(
                    f"graph node {node.node_id} has no context render plan"
                )
            if render_plan_id not in request_context_render_plans_by_id:
                raise CompilerValidationError(
                    f"graph node {node.node_id} references unknown context render "
                    f"plan {render_plan_id}"
                )
            if (
                render_plan_id != node_profile.primary_render_plan_id
                and not node_profile.allow_render_plan_override
            ):
                raise CompilerValidationError(
                    f"graph node {node.node_id} overrides request context render plan "
                    f"{node_profile.primary_render_plan_id} with {render_plan_id}, but "
                    f"profile {node_profile.profile_id} does not allow render plan overrides"
                )
            render_plan = request_context_render_plans_by_id[render_plan_id]
            missing_capabilities = sorted(
                set(render_plan.required_provider_capabilities) - set(provider.capabilities)
            )
            if missing_capabilities:
                raise CompilerValidationError(
                    f"graph node {node.node_id} context render plan {render_plan_id} "
                    f"requires provider capabilities not declared by {provider.provider_id}: "
                    f"{', '.join(missing_capabilities)}"
                )


__all__ = ["validate_request_context_profiles"]
