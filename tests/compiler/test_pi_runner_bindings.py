from copy import deepcopy

import pytest

from millrace.compiler.runner_bindings import normalize_selected_runner_bindings

EFFECTS = [
    "unrestricted.filesystem.read",
    "unrestricted.filesystem.write",
    "unrestricted.process.execute",
]


def source():
    return {
        "runner_bindings": [
            {
                "id": "opaque",
                "adapter_kind": "pi_rpc",
                "required_capability_ids": EFFECTS.copy(),
            }
        ],
        "capabilities": [
            {
                "id": name,
                "kind": "runner.invoke",
                "support_status": "supported",
                "grant_status": "granted",
                "approval_policy_id": None,
            }
            for name in EFFECTS
        ],
    }


def diagnostics(value):
    found = []
    result = normalize_selected_runner_bindings(
        value, workflow_id="opaque", workflow_version="1", diagnostics=found
    )
    return result, found


def test_explicit_pi_is_preserved_without_component_defaults():
    result, found = diagnostics(source())
    assert not found
    assert result["runner_bindings"][0]["adapter_kind"] == "pi_rpc"


@pytest.mark.parametrize("index", range(3))
@pytest.mark.parametrize("mutation", ["missing", "denied", "approval", "duplicate"])
def test_every_fixed_tool_effect_requires_one_granted_declaration(index, mutation):
    value = deepcopy(source())
    if mutation == "missing":
        value["runner_bindings"][0]["required_capability_ids"].pop(index)
    elif mutation == "denied":
        value["capabilities"][index]["grant_status"] = "denied"
    elif mutation == "approval":
        value["capabilities"][index]["approval_policy_id"] = "approval"
    else:
        value["capabilities"].append(value["capabilities"][index].copy())
    _, found = diagnostics(value)
    assert any(d.code == "pi_runner_capability_unsupported" for d in found)
