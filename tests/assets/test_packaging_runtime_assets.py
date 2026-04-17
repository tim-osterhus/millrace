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
        "millrace_ai/assets/entrypoints/execution/builder.md",
        "millrace_ai/assets/entrypoints/planning/planner.md",
        "millrace_ai/assets/loops/execution/default.json",
        "millrace_ai/assets/modes/standard_plain.json",
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
        "millrace_ai/assets/skills/stage/planning/planner-core/SKILL.md",
    }
    assert required_assets.issubset(entries)
    assert "millrace_ai/py.typed" in wheel_names
    assert all(not name.startswith("millrace_ai/assets/roles/") for name in entries)
    assert all(not name.endswith(".DS_Store") for name in wheel_names)
