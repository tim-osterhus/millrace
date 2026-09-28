"""Core setup v1 inspection transport. Inspection never grants execution authority."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, fields
from hashlib import sha256
from typing import Any, Literal

ActionKind = Literal["demo", "useful_recipe"]
Disposition = Literal["pass", "block", "deferred"]
Category = Literal[
    "component",
    "command",
    "package",
    "runtime",
    "runner",
    "provider",
    "context",
    "management",
    "trust",
]
REQUIRED_CHECKS: dict[str, dict[str, Category]] = {
    "demo": {
        "demo.command": "command",
        "demo.package": "package",
        "demo.runtime": "runtime",
    },
    "useful_recipe": {
        "recipe.plan": "package",
        "recipe.context": "context",
        "recipe.runner": "runner",
    },
}


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def digest(value: object) -> str:
    return "sha256:" + sha256(canonical(value)).hexdigest()


class SetupRefusal(ValueError):
    """A stable public refusal code, never caller data or exception details."""


def _require(ok: bool) -> None:
    if not ok:
        raise SetupRefusal("setup_wire_unrepresentable")


def _text(value: object, maximum: int, minimum: int = 0) -> None:
    _require(type(value) is str and minimum <= len(value) <= maximum)


def _hash(value: object) -> None:
    _require(
        type(value) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None
    )


def _enum(value: object, choices: tuple[str, ...]) -> None:
    _require(type(value) is str and value in choices)


@dataclass(frozen=True, slots=True)
class SetupRequest:
    action_kind: ActionKind = "demo"
    workspace: str | None = None
    db: str | None = None
    cas: str | None = None
    plan_fingerprint: str | None = None
    management_mode: str = "headless"
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise SetupRefusal("unsupported_setup_contract")
        if self.action_kind not in REQUIRED_CHECKS:
            raise SetupRefusal("unsupported_setup_action")
        if self.management_mode != "headless":
            raise SetupRefusal("managed_setup_unsupported")
        for value in (self.workspace, self.db, self.cas, self.plan_fingerprint):
            if value is not None and (
                not isinstance(value, str) or not value.strip() or len(value) > 512
            ):
                raise SetupRefusal("invalid_setup_selection")
        if self.action_kind == "demo" and any(
            value is not None
            for value in (self.workspace, self.db, self.cas, self.plan_fingerprint)
        ):
            raise SetupRefusal("demo_selection_not_configurable")


@dataclass(frozen=True, slots=True)
class ComponentPin:
    distribution: str
    version: str
    artifact_sha256: str | None
    contract_version: str | None


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    workspace_id: str
    workspace_path: str
    store_schema: int = 11
    plan_schema: int = 18
    disposition: str = "initialized"


@dataclass(frozen=True, slots=True)
class DemoCommandIdentity:
    distribution: str
    version: str
    artifact_sha256: str
    adapter_version: str


@dataclass(frozen=True, slots=True)
class SetupSelection:
    action_kind: ActionKind
    component_pins: tuple[ComponentPin, ...]
    declared_next_action: str
    workspace: str | None = None
    package_id: str | None = None
    package_version: str | None = None
    source_digest: str | None = None
    import_record_digest: str | None = None
    manifest_digest: str | None = None
    plan_fingerprint: str | None = None
    local_config_digest: str | None = None
    runtime_identity: RuntimeIdentity | None = None
    management_mode: Literal["headless"] = "headless"
    provider_identity: None = None
    managed_mapping: None = None
    context_digest: str | None = None
    demo_command_identity: DemoCommandIdentity | None = None

    def validate(self) -> None:
        """Validate actual resolved values against the closed C01 inspection shape."""
        _enum(self.action_kind, ("demo", "useful_recipe"))
        _enum(self.management_mode, ("headless",))
        _text(self.declared_next_action, 256, 1)
        for value in (self.workspace, self.package_id, self.package_version):
            if value is not None:
                _text(value, 512)
        for value in (
            self.source_digest,
            self.import_record_digest,
            self.manifest_digest,
            self.plan_fingerprint,
            self.local_config_digest,
            self.context_digest,
        ):
            if value is not None:
                _hash(value)
        _require(
            type(self.component_pins) is tuple and 1 <= len(self.component_pins) <= 32
        )
        for pin in self.component_pins:
            _require(type(pin) is ComponentPin)
            _text(pin.distribution, 512, 1)
            _text(pin.version, 512, 1)
            if pin.artifact_sha256 is not None:
                _hash(pin.artifact_sha256)
            if pin.contract_version is not None:
                _text(pin.contract_version, 512)
        if self.runtime_identity is not None:
            runtime = self.runtime_identity
            _require(type(runtime) is RuntimeIdentity)
            _text(runtime.workspace_id, 512, 1)
            _text(runtime.workspace_path, 4096, 1)
            _require(type(runtime.store_schema) is int and runtime.store_schema == 11)
            _require(type(runtime.plan_schema) is int and runtime.plan_schema == 18)
            _enum(runtime.disposition, ("planned_isolated", "initialized"))
        _require(
            all(
                value is None
                for value in (
                    self.provider_identity,
                    self.managed_mapping,
                )
            )
        )

        if self.demo_command_identity is not None:
            command = self.demo_command_identity
            _require(type(command) is DemoCommandIdentity)
            _require(command.distribution == "millrace-ai")
            _text(command.version, 512, 1)
            _text(command.adapter_version, 512, 1)
            _hash(command.artifact_sha256)

    def to_wire(self) -> dict[str, object]:
        self.validate()
        # JSON conversion also detaches the immutable transport's tuples.
        result: dict[str, object] = json.loads(canonical(asdict(self)))
        return result


@dataclass(frozen=True, slots=True)
class SetupCheck:
    id: str
    owner: str
    affected_identity: str
    disposition: Disposition
    category: Category
    required: bool
    evidence_reference: str
    observed_at: str
    gates_next_action: bool
    producer_binding: str


@dataclass(frozen=True, slots=True)
class SetupBlocker:
    code: str
    owner: str
    affected_identity: str
    explanation: str
    safe_next_action: str
    retry_meaningful: bool = False
    progress_valid: bool = False


@dataclass(frozen=True, slots=True)
class ContextSourceCoverage:
    source_kind: str
    source_ref: str
    required: bool
    max_files: int
    max_bytes: int
    empty_policy: str
    disposition: Disposition
    evidence_reference: str

    def validate(self) -> None:
        _enum(
            self.source_kind,
            (
                "workspace_relative_root",
                "dispatch_material",
                "selected_artifacts",
                "selected_attempts",
            ),
        )
        _text(self.source_ref, 512, 1)
        _require(type(self.required) is bool)
        for value in (self.max_files, self.max_bytes):
            _require(type(value) is int and value >= 1)
        _enum(self.empty_policy, ("require_nonempty", "omit_if_absent"))
        _enum(self.disposition, ("pass", "block", "deferred"))
        _hash(self.evidence_reference)


@dataclass(frozen=True, slots=True)
class ContextWriteCoverage:
    relative_root: str
    disposition: str

    def validate(self) -> None:
        _text(self.relative_root, 4096, 1)
        _enum(self.disposition, ("direct_write", "protected_proposal"))


@dataclass(frozen=True, slots=True)
class StageContextCoverage:
    stage_id: str
    binding_id: str
    conditional: bool
    router_asset_id: str
    checkout_root: str
    max_hydrated_files: int
    max_hydrated_bytes: int
    materialization_retention: str
    mutation_policy: str
    sources: tuple[ContextSourceCoverage, ...]
    write_rules: tuple[ContextWriteCoverage, ...]
    writeback_terminal_action_id: str | None
    writeback_artifact_schema_id: str | None

    def validate(self) -> None:
        for value in (self.stage_id, self.binding_id, self.router_asset_id):
            _text(value, 512, 1)
        _require(type(self.conditional) is bool)
        _text(self.checkout_root, 4096, 1)
        for bound in (self.max_hydrated_files, self.max_hydrated_bytes):
            _require(type(bound) is int and bound >= 1)
        _enum(self.materialization_retention, ("until_session_durable_terminal",))
        _enum(
            self.mutation_policy, ("forbid_selected_roots", "reconcile_selected_writes")
        )
        _require(type(self.sources) is tuple and len(self.sources) <= 256)
        _require(type(self.write_rules) is tuple and len(self.write_rules) <= 256)
        for source in self.sources:
            _require(type(source) is ContextSourceCoverage)
            source.validate()
        for rule in self.write_rules:
            _require(type(rule) is ContextWriteCoverage)
            rule.validate()
        for identifier in (
            self.writeback_terminal_action_id,
            self.writeback_artifact_schema_id,
        ):
            if identifier is not None:
                _text(identifier, 512, 1)


@dataclass(frozen=True, slots=True)
class ProposedContextWrite:
    """A path/classification to inspect, never content or an execution grant."""

    stage_id: str
    path: str
    disposition: str

    def validate(self) -> None:
        _text(self.stage_id, 512, 1)
        _text(self.path, 4096, 1)
        _enum(self.disposition, ("direct_write", "protected_proposal"))


@dataclass(frozen=True, slots=True)
class SetupResponse:
    selection: SetupSelection
    checks: tuple[SetupCheck, ...]
    blockers: tuple[SetupBlocker, ...]
    next_action: str | None
    schema_version: Literal[1] = 1
    status: Literal["blocked", "ready"] = "blocked"
    proposed_actions: tuple[SetupAction, ...] = ()
    trust_disclosure: TrustDisclosure | None = None
    trust_acceptance: TrustAcceptance | None = None
    context_coverage: tuple[StageContextCoverage, ...] = ()

    @property
    def selection_digest(self) -> str:
        return digest(self.selection.to_wire())

    def to_wire(self) -> dict[str, object]:
        self.validate()
        value: dict[str, object] = json.loads(canonical(asdict(self)))
        value["selection_digest"] = self.selection_digest
        return value

    def validate(self) -> None:
        _require(type(self.selection) is SetupSelection)
        self.selection.validate()
        _require(type(self.schema_version) is int and self.schema_version == 1)
        _enum(self.status, ("blocked", "ready"))
        if self.next_action is not None:
            _text(self.next_action, 256)
        _require(type(self.checks) is tuple and len(self.checks) <= 256)
        _require(type(self.blockers) is tuple and len(self.blockers) <= 256)
        _require(
            type(self.proposed_actions) is tuple and len(self.proposed_actions) <= 64
        )
        _require(
            type(self.context_coverage) is tuple and len(self.context_coverage) <= 256
        )
        for coverage in self.context_coverage:
            _require(type(coverage) is StageContextCoverage)
            coverage.validate()
        if self.trust_disclosure is not None:
            _require(type(self.trust_disclosure) is TrustDisclosure)
            self.trust_disclosure.validate()
            _require(self.trust_disclosure.selection_digest == self.selection_digest)
        if self.trust_acceptance is not None:
            _require(type(self.trust_acceptance) is TrustAcceptance)
            self.trust_acceptance.validate()
            _require(self.trust_disclosure is not None)
            assert self.trust_disclosure is not None
            _require(self.trust_acceptance.selection_digest == self.selection_digest)
            _require(
                self.trust_acceptance.disclosure_digest
                == self.trust_disclosure.disclosure_digest
            )
        for action in self.proposed_actions:
            _require(type(action) is SetupAction)
            action.validate()
            _require(self.trust_acceptance is not None)
            assert self.trust_acceptance is not None
            _require(action.selection_digest == self.selection_digest)
            _require(
                action.disclosure_digest == self.trust_acceptance.disclosure_digest
            )
            _require(action.consent_receipt_id == self.trust_acceptance.receipt_id)
        for check in self.checks:
            _require(type(check) is SetupCheck)
            _text(check.id, 512, 1)
            _text(check.owner, 512, 1)
            _hash(check.affected_identity)
            _hash(check.evidence_reference)
            _enum(check.disposition, ("pass", "block", "deferred"))
            _enum(
                check.category,
                (
                    "component",
                    "command",
                    "package",
                    "runtime",
                    "runner",
                    "provider",
                    "context",
                    "management",
                    "trust",
                ),
            )
            _require(
                type(check.required) is bool and type(check.gates_next_action) is bool
            )
            _text(check.observed_at, 4096, 1)
            _require(
                type(check.producer_binding) is str and len(check.producer_binding) >= 1
            )
        for blocker in self.blockers:
            _require(type(blocker) is SetupBlocker)
            for value in (blocker.code, blocker.owner, blocker.affected_identity):
                _text(value, 512, 1)
            _text(blocker.explanation, 4096, 1)
            _text(blocker.safe_next_action, 4096, 1)
            _require(
                type(blocker.retry_meaningful) is bool
                and type(blocker.progress_valid) is bool
            )
        if self.status == "blocked" and not self.blockers:
            raise SetupRefusal("setup_authority_unavailable")
        if self.status == "ready":
            selected = self.selection
            if (
                selected.action_kind != "demo"
                or self.blockers
                or self.next_action != "millrace demo"
                or self.trust_acceptance is None
                or self.trust_disclosure is None
                or not self.trust_acceptance.accepted
                or any(
                    (c.required or c.gates_next_action) and c.disposition != "pass"
                    for c in self.checks
                )
            ):
                raise SetupRefusal("setup_readiness_authority_missing")
            if any(
                value is None
                for value in (
                    selected.workspace,
                    selected.package_id,
                    selected.package_version,
                    selected.source_digest,
                    selected.import_record_digest,
                    selected.manifest_digest,
                    selected.plan_fingerprint,
                    selected.local_config_digest,
                    selected.runtime_identity,
                    selected.demo_command_identity,
                )
            ):
                raise SetupRefusal("setup_readiness_identity_missing")
            pins = {p.distribution: p for p in selected.component_pins}
            if len(pins) != len(selected.component_pins) or any(
                p.artifact_sha256 is None or p.contract_version is None
                for p in selected.component_pins
            ):
                raise SetupRefusal("setup_component_identity_missing")
            if any(
                name not in pins
                or pins[name].artifact_sha256 is None
                or pins[name].contract_version is None
                for name in ("millrace-ai", "millrace-plus")
            ):
                raise SetupRefusal("setup_component_identity_missing")
            command = selected.demo_command_identity
            assert command is not None
            if (
                command.version != pins["millrace-ai"].version
                or command.artifact_sha256 != pins["millrace-ai"].artifact_sha256
            ):
                raise SetupRefusal("setup_command_identity_mismatch")
        if self.selection.action_kind == "demo":
            _require(
                not self.context_coverage and self.selection.context_digest is None
            )
        elif self.selection.context_digest is not None:
            _require(
                self.selection.context_digest
                == digest([asdict(c) for c in self.context_coverage])
            )
        else:
            _require(not self.context_coverage)
        _require(
            len({c.stage_id for c in self.context_coverage})
            == len(self.context_coverage)
        )
        required = REQUIRED_CHECKS[self.selection.action_kind]
        by_id = {check.id: check for check in self.checks}
        if len(by_id) != len(self.checks) or not set(required) <= set(by_id):
            raise SetupRefusal("missing_or_duplicate_setup_check")
        for check in self.checks:
            if (
                check.owner != "Core"
                or check.producer_binding != f"Core.{check.id}.v1"
                or check.affected_identity != self.selection_digest
                or check.evidence_reference != check_evidence(self.selection, check)
            ):
                raise SetupRefusal("setup_check_identity_mismatch")
            if check.id in required and (
                check.category != required[check.id]
                or not check.required
                or not check.gates_next_action
            ):
                raise SetupRefusal("setup_required_check_mismatch")
        if any(
            b.owner != "Core" or b.affected_identity != self.selection_digest
            for b in self.blockers
        ):
            raise SetupRefusal("setup_blocker_identity_mismatch")


def check_evidence(selection: SetupSelection, check: SetupCheck) -> str:
    return digest(
        {
            "selection": selection.to_wire(),
            "check_id": check.id,
            "owner": check.owner,
            "category": check.category,
            "required": check.required,
            "gates_next_action": check.gates_next_action,
            "disposition": check.disposition,
            "observed_at": check.observed_at,
        }
    )


OPERATIONS = (
    "acquire_install_evidence",
    "initialize_workspace",
    "import_package",
    "compile_plan",
    "create_project_note",
    "configure_managed_session",
    "disclose_trust",
    "accept_trust",
)


def closed_record(value: object, cls: type[Any]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {f.name for f in fields(cls)}:
        raise SetupRefusal("setup_wire_unrepresentable")
    return dict(value)


def selection_from_wire(value: object) -> SetupSelection:
    data = dict(closed_record(value, SetupSelection))
    pins = data["component_pins"]
    _require(type(pins) is list)
    data["component_pins"] = tuple(
        ComponentPin(**closed_record(p, ComponentPin)) for p in pins
    )
    if data["runtime_identity"] is not None:
        data["runtime_identity"] = RuntimeIdentity(
            **closed_record(data["runtime_identity"], RuntimeIdentity)
        )
    if data["demo_command_identity"] is not None:
        data["demo_command_identity"] = DemoCommandIdentity(
            **closed_record(data["demo_command_identity"], DemoCommandIdentity)
        )
    result = SetupSelection(**data)
    result.validate()
    return result


@dataclass(frozen=True, slots=True)
class SetupIntent:
    operation: str
    target: str
    content: str | None
    idempotency_key: str

    def validate(self) -> None:
        _enum(self.operation, OPERATIONS)
        _text(self.target, 4096, 1)
        _text(self.idempotency_key, 512, 1)
        if self.operation == "create_project_note":
            _text(self.content, 16384, 1)
            assert isinstance(self.content, str)
            if not self.content.strip() or len(self.content.encode("utf-8")) > 16384:
                raise SetupRefusal("setup_note_content_invalid")
        else:
            _require(self.content is None)

    @classmethod
    def from_wire(cls, value: object) -> SetupIntent:
        result = cls(**closed_record(value, cls))
        result.validate()
        return result


@dataclass(frozen=True, slots=True)
class SetupConsentRequest:
    intent: SetupIntent
    selection_digest: str
    disclosure_digest: str
    idempotency_key: str
    accepted: bool

    def validate(self) -> None:
        _require(type(self.intent) is SetupIntent)
        self.intent.validate()
        _hash(self.selection_digest)
        _hash(self.disclosure_digest)
        _text(self.idempotency_key, 512, 1)
        _require(type(self.accepted) is bool)

    @classmethod
    def from_wire(cls, value: object) -> SetupConsentRequest:
        data = dict(closed_record(value, cls))
        data["intent"] = SetupIntent.from_wire(data["intent"])
        result = cls(**data)
        result.validate()
        return result


@dataclass(frozen=True, slots=True)
class DisclosureRoot:
    path: str
    access: str


@dataclass(frozen=True, slots=True)
class TrustDisclosure:
    id: str
    owner: str
    selection_digest: str
    disclosure_digest: str
    roots: tuple[DisclosureRoot, ...]
    runner_binding: str
    credential_sources: tuple[str, ...]
    network_boundary: str
    host_mutations: tuple[str, ...]

    def validate(self) -> None:
        _text(self.id, 512, 1)
        _require(self.owner == "Core")
        _hash(self.selection_digest)
        _hash(self.disclosure_digest)
        _require(type(self.roots) is tuple and len(self.roots) <= 256)
        for root in self.roots:
            _require(type(root) is DisclosureRoot)
            _text(root.path, 4096, 1)
            _enum(root.access, ("read", "write", "protected_proposal"))
        _text(self.runner_binding, 512, 1)
        _text(self.network_boundary, 4096, 1)
        for values, bound in (
            (self.credential_sources, 512),
            (self.host_mutations, 4096),
        ):
            _require(type(values) is tuple and len(values) <= 32)
            for value in values:
                _text(value, bound, 1)
        body = asdict(self)
        body.pop("disclosure_digest")
        _require(digest(body) == self.disclosure_digest)

    def to_wire(self) -> dict[str, Any]:
        self.validate()
        value: dict[str, Any] = json.loads(canonical(asdict(self)))
        return value

    @classmethod
    def from_wire(cls, value: object) -> TrustDisclosure:
        values = closed_record(value, cls)
        _require(type(values["roots"]) is list)
        values["roots"] = tuple(
            DisclosureRoot(**closed_record(root, DisclosureRoot))
            for root in values["roots"]
        )
        for name in ("credential_sources", "host_mutations"):
            _require(type(values[name]) is list)
            values[name] = tuple(values[name])
        result = cls(**values)
        result.validate()
        return result


@dataclass(frozen=True, slots=True)
class TrustAcceptance:
    receipt_id: str
    owner: str
    actor_id: str
    selection_digest: str
    disclosure_digest: str
    accepted: bool
    idempotency_key: str
    observed_at: str

    def validate(self) -> None:
        for value in (self.receipt_id, self.owner, self.actor_id, self.idempotency_key):
            _text(value, 512, 1)
        _require(self.owner == "Core" and self.accepted is True)
        _hash(self.selection_digest)
        _hash(self.disclosure_digest)
        _text(self.observed_at, 4096, 1)

    def to_wire(self) -> dict[str, Any]:
        self.validate()
        value: dict[str, Any] = json.loads(canonical(asdict(self)))
        return value

    @classmethod
    def from_wire(cls, value: object) -> TrustAcceptance:
        result = cls(**closed_record(value, cls))
        result.validate()
        return result


@dataclass(frozen=True, slots=True)
class SetupAction:
    id: str
    owner: str
    operation: str
    selection_digest: str
    target: str
    requires_consent: bool
    idempotency_key: str
    disclosure_digest: str
    consent_receipt_id: str
    expected_mapping_generation: int

    def validate(self) -> None:
        for value in (
            self.id,
            self.owner,
            self.idempotency_key,
            self.consent_receipt_id,
        ):
            _text(value, 512, 1)
        _require(self.owner == "Core" and self.requires_consent is True)
        _enum(self.operation, OPERATIONS)
        _text(self.target, 4096, 1)
        _hash(self.selection_digest)
        _hash(self.disclosure_digest)
        _require(
            type(self.expected_mapping_generation) is int
            and self.expected_mapping_generation == 0
        )

    def to_wire(self) -> dict[str, Any]:
        self.validate()
        value: dict[str, Any] = json.loads(canonical(asdict(self)))
        return value

    @classmethod
    def from_wire(cls, value: object) -> SetupAction:
        result = cls(**closed_record(value, cls))
        result.validate()
        return result


@dataclass(frozen=True, slots=True)
class SetupActionResult:
    schema_version: int
    receipt_id: str
    owner: str
    actor_id: str
    action_id: str
    operation: str
    idempotency_key: str
    disclosure_digest: str
    request_selection_digest: str
    resulting_selection_digest: str
    resulting_selection: SetupSelection
    consent_receipt_id: str
    outcome: str
    invalidated_checks: tuple[str, ...]
    retry_meaningful: bool
    progress_valid: bool
    mapping_transition: None = None

    def validate(self) -> None:
        _require(type(self.schema_version) is int and self.schema_version == 1)
        for value in (
            self.receipt_id,
            self.owner,
            self.actor_id,
            self.action_id,
            self.idempotency_key,
            self.consent_receipt_id,
        ):
            _text(value, 512, 1)
        _require(self.owner == "Core")
        _enum(self.operation, OPERATIONS)
        for value in (
            self.disclosure_digest,
            self.request_selection_digest,
            self.resulting_selection_digest,
        ):
            _hash(value)
        _require(type(self.resulting_selection) is SetupSelection)
        self.resulting_selection.validate()
        _require(
            digest(self.resulting_selection.to_wire())
            == self.resulting_selection_digest
        )
        _enum(self.outcome, ("applied", "no_effect", "blocked", "unknown"))
        _require(
            type(self.invalidated_checks) is tuple
            and len(self.invalidated_checks) <= 256
        )
        for value in self.invalidated_checks:
            _text(value, 512, 1)
        _require(
            type(self.retry_meaningful) is bool and type(self.progress_valid) is bool
        )
        _require(self.mapping_transition is None)
        changed = self.request_selection_digest != self.resulting_selection_digest
        _require(bool(self.invalidated_checks) == changed)
        if changed:
            _require(self.outcome in ("applied", "blocked", "unknown"))
        if changed or self.outcome in ("blocked", "unknown"):
            _require(self.progress_valid is False)
        if self.outcome == "unknown":
            _require(self.retry_meaningful is False)

    def to_wire(self) -> dict[str, Any]:
        self.validate()
        value: dict[str, Any] = json.loads(canonical(asdict(self)))
        return value

    @classmethod
    def from_wire(cls, value: object) -> SetupActionResult:
        data = dict(closed_record(value, cls))
        data["resulting_selection"] = selection_from_wire(data["resulting_selection"])
        _require(type(data["invalidated_checks"]) is list)
        data["invalidated_checks"] = tuple(data["invalidated_checks"])
        result = cls(**data)
        result.validate()
        return result


@dataclass(frozen=True, slots=True)
class SetupTrustResult:
    """Bootstrap envelope containing three unchanged v1 owner records."""

    trust_acceptance: TrustAcceptance
    action: SetupAction
    action_result: SetupActionResult

    def to_wire(self) -> dict[str, Any]:
        consent = self.trust_acceptance
        action = self.action
        result = self.action_result
        consent.validate()
        action.validate()
        result.validate()
        _require(action.operation == result.operation == "accept_trust")
        _require(action.owner == result.owner == consent.owner)
        _require(
            action.consent_receipt_id == result.consent_receipt_id == consent.receipt_id
        )
        _require(
            action.selection_digest
            == result.request_selection_digest
            == consent.selection_digest
        )
        _require(
            action.disclosure_digest
            == result.disclosure_digest
            == consent.disclosure_digest
        )
        _require(
            action.id == result.action_id
            and action.idempotency_key == result.idempotency_key
        )
        return {
            "trust_acceptance": consent.to_wire(),
            "action": action.to_wire(),
            "action_result": result.to_wire(),
        }
