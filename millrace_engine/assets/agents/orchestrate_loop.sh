#!/usr/bin/env bash
set -euo pipefail

# Foreground orchestrator loop (local runner).
#
# This script drains agents/tasksbacklog.md by promoting cards into agents/tasks.md,
# then running Builder/Integration/QA (and optional Hotfix/Doublecheck) headlessly.
#
# IMPORTANT:
# - This is a *local* orchestration tool. It intentionally does not rely on a single
#   chat "turn" staying alive for hours/days.
# - It uses the standard stage prompt strings from agents/_orchestrate.md.
# - It uses agents/status.md as the signaling contract.
# - Canonical marker ownership and unknown marker policy: agents/status_contract.md.
# - On hard blockers, it runs deterministic escalation:
#   Troubleshooter -> Consult -> NEEDS_RESEARCH quarantine.

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

TASKS="agents/tasks.md"
BACKLOG="agents/tasksbacklog.md"
MANUAL_BACKLOG="agents/tasksbackburner.md"
BLOCKER_BACKLOG="agents/tasksblocker.md"
ARCHIVE="agents/tasksarchive.md"
STATUS="agents/status.md"
HISTORY="agents/historylog.md"
SIZE_STATUS="agents/size_status.md"
AUTONOMY_COMPLETE_MARKER="agents/AUTONOMY_COMPLETE"

MODEL_CFG="agents/options/model_config.md"
WF_CFG="agents/options/workflow_config.md"

RUNS_DIR="agents/runs"
DIAGS_DIR="agents/diagnostics"
TMP_DIR="agents/.tmp"
TASK_STORE_LOCK_FILE="$TMP_DIR/task_store.lock"
ESCALATION_STATE_FILE="$TMP_DIR/escalation_state.json"
RESEARCH_RECOVERY_LATCH_FILE="$TMP_DIR/research_recovery_latch.json"
INCIDENTS_ROOT_DIR="agents/ideas/incidents"
INCIDENTS_INCOMING_DIR="$INCIDENTS_ROOT_DIR/incoming"
AUDIT_INCOMING_DIR="agents/ideas/audit/incoming"
NEEDS_RESEARCH_QUARANTINE_MODE="${NEEDS_RESEARCH_QUARANTINE_MODE:-full}"
STAGING_REPO_DIR="${STAGING_REPO_DIR:-}"
STAGING_MANIFEST_PATH="agents/staging_manifest.yml"
STAGING_SYNC_TOOL="agents/tools/staging_sync.sh"
STAGING_COMMIT_TOOL="agents/tools/staging_commit.sh"
DIAG_REDACT_TOOL="agents/tools/redact_bundle.py"
DIAG_REDACTION_LAST_ERROR=""
USAGE_SAMPLER_TOOL="agents/tools/usage_sampler.py"
CODEX_EXEC_USAGE_TOOL="agents/tools/extract_codex_exec_usage.py"
USAGE_STATE_FILE="$TMP_DIR/usage_state.json"
CODEX_AUTH_SOURCE_DIR="${CODEX_AUTH_SOURCE_DIR:-$HOME/.codex}"
CODEX_RUNTIME_HOME="${CODEX_RUNTIME_HOME:-$TMP_DIR/codex-runtime-home}"
EMPTY_BACKLOG_AUDIT_HANDOFF_STAMP="$TMP_DIR/orch_empty_backlog_audit_handoff.stamp"
STAGE_FAILURE_CLASSIFIER_TOOL="agents/tools/classify_stage_failure.py"
ENV_PREFLIGHT_TOOL="agents/tools/env_preflight.py"
NETWORK_GUARD_TOOL="agents/tools/network_guard.sh"
SMALL_UPDATE_PROMPT_FILE="agents/prompts/small_update.md"
LARGE_UPDATE_PROMPT_FILE="agents/prompts/large_update.md"
LARGE_BUILDER_PLAN_ENTRYPOINT="agents/_start_large_plan.md"
LARGE_BUILDER_EXEC_ENTRYPOINT="agents/_start_large_execute.md"
LARGE_REASSESS_PROMPT_FILE="agents/prompts/reassess.md"
LARGE_REFACTOR_ENTRYPOINT="agents/_refactor.md"
LARGE_QA_PLAN_ENTRYPOINT="agents/_qa_plan.md"
LARGE_QA_EXEC_ENTRYPOINT="agents/_qa_execute.md"
LARGE_PLAN_COMPLETE_STATUS="### LARGE_PLAN_COMPLETE"
LARGE_EXECUTE_COMPLETE_STATUS="### LARGE_EXECUTE_COMPLETE"
LARGE_REASSESS_COMPLETE_STATUS="### LARGE_REASSESS_COMPLETE"
LARGE_REFACTOR_COMPLETE_STATUS="### LARGE_REFACTOR_COMPLETE"
# Retention targets: keep last 100 run folders and keep last 25 diagnostics bundles.
RUNS_RETENTION_KEEP="${RUNS_RETENTION_KEEP:-100}"
DIAGS_RETENTION_KEEP="${DIAGS_RETENTION_KEEP:-25}"

HEARTBEAT_SECS="${HEARTBEAT_SECS:-60}"
NOTIFY_ON_BLOCKER="${NOTIFY_ON_BLOCKER:-true}"
DAEMON_MODE="${DAEMON_MODE:-true}"
IDLE_MODE="${IDLE_MODE:-auto}"
IDLE_POLL_SECS="${IDLE_POLL_SECS:-60}"
IDLE_DEBOUNCE_SECS="${IDLE_DEBOUNCE_SECS:-300}"
ORCH_ALLOW_SEARCH="${ORCH_ALLOW_SEARCH:-off}"
ORCH_ALLOW_SEARCH_EXCEPTION="${ORCH_ALLOW_SEARCH_EXCEPTION:-off}"
ORCH_NETWORK_POLICY_EXCEPTION="${ORCH_NETWORK_POLICY_EXCEPTION:-off}"
ORCH_SEARCH_ENABLED="true"
ENV_PREFLIGHT_MODE="${ENV_PREFLIGHT_MODE:-on}"
ENV_PREFLIGHT_TRANSPORT_CHECK="${ENV_PREFLIGHT_TRANSPORT_CHECK:-on}"
NETWORK_GUARD_MODE="${NETWORK_GUARD_MODE:-off}"
ORCH_NETWORK_GUARD_POLICY="${ORCH_NETWORK_GUARD_POLICY:-deny}"
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
IDLE_WATCH_TOOL=""
TIMEOUT_BIN="timeout"

SCRIPT_START_EPOCH="$(date +%s)"
TASKS_COMPLETED=0
TASKS_DEMOTED=0
CARD_DEMOTED=0
LAST_RUN_FAILURE_KIND="none"
LAST_RUN_MODEL=""
POST_QA_UPDATE_FAILURE_REASON=""

ensure_repo_root() {
  # Long-running loops can end up with an "unlinked" cwd (e.g., WSL/drive remounts),
  # which breaks tools that call getcwd(3) (git, gh, codex). Re-anchor to repo root.
  cd "$REPO_ROOT" 2>/dev/null || {
    echo "FATAL: unable to cd to repo root: $REPO_ROOT" >&2
    exit 1
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
    exit 1
  }
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

spawn_with_timeout() {
  local seconds="$1"
  shift
  if [ -n "$TIMEOUT_BIN" ]; then
    "$TIMEOUT_BIN" "$seconds" "$@"
  else
    run_with_python_timeout "$seconds" "$@"
  fi
}

log() {
  local ts
  ts="$(date '+%F %T')"
  printf '[%s] %s\n' "$ts" "$*" >&2
}

wait_with_heartbeat() {
  local pid="$1"
  local label="$2"
  local started_at="$3"
  local heartbeat_secs="$4"
  local next_hb now elapsed

  if ! [[ "$heartbeat_secs" =~ ^[0-9]+$ ]]; then
    heartbeat_secs=60
  fi

  if [ "$heartbeat_secs" -le 0 ]; then
    wait "$pid"
    return $?
  fi

  next_hb=$(( started_at + heartbeat_secs ))
  while kill -0 "$pid" >/dev/null 2>&1; do
    now="$(date +%s)"
    if [ "$now" -ge "$next_hb" ]; then
      elapsed=$(( now - started_at ))
      log "Stage $label: still running (${elapsed}s elapsed)"
      next_hb=$(( now + heartbeat_secs ))
    fi
    sleep 1
  done

  wait "$pid"
  return $?
}

format_duration() {
  local total="${1:-0}"
  if ! [[ "$total" =~ ^[0-9]+$ ]]; then
    total=0
  fi
  local h m s
  h=$(( total / 3600 ))
  m=$(( (total % 3600) / 60 ))
  s=$(( total % 60 ))
  printf '%02dh%02dm%02ds' "$h" "$m" "$s"
}

migrate_legacy_incidents_to_incoming() {
  python3 - "$INCIDENTS_ROOT_DIR" "$INCIDENTS_INCOMING_DIR" <<'PY'
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

trim() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"  # leading
  s="${s%"${s##*[![:space:]]}"}"  # trailing
  printf '%s' "$s"
}

now_run_id() { date +%F_%H%M%S; }

read_status() {
  if [ -f "$STATUS" ]; then
    # Some sub-agents may mistakenly append instead of overwriting. Treat the last
    # marker line as authoritative.
    local st
    st="$(awk '/^### /{st=$0} END{print st}' "$STATUS" | tr -d '\r')"
    st="$(trim "$st")"
    if [ -n "$st" ]; then
      printf '%s\n' "$st"
    else
      head -n 1 "$STATUS" | tr -d '\r'
    fi
  else
    echo "### IDLE"
  fi
}

set_status() {
  printf '%s\n' "$1" >"$STATUS"
}

autonomy_complete_marker_present() {
  [ -f "$AUTONOMY_COMPLETE_MARKER" ]
}

normalize_size_status() {
  local raw upper
  raw="$(trim "${1:-}")"
  raw="${raw#\#\#\#}"
  raw="$(trim "$raw")"
  upper="$(printf '%s' "$raw" | tr '[:lower:]' '[:upper:]')"
  case "$upper" in
    LARGE) printf 'LARGE' ;;
    SMALL|"") printf 'SMALL' ;;
    *) printf 'SMALL' ;;
  esac
}

ensure_size_status_file() {
  if [ ! -f "$SIZE_STATUS" ]; then
    printf '### SMALL\n' >"$SIZE_STATUS"
  fi
}

current_size_status() {
  local raw
  ensure_size_status_file
  raw="$(awk '/^### /{st=$0} END{print st}' "$SIZE_STATUS" | tr -d '\r')"
  if [ -z "$raw" ]; then
    raw="$(head -n 1 "$SIZE_STATUS" | tr -d '\r')"
  fi
  normalize_size_status "$raw"
}

is_known_large_builder_stage_status() {
  local st
  st="$(trim "${1:-}")"
  case "$st" in
    "$LARGE_PLAN_COMPLETE_STATUS"|"$LARGE_EXECUTE_COMPLETE_STATUS"|"$LARGE_REASSESS_COMPLETE_STATUS"|"$LARGE_REFACTOR_COMPLETE_STATUS")
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_unknown_large_builder_stage_status() {
  local st
  st="$(trim "${1:-}")"
  case "$st" in
    "### LARGE_"*)
      if is_known_large_builder_stage_status "$st"; then
        return 1
      fi
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

large_builder_next_stage_from_status() {
  local st
  st="$(trim "${1:-### IDLE}")"
  case "$st" in
    "### IDLE") printf 'plan' ;;
    "$LARGE_PLAN_COMPLETE_STATUS") printf 'execute' ;;
    "$LARGE_EXECUTE_COMPLETE_STATUS") printf 'reassess' ;;
    "$LARGE_REASSESS_COMPLETE_STATUS") printf 'refactor' ;;
    "$LARGE_REFACTOR_COMPLETE_STATUS"|"### BUILDER_COMPLETE") printf 'complete' ;;
    *)
      printf 'invalid'
      ;;
  esac
}

update_prompt_for_size_status() {
  local size
  size="$(normalize_size_status "${1:-SMALL}")"
  case "$size" in
    LARGE) printf 'Open %s and follow instructions.' "$LARGE_UPDATE_PROMPT_FILE" ;;
    *) printf 'Open %s and follow instructions.' "$SMALL_UPDATE_PROMPT_FILE" ;;
  esac
}

update_effort_for_size_status() {
  local size
  size="$(normalize_size_status "${1:-SMALL}")"
  case "$size" in
    LARGE) printf 'high' ;;
    *) printf 'medium' ;;
  esac
}

repo_size_metrics() {
  python3 - "$REPO_ROOT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
skip_names = {".git", "staging", "node_modules", ".venv", "venv", "__pycache__"}
skip_prefixes = ("agents", "agents/runs", "agents/diagnostics", "agents/.tmp")

file_count = 0
nonempty_loc = 0

for path in root.rglob("*"):
    if not path.is_file():
        continue

    rel = path.relative_to(root).as_posix()
    parts = rel.split("/")
    if any(part in skip_names for part in parts):
        continue
    if any(rel == prefix or rel.startswith(prefix + "/") for prefix in skip_prefixes):
        continue

    file_count += 1
    try:
        data = path.read_bytes()
    except Exception:
        continue

    if b"\x00" in data:
        continue

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("latin-1")
        except Exception:
            continue

    nonempty_loc += sum(1 for line in text.splitlines() if line.strip())

print(f"{file_count} {nonempty_loc}")
PY
}

maybe_latch_large_repo_size_mode() {
  local run_dir="$1"
  local source_stage="$2"
  local size_status metrics files loc files_hit=false loc_hit=false

  size_status="$(current_size_status)"
  if [ "$size_status" = "LARGE" ]; then
    write_runner_note "$run_dir" "Size check: skipped source=$source_stage status=LARGE (latched)"
    return 0
  fi

  metrics="$(repo_size_metrics 2>/dev/null || true)"
  files="$(printf '%s\n' "$metrics" | awk '{print $1}')"
  loc="$(printf '%s\n' "$metrics" | awk '{print $2}')"
  if ! [[ "$files" =~ ^[0-9]+$ ]] || ! [[ "$loc" =~ ^[0-9]+$ ]]; then
    write_runner_note "$run_dir" "Size check: failed source=$source_stage reason=metric-parse thresholds(files=$LARGE_FILES_THRESHOLD loc=$LARGE_LOC_THRESHOLD)"
    return 0
  fi

  if [ "$files" -ge "$LARGE_FILES_THRESHOLD" ]; then
    files_hit=true
  fi
  if [ "$loc" -ge "$LARGE_LOC_THRESHOLD" ]; then
    loc_hit=true
  fi

  if [ "$files_hit" = true ] || [ "$loc_hit" = true ]; then
    printf '### LARGE\n' >"$SIZE_STATUS"
    write_runner_note "$run_dir" "Size check: latched LARGE source=$source_stage files=$files/$LARGE_FILES_THRESHOLD loc=$loc/$LARGE_LOC_THRESHOLD"
    log "Size mode latched to LARGE: source=$source_stage files=$files/$LARGE_FILES_THRESHOLD loc=$loc/$LARGE_LOC_THRESHOLD"
  else
    write_runner_note "$run_dir" "Size check: remains SMALL source=$source_stage files=$files/$LARGE_FILES_THRESHOLD loc=$loc/$LARGE_LOC_THRESHOLD"
  fi
}

has_active_card() { rg -n '^## ' -q "$TASKS"; }
has_backlog_cards() { rg -n '^## ' -q "$BACKLOG"; }
has_research_recovery_latch() { [ -f "$RESEARCH_RECOVERY_LATCH_FILE" ]; }
has_orchestrate_progress() { [ "$TASKS_COMPLETED" -gt 0 ] || [ "$TASKS_DEMOTED" -gt 0 ]; }

should_emit_research_audit_handoff_on_empty_backlog() {
  local audit_trigger
  audit_trigger="$(printf '%s' "${AUDIT_TRIGGER:-queue_empty}" | tr '[:upper:]' '[:lower:]')"

  if [ "$audit_trigger" != "queue_empty" ]; then
    return 1
  fi

  if ! has_orchestrate_progress; then
    return 1
  fi

  return 0
}

emit_research_audit_handoff_on_empty_backlog() {
  local backlog_mtime="${1:-0}"
  local stamp_payload previous_stamp now_iso now_compact audit_id ticket_path

  stamp_payload="empty_transition_after_progress completed=$TASKS_COMPLETED demoted=$TASKS_DEMOTED"
  if [ -f "$EMPTY_BACKLOG_AUDIT_HANDOFF_STAMP" ]; then
    previous_stamp="$(tr -d '\r' <"$EMPTY_BACKLOG_AUDIT_HANDOFF_STAMP" || true)"
    if [ "$previous_stamp" = "$stamp_payload" ]; then
      return 0
    fi
  fi

  if ! mkdir -p "$AUDIT_INCOMING_DIR" "$TMP_DIR"; then
    log "WARN: unable to prepare audit handoff directories for backlog-empty transition"
    return 0
  fi

  now_iso="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  now_compact="$(date -u '+%Y%m%dT%H%M%SZ')"
  audit_id="AUD-ORCH-BACKLOG-EMPTY-${now_compact}-${backlog_mtime}"
  ticket_path="$AUDIT_INCOMING_DIR/$audit_id.md"

  if ! cat >"$ticket_path" <<EOF
---
audit_id: $audit_id
scope: orchestration-loop-backlog-empty-handoff
trigger: queue_empty
status: incoming
owner: research-loop
created_at: $now_iso
updated_at: $now_iso
---

## Objective
- Run a research audit cycle after orchestrator observed an empty execution backlog.

## Inputs
- \`agents/tasks.md\`
- \`agents/tasksbacklog.md\`
- \`agents/taskspending.md\`
- \`agents/specs/stable/\`
- \`agents/reports/marathon_results.md\` (if present)

## Checks
- Active task, backlog, and pending queues contain no real task cards.
- Completion-vs-spec audit is re-evaluated with current repo state.
- Any uncovered gaps are routed into remediation task cards.

## Findings
- Trigger emitted by orchestrator daemon on backlog-empty transition.

## Evidence
- Orchestrator logs showing backlog-empty handoff emission.
- Research events showing \`AUDIT_INTAKE\`, \`MARATHON_AUDIT_RESULT\`, and follow-up outcomes.

## Decision
- Pending (research audit will record PASS/FAIL).

## Follow-ups
- If FAIL or BLOCKED, enqueue remediation cards and rerun audit.
EOF
  then
    rm -f "$ticket_path" >/dev/null 2>&1 || true
    log "WARN: failed to write backlog-empty audit handoff ticket at $ticket_path"
    return 0
  fi

  if ! printf '%s\n' "$stamp_payload" >"$EMPTY_BACKLOG_AUDIT_HANDOFF_STAMP"; then
    log "WARN: failed to persist backlog-empty handoff stamp at $EMPTY_BACKLOG_AUDIT_HANDOFF_STAMP"
  fi
  log "Handoff: queued research audit ticket on backlog-empty transition: $ticket_path"
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
  local quiet="${2:-$IDLE_DEBOUNCE_SECS}"

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

wait_for_backlog_change() {
  local previous_mtime="${1:-0}"
  local mode="$IDLE_MODE"
  local backlog_dir backlog_base event_file current_mtime

  backlog_dir="$(dirname "$BACKLOG")"
  backlog_base="$(basename "$BACKLOG")"

  while true; do
    current_mtime="$(stat_mtime "$BACKLOG")"
    if [ "$current_mtime" != "$previous_mtime" ]; then
      return 0
    fi

    case "$mode" in
      watch)
        if [ "$IDLE_WATCH_TOOL" = "inotifywait" ]; then
          while true; do
            event_file="$(inotifywait -q -e close_write,create,delete,moved_to,move --format '%f' "$backlog_dir" 2>/dev/null || true)"
            if [ "$event_file" = "$backlog_base" ]; then
              break
            fi
          done
        elif [ "$IDLE_WATCH_TOOL" = "fswatch" ]; then
          fswatch -1 "$backlog_dir" >/dev/null 2>&1 || true
        else
          sleep "$IDLE_POLL_SECS"
        fi
        ;;
      auto)
        if [ "$IDLE_WATCH_TOOL" = "inotifywait" ]; then
          while true; do
            event_file="$(inotifywait -q -e close_write,create,delete,moved_to,move --format '%f' "$backlog_dir" 2>/dev/null || true)"
            if [ "$event_file" = "$backlog_base" ]; then
              break
            fi
          done
        elif [ "$IDLE_WATCH_TOOL" = "fswatch" ]; then
          fswatch -1 "$backlog_dir" >/dev/null 2>&1 || true
        else
          sleep "$IDLE_POLL_SECS"
        fi
        ;;
      poll)
        sleep "$IDLE_POLL_SECS"
        ;;
      *)
        sleep "$IDLE_POLL_SECS"
        ;;
    esac
  done
}

active_task_heading() {
  sed -n 's/^##[[:space:]]*//p' "$TASKS" | head -n 1 | tr -d '\r'
}

promote_next_card() {
  python3 - "$BACKLOG" "$TASKS" "$TASK_STORE_LOCK_FILE" <<'PY'
from pathlib import Path
import fcntl
import re, sys
backlog = Path(sys.argv[1])
tasks = Path(sys.argv[2])
lock_path = Path(sys.argv[3])
lock_path.parent.mkdir(parents=True, exist_ok=True)
with lock_path.open("a+", encoding="utf-8") as lock_fp:
  fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
  text = backlog.read_text(encoding="utf-8", errors="replace")
  m = re.search(r"^##\s+.*$", text, flags=re.M)
  if not m:
    print("NO_TASKS")
    raise SystemExit(0)
  start = m.start()
  m2 = re.search(r"^##\s+.*$", text[m.end():], flags=re.M)
  end = (m.end() + m2.start()) if m2 else len(text)
  card = text[start:end].rstrip() + "\n"
  new_text = (text[:start].rstrip() + "\n\n" + text[end:].lstrip("\n")).rstrip() + "\n"

  def has_real_active_card(raw: str) -> bool:
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
      return False
    return bool(re.search(r"^##\s+.+$", normalized, flags=re.M))

  if has_real_active_card(tasks.read_text(encoding="utf-8", errors="replace")):
    raise SystemExit("agents/tasks.md is not empty; refusing to overwrite")
  tasks.write_text(card, encoding="utf-8")
  backlog.write_text(new_text, encoding="utf-8")
  print(card.splitlines()[0])
PY
}

archive_active_card_and_clear() {
  python3 - "$TASKS" "$ARCHIVE" "$TASK_STORE_LOCK_FILE" <<'PY'
from pathlib import Path
import fcntl
import sys
tasks = Path(sys.argv[1])
archive = Path(sys.argv[2])
lock_path = Path(sys.argv[3])
lock_path.parent.mkdir(parents=True, exist_ok=True)
with lock_path.open("a+", encoding="utf-8") as lock_fp:
  fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
  card = tasks.read_text(encoding="utf-8", errors="replace").strip("\n")
  if not card.strip():
    raise SystemExit("No active card to archive")
  existing = archive.read_text(encoding="utf-8", errors="replace") if archive.exists() else ""
  archive.write_text(card.rstrip() + "\n\n" + existing.lstrip("\n"), encoding="utf-8")
  tasks.write_text("", encoding="utf-8")
PY
}

demote_active_card_to_manual_backlog_and_clear() {
  local run_dir="$1"
  local stage="$2"
  local why="$3"
  local diag_dir="$4"
  local status_at_demote="$5"

  python3 - "$TASKS" "$MANUAL_BACKLOG" "$run_dir" "$stage" "$why" "$diag_dir" "$status_at_demote" "$TASK_STORE_LOCK_FILE" <<'PY'
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import fcntl
import sys

tasks_path = Path(sys.argv[1])
manual_backlog_path = Path(sys.argv[2])
run_dir = sys.argv[3]
stage = sys.argv[4]
why = sys.argv[5]
diag_dir = sys.argv[6]
status_at_demote = sys.argv[7]
lock_path = Path(sys.argv[8])

lock_path.parent.mkdir(parents=True, exist_ok=True)
with lock_path.open("a+", encoding="utf-8") as lock_fp:
    fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)

    card = tasks_path.read_text(encoding="utf-8", errors="replace").strip("\n")
    if not card.strip():
        raise SystemExit("No active card to demote (agents/tasks.md empty)")

    lines = card.splitlines()
    heading_idx = 0
    for idx, line in enumerate(lines):
        if line.startswith("## "):
            heading_idx = idx
            break

    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    meta = [
        "",
        "### Auto-demoted (BLOCKED)",
        "",
        f"- Stage: `{stage}`",
        f"- Why: {why}",
        f"- Run dir: `{run_dir}`",
        f"- Diagnostics: `{diag_dir}`",
        f"- Status at demotion: `{status_at_demote}`",
        f"- Timestamp: `{ts}`",
        "",
        "---",
        "",
    ]

    new_lines = lines[: heading_idx + 1] + meta + lines[heading_idx + 1 :]
    new_card = "\n".join(new_lines).rstrip() + "\n"

    section_heading = "### Auto-demoted blocked cards (from orchestrate_loop.sh)"
    manual_text = manual_backlog_path.read_text(encoding="utf-8", errors="replace") if manual_backlog_path.exists() else ""
    manual_lines = manual_text.splitlines()

    if section_heading not in manual_lines:
        insert_after_hr = None
        for i, line in enumerate(manual_lines):
            if line.strip() == "---":
                insert_after_hr = i + 1
                break
        if insert_after_hr is None:
            insert_after_hr = len(manual_lines)
        manual_lines[insert_after_hr:insert_after_hr] = ["", section_heading, ""]

    sec_idx = manual_lines.index(section_heading)
    insert_at = sec_idx + 1
    if insert_at >= len(manual_lines) or manual_lines[insert_at].strip() != "":
        manual_lines.insert(insert_at, "")
        insert_at += 1

    card_lines = new_card.rstrip("\n").splitlines()
    manual_lines[insert_at:insert_at] = card_lines + [""]

    manual_backlog_path.write_text("\n".join(manual_lines).rstrip() + "\n", encoding="utf-8")
    tasks_path.write_text("", encoding="utf-8")
PY
}

ensure_task_store_scaffolds_locked() {
  python3 - "$TASKS" "$BACKLOG" "$MANUAL_BACKLOG" "$BLOCKER_BACKLOG" "$TASK_STORE_LOCK_FILE" <<'PY'
from pathlib import Path
import fcntl
import sys

tasks_path = Path(sys.argv[1])
backlog_path = Path(sys.argv[2])
backburner_path = Path(sys.argv[3])
blocker_path = Path(sys.argv[4])
lock_path = Path(sys.argv[5])

backburner_scaffold = (
    "# Tasks Backburner\n\n"
    "Backlog for cards auto-demoted by the local orchestrator after hard blockers.\n"
    "Review and triage these cards before re-adding them to `agents/tasksbacklog.md`.\n\n"
    "---\n\n"
    "### Auto-demoted blocked cards (from orchestrate_loop.sh)\n\n"
)
blocker_scaffold = (
    "# Tasks Blocker Queue\n\n"
    "Blocked task cards are quarantined here with evidence pointers and deterministic next actions.\n"
    "This file is prepend-first (newest blocker entries at the top).\n\n"
    "## Entry Template\n\n"
    "## YYYY-MM-DD HH:MM:SS UTC — <Task Title>\n\n"
    "- **Status:** `### BLOCKED` | `### CONSULT_COMPLETE` | `### NEEDS_RESEARCH`\n"
    "- **Stage blocked:** <Builder | QA | Quickfix | Doublecheck | Consult>\n"
    "- **Source task card:** <path and heading>\n"
    "- **Prompt artifact:** <path, if available>\n"
    "- **Evidence:**\n"
    "  - Runs: `agents/runs/<RUN_ID>/`\n"
    "  - Diagnostics: `agents/diagnostics/<TIMESTAMP>/` (if available)\n"
    "  - Quickfix/expectations: <paths, if available>\n"
    "- **Root-cause summary:** <short diagnosis>\n"
    "- **Deterministic next action:** <exact next stage/entrypoint>\n"
    "- **Incident intake:** `agents/ideas/incidents/incoming/INC-<fingerprint>.md` (required when status is `### NEEDS_RESEARCH`)\n"
    "- **Notes:** <constraints, handoff details, or unresolved risks>\n"
)

