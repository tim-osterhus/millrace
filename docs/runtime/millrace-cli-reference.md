# Millrace CLI Reference

Installed command: `millrace`  
Module entrypoint: `python -m millrace_ai`

## Defaults

- `--workspace` points to the operator workspace root.
- Runtime config defaults to `<workspace>/millrace-agents/millrace.toml`.
- Runtime bootstrap/output stays under `<workspace>/millrace-agents/`.
- Use `millrace --version` or `millrace version` to print the installed
  package version.

## Primary Command Groups

- `millrace init`
- `millrace upgrade`
- `millrace run ...`
- `millrace status ...`
- `millrace runs ...`
- `millrace queue ...`
- `millrace incident ...`
- `millrace planning ...`
- `millrace approvals ...`
- `millrace config ...`
- `millrace model-aliases ...`
- `millrace control ...`
- `millrace compile ...`
- `millrace modes ...`
- `millrace skills ...`
- `millrace doctor`
- `millrace version`

Compatibility aliases remain for top-level operator commands:

- `millrace add-task`, `millrace add-probe`, `millrace add-spec`, `millrace add-idea`
- `millrace pause`, `millrace resume`, `millrace stop`
- `millrace retry-active`, `millrace clear-stale-state`, `millrace reload-config`

These top-level aliases use the same flags and behavior as their grouped forms.

## Workspace Setup Commands

### `millrace init`

Creates the canonical workspace baseline under `<workspace>/millrace-agents/`.
This is explicit now: most operator commands require an initialized workspace
and will tell you to run `millrace init --workspace <path>` first if the
baseline is missing.
Initialization also writes the current workspace schema epoch marker under
`millrace-agents/state/`. Runtime startup refuses an initialized workspace
whose mutable runtime state is missing that marker or carries an incompatible
epoch.

Options:

- `--workspace PATH`

### `millrace upgrade`

Previews packaged managed-file updates against the workspace baseline manifest.
This command refreshes managed workspace assets only; it does not install or
upgrade the `millrace-ai` Python package that provides the runtime code. Update
the installed package through the deployment environment first, then run
`millrace upgrade` when the workspace baseline should be refreshed from that
installed package.
Default output is preview-only and prints:

- `applied`
- `baseline_manifest_id`
- `candidate_manifest_id`
- counts by disposition
- one `entry: <relative_path> <disposition>` line per managed file

When `--apply` succeeds, the command also prints `result_manifest_id`.

Dispositions currently exposed by the command:

- `unchanged`
- `safe_package_update`
- `local_only_modification`
- `already_converged`
- `localized_removed`
- `conflict`
- `missing`

Use `millrace upgrade --apply` to apply only safe managed baseline updates.
Conflicts fail the apply and leave the workspace baseline unchanged. If a
package release removes a managed asset that you intentionally want to keep as
workspace-local content, preview and apply with `--localize-removed PATH`.
For multiple paths, repeat the flag or use `--localize-removed-from FILE` with
one workspace-relative managed asset path per line.

Options:

- `--workspace PATH`
- `--apply`
- `--localize-removed PATH`
- `--localize-removed-from FILE`

## Run Commands

### `millrace run daemon`

Runs repeated ticks until stop/interrupt, or until `--max-ticks` is reached.
For bounded one-off execution, use `millrace run daemon --max-ticks 1`.
The former public `millrace run once` surface has been removed; bounded daemon
ticks are the supported one-off operation.

Options:

- `--workspace PATH`
- `--mode MODE_ID`
- `--config PATH`
- `--max-ticks N`
- `--monitor [none|basic]`
- `--monitor-log PATH`

The default monitor mode is `none`; `millrace run daemon` does not print live
monitor lines unless `--monitor basic` is passed explicitly. The existing
daemon summary output remains unchanged.

Daemon mode uses a compiled lane scheduler. Default modes remain one active
lane per plane, and shipped policies keep Planning and Execution mutually
exclusive. Runtime-owned queue, snapshot, counter, status, and router mutation
remains single-writer and serialized by the daemon supervisor.
In learning-enabled modes, successful Planner runs can trigger Librarian on the
Learning plane to install relevant remote optional skills into the workspace
without blocking foreground work.
Integrated Codex modes remain opt-in: `default_codex_integrated` and
`learning_codex_integrated` select `execution.with_integrator`, so every
successful Builder run goes through Integrator before Checker. The integrated
learning mode keeps the same Learning concurrency policy as `learning_codex`.
`efficient_learning_mixed` keeps the standard execution loop, leaves
Integrator inactive by default, and uses the same Learning concurrency policy
with a mode-local mixed Codex/Pi model/depth alias profile. DeepSeek stages use
`pi_rpc`; Codex stages use `codex_cli`.
`blueprint_learning_codex` keeps the standard execution loop, selects
`planning.blueprint`, and uses the same Learning concurrency and
Planner-to-Librarian trigger policy as `learning_codex`.

