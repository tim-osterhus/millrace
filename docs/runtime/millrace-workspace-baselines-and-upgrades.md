# Millrace Workspace Baselines And Upgrades

## Scope

This document describes the managed workspace baseline lifecycle:

- explicit workspace initialization
- baseline manifest identity
- upgrade preview/apply behavior
- stable deployed baselines for long-horizon work

Use `docs/runtime/millrace-cli-reference.md` for exact command syntax.

## Explicit Initialization

Workspace creation is no longer an accidental side effect of unrelated
commands.

Initialize a workspace explicitly:

```bash
millrace init --workspace /absolute/path/to/workspace
```

That creates the canonical runtime subtree under:

- `<workspace>/millrace-agents/`

It also seeds:

- managed runtime asset families such as `entrypoints/`, `skills/`, `modes/`,
  `graphs/`, `registry/`, and compatibility `loops/`
- baseline config in `millrace-agents/millrace.toml`
- queue/runtime state surfaces
- `millrace-agents/state/baseline_manifest.json`

Most non-`init` operator commands now require an initialized workspace and fail
clearly if the baseline is missing.

## What The Baseline Manifest Stores

The baseline manifest records the managed deployed asset surface as initialized
from the package baseline.

Each manifest entry stores:

- `relative_path`
- `asset_family`
- `original_sha256`

The manifest also stores:

- `manifest_id`
- `schema_version`
- `seed_package_version`

The manifest is written to:

- `millrace-agents/state/baseline_manifest.json`

This gives the upgrade path a stable source of truth for what was originally
deployed into the workspace.

## Stable Workspace Baselines

Millrace keeps the deployed baseline local to the workspace on purpose.

That means:

- upgrading the installed Python package does not silently rewrite an existing
  workspace baseline
- long-running workspaces stay stable until an operator chooses to refresh the
  managed baseline
- operators may still edit deployed workspace assets directly

This is the core reason `init` and `upgrade` are separate explicit lifecycle
steps rather than hidden bootstrap behavior.

## Upgrade Preview

Use preview first:

```bash
millrace upgrade --workspace /absolute/path/to/workspace
```

Preview prints:

- `baseline_manifest_id`
- `candidate_manifest_id`
- counts by disposition
- one per-file disposition line for managed assets

Current dispositions:

- `unchanged`
- `safe_package_update`
- `local_only_modification`
- `already_converged`
- `conflict`
- `missing`

## How Classification Works

Upgrade classification uses a three-way comparison:

- original deployed hash from `baseline_manifest.json`
- current workspace file hash
- candidate package file hash

That distinction matters because it lets Millrace separate:

- package drift with no local edits
- local edits with no package drift
- already-converged files
- real conflicts

## Upgrade Apply

Apply only after preview looks acceptable:

```bash
millrace upgrade --workspace /absolute/path/to/workspace --apply
```

Current apply behavior:

- applies `safe_package_update`
- restores `missing` managed files from the candidate baseline
- preserves `local_only_modification`
- preserves `already_converged`
- refuses to apply when any `conflict` remains

On success, Millrace writes a refreshed baseline manifest for the new deployed
baseline.

## Relationship To Compile Currentness

Baseline identity and compile currentness are related but not identical.

Baseline upgrade answers:

- what managed files were originally deployed here?
- what package-managed changes are safe to refresh?

Compile currentness answers:

- does the persisted compiled plan still match the current config and asset
  inputs?

After `upgrade --apply`, the workspace baseline may be newer than the persisted
compiled plan. In that case, `millrace status` will report the compiled plan as
`stale` until the workspace is recompiled.

## Recommended Operator Flow

For a fresh workspace:

```bash
millrace init --workspace /absolute/path/to/workspace
millrace compile validate --workspace /absolute/path/to/workspace
millrace run once --workspace /absolute/path/to/workspace
```

For an existing workspace that needs a packaged baseline refresh:

```bash
millrace upgrade --workspace /absolute/path/to/workspace
millrace upgrade --workspace /absolute/path/to/workspace --apply
millrace compile validate --workspace /absolute/path/to/workspace
```

That sequence preserves the Millrace stability model:

- deployed baseline changes are explicit
- compile authority is re-established after baseline drift
- stale compiled plans are visible before execution resumes
