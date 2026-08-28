# Maintainer Live Workflow Qualification

Source release readiness, publication, and provider-backed live workflow
evidence are separate claims. Offline tests can support source readiness only.
They do not publish artifacts, invoke a provider, or prove workflow closure.

## Maintained Checkout Evidence

The maintained live-related module is
`tests/e2e/test_actual_model_workflow_smoke.py`. Its unmarked tests validate
bounded configuration, selected runner identity, redaction, artifact roots,
and failure classification without calling a model:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/e2e/test_actual_model_workflow_smoke.py -m "not live_model"
```

Its `live_model` row accepts bounded Codex preflight and then skips. It is not
a completed provider-backed workflow proof. The checkout does not currently
ship separate `simple_loop` or `vendor_selection` live-proof modules, so do
not cite those historical paths or treat their absence as a passing live row.

## Exact-Candidate Procedure

A release-candidate live qualification must:

1. build the candidate distributions deterministically and record their
   digests;
2. install the exact built wheels in a fresh environment;
3. import the workflow package from the installed distribution bytes;
4. use a fresh workspace outside the source repository;
5. select a workflow whose declared runner binding matches the configured
   adapter;
6. enqueue through the public CLI and use finite daemon ticks;
7. inspect status, runs, traces, waits, interventions, and doctor output; and
8. retain sanitized evidence without retaining credentials or derived
   session-local material.

`simple_loop` selects Millforge runner bindings. The governed semantic LAD
workflow selects Codex runner bindings. Do not override a selected adapter
kind merely to make local credentials fit a workflow. Configure the selected
adapter through [Codex runner setup](../codex-runner.md) or
[Millforge runner setup](../millforge-runner.md), as applicable.

For cautious execution, use the supported bounded form:

```bash
millrace --workspace /absolute/path/to/workspace run daemon \
  --max-ticks 1 \
  --adapter-kind <selected-kind> \
  --adapter-config-json /absolute/path/to/local-adapter.json
```

A recovery qualification must enter recovery through a declared graph-visible
outcome and then execute the graph-selected recovery stage. Do not hand-edit
runtime state, synthesize a recovery projection, or relabel an offline fake as
provider-backed evidence. Use [Errors and refusals](../errors.md) to classify a
row that does not reach its declared barrier.

## Authentication And Evidence Boundary

Adapter configuration and credentials are local operator inputs, not workflow
package authority. Do not write API keys, OAuth tokens, credential paths, or
secret-bearing config snapshots into retained evidence. Temporary auth or
adapter material must be absent at the durable boundary.

A clean live-success claim requires durable runtime evidence: no active runs,
open waits, closure blocks, quarantines, or interventions, plus the expected
results and artifacts. A deliberately selected recovery transition may stop
at its declared recovery barrier, but must be reported as recovery evidence,
not completed workflow closure.
