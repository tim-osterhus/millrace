<div align="center">
  <p>
    <a href="https://pypi.org/project/millrace-ai/"><img alt="PyPI v0.23.2" src="https://img.shields.io/badge/pypi-v0.23.2-orange.svg"></a>
    <a href="https://www.python.org/downloads/"><img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11+-blue.svg"></a>
    <a href="https://github.com/tim-osterhus/millrace/blob/main/LICENSE"><img alt="Apache-2.0 license" src="https://img.shields.io/badge/license-Apache--2.0-green.svg"></a>
  </p>
  <img
    src="https://raw.githubusercontent.com/tim-osterhus/millrace/main/docs/assets/images/millrace-icon-signal-transparent-glow.png"
    alt="Millrace signal mark"
    width="180"
  />
  <h1>Millrace</h1>
</div>

Millrace Core routes durable workflow graphs through command steps and explicit external execution hosts.
It persists results, transitions, requests, revisions and writer observations so work can resume after interruption.

## Install and run

Python 3.11 or newer is required.

```sh
python -m pip install "millrace-ai==0.23.2"
millrace router --help
```

Follow [Getting started](https://github.com/tim-osterhus/millrace/blob/v0.23.2/docs/getting-started.md) for a command-only workflow.
Core has no runtime dependencies. [Millrace Plus](https://github.com/tim-osterhus/millrace-plus)
supplies optional native adapters, catalogs and editable workflow templates.
[Millrace OS](https://github.com/tim-osterhus/millrace-os) operates independent instances through the same public controls.
The `millrace` bundle combines compatible versions of Core, Plus and OS on Python 3.12 or newer.

## Structure

- `src/millrace/router/`: graph validation, durable state, controls, settings and process mechanics.
- `tests/router/`: command, corruption, recovery, planes, seams and installed boundary proofs.
- `docs/maintainers/`: public control and cutover contracts.

Core 0.23.2 accepts graph-v3 and graph-v4 and creates store-v8.
Graph-v4 lets you omit either overall limit, `limits.max_invocations` or
`limits.max_elapsed_seconds`. Keep the `limits` object. Supplied limits must
be positive and finite. Invocation timeouts and operator controls still apply.
Retained graph-v3/store-v7 state keeps its schema and finite limits. Core
0.23.1 cannot read graph-v4 or store-v8. Core refuses unsupported formats
before mutation and provides no store conversion.

See [How Millrace works](https://github.com/tim-osterhus/millrace/blob/v0.23.2/docs/how-millrace-works.md), [planes](https://github.com/tim-osterhus/millrace/blob/v0.23.2/docs/router-planes.md),
[controls](https://github.com/tim-osterhus/millrace/blob/v0.23.2/docs/maintainers/router-controls.md) and [cutover](https://github.com/tim-osterhus/millrace/blob/v0.23.2/docs/maintainers/router-cutover.md).
