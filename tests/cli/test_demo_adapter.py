from __future__ import annotations

from types import SimpleNamespace

import pytest

from millrace.adapters.cli.demo_adapter import synthetic_result
from millrace.contracts.demo import DemoRefusal


def request(stage, payload=None, evidence=None):
    return SimpleNamespace(
        dispatch_envelope=SimpleNamespace(
            stage_kind_id=stage,
            work_item_payload=payload or {},
            selected_join_evidence=evidence,
        )
    )


def test_verifier_requires_both_actual_join_evidence_branches() -> None:
    with pytest.raises(DemoRefusal, match="join_evidence"):
        synthetic_result(request("verify_first"))
    evidence = {
        "evidence_artifacts": [
            {
                "artifact_schema_id": "branch_a",
                "payload": {"summary": "Synthetic artifact A: complete"},
            },
            {
                "artifact_schema_id": "branch_b",
                "payload": {"summary": "Synthetic artifact B: checksum missing"},
            },
        ]
    }
    marker, value = synthetic_result(request("verify_first", evidence=evidence))
    assert marker == "EXPECTED_FAIL" and value["check_state"] == "defective"
    marker, repaired = synthetic_result(request("repair", value))
    assert marker == "CORRECTED"
    assert synthetic_result(request("verify_pass", repaired))[0] == "PASS"
    with pytest.raises(DemoRefusal, match="verification_input"):
        synthetic_result(request("verify_pass", value))


def test_missing_recovery_evidence_refuses_without_new_start_or_completion():
    from millrace.adapters.cli.demo_adapter import _DemoAdapter

    validated = []
    retained = []
    invocation = SimpleNamespace(session_id="same-session")
    adapter = _DemoAdapter(
        validated.append,
        lambda *args: retained.append(args),
        lambda session_id: None,
        lambda request: True,
    )
    with pytest.raises(DemoRefusal, match="retained_session_evidence_missing"):
        adapter.reconcile_session(SimpleNamespace(invocation_request=invocation))
    assert validated == [invocation]
    assert retained == []
