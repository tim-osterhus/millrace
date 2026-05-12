from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "src" / "millrace_ai" / "assets" / "skills"
LINT_SCRIPT = SKILLS_DIR / "millrace-skill-creator" / "scripts" / "lint_skill.py"


def test_every_shipped_skill_package_passes_current_skill_lint() -> None:
    skill_packages = sorted(path.parent for path in SKILLS_DIR.rglob("SKILL.md"))

    failures: list[str] = []
    for package_path in skill_packages:
        result = subprocess.run(
            [sys.executable, str(LINT_SCRIPT), str(package_path)],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            failures.append(f"{package_path.relative_to(REPO_ROOT)}\n{result.stdout}{result.stderr}")

    assert not failures, "\n\n".join(failures)
