# Millrace

Millrace is a thin-core autonomous runtime packaged as `millrace_ai`.

The runtime bootstraps all operational files under `<workspace>/millrace-agents/` and keeps canonical source code in the package itself.

## Quick Start

```bash
WORKSPACE=/absolute/path/to/workspace

uv run --extra dev python -m millrace_ai compile validate --workspace "$WORKSPACE"
uv run --extra dev python -m millrace_ai run once --workspace "$WORKSPACE"
uv run --extra dev python -m millrace_ai status --workspace "$WORKSPACE"
```

Equivalent installed CLI:

```bash
millrace compile validate --workspace "$WORKSPACE"
millrace run once --workspace "$WORKSPACE"
millrace status --workspace "$WORKSPACE"
```

## Core Commands

- `millrace run once`
- `millrace run daemon --max-ticks N`
- `millrace status show`
- `millrace status watch --workspace <PATH> [--workspace <PATH> ...]`
- `millrace runs ls`
- `millrace runs show <RUN_ID>`
- `millrace runs tail <RUN_ID>`
- `millrace queue ls`
- `millrace queue show <WORK_ITEM_ID>`
- `millrace queue add-task <path-to-task.md|path-to-task.json>`
- `millrace queue add-spec <path-to-spec.md|path-to-spec.json>`
- `millrace queue add-idea <path-to-idea.md>`
- `millrace planning retry-active [--reason "..."]`
- `millrace config show`
- `millrace config validate [--mode MODE_ID]`
- `millrace config reload`
- `millrace control retry-active [--reason "..."]`
- `millrace control pause`
- `millrace control resume`
- `millrace control stop`
- `millrace control clear-stale-state`
- `millrace control reload-config`
- `millrace doctor`
- `millrace modes list`
- `millrace modes show --mode MODE_ID`
- `millrace compile validate [--mode MODE_ID]`
- `millrace compile show [--mode MODE_ID]`

Compatibility aliases for common operator flows remain available at top level:
`millrace add-task`, `millrace add-spec`, `millrace pause`, `millrace resume`, `millrace stop`,
`millrace retry-active`, `millrace clear-stale-state`, and `millrace reload-config`.

## Work Artifacts

Canonical queue artifacts are lightweight headed markdown work documents (`.md`) under:

- `millrace-agents/tasks/{queue,active,done,blocked}/`
- `millrace-agents/specs/{queue,active,done,blocked}/`
- `millrace-agents/incidents/{incoming,active,resolved,blocked}/`

Task/spec/incident files use a human-facing shape with an H1 title plus plain field headings such as:

```md
# Add run inspection CLI

Task-ID: example-task-001
Title: Add run inspection CLI

Target-Paths:
- src/millrace_ai/cli.py

Acceptance:
- `millrace runs ls` reports persisted run summaries.
```

JSON remains runtime-internal for snapshot, diagnostics, mailbox archives, and event/log surfaces.

## Stage Runner Resolution

Runtime stage execution now routes through a configurable runner dispatcher.

Resolution order per stage request:

1. `request.runner_name` (compiled from mode/stage bindings)
2. `runners.default_runner` from runtime config
3. fallback literal `codex_cli`

Default adapter is Codex CLI. Add runner settings in `<workspace>/millrace-agents/millrace.toml`:

```toml
[runners]
default_runner = "codex_cli"

[runners.codex]
command = "codex"
args = ["exec"]
permission_default = "basic"
# permission_by_stage = { builder = "elevated" }
# permission_by_model = { "gpt-5.4" = "maximum" }
skip_git_repo_check = true
```

Permission levels map to Codex CLI flags:

- `basic`: `--full-auto`
- `elevated`: `-c approval_policy="never" --sandbox danger-full-access`
- `maximum`: `--dangerously-bypass-approvals-and-sandbox`

## Runtime Docs

- `docs/runtime/millrace-runtime-architecture.md`
- `docs/runtime/millrace-cli-reference.md`
- `docs/runtime/millrace-entrypoint-mapping.md`
- `docs/runtime/millrace-runner-architecture.md`

## Verification

Authoritative local verification commands:

```bash
uv run --extra dev python -m pytest -q
uv run --extra dev ruff check src/millrace_ai tests
uv run --extra dev mypy src/millrace_ai
```

Operational source + wheel checks (minimum functionality workspace):

```bash
rm -rf /Users/timinator/Desktop/Millrace-Dev/workspaces/minimum-functionality/millrace-agents

uv run --extra dev python -m millrace_ai compile validate --workspace /Users/timinator/Desktop/Millrace-Dev/workspaces/minimum-functionality
uv run --extra dev python -m millrace_ai run once --workspace /Users/timinator/Desktop/Millrace-Dev/workspaces/minimum-functionality
uv run --extra dev python -m millrace_ai status --workspace /Users/timinator/Desktop/Millrace-Dev/workspaces/minimum-functionality

rm -rf /Users/timinator/Desktop/Millrace-Dev/workspaces/minimum-functionality/millrace-agents

uv build --wheel
python3 -m venv /tmp/millrace-wheel-test
source /tmp/millrace-wheel-test/bin/activate
pip install dist/*.whl
millrace compile validate --workspace /Users/timinator/Desktop/Millrace-Dev/workspaces/minimum-functionality
millrace run once --workspace /Users/timinator/Desktop/Millrace-Dev/workspaces/minimum-functionality
millrace status --workspace /Users/timinator/Desktop/Millrace-Dev/workspaces/minimum-functionality
```

For clean proof runs, refresh only `workspaces/minimum-functionality/millrace-agents/` in place. Do not mutate operator-authored files elsewhere in `workspaces/minimum-functionality/`.
