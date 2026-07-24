# Compiler

The compiler turns authored workflow data into immutable selected authority.

It validates workflow shape and semantics, resolves references, checks schemas
and terminal actions, selects one workflow entrypoint, and produces a compiled
plan with canonical bytes and an authority fingerprint.

## Main API

- `millrace.compiler.compile_workflow`
- `millrace.compiler.CompileResult`
- `millrace.compiler.canonical_authority_bytes`
- `millrace.compiler.authority_fingerprint`
- `millrace.compiler.compiled_plan_export_record`
- `millrace.compiler.compiled_plan_export_bytes`
- `millrace.compiler.verify_compiled_plan_export_record`
- `millrace.compiler.verify_compiled_plan_export_bytes`

Package-backed selection is exposed through
`millrace.compiler.package_selection`.

## What Compilation Proves

Compilation checks that the selected graph is complete and internally
coherent. Among other things, it verifies:

- stage, queue, runner, schema, outcome, and action references;
- legal terminal-marker and action pairings;
- artifact schemas and payload projections;
- recovery, wait, fanout, join, and completion declarations;
- canonical selected authority values;
- selected package and asset pins.

Export verification proves that compiled-plan bytes retain their expected
fingerprint. It does not admit the plan into a workspace or create runtime
state.

This is a versioned compiled-plan export. The compiled-plan export verification
API is not runtime plan admission. The compiler does not provide a CLI/operator
command or a package-marketplace import path for these exports.

## Boundary

The compiler depends on versioned contracts, not on the kernel, durable
storage, operator CLI, runner adapters, or hosted workflow names. Missing
runtime authority must be diagnosed during compilation rather than supplied by
hidden kernel defaults.
