# Millrace Compiled Stage Graphs And Run Traces

Millrace has two graph-shaped inspection surfaces with different authority.

The compiled stage graph is the reusable control-flow topology produced by the
compiler from mode, graph-loop, stage-kind, entrypoint, config, and packaged
asset inputs. It lives inside `<workspace>/millrace-agents/state/compiled_plan.json`
and remains the runtime authority for activation, request binding, recovery,
completion behavior, plane concurrency, and post-stage routing.

Use `docs/graphs/graphs-index.md` for the shipped mode-to-plane graph
configurations and per-plane topology references. Use this document for the
compiled export and per-run trace inspection surfaces.

The run trace graph is historical evidence for one concrete run. It records
which stage-request instances ran, which terminal outcomes they produced, which
graph-resolved or inferred terminal-action/lifecycle/runtime-operation metadata
was applied, which artifacts were written, and which follow-up work was
spawned. Trace edges carry terminal-state/action, router consequence, lifecycle
plan/action, terminal writes status, failure class, incident creation, runtime
operation, and terminal metadata source fields so graph-resolved edges stay
distinct from inferred fallback summaries. New runs write
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
the original stage results untouched. Run inspection surfaces `graph_resolved`,
`inferred`, and `unknown` provenance labels so derived traces do not masquerade
as authoritative router output.

Trace writing is best-effort and runtime-owned. Stage workers do not write
traces directly, and trace data is never used as a second source of routing
truth.

## Read-Only Inspection

Compiled graph exports and run traces are read-only inspection surfaces. They
do not acquire the daemon ownership lock, mutate queues, or influence runtime
routing. Use the CLI commands above when an operator or external tool needs to
inspect legal topology or one concrete run path.
