from __future__ import annotations

import millrace.contracts.state as state_contracts
import millrace.contracts.transition as transition_contracts
from millrace.contracts.state import (
    GovernanceEventRecord,
    TraceRecord,
    TransitionRecord,
    TransitionRefusal,
)
from millrace.contracts.transition import (
    AdmitPlan,
    AdmitPlanRef,
    ClaimWork,
    CloseWorkItem,
    CreateActivation,
    CreateRun,
    CreateWorkItem,
    EmitGovernanceEvent,
    EmitTrace,
    EnqueueWork,
    InitializeWorkspace,
    RecordArtifact,
    RecordCooldownWait,
    RecordInputReceipt,
    RecordLineageQuarantine,
    RecordRecoveryAttempt,
    RecordRefusal,
    RecordRunnerObservation,
    RecordTransition,
    RouteActivation,
    RunnerResultObserved,
    SelectDefaultPlan,
    SelectDefaultPlanRef,
    SetPause,
    SetQuarantine,
    TimerDue,
)


def test_transition_input_protocol_kinds_are_explicit_stable_ids() -> None:
    expected_kinds = (
        (InitializeWorkspace, "control.initialize_workspace"),
        (AdmitPlan, "control.admit_plan"),
        (SelectDefaultPlan, "control.select_default_plan"),
        (EnqueueWork, "workflow.enqueue_work"),
        (ClaimWork, "workflow.claim_work"),
        (TimerDue, "workflow.timer_due"),
        (RunnerResultObserved, "workflow.runner_result_observed"),
        (
            getattr(transition_contracts, "OperatorResumeLineage"),
            "workflow.operator_resume_lineage",
        ),
        (
            getattr(transition_contracts, "OperatorCloseLineage"),
            "workflow.operator_close_lineage",
        ),
        (
            getattr(transition_contracts, "OperatorReviseLineage"),
            "workflow.operator_revise_lineage",
        ),
    )

    for input_type, expected_kind in expected_kinds:
        assert input_type.input_kind == expected_kind
        assert input_type.input_schema_version == 1


def test_transition_mutation_protocol_kinds_are_explicit_stable_ids() -> None:
    expected_kinds = (
        (RecordInputReceipt, "mutation.record_input_receipt"),
        (AdmitPlanRef, "mutation.admit_plan_ref"),
        (SelectDefaultPlanRef, "mutation.select_default_plan_ref"),
        (CreateWorkItem, "mutation.create_work_item"),
        (CreateActivation, "mutation.create_activation"),
        (CreateRun, "mutation.create_run"),
        (RecordTransition, "mutation.record_transition"),
        (RecordRefusal, "mutation.record_refusal"),
        (RecordRunnerObservation, "mutation.record_runner_observation"),
        (RecordArtifact, "mutation.record_artifact"),
        (RouteActivation, "mutation.route_activation"),
        (
            getattr(transition_contracts, "RecordClosureTarget"),
            "mutation.record_closure_target",
        ),
        (
            getattr(transition_contracts, "CloseClosureTarget"),
            "mutation.close_closure_target",
        ),
        (
            getattr(transition_contracts, "RecordClosureEvaluation"),
            "mutation.record_closure_evaluation",
        ),
        (
            getattr(transition_contracts, "RecordClosureTerminal"),
            "mutation.record_closure_terminal",
        ),
        (
            getattr(transition_contracts, "RecordRemediationWork"),
            "mutation.record_remediation_work",
        ),
        (
            getattr(transition_contracts, "RecordClosureBlocked"),
            "mutation.record_closure_blocked",
        ),
        (CloseWorkItem, "mutation.close_work_item"),
        (SetPause, "mutation.set_pause"),
        (SetQuarantine, "mutation.set_quarantine"),
        (RecordLineageQuarantine, "mutation.record_lineage_quarantine"),
        (RecordRecoveryAttempt, "mutation.record_recovery_attempt"),
        (
            getattr(transition_contracts, "SupersedeLineageQuarantine"),
            "mutation.supersede_lineage_quarantine",
        ),
        (
            getattr(transition_contracts, "RecordOperatorIntervention"),
            "mutation.record_operator_intervention",
        ),
        (RecordCooldownWait, "mutation.record_cooldown_wait"),
        (EmitGovernanceEvent, "mutation.emit_governance_event"),
        (EmitTrace, "mutation.emit_trace"),
    )

    for mutation_type, expected_kind in expected_kinds:
        assert mutation_type.mutation_kind == expected_kind
        assert mutation_type.mutation_schema_version == 1


def test_audit_record_protocol_kinds_are_explicit_stable_ids() -> None:
    expected_kinds = (
        (TransitionRecord, "transition_record"),
        (TransitionRefusal, "transition_refusal"),
        (GovernanceEventRecord, "governance_event"),
        (TraceRecord, "trace"),
        (
            getattr(state_contracts, "OperatorInterventionRecord"),
            "operator_intervention",
        ),
    )

    for record_type, expected_kind in expected_kinds:
        assert record_type.record_kind == expected_kind
        assert record_type.schema_version == 1
