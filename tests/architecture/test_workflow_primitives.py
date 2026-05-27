from __future__ import annotations

import pytest
from pydantic import ValidationError

from millrace_ai.architecture import (
    ArtifactContractDefinition,
    ArtifactFilenameAdapterDefinition,
    ArtifactFormat,
    LaneConflictPolicyDefinition,
    LifecycleMutationPlanDefinition,
    OperatorControlCapabilityDefinition,
    OutcomeArtifactDefinition,
    PlaneQueueClaimPolicyDefinition,
    RequestContextProfileDefinition,
    RequestContextProviderDefinition,
    RequestContextRenderPlan,
    RuntimeEffectHandlerDefinition,
    RuntimeEffectOperationRunnerDefinition,
    RuntimeEffectRuleDefinition,
    RuntimeFailurePolicyDefinition,
    TerminalActionDefinition,
    WorkflowCompletionBehaviorDefinition,
    WorkflowLaneDefinition,
    WorkflowPlaneSchedulerPolicyDefinition,
    WorkflowRecoveryPolicyDefinition,
    WorkItemDocumentAdapterDefinition,
    WorkItemFamilyDefinition,
    WorkItemPartitionSelectorDefinition,
    WorkItemQueueDirs,
    WorkspaceSchemaEpochDefinition,
)
from millrace_ai.contracts import Plane


def _json_adapter(filename: str) -> ArtifactFilenameAdapterDefinition:
    return ArtifactFilenameAdapterDefinition(
        filename=filename,
        format="json",
        parser_id="builtin.json",
        renderer_id="builtin.json",
    )


def test_artifact_contract_accepts_canonical_and_fallback_filename_adapters() -> None:
    contract = ArtifactContractDefinition(
        artifact_id="generated_task",
        canonical_filename="generated_task.json",
        accepted_filenames=("generated_task.md",),
        preferred_format=ArtifactFormat.JSON,
        schema_id="task_document_v1",
        filename_adapters=(
            _json_adapter("generated_task.json"),
            ArtifactFilenameAdapterDefinition(
                filename="generated_task.md",
                format="markdown",
                parser_id="builtin.markdown",
                renderer_id="builtin.markdown",
            ),
        ),
        producer_stage_kind_ids=("evaluator_blueprint", "recon"),
        consumer_handler_ids=("evaluator_blueprint_approved_to_task",),
        consumer_operation_ids=("evaluator_blueprint_approved_to_task",),
        destination_family_id="task",
    )

    assert contract.artifact_id == "generated_task"
    assert contract.preferred_format is ArtifactFormat.JSON
    assert contract.all_filenames == ("generated_task.json", "generated_task.md")
    assert contract.filename_adapters_by_name["generated_task.md"].parser_id == "builtin.markdown"


def test_artifact_contract_requires_identity_filename_format_schema_and_adapter() -> None:
    with pytest.raises(ValidationError, match="artifact_id"):
        ArtifactContractDefinition(
            canonical_filename="report.md",
            preferred_format="markdown",
            schema_id="markdown_report_v1",
            filename_adapters=(
                ArtifactFilenameAdapterDefinition(
                    filename="report.md",
                    format="markdown",
                    parser_id="builtin.markdown",
                ),
            ),
        )


def test_artifact_contract_rejects_unsafe_artifact_id() -> None:
    with pytest.raises(ValidationError, match="artifact_id"):
        ArtifactContractDefinition(
            artifact_id="../Generated Task",
            canonical_filename="generated_task.json",
            preferred_format="json",
            schema_id="task_document_v1",
            filename_adapters=(_json_adapter("generated_task.json"),),
        )


def test_artifact_contract_rejects_duplicate_filenames() -> None:
    with pytest.raises(ValidationError, match="duplicate artifact filename"):
        ArtifactContractDefinition(
            artifact_id="generated_task",
            canonical_filename="generated_task.json",
            accepted_filenames=("generated_task.json",),
            preferred_format="json",
            schema_id="task_document_v1",
            filename_adapters=(_json_adapter("generated_task.json"),),
        )