lock_path.parent.mkdir(parents=True, exist_ok=True)
with lock_path.open("a+", encoding="utf-8") as lock_fp:
    fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
    if not tasks_path.exists():
        tasks_path.parent.mkdir(parents=True, exist_ok=True)
        tasks_path.write_text("", encoding="utf-8")
    if not backlog_path.exists():
        backlog_path.parent.mkdir(parents=True, exist_ok=True)
        backlog_path.write_text("", encoding="utf-8")
    if not backburner_path.exists():
        backburner_path.parent.mkdir(parents=True, exist_ok=True)
        backburner_path.write_text(backburner_scaffold, encoding="utf-8")
    if not blocker_path.exists():
        blocker_path.parent.mkdir(parents=True, exist_ok=True)
        blocker_path.write_text(blocker_scaffold, encoding="utf-8")
PY
}

task_needs_integration() { rg -n '^\*\*Gates:\*\*.*INTEGRATION' -q "$TASKS"; }

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
        INTEGRATION_RUNNER) INTEGRATION_RUNNER="$value" ;;
        INTEGRATION_MODEL) INTEGRATION_MODEL="$value" ;;
        BUILDER_RUNNER) BUILDER_RUNNER="$value" ;;
        BUILDER_MODEL) BUILDER_MODEL="$value" ;;
        QA_RUNNER) QA_RUNNER="$value" ;;
        QA_MODEL) QA_MODEL="$value" ;;
        HOTFIX_RUNNER) HOTFIX_RUNNER="$value" ;;
        HOTFIX_MODEL) HOTFIX_MODEL="$value" ;;
        DOUBLECHECK_RUNNER) DOUBLECHECK_RUNNER="$value" ;;
        DOUBLECHECK_MODEL) DOUBLECHECK_MODEL="$value" ;;
        UPDATE_RUNNER) UPDATE_RUNNER="$value" ;;
        UPDATE_MODEL) UPDATE_MODEL="$value" ;;
        TROUBLESHOOT_RUNNER) TROUBLESHOOT_RUNNER="$value" ;;
        TROUBLESHOOT_MODEL) TROUBLESHOOT_MODEL="$value" ;;
        CONSULT_RUNNER) CONSULT_RUNNER="$value" ;;
        CONSULT_MODEL) CONSULT_MODEL="$value" ;;
        MODERATE_BUILDER_MODEL_CHAIN) MODERATE_BUILDER_MODEL_CHAIN="$value" ;;
        MODERATE_HOTFIX_MODEL_CHAIN) MODERATE_HOTFIX_MODEL_CHAIN="$value" ;;
        INVOLVED_BUILDER_MODEL_CHAIN) INVOLVED_BUILDER_MODEL_CHAIN="$value" ;;
        INVOLVED_HOTFIX_MODEL_CHAIN) INVOLVED_HOTFIX_MODEL_CHAIN="$value" ;;
        COMPLEX_BUILDER_MODEL_CHAIN) COMPLEX_BUILDER_MODEL_CHAIN="$value" ;;
        COMPLEX_HOTFIX_MODEL_CHAIN) COMPLEX_HOTFIX_MODEL_CHAIN="$value" ;;
        QA_MODERATE_MODEL) QA_MODERATE_MODEL="$value" ;;
        QA_MODERATE_EFFORT) QA_MODERATE_EFFORT="$value" ;;
        QA_INVOLVED_MODEL) QA_INVOLVED_MODEL="$value" ;;
        QA_INVOLVED_EFFORT) QA_INVOLVED_EFFORT="$value" ;;
        QA_COMPLEX_MODEL) QA_COMPLEX_MODEL="$value" ;;
        QA_COMPLEX_EFFORT) QA_COMPLEX_EFFORT="$value" ;;
        DOUBLECHECK_MODERATE_MODEL) DOUBLECHECK_MODERATE_MODEL="$value" ;;
        DOUBLECHECK_MODERATE_EFFORT) DOUBLECHECK_MODERATE_EFFORT="$value" ;;
        DOUBLECHECK_INVOLVED_MODEL) DOUBLECHECK_INVOLVED_MODEL="$value" ;;
        DOUBLECHECK_INVOLVED_EFFORT) DOUBLECHECK_INVOLVED_EFFORT="$value" ;;
        DOUBLECHECK_COMPLEX_MODEL) DOUBLECHECK_COMPLEX_MODEL="$value" ;;
        DOUBLECHECK_COMPLEX_EFFORT) DOUBLECHECK_COMPLEX_EFFORT="$value" ;;
        *) : ;;
      esac
    fi
  done <"$MODEL_CFG"

  local required=(BUILDER_RUNNER BUILDER_MODEL QA_RUNNER QA_MODEL HOTFIX_RUNNER HOTFIX_MODEL DOUBLECHECK_RUNNER DOUBLECHECK_MODEL)
  local k
  for k in "${required[@]}"; do
    if [ -z "${!k:-}" ]; then
      echo "Missing $k in $MODEL_CFG (Active config block)" >&2
      exit 1
    fi
  done

  # Integration defaults (older configs may omit these).
  : "${INTEGRATION_RUNNER:=$BUILDER_RUNNER}"
  : "${INTEGRATION_MODEL:=$BUILDER_MODEL}"

  # Troubleshooter defaults (older configs may omit these).
  : "${TROUBLESHOOT_RUNNER:=$BUILDER_RUNNER}"
  : "${TROUBLESHOOT_MODEL:=$BUILDER_MODEL}"

  # Consult defaults (older configs may omit these).
  : "${CONSULT_RUNNER:=$TROUBLESHOOT_RUNNER}"
  : "${CONSULT_MODEL:=$TROUBLESHOOT_MODEL}"

  : "${UPDATE_RUNNER:=codex}"
  : "${UPDATE_MODEL:=gpt-5.3-codex}"

  # Optional complexity-routing model defaults.
  : "${MODERATE_BUILDER_MODEL_CHAIN:=$BUILDER_MODEL}"
  : "${MODERATE_HOTFIX_MODEL_CHAIN:=$HOTFIX_MODEL}"
  : "${INVOLVED_BUILDER_MODEL_CHAIN:=$BUILDER_MODEL}"
  : "${INVOLVED_HOTFIX_MODEL_CHAIN:=$HOTFIX_MODEL}"
  : "${COMPLEX_BUILDER_MODEL_CHAIN:=$BUILDER_MODEL}"
  : "${COMPLEX_HOTFIX_MODEL_CHAIN:=$HOTFIX_MODEL}"

  : "${QA_MODERATE_MODEL:=$QA_MODEL}"
  : "${QA_MODERATE_EFFORT:=medium}"
  : "${QA_INVOLVED_MODEL:=$QA_MODEL}"
  : "${QA_INVOLVED_EFFORT:=high}"
  : "${QA_COMPLEX_MODEL:=$QA_MODEL}"
  : "${QA_COMPLEX_EFFORT:=xhigh}"

  : "${DOUBLECHECK_MODERATE_MODEL:=$QA_MODERATE_MODEL}"
  : "${DOUBLECHECK_MODERATE_EFFORT:=$QA_MODERATE_EFFORT}"
  : "${DOUBLECHECK_INVOLVED_MODEL:=$QA_INVOLVED_MODEL}"
  : "${DOUBLECHECK_INVOLVED_EFFORT:=$QA_INVOLVED_EFFORT}"
  : "${DOUBLECHECK_COMPLEX_MODEL:=$QA_COMPLEX_MODEL}"
  : "${DOUBLECHECK_COMPLEX_EFFORT:=$QA_COMPLEX_EFFORT}"
}

parse_workflow_config() {
  local line key value
  while IFS= read -r line || [ -n "$line" ]; do
    if [[ "$line" =~ ^##[[:space:]]*([A-Z0-9_]+)[[:space:]]*=[[:space:]]*(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      value="$(trim "${BASH_REMATCH[2]}")"
      case "$key" in
        INTEGRATION_MODE) INTEGRATION_MODE="$value" ;;
        INTEGRATION_COUNT) INTEGRATION_COUNT="$value" ;;
        INTEGRATION_TARGET) INTEGRATION_TARGET="$value" ;;
        HEADLESS_PERMISSIONS) HEADLESS_PERMISSIONS="$value" ;;
        OPENCLAW_GATEWAY_URL) OPENCLAW_GATEWAY_URL="$value" ;;
        OPENCLAW_AGENT_ID) OPENCLAW_AGENT_ID="$value" ;;
        COMPLEXITY_ROUTING) COMPLEXITY_ROUTING="$value" ;;
        RUN_UPDATE_ON_EMPTY) RUN_UPDATE_ON_EMPTY="$value" ;;
        AUDIT_TRIGGER) AUDIT_TRIGGER="$value" ;;
        ORCH_ALLOW_SEARCH) ORCH_ALLOW_SEARCH="$value" ;;
        ORCH_ALLOW_SEARCH_EXCEPTION) ORCH_ALLOW_SEARCH_EXCEPTION="$value" ;;
        ORCH_NETWORK_POLICY_EXCEPTION) ORCH_NETWORK_POLICY_EXCEPTION="$value" ;;
        USAGE_AUTOPAUSE_MODE) USAGE_AUTOPAUSE_MODE="$value" ;;
        USAGE_SAMPLER_PROVIDER) USAGE_SAMPLER_PROVIDER="$value" ;;
        USAGE_SAMPLER_CACHE_MAX_AGE_SECS) USAGE_SAMPLER_CACHE_MAX_AGE_SECS="$value" ;;
        USAGE_SAMPLER_ORCH_CMD) USAGE_SAMPLER_ORCH_CMD="$value" ;;
        USAGE_SAMPLER_RESEARCH_CMD) USAGE_SAMPLER_RESEARCH_CMD="$value" ;;
        CODEX_AUTH_SOURCE_DIR) CODEX_AUTH_SOURCE_DIR="$value" ;;
        CODEX_RUNTIME_HOME) CODEX_RUNTIME_HOME="$value" ;;
        ORCH_WEEKLY_USAGE_REMAINING_THRESHOLD) ORCH_WEEKLY_USAGE_REMAINING_THRESHOLD="$value" ;;
        ORCH_WEEKLY_USAGE_CONSUMED_THRESHOLD) ORCH_WEEKLY_USAGE_CONSUMED_THRESHOLD="$value" ;;
        ORCH_WEEKLY_USAGE_THRESHOLD) ORCH_WEEKLY_USAGE_THRESHOLD="$value" ;;
        ORCH_WEEKLY_REFRESH_UTC) ORCH_WEEKLY_REFRESH_UTC="$value" ;;
        ORCH_INTER_TASK_DELAY_MODE) ORCH_INTER_TASK_DELAY_MODE="$value" ;;
        ORCH_INTER_TASK_DELAY_SECS) ORCH_INTER_TASK_DELAY_SECS="$value" ;;
        NETWORK_GUARD_MODE) NETWORK_GUARD_MODE="$value" ;;
        ORCH_NETWORK_GUARD_POLICY) ORCH_NETWORK_GUARD_POLICY="$value" ;;
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
        ENV_PREFLIGHT_MODE) ENV_PREFLIGHT_MODE="$value" ;;
        ENV_PREFLIGHT_TRANSPORT_CHECK) ENV_PREFLIGHT_TRANSPORT_CHECK="$value" ;;
        LARGE_FILES_THRESHOLD) LARGE_FILES_THRESHOLD="$value" ;;
        LARGE_LOC_THRESHOLD) LARGE_LOC_THRESHOLD="$value" ;;
        *) : ;;
      esac
    fi
  done <"$WF_CFG"

  : "${INTEGRATION_MODE:=Low}"
  : "${INTEGRATION_COUNT:=0}"
  : "${INTEGRATION_TARGET:=0}"
  : "${HEADLESS_PERMISSIONS:=Maximum}"
  : "${OPENCLAW_GATEWAY_URL:=http://127.0.0.1:18789}"
  : "${OPENCLAW_AGENT_ID:=main}"
  : "${COMPLEXITY_ROUTING:=Off}"
  : "${RUN_UPDATE_ON_EMPTY:=On}"
  : "${AUDIT_TRIGGER:=queue_empty}"
  : "${ORCH_ALLOW_SEARCH:=off}"
  : "${ORCH_ALLOW_SEARCH_EXCEPTION:=off}"
  : "${ORCH_NETWORK_POLICY_EXCEPTION:=off}"
  : "${USAGE_AUTOPAUSE_MODE:=off}"
  : "${USAGE_SAMPLER_PROVIDER:=codex}"
  : "${USAGE_SAMPLER_CACHE_MAX_AGE_SECS:=900}"
  : "${USAGE_SAMPLER_ORCH_CMD:=}"
  : "${USAGE_SAMPLER_RESEARCH_CMD:=}"
  : "${CODEX_AUTH_SOURCE_DIR:=$HOME/.codex}"
  : "${CODEX_RUNTIME_HOME:=$TMP_DIR/codex-runtime-home}"
  : "${ORCH_WEEKLY_USAGE_REMAINING_THRESHOLD:=}"
  : "${ORCH_WEEKLY_USAGE_CONSUMED_THRESHOLD:=}"
  : "${ORCH_WEEKLY_USAGE_THRESHOLD:=}"
  : "${ORCH_WEEKLY_REFRESH_UTC:=MON 00:00}"
  : "${ORCH_INTER_TASK_DELAY_MODE:=}"
  : "${ORCH_INTER_TASK_DELAY_SECS:=0}"
  : "${NETWORK_GUARD_MODE:=off}"
  : "${ORCH_NETWORK_GUARD_POLICY:=deny}"
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
  : "${ENV_PREFLIGHT_MODE:=on}"
  : "${ENV_PREFLIGHT_TRANSPORT_CHECK:=on}"
  : "${LARGE_FILES_THRESHOLD:=999999999}"
  : "${LARGE_LOC_THRESHOLD:=999999999}"
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
      # codex-cli forbids combining --full-auto with --dangerously-bypass-approvals-and-sandbox.
      CODEX_PERM_FLAGS=(--dangerously-bypass-approvals-and-sandbox)
      CLAUDE_PERM_FLAGS=(--dangerously-skip-permissions)
      ;;
    *)
      echo "Unknown HEADLESS_PERMISSIONS: ${HEADLESS_PERMISSIONS}" >&2
      exit 1
      ;;
  esac
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

  # Minimal OpenResponses request. This assumes the Gateway supports /v1/responses.
  # If your OpenClaw install differs, update this function.
  local payload
  payload="$(python3 - <<PY
import json,sys
print(json.dumps({
  "model": "$model",
  "input": [{"role": "user", "content": [{"type": "text", "text": "$prompt"}]}],
  "metadata": {"openclaw_agent_id": "$OPENCLAW_AGENT_ID"},
}))
PY
)"

  curl -sS -X POST "$OPENCLAW_GATEWAY_URL/v1/responses" \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d "$payload" \
    >"$stdout_path" 2>"$stderr_path"

  # Best-effort extract to last_path (text). If parsing fails, still keep raw JSON in stdout.
  if [ -n "$last_path" ]; then
    python3 - "$stdout_path" "$last_path" <<'PY'
import json,sys
src, dst = sys.argv[1], sys.argv[2]
try:
  data=json.load(open(src,'r',encoding='utf-8'))
except Exception:
  raise SystemExit(0)
text=[]
out=data.get("output") or []
for item in out:
  c=item.get("content") or []
  for part in c:
    if part.get("type")=="output_text" and isinstance(part.get("text"), str):
      text.append(part["text"])
if text:
  open(dst,'w',encoding='utf-8').write("\n".join(text).strip()+"\n")
PY
  fi
}

apply_orch_network_guard() {
  local label="$1"
  local stdout_path="$2"
  local stderr_path="$3"
  local mode policy run_dir stage_slug state_file guard_out rc

  if truthy "${NETWORK_GUARD_MODE:-off}"; then
    mode="on"
  else
    mode="off"
  fi
  policy="$(printf '%s' "${ORCH_NETWORK_GUARD_POLICY:-deny}" | tr '[:upper:]' '[:lower:]')"

  run_dir="$(dirname "$stdout_path")"
  mkdir -p "$run_dir"
  stage_slug="$(slugify_token "$label")"
  state_file="$run_dir/network_guard_${stage_slug}.json"

  if [ ! -f "$NETWORK_GUARD_TOOL" ]; then
    if [ "$mode" = "on" ]; then
      printf 'NETWORK_GUARD_ERROR missing_tool=%s mode=%s policy=%s\n' "$NETWORK_GUARD_TOOL" "$mode" "$policy" >>"$stderr_path"
      write_runner_note "$run_dir" "Network guard: stage=$label mode=$mode policy=$policy state=$state_file result=error reason=missing_tool"
      log "Stage $label: network guard missing tool while enabled: $NETWORK_GUARD_TOOL"
      return 1
    fi
    write_runner_note "$run_dir" "Network guard: stage=$label mode=$mode policy=$policy result=skip reason=missing_tool_mode_off"
    return 0
  fi

  if guard_out="$(bash "$NETWORK_GUARD_TOOL" --phase build --enabled "$mode" --policy "$policy" --context "$label" --state-file "$state_file" 2>&1)"; then
    write_runner_note "$run_dir" "Network guard: stage=$label mode=$mode policy=$policy state=$state_file result=pass"
    log "Stage $label: network guard pass mode=$mode policy=$policy state=$state_file"
    return 0
  fi

  rc=$?
  if [ -n "$guard_out" ]; then
    printf '%s\n' "$guard_out" >>"$stderr_path"
  fi
  write_runner_note "$run_dir" "Network guard: stage=$label mode=$mode policy=$policy state=$state_file result=block exit=$rc"
  log "Stage $label: network guard blocked execution mode=$mode policy=$policy exit=$rc state=$state_file"
  return "$rc"
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
  local label="${9:-Cycle}"

  local CODEX_SEARCH_FLAGS=()
  if [ "$search" = "true" ]; then
    CODEX_SEARCH_FLAGS=(--search)
  fi

  local CODEX_REASONING_FLAGS=(-c "model_reasoning_effort=\"$effort\"")
  local exit_code=0
  local started_at elapsed hb
  local guard_rc=0

  hb="$HEARTBEAT_SECS"
  if ! [[ "$hb" =~ ^[0-9]+$ ]]; then
    hb=60
  fi

  log "Stage $label: runner=$runner model=$model effort=$effort search=$search"
  log "Stage $label: logs: stdout=$stdout_path stderr=$stderr_path"
  apply_orch_network_guard "$label" "$stdout_path" "$stderr_path" || guard_rc=$?
  if [ "$guard_rc" -ne 0 ]; then
    log "Stage $label: exit=$guard_rc (blocked by network guard)"
    return "$guard_rc"
  fi

  case "$runner" in
    codex)
      started_at="$(date +%s)"
      local codex_cmd=(codex)
      local codex_home=""
      local codex_exec_prefix=()
      codex_home="$(prepare_codex_runtime_home 2>/dev/null || true)"
      if [ -n "$codex_home" ]; then
        codex_exec_prefix=(env "HOME=$codex_home")
      fi
      if [ "${#CODEX_SEARCH_FLAGS[@]}" -gt 0 ]; then
        codex_cmd+=("${CODEX_SEARCH_FLAGS[@]}")
      fi
      codex_cmd+=(exec --json --skip-git-repo-check --model "$model")
      if [ "${#CODEX_PERM_FLAGS[@]}" -gt 0 ]; then
        codex_cmd+=("${CODEX_PERM_FLAGS[@]}")
      fi
      codex_cmd+=("${CODEX_REASONING_FLAGS[@]}")
      if [ -n "$TIMEOUT_BIN" ]; then
        if [ -n "$last_path" ]; then
          "$TIMEOUT_BIN" 5400 "${codex_exec_prefix[@]}" "${codex_cmd[@]}" -o "$last_path" "$prompt" >"$stdout_path" 2>"$stderr_path" &
        else
          "$TIMEOUT_BIN" 5400 "${codex_exec_prefix[@]}" "${codex_cmd[@]}" "$prompt" >"$stdout_path" 2>"$stderr_path" &
        fi
        local pid=$!
        if wait_with_heartbeat "$pid" "$label" "$started_at" "$hb"; then
          exit_code=0
        else
          exit_code=$?
        fi
      else
        if [ -n "$last_path" ]; then
          if run_with_python_timeout 5400 "${codex_exec_prefix[@]}" "${codex_cmd[@]}" -o "$last_path" "$prompt" >"$stdout_path" 2>"$stderr_path"; then
            exit_code=0
          else
            exit_code=$?
          fi
        else
          if run_with_python_timeout 5400 "${codex_exec_prefix[@]}" "${codex_cmd[@]}" "$prompt" >"$stdout_path" 2>"$stderr_path"; then
            exit_code=0
          else
            exit_code=$?
          fi
        fi
      fi
      log "Stage $label: exit=$exit_code"
      emit_codex_exec_usage_summary "$stdout_path" "$label" "$model" "orchestrate"
      return $exit_code
      ;;
    claude)
      started_at="$(date +%s)"
      if [ -n "$TIMEOUT_BIN" ]; then
        "$TIMEOUT_BIN" 5400 claude -p "$prompt" --model "$model" --output-format text "${CLAUDE_PERM_FLAGS[@]}" \
          >"$stdout_path" 2>"$stderr_path" &
        local pid=$!
        if wait_with_heartbeat "$pid" "$label" "$started_at" "$hb"; then
          exit_code=0
        else
          exit_code=$?
        fi
      else
        if run_with_python_timeout 5400 claude -p "$prompt" --model "$model" --output-format text "${CLAUDE_PERM_FLAGS[@]}" \
          >"$stdout_path" 2>"$stderr_path"; then
          exit_code=0
        else
          exit_code=$?
        fi
      fi
      if [ -n "$last_path" ] && [ -f "$stdout_path" ]; then
        cp -f "$stdout_path" "$last_path" || true
      fi
      log "Stage $label: exit=$exit_code"
      return $exit_code
      ;;
    openclaw)
      if openclaw_run "$model" "$prompt" "$stdout_path" "$stderr_path" "$last_path"; then
        exit_code=0
      else
        exit_code=$?
      fi
      log "Stage $label: exit=$exit_code"
      return $exit_code
      ;;
    *)
      echo "Unknown runner: $runner (expected codex|claude|openclaw). Check $MODEL_CFG" >&2
      return 1
      ;;
  esac
}

run_cycle_with_fallback() {
  local runner="$1"
  local model_chain="$2"
  local default_model="$3"
  local prompt="$4"
  local stdout_path="$5"
  local stderr_path="$6"
  local last_path="$7"
  local search="${8:-false}"
  local effort="${9:-high}"
  local label="${10:-Cycle}"

  LAST_RUN_FAILURE_KIND="none"
  LAST_RUN_MODEL=""

  local chain
  chain="$(trim "$model_chain")"
  [ -n "$chain" ] || chain="$default_model"

  local -a models=()
  local part
  IFS='|' read -r -a models <<< "$chain"

  if [ "$runner" != "codex" ]; then
    local chosen
    chosen="$(trim "${models[0]:-$default_model}")"
    [ -n "$chosen" ] || chosen="$default_model"
    LAST_RUN_MODEL="$chosen"
    if run_cycle "$runner" "$chosen" "$prompt" "$stdout_path" "$stderr_path" "$last_path" "$search" "$effort" "$label"; then
      LAST_RUN_FAILURE_KIND="none"
      return 0
    fi
    LAST_RUN_FAILURE_KIND="other"
    return $?
  fi

  local attempted=0
  local model exit_code=1
  for part in "${models[@]}"; do
    model="$(trim "$part")"
    [ -n "$model" ] || continue

    attempted=1
    LAST_RUN_MODEL="$model"
    if run_cycle "$runner" "$model" "$prompt" "$stdout_path" "$stderr_path" "$last_path" "$search" "$effort" "$label"; then
      LAST_RUN_FAILURE_KIND="none"
      return 0
    fi
    exit_code=$?
    LAST_RUN_FAILURE_KIND="other"
  done

  if [ "$attempted" -eq 0 ] && [ -n "$default_model" ]; then
    LAST_RUN_MODEL="$default_model"
    if run_cycle "$runner" "$default_model" "$prompt" "$stdout_path" "$stderr_path" "$last_path" "$search" "$effort" "$label"; then
      LAST_RUN_FAILURE_KIND="none"
      return 0
    fi
    LAST_RUN_FAILURE_KIND="other"
    return $?
  fi

  LAST_RUN_FAILURE_KIND="${LAST_RUN_FAILURE_KIND:-other}"
  return $exit_code
}

