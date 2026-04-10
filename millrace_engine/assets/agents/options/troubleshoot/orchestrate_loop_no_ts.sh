#!/usr/bin/env bash
set -euo pipefail

# Foreground orchestrator loop (local runner; NO troubleshoot-on-blocker).
#
# This script drains agents/tasksbacklog.md by promoting cards into agents/tasks.md,
# then running Builder/Integration/QA (and optional Hotfix/Doublecheck) headlessly.
#
# IMPORTANT:
# - This is a *local* orchestration tool. It intentionally does not rely on a single
#   chat "turn" staying alive for hours/days.
# - It uses agents/status.md as the signaling contract.
# - On hard blockers, it auto-demotes the active card into
#   agents/tasksbackburner.md and continues to the next card.
# - This variant does NOT attempt a Troubleshooter cycle.

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
ARCHIVE="agents/tasksarchive.md"
STATUS="agents/status.md"
HISTORY="agents/historylog.md"

MODEL_CFG="agents/options/model_config.md"
WF_CFG="agents/options/workflow_config.md"

RUNS_DIR="agents/runs"
DIAGS_DIR="agents/diagnostics"
TMP_DIR="agents/.tmp"

HEARTBEAT_SECS="${HEARTBEAT_SECS:-60}"
NOTIFY_ON_BLOCKER="${NOTIFY_ON_BLOCKER:-true}"
DAEMON_MODE="${DAEMON_MODE:-true}"
IDLE_MODE="${IDLE_MODE:-auto}"
IDLE_POLL_SECS="${IDLE_POLL_SECS:-3600}"
IDLE_DEBOUNCE_SECS="${IDLE_DEBOUNCE_SECS:-120}"
IDLE_WATCH_TOOL=""

SCRIPT_START_EPOCH="$(date +%s)"
TASKS_COMPLETED=0
TASKS_DEMOTED=0
CARD_DEMOTED=0

ensure_repo_root() {
  # Long-running loops can end up with an "unlinked" cwd (e.g., WSL/drive remounts),
  # which breaks tools that call getcwd(3) (git, gh, codex). Re-anchor to repo root.
  cd "$REPO_ROOT" 2>/dev/null || {
    echo "FATAL: unable to cd to repo root: $REPO_ROOT" >&2
    exit 1
  }
}

require() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

log() {
  local ts
  ts="$(date '+%F %T')"
  printf '[%s] %s\n' "$ts" "$*" >&2
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

has_active_card() { rg -n '^## ' -q "$TASKS"; }
has_backlog_cards() { rg -n '^## ' -q "$BACKLOG"; }

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
  python3 - "$BACKLOG" "$TASKS" <<'PY'
from pathlib import Path
import re, sys
backlog = Path(sys.argv[1])
tasks = Path(sys.argv[2])
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
if tasks.read_text(encoding="utf-8", errors="replace").strip():
  raise SystemExit("agents/tasks.md is not empty; refusing to overwrite")
tasks.write_text(card, encoding="utf-8")
backlog.write_text(new_text, encoding="utf-8")
print(card.splitlines()[0])
PY
}

