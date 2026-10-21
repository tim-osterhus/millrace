"""Closure-request overflow authority parity across compiler, export, codec, and kernel.

Every invalid ``request_overflow`` declaration must be refused the same way by the
compiler diagnostic, the shared predicate, the typed constructor path, the export
verifier, the CAS codec, and kernel admission. Fixtures are built in-process; the
tests read no workspace, runtime, or external path.
"""

from dataclasses import replace

import pytest

from millrace.compiler import compile_workflow
from millrace.compiler.export import (
    CompiledPlanExportError,
    compiled_plan_export_bytes,
    verify_compiled_plan_export_bytes,
)
from millrace.contracts.compiled_plan import (
    ClosureRequestOverflowDeclaration,
    CompletionBehaviorDeclaration,
    CompletionBehaviorWithRequestOverflowDeclaration,
    authority_fingerprint,
    canonical_authority_bytes,
    closure_request_overflow_refusal,
    context_binding_authority_refusal,
)
from millrace.contracts.runner_payload_capacity import (
    installed_payload_capacity_pin,
    payload_capacity_pin_record,
)
from millrace.contracts.transition import AdmitPlan
from millrace.kernel import decide, empty_runtime_state
from millrace.substrate import codecs
from millrace.substrate.codecs import InvalidCasObject
from millrace.substrate.errors import UnsupportedRecordKind, UnsupportedSchemaVersion
from millrace.testing import deterministic_context
from support.generic_lifecycle import source_with_completion_behavior
from tests.compiler.test_pi_runner_bindings import EFFECTS

MIB = 1024 * 1024
REQUEST_LIMIT = 8192
BASE_OVERFLOW = {
    "policy": "materialized_evidence_pages",
    "materialization_root": "closure-evidence",
    "page_byte_limit": 4096,
    "max_snapshot_bytes": MIB,
    "max_page_count": 1024,
}
BEHAVIOR_ID = "lifecycle.completion"
REVIEW_BINDING_ID = "lifecycle.review_context"
BAD_DECLARATION = "invalid_completion_behavior_declaration"
DECLARATION_PATH = "completion_behaviors[0].request_overflow"
OVERLAP_CODE = "context_binding_overflow_root_overlap"
UNSUPPORTED_AUTHORITY = "unsupported_selected_authority"

# (field, bad value, expected shared refusal reason). Each row is a value that a typed
# declaration can carry, so it is also exercised through object.__setattr__ tampering.
INVALID_FIELD_VARIANTS = [
    pytest.param("policy", "other", "request_overflow_policy", id="policy"),
    pytest.param(
        "materialization_root",
        ".hidden/evidence",
        "request_overflow_materialization_root",
        id="hidden_root",
    ),
    pytest.param(
        "materialization_root",
        ".millrace/evidence",
        "request_overflow_materialization_root",
        id="millrace_root",
    ),
    pytest.param(
        "materialization_root",
        "closure/../evidence",
        "request_overflow_materialization_root",
        id="dotdot_root",
    ),
    pytest.param(
        "materialization_root",
        "/abs/evidence",
        "request_overflow_materialization_root",
        id="absolute_root",
    ),
    pytest.param(
        "materialization_root",
        "cafe\u0301/evidence",
        "request_overflow_materialization_root",
        id="non_nfc_root",
    ),
    pytest.param(
        "page_byte_limit", 4095, "request_overflow_page_byte_limit", id="page_below_min"
    ),
    pytest.param(
        "page_byte_limit",
        8193,
        "request_overflow_page_byte_limit",
        id="page_above_request",
    ),
    pytest.param(
        "max_snapshot_bytes",
        8192,
        "request_overflow_max_snapshot_bytes",
        id="snapshot_equal_request",
    ),
    pytest.param(
        "max_snapshot_bytes",
        64 * MIB + 1,
        "request_overflow_max_snapshot_bytes",
        id="snapshot_above_cap",
    ),
    pytest.param(
        "max_page_count", 0, "request_overflow_max_page_count", id="page_count_zero"
    ),
    pytest.param(
        "max_page_count",
        16385,
        "request_overflow_max_page_count",
        id="page_count_above_cap",
    ),
    pytest.param(
        "max_page_count", True, "request_overflow_max_page_count", id="page_count_bool"
    ),
    pytest.param(
        "max_page_count",
        False,
        "request_overflow_max_page_count",
        id="page_count_false",
    ),
    pytest.param(
        "page_byte_limit",
        "4096",
        "request_overflow_page_byte_limit",
        id="page_limit_string",
    ),
    pytest.param(
        "page_byte_limit",
        True,
        "request_overflow_page_byte_limit",
        id="page_limit_bool",
    ),
]

