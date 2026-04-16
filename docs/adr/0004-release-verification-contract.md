# ADR-0004: Align release verification with the current packaged runtime contract

**Status**: Accepted  
**Date**: 2026-04-16  
**Deciders**: Millrace maintainers

## Context

The runtime had already converged on a thinner package and a smaller operator contract, but parts of the checked-in workflow and release automation still referenced historical surfaces. That drift is dangerous because stale automation can stay green or simply stop running, while contributors assume the release path is still proving the right thing.

The repository also had an environment-sensitive verification footgun: some `uv run pytest` invocations could pick up an unexpected interpreter or third-party plugin set, making local results less reliable than they appeared. We needed a release-verification contract that was explicit about the commands, the package layout, and the minimum-functionality workspace it proves.

## Decision

Millrace will treat release verification as a checked-in contract tied to the current packaged runtime, current CLI surface, and current minimum-functionality workspace. README guidance, local verification commands, and CI/release workflows must all point at the same real commands and the same packaged-runtime behavior.

Historical workflow steps that reference missing scripts, obsolete CLI commands, or pre-thin-core workspace layouts will be repaired or removed instead of being preserved as dead residue.

## Alternatives considered

- **Leave historical workflow commands in place if they are harmless**: Rejected because dead or misleading automation is worse than no automation; it creates false confidence.
- **Rely on unit tests alone**: Rejected because packaging, wheel data, and CLI entrypoint integrity need explicit proof beyond unit coverage.
- **Create a new large release-engineering framework now**: Rejected because the problem is truthfulness, not a lack of orchestration tooling.

## Consequences

**Positive:**
- Contributors get one authoritative set of verification commands.
- Packaged-wheel behavior remains a first-class regression surface.
- Workflow failures become meaningful signals instead of archaeology.

**Negative / accepted costs:**
- Workflow maintenance becomes part of normal refactor work; module moves that affect packaging or commands must update automation in the same slice or soon after.
- Release checks may be slower because they include real packaging proof, not just in-process imports.

**Neutral but notable:**
- The contract is centered on the minimum-functionality workspace and the current thin-core runtime, not on recreating the historical breadth of the pre-refactor product.

## Follow-up

- Repair or remove dead workflow references.
- Normalize verification commands around the `src` layout.
- Keep README, CI, and release smoke paths aligned as the package topology continues to evolve.
