from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pytest
from pydantic import ValidationError

import millrace_engine.config_runtime as config_runtime
from millrace_engine.config import (
    CompoundingProfile,
    ConfigApplyBoundary,
    ComplexityBand,
    StageConfig,
    WatchRoot,
    build_runtime_paths,
    default_stage_configs,
    diff_config_fields,
    load_engine_config,
)
from millrace_engine.contracts import RunnerKind, StageContext, StageType, load_objective_contract
from millrace_engine.contracts import (
    ContextFactArtifact,
    ContextFactInjectionBundle,
    ContextFactLifecycleState,
    ContextFactRetrievalRule,
    ContextFactSelectionReason,
    ContextFactScope,
    HarnessBenchmarkOutcome,
    HarnessBenchmarkResult,
    HarnessBenchmarkStatus,
    HarnessCandidateArtifact,
    HarnessCandidatePromptAssetOverride,
    HarnessCandidateState,
    HarnessChangedSurface,
    HarnessChangedSurfaceKind,
    HarnessRecommendationArtifact,
    HarnessRecommendationDisposition,
    HarnessSearchRequestArtifact,
    InjectedContextFact,
    InjectedProcedure,
    LabHarnessComparisonArtifact,
    LabHarnessComparisonRow,
    LabHarnessProposalArtifact,
    LabHarnessProposalState,
    LabHarnessRequestArtifact,
    LabHarnessRequestSourceKind,
    ProcedureInjectionBundle,
    ProcedureLifecycleRecord,
    ProcedureLifecycleState,
    ProcedureRetrievalRule,
    ProcedureScope,
    ProcedureUsageDisposition,
    ProcedureUsageRecord,
    ReusableProcedureArtifact,
)
from millrace_engine.paths import format_historylog_entry_name
from tests.support import runtime_workspace


def test_native_config_loads_from_runtime_workspace(tmp_path: Path) -> None:
    workspace_root, config_path = runtime_workspace(tmp_path)
    loaded = load_engine_config(config_path)

    assert loaded.source.kind == "native_toml"
    assert loaded.config.paths.workspace == workspace_root.resolve()
    assert loaded.config.paths.agents_dir == (workspace_root / "agents").resolve()
    assert loaded.config.stages[StageType.BUILDER].prompt_file == (workspace_root / "agents/_start.md").resolve()


def test_default_stage_configs_use_real_shipped_model_ids() -> None:
    stages = default_stage_configs()

    assert stages[StageType.BUILDER].runner.value == "codex"
    assert stages[StageType.BUILDER].model == "gpt-5.3-codex"
    assert stages[StageType.GOAL_INTAKE].model == "gpt-5.3-codex"
    assert stages[StageType.SPEC_SYNTHESIS].model == "gpt-5.2"
    assert stages[StageType.CLARIFY].model == "gpt-5.2"


def test_execution_stage_defaults_use_one_hour_timeout() -> None:
    stages = default_stage_configs()

    assert StageConfig().timeout_seconds == 3600
    assert stages[StageType.BUILDER].timeout_seconds == 3600
    assert stages[StageType.QA].timeout_seconds == 3600
    assert stages[StageType.INTEGRATION].timeout_seconds == 3600


def test_config_re_exports_runtime_stage_and_boundary_family() -> None:
    assert StageConfig is config_runtime.StageConfig
    assert ConfigApplyBoundary is config_runtime.ConfigApplyBoundary
    assert default_stage_configs is config_runtime.default_stage_configs


def test_stage_context_default_timeout_matches_stage_defaults(tmp_path: Path) -> None:
    context = StageContext(
        stage=StageType.BUILDER,
        runner=RunnerKind.CODEX,
        model="gpt-5.3-codex",
        working_dir=tmp_path,
    )

    assert context.timeout_seconds == 3600


def test_native_config_is_required_for_runtime_load(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)

    with pytest.raises(FileNotFoundError, match="native Millrace config not found"):
        load_engine_config(workspace_root / "millrace.toml")