KEY_SHAPE_VARIANTS = [
    pytest.param("missing_key", id="missing_key"),
    pytest.param("extra_key", id="extra_key"),
]


def _key_shape_mapping(case):
    mapping = dict(BASE_OVERFLOW)
    if case == "missing_key":
        del mapping["max_page_count"]
    else:
        mapping["extra"] = 1
    return mapping


def _source_with_overflow(mapping, request_limit=REQUEST_LIMIT):
    source = source_with_completion_behavior()
    behavior = source["completion_behaviors"][0]
    behavior["request_payload_byte_limit"] = request_limit
    behavior["request_overflow"] = dict(mapping)
    return source


def _valid_overflow_plan(request_limit=REQUEST_LIMIT, **overrides):
    result = compile_workflow(
        _source_with_overflow({**BASE_OVERFLOW, **overrides}, request_limit)
    )
    assert result.plan is not None, result.diagnostics
    return result.plan


def _base_behavior():
    source = source_with_completion_behavior()
    source["completion_behaviors"][0]["request_payload_byte_limit"] = REQUEST_LIMIT
    result = compile_workflow(source)
    assert result.plan is not None, result.diagnostics
    behavior = result.plan.completion_behaviors[0]
    assert type(behavior) is CompletionBehaviorDeclaration
    return behavior


def _tampered_plan(field, value):
    plan = _valid_overflow_plan()
    object.__setattr__(plan.completion_behaviors[0].request_overflow, field, value)
    return plan


def _admit(plan, label):
    return decide(
        empty_runtime_state(),
        AdmitPlan(
            label, selected_plan=plan, authority_fingerprint=authority_fingerprint(plan)
        ),
        deterministic_context(transition_id=f"transition-{label}"),
    )


def _declaration_diagnostics(result):
    return [
        (d.code, d.context.get("reason"), d.declaration_path)
        for d in result.diagnostics
        if d.code == BAD_DECLARATION
    ]


def _encoded_overflow_record():
    plan = _valid_overflow_plan()
    return codecs._encode_completion_behavior(plan.completion_behaviors[0])


def _with_overflow(record, **changes):
    # Shallow copies only: encoded records hold tuples and mappingproxy values.
    return {**record, "request_overflow": {**record["request_overflow"], **changes}}


def _capacity_source():
    source = source_with_completion_behavior()
    for runner in source["runner_bindings"]:
        runner["adapter_kind"] = "pi_rpc"
        runner["component_pin"] = None
        runner["terminal_result_mappings"] = ()
        runner["required_capability_ids"] = EFFECTS
        runner["payload_capacity_pin"] = payload_capacity_pin_record(
            installed_payload_capacity_pin()
        )
    known = {entry["id"] for entry in source["capabilities"]}
    source["capabilities"].extend(
        dict(
            id=name,
            kind="runner.invoke",
            support_status="supported",
            grant_status="granted",
            approval_policy_id=None,
        )
        for name in EFFECTS
        if name not in known
    )
    return source


def _binding_source(checkout="checkout", required=None):
    source = _source_with_overflow(BASE_OVERFLOW)
    source["assets"].append(
        {"id": "lifecycle.context_router", "kind": "template", "body": "Route."}
    )
    required_sources = required or [
        {
            "source_kind": "dispatch_material",
            "source_ref": "current",
            "max_files": 8,
            "max_bytes": 4096,
        }
    ]
    source["context_bindings"] = [
        {
            "id": REVIEW_BINDING_ID,
            "stage_kind_id": "review_stage",
            "router_asset_id": "lifecycle.context_router",
            "checkout_root": checkout,
            "max_hydrated_files": 16,
            "max_hydrated_bytes": 16384,
            "mutation_policy": "forbid_selected_roots",
            "materialization_retention": "until_session_durable_terminal",
            "required_sources": required_sources,
            "discoverable_sources": [
                {
                    "source_kind": "workspace_relative_root",
                    "source_ref": "docs",
                    "max_files": 4,
                    "max_bytes": 2048,
                }
            ],
        }
    ]
    return source


def _binding_plan(checkout="checkout"):
    result = compile_workflow(_binding_source(checkout=checkout))
    assert result.plan is not None, result.diagnostics
    return result.plan


