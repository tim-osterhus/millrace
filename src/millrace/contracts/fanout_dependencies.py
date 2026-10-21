"""Shared compiler/admission checks for selected sibling dependency authority."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256

from millrace.contracts.ids import ActionId


def item_dependency_graph(
    items: tuple[tuple[str, Mapping[object, object]], ...],
    dependency_key: str,
) -> Mapping[str, tuple[str, ...]] | None:
    """Require exact same-batch IDs and an acyclic graph before materialization."""
    keys = {key for key, _ in items}
    graph: dict[str, tuple[str, ...]] = {}
    if len(keys) != len(items) or any(not _text(key) for key in keys):
        return None
    for key, item in items:
        dependencies = item.get(dependency_key)
        if (
            not isinstance(dependencies, tuple)
            or any(
                not _text(item) or item not in keys or item == key
                for item in dependencies
            )
            or len(set(dependencies)) != len(dependencies)
        ):
            return None
        graph[key] = dependencies
    pending = {key: len(dependencies) for key, dependencies in graph.items()}
    successors: dict[str, list[str]] = {key: [] for key in graph}
    for key, dependencies in graph.items():
        for prerequisite in dependencies:
            successors[prerequisite].append(key)
    ready = [key for key, count in pending.items() if count == 0]
    visited = 0
    while ready:
        visited += 1
        for key in successors[ready.pop()]:
            pending[key] -= 1
            if pending[key] == 0:
                ready.append(key)
    return graph if visited == len(graph) else None


def sibling_dependency_id(fanout_record_id: str, prerequisite_key: str) -> str:
    digest = sha256(f"{fanout_record_id}\0{prerequisite_key}".encode()).hexdigest()[:32]
    return f"dependency-sibling:{digest}"


def sibling_dependency_authority_refusal(authority: object) -> str | None:
    """Validate the same typed contract in source, export and admitted IR."""
    schemas = {
        str(_value(item, "id")): _value(item, "schema")
        for item in _sequence(_value(authority, "artifact_schemas"))
    }
    actions = {
        str(_value(item, "id")): item
        for item in _sequence(_value(authority, "terminal_actions"))
    }
    for fanout in _sequence(_value(authority, "fanout_declarations")):
        fanout_id = str(_value(fanout, "id"))
        dependency_key = _value(fanout, "item_dependency_key")
        success_ids = _value(fanout, "dependency_success_action_ids")
        has_contract = (
            "item_dependency_key" in fanout or "dependency_success_action_ids" in fanout
            if isinstance(fanout, Mapping)
            else hasattr(fanout, "item_dependency_key")
        )
        if not has_contract:
            if _value(fanout, "schema_version") == 3:
                return f"missing_dependency_contract:{fanout_id}"
            continue
        version = _value(fanout, "schema_version")
        if version is not None and version != 3:
            return f"dependency_schema_version:{fanout_id}"
        if not _text(dependency_key) or dependency_key == _value(fanout, "item_id_key"):
            return f"item_dependency_key:{fanout_id}"
        if (
            not isinstance(success_ids, (tuple, list))
            or not success_ids
            or any(
                not isinstance(item, (str, ActionId)) or not _text(str(item))
                for item in success_ids
            )
            or len(set(map(str, success_ids))) != len(success_ids)
        ):
            return f"dependency_success_action_ids:{fanout_id}"
        routes = {
            str(_value(route, "id")): route
            for collection in ("external_enqueue_routes", "generated_work_routes")
            for route in _sequence(_value(authority, collection))
        }
        target_route = routes.get(str(_value(fanout, "target_route_id")))
        start_stage = _value(
            fanout, "target_stage_kind_id", _value(target_route, "stage_kind_id")
        )
        reachable = _reachable_stages(actions, str(start_stage))
        for action_id in success_ids:
            action = actions.get(str(action_id))
            if (
                action is None
                or str(action_id) == str(_value(fanout, "source_action_id"))
                or _value(action, "action_kind", _value(action, "kind"))
                not in {"close", "complete_work_item"}
                or str(_value(action, "artifact_schema_id")) not in schemas
                or str(_value(action, "stage_kind_id")) not in reachable
            ):
                return f"dependency_success_action:{fanout_id}:{action_id}"
        schema = schemas.get(str(_value(fanout, "source_artifact_schema_id")))
        for segment in _sequence(_value(fanout, "item_source_path")):
            if not isinstance(schema, Mapping):
                return f"dependency_item_schema:{fanout_id}"
            properties = schema.get("properties")
            schema = (
                properties.get(segment) if isinstance(properties, Mapping) else None
            )
        if not isinstance(schema, Mapping) or schema.get("type") != "array":
            return f"dependency_item_schema:{fanout_id}"
        item_schema = schema.get("items")
        if not isinstance(item_schema, Mapping) or item_schema.get("type") != "object":
            return f"dependency_item_schema:{fanout_id}"
        properties = item_schema.get("properties")
        required = _sequence(item_schema.get("required"))
        id_key = _value(fanout, "item_id_key")
        if (
            not isinstance(properties, Mapping)
            or id_key not in required
            or dependency_key not in required
            or not _nonempty_string_schema(properties.get(id_key))
        ):
            return f"dependency_item_schema:{fanout_id}"
        dependency_schema = properties.get(dependency_key)
        if (
            not isinstance(dependency_schema, Mapping)
            or dependency_schema.get("type") != "array"
            or not _nonempty_string_schema(dependency_schema.get("items"))
        ):
            return f"dependency_list_schema:{fanout_id}"
    return None


def _reachable_stages(actions: Mapping[str, object], start_stage: str) -> set[str]:
    reachable = {start_stage}
    pending = [start_stage]
    while pending:
        stage = pending.pop()
        for action in actions.values():
            if str(_value(action, "stage_kind_id")) != stage or _value(
                action, "action_kind", _value(action, "kind")
            ) not in {"route", "create_incident_route", "recovery_route"}:
                continue
            targets = [_value(action, "target_stage_kind_id")]
            selector = _value(action, "dynamic_target_selector")
            if isinstance(selector, Mapping) and isinstance(
                selector.get("targets"), Mapping
            ):
                targets.extend(
                    _value(target, "target_stage_kind_id")
                    for target in selector["targets"].values()
                )
            for target in targets:
                if isinstance(target, str) or target is not None:
                    target_id = str(target)
                    if target_id not in reachable:
                        reachable.add(target_id)
                        pending.append(target_id)
    return reachable


def _nonempty_string_schema(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("type") == "string"
        and isinstance(value.get("min_length"), int)
        and value["min_length"] >= 1
    )


def _value(record: object, name: str, default: object = None) -> object:
    return (
        record.get(name, default)
        if isinstance(record, Mapping)
        else getattr(record, name, default)
    )


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, (tuple, list)) else ()


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


__all__ = (
    "item_dependency_graph",
    "sibling_dependency_id",
    "sibling_dependency_authority_refusal",
)
