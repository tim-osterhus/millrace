from __future__ import annotations

import yaml
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_PATH = REPO_ROOT / "AGENTS.md"
ORCHESTRATOR_PATH = REPO_ROOT / "work" / "Orchestrator.md"
RELEASE_PATH = REPO_ROOT / "work" / "Release.md"
PROMOTE_SCRIPT_PATH = REPO_ROOT / "tools" / "promote_clean.py"
STAGE_SCRIPT_PATH = REPO_ROOT / "work" / "migration" / "stage_clean_repo.py"
PROMOTION_MANIFEST_PATH = REPO_ROOT / "work" / "migration" / "clean_promotion_manifest.yaml"


def test_routine_promotion_contract_uses_promote_clean_and_not_release_clean() -> None:
    agents = AGENTS_PATH.read_text(encoding="utf-8")
    orchestrator = ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    release = RELEASE_PATH.read_text(encoding="utf-8")

    assert "clean/` is a normal local mirror of public `main`, not a scratch checkout." in agents
    assert "tools/promote_clean.py --commit-message" in agents
    assert "tools/release_clean.py" in agents
    assert "Do not use `tools/release_clean.py` as a substitute for routine promotion." in agents

    assert "python3 tools/promote_clean.py --commit-message" in orchestrator
    assert "work/migration/stage_clean_repo.py --apply" in orchestrator
    assert "release_clean.py" not in orchestrator

    assert "python3 tools/release_clean.py <version>" in release
    assert "python3 tools/promote_clean.py --commit-message" in release
    assert "Do not use `tools/release_clean.py` as a substitute for routine source-to-clean promotion." in release


def test_promote_clean_script_guards_local_clean_alignment_contract() -> None:
    promote_script = PROMOTE_SCRIPT_PATH.read_text(encoding="utf-8")
    stage_script = STAGE_SCRIPT_PATH.read_text(encoding="utf-8")

    expected_snippets = (
        "clean/ is a normal local mirror of public main, not a scratch checkout.",
        "Local clean repo must be exactly aligned with origin/main before promotion.",
        "Local clean repo diverged from origin/main after push.",
        "stage_clean_repo.py",
    )

    for snippet in expected_snippets:
        assert snippet in promote_script, snippet

    assert "tools/promote_clean.py" in stage_script
    assert "Routine\nrun-card promotion should go through ``tools/promote_clean.py``." in stage_script


def test_clean_promotion_manifest_generates_runtime_docs_packaged_copy() -> None:
    manifest = yaml.safe_load(PROMOTION_MANIFEST_PATH.read_text(encoding="utf-8"))
    generated_docs = {
        (entry["source"], entry["dest"]) for entry in manifest["generated_packaged_docs"]
    }

    assert (
        "docs/runtime/README.md",
        "millrace_engine/assets/docs/runtime/README.md",
    ) in generated_docs
