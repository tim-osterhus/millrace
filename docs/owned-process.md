# Owned byte processes

The development 0.23.2 API `millrace.router.process` exposes
`OWNED_PROCESS_CONTRACT = "owned-process-v1"`, immutable `ProcessRequest` and
`ProcessResult`, `OwnedProcessError`, and synchronous `run_owned_process`.
Core remains dependency-free. This module contains no native adapter protocol,
provider settings, result parser, durable state or retry policy.

`ProcessRequest` captures argv, cwd, explicit environment, optional stdin bytes,
an absolute wall-clock deadline, finite input/capture limits and optional
fail-on-observed-byte limits. It snapshots the environment and rejects invalid
launch shapes or limits before spawning. Arguments after the executable may be
empty strings and are passed unchanged. `stdin=None` selects DEVNULL. Byte input
is delivered nonblocking while both outputs drain fairly. Retained
captures are bounded; counters and SHA-256 describe only bytes actually read.

The helper converts the remaining deadline once to a monotonic budget. Control
polling and callbacks share it. Callbacks must do bounded synchronous work;
Python callback code cannot be preempted, and the helper checks time immediately
on return. Timeout, cancellation, overflow, delivery/setup failure or callback
failure closes stdin and starts one cleanup clock: TERM, KILL after two seconds
from the first TERM attempt if needed, and a five-second total cleanup bound.
Repeated cancellation does not reset that bound. Root-exit draining reserves the
existing two-second escalation interval inside the same total bound. Sustained
inherited output cannot postpone the first signal until the cleanup budget is
gone. After a finite callback returns past the total bound, the helper attempts
KILL immediately instead of starting another grace period; arbitrary callback
code still cannot be preempted.

`run_owned_process(request, *, poll, on_started, on_stdout=None, on_stderr=None,
on_drained=None, on_stop=None)` reports root exit/reaping, stdin delivery/closure,
output EOF,
group state, forced cleanup and cleanup confirmation separately. Permission
denial probing a group means unknown. Only the group created during the live
call may be signaled; a historical PID is never accepted. Detached or remote
effects are not established by local closure.
Once the reaped root's group is observed closed, that numeric group is never
probed or signaled again. If pipe setup fails, EOF may remain unobserved; cleanup
can return after the owned root and group close with confirmation still false.

A reaped nonzero root, including a signal-negative exit, starts the same
five-second cleanup clock. The default `cleanup_on_root_exit=False` retains
natural child/output drain after a zero exit. Setting it `True` starts cleanup
after every root exit, including zero. Failed-root and explicit all-root cleanup
preserve the actual root exit code and add no stop cause by themselves.
Queued output drains through EOF/EAGAIN in bounded fair quanta with control/time
checks before the first root-policy signal. `on_drained(exit_code)` runs once
after the root is reaped, stdin is closed and both output EOFs are established,
before forced cleanup. It never runs after a prior causal stop or forced
cleanup. The caller can snapshot a separate bounded result channel at that
boundary without teaching Core its protocol.

`stop_cause=None` means no causal stop was observed. It does not attest native
success or clean writer closure: failed-root or opted-in cleanup may force
closure without assigning a cause. At a shared checkpoint cancellation precedes
newly observed stdout overflow, stderr overflow and deadline expiry; the first
committed cause
remains. Callers preserve their own native failure precedence and map process
facts into their existing writer classifications. For example, a captured
completed native result may remain held with `may_live` until exact guarded
reconciliation and explicit resume; failed work needs a fresh explicit retry.

`on_stop(cause)` observes the first committed generic cause once, after that
cause and the original cleanup clock are fixed, before later cleanup stream
callbacks. Root-only cleanup without a cause does not notify. Stream callbacks
continue receiving cleanup bytes, so callers can retain diagnostics while
keeping an earlier cancellation, timeout or native failure classification.
The observer does not choose a cause or add a cleanup grace period. It follows
the same bounded-work and exception rules as other callbacks. If another caller
callback is already failing, its original exception takes precedence over a
secondary observer exception; the observer is never called recursively.

An ordinary callback exception triggers bounded cleanup and raises
`OwnedProcessError` with `.result` and the original exception as its cause.
BaseException interruptions are re-raised unchanged after cleanup. Arbitrary
exception messages are not copied into process diagnostics.

The builtin command adapter composes this API with DEVNULL stdin, a 65,536-byte
stdout protocol threshold and independently truncated/redacted stderr. Core's
existing engine, acknowledgment, hold, reconciliation and store-validation
paths remain authoritative. Linux fake-process proof is separate from any
macOS or native/provider route qualification; previous native receipts apply
only to their original source bytes.

Command request validation failures return stopped no-child execution failures.
Spawn failure diagnostics intentionally use the stable generic `start_failed`
code instead of an exception class name. Cancellation before spawn similarly
returns a stopped no-child cancellation. These changes affect diagnostics or
avoid unnecessary launch; native result taxonomies and writer controls remain
unchanged. A timeout followed by stdout overflow preserves timeout while
cleanup is unconfirmed, matching the earlier command cleanup result.
