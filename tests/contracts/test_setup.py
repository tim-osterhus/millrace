from __future__ import annotations

import copy

import pytest

from millrace.adapters.cli.setup import SetupService
from millrace.contracts.setup import SetupRefusal, SetupRequest, canonical, digest


def test_owner_resolves_response_without_caller_trusted_facts():
    service = SetupService()
    wire = service.inspect().to_wire()
    assert service.validate_response(wire) == service.inspect()
    wire["status"] = "ready"
    with pytest.raises(SetupRefusal, match="authority_mismatch"):
        service.validate_response(wire)


@pytest.mark.parametrize(
    "field",
    [
        "workspace",
        "package_id",
        "package_version",
        "source_digest",
        "import_record_digest",
        "manifest_digest",
        "plan_fingerprint",
        "local_config_digest",
        "runtime_identity",
        "demo_command_identity",
        "component_pins",
        "action_kind",
        "management_mode",
        "provider_identity",
        "managed_mapping",
        "context_digest",
        "declared_next_action",
    ],
)
def test_selection_substitution_refuses_even_with_recomputed_identity(field):
    service = SetupService()
    value = copy.deepcopy(service.inspect().to_wire())
    value["selection"][field] = "substituted"
    value["selection_digest"] = digest(value["selection"])
    with pytest.raises(SetupRefusal):
        service.validate_response(value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner", "Other"),
        ("producer_binding", "Other.demo.command.v1"),
        ("category", "context"),
        ("required", False),
        ("gates_next_action", False),
        ("affected_identity", "sha256:" + "a" * 64),
        ("evidence_reference", "sha256:" + "b" * 64),
        ("observed_at", "yesterday"),
        ("disposition", "pass"),
    ],
)
def test_check_requires_separate_owner_observation(field, value):
    service = SetupService()
    wire = service.inspect().to_wire()
    item = wire["checks"][0]
    item[field] = value
    # Attack recomputes public correlation; it cannot create an owner observation.
    item["evidence_reference"] = (
        value
        if field == "evidence_reference"
        else digest(
            {
                "selection": wire["selection"],
                "check_id": item["id"],
                **{
                    key: item[key]
                    for key in [
                        "owner",
                        "category",
                        "required",
                        "gates_next_action",
                        "disposition",
                        "observed_at",
                    ]
                },
            }
        )
    )
    with pytest.raises(SetupRefusal):
        service.validate_response(wire)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 2),
        ("schema_version", True),
        ("status", "ready"),
        ("checks", []),
        ("blockers", []),
        ("next_action", "millrace demo"),
        ("trust_acceptance", {"accepted": True}),
        ("trust_disclosure", {"owner": "Core"}),
        ("context_coverage", [{"stage": "invented"}]),
        ("proposed_actions", [{"operation": "accept_trust"}]),
        ("unknown", None),
    ],
)
def test_closed_staged_transport(field, value):
    service = SetupService()
    wire = service.inspect().to_wire()
    wire[field] = value
    with pytest.raises(SetupRefusal):
        service.validate_response(wire)


def test_missing_and_duplicate_required_checks_refuse():
    service = SetupService()
    for mutate in (
        lambda c: c.remove(next(x for x in c if x["id"] == "demo.runtime")),
        lambda c: c.append(c[0]),
    ):
        wire = service.inspect().to_wire()
        mutate(wire["checks"])
        with pytest.raises(SetupRefusal):
            service.validate_response(wire)


@pytest.mark.parametrize(
    "options",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"management_mode": "managed"},
        {"action_kind": "factory"},
        {"workspace": "/selected"},
        {"plan_fingerprint": "fake"},
    ],
)
def test_unknown_or_excluded_request_refuses(options):
    with pytest.raises(SetupRefusal):
        SetupRequest(**options)


def test_canonical_identity_is_exact_and_not_authentication():
    assert canonical({"b": 1, "a": "é"}) == '{"a":"é","b":1}'.encode()
    assert digest({"x": 1}) != digest({"x": True})


@pytest.mark.parametrize("missing_id", ["demo.command", "demo.package", "demo.runtime"])
def test_exact_missing_demo_required_check(missing_id):
    service = SetupService()
    wire = service.inspect().to_wire()
    assert any(c["id"] == missing_id for c in wire["checks"])
    wire["checks"] = [c for c in wire["checks"] if c["id"] != missing_id]
    assert any(c["id"] == "trust.acceptance" for c in wire["checks"])
    with pytest.raises(SetupRefusal):
        service.validate_response(wire)


