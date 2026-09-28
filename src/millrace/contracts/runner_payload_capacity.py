"""Core-owned component-free payload capacity; no provider or config execution."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
from typing import ClassVar

_DESCRIPTOR_BYTES = (
    b'{"adapter_kind":"pi_rpc","contract_id":"millrace.pi-rpc.payload.v'
    b'1","max_work_item_payload_bytes":1048576,"payload_encoding":"cano'
    b'nical_authority_mapping_bytes","record_kind":"runner_payload_capa'
    b'city_descriptor","schema_version":1}\n'
)


class PayloadCapacityError(ValueError):
    """A selected capacity cannot authorize this request."""


def installed_payload_capacity_pin() -> RunnerPayloadCapacityPin:
    """Read on every boundary so changed/missing packaged authority fails closed."""
    try:
        data = files("millrace.contracts").joinpath(
            "pi-rpc-payload-capacity.v1.json"
        ).read_bytes()
    except (OSError, ModuleNotFoundError) as exc:
        raise PayloadCapacityError("missing_installed_payload_capacity") from exc
    if data != _DESCRIPTOR_BYTES:
        raise PayloadCapacityError("invalid_installed_payload_capacity")
    descriptor = json.loads(data)
    return RunnerPayloadCapacityPin(
        contract_id=descriptor["contract_id"],
        adapter_kind=descriptor["adapter_kind"],
        descriptor_sha256=sha256(data).hexdigest(),
        max_work_item_payload_bytes=descriptor["max_work_item_payload_bytes"],
    )


@dataclass(frozen=True, slots=True)
class RunnerPayloadCapacityPin:
    record_kind: ClassVar[str] = "runner_payload_capacity_pin"
    schema_version: ClassVar[int] = 1
    contract_id: str
    adapter_kind: str
    descriptor_sha256: str
    max_work_item_payload_bytes: int


def payload_capacity_pin_record(pin: RunnerPayloadCapacityPin) -> dict[str, object]:
    return {
        "record_kind": pin.record_kind,
        "schema_version": pin.schema_version,
        "contract_id": pin.contract_id,
        "adapter_kind": pin.adapter_kind,
        "descriptor_sha256": pin.descriptor_sha256,
        "max_work_item_payload_bytes": pin.max_work_item_payload_bytes,
    }


def validate_payload_capacity_pin(value: object, adapter_kind: object) -> int:
    expected = payload_capacity_pin_record(installed_payload_capacity_pin())
    if type(value) is RunnerPayloadCapacityPin:
        actual = payload_capacity_pin_record(value)
    elif isinstance(value, Mapping):
        actual = dict(value)
    else:
        raise PayloadCapacityError("invalid_runner_payload_capacity_pin")
    if (
        actual.keys() != expected.keys()
        or any(type(actual[k]) is not type(v) or actual[k] != v
               for k, v in expected.items())
        or adapter_kind != expected["adapter_kind"]
    ):
        raise PayloadCapacityError("invalid_runner_payload_capacity_pin")
    return installed_payload_capacity_pin().max_work_item_payload_bytes


def decode_payload_capacity_pin(value: object) -> RunnerPayloadCapacityPin:
    validate_payload_capacity_pin(value, "pi_rpc")
    return installed_payload_capacity_pin()


def _field(value: object, key: str, default: object = None) -> object:
    return (
        value.get(key, default)
        if isinstance(value, Mapping)
        else getattr(value, key, default)
    )


def validate_payload_capacity_binding(binding: object) -> None:
    # Source mappings omit the nested header. Serialized mappings require their
    # exact header/keys at their codec boundary.
    from millrace.contracts.compiled_plan import (
        RunnerBindingDeclaration,
        RunnerBindingWithPayloadCapacityDeclaration,
    )
    if isinstance(binding, Mapping):
        present = "payload_capacity_pin" in binding
        version = binding.get("schema_version", 4 if present else 3)
        if type(version) is not int or version not in (3, 4):
            raise PayloadCapacityError("invalid_runner_binding_version")
    else:
        if type(binding) not in (
            RunnerBindingDeclaration,
            RunnerBindingWithPayloadCapacityDeclaration,
        ):
            raise PayloadCapacityError("invalid_runner_binding_type")
        present = type(binding) is RunnerBindingWithPayloadCapacityDeclaration
        version = _field(binding, "schema_version")
    if (version == 4) != present:
        raise PayloadCapacityError("invalid_runner_binding_capacity_shape")
    if present:
        if (
            not isinstance(binding, Mapping)
            and type(_field(binding, "payload_capacity_pin"))
            is not RunnerPayloadCapacityPin
        ):
            raise PayloadCapacityError("invalid_runner_payload_capacity_pin_type")
        if _field(binding, "component_pin") is not None or _field(
            binding, "terminal_result_mappings", ()
        ):
            raise PayloadCapacityError(
                "payload_capacity_requires_component_free_runner"
            )
        validate_payload_capacity_pin(
            _field(binding, "payload_capacity_pin"), _field(binding, "adapter_kind")
        )


def completion_capacity(binding: object, request_limit: object) -> int:
    """Shared compiler/export/kernel predicate, including reconstructed records."""
    if binding is None:
        raise PayloadCapacityError("missing_runner_binding_capacity")
    validate_payload_capacity_binding(binding)
    component = _field(binding, "component_pin")
    if component is not None:
        capacity = _field(component, "max_work_item_payload_bytes")
        if capacity is None:
            raise PayloadCapacityError("missing_runner_payload_capacity")
        if type(capacity) is not int or capacity <= 0:
            raise PayloadCapacityError("invalid_runner_payload_capacity")
    else:
        pin = _field(binding, "payload_capacity_pin")
        if pin is None:
            raise PayloadCapacityError("missing_runner_component_pin")
        capacity = validate_payload_capacity_pin(pin, _field(binding, "adapter_kind"))
    if type(request_limit) is not int or request_limit <= 0:
        raise PayloadCapacityError("invalid_request_payload_byte_limit")
    if request_limit > capacity:
        raise PayloadCapacityError("request_payload_byte_limit_exceeds_runner_capacity")
    return capacity
