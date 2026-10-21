"""Reject declared ordinary ingress whose selected terminal options need an attempt.

This checks declared authority only. Dispatch and result resolution still validate
the exact current recovery attempt and any artifact-dependent action conditions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

ATTEMPT_OWNED_ACTION_KINDS = frozenset(
    {"return_to_recorded_source", "quarantine_lineage"}
)


def terminal_ingress_authority_refusal(authority: object) -> str | None:
    """Use the same finite ingress rule for normalized source, export and plan IR."""
    stages = {
        str(_value(stage, "id")): stage for stage in _records(authority, "stage_kinds")
    }
    bindings = {
        str(_value(binding, "id")): binding
        for binding in _records(authority, "runner_bindings")
    }
    actions = _records(authority, "terminal_actions")
    visible_outcomes = {
        (str(_value(outcome, "stage_kind_id")), str(_value(outcome, "id")))
        for outcome in _records(authority, "terminal_outcomes")
        if isinstance(_value(outcome, "marker"), str)
        and str(_value(outcome, "marker")).strip()
    }
    formal_stage_ids = {
        str(_value(action, "stage_kind_id"))
        for action in actions
        if _action_kind(action) in ATTEMPT_OWNED_ACTION_KINDS
    }
    ordinary_exits: set[str] = set()
    for stage_id in formal_stage_ids:
        stage = stages.get(stage_id)
        if stage is None:
            continue  # The existing reference validator owns missing stage IDs.
        declared = {
            str(item) for item in _sequence(_value(stage, "declared_outcome_ids"))
        }
        binding = bindings.get(str(_value(stage, "runner_binding_id")))
        if binding is not None and _value(binding, "component_pin") is not None:
            declared &= {
                str(_value(mapping, "outcome_id"))
                for mapping in _records(binding, "terminal_result_mappings")
                if str(_value(mapping, "stage_kind_id")) == stage_id
            }
        if any(
            str(_value(action, "stage_kind_id")) == stage_id
            and str(_value(action, "outcome_id")) in declared
            and (stage_id, str(_value(action, "outcome_id"))) in visible_outcomes
            and _action_kind(action) not in ATTEMPT_OWNED_ACTION_KINDS
            for action in actions
        ):
            ordinary_exits.add(stage_id)

    for owner_id, target_stage_id in _ordinary_targets(authority):
        if (
            target_stage_id in formal_stage_ids
            and target_stage_id not in ordinary_exits
        ):
            return (
                "ordinary_ingress_requires_terminal_option:"
                f"{owner_id}:{target_stage_id}"
            )
    return None


def _ordinary_targets(authority: object) -> tuple[tuple[str, str], ...]:
    targets: list[tuple[str, str]] = []
    for action in _records(authority, "terminal_actions"):
        if _action_kind(action) not in {"route", "create_incident_route"}:
            continue
        owner = str(_value(action, "id"))
        targets.append((owner, str(_value(action, "target_stage_kind_id"))))
        selector = _value(action, "dynamic_target_selector")
        variants = _value(selector, "targets")
        if isinstance(variants, Mapping):
            targets.extend(
                (owner, str(_value(target, "target_stage_kind_id")))
                for target in variants.values()
            )
    for section in ("external_enqueue_routes", "generated_work_routes"):
        targets.extend(
            (str(_value(route, "id")), str(_value(route, "stage_kind_id")))
            for route in _records(authority, section)
        )
    for section in ("fanout_declarations", "remediation_policies", "operator_waits"):
        targets.extend(
            (str(_value(record, "id")), str(_value(record, "target_stage_kind_id")))
            for record in _records(authority, section)
        )
    targets.extend(
        (str(_value(behavior, "id")), str(_value(behavior, "target_stage_kind_id")))
        for behavior in _records(authority, "completion_behaviors")
    )
    return tuple(targets)


def _action_kind(action: object) -> object:
    return _value(action, "action_kind", _value(action, "kind"))


def _value(record: object, name: str, default: object = None) -> object:
    return (
        record.get(name, default)
        if isinstance(record, Mapping)
        else getattr(record, name, default)
    )


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, (tuple, list)) else ()


def _records(authority: object, section: str) -> Sequence[object]:
    return _sequence(_value(authority, section))


__all__ = ("ATTEMPT_OWNED_ACTION_KINDS", "terminal_ingress_authority_refusal")
