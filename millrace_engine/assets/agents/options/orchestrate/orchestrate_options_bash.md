# Orchestrator Options (Bash/WSL Templates)

This document is a copy/paste scratchpad for running Millrace cycles headlessly from a WSL/Linux shell.

It is intentionally kept out of `agents/_orchestrate.md` to keep the Runner entrypoint small.

## Assumptions

- You are running from the repo root (the directory containing `agents/`).
- You have the CLIs you plan to use installed:
  - `codex` (Codex CLI)
  - `claude` (Claude Code CLI), if used

## Create a run folder + run a cycle (bash)

```bash
RUN_ID="$(date +%F_%H%M%S)"
RUN_DIR="agents/runs/$RUN_ID"
mkdir -p "$RUN_DIR"

trim() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"  # leading
  s="${s%"${s##*[![:space:]]}"}"  # trailing
  printf '%s' "$s"
}

parse_model_config() {
  local in_active=0
  local line key value

  while IFS= read -r line || [ -n "$line" ]; do
    if [ "$in_active" -eq 0 ]; then
      case "$line" in
        "## Active config"*) in_active=1 ;;
      esac
      continue
    fi

    case "$line" in
      ---) break ;;
    esac

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
        *) echo "Warning: Unknown key in model_config (ignored): $key" >&2 ;;
      esac
    fi
  done < "agents/options/model_config.md"

  local required=(BUILDER_RUNNER BUILDER_MODEL QA_RUNNER QA_MODEL HOTFIX_RUNNER HOTFIX_MODEL DOUBLECHECK_RUNNER DOUBLECHECK_MODEL)
  for k in "${required[@]}"; do
    if [ -z "${!k:-}" ]; then
      echo "Missing $k in Active config block" >&2
      return 1
    fi
  done
}

parse_workflow_config() {
  local line key value

  while IFS= read -r line || [ -n "$line" ]; do
    if [[ "$line" =~ ^##[[:space:]]*([A-Z0-9_]+)[[:space:]]*=[[:space:]]*(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      value="$(trim "${BASH_REMATCH[2]}")"

      case "$key" in
        INITIALIZED) INITIALIZED="$value" ;;
        INTEGRATION_MODE) INTEGRATION_MODE="$value" ;;
        INTEGRATION_COUNT) INTEGRATION_COUNT="$value" ;;
        INTEGRATION_TARGET) INTEGRATION_TARGET="$value" ;;
        HEADLESS_PERMISSIONS) HEADLESS_PERMISSIONS="$value" ;;
        SHELL_TEMPLATES) SHELL_TEMPLATES="$value" ;;
        UI_VERIFY_MODE) UI_VERIFY_MODE="$value" ;;
        UI_VERIFY_EXECUTOR) UI_VERIFY_EXECUTOR="$value" ;;
        UI_VERIFY_ANALYZER) UI_VERIFY_ANALYZER="$value" ;;
        UI_VERIFY_COVERAGE) UI_VERIFY_COVERAGE="$value" ;;
        UI_VERIFY_QUOTA_GUARD) UI_VERIFY_QUOTA_GUARD="$value" ;;
        UI_VERIFY_BROWSER_PROFILE) UI_VERIFY_BROWSER_PROFILE="$value" ;;
        *) echo "Warning: Unknown key in workflow_config (ignored): $key" >&2 ;;
      esac
    fi
  done < "agents/options/workflow_config.md"

  if [ -z "${HEADLESS_PERMISSIONS:-}" ]; then
    echo "Missing HEADLESS_PERMISSIONS in workflow_config" >&2
    return 1
  fi
}

set_permission_flags() {
  case "$HEADLESS_PERMISSIONS" in
    Normal)
      CODEX_PERM_FLAGS=(--full-auto)
      CLAUDE_PERM_FLAGS=()
      ;;
    Elevated)
      CODEX_PERM_FLAGS=(--full-auto --sandbox danger-full-access)
      CLAUDE_PERM_FLAGS=(--permission-mode acceptEdits)
      ;;
    Maximum)
      CODEX_PERM_FLAGS=(--full-auto --dangerously-bypass-approvals-and-sandbox)
      CLAUDE_PERM_FLAGS=(--dangerously-skip-permissions)
      ;;
    *)
      echo "Unknown HEADLESS_PERMISSIONS: $HEADLESS_PERMISSIONS" >&2
      return 1
      ;;
  esac
}

parse_model_config || exit 1
parse_workflow_config || exit 1
set_permission_flags || exit 1

run_cycle() {
  runner="$1"
  model="$2"
  prompt="$3"
  stdout_path="$4"
  stderr_path="$5"
  last_path="$6"
  codex_search="${7:-}"

  local CODEX_SEARCH_FLAGS=()
  if [ "$codex_search" = "true" ]; then
    CODEX_SEARCH_FLAGS=(--search)
  fi

  if [ "$runner" = "codex" ]; then
    if [ -n "$last_path" ]; then
      codex exec --model "$model" "${CODEX_PERM_FLAGS[@]}" "${CODEX_SEARCH_FLAGS[@]}" -o "$last_path" "$prompt" >"$stdout_path" 2>"$stderr_path"
    else
      codex exec --model "$model" "${CODEX_PERM_FLAGS[@]}" "${CODEX_SEARCH_FLAGS[@]}" "$prompt" >"$stdout_path" 2>"$stderr_path"
    fi
    return $?
  fi

  if [ "$runner" = "claude" ]; then
    claude -p "$prompt" --model "$model" --output-format text "${CLAUDE_PERM_FLAGS[@]}" >"$stdout_path" 2>"$stderr_path"
    exit_code=$?
    if [ -n "$last_path" ]; then
      cp -f "$stdout_path" "$last_path"
    fi
    return $exit_code
  fi

  echo "Unknown runner: $runner (expected codex|claude). Check agents/options/model_config.md" >&2
  return 1
}

# Builder
run_cycle "$BUILDER_RUNNER" "$BUILDER_MODEL" "Open agents/_start.md and follow instructions." \
  "$RUN_DIR/builder.stdout.log" "$RUN_DIR/builder.stderr.log" "$RUN_DIR/builder.last.md"

# QA
run_cycle "$QA_RUNNER" "$QA_MODEL" "Open agents/_check.md and follow instructions." \
  "$RUN_DIR/qa.stdout.log" "$RUN_DIR/qa.stderr.log" "$RUN_DIR/qa.last.md"
```