def test_stage_context_accepts_typed_procedure_injection_bundle(tmp_path: Path) -> None:
    context = StageContext.model_validate(
        {
            "stage": "qa",
            "runner": "codex",
            "model": "gpt-5.3-codex",
            "working_dir": tmp_path,
            "procedure_injection": {
                "stage": "qa",
                "rule": {
                    "stage": "qa",
                    "allowed_scopes": ["run", "workspace"],
                    "allowed_source_stages": ["builder", "qa"],
                    "max_procedures": 2,
                    "max_prompt_characters": 2400,
                },
                "procedures": [
                    {
                        "procedure_id": "proc.run.builder.001",
                        "scope": "run",
                        "source_stage": "builder",
                        "title": "Builder Procedure",
                        "summary": "Carry forward the builder fix sequence.",
                        "prompt_excerpt": "1. Apply the builder fix.\n2. Re-run QA.",
                        "evidence_refs": ["agents/runs/run-001/transition_history.jsonl"],
                        "original_characters": 50,
                        "injected_characters": 42,
                        "truncated": False,
                    }
                ],
                "candidate_count": 1,
                "selected_count": 1,
                "budget_characters": 2400,
                "used_characters": 42,
                "truncated_count": 0,
            },
        }
    )

    assert context.procedure_injection is not None
    assert isinstance(context.procedure_injection, ProcedureInjectionBundle)
    assert context.procedure_injection.rule == ProcedureRetrievalRule(
        stage=StageType.QA,
        allowed_scopes=(ProcedureScope.RUN, ProcedureScope.WORKSPACE),
        allowed_source_stages=(StageType.BUILDER, StageType.QA),
        max_procedures=2,
        max_prompt_characters=2400,
    )
    assert context.procedure_injection.procedures == (
        InjectedProcedure(
            procedure_id="proc.run.builder.001",
            scope=ProcedureScope.RUN,
            source_stage=StageType.BUILDER,
            title="Builder Procedure",
            summary="Carry forward the builder fix sequence.",
            prompt_excerpt="1. Apply the builder fix.\n2. Re-run QA.",
            evidence_refs=("agents/runs/run-001/transition_history.jsonl",),
            original_characters=50,
            injected_characters=42,
            truncated=False,
        ),
    )


def test_stage_context_accepts_typed_context_fact_injection_bundle(tmp_path: Path) -> None:
    context = StageContext.model_validate(
        {
            "stage": "builder",
            "runner": "codex",
            "model": "gpt-5.3-codex",
            "working_dir": tmp_path,
            "compounding_profile": "governed_plus",
            "context_fact_injection": {
                "stage": "builder",
                "rule": {
                    "stage": "builder",
                    "allowed_scopes": ["workspace"],
                    "allowed_source_stages": ["builder", "hotfix"],
                    "max_facts": 2,
                    "max_prompt_characters": 900,
                },
                "facts": [
                    {
                        "fact_id": "fact.workspace.builder.001",
                        "scope": "workspace",
                        "source_stage": "builder",
                        "title": "Builder fact",
                        "summary": "Preserve the generated audit trail.",
                        "statement_excerpt": "The builder path must preserve the audit trail.",
                        "tags": ["builder", "audit"],
                        "selection_reason": "pattern_match",
                        "original_characters": 47,
                        "injected_characters": 47,
                        "truncated": False,
                    }
                ],
                "candidate_count": 1,
                "selected_count": 1,
                "budget_characters": 900,
                "used_characters": 47,
                "truncated_count": 0,
            },
        }
    )

    assert context.compounding_profile == "governed_plus"
    assert context.context_fact_injection == ContextFactInjectionBundle(
        stage=StageType.BUILDER,
        rule=ContextFactRetrievalRule(
            stage=StageType.BUILDER,
            allowed_scopes=(ContextFactScope.WORKSPACE,),
            allowed_source_stages=(StageType.BUILDER, StageType.HOTFIX),
            max_facts=2,
            max_prompt_characters=900,
        ),
        facts=(
            InjectedContextFact(
                fact_id="fact.workspace.builder.001",
                scope=ContextFactScope.WORKSPACE,
                source_stage=StageType.BUILDER,
                title="Builder fact",
                summary="Preserve the generated audit trail.",
                statement_excerpt="The builder path must preserve the audit trail.",
                tags=("builder", "audit"),
                selection_reason=ContextFactSelectionReason.PATTERN_MATCH,
                original_characters=47,
                injected_characters=47,
                truncated=False,
            ),
        ),
        candidate_count=1,
        selected_count=1,
        budget_characters=900,
        used_characters=47,
        truncated_count=0,
    )


