# Millrace Advisor Agent Prompt

This file is for agents acting as the local operator shell over a Millrace workspace.

## Runtime Assumptions

- Active package namespace: `millrace_ai`
- CLI command: `millrace`
- Runtime workspace root: `<workspace>/millrace-agents/`
- Default config path: `<workspace>/millrace-agents/millrace.toml`

Use module form during source development:

```bash
uv run python -m millrace_ai <command>
```

Use installed CLI form otherwise:

```bash
millrace <command>
```

## Supported Control Surface

Use only supported CLI commands:

- `run once`
- `run daemon --max-ticks N`
- `status`
- `queue ls`
- `add-task <task.json>`
- `add-spec <spec.json>`
- `pause`
- `resume`
- `stop`
- `retry-active --reason "..."`
- `modes list`
- `compile validate [--mode MODE_ID]`

## Operating Rules

- Do not edit runtime-owned files directly under `millrace-agents/state` or queue folders.
- Use command outputs as the authority for current runtime state.
- Write intake files (`task.json` / `spec.json`) explicitly, then enqueue through CLI.
- Keep intake outcome-focused; do not embed stage-routing instructions in intake content.

## Quick Diagnostic Sequence

1. `millrace compile validate --workspace <workspace>`
2. `millrace status --workspace <workspace>`
3. `millrace queue ls --workspace <workspace>`
4. `millrace run once --workspace <workspace>` (if safe to tick)
5. `millrace status --workspace <workspace>`

## References

- `docs/OPERATOR_GUIDE.md`
- `docs/RUNTIME_DEEP_DIVE.md`
- `docs/runtime/README.md`
