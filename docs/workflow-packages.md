# Workflow Packages

Workflow packages are how Millrace receives workflow behavior without adding
workflow-specific code to the runtime.

Each package has a manifest, one or more selectable workflows, and any text
assets those workflows require. Millrace reads the package as data, validates
its declared bytes, and compiles one selected workflow entrypoint.

## What A Workflow Declares

A complete workflow definition describes its decision tree or graph:

- graph nodes and stage contracts;
- queue families and input routes;
- runner bindings;
- legal terminal markers and actions;
- artifact schemas;
- payload projections between stages;
- recovery, retry, wait, and quarantine behavior;
- fanout, join, lineage, and completion rules;
- selected prompt and skill assets.
- optional stage context bindings, including a router asset, bounded source
  declarations, checkout root, and writeback policy.

The compiler rejects missing references, ambiguous markers, invalid schemas,
unsupported actions, and incomplete routes before runtime admission. For
runtime-owned counter thresholds, compiler and admission both check threshold/
increment artifact schema and normalized condition compatibility; threshold
conditions may only be an exact key/value subset of increment conditions.

## Same-batch fanout prerequisites

A fanout may opt into sibling dependencies by declaring both
`item_dependency_key` and `dependency_success_action_ids`. For example:

```json
"item_dependency_key": "depends_on_task_card_ids",
"dependency_success_action_ids": ["execution.close_updater_complete"]
```

The source artifact's item schema must require an exact nonempty string ID and
an array of exact nonempty prerequisite IDs. Every item includes the array;
`[]` means independent. IDs must refer to other unique items in that same
source batch. Duplicate IDs or prerequisites, unknown/self references, malformed
arrays and cycles refuse the entire source result before it closes or creates
partial child work. Prose dependencies do not create runtime edges.

The selected success IDs must declare artifact-bearing `close` or
`complete_work_item` actions, distinct from the fanout's own source action.
A prerequisite releases only after an authenticated selected success artifact
and matching terminal closure from the exact original prerequisite item or its
routed descendants. Shared lineage, another sibling's success, a blocked/gap
closure or an intermediate stage closing is insufficient. Routed descendants
retain the original dependency identity through authenticated route and
recorded-source recovery history. Claims, dispatch readiness and durable load
use the same selected dependency contract.

This opt-in compiles to fanout schema 3. Existing fanouts without those two
fields retain schema 2, canonical bytes and parent-source dependency behavior.
The SQLite schema is unchanged. Older readers refuse the new nested schema
rather than silently dropping prerequisites. Assignment-level shared lineage
and its selected counters, recovery and quarantine scope remain unchanged.

## Closure request capacity and overflow

A closure request is the work item that a closure evaluator or recovery stage
receives. Its canonical body is the UTF-8 JSON from
`canonical_authority_mapping_bytes`. The body is delivered inline only when it
fits the completion behavior's `request_payload_byte_limit`. Compiler, build,
export, codec, and kernel admission validate the limit and any declaration
against it. Compiler, export, kernel admission, and the CLI runner-binding path
also check the limit against the selected runner's capacity. The packaged Pi
capacity pin is 1048576 bytes; a component pin declares its own
`max_work_item_payload_bytes`.

An opt-in `request_overflow` on a completion behavior selects the policy
`materialized_evidence_pages` and these fields:

- `materialization_root`: a safe NFC workspace-relative POSIX path with no
  hidden, `.millrace`, or `..` components.
- `page_byte_limit`: from 4096 through the smaller of the request limit and
  1 MiB.
- `max_snapshot_bytes`: above the request limit and at most 64 MiB.
- `max_page_count`: from 1 through 16384.

A context binding's checkout root or source root may not overlap
`materialization_root`. Compile, export, and admission refuse with
`context_binding_overflow_root_overlap:<binding id>`.

The declaration compiles to completion-behavior schema 3. Behaviors without
`request_overflow` keep schema 2, canonical bytes, and fingerprints. Readers in
this source refuse an unknown completion-behavior schema or an extra field
instead of dropping `request_overflow`. Store schema 11, selected-plan schema 18,
dispatch schema 7, and runner-session schema 2 are unchanged.

When the body is above the limit, the durable closure request still holds the
complete sealed `closure_evidence_snapshot`. Anchors, provenance, replay, and
verdict checks are unchanged. Dispatch replaces only that member with
`closure_evidence_overflow`. The projection carries the snapshot digest and byte
count, the closure target, selected-plan fingerprint, lineage, anchor and
root-contract digests, prior verdict digest, artifact count, page limit, page
count and bytes, the materialization directory, and the index page paths and
digests. Dispatch projects it for runs on a work item that has a
closure-evaluation record, including the evaluator and recovery stages. The
directory is `<materialization_root>/<component>`, where the component is the
first 32 hex digits of SHA-256 over the session ID, a newline, and the dispatch
generation. Sessions therefore get different directories but the same snapshot
digest and anchors.

