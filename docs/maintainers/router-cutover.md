# Millrace 0.23 artifact and state boundary

The Core wheel and sdist contain the inert `millrace` namespace, its typing marker and the generic router.
The active source tree removes the retired compiler, kernel, operator, substrate, native-adapter and workflow packages.
The console entrypoint is `millrace.router.cli:cli`. Runtime dependencies are empty.

Plus requires the public `millrace-router-execution/v2` contract.
Equal package versions alone do not establish contract compatibility.

Released 0.23.1 uses graph-v3/store-v7 state with request-v2, receipt-v2 and execution-v2 controls.
Unsupported old formats refuse before mutation. Do not migrate, backfill or downgrade old graphs or stores.
If real protected old work exists, preserve its matching independent runtime, environment and state before replacement.
A dormant old installation does not require reconstructing an obsolete deployment.

## Verify artifacts

Run the active suite, Ruff, strict mypy and build checks. Verify wheel and sdist payloads with
`scripts/release/verify_v023_distribution.py`. Rebuild the wheel from the sdist and compare its payload.
Install the exact wheel outside source checkouts with `PYTHONPATH` unset.
Prove command execution, exact replay, module provenance and absence of Plus and retired modules.

Preserved v0.22 source is available through Git commit
`af2ceb0ab10d1b265b4ec65c9658e16c66b71a6b`. Its docs and test history describe that release only.
Historical provider qualification does not qualify changed adapters or new artifacts.
Use fake-process tests for packaging/control proofs. Native provider calls need a separate finite allowance.


## Core 0.23.2 graph and store compatibility

Core 0.23.2 adds authored graph-v4 and creates store-v8. It continues operating
genuine finite graph-v3/store-v7 without changing the schema, format metadata or
startup bounds. New v8 instances can start from v3 or v4; retained v7 instances
refuse a v4 candidate before candidate/activation mutation. That supported-control
refusal may record only its targeted durable receipt. Unknown-format opens and
corrupt state retain the no-write refusal rule.

V8 adds only a private required `runs.started_at REAL NOT NULL` anchor, written
from the same original-start sample as public `started_at` in the atomic creation
transaction. Normal updates never rewrite it. Validate its presence even in an
empty store and exact finite equality before replay; do not repair or backfill.
This is consistency evidence against isolated edits, not an authenticity system.
Do not upgrade disposable anchorless intermediate candidate fixtures.

Core 0.23.1 refuses graph-v4 and store-v8 before state mutation. When checking
compatibility, retain the old runtime and artifacts. Verify creation receipt
replay, a subsequent finite run and schema identity against authentic v7 state.
Use the exact wheel and rebuild from the sdist for installed checks.
