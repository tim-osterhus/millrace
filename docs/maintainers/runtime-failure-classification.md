# Terminal runner failure classification

The compiler freezes each stage/failure/action mapping. The kernel applies that
selected action only after exact current completion, claim, dispatch, fence,
cleanup and diagnostic authority is proven. Every applied mechanical failure
uses the existing recovery policy's finite attempts, cooldown and quarantine;
it does not consume agent-outcome counters or manufacture successful output.

| Terminal kind | Recovery classification | Required evidence or reason for refusal |
| --- | --- | --- |
| `timeout` | Selected recovery | Failed adapter timeout or authenticated runtime invocation deadline; complete/not_required cleanup, exact diagnostic. |
| `invocation_failed` | Selected recovery | Owned process/provider/tool/transport failure with safely settled completion. A proven preexternal start refusal may have no start timestamp and not_required cleanup. |
| `result_parse_failed` | Selected recovery | Invalid/missing JSON, final marker or structurally invalid response is still a failed adapter result. Strict parsing remains unchanged. |
| `output_too_large` | Selected recovery | Bounded terminal output failure after safe process settlement; no candidate accepted. |
| `cancelled` | Refuse as generic error | Operator work cancellation and daemon shutdown/global budget are intent, not a new repair allowance. Only an exact runtime invocation deadline with complete operation/cleanup proof is classified as timeout. |
| `missing_opt_in_config` | Refuse | Local configuration/consent absent before agent execution. |
| `unsupported_adapter_kind` | Refuse | Selected invocation cannot resolve through supported local adapter authority. |
| `input_too_large` | Refuse | Selected input-capacity refusal regardless of reporting time (normally predispatch); replaying a repair cannot enlarge selected capacity. |
| `redaction_refused` | Refuse | Redaction safety cannot be bypassed by recovery. |
| `selected_authority_refused` | Refuse | Selected component, effect, mapping, capability or dispatch authority is invalid. |
| `result_rejected` | Selected recovery | Completed authenticated transport candidate has an exact persisted semantic refusal and original immutable CAS evidence; use a distinct mechanical input. |

`result_rejected` admits only these candidate failures, after the original
validator has refused the candidate without accepting any of its effects:

| Candidate refusal | Meaning under authenticated selected authority |
| --- | --- |
| `undeclared_terminal_outcome` | Missing, unknown or illegal selected marker. |
| `recovery_action_context_missing` | Authentic ordinary invocation chooses an attempt-owned return/quarantine marker without an applicable attempt; the marker remains refused. |
| `invalid_artifact_payload` | Missing or schema-invalid candidate artifact. |
| `invalid_dynamic_route_target`, `invalid_route_projection` | Candidate target or projected route input violates the selected contract. |
| `closure_verdict_schema_invalid` | Candidate verdict violates the selected schema. |
| `closure_verdict_target_mismatch` | Candidate names a different closure target; the durable target remains valid. |
| `closure_root_contract_digest_mismatch`, `closure_freshness_anchor_mismatch` | Candidate disagrees with the exact rebuilt source snapshot. |
| `closure_rubric_mismatch` | Candidate changes the rubric from an authenticated prior verdict. |
| `closure_criterion_set_mismatch`, `closure_verdict_evidence_mismatch` | Candidate criteria or evidence references conflict with its selected verdict contract. |
| `closure_marker_status_mismatch` | Candidate statuses/evidence do not support its chosen terminal marker. |
| `invalid_fanout_payload`, `invalid_remediation_payload` | Candidate-derived child payload fails its selected schema before any child, artifact or observation is accepted. |
| `counter_threshold_requires_escalation`, `counter_threshold_not_reached` | Candidate chooses an outcome inconsistent with the existing counter; the refused candidate changes no counter. |

The original candidate remains invalid. Recovery authentication reuses the exact
refusal chain; it never invents a successful marker or artifact. The broad
`closure_target_mismatch` reason still denotes a durable target/selected graph
mismatch and stays fatal. `missing_remediation_payload_schema` denotes absent
selected authority and also stays fatal. Missing prior verdict evidence,
invalid prior rubric authority, stale snapshots and missing or ambiguous
evaluator provenance cannot select candidate recovery.
Recovery context refusals for stale, ambiguous, foreign/ownership or invalid
phase authority likewise remain fatal; only the clean missing-context candidate
case enters the selected `result_rejected` route.

A returned evaluator retains its original closure target, source snapshot and
selected evaluation contract. The runtime follows authenticated historical
recorded-source returns to the original evaluation, including repeated recovery;
it does not read a mutable latest-source pointer or infer evaluation authority
from a stage name. Missing, duplicate or foreign return/creator/evaluation links
refuse validation and durable load. Ordinary work that is not an evaluator
continues through its ordinary selected actions. Accepted evaluator creation
requires exactly one durable evaluation record. Valid returned pass/gap/blocked
outcomes retain their normal closure/remediation/block semantics.

