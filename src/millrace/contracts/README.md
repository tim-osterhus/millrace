# Contracts

This package owns versioned records that cross module, process, package, or
persistence boundaries.

Contracts describe the shape and identity of data. They do not decide workflow
policy or mutate runtime state.

The package includes compiled-plan records, transition inputs and decisions,
runtime state records, package contracts, diagnostics, schemas, identifiers,
events, and traces.

In-memory dataclasses are domain objects, not persistence formats. SQLite rows
and content-addressed objects use separate versioned codecs so storage does not
silently depend on Python field layout.

Stable protocol kind IDs are explicit. Python class names are never used as
durable wire or storage authority.