archive_active_card_and_clear() {
  python3 - "$TASKS" "$ARCHIVE" <<'PY'
from pathlib import Path
import sys
tasks = Path(sys.argv[1])
archive = Path(sys.argv[2])
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

  python3 - "$TASKS" "$MANUAL_BACKLOG" "$run_dir" "$stage" "$why" "$diag_dir" "$status_at_demote" <<'PY'
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import sys

tasks_path = Path(sys.argv[1])
manual_backlog_path = Path(sys.argv[2])
run_dir = sys.argv[3]
stage = sys.argv[4]
why = sys.argv[5]
diag_dir = sys.argv[6]
status_at_demote = sys.argv[7]

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

  hb="$HEARTBEAT_SECS"
  if ! [[ "$hb" =~ ^[0-9]+$ ]]; then
    hb=60
  fi

  log "Stage $label: runner=$runner model=$model effort=$effort search=$search"
  log "Stage $label: logs: stdout=$stdout_path stderr=$stderr_path"

  case "$runner" in
    codex)
      started_at="$(date +%s)"
      if [ -n "$last_path" ]; then
        timeout 5400 codex "${CODEX_SEARCH_FLAGS[@]}" exec --model "$model" "${CODEX_PERM_FLAGS[@]}" \
          "${CODEX_REASONING_FLAGS[@]}" -o "$last_path" "$prompt" >"$stdout_path" 2>"$stderr_path" &
      else
        timeout 5400 codex "${CODEX_SEARCH_FLAGS[@]}" exec --model "$model" "${CODEX_PERM_FLAGS[@]}" \
          "${CODEX_REASONING_FLAGS[@]}" "$prompt" >"$stdout_path" 2>"$stderr_path" &
      fi
      local pid=$!
      local hb_pid=""
      if [ "$hb" -gt 0 ]; then
        (
          while kill -0 "$pid" >/dev/null 2>&1; do
            sleep "$hb"
            if ! kill -0 "$pid" >/dev/null 2>&1; then
              exit 0
            fi
            elapsed=$(( $(date +%s) - started_at ))
            log "Stage $label: still running (${elapsed}s elapsed)"
          done
        ) &
        hb_pid=$!
      fi
      if wait "$pid"; then
        exit_code=0
      else
        exit_code=$?
      fi
      if [ -n "${hb_pid:-}" ]; then
        kill "$hb_pid" >/dev/null 2>&1 || true
        wait "$hb_pid" >/dev/null 2>&1 || true
      fi
      log "Stage $label: exit=$exit_code"
      return $exit_code
      ;;
    claude)
      started_at="$(date +%s)"
      timeout 5400 claude -p "$prompt" --model "$model" --output-format text "${CLAUDE_PERM_FLAGS[@]}" \
        >"$stdout_path" 2>"$stderr_path" &
      local pid=$!
      local hb_pid=""
      if [ "$hb" -gt 0 ]; then
        (
          while kill -0 "$pid" >/dev/null 2>&1; do
            sleep "$hb"
            if ! kill -0 "$pid" >/dev/null 2>&1; then
              exit 0
            fi
            elapsed=$(( $(date +%s) - started_at ))
            log "Stage $label: still running (${elapsed}s elapsed)"
          done
        ) &
        hb_pid=$!
      fi
      if wait "$pid"; then
        exit_code=0
      else
        exit_code=$?
      fi
      if [ -n "${hb_pid:-}" ]; then
        kill "$hb_pid" >/dev/null 2>&1 || true
        wait "$hb_pid" >/dev/null 2>&1 || true
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

