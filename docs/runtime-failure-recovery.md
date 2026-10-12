# Selected recovery for runner timeouts

A runner invocation can fail before producing agent output. A workflow may
explicitly map a clean adapter timeout to its selected recovery route:

```json
"runtime_failure_mappings": [
  {
    "stage_kind_id": "execution.builder",
    "failure_kind": "timeout",
    "action_id": "execution.builder.runtime_failure"
  }
]
```

The entry belongs inside that stage's runner binding. IDs in this example are
illustrative; the selected workflow must declare the stage, outcome, recovery
route, target binding and recovery policy. The compiler does not add any of them.
A binding may map several of its own stages, but each stage/failure pair is
unique and canonically sorted. `timeout` is the supported failure kind.

Mapped bindings use runner-binding schema 5, or schema 6 when they also carry a
payload-capacity pin. Bindings without mappings retain schema 3/4 and their
existing canonical bytes and fingerprints. The store remains schema 11 and the
selected plan schema remains 18. Earlier readers refuse the new nested binding
versions rather than silently removing selected recovery authority.

The selected action must be a `recovery_route`, use an outcome declared by the
source stage, belong to exactly one selected recovery policy, and require no
agent artifact. A mapping cannot select a counter action whose component-owned
outcomes enable a runtime-owned non-recovery threshold redirect. Author a
separate explicit recovery action when that threshold behavior is also needed.

Millrace authenticates the run, session, current dispatch generation, session
fence, claim, plan, stage, graph node, binding, completion and diagnostic digest.
The failed session must have started, completed cleanup and have no cancellation
history or runner-result evidence. The bounded canonical diagnostic must echo
that exact authority and identify a timeout. Malformed or missing CAS bytes
fail closed. Non-timeout failures, cancelled work, lost sessions, failed cleanup,
unmapped failures and operator holds do not route automatically.

Recovery uses `workflow.apply_runner_runtime_failure`. It preserves the failed
completion and creates no `RunnerResultObserved`, runner observation, runner
result evidence or candidate artifact. The same selected recovery policy owns
immediate recovery, cooldown, return, successful reset and quarantine. Recovery
is bounded by those declarations; this mechanism adds no retry allowance or
stage timeout defaults.

Recovery may inherit the source work item's queue family even when the recovery
stage declares a different family for ordinary input. Timeout authentication
follows the exact accepted recovery route or consumed cooldown timer back to its
selected source, including command, claim and audit provenance. It does not
expand the recovery stage's ordinary input families or trust the attempt's
mutable latest-activation fields. Earlier applications remain replayable after
later recovery attempts update those fields.
The accepted input kind distinguishes an agent-produced recovery outcome from
a mechanical timeout when reconstructing the creating command. Both kinds can
create a selected cooldown and retain their exact receipt and audit proof.

If recovery itself fails, the policy records that failed recovery invocation as
the new source. A later `return_to_recorded_source` action returns to that exact
source, including its queue, graph node and runner. When the source is the
recovery stage, Millrace tracks the fresh returned activation and its next claim
as recovery work. It authenticates the return against the accepted historical
source and current tracking before dispatch and on reload. The graph node may
differ from the latest recurrence route's target. Further valid returns and
clean timeouts use the same selected policy and remaining attempt budget.
Returning to an ordinary source stage preserves its existing tracking. This
behavior does not invent a return to an earlier source or reset the budget.

Dispatch also authenticates inherited-input recovery targets through that
accepted route/timer/recorded-source provenance when they are outside the
stage's ordinary selected input targets. The current invocation must also
belong to exactly one authenticated selected attempt with matching work item, lineage,
run, activation, recovery stage and active phase. Missing, stale, foreign or
ambiguous current ownership is refused; historical replay proof remains
unchanged. This permits a legal returned recovery run to render its dispatch
at the exact recorded graph node; ordinary target guards remain in force.
A shared quarantine action resolves against the current run's
selected policy and attempt, preserving its existing phase, lineage and
activation checks.

Workflow diagnostic completion and recorded-source recovery are distinct
selected actions. For example, Plus execution's `TROUBLESHOOT_COMPLETE` report
uses its declared execution re-entry route, while `TROUBLESHOOT_RECOVERED`
returns to the latest recorded source, which may itself be Troubleshooter.
A workflow may author a QA handoff that preserves an existing carrier or
derives its task contract and trusted digest from the actual current task when
the carrier is absent. It must not invent a Checker baseline, weaken result
validation or reinterpret a rejected candidate. Active runs retain their exact
selected projection and asset authority.

Mechanical timeout applications consume the selected recovery policy rather
than counters for agent-produced terminal outcomes. They do not advance those
counters. Ordinary accepted runner outcomes retain their counter enforcement.

Once receipt, audit and recovery effects agree, the source run is settled and
releases its concurrency slot. Public status uses the kernel's public
`run_is_settled` query for the same authenticated settlement check: it excludes
the source from active runs and allows coherent
unclaimed recovery or returned work to appear ready. A terminal session alone
does not settle a run; pending, unmapped or cancelled failures keep their
existing logical status. Active-run status describes workflow claims, not
operating-system process liveness. A settled source cannot be dispatched or
explicitly retried.
The next recovery activation is ordinary selected work. Replaying the exact
application authenticates its durable effects and changes nothing. Pending
failures are reconciled before new dispatch after restart; pending cooldown
refusals retain the original completion without an application receipt until
policy allows progression. Draining does not create new recovery work.

The daemon counts `runtime_failure_recovery_applied` separately from accepted
agent output. A bounded successful invocation still returns native exit 0 and
JSON `ok: true`, with `data.stopped_reason: "max_ticks"` when that bound is reached.
This is a generic runtime contract, independent of Pi, LAD and evaluation
launchers. Pi's own bounded intrinsic retry is a separate adapter mechanism.

Deterministic tests qualify compiler, kernel, durable storage and CLI behavior.
Installed artifact tests and actual Linux/model qualification are separate gates.
