import json
from dataclasses import replace

import pytest

from millrace.compiler import compile_workflow
from millrace.compiler.export import (
    compiled_plan_export_bytes,
    verify_compiled_plan_export_bytes,
)
from millrace.contracts.compiled_plan import (
    RunnerBindingWithPayloadCapacityDeclaration,
    authority_fingerprint,
    canonical_authority_bytes,
)
from millrace.contracts.runner_payload_capacity import (
    PayloadCapacityError,
    completion_capacity,
    installed_payload_capacity_pin,
    payload_capacity_pin_record,
)
from millrace.substrate.codecs import _decode_runner_binding, _encode_runner_binding
from support.generic_lifecycle import source_with_completion_behavior
from tests.compiler.test_pi_runner_bindings import EFFECTS


def capacity_source():
    source = source_with_completion_behavior()
    for runner in source['runner_bindings']:
        runner['adapter_kind'] = 'pi_rpc'
        runner['component_pin'] = None
        runner['terminal_result_mappings'] = ()
        runner['required_capability_ids'] = EFFECTS
        runner["payload_capacity_pin"] = payload_capacity_pin_record(
            installed_payload_capacity_pin()
        )
    source["capabilities"].extend(
        dict(
            id=name,
            kind="runner.invoke",
            support_status="supported",
            grant_status="granted",
            approval_policy_id=None,
        )
        for name in EFFECTS
        if name not in {d["id"] for d in source["capabilities"]}
    )
    return source


def test_completion_v4_roundtrip_export_and_capacity_boundary():
    source = capacity_source()
    source['completion_behaviors'][0]['request_payload_byte_limit'] = 1048576
    result = compile_workflow(source)
    assert result.plan is not None, result.diagnostics
    plan = result.plan
    runner = plan.runner_bindings[0]
    assert type(runner) is RunnerBindingWithPayloadCapacityDeclaration
    assert completion_capacity(runner, 1048576) == 1048576
    decoded = _decode_runner_binding(_encode_runner_binding(runner))
    assert decoded == runner
    assert canonical_authority_bytes(
        replace(plan, runner_bindings=(decoded,))
    ) == canonical_authority_bytes(plan)
    verify_compiled_plan_export_bytes(compiled_plan_export_bytes(plan))
    source['completion_behaviors'][0]['request_payload_byte_limit'] += 1
    rejected = compile_workflow(source)
    assert rejected.plan is None
    assert any(
        d.context.get("reason") == "request_payload_byte_limit_exceeds_runner_capacity"
        for d in rejected.diagnostics
    )


@pytest.mark.parametrize('limit', [True, 0, -1, '1', None, 1048577])
def test_shared_predicate_refuses_bad_request(limit):
    result = compile_workflow(capacity_source())
    assert result.plan is not None, result.diagnostics
    with pytest.raises(PayloadCapacityError):
        completion_capacity(result.plan.runner_bindings[0], limit)


@pytest.mark.parametrize(
    "mutation", ["missing", "null", "native", "hash", "bool", "extra", "v3"]
)
def test_compiler_refuses_untrusted_capacity(mutation):
    source = capacity_source()
    runner = source['runner_bindings'][0]
    if mutation == 'missing':
        runner.pop('payload_capacity_pin')
    elif mutation == 'null':
        runner['payload_capacity_pin'] = None
    elif mutation == 'native':
        runner["component_pin"] = source_with_completion_behavior()["runner_bindings"][
            0
        ]["component_pin"]
    elif mutation == 'hash':
        runner['payload_capacity_pin']['descriptor_sha256'] = '0' * 64
    elif mutation == 'bool':
        runner['payload_capacity_pin']['max_work_item_payload_bytes'] = True
    elif mutation == 'extra':
        runner['payload_capacity_pin']['extra'] = 1
    else:
        runner['schema_version'] = 3
    assert compile_workflow(source).plan is None