def _overlap_diagnostics(result):
    return [
        (d.code, d.declaration_path)
        for d in result.diagnostics
        if d.code == OVERLAP_CODE
    ]


# ---------------------------------------------------------------- compiler


@pytest.mark.parametrize(("field", "value", "reason"), INVALID_FIELD_VARIANTS)
def test_compile_refuses_invalid_overflow_field(field, value, reason):
    result = compile_workflow(_source_with_overflow({**BASE_OVERFLOW, field: value}))
    assert result.plan is None
    assert _declaration_diagnostics(result) == [
        (BAD_DECLARATION, reason, DECLARATION_PATH)
    ]


@pytest.mark.parametrize("case", KEY_SHAPE_VARIANTS)
def test_compile_refuses_overflow_key_shape(case):
    result = compile_workflow(_source_with_overflow(_key_shape_mapping(case)))
    assert result.plan is None
    assert _declaration_diagnostics(result) == [
        (BAD_DECLARATION, "request_overflow_fields", DECLARATION_PATH)
    ]


@pytest.mark.parametrize(("field", "value", "reason"), INVALID_FIELD_VARIANTS)
def test_shared_predicate_refuses_invalid_overflow_mapping(field, value, reason):
    refusal = closure_request_overflow_refusal(
        {**BASE_OVERFLOW, field: value}, request_payload_byte_limit=REQUEST_LIMIT
    )
    assert refusal == reason


@pytest.mark.parametrize("case", KEY_SHAPE_VARIANTS)
def test_shared_predicate_refuses_overflow_key_shape(case):
    refusal = closure_request_overflow_refusal(
        _key_shape_mapping(case), request_payload_byte_limit=REQUEST_LIMIT
    )
    assert refusal == "request_overflow_fields"


# ---------------------------------------------------------- typed constructor


@pytest.mark.parametrize(("field", "value", "reason"), INVALID_FIELD_VARIANTS)
def test_typed_constructor_refuses_invalid_overflow(field, value, reason):
    declaration = ClosureRequestOverflowDeclaration(**{**BASE_OVERFLOW, field: value})
    with pytest.raises(ValueError) as caught:
        CompletionBehaviorWithRequestOverflowDeclaration.from_behavior(
            _base_behavior(), request_overflow=declaration
        )
    assert str(caught.value) == reason


# ------------------------------------------------------------------ export


@pytest.mark.parametrize(("field", "value", "reason"), INVALID_FIELD_VARIANTS)
def test_export_verifier_refuses_tampered_overflow(field, value, reason):
    raw = compiled_plan_export_bytes(_tampered_plan(field, value))
    with pytest.raises(CompiledPlanExportError) as caught:
        verify_compiled_plan_export_bytes(raw)
    assert str(caught.value) == (
        f"completion behavior request_overflow is invalid: {reason}"
    )


# ------------------------------------------------------------------ kernel


@pytest.mark.parametrize(("field", "value", "reason"), INVALID_FIELD_VARIANTS)
def test_kernel_admission_refuses_tampered_overflow(field, value, reason):
    decision = _admit(_tampered_plan(field, value), f"tampered-{reason}")
    assert decision.accepted is False
    assert decision.refusal.reason == UNSUPPORTED_AUTHORITY
    assert (
        decision.refusal.detail == f"completion_behavior_request_overflow:{BEHAVIOR_ID}"
    )


# ------------------------------------------------------------------- codec


_CODEC_INTEGER_FIELDS = {"page_byte_limit", "max_snapshot_bytes", "max_page_count"}


def _expected_codec_message(field, value, reason):
    # Non-integer values (including bool) in integer fields fail the typed field
    # read first; a zero max_page_count fails the positive-integer read; every other
    # mutation reaches from_behavior and reports its refusal reason.
    if field in _CODEC_INTEGER_FIELDS and (
        isinstance(value, bool) or not isinstance(value, int)
    ):
        return f"CAS object field must be an integer: {field}"
    if field == "max_page_count" and value == 0:
        return f"CAS object field must be a positive integer: {field}"
    return f"invalid completion behavior overflow: {reason}"


@pytest.mark.parametrize(("field", "value", "reason"), INVALID_FIELD_VARIANTS)
def test_codec_refuses_mutated_schema3_record(field, value, reason):
    record = _with_overflow(_encoded_overflow_record(), **{field: value})
    with pytest.raises(InvalidCasObject) as caught:
        codecs._decode_completion_behavior(record)
    assert str(caught.value) == _expected_codec_message(field, value, reason)


