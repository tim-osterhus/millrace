from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from pathlib import Path

import pytest

import millrace.contracts as contracts
from millrace.adapters.cli.context_checkout import PreparedContextCheckout
from millrace.contracts import (
    ContextCheckoutFile,
    ContextCheckoutLegacyManifest,
    ContextCheckoutManifest,
    ContextCheckoutOmission,
    ContextCheckoutRootState,
    context_checkout_manifest_digest,
    decode_context_checkout_manifest,
    encode_context_checkout_manifest,
)
from millrace.contracts.context_checkout import ContextCheckoutCatalogEntry


def _manifest() -> ContextCheckoutManifest:
    return ContextCheckoutManifest(
        session_id="session-1",
        dispatch_generation=1,
        plan_fingerprint="sha256:" + "a" * 64,
        binding_id="binding-1",
        router_asset_id="router-1",
        files=(
            ContextCheckoutFile(
                checkout_path="a.txt",
                source_kind="selected_router",
                source_ref="router-1",
                content_digest="sha256:" + "c" * 64,
                byte_length=3,
                required=True,
            ),
        ),
        catalog=(
            ContextCheckoutCatalogEntry(
                logical_path="b.txt",
                source_kind="workspace_relative_root",
                source_ref="docs",
                content_digest="sha256:" + "b" * 64,
                byte_length=2,
                provenance_ids=("workspace:docs/b.txt",),
            ),
        ),
        omissions=(
            ContextCheckoutOmission(
                source_kind="workspace_relative_root",
                source_ref="missing",
                reason="source_missing",
            ),
        ),
    )


def test_context_checkout_contracts_are_frozen_slotted_and_public() -> None:
    for record in (
        ContextCheckoutFile,
        ContextCheckoutOmission,
        ContextCheckoutCatalogEntry,
        ContextCheckoutManifest,
        PreparedContextCheckout,
    ):
        assert is_dataclass(record)
        assert record.__dataclass_params__.frozen is True
        assert hasattr(record, "__slots__")

    manifest = _manifest()
    with pytest.raises(FrozenInstanceError):
        manifest.session_id = "changed"  # type: ignore[misc]

    assert tuple(field.name for field in fields(PreparedContextCheckout)) == (
        "manifest",
        "manifest_digest",
        "materialized_checkout_root",
    )
    assert "PreparedContextCheckout" not in contracts.__all__
    assert not hasattr(contracts, "PreparedContextCheckout")


def test_context_checkout_package_hides_contract_exception() -> None:
    assert "ContextCheckoutContractError" not in contracts.__all__
    assert not hasattr(contracts, "ContextCheckoutContractError")


def test_prepared_context_checkout_refuses_manifest_digest_mismatch() -> None:
    with pytest.raises(ValueError):
        PreparedContextCheckout(
            manifest=_manifest(),
            manifest_digest="sha256:" + "0" * 64,
            materialized_checkout_root=Path("checkout"),
        )