def test_runtime_paths_are_resolved_under_runtime_workspace(tmp_path: Path) -> None:
    workspace_root, config_path = runtime_workspace(tmp_path)
    loaded = load_engine_config(config_path)
    paths = build_runtime_paths(loaded.config)

    assert paths.root == workspace_root.resolve()
    assert paths.agents_dir == (workspace_root / "agents").resolve()
    assert paths.status_file == (workspace_root / "agents/status.md").resolve()
    assert paths.size_status_file == (workspace_root / "agents/size_status.md").resolve()
    assert paths.ideas_staging_dir == (workspace_root / "agents/ideas/staging").resolve()
    assert paths.ideas_specs_dir == (workspace_root / "agents/ideas/specs").resolve()
    assert paths.ideas_specs_reviewed_dir == (workspace_root / "agents/ideas/specs_reviewed").resolve()
    assert paths.objective_profile_sync_state_file == (
        workspace_root / "agents/objective/profile_sync_state.json"
    ).resolve()
    assert paths.audit_completion_manifest_file == (
        workspace_root / "agents/audit/completion_manifest.json"
    ).resolve()
    assert paths.compounding_dir == (workspace_root / "agents/compounding").resolve()
    assert paths.compounding_procedures_dir == (
        workspace_root / "agents/compounding/procedures"
    ).resolve()
    assert paths.compounding_context_facts_dir == (
        workspace_root / "agents/compounding/context_facts"
    ).resolve()
    assert paths.compounding_usage_records_dir == (
        workspace_root / "agents/compounding/usage"
    ).resolve()
    assert paths.compounding_lifecycle_records_dir == (
        workspace_root / "agents/compounding/lifecycle"
    ).resolve()
    assert paths.compounding_harness_candidates_dir == (
        workspace_root / "agents/compounding/harness_candidates"
    ).resolve()
    assert paths.compounding_harness_candidate_assets_dir == (
        workspace_root / "agents/compounding/harness_candidate_assets"
    ).resolve()
    assert paths.compounding_benchmark_results_dir == (
        workspace_root / "agents/compounding/benchmark_results"
    ).resolve()
    assert paths.compounding_harness_search_requests_dir == (
        workspace_root / "agents/compounding/harness_searches"
    ).resolve()
    assert paths.compounding_harness_recommendations_dir == (
        workspace_root / "agents/compounding/harness_recommendations"
    ).resolve()
    assert paths.lab_dir == (workspace_root / "agents/lab").resolve()
    assert paths.lab_harness_requests_dir == (
        workspace_root / "agents/lab/harness_requests"
    ).resolve()
    assert paths.lab_harness_proposals_dir == (
        workspace_root / "agents/lab/harness_proposals"
    ).resolve()
    assert paths.lab_harness_candidate_assets_dir == (
        workspace_root / "agents/lab/harness_candidate_assets"
    ).resolve()
    assert paths.lab_harness_comparisons_dir == (
        workspace_root / "agents/lab/harness_comparisons"
    ).resolve()
    assert paths.completion_manifest_plan_file == (
        workspace_root / "agents/reports/completion_manifest_plan.md"
    ).resolve()
    assert paths.staging_manifest_file == (workspace_root / "agents/staging_manifest.yml").resolve()
    assert paths.goalspec_goal_intake_records_dir == (
        workspace_root / "agents/.research_runtime/goalspec/goal_intake"
    ).resolve()
    assert paths.goalspec_completion_manifest_records_dir == (
        workspace_root / "agents/.research_runtime/goalspec/completion_manifest"
    ).resolve()
    assert paths.goalspec_spec_synthesis_records_dir == (
        workspace_root / "agents/.research_runtime/goalspec/spec_synthesis"
    ).resolve()
    assert paths.goalspec_spec_interview_records_dir == (
        workspace_root / "agents/.research_runtime/goalspec/spec_interview"
    ).resolve()
    assert paths.goalspec_spec_review_records_dir == (
        workspace_root / "agents/.research_runtime/goalspec/spec_review"
    ).resolve()
    assert paths.goalspec_lineage_dir == (
        workspace_root / "agents/.research_runtime/goalspec/lineage"
    ).resolve()
    assert paths.specs_index_file == (workspace_root / "agents/specs/index.json").resolve()
    assert paths.specs_stable_golden_dir == (
        workspace_root / "agents/specs/stable/golden"
    ).resolve()
    assert paths.specs_stable_phase_dir == (
        workspace_root / "agents/specs/stable/phase"
    ).resolve()
    assert paths.specs_questions_dir == (workspace_root / "agents/specs/questions").resolve()
    assert paths.specs_decisions_dir == (workspace_root / "agents/specs/decisions").resolve()
    assert paths.staging_repo_dir == (workspace_root / "staging").resolve()
    assert paths.historylog_dir == (workspace_root / "agents/historylog").resolve()
    assert paths.research_state_file == (workspace_root / "agents/research_state.json").resolve()
    assert paths.runs_dir == (workspace_root / "agents/runs").resolve()
    assert paths.diagnostics_dir == (workspace_root / "agents/diagnostics").resolve()
    assert paths.commands_incoming_dir == (workspace_root / "agents/.runtime/commands/incoming").resolve()
    assert paths.research_recovery_latch_file == (
        workspace_root / "agents/.runtime/research_recovery_latch.json"
    ).resolve()
    assert paths.progress_watchdog_state_file == (
        workspace_root / "agents/.research_runtime/progress_watchdog_state.json"
    ).resolve()
    assert paths.progress_watchdog_report_file == (
        workspace_root / "agents/.tmp/progress_watchdog_report.json"
    ).resolve()
    assert paths.incident_recurrence_ledger_file == (
        workspace_root / "agents/.research_runtime/incidents/recurrence_ledger.json"
    ).resolve()


