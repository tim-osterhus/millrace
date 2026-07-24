# Substrate

The substrate stores Millrace runtime state without owning workflow policy.

SQLite holds local control records. A filesystem content-addressed store holds
immutable plans, payloads, artifacts, and other byte objects. Explicit codecs
connect domain records to those durable formats.

## Responsibilities

- initialize and validate a fresh runtime store;
- atomically persist applied runtime state;
- publish and verify content-addressed objects;
- load state through versioned row and object codecs;
- validate references, object kinds, digests, fingerprints, and relationships;
- refuse unsupported versions or corrupt durable state.

The store does not inspect terminal markers, select routes, rebuild workflows
from source, call runners, or migrate v0.21 workspaces. It persists decisions
that the kernel has already accepted.

Content-addressed object digests identify stored bytes. Compiled-plan authority
fingerprints remain a separate compiler and contract concern.

Publishing an object is process-crash-safe once the atomic replacement
completes. The store does not claim power-loss durability beyond the guarantees
provided by the host filesystem and operating system.
