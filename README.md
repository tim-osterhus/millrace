# Millrace Core

Millrace Core routes durable workflow graphs through command steps and explicit external execution hosts.
It persists results, transitions, requests, revisions and writer observations so work can resume after interruption.

## Install and run

Python 3.11 or newer is required.

```sh
python -m pip install "millrace-ai==0.23.0"
millrace router --help
```

Follow [Getting started](docs/getting-started.md) for a command-only workflow.
Core has no runtime dependencies. [Millrace Plus](https://github.com/tim-osterhus/millrace-plus)
supplies optional native adapters, catalogs and editable workflow templates.
[Millrace OS](https://github.com/tim-osterhus/millrace-os) operates independent instances through the same public controls.
The `millrace==0.23.0` bundle installs Core, Plus and OS together on Python 3.12 or newer.

## Structure

- `src/millrace/router/`: graph validation, durable state, controls, settings and process mechanics.
- `tests/router/`: command, corruption, recovery, planes, seams and installed boundary proofs.
- `docs/maintainers/`: public control and cutover contracts.

Graphs use `millrace-router/v3`; fresh stores use `millrace-router-store/v7`.
Unsupported historical formats refuse before mutation. There is no state migration.
See [How Millrace works](docs/how-millrace-works.md), [planes](docs/router-planes.md),
[controls](docs/maintainers/router-controls.md) and [cutover](docs/maintainers/router-cutover.md).
