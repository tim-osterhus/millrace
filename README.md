# Millrace

Millrace is a thin-core autonomous runtime packaged as `millrace_ai`.

The runtime bootstraps all operational files under `<workspace>/millrace-agents/` and keeps canonical source code in the package itself.

## Source Layout

- importable code lives under `src/millrace_ai/`
- tests mirror package ownership under `tests/assets/`, `tests/cli/`, `tests/config/`, `tests/runners/`, `tests/runtime/`, `tests/workspace/`, and `tests/integration/`
- preserved compatibility facades remain at the package root for legacy imports such as `millrace_ai.control`, `millrace_ai.run_inspection`, `millrace_ai.runner`, `millrace_ai.entrypoints`, and workspace support modules

Use `docs/source-package-map.md` for the old-to-new module map and the intentionally preserved facade list.

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

## Operator Surface

Use the shortest truthful forms for routine operation:

- `millrace run once`
- `millrace status`
- `millrace runs ls`
- `millrace queue add-task <path-to-task.md|path-to-task.json>`
- `millrace pause`, `millrace resume`, `millrace stop`

`millrace status show` remains available as the explicit subcommand form, but the canonical operator example is `millrace status`.

Use `docs/OPERATOR_GUIDE.md` for the current operator workflow and `docs/runtime/millrace-cli-reference.md` for the full command inventory, grouped forms, and alias details.

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
- src/millrace_ai/cli/commands/runs.py

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
- `docs/source-package-map.md`

## Verification

Authoritative local verification commands:

```bash
uv run --extra dev python -m pytest -q
uv run --with ruff ruff check src/millrace_ai tests
uv run --with mypy mypy src/millrace_ai
```

Operational source + wheel checks (minimum functionality workspace):

```bash
WORKSPACE=/absolute/path/to/minimum-functionality-workspace

rm -rf "$WORKSPACE/millrace-agents"

uv run --extra dev python -m millrace_ai compile validate --workspace "$WORKSPACE"
uv run --extra dev python -m millrace_ai run once --workspace "$WORKSPACE"
uv run --extra dev python -m millrace_ai status --workspace "$WORKSPACE"

rm -rf "$WORKSPACE/millrace-agents"

uv build --wheel
python3 -m venv /tmp/millrace-wheel-test
source /tmp/millrace-wheel-test/bin/activate
pip install dist/*.whl
millrace compile validate --workspace "$WORKSPACE"
millrace run once --workspace "$WORKSPACE"
millrace status --workspace "$WORKSPACE"
```

For clean proof runs, refresh only `"$WORKSPACE/millrace-agents/"` in place. Do not mutate operator-authored files elsewhere in the workspace root.
