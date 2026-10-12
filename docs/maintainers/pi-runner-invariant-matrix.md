# Private Pi implementation invariant evidence

PI-I01, version 0.22.4.dev3+pi.b01. This matrix describes implementation evidence; independent Manager review and Q01 installed qualification remain separate. Exact execution logs and frozen hashes live in results/PI-B01/PI-I01.

| Row | Source enforcement | Executed evidence and limit |
| --- | --- | --- |
| PI-SEL | compiler/runner_bindings.py exact kind, component-free authority and all three effects; adapters/cli/run.py repeats the predicate before claim/request construction; run_controls.py only adds eligible unstarted scheduling | test_pi_runner_bindings.py removes, denies, requires approval for, and duplicates each effect. test_cli_pi_execution.py repeats authored/runtime parity, component refusal, pause/resume/replay. Existing native/Codex diagnostic tests remain green. |
| PI-CFG | pi_rpc_config.py fixed schemas, canonical bounded reads, pinned source identities/full inventory, immutable templates, fresh exclusive attempt directories, closed environment and prespawn drift checks | test_pi_rpc_config.py hostile JSON/schema/path/size, overlapping attempts, copy/original drift, partial cleanup, replacement identity and environment injection. Synthetic fixture source identities are monkeypatched only in tests; separate pinned-config-readonly log verifies actual accepted package. Actual startup/settings qualification remains Q01. |
| PI-PROMPT | pi_rpc.py prompt_for_request exact selected asset/schema sets and authenticated dispatch projection | test_prompt_projects_only_exact_selected_assets_schemas_and_navigation and opaque kernel-ping coordinator fixture. No workflow-ID branch or ambient file-body search. |
| PI-RESULT | pi_rpc_client.py strict final three-key JSON and optional string report; existing runner evidence/kernel acceptance | test_pi_rpc_client.py hostile envelope/report bounds; test_real_pi_adapter_fake_wire_accepts_artifact_once_and_reloads covers legal artifact, invalid artifact, unknown route and report type. Durable accepted artifact/CAS and next legal stage read back; repeats do not add observations. |
| PI-FENCE | Pi builds DispatchEcho from request; shared runner_contract validates it and coordinator persists lifecycle | test_pi_candidate_cannot_cross_request_fences changes ten identity fields after a legal result. Existing runner session validation/persistence and bounded CLI suites test durable corruption/restart; Pi fake-wire test reloads and replays. |
| PI-CONTEXT | CLI extends existing bound-cwd predicate to Pi; existing capture/hydration/writeback ownership remains unchanged | Existing test_cli_bounded_execution_unit.py, context adapter/contracts/compiler and runner_session_context suites cover bound mismatch/drift/protected/unreported writes and authenticated navigation. Pi-specific real tool context qualification is reserved for Q01; no new context or containment semantics. |
| PI-WIRE | pi_rpc_client.py bounded readers/queue/nonblocking writes, correlated IDs, full base lifecycle before text-tool discriminator, one prompt and settled boundary | Fake subprocess tests exercise readiness/interleaving, closure, malformed UTF-8/JSONL, partial lines, stderr/stream bounds and deadlines. Lifecycle tests corrupt roles, snapshots, IDs and completion ordering; retry parses but remains sticky uncertainty; last failure cannot reuse prior text. Accepted P01 real text/bash traces replay against this parser. |
| PI-CANCEL | PiSession bounded cancel/escalation/closure; actual parent reap, EOF, reader join, pipe close, identity-owned materialization removal; existing orphan-risk application gate | Ignored abort escalation test records timed_out then parent termination, bounded worker completion, orphan_risk and idempotence. Text-tools positive and bash/image/error/unknown paths remain sticky. Shared lifecycle tests cover coordinator races/orphan persistence. Reconcile explicitly returns Unsupported, including PID metadata. Parent signaling makes no descendant claim. |
| PI-USAGE | exact integer input+cacheRead+cacheWrite/output/total mapping, complete event fallback without duplicate snapshots, final stats correlation | Cache/coercion/missing/zero/overflow tests. Mapping capability is only exposed for the accepted loopback-scripted profile; production model accounting remains unqualified. Existing budget tests preserve unavailable usage refusal. Optional attribution fields are not synthesized. |
| PI-REGRESS | unchanged durable schemas and default/native/Codex code paths; static private version and empty runtime dependencies | Applicable Core regression selection, Ruff and strict mypy; direct pinned uv-build wheel/sdist, sdist rebuild and isolated ordinary wheel install/import proof. Exact Git-dependent exclusions and inherited omissions remain explicit; no claim that every Core test ran. |
| PI-ASSETS | Plus owner A01 | Out of this writer's scope; no Plus edits. |
| PI-INSTALLED | Q01 shared three-arm qualification | Not run in PI-I01. Deterministic fake process tests and P01 replay are not current installed Pi or production-model qualification. |

