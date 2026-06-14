# Config-Driven Behavior Test Dependencies

This directory documents the config data used by config-driven behavior tests.
It does not ship standalone JSON fixtures. The tests use built-in runtime
assets copied into temporary workspaces, plus explicit `RuntimeConfig`
overrides where the scenario is a runtime-config change.

## Covered Dependencies

- `assets/graphs/execution/lad.json` and
  `RuntimeConfig.recovery.max_troubleshoot_attempts_before_consult`: used by
  `test_recovery_counter_thresholds.py::TestConfigDrivenThresholdRouting` to
  compare standard threshold routing with a lowered recovery-heavy threshold
  override.
- `assets/modes/lad_codex.json` and `assets/modes/learning_lad_codex.json`:
  used by `test_runtime.py::TestConfigDrivenLearningRequestCreation` and
  supervisor tests to compare Learning-disabled and Learning-enabled behavior.
- `assets/graphs/execution/lad.json`,
  `assets/graphs/execution/lad_integrator.json`,
  `assets/modes/lad_codex.json`, and
  `assets/modes/lad_codex_integrated.json`: used by
  `test_workflow_validation.py::TestConfigDrivenGraphRouting` to prove a
  graph-only route change alters compiled dispatch.
- `assets/modes/lad_codex.json` and
  `assets/registry/extensions/example_blueprint_enhanced.json`: used by
  `test_extension_validation.py::TestConfigDrivenExtensionValidation` to prove
  `required_extensions` declarations alter compile outcomes.
