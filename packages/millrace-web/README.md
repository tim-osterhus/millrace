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

If `millrace-web` is not available from PyPI in your environment yet, install
the matching `millrace_web-*.whl` asset attached to the GitHub release instead.

The server binds to `127.0.0.1:8765` by default and only serves workspaces
explicitly passed with `--workspace`.

## Views

- `Detail`: the default dense operator view for active runtime state, queues,
  run artifacts, compiled plan identity, usage governance, and Arbiter status.
- `Flow`: a visual runtime-flow view over the same read-only backend data.

Both views share the same DTOs and refresh loop. The first release does not
mount write or control routes.

## Safety Model

The dashboard reads initialized workspace state under `millrace-agents/` and
does not acquire the daemon ownership lock. Future interactivity should be
added through explicit control routes that call Millrace's supported runtime
control surfaces, not by writing runtime files directly.