The successful cleanup scope is text read/edit/write/content verification only, after coherent complete base protocol and actual owned-resource closure. Bash, image, error, retry/compaction, aborted, incomplete and unknown branches retain orphan_risk. Provider shutdown is experiment cleanup, not a production requirement. No native pause/resume/reattach or filesystem rollback is claimed.

Scope-hygiene deviation is retained in implementation/tests/resource reports: early default pytest temp roots and one support bytecode file were removed only where attributable; absent prior inventories leave original-byte and automatic-retention uncertainty unresolved.


## Targeted repair 1

R01 adds nonblocking regular-file descriptor acquisition, pre-decode 64 KiB shared
config enforcement and an absolute startup deadline. New tests cover a reaped
FIFO watchdog through both entry points, existing device refusal, replacement
race, profile/template/manifest size bounds, deep/invalid data, and valid Pi.
Codex and Millforge config tests cover below-bound and exact-bound success,
resolved symlinks, retained duplicate-key behavior and typed over-bound refusal.
The newly enforced shared-file limit intentionally refuses previously accepted
oversized legacy files; Manager's bound interpretation requires independent
re-review and does not establish arbitrary-size compatibility.

R02 tests fail disposal with live parent, then reaped parent/unclosed resources,
and later established disposal. Materialization remains until closure; retry
is not suppressed by a cached unresolved receipt. Failed-start handles are
retained for bounded retry where available. Constructor no-spawn refusal differs
from ambiguity. Already-absent cleanup is idempotent; existing ownership-replacement,
sibling preservation and positive text closure tests remain mandatory.


## Q01 public readiness correction

The classifier now calls existing `_require_pi_selected_authority` only for exact
Pi bindings, then the existing config/preclaim predicate. Every other adapter
retains its pin prerequisite. Tests cover legal component-free Pi, invalid kind,
missing/drifted config or credential, denied/missing grants, component-free Codex,
lost/running/orphan sessions and unsupported started-session scheduling pause.
The separate completion-capacity test still requires `missing_runner_component_pin`
for the parked I01-R03 case. Public macOS proof must retain ACTIVE_LIFECYCLE and
use a supported short endpoint; internal execution is not public-command proof.

## Component-free completion capacity checks

`test_runner_payload_capacity.py` covers installed descriptor trust and exact pin
shape. `test_completion_capacity.py` covers compiler/export/CAS, v3/v4 and exact
context-bound types. Closure persistence/queue tests cover hostile reconstruction,
pass/gap/blocked, reload and accepted replay. Pi adapter tests cover canonical
payload C/C+1, projection/descriptor drift and the independent full-prompt bound.
CLI fake-wire tests exercise both generic v3 and selected v4 request construction.
These are source/fixture checks; installed full LAD and artifact resource presence
remain qualification gates, and foreground cleanup claims are unchanged.

## Explicit v2 group accounting

This supersedes the earlier text-only success scope only for the explicitly
selected `pi-rpc-profile.v2`. Legacy v1 behavior is unchanged. The trusted-workspace
scope excludes descendants that escape the observed command group.

| Boundary | Positive rule | Refusal / aftermath | Source tests |
| --- | --- | --- | --- |
| Profile | Exact packaged observer, actual bundled source and existing Pi/Node pins; explicit preload/private inherited pipe | Wrong/arbitrary observer, changed bundle, platform or profile refuses; no v1 fallback | `test_v2_profile_is_explicit_and_requires_qualified_installed_bridge` |
| Call facts | Ordered one-to-one raw RPC hashes, exact args/cwd/environment/options, unique spawn and matched lifecycle | Missing/duplicate/stale/unbound/ambiguous facts invalidate observation | `test_pi_rpc_groups.py`; packaged observer tests |
| Failed checks | Real numeric nonzero, no control cause, both genuine ends, independently absent group | Signal, timeout/abort, unknown control, survivor or premature destroy remains sticky across later success | Group witness negatives; task actual-Pi hook and product-client smoke |
| Channel | Strict per-kind records, bounded bytes/count/lines, complete hello/bye and joined reader | EPIPE, death, exhaustion, partial tail or corruption never qualifies | `test_channel_failure_never_has_complete_positive_tail`; descriptor failure regression |
| Disposal | Complete trusted facts, retired groups, reaped Pi, three joined readers, removed owned attempt | Missing identity/channel remains orphan-risk; no guessed group signal; cleanup retry cannot clear execution uncertainty | `test_survivor_cleanup_retry_never_clears_uncertainty_or_signals`; `test_group_dispose_drains_exit_witness_without_claiming_execution` |