def test_historylog_entry_names_use_canonical_utc_format() -> None:
    filename = format_historylog_entry_name(
        datetime(2026, 3, 16, 21, 5, 33, tzinfo=timezone.utc),
        stage="QA",
        task="123",
    )

    assert filename == "2026-03-16T21-05-33Z__stage-qa__task-123.md"


def test_historylog_entry_names_truncate_long_labels_with_hash_suffix() -> None:
    filename = format_historylog_entry_name(
        datetime(2026, 3, 16, 21, 5, 33, tzinfo=timezone.utc),
        stage="stage-" + ("alpha-" * 20),
        task="task-" + ("very-long-label-" * 20),
    )

    assert filename.startswith("2026-03-16T21-05-33Z__stage-stage-alpha-alpha")
    assert "__task-task-very-long-label-very-long-label-" in filename
    assert filename.endswith(".md")
    assert len(filename) < 200


def test_objective_contract_loader_accepts_valid_typed_json() -> None:
    contract = load_objective_contract(
        json.dumps(
            {
                "schema_version": "1.0",
                "objective_id": "OBJ-TYPED-001",
                "objective_root": ".",
                "completion": {
                    "authoritative_decision_file": "agents/reports/completion_decision.json",
                    "fallback_decision_file": "agents/reports/audit_gate_decision.json",
                    "require_task_store_cards_zero": True,
                    "require_open_gaps_zero": True,
                },
                "objective_profile": {"profile_id": "typed-profile"},
            }
        )
    )

    assert contract.objective_id == "OBJ-TYPED-001"
    assert contract.objective_profile["profile_id"] == "typed-profile"


def test_objective_contract_loader_rejects_malformed_typed_json() -> None:
    with pytest.raises(ValidationError):
        load_objective_contract(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "objective_id": "OBJ-BROKEN-001",
                    "objective_root": ".",
                    "completion": {},
                }
            )
        )


def test_compounding_contract_models_validate_and_normalize_payloads() -> None:
    artifact = ReusableProcedureArtifact.model_validate(
        {
            "procedure_id": "proc.builder.quickfix.001",
            "scope": "workspace",
            "source_run_id": "run-001",
            "source_stage": "builder",
            "title": " Recover from missing imports ",
            "summary": "Add the missing import before rerunning QA.",
            "procedure_markdown": "1. Add the import.\n2. Re-run QA.",
            "tags": ["python", "python", " quickfix "],
            "evidence_refs": ["agents/runs/run-001/qa.md", "agents/runs/run-001/qa.md"],
            "created_at": "2026-04-07T18:00:00Z",
        }
    )

    usage = ProcedureUsageRecord.model_validate(
        {
            "usage_id": "usage:001",
            "procedure_id": artifact.procedure_id,
            "run_id": "run-002",
            "stage": "qa",
            "disposition": "injected",
            "recorded_at": "2026-04-07T19:00:00Z",
            "execution_ref": "stage-exec-001",
        }
    )

    lifecycle = ProcedureLifecycleRecord.model_validate(
        {
            "record_id": "lifecycle:001",
            "procedure_id": artifact.procedure_id,
            "state": "promoted",
            "scope": "workspace",
            "changed_at": "2026-04-07T20:00:00Z",
            "changed_by": "advisor",
            "reason": "Validated across repeated runs.",
        }
    )

    assert artifact.scope is ProcedureScope.WORKSPACE
    assert artifact.source_stage is StageType.BUILDER
    assert artifact.tags == ("python", "quickfix")
    assert artifact.evidence_refs == ("agents/runs/run-001/qa.md",)
    assert usage.disposition is ProcedureUsageDisposition.INJECTED
    assert lifecycle.state is ProcedureLifecycleState.PROMOTED
    assert lifecycle.model_dump(mode="json")["changed_at"] == "2026-04-07T20:00:00Z"