## Optional cycles (only if your repo enables them)

Integration cycle (only if `agents/_orchestrate.md` has Integration enabled):

```bash
run_cycle "$INTEGRATION_RUNNER" "$INTEGRATION_MODEL" "Open agents/_integrate.md and follow instructions." \
  "$RUN_DIR/integration.stdout.log" "$RUN_DIR/integration.stderr.log" "$RUN_DIR/integration.last.md"
```

Hotfix + Doublecheck (only if QA writes `### QUICKFIX_NEEDED`):

```bash
run_cycle "$HOTFIX_RUNNER" "$HOTFIX_MODEL" "Open agents/_hotfix.md and follow instructions." \
  "$RUN_DIR/hotfix.stdout.log" "$RUN_DIR/hotfix.stderr.log" "$RUN_DIR/hotfix.last.md"

run_cycle "$DOUBLECHECK_RUNNER" "$DOUBLECHECK_MODEL" "Open agents/_doublecheck.md and follow instructions." \
  "$RUN_DIR/doublecheck.stdout.log" "$RUN_DIR/doublecheck.stderr.log" "$RUN_DIR/doublecheck.last.md"
```

Troubleshooter (only if installed and enabled by `agents/_orchestrate.md`):

```bash
run_cycle "codex" "gpt-5.3-codex" "Open agents/_troubleshoot.md and follow instructions. For context: \"<blocker summary>\"" \
  "$RUN_DIR/troubleshoot.stdout.log" "$RUN_DIR/troubleshoot.stderr.log" "$RUN_DIR/troubleshoot.last.md"
```

## Timeouts (bash)

If you have GNU `timeout`, wrap a call like:

```bash
timeout 5400 codex exec --model gpt-5.3-codex --full-auto -o "$RUN_DIR/builder.last.md" "Open agents/_start.md and follow instructions."
```

## Diagnostics PR helpers (gh)

Example commands (run from the diagnostics branch):

```bash
gh pr create --title "Diagnostics: runner blocked $(date +%F\ %T)" --body "See agents/diagnostics/<DIAG_DIR> for logs and snapshots."
gh pr comment --body "@codex Diagnose why the local runner got blocked. Read agents/diagnostics/<DIAG_DIR> and propose the smallest fix to unblock the runner."
```

## Notes

- Keep the *prompt string* exactly as specified in the Runner entrypoints; only flags/redirects may change.
- Permission flags are controlled by `HEADLESS_PERMISSIONS` in `agents/options/workflow_config.md`.
- Execution stages are offline-default; keep QA/Doublecheck without `--search` unless an active task explicitly overrides this.
