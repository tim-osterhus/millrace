#!/usr/bin/env bash
set -euo pipefail

# Foreground research loop (local runner).
#
# Millrace mode dispatcher:
# - GoalSpec: condensed spec engineering pipeline
#   (goal/raw|articulated -> goal_intake -> staging -> spec_synthesis -> spec_review -> specs/tasks)
#   using template contracts in agents/specs/templates/golden_spec_template.md and
#   agents/specs/templates/phase_spec_template.md
# - Incident: blocker/incident queue progression using
#   agents/specs/templates/incident_spec_template.md
# - Audit: task backlog audit/merge checks plus marathon completion auditing
#   (fullexpectations + gaps scan + completion verdict) using
#   agents/specs/templates/audit_template.md
#
# The loop maintains deterministic research state and contract artifacts and
# never writes execution status markers (`agents/status.md`).
# Canonical marker ownership and unknown marker policy: agents/status_contract.md.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

resolve_repo_root() {
  local candidate
  for candidate in "$SCRIPT_DIR/.." "$SCRIPT_DIR/../../.."; do
    if [ -f "$candidate/agents/_orchestrate.md" ]; then
      (cd "$candidate" && pwd -P)
      return 0
    fi
  done
  (cd "$SCRIPT_DIR/.." && pwd -P)
}

REPO_ROOT="$(resolve_repo_root)"
cd "$REPO_ROOT"

RAW_DIR="agents/ideas/raw"
ARTICULATED_DIR="agents/ideas/articulated"
STAGING_DIR="agents/ideas/staging"
LATER_DIR="agents/ideas/later"
QUEUE_SPECS_DIR="agents/ideas/specs"
REVIEWED_SPECS_DIR="agents/ideas/specs_reviewed"
GOAL_DIR="agents/ideas/goal"
IDEAS_ARCHIVED_DIR="agents/ideas/archived"
IDEAS_FINISHED_DIR="agents/ideas/finished"
IDEAS_AMBIGUOUS_DIR="agents/ideas/ambiguous"
INCIDENTS_ROOT_DIR="agents/ideas/incidents"
INCIDENT_INCOMING_DIR="$INCIDENTS_ROOT_DIR/incoming"
INCIDENT_WORKING_DIR="agents/ideas/incidents/working"
INCIDENT_RESOLVED_DIR="agents/ideas/incidents/resolved"
INCIDENT_ARCHIVED_DIR="agents/ideas/incidents/archived"
BLOCKERS_ROOT_DIR="agents/ideas/blockers"
BLOCKER_INCOMING_DIR="$BLOCKERS_ROOT_DIR/incoming"
BLOCKER_WORKING_DIR="$BLOCKERS_ROOT_DIR/working"
BLOCKER_RESOLVED_DIR="$BLOCKERS_ROOT_DIR/resolved"
BLOCKER_ARCHIVED_DIR="$BLOCKERS_ROOT_DIR/archived"
AUDIT_INCOMING_DIR="agents/ideas/audit/incoming"
AUDIT_WORKING_DIR="agents/ideas/audit/working"
AUDIT_PASSED_DIR="agents/ideas/audit/passed"
AUDIT_FAILED_DIR="agents/ideas/audit/failed"
SPECS_STABLE_DIR="agents/specs/stable"
SPECS_STABLE_GOLDEN_DIR="$SPECS_STABLE_DIR/golden"
SPECS_STABLE_PHASE_DIR="$SPECS_STABLE_DIR/phase"
SPECS_QUESTIONS_DIR="agents/specs/questions"
SPECS_DECISIONS_DIR="agents/specs/decisions"
SPECS_GOVERNANCE_DIR="agents/specs/governance"
RESEARCH_STATE="agents/research_state.json"
RESEARCH_EVENTS="agents/research_events.md"
SPECS_INDEX="agents/specs/index.json"
TASK_PROVENANCE="agents/task_provenance.json"
FROZEN_SPEC_DIR="agents/specs/stable/.frozen"
STOP_AUTONOMY_MARKER="agents/STOP_AUTONOMY"
AUTONOMY_COMPLETE_MARKER="agents/AUTONOMY_COMPLETE"
RESEARCH_STATUS="agents/research_status.md"
AUDIT_HISTORY_FILE="agents/audit_history.md"
AUDIT_SUMMARY_FILE="agents/audit_summary.json"
TASKS_FILE="agents/tasks.md"
TASKS_BACKLOG_FILE="agents/tasksbacklog.md"
TASKS_PENDING_FILE="agents/taskspending.md"
TASKS_PENDING_SHARDS_DIR="agents/taskspending"
EXPECTATIONS_FILE="agents/expectations.md"
GAPS_FILE="agents/gaps.md"
REPORTS_DIR="agents/reports"
ACCEPTANCE_PROFILES_DIR="$REPORTS_DIR/acceptance_profiles"
FULL_EXPECTATIONS_REPORT="$REPORTS_DIR/fullexpectations.md"
MARATHON_RESULTS_REPORT="$REPORTS_DIR/marathon_results.md"
GOAL_GAP_REVIEW_REPORT_JSON="$REPORTS_DIR/goal_gap_review.json"
GOAL_GAP_REVIEW_REPORT_MD="$REPORTS_DIR/goal_gap_review.md"
GOAL_GAP_REMEDIATION_SELECTION_REPORT_JSON="$REPORTS_DIR/goal_gap_remediation_selection.json"
GOAL_GAP_REMEDIATION_SELECTION_REPORT_MD="$REPORTS_DIR/goal_gap_remediation_selection.md"
AUDIT_CONTRACT_FILE="$REPORTS_DIR/audit_contract.json"
AUDIT_EXECUTION_REPORT="$REPORTS_DIR/audit_execution.json"
AUDIT_GATE_DECISION_FILE="$REPORTS_DIR/audit_gate_decision.json"
COMPLETION_DECISION_FILE="$REPORTS_DIR/completion_decision.json"
AUDIT_PLAN_REPORT="$REPORTS_DIR/audit_plan.md"
AUDIT_LOGS_DIR="$REPORTS_DIR/audit_logs"
AUDIT_CONFIG_DIR="agents/audit"
OBJECTIVE_DIR="agents/objective"
OBJECTIVE_CONTRACT_SCHEMA_FILE="${OBJECTIVE_CONTRACT_SCHEMA_FILE:-$OBJECTIVE_DIR/contract.schema.json}"
OBJECTIVE_CONTRACT_FILE="${OBJECTIVE_CONTRACT_FILE:-$OBJECTIVE_DIR/contract.yaml}"
OBJECTIVE_PROFILE_SYNC_STATE_FILE="${OBJECTIVE_PROFILE_SYNC_STATE_FILE:-$OBJECTIVE_DIR/profile_sync_state.json}"
OBJECTIVE_PROFILE_SYNC_REPORT_FILE="${OBJECTIVE_PROFILE_SYNC_REPORT_FILE:-$REPORTS_DIR/objective_profile_sync.md}"
OBJECTIVE_PROFILE_SYNC_CONTRACT_VALIDATION_REPORT="${OBJECTIVE_PROFILE_SYNC_CONTRACT_VALIDATION_REPORT:-$REPORTS_DIR/objective_profile_sync_contract_validation.json}"
FAMILY_POLICY_FILE="${FAMILY_POLICY_FILE:-$OBJECTIVE_DIR/family_policy.json}"
COMMAND_CONTRACT_REPORT_FILE="${COMMAND_CONTRACT_REPORT_FILE:-$REPORTS_DIR/command_contract.json}"
OBJECTIVE_CONTRACT_VALIDATOR_TOOL="${OBJECTIVE_CONTRACT_VALIDATOR_TOOL:-agents/tools/validate_objective_contract.py}"
COMMAND_CONTRACT_GUARD_TOOL="${COMMAND_CONTRACT_GUARD_TOOL:-agents/tools/command_contract_guard.py}"
GOAL_GAP_REVIEW_TOOL="${GOAL_GAP_REVIEW_TOOL:-agents/tools/goal_gap_review.py}"
GOAL_GAP_REMEDIATION_SELECT_TOOL="${GOAL_GAP_REMEDIATION_SELECT_TOOL:-agents/tools/goal_gap_remediation_select.py}"
FAMILY_GOVERNOR_TOOL="${FAMILY_GOVERNOR_TOOL:-agents/tools/family_governor.py}"
GOALSPEC_IDEMPOTENCY_GUARD_TOOL="${GOALSPEC_IDEMPOTENCY_GUARD_TOOL:-agents/tools/goalspec_idempotency_guard.py}"
INITIAL_FAMILY_PLAN_GUARD_TOOL="${INITIAL_FAMILY_PLAN_GUARD_TOOL:-agents/tools/initial_family_plan_guard.py}"
QUEUE_GOVERNOR_TOOL="${QUEUE_GOVERNOR_TOOL:-agents/tools/queue_governor.py}"
QUEUE_GOVERNOR_REPORT_FILE="${QUEUE_GOVERNOR_REPORT_FILE:-$REPORTS_DIR/queue_governor.json}"
QUEUE_GOVERNOR_MODE="${QUEUE_GOVERNOR_MODE:-off}"
GOVERNANCE_CANARY_TOOL="${GOVERNANCE_CANARY_TOOL:-agents/tools/governance_canary.py}"
GOVERNANCE_CANARY_MODE="${GOVERNANCE_CANARY_MODE:-off}"
GOVERNANCE_CANARY_BASELINE_POLICY_FILE="${GOVERNANCE_CANARY_BASELINE_POLICY_FILE:-agents/policies/drift_control_policy.baseline.json}"
GOVERNANCE_CANARY_REPORT_FILE="${GOVERNANCE_CANARY_REPORT_FILE:-$REPORTS_DIR/governance_canary.json}"
GOVERNANCE_CANARY_ARCHIVE_ROOTS="${GOVERNANCE_CANARY_ARCHIVE_ROOTS:-agents/diagnostics}"
GOVERNANCE_CANARY_MAX_SCENARIOS="${GOVERNANCE_CANARY_MAX_SCENARIOS:-40}"
GOVERNANCE_CANARY_MIN_OBJECTIVE_SHARE_DELTA="${GOVERNANCE_CANARY_MIN_OBJECTIVE_SHARE_DELTA:-0.01}"
GOVERNANCE_CANARY_MIN_OUTCOME_DELTA_PROXY_DELTA="${GOVERNANCE_CANARY_MIN_OUTCOME_DELTA_PROXY_DELTA:-0.01}"
GOVERNANCE_CANARY_MAX_BLOCKER_RATE_DELTA="${GOVERNANCE_CANARY_MAX_BLOCKER_RATE_DELTA:-0}"
MARATHON_REMEDIATION_SPEC_ID="SPEC-MARATHON-COMPLETION-AUDIT"
AUDIT_COMPLETION_MANIFEST="${AUDIT_COMPLETION_MANIFEST:-$AUDIT_CONFIG_DIR/completion_manifest.json}"
AUDIT_STRICT_CONTRACT_FILE="${AUDIT_STRICT_CONTRACT_FILE:-$AUDIT_CONFIG_DIR/strict_contract.json}"
AUDIT_COMPLETENESS_MODE="${AUDIT_COMPLETENESS_MODE:-comprehensive}"
AUDIT_COMPREHENSIVE_MAX_SKIPS="${AUDIT_COMPREHENSIVE_MAX_SKIPS:-0}"
MODEL_CFG="agents/options/model_config.md"
WF_CFG="agents/options/workflow_config.md"
RUNS_DIR="agents/runs/research"
DIAGNOSTICS_DIR="agents/diagnostics"
TMP_DIR="agents/.tmp"
TASK_STORE_LOCK_FILE="$TMP_DIR/task_store.lock"
RESEARCH_EMPTY_BACKLOG_AUDIT_STAMP="$TMP_DIR/research_empty_backlog_audit.stamp"
RUNTIME_STATE_DIR="agents/.research_runtime"
ACTIVE_STAGE_FILE="$RUNTIME_STATE_DIR/active_stage.env"
INTERROGATION_STATE="$RUNTIME_STATE_DIR/interrogation_state.json"
GOAL_PROMOTION_STATE="$RUNTIME_STATE_DIR/goal_promotion_state.json"
SPEC_FAMILY_STATE_FILE="$RUNTIME_STATE_DIR/spec_family_state.json"
DEFERRED_FOLLOW_ONS_FILE="${DEFERRED_FOLLOW_ONS_FILE:-$RUNTIME_STATE_DIR/deferred_follow_ons.json}"
USAGE_SAMPLER_TOOL="agents/tools/usage_sampler.py"
CODEX_EXEC_USAGE_TOOL="agents/tools/extract_codex_exec_usage.py"
USAGE_STATE_FILE="$TMP_DIR/usage_state.json"
CODEX_AUTH_SOURCE_DIR="${CODEX_AUTH_SOURCE_DIR:-$HOME/.codex}"
CODEX_RUNTIME_HOME="${CODEX_RUNTIME_HOME:-$TMP_DIR/codex-runtime-home}"
STAGE_FAILURE_CLASSIFIER_TOOL="agents/tools/classify_stage_failure.py"
ENV_PREFLIGHT_TOOL="${ENV_PREFLIGHT_TOOL:-agents/tools/env_preflight.py}"
SPEC_FAMILY_STATE_TOOL="${SPEC_FAMILY_STATE_TOOL:-agents/tools/spec_family_state.py}"
ASSEMBLE_PENDING_FAMILY_TOOL="${ASSEMBLE_PENDING_FAMILY_TOOL:-agents/tools/assemble_pending_family.py}"
MERGE_PENDING_FAMILY_TOOL="${MERGE_PENDING_FAMILY_TOOL:-agents/tools/merge_pending_family.py}"
NETWORK_GUARD_TOOL="agents/tools/network_guard.sh"
DRIFT_CONTROL_POLICY_FILE="${DRIFT_CONTROL_POLICY_FILE:-agents/policies/drift_control_policy.json}"
DRIFT_CONTROL_MODE="${DRIFT_CONTROL_MODE:-off}"
DRIFT_ENFORCE_ON_AUTONOMY_COMPLETE="${DRIFT_ENFORCE_ON_AUTONOMY_COMPLETE:-off}"
DRIFT_DETECTOR_MODE="${DRIFT_DETECTOR_MODE:-off}"
DRIFT_DETECTOR_TOOL="${DRIFT_DETECTOR_TOOL:-agents/tools/drift_detector.py}"
DRIFT_METRICS_FILE="$TMP_DIR/drift_lane_metrics.json"
PROGRESS_WATCHDOG_STATE_FILE="$RUNTIME_STATE_DIR/progress_watchdog_state.json"
PROGRESS_WATCHDOG_REPORT_FILE="$TMP_DIR/progress_watchdog_report.json"
DRIFT_STATUS_REPORT_FILE="${DRIFT_STATUS_REPORT_FILE:-$REPORTS_DIR/drift_status.json}"
DRIFT_WARNING_MARKER_FILE="$RUNTIME_STATE_DIR/drift_warning.json"
DRIFT_HARD_LATCH_FILE="$RUNTIME_STATE_DIR/drift_hard_latch.json"
INCIDENT_RECURRENCE_LEDGER_FILE="$RUNTIME_STATE_DIR/incident_recurrence_ledger.json"
GOLDEN_VERSION_REGISTRY="$SPECS_GOVERNANCE_DIR/golden_versions.json"
SPEC_QUALITY_STATE="$SPECS_GOVERNANCE_DIR/spec_quality_state.json"
DECISION_LOG_SCHEMA="$SPECS_GOVERNANCE_DIR/decision_log_schema.json"
# Retention policy (keep last): research run groups=100, diagnostics bundles=25, audit history entries=100.
RESEARCH_RUNS_RETENTION_KEEP="${RESEARCH_RUNS_RETENTION_KEEP:-100}"
DIAGNOSTICS_RETENTION_KEEP="${DIAGNOSTICS_RETENTION_KEEP:-25}"
AUDIT_HISTORY_RETENTION_KEEP="${AUDIT_HISTORY_RETENTION_KEEP:-100}"

EXIT_OK=0
EXIT_BAD_ARGS=2
EXIT_CONFIG_ERROR=3
EXIT_LOCK_HELD=4
EXIT_STAGE_FAILED=20
EXIT_AUTONOMY_STOP=21
EXIT_AUTONOMY_COMPLETE=22
EXIT_RUNTIME_ERROR=30

IDEA_DEBOUNCE_SECS="${IDEA_DEBOUNCE_SECS:-120}"
RESEARCH_POLL_SECS="${RESEARCH_POLL_SECS:-60}"
HEARTBEAT_SECS="${HEARTBEAT_SECS:-60}"
MODE="forever"
CLI_MODE_OVERRIDE=""
TASK_STORE_LOCK_TIMEOUT_SECS="${TASK_STORE_LOCK_TIMEOUT_SECS:-15}"
DEFAULT_STAGE_TIMEOUT_SECS="${DEFAULT_STAGE_TIMEOUT_SECS:-5400}"
STAGE_RETRY_MAX="${STAGE_RETRY_MAX:-1}"
STAGE_RETRY_BACKOFF_SECS="${STAGE_RETRY_BACKOFF_SECS:-5}"
RESEARCH_FAILURE_BACKOFF_SECS="${RESEARCH_FAILURE_BACKOFF_SECS:-60}"
NETWORK_GUARD_MODE="${NETWORK_GUARD_MODE:-off}"
RESEARCH_NETWORK_GUARD_POLICY="${RESEARCH_NETWORK_GUARD_POLICY:-deny}"
RESEARCH_ALLOW_SEARCH_EXCEPTION="${RESEARCH_ALLOW_SEARCH_EXCEPTION:-off}"
RESEARCH_NETWORK_POLICY_EXCEPTION="${RESEARCH_NETWORK_POLICY_EXCEPTION:-off}"
ENV_PREFLIGHT_MODE="${ENV_PREFLIGHT_MODE:-on}"
ENV_PREFLIGHT_TRANSPORT_CHECK="${ENV_PREFLIGHT_TRANSPORT_CHECK:-on}"
NETWORK_OUTAGE_RESILIENCE_MODE="${NETWORK_OUTAGE_RESILIENCE_MODE:-on}"
NETWORK_OUTAGE_WAIT_INITIAL_SECS="${NETWORK_OUTAGE_WAIT_INITIAL_SECS:-15}"
NETWORK_OUTAGE_WAIT_MAX_SECS="${NETWORK_OUTAGE_WAIT_MAX_SECS:-300}"
NETWORK_OUTAGE_MAX_PROBES="${NETWORK_OUTAGE_MAX_PROBES:-0}"
NETWORK_OUTAGE_PROBE_TIMEOUT_SECS="${NETWORK_OUTAGE_PROBE_TIMEOUT_SECS:-5}"
NETWORK_OUTAGE_PROBE_HOST="${NETWORK_OUTAGE_PROBE_HOST:-api.openai.com}"
NETWORK_OUTAGE_PROBE_PORT="${NETWORK_OUTAGE_PROBE_PORT:-443}"
NETWORK_OUTAGE_PROBE_CMD="${NETWORK_OUTAGE_PROBE_CMD:-}"
NETWORK_OUTAGE_POLICY="${NETWORK_OUTAGE_POLICY:-pause_resume}"
NETWORK_OUTAGE_ROUTE_TO_BLOCKER="${NETWORK_OUTAGE_ROUTE_TO_BLOCKER:-off}"
NETWORK_OUTAGE_ROUTE_TO_INCIDENT="${NETWORK_OUTAGE_ROUTE_TO_INCIDENT:-off}"
TASKMASTER_COMPLEXITY_PROFILE="${TASKMASTER_COMPLEXITY_PROFILE:-auto}"
TASKMASTER_COMPLEXITY_PROFILE_RESOLVED="${TASKMASTER_COMPLEXITY_PROFILE_RESOLVED:-moderate}"
TASKMASTER_COMPLEXITY_PROFILE_SOURCE="${TASKMASTER_COMPLEXITY_PROFILE_SOURCE:-fallback}"
TASKMASTER_COMPLEXITY_PROFILE_EVIDENCE="${TASKMASTER_COMPLEXITY_PROFILE_EVIDENCE:-none}"
TASKMASTER_MIN_TOTAL_CARDS="${TASKMASTER_MIN_TOTAL_CARDS:-0}"
TASKMASTER_TARGET_TOTAL_CARDS="${TASKMASTER_TARGET_TOTAL_CARDS:-0}"
SPEC_DECOMPOSITION_GOVERNANCE="${SPEC_DECOMPOSITION_GOVERNANCE:-on}"
TASKCARD_TARGET_SHORTFALL_MODE="${TASKCARD_TARGET_SHORTFALL_MODE:-auto}"
TASKCARD_SCOPE_LINT="${TASKCARD_SCOPE_LINT:-on}"
PHASE_ASSUMPTIONS_BUDGET="${PHASE_ASSUMPTIONS_BUDGET:-8}"
SPEC_QUALITY_FAIL_MAX="${SPEC_QUALITY_FAIL_MAX:-2}"
INCIDENT_FIXSPEC_INTERROGATION_ROUNDS="${INCIDENT_FIXSPEC_INTERROGATION_ROUNDS:-1}"

TIMEOUT_BIN="timeout"
IDLE_WATCH_TOOL=""
RUN_HEARTBEAT_LABEL="Research-stage"

RESEARCH_WATCH_DIRS=(
  "$RAW_DIR" "$GOAL_DIR" "$ARTICULATED_DIR" "$STAGING_DIR" "$QUEUE_SPECS_DIR" "$REVIEWED_SPECS_DIR"
  "$INCIDENT_INCOMING_DIR" "$INCIDENT_WORKING_DIR" "$INCIDENT_RESOLVED_DIR" "$INCIDENT_ARCHIVED_DIR"
  "$BLOCKER_INCOMING_DIR" "$BLOCKER_WORKING_DIR" "$BLOCKER_RESOLVED_DIR" "$BLOCKER_ARCHIVED_DIR"
  "$AUDIT_INCOMING_DIR" "$AUDIT_WORKING_DIR" "$AUDIT_PASSED_DIR" "$AUDIT_FAILED_DIR"
)

CODEX_PERM_FLAGS=()
CLAUDE_PERM_FLAGS=()

trim() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s' "$s"
}

log() {
  local ts
  ts="$(date '+%F %T')"
  printf '[%s] %s\n' "$ts" "$*" >&2
}

ensure_repo_root() {
  cd "$REPO_ROOT" 2>/dev/null || {
    echo "FATAL: unable to cd to repo root: $REPO_ROOT" >&2
    exit "$EXIT_RUNTIME_ERROR"
  }
}

prepare_codex_runtime_home() {
  local source_dir runtime_home runtime_codex source_auth source_config source_credentials
  local target_dir target_auth target_config target_credentials

  runtime_home="$(trim "${CODEX_RUNTIME_HOME:-}")"
  if [ -z "$runtime_home" ]; then
    return 1
  fi
  runtime_codex="$runtime_home/.codex"

  mkdir -p "$runtime_home" "$runtime_codex" 2>/dev/null || return 1
  chmod 700 "$runtime_home" "$runtime_codex" 2>/dev/null || true

  source_dir="$(trim "${CODEX_AUTH_SOURCE_DIR:-$HOME/.codex}")"
  if [ -n "$source_dir" ] && [ -d "$source_dir" ]; then
    source_auth="$source_dir/auth.json"
    source_config="$source_dir/config.toml"
    source_credentials="$source_dir/credentials.json"
    for target_dir in "$runtime_home" "$runtime_codex"; do
      target_auth="$target_dir/auth.json"
      target_config="$target_dir/config.toml"
      target_credentials="$target_dir/credentials.json"
      if [ -f "$source_auth" ]; then
        cp -f "$source_auth" "$target_auth" 2>/dev/null || true
        chmod 600 "$target_auth" 2>/dev/null || true
      fi
      if [ -f "$source_config" ]; then
        cp -f "$source_config" "$target_config" 2>/dev/null || true
        chmod 600 "$target_config" 2>/dev/null || true
      fi
      if [ -f "$source_credentials" ]; then
        cp -f "$source_credentials" "$target_credentials" 2>/dev/null || true
        chmod 600 "$target_credentials" 2>/dev/null || true
      fi
    done
  fi

  printf '%s\n' "$runtime_home"
}

require() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit "$EXIT_CONFIG_ERROR"
  }
}

validate_objective_contract_runtime() {
  local report_path="$REPORTS_DIR/objective_contract_validation.json"
  if ! python3 "$OBJECTIVE_CONTRACT_VALIDATOR_TOOL" \
      --schema "$OBJECTIVE_CONTRACT_SCHEMA_FILE" \
      --contract "$OBJECTIVE_CONTRACT_FILE" \
      --strict-contract "$AUDIT_STRICT_CONTRACT_FILE" \
      --command-contract-report "$COMMAND_CONTRACT_REPORT_FILE" \
      --output "$report_path"; then
    echo "Invalid objective contract: $OBJECTIVE_CONTRACT_FILE (see $report_path)" >&2
    exit "$EXIT_CONFIG_ERROR"
  fi
}

write_research_status() {
  printf '%s\n' "$1" >"$RESEARCH_STATUS"
}

read_research_status() {
  if [ -f "$RESEARCH_STATUS" ]; then
    awk '/^### /{st=$0} END{if(st) print st; else print "### IDLE"}' "$RESEARCH_STATUS" | tr -d '\r'
  else
    echo "### IDLE"
  fi
}

stat_mtime() {
  local path="$1"
  if [ ! -e "$path" ]; then
    printf '0\n'
    return 0
  fi

  if stat -c %Y "$path" >/dev/null 2>&1; then
    stat -c %Y "$path"
    return 0
  fi
  if stat -f %m "$path" >/dev/null 2>&1; then
    stat -f %m "$path"
    return 0
  fi

  printf '0\n'
}

debounce_quiet_period() {
  local path="$1"
  local quiet="${2:-$IDEA_DEBOUNCE_SECS}"

  if ! [[ "$quiet" =~ ^[0-9]+$ ]]; then
    quiet=120
  fi
  if [ "$quiet" -le 0 ]; then
    return 0
  fi

  local stable_for=0
  local prev now
  prev="$(stat_mtime "$path")"

  while [ "$stable_for" -lt "$quiet" ]; do
    sleep 1
    now="$(stat_mtime "$path")"
    if [ "$now" = "$prev" ]; then
      stable_for=$(( stable_for + 1 ))
    else
      prev="$now"
      stable_for=0
    fi
  done
}

dir_has_payload_files() {
  local dir="$1"
  find "$dir" -maxdepth 1 -type f ! -name '.gitkeep' -print -quit | grep -q .
}

goal_seed_promotion_needed() {
  local result
  result="$(
    python3 - \
      "$GOAL_DIR" "$RAW_DIR" "$ARTICULATED_DIR" "$STAGING_DIR" "$QUEUE_SPECS_DIR" "$GOAL_PROMOTION_STATE" <<'PY'
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys

goal_dir = Path(sys.argv[1])
raw_dir = Path(sys.argv[2])
articulated_dir = Path(sys.argv[3])
staging_dir = Path(sys.argv[4])
queue_specs_dir = Path(sys.argv[5])
state_path = Path(sys.argv[6])

def has_payload(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    for p in path.iterdir():
        if p.is_file() and p.name != ".gitkeep":
            return True
    return False

if has_payload(raw_dir) or has_payload(articulated_dir) or has_payload(staging_dir) or has_payload(queue_specs_dir):
    print("false")
    raise SystemExit(0)

if not goal_dir.exists() or not goal_dir.is_dir():
    print("false")
    raise SystemExit(0)

candidates = []
for p in goal_dir.iterdir():
    if p.is_file() and p.name != ".gitkeep":
        st = p.stat()
        candidates.append((st.st_mtime, p.name, p))

if not candidates:
    print("false")
    raise SystemExit(0)

candidates.sort(key=lambda row: (row[0], row[1]))
goal_file = candidates[0][2]
goal_sha = hashlib.sha256(goal_file.read_bytes()).hexdigest()

state = {}
if state_path.exists():
    try:
        loaded = json.loads(state_path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(loaded, dict):
            state = loaded
    except Exception:
        state = {}

last_sha = str(state.get("source_sha256") or "")
last_source = str(state.get("source_path") or "")
if last_sha == goal_sha and last_source == goal_file.as_posix():
    print("false")
else:
    print("true")
PY
  )"
  [ "$result" = "true" ]
}

record_goal_promotion_state() {
  local src_path="$1"
  local dst_path="$2"
  local now
  now="$(utc_now_iso)"
  python3 - "$src_path" "$dst_path" "$GOAL_PROMOTION_STATE" "$now" <<'PY'
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
state_path = Path(sys.argv[3])
now = sys.argv[4]

sha = hashlib.sha256(src.read_bytes()).hexdigest() if src.exists() else ""
state = {
    "schema_version": "1.0",
    "source_path": src.as_posix(),
    "source_sha256": sha,
    "copied_to": dst.as_posix(),
    "promoted_at": now,
}
state_path.parent.mkdir(parents=True, exist_ok=True)
state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

utc_now_iso() {
  date -u '+%Y-%m-%dT%H:%M:%SZ'
}

GOALSPEC_QUEUE_HAS_PAYLOAD="false"
INCIDENT_QUEUE_HAS_PAYLOAD="false"
BLOCKER_QUEUE_HAS_PAYLOAD="false"
AUDIT_QUEUE_HAS_PAYLOAD="false"
ANY_RESEARCH_WORK="false"
RAW_HAS_PAYLOAD="false"
GOAL_HAS_PAYLOAD="false"
AUTONOMY_SIGNAL=""
OBJECTIVE_PROFILE_SYNC_LAST_ACTION="noop"

capture_queue_snapshot() {
  RAW_HAS_PAYLOAD="false"
  GOAL_HAS_PAYLOAD="false"
  GOALSPEC_QUEUE_HAS_PAYLOAD="false"
  INCIDENT_QUEUE_HAS_PAYLOAD="false"
  BLOCKER_QUEUE_HAS_PAYLOAD="false"
  AUDIT_QUEUE_HAS_PAYLOAD="false"
  ANY_RESEARCH_WORK="false"

  if dir_has_payload_files "$RAW_DIR"; then
    RAW_HAS_PAYLOAD="true"
    GOALSPEC_QUEUE_HAS_PAYLOAD="true"
  fi
  if goal_seed_promotion_needed; then
    GOAL_HAS_PAYLOAD="true"
    GOALSPEC_QUEUE_HAS_PAYLOAD="true"
  fi
  if dir_has_payload_files "$ARTICULATED_DIR" || dir_has_payload_files "$STAGING_DIR" || dir_has_payload_files "$QUEUE_SPECS_DIR" || dir_has_payload_files "$REVIEWED_SPECS_DIR"; then
    GOALSPEC_QUEUE_HAS_PAYLOAD="true"
  fi
  if dir_has_payload_files "$INCIDENT_INCOMING_DIR" || dir_has_payload_files "$INCIDENT_WORKING_DIR" || dir_has_payload_files "$INCIDENT_RESOLVED_DIR"; then
    INCIDENT_QUEUE_HAS_PAYLOAD="true"
  fi
  if dir_has_payload_files "$BLOCKER_INCOMING_DIR" || dir_has_payload_files "$BLOCKER_WORKING_DIR" || dir_has_payload_files "$BLOCKER_RESOLVED_DIR"; then
    BLOCKER_QUEUE_HAS_PAYLOAD="true"
  fi
  if dir_has_payload_files "$AUDIT_INCOMING_DIR" || dir_has_payload_files "$AUDIT_WORKING_DIR"; then
    AUDIT_QUEUE_HAS_PAYLOAD="true"
  fi
  if [ "$GOALSPEC_QUEUE_HAS_PAYLOAD" = "true" ] || [ "$INCIDENT_QUEUE_HAS_PAYLOAD" = "true" ] || [ "$BLOCKER_QUEUE_HAS_PAYLOAD" = "true" ] || [ "$AUDIT_QUEUE_HAS_PAYLOAD" = "true" ]; then
    ANY_RESEARCH_WORK="true"
  fi
}

normalize_research_mode() {
  case "${1:-}" in
    GOALSPEC|INCIDENT|AUDIT) printf '%s\n' "$1" ;;
    *) printf 'AUDIT\n' ;;
  esac
}

normalize_mode_or_auto() {
  local value
  value="$(printf '%s' "${1:-AUTO}" | tr '[:lower:]' '[:upper:]')"
  case "$value" in
    AUTO|GOALSPEC|INCIDENT|AUDIT) printf '%s\n' "$value" ;;
    *) return 1 ;;
  esac
}

read_current_research_mode() {
  if [ ! -f "$RESEARCH_STATE" ]; then
    printf 'AUDIT\n'
    return 0
  fi
  python3 - "$RESEARCH_STATE" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    print("AUDIT")
    raise SystemExit(0)

mode = data.get("current_mode")
if mode not in {"GOALSPEC", "INCIDENT", "AUDIT"}:
    mode = "AUDIT"
print(mode)
PY
}

append_research_event() {
  local event="$1"
  local details="${2:-}"
  local ts
  ts="$(utc_now_iso)"
  mkdir -p "$(dirname "$RESEARCH_EVENTS")"
  if [ ! -f "$RESEARCH_EVENTS" ]; then
    printf '# Research Events\n\n' >"$RESEARCH_EVENTS"
  fi
  if [ -n "$details" ]; then
    printf -- '- %s | %s | %s\n' "$ts" "$event" "$details" >>"$RESEARCH_EVENTS"
  else
    printf -- '- %s | %s\n' "$ts" "$event" >>"$RESEARCH_EVENTS"
  fi
}

emit_codex_exec_usage_summary() {
  local stdout_path="$1"
  local label="$2"
  local model="$3"
  local loop_name="${4:-research}"
  local summary_line payload rc event_details

  summary_line=""
  payload=""
  rc=0
  event_details=""

  if [ ! -f "$CODEX_EXEC_USAGE_TOOL" ]; then
    summary_line="Token usage: stage=$label runner=codex model=$model unavailable reason=missing_tool stdout=$stdout_path"
  else
    if payload="$(python3 "$CODEX_EXEC_USAGE_TOOL" --stdout-log "$stdout_path" --loop "$loop_name" --stage "$label" --model "$model" --runner codex 2>/dev/null)"; then
      rc=0
    else
      rc=$?
    fi

    if [ -n "$payload" ]; then
      summary_line="$(
        python3 - "$rc" "$payload" <<'PY'
from __future__ import annotations

import json
import sys

try:
    helper_exit = int(sys.argv[1])
except Exception:
    helper_exit = 0

try:
    payload = json.loads(sys.argv[2])
except Exception:
    raise SystemExit(1)

stage = str(payload.get("stage") or "")
runner = str(payload.get("runner") or "codex")
model = str(payload.get("model") or "")
source = str(payload.get("source") or "")

if payload.get("ok") is True:
    print(
        f"Token usage: stage={stage} runner={runner} model={model} "
        f"input={payload['input_tokens']} cached={payload['cached_input_tokens']} "
        f"output={payload['output_tokens']} stdout={source}"
    )
else:
    reason = str(payload.get("reason") or "unknown")
    print(
        f"Token usage: stage={stage} runner={runner} model={model} "
        f"unavailable reason={reason} helper_exit={helper_exit} stdout={source}"
    )
PY
      )" || summary_line=""
    fi

    if [ -z "$summary_line" ]; then
      summary_line="Token usage: stage=$label runner=codex model=$model unavailable reason=formatter_failed helper_exit=$rc stdout=$stdout_path"
    fi
  fi

  log "$summary_line"
  event_details="${summary_line#Token usage: }"
  append_research_event "TOKEN_USAGE" "$event_details"
}

write_interrogation_state() {
  local target="${1:-NONE}"
  local stage="${2:-none}"
  local round="${3:-0}"
  local round_limit="${4:-0}"
  local status="${5:-idle}"

  python3 - "$INTERROGATION_STATE" "$target" "$stage" "$round" "$round_limit" "$status" <<'PY'
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

path = Path(sys.argv[1])
target = (sys.argv[2] or "NONE").upper()
stage = (sys.argv[3] or "none").strip()
status = (sys.argv[6] or "idle").strip()

def to_nonnegative_int(raw: str, default: int = 0) -> int:
    try:
        value = int((raw or "").strip())
    except Exception:
        return default
    return value if value >= 0 else default

round_value = to_nonnegative_int(sys.argv[4], 0)
round_limit = to_nonnegative_int(sys.argv[5], 0)

payload = {
    "schema_version": "1.0",
    "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "target": target,
    "stage": stage,
    "round": round_value,
    "round_limit": round_limit,
    "status": status,
}
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

clear_interrogation_context_env() {
  unset INTERROGATION_TARGET
  unset INTERROGATION_ROUND_INDEX
  unset INTERROGATION_ROUND_LIMIT
  unset INTERROGATION_STAGE
  unset INTERROGATION_SOURCE_PATH
}

interrogation_material_signature() {
  local source_path="${1:-}"
  python3 - "$SPECS_QUESTIONS_DIR" "$SPECS_DECISIONS_DIR" "$source_path" <<'PY'
from __future__ import annotations

import hashlib
import re
from pathlib import Path
import sys

questions_dir = Path(sys.argv[1])
decisions_dir = Path(sys.argv[2])
source_path = Path(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] else None
source_slug = source_path.stem if source_path and source_path.name else ""

def newest_artifact(root: Path, artifact_kind: str):
    if not root.exists() or not root.is_dir():
        return None
    if source_slug:
        pattern = f"{source_slug}__{artifact_kind}-round-*.md"
    else:
        pattern = f"*__{artifact_kind}-round-*.md"
    candidates = [p for p in root.glob(pattern) if p.is_file()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: (p.stat().st_mtime, p.name))
    return candidates[-1]

def normalize_material(text: str) -> str:
    value = text.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"__critic-round-\d+", "__critic-round-<n>", value, flags=re.IGNORECASE)
    value = re.sub(r"__designer-round-\d+", "__designer-round-<n>", value, flags=re.IGNORECASE)
    value = re.sub(r"(?i)round-\d+/\d+", "round-<n>/<n>", value)
    value = re.sub(r"(?i)round-\d+", "round-<n>", value)
    value = re.sub(r"(?i)(round\s+)\d+(\s*/\s*\d+)?", r"\1<n>/<n>", value)
    value = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", "<timestamp>", value)
    lines = [line.rstrip() for line in value.split("\n")]
    return "\n".join(lines).strip()

critic_path = newest_artifact(questions_dir, "critic")
designer_path = newest_artifact(decisions_dir, "designer")

if not critic_path and not designer_path:
    print("none|none|none")
    raise SystemExit(0)

critic_text = ""
designer_text = ""
if critic_path:
    critic_text = normalize_material(critic_path.read_text(encoding="utf-8", errors="replace"))
if designer_path:
    designer_text = normalize_material(designer_path.read_text(encoding="utf-8", errors="replace"))

material_payload = (
    f"source={source_slug or 'unknown'}\n"
    f"[critic]\n{critic_text}\n"
    f"[designer]\n{designer_text}\n"
)
digest = hashlib.sha256(material_payload.encode("utf-8")).hexdigest()
print(f"{digest}|{critic_path.name if critic_path else 'none'}|{designer_path.name if designer_path else 'none'}")
PY
}

oldest_payload_file() {
  local dir="$1"
  python3 - "$dir" <<'PY'
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
if not root.exists() or not root.is_dir():
    raise SystemExit(0)

candidates = []
for p in root.iterdir():
    if not p.is_file():
        continue
    if p.name == ".gitkeep":
        continue
    st = p.stat()
    candidates.append((st.st_mtime, p.name, str(p)))

if not candidates:
    raise SystemExit(0)

candidates.sort(key=lambda row: (row[0], row[1]))
print(candidates[0][2])
PY
}

move_oldest_payload_file() {
  local src_dir="$1"
  local dst_dir="$2"
  local src_path dst_path

  src_path="$(oldest_payload_file "$src_dir")"
  if [ -z "$src_path" ]; then
    return 1
  fi
  mkdir -p "$dst_dir"
  dst_path="$dst_dir/$(basename "$src_path")"
  mv "$src_path" "$dst_path"
  printf '%s\n' "$dst_path"
}

move_payload_file_path() {
  local src_path="$1"
  local dst_dir="$2"
  local dst_path
  [ -f "$src_path" ] || return 1
  mkdir -p "$dst_dir"
  dst_path="$dst_dir/$(basename "$src_path")"
  mv "$src_path" "$dst_path"
  printf '%s\n' "$dst_path"
}

select_incident_preemption_target() {
  python3 - "$INCIDENT_INCOMING_DIR" "$INCIDENT_WORKING_DIR" "$INCIDENT_RESOLVED_DIR" <<'PY'
from __future__ import annotations
from pathlib import Path
import re
import sys

incoming_dir = Path(sys.argv[1])
working_dir = Path(sys.argv[2])
resolved_dir = Path(sys.argv[3])

def normalize_severity(raw: str) -> tuple[str, int]:
    value = (raw or "").strip().upper()
    if value in {"S1", "SEV1", "P0", "CRITICAL"}:
        return ("S1", 1)
    if value in {"S2", "SEV2", "P1", "HIGH"}:
        return ("S2", 2)
    if value in {"S3", "SEV3", "P2", "MEDIUM"}:
        return ("S3", 3)
    if value in {"S4", "SEV4", "P3", "LOW"}:
        return ("S4", 4)
    return ("S3", 3)

def preemption_behavior_for(severity_class: str) -> str:
    if severity_class == "S1":
        return "S1-preempt-all-incident-work"
    if severity_class == "S2":
        return "S2-preempt-S3-S4"
    if severity_class == "S3":
        return "S3-standard-fifo"
    return "S4-deferred-low-urgency"

def severity_from_incident(path: Path) -> tuple[str, int]:
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"(?im)^- Severity Class:\s*`?([^`\n]+)`?\s*$", text)
    if m:
        return normalize_severity(m.group(1))
    m = re.search(r"(?im)^severity:\s*([A-Za-z0-9._-]+)\s*$", text)
    if m:
        return normalize_severity(m.group(1))
    return ("S3", 3)

candidates: list[tuple[int, int, float, str, str, str]] = []
queue_priority = {"working": 0, "incoming": 1, "resolved": 2}
scan = [
    ("incoming", incoming_dir),
    ("working", working_dir),
    ("resolved", resolved_dir),
]
for queue_name, queue_dir in scan:
    if not queue_dir.exists() or not queue_dir.is_dir():
        continue
    for path in queue_dir.iterdir():
        if not path.is_file() or path.name == ".gitkeep":
            continue
        sev_class, sev_rank = severity_from_incident(path)
        stat = path.stat()
        candidates.append((sev_rank, queue_priority[queue_name], stat.st_mtime, path.name, queue_name, path.as_posix(), sev_class))

if not candidates:
    raise SystemExit(0)

candidates.sort(key=lambda row: (row[0], row[1], row[2], row[3]))
_, _, _, _, queue_name, target_path, severity_class = candidates[0]
preemption = preemption_behavior_for(severity_class)
print(f"{queue_name}|{target_path}|{severity_class}|{preemption}")
PY
}

incident_requires_framework_level_routing() {
  local incident_path="$1"
  [ -f "$incident_path" ] || return 1
  python3 - "$incident_path" <<'PY'
from __future__ import annotations
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8", errors="replace")

explicit = re.search(r"(?im)^- Incident Class:\s*`?([^`\n]+)`?\s*$", text)
if explicit and explicit.group(1).strip().lower() == "framework-level":
    print("true")
    raise SystemExit(0)

if re.search(r"(?i)(tool|script)[ -]?contract failure", text):
    print("true")
    raise SystemExit(0)

if re.search(r"(?i)\bframework-level\b", text):
    print("true")
    raise SystemExit(0)

  print("false")
PY
}

incident_class_for_path() {
  local incident_path="$1"
  [ -f "$incident_path" ] || return 1
  python3 - "$incident_path" <<'PY'
from __future__ import annotations
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8", errors="replace")
m = re.search(r"(?im)^- Incident Class:\s*`?([^`\n]+)`?\s*$", text)
raw = m.group(1).strip().lower() if m else ""
aliases = {
    "bug": "bug-class",
    "bug-class": "bug-class",
    "bug_class": "bug-class",
    "spec": "spec-level",
    "spec-level": "spec-level",
    "spec_level": "spec-level",
    "framework": "framework-level",
    "framework-level": "framework-level",
    "framework_level": "framework-level",
    "external-blocked": "external-blocked",
    "external_blocked": "external-blocked",
    "external": "external-blocked",
}
if raw in aliases:
    print(aliases[raw])
    raise SystemExit(0)
if re.search(r"(?i)\bexternal[ -]?blocked\b|\bmanual approval\b|\boutside repo\b", text):
    print("external-blocked")
    raise SystemExit(0)
if re.search(r"(?i)(tool|script)[ -]?contract failure", text):
    print("framework-level")
    raise SystemExit(0)
print("other")
PY
}

ensure_external_blocker_record() {
  local incident_path="$1"
  local fix_spec_path="${2:-}"
  local output
  output="$(
    python3 - "$incident_path" "$fix_spec_path" "$BLOCKER_INCOMING_DIR" "$(utc_now_iso)" <<'PY'
from __future__ import annotations
from pathlib import Path
import hashlib
import re
import sys

incident_path = Path(sys.argv[1])
fix_spec_path = Path(sys.argv[2]) if sys.argv[2] else Path("")
incoming_dir = Path(sys.argv[3])
now = sys.argv[4]
incoming_dir.mkdir(parents=True, exist_ok=True)

text = incident_path.read_text(encoding="utf-8", errors="replace")
title_match = re.search(r"(?m)^#\s+(.+)$", text)
title = title_match.group(1).strip() if title_match else incident_path.stem

severity_match = re.search(r"(?im)^- Severity Class:\s*`?([^`\n]+)`?\s*$", text)
severity = severity_match.group(1).strip() if severity_match else "S3"

summary_match = re.search(r"(?im)^- Why:\s*(.+)$", text)
summary = summary_match.group(1).strip() if summary_match else "External dependency is required before remediation can continue."

required_evidence = "<pending external evidence>"
for pattern in (
    r"(?im)^- external blocker routing:\s*(.+)$",
    r"(?im)^- framework-level routing:\s*(.+)$",
):
    m = re.search(pattern, text)
    if m:
        required_evidence = m.group(1).strip()
        break

blocker_id = "BLK-" + hashlib.sha256(str(incident_path).encode("utf-8")).hexdigest()[:12]
blocker_path = incoming_dir / f"{blocker_id}.md"

body = "\n".join(
    [
        "# External Blocker",
        "",
        f"- Blocker ID: `{blocker_id}`",
        f"- Source Incident: `{incident_path.as_posix()}`",
        f"- Severity Class: `{severity}`",
        "- Owner: `external-owner`",
        "- Status: `pending`",
        f"- Created UTC: `{now}`",
        f"- Updated UTC: `{now}`",
        "",
        "## Context",
        f"- Why blocked: {summary}",
        f"- Incident title: {title}",
        "",
        "## Required Evidence",
        f"- {required_evidence}",
        "",
        "## Ready Criteria",
        "- Evidence bundle exists and validates with `python3 agents/tools/blocker_ready.py <blocker-path>`.",
        "",
        "## Resolution",
        "- Resolution notes: pending",
        "- Resolution UTC: pending",
    ]
)
if fix_spec_path:
    body += f"\n- Linked Fix Spec: `{fix_spec_path.as_posix()}`\n"

blocker_path.write_text(body.rstrip() + "\n", encoding="utf-8")
print(blocker_path.as_posix())
PY
  )" || return 1
  printf '%s\n' "$output"
}

ensure_incident_mode_b_sections() {
  local incident_path="$1"
  [ -f "$incident_path" ] || return 1

  python3 - "$incident_path" "$(utc_now_iso)" <<'PY'
from __future__ import annotations
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
now = sys.argv[2]
text = path.read_text(encoding="utf-8", errors="replace")
normalized = text if text.endswith("\n") else text + "\n"
lower = normalized.lower()

def append_block(body: str, block: str) -> str:
    body = body.rstrip() + "\n\n"
    body += block.strip() + "\n"
    return body

required_blocks = [
    (
        "## Hypothesis",
        """
## Hypothesis
- Primary hypothesis: <state likely root cause>
- Confidence: low|medium|high
- Evidence:
  - <path or excerpt>
""",
    ),
    (
        "## Alternative Hypotheses",
        """
## Alternative Hypotheses
- AH-01: <alternative explanation>
  - Status: candidate
  - Evidence: <path or excerpt>
- AH-02: <alternative explanation>
  - Status: unsupported
  - Evidence: <counter-evidence path or excerpt>
""",
    ),
    (
        "## Investigation",
        """
## Investigation
- Steps:
  1. <investigation step>
  2. <investigation step>
- Findings:
  - <what was confirmed or rejected>
""",
    ),
    (
        "## Governance Routing",
        """
## Governance Routing
- Severity Class: S3
- preemption behavior: S1 preempt-all incident work; S2 preempt S3/S4; S3/S4 continue FIFO.
- Incident Class: other
- minimal-unblock-first path: <smallest safe unblock step before full remediation>
- rewrite task card path: <required when original task is malformed or overscoped>
- spec addendum backflow: <required when root cause is spec-level; include spec addendum + reconciliation task>
- regression test requirement: <required for bug-class incidents; capture failing and passing evidence>
- framework-level routing: <required for tool/script contract failures; route framework-level fix into taskspending>
- external blocker routing: <required for external-blocked incidents; route to blockers queue with explicit evidence>
""",
    ),
    (
        "## fix_spec",
        """
## fix_spec
- Fix Spec ID: <pending>
- Fix Spec Path: <pending>
- Summary: <remediation approach>
- Severity Class: <S1|S2|S3|S4>
- preemption behavior: <copied from governance routing>
- minimal-unblock-first path: <required>
- rewrite task card path: <required when malformed/overscoped>
- spec addendum backflow: <required when spec-level>
- regression test requirement: <required for bug-class incidents>
- framework-level routing: <required for tool/script contract failures>
- external blocker routing: <required for external-blocked incidents>
""",
    ),
    (
        "## Task Handoff",
        """
## Task Handoff
- taskspending target: `agents/taskspending.md`
- Decomposition trigger: `run_stage taskmaster`
""",
    ),
    (
        "## Incident Closeout",
        """
## Incident Closeout
- Closeout status: pending
- Closeout artifacts:
  - fix_spec: <pending>
  - taskspending: `agents/taskspending.md`
  - unsupported hypotheses log: <pending>
- Resolution criteria:
  - <what must be true before archive>
""",
    ),
]

for heading, block in required_blocks:
    if heading.lower() not in lower:
        normalized = append_block(normalized, block)
        lower = normalized.lower()

if "unsupported" not in lower:
    normalized = append_block(
        normalized,
        """
## Unsupported Hypotheses
- AH-unknown
  - Status: unsupported
  - Evidence: <counter-evidence required>
""",
    )

if re.search(r"(?m)^- Updated UTC:\s*", normalized):
    normalized = re.sub(r"(?m)^- Updated UTC:\s*.*$", f"- Updated UTC: {now}", normalized)
else:
    normalized = normalized.rstrip() + f"\n- Updated UTC: {now}\n"

path.write_text(normalized, encoding="utf-8")
PY
}

ensure_incident_fix_spec_artifact() {
  local incident_path="$1"
  local output
  local tmp_output
  [ -f "$incident_path" ] || return 1
  tmp_output="$(mktemp)"

  if ! python3 - "$incident_path" "$QUEUE_SPECS_DIR" "$(utc_now_iso)" <<'PY' >"$tmp_output"
from __future__ import annotations
import hashlib
import os
import re
from pathlib import Path
import sys

incident_path = Path(sys.argv[1])
queue_dir = Path(sys.argv[2])
now = sys.argv[3]
text = incident_path.read_text(encoding="utf-8", errors="replace")

def slugify(raw: str, default: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", (raw or "").strip().lower()).strip("-")
    return value or default

def extract(pattern: str, fallback: str = "") -> str:
    m = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    return (m.group(1).strip() if m else fallback)

def parse_frontmatter_spec_id(path: Path) -> str:
    if not path.exists():
        return ""
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^\s*spec_id:\s*([A-Za-z0-9._-]+)\s*$", line)
        if m:
            return m.group(1).strip()
    return ""

def parse_canonical_unsupported_entries(src: str) -> list[str]:
    section_match = re.search(
        r"(?ims)^##\s+Unsupported Hypotheses\s*$\n(.*?)(?=^\s*##\s+|\Z)",
        src,
    )
    if not section_match:
        return ["- AH-unknown | Status: unsupported | Evidence: <required>"]

    lines = section_match.group(1).splitlines()
    entries: list[list[str]] = []
    current: list[str] = []

    def flush_entry() -> None:
        nonlocal current
        if current:
            entries.append(current[:])
            current = []

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            flush_entry()
            continue
        if re.match(r"^\s*-\s*AH-[A-Za-z0-9._-]+", line):
            flush_entry()
            current = [line.strip()]
            continue
        if current and re.match(r"^\s{2,}-\s+", line):
            current.append(line.rstrip())
            continue
        if current and re.search(r"status:\s*unsupported", line, flags=re.IGNORECASE):
            current.append(line.strip())
            continue
        if current and re.search(r"evidence:\s*\S+", line, flags=re.IGNORECASE):
            current.append(line.strip())
            continue
    flush_entry()

    normalized: list[str] = []
    for entry in entries:
        block = "\n".join(entry)
        has_unsupported = bool(re.search(r"status:\s*unsupported", block, flags=re.IGNORECASE))
        has_evidence = bool(re.search(r"evidence:\s*(?!<required>\s*$)\S+", block, flags=re.IGNORECASE))
        if has_unsupported and has_evidence:
            normalized.extend(entry)

    if not normalized:
        return ["- AH-unknown | Status: unsupported | Evidence: <required>"]
    return normalized

def normalize_rel_repo_path(raw: str) -> str:
    value = (raw or "").strip().strip("`").strip()
    if not value:
        return ""
    if os.path.isabs(value):
        return ""
    normalized = value.replace("\\", "/")
    normalized = re.sub(r"/+", "/", normalized).strip()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.startswith("../") or "/../" in normalized or normalized == "..":
        return ""
    return normalized

def normalize_severity_class(raw: str) -> str:
    value = (raw or "").strip().upper()
    if value in {"S1", "SEV1", "P0", "CRITICAL"}:
        return "S1"
    if value in {"S2", "SEV2", "P1", "HIGH"}:
        return "S2"
    if value in {"S3", "SEV3", "P2", "MEDIUM"}:
        return "S3"
    if value in {"S4", "SEV4", "P3", "LOW"}:
        return "S4"
    return "S3"

def preemption_behavior_for(severity_class: str) -> str:
    if severity_class == "S1":
        return "S1 preempt-all incident work"
    if severity_class == "S2":
        return "S2 preempt S3/S4 incident work"
    if severity_class == "S3":
        return "S3 standard FIFO execution"
    return "S4 deferred low-urgency FIFO execution"

def normalize_incident_class(raw: str) -> str:
    value = (raw or "").strip().lower()
    if value in {"bug", "bug-class", "bug_class"}:
        return "bug-class"
    if value in {"spec-level", "spec_level", "spec"}:
        return "spec-level"
    if value in {"framework-level", "framework_level", "framework"}:
        return "framework-level"
    if value in {"external-blocked", "external_blocked", "external"}:
        return "external-blocked"
    if value in {"task-card-quality", "task_card_quality", "malformed-task"}:
        return "task-card-quality"
    if value:
        return value
    return "other"

incident_id = extract(r"^incident_id:\s*([A-Za-z0-9._-]+)\s*$")
if not incident_id:
    incident_id = extract(r"^- Incident-ID:\s*`?([^`\n]+)`?\s*$")
if not incident_id:
    incident_id = f"INC-{hashlib.sha256(str(incident_path).encode('utf-8')).hexdigest()[:12]}"

task_title = extract(r"^- Task title:\s*`?([^`\n]+)`?\s*$", "incident-remediation")
summary = extract(r"^- Why:\s*(.+)$", "Incident remediation required")
existing_spec_id = extract(r"^- Fix Spec ID:\s*`?([A-Za-z0-9._-]+)`?\s*$")
spec_id = existing_spec_id or re.sub(r"[^A-Z0-9-]+", "-", f"SPEC-{incident_id.upper()}").strip("-")
spec_slug = slugify(task_title, slugify(incident_id, "incident-remediation"))
queue_dir.mkdir(parents=True, exist_ok=True)
existing_fix_spec_path = normalize_rel_repo_path(extract(r"^- Fix Spec Path:\s*`?([^`\n]+)`?\s*$"))

severity_class = normalize_severity_class(
    extract(r"^- Severity Class:\s*`?([^`\n]+)`?\s*$") or extract(r"^severity:\s*([A-Za-z0-9._-]+)\s*$", "S3")
)
preemption_behavior = preemption_behavior_for(severity_class)

incident_class_raw = extract(r"^- Incident Class:\s*`?([^`\n]+)`?\s*$")
if not incident_class_raw and re.search(r"(?i)(tool|script)[ -]?contract failure", text):
    incident_class_raw = "framework-level"
incident_class = normalize_incident_class(incident_class_raw)

minimal_unblock_path = extract(
    r"^- minimal-unblock-first path:\s*(.+)$",
    "Apply the smallest safe change that unblocks progression before broad remediation.",
)
rewrite_task_card_path = extract(
    r"^- rewrite task card path:\s*(.+)$",
    "If task is malformed/overscoped, rewrite task card in agents/taskspending.md via agents/_taskmaster.md.",
)
spec_addendum_backflow = extract(
    r"^- spec addendum backflow:\s*(.+)$",
    "If root cause is spec-level, emit spec addendum and reconciliation task into agents/taskspending.md.",
)
regression_test_requirement = extract(
    r"^- regression test requirement:\s*(.+)$",
    "For bug-class incidents, capture failing and passing regression test evidence.",
)
regression_test_evidence = extract(
    r"^- regression test evidence:\s*(.+)$",
    "<required for bug-class incidents>",
)
framework_level_routing = extract(
    r"^- framework-level routing:\s*(.+)$",
    "For tool/script contract failures, route framework-level remediation to agents/taskspending.md.",
)
external_blocker_routing = extract(
    r"^- external blocker routing:\s*(.+)$",
    "For external dependencies, route blocker record to agents/ideas/blockers/incoming with required evidence.",
)

queue_path: Path
valid_existing_path = False
if existing_fix_spec_path:
    candidate = Path(existing_fix_spec_path)
    expected_prefix = f"{spec_id}__"
    valid_existing_path = (
        candidate.suffix.lower() == ".md"
        and candidate.parent.as_posix() == queue_dir.as_posix()
        and candidate.name.startswith(expected_prefix)
    )
    if valid_existing_path:
        queue_path = candidate
    else:
        queue_path = queue_dir / f"{spec_id}__{spec_slug}.md"
else:
    queue_path = queue_dir / f"{spec_id}__{spec_slug}.md"

if not valid_existing_path:
    matches = sorted(queue_dir.glob(f"{spec_id}__*.md"))
    if len(matches) == 1:
        queue_path = matches[0]

existing_path_spec_id = parse_frontmatter_spec_id(queue_path)
if existing_path_spec_id and existing_path_spec_id != spec_id:
    queue_path = queue_dir / f"{spec_id}__{spec_slug}.md"

unsupported_lines = parse_canonical_unsupported_entries(text)

hypothesis_lines = []
capture = False
for raw in text.splitlines():
    if raw.strip().lower().startswith("## hypothesis"):
        capture = True
        continue
    if capture and raw.strip().startswith("## "):
        break
    if capture and raw.strip().startswith("-"):
        hypothesis_lines.append(raw.rstrip())
if not hypothesis_lines:
    hypothesis_lines = ["- Primary hypothesis pending completion."]

def has_taskmaster_traceability(src: str) -> bool:
    req_ids = set(re.findall(r"(?im)\bREQ(?:-[A-Z0-9]+)?-[0-9]{3,}\b", src))
    ac_ids = set(re.findall(r"(?im)\bAC(?:-[A-Z0-9]+)?-[0-9]{3,}\b", src))
    return bool(req_ids and ac_ids and re.search(r"(?i)\bSHALL(?:\s+NOT)?\b", src))

fallback_body = "\n".join(
    [
        "---",
        f"spec_id: {spec_id}",
        f"idea_id: {incident_id}",
        f"title: Incident remediation for {task_title}",
        "effort: 2",
        "depends_on_specs: []",
        "---",
        "",
        f"# Incident Fix Spec - {task_title}",
        "",
        "## fix_spec",
        f"- Fix Spec ID: `{spec_id}`",
        f"- Source Incident: `{incident_path.as_posix()}`",
        f"- Severity Class: `{severity_class}`",
        f"- preemption behavior: {preemption_behavior}",
        f"- Incident Class: `{incident_class}`",
        f"- minimal-unblock-first path: {minimal_unblock_path}",
        f"- rewrite task card path: {rewrite_task_card_path}",
        f"- spec addendum backflow: {spec_addendum_backflow}",
        f"- regression test requirement: {regression_test_requirement}",
        f"- regression test evidence: {regression_test_evidence}",
        f"- framework-level routing: {framework_level_routing}",
        f"- external blocker routing: {external_blocker_routing}",
        f"- Created UTC: {now}",
        "",
        "## Requirements",
        "- REQ-FIX-001: The remediation workflow SHALL execute the documented minimal-unblock-first path before broader changes.",
        "- REQ-FIX-002: The remediation workflow SHALL preserve required external command identity unless governance explicitly approves a replacement.",
        "- REQ-FIX-003: The remediation workflow SHALL produce deterministic verification evidence after remediation using executable commands and artifact paths.",
        "- REQ-FIX-004: If verification remains blocked, the workflow SHALL emit a follow-up routed incident with explicit next owner and unblock action.",
        "",
        "## Acceptance Criteria",
        "- AC-FIX-001 (REQ-FIX-001) Verification Method: Inspection. Pass signal: first execution card step references the minimal-unblock-first path verbatim.",
        "- AC-FIX-002 (REQ-FIX-002) Verification Method: Inspection. Pass signal: required command path/identity remains unchanged or governance exception is documented with evidence.",
        "- AC-FIX-003 (REQ-FIX-003) Verification Method: Test. Pass signal: verification commands are executable and write updated artifacts under agents/reports or agents/runs.",
        "- AC-FIX-004 (REQ-FIX-004) Verification Method: Inspection. Pass signal: unresolved state produces a routed follow-up incident with owner and deterministic unblock plan.",
        "",
        "## Traceability Matrix",
        "| Requirement ID | Acceptance ID | Verification Method | Evidence Target |",
        "| --- | --- | --- | --- |",
        "| REQ-FIX-001 | AC-FIX-001 | Inspection | agents/taskspending.md |",
        "| REQ-FIX-002 | AC-FIX-002 | Inspection | agents/ideas/incidents/working/*.md |",
        "| REQ-FIX-003 | AC-FIX-003 | Test | agents/reports/, agents/runs/ |",
        "| REQ-FIX-004 | AC-FIX-004 | Inspection | agents/ideas/incidents/* |",
        "",
        "## Investigation Summary",
        f"- {summary}",
        "",
        "## Hypotheses",
        *hypothesis_lines,
        "",
        "## Unsupported Hypotheses",
        *unsupported_lines,
        "",
        "## Remediation Plan",
        f"- minimal-unblock-first path: {minimal_unblock_path}",
        f"- rewrite task card path: {rewrite_task_card_path}",
        f"- spec addendum backflow: {spec_addendum_backflow}",
        f"- regression test requirement: {regression_test_requirement}",
        f"- framework-level routing: {framework_level_routing}",
        f"- external blocker routing: {external_blocker_routing}",
        "- Convert supported findings into executable backlog tasks.",
        "- Preserve unsupported hypotheses with linked evidence.",
        "",
        "## Task Handoff",
        "- taskspending target: `agents/taskspending.md`",
        "- decomposition stage: `agents/_taskmaster.md`",
        "",
    ]
)

existing_fix_spec_text = ""
if queue_path.exists():
    existing_fix_spec_text = queue_path.read_text(encoding="utf-8", errors="replace")

if not has_taskmaster_traceability(existing_fix_spec_text):
    queue_path.write_text(fallback_body + "\n", encoding="utf-8")

def upsert_line(src: str, prefix: str, value: str) -> str:
    pattern = re.compile(rf"(?m)^{re.escape(prefix)}.*$")
    if pattern.search(src):
        return pattern.sub(f"{prefix} {value}", src)
    return src.rstrip() + f"\n{prefix} {value}\n"

updated = text
updated = upsert_line(updated, "- Fix Spec ID:", f"`{spec_id}`")
updated = upsert_line(updated, "- Fix Spec Path:", f"`{queue_path.as_posix()}`")
updated = upsert_line(updated, "- Final fix_spec path:", f"`{queue_path.as_posix()}`")
updated = upsert_line(updated, "- Severity Class:", severity_class)
updated = upsert_line(updated, "- preemption behavior:", preemption_behavior)
updated = upsert_line(updated, "- Incident Class:", incident_class)
updated = upsert_line(updated, "- minimal-unblock-first path:", minimal_unblock_path)
updated = upsert_line(updated, "- rewrite task card path:", rewrite_task_card_path)
updated = upsert_line(updated, "- spec addendum backflow:", spec_addendum_backflow)
updated = upsert_line(updated, "- regression test requirement:", regression_test_requirement)
updated = upsert_line(updated, "- regression test evidence:", regression_test_evidence)
updated = upsert_line(updated, "- framework-level routing:", framework_level_routing)
updated = upsert_line(updated, "- external blocker routing:", external_blocker_routing)
updated = upsert_line(updated, "- Task Handoff Output:", "`agents/taskspending.md`")
updated = upsert_line(updated, "- Updated UTC:", now)
incident_path.write_text(updated if updated.endswith("\n") else updated + "\n", encoding="utf-8")

print(queue_path.as_posix())
PY
  then
    rm -f "$tmp_output"
    return 1
  fi
  output="$(cat "$tmp_output")"
  rm -f "$tmp_output"
  [ -n "$output" ] || return 1
  printf '%s\n' "$output"
}

validate_incident_fix_spec_consistency() {
  local incident_path="$1"
  local canonical_fix_spec_path="$2"
  local output
  local tmp_output
  [ -f "$incident_path" ] || return 1
  [ -n "$canonical_fix_spec_path" ] || return 1
  tmp_output="$(mktemp)"

  if ! python3 - "$incident_path" "$canonical_fix_spec_path" "$QUEUE_SPECS_DIR" "agents/diagnostics" "$(utc_now_iso)" <<'PY' >"$tmp_output"
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import os
import re
import sys

incident_path = Path(sys.argv[1])
canonical_raw = sys.argv[2]
queue_dir = Path(sys.argv[3])
diagnostics_root = Path(sys.argv[4])
now = sys.argv[5]

def normalize_rel_repo_path(raw: str) -> str:
    value = (raw or "").strip().strip("`").strip()
    if not value:
        return ""
    if os.path.isabs(value):
        return ""
    normalized = value.replace("\\", "/")
    normalized = re.sub(r"/+", "/", normalized).strip()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.startswith("../") or "/../" in normalized or normalized == "..":
        return ""
    return normalized

def read_frontmatter_spec_id(path: Path) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^\s*spec_id:\s*([A-Za-z0-9._-]+)\s*$", line)
        if m:
            return m.group(1).strip()
    return ""

canonical = normalize_rel_repo_path(canonical_raw)
errors: list[str] = []
incident_text = incident_path.read_text(encoding="utf-8", errors="replace")
if not canonical:
    errors.append("Canonical Fix Spec Path is empty/invalid.")

canonical_path = Path(canonical) if canonical else Path("")
if canonical and not canonical_path.exists():
    errors.append(f"Canonical Fix Spec Path does not exist: {canonical}")

fix_spec_text = ""
if canonical and canonical_path.exists():
    fix_spec_text = canonical_path.read_text(encoding="utf-8", errors="replace")

def extract_line_value(src: str, label: str) -> str:
    m = re.search(
        rf"(?im)^\s*(?:[-*]\s*)?{re.escape(label)}:\s*(.+?)\s*$",
        src,
    )
    if not m:
        return ""
    value = m.group(1).strip()
    if value.startswith("`") and value.endswith("`") and len(value) >= 2:
        inner = value[1:-1].strip()
        if inner and "`" not in inner:
            return inner
    return value

def looks_placeholder(value: str) -> bool:
    lowered = (value or "").strip().lower()
    if not lowered:
        return True
    return lowered.startswith("<") or lowered in {"tbd", "pending", "n/a", "none", "not-set"}

spec_id = ""
if canonical:
    spec_id = read_frontmatter_spec_id(canonical_path)
if not spec_id:
    m_id = re.search(r"(?im)^- Fix Spec ID:\s*`?([A-Za-z0-9._-]+)`?\s*$", incident_text)
    if m_id:
        spec_id = m_id.group(1).strip()
if not spec_id and canonical_path.name:
    spec_id = canonical_path.name.split("__", 1)[0]

queue_matches: list[str] = []
if spec_id:
    queue_matches = sorted(p.as_posix() for p in queue_dir.glob(f"{spec_id}__*.md"))
    if len(queue_matches) != 1:
        errors.append(f"Expected exactly one queue spec file for {spec_id}, found {len(queue_matches)}.")
    elif canonical and queue_matches[0] != canonical:
        errors.append(f"Canonical Fix Spec Path mismatch. queue={queue_matches[0]} canonical={canonical}.")
else:
    errors.append("Unable to determine spec_id for queue consistency check.")

fix_spec_path_refs = re.findall(r"(?im)^- Fix Spec Path:\s*`?([^`\n]+)`?\s*$", incident_text)
final_fix_spec_path_refs = re.findall(r"(?im)^- Final fix_spec path:\s*`?([^`\n]+)`?\s*$", incident_text)
all_refs = [normalize_rel_repo_path(v) for v in fix_spec_path_refs + final_fix_spec_path_refs if normalize_rel_repo_path(v)]
unique_refs = sorted(set(all_refs))
if not unique_refs:
    errors.append("Incident file is missing Fix Spec Path references.")
elif len(unique_refs) != 1:
    errors.append("Incident file has multiple Fix Spec Path references: " + ", ".join(unique_refs))
elif canonical and unique_refs[0] != canonical:
    errors.append(f"Incident Fix Spec Path reference does not match canonical path: {unique_refs[0]} != {canonical}")

required_incident_labels = [
    "Severity Class",
    "preemption behavior",
    "Incident Class",
    "minimal-unblock-first path",
    "rewrite task card path",
    "spec addendum backflow",
    "regression test requirement",
    "framework-level routing",
]
for label in required_incident_labels:
    if not extract_line_value(incident_text, label):
        errors.append(f"Incident governance field missing: {label}.")

if fix_spec_text:
    required_fixspec_labels = [
        "Severity Class",
        "preemption behavior",
        "Incident Class",
        "minimal-unblock-first path",
        "rewrite task card path",
        "spec addendum backflow",
        "regression test requirement",
        "framework-level routing",
    ]
    for label in required_fixspec_labels:
        if not extract_line_value(fix_spec_text, label):
            errors.append(f"Fix spec governance field missing: {label}.")
else:
    errors.append("Fix spec content unavailable for governance checks.")

if fix_spec_text:
    req_ids = sorted(set(re.findall(r"(?im)\bREQ(?:-[A-Z0-9]+)?-[0-9]{3,}\b", fix_spec_text)))
    ac_ids = sorted(set(re.findall(r"(?im)\bAC(?:-[A-Z0-9]+)?-[0-9]{3,}\b", fix_spec_text)))
    if not req_ids:
        errors.append("Fix spec missing explicit REQ-* obligations required for Taskmaster traceability.")
    if not ac_ids:
        errors.append("Fix spec missing explicit AC-* acceptance checks required for Taskmaster traceability.")
    if not re.search(r"(?i)\bSHALL(?:\s+NOT)?\b", fix_spec_text):
        errors.append("Fix spec missing explicit SHALL/SHALL NOT obligation language.")

    def numeric_suffixes(values: list[str]) -> set[str]:
        suffixes: set[str] = set()
        for value in values:
            m = re.search(r"-([0-9]{3,})$", value)
            if m:
                suffixes.add(m.group(1))
        return suffixes

    req_suffixes = numeric_suffixes(req_ids)
    ac_suffixes = numeric_suffixes(ac_ids)
    if req_suffixes and ac_suffixes and req_suffixes.isdisjoint(ac_suffixes):
        errors.append("Fix spec REQ/AC identifiers do not share any numeric traceability mapping.")

incident_class = extract_line_value(incident_text, "Incident Class").lower()
if not incident_class:
    incident_class = "other"
incident_class = incident_class.replace("_", "-")

if incident_class == "bug-class":
    regression_evidence = extract_line_value(fix_spec_text, "regression test evidence")
    if looks_placeholder(regression_evidence):
        errors.append("bug-class incident requires concrete regression test evidence in fix spec.")

if incident_class == "spec-level":
    spec_addendum = extract_line_value(fix_spec_text, "spec addendum backflow")
    if looks_placeholder(spec_addendum):
        errors.append("spec-level incident requires concrete spec addendum backflow details.")

if incident_class == "task-card-quality":
    rewrite_path = extract_line_value(fix_spec_text, "rewrite task card path")
    if looks_placeholder(rewrite_path):
        errors.append("task-card-quality incident requires concrete rewrite task card path.")

if incident_class == "framework-level":
    framework_route = extract_line_value(fix_spec_text, "framework-level routing")
    if looks_placeholder(framework_route):
        errors.append("framework-level incident requires concrete framework-level routing details.")

if incident_class == "external-blocked":
    blocker_route = extract_line_value(fix_spec_text, "external blocker routing")
    if looks_placeholder(blocker_route):
        errors.append("external-blocked incident requires concrete external blocker routing details.")

if errors:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    diagnostics_dir = diagnostics_root / stamp
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    report_path = diagnostics_dir / "incident_fix_spec_consistency.md"
    report_lines = [
        "# Incident Fix Spec Consistency Failure",
        "",
        f"- Incident: `{incident_path.as_posix()}`",
        f"- Canonical Fix Spec Path: `{canonical or '<invalid>'}`",
        f"- Spec ID: `{spec_id or '<unknown>'}`",
        f"- Incident Class: `{incident_class}`",
        f"- Generated UTC: {now}",
        "",
        "## Errors",
    ]
    report_lines.extend(f"- {err}" for err in errors)
    report_lines.extend(["", "## Queue Matches"])
    report_lines.extend([f"- `{m}`" for m in queue_matches] if queue_matches else ["- `<none>`"])
    report_lines.extend(["", "## Incident Path References"])
    report_lines.extend([f"- `{r}`" for r in unique_refs] if unique_refs else ["- `<none>`"])
    report_lines.append("")
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(f"ERROR::{report_path.as_posix()}::{' | '.join(errors)}")
    raise SystemExit(1)

print(canonical)
PY
  then
    output="$(cat "$tmp_output" 2>/dev/null || true)"
    rm -f "$tmp_output"
    if [ -n "$output" ] && [[ "$output" == ERROR::* ]]; then
      local payload="${output#ERROR::}"
      local diag_path="${payload%%::*}"
      local diag_summary="${payload#*::}"
      append_research_event "INCIDENT_FIX_SPEC_CONSISTENCY_FAIL" "incident=$incident_path fix_spec=$canonical_fix_spec_path diagnostics=$diag_path summary=$diag_summary"
      log "Incident mode: fix-spec consistency check failed for $incident_path (diagnostics: $diag_path)"
    else
      append_research_event "INCIDENT_FIX_SPEC_CONSISTENCY_FAIL" "incident=$incident_path fix_spec=$canonical_fix_spec_path diagnostics=unavailable"
      log "Incident mode: fix-spec consistency check failed for $incident_path"
    fi
    return 1
  fi

  output="$(cat "$tmp_output")"
  rm -f "$tmp_output"
  printf '%s\n' "$output"
}

run_incident_fix_spec_troubleshoot() {
  local incident_path="$1"
  local fix_spec_path="${2:-}"
  local canonical_fix_spec_path=""
  local status_before=""

  [ -f "$incident_path" ] || return 1
  status_before="$(read_research_status)"
  write_research_status "### TROUBLESHOOT_RUNNING"
  append_research_event "INCIDENT_TROUBLESHOOT_START" "incident=$incident_path fix_spec=${fix_spec_path:-unknown} reason=fix-spec-consistency"
  log "Incident mode: troubleshoot start for $incident_path (reason=fix-spec-consistency)"

  if ! ensure_incident_mode_b_sections "$incident_path"; then
    write_research_status "### BLOCKED"
    append_research_event "INCIDENT_TROUBLESHOOT_FAIL" "incident=$incident_path step=ensure_incident_mode_b_sections"
    log "Incident mode: troubleshoot failed while refreshing incident scaffolding for $incident_path"
    return 1
  fi

  if ! canonical_fix_spec_path="$(ensure_incident_fix_spec_artifact "$incident_path")"; then
    write_research_status "### BLOCKED"
    append_research_event "INCIDENT_TROUBLESHOOT_FAIL" "incident=$incident_path step=ensure_incident_fix_spec_artifact"
    log "Incident mode: troubleshoot failed while regenerating fix-spec artifact for $incident_path"
    return 1
  fi

  if ! canonical_fix_spec_path="$(validate_incident_fix_spec_consistency "$incident_path" "$canonical_fix_spec_path")"; then
    write_research_status "### BLOCKED"
    append_research_event "INCIDENT_TROUBLESHOOT_FAIL" "incident=$incident_path fix_spec=$canonical_fix_spec_path step=validate_incident_fix_spec_consistency"
    log "Incident mode: troubleshoot failed consistency validation for $incident_path"
    return 1
  fi

  append_research_event "INCIDENT_TROUBLESHOOT_RECOVERED" "incident=$incident_path fix_spec=$canonical_fix_spec_path"
  log "Incident mode: troubleshoot recovered consistency for $incident_path"
  if [ -n "$status_before" ]; then
    write_research_status "$status_before"
  else
    write_research_status "### IDLE"
  fi
  printf '%s\n' "$canonical_fix_spec_path"
}

ensure_incident_closeout_artifact() {
  local incident_path="$1"
  [ -f "$incident_path" ] || return 1

  python3 - "$incident_path" "$(utc_now_iso)" <<'PY'
from __future__ import annotations
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
now = sys.argv[2]
text = path.read_text(encoding="utf-8", errors="replace")

fix_spec_path = ""
m = re.search(r"^- Fix Spec Path:\s*`?([^`\n]+)`?\s*$", text, flags=re.MULTILINE | re.IGNORECASE)
if m:
    fix_spec_path = m.group(1).strip()

severity_class = "S3"
m = re.search(r"^- Severity Class:\s*`?([^`\n]+)`?\s*$", text, flags=re.MULTILINE | re.IGNORECASE)
if m:
    severity_class = m.group(1).strip()

incident_class = "other"
m = re.search(r"^- Incident Class:\s*`?([^`\n]+)`?\s*$", text, flags=re.MULTILINE | re.IGNORECASE)
if m:
    incident_class = m.group(1).strip().lower()

regression_requirement = "not-required"
if incident_class == "bug-class":
    regression_requirement = "required"

closeout_block = "\n".join(
    [
        "## Incident Closeout Artifact",
        f"- Closed UTC: {now}",
        f"- Fix Spec Path: `{fix_spec_path or '<pending>'}`",
        f"- Severity Class: `{severity_class}`",
        "- taskspending checkpoint: `agents/taskspending.md`",
        "- minimal-unblock-first path: captured in fix_spec",
        "- rewrite task card path: captured when malformed/overscoped",
        "- spec addendum backflow: captured when spec-level root cause",
        f"- regression test requirement: {regression_requirement}",
        "- framework-level routing: captured for tool/script contract failures",
        "- Unsupported hypotheses evidence: captured",
        "- Closeout decision: archived",
        "",
    ]
)

if "## Incident Closeout Artifact" in text:
    text = re.sub(
        r"(?s)## Incident Closeout Artifact.*?(?=\n## |\Z)",
        closeout_block.rstrip(),
        text,
        count=1,
    )
else:
    text = text.rstrip() + "\n\n" + closeout_block

if re.search(r"(?m)^- Updated UTC:\s*", text):
    text = re.sub(r"(?m)^- Updated UTC:\s*.*$", f"- Updated UTC: {now}", text)
else:
    text = text.rstrip() + f"\n- Updated UTC: {now}\n"

path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
PY
}

ensure_contract_scaffolding() {
  local d
  for d in \
    "$TMP_DIR" \
    "$RUNTIME_STATE_DIR" \
    "$REPORTS_DIR" \
    "$ACCEPTANCE_PROFILES_DIR" \
    "$AUDIT_LOGS_DIR" \
    "$AUDIT_CONFIG_DIR" \
    "$RUNS_DIR" \
    "$DIAGNOSTICS_DIR" \
    "$RAW_DIR" "$ARTICULATED_DIR" "$STAGING_DIR" "$LATER_DIR" "$QUEUE_SPECS_DIR" "$REVIEWED_SPECS_DIR" \
    "$IDEAS_ARCHIVED_DIR" "$IDEAS_FINISHED_DIR" "$IDEAS_AMBIGUOUS_DIR" \
    "$GOAL_DIR" \
    "$INCIDENT_INCOMING_DIR" "$INCIDENT_WORKING_DIR" "$INCIDENT_RESOLVED_DIR" "$INCIDENT_ARCHIVED_DIR" \
    "$BLOCKER_INCOMING_DIR" "$BLOCKER_WORKING_DIR" "$BLOCKER_RESOLVED_DIR" "$BLOCKER_ARCHIVED_DIR" \
    "$AUDIT_INCOMING_DIR" "$AUDIT_WORKING_DIR" "$AUDIT_PASSED_DIR" "$AUDIT_FAILED_DIR" \
    "$OBJECTIVE_DIR" \
    "$SPECS_STABLE_DIR" "$SPECS_STABLE_GOLDEN_DIR" "$SPECS_STABLE_PHASE_DIR" \
    "$SPECS_QUESTIONS_DIR" "$SPECS_DECISIONS_DIR" "$SPECS_GOVERNANCE_DIR" \
    "$FROZEN_SPEC_DIR" \
    "$TASKS_PENDING_SHARDS_DIR"; do
    mkdir -p "$d"
  done

  if [ ! -f "$EXPECTATIONS_FILE" ]; then
    cat >"$EXPECTATIONS_FILE" <<'EOF'
# QA Expectations — Template

## Goal
<One paragraph summary of the intended outcome.>

## Expected Behavior
- <Behavior 1>
- <Behavior 2>
- <Behavior 3>

## Expected File Changes
- <File path or area>
- <File path or area>

## Tests / Verification Commands
- <Command 1>
- <Command 2>
- <Command 3>

## Non-Functional Requirements
- <Performance or latency constraints>
- <Logging/audit requirements>
- <Compliance or safety constraints>

## Notes
- <Assumptions, open questions, or edge cases>
EOF
  fi

  if [ ! -f "$GAPS_FILE" ]; then
    cat >"$GAPS_FILE" <<'EOF'
# Research Gaps

Track unresolved research gaps discovered during GoalSpec, Incident, and Audit work.

## Open Gaps

| Gap ID | Stage | Summary | Blocking Scope | Owner | Status | Updated |
| --- | --- | --- | --- | --- | --- | --- |
| GAP-000 | <stage> | <what is missing> | <task/spec/incident impacted> | <owner> | open | <ISO8601> |

## Resolved Gaps

| Gap ID | Resolution | Evidence | Closed At |
| --- | --- | --- | --- |
| GAP-000 | <how it was resolved> | <spec/task/log path> | <ISO8601> |

## Notes
- Keep entries additive; do not delete old rows.
- Move only validated closures to the resolved table.
EOF
  fi

  if [ ! -f "$RESEARCH_STATUS" ]; then
    cat >"$RESEARCH_STATUS" <<'EOF'
# Research Status Markers

- `### IDLE`
- `### BLOCKED`
- `### TROUBLESHOOT_RUNNING`
- `### AUDIT_RUNNING`
- `### AUDIT_PASS`
- `### AUDIT_FAIL`

### IDLE
EOF
  fi

  if [ ! -f "$AUDIT_HISTORY_FILE" ]; then
    cat >"$AUDIT_HISTORY_FILE" <<'EOF'
# Audit History

Local audit outcomes recorded by `agents/research_loop.sh` (newest first).
EOF
  fi

  if [ ! -f "$AUDIT_SUMMARY_FILE" ]; then
    cat >"$AUDIT_SUMMARY_FILE" <<EOF
{
  "schema_version": "1.0",
  "updated_at": "$(utc_now_iso)",
  "last_outcome": {
    "status": "none",
    "details": "none",
    "at": ""
  },
  "counts": {
    "total": 0,
    "pass": 0,
    "fail": 0
  }
}
EOF
  fi

  if [ ! -f "$RESEARCH_EVENTS" ]; then
    printf '# Research Events\n\n' >"$RESEARCH_EVENTS"
  fi
  if [ ! -f "$RESEARCH_STATE" ]; then
    cat >"$RESEARCH_STATE" <<EOF
{
  "schema_version": "1.0",
  "updated_at": "$(utc_now_iso)",
  "current_mode": "AUDIT",
  "last_mode": "AUDIT",
  "mode_reason": "initialized",
  "cycle_count": 0,
  "transition_count": 0,
  "queue_snapshot": {
    "goalspec_ready": false,
    "incident_ready": false,
    "audit_ready": false
  }
}
EOF
  fi
  if [ ! -f "$INCIDENT_RECURRENCE_LEDGER_FILE" ]; then
    cat >"$INCIDENT_RECURRENCE_LEDGER_FILE" <<EOF
{
  "schema_version": "1.0",
  "updated_at": "$(utc_now_iso)",
  "entries": {}
}
EOF
  fi
  if [ ! -f "$DRIFT_STATUS_REPORT_FILE" ]; then
    cat >"$DRIFT_STATUS_REPORT_FILE" <<EOF
{
  "schema_version": "1.0",
  "generated_at": "$(utc_now_iso)",
  "status": "PASS",
  "context": "bootstrap",
  "mode": "on"
}
EOF
  fi
  if [ ! -f "$SPECS_INDEX" ]; then
    cat >"$SPECS_INDEX" <<EOF
{
  "schema_version": "1.0",
  "updated_at": "$(utc_now_iso)",
  "stable_specs": []
}
EOF
  fi
  if [ ! -f "$TASK_PROVENANCE" ]; then
    cat >"$TASK_PROVENANCE" <<EOF
{
  "schema_version": "1.0",
  "updated_at": "$(utc_now_iso)",
  "sources": [],
  "task_cards": []
}
EOF
  fi
  if [ ! -f "$INTERROGATION_STATE" ]; then
    cat >"$INTERROGATION_STATE" <<EOF
{
  "schema_version": "1.0",
  "updated_at": "$(utc_now_iso)",
  "target": "NONE",
  "stage": "none",
  "round": 0,
  "round_limit": 0,
  "status": "idle"
}
EOF
  fi
  if [ ! -f "$GOLDEN_VERSION_REGISTRY" ]; then
    cat >"$GOLDEN_VERSION_REGISTRY" <<EOF
{
  "schema_version": "1.0",
  "updated_at": "$(utc_now_iso)",
  "goal_source": {
    "path": "",
    "sha256": ""
  },
  "spec_versions": {}
}
EOF
  fi
  if [ ! -f "$SPEC_QUALITY_STATE" ]; then
    cat >"$SPEC_QUALITY_STATE" <<EOF
{
  "schema_version": "1.0",
  "updated_at": "$(utc_now_iso)",
  "threshold": "${SPEC_QUALITY_THRESHOLD:-0.75}",
  "phase_assumptions_budget": ${PHASE_ASSUMPTIONS_BUDGET:-8},
  "failure_limit": ${SPEC_QUALITY_FAIL_MAX:-2},
  "specs": {}
}
EOF
  fi
  if [ ! -f "$DECISION_LOG_SCHEMA" ]; then
    cat >"$DECISION_LOG_SCHEMA" <<EOF
{
  "schema_version": "1.0",
  "updated_at": "$(utc_now_iso)",
  "required_fields": [
    "decision_id",
    "phase_key",
    "phase_priority",
    "status",
    "owner",
    "rationale",
    "timestamp"
  ],
  "phase_priorities": [
    "P0",
    "P1",
    "P2",
    "P3"
  ],
  "decision_statuses": [
    "proposed",
    "accepted",
    "superseded",
    "rejected"
  ]
}
EOF
  fi

  if [ ! -f "$OBJECTIVE_CONTRACT_SCHEMA_FILE" ]; then
    cat >"$OBJECTIVE_CONTRACT_SCHEMA_FILE" <<'EOF'
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "Millrace Objective Contract",
  "type": "object",
  "required": [
    "schema_version",
    "objective_id",
    "objective_root",
    "completion",
    "gate_integrity",
    "artifacts"
  ],
  "properties": {
    "schema_version": {
      "type": "string"
    },
    "objective_id": {
      "type": "string",
      "minLength": 1
    },
    "objective_root": {
      "type": "string",
      "minLength": 1
    },
    "completion": {
      "type": "object",
      "required": [
        "authoritative_decision_file",
        "fallback_decision_file",
        "require_task_store_cards_zero",
        "require_open_gaps_zero"
      ],
      "properties": {
        "authoritative_decision_file": {
          "type": "string",
          "minLength": 1
        },
        "fallback_decision_file": {
          "type": "string",
          "minLength": 1
        },
        "require_task_store_cards_zero": {
          "type": "boolean"
        },
        "require_open_gaps_zero": {
          "type": "boolean"
        }
      },
      "additionalProperties": true
    },
    "gate_integrity": {
      "type": "object",
      "required": [
        "allow_empty_required_commands",
        "forbid_sampled_commands",
        "sampled_command_markers",
        "forbidden_command_markers",
        "required_command_substrings",
        "required_summaries"
      ],
      "properties": {
        "allow_empty_required_commands": {
          "type": "boolean"
        },
        "forbid_sampled_commands": {
          "type": "boolean"
        },
        "sampled_command_markers": {
          "type": "array",
          "items": {
            "type": "string"
          }
        },
        "forbidden_command_markers": {
          "type": "array",
          "items": {
            "type": "string"
          }
        },
        "required_command_substrings": {
          "type": "array",
          "items": {
            "type": "string"
          }
        },
        "required_summaries": {
          "type": "array",
          "items": {
            "type": "object"
          }
        }
      },
      "additionalProperties": true
    },
    "artifacts": {
      "type": "object",
      "required": [
        "strict_contract_file",
        "command_contract_report"
      ],
      "properties": {
        "strict_contract_file": {
          "type": "string",
          "minLength": 1
        },
        "command_contract_report": {
          "type": "string",
          "minLength": 1
        }
      },
      "additionalProperties": true
    },
    "drift": {
      "type": "object",
      "properties": {
        "product_root": {
          "type": "string"
        },
        "scoreboard_path": {
          "type": "string"
        }
      },
      "additionalProperties": true
    },
    "workload": {
      "type": "object",
      "properties": {
        "field_name": {
          "type": "string",
          "minLength": 1
        },
        "classes": {
          "type": "array",
          "items": {
            "type": "object",
            "required": [
              "id"
            ],
            "properties": {
              "id": {
                "type": "string",
                "minLength": 1
              },
              "description": {
                "type": "string"
              },
              "path_prefixes": {
                "type": "array",
                "items": {
                  "type": "string",
                  "minLength": 1
                }
              },
              "keyword_hints": {
                "type": "array",
                "items": {
                  "type": "string",
                  "minLength": 1
                }
              }
            },
            "additionalProperties": true
          }
        },
        "objective_open": {
          "type": "object",
          "properties": {
            "require_classes": {
              "type": "array",
              "items": {
                "type": "string",
                "minLength": 1
              }
            }
          },
          "additionalProperties": true
        }
      },
      "additionalProperties": true
    }
  },
  "additionalProperties": true
}
EOF
  fi

  if [ ! -f "$OBJECTIVE_CONTRACT_FILE" ]; then
    cat >"$OBJECTIVE_CONTRACT_FILE" <<'EOF'
{
  "schema_version": "1.0",
  "objective_id": "default-objective",
  "objective_root": "src",
  "completion": {
    "authoritative_decision_file": "agents/reports/completion_decision.json",
    "fallback_decision_file": "agents/reports/audit_gate_decision.json",
    "require_task_store_cards_zero": true,
    "require_open_gaps_zero": true
  },
  "gate_integrity": {
    "allow_empty_required_commands": false,
    "forbid_sampled_commands": true,
    "sampled_command_markers": [
      "--fast",
      "--sample",
      "subset"
    ],
    "forbidden_command_markers": [],
    "required_command_substrings": [],
    "required_summaries": []
  },
  "artifacts": {
    "strict_contract_file": "agents/audit/strict_contract.json",
    "command_contract_report": "agents/reports/command_contract.json"
  },
  "drift": {
    "product_root": "src",
    "scoreboard_path": "agents/reports/completion_decision.json"
  }
}
EOF
  fi

  if [ ! -f "$AUDIT_COMPLETION_MANIFEST" ]; then
    cat >"$AUDIT_COMPLETION_MANIFEST" <<'EOF'
{
  "schema_version": "1.0",
  "profile_id": "default",
  "configured": false,
  "notes": [
    "Set configured=true and define required_completion_commands before expecting marathon completion PASS."
  ],
  "required_completion_commands": []
}
EOF
  fi

  if [ ! -f "$AUDIT_STRICT_CONTRACT_FILE" ]; then
    cat >"$AUDIT_STRICT_CONTRACT_FILE" <<'EOF'
{
  "schema_version": "1.0",
  "contract_id": "objective-strict-v1",
  "enabled": true,
  "description": "Strict completion gate generated from objective contract policy.",
  "required_command_substrings": [],
  "forbidden_command_markers": [
    "--fast",
    "--sample",
    "subset"
  ],
  "required_summaries": []
}
EOF
  fi
}

migrate_legacy_incidents_to_incoming() {
  python3 - "$INCIDENTS_ROOT_DIR" "$INCIDENT_INCOMING_DIR" <<'PY'
from pathlib import Path
import hashlib
import sys

root_dir = Path(sys.argv[1])
incoming_dir = Path(sys.argv[2])
root_dir.mkdir(parents=True, exist_ok=True)
incoming_dir.mkdir(parents=True, exist_ok=True)

migrated = 0
deduped = 0
renamed = 0

for legacy_path in sorted(root_dir.glob("INC-*.md")):
    if not legacy_path.is_file():
        continue

    target_path = incoming_dir / legacy_path.name
    legacy_bytes = legacy_path.read_bytes()

    if not target_path.exists():
        legacy_path.replace(target_path)
        migrated += 1
        continue

    target_bytes = target_path.read_bytes()
    if target_bytes == legacy_bytes:
        legacy_path.unlink(missing_ok=True)
        deduped += 1
        continue

    digest = hashlib.sha256(legacy_bytes).hexdigest()[:8]
    candidate = incoming_dir / f"{legacy_path.stem}-legacy-{digest}{legacy_path.suffix}"
    suffix = 1
    while candidate.exists() and candidate.read_bytes() != legacy_bytes:
        suffix += 1
        candidate = incoming_dir / f"{legacy_path.stem}-legacy-{digest}-{suffix}{legacy_path.suffix}"

    if candidate.exists() and candidate.read_bytes() == legacy_bytes:
        legacy_path.unlink(missing_ok=True)
        deduped += 1
        continue

    legacy_path.replace(candidate)
    renamed += 1

print(f"migrated={migrated} deduped={deduped} renamed={renamed}")
PY
}

write_stage_checkpoint() {
  local stage="$1"
  local base="$2"
  local stdout_path="$3"
  local stderr_path="$4"
  local last_path="$5"

  mkdir -p "$RUNTIME_STATE_DIR"
  cat >"$ACTIVE_STAGE_FILE" <<EOF
stage=$stage
base=$base
stdout=$stdout_path
stderr=$stderr_path
last=$last_path
supervisor_pid=$$
started_at=$(utc_now_iso)
EOF
  : >"${base}.partial"
}

clear_stage_checkpoint() {
  local base="${1:-}"
  rm -f "$ACTIVE_STAGE_FILE"
  if [ -n "$base" ]; then
    rm -f "${base}.partial"
  fi
}

research_supervisor_pid_is_live() {
  local pid="${1:-}"
  local cmdline=""

  [ -n "$pid" ] || return 1
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  [ "$pid" -gt 1 ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1

  if [ -r "/proc/$pid/cmdline" ]; then
    cmdline="$(tr '\0' ' ' </proc/"$pid"/cmdline 2>/dev/null || true)"
    if [ -n "$cmdline" ] && [[ "$cmdline" != *"research_loop.sh"* ]]; then
      return 1
    fi
  elif command -v ps >/dev/null 2>&1; then
    cmdline="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    if [ -n "$cmdline" ] && [[ "$cmdline" != *"research_loop.sh"* ]]; then
      return 1
    fi
  fi

  return 0
}

recover_stage_checkpoint_if_needed() {
  if [ ! -f "$ACTIVE_STAGE_FILE" ]; then
    return 0
  fi

  local stage base stdout_path stderr_path last_path supervisor_pid started_at status
  stage="$(awk -F= '/^stage=/{print substr($0,7)}' "$ACTIVE_STAGE_FILE" | head -n 1)"
  base="$(awk -F= '/^base=/{print substr($0,6)}' "$ACTIVE_STAGE_FILE" | head -n 1)"
  stdout_path="$(awk -F= '/^stdout=/{print substr($0,8)}' "$ACTIVE_STAGE_FILE" | head -n 1)"
  stderr_path="$(awk -F= '/^stderr=/{print substr($0,8)}' "$ACTIVE_STAGE_FILE" | head -n 1)"
  last_path="$(awk -F= '/^last=/{print substr($0,6)}' "$ACTIVE_STAGE_FILE" | head -n 1)"
  supervisor_pid="$(awk -F= '/^supervisor_pid=/{print substr($0,16)}' "$ACTIVE_STAGE_FILE" | head -n 1)"
  started_at="$(awk -F= '/^started_at=/{print substr($0,12)}' "$ACTIVE_STAGE_FILE" | head -n 1)"
  status="$(read_research_status)"

  if [ -n "$last_path" ] && [ -f "$last_path" ] && [ "$status" = "### IDLE" ]; then
    if research_supervisor_pid_is_live "$supervisor_pid"; then
      log "Stage checkpoint preserved because supervisor pid=${supervisor_pid:-unknown} still appears live for ${stage:-unknown}"
      return 0
    fi
    if [ -n "$base" ]; then
      rm -f "${base}.partial"
    fi
    rm -f "$ACTIVE_STAGE_FILE"
    append_research_event "STALE_ACTIVE_STAGE_CLEARED" "stage=${stage:-unknown} started_at=${started_at:-unknown} completed_last_preserved=true supervisor_pid=${supervisor_pid:-unknown} status=$status"
    log "Cleared stale completed stage checkpoint for ${stage:-unknown}; preserved completed stage artifacts"
    return 0
  fi

  if [ -n "$base" ]; then
    rm -f "${base}.partial"
  fi
  if [ -n "$stdout_path" ]; then
    rm -f "$stdout_path"
  fi
  if [ -n "$stderr_path" ]; then
    rm -f "$stderr_path"
  fi
  if [ -n "$last_path" ]; then
    rm -f "$last_path"
  fi
  rm -f "$ACTIVE_STAGE_FILE"

  append_research_event "RESUME_RECOVERY" "stage=${stage:-unknown} started_at=${started_at:-unknown} partial_outputs_cleaned=true"
  log "Recovered interrupted stage checkpoint for ${stage:-unknown}; stale partial outputs were cleaned"
}

refresh_specs_contracts() {
  python3 - "$SPECS_STABLE_DIR" "$FROZEN_SPEC_DIR" "$SPECS_INDEX" <<'PY'
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

stable_root = Path(__import__("sys").argv[1])
frozen_dir = Path(__import__("sys").argv[2])
index_path = Path(__import__("sys").argv[3])
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

stable_root.mkdir(parents=True, exist_ok=True)
frozen_dir.mkdir(parents=True, exist_ok=True)

def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def rel(path: Path) -> str:
    return path.as_posix()

def frozen_tier(spec_path: str) -> str:
    lower = spec_path.lower()
    if "/golden/" in lower or "golden" in lower:
        return "golden"
    if "/phase/" in lower or "phase" in lower:
        return "phase"
    return ""

spec_paths = []
for p in stable_root.rglob("*.md"):
    if ".frozen" in p.parts:
        continue
    if any(part.startswith(".") for part in p.parts):
        continue
    spec_paths.append(p)
spec_paths.sort(key=lambda p: p.as_posix())

expected_marker_files = set()
stable_specs = []

for spec in spec_paths:
    spec_rel = rel(spec)
    sha = digest(spec)
    tier = frozen_tier(spec_rel)
    frozen = bool(tier)
    marker_rel = ""
    checksum_rel = ""
    if frozen:
        safe = spec_rel.replace("/", "__")
        marker = frozen_dir / f"{safe}.frozen"
        checksum = frozen_dir / f"{safe}.sha256"
        marker.write_text(
            f"spec_path: {spec_rel}\n"
            f"frozen_tier: {tier}\n"
            f"checksum_sha256: {sha}\n"
            f"updated_at: {now}\n",
            encoding="utf-8",
        )
        checksum.write_text(f"{sha}  {spec_rel}\n", encoding="utf-8")
        marker_rel = rel(marker)
        checksum_rel = rel(checksum)
        expected_marker_files.add(marker.resolve())
        expected_marker_files.add(checksum.resolve())

    stable_specs.append(
        {
            "spec_path": spec_rel,
            "checksum_sha256": sha,
            "frozen": frozen,
            "frozen_tier": tier,
            "freeze_marker": marker_rel,
            "checksum_marker": checksum_rel,
        }
    )

for old in frozen_dir.glob("*"):
    if old.is_file() and old.resolve() not in expected_marker_files:
        old.unlink()

payload = {
    "schema_version": "1.0",
    "updated_at": now,
    "stable_specs": stable_specs,
}
index_path.parent.mkdir(parents=True, exist_ok=True)
index_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

refresh_task_provenance() {
  python3 - "$TASK_PROVENANCE" <<'PY'
from __future__ import annotations
import json
import re
from pathlib import Path
from datetime import datetime, timezone

out_path = Path(__import__("sys").argv[1])
sources = [
    Path("agents/tasks.md"),
    Path("agents/tasksbacklog.md"),
    Path("agents/tasksarchive.md"),
]

heading_re = re.compile(r"^\s*##\s*(\d{4}-\d{2}-\d{2})\s*[—-]\s*(.+?)\s*$")
spec_re = re.compile(
    r"^\s*(?:[-*]\s*\*\*Spec-ID:\*\*|[-*]?\s*Spec-ID:)\s*`?([^`\n]+)`?\s*$",
    re.IGNORECASE,
)

cards = []
source_meta = []

for source in sources:
    if not source.exists():
        source_meta.append({"source_file": source.as_posix(), "present": False, "card_count": 0})
        continue
    text = source.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    headings = [i for i, line in enumerate(lines) if heading_re.match(line)]
    headings.append(len(lines))
    card_count = 0
    for idx in range(len(headings) - 1):
        start = headings[idx]
        end = headings[idx + 1]
        m = heading_re.match(lines[start])
        if not m:
            continue
        title = m.group(2).strip()
        block = "\n".join(lines[start:end])
        spec_id_match = spec_re.search(block)
        cards.append(
            {
                "source_file": source.as_posix(),
                "title": title,
                "spec_id": spec_id_match.group(1).strip() if spec_id_match else "",
            }
        )
        card_count += 1
    source_meta.append({"source_file": source.as_posix(), "present": True, "card_count": card_count})

payload = {
    "schema_version": "1.0",
    "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "sources": source_meta,
    "task_cards": cards,
}
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

refresh_research_contracts() {
  refresh_specs_contracts
  refresh_task_provenance
}

retention_keep_or_default() {
  local raw="${1:-}"
  local fallback="${2:-1}"
  if [[ "$raw" =~ ^[0-9]+$ ]] && [ "$raw" -ge 1 ]; then
    printf '%s\n' "$raw"
  else
    printf '%s\n' "$fallback"
  fi
}

record_audit_outcome() {
  local status="$1"
  local details="${2:-}"
  local now_iso history_keep
  now_iso="$(utc_now_iso)"
  history_keep="$(retention_keep_or_default "$AUDIT_HISTORY_RETENTION_KEEP" 100)"

  python3 - "$AUDIT_HISTORY_FILE" "$AUDIT_SUMMARY_FILE" "$status" "$details" "$now_iso" "$history_keep" <<'PY'
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

history_path = Path(sys.argv[1])
summary_path = Path(sys.argv[2])
status = (sys.argv[3] or "AUDIT_FAIL").strip().upper()
details = " ".join((sys.argv[4] or "").split())
now_iso = (sys.argv[5] or "").strip() or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

try:
    history_keep = int((sys.argv[6] or "").strip())
except Exception:
    history_keep = 100
if history_keep < 1:
    history_keep = 100

summary: dict[str, object] = {}
if summary_path.exists():
    try:
        loaded = json.loads(summary_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            summary = loaded
    except Exception:
        summary = {}

counts = summary.get("counts")
if not isinstance(counts, dict):
    counts = {}

def as_nonnegative(value: object) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except Exception:
        return 0
    return parsed if parsed >= 0 else 0

pass_count = as_nonnegative(counts.get("pass"))
fail_count = as_nonnegative(counts.get("fail"))
total_count = as_nonnegative(counts.get("total"))

if status == "AUDIT_PASS":
    pass_count += 1
    total_count += 1
elif status == "AUDIT_FAIL":
    fail_count += 1
    total_count += 1

summary_payload = {
    "schema_version": "1.0",
    "updated_at": now_iso,
    "last_outcome": {
        "status": status,
        "details": details or "none",
        "at": now_iso,
    },
    "counts": {
        "total": total_count,
        "pass": pass_count,
        "fail": fail_count,
    },
}

summary_path.parent.mkdir(parents=True, exist_ok=True)
summary_path.write_text(json.dumps(summary_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

existing_entries: list[str] = []
if history_path.exists():
    text = history_path.read_text(encoding="utf-8", errors="replace")
    existing_entries = [m.group(0).rstrip() for m in re.finditer(r"(?ms)^## .*?(?=^## |\Z)", text)]

entry_lines = [
    f"## {now_iso} — {status}",
    "",
    f"- Details: {details or 'none'}",
    "- Source: `agents/research_loop.sh`",
]
new_entry = "\n".join(entry_lines).rstrip()
entries = [new_entry] + existing_entries
entries = entries[:history_keep]

header = [
    "# Audit History",
    "",
    "Local audit outcomes recorded by `agents/research_loop.sh` (newest first).",
    "",
]
body = "\n\n".join(item.rstrip() for item in entries if item.strip())
rendered = "\n".join(header)
if body:
    rendered += body + "\n"
history_path.parent.mkdir(parents=True, exist_ok=True)
history_path.write_text(rendered, encoding="utf-8")
PY
}

audit_gate_decision_is_pass() {
  python3 - "$COMPLETION_DECISION_FILE" "$AUDIT_GATE_DECISION_FILE" <<'PY'
from __future__ import annotations

import json
from pathlib import Path
import sys

decision_paths = [Path(sys.argv[1]), Path(sys.argv[2])]
decision_path = None
for candidate in decision_paths:
    if candidate.exists():
        decision_path = candidate
        break
if decision_path is None:
    raise SystemExit(1)

try:
    payload = json.loads(decision_path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)

decision = str(payload.get("decision", "")).strip().upper()
if decision == "PASS":
    raise SystemExit(0)
raise SystemExit(1)
PY
}

resolve_objective_open_mode() {
  local objective_open="${1:-auto}"
  objective_open="$(printf '%s' "$objective_open" | tr '[:upper:]' '[:lower:]')"
  case "$objective_open" in
    on|off)
      printf '%s\n' "$objective_open"
      return 0
      ;;
    *)
      ;;
  esac

  if [ -f "$AUTONOMY_COMPLETE_MARKER" ] && audit_gate_decision_is_pass; then
    printf 'off\n'
  else
    printf 'on\n'
  fi
}

normalize_audit_gate_decision() {
  python3 - "$AUDIT_CONTRACT_FILE" "$AUDIT_EXECUTION_REPORT" "$AUDIT_COMPLETION_MANIFEST" "$TASKS_FILE" "$TASKS_BACKLOG_FILE" "$TASKS_PENDING_FILE" "$GAPS_FILE" "$AUDIT_GATE_DECISION_FILE" "$AUDIT_COMPLETENESS_MODE" "$AUDIT_COMPREHENSIVE_MAX_SKIPS" "$AUDIT_STRICT_CONTRACT_FILE" "$COMPLETION_DECISION_FILE" <<'PY'
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

contract_path = Path(sys.argv[1])
execution_path = Path(sys.argv[2])
completion_manifest_path = Path(sys.argv[3])
tasks_path = Path(sys.argv[4])
backlog_path = Path(sys.argv[5])
pending_path = Path(sys.argv[6])
gaps_path = Path(sys.argv[7])
gate_path = Path(sys.argv[8])
mode_raw = (sys.argv[9] or "").strip().lower()
completeness_mode = mode_raw if mode_raw in {"standard", "comprehensive"} else "comprehensive"
try:
    max_skips = int((sys.argv[10] or "").strip())
except Exception:
    max_skips = 0
if max_skips < 0:
    max_skips = 0
strict_contract_path = Path(sys.argv[11])
completion_path = Path(sys.argv[12])

def load_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(payload, dict):
        return payload
    return {}

def real_task_card_count(path: Path) -> int:
    if not path.exists():
        return 0
    heading_re = re.compile(r"^\s*##\s*(\d{4}-\d{2}-\d{2})\s*[—-]\s*(.+?)\s*$")
    count = 0
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = heading_re.match(raw)
        if not m:
            continue
        title = m.group(2).strip()
        if "<" in title or ">" in title:
            continue
        if title.lower() == "pending task cards":
            continue
        count += 1
    return count

def actionable_open_gap_count(path: Path) -> int:
    if not path.exists():
        return 0
    text = path.read_text(encoding="utf-8", errors="replace")
    in_open = False
    count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Open Gaps"):
            in_open = True
            continue
        if in_open and stripped.startswith("## "):
            break
        if not in_open or not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.split("|")[1:-1]]
        if len(cells) < 7:
            continue
        gap_id = cells[0]
        status = cells[5].lower()
        if not gap_id or gap_id in {"Gap ID", "---", "GAP-000"}:
            continue
        if "<" in stripped or ">" in stripped:
            continue
        if status == "open":
            count += 1
    return count

skip_patterns = [
    re.compile(r"(?im)^\s*Skipped:\s*(\d+)\b"),
    re.compile(r"(?i)\bskips=\s*(\d+)\b"),
    re.compile(r"(?i)\bskipped=\s*(\d+)\b"),
]
summary_fail_re = re.compile(r"(?im)^\s*Failed:\s*(\d+)\b")
summary_total_re = re.compile(r"(?im)^\s*Total tested:\s*(\d+)\b")

def parsed_skip_count(text: str) -> int:
    total = 0
    for pattern in skip_patterns:
        for match in pattern.finditer(text):
            try:
                total += int(match.group(1))
            except Exception:
                continue
    return total

def merged_execution_logs(execution_item: dict) -> str:
    merged_logs = ""
    for key in ("stdout_log", "stderr_log"):
        log_ref = str(execution_item.get(key, "")).strip()
        if not log_ref:
            continue
        log_path = Path(log_ref)
        if not log_path.is_absolute():
            log_path = Path.cwd() / log_path
        if log_path.exists():
            merged_logs += "\n" + log_path.read_text(encoding="utf-8", errors="replace")
    return merged_logs

def parsed_summary_max(pattern: re.Pattern[str], text: str) -> int | None:
    values: list[int] = []
    for match in pattern.finditer(text):
        try:
            values.append(int(match.group(1)))
        except Exception:
            continue
    if not values:
        return None
    return max(values)

def parsed_summary_fail_count(text: str) -> int | None:
    count = parsed_summary_max(summary_fail_re, text)
    if count is not None:
        return count
    if re.search(r'"result"\s*:\s*"FAIL"', text):
        return 1
    return None

def parsed_summary_total_count(text: str) -> int | None:
    return parsed_summary_max(summary_total_re, text)

reasons: list[str] = []

contract_payload = load_json(contract_path) if contract_path.exists() else {}
execution_payload = load_json(execution_path) if execution_path.exists() else {}
completion_payload = load_json(completion_manifest_path) if completion_manifest_path.exists() else {}
strict_payload = load_json(strict_contract_path) if strict_contract_path.exists() else {}

if not contract_path.exists():
    reasons.append("Missing audit contract artifact.")
if not execution_path.exists():
    reasons.append("Missing audit execution artifact.")
if not completion_manifest_path.exists():
    reasons.append("Missing completion manifest.")

strict_enabled = bool(strict_payload.get("enabled")) if strict_payload else False

completion_configured = bool(completion_payload.get("configured")) if completion_payload else False
if completion_manifest_path.exists() and not completion_configured:
    reasons.append("Completion manifest is not configured (`configured=false`).")

contract_checks = contract_payload.get("checks")
if not isinstance(contract_checks, list):
    contract_checks = contract_payload.get("mandatory_checks")
if not isinstance(contract_checks, list):
    contract_checks = []
if not contract_checks:
    reasons.append("Audit contract has no checks.")

execution_checks = execution_payload.get("checks", [])
if not isinstance(execution_checks, list):
    execution_checks = []

required_total = 0
required_pass = 0
required_fail = 0
required_blocked = 0
execution_by_id: dict[str, dict] = {}
for item in execution_checks:
    if not isinstance(item, dict):
        continue
    check_id = str(item.get("id", "")).strip()
    if check_id:
        execution_by_id[check_id] = item
    required = bool(item.get("required", True))
    if not required:
        continue
    required_total += 1
    status = str(item.get("status", "")).strip().upper()
    if status == "PASS":
        required_pass += 1
    elif status == "BLOCKED":
        required_blocked += 1
    else:
        required_fail += 1

if required_fail or required_blocked:
    reasons.append(
        f"{required_fail + required_blocked} required audit check(s) are non-pass "
        f"(fail={required_fail}, blocked={required_blocked})."
    )

completion_required_commands: list[str] = []
cmds = completion_payload.get("required_completion_commands", []) if isinstance(completion_payload, dict) else []
if isinstance(cmds, list):
    for item in cmds:
        if not isinstance(item, dict):
            continue
        if not bool(item.get("required", True)):
            continue
        command = str(item.get("command", "")).strip()
        if command:
            completion_required_commands.append(command)
completion_required_commands = sorted(set(completion_required_commands))
completion_required = len(completion_required_commands)
if completion_manifest_path.exists() and completion_configured and completion_required == 0:
    reasons.append("Completion manifest is configured but has zero required commands.")

contract_required_command_by_id: dict[str, str] = {}
contract_required_ids_by_command: dict[str, list[str]] = {}
for check in contract_checks:
    if not isinstance(check, dict):
        continue
    if not bool(check.get("required", True)):
        continue
    if str(check.get("type", "")).strip().lower() != "command":
        continue
    check_id = str(check.get("id", "")).strip()
    command = str(check.get("command", "")).strip()
    if not check_id or not command:
        continue
    contract_required_command_by_id[check_id] = command
    contract_required_ids_by_command.setdefault(command, []).append(check_id)

missing_completion_commands = [cmd for cmd in completion_required_commands if cmd not in contract_required_ids_by_command]
if missing_completion_commands:
    reasons.append(
        "Audit contract missing required completion command(s): "
        + "; ".join(missing_completion_commands[:5])
    )

completion_pass = 0
for command in completion_required_commands:
    ids = contract_required_ids_by_command.get(command, [])
    if not ids:
        continue
    if any(str(execution_by_id.get(cid, {}).get("status", "")).strip().upper() == "PASS" for cid in ids):
        completion_pass += 1
if completion_pass < completion_required:
    reasons.append(f"Completion required pass coverage incomplete ({completion_pass}/{completion_required}).")

strict_rules_total = 0
strict_rules_pass = 0
if strict_enabled:
    required_command_markers: list[str] = []
    raw_required_markers = strict_payload.get("required_command_substrings", [])
    if isinstance(raw_required_markers, list):
        for marker in raw_required_markers:
            value = str(marker).strip()
            if value:
                required_command_markers.append(value)
    if required_command_markers:
        strict_rules_total += len(required_command_markers)
        required_commands = sorted(set(contract_required_command_by_id.values()))
        for marker in required_command_markers:
            marker_lc = marker.lower()
            if any(marker_lc in command.lower() for command in required_commands):
                strict_rules_pass += 1
            else:
                reasons.append(
                    "Strict audit contract required command marker missing from required checks: "
                    + marker
                )

    forbidden_markers: list[str] = []
    raw_forbidden_markers = strict_payload.get("forbidden_command_markers", [])
    if isinstance(raw_forbidden_markers, list):
        for marker in raw_forbidden_markers:
            value = str(marker).strip()
            if value:
                forbidden_markers.append(value)
    strict_rules_total += len(forbidden_markers)
    for marker in forbidden_markers:
        marker_lc = marker.lower()
        offenders = [
            command for command in sorted(set(contract_required_command_by_id.values()))
            if marker_lc in command.lower()
        ]
        if offenders:
            reasons.append(
                "Strict audit contract forbids marker in required command set: "
                + marker
                + " offenders="
                + "; ".join(offenders[:4])
            )
        else:
            strict_rules_pass += 1

    raw_summary_rules = strict_payload.get("required_summaries", [])
    summary_rules: list[dict] = []
    if isinstance(raw_summary_rules, list):
        for item in raw_summary_rules:
            if isinstance(item, dict):
                summary_rules.append(item)
    if summary_rules:
        strict_rules_total += len(summary_rules)
        for rule in summary_rules:
            rule_id = str(rule.get("id", "")).strip() or "STRICT-SUMMARY"
            marker = str(rule.get("command_substring", "")).strip()
            try:
                min_total = int(rule.get("min_total_tested", 1))
            except Exception:
                min_total = 1
            try:
                max_failed = int(rule.get("max_failed", 0))
            except Exception:
                max_failed = 0
            if min_total < 0:
                min_total = 0
            if max_failed < 0:
                max_failed = 0
            if not marker:
                reasons.append(f"{rule_id}: missing command_substring.")
                continue

            matching_ids = [
                check_id
                for check_id, command in sorted(contract_required_command_by_id.items())
                if marker.lower() in command.lower()
            ]
            if not matching_ids:
                reasons.append(f"{rule_id}: no required check command matched marker `{marker}`.")
                continue

            best_total: int | None = None
            best_failed: int | None = None
            saw_required_pass = False
            for check_id in matching_ids:
                execution_item = execution_by_id.get(check_id, {})
                status = str(execution_item.get("status", "")).strip().upper()
                if status != "PASS":
                    continue
                saw_required_pass = True
                merged_logs = merged_execution_logs(execution_item)
                if not merged_logs:
                    continue
                total_count = parsed_summary_total_count(merged_logs)
                failed_count = parsed_summary_fail_count(merged_logs)
                if total_count is None or failed_count is None:
                    continue
                if best_total is None or total_count > best_total:
                    best_total = total_count
                    best_failed = failed_count

            if not saw_required_pass:
                reasons.append(
                    f"{rule_id}: no PASS execution found for required command marker `{marker}`."
                )
                continue
            if best_total is None or best_failed is None:
                reasons.append(
                    f"{rule_id}: unable to parse `Total tested`/`Failed` summary for marker `{marker}`."
                )
                continue
            if best_total < min_total:
                reasons.append(
                    f"{rule_id}: total tested below strict threshold ({best_total} < {min_total})."
                )
                continue
            if best_failed > max_failed:
                reasons.append(
                    f"{rule_id}: failed count above strict threshold ({best_failed} > {max_failed})."
                )
                continue
            strict_rules_pass += 1

# `agents/tasks.md` holds the in-flight active card until orchestrator finalize.
# Gate on queued stores only (backlog + pending) to avoid impossible in-flight FAIL.
task_store_cards = real_task_card_count(backlog_path) + real_task_card_count(pending_path)
if task_store_cards > 0:
    reasons.append(f"Task stores still contain real task cards ({task_store_cards}).")

open_gaps = actionable_open_gap_count(gaps_path)
if open_gaps > 0:
    reasons.append(f"{open_gaps} actionable open gap row(s) remain.")

if completeness_mode == "comprehensive":
    sample_markers = ("--fast", "--sample", "subset")
    sampled_commands = [
        cmd for cmd in sorted(set(completion_required_commands + list(contract_required_command_by_id.values())))
        if any(marker in cmd.lower() for marker in sample_markers)
    ]
    if sampled_commands:
        reasons.append(
            "Comprehensive mode forbids sampled completion commands: "
            + "; ".join(sampled_commands[:5])
        )

    skip_total = 0
    skip_sources: list[str] = []
    for check_id, command in sorted(contract_required_command_by_id.items()):
        execution_item = execution_by_id.get(check_id, {})
        if str(execution_item.get("status", "")).strip().upper() != "PASS":
            continue
        merged_logs = merged_execution_logs(execution_item)
        if not merged_logs:
            continue
        parsed = parsed_skip_count(merged_logs)
        if parsed > 0:
            skip_total += parsed
            skip_sources.append(f"{command}={parsed}")

    if skip_total > max_skips:
        if skip_sources:
            reasons.append(
                "Comprehensive skip budget exceeded: "
                + "; ".join(skip_sources[:8])
                + f" (max={max_skips})"
            )
        else:
            reasons.append(
                f"Comprehensive skip budget exceeded (skip_total={skip_total}, max={max_skips})."
            )

decision = "PASS" if not reasons else "FAIL"
payload = {
    "schema_version": "1.0",
    "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "decision": decision,
    "reasons": reasons,
    "counts": {
        "required_total": required_total,
        "required_pass": required_pass,
        "required_fail": required_fail,
        "required_blocked": required_blocked,
        "completion_required": completion_required,
        "completion_pass": completion_pass,
        "open_gaps": open_gaps,
        "task_store_cards": task_store_cards,
        "strict_rules_total": strict_rules_total,
        "strict_rules_pass": strict_rules_pass,
        "strict_contract_enforced": bool(strict_enabled),
    },
    "source": "deterministic-normalizer",
}

gate_path.parent.mkdir(parents=True, exist_ok=True)
gate_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
completion_path.parent.mkdir(parents=True, exist_ok=True)
completion_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

apply_research_retention_policy() {
  local runs_keep diagnostics_keep summary
  runs_keep="$(retention_keep_or_default "$RESEARCH_RUNS_RETENTION_KEEP" 100)"
  diagnostics_keep="$(retention_keep_or_default "$DIAGNOSTICS_RETENTION_KEEP" 25)"

  summary="$(
    python3 - "$RUNS_DIR" "$DIAGNOSTICS_DIR" "$runs_keep" "$diagnostics_keep" <<'PY'
from __future__ import annotations
from pathlib import Path
import shutil
import sys

runs_root = Path(sys.argv[1])
diagnostics_root = Path(sys.argv[2])

try:
    runs_keep = int((sys.argv[3] or "").strip())
except Exception:
    runs_keep = 100
if runs_keep < 1:
    runs_keep = 100

try:
    diagnostics_keep = int((sys.argv[4] or "").strip())
except Exception:
    diagnostics_keep = 25
if diagnostics_keep < 1:
    diagnostics_keep = 25

def newest_first(paths: list[Path]) -> list[Path]:
    return sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)

def prune_dirs(root: Path, keep: int) -> tuple[int, int, int]:
    if not root.exists() or not root.is_dir():
        return (0, 0, 0)
    dirs = newest_first([p for p in root.iterdir() if p.is_dir()])
    before = len(dirs)
    pruned = 0
    for path in dirs[keep:]:
        shutil.rmtree(path, ignore_errors=True)
        pruned += 1
    after = len(newest_first([p for p in root.iterdir() if p.is_dir()]))
    return (before, after, pruned)

def run_group_key(name: str) -> str:
    for suffix in (".stdout.log", ".stderr.log", ".last.md"):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name

def prune_run_groups(root: Path, keep: int) -> tuple[int, int, int]:
    if not root.exists() or not root.is_dir():
        return (0, 0, 0)
    groups: dict[str, list[Path]] = {}
    for path in root.iterdir():
        if not path.is_file() or path.name == ".gitkeep":
            continue
        groups.setdefault(run_group_key(path.name), []).append(path)
    ordered = sorted(
        groups.items(),
        key=lambda item: max((p.stat().st_mtime for p in item[1]), default=0.0),
        reverse=True,
    )
    before = len(ordered)
    pruned = 0
    for _, paths in ordered[keep:]:
        for path in paths:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except IsADirectoryError:
                shutil.rmtree(path, ignore_errors=True)
        pruned += 1
    remaining_groups: dict[str, list[Path]] = {}
    for path in root.iterdir():
        if not path.is_file() or path.name == ".gitkeep":
            continue
        remaining_groups.setdefault(run_group_key(path.name), []).append(path)
    after = len(remaining_groups)
    return (before, after, pruned)

runs_dirs_before, runs_dirs_after, runs_dirs_pruned = prune_dirs(runs_root, runs_keep)
runs_groups_before, runs_groups_after, runs_groups_pruned = prune_run_groups(runs_root, runs_keep)
diag_before, diag_after, diag_pruned = prune_dirs(diagnostics_root, diagnostics_keep)

print(
    "runs_dirs_before={0} runs_dirs_after={1} runs_dirs_pruned={2} "
    "runs_groups_before={3} runs_groups_after={4} runs_groups_pruned={5} "
    "diagnostics_before={6} diagnostics_after={7} diagnostics_pruned={8}".format(
        runs_dirs_before,
        runs_dirs_after,
        runs_dirs_pruned,
        runs_groups_before,
        runs_groups_after,
        runs_groups_pruned,
        diag_before,
        diag_after,
        diag_pruned,
    )
)
PY
  )"

  log "Retention prune: keep last runs=${runs_keep} diagnostics=${diagnostics_keep} ${summary}"
}

markdown_has_real_task_cards() {
  local path="$1"
  python3 - "$path" <<'PY'
from __future__ import annotations
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(1)

heading_re = re.compile(r"^\s*##\s*(\d{4}-\d{2}-\d{2})\s*[—-]\s*(.+?)\s*$")
text = path.read_text(encoding="utf-8", errors="replace")
for raw in text.splitlines():
    m = heading_re.match(raw)
    if not m:
        continue
    title = m.group(2).strip()
    if "<" in title or ">" in title:
        continue
    if title.lower() == "pending task cards":
        continue
    raise SystemExit(0)

raise SystemExit(1)
PY
}

backlog_has_real_cards() {
  markdown_has_real_task_cards "$TASKS_BACKLOG_FILE"
}

taskspending_has_real_cards() {
  markdown_has_real_task_cards "$TASKS_PENDING_FILE"
}

taskspending_has_single_marathon_remediation_card() {
  python3 - "$TASKS_PENDING_FILE" "$MARATHON_REMEDIATION_SPEC_ID" <<'PY'
from __future__ import annotations
from pathlib import Path
import re
import sys

pending = Path(sys.argv[1])
expected_spec = (sys.argv[2] or "").strip().upper()
if not pending.exists() or not expected_spec:
    raise SystemExit(1)

heading_re = re.compile(r"^\s*##\s*(\d{4}-\d{2}-\d{2})\s*[—-]\s*(.+?)\s*$")
spec_re = re.compile(r"^\s*-\s*\*\*Spec-ID:\*\*\s*`?([^`\n]+)`?\s*$", flags=re.IGNORECASE)
lines = pending.read_text(encoding="utf-8", errors="replace").splitlines()

cards: list[dict[str, str]] = []
current: dict[str, str] | None = None
for line in lines:
    m = heading_re.match(line)
    if m:
        title = m.group(2).strip()
        if "<" in title or ">" in title or title.lower() == "pending task cards":
            continue
        if current is not None:
            cards.append(current)
        current = {"title": title, "spec": ""}
        continue

    if current is None:
        continue

    s = spec_re.match(line)
    if s and not current["spec"]:
        current["spec"] = s.group(1).strip().upper()

if current is not None:
    cards.append(current)

if len(cards) != 1:
    raise SystemExit(1)

card = cards[0]
if card["spec"] != expected_spec:
    raise SystemExit(1)

raise SystemExit(0)
PY
}

tasks_has_real_cards() {
  markdown_has_real_task_cards "$TASKS_FILE"
}

research_backlog_empty_audit_fallback_ready() {
  local stamp_payload previous_stamp

  if [ "${ANY_RESEARCH_WORK:-false}" = "true" ] || \
     backlog_has_real_cards || \
     taskspending_has_real_cards || \
     tasks_has_real_cards; then
    rm -f "$RESEARCH_EMPTY_BACKLOG_AUDIT_STAMP" >/dev/null 2>&1 || true
    return 1
  fi

  stamp_payload="tasks=$(stat_mtime "$TASKS_FILE");backlog=$(stat_mtime "$TASKS_BACKLOG_FILE");pending=$(stat_mtime "$TASKS_PENDING_FILE")"
  if [ -f "$RESEARCH_EMPTY_BACKLOG_AUDIT_STAMP" ]; then
    previous_stamp="$(tr -d '\r' <"$RESEARCH_EMPTY_BACKLOG_AUDIT_STAMP" || true)"
    if [ "$previous_stamp" = "$stamp_payload" ]; then
      return 1
    fi
  fi

  printf '%s\n' "$stamp_payload" >"$RESEARCH_EMPTY_BACKLOG_AUDIT_STAMP"
  return 0
}

record_incident_recurrence_signature() {
  return 0
}

drift_hard_latch_active() {
  return 1
}

clear_drift_signals() {
  rm -f "$DRIFT_HARD_LATCH_FILE" "$DRIFT_WARNING_MARKER_FILE" >/dev/null 2>&1 || true
  return 0
}

run_drift_detector_check() {
  return 0
}

run_queue_governor_check() {
  return 0
}

run_progress_watchdog_check() {
  return 0
}

ensure_product_recovery_task_cards() {
  return 0
}

enforce_drift_controls() {
  return 0
}

refresh_marathon_audit_artifacts() {
  python3 - "$EXPECTATIONS_FILE" "$GAPS_FILE" "$FULL_EXPECTATIONS_REPORT" "$MARATHON_RESULTS_REPORT" "$QUEUE_SPECS_DIR" "$SPECS_STABLE_GOLDEN_DIR" "$AUDIT_CONTRACT_FILE" "$AUDIT_EXECUTION_REPORT" "$AUDIT_GATE_DECISION_FILE" "$COMPLETION_DECISION_FILE" "$AUDIT_COMPLETION_MANIFEST" "$AUDIT_COMPLETENESS_MODE" "$AUDIT_COMPREHENSIVE_MAX_SKIPS" "$OBJECTIVE_PROFILE_SYNC_STATE_FILE" "$OBJECTIVE_CONTRACT_FILE" "$AUDIT_STRICT_CONTRACT_FILE" <<'PY'
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

expectations_path = Path(sys.argv[1])
gaps_path = Path(sys.argv[2])
full_path = Path(sys.argv[3])
results_path = Path(sys.argv[4])
queue_specs_dir = Path(sys.argv[5])
golden_dir = Path(sys.argv[6])
contract_path = Path(sys.argv[7])
execution_path = Path(sys.argv[8])
gate_path = Path(sys.argv[9])
completion_path = Path(sys.argv[10])
completion_manifest_path = Path(sys.argv[11])
completeness_mode_raw = (sys.argv[12] or "").strip().lower()
if completeness_mode_raw in {"standard", "comprehensive"}:
    completeness_mode = completeness_mode_raw
else:
    completeness_mode = "comprehensive"
try:
    comprehensive_max_skips = int((sys.argv[13] or "").strip())
except Exception:
    comprehensive_max_skips = 0
if comprehensive_max_skips < 0:
    comprehensive_max_skips = 0
objective_profile_state_path = Path(sys.argv[14])
objective_contract_path = Path(sys.argv[15])
strict_contract_path = Path(sys.argv[16])
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

issues: list[str] = []
checks: list[tuple[str, bool, str]] = []

def load_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(payload, dict):
        return payload
    return {}

skip_patterns = [
    re.compile(r"(?i)\bskips?\s*[:=]\s*(\d+)\b"),
    re.compile(r"(?i)\bskipped\s*[:=]\s*(\d+)\b"),
]

def extract_skip_count(text: str) -> int:
    total = 0
    for pattern in skip_patterns:
        for match in pattern.finditer(text):
            try:
                total += int(match.group(1))
            except Exception:
                continue
    return total

def combined_logs_for_check(item: dict) -> str:
    combined_logs = ""
    for log_key in ("stdout_log", "stderr_log"):
        log_ref = str(item.get(log_key, "")).strip()
        if not log_ref:
            continue
        log_path = Path(log_ref)
        if not log_path.is_absolute():
            log_path = Path.cwd() / log_path
        if log_path.exists():
            combined_logs += "\n" + log_path.read_text(encoding="utf-8", errors="replace")
    return combined_logs

expectations_text = ""
expectations_exists = expectations_path.exists()
if expectations_exists:
    expectations_text = expectations_path.read_text(encoding="utf-8", errors="replace")
checks.append(("expectations-present", expectations_exists, expectations_path.as_posix()))
if not expectations_exists:
    issues.append("Missing agents/expectations.md input for marathon audit.")

placeholder_found = bool(re.search(r"<[^>\n]+>", expectations_text))
checks.append(("expectations-no-placeholders", not placeholder_found, "template placeholders must be resolved"))
if placeholder_found:
    issues.append("agents/expectations.md still contains unresolved template placeholders.")

contract_exists = contract_path.exists()
contract_payload = load_json(contract_path) if contract_exists else {}
contract_checks = contract_payload.get("checks")
if not isinstance(contract_checks, list):
    contract_checks = contract_payload.get("mandatory_checks")
if not isinstance(contract_checks, list):
    contract_checks = []
checks.append(("audit-contract-present", contract_exists, contract_path.as_posix()))
if not contract_exists:
    issues.append("Missing audit contract artifact (agents/reports/audit_contract.json).")
checks.append(("audit-contract-has-checks", len(contract_checks) > 0, f"checks={len(contract_checks)}"))
if len(contract_checks) == 0:
    issues.append("Audit contract has no checks; exhaustive validation cannot run.")

objective_profile_state_exists = objective_profile_state_path.exists()
objective_profile_state = load_json(objective_profile_state_path) if objective_profile_state_exists else {}
checks.append(("objective-profile-sync-state-present", objective_profile_state_exists, objective_profile_state_path.as_posix()))
if not objective_profile_state_exists:
    issues.append("Missing objective profile sync state (agents/objective/profile_sync_state.json).")

synced_profile_id = str(objective_profile_state.get("profile_id", "")).strip() if objective_profile_state else ""
synced_profile_path = str(objective_profile_state.get("profile_path", "")).strip() if objective_profile_state else ""
synced_goal_path = str(objective_profile_state.get("goal_path", "")).strip() if objective_profile_state else ""
synced_goal_sha = str(objective_profile_state.get("goal_sha256", "")).strip() if objective_profile_state else ""
synced_milestones = sorted(
    str(item).strip() for item in objective_profile_state.get("milestone_ids", []) if str(item).strip()
) if objective_profile_state else []
synced_gates = sorted(
    str(item).strip() for item in objective_profile_state.get("gate_ids", []) if str(item).strip()
) if objective_profile_state else []
checks.append(("objective-profile-sync-has-profile-id", bool(synced_profile_id), f"profile_id={synced_profile_id or 'missing'}"))
checks.append(("objective-profile-sync-has-profile-path", bool(synced_profile_path), f"profile_path={synced_profile_path or 'missing'}"))
if objective_profile_state_exists and not synced_profile_id:
    issues.append("Objective profile sync state is missing profile_id.")
if objective_profile_state_exists and not synced_profile_path:
    issues.append("Objective profile sync state is missing profile_path.")

objective_contract_exists = objective_contract_path.exists()
objective_contract_payload = load_json(objective_contract_path) if objective_contract_exists else {}
checks.append(("objective-contract-present", objective_contract_exists, objective_contract_path.as_posix()))
if not objective_contract_exists:
    issues.append(f"Missing objective contract ({objective_contract_path.as_posix()}).")

strict_contract_exists = strict_contract_path.exists()
strict_contract_payload = load_json(strict_contract_path) if strict_contract_exists else {}
checks.append(("strict-contract-present", strict_contract_exists, strict_contract_path.as_posix()))
if not strict_contract_exists:
    issues.append(f"Missing strict contract ({strict_contract_path.as_posix()}).")

contract_profile_id = str(contract_payload.get("profile_id", "")).strip() if contract_payload else ""
contract_objective_profile = contract_payload.get("objective_profile", {})
if not isinstance(contract_objective_profile, dict):
    contract_objective_profile = {}
contract_objective_contract = str(contract_payload.get("objective_contract", "")).strip() if contract_payload else ""
contract_strict_contract = str(contract_payload.get("strict_contract", "")).strip() if contract_payload else ""
contract_profile_path = str(contract_objective_profile.get("profile_path", "")).strip()
contract_goal_path = str(contract_objective_profile.get("goal_path", "")).strip()
contract_goal_sha = str(contract_objective_profile.get("goal_sha256", "")).strip()
contract_completion_manifest = str(contract_payload.get("completion_manifest", "")).strip() if contract_payload else ""
contract_milestones = sorted(
    str(item).strip() for item in contract_objective_profile.get("milestone_ids", []) if str(item).strip()
)
contract_gates = sorted(
    str(item).strip() for item in contract_objective_profile.get("gate_ids", []) if str(item).strip()
)
checks.append(("audit-contract-profile-id", bool(contract_profile_id), f"profile_id={contract_profile_id or 'missing'}"))
checks.append(("audit-contract-objective-profile-block", bool(contract_objective_profile), "top-level objective_profile metadata"))
checks.append(("audit-contract-objective-contract-path", contract_objective_contract == objective_contract_path.as_posix(), f"value={contract_objective_contract or 'missing'}"))
checks.append(("audit-contract-strict-contract-path", contract_strict_contract == strict_contract_path.as_posix(), f"value={contract_strict_contract or 'missing'}"))
checks.append(("audit-contract-completion-manifest-path", contract_completion_manifest == completion_manifest_path.as_posix(), f"value={contract_completion_manifest or 'missing'}"))
if contract_objective_contract != objective_contract_path.as_posix():
    issues.append(
        f"Audit contract objective_contract path mismatch ({contract_objective_contract or 'missing'} != {objective_contract_path.as_posix()})."
    )
if contract_strict_contract != strict_contract_path.as_posix():
    issues.append(
        f"Audit contract strict_contract path mismatch ({contract_strict_contract or 'missing'} != {strict_contract_path.as_posix()})."
    )
if contract_completion_manifest != completion_manifest_path.as_posix():
    issues.append(
        f"Audit contract completion_manifest path mismatch ({contract_completion_manifest or 'missing'} != {completion_manifest_path.as_posix()})."
    )
if objective_profile_state_exists and contract_profile_id != synced_profile_id:
    issues.append(
        f"Audit contract profile_id mismatch ({contract_profile_id or 'missing'} != {synced_profile_id or 'missing'})."
    )
if objective_profile_state_exists and str(contract_objective_profile.get("profile_id", "")).strip() != synced_profile_id:
    issues.append("Audit contract objective_profile.profile_id does not match synced objective profile.")
if objective_profile_state_exists and contract_profile_path != synced_profile_path:
    issues.append("Audit contract objective_profile.profile_path does not match synced objective profile state.")
if objective_profile_state_exists and contract_goal_path != synced_goal_path:
    issues.append("Audit contract objective_profile.goal_path does not match synced objective profile state.")
if objective_profile_state_exists and contract_goal_sha != synced_goal_sha:
    issues.append("Audit contract objective_profile.goal_sha256 does not match synced objective profile state.")
if objective_profile_state_exists and contract_milestones != synced_milestones:
    issues.append("Audit contract objective_profile.milestone_ids do not match synced objective profile state.")
if objective_profile_state_exists and contract_gates != synced_gates:
    issues.append("Audit contract objective_profile.gate_ids do not match synced objective profile state.")

objective_contract_profile = objective_contract_payload.get("objective_profile", {})
if not isinstance(objective_contract_profile, dict):
    objective_contract_profile = {}
objective_contract_profile_id = str(objective_contract_profile.get("profile_id", "")).strip()
objective_contract_profile_path = str(objective_contract_profile.get("profile_path", "")).strip()
objective_contract_goal_path = str(objective_contract_profile.get("goal_path", "")).strip()
objective_contract_goal_sha = str(objective_contract_profile.get("goal_sha256", "")).strip()
objective_contract_milestones = sorted(
    str(item).strip() for item in objective_contract_profile.get("milestone_ids", []) if str(item).strip()
)
objective_contract_gates = sorted(
    str(item).strip() for item in objective_contract_profile.get("gate_ids", []) if str(item).strip()
)
checks.append(("objective-contract-objective-profile-block", bool(objective_contract_profile), "objective contract objective_profile metadata"))
if objective_profile_state_exists and objective_contract_profile_id != synced_profile_id:
    issues.append("Objective contract objective_profile.profile_id does not match synced objective profile state.")
if objective_profile_state_exists and objective_contract_profile_path != synced_profile_path:
    issues.append("Objective contract objective_profile.profile_path does not match synced objective profile state.")
if objective_profile_state_exists and objective_contract_goal_path != synced_goal_path:
    issues.append("Objective contract objective_profile.goal_path does not match synced objective profile state.")
if objective_profile_state_exists and objective_contract_goal_sha != synced_goal_sha:
    issues.append("Objective contract objective_profile.goal_sha256 does not match synced objective profile state.")
if objective_profile_state_exists and objective_contract_milestones != synced_milestones:
    issues.append("Objective contract objective_profile.milestone_ids do not match synced objective profile state.")
if objective_profile_state_exists and objective_contract_gates != synced_gates:
    issues.append("Objective contract objective_profile.gate_ids do not match synced objective profile state.")

strict_contract_profile_id = str(strict_contract_payload.get("profile_id", "")).strip() if strict_contract_payload else ""
checks.append(("strict-contract-profile-id", bool(strict_contract_profile_id), f"profile_id={strict_contract_profile_id or 'missing'}"))
if objective_profile_state_exists and strict_contract_profile_id != synced_profile_id:
    issues.append("Strict contract profile_id does not match synced objective profile state.")

completion_manifest_exists = completion_manifest_path.exists()
completion_payload = load_json(completion_manifest_path) if completion_manifest_exists else {}
completion_configured = bool(completion_payload.get("configured")) if completion_payload else False
checks.append(("completion-manifest-present", completion_manifest_exists, completion_manifest_path.as_posix()))
if not completion_manifest_exists:
    issues.append("Missing completion manifest (agents/audit/completion_manifest.json).")
checks.append(("completion-manifest-configured", completion_configured, f"configured={completion_configured}"))
if completion_manifest_exists and not completion_configured:
    issues.append("Completion manifest is not configured (`configured=false`).")

manifest_commands: list[str] = []
if completion_manifest_exists:
    cmds = completion_payload.get("required_completion_commands", [])
    if isinstance(cmds, list):
        for item in cmds:
            if not isinstance(item, dict):
                continue
            command = str(item.get("command", "")).strip()
            required = item.get("required", True)
            if command and bool(required):
                manifest_commands.append(command)
manifest_commands = sorted(set(manifest_commands))
checks.append(("completion-required-commands", len(manifest_commands) > 0, f"required_commands={len(manifest_commands)}"))
if completion_manifest_exists and completion_configured and len(manifest_commands) == 0:
    issues.append("Completion manifest is configured but declares no required completion commands.")

contract_command_set: set[str] = set()
runtime_required_count = 0
required_command_by_id: dict[str, str] = {}
for check in contract_checks:
    if not isinstance(check, dict):
        continue
    check_type = str(check.get("type", "")).strip().lower()
    command = str(check.get("command", "")).strip()
    required = bool(check.get("required", True))
    if command:
        contract_command_set.add(command)
    if required and check_type == "command":
        runtime_required_count += 1
        check_id = str(check.get("id", "")).strip()
        if check_id and command:
            required_command_by_id[check_id] = command

missing_manifest_commands = [cmd for cmd in manifest_commands if cmd not in contract_command_set]
checks.append(("completion-commands-covered", len(missing_manifest_commands) == 0, f"missing={len(missing_manifest_commands)}"))
if missing_manifest_commands:
    issues.append(
        "Audit contract missing required completion command(s): "
        + "; ".join(missing_manifest_commands[:5])
    )

checks.append(("audit-completeness-mode", True, f"mode={completeness_mode} max_skips={comprehensive_max_skips}"))
if completeness_mode == "comprehensive":
    sample_command_markers = ("--fast", "--sample", "subset")
    sampled_commands = [
        cmd for cmd in sorted(set(manifest_commands + list(required_command_by_id.values())))
        if any(marker in cmd.lower() for marker in sample_command_markers)
    ]
    checks.append(
        (
            "comprehensive-no-sampling-commands",
            len(sampled_commands) == 0,
            f"sampled_commands={len(sampled_commands)}",
        )
    )
    if sampled_commands:
        issues.append(
                "Comprehensive audit mode forbids sampled completion commands: "
            + "; ".join(sampled_commands[:5])
        )

execution_exists = execution_path.exists()
execution_payload = load_json(execution_path) if execution_exists else {}
execution_checks = execution_payload.get("checks", [])
if not isinstance(execution_checks, list):
    execution_checks = []
checks.append(("audit-execution-present", execution_exists, execution_path.as_posix()))
if not execution_exists:
    issues.append("Missing audit execution artifact (agents/reports/audit_execution.json).")

required_failures = 0
required_blocked = 0
runtime_pass_count = 0
for item in execution_checks:
    if not isinstance(item, dict):
        continue
    status = str(item.get("status", "")).strip().upper()
    required = bool(item.get("required", True))
    check_type = str(item.get("type", "")).strip().lower()
    if required and status != "PASS":
        if status == "BLOCKED":
            required_blocked += 1
        else:
            required_failures += 1
    if required and check_type == "command" and status == "PASS":
        runtime_pass_count += 1

checks.append(
    (
        "required-checks-pass",
        required_failures == 0 and required_blocked == 0,
        f"required_failures={required_failures} required_blocked={required_blocked}",
    )
)
if required_failures or required_blocked:
    issues.append(
        f"{required_failures + required_blocked} required audit check(s) are non-pass "
        f"(fail={required_failures}, blocked={required_blocked})."
    )

checks.append(
    (
        "runtime-command-coverage",
        runtime_pass_count >= runtime_required_count,
        f"runtime_required={runtime_required_count} runtime_pass={runtime_pass_count}",
    )
)
if runtime_pass_count < runtime_required_count:
    issues.append(
        f"Runtime command checks incomplete: required={runtime_required_count}, pass={runtime_pass_count}."
    )

if completeness_mode == "comprehensive":
    comprehensive_skip_total = 0
    comprehensive_skip_sources: list[str] = []
    for item in execution_checks:
        if not isinstance(item, dict):
            continue
        required = bool(item.get("required", True))
        check_type = str(item.get("type", "")).strip().lower()
        status = str(item.get("status", "")).strip().upper()
        if not required or check_type != "command" or status != "PASS":
            continue
        check_id = str(item.get("id", "")).strip()
        command = required_command_by_id.get(check_id, check_id or "unknown")
        combined_logs = combined_logs_for_check(item)
        if not combined_logs:
            continue
        skip_count = extract_skip_count(combined_logs)
        if skip_count > 0:
            comprehensive_skip_total += skip_count
            comprehensive_skip_sources.append(f"{command}={skip_count}")

    checks.append(
        (
            "comprehensive-skip-budget",
            comprehensive_skip_total <= comprehensive_max_skips,
            f"skip_total={comprehensive_skip_total} max={comprehensive_max_skips}",
        )
    )
    if comprehensive_skip_total > comprehensive_max_skips:
        if comprehensive_skip_sources:
            issues.append(
                "Comprehensive audit skip budget exceeded: "
                + "; ".join(comprehensive_skip_sources[:8])
            )
        else:
            issues.append(
                f"Comprehensive audit skip budget exceeded (skip_total={comprehensive_skip_total}, "
                f"max={comprehensive_max_skips})."
            )

decision_path = completion_path if completion_path.exists() else gate_path
decision_exists = decision_path.exists()
decision_payload = load_json(decision_path) if decision_exists else {}
decision_value = str(decision_payload.get("decision", "")).strip().upper()
checks.append(("completion-decision-present", decision_exists, decision_path.as_posix()))
if not decision_exists:
    issues.append(
        "Missing completion decision artifact (agents/reports/completion_decision.json) "
        "and fallback gate decision (agents/reports/audit_gate_decision.json)."
    )
checks.append(("completion-decision-pass", decision_value == "PASS", f"decision={decision_value or 'missing'}"))
if decision_exists and decision_value != "PASS":
    gate_reasons = decision_payload.get("reasons", [])
    if isinstance(gate_reasons, list) and gate_reasons:
        issues.append("Audit gatekeeper rejected completion: " + "; ".join(str(x) for x in gate_reasons[:4]))
    else:
        issues.append("Audit gatekeeper rejected completion.")

queue_pending_count = 0
if queue_specs_dir.exists():
    queue_pending_count = sum(1 for p in queue_specs_dir.iterdir() if p.is_file() and p.name != ".gitkeep")
checks.append(("spec-queue-empty", queue_pending_count == 0, f"pending_specs={queue_pending_count}"))
if queue_pending_count > 0:
    issues.append(f"Spec queue is not empty ({queue_pending_count} pending file(s)).")

golden_count = 0
if golden_dir.exists():
    for path in golden_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() == ".md" and path.name != ".gitkeep":
            golden_count += 1
checks.append(("stable-golden-present", golden_count > 0, f"stable_golden_specs={golden_count}"))
if golden_count == 0:
    issues.append("No stable golden spec artifacts were found.")

gaps_text = ""
actionable_open_gaps = 0
if gaps_path.exists():
    gaps_text = gaps_path.read_text(encoding="utf-8", errors="replace")
    in_open = False
    for line in gaps_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Open Gaps"):
            in_open = True
            continue
        if in_open and stripped.startswith("## "):
            break
        if not in_open or not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.split("|")[1:-1]]
        if len(cells) < 7:
            continue
        gap_id = cells[0]
        status = cells[5].lower()
        if not gap_id or gap_id in {"Gap ID", "---", "GAP-000"}:
            continue
        if "<" in stripped or ">" in stripped:
            continue
        if status == "open":
            actionable_open_gaps += 1

checks.append(("gaps-open-actionable", actionable_open_gaps == 0, f"actionable_open_gaps={actionable_open_gaps}"))
if actionable_open_gaps > 0:
    issues.append(f"{actionable_open_gaps} actionable open gap row(s) remain in agents/gaps.md.")

full_path.parent.mkdir(parents=True, exist_ok=True)
if expectations_exists:
    expectation_snapshot = expectations_text.rstrip()
else:
    expectation_snapshot = "_No expectations file was found; marathon audit generated a failure signal._"

full_body = [
    "# fullexpectations",
    "",
    f"- Generated UTC: {now}",
    f"- Source: `{expectations_path.as_posix()}`",
    "",
    "## Snapshot",
    "```markdown",
    expectation_snapshot,
    "```",
]
full_path.write_text("\n".join(full_body).rstrip() + "\n", encoding="utf-8")

rows = [
    "# marathon results",
    "",
    f"- Generated UTC: {now}",
    f"- fullexpectations: `{full_path.as_posix()}`",
    f"- gaps tracker: `{gaps_path.as_posix()}`",
    f"- audit contract: `{contract_path.as_posix()}`",
    f"- audit execution: `{execution_path.as_posix()}`",
    f"- audit gate decision: `{gate_path.as_posix()}`",
    f"- objective profile sync state: `{objective_profile_state_path.as_posix()}`",
    f"- objective contract: `{objective_contract_path.as_posix()}`",
    f"- strict contract: `{strict_contract_path.as_posix()}`",
    f"- completion manifest: `{completion_manifest_path.as_posix()}`",
    "",
    "## Checks",
    "| Check | Result | Details |",
    "| --- | --- | --- |",
]
for name, passed, detail in checks:
    rows.append(f"| {name} | {'PASS' if passed else 'FAIL'} | {detail} |")

rows.append("")
rows.append("## Open Issues")
if issues:
    for issue in issues:
        rows.append(f"- {issue}")
else:
    rows.append("- none")
results_path.write_text("\n".join(rows).rstrip() + "\n", encoding="utf-8")

if gaps_path.exists():
    current = gaps_text
else:
    current = "# Research Gaps\n\n## Open Gaps\n\n| Gap ID | Stage | Summary | Blocking Scope | Owner | Status | Updated |\n| --- | --- | --- | --- | --- | --- | --- |\n\n## Resolved Gaps\n\n| Gap ID | Resolution | Evidence | Closed At |\n| --- | --- | --- | --- |\n"

scan_lines = [
    "## Marathon Scan",
    f"- Updated UTC: {now}",
    f"- Results: `{results_path.as_posix()}`",
    f"- Fullexpectations: `{full_path.as_posix()}`",
    f"- Open issues detected: {len(issues)}",
    "- Summary:",
]
if issues:
    for issue in issues:
        scan_lines.append(f"  - {issue}")
else:
    scan_lines.append("  - none")
scan_block = "\n".join(scan_lines).rstrip() + "\n"

pattern = re.compile(r"(?ms)^## Marathon Scan\s*\n.*?(?=^## |\Z)")
if pattern.search(current):
    updated = pattern.sub(scan_block, current)
else:
    updated = current.rstrip() + "\n\n" + scan_block
gaps_path.write_text(updated.rstrip() + "\n", encoding="utf-8")

print(len(issues))
PY
}

ensure_marathon_remediation_task_card() {
  local open_gaps="$1"
  local remediation_card_floor="${2:-1}"
  local today product_root scoreboard_path
  today="$(date +%F)"
  product_root="$(drift_policy_product_root)"
  scoreboard_path="$(drift_policy_scoreboard_path)"
  python3 - "$TASKS_PENDING_FILE" "$EXPECTATIONS_FILE" "$MARATHON_RESULTS_REPORT" "$GAPS_FILE" "$AUDIT_CONTRACT_FILE" "$AUDIT_EXECUTION_REPORT" "$AUDIT_GATE_DECISION_FILE" "$AUDIT_COMPLETION_MANIFEST" "$open_gaps" "$remediation_card_floor" "$today" "$TASK_STORE_LOCK_FILE" "$TASK_STORE_LOCK_TIMEOUT_SECS" "$product_root" "$scoreboard_path" <<'PY'
from __future__ import annotations

import fcntl
import re
import sys
import time
from pathlib import Path

pending_path = Path(sys.argv[1])
expectations_file = sys.argv[2]
results_file = sys.argv[3]
gaps_file = sys.argv[4]
contract_file = sys.argv[5]
execution_file = sys.argv[6]
gate_file = sys.argv[7]
completion_manifest_file = sys.argv[8]
open_gaps = sys.argv[9]
try:
    remediation_card_floor = int((sys.argv[10] or "").strip())
except ValueError:
    print(f"ERROR: invalid remediation card floor: {sys.argv[10]}", file=sys.stderr)
    raise SystemExit(2)
if remediation_card_floor < 1:
    remediation_card_floor = 1

today = sys.argv[11]
lock_path = Path(sys.argv[12])
try:
    lock_timeout_secs = int(sys.argv[13])
except ValueError:
    print(f"ERROR: invalid TASK_STORE_LOCK_TIMEOUT_SECS: {sys.argv[13]}", file=sys.stderr)
    raise SystemExit(2)

if lock_timeout_secs < 0:
    print("ERROR: TASK_STORE_LOCK_TIMEOUT_SECS must be >= 0", file=sys.stderr)
    raise SystemExit(2)
product_root = (sys.argv[14] or "").strip() or "src"
scoreboard_path = (sys.argv[15] or "").strip() or "agents/reports/completion_decision.json"

scaffold = (
    "# Tasks Pending\n\n"
    "Temporary queue generated by `agents/_taskmaster.md`.\n"
    "This file is overwritten on each Taskmaster run and merged by `agents/_taskaudit.md`.\n\n"
    "## Pending Task Cards\n"
)

def card_block(
    idx: int,
    title_suffix: str,
    steps: list[str],
    dependencies: str,
    complexity: str,
    lane: str,
) -> str:
    normalized_product_root = product_root.rstrip("/") or product_root
    files_to_touch = [
        f"{normalized_product_root}/",
        scoreboard_path,
        gaps_file,
        expectations_file,
        contract_file,
        execution_file,
        gate_file,
        results_file,
        "agents/reports/audit_logs/",
        "agents/tools/run_marathonqa.sh",
        "agents/research_loop.sh",
        "agents/_taskaudit.md",
    ]
    verification_commands = [
        "bash agents/tools/run_marathonqa.sh",
        f'rg -n "\\\"decision\\\"\\\\s*:\\\\s*\\\"PASS\\\"" {gate_file}',
        f'rg -n "Open issues detected: 0" {results_file}',
        "bash agents/tools/check_marathon_pass_contract.sh",
    ]
    files_block = "\n".join(f"  - `{path}`" for path in files_to_touch)
    steps_block = "\n".join(f"  {i}. {step}" for i, step in enumerate(steps, start=1))
    verify_block = "\n".join(f"  - `{cmd}`" for cmd in verification_commands)
    title = f"Marathon Completion Audit Remediation {idx:02d} - {title_suffix}"
    return f"""
## {today} — {title}

- **Goal:** Resolve completion-audit gaps through root-cause fixes so autonomy can terminate with `agents/AUTONOMY_COMPLETE`.
- **Context:** marathon audit currently reports `{open_gaps}` unresolved issue(s); evidence in `{results_file}` and `{gaps_file}`.
- **Spec-ID:** SPEC-MARATHON-COMPLETION-AUDIT
- **Requirement IDs:** REQ-MARATHON-COMPLETION-AUDIT-001
- **Acceptance IDs:** AC-MARATHON-COMPLETION-AUDIT-001
- **Phase Step IDs:** PHASE_01.1
- **Lane:** {lane}
- **Contract Trace:** objective:default-objective REQ-MARATHON-COMPLETION-AUDIT-001 AC-MARATHON-COMPLETION-AUDIT-001 OUTCOME-audit-gap-closure
- **Prompt Source:** `agents/prompts/taskmaster_decompose.md`
- **Files to touch:**
{files_block}
- **Hard rules (must follow):**
  1. Treat `{completion_manifest_file}` as read-only; do not change required completion command definitions.
  2. Do not replace required completion commands with lighter substitutes, sampled forms, or forced task-count overrides.
  3. If required commands fail, repair the underlying implementation/harness path; do not paper over failures by editing only artifacts.
- **Steps:**
{steps_block}
- **Verification commands:**
{verify_block}
- **Dependencies:** {dependencies}
- **Complexity:** {complexity}
- **Tags:** MARATHON_AUDIT QA_VALIDATION GAP_REMEDIATION ROOT_CAUSE
- **Gates:** completion-audit-pass autonomy-complete-marker
""".strip("\n")

templates: list[tuple[str, list[str], str, str, str]] = [
    (
        "Root-Cause Triage",
        [
            f"Inspect `{execution_file}` and linked audit logs; enumerate each non-pass or semantic mismatch into `{gaps_file}`.",
            f"Identify whether each issue is implementation, harness, contract, or gate-normalization and record deterministic evidence paths.",
            "Define concrete remediation sequence with explicit ownership of command-level fixes before artifact refresh.",
        ],
        "existing GoalSpec/Incident contracts and taskaudit merge path",
        "high",
        "RELIABILITY",
    ),
    (
        "Implementation And Harness Repair",
        [
            "Apply minimal deterministic fixes for failing build/integration/regression commands uncovered in triage.",
            "If a command exits success while reporting failed tests, harden the command or parser semantics to fail closed.",
            "Keep required command set strict and exhaustive; do not downgrade to sampled substitutes.",
        ],
        "Marathon Completion Audit Remediation 01 - Root-Cause Triage",
        "high",
        "OBJECTIVE",
    ),
    (
        "Artifact Regeneration And Gate Sync",
        [
            f"Regenerate `{contract_file}` with exhaustive required checks from immutable `{completion_manifest_file}`.",
            f"Re-run required audit checks to refresh `{execution_file}` and `{results_file}` with command-backed evidence.",
            f"Recompute `{gate_file}` and close actionable rows in `{gaps_file}` only when evidence supports closure.",
        ],
        "Marathon Completion Audit Remediation 02 - Implementation And Harness Repair",
        "high",
        "OBJECTIVE",
    ),
]

while len(templates) < remediation_card_floor:
    batch = len(templates) + 1
    templates.append(
        (
            f"Residual Gap Closure Batch {batch:02d}",
            [
                f"Select a deterministic subset of unresolved findings from `{gaps_file}` and remediate root causes.",
                f"Re-run affected required checks and refresh `{execution_file}` plus `{results_file}` evidence.",
                "Update gap status with explicit evidence paths; keep unresolved blockers open with actionable detail.",
            ],
            "Earlier remediation cards in this same batch",
            "medium",
            "OBJECTIVE",
        )
    )

entries = []
for i, (title, steps, _deps, complexity, lane) in enumerate(templates[:remediation_card_floor]):
    dependencies = "none" if i == 0 else f"Marathon Completion Audit Remediation {i:02d} - {templates[i - 1][0]}"
    entries.append(card_block(i + 1, title, steps, dependencies, complexity, lane))

marathon_heading_re = re.compile(
    r"(?ms)^##\s*\d{4}-\d{2}-\d{2}\s*[—-]\s*Marathon Completion Audit Remediation[^\n]*\n.*?(?=^##\s*\d{4}-\d{2}-\d{2}\s*[—-]|\Z)"
)

lock_path.parent.mkdir(parents=True, exist_ok=True)
start = time.monotonic()
with lock_path.open("a+", encoding="utf-8") as lock_fp:
    while True:
        try:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() - start >= lock_timeout_secs:
                print(
                    f"ERROR: lock timeout while acquiring {lock_path.as_posix()} after {lock_timeout_secs}s",
                    file=sys.stderr,
                )
                raise SystemExit(4)
            time.sleep(0.05)

    try:
        if not pending_path.exists():
            pending_path.parent.mkdir(parents=True, exist_ok=True)
            pending_path.write_text(scaffold, encoding="utf-8")

        pending_text = pending_path.read_text(encoding="utf-8", errors="replace")
        if not pending_text.strip():
            pending_text = scaffold

        normalized = marathon_heading_re.sub("", pending_text).rstrip()
        if "## Pending Task Cards" not in normalized:
            normalized = scaffold.rstrip()

        updated = normalized
        for entry in entries:
            updated += "\n\n" + entry
        updated += "\n"
        pending_path.write_text(updated, encoding="utf-8")
    finally:
        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
PY
}

run_goal_gap_review() {
  python3 "$GOAL_GAP_REVIEW_TOOL" \
    --profile-sync-state "$OBJECTIVE_PROFILE_SYNC_STATE_FILE" \
    --audit-contract "$AUDIT_CONTRACT_FILE" \
    --audit-execution "$AUDIT_EXECUTION_REPORT" \
    --completion-manifest "$AUDIT_COMPLETION_MANIFEST" \
    --audit-gate-decision "$AUDIT_GATE_DECISION_FILE" \
    --completion-decision "$COMPLETION_DECISION_FILE" \
    --tasks "$TASKS_FILE" \
    --tasks-backlog "$TASKS_BACKLOG_FILE" \
    --tasks-pending "$TASKS_PENDING_FILE" \
    --gaps "$GAPS_FILE" \
    --marathon-results "$MARATHON_RESULTS_REPORT" \
    --output-json "$GOAL_GAP_REVIEW_REPORT_JSON" \
    --output-md "$GOAL_GAP_REVIEW_REPORT_MD"
}

goal_gap_unresolved_count() {
  python3 - "$GOAL_GAP_REVIEW_REPORT_JSON" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(1)
payload = json.loads(path.read_text(encoding="utf-8"))
items = payload.get("unresolved_milestone_ids", [])
if not isinstance(items, list):
    raise SystemExit(1)
print(len(items))
PY
}

goal_gap_overall_status() {
  python3 - "$GOAL_GAP_REVIEW_REPORT_JSON" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(1)
payload = json.loads(path.read_text(encoding="utf-8"))
status = str(payload.get("overall_status", "")).strip()
if not status:
    raise SystemExit(1)
print(status)
PY
}

goal_gap_remediation_idea_path_for_goal() {
  local canonical_goal_path="$1"
  local base_name stem

  base_name="$(basename "$canonical_goal_path")"
  stem="${base_name%.md}"
  printf '%s/%s__goal-gap-remediation.md\n' "$STAGING_DIR" "$stem"
}

run_goal_gap_remediation_selection() {
  local canonical_goal_path="$1"
  local remediation_idea_path

  remediation_idea_path="$(goal_gap_remediation_idea_path_for_goal "$canonical_goal_path")"
  python3 "$GOAL_GAP_REMEDIATION_SELECT_TOOL" \
    --goal-gap-review "$GOAL_GAP_REVIEW_REPORT_JSON" \
    --canonical-goal "$canonical_goal_path" \
    --family-policy "$FAMILY_POLICY_FILE" \
    --deferred-follow-ons "$DEFERRED_FOLLOW_ONS_FILE" \
    --output-json "$GOAL_GAP_REMEDIATION_SELECTION_REPORT_JSON" \
    --output-md "$GOAL_GAP_REMEDIATION_SELECTION_REPORT_MD" \
    --output-idea "$remediation_idea_path"
}

stage_goal_gap_remediation_family() {
  local canonical_goal_path="$1"
  local parsed remediation_item_count selected_deferred_count synthesized_count remediation_idea_path

  if [ -z "$canonical_goal_path" ] || [ ! -f "$canonical_goal_path" ]; then
    write_research_status "### BLOCKED"
    log "Goal-gap remediation: missing canonical goal path (${canonical_goal_path:-none})"
    return 1
  fi

  if ! run_goal_gap_remediation_selection "$canonical_goal_path" >/dev/null; then
    write_research_status "### BLOCKED"
    log "Goal-gap remediation: selector failed for canonical goal $canonical_goal_path"
    return 1
  fi

  if ! parsed="$(
    python3 - "$GOAL_GAP_REMEDIATION_SELECTION_REPORT_JSON" <<'PY'
import json
import shlex
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(1)
payload = json.loads(path.read_text(encoding="utf-8"))
pairs = {
    "remediation_item_count": payload.get("total_remediation_items", 0),
    "selected_deferred_count": len(payload.get("selected_deferred_spec_ids", []) or []),
    "synthesized_count": len(payload.get("synthesized_remediation_ids", []) or []),
    "remediation_idea_path": payload.get("output_idea_path", ""),
}
for key, value in pairs.items():
    print(f"{key}={shlex.quote(str(value))}")
PY
  )"; then
    write_research_status "### BLOCKED"
    log "Goal-gap remediation: unable to parse selector report $GOAL_GAP_REMEDIATION_SELECTION_REPORT_JSON"
    return 1
  fi
  # shellcheck disable=SC2086
  eval "$parsed"

  if ! [[ "${remediation_item_count:-}" =~ ^[0-9]+$ ]] || [ "${remediation_item_count:-0}" -lt 1 ]; then
    write_research_status "### BLOCKED"
    log "Goal-gap remediation: selector produced no remediation items for $canonical_goal_path"
    return 1
  fi
  if [ -z "${remediation_idea_path:-}" ] || [ ! -f "$remediation_idea_path" ]; then
    write_research_status "### BLOCKED"
    log "Goal-gap remediation: selector did not stage remediation source idea (${remediation_idea_path:-none})"
    return 1
  fi

  if ! python3 "$SPEC_FAMILY_STATE_TOOL" init \
    --state "$SPEC_FAMILY_STATE_FILE" \
    --goal-file "$canonical_goal_path" \
    --source-idea-path "$remediation_idea_path" \
    --family-phase goal_gap_remediation \
    --force-reset >/dev/null; then
    write_research_status "### BLOCKED"
    log "Goal-gap remediation: failed to reset spec family state for remediation source $remediation_idea_path"
    return 1
  fi

  append_research_event "GOAL_GAP_REMEDIATION_FAMILY_STAGED" \
    "goal=$canonical_goal_path idea=$remediation_idea_path items=$remediation_item_count deferred=$selected_deferred_count synthesized=$synthesized_count report=$GOAL_GAP_REMEDIATION_SELECTION_REPORT_JSON"
  log "Goal-gap remediation: staged remediation family at $remediation_idea_path (items=$remediation_item_count deferred=$selected_deferred_count synthesized=$synthesized_count)"
  return 0
}

ensure_goal_gap_remediation_task_card() {
  local open_gaps="$1"
  local today product_root scoreboard_path
  today="$(date +%F)"
  product_root="$(drift_policy_product_root)"
  scoreboard_path="$(drift_policy_scoreboard_path)"
  python3 - "$GOAL_GAP_REVIEW_REPORT_JSON" "$TASKS_PENDING_FILE" "$GAPS_FILE" "$MARATHON_RESULTS_REPORT" "$AUDIT_CONTRACT_FILE" "$AUDIT_EXECUTION_REPORT" "$AUDIT_GATE_DECISION_FILE" "$COMPLETION_DECISION_FILE" "$AUDIT_COMPLETION_MANIFEST" "$GOAL_GAP_REVIEW_REPORT_MD" "$open_gaps" "$today" "$TASK_STORE_LOCK_FILE" "$TASK_STORE_LOCK_TIMEOUT_SECS" "$product_root" "$scoreboard_path" <<'PY'
from __future__ import annotations

import fcntl
import json
import re
import sys
import time
from pathlib import Path

review_path = Path(sys.argv[1])
pending_path = Path(sys.argv[2])
gaps_file = sys.argv[3]
results_file = sys.argv[4]
contract_file = sys.argv[5]
execution_file = sys.argv[6]
gate_file = sys.argv[7]
completion_file = sys.argv[8]
completion_manifest_file = sys.argv[9]
review_md_file = sys.argv[10]
open_gaps = sys.argv[11]
today = sys.argv[12]
lock_path = Path(sys.argv[13])
try:
    lock_timeout_secs = int(sys.argv[14])
except ValueError:
    print(f"ERROR: invalid TASK_STORE_LOCK_TIMEOUT_SECS: {sys.argv[14]}", file=sys.stderr)
    raise SystemExit(2)
if lock_timeout_secs < 0:
    print("ERROR: TASK_STORE_LOCK_TIMEOUT_SECS must be >= 0", file=sys.stderr)
    raise SystemExit(2)
product_root = (sys.argv[15] or "").strip() or "src"
scoreboard_path = (sys.argv[16] or "").strip() or "agents/reports/completion_decision.json"

if not review_path.exists():
    print(f"ERROR: missing goal gap review: {review_path.as_posix()}", file=sys.stderr)
    raise SystemExit(3)

payload = json.loads(review_path.read_text(encoding="utf-8"))
milestones = payload.get("milestones", [])
if not isinstance(milestones, list):
    print("ERROR: goal gap review milestones must be an array", file=sys.stderr)
    raise SystemExit(5)
unresolved = [item for item in milestones if isinstance(item, dict) and str(item.get("status", "")).strip() != "satisfied"]
if not unresolved:
    print("0")
    raise SystemExit(0)

profile_id = str(payload.get("profile_id", "")).strip() or "goal-gap-profile"
scaffold = (
    "# Tasks Pending\n\n"
    "Temporary queue generated by `agents/_taskmaster.md`.\n"
    "This file is overwritten on each Taskmaster run and merged by `agents/_taskaudit.md`.\n\n"
    "## Pending Task Cards\n"
)

def truncate_title(text: str, limit: int = 72) -> str:
    normalized = " ".join(str(text).strip().split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."

def unique_commands(item: dict) -> list[str]:
    commands: list[str] = []
    seen: set[str] = set()
    for bucket in ("failed_evidence", "blocked_evidence"):
        evidence = item.get(bucket, [])
        if not isinstance(evidence, list):
            continue
        for entry in evidence:
            if not isinstance(entry, dict):
                continue
            command = str(entry.get("command", "")).strip()
            if not command or command in seen:
                continue
            seen.add(command)
            commands.append(command)
    return commands

def card_block(
    *,
    index: int,
    title_suffix: str,
    goal: str,
    context: str,
    steps: list[str],
    verification_commands: list[str],
    dependencies: str,
    complexity: str,
    lane: str,
) -> str:
    normalized_product_root = product_root.rstrip("/") or product_root
    files_to_touch = [
        f"{normalized_product_root}/",
        scoreboard_path,
        gaps_file,
        contract_file,
        execution_file,
        gate_file,
        completion_file,
        results_file,
        review_path.as_posix(),
        review_md_file,
        "agents/reports/audit_logs/",
        "agents/tools/run_marathonqa.sh",
        "agents/tools/goal_gap_review.py",
    ]
    files_block = "\n".join(f"  - `{path}`" for path in files_to_touch)
    steps_block = "\n".join(f"  {i}. {step}" for i, step in enumerate(steps, start=1))
    verify_block = "\n".join(f"  - `{cmd}`" for cmd in verification_commands)
    title = f"Goal Gap Remediation {index:02d} - {title_suffix}"
    return f"""
## {today} — {title}

- **Goal:** {goal}
- **Context:** {context}
- **Spec-ID:** SPEC-GOAL-GAP-REMEDIATION
- **Requirement IDs:** REQ-GOAL-GAP-REMEDIATION-{index:03d}
- **Acceptance IDs:** AC-GOAL-GAP-REMEDIATION-{index:03d}
- **Phase Step IDs:** PHASE_01.{index}
- **Lane:** {lane}
- **Contract Trace:** objective:goal-gap-remediation REQ-GOAL-GAP-REMEDIATION-{index:03d} AC-GOAL-GAP-REMEDIATION-{index:03d} OUTCOME-goal-gap-closure
- **Prompt Source:** `agents/prompts/taskmaster_decompose.md`
- **Files to touch:**
{files_block}
- **Hard rules (must follow):**
  1. Treat `{completion_manifest_file}` as read-only; do not change required completion command definitions.
  2. Do not replace required completion commands with lighter substitutes, sampled forms, or artifact-only paperovers.
  3. Close the underlying milestone gap with command-backed or artifact-backed evidence before refreshing completion markers.
- **Steps:**
{steps_block}
- **Verification commands:**
{verify_block}
- **Dependencies:** {dependencies}
- **Complexity:** {complexity}
- **Tags:** GOAL_GAP_REMEDIATION CAPABILITY_PROOF ROOT_CAUSE
- **Gates:** semantic-goal-gap-closure marathon-audit-pass
""".strip("\n")

entries: list[str] = []
titles: list[str] = []

for idx, milestone in enumerate(unresolved, start=1):
    milestone_id = str(milestone.get("id", "")).strip() or f"CAP-{idx:03d}"
    outcome = str(milestone.get("outcome", "")).strip() or "Close the unresolved semantic goal gap."
    lane = str(milestone.get("remediation_lane", "")).strip() or "OBJECTIVE"
    failed = milestone.get("failed_evidence", [])
    blocked = milestone.get("blocked_evidence", [])
    evidence_refs: list[str] = []
    for bucket in (failed, blocked):
        if not isinstance(bucket, list):
            continue
        for entry in bucket:
            if not isinstance(entry, dict):
                continue
            ref = str(entry.get("ref", "")).strip() or str(entry.get("check_id", "")).strip()
            reason = str(entry.get("reason", "")).strip()
            if ref and reason:
                evidence_refs.append(f"{ref}: {reason}")
            elif ref:
                evidence_refs.append(ref)
    evidence_summary = "; ".join(evidence_refs[:4]) if evidence_refs else "see goal gap review artifacts"
    verify_cmds = unique_commands(milestone)
    if not verify_cmds:
        verify_cmds = ["bash agents/tools/run_marathonqa.sh"]
    verify_cmds.append(
        "python3 agents/tools/goal_gap_review.py --profile-sync-state agents/objective/profile_sync_state.json "
        "--audit-contract agents/reports/audit_contract.json --audit-execution agents/reports/audit_execution.json "
        "--completion-manifest agents/audit/completion_manifest.json --audit-gate-decision agents/reports/audit_gate_decision.json "
        "--completion-decision agents/reports/completion_decision.json --tasks agents/tasks.md --tasks-backlog agents/tasksbacklog.md "
        "--tasks-pending agents/taskspending.md --gaps agents/gaps.md --marathon-results agents/reports/marathon_results.md "
        "--output-json agents/reports/goal_gap_review.json --output-md agents/reports/goal_gap_review.md"
    )
    title_suffix = f"{milestone_id} - {truncate_title(outcome)}"
    titles.append(title_suffix)
    dependencies = "none" if idx == 1 else f"Goal Gap Remediation {idx - 1:02d} - {titles[idx - 2]}"
    steps = [
        f"Inspect `{review_path.as_posix()}` and `{review_md_file}` for milestone `{milestone_id}`; confirm the listed failing or blocked evidence.",
        f"Apply the smallest deterministic fix that closes `{milestone_id}` without weakening `{completion_manifest_file}` or bypassing required proof commands.",
        f"Re-run the affected milestone evidence and refresh marathon artifacts until `{milestone_id}` no longer appears unresolved in the goal gap review.",
    ]
    context = (
        f"Goal gap review reports milestone `{milestone_id}` as `{milestone.get('status', 'unsatisfied')}`; "
        f"open audit gaps currently `{open_gaps}`. Evidence: {evidence_summary}."
    )
    entries.append(
        card_block(
            index=idx,
            title_suffix=title_suffix,
            goal=f"Close semantic capability milestone `{milestone_id}`: {outcome}",
            context=context,
            steps=steps,
            verification_commands=verify_cmds,
            dependencies=dependencies,
            complexity="high",
            lane=lane,
        )
    )

proof_index = len(entries) + 1
proof_dependencies = "none" if proof_index == 1 else f"Goal Gap Remediation {proof_index - 1:02d} - {titles[-1]}"
proof_steps = [
    "Re-run marathon audit verification to refresh contract, execution, results, and gap artifacts after the milestone fixes.",
    "Recompute `agents/reports/goal_gap_review.json` and confirm that `unresolved_milestone_ids` is empty.",
    "Verify completion decision is `PASS` and that no actionable marathon gaps remain before autonomy completion is attempted.",
]
proof_verification = [
    "bash agents/tools/run_marathonqa.sh",
    "python3 agents/tools/goal_gap_review.py --profile-sync-state agents/objective/profile_sync_state.json --audit-contract agents/reports/audit_contract.json --audit-execution agents/reports/audit_execution.json --completion-manifest agents/audit/completion_manifest.json --audit-gate-decision agents/reports/audit_gate_decision.json --completion-decision agents/reports/completion_decision.json --tasks agents/tasks.md --tasks-backlog agents/tasksbacklog.md --tasks-pending agents/taskspending.md --gaps agents/gaps.md --marathon-results agents/reports/marathon_results.md --output-json agents/reports/goal_gap_review.json --output-md agents/reports/goal_gap_review.md",
    "python3 - <<'VERIFY'\nimport json\nfrom pathlib import Path\npayload = json.loads(Path('agents/reports/goal_gap_review.json').read_text())\nassert payload.get('unresolved_milestone_ids') == []\nVERIFY",
    f"rg -n '\"decision\"\\s*:\\s*\"PASS\"' {completion_file}",
]
entries.append(
    card_block(
        index=proof_index,
        title_suffix="Final proof refresh",
        goal="Refresh audit and goal-gap evidence until the semantic profile is fully satisfied.",
        context=(
            f"Goal gap remediation must end with zero unresolved milestone ids, zero actionable marathon gaps, and a PASS completion decision. "
            f"Current review: `{review_path.as_posix()}`."
        ),
        steps=proof_steps,
        verification_commands=proof_verification,
        dependencies=proof_dependencies,
        complexity="medium",
        lane="RELIABILITY",
    )
)

goal_gap_heading_re = re.compile(
    r"(?ms)^##\s*\d{4}-\d{2}-\d{2}\s*[—-]\s*Goal Gap Remediation[^\n]*\n.*?(?=^##\s*\d{4}-\d{2}-\d{2}\s*[—-]|\Z)"
)

lock_path.parent.mkdir(parents=True, exist_ok=True)
start = time.monotonic()
with lock_path.open("a+", encoding="utf-8") as lock_fp:
    while True:
        try:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() - start >= lock_timeout_secs:
                print(
                    f"ERROR: lock timeout while acquiring {lock_path.as_posix()} after {lock_timeout_secs}s",
                    file=sys.stderr,
                )
                raise SystemExit(4)
            time.sleep(0.05)

    try:
        if not pending_path.exists():
            pending_path.parent.mkdir(parents=True, exist_ok=True)
            pending_path.write_text(scaffold, encoding="utf-8")

        pending_text = pending_path.read_text(encoding="utf-8", errors="replace")
        if not pending_text.strip():
            pending_text = scaffold

        normalized = goal_gap_heading_re.sub("", pending_text).rstrip()
        if "## Pending Task Cards" not in normalized:
            normalized = scaffold.rstrip()

        updated = normalized
        for entry in entries:
            updated += "\n\n" + entry
        updated += "\n"
        pending_path.write_text(updated, encoding="utf-8")
    finally:
        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)

print(len(entries))
PY
}

effective_remediation_min_cards() {
  local min_cards="${TASKMASTER_MIN_CARDS_PER_SPEC:-}"
  if ! [[ "${min_cards:-}" =~ ^[0-9]+$ ]] || [ "$min_cards" -lt 1 ]; then
    min_cards=6
  fi
  printf '%s\n' "$min_cards"
}

run_taskaudit_with_temporary_min_cards() {
  local min_cards="${1:-}"
  local previous_min="$TASKMASTER_MIN_CARDS_PER_SPEC"
  local rc=0

  if ! [[ "${min_cards:-}" =~ ^[0-9]+$ ]] || [ "$min_cards" -lt 1 ]; then
    min_cards="$(effective_remediation_min_cards)"
  fi

  TASKMASTER_MIN_CARDS_PER_SPEC="$min_cards"
  export TASKMASTER_MIN_CARDS_PER_SPEC
  if run_stage taskaudit; then
    rc=0
  else
    rc=$?
  fi
  TASKMASTER_MIN_CARDS_PER_SPEC="$previous_min"
  export TASKMASTER_MIN_CARDS_PER_SPEC
  return "$rc"
}

run_marathon_completion_audit() {
  local open_gaps goal_gap_count goal_gap_status remediation_min_cards canonical_goal_path

  write_research_status "### AUDIT_RUNNING"
  append_research_event "MARATHON_AUDIT_START" "trigger=queue-empty-backlog-empty"

  if ! run_stage audit_intake; then
    write_research_status "### AUDIT_FAIL"
    record_audit_outcome "AUDIT_FAIL" "stage=audit_intake"
    append_research_event "MARATHON_AUDIT_FAILED" "stage=audit_intake"
    return 1
  fi
  if ! python3 "$COMMAND_CONTRACT_GUARD_TOOL" \
      --contract "$AUDIT_CONTRACT_FILE" \
      --objective-contract "$OBJECTIVE_CONTRACT_FILE" \
      --output "$COMMAND_CONTRACT_REPORT_FILE"; then
    write_research_status "### AUDIT_FAIL"
    record_audit_outcome "AUDIT_FAIL" "stage=command_contract_guard"
    append_research_event "MARATHON_AUDIT_FAILED" "stage=command_contract_guard report=$COMMAND_CONTRACT_REPORT_FILE"
    return 1
  fi
  if ! run_stage audit_validate; then
    write_research_status "### AUDIT_FAIL"
    record_audit_outcome "AUDIT_FAIL" "stage=audit_validate"
    append_research_event "MARATHON_AUDIT_FAILED" "stage=audit_validate"
    return 1
  fi
  if ! run_stage audit_gatekeeper; then
    write_research_status "### AUDIT_FAIL"
    record_audit_outcome "AUDIT_FAIL" "stage=audit_gatekeeper"
    append_research_event "MARATHON_AUDIT_FAILED" "stage=audit_gatekeeper"
    return 1
  fi
  if ! normalize_audit_gate_decision; then
    write_research_status "### AUDIT_FAIL"
    record_audit_outcome "AUDIT_FAIL" "stage=audit_gatekeeper reason=deterministic-normalizer-failed"
    append_research_event "MARATHON_AUDIT_FAILED" "stage=audit_gatekeeper reason=deterministic-normalizer-failed"
    return 1
  fi

  open_gaps="$(refresh_marathon_audit_artifacts || true)"
  if ! [[ "$open_gaps" =~ ^[0-9]+$ ]]; then
    log "Marathon audit: failed to parse open gap count from artifacts"
    write_research_status "### AUDIT_FAIL"
    record_audit_outcome "AUDIT_FAIL" "reason=invalid-gap-count"
    append_research_event "MARATHON_AUDIT_FAILED" "reason=invalid-gap-count"
    return 1
  fi

  append_research_event "MARATHON_AUDIT_RESULT" "open_gaps=$open_gaps report=$MARATHON_RESULTS_REPORT fullexpectations=$FULL_EXPECTATIONS_REPORT"

  if ! run_goal_gap_review; then
    write_research_status "### AUDIT_FAIL"
    record_audit_outcome "AUDIT_FAIL" "stage=goal_gap_review"
    append_research_event "MARATHON_AUDIT_FAILED" "stage=goal_gap_review"
    return 1
  fi
  goal_gap_count="$(goal_gap_unresolved_count || true)"
  goal_gap_status="$(goal_gap_overall_status || true)"
  if ! [[ "$goal_gap_count" =~ ^[0-9]+$ ]]; then
    write_research_status "### AUDIT_FAIL"
    record_audit_outcome "AUDIT_FAIL" "stage=goal_gap_review reason=invalid-unresolved-count"
    append_research_event "MARATHON_AUDIT_FAILED" "stage=goal_gap_review reason=invalid-unresolved-count"
    return 1
  fi
  append_research_event "GOAL_GAP_REVIEW_RESULT" "unresolved=$goal_gap_count status=${goal_gap_status:-unknown} report=$GOAL_GAP_REVIEW_REPORT_JSON"

  if ! audit_gate_decision_is_pass; then
    if [ "$open_gaps" -eq 0 ]; then
      open_gaps=1
    fi
    append_research_event "MARATHON_AUDIT_GATE_REJECTED" "decision_file=$COMPLETION_DECISION_FILE fallback=$AUDIT_GATE_DECISION_FILE"
  fi
  if tasks_has_real_cards || backlog_has_real_cards || taskspending_has_real_cards; then
    if [ "$open_gaps" -eq 0 ]; then
      open_gaps=1
    fi
    append_research_event "MARATHON_AUDIT_GATE_REJECTED" "reason=task-stores-not-empty"
  fi
  if [ "$open_gaps" -eq 0 ]; then
    if ! bash agents/tools/check_marathon_pass_contract.sh; then
      if [ "$open_gaps" -eq 0 ]; then
        open_gaps=1
      fi
      append_research_event "MARATHON_AUDIT_GATE_REJECTED" "reason=strict-pass-contract-failed"
      log "Marathon audit strict pass contract check failed; scheduling remediation handoff"
    fi
  fi
  if [ "$open_gaps" -eq 0 ] && [ "${DRIFT_ENFORCE_ON_AUTONOMY_COMPLETE:-on}" = "on" ]; then
    if ! enforce_drift_controls "marathon-autonomy-gate" "off"; then
      if [ "$open_gaps" -eq 0 ]; then
        open_gaps=1
      fi
      append_research_event "MARATHON_AUDIT_GATE_REJECTED" "reason=post-audit-gate"
      log "Marathon autonomy completion gate rejected; remediation required"
    fi
  fi

  if [ "$goal_gap_count" -gt 0 ]; then
    canonical_goal_path="$(resolve_objective_profile_sync_goal_path_for_audit || true)"
    if [ -z "$canonical_goal_path" ] || [ ! -f "$canonical_goal_path" ]; then
      write_research_status "### AUDIT_FAIL"
      record_audit_outcome "AUDIT_FAIL" "goal_gaps=$goal_gap_count reason=canonical-goal-unavailable"
      append_research_event "MARATHON_AUDIT_FAILED" "goal_gaps=$goal_gap_count reason=canonical-goal-unavailable"
      return 1
    fi
    if ! stage_goal_gap_remediation_family "$canonical_goal_path"; then
      write_research_status "### AUDIT_FAIL"
      record_audit_outcome "AUDIT_FAIL" "goal_gaps=$goal_gap_count reason=goal-gap-remediation-family-stage-failed"
      append_research_event "MARATHON_AUDIT_FAILED" "goal_gaps=$goal_gap_count reason=goal-gap-remediation-family-stage-failed"
      return 1
    fi
    write_research_status "### AUDIT_FAIL"
    record_audit_outcome "AUDIT_FAIL" "goal_gaps=$goal_gap_count remediation_family=queued"
    return 0
  fi

  if [ "$open_gaps" -gt 0 ]; then
    remediation_min_cards="$(effective_remediation_min_cards)"
    if ! ensure_marathon_remediation_task_card "$open_gaps" "$remediation_min_cards"; then
      write_research_status "### AUDIT_FAIL"
      record_audit_outcome "AUDIT_FAIL" "open_gaps=$open_gaps reason=task_store_lock_timeout"
      append_research_event "MARATHON_AUDIT_FAILED" "open_gaps=$open_gaps reason=task_store_lock_timeout"
      return 1
    fi
    append_research_event "MARATHON_REMEDIATION_ENQUEUED" "open_gaps=$open_gaps taskspending=$TASKS_PENDING_FILE"
    append_research_event "MARATHON_REMEDIATION_TASKAUDIT_PROFILE" "min_cards_per_spec=$remediation_min_cards profile=${TASKMASTER_COMPLEXITY_PROFILE_RESOLVED:-moderate} reason=complexity-profile-remediation-handoff"
    log "Marathon remediation handoff: running Taskaudit with temporary TASKMASTER_MIN_CARDS_PER_SPEC=$remediation_min_cards (profile=${TASKMASTER_COMPLEXITY_PROFILE_RESOLVED:-moderate})"
    if ! run_taskaudit_with_temporary_min_cards "$remediation_min_cards"; then
      write_research_status "### AUDIT_FAIL"
      record_audit_outcome "AUDIT_FAIL" "open_gaps=$open_gaps stage=taskaudit reason=remediation-handoff-failed"
      append_research_event "MARATHON_AUDIT_FAILED" "stage=taskaudit reason=remediation-handoff-failed"
      return 1
    fi
    write_research_status "### AUDIT_FAIL"
    record_audit_outcome "AUDIT_FAIL" "open_gaps=$open_gaps remediation=queued"
    return 0
  fi

  cat >"$AUTONOMY_COMPLETE_MARKER" <<EOF
completed_at: $(utc_now_iso)
source: marathon-audit
results: $MARATHON_RESULTS_REPORT
EOF
  write_research_status "### AUDIT_PASS"
  record_audit_outcome "AUDIT_PASS" "open_gaps=0 autonomy_complete_written=true"
  AUTONOMY_SIGNAL="complete"
  append_research_event "MARATHON_AUTONOMY_COMPLETE_WRITTEN" "marker=$AUTONOMY_COMPLETE_MARKER"
  log "Marathon audit: wrote autonomy completion marker at $AUTONOMY_COMPLETE_MARKER"
  return 0
}

check_autonomy_markers() {
  AUTONOMY_SIGNAL=""

  if [ -f "$STOP_AUTONOMY_MARKER" ]; then
    AUTONOMY_SIGNAL="stop"
    write_research_status "### BLOCKED"
    append_research_event "AUTONOMY_STOP" "marker=$STOP_AUTONOMY_MARKER"
    log "Autonomy stop marker detected at $STOP_AUTONOMY_MARKER"
    return 0
  fi

  if [ -f "$AUTONOMY_COMPLETE_MARKER" ]; then
    AUTONOMY_SIGNAL="complete"
    write_research_status "### IDLE"
    append_research_event "AUTONOMY_COMPLETE" "marker=$AUTONOMY_COMPLETE_MARKER"
    log "Autonomy completion marker detected at $AUTONOMY_COMPLETE_MARKER"
    return 0
  fi

  return 1
}

resolve_research_mode() {
  local prev_mode="$1"
  local forced_mode="${RESEARCH_MODE:-AUTO}"
  prev_mode="$(normalize_research_mode "$prev_mode")"
  capture_queue_snapshot

  if [ "$forced_mode" != "AUTO" ]; then
    printf '%s|forced-by-config\n' "$forced_mode"
    return 0
  fi

  if [ "${MAX_AUTONOMY_MODE:-On}" = "Off" ]; then
    printf 'AUDIT|max-autonomy-off\n'
    return 0
  fi

  if drift_hard_latch_active; then
    if [ "$INCIDENT_QUEUE_HAS_PAYLOAD" = "true" ] || [ "$BLOCKER_QUEUE_HAS_PAYLOAD" = "true" ]; then
      ensure_drift_hard_replan_spec || true
      capture_queue_snapshot
      if [ "$GOALSPEC_QUEUE_HAS_PAYLOAD" = "true" ]; then
        printf 'GOALSPEC|drift-hard-focused-replan\n'
      else
        printf 'AUDIT|drift-hard-incident-pause\n'
      fi
      return 0
    fi
  fi

  if [ "$INCIDENT_QUEUE_HAS_PAYLOAD" = "true" ]; then
    printf 'INCIDENT|incident-queue-ready\n'
    return 0
  fi
  if [ "$BLOCKER_QUEUE_HAS_PAYLOAD" = "true" ]; then
    printf 'INCIDENT|blocker-queue-ready\n'
    return 0
  fi
  if [ "$GOALSPEC_QUEUE_HAS_PAYLOAD" = "true" ]; then
    printf 'GOALSPEC|goal-or-spec-queue-ready\n'
    return 0
  fi
  if [ "$AUDIT_QUEUE_HAS_PAYLOAD" = "true" ]; then
    printf 'AUDIT|audit-queue-ready\n'
    return 0
  fi

  case "$prev_mode" in
    INCIDENT|GOALSPEC) printf 'AUDIT|idle-fallback-after-%s\n' "$prev_mode" ;;
    AUDIT) printf 'AUDIT|idle-audit\n' ;;
    *) printf 'AUDIT|idle-default\n' ;;
  esac
}

update_research_state() {
  local mode="$1"
  local reason="$2"
  local prev_mode="$3"
  mode="$(normalize_research_mode "$mode")"
  prev_mode="$(normalize_research_mode "$prev_mode")"

  python3 - "$RESEARCH_STATE" "$mode" "$reason" "$prev_mode" \
    "$GOALSPEC_QUEUE_HAS_PAYLOAD" "$INCIDENT_QUEUE_HAS_PAYLOAD" "$BLOCKER_QUEUE_HAS_PAYLOAD" "$AUDIT_QUEUE_HAS_PAYLOAD" <<'PY'
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone
import sys

path = Path(sys.argv[1])
mode = sys.argv[2]
reason = sys.argv[3]
prev_mode = sys.argv[4]
goalspec_ready = sys.argv[5].lower() == "true"
incident_ready = sys.argv[6].lower() == "true"
blocker_ready = sys.argv[7].lower() == "true"
audit_ready = sys.argv[8].lower() == "true"

def valid_mode(value: str) -> str:
    if value in {"GOALSPEC", "INCIDENT", "AUDIT"}:
        return value
    return "AUDIT"

previous = {}
if path.exists():
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        previous = {}

old_mode = valid_mode(previous.get("current_mode", prev_mode))
cycle_count = int(previous.get("cycle_count", 0)) + 1
transition_count = int(previous.get("transition_count", 0))
if mode != old_mode:
    transition_count += 1

payload = {
    "schema_version": "1.0",
    "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "current_mode": mode,
    "last_mode": old_mode,
    "mode_reason": reason,
    "cycle_count": cycle_count,
    "transition_count": transition_count,
    "queue_snapshot": {
        "goalspec_ready": goalspec_ready,
        "incident_ready": incident_ready,
        "blocker_ready": blocker_ready,
        "audit_ready": audit_ready,
    },
}

path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

truthy() {
  local normalized
  normalized="$(trim "${1:-}")"
  normalized="$(printf '%s' "$normalized" | tr '[:upper:]' '[:lower:]')"
  case "$normalized" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

is_nonnegative_number() {
  [[ "${1:-}" =~ ^[0-9]+([.][0-9]+)?$ ]]
}

number_ge() {
  python3 - "$1" "$2" <<'PY'
from decimal import Decimal, InvalidOperation
import sys

try:
    a = Decimal((sys.argv[1] or "").strip())
    b = Decimal((sys.argv[2] or "").strip())
except (InvalidOperation, ValueError):
    raise SystemExit(2)

raise SystemExit(0 if a >= b else 1)
PY
}

next_weekly_refresh_info_utc() {
  local spec="$1"
  python3 - "$spec" <<'PY'
from datetime import datetime, timezone, timedelta
import re
import sys

spec = (sys.argv[1] or "").strip().upper()
m = re.match(r"^(MON|TUE|WED|THU|FRI|SAT|SUN)\s+([01]\d|2[0-3]):([0-5]\d)$", spec)
if not m:
    raise SystemExit(2)

target_day = {
    "MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6,
}[m.group(1)]
hour = int(m.group(2))
minute = int(m.group(3))

now = datetime.now(timezone.utc)
candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
candidate += timedelta(days=(target_day - candidate.weekday()) % 7)
if candidate <= now:
    candidate += timedelta(days=7)

print(int(candidate.timestamp()))
print(candidate.strftime("%Y-%m-%d %H:%M UTC"))
PY
}

read_usage_current_from_state() {
  local loop_name="$1"
  python3 - "$USAGE_STATE_FILE" "$loop_name" "${USAGE_SAMPLER_CACHE_MAX_AGE_SECS:-900}" <<'PY'
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

state_path = Path(sys.argv[1])
loop_name = (sys.argv[2] or "").strip()
max_age_raw = (sys.argv[3] or "").strip()
if not state_path.is_file():
    raise SystemExit(1)

try:
    payload = json.loads(state_path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)

entry = (payload.get("loops") or {}).get(loop_name) or {}
value = entry.get("current")
if value is None:
    raise SystemExit(1)

source = str(entry.get("source") or "").strip().lower()
if not (source.startswith("env:") or source.startswith("command:") or source.startswith("codex:")):
    raise SystemExit(1)

try:
    max_age = max(0, int(max_age_raw))
except Exception:
    max_age = 900

if max_age > 0:
    sampled_at_raw = str(entry.get("sampled_at") or "").strip()
    if sampled_at_raw.endswith("Z"):
        sampled_at_raw = sampled_at_raw[:-1] + "+00:00"
    try:
        sampled_at = datetime.fromisoformat(sampled_at_raw)
    except Exception:
        raise SystemExit(1)
    if sampled_at.tzinfo is None:
        raise SystemExit(1)
    age_secs = max(0, int((datetime.now(timezone.utc) - sampled_at.astimezone(timezone.utc)).total_seconds()))
    if age_secs > max_age:
        raise SystemExit(1)

raw = str(value).strip()
if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", raw):
    raise SystemExit(1)

print(raw)
PY
}

write_usage_sampler_warning_artifact() {
  local loop_name="$1"
  local checkpoint="$2"
  local rc="$3"
  local stderr_path="$4"
  local diag_path="$TMP_DIR/usage_sampler_warning_${loop_name}.json"

  python3 - "$diag_path" "$loop_name" "$checkpoint" "$rc" "$stderr_path" "$USAGE_STATE_FILE" <<'PY'
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

diag_path = Path(sys.argv[1])
loop_name = (sys.argv[2] or "").strip()
checkpoint = (sys.argv[3] or "").strip()
rc_raw = (sys.argv[4] or "").strip()
stderr_path = Path(sys.argv[5])
state_path = Path(sys.argv[6])

try:
    rc = int(rc_raw)
except Exception:
    rc = 1

stderr_excerpt = ""
if stderr_path.is_file():
    stderr_excerpt = stderr_path.read_text(encoding="utf-8", errors="replace")[:4000]

payload = {
    "checkpoint": checkpoint,
    "error": "usage sampler execution failed",
    "exit_code": rc,
    "loop": loop_name,
    "ok": False,
    "sampled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "state_file": state_path.as_posix(),
    "stderr_excerpt": stderr_excerpt,
}

diag_path.parent.mkdir(parents=True, exist_ok=True)
tmp_path = diag_path.parent / f".{diag_path.name}.tmp.{os.getpid()}"
tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
tmp_path.replace(diag_path)
print(diag_path.as_posix())
PY
}

refresh_research_weekly_usage_current() {
  local checkpoint="${1:-pre-cycle}"
  local provider out_path err_path sampled rc diag_path cached

  provider="$(trim "${USAGE_SAMPLER_PROVIDER:-codex}")"
  if [ -z "$provider" ]; then
    provider="env"
  fi
  if [ ! -f "$USAGE_SAMPLER_TOOL" ]; then
    log "WARN: Research auto-pause ($checkpoint): usage sampler is missing at $USAGE_SAMPLER_TOOL"
    return 0
  fi

  out_path="$TMP_DIR/usage_sampler_research.out"
  err_path="$TMP_DIR/usage_sampler_research.err"
  RESEARCH_WEEKLY_USAGE_CURRENT=""
  export RESEARCH_WEEKLY_USAGE_CURRENT

  if USAGE_SAMPLER_CODEX_AUTH_SOURCE_DIR="$CODEX_AUTH_SOURCE_DIR" \
     USAGE_SAMPLER_CODEX_HOME="$CODEX_RUNTIME_HOME" \
     python3 "$USAGE_SAMPLER_TOOL" --loop research --provider "$provider" --cache-max-age-secs "${USAGE_SAMPLER_CACHE_MAX_AGE_SECS:-900}" --state-file "$USAGE_STATE_FILE" --print-current >"$out_path" 2>"$err_path"; then
    sampled="$(trim "$(cat "$out_path" 2>/dev/null || true)")"
    if is_nonnegative_number "$sampled"; then
      RESEARCH_WEEKLY_USAGE_CURRENT="$sampled"
      export RESEARCH_WEEKLY_USAGE_CURRENT
      log "Research auto-pause ($checkpoint): sampled RESEARCH_WEEKLY_USAGE_CURRENT=$sampled provider=$provider"
      return 0
    fi
    printf 'invalid sampler output: %s\n' "$sampled" >"$err_path"
    rc=5
  else
    rc=$?
  fi

  diag_path="$(write_usage_sampler_warning_artifact "research" "$checkpoint" "$rc" "$err_path" 2>/dev/null || true)"
  if [ -n "$diag_path" ]; then
    log "WARN: Research auto-pause ($checkpoint): usage sampler failed (rc=$rc) diagnostics=$diag_path"
  else
    log "WARN: Research auto-pause ($checkpoint): usage sampler failed (rc=$rc)"
  fi

  cached="$(read_usage_current_from_state "research" 2>/dev/null || true)"
  if [ -n "$cached" ]; then
    RESEARCH_WEEKLY_USAGE_CURRENT="$cached"
    export RESEARCH_WEEKLY_USAGE_CURRENT
    log "Research auto-pause ($checkpoint): using cached RESEARCH_WEEKLY_USAGE_CURRENT=$cached state=$USAGE_STATE_FILE"
  fi
  return 0
}

resolve_research_weekly_usage_contract() {
  local remaining consumed legacy
  remaining="$(trim "${RESEARCH_WEEKLY_USAGE_REMAINING_THRESHOLD:-}")"
  consumed="$(trim "${RESEARCH_WEEKLY_USAGE_CONSUMED_THRESHOLD:-}")"
  legacy="$(trim "${RESEARCH_WEEKLY_USAGE_THRESHOLD:-}")"

  if [ -n "$remaining" ] && [ -n "$consumed" ]; then
    log "WARN: Research auto-pause: both RESEARCH_WEEKLY_USAGE_REMAINING_THRESHOLD and RESEARCH_WEEKLY_USAGE_CONSUMED_THRESHOLD are set; using remaining semantics"
  fi

  if [ -n "$remaining" ]; then
    printf 'remaining\n%s\nRESEARCH_WEEKLY_USAGE_REMAINING_THRESHOLD\n' "$remaining"
    return 0
  fi

  if [ -n "$consumed" ]; then
    printf 'consumed\n%s\nRESEARCH_WEEKLY_USAGE_CONSUMED_THRESHOLD\n' "$consumed"
    return 0
  fi

  if [ -n "$legacy" ]; then
    printf 'remaining\n%s\nRESEARCH_WEEKLY_USAGE_THRESHOLD\n' "$legacy"
    return 0
  fi

  return 1
}

pause_until_research_weekly_refresh_if_needed() {
  local checkpoint="${1:-pre-cycle}"
  local mode semantics threshold threshold_source current refresh_spec refresh_info usage_contract
  local refresh_epoch refresh_label now_epoch sleep_secs

  mode="$(trim "${USAGE_AUTOPAUSE_MODE:-off}")"
  if ! truthy "$mode"; then
    return 0
  fi

  usage_contract="$(resolve_research_weekly_usage_contract 2>/dev/null || true)"
  if [ -z "$usage_contract" ]; then
    return 0
  fi
  semantics="$(printf '%s\n' "$usage_contract" | sed -n '1p')"
  threshold="$(printf '%s\n' "$usage_contract" | sed -n '2p')"
  threshold_source="$(printf '%s\n' "$usage_contract" | sed -n '3p')"
  threshold_source="${threshold_source:-RESEARCH_WEEKLY_USAGE_THRESHOLD}"
  if ! is_nonnegative_number "$threshold"; then
    log "Research auto-pause ($checkpoint): invalid $threshold_source=\"$threshold\"; skipping pause check"
    return 0
  fi

  current="$(trim "${RESEARCH_WEEKLY_USAGE_CURRENT:-}")"
  if [ -z "$current" ]; then
    log "Research auto-pause ($checkpoint): enabled with semantics=$semantics threshold=$threshold source=$threshold_source but RESEARCH_WEEKLY_USAGE_CURRENT is unset; continuing"
    return 0
  fi
  if ! is_nonnegative_number "$current"; then
    log "Research auto-pause ($checkpoint): invalid RESEARCH_WEEKLY_USAGE_CURRENT=\"$current\"; continuing"
    return 0
  fi

  case "$semantics" in
    remaining)
      if ! number_ge "$threshold" "$current"; then
        log "Research auto-pause ($checkpoint): remaining=$current threshold=$threshold source=$threshold_source -> continue"
        return 0
      fi
      ;;
    consumed)
      if ! number_ge "$current" "$threshold"; then
        log "Research auto-pause ($checkpoint): consumed=$current threshold=$threshold source=$threshold_source -> continue"
        return 0
      fi
      ;;
    *)
      log "Research auto-pause ($checkpoint): unknown semantics=$semantics source=$threshold_source; skipping pause check"
      return 0
      ;;
  esac

  if [ "$threshold_source" = "RESEARCH_WEEKLY_USAGE_THRESHOLD" ]; then
    log "Research auto-pause ($checkpoint): compatibility fallback using RESEARCH_WEEKLY_USAGE_THRESHOLD as remaining threshold"
  fi

  refresh_spec="$(trim "${RESEARCH_WEEKLY_REFRESH_UTC:-MON 00:00}")"
  refresh_info="$(next_weekly_refresh_info_utc "$refresh_spec" 2>/dev/null || true)"
  if [ -z "$refresh_info" ]; then
    log "Research auto-pause ($checkpoint): invalid RESEARCH_WEEKLY_REFRESH_UTC=\"$refresh_spec\"; cannot compute refresh boundary"
    return 0
  fi

  refresh_epoch="$(printf '%s\n' "$refresh_info" | sed -n '1p')"
  refresh_label="$(printf '%s\n' "$refresh_info" | sed -n '2p')"
  now_epoch="$(date +%s)"
  sleep_secs=$(( refresh_epoch - now_epoch ))
  if [ "$sleep_secs" -lt 1 ]; then
    sleep_secs=1
  fi

  log "Research auto-pause ($checkpoint): PAUSE semantics=$semantics current=$current threshold=$threshold source=$threshold_source refresh_utc=\"$refresh_label\" sleep_secs=$sleep_secs"
  sleep "$sleep_secs"
  log "Research auto-pause ($checkpoint): RESUME now_utc=$(date -u '+%Y-%m-%d %H:%M:%S UTC') refresh_utc=\"$refresh_label\""
}

parse_args() {
  local mode_value=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --once)
        MODE="once"
        ;;
      --forever)
        MODE="forever"
        ;;
      --mode)
        if [ "$#" -lt 2 ]; then
          echo "Missing value for --mode (expected GoalSpec|Incident|Audit|Auto)" >&2
          exit "$EXIT_BAD_ARGS"
        fi
        mode_value="$2"
        CLI_MODE_OVERRIDE="$(normalize_mode_or_auto "$mode_value" 2>/dev/null || true)"
        if [ -z "$CLI_MODE_OVERRIDE" ]; then
          echo "Invalid --mode value: $mode_value (expected GoalSpec|Incident|Audit|Auto)" >&2
          exit "$EXIT_BAD_ARGS"
        fi
        shift
        ;;
      --mode=*)
        mode_value="${1#*=}"
        CLI_MODE_OVERRIDE="$(normalize_mode_or_auto "$mode_value" 2>/dev/null || true)"
        if [ -z "$CLI_MODE_OVERRIDE" ]; then
          echo "Invalid --mode value: $mode_value (expected GoalSpec|Incident|Audit|Auto)" >&2
          exit "$EXIT_BAD_ARGS"
        fi
        ;;
      -h|--help)
        cat <<USAGE
Usage: bash agents/research_loop.sh [--once|--forever] [--mode <GoalSpec|Incident|Audit|Auto>]

Options:
  --once     Run at most one stage then exit.
  --forever  Run continuously (default).
  --mode     Force research mode for this run (overrides RESEARCH_MODE config).

Environment:
  IDEA_DEBOUNCE_SECS (default: 120)
  RESEARCH_POLL_SECS (default: 60)
  HEARTBEAT_SECS (default: 60)
  STAGE_RETRY_MAX (default: 1)
  STAGE_RETRY_BACKOFF_SECS (default: 5)
  RESEARCH_FAILURE_BACKOFF_SECS (default: 60)

Exit codes:
  0   Success / clean idle cycle
  2   Invalid CLI args
  3   Configuration or preflight error
  4   Research lock already held
  20  Stage execution failed after retries
  21  STOP_AUTONOMY marker observed
  22  AUTONOMY_COMPLETE marker observed
  30  Runtime/internal failure
USAGE
        exit "$EXIT_OK"
        ;;
      *)
        echo "Unknown argument: $1" >&2
        exit "$EXIT_BAD_ARGS"
        ;;
    esac
    shift
  done
}

parse_model_config() {
  local in_active=0 line key value

  while IFS= read -r line || [ -n "$line" ]; do
    if [ "$in_active" -eq 0 ]; then
      case "$line" in "## Active config"*) in_active=1 ;; esac
      continue
    fi

    case "$line" in ---) break ;; esac

    if [[ "$line" =~ ^[[:space:]]*([A-Z0-9_]+)[[:space:]]*=[[:space:]]*(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      value="$(trim "${BASH_REMATCH[2]}")"
      case "$key" in
        BUILDER_RUNNER) BUILDER_RUNNER="$value" ;;
        BUILDER_MODEL) BUILDER_MODEL="$value" ;;
        ARTICULATE_RUNNER) ARTICULATE_RUNNER="$value" ;;
        ARTICULATE_MODEL) ARTICULATE_MODEL="$value" ;;
        ARTICULATE_EFFORT) ARTICULATE_EFFORT="$value" ;;
        ANALYZE_RUNNER) ANALYZE_RUNNER="$value" ;;
        ANALYZE_MODEL) ANALYZE_MODEL="$value" ;;
        ANALYZE_EFFORT) ANALYZE_EFFORT="$value" ;;
        CLARIFY_RUNNER) CLARIFY_RUNNER="$value" ;;
        CLARIFY_MODEL) CLARIFY_MODEL="$value" ;;
        CLARIFY_EFFORT) CLARIFY_EFFORT="$value" ;;
        GOAL_INTAKE_RUNNER) GOAL_INTAKE_RUNNER="$value" ;;
        GOAL_INTAKE_MODEL) GOAL_INTAKE_MODEL="$value" ;;
        GOAL_INTAKE_EFFORT) GOAL_INTAKE_EFFORT="$value" ;;
        OBJECTIVE_PROFILE_SYNC_RUNNER) OBJECTIVE_PROFILE_SYNC_RUNNER="$value" ;;
        OBJECTIVE_PROFILE_SYNC_MODEL) OBJECTIVE_PROFILE_SYNC_MODEL="$value" ;;
        OBJECTIVE_PROFILE_SYNC_EFFORT) OBJECTIVE_PROFILE_SYNC_EFFORT="$value" ;;
        SPEC_SYNTHESIS_RUNNER) SPEC_SYNTHESIS_RUNNER="$value" ;;
        SPEC_SYNTHESIS_MODEL) SPEC_SYNTHESIS_MODEL="$value" ;;
        SPEC_SYNTHESIS_EFFORT) SPEC_SYNTHESIS_EFFORT="$value" ;;
        SPEC_REVIEW_RUNNER) SPEC_REVIEW_RUNNER="$value" ;;
        SPEC_REVIEW_MODEL) SPEC_REVIEW_MODEL="$value" ;;
        SPEC_REVIEW_EFFORT) SPEC_REVIEW_EFFORT="$value" ;;
        TASKMASTER_RUNNER) TASKMASTER_RUNNER="$value" ;;
        TASKMASTER_MODEL) TASKMASTER_MODEL="$value" ;;
        TASKMASTER_EFFORT) TASKMASTER_EFFORT="$value" ;;
        TASKAUDIT_RUNNER) TASKAUDIT_RUNNER="$value" ;;
        TASKAUDIT_MODEL) TASKAUDIT_MODEL="$value" ;;
        TASKAUDIT_EFFORT) TASKAUDIT_EFFORT="$value" ;;
        CRITIC_RUNNER) CRITIC_RUNNER="$value" ;;
        CRITIC_MODEL) CRITIC_MODEL="$value" ;;
        CRITIC_EFFORT) CRITIC_EFFORT="$value" ;;
        DESIGNER_RUNNER) DESIGNER_RUNNER="$value" ;;
        DESIGNER_MODEL) DESIGNER_MODEL="$value" ;;
        DESIGNER_EFFORT) DESIGNER_EFFORT="$value" ;;
        PHASESPLIT_RUNNER) PHASESPLIT_RUNNER="$value" ;;
        PHASESPLIT_MODEL) PHASESPLIT_MODEL="$value" ;;
        PHASESPLIT_EFFORT) PHASESPLIT_EFFORT="$value" ;;
        INCIDENT_INTAKE_RUNNER) INCIDENT_INTAKE_RUNNER="$value" ;;
        INCIDENT_INTAKE_MODEL) INCIDENT_INTAKE_MODEL="$value" ;;
        INCIDENT_INTAKE_EFFORT) INCIDENT_INTAKE_EFFORT="$value" ;;
        INCIDENT_RESOLVE_RUNNER) INCIDENT_RESOLVE_RUNNER="$value" ;;
        INCIDENT_RESOLVE_MODEL) INCIDENT_RESOLVE_MODEL="$value" ;;
        INCIDENT_RESOLVE_EFFORT) INCIDENT_RESOLVE_EFFORT="$value" ;;
        INCIDENT_ARCHIVE_RUNNER) INCIDENT_ARCHIVE_RUNNER="$value" ;;
        INCIDENT_ARCHIVE_MODEL) INCIDENT_ARCHIVE_MODEL="$value" ;;
        INCIDENT_ARCHIVE_EFFORT) INCIDENT_ARCHIVE_EFFORT="$value" ;;
        AUDIT_INTAKE_RUNNER) AUDIT_INTAKE_RUNNER="$value" ;;
        AUDIT_INTAKE_MODEL) AUDIT_INTAKE_MODEL="$value" ;;
        AUDIT_INTAKE_EFFORT) AUDIT_INTAKE_EFFORT="$value" ;;
        AUDIT_VALIDATE_RUNNER) AUDIT_VALIDATE_RUNNER="$value" ;;
        AUDIT_VALIDATE_MODEL) AUDIT_VALIDATE_MODEL="$value" ;;
        AUDIT_VALIDATE_EFFORT) AUDIT_VALIDATE_EFFORT="$value" ;;
        AUDIT_GATEKEEPER_RUNNER) AUDIT_GATEKEEPER_RUNNER="$value" ;;
        AUDIT_GATEKEEPER_MODEL) AUDIT_GATEKEEPER_MODEL="$value" ;;
        AUDIT_GATEKEEPER_EFFORT) AUDIT_GATEKEEPER_EFFORT="$value" ;;
        MECHANIC_RUNNER) MECHANIC_RUNNER="$value" ;;
        MECHANIC_MODEL) MECHANIC_MODEL="$value" ;;
        MECHANIC_EFFORT) MECHANIC_EFFORT="$value" ;;
        # Backward-compatible aliases.
        RESEARCH_RUNNER) RESEARCH_RUNNER="$value" ;;
        RESEARCH_MODEL) RESEARCH_MODEL="$value" ;;
        *) : ;;
      esac
    fi
  done <"$MODEL_CFG"

  local required=(BUILDER_RUNNER BUILDER_MODEL)
  local k
  for k in "${required[@]}"; do
    if [ -z "${!k:-}" ]; then
      echo "Missing $k in $MODEL_CFG (Active config block)" >&2
      exit "$EXIT_CONFIG_ERROR"
    fi
  done

  : "${RESEARCH_RUNNER:=$BUILDER_RUNNER}"
  : "${RESEARCH_MODEL:=$BUILDER_MODEL}"

  : "${ARTICULATE_RUNNER:=$RESEARCH_RUNNER}"
  : "${ARTICULATE_MODEL:=$RESEARCH_MODEL}"
  : "${ARTICULATE_EFFORT:=high}"

  : "${ANALYZE_RUNNER:=$RESEARCH_RUNNER}"
  : "${ANALYZE_MODEL:=$RESEARCH_MODEL}"
  : "${ANALYZE_EFFORT:=high}"

  : "${CLARIFY_RUNNER:=$RESEARCH_RUNNER}"
  : "${CLARIFY_MODEL:=$RESEARCH_MODEL}"
  : "${CLARIFY_EFFORT:=xhigh}"

  : "${GOAL_INTAKE_RUNNER:=$ARTICULATE_RUNNER}"
  : "${GOAL_INTAKE_MODEL:=$ARTICULATE_MODEL}"
  : "${GOAL_INTAKE_EFFORT:=$ARTICULATE_EFFORT}"

  : "${OBJECTIVE_PROFILE_SYNC_RUNNER:=$GOAL_INTAKE_RUNNER}"
  : "${OBJECTIVE_PROFILE_SYNC_MODEL:=$GOAL_INTAKE_MODEL}"
  : "${OBJECTIVE_PROFILE_SYNC_EFFORT:=$GOAL_INTAKE_EFFORT}"

  : "${SPEC_SYNTHESIS_RUNNER:=$CLARIFY_RUNNER}"
  : "${SPEC_SYNTHESIS_MODEL:=$CLARIFY_MODEL}"
  : "${SPEC_SYNTHESIS_EFFORT:=$CLARIFY_EFFORT}"

  : "${TASKMASTER_RUNNER:=$RESEARCH_RUNNER}"
  : "${TASKMASTER_MODEL:=$RESEARCH_MODEL}"
  : "${TASKMASTER_EFFORT:=xhigh}"

  : "${TASKAUDIT_RUNNER:=$RESEARCH_RUNNER}"
  : "${TASKAUDIT_MODEL:=$RESEARCH_MODEL}"
  : "${TASKAUDIT_EFFORT:=medium}"

  : "${CRITIC_RUNNER:=$RESEARCH_RUNNER}"
  : "${CRITIC_MODEL:=$RESEARCH_MODEL}"
  : "${CRITIC_EFFORT:=high}"

  : "${SPEC_REVIEW_RUNNER:=$CRITIC_RUNNER}"
  : "${SPEC_REVIEW_MODEL:=$CRITIC_MODEL}"
  : "${SPEC_REVIEW_EFFORT:=$CRITIC_EFFORT}"

  : "${DESIGNER_RUNNER:=$RESEARCH_RUNNER}"
  : "${DESIGNER_MODEL:=$RESEARCH_MODEL}"
  : "${DESIGNER_EFFORT:=high}"

  : "${PHASESPLIT_RUNNER:=$RESEARCH_RUNNER}"
  : "${PHASESPLIT_MODEL:=$RESEARCH_MODEL}"
  : "${PHASESPLIT_EFFORT:=high}"

  : "${INCIDENT_INTAKE_RUNNER:=$RESEARCH_RUNNER}"
  : "${INCIDENT_INTAKE_MODEL:=$RESEARCH_MODEL}"
  : "${INCIDENT_INTAKE_EFFORT:=high}"

  : "${INCIDENT_RESOLVE_RUNNER:=$RESEARCH_RUNNER}"
  : "${INCIDENT_RESOLVE_MODEL:=$RESEARCH_MODEL}"
  : "${INCIDENT_RESOLVE_EFFORT:=high}"

  : "${INCIDENT_ARCHIVE_RUNNER:=$RESEARCH_RUNNER}"
  : "${INCIDENT_ARCHIVE_MODEL:=$RESEARCH_MODEL}"
  : "${INCIDENT_ARCHIVE_EFFORT:=medium}"

  : "${AUDIT_INTAKE_RUNNER:=$RESEARCH_RUNNER}"
  : "${AUDIT_INTAKE_MODEL:=$RESEARCH_MODEL}"
  : "${AUDIT_INTAKE_EFFORT:=high}"

  : "${AUDIT_VALIDATE_RUNNER:=$RESEARCH_RUNNER}"
  : "${AUDIT_VALIDATE_MODEL:=$RESEARCH_MODEL}"
  : "${AUDIT_VALIDATE_EFFORT:=high}"

  : "${AUDIT_GATEKEEPER_RUNNER:=$RESEARCH_RUNNER}"
  : "${AUDIT_GATEKEEPER_MODEL:=$RESEARCH_MODEL}"
  : "${AUDIT_GATEKEEPER_EFFORT:=high}"

  : "${MECHANIC_RUNNER:=$RESEARCH_RUNNER}"
  : "${MECHANIC_MODEL:=$RESEARCH_MODEL}"
  : "${MECHANIC_EFFORT:=xhigh}"

  local runner_key
  for runner_key in \
    GOAL_INTAKE_RUNNER OBJECTIVE_PROFILE_SYNC_RUNNER SPEC_SYNTHESIS_RUNNER SPEC_REVIEW_RUNNER \
    ARTICULATE_RUNNER ANALYZE_RUNNER CLARIFY_RUNNER TASKMASTER_RUNNER TASKAUDIT_RUNNER \
    CRITIC_RUNNER DESIGNER_RUNNER PHASESPLIT_RUNNER \
    INCIDENT_INTAKE_RUNNER INCIDENT_RESOLVE_RUNNER INCIDENT_ARCHIVE_RUNNER \
    AUDIT_INTAKE_RUNNER AUDIT_VALIDATE_RUNNER AUDIT_GATEKEEPER_RUNNER MECHANIC_RUNNER; do
    case "${!runner_key}" in
      codex|claude|openclaw) ;;
      *)
        echo "Invalid $runner_key=${!runner_key} in $MODEL_CFG (expected codex|claude|openclaw)" >&2
        exit "$EXIT_CONFIG_ERROR"
        ;;
    esac
  done
}

normalize_taskmaster_complexity_profile() {
  local value
  value="$(printf '%s' "${1:-auto}" | tr '[:upper:]' '[:lower:]')"
  case "$value" in
    auto|trivial|simple|moderate|involved|complex|massive) printf '%s\n' "$value" ;;
    *) printf 'auto\n' ;;
  esac
}

resolve_taskmaster_complexity_profile() {
  local requested="${1:-auto}"
  requested="$(normalize_taskmaster_complexity_profile "$requested")"
  if [ "$requested" != "auto" ]; then
    printf '%s|config|%s\n' "$requested" "$WF_CFG"
    return 0
  fi

  python3 - "$STAGING_DIR" "$QUEUE_SPECS_DIR" "$GOAL_DIR" "$ARTICULATED_DIR" "$RAW_DIR" <<'PY'
from __future__ import annotations

from pathlib import Path
import re
import sys

dirs = [Path(p) for p in sys.argv[1:] if p]

ALIASES = {
    "trivial": "trivial",
    "tiny": "trivial",
    "simple": "simple",
    "small": "simple",
    "basic": "simple",
    "moderate": "moderate",
    "medium": "moderate",
    "involved": "involved",
    "advanced": "involved",
    "complex": "complex",
    "heavy": "complex",
    "hard": "complex",
    "massive": "massive",
    "huge": "massive",
    "epic": "massive",
}

PATTERNS = [
    re.compile(r"(?im)^\s*decomposition(?:[_\-\s](?:profile|tier|class|level|band))?\s*:\s*`?([A-Za-z_-]+)`?[.!]?\s*$"),
    re.compile(r"(?im)^\s*complexity(?:[_\-\s](?:profile|tier|class|level|band))?\s*:\s*`?([A-Za-z_-]+)`?[.!]?\s*$"),
    re.compile(r"(?im)^\s*-\s*Decomposition(?:\s+profile)?\s*:\s*`?([A-Za-z_-]+)`?[.!]?\s*$"),
    re.compile(r"(?im)^\s*\*\*Complexity:\*\*\s*`?([A-Za-z_-]+)`?[.!]?\s*$"),
    re.compile(r"(?im)^\s*-\s*Complexity:\s*`?([A-Za-z_-]+)`?[.!]?\s*$"),
]

HIGH_SCOPE_TOKENS = (
    "language runtime",
    "runtime",
    "build system",
    "build pipeline",
    "from scratch",
    "bootstrap",
    "cross-platform",
    "api",
    "service",
    "database",
    "migration",
    "integration",
    "backend",
    "frontend",
)

def newest_payload(path: Path) -> Path | None:
    if not path.exists() or not path.is_dir():
        return None
    candidates = [p for p in path.iterdir() if p.is_file() and p.name != ".gitkeep"]
    if not candidates:
        return None
    candidates.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    return candidates[0]

def normalize_label(raw: str) -> str | None:
    value = (raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    return ALIASES.get(value)

def explicit_profile(text: str) -> str | None:
    for pattern in PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        profile = normalize_label(m.group(1))
        if profile:
            return profile
    return None

def heuristic_profile(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    line_count = len(lines)
    bullet_count = sum(1 for line in lines if line.startswith("- ") or line.startswith("* "))
    lower = text.lower()
    constraints_count = len(re.findall(r"\b(must|shall|constraint|required|acceptance|verification|gate|milestone)\b", lower))
    unknown_count = len(re.findall(r"\b(unknown|risk|dependency|assumption|question|open)\b", lower))
    high_scope_hits = sum(1 for token in HIGH_SCOPE_TOKENS if token in lower)

    score = 0
    if line_count > 60:
        score += 1
    if line_count > 120:
        score += 1
    if line_count > 220:
        score += 1
    if bullet_count > 10:
        score += 1
    if bullet_count > 20:
        score += 1
    if constraints_count > 12:
        score += 1
    if constraints_count > 24:
        score += 1
    if unknown_count > 8:
        score += 1
    if unknown_count > 18:
        score += 1
    if high_scope_hits > 0:
        score += 1
    if high_scope_hits >= 4:
        score += 1
    if "from scratch" in lower and high_scope_hits >= 3:
        score += 2
    if high_scope_hits >= 6:
        return "massive"

    if score <= 1:
        return "simple"
    if score <= 3:
        return "moderate"
    if score <= 5:
        return "involved"
    if score <= 7:
        return "complex"
    return "massive"

selected = []
for root in dirs:
    payload = newest_payload(root)
    if payload:
        selected.append(payload)

for path in selected:
    text = path.read_text(encoding="utf-8", errors="replace")
    profile = explicit_profile(text)
    if profile:
        print(f"{profile}|explicit|{path.as_posix()}")
        raise SystemExit(0)

if selected:
    path = selected[0]
    text = path.read_text(encoding="utf-8", errors="replace")
    print(f"{heuristic_profile(text)}|heuristic|{path.as_posix()}")
else:
    print("moderate|fallback|none")
PY
}

apply_taskmaster_complexity_profile() {
  local profile
  profile="$(normalize_taskmaster_complexity_profile "${1:-moderate}")"
  case "$profile" in
    trivial)
      TASKMASTER_MIN_CARDS_PER_SPEC=1
      TASKMASTER_TARGET_CARDS_PER_SPEC=2
      TASKMASTER_MIN_TOTAL_CARDS=0
      TASKMASTER_TARGET_TOTAL_CARDS=0
      ;;
    simple)
      TASKMASTER_MIN_CARDS_PER_SPEC=3
      TASKMASTER_TARGET_CARDS_PER_SPEC=5
      TASKMASTER_MIN_TOTAL_CARDS=0
      TASKMASTER_TARGET_TOTAL_CARDS=0
      ;;
    moderate)
      TASKMASTER_MIN_CARDS_PER_SPEC=6
      TASKMASTER_TARGET_CARDS_PER_SPEC=10
      TASKMASTER_MIN_TOTAL_CARDS=0
      TASKMASTER_TARGET_TOTAL_CARDS=0
      ;;
    involved)
      TASKMASTER_MIN_CARDS_PER_SPEC=12
      TASKMASTER_TARGET_CARDS_PER_SPEC=16
      TASKMASTER_MIN_TOTAL_CARDS=0
      TASKMASTER_TARGET_TOTAL_CARDS=0
      ;;
    complex)
      TASKMASTER_MIN_CARDS_PER_SPEC=20
      TASKMASTER_TARGET_CARDS_PER_SPEC=28
      TASKMASTER_MIN_TOTAL_CARDS=0
      TASKMASTER_TARGET_TOTAL_CARDS=0
      ;;
    massive)
      TASKMASTER_MIN_CARDS_PER_SPEC=30
      TASKMASTER_TARGET_CARDS_PER_SPEC=45
      TASKMASTER_MIN_TOTAL_CARDS=0
      TASKMASTER_TARGET_TOTAL_CARDS=0
      ;;
    *)
      TASKMASTER_MIN_CARDS_PER_SPEC=6
      TASKMASTER_TARGET_CARDS_PER_SPEC=10
      TASKMASTER_MIN_TOTAL_CARDS=0
      TASKMASTER_TARGET_TOTAL_CARDS=0
      ;;
  esac
}

parse_workflow_config() {
  normalize_choice_ci() {
    local value="$1"
    local default="$2"
    shift 2
    local v candidate c
    v="$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')"
    for candidate in "$@"; do
      c="$(printf '%s' "$candidate" | tr '[:upper:]' '[:lower:]')"
      if [ "$v" = "$c" ]; then
        printf '%s\n' "$candidate"
        return 0
      fi
    done
    printf '%s\n' "$default"
  }

  normalize_mode() {
    local v
    v="$(printf '%s' "${1:-AUTO}" | tr '[:lower:]' '[:upper:]')"
    case "$v" in
      AUTO|GOALSPEC|INCIDENT|AUDIT) printf '%s\n' "$v" ;;
      *) printf 'AUTO\n' ;;
    esac
  }

  normalize_int_min() {
    local value="${1:-}"
    local default="$2"
    local min="$3"
    if [[ "$value" =~ ^[0-9]+$ ]] && [ "$value" -ge "$min" ]; then
      printf '%s\n' "$value"
    else
      printf '%s\n' "$default"
    fi
  }

  normalize_dec_0_1() {
    local value="${1:-}"
    local default="$2"
    if python3 - "$value" <<'PY'
from decimal import Decimal, InvalidOperation
import sys
try:
    v = Decimal((sys.argv[1] or "").strip())
except (InvalidOperation, ValueError):
    raise SystemExit(1)
raise SystemExit(0 if Decimal("0") <= v <= Decimal("1") else 1)
PY
    then
      printf '%s\n' "$value"
    else
      printf '%s\n' "$default"
    fi
  }

  normalize_dec_nonnegative() {
    local value="${1:-}"
    local default="$2"
    if python3 - "$value" <<'PY'
from decimal import Decimal, InvalidOperation
import sys
try:
    v = Decimal((sys.argv[1] or "").strip())
except (InvalidOperation, ValueError):
    raise SystemExit(1)
raise SystemExit(0 if v >= Decimal("0") else 1)
PY
    then
      printf '%s\n' "$value"
    else
      printf '%s\n' "$default"
    fi
  }

  normalize_bool() {
    local value="${1:-}"
    local default="${2:-false}"
    case "$value" in
      1|true|TRUE|yes|YES|on|ON) printf 'true\n' ;;
      0|false|FALSE|no|NO|off|OFF) printf 'false\n' ;;
      *) printf '%s\n' "$default" ;;
    esac
  }

  local line key value
  while IFS= read -r line || [ -n "$line" ]; do
    if [[ "$line" =~ ^##[[:space:]]*([A-Z0-9_]+)[[:space:]]*=[[:space:]]*(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      value="$(trim "${BASH_REMATCH[2]}")"
      case "$key" in
        HEADLESS_PERMISSIONS) HEADLESS_PERMISSIONS="$value" ;;
        OPENCLAW_GATEWAY_URL) OPENCLAW_GATEWAY_URL="$value" ;;
        OPENCLAW_AGENT_ID) OPENCLAW_AGENT_ID="$value" ;;
        USAGE_AUTOPAUSE_MODE) USAGE_AUTOPAUSE_MODE="$value" ;;
        USAGE_SAMPLER_PROVIDER) USAGE_SAMPLER_PROVIDER="$value" ;;
        USAGE_SAMPLER_CACHE_MAX_AGE_SECS) USAGE_SAMPLER_CACHE_MAX_AGE_SECS="$value" ;;
        USAGE_SAMPLER_ORCH_CMD) USAGE_SAMPLER_ORCH_CMD="$value" ;;
        USAGE_SAMPLER_RESEARCH_CMD) USAGE_SAMPLER_RESEARCH_CMD="$value" ;;
        CODEX_AUTH_SOURCE_DIR) CODEX_AUTH_SOURCE_DIR="$value" ;;
        CODEX_RUNTIME_HOME) CODEX_RUNTIME_HOME="$value" ;;
        RESEARCH_WEEKLY_USAGE_REMAINING_THRESHOLD) RESEARCH_WEEKLY_USAGE_REMAINING_THRESHOLD="$value" ;;
        RESEARCH_WEEKLY_USAGE_CONSUMED_THRESHOLD) RESEARCH_WEEKLY_USAGE_CONSUMED_THRESHOLD="$value" ;;
        RESEARCH_WEEKLY_USAGE_THRESHOLD) RESEARCH_WEEKLY_USAGE_THRESHOLD="$value" ;;
        RESEARCH_WEEKLY_REFRESH_UTC) RESEARCH_WEEKLY_REFRESH_UTC="$value" ;;
        MAX_AUTONOMY_MODE) MAX_AUTONOMY_MODE="$value" ;;
        RESEARCH_MODE) RESEARCH_MODE="$value" ;;
        SPEC_INTERROGATION_ROUNDS) SPEC_INTERROGATION_ROUNDS="$value" ;;
        PHASE_INTERROGATION_ROUNDS) PHASE_INTERROGATION_ROUNDS="$value" ;;
        SPEC_QUALITY_THRESHOLD) SPEC_QUALITY_THRESHOLD="$value" ;;
        PHASE_ASSUMPTIONS_BUDGET) PHASE_ASSUMPTIONS_BUDGET="$value" ;;
        SPEC_QUALITY_FAIL_MAX) SPEC_QUALITY_FAIL_MAX="$value" ;;
        INCIDENT_FIXSPEC_INTERROGATION_ROUNDS) INCIDENT_FIXSPEC_INTERROGATION_ROUNDS="$value" ;;
        INCIDENT_MAX_CYCLES) INCIDENT_MAX_CYCLES="$value" ;;
        AUDIT_TRIGGER) AUDIT_TRIGGER="$value" ;;
        RESEARCH_IDLE_MODE) RESEARCH_IDLE_MODE="$value" ;;
        RESEARCH_IDLE_POLL_SECS) RESEARCH_IDLE_POLL_SECS="$value" ;;
        RESEARCH_LOCKING) RESEARCH_LOCKING="$value" ;;
        RESEARCH_ALLOW_SEARCH) RESEARCH_ALLOW_SEARCH="$value" ;;
        RESEARCH_ALLOW_SEARCH_EXCEPTION) RESEARCH_ALLOW_SEARCH_EXCEPTION="$value" ;;
        OBJECTIVE_CONTRACT_SCHEMA_FILE) OBJECTIVE_CONTRACT_SCHEMA_FILE="$value" ;;
        OBJECTIVE_CONTRACT_FILE) OBJECTIVE_CONTRACT_FILE="$value" ;;
        AUDIT_COMPLETION_MANIFEST) AUDIT_COMPLETION_MANIFEST="$value" ;;
        AUDIT_STRICT_CONTRACT_FILE) AUDIT_STRICT_CONTRACT_FILE="$value" ;;
        COMMAND_CONTRACT_REPORT_FILE) COMMAND_CONTRACT_REPORT_FILE="$value" ;;
        AUDIT_COMPLETENESS_MODE) AUDIT_COMPLETENESS_MODE="$value" ;;
        AUDIT_COMPREHENSIVE_MAX_SKIPS) AUDIT_COMPREHENSIVE_MAX_SKIPS="$value" ;;
        DRIFT_CONTROL_MODE) DRIFT_CONTROL_MODE="$value" ;;
        DRIFT_CONTROL_POLICY_FILE) DRIFT_CONTROL_POLICY_FILE="$value" ;;
        DRIFT_ENFORCE_ON_AUTONOMY_COMPLETE) DRIFT_ENFORCE_ON_AUTONOMY_COMPLETE="$value" ;;
        DRIFT_DETECTOR_MODE) DRIFT_DETECTOR_MODE="$value" ;;
        DRIFT_STATUS_REPORT_FILE) DRIFT_STATUS_REPORT_FILE="$value" ;;
        QUEUE_GOVERNOR_MODE) QUEUE_GOVERNOR_MODE="$value" ;;
        QUEUE_GOVERNOR_REPORT_FILE) QUEUE_GOVERNOR_REPORT_FILE="$value" ;;
        GOVERNANCE_CANARY_MODE) GOVERNANCE_CANARY_MODE="$value" ;;
        GOVERNANCE_CANARY_BASELINE_POLICY_FILE) GOVERNANCE_CANARY_BASELINE_POLICY_FILE="$value" ;;
        GOVERNANCE_CANARY_REPORT_FILE) GOVERNANCE_CANARY_REPORT_FILE="$value" ;;
        GOVERNANCE_CANARY_ARCHIVE_ROOTS) GOVERNANCE_CANARY_ARCHIVE_ROOTS="$value" ;;
        GOVERNANCE_CANARY_MAX_SCENARIOS) GOVERNANCE_CANARY_MAX_SCENARIOS="$value" ;;
        GOVERNANCE_CANARY_MIN_OBJECTIVE_SHARE_DELTA) GOVERNANCE_CANARY_MIN_OBJECTIVE_SHARE_DELTA="$value" ;;
        GOVERNANCE_CANARY_MIN_OUTCOME_DELTA_PROXY_DELTA) GOVERNANCE_CANARY_MIN_OUTCOME_DELTA_PROXY_DELTA="$value" ;;
        GOVERNANCE_CANARY_MAX_BLOCKER_RATE_DELTA) GOVERNANCE_CANARY_MAX_BLOCKER_RATE_DELTA="$value" ;;
        TASKMASTER_COMPLEXITY_PROFILE) TASKMASTER_COMPLEXITY_PROFILE="$value" ;;
        TASKMASTER_MIN_CARDS_PER_SPEC) TASKMASTER_MIN_CARDS_PER_SPEC="$value" ;;
        TASKMASTER_MAX_CARDS_PER_SPEC) TASKMASTER_MAX_CARDS_PER_SPEC="$value" ;;
        TASKMASTER_TARGET_CARDS_PER_SPEC) TASKMASTER_TARGET_CARDS_PER_SPEC="$value" ;;
        TASKMASTER_MIN_TOTAL_CARDS) TASKMASTER_MIN_TOTAL_CARDS="$value" ;;
        TASKMASTER_TARGET_TOTAL_CARDS) TASKMASTER_TARGET_TOTAL_CARDS="$value" ;;
        TASKCARD_TARGET_SHORTFALL_MODE) TASKCARD_TARGET_SHORTFALL_MODE="$value" ;;
        TASKCARD_FORMAT_STRICT) TASKCARD_FORMAT_STRICT="$value" ;;
        TASKCARD_ENFORCE_EXECUTION_TEMPLATE) TASKCARD_ENFORCE_EXECUTION_TEMPLATE="$value" ;;
        TASKCARD_PHASE_WORKPLAN_COVERAGE) TASKCARD_PHASE_WORKPLAN_COVERAGE="$value" ;;
        TASKCARD_MAX_PHASE_STEPS_PER_CARD) TASKCARD_MAX_PHASE_STEPS_PER_CARD="$value" ;;
        TASKCARD_SCOPE_LINT) TASKCARD_SCOPE_LINT="$value" ;;
        SPEC_DECOMPOSITION_GOVERNANCE) SPEC_DECOMPOSITION_GOVERNANCE="$value" ;;
        STAGE_RETRY_MAX) STAGE_RETRY_MAX="$value" ;;
        STAGE_RETRY_BACKOFF_SECS) STAGE_RETRY_BACKOFF_SECS="$value" ;;
        RESEARCH_FAILURE_BACKOFF_SECS) RESEARCH_FAILURE_BACKOFF_SECS="$value" ;;
        NETWORK_GUARD_MODE) NETWORK_GUARD_MODE="$value" ;;
        RESEARCH_NETWORK_GUARD_POLICY) RESEARCH_NETWORK_GUARD_POLICY="$value" ;;
        RESEARCH_NETWORK_POLICY_EXCEPTION) RESEARCH_NETWORK_POLICY_EXCEPTION="$value" ;;
        ENV_PREFLIGHT_MODE) ENV_PREFLIGHT_MODE="$value" ;;
        ENV_PREFLIGHT_TRANSPORT_CHECK) ENV_PREFLIGHT_TRANSPORT_CHECK="$value" ;;
        NETWORK_OUTAGE_RESILIENCE_MODE) NETWORK_OUTAGE_RESILIENCE_MODE="$value" ;;
        NETWORK_OUTAGE_WAIT_INITIAL_SECS) NETWORK_OUTAGE_WAIT_INITIAL_SECS="$value" ;;
        NETWORK_OUTAGE_WAIT_MAX_SECS) NETWORK_OUTAGE_WAIT_MAX_SECS="$value" ;;
        NETWORK_OUTAGE_MAX_PROBES) NETWORK_OUTAGE_MAX_PROBES="$value" ;;
        NETWORK_OUTAGE_PROBE_TIMEOUT_SECS) NETWORK_OUTAGE_PROBE_TIMEOUT_SECS="$value" ;;
        NETWORK_OUTAGE_PROBE_HOST) NETWORK_OUTAGE_PROBE_HOST="$value" ;;
        NETWORK_OUTAGE_PROBE_PORT) NETWORK_OUTAGE_PROBE_PORT="$value" ;;
        NETWORK_OUTAGE_PROBE_CMD) NETWORK_OUTAGE_PROBE_CMD="$value" ;;
        NETWORK_OUTAGE_POLICY) NETWORK_OUTAGE_POLICY="$value" ;;
        NETWORK_OUTAGE_ROUTE_TO_BLOCKER) NETWORK_OUTAGE_ROUTE_TO_BLOCKER="$value" ;;
        NETWORK_OUTAGE_ROUTE_TO_INCIDENT) NETWORK_OUTAGE_ROUTE_TO_INCIDENT="$value" ;;
        ARTICULATE_SEARCH) ARTICULATE_SEARCH="$value" ;;
        ARTICULATE_TIMEOUT_SECS) ARTICULATE_TIMEOUT_SECS="$value" ;;
        ANALYZE_SEARCH) ANALYZE_SEARCH="$value" ;;
        ANALYZE_TIMEOUT_SECS) ANALYZE_TIMEOUT_SECS="$value" ;;
        CLARIFY_SEARCH) CLARIFY_SEARCH="$value" ;;
        CLARIFY_TIMEOUT_SECS) CLARIFY_TIMEOUT_SECS="$value" ;;
        GOAL_INTAKE_SEARCH) GOAL_INTAKE_SEARCH="$value" ;;
        GOAL_INTAKE_TIMEOUT_SECS) GOAL_INTAKE_TIMEOUT_SECS="$value" ;;
        OBJECTIVE_PROFILE_SYNC_SEARCH) OBJECTIVE_PROFILE_SYNC_SEARCH="$value" ;;
        OBJECTIVE_PROFILE_SYNC_TIMEOUT_SECS) OBJECTIVE_PROFILE_SYNC_TIMEOUT_SECS="$value" ;;
        SPEC_SYNTHESIS_SEARCH) SPEC_SYNTHESIS_SEARCH="$value" ;;
        SPEC_SYNTHESIS_TIMEOUT_SECS) SPEC_SYNTHESIS_TIMEOUT_SECS="$value" ;;
        SPEC_REVIEW_SEARCH) SPEC_REVIEW_SEARCH="$value" ;;
        SPEC_REVIEW_TIMEOUT_SECS) SPEC_REVIEW_TIMEOUT_SECS="$value" ;;
        TASKMASTER_SEARCH) TASKMASTER_SEARCH="$value" ;;
        TASKMASTER_TIMEOUT_SECS) TASKMASTER_TIMEOUT_SECS="$value" ;;
        TASKAUDIT_SEARCH) TASKAUDIT_SEARCH="$value" ;;
        TASKAUDIT_TIMEOUT_SECS) TASKAUDIT_TIMEOUT_SECS="$value" ;;
        CRITIC_SEARCH) CRITIC_SEARCH="$value" ;;
        CRITIC_TIMEOUT_SECS) CRITIC_TIMEOUT_SECS="$value" ;;
        DESIGNER_SEARCH) DESIGNER_SEARCH="$value" ;;
        DESIGNER_TIMEOUT_SECS) DESIGNER_TIMEOUT_SECS="$value" ;;
        PHASESPLIT_SEARCH) PHASESPLIT_SEARCH="$value" ;;
        PHASESPLIT_TIMEOUT_SECS) PHASESPLIT_TIMEOUT_SECS="$value" ;;
        INCIDENT_INTAKE_SEARCH) INCIDENT_INTAKE_SEARCH="$value" ;;
        INCIDENT_INTAKE_TIMEOUT_SECS) INCIDENT_INTAKE_TIMEOUT_SECS="$value" ;;
        INCIDENT_RESOLVE_SEARCH) INCIDENT_RESOLVE_SEARCH="$value" ;;
        INCIDENT_RESOLVE_TIMEOUT_SECS) INCIDENT_RESOLVE_TIMEOUT_SECS="$value" ;;
        INCIDENT_ARCHIVE_SEARCH) INCIDENT_ARCHIVE_SEARCH="$value" ;;
        INCIDENT_ARCHIVE_TIMEOUT_SECS) INCIDENT_ARCHIVE_TIMEOUT_SECS="$value" ;;
        AUDIT_INTAKE_SEARCH) AUDIT_INTAKE_SEARCH="$value" ;;
        AUDIT_INTAKE_TIMEOUT_SECS) AUDIT_INTAKE_TIMEOUT_SECS="$value" ;;
        AUDIT_VALIDATE_SEARCH) AUDIT_VALIDATE_SEARCH="$value" ;;
        AUDIT_VALIDATE_TIMEOUT_SECS) AUDIT_VALIDATE_TIMEOUT_SECS="$value" ;;
        AUDIT_GATEKEEPER_SEARCH) AUDIT_GATEKEEPER_SEARCH="$value" ;;
        AUDIT_GATEKEEPER_TIMEOUT_SECS) AUDIT_GATEKEEPER_TIMEOUT_SECS="$value" ;;
        MECHANIC_SEARCH) MECHANIC_SEARCH="$value" ;;
        MECHANIC_TIMEOUT_SECS) MECHANIC_TIMEOUT_SECS="$value" ;;
        *) : ;;
      esac
    fi
  done <"$WF_CFG"

  : "${HEADLESS_PERMISSIONS:=Maximum}"
  : "${OPENCLAW_GATEWAY_URL:=http://127.0.0.1:18789}"
  : "${OPENCLAW_AGENT_ID:=main}"
  : "${USAGE_AUTOPAUSE_MODE:=off}"
  : "${USAGE_SAMPLER_PROVIDER:=codex}"
  : "${USAGE_SAMPLER_CACHE_MAX_AGE_SECS:=900}"
  : "${USAGE_SAMPLER_ORCH_CMD:=}"
  : "${USAGE_SAMPLER_RESEARCH_CMD:=}"
  : "${CODEX_AUTH_SOURCE_DIR:=$HOME/.codex}"
  : "${CODEX_RUNTIME_HOME:=$TMP_DIR/codex-runtime-home}"
  : "${RESEARCH_WEEKLY_USAGE_REMAINING_THRESHOLD:=}"
  : "${RESEARCH_WEEKLY_USAGE_CONSUMED_THRESHOLD:=}"
  : "${RESEARCH_WEEKLY_USAGE_THRESHOLD:=}"
  : "${RESEARCH_WEEKLY_REFRESH_UTC:=MON 00:00}"
  : "${MAX_AUTONOMY_MODE:=On}"
  : "${RESEARCH_MODE:=AUTO}"
  : "${SPEC_INTERROGATION_ROUNDS:=2}"
  : "${PHASE_INTERROGATION_ROUNDS:=1}"
  : "${SPEC_QUALITY_THRESHOLD:=0.75}"
  : "${PHASE_ASSUMPTIONS_BUDGET:=8}"
  : "${SPEC_QUALITY_FAIL_MAX:=2}"
  : "${INCIDENT_FIXSPEC_INTERROGATION_ROUNDS:=1}"
  : "${INCIDENT_MAX_CYCLES:=3}"
  : "${AUDIT_TRIGGER:=queue_empty}"
  : "${RESEARCH_IDLE_MODE:=poll}"
  : "${RESEARCH_IDLE_POLL_SECS:=${RESEARCH_POLL_SECS:-60}}"
  : "${RESEARCH_LOCKING:=off}"
  : "${RESEARCH_ALLOW_SEARCH:=off}"
  : "${RESEARCH_ALLOW_SEARCH_EXCEPTION:=off}"
  : "${OBJECTIVE_CONTRACT_SCHEMA_FILE:=agents/objective/contract.schema.json}"
  : "${OBJECTIVE_CONTRACT_FILE:=agents/objective/contract.yaml}"
  : "${AUDIT_COMPLETION_MANIFEST:=agents/audit/completion_manifest.json}"
  : "${AUDIT_STRICT_CONTRACT_FILE:=agents/audit/strict_contract.json}"
  : "${COMMAND_CONTRACT_REPORT_FILE:=agents/reports/command_contract.json}"
  : "${AUDIT_COMPLETENESS_MODE:=comprehensive}"
  : "${AUDIT_COMPREHENSIVE_MAX_SKIPS:=0}"
  : "${DRIFT_CONTROL_MODE:=off}"
  : "${DRIFT_CONTROL_POLICY_FILE:=agents/policies/drift_control_policy.json}"
  : "${DRIFT_ENFORCE_ON_AUTONOMY_COMPLETE:=off}"
  : "${DRIFT_DETECTOR_MODE:=off}"
  : "${DRIFT_STATUS_REPORT_FILE:=agents/reports/drift_status.json}"
  : "${QUEUE_GOVERNOR_MODE:=off}"
  : "${QUEUE_GOVERNOR_REPORT_FILE:=agents/reports/queue_governor.json}"
  : "${GOVERNANCE_CANARY_MODE:=off}"
  : "${GOVERNANCE_CANARY_BASELINE_POLICY_FILE:=agents/policies/drift_control_policy.baseline.json}"
  : "${GOVERNANCE_CANARY_REPORT_FILE:=agents/reports/governance_canary.json}"
  : "${GOVERNANCE_CANARY_ARCHIVE_ROOTS:=agents/diagnostics}"
  : "${GOVERNANCE_CANARY_MAX_SCENARIOS:=40}"
  : "${GOVERNANCE_CANARY_MIN_OBJECTIVE_SHARE_DELTA:=0.01}"
  : "${GOVERNANCE_CANARY_MIN_OUTCOME_DELTA_PROXY_DELTA:=0.01}"
  : "${GOVERNANCE_CANARY_MAX_BLOCKER_RATE_DELTA:=0}"
  : "${TASKMASTER_COMPLEXITY_PROFILE:=auto}"
  : "${TASKMASTER_MIN_CARDS_PER_SPEC:=1}"
  : "${TASKMASTER_MAX_CARDS_PER_SPEC:=60}"
  : "${TASKMASTER_TARGET_CARDS_PER_SPEC:=10}"
  : "${TASKMASTER_MIN_TOTAL_CARDS:=0}"
  : "${TASKMASTER_TARGET_TOTAL_CARDS:=0}"
  : "${TASKCARD_TARGET_SHORTFALL_MODE:=auto}"
  : "${TASKCARD_FORMAT_STRICT:=on}"
  : "${TASKCARD_ENFORCE_EXECUTION_TEMPLATE:=on}"
  : "${TASKCARD_PHASE_WORKPLAN_COVERAGE:=on}"
  : "${TASKCARD_MAX_PHASE_STEPS_PER_CARD:=2}"
  : "${TASKCARD_SCOPE_LINT:=on}"
  : "${SPEC_DECOMPOSITION_GOVERNANCE:=on}"
  : "${STAGE_RETRY_MAX:=1}"
  : "${STAGE_RETRY_BACKOFF_SECS:=5}"
  : "${RESEARCH_FAILURE_BACKOFF_SECS:=60}"
  : "${NETWORK_GUARD_MODE:=off}"
  : "${RESEARCH_NETWORK_GUARD_POLICY:=deny}"
  : "${RESEARCH_NETWORK_POLICY_EXCEPTION:=off}"
  : "${ENV_PREFLIGHT_MODE:=on}"
  : "${ENV_PREFLIGHT_TRANSPORT_CHECK:=on}"
  : "${NETWORK_OUTAGE_RESILIENCE_MODE:=on}"
  : "${NETWORK_OUTAGE_WAIT_INITIAL_SECS:=15}"
  : "${NETWORK_OUTAGE_WAIT_MAX_SECS:=300}"
  : "${NETWORK_OUTAGE_MAX_PROBES:=0}"
  : "${NETWORK_OUTAGE_PROBE_TIMEOUT_SECS:=5}"
  : "${NETWORK_OUTAGE_PROBE_HOST:=api.openai.com}"
  : "${NETWORK_OUTAGE_PROBE_PORT:=443}"
  : "${NETWORK_OUTAGE_PROBE_CMD:=}"
  : "${NETWORK_OUTAGE_POLICY:=pause_resume}"
  : "${NETWORK_OUTAGE_ROUTE_TO_BLOCKER:=off}"
  : "${NETWORK_OUTAGE_ROUTE_TO_INCIDENT:=off}"

  : "${ARTICULATE_SEARCH:=off}"
  : "${ARTICULATE_TIMEOUT_SECS:=$DEFAULT_STAGE_TIMEOUT_SECS}"
  : "${ANALYZE_SEARCH:=off}"
  : "${ANALYZE_TIMEOUT_SECS:=$DEFAULT_STAGE_TIMEOUT_SECS}"
  : "${CLARIFY_SEARCH:=off}"
  : "${CLARIFY_TIMEOUT_SECS:=$DEFAULT_STAGE_TIMEOUT_SECS}"
  : "${GOAL_INTAKE_SEARCH:=$ARTICULATE_SEARCH}"
  : "${GOAL_INTAKE_TIMEOUT_SECS:=$ARTICULATE_TIMEOUT_SECS}"
  : "${OBJECTIVE_PROFILE_SYNC_SEARCH:=$GOAL_INTAKE_SEARCH}"
  : "${OBJECTIVE_PROFILE_SYNC_TIMEOUT_SECS:=$GOAL_INTAKE_TIMEOUT_SECS}"
  : "${SPEC_SYNTHESIS_SEARCH:=$CLARIFY_SEARCH}"
  : "${SPEC_SYNTHESIS_TIMEOUT_SECS:=$CLARIFY_TIMEOUT_SECS}"
  : "${TASKMASTER_SEARCH:=off}"
  : "${TASKMASTER_TIMEOUT_SECS:=$DEFAULT_STAGE_TIMEOUT_SECS}"
  : "${TASKAUDIT_SEARCH:=off}"
  : "${TASKAUDIT_TIMEOUT_SECS:=$DEFAULT_STAGE_TIMEOUT_SECS}"

  : "${CRITIC_SEARCH:=off}"
  : "${CRITIC_TIMEOUT_SECS:=$DEFAULT_STAGE_TIMEOUT_SECS}"
  : "${DESIGNER_SEARCH:=off}"
  : "${DESIGNER_TIMEOUT_SECS:=$DEFAULT_STAGE_TIMEOUT_SECS}"
  : "${SPEC_REVIEW_SEARCH:=$CRITIC_SEARCH}"
  : "${SPEC_REVIEW_TIMEOUT_SECS:=$CRITIC_TIMEOUT_SECS}"
  : "${PHASESPLIT_SEARCH:=off}"
  : "${PHASESPLIT_TIMEOUT_SECS:=$DEFAULT_STAGE_TIMEOUT_SECS}"
  : "${INCIDENT_INTAKE_SEARCH:=off}"
  : "${INCIDENT_INTAKE_TIMEOUT_SECS:=$DEFAULT_STAGE_TIMEOUT_SECS}"
  : "${INCIDENT_RESOLVE_SEARCH:=off}"
  : "${INCIDENT_RESOLVE_TIMEOUT_SECS:=$DEFAULT_STAGE_TIMEOUT_SECS}"
  : "${INCIDENT_ARCHIVE_SEARCH:=off}"
  : "${INCIDENT_ARCHIVE_TIMEOUT_SECS:=$DEFAULT_STAGE_TIMEOUT_SECS}"
  : "${AUDIT_INTAKE_SEARCH:=off}"
  : "${AUDIT_INTAKE_TIMEOUT_SECS:=$DEFAULT_STAGE_TIMEOUT_SECS}"
  : "${AUDIT_VALIDATE_SEARCH:=off}"
  : "${AUDIT_VALIDATE_TIMEOUT_SECS:=$DEFAULT_STAGE_TIMEOUT_SECS}"
  : "${AUDIT_GATEKEEPER_SEARCH:=off}"
  : "${AUDIT_GATEKEEPER_TIMEOUT_SECS:=$DEFAULT_STAGE_TIMEOUT_SECS}"
  : "${MECHANIC_SEARCH:=off}"
  : "${MECHANIC_TIMEOUT_SECS:=$DEFAULT_STAGE_TIMEOUT_SECS}"

  MAX_AUTONOMY_MODE="$(normalize_choice_ci "$MAX_AUTONOMY_MODE" "On" "Off" "On" "Max")"
  RESEARCH_MODE="$(normalize_mode "$RESEARCH_MODE")"
  SPEC_INTERROGATION_ROUNDS="$(normalize_int_min "$SPEC_INTERROGATION_ROUNDS" 2 0)"
  PHASE_INTERROGATION_ROUNDS="$(normalize_int_min "$PHASE_INTERROGATION_ROUNDS" 1 0)"
  SPEC_QUALITY_THRESHOLD="$(normalize_dec_0_1 "$SPEC_QUALITY_THRESHOLD" 0.75)"
  PHASE_ASSUMPTIONS_BUDGET="$(normalize_int_min "$PHASE_ASSUMPTIONS_BUDGET" 8 1)"
  SPEC_QUALITY_FAIL_MAX="$(normalize_int_min "$SPEC_QUALITY_FAIL_MAX" 2 1)"
  INCIDENT_FIXSPEC_INTERROGATION_ROUNDS="$(normalize_int_min "$INCIDENT_FIXSPEC_INTERROGATION_ROUNDS" 1 0)"
  INCIDENT_MAX_CYCLES="$(normalize_int_min "$INCIDENT_MAX_CYCLES" 3 1)"
  AUDIT_TRIGGER="$(normalize_choice_ci "$AUDIT_TRIGGER" "queue_empty" "always" "queue_empty" "manual")"
  RESEARCH_IDLE_MODE="$(normalize_choice_ci "$RESEARCH_IDLE_MODE" "poll" "poll" "watch")"
  RESEARCH_IDLE_POLL_SECS="$(normalize_int_min "$RESEARCH_IDLE_POLL_SECS" "${RESEARCH_POLL_SECS:-60}" 1)"
  RESEARCH_LOCKING="$(normalize_choice_ci "$RESEARCH_LOCKING" "off" "on" "off")"
  RESEARCH_ALLOW_SEARCH="$(normalize_choice_ci "$RESEARCH_ALLOW_SEARCH" "off" "on" "off")"
  RESEARCH_ALLOW_SEARCH_EXCEPTION="$(normalize_choice_ci "$RESEARCH_ALLOW_SEARCH_EXCEPTION" "off" "on" "off")"
  OBJECTIVE_CONTRACT_SCHEMA_FILE="$(trim "${OBJECTIVE_CONTRACT_SCHEMA_FILE:-agents/objective/contract.schema.json}")"
  if [ -z "$OBJECTIVE_CONTRACT_SCHEMA_FILE" ]; then
    OBJECTIVE_CONTRACT_SCHEMA_FILE="agents/objective/contract.schema.json"
  fi
  OBJECTIVE_CONTRACT_FILE="$(trim "${OBJECTIVE_CONTRACT_FILE:-agents/objective/contract.yaml}")"
  if [ -z "$OBJECTIVE_CONTRACT_FILE" ]; then
    OBJECTIVE_CONTRACT_FILE="agents/objective/contract.yaml"
  fi
  AUDIT_COMPLETION_MANIFEST="$(trim "${AUDIT_COMPLETION_MANIFEST:-agents/audit/completion_manifest.json}")"
  if [ -z "$AUDIT_COMPLETION_MANIFEST" ]; then
    AUDIT_COMPLETION_MANIFEST="agents/audit/completion_manifest.json"
  fi
  AUDIT_STRICT_CONTRACT_FILE="$(trim "${AUDIT_STRICT_CONTRACT_FILE:-agents/audit/strict_contract.json}")"
  if [ -z "$AUDIT_STRICT_CONTRACT_FILE" ]; then
    AUDIT_STRICT_CONTRACT_FILE="agents/audit/strict_contract.json"
  fi
  COMMAND_CONTRACT_REPORT_FILE="$(trim "${COMMAND_CONTRACT_REPORT_FILE:-agents/reports/command_contract.json}")"
  if [ -z "$COMMAND_CONTRACT_REPORT_FILE" ]; then
    COMMAND_CONTRACT_REPORT_FILE="agents/reports/command_contract.json"
  fi
  AUDIT_COMPLETENESS_MODE="$(normalize_choice_ci "$AUDIT_COMPLETENESS_MODE" "comprehensive" "standard" "comprehensive")"
  AUDIT_COMPREHENSIVE_MAX_SKIPS="$(normalize_int_min "$AUDIT_COMPREHENSIVE_MAX_SKIPS" 0 0)"
  DRIFT_CONTROL_MODE="$(normalize_choice_ci "$DRIFT_CONTROL_MODE" "off" "off" "telemetry" "on")"
  DRIFT_CONTROL_POLICY_FILE="$(trim "${DRIFT_CONTROL_POLICY_FILE:-agents/policies/drift_control_policy.json}")"
  if [ -z "$DRIFT_CONTROL_POLICY_FILE" ]; then
    DRIFT_CONTROL_POLICY_FILE="agents/policies/drift_control_policy.json"
  fi
  DRIFT_ENFORCE_ON_AUTONOMY_COMPLETE="$(normalize_choice_ci "$DRIFT_ENFORCE_ON_AUTONOMY_COMPLETE" "on" "on" "off")"
  DRIFT_DETECTOR_MODE="$(normalize_choice_ci "$DRIFT_DETECTOR_MODE" "off" "off" "telemetry" "on")"
  DRIFT_STATUS_REPORT_FILE="$(trim "${DRIFT_STATUS_REPORT_FILE:-agents/reports/drift_status.json}")"
  if [ -z "$DRIFT_STATUS_REPORT_FILE" ]; then
    DRIFT_STATUS_REPORT_FILE="agents/reports/drift_status.json"
  fi
  QUEUE_GOVERNOR_MODE="$(normalize_choice_ci "$QUEUE_GOVERNOR_MODE" "off" "off" "telemetry" "on")"
  QUEUE_GOVERNOR_REPORT_FILE="$(trim "${QUEUE_GOVERNOR_REPORT_FILE:-agents/reports/queue_governor.json}")"
  if [ -z "$QUEUE_GOVERNOR_REPORT_FILE" ]; then
    QUEUE_GOVERNOR_REPORT_FILE="agents/reports/queue_governor.json"
  fi
  GOVERNANCE_CANARY_MODE="$(normalize_choice_ci "$GOVERNANCE_CANARY_MODE" "off" "off" "telemetry" "on")"
  GOVERNANCE_CANARY_BASELINE_POLICY_FILE="$(trim "${GOVERNANCE_CANARY_BASELINE_POLICY_FILE:-agents/policies/drift_control_policy.baseline.json}")"
  if [ -z "$GOVERNANCE_CANARY_BASELINE_POLICY_FILE" ]; then
    GOVERNANCE_CANARY_BASELINE_POLICY_FILE="agents/policies/drift_control_policy.baseline.json"
  fi
  GOVERNANCE_CANARY_REPORT_FILE="$(trim "${GOVERNANCE_CANARY_REPORT_FILE:-agents/reports/governance_canary.json}")"
  if [ -z "$GOVERNANCE_CANARY_REPORT_FILE" ]; then
    GOVERNANCE_CANARY_REPORT_FILE="agents/reports/governance_canary.json"
  fi
  GOVERNANCE_CANARY_ARCHIVE_ROOTS="$(trim "${GOVERNANCE_CANARY_ARCHIVE_ROOTS:-agents/diagnostics}")"
  if [ -z "$GOVERNANCE_CANARY_ARCHIVE_ROOTS" ]; then
    GOVERNANCE_CANARY_ARCHIVE_ROOTS="agents/diagnostics"
  fi
  GOVERNANCE_CANARY_MAX_SCENARIOS="$(normalize_int_min "$GOVERNANCE_CANARY_MAX_SCENARIOS" 40 1)"
  GOVERNANCE_CANARY_MIN_OBJECTIVE_SHARE_DELTA="$(normalize_dec_nonnegative "$GOVERNANCE_CANARY_MIN_OBJECTIVE_SHARE_DELTA" 0.01)"
  GOVERNANCE_CANARY_MIN_OUTCOME_DELTA_PROXY_DELTA="$(normalize_dec_nonnegative "$GOVERNANCE_CANARY_MIN_OUTCOME_DELTA_PROXY_DELTA" 0.01)"
  GOVERNANCE_CANARY_MAX_BLOCKER_RATE_DELTA="$(normalize_dec_0_1 "$GOVERNANCE_CANARY_MAX_BLOCKER_RATE_DELTA" 0)"
  TASKMASTER_COMPLEXITY_PROFILE="$(normalize_taskmaster_complexity_profile "$TASKMASTER_COMPLEXITY_PROFILE")"
  local taskmaster_profile_resolution taskmaster_profile_remainder
  taskmaster_profile_resolution="$(resolve_taskmaster_complexity_profile "$TASKMASTER_COMPLEXITY_PROFILE")"
  TASKMASTER_COMPLEXITY_PROFILE_RESOLVED="${taskmaster_profile_resolution%%|*}"
  taskmaster_profile_remainder="${taskmaster_profile_resolution#*|}"
  TASKMASTER_COMPLEXITY_PROFILE_SOURCE="${taskmaster_profile_remainder%%|*}"
  TASKMASTER_COMPLEXITY_PROFILE_EVIDENCE="${taskmaster_profile_remainder#*|}"
  apply_taskmaster_complexity_profile "$TASKMASTER_COMPLEXITY_PROFILE_RESOLVED"
  TASKMASTER_MIN_CARDS_PER_SPEC="$(normalize_int_min "$TASKMASTER_MIN_CARDS_PER_SPEC" 1 1)"
  TASKMASTER_MAX_CARDS_PER_SPEC="$(normalize_int_min "$TASKMASTER_MAX_CARDS_PER_SPEC" 60 0)"
  TASKMASTER_TARGET_CARDS_PER_SPEC="$(normalize_int_min "$TASKMASTER_TARGET_CARDS_PER_SPEC" 0 0)"
  TASKMASTER_MIN_TOTAL_CARDS="$(normalize_int_min "$TASKMASTER_MIN_TOTAL_CARDS" 0 0)"
  TASKMASTER_TARGET_TOTAL_CARDS="$(normalize_int_min "$TASKMASTER_TARGET_TOTAL_CARDS" 0 0)"
  TASKCARD_TARGET_SHORTFALL_MODE="$(normalize_choice_ci "$TASKCARD_TARGET_SHORTFALL_MODE" "auto" "warn" "error" "auto")"
  TASKCARD_FORMAT_STRICT="$(normalize_choice_ci "$TASKCARD_FORMAT_STRICT" "on" "on" "off")"
  TASKCARD_ENFORCE_EXECUTION_TEMPLATE="$(normalize_choice_ci "$TASKCARD_ENFORCE_EXECUTION_TEMPLATE" "on" "on" "off")"
  TASKCARD_PHASE_WORKPLAN_COVERAGE="$(normalize_choice_ci "$TASKCARD_PHASE_WORKPLAN_COVERAGE" "on" "on" "off")"
  TASKCARD_MAX_PHASE_STEPS_PER_CARD="$(normalize_int_min "$TASKCARD_MAX_PHASE_STEPS_PER_CARD" 2 0)"
  TASKCARD_SCOPE_LINT="$(normalize_choice_ci "$TASKCARD_SCOPE_LINT" "on" "on" "off")"
  SPEC_DECOMPOSITION_GOVERNANCE="$(normalize_choice_ci "$SPEC_DECOMPOSITION_GOVERNANCE" "on" "on" "off")"
  if [ "$TASKMASTER_MAX_CARDS_PER_SPEC" -gt 0 ] && [ "$TASKMASTER_MAX_CARDS_PER_SPEC" -lt "$TASKMASTER_MIN_CARDS_PER_SPEC" ]; then
    TASKMASTER_MAX_CARDS_PER_SPEC="$TASKMASTER_MIN_CARDS_PER_SPEC"
  fi
  if [ "$TASKMASTER_TARGET_CARDS_PER_SPEC" -gt 0 ] && [ "$TASKMASTER_TARGET_CARDS_PER_SPEC" -lt "$TASKMASTER_MIN_CARDS_PER_SPEC" ]; then
    TASKMASTER_TARGET_CARDS_PER_SPEC="$TASKMASTER_MIN_CARDS_PER_SPEC"
  fi
  if [ "$TASKMASTER_MAX_CARDS_PER_SPEC" -gt 0 ] && [ "$TASKMASTER_TARGET_CARDS_PER_SPEC" -gt "$TASKMASTER_MAX_CARDS_PER_SPEC" ]; then
    TASKMASTER_TARGET_CARDS_PER_SPEC="$TASKMASTER_MAX_CARDS_PER_SPEC"
  fi
  if [ "$TASKMASTER_TARGET_TOTAL_CARDS" -gt 0 ] && [ "$TASKMASTER_TARGET_TOTAL_CARDS" -lt "$TASKMASTER_MIN_TOTAL_CARDS" ]; then
    TASKMASTER_TARGET_TOTAL_CARDS="$TASKMASTER_MIN_TOTAL_CARDS"
  fi
  STAGE_RETRY_MAX="$(normalize_int_min "$STAGE_RETRY_MAX" 1 0)"
  STAGE_RETRY_BACKOFF_SECS="$(normalize_int_min "$STAGE_RETRY_BACKOFF_SECS" 5 0)"
  RESEARCH_FAILURE_BACKOFF_SECS="$(normalize_int_min "$RESEARCH_FAILURE_BACKOFF_SECS" 60 1)"
  ENV_PREFLIGHT_MODE="$(normalize_choice_ci "$ENV_PREFLIGHT_MODE" "on" "on" "off")"
  ENV_PREFLIGHT_TRANSPORT_CHECK="$(normalize_choice_ci "$ENV_PREFLIGHT_TRANSPORT_CHECK" "on" "on" "off")"
  NETWORK_GUARD_MODE="$(normalize_choice_ci "$NETWORK_GUARD_MODE" "off" "on" "off")"
  RESEARCH_NETWORK_GUARD_POLICY="$(normalize_choice_ci "$RESEARCH_NETWORK_GUARD_POLICY" "deny" "allow" "deny")"
  RESEARCH_NETWORK_POLICY_EXCEPTION="$(normalize_choice_ci "$RESEARCH_NETWORK_POLICY_EXCEPTION" "off" "on" "off")"
  NETWORK_OUTAGE_RESILIENCE_MODE="$(normalize_choice_ci "$NETWORK_OUTAGE_RESILIENCE_MODE" "on" "on" "off")"
  NETWORK_OUTAGE_WAIT_INITIAL_SECS="$(normalize_int_min "$NETWORK_OUTAGE_WAIT_INITIAL_SECS" 15 1)"
  NETWORK_OUTAGE_WAIT_MAX_SECS="$(normalize_int_min "$NETWORK_OUTAGE_WAIT_MAX_SECS" 300 1)"
  NETWORK_OUTAGE_MAX_PROBES="$(normalize_int_min "$NETWORK_OUTAGE_MAX_PROBES" 0 0)"
  NETWORK_OUTAGE_PROBE_TIMEOUT_SECS="$(normalize_int_min "$NETWORK_OUTAGE_PROBE_TIMEOUT_SECS" 5 1)"
  NETWORK_OUTAGE_PROBE_PORT="$(normalize_int_min "$NETWORK_OUTAGE_PROBE_PORT" 443 1)"
  NETWORK_OUTAGE_POLICY="$(normalize_choice_ci "$NETWORK_OUTAGE_POLICY" "pause_resume" "pause_resume" "incident" "blocker")"
  NETWORK_OUTAGE_ROUTE_TO_BLOCKER="$(normalize_choice_ci "$NETWORK_OUTAGE_ROUTE_TO_BLOCKER" "off" "on" "off")"
  NETWORK_OUTAGE_ROUTE_TO_INCIDENT="$(normalize_choice_ci "$NETWORK_OUTAGE_ROUTE_TO_INCIDENT" "off" "on" "off")"

  ARTICULATE_SEARCH="$(normalize_bool "$ARTICULATE_SEARCH" false)"
  ANALYZE_SEARCH="$(normalize_bool "$ANALYZE_SEARCH" false)"
  CLARIFY_SEARCH="$(normalize_bool "$CLARIFY_SEARCH" false)"
  GOAL_INTAKE_SEARCH="$(normalize_bool "$GOAL_INTAKE_SEARCH" false)"
  OBJECTIVE_PROFILE_SYNC_SEARCH="$(normalize_bool "$OBJECTIVE_PROFILE_SYNC_SEARCH" false)"
  SPEC_SYNTHESIS_SEARCH="$(normalize_bool "$SPEC_SYNTHESIS_SEARCH" false)"
  SPEC_REVIEW_SEARCH="$(normalize_bool "$SPEC_REVIEW_SEARCH" false)"
  TASKMASTER_SEARCH="$(normalize_bool "$TASKMASTER_SEARCH" false)"
  TASKAUDIT_SEARCH="$(normalize_bool "$TASKAUDIT_SEARCH" false)"
  CRITIC_SEARCH="$(normalize_bool "$CRITIC_SEARCH" false)"
  DESIGNER_SEARCH="$(normalize_bool "$DESIGNER_SEARCH" false)"
  PHASESPLIT_SEARCH="$(normalize_bool "$PHASESPLIT_SEARCH" false)"
  INCIDENT_INTAKE_SEARCH="$(normalize_bool "$INCIDENT_INTAKE_SEARCH" false)"
  INCIDENT_RESOLVE_SEARCH="$(normalize_bool "$INCIDENT_RESOLVE_SEARCH" false)"
  INCIDENT_ARCHIVE_SEARCH="$(normalize_bool "$INCIDENT_ARCHIVE_SEARCH" false)"
  AUDIT_INTAKE_SEARCH="$(normalize_bool "$AUDIT_INTAKE_SEARCH" false)"
  AUDIT_VALIDATE_SEARCH="$(normalize_bool "$AUDIT_VALIDATE_SEARCH" false)"
  AUDIT_GATEKEEPER_SEARCH="$(normalize_bool "$AUDIT_GATEKEEPER_SEARCH" false)"
  MECHANIC_SEARCH="$(normalize_bool "$MECHANIC_SEARCH" false)"

  ARTICULATE_TIMEOUT_SECS="$(normalize_int_min "$ARTICULATE_TIMEOUT_SECS" "$DEFAULT_STAGE_TIMEOUT_SECS" 1)"
  ANALYZE_TIMEOUT_SECS="$(normalize_int_min "$ANALYZE_TIMEOUT_SECS" "$DEFAULT_STAGE_TIMEOUT_SECS" 1)"
  CLARIFY_TIMEOUT_SECS="$(normalize_int_min "$CLARIFY_TIMEOUT_SECS" "$DEFAULT_STAGE_TIMEOUT_SECS" 1)"
  GOAL_INTAKE_TIMEOUT_SECS="$(normalize_int_min "$GOAL_INTAKE_TIMEOUT_SECS" "$DEFAULT_STAGE_TIMEOUT_SECS" 1)"
  OBJECTIVE_PROFILE_SYNC_TIMEOUT_SECS="$(normalize_int_min "$OBJECTIVE_PROFILE_SYNC_TIMEOUT_SECS" "$DEFAULT_STAGE_TIMEOUT_SECS" 1)"
  SPEC_SYNTHESIS_TIMEOUT_SECS="$(normalize_int_min "$SPEC_SYNTHESIS_TIMEOUT_SECS" "$DEFAULT_STAGE_TIMEOUT_SECS" 1)"
  SPEC_REVIEW_TIMEOUT_SECS="$(normalize_int_min "$SPEC_REVIEW_TIMEOUT_SECS" "$DEFAULT_STAGE_TIMEOUT_SECS" 1)"
  TASKMASTER_TIMEOUT_SECS="$(normalize_int_min "$TASKMASTER_TIMEOUT_SECS" "$DEFAULT_STAGE_TIMEOUT_SECS" 1)"
  TASKAUDIT_TIMEOUT_SECS="$(normalize_int_min "$TASKAUDIT_TIMEOUT_SECS" "$DEFAULT_STAGE_TIMEOUT_SECS" 1)"
  CRITIC_TIMEOUT_SECS="$(normalize_int_min "$CRITIC_TIMEOUT_SECS" "$DEFAULT_STAGE_TIMEOUT_SECS" 1)"
  DESIGNER_TIMEOUT_SECS="$(normalize_int_min "$DESIGNER_TIMEOUT_SECS" "$DEFAULT_STAGE_TIMEOUT_SECS" 1)"
  PHASESPLIT_TIMEOUT_SECS="$(normalize_int_min "$PHASESPLIT_TIMEOUT_SECS" "$DEFAULT_STAGE_TIMEOUT_SECS" 1)"
  INCIDENT_INTAKE_TIMEOUT_SECS="$(normalize_int_min "$INCIDENT_INTAKE_TIMEOUT_SECS" "$DEFAULT_STAGE_TIMEOUT_SECS" 1)"
  INCIDENT_RESOLVE_TIMEOUT_SECS="$(normalize_int_min "$INCIDENT_RESOLVE_TIMEOUT_SECS" "$DEFAULT_STAGE_TIMEOUT_SECS" 1)"
  INCIDENT_ARCHIVE_TIMEOUT_SECS="$(normalize_int_min "$INCIDENT_ARCHIVE_TIMEOUT_SECS" "$DEFAULT_STAGE_TIMEOUT_SECS" 1)"
  AUDIT_INTAKE_TIMEOUT_SECS="$(normalize_int_min "$AUDIT_INTAKE_TIMEOUT_SECS" "$DEFAULT_STAGE_TIMEOUT_SECS" 1)"
  AUDIT_VALIDATE_TIMEOUT_SECS="$(normalize_int_min "$AUDIT_VALIDATE_TIMEOUT_SECS" "$DEFAULT_STAGE_TIMEOUT_SECS" 1)"
  AUDIT_GATEKEEPER_TIMEOUT_SECS="$(normalize_int_min "$AUDIT_GATEKEEPER_TIMEOUT_SECS" "$DEFAULT_STAGE_TIMEOUT_SECS" 1)"
  MECHANIC_TIMEOUT_SECS="$(normalize_int_min "$MECHANIC_TIMEOUT_SECS" "$DEFAULT_STAGE_TIMEOUT_SECS" 1)"
}

set_permission_flags() {
  case "${HEADLESS_PERMISSIONS:-Maximum}" in
    Normal)
      CODEX_PERM_FLAGS=(--full-auto)
      CLAUDE_PERM_FLAGS=()
      ;;
    Elevated)
      CODEX_PERM_FLAGS=(--full-auto --sandbox danger-full-access)
      CLAUDE_PERM_FLAGS=(--permission-mode acceptEdits)
      ;;
    Maximum)
      CODEX_PERM_FLAGS=(--dangerously-bypass-approvals-and-sandbox)
      CLAUDE_PERM_FLAGS=(--dangerously-skip-permissions)
      ;;
    *)
      echo "Unknown HEADLESS_PERMISSIONS: ${HEADLESS_PERMISSIONS}" >&2
      exit "$EXIT_CONFIG_ERROR"
      ;;
  esac
}

run_command_with_heartbeat() {
  local label="$1"
  shift
  local interval="${HEARTBEAT_SECS:-60}"

  if ! [[ "$interval" =~ ^[0-9]+$ ]] || [ "$interval" -le 0 ]; then
    interval=60
  fi

  "$@" &
  local cmd_pid=$!
  local started_at now last_beat elapsed
  started_at="$(date +%s)"
  last_beat="$started_at"

  while kill -0 "$cmd_pid" 2>/dev/null; do
    sleep 1
    now="$(date +%s)"
    if [ $(( now - last_beat )) -ge "$interval" ]; then
      elapsed=$(( now - started_at ))
      log "$label: heartbeat elapsed=${elapsed}s"
      last_beat="$now"
    fi
  done

  wait "$cmd_pid"
}

run_with_python_timeout() {
  local seconds="$1"
  shift

  python3 - "$seconds" "$@" <<'PY'
import os
import signal
import subprocess
import sys

if len(sys.argv) < 3:
    print("python timeout wrapper: missing command", file=sys.stderr)
    raise SystemExit(2)

try:
    timeout_secs = int(float(sys.argv[1]))
except Exception:
    print(f"python timeout wrapper: invalid timeout: {sys.argv[1]!r}", file=sys.stderr)
    raise SystemExit(2)

if timeout_secs <= 0:
    timeout_secs = 1

command = sys.argv[2:]
proc = subprocess.Popen(command, start_new_session=True)
try:
    code = proc.wait(timeout=timeout_secs)
except subprocess.TimeoutExpired:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        proc.wait()
    raise SystemExit(124)

if code < 0:
    code = 128 + abs(code)

raise SystemExit(code)
PY
}

run_with_optional_timeout() {
  local seconds="$1"
  shift
  if [ -n "$TIMEOUT_BIN" ]; then
    run_command_with_heartbeat "$RUN_HEARTBEAT_LABEL" "$TIMEOUT_BIN" "$seconds" "$@"
  else
    # Shell functions cannot be executed via the python timeout wrapper.
    if declare -F "${1:-}" >/dev/null 2>&1; then
      run_command_with_heartbeat "$RUN_HEARTBEAT_LABEL" "$@"
    else
      run_command_with_heartbeat "$RUN_HEARTBEAT_LABEL" run_with_python_timeout "$seconds" "$@"
    fi
  fi
}

get_openclaw_token() {
  if [ -n "${OPENCLAW_GATEWAY_TOKEN:-}" ]; then
    printf '%s' "$OPENCLAW_GATEWAY_TOKEN"
    return 0
  fi
  if command -v openclaw >/dev/null 2>&1; then
    openclaw config get gateway.auth.token 2>/dev/null | tr -d '\r\n'
    return 0
  fi
  if command -v openclaw.exe >/dev/null 2>&1; then
    openclaw.exe config get gateway.auth.token 2>/dev/null | tr -d '\r\n'
    return 0
  fi
  return 1
}

openclaw_run() {
  local model="$1"
  local prompt="$2"
  local stdout_path="$3"
  local stderr_path="$4"
  local last_path="$5"

  local token
  token="$(get_openclaw_token)" || {
    echo "OpenClaw runner requested but no OPENCLAW_GATEWAY_TOKEN and openclaw/openclaw.exe not available" >&2
    return 1
  }

  local payload
  payload="$(python3 - "$model" "$prompt" "$OPENCLAW_AGENT_ID" <<'PY'
import json
import sys
print(json.dumps({
  "model": sys.argv[1],
  "input": [{"role": "user", "content": [{"type": "text", "text": sys.argv[2]}]}],
  "metadata": {"openclaw_agent_id": sys.argv[3]},
}))
PY
)"

  curl -sS -X POST "$OPENCLAW_GATEWAY_URL/v1/responses" \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d "$payload" \
    >"$stdout_path" 2>"$stderr_path"

  if [ -n "$last_path" ]; then
    python3 - "$stdout_path" "$last_path" <<'PY'
import json
import sys
src, dst = sys.argv[1], sys.argv[2]
try:
  data = json.load(open(src, 'r', encoding='utf-8'))
except Exception:
  raise SystemExit(0)
text = []
for item in data.get('output') or []:
  for part in item.get('content') or []:
    if part.get('type') == 'output_text' and isinstance(part.get('text'), str):
      text.append(part['text'])
if text:
  open(dst, 'w', encoding='utf-8').write("\n".join(text).strip() + "\n")
PY
  fi
}

run_cycle() {
  ensure_repo_root

  local runner="$1"
  local model="$2"
  local prompt="$3"
  local stdout_path="$4"
  local stderr_path="$5"
  local last_path="$6"
  local search="${7:-false}"
  local effort="${8:-high}"
  local timeout_secs="${9:-$DEFAULT_STAGE_TIMEOUT_SECS}"
  local label="${10:-Stage}"

  local code=0
  local search_flags=()
  local codex_cmd=()
  local claude_cmd=()
  if [ "$search" = "true" ]; then
    search_flags=(--search)
  fi

  local reasoning_flags=(-c "model_reasoning_effort=\"$effort\"")

  log "$label: runner=$runner model=$model effort=$effort search=$search timeout=${timeout_secs}s"
  RUN_HEARTBEAT_LABEL="$label"

  case "$runner" in
    codex)
      codex_cmd=(codex)
      local codex_home=""
      local codex_exec_prefix=()
      codex_home="$(prepare_codex_runtime_home 2>/dev/null || true)"
      if [ -n "$codex_home" ]; then
        codex_exec_prefix=(env "HOME=$codex_home")
      fi
      if [ "${#search_flags[@]}" -gt 0 ]; then
        codex_cmd+=("${search_flags[@]}")
      fi
      codex_cmd+=(exec --json --skip-git-repo-check --model "$model")
      if [ "${#CODEX_PERM_FLAGS[@]}" -gt 0 ]; then
        codex_cmd+=("${CODEX_PERM_FLAGS[@]}")
      fi
      codex_cmd+=("${reasoning_flags[@]}")
      if [ -n "$last_path" ]; then
        run_with_optional_timeout "$timeout_secs" "${codex_exec_prefix[@]}" "${codex_cmd[@]}" -o "$last_path" "$prompt" \
          >"$stdout_path" 2>"$stderr_path" || code=$?
      else
        run_with_optional_timeout "$timeout_secs" "${codex_exec_prefix[@]}" "${codex_cmd[@]}" "$prompt" \
          >"$stdout_path" 2>"$stderr_path" || code=$?
      fi
      emit_codex_exec_usage_summary "$stdout_path" "$label" "$model" "research"
      ;;
    claude)
      claude_cmd=(claude -p "$prompt" --model "$model" --output-format text)
      if [ "${#CLAUDE_PERM_FLAGS[@]}" -gt 0 ]; then
        claude_cmd+=("${CLAUDE_PERM_FLAGS[@]}")
      fi
      run_with_optional_timeout "$timeout_secs" "${claude_cmd[@]}" \
        >"$stdout_path" 2>"$stderr_path" || code=$?
      if [ -n "$last_path" ] && [ -f "$stdout_path" ]; then
        cp -f "$stdout_path" "$last_path" || true
      fi
      ;;
    openclaw)
      run_with_optional_timeout "$timeout_secs" openclaw_run "$model" "$prompt" "$stdout_path" "$stderr_path" "$last_path" || code=$?
      ;;
    *)
      echo "Unknown runner: $runner (expected codex|claude|openclaw). Check $MODEL_CFG" >&2
      return 1
      ;;
  esac

  if [ "$code" -ne 0 ]; then
    log "$label: exit=$code"
    return "$code"
  fi
  log "$label: exit=0"
  return 0
}

run_cycle_with_fallback() {
  local runner="$1"
  local model_chain="$2"
  local effort_chain="$3"
  local prompt="$4"
  local stdout_path="$5"
  local stderr_path="$6"
  local last_path="$7"
  local search="${8:-false}"
  local default_effort="${9:-high}"
  local timeout_secs="${10:-$DEFAULT_STAGE_TIMEOUT_SECS}"
  local label="${11:-Stage}"

  local chain efforts_chain
  chain="$(trim "$model_chain")"
  efforts_chain="$(trim "$effort_chain")"
  [ -n "$chain" ] || return 1
  [ -n "$efforts_chain" ] || efforts_chain="$default_effort"

  local -a models=()
  local -a efforts=()
  IFS='|' read -r -a models <<< "$chain"
  IFS='|' read -r -a efforts <<< "$efforts_chain"

  local idx model effort exit_code=1 ran=0
  for idx in "${!models[@]}"; do
    model="$(trim "${models[$idx]}")"
    [ -n "$model" ] || continue

    effort="$default_effort"
    if [ "$idx" -lt "${#efforts[@]}" ]; then
      effort="$(trim "${efforts[$idx]}")"
    fi
    [ -n "$effort" ] || effort="$default_effort"

    ran=1
    if run_cycle "$runner" "$model" "$prompt" "$stdout_path" "$stderr_path" "$last_path" "$search" "$effort" "$timeout_secs" "$label"; then
      return 0
    fi
    exit_code=$?

    if [ "$idx" -lt $(( ${#models[@]} - 1 )) ]; then
      log "$label: fallback to next model after failure on $model (exit=$exit_code)"
    fi
  done

  if [ "$ran" -eq 0 ]; then
    return 1
  fi
  return "$exit_code"
}

stage_runner_for() {
  local stage="$1"
  case "$stage" in
    goal_intake) printf '%s\n' "$GOAL_INTAKE_RUNNER" ;;
    objective_profile_sync) printf '%s\n' "$OBJECTIVE_PROFILE_SYNC_RUNNER" ;;
    spec_synthesis) printf '%s\n' "$SPEC_SYNTHESIS_RUNNER" ;;
    spec_review) printf '%s\n' "$SPEC_REVIEW_RUNNER" ;;
    articulate) printf '%s\n' "$ARTICULATE_RUNNER" ;;
    analyze) printf '%s\n' "$ANALYZE_RUNNER" ;;
    clarify) printf '%s\n' "$CLARIFY_RUNNER" ;;
    taskmaster) printf '%s\n' "$TASKMASTER_RUNNER" ;;
    taskaudit) printf '%s\n' "$TASKAUDIT_RUNNER" ;;
    critic) printf '%s\n' "$CRITIC_RUNNER" ;;
    designer) printf '%s\n' "$DESIGNER_RUNNER" ;;
    phasesplit) printf '%s\n' "$PHASESPLIT_RUNNER" ;;
    incident_intake) printf '%s\n' "$INCIDENT_INTAKE_RUNNER" ;;
    incident_resolve) printf '%s\n' "$INCIDENT_RESOLVE_RUNNER" ;;
    incident_archive) printf '%s\n' "$INCIDENT_ARCHIVE_RUNNER" ;;
    audit_intake) printf '%s\n' "$AUDIT_INTAKE_RUNNER" ;;
    audit_validate) printf '%s\n' "$AUDIT_VALIDATE_RUNNER" ;;
    audit_gatekeeper) printf '%s\n' "$AUDIT_GATEKEEPER_RUNNER" ;;
    mechanic) printf '%s\n' "$MECHANIC_RUNNER" ;;
    *) return 1 ;;
  esac
}

stage_model_for() {
  local stage="$1"
  case "$stage" in
    goal_intake) printf '%s\n' "$GOAL_INTAKE_MODEL" ;;
    objective_profile_sync) printf '%s\n' "$OBJECTIVE_PROFILE_SYNC_MODEL" ;;
    spec_synthesis) printf '%s\n' "$SPEC_SYNTHESIS_MODEL" ;;
    spec_review) printf '%s\n' "$SPEC_REVIEW_MODEL" ;;
    articulate) printf '%s\n' "$ARTICULATE_MODEL" ;;
    analyze) printf '%s\n' "$ANALYZE_MODEL" ;;
    clarify) printf '%s\n' "$CLARIFY_MODEL" ;;
    taskmaster) printf '%s\n' "$TASKMASTER_MODEL" ;;
    taskaudit) printf '%s\n' "$TASKAUDIT_MODEL" ;;
    critic) printf '%s\n' "$CRITIC_MODEL" ;;
    designer) printf '%s\n' "$DESIGNER_MODEL" ;;
    phasesplit) printf '%s\n' "$PHASESPLIT_MODEL" ;;
    incident_intake) printf '%s\n' "$INCIDENT_INTAKE_MODEL" ;;
    incident_resolve) printf '%s\n' "$INCIDENT_RESOLVE_MODEL" ;;
    incident_archive) printf '%s\n' "$INCIDENT_ARCHIVE_MODEL" ;;
    audit_intake) printf '%s\n' "$AUDIT_INTAKE_MODEL" ;;
    audit_validate) printf '%s\n' "$AUDIT_VALIDATE_MODEL" ;;
    audit_gatekeeper) printf '%s\n' "$AUDIT_GATEKEEPER_MODEL" ;;
    mechanic) printf '%s\n' "$MECHANIC_MODEL" ;;
    *) return 1 ;;
  esac
}

stage_effort_for() {
  local stage="$1"
  case "$stage" in
    goal_intake) printf '%s\n' "$GOAL_INTAKE_EFFORT" ;;
    objective_profile_sync) printf '%s\n' "$OBJECTIVE_PROFILE_SYNC_EFFORT" ;;
    spec_synthesis) printf '%s\n' "$SPEC_SYNTHESIS_EFFORT" ;;
    spec_review) printf '%s\n' "$SPEC_REVIEW_EFFORT" ;;
    articulate) printf '%s\n' "$ARTICULATE_EFFORT" ;;
    analyze) printf '%s\n' "$ANALYZE_EFFORT" ;;
    clarify) printf '%s\n' "$CLARIFY_EFFORT" ;;
    taskmaster) printf '%s\n' "$TASKMASTER_EFFORT" ;;
    taskaudit) printf '%s\n' "$TASKAUDIT_EFFORT" ;;
    critic) printf '%s\n' "$CRITIC_EFFORT" ;;
    designer) printf '%s\n' "$DESIGNER_EFFORT" ;;
    phasesplit) printf '%s\n' "$PHASESPLIT_EFFORT" ;;
    incident_intake) printf '%s\n' "$INCIDENT_INTAKE_EFFORT" ;;
    incident_resolve) printf '%s\n' "$INCIDENT_RESOLVE_EFFORT" ;;
    incident_archive) printf '%s\n' "$INCIDENT_ARCHIVE_EFFORT" ;;
    audit_intake) printf '%s\n' "$AUDIT_INTAKE_EFFORT" ;;
    audit_validate) printf '%s\n' "$AUDIT_VALIDATE_EFFORT" ;;
    audit_gatekeeper) printf '%s\n' "$AUDIT_GATEKEEPER_EFFORT" ;;
    mechanic) printf '%s\n' "$MECHANIC_EFFORT" ;;
    *) return 1 ;;
  esac
}

stage_search_for() {
  local stage="$1"
  if [ "${RESEARCH_ALLOW_SEARCH:-off}" != "on" ]; then
    printf 'false\n'
    return 0
  fi

  case "$stage" in
    goal_intake) printf '%s\n' "$GOAL_INTAKE_SEARCH" ;;
    objective_profile_sync) printf '%s\n' "$OBJECTIVE_PROFILE_SYNC_SEARCH" ;;
    spec_synthesis) printf '%s\n' "$SPEC_SYNTHESIS_SEARCH" ;;
    spec_review) printf '%s\n' "$SPEC_REVIEW_SEARCH" ;;
    articulate) printf '%s\n' "$ARTICULATE_SEARCH" ;;
    analyze) printf '%s\n' "$ANALYZE_SEARCH" ;;
    clarify) printf '%s\n' "$CLARIFY_SEARCH" ;;
    taskmaster) printf '%s\n' "$TASKMASTER_SEARCH" ;;
    taskaudit) printf '%s\n' "$TASKAUDIT_SEARCH" ;;
    critic) printf '%s\n' "$CRITIC_SEARCH" ;;
    designer) printf '%s\n' "$DESIGNER_SEARCH" ;;
    phasesplit) printf '%s\n' "$PHASESPLIT_SEARCH" ;;
    incident_intake) printf '%s\n' "$INCIDENT_INTAKE_SEARCH" ;;
    incident_resolve) printf '%s\n' "$INCIDENT_RESOLVE_SEARCH" ;;
    incident_archive) printf '%s\n' "$INCIDENT_ARCHIVE_SEARCH" ;;
    audit_intake) printf '%s\n' "$AUDIT_INTAKE_SEARCH" ;;
    audit_validate) printf '%s\n' "$AUDIT_VALIDATE_SEARCH" ;;
    audit_gatekeeper) printf '%s\n' "$AUDIT_GATEKEEPER_SEARCH" ;;
    mechanic) printf '%s\n' "$MECHANIC_SEARCH" ;;
    *) printf 'false\n' ;;
  esac
}

stage_timeout_for() {
  local stage="$1"
  case "$stage" in
    goal_intake) printf '%s\n' "$GOAL_INTAKE_TIMEOUT_SECS" ;;
    objective_profile_sync) printf '%s\n' "$OBJECTIVE_PROFILE_SYNC_TIMEOUT_SECS" ;;
    spec_synthesis) printf '%s\n' "$SPEC_SYNTHESIS_TIMEOUT_SECS" ;;
    spec_review) printf '%s\n' "$SPEC_REVIEW_TIMEOUT_SECS" ;;
    articulate) printf '%s\n' "$ARTICULATE_TIMEOUT_SECS" ;;
    analyze) printf '%s\n' "$ANALYZE_TIMEOUT_SECS" ;;
    clarify) printf '%s\n' "$CLARIFY_TIMEOUT_SECS" ;;
    taskmaster) printf '%s\n' "$TASKMASTER_TIMEOUT_SECS" ;;
    taskaudit) printf '%s\n' "$TASKAUDIT_TIMEOUT_SECS" ;;
    critic) printf '%s\n' "$CRITIC_TIMEOUT_SECS" ;;
    designer) printf '%s\n' "$DESIGNER_TIMEOUT_SECS" ;;
    phasesplit) printf '%s\n' "$PHASESPLIT_TIMEOUT_SECS" ;;
    incident_intake) printf '%s\n' "$INCIDENT_INTAKE_TIMEOUT_SECS" ;;
    incident_resolve) printf '%s\n' "$INCIDENT_RESOLVE_TIMEOUT_SECS" ;;
    incident_archive) printf '%s\n' "$INCIDENT_ARCHIVE_TIMEOUT_SECS" ;;
    audit_intake) printf '%s\n' "$AUDIT_INTAKE_TIMEOUT_SECS" ;;
    audit_validate) printf '%s\n' "$AUDIT_VALIDATE_TIMEOUT_SECS" ;;
    audit_gatekeeper) printf '%s\n' "$AUDIT_GATEKEEPER_TIMEOUT_SECS" ;;
    mechanic) printf '%s\n' "$MECHANIC_TIMEOUT_SECS" ;;
    *) printf '%s\n' "$DEFAULT_STAGE_TIMEOUT_SECS" ;;
  esac
}

stage_prompt_for() {
  local stage="$1"
  case "$stage" in
    goal_intake) printf '%s\n' "Open agents/_goal_intake.md and follow instructions." ;;
    objective_profile_sync) printf '%s\n' "Open agents/_objective_profile_sync.md and follow instructions." ;;
    spec_synthesis) printf '%s\n' "Open agents/_spec_synthesis.md and follow instructions." ;;
    spec_review) printf '%s\n' "Open agents/_spec_review.md and follow instructions." ;;
    articulate) printf '%s\n' "Open agents/_articulate.md and follow instructions." ;;
    analyze) printf '%s\n' "Open agents/_analyze.md and follow instructions." ;;
    clarify) printf '%s\n' "Open agents/_clarify.md and follow instructions." ;;
    taskmaster) printf '%s\n' "Open agents/_taskmaster.md and follow instructions." ;;
    taskaudit) printf '%s\n' "Open agents/_taskaudit.md and follow instructions." ;;
    critic) printf '%s\n' "Open agents/_critic.md and follow instructions." ;;
    designer) printf '%s\n' "Open agents/_designer.md and follow instructions." ;;
    incident_intake) printf '%s\n' "Open agents/_incident_intake.md and follow instructions." ;;
    incident_resolve) printf '%s\n' "Open agents/_incident_resolve.md and follow instructions." ;;
    incident_archive) printf '%s\n' "Open agents/_incident_archive.md and follow instructions." ;;
    audit_intake) printf '%s\n' "Open agents/_audit_intake.md and follow instructions." ;;
    audit_validate) printf '%s\n' "Open agents/_audit_validate.md and follow instructions." ;;
    audit_gatekeeper) printf '%s\n' "Open agents/_audit_gatekeeper.md and follow instructions." ;;
    mechanic) printf '%s\n' "Open agents/_mechanic.md and follow instructions." ;;
    *) return 1 ;;
  esac
}

slugify_token() {
  local raw="${1:-}"
  local slug
  slug="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-')"
  slug="${slug##-}"
  slug="${slug%%-}"
  if [ -z "$slug" ]; then
    slug="unknown"
  fi
  printf '%s\n' "$slug"
}

classify_research_stage_failure() {
  local stage="$1"
  local stderr_path="$2"
  local stdout_path="$3"
  local exit_code="$4"
  local stage_slug classifier_path classification failure_class primary fingerprint parsed

  stage_slug="$(slugify_token "$stage")"
  classifier_path="$TMP_DIR/research_failure_classification_${stage_slug}.json"
  classification="non_network"
  failure_class="OBJECTIVE_FAIL"
  primary="none"
  fingerprint=""

  if [ -f "$STAGE_FAILURE_CLASSIFIER_TOOL" ]; then
    if python3 "$STAGE_FAILURE_CLASSIFIER_TOOL" \
      --stage "$stage" \
      --runner "research_loop" \
      --exit-code "$exit_code" \
      --stderr-file "$stderr_path" \
      --stdout-file "$stdout_path" \
      --json-out "$classifier_path" \
      > /dev/null 2>&1; then
      parsed="$(
        python3 - "$classifier_path" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as fh:
    payload = json.load(fh)

print(payload.get("classification", "non_network"))
print(payload.get("failure_class", "OBJECTIVE_FAIL"))
print(payload.get("primary_signature", "none"))
print(payload.get("fingerprint", ""))
PY
      )"
      classification="$(printf '%s\n' "$parsed" | sed -n '1p')"
      failure_class="$(printf '%s\n' "$parsed" | sed -n '2p')"
      primary="$(printf '%s\n' "$parsed" | sed -n '3p')"
      fingerprint="$(printf '%s\n' "$parsed" | sed -n '4p')"
    else
      log "WARN: research outage classifier failed for stage=$stage; using non_network fallback"
      classifier_path=""
    fi
  else
    classifier_path=""
  fi

  printf '%s|%s|%s|%s|%s\n' "${classification:-non_network}" "${failure_class:-OBJECTIVE_FAIL}" "${primary:-none}" "${fingerprint:-}" "${classifier_path:-}"
}

run_research_network_outage_probe() {
  local probe_stdout="$1"
  local probe_stderr="$2"
  local timeout_secs="${NETWORK_OUTAGE_PROBE_TIMEOUT_SECS:-5}"
  local probe_cmd host port

  probe_cmd="$(trim "${NETWORK_OUTAGE_PROBE_CMD:-}")"
  if [ -n "$probe_cmd" ]; then
    run_with_optional_timeout "$timeout_secs" bash -lc "$probe_cmd" >"$probe_stdout" 2>"$probe_stderr"
    return $?
  fi

  host="$(trim "${NETWORK_OUTAGE_PROBE_HOST:-api.openai.com}")"
  port="$(trim "${NETWORK_OUTAGE_PROBE_PORT:-443}")"
  python3 - "$host" "$port" "$timeout_secs" >"$probe_stdout" 2>"$probe_stderr" <<'PY'
import socket
import sys

host = (sys.argv[1] or "").strip()
port_raw = (sys.argv[2] or "").strip()
timeout_raw = (sys.argv[3] or "").strip()
if not host:
    raise SystemExit(2)

try:
    port = int(port_raw)
except Exception:
    raise SystemExit(2)

try:
    timeout = float(timeout_raw)
except Exception:
    timeout = 5.0
if timeout <= 0:
    timeout = 5.0

try:
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
except OSError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1)

last_error = None
for addr in addresses:
    family, socktype, proto, _, sockaddr = addr
    sock = socket.socket(family, socktype, proto)
    sock.settimeout(timeout)
    try:
        sock.connect(sockaddr)
        print(f"probe_ok host={host} addr={sockaddr[0]} port={port}")
        raise SystemExit(0)
    except OSError as exc:
        last_error = str(exc)
    finally:
        try:
            sock.close()
        except Exception:
            pass

if last_error:
    print(last_error, file=sys.stderr)
raise SystemExit(1)
PY
}

run_research_network_outage_probe_capture_rc() {
  local probe_stdout="$1"
  local probe_stderr="$2"
  local rc=0
  local had_errexit="off"

  case "$-" in
    *e*)
      had_errexit="on"
      set +e
      ;;
  esac

  run_research_network_outage_probe "$probe_stdout" "$probe_stderr"
  rc=$?

  if [ "$had_errexit" = "on" ]; then
    set -e
  fi

  return "$rc"
}

write_research_outage_diagnostic_payload() {
  local output_path="$1"
  local stage="$2"
  local why="$3"
  local outcome="$4"
  local primary_signature="$5"
  local fingerprint="$6"
  local classifier_path="$7"
  local stage_stderr="$8"
  local stage_stdout="$9"
  local probe_stdout="${10}"
  local probe_stderr="${11}"
  local attempts_file="${12}"

  python3 - "$output_path" "$stage" "$why" "$outcome" "$primary_signature" "$fingerprint" "$classifier_path" "$stage_stderr" "$stage_stdout" "$probe_stdout" "$probe_stderr" "$attempts_file" <<'PY'
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

(
    output_path,
    stage,
    why,
    outcome,
    primary_signature,
    fingerprint,
    classifier_path,
    stage_stderr,
    stage_stdout,
    probe_stdout,
    probe_stderr,
    attempts_file,
) = sys.argv[1:13]

def read_excerpt(path_raw: str, limit: int = 1200) -> str:
    path = Path(path_raw)
    if not path_raw or not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    text = text[-limit:]
    return re.sub(r"\\s+", " ", text).strip()

attempts: list[dict[str, object]] = []
attempts_path = Path(attempts_file)
if attempts_file and attempts_path.is_file():
    for line in attempts_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) != 4:
            continue
        ts, attempt_raw, rc_raw, sleep_raw = parts
        try:
            attempt = int(attempt_raw)
        except Exception:
            attempt = 0
        try:
            rc = int(rc_raw)
        except Exception:
            rc = 1
        try:
            sleep_secs = int(sleep_raw)
        except Exception:
            sleep_secs = 0
        attempts.append(
            {
                "attempt": attempt,
                "probe_exit_code": rc,
                "scheduled_sleep_secs": sleep_secs,
                "timestamp_utc": ts,
            }
        )

payload: dict[str, object] = {
    "schema_version": "1.0",
    "kind": "network_outage_wait",
    "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "stage": stage,
    "why": why,
    "outcome": outcome,
    "primary_signature": primary_signature or "none",
    "fingerprint": fingerprint or "",
    "classifier_payload": classifier_path if classifier_path else "",
    "stage_logs": {
        "stderr": stage_stderr if stage_stderr else "",
        "stdout": stage_stdout if stage_stdout else "",
    },
    "probe_logs": {
        "stdout": probe_stdout if probe_stdout else "",
        "stderr": probe_stderr if probe_stderr else "",
    },
    "probe_attempts": attempts,
    "probe_attempt_count": len(attempts),
    "probe_stderr_excerpt": read_excerpt(probe_stderr),
}

out_path = Path(output_path)
out_path.parent.mkdir(parents=True, exist_ok=True)
tmp_path = out_path.parent / f".{out_path.name}.tmp"
tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
tmp_path.replace(out_path)
PY
}

emit_or_update_research_outage_incident() {
  local stage="$1"
  local why="$2"
  local primary_signature="$3"
  local fingerprint="$4"
  local classifier_path="$5"
  local diag_json="$6"
  local stage_stderr="$7"
  local stage_stdout="$8"
  local probe_stdout="$9"
  local probe_stderr="${10}"
  local attempts_file="${11}"

  python3 - "$INCIDENT_INCOMING_DIR" "$INCIDENT_WORKING_DIR" "$stage" "$why" "$primary_signature" "$fingerprint" "$classifier_path" "$diag_json" "$stage_stderr" "$stage_stdout" "$probe_stdout" "$probe_stderr" "$attempts_file" <<'PY'
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import sys

(
    incoming_dir_raw,
    working_dir_raw,
    stage,
    why,
    primary_signature,
    fingerprint,
    classifier_path,
    diag_json,
    stage_stderr,
    stage_stdout,
    probe_stdout,
    probe_stderr,
    attempts_file,
) = sys.argv[1:14]

incoming_dir = Path(incoming_dir_raw)
working_dir = Path(working_dir_raw)

def read_text(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""

def normalize_token(value: str) -> str:
    lowered = (value or "").strip().lower()
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered

def parse_attempt_rows(path: Path) -> list[tuple[str, int, int, int]]:
    rows: list[tuple[str, int, int, int]] = []
    raw = read_text(path)
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) != 4:
            continue
        ts, attempt_raw, rc_raw, sleep_raw = parts
        try:
            attempt = int(attempt_raw)
        except Exception:
            attempt = 0
        try:
            rc = int(rc_raw)
        except Exception:
            rc = 1
        try:
            sleep_secs = int(sleep_raw)
        except Exception:
            sleep_secs = 0
        rows.append((ts.strip(), attempt, rc, sleep_secs))
    return rows

def parse_classifier_signatures(path: Path, fallback: str) -> list[str]:
    payload_raw = read_text(path)
    signatures: list[str] = []
    if payload_raw:
        try:
            payload = json.loads(payload_raw)
            matched = payload.get("matched_signatures")
            if isinstance(matched, list):
                for item in matched:
                    token = str(item or "").strip()
                    if token:
                        signatures.append(token)
        except Exception:
            signatures = []
    if not signatures:
        token = str(fallback or "").strip()
        signatures = [token] if token else ["none"]
    deduped: list[str] = []
    seen: set[str] = set()
    for token in signatures:
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(token)
    return deduped or ["none"]

def remediation_for(signature: str) -> str:
    key = normalize_token(signature)
    if key == "dns_resolution_failure":
        return "Validate resolver and outbound DNS path for provider hosts, then retry the failed stage."
    if key == "transport_timeout":
        return "Check upstream latency/path health and firewall state; keep probe retries active until timeout failures clear."
    if key in {"socket_connect_failure", "api_connection_error", "curl_transport_failure"}:
        return "Restore TCP/TLS connectivity to provider endpoints before resuming stage execution."
    if key == "http_transport_5xx":
        return "Treat as upstream transport instability; pause stage execution and resume after provider/API health recovers."
    if key == "resume-net-wait":
        return "Recover baseline network access first, then rerun research loop startup to clear stale NET_WAIT state."
    return "Restore provider/network transport reachability and rerun the affected stage."

def extract_existing_attempt_count(text: str) -> int:
    m = re.search(r"(?m)^- Attempt Count:\s*(\d+)\s*$", text)
    if not m:
        return 0
    try:
        return int(m.group(1))
    except Exception:
        return 0

def extract_existing_created_utc(text: str, fallback: str) -> str:
    m = re.search(r"(?m)^- Created UTC:\s*(.+)\s*$", text)
    if not m:
        return fallback
    value = m.group(1).strip()
    return value or fallback

def extract_history_lines(text: str) -> list[str]:
    match = re.search(r"(?ims)^##\s+Attempt History\s*\n(.*?)(?=^\s*##\s+|\Z)", text)
    if not match:
        return []
    lines: list[str] = []
    for raw in match.group(1).splitlines():
        line = raw.rstrip()
        if line.startswith("- "):
            lines.append(line)
    return lines

def latest_probe_excerpt(path: Path, limit: int = 900) -> str:
    text = read_text(path)
    if not text:
        return ""
    trimmed = text[-limit:]
    return re.sub(r"\s+", " ", trimmed).strip()

now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
safe_stage = normalize_token(stage) or "unknown"
primary = (primary_signature or "").strip() or "none"
fingerprint_value = (fingerprint or "").strip()
classifier_file = Path(classifier_path) if classifier_path else Path("")
probe_rows = parse_attempt_rows(Path(attempts_file) if attempts_file else Path(""))
signatures = parse_classifier_signatures(classifier_file, primary)
primary = signatures[0] if signatures else primary
dedupe_material = "|".join(
    [
        "research_network_outage",
        safe_stage,
        normalize_token(primary),
        normalize_token(fingerprint_value),
        ",".join(sorted(normalize_token(sig) for sig in signatures)),
    ]
)
dedupe_key = hashlib.sha256(dedupe_material.encode("utf-8")).hexdigest()
incident_id = f"INC-{dedupe_key[:12]}"
incident_filename = f"{incident_id}.md"
incoming_path = incoming_dir / incident_filename
working_path = working_dir / incident_filename
if working_path.is_file():
    incident_path = working_path
else:
    incident_path = incoming_path
incident_status = "working" if incident_path == working_path else "incoming"

existing = read_text(incident_path)
attempt_count = extract_existing_attempt_count(existing) + 1
created_utc = extract_existing_created_utc(existing, now_iso)
history_lines = extract_history_lines(existing)
probe_count = len(probe_rows)
history_lines.append(
    "- {ts} | Attempt {attempt} | Stage={stage} | Signature={sig} | ProbeAttempts={probe_count} | Diagnostics={diag}".format(
        ts=now_iso,
        attempt=attempt_count,
        stage=stage or "unknown",
        sig=primary,
        probe_count=probe_count,
        diag=diag_json or "<none>",
    )
)
probe_excerpt = latest_probe_excerpt(Path(probe_stderr) if probe_stderr else Path(""))
fingerprint_field = fingerprint_value or dedupe_key[:16]
minimal_unblock = remediation_for(primary)

probe_retry_lines = [
    "- {ts} | probe_attempt={attempt} | rc={rc} | next_sleep_secs={sleep}".format(
        ts=ts or "<unknown>",
        attempt=attempt,
        rc=rc,
        sleep=sleep_secs,
    )
    for ts, attempt, rc, sleep_secs in probe_rows
]
if not probe_retry_lines:
    probe_retry_lines = ["- <none captured>"]

run_artifacts = []
for label, value in (
    ("stage stderr", stage_stderr),
    ("stage stdout", stage_stdout),
    ("probe stdout", probe_stdout),
    ("probe stderr", probe_stderr),
    ("probe attempts", attempts_file),
):
    token = (value or "").strip()
    if token:
        run_artifacts.append(f"- {label}: `{token}`")
if not run_artifacts:
    run_artifacts = ["- run artifacts: <none>"]

body = "\n".join(
    [
        "---",
        f"incident_id: {incident_id}",
        f"fingerprint: {fingerprint_field}",
        f"failure_signature: {primary}",
        f"status: {incident_status}",
        "severity: S2",
        f"source_task: research-stage/{stage or 'unknown'}",
        f"opened_at: {created_utc}",
        f"updated_at: {now_iso}",
        "---",
        "",
        "# Research Network Outage Incident Intake",
        "",
        f"- Incident-ID: `{incident_id}`",
        f"- Fingerprint: `{fingerprint_field}`",
        f"- Failure signature: `{primary}`",
        f"- Dedupe key: `{dedupe_key}`",
        f"- Attempt Count: {attempt_count}",
        f"- Created UTC: {created_utc}",
        f"- Updated UTC: {now_iso}",
        f"- Stage: `{stage or 'unknown'}`",
        f"- Why: {why or 'network outage classification exhausted probe wait window'}",
        "",
        "## Summary",
        f"- Research stage `{stage or 'unknown'}` hit repeated network/API transport failures and exhausted NET_WAIT probes.",
        "",
        "## Impact",
        "- Stage progression is paused until transport reachability is restored or a remediation path is applied.",
        "",
        "## Trigger",
        f"- Primary signature: `{primary}`",
        f"- Matched provider/network signatures: `{', '.join(signatures)}`",
        f"- Classifier payload: `{classifier_path or '<none>'}`",
        f"- Probe attempts captured: `{probe_count}`",
        "",
        "## Evidence",
        f"- Diagnostics bundle: `{diag_json or '<none>'}`",
        *run_artifacts,
        f"- Probe stderr excerpt: `{probe_excerpt or '<none>'}`",
        "",
        "## Reproduction",
        "1. Trigger a stage failure classified as `network_outage` (or resume from `### NET_WAIT`).",
        "2. Keep outage probe failing until `NETWORK_OUTAGE_MAX_PROBES` is reached.",
        "3. Run `bash agents/research_loop.sh --once` and verify this incident file is emitted/updated.",
        "",
        "## Hypothesis",
        "- Primary hypothesis: external provider/network transport outage blocked stage execution.",
        "- Confidence: medium",
        f"- Evidence: classifier signature `{primary}` and diagnostics `{diag_json or '<none>'}`.",
        "",
        "## Alternative Hypotheses",
        "- AH-01: local network guard or firewall policy blocked egress.",
        "  - Status: candidate",
        f"  - Evidence: inspect `probe_stderr` `{probe_stderr or '<none>'}` and guard state artifacts.",
        "- AH-02: stage implementation bug unrelated to transport.",
        "  - Status: unsupported",
        f"  - Evidence: outage classifier reported transport signatures `{', '.join(signatures)}`.",
        "",
        "## Investigation",
        "- Steps:",
        "  1. Confirm outage classifier and probe artifacts agree on transport failure.",
        "  2. Validate provider reachability and DNS/TLS path from the runner host.",
        "  3. Re-run the blocked stage after connectivity is restored.",
        "- Findings:",
        f"  - Probe attempts recorded: `{probe_count}`.",
        f"  - Minimal unblock recommendation: {minimal_unblock}",
        "",
        "## Governance Routing",
        "- Severity Class: S2",
        "- preemption behavior: S1 preempt-all incident work; S2 preempt S3/S4; S3/S4 continue FIFO.",
        "- Incident Class: other",
        f"- minimal-unblock-first path: {minimal_unblock}",
        "- rewrite task card path: Not required unless outage handling requirements are malformed/overscoped.",
        "- spec addendum backflow: Add only if outage handling contract needs spec-level correction.",
        "- regression test requirement: Add a deterministic outage-recovery smoke scenario for future regressions.",
        f"- regression test evidence: `{diag_json or '<none>'}`",
        "- framework-level routing: If transport recovers but stage still fails with provider signatures, route framework-level remediation into `agents/taskspending.md`.",
        "- external blocker routing: Route unresolved provider/network dependencies to `agents/ideas/blockers/incoming` with outage evidence bundle.",
        "",
        "## Unsupported Hypotheses",
        "- AH-02",
        "  - Status: unsupported",
        f"  - Evidence: transport signatures persisted (`{', '.join(signatures)}`) across probe retries.",
        "",
        "## fix_spec",
        "- Fix Spec ID: `<pending>`",
        "- Fix Spec Path: `agents/ideas/specs/<pending>.md`",
        f"- Scope summary: Restore deterministic progression for stage `{stage or 'unknown'}` after transport outages.",
        "- Severity Class: S2",
        "- preemption behavior: S2 preempt S3/S4 incident work",
        f"- minimal-unblock-first path: {minimal_unblock}",
        "- rewrite task card path: not required by default",
        "- spec addendum backflow: pending triage",
        "- regression test requirement: required",
        f"- regression test evidence: `{diag_json or '<none>'}`",
        "- framework-level routing: conditional",
        "- external blocker routing: conditional",
        "",
        "## Task Handoff",
        "- taskspending target: `agents/taskspending.md`",
        "- decomposition stage: `agents/_taskmaster.md`",
        "- handoff status: pending",
        "",
        "## Probe Retry History",
        *probe_retry_lines,
        "",
        "## Attempt History",
        *history_lines,
        "",
        "## Resolution Criteria",
        "- Connectivity is restored and affected stage succeeds without network outage classification.",
        "",
        "## Closeout Artifact",
        "- Close timestamp: <pending>",
        "- Final fix_spec path: `<pending>`",
        "- taskspending checkpoint: `agents/taskspending.md`",
        "- Unsupported hypotheses preserved with evidence: yes",
        "- Closeout decision: deferred",
        "",
        "## Follow-ups",
        "- Monitor recurrence for the same dedupe signature and adjust probe/remediation thresholds only with evidence.",
        "",
    ]
)

incident_path.parent.mkdir(parents=True, exist_ok=True)
incident_path.write_text(body, encoding="utf-8")
print(f"{incident_path.as_posix()}|{attempt_count}|{incident_id}")
PY
}

wait_for_research_network_recovery() {
  local stage="$1"
  local why="$2"
  local primary_signature="$3"
  local fingerprint="$4"
  local classifier_path="$5"
  local stage_stderr="$6"
  local stage_stdout="$7"

  local attempt=1
  local wait_secs="${NETWORK_OUTAGE_WAIT_INITIAL_SECS:-15}"
  local wait_max="${NETWORK_OUTAGE_WAIT_MAX_SECS:-300}"
  local max_probes="${NETWORK_OUTAGE_MAX_PROBES:-0}"
  local policy route_to_blocker route_to_incident
  local safe_stage outage_dir attempts_file probe_stdout probe_stderr diag_json outcome rc now_utc
  local incident_emit_result incident_path incident_attempt_count incident_id

  policy="$(printf '%s' "${NETWORK_OUTAGE_POLICY:-pause_resume}" | tr '[:upper:]' '[:lower:]')"
  if [ -z "$policy" ]; then
    policy="pause_resume"
  fi
  route_to_blocker="off"
  route_to_incident="off"
  if [ "$policy" = "blocker" ] || truthy "${NETWORK_OUTAGE_ROUTE_TO_BLOCKER:-off}"; then
    route_to_blocker="on"
  fi
  if [ "$policy" = "incident" ] || truthy "${NETWORK_OUTAGE_ROUTE_TO_INCIDENT:-off}"; then
    route_to_incident="on"
  fi

  safe_stage="$(slugify_token "$stage")"
  outage_dir="$DIAGNOSTICS_DIR/$(date +%F_%H%M%S)_net_wait_${safe_stage}"
  mkdir -p "$outage_dir"
  attempts_file="$outage_dir/probe_attempts.log"
  probe_stdout="$outage_dir/probe.stdout.log"
  probe_stderr="$outage_dir/probe.stderr.log"
  diag_json="$outage_dir/outage_event.json"
  outcome="waiting"

  write_research_status "### NET_WAIT"
  append_research_event "NETWORK_OUTAGE_WAIT" "stage=$stage signature=${primary_signature:-none} fingerprint=${fingerprint:-none} diagnostics=$outage_dir"
  log "Research NET_WAIT: stage=$stage signature=${primary_signature:-none} diagnostics=$outage_dir"

  while true; do
    : >"$probe_stdout"
    : >"$probe_stderr"
    if run_research_network_outage_probe_capture_rc "$probe_stdout" "$probe_stderr"; then
      rc=0
      now_utc="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
      printf '%s|%s|%s|%s\n' "$now_utc" "$attempt" "$rc" "$wait_secs" >>"$attempts_file"
      outcome="recovered"
      record_incident_recurrence_signature "network|stage=${stage}|signature=${primary_signature:-none}" "net_wait" "recovered" || true
      append_research_event "NETWORK_OUTAGE_RECOVERED" "stage=$stage attempt=$attempt"
      log "Research NET_WAIT: recovered at attempt=$attempt stage=$stage"
      write_research_status "### IDLE"
      write_research_outage_diagnostic_payload "$diag_json" "$stage" "$why" "$outcome" "$primary_signature" "$fingerprint" "$classifier_path" "$stage_stderr" "$stage_stdout" "$probe_stdout" "$probe_stderr" "$attempts_file"
      return 0
    else
      rc=$?
    fi
    now_utc="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    printf '%s|%s|%s|%s\n' "$now_utc" "$attempt" "$rc" "$wait_secs" >>"$attempts_file"
    log "Research NET_WAIT: probe failed stage=$stage attempt=$attempt rc=$rc next_sleep=${wait_secs}s"

    if [[ "$max_probes" =~ ^[0-9]+$ ]] && [ "$max_probes" -gt 0 ] && [ "$attempt" -ge "$max_probes" ]; then
      append_research_event "NETWORK_OUTAGE_EXHAUSTED" "stage=$stage attempts=$attempt"
      record_incident_recurrence_signature "network|stage=${stage}|signature=${primary_signature:-none}" "net_wait" "fail" || true
      write_research_outage_diagnostic_payload "$diag_json" "$stage" "$why" "$outcome" "$primary_signature" "$fingerprint" "$classifier_path" "$stage_stderr" "$stage_stdout" "$probe_stdout" "$probe_stderr" "$attempts_file"
      if [ "$route_to_incident" = "on" ]; then
        incident_emit_result=""
        if incident_emit_result="$(emit_or_update_research_outage_incident "$stage" "$why" "$primary_signature" "$fingerprint" "$classifier_path" "$diag_json" "$stage_stderr" "$stage_stdout" "$probe_stdout" "$probe_stderr" "$attempts_file")"; then
          IFS='|' read -r incident_path incident_attempt_count incident_id <<<"$incident_emit_result"
          append_research_event "NETWORK_OUTAGE_INCIDENT_INTAKE" "incident=$incident_path incident_id=${incident_id:-unknown} attempt_count=${incident_attempt_count:-0} stage=$stage signature=${primary_signature:-none} diagnostics=$diag_json"
          log "Research NET_WAIT: emitted outage incident intake incident=${incident_path:-unknown} attempt_count=${incident_attempt_count:-0} signature=${primary_signature:-none}"
        else
          log "WARN: failed to emit outage incident intake for stage=$stage signature=${primary_signature:-none}"
        fi
      fi
      if [ "$route_to_incident" = "on" ] || [ "$route_to_blocker" = "on" ]; then
        append_research_event "NETWORK_OUTAGE_ESCALATED" "stage=$stage policy=$policy route_to_blocker=$route_to_blocker route_to_incident=$route_to_incident attempts=$attempt"
        write_research_status "### IDLE"
        log "Research NET_WAIT: exhausted probe budget; routing by policy stage=$stage policy=$policy route_to_blocker=$route_to_blocker route_to_incident=$route_to_incident"
        return 1
      fi
      append_research_event "NETWORK_OUTAGE_CONTINUE" "stage=$stage attempts=$attempt policy=$policy"
      log "Research NET_WAIT: probe budget exhausted stage=$stage attempts=$attempt; continuing wait loop (policy=$policy)"
      attempt=0
      wait_secs="${NETWORK_OUTAGE_WAIT_INITIAL_SECS:-15}"
    fi

    if [ "$wait_secs" -gt 0 ]; then
      sleep "$wait_secs"
    fi
    wait_secs=$(( wait_secs * 2 ))
    if [ "$wait_secs" -gt "$wait_max" ]; then
      wait_secs="$wait_max"
    fi
    attempt=$(( attempt + 1 ))
  done
}

run_research_network_guard() {
  local stage="$1"
  local base="$2"
  local stderr_path="${3:-}"
  local mode policy state_file guard_out rc

  mode="${NETWORK_GUARD_MODE:-off}"
  policy="${RESEARCH_NETWORK_GUARD_POLICY:-allow}"
  state_file="${base}.network_guard.json"

  if [ ! -f "$NETWORK_GUARD_TOOL" ]; then
    if [ "$mode" = "on" ]; then
      [ -n "$stderr_path" ] && printf 'NETWORK_GUARD_ERROR missing_tool=%s mode=%s policy=%s\n' "$NETWORK_GUARD_TOOL" "$mode" "$policy" >>"$stderr_path"
      append_research_event "NETWORK_GUARD" "stage=$stage mode=$mode policy=$policy result=error state=$state_file reason=missing_tool"
      log "Stage $stage: network guard missing tool while enabled: $NETWORK_GUARD_TOOL"
      return 1
    fi
    append_research_event "NETWORK_GUARD" "stage=$stage mode=$mode policy=$policy result=skip state=$state_file reason=missing_tool_mode_off"
    return 0
  fi

  if guard_out="$(bash "$NETWORK_GUARD_TOOL" --phase research --enabled "$mode" --policy "$policy" --context "$stage" --state-file "$state_file" 2>&1)"; then
    append_research_event "NETWORK_GUARD" "stage=$stage mode=$mode policy=$policy result=pass state=$state_file"
    log "Stage $stage: network guard pass mode=$mode policy=$policy state=$state_file"
    return 0
  fi

  rc=$?
  if [ -n "$guard_out" ] && [ -n "$stderr_path" ]; then
    printf '%s\n' "$guard_out" >>"$stderr_path"
  fi
  append_research_event "NETWORK_GUARD" "stage=$stage mode=$mode policy=$policy result=block exit=$rc state=$state_file"
  log "Stage $stage: network guard blocked execution mode=$mode policy=$policy exit=$rc state=$state_file"
  return "$rc"
}

STAGE_COUNTER=0

run_stage() {
  local stage="$1"
  local runner model prompt effort search timeout_secs base stdout_path stderr_path last_path status
  local classification_info outage_classification outage_failure_class outage_signature outage_fingerprint outage_classifier_path
  local attempt=1
  local max_attempts
  local retry_sleep
  local exit_code=1
  local guard_rc=0

  runner="$(stage_runner_for "$stage")"
  model="$(stage_model_for "$stage")"
  effort="$(stage_effort_for "$stage")"
  search="$(stage_search_for "$stage")"
  timeout_secs="$(stage_timeout_for "$stage")"
  prompt="$(stage_prompt_for "$stage")"
  max_attempts=$(( STAGE_RETRY_MAX + 1 ))
  retry_sleep="$STAGE_RETRY_BACKOFF_SECS"

  while [ "$attempt" -le "$max_attempts" ]; do
    STAGE_COUNTER=$(( STAGE_COUNTER + 1 ))
    base="$RUNS_DIR/$(date +%F_%H%M%S)_$(printf '%04d' "$STAGE_COUNTER")_${stage}"
    stdout_path="$base.stdout.log"
    stderr_path="$base.stderr.log"
    last_path="$base.last.md"
    write_stage_checkpoint "$stage" "$base" "$stdout_path" "$stderr_path" "$last_path"
    guard_rc=0
    run_research_network_guard "$stage" "$base" "$stderr_path" || guard_rc=$?
    if [ "$guard_rc" -ne 0 ]; then
      exit_code="$guard_rc"
      clear_stage_checkpoint "$base"
      break
    fi

    if run_cycle_with_fallback "$runner" "$model" "$effort" "$prompt" "$stdout_path" "$stderr_path" "$last_path" "$search" high "$timeout_secs" "Research-$stage"; then
      clear_stage_checkpoint "$base"
      status="$(read_research_status)"
      case "$status" in
        "### IDLE")
          return 0
          ;;
        "### BLOCKED")
          log "Stage $stage reported BLOCKED"
          exit_code=1
          ;;
        *)
          log "Stage $stage left unexpected research status: $status"
          write_research_status "### BLOCKED"
          exit_code=1
          ;;
      esac
    else
      exit_code=$?
      clear_stage_checkpoint "$base"
    fi

    classification_info="$(classify_research_stage_failure "$stage" "$stderr_path" "$stdout_path" "$exit_code")"
    IFS='|' read -r outage_classification outage_failure_class outage_signature outage_fingerprint outage_classifier_path <<<"$classification_info"
    if { [ "$outage_classification" = "network_outage" ] || [ "$outage_failure_class" = "NET_WAIT" ]; } && truthy "${NETWORK_OUTAGE_RESILIENCE_MODE:-on}"; then
      log "Stage $stage classified as network_outage (failure_class=${outage_failure_class:-OBJECTIVE_FAIL} signature=${outage_signature:-none}); entering NET_WAIT"
      if wait_for_research_network_recovery "$stage" "stage failure attempt=$attempt exit=$exit_code" "$outage_signature" "$outage_fingerprint" "$outage_classifier_path" "$stderr_path" "$stdout_path"; then
        continue
      fi
      break
    fi

    if [ "$attempt" -lt "$max_attempts" ]; then
      append_research_event "STAGE_RETRY" "stage=$stage attempt=$attempt next_attempt=$(( attempt + 1 )) exit=$exit_code sleep_secs=$retry_sleep"
      log "Stage $stage failed on attempt $attempt/$max_attempts (exit=$exit_code); retrying in ${retry_sleep}s"
      if [ "$retry_sleep" -gt 0 ]; then
        sleep "$retry_sleep"
      fi
    fi
    attempt=$(( attempt + 1 ))
  done

  write_research_status "### BLOCKED"
  log "Stage $stage failed after $max_attempts attempt(s). Last logs: $stdout_path $stderr_path"
  return "$exit_code"
}

promote_goal_to_raw_if_needed() {
  local src_path dst_path
  if ! goal_seed_promotion_needed; then
    return 0
  fi
  src_path="$(oldest_payload_file "$GOAL_DIR")"
  if [ -z "$src_path" ]; then
    return 0
  fi
  mkdir -p "$RAW_DIR"
  dst_path="$RAW_DIR/$(basename "$src_path")"
  cp "$src_path" "$dst_path"
  if [ -f "$dst_path" ]; then
    record_goal_promotion_state "$src_path" "$dst_path"
    append_research_event "GOAL_PROMOTED_TO_RAW" "path=$dst_path source=$src_path mode=copy"
    log "GoalSpec: promoted one goal file into raw queue via copy (source preserved): $dst_path"
  fi
}

enforce_canonical_goal_route_policy() {
  local canonical_goal="$GOAL_DIR/base_goal.md"
  local routed_later="$LATER_DIR/base_goal.md"
  local moved_path

  if [ ! -f "$canonical_goal" ] || [ ! -f "$routed_later" ]; then
    return 0
  fi

  moved_path="$(move_payload_file_path "$routed_later" "$STAGING_DIR" || true)"
  if [ -z "$moved_path" ]; then
    write_research_status "### BLOCKED"
    log "GoalSpec policy: failed to override canonical base goal route from later to staging"
    return 1
  fi

  append_research_event "GOAL_ROUTE_OVERRIDE" "from=$routed_later to=$moved_path reason=canonical_goal_cannot_route_later"
  log "GoalSpec policy: canonical base goal cannot route to later; moved to staging: $moved_path"
  return 0
}

objective_profile_sync_state_probe() {
  local goal_path="$1"
  python3 - "$goal_path" \
    "$OBJECTIVE_PROFILE_SYNC_STATE_FILE" \
    "$OBJECTIVE_CONTRACT_FILE" \
    "$AUDIT_STRICT_CONTRACT_FILE" \
    "$FAMILY_POLICY_FILE" \
    "$AUDIT_COMPLETION_MANIFEST" \
    "$OBJECTIVE_PROFILE_SYNC_REPORT_FILE" \
    "$OBJECTIVE_PROFILE_SYNC_CONTRACT_VALIDATION_REPORT" <<'PY'
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

goal_path = Path(sys.argv[1])
state_path = Path(sys.argv[2])
objective_contract_path = Path(sys.argv[3])
strict_contract_path = Path(sys.argv[4])
family_policy_path = Path(sys.argv[5])
completion_manifest_path = Path(sys.argv[6])
report_path = Path(sys.argv[7])
validation_report_path = Path(sys.argv[8])
repo_root = Path.cwd()


def resolve_path(raw: str) -> Path | None:
    value = str(raw or "").strip()
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = repo_root / path
    return path


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonicalize_goal_text(goal_text: str) -> str:
    kept = []
    skipping = False

    for line in goal_text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if stripped.startswith("## "):
            title = stripped[3:].strip()
            if title == "Spec Family (Synthesis Outputs)":
                while kept and not kept[-1].strip():
                    kept.pop()
                skipping = True
                continue
            skipping = False
        if not skipping:
            kept.append(line)

    canonical = "".join(kept)
    if canonical and not canonical.endswith("\n"):
        canonical += "\n"
    return canonical


def sha256_goal_file(path: Path) -> str:
    return hashlib.sha256(canonicalize_goal_text(path.read_text(encoding="utf-8")).encode("utf-8")).hexdigest()


issues: list[str] = []
state: dict[str, object] = {}
current_goal_sha = ""

if not goal_path.exists() or not goal_path.is_file():
    issues.append(f"missing-goal:{goal_path.as_posix()}")
else:
    current_goal_sha = sha256_goal_file(goal_path)

if not state_path.exists() or not state_path.is_file():
    issues.append("missing-state")
else:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:
        issues.append(f"invalid-state:{exc}")

if current_goal_sha and state:
    if not bool(state.get("artifacts_valid")):
        issues.append("artifacts-invalid")
    if str(state.get("goal_sha256", "")).strip() != current_goal_sha:
        issues.append("goal-sha-mismatch")

    expected_pairs = (
        ("objective_contract_path", objective_contract_path, "objective-contract-path-mismatch"),
        ("strict_contract_path", strict_contract_path, "strict-contract-path-mismatch"),
        ("family_policy_path", family_policy_path, "family-policy-path-mismatch"),
        ("completion_manifest_path", completion_manifest_path, "completion-manifest-path-mismatch"),
        ("report_path", report_path, "sync-report-path-mismatch"),
    )
    for field_name, expected_path, issue_name in expected_pairs:
        recorded = resolve_path(state.get(field_name, ""))
        if recorded is None:
            issues.append(f"missing-{field_name}")
            continue
        if recorded.resolve() != expected_path.resolve():
            issues.append(issue_name)

    profile_path = resolve_path(state.get("profile_path", ""))
    if profile_path is None:
        issues.append("missing-profile-path")
    elif not profile_path.exists() or not profile_path.is_file():
        issues.append("missing-profile")
    else:
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception as exc:
            issues.append(f"invalid-profile:{exc}")
        else:
            if str(profile.get("profile_id", "")).strip() != str(state.get("profile_id", "")).strip():
                issues.append("profile-id-mismatch")
            source_goal = profile.get("source_goal")
            if not isinstance(source_goal, dict) or str(source_goal.get("sha256", "")).strip() != current_goal_sha:
                issues.append("profile-goal-sha-mismatch")

    profile_markdown_path = resolve_path(state.get("profile_markdown_path", ""))
    if profile_markdown_path is None:
        issues.append("missing-profile-markdown-path")
    elif not profile_markdown_path.exists() or not profile_markdown_path.is_file():
        issues.append("missing-profile-markdown")

    semantic_seed_path = resolve_path(state.get("semantic_seed_path", ""))
    semantic_seed_sha = str(state.get("semantic_seed_sha256", "")).strip()
    if semantic_seed_path is not None:
        if not semantic_seed_path.exists() or not semantic_seed_path.is_file():
            issues.append("missing-semantic-seed")
        elif semantic_seed_sha and sha256_file(semantic_seed_path) != semantic_seed_sha:
            issues.append("semantic-seed-sha-mismatch")

    recorded_family_policy_path = resolve_path(state.get("family_policy_path", ""))
    if recorded_family_policy_path is None:
        issues.append("missing-family-policy-path")
    elif not recorded_family_policy_path.exists() or not recorded_family_policy_path.is_file():
        issues.append("missing-family-policy")
    else:
        family_policy_sha = str(state.get("family_policy_sha256", "")).strip()
        if family_policy_sha and sha256_file(recorded_family_policy_path) != family_policy_sha:
            issues.append("family-policy-sha-mismatch")

required_paths = (
    (objective_contract_path, "missing-objective-contract"),
    (strict_contract_path, "missing-strict-contract"),
    (family_policy_path, "missing-family-policy"),
    (completion_manifest_path, "missing-completion-manifest"),
    (report_path, "missing-sync-report"),
    (validation_report_path, "missing-validation-report"),
)
for path, issue_name in required_paths:
    if not path.exists() or not path.is_file():
        issues.append(issue_name)

if validation_report_path.exists() and validation_report_path.is_file():
    try:
        json.loads(validation_report_path.read_text(encoding="utf-8"))
    except Exception as exc:
        issues.append(f"invalid-validation-report:{exc}")

if issues:
    print(",".join(issues))
    raise SystemExit(1)

print("current")
PY
}

objective_profile_sync_contract_probe() {
  local tmp_output="$TMP_DIR/objective_profile_sync_contract_probe.json"
  local tmp_stderr="$TMP_DIR/objective_profile_sync_contract_probe.stderr"

  if python3 "$OBJECTIVE_CONTRACT_VALIDATOR_TOOL" \
    --schema "$OBJECTIVE_CONTRACT_SCHEMA_FILE" \
    --contract "$OBJECTIVE_CONTRACT_FILE" \
    --strict-contract "$AUDIT_STRICT_CONTRACT_FILE" \
    --command-contract-report "$COMMAND_CONTRACT_REPORT_FILE" \
    --output "$tmp_output" >/dev/null 2>"$tmp_stderr"; then
    rm -f -- "$tmp_output" "$tmp_stderr"
    printf 'current\n'
    return 0
  fi

  if [ -s "$tmp_stderr" ]; then
    sed -n '1p' "$tmp_stderr"
  else
    printf 'objective-contract-validation-failed\n'
  fi
  rm -f -- "$tmp_output" "$tmp_stderr"
  return 1
}

resolve_authoritative_goal_path() {
  local state_goal family_goal goal_payload staged_payload articulated_payload raw_payload finished_goal

  state_goal="$(
    python3 - "$OBJECTIVE_PROFILE_SYNC_STATE_FILE" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(1)
data = json.loads(path.read_text(encoding="utf-8"))
goal_path = str(data.get("goal_path", "")).strip()
if not goal_path:
    raise SystemExit(1)
print(goal_path)
PY
  )" || state_goal=""
  if [ -n "$state_goal" ] && [ -f "$state_goal" ]; then
    printf '%s\n' "$state_goal"
    return 0
  fi

  family_goal="$(
    python3 - "$SPEC_FAMILY_STATE_FILE" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(1)
data = json.loads(path.read_text(encoding="utf-8"))
goal_path = str(data.get("source_idea_path", "")).strip()
if not goal_path:
    raise SystemExit(1)
print(goal_path)
PY
  )" || family_goal=""
  if [ -n "$family_goal" ] && [ -f "$family_goal" ]; then
    printf '%s\n' "$family_goal"
    return 0
  fi

  if [ -f "$GOAL_DIR/base_goal.md" ]; then
    printf '%s\n' "$GOAL_DIR/base_goal.md"
    return 0
  fi

  finished_goal="$IDEAS_FINISHED_DIR/base_goal.md"
  if [ -f "$finished_goal" ]; then
    printf '%s\n' "$finished_goal"
    return 0
  fi

  goal_payload="$(oldest_payload_file "$GOAL_DIR" || true)"
  if [ -n "$goal_payload" ] && [ -f "$goal_payload" ]; then
    printf '%s\n' "$goal_payload"
    return 0
  fi

  staged_payload="$(oldest_payload_file "$STAGING_DIR" || true)"
  if [ -n "$staged_payload" ] && [ -f "$staged_payload" ]; then
    printf '%s\n' "$staged_payload"
    return 0
  fi

  articulated_payload="$(oldest_payload_file "$ARTICULATED_DIR" || true)"
  if [ -n "$articulated_payload" ] && [ -f "$articulated_payload" ]; then
    printf '%s\n' "$articulated_payload"
    return 0
  fi

  raw_payload="$(oldest_payload_file "$RAW_DIR" || true)"
  if [ -n "$raw_payload" ] && [ -f "$raw_payload" ]; then
    printf '%s\n' "$raw_payload"
    return 0
  fi

  return 1
}

resolve_objective_profile_sync_goal_path_for_audit() {
  resolve_authoritative_goal_path
}

run_objective_profile_sync_for_goal() {
  local goal_path="$1"
  local trigger="${2:-unspecified}"
  local spec_family_state_path="${SPEC_FAMILY_STATE_FILE:-agents/.research_runtime/spec_family_state.json}"
  local previous_source="__UNSET__"
  local previous_spec_family_state="__UNSET__"
  local rc=0
  local verify_reason=""
  local pin_summary=""
  local initial_family_policy_pin_active="false"
  local initial_family_policy_pin_reason=""
  local initial_family_policy_pin_fields="none"
  local initial_family_policy_pin_state_path=""

  if [ -z "$goal_path" ] || [ ! -f "$goal_path" ]; then
    write_research_status "### BLOCKED"
    log "Objective profile sync: missing goal path for trigger=$trigger (${goal_path:-none})"
    return 1
  fi

  if [ "${OBJECTIVE_PROFILE_SYNC_SOURCE_PATH+x}" = "x" ]; then
    previous_source="$OBJECTIVE_PROFILE_SYNC_SOURCE_PATH"
  fi
  if [ "${OBJECTIVE_PROFILE_SYNC_SPEC_FAMILY_STATE_PATH+x}" = "x" ]; then
    previous_spec_family_state="$OBJECTIVE_PROFILE_SYNC_SPEC_FAMILY_STATE_PATH"
  fi
  export OBJECTIVE_PROFILE_SYNC_SOURCE_PATH="$goal_path"
  export OBJECTIVE_PROFILE_SYNC_SPEC_FAMILY_STATE_PATH="$spec_family_state_path"

  append_research_event "OBJECTIVE_PROFILE_SYNC_START" "trigger=$trigger goal=$goal_path"
  run_stage objective_profile_sync || rc=$?

  if [ "$previous_source" = "__UNSET__" ]; then
    unset OBJECTIVE_PROFILE_SYNC_SOURCE_PATH || true
  else
    export OBJECTIVE_PROFILE_SYNC_SOURCE_PATH="$previous_source"
  fi
  if [ "$previous_spec_family_state" = "__UNSET__" ]; then
    unset OBJECTIVE_PROFILE_SYNC_SPEC_FAMILY_STATE_PATH || true
  else
    export OBJECTIVE_PROFILE_SYNC_SPEC_FAMILY_STATE_PATH="$previous_spec_family_state"
  fi

  if [ "$rc" -ne 0 ]; then
    return "$rc"
  fi

  if ! verify_reason="$(objective_profile_sync_state_probe "$goal_path")"; then
    write_research_status "### BLOCKED"
    append_research_event "OBJECTIVE_PROFILE_SYNC_FAILED" "trigger=$trigger goal=$goal_path reason=${verify_reason:-state-probe-failed}"
    log "Objective profile sync: post-stage verification failed for $goal_path (${verify_reason:-state-probe-failed})"
    return 1
  fi
  if ! verify_reason="$(objective_profile_sync_contract_probe)"; then
    write_research_status "### BLOCKED"
    append_research_event "OBJECTIVE_PROFILE_SYNC_FAILED" "trigger=$trigger goal=$goal_path reason=${verify_reason:-objective-contract-validation-failed}"
    log "Objective profile sync: contract validation failed for $goal_path (${verify_reason:-objective-contract-validation-failed})"
    return 1
  fi

  if pin_summary="$(
    python3 - "$OBJECTIVE_PROFILE_SYNC_STATE_FILE" <<'PY'
import json
import shlex
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = {}
if path.exists():
    payload = json.loads(path.read_text(encoding="utf-8"))
pin = payload.get("initial_family_policy_pin", {})
active = isinstance(pin, dict) and bool(pin.get("active"))
pairs = {
    "initial_family_policy_pin_active": "true" if active else "false",
    "initial_family_policy_pin_reason": pin.get("reason", "") if isinstance(pin, dict) else "",
    "initial_family_policy_pin_fields": ",".join(
        str(item).strip() for item in (pin.get("pinned_fields", []) if isinstance(pin, dict) else []) if str(item).strip()
    ) or "none",
    "initial_family_policy_pin_state_path": pin.get("spec_family_state_path", "") if isinstance(pin, dict) else "",
}
for key, value in pairs.items():
    print(f"{key}={shlex.quote(str(value))}")
PY
  )"; then
    # shellcheck disable=SC2086
    eval "$pin_summary"
  fi

  OBJECTIVE_PROFILE_SYNC_LAST_ACTION="ran"
  if [ "$initial_family_policy_pin_active" = "true" ]; then
    append_research_event "INITIAL_FAMILY_POLICY_PINNED" \
      "trigger=$trigger goal=$goal_path reason=$initial_family_policy_pin_reason fields=$initial_family_policy_pin_fields state=$initial_family_policy_pin_state_path"
    log "Objective profile sync: pinned initial-family policy fields during sync (trigger=$trigger fields=$initial_family_policy_pin_fields)"
  fi
  append_research_event "OBJECTIVE_PROFILE_SYNC_COMPLETE" "trigger=$trigger goal=$goal_path"
  log "Objective profile sync: refreshed goal-derived artifacts for $goal_path (trigger=$trigger)"
  return 0
}

ensure_objective_profile_synced() {
  local goal_path="$1"
  local trigger="${2:-unspecified}"
  local stale_reason=""

  OBJECTIVE_PROFILE_SYNC_LAST_ACTION="noop"

  if [ -z "$goal_path" ] || [ ! -f "$goal_path" ]; then
    write_research_status "### BLOCKED"
    log "Objective profile sync: unable to resolve authoritative goal for trigger=$trigger (${goal_path:-none})"
    return 1
  fi

  if ! stale_reason="$(objective_profile_sync_state_probe "$goal_path")"; then
    append_research_event "OBJECTIVE_PROFILE_SYNC_REQUIRED" "trigger=$trigger goal=$goal_path reason=${stale_reason:-state-probe-failed}"
    log "Objective profile sync: refresh required for $goal_path (trigger=$trigger reason=${stale_reason:-state-probe-failed})"
    run_objective_profile_sync_for_goal "$goal_path" "$trigger"
    return $?
  fi

  if ! stale_reason="$(objective_profile_sync_contract_probe)"; then
    append_research_event "OBJECTIVE_PROFILE_SYNC_REQUIRED" "trigger=$trigger goal=$goal_path reason=${stale_reason:-objective-contract-validation-failed}"
    log "Objective profile sync: refresh required for $goal_path (trigger=$trigger reason=${stale_reason:-objective-contract-validation-failed})"
    run_objective_profile_sync_for_goal "$goal_path" "$trigger"
    return $?
  fi

  OBJECTIVE_PROFILE_SYNC_LAST_ACTION="current"
  return 0
}

run_bounded_interrogation_rounds() {
  local target="$1"
  local rounds="$2"
  local source_path="${3:-}"
  local round=1
  local safe_rounds
  local previous_material_sig=""
  local no_delta_streak=0
  local completed_round=0
  local early_stop_reason="none"
  local sig_result sig_hash sig_critic sig_designer

  if [[ "${rounds:-}" =~ ^[0-9]+$ ]]; then
    safe_rounds="$rounds"
  else
    safe_rounds=0
  fi

  if [ "$safe_rounds" -le 0 ]; then
    write_interrogation_state "$target" "none" 0 0 "skipped"
    append_research_event "INTERROGATION_SKIPPED" "target=$target rounds=0"
    clear_interrogation_context_env
    return 0
  fi

  if [ "$target" = "INCIDENT_FIXSPEC" ]; then
    if [ -z "$source_path" ] || [ ! -f "$source_path" ] || [ ! -r "$source_path" ]; then
      write_interrogation_state "$target" "none" 0 "$safe_rounds" "idle"
      write_research_status "### IDLE"
      append_research_event "INTERROGATION_IDLE" "target=$target reason=no-eligible-fixspec source=${source_path:-none}"
      log "Interrogation idle: target=$target has no eligible fix-spec artifact (source=${source_path:-none})"
      clear_interrogation_context_env
      return 0
    fi
    export INTERROGATION_SOURCE_PATH="$source_path"
    append_research_event "INTERROGATION_SOURCE" "target=$target source=$source_path"
  else
    unset INTERROGATION_SOURCE_PATH
  fi

  append_research_event "INTERROGATION_START" "target=$target rounds=$safe_rounds"
  while [ "$round" -le "$safe_rounds" ]; do
    export INTERROGATION_TARGET="$target"
    export INTERROGATION_ROUND_INDEX="$round"
    export INTERROGATION_ROUND_LIMIT="$safe_rounds"

    export INTERROGATION_STAGE="critic"
    write_interrogation_state "$target" "critic" "$round" "$safe_rounds" "running"
    append_research_event "INTERROGATION_CRITIC" "target=$target round=$round/$safe_rounds"
    if ! run_stage critic; then
      write_interrogation_state "$target" "critic" "$round" "$safe_rounds" "blocked"
      clear_interrogation_context_env
      return 1
    fi

    export INTERROGATION_STAGE="designer"
    write_interrogation_state "$target" "designer" "$round" "$safe_rounds" "running"
    append_research_event "INTERROGATION_DESIGNER" "target=$target round=$round/$safe_rounds"
    if ! run_stage designer; then
      write_interrogation_state "$target" "designer" "$round" "$safe_rounds" "blocked"
      clear_interrogation_context_env
      return 1
    fi

    completed_round="$round"

    if [ "$target" = "INCIDENT_FIXSPEC" ] && [ "$safe_rounds" -gt 1 ]; then
      sig_result="$(interrogation_material_signature "$source_path")"
      IFS='|' read -r sig_hash sig_critic sig_designer <<<"$sig_result"
      if [ -n "$previous_material_sig" ] && [ "$sig_hash" != "none" ] && [ "$sig_hash" = "$previous_material_sig" ]; then
        no_delta_streak=$(( no_delta_streak + 1 ))
      else
        no_delta_streak=0
      fi
      previous_material_sig="$sig_hash"

      if [ "$no_delta_streak" -ge 1 ] && [ "$round" -lt "$safe_rounds" ]; then
        early_stop_reason="no-material-delta"
        append_research_event "INTERROGATION_EARLY_STOP" "target=$target reason=$early_stop_reason round=$round/$safe_rounds consecutive_rounds=$(( no_delta_streak + 1 )) source=${source_path:-none} critic=${sig_critic:-none} designer=${sig_designer:-none} prefer_early=true"
        log "Interrogation early stop: target=$target round=$round/$safe_rounds reason=$early_stop_reason (prefer early stop over speculative continuation)"
        break
      fi
    fi

    round=$(( round + 1 ))
  done

  write_interrogation_state "$target" "critic" "$completed_round" "$safe_rounds" "complete"
  append_research_event "INTERROGATION_COMPLETE" "target=$target rounds=$safe_rounds rounds_executed=$completed_round early_stop_reason=$early_stop_reason"
  clear_interrogation_context_env
  return 0
}

list_queue_specs_sorted() {
  find "$QUEUE_SPECS_DIR" -maxdepth 1 -type f ! -name '.gitkeep' | sort
}

list_reviewed_specs_sorted() {
  find "$REVIEWED_SPECS_DIR" -maxdepth 1 -type f ! -name '.gitkeep' | sort
}

spec_file_spec_id() {
  local spec_path="$1"
  python3 - "$spec_path" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8", errors="replace")
spec_id = ""
if text.startswith("---\n"):
    end = text.find("\n---\n", 4)
    if end != -1:
        for raw in text[4:end].splitlines():
            if ":" not in raw:
                continue
            key, value = raw.split(":", 1)
            if key.strip() == "spec_id":
                spec_id = value.strip()
                break
if not spec_id:
    spec_id = path.name.split("__", 1)[0].strip() or path.stem
print(spec_id)
PY
}

spec_family_state_exists() {
  [ -f "$SPEC_FAMILY_STATE_FILE" ]
}

ensure_spec_family_state_initialized() {
  local source_path="$1"
  python3 "$SPEC_FAMILY_STATE_TOOL" init \
    --state "$SPEC_FAMILY_STATE_FILE" \
    --goal-file "$source_path" \
    --source-idea-path "$source_path" >/dev/null
}

read_spec_family_complete_flag() {
  python3 - "$SPEC_FAMILY_STATE_FILE" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(1)
data = json.loads(path.read_text(encoding="utf-8"))
print("on" if bool(data.get("family_complete")) else "off")
PY
}

spec_family_all_specs_decomposed() {
  python3 - "$SPEC_FAMILY_STATE_FILE" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(1)
data = json.loads(path.read_text(encoding="utf-8"))
specs = data.get("specs", {})
if not isinstance(specs, dict) or not specs:
    raise SystemExit(1)
for payload in specs.values():
    if not isinstance(payload, dict):
        raise SystemExit(1)
    if str(payload.get("status", "")).strip() != "decomposed":
        raise SystemExit(1)
raise SystemExit(0)
PY
}

spec_family_ready_for_final_merge() {
  local guard_rc=0
  spec_family_state_exists || return 1
  [ "$(read_spec_family_complete_flag 2>/dev/null || printf 'off')" = "on" ] || return 1
  spec_family_all_specs_decomposed || return 1
  enforce_initial_family_plan_runtime "" "" "final-merge" "off" || guard_rc=$?
  case "$guard_rc" in
    0) ;;
    *) return 2 ;;
  esac
  return 0
}

assemble_pending_family_runtime() {
  if ! python3 "$ASSEMBLE_PENDING_FAMILY_TOOL" \
    "$TASKS_PENDING_SHARDS_DIR" \
    "$TASKS_PENDING_FILE" \
    --state "$SPEC_FAMILY_STATE_FILE" >/dev/null; then
    write_research_status "### BLOCKED"
    log "GoalSpec mode: failed to assemble pending family into $TASKS_PENDING_FILE"
    return 1
  fi
  if ! taskspending_has_real_cards; then
    write_research_status "### BLOCKED"
    log "GoalSpec mode: family assembly completed but produced no real pending cards in $TASKS_PENDING_FILE"
    return 1
  fi
  append_research_event "PENDING_FAMILY_ASSEMBLED" "pending=$TASKS_PENDING_FILE shards_dir=$TASKS_PENDING_SHARDS_DIR"
  log "GoalSpec mode: assembled pending family into $TASKS_PENDING_FILE"
  return 0
}

apply_family_governor_runtime() {
  local source_path="$1"
  local trigger_spec_path="$2"
  local trigger_spec_id summary_json parsed
  local enabled budget_hit active_spec_count active_planned_count
  local family_complete_before family_complete_after
  local deferred_count deferred_ids registry_path family_phase applied_family_max_specs initial_family_max_specs remediation_family_max_specs

  [ -f "$FAMILY_POLICY_FILE" ] || return 0

  if [ ! -f "$FAMILY_GOVERNOR_TOOL" ]; then
    write_research_status "### BLOCKED"
    log "GoalSpec mode: family policy exists but governor tool is missing: $FAMILY_GOVERNOR_TOOL"
    return 1
  fi

  trigger_spec_id="$(spec_file_spec_id "$trigger_spec_path")"
  if ! summary_json="$(
    python3 "$FAMILY_GOVERNOR_TOOL" \
      --state "$SPEC_FAMILY_STATE_FILE" \
      --policy "$FAMILY_POLICY_FILE" \
      --trigger-spec-id "$trigger_spec_id" \
      --source-idea-path "$source_path" \
      --json
  )"; then
    write_research_status "### BLOCKED"
    log "GoalSpec mode: family governor failed for trigger spec $trigger_spec_id"
    return 1
  fi

  if ! parsed="$(parse_family_governor_summary_pairs "$summary_json")"; then
    write_research_status "### BLOCKED"
    log "GoalSpec mode: unable to parse family governor summary for $trigger_spec_id"
    return 1
  fi
  # shellcheck disable=SC2086
  eval "$parsed"

  if [ "$enabled" != "true" ]; then
    return 0
  fi

  if [ "${deferred_count:-0}" -gt 0 ]; then
    append_research_event "SPEC_FAMILY_OVERFLOW_DEFERRED" \
      "trigger_spec_id=$trigger_spec_id family_phase=$family_phase deferred_count=$deferred_count deferred_ids=$deferred_ids registry=$registry_path max_specs=$applied_family_max_specs"
    log "GoalSpec mode: deferred overflow follow-ons after $trigger_spec_id (family_phase=$family_phase count=$deferred_count ids=$deferred_ids max_specs=$applied_family_max_specs)"
  fi

  if [ "$budget_hit" = "true" ] && { [ "${deferred_count:-0}" -gt 0 ] || [ "$family_complete_before" != "$family_complete_after" ]; }; then
    append_research_event "SPEC_FAMILY_BUDGET_HIT" \
      "trigger_spec_id=$trigger_spec_id family_phase=$family_phase max_specs=$applied_family_max_specs active_specs=$active_spec_count active_planned=$active_planned_count family_complete=$family_complete_after"
    log "GoalSpec mode: bounded family budget hit after $trigger_spec_id (family_phase=$family_phase max_specs=$applied_family_max_specs active_specs=$active_spec_count active_planned=$active_planned_count family_complete=$family_complete_after)"
  fi

  return 0
}

parse_family_governor_summary_pairs() {
  local summary_json="$1"

  python3 - "$summary_json" <<'PY'
import json
import shlex
import sys

d = json.loads(sys.argv[1])
pairs = {
    "enabled": "true" if d.get("enabled") else "false",
    "budget_hit": "true" if d.get("budget_hit") else "false",
    "family_phase": d.get("family_phase", "initial_family"),
    "active_spec_count": d.get("active_spec_count", 0),
    "active_planned_count": d.get("active_planned_count", 0),
    "family_complete_before": "on" if d.get("family_complete_before") else "off",
    "family_complete_after": "on" if d.get("family_complete_after") else "off",
    "deferred_count": d.get("deferred_count", 0),
    "deferred_ids": ",".join(
        str(item).strip() for item in d.get("deferred_spec_ids", []) if str(item).strip()
    )
    or "none",
    "registry_path": d.get("overflow_registry_path", ""),
    "initial_family_max_specs": d.get("initial_family_max_specs", 0),
    "remediation_family_max_specs": d.get("remediation_family_max_specs", 0),
    "applied_family_max_specs": d.get("applied_family_max_specs", 0),
}
for key, value in pairs.items():
    print(f"{key}={shlex.quote(str(value))}")
PY
}

parse_initial_family_plan_guard_summary_pairs() {
  local summary_json="$1"

  python3 - "$summary_json" <<'PY'
import json
import shlex
import sys

d = json.loads(sys.argv[1])
pairs = {
    "INITIAL_FAMILY_PLAN_GUARD_ACTION": d.get("action", ""),
    "INITIAL_FAMILY_PLAN_GUARD_REASON": d.get("reason", ""),
    "INITIAL_FAMILY_PLAN_GUARD_FROZEN": "true" if d.get("frozen") else "false",
    "INITIAL_FAMILY_PLAN_GUARD_FREEZE_MODE": d.get("freeze_mode", ""),
    "INITIAL_FAMILY_PLAN_GUARD_FAMILY_COMPLETE": "on" if d.get("family_complete") else "off",
    "INITIAL_FAMILY_PLAN_GUARD_SPEC_COUNT": d.get("spec_count", 0),
    "INITIAL_FAMILY_PLAN_GUARD_TRIGGER_SPEC_ID": d.get("trigger_spec_id", ""),
    "INITIAL_FAMILY_PLAN_GUARD_GOAL_PATH": d.get("goal_path", ""),
    "INITIAL_FAMILY_PLAN_GUARD_POLICY_PATH": d.get("policy_path", ""),
    "INITIAL_FAMILY_PLAN_GUARD_LIVE_POLICY_SHA256": d.get("live_policy_sha256", ""),
    "INITIAL_FAMILY_PLAN_GUARD_FROZEN_POLICY_SHA256": d.get("frozen_policy_sha256", ""),
    "INITIAL_FAMILY_PLAN_GUARD_ADDED_SPEC_IDS": ",".join(
        str(item).strip() for item in d.get("added_spec_ids", []) if str(item).strip()
    )
    or "none",
    "INITIAL_FAMILY_PLAN_GUARD_REMOVED_SPEC_IDS": ",".join(
        str(item).strip() for item in d.get("removed_spec_ids", []) if str(item).strip()
    )
    or "none",
    "INITIAL_FAMILY_PLAN_GUARD_MUTATED_SPEC_IDS": ",".join(
        str(item).strip() for item in d.get("mutated_spec_ids", []) if str(item).strip()
    )
    or "none",
    "INITIAL_FAMILY_PLAN_GUARD_VIOLATION_CODES": ",".join(
        str(item).strip() for item in d.get("violation_codes", []) if str(item).strip()
    )
    or "none",
}
for key, value in pairs.items():
    print(f"{key}={shlex.quote(str(value))}")
PY
}

evaluate_initial_family_plan_guard_runtime() {
  local goal_path="${1:-}"
  local trigger_spec_id="${2:-}"
  local summary_json parsed
  local cmd=(python3 "$INITIAL_FAMILY_PLAN_GUARD_TOOL" --state "$SPEC_FAMILY_STATE_FILE" --policy "$FAMILY_POLICY_FILE" --trigger-spec-id "$trigger_spec_id" --json)

  if [ ! -f "$INITIAL_FAMILY_PLAN_GUARD_TOOL" ]; then
    write_research_status "### BLOCKED"
    log "GoalSpec mode: initial family plan guard tool is missing: $INITIAL_FAMILY_PLAN_GUARD_TOOL"
    return 1
  fi

  if [ -n "$goal_path" ]; then
    cmd+=(--goal-file "$goal_path")
  fi

  if ! summary_json="$("${cmd[@]}")"; then
    write_research_status "### BLOCKED"
    log "GoalSpec mode: initial family plan guard execution failed for state $SPEC_FAMILY_STATE_FILE"
    return 1
  fi

  if ! parsed="$(parse_initial_family_plan_guard_summary_pairs "$summary_json")"; then
    write_research_status "### BLOCKED"
    log "GoalSpec mode: unable to parse initial family plan guard summary"
    return 1
  fi

  # shellcheck disable=SC2086
  eval "$parsed"
  return 0
}

enforce_initial_family_plan_runtime() {
  local goal_path="${1:-}"
  local trigger_spec_id="${2:-}"
  local context="${3:-runtime}"
  local emit_success_events="${4:-on}"

  if ! evaluate_initial_family_plan_guard_runtime "$goal_path" "$trigger_spec_id"; then
    return 2
  fi

  case "$INITIAL_FAMILY_PLAN_GUARD_ACTION" in
    freeze)
      if [ "$emit_success_events" = "on" ]; then
        append_research_event "INITIAL_FAMILY_PLAN_FROZEN" \
          "context=$context trigger_spec_id=${INITIAL_FAMILY_PLAN_GUARD_TRIGGER_SPEC_ID:-$trigger_spec_id} goal=${INITIAL_FAMILY_PLAN_GUARD_GOAL_PATH:-$goal_path} spec_count=$INITIAL_FAMILY_PLAN_GUARD_SPEC_COUNT freeze_mode=$INITIAL_FAMILY_PLAN_GUARD_FREEZE_MODE"
      fi
      log "GoalSpec mode: initial family plan frozen (context=$context trigger_spec_id=${INITIAL_FAMILY_PLAN_GUARD_TRIGGER_SPEC_ID:-$trigger_spec_id} spec_count=$INITIAL_FAMILY_PLAN_GUARD_SPEC_COUNT)"
      return 0
      ;;
    validate)
      if [ "$emit_success_events" = "on" ] && [ "$INITIAL_FAMILY_PLAN_GUARD_FROZEN" = "true" ] && [ "$INITIAL_FAMILY_PLAN_GUARD_REASON" != "non-initial-family-phase" ]; then
        append_research_event "INITIAL_FAMILY_PLAN_VALIDATED" \
          "context=$context trigger_spec_id=${INITIAL_FAMILY_PLAN_GUARD_TRIGGER_SPEC_ID:-$trigger_spec_id} reason=$INITIAL_FAMILY_PLAN_GUARD_REASON family_complete=$INITIAL_FAMILY_PLAN_GUARD_FAMILY_COMPLETE spec_count=$INITIAL_FAMILY_PLAN_GUARD_SPEC_COUNT"
      fi
      return 0
      ;;
    block)
      write_research_status "### BLOCKED"
      append_research_event "INITIAL_FAMILY_PLAN_BLOCKED" \
        "context=$context trigger_spec_id=${INITIAL_FAMILY_PLAN_GUARD_TRIGGER_SPEC_ID:-$trigger_spec_id} goal=$INITIAL_FAMILY_PLAN_GUARD_GOAL_PATH policy=$INITIAL_FAMILY_PLAN_GUARD_POLICY_PATH live_policy_sha256=$INITIAL_FAMILY_PLAN_GUARD_LIVE_POLICY_SHA256 frozen_policy_sha256=$INITIAL_FAMILY_PLAN_GUARD_FROZEN_POLICY_SHA256 added=$INITIAL_FAMILY_PLAN_GUARD_ADDED_SPEC_IDS removed=$INITIAL_FAMILY_PLAN_GUARD_REMOVED_SPEC_IDS mutated=$INITIAL_FAMILY_PLAN_GUARD_MUTATED_SPEC_IDS violations=$INITIAL_FAMILY_PLAN_GUARD_VIOLATION_CODES"
      log "GoalSpec mode: initial family plan guard blocked drift (context=$context reason=$INITIAL_FAMILY_PLAN_GUARD_REASON added=$INITIAL_FAMILY_PLAN_GUARD_ADDED_SPEC_IDS removed=$INITIAL_FAMILY_PLAN_GUARD_REMOVED_SPEC_IDS mutated=$INITIAL_FAMILY_PLAN_GUARD_MUTATED_SPEC_IDS violations=$INITIAL_FAMILY_PLAN_GUARD_VIOLATION_CODES)"
      return 2
      ;;
    *)
      write_research_status "### BLOCKED"
      log "GoalSpec mode: initial family plan guard returned unsupported action: $INITIAL_FAMILY_PLAN_GUARD_ACTION"
      return 2
      ;;
  esac
}

set_markdown_frontmatter_status() {
  local path="$1"
  local status="$2"
  python3 - "$path" "$status" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
status_value = sys.argv[2]
text = path.read_text(encoding="utf-8", errors="replace")
if not text.startswith("---\n"):
    raise SystemExit(0)
end = text.find("\n---\n", 4)
if end == -1:
    raise SystemExit(0)
frontmatter = text[4:end].splitlines()
body = text[end + 5:]
updated = []
saw_status = False
for raw in frontmatter:
    if raw.startswith("status:"):
        updated.append(f"status: {status_value}")
        saw_status = True
    else:
        updated.append(raw)
if not saw_status:
    updated.append(f"status: {status_value}")
path.write_text("---\n" + "\n".join(updated) + "\n---\n" + body, encoding="utf-8")
PY
}

run_taskmaster_with_overrides() {
  local source_spec_path="${1:-}"
  local output_path="${2:-}"
  local rc=0
  local restore_source="__UNSET__"
  local restore_output="__UNSET__"

  if [ "${TASKMASTER_SOURCE_SPEC_PATH+x}" = "x" ]; then
    restore_source="$TASKMASTER_SOURCE_SPEC_PATH"
  fi
  if [ "${TASKMASTER_OUTPUT_SHARD_PATH+x}" = "x" ]; then
    restore_output="$TASKMASTER_OUTPUT_SHARD_PATH"
  fi

  if [ -n "$source_spec_path" ]; then
    export TASKMASTER_SOURCE_SPEC_PATH="$source_spec_path"
  else
    unset TASKMASTER_SOURCE_SPEC_PATH || true
  fi

  if [ -n "$output_path" ]; then
    mkdir -p "$(dirname "$output_path")"
    export TASKMASTER_OUTPUT_SHARD_PATH="$output_path"
  else
    unset TASKMASTER_OUTPUT_SHARD_PATH || true
  fi

  run_stage taskmaster || rc=$?

  if [ "$restore_source" = "__UNSET__" ]; then
    unset TASKMASTER_SOURCE_SPEC_PATH || true
  else
    export TASKMASTER_SOURCE_SPEC_PATH="$restore_source"
  fi
  if [ "$restore_output" = "__UNSET__" ]; then
    unset TASKMASTER_OUTPUT_SHARD_PATH || true
  else
    export TASKMASTER_OUTPUT_SHARD_PATH="$restore_output"
  fi

  return "$rc"
}

run_goalspec_idempotency_guard() {
  local artifact_kind="$1"
  local spec_path="$2"
  local candidate_shard_path="${3:-}"

  [ -f "$SPEC_FAMILY_STATE_FILE" ] || return 2

  if [ ! -f "$GOALSPEC_IDEMPOTENCY_GUARD_TOOL" ]; then
    write_research_status "### BLOCKED"
    log "GoalSpec mode: idempotency guard tool is missing: $GOALSPEC_IDEMPOTENCY_GUARD_TOOL"
    return 1
  fi

  if [ -n "$candidate_shard_path" ]; then
    python3 "$GOALSPEC_IDEMPOTENCY_GUARD_TOOL" \
      --state "$SPEC_FAMILY_STATE_FILE" \
      --artifact-kind "$artifact_kind" \
      --spec-file "$spec_path" \
      --candidate-shard-path "$candidate_shard_path" \
      --json
  else
    python3 "$GOALSPEC_IDEMPOTENCY_GUARD_TOOL" \
      --state "$SPEC_FAMILY_STATE_FILE" \
      --artifact-kind "$artifact_kind" \
      --spec-file "$spec_path" \
      --json
  fi
}

apply_goalspec_idempotency_guard() {
  local artifact_kind="$1"
  local spec_path="$2"
  local candidate_shard_path="${3:-}"
  local summary_json parsed
  local action reason spec_id state_status cleanup_paths
  local authoritative_reviewed authoritative_shard
  local cleanup_path=""
  local -a cleanup_items=()

  if ! summary_json="$(run_goalspec_idempotency_guard "$artifact_kind" "$spec_path" "$candidate_shard_path" 2>/dev/null)"; then
    case "$?" in
      2)
        return 0
        ;;
      *)
        write_research_status "### BLOCKED"
        log "GoalSpec mode: idempotency guard failed for $artifact_kind artifact $spec_path"
        return 1
        ;;
    esac
  fi

  if ! parsed="$(python3 - "$summary_json" <<'PY'
import json
import shlex
import sys

payload = json.loads(sys.argv[1])
cleanup = [str(item).strip() for item in payload.get("cleanup_paths", []) if str(item).strip()]
authoritative = payload.get("authoritative_paths", {})
if not isinstance(authoritative, dict):
    authoritative = {}
pairs = {
    "action": payload.get("action", "proceed"),
    "reason": payload.get("reason", "unknown"),
    "spec_id": payload.get("spec_id", ""),
    "state_status": payload.get("state_status", ""),
    "cleanup_paths": "::".join(cleanup),
    "authoritative_reviewed": str(authoritative.get("reviewed_path", "")).strip(),
    "authoritative_shard": str(authoritative.get("pending_shard_path", "")).strip(),
}
for key, value in pairs.items():
    print(f"{key}={shlex.quote(str(value))}")
PY
  )"; then
    write_research_status "### BLOCKED"
    log "GoalSpec mode: unable to parse idempotency guard summary for $artifact_kind artifact $spec_path"
    return 1
  fi
  # shellcheck disable=SC2086
  eval "$parsed"

  case "$action" in
    proceed)
      return 0
      ;;
    noop_cleanup)
      if [ -n "${cleanup_paths:-}" ]; then
        IFS='::' read -r -a cleanup_items <<<"$cleanup_paths"
        for cleanup_path in "${cleanup_items[@]}"; do
          [ -n "$cleanup_path" ] || continue
          if [ -f "$cleanup_path" ]; then
            rm -f -- "$cleanup_path"
          fi
        done
      fi
      append_research_event "GOALSPEC_RETRY_NOOP" \
        "artifact_kind=$artifact_kind spec_id=$spec_id state_status=$state_status reason=$reason cleanup=${cleanup_paths:-none} reviewed=${authoritative_reviewed:-none} shard=${authoritative_shard:-none}"
      log "GoalSpec mode: idempotent no-op for stale $artifact_kind artifact $spec_path (spec_id=$spec_id status=$state_status reason=$reason)"
      return 10
      ;;
    block_inconsistent)
      write_research_status "### BLOCKED"
      append_research_event "GOALSPEC_RETRY_INCONSISTENT" \
        "artifact_kind=$artifact_kind spec_id=$spec_id state_status=$state_status reason=$reason reviewed=${authoritative_reviewed:-none} shard=${authoritative_shard:-none}"
      log "GoalSpec mode: inconsistent retry artifact $spec_path (artifact_kind=$artifact_kind spec_id=$spec_id status=$state_status reason=$reason)"
      return 1
      ;;
    *)
      write_research_status "### BLOCKED"
      log "GoalSpec mode: idempotency guard returned unknown action=$action for $spec_path"
      return 1
      ;;
  esac
}

resolve_goalspec_source_idea_path_from_state() {
  python3 - "$SPEC_FAMILY_STATE_FILE" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(1)
data = json.loads(path.read_text(encoding="utf-8"))
goal_path = str(data.get("source_idea_path", "")).strip()
if not goal_path:
    raise SystemExit(1)
print(goal_path)
PY
}

finish_goalspec_source_runtime() {
  local source_path="${1:-}"
  local finished_path moved_path

  if [ -z "$source_path" ]; then
    source_path="$(resolve_goalspec_source_idea_path_from_state 2>/dev/null || true)"
  fi

  if [ -z "$source_path" ]; then
    write_research_status "### BLOCKED"
    log "GoalSpec mode: family marked complete but source idea path could not be resolved from $SPEC_FAMILY_STATE_FILE"
    return 1
  fi

  finished_path="$IDEAS_FINISHED_DIR/$(basename "$source_path")"
  if [ -f "$source_path" ]; then
    set_markdown_frontmatter_status "$source_path" "finished" || true
    moved_path="$(move_payload_file_path "$source_path" "$IDEAS_FINISHED_DIR" || true)"
    if [ -z "$moved_path" ]; then
      write_research_status "### BLOCKED"
      log "GoalSpec mode: failed to move completed source idea to finished: $source_path"
      return 1
    fi
    append_research_event "SPEC_FAMILY_SOURCE_FINISHED" "source=$source_path moved_to=$moved_path"
    return 0
  fi

  if [ -f "$finished_path" ]; then
    set_markdown_frontmatter_status "$finished_path" "finished" || true
    return 0
  fi

  write_research_status "### BLOCKED"
  log "GoalSpec mode: family marked complete but source idea is missing from staging/finished: $source_path"
  return 1
}

finalize_spec_synthesis_runtime() {
  local source_path="$1"
  local emitted_spec_path="$2"
  local family_complete trigger_spec_id

  if ! spec_family_state_exists; then
    write_research_status "### BLOCKED"
    log "GoalSpec mode: spec_synthesis must create family state before runtime finalization: $SPEC_FAMILY_STATE_FILE"
    return 1
  fi

  ensure_spec_family_state_initialized "$source_path" || {
    write_research_status "### BLOCKED"
    log "GoalSpec mode: failed to initialize spec family state for $source_path"
    return 1
  }

  if ! python3 "$SPEC_FAMILY_STATE_TOOL" upsert-spec \
    --state "$SPEC_FAMILY_STATE_FILE" \
    --spec-file "$emitted_spec_path" \
    --status emitted \
    --queue-path "$emitted_spec_path" \
    --set-active >/dev/null; then
    write_research_status "### BLOCKED"
    log "GoalSpec mode: failed to record emitted spec in family state: $emitted_spec_path"
    return 1
  fi

  if ! apply_family_governor_runtime "$source_path" "$emitted_spec_path"; then
    return 1
  fi

  trigger_spec_id="$(spec_file_spec_id "$emitted_spec_path")"
  if ! enforce_initial_family_plan_runtime "$source_path" "$trigger_spec_id" "spec-synthesis" "on"; then
    return 1
  fi

  if ! family_complete="$(read_spec_family_complete_flag)"; then
    write_research_status "### BLOCKED"
    log "GoalSpec mode: unable to read family_complete from $SPEC_FAMILY_STATE_FILE"
    return 1
  fi

  if [ "$family_complete" = "off" ]; then
    if [ ! -f "$source_path" ]; then
      write_research_status "### BLOCKED"
      log "GoalSpec mode: family remains open but staged idea disappeared: $source_path"
      return 1
    fi
    return 0
  fi

  finish_goalspec_source_runtime "$source_path"
}

promote_reviewed_spec_runtime() {
  local queue_spec_path="$1"
  local spec_id reviewed_path
  local guard_rc=0

  if ! spec_family_state_exists; then
    write_research_status "### BLOCKED"
    log "GoalSpec mode: reviewed spec cannot be promoted without family state: $SPEC_FAMILY_STATE_FILE"
    return 1
  fi

  apply_goalspec_idempotency_guard queue "$queue_spec_path" || guard_rc=$?
  case "$guard_rc" in
    0) ;;
    10) return 0 ;;
    *) return 1 ;;
  esac

  spec_id="$(spec_file_spec_id "$queue_spec_path")"
  reviewed_path="$(move_payload_file_path "$queue_spec_path" "$REVIEWED_SPECS_DIR" || true)"
  if [ -z "$reviewed_path" ]; then
    write_research_status "### BLOCKED"
    log "GoalSpec mode: failed to move reviewed spec into $REVIEWED_SPECS_DIR: $queue_spec_path"
    return 1
  fi

  if ! python3 "$SPEC_FAMILY_STATE_TOOL" upsert-spec \
    --state "$SPEC_FAMILY_STATE_FILE" \
    --spec-file "$reviewed_path" \
    --status reviewed \
    --reviewed-path "$reviewed_path" \
    --clear-queue-path \
    --set-active >/dev/null; then
    write_research_status "### BLOCKED"
    log "GoalSpec mode: failed to record reviewed spec in family state: $reviewed_path"
    return 1
  fi

  append_research_event "SPEC_REVIEWED" "spec_id=$spec_id reviewed=$reviewed_path"
  log "GoalSpec mode: reviewed one spec and promoted it to $REVIEWED_SPECS_DIR: $reviewed_path"
  return 0
}

finalize_taskmaster_runtime() {
  local reviewed_spec_path="$1"
  local shard_path="$2"
  local spec_id archived_path family_complete
  local guard_rc=0

  if ! spec_family_state_exists; then
    write_research_status "### BLOCKED"
    log "GoalSpec mode: decomposed spec cannot be finalized without family state: $SPEC_FAMILY_STATE_FILE"
    return 1
  fi

  apply_goalspec_idempotency_guard reviewed "$reviewed_spec_path" "$shard_path" || guard_rc=$?
  case "$guard_rc" in
    0) ;;
    10) return 0 ;;
    *) return 1 ;;
  esac

  spec_id="$(spec_file_spec_id "$reviewed_spec_path")"
  archived_path="$(move_payload_file_path "$reviewed_spec_path" "$IDEAS_ARCHIVED_DIR" || true)"
  if [ -z "$archived_path" ]; then
    write_research_status "### BLOCKED"
    log "GoalSpec mode: failed to archive decomposed reviewed spec: $reviewed_spec_path"
    return 1
  fi

  if ! python3 "$SPEC_FAMILY_STATE_TOOL" upsert-spec \
    --state "$SPEC_FAMILY_STATE_FILE" \
    --spec-file "$archived_path" \
    --status decomposed \
    --archived-path "$archived_path" \
    --pending-shard-path "$shard_path" \
    --clear-reviewed-path \
    --set-active >/dev/null; then
    write_research_status "### BLOCKED"
    log "GoalSpec mode: failed to record decomposed spec in family state: $archived_path"
    return 1
  fi

  if ! enforce_initial_family_plan_runtime "" "$spec_id" "taskmaster" "on"; then
    return 1
  fi

  if ! family_complete="$(read_spec_family_complete_flag)"; then
    write_research_status "### BLOCKED"
    log "GoalSpec mode: unable to read family_complete after taskmaster for $archived_path"
    return 1
  fi

  if [ "$family_complete" = "on" ]; then
    if ! finish_goalspec_source_runtime; then
      return 1
    fi
  fi

  if [ "$family_complete" = "off" ] && \
    ! dir_has_payload_files "$STAGING_DIR" && \
    ! dir_has_payload_files "$QUEUE_SPECS_DIR" && \
    ! dir_has_payload_files "$REVIEWED_SPECS_DIR"; then
    write_research_status "### BLOCKED"
    log "GoalSpec mode: family remains open but no staged/spec work remains after decomposing $spec_id"
    return 1
  fi

  append_research_event "SPEC_DECOMPOSED" "spec_id=$spec_id archived=$archived_path shard=$shard_path family_complete=$family_complete"
  log "GoalSpec mode: decomposed one reviewed spec into shard $shard_path and archived $archived_path"
  return 0
}

evaluate_spec_governance_gate() {
  local queue_spec="$1"
  local governance_goal_path=""
  local result_json parsed
  local spec_id score threshold passed reasons fail_count version version_bumped phase00_emitted
  local decomp_json decomp_parsed decomp_ok decomp_errors

  if [ ! -f "$queue_spec" ]; then
    log "Governance gate: missing queue spec $queue_spec"
    return 1
  fi

  governance_goal_path="$(resolve_authoritative_goal_path || true)"
  if ! result_json="$(python3 - \
    "$queue_spec" "$governance_goal_path" "$SPECS_STABLE_GOLDEN_DIR" "$SPECS_STABLE_PHASE_DIR" \
    "$SPEC_QUALITY_THRESHOLD" "$PHASE_ASSUMPTIONS_BUDGET" "$SPEC_QUALITY_FAIL_MAX" \
    "$GOLDEN_VERSION_REGISTRY" "$SPEC_QUALITY_STATE" "$(utc_now_iso)" <<'PY'
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
import sys

queue_spec = Path(sys.argv[1])
goal_path_raw = str(sys.argv[2]).strip()
goal_file = Path(goal_path_raw) if goal_path_raw else None
golden_dir = Path(sys.argv[3])
phase_dir = Path(sys.argv[4])
threshold = float(sys.argv[5])
assumptions_budget = int(sys.argv[6])
failure_limit = int(sys.argv[7])
version_registry_path = Path(sys.argv[8])
quality_state_path = Path(sys.argv[9])
now = sys.argv[10]

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")

def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return dict(default)
    try:
        value = json.loads(read_text(path))
        if isinstance(value, dict):
            return value
    except Exception:
        pass
    return dict(default)

def parse_frontmatter(text: str) -> dict:
    m = re.match(r"(?s)^---\s*\n(.*?)\n---\s*\n?", text)
    if not m:
        return {}
    out = {}
    for raw in m.group(1).splitlines():
        if ":" not in raw:
            continue
        k, v = raw.split(":", 1)
        out[k.strip()] = v.strip()
    return out

def parse_spec_id(path: Path, text: str) -> str:
    fm = parse_frontmatter(text)
    if fm.get("spec_id"):
        return fm["spec_id"]
    m = re.match(r"^(SPEC-[0-9]+)__", path.name, flags=re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return path.stem.split("__", 1)[0].upper()

def has_section(text: str, title: str) -> bool:
    pattern = re.compile(rf"(?mi)^##\s+{re.escape(title)}\s*$")
    return bool(pattern.search(text))

def assumptions_count(text: str) -> int:
    m = re.search(r"(?ims)^##\s+Assumptions Ledger\s*\n(.*?)(?:\n##\s+|\Z)", text)
    if not m:
        return 0
    count = 0
    for line in m.group(1).splitlines():
        if re.match(r"^\s*[-*]\s+\S", line):
            count += 1
    return count

queue_text = read_text(queue_spec)
spec_id = parse_spec_id(queue_spec, queue_text)
spec_title = parse_frontmatter(queue_text).get("title") or queue_spec.stem

golden_files = sorted(golden_dir.glob(f"{spec_id}__*.md"))
phase_files = sorted(phase_dir.glob(f"{spec_id}__*.md"))
golden_text = read_text(golden_files[0]) if golden_files else ""
phase_texts = [read_text(p) for p in phase_files]

checks = []
reasons = []

def add_check(name: str, ok: bool, fail_reason: str) -> None:
    checks.append((name, ok))
    if not ok:
        reasons.append(fail_reason)

add_check("golden_exists", bool(golden_files), "missing stable golden spec")
add_check("phase_exists", bool(phase_files), "missing stable phase spec")
add_check(
    "golden_reqid_matrix",
    bool(golden_text) and has_section(golden_text, "Requirements Traceability (Req-ID Matrix)"),
    "golden spec missing Req-ID matrix section",
)
add_check(
    "golden_assumptions",
    bool(golden_text) and has_section(golden_text, "Assumptions Ledger"),
    "golden spec missing assumptions ledger section",
)
add_check(
    "golden_decision_log",
    bool(golden_text) and has_section(golden_text, "Structured Decision Log"),
    "golden spec missing structured decision log section",
)

has_phase_files = bool(phase_files)
all_phase_keys = has_phase_files
all_phase_priorities = has_phase_files
all_phase_reqid = has_phase_files
all_phase_decision_log = has_phase_files
all_phase_assumptions_budget_ok = has_phase_files
over_budget_counts = []

for phase_file, phase_text in zip(phase_files, phase_texts):
    fm = parse_frontmatter(phase_text)
    phase_key = (fm.get("phase_key") or "").strip()
    phase_priority = (fm.get("phase_priority") or "").strip().upper()
    if not re.match(r"^PHASE_[0-9]{2}$", phase_key):
        all_phase_keys = False
    if phase_priority not in {"P0", "P1", "P2", "P3"}:
        all_phase_priorities = False
    if not has_section(phase_text, "Requirements Traceability (Req-ID)"):
        all_phase_reqid = False
    if not has_section(phase_text, "Structured Decision Log"):
        all_phase_decision_log = False
    count = assumptions_count(phase_text)
    if count > assumptions_budget:
        all_phase_assumptions_budget_ok = False
        over_budget_counts.append(f"{phase_file.name}:{count}")

add_check("phase_keys", all_phase_keys, "one or more phase specs missing PHASE_<nn> key")
add_check("phase_priorities", all_phase_priorities, "one or more phase specs missing phase_priority P0-P3")
add_check("phase_reqid", all_phase_reqid, "one or more phase specs missing Req-ID section")
add_check("phase_decision_log", all_phase_decision_log, "one or more phase specs missing structured decision log section")
add_check(
    "phase_assumptions_budget",
    all_phase_assumptions_budget_ok,
    "phase assumptions budget exceeded: " + (", ".join(over_budget_counts) if over_budget_counts else "unknown"),
)

score = 0.0
if checks:
    passed_count = sum(1 for _, ok in checks if ok)
    score = round(passed_count / float(len(checks)), 4)
passed = score >= threshold

version_registry = load_json(
    version_registry_path,
    {
        "schema_version": "1.0",
        "updated_at": now,
        "goal_source": {"path": goal_path_raw, "sha256": ""},
        "spec_versions": {},
    },
)
goal_source = version_registry.get("goal_source")
if not isinstance(goal_source, dict):
    legacy_goal = version_registry.get("base_goal")
    if isinstance(legacy_goal, dict):
        goal_source = {
            "path": str(legacy_goal.get("path", "")).strip(),
            "sha256": str(legacy_goal.get("sha256", "")).strip(),
        }
    else:
        goal_source = {"path": "", "sha256": ""}
spec_versions = version_registry.get("spec_versions")
if not isinstance(spec_versions, dict):
    spec_versions = {}
goal_sha = sha256_file(goal_file) if goal_file is not None and goal_file.exists() else ""
entry = spec_versions.get(spec_id, {})
if not isinstance(entry, dict):
    entry = {}
current_version = int(entry.get("version") or 0)
previous_sha = str(entry.get("current_goal_sha256") or "")
if current_version < 1:
    current_version = 1
version_bumped = False
if goal_sha and previous_sha and goal_sha != previous_sha:
    current_version += 1
    version_bumped = True
elif goal_sha and not previous_sha:
    version_bumped = current_version == 1

spec_versions[spec_id] = {
    "version": current_version,
    "current_goal_path": goal_path_raw,
    "current_goal_sha256": goal_sha,
    "updated_at": now,
}
version_registry["schema_version"] = "1.0"
version_registry["updated_at"] = now
version_registry["goal_source"] = {"path": goal_path_raw, "sha256": goal_sha}
version_registry.pop("base_goal", None)
version_registry["spec_versions"] = spec_versions
version_registry_path.parent.mkdir(parents=True, exist_ok=True)
version_registry_path.write_text(json.dumps(version_registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")

quality_state = load_json(
    quality_state_path,
    {
        "schema_version": "1.0",
        "updated_at": now,
        "threshold": threshold,
        "phase_assumptions_budget": assumptions_budget,
        "failure_limit": failure_limit,
        "specs": {},
    },
)
spec_map = quality_state.get("specs")
if not isinstance(spec_map, dict):
    spec_map = {}
state_entry = spec_map.get(spec_id, {})
if not isinstance(state_entry, dict):
    state_entry = {}
consecutive_failures = int(state_entry.get("consecutive_failures") or 0)
if passed:
    consecutive_failures = 0
else:
    consecutive_failures += 1

phase00_emitted = False
if (not passed) and consecutive_failures >= failure_limit:
    phase00_path = phase_dir / f"{spec_id}__phase-00.md"
    if not phase00_path.exists():
        phase00_path.write_text(
            "\n".join(
                [
                    "---",
                    "phase_id: PHASE-0000",
                    "phase_key: PHASE_00",
                    "phase_priority: P0",
                    f"parent_spec_id: {spec_id}",
                    f"title: Scope-shrink fallback for {spec_title}",
                    "status: planned",
                    "owner: research-loop",
                    f"created_at: {now}",
                    f"updated_at: {now}",
                    "---",
                    "",
                    "## Objective",
                    "- Shrink scope to a smallest-passable delivery slice that can satisfy governance gates.",
                    "",
                    "## Entry Criteria",
                    f"- Quality score remains below threshold `{threshold}` after repeated attempts.",
                    "",
                    "## Scope",
                    "### In Scope",
                    "- Remove optional work and keep only must-have requirements for the first safe delivery.",
                    "",
                    "### Out of Scope",
                    "- Any additive capability not required for immediate quality compliance.",
                    "",
                    "## Work Plan",
                    "1. Reduce to minimal required requirement set.",
                    "2. Re-validate Req-ID mappings and assumptions budget.",
                    "3. Regenerate phase plan with explicit verification evidence.",
                    "",
                    "## Requirements Traceability (Req-ID)",
                    "- `Req-ID: REQ-000` mapped to PHASE_00 governance recovery scope.",
                    "",
                    "## Assumptions Ledger",
                    f"- Scope reduction is acceptable for governance recovery (confidence: inferred, budget_cap: {assumptions_budget}).",
                    "",
                    "## Structured Decision Log",
                    "| decision_id | phase_key | phase_priority | status | owner | rationale | timestamp |",
                    "| --- | --- | --- | --- | --- | --- | --- |",
                    f"| DEC-PHASE00-001 | PHASE_00 | P0 | accepted | research-loop | scope-shrink fallback after repeated quality failures | {now} |",
                    "",
                    "## Interrogation Notes",
                    "- PHASE_00 created by governance fallback after repeated quality failures.",
                    "",
                    "## Verification",
                    "- Re-run GoalSpec governance gate and confirm score meets threshold.",
                    "",
                    "## Exit Criteria",
                    "- Quality gate passes with assumptions budget within cap.",
                    "",
                    "## Handoff",
                    "- Feed revised PHASE_00 plan into Taskmaster generation once gate passes.",
                    "",
                    "## Risks",
                    "- Risk: reduced scope misses non-critical requirements.",
                    "- Mitigation: schedule deferred items in follow-on phases after governance pass.",
                    "",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        phase00_emitted = True

state_entry.update(
    {
        "last_checked_at": now,
        "last_status": "pass" if passed else "fail",
        "last_score": score,
        "threshold": threshold,
        "consecutive_failures": consecutive_failures,
        "failure_limit": failure_limit,
        "phase_assumptions_budget": assumptions_budget,
        "last_reasons": reasons,
        "phase_00_fallback_emitted": phase00_emitted or bool(state_entry.get("phase_00_fallback_emitted")),
        "latest_golden_path": golden_files[0].as_posix() if golden_files else "",
        "latest_phase_count": len(phase_files),
    }
)
spec_map[spec_id] = state_entry
quality_state["schema_version"] = "1.0"
quality_state["updated_at"] = now
quality_state["threshold"] = threshold
quality_state["phase_assumptions_budget"] = assumptions_budget
quality_state["failure_limit"] = failure_limit
quality_state["specs"] = spec_map
quality_state_path.parent.mkdir(parents=True, exist_ok=True)
quality_state_path.write_text(json.dumps(quality_state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

result = {
    "spec_id": spec_id,
    "score": score,
    "threshold": threshold,
    "passed": passed,
    "reasons": reasons,
    "consecutive_failures": consecutive_failures,
    "phase_00_emitted": phase00_emitted,
    "version": current_version,
    "version_bumped": version_bumped,
}
print(json.dumps(result, sort_keys=True))
PY
  )"; then
    log "Governance gate: python evaluation failed for $queue_spec"
    return 1
  fi

  if ! parsed="$(python3 - "$result_json" <<'PY'
import json
import shlex
import sys
d = json.loads(sys.argv[1])
pairs = {
    "spec_id": d.get("spec_id", ""),
    "score": d.get("score", 0),
    "threshold": d.get("threshold", 0),
    "passed": "true" if d.get("passed") else "false",
    "reasons": "; ".join(d.get("reasons", [])) or "none",
    "fail_count": d.get("consecutive_failures", 0),
    "version": d.get("version", 1),
    "version_bumped": "true" if d.get("version_bumped") else "false",
    "phase00_emitted": "true" if d.get("phase_00_emitted") else "false",
}
for k, v in pairs.items():
    print(f"{k}={shlex.quote(str(v))}")
PY
  )"; then
    log "Governance gate: failed to parse quality result payload for $queue_spec"
    return 1
  fi
  # shellcheck disable=SC2086
  eval "$parsed"

  if [ "${SPEC_DECOMPOSITION_GOVERNANCE:-on}" = "on" ]; then
    if decomp_json="$(python3 agents/tools/lint_spec_decomposition.py "$queue_spec" --golden-dir "$SPECS_STABLE_GOLDEN_DIR" --phase-dir "$SPECS_STABLE_PHASE_DIR" --json 2>/dev/null || true)"; then
      :
    fi
    if [ -z "${decomp_json:-}" ]; then
      decomp_ok="false"
      decomp_errors="decomposition linter returned no result"
    elif decomp_parsed="$(python3 - "$decomp_json" <<'PY'
import json
import shlex
import sys
d = json.loads(sys.argv[1])
errors = d.get("errors", [])
print(f"decomp_ok={'true' if d.get('ok') else 'false'}")
print(f"decomp_errors={shlex.quote('; '.join(str(item) for item in errors) if errors else 'none')}")
PY
    )"; then
      # shellcheck disable=SC2086
      eval "$decomp_parsed"
    else
      decomp_ok="false"
      decomp_errors="decomposition linter result parse failed"
    fi
    if [ "${decomp_ok:-true}" != "true" ]; then
      append_research_event "SPEC_DECOMPOSITION_FAIL" "spec_id=$spec_id reasons=${decomp_errors:-none}"
      log "Governance: decomposition-readiness gate failed for $spec_id: ${decomp_errors:-unknown}"
      passed="false"
      if [ "${reasons:-none}" = "none" ]; then
        reasons="${decomp_errors:-unknown}"
      else
        reasons="${reasons}; ${decomp_errors:-unknown}"
      fi
    fi
  fi

  if [ "$version_bumped" = "true" ]; then
    append_research_event "GOLDEN_VERSION_BUMP" "spec_id=$spec_id version=$version reason=goal_source_changed"
    log "Governance: bumped golden version for $spec_id to v$version (goal source change detected)"
  fi

  if [ "$passed" = "true" ]; then
    append_research_event "SPEC_QUALITY_PASS" "spec_id=$spec_id score=$score threshold=$threshold version=$version"
    return 0
  fi

  append_research_event "SPEC_QUALITY_FAIL" "spec_id=$spec_id score=$score threshold=$threshold failures=$fail_count reasons=$reasons"
  log "Governance: quality gate failed for $spec_id (score=$score threshold=$threshold failures=$fail_count): $reasons"
  if [ "$phase00_emitted" = "true" ]; then
    append_research_event "PHASE_00_FALLBACK" "spec_id=$spec_id reason=quality_failures_exhausted"
    log "Governance: PHASE_00 scope-shrink fallback emitted for $spec_id"
  fi
  return 1
}

enforce_goalspec_governance_gate() {
  local queue_spec
  local has_specs=0

  while IFS= read -r queue_spec; do
    has_specs=1
    if ! evaluate_spec_governance_gate "$queue_spec"; then
      write_research_status "### BLOCKED"
      return 1
    fi
  done < <(list_queue_specs_sorted)

  if [ "$has_specs" -eq 0 ]; then
    return 0
  fi

  return 0
}

run_mode_goalspec_once() {
  local queue_spec_path reviewed_spec_path staged_source_path emitted_spec_path spec_id shard_path objective_sync_goal_path
  local pending_before_real="false"
  local guard_rc=0
  local family_ready_rc=0
  local -a queue_specs_before=()
  local -a queue_specs_after=()
  local -a new_queue_specs=()

  promote_goal_to_raw_if_needed

  if dir_has_payload_files "$RAW_DIR" || dir_has_payload_files "$ARTICULATED_DIR"; then
    if ! run_stage goal_intake; then
      return 1
    fi
    if ! enforce_canonical_goal_route_policy; then
      return 1
    fi
    return 0
  fi

  if dir_has_payload_files "$QUEUE_SPECS_DIR"; then
    queue_spec_path="$(oldest_payload_file "$QUEUE_SPECS_DIR" || true)"
    if [ -z "$queue_spec_path" ] || [ ! -f "$queue_spec_path" ]; then
      write_research_status "### BLOCKED"
      log "GoalSpec mode: queue spec payload detected but no readable file found in $QUEUE_SPECS_DIR"
      return 1
    fi
    apply_goalspec_idempotency_guard queue "$queue_spec_path" || guard_rc=$?
    case "$guard_rc" in
      0) ;;
      10) return 0 ;;
      *) return 1 ;;
    esac
    if ! run_stage spec_review; then
      return 1
    fi
    if ! evaluate_spec_governance_gate "$queue_spec_path"; then
      return 1
    fi
    if ! promote_reviewed_spec_runtime "$queue_spec_path"; then
      return 1
    fi
    return 0
  fi

  if dir_has_payload_files "$REVIEWED_SPECS_DIR"; then
    reviewed_spec_path="$(oldest_payload_file "$REVIEWED_SPECS_DIR" || true)"
    if [ -z "$reviewed_spec_path" ] || [ ! -f "$reviewed_spec_path" ]; then
      write_research_status "### BLOCKED"
      log "GoalSpec mode: reviewed spec payload detected but no readable file found in $REVIEWED_SPECS_DIR"
      return 1
    fi
    spec_id="$(spec_file_spec_id "$reviewed_spec_path")"
    shard_path="$TASKS_PENDING_SHARDS_DIR/${spec_id}.md"
    apply_goalspec_idempotency_guard reviewed "$reviewed_spec_path" "$shard_path" || guard_rc=$?
    case "$guard_rc" in
      0) ;;
      10) return 0 ;;
      *) return 1 ;;
    esac
    rm -f -- "$shard_path"
    if taskspending_has_real_cards; then
      pending_before_real="true"
    fi
    if ! run_taskmaster_with_overrides "$reviewed_spec_path" "$shard_path"; then
      return 1
    fi
    if ! markdown_has_real_task_cards "$shard_path"; then
      write_research_status "### BLOCKED"
      append_research_event "TASKMASTER_PENDING_EMPTY" "source=goalspec spec_id=$spec_id shard=$shard_path reason=missing-real-cards"
      log "GoalSpec mode: taskmaster did not produce a real pending shard for $spec_id at $shard_path"
      return 1
    fi
    if [ "$pending_before_real" = "false" ] && taskspending_has_real_cards; then
      write_research_status "### BLOCKED"
      log "GoalSpec mode: taskmaster unexpectedly wrote assembled pending cards to $TASKS_PENDING_FILE during per-spec Turn 1 flow"
      return 1
    fi
    if ! finalize_taskmaster_runtime "$reviewed_spec_path" "$shard_path"; then
      return 1
    fi
    return 0
  fi

  if dir_has_payload_files "$STAGING_DIR"; then
    staged_source_path="$(oldest_payload_file "$STAGING_DIR" || true)"
    if [ -z "$staged_source_path" ] || [ ! -f "$staged_source_path" ]; then
      write_research_status "### BLOCKED"
      log "GoalSpec mode: staged payload detected but no readable file found in $STAGING_DIR"
      return 1
    fi
    if spec_family_state_exists; then
      local staged_family_complete="off"
      if ! staged_family_complete="$(read_spec_family_complete_flag 2>/dev/null || printf 'off')"; then
        staged_family_complete="off"
      fi
      if [ "$staged_family_complete" = "on" ]; then
        spec_family_ready_for_final_merge || family_ready_rc=$?
        case "$family_ready_rc" in
          0)
            if ! finish_goalspec_source_runtime "$staged_source_path"; then
              return 1
            fi
            ;;
          2) return 1 ;;
        esac
        if [ "$family_ready_rc" -eq 0 ]; then
          if taskspending_has_real_cards; then
            append_research_event "PENDING_FAMILY_READY" "pending=$TASKS_PENDING_FILE status=already-assembled"
            log "GoalSpec mode: completed family staged source was finished and pending file is already assembled"
            return 0
          fi
          if dir_has_payload_files "$TASKS_PENDING_SHARDS_DIR"; then
            if ! assemble_pending_family_runtime; then
              return 1
            fi
            return 0
          fi
        fi
        append_research_event "GOALSPEC_DEFERRED" "reason=family_complete_staged_source_residue source=$staged_source_path"
        log "GoalSpec mode: deferred completed-family staged source residue because final merge is not ready"
        return 0
      fi
    fi
    objective_sync_goal_path="$(resolve_authoritative_goal_path || true)"
    if [ -z "$objective_sync_goal_path" ] || [ ! -f "$objective_sync_goal_path" ]; then
      objective_sync_goal_path="$staged_source_path"
    fi
    if ! ensure_objective_profile_synced "$objective_sync_goal_path" "goalspec-pre-spec-synthesis"; then
      return 1
    fi
    if [ "$OBJECTIVE_PROFILE_SYNC_LAST_ACTION" = "ran" ]; then
      append_research_event "GOALSPEC_DEFERRED" "reason=objective-profile-sync goal=$objective_sync_goal_path source=$staged_source_path"
      log "GoalSpec mode: deferred spec_synthesis because objective profile sync ran for canonical goal $objective_sync_goal_path (source=$staged_source_path)"
      return 0
    fi
    mapfile -t queue_specs_before < <(list_queue_specs_sorted)
    if ! run_stage spec_synthesis; then
      return 1
    fi
    mapfile -t queue_specs_after < <(list_queue_specs_sorted)
    new_queue_specs=()
    for emitted_spec_path in "${queue_specs_after[@]}"; do
      seen_before="false"
      local before_path
      for before_path in "${queue_specs_before[@]}"; do
        if [ "$emitted_spec_path" = "$before_path" ]; then
          seen_before="true"
          break
        fi
      done
      if [ "$seen_before" = "false" ]; then
        new_queue_specs+=("$emitted_spec_path")
      fi
    done
    if [ "${#new_queue_specs[@]}" -ne 1 ]; then
      write_research_status "### BLOCKED"
      log "GoalSpec mode: spec_synthesis must emit exactly one new queue spec per run (found ${#new_queue_specs[@]})"
      return 1
    fi
    emitted_spec_path="${new_queue_specs[0]}"
    if ! enforce_goalspec_governance_gate; then
      return 1
    fi
    if ! finalize_spec_synthesis_runtime "$staged_source_path" "$emitted_spec_path"; then
      return 1
    fi
    append_research_event "SPEC_SYNTHESIS_EMITTED" "source=$staged_source_path spec=$emitted_spec_path"
    log "GoalSpec mode: synthesized exactly one queue spec: $emitted_spec_path"
    return 0
  fi

  family_ready_rc=0
  spec_family_ready_for_final_merge || family_ready_rc=$?
  if [ "$family_ready_rc" -eq 2 ]; then
    return 1
  fi
  if [ "$family_ready_rc" -eq 0 ]; then
    if taskspending_has_real_cards; then
      append_research_event "PENDING_FAMILY_READY" "pending=$TASKS_PENDING_FILE status=already-assembled"
      log "GoalSpec mode: final family is ready for Taskaudit and pending file is already assembled"
      return 0
    fi
    if dir_has_payload_files "$TASKS_PENDING_SHARDS_DIR"; then
      if ! assemble_pending_family_runtime; then
        return 1
      fi
      return 0
    fi
  fi

  write_interrogation_state "NONE" "none" 0 0 "idle"
  log "GoalSpec mode: no queue work detected"
  return 0
}

run_blocker_queue_once() {
  local moved_path=""
  local blocker_path=""

  if dir_has_payload_files "$BLOCKER_INCOMING_DIR"; then
    moved_path="$(move_oldest_payload_file "$BLOCKER_INCOMING_DIR" "$BLOCKER_WORKING_DIR" || true)"
    if [ -n "$moved_path" ]; then
      append_research_event "BLOCKER_INTAKE" "moved_to=$moved_path"
      log "Blocker queue: moved one blocker from incoming to working: $moved_path"
      return 0
    fi
  fi

  if dir_has_payload_files "$BLOCKER_WORKING_DIR"; then
    blocker_path="$(oldest_payload_file "$BLOCKER_WORKING_DIR" || true)"
    if [ -n "$blocker_path" ] && [ -f "$blocker_path" ]; then
      if python3 agents/tools/blocker_ready.py "$blocker_path" >/dev/null 2>&1; then
        moved_path="$(move_payload_file_path "$blocker_path" "$BLOCKER_RESOLVED_DIR" || true)"
        if [ -n "$moved_path" ]; then
          append_research_event "BLOCKER_READY" "moved_to=$moved_path"
          log "Blocker queue: blocker ready and moved to resolved: $moved_path"
          return 0
        fi
      else
        append_research_event "BLOCKER_WAITING" "path=$blocker_path"
      fi
    fi
  fi

  if dir_has_payload_files "$BLOCKER_RESOLVED_DIR"; then
    moved_path="$(move_oldest_payload_file "$BLOCKER_RESOLVED_DIR" "$BLOCKER_ARCHIVED_DIR" || true)"
    if [ -n "$moved_path" ]; then
      append_research_event "BLOCKER_ARCHIVED" "moved_to=$moved_path"
      log "Blocker queue: archived resolved blocker: $moved_path"
      return 0
    fi
  fi

  return 0
}

run_mode_incident_once() {
  local moved_path incident_path fix_spec_path
  local max_cycles="${INCIDENT_MAX_CYCLES:-3}"
  local cycle=0
  local moved_any=0
  local target queue_name selected_path severity_class preemption_behavior framework_route

  if drift_hard_latch_active; then
    ensure_drift_hard_replan_spec || true
    append_research_event "INCIDENT_PAUSED_BY_DRIFT_HARD" "latch=$DRIFT_HARD_LATCH_FILE"
    log "Incident mode paused by DRIFT_HARD latch; focused objective replan is queued"
    write_research_status "### IDLE"
    return 0
  fi

  if ! [[ "$max_cycles" =~ ^[0-9]+$ ]] || [ "$max_cycles" -lt 1 ]; then
    max_cycles=3
  fi

  run_blocker_queue_once || true

  while [ "$cycle" -lt "$max_cycles" ]; do
    moved_path=""
    incident_path=""
    fix_spec_path=""
    target="$(select_incident_preemption_target || true)"
    if [ -z "$target" ]; then
      break
    fi

    IFS='|' read -r queue_name selected_path severity_class preemption_behavior <<<"$target"
    if [ -z "${queue_name:-}" ] || [ -z "${selected_path:-}" ]; then
      break
    fi

    if [ "$severity_class" = "S1" ] || [ "$severity_class" = "S2" ]; then
      append_research_event "INCIDENT_PREEMPTION" "severity=$severity_class behavior=$preemption_behavior queue=$queue_name incident=$selected_path"
      log "Incident mode: $severity_class preemption selected $selected_path ($queue_name, $preemption_behavior)"
    fi

    case "$queue_name" in
      incoming)
        moved_path="$(move_payload_file_path "$selected_path" "$INCIDENT_WORKING_DIR" || true)"
        if [ -n "$moved_path" ]; then
          incident_path="$moved_path"
          if ! ensure_incident_mode_b_sections "$incident_path"; then
            log "Incident mode: failed to scaffold intake sections for $incident_path"
            write_research_status "### BLOCKED"
            return 1
          fi
          append_research_event "INCIDENT_INTAKE" "moved_to=$incident_path severity=$severity_class preemption=$preemption_behavior hypothesis=intake investigation=queued"
          log "Incident mode: moved one incident from incoming to working and scaffolded intake contract: $incident_path"
          if ! run_stage incident_intake; then
            return 1
          fi
        fi
        ;;
      working)
        incident_path="$selected_path"
        if [ -n "$incident_path" ]; then
          if ! ensure_incident_mode_b_sections "$incident_path"; then
            log "Incident mode: failed to scaffold working incident for resolve stage: $incident_path"
            write_research_status "### BLOCKED"
            return 1
          fi
          if ! run_stage incident_resolve; then
            return 1
          fi
          if ! ensure_incident_mode_b_sections "$incident_path"; then
            log "Incident mode: failed to refresh hypothesis/investigation scaffolding after resolve stage: $incident_path"
            write_research_status "### BLOCKED"
            return 1
          fi
          if ! fix_spec_path="$(ensure_incident_fix_spec_artifact "$incident_path")"; then
            log "Incident mode: failed to generate fix_spec artifact for $incident_path"
            write_research_status "### BLOCKED"
            return 1
          fi
          if ! run_bounded_interrogation_rounds "INCIDENT_FIXSPEC" "$INCIDENT_FIXSPEC_INTERROGATION_ROUNDS" "$fix_spec_path"; then
            return 1
          fi
          if ! fix_spec_path="$(ensure_incident_fix_spec_artifact "$incident_path")"; then
            log "Incident mode: failed to refresh fix_spec artifact after interrogation rounds for $incident_path"
            write_research_status "### BLOCKED"
            return 1
          fi
          if ! fix_spec_path="$(validate_incident_fix_spec_consistency "$incident_path" "$fix_spec_path")"; then
            if ! fix_spec_path="$(run_incident_fix_spec_troubleshoot "$incident_path" "$fix_spec_path")"; then
              write_research_status "### BLOCKED"
              return 1
            fi
          fi
          append_research_event "INCIDENT_FIX_SPEC_READY" "incident=$incident_path severity=$severity_class preemption=$preemption_behavior fix_spec=$fix_spec_path investigation=completed"
          local incident_class blocker_path
          incident_class="$(incident_class_for_path "$incident_path" || true)"
          if [ "$incident_class" = "external-blocked" ]; then
            if ! blocker_path="$(ensure_external_blocker_record "$incident_path" "$fix_spec_path")"; then
              log "Incident mode: failed to create external blocker record for $incident_path"
              write_research_status "### BLOCKED"
              return 1
            fi
            append_research_event "INCIDENT_EXTERNAL_BLOCKED_ROUTED" "incident=$incident_path blocker=$blocker_path fix_spec=$fix_spec_path"
            log "Incident mode: routed external-blocked incident to blocker queue: $blocker_path"
          else
            framework_route="$(incident_requires_framework_level_routing "$incident_path" || true)"
            if [ "$framework_route" = "true" ]; then
              append_research_event "INCIDENT_FRAMEWORK_ROUTED" "incident=$incident_path fix_spec=$fix_spec_path framework-level route=agents/taskspending.md trigger=tool-script-contract-failure"
              log "Incident mode: framework-level routing enforced for $incident_path"
            fi

            if dir_has_payload_files "$QUEUE_SPECS_DIR"; then
              if ! run_taskmaster_with_overrides "" "$TASKS_PENDING_FILE"; then
                return 1
              fi
              if taskspending_has_real_cards && ! run_queue_governor_check "post-taskmaster-incident" "auto"; then
                write_research_status "### BLOCKED"
                return 1
              fi
              append_research_event "INCIDENT_TASK_HANDOFF" "incident=$incident_path fix_spec=$fix_spec_path taskspending=agents/taskspending.md"
              log "Incident mode: handed off fix_spec to taskmaster and refreshed taskspending output for $incident_path"
            fi
          fi

          moved_path="$(move_payload_file_path "$incident_path" "$INCIDENT_RESOLVED_DIR" || true)"
          if [ -n "$moved_path" ]; then
            append_research_event "INCIDENT_RESOLVED" "moved_to=$moved_path fix_spec=$fix_spec_path"
            log "Incident mode: moved one incident from working to resolved: $moved_path"
          else
            log "Incident mode: resolve stage completed but move to resolved failed for $incident_path"
            write_research_status "### BLOCKED"
            return 1
          fi
        fi
        ;;
      resolved)
        incident_path="$selected_path"
        if [ -n "$incident_path" ]; then
          if ! ensure_incident_mode_b_sections "$incident_path"; then
            log "Incident mode: failed to scaffold resolved incident prior to closeout: $incident_path"
            write_research_status "### BLOCKED"
            return 1
          fi
          if ! run_stage incident_archive; then
            return 1
          fi
          if ! ensure_incident_closeout_artifact "$incident_path"; then
            log "Incident mode: failed to write structured closeout artifact for $incident_path"
            write_research_status "### BLOCKED"
            return 1
          fi
          moved_path="$(move_payload_file_path "$incident_path" "$INCIDENT_ARCHIVED_DIR" || true)"
          if [ -n "$moved_path" ]; then
            append_research_event "INCIDENT_ARCHIVED" "moved_to=$moved_path closeout=recorded"
            log "Incident mode: moved one incident from resolved to archived: $moved_path"
          else
            log "Incident mode: archive stage completed but move to archived failed for $incident_path"
            write_research_status "### BLOCKED"
            return 1
          fi
        fi
        ;;
      *)
        log "Incident mode: invalid preemption target queue '$queue_name' for $selected_path"
        write_research_status "### BLOCKED"
        return 1
        ;;
    esac

    if [ -z "$moved_path" ]; then
      break
    fi

    moved_any=1
    cycle=$(( cycle + 1 ))
  done

  if [ "$moved_any" -eq 0 ]; then
    log "Incident mode: no queue work detected"
  fi

  return 0
}

run_mode_audit_once() {
  local moved_path
  local audit_trigger="${AUDIT_TRIGGER:-queue_empty}"
  local needs_taskaudit=0
  local force_marathon_with_backlog=0
  local family_guard_required=0

  if [ "${CLI_MODE_OVERRIDE:-}" = "AUDIT" ] && [ "${MODE:-}" = "once" ]; then
    force_marathon_with_backlog=1
  fi

  if dir_has_payload_files "$AUDIT_INCOMING_DIR"; then
    moved_path="$(move_oldest_payload_file "$AUDIT_INCOMING_DIR" "$AUDIT_WORKING_DIR" || true)"
    if [ -n "$moved_path" ]; then
      write_research_status "### IDLE"
      append_research_event "AUDIT_INTAKE" "moved_to=$moved_path"
      log "Audit mode: moved one audit ticket from incoming to working: $moved_path"
      return 0
    fi
  fi

  if [ "$audit_trigger" = "manual" ] && ! dir_has_payload_files "$AUDIT_WORKING_DIR"; then
    write_research_status "### IDLE"
    append_research_event "AUDIT_MANUAL_WAIT" "reason=manual_trigger_without_working_items"
    log "Audit mode: manual trigger enabled and no working tickets"
    return 0
  fi

  if [ "$audit_trigger" = "queue_empty" ] && dir_has_payload_files "$QUEUE_SPECS_DIR"; then
    write_research_status "### IDLE"
    log "Audit mode deferred: queue specs present in $QUEUE_SPECS_DIR"
    append_research_event "AUDIT_DEFERRED" "reason=queue_specs_not_empty"
    return 0
  fi

  if dir_has_payload_files "$AUDIT_WORKING_DIR"; then
    needs_taskaudit=1
  elif taskspending_has_real_cards; then
    needs_taskaudit=1
  fi

  if [ "$needs_taskaudit" -eq 1 ] && taskspending_has_real_cards; then
    if dir_has_payload_files "$TASKS_PENDING_SHARDS_DIR"; then
      family_guard_required=1
    fi
  fi

  if [ "$needs_taskaudit" -eq 1 ]; then
    local taskaudit_min_override=""
    local taskaudit_rc=0
    if [ "$family_guard_required" -eq 1 ]; then
      local family_ready_rc=0
      spec_family_ready_for_final_merge || family_ready_rc=$?
      if [ "$family_ready_rc" -ne 0 ]; then
        write_research_status "### BLOCKED"
        if [ "$family_ready_rc" -eq 2 ]; then
          append_research_event "TASKAUDIT_FAMILY_GUARD_FAIL" "pending=$TASKS_PENDING_FILE state=$SPEC_FAMILY_STATE_FILE reason=family-plan-invalid"
          log "Audit mode: Taskaudit blocked because the frozen spec family plan is invalid"
        else
          append_research_event "TASKAUDIT_FAMILY_GUARD_FAIL" "pending=$TASKS_PENDING_FILE state=$SPEC_FAMILY_STATE_FILE reason=family-not-ready"
          log "Audit mode: Taskaudit blocked because the spec family is not yet fully decomposed and complete"
        fi
        return 1
      fi
    fi
    if taskspending_has_single_marathon_remediation_card; then
      taskaudit_min_override="$(effective_remediation_min_cards)"
      append_research_event "TASKAUDIT_PROFILE_OVERRIDE" "min_cards_per_spec=$taskaudit_min_override profile=${TASKMASTER_COMPLEXITY_PROFILE_RESOLVED:-moderate} reason=marathon-remediation-profile-floor"
      log "Audit mode: single marathon remediation card detected; running Taskaudit with temporary TASKMASTER_MIN_CARDS_PER_SPEC=$taskaudit_min_override (profile=${TASKMASTER_COMPLEXITY_PROFILE_RESOLVED:-moderate})"
    fi
    write_research_status "### AUDIT_RUNNING"
    if taskspending_has_real_cards; then
      if ! run_queue_governor_check "pre-taskaudit" "auto"; then
        write_research_status "### AUDIT_FAIL"
        record_audit_outcome "AUDIT_FAIL" "mode=taskaudit reason=queue-governor"
        return 1
      fi
    fi
    if [ -n "$taskaudit_min_override" ]; then
      run_taskaudit_with_temporary_min_cards "$taskaudit_min_override" || taskaudit_rc=$?
    else
      run_stage taskaudit || taskaudit_rc=$?
    fi

    if [ "$taskaudit_rc" -eq 0 ]; then
      if ! enforce_drift_controls "post-taskaudit" "auto"; then
        write_research_status "### AUDIT_FAIL"
        record_audit_outcome "AUDIT_FAIL" "mode=taskaudit reason=post-audit-gate"
        return 0
      fi
      if dir_has_payload_files "$AUDIT_WORKING_DIR"; then
        moved_path="$(move_oldest_payload_file "$AUDIT_WORKING_DIR" "$AUDIT_PASSED_DIR" || true)"
        if [ -n "$moved_path" ]; then
          append_research_event "AUDIT_PASSED" "moved_to=$moved_path"
          write_research_status "### AUDIT_PASS"
          record_audit_outcome "AUDIT_PASS" "mode=taskaudit moved_to=$moved_path"
        else
          write_research_status "### AUDIT_PASS"
          record_audit_outcome "AUDIT_PASS" "mode=taskaudit moved_to=none"
        fi
      else
        append_research_event "AUDIT_IDLE" "taskaudit_completed_without_work_items"
        write_research_status "### AUDIT_PASS"
        record_audit_outcome "AUDIT_PASS" "mode=taskaudit no_working_items"
      fi
      return 0
    fi

    if dir_has_payload_files "$AUDIT_WORKING_DIR"; then
      moved_path="$(move_oldest_payload_file "$AUDIT_WORKING_DIR" "$AUDIT_FAILED_DIR" || true)"
      if [ -n "$moved_path" ]; then
        append_research_event "AUDIT_FAILED" "moved_to=$moved_path"
        write_research_status "### AUDIT_FAIL"
        record_audit_outcome "AUDIT_FAIL" "mode=taskaudit moved_to=$moved_path"
      else
        write_research_status "### AUDIT_FAIL"
        record_audit_outcome "AUDIT_FAIL" "mode=taskaudit moved_to=none"
      fi
    else
      append_research_event "AUDIT_FAILED" "taskaudit_blocked_without_work_items"
      write_research_status "### AUDIT_FAIL"
      record_audit_outcome "AUDIT_FAIL" "mode=taskaudit no_working_items"
    fi
    return 1
  fi

  if backlog_has_real_cards; then
    if [ "$force_marathon_with_backlog" -eq 1 ]; then
      append_research_event "AUDIT_BACKLOG_BYPASS" "reason=explicit_cli_mode_audit_once"
      log "Audit mode: explicit --once --mode Audit bypassed backlog defer; running marathon completion audit"
    else
      write_research_status "### IDLE"
      append_research_event "AUDIT_DEFERRED" "reason=tasksbacklog_not_empty"
      log "Audit mode deferred: backlog has active cards in $TASKS_BACKLOG_FILE"
      return 0
    fi
  fi

  local audit_goal_path=""
  audit_goal_path="$(resolve_objective_profile_sync_goal_path_for_audit || true)"
  if [ -z "$audit_goal_path" ] || [ ! -f "$audit_goal_path" ]; then
    write_research_status "### BLOCKED"
    append_research_event "AUDIT_DEFERRED" "reason=objective-profile-goal-missing"
    log "Audit mode blocked: unable to resolve an authoritative goal file for objective profile sync"
    return 1
  fi
  if ! ensure_objective_profile_synced "$audit_goal_path" "audit-pre-marathon"; then
    return 1
  fi
  if [ "$OBJECTIVE_PROFILE_SYNC_LAST_ACTION" = "ran" ]; then
    write_research_status "### IDLE"
    append_research_event "AUDIT_DEFERRED" "reason=objective-profile-sync goal=$audit_goal_path"
    log "Audit mode: deferred marathon completion audit because objective profile sync ran for $audit_goal_path"
    return 0
  fi

  if run_marathon_completion_audit; then
    append_research_event "AUDIT_MARATHON_COMPLETE" "results=$MARATHON_RESULTS_REPORT"
    return 0
  fi
  append_research_event "AUDIT_FAILED" "reason=marathon_completion_audit_failed"
  return 1
}

dispatch_mode_once() {
  local mode="$1"
  case "$mode" in
    GOALSPEC) run_mode_goalspec_once ;;
    INCIDENT) run_mode_incident_once ;;
    AUDIT) run_mode_audit_once ;;
    *)
      log "Invalid mode dispatched: $mode"
      return 1
      ;;
  esac
}

run_research_mode_troubleshoot() {
  local mode="$1"
  local dispatch_rc="${2:-1}"
  local backoff="${RESEARCH_FAILURE_BACKOFF_SECS:-60}"
  local status_before=""
  local failure_signature=""
  local target queue_name selected_path severity_class preemption_behavior
  local recovered=1

  if ! [[ "$backoff" =~ ^[0-9]+$ ]] || [ "$backoff" -lt 1 ]; then
    backoff=60
  fi

  status_before="$(read_research_status)"
  failure_signature="mode=${mode}|status=${status_before}|exit=${dispatch_rc}"
  record_incident_recurrence_signature "$failure_signature" "mode_dispatch" "fail" || true
  append_research_event "MODE_DISPATCH_FAIL" "mode=$mode exit=$dispatch_rc status=$status_before"
  write_research_status "### TROUBLESHOOT_RUNNING"
  append_research_event "RESEARCH_TROUBLESHOOT_START" "mode=$mode exit=$dispatch_rc"
  log "Mode dispatch failed: mode=$mode exit=$dispatch_rc status=$status_before; entering research troubleshoot path"

  if ! run_drift_detector_check "mode-dispatch-fail-$mode" "on"; then
    write_research_status "### BLOCKED"
    append_research_event "RESEARCH_TROUBLESHOOT_COMPLETE" "mode=$mode exit=$dispatch_rc action=drift-hard-backoff sleep_secs=$backoff"
    log "Research troubleshoot paused by DRIFT_HARD signal; retrying after ${backoff}s"
    sleep "$backoff"
    return 1
  fi

  case "$mode" in
    INCIDENT)
      target="$(select_incident_preemption_target || true)"
      if [ -n "$target" ]; then
        IFS='|' read -r queue_name selected_path severity_class preemption_behavior <<<"$target"
        if [ -n "${selected_path:-}" ] && [ -f "$selected_path" ]; then
          case "$queue_name" in
            working)
              if run_incident_fix_spec_troubleshoot "$selected_path" >/dev/null; then
                recovered=0
                append_research_event "RESEARCH_TROUBLESHOOT_RECOVERED" "mode=$mode queue=$queue_name incident=$selected_path severity=$severity_class preemption=$preemption_behavior"
              fi
              ;;
            incoming|resolved)
              if ensure_incident_mode_b_sections "$selected_path"; then
                append_research_event "RESEARCH_TROUBLESHOOT_NOTE" "mode=$mode queue=$queue_name incident=$selected_path severity=$severity_class preemption=$preemption_behavior remediation=scaffold-refresh"
              fi
              ;;
            *) : ;;
          esac
        fi
      fi
      ;;
    *)
      # Non-incident modes currently rely on bounded retry/backoff.
      recovered=1
      ;;
  esac

  if [ "$recovered" -eq 0 ]; then
    record_incident_recurrence_signature "$failure_signature" "mode_dispatch" "recovered" || true
    run_drift_detector_check "mode-dispatch-recovered-$mode" "on" || true
    write_research_status "### IDLE"
    append_research_event "RESEARCH_TROUBLESHOOT_COMPLETE" "mode=$mode exit=$dispatch_rc action=recovered"
    log "Research troubleshoot recovered mode=$mode; continuing loop"
    return 0
  fi

  append_research_event "RESEARCH_MECHANIC_START" "mode=$mode exit=$dispatch_rc"
  log "Research troubleshoot deterministic path did not recover mode=$mode; invoking mechanic stage"
  if run_stage mechanic; then
    record_incident_recurrence_signature "$failure_signature" "mode_dispatch" "recovered" || true
    run_drift_detector_check "mode-dispatch-mechanic-recovered-$mode" "on" || true
    write_research_status "### IDLE"
    append_research_event "RESEARCH_MECHANIC_RECOVERED" "mode=$mode exit=$dispatch_rc"
    append_research_event "RESEARCH_TROUBLESHOOT_COMPLETE" "mode=$mode exit=$dispatch_rc action=mechanic-recovered"
    log "Research mechanic recovered mode=$mode; continuing loop"
    return 0
  fi
  append_research_event "RESEARCH_MECHANIC_FAIL" "mode=$mode exit=$dispatch_rc"
  log "Research mechanic could not recover mode=$mode; entering backoff"

  write_research_status "### BLOCKED"
  append_research_event "RESEARCH_TROUBLESHOOT_COMPLETE" "mode=$mode exit=$dispatch_rc action=backoff sleep_secs=$backoff"
  log "Research troubleshoot could not auto-recover mode=$mode; retrying after ${backoff}s"
  sleep "$backoff"
  return 1
}

wait_for_research_work() {
  local poll="${RESEARCH_IDLE_POLL_SECS:-${RESEARCH_POLL_SECS:-60}}"
  local idle_mode="${RESEARCH_IDLE_MODE:-poll}"
  local wake_mode=""

  if ! [[ "$poll" =~ ^[0-9]+$ ]]; then
    poll=60
  fi
  if [ "$poll" -le 0 ]; then
    poll=60
  fi

  log "Idle: waiting for research queue activity (mode=$idle_mode poll=${poll}s)"
  while true; do
    if check_autonomy_markers; then
      return 0
    fi

    wake_mode="$(normalize_research_mode "${RESEARCH_MODE:-AUTO}")"
    if taskspending_has_real_cards; then
      case "$wake_mode" in
        AUTO|AUDIT)
          log "Pending task cards detected in $TASKS_PENDING_FILE; waking for Taskaudit merge"
          return 0
          ;;
      esac
    fi

    capture_queue_snapshot
    if [ "$ANY_RESEARCH_WORK" = "true" ]; then
      if [ "$RAW_HAS_PAYLOAD" = "true" ]; then
        log "Raw queue activity detected; debouncing for ${IDEA_DEBOUNCE_SECS}s"
        debounce_quiet_period "$RAW_DIR" "$IDEA_DEBOUNCE_SECS"
      elif [ "$GOAL_HAS_PAYLOAD" = "true" ]; then
        log "Goal queue activity detected; debouncing for ${IDEA_DEBOUNCE_SECS}s"
        debounce_quiet_period "$GOAL_DIR" "$IDEA_DEBOUNCE_SECS"
      fi
      return 0
    fi
    if research_backlog_empty_audit_fallback_ready; then
      case "$wake_mode" in
        AUTO|AUDIT)
          append_research_event "AUDIT_WAKE_BACKLOG_EMPTY_FALLBACK" \
            "reason=task-files-card-empty tasks=$TASKS_FILE backlog=$TASKS_BACKLOG_FILE taskspending=$TASKS_PENDING_FILE"
          log "Fallback: active/backlog/pending task stores are card-empty; waking for marathon audit check"
          return 0
          ;;
      esac
    fi
    if [ "$idle_mode" = "watch" ] && [ "$IDLE_WATCH_TOOL" = "inotifywait" ]; then
      inotifywait -q -t "$poll" -e create,modify,move,delete \
        "${RESEARCH_WATCH_DIRS[@]}" >/dev/null 2>&1 || true
    else
      sleep "$poll"
    fi
  done
}

run_once() {
  local prev_mode mode_info mode reason dispatch_rc=0

  refresh_research_weekly_usage_current "pre-cycle"
  pause_until_research_weekly_refresh_if_needed "pre-cycle"
  ensure_contract_scaffolding

  if check_autonomy_markers; then
    case "$AUTONOMY_SIGNAL" in
      stop) return "$EXIT_AUTONOMY_STOP" ;;
      complete) return "$EXIT_AUTONOMY_COMPLETE" ;;
      *) return "$EXIT_OK" ;;
    esac
  fi

  refresh_research_contracts
  prev_mode="$(read_current_research_mode)"
  mode_info="$(resolve_research_mode "$prev_mode")"
  mode="${mode_info%%|*}"
  reason="${mode_info#*|}"
  mode="$(normalize_research_mode "$mode")"

  update_research_state "$mode" "$reason" "$prev_mode"
  append_research_event "MODE_DISPATCH" "mode=$mode reason=$reason previous=$prev_mode"
  dispatch_rc=0
  dispatch_mode_once "$mode" || dispatch_rc=$?
  if [ "$dispatch_rc" -ne 0 ]; then
    if run_research_mode_troubleshoot "$mode" "$dispatch_rc"; then
      refresh_research_contracts
      apply_research_retention_policy
      return "$EXIT_OK"
    fi
    return "$EXIT_STAGE_FAILED"
  fi
  refresh_research_contracts
  if ! run_drift_detector_check "post-dispatch-$mode" "on"; then
    write_research_status "### BLOCKED"
    return "$EXIT_STAGE_FAILED"
  fi
  apply_research_retention_policy
  if check_autonomy_markers; then
    case "$AUTONOMY_SIGNAL" in
      stop) return "$EXIT_AUTONOMY_STOP" ;;
      complete) return "$EXIT_AUTONOMY_COMPLETE" ;;
      *) return "$EXIT_OK" ;;
    esac
  fi
  return "$EXIT_OK"
}

run_forever() {
  local prev_mode mode_info mode reason dispatch_rc=0

  while true; do
    refresh_research_weekly_usage_current "pre-cycle"
    pause_until_research_weekly_refresh_if_needed "pre-cycle"
    ensure_contract_scaffolding

    if check_autonomy_markers; then
      break
    fi

    refresh_research_contracts
    prev_mode="$(read_current_research_mode)"
    mode_info="$(resolve_research_mode "$prev_mode")"
    mode="${mode_info%%|*}"
    reason="${mode_info#*|}"
    mode="$(normalize_research_mode "$mode")"

    update_research_state "$mode" "$reason" "$prev_mode"
    append_research_event "MODE_DISPATCH" "mode=$mode reason=$reason previous=$prev_mode"
    dispatch_rc=0
    dispatch_mode_once "$mode" || dispatch_rc=$?
    if [ "$dispatch_rc" -ne 0 ]; then
      run_research_mode_troubleshoot "$mode" "$dispatch_rc" || true
      refresh_research_contracts
      apply_research_retention_policy
      if check_autonomy_markers; then
        break
      fi
      wait_for_research_work
      if [ -n "$AUTONOMY_SIGNAL" ]; then
        break
      fi
      continue
    fi
    refresh_research_contracts
    if ! run_drift_detector_check "post-dispatch-$mode" "on"; then
      write_research_status "### BLOCKED"
      apply_research_retention_policy
      if check_autonomy_markers; then
        break
      fi
      wait_for_research_work
      if [ -n "$AUTONOMY_SIGNAL" ]; then
        break
      fi
      continue
    fi
    apply_research_retention_policy
    if check_autonomy_markers; then
      break
    fi

    wait_for_research_work
    if [ -n "$AUTONOMY_SIGNAL" ]; then
      break
    fi
  done

  case "$AUTONOMY_SIGNAL" in
    stop) return "$EXIT_AUTONOMY_STOP" ;;
    complete) return "$EXIT_AUTONOMY_COMPLETE" ;;
    *) return "$EXIT_OK" ;;
  esac
}

export_research_config_env() {
  export MAX_AUTONOMY_MODE
  export RESEARCH_MODE
  export SPEC_INTERROGATION_ROUNDS
  export PHASE_INTERROGATION_ROUNDS
  export SPEC_QUALITY_THRESHOLD
  export PHASE_ASSUMPTIONS_BUDGET
  export SPEC_QUALITY_FAIL_MAX
  export INCIDENT_FIXSPEC_INTERROGATION_ROUNDS
  export INCIDENT_MAX_CYCLES
  export AUDIT_TRIGGER
  export RESEARCH_IDLE_MODE
  export RESEARCH_IDLE_POLL_SECS
  export RESEARCH_LOCKING
  export RESEARCH_ALLOW_SEARCH
  export RESEARCH_ALLOW_SEARCH_EXCEPTION
  export OBJECTIVE_CONTRACT_SCHEMA_FILE
  export OBJECTIVE_CONTRACT_FILE
  export AUDIT_COMPLETION_MANIFEST
  export AUDIT_STRICT_CONTRACT_FILE
  export COMMAND_CONTRACT_REPORT_FILE
  export AUDIT_COMPLETENESS_MODE
  export AUDIT_COMPREHENSIVE_MAX_SKIPS
  export DRIFT_CONTROL_MODE
  export DRIFT_CONTROL_POLICY_FILE
  export DRIFT_ENFORCE_ON_AUTONOMY_COMPLETE
  export DRIFT_DETECTOR_MODE
  export DRIFT_STATUS_REPORT_FILE
  export QUEUE_GOVERNOR_MODE
  export QUEUE_GOVERNOR_REPORT_FILE
  export DRIFT_METRICS_FILE
  export PROGRESS_WATCHDOG_STATE_FILE
  export PROGRESS_WATCHDOG_REPORT_FILE
  export INCIDENT_RECURRENCE_LEDGER_FILE
  export TASKMASTER_COMPLEXITY_PROFILE
  export TASKMASTER_COMPLEXITY_PROFILE_RESOLVED
  export TASKMASTER_COMPLEXITY_PROFILE_SOURCE
  export TASKMASTER_COMPLEXITY_PROFILE_EVIDENCE
  export SPEC_DECOMPOSITION_GOVERNANCE
  export TASKMASTER_MIN_CARDS_PER_SPEC
  export TASKMASTER_MAX_CARDS_PER_SPEC
  export TASKMASTER_TARGET_CARDS_PER_SPEC
  export TASKMASTER_MIN_TOTAL_CARDS
  export TASKMASTER_TARGET_TOTAL_CARDS
  export TASKCARD_TARGET_SHORTFALL_MODE
  export TASKCARD_FORMAT_STRICT
  export TASKCARD_ENFORCE_EXECUTION_TEMPLATE
  export TASKCARD_PHASE_WORKPLAN_COVERAGE
  export TASKCARD_MAX_PHASE_STEPS_PER_CARD
  export TASKCARD_SCOPE_LINT
  export STAGE_RETRY_MAX
  export STAGE_RETRY_BACKOFF_SECS
  export RESEARCH_FAILURE_BACKOFF_SECS
  export ENV_PREFLIGHT_MODE
  export ENV_PREFLIGHT_TRANSPORT_CHECK
  export NETWORK_GUARD_MODE
  export RESEARCH_NETWORK_GUARD_POLICY
  export RESEARCH_NETWORK_POLICY_EXCEPTION
  export NETWORK_OUTAGE_RESILIENCE_MODE
  export NETWORK_OUTAGE_WAIT_INITIAL_SECS
  export NETWORK_OUTAGE_WAIT_MAX_SECS
  export NETWORK_OUTAGE_MAX_PROBES
  export NETWORK_OUTAGE_PROBE_TIMEOUT_SECS
  export NETWORK_OUTAGE_PROBE_HOST
  export NETWORK_OUTAGE_PROBE_PORT
  export NETWORK_OUTAGE_PROBE_CMD
  export NETWORK_OUTAGE_POLICY
  export NETWORK_OUTAGE_ROUTE_TO_BLOCKER
  export NETWORK_OUTAGE_ROUTE_TO_INCIDENT
  export AUDIT_CONTRACT_FILE
  export AUDIT_EXECUTION_REPORT
  export AUDIT_GATE_DECISION_FILE
  export AUDIT_PLAN_REPORT
  export AUDIT_LOGS_DIR
  export TASK_STORE_LOCK_FILE
  export TASK_STORE_LOCK_TIMEOUT_SECS
}

setup_research_idle_watch() {
  IDLE_WATCH_TOOL=""
  if [ "${RESEARCH_IDLE_MODE:-poll}" != "watch" ]; then
    return 0
  fi
  if command -v inotifywait >/dev/null 2>&1; then
    IDLE_WATCH_TOOL="inotifywait"
  else
    log "RESEARCH_IDLE_MODE=watch requested but inotifywait is unavailable; falling back to poll"
  fi
}

acquire_research_lock_if_enabled() {
  if [ "${RESEARCH_LOCKING:-off}" != "on" ]; then
    return 0
  fi

  mkdir -p "agents/.locks"

  if command -v flock >/dev/null 2>&1; then
    exec {RESEARCH_LOCK_FD}> "agents/.locks/research_loop.lock"
    if ! flock -n "$RESEARCH_LOCK_FD"; then
      echo "Research loop lock is already held: agents/.locks/research_loop.lock" >&2
      exit "$EXIT_LOCK_HELD"
    fi
    return 0
  fi

  if mkdir "agents/.locks/research_loop.lock.d" 2>/dev/null; then
    trap 'rmdir "agents/.locks/research_loop.lock.d" >/dev/null 2>&1 || true' EXIT
    return 0
  fi

  echo "Research loop lock is already held: agents/.locks/research_loop.lock.d" >&2
  exit "$EXIT_LOCK_HELD"
}

run_research_env_preflight() {
  local report_path="$TMP_DIR/research_env_preflight.json"
  local rc=0

  if ! truthy "${ENV_PREFLIGHT_MODE:-on}"; then
    log "Env preflight: disabled (ENV_PREFLIGHT_MODE=${ENV_PREFLIGHT_MODE:-off})"
    return 0
  fi

  python3 "$ENV_PREFLIGHT_TOOL" \
    --phase research \
    --repo-root "$REPO_ROOT" \
    --report-out "$report_path" \
    --strict-isolation on \
    --allow-search "${RESEARCH_ALLOW_SEARCH:-off}" \
    --allow-search-exception "${RESEARCH_ALLOW_SEARCH_EXCEPTION:-off}" \
    --network-guard-mode "${NETWORK_GUARD_MODE:-off}" \
    --network-guard-policy "${RESEARCH_NETWORK_GUARD_POLICY:-deny}" \
    --network-policy-exception "${RESEARCH_NETWORK_POLICY_EXCEPTION:-off}" \
    --transport-check "${ENV_PREFLIGHT_TRANSPORT_CHECK:-on}" \
    --probe-host "${NETWORK_OUTAGE_PROBE_HOST:-api.openai.com}" \
    --probe-port "${NETWORK_OUTAGE_PROBE_PORT:-443}" \
    --probe-timeout-secs "${NETWORK_OUTAGE_PROBE_TIMEOUT_SECS:-5}" \
    --require-command bash \
    --require-command python3 \
    --require-file "$MODEL_CFG" \
    --require-file "$WF_CFG" \
    --require-dir "$RUNS_DIR" \
    --require-dir "$DIAGNOSTICS_DIR" \
    --require-dir "$TMP_DIR" \
    --require-writable-dir "$RUNS_DIR" \
    --require-writable-dir "$DIAGNOSTICS_DIR" \
    --require-writable-dir "$TMP_DIR" \
    >/dev/null 2>&1 || rc=$?

  case "$rc" in
    0)
      log "Env preflight: PASS report=$report_path"
      return 0
      ;;
    12)
      log "Env preflight: NET_WAIT report=$report_path"
      if truthy "${NETWORK_OUTAGE_RESILIENCE_MODE:-on}"; then
        write_research_status "### NET_WAIT"
        if wait_for_research_network_recovery "Preflight" "env_preflight transport check failed" "preflight_transport_unreachable" "" "$report_path" "" ""; then
          write_research_status "### IDLE"
          log "Env preflight: transport recovered; continuing"
          return 0
        fi
      fi
      echo "Environment preflight transport check failed (NET_WAIT). See $report_path" >&2
      return 1
      ;;
    10)
      echo "Environment preflight failed with ENV_BLOCKED. See $report_path" >&2
      return 1
      ;;
    11)
      echo "Environment preflight failed with POLICY_BLOCKED. See $report_path" >&2
      return 1
      ;;
    *)
      echo "Environment preflight failed (exit=$rc). See $report_path" >&2
      return 1
      ;;
  esac
}

run_governance_canary_check() {
  return 0
}

preflight() {
  require bash
  require python3

  if command -v timeout >/dev/null 2>&1; then
    TIMEOUT_BIN="timeout"
  elif command -v gtimeout >/dev/null 2>&1; then
    TIMEOUT_BIN="gtimeout"
  else
    TIMEOUT_BIN=""
    log "timeout/gtimeout not found; using python3 timeout wrapper"
  fi

  [ -f "$MODEL_CFG" ] || { echo "Missing $MODEL_CFG" >&2; exit "$EXIT_CONFIG_ERROR"; }
  [ -f "$WF_CFG" ] || { echo "Missing $WF_CFG" >&2; exit "$EXIT_CONFIG_ERROR"; }

  ensure_contract_scaffolding
  local incident_migration_summary=""
  incident_migration_summary="$(migrate_legacy_incidents_to_incoming)"
  if [ "$incident_migration_summary" != "migrated=0 deduped=0 renamed=0" ]; then
    log "Incident queue migration: $incident_migration_summary"
  fi
  recover_stage_checkpoint_if_needed

  parse_model_config
  parse_workflow_config
  if [ -n "$CLI_MODE_OVERRIDE" ]; then
    RESEARCH_MODE="$CLI_MODE_OVERRIDE"
    log "Config: CLI mode override applied: RESEARCH_MODE=$RESEARCH_MODE"
  fi
  [ -f "$OBJECTIVE_CONTRACT_VALIDATOR_TOOL" ] || { echo "Missing objective contract validator: $OBJECTIVE_CONTRACT_VALIDATOR_TOOL" >&2; exit "$EXIT_CONFIG_ERROR"; }
  [ -f "$COMMAND_CONTRACT_GUARD_TOOL" ] || { echo "Missing command contract guard: $COMMAND_CONTRACT_GUARD_TOOL" >&2; exit "$EXIT_CONFIG_ERROR"; }
  [ -f "$OBJECTIVE_CONTRACT_SCHEMA_FILE" ] || { echo "Missing OBJECTIVE_CONTRACT_SCHEMA_FILE: $OBJECTIVE_CONTRACT_SCHEMA_FILE" >&2; exit "$EXIT_CONFIG_ERROR"; }
  [ -f "$OBJECTIVE_CONTRACT_FILE" ] || { echo "Missing OBJECTIVE_CONTRACT_FILE: $OBJECTIVE_CONTRACT_FILE" >&2; exit "$EXIT_CONFIG_ERROR"; }
  validate_objective_contract_runtime
  export_research_config_env
  setup_research_idle_watch
  acquire_research_lock_if_enabled

  case "${USAGE_AUTOPAUSE_MODE:-off}" in
    On|ON|on|Off|OFF|off|true|TRUE|false|FALSE) ;;
    *)
      echo "Invalid USAGE_AUTOPAUSE_MODE: ${USAGE_AUTOPAUSE_MODE} (expected On|Off)" >&2
      exit "$EXIT_CONFIG_ERROR"
      ;;
  esac
  local research_usage_contract="" research_usage_semantics="" research_usage_threshold="" research_usage_source=""
  research_usage_contract="$(resolve_research_weekly_usage_contract 2>/dev/null || true)"
  if [ -n "$research_usage_contract" ]; then
    research_usage_semantics="$(printf '%s\n' "$research_usage_contract" | sed -n '1p')"
    research_usage_threshold="$(printf '%s\n' "$research_usage_contract" | sed -n '2p')"
    research_usage_source="$(printf '%s\n' "$research_usage_contract" | sed -n '3p')"
    if ! is_nonnegative_number "$research_usage_threshold"; then
      echo "Invalid ${research_usage_source}: ${research_usage_threshold} (expected non-negative number or empty)" >&2
      exit "$EXIT_CONFIG_ERROR"
    fi
    if [ -n "$(trim "${RESEARCH_WEEKLY_USAGE_REMAINING_THRESHOLD:-}")" ] && [ -n "$(trim "${RESEARCH_WEEKLY_USAGE_CONSUMED_THRESHOLD:-}")" ]; then
      log "WARN: Both RESEARCH_WEEKLY_USAGE_REMAINING_THRESHOLD and RESEARCH_WEEKLY_USAGE_CONSUMED_THRESHOLD are set; preflight will use remaining semantics"
    fi
  fi
  if truthy "${USAGE_AUTOPAUSE_MODE:-off}" && [ -n "$research_usage_contract" ]; then
    if ! next_weekly_refresh_info_utc "$(trim "${RESEARCH_WEEKLY_REFRESH_UTC:-MON 00:00}")" >/dev/null 2>&1; then
      echo "Invalid RESEARCH_WEEKLY_REFRESH_UTC: ${RESEARCH_WEEKLY_REFRESH_UTC} (expected DAY HH:MM, UTC)" >&2
      exit "$EXIT_CONFIG_ERROR"
    fi
  fi
  case "${RESEARCH_ALLOW_SEARCH_EXCEPTION:-off}" in
    On|ON|on|Off|OFF|off|true|TRUE|false|FALSE) ;;
    *)
      echo "Invalid RESEARCH_ALLOW_SEARCH_EXCEPTION: ${RESEARCH_ALLOW_SEARCH_EXCEPTION} (expected On|Off)" >&2
      exit "$EXIT_CONFIG_ERROR"
      ;;
  esac
  case "${RESEARCH_NETWORK_POLICY_EXCEPTION:-off}" in
    On|ON|on|Off|OFF|off|true|TRUE|false|FALSE) ;;
    *)
      echo "Invalid RESEARCH_NETWORK_POLICY_EXCEPTION: ${RESEARCH_NETWORK_POLICY_EXCEPTION} (expected On|Off)" >&2
      exit "$EXIT_CONFIG_ERROR"
      ;;
  esac
  case "${ENV_PREFLIGHT_MODE:-on}" in
    On|ON|on|Off|OFF|off|true|TRUE|false|FALSE) ;;
    *)
      echo "Invalid ENV_PREFLIGHT_MODE: ${ENV_PREFLIGHT_MODE} (expected On|Off)" >&2
      exit "$EXIT_CONFIG_ERROR"
      ;;
  esac
  case "${ENV_PREFLIGHT_TRANSPORT_CHECK:-on}" in
    On|ON|on|Off|OFF|off|true|TRUE|false|FALSE) ;;
    *)
      echo "Invalid ENV_PREFLIGHT_TRANSPORT_CHECK: ${ENV_PREFLIGHT_TRANSPORT_CHECK} (expected On|Off)" >&2
      exit "$EXIT_CONFIG_ERROR"
      ;;
  esac
  case "${NETWORK_GUARD_MODE:-off}" in
    On|ON|on|Off|OFF|off|true|TRUE|false|FALSE) ;;
    *)
      echo "Invalid NETWORK_GUARD_MODE: ${NETWORK_GUARD_MODE} (expected On|Off)" >&2
      exit "$EXIT_CONFIG_ERROR"
      ;;
  esac
  case "${RESEARCH_NETWORK_GUARD_POLICY:-deny}" in
    allow|ALLOW|Allow|deny|DENY|Deny) ;;
    *)
      echo "Invalid RESEARCH_NETWORK_GUARD_POLICY: ${RESEARCH_NETWORK_GUARD_POLICY} (expected allow|deny)" >&2
      exit "$EXIT_CONFIG_ERROR"
      ;;
  esac
  if truthy "${NETWORK_GUARD_MODE:-off}"; then
    NETWORK_GUARD_MODE="on"
  else
    NETWORK_GUARD_MODE="off"
  fi
  RESEARCH_NETWORK_GUARD_POLICY="$(printf '%s' "${RESEARCH_NETWORK_GUARD_POLICY:-deny}" | tr '[:upper:]' '[:lower:]')"
  if [ "$NETWORK_GUARD_MODE" = "on" ] && [ ! -f "$NETWORK_GUARD_TOOL" ]; then
    echo "Missing NETWORK_GUARD_TOOL: $NETWORK_GUARD_TOOL (required when NETWORK_GUARD_MODE=on)" >&2
    exit "$EXIT_CONFIG_ERROR"
  fi
  case "${NETWORK_OUTAGE_RESILIENCE_MODE:-on}" in
    On|ON|on|Off|OFF|off|true|TRUE|false|FALSE) ;;
    *)
      echo "Invalid NETWORK_OUTAGE_RESILIENCE_MODE: ${NETWORK_OUTAGE_RESILIENCE_MODE} (expected On|Off)" >&2
      exit "$EXIT_CONFIG_ERROR"
      ;;
  esac
  if ! [[ "${NETWORK_OUTAGE_WAIT_INITIAL_SECS:-15}" =~ ^[0-9]+$ ]] || [ "${NETWORK_OUTAGE_WAIT_INITIAL_SECS:-15}" -lt 1 ]; then
    echo "Invalid NETWORK_OUTAGE_WAIT_INITIAL_SECS: ${NETWORK_OUTAGE_WAIT_INITIAL_SECS} (expected integer >= 1)" >&2
    exit "$EXIT_CONFIG_ERROR"
  fi
  if ! [[ "${NETWORK_OUTAGE_WAIT_MAX_SECS:-300}" =~ ^[0-9]+$ ]] || [ "${NETWORK_OUTAGE_WAIT_MAX_SECS:-300}" -lt 1 ]; then
    echo "Invalid NETWORK_OUTAGE_WAIT_MAX_SECS: ${NETWORK_OUTAGE_WAIT_MAX_SECS} (expected integer >= 1)" >&2
    exit "$EXIT_CONFIG_ERROR"
  fi
  if ! [[ "${NETWORK_OUTAGE_MAX_PROBES:-0}" =~ ^[0-9]+$ ]] || [ "${NETWORK_OUTAGE_MAX_PROBES:-0}" -lt 0 ]; then
    echo "Invalid NETWORK_OUTAGE_MAX_PROBES: ${NETWORK_OUTAGE_MAX_PROBES} (expected integer >= 0)" >&2
    exit "$EXIT_CONFIG_ERROR"
  fi
  if ! [[ "${NETWORK_OUTAGE_PROBE_TIMEOUT_SECS:-5}" =~ ^[0-9]+$ ]] || [ "${NETWORK_OUTAGE_PROBE_TIMEOUT_SECS:-5}" -lt 1 ]; then
    echo "Invalid NETWORK_OUTAGE_PROBE_TIMEOUT_SECS: ${NETWORK_OUTAGE_PROBE_TIMEOUT_SECS} (expected integer >= 1)" >&2
    exit "$EXIT_CONFIG_ERROR"
  fi
  if ! [[ "${NETWORK_OUTAGE_PROBE_PORT:-443}" =~ ^[0-9]+$ ]] || [ "${NETWORK_OUTAGE_PROBE_PORT:-443}" -lt 1 ] || [ "${NETWORK_OUTAGE_PROBE_PORT:-443}" -gt 65535 ]; then
    echo "Invalid NETWORK_OUTAGE_PROBE_PORT: ${NETWORK_OUTAGE_PROBE_PORT} (expected integer 1..65535)" >&2
    exit "$EXIT_CONFIG_ERROR"
  fi
  case "${NETWORK_OUTAGE_POLICY:-pause_resume}" in
    pause_resume|PAUSE_RESUME|Pause_Resume|incident|INCIDENT|Incident|blocker|BLOCKER|Blocker) ;;
    *)
      echo "Invalid NETWORK_OUTAGE_POLICY: ${NETWORK_OUTAGE_POLICY} (expected pause_resume|incident|blocker)" >&2
      exit "$EXIT_CONFIG_ERROR"
      ;;
  esac
  case "${NETWORK_OUTAGE_ROUTE_TO_BLOCKER:-off}" in
    On|ON|on|Off|OFF|off|true|TRUE|false|FALSE) ;;
    *)
      echo "Invalid NETWORK_OUTAGE_ROUTE_TO_BLOCKER: ${NETWORK_OUTAGE_ROUTE_TO_BLOCKER} (expected On|Off)" >&2
      exit "$EXIT_CONFIG_ERROR"
      ;;
  esac
  case "${NETWORK_OUTAGE_ROUTE_TO_INCIDENT:-off}" in
    On|ON|on|Off|OFF|off|true|TRUE|false|FALSE) ;;
    *)
      echo "Invalid NETWORK_OUTAGE_ROUTE_TO_INCIDENT: ${NETWORK_OUTAGE_ROUTE_TO_INCIDENT} (expected On|Off)" >&2
      exit "$EXIT_CONFIG_ERROR"
      ;;
  esac
  NETWORK_OUTAGE_POLICY="$(printf '%s' "${NETWORK_OUTAGE_POLICY:-pause_resume}" | tr '[:upper:]' '[:lower:]')"
  if [ -z "$NETWORK_OUTAGE_POLICY" ]; then
    NETWORK_OUTAGE_POLICY="pause_resume"
  fi
  if truthy "${NETWORK_OUTAGE_ROUTE_TO_BLOCKER:-off}"; then
    NETWORK_OUTAGE_ROUTE_TO_BLOCKER="on"
  else
    NETWORK_OUTAGE_ROUTE_TO_BLOCKER="off"
  fi
  if truthy "${NETWORK_OUTAGE_ROUTE_TO_INCIDENT:-off}"; then
    NETWORK_OUTAGE_ROUTE_TO_INCIDENT="on"
  else
    NETWORK_OUTAGE_ROUTE_TO_INCIDENT="off"
  fi
  if ! [[ "${RESEARCH_FAILURE_BACKOFF_SECS:-60}" =~ ^[0-9]+$ ]] || [ "${RESEARCH_FAILURE_BACKOFF_SECS:-60}" -lt 1 ]; then
    echo "Invalid RESEARCH_FAILURE_BACKOFF_SECS: ${RESEARCH_FAILURE_BACKOFF_SECS} (expected integer >= 1)" >&2
    exit "$EXIT_CONFIG_ERROR"
  fi
  if [ -z "$(trim "${NETWORK_OUTAGE_PROBE_CMD:-}")" ] && [ -z "$(trim "${NETWORK_OUTAGE_PROBE_HOST:-api.openai.com}")" ]; then
    echo "Invalid NETWORK_OUTAGE_PROBE_HOST: empty host with no NETWORK_OUTAGE_PROBE_CMD override" >&2
    exit "$EXIT_CONFIG_ERROR"
  fi
  if [ "${NETWORK_OUTAGE_WAIT_INITIAL_SECS:-15}" -gt "${NETWORK_OUTAGE_WAIT_MAX_SECS:-300}" ]; then
    echo "Invalid outage wait config: NETWORK_OUTAGE_WAIT_INITIAL_SECS must be <= NETWORK_OUTAGE_WAIT_MAX_SECS" >&2
    exit "$EXIT_CONFIG_ERROR"
  fi
  if ! [[ "${RESEARCH_RUNS_RETENTION_KEEP:-100}" =~ ^[0-9]+$ ]] || [ "${RESEARCH_RUNS_RETENTION_KEEP:-100}" -lt 1 ]; then
    echo "Invalid RESEARCH_RUNS_RETENTION_KEEP: ${RESEARCH_RUNS_RETENTION_KEEP} (expected integer >= 1)" >&2
    exit "$EXIT_CONFIG_ERROR"
  fi
  if ! [[ "${DIAGNOSTICS_RETENTION_KEEP:-25}" =~ ^[0-9]+$ ]] || [ "${DIAGNOSTICS_RETENTION_KEEP:-25}" -lt 1 ]; then
    echo "Invalid DIAGNOSTICS_RETENTION_KEEP: ${DIAGNOSTICS_RETENTION_KEEP} (expected integer >= 1)" >&2
    exit "$EXIT_CONFIG_ERROR"
  fi
  if ! [[ "${AUDIT_HISTORY_RETENTION_KEEP:-100}" =~ ^[0-9]+$ ]] || [ "${AUDIT_HISTORY_RETENTION_KEEP:-100}" -lt 1 ]; then
    echo "Invalid AUDIT_HISTORY_RETENTION_KEEP: ${AUDIT_HISTORY_RETENTION_KEEP} (expected integer >= 1)" >&2
    exit "$EXIT_CONFIG_ERROR"
  fi
  case "${DRIFT_CONTROL_MODE:-on}" in
    off|telemetry|on) ;;
    *)
      echo "Invalid DRIFT_CONTROL_MODE: ${DRIFT_CONTROL_MODE} (expected off|telemetry|on)" >&2
      exit "$EXIT_CONFIG_ERROR"
      ;;
  esac
  set_permission_flags
  [ -f "$ENV_PREFLIGHT_TOOL" ] || { echo "Missing env preflight tool: $ENV_PREFLIGHT_TOOL" >&2; exit "$EXIT_CONFIG_ERROR"; }
  run_research_env_preflight || exit "$EXIT_CONFIG_ERROR"

  if [ ! -f "$RESEARCH_STATUS" ]; then
    write_research_status "### IDLE"
  elif [ "$(read_research_status)" = "### NET_WAIT" ]; then
    log "Config: detected stale ### NET_WAIT at startup; running outage recovery probe loop"
    if ! wait_for_research_network_recovery "Resume" "status marker ### NET_WAIT at startup" "resume-net-wait" "" "" "" ""; then
      write_research_status "### BLOCKED"
      echo "Network outage recovery routed out of NET_WAIT at startup by policy" >&2
      exit "$EXIT_STAGE_FAILED"
    fi
  fi

  refresh_research_contracts
  apply_research_retention_policy

  log "Config: mode=$MODE debounce=${IDEA_DEBOUNCE_SECS}s poll=${RESEARCH_POLL_SECS}s idle_mode=${RESEARCH_IDLE_MODE} idle_poll=${RESEARCH_IDLE_POLL_SECS}s idle_watch_tool=${IDLE_WATCH_TOOL:-none}"
  log "Config: autonomy max_mode=${MAX_AUTONOMY_MODE} research_mode=${RESEARCH_MODE} audit_trigger=${AUDIT_TRIGGER} incident_max_cycles=${INCIDENT_MAX_CYCLES} allow_search=${RESEARCH_ALLOW_SEARCH} allow_search_exception=${RESEARCH_ALLOW_SEARCH_EXCEPTION} locking=${RESEARCH_LOCKING}"
  log "Config: objective_contract=${OBJECTIVE_CONTRACT_FILE} objective_schema=${OBJECTIVE_CONTRACT_SCHEMA_FILE} command_contract_report=${COMMAND_CONTRACT_REPORT_FILE}"
  log "Config: audit_completion_manifest=${AUDIT_COMPLETION_MANIFEST} audit_strict_contract=${AUDIT_STRICT_CONTRACT_FILE} audit_completeness_mode=${AUDIT_COMPLETENESS_MODE} audit_comprehensive_max_skips=${AUDIT_COMPREHENSIVE_MAX_SKIPS}"
  log "Config: retention keep_last runs=${RESEARCH_RUNS_RETENTION_KEEP:-100} diagnostics=${DIAGNOSTICS_RETENTION_KEEP:-25} audit_history=${AUDIT_HISTORY_RETENTION_KEEP:-100}"
  log "Config: stage_retry_max=${STAGE_RETRY_MAX} stage_retry_backoff_secs=${STAGE_RETRY_BACKOFF_SECS} research_failure_backoff_secs=${RESEARCH_FAILURE_BACKOFF_SECS} heartbeat_secs=${HEARTBEAT_SECS}"
  log "Config: env_preflight_mode=${ENV_PREFLIGHT_MODE} env_preflight_transport_check=${ENV_PREFLIGHT_TRANSPORT_CHECK} env_preflight_tool=${ENV_PREFLIGHT_TOOL}"
  log "Config: network_guard_mode=${NETWORK_GUARD_MODE} research_network_guard_policy=${RESEARCH_NETWORK_GUARD_POLICY} research_network_policy_exception=${RESEARCH_NETWORK_POLICY_EXCEPTION} network_guard_tool=${NETWORK_GUARD_TOOL}"
  local outage_probe_cmd_set="false"
  if [ -n "$(trim "${NETWORK_OUTAGE_PROBE_CMD:-}")" ]; then
    outage_probe_cmd_set="true"
  fi
  log "Config: outage_resilience_mode=${NETWORK_OUTAGE_RESILIENCE_MODE} outage_wait_initial_secs=${NETWORK_OUTAGE_WAIT_INITIAL_SECS} outage_wait_max_secs=${NETWORK_OUTAGE_WAIT_MAX_SECS} outage_max_probes=${NETWORK_OUTAGE_MAX_PROBES} outage_probe_timeout_secs=${NETWORK_OUTAGE_PROBE_TIMEOUT_SECS} outage_probe_host=${NETWORK_OUTAGE_PROBE_HOST} outage_probe_port=${NETWORK_OUTAGE_PROBE_PORT} outage_probe_cmd_set=${outage_probe_cmd_set} outage_policy=${NETWORK_OUTAGE_POLICY} outage_route_to_blocker=${NETWORK_OUTAGE_ROUTE_TO_BLOCKER} outage_route_to_incident=${NETWORK_OUTAGE_ROUTE_TO_INCIDENT}"
  log "Config: spec_rounds=${SPEC_INTERROGATION_ROUNDS} phase_rounds=${PHASE_INTERROGATION_ROUNDS} incident_fixspec_rounds=${INCIDENT_FIXSPEC_INTERROGATION_ROUNDS} spec_quality_threshold=${SPEC_QUALITY_THRESHOLD} phase_assumptions_budget=${PHASE_ASSUMPTIONS_BUDGET} spec_quality_fail_max=${SPEC_QUALITY_FAIL_MAX} spec_decomposition_governance=${SPEC_DECOMPOSITION_GOVERNANCE} taskmaster_profile=${TASKMASTER_COMPLEXITY_PROFILE_RESOLVED} profile_source=${TASKMASTER_COMPLEXITY_PROFILE_SOURCE} profile_evidence=${TASKMASTER_COMPLEXITY_PROFILE_EVIDENCE} taskmaster_min_cards=${TASKMASTER_MIN_CARDS_PER_SPEC} taskmaster_max_cards=${TASKMASTER_MAX_CARDS_PER_SPEC} taskmaster_target_cards=${TASKMASTER_TARGET_CARDS_PER_SPEC} taskmaster_min_total_cards=${TASKMASTER_MIN_TOTAL_CARDS} taskmaster_target_total_cards=${TASKMASTER_TARGET_TOTAL_CARDS} taskcard_target_shortfall_mode=${TASKCARD_TARGET_SHORTFALL_MODE} taskcard_format_strict=${TASKCARD_FORMAT_STRICT} taskcard_execution_template=${TASKCARD_ENFORCE_EXECUTION_TEMPLATE} taskcard_phase_workplan_coverage=${TASKCARD_PHASE_WORKPLAN_COVERAGE} taskcard_max_phase_steps_per_card=${TASKCARD_MAX_PHASE_STEPS_PER_CARD} taskcard_scope_lint=${TASKCARD_SCOPE_LINT}"
  log "Contracts: taskmaster/taskaudit use agents/tools/lint_task_cards.py + agents/tools/merge_pending_family.py (family batch merge) + agents/tools/merge_task.py (single-card compatibility)"
  log "Config: goal_intake=$GOAL_INTAKE_RUNNER/$GOAL_INTAKE_MODEL effort=$GOAL_INTAKE_EFFORT"
  log "Config: objective_profile_sync=$OBJECTIVE_PROFILE_SYNC_RUNNER/$OBJECTIVE_PROFILE_SYNC_MODEL effort=$OBJECTIVE_PROFILE_SYNC_EFFORT"
  log "Config: spec_synthesis=$SPEC_SYNTHESIS_RUNNER/$SPEC_SYNTHESIS_MODEL effort=$SPEC_SYNTHESIS_EFFORT"
  log "Config: spec_review=$SPEC_REVIEW_RUNNER/$SPEC_REVIEW_MODEL effort=$SPEC_REVIEW_EFFORT"
  log "Config: taskmaster=$TASKMASTER_RUNNER/$TASKMASTER_MODEL effort=$TASKMASTER_EFFORT"
  log "Config: taskaudit=$TASKAUDIT_RUNNER/$TASKAUDIT_MODEL effort=$TASKAUDIT_EFFORT"
  log "Config: audit_intake=$AUDIT_INTAKE_RUNNER/$AUDIT_INTAKE_MODEL effort=$AUDIT_INTAKE_EFFORT"
  log "Config: audit_validate=$AUDIT_VALIDATE_RUNNER/$AUDIT_VALIDATE_MODEL effort=$AUDIT_VALIDATE_EFFORT"
  log "Config: audit_gatekeeper=$AUDIT_GATEKEEPER_RUNNER/$AUDIT_GATEKEEPER_MODEL effort=$AUDIT_GATEKEEPER_EFFORT"
  log "Config: mechanic=$MECHANIC_RUNNER/$MECHANIC_MODEL effort=$MECHANIC_EFFORT"
  log "Config: USAGE_AUTOPAUSE_MODE=${USAGE_AUTOPAUSE_MODE:-off} RESEARCH_WEEKLY_USAGE_REMAINING_THRESHOLD=${RESEARCH_WEEKLY_USAGE_REMAINING_THRESHOLD:-} RESEARCH_WEEKLY_USAGE_CONSUMED_THRESHOLD=${RESEARCH_WEEKLY_USAGE_CONSUMED_THRESHOLD:-} RESEARCH_WEEKLY_USAGE_THRESHOLD=${RESEARCH_WEEKLY_USAGE_THRESHOLD:-} RESEARCH_WEEKLY_REFRESH_UTC=${RESEARCH_WEEKLY_REFRESH_UTC:-MON 00:00} RESEARCH_USAGE_SEMANTICS=${research_usage_semantics:-none} RESEARCH_USAGE_THRESHOLD_SOURCE=${research_usage_source:-none}"
  log "Contracts: state=$RESEARCH_STATE events=$RESEARCH_EVENTS specs_index=$SPECS_INDEX task_provenance=$TASK_PROVENANCE interrogation_state=$INTERROGATION_STATE golden_versions=$GOLDEN_VERSION_REGISTRY spec_quality_state=$SPEC_QUALITY_STATE"

  refresh_research_weekly_usage_current "startup"
  pause_until_research_weekly_refresh_if_needed "startup"
}

parse_args "$@"
preflight

exit_code="$EXIT_OK"
if [ "$MODE" = "once" ]; then
  run_once || exit_code=$?
else
  run_forever || exit_code=$?
fi
exit "$exit_code"
