# Runner Adapters, Model Selection, And Permission Profile Semantics

## 1. Purpose And Scope

This document owns the runtime boundary that explains how Millrace chooses the runner-facing stage settings, normalizes them into `StageContext`, and turns that context into a concrete runner command and artifact set.

It covers `StageConfig` defaults in `millrace_engine/config_runtime.py`, native config loading and prompt-path resolution in `millrace_engine/config.py`, the public enums in `millrace_engine/contract_core.py`, the normalized runner payload in `millrace_engine/contract_runtime.py`, the stage-to-runner handoff in `millrace_engine/stages/base.py`, and the concrete runner adapters in `millrace_engine/runner.py`.

It does not own execution-plane routing, prompt legality, registry/materialization-driven model-profile binding, or network-policy enforcement outside the runner payload this boundary receives. Those remain with the execution, prompt-contract, configuration/materialization, and policy docs.

## 2. Source-Of-Truth Surfaces

The authoritative surfaces for this boundary are:

- `millrace_engine/config_runtime.py`: defines `StageConfig` and `default_stage_configs()`, which are the shipped defaults for runner, model, effort, permission profile, timeout, prompt asset, and `allow_search`.
- `millrace_engine/config.py`: loads native TOML, finalizes workspace-relative prompt paths, and defines adjacent config defaults such as Sentinel diagnostic runner/model/effort and complexity-profile references.
- `millrace_engine/contract_core.py`: defines the public enums `RunnerKind`, `HeadlessPermissionProfile`, and `ReasoningEffort`.
- `millrace_engine/contract_runtime.py`: defines `StageContext`, the normalized runner input that carries resolved model, effort, permission profile, prompt provenance, search/network flags, and any injected compounding payloads.
- `millrace_engine/stages/base.py`: resolves the active `StageConfig`, builds `StageContext`, and passes it to the configured runner.
- `millrace_engine/runner.py`: maps runner kinds to executables, builds default commands, exports runner environment variables, executes subprocesses, captures artifacts, applies timeouts, and extracts Codex usage telemetry.
- `tests/test_runner.py` and `tests/test_config.py`: prove the current command mapping, enum values, stage defaults, prompt-path resolution, and runner environment export.

Authority order for this boundary is:

1. enum values and model contracts in `contract_core.py` and `contract_runtime.py`
2. stage-config defaults and load/finalization behavior in `config_runtime.py` and `config.py`
3. stage-to-runner handoff in `stages/base.py`
4. runner-specific command construction and subprocess behavior in `runner.py`
5. deep docs and portal links

If the docs disagree with the code or tests, the code and tests win.

## 3. Lifecycle And State Transitions

### 3.1 Stage Settings Start In `StageConfig`

`StageConfig` is the concrete runtime-owned stage settings model. Its fields are:

- `runner`
- `model`
- `effort`
- `permission_profile`
- `timeout_seconds`
- `prompt_file`
- `allow_search`

The base defaults are intentionally simple:

- `runner = codex`
- `model = gpt-5.3-codex`
- `effort = null`
- `permission_profile = normal`
- `timeout_seconds = 3600`
- `allow_search = false`

`default_stage_configs()` then specializes selected stages. Standard execution stages mostly inherit the default Codex runner and model, while some research-family stages move to different models or higher reasoning effort. Sentinel diagnostics are adjacent but separate: `SentinelDiagnosticConfig` defaults to Codex, `gpt-5.3-codex`, and `medium`.

### 3.2 Native Config Load Finalizes Prompt Paths

`load_engine_config()` accepts only native TOML. After validation, `_finalize_config()` resolves:

- `paths.workspace`
- `paths.agents_dir`
- every `StageConfig.prompt_file`

That means runner-facing prompt files are stored as resolved workspace paths by the time execution uses them. This boundary owns that prompt-path finalization because the runner handoff needs a concrete prompt asset location, not an unresolved relative path.

### 3.3 `StageContext` Is The Normalized Runner Payload

`ExecutionStage.run()` in `stages/base.py` bridges runtime config into `StageContext`. It copies the stage-facing values the runner actually consumes:

