# Kernel

The kernel is Millrace's deterministic state-change core.

It consumes an admitted compiled plan, immutable runtime state, and one
explicit transition input. `decide()` returns either a legal transition
decision or a typed refusal. `apply()` rechecks the decision's expectations and
returns the next immutable state.

## Main API

- `millrace.kernel.decide`
- `millrace.kernel.apply`
- `millrace.kernel.empty_runtime_state`
- `millrace.kernel.StateConcurrencyError`
- `millrace.kernel.UnsupportedMutationError`

## Responsibilities

The kernel owns:

- plan admission checks;
- work enqueue, claim, dispatch, and closure transitions;
- runner-observation authentication;
- terminal marker, artifact, and payload-projection validation;
- selected routes, recovery, waits, counters, fanout, and joins;
- deterministic events, traces, and refusal records;
- stale-decision and fencing checks.

Workflow-specific names do not create behavior in this package. Every legal
route and consequence comes from the selected plan.

## Boundary

The kernel returns state decisions but does not write SQLite, call runners,
parse CLI commands, read workflow source, or import hosted workflow packages.
Durable storage implements the persistence side of the boundary without being
allowed to choose workflow progression.