def test_context_checkout_manifest_uses_canonical_bytes_and_raw_digest() -> None:
    manifest = _manifest()

    raw = encode_context_checkout_manifest(manifest)
    assert raw == encode_context_checkout_manifest(
        ContextCheckoutManifest(
            session_id=manifest.session_id,
            dispatch_generation=manifest.dispatch_generation,
            plan_fingerprint=manifest.plan_fingerprint,
            binding_id=manifest.binding_id,
            router_asset_id=manifest.router_asset_id,
            files=tuple(reversed(manifest.files)),
            catalog=tuple(reversed(manifest.catalog)),
            omissions=manifest.omissions,
        )
    )
    assert raw == (
        b'{"binding_id":"binding-1","catalog":[{"byte_length":2,'
        b'"content_digest":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
        b'"logical_path":"b.txt","provenance_ids":["workspace:docs/b.txt"],'
        b'"source_kind":"workspace_relative_root","source_ref":"docs"}],"'
        b'dispatch_generation":1,"files":[{"byte_length":3,"checkout_path":"a.txt",'
        b'"content_digest":"sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",'
        b'"required":true,"source_kind":"selected_router","source_ref":"router-1"}],'
        b'"omissions":[{"reason":"source_missing","source_kind":"workspace_relative_root",'
        b'"source_ref":"missing"}],"plan_fingerprint":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'"record_kind":"millrace.context_checkout_manifest","root_states":[],'
        b'"router_asset_id":"router-1","schema_version":3,"session_id":"session-1"}'
    )
    assert decode_context_checkout_manifest(raw) == ContextCheckoutManifest(
        session_id="session-1",
        dispatch_generation=1,
        plan_fingerprint="sha256:" + "a" * 64,
        binding_id="binding-1",
        router_asset_id="router-1",
        files=tuple(sorted(manifest.files, key=lambda item: item.checkout_path)),
        catalog=manifest.catalog,
        omissions=manifest.omissions,
    )
    assert context_checkout_manifest_digest(manifest) == (
        "sha256:" + __import__("hashlib").sha256(raw).hexdigest()
    )


def test_encoding_revalidates_a_tampered_frozen_record() -> None:
    manifest = _manifest()
    object.__setattr__(manifest.files[0], "content_digest", "sha256:BAD")

    with pytest.raises(ValueError):
        encode_context_checkout_manifest(manifest)


@pytest.mark.parametrize("checkout_path", ("C:report.txt", "C:/report.txt"))
def test_checkout_path_rejects_drive_like_first_component(checkout_path: str) -> None:
    with pytest.raises(ValueError):
        ContextCheckoutFile(
            checkout_path=checkout_path,
            source_kind="workspace_relative_root",
            source_ref="docs",
            content_digest="sha256:" + "a" * 64,
            byte_length=1,
            required=True,
        )


def test_wire_mapping_is_strict_and_canonicalized() -> None:
    manifest = _manifest()
    record = json.loads(encode_context_checkout_manifest(manifest))
    record["files"] = list(reversed(record["files"]))

    assert encode_context_checkout_manifest(record) == encode_context_checkout_manifest(
        manifest
    )
    assert decode_context_checkout_manifest(record) == decode_context_checkout_manifest(
        encode_context_checkout_manifest(manifest)
    )

    record["unexpected"] = True
    with pytest.raises(ValueError):
        encode_context_checkout_manifest(record)


def test_decode_context_checkout_manifest_refuses_noncanonical_bytes() -> None:
    manifest = ContextCheckoutManifest(
        session_id="séssion-1",
        dispatch_generation=1,
        plan_fingerprint="sha256:" + "a" * 64,
        binding_id="binding-1",
        router_asset_id="router-1",
        files=(),
        catalog=(),
        omissions=(),
    )
    canonical = encode_context_checkout_manifest(manifest)
    reordered = json.dumps(
        dict(reversed(tuple(json.loads(canonical).items()))),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    escaped_unicode = json.dumps(
        json.loads(canonical),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    for raw in (b" " + canonical, canonical + b"\n", reordered, escaped_unicode):
        assert raw != canonical
        with pytest.raises(ValueError):
            decode_context_checkout_manifest(raw)


@pytest.mark.parametrize(
    "raw",
    (
        b"{}",
        b'{"record_kind":"wrong","schema_version":1,"session_id":"s",'
        b'"dispatch_generation":1,"plan_fingerprint":"sha256:'
        b'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'"binding_id":"b","router_asset_id":"r","files":[],"omissions":[]}',
        b'{"record_kind":"millrace.context_checkout_manifest","schema_version":true,'
        b'session_id":"s","dispatch_generation":1,"plan_fingerprint":"sha256:'
        b'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'"binding_id":"b","router_asset_id":"r","files":[],"omissions":[]}',
        b'{"record_kind":"millrace.context_checkout_manifest","schema_version":1,'
        b'session_id":"s","session_id":"s","dispatch_generation":1,"plan_fingerprint":"sha256:'
        b'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'"binding_id":"b","router_asset_id":"r","files":[],"omissions":[]}',
    ),
)
def test_manifest_decode_refuses_malformed_wire_records(raw: bytes) -> None:
    with pytest.raises(ValueError):
        decode_context_checkout_manifest(raw)


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("session_id", ""),
        ("binding_id", "cafe\u0301"),
        ("router_asset_id", "bad\x00id"),
        ("plan_fingerprint", "sha256:" + "A" * 64),
        ("dispatch_generation", True),
    ),
)
def test_manifest_rejects_invalid_identity_and_integer_fields(
    field_name: str,
    value: object,
) -> None:
    values = {
        "session_id": "session-1",
        "dispatch_generation": 1,
        "plan_fingerprint": "sha256:" + "a" * 64,
        "binding_id": "binding-1",
        "router_asset_id": "router-1",
        "files": (),
        "catalog": (),
        "omissions": (),
    }
    values[field_name] = value

    with pytest.raises(ValueError):
        ContextCheckoutManifest(**values)  # type: ignore[arg-type]


