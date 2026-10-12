"""Builder failures expose fixed process uncertainty codes without raw details."""

import json
from types import SimpleNamespace

import pytest

from millrace.adapters.pi_rpc_client import PiRpcError
from millrace.adapters.pi_rpc_diagnostics import native_failure_diagnostics

_GROUP_UNCERTAINTIES = (
    "invalid group observer",
    "process control observed",
    "group observer fault",
    "Pi nonzero exit",
    "signal or surviving group",
    "group identity discontinuity",
    "premature stream destroy",
    "shell spawn error",
    "incomplete group evidence",
)


def _diagnostics(uncertainties: set[str]) -> dict[str, object]:
    client = SimpleNamespace(
        lifecycle=SimpleNamespace(uncertain=uncertainties),
        stream_bytes={},
        stream_events={},
        stream_error_codes={},
        errors=[],
        pending={},
        stderr=b"",
        process=SimpleNamespace(poll=lambda: 1),
    )
    return native_failure_diagnostics(client, PiRpcError("incomplete process closure"))


@pytest.mark.parametrize("uncertainty", _GROUP_UNCERTAINTIES)
def test_group_failure_diagnostic_exposes_only_its_fixed_code(uncertainty):
    private_detail = "untrusted observer detail /private/path"

    diagnostics = _diagnostics({uncertainty, private_detail})

    assert diagnostics["lifecycle_uncertainty_count"] == 2
    assert diagnostics["lifecycle_uncertainties"] == [
        uncertainty.replace(" ", "_").replace("/", "_")
    ]
    assert diagnostics["lifecycle_unclassified_uncertainty_count"] == 1
    assert private_detail not in json.dumps(diagnostics)


def test_builder_diagnostic_counts_known_and_unclassified_failures_separately():
    uncertainties = {
        *_GROUP_UNCERTAINTIES,
        "unqualified tool result",
        "untrusted observer detail /private/path",
    }

    diagnostics = _diagnostics(uncertainties)

    assert diagnostics["lifecycle_uncertainty_count"] == 11
    assert set(diagnostics["lifecycle_uncertainties"]) == {
        *(value.replace(" ", "_").replace("/", "_") for value in _GROUP_UNCERTAINTIES),
        "unqualified_tool_result",
    }
    assert diagnostics["lifecycle_unclassified_uncertainty_count"] == 1
    assert "untrusted observer detail" not in json.dumps(diagnostics)
