"""Built-in extension boundary implementations.

Each module in this package provides a thin adapter that wraps existing
domain-specific runtime code behind an extension-owned interface.  These
adapters are registered with the BuiltInExtensionBoundaryRegistry and
should be replaced with fully extension-owned implementations as domain
behaviour is migrated to the runtime-operation-step model (ADR-0014).

Maintenance guardrails:
- Do not add new direct kernel-to-domain imports in these adapters.
  They exist only to bridge existing code to the extension interfaces.
- When the underlying domain module is refactored to use
  operation-id-indexed registrations, replace the adapter with the new
  registration path rather than updating the adapter's delegation.
- If a shipped mode no longer requires a domain, its adapters must not
  be loaded.  The lazy-import boundary registry ensures this for
  adapters whose interfaces are never requested.
"""
