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
unsupported actions, and incomplete routes before runtime admission.

## Context Bindings

Context bindings are selected workflow authority, not ambient workspace
discovery. In v0.22.3, binding schema 2 selects one `template` router asset, a
normalized workspace-relative checkout root, bounded required and discoverable
sources, hydration limits, a mutation policy, and
`materialization_retention=until_session_durable_terminal`. The complete
binding and its fields are compiled into the selected plan; the runtime does
not inject hidden context or policy defaults.

The closed source pairs are `dispatch_material/current`,
`workspace_relative_root/<safe-relative-root>`,
`selected_artifacts/direct_predecessors`,
`selected_artifacts/current_lineage`,
`selected_attempts/since_last_accepted_transition`, and
`selected_attempts/current_lineage`. Required sources fail closed when missing
or over bounds. Discoverable sources are captured into CAS and authenticated by
the immutable schema-2 manifest, but are represented only by catalog entries
until selected.

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
