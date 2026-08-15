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
discovery. A binding selects one `template` router asset and a normalized
workspace-relative checkout root. Its sources are limited to
`dispatch_material:current`, `accepted_lineage_artifacts:current_lineage`,
`lineage_attempt_history:current_lineage`, and explicitly selected
`workspace_relative_root` paths, each with file and byte bounds. Required
sources fail closed; discoverable sources may be recorded as whole-source
omissions with a deterministic reason.

The compiler validates the binding against the selected stage, runner, router,
source roots, and optional writeback action/schema. `direct_write` and
`protected_proposal` are the only write dispositions. A write-enabled binding
must select both sides of its writeback linkage; a read-only binding selects
neither. The selected plan fingerprint therefore covers context policy just
as it covers graph, asset, and runner authority.

At runtime, a bound session captures a schema-1 manifest and its selected file
bytes into the existing CAS before external start. The runner receives a
compact, authenticated descriptor for the materialized checkout; it does not
receive an ambient file search or an inline copy of the checkout. A bound
Codex session uses wrapper protocol 4 and the initialized Millrace workspace
as its `cwd`. An unbound workflow keeps the existing dispatch behavior and
does not create a checkout.

The base package remains the diagnostic `kernel_ping` surface and carries no
hosted workflow checkout policy. A custom or Plus package may select its own
generic bindings and relative roots, including workflow-specific assets, but
the runtime never branches on those names. Before a bound result is accepted,
Millrace verifies the materialized checkout and, for write-enabled bindings,
validates the linked direct/protected/no-op report against the selected live
roots. Refusal prevents the result from becoming a workflow artifact or route;
protected proposals are not promoted automatically.

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
