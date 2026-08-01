# Getting Started

This guide creates a fresh v0.22 workspace, selects the official
`simple_loop` workflow, queues one prompt, and runs it through Millforge.

## 1. Install Millrace

The base `millrace-ai` runtime requires Python 3.11 or newer. The complete
`millrace` bundle requires Python 3.12 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install "millrace==0.22.1"
millrace --version
```

Choose an empty workspace:

```bash
export WORKSPACE=/absolute/path/to/new-workspace

millrace --workspace "$WORKSPACE" workspace init \
  --input-id workspace-init-001
millrace --workspace "$WORKSPACE" workspace check
```

Millrace creates SQLite state and content-addressed storage inside the
workspace. Do not reuse a v0.21 workspace.

## 2. Import The Official Workflows

```bash
millrace --workspace "$WORKSPACE" package import-installed \
  millrace-plus --resource-root millrace_workflow_package \
  --command-id import-plus-001

millrace --workspace "$WORKSPACE" package enable \
  millrace.plus.official 0.22.0 --command-id enable-plus-001

millrace --workspace "$WORKSPACE" package list \
  --command-id list-plus-001
```

Import validates the manifest and every declared asset before package state is
committed. It reads package resources without importing package Python code.

## 3. Select `simple_loop`

Verify and admit the workflow:

```bash
millrace --workspace "$WORKSPACE" package verify \
  millrace.plus.official 0.22.0 \
  --workflow-id simple_loop --workflow-version 0.1 --entrypoint default \
  --command-id verify-simple-loop-001

millrace --json --workspace "$WORKSPACE" plan admit-package \
  millrace.plus.official 0.22.0 \
  --workflow-id simple_loop --workflow-version 0.1 --entrypoint default \
  --command-id admit-simple-loop-001 --input-id admit-simple-loop-001
```

Copy the returned `authority_fingerprint`, then select it:

```bash
export PLAN_FINGERPRINT='sha256:copy-the-returned-value-here'

millrace --workspace "$WORKSPACE" plan select-default \
  "$PLAN_FINGERPRINT" --input-id select-simple-loop-001
```

The admitted plan contains its selected runner bindings and package pins. A
later default change does not remap an active plan.

Planning output does not authorize implementation. If a planning workflow
produces task cards, select a separate execution-capable compiled plan and
explicitly enqueue each approved task into its declared external execution
family. Artifact creation alone never performs that enqueue.

## 4. Queue Work

```bash
millrace --workspace "$WORKSPACE" queue enqueue work_prompt \
  --payload-json '{"prompt_id":"example-001","body":"Add a focused test for the requested behavior, implement the change, and verify it."}' \
  --plan-fingerprint "$PLAN_FINGERPRINT" \
  --input-id enqueue-example-001

millrace --workspace "$WORKSPACE" status
```

The prompt is now durable and remains queued across process restarts.

## 5. Configure Millforge

Create an adapter JSON file using the closed configuration described in
[Millforge runner setup](millforge-runner.md). Select the provider endpoint,
model, capabilities, request options, and reasoning policy explicitly. Put the
credential only in the environment variable named by `secret_ref`.

```bash
export MILLRACE_MODEL_API_KEY='<provider credential>'

millrace --workspace "$WORKSPACE" run daemon \
  --max-ticks 8 \
  --adapter-kind millforge \
  --adapter-config-json /absolute/path/to/millforge-adapter.json \
  --monitor basic
```

`--max-ticks` bounds daemon work units; it is not a separate run-once mode.
Omit it when the daemon should continue until stopped. Codex remains an
explicit alternative when selected by the plan; see
[Codex runner setup](codex-runner.md).

## 6. Inspect Or Intervene

```bash
millrace --workspace "$WORKSPACE" status
millrace --workspace "$WORKSPACE" runs list
millrace --workspace "$WORKSPACE" runs show RUN_ID
millrace --workspace "$WORKSPACE" trace show
millrace --workspace "$WORKSPACE" trace show RUN_ID
millrace --workspace "$WORKSPACE" waits list
millrace --workspace "$WORKSPACE" interventions list
millrace --workspace "$WORKSPACE" doctor
```

For an active session, request replay-safe operator cancellation with
`millrace --workspace "$WORKSPACE" runs cancel RUN_ID --input-id ID`. This
records a durable request; it does not signal a process directly. Read a
finite bounded event page with
`millrace --workspace "$WORKSPACE" runs follow RUN_ID --after-sequence N`.
Status, run, trace, follow, and doctor projections are read-only.

To stop only new claim acceptance, use the selected plan fingerprint and a
durable input identity:

```bash
millrace --workspace "$WORKSPACE" dispatch suspend \
  --plan-fingerprint "$PLAN_FINGERPRINT" \
  --input-id suspend-dispatch-001 \
  --reason "operator maintenance"
```

The result returns a `suspension_id`. Resume only that exact suspension:

```bash
millrace --workspace "$WORKSPACE" dispatch resume \
  --plan-fingerprint "$PLAN_FINGERPRINT" \
  --suspension-id SUSPENSION_ID \
  --input-id resume-dispatch-001 \
  --reason "maintenance complete"
```

This control does not cancel active work or invalidate a claim accepted before
the suspension. It is separate from workflow-authored pause state.

To close eligible queued workflow work without deleting it or signaling a
runner:

```bash
millrace --workspace "$WORKSPACE" queue cancel WORK_ITEM_ID \
  --plan-fingerprint "$PLAN_FINGERPRINT" \
  --input-id cancel-work-001 \
  --reason "superseded before dispatch"

millrace --workspace "$WORKSPACE" queue cancel-lineage LINEAGE_ID \
  --plan-fingerprint "$PLAN_FINGERPRINT" \
  --input-id cancel-lineage-001 \
  --reason "the complete lineage is obsolete"
```

Lineage closure preflights every selected-plan member and commits the complete
set or none of it. Accepted claims that may still start, live sessions, lost
sessions, and unresolved cleanup or orphan risk block queue closure. Use
`runs cancel`, not `queue cancel`, for runner-session cancellation. Status,
trace, and doctor expose bounded queue-closure audit projections.

Use `millrace <group> --help` for exact arguments. The
[system overview](how-millrace-works.md) explains the authority boundaries
between packages, selected plans, runners, runtime transitions, and operator
commands. See [Runner-session architecture](runner-session-architecture.md)
and [Daemon lifecycle](daemon-lifecycle.md) for cancellation, restart, and
signal behavior.
