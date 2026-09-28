from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy

import pytest

from cli.test_setup_actions import consent, note_fixture
from millrace.adapters.cli import setup_actions
from millrace.adapters.cli.setup_actions import SetupActionService
from millrace.adapters.cli.setup_receipts import SetupJournal
from millrace.contracts.setup import SetupIntent, SetupRefusal, SetupRequest


def cli(request, home, operation, payload):
    env = dict(os.environ, HOME=str(home), PYTHONDONTWRITEBYTECODE="1")
    args = [
        sys.executable,
        "-m",
        "millrace.adapters.cli.main",
        "--json",
        "--workspace",
        request.workspace,
        "setup",
        "--action",
        "useful_recipe",
        "--setup-operation",
        operation,
        "--request-json",
        json.dumps(payload),
    ]
    if request.plan_fingerprint is not None:
        args.extend(["--plan-fingerprint", request.plan_fingerprint])
    completed = subprocess.run(
        args, env=env, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stderr == ""
    return json.loads(completed.stdout)["data"]


def test_actual_cli_bootstrap_and_fresh_process_receipt_lookup(tmp_path):
    workspace, request, _ = note_fixture(tmp_path)
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    intent = {
        "operation": "create_project_note",
        "target": "docs/project.md",
        "content": "User-authored task criteria",
        "idempotency_key": "note-key",
    }
    disclosure = cli(request, home, "disclose_trust", intent)
    assert not (home / ".millrace").exists()
    assert disclosure["proposed_actions"] == []
    bundle = cli(
        request,
        home,
        "accept_trust",
        {
            "intent": intent,
            "selection_digest": disclosure["selection_digest"],
            "disclosure_digest": disclosure["trust_disclosure"]["disclosure_digest"],
            "idempotency_key": "consent-key",
            "accepted": True,
        },
    )
    receipt = bundle["trust_acceptance"]
    acceptance_result = bundle["action_result"]
    assert acceptance_result["operation"] == "accept_trust"
    assert (
        cli(request, home, "lookup", {"receipt_id": acceptance_result["receipt_id"]})
        == acceptance_result
    )
    proposed = cli(request, home, "propose", {"receipt_id": receipt["receipt_id"]})
    action = proposed["proposed_actions"][0]
    result = cli(request, home, "apply", action)
    assert result["outcome"] == "applied"
    assert cli(request, home, "lookup", {"receipt_id": result["receipt_id"]}) == result
    assert cli(request, home, "apply", action) == result
    assert (
        cli(request, home, "lookup", {"receipt_id": receipt["receipt_id"]}) == receipt
    )
    assert (workspace / "docs/project.md").read_text() == intent["content"]


def test_changed_selection_interruption_recovers_unknown_in_fresh_process(
    tmp_path, monkeypatch
):
    workspace, request, _ = note_fixture(tmp_path)
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    journal = home / ".millrace/setup-journal"
    service = SetupActionService(request, journal_root=journal)
    _, _, action = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "note"),
    )
    original = setup_actions._perform

    def interrupted(*args):
        original(*args)
        raise KeyboardInterrupt

    monkeypatch.setattr(setup_actions, "_perform", interrupted)
    with pytest.raises(KeyboardInterrupt):
        service.execute(action.to_wire())
    before = (workspace / "docs/project.md").stat().st_mtime_ns
    result = cli(request, home, "apply", action.to_wire())
    assert result["outcome"] == "unknown"
    assert result["invalidated_checks"] and result["progress_valid"] is False
    assert result["retry_meaningful"] is False
    assert cli(request, home, "lookup", {"receipt_id": result["receipt_id"]}) == result
    assert cli(request, home, "apply", action.to_wire()) == result
    assert (workspace / "docs/project.md").stat().st_mtime_ns == before


def test_proven_pre_effect_drift_result_survives_fresh_lookup(tmp_path, monkeypatch):
    workspace, request, _ = note_fixture(tmp_path)
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    service = SetupActionService(request, journal_root=home / ".millrace/setup-journal")
    _, _, action = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "note"),
    )
    original = service._fresh
    calls = 0

    def drift(record):
        nonlocal calls
        calls += 1
        if calls == 2:
            (workspace / "docs").mkdir()
            (workspace / "docs/external.md").write_text("External content")
        return original(record)

    monkeypatch.setattr(service, "_fresh", drift)

    def forbidden(*args):
        raise AssertionError("effect attempted")

    monkeypatch.setattr(setup_actions, "_perform", forbidden)
    result = service.execute(action.to_wire()).to_wire()
    assert result["outcome"] == "blocked" and result["invalidated_checks"]
    assert not (workspace / "docs/project.md").exists()
    assert cli(request, home, "lookup", {"receipt_id": result["receipt_id"]}) == result


