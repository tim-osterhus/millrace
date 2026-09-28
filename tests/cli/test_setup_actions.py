from __future__ import annotations

import pytest

from cli.test_setup_preflight import recipe
from millrace.adapters.cli import setup_actions
from millrace.adapters.cli.setup_actions import SetupActionService
from millrace.contracts.setup import (
    SetupConsentRequest,
    SetupIntent,
    SetupRefusal,
    SetupRequest,
)


def note_fixture(tmp_path):
    workspace, request = recipe(tmp_path)
    return workspace, request, tmp_path / "journal"


def consent(service, intent, key="consent-key"):
    response = service.disclose(intent)
    assert response.proposed_actions == () and response.trust_acceptance is None
    disclosure = response.trust_disclosure
    assert disclosure is not None
    request = SetupConsentRequest(
        intent, response.selection_digest, disclosure.disclosure_digest, key, True
    )
    bundle = service.accept(request)
    assert bundle is not None
    accepted = bundle.trust_acceptance
    proposed = service.propose(accepted.receipt_id)
    assert proposed.status == "blocked"
    return request, accepted, proposed.proposed_actions[0]


def test_note_consent_action_result_and_exact_replay(tmp_path):
    workspace, request, journal = note_fixture(tmp_path)
    service = SetupActionService(request, journal_root=journal)
    intent = SetupIntent(
        "create_project_note",
        "docs/project.md",
        "Task and acceptance criteria written by the user.\n",
        "note-key",
    )
    consent_request, accepted, action = consent(service, intent)
    result = service.execute(action.to_wire())
    assert result.outcome == "applied"
    assert (workspace / "docs/project.md").read_text() == intent.content
    assert len({action.id, accepted.receipt_id, result.receipt_id}) == 3
    assert result.action_id == action.id
    assert result.consent_receipt_id == action.consent_receipt_id == accepted.receipt_id
    assert result.resulting_selection_digest != result.request_selection_digest
    assert result.invalidated_checks and not result.progress_valid
    fresh = SetupActionService(request, journal_root=journal)
    assert fresh.lookup(result.receipt_id) == result
    assert fresh.execute(action.to_wire()) == result
    assert fresh.accept(consent_request).trust_acceptance == accepted
    assert fresh.validate_result(action.to_wire(), result.to_wire()) == result


def test_workspace_initialization_uses_public_api(tmp_path):
    workspace = tmp_path / "workspace"
    request = SetupRequest(action_kind="useful_recipe", workspace=str(workspace))
    service = SetupActionService(request, journal_root=tmp_path / "journal")
    _, _, action = consent(
        service, SetupIntent("initialize_workspace", str(workspace), None, "init-key")
    )
    result = service.execute(action.to_wire())
    assert result.outcome == "applied"
    assert result.resulting_selection.runtime_identity.disposition == "initialized"
    assert result.resulting_selection.runtime_identity.store_schema == 11
    assert (workspace / ".millrace/runtime.sqlite3").is_file()
    assert (
        SetupActionService(request, journal_root=tmp_path / "journal").execute(
            action.to_wire()
        )
        == result
    )


def test_declined_consent_and_disclosure_have_no_effects(tmp_path):
    _, request, journal = note_fixture(tmp_path)
    service = SetupActionService(request, journal_root=journal)
    response = service.disclose(
        SetupIntent("create_project_note", "docs/project.md", "User task", "key")
    )
    assert not journal.exists()
    consent_request = SetupConsentRequest(
        SetupIntent("create_project_note", "docs/project.md", "User task", "key"),
        response.selection_digest,
        response.trust_disclosure.disclosure_digest,
        "no",
        False,
    )
    assert service.accept(consent_request) is None
    assert not journal.exists()
    service.validate_response(response.to_wire(), intent=consent_request.intent)