@pytest.mark.parametrize("case", ["missing_key", "extra_key"])
def test_codec_refuses_overflow_key_shape(case):
    good = _encoded_overflow_record()
    if case == "missing_key":
        overflow = {
            k: v for k, v in good["request_overflow"].items() if k != "max_page_count"
        }
        record = {**good, "request_overflow": overflow}
        fragment = "missing CAS object fields: max_page_count"
    else:
        record = _with_overflow(good, extra=1)
        fragment = "unexpected CAS object fields: extra"
    with pytest.raises(InvalidCasObject) as caught:
        codecs._decode_completion_behavior(record)
    assert str(caught.value) == fragment


def test_codec_refuses_nested_record_kind():
    record = _with_overflow(_encoded_overflow_record(), record_kind="x")
    with pytest.raises(UnsupportedRecordKind) as caught:
        codecs._decode_completion_behavior(record)
    assert str(caught.value) == (
        "expected record kind closure_request_overflow_declaration, got x"
    )


@pytest.mark.parametrize("version", [2, 4])
def test_codec_refuses_nested_schema_version(version):
    record = _with_overflow(_encoded_overflow_record(), schema_version=version)
    with pytest.raises(UnsupportedSchemaVersion) as caught:
        codecs._decode_completion_behavior(record)
    assert str(caught.value) == f"expected schema version 1, got {version}"


def test_codec_refuses_unknown_future_completion_schema():
    record = {**_encoded_overflow_record(), "schema_version": 4}
    with pytest.raises(UnsupportedSchemaVersion) as caught:
        codecs._decode_completion_behavior(record)
    assert str(caught.value) == "expected schema version 2, got 4"


def test_codec_refuses_top_level_record_kind():
    record = {**_encoded_overflow_record(), "record_kind": "x"}
    with pytest.raises(UnsupportedRecordKind) as caught:
        codecs._decode_completion_behavior(record)
    assert str(caught.value) == (
        "expected record kind completion_behavior_declaration, got x"
    )


def test_codec_refuses_schema2_record_carrying_overflow():
    record = {**_encoded_overflow_record(), "schema_version": 2}
    with pytest.raises(InvalidCasObject) as caught:
        codecs._decode_completion_behavior(record)
    assert str(caught.value) == "unexpected CAS object fields: request_overflow"


def test_codec_refuses_schema3_record_without_overflow():
    good = _encoded_overflow_record()
    record = {k: v for k, v in good.items() if k != "request_overflow"}
    with pytest.raises(InvalidCasObject) as caught:
        codecs._decode_completion_behavior(record)
    assert str(caught.value) == "missing CAS object fields: request_overflow"


# ----------------------------------------------------------- round trips


def test_schema3_round_trip_is_canonical():
    plan = _valid_overflow_plan()
    behavior = plan.completion_behaviors[0]
    assert type(behavior) is CompletionBehaviorWithRequestOverflowDeclaration
    assert behavior.schema_version == 3
    record = codecs._encode_completion_behavior(behavior)
    assert record["schema_version"] == 3 and "request_overflow" in record
    decoded = codecs._decode_completion_behavior(record)
    assert decoded == behavior
    assert canonical_authority_bytes(
        replace(plan, completion_behaviors=(decoded,))
    ) == (canonical_authority_bytes(plan))
    verify_compiled_plan_export_bytes(compiled_plan_export_bytes(plan))
    assert authority_fingerprint(plan) == authority_fingerprint(_valid_overflow_plan())
    assert _admit(plan, "schema3-round-trip").accepted is True


def test_schema2_behavior_is_unchanged_without_overflow():
    plan_a = compile_workflow(source_with_completion_behavior()).plan
    plan_b = compile_workflow(source_with_completion_behavior()).plan
    assert plan_a is not None and plan_b is not None
    behavior = plan_a.completion_behaviors[0]
    assert type(behavior) is CompletionBehaviorDeclaration
    record = codecs._encode_completion_behavior(behavior)
    assert record["schema_version"] == 2 and "request_overflow" not in record
    assert b"request_overflow" not in canonical_authority_bytes(plan_a)
    assert authority_fingerprint(plan_a) == authority_fingerprint(plan_b)
    decoded = codecs._decode_completion_behavior(record)
    assert decoded == behavior
    assert canonical_authority_bytes(
        replace(plan_a, completion_behaviors=(decoded,))
    ) == (canonical_authority_bytes(plan_a))


# ------------------------------------------------------------- boundaries


