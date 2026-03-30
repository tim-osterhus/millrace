# Test Fixtures

These fixtures materialize realistic `millrace/` workspaces for integration tests.

- `base/` is the common workspace skeleton.
- named scenario folders extend `base/` through `fixture.toml`.
- scenario overlays replace only the files that differ for that test case.
