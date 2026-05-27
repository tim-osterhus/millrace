# ADR-0011: Declarative Runtime Effect Operations

Status: Accepted

Date: 2026-05-26

## Context

Runtime effect rules already decide which post-stage mutation handler consumes
stage artifacts, but the implementation still treats handler ids as both the
compiled policy key and the runtime implementation key. That makes Blueprint
effects look like a privileged runtime subsystem instead of data-driven
workflow authority.

## Decision

Introduce compiler-validated runtime effect operation catalogs:

- runtime effect operations describe steps, required artifacts, legacy handler
  compatibility, idempotency, failure mappings, mutation journal schema, and
  partial-commit policy;
- effect stores describe safe runtime-relative storage roots and write policy;
- effect validators describe artifact/store validation primitives and failure
  classes.

Runtime effect dispatch is operation-id first. `effect_operation_id` selects a
compiled runtime-effect runner, and the runner selects the Python implementation
through an operation-indexed registry. `handler_id` remains optional legacy
alias metadata for old artifacts, older policies, and compatibility displays;
it is no longer the runtime authority for selecting an effect.

During the migration, each rule's legacy handler id, when present, must be
declared as an alias on the selected runner and operation. Required run
artifacts must be declared by that operation, duplicate/replay policies must
agree, and handler-authored result metadata cannot override the compiled
operation id or runner id.

## Consequences

This preserves compatibility for old handler-id artifacts while making the
compiler's operation catalog the stable identity for dispatch, failure-policy
matching, spawned-work destination lookup, metadata, and runtime events. It also
makes unsafe store paths, unknown primitives, unknown validators, unknown
stores, missing runner ownership, and missing partial-commit policy visible
before runtime. Persisted plans that predate the operation/store/validator/
runner catalogs are treated as stale so startup `compile_if_needed` refreshes
them instead of silently reusing empty catalog defaults.

The immediate cost is dual metadata during migration: operation ids and runner
ids are required runtime metadata, while legacy handler ids remain optional
aliases where compatibility requires them. Later ADRs or release notes may
retire handler-id authoring once the compatibility window closes.
