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

The compiler rejects missing references, ambiguous markers, invalid schemas,
unsupported actions, and incomplete routes before runtime admission.

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
pinned to the exact plan and package assets it started with.

## Official And Custom Packages

The base runtime ships only the diagnostic `kernel_ping` workflow. The
`millrace-plus` distribution provides the official workflow collection and
authoring skills.

Custom packages can use any domain vocabulary or graph shape supported by the
compiler. Plane names such as Management, Planning, Execution, Learning, or
Review remain workflow data. The runtime does not assign meaning to them.

For practical authoring guidance, read the `millrace-loop-configuration` and
`millrace-entrypoint-authoring` skills distributed with `millrace-plus`.