should_run_integration() {
  case "${INTEGRATION_MODE:-Low}" in
    Low)
      task_needs_integration
      ;;
    Medium)
      if task_needs_integration; then return 0; fi
      if [ "${INTEGRATION_COUNT:-0}" -ge "${INTEGRATION_TARGET:-0}" ] && [ "${INTEGRATION_TARGET:-0}" -gt 0 ]; then
        return 0
      fi
      return 1
      ;;
    High)
      [ "${INTEGRATION_COUNT:-0}" -ge 1 ]
      ;;
    *)
      task_needs_integration
      ;;
  esac
}

integration_counter_on_ran() {
  # If Integration ran: set INTEGRATION_COUNT=0 and, for Medium mode, rotate target 3->4->5->6->3.
  python3 - "$WF_CFG" "${INTEGRATION_MODE:-Low}" <<'PY'
from pathlib import Path
import re, sys
p=Path(sys.argv[1])
mode=sys.argv[2]
lines=p.read_text(encoding="utf-8", errors="replace").splitlines(True)
out=[]
target=None
for line in lines:
  if line.startswith("## INTEGRATION_COUNT="):
    out.append("## INTEGRATION_COUNT=0\n")
    continue
  if line.startswith("## INTEGRATION_TARGET="):
    try:
      target=int(line.split("=",1)[1].strip() or "0")
    except Exception:
      target=0
    out.append(line)
    continue
  out.append(line)
if mode=="Medium":
  # Update target in-place if present, else append.
  cycle=[3,4,5,6]
  if target in cycle:
    nxt=cycle[(cycle.index(target)+1)%len(cycle)]
  else:
    nxt=3
  new=[]
  replaced=False
  for line in out:
    if line.startswith("## INTEGRATION_TARGET="):
      new.append(f"## INTEGRATION_TARGET={nxt}\n")
      replaced=True
    else:
      new.append(line)
  out=new
  if not replaced:
    out.append(f"## INTEGRATION_TARGET={nxt}\n")
p.write_text("".join(out), encoding="utf-8")
PY
}

integration_counter_on_skip() {
  python3 - "$WF_CFG" <<'PY'
from pathlib import Path
import re, sys
p=Path(sys.argv[1])
lines=p.read_text(encoding="utf-8", errors="replace").splitlines(True)
out=[]
done=False
for line in lines:
  if line.startswith("## INTEGRATION_COUNT="):
    try:
      n=int(line.split("=",1)[1].strip() or "0")
    except Exception:
      n=0
    out.append(f"## INTEGRATION_COUNT={n+1}\n")
    done=True
  else:
    out.append(line)
if not done:
  out.append("## INTEGRATION_COUNT=1\n")
p.write_text("".join(out), encoding="utf-8")
PY
}

write_runner_note() {
  local run_dir="$1"
  local line="$2"
  printf '%s\n' "$line" >>"$run_dir/runner_notes.md"
}

emit_codex_exec_usage_summary() {
  local stdout_path="$1"
  local label="$2"
  local model="$3"
  local loop_name="${4:-orchestrate}"
  local run_dir summary_line payload rc

  run_dir="$(dirname "$stdout_path")"
  summary_line=""
  payload=""
  rc=0

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
  if [ -d "$run_dir" ]; then
    write_runner_note "$run_dir" "$summary_line"
  fi
}

is_retention_timestamp_dir_name() {
  local name="$1"
  [[ "$name" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{6}$ ]]
}

list_retention_candidates() {
  local root="$1"
  if [ ! -d "$root" ]; then
    return 0
  fi

  find "$root" -mindepth 1 -maxdepth 1 -type d | while IFS= read -r entry; do
    local base
    base="$(basename "$entry")"
    if is_retention_timestamp_dir_name "$base"; then
      printf '%s\n' "$entry"
    fi
  done | sort
}

prune_retention_root() {
  local root="$1"
  local keep="$2"
  local label="$3"
  local before=0 after=0 to_remove idx pruned=0
  local candidates=()

  if [ ! -d "$root" ]; then
    return 0
  fi

  while IFS= read -r entry; do
    [ -n "$entry" ] || continue
    candidates+=("$entry")
  done < <(list_retention_candidates "$root")

  before="${#candidates[@]}"
  if [ "$before" -le "$keep" ]; then
    return 0
  fi

  to_remove=$(( before - keep ))
  for ((idx=0; idx<to_remove; idx++)); do
    if rm -rf -- "${candidates[$idx]}"; then
      pruned=$(( pruned + 1 ))
    else
      log "Retention prune: $label failed path=${candidates[$idx]}"
    fi
  done

  while IFS= read -r _entry; do
    after=$(( after + 1 ))
  done < <(list_retention_candidates "$root")
  log "Retention prune: $label before=$before after=$after pruned=$pruned keep=$keep"
}

apply_artifact_retention() {
  prune_retention_root "$RUNS_DIR" "$RUNS_RETENTION_KEEP" "runs"
  prune_retention_root "$DIAGS_DIR" "$DIAGS_RETENTION_KEEP" "diagnostics"
}

record_diag_artifact_status() {
  local manifest="$1"
  local label="$2"
  local state="$3"
  local detail="$4"
  printf -- '- %s: %s (%s)\n' "$label" "$state" "$detail" >>"$manifest"
}

copy_diag_artifact_if_present() {
  local src="$1"
  local dest="$2"
  local manifest="$3"
  local label="$4"
  if [ -f "$src" ]; then
    cp -a "$src" "$dest" || true
    record_diag_artifact_status "$manifest" "$label" "present" "$src"
  else
    record_diag_artifact_status "$manifest" "$label" "missing" "$src"
  fi
}

capture_git_snapshot_for_diag() {
  local repo_dir="$1"
  local prefix="$2"
  local diag_dir="$3"
  local manifest="$4"
  local label="$5"

  if [ -z "$repo_dir" ]; then
    : >"$diag_dir/${prefix}_git_status.txt"
    : >"$diag_dir/${prefix}_git_diff.txt"
    record_diag_artifact_status "$manifest" "$label git status" "missing" "repo path not configured"
    record_diag_artifact_status "$manifest" "$label git diff" "missing" "repo path not configured"
    return 0
  fi

  if git -C "$repo_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git -C "$repo_dir" status --porcelain=v1 -uall >"$diag_dir/${prefix}_git_status.txt" || true
    git -C "$repo_dir" diff >"$diag_dir/${prefix}_git_diff.txt" || true
    record_diag_artifact_status "$manifest" "$label git status" "present" "$diag_dir/${prefix}_git_status.txt"
    record_diag_artifact_status "$manifest" "$label git diff" "present" "$diag_dir/${prefix}_git_diff.txt"
  else
    : >"$diag_dir/${prefix}_git_status.txt"
    : >"$diag_dir/${prefix}_git_diff.txt"
    record_diag_artifact_status "$manifest" "$label git status" "missing" "not a git work tree: $repo_dir"
    record_diag_artifact_status "$manifest" "$label git diff" "missing" "not a git work tree: $repo_dir"
  fi
}

capture_failing_test_logs_for_diag() {
  local run_dir="$1"
  local diag_dir="$2"
  local manifest="$3"
  local test_logs_dir="$diag_dir/failing_test_logs"
  local found=0
  mkdir -p "$test_logs_dir"

  while IFS= read -r log_file; do
    if rg -n -i "fail|failed|error|traceback|assertion" "$log_file" >/dev/null 2>&1; then
      cp -a "$log_file" "$test_logs_dir/" || true
      found=1
    fi
  done < <(
    find "$run_dir" -maxdepth 1 -type f \
      \( -name "*.stdout.log" -o -name "*.stderr.log" -o -name "*.last.md" -o -name "*test*.log" \) \
      | sort
  )

  if [ "$found" -eq 1 ]; then
    record_diag_artifact_status "$manifest" "failing test logs" "present" "$test_logs_dir"
  else
    record_diag_artifact_status "$manifest" "failing test logs" "missing" "no failing test signatures found in run logs"
  fi
}

