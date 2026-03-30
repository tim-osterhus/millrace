from __future__ import annotations

from pathlib import Path

from millrace_engine.config import SizingConfig
from millrace_engine.contracts import TaskCard
from millrace_engine.policies.sizing import (
    SizeClass,
    adaptive_upscope_task_card,
    evaluate_size_policy,
    refresh_size_status,
)


def _task_card(body: str, *, metadata: dict[str, object] | None = None) -> TaskCard:
    return TaskCard.model_validate(
        {
            "heading": "## 2026-03-19 - Size policy test",
            "body": body,
            "metadata": metadata or {},
        }
    )


def test_task_size_uses_files_to_touch_section_and_complexity_for_two_of_three(tmp_path: Path) -> None:
    root = tmp_path
    (root / "src").mkdir()
    (root / "src/app.py").write_text("alpha\n\nbeta\n", encoding="utf-8")
    task = _task_card(
        "\n".join(
            [
                "**Complexity:** INVOLVED",
                "",
                "### Files to touch (explicit):",
                "- `src/app.py`",
            ]
        )
    )
    config = SizingConfig.model_validate(
        {
            "mode": "task",
            "task": {
                "file_count_threshold": 1,
                "nonempty_line_count_threshold": 999_999_999,
            },
        }
    )

    view = evaluate_size_policy(root=root, task=task, config=config)

    assert view.classified_as is SizeClass.LARGE
    assert view.latched_as is SizeClass.LARGE
    assert view.triggered_sources == ("task",)
    assert view.task.file_count == 1
    assert view.task.file_count_source == "body:files_to_touch"
    assert view.task.nonempty_line_count == 2
    assert view.task.nonempty_line_count_source == "repo:files_to_touch"
    assert view.task.files_to_touch == ("src/app.py",)
    assert view.task.threshold_hits == ("file_count", "complexity")


def test_task_size_uses_narrow_metadata_fallbacks_without_explicit_file_list(tmp_path: Path) -> None:
    task = _task_card(
        "**Complexity:** MODERATE",
        metadata={
            "files_to_touch_count": 3,
            "task_large_loc": 120,
        },
    )
    config = SizingConfig.model_validate(
        {
            "mode": "task",
            "task": {
                "file_count_threshold": 3,
                "nonempty_line_count_threshold": 100,
            },
        }
    )

    view = evaluate_size_policy(root=tmp_path, task=task, config=config)

    assert view.classified_as is SizeClass.LARGE
    assert view.task.file_count == 3
    assert view.task.nonempty_line_count == 120
    assert view.task.file_count_source == "metadata:file_count"
    assert view.task.nonempty_line_count_source == "metadata:nonempty_line_count"
    assert view.task.threshold_hits == ("file_count", "nonempty_line_count")
    assert view.task.qualifying_signal_count == 2
    assert view.task.files_to_touch == ()


def test_task_size_uses_loc_fallback_when_explicit_files_to_touch_are_stale(tmp_path: Path) -> None:
    task = _task_card(
        "\n".join(
            [
                "**Complexity:** MODERATE",
                "",
                "### Files to touch (explicit):",
                "- `missing_a.py`",
                "- `missing_b.py`",
            ]
        ),
        metadata={
            "task_large_loc": 120,
        },
    )
    config = SizingConfig.model_validate(
        {
            "mode": "task",
            "task": {
                "file_count_threshold": 2,
                "nonempty_line_count_threshold": 100,
            },
        }
    )

    view = evaluate_size_policy(root=tmp_path, task=task, config=config)

    assert view.classified_as is SizeClass.LARGE
    assert view.task.file_count == 2
    assert view.task.nonempty_line_count == 120
    assert view.task.file_count_source == "body:files_to_touch"
    assert view.task.nonempty_line_count_source == "metadata:nonempty_line_count"
    assert view.task.files_to_touch == ("missing_a.py", "missing_b.py")
    assert view.task.missing_files_to_touch == ("missing_a.py", "missing_b.py")
    assert view.task.threshold_hits == ("file_count", "nonempty_line_count")
    assert view.task.qualifying_signal_count == 2


def test_refresh_size_status_retains_large_latch_across_hybrid_recheck(tmp_path: Path) -> None:
    root = tmp_path
    (root / "src").mkdir()
    (root / "src/app.py").write_text("alpha\nbeta\n", encoding="utf-8")
    latch_path = root / "agents" / "size_status.md"
    latch_path.parent.mkdir(parents=True, exist_ok=True)
    large_config = SizingConfig.model_validate(
        {
            "mode": "hybrid",
            "repo": {
                "file_count_threshold": 1,
                "nonempty_line_count_threshold": 999_999_999,
            },
        }
    )
    small_config = SizingConfig.model_validate(
        {
            "mode": "hybrid",
            "repo": {
                "file_count_threshold": 999_999_999,
                "nonempty_line_count_threshold": 999_999_999,
            },
            "task": {
                "file_count_threshold": 999_999_999,
                "nonempty_line_count_threshold": 999_999_999,
            },
        }
    )

    first = refresh_size_status(root=root, task=None, config=large_config, latch_path=latch_path)
    second = refresh_size_status(root=root, task=None, config=small_config, latch_path=latch_path)

    assert first.classified_as is SizeClass.LARGE
    assert first.triggered_sources == ("repo",)
    assert latch_path.read_text(encoding="utf-8") == "### LARGE\n"
    assert second.classified_as is SizeClass.SMALL
    assert second.latched_as is SizeClass.LARGE
    assert second.latch_reason == "retained_large_latch"
    assert latch_path.read_text(encoding="utf-8") == "### LARGE\n"


def test_adaptive_upscope_task_card_preserves_existing_task_contract_fields() -> None:
    task = TaskCard.model_validate(
        {
            "heading": "## 2026-03-19 - Adaptive task",
            "body": "**Complexity:** MODERATE",
            "metadata": {
                "files_to_touch_count": 3,
                "task_large_loc": 120,
            },
            "gates": ("INTEGRATION",),
            "integration_preference": "skip",
            "depends_on": ("REQ-1",),
            "source_file": Path("agents/tasks.md"),
        }
    )

    updated = adaptive_upscope_task_card(
        task,
        target=SizeClass.LARGE,
        rule="blocked_small_non_usage_v1",
        stage="Resume",
        reason="explicit rule",
    )

    assert updated.metadata == task.metadata
    assert updated.gates == ("INTEGRATION",)
    assert updated.integration_preference == "skip"
    assert updated.depends_on == ("REQ-1",)
    assert updated.source_file == Path("agents/tasks.md")
    assert "- **Adaptive Upscope:** LARGE" in updated.raw_markdown