def test_interruption_after_note_effect_is_unknown_and_never_repeated(
    tmp_path, monkeypatch
):
    workspace, request, journal = note_fixture(tmp_path)
    service = SetupActionService(request, journal_root=journal)
    _, _, action = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "key"),
    )
    original = setup_actions._perform

    def interrupt(*args):
        original(*args)
        raise KeyboardInterrupt

    monkeypatch.setattr(setup_actions, "_perform", interrupt)
    with pytest.raises(KeyboardInterrupt):
        service.execute(action.to_wire())
    assert (workspace / "docs/project.md").read_text() == "User task"

    def forbidden(*args):
        raise AssertionError("unknown effect repeated")

    monkeypatch.setattr(setup_actions, "_perform", forbidden)
    fresh = SetupActionService(request, journal_root=journal)
    result = fresh.execute(action.to_wire())
    assert result.outcome == "unknown"
    assert result.resulting_selection_digest != result.request_selection_digest
    assert (
        result.invalidated_checks
        and not result.retry_meaningful
        and not result.progress_valid
    )
    assert fresh.execute(action.to_wire()) == result
    assert fresh.lookup(result.receipt_id) == result


def test_post_intent_selection_drift_is_proven_blocked(tmp_path, monkeypatch):
    workspace, request, journal = note_fixture(tmp_path)
    service = SetupActionService(request, journal_root=journal)
    _, _, action = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "key"),
    )
    original = service._fresh
    calls = 0

    def drift(record):
        nonlocal calls
        calls += 1
        if calls == 2:
            (workspace / "docs").mkdir()
            (workspace / "docs/external.md").write_text("Independent external update")
        return original(record)

    monkeypatch.setattr(service, "_fresh", drift)

    def forbidden(*args):
        raise AssertionError("effect attempted after known drift")

    monkeypatch.setattr(setup_actions, "_perform", forbidden)
    result = service.execute(action.to_wire())
    assert result.outcome == "blocked"
    assert result.resulting_selection_digest != result.request_selection_digest
    assert result.invalidated_checks and not result.progress_valid
    assert not (workspace / "docs/project.md").exists()
    assert (
        SetupActionService(request, journal_root=journal).lookup(result.receipt_id)
        == result
    )


@pytest.mark.parametrize(
    "operation",
    [
        "import_package",
        "compile_plan",
        "configure_managed_session",
        "disclose_trust",
        "accept_trust",
    ],
)
def test_unsupported_effects_never_create_journal(tmp_path, operation):
    _, request, journal = note_fixture(tmp_path)
    with pytest.raises(SetupRefusal, match="setup_operation_unsupported_"):
        SetupActionService(request, journal_root=journal).disclose(
            SetupIntent(operation, "target", None, "key")
        )
    assert not journal.exists()


def test_otherwise_valid_alternate_consent_cannot_replace_admitted_consent(tmp_path):
    _, request, journal = note_fixture(tmp_path)
    service = SetupActionService(request, journal_root=journal)
    _, first, action = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "first"),
        "consent-first",
    )
    _, second, _ = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "second"),
        "consent-second",
    )
    assert first.selection_digest == second.selection_digest
    assert first.disclosure_digest == second.disclosure_digest
    assert first.receipt_id != second.receipt_id
    forged = action.to_wire()
    forged["consent_receipt_id"] = second.receipt_id
    with pytest.raises(SetupRefusal, match="setup_action_authority_mismatch"):
        service.execute(forged)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "other"),
        ("owner", "Other"),
        ("operation", "initialize_workspace"),
        ("target", "docs/other.md"),
        ("idempotency_key", "other"),
        ("disclosure_digest", "sha256:" + "0" * 64),
        ("selection_digest", "sha256:" + "0" * 64),
        ("consent_receipt_id", "other"),
        ("expected_mapping_generation", 1),
    ],
)
def test_action_field_substitution_refuses_before_effect(tmp_path, field, value):
    workspace, request, journal = note_fixture(tmp_path)
    service = SetupActionService(request, journal_root=journal)
    _, _, action = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "key"),
    )
    forged = action.to_wire()
    forged[field] = value
    before = (journal / "state.json").read_bytes()
    with pytest.raises(SetupRefusal):
        service.execute(forged)
    assert (journal / "state.json").read_bytes() == before
    assert not (workspace / "docs").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("receipt_id", "other"),
        ("owner", "Other"),
        ("actor_id", "other"),
        ("action_id", "other"),
        ("operation", "initialize_workspace"),
        ("idempotency_key", "other"),
        ("disclosure_digest", "sha256:" + "0" * 64),
        ("request_selection_digest", "sha256:" + "0" * 64),
        ("consent_receipt_id", "other"),
        ("outcome", "unknown"),
        ("invalidated_checks", []),
        ("progress_valid", True),
    ],
)
def test_result_field_substitution_refuses_owner_lookup(tmp_path, field, value):
    _, request, journal = note_fixture(tmp_path)
    service = SetupActionService(request, journal_root=journal)
    _, _, action = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "key"),
    )
    result = service.execute(action.to_wire())
    forged = result.to_wire()
    forged[field] = value
    with pytest.raises(SetupRefusal):
        service.validate_result(action.to_wire(), forged)