A closure evaluator run may also fail mechanically. Its `timeout`, or another
eligible selected mechanical failure such as a recoverable `result_rejected`,
selects the declared recovery route even when the evaluator's queue, stage, node
and binding tuple is not an ordinary selected route target. Authority comes only
from the claimed original evaluator activation that matches one accepted
evaluation creator and its single durable evaluation record; the static tuple
alone grants nothing. Forged, duplicate, drifted or non-original provenance is
refused before application and on durable load. A returned pass after the target
has closed resolves its evaluation through the same authenticated return chain,
so it remains a lawful closed-target outcome.

Closure-evaluator runtime-failure authority is authenticated from immutable
recorded history before routing to recovery: one accepted
EvaluateCompletionBehavior creator with one paired governance event and trace,
owned by evaluator work; the target authenticated through its own
OpenClosureTarget creator, paired audit and immutable root work item; and the
sealed snapshot identifies that target and matches the immutable root contract,
any prior verdict and the freshness anchor, with no other evaluation of the
target sharing that anchor. Current target status or readiness is never
re-demanded, so the origin survives closure and reload. The same check runs on
durable load. Duplicated, missing, foreign-owned or drifted provenance is
refused as runtime_failure_authority_mismatch with no failure applied.

Sealed closure evidence is authenticated against the same immutable history. The
evaluator's sealed prior verdict and ordered evidence artifacts must equal a
recomputation of the selected capture rule over accepted history strictly before
the evaluation's accepted EvaluateCompletionBehavior creator transition. The
prior verdict and every evidence artifact must each pass the same canonical
artifact-provenance check: accepted receipt, transition and observation identity
with payload digest; selected source run, work, activation and plan links; a
paired accepted governance event and trace naming the action; and the selected
action's artifact schema, payload and projection. Creator audit counts exactly one accepted governance event and
one accepted trace per creator input id; refused replays of that input stay
lawful. Later lawful history is ignored and current readiness is never
consulted. A mismatch is refused as runtime_failure_authority_mismatch with no
failure applied.

A GAP verdict from a recovered closure evaluator is a lawful prior verdict for the
next closure evaluation when the verdict run reaches the original evaluation
through the authenticated recorded-source return chain. Kernel snapshot capture
and the sealed prior-verdict check both use `closure_verdict_run_refusal`: the run
must be the original claimed evaluator run or return to it, and its work, plan,
stage and runner must match the evaluation and behavior. Matching work, plan or
lineage alone never grants that authority, and the check never consults current
readiness or a latest-source pointer. Refusals keep
`closure_prior_verdict_provenance_mismatch` at capture and
`closure_snapshot_authority_mismatch` for a sealed evaluator origin.

Run/claim/session/plan/fence identity mismatches, invalid selected declarations,
closed work, quarantine, operator waits and holds, stale closure snapshots,
missing authoritative source records and other state-integrity refusals do not
select semantic recovery. Context writeback refusal preserves its safety exit.
A pending selected cooldown waits; it does not create another observation or
mechanical receipt before eligibility. Unknown errors never become implicit
recovery authority.

Unclassified start exceptions, indeterminate starts, lost live handles,
contradictory adapter echoes, malformed cancellation outcomes and cleanup
uncertainty require supported reconciliation or operator intervention. A raw
Python exception does not prove that external work stopped. Polling/control
exceptions recorded as `runtime_failure` cancellation therefore retain their
reconciliation boundary even if a later terminal string says cleanup completed.
No blanket exception handler grants an agent authority to repair those states.

A runtime invocation deadline needs one primary runtime request, accepted exact
request provenance, contiguous ordered cancellation operations, one final
successful transport cleanup, exact operation receipts/transitions and bounded
canonical cleanup CAS diagnostics. Any second request, wrong owner, missing
operation or mismatched digest refuses settlement and replay. Remaining campaign
wall bounds use the existing daemon_shutdown request before this classification.
Draining defers a new application, and invocation budgets still admit each
recovery runner start. Policy exhaustion creates declared quarantine; it adds
no invocation or campaign budget.

The generic runtime never names Troubleshooter, Mechanic or a plane as its
fallback. Official workflow packages own historical stage coverage and recovery
targets. Agent-declared BLOCKED/FIX_NEEDED routes and their escalation thresholds
continue through ordinary selected terminal actions. Mechanical failures use
the package's explicit no-artifact route and selected finite policy. Learning
workflows with no selected recovery declaration receive no invented target.

Failure handoff is `governance_context.runtime_failure_source`, derived from the
current authenticated recovery owner and recorded source application. It carries
policy/attempt, source run/session/stage/node/binding, failure kind, original
refusal and immutable diagnostic/evidence references. Invocation preparation
adds the bounded already-redacted diagnostic only after full native dispatch
and CAS authentication. This is evidence for diagnosis, never workflow authority.
