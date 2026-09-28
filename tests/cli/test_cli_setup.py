from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout

import pytest

from millrace.adapters.cli.main import main


def invoke(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(args)
    return code, out.getvalue(), err.getvalue()


def test_demo_is_truthfully_unavailable_without_effects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-do-not-disclose")
    code, out, err = invoke(["--json", "setup"])
    assert code == 0 and err == ""
    result = json.loads(out)
    assert result["command"] == "setup" and result["ok"]
    data = result["data"]
    assert data["status"] == "blocked"
    assert "demo_unavailable" in {x["code"] for x in data["blockers"]}
    assert data["next_action"] is None and data["proposed_actions"] == []
    assert data["trust_acceptance"] is None
    assert "test-secret" not in out
    assert not list(tmp_path.iterdir())
    optional = [x for x in data["checks"] if not x["required"]]
    assert optional and all(not x["gates_next_action"] for x in optional)


@pytest.mark.parametrize(
    "args",
    [
        ["setup", "--action", "factory"],
        ["setup", "--setup-schema-version", "2"],
        ["setup", "--management-mode", "managed"],
        ["setup", "--consent", "yes"],
        ["--actor-id", "", "setup"],
        ["--bounded", "setup"],
        ["--workspace", "somewhere", "setup"],
    ],
)
def test_parser_and_unsupported_modes_refuse_before_mutation(
    args, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    code, out, err = invoke(["--json", *args])
    assert code != 0 and err == ""
    assert not json.loads(out)["ok"]
    assert not list(tmp_path.iterdir())


def test_useful_recipe_missing_workspace_is_inspection_not_initialization(tmp_path):
    target = tmp_path / "absent"
    code, out, err = invoke(
        ["--json", "--workspace", str(target), "setup", "--action", "useful_recipe"]
    )
    assert code == 0 and err == ""
    data = json.loads(out)["data"]
    assert "workspace_missing" in {x["code"] for x in data["blockers"]}
    assert data["selection"]["action_kind"] == "useful_recipe"
    assert not target.exists()


def test_setup_help_remains_one_json_document():
    code, out, err = invoke(["--json", "setup", "--help"])
    assert code == 0 and err == ""
    assert "--action" in json.loads(out)["data"]["help"]


def test_text_setup_has_actionable_blocker_explanation():
    code, out, err = invoke(["setup"])
    assert code == 0 and err == ""
    assert "blocked" in out


def test_parse_error_omits_untrusted_argument_payload():
    code, out, err = invoke(["--json", "setup", "--action", "SECRET-PAYLOAD"])
    assert code != 0 and err == "" and "SECRET-PAYLOAD" not in out
    assert json.loads(out)["code"] == "argument_parse_error"


def test_setup_word_in_old_command_argument_preserves_legacy_error_stream():
    code, out, err = invoke(["--json", "--workspace", "setup", "not-a-command"])
    assert code != 0 and out == "" and json.loads(err)["code"] == "argument_parse_error"


def test_resolved_workspace_overflow_refuses_without_truncation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    relative = "/".join(["x" * 100] * 5)
    assert len(relative) == 504 and len(str(tmp_path / relative)) > 512
    code, out, err = invoke(
        ["--json", "--workspace", relative, "setup", "--action", "useful_recipe"]
    )
    assert code == 3 and err == ""
    result = json.loads(out)
    assert not result["ok"] and result["code"] == "setup_wire_unrepresentable"
    assert relative not in out and not list(tmp_path.iterdir())


def test_selected_context_cli_reports_captured_and_deferred_sources(tmp_path):
    from cli.test_setup_preflight import official_recipe

    _, request = official_recipe(tmp_path)
    code, out, err = invoke(
        [
            "--json",
            "--workspace",
            request.workspace,
            "setup",
            "--action",
            "useful_recipe",
            "--plan-fingerprint",
            request.plan_fingerprint,
        ]
    )
    assert code == 0 and err == ""
    data = json.loads(out)["data"]
    assert data["selection"]["context_digest"].startswith("sha256:")
    assert data["selection"]["local_config_digest"].startswith("sha256:")
    assert len(data["context_coverage"]) == 3
    assert data["status"] == "blocked" and data["next_action"] is None
    assert any(c["conditional"] for c in data["context_coverage"])
    assert "runner_readiness_unavailable" in {b["code"] for b in data["blockers"]}


@pytest.mark.parametrize(
    "payload", ["{secret-invalid-json", '{"secret":"not-an-action"}']
)
@pytest.mark.parametrize("json_mode", [True, False])
def test_action_transport_refusal_emits_one_response_without_payload(
    tmp_path, monkeypatch, payload, json_mode
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "credential-secret-not-exposed")
    args = [
        "--workspace",
        str(tmp_path / "workspace"),
        "setup",
        "--action",
        "useful_recipe",
        "--setup-operation",
        "apply",
        "--request-json",
        payload,
    ]
    code, out, err = invoke((["--json"] if json_mode else []) + args)
    assert code != 0
    assert "secret" not in out + err and payload not in out + err
    if json_mode:
        assert err == "" and json.loads(out)["ok"] is False
    else:
        assert out == "" and len(err.splitlines()) == 1
    assert not list(tmp_path.iterdir())