@pytest.mark.parametrize(
    "field,value",
    [
        ("receipt_id", "consent:wrong"),
        ("owner", "Other"),
        ("actor_id", "other"),
        ("selection_digest", "sha256:" + "0" * 64),
        ("disclosure_digest", "sha256:" + "0" * 64),
        ("accepted", False),
    ],
)
def test_durable_consent_corruption_refuses_exact_registry_lookup(
    tmp_path, field, value
):
    workspace, request, journal_root = note_fixture(tmp_path)
    service = SetupActionService(request, journal_root=journal_root)
    _, accepted, action = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "note"),
    )
    with SetupJournal(journal_root).locked(write=True) as journal:
        journal.state["consents"][accepted.receipt_id]["receipt"][field] = value
        journal.commit()
    with pytest.raises(SetupRefusal):
        service.execute(action.to_wire())
    assert not (workspace / "docs").exists()


@pytest.mark.parametrize(
    "kind",
    [
        "root_symlink",
        "root_world_write",
        "state_symlink",
        "state_mode",
        "state_hardlink",
        "state_invalid_json",
    ],
)
def test_journal_unsafe_roots_files_and_corruption_refuse(tmp_path, kind):
    _, request, root = note_fixture(tmp_path)
    service = SetupActionService(request, journal_root=root)
    _, accepted, _ = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "note"),
    )
    if kind == "root_symlink":
        other = root.with_name("actual-journal")
        root.rename(other)
        root.symlink_to(other, target_is_directory=True)
    elif kind == "root_world_write":
        root.chmod(0o777)
    elif kind == "state_symlink":
        state = root / "state.json"
        other = root / "actual.json"
        state.rename(other)
        state.symlink_to(other)
    elif kind == "state_mode":
        (root / "state.json").chmod(0o644)
    elif kind == "state_hardlink":
        os.link(root / "state.json", root / "linked.json")
    else:
        (root / "state.json").write_text("not json")
    with pytest.raises(SetupRefusal):
        service.lookup(accepted.receipt_id)


def test_missing_lookup_and_forged_action_do_not_initialize_journal(tmp_path):
    _, request, root = note_fixture(tmp_path)
    service = SetupActionService(request, journal_root=root)
    with pytest.raises(SetupRefusal):
        service.lookup("result:absent")
    assert not root.exists()
    from millrace.contracts.setup import SetupAction

    action = SetupAction(
        "action:absent",
        "Core",
        "create_project_note",
        "sha256:" + "0" * 64,
        "docs/project.md",
        True,
        "key",
        "sha256:" + "0" * 64,
        "consent:absent",
        0,
    )
    with pytest.raises(SetupRefusal):
        service.execute(action.to_wire())
    assert not root.exists()


def test_exact_replay_conflict_preserves_receipt_and_note(tmp_path):
    workspace, request, root = note_fixture(tmp_path)
    service = SetupActionService(request, journal_root=root)
    consent_request, _, action = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "note"),
    )
    result = service.execute(action.to_wire())
    changed = deepcopy(action.to_wire())
    changed["target"] = "docs/other.md"
    with pytest.raises(SetupRefusal, match="authority_mismatch"):
        service.execute(changed)
    from dataclasses import replace

    with pytest.raises(SetupRefusal, match="replay_conflict"):
        service.accept(
            replace(
                consent_request,
                intent=replace(consent_request.intent, content="Different task"),
            )
        )
    assert service.lookup(result.receipt_id) == result
    assert (workspace / "docs/project.md").read_text() == "User task"


def test_partial_initialization_is_not_repeated(tmp_path, monkeypatch):
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    workspace = tmp_path / "workspace"
    request = SetupRequest(action_kind="useful_recipe", workspace=str(workspace))
    root = tmp_path / "journal"
    service = SetupActionService(request, journal_root=root)
    _, _, action = consent(
        service, SetupIntent("initialize_workspace", str(workspace), None, "init")
    )

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(SQLiteRuntimeStore, "persist_runtime_state", interrupted)
    with pytest.raises(KeyboardInterrupt):
        service.execute(action.to_wire())
    assert (workspace / ".millrace/runtime.sqlite3").exists()
    fresh = SetupActionService(request, journal_root=root)
    result = fresh.execute(action.to_wire())
    assert result.outcome == "unknown" and result.invalidated_checks
    assert fresh.execute(action.to_wire()) == result
    with pytest.raises(SetupRefusal, match="already_initialized"):
        fresh.disclose(
            SetupIntent("initialize_workspace", str(workspace), None, "different")
        )


