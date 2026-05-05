# Millrace Compiled Stage Graphs And Run Traces

Millrace has two graph-shaped inspection surfaces with different authority.

The compiled stage graph is the reusable control-flow topology produced by the
compiler from mode, graph-loop, stage-kind, entrypoint, config, and packaged
asset inputs. It lives inside `<workspace>/millrace-agents/state/compiled_plan.json`
and remains the runtime authority for activation, request binding, recovery,
completion behavior, plane concurrency, and post-stage routing.

The run trace graph is historical evidence for one concrete run. It records
which stage-request instances ran, which terminal outcomes they produced, which
runtime routing decision was applied, which artifacts were written, and which
follow-up work was spawned. New runs write
`<workspace>/millrace-agents/runs/<run_id>/run_trace.json`.

Do not describe the compiled topology as a DAG. Shipped control-flow graphs can
contain intentional recovery cycles. A run trace is usually acyclic because it
records events that already happened, but it is still an inspection artifact,
not a routing authority.

## Compiled Stage Graph Export

Use this when an operator or external agent needs to understand the legal
runtime topology for the selected mode:

```bash
millrace compile graph --workspace <workspace>
millrace compile graph --workspace <workspace> --plane execution
millrace compile graph --workspace <workspace> --format json
millrace compile graph --workspace <workspace> --output compiled-graphs.json
```

The JSON output is a list of `CompiledStageGraphExport` objects. Each export
includes the compiled plan id, mode id, loop id, plane, node bindings, edges,
entry surfaces, terminal states, and source references. The command compiles
and persists the selected plan the same way `compile validate` and
`compile show` do; it does not change queue or runtime snapshot state.

## Run Trace Inspection

Use this when diagnosing why a run followed a specific path:

```bash
millrace runs trace <run_id> --workspace <workspace>
millrace runs trace <run_id> --workspace <workspace> --format json
millrace runs trace <run_id> --workspace <workspace> --output run-trace.json
```

The JSON output is a `RunTraceGraph`. Trace nodes represent concrete stage
results. Trace edges represent the runtime's authoritative router decision
after each result. Edge targets may point to a next compiled node, a terminal
state, blocked/handoff status, or spawned work such as a planning incident or
learning request.

Existing run directories from older releases are still inspectable. If
`run_trace.json` is absent, Millrace derives a read-only fallback trace from
stage-result artifacts and marks it `incomplete`. If `run_trace.json` is
malformed, Millrace returns a fallback trace with a diagnostic note and leaves
the original stage results untouched.

Trace writing is best-effort and runtime-owned. Stage workers do not write
traces directly, and trace data is never used as a second source of routing
truth.

## Web UI Use

The optional `millrace-web` package uses the same graph and trace readers:

- `/api/workspaces/<workspace_id>/compiled-plan/graphs` returns compiled graph
  exports.
- `/api/workspaces/<workspace_id>/runs/<run_id>/trace` returns a compact run
  trace summary.
- The Flow view renders compiled topology as the stable lane structure and
  overlays active runtime state plus recent trace outcomes when available.

The dashboard remains read-only. It does not acquire the daemon ownership lock
and does not expose queue or control mutation routes.
