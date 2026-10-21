"""Standard-library fake Pi wire that verifies materialized closure evidence pages.

Audited test fixture. It imports nothing from Millrace, opens no network
connection, and executes no child command. On ``prompt`` it parses the Pi
prompt JSON, reads the closure evidence index and page files from its current
working directory (the Pi workspace) with bounded reads, verifies every page
digest and byte count, rebuilds each segment document, and answers with a PASS
verdict anchored to the verified snapshot. The runner report is JSON text that
records what was verified so the caller can check the last-page proof.
"""

import hashlib
import json
import os
import stat
import sys

OVERFLOW_KEY = "closure_evidence_overflow"
OVERFLOW_RECORD_KIND = "closure_evidence_overflow"
PAGE_INDEX_RECORD_KIND = "closure_evidence_page_index"
SNAPSHOT_KEY = "closure_evidence_snapshot"
PAYLOAD_KEY = "work_item_payload"
PASS_MARKER = "REVIEW_PASSED"
FAIL_MARKER = "OPAQUE"
READ_CHUNK = 65536
MODEL = {
    "id": "opaque-model",
    "provider": "local",
    "api": "openai-completions",
    "endpoint": "http://127.0.0.1:43187/v1",
    "context_window": 32000,
    "max_output_tokens": 256,
}


class EvidenceError(ValueError):
    """Materialized closure evidence failed verification."""


def emit(value):
    print(json.dumps(value), flush=True)


