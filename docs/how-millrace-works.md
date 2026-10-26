# How Millrace works

A workflow folder contains an authored graph and its scripts, prompts and profiles.
Core validates graph structure and captures the declared control files.
The command adapter runs deterministic steps. External execution hosts explicitly supply other adapters.

A durable run owns one cursor per plane and at most one invocation per plane.
Core records the launch contract before execution, commits the raw result, then parses and routes it.
Graph outcomes select `goto`, `pause` or `finish` actions. Plane actions affect that plane.
Run controls can act across the run. Seams carry declared terminal dependencies or recurring handoffs.

Core owns routing, state and receipts. An adapter reports execution evidence and writer observations.
A useful result does not prove the writer stopped. Unknown writers retain holds until supported reconciliation establishes closure.
Core never imports Plus automatically and never treats prompt prose as routing authority.

Locked editing is the default. A changed control closure pauses before further dispatch.
Live editing validates a pending revision at an invocation boundary after persisting the completed result.
The producing revision parses the result; the active routing revision selects its transition.

See [controls](maintainers/router-controls.md), [planes](router-planes.md), and [cutover](maintainers/router-cutover.md).


The unpublished 0.23.2 candidate accepts graph-v4 with independently optional
overall invocation and elapsed bounds. Omit a field to leave that dimension
unbounded; the limits object remains required and explicit null is invalid.
Each invocation still has a positive finite timeout, resolved under operator
policy, with a 1,800-second warning fallback only when none was supplied.

Effective bounds take the smaller present startup and active-revision values.
Elapsed deadlines stay anchored to the original run start, including pause and
controller downtime. Removing an active bound cannot weaken a finite startup
bound. Count remains evidence even without a cap. Results commit before routing;
a denied reservation adds no invocation or extra retry increment beyond any
already committed retry intent. Writer holds, seams and explicit controls retain
their authority. Fresh v8 stores anchor the original start independently; retained
finite v3/v7 state operates unchanged without conversion.
