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

Packet 01 keeps these catalogs inert for dispatch. Existing runtime effect
rules still execute through `handler_id`; `effect_operation_id` is now a
validated compiled authority surface that later packets can route through.

## Consequences

This preserves old compiled-plan and handler-id behavior while giving the
compiler a stable operation id to validate. It also makes unsafe store paths,
unknown primitives, unknown validators, unknown stores, and missing
partial-commit policy visible before runtime.

The immediate cost is dual metadata during migration: handler ids remain
required for legacy execution while operation ids become the forward-looking
authoring key. Later ADRs or release notes may retire handler-id authoring once
operation dispatch fully replaces legacy handlers.
