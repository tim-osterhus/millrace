# Workflow config (flags only)

## INITIALIZED=false
## INTEGRATION_MODE=Manual
## INTEGRATION_COUNT=0
## INTEGRATION_TARGET=0
## HEADLESS_PERMISSIONS=Normal
## SHELL_TEMPLATES=Bash
## COMPLEXITY_ROUTING=Off
## RUN_UPDATE_ON_EMPTY=On
# ORCH_ALLOW_SEARCH allowed: on|off. Fallback: off.
## ORCH_ALLOW_SEARCH=off
# ORCH_ALLOW_SEARCH_EXCEPTION allowed: on|off. Fallback: off.
# - Keep `off` for strict clean-room operation.
# - Set to `on` only when intentionally enabling search.
## ORCH_ALLOW_SEARCH_EXCEPTION=off
# Size routing controls:
# - SIZE_METRIC_MODE allowed: repo|task|hybrid. Fallback: hybrid.
# - `repo` uses repository-wide file/LOC thresholds.
# - `task` uses active-card signals and promotes LARGE only when at least 2 of 3 are true:
#   files-to-touch threshold, files-to-touch LOC threshold, complexity tier (involved|complex).
# - `hybrid` triggers LARGE when either repo thresholds trip OR task signals reach 2-of-3.
## SIZE_METRIC_MODE=hybrid
## LARGE_FILES_THRESHOLD=999999999
## LARGE_LOC_THRESHOLD=999999999
## TASK_LARGE_FILES_THRESHOLD=999999999
## TASK_LARGE_LOC_THRESHOLD=999999999
## USAGE_AUTOPAUSE_MODE=On
# Usage sampler provider controls:
# - USAGE_SAMPLER_PROVIDER allowed: codex|env|command. Recommended: codex.
# - USAGE_SAMPLER_CACHE_MAX_AGE_SECS allowed: integer >= 0. `0` disables fresh-cache reuse.
# - USAGE_SAMPLER_ORCH_CMD / USAGE_SAMPLER_RESEARCH_CMD are required when provider=command.
# - CODEX_AUTH_SOURCE_DIR points to read-only/shared Codex auth source (typically host mount).
# - Leave it empty to use the loop default of `$HOME/.codex`; set an absolute path only when auth is mounted elsewhere.
# - CODEX_RUNTIME_HOME points to writable in-container Codex home used by loops/sampler.
## USAGE_SAMPLER_PROVIDER=codex
## USAGE_SAMPLER_CACHE_MAX_AGE_SECS=900
## USAGE_SAMPLER_ORCH_CMD=
## USAGE_SAMPLER_RESEARCH_CMD=
## CODEX_AUTH_SOURCE_DIR=
## CODEX_RUNTIME_HOME=agents/.tmp/codex-runtime-home
# Weekly auto-pause semantics:
# - Remaining contract (recommended): pause when current remaining <= remaining threshold.
# - Consumed contract (optional): pause when current consumed >= consumed threshold.
# - Legacy compatibility: *_WEEKLY_USAGE_THRESHOLD is treated as remaining threshold.
## ORCH_WEEKLY_USAGE_REMAINING_THRESHOLD=10
## ORCH_WEEKLY_USAGE_CONSUMED_THRESHOLD=
## ORCH_WEEKLY_USAGE_THRESHOLD=
## ORCH_WEEKLY_REFRESH_UTC=TUE 03:45
## RESEARCH_WEEKLY_USAGE_REMAINING_THRESHOLD=10
## RESEARCH_WEEKLY_USAGE_CONSUMED_THRESHOLD=
## RESEARCH_WEEKLY_USAGE_THRESHOLD=
## RESEARCH_WEEKLY_REFRESH_UTC=TUE 03:45

# Orchestrator inter-task pacing controls:
# - ORCH_INTER_TASK_DELAY_MODE allowed: On|ON|on|Off|OFF|off|true|TRUE|false|FALSE; empty means implicit-on when secs > 0.
# - ORCH_INTER_TASK_DELAY_SECS allowed: integer >= 0; delay is applied only when value is > 0.
## ORCH_INTER_TASK_DELAY_MODE=
## ORCH_INTER_TASK_DELAY_SECS=0