def test_existing_partial_runtime_is_known_pre_effect_block(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / ".millrace").mkdir(parents=True)
    marker = workspace / ".millrace/operator-marker"
    marker.write_text("Preserve partial state")
    request = SetupRequest(action_kind="useful_recipe", workspace=str(workspace))
    service = SetupActionService(request, journal_root=tmp_path / "journal")
    _, _, action = consent(
        service, SetupIntent("initialize_workspace", str(workspace), None, "init")
    )
    result = service.execute(action.to_wire())
    assert result.outcome == "blocked"
    assert marker.read_text() == "Preserve partial state"
    assert not (workspace / ".millrace/runtime.sqlite3").exists()


def test_readonly_lookup_and_repeat_decode_preserve_journal_bytes(tmp_path):
    _, request, journal = note_fixture(tmp_path)
    service = SetupActionService(request, journal_root=journal)
    _, accepted, action = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "key"),
    )
    result = service.execute(action.to_wire())
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in journal.iterdir()}
    for _ in range(2):
        assert service.lookup(accepted.receipt_id) == accepted
        assert service.lookup(result.receipt_id) == result
    assert {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in journal.iterdir()
    } == before


def test_stored_disclosure_corruption_refuses_lookup(tmp_path):
    _, request, root = note_fixture(tmp_path)
    service = SetupActionService(request, journal_root=root)
    _, accepted, _ = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "key"),
    )
    with SetupJournal(root).locked(write=True) as journal:
        journal.state["consents"][accepted.receipt_id]["disclosure"]["owner"] = "Other"
        journal.commit()
    with pytest.raises(SetupRefusal):
        service.lookup(accepted.receipt_id)


@pytest.mark.parametrize("phase", ["before_commit", "after_commit"])
def test_acceptance_atomic_interruption_and_fresh_process_exact_replay(
    tmp_path, monkeypatch, phase
):
    from dataclasses import asdict

    from millrace.adapters.cli.setup_receipts import LockedJournal
    from millrace.contracts.setup import SetupConsentRequest

    workspace, request, _ = note_fixture(tmp_path)
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    root = home / ".millrace/setup-journal"
    service = SetupActionService(request, journal_root=root)
    intent = SetupIntent("create_project_note", "docs/project.md", "User task", "note")
    disclosed = service.disclose(intent)
    payload = SetupConsentRequest(
        intent,
        disclosed.selection_digest,
        disclosed.trust_disclosure.disclosure_digest,
        "trust",
        True,
    )
    original = LockedJournal.commit

    def interrupted(journal):
        if phase == "after_commit":
            original(journal)
        raise KeyboardInterrupt

    monkeypatch.setattr(LockedJournal, "commit", interrupted)
    with pytest.raises(KeyboardInterrupt):
        service.accept(payload)
    with SetupJournal(root).locked() as journal:
        if phase == "before_commit":
            assert journal.state["consents"] == {} and journal.state["results"] == {}
        else:
            assert len(journal.state["consents"]) == len(journal.state["results"]) == 1
            assert len(journal.state["actions"]) == 2
    bundle = cli(request, home, "accept_trust", asdict(payload))
    assert cli(request, home, "accept_trust", asdict(payload)) == bundle
    accepted, action, result = (
        bundle[k] for k in ("trust_acceptance", "action", "action_result")
    )
    assert result["operation"] == action["operation"] == "accept_trust"
    assert result["outcome"] == "applied"
    assert (
        result["consent_receipt_id"]
        == action["consent_receipt_id"]
        == accepted["receipt_id"]
    )
    assert len({accepted["receipt_id"], action["id"], result["receipt_id"]}) == 3
    assert (
        result["request_selection_digest"]
        == result["resulting_selection_digest"]
        == accepted["selection_digest"]
    )
    assert result["invalidated_checks"] == [] and not result["progress_valid"]
    assert cli(request, home, "lookup", {"receipt_id": result["receipt_id"]}) == result
    assert cli(request, home, "apply", action) == result
    assert not (workspace / "docs").exists()
    assert service.validate_result(action, result).to_wire() == result


