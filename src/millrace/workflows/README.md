# Included Workflows

The base `millrace-ai` package includes only `kernel_ping`.

`kernel_ping` is a small diagnostic workflow used to verify that the installed
compiler, kernel, storage, and runner boundaries can execute a minimal selected
plan. It is not intended as a general-purpose work loop.

Official ready-to-use workflows such as `simple_loop`, LAD, and
`vendor_selection` live in the separate `millrace-plus` data package. Keeping
them outside the base runtime prevents workflow vocabulary from becoming
kernel behavior.

Some source-only donor modules remain in the repository for conformance tests.
They are excluded from the built wheel and
source distribution, are not exported from `millrace.workflows`, and are not
part of the base package's supported workflow inventory: `simple_loop`,
`lad_execution`, `lad_planning`, `lad_learning`, and `vendor_selection`.
