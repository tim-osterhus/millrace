# Millrace FAQ

## What does Core run?

Core routes authored graphs and runs local commands. Plus supplies optional native harness adapters and workflow templates.
OS operates multiple independent instances through public controls.

## Is a service required?

Core runs standalone. The optional local OS service supports headless controls and an optional browser client.

## Can Core 0.23.2 read older workspaces?

Core 0.23.2 continues finite graph-v3/store-v7 instances with their existing
schema and limits. New instances use store-v8 with graph-v3 or graph-v4. Core
refuses unsupported graph and store formats before mutation and provides no
store migration.

## How do I edit a workflow?

Edit its ordinary workflow copy. Locked runs pause on changed controls.
Live-edit mode validates changes at invocation boundaries. See [controls](docs/maintainers/router-controls.md).