def test_useful_with_only_demo_checks(tmp_path):
    service = SetupService(
        SetupRequest(action_kind="useful_recipe", workspace=str(tmp_path))
    )
    wire = service.inspect().to_wire()
    demo = SetupService().inspect().to_wire()
    assert {c["id"] for c in wire["checks"]} >= {
        "recipe.plan",
        "recipe.context",
        "recipe.runner",
    }
    wire["checks"] = [c for c in demo["checks"] if c["id"].startswith("demo.")]
    for c in wire["checks"]:
        c["affected_identity"] = wire["selection_digest"]
        c["evidence_reference"] = _wire_check_evidence(wire, c)
    assert {c["id"] for c in wire["checks"]} == {
        "demo.command",
        "demo.package",
        "demo.runtime",
    }
    with pytest.raises(SetupRefusal):
        service.validate_response(wire)


def _wire_check_evidence(wire, check):
    return digest(
        {
            "selection": wire["selection"],
            "check_id": check["id"],
            **{
                k: check[k]
                for k in (
                    "owner",
                    "category",
                    "required",
                    "gates_next_action",
                    "disposition",
                    "observed_at",
                )
            },
        }
    )


@pytest.mark.parametrize(
    "disposition,required,gating",
    [
        ("block", False, False),
        ("deferred", False, True),
        ("deferred", True, False),
    ],
    ids=["extra-blockFalseFalse", "extra-deferredFalseTrue", "extra-deferredTrueFalse"],
)
def test_exact_extra_check(disposition, required, gating):
    service = SetupService()
    wire = service.inspect().to_wire()
    extra = dict(next(c for c in wire["checks"] if c["id"] == "demo.command"))
    extra.update(
        id="extra", disposition=disposition, required=required, gates_next_action=gating
    )
    extra["evidence_reference"] = _wire_check_evidence(wire, extra)
    wire["checks"].append(extra)
    with pytest.raises(SetupRefusal):
        service.validate_response(wire)


@pytest.mark.parametrize(
    "case,field,value",
    [
        ("wrong-check-category", "category", "component"),
        ("check-affected_identity", "affected_identity", "unrelated-package"),
        ("check-evidence_reference", "evidence_reference", "unrelated-evidence"),
        ("check-owner", "owner", "Plus"),
        ("check-producer_binding", "producer_binding", "Plus.demo.command.v1"),
        ("check-observed_at", "observed_at", "stale"),
        ("typed-wrong-check-identity", "affected_identity", "sha256:" + "0" * 64),
    ],
    ids=[
        "wrong-check-category",
        "check-affected_identity",
        "check-evidence_reference",
        "check-owner",
        "check-producer_binding",
        "check-observed_at",
        "typed-wrong-check-identity",
    ],
)
def test_exact_retained_owner_field(case, field, value):
    service = SetupService()
    wire = service.inspect().to_wire()
    check = next(c for c in wire["checks"] if c["id"] == "demo.command")
    assert check[field] != value
    check[field] = value
    if field in {"owner", "category", "observed_at"}:
        check["evidence_reference"] = _wire_check_evidence(wire, check)
    with pytest.raises(SetupRefusal):
        service.validate_response(wire)


def test_empty_component_pins_is_actual_removal():
    service = SetupService()
    wire = service.inspect().to_wire()
    assert wire["selection"]["component_pins"]
    wire["selection"]["component_pins"] = []
    wire["selection_digest"] = digest(wire["selection"])
    with pytest.raises(SetupRefusal):
        service.validate_response(wire)


