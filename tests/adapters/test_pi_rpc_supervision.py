"""Private supervisor reports must prove disposal in order, to completion."""

import json
import os
import time

import pytest

from millrace.adapters.pi_rpc_supervision import PiSupervisor


def _fake(tmp_path, reports, returncode=0):
    script = tmp_path / "fake_supervisor.py"
    payloads = [
        report.encode() if isinstance(report, str) else report for report in reports
    ]
    script.write_text(
        "import os,sys\n"
        f"for report in {payloads!r}: os.write(int(sys.argv[1]), report)\n"
        f"sys.exit({returncode})\n"
    )
    owner = PiSupervisor(
        script,
        ("unused",),
        tmp_path,
        {"PATH": os.environ.get("PATH", "")},
        -1,
        time.monotonic() + 3,
    )
    return owner


def _ready():
    return json.dumps({"kind": "ready", "pi_pid": 123456}) + "\n"


def _complete(*, pi_returncode=0, disposed=True, remaining_children=0):
    return json.dumps(
        {
            "kind": "complete",
            "pi_returncode": pi_returncode,
            "disposed": disposed,
            "reaped_children": 2,
            "term_count": 1,
            "kill_count": 0,
            "remaining_children": remaining_children,
        }
    ) + "\n"


@pytest.mark.parametrize(
    "reports,returncode",
    [
        ([_ready()], 0),
        ([_complete(), _ready()], 0),
        ([_ready(), _complete(), _complete()], 0),
        ([_ready(), _complete().replace('"disposed": true', '"disposed": false')], 0),
        ([_ready(), _complete(pi_returncode=-9)], 0),
        ([_ready(), _complete(pi_returncode=0)], 1),
        ([_ready(), '{"kind":"complete","kind":"complete"}\n'], 0),
        ([_ready(), b"\xff\n"], 0),
        ([_ready(), _complete(remaining_children=1)], 0),
    ],
)
def test_missing_out_of_order_duplicate_or_contradictory_report_fails_closed(
    tmp_path, reports, returncode
):
    owner = _fake(tmp_path, reports, returncode)
    try:
        try:
            owner.startup(time.monotonic() + 3)
        except ValueError:
            pass
        owner.process.wait(timeout=3)
        assert not owner.finish()
        assert not owner.finish()
        with pytest.raises(ValueError):
            owner.signal_pi("terminate")
    finally:
        if owner.process.poll() is None:
            owner.process.kill()
            owner.process.wait(timeout=3)


def test_complete_report_is_idempotent_under_late_stop(tmp_path):
    owner = _fake(tmp_path, [_ready(), _complete()])
    try:
        assert owner.startup(time.monotonic() + 3) == 123456
        owner.process.wait(timeout=3)
        assert owner.finish()
        owner.signal_pi("terminate")
        owner.signal_pi("kill")
        assert owner.finish()
    finally:
        if owner.process.poll() is None:
            owner.process.kill()
            owner.process.wait(timeout=3)


def test_nonzero_pi_can_prove_disposal_but_not_zero_exit(tmp_path):
    owner = _fake(tmp_path, [_ready(), _complete(pi_returncode=-9)], 1)
    try:
        owner.startup(time.monotonic() + 3)
        assert owner.process.wait(timeout=3) == 1
        assert owner.finish()
        assert owner.report is not None
        assert owner.report["pi_returncode"] == -9
        assert owner.process.returncode != 0
    finally:
        if owner.process.poll() is None:
            owner.process.kill()
            owner.process.wait(timeout=3)
