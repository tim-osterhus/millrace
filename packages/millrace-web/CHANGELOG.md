# Changelog

## Unreleased

## 0.19.0

- Sync the package version and runtime dependency to `millrace-ai>=0.19.0`.

## 0.18.6

- Sync the package version and runtime dependency to `millrace-ai>=0.18.6`.

## 0.18.5

- Sync the package version and runtime dependency to `millrace-ai>=0.18.5`.

## 0.18.4

- Sync the package version and runtime dependency to `millrace-ai>=0.18.4`.

## 0.18.3

- Sync the package version and runtime dependency to `millrace-ai>=0.18.3`.

## 0.18.2

- Sync the package version and runtime dependency to `millrace-ai>=0.18.2`.

## 0.18.1

- Sync the package version and runtime dependency to `millrace-ai>=0.18.1`.

## 0.18.0

- Add read-only compiled graph and run-trace API surfaces for Flow:
  `/api/workspaces/<workspace_id>/compiled-plan/graphs` and
  `/api/workspaces/<workspace_id>/runs/<run_id>/trace`.
- Overlay recent trace outcomes in the Flow view while preserving the compiled
  topology as the stable lane structure.
- Sync the package version and runtime dependency to `millrace-ai>=0.18.0`.

## 0.17.4

- Sync the package version and runtime dependency to `millrace-ai>=0.17.4`.

## 0.17.3

- Sync the package version and runtime dependency to `millrace-ai>=0.17.3`.

## 0.17.2

- Prevent the Flow graph from rebuilding animated lane DOM on unchanged
  one-second workspace summary polls.
- Polish Detail and Flow layout density, long identifier display, Flow lane
  wrapping, and read-only dashboard legibility.
- Replace visibly tiled particle motion with non-repeating random-walk particle
  layers in the Flow view.
- Update package docs and the shipped Millrace ops skill with current
  `millrace-web serve` usage and the read-only/no-lock safety boundary.
- Sync the package version and runtime dependency to `millrace-ai>=0.17.2`.

## 0.17.1

- Publish as a normal PyPI sidecar package through the configured
  `millrace-web` trusted publisher.
- Sync the package version and runtime dependency to `millrace-ai>=0.17.1`.

## 0.17.0

- Add optional `millrace-web` distribution with `millrace-web serve`.
- Add read-only FastAPI routes for workspace summaries, queues, runs, compiled
  plan state, usage governance, Arbiter state, and events.
- Add static Detail and Flow views backed by the same API contract.