def test_harness_contract_models_validate_and_normalize_payloads() -> None:
    candidate = HarnessCandidateArtifact.model_validate(
        {
            "candidate_id": "harness.candidate.compounding-001",
            "name": " Governed Plus Budget Trial ",
            "baseline_ref": "workspace.live",
            "benchmark_suite_ref": "preview.standard.v1",
            "state": "candidate",
            "changed_surfaces": [
                {
                    "kind": "config",
                    "target": "policies.compounding.profile",
                    "summary": "Switch the profile into governed plus mode.",
                }
            ],
            "compounding_policy_override": {
                "profile": "governed_plus",
                "governed_plus_budget_characters": 2800,
            },
            "prompt_asset_overrides": [
                {
                    "stage": "builder",
                    "source_ref": "package:agents/_start.md",
                    "candidate_prompt_file": "agents/compounding/harness_candidate_assets/search-001/builder.md",
                }
            ],
            "reviewer_note": " Bound review only ",
            "created_at": "2026-04-07T21:00:00Z",
            "created_by": "cli.fixture",
        }
    )

    result = HarnessBenchmarkResult.model_validate(
        {
            "result_id": "bench.20260407T210500Z.harness-candidate-compounding-001",
            "candidate_id": candidate.candidate_id,
            "baseline_ref": candidate.baseline_ref,
            "benchmark_suite_ref": candidate.benchmark_suite_ref,
            "status": "complete",
            "outcome": "changed",
            "started_at": "2026-04-07T21:05:00Z",
            "completed_at": "2026-04-07T21:05:05Z",
            "outcome_summary": {
                "selection_changed": False,
                "changed_config_fields": ["policies.compounding.profile"],
                "changed_stage_bindings": [],
                "baseline_mode_ref": "mode.standard@1.0.0",
                "candidate_mode_ref": "mode.standard@1.0.0",
                "message": "Candidate changes compounding policy only.",
            },
            "cost_summary": {
                "baseline_governed_plus_budget_characters": 3200,
                "candidate_governed_plus_budget_characters": 2800,
                "budget_delta_characters": -400,
            },
            "artifact_refs": ["agents/compounding/benchmark_results/example-baseline.json"],
        }
    )
    search = HarnessSearchRequestArtifact.model_validate(
        {
            "search_id": "search.20260407T211000Z",
            "baseline_ref": "workspace.live",
            "benchmark_suite_ref": "preview.standard.v1",
            "config_variants": [
                {
                    "profile": "baseline",
                    "governed_plus_budget_characters": 3200,
                }
            ],
            "asset_targets": [
                {
                    "stage": "builder",
                    "source_ref": "package:agents/_start.md",
                }
            ],
            "created_at": "2026-04-07T21:10:00Z",
            "created_by": "cli.search",
        }
    )
    recommendation = HarnessRecommendationArtifact.model_validate(
        {
            "recommendation_id": "recommend.search.20260407T211000Z",
            "search_id": search.search_id,
            "disposition": "recommend",
            "recommended_candidate_id": candidate.candidate_id,
            "recommended_result_id": result.result_id,
            "candidate_ids": [candidate.candidate_id],
            "benchmark_result_ids": [result.result_id],
            "summary": " Recommend the asset-backed candidate. ",
            "created_at": "2026-04-07T21:10:30Z",
            "created_by": "cli.search",
        }
    )

    assert candidate.state is HarnessCandidateState.CANDIDATE
    assert candidate.changed_surfaces == (
        HarnessChangedSurface(
            kind=HarnessChangedSurfaceKind.CONFIG,
            target="policies.compounding.profile",
            summary="Switch the profile into governed plus mode.",
        ),
    )
    assert candidate.prompt_asset_overrides == (
        HarnessCandidatePromptAssetOverride(
            stage=StageType.BUILDER,
            source_ref="package:agents/_start.md",
            candidate_prompt_file=Path("agents/compounding/harness_candidate_assets/search-001/builder.md"),
        ),
    )
    assert result.status is HarnessBenchmarkStatus.COMPLETE
    assert result.outcome is HarnessBenchmarkOutcome.CHANGED
    assert result.model_dump(mode="json")["completed_at"] == "2026-04-07T21:05:05Z"
    assert search.asset_targets[0].stage is StageType.BUILDER
    assert recommendation.disposition is HarnessRecommendationDisposition.RECOMMEND
    assert recommendation.summary == "Recommend the asset-backed candidate."


