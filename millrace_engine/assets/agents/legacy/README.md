# Legacy Shell Support Status

These files remain packaged only for compatibility and historical reference. None of them are part of the supported Python operator workflow described in `../../OPERATOR_GUIDE.md`.

## Status Table

- `agents/orchestrate_loop.sh`: compatibility-only. Retained for historical cutover context and limited legacy workspace compatibility, not as an authoritative runtime path.
- `agents/research_loop.sh`: reference-only. Retained as a historical/bash parity artifact, not as a supported research operator path.
- `agents/options/workflow_config.md` and `agents/options/model_config.md`: compatibility-only. The Python runtime can still read them as fallback config inputs, but `millrace.toml` is authoritative.
- `agents/AUTONOMY_COMPLETE` and `agents/STOP_AUTONOMY`: compatibility-only. They remain accepted legacy markers, but operators should use the Python CLI control surfaces instead.
- `agents/legacy/_orchestrate.md`: reference-only.
- `agents/legacy/_supervisor.md`: reference-only.

## Supported Replacement

- Use `python3 -m millrace_engine --config millrace.toml health --json` for bootstrap and cutover preflight.
- Use `status`, `queue`, `start`, `pause`, `resume`, `stop`, `research`, `run-provenance`, and `publish` from the Python CLI for supported operation.
- Do not treat any shell loop or legacy prompt in this folder as authoritative.