# Clean-room network guard controls (loop hook + per-cycle audit artifacts):
# - NETWORK_GUARD_MODE controls whether runtime guard hooks are active.
# - ORCH_NETWORK_GUARD_POLICY should stay `deny` for build-phase no-egress enforcement when guard mode is on.
# - RESEARCH_NETWORK_GUARD_POLICY defaults to `deny`; explicit exceptions must be acknowledged.
# - Guard state artifacts are emitted per cycle/stage under run logs.
## NETWORK_GUARD_MODE=off
## ORCH_NETWORK_GUARD_POLICY=deny
## RESEARCH_NETWORK_GUARD_POLICY=deny
# ORCH_NETWORK_POLICY_EXCEPTION / RESEARCH_NETWORK_POLICY_EXCEPTION allowed: on|off. Fallback: off.
# - Required when guard mode is enabled with allow policy under strict isolation defaults.
## ORCH_NETWORK_POLICY_EXCEPTION=off
## RESEARCH_NETWORK_POLICY_EXCEPTION=off

# Environment preflight controls:
# - ENV_PREFLIGHT_MODE allowed: on|off. Fallback: on.
# - ENV_PREFLIGHT_TRANSPORT_CHECK allowed: on|off. Fallback: on.
## ENV_PREFLIGHT_MODE=on
## ENV_PREFLIGHT_TRANSPORT_CHECK=on

# Network outage resilience controls (shared by orchestrate + research loops):
# - If classifier tags a stage failure as transient network/API transport outage, loops enter `### NET_WAIT`.
# - Probe loop uses exponential backoff and auto-resumes on probe success.
# - NETWORK_OUTAGE_MAX_PROBES=0 means unbounded probe attempts.
# - NETWORK_OUTAGE_POLICY controls escalation after probe-budget exhaustion:
#   - pause_resume (default): keep waiting in NET_WAIT; do not escalate.
#   - incident: route outage exhaustion into incident intake.
#   - blocker: route outage exhaustion back into blocker/troubleshoot flow.
# - NETWORK_OUTAGE_ROUTE_TO_BLOCKER / NETWORK_OUTAGE_ROUTE_TO_INCIDENT can force routing regardless of policy.
## NETWORK_OUTAGE_RESILIENCE_MODE=on
## NETWORK_OUTAGE_WAIT_INITIAL_SECS=15
## NETWORK_OUTAGE_WAIT_MAX_SECS=300
## NETWORK_OUTAGE_MAX_PROBES=0
## NETWORK_OUTAGE_PROBE_TIMEOUT_SECS=5
## NETWORK_OUTAGE_PROBE_HOST=api.openai.com
## NETWORK_OUTAGE_PROBE_PORT=443
## NETWORK_OUTAGE_PROBE_CMD=
## NETWORK_OUTAGE_POLICY=pause_resume
## NETWORK_OUTAGE_ROUTE_TO_BLOCKER=off
## NETWORK_OUTAGE_ROUTE_TO_INCIDENT=off

# Research autonomy + dispatch controls.
# MAX_AUTONOMY_MODE allowed: Off|On|Max. Fallback: On.
## MAX_AUTONOMY_MODE=On
# RESEARCH_MODE allowed: AUTO|GOALSPEC|INCIDENT|AUDIT. Fallback: AUTO.
## RESEARCH_MODE=AUTO
# SPEC_INTERROGATION_ROUNDS allowed: integer >= 0. Fallback: 2.
## SPEC_INTERROGATION_ROUNDS=2
# PHASE_INTERROGATION_ROUNDS allowed: integer >= 0. Fallback: 1.
## PHASE_INTERROGATION_ROUNDS=1
# INCIDENT_FIXSPEC_INTERROGATION_ROUNDS allowed: integer >= 0. Fallback: 1.
## INCIDENT_FIXSPEC_INTERROGATION_ROUNDS=1
# SPEC_QUALITY_THRESHOLD allowed: decimal 0..1. Fallback: 0.75.
## SPEC_QUALITY_THRESHOLD=0.75
# PHASE_ASSUMPTIONS_BUDGET allowed: integer >= 1. Fallback: 8.
## PHASE_ASSUMPTIONS_BUDGET=8
# SPEC_QUALITY_FAIL_MAX allowed: integer >= 1. Fallback: 2.
## SPEC_QUALITY_FAIL_MAX=2
# INCIDENT_MAX_CYCLES allowed: integer >= 1. Fallback: 3.
## INCIDENT_MAX_CYCLES=3
# AUDIT_TRIGGER allowed: always|queue_empty|manual. Fallback: queue_empty.
## AUDIT_TRIGGER=queue_empty
# RESEARCH_IDLE_MODE allowed: poll|watch. Fallback: poll.
## RESEARCH_IDLE_MODE=poll
# RESEARCH_IDLE_POLL_SECS allowed: integer >= 1. Fallback: 60.
## RESEARCH_IDLE_POLL_SECS=60
# RESEARCH_LOCKING allowed: on|off. Fallback: off.
## RESEARCH_LOCKING=off
# RESEARCH_ALLOW_SEARCH allowed: on|off. Fallback: off.
## RESEARCH_ALLOW_SEARCH=off
# RESEARCH_ALLOW_SEARCH_EXCEPTION allowed: on|off. Fallback: off.
# - Keep `off` for strict clean-room operation.
# - Set to `on` only when intentionally enabling search.
## RESEARCH_ALLOW_SEARCH_EXCEPTION=off

