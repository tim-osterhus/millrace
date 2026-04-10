# Legacy Shell Autonomy Reference

This file is retained only to document the retired shell-loop surface. It is not the supported Millrace operator runbook.

For the supported Python and TUI operator path, use `../OPERATOR_GUIDE.md`. For the retained shell-asset status table, use `legacy/README.md`.

## Support Status

- `bash agents/orchestrate_loop.sh ...`: removed from the supported path
- `agents/orchestrate_loop.sh`: compatibility-only
- `bash agents/research_loop.sh ...`: removed from the supported path
- `agents/research_loop.sh`: reference-only
- `agents/options/workflow_config.md` and `agents/options/model_config.md`: compatibility-only fallback inputs
- `agents/AUTONOMY_COMPLETE` and `agents/STOP_AUTONOMY`: compatibility-only markers

## Supported Replacement

Run from the workspace root:

```bash
python3 -m millrace_engine --config millrace.toml health --json
python3 -m millrace_engine --config millrace.toml status --detail --json
python3 -m millrace_engine --config millrace.toml start --once
python3 -m millrace_engine --config millrace.toml research --json
python3 -m millrace_engine --config millrace.toml publish preflight --json
python3 -m millrace_engine --config millrace.toml publish commit --no-push --json
```

## Legacy Notes

- The Python runtime remains the authority for execution status, research status, audit visibility, queue state, and publish readiness.
- Legacy markdown config files remain readable only as fallback compatibility inputs when native `millrace.toml` is absent.
- `AUTONOMY_COMPLETE` and `STOP_AUTONOMY` are still recognized by the runtime for compatibility, but operators should use `status`, `pause`, and `stop`.
- Shell-loop evidence may still matter for parity work or historical investigation, but it is not part of supported day-to-day operation.
