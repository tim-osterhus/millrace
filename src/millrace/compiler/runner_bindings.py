"""Selected runner binding normalization.

This module owns source-side selected runner adapter policy. Runtime runner
adapters may refuse unsupported selected authority, but they must not rewrite it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from millrace.compiler.diagnostics import compiler_error, compiler_warning
from millrace.compiler.source import is_non_empty_text, is_sequence, records
from millrace.contracts import Diagnostic
from millrace.contracts.diagnostics import DiagnosticContextValue

CODEX_ADAPTER_KIND = "codex"
MILLFORGE_ADAPTER_KIND = "millforge"
DEFAULT_SELECTED_RUNNER_ADAPTER_KIND = MILLFORGE_ADAPTER_KIND
DEFAULT_RUNNER_INVOCATION_TIMEOUT_SECONDS = 3600
INVALID_RUNNER_INVOCATION_TIMEOUT_SECONDS = "invalid_runner_invocation_timeout_seconds"
RUNNER_ADAPTER_KIND_DEFAULTED = "runner_adapter_kind_defaulted"
RUNNER_ADAPTER_KIND_UNSUPPORTED = "runner_adapter_kind_unsupported"
RUNNER_COMPONENT_AUTHORITY_CANNOT_DEFAULT_ADAPTER = (
    "runner_component_authority_cannot_default_adapter"
)
RUNNER_DEFAULT_COMPONENT_AUTHORITY_INCOMPATIBLE = (
    "runner_default_component_authority_incompatible"
)
RUNNER_DEFAULT_COMPONENT_CAPABILITY_UNUSABLE = (
    "runner_default_component_capability_unusable"
)
RUNNER_DEFAULT_COMPONENT_MAPPING_INCOMPLETE = (
    "runner_default_component_mapping_incomplete"
)

_COMPONENT_SELECTOR_FIELDS = (
    "component_kind",
    "component_id",
    "component_version",
    "provider_distribution",
    "provider_version",
    "descriptor_media_type",
)
_MILLFORGE_DEFAULT_COMPONENT_SELECTOR = (
    ("component_kind", "runner"),
    ("component_id", "millforge-base"),
    ("component_version", "2"),
    ("provider_distribution", "millforge"),
    ("provider_version", "0.1.0"),
    ("descriptor_media_type", "application/json"),
)
_MILLFORGE_DEFAULT_COMPONENT_CAPABILITY_IDS = frozenset(
    {
        "terminal.intent",
        "unrestricted.filesystem.read",
        "unrestricted.filesystem.write",
        "unrestricted.process.execute",
    }
)


@dataclass(frozen=True, slots=True)
class SelectedRunnerAdapterPolicy:
    default_adapter_kind: str = DEFAULT_SELECTED_RUNNER_ADAPTER_KIND
    supported_adapter_kinds: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {CODEX_ADAPTER_KIND, DEFAULT_SELECTED_RUNNER_ADAPTER_KIND}
        ),
    )
    component_bound_adapter_kinds: frozenset[str] = field(
        default_factory=lambda: frozenset({MILLFORGE_ADAPTER_KIND}),
    )
    default_invalid_adapter_kinds: bool = True
    default_component_selector: tuple[tuple[str, str], ...] | None = (
        _MILLFORGE_DEFAULT_COMPONENT_SELECTOR
    )
    default_component_required_capability_ids: frozenset[str] = field(
        default_factory=lambda: _MILLFORGE_DEFAULT_COMPONENT_CAPABILITY_IDS,
    )
    default_component_requires_complete_mappings: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.default_adapter_kind, str) or not (
            self.default_adapter_kind.strip()
        ):
            raise ValueError("default_adapter_kind must be nonblank")
        supported = frozenset(self.supported_adapter_kinds)
        if any(not isinstance(item, str) or not item.strip() for item in supported):
            raise ValueError("supported_adapter_kinds must contain nonblank strings")
        if self.default_adapter_kind not in supported:
            raise ValueError("default_adapter_kind must be supported")
        component_bound = frozenset(self.component_bound_adapter_kinds)
        if not component_bound.issubset(supported):
            raise ValueError("component-bound adapter kinds must be supported")
        if type(self.default_invalid_adapter_kinds) is not bool:
            raise TypeError("default_invalid_adapter_kinds must be a bool")
        selector = self.default_component_selector
        required_capability_ids = frozenset(
            self.default_component_required_capability_ids
        )
        if selector is None:
            if required_capability_ids:
                raise ValueError(
                    "default component capabilities require a component selector"
                )
            if self.default_component_requires_complete_mappings:
                raise ValueError(
                    "complete default mappings require a component selector"
                )
        else:
            if (
                not isinstance(selector, tuple)
                or tuple(field_name for field_name, _value in selector)
                != _COMPONENT_SELECTOR_FIELDS
                or any(
                    not isinstance(value, str) or not value.strip()
                    for _field_name, value in selector
                )
            ):
                raise ValueError("default_component_selector must be canonical")
            if self.default_adapter_kind not in component_bound:
                raise ValueError(
                    "default component selector requires a component-bound adapter"
                )
        if any(
            not isinstance(item, str) or not item.strip()
            for item in required_capability_ids
        ):
            raise ValueError(
                "default component capability ids must contain nonblank strings"
            )
        if type(self.default_component_requires_complete_mappings) is not bool:
            raise TypeError(
                "default_component_requires_complete_mappings must be a bool"
            )
        object.__setattr__(self, "supported_adapter_kinds", supported)
        object.__setattr__(self, "component_bound_adapter_kinds", component_bound)
        object.__setattr__(
            self,
            "default_component_required_capability_ids",
            required_capability_ids,
        )


DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY = SelectedRunnerAdapterPolicy()


def normalize_selected_runner_bindings(
    source: Mapping[str, object],
    *,
    workflow_id: str,
    workflow_version: str,
    diagnostics: list[Diagnostic],
    policy: SelectedRunnerAdapterPolicy = DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY,
    declaration_path_prefix: str = "",
    diagnostic_context: Mapping[str, DiagnosticContextValue] | None = None,
) -> Mapping[str, object]:
    """Return source whose selected runner bindings satisfy adapter policy."""

    _require_policy(policy)
    bindings = records(source, "runner_bindings")
    if not bindings:
        return source

    base_context: dict[str, DiagnosticContextValue] = {
        "workflow_id": workflow_id,
        "workflow_version": workflow_version,
        "source_kind": "workflow_source",
    }
    if diagnostic_context is not None:
        base_context.update(diagnostic_context)

    normalized_bindings: list[Mapping[str, object]] = []
    changed = False
    for index, binding in enumerate(bindings):
        path = f"{declaration_path_prefix}runner_bindings[{index}]"
        adapter_path = f"{path}.adapter_kind"
        timeout_path = f"{path}.invocation_timeout_seconds"
        runner_binding_id = _runner_binding_id(binding)
        normalized, binding_changed = _normalize_runner_component_authority(binding)
        raw_timeout = binding.get("invocation_timeout_seconds")
        if raw_timeout is None:
            normalized["invocation_timeout_seconds"] = (
                DEFAULT_RUNNER_INVOCATION_TIMEOUT_SECONDS
            )
            binding_changed = True
        elif type(raw_timeout) is int and raw_timeout <= 0:
            diagnostics.append(
                compiler_error(
                    code=INVALID_RUNNER_INVOCATION_TIMEOUT_SECONDS,
                    declaration_path=timeout_path,
                    message="Runner invocation timeout must be positive.",
                    context={
                        **base_context,
                        "runner_binding_id": runner_binding_id,
                        "authored_value": raw_timeout,
                        "minimum_accepted_value": 1,
                    },
                    hint=(
                        "Use a positive integer or omit the field for the "
                        "3600-second default."
                    ),
                )
            )
        raw_adapter_kind = binding.get("adapter_kind")
        if not _is_nonblank_adapter_kind(raw_adapter_kind):
            diagnostics.append(
                compiler_error(
                    code="missing_runner_adapter_kind",
                    declaration_path=adapter_path,
                    message="Runner binding is missing a non-empty adapter_kind.",
                    context={
                        **base_context,
                        "runner_binding_id": runner_binding_id,
                    },
                    hint=(
                        "Declare a non-empty adapter_kind. Malformed runner "
                        "bindings are not defaulted."
                    ),
                )
            )
            normalized_bindings.append(normalized)
            changed = changed or binding_changed
            continue

        adapter_kind = str(raw_adapter_kind)
        if adapter_kind in policy.supported_adapter_kinds:
            if adapter_kind in policy.component_bound_adapter_kinds:
                _append_component_bound_authority_diagnostics(
                    binding=normalized,
                    path=path,
                    context={
                        **base_context,
                        "runner_binding_id": runner_binding_id,
                        "adapter_kind": adapter_kind,
                    },
                    diagnostics=diagnostics,
                )
            normalized_bindings.append(normalized)
            changed = changed or binding_changed
            continue
        if not policy.default_invalid_adapter_kinds:
            diagnostics.append(
                compiler_error(
                    code=RUNNER_ADAPTER_KIND_UNSUPPORTED,
                    declaration_path=adapter_path,
                    message="Selected runner adapter kind is unsupported.",
                    context={
                        **base_context,
                        "runner_binding_id": runner_binding_id,
                        "adapter_kind": adapter_kind,
                    },
                    hint=(
                        "Use a supported adapter_kind or enable explicit "
                        "selected-runner defaulting before compile."
                    ),
                )
            )
            normalized_bindings.append(normalized)
            changed = changed or binding_changed
            continue

        selector = policy.default_component_selector
        if selector is not None:
            if _append_default_component_authority_diagnostics(
                source=source,
                binding=normalized,
                path=path,
                context={
                    **base_context,
                    "runner_binding_id": runner_binding_id,
                    "adapter_kind": adapter_kind,
                },
                policy=policy,
                diagnostics=diagnostics,
            ):
                normalized_bindings.append(normalized)
                changed = changed or binding_changed
                continue
        elif _has_runner_component_authority(binding):
            diagnostics.append(
                compiler_error(
                    code=RUNNER_COMPONENT_AUTHORITY_CANNOT_DEFAULT_ADAPTER,
                    declaration_path=adapter_path,
                    message=(
                        "Runner component authority cannot survive adapter defaulting."
                    ),
                    context={
                        **base_context,
                        "runner_binding_id": runner_binding_id,
                        "adapter_kind": adapter_kind,
                    },
                    hint=(
                        "Use a supported adapter_kind when selecting a component "
                        "pin or terminal result mappings."
                    ),
                )
            )
            normalized_bindings.append(normalized)
            changed = changed or binding_changed
            continue

        normalized["adapter_kind"] = policy.default_adapter_kind
        binding_changed = True
        normalized_bindings.append(normalized)
        diagnostics.append(
            compiler_warning(
                code=RUNNER_ADAPTER_KIND_DEFAULTED,
                declaration_path=adapter_path,
                message=(
                    "Selected runner adapter kind is unsupported and was "
                    "defaulted before selected-plan construction."
                ),
                context={
                    **base_context,
                    "runner_binding_id": runner_binding_id,
                    "original_adapter_kind": adapter_kind,
                    "default_adapter_kind": policy.default_adapter_kind,
                },
                hint=(
                    "The selected compiled plan uses the default adapter kind. "
                    "The daemon will not remap runner bindings at execution time."
                ),
            )
        )
        changed = changed or binding_changed

    if not changed:
        return source
    normalized_source = dict(source)
    normalized_source["runner_bindings"] = normalized_bindings
    return normalized_source


def _runner_binding_id(binding: Mapping[str, object]) -> str:
    raw_id = binding.get("id")
    return str(raw_id) if is_non_empty_text(raw_id) else ""


def _normalize_runner_component_authority(
    binding: Mapping[str, object],
) -> tuple[dict[str, object], bool]:
    normalized = dict(binding)
    changed = False
    raw_pin = binding.get("component_pin")
    if isinstance(raw_pin, Mapping):
        pin = dict(raw_pin)
        for field_name in (
            "required_capability_ids",
            "legal_terminal_result_ids",
        ):
            raw_values = pin.get(field_name, ())
            if not is_sequence(raw_values):
                continue
            canonical_values = tuple(
                sorted(
                    (str(value) for value in raw_values),
                    key=lambda value: value.encode("utf-8"),
                )
            )
            changed = changed or canonical_values != raw_values
            pin[field_name] = canonical_values
        changed = changed or pin != raw_pin
        normalized["component_pin"] = pin

    raw_mappings = binding.get("terminal_result_mappings")
    if is_sequence(raw_mappings):
        mappings = tuple(
            dict(item) for item in raw_mappings if isinstance(item, Mapping)
        )
        canonical_mappings = tuple(
            sorted(
                mappings,
                key=lambda item: (
                    str(item["stage_kind_id"]).encode("utf-8"),
                    str(item["runner_result_id"]).encode("utf-8"),
                    str(item["outcome_id"]).encode("utf-8"),
                ),
            )
        )
        changed = changed or canonical_mappings != raw_mappings
        normalized["terminal_result_mappings"] = canonical_mappings
    return normalized, changed


def _has_runner_component_authority(binding: Mapping[str, object]) -> bool:
    if binding.get("component_pin") is not None:
        return True
    mappings = binding.get("terminal_result_mappings")
    return is_sequence(mappings) and bool(mappings)


def _append_default_component_authority_diagnostics(
    *,
    source: Mapping[str, object],
    binding: Mapping[str, object],
    path: str,
    context: Mapping[str, DiagnosticContextValue],
    policy: SelectedRunnerAdapterPolicy,
    diagnostics: list[Diagnostic],
) -> bool:
    selector = policy.default_component_selector
    if selector is None:
        return False
    pin = binding.get("component_pin")
    if not isinstance(pin, Mapping):
        diagnostics.append(
            compiler_error(
                code=RUNNER_DEFAULT_COMPONENT_AUTHORITY_INCOMPATIBLE,
                declaration_path=f"{path}.component_pin",
                message=(
                    "Default adapter selection requires its authored component pin."
                ),
                context=context,
                hint="Author the configured default component selector exactly.",
            )
        )
        return True

    expected_selector = dict(selector)
    mismatched_fields = tuple(
        field_name
        for field_name, expected_value in expected_selector.items()
        if pin.get(field_name) != expected_value
    )
    if mismatched_fields:
        field_name = mismatched_fields[0]
        diagnostics.append(
            compiler_error(
                code=RUNNER_DEFAULT_COMPONENT_AUTHORITY_INCOMPATIBLE,
                declaration_path=f"{path}.component_pin.{field_name}",
                message="Authored component pin does not match the default selector.",
                context={
                    **context,
                    "mismatched_fields": mismatched_fields,
                },
                hint="Match the configured default component selector exactly.",
            )
        )
        return True

    required_capability_ids = policy.default_component_required_capability_ids
    raw_pin_capability_ids = pin.get("required_capability_ids", ())
    pin_capability_ids = (
        frozenset(
            str(value) for value in raw_pin_capability_ids if isinstance(value, str)
        )
        if is_sequence(raw_pin_capability_ids)
        else frozenset()
    )
    raw_binding_capability_ids = binding.get("required_capability_ids", ())
    binding_capability_ids = (
        frozenset(
            str(value) for value in raw_binding_capability_ids if isinstance(value, str)
        )
        if is_sequence(raw_binding_capability_ids)
        else frozenset()
    )
    capabilities = {
        str(capability.get("id")): capability
        for capability in records(source, "capabilities")
        if is_non_empty_text(capability.get("id"))
    }
    unusable_capability_ids = tuple(
        sorted(
            (
                capability_id
                for capability_id in required_capability_ids
                if capability_id not in pin_capability_ids
                or capability_id not in binding_capability_ids
                or capability_id not in capabilities
                or capabilities[capability_id].get("support_status") != "supported"
                or capabilities[capability_id].get("grant_status") != "granted"
            ),
            key=lambda value: value.encode("utf-8"),
        )
    )
    if unusable_capability_ids:
        diagnostics.append(
            compiler_error(
                code=RUNNER_DEFAULT_COMPONENT_CAPABILITY_UNUSABLE,
                declaration_path=f"{path}.required_capability_ids",
                message=(
                    "Default adapter selection requires usable authored component "
                    "capabilities."
                ),
                context={
                    **context,
                    "unusable_capability_ids": unusable_capability_ids,
                },
                hint=(
                    "Select and grant every capability required by the configured "
                    "default component."
                ),
            )
        )
        return True

    if policy.default_component_requires_complete_mappings:
        raw_stage_kind_ids = binding.get("stage_kind_ids", ())
        raw_result_ids = pin.get("legal_terminal_result_ids", ())
        stage_kind_ids = raw_stage_kind_ids if is_sequence(raw_stage_kind_ids) else ()
        result_ids = raw_result_ids if is_sequence(raw_result_ids) else ()
        expected_mapping_keys = {
            (stage_kind_id, result_id)
            for stage_kind_id in stage_kind_ids
            if isinstance(stage_kind_id, str)
            for result_id in result_ids
            if isinstance(result_id, str)
        }
        raw_mappings = binding.get("terminal_result_mappings", ())
        mappings = raw_mappings if is_sequence(raw_mappings) else ()
        authored_mapping_keys = {
            (
                str(mapping.get("stage_kind_id", "")),
                str(mapping.get("runner_result_id", "")),
            )
            for mapping in mappings
            if isinstance(mapping, Mapping)
        }
        if authored_mapping_keys != expected_mapping_keys:
            diagnostics.append(
                compiler_error(
                    code=RUNNER_DEFAULT_COMPONENT_MAPPING_INCOMPLETE,
                    declaration_path=f"{path}.terminal_result_mappings",
                    message=(
                        "Default adapter selection requires one mapping for every "
                        "bound stage and configured result."
                    ),
                    context={
                        **context,
                        "missing_mapping_keys": tuple(
                            f"{stage_kind_id}:{result_id}"
                            for stage_kind_id, result_id in sorted(
                                expected_mapping_keys - authored_mapping_keys
                            )
                        ),
                        "extra_mapping_keys": tuple(
                            f"{stage_kind_id}:{result_id}"
                            for stage_kind_id, result_id in sorted(
                                authored_mapping_keys - expected_mapping_keys
                            )
                        ),
                    },
                    hint="Author the complete stage-scoped terminal mapping set.",
                )
            )
            return True
    return False


def _append_component_bound_authority_diagnostics(
    *,
    binding: Mapping[str, object],
    path: str,
    context: Mapping[str, DiagnosticContextValue],
    diagnostics: list[Diagnostic],
) -> None:
    if binding.get("component_pin") is None:
        diagnostics.append(
            compiler_error(
                code="missing_runner_component_authority",
                declaration_path=f"{path}.component_pin",
                message="This adapter kind requires one runner component pin.",
                context=context,
                hint="Declare the selected runner component pin.",
            )
        )
    mappings = binding.get("terminal_result_mappings")
    if not is_sequence(mappings) or not mappings:
        diagnostics.append(
            compiler_error(
                code="missing_runner_terminal_mapping_authority",
                declaration_path=f"{path}.terminal_result_mappings",
                message="This adapter kind requires at least one terminal mapping.",
                context=context,
                hint="Declare selected stage terminal-result mappings.",
            )
        )


def _is_nonblank_adapter_kind(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _require_policy(policy: SelectedRunnerAdapterPolicy) -> None:
    if not isinstance(policy, SelectedRunnerAdapterPolicy):
        raise TypeError("selected runner policy must be SelectedRunnerAdapterPolicy")


__all__ = (
    "DEFAULT_RUNNER_INVOCATION_TIMEOUT_SECONDS",
    "DEFAULT_SELECTED_RUNNER_ADAPTER_KIND",
    "DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY",
    "INVALID_RUNNER_INVOCATION_TIMEOUT_SECONDS",
    "MILLFORGE_ADAPTER_KIND",
    "RUNNER_ADAPTER_KIND_DEFAULTED",
    "RUNNER_ADAPTER_KIND_UNSUPPORTED",
    "RUNNER_COMPONENT_AUTHORITY_CANNOT_DEFAULT_ADAPTER",
    "RUNNER_DEFAULT_COMPONENT_AUTHORITY_INCOMPATIBLE",
    "RUNNER_DEFAULT_COMPONENT_CAPABILITY_UNUSABLE",
    "RUNNER_DEFAULT_COMPONENT_MAPPING_INCOMPLETE",
    "SelectedRunnerAdapterPolicy",
    "normalize_selected_runner_bindings",
)