- `runner`, `model`, `permission_profile`, `timeout_seconds`, and `effort` from the active `StageConfig`
- the rendered prompt text and working directory
- the resolved workspace prompt path when the prompt came from the workspace rather than the packaged bundle
- `allow_search` from the stage config unless an explicit override was supplied
- `allow_network`, which defaults to `True` unless an override was supplied
- `command`, `env`, and any compounding injections

`StageContext` is therefore the stable runtime seam between configuration and execution. It is also where some values become more explicit than the stage config itself. For example, `allow_network` is not a `StageConfig` field today, but it is part of the normalized runner payload.

### 3.4 Exact Public Enums

This boundary documents three public enums exactly as shipped:

- `RunnerKind`: `codex`, `claude`, `subprocess`
- `HeadlessPermissionProfile`: `normal`, `elevated`, `maximum`
- `ReasoningEffort`: `low`, `medium`, `high`, `xhigh`

The doc-proof surface must stay exact on these values. Adding synonyms or extra profiles here would be false.

### 3.5 Default Command Construction

Runner adapters split into three families.

`SubprocessRunner`:

- requires an explicit `context.command`
- raises `ValueError` if the command is empty
- otherwise returns the command unchanged

`CodexRunner`:

- uses `context.command` as-is when present
- otherwise builds a structured `codex` CLI invocation
- starts from `codex exec --json --skip-git-repo-check --model <model>`
- adds `--search` before `exec` when `allow_search` is true
- adds permission flags according to `permission_profile`
- adds `-c model_reasoning_effort="<value>"` when `effort` is set
- writes the rendered last-response file with `-o <path>`

`ClaudeRunner`:

- uses `context.command` as-is when present
- otherwise currently returns `("claude", context.prompt)`
- does not currently translate permission profile, search, or reasoning effort into Claude-specific CLI flags in `build_command()`

### 3.6 Permission Profile Semantics

The current Codex command mapping is exact and narrow:

- `normal`: adds `--full-auto`
- `elevated`: adds `--full-auto --sandbox danger-full-access`
- `maximum`: adds `--dangerously-bypass-approvals-and-sandbox`

This is the behavioral contract proved by `tests/test_runner.py`. It is also the safety boundary operators need to understand:

- `normal` is the default automated path
- `elevated` still runs through the full-auto path but asks Codex for a danger-full-access sandbox
- `maximum` bypasses approvals and sandboxing entirely for the default Codex command path

The profile value is also exported through `MILLRACE_PERMISSION_PROFILE`, so downstream wrappers can see the runtime-selected profile even when they do not inspect the original TOML.

### 3.7 Artifact Capture And Environment Export

Every `BaseRunner.execute()` call allocates a per-run directory, writes `stdout`, `stderr`, and last-response artifacts, and appends runner notes that summarize the stage, runner, model, exit code, marker, and artifact paths.

The shared runner environment exports:

- `MILLRACE_PROMPT`
- `MILLRACE_STAGE`
- `MILLRACE_MODEL`
- `MILLRACE_PERMISSION_PROFILE`
- `MILLRACE_ALLOW_SEARCH`
- `MILLRACE_ALLOW_NETWORK`
- `MILLRACE_REASONING_EFFORT`

Important truthful limitation: `allow_network` is exported and carried through `StageContext`, but `CodexRunner.build_command()` does not currently translate it into a Codex CLI flag. The command contract here is search-aware, not network-flag-aware.

## 4. Failure Modes And Recovery

### 4.1 Invalid Or Missing Runner Commands

The first failure class is configuration or invocation mismatch:

- `SubprocessRunner` fails if no explicit command is supplied
- any runner fails if `StageContext.runner` does not match the runner adapter instance
- `BaseRunner.execute()` fails if the final command tuple is empty

These are hard boundary failures because the runtime cannot safely infer a replacement command.

### 4.2 Missing Executables And OS-Level Launch Failures