write_diag_incident_pointer() {
  local diag_dir="$1"
  local run_dir="$2"
  local stage="$3"
  local failure_signature="$4"
  local incident_path="$5"
  local status_value="pending"
  if [ -n "$incident_path" ]; then
    status_value="linked"
  fi
  cat >"$diag_dir/incident_pointer.md" <<EOF
# Incident Pointer

- Status: \`$status_value\`
- Incident path: \`${incident_path:-<unavailable>}\`
- Run: \`$run_dir\`
- Diagnostics: \`$diag_dir\`
- Stage: \`${stage:-<pending>}\`
- Failure signature: \`${failure_signature:-<pending>}\`
- Updated UTC: $(date -u '+%F %T UTC')
EOF
}

run_diag_redaction_pass() {
  local diag_dir="$1"
  local report_path="$diag_dir/redaction_report.md"
  local tool_output=""
  DIAG_REDACTION_LAST_ERROR=""

  if [ ! -f "$DIAG_REDACT_TOOL" ]; then
    DIAG_REDACTION_LAST_ERROR="missing tool: $DIAG_REDACT_TOOL"
    cat >"$report_path" <<EOF
# Diagnostics Redaction Report

- Status: \`failed_closed\`
- Reason: \`$DIAG_REDACTION_LAST_ERROR\`
- Generated UTC: $(date -u '+%F %T UTC')
EOF
    return 1
  fi

  if tool_output="$(python3 "$DIAG_REDACT_TOOL" "$diag_dir" --report "$report_path" 2>&1)"; then
    printf '%s\n' "$tool_output" >"$diag_dir/redaction_summary.json"
    return 0
  fi

  local tool_exit="$?"
  local tool_output_sha="unavailable"
  local tool_output_bytes="0"
  if [ -n "$tool_output" ]; then
    tool_output_bytes="$(printf '%s' "$tool_output" | wc -c | tr -d ' ')"
    if command -v shasum >/dev/null 2>&1; then
      tool_output_sha="$(printf '%s' "$tool_output" | shasum -a 256 | awk '{print $1}')"
    fi
  fi
  DIAG_REDACTION_LAST_ERROR="redaction tool returned non-zero (exit=$tool_exit)"
  cat >"$report_path" <<EOF
# Diagnostics Redaction Report

- Status: \`failed_closed\`
- Reason: \`$DIAG_REDACTION_LAST_ERROR\`
- Tool output bytes: \`$tool_output_bytes\`
- Tool output sha256: \`$tool_output_sha\`
- Generated UTC: $(date -u '+%F %T UTC')
EOF
  {
    printf 'status=failed_closed\n'
    printf 'reason=%s\n' "$DIAG_REDACTION_LAST_ERROR"
    printf 'tool_output_bytes=%s\n' "$tool_output_bytes"
    printf 'tool_output_sha256=%s\n' "$tool_output_sha"
  } >"$diag_dir/redaction_error.log"
  return 1
}

fail_closed_diagnostics_bundle() {
  local diag_dir="$1"
  local run_dir="$2"
  local why="$3"
  local failure_reason="${DIAG_REDACTION_LAST_ERROR:-redaction tool failure}"

  rm -rf "$diag_dir"
  mkdir -p "$diag_dir"

  write_diag_incident_pointer "$diag_dir" "$run_dir" "" "" ""

  cat >"$diag_dir/redaction_report.md" <<EOF
# Diagnostics Redaction Report

- Status: \`failed_closed\`
- Reason: \`$failure_reason\`
- Action: \`bundle payload removed to prevent unredacted secret leakage\`
- Generated UTC: $(date -u '+%F %T UTC')
EOF

  cat >"$diag_dir/bundle_manifest.md" <<EOF
# Diagnostics Bundle Manifest

- Status: \`failed_closed\`
- WHY: $why
- RUN_DIR: $run_dir
- DIAG_DIR: $diag_dir
- redaction_report: $diag_dir/redaction_report.md
- incident pointer: $diag_dir/incident_pointer.md
EOF

  cat >"$diag_dir/summary.txt" <<EOF
WHY: $why
RUN_DIR: $run_dir
DIAG_DIR: $diag_dir
STATUS: failed_closed
EOF

  cat >"$diag_dir/summary_with_manifest.txt" <<EOF
# Diagnostics Bundle Manifest

- WHY: $why
- RUN_DIR: $run_dir
- DIAG_DIR: $diag_dir
- STATUS: failed_closed
- incident pointer: $diag_dir/incident_pointer.md

## Artifact Presence
- \`payload\` -> \`removed\` (fail-closed redaction gate)
- \`redaction_report.md\` -> \`present\`
- \`incident_pointer.md\` -> \`present\`
EOF

  cat >"$diag_dir/quarantine_notice.md" <<EOF
# Diagnostics Quarantine Notice

Redaction failed and this bundle was fail-closed. Raw copied diagnostics payload was removed to prevent secret leakage.

- Reason: \`$failure_reason\`
- Next action: fix redaction tool availability/health, then re-run the failing stage to regenerate a redacted diagnostics bundle.
EOF
}

create_diagnostics_and_block() {
  ensure_repo_root

  local run_dir="$1"
  local why="$2"

  local diag_id diag_dir manifest staging_repo_dir
  diag_id="$(now_run_id)"
  diag_dir="$DIAGS_DIR/$diag_id"
  mkdir -p "$diag_dir"
  manifest="$diag_dir/bundle_manifest.md"
  : >"$manifest"
  staging_repo_dir="$STAGING_REPO_DIR"

  cp -a "$run_dir" "$diag_dir/" || true
  cp -a "$TASKS" "$diag_dir/tasks.md" || true
  cp -a "$BACKLOG" "$diag_dir/tasksbacklog.md" || true
  cp -a "$ARCHIVE" "$diag_dir/tasksarchive.md" || true
  cp -a "$HISTORY" "$diag_dir/historylog.md" || true
  cp -a "$STATUS" "$diag_dir/status.md" || true
  cp -a "$MODEL_CFG" "$diag_dir/model_config.md" || true
  cp -a "$WF_CFG" "$diag_dir/workflow_config.md" || true
  copy_diag_artifact_if_present "agents/expectations.md" "$diag_dir/expectations.md" "$manifest" "agents/expectations.md"
  copy_diag_artifact_if_present "agents/quickfix.md" "$diag_dir/quickfix.md" "$manifest" "agents/quickfix.md"
  copy_diag_artifact_if_present "agents/retrospect.md" "$diag_dir/retrospect.md" "$manifest" "agents/retrospect.md"
  copy_diag_artifact_if_present "agents/iterations.md" "$diag_dir/iterations.md" "$manifest" "agents/iterations.md"
  capture_failing_test_logs_for_diag "$run_dir" "$diag_dir" "$manifest"

  if [ -z "$staging_repo_dir" ] && [ -d "$REPO_ROOT/staging" ]; then
    staging_repo_dir="$REPO_ROOT/staging"
  fi

  capture_git_snapshot_for_diag "$REPO_ROOT" "development" "$diag_dir" "$manifest" "development"
  capture_git_snapshot_for_diag "$staging_repo_dir" "staging" "$diag_dir" "$manifest" "staging"

  {
    printf '# Diagnostics Bundle Manifest\n\n'
    printf -- '- WHY: %s\n' "$why"
    printf -- '- RUN_DIR: %s\n' "$run_dir"
    printf -- '- DIAG_DIR: %s\n' "$diag_dir"
    printf -- '- incident pointer: %s\n\n' "$diag_dir/incident_pointer.md"
    printf '## Artifact Presence\n'
    cat "$manifest"
  } >"$diag_dir/summary_with_manifest.txt"

  write_diag_incident_pointer "$diag_dir" "$run_dir" "" "" ""

  if git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git -C "$REPO_ROOT" status --porcelain=v1 -uall >"$diag_dir/git_status.txt" 2>&1 || true
    git -C "$REPO_ROOT" diff >"$diag_dir/git_diff.txt" 2>&1 || true
  else
    printf 'not a git work tree: %s\n' "$REPO_ROOT" >"$diag_dir/git_status.txt"
    printf 'not a git work tree: %s\n' "$REPO_ROOT" >"$diag_dir/git_diff.txt"
  fi

  printf 'WHY: %s\nRUN_DIR: %s\nDIAG_DIR: %s\n' "$why" "$run_dir" "$diag_dir" >"$diag_dir/summary.txt"
  if ! run_diag_redaction_pass "$diag_dir"; then
    fail_closed_diagnostics_bundle "$diag_dir" "$run_dir" "$why"
    echo "$diag_dir"
    return 1
  fi

  echo "$diag_dir"
}

sanitize_troubleshoot_context() {
  local value="$1"
  value="${value//$'\r'/ }"
  value="${value//$'\n'/ }"
  value="${value//\"/\'}"
  printf '%s' "$value"
}

material_change_fingerprint() {
  python3 - "$TASKS" "agents/quickfix.md" "agents/expectations.md" "$WF_CFG" "$MODEL_CFG" <<'PY'
from pathlib import Path
import hashlib
import sys

digest = hashlib.sha256()
for raw in sys.argv[1:]:
    path = Path(raw)
    digest.update(f"path={raw}\n".encode("utf-8"))
    if path.exists():
        try:
            data = path.read_bytes()
        except Exception:
            data = b""
        digest.update(f"size={len(data)}\n".encode("utf-8"))
        digest.update(data)
    else:
        digest.update(b"<missing>\n")

print(digest.hexdigest())
PY
}

failure_signature_for_blocker() {
  local stage="$1"
  local why="$2"
  local status_hint="$3"
  local task
  task="$(active_task_heading)"
  python3 - "$stage" "$why" "$status_hint" "$task" <<'PY'
import hashlib
import re
import sys

stage, why, status_hint, task = sys.argv[1:5]

def norm(value: str) -> str:
    text = (value or "").replace("\r", " ").replace("\n", " ").lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"attempt=\d+", "attempt=<n>", text)
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}[ _]\d{6}\b", "<run-id>", text)
    text = re.sub(r"agents/runs/[0-9_\-]+", "agents/runs/<run-id>", text)
    text = re.sub(r"agents/diagnostics/[0-9_\-]+", "agents/diagnostics/<diag-id>", text)
    return text

payload = "||".join([norm(stage), norm(task), norm(status_hint), norm(why)])
print(hashlib.sha256(payload.encode("utf-8")).hexdigest())
PY
}

active_task_fingerprint() {
  python3 - "$TASKS" <<'PY'
from pathlib import Path
import hashlib
import sys

path = Path(sys.argv[1])
payload = b""
if path.exists():
    try:
        payload = path.read_bytes()
    except Exception:
        payload = b""

print(hashlib.sha256(payload).hexdigest())
PY
}

discover_consult_needs_research_handoff() {
  local run_dir="$1"
  local consult_last_path="${2:-$run_dir/consult.last.md}"
  python3 - "$BLOCKER_BACKLOG" "$consult_last_path" "$run_dir" "$(pwd -P)" <<'PY'
from __future__ import annotations

from pathlib import Path
import re
import sys

blocker_path = Path(sys.argv[1])
consult_last_path = Path(sys.argv[2])
run_dir = sys.argv[3]
repo_root = Path(sys.argv[4]).resolve()
run_id = Path(run_dir.rstrip("/")).name


def warn(message: str) -> None:
    print(message, file=sys.stderr)


def normalize_candidate(raw: str) -> str | None:
    candidate = (raw or "").strip()
    if not candidate:
        return None
    path = Path(candidate)
    if path.is_absolute():
        try:
            resolved = path.resolve(strict=True)
        except Exception:
            warn(f"WARN: consult handoff absolute incident path missing: {candidate}")
            return None
        try:
            rel = resolved.relative_to(repo_root)
        except ValueError:
            warn(f"WARN: consult handoff absolute incident path is outside repo root: {candidate}")
            return None
        return rel.as_posix()

    resolved = (repo_root / path).resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError:
        warn(f"WARN: consult handoff relative incident path escapes repo root: {candidate}")
        return None
    if not resolved.is_file():
        warn(f"WARN: consult handoff incident path missing: {candidate}")
        return None
    return path.as_posix()


def emit_result(source: str, incident_path: str) -> None:
    print(f"{source}\t{incident_path}")
    raise SystemExit(0)


if blocker_path.exists():
    text = blocker_path.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"(?m)^##\s+", text)
    candidates: list[str] = []
    for raw in blocks[1:]:
        block = "## " + raw
        if "### NEEDS_RESEARCH" not in block:
            continue
        stage_match = re.search(r"- \*\*Stage blocked:\*\* `?([^`\n]+)`?", block)
        if not stage_match or not stage_match.group(1).strip().lower().startswith("consult"):
            continue
        if run_dir not in block and run_id not in block:
            continue
        incident_match = re.search(r"- \*\*Incident intake:\*\* `([^`]+)`", block)
        if not incident_match:
            continue
        normalized = normalize_candidate(incident_match.group(1))
        if normalized:
            candidates.append(normalized)
    if candidates:
        if len(candidates) > 1:
            warn(
                f"WARN: multiple consult blocker incident candidates found for run {run_id}; "
                f"reusing newest visible entry {candidates[0]}"
            )
        emit_result("tasksblocker", candidates[0])

if consult_last_path.exists():
    text = consult_last_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"incident intake[^\n]*\[[^\]]+\]\(([^)]+)\)", text, flags=re.IGNORECASE)
    if match:
        normalized = normalize_candidate(match.group(1))
        if normalized:
            emit_result("consult.last", normalized)
    elif "### NEEDS_RESEARCH" in text:
        warn(f"WARN: consult.last for run {run_id} indicates NEEDS_RESEARCH without a parseable incident link")

raise SystemExit(1)
PY
}

emit_or_update_needs_research_incident() {
  local run_dir="$1"
  local stage="$2"
  local why="$3"
  local diag_dir="$4"
  local failure_signature="$5"
  local task_fingerprint
  task_fingerprint="$(active_task_fingerprint)"
  python3 - "$INCIDENTS_INCOMING_DIR" "$TASKS" "$ESCALATION_STATE_FILE" "$run_dir" "$stage" "$why" "$diag_dir" "$failure_signature" "$task_fingerprint" <<'PY'
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import sys

(
    incidents_dir_raw,
    tasks_path_raw,
    state_path_raw,
    run_dir,
    stage,
    why,
    diag_dir,
    failure_signature,
    task_fingerprint,
) = sys.argv[1:10]

incidents_dir = Path(incidents_dir_raw)
tasks_path = Path(tasks_path_raw)
state_path = Path(state_path_raw)

def as_int(value) -> int:
    try:
        return int(value)
    except Exception:
        return 0

def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""

task_text = read_text(tasks_path)
task_title = "<No active task heading>"
for line in task_text.splitlines():
    if line.startswith("## "):
        task_title = line[3:].strip()
        break

pair_payload = f"{task_fingerprint}|{failure_signature}".encode("utf-8")
dedupe_key = hashlib.sha256(pair_payload).hexdigest()
incident_id = f"INC-{dedupe_key[:12]}"
incident_path = incidents_dir / f"{incident_id}.md"
run_id = Path(run_dir).name

state_data = {}
try:
    loaded = json.loads(read_text(state_path))
    if isinstance(loaded, dict):
        state_data = loaded
except Exception:
    state_data = {}

signature_map = state_data.get("signature_attempts", {})
if not isinstance(signature_map, dict):
    signature_map = {}
signature_entry = signature_map.get(failure_signature, {})
if not isinstance(signature_entry, dict):
    signature_entry = {}

quickfix_attempts = as_int(state_data.get("quickfix_attempts"))
troubleshoot_attempts = as_int(state_data.get("troubleshoot_attempts"))
consult_attempts = as_int(state_data.get("consult_attempts"))
sig_troubleshoot_attempts = as_int(signature_entry.get("troubleshoot_attempts"))
sig_consult_attempts = as_int(signature_entry.get("consult_attempts"))
material_fp = str(signature_entry.get("last_material_fingerprint", "") or state_data.get("last_material_fingerprint", "") or "")

now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
created_utc = now_utc
attempt_count = 1
history_lines: list[str] = []

existing = read_text(incident_path)
if existing:
    m = re.search(r"^- Attempt Count:\s*(\d+)\s*$", existing, flags=re.MULTILINE)
    if m:
        attempt_count = as_int(m.group(1)) + 1
    m = re.search(r"^- Created UTC:\s*(.+)\s*$", existing, flags=re.MULTILINE)
    if m:
        created_utc = m.group(1).strip()
    if "## Attempt History" in existing:
        _, tail = existing.split("## Attempt History", 1)
        for raw_line in tail.splitlines():
            line = raw_line.rstrip()
            if line.startswith("- "):
                history_lines.append(line)

history_lines.append(
    f"- {now_utc} | Attempt {attempt_count} | Stage={stage} | Run={run_dir} | Diagnostics={diag_dir}"
)

incidents_dir.mkdir(parents=True, exist_ok=True)
body = "\n".join(
    [
        "# Research Incident Intake",
        "",
        "- Incident-ID: `{}`".format(incident_id),
        "- Task title: `{}`".format(task_title),
        "- Fingerprint: `{}`".format(task_fingerprint),
        "- Failure signature: `{}`".format(failure_signature),
        "- Dedupe key: `{}`".format(dedupe_key),
        "- Attempt Count: {}".format(attempt_count),
        "- Created UTC: {}".format(created_utc),
        "- Updated UTC: {}".format(now_utc),
        "- Stage: `{}`".format(stage),
        "- Run: `{}`".format(run_dir),
        "- Diagnostics: `{}`".format(diag_dir),
        "",
        "## Attempt Counters",
        "- Attempt quickfix (global): {}".format(quickfix_attempts),
        "- Attempt troubleshoot (global): {}".format(troubleshoot_attempts),
        "- Attempt consult (global): {}".format(consult_attempts),
        "- Attempt troubleshoot (signature): {}".format(sig_troubleshoot_attempts),
        "- Attempt consult (signature): {}".format(sig_consult_attempts),
        "",
        "## Failure Context",
        "- Why: {}".format(why),
        "- Escalation stage: `{}`".format(stage),
        "- Last material fingerprint: `{}`".format(material_fp),
        "- Run ID: `{}`".format(run_id),
        "",
        "## Root-Cause Hypothesis",
        "- Primary hypothesis: <state likely root cause>",
        "- Confidence: low|medium|high",
        "- Evidence:",
        "  - <path or excerpt>",
        "",
        "## Alternative Hypotheses",
        "- AH-01: <alternative explanation>",
        "  - Status: candidate",
        "  - Evidence: <path or excerpt>",
        "- AH-02: <alternative explanation>",
        "  - Status: unsupported",
        "  - Evidence: <counter-evidence path or excerpt>",
        "",
        "## Investigation Steps",
        "- Steps:",
        "  1. <deterministic probe 1>",
        "  2. <deterministic probe 2>",
        "- Findings:",
        "  - <what was confirmed or rejected>",
        "",
        "## Governance Routing",
        "- Severity Class: S3",
        "- preemption behavior: S1 preempt-all incident work; S2 preempt S3/S4; S3/S4 continue FIFO.",
        "- Incident Class: other",
        "- minimal-unblock-first path: <smallest safe unblock step before full remediation>",
        "- rewrite task card path: <required when original task is malformed or overscoped>",
        "- spec addendum backflow: <required when root cause is spec-level; include spec addendum + reconciliation task>",
        "- regression test requirement: <required for bug-class incidents; capture failing and passing evidence>",
        "- framework-level routing: <required for tool/script contract failures; route framework-level fix into taskspending>",
        "",
        "## Unsupported Hypotheses",
        "- AH-unknown",
        "  - Status: unsupported",
        "  - Evidence: <counter-evidence required>",
        "",
        "## Task Synthesis Draft",
        "- Fix Spec ID: <pending>",
        "- Fix Spec Path: <pending>",
        "- Summary: <remediation approach>",
        "- Severity Class: <S1|S2|S3|S4>",
        "- preemption behavior: <copied from governance routing>",
        "- minimal-unblock-first path: <required>",
        "- rewrite task card path: <required when malformed/overscoped>",
        "- spec addendum backflow: <required when spec-level>",
        "- regression test requirement: <required for bug-class incidents>",
        "- framework-level routing: <required for tool/script contract failures>",
        "",
        "## Task Handoff",
        "- taskspending target: `agents/taskspending.md`",
        "- Decomposition trigger: `run_stage taskmaster`",
        "",
        "## Incident Closeout",
        "- Closeout status: pending",
        "- Closeout artifacts:",
        "  - fix_spec: <pending>",
        "  - taskspending: `agents/taskspending.md`",
        "  - unsupported hypotheses log: <pending>",
        "- Resolution criteria:",
        "  - <what must be true before archive>",
        "",
        "## Attempt History",
        *history_lines,
        "",
    ]
)
incident_path.write_text(body, encoding="utf-8")
print(str(incident_path))
PY
}

init_escalation_state_for_run() {
  local run_id="$1"
  python3 - "$ESCALATION_STATE_FILE" "$run_id" <<'PY'
from pathlib import Path
import json
import sys

path = Path(sys.argv[1])
run_id = sys.argv[2]

def fresh_state(current_run_id: str) -> dict:
    return {
        "run_id": current_run_id,
        "quickfix_attempts": 0,
        "troubleshoot_attempts": 0,
        "consult_attempts": 0,
        "last_failure_signature": "",
        "last_material_fingerprint": "",
        "signature_attempts": {},
    }

data = {}
if path.exists():
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    except Exception:
        data = {}

if data.get("run_id") != run_id:
    data = fresh_state(run_id)
else:
    defaults = fresh_state(run_id)
    for key, value in defaults.items():
        if key not in data:
            data[key] = value
    if not isinstance(data.get("signature_attempts"), dict):
        data["signature_attempts"] = {}

path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

escalation_state_mark_failure_signature() {
  local run_id="$1"
  local signature="$2"
  local material_fp="$3"
  python3 - "$ESCALATION_STATE_FILE" "$run_id" "$signature" "$material_fp" <<'PY'
from pathlib import Path
import json
import sys

path = Path(sys.argv[1])
run_id, signature, material_fp = sys.argv[2:5]

def read_state() -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}

def fresh_state(current_run_id: str) -> dict:
    return {
        "run_id": current_run_id,
        "quickfix_attempts": 0,
        "troubleshoot_attempts": 0,
        "consult_attempts": 0,
        "last_failure_signature": "",
        "last_material_fingerprint": "",
        "signature_attempts": {},
    }

data = read_state()
if data.get("run_id") != run_id:
    data = fresh_state(run_id)
if not isinstance(data.get("signature_attempts"), dict):
    data["signature_attempts"] = {}

data["last_failure_signature"] = signature
data["last_material_fingerprint"] = material_fp

path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

escalation_state_increment_counter() {
  local run_id="$1"
  local signature="$2"
  local counter_kind="$3"
  local material_fp="$4"
  python3 - "$ESCALATION_STATE_FILE" "$run_id" "$signature" "$counter_kind" "$material_fp" <<'PY'
from pathlib import Path
import json
import sys

path = Path(sys.argv[1])
run_id, signature, counter_kind, material_fp = sys.argv[2:6]

def as_int(value) -> int:
    try:
        return int(value)
    except Exception:
        return 0

def read_state() -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}

def fresh_state(current_run_id: str) -> dict:
    return {
        "run_id": current_run_id,
        "quickfix_attempts": 0,
        "troubleshoot_attempts": 0,
        "consult_attempts": 0,
        "last_failure_signature": "",
        "last_material_fingerprint": "",
        "signature_attempts": {},
    }

if counter_kind not in {"quickfix", "troubleshoot", "consult"}:
    raise SystemExit(2)

data = read_state()
if data.get("run_id") != run_id:
    data = fresh_state(run_id)
if not isinstance(data.get("signature_attempts"), dict):
    data["signature_attempts"] = {}

global_key = f"{counter_kind}_attempts"
data[global_key] = as_int(data.get(global_key)) + 1
data["last_failure_signature"] = signature
data["last_material_fingerprint"] = material_fp

if counter_kind in {"troubleshoot", "consult"}:
    entry = data["signature_attempts"].get(signature, {})
    if not isinstance(entry, dict):
        entry = {}
    entry.setdefault("troubleshoot_attempts", 0)
    entry.setdefault("consult_attempts", 0)
    entry[f"{counter_kind}_attempts"] = as_int(entry.get(f"{counter_kind}_attempts")) + 1
    entry["last_material_fingerprint"] = material_fp
    data["signature_attempts"][signature] = entry

path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

escalation_stage_should_run() {
  local run_id="$1"
  local signature="$2"
  local stage_kind="$3"
  local material_fp="$4"
  python3 - "$ESCALATION_STATE_FILE" "$run_id" "$signature" "$stage_kind" "$material_fp" <<'PY'
from pathlib import Path
import json
import sys

path = Path(sys.argv[1])
run_id, signature, stage_kind, material_fp = sys.argv[2:6]

if stage_kind not in {"troubleshoot", "consult"}:
    raise SystemExit(2)

if not path.exists():
    raise SystemExit(0)

try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(0)

if not isinstance(data, dict):
    raise SystemExit(0)
if data.get("run_id") != run_id:
    raise SystemExit(0)

attempts_map = data.get("signature_attempts")
if not isinstance(attempts_map, dict):
    raise SystemExit(0)
entry = attempts_map.get(signature)
if not isinstance(entry, dict):
    raise SystemExit(0)

attempt_count = entry.get(f"{stage_kind}_attempts", 0)
try:
    attempt_count = int(attempt_count)
except Exception:
    attempt_count = 0

if attempt_count <= 0:
    raise SystemExit(0)

prior_fp = str(entry.get("last_material_fingerprint", "") or "")
if prior_fp and material_fp and prior_fp != material_fp:
    raise SystemExit(0)

raise SystemExit(1)
PY
}

freeze_queues_for_needs_research() {
  local run_dir="$1"
  local stage="$2"
  local why="$3"
  local diag_dir="$4"
  local failure_signature="$5"
  local incident_path="${6:-}"
  local blocker_entry_mode="${7:-write}"

  python3 - "$TASKS" "$BACKLOG" "$MANUAL_BACKLOG" "$BLOCKER_BACKLOG" "$RESEARCH_RECOVERY_LATCH_FILE" "$TASK_STORE_LOCK_FILE" "$run_dir" "$stage" "$why" "$diag_dir" "$failure_signature" "$incident_path" "$NEEDS_RESEARCH_QUARANTINE_MODE" "$blocker_entry_mode" <<'PY'
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import fcntl
import json
import re
import sys

tasks_path = Path(sys.argv[1])
backlog_path = Path(sys.argv[2])
backburner_path = Path(sys.argv[3])
blocker_path = Path(sys.argv[4])
latch_path = Path(sys.argv[5])
lock_path = Path(sys.argv[6])
run_dir = sys.argv[7]
stage = sys.argv[8]
why = sys.argv[9]
diag_dir = sys.argv[10]
failure_signature = sys.argv[11]
incident_path = sys.argv[12] if len(sys.argv) > 12 else ""
quarantine_mode_raw = (sys.argv[13] if len(sys.argv) > 13 else "full").strip().lower()
blocker_entry_mode = (sys.argv[14] if len(sys.argv) > 14 else "write").strip().lower()
if blocker_entry_mode not in {"write", "skip"}:
    blocker_entry_mode = "write"

def ensure_backburner_scaffold(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Tasks Backburner\n\n"
        "Backlog for cards auto-demoted by the local orchestrator after hard blockers.\n"
        "Review and triage these cards before re-adding them to `agents/tasksbacklog.md`.\n\n"
        "---\n\n"
        "### Auto-demoted blocked cards (from orchestrate_loop.sh)\n\n",
        encoding="utf-8",
    )

def ensure_blocker_scaffold(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Tasks Blocker Queue\n\n"
        "Blocked task cards are quarantined here with evidence pointers and deterministic next actions.\n"
        "This file is prepend-first (newest blocker entries at the top).\n\n"
        "## Entry Template\n\n"
        "## YYYY-MM-DD HH:MM:SS UTC — <Task Title>\n\n"
        "- **Status:** `### BLOCKED` | `### CONSULT_COMPLETE` | `### NEEDS_RESEARCH`\n"
        "- **Stage blocked:** <Builder | QA | Quickfix | Doublecheck | Consult>\n"
        "- **Source task card:** <path and heading>\n"
        "- **Prompt artifact:** <path, if available>\n"
        "- **Evidence:**\n"
        "  - Runs: `agents/runs/<RUN_ID>/`\n"
        "  - Diagnostics: `agents/diagnostics/<TIMESTAMP>/` (if available)\n"
        "  - Quickfix/expectations: <paths, if available>\n"
        "- **Root-cause summary:** <short diagnosis>\n"
        "- **Deterministic next action:** <exact next stage/entrypoint>\n"
        "- **Incident intake:** `agents/ideas/incidents/incoming/INC-<fingerprint>.md` (required when status is `### NEEDS_RESEARCH`)\n"
        "- **Notes:** <constraints, handoff details, or unresolved risks>\n",
        encoding="utf-8",
    )

def split_cards(text: str) -> tuple[str, list[str]]:
    headings = list(re.finditer(r"^##\s+.*$", text, flags=re.M))
    if not headings:
        base = text.rstrip()
        if base:
            return base + "\n", []
        return "", []
    base = text[: headings[0].start()].rstrip()
    cards: list[str] = []
    for idx, heading in enumerate(headings):
        start = heading.start()
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(text)
        card = text[start:end].strip("\n")
        if card:
            cards.append(card + "\n")
    if base:
        return base + "\n", cards
    return "", cards

def render_cards(base: str, cards: list[str]) -> str:
    card_blocks = [card.strip("\n") for card in cards if card.strip()]
    base_block = base.rstrip("\n")
    if not card_blocks:
        if base_block:
            return base_block + "\n"
        return ""
    card_text = "\n\n".join(card_blocks).rstrip() + "\n"
    if base_block:
        return base_block + "\n\n" + card_text
    return card_text

def card_heading(card_text: str) -> str:
    for line in card_text.splitlines():
        if line.startswith("## "):
            return line[3:].strip()
    return "Unknown Task"

def normalize_token(value: str) -> str:
    token = re.sub(r"\s+", " ", (value or "").strip().lower())
    return token

def parse_token_list(value: str) -> set[str]:
    tokens: set[str] = set()
    for raw in value.split(","):
        token = normalize_token(raw)
        if token:
            tokens.add(token)
    return tokens

def extract_dependency_metadata(card_text: str) -> tuple[set[str], set[str]]:
    spec_ids: set[str] = set()
    dependency_tags: set[str] = set()
    for raw_line in card_text.splitlines():
        line = raw_line.strip()
        spec_match = re.match(r"^-\s+\*\*Spec-ID:\*\*\s*(.+)$", line, flags=re.I)
        if spec_match:
            spec_ids.update(parse_token_list(spec_match.group(1)))
            continue
        tags_match = re.match(r"^-\s+\*\*Dependency(?:\s+|-)Tags:\*\*\s*(.+)$", line, flags=re.I)
        if tags_match:
            dependency_tags.update(parse_token_list(tags_match.group(1)))
    return spec_ids, dependency_tags

lock_path.parent.mkdir(parents=True, exist_ok=True)
with lock_path.open("a+", encoding="utf-8") as lock_fp:
    fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)

    tasks_text = tasks_path.read_text(encoding="utf-8", errors="replace") if tasks_path.exists() else ""
    backlog_text = backlog_path.read_text(encoding="utf-8", errors="replace") if backlog_path.exists() else ""
    active_card = tasks_text.strip("\n")
    backlog_base, backlog_cards = split_cards(backlog_text)

    ensure_backburner_scaffold(backburner_path)
    ensure_blocker_scaffold(blocker_path)

    now = datetime.now(timezone.utc)
    ts_readable = now.strftime("%Y-%m-%d %H:%M:%S UTC")
    ts_iso = now.isoformat().replace("+00:00", "Z")
    batch_id = now.strftime("%Y%m%dT%H%M%SZ")

    if quarantine_mode_raw not in {"full", "dependency"}:
        quarantine_mode_requested = "full"
        quarantine_reason = f"fallback_full_invalid_mode:{quarantine_mode_raw or 'empty'}"
    else:
        quarantine_mode_requested = quarantine_mode_raw
        quarantine_reason = "requested"

    quarantine_mode_applied = quarantine_mode_requested
    frozen_cards: list[str] = list(backlog_cards)
    retained_cards: list[str] = []
    cards_missing_metadata = 0

    if quarantine_mode_requested == "dependency":
        active_spec_ids, active_dependency_tags = extract_dependency_metadata(active_card)
        if not active_spec_ids and not active_dependency_tags:
            quarantine_mode_applied = "full"
            quarantine_reason = "fallback_full_active_metadata_missing"
        else:
            quarantine_mode_applied = "dependency"
            quarantine_reason = "dependency_overlap_match"
            frozen_cards = []
            retained_cards = []
            for card in backlog_cards:
                card_spec_ids, card_dependency_tags = extract_dependency_metadata(card)
                if not card_spec_ids and not card_dependency_tags:
                    cards_missing_metadata += 1
                    frozen_cards.append(card)
                    continue
                if active_spec_ids.intersection(card_spec_ids) or active_dependency_tags.intersection(card_dependency_tags):
                    frozen_cards.append(card)
                else:
                    retained_cards.append(card)
    else:
        quarantine_mode_applied = "full"
        if quarantine_reason == "requested":
            quarantine_reason = "full_mode"

    frozen_count = len(frozen_cards)
    retained_count = len(retained_cards)
    write_thaw_latch = not (quarantine_mode_applied == "dependency" and retained_count > 0)
    frozen_cards_text = "\n\n".join(card.strip("\n") for card in frozen_cards if card.strip())

    next_action = (
        "Run research loop to regenerate backlog, then orchestrator thaws frozen cards and resumes promotion."
        if write_thaw_latch
        else "Run research loop to regenerate dependency-related backlog cards; unrelated backlog cards continue promotion while quarantined cards stay in agents/tasksbackburner.md."
    )

    if active_card.strip() and blocker_entry_mode != "skip":
        task_title = card_heading(active_card)
        incident_display = incident_path if incident_path else "(pending incident path)"
        note_parts = [
            f"failure_signature=`{failure_signature}`",
            f"quarantine_mode=`{quarantine_mode_applied}`",
            f"quarantine_reason=`{quarantine_reason}`",
            f"frozen_backlog_cards=`{frozen_count}`",
            f"retained_backlog_cards=`{retained_count}`",
        ]
        if cards_missing_metadata:
            note_parts.append(f"missing_metadata_quarantined=`{cards_missing_metadata}`")
        if write_thaw_latch:
            note_parts.append("research_recovery batch pending thaw")
        else:
            note_parts.append("partial dependency quarantine; auto-thaw latch skipped")
        entry = [
            f"## {ts_readable} — {task_title}",
            "",
            "- **Status:** `### NEEDS_RESEARCH`",
            f"- **Stage blocked:** {stage}",
            f"- **Source task card:** `agents/tasks.md` ({task_title})",
            "- **Prompt artifact:** `<unknown at orchestration time>`",
            "- **Evidence:**",
            f"  - Runs: `{run_dir}`",
            f"  - Diagnostics: `{diag_dir}`",
            "  - Quickfix/expectations: `agents/quickfix.md`, `agents/expectations.md` (if present)",
            f"- **Root-cause summary:** {why}",
            f"- **Deterministic next action:** {next_action}",
            f"- **Incident intake:** `{incident_display}`",
            f"- **Notes:** {'; '.join(note_parts)}.",
            "",
        ]
        blocker_text = blocker_path.read_text(encoding="utf-8", errors="replace")
        marker = "\n## Entry Template"
        if marker in blocker_text:
            idx = blocker_text.index(marker)
            blocker_new = blocker_text[:idx].rstrip() + "\n\n" + "\n".join(entry) + blocker_text[idx:]
        else:
            blocker_new = "\n".join(entry).rstrip() + "\n\n" + blocker_text.lstrip("\n")
        blocker_path.write_text(blocker_new, encoding="utf-8")

    if frozen_cards_text:
        freeze_block = [
            f"<!-- research_recovery:freeze:start {batch_id} -->",
            f"### research_recovery freeze batch `{batch_id}`",
            f"- Frozen at: `{ts_iso}`",
            f"- Trigger: `### NEEDS_RESEARCH` stage `{stage}`",
            f"- Quarantine mode: `{quarantine_mode_applied}` (`{quarantine_reason}`)",
            f"- Frozen cards: `{frozen_count}`",
            f"- Retained backlog cards: `{retained_count}`",
            "",
            frozen_cards_text,
            f"<!-- research_recovery:freeze:end {batch_id} -->",
            "",
        ]
        backburner_text = backburner_path.read_text(encoding="utf-8", errors="replace").rstrip()
        if backburner_text:
            backburner_text += "\n\n"
        backburner_text += "\n".join(freeze_block).rstrip() + "\n"
        backburner_path.write_text(backburner_text, encoding="utf-8")

    backlog_path.write_text(render_cards(backlog_base, retained_cards), encoding="utf-8")
    tasks_path.write_text("", encoding="utf-8")

    if write_thaw_latch:
        latch = {
            "state": "frozen",
            "batch_id": batch_id,
            "frozen_at": ts_iso,
            "run_dir": run_dir,
            "diag_dir": diag_dir,
            "failure_signature": failure_signature,
            "incident_path": incident_path,
            "stage": stage,
            "reason": why,
            "frozen_backlog_cards": frozen_count,
            "retained_backlog_cards": retained_count,
            "quarantine_mode_requested": quarantine_mode_requested,
            "quarantine_mode_applied": quarantine_mode_applied,
            "quarantine_reason": quarantine_reason,
            "missing_metadata_quarantined": cards_missing_metadata,
        }
        latch_path.parent.mkdir(parents=True, exist_ok=True)
        latch_path.write_text(json.dumps(latch, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        latch_path.unlink(missing_ok=True)

    latch_state = "written" if write_thaw_latch else "skipped"
    print(
        "research_recovery freeze "
        f"batch={batch_id} "
        f"quarantine_mode={quarantine_mode_applied} "
        f"frozen_backlog_cards={frozen_count} "
        f"retained_backlog_cards={retained_count} "
        f"missing_metadata_quarantined={cards_missing_metadata} "
        f"thaw_latch={latch_state}"
    )
PY
}

thaw_research_recovery_backlog_if_ready() {
  python3 - "$BACKLOG" "$MANUAL_BACKLOG" "$RESEARCH_RECOVERY_LATCH_FILE" "$TASK_STORE_LOCK_FILE" <<'PY'
from __future__ import annotations

from pathlib import Path
import fcntl
import json
import re
import sys

backlog_path = Path(sys.argv[1])
backburner_path = Path(sys.argv[2])
latch_path = Path(sys.argv[3])
lock_path = Path(sys.argv[4])

if not latch_path.exists():
    print("research_recovery: no latch")
    raise SystemExit(0)

lock_path.parent.mkdir(parents=True, exist_ok=True)
with lock_path.open("a+", encoding="utf-8") as lock_fp:
    fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)

    if not latch_path.exists():
        print("research_recovery: latch cleared by another process")
        raise SystemExit(0)

    try:
        latch = json.loads(latch_path.read_text(encoding="utf-8"))
    except Exception:
        latch = {}

    batch_id = str(latch.get("batch_id", "") or "")
    backlog_text = backlog_path.read_text(encoding="utf-8", errors="replace") if backlog_path.exists() else ""
    if not re.search(r"^##\s+.*$", backlog_text, flags=re.M):
        print("research_recovery: waiting for regenerated backlog cards")
        raise SystemExit(3)

    frozen_cards = ""
    if batch_id and backburner_path.exists():
        marker_start = f"<!-- research_recovery:freeze:start {batch_id} -->"
        marker_end = f"<!-- research_recovery:freeze:end {batch_id} -->"
        backburner_text = backburner_path.read_text(encoding="utf-8", errors="replace")
        start = backburner_text.find(marker_start)
        end = backburner_text.find(marker_end)
        if start != -1 and end != -1 and end > start:
            end += len(marker_end)
            block = backburner_text[start:end]
            block_body = block.replace(marker_start, "", 1).replace(marker_end, "", 1)
            card_match = re.search(r"^##\s+.*$", block_body, flags=re.M)
            if card_match:
                frozen_cards = block_body[card_match.start():].strip("\n")
            new_backburner = (backburner_text[:start] + backburner_text[end:]).strip("\n")
            if new_backburner:
                new_backburner += "\n"
            backburner_path.write_text(new_backburner, encoding="utf-8")

    if frozen_cards:
        merged = backlog_text.rstrip() + "\n\n" + frozen_cards.rstrip() + "\n"
        backlog_path.write_text(merged, encoding="utf-8")
        thawed_count = len(re.findall(r"^##\s+.*$", frozen_cards, flags=re.M))
    else:
        thawed_count = 0

    latch_path.unlink(missing_ok=True)
    print(f"research_recovery thawed_backlog_cards={thawed_count}")
PY
}

route_blocker_to_needs_research() {
  local run_dir="$1"
  local stage="$2"
  local why="$3"
  local diag_dir="$4"
  local failure_signature="$5"
  local handoff_owner="${6:-}"
  local task elapsed incident_path="" handoff_source="" handoff_reused=0 blocker_entry_mode="write"

  task="$(active_task_heading)"
  if [ "$handoff_owner" = "consult" ]; then
    local consult_handoff=""
    if consult_handoff="$(discover_consult_needs_research_handoff "$run_dir")"; then
      IFS=$'\t' read -r handoff_source incident_path <<<"$consult_handoff"
      handoff_reused=1
      if [ "$handoff_source" = "tasksblocker" ]; then
        blocker_entry_mode="skip"
        write_runner_note "$run_dir" "Escalation: preserving existing Consult blocker entry for run $(basename "$run_dir")"
      fi
      write_runner_note "$run_dir" "Escalation: reusing Consult-authored incident path=$incident_path source=$handoff_source"
      echo "Incident: $incident_path" >&2
    else
      write_runner_note "$run_dir" "Escalation: consult-authored incident unavailable; falling back to orchestrator emission failure_signature=$failure_signature"
    fi
  fi

  if [ -z "$incident_path" ]; then
    if incident_path="$(emit_or_update_needs_research_incident "$run_dir" "$stage" "$why" "$diag_dir" "$failure_signature")"; then
      write_runner_note "$run_dir" "Escalation: incident updated path=$incident_path dedupe_key_basis=task-fingerprint+failure-signature"
      echo "Incident: $incident_path" >&2
    else
      incident_path=""
      write_runner_note "$run_dir" "Escalation: incident emission failed for failure_signature=$failure_signature"
    fi
  fi
  write_diag_incident_pointer "$diag_dir" "$run_dir" "$stage" "$failure_signature" "$incident_path"

  set_status "### NEEDS_RESEARCH"
  echo "NEEDS_RESEARCH: $stage: $why (failure signature: $failure_signature)" >&2
  echo "Diagnostics: $diag_dir" >&2
  write_runner_note "$run_dir" "Escalation: NEEDS_RESEARCH stage=$stage failure_signature=$failure_signature"
  notify_blocker "$run_dir" "$stage" "$why (NEEDS_RESEARCH; failure signature: $failure_signature)" "$diag_dir"

  if has_active_card || has_backlog_cards; then
    local freeze_out=""
    if freeze_out="$(freeze_queues_for_needs_research "$run_dir" "$stage" "$why" "$diag_dir" "$failure_signature" "$incident_path" "$blocker_entry_mode")"; then
      write_runner_note "$run_dir" "Escalation: $freeze_out"
    else
      write_runner_note "$run_dir" "Escalation: research_recovery freeze failed; active task may require manual queue repair"
      log "WARN: research_recovery freeze failed during NEEDS_RESEARCH routing"
    fi
    CARD_DEMOTED=1
    TASKS_DEMOTED=$(( TASKS_DEMOTED + 1 ))
    elapsed=$(( $(date +%s) - SCRIPT_START_EPOCH ))
    log "Progress: tasks_completed=$TASKS_COMPLETED tasks_demoted=$TASKS_DEMOTED elapsed=$(format_duration "$elapsed") demoted_task=\"$task\""
  fi

  set_status "### IDLE"
  LAST_WAS_TROUBLESHOOT=0
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

refresh_orch_weekly_usage_current() {
  local checkpoint="${1:-pre-cycle}"
  local provider out_path err_path sampled rc diag_path cached

  provider="$(trim "${USAGE_SAMPLER_PROVIDER:-codex}")"
  if [ -z "$provider" ]; then
    provider="env"
  fi
  if [ ! -f "$USAGE_SAMPLER_TOOL" ]; then
    log "WARN: Auto-pause ($checkpoint): usage sampler is missing at $USAGE_SAMPLER_TOOL"
    return 0
  fi

  out_path="$TMP_DIR/usage_sampler_orch.out"
  err_path="$TMP_DIR/usage_sampler_orch.err"
  ORCH_WEEKLY_USAGE_CURRENT=""
  export ORCH_WEEKLY_USAGE_CURRENT

  if USAGE_SAMPLER_CODEX_AUTH_SOURCE_DIR="$CODEX_AUTH_SOURCE_DIR" \
     USAGE_SAMPLER_CODEX_HOME="$CODEX_RUNTIME_HOME" \
     python3 "$USAGE_SAMPLER_TOOL" --loop orchestrate --provider "$provider" --cache-max-age-secs "${USAGE_SAMPLER_CACHE_MAX_AGE_SECS:-900}" --state-file "$USAGE_STATE_FILE" --print-current >"$out_path" 2>"$err_path"; then
    sampled="$(trim "$(cat "$out_path" 2>/dev/null || true)")"
    if is_nonnegative_number "$sampled"; then
      ORCH_WEEKLY_USAGE_CURRENT="$sampled"
      export ORCH_WEEKLY_USAGE_CURRENT
      log "Auto-pause ($checkpoint): sampled ORCH_WEEKLY_USAGE_CURRENT=$sampled provider=$provider"
      return 0
    fi
    printf 'invalid sampler output: %s\n' "$sampled" >"$err_path"
    rc=5
  else
    rc=$?
  fi

  diag_path="$(write_usage_sampler_warning_artifact "orchestrate" "$checkpoint" "$rc" "$err_path" 2>/dev/null || true)"
  if [ -n "$diag_path" ]; then
    log "WARN: Auto-pause ($checkpoint): usage sampler failed (rc=$rc) diagnostics=$diag_path"
  else
    log "WARN: Auto-pause ($checkpoint): usage sampler failed (rc=$rc)"
  fi

  cached="$(read_usage_current_from_state "orchestrate" 2>/dev/null || true)"
  if [ -n "$cached" ]; then
    ORCH_WEEKLY_USAGE_CURRENT="$cached"
    export ORCH_WEEKLY_USAGE_CURRENT
    log "Auto-pause ($checkpoint): using cached ORCH_WEEKLY_USAGE_CURRENT=$cached state=$USAGE_STATE_FILE"
  fi
  return 0
}

resolve_orch_weekly_usage_contract() {
  local remaining consumed legacy
  remaining="$(trim "${ORCH_WEEKLY_USAGE_REMAINING_THRESHOLD:-}")"
  consumed="$(trim "${ORCH_WEEKLY_USAGE_CONSUMED_THRESHOLD:-}")"
  legacy="$(trim "${ORCH_WEEKLY_USAGE_THRESHOLD:-}")"

  if [ -n "$remaining" ] && [ -n "$consumed" ]; then
    log "WARN: Auto-pause: both ORCH_WEEKLY_USAGE_REMAINING_THRESHOLD and ORCH_WEEKLY_USAGE_CONSUMED_THRESHOLD are set; using remaining semantics"
  fi

  if [ -n "$remaining" ]; then
    printf 'remaining\n%s\nORCH_WEEKLY_USAGE_REMAINING_THRESHOLD\n' "$remaining"
    return 0
  fi

  if [ -n "$consumed" ]; then
    printf 'consumed\n%s\nORCH_WEEKLY_USAGE_CONSUMED_THRESHOLD\n' "$consumed"
    return 0
  fi

  if [ -n "$legacy" ]; then
    printf 'remaining\n%s\nORCH_WEEKLY_USAGE_THRESHOLD\n' "$legacy"
    return 0
  fi

  return 1
}

pause_until_orch_weekly_refresh_if_needed() {
  local checkpoint="${1:-pre-cycle}"
  local mode semantics threshold threshold_source current refresh_spec refresh_info usage_contract
  local refresh_epoch refresh_label now_epoch sleep_secs

  mode="$(trim "${USAGE_AUTOPAUSE_MODE:-off}")"
  if ! truthy "$mode"; then
    return 0
  fi

  usage_contract="$(resolve_orch_weekly_usage_contract 2>/dev/null || true)"
  if [ -z "$usage_contract" ]; then
    return 0
  fi
  semantics="$(printf '%s\n' "$usage_contract" | sed -n '1p')"
  threshold="$(printf '%s\n' "$usage_contract" | sed -n '2p')"
  threshold_source="$(printf '%s\n' "$usage_contract" | sed -n '3p')"
  threshold_source="${threshold_source:-ORCH_WEEKLY_USAGE_THRESHOLD}"

  if ! is_nonnegative_number "$threshold"; then
    log "Auto-pause ($checkpoint): invalid $threshold_source=\"$threshold\"; skipping pause check"
    return 0
  fi

  current="$(trim "${ORCH_WEEKLY_USAGE_CURRENT:-}")"
  if [ -z "$current" ]; then
    log "Auto-pause ($checkpoint): enabled with semantics=$semantics threshold=$threshold source=$threshold_source but ORCH_WEEKLY_USAGE_CURRENT is unset; continuing"
    return 0
  fi
  if ! is_nonnegative_number "$current"; then
    log "Auto-pause ($checkpoint): invalid ORCH_WEEKLY_USAGE_CURRENT=\"$current\"; continuing"
    return 0
  fi

  case "$semantics" in
    remaining)
      if ! number_ge "$threshold" "$current"; then
        log "Auto-pause ($checkpoint): remaining=$current threshold=$threshold source=$threshold_source -> continue"
        return 0
      fi
      ;;
    consumed)
      if ! number_ge "$current" "$threshold"; then
        log "Auto-pause ($checkpoint): consumed=$current threshold=$threshold source=$threshold_source -> continue"
        return 0
      fi
      ;;
    *)
      log "Auto-pause ($checkpoint): unknown semantics=$semantics source=$threshold_source; skipping pause check"
      return 0
      ;;
  esac

  if [ "$threshold_source" = "ORCH_WEEKLY_USAGE_THRESHOLD" ]; then
    log "Auto-pause ($checkpoint): compatibility fallback using ORCH_WEEKLY_USAGE_THRESHOLD as remaining threshold"
  fi

  refresh_spec="$(trim "${ORCH_WEEKLY_REFRESH_UTC:-MON 00:00}")"
  refresh_info="$(next_weekly_refresh_info_utc "$refresh_spec" 2>/dev/null || true)"
  if [ -z "$refresh_info" ]; then
    log "Auto-pause ($checkpoint): invalid ORCH_WEEKLY_REFRESH_UTC=\"$refresh_spec\"; cannot compute refresh boundary"
    return 0
  fi

  refresh_epoch="$(printf '%s\n' "$refresh_info" | sed -n '1p')"
  refresh_label="$(printf '%s\n' "$refresh_info" | sed -n '2p')"
  now_epoch="$(date +%s)"
  sleep_secs=$(( refresh_epoch - now_epoch ))
  if [ "$sleep_secs" -lt 1 ]; then
    sleep_secs=1
  fi

  log "Auto-pause ($checkpoint): PAUSE semantics=$semantics current=$current threshold=$threshold source=$threshold_source refresh_utc=\"$refresh_label\" sleep_secs=$sleep_secs"
  sleep "$sleep_secs"
  log "Auto-pause ($checkpoint): RESUME now_utc=$(date -u '+%Y-%m-%d %H:%M:%S UTC') refresh_utc=\"$refresh_label\""
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

orchestrate_stage_log_prefix() {
  local stage="$1"
  case "$stage" in
    Builder) printf 'builder\n' ;;
    Integration) printf 'integration\n' ;;
    QA) printf 'qa\n' ;;
    Hotfix) printf 'hotfix\n' ;;
    Doublecheck) printf 'doublecheck\n' ;;
    Update) printf 'update\n' ;;
    Troubleshoot) printf 'troubleshoot\n' ;;
    Consult) printf 'consult\n' ;;
    *) printf '\n' ;;
  esac
}

