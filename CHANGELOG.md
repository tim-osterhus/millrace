# Changelog

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
