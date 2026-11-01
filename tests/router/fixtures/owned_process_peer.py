"""Finite process-pressure and root/pipe-order scenarios, never a native tool."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

mode = sys.argv[1]
if mode == "pressure":
    os.write(1, b"o" * 131072)
    os.write(2, b"e" * 131072)
    raw = sys.stdin.buffer.read()
    os.write(1, str(len(raw)).encode())
elif mode == "root-flood":
    code = (
        "import os,signal;from pathlib import Path;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "Path('flood-ready').write_text('ready');"
        "\nwhile True: os.write(1,b'x'*8192)\n"
    )
    child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.DEVNULL)
    Path("child.pid").write_text(str(child.pid))
    until = time.monotonic() + 2
    while not Path("flood-ready").exists() and time.monotonic() < until:
        time.sleep(0.002)
    assert Path("flood-ready").exists()
elif mode in {"root-tail", "root-pipe"}:
    code = (
        "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "time.sleep(30)"
    )
    kwargs = {} if mode == "root-pipe" else {
        "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
    }
    child = subprocess.Popen(
        [sys.executable, "-c", code], stdin=subprocess.DEVNULL, **kwargs
    )
    Path("child.pid").write_text(str(child.pid))
    if mode == "root-tail":
        for _ in range(64):
            os.write(1, b'{"record":"' + b"x" * 2048 + b'"}\n')
    time.sleep(0.04)
elif mode == "eof-first":
    os.close(1)
    os.close(2)
    time.sleep(0.15)
elif mode in {"root-late-eof", "detached-tail"}:
    child = subprocess.Popen(
        [sys.executable, "-c", "import os,time;time.sleep(.2);os.write(1,b'tail')"],
        stdin=subprocess.DEVNULL, start_new_session=mode == "detached-tail",
    )
    Path("child.pid").write_text(str(child.pid))
elif mode == "ignore":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    Path("ready").write_text("ready")
    while True:
        time.sleep(0.02)

elif mode == "failed-root":
    failure, pipes = sys.argv[2:4]
    code = (
        "import json,os,signal,time;from pathlib import Path\n"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
        "ready=Path('child-ready');temporary=Path('child-ready.tmp')\n"
        "temporary.write_text(json.dumps({'pid':os.getpid(),'pgid':os.getpgrp()}))\n"
        "temporary.replace(ready)\n"
        "while True:\n"
        " with open('child.ticks','ab') as stream: stream.write(b'tick\\n')\n"
        " time.sleep(.02)\n"
    )
    kwargs = {} if pipes == "inherited" else {
        "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
    }
    child = subprocess.Popen(
        [sys.executable, "-c", code], stdin=subprocess.DEVNULL, **kwargs
    )
    Path("child.pid").write_text(str(child.pid))
    until = time.monotonic() + 3
    while not Path("child-ready").exists() and time.monotonic() < until:
        time.sleep(0.002)
    assert Path("child-ready").exists()
    Path("root-ready.tmp").write_text(json.dumps({
        "pid": os.getpid(), "pgid": os.getpgrp(), "child_pid": child.pid,
        "child": json.loads(Path("child-ready").read_text()),
        "failure": failure, "pipes": pipes,
    }))
    Path("root-ready.tmp").replace("root-ready")
    until = time.monotonic() + 3
    while not Path("root-release").exists() and time.monotonic() < until:
        time.sleep(0.002)
    assert Path("root-release").exists()
    print('{"outcome":"READY"}', flush=True)
    if failure == "signal":
        os.kill(os.getpid(), signal.SIGKILL)
    else:
        sys.exit(7)
