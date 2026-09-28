"""Explicit local setup consent and effects, resolved through the private journal."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from millrace.adapters.cli.context import workspace_paths
from millrace.adapters.cli.context_checkout import _safe_relative_path
from millrace.adapters.cli.output import CliSuccess
from millrace.adapters.cli.setup import SetupService
from millrace.adapters.cli.setup_receipts import (
    LockedJournal,
    SetupJournal,
    secure_directory,
)
from millrace.contracts.setup import (
    DisclosureRoot,
    SetupAction,
    SetupActionResult,
    SetupConsentRequest,
    SetupIntent,
    SetupRefusal,
    SetupRequest,
    SetupResponse,
    SetupTrustResult,
    TrustAcceptance,
    TrustDisclosure,
    canonical,
    check_evidence,
    digest,
    selection_from_wire,
)

SUPPORTED_EFFECTS = (
    "initialize_workspace",
    "create_project_note",
    "disclose_trust",
    "acquire_install_evidence",
)
JOURNALED_OPERATIONS = (*SUPPORTED_EFFECTS, "accept_trust")


def _acceptance_key(key: str) -> str:
    return digest({"operation": "accept_trust", "consent_idempotency_key": key})


def _same(left: object, right: object, code: str) -> None:
    if canonical(left) != canonical(right):
        raise SetupRefusal(code)


def _actor() -> str:
    return f"operator:uid:{os.getuid()}"


def _observation(service: SetupService) -> str:
    return digest(asdict(service._observation))


def _safe_parent(path: Path) -> None:
    candidate = path.parent
    while True:
        try:
            fd = secure_directory(candidate)
            os.close(fd)
            return
        except FileNotFoundError:
            candidate = candidate.parent
        except OSError as exc:
            raise SetupRefusal("setup_unsafe_target") from exc


def _target_abs(request: SetupRequest, intent: SetupIntent) -> Path:
    workspace = workspace_paths(request).workspace_path
    if intent.operation == "initialize_workspace":
        if (
            intent.target != str(workspace)
            or request.db is not None
            or request.cas is not None
            or request.plan_fingerprint is not None
        ):
            raise SetupRefusal("setup_initialize_selection_invalid")
        return workspace
    try:
        relative = _safe_relative_path(intent.target, "note target")
    except ValueError as exc:
        raise SetupRefusal("setup_note_target_unsafe") from exc
    return workspace / relative


def _eligible(
    request: SetupRequest, intent: SetupIntent, service: SetupService
) -> Path:
    intent.validate()
    if intent.operation not in SUPPORTED_EFFECTS:
        raise SetupRefusal("setup_operation_unsupported_" + intent.operation)
    if intent.operation == "acquire_install_evidence":
        from millrace.adapters.cli.setup_install_evidence import inspect_evidence_source

        if request.action_kind != "demo":
            raise SetupRefusal("setup_install_evidence_demo_only")
        return Path(inspect_evidence_source(intent.target)["destination"])
    if intent.operation == "disclose_trust":
        if request.action_kind != "demo":
            raise SetupRefusal("setup_operation_unsupported_" + intent.operation)
        from millrace.adapters.cli.demo_workspace import demo_root

        if (
            intent.target != "millrace demo"
            or service._observation.plan_disposition != "demo_observed"
        ):
            raise SetupRefusal("demo_setup_unavailable")
        return demo_root()
    if request.action_kind != "useful_recipe":
        raise SetupRefusal("demo_setup_actions_unavailable")
    target = _target_abs(request, intent)
    _safe_parent(target)
    if intent.operation == "initialize_workspace":
        if service.inspect().selection.runtime_identity is not None:
            raise SetupRefusal("setup_workspace_already_initialized")
        return target
    observation = service._observation
    if observation.plan_disposition != "observed" or observation.context is None:
        raise SetupRefusal("setup_note_selection_unavailable")
    roots = {
        issue.source_ref
        for issue in observation.context.issues
        if issue.code in ("context_missing", "context_empty")
    }
    matches = [
        s
        for c in observation.context.coverage
        for s in c.sources
        if s.required
        and s.source_kind == "workspace_relative_root"
        and s.source_ref in roots
        and (
            intent.target == s.source_ref
            or intent.target.startswith(s.source_ref + "/")
        )
    ]
    if not matches:
        raise SetupRefusal("setup_note_remediation_not_selected")
    assert intent.content is not None
    if any(
        len(intent.content.encode("utf-8")) > source.max_bytes for source in matches
    ):
        raise SetupRefusal("setup_note_capture_bound_exceeded")
    return target


def _pre_effect_guard(request: SetupRequest, intent: SetupIntent) -> None:
    if intent.operation in {"disclose_trust", "acquire_install_evidence"}:
        _eligible(request, intent, SetupService(request))
        return
    target = _target_abs(request, intent)
    _safe_parent(target)
    if intent.operation == "initialize_workspace":
        try:
            fd = secure_directory(target)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise SetupRefusal("setup_unsafe_target") from exc
        try:
            try:
                os.stat(".millrace", dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            raise SetupRefusal("setup_partial_workspace")
        finally:
            os.close(fd)
    try:
        target.lstat()
    except FileNotFoundError:
        return
    raise SetupRefusal("setup_note_target_exists")


def _perform(
    request: SetupRequest,
    intent: SetupIntent,
    action: SetupAction,
    guard: Callable[[], object],
) -> None:
    if intent.operation == "acquire_install_evidence":
        from millrace.adapters.cli.setup_install_evidence import acquire_evidence

        acquire_evidence(intent.target, guard=lambda: guard())
        return
    if intent.operation == "disclose_trust":
        return  # The durable owner result is the handoff; no runtime is created.
    if intent.operation == "initialize_workspace":
        from millrace.adapters.cli.workspace import handle_workspace_command

        handle_workspace_command(
            SimpleNamespace(
                command="workspace.init",
                workspace=request.workspace,
                db=None,
                cas=None,
                input_id="setup:" + action.id,
            )
        )
        return
    target = _target_abs(request, intent)
    directory_fd = secure_directory(target.parent, create=True)
    try:
        fd = os.open(
            target.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory_fd,
        )
        try:
            assert intent.content is not None
            data = intent.content.encode("utf-8")
            offset = 0
            while offset < len(data):
                offset += os.write(fd, data[offset:])
            os.fsync(fd)
            os.fsync(directory_fd)
        finally:
            os.close(fd)
    finally:
        os.close(directory_fd)


class SetupActionService:
    def __init__(
        self, request: SetupRequest, *, journal_root: Path | None = None
    ) -> None:
        request.__post_init__()
        self.request = (
            replace(request, workspace=str(workspace_paths(request).workspace_path))
            if request.action_kind == "useful_recipe"
            else request
        )
        self.service = SetupService(self.request)
        self.journal = SetupJournal(journal_root)

    def resolve_demo_trust(self) -> TrustAcceptance | None:
        """Resolve current demo authority without creating or repairing state.

        Missing or stale consent may be renewed explicitly. Corrupt records or
        uncertain actions refuse, so a new prompt cannot bypass recovery.
        """
        if self.request != SetupRequest():
            raise SetupRefusal("demo_setup_actions_unavailable")
        self.service = SetupService(self.request)
        response = self.service.inspect()
        if any(b.code != "trust_acceptance_unavailable" for b in response.blockers):
            raise SetupRefusal("demo_setup_unavailable")
        selected: TrustAcceptance | None = None
        with self.journal.locked() as journal:
            if journal is None:
                return None
            from millrace.adapters.cli.setup_install_evidence import evidence_directory

            self._check_evidence_recovery(journal.state, str(evidence_directory()))
            for receipt_id in sorted(journal.state["consents"]):
                accepted = self._accepted(journal.state, receipt_id).trust_acceptance
                record = journal.state["consents"][receipt_id]
                action, row, admitted_record = self._admitted(
                    journal.state, record["action_id"]
                )
                if (
                    action.consent_receipt_id != receipt_id
                    or accepted.receipt_id != receipt_id
                ):
                    raise SetupRefusal("setup_action_consent_mismatch")
                record = admitted_record
                intent = SetupConsentRequest.from_wire(record["request"]).intent
                if intent.operation != "disclose_trust":
                    continue
                _same(
                    record["setup_request"],
                    asdict(self.request),
                    "setup_request_selection_mismatch",
                )
                if intent.target != "millrace demo" or intent.content is not None:
                    raise SetupRefusal("setup_action_consent_mismatch")
                if row["status"] == "pending":
                    raise SetupRefusal("demo_trust_action_unresolved")
                if row["status"] == "complete":
                    result = self._result(journal.state, row["result_receipt_id"])
                    if result.outcome != "applied":
                        raise SetupRefusal("demo_trust_action_unresolved")
                try:
                    self._fresh(record)
                except SetupRefusal as exc:
                    if str(exc) not in {
                        "setup_selection_changed",
                        "setup_disclosure_changed",
                    }:
                        raise
                    continue
                if selected is None:
                    selected = accepted
            self.service.validate_response(response.to_wire())
        return selected

    def disclose(self, intent: SetupIntent) -> SetupResponse:
        if type(intent) is not SetupIntent:
            raise SetupRefusal("invalid_setup_intent")
        target = _eligible(self.request, intent, self.service)
        response = self.service.inspect()
        selection_digest = response.selection_digest
        effect = {k: v for k, v in asdict(intent).items() if k != "idempotency_key"}
        for coverage in response.context_coverage:
            for source in coverage.sources:
                if source.source_kind == "workspace_relative_root":
                    root = (
                        workspace_paths(self.request).workspace_path / source.source_ref
                    )
                    if self.journal.root.is_relative_to(root) or root.is_relative_to(
                        self.journal.root
                    ):
                        raise SetupRefusal("setup_journal_overlaps_context")
        body: dict[str, Any] = {
            "id": "disclosure:"
            + digest(
                {
                    "selection": selection_digest,
                    "intent": effect,
                    "journal": str(self.journal.root),
                }
            )[7:],
            "owner": "Core",
            "selection_digest": selection_digest,
            "roots": (
                DisclosureRoot(
                    str(target)
                    if intent.operation == "disclose_trust"
                    else str(workspace_paths(self.request).workspace_path),
                    "read",
                ),
                DisclosureRoot(str(target), "write"),
                DisclosureRoot(str(self.journal.root), "write"),
            ),
            "runner_binding": "millrace.demo.synthetic"
            if intent.operation == "disclose_trust"
            else "none:headless-setup-actions",
            "credential_sources": (),
            "network_boundary": (
                "No network, provider, paid work, runner or process launch."
            ),
            "host_mutations": (
                "Create private local consent/action journal records.",
                "Explicit operation: " + intent.operation,
                "Exact effect digest: " + digest(effect),
                "The demo creates an isolated synthetic runtime; no keys, network "
                "or user repository effects. Keep interrupted state and receipts "
                "for recovery; remove the successful runtime unless --keep is used."
                if intent.operation == "disclose_trust"
                else "Create missing parents; never overwrite a project note."
                if intent.operation == "create_project_note"
                else "Initialize this workspace using the public Core workspace API.",
            ),
        }
        if intent.operation == "acquire_install_evidence":
            from millrace.adapters.cli.setup_install_evidence import (
                inspect_evidence_source,
            )

            plan = inspect_evidence_source(intent.target)
            body["roots"] = (
                *(
                    (DisclosureRoot(intent.target, "read"),)
                    if intent.target != "pypi"
                    else ()
                ),
                DisclosureRoot(str(target), "write"),
                DisclosureRoot(str(self.journal.root), "write"),
            )
            body["network_boundary"] = (
                (
                    "Download only the exact installed Core/Plus wheels from HTTPS "
                    "pypi.org and files.pythonhosted.org. "
                    "No proxy, credentials, models or paid work."
                )
                if intent.target == "pypi"
                else (
                    "No network. Read the two exact wheels "
                    "in the selected local wheelhouse."
                )
            )
            body["host_mutations"] = (
                "Retain verified complete Core/Plus wheels in "
                + str(target)
                + ". Never install packages or overwrite conflicting evidence.",
                "Create private consent/action journal records in "
                + str(self.journal.root)
                + ".",
                "Installed observation: " + _observation(self.service),
                "Exact source and destination: " + canonical(plan).decode("utf-8"),
            )
        disclosure = TrustDisclosure(
            **body,
            disclosure_digest=digest(
                {**body, "roots": [asdict(root) for root in body["roots"]]}
            ),
        )
        result = replace(response, trust_disclosure=disclosure)
        result.validate()
        return result

    def accept(self, request: SetupConsentRequest) -> SetupTrustResult | None:
        if type(request) is not SetupConsentRequest:
            raise SetupRefusal("invalid_setup_consent_request")
        request.validate()
        if not request.accepted:
            return None
        with self.journal.locked() as existing:
            if existing is not None:
                prior_id = existing.state["consent_keys"].get(request.idempotency_key)
                if prior_id is not None:
                    _same(
                        existing.state["consents"][prior_id]["request"],
                        asdict(request),
                        "setup_consent_replay_conflict",
                    )
                    _same(
                        existing.state["consents"][prior_id]["setup_request"],
                        asdict(self.request),
                        "setup_request_selection_mismatch",
                    )
                    return self._accepted(existing.state, prior_id)
        response = self.disclose(request.intent)
        disclosure = response.trust_disclosure
        assert disclosure is not None
        _same(
            request.selection_digest,
            response.selection_digest,
            "setup_consent_selection_mismatch",
        )
        _same(
            request.disclosure_digest,
            disclosure.disclosure_digest,
            "setup_consent_disclosure_mismatch",
        )
        self.service.validate_response(self.service.inspect().to_wire())
        with self.journal.locked(create=True) as journal:
            assert journal is not None
            state = journal.state
            prior = state["consent_keys"].get(request.idempotency_key)
            if prior is not None:
                record = state["consents"].get(prior)
                if record is None:
                    raise SetupRefusal("setup_journal_corrupt")
                _same(
                    record["request"], asdict(request), "setup_consent_replay_conflict"
                )
                _same(
                    record["setup_request"],
                    asdict(self.request),
                    "setup_request_selection_mismatch",
                )
                return self._accepted(state, prior)
            if request.intent.idempotency_key in state["action_keys"]:
                raise SetupRefusal("setup_action_replay_conflict")
            if self.request.action_kind == "demo":
                from millrace.adapters.cli.setup_install_evidence import (
                    evidence_directory,
                )

                destination = (
                    str(_eligible(self.request, request.intent, self.service))
                    if request.intent.operation == "acquire_install_evidence"
                    else str(evidence_directory())
                )
                self._check_evidence_recovery(state, destination)
            acceptance_key = _acceptance_key(request.idempotency_key)
            if (
                acceptance_key == request.intent.idempotency_key
                or acceptance_key in state["action_keys"]
            ):
                raise SetupRefusal("setup_action_replay_conflict")
            if len(state["actions"]) + 2 > 256:
                raise SetupRefusal("setup_journal_full")
            # The exclusive lock may have waited while selected bytes changed.
            # Exact committed replay above is historical; new acceptance is current.
            current = SetupActionService(self.request, journal_root=self.journal.root)
            _same(
                _observation(current.service),
                _observation(self.service),
                "setup_selection_changed",
            )
            fresh_response = current.disclose(request.intent)
            fresh_disclosure = fresh_response.trust_disclosure
            assert fresh_disclosure is not None
            _same(
                fresh_response.selection_digest,
                request.selection_digest,
                "setup_consent_selection_mismatch",
            )
            _same(
                fresh_disclosure.disclosure_digest,
                request.disclosure_digest,
                "setup_consent_disclosure_mismatch",
            )
            _same(
                fresh_disclosure.to_wire(),
                disclosure.to_wire(),
                "setup_disclosure_changed",
            )
            response, disclosure = fresh_response, fresh_disclosure
            receipt = TrustAcceptance(
                "consent:" + uuid4().hex,
                "Core",
                _actor(),
                response.selection_digest,
                disclosure.disclosure_digest,
                True,
                request.idempotency_key,
                datetime.now(UTC).isoformat(),
            )
            action = SetupAction(
                "action:" + uuid4().hex,
                "Core",
                request.intent.operation,
                response.selection_digest,
                request.intent.target,
                True,
                request.intent.idempotency_key,
                disclosure.disclosure_digest,
                receipt.receipt_id,
                0,
            )
            state["consents"][receipt.receipt_id] = {
                "setup_request": asdict(self.request),
                "receipt": receipt.to_wire(),
                "request": asdict(request),
                "selection": response.selection.to_wire(),
                "disclosure": disclosure.to_wire(),
                "observation": _observation(self.service),
                "check_ids": [c.id for c in response.checks],
                "action_id": action.id,
            }
            state["consent_keys"][request.idempotency_key] = receipt.receipt_id
            state["actions"][action.id] = {
                "action": action.to_wire(),
                "status": "admitted",
                "result_receipt_id": "result:" + uuid4().hex,
                "evidence": None,
            }
            state["action_keys"][action.idempotency_key] = action.id
            acceptance_action = SetupAction(
                "action:" + uuid4().hex,
                "Core",
                "accept_trust",
                response.selection_digest,
                str(self.journal.root),
                True,
                acceptance_key,
                disclosure.disclosure_digest,
                receipt.receipt_id,
                0,
            )
            acceptance_result = SetupActionResult(
                1,
                "result:" + uuid4().hex,
                "Core",
                _actor(),
                acceptance_action.id,
                "accept_trust",
                acceptance_key,
                disclosure.disclosure_digest,
                response.selection_digest,
                response.selection_digest,
                response.selection,
                receipt.receipt_id,
                "applied",
                (),
                False,
                False,
            )
            state["consents"][receipt.receipt_id]["acceptance_action_id"] = (
                acceptance_action.id
            )
            state["actions"][acceptance_action.id] = {
                "action": acceptance_action.to_wire(),
                "status": "complete",
                "result_receipt_id": acceptance_result.receipt_id,
                "evidence": "atomic_consent_acceptance_committed",
            }
            state["action_keys"][acceptance_key] = acceptance_action.id
            state["results"][acceptance_result.receipt_id] = acceptance_result.to_wire()
            # Consent, its result and the intended effect admission are one commit.
            # Before commit none exists; after commit exact replay recovers all three.
            journal.commit()
            return self._accepted(state, receipt.receipt_id)

    def _accepted(self, state: dict[str, Any], receipt_id: str) -> SetupTrustResult:
        consent = self._consent(state, receipt_id)
        record = state["consents"][receipt_id]
        action, row, _ = self._admitted(state, record["acceptance_action_id"])
        result = self._result(state, row["result_receipt_id"])
        bundle = SetupTrustResult(consent, action, result)
        bundle.to_wire()
        return bundle

    @staticmethod
    def _consent(state: dict[str, Any], receipt_id: str) -> TrustAcceptance:
        record = state["consents"].get(receipt_id)
        if type(record) is not dict:
            raise SetupRefusal("setup_consent_not_found")
        receipt = TrustAcceptance.from_wire(record["receipt"])
        if (
            receipt.receipt_id != receipt_id
            or receipt.actor_id != _actor()
            or state["consent_keys"].get(receipt.idempotency_key) != receipt_id
        ):
            raise SetupRefusal("setup_consent_identity_mismatch")
        selected = selection_from_wire(record["selection"])
        disclosure = TrustDisclosure.from_wire(record["disclosure"])
        if (
            digest(selected.to_wire()) != receipt.selection_digest
            or disclosure.selection_digest != receipt.selection_digest
            or disclosure.disclosure_digest != receipt.disclosure_digest
        ):
            raise SetupRefusal("setup_consent_binding_mismatch")
        stored_request = SetupConsentRequest.from_wire(record["request"])
        if (
            stored_request.selection_digest != receipt.selection_digest
            or stored_request.disclosure_digest != receipt.disclosure_digest
            or stored_request.idempotency_key != receipt.idempotency_key
            or not stored_request.accepted
        ):
            raise SetupRefusal("setup_consent_binding_mismatch")
        return receipt

    def _admitted(
        self, state: dict[str, Any], action_id: str
    ) -> tuple[SetupAction, dict[str, Any], dict[str, Any]]:
        row = state["actions"].get(action_id)
        if type(row) is not dict:
            raise SetupRefusal("setup_action_not_found")
        action = SetupAction.from_wire(row["action"])
        if (
            action.id != action_id
            or action.operation not in JOURNALED_OPERATIONS
            or state["action_keys"].get(action.idempotency_key) != action.id
        ):
            raise SetupRefusal("setup_action_identity_mismatch")
        consent = self._consent(state, action.consent_receipt_id)
        record = state["consents"][consent.receipt_id]
        intent = SetupConsentRequest.from_wire(record["request"]).intent
        acceptance = action.operation == "accept_trust"
        expected_id = (
            record["acceptance_action_id"] if acceptance else record["action_id"]
        )
        expected_target = str(self.journal.root) if acceptance else intent.target
        expected_key = (
            _acceptance_key(consent.idempotency_key)
            if acceptance
            else intent.idempotency_key
        )
        if (
            expected_id != action.id
            or action.selection_digest != consent.selection_digest
            or action.disclosure_digest != consent.disclosure_digest
            or action.operation != ("accept_trust" if acceptance else intent.operation)
            or action.target != expected_target
            or action.idempotency_key != expected_key
        ):
            raise SetupRefusal("setup_action_consent_mismatch")
        if acceptance and row["status"] != "complete":
            raise SetupRefusal("setup_acceptance_incomplete")
        if row["status"] not in ("admitted", "pending", "complete"):
            raise SetupRefusal("setup_journal_corrupt")
        return action, row, record

    def propose(self, consent_receipt_id: str) -> SetupResponse:
        with self.journal.locked() as journal:
            if journal is None:
                raise SetupRefusal("setup_consent_not_found")
            consent = self._consent(journal.state, consent_receipt_id)
            record = journal.state["consents"][consent.receipt_id]
            action, row, record = self._admitted(journal.state, record["action_id"])
            _same(
                record["setup_request"],
                asdict(self.request),
                "setup_request_selection_mismatch",
            )
            if row["status"] != "admitted":
                raise SetupRefusal("setup_action_already_started")
            response = self.disclose(
                SetupConsentRequest.from_wire(record["request"]).intent
            )
            self._fresh(record)
            checks = tuple(
                replace(c, disposition="pass") if c.id == "trust.acceptance" else c
                for c in response.checks
            )
            checks = tuple(
                replace(c, evidence_reference=check_evidence(response.selection, c))
                for c in checks
            )
            result = replace(
                response,
                trust_acceptance=consent,
                proposed_actions=(action,),
                checks=checks,
                blockers=tuple(
                    b
                    for b in response.blockers
                    if b.code != "trust_acceptance_unavailable"
                ),
            )
            if self.request.action_kind == "demo" and not result.blockers:
                result = replace(result, status="ready", next_action="millrace demo")
            result.validate()
            return result

    def _fresh(self, record: dict[str, Any]) -> SetupService:
        current = SetupService(self.request)
        _same(_observation(current), record["observation"], "setup_selection_changed")
        intent = SetupConsentRequest.from_wire(record["request"]).intent
        owner = SetupActionService(self.request, journal_root=self.journal.root)
        disclosure = owner.disclose(intent).trust_disclosure
        assert disclosure is not None
        _same(disclosure.to_wire(), record["disclosure"], "setup_disclosure_changed")
        _same(
            _observation(owner.service),
            record["observation"],
            "setup_selection_changed",
        )
        return owner.service

    def execute(self, supplied: object) -> SetupActionResult:
        action = SetupAction.from_wire(supplied)
        if action.operation not in JOURNALED_OPERATIONS:
            raise SetupRefusal("setup_operation_unsupported_" + action.operation)
        with self.journal.locked(write=True) as journal:
            if journal is None:
                raise SetupRefusal("setup_action_not_found")
            admitted, row, record = self._admitted(journal.state, action.id)
            _same(
                action.to_wire(), admitted.to_wire(), "setup_action_authority_mismatch"
            )
            _same(
                record["setup_request"],
                asdict(self.request),
                "setup_request_selection_mismatch",
            )
            if row["status"] == "complete":
                return self._result(journal.state, row["result_receipt_id"])
            if row["status"] == "pending":
                return self._finish(
                    journal,
                    admitted,
                    row,
                    record,
                    "unknown",
                    "interrupted_intent_completion_unestablished",
                )
            self._fresh(record)
            intent = SetupConsentRequest.from_wire(record["request"]).intent
            try:
                _pre_effect_guard(self.request, intent)
            except SetupRefusal:
                return self._finish(
                    journal,
                    admitted,
                    row,
                    record,
                    "blocked",
                    "pre_effect_guard_refused_no_effect_attempted",
                )
            row["status"] = "pending"
            row["evidence"] = "durable_intent_before_effect"
            journal.commit()
            try:
                self._fresh(record)
                _pre_effect_guard(self.request, intent)
            except SetupRefusal:
                return self._finish(
                    journal,
                    admitted,
                    row,
                    record,
                    "blocked",
                    "post_intent_recheck_refused_no_effect_attempted",
                )
            try:
                _perform(self.request, intent, admitted, lambda: self._fresh(record))
            except Exception:
                return self._finish(
                    journal,
                    admitted,
                    row,
                    record,
                    "unknown",
                    "effect_attempted_completion_unestablished",
                )
            return self._finish(
                journal,
                admitted,
                row,
                record,
                "applied",
                "public_effect_returned_and_result_durably_recorded",
            )

    def _finish(
        self,
        journal: LockedJournal,
        action: SetupAction,
        row: dict[str, Any],
        record: dict[str, Any],
        outcome: str,
        evidence: str,
    ) -> SetupActionResult:
        current = SetupService(self.request).inspect().selection
        resulting_digest = digest(current.to_wire())
        changed = resulting_digest != action.selection_digest
        result = SetupActionResult(
            1,
            row["result_receipt_id"],
            "Core",
            _actor(),
            action.id,
            action.operation,
            action.idempotency_key,
            action.disclosure_digest,
            action.selection_digest,
            resulting_digest,
            current,
            action.consent_receipt_id,
            outcome,
            tuple(record["check_ids"]) if changed else (),
            False,
            False,
        )
        result.validate()
        row["status"] = "complete"
        row["evidence"] = evidence
        journal.state["results"][result.receipt_id] = result.to_wire()
        journal.commit()
        return result

    def _result(self, state: dict[str, Any], receipt_id: str) -> SetupActionResult:
        value = state["results"].get(receipt_id)
        if value is None:
            raise SetupRefusal("setup_result_not_found")
        result = SetupActionResult.from_wire(value)
        action, row, record = self._admitted(state, result.action_id)
        if (
            result.receipt_id != receipt_id
            or row["result_receipt_id"] != receipt_id
            or row["status"] != "complete"
            or result.actor_id != _actor()
        ):
            raise SetupRefusal("setup_result_identity_mismatch")
        for name in (
            "owner",
            "operation",
            "idempotency_key",
            "disclosure_digest",
            "consent_receipt_id",
        ):
            _same(
                getattr(result, name),
                getattr(action, name),
                "setup_result_binding_mismatch",
            )
        _same(
            result.request_selection_digest,
            action.selection_digest,
            "setup_result_selection_mismatch",
        )
        _same(
            list(result.invalidated_checks),
            record["check_ids"]
            if result.resulting_selection_digest != action.selection_digest
            else [],
            "setup_result_invalidation_mismatch",
        )
        expected = {
            "applied": (
                "atomic_consent_acceptance_committed"
                if action.operation == "accept_trust"
                else "public_effect_returned_and_result_durably_recorded",
            ),
            "unknown": (
                "interrupted_intent_completion_unestablished",
                "effect_attempted_completion_unestablished",
            ),
            "blocked": (
                "pre_effect_guard_refused_no_effect_attempted",
                "post_intent_recheck_refused_no_effect_attempted",
            ),
        }
        if action.operation == "accept_trust":
            _same(
                result.resulting_selection.to_wire(),
                record["selection"],
                "setup_acceptance_result_selection_mismatch",
            )
            if (
                result.outcome != "applied"
                or result.retry_meaningful
                or result.progress_valid
            ):
                raise SetupRefusal("setup_acceptance_result_outcome_mismatch")
        if row["evidence"] not in expected.get(result.outcome, ()):
            raise SetupRefusal("setup_result_outcome_mismatch")
        return result

    def _check_evidence_recovery(self, state: dict[str, Any], destination: str) -> None:
        for row in state["actions"].values():
            action = SetupAction.from_wire(row["action"])
            if action.operation != "acquire_install_evidence":
                continue
            record = state["consents"][action.consent_receipt_id]
            if not any(
                root["path"] == destination and root["access"] == "write"
                for root in record["disclosure"]["roots"]
            ):
                continue
            if row["status"] == "pending" or (
                row["status"] == "complete"
                and self._result(state, row["result_receipt_id"]).outcome == "unknown"
            ):
                raise SetupRefusal(
                    "unresolved_install_evidence; recover with "
                    "millrace setup --resume-receipt " + action.consent_receipt_id
                )

    def check_evidence_recovery(self) -> None:
        """Do not let an interactive restart hide an uncertain wheel retention."""
        from millrace.adapters.cli.setup_install_evidence import evidence_directory

        destination = str(evidence_directory())
        with self.journal.locked() as journal:
            if journal is not None:
                self._check_evidence_recovery(journal.state, destination)

    def resume(self, consent_receipt_id: str) -> SetupActionResult:
        """Resolve a consent-owned action and recover it without widening authority."""
        with self.journal.locked() as journal:
            if journal is None:
                raise SetupRefusal("setup_consent_not_found")
            self._consent(journal.state, consent_receipt_id)
            record = journal.state["consents"][consent_receipt_id]
            action, _, _ = self._admitted(journal.state, record["action_id"])
        return self.execute(action.to_wire())

    def lookup(self, receipt_id: str) -> TrustAcceptance | SetupActionResult:
        with self.journal.locked() as journal:
            if journal is None:
                raise SetupRefusal("setup_receipt_not_found")
            if receipt_id in journal.state["consents"]:
                return self._consent(journal.state, receipt_id)
            return self._result(journal.state, receipt_id)

    def validate_response(
        self,
        value: object,
        *,
        intent: SetupIntent | None = None,
        consent_receipt_id: str | None = None,
    ) -> SetupResponse:
        if (intent is None) == (consent_receipt_id is None):
            raise SetupRefusal("setup_response_validation_request_invalid")
        expected = (
            self.disclose(intent)
            if intent is not None
            else self.propose(str(consent_receipt_id))
        )
        _same(value, expected.to_wire(), "setup_response_authority_mismatch")
        self.service.validate_response(self.service.inspect().to_wire())
        return expected

    def validate_result(self, action: object, result: object) -> SetupActionResult:
        parsed_action = SetupAction.from_wire(action)
        parsed_result = SetupActionResult.from_wire(result)
        with self.journal.locked() as journal:
            if journal is None:
                raise SetupRefusal("setup_receipt_not_found")
            admitted, row, _ = self._admitted(journal.state, parsed_action.id)
            _same(
                parsed_action.to_wire(),
                admitted.to_wire(),
                "setup_action_authority_mismatch",
            )
            if parsed_result.receipt_id != row["result_receipt_id"]:
                raise SetupRefusal("setup_result_identity_mismatch")
            actual = self._result(journal.state, parsed_result.receipt_id)
            _same(
                parsed_result.to_wire(),
                actual.to_wire(),
                "setup_result_authority_mismatch",
            )
            return actual


def handle_setup_action(namespace: object, request: SetupRequest) -> CliSuccess:
    import json

    from millrace.adapters.cli.output import success_result

    operation = getattr(namespace, "setup_operation", "inspect")
    raw = getattr(namespace, "request_json", None)
    if type(raw) is not str or len(raw) > 65536:
        raise SetupRefusal("setup_request_json_required")
    try:
        value = json.loads(raw)
        canonical(value)
    except (ValueError, TypeError, UnicodeError) as exc:
        raise SetupRefusal("setup_request_json_invalid") from exc
    service = SetupActionService(request)
    if operation == "disclose_trust":
        result = service.disclose(SetupIntent.from_wire(value))
        disclosure = result.trust_disclosure
        assert disclosure is not None
        return success_result(
            command="setup.disclose_trust",
            code="setup_disclosure",
            message="Explicit setup disclosure. Selection: "
            + result.selection_digest
            + ". Disclosure: "
            + disclosure.disclosure_digest
            + ". "
            + " ".join(disclosure.host_mutations)
            + " No action is authorized before consent.",
            data=result.to_wire(),
        )
    if operation == "accept_trust":
        consent = service.accept(SetupConsentRequest.from_wire(value))
        return success_result(
            command="setup.accept_trust",
            code="setup_trust_accepted"
            if consent is not None
            else "setup_consent_declined",
            message="Trust acceptance and its result retained; workspace unchanged."
            if consent is not None
            else "Consent declined; no journal or setup state changed.",
            data=consent.to_wire() if consent is not None else {"accepted": False},
        )
    if operation in ("propose", "lookup", "resume"):
        if (
            type(value) is not dict
            or set(value) != {"receipt_id"}
            or type(value["receipt_id"]) is not str
            or not 1 <= len(value["receipt_id"]) <= 512
        ):
            raise SetupRefusal("setup_receipt_request_invalid")
        found = (
            service.propose(value["receipt_id"])
            if operation == "propose"
            else service.resume(value["receipt_id"])
            if operation == "resume"
            else service.lookup(value["receipt_id"])
        )
        return success_result(
            command="setup." + operation,
            code="setup_" + operation,
            message="Core setup journal observation; execution remains unavailable.",
            data=found.to_wire(),
        )
    if operation == "apply":
        applied = service.execute(value)
        return success_result(
            command="setup.apply",
            code="setup_action_" + applied.outcome,
            message="Setup action outcome: "
            + applied.outcome
            + ". Receipt: "
            + applied.receipt_id
            + ". Unknown effects must not be repeated automatically.",
            data=applied.to_wire(),
        )
    raise SetupRefusal("setup_operation_unsupported")