@pytest.mark.parametrize(
    "target", ["../escape", "/absolute", ".millrace/private", "other/note"]
)
def test_note_must_remediate_selected_safe_root(tmp_path, target):
    workspace, request, journal = note_fixture(tmp_path)
    service = SetupActionService(request, journal_root=journal)
    with pytest.raises(SetupRefusal):
        service.disclose(SetupIntent("create_project_note", target, "User task", "key"))
    assert not journal.exists() and not (workspace / "docs").exists()


@pytest.mark.parametrize("content", ["", "   ", "x" * 16385])
def test_note_content_is_user_authored_and_bounded(tmp_path, content):
    _, request, journal = note_fixture(tmp_path)
    with pytest.raises(SetupRefusal):
        SetupActionService(request, journal_root=journal).disclose(
            SetupIntent("create_project_note", "docs/project.md", content, "key")
        )
    assert not journal.exists()


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_note_target_created_after_consent_is_never_overwritten(tmp_path, kind):
    workspace, request, journal = note_fixture(tmp_path)
    service = SetupActionService(request, journal_root=journal)
    _, _, action = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "key"),
    )
    (workspace / "docs").mkdir()
    target = workspace / "docs/project.md"
    outside = tmp_path / "retained"
    outside.write_text("Retained existing content")
    if kind == "file":
        target.write_text("Retained existing content")
    else:
        target.symlink_to(outside)
    before = (journal / "state.json").read_bytes()
    with pytest.raises(SetupRefusal):
        service.execute(action.to_wire())
    assert target.read_text() == outside.read_text() == "Retained existing content"
    assert (journal / "state.json").read_bytes() == before


def test_actions_have_no_network_provider_or_process_effects(tmp_path, monkeypatch):
    import socket
    import subprocess

    from millrace.adapters.cli import setup_inventory

    _, request, journal = note_fixture(tmp_path)
    calls = []

    def forbidden(*args, **kwargs):
        calls.append("forbidden")
        raise AssertionError("forbidden runtime effect")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    # No credential or provider session is needed to observe installed components.
    assert setup_inventory is not None
    service = SetupActionService(request, journal_root=journal)
    _, _, action = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "key"),
    )
    result = service.execute(action.to_wire())
    assert result.outcome == "applied" and calls == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("consent_receipt_id", None),
        ("consent_receipt_id", ""),
        ("requires_consent", False),
        ("expected_mapping_generation", True),
        ("extra", "unexpected"),
    ],
)
def test_closed_action_wire_rejects_missing_consent_and_invalid_types(
    tmp_path, field, value
):
    from millrace.contracts.setup import SetupAction

    _, request, journal = note_fixture(tmp_path)
    service = SetupActionService(request, journal_root=journal)
    _, _, action = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "key"),
    )
    wire = action.to_wire()
    wire[field] = value
    with pytest.raises(SetupRefusal):
        SetupAction.from_wire(wire)