def test_nested_contracts_reject_digest_bounds_reasons_and_duplicate_paths() -> None:
    with pytest.raises(ValueError):
        ContextCheckoutFile(
            checkout_path="a.txt",
            source_kind="workspace_relative_root",
            source_ref="docs",
            content_digest="sha256:" + "A" * 64,
            byte_length=1,
            required=True,
        )
    with pytest.raises(ValueError):
        ContextCheckoutFile(
            checkout_path="a.txt",
            source_kind="workspace_relative_root",
            source_ref="docs",
            content_digest="sha256:" + "a" * 64,
            byte_length=-1,
            required=True,
        )
    with pytest.raises(ValueError):
        ContextCheckoutOmission(
            source_kind="workspace_relative_root",
            source_ref="docs",
            reason="unsupported",
        )
    file_record = ContextCheckoutFile(
        checkout_path="a.txt",
        source_kind="workspace_relative_root",
        source_ref="docs",
        content_digest="sha256:" + "a" * 64,
        byte_length=1,
        required=True,
    )
    with pytest.raises(ValueError):
        ContextCheckoutManifest(
            session_id="session-1",
            dispatch_generation=1,
            plan_fingerprint="sha256:" + "a" * 64,
            binding_id="binding-1",
            router_asset_id="router-1",
            files=(file_record, file_record),
            omissions=(),
        )


def test_manifest_schema_two_catalog_is_canonical_and_payload_free() -> None:
    record = {
        "record_kind": "millrace.context_checkout_manifest",
        "schema_version": 2,
        "session_id": "session-1",
        "dispatch_generation": 1,
        "plan_fingerprint": "sha256:" + "a" * 64,
        "binding_id": "binding-1",
        "router_asset_id": "router-1",
        "files": [],
        "catalog": [
            {
                "logical_path": "discoverable/z.txt",
                "source_kind": "workspace_relative_root",
                "source_ref": "docs",
                "content_digest": "sha256:" + "b" * 64,
                "byte_length": 2,
                "provenance_ids": ["workspace:docs/z.txt"],
            },
            {
                "logical_path": "discoverable/a.txt",
                "source_kind": "workspace_relative_root",
                "source_ref": "docs",
                "content_digest": "sha256:" + "a" * 64,
                "byte_length": 1,
                "provenance_ids": ["workspace:docs/a.txt"],
            },
        ],
        "omissions": [],
    }

    manifest = decode_context_checkout_manifest(
        encode_context_checkout_manifest(record)
    )
    raw = encode_context_checkout_manifest(manifest)
    encoded = json.loads(raw)
    assert encoded["schema_version"] == 2
    assert [entry["logical_path"] for entry in encoded["catalog"]] == [
        "discoverable/a.txt",
        "discoverable/z.txt",
    ]
    assert all("payload" not in entry for entry in encoded["catalog"])