Cleanup completion is a resource statement, not successful execution. V2 refuses
candidate success before parsing/applying an uncertain final result, but can report
complete disposal after a failed/cancelled invocation when independently proved.
Installed stage acceptance/reload/replay, cancellation and full coding LAD remain
separate final-pair qualification gates; this matrix does not mark them passed.

## Opt-in Linux v3 owned disposal

`pi-rpc-profile.v3` retains v2's Pi/Node/bridge identities and Bash observer
contract. Its extra exact-path/SHA-256 Python supervisor is a Linux subreaper;
v1/v2 and Darwin behavior are unchanged. The supervisor is the sole reaper for
its inner Pi and adopted local descendants. It signals only unreaped direct
children through pidfds, then requires `ECHILD` before reporting disposal. Its
private control and report descriptors are closed before Pi exec.

| Boundary | Positive rule | Refusal / aftermath | Source tests |
| --- | --- | --- | --- |
| Admission | Explicit Linux v3 profile with pinned helper and bridge identities | Wrong host, path, hash, schema, or unsupported platform refuses | `test_linux_v3_supervisor_identity_and_platform_are_explicit`; config suite |
| Transcript | Ordered ready report identifies inner Pi PID for observer hello; one complete report agrees with helper exit | Missing, duplicate, out-of-order, malformed, or contradictory report refuses; helper death never proves disposal | `test_pi_rpc_supervision.py`; real bridge tests |
| Ownership | Adopted Bash service is signalled as a live direct child and reaped; unrelated process remains live | No saved PGID signal, reused PID, stale group inference, or unbounded wait | `test_pi_rpc_supervisor.py`; `test_supervised_observer_accepts_service_after_owned_disposal` |
| Execution | Known-present group may be deferred until supervisor disposal; all other observer, command, hash, lifecycle, and result checks still apply | Unknown group probe, root signal, control, abort, invalid/missing observer facts, or nonzero Pi exit cannot qualify output | `test_pi_rpc_groups.py`; `test_supervisor_forced_pi_kill_proves_disposal_without_observer_bye` |
| Cleanup | Valid report with zero remaining children can prove resource disposal even after forced Pi kill; zero-spawn sessions also require it | Failed report remains orphan risk; cleanup success never accepts cancelled output | `test_pi_rpc_supervision.py`; `test_pi_rpc_groups.py`; forced-kill bridge test |

The helper's finite TERM/KILL and reaping interval runs after the invocation
work deadline without permitting another model or tool turn. This is an owned
local process-lifetime proof, not a cgroup containment guarantee. Remote work,
external effects, descendants that leave the subreaper's ancestry, and hostile
same-user interference remain outside scope. Installed stock Pi and real-model
qualification are separate acceptance evidence; this matrix records source
invariants only.

## Linux CPU qualification

The explicit v2 platform may be `linux` only when it matches the host and the
qualified Linux Node 24.18.0 binary, Pi 0.87.0 shrinkwrap and complete package
inventory hashes match their Linux pins. The bridge and Pi bundle retain their
independent exact hashes. Darwin's CoreFoundation environment entry is expected
only on Darwin. A malformed, wrong-host or unsupported platform refuses through
the public config boundary as `ValueError`.

`test_linux_v2_profile_selects_linux_witness_and_rejects_wrong_host`,
`test_linux_uses_its_own_installed_node_and_package_pins`,
`test_v2_profile_refuses_malformed_or_unqualified_platform_as_config_error`, and
`test_linux_hello_uses_linux_identity_without_darwin_environment` cover the new
profile boundary. The installed Linux adapter suite passes 77 tests, including
the real preload and numeric nonzero shell witness. Task evidence is under
`lab/tasks/local-inference/2026-09-22-pi-linux-qualification/`.