`--monitor basic` prints a compact terminal stream for visible daemon sessions:
startup lifecycle context, baseline/currentness identity, loop and concurrency
policy, status/queue snapshots, stage start and completion lines, router
decisions, usage-governance pause/resume/degraded events, run elapsed time, and
known token usage. The basic monitor is optimized for human scanning: redundant
stage/node/kind identity is collapsed, long run ids are shortened to stable
display handles, intentionally absent route targets are not rendered as
`unknown`, and unknown token usage is omitted. Monitor output is live-only and
does not replace persisted runtime events, run artifacts, `millrace runs ls`,
or `millrace runs show`.

When the daemon is idle with `reason=no_work`, the basic monitor prints the
first idle line immediately and then treats repeated `no_work` idles as a
heartbeat. It emits that heartbeat at most once every 6 hours while the
same idle condition continues. Any non-idle monitor event, or an idle event
with a different reason, resets the heartbeat.

`--monitor-log PATH` writes the same basic monitor format to a file. It can be
used with `--monitor none` for a quiet foreground daemon that still leaves a
clean monitor trail, or with `--monitor basic` to mirror the same live stream
to both stdout and a file.
When router metadata is available, the basic monitor also shows compact
terminal-state/action/lifecycle/runtime-operation details, plus failure-class
and incident markers, on the relevant route lines.

## Status Commands

Canonical operator form: `millrace status`  
Explicit subcommand form: `millrace status show`

### `millrace status`

`millrace status` prints both the legacy foreground active projection and the
canonical lane/`active_run` lines for every active lane. When Learning is
running beside a foreground lane, expect `active_run_count: 2`, one lane line
per declared scheduler lane, and one active-run line per active lane.

Prints runtime snapshot and queue depth for one workspace.
When a failure class is active, status also shows the current failure class plus non-zero retry counters.
`process_running` is reported as true only when the snapshot says the runtime
is running and the workspace ownership lock is currently active. The
`runtime_ownership_lock` line reports the lock state separately.
The `execution_status_marker` and `planning_status_marker` fields show the
currently running stage marker while a stage is executing, then fall back to
the latest terminal marker or `### IDLE` when no stage is active on that plane.
When a learning-enabled mode is active, status also includes
`learning_status_marker` and `learning_queue_depth`.
Status also prints family-specific queue-depth lines, such as
`queue_depth_task`, from the shared family-depth projection.
Status now also surfaces compiled-plan and managed-baseline identity:

- `compiled_plan_id`
- `compiled_plan_fingerprint`
- `pending_compiled_plan_id`
- `pending_compiled_plan_fingerprint`
- `pending_compiled_plan_path`
- `compiled_plan_currentness` (`current`, `stale`, `missing`, or `unknown`)
- `active_node_id`
- `active_stage_kind_id`
- `baseline_manifest_id`
- `baseline_seed_package_version`
- `compile_input.*`
- `persisted_compile_input.*`

Status also surfaces pause and usage-governance context:

- `pause_sources`
- `usage_governance_enabled`
- `usage_governance_paused`
- `usage_governance_blocker_count`
- `usage_governance_auto_resume_possible`
- `usage_governance_next_auto_resume_at`
- `usage_governance_subscription_status`
- `usage_governance_subscription_detail` when present
- `usage_governance_blocker: source=... rule=... window=... observed=... threshold=...`

When Arbiter closure is active, status surfaces closure-target backpressure:

- `closure_target_root_spec_id`
- `closure_target_open`
- `closure_target_blocked_by_lineage_work`
- `planning_root_specs_deferred_by_closure_target`
- `closure_target_latest_verdict_path`
- `closure_target_latest_report_path`

Status also prints `blocked_idle`, `latest_runtime_error_report_path`,
`latest_runtime_failure_origin`, and `latest_operator_intervention`. The
intervention line shows the latest audited operator cleanup event, timestamp,
work item id, and archive destination when one exists.
For repairable Blueprint Evaluator approval generated-task failures, status
also prints the latest runtime-effect metadata plus the structured repair
contract, replay conflict classes, inert-artifact guard, and runtime ownership
boundary. If a later `mechanic_blueprint_repair_apply` effect succeeds in the
same run, status keeps the original repairable Evaluator failure context.
Lane lines include lane id, plane, lane status, lane plan/fingerprint,
active-run ids, active work refs, last terminal outcome, and lane-level
pause/drain/stop requests. Active-run lines include the lane id and the
launch-plan id/fingerprint that remain authoritative for that active run even
when a reload has compiled a newer pending plan.
`blocked_idle: true` means the daemon is running, no plane has an active run,
all queues are empty, an open closure target remains blocked by lineage work,
and the planning status is `### BLOCKED`. That is a diagnostic state, not
normal no-work idleness.

`millrace status show` is an explicit alias for the same text output. Pass
`--format json` to either `millrace status` or `millrace status show` for a
machine-readable status payload with the same key state, including:

