"""Pure closure-request overflow contract.

Exercises ``millrace.contracts.closure_request_overflow`` directly: inline
boundaries, projection keys and echo fields, page bounds, full segment
reassembly, and the refusal reasons. Portable: no workspace or external paths.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path

import pytest

from millrace.contracts.closure_request_overflow import (
    OVERFLOW_KEY,
    OVERFLOW_RECORD_KIND,
    PAGE_INDEX_RECORD_KIND,
    SNAPSHOT_KEY,
    ClosureRequestOverflowError,
    closure_request_dispatch_payload,
    closure_request_evidence_pages,
    closure_request_payload_refusal,
    materialization_component,
    materialization_directory,
)
from millrace.contracts.compiled_plan import (
    ClosureRequestOverflowDeclaration,
    CompletionBehaviorDeclaration,
    CompletionBehaviorWithRequestOverflowDeclaration,
    completion_behavior_request_overflow,
)
from millrace.contracts.ids import (
    ActionId,
    ArtifactSchemaId,
    CompletionBehaviorId,
    QueueFamilyId,
    RemediationPolicyId,
    RunnerBindingId,
    StageKindId,
)
from millrace.contracts.transition import canonical_authority_mapping_bytes

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "closure_overflow"
    / "c25_retained_closure_request_payload.json"
)
FIXTURE_BODY_BYTES = 18_566
FIXTURE_BODY_SHA256 = "f3fd14ebd7364c02327b60944d101e2e9bac1e0f0e7df34b31053bc62fd1a745"

INLINE_LIMIT = 131_072
MIB = 1_048_576
PAGE_LIMIT = 4_096
ROOT = "closure-evidence"
SESSION = "session-0001"

CHARACTERS = {
    "ascii": "a",
    "two-byte": "é",
    "three-byte": "€",
    "four-byte": "\U0001f600",
}
# One 1-, 2-, 3- and 4-byte character (10 UTF-8 bytes together).
MIXED = "aé€\U0001f600"
FOUR_BYTE = "\U0001f600".encode("utf-8")

PROJECTION_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "snapshot_digest",
        "snapshot_bytes",
        "closure_target_id",
        "selected_plan_fingerprint",
        "lineage_id",
        "freshness_anchor_digest",
        "root_contract_digest",
        "prior_verdict_digest",
        "evidence_artifact_count",
        "page_byte_limit",
        "page_count",
        "page_bytes",
        "materialization_directory",
        "index_pages",
    }
)
PAGE_NAME = re.compile(
    r"(index-\d{5}|root-contract-\d{5}|prior-verdict-\d{5}|evidence-\d{4}-\d{5})\.txt"
)


def _canonical(value: Mapping[str, object]) -> bytes:
    return canonical_authority_mapping_bytes(value)  # type: ignore[arg-type]


def _digest(label: str) -> str:
    return "sha256:" + sha256(label.encode("utf-8")).hexdigest()


def _digest_of(data: bytes) -> str:
    return "sha256:" + sha256(data).hexdigest()


def _behavior(
    *,
    request_limit: int = INLINE_LIMIT,
    overflow: bool = True,
    page_byte_limit: int = PAGE_LIMIT,
    max_snapshot_bytes: int = MIB,
    max_page_count: int = 1024,
) -> CompletionBehaviorDeclaration:
    base = CompletionBehaviorDeclaration(
        id=CompletionBehaviorId("completion.v2"),
        trigger="backlog_drained",
        readiness_rule="no_open_lineage_work",
        request_kind="closure_target",
        target_selector="active_closure_target",
        target_stage_kind_id=StageKindId("review"),
        target_graph_node_id="review.start",
        runner_binding_id=RunnerBindingId("review.runner"),
        request_queue_family_id=QueueFamilyId("review"),
        pass_action_id=ActionId("review.pass"),
        gap_action_id=ActionId("review.gap"),
        blocked_action_id=ActionId("review.blocked"),
        verdict_artifact_schema_id=ArtifactSchemaId("review.verdict"),
        evidence_artifact_schema_ids=(ArtifactSchemaId("review.evidence"),),
        evidence_item_limit=8,
        request_payload_byte_limit=request_limit,
        remediation_policy_id=RemediationPolicyId("review.remediation"),
        accepted_root_source_kinds=("origin",),
        root_source_resolution="runtime_inventory",
        evidence_window_policy="lineage",
        rubric_policy="reuse_or_create",
        blocked_work_policy="suppress",
        skip_if_closed=True,
        presentation={},
    )
    if not overflow:
        return base
    return CompletionBehaviorWithRequestOverflowDeclaration.from_behavior(
        base,
        request_overflow=ClosureRequestOverflowDeclaration(
            policy="materialized_evidence_pages",
            materialization_root=ROOT,
            page_byte_limit=page_byte_limit,
            max_snapshot_bytes=max_snapshot_bytes,
            max_page_count=max_page_count,
        ),
    )


def _artifact(position: int, text: str = "") -> dict[str, object]:
    return {
        "artifact_id": f"artifact-{position:05d}",
        "artifact_schema_id": "review.evidence",
        "payload": {"position": position, "text": text},
        "payload_digest": _digest(f"artifact-{position}:{text}"),
        "source_action_id": "review.evidence",
        "source_run_id": f"run-{position:05d}",
        "source_stage_kind_id": "review",
        "source_work_item_id": "work-0001",
        "transition_id": f"transition-{position:05d}",
    }


def _snapshot(
    *,
    root_pad: str = "",
    artifacts: tuple[dict[str, object], ...] = (),
    prior: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "record_kind": "closure_evidence_snapshot",
        "closure_target_id": "closure-target-0001",
        "selected_plan_fingerprint": _digest("plan-0001"),
        "lineage_id": "lineage-0001",
        "freshness_anchor_digest": _digest("freshness-0001"),
        "root_contract": {
            "work_item_id": "work-0001",
            "payload_digest": _digest("root-0001"),
            "pad": root_pad,
        },
        "prior_verdict": prior,
        "evidence_artifacts": list(artifacts),
    }


def _payload(snapshot: object | None, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "closure_target_id": "closure-target-0001",
        "graph_node_id": "review.start",
        "request_kind": "closure_target",
        "runner_binding_id": "review.runner",
        "stage_kind_id": "review",
    }
    if snapshot is not None:
        payload[SNAPSHOT_KEY] = snapshot
    payload.update(extra)
    return payload


def _pad_of_bytes(count: int, character: str) -> str:
    """Exactly ``count`` UTF-8 bytes built from ``character`` plus ASCII."""
    width = len(character.encode("utf-8"))
    whole, rest = divmod(count, width)
    return character * whole + "a" * rest


def _sized_payload(target: int, character: str) -> dict[str, object]:
    base = len(_canonical(_payload(_snapshot(root_pad=""))))
    return _payload(_snapshot(root_pad=_pad_of_bytes(target - base, character)))


def _straddling_payload(start: int) -> dict[str, object]:
    """Payload whose one 4-byte character occupies canonical bytes [start, start+4)."""
    empty = _canonical(_payload(_snapshot(root_pad="")))
    opening = empty.index(b'"pad":"') + len(b'"pad":"')
    return _payload(_snapshot(root_pad="a" * (start - opening) + "\U0001f600"))


def _dispatch(
    payload: Mapping[str, object],
    behavior: CompletionBehaviorDeclaration,
    session_id: str = SESSION,
    generation: int = 1,
) -> tuple[Mapping[str, object], tuple[object, ...]]:
    kwargs = {
        "behavior": behavior,
        "session_id": session_id,
        "dispatch_generation": generation,
    }
    projected = closure_request_dispatch_payload(payload, **kwargs)  # type: ignore[arg-type]
    pages = closure_request_evidence_pages(payload, **kwargs)  # type: ignore[arg-type]
    return projected, pages


def _assert_pages_reassemble(
    snapshot: Mapping[str, object],
    projection: Mapping[str, object],
    pages: tuple[object, ...],
    *,
    directory: str,
    page_limit: int,
) -> None:
    by_path = {page.path: page for page in pages}  # type: ignore[attr-defined]
    assert len(by_path) == len(pages)
    for page in pages:
        prefix, _, name = page.path.rpartition("/")  # type: ignore[attr-defined]
        assert prefix == directory
        assert PAGE_NAME.fullmatch(name), name
        assert 0 < len(page.data) <= page_limit  # type: ignore[attr-defined]
        page.data.decode("utf-8")  # type: ignore[attr-defined]

    index_descriptors = projection["index_pages"]
    index_pages = [by_path[descriptor["path"]] for descriptor in index_descriptors]  # type: ignore[index]
    listed_index = [
        page.path  # type: ignore[attr-defined]
        for page in pages
        if page.path.rpartition("/")[2].startswith("index-")  # type: ignore[attr-defined]
    ]
    assert sorted(page.path for page in index_pages) == sorted(listed_index)  # type: ignore[attr-defined]
    for descriptor, page in zip(index_descriptors, index_pages, strict=True):  # type: ignore[arg-type]
        assert descriptor["sha256"] == sha256(page.data).hexdigest()  # type: ignore[attr-defined]
        assert descriptor["bytes"] == len(page.data)  # type: ignore[attr-defined]

    index = json.loads(b"".join(page.data for page in index_pages).decode("utf-8"))  # type: ignore[attr-defined]
    assert index["record_kind"] == PAGE_INDEX_RECORD_KIND
    assert index["schema_version"] == 1
    assert index["snapshot_digest"] == projection["snapshot_digest"]
    assert index["closure_target_id"] == snapshot["closure_target_id"]

    members: list[tuple[str, Mapping[str, object]]] = [
        ("root_contract", snapshot["root_contract"])  # type: ignore[list-item]
    ]
    if snapshot["prior_verdict"] is not None:
        members.append(("prior_verdict", snapshot["prior_verdict"]))  # type: ignore[arg-type]
    members.extend(
        ("evidence_artifact", artifact)  # type: ignore[misc]
        for artifact in snapshot["evidence_artifacts"]  # type: ignore[union-attr]
    )
    assert len(index["segments"]) == len(members)

    referenced = {page.path for page in index_pages}  # type: ignore[attr-defined]
    for entry, (kind, member) in zip(index["segments"], members, strict=True):
        assert entry["segment"] == kind
        assert entry["payload_digest"] == member["payload_digest"]
        document = (
            json.dumps(member, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        assert entry["document_bytes"] == len(document)
        assert entry["document_sha256"] == sha256(document).hexdigest()
        segment_pages = [by_path[descriptor["path"]] for descriptor in entry["pages"]]
        for descriptor, page in zip(entry["pages"], segment_pages, strict=True):
            assert descriptor["sha256"] == sha256(page.data).hexdigest()  # type: ignore[attr-defined]
            assert descriptor["bytes"] == len(page.data)  # type: ignore[attr-defined]
        referenced.update(descriptor["path"] for descriptor in entry["pages"])
        joined = b"".join(page.data for page in segment_pages)  # type: ignore[attr-defined]
        assert joined == document
        assert json.loads(joined) == json.loads(_canonical(member))
    assert referenced == set(by_path)


def _assert_projection_sound(
    payload: Mapping[str, object],
    behavior: CompletionBehaviorDeclaration,
    projected: Mapping[str, object],
    pages: tuple[object, ...],
    *,
    session_id: str = SESSION,
    generation: int = 1,
) -> None:
    declaration = completion_behavior_request_overflow(behavior)
    assert declaration is not None
    snapshot = payload[SNAPSHOT_KEY]
    assert isinstance(snapshot, Mapping)
    snapshot_bytes = _canonical(snapshot)  # type: ignore[arg-type]

    assert SNAPSHOT_KEY not in projected
    assert len(_canonical(projected)) <= behavior.request_payload_byte_limit  # type: ignore[arg-type]
    assert set(projected) == (set(payload) - {SNAPSHOT_KEY}) | {OVERFLOW_KEY}
    for key in payload:
        if key != SNAPSHOT_KEY:
            assert _canonical({key: projected[key]}) == _canonical({key: payload[key]})  # type: ignore[dict-item]

    projection = projected[OVERFLOW_KEY]
    assert isinstance(projection, Mapping)
    assert set(projection) == PROJECTION_KEYS
    assert projection["record_kind"] == OVERFLOW_RECORD_KIND
    assert projection["schema_version"] == 1
    assert projection["snapshot_digest"] == _digest_of(snapshot_bytes)
    assert projection["snapshot_bytes"] == len(snapshot_bytes)
    for key in (
        "closure_target_id",
        "selected_plan_fingerprint",
        "lineage_id",
        "freshness_anchor_digest",
    ):
        assert projection[key] == snapshot[key]
    root_digest = snapshot["root_contract"]["payload_digest"]  # type: ignore[index]
    assert projection["root_contract_digest"] == root_digest
    prior = snapshot["prior_verdict"]
    assert projection["prior_verdict_digest"] == (
        None if prior is None else prior["payload_digest"]  # type: ignore[index]
    )
    assert projection["evidence_artifact_count"] == len(snapshot["evidence_artifacts"])  # type: ignore[arg-type]
    assert projection["page_byte_limit"] == declaration.page_byte_limit
    directory = materialization_directory(
        declaration, session_id=session_id, dispatch_generation=generation
    )
    assert projection["materialization_directory"] == directory
    assert projection["page_count"] == len(pages)
    assert projection["page_bytes"] == sum(len(page.data) for page in pages)  # type: ignore[attr-defined]
    assert 0 < len(pages) <= declaration.max_page_count
    _assert_pages_reassemble(
        snapshot,  # type: ignore[arg-type]
        projection,
        pages,
        directory=directory,
        page_limit=declaration.page_byte_limit,
    )


def _grown_fixture_payload(minimum: int) -> dict[str, object]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    snapshot = payload[SNAPSHOT_KEY]
    originals = list(snapshot["evidence_artifacts"])
    grown = list(originals)
    copies = 0
    while len(_canonical(payload)) <= minimum:
        copies += 1
        source = originals[copies % len(originals)]
        grown.append(
            {
                **source,
                "artifact_id": f"{source['artifact_id']}-copy-{copies:04d}",
                "transition_id": f"{source['transition_id']}-copy-{copies:04d}",
                "payload_digest": _digest(f"copy-{copies}-{source['payload_digest']}"),
            }
        )
        snapshot["evidence_artifacts"] = grown
    return payload


_MALFORMED_BIG = "p" * 140_000
MALFORMED_SNAPSHOTS = {
    "missing-snapshot": _payload(None, filler=_MALFORMED_BIG),
    "non-mapping-snapshot": _payload([_MALFORMED_BIG]),
    "wrong-record-kind": _payload(
        {**_snapshot(root_pad=_MALFORMED_BIG), "record_kind": "other_record"}
    ),
    "root-contract-not-mapping": _payload(
        {**_snapshot(root_pad=""), "root_contract": _MALFORMED_BIG}
    ),
    "artifacts-not-sequence": _payload(
        {**_snapshot(root_pad=_MALFORMED_BIG), "evidence_artifacts": _MALFORMED_BIG}
    ),
    "non-authority-float": _payload(
        {**_snapshot(root_pad=_MALFORMED_BIG), "score": 0.5}
    ),
    "overflow-key-already-present": _payload(
        _snapshot(root_pad=_MALFORMED_BIG),
        **{OVERFLOW_KEY: {"record_kind": "forged"}},
    ),
}


# Inline boundary and one-byte-over (exact canonical body sizes).


@pytest.mark.parametrize("character", list(CHARACTERS.values()), ids=list(CHARACTERS))
@pytest.mark.parametrize("size", [131_071, 131_072])
def test_exact_inline_boundary_keeps_payload_unchanged(
    character: str, size: int
) -> None:
    payload = _sized_payload(size, character)
    assert len(_canonical(payload)) == size
    for behavior in (_behavior(), _behavior(overflow=False)):
        assert closure_request_payload_refusal(payload, behavior=behavior) is None
        projected, pages = _dispatch(payload, behavior)
        assert projected == payload
        assert OVERFLOW_KEY not in projected
        assert pages == ()


@pytest.mark.parametrize("character", list(CHARACTERS.values()), ids=list(CHARACTERS))
def test_one_byte_over_boundary_refused_without_overflow(character: str) -> None:
    payload = _sized_payload(131_073, character)
    assert len(_canonical(payload)) == 131_073
    behavior = _behavior(overflow=False)
    assert completion_behavior_request_overflow(behavior) is None
    reason = "closure_request_payload_limit_exceeded"
    assert closure_request_payload_refusal(payload, behavior=behavior) == reason
    with pytest.raises(ClosureRequestOverflowError) as dispatch_error:
        closure_request_dispatch_payload(
            payload, behavior=behavior, session_id=SESSION, dispatch_generation=1
        )
    assert dispatch_error.value.reason == reason
    with pytest.raises(ClosureRequestOverflowError):
        closure_request_evidence_pages(
            payload, behavior=behavior, session_id=SESSION, dispatch_generation=1
        )


@pytest.mark.parametrize("character", list(CHARACTERS.values()), ids=list(CHARACTERS))
def test_one_byte_over_boundary_projects_with_overflow(character: str) -> None:
    payload = _sized_payload(131_073, character)
    assert len(_canonical(payload)) == 131_073
    behavior = _behavior()
    assert closure_request_payload_refusal(payload, behavior=behavior) is None
    projected, pages = _dispatch(payload, behavior)
    _assert_projection_sound(payload, behavior, projected, pages)


@pytest.mark.parametrize("start", [131_069, 131_070, 131_071])
def test_four_byte_character_straddles_inline_limit(start: int) -> None:
    payload = _straddling_payload(start)
    body = _canonical(payload)
    assert body[start : start + 4] == FOUR_BYTE
    assert start < INLINE_LIMIT < start + 4
    assert len(body) > INLINE_LIMIT
    with pytest.raises(UnicodeDecodeError):
        body[:INLINE_LIMIT].decode("utf-8")
    assert (
        closure_request_payload_refusal(payload, behavior=_behavior(overflow=False))
        == "closure_request_payload_limit_exceeded"
    )
    behavior = _behavior()
    assert closure_request_payload_refusal(payload, behavior=behavior) is None
    projected, pages = _dispatch(payload, behavior)
    _assert_projection_sound(payload, behavior, projected, pages)


# Determinism, keys, digests, and page bounds.


def test_projection_and_pages_are_deterministic() -> None:
    payload = _sized_payload(131_073, CHARACTERS["two-byte"])
    reordered_snapshot = dict(reversed(list(payload[SNAPSHOT_KEY].items())))  # type: ignore[union-attr]
    reordered = dict(reversed(list(payload.items())))
    reordered[SNAPSHOT_KEY] = reordered_snapshot
    assert _canonical(reordered) == _canonical(payload)
    behavior = _behavior()
    first_projected, first_pages = _dispatch(payload, behavior)
    second_projected, second_pages = _dispatch(payload, behavior)
    reordered_projected, reordered_pages = _dispatch(reordered, behavior)
    assert _canonical(first_projected) == _canonical(second_projected)
    assert first_pages == second_pages
    assert _canonical(reordered_projected) == _canonical(first_projected)
    assert reordered_pages == first_pages


def test_projection_has_exact_keys_and_echo_fields() -> None:
    payload = _sized_payload(131_073, CHARACTERS["three-byte"])
    behavior = _behavior()
    projected, pages = _dispatch(payload, behavior)
    _assert_projection_sound(payload, behavior, projected, pages)
    projection = projected[OVERFLOW_KEY]
    assert projection["prior_verdict_digest"] is None  # type: ignore[index]
    assert projection["evidence_artifact_count"] == 0  # type: ignore[index]
    assert projection["snapshot_digest"] == _digest_of(  # type: ignore[index]
        _canonical(payload[SNAPSHOT_KEY])  # type: ignore[arg-type]
    )


def test_snapshot_digest_tracks_snapshot_bytes() -> None:
    first = _sized_payload(131_073, "a")
    changed = copy.deepcopy(first)
    changed[SNAPSHOT_KEY]["lineage_id"] = "lineage-0002"  # type: ignore[index]
    first_snapshot = _canonical(first[SNAPSHOT_KEY])  # type: ignore[arg-type]
    changed_snapshot = _canonical(changed[SNAPSHOT_KEY])  # type: ignore[arg-type]
    assert len(changed_snapshot) == len(first_snapshot)
    assert changed_snapshot != first_snapshot
    behavior = _behavior()
    first_projected, _ = _dispatch(first, behavior)
    again_projected, _ = _dispatch(first, behavior)
    changed_projected, _ = _dispatch(changed, behavior)
    assert (
        first_projected[OVERFLOW_KEY]["snapshot_digest"]  # type: ignore[index]
        == again_projected[OVERFLOW_KEY]["snapshot_digest"]  # type: ignore[index]
    )
    assert (
        first_projected[OVERFLOW_KEY]["snapshot_digest"]  # type: ignore[index]
        != changed_projected[OVERFLOW_KEY]["snapshot_digest"]  # type: ignore[index]
    )


@pytest.mark.parametrize("page_limit", list(range(4096, 4104)))
def test_pages_split_only_at_character_boundaries(page_limit: int) -> None:
    payload = _payload(_snapshot(root_pad=MIXED * 14_000))
    assert len(_canonical(payload)) > INLINE_LIMIT
    behavior = _behavior(page_byte_limit=page_limit)
    projected, pages = _dispatch(payload, behavior)
    _assert_projection_sound(payload, behavior, projected, pages)


@pytest.mark.parametrize("page_limit", [4096, 4097, 5000])
def test_huge_root_contract_pages_reassemble(page_limit: int) -> None:
    payload = _payload(_snapshot(root_pad=MIXED * 20_000))
    assert len(_canonical(payload)) > INLINE_LIMIT
    behavior = _behavior(page_byte_limit=page_limit)
    projected, pages = _dispatch(payload, behavior)
    _assert_projection_sound(payload, behavior, projected, pages)


def test_huge_single_artifact_string_pages_reassemble() -> None:
    payload = _payload(_snapshot(artifacts=(_artifact(1, MIXED * 20_000),)))
    assert len(_canonical(payload)) > INLINE_LIMIT
    behavior = _behavior()
    projected, pages = _dispatch(payload, behavior)
    _assert_projection_sound(payload, behavior, projected, pages)
    assert projected[OVERFLOW_KEY]["evidence_artifact_count"] == 1  # type: ignore[index]


def test_huge_prior_verdict_pages_reassemble() -> None:
    prior = _artifact(0, MIXED * 20_000)
    payload = _payload(_snapshot(prior=prior, artifacts=(_artifact(1, "small"),)))
    assert len(_canonical(payload)) > INLINE_LIMIT
    behavior = _behavior()
    projected, pages = _dispatch(payload, behavior)
    _assert_projection_sound(payload, behavior, projected, pages)
    assert (
        projected[OVERFLOW_KEY]["prior_verdict_digest"]  # type: ignore[index]
        == prior["payload_digest"]
    )


def test_ninety_six_small_artifacts_reassemble() -> None:
    artifacts = tuple(_artifact(position, "s" * 1_400) for position in range(96))
    payload = _payload(_snapshot(artifacts=artifacts))
    assert len(_canonical(payload)) > INLINE_LIMIT
    behavior = _behavior()
    projected, pages = _dispatch(payload, behavior)
    _assert_projection_sound(payload, behavior, projected, pages)
    assert projected[OVERFLOW_KEY]["evidence_artifact_count"] == 96  # type: ignore[index]


# Retained C25 body.


def test_retained_fixture_is_the_recorded_c25_body() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    body = _canonical(payload)
    assert len(body) == FIXTURE_BODY_BYTES
    assert sha256(body).hexdigest() == FIXTURE_BODY_SHA256
    assert payload[SNAPSHOT_KEY]["record_kind"] == "closure_evidence_snapshot"


def test_retained_c25_body_stays_inline_at_production_limit() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    behavior = _behavior()
    assert closure_request_payload_refusal(payload, behavior=behavior) is None
    projected, pages = _dispatch(payload, behavior)
    assert projected == payload
    assert pages == ()


def test_retained_c25_body_projects_under_small_limit() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert (
        closure_request_payload_refusal(
            payload, behavior=_behavior(overflow=False, request_limit=8_192)
        )
        == "closure_request_payload_limit_exceeded"
    )
    behavior = _behavior(request_limit=8_192)
    assert closure_request_payload_refusal(payload, behavior=behavior) is None
    projected, pages = _dispatch(payload, behavior)
    _assert_projection_sound(payload, behavior, projected, pages)


def test_retained_fixture_grown_past_four_inline_limits_reassembles() -> None:
    minimum = 4 * INLINE_LIMIT
    payload = _grown_fixture_payload(minimum)
    assert len(_canonical(payload)) > minimum
    artifacts = payload[SNAPSHOT_KEY]["evidence_artifacts"]  # type: ignore[index]
    assert len(artifacts) > 6
    assert len({artifact["artifact_id"] for artifact in artifacts}) == len(artifacts)
    behavior = _behavior(max_page_count=4096)
    projected, pages = _dispatch(payload, behavior)
    _assert_projection_sound(payload, behavior, projected, pages)


# Session-owned directories and projection size.


def test_projection_size_independent_of_session_and_generation() -> None:
    payload = _payload(_snapshot(root_pad=MIXED * 14_000))
    behavior = _behavior()
    shapes = set()
    for session_id, generation in (
        ("s", 1),
        ("session-with-a-much-longer-identifier-0001", 99_999),
    ):
        projected, pages = _dispatch(payload, behavior, session_id, generation)
        projection = projected[OVERFLOW_KEY]  # type: ignore[index]
        shapes.add(
            (
                len(_canonical(projected)),  # type: ignore[arg-type]
                len(pages),
                projection["page_bytes"],  # type: ignore[index]
            )
        )
    assert len(shapes) == 1


def test_sessions_get_distinct_directories_with_identical_segment_pages() -> None:
    payload = _payload(_snapshot(root_pad=MIXED * 14_000))
    behavior = _behavior()
    first_projected, first_pages = _dispatch(payload, behavior, "session-a", 1)
    second_projected, second_pages = _dispatch(payload, behavior, "session-b", 1)
    third_projected, _ = _dispatch(payload, behavior, "session-a", 2)
    first_dir = first_projected[OVERFLOW_KEY]["materialization_directory"]  # type: ignore[index]
    second_dir = second_projected[OVERFLOW_KEY]["materialization_directory"]  # type: ignore[index]
    third_dir = third_projected[OVERFLOW_KEY]["materialization_directory"]  # type: ignore[index]
    assert len({first_dir, second_dir, third_dir}) == 3
    assert all(page.path.startswith(first_dir + "/") for page in first_pages)  # type: ignore[attr-defined]
    assert all(page.path.startswith(second_dir + "/") for page in second_pages)  # type: ignore[attr-defined]

    def segment_data(pages: tuple[object, ...]) -> list[bytes]:
        return [
            page.data  # type: ignore[attr-defined]
            for page in pages
            if not page.path.rpartition("/")[2].startswith("index-")  # type: ignore[attr-defined]
        ]

    assert segment_data(first_pages) == segment_data(second_pages)


def test_materialization_component_is_fixed_length_hex_and_session_bound() -> None:
    component = materialization_component(SESSION, 1)
    assert re.fullmatch(r"[0-9a-f]{32}", component)
    assert component == sha256(b"session-0001\n1").hexdigest()[:32]
    assert materialization_component(SESSION, 2) != component
    assert materialization_component("session-0002", 1) != component
    assert len(materialization_component("x" * 500, 1)) == 32


def test_materialization_directory_uses_declared_root() -> None:
    declaration = completion_behavior_request_overflow(_behavior())
    assert declaration is not None
    directory = materialization_directory(
        declaration, session_id=SESSION, dispatch_generation=1
    )
    assert directory == f"{ROOT}/{materialization_component(SESSION, 1)}"


# Declared maxima and refusals.


def test_snapshot_limit_boundary_is_inclusive() -> None:
    base = len(_canonical(_snapshot(root_pad="")))
    payload = _payload(_snapshot(root_pad=_pad_of_bytes(9_000 - base, "a")))
    snapshot_bytes = _canonical(payload[SNAPSHOT_KEY])  # type: ignore[arg-type]
    assert len(snapshot_bytes) == 9_000
    assert len(_canonical(payload)) > 8_192
    at_maximum = _behavior(request_limit=8_192, max_snapshot_bytes=9_000)
    below_maximum = _behavior(request_limit=8_192, max_snapshot_bytes=8_999)
    assert closure_request_payload_refusal(payload, behavior=at_maximum) is None
    assert (
        closure_request_payload_refusal(payload, behavior=below_maximum)
        == "closure_request_overflow_snapshot_limit_exceeded"
    )


def test_page_limit_boundary_is_inclusive() -> None:
    payload = _payload(_snapshot(root_pad=MIXED * 2_000))
    generous = _behavior(request_limit=8_192)
    _projected, pages = _dispatch(payload, generous)
    count = len(pages)
    assert count >= 3
    assert closure_request_payload_refusal(
        payload, behavior=_behavior(request_limit=8_192, max_page_count=count)
    ) is None
    assert (
        closure_request_payload_refusal(
            payload,
            behavior=_behavior(request_limit=8_192, max_page_count=count - 1),
        )
        == "closure_request_overflow_page_limit_exceeded"
    )


def test_projection_over_tiny_limit_refused_with_many_index_pages() -> None:
    artifacts = tuple(_artifact(position, f"row {position}") for position in range(400))
    payload = _payload(_snapshot(artifacts=artifacts))
    assert len(_canonical(payload)) > INLINE_LIMIT
    _projected, pages = _dispatch(payload, _behavior())
    index_pages = [
        page
        for page in pages
        if page.path.rpartition("/")[2].startswith("index-")  # type: ignore[attr-defined]
    ]
    assert len(index_pages) > 20
    tiny = _behavior(request_limit=PAGE_LIMIT, max_page_count=16_384)
    assert (
        closure_request_payload_refusal(payload, behavior=tiny)
        == "closure_request_overflow_projection_limit_exceeded"
    )
    with pytest.raises(ClosureRequestOverflowError) as error:
        closure_request_dispatch_payload(
            payload, behavior=tiny, session_id=SESSION, dispatch_generation=1
        )
    assert error.value.reason == "closure_request_overflow_projection_limit_exceeded"


@pytest.mark.parametrize(
    "payload",
    list(MALFORMED_SNAPSHOTS.values()),
    ids=list(MALFORMED_SNAPSHOTS),
)
def test_malformed_snapshot_is_refused_as_invalid(
    payload: Mapping[str, object],
) -> None:
    reason = "closure_request_overflow_snapshot_invalid"
    behavior = _behavior()
    assert closure_request_payload_refusal(payload, behavior=behavior) == reason
    with pytest.raises(ClosureRequestOverflowError) as error:
        closure_request_dispatch_payload(
            payload, behavior=behavior, session_id=SESSION, dispatch_generation=1
        )
    assert error.value.reason == reason


def test_overflow_error_is_a_value_error_carrying_reason() -> None:
    error = ClosureRequestOverflowError("closure_request_payload_limit_exceeded")
    assert isinstance(error, ValueError)
    assert error.reason == "closure_request_payload_limit_exceeded"