An ordinary installed private Core/Plus wheel pair on the pinned Ubuntu 24.04
x86_64 CPU host accepted the five-stage scripted-provider LAD route. The saved
`linux-final-lad` case records actual Builder test exit 1, edit, identical test
exit 0, Checker exit 0, five applied artifacts, completed cleanup, closure and
public enqueue replay. The case exercises the real stock Pi/Node process and
localhost provider, not a real model. The other twelve selected LAD stages,
historical transport and three-arm comparison remain separate gates.

## October result-failure diagnostics repair

Source-only prospective repair, October 4, 2026. The closed C campaign's sixth
final response is unavailable; these synthetic cases cannot establish its exact
trigger. Installed private packet and model qualification remain separate.

| Authority field/record | Legal state/sequence | Illegal/corrupt state | Compiler rule | Admission/runtime rule | Durable/projection rule | Test proof |
| --- | --- | --- | --- | --- | --- | --- |
| Pi final candidate | Settled client returns a legal envelope; existing successful artifact path remains valid | Malformed JSON, duplicate/wrong/extra envelope, blank marker, payload/report type, nonfinite/nested JSON | N/A: runtime candidate data, not authored workflow authority | Same strict parser refuses; fixed phase/reason codes record the boundary | Existing error CAS stores diagnostic only; no result evidence, artifacts, observations, or routes | `test_pi_rpc_result_diagnostics.py::test_rejected_candidate_retains_safe_parse_reason`; `test_cli_pi_execution.py::test_real_pi_adapter_fake_wire_accepts_artifact_once_and_reloads` |
| Normalized evidence | Legal report remains redacted and candidate success remains unchanged | Redacted candidate mapping-key collision; float unsupported by authority normalization; unsafe injected exception contents | N/A: runner output normalization | `result_parse_failed` retained; phase is evidence normalization; arbitrary exception text is discarded | Final text remains a string diagnostic, never a payload mapping | `test_pi_rpc_result_diagnostics.py::test_redaction_key_collision_retains_text_without_accepting_candidate`, `test_json_float_evidence_is_rejected_without_losing_failure_phase`, `test_normalization_exception_content_is_never_retained` |
| Diagnostic secrecy and size | Request/config/materialized credential redacted before bounded text retention | Escaped credential, environment drift, unsafe strings/UTF-8, expansion, colliding diagnostic keys, large escaped/unicode excerpts | N/A: diagnostics add no compiled authority | Decode string literals for redaction; 4096 serialized-byte excerpt; unsafe/result-bound text omitted; key collision uses content-free list fallback | Existing full diagnostic bound remains 16 KiB | `test_pi_rpc_result_diagnostics.py` credential, encoding, bounds, and key-collision tests; large variants of `test_cli_pi_result_diagnostics.py::test_failed_pi_diagnostic_survives_disposal_reload_and_refuses_foreign_binding` |
| Session completion diagnostic binding | Rejection -> owned attempt removed -> completion CAS -> reload -> ordinary replay stays refused | Each of run/session IDs, generation/session generation, session/run fences, plan, claim, stage, graph node, or binding changed | N/A: existing runtime session identity | Existing dispatch echo authenticates error result | Existing typed diagnostic and projection enforce every identity; foreign diagnostic is corrupt and suppressed | `test_cli_pi_result_diagnostics.py::test_failed_pi_diagnostic_survives_disposal_reload_and_refuses_foreign_binding` mutates all eleven bound fields |
| Cleanup and cancellation | Complete disposal records failed/error; orphan-risk disposal records lost; attempt cleanup can repeat | Protocol failure, cancellation, incomplete disposal | N/A: existing resource lifecycle contract | Diagnostic capture adds no cleanup proof, retry, repair, resubmit, or candidate acceptance | Error evidence retained for both cleanup dispositions; existing cancellation/error categories unchanged | `test_pi_rpc_result_diagnostics.py::test_protocol_and_cancellation_failures_do_not_claim_candidate_failure`, `test_failed_candidate_survives_idempotent_attempt_cleanup`; durable test clean/orphan variants; existing `test_pi_rpc_adapter.py` lifecycle tests |

## Native invocation diagnostics

`test_pi_rpc_invocation_diagnostics.py` covers allowlisted protocol reasons,
content-free fallback, errno-only OS errors, and cleanup survival. The installed
CAS lifecycle test also covers invocation failures after disposal and reload,
including foreign-binding refusal. No result acceptance or retry is added.

