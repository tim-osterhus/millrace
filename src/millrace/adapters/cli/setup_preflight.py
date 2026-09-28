"""Advisory read-only context inspection using the runtime's capture policy."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256

from millrace.adapters.cli import context_checkout as capture
from millrace.adapters.cli.context import CliWorkspacePaths
from millrace.adapters.cli.context_writeback import _path_under_any, _write_rule_roots
from millrace.contracts.compiled_plan import (
    SelectedCompiledPlan,
    context_binding_authority_refusal,
)
from millrace.contracts.setup import (
    ContextSourceCoverage,
    ContextWriteCoverage,
    Disposition,
    ProposedContextWrite,
    SetupRefusal,
    StageContextCoverage,
    digest,
)
from millrace.substrate.cas import ContentAddressedByteStore


@dataclass(frozen=True, slots=True)
class ContextIssue:
    code: str
    stage_id: str
    source_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ContextPreflight:
    coverage: tuple[StageContextCoverage, ...]
    issues: tuple[ContextIssue, ...]
    disposition: Disposition

    @property
    def context_digest(self) -> str:
        return digest([asdict(c) for c in self.coverage])


def inspect_context(
    plan: SelectedCompiledPlan,
    paths: CliWorkspacePaths,
    cas: ContentAddressedByteStore,
) -> ContextPreflight:
    """Only the inventory owner passes an independently admitted selected plan.

    Inspect every selected binding, including conditional recovery. This is a
    conservative superset of reachable stages, so no alternate route is omitted.
    No prepare/publish/CAS-put or runner adapter is invoked.
    """
    if context_binding_authority_refusal(plan) is not None:
        return ContextPreflight(
            (), (ContextIssue("context_authority_invalid", "plan"),), "block"
        )
    conditional = {str(p.recovery_stage_kind_id) for p in plan.recovery_policies}
    conditional -= {str(r.stage_kind_id) for r in plan.external_enqueue_routes}
    first = _inspect_bindings(plan, paths, cas, conditional)
    second = _inspect_bindings(plan, paths, cas, conditional)
    if first != second:
        # Each source read already checks its own before/after identity and bytes.
        # A second complete sweep also detects cross-stage changes during inspection.
        return replace(
            second,
            issues=(*second.issues, ContextIssue("context_unstable", "plan")),
            disposition="block",
        )
    return first


def _inspect_bindings(
    plan: SelectedCompiledPlan,
    paths: CliWorkspacePaths,
    cas: ContentAddressedByteStore,
    conditional: set[str],
) -> ContextPreflight:
    coverage: list[StageContextCoverage] = []
    issues: list[ContextIssue] = []
    for binding in plan.context_bindings:
        stage = str(binding.stage_kind_id)
        stage_issues: list[ContextIssue] = []
        sources: list[ContextSourceCoverage] = []
        selections: dict[tuple[str, str, bool], capture._SourceSelection] = {}
        authority_error: str | None = None
        try:
            authority = capture._validate_paths(
                paths=paths, binding=binding, cas_store=cas
            )
            selected = capture._validate_sources(
                binding=binding, path_authority=authority
            )
            roots = tuple(
                s.declaration.source_ref
                for s in selected
                if s.declaration.source_kind == "workspace_relative_root"
            )
            if isinstance(_write_rule_roots(binding, selected_roots=roots), str):
                authority_error = "context_write_rules_invalid"
            selections = {
                (s.declaration.source_kind, s.declaration.source_ref, s.required): s
                for s in selected
            }
        except (ValueError, OSError, capture._CaptureInstability) as exc:
            authority_error = _capture_refusal(exc)
        for required, declarations in (
            (True, binding.required_sources),
            (False, binding.discoverable_sources),
        ):
            for source in declarations:
                metadata = {**asdict(source), "required": required}
                disposition: Disposition = "pass"
                observation: dict[str, object]
                code: str | None = authority_error
                if code is not None:
                    observation = {"state": code}
                    disposition = "block"
                elif source.source_kind != "workspace_relative_root":
                    # There is no dispatch/attempt/lineage yet. Even omit_if_absent
                    # is a runtime decision; do not invent an empty accepted set.
                    disposition = "deferred"
                    code = "context_runtime_artifact_deferred"
                    observation = {"state": code}
                else:
                    selection = selections[
                        (source.source_kind, source.source_ref, required)
                    ]
                    try:
                        result = capture._capture_workspace_sources(
                            selections=(selection,),
                            workspace=paths.workspace_path,
                            protect_optional_roots=binding.mutation_policy
                            == "reconcile_selected_writes",
                        )[0]
                        observation = {
                            "state": "captured"
                            if result.omission is None
                            else "omitted",
                            "omission": asdict(result.omission)
                            if result.omission is not None
                            else None,
                            "files": [
                                {
                                    "path": f.checkout_path,
                                    "bytes": len(f.payload),
                                    "sha256": "sha256:" + sha256(f.payload).hexdigest(),
                                }
                                for f in result.files
                            ],
                        }
                        if result.omission is not None:
                            code = "context_optional_omitted"
                    except (ValueError, OSError, capture._CaptureInstability) as exc:
                        code = _capture_refusal(exc)
                        disposition = "block"
                        observation = {"state": code}
                evidence = digest(
                    {
                        "domain": "Core.setup.context-source.v1",
                        "binding": asdict(binding),
                        "source": metadata,
                        "disposition": disposition,
                        "capture": observation,
                    }
                )
                sources.append(
                    ContextSourceCoverage(
                        source.source_kind,
                        source.source_ref,
                        required,
                        source.max_files,
                        source.max_bytes,
                        source.empty_policy,
                        disposition,
                        evidence,
                    )
                )
                if code is not None:
                    stage_issues.append(ContextIssue(code, stage, source.source_ref))
        if authority_error is not None and not sources:
            stage_issues.append(ContextIssue(authority_error, stage))
        # Hydration limits govern later selected catalog entries and receipts.
        # Capturing the catalog does not hydrate all its files. Preserve these
        # limits without inventing a cumulative hydration request here.
        coverage.append(
            StageContextCoverage(
                stage,
                str(binding.id),
                stage in conditional,
                str(binding.router_asset_id),
                binding.checkout_root,
                binding.max_hydrated_files,
                binding.max_hydrated_bytes,
                binding.materialization_retention,
                binding.mutation_policy,
                tuple(sources),
                tuple(
                    ContextWriteCoverage(w.relative_root, w.disposition)
                    for w in binding.write_rules
                ),
                str(binding.writeback_terminal_action_id)
                if binding.writeback_terminal_action_id is not None
                else None,
                str(binding.writeback_artifact_schema_id)
                if binding.writeback_artifact_schema_id is not None
                else None,
            )
        )
        issues.extend(stage_issues)
    deferred = any(s.disposition == "deferred" for c in coverage for s in c.sources)
    blocked = any(
        i.code not in {"context_optional_omitted", "context_runtime_artifact_deferred"}
        for i in issues
    )
    return ContextPreflight(
        tuple(coverage),
        tuple(issues),
        "block" if blocked else "deferred" if deferred else "pass",
    )


def _capture_refusal(exc: ValueError | OSError | capture._CaptureInstability) -> str:
    # Runtime errors contain constant policy descriptions, but never forward
    # exception text or captured content to the public response.
    message = str(exc)
    if isinstance(exc, capture._CaptureInstability):
        return "context_unstable"
    for marker, code in (
        ("symlink", "context_symlink"),
        ("missing", "context_missing"),
        ("empty", "context_empty"),
        ("bound", "context_capture_bound_exceeded"),
        ("UTF-8", "context_not_utf8"),
        ("special file", "context_unsafe_file"),
    ):
        if marker in message:
            return code
    return "context_unsafe_or_unreadable"


def inspect_write(
    proposed: ProposedContextWrite,
    coverage: tuple[StageContextCoverage, ...],
    paths: CliWorkspacePaths,
) -> str:
    """Inspect a classification against owner-resolved coverage; never apply it."""
    if type(proposed) is not ProposedContextWrite:
        raise SetupRefusal("invalid_context_write_request")
    proposed.validate()
    stage = next((c for c in coverage if c.stage_id == proposed.stage_id), None)
    if stage is None:
        raise SetupRefusal("context_write_stage_not_selected")
    try:
        path = capture._safe_relative_path(proposed.path, "proposed path")
        target = paths.workspace_path / path
        capture._reject_symlink_components(target, stop=paths.workspace_path)
        if any(
            capture._paths_overlap(target, p)
            for p in (
                paths.db_path,
                paths.cas_path,
                paths.workspace_path / stage.checkout_root,
            )
        ):
            raise ValueError("protected runtime path")
    except (ValueError, OSError, capture._CaptureInstability) as exc:
        raise SetupRefusal("context_write_unsafe_path") from exc
    roots = tuple(
        s.source_ref
        for selected_stage in coverage
        for s in selected_stage.sources
        if s.source_kind == "workspace_relative_root"
    )
    if not _path_under_any(path, roots):
        return "outside_selected_context_authority"
    allowed = tuple(
        w.relative_root
        for w in stage.write_rules
        if w.disposition == proposed.disposition
    )
    if stage.mutation_policy != "reconcile_selected_writes" or not _path_under_any(
        path, allowed
    ):
        raise SetupRefusal("context_write_outside_selected_rules")
    if (
        stage.writeback_terminal_action_id is None
        or stage.writeback_artifact_schema_id is None
    ):
        raise SetupRefusal("context_writeback_authority_unavailable")
    return "selected_" + proposed.disposition + "_advisory"
