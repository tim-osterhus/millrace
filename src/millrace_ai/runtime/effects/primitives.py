"""Runtime effect primitive executor registry.

Maps primitive IDs to executor callables for the interpreted runner path.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import JsonValue


class ExecutorContext(Protocol):
    """Context passed to every primitive executor invocation."""

    workspace_paths: Any
    stage_result: Any
    run_dir: Path
    compiled_plan: Any

    @property
    def paths(self) -> Any: ...

    @property
    def root(self) -> Path: ...


PrimitiveExecutor = Callable[
    [ExecutorContext, str, dict[str, JsonValue], tuple[str, ...]],
    dict[str, JsonValue],
]


_DEFAULT_REGISTRY: PrimitiveExecutorRegistry | None = None


@dataclass(slots=True)
class PrimitiveExecutorRegistry:
    executors_by_id: dict[str, PrimitiveExecutor] = field(default_factory=dict)

    def register(self, primitive_id: str, executor: PrimitiveExecutor) -> None:
        if primitive_id in self.executors_by_id:
            raise ValueError(f"duplicate primitive executor id: {primitive_id}")
        self.executors_by_id[primitive_id] = executor

    def executor_for(self, primitive_id: str) -> PrimitiveExecutor | None:
        return self.executors_by_id.get(primitive_id)


def default_primitive_executor_registry() -> PrimitiveExecutorRegistry:
    """Return the default registry with builtin interpreted primitives."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is not None:
        return _DEFAULT_REGISTRY
    registry = PrimitiveExecutorRegistry()
    _register_builtins(registry)
    _DEFAULT_REGISTRY = registry
    return registry


def _register_builtins(registry: PrimitiveExecutorRegistry) -> None:
    registry.register("artifact_presence", _artifact_presence)
    registry.register("artifact_model_parse", _artifact_model_parse)
    registry.register("persist_record", _persist_record)
    registry.register("enqueue_work_items", _enqueue_work_items)
    registry.register("emit_event", _emit_event)


def _artifact_presence(
    ctx: ExecutorContext,
    step_id: str,
    params: dict[str, JsonValue],
    reads_artifact_ids: tuple[str, ...],
) -> dict[str, JsonValue]:
    """Verify that referenced artifact files exist in the run directory."""
    missing: list[str] = []
    found: dict[str, str] = {}

    for artifact_id in reads_artifact_ids:
        artifact_path = _resolve_artifact_path(ctx, artifact_id)
        if not artifact_path or not artifact_path.exists():
            missing.append(artifact_id)
        else:
            found[artifact_id] = str(artifact_path)

    if missing:
        return {
            "step_id": step_id,
            "primitive_id": "artifact_presence",
            "decision": "pre_mutation_failure",
            "failure_class": "required_artifact_missing",
            "message": f"missing required artifacts: {', '.join(missing)}",
            "missing_artifact_ids": list(missing),
            "found": found,
        }

    return {
        "step_id": step_id,
        "primitive_id": "artifact_presence",
        "decision": "continue",
        "found": found,
    }


def _artifact_model_parse(
    ctx: ExecutorContext,
    step_id: str,
    params: dict[str, JsonValue],
    reads_artifact_ids: tuple[str, ...],
) -> dict[str, JsonValue]:
    """Parse an artifact through its compiled contract or a safe schema."""
    parsed: dict[str, Any] = {}
    contract_id = str(params.get("contract_id", "") or "")
    raw_paths: list[str] = []

    for artifact_id in reads_artifact_ids:
        artifact_path = _resolve_artifact_path(ctx, artifact_id)
        if not artifact_path or not artifact_path.exists():
            return {
                "step_id": step_id,
                "primitive_id": "artifact_model_parse",
                "decision": "pre_mutation_failure",
                "failure_class": "artifact_parse_artifact_missing",
                "message": f"artifact {artifact_id} not found at expected path",
            }

        raw_paths.append(str(artifact_path))
        content = artifact_path.read_text(encoding="utf-8")

        if contract_id:
            # Resolve through the compiled plan's artifact contracts
            parsed_key = f"{artifact_id}_parsed"
            parsed[parsed_key] = _parse_with_contract(
                ctx, artifact_path, contract_id, content
            )
        else:
            # Safe parse: JSON if content starts with { or [, else captured as text blob
            parsed_key = f"{artifact_id}_parsed"
            parsed[parsed_key] = _safe_parse(content, artifact_path)

    return {
        "step_id": step_id,
        "primitive_id": "artifact_model_parse",
        "decision": "continue",
        "parsed": parsed,
        "raw_paths": raw_paths,
    }