def test_lab_harness_contract_models_validate_and_normalize_payloads() -> None:
    request = LabHarnessRequestArtifact.model_validate(
        {
            "request_id": "lab.request.search.20260407T211000Z.20260407T220000Z",
            "source_kind": "recommendation",
            "source_recommendation_id": "recommend.search.20260407T211000Z",
            "source_search_id": "search.20260407T211000Z",
            "source_candidate_ids": ["harness.search.search.20260407T211000Z.config.baseline"],
            "source_benchmark_result_ids": ["bench.20260407T211500Z.harness-search-example"],
            "created_at": "2026-04-07T22:00:00Z",
            "created_by": " lab.fixture ",
        }
    )
    proposal = LabHarnessProposalArtifact.model_validate(
        {
            "proposal_id": "lab.harness.proposal.lab-request-search-20260407T211000Z-20260407T220000Z.harness-search-example",
            "request_id": request.request_id,
            "source_candidate_id": "harness.search.search.20260407T211000Z.config.baseline",
            "source_benchmark_result_id": "bench.20260407T211500Z.harness-search-example",
            "state": "proposal",
            "name": " Lab proposal from bounded runtime candidate ",
            "summary": " Copy the bounded candidate into the off-path lab lane. ",
            "changed_surfaces": [
                {
                    "kind": "config",
                    "target": "policies.compounding.profile",
                    "summary": "Switch the profile into governed plus mode.",
                }
            ],
            "compounding_policy_override": {
                "profile": "governed_plus",
                "governed_plus_budget_characters": 2800,
            },
            "prompt_asset_overrides": [
                {
                    "stage": "builder",
                    "source_ref": "package:agents/_start.md",
                    "candidate_prompt_file": "agents/lab/harness_candidate_assets/example/builder.md",
                }
            ],
            "created_at": "2026-04-07T22:00:00Z",
            "created_by": "lab.fixture",
        }
    )
    comparison = LabHarnessComparisonArtifact.model_validate(
        {
            "comparison_id": "lab.compare.lab-request-search-20260407T211000Z-20260407T220000Z",
            "request_id": request.request_id,
            "source_recommendation_id": request.source_recommendation_id,
            "proposal_ids": [proposal.proposal_id],
            "rows": [
                {
                    "source_candidate_id": proposal.source_candidate_id,
                    "source_benchmark_result_id": proposal.source_benchmark_result_id,
                    "proposal_id": proposal.proposal_id,
                    "benchmark_status": "complete",
                    "benchmark_outcome": "changed",
                    "selection_changed": False,
                    "changed_config_fields": ["policies.compounding.profile"],
                    "changed_stage_bindings": ["builder"],
                    "budget_delta_characters": -400,
                    "summary": "Bounded runtime benchmark showed a builder prompt delta.",
                }
            ],
            "summary": " Generated one off-path proposal. ",
            "created_at": "2026-04-07T22:00:00Z",
            "created_by": "lab.fixture",
        }
    )

    assert request.source_kind is LabHarnessRequestSourceKind.RECOMMENDATION
    assert request.created_by == "lab.fixture"
    assert proposal.state is LabHarnessProposalState.PROPOSAL
    assert proposal.name == "Lab proposal from bounded runtime candidate"
    assert proposal.prompt_asset_overrides == (
        HarnessCandidatePromptAssetOverride(
            stage=StageType.BUILDER,
            source_ref="package:agents/_start.md",
            candidate_prompt_file=Path("agents/lab/harness_candidate_assets/example/builder.md"),
        ),
    )
    assert comparison.rows == (
        LabHarnessComparisonRow(
            source_candidate_id=proposal.source_candidate_id,
            source_benchmark_result_id=proposal.source_benchmark_result_id,
            proposal_id=proposal.proposal_id,
            benchmark_status="complete",
            benchmark_outcome="changed",
            selection_changed=False,
            changed_config_fields=("policies.compounding.profile",),
            changed_stage_bindings=("builder",),
            budget_delta_characters=-400,
            summary="Bounded runtime benchmark showed a builder prompt delta.",
        ),
    )


def test_context_fact_contract_models_validate_and_normalize_payloads() -> None:
    artifact = ContextFactArtifact.model_validate(
        {
            "fact_id": "fact.workspace.build.001",
            "scope": "workspace",
            "lifecycle_state": "promoted",
            "source_run_id": "run-101",
            "source_stage": "builder",
            "title": " Build root requires editable install ",
            "statement": "The Millrace source tree requires `pip install -e '.[dev]'` before local test runs.",
            "summary": "Local source-checkout verification depends on the editable dev install.",
            "tags": ["python", " python ", "packaging"],
            "evidence_refs": ["docs/setup.md", "docs/setup.md"],
            "created_at": "2026-04-07T18:00:00Z",
            "observed_at": "2026-04-07T18:15:00Z",
        }
    )

    stale = ContextFactArtifact.model_validate(
        {
            "fact_id": "fact.workspace.qa.001",
            "scope": "workspace",
            "lifecycle_state": "stale",
            "source_run_id": "run-102",
            "source_stage": "qa",
            "title": "Old QA baseline",
            "statement": "The previous QA baseline no longer reflects current failures.",
            "summary": "The fact remains recorded but should not be reused until refreshed.",
            "created_at": "2026-04-07T19:00:00Z",
            "stale_reason": "Superseded by newer validation evidence.",
        }
    )

    assert artifact.scope is ContextFactScope.WORKSPACE
    assert artifact.lifecycle_state is ContextFactLifecycleState.PROMOTED
    assert artifact.tags == ("python", "packaging")
    assert artifact.evidence_refs == ("docs/setup.md",)
    assert stale.lifecycle_state is ContextFactLifecycleState.STALE
    assert stale.model_dump(mode="json")["created_at"] == "2026-04-07T19:00:00Z"


