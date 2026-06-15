# Millrace Web Documentation

`millrace-web` is a local observer for Millrace runtime workspaces. It is not a
runtime owner and it does not change queue, daemon, Arbiter, Learning, compiled
plan, or usage-governance state.

## Command

```bash
millrace-web serve --workspace /path/to/workspace
```

Options:

- `--workspace PATH`: repeatable initialized workspace path
- `--host HOST`: defaults to `127.0.0.1`
- `--port PORT`: defaults to `8765`
- `--view detail|flow`: defaults to `detail`
- `--poll-interval-seconds FLOAT`: defaults to `1.0`

## API

The current dashboard exposes read-only routes under `/api`. It intentionally
does not mount `/control` or queue mutation routes.

The static Detail and Flow views poll workspace summaries. Flow preserves its
animated graph DOM between unchanged responses so the dashboard can keep
refreshing state without restarting visual lane effects every second.

Graph and trace data use the same read-only contracts as the CLI:

- `/api/workspaces/<workspace_id>/compiled-plan/graphs` returns compiled stage
  graph exports derived from `compiled_plan.json`.
- `/api/workspaces/<workspace_id>/runs/<run_id>/trace` returns a compact trace
  summary from `run_trace.json`, or from stage-result fallback inspection when
  the trace artifact is absent.
- Flow renders compiled topology as stable lane structure and overlays active
  runtime state plus recent trace outcomes.

## Future Controls

Future interactive controls should use Millrace `RuntimeControl` or the same
intake/control functions used by the CLI. The web server should not write
authoritative runtime files directly.
