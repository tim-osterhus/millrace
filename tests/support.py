from __future__ import annotations

from pathlib import Path
from typing import Callable, Any, Literal
import json
import shutil
import time
import tomllib

from millrace_engine.config import build_runtime_paths, load_engine_config
from millrace_engine.control import EngineControl


TESTS_ROOT = Path(__file__).resolve().parent
FIXTURE_ROOT = TESTS_ROOT / "fixtures"
RUNTIME_ROOT = TESTS_ROOT.parent
ASSET_AGENTS_ROOT = RUNTIME_ROOT / "millrace_engine" / "assets" / "agents"
SAMPLE_AGENTS_ROOT = FIXTURE_ROOT / "tui_samples" / "agents"


def _copy_prompt_assets(destination: Path) -> None:
    source_agents = ASSET_AGENTS_ROOT
    destination_agents = destination / "agents"
    destination_agents.mkdir(parents=True, exist_ok=True)
    for prompt_path in sorted(source_agents.glob("_*.md")):
        shutil.copy2(prompt_path, destination_agents / prompt_path.name)


def fixture_source(name: str) -> Path:
    path = FIXTURE_ROOT / name
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(f"unknown test fixture: {name}")
    return path


def _fixture_metadata(source: Path) -> dict[str, Any]:
    metadata_path = source / "fixture.toml"
    if not metadata_path.exists():
        return {}
    return tomllib.loads(metadata_path.read_text(encoding="utf-8"))


def _copy_overlay(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        if child.name == "fixture.toml":
            continue
        target = destination / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(child, target)


def materialize_workspace_fixture(name: str, destination: Path) -> Path:
    source = fixture_source(name)
    metadata = _fixture_metadata(source)
    parent = metadata.get("extends")
    if parent:
        materialize_workspace_fixture(str(parent), destination)
    _copy_overlay(source, destination)
    _copy_prompt_assets(destination)
    return destination


def load_workspace_fixture(tmp_path: Path, name: str) -> tuple[Path, Path]:
    workspace = tmp_path / "millrace"
    if workspace.exists():
        shutil.rmtree(workspace)
    materialize_workspace_fixture(name, workspace)
    return workspace, workspace / "millrace.toml"


def runtime_paths(config_path: Path):
    loaded = load_engine_config(config_path)
    return build_runtime_paths(loaded.config)


def runtime_workspace(tmp_path: Path, *, name: str = "millrace-runtime") -> tuple[Path, Path]:
    workspace = tmp_path / name
    if workspace.exists():
        shutil.rmtree(workspace)
    result = EngineControl.init_workspace(workspace)
    assert result.applied is True
    return workspace, workspace / "millrace.toml"


def wait_for(predicate: Callable[[], bool], *, timeout: float = 5.0, interval: float = 0.05) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("timed out waiting for condition")


def read_state(state_path: Path) -> dict[str, object]:
    return json.loads(state_path.read_text(encoding="utf-8"))


def set_engine_idle_mode(
    config_path: Path,
    mode: Literal["watch", "poll"],
    *,
    poll_interval_seconds: int | None = None,
) -> None:
    lines = config_path.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    in_engine = False
    saw_mode = False
    saw_poll_interval = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_engine = stripped == "[engine]"
        if in_engine and stripped.startswith("idle_mode = "):
            updated.append(f'idle_mode = "{mode}"')
            saw_mode = True
            continue
        if in_engine and poll_interval_seconds is not None and stripped.startswith("poll_interval_seconds = "):
            updated.append(f"poll_interval_seconds = {poll_interval_seconds}")
            saw_poll_interval = True
            continue
        updated.append(line)

    if not saw_mode:
        raise AssertionError(f"engine idle_mode not found in {config_path}")
    if poll_interval_seconds is not None and not saw_poll_interval:
        raise AssertionError(f"engine poll_interval_seconds not found in {config_path}")

    config_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
