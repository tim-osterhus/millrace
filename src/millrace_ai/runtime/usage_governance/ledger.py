"""Usage-governance token ledger persistence and reconciliation."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import StageResultEnvelope
from millrace_ai.paths import WorkspacePaths

from .models import UsageGovernanceLedgerEntry


def record_stage_result_usage(
    paths: WorkspacePaths,
    *,
    stage_result: StageResultEnvelope,
    stage_result_path: Path,
    now: datetime,
    daemon_session_id: str | None,
    config: RuntimeConfig,
) -> bool:
    if not should_record_runtime_tokens(config, stage_result):
        return False

    dedupe_key = stage_result_dedupe_key(paths, stage_result_path)
    existing_keys = {entry.dedupe_key for entry in load_usage_governance_ledger(paths)}
    if dedupe_key in existing_keys:
        return False

    entry = ledger_entry_from_stage_result(
        paths,
        stage_result=stage_result,
        stage_result_path=stage_result_path,
        counted_at=now,
        daemon_session_id=daemon_session_id,
    )
    append_ledger_entry(paths, entry)
    return True


def reconcile_usage_ledger_from_stage_results(
    paths: WorkspacePaths,
    *,
    now: datetime,
    daemon_session_id: str | None,
    config: RuntimeConfig,
) -> int:
    if not config.usage_governance.enabled or not config.usage_governance.runtime_token_rules.enabled:
        return 0

    existing_keys = {entry.dedupe_key for entry in load_usage_governance_ledger(paths)}
    repaired = 0
    for stage_result_path in sorted(paths.runs_dir.glob("*/stage_results/*.json")):
        dedupe_key = stage_result_dedupe_key(paths, stage_result_path)
        if dedupe_key in existing_keys:
            continue
        try:
            payload = json.loads(stage_result_path.read_text(encoding="utf-8"))
            stage_result = StageResultEnvelope.model_validate(payload)
        except Exception:
            continue
        if not should_record_runtime_tokens(config, stage_result):
            continue
        entry = ledger_entry_from_stage_result(
            paths,
            stage_result=stage_result,
            stage_result_path=stage_result_path,
            counted_at=now,
            daemon_session_id=daemon_session_id,
        )
        append_ledger_entry(paths, entry)
        existing_keys.add(dedupe_key)
        repaired += 1
    return repaired


def load_usage_governance_ledger(paths: WorkspacePaths) -> tuple[UsageGovernanceLedgerEntry, ...]:
    if not paths.usage_governance_ledger_file.is_file():
        return ()
    entries: list[UsageGovernanceLedgerEntry] = []
    for line in paths.usage_governance_ledger_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entries.append(UsageGovernanceLedgerEntry.model_validate_json(line))
    return tuple(entries)


def should_record_runtime_tokens(
    config: RuntimeConfig,
    stage_result: StageResultEnvelope,
) -> bool:
    return (
        config.usage_governance.enabled
        and config.usage_governance.runtime_token_rules.enabled
        and stage_result.token_usage is not None
    )


def ledger_entry_from_stage_result(
    paths: WorkspacePaths,
    *,
    stage_result: StageResultEnvelope,
    stage_result_path: Path,
    counted_at: datetime,
    daemon_session_id: str | None,
) -> UsageGovernanceLedgerEntry:
    assert stage_result.token_usage is not None
    relative_path = stage_result_dedupe_key(paths, stage_result_path)
    return UsageGovernanceLedgerEntry(
        dedupe_key=relative_path,
        counted_at=counted_at,
        stage_completed_at=stage_result.completed_at,
        plane=stage_result.plane,
        run_id=stage_result.run_id,
        stage_id=stage_result.stage_kind_id or stage_result.stage.value,
        work_item_kind=stage_result.work_item_kind,
        work_item_id=stage_result.work_item_id,
        token_usage=stage_result.token_usage,
        stage_result_path=relative_path,
        daemon_session_id=daemon_session_id,
    )


def append_ledger_entry(paths: WorkspacePaths, entry: UsageGovernanceLedgerEntry) -> None:
    paths.usage_governance_ledger_file.parent.mkdir(parents=True, exist_ok=True)
    with paths.usage_governance_ledger_file.open("a", encoding="utf-8") as handle:
        handle.write(entry.model_dump_json() + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def stage_result_dedupe_key(paths: WorkspacePaths, stage_result_path: Path) -> str:
    try:
        return stage_result_path.resolve().relative_to(paths.root).as_posix()
    except ValueError:
        return stage_result_path.resolve().as_posix()


__all__ = [
    "append_ledger_entry",
    "ledger_entry_from_stage_result",
    "load_usage_governance_ledger",
    "reconcile_usage_ledger_from_stage_results",
    "record_stage_result_usage",
    "should_record_runtime_tokens",
    "stage_result_dedupe_key",
]