classify_orch_stage_failure() {
  local run_dir="$1"
  local stage="$2"
  local why="$3"
  local stage_prefix stdout_path stderr_path stage_slug exit_code classifier_path classification failure_class primary fingerprint parsed

  stage_prefix="$(orchestrate_stage_log_prefix "$stage")"
  stdout_path=""
  stderr_path=""
  if [ -n "$stage_prefix" ]; then
    stdout_path="$run_dir/${stage_prefix}.stdout.log"
    stderr_path="$run_dir/${stage_prefix}.stderr.log"
  fi

  stage_slug="$(slugify_token "$stage")"
  classifier_path="$TMP_DIR/orch_failure_classification_${stage_slug}.json"

  exit_code="$(printf '%s\n' "$why" | sed -n 's/.*exit=\([0-9][0-9]*\).*/\1/p' | head -n 1)"
  if [ -z "$exit_code" ]; then
    exit_code=1
  fi

  classification="non_network"
  failure_class="OBJECTIVE_FAIL"
  primary="none"
  fingerprint=""

  if [ -f "$STAGE_FAILURE_CLASSIFIER_TOOL" ]; then
    if python3 "$STAGE_FAILURE_CLASSIFIER_TOOL" \
      --stage "$stage" \
      --runner "orchestrate_loop" \
      --exit-code "$exit_code" \
      --stderr-file "${stderr_path:-$run_dir/.missing.stderr.log}" \
      --stdout-file "${stdout_path:-}" \
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
      log "WARN: outage classifier failed for stage=$stage; continuing with non_network fallback"
      classifier_path=""
    fi
  else
    classifier_path=""
  fi

  printf '%s|%s|%s|%s|%s|%s|%s\n' \
    "${classification:-non_network}" \
    "${failure_class:-OBJECTIVE_FAIL}" \
    "${primary:-none}" \
    "${fingerprint:-}" \
    "${classifier_path:-}" \
    "${stderr_path:-}" \
    "${stdout_path:-}"
}

run_network_outage_probe() {
  local probe_stdout="$1"
  local probe_stderr="$2"
  local timeout_secs="${NETWORK_OUTAGE_PROBE_TIMEOUT_SECS:-5}"
  local probe_cmd host port

  probe_cmd="$(trim "${NETWORK_OUTAGE_PROBE_CMD:-}")"
  if [ -n "$probe_cmd" ]; then
    spawn_with_timeout "$timeout_secs" bash -lc "$probe_cmd" >"$probe_stdout" 2>"$probe_stderr"
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

write_orch_outage_diagnostic_payload() {
  local output_path="$1"
  local stage="$2"
  local why="$3"
  local run_dir="$4"
  local outcome="$5"
  local primary_signature="$6"
  local fingerprint="$7"
  local classifier_path="$8"
  local stage_stderr="$9"
  local stage_stdout="${10}"
  local probe_stdout="${11}"
  local probe_stderr="${12}"
  local attempts_file="${13}"

  python3 - "$output_path" "$stage" "$why" "$run_dir" "$outcome" "$primary_signature" "$fingerprint" "$classifier_path" "$stage_stderr" "$stage_stdout" "$probe_stdout" "$probe_stderr" "$attempts_file" <<'PY'
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
    run_dir,
    outcome,
    primary_signature,
    fingerprint,
    classifier_path,
    stage_stderr,
    stage_stdout,
    probe_stdout,
    probe_stderr,
    attempts_file,
) = sys.argv[1:14]

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
    "run_dir": run_dir,
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

wait_for_orch_network_recovery() {
  local run_dir="$1"
  local stage="$2"
  local why="$3"
  local primary_signature="$4"
  local fingerprint="$5"
  local classifier_path="$6"
  local stage_stderr="$7"
  local stage_stdout="$8"

  local attempt=1
  local wait_secs="${NETWORK_OUTAGE_WAIT_INITIAL_SECS:-15}"
  local wait_max="${NETWORK_OUTAGE_WAIT_MAX_SECS:-300}"
  local max_probes="${NETWORK_OUTAGE_MAX_PROBES:-0}"
  local policy route_to_blocker
  local safe_stage outage_dir attempts_file probe_stdout probe_stderr diag_json outcome rc now_utc

  policy="$(printf '%s' "${NETWORK_OUTAGE_POLICY:-pause_resume}" | tr '[:upper:]' '[:lower:]')"
  if [ -z "$policy" ]; then
    policy="pause_resume"
  fi
  route_to_blocker="off"
  if [ "$policy" = "blocker" ] || truthy "${NETWORK_OUTAGE_ROUTE_TO_BLOCKER:-off}"; then
    route_to_blocker="on"
  fi

  safe_stage="$(slugify_token "$stage")"
  outage_dir="$DIAGS_DIR/$(date +%F_%H%M%S)_net_wait_${safe_stage}"
  mkdir -p "$outage_dir"
  attempts_file="$outage_dir/probe_attempts.log"
  probe_stdout="$outage_dir/probe.stdout.log"
  probe_stderr="$outage_dir/probe.stderr.log"
  diag_json="$outage_dir/outage_event.json"
  outcome="waiting"

  set_status "### NET_WAIT"
  write_runner_note "$run_dir" "NET_WAIT: stage=$stage signature=${primary_signature:-none} fingerprint=${fingerprint:-none} diagnostics=$outage_dir"
  log "NET_WAIT: stage=$stage signature=${primary_signature:-none} diagnostics=$outage_dir"

  while true; do
    : >"$probe_stdout"
    : >"$probe_stderr"
    if run_network_outage_probe "$probe_stdout" "$probe_stderr"; then
      rc=0
      now_utc="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
      printf '%s|%s|%s|%s\n' "$now_utc" "$attempt" "$rc" "$wait_secs" >>"$attempts_file"
      outcome="recovered"
      write_runner_note "$run_dir" "NET_WAIT: recovery probe succeeded at attempt=$attempt"
      log "NET_WAIT: recovered at attempt=$attempt stage=$stage"
      set_status "### IDLE"
      write_orch_outage_diagnostic_payload "$diag_json" "$stage" "$why" "$run_dir" "$outcome" "$primary_signature" "$fingerprint" "$classifier_path" "$stage_stderr" "$stage_stdout" "$probe_stdout" "$probe_stderr" "$attempts_file"
      return 0
    fi

    rc=$?
    now_utc="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    printf '%s|%s|%s|%s\n' "$now_utc" "$attempt" "$rc" "$wait_secs" >>"$attempts_file"
    log "NET_WAIT: probe failed stage=$stage attempt=$attempt rc=$rc next_sleep=${wait_secs}s"

    if [[ "$max_probes" =~ ^[0-9]+$ ]] && [ "$max_probes" -gt 0 ] && [ "$attempt" -ge "$max_probes" ]; then
      write_orch_outage_diagnostic_payload "$diag_json" "$stage" "$why" "$run_dir" "$outcome" "$primary_signature" "$fingerprint" "$classifier_path" "$stage_stderr" "$stage_stdout" "$probe_stdout" "$probe_stderr" "$attempts_file"
      if [ "$route_to_blocker" = "on" ]; then
        write_runner_note "$run_dir" "NET_WAIT: exhausted probes stage=$stage attempts=$attempt; routing to blocker flow (policy=$policy)"
        set_status "### IDLE"
        return 1
      fi
      write_runner_note "$run_dir" "NET_WAIT: exhausted probes stage=$stage attempts=$attempt; continuing wait loop (policy=$policy)"
      log "NET_WAIT: probe budget exhausted stage=$stage attempts=$attempt; continuing wait loop (policy=$policy route_to_blocker=off)"
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

run_inter_task_delay_if_configured() {
  local mode secs
  mode="$(trim "${ORCH_INTER_TASK_DELAY_MODE:-}")"
  secs="${ORCH_INTER_TASK_DELAY_SECS:-0}"

  if ! [[ "$secs" =~ ^[0-9]+$ ]] || [ "$secs" -le 0 ]; then
    return 0
  fi

  if [ -n "$mode" ] && ! truthy "$mode"; then
    log "Inter-task delay: skipped (mode=$mode secs=$secs)"
    return 0
  fi

  log "Inter-task delay: start secs=$secs mode=${mode:-implicit-on}"
  sleep "$secs"
  log "Inter-task delay: end secs=$secs"
}

complexity_routing_enabled() {
  truthy "${COMPLEXITY_ROUTING:-Off}"
}

normalize_complexity() {
  local raw upper
  raw="$(trim "${1:-}")"
  upper="$(printf '%s' "$raw" | tr '[:lower:]' '[:upper:]')"
  case "$upper" in
    MODERATE) printf 'MODERATE' ;;
    INVOLVED) printf 'INVOLVED' ;;
    COMPLEX) printf 'COMPLEX' ;;
    UNKNOWN) printf 'MODERATE' ;;
    *) printf 'MODERATE' ;;
  esac
}

current_task_complexity() {
  local raw
  raw="$(sed -n 's/^\*\*Complexity:\*\*[[:space:]]*//p' "$TASKS" | head -n 1 | tr -d '\r')"
  normalize_complexity "$raw"
}

complexity_band() {
  local c
  c="$(normalize_complexity "${1:-MODERATE}")"
  case "$c" in
    MODERATE) printf 'MODERATE' ;;
    INVOLVED) printf 'INVOLVED' ;;
    COMPLEX) printf 'COMPLEX' ;;
    *) printf 'MODERATE' ;;
  esac
}

builder_prompt_for_complexity() {
  local c
  c="$(normalize_complexity "${1:-MODERATE}")"
  printf 'Open agents/_start.md and follow instructions.'
}

large_builder_prompt_for_stage() {
  local stage
  stage="$(trim "${1:-execute}")"
  case "$stage" in
    plan)
      if [ -f "$LARGE_BUILDER_PLAN_ENTRYPOINT" ]; then
        printf 'Open %s and follow instructions.' "$LARGE_BUILDER_PLAN_ENTRYPOINT"
      else
        printf 'Open agents/_start.md and follow instructions.'
      fi
      ;;
    execute)
      if [ -f "$LARGE_BUILDER_EXEC_ENTRYPOINT" ]; then
        printf 'Open %s and follow instructions.' "$LARGE_BUILDER_EXEC_ENTRYPOINT"
      else
        printf 'Open agents/_start.md and follow instructions.'
      fi
      ;;
    reassess)
      if [ -f "$LARGE_REASSESS_PROMPT_FILE" ]; then
        printf 'Open %s and follow instructions.' "$LARGE_REASSESS_PROMPT_FILE"
      else
        printf 'Open agents/_start.md and follow instructions.'
      fi
      ;;
    refactor)
      if [ -f "$LARGE_REFACTOR_ENTRYPOINT" ]; then
        printf 'Open %s and follow instructions.' "$LARGE_REFACTOR_ENTRYPOINT"
      else
        printf ''
      fi
      ;;
    *)
      printf 'Open agents/_start.md and follow instructions.'
      ;;
  esac
}

large_qa_prompt_for_stage() {
  local stage
  stage="$(trim "${1:-execute}")"
  case "$stage" in
    plan)
      if [ -f "$LARGE_QA_PLAN_ENTRYPOINT" ]; then
        printf 'Open %s and follow instructions.' "$LARGE_QA_PLAN_ENTRYPOINT"
      else
        printf 'Open agents/_check.md and follow instructions.'
      fi
      ;;
    execute)
      if [ -f "$LARGE_QA_EXEC_ENTRYPOINT" ]; then
        printf 'Open %s and follow instructions.' "$LARGE_QA_EXEC_ENTRYPOINT"
      else
        printf 'Open agents/_check.md and follow instructions.'
      fi
      ;;
    *)
      printf 'Open agents/_check.md and follow instructions.'
      ;;
  esac
}

builder_model_chain_for_complexity() {
  local c
  c="$(normalize_complexity "${1:-MODERATE}")"
  case "$c" in
    MODERATE) printf '%s' "$MODERATE_BUILDER_MODEL_CHAIN" ;;
    INVOLVED) printf '%s' "$INVOLVED_BUILDER_MODEL_CHAIN" ;;
    COMPLEX) printf '%s' "$COMPLEX_BUILDER_MODEL_CHAIN" ;;
    *) printf '%s' "$BUILDER_MODEL" ;;
  esac
}

hotfix_model_chain_for_complexity() {
  local c
  c="$(normalize_complexity "${1:-MODERATE}")"
  case "$c" in
    MODERATE) printf '%s' "$MODERATE_HOTFIX_MODEL_CHAIN" ;;
    INVOLVED) printf '%s' "$INVOLVED_HOTFIX_MODEL_CHAIN" ;;
    COMPLEX) printf '%s' "$COMPLEX_HOTFIX_MODEL_CHAIN" ;;
    *) printf '%s' "$HOTFIX_MODEL" ;;
  esac
}

qa_model_for_complexity() {
  local c stage
  c="$(normalize_complexity "${1:-MODERATE}")"
  stage="${2:-qa}"
  if [ "$stage" = "doublecheck" ]; then
    case "$c" in
      MODERATE) printf '%s' "$DOUBLECHECK_MODERATE_MODEL" ;;
      INVOLVED) printf '%s' "$DOUBLECHECK_INVOLVED_MODEL" ;;
      COMPLEX) printf '%s' "$DOUBLECHECK_COMPLEX_MODEL" ;;
      *) printf '%s' "$DOUBLECHECK_MODEL" ;;
    esac
  else
    case "$c" in
      MODERATE) printf '%s' "$QA_MODERATE_MODEL" ;;
      INVOLVED) printf '%s' "$QA_INVOLVED_MODEL" ;;
      COMPLEX) printf '%s' "$QA_COMPLEX_MODEL" ;;
      *) printf '%s' "$QA_MODEL" ;;
    esac
  fi
}

qa_effort_for_complexity() {
  local c stage
  c="$(normalize_complexity "${1:-MODERATE}")"
  stage="${2:-qa}"
  if [ "$stage" = "doublecheck" ]; then
    case "$c" in
      MODERATE) printf '%s' "$DOUBLECHECK_MODERATE_EFFORT" ;;
      INVOLVED) printf '%s' "$DOUBLECHECK_INVOLVED_EFFORT" ;;
      COMPLEX) printf '%s' "$DOUBLECHECK_COMPLEX_EFFORT" ;;
      *) printf 'xhigh' ;;
    esac
  else
    printf 'xhigh'
  fi
}

upscope_active_card_to_moderate() {
  python3 - "$TASKS" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8", errors="replace")
lines = text.splitlines()

done = False
for i, line in enumerate(lines):
    if line.startswith("**Complexity:**"):
        lines[i] = "**Complexity:** MODERATE"
        done = True
        break

if not done:
    insert_at = 0
    for i, line in enumerate(lines):
        if line.startswith("## "):
            insert_at = i + 1
            break
    lines.insert(insert_at, "**Complexity:** MODERATE")

path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
PY
}

maybe_upscope_small_task() {
  return 1
}

github_repo_slug() {
  command -v gh >/dev/null 2>&1 || return 1
  gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null | tr -d '\r'
}

github_default_mention() {
  local repo="$1"
  local owner="${repo%%/*}"
  if [ -n "$owner" ] && [ "$owner" != "$repo" ]; then
    printf '@%s' "$owner"
  fi
}

