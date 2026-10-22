# Millrace FAQ

## What does Core run?

Core routes authored graphs and runs local commands. Plus supplies optional native harness adapters and workflow templates.
OS operates multiple independent instances through public controls.

## Is a service required?

Core runs standalone. The optional local OS service supports headless controls and an optional browser client.

## Can 0.23 read older workspaces?

Unsupported graph/store versions refuse before mutation. Create fresh graph-v3/store-v7 state. There is no migration.

## How do I edit a workflow?

Edit its ordinary workflow copy. Locked runs pause on changed controls.
Live-edit mode validates changes at invocation boundaries. See [controls](docs/maintainers/router-controls.md).
