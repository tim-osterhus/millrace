from __future__ import annotations

from pathlib import Path

import pytest

from millrace.adapters.cli.main import main


@pytest.mark.parametrize(
    "argv",
    [
        ["--workspace", "/tmp/not-owned", "demo", "--auto-confirm"],
        ["--actor-id", "local_operator", "demo", "--auto-confirm"],
        ["--db", "/tmp/not-owned", "demo", "--auto-confirm"],
        ["--cas", "/tmp/not-owned", "demo", "--auto-confirm"],
        ["--bounded", "demo", "--auto-confirm"],
        ["demo", "--json"],
        ["demo", "--resume", "../x"],
        ["demo", "--unknown"],
        ["demo"],
    ],
)
def test_demo_parser_and_preflight_refuse_before_effects(
    tmp_path: Path, monkeypatch, argv
):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert main(argv) != 0
    assert list(tmp_path.iterdir()) == []


def test_demo_help_has_only_declared_flags(capsys) -> None:
    assert main(["demo", "--help"]) == 0
    text = capsys.readouterr().out
    assert all(flag in text for flag in ("--keep", "--auto-confirm", "--resume"))
    assert "--workspace" not in text


@pytest.mark.parametrize("terminal", [False, True])
def test_demo_auto_confirm_and_json_do_not_accept_trust(
    tmp_path, monkeypatch, terminal
):
    import io
    import json

    from cli.test_cli_setup import invoke
    from cli.test_setup_actions import demo_trust_fixture
    from cli.test_setup_interactive import TerminalInput
    from millrace.adapters.cli import demo

    service, _ = demo_trust_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "sys.stdin", TerminalInput("yes\n") if terminal else io.StringIO("yes\n")
    )
    monkeypatch.setattr(
        demo, "_prepare", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    code, out, err = invoke(["--json", "demo", "--auto-confirm"])
    assert code == 2
    assert json.loads(out)["reason"] == "demo_trust_required"
    assert "[y/N]" not in err
    assert not demo.demo_root().exists()
    assert not service.journal.root.exists()


def test_demo_current_owner_consent_enters_and_projects_receipt(tmp_path, monkeypatch):
    from cli.test_setup_actions import accept_demo_trust, demo_trust_fixture
    from millrace.adapters.cli import demo

    service, _ = demo_trust_fixture(tmp_path, monkeypatch)
    accepted = accept_demo_trust(service)
    monkeypatch.setattr(
        demo, "_prepare", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    code, result = demo.run_demo(keep=True, auto_confirm=True, resume=None)
    assert code == 130 and result["outcome"] == "demo_interrupted"
    assert result["trust_acceptance"] == accepted.to_wire()
    assert demo.demo_root().exists()
    # A fresh owner resolves the same consent on same-ID resume.
    code, resumed = demo.run_demo(
        keep=True, auto_confirm=True, resume=result["demo_id"]
    )
    assert code == 130 and resumed["demo_id"] == result["demo_id"]


def test_demo_stale_trust_refuses_resume_without_mutation(tmp_path, monkeypatch):
    from cli.test_setup_actions import accept_demo_trust, demo_trust_fixture
    from millrace.adapters.cli import demo
    from millrace.contracts.setup import SetupRefusal

    service, identity = demo_trust_fixture(tmp_path, monkeypatch)
    accept_demo_trust(service)
    monkeypatch.setattr(
        demo, "_prepare", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    _, result = demo.run_demo(keep=True, auto_confirm=True, resume=None)
    before = {
        str(p): p.read_bytes() for p in demo.demo_root().rglob("*") if p.is_file()
    }
    identity["plus_artifact"] = "sha256:" + "8" * 64
    with pytest.raises(SetupRefusal, match="demo_trust_required"):
        demo.run_demo(keep=True, auto_confirm=True, resume=result["demo_id"])
    assert before == {
        str(p): p.read_bytes() for p in demo.demo_root().rglob("*") if p.is_file()
    }


def test_demo_revalidates_identity_before_mutation(tmp_path, monkeypatch):
    from cli.test_setup_actions import accept_demo_trust, demo_trust_fixture
    from millrace.adapters.cli import demo
    from millrace.contracts.setup import SetupRefusal

    service, _ = demo_trust_fixture(tmp_path, monkeypatch)
    accept_demo_trust(service)
    monkeypatch.setattr(
        demo, "_prepare", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    monkeypatch.setattr(demo, "installed_identity", lambda: {"substituted": True})
    with pytest.raises(SetupRefusal, match="demo_trust_selection_changed"):
        demo.run_demo(keep=True, auto_confirm=True, resume=None)
    assert not demo.demo_root().exists()
