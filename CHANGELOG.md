# Changelog

## 0.23.2 (unpublished development candidate)

- Add the public synchronous `millrace.router.process` API with the
  `owned-process-v1` capability, immutable launch/result records, bounded byte
  capture and owned process-group cleanup. Core remains dependency-free.
- Migrate the builtin command adapter to that API. Optional lifecycle callbacks
  preserve first-stop ordering and drained-result snapshots without native
  protocol policy in Core; synchronous callbacks must do bounded work.
- Preserve graph-v3/store-v7 and request-v2/receipt-v2/execution-v2 formats.
  Offline Linux acceptance does not establish native/provider or macOS
  qualification, or publication. The published 0.23.1 release is unchanged.

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