For a newly created session, the CLI materializes the pages before start
admission. The index pages are `index-00001.txt` onward. Segments for the root
contract, the prior verdict, if any, and each evidence artifact follow, for
example `root-contract-00001.txt`, `prior-verdict-00001.txt`, and
`evidence-0001-00001.txt`. A segment larger than one page continues on the next
numbered page. Each page is at most `page_byte_limit` bytes. Pages break at line
boundaries, and a line longer than a page splits at UTF-8 character boundaries.
Segment bytes are UTF-8 JSON with indent 2 and sorted keys. Pages are written to
a temporary `.closure-evidence-*` directory. Files are created with mode 0600 and
then set to 0444. The directory is renamed into place while writable, then set
to 0555. An existing session directory whose pages match the expected set is
reused. Otherwise it is removed and rebuilt from durable state. Symlinks in the
path and overlap with the database or CAS refuse materialization. A
materialization error refuses with `session_preparation_refused` before start
admission. The refusal is also recorded as a best-effort
`closure_evidence_materialization_refused` diagnostic event that carries the
reason, truncated to 256 characters.

Before a runner result that carries result evidence is persisted, the runtime
re-verifies the session's pages, starting from the index page digests in the
authenticated projection. A changed, removed, or added page, a corrupted index,
or a wrong-session directory refuses the result with `context_mutation_refused`.
The session then records a non-completed completion (`failed`, or `lost` when
cleanup is an orphan risk) with adapter error kind `context_mutation_refused`,
and no verdict is accepted. An explicit retry starts a new dispatch generation,
so it gets a new session directory and fresh pages. Adapter errors such as
timeouts carry no result and keep their selected runtime-failure classification.
Only results that pass this check are persisted, so applying a persisted
completion later does not re-check.

After durable completion, the session removes its own directory and records the
disposition (`not_required`, `absent`, `removed`, or `refused`) as a best-effort
diagnostic event. Pages are read-only evidence views and never authority.

Kernel payload refusals are durable refused transitions, not crashes. Refusal
reasons:

- `closure_request_payload_limit_exceeded`: the body is above the limit and the
  behavior declares no overflow.
- `closure_request_overflow_snapshot_invalid`: the sealed snapshot is not valid
  for projection.
- `closure_request_overflow_snapshot_limit_exceeded`: the snapshot is above
  `max_snapshot_bytes`.
- `closure_request_overflow_page_limit_exceeded`: the pages exceed
  `max_page_count`.
- `closure_request_overflow_projection_limit_exceeded`: the inline projection is
  still above the request limit.
- `closure_request_authority_mismatch`: dispatch found more than one closure
  evaluation record for the work item, a record whose plan does not match the
  run, or a completion behavior missing from the selected plan. A work item with
  no evaluation record receives no projection.
- `session_preparation_refused`: session preparation fails before start
  admission, when the materialization callback raises or the durable prepared
  session does not match.

Overflow does not change the evidence item limit. A closure evaluation with more
evidence artifacts than `evidence_item_limit` refuses with
`closure_evidence_item_limit_exceeded`, which is a capacity block described
below. The kernel accepts a limit from 1 through 256. The Plus `lad.full.pi`
completion behavior keeps 96 evidence items, and `planning.lad` keeps 64.

Page reads are bounded. The runtime reads each index and segment page through a
reader that does not follow links and does not wait on special files. The opened
file must be a regular file whose size equals the length in the authenticated
projection, and at most that many bytes plus one are read. A FIFO, directory,
symlink, oversized file, or file with other bytes is never read as evidence.
During pre-start reuse of an existing session directory, such a page fails the
check, and the directory is removed and rebuilt from durable state. A symlink at the
session directory path is refused, and nothing is written through it. The pre-result
drift check refuses the same shapes with `context_mutation_refused`, as described
above.

A paged closure request also needs a verifiable runner working directory. When a
run's closure request is delivered through pages, the CLI compares the configured
adapter working directory with the workspace after the run is claimed and before
any session is created or the runner is spawned. This check does not depend on
context bindings, so it applies to the official Pi workflow, which has none. A
mismatch refuses as `adapter_failure` with one of these reasons, where `<kind>` is
the selected adapter kind:

- `closure_evidence_<kind>_cwd_mismatch`: the configured working directory is not
  the workspace.
- `closure_evidence_<kind>_cwd_unresolvable`: the configured working directory
  cannot be resolved.
