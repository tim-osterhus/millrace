# Migrating From v0.21

Millrace v0.22 replaces the v0.21 runtime with a compiler-validated workflow
package, selected-plan, runner, and durable-state contract. It is a clean
break, not an in-place upgrade.

## Start With New State

1. Install the published v0.22.2 bundle or explicit reviewed v0.22.3 candidate
   members as described in [Getting Started](getting-started.md).
2. Create an empty workspace with `millrace workspace init`.
3. Recreate local runner credentials and adapter configuration.
4. Import current workflow packages.
5. Compile, admit, and select new v0.22 plans.
6. Re-enqueue work intentionally through current queue families.

Do not copy v0.21 SQLite state, snapshots, generated workspace files, package
layouts, or compatibility profiles into the new workspace. v0.22 does not
read, repair, migrate, or reinterpret them.

## Update Operator Scripts

Use the current command tree:

- `millrace workspace ...`
- `millrace package ...`
- `millrace plan ...`
- `millrace queue ...`
- `millrace status`
- `millrace runs ...`
- `millrace trace ...`
- `millrace waits ...`
- `millrace interventions ...`
- `millrace dispatch ...`
- `millrace doctor`
- `millrace run daemon ...`

Root aliases such as `add-task`, `add-spec`, `add-probe`, `add-idea`, `pause`,
`resume`, `stop`, `retry-active`, `clear-stale-state`, and `reload-config`
are not v0.22 commands.

## Recreate Workflow And Runner Authority

The base `millrace-ai` package includes only the `kernel_ping` diagnostic
workflow. Install `millrace-plus` for official workflows and import its
package data into the new workspace.

Compile new authority with a supported runner:

- Millforge is the default for eligible new compilation.
- Codex remains explicitly selectable.
- Existing selected plans are never remapped.
- Pi RPC and other v0.21 runner configuration are not carried forward.

Workflow package import/export moves v0.22 package data only. It does not
migrate queued work, runs, plans, waits, artifacts, or history.

## Verify The New Workspace

After selection and before unattended execution:

```bash
millrace --workspace "$WORKSPACE" workspace check
millrace --workspace "$WORKSPACE" package list --command-id list-packages-001
millrace --workspace "$WORKSPACE" plan show
millrace --workspace "$WORKSPACE" status
millrace --workspace "$WORKSPACE" doctor
```

Then run a bounded daemon session with explicit adapter configuration and
inspect status and trace output before leaving it active.

If an older workspace contains active work, finish or retire that work with
the matching runtime and preserve its state. The v0.22.3 candidate uses store
schema 11. It refuses exact schema-6 and schema-7 workspaces unchanged as
`workspace_upgrade_required`; schema 8 (used by v0.22.2) and schema 9 are
unsupported and also remain unchanged. There is no database/CAS migration or
inferred operator-control state. Initialize a fresh workspace rather than
copying old state into it.

For the complete removed and deferred inventory, read
[v0.22 compatibility](v0.22-compatibility.md). For stable error families and
operator actions, read [Errors and refusals](errors.md).
