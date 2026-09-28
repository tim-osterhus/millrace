from dataclasses import replace

import pytest

from millrace.contracts.runner_payload_capacity import (
    PayloadCapacityError,
    installed_payload_capacity_pin,
    payload_capacity_pin_record,
    validate_payload_capacity_pin,
)


@pytest.mark.parametrize('field,value', [
    ('record_kind', 'wrong'), ('schema_version', True), ('schema_version', 2),
    ('contract_id', 'local'), ('adapter_kind', 'codex'),
    ('descriptor_sha256', '0' * 64), ('max_work_item_payload_bytes', True),
    ('max_work_item_payload_bytes', 1048577), ('extra', 1),
])
def test_exact_installed_pin(field, value):
    pin = installed_payload_capacity_pin()
    record = payload_capacity_pin_record(pin)
    record[field] = value
    with pytest.raises(PayloadCapacityError):
        validate_payload_capacity_pin(record, 'pi_rpc')


def test_missing_and_null_fields_and_wrong_enclosing_adapter():
    record = payload_capacity_pin_record(installed_payload_capacity_pin())
    for key in record:
        missing = dict(record)
        missing.pop(key)
        for bad in (missing, {**record, key: None}):
            with pytest.raises(PayloadCapacityError):
                validate_payload_capacity_pin(bad, 'pi_rpc')
    with pytest.raises(PayloadCapacityError):
        validate_payload_capacity_pin(record, 'codex')


def test_descriptor_is_checked_each_time(monkeypatch, tmp_path):
    from millrace.contracts import runner_payload_capacity as capacity
    pin = installed_payload_capacity_pin()
    monkeypatch.setattr(capacity, 'files', lambda _: tmp_path)
    with pytest.raises(PayloadCapacityError, match='missing_installed'):
        validate_payload_capacity_pin(pin, 'pi_rpc')
    (tmp_path / 'pi-rpc-payload-capacity.v1.json').write_bytes(b'{}\n')
    with pytest.raises(PayloadCapacityError, match='invalid_installed'):
        validate_payload_capacity_pin(pin, 'pi_rpc')


def test_pin_mutation_is_not_capacity_authority():
    pin = installed_payload_capacity_pin()
    with pytest.raises(PayloadCapacityError):
        validate_payload_capacity_pin(
            replace(pin, max_work_item_payload_bytes=2**30), "pi_rpc"
        )