def test_context_fact_contract_models_reject_invalid_or_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ContextFactArtifact.model_validate(
            {
                "fact_id": "fact.workspace.invalid.001",
                "scope": "workspace",
                "lifecycle_state": "stale",
                "source_run_id": "run-001",
                "source_stage": "builder",
                "title": "Broken",
                "statement": "Broken",
                "summary": "Broken",
                "created_at": "2026-04-07T18:00:00Z",
            }
        )

    with pytest.raises(ValidationError):
        ContextFactArtifact.model_validate(
            {
                "fact_id": "bad id with spaces",
                "scope": "workspace",
                "lifecycle_state": "promoted",
                "source_run_id": "run-001",
                "source_stage": "builder",
                "title": "Broken",
                "statement": "Broken",
                "summary": "Broken",
                "created_at": "2026-04-07T18:00:00Z",
                "unexpected": True,
            }
        )


def test_compounding_contract_models_reject_invalid_or_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ReusableProcedureArtifact.model_validate(
            {
                "procedure_id": "bad id with spaces",
                "source_run_id": "run-001",
                "source_stage": "builder",
                "title": "Broken",
                "summary": "Broken",
                "procedure_markdown": "Broken",
                "created_at": "2026-04-07T18:00:00Z",
            }
        )

    with pytest.raises(ValidationError):
        ProcedureUsageRecord.model_validate(
            {
                "usage_id": "usage:001",
                "procedure_id": "proc.builder.quickfix.001",
                "run_id": "run-002",
                "stage": "qa",
                "disposition": "skipped",
                "recorded_at": "2026-04-07T19:00:00Z",
                "unexpected": True,
            }
        )

    with pytest.raises(ValidationError):
        HarnessCandidateArtifact.model_validate(
            {
                "candidate_id": "bad id with spaces",
                "name": "Broken Candidate",
                "baseline_ref": "workspace.live",
                "benchmark_suite_ref": "preview.standard.v1",
                "state": "candidate",
                "changed_surfaces": [],
                "created_at": "2026-04-07T21:00:00Z",
                "created_by": "cli.fixture",
            }
        )


def test_invalid_native_config_fails_validation(tmp_path: Path) -> None:
    config_path = tmp_path / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[engine]",
                'mode = "once"',
                "poll_interval_seconds = 0",
                "",
                "[unexpected]",
                'value = "boom"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_engine_config(config_path)


def test_native_config_loads_spec_interview_policy(tmp_path: Path) -> None:
    config_path = tmp_path / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[engine]",
                'mode = "once"',
                "",
                "[paths]",
                'workspace = "."',
                'agents_dir = "agents"',
                "",
                "[research]",
                'mode = "goalspec"',
                'interview_policy = "always"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_engine_config(config_path)

    assert loaded.config.research.interview_policy.value == "always"


def test_native_config_loads_typed_complexity_routing_policy(tmp_path: Path) -> None:
    config_path = tmp_path / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[engine]",
                'mode = "once"',
                "",
                "[paths]",
                'workspace = "."',
                'agents_dir = "agents"',
                "",
                "[policies.complexity]",
                "enabled = true",
                'default_band = "involved"',
                "",
                "[policies.complexity.profiles.moderate]",
                'kind = "model_profile"',
                'id = "model.workspace.moderate"',
                'version = "1.0.0"',
                "",
                "[policies.complexity.profiles.involved]",
                'kind = "model_profile"',
                'id = "model.workspace.involved"',
                'version = "1.0.0"',
                "",
                "[policies.complexity.profiles.complex]",
                'kind = "model_profile"',
                'id = "model.workspace.complex"',
                'version = "1.0.0"',
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_engine_config(config_path)

    assert loaded.config.policies.complexity.enabled is True
    assert loaded.config.policies.complexity.default_band is ComplexityBand.INVOLVED
    assert loaded.config.policies.complexity.profiles.involved.id == "model.workspace.involved"
    assert loaded.config.boundaries.classify_field("policies.complexity") is ConfigApplyBoundary.STAGE_BOUNDARY


def test_native_config_loads_typed_compounding_policy(tmp_path: Path) -> None:
    config_path = tmp_path / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[engine]",
                'mode = "once"',
                "",
                "[paths]",
                'workspace = "."',
                'agents_dir = "agents"',
                "",
                "[policies.compounding]",
                'profile = "governed_plus"',
                "governed_plus_budget_characters = 2800",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_engine_config(config_path)

    assert loaded.config.policies.compounding.profile is CompoundingProfile.GOVERNED_PLUS
    assert loaded.config.policies.compounding.governed_plus_budget_characters == 2800
    assert loaded.config.boundaries.classify_field("policies.compounding") is ConfigApplyBoundary.STAGE_BOUNDARY


def test_native_config_loads_typed_watcher_settings(tmp_path: Path) -> None:
    config_path = tmp_path / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[engine]",
                'mode = "once"',
                'idle_mode = "watch"',
                "poll_interval_seconds = 30",
                "inter_task_delay_seconds = 0",
                "",
                "[paths]",
                'workspace = "."',
                'agents_dir = "agents"',
                "",
                "[execution]",
                'integration_mode = "never"',
                "quickfix_max_attempts = 2",
                "run_update_on_empty = false",
                "",
                "[research]",
                'mode = "stub"',
                "",
                "[watchers]",
                "debounce_seconds = 1.25",
                'roots = ["config_file", "commands_incoming"]',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_engine_config(config_path)

    assert loaded.config.watchers.debounce_seconds == pytest.approx(1.25)
    assert loaded.config.watchers.roots == (
        WatchRoot.CONFIG_FILE,
        WatchRoot.COMMANDS_INCOMING,
    )