- `process_running`
- `compiled_plan_fingerprint`
- `pending_compiled_plan_id`
- `pending_compiled_plan_fingerprint`
- `pending_compiled_plan_path`
- `active_run_count`
- `lanes_by_id`
- `active_runs_by_plane`
- `queue_depths_by_family`
- `execution_queue_depth`
- `planning_queue_depth`
- `learning_queue_depth`
- `closure_target_open`
- `closure_target_blocked_by_lineage_work`
- `blocked_idle`
- `latest_runtime_failure_origin`
- `current_failure_class`
- `latest_runtime_error_report_path`
- `latest_operator_intervention`
- `latest_runtime_effect_handler_id`
- `latest_runtime_effect_operation_id`
- `latest_runtime_effect_runner_id`
- `latest_runtime_effect_legacy_handler_id`
- `latest_runtime_effect_decision`
- `latest_runtime_effect_failure_class`
- `latest_runtime_effect_failure_message`
- `latest_runtime_effect_mutation_phase`
- `latest_runtime_effect_failure_policy_id`
- `latest_runtime_effect_recovery_action`

### `millrace status watch`

Polls runtime status repeatedly.

Options:

- `--workspace PATH` (repeatable; monitors multiple workspaces in one session)
- `--max-updates N`
- `--interval-seconds FLOAT`

`status watch` is monitor-only and does not acquire runtime ownership locks.

## Run Inspection Commands

### `millrace runs ls`

Lists persisted run summaries from `millrace-agents/runs/`.
The `status` field is retained as the artifact-parse status for compatibility.
Use `artifact_status` for the stage-result parse/validation state and
`runtime_outcome` for the route/effect outcome derived from `run_trace.json`
when present. A run can therefore show `artifact_status: valid` and
`runtime_outcome: blocked` when a schema-valid stage result failed during a
runtime effect or recovery route.
The rendered run rows also include terminal metadata provenance plus
terminal-state/action, router consequence, lifecycle plan/action,
writes-status, incident, and runtime-operation fields when available.

### `millrace runs show <RUN_ID>`

Prints one run summary, including work item identity, compiled identity, failure
class, run-level elapsed time, aggregated token usage, per-stage elapsed time,
stdout/stderr paths, and troubleshoot report path when present.

Top-level run fields now include:

- `status` (compatibility alias for artifact status)
- `artifact_status`
- `runtime_outcome`
- `compiled_plan_id`
- `mode_id`
- `request_kind`
- `closure_target_root_spec_id`
- `runtime_effect_decision`
- `runtime_effect_failure_class`
- `runtime_effect_failure_message`
- `runtime_effect_failure_policy_id`
- `terminal_state_id`
- `terminal_action_id`
- `terminal_action_router_consequence`
- `lifecycle_mutation_plan_id`
- `lifecycle_action_id`
- `terminal_writes_status`
- `terminal_metadata_source`
- `terminal_create_incident`
- `runtime_operation_id`

`terminal_metadata_source` distinguishes graph-resolved router output from
derived inspection fallback data. When a run falls back to stage-result
artifacts, the provenance makes the inferred metadata visible instead of
silently presenting it as authoritative trace data. The label is
`graph_resolved` for router-written edge metadata, `inferred` for fallback
inspection data derived from stage results, and `unknown` when no authoritative
edge metadata is available.

Each stage-result block now includes:

- `compiled_plan_id`
- `mode_id`
- `node_id`
- `stage_kind_id`
- `request_kind`
- `closure_target_root_spec_id`
- `runtime_effect_handler_id`
- `runtime_effect_operation_id`
- `runtime_effect_runner_id`
- `runtime_effect_legacy_handler_id`
- `runtime_effect_decision`
- `runtime_effect_failure_class`
- `runtime_effect_failure_message`
- `runtime_effect_mutation_phase`
- `runtime_effect_failure_policy_id`
- `runtime_effect_recovery_action`
- `runtime_effect_source_lifecycle_plan_id`
- `runtime_effect_source_lifecycle_action`
- `runtime_effect_created_path`
- compact `capability_grant` lines when the stage result contains compiled
  execution grants
- compact `capability_support` lines when the selected runner reported
  contextual grant support
- `model_assignment_alias_id` and `model_assignment_source` when the compiled
  node used an alias policy

For Blueprint failures, diagnose duplicate ids separately from same-root
lineage. Same-root remediation is expected when two manifests share
`root_spec_id` but have different `manifest_id` values. A
`blueprint_manifest_duplicate` failure means a manifest id conflict or
divergent duplicate file, not merely another manifest under the same root.
The shipped policy blocks Manager Blueprint runtime-effect failures
conservatively. Only Evaluator approval pre-mutation
`generated_task_missing` and `generated_task_invalid` failures route to
`mechanic_blueprint` automatically. For that repairable approval context,
`runs ls` and `runs show` preserve the original Evaluator failure metadata
even after a later successful `mechanic_blueprint_repair_apply` runtime effect.