The fixed uncertainty projection now includes all process-observer refusal
codes. C21 retained four distinct uncertainties but exposed only two codes;
its raw tool and observer events are unavailable, so the two omitted reasons
and exact invocation trigger remain unknown. This is an observability repair.

| Authority field/record | Legal state/sequence | Illegal/corrupt state | Compiler rule | Admission/runtime rule | Durable/projection rule | Test proof |
| --- | --- | --- | --- | --- | --- | --- |
| Native uncertainty diagnostic | Fixed group refusal -> content-free diagnostic -> owned cleanup; known codes and unclassified count account for all distinct reasons | Arbitrary observer text or an omitted fixed group code | N/A: diagnostic facts, authored authority unchanged | Every existing tool, process, settlement, cancellation and deadline refusal remains unchanged | Existing bound diagnostic retains only fixed codes and counts; no result, artifact or route authority | `tests/adapters/test_pi_rpc_builder_tool_contract.py::test_group_failure_diagnostic_exposes_only_its_fixed_code`, `test_builder_diagnostic_counts_known_and_unclassified_failures_separately`; existing bound CAS/reload and foreign-binding tests |

## Pi 0.87 recovery boundary repair

### Narrow recovered-result source preparation

The admitted supervised Linux profile now has a private bounded recovery ledger.
This is independently reviewed source preparation; it is not installed positive
or model/campaign qualification. A4 proved the negative threshold/retry gap. A7
validated native edit/failed-target/active-branch/system/RPC-observer bindings
against the prior artifact. A newly built artifact, native positive result
application/CAS/reload/replay, hostile refusal and cancellation/deadline controls
remain pending. C02's historical
trigger remains unknown. No compiler, selected-plan, candidate or durable schema
changed. V1/V2 remain sticky, and disposal is separate from result eligibility.

| Authority field/record | Legal state/sequence and aftermath | Illegal/corrupt state | Compiler rule | Admission/runtime rule | Durable/projection rule | Test proof |
| --- | --- | --- | --- | --- | --- | --- |
| Recovery capability | Existing admitted Linux supervisor; ordinary execution unchanged | Legacy or non-Linux profile | N/A: existing profile pin admission | Capability derived internally; no config/workflow/provider override | No durable policy switch | `test_pi_rpc_recovery_acceptance.py::test_capability_is_derived_only_from_existing_linux_supervisor`; `test_legacy_recovery_retains_original_labels_and_result_refusal` |
| Threshold receipt | Completed tool turn and same-run successful resumed turn, or completed final stop run and unchanged final assistant through settlement; paired successful typed stock result and persisted compaction binding | Failure/abort, empty/corrupt result, foreign retained-entry link, missing/changed copied system or rebased timestamp, unsupported compaction, absent aftermath/binding | N/A: native wire evidence | Successful receipt, exact aftermath and original-budget query required; actual summary usage included | No candidate authority from receipt | `test_threshold_qualified_only_after_same_run_successful_tool_aftermath`; `test_post_final_threshold_qualifies_only_after_binding_and_settlement`; `test_post_final_threshold_never_relaxes_result_binding_or_closure`; `test_threshold_requires_exact_typed_result_and_persisted_compaction`; `test_compaction_copies_observed_system_and_exact_entry_epoch`; `test_failed_threshold_is_sticky_after_later_valid_text`; existing `test_pi_rpc_recovery.py` ordering probes |
| Retry provenance | Failed run, progressive attempts, exact null omission, successful assistant/retry end, complete tool/turn aftermath | Missing/duplicate/foreign edit, bad target/entry/message digest, failed/aborted/length cycle | N/A: native wire evidence | Each failed assistant covered once by its owning successful cycle | Raw transcript discarded; hash/count diagnosis only | `test_retry_requires_exact_persisted_omission_and_complete_aftermath`; `test_each_later_failure_needs_its_own_successful_cycle`; `test_context_edit_corruption_refuses_forever` |
| Read-only transcript | Exact initial model/thinking metadata, system/user/assistant/tool messages, edits/threshold records on active chain | Missing/extra metadata, drift, inactive/duplicate/cyclic/foreign ancestry, unsupported entry, non-source timestamp | N/A: query evidence | Final bounded correlated query; exact event and active-target binding | No transcript/IDs retained | `test_transcript_corruption_never_binds_owning_failure`; `test_initial_metadata_is_exact_and_later_mutations_are_not_eligible`; `test_only_source_utc_millisecond_timestamp_can_bind_retry` |
| Whole recovery aftermath | Two progressive attempts, independent later cycle, threshold then independent retry with later tool work | Prior success copied to later error; failed threshold later hidden by retry | N/A: evidence ledger | Every cycle succeeds and closes its own resumed turn or exact post-final settlement | No recovery-derived workflow action | `test_threshold_success_then_independent_retry_keeps_both_receipts`; `test_threshold_then_separately_matched_retry_and_later_tool_aftermath` |
| Permanent refusal/bounds | Complete ledger plus unchanged hard tool/observer facts | Parser damage, every unrelated uncertainty, record overflow, cancellation/deadline | N/A: lifecycle | No clear/discard/label subtraction; no record eviction; original deadline | Failure diagnosis only | `test_parser_damage_cannot_be_repaired_by_later_valid_events`; `test_qualified_recovery_preserves_each_unrelated_uncertainty`; `test_bounded_ledger_never_evicts_old_failure_to_gain_acceptance`; existing deadline/abort tests |
| Closure/usage/session | Actual final stats, final text and session match, observer/supervisor/process/EOF/readers/pending complete | Missing/conflicting usage, stale text/session, observer failure, repeated failed close or late cleanup | N/A: existing native contract | Same predicate in both close paths; failure monotonic; disposal independent | Candidate parsing and authenticated success path unchanged | `test_client_final_readback_is_bounded_bound_to_session_and_discarded`; `test_recovery_never_relaxes_closure_and_cannot_improve_on_repeated_close`; `test_shutdown_tail_cannot_complete_unfinished_threshold` |
| Diagnostic CAS binding | Error summary survives removal, completion CAS and reopen | Any of eleven foreign identity links; secret-like message/entry text | N/A: existing completion schema | Fixed reason/count/boolean projection only | Existing authenticated loader/projection refuses corruption; no observation/artifact/route | `test_pi_rpc_result_diagnostics.py::test_content_free_recovery_summary_survives_bound_cas_reload`; `test_recovery_diagnosis_is_bounded_and_content_free` |