- `closure_evidence_<kind>_cwd_unverifiable`: the adapter kind has no verifiable
  working directory. Adapter kinds other than `codex` and `pi` refuse paged runs
  this way.

Five kernel refusals are capacity blocks: `closure_evidence_item_limit_exceeded`,
`closure_request_payload_limit_exceeded`,
`closure_request_overflow_snapshot_limit_exceeded`,
`closure_request_overflow_page_limit_exceeded`, and
`closure_request_overflow_projection_limit_exceeded`. For these reasons the
refusal detail is a `closure_capacity_block` record. The record has
`record_kind` `closure_capacity_block`, `schema_version` 1, the closure target ID,
the reason, `operator_required` true, and a `measurements` object. The
measurements are computed for the full, uncapped request when the refusal is
created. Refusal detail is diagnostic data, so a later reader cannot authenticate
those stored values. They include `request_bytes`, `request_payload_byte_limit`,
`evidence_items`, and `evidence_item_limit`. When the behavior declares an
overflow, they also include `snapshot_bytes`, `max_snapshot_bytes`, `page_count`,
`max_page_count`, and `page_byte_limit`.

The refusal is durable. The receipt, refusal, governance event, and trace for the
deterministic evaluate input ID carry the record. Replaying the same input returns
the same refusal and leaves durable state unchanged. The lifecycle does not
propose that evaluation again, because its declared `blocked_work_policy` is
`suppress`. A lifecycle tick that reaches the block returns
`lifecycle_closure_blocked`, starts no runner, and does not stop the daemon. A
later tick finds no ready work for that evaluation and reports `no_ready_work`.
`status` lists recorded capacity refusals for open targets under
`closure_capacity_blocks`. The receipt's input payload digest identifies the
target, so missing or altered detail cannot hide a genuine refused input. Each
entry reports the target, input ID, refusal record, reason, `detail_status`,
`selected_capacity_limits`, and `freshness_status`. Selected limits come from the
target's admitted plan. Stored size measurements remain unauthenticated even
when they agree with those limits, so `measurements` is null. Coherent measured
detail is `unverified`; other classifications are `absent` (including honest
pre-detail history), `unmeasured`, and `invalid`. A receipt that authenticates no
target is listed with a null target rather than hidden. `freshness_status` is
`unknown`: a historical refusal does not establish that the current evidence's
evaluation input is suppressed. Scheduling tests the exact current input ID.
Reopening the store preserves the recorded refusal and its classification.

Automatic retry is not lawful. The block is a durable decision for one input ID,
and an active run cannot enlarge the selected capacity of its admitted plan, so
rerunning the same evaluation would only reproduce the refusal. This matches the
existing classification that input capacity is not recoverable. Operator action is
required. A capacity block is a mechanical runtime hold, not an evaluator `BLOCKED`
verdict. Core has no in-place clear or retry command for it: the target's admitted
plan is immutable, selecting another default plan does not change that target, and
`queue cancel-lineage` refuses an already completed lineage
(`queued_work_not_cancelable`). The supported way forward is fresh execution under a
plan whose selected capacity covers the evidence, for example in a new runtime,
with the blocked target and its evidence kept as history. New legal evidence
produces a new evaluation input ID, and that input is
measured again. It is blocked again while it remains above capacity. Sealed
evidence stays durable, and no evaluator, session, start, or verdict is fabricated
for a blocked input.

Accepted runner observations authenticate emitted artifacts independently of the
artifact table. An emitting action has exactly one canonical output, identified
by its accepted transition's `<record_id>:artifact`. Removing it or adding an
alias with copied payload and provenance is an integrity failure at durable
load, closure capture, and historical sealed-evidence verification. Distinct
accepted transitions retain their separate outputs. Actions that do not emit
artifacts remain lawful.

This section describes source behavior only. It does not qualify native-model,
Linux, Pi, or campaign runs.

## Context Bindings

Context bindings are selected workflow authority, not ambient workspace
discovery. In v0.22.3, binding schema 2 selects one `template` router asset, a
normalized workspace-relative checkout root, bounded required and discoverable
sources, hydration limits, a mutation policy, and
`materialization_retention=until_session_durable_terminal`. It is distinct from
the current schema-3 `millrace.context_checkout_manifest`. The complete binding
and its fields are compiled into the selected plan; the runtime does not inject
hidden context or policy defaults.