### `millrace runs tail <RUN_ID>`

Prints the primary tailable artifact for one run. Millrace prefers the troubleshoot report first, then stdout/stderr artifacts.

### `millrace runs trace <RUN_ID>`

Prints the graph-shaped trace for one run. New runs persist
`millrace-agents/runs/<run_id>/run_trace.json`; older runs without that file are
derived from stage-result artifacts and reported with a fallback note.
Trace edges in the text output carry compact terminal metadata parts when
available: `terminal_metadata`, `action`, `consequence`, `lifecycle_plan`,
`lifecycle_action`, `writes_status`, `runtime_operation`, and
`create_incident`.
The JSON output preserves the same edge provenance via `terminal_metadata_source`
so graph-resolved trace edges stay distinguishable from inferred fallback
inspection data.

Options:

- `--workspace PATH`
- `--format [text|json]`
- `--output PATH`

Use this when you need to see concrete stage instances and runtime router
decisions, for example `builder BUILDER_COMPLETE -> checker` or
`builder BUILDER_COMPLETE -> integrator` in an integrated mode, or
`updater UPDATE_COMPLETE -> terminal:update_complete`.

## Queue Commands

### `millrace queue ls`

Prints queue/active counts for execution, planning, and learning surfaces,
including the probe/spec/incident breakdown inside Planning. It also includes
terminal intervention counters such as `cancelled_task_count`,
`superseded_task_count`, `cancelled_incident_count`, and
`operator_resolved_incident_count`.
For every compiled work-item family, including built-ins and graph-owned
families, `queue ls` also prints family-specific queue/active/blocked
counters, for example `task_queue_depth`, `blueprint_draft_queue_depth`, and
`active_blueprint_draft_count`.

### `millrace queue show <WORK_ITEM_ID>`

Finds and prints one task/probe/spec/incident document summary by ID. Lookup
includes active, queued, done/resolved, blocked, cancelled, superseded, and
operator-resolved archive records.

### `millrace queue add-task <task.md|task.json>`

Imports `TaskDocument`. Canonical queue artifacts are markdown (`.md`); JSON is import-only and must validate against the same contract.

### `millrace queue add-probe <probe.md|probe.json>`

Imports `ProbeDocument`. Probes are lightweight Planning intake that run
through Recon before becoming a generated execution task, generated planning
spec, no-op, or blocked probe. The input file must already be a valid
`ProbeDocument`; arbitrary markdown should be converted into a typed probe
before enqueueing.

### `millrace queue add-spec <spec.md|spec.json>`

Imports `SpecDocument`. Canonical queue artifacts are markdown (`.md`); JSON is import-only and must validate against the same contract.

### `millrace queue add-idea <idea.md>`

Drops idea-shaped markdown into planning intake.

Typed queue commands are not generic markdown ingestion commands. Supporting
local material should be embedded in the typed document or copied into the
active workspace/repo and referenced with repo-relative paths. Do not enqueue
thin wrappers that depend on arbitrary local absolute paths outside the active
workspace. Stable public URLs are acceptable when deliberately supplied by the
operator.

Top-level convenience alias:

- `millrace add-probe <probe.md|probe.json>`
- `millrace add-idea <idea.md>`

### `millrace queue retry-blocked <WORK_ITEM_ID> --reason "..."`

Moves one blocked work item back to its family queue through the supported
audited transition. Built-in families are `task`, `probe`, `spec`, `incident`,
and `learning_request`; compiled graph families such as `blueprint_draft` are
supported only when their family definition declares queue and blocked
directories plus a parse-capable document adapter for the artifact extension.

```bash
millrace queue retry-blocked <TASK_ID> --workspace <workspace> --reason "retry after network outage"
millrace queue retry-blocked <SPEC_ID> --family spec --workspace <workspace> --reason "retry after spec repair" --force
```

When no selector is supplied, task-only usage remains compatible if the blocked
id is unambiguous. Use `--family` as the primary selector for non-task or graph
families; `--kind` remains available for built-in compatibility.

The command refuses work items that are already queued, active, done, missing,
ambiguous without `--family`, malformed, outside the supplied `--root-spec-id`,
not retryable, past the configured retry budget, or in a workspace currently
owned by a live daemon. Stop the daemon first, or cancel the blocked item and
intake fresh corrected work when a retry would replay bad input. Use `--force`
only after inspecting the blocked artifact and accepting that retryability and
budget checks are being overridden. The command appends
`<family-queue>/<WORK_ITEM_ID>.requeue.jsonl`, refreshes queue-depth snapshot
fields, emits `blocked_work_item_requeued`, and still emits
`blocked_task_requeued` for task compatibility.