def test_artifact_contract_rejects_unknown_preferred_format() -> None:
    with pytest.raises(ValidationError, match="preferred_format"):
        ArtifactContractDefinition(
            artifact_id="report",
            canonical_filename="report.md",
            preferred_format="pdf",
            schema_id="markdown_report_v1",
            filename_adapters=(
                ArtifactFilenameAdapterDefinition(
                    filename="report.md",
                    format="markdown",
                    parser_id="builtin.markdown",
                ),
            ),
        )


def test_artifact_contract_requires_parser_semantics_for_every_filename() -> None:
    with pytest.raises(ValidationError, match="filename_adapters"):
        ArtifactContractDefinition(
            artifact_id="generated_task",
            canonical_filename="generated_task.json",
            accepted_filenames=("generated_task.md",),
            preferred_format="json",
            schema_id="task_document_v1",
            filename_adapters=(_json_adapter("generated_task.json"),),
        )


def test_artifact_contract_mixed_formats_require_explicit_filename_adapters() -> None:
    with pytest.raises(ValidationError, match="filename_adapters"):
        ArtifactContractDefinition(
            artifact_id="generated_task",
            canonical_filename="generated_task.json",
            accepted_filenames=("generated_task.md",),
            preferred_format="json",
            schema_id="task_document_v1",
            filename_adapters=(),
        )


def test_work_item_family_accepts_valid_custom_family() -> None:
    family = WorkItemFamilyDefinition(
        family_id="blueprint_draft",
        plane=Plane.PLANNING,
        entry_key="blueprint_draft",
        display_name="Blueprint Draft",
        document_kind="blueprint_draft",
        runtime_relative_dir="planning/blueprint-drafts",
        file_extension=".md",
        schema_id="blueprint_draft_v1",
        document_adapter_id="blueprint_draft_markdown_v1",
        queue_lifecycle_adapter_id="custom.queue_lifecycle.blueprint_draft",
        queue_dirs=WorkItemQueueDirs(
            queue="queued",
            active="active",
            done="done",
            blocked="blocked",
        ),
        lifecycle_states=("queued", "active", "done", "blocked"),
        claimable_state="queued",
        active_state="active",
        done_state="done",
        blocked_state="blocked",
        closure_blocking_states=("queued", "active", "blocked"),
        default_entry_key="blueprint_draft",
    )

    assert family.family_id == "blueprint_draft"
    assert family.lifecycle_states == ("queued", "active", "done", "blocked")
    assert family.runtime_relative_dir == "planning/blueprint-drafts"
    assert family.queue_lifecycle_adapter_id == "custom.queue_lifecycle.blueprint_draft"


def test_work_item_family_backfills_builtin_queue_lifecycle_adapter_id() -> None:
    family = WorkItemFamilyDefinition(
        family_id="task",
        plane=Plane.EXECUTION,
        entry_key="task",
        display_name="Task",
        document_kind="task",
        runtime_relative_dir="tasks",
        schema_id="task_v1",
        document_adapter_id="markdown_v1",
        queue_dirs=WorkItemQueueDirs(
            queue="queued",
            active="active",
            done="done",
            blocked="blocked",
        ),
        lifecycle_states=("queued", "active", "done", "blocked"),
        closure_blocking_states=("queued", "active", "blocked"),
        id_field="task_id",
    )

    assert family.queue_lifecycle_adapter_id == "builtin.queue_lifecycle.task"


def test_work_item_family_rejects_duplicate_lifecycle_states() -> None:
    with pytest.raises(ValidationError, match="duplicate lifecycle_states"):
        WorkItemFamilyDefinition(
            family_id="task",
            plane=Plane.EXECUTION,
            entry_key="task",
            display_name="Task",
            document_kind="task",
            runtime_relative_dir="tasks",
            schema_id="task_v1",
            document_adapter_id="markdown_v1",
            queue_dirs=WorkItemQueueDirs(
                queue="queued",
                active="active",
                done="done",
                blocked="blocked",
            ),
            lifecycle_states=("queued", "active", "queued", "blocked"),
            closure_blocking_states=("queued", "active", "blocked"),
            id_field="task_id",
        )


