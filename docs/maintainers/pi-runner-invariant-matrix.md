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
