# ADR-0006: Use explicit workspace baselines and managed upgrades

**Status**: Accepted  
**Date**: 2026-04-27  
**Deciders**: Millrace maintainers

## Context

Millrace workspaces are long-lived runtime boundaries. They contain queue state,
compiled plans, status markers, run artifacts, deployed entrypoints, deployed
skills, mode assets, graph assets, and operator configuration. Earlier behavior
could create or refresh parts of that tree as a side effect of commands that
were not primarily about workspace lifecycle.

That implicit behavior was convenient for early development, but it became a
liability once workspaces needed to support resumable multi-day operation and
package upgrades. If installing a newer Millrace package could silently rewrite
deployed workspace assets, an in-progress run could change under the operator.
If commands quietly bootstrapped missing files, operators could also miss that
they were running against an incomplete or stale workspace baseline.

At the same time, deployed runtime assets are not all the same. Some files are
package-managed defaults, some may be locally edited by operators, and some are
runtime-owned state. Millrace needed an upgrade model that could distinguish
safe package updates from local modifications and real conflicts.

## Decision

Millrace workspace creation is explicit. Operators create the managed runtime
tree with `millrace init --workspace <workspace>`.

Initialization deploys the package-managed baseline under
`<workspace>/millrace-agents/` and writes
`millrace-agents/state/baseline_manifest.json`. The manifest records the
managed deployed asset set and original hashes so later package upgrades can be
classified against both the original baseline and the current workspace files.

Package baseline refresh is also explicit. Operators use `millrace upgrade` to
preview classifications and `millrace upgrade --apply` to apply only safe
managed updates. Upgrade apply preserves local-only modifications, refuses
conflicts, restores missing managed files from the candidate baseline, and
writes a refreshed manifest only after a successful apply.

Most non-`init` operator commands require an initialized workspace and fail with
a direct instruction to run `millrace init` when the baseline is missing.

## Alternatives considered

- **Continue implicit workspace bootstrap in runtime and status commands**:
  Rejected because it hides lifecycle state and makes it too easy to operate an
  accidentally created or incomplete workspace.
- **Rewrite workspace assets automatically when the installed package changes**:
  Rejected because long-running workspaces need stable deployed assets until the
  operator explicitly accepts a refresh.
- **Treat all deployed files as user-owned after initialization**: Rejected
  because it would make safe package asset updates hard to identify and would
  force operators to perform manual diff archaeology.
- **Treat all deployed files as package-owned and overwrite local edits**:
  Rejected because entrypoints, skills, and workspace-local assets may be
  intentionally customized during experiments.
- **Use git state as the workspace baseline**: Rejected because runtime
  workspaces are not required to be git repositories, and package-managed asset
  ownership must work independently of source checkout state.

## Consequences

**Positive:**
- Workspace creation and workspace upgrade become deliberate operator actions.
- Long-running workspaces are insulated from silent package asset drift.
- Operators can preview package updates before applying them.
- The runtime can report baseline identity separately from compiled-plan
  currentness, which makes stale assets and stale compiled authority easier to
  distinguish.
- Managed-file conflict handling becomes deterministic instead of relying on
  ad hoc overwrite behavior.

**Negative / accepted costs:**
- First-run setup requires an explicit `millrace init` step.
- Package upgrades require an explicit preview/apply cycle before deployed
  workspace assets change.
- The baseline manifest becomes a real compatibility surface and must be
  maintained as package asset families evolve.
- Operator docs and CLI errors must stay clear about the difference between
  uninitialized workspaces, stale compiled plans, and baseline upgrade
  candidates.

**Neutral but notable:**
- Explicit baselines do not prevent operators from editing deployed workspace
  assets. They make those edits visible to upgrade classification.
- Baseline identity and compile currentness are related but separate. A
  workspace can have a current baseline and a stale compiled plan, or a locally
  modified baseline that still compiles.

## Follow-up

- Keep `millrace status` and `millrace compile show` clear about baseline
  manifest identity and compiled-plan currentness.
- Maintain upgrade classifications as the managed asset set grows.
- Keep package asset deployment tests tied to the same files that `millrace
  init` and `millrace upgrade` manage.
- Document any future workspace-local extension mechanism in terms of package
  managed, runtime-owned, and operator-owned files.