def test_self_consistent_forged_result_selection_is_not_owner_authority(tmp_path):
    from millrace.contracts.setup import digest

    _, request, journal = note_fixture(tmp_path)
    service = SetupActionService(request, journal_root=journal)
    _, _, action = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "key"),
    )
    result = service.execute(action.to_wire())
    forged = result.to_wire()
    forged["resulting_selection"]["local_config_digest"] = "sha256:" + "0" * 64
    forged["resulting_selection_digest"] = digest(forged["resulting_selection"])
    with pytest.raises(SetupRefusal):
        service.validate_result(action.to_wire(), forged)


def test_unknown_cannot_be_promoted_to_applied_by_caller(tmp_path, monkeypatch):
    _, request, journal = note_fixture(tmp_path)
    service = SetupActionService(request, journal_root=journal)
    _, _, action = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "key"),
    )
    original = setup_actions._perform

    def interrupted(*args):
        original(*args)
        raise KeyboardInterrupt

    monkeypatch.setattr(setup_actions, "_perform", interrupted)
    with pytest.raises(KeyboardInterrupt):
        service.execute(action.to_wire())
    result = service.execute(action.to_wire())
    assert result.outcome == "unknown" and result.invalidated_checks
    forged = result.to_wire()
    forged["outcome"] = "applied"
    with pytest.raises(SetupRefusal):
        service.validate_result(action.to_wire(), forged)
    forged = result.to_wire()
    forged["invalidated_checks"] = []
    with pytest.raises(SetupRefusal):
        service.validate_result(action.to_wire(), forged)


def test_result_alternate_otherwise_valid_consent_is_refused(tmp_path):
    _, request, journal = note_fixture(tmp_path)
    service = SetupActionService(request, journal_root=journal)
    _, first, action = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "first"),
        "first-consent",
    )
    _, alternate, _ = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "second"),
        "second-consent",
    )
    assert first.selection_digest == alternate.selection_digest
    assert first.disclosure_digest == alternate.disclosure_digest
    assert first.receipt_id != alternate.receipt_id
    result = service.execute(action.to_wire())
    forged = result.to_wire()
    forged["consent_receipt_id"] = alternate.receipt_id
    with pytest.raises(SetupRefusal):
        service.validate_result(action.to_wire(), forged)


def test_wrong_result_digest_refuses_unchanged_resulting_selection(tmp_path):
    _, request, journal = note_fixture(tmp_path)
    service = SetupActionService(request, journal_root=journal)
    _, _, action = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "key"),
    )
    result = service.execute(action.to_wire())
    forged = result.to_wire()
    forged["resulting_selection_digest"] = "sha256:" + "0" * 64
    with pytest.raises(SetupRefusal):
        service.validate_result(action.to_wire(), forged)


def test_missing_result_consent_fails_closed_wire(tmp_path):
    from millrace.contracts.setup import SetupActionResult

    _, request, journal = note_fixture(tmp_path)
    service = SetupActionService(request, journal_root=journal)
    _, _, action = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "key"),
    )
    wire = service.execute(action.to_wire()).to_wire()
    del wire["consent_receipt_id"]
    with pytest.raises(SetupRefusal):
        SetupActionResult.from_wire(wire)


