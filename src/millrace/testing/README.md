# Testing Support

This package contains reusable test helpers shared across Millrace modules.

It is the appropriate home for fake ports, conformance helpers, deterministic
fixture IDs, and golden-data utilities that have more than one consumer.
Behavior-specific fixtures should remain beside the tests that use them.

Nothing in this package is runtime authority or a supported production
extension surface.
