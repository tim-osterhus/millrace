# Millrace Operator Guide

This guide covers the supported operator workflow for the current Millrace runtime.

## Runtime Model

- Package namespace: `millrace_ai`
- Installed CLI: `millrace`
- Runtime workspace root: `<workspace>/millrace-agents/`
- Default config path: `<workspace>/millrace-agents/millrace.toml`

## Supported Commands

Run from the package repo (module form):

```bash
uv run --extra dev python -m millrace_ai <command>
```

Run from an installed environment (CLI form):

```bash
millrace <command>
```

Primary commands:

- `run once`
- `run daemon --max-ticks N`
- `status`
- `runs ls`
- `runs show <RUN_ID>`
- `runs tail <RUN_ID>`
- `queue ls`
- `queue add-task <task.md|task.json>`
- `queue add-spec <spec.md|spec.json>`
- `queue add-idea <idea.md>`
- `planning retry-active --reason "..."`
- `config show`
- `config validate [--mode MODE_ID]`
- `config reload`
- `pause`
- `resume`
- `stop`
- `retry-active --reason "..."`
- `modes list`
- `compile validate [--mode MODE_ID]`

## Basic Workflow

1. Validate compile state:

```bash
millrace compile validate --workspace /absolute/path/to/workspace
```

2. Execute one tick:

```bash
millrace run once --workspace /absolute/path/to/workspace
```

3. Inspect runtime status:

```bash
millrace status --workspace /absolute/path/to/workspace
```

4. Inspect queues:

```bash
millrace queue ls --workspace /absolute/path/to/workspace
```

5. Inspect recent runs:

```bash
millrace runs ls --workspace /absolute/path/to/workspace
```

## Notes

- Canonical queue artifacts are headed markdown documents under `<workspace>/millrace-agents/{tasks,specs,incidents}/`.
- Treat files under `<workspace>/millrace-agents/state/` and queue directories as runtime-owned; mutate through CLI commands.