def test_work_item_family_rejects_path_traversal() -> None:
    with pytest.raises(ValidationError, match="runtime_relative_dir"):
        WorkItemFamilyDefinition(
            family_id="task",
            plane=Plane.EXECUTION,
            entry_key="task",
            display_name="Task",
            document_kind="task",
            runtime_relative_dir="../tasks",
            schema_id="task_v1",
            document_adapter_id="markdown_v1",
            queue_dirs=WorkItemQueueDirs(
                queue="queued",
                active="active",
                done="done",
                blocked="blocked",
            ),
            lifecycle_states=("queued", "active", "done", "blocked"),
            closure_blocking_states=("queued", "active", "blocked"),
            id_field="task_id",
        )


def test_document_adapter_rejects_duplicate_family_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate family_ids"):
        WorkItemDocumentAdapterDefinition(
            adapter_id="markdown_v1",
            schema_id="work_document_v1",
            supported_file_extensions=(".md",),
            family_ids=("task", "Task"),
            can_parse=True,
            can_render=True,
            can_summarize=True,
            supports_dependencies=True,
            supports_lineage=True,
        )


def test_plane_queue_claim_policy_rejects_duplicate_family_order() -> None:
    with pytest.raises(ValidationError, match="duplicate family_order"):
        PlaneQueueClaimPolicyDefinition(
            policy_id="execution.default",
            plane=Plane.EXECUTION,
            family_order=("task", "task"),
        )


def test_lane_custom_partition_requires_selector() -> None:
    with pytest.raises(ValidationError, match="partition_selector_id"):
        WorkflowLaneDefinition(
            lane_id="execution.repo_paths",
            plane=Plane.EXECUTION,
            allowed_family_ids=("task",),
            claim_policy_id="execution.default",
            one_active_scope="custom_partition",
            conflict_policy_id="execution.default",
        )


def test_lane_conflict_policy_rejects_missing_lock_for_workspace_scope() -> None:
    with pytest.raises(ValidationError, match="lock_acquisition_order"):
        LaneConflictPolicyDefinition(
            policy_id="default",
            lane_ids=("planning.default",),
            concurrent_with_lane_ids=("execution.default",),
            conflict_scopes=("workspace",),
            lock_acquisition_order=(),
        )


def test_terminal_action_rejects_missing_lifecycle_plan() -> None:
    with pytest.raises(ValidationError, match="lifecycle_mutation_plan_id"):
        TerminalActionDefinition(
            terminal_action_id="complete_source",
            terminal_class="success",
            lifecycle_mutation_plan_id="",
        )


def test_lifecycle_mutation_plan_rejects_mutation_without_action() -> None:
    with pytest.raises(ValidationError, match="lifecycle_action_id"):
        LifecycleMutationPlanDefinition(
            plan_id="complete_task",
            source_node_id="builder",
            outcome_id="BUILDER_COMPLETE",
            source_family_id="task",
            owner="terminal_action",
            source_from_state="active",
            source_to_state="done",
            ordering="after_effect_success",
        )


def test_runtime_effect_handler_rejects_incoherent_lifecycle_metadata() -> None:
    with pytest.raises(ValidationError, match="requires_lifecycle_mutation_plan"):
        RuntimeEffectHandlerDefinition(
            handler_id="generated_task_artifact_to_task_queue",
            source_planes=(Plane.PLANNING,),
            allowed_source_families=("spec",),
            destination_kinds=("task",),
            required_artifacts=("task_packet",),
            returns_source_lifecycle_intent=True,
            requires_lifecycle_mutation_plan=False,
            creates_work_items=True,
            failure_classes=("effect_validation_failure",),
        )


