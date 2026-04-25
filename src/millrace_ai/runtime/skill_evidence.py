"""Skill revision evidence for audit-exact learning-enabled runs."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def write_skill_revision_evidence(
    *,
    run_dir: Path,
    request_id: str,
    run_id: str,
    mode_id: str,
    compiled_plan_id: str,
    skill_paths: tuple[str, ...],
) -> Path:
    """Persist content hashes for the skill files referenced by a stage request."""

    evidence_path = run_dir / "skill_revision_evidence.json"
    payload = {
        "schema_version": "1.0",
        "kind": "skill_revision_evidence",
        "request_id": request_id,
        "run_id": run_id,
        "mode_id": mode_id,
        "compiled_plan_id": compiled_plan_id,
        "emitted_at": datetime.now(timezone.utc).isoformat(),
        "skills": [_skill_evidence(path) for path in skill_paths],
    }
    evidence_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return evidence_path


def _skill_evidence(raw_path: str) -> dict[str, object]:
    path = Path(raw_path)
    exists = path.is_file()
    content = path.read_bytes() if exists else b""
    return {
        "path": raw_path,
        "exists": exists,
        "sha256": hashlib.sha256(content).hexdigest() if exists else None,
        "size_bytes": len(content) if exists else None,
    }


__all__ = ["write_skill_revision_evidence"]