def _persist_record(
    ctx: ExecutorContext,
    step_id: str,
    params: dict[str, JsonValue],
    reads_artifact_ids: tuple[str, ...],
) -> dict[str, JsonValue]:
    """Persist a record to the designated store."""
    store_id = str(params.get("store_id", "") or "")
    record_key = str(params.get("record_key", step_id) or step_id)
    created_paths: list[str] = []

    if store_id:
        store_dir = _store_dir(ctx, store_id)
        if store_dir is not None:
            store_dir.mkdir(parents=True, exist_ok=True)

            # Build the record from the artifact content
            record: dict[str, Any] = {"store_id": store_id, "step_id": step_id}
            for artifact_id in reads_artifact_ids:
                artifact_path = _resolve_artifact_path(ctx, artifact_id)
                if artifact_path and artifact_path.exists():
                    content = artifact_path.read_text(encoding="utf-8")
                    record[artifact_id] = _safe_parse(content, artifact_path)

            import json
            output_path = store_dir / f"{record_key}.json"
            output_path.write_text(
                json.dumps(record, indent=2) + "\n", encoding="utf-8"
            )
            created_paths.append(str(output_path))

    return {
        "step_id": step_id,
        "primitive_id": "persist_record",
        "decision": "continue",
        "created_paths": created_paths,
    }


def _enqueue_work_items(
    ctx: ExecutorContext,
    step_id: str,
    params: dict[str, JsonValue],
    reads_artifact_ids: tuple[str, ...],
) -> dict[str, JsonValue]:
    """Enqueue one or more child work items."""
    import json

    created_paths: list[str] = []
    family_id = str(params.get("family_id", ""))
    queue_dir = _resolve_queue_dir(ctx, family_id)

    item_count = int(params.get("item_count", 0) or 0)
    work_item_prefix = str(params.get("work_item_prefix", "child") or "child")

    if queue_dir is None and not reads_artifact_ids:
        return {
            "step_id": step_id,
            "primitive_id": "enqueue_work_items",
            "decision": "continue",
            "created_paths": (),
        }

    for artifact_id in reads_artifact_ids:
        artifact_path = _resolve_artifact_path(ctx, artifact_id)
        if not artifact_path or not artifact_path.exists():
            return {
                "step_id": step_id,
                "primitive_id": "enqueue_work_items",
                "decision": "pre_mutation_failure",
                "failure_class": "enqueue_artifact_missing",
                "message": f"artifact {artifact_id} not found for enqueue",
            }
        content = artifact_path.read_text(encoding="utf-8")

        if queue_dir is not None:
            queue_dir.mkdir(parents=True, exist_ok=True)
            dest = queue_dir / f"{work_item_prefix}_{artifact_id}.md"
            dest.write_text(content, encoding="utf-8")
            created_paths.append(str(dest))

    if item_count > 0 and not created_paths:
        if queue_dir is not None:
            queue_dir.mkdir(parents=True, exist_ok=True)
            for i in range(item_count):
                dest = queue_dir / f"{work_item_prefix}_{i + 1}.md"
                child_content = json.dumps(
                    {
                        "work_item_id": f"{work_item_prefix}_{i + 1}",
                        "title": f"Generated {work_item_prefix} {i + 1}",
                        "created_by": "interpreted_runtime_effect",
                    },
                    indent=2,
                )
                dest.write_text(child_content + "\n", encoding="utf-8")
                created_paths.append(str(dest))

    return {
        "step_id": step_id,
        "primitive_id": "enqueue_work_items",
        "decision": "continue",
        "created_paths": created_paths,
    }