def test_manifest_schema_one_is_refused() -> None:
    raw = (
        b'{"binding_id":"binding-1","dispatch_generation":1,'
        b'"files":[],"omissions":[],"plan_fingerprint":"sha256:'
        b'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'"record_kind":"millrace.context_checkout_manifest",'
        b'"router_asset_id":"router-1","schema_version":1,'
        b'"session_id":"session-1"}'
    )

    with pytest.raises(ValueError, match="schema_version"):
        decode_context_checkout_manifest(raw)


def test_catalog_rejects_duplicate_paths_and_conflicting_digest_sizes() -> None:
    def catalog_entry(
        logical_path: str, content_digest: str, byte_length: int, provenance: str
    ) -> dict[str, object]:
        return {
            "logical_path": logical_path,
            "source_kind": "workspace_relative_root",
            "source_ref": "docs",
            "content_digest": content_digest,
            "byte_length": byte_length,
            "provenance_ids": [provenance],
        }

    values = {
        "record_kind": "millrace.context_checkout_manifest",
        "schema_version": 2,
        "session_id": "session-1",
        "dispatch_generation": 1,
        "plan_fingerprint": "sha256:" + "a" * 64,
        "binding_id": "binding-1",
        "router_asset_id": "router-1",
        "files": [],
        "omissions": [],
    }
    duplicate = dict(values, catalog=[
        catalog_entry("discoverable/a.txt", "sha256:" + "a" * 64, 1, "a"),
        catalog_entry("discoverable/a.txt", "sha256:" + "b" * 64, 2, "b"),
    ])
    with pytest.raises(ValueError, match="duplicate"):
        encode_context_checkout_manifest(duplicate)

    conflicting_size = dict(values, catalog=[
        catalog_entry("discoverable/a.txt", "sha256:" + "a" * 64, 1, "a"),
        catalog_entry("discoverable/b.txt", "sha256:" + "a" * 64, 2, "b"),
    ])
    with pytest.raises(ValueError, match="size"):
        encode_context_checkout_manifest(conflicting_size)


def test_schema_three_root_state_round_trips_and_is_authenticated() -> None:
    root_state = ContextCheckoutRootState(
        source_kind="workspace_relative_root",
        source_ref="docs",
        root_kind="directory",
        files=("guide.txt",),
        directories=("", "nested"),
    )
    manifest = replace(_manifest(), root_states=(root_state,))

    raw = encode_context_checkout_manifest(manifest)
    decoded = decode_context_checkout_manifest(raw)

    assert manifest.schema_version == 3
    assert decoded == manifest
    assert json.loads(raw)["root_states"] == [
        {
            "directories": ["", "nested"],
            "files": ["guide.txt"],
            "root_kind": "directory",
            "source_kind": "workspace_relative_root",
            "source_ref": "docs",
        }
    ]
    assert context_checkout_manifest_digest(raw) == context_checkout_manifest_digest(
        manifest
    )


def test_legacy_schema_two_manifest_round_trips_original_bytes_for_inspection() -> None:
    raw = (
        b'{"binding_id":"binding-1","catalog":[],'
        b'"dispatch_generation":1,"files":[],'
        b'"omissions":[{"reason":"source_missing",'
        b'"source_kind":"workspace_relative_root",'
        b'"source_ref":"missing"}],"plan_fingerprint":"sha256:'
        b'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'"record_kind":"millrace.context_checkout_manifest",'
        b'"router_asset_id":"router-1","schema_version":2,'
        b'"session_id":"session-1"}'
    )

    legacy = decode_context_checkout_manifest(raw)

    assert isinstance(legacy, ContextCheckoutLegacyManifest)
    assert legacy.schema_version == 2
    assert encode_context_checkout_manifest(legacy) == raw
    assert context_checkout_manifest_digest(legacy) == (
        "sha256:" + __import__("hashlib").sha256(raw).hexdigest()
    )
    assert "root_states" not in json.loads(encode_context_checkout_manifest(legacy))
