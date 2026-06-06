# ADR-0014: Runtime Operation Step Interpreter

Status: Accepted

Date: 2026-06-04

## Context

ADR-0011 introduced compiler-validated runtime effect operation catalogs. Each
effect operation declares steps, required artifacts, legacy handler
compatibility, idempotency policy, failure mappings, mutation journal schema,
and partial-commit policy. Runtime-effect dispatch is operation-id-first:
compiled `effect_operation_id` selects a runner, and the runner selects the
Python implementation through an operation-indexed registry.

That model works well for runtime effects, but Millrace has other post-stage
operations that follow the same pattern:

- Terminal actions that apply lifecycle mutation plans to source work items
  (see ADR-0010, ADR-0012)
- Counter mutations that increment compiled recovery-counter metadata
- Work-item transitions that move documents between queue directories
- Recon handoff validation and packet persistence
- Runtime effect repair-exhaustion routing to terminal states
- Learning promotion and Librarian skill-install operations

Each of these is conceptually a runtime operation step: it has a compiled
operation id, required input artifacts, a Python implementation, failure
modes, and idempotency constraints. But currently each is implemented as a
bespoke code path in the kernel rather than as a registered operation step.

This ADR extends the ADR-0011 model to cover all runtime operation steps, not
just runtime effects. The goal is a unified step-interpreter pattern where any
compiled operation step can be dispatched, validated, traced, and recovered
through the same machinery.

## Decision

The runtime operation step interpreter is an extension of the ADR-0011
runtime-effect model. Each runtime operation step is defined by:

- **Operation ID**: A unique compiled identifier that selects the step
  implementation (analogous to `effect_operation_id`).
- **Step contract**: A typed schema that declares required input artifacts,
  optional input artifacts, expected output artifacts, failure classes, and
  idempotency constraints.
- **Runner ownership**: A registered Python implementation that executes the
  step, validated at compile time against the step contract.
- **Failure policy**: A compiled failure-policy entry that maps step failure
  classes to router decisions, recovery routes, and incident creation rules.
- **Trace contract**: A structured trace that records step identity, input
  artifact refs, output artifact refs, duration, and outcome for run-trace
  graphs and monitor events.

The following safety properties from ADR-0011 are preserved and extended:

1. **operation-id-first dispatch**: Every runtime operation step is selected by
   compiled operation id, never by hard-coded stage/plane name or legacy enum
   value.
2. **compile-time validation**: The compiler validates that every referenced
   operation step has a registered runner, that required input artifacts exist,
   and that failure-policy entries are consistent with the step contract.
3. **stale-plan detection**: If a persisted compiled plan references operation
   steps whose catalogs have changed (added, removed, or incompatible
   contracts), the plan is treated as stale and must be recompiled.
4. **idempotency enforcement**: Each step declares its idempotency policy
   (idempotent, safe-to-retry, or unsafe-to-retry). The runtime enforces that
   policy before re-dispatching a step after a crash or recovery.
5. **failure-scoped routing**: Step failures route through compiled failure
   policies with explicit failure classes. No step failure falls through to an
   opaque default handler.
6. **mutation journal schema**: Steps that mutate durable state declare a
   mutation journal schema so partial commits can be detected and rolled back
   or completed during recovery.
7. **partial-commit policy**: Steps that produce multiple output artifacts
   declare whether partial success (some artifacts written, some not) is
   acceptable or requires full rollback.
8. **trace visibility**: Every dispatched step produces a structured trace
   entry with operation id, runner id, input/output artifact refs, failure
   class (if any), and duration. These entries feed run-trace graphs and
   monitor event output.
9. **no kernel semantic ownership**: The step interpreter dispatches by
   compiled operation id and does not understand the meaning of any step. Step
   semantics are owned by the extension packages, graph assets, or workflow
   primitive contracts that register them.

In addition to the numbered properties above, the step interpreter satisfies
the following rubric-aligned safety guarantees:

- **no unknown primitives**: Every compiled operation step resolves to a
  registered runner; an unrecognized operation id is a validation failure.
- **no unsafe paths**: The interpreter never falls through to a default
  handler; every step outcome routes through an explicit compiled
  failure-policy entry.
- **no unvalidated stores**: Steps that write durable state declare a
  mutation journal schema; writes without a declared schema are rejected at
  compile time.
- **no undeclared mutation phases**: Steps that produce multiple output
  artifacts declare a partial-commit policy; silent partial writes are
  detected during recovery.
- **no missing partial-commit policy**: Every multi-artifact step declares
  whether partial success is acceptable; steps without a declared policy are
  invalid.
- **no arbitrary Python imports from graph or config data**: The interpreter
  dispatches by compiled operation id; graph and config data cannot inject
  arbitrary Python code or select unregistered implementations.
- **no unbounded side effects**: Each step contract declares its required and
  optional input artifacts, expected output artifacts, and failure classes;
  steps cannot produce side effects outside their declared artifact surface.
- **no hidden Python handler override of compiled identity**: The interpreter
  dispatches by compiled operation id; Python handler resolution goes through
  the operation-indexed registry, not through naming conventions, module
  scanning, or stage/plane-name pattern matching.

Steps that are candidates for this unified model (but not required to migrate
in this ADR) include:

- Terminal action application (currently resolved through
  `runtime/graph_authority/terminal_actions.py` and
  `runtime/lifecycle_interpreter.py`)
- Counter mutation (currently applied through
  `runtime/graph_authority/counters.py` and `runtime/result_counters.py`)
- Work-item transitions (currently in `runtime/work_item_transitions.py`)
- Recon handoff operations (currently in `runtime/recon_transitions.py`)
- Runtime-effect repair-exhaustion terminal routing (currently in
  `runtime/effect_execution.py`)
- Learning promotion operations (currently in `runtime/supervisor.py`)

## Consequences

The step interpreter pattern makes every runtime operation step uniformly
discoverable, testable, traceable, and recoverable. Adding a new step type
requires only a new registered runner and step contract, not a new kernel code
path.

The immediate cost is migration effort: existing bespoke operation paths
must be refactored into registered steps. This ADR does not require that
migration in one pass. It establishes the pattern so future work can
incrementally register steps, starting with the simplest (counter mutations,
work-item transitions) and progressing to the most complex (terminal action
application, Recon handoff).

A prospective `runtime/operations/interpreter.py` or
`runtime/operations/` package is not yet created. This ADR defines the
conceptual extension of the ADR-0011 model; the actual package structure will
be created when the first migration batch lands.
