# Millrace

> A governed runtime for long-running agent work that needs durable state,
> staged execution, and recovery-aware operation.

[![PyPI](https://img.shields.io/pypi/v/millrace-ai.svg)](https://pypi.org/project/millrace-ai/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/github/license/tim-osterhus/millrace.svg)](LICENSE)

Millrace is not a replacement for Codex, Claude Code, Aider, or similar raw
agent harnesses. It is the runtime layer you put around them when the work is
too long-running, stateful, or recovery-sensitive to trust to a single session.

Millrace is designed to be operated by a dedicated ops agent. The raw harness
still does the local stage work. Millrace owns the queue, compiled plan, stage
progression, runtime state, and persisted audit trail around that work.

## Why Millrace Exists

Raw harnesses are excellent at bounded sessions:

- implement a feature
- fix a bug
- review a diff
- work a short plan in one sitting

Millrace starts where that model stops being enough.

Use it when the job needs to:

- survive pauses, crashes, or context loss
- move through explicit execution or planning stages
- preserve durable queue and runtime state on disk
- route failures into recovery work instead of just exiting
- leave behind persisted run artifacts that an operator can inspect later

## How Millrace Fits With Raw Harnesses

Think of the split this way:

- the raw harness reasons, edits code, and emits a stage result
- Millrace compiles the active mode and loops into a frozen plan
- Millrace decides which stage runs next, which work item is active, and what
  to persist after each handoff
- the ops agent chooses when work enters the runtime and how the workspace is
  configured

Millrace should be used in conjunction with raw harnesses, not instead of them.
If a direct Codex or Claude Code session is enough, use the direct session.

## When To Use Millrace

Use Millrace when:

- the work will outlast a single agent session
- you want explicit stage gates instead of "the agent said it is done"
- recovery and resumability matter
- you need run history, diagnostics, and durable state under
  `<workspace>/millrace-agents/`
- a dedicated ops agent is available to operate the runtime intentionally

Do not use Millrace when:

- the task is small and bounded
- the work is exploratory and governance adds more overhead than value
- single-session throughput matters more than persistence and recovery
- no ops agent is available to manage intake, configuration, and runtime state

## 60-Second Proof

Install:

```bash
pip install millrace-ai
```

Then point Millrace at a workspace:

```bash
export WORKSPACE=/absolute/path/to/your/workspace

millrace compile validate --workspace "$WORKSPACE"
millrace run once --workspace "$WORKSPACE"
millrace status --workspace "$WORKSPACE"
```

That flow proves three important things quickly:

- Millrace can bootstrap its workspace contract under `millrace-agents/`
- the selected mode and loops compile into a frozen plan
- the runtime can execute one deterministic tick and report persisted status

## What Millrace Governs

Millrace is a real runtime, not a thin wrapper script. The current shipped core
includes:

- a compile step that freezes mode and loop assets into a persisted run plan
- separate execution and planning loops
- typed stage terminals rather than prose-only handoff semantics
- file-backed runtime state and queue artifacts under `millrace-agents/`
- persisted run artifacts for inspection and troubleshooting
- runner dispatch that invokes a raw harness through a defined adapter contract

The runtime is packaged as `millrace_ai`. Source lives under `src/millrace_ai/`
and the tests mirror those domains under `tests/`.

## Docs And Skills

If you are an agent reading this README, load
`docs/skills/millrace-ops-agent-manual/SKILL.md` first before operating Millrace.

Primary docs:

- `docs/skills/millrace-ops-agent-manual/SKILL.md`
- `docs/runtime/README.md`
- `docs/runtime/millrace-cli-reference.md`
- `docs/runtime/millrace-compiler-and-frozen-plans.md`
- `docs/runtime/millrace-modes-and-loops.md`
- `docs/runtime/millrace-loop-authoring.md`
- `docs/runtime/millrace-runner-architecture.md`
- `docs/runtime/millrace-runtime-error-codes.md`
- `docs/source-package-map.md`

Use the CLI reference for the full command inventory. Use the compiler and loop
docs when you need to understand or extend the runtime contract rather than just
operate it.

## Default Permission Posture

Millrace intentionally ships with maximum Codex permissions as the default
workspace posture.

The default resolution order is:

1. `runners.codex.permission_by_stage`
2. `runners.codex.permission_by_model`
3. `runners.codex.permission_default`

New workspaces are bootstrapped with `permission_default = "maximum"` explicitly
written into `millrace-agents/millrace.toml`.

Existing workspace configs are preserved on bootstrap/update. If an operator has
already customized Codex permission settings, Millrace does not overwrite those
choices when a newer runtime version is deployed.

## Verification

Authoritative local verification commands:

```bash
uv run --extra dev python -m pytest -q
uv run --with ruff ruff check src/millrace_ai tests
uv run --with mypy mypy src/millrace_ai
```

## Status

Millrace is close to the `v1.0.0` baseline, but it is still stabilizing its
public documentation and some surrounding release surfaces. If you depend on a
specific behavior, pin to a patch version instead of assuming the latest build
is identical.

The formal `CHANGELOG.md` is staged for the `v1.0.0` cut; until then, the
changelog format is being prepared outside the package repo so the eventual move
is mechanical rather than improvised.

## License

See `LICENSE`.