Related daemon behavior: when auto-recovery is enabled, an idle daemon can
autonomously requeue a same-lineage blocked dependency only when the latest
blocked metadata classifies it as `network_unavailable`,
`provider_unavailable`, `provider_rate_limited`, or `runner_timeout` and the
cooldown/budget gates pass. Stage-authored blocked states, auth failures,
missing runner binaries, malformed terminal output, and unknown transport
failures remain operator-review states.

Options:

- `--workspace PATH`
- `--family FAMILY_ID`
- `--kind task|probe|spec|incident|learning_request|blueprint_draft`
- `--reason TEXT`
- `--root-spec-id ROOT_SPEC_ID`
- `--force`

### `millrace queue cancel <WORK_ITEM_ID> --reason "..."`

Cancels a queued or blocked task/probe/spec/incident intake document without
deleting it. The command moves the artifact into a matching `cancelled/`
archive directory, writes an `interventions.jsonl` audit entry, emits
`work_item_cancelled`, refreshes queue-depth snapshot fields, and prints the
control result.

Use `--kind task|probe|spec|incident` when an id is ambiguous across work-item
kinds. `--force` is reserved for future duplicate/lineage warning overrides;
it does not bypass live mutation safety.

### `millrace queue archive-blocked <TASK_ID> --reason "..."`

Archives a blocked task that should not be retried. This is the explicit
operator alternative to `queue retry-blocked` when the task is bad intake or no
longer valid work.

### `millrace queue supersede <OLD_TASK_ID> --replacement <NEW_TASK_ID>`

Retires a queued or blocked task because an existing queued, active, or done
replacement task carries the work forward. The old task moves to a
`superseded/` archive, the intervention record stores the replacement id, and
the runtime emits `task_superseded`.

Use `--cascade none|retarget|cancel` to choose dependent handling. The default
`none` reports queued dependents but leaves them untouched. `retarget` rewrites
queued dependent `depends_on` entries to the replacement task. `cancel` archives
queued dependents through the same cancellation helper.

### `millrace queue retarget-dependency <TASK_ID> --from <OLD> --to <NEW>`

Rewrites one queued task dependency to point from an old predecessor to an
existing queued, active, or done replacement task. This is the precise manual
path when `supersede --cascade retarget` would be too broad.

## Incident Commands

### `millrace incident resolve <INCIDENT_ID> --reason "..."`

Moves an incoming, active, or blocked incident to
`incidents/resolved/operator/` and emits `incident_resolved_by_operator`. Use
this when an operator has confirmed no more runtime work is needed.

### `millrace incident cancel <INCIDENT_ID> --reason "..."`

Cancels an incoming, active, or blocked incident without treating it as
successful planning work. The document moves into a matching `cancelled/`
archive directory and the runtime emits `incident_cancelled`.

### `millrace incident archive-invalid <FILENAME> --reason "..."`

Archives a single invalid file already present under `incidents/incoming/`.
The filename must be a single relative filename and either end with `.invalid`
or appear in `incidents/incoming/invalid-artifacts.jsonl`.

Operator intervention commands use the same control routing as pause/reload:
when no daemon owns the workspace they apply directly; when a daemon owns the
workspace they enqueue mailbox commands. A daemon applies them at the beginning
of a tick only when no active stage worker is currently mutating runtime state.

### `millrace queue repair-lineage --root-spec-id <ROOT_SPEC_ID>`

Previews safe queued/blocked work-document repairs when an open Arbiter closure
target has closure lineage drift. This is the supported recovery path when a
task/spec/incident is tied to the same root idea but has a mismatched
`Root-Spec-ID`.

Use `--apply` only while the daemon is stopped:

```bash
millrace queue repair-lineage --workspace <workspace> --root-spec-id <ROOT_SPEC_ID>
millrace queue repair-lineage --workspace <workspace> --root-spec-id <ROOT_SPEC_ID> --apply
```

Apply mode refuses a live daemon ownership lock or an active stage. It repairs
safe queued/blocked task lineage fields, writes a repair report under
`millrace-agents/arbiter/diagnostics/lineage-repairs/`, and emits a runtime
event.

## Control Commands

- `millrace control pause`
- `millrace control resume`
- `millrace control stop`
- `millrace control retry-active --reason "..."`
- `millrace control clear-stale-state --reason "..."`
- `millrace control reload-config`

Control routing behavior:

- If daemon owns the workspace: command is mailbox-routed.
- If no daemon owns the workspace: command applies directly.
Pause/resume behavior:

- `pause` adds the operator pause source.
- `resume` clears the operator pause source.
- `resume` does not bypass an active `usage_governance` pause source; the
  command reports that resume is blocked by usage governance until the active
  blocker clears or governance config changes.
- `reload-config` does not print a governance-specific cleared/remained
  summary. Governance changes are evaluated on the next runtime tick and are
  visible through `millrace status` and the basic daemon monitor.
- `reload-config` compiles the requested config immediately. If active runs
  exist, the active runs keep their launch-plan authority and the newly
  compiled plan is recorded as pending until active runs drain.
