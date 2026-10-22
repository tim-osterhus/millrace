# Plane runtime contract (unreleased runtime)

The runtime uses graph v3, store v7, execution v2 and request v2. The external
task record owns source/verification/acceptance status; this guide does not grant
release or native qualification. Older graphs/stores refuse. Retain historical
artifacts with their matching runtimes and reauthor explicitly.

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
