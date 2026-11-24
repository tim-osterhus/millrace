# Changelog

## 0.23.2

- Accept graph-v4 with independently optional overall invocation and elapsed
  limits. New instances use store-v8. Retained graph-v3/store-v7 instances keep
  their finite bounds and schema.
- Add bounded public run inspection and exact invocation lookup. Add an
  exclusive public snapshot for offline maintenance.
- Add the synchronous `millrace.router.process` API and move command execution
  onto it. Clean up owned process groups after unsuccessful root exit.
- Reuse validated invocation plans and validate adapters once per graph
  projection.
- Route `millrace demo` to the compatible Millrace Plus demo when installed.
- Retain request-v2, receipt-v2 and execution-v2 contracts. Core remains
  dependency-free.

## 0.23.1

- Ignore exact regular `.DS_Store` metadata files when creating and listing instances.
- Tolerate disappearing Finder metadata and report other metadata access errors as structured integrity failures.
- Preserve refusal for symlinks and other unexpected state entries.
- Keep graph-v3/store-v7 and request-v2/receipt-v2/execution-v2 contracts unchanged.

## 0.23.0

- Replace the legacy engine with a standalone durable graph router.
- Add graph-v3 planes and seams, exact scoped controls, durable results, revisions and writer reconciliation.
- Keep Core dependency-free. Move native adapters and workflow assets to Plus.
- Remove obsolete source, tests and documentation from the active distribution.
- Refuse unsupported historical state without migration.

Earlier release source and changelogs remain in preserved Git history.