# Objective contract (A-core):
# - OBJECTIVE_CONTRACT_SCHEMA_FILE points to schema used by objective contract validator.
## OBJECTIVE_CONTRACT_SCHEMA_FILE=agents/objective/contract.schema.json
# - OBJECTIVE_CONTRACT_FILE points to project-supplied objective contract instance.
## OBJECTIVE_CONTRACT_FILE=agents/objective/contract.yaml
# - COMMAND_CONTRACT_REPORT_FILE is written by command contract guard and consumed by completion gate checks.
## COMMAND_CONTRACT_REPORT_FILE=agents/reports/command_contract.json

# Marathon audit harness adapter (repo-specific, consumed by generic audit planner/runner/gatekeeper flow).
# - AUDIT_COMPLETION_MANIFEST must point to a configured JSON manifest listing required completion commands.
## AUDIT_COMPLETION_MANIFEST=agents/audit/completion_manifest.json
# - AUDIT_STRICT_CONTRACT_FILE points to strict completion-gate policy for the current objective.
## AUDIT_STRICT_CONTRACT_FILE=agents/audit/strict_contract.json
# - AUDIT_COMPLETENESS_MODE allowed: standard|comprehensive. `comprehensive` forbids sampled completion commands (`--fast`, `--sample`, `subset`) and enforces skip budget.
## AUDIT_COMPLETENESS_MODE=comprehensive
# - AUDIT_COMPREHENSIVE_MAX_SKIPS allowed: integer >= 0. Used only when AUDIT_COMPLETENESS_MODE=comprehensive.
## AUDIT_COMPREHENSIVE_MAX_SKIPS=0

#   - blocker rate delta must be <= max blocker-rate delta (ratio 0..1).
## GOVERNANCE_CANARY_MIN_OBJECTIVE_SHARE_DELTA=0.01
## GOVERNANCE_CANARY_MIN_OUTCOME_DELTA_PROXY_DELTA=0.01
## GOVERNANCE_CANARY_MAX_BLOCKER_RATE_DELTA=0

# Strict generation controls.
# TASKMASTER_COMPLEXITY_PROFILE allowed: auto|trivial|simple|moderate|involved|complex|massive. Fallback: auto.
# - `auto` resolves profile from staged/queued goal/spec artifacts (explicit metadata first, then deterministic heuristic).
# - Preset card floors/targets:
#   - when specs carry explicit `decomposition_profile`, lint derives per-spec floors/targets from that field and sums them across the package
#   - derived profile presets:
#     - trivial: min 1 / target 2
#     - simple: min 3 / target 5
#     - moderate: min 6 / target 10
#     - involved: min 12 / target 16
#     - complex: min 20 / target 28
#     - massive: min 30 / target 45
#   - `TASKMASTER_MIN/TARGET_TOTAL_CARDS` remain optional explicit overrides when a project wants a package-level floor above or below the derived sum
## TASKMASTER_COMPLEXITY_PROFILE=auto
# TASKMASTER_MIN_CARDS_PER_SPEC allowed: integer >= 1. Fallback: 1. Used as fallback only when a spec lacks explicit `decomposition_profile`.
## TASKMASTER_MIN_CARDS_PER_SPEC=1
# TASKMASTER_MAX_CARDS_PER_SPEC allowed: integer >= 0. `0` disables the max cap. Fallback: 60.
## TASKMASTER_MAX_CARDS_PER_SPEC=60
# TASKMASTER_TARGET_CARDS_PER_SPEC allowed: integer >= 0. `0` disables fallback per-spec target. Used only when a spec lacks explicit `decomposition_profile`.
## TASKMASTER_TARGET_CARDS_PER_SPEC=0
# TASKMASTER_MIN_TOTAL_CARDS allowed: integer >= 0. `0` disables package floor. Fallback: profile-derived.
## TASKMASTER_MIN_TOTAL_CARDS=0
# TASKMASTER_TARGET_TOTAL_CARDS allowed: integer >= 0. `0` disables package target. Fallback: profile-derived.
## TASKMASTER_TARGET_TOTAL_CARDS=0
# TASKCARD_FORMAT_STRICT allowed: on|off. Fallback: on.
## TASKCARD_FORMAT_STRICT=on
# TASKCARD_ENFORCE_EXECUTION_TEMPLATE allowed: on|off. Fallback: on.
## TASKCARD_ENFORCE_EXECUTION_TEMPLATE=on
# TASKCARD_PHASE_WORKPLAN_COVERAGE allowed: on|off. Fallback: on.
## TASKCARD_PHASE_WORKPLAN_COVERAGE=on
# TASKCARD_MAX_PHASE_STEPS_PER_CARD allowed: integer >= 0. `0` disables the cap. Fallback: 2.
## TASKCARD_MAX_PHASE_STEPS_PER_CARD=2
# TASKCARD_TARGET_SHORTFALL_MODE allowed: warn|error|auto. `auto` blocks below-target sets for complex/massive campaigns and warns otherwise.
## TASKCARD_TARGET_SHORTFALL_MODE=auto
# TASKCARD_SCOPE_LINT allowed: on|off. Fallback: on.
## TASKCARD_SCOPE_LINT=on
# SPEC_DECOMPOSITION_GOVERNANCE allowed: on|off. Fallback: on.
## SPEC_DECOMPOSITION_GOVERNANCE=on

