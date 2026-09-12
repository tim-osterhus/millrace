"""Public CLI launcher with a real stock file-lock contender for N05 only."""

import os
import sys
import threading
import time
from pathlib import Path

# Fault-injection barriers delay scheduling only; no product method, facade,
# writer, clock or transport is replaced. They isolate native preemption before
# Core's next cancellation poll and stop after durable invalidation, before
# finalization can enter its effects.
if os.environ.get("CORE_NATIVE_DEADLINE_TRAP") == "1":
    trap_root = Path(sys.argv[sys.argv.index("--workspace") + 1]).parent
    trapped = set()

    def trace_return(frame, event, arg):
        if event != "return":
            return
        name = frame.f_code.co_name
        if name == "drive_native_control" and name not in trapped:
            current = frame.f_locals.get("current")
            if getattr(current, "state", None) == "paused":
                trapped.add(name)
                (trap_root / "coordinator-parked").write_text(
                    "after durable pause settlement"
                )
                threading.Event().wait()
        if (
            name == "invalidate"
            and frame.f_code.co_filename.endswith("/pause_control.py")
            and name not in trapped
        ):
            control = frame.f_locals.get("self")
            if getattr(control, "_reason", None) in {"native_preempted", "finalizing"}:
                trapped.add(name)
                (trap_root / "preemption-durable").write_text(control._reason)
                threading.Event().wait()

    threading.setprofile(trace_return)
    sys.setprofile(trace_return)

if os.environ.get("CORE_NATIVE_ADMISSION_TRAP") == "1":
    trap_root = Path(sys.argv[sys.argv.index("--workspace") + 1]).parent

    def admission_trace(frame, event, arg):
        if event == "call" and frame.f_code.co_name == "admit_native_control":
            (trap_root / "admission-waiting").write_text("native lane entered")
            until = time.monotonic() + 4
            while (
                not (trap_root / "admission-release").exists()
                and time.monotonic() < until
            ):
                time.sleep(0.01)

    threading.setprofile(admission_trace)

lock_seconds = float(os.environ.get("CORE_NATIVE_TOOL_LOCK_SECONDS", "0"))
if lock_seconds:
    from millforge.tools.pi_compat.mutations import file_mutation_lock

    ready = threading.Event()
    root = Path(sys.argv[sys.argv.index("--workspace") + 1])

    def contend():
        with file_mutation_lock(root / "after.txt"):
            ready.set()
            time.sleep(lock_seconds)

    contender = threading.Thread(
        target=contend, name="external-lock-contender", daemon=True
    )
    contender.start()
    assert ready.wait(2)
from millrace.adapters.cli.main import main  # noqa: E402 - install barriers first

raise SystemExit(main())