All newly named recovery tests above are in
`tests/adapters/test_pi_rpc_recovery_acceptance.py` unless otherwise named.
Positive unit supervisor/group facts are synthetic. Native acceptance and source
deployment readiness must not be inferred from this matrix.

This is source qualification for the October 6 C02 failure, whose exact
triggering event is unavailable. The inspected Pi/agent-core/pi-ai source
members match the retained qualification inventory; the source permits
threshold compaction between turns in an open run. Installed Linux, pinned
Node, model, observer, and subreaper qualification remain separate gates.

| Authority field/record | Legal state/sequence | Illegal/corrupt state | Compiler rule | Admission/runtime rule | Durable/projection rule | Test proof |
| --- | --- | --- | --- | --- | --- | --- |
| Low-level recovery boundary | Completed tool turn -> threshold compaction -> matching end -> next turn -> agent end -> settlement | Open message/tool; open-run retry/overflow/manual start; nested compaction; reason mismatch | N/A: Pi runtime events, authored workflow authority unchanged | Only threshold compaction may use the idle-turn boundary of an open run | No new authority or retry intent from diagnostics | `tests/adapters/test_pi_rpc_recovery.py::test_threshold_compaction_between_tool_turns_keeps_same_agent_run`, `tests/adapters/test_pi_rpc_recovery.py::test_compaction_cannot_interrupt_open_message_or_tool`, `tests/adapters/test_pi_rpc_recovery.py::test_only_threshold_compaction_may_start_between_turns_in_open_run`, `tests/adapters/test_pi_rpc_recovery.py::test_recovery_ends_nesting_and_unsolicited_continuation_are_refused` |
| Retry cycle | Error end with willRetry -> bounded progressive attempts -> continued assistant message end -> successful retry end -> turn/agent end -> settlement; cancelled backoff closes cycle | Unmatched/mismatched end, malformed numbers, non-error false end, missing successful end, unused token | N/A: pinned intrinsic protocol, not compiler retry policy | Pair attempts and outcomes; error/cancel never reuses earlier candidate; counters reset at cycle end | Sticky uncertainty; no fallback token estimate after recovery | `tests/adapters/test_pi_rpc_recovery.py::test_retry_success_ends_at_message_boundary_before_turn_end`, `tests/adapters/test_pi_rpc_recovery.py::test_multiple_retry_attempts_are_one_cycle`, `tests/adapters/test_pi_rpc_recovery.py::test_retry_attempts_reset_after_successful_tool_turn_and_later_error`, `tests/adapters/test_pi_rpc_recovery.py::test_failed_or_cancelled_retry_closes_recovery_and_never_reuses_text`, `tests/adapters/test_pi_rpc_recovery.py::test_false_retry_end_cannot_qualify_success_without_native_success_end`, `tests/adapters/test_pi_rpc_recovery.py::test_retry_fields_are_strict_and_bounded`, `tests/adapters/test_pi_rpc_recovery.py::test_retry_end_requires_matching_attempt` |
| Overflow/summary recovery | Closed failed run -> overflow compact -> single continuation; repeated overflow failure has an end without new start; summary retry can finish during aborted backoff | Duplicate overflow start, threshold retry token, wrong summary source/order, open recovery settlement | N/A: runtime recovery stream; compiled authority unchanged | Continuation requires successful overflow end; summary retries remain inside compaction | No workflow action or hidden invocation is created | `tests/adapters/test_pi_rpc_recovery.py::test_overflow_compaction_authorizes_exactly_one_continuation`, `tests/adapters/test_pi_rpc_recovery.py::test_compaction_recovery_failure_without_second_start_is_narrowly_supported`, `tests/adapters/test_pi_rpc_recovery.py::test_overflow_recovery_cannot_repeat_its_compact_and_retry`, `tests/adapters/test_pi_rpc_recovery.py::test_summarization_retry_is_nested_and_balanced_inside_compaction`, `tests/adapters/test_pi_rpc_recovery.py::test_summary_retry_can_repeat_or_finish_during_aborted_backoff`, `tests/adapters/test_pi_rpc_recovery.py::test_overflow_recovery_resets_after_successful_tool_turn_and_later_overflow`, `tests/adapters/test_pi_rpc_recovery.py::test_terminal_compaction_never_grants_a_spurious_new_run` |
| Cancellation/deadline/closure | Recovering subprocess reaches fixed deadline or correlated abort; prior text refuses; owned process and readers close | Incomplete recovery, accepted earlier text, deadline movement or surviving owned process | N/A: existing adapter lifecycle | Same absolute client deadline; cancellation and actual closure remain required | No extra work time or restored active session | `tests/adapters/test_pi_rpc_recovery.py::test_recovery_never_moves_absolute_invocation_deadline`, `tests/adapters/test_pi_rpc_recovery.py::test_abort_during_recovery_closes_cycle_and_cannot_accept_prior_text`, `tests/adapters/test_pi_rpc_recovery.py::test_scripted_subprocess_recovery_checks_final_text_stats_and_owned_closure`; existing adapter/supervisor tests |
| Native structural diagnostic | Event -> fixed pre-validation structural history -> error -> disposal -> session-bound CAS -> reload | Secret/unknown payload, hostile types, oversized history, any foreign completion binding | N/A: diagnostic evidence is not authored authority | Fixed enums/booleans/counters and at most eight records, 4096 serialized bytes; full diagnostic still 16 KiB | Existing completion binding and corrupt projection refusal; no artifact/observation/routing authority | `tests/adapters/test_pi_rpc_event_diagnostics.py::test_recovery_event_snapshot_keeps_exact_type_and_pre_event_state`, `tests/adapters/test_pi_rpc_event_diagnostics.py::test_unknown_and_malformed_event_types_never_retain_arbitrary_text`, `tests/adapters/test_pi_rpc_event_diagnostics.py::test_hostile_payloads_and_state_stay_within_snapshot_byte_cap`, `tests/adapters/test_pi_rpc_event_diagnostics.py::test_history_evicts_old_events_but_keeps_latest_triggering_event`; recovery variants of `tests/cli/test_cli_pi_result_diagnostics.py::test_failed_pi_diagnostic_survives_disposal_reload_and_refuses_foreign_binding` mutate all eleven bound identity fields |

