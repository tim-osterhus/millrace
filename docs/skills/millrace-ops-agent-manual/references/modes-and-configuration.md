# Modes And Configuration

## Contents

- Pure graph-authority contract
- Shipped modes
- Learning and Librarian behavior
- Configuration and reload
- Runners, model aliases, capabilities, and usage governance

## Pure Graph-Authority Contract

Current `0.21.x` workspaces use a breaking pure graph-authority contract.
Daemon decisions require compiled graph, extension, scheduler, recovery,
lifecycle, runtime-effect, queue-family, request-context, and artifact-contract
metadata. Missing compiled policy is an error.

If validation reports stale or missing authority metadata, refresh managed
assets with:

```bash
millrace upgrade --apply --workspace <workspace>
millrace compile validate --workspace <workspace>
```

For disposable workspaces, reinitialization may be cleaner than compatibility
repair.

## Shipped Modes

Use canonical LAD names for new config and documentation:

- `lad_codex`: canonical bootstrap baseline
- `lad_pi`: same loops and stage semantics, Pi RPC runner binding
- `learning_lad_codex`: LAD plus Learning plane on Codex
- `learning_lad_pi`: LAD plus Learning plane on Pi
- `efficient_learning_lad_mixed`: LAD plus Learning topology with mixed
  Codex/Pi mode-local stage aliases; Integrator is inactive because the mode
  selects `execution.lad`
- `lad_codex_integrated`: Codex with `execution.lad_integrator`
- `learning_lad_codex_integrated`: integrated Execution plus Learning
- `blueprint_lad_codex`: LAD Execution plus Blueprint Planning
- `blueprint_learning_lad_codex`: Blueprint Planning plus Learning

Compatibility aliases remain accepted only for older operator configs:

- `standard_plain -> lad_codex`
- `standard_millrace -> lad_pi`
- `learning_enabled_millrace -> learning_lad_pi`
- `default_codex -> lad_codex`
- `default_pi -> lad_pi`
- `learning_codex -> learning_lad_codex`
- `efficient_learning_mixed -> efficient_learning_lad_mixed`
- `learning_pi -> learning_lad_pi`
- `default_codex_integrated -> lad_codex_integrated`
- `learning_codex_integrated -> learning_lad_codex_integrated`
- `blueprint_codex -> blueprint_lad_codex`
- `blueprint_learning_codex -> blueprint_learning_lad_codex`

Daemon mode uses a compiled lane scheduler. LAD modes remain one active lane
per plane, and shipped policies keep Planning and Execution mutually exclusive.
Learning is the opportunistic concurrent lane when the compiled policy permits
it. Runtime-owned mutation remains single-writer and serialized by the daemon
supervisor.

## Learning And Librarian

Learning-enabled modes add Analyst, Professor, Curator, and Librarian.

Generic success-triggered learning starts at Analyst. Direct Curator trigger
rules are valid only when a compiled mode names a safe destination such as
`target_skill_id` or `preferred_output_paths`.

Successful Planner runs enqueue Librarian as a targeted Learning request.
Librarian reads Planner output, checks the installed skill index, refreshes or
checks the supported remote skill index, installs up to eight relevant remote
optional skills that are not already installed, and exits as a clean no-op when
no relevant uninstalled remote skill exists. Planning and Execution do not wait
on Librarian.

Curator may perform a format-only migration of a touched workspace-installed
skill only when it is already applying an evidence-backed behavior patch, the
current skill linter reports a package/section-shape problem, and the migration
preserves existing semantics. It must not edit source-packaged skills or
promote them.

## Configuration Surface

Treat `<workspace>/millrace-agents/millrace.toml` as the supported operator
configuration surface. Configure runner behavior there rather than through side
channels.

New workspaces bootstrap with:

- `runtime.default_mode = "lad_codex"`
- `runners.default_runner = "codex_cli"`
- Codex `permission_default = "maximum"`

To switch a managed workspace into an integrated quality loop after a package
update:

1. `millrace upgrade --apply --workspace <workspace>`
2. Set `runtime.default_mode = "lad_codex_integrated"` or
   `"learning_lad_codex_integrated"`.
3. Run `millrace config reload --workspace <workspace>`.

If the daemon was started with explicit `--mode`, that override remains pinned
across reloads. Restart without the override or with the intended mode.

Config reload recompiles changes such as `runtime.default_mode`,
`stages.<stage>.*`, `model_aliases.*`, and `model_assignment.*` on the daemon's
next tick when a daemon owns the workspace. Active runs keep their launch
compiled plan while a newer alias plan waits as pending.

Stage config supports learning stages such as `professor` and `librarian`,
including `model`, runner-neutral `thinking_level`, legacy Codex
`model_reasoning_effort`, and `timeout_seconds`.

## Model Aliases And Runners

Model aliases live under `[model_aliases.<alias>]` and assignment policy lives
under `[model_assignment]`. Defaults are `fast`, `standard`, and `deep`, with
`standard` selected globally. Prefer `millrace model-aliases ...` commands over
hand-editing.

Shipped modes can carry mode-local aliases. `efficient_learning_lad_mixed`
uses:

- Codex aliases: `codex_max`, `codex_med`, `codex_fast`
- Pi/DeepSeek aliases: `deepseek_max`, `deepseek_med`, `deepseek_fast`

Pi defaults disable Pi-native context-file and skill discovery so shipped
`lad_pi` remains deterministic.

Codex permission resolution order:

1. `runners.codex.permission_by_stage`
2. `runners.codex.permission_by_model`
3. `runners.codex.permission_default`

## Usage Governance And Capabilities

Usage governance is disabled by default. When enabled, it evaluates between
stages, can pause via `usage_governance`, and can auto-resume only when active
governance blockers clear. `config reload` applies governance changes at the
next runtime tick; inspect `millrace status` and monitor lines for the result.

Execution capability policy lives under `[execution_capabilities]`.
Grant-affecting changes are recompile changes, not next-tick runtime-only
changes. Defaults keep rollout compatible: advisory grants are allowed, strict
required-advisory failure is disabled, network access is denied, and package
install plus git mutate grants require operator approval.

Do not describe advisory execution capability grants as enforced. Codex
`maximum` and broad Pi RPC operation may be operationally powerful without
giving Millrace a narrow enforceable boundary.