- unscoped `retry-active` is valid only when exactly one retryable active work
  item exists. If multiple planes are active, use a plane-scoped retry surface.
- `clear-stale-state` is the supported recovery command after an old
  closure-target invariant failure leaves an unrelated root spec half-claimed.
  It requeues active task, probe, spec, incident, and learning-request artifacts,
  clears `active_runs_by_plane`, and preserves the open closure target.

## Approval Commands

Execution capability approvals are runtime control actions for grants that
compile as `approval_required`.

### `millrace approvals ls`

Lists pending and resolved execution capability approvals for a workspace.

### `millrace approvals show <APPROVAL_ID>`

Prints the full approval object as JSON.

### `millrace approvals approve <APPROVAL_ID> --reason "..."`

Approves one pending capability grant. If a daemon owns the workspace, the
approval is mailbox-routed and applied at a safe runtime control boundary. If no
daemon owns the workspace, it applies directly.

### `millrace approvals deny <APPROVAL_ID> --reason "..."`

Denies one pending capability grant through the same direct/mailbox routing
surface.

## Planning Commands

### `millrace planning retry-active --reason "..."`

Requests a retry only when the active work is on the planning plane. If
execution or learning work is active instead, the runtime records a skipped
retry action rather than mutating the wrong plane. If Planning and Learning are
both active, the planning retry requeues only the Planning work item and leaves
the Learning lane active.

## Config Commands

### `millrace config show`

Prints the effective runtime defaults plus the snapshot-exposed config version
and last reload outcome/error state. The output includes
`auto_recovery.enabled`, `usage_governance.enabled`, and
`execution_capabilities.*` rollout flags.

Usage governance is configured under `[usage_governance]` in
`millrace-agents/millrace.toml`. It is default-off. When enabled, runtime token
rules are evaluated between stages and can automatically pause/resume the
workspace without changing the compiled plan. See
`docs/runtime/millrace-usage-governance.md` for the full config shape and
state artifacts.

Execution capability policy is configured under `[execution_capabilities]`.
It is enabled by default, with advisory grants allowed and strict
required-advisory failure disabled for the initial rollout. The default policy
denies raw network access, requires approval for package install and git mutate
capabilities, and allows shell run and workspace write capability requests.

Model aliases are configured under `[model_aliases.<alias>]` and selected under
`[model_assignment]`. `config show` prints `model_assignment.*`, each
`model_alias.<alias>`, and any loop or stage assignment overrides. Some shipped
modes, such as `efficient_learning_mixed`, also carry mode-local aliases; use
`compile show --mode <mode>` to inspect the resolved per-node assignments.

### `millrace config validate [--mode MODE_ID]`

Loads the effective config, compiles the selected mode, and prints compile diagnostics. This is the preferred operator-facing config validation command.

### `millrace config reload`

Requests a daemon-safe config reload. The runtime records reload failures in
snapshot state and runtime events. If recompile fails but the last-known-good
plan still matches current compile inputs, Millrace keeps that plan active. If
current compile inputs have drifted and the last-known-good plan is stale, the
reload is refused instead of continuing on the stale plan.

Usage-governance config is next-tick runtime state. A successful reload makes
new governance settings available to the next tick; `millrace status` and the
basic daemon monitor show whether a governance-owned pause cleared, remained,
or was newly applied.

Config changes that affect compile inputs, including `runtime.default_mode`
and `stages.<stage>.*`, `model_aliases.*`, or `model_assignment.*`, are
recompile changes. When a daemon owns the
workspace, `millrace config reload` is mailbox-routed. If no active runs exist,
the daemon can apply the new compiled plan on the next tick. If active runs
exist, those runs keep their launch-plan authority and the newly compiled plan
is recorded as pending until active runs drain. If the daemon was started with
an explicit `--mode`, that mode override remains pinned across reloads; start
without `--mode`, or with the intended mode, when config-driven mode selection
should take effect.

## Model Alias Commands

Model aliases let operators switch model/depth policy without editing every
stage. Defaults are `fast`, `standard`, and `deep`; the default assignment is
`standard`.

Commands:

- `millrace model-aliases list --workspace <workspace>`
- `millrace model-aliases show <alias> --workspace <workspace>`
- `millrace model-aliases set <alias> --model <model> --thinking-level <level> --workspace <workspace>`
- `millrace model-aliases remove <alias> --workspace <workspace>`
- `millrace model-aliases assign-global <alias> --workspace <workspace>`
- `millrace model-aliases assign-loop <loop_id> <alias> --workspace <workspace>`
- `millrace model-aliases assign-stage <stage> <alias> --workspace <workspace>`
- `millrace model-aliases clear-global --workspace <workspace>`
- `millrace model-aliases clear-loop <loop_id> --workspace <workspace>`
- `millrace model-aliases clear-stage <stage> --workspace <workspace>`