## Mixed stock edit arguments under group accounting

This source correction covers frozen Pi 0.87.0's preparation of an existing
`edits` array plus a legacy `oldText`/`newText` pair. Preparation returns a
new object containing both replacements. The original declaration and tool
start remain coherent, while the native success sentence counts the prepared
array. Core now counts that same combination in its local qualification copy.
It does not accept changed argument snapshots. This correction originally kept
failed tool results uncertain; the completed file-error policy below supersedes
that restriction under explicit group accounting.

The C20 retained diagnostic identifies `unqualified_tool_result` at settled
process closure but does not identify its triggering event. The regression
is source-derived evidence for this boundary, not proof that C20 used this
mixed argument form. No installed Linux/model qualification is claimed here.

| Authority field/record | Legal state/sequence | Illegal/corrupt state | Compiler rule | Admission/runtime rule | Durable/projection rule | Test proof |
| --- | --- | --- | --- | --- | --- | --- |
| Stock edit declaration and result | Original mixed arguments -> native appended pair -> matching two-block result -> coherent result/turn/agent snapshots -> settlement -> complete closure | Stale result ID, missing result message, or malformed successful replacement | N/A: native tool arguments, authored workflow unchanged | Explicit group accounting counts both strict replacements; ordinary edit arrays remain accepted; successful-result checks are unchanged by completed error feedback | N/A: no durable or selected-authority changes | `tests/adapters/test_pi_rpc_tool_result_contract.py::test_pinned_pi_mixed_legacy_edit_pair_qualifies_its_successful_closure`, `test_ordinary_edit_array_result_closes_the_tool_and_agent`, `test_legacy_failed_edit_result_remains_uncertain_after_agent_settlement`, `test_stale_or_incomplete_edit_result_cannot_close_the_turn` |