notify_blocker() {
  ensure_repo_root

  local run_dir="$1"
  local stage="$2"
  local why="$3"
  local diag_dir="$4"

  if ! truthy "$NOTIFY_ON_BLOCKER"; then
    return 0
  fi

  command -v gh >/dev/null 2>&1 || {
    log "Notify: gh not found; skipping GitHub notification"
    return 0
  }

  if ! gh auth status -h github.com >/dev/null 2>&1; then
    log "Notify: gh not authenticated; skipping GitHub notification"
    return 0
  fi

  local repo mention task st head_sha run_id
  repo="$(github_repo_slug)" || {
    log "Notify: unable to determine GitHub repo; skipping"
    return 0
  }
  mention="$(github_default_mention "$repo")"
  task="$(active_task_heading)"
  st="$(read_status)"
  head_sha="$(git rev-parse --short HEAD 2>/dev/null || true)"
  run_id="$(basename "$run_dir")"

  local body
  body="$(
    cat <<EOF
Millrace local orchestrator hit an escalation event. The card may have been quarantined for `### NEEDS_RESEARCH` or demoted based on runtime outcome.

- Repo: \`$repo\`
- Commit: \`$head_sha\`
- Task: \`$task\`
- Stage: \`$stage\`
- Status: \`$st\`
- Why: $why
- Run dir (local): \`$run_dir\`
- Diagnostics (local): \`$diag_dir\`

$mention
EOF
  )"

  local title url
  title="Millrace BLOCKED: $stage - $task - $run_id"
  if url="$(gh issue create -R "$repo" --title "$title" --body "$body" 2>/dev/null)"; then
    log "Notify: GitHub issue created: $url"
    write_runner_note "$run_dir" "Notify: GitHub issue: $url"
  else
    log "Notify: gh issue create failed"
  fi
}

handle_blocker() {
  local run_dir="$1"
  local stage="$2"
  local why="$3"
  local classification_info outage_classification outage_failure_class outage_signature outage_fingerprint outage_classifier_path outage_stage_stderr outage_stage_stdout
  local run_id
  run_id="$(basename "$run_dir")"

  CARD_DEMOTED=0

  classification_info="$(classify_orch_stage_failure "$run_dir" "$stage" "$why")"
  IFS='|' read -r outage_classification outage_failure_class outage_signature outage_fingerprint outage_classifier_path outage_stage_stderr outage_stage_stdout <<<"$classification_info"
  if { [ "$outage_classification" = "network_outage" ] || [ "$outage_failure_class" = "NET_WAIT" ]; } && truthy "${NETWORK_OUTAGE_RESILIENCE_MODE:-on}"; then
    write_runner_note "$run_dir" "Outage classifier: stage=$stage classification=${outage_classification:-non_network} failure_class=${outage_failure_class:-OBJECTIVE_FAIL} signature=${outage_signature:-none} fingerprint=${outage_fingerprint:-none}"
    log "Outage classifier: stage=$stage classification=${outage_classification:-non_network} failure_class=${outage_failure_class:-OBJECTIVE_FAIL} signature=${outage_signature:-none}"
    if wait_for_orch_network_recovery "$run_dir" "$stage" "$why" "$outage_signature" "$outage_fingerprint" "$outage_classifier_path" "$outage_stage_stderr" "$outage_stage_stdout"; then
      write_runner_note "$run_dir" "Outage recovery: resumed automatically stage=$stage"
      return 0
    fi
    write_runner_note "$run_dir" "Outage recovery: routed to blocker flow by policy stage=$stage"
  fi

  if [ "${outage_failure_class:-OBJECTIVE_FAIL}" = "POLICY_BLOCKED" ]; then
    write_runner_note "$run_dir" "Failure classifier: POLICY_BLOCKED stage=$stage signature=${outage_signature:-none}"
    log "Failure classifier: stage=$stage failure_class=POLICY_BLOCKED signature=${outage_signature:-none}"
  elif [ "${outage_failure_class:-OBJECTIVE_FAIL}" = "ENV_BLOCKED" ]; then
    write_runner_note "$run_dir" "Failure classifier: ENV_BLOCKED stage=$stage signature=${outage_signature:-none}"
    log "Failure classifier: stage=$stage failure_class=ENV_BLOCKED signature=${outage_signature:-none}"
  fi

  local diag_dir diag_failed_closed=0
  if diag_dir="$(create_diagnostics_and_block "$run_dir" "Stage=$stage :: $why")"; then
    diag_failed_closed=0
  else
    diag_failed_closed=1
  fi
  log "Blocker: stage=$stage why=$why"
  log "Blocker: diagnostics=$diag_dir"
  if [ "$diag_failed_closed" -eq 1 ]; then
    log "Blocker: diagnostics payload removed (fail-closed redaction gate)"
    write_runner_note "$run_dir" "Diagnostics fail-closed: payload removed due to redaction failure; safe pointer bundle retained at $diag_dir"
  fi

  local status_hint failure_signature material_fp
  status_hint="$(read_status)"
  failure_signature="$(failure_signature_for_blocker "$stage" "$why" "$status_hint")"
  material_fp="$(material_change_fingerprint)"
  escalation_state_mark_failure_signature "$run_id" "$failure_signature" "$material_fp"
  write_runner_note "$run_dir" "Escalation: failure signature=$failure_signature stage=$stage status=$status_hint"
  log "Blocker: failure signature=$failure_signature stage=$stage"

  local st
  if escalation_stage_should_run "$run_id" "$failure_signature" "troubleshoot" "$material_fp"; then
    log "Blocker: running Troubleshooter (failure signature=$failure_signature)"
    run_troubleshoot "$run_dir" || true
    escalation_state_increment_counter "$run_id" "$failure_signature" "troubleshoot" "$material_fp"
    st="$(read_status)"
    write_runner_note "$run_dir" "Troubleshooter outcome: status=$st failure_signature=$failure_signature"
    if [ "$st" = "### TROUBLESHOOT_COMPLETE" ]; then
      LAST_WAS_TROUBLESHOOT=1
      set_status "### IDLE"
      return 0
    fi
    if [ "$st" = "### NEEDS_RESEARCH" ]; then
      # Contract guard: Troubleshooter owns unblock/manual block only.
      # Consult is the only stage allowed to emit NEEDS_RESEARCH.
      log "WARN: Troubleshooter contract violation: emitted ### NEEDS_RESEARCH (routing through Consult escalation)"
      write_runner_note "$run_dir" "Troubleshooter contract violation: emitted ### NEEDS_RESEARCH; routing through Consult ownership chain"
      set_status "### BLOCKED"
      st="### BLOCKED"
    fi
  else
    write_runner_note "$run_dir" "Troubleshooter skipped: already attempted for failure signature=$failure_signature with no material repo changes"
  fi

  if escalation_stage_should_run "$run_id" "$failure_signature" "consult" "$material_fp"; then
    if [ -f "agents/_consult.md" ]; then
      log "Blocker: running Consult (failure signature=$failure_signature)"
      run_consult "$run_dir" || true
      escalation_state_increment_counter "$run_id" "$failure_signature" "consult" "$material_fp"
      st="$(read_status)"
      write_runner_note "$run_dir" "Consult outcome: status=$st failure_signature=$failure_signature"

      if [ "$st" = "### CONSULT_COMPLETE" ]; then
        set_status "### IDLE"
        return 0
      fi

      if [ "$st" = "### NEEDS_RESEARCH" ]; then
        route_blocker_to_needs_research "$run_dir" "$stage" "$why (Consult returned NEEDS_RESEARCH)" "$diag_dir" "$failure_signature" "consult"
        return 0
      fi

      route_blocker_to_needs_research "$run_dir" "$stage" "$why (post-consult status: $st)" "$diag_dir" "$failure_signature"
      return 0
    fi
    write_runner_note "$run_dir" "Consult skipped: missing agents/_consult.md (failure signature=$failure_signature)"
  else
    write_runner_note "$run_dir" "Consult skipped: already attempted for failure signature=$failure_signature with no material repo changes"
  fi

  route_blocker_to_needs_research "$run_dir" "$stage" "$why (escalation exhausted for failure signature)" "$diag_dir" "$failure_signature"
  return 0
}

run_orch_env_preflight() {
  local report_path="$TMP_DIR/orch_env_preflight.json"
  local rc=0
  local preflight_run_dir=""

  if ! truthy "${ENV_PREFLIGHT_MODE:-on}"; then
    log "Env preflight: disabled (ENV_PREFLIGHT_MODE=${ENV_PREFLIGHT_MODE:-off})"
    return 0
  fi

  python3 "$ENV_PREFLIGHT_TOOL" \
    --phase orchestrate \
    --repo-root "$REPO_ROOT" \
    --report-out "$report_path" \
    --strict-isolation on \
    --allow-search "$ORCH_SEARCH_ENABLED" \
    --allow-search-exception "${ORCH_ALLOW_SEARCH_EXCEPTION:-off}" \
    --network-guard-mode "${NETWORK_GUARD_MODE:-off}" \
    --network-guard-policy "${ORCH_NETWORK_GUARD_POLICY:-deny}" \
    --network-policy-exception "${ORCH_NETWORK_POLICY_EXCEPTION:-off}" \
    --transport-check "${ENV_PREFLIGHT_TRANSPORT_CHECK:-on}" \
    --probe-host "${NETWORK_OUTAGE_PROBE_HOST:-api.openai.com}" \
    --probe-port "${NETWORK_OUTAGE_PROBE_PORT:-443}" \
    --probe-timeout-secs "${NETWORK_OUTAGE_PROBE_TIMEOUT_SECS:-5}" \
    --require-command bash \
    --require-command python3 \
    --require-command rg \
    --require-file "$MODEL_CFG" \
    --require-file "$WF_CFG" \
    --require-dir "$RUNS_DIR" \
    --require-dir "$DIAGS_DIR" \
    --require-dir "$TMP_DIR" \
    --require-writable-dir "$RUNS_DIR" \
    --require-writable-dir "$DIAGS_DIR" \
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
        preflight_run_dir="$RUNS_DIR/$(now_run_id)_preflight"
        mkdir -p "$preflight_run_dir"
        set_status "### NET_WAIT"
        if wait_for_orch_network_recovery "$preflight_run_dir" "Preflight" "env_preflight transport check failed" "preflight_transport_unreachable" "" "$report_path" "" ""; then
          set_status "### IDLE"
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


preflight() {
  ensure_repo_root

  IDLE_MODE="$(printf '%s' "$IDLE_MODE" | tr '[:upper:]' '[:lower:]')"
  case "$IDLE_MODE" in
    auto|watch|poll) ;;
    *)
      echo "Invalid IDLE_MODE: $IDLE_MODE (expected auto|watch|poll)" >&2
      exit 1
      ;;
  esac

  if ! [[ "$IDLE_POLL_SECS" =~ ^[0-9]+$ ]] || [ "$IDLE_POLL_SECS" -lt 1 ]; then
    echo "Invalid IDLE_POLL_SECS: $IDLE_POLL_SECS (expected integer >= 1)" >&2
    exit 1
  fi
  if ! [[ "$IDLE_DEBOUNCE_SECS" =~ ^[0-9]+$ ]]; then
    echo "Invalid IDLE_DEBOUNCE_SECS: $IDLE_DEBOUNCE_SECS (expected integer >= 0)" >&2
    exit 1
  fi

  if command -v inotifywait >/dev/null 2>&1; then
    IDLE_WATCH_TOOL="inotifywait"
  elif command -v fswatch >/dev/null 2>&1; then
    IDLE_WATCH_TOOL="fswatch"
  else
    IDLE_WATCH_TOOL=""
  fi

  if [ "$IDLE_MODE" = "watch" ] && [ -z "$IDLE_WATCH_TOOL" ]; then
    echo "IDLE_MODE=watch requires inotifywait or fswatch." >&2
    exit 1
  fi

  log "Orchestrate loop starting"
  log "Config: HEARTBEAT_SECS=$HEARTBEAT_SECS NOTIFY_ON_BLOCKER=$NOTIFY_ON_BLOCKER DAEMON_MODE=$DAEMON_MODE IDLE_MODE=$IDLE_MODE IDLE_POLL_SECS=$IDLE_POLL_SECS IDLE_DEBOUNCE_SECS=$IDLE_DEBOUNCE_SECS"
  if [ -n "$IDLE_WATCH_TOOL" ]; then
    log "Config: idle watcher=$IDLE_WATCH_TOOL"
  elif [ "$IDLE_MODE" = "auto" ]; then
    log "Config: idle watcher=none (auto mode will poll)"
  fi

  require rg
  require python3
  if command -v timeout >/dev/null 2>&1; then
    TIMEOUT_BIN="timeout"
  elif command -v gtimeout >/dev/null 2>&1; then
    TIMEOUT_BIN="gtimeout"
  else
    TIMEOUT_BIN=""
    log "timeout/gtimeout not found; using python3 timeout wrapper"
  fi
  mkdir -p "$RUNS_DIR" "$DIAGS_DIR" "$TMP_DIR" "$INCIDENTS_ROOT_DIR" "$INCIDENTS_INCOMING_DIR" "$AUDIT_INCOMING_DIR"
  local incident_migration_summary=""
  incident_migration_summary="$(migrate_legacy_incidents_to_incoming)"
  if [ "$incident_migration_summary" != "migrated=0 deduped=0 renamed=0" ]; then
    log "Incident queue migration: $incident_migration_summary"
  fi

  ensure_task_store_scaffolds_locked
  if [ ! -f "$ARCHIVE" ]; then : >"$ARCHIVE"; fi
  if [ ! -f "$STATUS" ]; then set_status "### IDLE"; fi

  parse_model_config
  parse_workflow_config

  case "${COMPLEXITY_ROUTING:-Off}" in
    Off|OFF|off|On|ON|on|true|TRUE|false|FALSE) ;;
    *)
      echo "Invalid COMPLEXITY_ROUTING: ${COMPLEXITY_ROUTING} (expected Off|On)" >&2
      exit 1
      ;;
  esac
  case "${RUN_UPDATE_ON_EMPTY:-On}" in
    On|ON|on|Off|OFF|off|true|TRUE|false|FALSE) ;;
    *)
      echo "Invalid RUN_UPDATE_ON_EMPTY: ${RUN_UPDATE_ON_EMPTY} (expected On|Off)" >&2
      exit 1
      ;;
  esac
  case "${ORCH_ALLOW_SEARCH:-off}" in
    On|ON|on|Off|OFF|off|true|TRUE|false|FALSE) ;;
    *)
      echo "Invalid ORCH_ALLOW_SEARCH: ${ORCH_ALLOW_SEARCH} (expected On|Off)" >&2
      exit 1
      ;;
  esac
  case "${ORCH_ALLOW_SEARCH_EXCEPTION:-off}" in
    On|ON|on|Off|OFF|off|true|TRUE|false|FALSE) ;;
    *)
      echo "Invalid ORCH_ALLOW_SEARCH_EXCEPTION: ${ORCH_ALLOW_SEARCH_EXCEPTION} (expected On|Off)" >&2
      exit 1
      ;;
  esac
  case "${ORCH_NETWORK_POLICY_EXCEPTION:-off}" in
    On|ON|on|Off|OFF|off|true|TRUE|false|FALSE) ;;
    *)
      echo "Invalid ORCH_NETWORK_POLICY_EXCEPTION: ${ORCH_NETWORK_POLICY_EXCEPTION} (expected On|Off)" >&2
      exit 1
      ;;
  esac
  case "${ENV_PREFLIGHT_MODE:-on}" in
    On|ON|on|Off|OFF|off|true|TRUE|false|FALSE) ;;
    *)
      echo "Invalid ENV_PREFLIGHT_MODE: ${ENV_PREFLIGHT_MODE} (expected On|Off)" >&2
      exit 1
      ;;
  esac
  case "${ENV_PREFLIGHT_TRANSPORT_CHECK:-on}" in
    On|ON|on|Off|OFF|off|true|TRUE|false|FALSE) ;;
    *)
      echo "Invalid ENV_PREFLIGHT_TRANSPORT_CHECK: ${ENV_PREFLIGHT_TRANSPORT_CHECK} (expected On|Off)" >&2
      exit 1
      ;;
  esac
  if truthy "${ORCH_ALLOW_SEARCH:-off}"; then
    ORCH_SEARCH_ENABLED="true"
  else
    ORCH_SEARCH_ENABLED="false"
  fi
  case "${USAGE_AUTOPAUSE_MODE:-off}" in
    On|ON|on|Off|OFF|off|true|TRUE|false|FALSE) ;;
    *)
      echo "Invalid USAGE_AUTOPAUSE_MODE: ${USAGE_AUTOPAUSE_MODE} (expected On|Off)" >&2
      exit 1
      ;;
  esac
  local orch_usage_contract="" orch_usage_semantics="" orch_usage_threshold="" orch_usage_source=""
  orch_usage_contract="$(resolve_orch_weekly_usage_contract 2>/dev/null || true)"
  if [ -n "$orch_usage_contract" ]; then
    orch_usage_semantics="$(printf '%s\n' "$orch_usage_contract" | sed -n '1p')"
    orch_usage_threshold="$(printf '%s\n' "$orch_usage_contract" | sed -n '2p')"
    orch_usage_source="$(printf '%s\n' "$orch_usage_contract" | sed -n '3p')"
    if ! is_nonnegative_number "$orch_usage_threshold"; then
      echo "Invalid ${orch_usage_source}: ${orch_usage_threshold} (expected non-negative number or empty)" >&2
      exit 1
    fi
    if [ -n "$(trim "${ORCH_WEEKLY_USAGE_REMAINING_THRESHOLD:-}")" ] && [ -n "$(trim "${ORCH_WEEKLY_USAGE_CONSUMED_THRESHOLD:-}")" ]; then
      log "WARN: Both ORCH_WEEKLY_USAGE_REMAINING_THRESHOLD and ORCH_WEEKLY_USAGE_CONSUMED_THRESHOLD are set; preflight will use remaining semantics"
    fi
  fi
  if truthy "${USAGE_AUTOPAUSE_MODE:-off}" && [ -n "$orch_usage_contract" ]; then
    if ! next_weekly_refresh_info_utc "$(trim "${ORCH_WEEKLY_REFRESH_UTC:-MON 00:00}")" >/dev/null 2>&1; then
      echo "Invalid ORCH_WEEKLY_REFRESH_UTC: ${ORCH_WEEKLY_REFRESH_UTC} (expected DAY HH:MM, UTC)" >&2
      exit 1
    fi
  fi
  if ! [[ "${ORCH_INTER_TASK_DELAY_SECS:-0}" =~ ^[0-9]+$ ]]; then
    echo "Invalid ORCH_INTER_TASK_DELAY_SECS: ${ORCH_INTER_TASK_DELAY_SECS} (expected integer >= 0)" >&2
    exit 1
  fi
  if [ -n "$(trim "${ORCH_INTER_TASK_DELAY_MODE:-}")" ]; then
    case "${ORCH_INTER_TASK_DELAY_MODE}" in
      On|ON|on|Off|OFF|off|true|TRUE|false|FALSE) ;;
      *)
        echo "Invalid ORCH_INTER_TASK_DELAY_MODE: ${ORCH_INTER_TASK_DELAY_MODE} (expected On|Off or empty)" >&2
        exit 1
        ;;
    esac
  fi
  case "${NETWORK_GUARD_MODE:-off}" in
    On|ON|on|Off|OFF|off|true|TRUE|false|FALSE) ;;
    *)
      echo "Invalid NETWORK_GUARD_MODE: ${NETWORK_GUARD_MODE} (expected On|Off)" >&2
      exit 1
      ;;
  esac
  case "${ORCH_NETWORK_GUARD_POLICY:-deny}" in
    allow|ALLOW|Allow|deny|DENY|Deny) ;;
    *)
      echo "Invalid ORCH_NETWORK_GUARD_POLICY: ${ORCH_NETWORK_GUARD_POLICY} (expected allow|deny)" >&2
      exit 1
      ;;
  esac
  if truthy "${NETWORK_GUARD_MODE:-off}"; then
    NETWORK_GUARD_MODE="on"
  else
    NETWORK_GUARD_MODE="off"
  fi
  ORCH_NETWORK_GUARD_POLICY="$(printf '%s' "${ORCH_NETWORK_GUARD_POLICY:-deny}" | tr '[:upper:]' '[:lower:]')"
  if [ "$NETWORK_GUARD_MODE" = "on" ] && [ ! -f "$NETWORK_GUARD_TOOL" ]; then
    echo "Missing NETWORK_GUARD_TOOL: $NETWORK_GUARD_TOOL (required when NETWORK_GUARD_MODE=on)" >&2
    exit 1
  fi
  case "${NETWORK_OUTAGE_RESILIENCE_MODE:-on}" in
    On|ON|on|Off|OFF|off|true|TRUE|false|FALSE) ;;
    *)
      echo "Invalid NETWORK_OUTAGE_RESILIENCE_MODE: ${NETWORK_OUTAGE_RESILIENCE_MODE} (expected On|Off)" >&2
      exit 1
      ;;
  esac
  if ! [[ "${NETWORK_OUTAGE_WAIT_INITIAL_SECS:-15}" =~ ^[0-9]+$ ]] || [ "${NETWORK_OUTAGE_WAIT_INITIAL_SECS:-15}" -lt 1 ]; then
    echo "Invalid NETWORK_OUTAGE_WAIT_INITIAL_SECS: ${NETWORK_OUTAGE_WAIT_INITIAL_SECS} (expected integer >= 1)" >&2
    exit 1
  fi
  if ! [[ "${NETWORK_OUTAGE_WAIT_MAX_SECS:-300}" =~ ^[0-9]+$ ]] || [ "${NETWORK_OUTAGE_WAIT_MAX_SECS:-300}" -lt 1 ]; then
    echo "Invalid NETWORK_OUTAGE_WAIT_MAX_SECS: ${NETWORK_OUTAGE_WAIT_MAX_SECS} (expected integer >= 1)" >&2
    exit 1
  fi
  if ! [[ "${NETWORK_OUTAGE_MAX_PROBES:-0}" =~ ^[0-9]+$ ]] || [ "${NETWORK_OUTAGE_MAX_PROBES:-0}" -lt 0 ]; then
    echo "Invalid NETWORK_OUTAGE_MAX_PROBES: ${NETWORK_OUTAGE_MAX_PROBES} (expected integer >= 0)" >&2
    exit 1
  fi
  if ! [[ "${NETWORK_OUTAGE_PROBE_TIMEOUT_SECS:-5}" =~ ^[0-9]+$ ]] || [ "${NETWORK_OUTAGE_PROBE_TIMEOUT_SECS:-5}" -lt 1 ]; then
    echo "Invalid NETWORK_OUTAGE_PROBE_TIMEOUT_SECS: ${NETWORK_OUTAGE_PROBE_TIMEOUT_SECS} (expected integer >= 1)" >&2
    exit 1
  fi
  if ! [[ "${NETWORK_OUTAGE_PROBE_PORT:-443}" =~ ^[0-9]+$ ]] || [ "${NETWORK_OUTAGE_PROBE_PORT:-443}" -lt 1 ] || [ "${NETWORK_OUTAGE_PROBE_PORT:-443}" -gt 65535 ]; then
    echo "Invalid NETWORK_OUTAGE_PROBE_PORT: ${NETWORK_OUTAGE_PROBE_PORT} (expected integer 1..65535)" >&2
    exit 1
  fi
  case "${NETWORK_OUTAGE_POLICY:-pause_resume}" in
    pause_resume|PAUSE_RESUME|Pause_Resume|incident|INCIDENT|Incident|blocker|BLOCKER|Blocker) ;;
    *)
      echo "Invalid NETWORK_OUTAGE_POLICY: ${NETWORK_OUTAGE_POLICY} (expected pause_resume|incident|blocker)" >&2
      exit 1
      ;;
  esac
  case "${NETWORK_OUTAGE_ROUTE_TO_BLOCKER:-off}" in
    On|ON|on|Off|OFF|off|true|TRUE|false|FALSE) ;;
    *)
      echo "Invalid NETWORK_OUTAGE_ROUTE_TO_BLOCKER: ${NETWORK_OUTAGE_ROUTE_TO_BLOCKER} (expected On|Off)" >&2
      exit 1
      ;;
  esac
  case "${NETWORK_OUTAGE_ROUTE_TO_INCIDENT:-off}" in
    On|ON|on|Off|OFF|off|true|TRUE|false|FALSE) ;;
    *)
      echo "Invalid NETWORK_OUTAGE_ROUTE_TO_INCIDENT: ${NETWORK_OUTAGE_ROUTE_TO_INCIDENT} (expected On|Off)" >&2
      exit 1
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
  if [ -z "$(trim "${NETWORK_OUTAGE_PROBE_CMD:-}")" ] && [ -z "$(trim "${NETWORK_OUTAGE_PROBE_HOST:-api.openai.com}")" ]; then
    echo "Invalid NETWORK_OUTAGE_PROBE_HOST: empty host with no NETWORK_OUTAGE_PROBE_CMD override" >&2
    exit 1
  fi
  if [ "${NETWORK_OUTAGE_WAIT_INITIAL_SECS:-15}" -gt "${NETWORK_OUTAGE_WAIT_MAX_SECS:-300}" ]; then
    echo "Invalid outage wait config: NETWORK_OUTAGE_WAIT_INITIAL_SECS must be <= NETWORK_OUTAGE_WAIT_MAX_SECS" >&2
    exit 1
  fi
  if ! [[ "${RUNS_RETENTION_KEEP:-100}" =~ ^[0-9]+$ ]] || [ "${RUNS_RETENTION_KEEP:-100}" -lt 1 ]; then
    echo "Invalid RUNS_RETENTION_KEEP: ${RUNS_RETENTION_KEEP} (expected integer >= 1)" >&2
    exit 1
  fi
  if ! [[ "${DIAGS_RETENTION_KEEP:-25}" =~ ^[0-9]+$ ]] || [ "${DIAGS_RETENTION_KEEP:-25}" -lt 1 ]; then
    echo "Invalid DIAGS_RETENTION_KEEP: ${DIAGS_RETENTION_KEEP} (expected integer >= 1)" >&2
    exit 1
  fi
  if ! [[ "${LARGE_FILES_THRESHOLD:-999999999}" =~ ^[0-9]+$ ]] || [ "${LARGE_FILES_THRESHOLD:-999999999}" -lt 1 ]; then
    echo "Invalid LARGE_FILES_THRESHOLD: ${LARGE_FILES_THRESHOLD} (expected integer >= 1)" >&2
    exit 1
  fi
  if ! [[ "${LARGE_LOC_THRESHOLD:-999999999}" =~ ^[0-9]+$ ]] || [ "${LARGE_LOC_THRESHOLD:-999999999}" -lt 1 ]; then
    echo "Invalid LARGE_LOC_THRESHOLD: ${LARGE_LOC_THRESHOLD} (expected integer >= 1)" >&2
    exit 1
  fi

  set_permission_flags
  [ -f "$ENV_PREFLIGHT_TOOL" ] || { echo "Missing env preflight tool: $ENV_PREFLIGHT_TOOL" >&2; exit 1; }
  run_orch_env_preflight || exit 1

  local outage_probe_cmd_set="false"
  if [ -n "$(trim "${NETWORK_OUTAGE_PROBE_CMD:-}")" ]; then
    outage_probe_cmd_set="true"
  fi
  log "Config: BUILDER=$BUILDER_RUNNER/$BUILDER_MODEL QA=$QA_RUNNER/$QA_MODEL INTEGRATION=$INTEGRATION_RUNNER/$INTEGRATION_MODEL"
  log "Config: HOTFIX=$HOTFIX_RUNNER/$HOTFIX_MODEL DOUBLECHECK=$DOUBLECHECK_RUNNER/$DOUBLECHECK_MODEL UPDATE=$UPDATE_RUNNER/$UPDATE_MODEL TROUBLESHOOT=$TROUBLESHOOT_RUNNER/$TROUBLESHOOT_MODEL CONSULT=$CONSULT_RUNNER/$CONSULT_MODEL"
  log "Workflow: INTEGRATION_MODE=${INTEGRATION_MODE:-Low} HEADLESS_PERMISSIONS=${HEADLESS_PERMISSIONS:-Maximum} COMPLEXITY_ROUTING=${COMPLEXITY_ROUTING:-Off} RUN_UPDATE_ON_EMPTY=${RUN_UPDATE_ON_EMPTY:-On} ORCH_ALLOW_SEARCH=${ORCH_SEARCH_ENABLED} ORCH_ALLOW_SEARCH_EXCEPTION=${ORCH_ALLOW_SEARCH_EXCEPTION:-off}"
  log "Workflow: USAGE_AUTOPAUSE_MODE=${USAGE_AUTOPAUSE_MODE:-off} ORCH_WEEKLY_USAGE_REMAINING_THRESHOLD=${ORCH_WEEKLY_USAGE_REMAINING_THRESHOLD:-} ORCH_WEEKLY_USAGE_CONSUMED_THRESHOLD=${ORCH_WEEKLY_USAGE_CONSUMED_THRESHOLD:-} ORCH_WEEKLY_USAGE_THRESHOLD=${ORCH_WEEKLY_USAGE_THRESHOLD:-} ORCH_WEEKLY_REFRESH_UTC=${ORCH_WEEKLY_REFRESH_UTC:-MON 00:00} ORCH_INTER_TASK_DELAY_MODE=${ORCH_INTER_TASK_DELAY_MODE:-} ORCH_INTER_TASK_DELAY_SECS=${ORCH_INTER_TASK_DELAY_SECS:-0} ORCH_USAGE_SEMANTICS=${orch_usage_semantics:-none} ORCH_USAGE_THRESHOLD_SOURCE=${orch_usage_source:-none}"
  log "Workflow: NETWORK_GUARD_MODE=${NETWORK_GUARD_MODE:-off} ORCH_NETWORK_GUARD_POLICY=${ORCH_NETWORK_GUARD_POLICY:-deny} ORCH_NETWORK_POLICY_EXCEPTION=${ORCH_NETWORK_POLICY_EXCEPTION:-off} NETWORK_GUARD_TOOL=${NETWORK_GUARD_TOOL}"
  log "Workflow: ENV_PREFLIGHT_MODE=${ENV_PREFLIGHT_MODE:-on} ENV_PREFLIGHT_TRANSPORT_CHECK=${ENV_PREFLIGHT_TRANSPORT_CHECK:-on} ENV_PREFLIGHT_TOOL=${ENV_PREFLIGHT_TOOL}"
  log "Workflow: NETWORK_OUTAGE_RESILIENCE_MODE=${NETWORK_OUTAGE_RESILIENCE_MODE:-on} NETWORK_OUTAGE_WAIT_INITIAL_SECS=${NETWORK_OUTAGE_WAIT_INITIAL_SECS:-15} NETWORK_OUTAGE_WAIT_MAX_SECS=${NETWORK_OUTAGE_WAIT_MAX_SECS:-300} NETWORK_OUTAGE_MAX_PROBES=${NETWORK_OUTAGE_MAX_PROBES:-0} NETWORK_OUTAGE_PROBE_TIMEOUT_SECS=${NETWORK_OUTAGE_PROBE_TIMEOUT_SECS:-5} NETWORK_OUTAGE_PROBE_HOST=${NETWORK_OUTAGE_PROBE_HOST:-api.openai.com} NETWORK_OUTAGE_PROBE_PORT=${NETWORK_OUTAGE_PROBE_PORT:-443} NETWORK_OUTAGE_PROBE_CMD_SET=${outage_probe_cmd_set} NETWORK_OUTAGE_POLICY=${NETWORK_OUTAGE_POLICY:-pause_resume} NETWORK_OUTAGE_ROUTE_TO_BLOCKER=${NETWORK_OUTAGE_ROUTE_TO_BLOCKER:-off} NETWORK_OUTAGE_ROUTE_TO_INCIDENT=${NETWORK_OUTAGE_ROUTE_TO_INCIDENT:-off}"
  log "Workflow: size latch large_files_threshold=${LARGE_FILES_THRESHOLD:-999999999} large_loc_threshold=${LARGE_LOC_THRESHOLD:-999999999}"
  log "Workflow: retention runs_keep=${RUNS_RETENTION_KEEP:-100} diagnostics_keep=${DIAGS_RETENTION_KEEP:-25}"
  log "Workflow: escalation_state=$ESCALATION_STATE_FILE"

  local runners=(
    "$BUILDER_RUNNER"
    "$QA_RUNNER"
    "$HOTFIX_RUNNER"
    "$DOUBLECHECK_RUNNER"
    "$UPDATE_RUNNER"
    "$INTEGRATION_RUNNER"
    "$TROUBLESHOOT_RUNNER"
    "$CONSULT_RUNNER"
  )

  local runner
  for runner in "${runners[@]}"; do
    case "$runner" in
      codex) require codex ;;
      claude) require claude ;;
      openclaw) require curl ;;
      *) echo "Unknown runner: $runner (expected codex|claude|openclaw). Check $MODEL_CFG" >&2; exit 1 ;;
    esac
  done

  refresh_orch_weekly_usage_current "startup"
  pause_until_orch_weekly_refresh_if_needed "startup"
}

run_builder() {
  local run_dir="$1"
  local c chain prompt size_status
  c="$(current_task_complexity)"

  chain="$(builder_model_chain_for_complexity "$c")"
  size_status="$(current_size_status)"

  if [ "$size_status" = "LARGE" ]; then
    local resume_status start_stage
    resume_status="$(read_status)"

    if is_unknown_large_builder_stage_status "$resume_status"; then
      write_runner_note "$run_dir" "Builder route: unknown LARGE status marker on resume: $resume_status"
      return 1
    fi

    start_stage="$(large_builder_next_stage_from_status "$resume_status")"
    if [ "$start_stage" = "invalid" ]; then
      write_runner_note "$run_dir" "Builder route: invalid resume status for LARGE chain: $resume_status"
      return 1
    fi

    if [ "$start_stage" = "complete" ]; then
      set_status "### BUILDER_COMPLETE"
      write_runner_note "$run_dir" "Builder route: resume status already complete ($resume_status)"
      return 0
    fi

    local plan_prompt execute_prompt reassess_prompt refactor_prompt
    local run_plan=false run_execute=false run_reassess=false run_refactor=false
    plan_prompt="$(large_builder_prompt_for_stage "plan")"
    execute_prompt="$(large_builder_prompt_for_stage "execute")"
    reassess_prompt="$(large_builder_prompt_for_stage "reassess")"
    refactor_prompt="$(large_builder_prompt_for_stage "refactor")"

    case "$start_stage" in
      plan)
        run_plan=true
        run_execute=true
        run_reassess=true
        run_refactor=true
        ;;
      execute)
        run_execute=true
        run_reassess=true
        run_refactor=true
        ;;
      reassess)
        run_reassess=true
        run_refactor=true
        ;;
      refactor)
        run_refactor=true
        ;;
      *)
        write_runner_note "$run_dir" "Builder route: unexpected LARGE start stage=$start_stage from status=$resume_status"
        return 1
        ;;
    esac

    write_runner_note "$run_dir" "Builder route: complexity=$c size_status=$size_status mode=large-phase-chain model_chain=$chain resume_status=$resume_status start_stage=$start_stage"
    write_runner_note "$run_dir" "Builder phase route: plan=$(printf '%q' "$plan_prompt") execute=$(printf '%q' "$execute_prompt") reassess=$(printf '%q' "$reassess_prompt") refactor=$(printf '%q' "$refactor_prompt")"

    if [ "$run_plan" = true ]; then
      set_status "### IDLE"
      local plan_exit=0
      if run_cycle_with_fallback "$BUILDER_RUNNER" "$chain" "$BUILDER_MODEL" "$plan_prompt" \
        "$run_dir/builder_plan.stdout.log" "$run_dir/builder_plan.stderr.log" "$run_dir/builder_plan.last.md" "$ORCH_SEARCH_ENABLED" "high" "Builder Plan"; then
        plan_exit=0
      else
        plan_exit=$?
      fi
      local plan_status
      plan_status="$(read_status)"
      log "Builder Plan: exit=$plan_exit status=$plan_status"
      if [ "$plan_exit" -eq 0 ] && [ "$plan_status" = "### IDLE" ]; then
        write_runner_note "$run_dir" "Builder Plan: synthesized LARGE_PLAN_COMPLETE (exit=0 status=### IDLE)"
        set_status "$LARGE_PLAN_COMPLETE_STATUS"
        plan_status="$LARGE_PLAN_COMPLETE_STATUS"
      fi
      if [ "$plan_status" != "$LARGE_PLAN_COMPLETE_STATUS" ]; then
        return 1
      fi
    else
      write_runner_note "$run_dir" "Builder Plan: skipped (resume_status=$resume_status)"
    fi

    if [ "$run_execute" = true ]; then
      set_status "### IDLE"
      local execute_exit=0
      if run_cycle_with_fallback "$BUILDER_RUNNER" "$chain" "$BUILDER_MODEL" "$execute_prompt" \
        "$run_dir/builder_execute.stdout.log" "$run_dir/builder_execute.stderr.log" "$run_dir/builder_execute.last.md" "$ORCH_SEARCH_ENABLED" "high" "Builder Execute"; then
        execute_exit=0
      else
        execute_exit=$?
      fi
      local execute_status
      execute_status="$(read_status)"
      log "Builder Execute: exit=$execute_exit status=$execute_status"
      if [ "$execute_exit" -eq 0 ] && [ "$execute_status" = "### IDLE" ]; then
        write_runner_note "$run_dir" "Builder Execute: synthesized LARGE_EXECUTE_COMPLETE (exit=0 status=### IDLE)"
        set_status "$LARGE_EXECUTE_COMPLETE_STATUS"
        execute_status="$LARGE_EXECUTE_COMPLETE_STATUS"
      fi
      if [ "$execute_status" != "$LARGE_EXECUTE_COMPLETE_STATUS" ]; then
        return 1
      fi
    else
      write_runner_note "$run_dir" "Builder Execute: skipped (resume_status=$resume_status)"
    fi

    if [ "$run_reassess" = true ]; then
      set_status "### IDLE"
      local reassess_exit=0
      if run_cycle_with_fallback "$BUILDER_RUNNER" "$chain" "$BUILDER_MODEL" "$reassess_prompt" \
        "$run_dir/reassess.stdout.log" "$run_dir/reassess.stderr.log" "$run_dir/reassess.last.md" "$ORCH_SEARCH_ENABLED" "high" "Reassess"; then
        reassess_exit=0
      else
        reassess_exit=$?
      fi
      local reassess_status
      reassess_status="$(read_status)"
      log "Reassess: exit=$reassess_exit status=$reassess_status"
      if [ "$reassess_exit" -eq 0 ] && [ "$reassess_status" = "### IDLE" ]; then
        write_runner_note "$run_dir" "Reassess: synthesized LARGE_REASSESS_COMPLETE (exit=0 status=### IDLE)"
        set_status "$LARGE_REASSESS_COMPLETE_STATUS"
        reassess_status="$LARGE_REASSESS_COMPLETE_STATUS"
      fi
      if [ "$reassess_status" != "$LARGE_REASSESS_COMPLETE_STATUS" ]; then
        return 1
      fi
    else
      write_runner_note "$run_dir" "Reassess: skipped (resume_status=$resume_status)"
    fi

    if [ "$run_refactor" = true ] && [ -n "$refactor_prompt" ]; then
      set_status "### IDLE"
      local refactor_exit=0
      if run_cycle_with_fallback "$BUILDER_RUNNER" "$chain" "$BUILDER_MODEL" "$refactor_prompt" \
        "$run_dir/refactor.stdout.log" "$run_dir/refactor.stderr.log" "$run_dir/refactor.last.md" "$ORCH_SEARCH_ENABLED" "medium" "Refactor"; then
        refactor_exit=0
      else
        refactor_exit=$?
      fi
      local refactor_status
      refactor_status="$(read_status)"
      log "Refactor: exit=$refactor_exit status=$refactor_status"

      if [ "$refactor_exit" -eq 0 ] && [ "$refactor_status" = "### IDLE" ]; then
        write_runner_note "$run_dir" "Refactor: synthesized LARGE_REFACTOR_COMPLETE (exit=0 status=### IDLE)"
        set_status "$LARGE_REFACTOR_COMPLETE_STATUS"
        refactor_status="$LARGE_REFACTOR_COMPLETE_STATUS"
      fi

      if [ "$refactor_status" = "$LARGE_REFACTOR_COMPLETE_STATUS" ]; then
        write_runner_note "$run_dir" "Refactor: non-blocking success exit=$refactor_exit status=$refactor_status"
      else
        write_runner_note "$run_dir" "Refactor: non-blocking recovery exit=$refactor_exit status=$refactor_status (continuing to QA)"
      fi
    fi

    if [ "$run_refactor" = true ] && [ -z "$refactor_prompt" ]; then
      set_status "$LARGE_REFACTOR_COMPLETE_STATUS"
      write_runner_note "$run_dir" "Refactor: skipped (entrypoint missing); synthesized LARGE_REFACTOR_COMPLETE"
    fi

    if [ "$run_refactor" = false ]; then
      write_runner_note "$run_dir" "Refactor: skipped (resume_status=$resume_status)"
    fi

    set_status "### BUILDER_COMPLETE"
    write_runner_note "$run_dir" "Builder chain complete: plan->execute->reassess->refactor (size_status=LARGE)"
    return 0
  fi

  set_status "### IDLE"
  prompt="$(builder_prompt_for_complexity "$c")"
  write_runner_note "$run_dir" "Builder route: complexity=$c size_status=$size_status prompt=$(printf '%q' "$prompt") model_chain=$chain"

  run_cycle_with_fallback "$BUILDER_RUNNER" "$chain" "$BUILDER_MODEL" "$prompt" \
    "$run_dir/builder.stdout.log" "$run_dir/builder.stderr.log" "$run_dir/builder.last.md" "$ORCH_SEARCH_ENABLED" "high" "Builder"
}

run_integration() {
  local run_dir="$1"
  set_status "### IDLE"
  run_cycle "$INTEGRATION_RUNNER" "$INTEGRATION_MODEL" "Open agents/_integrate.md and follow instructions." \
    "$run_dir/integrate.stdout.log" "$run_dir/integrate.stderr.log" "$run_dir/integrate.last.md" "$ORCH_SEARCH_ENABLED" "high" "Integration"
}

run_qa() {
  local run_dir="$1"
  local c model effort size_status
  set_status "### IDLE"
  c="$(current_task_complexity)"
  size_status="$(current_size_status)"

  if complexity_routing_enabled; then
    model="$(qa_model_for_complexity "$c" "qa")"
    effort="$(qa_effort_for_complexity "$c" "qa")"
  else
    model="$QA_MODEL"
    effort="xhigh"
  fi
  if [ "$size_status" = "LARGE" ]; then
    local plan_prompt execute_prompt
    plan_prompt="$(large_qa_prompt_for_stage "plan")"
    execute_prompt="$(large_qa_prompt_for_stage "execute")"
    write_runner_note "$run_dir" "QA route: complexity=$c size_status=$size_status mode=large-phase-split model=$model effort=$effort"
    write_runner_note "$run_dir" "QA phase route: plan=$(printf '%q' "$plan_prompt") execute=$(printf '%q' "$execute_prompt")"

    set_status "### IDLE"
    local plan_exit=0 plan_status
    if run_cycle_with_fallback "$QA_RUNNER" "$model" "$QA_MODEL" "$plan_prompt" \
      "$run_dir/qa_plan.stdout.log" "$run_dir/qa_plan.stderr.log" "$run_dir/qa_plan.last.md" "$ORCH_SEARCH_ENABLED" "$effort" "QA Plan"; then
      plan_exit=0
    else
      plan_exit=$?
    fi
    plan_status="$(read_status)"
    log "QA Plan: exit=$plan_exit status=$plan_status"

    if [ "$plan_exit" -eq 0 ] && [ "$plan_status" = "### IDLE" ]; then
      write_runner_note "$run_dir" "QA Plan: complete (expectations prepared)"
    else
      return 1
    fi

    set_status "### IDLE"
    run_cycle_with_fallback "$QA_RUNNER" "$model" "$QA_MODEL" "$execute_prompt" \
      "$run_dir/qa_execute.stdout.log" "$run_dir/qa_execute.stderr.log" "$run_dir/qa_execute.last.md" "$ORCH_SEARCH_ENABLED" "$effort" "QA Execute"
    return $?
  fi

  write_runner_note "$run_dir" "QA route: complexity=$c size_status=$size_status model=$model effort=$effort"

  run_cycle_with_fallback "$QA_RUNNER" "$model" "$QA_MODEL" "Open agents/_check.md and follow instructions." \
    "$run_dir/qa.stdout.log" "$run_dir/qa.stderr.log" "$run_dir/qa.last.md" "$ORCH_SEARCH_ENABLED" "$effort" "QA"
}

run_hotfix() {
  local run_dir="$1"
  local c chain
  set_status "### IDLE"
  c="$(current_task_complexity)"
  chain="$(hotfix_model_chain_for_complexity "$c")"
  write_runner_note "$run_dir" "Hotfix route: complexity=$c model_chain=$chain"
  run_cycle_with_fallback "$HOTFIX_RUNNER" "$chain" "$HOTFIX_MODEL" "Open agents/_hotfix.md and follow instructions." \
    "$run_dir/hotfix.stdout.log" "$run_dir/hotfix.stderr.log" "$run_dir/hotfix.last.md" "$ORCH_SEARCH_ENABLED" "medium" "Hotfix"
}

run_doublecheck() {
  local run_dir="$1"
  local c model effort
  set_status "### IDLE"
  c="$(current_task_complexity)"

  if complexity_routing_enabled; then
    model="$(qa_model_for_complexity "$c" "doublecheck")"
    effort="$(qa_effort_for_complexity "$c" "doublecheck")"
  else
    model="$DOUBLECHECK_MODEL"
    effort="xhigh"
  fi
  write_runner_note "$run_dir" "Doublecheck route: complexity=$c model=$model effort=$effort"

  run_cycle_with_fallback "$DOUBLECHECK_RUNNER" "$model" "$DOUBLECHECK_MODEL" "Open agents/_doublecheck.md and follow instructions." \
    "$run_dir/doublecheck.stdout.log" "$run_dir/doublecheck.stderr.log" "$run_dir/doublecheck.last.md" "$ORCH_SEARCH_ENABLED" "$effort" "Doublecheck"
}

run_troubleshoot() {
  local run_dir="$1"
  set_status "### IDLE"
  run_cycle "$TROUBLESHOOT_RUNNER" "$TROUBLESHOOT_MODEL" "Open agents/_troubleshoot.md and follow instructions." \
    "$run_dir/troubleshoot.stdout.log" "$run_dir/troubleshoot.stderr.log" "$run_dir/troubleshoot.last.md" "$ORCH_SEARCH_ENABLED" "xhigh" "Troubleshoot"
}

run_consult() {
  local run_dir="$1"
  set_status "### IDLE"
  run_cycle "$CONSULT_RUNNER" "$CONSULT_MODEL" "Open agents/_consult.md and follow instructions." \
    "$run_dir/consult.stdout.log" "$run_dir/consult.stderr.log" "$run_dir/consult.last.md" "$ORCH_SEARCH_ENABLED" "xhigh" "Consult"
}

run_update() {
  local run_dir="$1"
  local size_status prompt effort
  set_status "### IDLE"
  size_status="$(current_size_status)"
  prompt="$(update_prompt_for_size_status "$size_status")"
  effort="$(update_effort_for_size_status "$size_status")"

  if [ "$size_status" = "LARGE" ] && [ ! -f "$LARGE_UPDATE_PROMPT_FILE" ]; then
    prompt="Open agents/_update.md and follow instructions."
    write_runner_note "$run_dir" "Update route: large prompt missing at $LARGE_UPDATE_PROMPT_FILE, falling back to agents/_update.md"
  fi
  if [ "$size_status" = "SMALL" ] && [ ! -f "$SMALL_UPDATE_PROMPT_FILE" ]; then
    prompt="Open agents/_update.md and follow instructions."
    write_runner_note "$run_dir" "Update route: small prompt missing at $SMALL_UPDATE_PROMPT_FILE, falling back to agents/_update.md"
  fi

  write_runner_note "$run_dir" "Update route: size_status=$size_status runner=$UPDATE_RUNNER model_chain=$UPDATE_MODEL effort=$effort prompt=$(printf '%q' "$prompt")"
  run_cycle_with_fallback "$UPDATE_RUNNER" "$UPDATE_MODEL" "$UPDATE_MODEL" "$prompt" \
    "$run_dir/update.stdout.log" "$run_dir/update.stderr.log" "$run_dir/update.last.md" "$ORCH_SEARCH_ENABLED" "$effort" "Update"
}

resolve_staging_repo_dir() {
  if [ -n "$STAGING_REPO_DIR" ]; then
    printf '%s\n' "$STAGING_REPO_DIR"
  else
    printf '%s\n' "$REPO_ROOT/staging"
  fi
}

run_staging_sync_from_manifest() {
  local run_dir="$1"
  local source_stage="$2"
  local staging_repo_dir sync_stdout sync_stderr rc

  staging_repo_dir="$(resolve_staging_repo_dir)"
  sync_stdout="$run_dir/staging_sync.stdout.log"
  sync_stderr="$run_dir/staging_sync.stderr.log"

  write_runner_note "$run_dir" "Staging sync route: tool=$STAGING_SYNC_TOOL manifest=$STAGING_MANIFEST_PATH staging_repo=$staging_repo_dir source=$source_stage"

  if [ ! -f "$STAGING_SYNC_TOOL" ]; then
    POST_QA_UPDATE_FAILURE_REASON="staging sync tool missing: $STAGING_SYNC_TOOL"
    return 1
  fi
  if [ ! -f "$STAGING_MANIFEST_PATH" ]; then
    POST_QA_UPDATE_FAILURE_REASON="staging manifest missing: $STAGING_MANIFEST_PATH"
    return 1
  fi

  if bash "$STAGING_SYNC_TOOL" "$REPO_ROOT" "$STAGING_MANIFEST_PATH" "$staging_repo_dir" >"$sync_stdout" 2>"$sync_stderr"; then
    write_runner_note "$run_dir" "Staging sync: success source=$source_stage stdout=$sync_stdout stderr=$sync_stderr"
    return 0
  fi

  rc=$?
  POST_QA_UPDATE_FAILURE_REASON="staging sync failed (source=$source_stage rc=$rc staging_repo=$staging_repo_dir)"
  write_runner_note "$run_dir" "Staging sync: failed source=$source_stage rc=$rc stdout=$sync_stdout stderr=$sync_stderr"
  return 1
}

run_staging_commit_push() {
  local run_dir="$1"
  local source_stage="$2"
  local staging_repo_dir commit_stdout commit_stderr rc run_id task commit_message reason commit_marker

  staging_repo_dir="$(resolve_staging_repo_dir)"
  commit_stdout="$run_dir/staging_commit.stdout.log"
  commit_stderr="$run_dir/staging_commit.stderr.log"
  run_id="$(basename "$run_dir")"
  task="$(active_task_heading)"
  commit_message="Millrace post-QA update run=$run_id source=$source_stage task=${task:-unknown}"

  write_runner_note "$run_dir" "Staging commit route: tool=$STAGING_COMMIT_TOOL staging_repo=$staging_repo_dir source=$source_stage"

  if [ ! -f "$STAGING_COMMIT_TOOL" ]; then
    POST_QA_UPDATE_FAILURE_REASON="staging commit tool missing: $STAGING_COMMIT_TOOL"
    return 1
  fi

  if bash "$STAGING_COMMIT_TOOL" "$staging_repo_dir" "$commit_message" >"$commit_stdout" 2>"$commit_stderr"; then
    commit_marker="$(head -n 1 "$commit_stdout" | tr -d '\r')"
    case "$commit_marker" in
      SKIP_PUBLISH*)
        write_runner_note "$run_dir" "Staging commit: skipped optional publish source=$source_stage marker=$(printf '%q' "$commit_marker") stdout=$commit_stdout stderr=$commit_stderr"
        ;;
      NO_CHANGES|PUSH_OK*)
        write_runner_note "$run_dir" "Staging commit: success source=$source_stage marker=$(printf '%q' "$commit_marker") stdout=$commit_stdout stderr=$commit_stderr"
        ;;
      *)
        write_runner_note "$run_dir" "Staging commit: success source=$source_stage marker=$(printf '%q' "${commit_marker:-UNKNOWN}") stdout=$commit_stdout stderr=$commit_stderr"
        ;;
    esac
    return 0
  fi

  rc=$?
  case "$rc" in
    10|11|12)
      write_runner_note "$run_dir" "Staging commit: skipped optional publish source=$source_stage legacy_rc=$rc stdout=$commit_stdout stderr=$commit_stderr"
      return 0
      ;;
    22) reason="staging push failed" ;;
    *) reason="staging commit/push failed rc=$rc" ;;
  esac
  POST_QA_UPDATE_FAILURE_REASON="$reason (source=$source_stage staging_repo=$staging_repo_dir)"
  write_runner_note "$run_dir" "Staging commit: failed source=$source_stage rc=$rc stdout=$commit_stdout stderr=$commit_stderr"
  return 1
}

run_post_qa_update_pipeline() {
  local run_dir="$1"
  local source_stage="$2"
  local st exit_code=0

  POST_QA_UPDATE_FAILURE_REASON=""
  write_runner_note "$run_dir" "Post-QA pipeline: start source=$source_stage"

  if run_update "$run_dir"; then
    exit_code=0
  else
    exit_code=$?
  fi
  st="$(read_status)"
  log "Update(post-QA): source=$source_stage exit=$exit_code status=$st"

  if [ "$exit_code" -eq 0 ] && [ "$st" = "### IDLE" ]; then
    write_runner_note "$run_dir" "Update(post-QA): synthesized UPDATE_COMPLETE (exit=0 status=### IDLE)"
    set_status "### UPDATE_COMPLETE"
    st="### UPDATE_COMPLETE"
  fi

  if [ "$st" != "### UPDATE_COMPLETE" ]; then
    POST_QA_UPDATE_FAILURE_REASON="post-QA update failed (source=$source_stage exit=$exit_code status=$st)"
    set_status "### IDLE"
    write_runner_note "$run_dir" "Post-QA pipeline: update failed source=$source_stage exit=$exit_code status=$st"
    return 1
  fi

  set_status "### IDLE"
  write_runner_note "$run_dir" "Post-QA pipeline: update complete source=$source_stage"
  maybe_latch_large_repo_size_mode "$run_dir" "$source_stage"

  if ! run_staging_sync_from_manifest "$run_dir" "$source_stage"; then
    return 1
  fi
  if ! run_staging_commit_push "$run_dir" "$source_stage"; then
    return 1
  fi

  write_runner_note "$run_dir" "Post-QA pipeline: complete source=$source_stage"
  return 0
}

handle_update_blocker_without_active_card() {
  local run_dir="$1"
  local stage="$2"
  local why="$3"

  local diag_dir diag_failed_closed=0
  if diag_dir="$(create_diagnostics_and_block "$run_dir" "Stage=$stage :: $why")"; then
    diag_failed_closed=0
  else
    diag_failed_closed=1
  fi
  log "Blocker: stage=$stage why=$why"
  log "Blocker: diagnostics=$diag_dir"
  if [ "$diag_failed_closed" -eq 1 ]; then
    log "Blocker: diagnostics payload removed (fail-closed redaction gate)"
    write_runner_note "$run_dir" "Diagnostics fail-closed: payload removed due to redaction failure; safe pointer bundle retained at $diag_dir"
  fi

  set_status "### BLOCKED"
  echo "BLOCKED: $stage: $why" >&2
  echo "Diagnostics: $diag_dir" >&2
  notify_blocker "$run_dir" "$stage" "$why (no active task card)" "$diag_dir"
  write_runner_note "$run_dir" "Update blocker (no active task): $why"
  return 1
}

run_update_on_empty_backlog() {
  local run_id run_dir st exit_code=0
  run_id="$(now_run_id)"
  run_dir="$RUNS_DIR/$run_id"
  mkdir -p "$run_dir"
  echo "$run_id" >"$TMP_DIR/current_run.txt"
  init_escalation_state_for_run "$run_id"
  printf 'Run: %s\nStarted: %s\n' "$run_id" "$(date '+%F %T %Z')" >"$run_dir/runner_notes.md"
  write_runner_note "$run_dir" "Update trigger: backlog empty"
  log "Run: $run_id"
  log "Update: backlog empty maintenance cycle"

  if run_update "$run_dir"; then
    exit_code=0
  else
    exit_code=$?
  fi
  st="$(read_status)"
  log "Update: exit=$exit_code status=$st"

  if [ "$exit_code" -eq 0 ] && [ "$st" = "### IDLE" ]; then
    # Be resilient if Update exits 0 without writing its terminal marker.
    write_runner_note "$run_dir" "Update: synthesized UPDATE_COMPLETE (exit=0 status=### IDLE)"
    set_status "### UPDATE_COMPLETE"
    st="### UPDATE_COMPLETE"
  fi

  if [ "$st" = "### UPDATE_COMPLETE" ]; then
    write_runner_note "$run_dir" "Update: UPDATE_COMPLETE"
    maybe_latch_large_repo_size_mode "$run_dir" "empty-backlog"
    set_status "### IDLE"
    apply_artifact_retention
    return 0
  fi

  local blocker_rc=0
  if handle_update_blocker_without_active_card "$run_dir" "Update" "exit=$exit_code status=$st"; then
    blocker_rc=0
  else
    blocker_rc=$?
  fi
  apply_artifact_retention
  return "$blocker_rc"
}

finalize_success() {
  local completed_task elapsed
  completed_task="$(active_task_heading)"

  rm -f agents/quickfix.md || true
  log "Finalize: archiving card and clearing tasks.md"
  archive_active_card_and_clear
  set_status "### IDLE"
  log "Finalize: done; status=### IDLE"

  TASKS_COMPLETED=$(( TASKS_COMPLETED + 1 ))
  elapsed=$(( $(date +%s) - SCRIPT_START_EPOCH ))
  log "Progress: tasks_completed=$TASKS_COMPLETED elapsed=$(format_duration "$elapsed") task=\"$completed_task\""
  run_inter_task_delay_if_configured
}

main_loop() {
  LAST_WAS_TROUBLESHOOT=0
  local EMPTY_UPDATE_DONE=0

  while true; do
    ensure_repo_root
    if autonomy_complete_marker_present; then
      log "Autonomy completion marker detected at $AUTONOMY_COMPLETE_MARKER; orchestrator stopping."
      set_status "### IDLE"
      break
    fi
    refresh_orch_weekly_usage_current "pre-cycle"
    pause_until_orch_weekly_refresh_if_needed "pre-cycle"
    apply_artifact_retention
    if has_research_recovery_latch; then
      local thaw_out thaw_rc=0
      thaw_out="$(thaw_research_recovery_backlog_if_ready 2>/dev/null)" || thaw_rc=$?
      case "$thaw_rc" in
        0)
          log "Recovery: $thaw_out"
          ;;
        3)
          log "Recovery: waiting for research backlog regeneration"
          ;;
        *)
          log "WARN: research_recovery thaw failed (rc=$thaw_rc)"
          ;;
      esac
    fi

    # Ensure active card.
    if ! has_active_card; then
      if has_backlog_cards; then
        EMPTY_UPDATE_DONE=0
        rm -f "$EMPTY_BACKLOG_AUDIT_HANDOFF_STAMP" >/dev/null 2>&1 || true
        local promoted
        promoted="$(promote_next_card)"
        log "Promote: $promoted"
      else
        if truthy "${RUN_UPDATE_ON_EMPTY:-On}" && [ "$EMPTY_UPDATE_DONE" -eq 0 ]; then
          if run_update_on_empty_backlog; then
            EMPTY_UPDATE_DONE=1
          else
            exit 1
          fi
        fi

        if truthy "$DAEMON_MODE"; then
          local backlog_mtime
          backlog_mtime="$(stat_mtime "$BACKLOG")"
          if should_emit_research_audit_handoff_on_empty_backlog; then
            emit_research_audit_handoff_on_empty_backlog "$backlog_mtime"
          fi
          log "Backlog empty; daemon wait (mode=$IDLE_MODE)"
          wait_for_backlog_change "$backlog_mtime"
          if [ "$IDLE_DEBOUNCE_SECS" -gt 0 ]; then
            log "Backlog update detected; debounce=${IDLE_DEBOUNCE_SECS}s"
            debounce_quiet_period "$BACKLOG" "$IDLE_DEBOUNCE_SECS"
          fi
          EMPTY_UPDATE_DONE=0
          continue
        fi

        echo "Backlog empty; done."
        break
      fi
    fi

    local run_id run_dir
    run_id="$(now_run_id)"
    run_dir="$RUNS_DIR/$run_id"
    mkdir -p "$run_dir"
    echo "$run_id" >"$TMP_DIR/current_run.txt"
    init_escalation_state_for_run "$run_id"
    printf 'Run: %s\nStarted: %s\n' "$run_id" "$(date '+%F %T %Z')" >"$run_dir/runner_notes.md"
    CARD_DEMOTED=0
    log "Run: $run_id"
    log "Task: $(active_task_heading)"

    local st
    st="$(read_status)"
    log "Status: $st"

    case "$st" in
      "### TROUBLESHOOT_COMPLETE")
        set_status "### IDLE"
        st="### IDLE"
        ;;
      "### CONSULT_COMPLETE")
        set_status "### IDLE"
        st="### IDLE"
        ;;
      "### NEEDS_RESEARCH")
        write_runner_note "$run_dir" "Resume: observed NEEDS_RESEARCH at loop start; enforcing research_recovery freeze"
        if has_active_card || has_backlog_cards; then
          if freeze_queues_for_needs_research "$run_dir" "Resume" "status marker ### NEEDS_RESEARCH at loop start" "" "resume-needs-research" ""; then
            write_runner_note "$run_dir" "Resume: research_recovery freeze ensured"
          else
            write_runner_note "$run_dir" "Resume: research_recovery freeze failed (manual intervention may be required)"
          fi
          CARD_DEMOTED=1
        fi
        set_status "### IDLE"
        st="### IDLE"
        ;;
      "### NET_WAIT")
        write_runner_note "$run_dir" "Resume: observed NET_WAIT marker; running outage recovery probe loop"
        if wait_for_orch_network_recovery "$run_dir" "Resume" "status marker ### NET_WAIT at loop start" "resume-net-wait" "" "" "" ""; then
          st="### IDLE"
        else
          if ! handle_blocker "$run_dir" "Resume" "network outage recovery exhausted from status marker ### NET_WAIT"; then
            exit 1
          fi
          if [ "$CARD_DEMOTED" -eq 1 ]; then
            LAST_WAS_TROUBLESHOOT=0
            continue
          fi
          st="$(read_status)"
        fi
        ;;
      "### UPDATE_COMPLETE")
        set_status "### IDLE"
        st="### IDLE"
        ;;
      "### BLOCKED")
        if maybe_upscope_small_task "$run_dir" "Resume" "agents/status.md was ### BLOCKED at loop start"; then
          LAST_WAS_TROUBLESHOOT=0
          st="### IDLE"
          continue
        fi
        if ! handle_blocker "$run_dir" "Resume" "agents/status.md is ### BLOCKED at loop start"; then
          exit 1
        fi
        if [ "$CARD_DEMOTED" -eq 1 ]; then
          LAST_WAS_TROUBLESHOOT=0
          continue
        fi
        st="$(read_status)"
        ;;
      "$LARGE_PLAN_COMPLETE_STATUS"|"$LARGE_EXECUTE_COMPLETE_STATUS"|"$LARGE_REASSESS_COMPLETE_STATUS"|"$LARGE_REFACTOR_COMPLETE_STATUS")
        if [ "$(current_size_status)" != "LARGE" ]; then
          if ! handle_blocker "$run_dir" "Resume" "LARGE stage marker outside LARGE mode: $st (size_status=$(current_size_status))"; then
            exit 1
          fi
          if [ "$CARD_DEMOTED" -eq 1 ]; then
            LAST_WAS_TROUBLESHOOT=0
            continue
          fi
          st="$(read_status)"
        fi
        ;;
      "### IDLE"|"### BUILDER_COMPLETE"|"### INTEGRATION_COMPLETE"|"### QUICKFIX_NEEDED"|"### QA_COMPLETE"|"### UPDATE_COMPLETE")
        ;;
      "### LARGE_"*)
        if ! handle_blocker "$run_dir" "Resume" "Unknown LARGE stage marker at loop start: $st"; then
          exit 1
        fi
        if [ "$CARD_DEMOTED" -eq 1 ]; then
          LAST_WAS_TROUBLESHOOT=0
          continue
        fi
        st="$(read_status)"
        ;;
      *)
        if maybe_upscope_small_task "$run_dir" "Resume" "Unexpected status flag at loop start: $st"; then
          LAST_WAS_TROUBLESHOOT=0
          st="### IDLE"
          continue
        fi
        if ! handle_blocker "$run_dir" "Resume" "Unexpected status flag at loop start: $st"; then
          exit 1
        fi
        if [ "$CARD_DEMOTED" -eq 1 ]; then
          LAST_WAS_TROUBLESHOOT=0
          continue
        fi
        st="$(read_status)"
        ;;
    esac

    if [ "$CARD_DEMOTED" -eq 1 ]; then
      LAST_WAS_TROUBLESHOOT=0
      continue
    fi

    if [ "$st" = "### QA_COMPLETE" ]; then
      if run_post_qa_update_pipeline "$run_dir" "QA(resume)"; then
        finalize_success
        write_runner_note "$run_dir" "QA: QA_COMPLETE (resume)"
        LAST_WAS_TROUBLESHOOT=0
        continue
      fi

      if ! handle_blocker "$run_dir" "Update" "${POST_QA_UPDATE_FAILURE_REASON:-post-QA update pipeline failed (resume)}"; then
        exit 1
      fi
      if [ "$CARD_DEMOTED" -eq 1 ]; then
        LAST_WAS_TROUBLESHOOT=0
        continue
      fi
      LAST_WAS_TROUBLESHOOT=0
      set_status "### IDLE"
      continue
    fi

    if [ "$st" = "### QUICKFIX_NEEDED" ]; then
      write_runner_note "$run_dir" "Resume: QUICKFIX_NEEDED"
    fi

    if [ "$st" = "### IDLE" ] || { [ "$(current_size_status)" = "LARGE" ] && is_known_large_builder_stage_status "$st"; }; then
      while true; do
        local exit_code=0
        if run_builder "$run_dir"; then
          exit_code=0
        else
          exit_code=$?
        fi
        st="$(read_status)"
        log "Builder: exit=$exit_code status=$st"

        if [ "$st" = "### BUILDER_COMPLETE" ]; then
          LAST_WAS_TROUBLESHOOT=0
          break
        fi

        if [ "$(current_size_status)" = "LARGE" ] && is_known_large_builder_stage_status "$st"; then
          write_runner_note "$run_dir" "Builder: resumable LARGE marker observed ($st); continuing chain"
          LAST_WAS_TROUBLESHOOT=0
          continue
        fi

        if is_unknown_large_builder_stage_status "$st"; then
          if ! handle_blocker "$run_dir" "Builder" "Unknown LARGE stage marker during builder chain: $st"; then
            exit 1
          fi
          if [ "$CARD_DEMOTED" -eq 1 ]; then
            LAST_WAS_TROUBLESHOOT=0
            continue 2
          fi
          st="### IDLE"
          continue
        fi

        if maybe_upscope_small_task "$run_dir" "Builder" "exit=$exit_code status=$st model=${LAST_RUN_MODEL:-unknown}"; then
          LAST_WAS_TROUBLESHOOT=0
          st="### IDLE"
          continue
        fi

        if ! handle_blocker "$run_dir" "Builder" "exit=$exit_code status=$st"; then
          exit 1
        fi
        if [ "$CARD_DEMOTED" -eq 1 ]; then
          LAST_WAS_TROUBLESHOOT=0
          continue 2
        fi
        st="### IDLE"
        continue
      done
    fi

    if [ "$st" = "### BUILDER_COMPLETE" ]; then
      local size_status
      size_status="$(current_size_status)"
      set_status "### IDLE"

      if [ "$size_status" = "LARGE" ] || should_run_integration; then
        if [ "$size_status" = "LARGE" ]; then
          write_runner_note "$run_dir" "Integration route: forced on (size_status=LARGE)"
        fi
        while true; do
          local exit_code=0
          if run_integration "$run_dir"; then
            exit_code=0
          else
            exit_code=$?
          fi
          st="$(read_status)"
          log "Integration: exit=$exit_code status=$st"

          if [ "$exit_code" -eq 0 ] && [ "$st" = "### IDLE" ]; then
            # Some Integration sub-agents have (incorrectly) exited 0 without writing a terminal
            # status flag. If we can positively confirm the integration gate passed, synthesize
            # the missing artifacts/flag so the loop can continue.
            if [ -f "$run_dir/integration_npm_test.log" ] && rg -q '^# fail 0$' "$run_dir/integration_npm_test.log"; then
              if [ ! -f "$run_dir/integration_report.md" ]; then
                cat >"$run_dir/integration_report.md" <<EOF
# Integration Report (Runner Fallback)

The Integration sub-agent exited 0 but did not write \`### INTEGRATION_COMPLETE\` to \`agents/status.md\`.
This report was generated by \`agents/orchestrate_loop.sh\` after confirming the gate passed.

- Gate: \`npm test\` PASS (\`# fail 0\`)
- Log: \`$run_dir/integration_npm_test.log\`
EOF
              fi

              write_runner_note "$run_dir" "Integration: runner fallback (exit=0 but status was ### IDLE); inferred PASS from integration_npm_test.log"
              set_status "### INTEGRATION_COMPLETE"
              st="### INTEGRATION_COMPLETE"
            fi
          fi

          if [ "$st" = "### INTEGRATION_COMPLETE" ]; then
            LAST_WAS_TROUBLESHOOT=0
            set_status "### IDLE"
            integration_counter_on_ran
            break
          fi

          if maybe_upscope_small_task "$run_dir" "Integration" "exit=$exit_code status=$st model=${LAST_RUN_MODEL:-unknown}"; then
            LAST_WAS_TROUBLESHOOT=0
            st="### IDLE"
            continue
          fi

          if ! handle_blocker "$run_dir" "Integration" "exit=$exit_code status=$st"; then
            exit 1
          fi
          if [ "$CARD_DEMOTED" -eq 1 ]; then
            LAST_WAS_TROUBLESHOOT=0
            continue 2
          fi
          st="### IDLE"
          continue
        done
      else
        integration_counter_on_skip
        log "Integration: skipped (mode=${INTEGRATION_MODE:-Low} size_status=$size_status)"
      fi

      while true; do
        local exit_code=0
        if run_qa "$run_dir"; then
          exit_code=0
        else
          exit_code=$?
        fi
        st="$(read_status)"
        log "QA: exit=$exit_code status=$st"

        if [ "$st" = "### QA_COMPLETE" ] || [ "$st" = "### QUICKFIX_NEEDED" ]; then
          LAST_WAS_TROUBLESHOOT=0
          break
        fi

        if maybe_upscope_small_task "$run_dir" "QA" "exit=$exit_code status=$st model=${LAST_RUN_MODEL:-unknown}"; then
          LAST_WAS_TROUBLESHOOT=0
          st="### IDLE"
          continue
        fi

        if ! handle_blocker "$run_dir" "QA" "exit=$exit_code status=$st"; then
          exit 1
        fi
        if [ "$CARD_DEMOTED" -eq 1 ]; then
          LAST_WAS_TROUBLESHOOT=0
          continue 2
        fi
        st="### IDLE"
        continue
      done
    fi

    if [ "$st" = "### INTEGRATION_COMPLETE" ]; then
      set_status "### IDLE"
      integration_counter_on_ran

      while true; do
        local exit_code=0
        if run_qa "$run_dir"; then
          exit_code=0
        else
          exit_code=$?
        fi
        st="$(read_status)"
        log "QA: exit=$exit_code status=$st"

        if [ "$st" = "### QA_COMPLETE" ] || [ "$st" = "### QUICKFIX_NEEDED" ]; then
          LAST_WAS_TROUBLESHOOT=0
          break
        fi

        if maybe_upscope_small_task "$run_dir" "QA" "exit=$exit_code status=$st model=${LAST_RUN_MODEL:-unknown}"; then
          LAST_WAS_TROUBLESHOOT=0
          st="### IDLE"
          continue
        fi

        if ! handle_blocker "$run_dir" "QA" "exit=$exit_code status=$st"; then
          exit 1
        fi
        if [ "$CARD_DEMOTED" -eq 1 ]; then
          LAST_WAS_TROUBLESHOOT=0
          continue 2
        fi
        st="### IDLE"
        continue
      done
    fi

    if [ "$st" = "### QA_COMPLETE" ]; then
      if run_post_qa_update_pipeline "$run_dir" "QA"; then
        finalize_success
        write_runner_note "$run_dir" "QA: QA_COMPLETE"
        LAST_WAS_TROUBLESHOOT=0
        continue
      fi

      if ! handle_blocker "$run_dir" "Update" "${POST_QA_UPDATE_FAILURE_REASON:-post-QA update pipeline failed}"; then
        exit 1
      fi
      if [ "$CARD_DEMOTED" -eq 1 ]; then
        LAST_WAS_TROUBLESHOOT=0
        continue
      fi
      LAST_WAS_TROUBLESHOOT=0
      set_status "### IDLE"
      continue
    fi

    if [ "$st" != "### QUICKFIX_NEEDED" ]; then
      if maybe_upscope_small_task "$run_dir" "Orchestrator" "Unexpected status after QA: $st"; then
        LAST_WAS_TROUBLESHOOT=0
        continue
      fi
      if ! handle_blocker "$run_dir" "Orchestrator" "Unexpected status after QA: $st"; then
        exit 1
      fi
      continue
    fi

    local attempt=1
    while [ "$attempt" -le 2 ]; do
        local quickfix_signature quickfix_fp
        quickfix_signature="$(failure_signature_for_blocker "Quickfix" "quickfix attempt loop" "### QUICKFIX_NEEDED")"
        quickfix_fp="$(material_change_fingerprint)"
        escalation_state_increment_counter "$run_id" "$quickfix_signature" "quickfix" "$quickfix_fp"
        write_runner_note "$run_dir" "Quickfix attempt=$attempt failure_signature=$quickfix_signature"

        while true; do
          set_status "### IDLE"
          local exit_code=0
          if run_hotfix "$run_dir"; then
            exit_code=0
          else
            exit_code=$?
          fi
          st="$(read_status)"
          log "Hotfix: attempt=$attempt exit=$exit_code status=$st"

          if [ "$st" = "### BUILDER_COMPLETE" ]; then
            LAST_WAS_TROUBLESHOOT=0
            break
        fi

        if maybe_upscope_small_task "$run_dir" "Hotfix" "attempt=$attempt exit=$exit_code status=$st model=${LAST_RUN_MODEL:-unknown}"; then
          LAST_WAS_TROUBLESHOOT=0
          st="### IDLE"
          continue
        fi

        if ! handle_blocker "$run_dir" "Hotfix" "attempt=$attempt exit=$exit_code status=$st"; then
          exit 1
        fi
        if [ "$CARD_DEMOTED" -eq 1 ]; then
          LAST_WAS_TROUBLESHOOT=0
          continue 3
        fi
        if [ "${LAST_WAS_TROUBLESHOOT:-0}" -eq 1 ]; then
          write_runner_note "$run_dir" "Hotfix: Troubleshooter recovered blocker; advancing to Doublecheck before retrying hotfix"
          st="### QUICKFIX_NEEDED"
          break
        fi
        continue
      done

        while true; do
          set_status "### IDLE"
          local exit_code=0
          if run_doublecheck "$run_dir"; then
            exit_code=0
          else
            exit_code=$?
          fi
          st="$(read_status)"
          log "Doublecheck: attempt=$attempt exit=$exit_code status=$st"

          if [ "$st" = "### QA_COMPLETE" ] || [ "$st" = "### QUICKFIX_NEEDED" ]; then
            LAST_WAS_TROUBLESHOOT=0
            break
        fi

        if maybe_upscope_small_task "$run_dir" "Doublecheck" "attempt=$attempt exit=$exit_code status=$st model=${LAST_RUN_MODEL:-unknown}"; then
          LAST_WAS_TROUBLESHOOT=0
          st="### IDLE"
          continue
        fi

        if ! handle_blocker "$run_dir" "Doublecheck" "attempt=$attempt exit=$exit_code status=$st"; then
          exit 1
        fi
        if [ "$CARD_DEMOTED" -eq 1 ]; then
          LAST_WAS_TROUBLESHOOT=0
          continue 3
        fi
        continue
      done

      if [ "$st" = "### QA_COMPLETE" ]; then
        if run_post_qa_update_pipeline "$run_dir" "Doublecheck"; then
          finalize_success
          write_runner_note "$run_dir" "Quickfix attempts: $attempt"
          write_runner_note "$run_dir" "Doublecheck: QA_COMPLETE"
          break
        fi

        if ! handle_blocker "$run_dir" "Update" "${POST_QA_UPDATE_FAILURE_REASON:-post-QA update pipeline failed (doublecheck)}"; then
          exit 1
        fi
        if [ "$CARD_DEMOTED" -eq 1 ]; then
          LAST_WAS_TROUBLESHOOT=0
          continue 2
        fi
        LAST_WAS_TROUBLESHOOT=0
        set_status "### IDLE"
        continue 2
      fi

      attempt=$((attempt + 1))
    done

    if [ "$(read_status)" = "### QUICKFIX_NEEDED" ]; then
      if ! handle_blocker "$run_dir" "Quickfix" "Quickfix attempts exhausted (still QUICKFIX_NEEDED)"; then
        exit 1
      fi
      LAST_WAS_TROUBLESHOOT=0
      continue
    fi
  done
}

usage() {
  cat <<'EOF'
Usage: bash agents/orchestrate_loop.sh [--once|--daemon]

  --once    Run until backlog is drained, then exit.
  --daemon  Keep watching for backlog changes and continue running (default).
EOF
}

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --once)
        DAEMON_MODE=false
        ;;
      --daemon)
        DAEMON_MODE=true
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      --)
        shift
        break
        ;;
      *)
        echo "Unknown argument: $1" >&2
        usage >&2
        exit 2
        ;;
    esac
    shift
  done

  if [ "$#" -gt 0 ]; then
    echo "Unexpected positional argument(s): $*" >&2
    usage >&2
    exit 2
  fi
}

orchestrate_main() {
  parse_args "$@"
  preflight
  main_loop
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  orchestrate_main "$@"
fi
