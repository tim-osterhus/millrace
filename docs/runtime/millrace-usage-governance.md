# Millrace Usage Governance

Usage governance is a runtime-owned pause/resume control surface for bounding
agent usage while long-running work continues across ticks. It is intentionally
default-off: existing workspaces keep the same execution behavior unless an
operator enables `[usage_governance]` in `millrace-agents/millrace.toml`.

## Implementation Status

The v1 usage-governance surface is implemented and shipped. The core runtime
feature landed in `0.15.4`; the follow-on documentation, entrypoint,
asset-policy, and typing coverage landed in `0.15.5`.

This implementation targets Millrace's current one-stage-at-a-time scheduler.
Governance is still written as a workspace-global runtime gate: it checks before
stage dispatch and after stage-result persistence, lets an already-running
stage finish, and prevents later stage launches after a governance pause has
been durably recorded.

## Runtime Contract

Usage governance evaluates only at runtime-owned boundaries. The shipped
boundary is `between_stages`, which means Millrace never interrupts an active
runner process. It checks governance before dispatching a stage and again after
the stage result has been persisted and routed.

When a rule blocks execution, the runtime adds the `usage_governance` pause
source to `runtime_snapshot.json`. This pause source is independent from the
operator pause source:

- `millrace control pause` adds `operator`.
- governance blockers add `usage_governance`.
- `millrace control resume` removes only `operator`.
- an active governance blocker keeps the workspace paused until the blocker
  clears, or until governance is disabled.

This lets manual pause intent and automatic quota protection coexist without
one control path erasing the other.

## Configuration

Minimal opt-in config:

```toml
[usage_governance]
enabled = true
```

That enables the documented runtime token defaults:

- `rolling-5h-default`: pause when rolling five-hour total tokens reach
  `750000`.
- `calendar-week-default`: pause when calendar-week total tokens reach
  `5000000`.

Full config shape:

```toml
[usage_governance]
enabled = true
auto_resume = true
evaluation_boundary = "between_stages"
calendar_timezone = "UTC"

[usage_governance.runtime_token_rules]
enabled = true

[[usage_governance.runtime_token_rules.rules]]
rule_id = "rolling-5h-default"
window = "rolling_5h"
metric = "total_tokens"
threshold = 750000

[[usage_governance.runtime_token_rules.rules]]
rule_id = "calendar-week-default"
window = "calendar_week"
metric = "total_tokens"
threshold = 5000000

[usage_governance.subscription_quota_rules]
enabled = false
provider = "codex_chatgpt_oauth"
degraded_policy = "fail_open"
refresh_interval_seconds = 60

[[usage_governance.subscription_quota_rules.rules]]
rule_id = "codex-five-hour-default"
window = "five_hour"
pause_at_percent_used = 95

[[usage_governance.subscription_quota_rules.rules]]
rule_id = "codex-weekly-default"
window = "weekly"
pause_at_percent_used = 95
```

Supported runtime token windows:

- `rolling_5h`
- `calendar_week`
- `daemon_session`
- `per_run`

The only supported runtime token metric today is `total_tokens`.

Supported subscription quota windows:

- `five_hour`
- `weekly`

Subscription quota checks are also default-off. The built-in
`codex_chatgpt_oauth` adapter is a best-effort local telemetry reader; it scans
recent Codex session JSONL files under `CODEX_HOME` or `~/.codex` for
`token_count` rate-limit payloads. It does not make a network request. If local
telemetry is unavailable, the subscription status is `degraded`. With the
default `fail_open` degraded policy, degradation is reported but does not pause
the runtime. With `fail_closed`, degradation becomes a non-auto-resumable
governance blocker.

All usage-governance config fields are next-tick apply fields. They do not
force a compiled-plan rebuild because governance is a runtime control surface,
not graph authority.

## Durable Artifacts

Usage governance persists two runtime-owned files:

- `millrace-agents/state/usage_governance_state.json`
- `millrace-agents/state/usage_governance_ledger.jsonl`

`usage_governance_state.json` records whether governance is enabled, the active
blockers, whether governance currently owns a pause source, subscription quota
telemetry status, and the next auto-resume candidate when one can be computed.

`usage_governance_ledger.jsonl` records counted stage-result token usage. The
dedupe key is the persisted stage-result artifact path, so a stage result is
counted once. On later ticks or after restart, Millrace reconciles missing
ledger entries from persisted stage results before evaluating rules.

## Operator Surfaces

`millrace status` and `millrace status show` include:

- `pause_sources`
- `usage_governance_enabled`
- `usage_governance_paused`
- `usage_governance_blocker_count`
- `usage_governance_auto_resume_possible`
- `usage_governance_next_auto_resume_at`
- `usage_governance_subscription_status`
- `usage_governance_subscription_detail`
- one `usage_governance_blocker` line per active blocker

`millrace run daemon --monitor basic` renders live governance events:

- `governance pause ...`
- `governance resume ...`
- `governance degraded ...`

These lines are live monitor output only. The runtime still persists the
underlying state, ledger, snapshot, and runtime events as the durable record.

## Auto-Resume Semantics

When `auto_resume = true`, Millrace removes the `usage_governance` pause source
after all active blockers clear. Rolling five-hour and calendar-week runtime
token blockers can compute a future auto-resume time. Subscription quota
blockers can auto-resume when telemetry includes a reset timestamp.

Some blockers are not auto-resumable. For example, `daemon_session` and
`per_run` runtime windows do not have a natural future clearing time, and
subscription telemetry degradation with `fail_closed` is treated as manually
actionable until telemetry recovers or config changes.

## Config Reload Behavior

Usage-governance config changes apply through the normal config reload path and
are evaluated on the next runtime tick. `millrace config reload` and
`millrace control reload-config` report reload routing and compile status; they
do not print a dedicated governance-cleared/governance-still-blocked summary.

After reload, use `millrace status` or the basic daemon monitor to see whether
governance is enabled, whether `usage_governance` still owns a pause source,
which blockers remain active, and whether auto-resume is possible. Disabling
governance clears a governance-owned pause on the next tick. Loosening or
tightening thresholds takes effect at the same next-tick evaluation boundary.
