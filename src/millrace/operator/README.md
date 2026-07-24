# Operator

The operator package implements the single-local-operator command boundary.

Mutation commands shape explicit audited inputs and submit them through the
runtime. Read commands project status from durable state. Neither path may
write authoritative records directly.

## Surfaces

The package supports:

- workflow package import, enable, disable, removal, inspection, and
  verification;
- compiled-plan admission and default selection;
- queue intake;
- status, run, trace, dispatch, wait, intervention, and health projections;
- operator wait and lineage-intervention choices.

Package verification checks whether a workflow can be selected. It does not
admit a plan or start a run. Health diagnostics report package, plan, storage,
and active-pin problems without repairing them.

The operator model records actor IDs for audit, but v0.22 assumes one local
operator. Authentication, access control, tenants, and remote fleet management
belong outside this runtime.