def _sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def _is_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _read_page(root, directory, page_byte_limit, descriptor):
    """Read one page descriptor under ``directory`` with bounded, contained I/O."""
    if not isinstance(descriptor, dict):
        raise EvidenceError("page_descriptor_not_mapping")
    path = descriptor.get("path")
    expected_bytes = descriptor.get("bytes")
    expected_sha = descriptor.get("sha256")
    if not isinstance(path, str) or not isinstance(expected_sha, str):
        raise EvidenceError("page_descriptor_malformed")
    if not _is_count(expected_bytes) or expected_bytes > page_byte_limit:
        raise EvidenceError("page_bytes_out_of_bounds")
    prefix = directory + "/"
    name = path[len(prefix) :] if path.startswith(prefix) else ""
    if not name or "/" in name or name in {".", ".."} or os.path.isabs(path):
        raise EvidenceError("page_path_outside_directory")
    target = os.path.join(root, path)
    resolved = os.path.realpath(target)
    if os.path.commonpath([resolved, root]) != root or resolved == root:
        raise EvidenceError("page_path_escapes_workspace")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(target, flags)
    except OSError as exc:
        raise EvidenceError("page_open_refused") from exc
    chunks = []
    total = 0
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size != expected_bytes:
            raise EvidenceError("page_not_regular_or_wrong_size")
        while total <= expected_bytes:
            chunk = os.read(fd, min(READ_CHUNK, expected_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if len(data) != expected_bytes or _sha256_hex(data) != expected_sha:
        raise EvidenceError("page_digest_mismatch")
    return data


def _read_document(root, directory, page_byte_limit, pages, reads):
    """Rebuild one document by concatenating its verified pages in order."""
    if not isinstance(pages, list) or not pages:
        raise EvidenceError("document_pages_missing")
    chunks = []
    for descriptor in pages:
        chunks.append(_read_page(root, directory, page_byte_limit, descriptor))
        reads[0] += 1
    return b"".join(chunks)


def _parse(document):
    try:
        value = json.loads(document.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise EvidenceError("document_not_json") from exc
    if not isinstance(value, dict):
        raise EvidenceError("document_not_mapping")
    return value


def _verify_paged(root, overflow):
    """Verify the index, every segment, and the last evidence page."""
    if overflow.get("record_kind") != OVERFLOW_RECORD_KIND:
        raise EvidenceError("overflow_record_kind_mismatch")
    directory = overflow.get("materialization_directory")
    page_byte_limit = overflow.get("page_byte_limit")
    if not isinstance(directory, str) or not directory:
        raise EvidenceError("materialization_directory_missing")
    if not _is_count(page_byte_limit) or page_byte_limit <= 0:
        raise EvidenceError("page_byte_limit_missing")
    reads = [0]
    index_pages = overflow.get("index_pages")
    if not isinstance(index_pages, list) or not index_pages:
        raise EvidenceError("index_pages_missing")
    index_bytes = _read_document(root, directory, page_byte_limit, index_pages, reads)
    index = _parse(index_bytes)
    if index.get("record_kind") != PAGE_INDEX_RECORD_KIND:
        raise EvidenceError("index_record_kind_mismatch")
    if index.get("snapshot_digest") != overflow.get("snapshot_digest"):
        raise EvidenceError("index_snapshot_digest_mismatch")
    if index.get("closure_target_id") != overflow.get("closure_target_id"):
        raise EvidenceError("index_closure_target_mismatch")
    segments = index.get("segments")
    if not isinstance(segments, list) or not segments:
        raise EvidenceError("index_segments_missing")

    root_digest = None
    prior_seen = False
    evidence_entries = []
    last_entry = None
    for entry in segments:
        if not isinstance(entry, dict):
            raise EvidenceError("segment_entry_not_mapping")
        document_bytes = _read_document(
            root, directory, page_byte_limit, entry.get("pages"), reads
        )
        if len(document_bytes) != entry.get("document_bytes") or (
            _sha256_hex(document_bytes) != entry.get("document_sha256")
        ):
            raise EvidenceError("segment_document_digest_mismatch")
        document = _parse(document_bytes)
        kind = entry.get("segment")
        if kind == "root_contract":
            if root_digest is not None:
                raise EvidenceError("duplicate_root_contract")
            root_digest = document.get("payload_digest")
            if root_digest != overflow.get("root_contract_digest"):
                raise EvidenceError("root_contract_digest_mismatch")
        elif kind == "prior_verdict":
            if prior_seen or overflow.get("prior_verdict_digest") is None:
                raise EvidenceError("unexpected_prior_verdict")
            prior_seen = True
            if document.get("payload_digest") != overflow.get("prior_verdict_digest"):
                raise EvidenceError("prior_verdict_digest_mismatch")
        elif kind == "evidence_artifact":
            if entry.get("position") != len(evidence_entries):
                raise EvidenceError("evidence_position_out_of_order")
            if document.get("payload_digest") != entry.get("payload_digest"):
                raise EvidenceError("evidence_payload_digest_mismatch")
            evidence_entries.append(entry)
            last_entry = entry
        else:
            raise EvidenceError("segment_kind_unknown")
    if root_digest is None:
        raise EvidenceError("root_contract_segment_missing")
    if overflow.get("prior_verdict_digest") is not None and not prior_seen:
        raise EvidenceError("prior_verdict_segment_missing")
    if len(evidence_entries) != overflow.get("evidence_artifact_count"):
        raise EvidenceError("evidence_count_mismatch")
    if last_entry is None:
        raise EvidenceError("evidence_segment_missing")
    last_page = last_entry["pages"][-1]
    anchor = {
        "closure_target_id": overflow.get("closure_target_id"),
        "root_contract_digest": root_digest,
        "freshness_anchor_digest": overflow.get("freshness_anchor_digest"),
    }
    report = {
        "mode": "paged",
        "evidence_count": len(evidence_entries),
        "last_evidence_position": last_entry["position"],
        "last_segment_document_sha256": last_entry["document_sha256"],
        "last_segment_document_bytes": last_entry["document_bytes"],
        "last_page_path": last_page["path"],
        "last_page_sha256": last_page["sha256"],
        "last_page_bytes": last_page["bytes"],
        "pages_read": reads[0],
    }
    return anchor, report


def _verify_inline(snapshot):
    """Inline requests carry the full snapshot; no pages are read."""
    root = snapshot.get("root_contract")
    if not isinstance(root, dict) or not isinstance(root.get("payload_digest"), str):
        raise EvidenceError("inline_root_contract_missing")
    anchor = {
        "closure_target_id": snapshot.get("closure_target_id"),
        "root_contract_digest": root["payload_digest"],
        "freshness_anchor_digest": snapshot.get("freshness_anchor_digest"),
    }
    report = {"mode": "inline", "pages_read": 0}
    return anchor, report


def _verdict(anchor):
    """PASS verdict shaped like the closure review schema."""
    return {
        "artifact_kind": "closure_verdict",
        "summary": "Review completed.",
        "closure_target_id": anchor["closure_target_id"],
        "root_contract_digest": anchor["root_contract_digest"],
        "freshness_anchor_digest": anchor["freshness_anchor_digest"],
        "rubric": {
            "criteria": [
                {
                    "criterion_id": "criterion-1",
                    "requirement": "The closure contract is satisfied.",
                    "evidence_rule": "Use current review evidence.",
                }
            ]
        },
        "criterion_results": [
            {
                "criterion_id": "criterion-1",
                "status": "passed",
                "provenance": "fresh",
                "evidence_refs": [{"evidence_id": "evidence-1", "summary": "reviewed"}],
            }
        ],
        "observations": [],
        "remediation_guidance": [],
        "confidence": "high",
        "residual_uncertainty": "none",
    }


def _answer(root, message):
    """Return the candidate object and the runner report for one Pi prompt."""
    report = {"mode": "error", "error": "", "pages_read": 0}
    try:
        prompt = json.loads(message)
        payload = prompt[PAYLOAD_KEY]
        if not isinstance(payload, dict):
            raise EvidenceError("work_item_payload_not_mapping")
        if OVERFLOW_KEY in payload:
            anchor, detail = _verify_paged(root, payload[OVERFLOW_KEY])
        elif isinstance(payload.get(SNAPSHOT_KEY), dict):
            anchor, detail = _verify_inline(payload[SNAPSHOT_KEY])
        else:
            raise EvidenceError("closure_evidence_absent")
        report = dict(detail)
        report["error"] = ""
        verdict = _verdict(anchor)
        marker = PASS_MARKER
    except (EvidenceError, KeyError, TypeError, ValueError, OSError) as exc:
        report["error"] = (str(exc) or type(exc).__name__)[:200]
        verdict = None
        marker = FAIL_MARKER
    report["prompt_bytes"] = len(message.encode("utf-8"))
    candidate = {
        "marker": marker,
        "artifact_payload_candidate": verdict,
        "observation_payload_candidate": {
            "runner_report": json.dumps(report, sort_keys=True)
        },
    }
    return candidate, report


def _lifecycle(prompt, final_text):
    """Event sequence mirroring the support lifecycle with the full prompt echoed."""
    user = {
        "role": "user",
        "content": [{"type": "text", "text": prompt}],
        "timestamp": 1,
    }
    assistant = {
        "role": "assistant",
        "content": [{"type": "text", "text": final_text}],
        "stopReason": "stop",
        "timestamp": 4,
        "model": MODEL["id"],
        "provider": MODEL["provider"],
        "api": MODEL["api"],
        "usage": {
            "input": 100,
            "output": 10,
            "cacheRead": 20,
            "cacheWrite": 0,
            "totalTokens": 130,
        },
    }
    return [
        {"type": "agent_start"},
        {"type": "turn_start"},
        {"type": "message_start", "message": user},
        {"type": "message_end", "message": user},
        {"type": "message_start", "message": assistant},
        {"type": "message_end", "message": assistant},
        {"type": "turn_end", "message": assistant, "toolResults": []},
        {"type": "agent_end", "messages": [user, assistant], "willRetry": False},
        {"type": "agent_settled"},
    ]


def main():
    root = os.path.realpath(os.getcwd())
    last_text = json.dumps(
        {
            "marker": FAIL_MARKER,
            "artifact_payload_candidate": None,
            "observation_payload_candidate": {"runner_report": "unset"},
        }
    )
    prompted = False
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        command = request["type"]
        data = {}
        if command == "get_state":
            data = {
                "sessionId": "fake-session",
                "model": {
                    "id": MODEL["id"],
                    "provider": MODEL["provider"],
                    "api": MODEL["api"],
                    "baseUrl": MODEL["endpoint"],
                    "contextWindow": MODEL["context_window"],
                    "maxTokens": MODEL["max_output_tokens"],
                },
                "thinkingLevel": "xhigh",
                "isStreaming": False,
                "isCompacting": False,
                "pendingMessageCount": 0,
            }
        elif command == "get_session_stats":
            # Zero before the first prompt: the client's readiness check
            # refuses a session that already carries message or token history.
            count = int(prompted)
            data = {
                "sessionId": "fake-session",
                "userMessages": count,
                "assistantMessages": count,
                "toolCalls": 0,
                "toolResults": 0,
                "tokens": {
                    "input": 100 * count,
                    "output": 10 * count,
                    "cacheRead": 20 * count,
                    "cacheWrite": 0,
                    "total": 130 * count,
                },
            }
        elif command == "get_last_assistant_text":
            data = {"text": last_text}
        elif command == "prompt":
            candidate, _ = _answer(root, request["message"])
            last_text = json.dumps(candidate)
            prompted = True
            for event in _lifecycle(request["message"], last_text):
                emit(event)
        emit(
            {
                "type": "response",
                "id": request["id"],
                "command": command,
                "success": True,
                "data": data,
            }
        )


if __name__ == "__main__":
    main()
