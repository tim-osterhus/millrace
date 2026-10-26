# Plane runtime contract (unreleased runtime)

The unpublished 0.23.2 candidate accepts graph v3/v4, creates store v8, and
retains execution v2 and request v2. Existing finite graph-v3/store-v7 state
continues with its unchanged schema and bounds; v7 refuses v4 revisions. The external
task record owns source/verification/acceptance status; this guide does not grant
release or native qualification. Unsupported graphs/stores refuse. Retain historical
artifacts with their matching runtimes; no store conversion is provided.

A graph declares `planes: {id: {start, workspace, defaults, nodes}}`, shared
bindings/defaults/control files and run limits. Each plane has one local cursor;
all goto, retry and pause-resume targets refer to nodes in that plane. Common
settings resolve runtime, workflow, plane, then node under operator policy.
Validation returns `resolved_nodes[plane_id][node_id]`; adapter configuration keys
use `(plane_id, node_id)` so repeated local names do not collide.

Terminal dependencies name a producer plane and required terminal status. The
union terminal-dependency graph must be acyclic. Recurring seams independently
name an exact producer `(plane, node, outcome)` and consumer `(plane, node)`;
recurring feedback cycles are allowed. A run freezes plane/workspace identities,
complete seam declarations, terminal dependencies and maximum parallelism.
Live revisions may expand local nodes/routes/prompts while preserving retained
cursor/resume/producing-node and producing-semantic-outcome links.

Live capture validates workspace directories and parallel disjointness, recording
absolute paths and filesystem identities. Captured/history validation checks
those stored shapes and links without probing today's project directories or
native availability. Live create/start/dispatch admission remains separate.
`validate_revision(run_id=...)` retains one readonly Store transaction while
collecting compatibility facts and reports its observed `run_state_version`.
The same pure compatibility predicate guards activation before mutation.

Execution requests identify plane and producing revision. `project_root` means
the selected plane workspace. An optional immutable `SeamInput` carries exact
path, bytes, hash and selection identities for process and nonprocess adapters.
Core reserves `seam-input.json` and `MILLRACE_SEAM_INPUT`; adapters cannot override
those names. References remain relative to the captured producer workspace and
do not promise frozen artifact bytes.

Consumer graph retry and self-resume pause retain the selected occurrence.
Goto (including goto-self), finish and pauses that do not self-resume acknowledge
on committed transition; a run gate never acknowledges. Selections commit before
reservation; raw completions commit before routing.
Routing, matching semantic publications and acknowledgements share one transaction.
Matching producer retries publish separate occurrences keyed by invocation;
protocol/execution/unknown results do not publish. FIFO selection and the selected
envelope survive consumer retries/reload. A terminal producer leaves committed
occurrences available; once drained, missing next input pauses seam_exhausted.
Pinned manual retry accepts only the current consumer invocation lineage; retry
of an acknowledged occurrence refuses. Ordinary eligible prior-invocation retries
remain available within their plane, without that seam-only restriction.

`plane_store.py` contains new relation DDL and connection-level helpers only.
The existing Store retains its connection, outer transaction and version owner.
Creation events, reservation/routing/control causes, gate history and seam
relations reconstruct the current projections. Historical validation checks
captured paths and links without probing current workspaces or artifact bytes.
All owned workers drain and join before holder return; unresolved external
writers retain the aggregate hold. See the controls guide for receipts and
explicit run/plane continuation semantics.


## Overall limits in the candidate

Graph-v4 requires a `limits` object, permitting independent omission of
`max_invocations` and `max_elapsed_seconds`. Explicit null, zero, malformed or
nonfinite values are invalid. `max_parallel_invocations` retains its positive
integer validation, default1 and fixed-per-run authority. Graph-v3 still requires
both finite overall bounds.

Startup captures never change. Each activation uses the minimum of present
startup and active bounds for each dimension, anchored to original `started_at`.
An absent startup bound can be tightened and later removed; a finite startup
bound cannot be removed by an active revision. Public inspection keeps six
required capture keys and nullable invocation `run_deadline`; invocation
`deadline` always remains finite. Null means no overall bound, never zero,
infinity or a fabricated remaining allowance.

New v8 keeps a private write-once SQL start anchor equal to public `started_at`;
normal operations never rewrite it. Missing or inconsistent facts refuse rather
than being repaired. This detects isolated inconsistencies, not coordinated
arbitrary database edits. V7 receives no column, backfill or relabel. Replay uses
each reservation's then-active revision; later tightening does not rewrite old
invocation deadlines or invalidate reservations that were legal when committed.