@pytest.mark.parametrize("kind", ["demo", "useful_recipe"])
def test_exact_wrong_handoff(kind, tmp_path):
    request = (
        SetupRequest()
        if kind == "demo"
        else SetupRequest(action_kind="useful_recipe", workspace=str(tmp_path))
    )
    service = SetupService(request)
    wire = service.inspect().to_wire()
    wire["next_action"] = "wrong action"
    with pytest.raises(SetupRefusal):
        service.validate_response(wire)


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace", "x" * 513),
        ("package_id", "x" * 513),
        ("package_version", 123),
        ("source_digest", "bad-digest"),
        ("declared_next_action", "x" * 257),
        ("management_mode", "managed"),
        ("component_pins", ()),
        ("action_kind", "unknown"),
    ],
)
def test_resolved_selection_shape_before_serialization(field, value):
    from dataclasses import replace

    response = SetupService().inspect()
    with pytest.raises(SetupRefusal, match="setup_wire_unrepresentable"):
        replace(
            response, selection=replace(response.selection, **{field: value})
        ).to_wire()


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "x" * 513),
        ("owner", "x" * 513),
        ("observed_at", "x" * 4097),
        ("required", 1),
        ("gates_next_action", 0),
        ("disposition", "unknown"),
        ("category", "unknown"),
        ("producer_binding", ""),
        ("evidence_reference", "bad-digest"),
    ],
)
def test_emitted_check_shape_before_serialization(field, value):
    from dataclasses import replace

    response = SetupService().inspect()
    check = replace(response.checks[0], **{field: value})
    with pytest.raises(SetupRefusal, match="setup_wire_unrepresentable"):
        replace(response, checks=(check, *response.checks[1:])).to_wire()


@pytest.mark.parametrize(
    "field,value",
    [
        ("code", ""),
        ("affected_identity", "x" * 513),
        ("explanation", "x" * 4097),
        ("safe_next_action", None),
        ("retry_meaningful", 1),
        ("progress_valid", 0),
    ],
)
def test_emitted_blocker_shape_before_serialization(field, value):
    from dataclasses import replace

    response = SetupService().inspect()
    blocker = replace(response.blockers[0], **{field: value})
    with pytest.raises(SetupRefusal, match="setup_wire_unrepresentable"):
        replace(response, blockers=(blocker, *response.blockers[1:])).to_wire()


@pytest.mark.parametrize(
    "field,value",
    [
        ("stage_id", ""),
        ("stage_id", "x" * 513),
        ("binding_id", None),
        ("conditional", 1),
        ("router_asset_id", "x" * 513),
        ("checkout_root", "x" * 4097),
        ("max_hydrated_files", True),
        ("max_hydrated_bytes", 0),
        ("materialization_retention", "forever"),
        ("mutation_policy", "allow_all"),
        ("sources", []),
        ("write_rules", []),
        ("writeback_terminal_action_id", "x" * 513),
        ("writeback_artifact_schema_id", 1),
    ],
)
def test_context_coverage_has_closed_types_and_bounds(field, value):
    from dataclasses import replace

    from millrace.contracts.setup import StageContextCoverage

    stage = StageContextCoverage(
        "stage",
        "binding",
        False,
        "router",
        "checkout",
        1,
        1,
        "until_session_durable_terminal",
        "forbid_selected_roots",
        (),
        (),
        None,
        None,
    )
    with pytest.raises(SetupRefusal, match="setup_wire_unrepresentable"):
        replace(stage, **{field: value}).validate()


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_kind", "other"),
        ("source_ref", ""),
        ("source_ref", "x" * 513),
        ("required", 1),
        ("max_files", True),
        ("max_files", 0),
        ("max_bytes", 0),
        ("empty_policy", "ignore"),
        ("disposition", "ready"),
        ("evidence_reference", "sha256:wrong"),
    ],
)
def test_context_source_has_closed_types_and_bounds(field, value):
    from dataclasses import replace

    from millrace.contracts.setup import ContextSourceCoverage

    source = ContextSourceCoverage(
        "workspace_relative_root",
        "docs",
        True,
        1,
        1,
        "require_nonempty",
        "pass",
        "sha256:" + "0" * 64,
    )
    with pytest.raises(SetupRefusal, match="setup_wire_unrepresentable"):
        replace(source, **{field: value}).validate()


@pytest.mark.parametrize(
    "field,value",
    [
        ("relative_root", ""),
        ("relative_root", "x" * 4097),
        ("disposition", "write_all"),
    ],
)
def test_context_write_rule_has_closed_types_and_bounds(field, value):
    from dataclasses import replace

    from millrace.contracts.setup import ContextWriteCoverage

    rule = ContextWriteCoverage("docs", "direct_write")
    with pytest.raises(SetupRefusal, match="setup_wire_unrepresentable"):
        replace(rule, **{field: value}).validate()
