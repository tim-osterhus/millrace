from __future__ import annotations

from pathlib import Path

from millrace_engine.control import EngineControl
from millrace_engine.health import HealthCheckStatus, WorkspaceHealthCheck, build_workspace_health_report
from tests.support import runtime_workspace


def _check(report, check_id: str) -> WorkspaceHealthCheck:
    return next(check for check in report.checks if check.check_id == check_id)


def _set_fake_codex_path(tmp_path: Path, monkeypatch) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    codex_path = fake_bin / "codex"
    codex_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    codex_path.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))


def test_workspace_health_report_passes_for_primary_runtime_workspace(tmp_path: Path, monkeypatch) -> None:
    _set_fake_codex_path(tmp_path, monkeypatch)
    _, config_path = runtime_workspace(tmp_path)
    report = build_workspace_health_report(config_path)

    assert report.status is HealthCheckStatus.PASS
    assert report.ok is True
    assert report.config_source_kind == "native_toml"
    assert _check(report, "workspace.directories").status is HealthCheckStatus.PASS
    assert _check(report, "workspace.files").status is HealthCheckStatus.PASS
    assert _check(report, "assets.required").status is HealthCheckStatus.PASS


def test_workspace_health_report_passes_for_initialized_workspace(tmp_path: Path, monkeypatch) -> None:
    _set_fake_codex_path(tmp_path, monkeypatch)
    destination = tmp_path / "healthy-workspace"
    init_result = EngineControl.init_workspace(destination)

    assert init_result.applied is True

    report = build_workspace_health_report(destination / "millrace.toml")

    assert report.status is HealthCheckStatus.PASS
    assert report.ok is True
    assert report.config_source_kind == "native_toml"
    assert report.workspace_root_source == "loaded_config"
    assert report.summary.failed_checks == 0
    assert report.summary.warning_checks == 0
    assert _check(report, "config.load").status is HealthCheckStatus.PASS
    assert _check(report, "workspace.directories").status is HealthCheckStatus.PASS
    assert _check(report, "workspace.files").status is HealthCheckStatus.PASS
    assert _check(report, "assets.required").status is HealthCheckStatus.PASS
    assert _check(report, "execution.runners").status is HealthCheckStatus.PASS
    assert report.bootstrap_ready is True
    assert report.execution_ready is True


def test_workspace_health_report_fails_for_invalid_native_config(tmp_path: Path) -> None:
    destination = tmp_path / "broken-config-workspace"
    init_result = EngineControl.init_workspace(destination)

    assert init_result.applied is True
    (destination / "millrace.toml").write_text("[engine\n", encoding="utf-8")

    report = build_workspace_health_report(destination / "millrace.toml")

    assert report.status is HealthCheckStatus.FAIL
    assert report.ok is False
    assert report.config_source_kind == "unresolved"
    config_check = _check(report, "config.load")
    assert config_check.status is HealthCheckStatus.FAIL
    assert any("config TOML is invalid" in detail for detail in config_check.details)


def test_workspace_health_report_detects_missing_runtime_dir_and_workspace_file(tmp_path: Path) -> None:
    destination = tmp_path / "missing-contracts-workspace"
    init_result = EngineControl.init_workspace(destination)

    assert init_result.applied is True
    (destination / "agents" / "ideas" / "raw").rmdir()
    (destination / "agents" / "status.md").unlink()

    report = build_workspace_health_report(destination / "millrace.toml")

    assert report.status is HealthCheckStatus.FAIL
    directories_check = _check(report, "workspace.directories")
    files_check = _check(report, "workspace.files")
    assert directories_check.status is HealthCheckStatus.FAIL
    assert files_check.status is HealthCheckStatus.FAIL
    assert any("ideas/raw" in detail for detail in directories_check.details)
    assert any("status.md" in detail for detail in files_check.details)


def test_workspace_health_report_fails_for_missing_configured_asset(tmp_path: Path) -> None:
    destination = tmp_path / "missing-asset-workspace"
    init_result = EngineControl.init_workspace(destination)

    assert init_result.applied is True
    config_path = destination / "millrace.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "\n[stages.builder]\nprompt_file = \"agents/custom/missing-builder.md\"\n",
        encoding="utf-8",
    )

    report = build_workspace_health_report(config_path)

    assert report.status is HealthCheckStatus.FAIL
    assets_check = _check(report, "assets.required")
    assert assets_check.status is HealthCheckStatus.FAIL
    assert any("missing-builder.md" in detail for detail in assets_check.details)


def test_workspace_health_report_distinguishes_bootstrap_from_execution_readiness(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace, config_path = runtime_workspace(tmp_path)
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))

    report = build_workspace_health_report(config_path)

    assert report.status is HealthCheckStatus.FAIL
    assert report.ok is False
    assert report.bootstrap_ready is True
    assert report.execution_ready is False
    execution_check = _check(report, "execution.runners")
    assert execution_check.status is HealthCheckStatus.FAIL
    assert any("missing prerequisite: codex" in detail for detail in execution_check.details)
    prerequisite = next(entry for entry in report.runner_prerequisites if entry.runner.value == "codex")
    assert prerequisite.available is False
    assert prerequisite.executable == "codex"
    assert prerequisite.affected_stage_nodes
    assert prerequisite.affected_stages
