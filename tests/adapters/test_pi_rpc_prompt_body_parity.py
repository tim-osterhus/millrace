"""Pi prompt parity: the work-item body reaches the runner as its canonical bytes.

The Pi prompt embeds the dispatched work-item payload verbatim as UTF-8 JSON, so the
selected body limit and the runner input bound measure the same bytes.
"""

from __future__ import annotations

import json

import pytest

from millrace.adapters.pi_rpc import prompt_for_request
from millrace.adapters.runner_contract import AdapterInvocationRequest, RedactionPolicy
from millrace.contracts.transition import canonical_authority_mapping_bytes
from tests.adapters.test_codex_adapter import (
    _default_selected_projection,
    _valid_dispatch_envelope,
)

CANONICAL_BODY_LIMIT = 131072
PROMPT_BOUND = 262144
EMPTY_BODY_CANONICAL_BYTES = len(b'{"body":""}')
# 4 characters, 10 UTF-8 bytes: 2 (e-acute) + 3 (euro) + 4 (emoji) + 1 (a).
UNICODE_UNIT = "é€😀a"
CHARACTER_CLASSES = {
    "ascii": ("a", 1),
    "two_byte": ("é", 2),
    "three_byte": ("€", 3),
    "four_byte": ("😀", 4),
}


def _request(body: dict[str, str]) -> AdapterInvocationRequest:
    dispatch = _valid_dispatch_envelope(work_item_payload=body)
    _, schemas = _default_selected_projection(dispatch)
    return AdapterInvocationRequest(
        adapter_id="pi-local",
        selected_runner_binding_id=dispatch.runner_binding_id,
        selected_adapter_kind="pi_rpc",
        dispatch_envelope=dispatch,
        session_id=dispatch.session_id,
        dispatch_generation=dispatch.dispatch_generation,
        session_fencing_token=dispatch.session_fencing_token,
        timeout_seconds=3,
        correlation_id="correlation",
        redaction_policy=RedactionPolicy("redact", ("secret-value",)),
        selected_asset_material={
            dispatch.entrypoint_asset_id: "opaque instructions",
            dispatch.skill_asset_ids[0]: "selected skill",
        },
        selected_artifact_schemas=schemas,
    )


def _unicode_heavy_body() -> dict[str, str]:
    body = {"body": UNICODE_UNIT * 13106 + "a"}
    assert len(canonical_authority_mapping_bytes(body)) == CANONICAL_BODY_LIMIT
    return body


@pytest.mark.parametrize(
    "text",
    ["proof it out", "café €5 😀", UNICODE_UNIT * 3],
    ids=["ascii", "mixed-unicode", "repeated-unicode"],
)
def test_canonical_body_bytes_appear_verbatim_in_the_utf8_prompt(text: str) -> None:
    body = {"body": text}
    request = _request(body)
    canonical = canonical_authority_mapping_bytes(body)

    prompt = prompt_for_request(request)

    assert request.dispatch_envelope.work_item_payload == body
    assert prompt.encode("utf-8").count(canonical) == 1
    assert json.loads(prompt)["work_item_payload"] == body


def test_non_ascii_body_is_not_ascii_escaped() -> None:
    body = {"body": UNICODE_UNIT * 3}

    prompt = prompt_for_request(_request(body))

    assert not prompt.isascii()
    assert "é" in prompt
    assert "€" in prompt
    assert "😀" in prompt
    assert "\\u" not in prompt


def test_prompt_overhead_is_independent_of_character_class() -> None:
    overheads: dict[str, int] = {}
    for name, (char, width) in CHARACTER_CLASSES.items():
        body = {"body": char * 200}
        canonical = canonical_authority_mapping_bytes(body)
        assert len(canonical) == EMPTY_BODY_CANONICAL_BYTES + 200 * width

        prompt_bytes = prompt_for_request(_request(body)).encode("utf-8")

        assert canonical in prompt_bytes
        overheads[name] = len(prompt_bytes) - len(canonical)

    assert overheads["ascii"] > 0
    assert len(set(overheads.values())) == 1, overheads


def test_unicode_heavy_131072_byte_body_keeps_prompt_within_the_bound() -> None:
    body = _unicode_heavy_body()
    canonical = canonical_authority_mapping_bytes(body)

    prompt_bytes = prompt_for_request(_request(body)).encode("utf-8")

    assert len(canonical) == CANONICAL_BODY_LIMIT
    assert canonical in prompt_bytes
    assert len(prompt_bytes) <= PROMPT_BOUND


def test_ascii_escaped_serialization_of_the_same_body_would_exceed_the_bound() -> None:
    body = _unicode_heavy_body()

    escaped = json.dumps(
        body,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")

    assert len(escaped) > PROMPT_BOUND