Mutation commands write `millrace-agents/millrace.toml` with TOML-preserving
edits and request `reload-config` by default. Pass `--no-reload` to edit only.
With an active daemon, reload is mailbox-routed; active runs keep their launch
compiled plan and future runs pick up the new alias policy when the pending
plan can safely become active. Unknown or invalid selected aliases warn at
compile time and fall back instead of blocking daemon startup. Millrace does
not statically verify provider support for a model id.

## Compile + Modes Commands

### `millrace compile validate [--mode MODE_ID]`

Compiles active mode and emits diagnostics (`ok`, warnings/errors,
last-known-good usage). Diagnostics now surface compile-input fingerprints:

- `compile_input.mode_id`
- `compile_input.config_fingerprint`
- `compile_input.assets_fingerprint`

### `millrace compile show [--mode MODE_ID]`

Compiles and prints operator inspectability surface:

- `compiled_plan_currentness`
- graph authority flags and graph entry surfaces
- graph node request-binding surfaces
- `compiled_plan_id`
- loop IDs by plane
- `baseline_manifest_id`
- `baseline_seed_package_version`
- `compile_input.*`
- `persisted_compile_input.*`
- frozen `completion_behavior.*` fields when the selected planning loop defines one
- stage ordering
- per-stage `thinking_level` when configured
- per-stage model-assignment alias provenance when configured
- per-stage execution capability grants and warnings
- terminal-state entries with `action`, `router_consequence`, `lifecycle_plan`,
  `lifecycle_action`, `writes_status`, `runtime_operation_id=none`, and
  `create_incident` details
- Codex compatibility `model_reasoning_effort` when configured
- entrypoint path per stage
- `stage_kind_id`
- `running_status_marker`
- `runner_name`, `model_name`, `timeout_seconds`
- `required_skills`, `attached_skills`

Entrypoint advisory model:

- `Required Stage-Core Skill`
- `Optional Secondary Skills`

Currentness interpretation:

- `current`: persisted compiled plan matches the current mode/config/assets fingerprint
- `stale`: persisted compiled plan exists but does not match current compile inputs
- `missing`: no persisted compiled plan exists yet

### `millrace compile graph [--mode MODE_ID]`

Compiles the selected mode and exports stable compiled-stage-graph contracts.
This is the legal topology surface: nodes, entry surfaces, transitions,
terminal states, runner/model/thinking bindings, and source refs derived from
the selected compiled plan.

Options:

- `--workspace PATH`
- `--mode MODE_ID`
- `--config PATH`
- `--plane [execution|planning|learning]`
- `--format [text|json]`
- `--output PATH`

Use this when an operator or outside agent needs to understand which stage
transitions are legal before inspecting any individual run. Use
`millrace runs trace <RUN_ID>` for what a concrete run actually did.

## Runtime / Compile Lifecycle Notes

- `millrace init` is the explicit workspace bootstrap step.
- `millrace compile validate` and `millrace compile show` both persist fresh compile diagnostics.
- `compile_if_needed` style runtime paths reuse the persisted compiled plan only when its compile-input fingerprint still matches current inputs.
- Runtime startup and `config reload` refuse to continue on a stale last-known-good plan when compile inputs have changed and recompilation fails.
- `usage_governance.*` and `auto_recovery.*` config fields apply on the next
  tick and do not require a recompile.
- `execution_capabilities.*` config fields are recompile boundaries because
  they change compiled grant decisions.

## Auto-Recovery Config

Blocked dependency auto-recovery is enabled by default and bounded. It only
acts when queued same-lineage execution tasks are stranded behind a blocked
predecessor whose latest blocked metadata classifies the failure as
`network_unavailable`, `provider_unavailable`, `provider_rate_limited`, or
`runner_timeout`.

Top-level fields:

- `auto_recovery.enabled`
- `auto_recovery.blocked_dependency_retry_enabled`
- `auto_recovery.max_auto_requeues_per_work_item`
- `auto_recovery.cooldown_seconds`

Default policy allows three automatic requeues per work item with cooldowns of
`300`, `900`, and `3600` seconds. Operators can still use
`millrace queue retry-blocked --force` after inspecting a blocked work item
that does not qualify for autonomous retry.

## Usage Governance Config

Usage governance is disabled by default. When enabled, Millrace evaluates usage
rules between stages and can pause the runtime with the `usage_governance`
pause source.

Top-level fields:

- `usage_governance.enabled`
- `usage_governance.auto_resume`
- `usage_governance.evaluation_boundary` (`between_stages`)
- `usage_governance.calendar_timezone`

Runtime token rules:

- `usage_governance.runtime_token_rules.enabled`
- `usage_governance.runtime_token_rules.rules`

Supported runtime token windows are `rolling_5h`, `calendar_week`,
`daemon_session`, and `per_run`. The supported metric is `total_tokens`.
Default enabled rules pause at `750000` total tokens over the rolling five-hour
window and `5000000` total tokens over the configured calendar week.

Subscription quota rules:

