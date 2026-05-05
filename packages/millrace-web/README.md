# Millrace Web

`millrace-web` is an optional local, read-only dashboard for initialized
Millrace workspaces. It is shipped as a separate package so the base
`millrace-ai` runtime stays lightweight and does not include web dependencies,
web modules, or static assets.

```bash
pip install millrace-web
millrace-web serve --workspace /path/to/workspace
millrace-web serve --workspace /path/a --workspace /path/b
```

The server binds to `127.0.0.1:8765` by default and only serves workspaces
explicitly passed with `--workspace`.

## Views

- `Detail`: the default dense operator view for active runtime state, queues,
  run artifacts, compiled plan identity, usage governance, and Arbiter status.
- `Flow`: a visual runtime-flow view over compiled stage graph topology,
  active runtime state, and recent run-trace outcomes when available.

Both views share the same DTOs and refresh loop. The dashboard does not mount
write or control routes.

`0.17.2` keeps the Flow graph DOM stable between unchanged poll responses so
animated lane effects continue smoothly while the rest of the read-only state
refreshes.

The API also exposes graph/trace-specific read surfaces:

- `/api/workspaces/<workspace_id>/compiled-plan/graphs`
- `/api/workspaces/<workspace_id>/runs/<run_id>/trace`

## Safety Model

The dashboard reads initialized workspace state under `millrace-agents/` and
does not acquire the daemon ownership lock. Future interactivity should be
added through explicit control routes that call Millrace's supported runtime
control surfaces, not by writing runtime files directly.