@pytest.mark.parametrize(
    ("overrides", "request_limit"),
    [
        pytest.param({"page_byte_limit": 4096}, REQUEST_LIMIT, id="page_min"),
        pytest.param(
            {"page_byte_limit": REQUEST_LIMIT}, REQUEST_LIMIT, id="page_equals_request"
        ),
        pytest.param(
            {"max_snapshot_bytes": REQUEST_LIMIT + 1},
            REQUEST_LIMIT,
            id="snapshot_request_plus_one",
        ),
        pytest.param(
            {"max_snapshot_bytes": 64 * MIB}, REQUEST_LIMIT, id="snapshot_cap"
        ),
        pytest.param({"max_page_count": 1}, REQUEST_LIMIT, id="page_count_min"),
        pytest.param({"max_page_count": 16384}, REQUEST_LIMIT, id="page_count_max"),
        pytest.param(
            {"materialization_root": "evidence/closure"},
            REQUEST_LIMIT,
            id="nested_root",
        ),
        pytest.param(
            {"materialization_root": "café/evidence"}, REQUEST_LIMIT, id="nfc_root"
        ),
    ],
)
def test_boundary_values_are_accepted(overrides, request_limit):
    plan = _valid_overflow_plan(request_limit=request_limit, **overrides)
    declaration = plan.completion_behaviors[0].request_overflow
    assert (
        closure_request_overflow_refusal(
            declaration, request_payload_byte_limit=request_limit
        )
        is None
    )
    verify_compiled_plan_export_bytes(compiled_plan_export_bytes(plan))
    assert _admit(plan, "boundary").accepted is True


def test_one_mib_page_boundary_under_pi_capacity():
    def build(page_byte_limit):
        source = _capacity_source()
        behavior = source["completion_behaviors"][0]
        behavior["request_payload_byte_limit"] = MIB
        behavior["request_overflow"] = {
            **BASE_OVERFLOW,
            "page_byte_limit": page_byte_limit,
            "max_snapshot_bytes": 64 * MIB,
        }
        return compile_workflow(source)

    accepted = build(MIB)
    assert accepted.plan is not None, accepted.diagnostics
    refused = build(MIB + 1)
    assert refused.plan is None
    assert _declaration_diagnostics(refused) == [
        (BAD_DECLARATION, "request_overflow_page_byte_limit", DECLARATION_PATH)
    ]


# ---------------------------------------------------------------- overlap


def test_overlap_compile_refuses_checkout_root():
    result = compile_workflow(
        _binding_source(checkout=BASE_OVERFLOW["materialization_root"])
    )
    assert result.plan is None
    assert _overlap_diagnostics(result) == [(OVERLAP_CODE, "context_bindings")]


def test_overlap_compile_refuses_workspace_source_root():
    required = [
        {
            "source_kind": "workspace_relative_root",
            "source_ref": BASE_OVERFLOW["materialization_root"],
            "max_files": 8,
            "max_bytes": 4096,
        }
    ]
    result = compile_workflow(_binding_source(required=required))
    assert result.plan is None
    assert _overlap_diagnostics(result) == [(OVERLAP_CODE, "context_bindings")]


@pytest.mark.parametrize(
    "root",
    [
        pytest.param("checkout", id="checkout_root"),
        pytest.param("docs", id="workspace_source_root"),
    ],
)
def test_overlap_typed_tamper_refused_by_export_and_admission(root):
    plan = _binding_plan(checkout="checkout")
    object.__setattr__(
        plan.completion_behaviors[0].request_overflow, "materialization_root", root
    )
    refusal = f"{OVERLAP_CODE}:{REVIEW_BINDING_ID}"
    assert context_binding_authority_refusal(plan) == refusal
    with pytest.raises(CompiledPlanExportError) as caught:
        verify_compiled_plan_export_bytes(compiled_plan_export_bytes(plan))
    assert str(caught.value) == (
        f"selected context binding authority is invalid: {refusal}"
    )
    decision = _admit(plan, f"overlap-{root}")
    assert decision.accepted is False
    assert decision.refusal.reason == UNSUPPORTED_AUTHORITY
    assert decision.refusal.detail == refusal


def test_sibling_overflow_root_accepted_through_compile_export_and_admission():
    plan = _binding_plan(checkout="checkout")
    assert context_binding_authority_refusal(plan) is None
    verify_compiled_plan_export_bytes(compiled_plan_export_bytes(plan))
    assert _admit(plan, "sibling-root").accepted is True