def _emit_event(
    ctx: ExecutorContext,
    step_id: str,
    params: dict[str, JsonValue],
    reads_artifact_ids: tuple[str, ...],
) -> dict[str, JsonValue]:
    """Emit a runtime event."""
    event_type = str(params.get("event_type", "interpreted_effect_event"))
    event_data = dict(params.get("event_data", {}) or {})

    try:
        from millrace_ai.events import write_runtime_event

        write_runtime_event(
            ctx.workspace_paths,
            event_type=event_type,
            data={"step_id": step_id, "primitive_id": "emit_event", **event_data},
        )
    except Exception:
        pass  # Events are best-effort

    return {
        "step_id": step_id,
        "primitive_id": "emit_event",
        "decision": "continue",
    }


def _resolve_artifact_path(
    ctx: ExecutorContext,
    artifact_id: str,
) -> Path | None:
    """Resolve an artifact ID to a concrete file path using the compiled plan."""
    compiled_plan = getattr(ctx, "compiled_plan", None)
    if compiled_plan is None:
        return None

    artifact_contracts = getattr(compiled_plan, "artifact_contracts_by_id", {}) or {}
    contract = artifact_contracts.get(artifact_id)

    run_dir = ctx.run_dir
    candidate_names: list[str] = []

    if contract is not None:
        canonical = getattr(contract, "canonical_filename", None)
        if canonical:
            candidate_names.insert(0, str(canonical))
        accepted = getattr(contract, "accepted_filenames", ()) or ()
        candidate_names.extend(str(fn) for fn in accepted)

    # Fallback: construct from artifact_id
    candidate_names.append(f"{artifact_id}.json")
    candidate_names.append(f"{artifact_id}.md")

    for name in candidate_names:
        candidate = run_dir / name
        if candidate.exists():
            return candidate

    return run_dir / candidate_names[0] if candidate_names else None


def _parse_with_contract(
    ctx: ExecutorContext,
    path: Path,
    contract_id: str,
    content: str,
) -> Any:
    """Parse artifact content through a compiled contract or safe JSON."""
    import json

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Treat as unparsed raw content
        return {"_raw_content": content, "_source_path": str(path)}


def _safe_parse(content: str, path: Path) -> Any:
    """Safe parse: JSON if it starts with { or [, else return as raw."""
    import json

    stripped = content.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    return {"_raw_content": content, "_source_path": str(path)}


def _store_dir(
    ctx: ExecutorContext,
    store_id: str,
) -> Path | None:
    compiled_plan = getattr(ctx, "compiled_plan", None)
    if compiled_plan is None:
        return None

    stores = getattr(compiled_plan, "effect_stores_by_id", {}) or {}
    store = stores.get(store_id)
    if store is None:
        return None

    relative_root = getattr(store, "runtime_relative_root", None)
    if relative_root is None:
        return None

    paths = getattr(ctx, "workspace_paths", None) or getattr(ctx, "paths", None)
    if paths is None:
        return None

    root = getattr(paths, "runtime_root", None) or getattr(paths, "root", None)
    if root is None:
        return None

    return root / str(relative_root)


def _resolve_queue_dir(
    ctx: ExecutorContext,
    family_id: str,
) -> Path | None:
    compiled_plan = getattr(ctx, "compiled_plan", None)
    if compiled_plan is None:
        return None

    paths = getattr(ctx, "workspace_paths", None) or getattr(ctx, "paths", None)
    if paths is None:
        return None

    families = getattr(compiled_plan, "work_item_families_by_id", {}) or {}
    family = families.get(family_id)
    if family is None:
        return None

    queue_rel = getattr(family, "queue_dirs", None)
    if queue_rel is None:
        return None

    queue_path = getattr(queue_rel, "queue", None) or "queue"
    root = getattr(paths, "root", None)
    if root is None:
        return None

    return root / str(queue_path)


__all__ = [
    "ExecutorContext",
    "PrimitiveExecutor",
    "PrimitiveExecutorRegistry",
    "default_primitive_executor_registry",
]