def test_runtime_effect_runner_maps_legacy_handler_aliases() -> None:
    runner = RuntimeEffectOperationRunnerDefinition(
        runner_id="legacy_python_handler",
        operation_ids=("planner_disposition", "fixture_echo_effect"),
        required_runtime_capabilities=("replay.planner_disposition",),
        legacy_handler_ids=("planner_disposition", "fixture_echo_effect"),
        legacy_handler_operation_ids={
            "planner_disposition": "planner_disposition",
            "fixture_echo_effect": "fixture_echo_effect",
        },
        result_display_aliases={"planner_disposition": "planner_disposition"},
    )

    assert runner.operation_id_for_legacy_handler("planner_disposition") == "planner_disposition"
    assert runner.operation_id_for_legacy_handler("missing_handler") is None


def test_runtime_effect_runner_rejects_ambiguous_multi_operation_alias() -> None:
    with pytest.raises(ValidationError, match="legacy_handler_operation_ids"):
        RuntimeEffectOperationRunnerDefinition(
            runner_id="legacy_python_handler",
            operation_ids=("planner_disposition", "fixture_echo_effect"),
            legacy_handler_ids=("planner_disposition",),
        )


def test_runtime_effect_rule_rejects_missing_destination_for_work_item_handler() -> None:
    with pytest.raises(ValidationError, match="destination_family_id"):
        RuntimeEffectRuleDefinition(
            rule_id="planner_task_artifact",
            effect_operation_id="planner-task-artifact",
            source_node_id="planner",
            on_outcomes=("PLANNER_COMPLETE",),
            handler_id="generated_task_artifact_to_task_queue",
            required_run_artifacts=("task_packet",),
            creates_work_items=True,
            duplicate_policy="idempotent",
            partial_commit_policy="block_source",
            replay_policy="resume_idempotently",
            lineage_policy="preserve_root",
            applies_before_route=False,
        )


def test_runtime_effect_rule_accepts_operation_only_authority() -> None:
    rule = RuntimeEffectRuleDefinition(
        rule_id="planner_task_artifact",
        effect_operation_id="planner-task-artifact",
        source_node_id="planner",
        on_outcomes=("PLANNER_COMPLETE",),
        required_run_artifacts=("task_packet",),
        creates_work_items=False,
        duplicate_policy="idempotent",
        partial_commit_policy="block_source",
        replay_policy="resume_idempotently",
        lineage_policy="preserve_root",
        applies_before_route=False,
    )

    assert rule.handler_id is None


def test_request_context_profile_rejects_unsafe_output_path_preference() -> None:
    with pytest.raises(ValidationError, match="output_path_preferences"):
        RequestContextProfileDefinition(
            profile_id="builder.default",
            request_kind="task",
            provider_id="generic.active_work_item",
            primary_render_plan_id="stage_request.default.v1",
            required_providers=("active_work_item",),
            output_path_preferences={"report": "../report.md"},
            visibility_policy="active_item_only",
        )


def test_recovery_policy_requires_one_exhausted_target() -> None:
    with pytest.raises(ValidationError, match="exactly one exhausted target"):
        WorkflowRecoveryPolicyDefinition(
            policy_id="fixer.retry",
            source_node_ids=("checker",),
            on_outcomes=("FIX_NEEDED",),
            counter_name="fix_cycle_count",
            threshold=3,
            exhausted_target_node_id="troubleshooter",
            exhausted_terminal_state_id="blocked",
            failure_class_template="fix_cycle_exhausted",
        )


def test_runtime_failure_policy_requires_recovery_node_for_recovery_action() -> None:
    with pytest.raises(ValidationError, match="recovery_node_id"):
        RuntimeFailurePolicyDefinition(
            policy_id="runner_timeout",
            applies_to_origins=("runner_timeout",),
            applies_to_planes=(Plane.EXECUTION,),
            action="route_to_recovery_node",
            threshold=1,
            counter_name="runner_timeout",
            failure_class_template="runner_timeout",
        )


