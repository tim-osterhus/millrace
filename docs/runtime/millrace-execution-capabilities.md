# Millrace Execution Capability Grants

Millrace now carries typed execution capability grants through compilation,
runtime dispatch, runner artifacts, and run inspection. The feature is a base
framework authority layer: it describes what execution powers a stage may use
and whether those powers are granted, denied, approval-gated, unsupported, or
advisory.

It is not an app permission catalog. Capability ids stay generic to local
runtime execution, such as `workspace.read`, `artifact.write`,
`runner.invoke`, `shell.run`, `git.mutate`, `package.install`,
`network.access`, and `runtime.control`.

## Compile-Time Authority

Stage kinds, graph nodes, modes, and runtime config can request or constrain
capabilities. The compiler resolves those inputs into sealed
`ExecutionCapabilityGrant` records on each compiled graph node.

Each grant records:

- capability id, access verb, and structured scope
- decision state: `granted`, `denied`, `approval_required`, or `unsupported`
- enforcement mode: `runtime_enforced`, `runner_enforced`,
  `adapter_enforced`, `external_api_enforced`, `advisory_only`, or
  `not_applicable`
- approval policy reference when operator approval is required
- evidence requirements and evidence status
- decision reason, resolver, and stable fingerprint

The compiled plan also carries plane/run summaries for operator inspection.
Grant decisions participate in the compiled-plan fingerprint, so changing
capability policy makes the persisted plan stale until recompiled.

## Runtime Gate

Before handing a `StageRunRequest` to a runner, the runtime evaluates the active
node's required grants.

The runtime blocks before runner invocation when a required grant is denied,
unsupported, waiting on approval, or claims enforcement that the runner support
surface cannot satisfy. Blocking writes a `capability_gate.<request_id>.json`
artifact in the run directory and emits a `capability_gate_evaluated` runtime
event.

Advisory grants may proceed only when policy permits advisory grants. They are
always labeled as advisory in prompt context, artifacts, and inspection output;
Millrace does not claim enforcement for boundaries it cannot enforce.

## Runtime Config

The default config keeps rollout compatible while making riskier powers
explicit:

```toml
[execution_capabilities]
enabled = true
default_unknown_capability = "deny"
allow_advisory_grants = true
fail_required_advisory = false

[execution_capabilities.defaults]
network_access = "deny"
package_install = "approval_required"
git_mutate = "approval_required"
shell_run = "allow"
workspace_write = "allow"
```

`execution_capabilities.*` changes are recompile changes. Use
`millrace config reload --workspace <workspace>` for daemon-safe recompile, or
restart without an explicit `--mode` override when config-driven mode selection
should also change.

## Approval-Gated Grants

Approval-required grants create approval objects under:

```text
millrace-agents/approvals/pending/<approval_id>.json
millrace-agents/approvals/resolved/<approval_id>.json
```

Supported commands:

```bash
millrace approvals ls --workspace <workspace>
millrace approvals show <approval_id> --workspace <workspace>
millrace approvals approve <approval_id> --workspace <workspace> --reason "<reason>"
millrace approvals deny <approval_id> --workspace <workspace> --reason "<reason>"
```

When a daemon owns the workspace, approve/deny mutations use the mailbox like
other runtime control actions. When no daemon owns the workspace, the control
layer applies them directly.

## Inspection

`millrace runs show <run_id>` prints compact per-stage lines such as:

```text
capability_grant: grant_id=... capability=runner.invoke decision=granted enforcement=runtime_enforced evidence=pending
capability_support: grant_id=... runner=codex_cli support=supported enforcement=runtime_enforced evidence_available=true
```

Full structured grant details remain in stage-result metadata, runner
invocation/completion artifacts, and capability-gate artifacts.

Use this distinction when diagnosing a blocked run:

- `capability_grant_denied`: runtime config, mode, or node policy denied a
  required grant.
- `capability_approval_required`: an operator approval is pending or unresolved.
- `capability_grant_unsupported`: the runner support surface cannot satisfy a
  required grant.

Credential values must not appear in grants, approvals, artifacts, runtime
events, incidents, or diagnostics. Capability records may reference stable
opaque scope ids, but never persisted secrets.
