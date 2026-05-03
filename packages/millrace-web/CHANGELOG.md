# Changelog

## 0.17.1

- Publish as a normal PyPI sidecar package through the configured
  `millrace-web` trusted publisher.
- Sync the package version and runtime dependency to `millrace-ai>=0.17.1`.

## 0.17.0

- Add optional `millrace-web` distribution with `millrace-web serve`.
- Add read-only FastAPI routes for workspace summaries, queues, runs, compiled
  plan state, usage governance, Arbiter state, and events.
- Add static Detail and Flow views backed by the same API contract.
