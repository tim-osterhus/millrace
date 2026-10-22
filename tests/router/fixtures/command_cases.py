import json
import os
import subprocess
import sys
import time
from pathlib import Path

mode = sys.argv[1]
if mode == "good":
    print(json.dumps({"outcome": "READY"}))
elif mode == "stderr":
    print('{"outcome":"BLOCKED"}', file=sys.stderr)
    print('{"outcome":"READY"}')
elif mode == "nonzero":
    print('{"outcome":"READY"}')
    sys.exit(2)
elif mode == "hugeout":
    sys.stdout.write("x" * 100000)
elif mode == "hugeerr":
    sys.stderr.write("x" * 1000000)
    print('{"outcome":"READY"}')
elif mode == "utf16":
    os.write(1, '{"outcome":"READY"}'.encode("utf-16"))
elif mode == "invalidutf8":
    os.write(1, b"\xff")
elif mode == "duplicate":
    print('{"outcome":"READY","outcome":"BLOCKED"}')
elif mode == "trailing":
    print('{"outcome":"READY"} {}')
elif mode == "unknown":
    print('{"outcome":"MYSTERY"}')
elif mode == "wait":
    time.sleep(20)
elif mode in {"child", "linger", "detached"}:
    if mode == "detached":
        sys.stderr.write("x" * 8192)
        sys.stderr.flush()
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time;time.sleep(" + ("0.2" if mode == "child" else "20") + ")",
        ],
        start_new_session=mode == "detached",
    )
    Path("child.pid").write_text(str(child.pid))
    print('{"outcome":"READY"}', flush=True)
elif mode == "both":
    os.write(2, b"e" * 100000)
    os.write(1, b"o" * 100000)
