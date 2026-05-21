# Millrace Architecture Decision Records

This directory records accepted architecture decisions for the Python
`millrace-ai` runtime. ADRs are append-only by default: newer records may
extend or supersede older decisions, but older decisions remain useful context
unless explicitly marked otherwise.

## Index

| ADR | Status | Summary |
| --- | --- | --- |
| `0001-adopt-src-layout-and-domain-packages.md` | Accepted | Use a `src/` layout and grow domain packages deliberately. |
| `0002-runtime-engine-decomposition.md` | Accepted | Keep `RuntimeEngine` as a facade while extracting owned runtime concerns. |
| `0003-error-taxonomy-and-public-boundaries.md` | Accepted | Root service-layer failures in a shared `MillraceError` hierarchy. |
| `0004-release-verification-contract.md` | Accepted | Keep release verification aligned with the packaged runtime contract. |
| `0005-compiled-graph-plan-as-runtime-authority.md` | Accepted, extended by ADR-0010 | Treat `compiled_plan.json` as the runtime-authoritative execution contract. |
| `0006-explicit-workspace-baselines-and-managed-upgrades.md` | Accepted | Require explicit workspace baselines and managed upgrade classification. |
| `0007-runtime-internal-authority-packages.md` | Accepted | Use stable facades with internal authority packages for high-risk runtime domains. |
| `0008-contract-facade-and-domain-contract-modules.md` | Accepted | Keep `millrace_ai.contracts` as a facade over domain contract modules. |
| `0009-stage-metadata-single-source-of-truth.md` | Accepted | Use typed stage metadata as the shipped-stage legality registry. |
| `0010-compiler-validated-workflow-primitives-as-runtime-authority.md` | Accepted | Extend compiled authority to workflow primitives, lanes, request context, effects, and schema epoch. |

## Reading Notes

ADR-0005 remains the foundation for compiled runtime authority. ADR-0010 does
not replace it; it extends the same authority model beyond graph topology into
compiler-validated workflow primitives and their runtime consumers.

ADR-0009 remains current for shipped stage legality. It does not claim that
stage metadata alone defines all custom workflow behavior. Stage metadata,
stage-kind assets, graph-loop assets, and workflow primitive assets together
feed the compiled plan.