create_diagnostics_and_block() {
  ensure_repo_root

  local run_dir="$1"
  local why="$2"

  local diag_id diag_dir
  diag_id="$(now_run_id)"
  diag_dir="$DIAGS_DIR/$diag_id"
  mkdir -p "$diag_dir"

  cp -a "$run_dir" "$diag_dir/" || true
  cp -a "$TASKS" "$diag_dir/tasks.md" || true
  cp -a "$BACKLOG" "$diag_dir/tasksbacklog.md" || true
  cp -a "$ARCHIVE" "$diag_dir/tasksarchive.md" || true
  cp -a "$HISTORY" "$diag_dir/historylog.md" || true
  cp -a "$STATUS" "$diag_dir/status.md" || true
  test -f agents/quickfix.md && cp -a agents/quickfix.md "$diag_dir/quickfix.md" || true
  cp -a "$MODEL_CFG" "$diag_dir/model_config.md" || true
  cp -a "$WF_CFG" "$diag_dir/workflow_config.md" || true

  git status --porcelain=v1 -uall >"$diag_dir/git_status.txt" || true
  git diff >"$diag_dir/git_diff.txt" || true

  printf 'WHY: %s\nRUN_DIR: %s\nDIAG_DIR: %s\n' "$why" "$run_dir" "$diag_dir" >"$diag_dir/summary.txt"

  echo "$diag_dir"
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
Millrace local orchestrator hit a blocker. The active card was auto-demoted to `agents/tasksbackburner.md` and the loop continued.

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

  CARD_DEMOTED=0

  local diag_dir
  diag_dir="$(create_diagnostics_and_block "$run_dir" "Stage=$stage :: $why")"
  log "Blocker: stage=$stage why=$why"
  log "Blocker: diagnostics=$diag_dir"

  local task
  task="$(active_task_heading)"

  set_status "### BLOCKED"
  echo "BLOCKED: $stage: $why" >&2
  echo "Diagnostics: $diag_dir" >&2
  notify_blocker "$run_dir" "$stage" "$why" "$diag_dir"

  write_runner_note "$run_dir" "Demote: hard blocker; moving card to $MANUAL_BACKLOG"
  demote_active_card_to_manual_backlog_and_clear "$run_dir" "$stage" "$why" "$diag_dir" "### BLOCKED"
  set_status "### IDLE"

  CARD_DEMOTED=1
  TASKS_DEMOTED=$(( TASKS_DEMOTED + 1 ))
  local elapsed
  elapsed=$(( $(date +%s) - SCRIPT_START_EPOCH ))
  log "Progress: tasks_completed=$TASKS_COMPLETED tasks_demoted=$TASKS_DEMOTED elapsed=$(format_duration "$elapsed") demoted_task=\"$task\""
  return 0
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
  require timeout
  mkdir -p "$RUNS_DIR" "$DIAGS_DIR" "$TMP_DIR"

  if [ ! -f "$TASKS" ]; then : >"$TASKS"; fi
  if [ ! -f "$BACKLOG" ]; then : >"$BACKLOG"; fi
  if [ ! -f "$MANUAL_BACKLOG" ]; then
    cat >"$MANUAL_BACKLOG" <<'EOF'
# Tasks Backburner

Backlog for cards auto-demoted by the local orchestrator after hard blockers.
Review and triage these cards before re-adding them to `agents/tasksbacklog.md`.

---

### Auto-demoted blocked cards (from orchestrate_loop.sh)

EOF
  fi
  if [ ! -f "$ARCHIVE" ]; then : >"$ARCHIVE"; fi
  if [ ! -f "$STATUS" ]; then set_status "### IDLE"; fi

  parse_model_config
  parse_workflow_config
  set_permission_flags
  log "Config: BUILDER=$BUILDER_RUNNER/$BUILDER_MODEL QA=$QA_RUNNER/$QA_MODEL INTEGRATION=$INTEGRATION_RUNNER/$INTEGRATION_MODEL"
  log "Config: HOTFIX=$HOTFIX_RUNNER/$HOTFIX_MODEL DOUBLECHECK=$DOUBLECHECK_RUNNER/$DOUBLECHECK_MODEL"
  log "Workflow: INTEGRATION_MODE=${INTEGRATION_MODE:-Low} HEADLESS_PERMISSIONS=${HEADLESS_PERMISSIONS:-Maximum}"

  local runners=(
    "$BUILDER_RUNNER"
    "$QA_RUNNER"
    "$HOTFIX_RUNNER"
    "$DOUBLECHECK_RUNNER"
    "$INTEGRATION_RUNNER"
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
}

run_builder() {
  local run_dir="$1"
  set_status "### IDLE"
  run_cycle "$BUILDER_RUNNER" "$BUILDER_MODEL" "Open agents/_start.md and follow instructions." \
    "$run_dir/builder.stdout.log" "$run_dir/builder.stderr.log" "$run_dir/builder.last.md" false "high" "Builder"
}

run_integration() {
  local run_dir="$1"
  set_status "### IDLE"
  run_cycle "$INTEGRATION_RUNNER" "$INTEGRATION_MODEL" "Open agents/_integrate.md and follow instructions." \
    "$run_dir/integration.stdout.log" "$run_dir/integration.stderr.log" "$run_dir/integration.last.md" false "high" "Integration"
}

run_qa() {
  local run_dir="$1"
  set_status "### IDLE"
  run_cycle "$QA_RUNNER" "$QA_MODEL" "Open agents/_check.md and follow instructions." \
    "$run_dir/qa.stdout.log" "$run_dir/qa.stderr.log" "$run_dir/qa.last.md" true "xhigh" "QA"
}

run_hotfix() {
  local run_dir="$1"
  set_status "### IDLE"
  run_cycle "$HOTFIX_RUNNER" "$HOTFIX_MODEL" "Open agents/_hotfix.md and follow instructions." \
    "$run_dir/hotfix.stdout.log" "$run_dir/hotfix.stderr.log" "$run_dir/hotfix.last.md" false "medium" "Hotfix"
}

run_doublecheck() {
  local run_dir="$1"
  set_status "### IDLE"
  run_cycle "$DOUBLECHECK_RUNNER" "$DOUBLECHECK_MODEL" "Open agents/_doublecheck.md and follow instructions." \
    "$run_dir/doublecheck.stdout.log" "$run_dir/doublecheck.stderr.log" "$run_dir/doublecheck.last.md" true "xhigh" "Doublecheck"
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
}

main_loop() {
  LAST_WAS_TROUBLESHOOT=0

  while true; do
    ensure_repo_root

    # Ensure active card.
    if ! has_active_card; then
      if has_backlog_cards; then
        local promoted
        promoted="$(promote_next_card)"
        log "Promote: $promoted"
      else
        if truthy "$DAEMON_MODE"; then
          local backlog_mtime
          backlog_mtime="$(stat_mtime "$BACKLOG")"
          log "Backlog empty; daemon wait (mode=$IDLE_MODE)"
          wait_for_backlog_change "$backlog_mtime"
          if [ "$IDLE_DEBOUNCE_SECS" -gt 0 ]; then
            log "Backlog update detected; debounce=${IDLE_DEBOUNCE_SECS}s"
            debounce_quiet_period "$BACKLOG" "$IDLE_DEBOUNCE_SECS"
          fi
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
    printf 'Run: %s\nStarted: %s\n' "$run_id" "$(date '+%F %T %Z')" >"$run_dir/runner_notes.md"
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
      "### BLOCKED")
        if ! handle_blocker "$run_dir" "Resume" "agents/status.md is ### BLOCKED at loop start"; then
          exit 1
        fi
        if [ "$CARD_DEMOTED" -eq 1 ]; then
          LAST_WAS_TROUBLESHOOT=0
          continue
        fi
        st="$(read_status)"
        ;;
      "### IDLE"|"### BUILDER_COMPLETE"|"### INTEGRATION_COMPLETE"|"### QUICKFIX_NEEDED"|"### QA_COMPLETE")
        ;;
      *)
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

    if [ "$st" = "### QA_COMPLETE" ]; then
      finalize_success
      write_runner_note "$run_dir" "QA: QA_COMPLETE (resume)"
      LAST_WAS_TROUBLESHOOT=0
      continue
    fi

    if [ "$st" = "### QUICKFIX_NEEDED" ]; then
      write_runner_note "$run_dir" "Resume: QUICKFIX_NEEDED"
    fi

    if [ "$st" = "### IDLE" ]; then
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
      set_status "### IDLE"

      if should_run_integration; then
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
        log "Integration: skipped (mode=${INTEGRATION_MODE:-Low})"
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
      finalize_success
      write_runner_note "$run_dir" "QA: QA_COMPLETE"
      LAST_WAS_TROUBLESHOOT=0
      continue
    fi

    if [ "$st" != "### QUICKFIX_NEEDED" ]; then
      if ! handle_blocker "$run_dir" "Orchestrator" "Unexpected status after QA: $st"; then
        exit 1
      fi
      continue
    fi

    local attempt=1
    while [ "$attempt" -le 2 ]; do
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

        if ! handle_blocker "$run_dir" "Hotfix" "attempt=$attempt exit=$exit_code status=$st"; then
          exit 1
        fi
        if [ "$CARD_DEMOTED" -eq 1 ]; then
          LAST_WAS_TROUBLESHOOT=0
          continue 3
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
        finalize_success
        write_runner_note "$run_dir" "Quickfix attempts: $attempt"
        write_runner_note "$run_dir" "Doublecheck: QA_COMPLETE"
        break
      fi

      attempt=$((attempt + 1))
    done

    if [ "$(read_status)" = "### QUICKFIX_NEEDED" ]; then
      local diag_dir
      diag_dir="$(create_diagnostics_and_block "$run_dir" "Quickfix attempts exhausted (still QUICKFIX_NEEDED)")"
      set_status "### BLOCKED"
      echo "BLOCKED: Quickfix attempts exhausted (still QUICKFIX_NEEDED)" >&2
      echo "Diagnostics: $diag_dir" >&2
      notify_blocker "$run_dir" "Quickfix" "Quickfix attempts exhausted (still QUICKFIX_NEEDED)" "$diag_dir"
      write_runner_note "$run_dir" "Demote: Quickfix attempts exhausted; moving card to $MANUAL_BACKLOG"
      local task elapsed
      task="$(active_task_heading)"
      demote_active_card_to_manual_backlog_and_clear "$run_dir" "Quickfix" "Quickfix attempts exhausted (still QUICKFIX_NEEDED)" "$diag_dir" "### BLOCKED"
      set_status "### IDLE"

      TASKS_DEMOTED=$(( TASKS_DEMOTED + 1 ))
      elapsed=$(( $(date +%s) - SCRIPT_START_EPOCH ))
      log "Progress: tasks_completed=$TASKS_COMPLETED tasks_demoted=$TASKS_DEMOTED elapsed=$(format_duration "$elapsed") demoted_task=\"$task\""
      LAST_WAS_TROUBLESHOOT=0
      continue
    fi
  done
}

preflight
main_loop