`runner_executable_name()` and `resolve_runner_executable_path()` expose the expected external executable names for Codex and Claude, but actual execution still depends on the executable being present. If subprocess launch raises `OSError`, `BaseRunner.execute()` writes the error to `stderr` and continues returning a normalized `RunnerResult`.

That keeps failure reporting truthful: the runtime still records artifacts and exits through the standard stage-result path instead of crashing out of the boundary with no evidence.

### 4.3 Timeouts

`BaseRunner.execute()` enforces `context.timeout_seconds` through `process.communicate(..., timeout=...)`.

On timeout it:

- terminates the process group
- appends `RUNNER_TIMEOUT after <seconds>s` to stderr
- returns exit code `124`

This is the runner-level timeout contract. Higher-level stage legality and routing decisions belong to the execution-stage boundary after the runner result comes back.

### 4.4 Marker And Telemetry Limitations

Runner adapters only extract the last `### MARKER` line from `stdout`, or from the rendered last-response file when needed. They do not decide whether that marker is legal for the stage; `stages/base.py` owns that later legality check.

Similarly, Codex usage telemetry is best-effort. `CodexRunner.telemetry()` extracts usage from JSONL stdout when present, but a missing usage record is not itself a runner crash.

## 5. Operator And Control Surfaces

Operators and supervisors usually do not instantiate runner adapters directly, but they depend on the evidence this boundary emits.

The main surfaces are:

- `millrace.toml` stage entries, which define runner/model/effort/prompt defaults
- stage prompt assets under `agents/`
- per-run artifacts under `agents/runs/<run_id>/`
- runner notes, which summarize the effective runner, model, marker, and artifact paths
- config inspection surfaces such as `config show --json`

Practical operator rules:

- treat permission profiles as runtime-selected execution safety modes, not as vague prose labels
- treat `gpt-5.3-codex`, `gpt-5.2`, and similar model strings as direct runtime values, not inferred aliases
- treat `allow_search` as the current command-level feature toggle in Codex command construction
- do not assume `allow_network` changes the current Codex CLI invocation, because the runtime does not wire it that way here

This boundary also explains why different stages can share the same runner while still varying meaningfully by model, reasoning effort, timeout, and prompt asset.

## 6. Proof Surface

The strongest proof for this boundary comes from:

- `tests/test_runner.py`, which verifies:
  - executable-name helpers
  - default Codex command construction
  - `elevated` and `maximum` Codex permission-profile mappings
  - search and reasoning-effort flags
  - runner environment export
  - subprocess and Claude artifact behavior
- `tests/test_config.py`, which verifies:
  - default stage configs use shipped model ids
  - stage defaults keep `permission_profile = normal` and `timeout_seconds = 3600`
  - `StageContext` defaults align with the config boundary
  - native TOML load resolves stage prompt paths into the workspace
  - Sentinel diagnostic runner/model/effort overrides load truthfully

Packaging proof for this doc stays narrow:

- `tests/test_package_parity.py` must require the public and packaged Run 06 doc paths plus the IA and portal mirrors
- `tests/test_baseline_assets.py` must require the bundled path and key markers for runner kinds, permission profiles, and Codex command semantics
- `millrace_engine/assets/manifest.json` must contain the correct SHA and size for the shipped packaged doc

Drift should fail proof when:

- the public and packaged docs diverge
- the IA still points at the stale Run 06 filename
- the doc invents extra permission profiles or runner kinds
- the Codex permission mapping stops matching `tests/test_runner.py`

## 7. Change Guidance

Update this doc when changes affect:

- `StageConfig` runner/model/effort/permission defaults
- `StageContext` runner-facing fields
- default Codex, Claude, or subprocess command construction
- runner environment export
- timeout handling or artifact capture

Do not expand this doc to absorb:

- stage legality or terminal-marker enforcement
- execution-plane routing
- config apply-boundary semantics
- deeper registry/materialization-driven model-profile binding

If a future change primarily alters config ownership or live-reload timing, route it to the configuration boundary doc. If it primarily changes stage transition legality or recovery after runner execution, route it to the execution-pipeline doc. Keep this document focused on runner adapters, direct model/effort selection at the stage-config seam, and permission-profile behavior.