- `usage_governance.subscription_quota_rules.enabled`
- `usage_governance.subscription_quota_rules.provider`
- `usage_governance.subscription_quota_rules.degraded_policy`
- `usage_governance.subscription_quota_rules.refresh_interval_seconds`
- `usage_governance.subscription_quota_rules.rules`

The current subscription provider is `codex_chatgpt_oauth`, which reads
best-effort local Codex token-count telemetry. Subscription quota checks are
disabled by default and fail open by default when telemetry is unavailable.
Default subscription rules, when enabled, pause at 95 percent usage for the
`five_hour` and `weekly` windows.

### `millrace modes list`

Lists built-in modes and loop references. Current packaged modes are:

- `default_codex`
- `default_pi`
- `learning_codex`
- `efficient_learning_mixed`
- `learning_pi`
- `default_codex_integrated`
- `learning_codex_integrated`
- `blueprint_codex`
- `blueprint_learning_codex`

### `millrace modes show MODE_ID`

Prints one mode definition summary. Use this to confirm whether a mode selects
`execution.standard` or the quality-first `execution.with_integrator` loop.

## Skills Commands

The `millrace skills` command group manages the optional skill workflow and the
learning-plane skill-improvement surface.

### `millrace skills ls`

Lists installed workspace skills.

Options:

- `--workspace PATH`

### `millrace skills show <SKILL_ID>`

Prints one installed workspace skill's identity, path, and first markdown
heading when present.

Options:

- `--workspace PATH`

### `millrace skills search <QUERY>`

Searches installed workspace skill ids and skill markdown text.

Options:

- `--workspace PATH`

### `millrace skills install <SKILL_REF>`

Installs a local skill directory, local `SKILL.md` file, or supported remote
skill id into the selected skill target. Remote ids are resolved through the
public `tim-osterhus/millrace-skills` index and installed into the workspace as
normal local skills.

Options:

- `--workspace PATH`
- `--target [workspace|source]`
- `--force`
- `--update`

### `millrace skills refresh-remote-index`

Fetches the supported optional skill index from
`github.com/tim-osterhus/millrace-skills` and writes it to
`millrace-agents/skills/remote_skills_index.md`.
Operators can run this manually before `millrace skills install`; Librarian uses
the same supported remote index during learning-enabled post-Planner
preparation.

Options:

- `--workspace PATH`

### `millrace skills create <PROMPT>`

Queues a learning-plane request to create a new skill. The selected mode must
support the learning plane.

Options:

- `--workspace PATH`
- `--mode MODE_ID`
- `--foreground`

### `millrace skills improve <SKILL_ID>`

Queues a learning-plane request to improve an installed skill. The selected
mode must support the learning plane.

Options:

- `--workspace PATH`
- `--mode MODE_ID`
- `--foreground`

### `millrace skills promote <SKILL_ID>`

Copies a workspace skill into the source skill asset surface when running from
a source checkout.

Options:

- `--workspace PATH`

### `millrace skills export <SKILL_ID>`

Exports one installed workspace skill as a zip archive.

Options:

- `--workspace PATH`
- `--output PATH`

Command summary:

- `millrace skills ls`
- `millrace skills show <SKILL_ID>`
- `millrace skills search <QUERY>`
- `millrace skills install <SKILL_REF>`
- `millrace skills refresh-remote-index`
- `millrace skills create <PROMPT>`
- `millrace skills improve <SKILL_ID>`
- `millrace skills promote <SKILL_ID>`
- `millrace skills export <SKILL_ID>`

Create/improve workflows require a learning-enabled mode such as
`learning_codex`, `efficient_learning_mixed`, `learning_pi`,
`learning_codex_integrated`, or `blueprint_learning_codex` because they enqueue
learning requests for the Analyst/Professor/Curator skill-improvement path.
Install/list/show/search/refresh can be used for the deployed skill surface
without changing the active runtime mode, and Librarian uses the same remote
index/install surface after Planner in learning-enabled modes.

## Doctor Command

### `millrace doctor`

Runs workspace integrity diagnostics, including stale lock/ownership checks.
Doctor also reports `closure_lineage_drift` when an open closure target has
same-root queued/active/blocked work under a different effective root spec.
Doctor reports `duplicate_task_lifecycle_state` when the same task ID appears
in more than one task lifecycle directory (`queue`, `active`, `done`, or
`blocked`).
Doctor validates every compiled work-item family queue with the family-declared
document adapter where possible, including Blueprint draft queues and custom
JSON/markdown graph-owned families. It warns with
`daemon_stopped_with_open_graph_work` when daemon mode is stopped unexpectedly
while an open closure target still has graph-owned backlog or blockers; restart
is normally the supported recovery path for that warning.
Same-root Blueprint manifests are not unhealthy by themselves. Doctor and
operator inspection should flag unresolved draft-to-manifest references,
divergent duplicate `manifest_id` content, missing manifest draft ids, and
manifest/draft lineage mismatches rather than treating root-lineage reuse as an
error.
