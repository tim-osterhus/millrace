# Millrace 0.23 artifact and state boundary

The Core wheel and sdist contain the inert `millrace` namespace, its typing marker and the generic router.
The active source tree removes the retired compiler, kernel, operator, substrate, native-adapter and workflow packages.
The console entrypoint is `millrace.router.cli:cli`. Runtime dependencies are empty.

Core, Plus and OS use release version 0.23.0. Plus requires the public `millrace-router-execution/v2` contract.
Equal package versions alone do not establish contract compatibility.

Use freshly authored graph-v3/store-v7 state with request-v2, receipt-v2 and execution-v2 controls.
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