## Completed file-error feedback under group accounting

Pinned Pi converts file execution and argument-validation exceptions into a
completed, one-text-block `isError:true` result and permits subsequent agent
work. Treating every such result as permanent invocation uncertainty was
excessive rejection. V2/v3 now qualify that native feedback shape without
changing its error bit or text. Successful file-result checks, legacy behavior,
final candidate validation and every lifecycle/process gate remain in place.
No same-path retry obligation or error-message classification is added.

The C22 historical result and observer records are unavailable. This prospective
source-policy correction does not establish its exact triggering call or waive
its independent signal, stream-destruction and incomplete-group refusals.

| Authority field/record | Legal state/sequence | Illegal/corrupt state | Compiler rule | Admission/runtime rule | Durable/projection rule | Test proof |
| --- | --- | --- | --- | --- | --- | --- |
| Completed selected file error | Missing read, mismatched edit or denied write -> truthful error feedback -> later successful work -> final settlement -> complete owned closure -> repeated close | Missing/foreign result, changed arguments, contradictory result snapshot or malformed error bit | N/A: native feedback, no authored policy field changes | Explicit group accounting accepts one text error block; all original correlation and snapshot checks apply | N/A: no durable schema or selected-authority change | `tests/adapters/test_pi_rpc_recoverable_file_feedback.py::test_group_accounting_accepts_recovered_file_error_feedback`, `test_invalid_error_feedback_or_snapshots_still_refuse` |
| Result scope | Existing strict successful read/edit/write remains valid | Error image/multiple-block content, unsupported tool, or malformed successful result | N/A: native result contract | Completed error feedback does not relax successful-result qualification or unknown-tool refusal | N/A: feedback creates no artifact or routing authority | `tests/adapters/test_pi_rpc_recoverable_file_feedback.py::test_invalid_error_feedback_or_snapshots_still_refuse`; existing `test_pi_rpc_tool_result_contract.py` success and stale-result cases |
| Final outcome and runtime closure | Recovered feedback -> coherent final candidate -> unchanged readback and owned closure | Invalid final candidate, cancellation, spent deadline, or incomplete supervisor disposal | N/A: existing invocation/result contract | Existing final parser, absolute deadline, cancellation and closure predicates remain required | Existing workflow validates terminal obligations; feedback alone is not task success | `tests/adapters/test_pi_rpc_recoverable_file_feedback.py::test_invalid_final_candidate_is_not_accepted_after_closed_tool_lifecycle`, `test_recovered_file_error_does_not_override_runtime_failure_gates`; existing `test_pi_rpc_groups.py` and `test_pi_rpc_guarded_timeout.py` signal/EOF/ownership cases |
| Legacy profile | Successful legacy text edit remains valid | Failed legacy edit followed by final settlement | N/A: unchanged legacy profile admission | Legacy file-error uncertainty remains unchanged | N/A: no legacy persistence change | `tests/adapters/test_pi_rpc_tool_result_contract.py::test_legacy_failed_edit_result_remains_uncertain_after_agent_settlement` |