The closed source pairs are `dispatch_material/current`,
`workspace_relative_root/<safe-relative-root>`,
`selected_artifacts/direct_predecessors`,
`selected_artifacts/current_lineage`,
`selected_attempts/since_last_accepted_transition`, and
`selected_attempts/current_lineage`. Required sources fail closed when missing
or over bounds. Discoverable sources are captured into CAS and authenticated by
the current schema-3 manifest, but are represented only by catalog entries
until selected. Optional source `max_files`/`max_bytes` caps bind actual
enumeration and payload reads. Omitted declared roots retain authenticated
missing, empty-directory, file, and directory baselines; protected
create/delete/rename/type/over-limit mutations refuse during reconciliation.

A bound session materializes required files and the router before runner start.
The catalog has no payload bytes. An exact request can hydrate one or more
catalog paths under the read-only `selected/` subtree:

```text
millrace context select --session-id <session-id> --manifest-digest <digest> --path <catalog-path>
```

Hydration is cumulative and bounded by the compiled file and byte limits. Each
selection is authenticated against the session, selected plan, binding,
manifest, catalog entry, and CAS object, then recorded by an idempotent receipt.
Unknown, stale, foreign, symlinked, missing, or digest-drifted selections fail
closed.

The runtime enforces `forbid_selected_roots` or
`reconcile_selected_writes` from compiled authority before applying runner
results. Selected-root mutations are refused or reconciled against exact
writeback evidence; protected runtime roots remain unwritable. Source-backed
attribution is diagnostic evidence, with unavailable values kept distinct from
observed zeroes. After durable terminal completion, cleanup removes only the
session-owned derived checkout and selected materializations and records a
bounded receipt; CAS, manifests, receipts, results, usage, and events remain.

The base package remains the diagnostic `kernel_ping` surface and carries no
hosted workflow checkout policy. A custom or Plus package may select its own
generic bindings and relative roots, including workflow-specific assets, but the
runtime never branches on those names. An unbound workflow keeps the existing
dispatch behavior and does not create a checkout.

## Assets

Entrypoint prompts and stage-core skills have different jobs:

| Asset | Responsibility |
| --- | --- |
| Entrypoint prompt | Defines the stage's role, scope, work process, required evidence, terminal markers, and stop conditions |
| Stage-core skill | Defines exact artifact shapes, handoff formats, examples, validation checks, and completion criteria |

Assets guide the agent. They do not choose the next stage or mutate runtime
state. The selected workflow definition owns those decisions.

Every selected asset is pinned by path, byte length, and content digest. A
runner receives the selected material from Millrace rather than discovering
files in the workspace.

## Import And Selection

Millrace can import a package from a directory, an archive, or an installed
Python distribution. All three forms lower to the same manifest and asset-byte
model. Importing an installed distribution reads its package resources without
importing its Python modules or loading entry points.

After import, an operator can:

1. enable the package for future selection;
2. verify a workflow and entrypoint;
3. compile and admit the selected plan;
4. choose that plan as the workspace default;
5. enqueue work through one of its declared external routes.

Package updates and removals affect future selections. An active run remains
pinned to the exact plan, context policy, and package assets it started with.

## Official And Custom Packages

The base runtime ships only the diagnostic `kernel_ping` workflow. The
`millrace-plus` distribution provides the official workflow collection and
authoring skills.

Custom packages can use any domain vocabulary or graph shape supported by the
compiler. Plane names such as Management, Planning, Execution, Learning, or
Review remain workflow data. The runtime does not assign meaning to them.

For practical authoring guidance, read the `millrace-loop-configuration` and
`millrace-entrypoint-authoring` skills distributed with `millrace-plus`.


## Private Pi workflow candidate

The coordinated unpublished Plus 0.22.4 source candidate includes an explicitly
selected Pi variant using adapter_kind pi_rpc; Millforge remains the default,
and published v0.22.3 does not include this runner. A component-free binding
must declare the complete supported and granted unrestricted read, write, and
process requirements. The optional bounded runner_report is adapter-format
narrative evidence only. The selected plan continues to own terminal options,
routes, and exact artifact schemas.

Pi completion selects Core's packaged payload-capacity contract through nested
runner-binding schema 4. Generic Pi bindings without this pin retain schema 3.
The retained scripted-provider proof used predecessor Core 0.22.4.dev6 and Plus
0.22.4.dev5 artifacts; it does not qualify final 0.22.4 artifacts or real-model
behavior. See [Pi runner](pi-runner.md) for the profile and evidence limits.

Runner bindings can declare stage-owned `runtime_failure_mappings` for safely
settled terminal errors and rejected result contracts. These mappings select
artifact-free recovery routes and explicit finite recovery policies; neither
stage labels nor exceptions infer routing. See
[runtime failure recovery](runtime-failure-recovery.md) for the supported
vocabulary, diagnostic handoff and cancellation/budget boundaries.
