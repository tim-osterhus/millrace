from __future__ import annotations

from copy import deepcopy
from typing import cast

import pytest

from millrace.compiler import (
    compiled_plan_export_record,
    verify_compiled_plan_export_record,
)
from millrace.compiler.canonical import authority_fingerprint
from millrace.compiler.export import CompiledPlanExportError
from millrace.substrate.codecs import (
    decode_selected_compiled_plan,
    encode_selected_compiled_plan,
)
from millrace.substrate.errors import InvalidCasObject
from tests.compiler.test_workflow_package_selection import (
    PACKAGE_ID,
    PACKAGE_VERSION,
    _compile_from_package,
)


def _package_backed_export() -> dict[str, object]:
    return cast(dict[str, object], compiled_plan_export_record(_compile_from_package()))


def _selected_authority(record: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], record["selected_authority"])


def _package_pin(record: dict[str, object]) -> dict[str, object]:
    pin = _selected_authority(record)["workflow_package_pin"]
    assert isinstance(pin, dict)
    return cast(dict[str, object], pin)


def _asset_pin(record: dict[str, object]) -> dict[str, object]:
    pins = cast(list[object], _package_pin(record)["selected_asset_pins"])
    assert pins
    return cast(dict[str, object], pins[0])


def _dependency_pin_payload(record: dict[str, object]) -> list[object]:
    return cast(list[object], _package_pin(record)["selected_dependency_pins"])


def _with_recomputed_fingerprint(record: dict[str, object]) -> dict[str, object]:
    selected = _selected_authority(record)
    record["authority_fingerprint"] = authority_fingerprint(selected)
    return record


def _encoded_payload() -> dict[str, object]:
    envelope = encode_selected_compiled_plan(_compile_from_package())
    return cast(dict[str, object], dict(envelope.payload))


def test_compiled_plan_export_includes_selected_package_pins() -> None:
    record = _package_backed_export()
    pin = _package_pin(record)
    asset_pin = _asset_pin(record)

    assert pin == {
        "record_kind": "selected_workflow_package_pin",
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "package_version": PACKAGE_VERSION,
        "package_format_version": "1",
        "workflow_id": "wf.package",
        "workflow_version": "1",
        "entrypoint": "default",
        "selected_asset_pins": [asset_pin],
        "selected_dependency_pins": [],
    }
    assert asset_pin["record_kind"] == "selected_workflow_package_asset_pin"
    assert asset_pin["schema_version"] == 1
    assert asset_pin["asset_id"] == "asset.prompt"
    assert str(asset_pin["content_digest"]).startswith("sha256:")


def test_selected_export_omits_source_provenance_import_record_status_audit_and_cas_digests() -> None:  # noqa: E501
    record = _package_backed_export()
    encoded = str(record).encode("utf-8")

    forbidden = (
        b"manifest_digest",
        b"package_digest",
        b"import_record_digest",
        b"source_kind",
        b"publication_scope",
        b"source_provenance",
        b"status_generation",
        b"latest_audit_id",
        b"manifest_cas_digest",
        b"cas_digest",
        b"package_generation",
    )
    assert [fragment for fragment in forbidden if fragment in encoded] == []


def test_compiled_plan_export_refuses_missing_selected_package_pin_fields() -> None:
    record = _package_backed_export()
    _package_pin(record).pop("package_id")

    with pytest.raises(
        CompiledPlanExportError,
        match="missing workflow_package_pin key: package_id",
    ):
        verify_compiled_plan_export_record(_with_recomputed_fingerprint(record))


def test_compiled_plan_export_refuses_missing_selected_asset_pin_fields() -> None:
    record = _package_backed_export()
    _asset_pin(record).pop("content_digest")

    with pytest.raises(
        CompiledPlanExportError,
        match="missing selected_asset_pins key: content_digest",
    ):
        verify_compiled_plan_export_record(_with_recomputed_fingerprint(record))


def test_compiled_plan_export_refuses_malformed_selected_asset_pin_fields() -> None:
    record = _package_backed_export()
    _asset_pin(record)["content_digest"] = "not-a-digest"

    with pytest.raises(
        CompiledPlanExportError,
        match="selected asset content_digest must be a sha256 digest",
    ):
        verify_compiled_plan_export_record(_with_recomputed_fingerprint(record))