def test_native_config_loads_typed_sizing_settings(tmp_path: Path) -> None:
    config_path = tmp_path / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[engine]",
                'mode = "once"',
                "",
                "[paths]",
                'workspace = "."',
                'agents_dir = "agents"',
                "",
                "[sizing]",
                'mode = "hybrid"',
                "",
                "[sizing.repo]",
                "file_count_threshold = 12",
                "nonempty_line_count_threshold = 345",
                "",
                "[sizing.task]",
                "file_count_threshold = 3",
                "nonempty_line_count_threshold = 144",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_engine_config(config_path)

    assert loaded.config.sizing.mode == "hybrid"
    assert loaded.config.sizing.repo.file_count_threshold == 12
    assert loaded.config.sizing.repo.nonempty_line_count_threshold == 345
    assert loaded.config.sizing.task.file_count_threshold == 3
    assert loaded.config.sizing.task.nonempty_line_count_threshold == 144


def test_native_config_accepts_legacy_task_loc_threshold_key(tmp_path: Path) -> None:
    config_path = tmp_path / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[engine]",
                'mode = "once"',
                "",
                "[paths]",
                'workspace = "."',
                'agents_dir = "agents"',
                "",
                "[sizing.task]",
                "body_nonempty_line_count_threshold = 77",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_engine_config(config_path)

    assert loaded.config.sizing.task.nonempty_line_count_threshold == 77


def test_config_boundaries_classify_changed_fields_by_runtime_application_point(tmp_path: Path) -> None:
    _, config_path = runtime_workspace(tmp_path)
    loaded = load_engine_config(config_path)

    stage_config = loaded.config.model_copy(deep=True)
    stage_config.execution.quickfix_max_attempts = 5
    stage_fields = diff_config_fields(loaded.config, stage_config)
    assert stage_fields == ("execution.quickfix_max_attempts",)
    assert loaded.config.boundaries.classify_fields(stage_fields) is ConfigApplyBoundary.STAGE_BOUNDARY

    compounding_config = loaded.config.model_copy(deep=True)
    compounding_config.policies.compounding.profile = CompoundingProfile.GOVERNED_PLUS
    compounding_fields = diff_config_fields(loaded.config, compounding_config)
    assert compounding_fields == ("policies.compounding.profile",)
    assert loaded.config.boundaries.classify_fields(compounding_fields) is ConfigApplyBoundary.STAGE_BOUNDARY

    cycle_config = loaded.config.model_copy(deep=True)
    cycle_config.engine.idle_mode = "poll"
    cycle_fields = diff_config_fields(loaded.config, cycle_config)
    assert cycle_fields == ("engine.idle_mode",)
    assert loaded.config.boundaries.classify_fields(cycle_fields) is ConfigApplyBoundary.CYCLE_BOUNDARY

    sizing_config = loaded.config.model_copy(deep=True)
    sizing_config.sizing.mode = "repo" if loaded.config.sizing.mode != "repo" else "task"
    sizing_fields = diff_config_fields(loaded.config, sizing_config)
    assert sizing_fields == ("sizing.mode",)
    assert loaded.config.boundaries.classify_fields(sizing_fields) is ConfigApplyBoundary.CYCLE_BOUNDARY

    watcher_config = loaded.config.model_copy(deep=True)
    watcher_config.watchers.roots = (WatchRoot.CONFIG_FILE,)
    watcher_fields = diff_config_fields(loaded.config, watcher_config)
    assert watcher_fields == ("watchers.roots",)
    assert loaded.config.boundaries.classify_fields(watcher_fields) is ConfigApplyBoundary.CYCLE_BOUNDARY

    live_config = loaded.config.model_copy(deep=True)
    live_config.engine.poll_interval_seconds = loaded.config.engine.poll_interval_seconds + 1
    live_fields = diff_config_fields(loaded.config, live_config)
    assert live_fields == ("engine.poll_interval_seconds",)
    assert loaded.config.boundaries.classify_fields(live_fields) is ConfigApplyBoundary.LIVE_IMMEDIATE

    startup_config = loaded.config.model_copy(deep=True)
    startup_config.paths.agents_dir = Path("other-agents")
    startup_fields = diff_config_fields(loaded.config, startup_config)
    assert startup_fields == ("paths.agents_dir",)
    assert loaded.config.boundaries.classify_fields(startup_fields) is ConfigApplyBoundary.STARTUP_ONLY