def test_runtime_failure_policy_accepts_runtime_effect_route_inputs() -> None:
    policy = RuntimeFailurePolicyDefinition(
        policy_id="blueprint_approval_pre_mutation_effect_validation",
        applies_to_origins=("runtime_effect",),
        applies_to_planes=(Plane.PLANNING,),
        applies_to_families=("blueprint_draft",),
        applies_to_failure_classes=(
            "generated_task_missing",
            "generated_task_invalid",
            "blueprint_task_promotion_invalid",
        ),
        applies_to_mutation_phases=("pre_mutation",),
        applies_to_handler_ids=("evaluator_blueprint_approved_to_task",),
        applies_to_source_node_ids=("evaluator_blueprint",),
        applies_to_source_terminal_state_ids=("blueprint_approved",),
        action="route_to_node",
        target_node_id="mechanic_blueprint",
        failure_class_template="runtime_effect_failure",
    )

    assert policy.action == "route_to_node"
    assert policy.target_node_id == "mechanic_blueprint"
    assert policy.applies_to_mutation_phases == ("pre_mutation",)


def test_scheduler_policy_requires_claim_policy_for_each_plane() -> None:
    lane = WorkflowLaneDefinition(
        lane_id="execution.default",
        plane=Plane.EXECUTION,
        allowed_family_ids=("task",),
        claim_policy_id="execution.default",
        conflict_policy_id="execution.default",
    )

    with pytest.raises(ValidationError, match="claim_policies_by_plane"):
        WorkflowPlaneSchedulerPolicyDefinition(
            policy_id="default",
            plane_order=(Plane.EXECUTION,),
            lanes=(lane,),
            claim_policies_by_plane={},
            completion_check_order=(Plane.EXECUTION,),
        )


def test_exported_primitive_models_round_trip() -> None:
    partition_selector = WorkItemPartitionSelectorDefinition(
        selector_id="task.lineage",
        family_id="task",
        output_kind="lineage",
        supports_static_compile_check=True,
    )
    outcome_artifacts = OutcomeArtifactDefinition(
        outcome_id="BUILDER_COMPLETE",
        required_artifacts=(),
    )
    render_plan = RequestContextRenderPlan(
        render_plan_id="builder.default",
        bundle_schema_version="1.0",
        included_sections=("active_work_item",),
        required_provider_capabilities=("active_work_item",),
        artifact_ref_policy="path_only",
        prompt_rendering_behavior="default_markdown",
        redaction_policy_id="default",
        max_inline_bytes_by_role={"task": 8192},
        missing_optional_provider_policy="omit",
    )
    provider = RequestContextProviderDefinition(
        provider_id="generic.active_work_item",
        python_registry_id="generic.active_work_item",
        supported_request_kinds=("active_work_item",),
        supported_planes=(Plane.EXECUTION,),
        capabilities=("active_work_item",),
        required_workspace_data_surfaces=("active_work_item",),
    )
    completion = WorkflowCompletionBehaviorDefinition(
        behavior_id="planning.closure",
        plane=Plane.PLANNING,
        target_scope="workspace",
        readiness_handler_ids=("built_in_lineage_work_closed",),
        target_entry_key="closure_target",
        target_node_id="arbiter",
        request_context_profile_id="arbiter.default",
        target_selector="active_closure_target",
        backpressure_policy="block_all",
        terminal_action_by_outcome={"ARBITER_COMPLETE": "closure_pass"},
    )
    control = OperatorControlCapabilityDefinition(
        capability_id="task.cancel",
        action="cancel",
        target_type="work_item",
        family_ids=("task",),
        allowed_lifecycle_states=("queued", "active", "blocked"),
    )
    epoch = WorkspaceSchemaEpochDefinition(
        epoch_id="v0.20",
        minimum_supported_epoch_id="v0.20",
        archive_required_from_epoch_ids=("v0.19",),
        reset_command="workspace reset-schema",
    )

    assert partition_selector.model_dump()["selector_id"] == "task.lineage"
    assert outcome_artifacts.model_dump()["outcome_id"] == "BUILDER_COMPLETE"
    assert render_plan.model_dump()["render_plan_id"] == "builder.default"
    assert provider.model_dump()["provider_id"] == "generic.active_work_item"
    assert completion.model_dump()["behavior_id"] == "planning.closure"
    assert control.model_dump()["capability_id"] == "task.cancel"
    assert epoch.model_dump()["epoch_id"] == "v0.20"
