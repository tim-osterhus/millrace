from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from cli.test_cli_setup import invoke


class TerminalInput(io.StringIO):
    def isatty(self):
        return True


def supported_install_evidence(monkeypatch):
    from importlib import metadata

    from millrace.adapters.cli.setup_install_evidence import VERSION

    class Distribution:
        def __init__(self, name):
            self.metadata = {"Name": name, "Version": VERSION}
            self.version = VERSION
            self.files = ()
            self.entry_points = ()

        @staticmethod
        def locate_file(path):
            return Path(sys.prefix) / str(path)

    installed = {
        name: Distribution(name) for name in ("millrace-ai", "millrace-plus")
    }

    def distribution(name):
        try:
            return installed[name]
        except KeyError as exc:
            raise metadata.PackageNotFoundError(name) from exc

    monkeypatch.setattr(metadata, "distribution", distribution)


def test_interactive_decline_explains_effects_and_changes_nothing(
    tmp_path, monkeypatch
):
    from millrace.adapters.cli import setup_actions
    from millrace.adapters.cli.setup_receipts import SetupJournal

    supported_install_evidence(monkeypatch)
    monkeypatch.setattr("sys.stdin", TerminalInput("n\n"))
    monkeypatch.setattr(
        setup_actions,
        "SetupJournal",
        lambda root=None: SetupJournal(tmp_path / "journal"),
    )
    code, out, err = invoke(["setup", "--interactive"])
    assert code == 0
    assert "pypi.org" in err and "files.pythonhosted.org" in err
    assert "[y/N]" in err
    assert "declined" in out.lower()
    assert not list(tmp_path.iterdir())


def test_interactive_eof_is_decline(tmp_path, monkeypatch):
    from millrace.adapters.cli import setup_actions
    from millrace.adapters.cli.setup_receipts import SetupJournal

    supported_install_evidence(monkeypatch)
    monkeypatch.setattr("sys.stdin", TerminalInput(""))
    monkeypatch.setattr(
        setup_actions,
        "SetupJournal",
        lambda root=None: SetupJournal(tmp_path / "journal"),
    )
    code, out, err = invoke(["setup", "--interactive"])
    assert code == 0 and "declined" in out.lower()
    assert not list(tmp_path.iterdir())


def test_interactive_rejects_json_without_prompt(monkeypatch):
    monkeypatch.setattr("sys.stdin", TerminalInput("y\n"))
    code, out, err = invoke(["--json", "setup", "--interactive"])
    assert code != 0 and "[y/N]" not in err
    assert "setup_interactive_requires_text_terminal" in out


def test_interactive_rejects_pipe_without_prompt(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))
    code, out, err = invoke(["setup", "--interactive"])
    assert code != 0 and "[y/N]" not in err


def test_wheelhouse_is_not_silently_ignored_by_inspection(tmp_path):
    code, out, err = invoke(["--json", "setup", "--wheelhouse", str(tmp_path)])
    assert code != 0
    assert "setup_wheelhouse_requires_interactive" in out


@pytest.mark.parametrize("answer", ["n\n", ""])
def test_inline_demo_trust_decline_creates_no_state(tmp_path, monkeypatch, answer):
    from cli.test_cli_setup import invoke
    from cli.test_setup_actions import demo_trust_fixture
    from millrace.adapters.cli import demo

    service, _ = demo_trust_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.stdin", TerminalInput(answer))
    monkeypatch.setattr(
        demo, "_prepare", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    code, out, err = invoke(["demo", "--auto-confirm"])
    assert code == 2 and "demo_trust_declined" in out
    assert str(demo.demo_root()) in err and "[y/N]" in err
    assert not demo.demo_root().exists() and not service.journal.root.exists()


def test_inline_demo_trust_accepts_through_real_owner(tmp_path, monkeypatch):
    from cli.test_cli_setup import invoke
    from cli.test_setup_actions import demo_trust_fixture
    from millrace.adapters.cli import demo

    service, _ = demo_trust_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.stdin", TerminalInput("yes\n"))
    monkeypatch.setattr(
        demo, "_prepare", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    code, out, err = invoke(["demo", "--auto-confirm"])
    assert code == 130
    assert "[y/N]" in err and "Consent receipt:" in err
    assert service.resolve_demo_trust() is not None
    assert demo.demo_root().exists()


def test_inline_demo_trust_interruption_never_enters_runtime(tmp_path, monkeypatch):
    from cli.test_setup_actions import demo_trust_fixture
    from millrace.adapters.cli import demo

    service, _ = demo_trust_fixture(tmp_path, monkeypatch)

    class InterruptedInput(TerminalInput):
        def readline(self):
            raise KeyboardInterrupt

    monkeypatch.setattr("sys.stdin", InterruptedInput())
    monkeypatch.setattr(
        demo, "_prepare", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    code, out, err = invoke(["demo", "--auto-confirm"])
    assert code == 130 and "demo_interrupted" in out
    assert not demo.demo_root().exists() and not service.journal.root.exists()


def test_inline_demo_trust_refuses_changed_selection_at_prompt(tmp_path, monkeypatch):
    from cli.test_setup_actions import demo_trust_fixture
    from millrace.adapters.cli import demo

    service, identity = demo_trust_fixture(tmp_path, monkeypatch)

    class ChangedInput(TerminalInput):
        def readline(self):
            identity["core_artifact"] = "sha256:" + "9" * 64
            return "yes\n"

    monkeypatch.setattr("sys.stdin", ChangedInput())
    monkeypatch.setattr(
        demo, "_prepare", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    code, out, err = invoke(["demo", "--auto-confirm"])
    assert code == 2 and "setup_observation_changed" in out
    assert not demo.demo_root().exists() and not service.journal.root.exists()


def test_inline_demo_trust_does_not_bypass_uncertain_action(tmp_path, monkeypatch):
    from cli.test_setup_actions import accept_demo_trust, demo_trust_fixture
    from millrace.adapters.cli import demo

    service, _ = demo_trust_fixture(tmp_path, monkeypatch)
    accepted = accept_demo_trust(service)
    with service.journal.locked(write=True) as journal:
        action_id = journal.state["consents"][accepted.receipt_id]["action_id"]
        journal.state["actions"][action_id]["status"] = "pending"
        journal.commit()
    monkeypatch.setattr("sys.stdin", TerminalInput("yes\n"))
    code, out, err = invoke(["demo", "--auto-confirm"])
    assert code == 2 and "demo_trust_action_unresolved" in out
    assert "[y/N]" not in err and not demo.demo_root().exists()
