# Workflow Surface

This directory contains the base `millrace-ai` workflow surface:

- `kernel_ping.py` defines the included diagnostic workflow.
- `inventory.py` exposes its public inventory metadata and source lookup.
- `__init__.py` exports that diagnostic workflow and inventory API.
- `README.md` documents the surface.

`kernel_ping` is a small diagnostic workflow used to verify that the installed
compiler, kernel, storage, and runner boundaries can execute a minimal selected
plan. It is not intended as a general-purpose work loop.