@pytest.mark.parametrize(
    "field,value",
    [
        ("receipt_id", "wrong"),
        ("owner", "Other"),
        ("action_id", "wrong"),
        ("operation", "initialize_workspace"),
        ("idempotency_key", "wrong"),
        ("disclosure_digest", "sha256:" + "0" * 64),
        ("consent_receipt_id", "wrong"),
        ("outcome", "unknown"),
    ],
)
def test_acceptance_result_identity_is_independently_resolved(tmp_path, field, value):
    _, request, root = note_fixture(tmp_path)
    service = SetupActionService(request, journal_root=root)
    payload, _, _ = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "note"),
    )
    bundle = service.accept(payload)
    forged = bundle.action_result.to_wire()
    forged[field] = value
    with pytest.raises(SetupRefusal):
        service.validate_result(bundle.action.to_wire(), forged)


def test_new_acceptance_refuses_actual_content_drift_after_write_lock(
    tmp_path, monkeypatch
):
    from contextlib import contextmanager
    from dataclasses import asdict

    from millrace.adapters.cli.setup import SetupService
    from millrace.contracts.setup import SetupConsentRequest

    workspace, request, root = note_fixture(tmp_path)
    service = SetupActionService(request, journal_root=root)
    intent = SetupIntent("create_project_note", "docs/project.md", "User task", "note")
    before = service.disclose(intent)
    payload = SetupConsentRequest(
        intent,
        before.selection_digest,
        before.trust_disclosure.disclosure_digest,
        "trust",
        True,
    )
    original = service.journal.locked
    injected = []

    @contextmanager
    def changed_after_lock(*, create=False, write=False):
        with original(create=create, write=write) as locked:
            if create:
                (workspace / "docs").mkdir()
                (workspace / "docs/external.md").write_text(
                    "Actual changed selected bytes"
                )
                injected.append(True)
            yield locked

    monkeypatch.setattr(service.journal, "locked", changed_after_lock)
    with pytest.raises(SetupRefusal, match="setup_selection_changed"):
        service.accept(payload)
    assert injected == [True]
    assert SetupService(request).inspect().selection_digest != before.selection_digest
    assert not (workspace / "docs/project.md").exists()
    # A separate process checks persisted owner state, not the writer's dict.
    script = """
import json,sys
from pathlib import Path
from millrace.adapters.cli.setup_actions import SetupActionService
from millrace.adapters.cli.setup_receipts import SetupJournal
from millrace.contracts.setup import SetupRequest, SetupConsentRequest, SetupRefusal
root=Path(sys.argv[1])
with SetupJournal(root).locked() as journal:
    for key in ("consents","actions","results","consent_keys","action_keys"):
        assert journal.state[key] == {}, key
service=SetupActionService(SetupRequest(**json.loads(sys.argv[2])),journal_root=root)
try:
    service.accept(SetupConsentRequest.from_wire(json.loads(sys.argv[3])))
except SetupRefusal:
    pass
else:
    raise AssertionError("stale acceptance accepted in fresh process")
with SetupJournal(root).locked() as journal:
    assert all(journal.state[k] == {} for k in ("consents","actions","results"))
print("no acceptance, result or admitted action")
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(root),
            json.dumps(asdict(request)),
            json.dumps(asdict(payload)),
        ],
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == "no acceptance, result or admitted action"


def test_committed_acceptance_replays_in_fresh_process_after_actual_drift(tmp_path):
    from dataclasses import asdict

    workspace, request, _ = note_fixture(tmp_path)
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    service = SetupActionService(request, journal_root=home / ".millrace/setup-journal")
    payload, _, _ = consent(
        service,
        SetupIntent("create_project_note", "docs/project.md", "User task", "note"),
    )
    original = service.accept(payload).to_wire()
    (workspace / "docs").mkdir()
    (workspace / "docs/external.md").write_text("Later changed selection")
    assert cli(request, home, "accept_trust", asdict(payload)) == original
    result = original["action_result"]
    assert cli(request, home, "lookup", {"receipt_id": result["receipt_id"]}) == result
    with pytest.raises(SetupRefusal):
        service.propose(original["trust_acceptance"]["receipt_id"])
    assert not (workspace / "docs/project.md").exists()
