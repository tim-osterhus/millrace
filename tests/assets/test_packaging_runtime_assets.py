from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

RUNTIME_ROOT = Path(__file__).resolve().parents[2]


def _build_wheel(tmp_path: Path) -> Path:
    out_dir = tmp_path / "dist"
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(RUNTIME_ROOT / "build", ignore_errors=True)
    for candidate in RUNTIME_ROOT.rglob(".DS_Store"):
        try:
            candidate.unlink()
        except OSError:
            continue

    result = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out_dir)],
        cwd=RUNTIME_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout

    wheels = sorted(out_dir.glob("millrace_ai-*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def _read_wheel_metadata(wheel_path: Path) -> str:
    with zipfile.ZipFile(wheel_path) as wheel:
        metadata_name = next(name for name in wheel.namelist() if name.endswith(".dist-info/METADATA"))
        return wheel.read(metadata_name).decode("utf-8")


def test_wheel_includes_runtime_assets(tmp_path: Path) -> None:
    wheel_path = _build_wheel(tmp_path)

    with zipfile.ZipFile(wheel_path) as wheel:
        wheel_names = set(wheel.namelist())
        entries = {
            name
            for name in wheel_names
            if name.startswith("millrace_ai/assets/") and not name.endswith("/")
        }

    assert entries
    required_assets = {
        "millrace_ai/assets/entrypoints/execution/lad_builder.md",
        "millrace_ai/assets/entrypoints/execution/lad_integrator.md",
        "millrace_ai/assets/entrypoints/learning/librarian.md",
        "millrace_ai/assets/entrypoints/planning/lad_planner.md",
        "millrace_ai/assets/graphs/execution/lad.json",
        "millrace_ai/assets/graphs/execution/lad_integrator.json",
        "millrace_ai/assets/graphs/planning/lad.json",
        "millrace_ai/assets/loops/execution/lad.json",
        "millrace_ai/assets/loops/execution/lad_integrator.json",
        "millrace_ai/assets/modes/lad_codex.json",
        "millrace_ai/assets/modes/lad_codex_integrated.json",
        "millrace_ai/assets/modes/lad_pi.json",
        "millrace_ai/assets/modes/efficient_learning_lad_mixed.json",
        "millrace_ai/assets/modes/" "blueprint_" "learning_lad_codex" ".json",
        "millrace_ai/assets/registry/stage_kinds/execution/lad_builder.json",
        "millrace_ai/assets/registry/stage_kinds/execution/lad_integrator.json",
        "millrace_ai/assets/registry/stage_kinds/learning/librarian.json",
        "millrace_ai/assets/registry/stage_kinds/planning/lad_arbiter.json",
        "millrace_ai/assets/registry/document_adapters/builtin_markdown_v1.json",
        "millrace_ai/assets/registry/lifecycle_mutation_plans/default_lifecycle_mutations.json",
        "millrace_ai/assets/registry/queue_claim_policies/default_queue_claim_policies.json",
        "millrace_ai/assets/registry/recovery_policies/default_recovery_policies.json",
        "millrace_ai/assets/registry/runtime_effect_handlers/default_effect_handlers.json",
        "millrace_ai/assets/registry/runtime_effect_runners/default_effect_runners.json",
        "millrace_ai/assets/registry/runtime_failure_policies/default_runtime_failure_policies.json",
        "millrace_ai/assets/registry/terminal_actions/default_terminal_actions.json",
        "millrace_ai/assets/registry/work_item_families/task.json",
        "millrace_ai/assets/registry/workspace_schema_epochs/current.json",
        "millrace_ai/assets/skills/README.md",
        "millrace_ai/assets/skills/millrace-skill-creator/SKILL.md",
        "millrace_ai/assets/skills/millrace-skill-creator/references/hybrid-format.md",
        "millrace_ai/assets/skills/millrace-skill-creator/references/donor-synthesis.md",
        "millrace_ai/assets/skills/millrace-skill-creator/scripts/_shared.py",
        "millrace_ai/assets/skills/millrace-skill-creator/scripts/scaffold_skill.py",
        "millrace_ai/assets/skills/millrace-skill-creator/scripts/lint_skill.py",
        "millrace_ai/assets/skills/millrace-skill-creator/scripts/evaluate_skill.py",
        "millrace_ai/assets/skills/millrace-skill-creator/evals/creator_smoke_cases.json",
        "millrace_ai/assets/skills/millrace-skill-creator/evals/pilot_shape_cases.json",
        "millrace_ai/assets/skills/skills_index.md",
        "millrace_ai/assets/skills/stage/execution/builder-core/SKILL.md",
        "millrace_ai/assets/skills/stage/execution/integrator-core/SKILL.md",
        "millrace_ai/assets/skills/stage/learning/librarian-core/SKILL.md",
        "millrace_ai/assets/skills/stage/planning/planner-core/SKILL.md",
    }
    assert required_assets.issubset(entries)
    removed_assets = {
        "millrace_ai/assets/modes/default_codex.json",
        "millrace_ai/assets/graphs/execution/standard.json",
        "millrace_ai/assets/registry/stage_kinds/execution/builder.json",
        "millrace_ai/assets/entrypoints/execution/builder.md",
    }
    assert not removed_assets & entries
    assert "millrace_ai/py.typed" in wheel_names
    assert all(not name.startswith("millrace_ai/assets/roles/") for name in entries)
    assert all(not name.startswith("millrace_ai/web/") for name in wheel_names)
    assert all(not name.startswith("millrace_web/") for name in wheel_names)
    assert all(not name.endswith(".DS_Store") for name in wheel_names)


def test_wheel_metadata_declares_apache_license(tmp_path: Path) -> None:
    wheel_path = _build_wheel(tmp_path)
    metadata = _read_wheel_metadata(wheel_path)

    assert "License-Expression: Apache-2.0" in metadata


def test_readme_uses_repo_license_badge_instead_of_pypi_license_badge() -> None:
    readme = (RUNTIME_ROOT / "README.md").read_text(encoding="utf-8")

    assert "https://img.shields.io/pypi/l/" not in readme
    assert "https://img.shields.io/github/license/tim-osterhus/millrace" in readme