def test_cas_rejects_wrong_version_shape_and_preserves_legacy():
    from tests.cli.test_cli_pi_execution import pi_source
    legacy = compile_workflow(pi_source()).plan
    assert legacy is not None
    old = legacy.runner_bindings[0]
    record = _encode_runner_binding(old)
    assert record['schema_version'] == 3 and 'payload_capacity_pin' not in record
    assert _encode_runner_binding(_decode_runner_binding(record)) == record
    assert authority_fingerprint(legacy) == authority_fingerprint(
        replace(legacy, runner_bindings=(_decode_runner_binding(record),))
    )
    for bad in (
        {**record, "schema_version": 4},
        {**record, "payload_capacity_pin": None},
    ):
        with pytest.raises(ValueError):
            _decode_runner_binding(bad)
    v4 = compile_workflow(capacity_source()).plan.runner_bindings[0]
    encoded = dict(_encode_runner_binding(v4))
    # Existing v3 reader header rejects the self-versioned new nested record.
    from millrace.substrate.codecs import _RUNNER_BINDING_KEYS, _ensure_record_header
    with pytest.raises(ValueError):
        _ensure_record_header(encoded, old.record_kind, 3, _RUNNER_BINDING_KEYS)
    selected = json.loads(
        canonical_authority_bytes(compile_workflow(capacity_source()).plan)
    )
    assert selected['runner_bindings'][0]['payload_capacity_pin']['schema_version'] == 1


def test_context_bound_exact_v4_and_arbitrary_subclass_refusal():
    from millrace.contracts.compiled_plan import context_binding_authority_refusal
    from tests.compiler.test_context_bindings import _source_with_context_binding
    source = _source_with_context_binding()
    for runner in source['runner_bindings']:
        runner['adapter_kind'] = 'pi_rpc'
        runner['component_pin'] = None
        runner['terminal_result_mappings'] = ()
        runner["payload_capacity_pin"] = payload_capacity_pin_record(
            installed_payload_capacity_pin()
        )
        runner['required_capability_ids'] = EFFECTS
    source["capabilities"].extend(
        dict(
            id=name,
            kind="runner.invoke",
            support_status="supported",
            grant_status="granted",
            approval_policy_id=None,
        )
        for name in EFFECTS
        if name not in {d["id"] for d in source["capabilities"]}
    )
    result = compile_workflow(source)
    assert result.plan is not None, result.diagnostics
    assert context_binding_authority_refusal(result.plan) is None
    verify_compiled_plan_export_bytes(compiled_plan_export_bytes(result.plan))
    class Untrusted(RunnerBindingWithPayloadCapacityDeclaration):
        pass
    runner = next(r for r in result.plan.runner_bindings if 'taskmaster' in str(r.id))
    from dataclasses import fields
    # Deliberately bypass constructor to model hostile reconstruction.
    bad = object.__new__(Untrusted)
    for field in fields(runner):
        object.__setattr__(bad, field.name, getattr(runner, field.name))
    plan = replace(
        result.plan,
        runner_bindings=tuple(
            bad if r.id == runner.id else r for r in result.plan.runner_bindings
        ),
    )
    assert 'runner_record' in context_binding_authority_refusal(plan)


def test_duplicate_pin_keys_refuse_export_and_cas_bytes():
    from millrace.substrate.codecs import _parse_json_object
    raw = compiled_plan_export_bytes(compile_workflow(capacity_source()).plan)
    duplicate = raw.replace(
        b'"contract_id":', b'"contract_id":"duplicate","contract_id":', 1
    )
    with pytest.raises(ValueError):
        verify_compiled_plan_export_bytes(duplicate)
    with pytest.raises(ValueError):
        _parse_json_object(duplicate)


@pytest.mark.parametrize(
    "missing_pin,reason,field",
    [
        (True, "missing_runner_component_pin", "runner_binding_id"),
        (False, "request_payload_byte_limit_exceeds_runner_capacity",
         "request_payload_byte_limit"),
    ],
)
def test_completion_capacity_diagnostic_preserves_declaration_path(
    missing_pin, reason, field
):
    source = capacity_source()
    if missing_pin:
        source["runner_bindings"][0].pop("payload_capacity_pin")
    else:
        source["completion_behaviors"][0]["request_payload_byte_limit"] = 1048577
    result = compile_workflow(source)
    assert result.plan is None
    diagnostics = [
        d for d in result.diagnostics
        if d.code == "invalid_completion_behavior_declaration"
    ]
    assert [
        (d.code, d.context["reason"], d.declaration_path)
        for d in diagnostics
    ] == [
        ("invalid_completion_behavior_declaration", reason,
         f"completion_behaviors[0].{field}")
    ]