def test_compiled_plan_export_refuses_duplicate_selected_asset_or_dependency_pins() -> None:  # noqa: E501
    duplicate_asset_record = _package_backed_export()
    pin = _package_pin(duplicate_asset_record)
    asset_pin = deepcopy(_asset_pin(duplicate_asset_record))
    cast(list[object], pin["selected_asset_pins"]).append(asset_pin)

    with pytest.raises(
        CompiledPlanExportError,
        match="duplicate selected asset pin",
    ):
        verify_compiled_plan_export_record(
            _with_recomputed_fingerprint(duplicate_asset_record)
        )

    duplicate_dependency_record = _package_backed_export()
    dependency_pin = {
        "record_kind": "selected_workflow_package_dependency_pin",
        "schema_version": 1,
        "package_id": "pkg.example.dep",
        "package_version": "2.0.0",
        "package_format_version": "1",
    }
    deps = _dependency_pin_payload(duplicate_dependency_record)
    deps.extend([dependency_pin, deepcopy(dependency_pin)])

    with pytest.raises(
        CompiledPlanExportError,
        match="duplicate selected dependency pin",
    ):
        verify_compiled_plan_export_record(
            _with_recomputed_fingerprint(duplicate_dependency_record)
        )


def test_compiled_plan_export_refuses_malformed_digest_fields() -> None:
    record = _package_backed_export()
    _asset_pin(record)["content_digest"] = "sha256:" + ("z" * 64)

    with pytest.raises(CompiledPlanExportError, match="sha256 digest"):
        verify_compiled_plan_export_record(_with_recomputed_fingerprint(record))


def test_compiled_plan_export_refuses_package_or_workflow_pin_mismatch() -> None:
    record = _package_backed_export()
    _package_pin(record)["workflow_id"] = "wf.other"

    with pytest.raises(
        CompiledPlanExportError,
        match="workflow_package_pin workflow mismatch",
    ):
        verify_compiled_plan_export_record(_with_recomputed_fingerprint(record))


def test_substrate_codec_refuses_missing_or_malformed_selected_package_pin_fields() -> None:  # noqa: E501
    envelope = encode_selected_compiled_plan(_compile_from_package())
    payload = dict(envelope.payload)
    pin = dict(cast(dict[str, object], payload["workflow_package_pin"]))
    pin.pop("package_id")
    payload["workflow_package_pin"] = pin

    with pytest.raises(InvalidCasObject, match="missing CAS object fields"):
        decode_selected_compiled_plan(replace_payload(envelope, payload))


def test_substrate_codec_refuses_missing_or_malformed_selected_asset_pin_fields() -> None:  # noqa: E501
    envelope = encode_selected_compiled_plan(_compile_from_package())
    payload = dict(envelope.payload)
    pin = dict(cast(dict[str, object], payload["workflow_package_pin"]))
    asset_pins = list(cast(tuple[object, ...], pin["selected_asset_pins"]))
    asset_pin = dict(cast(dict[str, object], asset_pins[0]))
    asset_pin["content_digest"] = "not-a-digest"
    asset_pins[0] = asset_pin
    pin["selected_asset_pins"] = tuple(asset_pins)
    payload["workflow_package_pin"] = pin

    with pytest.raises(InvalidCasObject, match="sha256 digest"):
        decode_selected_compiled_plan(replace_payload(envelope, payload))


def test_substrate_codec_refuses_forbidden_registry_fields_inside_workflow_package_pin() -> None:  # noqa: E501
    envelope = encode_selected_compiled_plan(_compile_from_package())
    payload = dict(envelope.payload)
    pin = dict(cast(dict[str, object], payload["workflow_package_pin"]))
    pin["manifest_digest"] = "sha256:" + ("1" * 64)
    payload["workflow_package_pin"] = pin

    with pytest.raises(InvalidCasObject, match="unexpected CAS object fields"):
        decode_selected_compiled_plan(replace_payload(envelope, payload))


def test_selected_package_pins_round_trip_through_export_verification() -> None:
    plan = _compile_from_package()
    record = cast(dict[str, object], compiled_plan_export_record(plan))

    verified = verify_compiled_plan_export_record(record)

    assert verified.authority_fingerprint == authority_fingerprint(plan)
    assert verified.selected_authority == record["selected_authority"]


def test_selected_package_pins_round_trip_through_substrate_codecs() -> None:
    plan = _compile_from_package()

    decoded = decode_selected_compiled_plan(encode_selected_compiled_plan(plan))

    assert decoded == plan
    assert decoded.workflow_package_pin == plan.workflow_package_pin


def test_exported_package_backed_plan_reloads_with_same_authority_fingerprint() -> None:
    plan = _compile_from_package()
    record = cast(dict[str, object], compiled_plan_export_record(plan))
    verified = verify_compiled_plan_export_record(record)

    assert authority_fingerprint(verified.selected_authority) == authority_fingerprint(
        plan
    )


def replace_payload(envelope, payload: dict[str, object]):
    from dataclasses import replace

    return replace(envelope, payload=payload)