# Stage retry controls (research loop stage-level retries).
# STAGE_RETRY_MAX allowed: integer >= 0. Fallback: 1.
## STAGE_RETRY_MAX=1
# STAGE_RETRY_BACKOFF_SECS allowed: integer >= 0. Fallback: 5.
## STAGE_RETRY_BACKOFF_SECS=5
# RESEARCH_FAILURE_BACKOFF_SECS allowed: integer >= 1. Fallback: 60.
## RESEARCH_FAILURE_BACKOFF_SECS=60

# Stage search/timeouts.
# Search values allowed: on|off (fallback off). Timeout values allowed: integer >= 1 (fallback 5400).
#
# Active condensed GoalSpec stages:
## GOAL_INTAKE_SEARCH=off
## GOAL_INTAKE_TIMEOUT_SECS=5400
## OBJECTIVE_PROFILE_SYNC_SEARCH=off
## OBJECTIVE_PROFILE_SYNC_TIMEOUT_SECS=5400
## SPEC_SYNTHESIS_SEARCH=off
## SPEC_SYNTHESIS_TIMEOUT_SECS=5400
## SPEC_REVIEW_SEARCH=off
## SPEC_REVIEW_TIMEOUT_SECS=5400
#
# Compatibility fallback keys for the pre-condense GoalSpec flow:
## ARTICULATE_SEARCH=off
## ARTICULATE_TIMEOUT_SECS=5400
## ANALYZE_SEARCH=off
## ANALYZE_TIMEOUT_SECS=5400
## CLARIFY_SEARCH=off
## CLARIFY_TIMEOUT_SECS=5400
## TASKMASTER_SEARCH=off
## TASKMASTER_TIMEOUT_SECS=5400
## TASKAUDIT_SEARCH=off
## TASKAUDIT_TIMEOUT_SECS=5400

# GoalSpec interrogation stage controls + reserved stage families for later expansion.
## CRITIC_SEARCH=off
## CRITIC_TIMEOUT_SECS=5400
## DESIGNER_SEARCH=off
## DESIGNER_TIMEOUT_SECS=5400
## PHASESPLIT_SEARCH=off
## PHASESPLIT_TIMEOUT_SECS=5400
## INCIDENT_INTAKE_SEARCH=off
## INCIDENT_INTAKE_TIMEOUT_SECS=5400
## INCIDENT_RESOLVE_SEARCH=off
## INCIDENT_RESOLVE_TIMEOUT_SECS=5400
## INCIDENT_ARCHIVE_SEARCH=off
## INCIDENT_ARCHIVE_TIMEOUT_SECS=5400
## AUDIT_INTAKE_SEARCH=off
## AUDIT_INTAKE_TIMEOUT_SECS=5400
## AUDIT_VALIDATE_SEARCH=off
## AUDIT_VALIDATE_TIMEOUT_SECS=5400
## AUDIT_GATEKEEPER_SEARCH=off
## AUDIT_GATEKEEPER_TIMEOUT_SECS=5400
## MECHANIC_SEARCH=off
## MECHANIC_TIMEOUT_SECS=5400

## UI_VERIFY_MODE=manual
## UI_VERIFY_EXECUTOR=playwright
## UI_VERIFY_ANALYZER=none
## UI_VERIFY_COVERAGE=smoke
## UI_VERIFY_QUOTA_GUARD=off
## UI_VERIFY_BROWSER_PROFILE=playwright