def test_different_current_configuration_cannot_recover_pending_action(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    workspace, request, journal = note_fixture(tmp_path)
    service = SetupActionService(request, journal_root=journal)
    _, _, action = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "key"),
    )

    def interrupted(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(setup_actions, "_perform", interrupted)
    with pytest.raises(KeyboardInterrupt):
        service.execute(action.to_wire())
    before = (journal / "state.json").read_bytes()
    other = SetupActionService(
        replace(request, db=str(workspace / ".millrace/other.sqlite3")),
        journal_root=journal,
    )
    with pytest.raises(SetupRefusal, match="setup_request_selection_mismatch"):
        other.execute(action.to_wire())
    assert (journal / "state.json").read_bytes() == before
    assert not (workspace / "docs").exists()
    result = service.execute(action.to_wire())
    assert result.outcome == "unknown" and not result.retry_meaningful


def evidence_action_fixture(tmp_path, monkeypatch):
    from millrace.adapters.cli import setup_install_evidence

    plan = {
        "source": str(tmp_path / "wheelhouse"),
        "destination": str(tmp_path / "evidence"),
        "wheels": [
            {
                "distribution": "millrace-ai",
                "version": "candidate",
                "sha256": "sha256:" + "1" * 64,
            }
        ],
    }
    monkeypatch.setattr(
        setup_install_evidence,
        "inspect_evidence_source",
        lambda source: (
            plan if source == plan["source"] else pytest.fail("unbound source")
        ),
    )
    monkeypatch.setattr(
        setup_install_evidence,
        "acquire_evidence",
        lambda source, **kwargs: (tmp_path / "effect").write_text(source),
    )
    return (
        SetupActionService(SetupRequest(), journal_root=tmp_path / "journal"),
        SetupIntent("acquire_install_evidence", plan["source"], None, "evidence-key"),
        plan,
    )


def test_evidence_action_disclosure_and_decline_are_pure(tmp_path, monkeypatch):
    service, intent, plan = evidence_action_fixture(tmp_path, monkeypatch)
    response = service.disclose(intent)
    disclosure = response.trust_disclosure
    assert plan["source"] in str(disclosure.to_wire())
    assert plan["destination"] in str(disclosure.to_wire())
    declined = service.accept(
        SetupConsentRequest(
            intent,
            response.selection_digest,
            disclosure.disclosure_digest,
            "decline",
            False,
        )
    )
    assert declined is None and not list(tmp_path.iterdir())


def test_evidence_action_source_change_invalidates_consent(tmp_path, monkeypatch):
    service, intent, plan = evidence_action_fixture(tmp_path, monkeypatch)
    _, _, action = consent(service, intent)
    plan["wheels"][0]["sha256"] = "sha256:" + "2" * 64
    with pytest.raises(SetupRefusal, match="setup_disclosure_changed"):
        service.execute(action.to_wire())
    assert not (tmp_path / "effect").exists()


def test_evidence_action_interrupted_after_effect_never_repeats(tmp_path, monkeypatch):
    service, intent, _ = evidence_action_fixture(tmp_path, monkeypatch)
    _, _, action = consent(service, intent)
    perform = setup_actions._perform

    def interrupt(*args):
        perform(*args)
        raise KeyboardInterrupt

    monkeypatch.setattr(setup_actions, "_perform", interrupt)
    with pytest.raises(KeyboardInterrupt):
        service.execute(action.to_wire())
    assert (tmp_path / "effect").read_text() == intent.target
    monkeypatch.setattr(
        setup_actions, "_perform", lambda *a: pytest.fail("unknown effect repeated")
    )
    fresh = SetupActionService(SetupRequest(), journal_root=tmp_path / "journal")
    result = fresh.execute(action.to_wire())
    assert result.outcome == "unknown"
    assert fresh.lookup(result.receipt_id) == result
    assert fresh.execute(action.to_wire()) == result


def test_unknown_acquisition_cannot_be_bypassed_with_new_consent(tmp_path, monkeypatch):
    from dataclasses import replace

    service, intent, _ = evidence_action_fixture(tmp_path, monkeypatch)
    _, accepted, action = consent(service, intent)
    monkeypatch.setattr(
        setup_actions,
        "_perform",
        lambda *a: (_ for _ in ()).throw(OSError("uncertain write")),
    )
    result = service.execute(action.to_wire())
    assert result.outcome == "unknown"
    assert service.resume(accepted.receipt_id) == result
    with pytest.raises(SetupRefusal, match="unresolved_install_evidence"):
        consent(
            service,
            replace(intent, idempotency_key="different"),
            key="different-consent",
        )


def demo_trust_fixture(tmp_path, monkeypatch):
    """Controlled install observations; consent/journal/entry remain real.

    This source fixture is not installed-artifact qualification.
    """
    from millrace.adapters.cli import demo, setup
    from millrace.adapters.cli.demo_workspace import demo_root
    from millrace.adapters.cli.setup_inventory import (
        InstalledComponent,
        SelectionObservation,
    )
    from millrace.contracts.demo import VERSION
    from millrace.contracts.setup import (
        ComponentPin,
        DemoCommandIdentity,
        RuntimeIdentity,
        SetupSelection,
        digest,
    )

    monkeypatch.setenv("HOME", str(tmp_path))
    identity = {
        "core_artifact": "sha256:" + "1" * 64,
        "plus_artifact": "sha256:" + "2" * 64,
    }
    monkeypatch.setattr(demo, "installed_identity", lambda: dict(identity))

    def observe(request):
        root = str(demo_root())
        pins = tuple(
            ComponentPin(name, VERSION, identity[key], "test-contract")
            for name, key in (
                ("millrace-ai", "core_artifact"),
                ("millrace-plus", "plus_artifact"),
            )
        )
        selected = SetupSelection(
            "demo",
            pins,
            "millrace demo",
            workspace=root,
            package_id="official.demo",
            package_version=VERSION,
            source_digest="sha256:" + "3" * 64,
            import_record_digest="sha256:" + "4" * 64,
            manifest_digest="sha256:" + "5" * 64,
            plan_fingerprint="sha256:" + "6" * 64,
            local_config_digest=digest(
                {"profile": "owned-demo-v1", "root": root, "installation": identity}
            ),
            runtime_identity=RuntimeIdentity(
                "planned:owned-demo-v1", root, disposition="planned_isolated"
            ),
            demo_command_identity=DemoCommandIdentity(
                "millrace-ai", VERSION, identity["core_artifact"], "1"
            ),
        )
        return SelectionObservation(
            selected,
            "demo_observed",
            tuple(InstalledComponent(p, "artifact_correlated", None) for p in pins),
            "approved_demo",
            "qualified",
        )

    monkeypatch.setattr(setup, "observe_selection", observe)
    return SetupActionService(SetupRequest()), identity


def accept_demo_trust(service, key="demo"):
    intent = SetupIntent("disclose_trust", "millrace demo", None, key)
    response = service.disclose(intent)
    return service.accept(
        SetupConsentRequest(
            intent,
            response.selection_digest,
            response.trust_disclosure.disclosure_digest,
            key + "-consent",
            True,
        )
    ).trust_acceptance


def accepted_demo_action(service, key="demo"):
    accepted = accept_demo_trust(service, key)
    action = service.propose(accepted.receipt_id).proposed_actions[0]
    return accepted, action


def test_demo_trust_absent_then_atomic_acceptance_and_applied_handoff(
    tmp_path, monkeypatch
):
    service, _ = demo_trust_fixture(tmp_path, monkeypatch)
    assert service.resolve_demo_trust() is None
    assert not service.journal.root.exists()
    accepted = accept_demo_trust(service)
    fresh = SetupActionService(SetupRequest())
    assert fresh.resolve_demo_trust() == accepted
    assert fresh.resume(accepted.receipt_id).outcome == "applied"
    assert SetupActionService(SetupRequest()).resolve_demo_trust() == accepted


def test_demo_trust_selects_current_matching_receipt(tmp_path, monkeypatch):
    service, identity = demo_trust_fixture(tmp_path, monkeypatch)
    accept_demo_trust(service)
    identity["core_artifact"] = "sha256:" + "7" * 64
    assert SetupActionService(SetupRequest()).resolve_demo_trust() is None
    fresh = SetupActionService(SetupRequest())
    current = accept_demo_trust(fresh, "current")
    assert SetupActionService(SetupRequest()).resolve_demo_trust() == current


@pytest.mark.parametrize("state", ["current", "stale", "pending"])
def test_demo_trust_rejects_cross_consent_action_link(tmp_path, monkeypatch, state):
    service, identity = demo_trust_fixture(tmp_path, monkeypatch)
    accepted_a, _ = accepted_demo_action(service, "first")
    if state == "stale":
        identity["core_artifact"] = "sha256:" + "7" * 64
        service = SetupActionService(SetupRequest())
    _, action_b = accepted_demo_action(service, "second")

    with service.journal.locked(write=True) as journal:
        assert journal is not None
        record_a = journal.state["consents"][accepted_a.receipt_id]
        if state == "pending":
            journal.state["actions"][record_a["action_id"]]["status"] = "pending"
        record_a["action_id"] = action_b.id
        journal.commit()

    with pytest.raises(SetupRefusal, match="setup_action_consent_mismatch"):
        SetupActionService(SetupRequest()).resolve_demo_trust()


@pytest.mark.parametrize(
    "attack",
    ["receipt_id", "owner", "actor_id", "result", "action", "pending", "unknown"],
)
def test_demo_trust_rejects_corrupt_or_uncertain_authority(
    tmp_path, monkeypatch, attack
):
    service, _ = demo_trust_fixture(tmp_path, monkeypatch)
    accepted = accept_demo_trust(service)
    with service.journal.locked(write=True) as journal:
        record = journal.state["consents"][accepted.receipt_id]
        row = journal.state["actions"][record["action_id"]]
        if attack in {"receipt_id", "owner", "actor_id"}:
            record["receipt"][attack] = "substituted"
        elif attack == "result":
            journal.state["results"].clear()
        elif attack == "action":
            row["action"]["consent_receipt_id"] = "substituted"
        else:
            row["status"] = "pending"
        journal.commit()
    if attack == "unknown":
        assert service.resume(accepted.receipt_id).outcome == "unknown"
    with pytest.raises(SetupRefusal):
        SetupActionService(SetupRequest()).resolve_demo_trust()


def test_demo_trust_does_not_accept_other_operation(tmp_path, monkeypatch):
    service, _ = demo_trust_fixture(tmp_path, monkeypatch)
    from millrace.adapters.cli import setup_install_evidence

    monkeypatch.setattr(
        setup_install_evidence,
        "inspect_evidence_source",
        lambda source: {
            "source": source,
            "destination": str(tmp_path / "evidence"),
            "wheels": [],
        },
    )
    intent = SetupIntent(
        "acquire_install_evidence", str(tmp_path / "wheels"), None, "acquire"
    )
    response = service.disclose(intent)
    service.accept(
        SetupConsentRequest(
            intent,
            response.selection_digest,
            response.trust_disclosure.disclosure_digest,
            "acquire-consent",
            True,
        )
    )
    assert service.resolve_demo_trust() is None


def test_demo_trust_blocked_handoff_is_not_renewed(tmp_path, monkeypatch):
    service, _ = demo_trust_fixture(tmp_path, monkeypatch)
    accepted = accept_demo_trust(service)

    def refuse(*args):
        raise SetupRefusal("guard_refused")

    monkeypatch.setattr(setup_actions, "_pre_effect_guard", refuse)
    assert service.resume(accepted.receipt_id).outcome == "blocked"
    with pytest.raises(SetupRefusal, match="demo_trust_action_unresolved"):
        service.resolve_demo_trust()


def test_demo_trust_changed_root_requires_new_acceptance(tmp_path, monkeypatch):
    from millrace.adapters.cli import demo_workspace

    service, _ = demo_trust_fixture(tmp_path, monkeypatch)
    accept_demo_trust(service)
    monkeypatch.setattr(demo_workspace, "demo_root", lambda: tmp_path / "other-root")
    assert SetupActionService(SetupRequest()).resolve_demo_trust() is None


def test_demo_trust_request_types_are_exact(tmp_path, monkeypatch):
    service, _ = demo_trust_fixture(tmp_path, monkeypatch)
    accepted = accept_demo_trust(service)
    with service.journal.locked(write=True) as journal:
        journal.state["consents"][accepted.receipt_id]["setup_request"][
            "schema_version"
        ] = True
        journal.commit()
    with pytest.raises(SetupRefusal, match="setup_request_selection_mismatch"):
        service.resolve_demo_trust()
